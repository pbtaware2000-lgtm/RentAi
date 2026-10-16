"""Small HTTP client for authenticated application records."""

import json
import logging
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import streamlit as st

from backend.core.config import SettingsError, get_api_base_url


# Temporary, value-free checkout diagnostics; remove after confirming the live flow.
_payment_logger = logging.getLogger(__name__)


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


def request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    params: dict | None = None,
    raw_body: bytes | None = None,
    content_type: str = "application/json",
):
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
    data = raw_body if raw_body is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "Content-Type": content_type,
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
        elif exc.code == 409:
            message = response_detail or "This conflicts with an existing request. Refresh and try again."
        if exc.code == 404:
            if record_id:
                message = "This record is no longer available. Refresh and try again."
            elif path == "/application/auth/records":
                message = "RentAI could not find the renter records endpoint."
            elif path == "/application/auth/listing-images":
                message = "RentAI could not find the listing images endpoint."
            elif path == "/application/auth/profile-images":
                message = "RentAI could not find the profile images endpoint."
            else:
                message = "RentAI could not find the requested API endpoint."
        elif path.endswith("/images") and exc.code == 413:
            message = response_detail or "This image exceeds the upload limit. Choose a smaller file."
        elif path.endswith("/images") and exc.code == 400:
            message = response_detail or "This image could not be uploaded. Choose a supported image file."
        elif path.endswith("/images") and exc.code >= 500:
            message = response_detail or "Image storage is temporarily unavailable. Check the server image configuration and try again."
        elif exc.code >= 500:
            message = "RentAI's application service is temporarily unavailable. Please try again later."
        elif exc.code not in {401, 403, 409}:
            message = "RentAI could not complete that request. Please try again."
        if path.startswith("/application/payments/razorpay/") and response_detail and exc.code not in {401, 403}:
            message = response_detail
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


def profile_images() -> list[dict]:
    return request("GET", "/application/auth/profile-images").get("images", [])


def upload_image(record_id: str, content: bytes, filename: str, mime_type: str) -> dict:
    """Upload validated image bytes through the authenticated FastAPI endpoint."""
    safe_name = filename.replace("\\", "/").split("/")[-1]
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", safe_name)[:120] or "image"
    safe_mime = (
        mime_type if re.fullmatch(r"image/[A-Za-z0-9.+-]+", mime_type)
        else "application/octet-stream"
    )
    boundary = f"rentai-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: {safe_mime}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return request(
        "POST", f"/application/records/{quote(record_id, safe='')}/images",
        raw_body=body, content_type=f"multipart/form-data; boundary={boundary}",
    )


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


def create_rent_payment_order(rent_record_id: str) -> dict:
    try:
        api_port = urlsplit(get_api_base_url()).port
    except SettingsError:
        api_port = None
    _payment_logger.info(
        "Razorpay checkout stage=order_request_started session_token_present=%s api_port=%s",
        bool(st.session_state.get("rentai_id_token")), api_port,
    )
    try:
        result = request("POST", f"/application/payments/razorpay/rent/{rent_record_id}/order")
    except ApplicationAPIError as exc:
        _payment_logger.warning(
            "Razorpay checkout stage=order_request_failed http_status=%s",
            exc.status_code,
        )
        raise
    if not isinstance(result, dict) or not all(result.get(key) for key in ("key_id", "order_id", "amount", "currency")):
        _payment_logger.error("Razorpay checkout stage=order_response_invalid")
        raise ApplicationAPIError("RentAI returned incomplete Razorpay checkout details.")
    _payment_logger.info(
        "Razorpay checkout stage=order_response_received key_id_present=%s order_id_present=%s amount_present=%s currency=%s",
        bool(result.get("key_id")), bool(result.get("order_id")),
        result.get("amount") is not None, result.get("currency"),
    )
    return result


def verify_rent_payment(payment_id: str, order_id: str, signature: str) -> dict:
    _payment_logger.info("Razorpay checkout stage=verification_request_started")
    try:
        result = request("POST", "/application/payments/razorpay/verify", payload={
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": order_id,
            "razorpay_signature": signature,
        })
    except ApplicationAPIError as exc:
        _payment_logger.warning(
            "Razorpay checkout stage=verification_request_failed http_status=%s",
            exc.status_code,
        )
        raise
    if not isinstance(result, dict) or result.get("status") != "paid":
        _payment_logger.error("Razorpay checkout stage=verification_response_unconfirmed")
        raise ApplicationAPIError("RentAI could not confirm this payment.")
    _payment_logger.info("Razorpay checkout stage=verification_confirmed")
    return result


def predict_rent(features: dict) -> float:
    result = request("POST", "/ml/rent-prediction", payload=features)
    if not isinstance(result, dict) or isinstance(result.get("predicted_rent"), bool):
        raise ApplicationAPIError("RentAI returned an invalid rent prediction.")
    try:
        return float(result["predicted_rent"])
    except (KeyError, TypeError, ValueError):
        raise ApplicationAPIError("RentAI returned an invalid rent prediction.") from None


def classify_rent(features: dict) -> str:
    result = request("POST", "/ml/rent-classification", payload=features)
    category = result.get("rent_category") if isinstance(result, dict) else None
    if not isinstance(category, str) or not category.strip():
        raise ApplicationAPIError("RentAI returned an invalid rent category.")
    return category


def similar_properties_for_features(features: dict, limit: int = 5) -> list[dict]:
    result = request(
        "POST", "/ml/similar-properties/search",
        payload={**features, "limit": limit},
    )
    properties = result.get("properties") if isinstance(result, dict) else None
    if not isinstance(properties, list) or any(not isinstance(item, dict) for item in properties):
        raise ApplicationAPIError("RentAI returned invalid comparable properties.")
    return properties
