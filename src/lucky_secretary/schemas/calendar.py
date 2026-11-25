"""Pydantic schemas for Google Calendar events."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from lucky_secretary.utils.datetime import (
    compute_end_datetime,
    ensure_timezone_aware,
    validate_timezone,
)


class CalendarEventRequest(BaseModel):
    """Validated calendar event payload ready for preview and Google Calendar creation."""

    summary: str
    start_datetime: datetime
    end_datetime: datetime | None = None
    duration_minutes: int | None = None
    timezone: str = "Asia/Ho_Chi_Minh"
    location: str | None = None
    description: str | None = None

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Event title (summary) must not be empty.")
        return cleaned

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        validate_timezone(value)
        return value.strip()

    @field_validator("location", "description", mode="before")
    @classmethod
    def _clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None

    @model_validator(mode="after")
    def _normalize_datetimes(self) -> CalendarEventRequest:
        self.start_datetime = ensure_timezone_aware(self.start_datetime, self.timezone)
        if self.end_datetime is not None:
            self.end_datetime = ensure_timezone_aware(self.end_datetime, self.timezone)

        resolved_end, resolved_duration = compute_end_datetime(
            start_dt=self.start_datetime,
            end_dt=self.end_datetime,
            duration_minutes=self.duration_minutes,
        )
        self.end_datetime = resolved_end
        self.duration_minutes = resolved_duration
        return self


class GoogleCalendarEventResult(BaseModel):
    """Result returned after creating an event via Google Calendar API."""

    event_id: str
    summary: str
    start_datetime: datetime
    end_datetime: datetime
    timezone: str
    html_link: str


class CalendarEventItem(BaseModel):
    """Summary item for an existing Google Calendar event returned by list_events."""

    event_id: str
    summary: str
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    all_day_date: str | None = None
    location: str | None = None
    description: str | None = None
    html_link: str | None = None

