"""Service layer wrapping Google Calendar operations."""

from __future__ import annotations

from datetime import datetime

from lucky_secretary.integrations.google_calendar.client import GoogleCalendarClient
from lucky_secretary.schemas.calendar import (
    CalendarEventItem,
    CalendarEventRequest,
    GoogleCalendarEventResult,
)


class CalendarService:
    """Coordinates validated event operations through GoogleCalendarClient."""

    def __init__(self, client: GoogleCalendarClient) -> None:
        self._client = client

    async def create_calendar_event(
        self,
        event: CalendarEventRequest,
    ) -> GoogleCalendarEventResult:
        """Create an event in Google Calendar."""
        return await self._client.create_event(event)

    async def list_calendar_events(
        self,
        time_min: datetime,
        time_max: datetime,
    ) -> list[CalendarEventItem]:
        """List events in Google Calendar within the given datetime range."""
        return await self._client.list_events(time_min=time_min, time_max=time_max)

    async def update_calendar_event(
        self,
        event_id: str,
        event: CalendarEventRequest,
    ) -> GoogleCalendarEventResult:
        """Update an existing event in Google Calendar."""
        return await self._client.update_event(event_id=event_id, event=event)

    async def delete_calendar_event(self, event_id: str) -> None:
        """Delete an existing event from Google Calendar."""
        await self._client.delete_event(event_id=event_id)

