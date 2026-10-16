"""Shared non-secret local configuration."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

PROJECT_ROOT=Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT/".env",override=False)
class SettingsError(RuntimeError): pass

def get_api_base_url():
    value=os.getenv("RENTAI_API_URL","http://127.0.0.1:8001").strip().rstrip("/")
    parsed=urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password: raise SettingsError("RENTAI_API_URL is invalid.")
    if parsed.scheme!="https" and not(parsed.scheme=="http" and parsed.hostname in {"127.0.0.1","localhost","::1"}): raise SettingsError("RENTAI_API_URL must use HTTPS outside local development.")
    return value

def startup_configuration_issues():
    try: get_api_base_url(); return ()
    except SettingsError: return ("RENTAI_API_URL",)
