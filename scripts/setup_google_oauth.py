"""CLI helper to perform initial Google OAuth 2.0 setup and save token JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucky_secretary.integrations.google_calendar.auth import (
    SCOPES,
    _save_credentials,
    run_local_oauth_flow,
)


class _GoogleOAuthEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8080/"
    google_token_path: Path = Path("./data/google_token.json")


def main() -> None:
    """Run OAuth 2.0 consent flow using .env variables or a client_secret.json file."""
    parser = argparse.ArgumentParser(description="Setup Google Calendar OAuth 2.0 credentials.")
    parser.add_argument(
        "--client-secret-file",
        type=Path,
        default=None,
        help="Optional path to Google Cloud OAuth client_secret_*.json file.",
    )
    args = parser.parse_args()

    env = _GoogleOAuthEnv()
    if args.client_secret_file is not None:
        raw = json.loads(args.client_secret_file.read_text(encoding="utf-8"))
        flow = InstalledAppFlow.from_client_config(raw, SCOPES)
        creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")
        _save_credentials(creds, env.google_token_path)
    else:
        run_local_oauth_flow(
            client_id=env.google_client_id,
            client_secret=env.google_client_secret,
            token_path=env.google_token_path,
            redirect_uri=env.google_redirect_uri,
        )

    print(f"Google OAuth token saved to: {env.google_token_path}")


if __name__ == "__main__":
    main()

