"""Unit tests for conversation state transitions, expiration, and duplicate protection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from lucky_secretary.database.models import ConversationState, RequestStatus
from lucky_secretary.database.repository import Repository
from lucky_secretary.schemas.agent import EventExtractionResult, UserIntent
from tests.conftest import make_conversation_service


async def test_state_transition_clarification_to_preview_to_completed(
    repository: Repository,
) -> None:
    missing_time = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Họp với team",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=["time"],
        clarification_question="Bạn muốn họp vào lúc mấy giờ?",
    )
    complete = EventExtractionResult(
        intent=UserIntent.PROVIDE_CLARIFICATION,
        summary="Họp với team",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(repository, [missing_time, complete])
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    resp1 = await service.handle_user_message("1001", "Tạo lịch họp với team vào thứ 6.", now=now)
    assert "Bạn muốn họp vào lúc mấy giờ?" in resp1.text
    assert resp1.reply_markup_request_id is None

    user = await repository.get_or_create_user("1001")
    conv = await repository.get_conversation(user.id)
    assert conv.state == ConversationState.COLLECTING_INFORMATION
    assert conv.pending_request_id is not None

    resp2 = await service.handle_user_message("1001", "9 giờ sáng.", now=now + timedelta(minutes=1))
    assert "Calendar Event Preview" in resp2.text
    assert resp2.reply_markup_request_id == conv.pending_request_id
    assert len(cal_client.created_events) == 0

    conv = await repository.get_conversation(user.id)
    assert conv.state == ConversationState.WAITING_CONFIRMATION

    confirm_resp = await service.handle_confirmation(
        "1001",
        resp2.reply_markup_request_id,  # type: ignore[arg-type]
        confirmed=True,
        now=now + timedelta(minutes=2),
    )
    assert "Event created successfully!" in confirm_resp.text
    assert "https://calendar.google.com" in confirm_resp.text
    assert len(cal_client.created_events) == 1

    conv = await repository.get_conversation(user.id)
    assert conv.state == ConversationState.IDLE
    assert conv.pending_request_id is None


async def test_pending_confirmation_not_overwritten_by_new_message(
    repository: Repository,
) -> None:
    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Project Discussion",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, llm, _ = make_conversation_service(repository, [complete])
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message("1001", "Meeting Friday at 9 AM", now=now)
    assert preview.reply_markup_request_id is not None

    blocked = await service.handle_user_message(
        "1001",
        "Another meeting tomorrow at 3 PM",
        now=now + timedelta(minutes=2),
    )
    assert "pending event waiting for confirmation" in blocked.text
    # LLM should not even be invoked while waiting for confirmation on an active request.
    assert len(llm.calls) == 1


async def test_expired_request_cannot_be_confirmed(repository: Repository) -> None:
    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Expired Event",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(repository, [complete])
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message("1001", "Meeting Friday at 9 AM", now=now)
    req_id = preview.reply_markup_request_id
    assert req_id is not None

    expired_resp = await service.handle_confirmation(
        "1001",
        req_id,
        confirmed=True,
        now=now + timedelta(minutes=16),
    )
    assert "expired" in expired_resp.text.lower()
    assert len(cal_client.created_events) == 0

    req = await repository.get_calendar_request(req_id)
    assert req is not None
    assert req.status == RequestStatus.EXPIRED


async def test_duplicate_confirmation_prevention(repository: Repository) -> None:
    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Idempotent Event",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(repository, [complete])
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message("1001", "Meeting Friday at 9 AM", now=now)
    req_id = preview.reply_markup_request_id
    assert req_id is not None

    r1, r2 = await asyncio.gather(
        service.handle_confirmation("1001", req_id, confirmed=True, now=now + timedelta(seconds=5)),
        service.handle_confirmation("1001", req_id, confirmed=True, now=now + timedelta(seconds=5)),
    )
    texts = [r1.text, r2.text]
    assert sum("Event created successfully!" in t for t in texts) == 1
    assert sum("already been processed" in t for t in texts) == 1
    assert len(cal_client.created_events) == 1


async def test_cancel_button_and_cancel_command(repository: Repository) -> None:
    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Cancelled Event",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(repository, [complete, complete])
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message("1001", "Meeting Friday at 9 AM", now=now)
    req_id = preview.reply_markup_request_id
    assert req_id is not None

    cancel_resp = await service.handle_confirmation("1001", req_id, confirmed=False, now=now)
    assert "cancelled" in cancel_resp.text.lower()
    assert len(cal_client.created_events) == 0

    req = await repository.get_calendar_request(req_id)
    assert req is not None
    assert req.status == RequestStatus.CANCELLED

    # Confirming a cancelled request must fail safely.
    retry_confirm = await service.handle_confirmation("1001", req_id, confirmed=True, now=now)
    assert "cancelled" in retry_confirm.text.lower()
    assert len(cal_client.created_events) == 0

    # Create another pending request and cancel via /cancel.
    preview2 = await service.handle_user_message("1001", "Meeting Friday at 9 AM", now=now)
    assert preview2.reply_markup_request_id is not None
    cmd_resp = await service.handle_cancel_command("1001")
    assert "cancelled" in cmd_resp.text.lower()


async def test_unauthorized_user_cannot_confirm_another_users_request(
    repository: Repository,
) -> None:
    complete = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Private Meeting",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(repository, [complete])
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message("1001", "Meeting Friday at 9 AM", now=now)
    req_id = preview.reply_markup_request_id
    assert req_id is not None

    attack = await service.handle_confirmation("9999", req_id, confirmed=True, now=now)
    assert "not authorized" in attack.text.lower()
    assert len(cal_client.created_events) == 0

