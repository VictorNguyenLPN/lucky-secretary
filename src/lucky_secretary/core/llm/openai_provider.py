"""OpenAI-compatible LLM provider supporting OpenAI, Gemini Free Tier, and Groq."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from lucky_secretary.core.llm.base import LLMProvider, LLMProviderError
from lucky_secretary.schemas.agent import EventExtractionResult

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Uses AsyncOpenAI Structured Outputs (beta.chat.completions.parse)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash-lite",
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )

    async def extract_event(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> EventExtractionResult:
        """Call OpenAI-compatible structured completion endpoint."""
        try:
            response = await self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=EventExtractionResult,
                temperature=0.0,
            )
        except OpenAIError as exc:
            logger.error("LLM API call failed: %s", type(exc).__name__)
            raise LLMProviderError("LLM service is currently unavailable.") from exc
        except ValidationError as exc:
            logger.error("LLM response failed schema validation: %s", exc)
            raise LLMProviderError("LLM returned an invalid structured response.") from exc

        if not response.choices:
            raise LLMProviderError("LLM returned no choices.")

        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise LLMProviderError("LLM refused to process the request.")

        parsed = message.parsed
        if parsed is None:
            raise LLMProviderError("LLM response could not be parsed into EventExtractionResult.")

        return parsed

