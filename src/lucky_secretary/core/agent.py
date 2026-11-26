"""AI Core agent orchestrating prompt construction and structured extraction."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lucky_secretary.core.llm.base import LLMProvider
from lucky_secretary.core.prompts import build_system_prompt, build_user_prompt
from lucky_secretary.schemas.agent import EventExtractionResult
from lucky_secretary.utils.datetime import ensure_timezone_aware, now_in_timezone

logger = logging.getLogger(__name__)


class CalendarAgent:
    """Extracts structured calendar event information without accessing Google Calendar."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        default_timezone: str = "Asia/Ho_Chi_Minh",
        default_duration_minutes: int = 60,
    ) -> None:
        self._llm = llm_provider
        self._default_timezone = default_timezone
        self._default_duration_minutes = default_duration_minutes

    async def process_message(
        self,
        user_message: str,
        partial_context: dict[str, Any] | None = None,
        candidate_events: list[dict[str, Any]] | None = None,
        chat_history: list[dict[str, Any]] | None = None,
        reference_time: datetime | None = None,
    ) -> EventExtractionResult:
        """Analyze a user message (optionally with history / partial context / candidates)."""
        ref_dt = (
            ensure_timezone_aware(reference_time, self._default_timezone)
            if reference_time is not None
            else now_in_timezone(self._default_timezone)
        )
        system_prompt = build_system_prompt(
            reference_time=ref_dt,
            timezone_name=self._default_timezone,
            default_duration_minutes=self._default_duration_minutes,
        )
        user_prompt = build_user_prompt(
            user_message=user_message,
            partial_context=partial_context,
            candidate_events=candidate_events,
            chat_history=chat_history,
        )
        result = await self._llm.extract_event(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        if not result.timezone:
            result.timezone = self._default_timezone
        logger.info(
            "LLM extraction completed: intent=%s complete=%s missing=%s",
            result.intent.value,
            result.is_complete(),
            result.missing_fields,
        )
        return result

