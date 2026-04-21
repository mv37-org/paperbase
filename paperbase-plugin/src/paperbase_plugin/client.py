"""HTTP client used by the shared paperbase MCP server."""

from __future__ import annotations

import logging
from typing import Any

import requests

from .config import PluginSettings, get_settings


logger = logging.getLogger(__name__)


class PaperbaseClient:
    def __init__(self, settings: PluginSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self._settings.base_url.rstrip('/')}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self._url(path)
        logger.info(
            "Forwarding plugin request",
            extra={
                "event": "plugin.request.start",
                "method": method,
                "path": path,
                "url": url,
            },
        )
        response = self._session.request(method, url, timeout=60, **kwargs)
        response.raise_for_status()
        logger.info(
            "Completed plugin request",
            extra={
                "event": "plugin.request.completed",
                "method": method,
                "path": path,
                "status_code": response.status_code,
            },
        )
        return response.json()

    def index_paper(self, arxiv_id: str, *, force: bool = False) -> dict[str, Any]:
        return self._request("POST", f"/v1/index/papers/arxiv/{arxiv_id}", params={"force": str(force).lower()})

    def sync_latest_papers(self) -> dict[str, Any]:
        return self._request("POST", "/v1/index/sync/latest")

    def search_papers(
        self,
        *,
        query: str,
        limit: int = 20,
        category: str | None = None,
        year: str | None = None,
        include_live_sources: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "limit": limit,
            "category": category,
            "year": year,
            "include_live_sources": include_live_sources,
        }
        return self._request("POST", "/v1/search", json=payload)

    def get_results(self, result_set_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/results/{result_set_id}")

    def search_sections(
        self,
        *,
        query: str,
        limit: int = 20,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "limit": limit,
            "result_set_id": result_set_id,
            "paper_ids": paper_ids,
        }
        return self._request("POST", "/v1/search/sections", json=payload)

    def grep_results(self, result_set_id: str, *, pattern: str, limit: int = 50) -> dict[str, Any]:
        return self._request("POST", f"/v1/results/{result_set_id}/grep", json={"pattern": pattern, "limit": limit})

    def get_paper(self, paper_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}")

    def get_paper_manifest(self, paper_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/manifest")

    def list_paper_files(self, paper_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/files")

    def read_paper_file(self, paper_id: str, path: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/files/{path}")

    def get_related_papers(self, paper_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/related")

    def get_figure(self, paper_id: str, figure_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/figures/{figure_id}")

    def get_table(self, paper_id: str, table_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/papers/{paper_id}/tables/{table_id}")

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def expand_query(self, query: str, *, variants: int = 4) -> list[str]:
        """Ask the paperbase server for query variants.

        Returns the server's response verbatim on success (which may be a
        single-item list for trivially short queries — that is the server's
        intended pass-through). Returns an empty list on any failure so the
        caller can cleanly distinguish "server declined" from "server said
        don't expand this" and fall back to a heuristic only when we truly
        got no response.
        """
        try:
            payload = self._request(
                "POST",
                "/v1/query/expand",
                params={"query": query, "variants": variants},
            )
        except requests.RequestException as exc:
            logger.warning(
                "Server-side query expansion unavailable; falling back",
                extra={"event": "plugin.query_expand.failed", "error": str(exc)},
            )
            return []
        queries = payload.get("queries") if isinstance(payload, dict) else None
        if not queries:
            return []
        return list(queries)

    def get_paper_references(self, paper_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            f"/v1/papers/{paper_id}/references",
            params={"refresh": str(refresh).lower()},
        )
