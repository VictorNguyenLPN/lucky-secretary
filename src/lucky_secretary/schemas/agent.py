"""Pydantic schemas for LLM intent and structured event extraction."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lucky_secretary.schemas.calendar import CalendarEventRequest


class UserIntent(str, Enum):
    """High-level intent detected by the AI Core."""

    CREATE_EVENT = "create_event"
    PROVIDE_CLARIFICATION = "provide_clarification"
    CANCEL = "cancel"
    OTHER = "other"


class EventExtractionResult(BaseModel):
    """Structured output returned by the LLM provider."""

    intent: UserIntent
    summary: str | None = None
    start_datetime_iso: str | None = None
    end_datetime_iso: str | None = None
    duration_minutes: int | None = None
    timezone: str = "Asia/Ho_Chi_Minh"
    location: str | None = None
    description: str | None = None
    has_explicit_date: bool = False
    has_explicit_time: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None

    def is_complete(self) -> bool:
        """Return True when all required event fields are present and unambiguous."""
        return (
            self.intent in (UserIntent.CREATE_EVENT, UserIntent.PROVIDE_CLARIFICATION)
            and bool(self.summary and self.summary.strip())
            and self.has_explicit_date
            and self.has_explicit_time
            and bool(self.start_datetime_iso)
            and len(self.missing_fields) == 0
        )

    def to_partial_context(self) -> dict[str, object]:
        """Serialize extracted fields for multi-turn clarification state."""
        return self.model_dump(mode="json")

    def to_calendar_event_request(
        self,
        default_duration: int = 60,
        default_timezone: str = "Asia/Ho_Chi_Minh",
    ) -> CalendarEventRequest:
        """Convert complete extraction output into a validated CalendarEventRequest."""
        if not self.summary or not self.summary.strip():
            raise ValueError("Missing event summary.")
        if not self.start_datetime_iso:
            raise ValueError("Missing start_datetime_iso.")

        start_dt = datetime.fromisoformat(self.start_datetime_iso)
        end_dt = datetime.fromisoformat(self.end_datetime_iso) if self.end_datetime_iso else None
        duration = self.duration_minutes
        if end_dt is None and duration is None:
            duration = default_duration

        return CalendarEventRequest(
            summary=self.summary,
            start_datetime=start_dt,
            end_datetime=end_dt,
            duration_minutes=duration,
            timezone=self.timezone or default_timezone,
            location=self.location,
            description=self.description,
        )

