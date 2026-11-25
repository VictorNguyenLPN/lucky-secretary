"""Unit tests for Pydantic schemas, datetime utilities, and Settings validation."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from lucky_secretary.core.config import Settings
from lucky_secretary.schemas.agent import EventExtractionResult, UserIntent
from lucky_secretary.schemas.calendar import CalendarEventRequest
from lucky_secretary.utils.datetime import (
    compute_end_datetime,
    ensure_timezone_aware,
    format_event_preview_datetime,
    validate_timezone,
)


def test_validate_timezone_valid_and_invalid() -> None:
    tz = validate_timezone("Asia/Ho_Chi_Minh")
    assert isinstance(tz, ZoneInfo)
    with pytest.raises(ValueError, match="Invalid timezone"):
        validate_timezone("Invalid/Zone")
    with pytest.raises(ValueError, match="empty"):
        validate_timezone("   ")


def test_calendar_event_request_defaults_to_60_minutes_and_aware_tz() -> None:
    req = CalendarEventRequest(
        summary="  Team Sync  ",
        start_datetime=datetime(2026, 11, 27, 9, 0),
    )
    assert req.summary == "Team Sync"
    assert req.timezone == "Asia/Ho_Chi_Minh"
    assert req.start_datetime.tzinfo is not None
    assert req.end_datetime == datetime(2026, 11, 27, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert req.duration_minutes == 60


def test_calendar_event_request_converts_utc_to_target_timezone() -> None:
    utc_start = datetime(2026, 11, 27, 2, 0, tzinfo=timezone.utc)
    req = CalendarEventRequest(
        summary="Morning Meeting",
        start_datetime=utc_start,
        duration_minutes=90,
        timezone="Asia/Ho_Chi_Minh",
    )
    assert req.start_datetime.hour == 9
    assert req.end_datetime is not None
    assert req.end_datetime.hour == 10
    assert req.end_datetime.minute == 30
    assert req.duration_minutes == 90


def test_calendar_event_request_rejects_invalid_fields() -> None:
    with pytest.raises(ValidationError):
        CalendarEventRequest(summary="   ", start_datetime=datetime(2026, 11, 27, 9, 0))

    with pytest.raises(ValidationError):
        CalendarEventRequest(
            summary="Bad End",
            start_datetime=datetime(2026, 11, 27, 10, 0),
            end_datetime=datetime(2026, 11, 27, 9, 0),
        )

    with pytest.raises(ValidationError):
        CalendarEventRequest(
            summary="Bad Duration",
            start_datetime=datetime(2026, 11, 27, 10, 0),
            duration_minutes=0,
        )

    with pytest.raises(ValidationError):
        CalendarEventRequest(
            summary="Bad TZ",
            start_datetime=datetime(2026, 11, 27, 10, 0),
            timezone="Mars/Base",
        )


def test_compute_end_datetime_with_explicit_end() -> None:
    start = ensure_timezone_aware(datetime(2026, 11, 27, 14, 0), "Asia/Ho_Chi_Minh")
    end = ensure_timezone_aware(datetime(2026, 11, 27, 15, 30), "Asia/Ho_Chi_Minh")
    resolved_end, minutes = compute_end_datetime(start, end, None)
    assert resolved_end == end
    assert minutes == 90


def test_format_event_preview_datetime() -> None:
    start = ensure_timezone_aware(datetime(2026, 11, 27, 9, 0), "Asia/Ho_Chi_Minh")
    end = ensure_timezone_aware(datetime(2026, 11, 27, 10, 0), "Asia/Ho_Chi_Minh")
    date_str, time_range = format_event_preview_datetime(start, end)
    assert date_str == "Friday, November 27, 2026"
    assert time_range == "09:00 - 10:00"


def test_extraction_result_completeness_and_conversion() -> None:
    incomplete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Họp team",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=["time"],
        clarification_question="Bạn muốn họp vào lúc mấy giờ?",
    )
    assert not incomplete.is_complete()

    complete = EventExtractionResult(
        intent=UserIntent.PROVIDE_CLARIFICATION,
        summary="Họp team",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    assert complete.is_complete()
    event = complete.to_calendar_event_request(default_duration=45)
    assert event.duration_minutes == 45
    assert event.summary == "Họp team"


def test_settings_validation_and_sqlite_path() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="123:ABC",
        telegram_allowed_user_ids="111, 222",
        openai_api_key="sk-test",
        openai_base_url="   ",
        database_url="sqlite+aiosqlite:///./data/test.db",
    )
    assert settings.allowed_user_ids == {111, 222}
    assert settings.openai_base_url is None
    assert settings.sqlite_path == "./data/test.db"

