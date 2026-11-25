"""Google OAuth 2.0 credential loading, refreshing, and initial setup flow."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleAuthError(Exception):
    """Raised when Google OAuth credentials are missing, invalid, or cannot be refreshed."""


def _save_credentials(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass


def load_or_refresh_credentials(
    token_path: Path,
    token_json: str = "",
) -> Credentials:
    """Load OAuth credentials from token_path (bootstrapping from token_json if provided)."""
    if not token_path.exists() and token_json.strip():
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token_json.strip(), encoding="utf-8")
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass
        logger.info("Bootstrapped Google OAuth token from GOOGLE_TOKEN_JSON.")

    if not token_path.exists():
        raise GoogleAuthError(
            "Google OAuth token not found. Run scripts/setup_google_oauth.py or set GOOGLE_TOKEN_JSON."
        )

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except (OSError, ValueError) as exc:
        raise GoogleAuthError("Google OAuth token file is invalid or unreadable.") from exc

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds, token_path)
            logger.info("Refreshed Google OAuth access token.")
            return creds
        except RefreshError as exc:
            logger.error("Failed to refresh Google OAuth token: %s", exc)
            raise GoogleAuthError(
                "Google OAuth refresh token has expired or been revoked. Re-run OAuth setup."
            ) from exc

    raise GoogleAuthError("Google OAuth credentials are invalid and cannot be refreshed.")


def run_local_oauth_flow(
    client_id: str,
    client_secret: str,
    token_path: Path,
    redirect_uri: str = "http://localhost:8080/",
) -> Credentials:
    """Run interactive local-server OAuth 2.0 flow and store resulting token JSON."""
    if not client_id or not client_secret:
        raise GoogleAuthError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required.")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    parsed_uri = urlparse(redirect_uri)
    port = parsed_uri.port or 8080

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=port, prompt="consent", access_type="offline")
    _save_credentials(creds, token_path)
    return creds

