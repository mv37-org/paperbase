"""HTTP client for talking to the paperbase backend."""

from __future__ import annotations

import logging
from typing import Any

import requests

from .config import ChatSettings, get_settings


logger = logging.getLogger(__name__)


class PaperbaseChatClient:
    def __init__(self, settings: ChatSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._settings.paperbase_api_key}",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self._settings.paperbase_base_url.rstrip('/')}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self._url(path)
        logger.info("paperbase-chat request", extra={"method": method, "path": path, "url": url})
        response = self._session.request(
            method,
            url,
            timeout=self._settings.request_timeout_seconds,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def search_papers(self, *, query: str, limit: int, include_live_sources: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/search",
            json={
                "query": query,
                "limit": limit,
                "include_live_sources": include_live_sources,
            },
        )

    def get_paper(self, paper_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}")

    def search_sections(self, *, query: str, limit: int, paper_ids: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/search/sections",
            json={
                "query": query,
                "limit": limit,
                "paper_ids": paper_ids,
            },
        )

    def read_paper_file(self, paper_id: str, path: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/files/{path}")
