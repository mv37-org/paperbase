"""FastMCP server that exposes hosted paperbase tools to Codex and Claude Code."""

from __future__ import annotations

from functools import lru_cache
import logging
import os

from mcp.server.fastmcp import FastMCP

from .client import PaperbaseClient
from .logging_utils import configure_logging


mcp = FastMCP("paperbase", json_response=True)
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client() -> PaperbaseClient:
    return PaperbaseClient()


@mcp.tool()
def index_paper(arxiv_id: str, force: bool = False) -> dict:
    """Enqueue indexing for a specific arXiv paper."""
    return get_client().index_paper(arxiv_id, force=force)


@mcp.tool()
def sync_latest_papers() -> dict:
    """Trigger the latest-paper sync job on the hosted paperbase service."""
    return get_client().sync_latest_papers()


@mcp.tool()
def search_papers(query: str, limit: int = 20, category: str | None = None, year: str | None = None, include_live_sources: bool = True) -> dict:
    """Hybrid-search papers and receive a result-set handle for downstream workflows."""
    return get_client().search_papers(
        query=query,
        limit=limit,
        category=category,
        year=year,
        include_live_sources=include_live_sources,
    )


@mcp.tool()
def get_results(result_set_id: str) -> dict:
    """Fetch a saved result set by ID."""
    return get_client().get_results(result_set_id)


@mcp.tool()
def grep_results(result_set_id: str, pattern: str, limit: int = 50) -> dict:
    """Narrow a saved result set with a regex-like pattern and receive a new result-set handle."""
    return get_client().grep_results(result_set_id, pattern=pattern, limit=limit)


@mcp.tool()
def get_paper(paper_id: str) -> dict:
    """Fetch paper metadata and indexing status."""
    return get_client().get_paper(paper_id)


@mcp.tool()
def get_paper_manifest(paper_id: str) -> dict:
    """Read the normalized artifact manifest for an indexed paper."""
    return get_client().get_paper_manifest(paper_id)


@mcp.tool()
def list_paper_files(paper_id: str) -> dict:
    """List extracted artifact files for an indexed paper."""
    return get_client().list_paper_files(paper_id)


@mcp.tool()
def read_paper_file(paper_id: str, path: str) -> dict:
    """Read a specific extracted artifact file by relative path."""
    return get_client().read_paper_file(paper_id, path)


@mcp.tool()
def get_related_papers(paper_id: str) -> dict:
    """Fetch Semantic Scholar related-paper recommendations for a paper."""
    return get_client().get_related_papers(paper_id)


@mcp.tool()
def get_figure(paper_id: str, figure_id: str) -> dict:
    """Fetch figure caption metadata and a signed image URL."""
    return get_client().get_figure(paper_id, figure_id)


@mcp.tool()
def get_table(paper_id: str, table_id: str) -> dict:
    """Fetch structured table rows and a signed CSV URL."""
    return get_client().get_table(paper_id, table_id)


def main() -> None:
    configure_logging(
        component="plugin",
        level=os.getenv("PAPERBASE_LOG_LEVEL", "INFO"),
        log_format=os.getenv("PAPERBASE_LOG_FORMAT", "text"),
    )
    transport = os.getenv("PAPERBASE_PLUGIN_TRANSPORT", "stdio")
    mount_path = os.getenv("PAPERBASE_PLUGIN_MOUNT_PATH")
    host = os.getenv("FASTMCP_HOST")
    port = os.getenv("FASTMCP_PORT")
    if host:
        mcp.settings.host = host
    if port:
        mcp.settings.port = int(port)
    logger.info(
        "Starting MCP bridge",
        extra={
            "event": "plugin.start",
            "transport": transport,
            "host": mcp.settings.host,
            "port": mcp.settings.port,
            "mount_path": mount_path,
        },
    )
    mcp.run(transport=transport, mount_path=mount_path)
