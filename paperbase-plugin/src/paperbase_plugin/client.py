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
