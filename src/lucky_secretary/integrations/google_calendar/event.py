"""Mapping between CalendarEventRequest and Google Calendar API v3 event resource."""

from __future__ import annotations

from typing import Any

from lucky_secretary.schemas.calendar import CalendarEventRequest


def build_google_event_body(event: CalendarEventRequest) -> dict[str, Any]:
    """Convert a validated CalendarEventRequest into a Google Calendar events.insert body."""
    assert event.end_datetime is not None
    body: dict[str, Any] = {
        "summary": event.summary,
        "start": {
            "dateTime": event.start_datetime.isoformat(),
            "timeZone": event.timezone,
        },
        "end": {
            "dateTime": event.end_datetime.isoformat(),
            "timeZone": event.timezone,
        },
    }
    if event.location:
        body["location"] = event.location
    if event.description:
        body["description"] = event.description
    return body

