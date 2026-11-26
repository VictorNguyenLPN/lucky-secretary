"""Database enums and record models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ConversationState(str, Enum):
    """Supported conversation states (FR-07)."""

    IDLE = "IDLE"
    COLLECTING_INFORMATION = "COLLECTING_INFORMATION"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RequestStatus(str, Enum):
    """Lifecycle states for a calendar request."""

    COLLECTING = "COLLECTING"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class UserRecord(BaseModel):
    """Row representation for the users table."""

    id: int
    telegram_user_id: str
    timezone: str
    created_at: datetime


class ConversationRecord(BaseModel):
    """Row representation for the conversations table."""

    id: int
    user_id: int
    state: ConversationState
    pending_request_id: str | None = None
    updated_at: datetime


class CalendarRequestRecord(BaseModel):
    """Row representation for the calendar_requests table."""

    id: str
    user_id: int
    status: RequestStatus
    event_payload: dict[str, Any]
    telegram_message_id: str | None = None
    google_event_id: str | None = None
    event_url: str | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class ChatMessageRecord(BaseModel):
    """Row representation for the chat_messages table (short-term history)."""

    id: int
    user_id: int
    role: str
    content: str
    referenced_events: list[dict[str, Any]] | None = None
    created_at: datetime

