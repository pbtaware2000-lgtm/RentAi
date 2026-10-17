"""Offline tests for safe xAI smoke-test diagnostics."""
import io
import unittest
from contextlib import redirect_stderr
from email.message import Message
from urllib.error import HTTPError

from backend.scripts.grok_smoke_test import _report_http_error, _safe_error_payload


class GrokSmokeDiagnosticsTests(unittest.TestCase):
    def test_error_payload_keeps_code_and_message_but_redacts_credentials(self):
        secret = "xai-abcdefghijklmnopqrstuvwxyz123456"
        body = (
            '{"code":"invalid-argument","error":{"message":"Rejected '
            + secret
            + '","type":"authentication_error","authorization":"Bearer hidden"}}'
        ).encode()
        result = _safe_error_payload(body, secret)
        self.assertIn('"code": "invalid-argument"', result)
        self.assertIn('"type": "authentication_error"', result)
        self.assertNotIn(secret, result)
        self.assertNotIn("hidden", result)
        self.assertIn("[redacted]", result)

    def test_http_failure_prints_status_body_and_request_id_not_headers(self):
        headers = Message()
        headers["x-request-id"] = "req_12345"
        headers["Authorization"] = "Bearer must-not-print"
        body = b'{"code":"invalid-argument","error":"Bad model parameter"}'
        error = HTTPError("https://api.x.ai/v1/models", 400, "Bad Request", headers, io.BytesIO(body))
        stream = io.StringIO()
        with redirect_stderr(stream):
            _report_http_error(error, "test-api-key", "model list")
        output = stream.getvalue()
        self.assertIn("HTTP 400", output)
        self.assertIn("invalid-argument", output)
        self.assertIn("Bad model parameter", output)
        self.assertIn("req_12345", output)
        self.assertNotIn("Authorization", output)
        self.assertNotIn("must-not-print", output)
        self.assertNotIn("test-api-key", output)


if __name__ == "__main__":
    unittest.main()
