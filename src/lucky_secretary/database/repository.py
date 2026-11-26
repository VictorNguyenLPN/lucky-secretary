"""Async repository for users, conversations, and calendar requests."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from lucky_secretary.database.connection import DatabaseManager
from lucky_secretary.database.models import (
    CalendarRequestRecord,
    ChatMessageRecord,
    ConversationRecord,
    ConversationState,
    RequestStatus,
    UserRecord,
)
from lucky_secretary.utils.datetime import now_utc


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _from_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _row_to_user(row: aiosqlite.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        telegram_user_id=row["telegram_user_id"],
        timezone=row["timezone"],
        created_at=_from_iso(row["created_at"]),
    )


def _row_to_conversation(row: aiosqlite.Row) -> ConversationRecord:
    return ConversationRecord(
        id=row["id"],
        user_id=row["user_id"],
        state=ConversationState(row["state"]),
        pending_request_id=row["pending_request_id"],
        updated_at=_from_iso(row["updated_at"]),
    )


def _row_to_request(row: aiosqlite.Row) -> CalendarRequestRecord:
    return CalendarRequestRecord(
        id=row["id"],
        user_id=row["user_id"],
        status=RequestStatus(row["status"]),
        event_payload=json.loads(row["event_payload"]),
        telegram_message_id=row["telegram_message_id"],
        google_event_id=row["google_event_id"],
        event_url=row["event_url"],
        expires_at=_from_iso(row["expires_at"]),
        created_at=_from_iso(row["created_at"]),
        updated_at=_from_iso(row["updated_at"]),
    )


def _row_to_chat_message(row: aiosqlite.Row) -> ChatMessageRecord:
    raw_refs = row["referenced_events"]
    return ChatMessageRecord(
        id=row["id"],
        user_id=row["user_id"],
        role=row["role"],
        content=row["content"],
        referenced_events=json.loads(raw_refs) if raw_refs else None,
        created_at=_from_iso(row["created_at"]),
    )


class Repository:
    """Data access layer with atomic state transitions for idempotent confirmations."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def get_or_create_user(
        self,
        telegram_user_id: str,
        default_timezone: str = "Asia/Ho_Chi_Minh",
    ) -> UserRecord:
        """Fetch an existing user or create user + IDLE conversation atomically."""
        now_iso = _to_utc_iso(now_utc())
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                return _row_to_user(row)

            cursor = await conn.execute(
                """
                INSERT INTO users (telegram_user_id, timezone, created_at)
                VALUES (?, ?, ?)
                """,
                (telegram_user_id, default_timezone, now_iso),
            )
            user_id = cursor.lastrowid
            await conn.execute(
                """
                INSERT INTO conversations (user_id, state, pending_request_id, updated_at)
                VALUES (?, ?, NULL, ?)
                """,
                (user_id, ConversationState.IDLE.value, now_iso),
            )
            await conn.commit()

            cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            created = await cursor.fetchone()
            assert created is not None
            return _row_to_user(created)

    async def get_conversation(self, user_id: int) -> ConversationRecord:
        """Return the conversation record for user_id, creating one if missing."""
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM conversations WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                return _row_to_conversation(row)

            now_iso = _to_utc_iso(now_utc())
            await conn.execute(
                """
                INSERT INTO conversations (user_id, state, pending_request_id, updated_at)
                VALUES (?, ?, NULL, ?)
                """,
                (user_id, ConversationState.IDLE.value, now_iso),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT * FROM conversations WHERE user_id = ?",
                (user_id,),
            )
            created = await cursor.fetchone()
            assert created is not None
            return _row_to_conversation(created)

    async def update_conversation(
        self,
        user_id: int,
        state: ConversationState,
        pending_request_id: str | None,
    ) -> None:
        """Update conversation state and pending request pointer."""
        now_iso = _to_utc_iso(now_utc())
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE conversations
                SET state = ?, pending_request_id = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (state.value, pending_request_id, now_iso, user_id),
            )
            await conn.commit()

    async def create_calendar_request(
        self,
        user_id: int,
        status: RequestStatus,
        event_payload: dict[str, Any],
        expires_at: datetime,
        request_id: str | None = None,
    ) -> CalendarRequestRecord:
        """Insert a new calendar_requests row."""
        req_id = request_id or str(uuid.uuid4())
        now_iso = _to_utc_iso(now_utc())
        expires_iso = _to_utc_iso(expires_at)
        payload_json = json.dumps(event_payload, ensure_ascii=False)

        async with self.db.connect() as conn:
            await conn.execute(
                """
                INSERT INTO calendar_requests (
                    id, user_id, status, event_payload,
                    telegram_message_id, google_event_id, event_url,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                """,
                (req_id, user_id, status.value, payload_json, expires_iso, now_iso, now_iso),
            )
            await conn.commit()
            cursor = await conn.execute("SELECT * FROM calendar_requests WHERE id = ?", (req_id,))
            row = await cursor.fetchone()
            assert row is not None
            return _row_to_request(row)

    async def get_calendar_request(self, request_id: str) -> CalendarRequestRecord | None:
        """Fetch a calendar request by UUID string."""
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM calendar_requests WHERE id = ?",
                (request_id,),
            )
            row = await cursor.fetchone()
            return _row_to_request(row) if row is not None else None

    async def update_calendar_request(
        self,
        request_id: str,
        *,
        status: RequestStatus | None = None,
        event_payload: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
        telegram_message_id: str | None = None,
        google_event_id: str | None = None,
        event_url: str | None = None,
    ) -> CalendarRequestRecord:
        """Update selected columns on a calendar_requests row."""
        updates: list[str] = []
        params: list[Any] = []

        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if event_payload is not None:
            updates.append("event_payload = ?")
            params.append(json.dumps(event_payload, ensure_ascii=False))
        if expires_at is not None:
            updates.append("expires_at = ?")
            params.append(_to_utc_iso(expires_at))
        if telegram_message_id is not None:
            updates.append("telegram_message_id = ?")
            params.append(telegram_message_id)
        if google_event_id is not None:
            updates.append("google_event_id = ?")
            params.append(google_event_id)
        if event_url is not None:
            updates.append("event_url = ?")
            params.append(event_url)

        updates.append("updated_at = ?")
        params.append(_to_utc_iso(now_utc()))
        params.append(request_id)

        async with self.db.connect() as conn:
            await conn.execute(
                f"UPDATE calendar_requests SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT * FROM calendar_requests WHERE id = ?",
                (request_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"Calendar request not found: {request_id}")
            return _row_to_request(row)

    async def try_acquire_request_for_processing(
        self,
        request_id: str,
        user_id: int,
        now: datetime,
    ) -> tuple[bool, str, CalendarRequestRecord | None]:
        """Atomically transition PENDING_CONFIRMATION -> PROCESSING to block duplicate clicks."""
        now_iso = _to_utc_iso(now)
        async with self.db.connect() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            cursor = await conn.execute(
                """
                UPDATE calendar_requests
                SET status = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND status = ? AND expires_at > ?
                """,
                (
                    RequestStatus.PROCESSING.value,
                    now_iso,
                    request_id,
                    user_id,
                    RequestStatus.PENDING_CONFIRMATION.value,
                    now_iso,
                ),
            )
            if cursor.rowcount == 1:
                await conn.execute(
                    """
                    UPDATE conversations
                    SET state = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (ConversationState.PROCESSING.value, now_iso, user_id),
                )
                await conn.commit()
                fetched = await conn.execute(
                    "SELECT * FROM calendar_requests WHERE id = ?",
                    (request_id,),
                )
                row = await fetched.fetchone()
                assert row is not None
                return True, "acquired", _row_to_request(row)

            # Inspect why acquisition failed so the caller can provide an accurate message.
            fetched = await conn.execute(
                "SELECT * FROM calendar_requests WHERE id = ?",
                (request_id,),
            )
            row = await fetched.fetchone()
            if row is None:
                await conn.rollback()
                return False, "not_found", None

            record = _row_to_request(row)
            if record.user_id != user_id:
                await conn.rollback()
                return False, "unauthorized", record

            if (
                record.status == RequestStatus.PENDING_CONFIRMATION
                and _to_utc_iso(record.expires_at) <= now_iso
            ):
                await conn.execute(
                    "UPDATE calendar_requests SET status = ?, updated_at = ? WHERE id = ?",
                    (RequestStatus.EXPIRED.value, now_iso, request_id),
                )
                await conn.execute(
                    """
                    UPDATE conversations
                    SET state = ?, pending_request_id = NULL, updated_at = ?
                    WHERE user_id = ? AND pending_request_id = ?
                    """,
                    (ConversationState.IDLE.value, now_iso, user_id, request_id),
                )
                await conn.commit()
                record.status = RequestStatus.EXPIRED
                return False, "expired", record

            await conn.rollback()
            if record.status == RequestStatus.EXPIRED:
                return False, "expired", record
            if record.status == RequestStatus.CANCELLED:
                return False, "cancelled", record
            return False, "already_processed", record

    async def mark_request_completed(
        self,
        request_id: str,
        user_id: int,
        google_event_id: str,
        event_url: str,
    ) -> None:
        """Mark request as COMPLETED and reset conversation to IDLE."""
        now_iso = _to_utc_iso(now_utc())
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE calendar_requests
                SET status = ?, google_event_id = ?, event_url = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (RequestStatus.COMPLETED.value, google_event_id, event_url, now_iso, request_id, user_id),
            )
            await conn.execute(
                """
                UPDATE conversations
                SET state = ?, pending_request_id = NULL, updated_at = ?
                WHERE user_id = ?
                """,
                (ConversationState.IDLE.value, now_iso, user_id),
            )
            await conn.commit()

    async def mark_request_cancelled(self, request_id: str, user_id: int) -> None:
        """Mark request as CANCELLED and reset conversation to IDLE."""
        now_iso = _to_utc_iso(now_utc())
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE calendar_requests
                SET status = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (RequestStatus.CANCELLED.value, now_iso, request_id, user_id),
            )
            await conn.execute(
                """
                UPDATE conversations
                SET state = ?, pending_request_id = NULL, updated_at = ?
                WHERE user_id = ?
                """,
                (ConversationState.IDLE.value, now_iso, user_id),
            )
            await conn.commit()

    async def mark_request_failed(self, request_id: str, user_id: int) -> None:
        """Mark request as FAILED and reset conversation to IDLE."""
        now_iso = _to_utc_iso(now_utc())
        async with self.db.connect() as conn:
            await conn.execute(
                """
                UPDATE calendar_requests
                SET status = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (RequestStatus.FAILED.value, now_iso, request_id, user_id),
            )
            await conn.execute(
                """
                UPDATE conversations
                SET state = ?, pending_request_id = NULL, updated_at = ?
                WHERE user_id = ?
                """,
                (ConversationState.IDLE.value, now_iso, user_id),
            )
            await conn.commit()

    async def add_chat_message(
        self,
        user_id: int,
        role: str,
        content: str,
        referenced_events: list[dict[str, Any]] | None = None,
        created_at: datetime | None = None,
        max_stored_per_user: int = 20,
    ) -> ChatMessageRecord:
        """Insert a short-term chat message and prune older rows beyond max_stored_per_user."""
        ts_iso = _to_utc_iso(created_at or now_utc())
        refs_json = json.dumps(referenced_events, ensure_ascii=False) if referenced_events else None

        async with self.db.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO chat_messages (user_id, role, content, referenced_events, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, role, content, refs_json, ts_iso),
            )
            msg_id = cursor.lastrowid
            if max_stored_per_user > 0:
                await conn.execute(
                    """
                    DELETE FROM chat_messages
                    WHERE user_id = ? AND id NOT IN (
                        SELECT id FROM chat_messages
                        WHERE user_id = ?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                    )
                    """,
                    (user_id, user_id, max_stored_per_user),
                )
            await conn.commit()
            fetched = await conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,))
            row = await fetched.fetchone()
            assert row is not None
            return _row_to_chat_message(row)

    async def get_recent_chat_messages(
        self,
        user_id: int,
        since: datetime,
        limit: int = 6,
    ) -> list[ChatMessageRecord]:
        """Fetch up to limit newest messages with created_at >= since in chronological order."""
        if limit <= 0:
            return []
        since_iso = _to_utc_iso(since)
        async with self.db.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE user_id = ? AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, since_iso, limit),
            )
            rows = await cursor.fetchall()
        return [_row_to_chat_message(row) for row in reversed(rows)]

    async def clear_chat_messages(self, user_id: int) -> None:
        """Delete all short-term chat history rows for user_id."""
        async with self.db.connect() as conn:
            await conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
            await conn.commit()

