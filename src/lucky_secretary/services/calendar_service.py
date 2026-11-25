"""Service layer wrapping Google Calendar operations."""

from __future__ import annotations

from lucky_secretary.integrations.google_calendar.client import GoogleCalendarClient
from lucky_secretary.schemas.calendar import CalendarEventRequest, GoogleCalendarEventResult


class CalendarService:
    """Coordinates validated event creation through GoogleCalendarClient."""

    def __init__(self, client: GoogleCalendarClient) -> None:
        self._client = client

    async def create_calendar_event(
        self,
        event: CalendarEventRequest,
    ) -> GoogleCalendarEventResult:
        """Create an event in Google Calendar."""
        return await self._client.create_event(event)

