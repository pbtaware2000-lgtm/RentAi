"""Renter workspace navigation, filtering, and user scoping tests."""

import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pandas as pd

from fastapi import HTTPException
from streamlit.testing.v1 import AppTest

from backend.auth.dependencies import CurrentUser
from backend.services.application_service import ApplicationService
from frontend.application_api import ApplicationAPIError, request
from frontend.pages.renter_workspace import filter_properties


class MemoryRecords:
    def __init__(self, records=None, images=None):
        self.records = records or []
        self.images = images or []

    def list_records(self):
        return self.records

    def list_images(self):
        return self.images

    def create(self, collection, data):
        self.records.append({"id": f"id-{len(self.records)}", **data})
        return self.records[-1]["id"], data

    def get(self, collection, record_id):
        return next((item for item in self.records if item.get("id") == record_id), None)

    def delete(self, collection, record_id):
        record = self.get(collection, record_id)
        if record is None:
            return False
        self.records.remove(record)
        return True


class RenterWorkspaceTests(unittest.TestCase):
    def test_search_filters_keep_sources_distinct_and_sort_by_real_values(self):
        items = [
            ({"property_id": "catalog-1", "city": "Pune", "locality": "Aundh", "rent": 30000, "bedrooms": 2}, "catalog"),
            ({"id": "listing-1", "city": "Pune", "locality": "Aundh", "rent": 22000, "bedrooms": 2}, "owner_listing"),
            ({"property_id": "catalog-2", "city": "Mumbai", "locality": "Aundh", "rent": 18000, "bedrooms": 2}, "catalog"),
        ]
        result = filter_properties(items, {
            "city": "Pune", "locality": "aund", "source": "Owner listings",
            "min_rent": 10000, "max_rent": 25000, "bedrooms": "2",
        })
        self.assertEqual([item[0]["id"] for item in result], ["listing-1"])

    def test_favorites_are_private_and_cannot_be_deleted_by_another_renter(self):
        repo = MemoryRecords([
            {"id": "saved-a", "kind": "favorite", "owner_uid": "renter-a", "data": {"property_id": "P1"}},
            {"id": "saved-b", "kind": "favorite", "owner_uid": "renter-b", "data": {"property_id": "P1"}},
        ])
        service = ApplicationService(repo)
        renter_a = CurrentUser(uid="renter-a", role="renter", claims={})
        renter_b = CurrentUser(uid="renter-b", role="renter", claims={})
        self.assertEqual([r["id"] for r in service.list_records_for_user(renter_a, "favorite")], ["saved-a"])
        with self.assertRaises(HTTPException) as caught:
            service.delete_record(renter_a, "saved-b")
        self.assertEqual(caught.exception.status_code, 403)
        service.delete_record(renter_a, "saved-a")
        self.assertEqual([r["id"] for r in service.list_records_for_user(renter_b, "favorite")], ["saved-b"])

    def test_renter_enquiries_are_private_to_the_authenticated_renter(self):
        repo = MemoryRecords([
            {"id": "enquiry-a", "kind": "inquiry", "owner_uid": "renter-a", "data": {"message": "Mine"}},
            {"id": "enquiry-b", "kind": "inquiry", "owner_uid": "renter-b", "data": {"message": "Not mine"}},
        ])
        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        self.assertEqual([item["id"] for item in ApplicationService(repo).list_records_for_user(renter, "inquiry")], ["enquiry-a"])

    def test_renter_listing_query_includes_only_published_records(self):
        repo = MemoryRecords([
            {"id": "published-a", "kind": "listing", "owner_uid": "owner-a", "data": {"status": "published"}},
            {"id": "draft-a", "kind": "listing", "owner_uid": "owner-a", "data": {"status": "draft"}},
            {"id": "deleted-a", "kind": "listing", "owner_uid": "owner-a", "data": {"status": "deleted"}},
            {"id": "published-b", "kind": "listing", "owner_uid": "owner-b", "data": {"status": "published"}},
        ])
        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        visible = ApplicationService(repo).list_records_for_user(renter, "listing")
        self.assertEqual({record["id"] for record in visible}, {"published-a", "published-b"})

    def test_renter_enquiry_links_listing_and_uses_verified_owner_and_renter_ids(self):
        repo = MemoryRecords([
            {"id": "listing-real", "kind": "listing", "owner_uid": "owner-real", "data": {
                "status": "published", "title": "From Firestore",
            }},
        ])
        renter = CurrentUser(uid="renter-real", role="renter", claims={"email": "renter@example.test"})
        service = ApplicationService(repo)
        enquiry = service.create_record(renter, "inquiry", {
            "listing_id": "listing-real", "client_name": "Renter", "message": "I would like to view it.",
        })
        self.assertEqual(enquiry["owner_uid"], "renter-real")
        self.assertEqual(enquiry["data"]["listing_id"], "listing-real")
        self.assertEqual(enquiry["data"]["client_uid"], "renter-real")
        self.assertEqual(enquiry["data"]["property_owner_uid"], "owner-real")
        self.assertEqual([item["id"] for item in service.list_records_for_user(
            CurrentUser(uid="owner-real", role="owner", claims={}), "inquiry"
        )], [enquiry["id"]])
        self.assertEqual(service.list_records_for_user(
            CurrentUser(uid="other-owner", role="owner", claims={}), "inquiry"
        ), [])

    def test_renter_record_queries_succeed_when_repository_has_no_records(self):
        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        service = ApplicationService(MemoryRecords())
        self.assertEqual(service.list_records_for_user(renter, "listing"), [])
        self.assertEqual(service.list_records_for_user(renter, "favorite"), [])
        self.assertEqual(service.list_records_for_user(renter, "profile"), [])

    def test_renter_collection_route_returns_empty_list_for_empty_repository(self):
        from fastapi.testclient import TestClient
        from backend.auth.dependencies import get_current_user
        from backend.main import app

        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        previous_override = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_current_user] = lambda: renter
        try:
            with patch(
                "backend.routes.application.ApplicationService",
                return_value=ApplicationService(MemoryRecords()),
            ):
                response = TestClient(app).get(
                    "/application/auth/records?kind=listing",
                    headers={"Authorization": "Bearer test-token"},
                )
        finally:
            if previous_override is None:
                app.dependency_overrides.pop(get_current_user, None)
            else:
                app.dependency_overrides[get_current_user] = previous_override
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"records": []})

    def test_saving_catalog_property_uses_canonical_dataset_record(self):
        repo = MemoryRecords()
        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        with patch("backend.services.property_service.get_property_by_id", return_value={
            "property_id": "RENT-1", "city": "Pune", "rent": 24000,
        }):
            saved = ApplicationService(repo).create_record(renter, "favorite", {
                "property_id": "RENT-1", "source": "catalog",
                "property": {"city": "Fake city", "admin_secret": "discard"},
            })
        self.assertEqual(saved["owner_uid"], "renter-a")
        self.assertEqual(saved["data"]["property"]["city"], "Pune")
        self.assertNotIn("admin_secret", saved["data"]["property"])

    def test_renter_cannot_save_draft_owner_listing(self):
        repo = MemoryRecords([
            {"id": "draft-1", "kind": "listing", "owner_uid": "owner-a",
             "data": {"status": "draft", "title": "Private"}},
        ])
        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        with self.assertRaises(HTTPException):
            ApplicationService(repo).create_record(renter, "favorite", {
                "property_id": "draft-1", "source": "owner_listing",
            })

    def test_listing_images_are_limited_to_publicly_visible_records(self):
        repo = MemoryRecords(
            records=[
                {"id": "published", "kind": "listing", "owner_uid": "owner-a", "data": {"status": "published"}},
                {"id": "draft", "kind": "listing", "owner_uid": "owner-a", "data": {"status": "draft"}},
            ],
            images=[
                {"id": "image-p", "record_id": "published", "url": "https://images.invalid/p.jpg", "owner_uid": "owner-a"},
                {"id": "image-d", "record_id": "draft", "url": "https://images.invalid/d.jpg", "owner_uid": "owner-a"},
            ],
        )
        renter = CurrentUser(uid="renter-a", role="renter", claims={})
        self.assertEqual([image["record_id"] for image in ApplicationService(repo).list_listing_images_for_user(renter)], ["published"])

    def test_renter_workspace_sidebar_retains_selected_page(self):
        app = AppTest.from_string(
            "from frontend.components.renter_sidebar import render_renter_sidebar\n"
            "render_renter_sidebar(on_logout=lambda: None)", default_timeout=30
        ).run()
        app.button(key="renter_nav_Saved Properties").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["renter_active_page"], "Saved Properties")

    def test_compare_selection_is_scoped_to_renter_session_key(self):
        app = AppTest.from_string(
            "import streamlit as st\n"
            "from frontend.pages.renter_support import add_to_comparison, renter_key\n"
            "st.session_state['rentai_uid'] = 'renter-a'\n"
            "added = add_to_comparison({'property_id': 'RENT-1'}, 'catalog')\n"
            "st.write(added)", default_timeout=30
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["renter_compare_renter-a"], ["catalog:RENT-1"])

        from frontend.pages import renter_workspace
        data = {"favorites": [], "enquiries": [], "listings": [], "profiles": [], "maintenance": [],
                "catalog": [{"property_id": "RENT-1", "city": "Pune", "locality": "Aundh", "rent": 24000}]}
        with patch("frontend.pages.renter_workspace._load", return_value=data):
            compare_app = AppTest.from_string(
                "import pandas as pd\nfrom frontend.pages.renter_workspace import render_compare\nrender_compare(pd.DataFrame())",
                default_timeout=30,
            ).run()
            compare_app.button(key="renter_compare_add").click().run()
        self.assertFalse(compare_app.exception)
        self.assertEqual(compare_app.session_state["renter_compare_session"], ["catalog:RENT-1"])

    def test_property_card_save_control_calls_user_scoped_api(self):
        with patch("frontend.pages.renter_support.create", return_value={"id": "saved-1"}) as create_record:
            app = AppTest.from_string(
                "from frontend.pages.renter_support import display_property_card\n"
                "display_property_card({'property_id': 'RENT-1', 'city': 'Pune', 'rent': 24000}, "
                "source='catalog', favorites=[], key_suffix='test')", default_timeout=30
            ).run()
            app.button(key="save_test_catalog_RENT-1").click().run()
            self.assertFalse(app.exception)
            create_record.assert_called_once()
            self.assertEqual(create_record.call_args.args[0], "favorite")
            self.assertEqual(create_record.call_args.args[1]["property_id"], "RENT-1")

    def test_search_renders_catalog_when_authorized_record_collections_are_empty(self):
        from frontend.pages import renter_workspace

        catalog = pd.DataFrame([{
            "property_id": "RENT-1", "city": "Pune", "locality": "Aundh",
            "property_type": "Apartment", "rent": 24000, "bedrooms": 2,
        }])
        with patch("frontend.pages.renter_support.records", return_value=[]), patch(
            "frontend.pages.renter_support.listing_images", return_value=[]
        ):
            app = AppTest.from_string(
                "import pandas as pd\nfrom frontend.pages.renter_workspace import render_search\n"
                "render_search(pd.DataFrame([{'property_id':'RENT-1','city':'Pune','locality':'Aundh',"
                "'property_type':'Apartment','rent':24000,'bedrooms':2}]))",
                default_timeout=30,
            ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Search Properties")
        self.assertTrue(any("1 matching catalog entries" in item.value for item in app.caption))

    def test_live_firestore_listing_is_not_buried_by_catalog_pagination(self):
        from frontend.pages import renter_workspace

        listing = {
            "id": "listing-live-test", "kind": "listing", "owner_uid": "owner-live-test",
            "data": {
                "title": "Dynamic listing result", "city": "Pune", "locality": "Aundh",
                "property_type": "Apartment", "rent": 90_000, "bedrooms": 2,
                "bathrooms": 2, "description": "Description loaded from the published record.",
                "status": "published", "occupancy_status": "vacant",
            },
        }
        catalog = pd.DataFrame([{
            "property_id": f"catalog-{index}", "city": "Pune", "locality": "Aundh",
            "property_type": "Apartment", "rent": 10_000 + index, "bedrooms": 2,
        } for index in range(45)])
        with patch(
            "frontend.pages.renter_support.records", side_effect=[[listing], [], []]
        ), patch("frontend.pages.renter_support.listing_images", return_value=[]):
            app = AppTest.from_string(
                "import pandas as pd\nfrom frontend.pages.renter_workspace import render_search\n"
                f"render_search(pd.DataFrame({catalog.to_dict(orient='records')!r}))",
                default_timeout=30,
            ).run()

        self.assertFalse(app.exception)
        self.assertTrue(any("Dynamic listing result" in item.value for item in app.markdown))
        self.assertTrue(any("1 matching live listings" in item.value for item in app.caption))
        self.assertTrue(any("45 matching catalog entries" in item.value for item in app.caption))
        self.assertTrue(any(
            "Description loaded from the published record." in str(item.value)
            for item in app.markdown
        ))

    def test_live_listing_filters_apply_to_real_owner_listing_fields(self):
        from frontend.pages import renter_workspace

        records = [
            ({"id": "listing-pune", "city": "Pune", "locality": "Aundh", "rent": 35000, "bedrooms": 2}, "owner_listing"),
            ({"id": "listing-mumbai", "city": "Mumbai", "locality": "Aundh", "rent": 35000, "bedrooms": 2}, "owner_listing"),
            ({"id": "listing-over-budget", "city": "Pune", "locality": "Aundh", "rent": 55000, "bedrooms": 2}, "owner_listing"),
            ({"id": "listing-wrong-bedrooms", "city": "Pune", "locality": "Aundh", "rent": 35000, "bedrooms": 1}, "owner_listing"),
        ]
        matches = renter_workspace.filter_properties(records, {
            "city": "Pune", "locality": "aund", "property_type": "", "source": "Owner listings",
            "min_rent": 20_000, "max_rent": 40_000, "bedrooms": "2", "sort_by": "rent",
        })
        self.assertEqual([item[0]["id"] for item in matches], ["listing-pune"])

    def test_search_keeps_catalog_available_when_records_endpoint_returns_404(self):
        from frontend.pages import renter_support

        with patch(
            "frontend.pages.renter_support.records",
            side_effect=[[], ApplicationAPIError(
                "RentAI could not find the renter records endpoint.",
                status_code=404,
                method="GET",
                path="/application/auth/records?kind=favorite",
                record_kind="favorite",
                response_detail="Not Found",
            )],
        ), patch(
            "frontend.pages.renter_support.listing_images", return_value=[]
        ):
            app = AppTest.from_string(
                "import pandas as pd\nfrom frontend.pages.renter_workspace import render_search\n"
                "render_search(pd.DataFrame([{'property_id':'RENT-1','city':'Pune','rent':24000}]))",
                default_timeout=30,
            ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Search Properties")
        self.assertIn("dataset search is still available", app.warning[0].value)
        self.assertTrue(any("1 matching catalog entries" in item.value for item in app.caption))
        self.assertTrue(any(
            "/application/auth/records?kind=favorite returned HTTP 404 · Not Found" in item.value
            for item in app.caption
        ))
        self.assertTrue(app.button(key="save_search_catalog_RENT-1").disabled)

    def test_listing_endpoint_failure_does_not_invent_live_properties(self):
        from frontend.pages import renter_support

        with patch(
            "frontend.pages.renter_support.records",
            side_effect=ApplicationAPIError(
                "RentAI could not find the renter records endpoint.", status_code=404,
                method="GET", path="/application/auth/records?kind=listing",
                record_kind="listing", response_detail="Not Found",
            ),
        ), patch("frontend.pages.renter_support.listing_images") as images:
            app = AppTest.from_string(
                "import pandas as pd\nfrom frontend.pages.renter_workspace import render_search\n"
                "render_search(pd.DataFrame([{'property_id':'catalog-real','city':'Pune','rent':24000}]))",
                default_timeout=30,
            ).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Published Owner listings are unavailable" in item.value for item in app.info))
        self.assertFalse(any("Published Owner listing" in item.value for item in app.caption))
        self.assertTrue(any("RentAI dataset property · not a live Owner listing" in item.value for item in app.caption))
        images.assert_not_called()

    def test_missing_saved_record_shows_friendly_message_without_breaking_card(self):
        from frontend.pages import renter_support

        with patch(
            "frontend.application_api.request",
            side_effect=ApplicationAPIError(
                "This record is no longer available. Refresh and try again.",
                status_code=404, method="DELETE", path="/application/records/favorite-123",
                record_id="favorite-123",
            ),
        ):
            app = AppTest.from_string(
                "from frontend.pages.renter_support import display_property_card\n"
                "display_property_card({'property_id':'RENT-1','city':'Pune','rent':24000}, "
                "source='catalog', favorites=[{'id':'favorite-123','kind':'favorite','data':"
                "{'property_id':'RENT-1','source':'catalog'}}], key_suffix='test')",
                default_timeout=30,
            ).run()
            app.button(key="save_test_catalog_RENT-1").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(
            app.error[0].value,
            "This saved property is no longer in your saved list. Refresh the page to continue.",
        )

    def test_application_api_maps_auth_not_found_and_server_errors_separately(self):
        cases = (
            (401, "Your session has expired. Sign in again."),
            (403, "Your account is not allowed to perform that action."),
            (404, "This record is no longer available. Refresh and try again."),
            (503, "RentAI's application service is temporarily unavailable. Please try again later."),
        )
        for status, expected_message in cases:
            with self.subTest(status=status), patch(
                "frontend.application_api.st.session_state", {"rentai_id_token": "test-token"}
            ), patch("frontend.application_api.get_api_base_url", return_value="http://127.0.0.1:8001"), patch(
                "frontend.application_api.urlopen",
                side_effect=HTTPError(
                    "http://127.0.0.1:8001/application/records/favorite-123",
                    status, "failure", None,
                    BytesIO(b'{"detail":"Record not found"}' if status == 404 else b"{}"),
                ),
            ):
                with self.assertRaises(ApplicationAPIError) as caught:
                    request("DELETE", "/application/records/favorite-123")
            self.assertEqual(str(caught.exception), expected_message)
            self.assertEqual(caught.exception.status_code, status)
            if status == 404:
                self.assertEqual(caught.exception.record_id, "favorite-123")
                self.assertEqual(caught.exception.response_detail, "Record not found")

        with patch(
            "frontend.application_api.st.session_state", {"rentai_id_token": "test-token"}
        ), patch("frontend.application_api.get_api_base_url", return_value="http://127.0.0.1:8001"), patch(
            "frontend.application_api.urlopen",
            side_effect=HTTPError(
                "http://127.0.0.1:8001/application/auth/records?kind=listing",
                404, "failure", None,
                BytesIO(b'{"detail":{"message":"Not Found","record_ids":["private-id"]}}'),
            ),
        ):
            with self.assertRaises(ApplicationAPIError) as caught:
                request("GET", "/application/auth/records", params={"kind": "listing"})
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIsNone(caught.exception.record_id)
        self.assertEqual(caught.exception.record_kind, "listing")
        self.assertEqual(caught.exception.path, "/application/auth/records?kind=listing")
        self.assertEqual(caught.exception.response_detail, "Not Found")
        self.assertNotIn("private-id", caught.exception.response_detail)
        self.assertEqual(
            str(caught.exception), "RentAI could not find the renter records endpoint."
        )

    def test_renter_settings_validation_only_accepts_supported_search_options(self):
        with self.assertRaises(HTTPException):
            ApplicationService._validate_profile_data({
                "record_type": "renter_settings", "sort_by": "income", "sort_order": "asc",
            })
        self.assertEqual(ApplicationService._validate_profile_data({
            "record_type": "renter_settings", "sort_by": "area_sqft", "sort_order": "desc",
        }), {"record_type": "renter_settings", "sort_by": "area_sqft", "sort_order": "desc"})

    def test_renter_pages_render_with_empty_authorized_data(self):
        from frontend.pages import renter_workspace

        empty = {"favorites": [], "enquiries": [], "listings": [], "profiles": [], "maintenance": [], "catalog": []}
        page_renderers = (
            renter_workspace.render_dashboard,
            renter_workspace.render_search,
            renter_workspace.render_prediction,
            renter_workspace.render_compare,
            renter_workspace.render_saved,
            renter_workspace.render_enquiries,
        )
        for render_page in page_renderers:
            with self.subTest(page=render_page.__name__), patch(
                "frontend.pages.renter_workspace._load", return_value=empty
            ):
                app = AppTest.from_string(
                    f"import pandas as pd\nfrom frontend.pages.renter_workspace import {render_page.__name__}\n"
                    f"{render_page.__name__}(pd.DataFrame())", default_timeout=30
                ).run()
                self.assertFalse(app.exception)

        with patch("frontend.pages.renter_workspace.records", return_value=[]):
            for render_page in (renter_workspace.render_profile, renter_workspace.render_settings):
                with self.subTest(page=render_page.__name__):
                    app = AppTest.from_string(
                        f"from frontend.pages.renter_workspace import {render_page.__name__}\n"
                        f"{render_page.__name__}()", default_timeout=30
                    ).run()
                    self.assertFalse(app.exception)

    def test_dashboard_and_search_render_real_listing_shapes(self):
        from frontend.pages import renter_workspace

        catalog_item = {
            "property_id": "RENT-1", "city": "Pune", "locality": "Aundh",
            "property_type": "Apartment", "rent": 24000, "bedrooms": 2,
            "bathrooms": 1, "area_sqft": 900, "posted_date": "2026-05-01",
        }
        owner_listing = {
            "id": "owner-list-1", "title": "Sunny flat", "city": "Pune",
            "locality": "Baner", "property_type": "Apartment", "rent": 28000,
            "bedrooms": 2, "bathrooms": 2, "status": "published",
        }
        data = {"favorites": [], "enquiries": [], "profiles": [], "maintenance": [],
                "listings": [{"id": "owner-list-1", "data": owner_listing}],
                "catalog": [catalog_item]}
        with patch("frontend.pages.renter_workspace._load", return_value=data):
            for renderer in (renter_workspace.render_dashboard, renter_workspace.render_search):
                with self.subTest(page=renderer.__name__):
                    app = AppTest.from_string(
                        f"import pandas as pd\nfrom frontend.pages.renter_workspace import {renderer.__name__}\n"
                        f"{renderer.__name__}(pd.DataFrame())", default_timeout=30
                    ).run()
                    self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
