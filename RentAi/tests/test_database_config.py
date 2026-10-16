"""SQLite database path configuration stays stable across working directories."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.repositories import sqlite_repository
from backend.repositories.sqlite_repository import resolve_database_path


class DatabasePathTests(unittest.TestCase):
    def test_relative_database_path_is_anchored_to_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "RentAI"
            expected = project_root / "data" / "rentai.sqlite3"
            self.assertEqual(
                resolve_database_path("data/rentai.sqlite3", project_root=project_root),
                expected.resolve(),
            )

    def test_absolute_database_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "custom.sqlite3"
            self.assertEqual(
                resolve_database_path(str(expected), project_root=Path("/unused")),
                expected.resolve(),
            )

    def test_blank_database_path_uses_project_database(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "RentAI"
            self.assertEqual(
                resolve_database_path("  ", project_root=project_root),
                (project_root / "data" / "rentai.sqlite3").resolve(),
            )

    def test_api_startup_initializes_the_configured_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "startup.sqlite3"
            with patch.object(sqlite_repository, "DB_PATH", database_path):
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").status_code, 200)
                self.assertTrue(database_path.is_file())
                import sqlite3

                database = sqlite3.connect(database_path)
                try:
                    tables = {
                        row[0]
                        for row in database.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                finally:
                    database.close()
                self.assertTrue({"users", "sessions", "application_records"}.issubset(tables))


if __name__ == "__main__":
    unittest.main()
