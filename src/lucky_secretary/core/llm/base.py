"""Abstract LLM provider interface for structured event extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from lucky_secretary.schemas.agent import EventExtractionResult


class LLMProviderError(Exception):
    """Raised when an LLM provider fails to return valid structured output."""


class LLMProvider(ABC):
    """Provider-agnostic interface so OpenAI, Gemini, or Groq can be swapped cleanly."""

    @abstractmethod
    async def extract_event(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> EventExtractionResult:
        """Extract structured calendar event details from prompts."""

