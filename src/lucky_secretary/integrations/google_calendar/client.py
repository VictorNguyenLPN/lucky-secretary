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
from lucky_secretary.schemas.calendar import (
    CalendarEventItem,
    CalendarEventRequest,
    GoogleCalendarEventResult,
)

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

        logger.info("Google Calendar event created: event_id=%s", created.get("id", ""))
        return _parse_event_result(created, event)

    async def update_event(
        self,
        event_id: str,
        event: CalendarEventRequest,
    ) -> GoogleCalendarEventResult:
        """Patch an existing event in the user's primary Google Calendar."""
        return await asyncio.to_thread(self._update_event_sync, event_id, event)

    def _update_event_sync(
        self,
        event_id: str,
        event: CalendarEventRequest,
    ) -> GoogleCalendarEventResult:
        try:
            creds = load_or_refresh_credentials(self._token_path)
        except GoogleAuthError as exc:
            raise GoogleCalendarError(str(exc)) from exc

        body = build_google_event_body(event)
        try:
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            updated: dict[str, Any] = (
                service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
            )
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", "unknown")
            logger.error("Google Calendar patch API HttpError status=%s", status)
            raise GoogleCalendarError(
                f"Google Calendar API returned an error (status {status})."
            ) from exc
        except Exception as exc:
            logger.error("Unexpected error updating Google Calendar event: %s", type(exc).__name__)
            raise GoogleCalendarError("Failed to update Google Calendar event.") from exc

        logger.info("Google Calendar event updated: event_id=%s", updated.get("id", event_id))
        return _parse_event_result(updated, event)

    async def delete_event(self, event_id: str) -> None:
        """Delete an event from the user's primary Google Calendar."""
        await asyncio.to_thread(self._delete_event_sync, event_id)

    def _delete_event_sync(self, event_id: str) -> None:
        try:
            creds = load_or_refresh_credentials(self._token_path)
        except GoogleAuthError as exc:
            raise GoogleCalendarError(str(exc)) from exc

        try:
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            service.events().delete(calendarId="primary", eventId=event_id).execute()
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", "unknown")
            logger.error("Google Calendar delete API HttpError status=%s", status)
            raise GoogleCalendarError(
                f"Google Calendar API returned an error (status {status})."
            ) from exc
        except Exception as exc:
            logger.error("Unexpected error deleting Google Calendar event: %s", type(exc).__name__)
            raise GoogleCalendarError("Failed to delete Google Calendar event.") from exc

        logger.info("Google Calendar event deleted: event_id=%s", event_id)

    async def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        max_results: int = 20,
    ) -> list[CalendarEventItem]:
        """List events from the user's primary Google Calendar within [time_min, time_max]."""
        return await asyncio.to_thread(self._list_events_sync, time_min, time_max, max_results)

    def _list_events_sync(
        self,
        time_min: datetime,
        time_max: datetime,
        max_results: int,
    ) -> list[CalendarEventItem]:
        try:
            creds = load_or_refresh_credentials(self._token_path)
        except GoogleAuthError as exc:
            raise GoogleCalendarError(str(exc)) from exc

        try:
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            response: dict[str, Any] = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=max_results,
                )
                .execute()
            )
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", "unknown")
            logger.error("Google Calendar list API HttpError status=%s", status)
            raise GoogleCalendarError(
                f"Google Calendar API returned an error (status {status})."
            ) from exc
        except Exception as exc:
            logger.error("Unexpected error listing Google Calendar events: %s", type(exc).__name__)
            raise GoogleCalendarError("Failed to fetch Google Calendar events.") from exc

        items: list[CalendarEventItem] = []
        for raw in response.get("items", []):
            start_info = raw.get("start", {})
            end_info = raw.get("end", {})
            start_dt_raw = start_info.get("dateTime")
            end_dt_raw = end_info.get("dateTime")
            all_day = start_info.get("date") if not start_dt_raw else None

            items.append(
                CalendarEventItem(
                    event_id=raw.get("id", ""),
                    summary=raw.get("summary") or "(No title)",
                    start_datetime=(
                        datetime.fromisoformat(start_dt_raw)
                        if isinstance(start_dt_raw, str)
                        else None
                    ),
                    end_datetime=(
                        datetime.fromisoformat(end_dt_raw)
                        if isinstance(end_dt_raw, str)
                        else None
                    ),
                    all_day_date=all_day,
                    location=raw.get("location"),
                    description=raw.get("description"),
                    html_link=raw.get("htmlLink"),
                )
            )
        return items


def _parse_event_result(
    payload: dict[str, Any],
    fallback_event: CalendarEventRequest,
) -> GoogleCalendarEventResult:
    start_raw = payload.get("start", {}).get("dateTime")
    end_raw = payload.get("end", {}).get("dateTime")
    start_dt = (
        datetime.fromisoformat(start_raw)
        if isinstance(start_raw, str)
        else fallback_event.start_datetime
    )
    assert fallback_event.end_datetime is not None
    end_dt = (
        datetime.fromisoformat(end_raw)
        if isinstance(end_raw, str)
        else fallback_event.end_datetime
    )
    return GoogleCalendarEventResult(
        event_id=payload.get("id", ""),
        summary=payload.get("summary", fallback_event.summary),
        start_datetime=start_dt,
        end_datetime=end_dt,
        timezone=fallback_event.timezone,
        html_link=payload.get("htmlLink", ""),
    )

