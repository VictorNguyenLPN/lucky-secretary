"""Async wrapper around Google Calendar API v3."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lucky_secretary.integrations.google_calendar.auth import (
    GoogleAuthError,
    load_or_refresh_credentials,
)
from lucky_secretary.integrations.google_calendar.event import build_google_event_body
from lucky_secretary.schemas.calendar import CalendarEventRequest, GoogleCalendarEventResult

logger = logging.getLogger(__name__)


class GoogleCalendarError(Exception):
    """Raised when Google Calendar API fails or credentials are invalid."""


class GoogleCalendarClient:
    """Creates Google Calendar events without blocking the asyncio event loop."""

    def __init__(self, token_path: Path) -> None:
        self._token_path = token_path

    async def create_event(self, event: CalendarEventRequest) -> GoogleCalendarEventResult:
        """Create an event in the user's primary Google Calendar."""
        return await asyncio.to_thread(self._create_event_sync, event)

    def _create_event_sync(self, event: CalendarEventRequest) -> GoogleCalendarEventResult:
        try:
            creds = load_or_refresh_credentials(self._token_path)
        except GoogleAuthError as exc:
            raise GoogleCalendarError(str(exc)) from exc

        body = build_google_event_body(event)
        try:
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            created: dict[str, Any] = (
                service.events().insert(calendarId="primary", body=body).execute()
            )
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", "unknown")
            logger.error("Google Calendar API HttpError status=%s", status)
            raise GoogleCalendarError(
                f"Google Calendar API returned an error (status {status})."
            ) from exc
        except Exception as exc:
            logger.error("Unexpected error calling Google Calendar API: %s", type(exc).__name__)
            raise GoogleCalendarError("Failed to create Google Calendar event.") from exc

        event_id = created.get("id", "")
        html_link = created.get("htmlLink", "")
        start_raw = created.get("start", {}).get("dateTime")
        end_raw = created.get("end", {}).get("dateTime")

        start_dt = (
            datetime.fromisoformat(start_raw) if isinstance(start_raw, str) else event.start_datetime
        )
        assert event.end_datetime is not None
        end_dt = (
            datetime.fromisoformat(end_raw) if isinstance(end_raw, str) else event.end_datetime
        )

        logger.info("Google Calendar event created: event_id=%s", event_id)
        return GoogleCalendarEventResult(
            event_id=event_id,
            summary=created.get("summary", event.summary),
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone=event.timezone,
            html_link=html_link,
        )

