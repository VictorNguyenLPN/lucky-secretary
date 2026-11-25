"""Pydantic schemas for LLM intent and structured event extraction."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field

from lucky_secretary.schemas.calendar import CalendarEventRequest
from lucky_secretary.utils.datetime import ensure_timezone_aware


class UserIntent(str, Enum):
    """High-level intent detected by the AI Core."""

    CREATE_EVENT = "create_event"
    PROVIDE_CLARIFICATION = "provide_clarification"
    LIST_EVENTS = "list_events"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    CANCEL = "cancel"
    OTHER = "other"


class EventExtractionResult(BaseModel):
    """Structured output returned by the LLM provider."""

    intent: UserIntent
    summary: str | None = None
    target_event_id: str | None = None
    target_event_ids: list[str] = Field(default_factory=list)
    delete_all_in_range: bool = False
    target_summary: str | None = None
    target_date_start_iso: str | None = None
    target_date_end_iso: str | None = None
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

    def resolved_target_event_ids(self) -> list[str]:
        """Return deduplicated non-empty target event IDs preserving order."""
        seen: set[str] = set()
        result: list[str] = []
        for item in [*self.target_event_ids, self.target_event_id or ""]:
            cleaned = item.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return result

    def is_complete(self) -> bool:
        """Return True when all required fields for the detected intent are present."""
        if self.missing_fields:
            return False

        if self.intent in (UserIntent.CREATE_EVENT, UserIntent.PROVIDE_CLARIFICATION):
            return (
                bool(self.summary and self.summary.strip())
                and self.has_explicit_date
                and self.has_explicit_time
                and bool(self.start_datetime_iso)
            )

        if self.intent == UserIntent.DELETE_EVENT:
            return bool(
                (self.delete_all_in_range and self.target_date_start_iso)
                or (self.target_summary and self.target_summary.strip())
                or (self.summary and self.summary.strip())
                or self.resolved_target_event_ids()
                or self.target_date_start_iso
                or self.start_datetime_iso
            )

        if self.intent == UserIntent.UPDATE_EVENT:
            return bool(
                (self.target_summary and self.target_summary.strip())
                or (self.summary and self.summary.strip())
                or self.target_event_id
            )

        return False

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

    def to_calendar_query_range(
        self,
        reference_time: datetime,
        default_timezone: str = "Asia/Ho_Chi_Minh",
    ) -> tuple[datetime, datetime]:
        """Resolve start and end bounds for a schedule listing query."""
        tz_name = self.timezone or default_timezone
        ref_local = ensure_timezone_aware(reference_time, tz_name)

        if self.start_datetime_iso:
            start_dt = ensure_timezone_aware(
                datetime.fromisoformat(self.start_datetime_iso),
                tz_name,
            )
        else:
            start_dt = ref_local.replace(hour=0, minute=0, second=0, microsecond=0)

        if self.end_datetime_iso:
            end_dt = ensure_timezone_aware(
                datetime.fromisoformat(self.end_datetime_iso),
                tz_name,
            )
        else:
            end_dt = start_dt.replace(hour=23, minute=59, second=59, microsecond=0)

        if end_dt <= start_dt:
            day_end = start_dt.replace(hour=23, minute=59, second=59, microsecond=0)
            end_dt = day_end if day_end > start_dt else start_dt + timedelta(days=1)

        return start_dt, end_dt

    def to_target_search_range(
        self,
        reference_time: datetime,
        default_timezone: str = "Asia/Ho_Chi_Minh",
    ) -> tuple[datetime, datetime]:
        """Resolve [search_min, search_max] for locating an existing event to update/delete."""
        tz_name = self.timezone or default_timezone
        ref_local = ensure_timezone_aware(reference_time, tz_name)

        if self.target_date_start_iso:
            start_dt = ensure_timezone_aware(
                datetime.fromisoformat(self.target_date_start_iso),
                tz_name,
            )
            if self.target_date_end_iso:
                end_dt = ensure_timezone_aware(
                    datetime.fromisoformat(self.target_date_end_iso),
                    tz_name,
                )
            else:
                end_dt = start_dt.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            start_dt = ref_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = (start_dt + timedelta(days=7)).replace(
                hour=23,
                minute=59,
                second=59,
                microsecond=0,
            )

        if end_dt <= start_dt:
            day_end = start_dt.replace(hour=23, minute=59, second=59, microsecond=0)
            end_dt = day_end if day_end > start_dt else start_dt + timedelta(days=1)

        return start_dt, end_dt

