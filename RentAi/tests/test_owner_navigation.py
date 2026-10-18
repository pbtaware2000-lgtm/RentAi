"""Checks that the Owner menu retains its section selection across reruns."""

import unittest
from unittest.mock import patch

from frontend.application_api import ApplicationAPIError
from streamlit.testing.v1 import AppTest


class OwnerNavigationTests(unittest.TestCase):
    def test_owner_sidebar_switches_section_without_leaving_owner_route(self):
        app = AppTest.from_string(
            "from frontend.components.owner_sidebar import render_owner_sidebar\n"
            "render_owner_sidebar(on_logout=lambda: None)",
            default_timeout=30,
        ).run()
        app.button(key="owner_nav_My Properties").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["owner_active_page"], "My Properties")

    def test_owner_sections_render_without_records(self):
        renderers = (
            ("frontend.pages.owner_dashboard", "render"),
            ("frontend.pages.owner_sections", "render_properties"),
            ("frontend.pages.owner_sections", "render_add_property"),
            ("frontend.pages.owner_sections", "render_enquiries"),
            ("frontend.pages.owner_sections", "render_renters"),
            ("frontend.pages.owner_sections", "render_analytics"),
            ("frontend.pages.owner_sections", "render_revenue"),
            ("frontend.pages.owner_sections", "render_maintenance"),
            ("frontend.pages.owner_sections", "render_profile"),
            ("frontend.pages.owner_sections", "render_settings"),
        )
        for module_name, renderer_name in renderers:
            with self.subTest(page=renderer_name), patch(
                "frontend.pages.owner_support.records", return_value=[]
            ), patch("frontend.pages.owner_sections.records", return_value=[]):
                app = AppTest.from_string(
                    f"from {module_name} import {renderer_name}\n{renderer_name}()",
                    default_timeout=30,
                ).run()
                self.assertFalse(app.exception)

    def test_property_edit_and_delete_require_explicit_owner_actions(self):
        listing = {
            "id": "listing-1", "kind": "listing", "owner_uid": "owner-1",
            "created_at": "2026-10-01 12:00:00", "data": {
                "title": "Aundh Apartment", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 28000, "bedrooms": 2,
                "bathrooms": 1, "area_sqft": 900, "floor": 3,
                "furnishing_status": "Semi-Furnished", "distance_to_city_center_km": 4,
                "age_of_property": 5, "parking": 1,
                "occupancy_status": "vacant", "description": "A real saved listing.",
                "status": "published",
            },
        }
        with patch("frontend.pages.owner_sections.records", return_value=[listing]), patch(
            "frontend.pages.owner_sections.update",
            side_effect=lambda record, data: {**record, "data": dict(data)},
        ) as update_record, patch("frontend.pages.owner_sections.delete") as delete_record, patch(
            "frontend.pages.owner_sections.st.rerun"
        ):
            app = AppTest.from_string(
                "from frontend.pages.owner_sections import render_properties\nrender_properties()",
                default_timeout=30,
            ).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.value == "Property details" for item in app.subheader))
            delete_button = next(button for button in app.button if button.label == "Delete property")
            self.assertTrue(delete_button.disabled)

            app.text_input(key="owner_edit_title").set_value("Updated Aundh Apartment")
            next(button for button in app.button if button.label == "Save changes").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(update_record.call_args.args[0]["id"], "listing-1")
            self.assertEqual(update_record.call_args.args[1]["title"], "Updated Aundh Apartment")

            app.checkbox(key="confirm_delete_owner_property_listing-1").set_value(True).run()
            next(button for button in app.button if button.label == "Delete property").click().run()
            self.assertFalse(app.exception)
            delete_record.assert_called_once_with(listing)

    def test_property_edit_page_exposes_saved_photo_gallery_and_upload_control(self):
        listing = {
            "id": "listing-photo", "kind": "listing", "owner_uid": "owner-1",
            "data": {
                "title": "Photo Home", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 15000, "bedrooms": 2,
                "occupancy_status": "vacant", "description": "Saved listing", "status": "published",
            },
        }
        with patch("frontend.pages.owner_sections.records", side_effect=lambda kind: [listing] if kind == "listing" else []), patch(
            "frontend.pages.owner_sections.listing_images",
            return_value=[{"record_id": "listing-photo", "url": "https://example.test/home.png"}],
        ):
            app = AppTest.from_string(
                "from frontend.pages.owner_sections import render_properties\nrender_properties()",
                default_timeout=30,
            ).run()

        self.assertFalse(app.exception)
        self.assertIn("**Property photos**", [item.value for item in app.markdown])
        self.assertFalse(any(button.label == "Upload property photo" for button in app.button))
        self.assertTrue(any(item.label == "Choose a property photo" for item in app.get("file_uploader")))

    def test_successful_property_update_message_survives_streamlit_rerun(self):
        listing = {
            "id": "listing-success", "kind": "listing", "owner_uid": "owner-1",
            "data": {
                "title": "Aundh Apartment", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 28000, "bedrooms": 2,
                "bathrooms": 1, "area_sqft": 900, "floor": 3,
                "furnishing_status": "Semi-Furnished", "distance_to_city_center_km": 4,
                "age_of_property": 5, "parking": 1,
                "occupancy_status": "vacant", "description": "Saved listing",
                "status": "published",
            },
        }
        persisted = {"record": listing}

        def load_records(_kind=None):
            return [persisted["record"]]

        def persist_update(record, data):
            persisted["record"] = {**record, "data": dict(data)}
            return persisted["record"]

        with patch("frontend.pages.owner_sections.records", side_effect=load_records), patch(
            "frontend.pages.owner_sections.update", side_effect=persist_update
        ) as update_record:
            app = AppTest.from_string(
                "from frontend.pages.owner_sections import render_properties\nrender_properties()",
                default_timeout=30,
            ).run()
            app.text_input(key="owner_edit_title").set_value("Renovated Aundh Apartment")
            next(button for button in app.button if button.label == "Save changes").click().run()

        self.assertFalse(app.exception)
        update_record.assert_called_once()
        self.assertEqual(update_record.call_args.args[0]["id"], "listing-success")
        self.assertEqual(persisted["record"]["data"]["title"], "Renovated Aundh Apartment")
        self.assertTrue(any(
            item.value == "Property updated successfully."
            for item in app.success
        ))

    def test_active_tenancy_locks_effective_occupancy_without_rewriting_saved_field(self):
        listing = {
            "id": "listing-active", "kind": "listing", "owner_uid": "owner-1",
            "data": {
                "title": "Pune 2 BHK Apartment", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 15000, "bedrooms": 2,
                "occupancy_status": "vacant", "description": "Saved listing", "status": "published",
            },
        }
        tenancy = {"id": "tenancy-active", "kind": "tenancy", "owner_uid": "owner-1", "data": {
            "listing_id": "listing-active", "status": "active", "renter_name": "Vishal Kokate",
        }}
        with patch(
            "frontend.pages.owner_sections.records",
            side_effect=lambda kind: {"listing": [listing], "tenancy": [tenancy]}[kind],
        ), patch(
            "frontend.pages.owner_sections.update",
            side_effect=lambda record, data: {**record, "data": dict(data)},
        ) as update_record, patch("frontend.pages.owner_sections.st.rerun"):
            app = AppTest.from_string(
                "from frontend.pages.owner_sections import render_properties\nrender_properties()",
                default_timeout=30,
            ).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="owner_edit_occupancy").value, "occupied")
            self.assertTrue(app.selectbox(key="owner_edit_occupancy").disabled)
            self.assertTrue(any("Occupied due to active tenancy with Vishal Kokate" in item.value for item in app.caption))

            app.text_input(key="owner_edit_title").set_value("Pune Apartment Updated")
            next(button for button in app.button if button.label == "Save changes").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(update_record.call_args.args[1]["title"], "Pune Apartment Updated")
        self.assertEqual(update_record.call_args.args[1]["occupancy_status"], "vacant")

    def test_ended_or_absent_tenancy_leaves_saved_occupancy_editable(self):
        listing = {
            "id": "listing-vacant", "kind": "listing", "owner_uid": "owner-1",
            "data": {
                "title": "Vacant Apartment", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 15000, "bedrooms": 2,
                "occupancy_status": "vacant", "description": "Saved listing", "status": "published",
            },
        }
        for tenancy in (
            {"id": "tenancy-ended", "kind": "tenancy", "owner_uid": "owner-1", "data": {"listing_id": "listing-vacant", "status": "ended"}},
            None,
        ):
            with self.subTest(tenancy=tenancy), patch(
                "frontend.pages.owner_sections.records",
                side_effect=lambda kind, tenancy=tenancy: {"listing": [listing], "tenancy": [tenancy] if tenancy else []}[kind],
            ):
                app = AppTest.from_string(
                    "from frontend.pages.owner_sections import render_properties\nrender_properties()",
                    default_timeout=30,
                ).run()
                self.assertFalse(app.exception)
                occupancy = app.selectbox(key="owner_edit_occupancy")
                self.assertEqual(occupancy.value, "vacant")
                self.assertFalse(occupancy.disabled)

    def test_enquiry_summary_displays_labels_and_values_from_owner_records(self):
        enquiries = [
            {"id": "inquiry-pending", "data": {"status": "pending", "client_name": "Renter One"}},
            {"id": "inquiry-accepted", "data": {"status": "accepted", "client_name": "Renter Two"}},
        ]
        with patch(
            "frontend.pages.owner_sections.records",
            side_effect=lambda kind: enquiries if kind == "inquiry" else [],
        ):
            app = AppTest.from_string(
                "from frontend.pages.owner_sections import render_enquiries\nrender_enquiries()",
                default_timeout=30,
            ).run()

        self.assertFalse(app.exception)
        visible_headings = [item.value for item in app.markdown]
        self.assertIn("**Total Enquiries**", visible_headings)
        self.assertIn("**Pending Enquiries**", visible_headings)
        self.assertNotIn("**Formal Applications**", visible_headings)
        self.assertEqual([metric.value for metric in app.metric[:2]], ["2", "1"])
        self.assertFalse(any("Formal application records" in item.value for item in app.caption))

    def test_property_update_api_failure_is_shown_without_internal_details(self):
        listing = {
            "id": "listing-failure", "kind": "listing", "owner_uid": "owner-1",
            "data": {
                "title": "Aundh Apartment", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 28000, "bedrooms": 2,
                "bathrooms": 1, "area_sqft": 900, "floor": 3,
                "furnishing_status": "Semi-Furnished", "distance_to_city_center_km": 4,
                "age_of_property": 5, "parking": 1,
                "occupancy_status": "vacant", "description": "Saved listing",
                "status": "published",
            },
        }
        with patch("frontend.pages.owner_sections.records", return_value=[listing]), patch(
            "frontend.pages.owner_sections.update",
            side_effect=ApplicationAPIError(
                "RentAI could not complete that request. Please try again.",
                status_code=400,
                response_detail="Rent must be positive",
            ),
        ) as update_record, patch("frontend.pages.owner_sections.st.rerun"):
            app = AppTest.from_string(
                "from frontend.pages.owner_sections import render_properties\nrender_properties()",
                default_timeout=30,
            ).run()
            app.text_input(key="owner_edit_title").set_value("Updated title")
            next(button for button in app.button if button.label == "Save changes").click().run()

        self.assertFalse(app.exception)
        update_record.assert_called_once()
        self.assertEqual(
            [item.value for item in app.error],
            ["RentAI could not complete that request. Please try again."],
        )
        self.assertFalse(app.success)


if __name__ == "__main__":
    unittest.main()
