"""Safely compare the configured Firebase Web API key with a local Console value."""

from getpass import getpass

from frontend.authentication import firebase_api_key_diagnostics


def main() -> int:
    expected_key = getpass("Paste the Firebase Web API key for project rentai-62ac9 (input hidden): ")
    result = firebase_api_key_diagnostics(expected_key)
    project_present = result["project_id_present"]
    project_matches = result["project_id_matches"]
    key_present = result["web_api_key_present"]
    key_matches = result["key_matches_console"]
    print(f"FIREBASE_PROJECT_ID: {'present' if project_present else 'missing'}; matches rentai-62ac9: {'yes' if project_matches else 'no' if project_matches is False else 'not checked'}")
    print(f"FIREBASE_WEB_API_KEY: {'present' if key_present else 'missing'}")
    print(f"Configured key matches Console key: {'yes' if key_matches else 'no' if key_matches is False else 'not checked (missing input or configuration)'}")
    print(f"Whitespace trimmed: {'yes' if result['whitespace_trimmed'] else 'no'}")
    return 0 if project_matches and key_matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
