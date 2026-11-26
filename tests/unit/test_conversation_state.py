"""Unit tests for conversation state transitions, expiration, and duplicate protection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from lucky_secretary.database.models import ConversationState, RequestStatus
from lucky_secretary.database.repository import Repository
from lucky_secretary.integrations.web_search.client import WebSearchResult
from lucky_secretary.schemas.agent import EventExtractionResult, ExtractedEventItem, UserIntent
from lucky_secretary.schemas.calendar import CalendarEventItem
from tests.conftest import FakeWebSearchClient, make_conversation_service


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


async def test_list_events_with_events_and_empty_schedule(repository: Repository) -> None:
    query_tomorrow = EventExtractionResult(
        intent=UserIntent.LIST_EVENTS,
        start_datetime_iso="2026-11-26T00:00:00+07:00",
        end_datetime_iso="2026-11-26T23:59:59+07:00",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=[],
    )
    events = [
        CalendarEventItem(
            event_id="e1",
            summary="Team Standup",
            start_datetime=datetime.fromisoformat("2026-11-26T09:00:00+07:00"),
            end_datetime=datetime.fromisoformat("2026-11-26T09:30:00+07:00"),
            location="Room 201",
        ),
        CalendarEventItem(
            event_id="e2",
            summary="Company Holiday",
            all_day_date="2026-11-26",
        ),
    ]
    service, _, cal_client = make_conversation_service(
        repository,
        [query_tomorrow],
        listed_events=events,
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    resp = await service.handle_user_message(
        "1001",
        "Ngày mai tôi có những lịch nào?",
        now=now,
    )
    assert resp.reply_markup_request_id is None
    assert "Schedule for Thursday, November 26, 2026" in resp.text
    assert "09:00 - 09:30" in resp.text
    assert "Team Standup" in resp.text
    assert "Room 201" in resp.text
    assert "All day" in resp.text
    assert "Company Holiday" in resp.text
    assert len(cal_client.list_queries) == 1

    user = await repository.get_or_create_user("1001")
    conv = await repository.get_conversation(user.id)
    assert conv.state == ConversationState.IDLE

    # Empty schedule case.
    empty_service, _, _ = make_conversation_service(
        repository,
        [query_tomorrow],
        listed_events=[],
    )
    empty_resp = await empty_service.handle_user_message(
        "1001",
        "Ngày mai tôi có những lịch nào?",
        now=now,
    )
    assert "Schedule for Thursday, November 26, 2026" in empty_resp.text
    assert "No events scheduled." in empty_resp.text


async def test_update_event_preview_and_confirmation(repository: Repository) -> None:
    existing = CalendarEventItem(
        event_id="evt_badminton",
        summary="Ăn tối với Ronaldo",
        start_datetime=datetime.fromisoformat("2026-11-25T20:00:00+07:00"),
        end_datetime=datetime.fromisoformat("2026-11-25T21:30:00+07:00"),
        location="Nhà hàng ABC",
    )
    pass1_extraction = EventExtractionResult(
        intent=UserIntent.UPDATE_EVENT,
        target_summary="ăn tối cùng ronaldo",
        target_date_start_iso="2026-11-25T00:00:00+07:00",
        target_date_end_iso="2026-11-25T23:59:59+07:00",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=[],
    )
    pass2_resolution = EventExtractionResult(
        intent=UserIntent.UPDATE_EVENT,
        target_event_id="evt_badminton",
        target_summary="ăn tối cùng ronaldo",
        summary="Ăn tối với Ronaldo",
        start_datetime_iso="2026-11-26T20:00:00+07:00",
        end_datetime_iso="2026-11-26T21:30:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, llm, cal_client = make_conversation_service(
        repository,
        [pass1_extraction, pass2_resolution],
        listed_events=[existing],
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message(
        "1001",
        "đổi lịch ăn tối cùng ronaldo tối nay sang ngày mai, cùng giờ",
        now=now,
    )
    assert len(llm.calls) == 2
    assert "Candidate events from user's Google Calendar" in llm.calls[1][1]
    assert "Update Event Preview" in preview.text
    assert "Ăn tối với Ronaldo" in preview.text
    assert "20:00 - 21:30" in preview.text
    assert "Nhà hàng ABC" in preview.text
    assert preview.reply_markup_request_id is not None
    assert len(cal_client.updated_events) == 0

    confirm_resp = await service.handle_confirmation(
        "1001",
        preview.reply_markup_request_id,
        confirmed=True,
        now=now + timedelta(seconds=10),
    )
    assert "Event updated successfully!" in confirm_resp.text
    assert len(cal_client.updated_events) == 1
    assert cal_client.updated_events[0][0] == "evt_badminton"
    assert cal_client.updated_events[0][1].duration_minutes == 90


async def test_delete_event_preview_and_confirmation(repository: Repository) -> None:
    existing = CalendarEventItem(
        event_id="evt_del",
        summary="Chơi cầu lông",
        start_datetime=datetime.fromisoformat("2026-11-26T18:00:00+07:00"),
        end_datetime=datetime.fromisoformat("2026-11-26T20:00:00+07:00"),
    )
    extraction = EventExtractionResult(
        intent=UserIntent.DELETE_EVENT,
        target_event_id="evt_del",
        target_summary="cầu lông",
        target_date_start_iso="2026-11-26T00:00:00+07:00",
        target_date_end_iso="2026-11-26T23:59:59+07:00",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(
        repository,
        [extraction],
        listed_events=[existing],
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message(
        "1001",
        "Xóa lịch chơi cầu lông ngày mai.",
        now=now,
    )
    assert "Delete Event Preview" in preview.text
    assert "Chơi cầu lông" in preview.text
    assert preview.reply_markup_request_id is not None
    assert len(cal_client.deleted_event_ids) == 0

    confirm_resp = await service.handle_confirmation(
        "1001",
        preview.reply_markup_request_id,
        confirmed=True,
        now=now + timedelta(seconds=10),
    )
    assert "Event deleted successfully!" in confirm_resp.text
    assert cal_client.deleted_event_ids == ["evt_del"]


async def test_unsupported_query_and_event_not_found_notices(repository: Repository) -> None:
    unsupported = EventExtractionResult(
        intent=UserIntent.OTHER,
        missing_fields=[],
    )
    not_found_delete = EventExtractionResult(
        intent=UserIntent.DELETE_EVENT,
        target_summary="không tồn tại",
        target_date_start_iso="2026-11-26T00:00:00+07:00",
        target_date_end_iso="2026-11-26T23:59:59+07:00",
        has_explicit_date=True,
        missing_fields=[],
    )
    service, _, _ = make_conversation_service(
        repository,
        [unsupported, not_found_delete],
        listed_events=[],
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    resp_other = await service.handle_user_message("1001", "gửi email giúp tôi", now=now)
    assert "<b>Unsupported Request</b>" in resp_other.text
    assert "<b>Supported capabilities</b>" in resp_other.text

    resp_missing = await service.handle_user_message(
        "1001",
        "xóa lịch không tồn tại ngày mai",
        now=now + timedelta(seconds=5),
    )
    assert "<b>Event Not Found</b>" in resp_missing.text


async def test_batch_delete_all_events_in_week(repository: Repository) -> None:
    weekly_events = [
        CalendarEventItem(
            event_id="w1",
            summary="Họp đầu tuần",
            start_datetime=datetime.fromisoformat("2026-11-23T09:00:00+07:00"),
            end_datetime=datetime.fromisoformat("2026-11-23T10:00:00+07:00"),
        ),
        CalendarEventItem(
            event_id="w2",
            summary="Chơi cầu lông",
            start_datetime=datetime.fromisoformat("2026-11-26T18:00:00+07:00"),
            end_datetime=datetime.fromisoformat("2026-11-26T20:00:00+07:00"),
        ),
    ]
    extraction = EventExtractionResult(
        intent=UserIntent.DELETE_EVENT,
        delete_all_in_range=True,
        target_date_start_iso="2026-11-23T00:00:00+07:00",
        target_date_end_iso="2026-11-29T23:59:59+07:00",
        has_explicit_date=True,
        missing_fields=[],
    )
    service, llm, cal_client = make_conversation_service(
        repository,
        [extraction],
        listed_events=weekly_events,
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message(
        "1001",
        "xóa toàn bộ lịch trong tuần",
        now=now,
    )
    assert len(llm.calls) == 1
    assert "<b>Delete Events Preview (2 events)</b>" in preview.text
    assert "Họp đầu tuần" in preview.text
    assert "Chơi cầu lông" in preview.text
    assert preview.reply_markup_request_id is not None
    assert cal_client.deleted_event_ids == []

    confirm_resp = await service.handle_confirmation(
        "1001",
        preview.reply_markup_request_id,
        confirmed=True,
        now=now + timedelta(seconds=10),
    )
    assert "<b>Events deleted successfully! (2 events)</b>" in confirm_resp.text
    assert cal_client.deleted_event_ids == ["w1", "w2"]


async def test_short_term_history_resolves_followup_after_list_events(
    repository: Repository,
) -> None:
    events = [
        CalendarEventItem(
            event_id="e1",
            summary="Họp team",
            start_datetime=datetime.fromisoformat("2026-11-26T09:00:00+07:00"),
            end_datetime=datetime.fromisoformat("2026-11-26T10:00:00+07:00"),
        ),
        CalendarEventItem(
            event_id="e2",
            summary="Chơi cầu lông",
            start_datetime=datetime.fromisoformat("2026-11-26T18:00:00+07:00"),
            end_datetime=datetime.fromisoformat("2026-11-26T20:00:00+07:00"),
        ),
    ]
    list_extraction = EventExtractionResult(
        intent=UserIntent.LIST_EVENTS,
        start_datetime_iso="2026-11-26T00:00:00+07:00",
        end_datetime_iso="2026-11-26T23:59:59+07:00",
        has_explicit_date=True,
        missing_fields=[],
    )
    followup_update = EventExtractionResult(
        intent=UserIntent.UPDATE_EVENT,
        target_event_id="e2",
        summary="Chơi cầu lông",
        start_datetime_iso="2026-11-26T19:00:00+07:00",
        end_datetime_iso="2026-11-26T21:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, llm, cal_client = make_conversation_service(
        repository,
        [list_extraction, followup_update],
        listed_events=events,
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    list_resp = await service.handle_user_message("1001", "Ngày mai tôi có lịch gì?", now=now)
    assert "Chơi cầu lông" in list_resp.text
    assert len(cal_client.list_queries) == 1

    followup_resp = await service.handle_user_message(
        "1001",
        "dời cái thứ 2 sang 19h",
        now=now + timedelta(minutes=2),
    )
    assert "Recent conversation history" in llm.calls[1][1]
    assert "e2" in llm.calls[1][1]
    # Resolved directly from short-term history without re-querying Google Calendar.
    assert len(cal_client.list_queries) == 1
    assert "Update Event Preview" in followup_resp.text
    assert "19:00 - 21:00" in followup_resp.text


async def test_short_term_history_resolves_followup_after_confirmed_creation(
    repository: Repository,
) -> None:
    create_extraction = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Họp team",
        start_datetime_iso="2026-11-27T09:00:00+07:00",
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    followup_update = EventExtractionResult(
        intent=UserIntent.UPDATE_EVENT,
        target_event_id="evt_1",
        summary="Họp team",
        location="Phòng 302",
        has_explicit_date=True,
        missing_fields=[],
    )
    service, llm, cal_client = make_conversation_service(
        repository,
        [create_extraction, followup_update],
    )
    now = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    preview = await service.handle_user_message("1001", "Họp team 9h ngày 27/11", now=now)
    assert preview.reply_markup_request_id is not None
    await service.handle_confirmation(
        "1001",
        preview.reply_markup_request_id,
        confirmed=True,
        now=now + timedelta(seconds=10),
    )

    update_preview = await service.handle_user_message(
        "1001",
        "thêm địa điểm Phòng 302 cho lịch vừa tạo",
        now=now + timedelta(minutes=1),
    )
    assert "evt_1" in llm.calls[1][1]
    assert "Update Event Preview" in update_preview.text
    assert "Phòng 302" in update_preview.text
    assert len(cal_client.list_queries) == 0


async def test_short_term_history_ttl_expiration_and_clear_command(
    repository: Repository,
) -> None:
    list_extraction = EventExtractionResult(
        intent=UserIntent.LIST_EVENTS,
        start_datetime_iso="2026-11-26T00:00:00+07:00",
        end_datetime_iso="2026-11-26T23:59:59+07:00",
        has_explicit_date=True,
        missing_fields=[],
    )
    service, llm, _ = make_conversation_service(
        repository,
        [list_extraction, list_extraction, list_extraction],
        listed_events=[],
    )
    t0 = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)

    await service.handle_user_message("1001", "Hôm nay có lịch gì?", now=t0)

    # After 31 minutes (> 30m TTL), older messages must NOT be included in prompt history.
    await service.handle_user_message(
        "1001",
        "Ngày mai có lịch gì?",
        now=t0 + timedelta(minutes=31),
    )
    assert "Recent conversation history" not in llm.calls[1][1]

    # /clear wipes all stored chat_messages immediately even within TTL.
    await service.handle_clear_command("1001")
    user = await repository.get_or_create_user("1001")
    remaining = await repository.get_recent_chat_messages(
        user.id,
        since=t0 - timedelta(hours=1),
        limit=20,
    )
    assert remaining == []

    await service.handle_user_message(
        "1001",
        "Ngày mai có lịch gì?",
        now=t0 + timedelta(minutes=32),
    )
    assert "Recent conversation history" not in llm.calls[2][1]


async def test_general_chat_everyday_questions_and_side_question_mid_clarification(
    repository: Repository,
) -> None:
    greeting = EventExtractionResult(
        intent=UserIntent.GENERAL_CHAT,
        reply_text="Chào bạn! Hôm nay là thứ Năm, ngày 26/11/2026.",
        missing_fields=[],
    )
    followup_chat = EventExtractionResult(
        intent=UserIntent.GENERAL_CHAT,
        reply_text="Thứ Sáu là ngày 27/11/2026.",
        missing_fields=[],
    )
    missing_time = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Họp team",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=["time"],
        clarification_question="Bạn muốn họp vào lúc mấy giờ?",
    )
    side_question = EventExtractionResult(
        intent=UserIntent.GENERAL_CHAT,
        reply_text="Thứ Sáu tuần này là ngày 27/11/2026.",
        missing_fields=[],
    )
    service, llm, _ = make_conversation_service(
        repository,
        [greeting, followup_chat, missing_time, side_question],
    )
    t0 = datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)

    r1 = await service.handle_user_message("1001", "Chào bạn, hôm nay thứ mấy?", now=t0)
    assert "thứ Năm, ngày 26/11/2026" in r1.text

    r2 = await service.handle_user_message(
        "1001",
        "Vậy thứ 6 là ngày mấy?",
        now=t0 + timedelta(minutes=1),
    )
    assert "Recent conversation history" in llm.calls[1][1]
    assert "27/11/2026" in r2.text

    # Side question during active clarification preserves COLLECTING_INFORMATION state.
    await service.handle_user_message("1001", "Tạo lịch họp team thứ 6", now=t0 + timedelta(minutes=2))
    r4 = await service.handle_user_message(
        "1001",
        "Thứ 6 là ngày bao nhiêu vậy?",
        now=t0 + timedelta(minutes=3),
    )
    assert "27/11/2026" in r4.text
    assert "pending event request is still waiting" in r4.text

    user = await repository.get_or_create_user("1001")
    conv = await repository.get_conversation(user.id)
    assert conv.state == ConversationState.COLLECTING_INFORMATION


async def test_batch_create_multiple_events_with_clarification(
    repository: Repository,
) -> None:
    missing_time = EventExtractionResult(
        intent=UserIntent.CREATE_EVENT,
        summary="Ăn tối với Messi",
        has_explicit_date=True,
        has_explicit_time=False,
        missing_fields=["time"],
        clarification_question="Daddy muốn ăn tối với Messi lúc mấy giờ vào thứ 7 và thứ 2 ạ?",
    )
    complete_batch = EventExtractionResult(
        intent=UserIntent.PROVIDE_CLARIFICATION,
        summary="Ăn tối với Messi",
        events=[
            ExtractedEventItem(
                summary="Ăn tối với Messi",
                start_datetime_iso="2026-11-28T19:00:00+07:00",
            ),
            ExtractedEventItem(
                summary="Ăn tối với Messi",
                start_datetime_iso="2026-11-30T19:00:00+07:00",
            ),
        ],
        has_explicit_date=True,
        has_explicit_time=True,
        missing_fields=[],
    )
    service, _, cal_client = make_conversation_service(
        repository,
        [missing_time, complete_batch],
    )
    now = datetime(2026, 11, 26, 3, 45, tzinfo=timezone.utc)

    q = await service.handle_user_message(
        "1001",
        "tạo lịch ăn tối với messi vào tối thứ 7 và tối thứ 2",
        now=now,
    )
    assert "thứ 7 và thứ 2" in q.text
    assert q.reply_markup_request_id is None

    preview = await service.handle_user_message("1001", "19h", now=now + timedelta(seconds=30))
    assert "Calendar Events Preview (2 events)" in preview.text
    assert "28/11" in preview.text
    assert "30/11" in preview.text
    assert preview.reply_markup_request_id is not None
    assert len(cal_client.created_events) == 0

    confirm_resp = await service.handle_confirmation(
        "1001",
        preview.reply_markup_request_id,
        confirmed=True,
        now=now + timedelta(seconds=45),
    )
    assert "Events created successfully! (2 events)" in confirm_resp.text
    assert len(cal_client.created_events) == 2
    assert cal_client.created_events[0].start_datetime.day == 28
    assert cal_client.created_events[1].start_datetime.day == 30


async def test_general_chat_with_tavily_web_search(repository: Repository) -> None:
    pass1 = EventExtractionResult(
        intent=UserIntent.GENERAL_CHAT,
        needs_web_search=True,
        search_query="Ho Chi Minh City weather tomorrow",
    )
    pass2 = EventExtractionResult(
        intent=UserIntent.GENERAL_CHAT,
        needs_web_search=False,
        reply_text="Ngày mai TP.HCM có nắng nhẹ, ít khả năng mưa daddy nhé.",
    )
    fake_web = FakeWebSearchClient(
        results=[
            WebSearchResult(
                title="HCMC Weather Forecast",
                url="https://example.com/weather",
                content="Partly cloudy, 26-33C, 10% rain.",
            )
        ]
    )
    service, llm, _ = make_conversation_service(
        repository,
        [pass1, pass2],
        web_search_client=fake_web,
    )
    now = datetime(2026, 11, 26, 3, 50, tzinfo=timezone.utc)

    resp = await service.handle_user_message("1001", "Ngày mai Sài Gòn có mưa không?", now=now)
    assert fake_web.queries == ["Ho Chi Minh City weather tomorrow"]
    assert "Web search results" in llm.calls[1][1]
    assert "nắng nhẹ" in resp.text
    assert "<b>Sources</b>" in resp.text
    assert "https://example.com/weather" in resp.text

    # Missing TAVILY_API_KEY (web_search_client=None) returns friendly notice.
    no_key_service, _, _ = make_conversation_service(
        repository,
        [pass1],
        web_search_client=None,
    )
    no_key_resp = await no_key_service.handle_user_message(
        "1001",
        "Ngày mai Sài Gòn có mưa không?",
        now=now + timedelta(minutes=1),
    )
    assert "Web Search Unavailable" in no_key_resp.text

