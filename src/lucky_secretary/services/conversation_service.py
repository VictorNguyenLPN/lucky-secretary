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
from lucky_secretary.schemas.agent import UserIntent
from lucky_secretary.schemas.calendar import CalendarEventRequest
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


def format_event_preview(event: CalendarEventRequest) -> str:
    """Render the FR-05 event preview message with clean minimalist typography."""
    assert event.end_datetime is not None
    date_str, time_range = format_event_preview_datetime(event.start_datetime, event.end_datetime)
    title = html.escape(event.summary)
    tz_label = html.escape(event.timezone)
    location = html.escape(event.location) if event.location else "Not specified"
    description = html.escape(event.description) if event.description else "Not specified"

    return (
        "<b>Calendar Event Preview</b>\n\n"
        f"• <b>Title:</b> {title}\n"
        f"• <b>Date:</b> {html.escape(date_str)}\n"
        f"• <b>Time:</b> {html.escape(time_range)}\n"
        f"• <b>Timezone:</b> {tz_label}\n"
        f"• <b>Location:</b> {location}\n"
        f"• <b>Description:</b> {description}"
    )


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
                        text=(
                            "You have a pending event waiting for confirmation. "
                            "Please click Confirm or Cancel on the preview above, "
                            "or send /cancel to discard it before creating a new event."
                        )
                    )
            else:
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)

        if conv.state == ConversationState.PROCESSING:
            return ConversationResponse(
                text="Your previous event request is still being processed. Please wait a moment."
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
                text=(
                    "Sorry, I encountered a temporary issue understanding your request. "
                    "Please try again in a moment."
                )
            )

        if extraction.intent == UserIntent.CANCEL:
            if active_collecting_id is not None:
                await self._repo.mark_request_cancelled(active_collecting_id, user.id)
            else:
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
            return ConversationResponse(text="Event request cancelled.")

        if extraction.intent == UserIntent.OTHER:
            if active_collecting_id is not None:
                return ConversationResponse(
                    text=(
                        "I'm still waiting for the remaining event details. "
                        "Please provide them or send /cancel to start over."
                    )
                )
            return ConversationResponse(
                text=(
                    "<b>How to use Lucky Secretary</b>\n\n"
                    "• Tạo lịch họp với team vào 9 giờ sáng mai\n"
                    "• Schedule a meeting tomorrow at 2 PM for 90 minutes"
                )
            )

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
                text=f"I couldn't validate those event details ({html.escape(str(exc))}). Please clarify."
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
                    text="This event request was not found.",
                    clear_keyboard=True,
                )
            if req.user_id != user.id:
                return ConversationResponse(
                    text="You are not authorized to modify this request."
                )
            if req.status == RequestStatus.PENDING_CONFIRMATION and req.expires_at <= current_time:
                await self._repo.update_calendar_request(request_id, status=RequestStatus.EXPIRED)
                await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
                return ConversationResponse(
                    text="This event confirmation request has expired. Please create a new request.",
                    clear_keyboard=True,
                )
            if req.status != RequestStatus.PENDING_CONFIRMATION:
                return ConversationResponse(
                    text="This event request has already been processed or cancelled.",
                    clear_keyboard=True,
                )
            await self._repo.mark_request_cancelled(request_id, user.id)
            return ConversationResponse(
                text="Event creation cancelled.",
                clear_keyboard=True,
            )

        acquired, reason, req = await self._repo.try_acquire_request_for_processing(
            request_id=request_id,
            user_id=user.id,
            now=current_time,
        )
        if not acquired:
            messages = {
                "not_found": ("This event request was not found.", True),
                "unauthorized": ("You are not authorized to confirm this request.", False),
                "expired": (
                    "This event confirmation request has expired. Please create a new request.",
                    True,
                ),
                "cancelled": ("This event request was already cancelled.", True),
                "already_processed": ("This event request has already been processed.", True),
            }
            msg, clear = messages.get(
                reason,
                ("This event request can no longer be confirmed.", True),
            )
            return ConversationResponse(text=msg, clear_keyboard=clear)

        assert req is not None
        try:
            event = CalendarEventRequest.model_validate(req.event_payload)
            result = await self._calendar_service.create_calendar_event(event)
        except (ValueError, GoogleCalendarError) as exc:
            logger.error("Calendar event creation failed for request_id=%s: %s", request_id, exc)
            await self._repo.mark_request_failed(request_id, user.id)
            return ConversationResponse(
                text=f"Failed to create Google Calendar event: {html.escape(str(exc))}",
                clear_keyboard=True,
            )

        await self._repo.mark_request_completed(
            request_id=request_id,
            user_id=user.id,
            google_event_id=result.event_id,
            event_url=result.html_link,
        )
        tz = ZoneInfo(result.timezone)
        local_start = result.start_datetime.astimezone(tz)
        local_end = result.end_datetime.astimezone(tz)
        date_str, time_range = format_event_preview_datetime(local_start, local_end)
        escaped_link = html.escape(result.html_link, quote=True)
        return ConversationResponse(
            text=(
                "<b>Event created successfully!</b>\n\n"
                f"• <b>Title:</b> {html.escape(result.summary)}\n"
                f"• <b>Date:</b> {html.escape(date_str)}\n"
                f"• <b>Time:</b> {html.escape(time_range)} ({html.escape(result.timezone)})\n\n"
                f'<a href="{escaped_link}">Open in Google Calendar</a>'
            ),
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
                text="Current event request has been cancelled.",
                clear_keyboard=True,
            )

        await self._repo.update_conversation(user.id, ConversationState.IDLE, None)
        return ConversationResponse(text="There is no active event request to cancel.")

