"""Offline checks for the role- and page-aware static RentAI chat guide."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core.auth import register_account
from backend.main import app
from backend.repositories import sqlite_repository
from backend.repositories.sqlite_repository import SQLiteRepository, initialize_database
from backend.services import chatbot_service


class ChatbotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(sqlite_repository, "DB_PATH", Path(self.temp.name) / "chat.sqlite3")
        self.db_patcher.start()
        self.addCleanup(self.db_patcher.stop)
        self.addCleanup(self.temp.cleanup)
        initialize_database()
        self.owner_uid, self.owner_token = register_account("owner@example.test", "owner-password", "Owner", "owner")
        self.renter_uid, self.renter_token = register_account("renter@example.test", "renter-password", "Renter", "renter")

    def test_owner_booking_answer_uses_actual_page_controls(self):
        answer, mode = chatbot_service.send_message(
            self.owner_uid, "owner", "How can I accept a booking?", "Booking Requests"
        )
        self.assertIn("Booking Requests", answer)
        self.assertIn("Accept request", answer)
        self.assertIn("My Bookings", answer)
        self.assertEqual(mode, "RentAI Guide")

    def test_renter_recommendation_explains_search_and_comparison(self):
        answer, mode = chatbot_service.send_message(
            self.renter_uid,
            "renter",
            "Can you recommend the best rental property by location, monthly rent, amenities, and nearby facilities?",
            "Search Properties",
        )
        self.assertIn("Search Properties", answer)
        self.assertIn("Compare Properties", answer)
        self.assertIn("monthly rent", answer)
        self.assertIn("can’t name a specific best property", answer)
        self.assertEqual(mode, "RentAI Guide")

    def test_current_page_drives_static_guidance(self):
        answer = chatbot_service.static_answer("renter", "AI Rent Prediction", "What should I do here?")
        self.assertIn("estimated rent", answer)
        self.assertIn("estimate", answer)

    def test_static_response_does_not_need_xai_credentials(self):
        with patch.dict("os.environ", {"XAI_API_KEY": ""}):
            answer, mode = chatbot_service.send_message(
                self.renter_uid, "renter", "How do I book?", "Search Properties"
            )
        self.assertIn("submit a booking request", answer)
        self.assertEqual(mode, "RentAI Guide")

    def test_rentai_only_refusal_for_known_unrelated_prompts(self):
        answer, mode = chatbot_service.send_message(self.renter_uid, "renter", "What is Newton's first law?")
        self.assertIn("RentAI", answer)
        self.assertEqual(mode, "RentAI Guide")

    def test_authenticated_endpoint_uses_session_role_not_client_role(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/chatbot/messages", json={"message": "Hello"}).status_code, 401)
            renter_response = client.post(
                "/chatbot/messages",
                headers={"Authorization": f"Bearer {self.renter_token}"},
                json={"message": "How do I accept a booking?", "page": "Dashboard", "role": "owner"},
            )
            self.assertEqual(renter_response.status_code, 200)
            self.assertEqual(renter_response.json()["mode"], "RentAI Guide")
            self.assertIn("Search Properties", renter_response.json()["answer"])
            owner_response = client.post(
                "/chatbot/messages",
                headers={"Authorization": f"Bearer {self.owner_token}"},
                json={"message": "How do I accept a booking?", "page": "Booking Requests"},
            )
            self.assertEqual(owner_response.status_code, 200)
            self.assertIn("Accept request", owner_response.json()["answer"])
            renter_history = client.get(
                "/chatbot/messages", headers={"Authorization": f"Bearer {self.renter_token}"}
            ).json()["messages"]
            self.assertTrue(all("Accept request" not in row["content"] for row in renter_history))

    def test_chat_history_is_user_private_and_clearable(self):
        repo = SQLiteRepository()
        repo.add_chatbot_message(self.renter_uid, "user", "renter question")
        repo.add_chatbot_message(self.owner_uid, "user", "owner question")
        self.assertEqual(repo.chatbot_history(self.renter_uid)[0]["content"], "renter question")
        self.assertEqual(repo.chatbot_history(self.owner_uid)[0]["content"], "owner question")
        repo.clear_chatbot_history(self.renter_uid)
        self.assertEqual(repo.chatbot_history(self.renter_uid), [])


if __name__ == "__main__":
    unittest.main()
