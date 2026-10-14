"""Firebase Authentication session helpers for the Streamlit server."""

from __future__ import annotations

import json
import hmac
import logging
import os
import re
import socket
import ssl
import time
from backend.core.config import SettingsError, get_api_base_url, get_web_api_key, load_settings
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

AUTH_STATE_KEYS = (
    "rentai_id_token", "rentai_refresh_token", "rentai_expires_at",
    "rentai_uid", "rentai_role", "rentai_display_name", "rentai_email",
)
LOGIN_INPUT_KEYS = ("rentai_login_email", "rentai_login_password")
REGISTER_INPUT_KEYS = ("rentai_register_name", "rentai_register_email", "rentai_register_password", "rentai_register_confirm")
logger = logging.getLogger(__name__)
FIREBASE_ERROR_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![A-Za-z0-9_])"
)
FIREBASE_ERROR_CODES = {
    "API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED",
    "INVALID_API_KEY", "PROJECT_NOT_FOUND", "CONFIGURATION_NOT_FOUND",
    "OPERATION_NOT_ALLOWED", "PASSWORD_LOGIN_DISABLED", "EMAIL_NOT_FOUND",
    "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "USER_DISABLED", "INVALID_EMAIL",
    "TOO_MANY_ATTEMPTS_TRY_LATER", "INVALID_ARGUMENT", "MISSING_EMAIL", "MISSING_PASSWORD",
    "INVALID_OOB_CODE", "EXPIRED_OOB_CODE", "WEAK_PASSWORD", "EMAIL_EXISTS",
    "INVALID_ID_TOKEN", "TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN", "USER_NOT_FOUND",
    "ADMIN_ONLY_OPERATION", "QUOTA_EXCEEDED", "CAPTCHA_CHECK_FAILED",
}
FIREBASE_ERROR_CODE_PREFIXES = {"API_KEY", "INVALID_LOGIN", "TOO_MANY_ATTEMPTS", "EMAIL_NOT"}
EMAIL_ADDRESS_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthenticationError(RuntimeError):
    """Safe, user-facing authentication error without upstream response details."""


def normalize_email(email: str) -> str:
    """Trim boundary whitespace and reject incomplete or internally spaced addresses."""
    normalized = email.strip() if isinstance(email, str) else ""
    if not EMAIL_ADDRESS_PATTERN.fullmatch(normalized):
        raise AuthenticationError("Enter a valid complete email address.")
    return normalized


def _code_from_candidate(candidate: str) -> str:
    candidate = candidate.strip()
    exact_match = FIREBASE_ERROR_CODE_PATTERN.fullmatch(candidate)
    if exact_match:
        code = exact_match.group(1)
        return "" if code in FIREBASE_ERROR_CODE_PREFIXES else code
    for match in FIREBASE_ERROR_CODE_PATTERN.finditer(candidate):
        code = match.group(1)
        if code in FIREBASE_ERROR_CODES or code.count("_") >= 2:
            return code
    return ""


def _extract_firebase_error_code(error_body: object) -> str:
    """Extract a complete Firebase code from the standard REST error envelope."""
    if not isinstance(error_body, dict):
        return ""
    firebase_error = error_body.get("error")
    candidates: list[str] = []
    if isinstance(firebase_error, dict):
        # Firebase's REST error envelope normally returns the machine code here.
        value = firebase_error.get("message")
        if isinstance(value, str):
            candidates.append(value)
        value = firebase_error.get("status")
        if isinstance(value, str):
            candidates.append(value)
        for field in ("details", "errors"):
            nested = firebase_error.get(field)
            if isinstance(nested, dict):
                nested = [nested]
            if isinstance(nested, list):
                for nested_error in nested:
                    if isinstance(nested_error, dict):
                        for key in ("reason", "status", "code", "message"):
                            value = nested_error.get(key)
                            if isinstance(value, str):
                                candidates.append(value)
    elif isinstance(firebase_error, str):
        candidates.append(firebase_error)

    for candidate in candidates:
        code = _code_from_candidate(candidate)
        if code:
            return code
    return ""


def _firebase_error_details(error_body: object) -> tuple[str, str, str, str, str]:
    """Return extracted code/source plus the original JSON error fields separately."""
    if not isinstance(error_body, dict) or not isinstance(error_body.get("error"), dict):
        return "", "unavailable", "unavailable", "unavailable", "unavailable"
    error = error_body["error"]
    code = _extract_firebase_error_code(error_body)
    json_code = error.get("code", "unavailable")
    json_code = str(json_code) if isinstance(json_code, (str, int)) else "unavailable"
    raw_message = error.get("message")
    raw_status = error.get("status")
    raw_message = raw_message if isinstance(raw_message, str) else "unavailable"
    raw_status = raw_status if isinstance(raw_status, str) else "unavailable"
    source = "unavailable"
    # Attribute the code to the field that actually contains it, not merely the
    # first nonempty field in the envelope.
    for field in ("message", "status"):
        value = error.get(field)
        if code and isinstance(value, str) and _code_from_candidate(value) == code:
            source = f"error.{field}"
            break
    if source == "unavailable" and code:
        for field in ("details", "errors"):
            nested = error.get(field)
            if isinstance(nested, dict):
                nested = [nested]
            if isinstance(nested, list):
                for entry in nested:
                    if isinstance(entry, dict):
                        for key in ("reason", "status", "code", "message"):
                            value = entry.get(key)
                            if isinstance(value, str) and _code_from_candidate(value) == code:
                                source = f"error.{field}[].{key}"
                                break
                        if source != "unavailable":
                            break
            if source != "unavailable":
                break
    if source == "unavailable" and raw_message != "unavailable":
        source = "error.message"
    return code, source, json_code, raw_message, raw_status


def _sanitize_firebase_message(message: str, url: str, payload: dict) -> str:
    """Redact request values before any upstream message is written to logs."""
    sanitized = message
    for secret in (payload.get("password"), payload.get("email")):
        if isinstance(secret, str) and secret:
            sanitized = sanitized.replace(secret, "[redacted]")
    try:
        from urllib.parse import parse_qs

        api_key = parse_qs(urlsplit(url).query).get("key", [""])[0]
    except (TypeError, ValueError):
        api_key = ""
    if api_key:
        sanitized = sanitized.replace(api_key, "[redacted]")
    sanitized = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[redacted-email]", sanitized)
    sanitized = re.sub(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "[redacted-token]", sanitized)
    sanitized = re.sub(r"(?i)\b(?:uid|user[_ -]?id)\s*[:=]\s*[A-Za-z0-9_-]+", "[redacted-user-id]", sanitized)
    sanitized = re.sub(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)", "[redacted-phone]", sanitized)
    sanitized = "".join(ch for ch in sanitized if ch.isprintable())
    return sanitized[:200]


def firebase_api_key_diagnostics(
    expected_key: str, expected_project_id: str = "rentai-62ac9"
) -> dict[str, bool | None]:
    """Return configuration status only; never return configured or expected values."""
    settings = load_settings()
    raw_key = os.getenv("FIREBASE_WEB_API_KEY", "")
    raw_project = os.getenv("FIREBASE_PROJECT_ID", "")
    if not raw_key:
        raw_key = settings.firebase_web_api_key
    if not raw_project:
        raw_project = settings.firebase_project_id
    if not isinstance(expected_key, str) or not isinstance(expected_project_id, str):
        expected_key = ""
        expected_project_id = ""
    raw_expected_key = expected_key
    raw_expected_project_id = expected_project_id
    key = raw_key.strip()
    project = raw_project.strip()
    expected_key = expected_key.strip()
    expected_project_id = expected_project_id.strip()

    def ascii_bytes(value: str) -> bytes | None:
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError:
            return None
        if any(byte < 0x21 or byte > 0x7E for byte in encoded):
            return None
        return encoded

    project_bytes = ascii_bytes(project) if project else None
    expected_project_bytes = ascii_bytes(expected_project_id) if expected_project_id else None
    key_bytes = ascii_bytes(key) if key else None
    expected_key_bytes = ascii_bytes(expected_key) if expected_key else None
    project_present = bool(project)
    key_present = bool(key)
    return {
        "project_id_present": project_present,
        "project_id_matches": (
            hmac.compare_digest(project_bytes, expected_project_bytes)
            if project_bytes is not None and expected_project_bytes is not None else False
        ) if project_present else None,
        "web_api_key_present": key_present,
        "key_matches_console": (
            hmac.compare_digest(key_bytes, expected_key_bytes)
            if key_bytes is not None and expected_key_bytes is not None else False
        ) if key_present and expected_key else None,
        "whitespace_trimmed": (
            raw_key != key or raw_project != project
            or raw_expected_key != expected_key
            or raw_expected_project_id != expected_project_id
            or "FIREBASE_WEB_API_KEY" in settings.whitespace_trimmed
            or "FIREBASE_PROJECT_ID" in settings.whitespace_trimmed
        ),
    }


def firebase_api_key_matches(expected_key: str, expected_project_id: str = "rentai-62ac9") -> bool:
    """Compatibility wrapper for callers that need a single match result."""
    result = firebase_api_key_diagnostics(expected_key, expected_project_id)
    return bool(result["project_id_matches"] and result["key_matches_console"])


def _network_failure_category(exc: BaseException) -> str:
    """Classify transport failures without logging exception text or request URLs."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return "tls"
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(reason, ConnectionError):
        return "connection"
    if isinstance(reason, OSError) and getattr(reason, "errno", None) in {
        10051, 10060, 10061, 10065, 110, 111, 113,
    }:
        return "connection"
    return "network"


def _network_failure_message(service: str, category: str) -> str:
    service_name = "Firebase sign-in" if service == "firebase" else "RentAI authentication service"
    messages = {
        "dns": f"Could not resolve the {service_name} host. Check DNS and network settings.",
        "connection_refused": f"Could not connect to the {service_name}. Check that the service is running.",
        "connection": f"The connection to the {service_name} failed.",
        "timeout": f"The {service_name} request timed out.",
        "tls": f"A secure TLS connection to the {service_name} could not be established.",
        "network": f"A network error interrupted the {service_name} request.",
    }
    return messages.get(category, messages["network"])


ROLE_PAGE_ACCESS = {
    "owner": {
        "Owner Dashboard",
        "Home", "Learning Module", "Implementation Overview", "Dataset & EDA",
        "Rent Prediction", "Rent Classification", "Property Clustering",
        "Property Search", "Property Comparison", "Location & Analytics",
        "Property Insights", "Algo Lab",
    },
    "renter": {
        "Renter Dashboard",
        "Home", "Learning Module", "Rent Prediction", "Rent Classification",
        "Property Search", "Property Comparison", "Location & Analytics",
        "Property Insights",
    },
    "broker": {
        "Broker Dashboard",
        "Home", "Learning Module", "Rent Prediction", "Rent Classification",
        "Property Clustering", "Property Search", "Property Comparison",
        "Location & Analytics", "Property Insights", "Algo Lab",
    },
    "admin": None,
}


def accessible_pages(role: str, page_names) -> list[str]:
    if role not in ROLE_PAGE_ACCESS:
        raise AuthenticationError("This account does not have an approved RentAI role.")
    allowed = ROLE_PAGE_ACCESS[role]
    return [name for name in page_names if allowed is None or name in allowed]


def page_is_allowed(role: str, page: str, page_names) -> bool:
    return page in accessible_pages(role, page_names)


def _firebase_api_key() -> str:
    try:
        return get_web_api_key()
    except SettingsError as exc:
        raise AuthenticationError(f"Firebase sign-in is not configured correctly: {exc}") from None


def _firebase_request(url: str, payload: dict) -> dict:
    request = Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            error_code, error_source, json_error_code, firebase_message, firebase_status = _firebase_error_details(error_body)
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            error_code, error_source = "", "unavailable"
            json_error_code, firebase_message, firebase_status = "unavailable", "unavailable", "unavailable"
        firebase_message = _sanitize_firebase_message(firebase_message, url, payload)
        json_error_code = _sanitize_firebase_message(json_error_code, url, payload)
        firebase_status = _sanitize_firebase_message(firebase_status, url, payload)
        safe_error_code = error_code
        if safe_error_code in {"API_KEY_INVALID", "INVALID_API_KEY"}:
            message = "Firebase rejected the Web API key. Check FIREBASE_WEB_API_KEY."
        elif safe_error_code in {"API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED"}:
            message = "Firebase blocked this API key. Check its API restrictions and application restrictions."
        elif safe_error_code == "PROJECT_NOT_FOUND":
            message = "Firebase could not find the project associated with this API key."
        elif safe_error_code == "CONFIGURATION_NOT_FOUND":
            message = "Firebase sign-in configuration was not found. Check the project and Email/Password provider."
        elif safe_error_code in {"OPERATION_NOT_ALLOWED", "PASSWORD_LOGIN_DISABLED"}:
            message = "Firebase Email/Password sign-in is disabled for this project."
        elif safe_error_code == "INVALID_EMAIL":
            message = "Enter a valid complete email address."
        elif safe_error_code == "EMAIL_EXISTS":
            message = "An account with this email already exists. Sign in instead."
        elif safe_error_code == "WEAK_PASSWORD":
            message = "Choose a stronger password (at least 6 characters)."
        elif safe_error_code in {
            "EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS",
            "USER_DISABLED",
        }:
            message = "Email or password is incorrect, or this account cannot sign in."
        elif safe_error_code == "TOO_MANY_ATTEMPTS_TRY_LATER":
            message = "Firebase temporarily blocked sign-in attempts. Try again later."
        elif exc.code in {400, 401, 403}:
            message = "Firebase rejected the sign-in request."
        else:
            message = "Firebase sign-in is temporarily unavailable."
        if safe_error_code in {"API_KEY_INVALID", "INVALID_API_KEY"}:
            diagnostic = "api_key_rejected"
        elif safe_error_code in {"OPERATION_NOT_ALLOWED", "PASSWORD_LOGIN_DISABLED"}:
            diagnostic = "password_provider_disabled"
        elif safe_error_code in {
            "EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS",
            "USER_DISABLED",
        }:
            diagnostic = "credentials_rejected"
        elif safe_error_code == "INVALID_EMAIL":
            diagnostic = "invalid_email"
        elif safe_error_code == "EMAIL_EXISTS":
            diagnostic = "account_exists"
        else:
            diagnostic = "http_error"
        if safe_error_code:
            message += f" Firebase error code: {safe_error_code}."
        elif exc.code in {400, 401, 403}:
            message += f" (HTTP {exc.code}; Firebase error code unavailable)."
        logger.warning(
            "auth request failed service=firebase http_status=%d category=%s code=%s "
            "code_source=%s json_error_code=%s error_message=%s error_status=%s "
            "payload_fields=%s payload_types=%s",
            exc.code,
            diagnostic,
            safe_error_code or "unavailable",
            error_source,
            json_error_code,
            firebase_message,
            firebase_status,
            ",".join(sorted(payload)),
            ",".join(f"{key}:{type(value).__name__}" for key, value in sorted(payload.items())),
        )
        raise AuthenticationError(message) from None
    except (URLError, TimeoutError, OSError) as exc:
        category = _network_failure_category(exc)
        logger.warning("auth transport failed service=firebase category=%s", category)
        raise AuthenticationError(_network_failure_message("firebase", category)) from None
    except json.JSONDecodeError:
        logger.warning("auth response invalid service=firebase category=invalid_json")
        raise AuthenticationError("Firebase returned an invalid sign-in response.") from None


def _session_tokens(response: dict, now: float | None = None) -> dict:
    try:
        lifetime = int(response["expiresIn"])
        return {
            "id_token": response["idToken"],
            "refresh_token": response["refreshToken"],
            "uid": response["localId"],
            "expires_at": (time.time() if now is None else now) + lifetime,
        }
    except (KeyError, TypeError, ValueError):
        raise AuthenticationError("Firebase returned an invalid sign-in response.") from None


def firebase_password_sign_in(email: str, password: str, now: float | None = None) -> dict:
    if not isinstance(email, str) or not email.strip() or not password:
        raise AuthenticationError("Enter your email and password.")
    email = normalize_email(email)
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?" + urlencode(
        {"key": _firebase_api_key()}
    )
    response = _firebase_request(url, {
        "email": email.strip(), "password": password, "returnSecureToken": True,
    })
    return _session_tokens(response, now)


def firebase_password_sign_up(email: str, password: str, display_name: str) -> dict:
    if not isinstance(email, str) or not email.strip() or not password or not display_name.strip():
        raise AuthenticationError("Enter your name, email, and password.")
    email = normalize_email(email)
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signUp?" + urlencode(
        {"key": _firebase_api_key()}
    )
    response = _firebase_request(url, {
        "email": email, "password": password,
        "displayName": display_name.strip(), "returnSecureToken": True,
    })
    return _session_tokens(response)


def provision_public_role(id_token: str, role: str) -> dict:
    request = Request(
        _api_base_url() + "/application/auth/register",
        data=json.dumps({"role": role}).encode("utf-8"),
        headers={"Authorization": f"Bearer {id_token}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 409:
            raise AuthenticationError("This account already has a role assigned. Sign in instead.") from None
        if exc.code == 503:
            raise AuthenticationError("RentAI could not save your account role. Try again later.") from None
        raise AuthenticationError("RentAI could not complete registration.") from None
    except (URLError, TimeoutError, OSError) as exc:
        category = _network_failure_category(exc)
        logger.warning("auth transport failed service=rentai_registration category=%s", category)
        raise AuthenticationError(_network_failure_message("rentai_api", category)) from None
    except json.JSONDecodeError:
        raise AuthenticationError("RentAI returned an invalid registration response.") from None
    return result


def refresh_firebase_session(refresh_token: str, now: float | None = None) -> dict:
    url = "https://securetoken.googleapis.com/v1/token?" + urlencode({"key": _firebase_api_key()})
    request = Request(
        url,
        data=urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        normalized = {
            "idToken": payload["id_token"],
            "refreshToken": payload.get("refresh_token", refresh_token),
            "localId": payload["user_id"],
            "expiresIn": payload["expires_in"],
        }
    except HTTPError as exc:
        logger.warning("auth request failed service=firebase_refresh status=%d category=http_error", exc.code)
        raise AuthenticationError("Your RentAI session expired. Please sign in again.") from None
    except (URLError, TimeoutError, OSError) as exc:
        category = _network_failure_category(exc)
        logger.warning("auth transport failed service=firebase_refresh category=%s", category)
        raise AuthenticationError(_network_failure_message("firebase", category)) from None
    except (KeyError, ValueError, json.JSONDecodeError):
        logger.warning("auth response invalid service=firebase_refresh category=invalid_response")
        raise AuthenticationError("Unable to refresh your RentAI session. Please sign in again.") from None
    return _session_tokens(normalized, now)


def _api_base_url() -> str:
    try:
        return get_api_base_url()
    except SettingsError as exc:
        raise AuthenticationError(str(exc)) from None


def fetch_verified_user(id_token: str) -> dict:
    request = Request(
        _api_base_url() + "/application/auth/me",
        headers={"Authorization": f"Bearer {id_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=12) as response:
            user = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise AuthenticationError("Your RentAI session expired. Please sign in again.") from None
        if exc.code == 403:
            raise AuthenticationError("Your account is not assigned a RentAI role. Contact an administrator.") from None
        if exc.code >= 500:
            raise AuthenticationError("RentAI authentication service is temporarily unavailable.") from None
        raise AuthenticationError("RentAI could not verify your account.") from None
    except (URLError, TimeoutError, OSError) as exc:
        category = _network_failure_category(exc)
        logger.warning("auth transport failed service=rentai_api category=%s", category)
        raise AuthenticationError(_network_failure_message("rentai_api", category)) from None
    except json.JSONDecodeError:
        logger.warning("auth response invalid service=rentai_api category=invalid_json")
        raise AuthenticationError("RentAI returned an invalid authentication response.") from None
    if not isinstance(user, dict) or not user.get("uid") or user.get("role") not in ROLE_PAGE_ACCESS:
        raise AuthenticationError("RentAI returned an invalid account profile.")
    return {
        "uid": user["uid"], "role": user["role"],
        "name": user.get("name") if isinstance(user.get("name"), str) else "",
        "email": user.get("email") if isinstance(user.get("email"), str) else "",
    }


def save_session(session, tokens: dict, user: dict) -> None:
    session["rentai_id_token"] = tokens["id_token"]
    session["rentai_refresh_token"] = tokens["refresh_token"]
    session["rentai_expires_at"] = tokens["expires_at"]
    session["rentai_uid"] = user["uid"]
    session["rentai_role"] = user["role"]
    session["rentai_display_name"] = user.get("name", "")
    session["rentai_email"] = user.get("email", "")


def clear_auth_session(
    session, *, clear_login_inputs: bool = False, reset_page: bool = True
) -> None:
    for key in AUTH_STATE_KEYS:
        session.pop(key, None)
    if clear_login_inputs:
        for key in LOGIN_INPUT_KEYS:
            session.pop(key, None)
    if reset_page:
        session.pop("rentai_selected_role", None)
        session.pop("rentai_auth_view", None)
        session["page"] = "Home"


def current_session_user(session, now: float | None = None) -> dict | None:
    id_token = session.get("rentai_id_token")
    refresh_token = session.get("rentai_refresh_token")
    if not id_token or not refresh_token:
        # Preserve the in-session role selection while public auth screens rerun.
        clear_auth_session(session, reset_page=False)
        return None
    current_time = time.time() if now is None else now
    if session.get("rentai_expires_at", 0) <= current_time + 60:
        tokens = refresh_firebase_session(refresh_token, current_time)
        session["rentai_id_token"] = tokens["id_token"]
        session["rentai_refresh_token"] = tokens["refresh_token"]
        session["rentai_expires_at"] = tokens["expires_at"]
        id_token = tokens["id_token"]
    user = fetch_verified_user(id_token)
    session["rentai_uid"] = user["uid"]
    session["rentai_role"] = user["role"]
    session["rentai_display_name"] = user.get("name", "")
    session["rentai_email"] = user.get("email", "")
    return user
