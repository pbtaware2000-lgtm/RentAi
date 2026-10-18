"""Small Streamlit component wrapper for Razorpay Standard Checkout."""

from pathlib import Path

import streamlit.components.v1 as components


_checkout_component = components.declare_component(
    "rentai_razorpay_checkout",
    path=str(Path(__file__).with_name("razorpay_checkout")),
)


def razorpay_checkout(*, checkout: dict, key: str):
    """Render Checkout and return its untrusted response for server verification."""
    return _checkout_component(checkout=checkout, key=key, default=None)
