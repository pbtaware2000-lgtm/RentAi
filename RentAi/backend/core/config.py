"""Shared, secret-safe configuration for Streamlit and FastAPI processes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT=Path(__file__).resolve().parents[2]
_payment_settings = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
_payment_settings_from_process = {name for name in _payment_settings if os.getenv(name) is not None}
load_dotenv(PROJECT_ROOT/".env",override=False)
# Shared configuration is imported by Streamlit too. If Razorpay values came
# from the shared .env file, leave them for the API process to load explicitly.
for _payment_setting in _payment_settings:
    if _payment_setting not in _payment_settings_from_process:
        os.environ.pop(_payment_setting, None)
class SettingsError(RuntimeError): pass

def get_api_base_url():
    value=os.getenv("RENTAI_API_URL","http://127.0.0.1:8001").strip().rstrip("/")
    parsed=urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password: raise SettingsError("RENTAI_API_URL is invalid.")
    if parsed.scheme!="https" and not(parsed.scheme=="http" and parsed.hostname in {"127.0.0.1","localhost","::1"}): raise SettingsError("RENTAI_API_URL must use HTTPS outside local development.")
    return value


def startup_configuration_issues() -> tuple[str, ...]:
    """Report only setting names that need attention; never return their values."""
    settings = load_settings()
    issues: list[str] = []
    if not settings.firebase_project_id or settings.firebase_project_id != EXPECTED_FIREBASE_PROJECT_ID:
        issues.append("FIREBASE_PROJECT_ID")
    key = settings.firebase_web_api_key
    try:
        encoded_key = key.encode("ascii")
    except UnicodeEncodeError:
        encoded_key = b""
    if not key or not encoded_key or any(byte < 0x21 or byte > 0x7E for byte in encoded_key):
        issues.append("FIREBASE_WEB_API_KEY")
    if settings.firebase_credentials_path and not settings.firebase_credentials_path.is_file():
        issues.append("FIREBASE_CREDENTIALS_PATH")
    try:
        get_api_base_url()
    except SettingsError:
        issues.append("RENTAI_API_URL")
    return tuple(dict.fromkeys(issues))
