
"""Firestore DAO for application-managed records; never used for CSV data."""

from backend.core.firebase import get_firestore_client


class FirestoreRepository:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = get_firestore_client()
        return self._client

    def create(self, collection: str, data: dict) -> tuple[str, dict]:
        reference = self.client.collection(collection).document()
        reference.set(data)
        return reference.id, data

    def get(self, collection: str, record_id: str) -> dict | None:
        snapshot = self.client.collection(collection).document(record_id).get()
        if not snapshot.exists:
            return None
        return {"id": snapshot.id, **snapshot.to_dict()}

    def update(self, collection: str, record_id: str, data: dict) -> bool:
        reference = self.client.collection(collection).document(record_id)
        snapshot = reference.get()
        if not snapshot.exists:
            return False
        reference.update(data)
        return True

    def delete(self, collection: str, record_id: str) -> bool:
        reference = self.client.collection(collection).document(record_id)
        if not reference.get().exists:
            return False
        reference.delete()
        return True

    def get_user_role(self, uid: str) -> str | None:
        snapshot = (
            self.client.collection("application_users")
            .document(uid)
            .get()
        )
        if not snapshot.exists:
            return None

        role = (snapshot.to_dict() or {}).get("role")
        return role if role in {"owner", "renter", "broker", "admin"} else None

    def set_user_role(self, uid: str, role: str) -> None:
        self.client.collection("application_users").document(uid).set(
            {"role": role},
            merge=True,
        )

    def create_user_role(self, uid: str, role: str) -> bool:
        """Create a role record only when the account has no role yet."""
        reference = self.client.collection("application_users").document(uid)
        try:
            reference.create({"role": role})
            return True
        except Exception as exc:
            # Firestore AlreadyExists is the expected duplicate case; avoid
            # turning transient storage failures into a role overwrite.
            if exc.__class__.__name__ == "AlreadyExists":
                return False
            raise

    def list_user_roles(self) -> list[dict]:
        return [
            {"uid": snapshot.id, **(snapshot.to_dict() or {})}
            for snapshot in self.client.collection("application_users").stream()
        ]

    def list_records(self) -> list[dict]:
        return [
            {"id": snapshot.id, **(snapshot.to_dict() or {})}
            for snapshot in self.client.collection("application_records").stream()
        ]

    def list_images(self) -> list[dict]:
        return [
            {"id": snapshot.id, **(snapshot.to_dict() or {})}
            for snapshot in self.client.collection("application_images").stream()
        ]

    def bootstrap_first_admin(self, uid: str) -> bool:
        """Reserve the first Admin and save the authoritative role."""
        from firebase_admin import firestore

        transaction = self.client.transaction()

        marker_ref = (
            self.client.collection("application_metadata")
            .document("admin_bootstrap")
        )
        role_ref = (
            self.client.collection("application_users")
            .document(uid)
        )
        admin_query = (
            self.client.collection("application_users")
            .where("role", "==", "admin")
            .limit(1)
        )

        @firestore.transactional
        def apply_bootstrap(txn):
            marker_value = txn.get(marker_ref)
            role_value = txn.get(role_ref)
            marker_results = [marker_value] if hasattr(marker_value, "exists") else list(marker_value)
            role_results = [role_value] if hasattr(role_value, "exists") else list(role_value)

            marker = marker_results[0] if marker_results else None
            role_snapshot = role_results[0] if role_results else None

            if marker is not None and marker.exists:
                marker_uid = (marker.to_dict() or {}).get("uid")
                existing_role = (
                    (role_snapshot.to_dict() or {}).get("role")
                    if role_snapshot is not None and role_snapshot.exists
                    else None
                )
                return marker_uid == uid and existing_role == "admin"

            if role_snapshot is not None and role_snapshot.exists:
                if (role_snapshot.to_dict() or {}).get("role"):
                    return False

            admins = list(txn.get(admin_query))
            if admins:
                return False

            txn.create(marker_ref, {"uid": uid})
            txn.set(role_ref, {"role": "admin"}, merge=True)
            return True

        return bool(apply_bootstrap(transaction))

    def assign_broker(
        self,
        record_id: str,
        broker_uid: str,
    ) -> list[str] | None:
        from firebase_admin import firestore

        reference = (
            self.client.collection("application_records")
            .document(record_id)
        )
        snapshot = reference.get()

        if not snapshot.exists:
            return None

        reference.update({
            "assigned_broker_uids": firestore.ArrayUnion([broker_uid]),
        })

        updated = reference.get().to_dict() or {}
        return sorted(set(updated.get("assigned_broker_uids", [])))
