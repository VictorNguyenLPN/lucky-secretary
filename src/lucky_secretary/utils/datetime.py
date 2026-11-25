"""Timezone and datetime normalization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(tz_name: str) -> ZoneInfo:
    """Validate an IANA timezone name and return its ZoneInfo instance."""
    if not tz_name or not tz_name.strip():
        raise ValueError("Timezone must not be empty.")
    try:
        return ZoneInfo(tz_name.strip())
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid timezone: {tz_name}") from exc


def now_utc() -> datetime:
    """Return the current UTC timezone-aware datetime."""
    return datetime.now(timezone.utc)


def now_in_timezone(tz_name: str = "Asia/Ho_Chi_Minh") -> datetime:
    """Return the current timezone-aware datetime in the given IANA timezone."""
    tz = validate_timezone(tz_name)
    return datetime.now(tz)


def ensure_timezone_aware(dt: datetime, tz_name: str) -> datetime:
    """Ensure a datetime is timezone-aware in the specified IANA timezone."""
    tz = validate_timezone(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def compute_end_datetime(
    start_dt: datetime,
    end_dt: datetime | None,
    duration_minutes: int | None,
    default_duration_minutes: int = 60,
) -> tuple[datetime, int]:
    """Resolve and validate event end_datetime and duration_minutes."""
    if duration_minutes is not None and duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive.")

    if end_dt is not None:
        if end_dt <= start_dt:
            raise ValueError("end_datetime must be later than start_datetime.")
        resolved_minutes = max(1, round((end_dt - start_dt).total_seconds() / 60))
        return end_dt, resolved_minutes

    minutes = duration_minutes if duration_minutes is not None else default_duration_minutes
    if minutes <= 0:
        raise ValueError("Default duration_minutes must be positive.")
    return start_dt + timedelta(minutes=minutes), minutes


def format_event_preview_datetime(start_dt: datetime, end_dt: datetime) -> tuple[str, str]:
    """Format start and end datetimes for the Telegram event preview."""
    date_str = start_dt.strftime("%A, %B %d, %Y")
    if end_dt.date() == start_dt.date():
        time_range_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
    else:
        time_range_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%Y-%m-%d %H:%M')}"
    return date_str, time_range_str

