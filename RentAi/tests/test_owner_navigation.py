"""Checks that the Owner menu retains its section selection across reruns."""

import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
