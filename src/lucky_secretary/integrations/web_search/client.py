"""Async Tavily Search API client for real-time general_chat queries."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class WebSearchError(Exception):
    """Raised when the web search provider fails or returns an error."""


class WebSearchResult(BaseModel):
    """Normalized search result item passed to the LLM for grounding."""

    title: str
    url: str
    content: str


class TavilySearchClient:
    """Thin async client over Tavily's REST search endpoint."""

    def __init__(
        self,
        api_key: str,
        max_results: int = 4,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._max_results = max_results
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def search(self, query: str) -> list[WebSearchResult]:
        """Search the web via Tavily and return cleaned snippets."""
        cleaned = query.strip()
        if not cleaned:
            return []

        payload = {
            "query": cleaned,
            "search_depth": "basic",
            "max_results": self._max_results,
            "include_answer": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    TAVILY_SEARCH_URL,
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.error("Tavily search HTTP transport error: %s", type(exc).__name__)
            raise WebSearchError("Web search service is currently unavailable.") from exc

        if response.status_code != 200:
            logger.error("Tavily search returned HTTP %s", response.status_code)
            raise WebSearchError(f"Web search failed (status {response.status_code}).")

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise WebSearchError("Invalid JSON response from web search.") from exc

        results: list[WebSearchResult] = []
        for item in data.get("results", []):
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            content = str(item.get("content") or "").strip()[:600]
            if url and (title or content):
                results.append(
                    WebSearchResult(
                        title=title or url,
                        url=url,
                        content=content,
                    )
                )
        logger.info("Tavily search returned %d results", len(results))
        return results

