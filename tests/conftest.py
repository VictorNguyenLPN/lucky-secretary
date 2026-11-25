"""Shared pytest fixtures for unit and integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest

from lucky_secretary.core.agent import CalendarAgent
from lucky_secretary.core.llm.base import LLMProvider
from lucky_secretary.database.connection import DatabaseManager
from lucky_secretary.database.repository import Repository
from lucky_secretary.integrations.google_calendar.client import GoogleCalendarError
from lucky_secretary.schemas.agent import EventExtractionResult
from lucky_secretary.schemas.calendar import (
    CalendarEventItem,
    CalendarEventRequest,
    GoogleCalendarEventResult,
)
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
    """Fake GoogleCalendarClient recording created events and returning stubbed schedule items."""

    def __init__(
        self,
        fail_with: str | None = None,
        listed_events: list[CalendarEventItem] | None = None,
    ) -> None:
        self.created_events: list[CalendarEventRequest] = []
        self.updated_events: list[tuple[str, CalendarEventRequest]] = []
        self.deleted_event_ids: list[str] = []
        self.list_queries: list[tuple[datetime, datetime]] = []
        self.listed_events: list[CalendarEventItem] = list(listed_events or [])
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

    async def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        max_results: int = 20,
    ) -> list[CalendarEventItem]:
        if self.fail_with:
            raise GoogleCalendarError(self.fail_with)
        self.list_queries.append((time_min, time_max))
        return self.listed_events[:max_results]

    async def update_event(
        self,
        event_id: str,
        event: CalendarEventRequest,
    ) -> GoogleCalendarEventResult:
        if self.fail_with:
            raise GoogleCalendarError(self.fail_with)
        self.updated_events.append((event_id, event))
        assert event.end_datetime is not None
        return GoogleCalendarEventResult(
            event_id=event_id,
            summary=event.summary,
            start_datetime=event.start_datetime,
            end_datetime=event.end_datetime,
            timezone=event.timezone,
            html_link=f"https://calendar.google.com/calendar/event?eid={event_id}",
        )

    async def delete_event(self, event_id: str) -> None:
        if self.fail_with:
            raise GoogleCalendarError(self.fail_with)
        self.deleted_event_ids.append(event_id)


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
    listed_events: list[CalendarEventItem] | None = None,
) -> tuple[ConversationService, FakeLLMProvider, FakeGoogleCalendarClient]:
    llm = FakeLLMProvider(responses)
    agent = CalendarAgent(llm_provider=llm)
    cal_client = FakeGoogleCalendarClient(
        fail_with=fail_calendar_with,
        listed_events=listed_events,
    )
    cal_service = CalendarService(cal_client)  # type: ignore[arg-type]
    service = ConversationService(
        repository=repo,
        agent=agent,
        calendar_service=cal_service,
    )
    return service, llm, cal_client

