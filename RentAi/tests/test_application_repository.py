"""Unit checks for Firestore DAO calls using an in-memory client double."""

import unittest
import sys
import types
from unittest.mock import patch

from backend.repositories.firestore_repository import FirestoreRepository


class Snapshot:
    def __init__(self, exists, data=None, doc_id="record-1"):
        self.exists = exists
        self._data = data or {}
        self.id = doc_id

    def to_dict(self):
        return self._data


class Document:
    id = "record-1"

    def __init__(self, data=None):
        self.data = data

    def set(self, data):
        self.data = data

    def get(self):
        return Snapshot(self.data is not None, self.data)

    def update(self, data):
        self.data.update(data)

    def delete(self):
        self.data = None


class Collection:
    def __init__(self):
        self.document_ref = Document()

    def document(self, doc_id=None):
        return self.document_ref


class Client:
    def __init__(self):
        self.collection_ref = Collection()

    def collection(self, name):
        return self.collection_ref


class RepositoryTests(unittest.TestCase):
    def test_create_get_update_delete(self):
        repository = FirestoreRepository(Client())
        record_id, _ = repository.create("application_records", {"owner_uid": "user-1"})
        self.assertEqual(record_id, "record-1")
        self.assertEqual(repository.get("application_records", record_id)["owner_uid"], "user-1")
        self.assertTrue(repository.update("application_records", record_id, {"kind": "listing"}))
        self.assertEqual(repository.get("application_records", record_id)["kind"], "listing")
        self.assertTrue(repository.delete("application_records", record_id))

    def test_broker_assignment_uses_atomic_array_union(self):
        class ArrayUnion:
            def __init__(self, values):
                self.values = values

        fake_firebase = types.ModuleType("firebase_admin")
        fake_firebase.firestore = types.SimpleNamespace(ArrayUnion=ArrayUnion)
        client = Client()
        client.collection_ref.document_ref.data = {
            "owner_uid": "owner-1",
            "assigned_broker_uids": ["broker-1"],
        }
        original_update = client.collection_ref.document_ref.update

        def update(data):
            value = data["assigned_broker_uids"]
            self.assertIsInstance(value, ArrayUnion)
            current = client.collection_ref.document_ref.data["assigned_broker_uids"]
            client.collection_ref.document_ref.data["assigned_broker_uids"] = sorted(
                set(current) | set(value.values)
            )

        client.collection_ref.document_ref.update = update
        repository = FirestoreRepository(client)
        with patch.dict(sys.modules, {"firebase_admin": fake_firebase}):
            assigned = repository.assign_broker("record-1", "broker-2")
        self.assertEqual(assigned, ["broker-1", "broker-2"])
        client.collection_ref.document_ref.update = original_update

    def test_first_admin_bootstrap_is_single_uid_and_retry_safe(self):
        class StoredSnapshot:
            def __init__(self, data):
                self.exists = data is not None
                self._data = data or {}

            def to_dict(self):
                return self._data

        class Reference:
            def __init__(self, collection_name, document_id):
                self.key = (collection_name, document_id)

        class Query:
            def __init__(self, collection_name):
                self.collection_name = collection_name

            def where(self, field, operation, value):
                self.filter = (field, operation, value)
                return self

            def limit(self, count):
                return self

        class CollectionRef:
            def __init__(self, name):
                self.name = name

            def document(self, doc_id):
                return Reference(self.name, doc_id)

            def where(self, field, operation, value):
                query = Query(self.name)
                return query.where(field, operation, value)

        class Transaction:
            def __init__(self, store):
                self.store = store

            def get(self, reference):
                if isinstance(reference, Query):
                    field, operation, value = reference.filter
                    return [
                        StoredSnapshot(data)
                        for (collection_name, _), data in self.store.items()
                        if collection_name == reference.collection_name and data.get(field) == value
                    ][:1]
                return StoredSnapshot(self.store.get(reference.key))

            def create(self, reference, data):
                if reference.key in self.store:
                    raise AssertionError("transaction create must be unique")
                self.store[reference.key] = dict(data)

            def set(self, reference, data, merge=False):
                self.store[reference.key] = {**self.store.get(reference.key, {}), **data}

        class FakeClient:
            def __init__(self):
                self.store = {}

            def collection(self, name):
                return CollectionRef(name)

            def transaction(self):
                return Transaction(self.store)

        fake_firestore = types.SimpleNamespace(transactional=lambda function: function)
        fake_firebase = types.ModuleType("firebase_admin")
        fake_firebase.firestore = fake_firestore
        client = FakeClient()
        repository = FirestoreRepository(client)
        with patch.dict(sys.modules, {"firebase_admin": fake_firebase}):
            self.assertTrue(repository.bootstrap_first_admin("trusted-uid"))
            self.assertTrue(repository.bootstrap_first_admin("trusted-uid"))
            self.assertFalse(repository.bootstrap_first_admin("different-uid"))
        self.assertEqual(
            client.store[("application_users", "trusted-uid")], {"role": "admin"}
        )
        self.assertEqual(
            client.store[("application_metadata", "admin_bootstrap")], {"uid": "trusted-uid"}
        )


if __name__ == "__main__":
    unittest.main()
