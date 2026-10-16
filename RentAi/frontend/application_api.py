"""Small HTTP client for authenticated application records."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

from backend.core.config import SettingsError, get_api_base_url


class ApplicationAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        method: str | None = None,
        path: str | None = None,
        record_id: str | None = None,
        record_kind: str | None = None,
        response_detail: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.method = method
        self.path = path
        self.record_id = record_id
        self.record_kind = record_kind
        self.response_detail = response_detail


def _safe_response_detail(error: HTTPError) -> str | None:
    try:
        payload = json.loads(error.read(4096).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    if not isinstance(detail, str):
        return None
    normalized = " ".join(detail.split())
    return normalized[:240] or None


def request(method: str, path: str, *, payload: dict | None = None, params: dict | None = None):
    token = st.session_state.get("rentai_id_token")
    if not token:
        raise ApplicationAPIError("Your session has expired. Sign in again.")
    try:
        url = get_api_base_url() + path
    except SettingsError:
        raise ApplicationAPIError("RentAI API is not configured correctly.") from None
    request_path = path
    if params:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        if query:
            url += "?" + query
            request_path += "?" + query
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "Content-Type": "application/json",
    }, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except HTTPError as exc:
        response_detail = _safe_response_detail(exc)
        path_parts = path.strip("/").split("/")
        record_id = path_parts[2] if len(path_parts) == 3 and path_parts[:2] == ["application", "records"] else None
        if exc.code == 403:
            message = "Your account is not allowed to perform that action."
        elif exc.code == 401:
            message = "Your session has expired. Sign in again."
        if exc.code == 404:
            if record_id:
                message = "This record is no longer available. Refresh and try again."
            elif path.startswith("/bookings") and response_detail:
                message = response_detail
            elif path == "/application/auth/records":
                message = "RentAI could not find the renter records endpoint."
            elif path == "/application/auth/listing-images":
                message = "RentAI could not find the listing images endpoint."
            else:
                message = "RentAI could not find the requested API endpoint."
        elif exc.code >= 500:
            message = "RentAI's application service is temporarily unavailable. Please try again later."
        elif exc.code == 409 and response_detail:
            message = response_detail
        elif exc.code not in {401, 403}:
            message = "RentAI could not complete that request. Please try again."
        raise ApplicationAPIError(
            message,
            status_code=exc.code,
            method=method,
            path=request_path,
            record_id=record_id,
            record_kind=(params or {}).get("kind"),
            response_detail=response_detail,
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ApplicationAPIError("Could not reach RentAI. Check that the API is running.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApplicationAPIError("RentAI returned an invalid response.") from None


def records(kind: str | None = None) -> list[dict]:
    result = request("GET", "/application/auth/records", params={"kind": kind})
    return result.get("records", [])


def listing_images() -> list[dict]:
    return request("GET", "/application/auth/listing-images").get("images", [])


def create(kind: str, data: dict) -> dict:
    return request("POST", "/application/records", payload={"kind": kind, "data": data})


def update(record: dict, data: dict) -> dict:
    return request("PUT", f"/application/records/{record['id']}", payload={"kind": record["kind"], "data": data})


def delete(record: dict) -> None:
    try:
        request("DELETE", f"/application/records/{record['id']}")
    except ApplicationAPIError as exc:
        if exc.status_code == 404 and record.get("kind") == "favorite":
            raise ApplicationAPIError(
                "This saved property is no longer in your saved list. Refresh the page to continue.",
                status_code=exc.status_code,
                method=exc.method,
                path=exc.path,
                record_id=str(record.get("id", "")),
                record_kind="favorite",
                response_detail=exc.response_detail,
            ) from None
        exc.record_kind = record.get("kind")
        raise


def admin_users() -> list[dict]:
    return request("GET", "/application/admin/users").get("users", [])


def set_user_role(uid: str, role: str) -> None:
    request("PUT", f"/application/admin/users/{uid}/role", payload={"role": role})


def bookings() -> list[dict]:
    return request("GET", "/bookings").get("bookings", [])


def booking_notifications() -> list[dict]:
    return request("GET", "/bookings/notifications").get("notifications", [])


def booking_payment_configured() -> bool:
    return bool(request("GET", "/bookings/payment-config").get("configured"))


def request_booking(property_id: str, move_in_date: str) -> dict:
    return request("POST", "/bookings", payload={"property_id": property_id, "move_in_date": move_in_date})["booking"]


def decide_booking(booking_id: str, decision: str) -> dict:
    return request("POST", f"/bookings/{booking_id}/decision", payload={"decision": decision})["booking"]


def booking_checkout(booking_id: str) -> dict:
    return request("POST", f"/bookings/{booking_id}/checkout")
