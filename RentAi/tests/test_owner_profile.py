"""Owner profile View/Edit and persistent avatar upload regressions."""

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from frontend.application_api import ApplicationAPIError
from frontend.pages.owner_sections import _owner_profile_widget_keys


def _saved_profile():
    return {
        "id": "profile-owner-a",
        "kind": "profile",
        "owner_uid": "owner-a",
        "data": {
            "record_type": "owner_profile",
            "name": "Aanya Sharma",
            "phone": "+91 98765 43210",
            "business_name": "Aundh Homes",
            "bio": "Helping renters find a comfortable home.",
        },
    }


def _profile_app():
    app = AppTest.from_string(
        "from frontend.pages.owner_sections import render_profile\nrender_profile()",
        default_timeout=30,
    )
    app.session_state["rentai_uid"] = "owner-a"
    app.session_state["rentai_id_token"] = "token-owner-a"
    app.session_state["rentai_role"] = "owner"
    app.session_state["rentai_display_name"] = "Aanya Sharma"
    app.session_state["rentai_email"] = "aanya@example.com"
    return app


class OwnerProfileTests(unittest.TestCase):
    def test_view_profile_shows_authenticated_owners_saved_values_by_default(self):
        with patch("frontend.pages.owner_sections.records", return_value=[_saved_profile()]), patch(
            "frontend.pages.owner_sections.profile_images", return_value=[]
        ):
            app = _profile_app().run()

        self.assertFalse(app.exception)
        content = "\n".join(item.value for item in app.markdown)
        self.assertIn("Aanya Sharma", content)
        self.assertIn("aanya@example.com", content)
        self.assertIn("+91 98765 43210", content)
        self.assertIn("Aundh Homes", content)
        self.assertIn("Helping renters find a comfortable home.", content)
        self.assertFalse(app.text_input)
        self.assertTrue(any(button.label == "Edit Profile" for button in app.button))

    def test_edit_profile_prefills_saved_values_even_if_cached_widget_values_were_blank(self):
        app = _profile_app()
        keys = _owner_profile_widget_keys("owner-a", "token-owner-a")
        for key in keys.values():
            app.session_state[key] = ""
        app.session_state["owner_profile_loaded_token_owner-a"] = "token-owner-a"
        app.session_state["owner_profile_saved_data_owner-a"] = dict(_saved_profile()["data"])

        with patch("frontend.pages.owner_sections.records", return_value=[_saved_profile()]), patch(
            "frontend.pages.owner_sections.profile_images", return_value=[]
        ):
            app.run()
            next(button for button in app.button if button.label == "Edit Profile").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key=keys["name"]).value, "Aanya Sharma")
        self.assertEqual(app.text_input(key=keys["phone"]).value, "+91 98765 43210")
        self.assertEqual(app.text_input(key=keys["business_name"]).value, "Aundh Homes")
        self.assertEqual(app.text_area(key=keys["bio"]).value, "Helping renters find a comfortable home.")

    def test_save_changes_persists_and_returns_to_view_after_confirmation(self):
        persisted = {"record": _saved_profile()}

        def load_profiles(_kind=None):
            return [persisted["record"]]

        def save_profile(record, data):
            persisted["record"] = {**record, "data": dict(data)}
            return persisted["record"]

        keys = _owner_profile_widget_keys("owner-a", "token-owner-a")
        with patch("frontend.pages.owner_sections.records", side_effect=load_profiles), patch(
            "frontend.pages.owner_sections.profile_images", return_value=[]
        ), patch("frontend.pages.owner_sections.update", side_effect=save_profile) as update:
            app = _profile_app().run()
            next(button for button in app.button if button.label == "Edit Profile").click().run()
            app.text_input(key=keys["name"]).set_value("Aanya S.")
            app.text_input(key=keys["phone"]).set_value("+91 91234 56789")
            app.text_input(key=keys["business_name"]).set_value("S. Properties")
            app.text_area(key=keys["bio"]).set_value("Updated owner profile.")
            next(button for button in app.button if button.label == "Save Changes").click().run()

        self.assertFalse(app.exception)
        update.assert_called_once()
        self.assertEqual(persisted["record"]["data"]["name"], "Aanya S.")
        self.assertNotIn("email", update.call_args.args[1])
        self.assertFalse(app.text_input)
        self.assertIn("Aanya S.", "\n".join(item.value for item in app.markdown))
        self.assertTrue(any("saved successfully" in item.value for item in app.success))

    def test_cancel_discards_unsaved_text_and_returns_to_saved_view(self):
        keys = _owner_profile_widget_keys("owner-a", "token-owner-a")
        with patch("frontend.pages.owner_sections.records", return_value=[_saved_profile()]), patch(
            "frontend.pages.owner_sections.profile_images", return_value=[]
        ):
            app = _profile_app().run()
            next(button for button in app.button if button.label == "Edit Profile").click().run()
            app.text_input(key=keys["name"]).set_value("Unsaved name")
            next(button for button in app.button if button.label == "Cancel").click().run()

        self.assertFalse(app.exception)
        self.assertFalse(app.text_input)
        content = "\n".join(item.value for item in app.markdown)
        self.assertIn("Aanya Sharma", content)
        self.assertNotIn("Unsaved name", content)

    def test_saved_photo_is_shown_and_native_uploader_is_available_in_edit_mode(self):
        photo_url = "https://res.cloudinary.com/test/image/upload/refreshed-profile.png"
        with patch("frontend.pages.owner_sections.records", return_value=[_saved_profile()]), patch(
            "frontend.pages.owner_sections.profile_images",
            return_value=[{"record_id": "profile-owner-a", "url": photo_url}],
        ):
            app = _profile_app().run()
            content = "\n".join(item.value for item in app.markdown)
            next(button for button in app.button if button.label == "Edit Profile").click().run()

        self.assertFalse(app.exception)
        self.assertIn(photo_url, content)
        self.assertTrue(any(item.label == "Choose profile photo" for item in app.get("file_uploader")))

    def test_profile_photo_uses_the_supported_native_streamlit_uploader(self):
        with patch("frontend.pages.owner_sections.records", return_value=[_saved_profile()]), patch(
            "frontend.pages.owner_sections.profile_images", return_value=[]
        ):
            app = _profile_app().run()
            self.assertFalse(app.get("file_uploader"))
            next(button for button in app.button if button.label == "Edit Profile").click().run()

        self.assertFalse(app.exception)
        self.assertTrue(any(item.label == "Choose profile photo" for item in app.get("file_uploader")))

    def test_profile_still_requires_authenticated_api_session(self):
        app = AppTest.from_string(
            "from frontend.pages.owner_sections import render_profile\nrender_profile()",
            default_timeout=30,
        ).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("could not be loaded" in item.value for item in app.error))

    def test_failed_save_stays_in_edit_mode_without_success_message(self):
        keys = _owner_profile_widget_keys("owner-a", "token-owner-a")
        with patch("frontend.pages.owner_sections.records", return_value=[_saved_profile()]), patch(
            "frontend.pages.owner_sections.profile_images", return_value=[]
        ), patch(
            "frontend.pages.owner_sections.update", side_effect=ApplicationAPIError("Unavailable")
        ):
            app = _profile_app().run()
            next(button for button in app.button if button.label == "Edit Profile").click().run()
            app.text_input(key=keys["name"]).set_value("Changed")
            next(button for button in app.button if button.label == "Save Changes").click().run()

        self.assertFalse(app.exception)
        self.assertTrue(app.text_input)
        self.assertTrue(any("Profile not saved" in item.value for item in app.error))
        self.assertFalse(app.success)


if __name__ == "__main__":
    unittest.main()
