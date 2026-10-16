"""Command line entry point for RentAI's independent notification worker."""

import argparse
import logging
from email.utils import parseaddr

from backend.services.email_service import (
    EmailConfigurationError,
    SMTPEmailTransport,
    get_smtp_settings,
)
from backend.services.notification_worker import NotificationWorker, run_forever


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RentAI durable notification worker.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Enqueue due summaries and process due outbox events once.")
    mode.add_argument("--dry-run", action="store_true", help="Enqueue due summaries and report counts without sending email.")
    mode.add_argument("--test-email", metavar="RECIPIENT", help="Send a test message only to this explicitly supplied address.")
    args = parser.parse_args()

    if args.test_email:
        recipient = parseaddr(args.test_email)[1]
        if not recipient or recipient != args.test_email.strip() or "@" not in recipient:
            parser.error("--test-email requires one explicit email address")
        try:
            transport = SMTPEmailTransport(get_smtp_settings())
            transport.send(
                recipient,
                "RentAI SMTP test",
                "This is an explicitly requested RentAI email transport test. No owner or renter data was included.",
                event_id="manual-test",
            )
        except EmailConfigurationError as exc:
            logging.error("Email configuration unavailable; %s", str(exc))
            return 2
        except Exception as exc:
            logging.error("Test email failed; error_type=%s", type(exc).__name__)
            return 1
        logging.info("SMTP accepted the test message for the explicitly supplied recipient.")
        return 0

    worker = NotificationWorker()
    if args.dry_run:
        worker.run_once(dry_run=True)
    elif args.once:
        try:
            get_smtp_settings()
        except EmailConfigurationError as exc:
            logging.error("Email configuration unavailable; %s", str(exc))
        worker.run_once()
    else:
        run_forever()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
