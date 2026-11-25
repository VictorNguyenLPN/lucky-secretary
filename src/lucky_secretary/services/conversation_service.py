"""Conversation state machine and event confirmation workflow."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from lucky_secretary.core.agent import CalendarAgent
from lucky_secretary.core.llm.base import LLMProviderError
from lucky_secretary.database.models import ConversationState, RequestStatus
from lucky_secretary.database.repository import Repository
from lucky_secretary.integrations.google_calendar.client import GoogleCalendarError
from lucky_secretary.schemas.agent import EventExtractionResult, UserIntent
from lucky_secretary.schemas.calendar import (
    CalendarEventItem,
    CalendarEventRequest,
    GoogleCalendarEventResult,
)
from lucky_secretary.services.calendar_service import CalendarService
from lucky_secretary.utils.datetime import (
    ensure_timezone_aware,
    format_event_preview_datetime,
    now_utc,
)

logger = logging.getLogger(__name__)


class ConversationResponse(BaseModel):
    """Framework-agnostic response returned to the Telegram interface."""

    text: str
    reply_markup_request_id: str | None = None
    clear_keyboard: bool = False


def format_event_preview(
    event: CalendarEventRequest,
    header: str = "Calendar Event Preview",
) -> str:
    """Render a calendar event preview message with clean minimalist typography."""
    assert event.end_datetime is not None
    date_str, time_range = format_event_preview_datetime(event.start_datetime, event.end_datetime)
    title = html.escape(event.summary)
    tz_label = html.escape(event.timezone)
    location = html.escape(event.location) if event.location else "Not specified"
    description = html.escape(event.description) if event.description else "Not specified"

    return (
        f"📅 <b>{html.escape(header)}</b>\n\n"
        f"• <b>Title:</b> {title}\n"
        f"• <b>Date:</b> {html.escape(date_str)}\n"
        f"• <b>Time:</b> {html.escape(time_range)}\n"
        f"• <b>Timezone:</b> {tz_label}\n"
        f"• <b>Location:</b> {location}\n"
        f"• <b>Description:</b> {description}"
    )


def format_delete_preview(target: CalendarEventItem, timezone_name: str) -> str:
    """Render a confirmation preview for deleting an existing Google Calendar event."""
    tz = ZoneInfo(timezone_name)
    if target.all_day_date:
        date_str = target.all_day_date
        time_range = "All day"
    elif target.start_datetime is not None and target.end_datetime is not None:
        date_str, time_range = format_event_preview_datetime(
            target.start_datetime.astimezone(tz),
            target.end_datetime.astimezone(tz),
        )
    else:
        date_str = "Not specified"
        time_range = "Not specified"

    location = html.escape(target.location) if target.location else "Not specified"
    return (
        "🗑 <b>Delete Event Preview</b>\n\n"
        f"• <b>Title:</b> {html.escape(target.summary)}\n"
        f"• <b>Date:</b> {html.escape(date_str)}\n"
        f"• <b>Time:</b> {html.escape(time_range)}\n"
        f"• <b>Timezone:</b> {html.escape(timezone_name)}\n"
        f"• <b>Location:</b> {location}"
    )


def format_batch_delete_preview(
    targets: list[CalendarEventItem],
    timezone_name: str,
) -> str:
    """Render a confirmation preview for deleting one or multiple events."""
    if len(targets) == 1:
        return format_delete_preview(targets[0], timezone_name)

    tz = ZoneInfo(timezone_name)
    lines = [f"🗑 <b>Delete Events Preview ({len(targets)} events)</b>", ""]
    for ev in targets:
        lines.append(_format_schedule_bullet(ev, tz, multi_day=True))
    return "\n".join(lines)


def _format_schedule_bullet(ev: CalendarEventItem, tz: ZoneInfo, multi_day: bool) -> str:
    if ev.all_day_date:
        time_part = f"All day ({ev.all_day_date})" if multi_day else "All day"
    elif ev.start_datetime is not None and ev.end_datetime is not None:
        local_start = ev.start_datetime.astimezone(tz)
        local_end = ev.end_datetime.astimezone(tz)
        day_prefix = f"{local_start.strftime('%a %d/%m')} " if multi_day else ""
        time_part = f"{day_prefix}{local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')}"
    else:
        time_part = "Scheduled"

    location_suffix = f" ({html.escape(ev.location)})" if ev.location else ""
    return f"• <b>{html.escape(time_part)}:</b> {html.escape(ev.summary)}{location_suffix}"


_NOTICE_ICONS: dict[str, str] = {
    "Service Error": "❌",
    "Google Calendar Error": "❌",
    "Invalid Event Details": "❌",
    "Invalid Request": "❌",
    "Unauthorized": "❌",
    "Event Not Found": "⚠️",
    "Request Not Found": "⚠️",
    "Pending Confirmation": "⚠️",
    "Clarification Needed": "⚠️",
    "Request Expired": "⏳",
    "Request In Progress": "⏳",
    "Request Cancelled": "🚫",
    "Already Processed": "ℹ️",
    "No Active Request": "ℹ️",
}


def _format_notice(
    heading: str,
    message: str,
    bullets: list[str] | None = None,
    footer: str | None = None,
) -> str:
    """Format a standardized minimalist HTML notice or error message."""
    icon = _NOTICE_ICONS.get(heading, "ℹ️")
    parts = [f"{icon} <b>{html.escape(heading)}</b>", "", html.escape(message)]
    if bullets:
        parts.append("")
        parts.extend(f"• {html.escape(item)}" for item in bullets)
    if footer:
        parts.extend(["", html.escape(footer)])
    return "\n".join(parts)


def _unsupported_request_text() -> str:
    return (
        "⚠️ <b>Unsupported Request</b>\n\n"
        "Lucky Secretary could not process this query or does not support this feature.\n\n"
        "<b>Supported capabilities</b>\n"
        "• Create event: Tạo lịch họp với team vào 9 giờ sáng mai.\n"
        "• View schedule: Ngày mai tôi có những lịch nào?\n"
        "• Update event: Đổi lịch họp team ngày mai sang 10h.\n"
        "• Delete event: Xóa lịch họp team ngày mai.\n\n"
        "Use /help for usage instructions or /cancel to reset."
    )


def _format_event_success(heading: str, result: GoogleCalendarEventResult) -> str:
    tz = ZoneInfo(result.timezone)
    local_start = result.start_datetime.astimezone(tz)
    local_end = result.end_datetime.astimezone(tz)
    date_str, time_range = format_event_preview_datetime(local_start, local_end)
    escaped_link = html.escape(result.html_link, quote=True)
    return (
        f"✅ <b>{html.escape(heading)}</b>\n\n"
        f"• <b>Title:</b> {html.escape(result.summary)}\n"
        f"• <b>Date:</b> {html.escape(date_str)}\n"
        f"• <b>Time:</b> {html.escape(time_range)} ({html.escape(result.timezone)})\n\n"
        f'<a href="{escaped_link}">Open in Google Calendar</a>'
    )


def _build_updated_event_request(
    target: CalendarEventItem,
    extraction: EventExtractionResult,
    timezone_name: str,
    default_duration_minutes: int,
) -> CalendarEventRequest:
    if extraction.summary and extraction.summary.strip():
        new_summary = extraction.summary.strip()
    else:
        new_summary = target.summary

    if extraction.start_datetime_iso:
        new_start = ensure_timezone_aware(
            datetime.fromisoformat(extraction.start_datetime_iso),
            timezone_name,
        )
    elif target.start_datetime is not None:
        new_start = ensure_timezone_aware(target.start_datetime, timezone_name)
    else:
        raise ValueError("Please specify the new start time for this all-day event.")

    new_end: datetime | None = None
    new_duration: int | None = extraction.duration_minutes
    if extraction.end_datetime_iso:
        new_end = ensure_timezone_aware(
            datetime.fromisoformat(extraction.end_datetime_iso),
            timezone_name,
        )
    elif new_duration is None:
        if target.start_datetime is not None and target.end_datetime is not None:
            new_duration = max(
                1,
                round((target.end_datetime - target.start_datetime).total_seconds() / 60),
            )
        else:
            new_duration = default_duration_minutes

    new_location = extraction.location if extraction.location is not None else target.location
    new_description = (
        extraction.description if extraction.description is not None else target.description
    )

    return CalendarEventRequest(
        summary=new_summary,
        start_datetime=new_start,
        end_datetime=new_end,
        duration_minutes=new_duration,
        timezone=timezone_name,
        location=new_location,
        description=new_description,
    )


def format_event_list(
    events: list[CalendarEventItem],
    time_min: datetime,
    time_max: datetime,
    timezone_name: str,
) -> str:
    """Render a list of Google Calendar events in minimalist bulleted HTML format."""
    tz = ZoneInfo(timezone_name)
    local_min = time_min.astimezone(tz)
    local_max = time_max.astimezone(tz)
    multi_day = local_min.date() != local_max.date()

    if multi_day:
        date_label = f"{local_min.strftime('%b %d, %Y')} - {local_max.strftime('%b %d, %Y')}"
    else:
        date_label = local_min.strftime("%A, %B %d, %Y")

    header = f"📅 <b>Schedule for {html.escape(date_label)}</b>"
    if not events:
        return f"{header}\n\nNo events scheduled."

    lines = [header, ""]
    for ev in events:
        lines.append(_format_schedule_bullet(ev, tz, multi_day=multi_day))

    return "\n".join(lines)


class ConversationService:
    """Manages state transitions, clarification context, expiration, and confirmation."""

    def __init__(
        self,
        repository: Repository,
        agent: CalendarAgent,
        calendar_service: CalendarService,
        default_timezone: str = "Asia/Ho_Chi_Minh",
        default_duration_minutes: int = 60,
        confirmation_timeout_minutes: int = 15,
    ) -> None:
        self._repo = repository
        self._agent = agent
        self._calendar_service = calendar_service
        self._default_timezone = default_timezone
        self._default_duration_minutes = default_duration_minutes
        self._confirmation_timeout_minutes = confirmation_timeout_minutes

    async def handle_user_message(
        self,
        telegram_user_id: str,
        text: str,
        now: datetime | None = None,
    ) -> ConversationResponse:
        """Process an incoming user message through the conversation state machine."""
        current_time = now or now_utc()
        user = await self._repo.get_or_create_user(telegram_user_id, self._default_timezone)
        conv = await self._repo.get_conversation(user.id)

        # Protect active pending confirmation requests from silent overwrite (FR-07).
        if conv.state == ConversationState.WAITING_CONFIRMATION and conv.pending_request_id:
            pending = await self._repo.get_calendar_request(conv.pending_request_id)
            if pending is not None and pending.status == RequestStatus.PENDING_CONFIRMATION:
                if pending.expires_at <= current_time:
                    await self._repo.update_calendar_request(
                        pending.id,
                        status=RequestStatus.EXPIRED,
                    )
                    await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
                else:
                    return ConversationResponse(
                        text=_format_notice(
                            "Pending Confirmation",
                            "You have a pending event waiting for confirmation.",
                            [
                                "Click Confirm or Cancel on the preview above",
                                "Send /cancel to discard it before creating a new request",
                            ],
                        )
                    )
            else:
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)

        if conv.state == ConversationState.PROCESSING:
            return ConversationResponse(
                text=_format_notice(
                    "Request In Progress",
                    "Your previous event request is still being processed. Please wait a moment.",
                )
            )

        partial_context = None
        active_collecting_id: str | None = None
        if conv.state == ConversationState.COLLECTING_INFORMATION and conv.pending_request_id:
            collecting_req = await self._repo.get_calendar_request(conv.pending_request_id)
            if (
                collecting_req is not None
                and collecting_req.status == RequestStatus.COLLECTING
                and collecting_req.expires_at > current_time
            ):
                partial_context = collecting_req.event_payload
                active_collecting_id = collecting_req.id
            else:
                if collecting_req is not None and collecting_req.status == RequestStatus.COLLECTING:
                    await self._repo.update_calendar_request(
                        collecting_req.id,
                        status=RequestStatus.EXPIRED,
                    )
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)

        ref_local = ensure_timezone_aware(current_time, user.timezone)
        try:
            extraction = await self._agent.process_message(
                user_message=text,
                partial_context=partial_context,
                reference_time=ref_local,
            )
        except LLMProviderError:
            return ConversationResponse(
                text=_format_notice(
                    "Service Error",
                    "Could not process your request right now. Please try again in a moment.",
                )
            )

        if extraction.intent == UserIntent.LIST_EVENTS:
            try:
                time_min, time_max = extraction.to_calendar_query_range(
                    reference_time=ref_local,
                    default_timezone=user.timezone,
                )
                events = await self._calendar_service.list_calendar_events(time_min, time_max)
            except (ValueError, GoogleCalendarError) as exc:
                logger.error("Failed to list calendar events: %s", exc)
                return ConversationResponse(
                    text=_format_notice(
                        "Google Calendar Error",
                        f"Failed to fetch your Google Calendar events: {exc}",
                    )
                )
            return ConversationResponse(
                text=format_event_list(events, time_min, time_max, user.timezone)
            )

        if extraction.intent == UserIntent.CANCEL:
            if active_collecting_id is not None:
                await self._repo.mark_request_cancelled(active_collecting_id, user.id)
            else:
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
            return ConversationResponse(
                text=_format_notice("Request Cancelled", "Event request cancelled.")
            )

        if extraction.intent == UserIntent.OTHER:
            if active_collecting_id is not None:
                return ConversationResponse(
                    text=_format_notice(
                        "Clarification Needed",
                        "I am still waiting for the remaining event details to complete your request.",
                        [
                            "Reply with the missing information",
                            "Send /cancel to discard and start over",
                        ],
                    )
                )
            return ConversationResponse(text=_unsupported_request_text())

        expires_at = current_time + timedelta(minutes=self._confirmation_timeout_minutes)

        if not extraction.is_complete():
            question = (
                extraction.clarification_question
                or "Could you please provide the missing event title, date, or start time?"
            )
            logger.info("Clarification requested for user_id=%s", user.id)
            partial_payload = extraction.to_partial_context()
            if active_collecting_id is not None:
                req = await self._repo.update_calendar_request(
                    active_collecting_id,
                    event_payload=partial_payload,
                    expires_at=expires_at,
                )
            else:
                req = await self._repo.create_calendar_request(
                    user_id=user.id,
                    status=RequestStatus.COLLECTING,
                    event_payload=partial_payload,
                    expires_at=expires_at,
                )
            await self._repo.update_conversation(
                user.id,
                ConversationState.COLLECTING_INFORMATION,
                req.id,
            )
            return ConversationResponse(text=html.escape(question))

        if extraction.intent in (UserIntent.UPDATE_EVENT, UserIntent.DELETE_EVENT):
            try:
                search_min, search_max = extraction.to_target_search_range(
                    reference_time=ref_local,
                    default_timezone=user.timezone,
                )
                candidates = await self._calendar_service.list_calendar_events(
                    search_min,
                    search_max,
                )
            except (ValueError, GoogleCalendarError) as exc:
                logger.error("Failed to search calendar events: %s", exc)
                return ConversationResponse(
                    text=_format_notice(
                        "Google Calendar Error",
                        f"Failed to search your Google Calendar events: {exc}",
                    )
                )

            if not candidates:
                if active_collecting_id is not None:
                    await self._repo.mark_request_cancelled(active_collecting_id, user.id)
                return ConversationResponse(
                    text=_format_notice(
                        "Event Not Found",
                        "No matching event was found in your Google Calendar for the specified period.",
                        ["Check your schedule first: Ngày mai tôi có những lịch nào?"],
                    )
                )

            candidate_by_id = {c.event_id: c for c in candidates if c.event_id}
            if (
                extraction.intent == UserIntent.DELETE_EVENT
                and extraction.delete_all_in_range
                and not (extraction.target_summary and extraction.target_summary.strip())
            ):
                matched_targets = list(candidate_by_id.values())
            else:
                resolved_ids = [
                    eid for eid in extraction.resolved_target_event_ids() if eid in candidate_by_id
                ]
                if not resolved_ids:
                    candidates_data = [c.model_dump(mode="json") for c in candidates]
                    try:
                        extraction = await self._agent.process_message(
                            user_message=text,
                            partial_context=partial_context,
                            candidate_events=candidates_data,
                            reference_time=ref_local,
                        )
                    except LLMProviderError:
                        return ConversationResponse(
                            text=_format_notice(
                                "Service Error",
                                "Could not process your request right now. Please try again in a moment.",
                            )
                        )

                if extraction.missing_fields:
                    question = (
                        extraction.clarification_question
                        or "Could you please clarify which event you want to modify?"
                    )
                    partial_payload = extraction.to_partial_context()
                    if active_collecting_id is not None:
                        req = await self._repo.update_calendar_request(
                            active_collecting_id,
                            event_payload=partial_payload,
                            expires_at=expires_at,
                        )
                    else:
                        req = await self._repo.create_calendar_request(
                            user_id=user.id,
                            status=RequestStatus.COLLECTING,
                            event_payload=partial_payload,
                            expires_at=expires_at,
                        )
                    await self._repo.update_conversation(
                        user.id,
                        ConversationState.COLLECTING_INFORMATION,
                        req.id,
                    )
                    return ConversationResponse(text=html.escape(question))

                matched_targets = [
                    candidate_by_id[eid]
                    for eid in extraction.resolved_target_event_ids()
                    if eid in candidate_by_id
                ]

            if not matched_targets:
                if active_collecting_id is not None:
                    await self._repo.mark_request_cancelled(active_collecting_id, user.id)
                return ConversationResponse(
                    text=_format_notice(
                        "Event Not Found",
                        "No matching event was found in your Google Calendar for the specified request.",
                        ["Check your schedule first: Ngày mai tôi có những lịch nào?"],
                    )
                )

            if extraction.intent == UserIntent.DELETE_EVENT:
                event_payload = {
                    "action": "delete",
                    "event_id": matched_targets[0].event_id,
                    "event_ids": [t.event_id for t in matched_targets],
                    "summary": matched_targets[0].summary,
                    "summaries": [t.summary for t in matched_targets],
                    "timezone": user.timezone,
                }
                preview_text = format_batch_delete_preview(matched_targets, user.timezone)
            else:
                target = matched_targets[0]
                try:
                    updated_event = _build_updated_event_request(
                        target=target,
                        extraction=extraction,
                        timezone_name=user.timezone,
                        default_duration_minutes=self._default_duration_minutes,
                    )
                except ValueError as exc:
                    return ConversationResponse(
                        text=_format_notice(
                            "Invalid Event Details",
                            f"Could not validate the updated event details ({exc}).",
                            [
                                "Reply with the corrected date, time, or duration",
                                "Send /cancel to discard this request",
                            ],
                        )
                    )
                event_payload = {
                    "action": "update",
                    "event_id": target.event_id,
                    **updated_event.model_dump(mode="json"),
                }
                preview_text = format_event_preview(
                    updated_event,
                    header="Update Event Preview",
                )

            if active_collecting_id is not None:
                req = await self._repo.update_calendar_request(
                    active_collecting_id,
                    status=RequestStatus.PENDING_CONFIRMATION,
                    event_payload=event_payload,
                    expires_at=expires_at,
                )
            else:
                req = await self._repo.create_calendar_request(
                    user_id=user.id,
                    status=RequestStatus.PENDING_CONFIRMATION,
                    event_payload=event_payload,
                    expires_at=expires_at,
                )
            await self._repo.update_conversation(
                user.id,
                ConversationState.WAITING_CONFIRMATION,
                req.id,
            )
            return ConversationResponse(
                text=preview_text,
                reply_markup_request_id=req.id,
            )

        try:
            event = extraction.to_calendar_event_request(
                default_duration=self._default_duration_minutes,
                default_timezone=user.timezone,
            )
        except ValueError as exc:
            logger.warning("Extracted event failed validation: %s", exc)
            partial_payload = extraction.to_partial_context()
            if active_collecting_id is not None:
                req = await self._repo.update_calendar_request(
                    active_collecting_id,
                    event_payload=partial_payload,
                    expires_at=expires_at,
                )
            else:
                req = await self._repo.create_calendar_request(
                    user_id=user.id,
                    status=RequestStatus.COLLECTING,
                    event_payload=partial_payload,
                    expires_at=expires_at,
                )
            await self._repo.update_conversation(
                user.id,
                ConversationState.COLLECTING_INFORMATION,
                req.id,
            )
            return ConversationResponse(
                text=_format_notice(
                    "Invalid Event Details",
                    f"Could not validate the event details ({exc}).",
                    [
                        "Reply with the corrected date, time, or duration",
                        "Send /cancel to discard this request",
                    ],
                )
            )

        event_payload = event.model_dump(mode="json")
        if active_collecting_id is not None:
            req = await self._repo.update_calendar_request(
                active_collecting_id,
                status=RequestStatus.PENDING_CONFIRMATION,
                event_payload=event_payload,
                expires_at=expires_at,
            )
        else:
            req = await self._repo.create_calendar_request(
                user_id=user.id,
                status=RequestStatus.PENDING_CONFIRMATION,
                event_payload=event_payload,
                expires_at=expires_at,
            )

        await self._repo.update_conversation(
            user.id,
            ConversationState.WAITING_CONFIRMATION,
            req.id,
        )
        return ConversationResponse(
            text=format_event_preview(event),
            reply_markup_request_id=req.id,
        )

    async def handle_confirmation(
        self,
        telegram_user_id: str,
        request_id: str,
        confirmed: bool,
        now: datetime | None = None,
    ) -> ConversationResponse:
        """Handle Confirm or Cancel callback actions idempotently."""
        current_time = now or now_utc()
        user = await self._repo.get_or_create_user(telegram_user_id, self._default_timezone)
        logger.info(
            "Event confirmation callback received: user_id=%s request_id=%s confirmed=%s",
            user.id,
            request_id,
            confirmed,
        )

        if not confirmed:
            req = await self._repo.get_calendar_request(request_id)
            if req is None:
                return ConversationResponse(
                    text=_format_notice("Request Not Found", "This event request was not found."),
                    clear_keyboard=True,
                )
            if req.user_id != user.id:
                return ConversationResponse(
                    text=_format_notice(
                        "Unauthorized",
                        "You are not authorized to modify this request.",
                    )
                )
            if req.status == RequestStatus.PENDING_CONFIRMATION and req.expires_at <= current_time:
                await self._repo.update_calendar_request(request_id, status=RequestStatus.EXPIRED)
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
                return ConversationResponse(
                    text=_format_notice(
                        "Request Expired",
                        "This event confirmation request has expired. Please create a new request.",
                    ),
                    clear_keyboard=True,
                )
            if req.status != RequestStatus.PENDING_CONFIRMATION:
                return ConversationResponse(
                    text=_format_notice(
                        "Already Processed",
                        "This event request has already been processed or cancelled.",
                    ),
                    clear_keyboard=True,
                )
            await self._repo.mark_request_cancelled(request_id, user.id)
            return ConversationResponse(
                text=_format_notice("Request Cancelled", "Event action cancelled."),
                clear_keyboard=True,
            )

        acquired, reason, req = await self._repo.try_acquire_request_for_processing(
            request_id=request_id,
            user_id=user.id,
            now=current_time,
        )
        if not acquired:
            messages = {
                "not_found": (
                    _format_notice("Request Not Found", "This event request was not found."),
                    True,
                ),
                "unauthorized": (
                    _format_notice(
                        "Unauthorized",
                        "You are not authorized to confirm this request.",
                    ),
                    False,
                ),
                "expired": (
                    _format_notice(
                        "Request Expired",
                        "This event confirmation request has expired. Please create a new request.",
                    ),
                    True,
                ),
                "cancelled": (
                    _format_notice(
                        "Request Cancelled",
                        "This event request was already cancelled.",
                    ),
                    True,
                ),
                "already_processed": (
                    _format_notice(
                        "Already Processed",
                        "This event request has already been processed.",
                    ),
                    True,
                ),
            }
            msg, clear = messages.get(
                reason,
                (
                    _format_notice(
                        "Invalid Request",
                        "This event request can no longer be confirmed.",
                    ),
                    True,
                ),
            )
            return ConversationResponse(text=msg, clear_keyboard=clear)

        assert req is not None
        action = req.event_payload.get("action", "create")

        if action == "delete":
            event_ids: list[str] = req.event_payload.get("event_ids") or [
                req.event_payload.get("event_id", "")
            ]
            summaries: list[str] = req.event_payload.get("summaries") or [
                req.event_payload.get("summary", "")
            ]
            try:
                for eid in event_ids:
                    await self._calendar_service.delete_calendar_event(eid)
            except GoogleCalendarError as exc:
                logger.error("Calendar event deletion failed for request_id=%s: %s", request_id, exc)
                await self._repo.mark_request_failed(request_id, user.id)
                return ConversationResponse(
                    text=_format_notice(
                        "Google Calendar Error",
                        f"Failed to delete Google Calendar event: {exc}",
                    ),
                    clear_keyboard=True,
                )

            await self._repo.mark_request_completed(
                request_id=request_id,
                user_id=user.id,
                google_event_id=",".join(event_ids),
                event_url="",
            )
            if len(summaries) == 1:
                text = (
                    "✅ <b>Event deleted successfully!</b>\n\n"
                    f"• <b>Title:</b> {html.escape(summaries[0])}"
                )
            else:
                bullet_lines = "\n".join(
                    f"• <b>Title:</b> {html.escape(name)}" for name in summaries
                )
                text = (
                    f"✅ <b>Events deleted successfully! ({len(summaries)} events)</b>\n\n"
                    f"{bullet_lines}"
                )
            return ConversationResponse(text=text, clear_keyboard=True)

        try:
            event = CalendarEventRequest.model_validate(req.event_payload)
            if action == "update":
                event_id = req.event_payload.get("event_id", "")
                result = await self._calendar_service.update_calendar_event(event_id, event)
                success_heading = "Event updated successfully!"
            else:
                result = await self._calendar_service.create_calendar_event(event)
                success_heading = "Event created successfully!"
        except (ValueError, GoogleCalendarError) as exc:
            logger.error("Calendar event operation failed for request_id=%s: %s", request_id, exc)
            await self._repo.mark_request_failed(request_id, user.id)
            return ConversationResponse(
                text=_format_notice(
                    "Google Calendar Error",
                    f"Failed to process Google Calendar event: {exc}",
                ),
                clear_keyboard=True,
            )

        await self._repo.mark_request_completed(
            request_id=request_id,
            user_id=user.id,
            google_event_id=result.event_id,
            event_url=result.html_link,
        )
        return ConversationResponse(
            text=_format_event_success(success_heading, result),
            clear_keyboard=True,
        )

    async def handle_cancel_command(self, telegram_user_id: str) -> ConversationResponse:
        """Cancel any active collecting or pending confirmation request via /cancel."""
        user = await self._repo.get_or_create_user(telegram_user_id, self._default_timezone)
        conv = await self._repo.get_conversation(user.id)
        if conv.pending_request_id and conv.state in (
            ConversationState.COLLECTING_INFORMATION,
            ConversationState.WAITING_CONFIRMATION,
        ):
            await self._repo.mark_request_cancelled(conv.pending_request_id, user.id)
            return ConversationResponse(
                text=_format_notice(
                    "Request Cancelled",
                    "Current event request has been cancelled.",
                ),
                clear_keyboard=True,
            )

        await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
        return ConversationResponse(
            text=_format_notice(
                "No Active Request",
                "There is no active event request to cancel.",
            )
        )

