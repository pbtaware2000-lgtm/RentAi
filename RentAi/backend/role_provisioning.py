"""Local CLI for the existing authenticated role provisioning routes."""

from __future__ import annotations

import argparse
import json
import logging
from getpass import getpass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.core.auth import create_first_admin_account
from backend.core.config import SettingsError, get_api_base_url
from frontend.authentication import AuthenticationError, password_sign_in


logger = logging.getLogger(__name__)


def _call_provisioning_api(path: str, method: str, id_token: str, body: dict | None = None) -> int:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {id_token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(get_api_base_url() + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=12) as response:
            return response.status
    except HTTPError as exc:
        raise RuntimeError(f"Role provisioning was rejected (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError):
        logger.warning("role provisioning transport failed")
        raise RuntimeError("Could not reach the RentAI API.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Use RentAI's existing secure role endpoints.")
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("create-admin", help="Create the first Admin account directly in SQLite.")
    grant = commands.add_parser("grant", help="Grant a role using an existing Admin account.")
    grant.add_argument("uid", help="RentAI UID of the target account.")
    grant.add_argument("--role", choices=("owner", "renter", "broker", "admin"), required=True)
    args = parser.parse_args(argv)

    if args.operation == "create-admin":
        email = input("Admin email: ").strip()
        name = input("Admin name: ").strip()
        password = getpass("Choose Admin password (input hidden): ")
        confirmation = getpass("Confirm Admin password: ")
        if password != confirmation:
            print("Passwords do not match.")
            return 1
        try:
            uid = create_first_admin_account(email, password, name)
            print(f"Admin account created in SQLite. UID: {uid}")
            print("Sign in using the Admin role, this email, and the password you just set.")
            return 0
        except ValueError as exc:
            print(str(exc))
            return 1
        finally:
            password = ""
            confirmation = ""

    email = input("RentAI account email: ").strip()
    password = getpass("RentAI account password (input hidden): ")
    id_token = None
    try:
        tokens = password_sign_in(email, password)
        id_token = tokens["id_token"]
        path = f"/application/admin/users/{args.uid}/role"
        status = _call_provisioning_api(
            path, "PUT", id_token, {"role": args.role}
        )
        if status != 204:
            raise RuntimeError(f"Role assignment did not complete (HTTP {status}).")
        print("Role assignment succeeded. The target account must sign in again.")
        return 0
    except (AuthenticationError, SettingsError, RuntimeError) as exc:
        print(str(exc))
        return 1
    finally:
        password = ""
        id_token = None
        tokens = None


if __name__ == "__main__":
    raise SystemExit(main())
