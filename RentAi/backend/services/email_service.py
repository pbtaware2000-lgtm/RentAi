"""Backend-only SMTP configuration and plain-text notification delivery."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, parseaddr
import os
import re
import smtplib
import ssl

from dotenv import dotenv_values

from backend.core.config import PROJECT_ROOT


REQUIRED_EMAIL_SETTINGS = (
    "RENTAI_EMAIL_SMTP_HOST",
    "RENTAI_EMAIL_SMTP_PORT",
    "RENTAI_EMAIL_SMTP_USERNAME",
    "RENTAI_EMAIL_SMTP_PASSWORD",
    "RENTAI_EMAIL_FROM",
)


class EmailConfigurationError(RuntimeError):
    """Safe, actionable configuration error containing names only."""

    def __init__(self, missing: list[str] | None = None, message: str | None = None):
        self.missing = tuple(missing or ())
        if self.missing:
            detail = "Missing required email settings: " + ", ".join(self.missing)
        else:
            detail = message or "Email configuration is invalid. Check the documented RentAI email settings."
        super().__init__(detail)


@dataclass(frozen=True)
class SMTPSettings:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    from_address: str
    security: str = "starttls"


def get_smtp_settings() -> SMTPSettings:
    """Read backend environment or .env values without exporting credentials."""
    try:
        file_values = dotenv_values(PROJECT_ROOT / ".env")
    except (OSError, UnicodeError):
        file_values = {}
    values = {
        name: str(os.environ.get(name) or file_values.get(name) or "").strip()
        for name in REQUIRED_EMAIL_SETTINGS
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise EmailConfigurationError(missing)
    try:
        port = int(values["RENTAI_EMAIL_SMTP_PORT"])
    except ValueError:
        raise EmailConfigurationError(message="RENTAI_EMAIL_SMTP_PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise EmailConfigurationError(message="RENTAI_EMAIL_SMTP_PORT must be an integer from 1 to 65535.")
    security = str(
        os.environ.get("RENTAI_EMAIL_SMTP_SECURITY")
        or file_values.get("RENTAI_EMAIL_SMTP_SECURITY")
        or "starttls"
    ).strip().lower()
    if security not in {"starttls", "ssl"}:
        raise EmailConfigurationError(message="RENTAI_EMAIL_SMTP_SECURITY must be starttls or ssl.")
    if any(char.isspace() for char in values["RENTAI_EMAIL_SMTP_HOST"]):
        raise EmailConfigurationError(message="RENTAI_EMAIL_SMTP_HOST is invalid.")
    address = parseaddr(values["RENTAI_EMAIL_FROM"])[1]
    if not address or "@" not in address:
        raise EmailConfigurationError(message="RENTAI_EMAIL_FROM must be a valid email address.")
    return SMTPSettings(
        host=values["RENTAI_EMAIL_SMTP_HOST"], port=port,
        username=values["RENTAI_EMAIL_SMTP_USERNAME"],
        password=values["RENTAI_EMAIL_SMTP_PASSWORD"],
        from_address=address, security=security,
    )


class SMTPEmailTransport:
    """SMTP transport. Errors are intentionally sanitized at the call site."""

    def __init__(self, settings: SMTPSettings | None = None, *, timeout: float = 20):
        self.settings = settings or get_smtp_settings()
        self.timeout = timeout

    def send(self, recipient: str, subject: str, body: str, *, event_id: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.from_address
        message["To"] = recipient
        message["Subject"] = "".join(char if char not in "\r\n" else " " for char in subject)
        message["Date"] = formatdate(localtime=False, usegmt=True)
        stable_id = re.sub(r"[^A-Za-z0-9_.-]", "", event_id) or "event"
        message["Message-ID"] = f"<rentai.{stable_id}@{self.settings.from_address.split('@', 1)[1]}>"
        message.set_content(body)
        context = ssl.create_default_context()
        if self.settings.security == "ssl":
            with smtplib.SMTP_SSL(
                self.settings.host, self.settings.port, timeout=self.timeout, context=context,
            ) as client:
                client.login(self.settings.username, self.settings.password)
                refused = client.send_message(message)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
            return
        with smtplib.SMTP(self.settings.host, self.settings.port, timeout=self.timeout) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(self.settings.username, self.settings.password)
            refused = client.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
