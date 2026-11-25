"""Shared pytest fixtures for unit and integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from lucky_secretary.core.agent import CalendarAgent
from lucky_secretary.core.llm.base import LLMProvider
from lucky_secretary.database.connection import DatabaseManager
from lucky_secretary.database.repository import Repository
from lucky_secretary.integrations.google_calendar.client import GoogleCalendarError
from lucky_secretary.schemas.agent import EventExtractionResult
from lucky_secretary.schemas.calendar import CalendarEventRequest, GoogleCalendarEventResult
from lucky_secretary.services.calendar_service import CalendarService
from lucky_secretary.services.conversation_service import ConversationService


class FakeLLMProvider(LLMProvider):
    """Deterministic fake LLM provider returning queued EventExtractionResult or raising errors."""

    def __init__(self, responses: list[EventExtractionResult | Exception] | None = None) -> None:
        self.responses: list[EventExtractionResult | Exception] = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    async def extract_event(self, system_prompt: str, user_prompt: str) -> EventExtractionResult:
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("FakeLLMProvider has no more scripted responses.")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeGoogleCalendarClient:
    """Fake GoogleCalendarClient recording created events."""

    def __init__(self, fail_with: str | None = None) -> None:
        self.created_events: list[CalendarEventRequest] = []
        self.fail_with = fail_with

    async def create_event(self, event: CalendarEventRequest) -> GoogleCalendarEventResult:
        if self.fail_with:
            raise GoogleCalendarError(self.fail_with)
        self.created_events.append(event)
        idx = len(self.created_events)
        assert event.end_datetime is not None
        return GoogleCalendarEventResult(
            event_id=f"evt_{idx}",
            summary=event.summary,
            start_datetime=event.start_datetime,
            end_datetime=event.end_datetime,
            timezone=event.timezone,
            html_link=f"https://calendar.google.com/calendar/event?eid=evt_{idx}",
        )


@pytest.fixture
async def db_manager(tmp_path: Path) -> AsyncIterator[DatabaseManager]:
    db_path = tmp_path / "test_lucky_secretary.db"
    manager = DatabaseManager(str(db_path))
    await manager.init_db()
    try:
        yield manager
    finally:
        await manager.close()


@pytest.fixture
def repository(db_manager: DatabaseManager) -> Repository:
    return Repository(db_manager)


def make_conversation_service(
    repo: Repository,
    responses: list[EventExtractionResult | Exception] | None = None,
    fail_calendar_with: str | None = None,
) -> tuple[ConversationService, FakeLLMProvider, FakeGoogleCalendarClient]:
    llm = FakeLLMProvider(responses)
    agent = CalendarAgent(llm_provider=llm)
    cal_client = FakeGoogleCalendarClient(fail_with=fail_calendar_with)
    cal_service = CalendarService(cal_client)  # type: ignore[arg-type]
    service = ConversationService(
        repository=repo,
        agent=agent,
        calendar_service=cal_service,
    )
    return service, llm, cal_client

