"""Safe Grok configuration check and opt-in live xAI smoke test.

Configuration-only by default. Pass --live to make authenticated, billable
requests: first GET /v1/models, then one RentAI chatbot completion.
"""
import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.core.config import get_xai_config, xai_configuration_status

API_ROOT = "https://api.x.ai/v1"
ERROR_TEXT_LIMIT = 2000


def _sanitize_text(value: object, api_key: str) -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = re.sub(r"(?i)Bearer\s+[^\s,\"']+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)\b(?:xai|sk)-[A-Za-z0-9_-]{16,}\b", "[redacted]", text)
    return re.sub(r"[\r\n\t]+", " ", text)[:ERROR_TEXT_LIMIT]


def _safe_error_payload(raw_body: bytes, api_key: str) -> str:
    """Return useful provider error fields while omitting arbitrary response data."""
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _sanitize_text(raw_body.decode("utf-8", errors="replace"), api_key) or "<empty response body>"

    if not isinstance(payload, dict):
        return _sanitize_text(json.dumps(payload), api_key)

    safe = {}
    for name in ("code", "type", "param", "message"):
        if payload.get(name) is not None:
            safe[name] = _sanitize_text(payload[name], api_key)

    error = payload.get("error")
    if isinstance(error, dict):
        safe_error = {
            name: _sanitize_text(error[name], api_key)
            for name in ("code", "type", "param", "message")
            if error.get(name) is not None
        }
        if safe_error:
            safe["error"] = safe_error
    elif error is not None:
        safe["error"] = _sanitize_text(error, api_key)

    if not safe and payload:
        return "Provider returned JSON without recognized error fields."
    return json.dumps(safe, ensure_ascii=True) if safe else "<empty response body>"


def _request_id(headers) -> str | None:
    """Read only known request-ID headers; never dump response headers."""
    for name in ("x-request-id", "request-id", "xai-request-id", "x-grok-request-id"):
        value = headers.get(name) if headers else None
        if value:
            candidate = str(value).strip()
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", candidate):
                return candidate
    return None


def _report_http_error(exc: HTTPError, api_key: str, stage: str) -> None:
    try:
        raw_body = exc.read(16_384)
    except OSError:
        raw_body = b""
    print(f"xAI request failed during {stage}: HTTP {exc.code}", file=sys.stderr)
    print("Sanitized xAI response body: " + _safe_error_payload(raw_body, api_key), file=sys.stderr)
    request_id = _request_id(exc.headers)
    if request_id:
        print(f"xAI request ID: {request_id}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call xAI models and chat endpoints (may incur charges).")
    args = parser.parse_args()
    status = xai_configuration_status()
    print(json.dumps(status, indent=2))
    config = get_xai_config()
    if not config["api_key"]:
        print("No XAI_API_KEY found. Set it in the FastAPI process environment or project .env.", file=sys.stderr)
        return 2
    if not args.live:
        print("Configuration is present. No external API request was made.")
        return 0

    request_stage = "model list"
    try:
        models_request = Request(
            f"{API_ROOT}/models", headers={"Authorization": f"Bearer {config['api_key']}"}, method="GET"
        )
        with urlopen(models_request, timeout=15) as response:
            models_payload = json.loads(response.read().decode("utf-8"))
        model_ids = {
            item.get("id") for item in models_payload.get("data", []) if isinstance(item, dict)
        }
        if config["model"] not in model_ids:
            print(f"Authentication succeeded, but configured model '{config['model']}' is not listed for this account.", file=sys.stderr)
            print("Available model IDs: " + ", ".join(sorted(model for model in model_ids if model)), file=sys.stderr)
            return 3

        request_stage = "chat completion"
        completion_request = Request(
            f"{API_ROOT}/chat/completions",
            data=json.dumps({
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": "Answer only about using RentAI."},
                    {"role": "user", "content": "How can I search for rental properties in RentAI?"},
                ],
                "max_tokens": 120,
            }).encode("utf-8"),
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(completion_request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        answer = payload["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            print("xAI authenticated, but returned an empty completion.", file=sys.stderr)
            return 4
        print(f"Authenticated API smoke test succeeded with model '{config['model']}'.")
        print(answer.strip())
        return 0
    except HTTPError as exc:
        _report_http_error(exc, config["api_key"], request_stage)
        if exc.code == 401:
            reason = "Authentication failed (HTTP 401). Check that this active key is copied from the xAI Console."
        elif exc.code == 403:
            reason = "Permission denied (HTTP 403). Check account and model permissions in the xAI Console."
        elif exc.code == 404:
            reason = "Model or endpoint unavailable (HTTP 404). Check the account's model list and configured endpoint."
        elif exc.code == 429:
            reason = "Rate limited (HTTP 429). Wait before retrying."
        else:
            reason = f"xAI returned HTTP {exc.code}."
        print(reason, file=sys.stderr)
        return 5
    except (URLError, TimeoutError, OSError) as exc:
        # Log only exception type; URL errors can embed request details.
        print(f"Network request failed ({type(exc).__name__}); check API-server connectivity and retry.", file=sys.stderr)
        return 6
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"xAI returned an unexpected response ({type(exc).__name__}).", file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
