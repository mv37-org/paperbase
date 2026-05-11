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
def get_job(job_id: str) -> dict:
    """Fetch the current state of an indexing or sync job."""
    return get_client().get_job(job_id)


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """Cancel a pending job or request cooperative cancellation for a running job."""
    return get_client().cancel_job(job_id)


@mcp.tool()
def paperbase_shell(command: str) -> dict:
    """Run a command in the Paperbase literature shell."""
    return get_client().paperbase_shell(command)


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
