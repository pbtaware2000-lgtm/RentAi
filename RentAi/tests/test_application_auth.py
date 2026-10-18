"""Unit checks for verified identities and application role boundaries."""

import unittest
import sys
import types
import os
import time
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.auth.dependencies import get_current_user, require_roles
from backend.services.application_service import ApplicationService
from backend.core.firebase import bootstrap_first_admin, grant_role_to_user
from backend.core.firebase import register_user_role
from backend.routes.application import bootstrap_admin


class MemoryRepository:
    def __init__(self, records=None):
        self.records = records or {}

    def create(self, collection, data):
        self.records["created"] = {"id": "new", **data}
        return "new", data

    def get(self, collection, record_id):
        return self.records.get(record_id)

    def update(self, collection, record_id, data):
        self.records[record_id].update(data)
        return True

    def delete(self, collection, record_id):
        return self.records.pop(record_id, None) is not None

    def get_user_role(self, uid):
        return self.records.get(f"role:{uid}")


class ApplicationAuthTests(unittest.TestCase):
    @patch("backend.repositories.firestore_repository.FirestoreRepository.create_user_role", return_value=True)
    @patch("firebase_admin.auth.get_user")
    @patch("backend.core.firebase.get_firebase_app", return_value=object())
    @patch("backend.core.firebase.verify_id_token")
    def test_public_registration_sets_only_verified_uid_role_once(self, verify, get_app, get_user, create_role):
        verify.return_value = {"uid": "new-user", "auth_time": int(time.time())}
        get_user.return_value.user_metadata.creation_timestamp = int(time.time() * 1000)
        self.assertEqual(register_user_role(id_token="signed-token", role="renter"), "new-user")
        verify.assert_called_once_with("signed-token")
        create_role.assert_called_once_with("new-user", "renter")

    @patch("backend.core.firebase.verify_id_token")
    def test_public_registration_rejects_admin_before_token_processing(self, verify):
        with self.assertRaises(ValueError):
            register_user_role(id_token="signed-token", role="admin")
        verify.assert_not_called()

    @patch("backend.repositories.firestore_repository.FirestoreRepository.create_user_role")
    @patch("firebase_admin.auth.get_user")
    @patch("backend.core.firebase.get_firebase_app", return_value=object())
    @patch("backend.core.firebase.verify_id_token")
    def test_existing_firebase_account_cannot_claim_public_registration(self, verify, get_app, get_user, create_role):
        now = int(time.time())
        verify.return_value = {"uid": "older-user", "auth_time": now}
        get_user.return_value.user_metadata.creation_timestamp = (now - 301) * 1000
        with self.assertRaises(PermissionError):
            register_user_role(id_token="signed-token", role="owner")
        create_role.assert_not_called()

    @patch("backend.repositories.firestore_repository.FirestoreRepository.create_user_role", return_value=False)
    @patch("firebase_admin.auth.get_user")
    @patch("backend.core.firebase.get_firebase_app", return_value=object())
    @patch("backend.core.firebase.verify_id_token")
    def test_public_registration_cannot_overwrite_existing_role(self, verify, get_app, get_user, create_role):
        verify.return_value = {"uid": "existing-user", "auth_time": int(time.time())}
        get_user.return_value.user_metadata.creation_timestamp = int(time.time() * 1000)
        with self.assertRaises(PermissionError):
            register_user_role(id_token="signed-token", role="broker")
        create_role.assert_called_once_with("existing-user", "broker")

    @patch("backend.auth.dependencies.verify_id_token")
    @patch("backend.auth.dependencies.FirestoreRepository.get_user_role", return_value="owner")
    def test_role_comes_from_authoritative_store_not_stale_token_claims(self, get_role, verify):
        verify.return_value = {"uid": "owner-1", "role": "admin"}
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")
        user = get_current_user(credentials)
        self.assertEqual((user.uid, user.role), ("owner-1", "owner"))
        verify.assert_called_once_with("signed-token")
        get_role.assert_called_once_with("owner-1")

    def test_missing_token_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            get_current_user(None)
        self.assertEqual(caught.exception.status_code, 401)

    @patch("backend.auth.dependencies.verify_id_token", return_value={"uid": "user-1"})
    @patch("backend.auth.dependencies.FirestoreRepository.get_user_role", return_value=None)
    def test_missing_firestore_role_is_rejected_even_when_token_is_valid(self, get_role, verify):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")
        with self.assertRaises(HTTPException) as caught:
            get_current_user(credentials)
        self.assertEqual(caught.exception.status_code, 403)
        get_role.assert_called_once_with("user-1")

    def test_role_dependency_rejects_other_role(self):
        dependency = require_roles("admin")
        with self.assertRaises(HTTPException) as caught:
            dependency(user=type("User", (), {"role": "broker"})())
        self.assertEqual(caught.exception.status_code, 403)

    def test_owner_and_broker_record_access_are_scoped(self):
        repository = MemoryRepository({
            "listing": {"id": "listing", "kind": "listing", "owner_uid": "owner-1", "assigned_broker_uids": ["broker-1"]}
        })
        service = ApplicationService(repository)
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        broker = type("User", (), {"uid": "broker-1", "role": "broker"})()
        other = type("User", (), {"uid": "broker-2", "role": "broker"})()
        service.get_record(owner, "listing")
        service.get_record(broker, "listing")
        with self.assertRaises(HTTPException) as caught:
            service.get_record(other, "listing")
        self.assertEqual(caught.exception.status_code, 403)

    def test_assigned_broker_cannot_update_or_delete_owner_listing(self):
        repository = MemoryRepository({
            "listing": {"id": "listing", "owner_uid": "owner-1", "kind": "listing", "assigned_broker_uids": ["broker-1"]}
        })
        service = ApplicationService(repository)
        broker = type("User", (), {"uid": "broker-1", "role": "broker"})()
        for operation in (
            lambda: service.update_record(broker, "listing", {"rent": 1}),
            lambda: service.delete_record(broker, "listing"),
        ):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 403)

    def test_kind_and_operation_matrix_is_enforced(self):
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        renter = type("User", (), {"uid": "renter-1", "role": "renter"})()
        repository = MemoryRepository({
            "listing-1": {"id": "listing-1", "owner_uid": "owner-1", "kind": "listing", "data": {"status": "published"}}
        })
        service = ApplicationService(repository)
        service.create_record(owner, "listing", {
            "title": "Sample home", "city": "Pune", "locality": "Kothrud",
            "rent": 1000, "bedrooms": 1, "property_type": "Apartment",
        })
        inquiry = service.create_record(
            renter,
            "inquiry",
            {"listing_id": "listing-1", "property_owner_uid": "attacker-selected-owner", "client_name": "Renter", "message": "I am interested"},
        )
        self.assertEqual(inquiry["data"]["property_owner_uid"], "owner-1")
        with self.assertRaises(HTTPException) as wrong_kind:
            service.create_record(renter, "listing", {})
        self.assertEqual(wrong_kind.exception.status_code, 403)
        with self.assertRaises(HTTPException) as unknown_kind:
            service.create_record(owner, "administrator", {})
        self.assertEqual(unknown_kind.exception.status_code, 400)

    def test_renter_maintenance_request_is_scoped_to_listing_owner_and_owner_can_update(self):
        repository = MemoryRepository({
            "listing": {"id": "listing", "kind": "listing", "owner_uid": "owner-1", "data": {"status": "published"}},
        })
        service = ApplicationService(repository)
        renter = type("User", (), {"uid": "renter-1", "role": "renter"})()
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        request = service.create_record(renter, "maintenance", {
            "listing_id": "listing", "title": "Leaking tap", "description": "Kitchen tap is leaking",
            "reported_by_uid": "spoofed", "property_owner_uid": "spoofed",
        })
        self.assertEqual(request["data"]["reported_by_uid"], "renter-1")
        self.assertEqual(request["data"]["property_owner_uid"], "owner-1")
        repository.records[request["id"]] = request
        self.assertEqual(service.get_record(owner, request["id"])["id"], request["id"])
        updated = service.update_record(owner, request["id"], {**request["data"], "status": "completed", "owner_note": "Fixed"})
        self.assertEqual(updated["data"]["owner_note"], "Fixed")
        with self.assertRaises(HTTPException) as renter_update:
            service.update_record(renter, request["id"], {**request["data"], "status": "completed"})
        self.assertEqual(renter_update.exception.status_code, 403)

    def test_owner_cannot_log_maintenance_for_another_owners_listing(self):
        repository = MemoryRepository({
            "foreign": {"id": "foreign", "kind": "listing", "owner_uid": "other-owner", "data": {"status": "published"}},
        })
        owner = type("User", (), {"uid": "owner-1", "role": "owner"})()
        with self.assertRaises(HTTPException) as caught:
            ApplicationService(repository).create_record(owner, "maintenance", {
                "listing_id": "foreign", "title": "Repair", "description": "Fix the lock",
            })
        self.assertEqual(caught.exception.status_code, 403)

    @patch("backend.core.firebase.verify_id_token", return_value={"uid": "operator-1", "role": "admin"})
    @patch("backend.core.firebase.get_firebase_app", return_value=object())
    @patch("backend.repositories.firestore_repository.FirestoreRepository")
    def test_role_provisioning_requires_authoritative_admin_and_revokes_tokens(
        self, repository_class, get_app, verify
    ):
        repository = repository_class.return_value
        repository.get_user_role.return_value = "admin"
        auth = types.ModuleType("firebase_admin.auth")
        auth.get_user = Mock(return_value=types.SimpleNamespace(custom_claims={"other": True}))
        auth.set_custom_user_claims = Mock()
        auth.revoke_refresh_tokens = Mock()
        fake_firebase = types.ModuleType("firebase_admin")
        fake_firebase.__path__ = []
        fake_firebase.auth = auth
        with patch.dict(sys.modules, {"firebase_admin": fake_firebase, "firebase_admin.auth": auth}):
            grant_role_to_user(actor_id_token="admin-token", uid="new-user", role="broker")
        repository.set_user_role.assert_called_once_with("new-user", "broker")
        auth.set_custom_user_claims.assert_called_once_with(
            "new-user", {"other": True, "role": "broker"}, app=get_app.return_value
        )
        auth.revoke_refresh_tokens.assert_called_once_with("new-user", app=get_app.return_value)

    @patch("backend.core.firebase.verify_id_token", return_value={"uid": "renter-1", "role": "admin"})
    @patch("backend.repositories.firestore_repository.FirestoreRepository")
    def test_stale_admin_claim_cannot_provision_roles(self, repository_class, verify):
        repository_class.return_value.get_user_role.return_value = "renter"
        with self.assertRaises(PermissionError):
            grant_role_to_user(actor_id_token="stale-admin-token", uid="renter-1", role="admin")

    @patch("backend.routes.application.ApplicationService.bootstrap_first_admin", side_effect=PermissionError)
    def test_bootstrap_route_rejects_unlisted_user(self, bootstrap):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")
        with self.assertRaises(HTTPException) as caught:
            bootstrap_admin(credentials)
        self.assertEqual(caught.exception.status_code, 403)
        bootstrap.assert_called_once_with("valid-token")

    @patch("backend.routes.application.ApplicationService.bootstrap_first_admin", return_value="operator-1")
    def test_bootstrap_route_delegates_verified_bootstrap_to_service(self, bootstrap):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")
        self.assertIsNone(bootstrap_admin(credentials))
        bootstrap.assert_called_once_with("valid-token")

    @patch("backend.core.firebase.verify_id_token", return_value={"uid": "operator-1"})
    @patch("backend.core.firebase.is_bootstrap_admin_uid", return_value=True)
    @patch("backend.core.firebase.get_firebase_app", return_value=object())
    @patch("backend.repositories.firestore_repository.FirestoreRepository")
    def test_bootstrap_provisions_allowlisted_uid_and_clears_runtime_allowlist(
        self, repository_class, get_app, allowlisted, verify
    ):
        repository_class.return_value.bootstrap_first_admin.return_value = True
        auth = types.ModuleType("firebase_admin.auth")
        auth.get_user = Mock(return_value=types.SimpleNamespace(custom_claims={"other": True}))
        auth.set_custom_user_claims = Mock()
        auth.revoke_refresh_tokens = Mock()
        fake_firebase = types.ModuleType("firebase_admin")
        fake_firebase.__path__ = []
        fake_firebase.auth = auth
        with patch.dict("os.environ", {"RENTAI_BOOTSTRAP_ADMIN_UIDS": "operator-1"}), patch.dict(
            sys.modules, {"firebase_admin": fake_firebase, "firebase_admin.auth": auth}
        ):
            self.assertEqual(bootstrap_first_admin(actor_id_token="admin-token"), "operator-1")
            self.assertEqual(os.environ["RENTAI_BOOTSTRAP_ADMIN_UIDS"], "")
        repository_class.return_value.bootstrap_first_admin.assert_called_once_with("operator-1")
        auth.set_custom_user_claims.assert_called_once_with(
            "operator-1", {"other": True, "role": "admin"}, app=get_app.return_value
        )
        auth.revoke_refresh_tokens.assert_called_once_with("operator-1", app=get_app.return_value)

    @patch("backend.core.firebase.verify_id_token", return_value={"uid": "other-user"})
    @patch("backend.core.firebase.is_bootstrap_admin_uid", return_value=False)
    @patch("backend.repositories.firestore_repository.FirestoreRepository")
    def test_bootstrap_rejects_non_allowlisted_uid_before_writing_role(
        self, repository_class, allowlisted, verify
    ):
        with self.assertRaises(PermissionError):
            bootstrap_first_admin(actor_id_token="valid-user-token")
        repository_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
