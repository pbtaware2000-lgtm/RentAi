"""Renter Dashboard home page for the dedicated Renter workspace."""

from frontend.pages.renter_workspace import render_dashboard


def render(data=None) -> None:
    render_dashboard(data)
