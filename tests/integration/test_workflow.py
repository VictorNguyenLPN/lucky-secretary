"""Integration tests for persistence, Google Calendar client, OAuth, and Telegram handlers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from lucky_secretary.bot.handlers.callback import confirmation_callback_handler
from lucky_secretary.bot.handlers.command import help_command, start_command
from lucky_secretary.bot.handlers.message import text_message_handler
from lucky_secretary.core.config import Settings
from lucky_secretary.database.connection import DatabaseManager
from lucky_secretary.database.repository import Repository
from lucky_secretary.integrations.google_calendar.auth import load_or_refresh_credentials
from lucky_secretary.integrations.google_calendar.client import GoogleCalendarClient
from lucky_secretary.schemas.agent import EventExtractionResult, UserIntent
from lucky_secretary.schemas.calendar import CalendarEventRequest
from tests.conftest import make_conversation_service


async def test_pending_request_survives_restart(tmp_path: Path) -> None:
    db_path = str(tmp_path / "restart.db")
    db1 = DatabaseManager(db_path)
    await db1.init_db()
    repo1 = Repository(db1)

    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Phỏng vấn ứng viên",
        start_datetime_iso="2026-11-28T10:00:00+07:00",
        duration_minutes=60,
        location="Meeting Room A",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service1, _, _ = make_conversation_service(repo1, [complete])
    now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
    preview = await service1.handle_user_message(
        "555",
        "Đặt lịch phỏng vấn lúc 10h sáng ngày 28/11, kéo dài 1 tiếng.",
        now=now,
    )
    req_id = preview.reply_markup_request_id
    assert req_id is not None
    await db1.close()

    # Simulate fresh process startup with a new DatabaseManager and Repository.
    db2 = DatabaseManager(db_path)
    await db2.init_db()
    repo2 = Repository(db2)
    service2, _, cal_client2 = make_conversation_service(repo2)

    result = await service2.handle_confirmation(
        "555",
        req_id,
        confirmed=True,
        now=now + timedelta(minutes=3),
    )
    assert "Event created successfully!" in result.text
    assert len(cal_client2.created_events) == 1
    assert cal_client2.created_events[0].summary == "Phỏng vấn ứng viên"
    await db2.close()


async def test_google_calendar_client_builds_expected_payload(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    client = GoogleCalendarClient(token_path=token_path)
    event = CalendarEventRequest(
        summary="Project Discussion",
        start_datetime=datetime(2026, 11, 27, 9, 0),
        duration_minutes=60,
        timezone="Asia/Ho_Chi_Minh",
        location="Office",
        description="Team meeting",
    )

    mock_insert = MagicMock()
    mock_insert.execute.return_value = {
        "id": "g_evt_123",
        "summary": "Project Discussion",
        "htmlLink": "https://calendar.google.com/event?eid=g_evt_123",
        "start": {"dateTime": "2026-11-27T09:00:00+07:00"},
        "end": {"dateTime": "2026-11-27T10:00:00+07:00"},
    }
    mock_events = MagicMock()
    mock_events.insert.return_value = mock_insert
    mock_service = MagicMock()
    mock_service.events.return_value = mock_events

    with (
        patch(
            "lucky_secretary.integrations.google_calendar.client.load_or_refresh_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "lucky_secretary.integrations.google_calendar.client.build",
            return_value=mock_service,
        ),
    ):
        created = await client.create_event(event)

    assert created.event_id == "g_evt_123"
    assert created.html_link == "https://calendar.google.com/event?eid=g_evt_123"
    mock_events.insert.assert_called_once()
    call_kwargs = mock_events.insert.call_args.kwargs
    assert call_kwargs["calendarId"] == "primary"
    assert call_kwargs["body"]["summary"] == "Project Discussion"
    assert call_kwargs["body"]["start"] == {
        "dateTime": "2026-11-27T09:00:00+07:00",
        "timeZone": "Asia/Ho_Chi_Minh",
    }


def test_load_or_refresh_credentials_refreshes_expired_token(tmp_path: Path) -> None:
    token_path = tmp_path / "google_token.json"
    token_path.write_text("{}", encoding="utf-8")

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh-tok"
    mock_creds.to_json.return_value = json.dumps({"token": "new-access-token"})

    with patch(
        "lucky_secretary.integrations.google_calendar.auth.Credentials.from_authorized_user_file",
        return_value=mock_creds,
    ):
        creds = load_or_refresh_credentials(token_path)

    assert creds is mock_creds
    mock_creds.refresh.assert_called_once()
    assert json.loads(token_path.read_text(encoding="utf-8"))["token"] == "new-access-token"


async def test_telegram_handlers_authorization_and_flow(repository: Repository) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="123:ABC",
        telegram_allowed_user_ids="42",
        openai_api_key="sk-test",
    )
    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="English Meeting",
        start_datetime_iso="2026-11-26T14:00:00+07:00",
        duration_minutes=90,
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(repository, [complete])
    bot_data = {
        "settings": settings,
        "repository": repository,
        "conversation_service": service,
    }
    context = SimpleNamespace(bot_data=bot_data)

    # Unauthorized /start is rejected.
    unauth_msg = MagicMock()
    unauth_msg.reply_text = AsyncMock()
    unauth_update = SimpleNamespace(effective_user=SimpleNamespace(id=999), message=unauth_msg)
    await start_command(unauth_update, context)  # type: ignore[arg-type]
    unauth_msg.reply_text.assert_awaited_once_with("Unauthorized access.")

    # Authorized /help succeeds.
    help_msg = MagicMock()
    help_msg.reply_text = AsyncMock()
    help_update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=help_msg)
    await help_command(help_update, context)  # type: ignore[arg-type]
    assert "Lucky Secretary Help" in help_msg.reply_text.call_args.args[0]

    # Authorized text message produces preview with inline keyboard.
    msg = MagicMock()
    msg.text = "Schedule a meeting tomorrow at 2 PM for 90 minutes."
    msg.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    msg_update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=msg)
    await text_message_handler(msg_update, context)  # type: ignore[arg-type]

    reply_kwargs = msg.reply_text.call_args.kwargs
    keyboard = reply_kwargs["reply_markup"]
    confirm_data = keyboard.inline_keyboard[0][0].callback_data
    assert confirm_data.startswith("confirm:")

    # Authorized callback confirms and creates event.
    query = MagicMock()
    query.data = confirm_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    cb_update = SimpleNamespace(effective_user=SimpleNamespace(id=42), callback_query=query)
    await confirmation_callback_handler(cb_update, context)  # type: ignore[arg-type]

    assert len(cal_client.created_events) == 1
    assert "Event created successfully!" in query.edit_message_text.call_args.kwargs["text"]

