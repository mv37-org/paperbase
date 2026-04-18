from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from paperbase_plugin import mcp_server


class StubClient:
    def search_papers(self, **kwargs):
        return {"result_set_id": "rs_test", "papers": [{"paper_id": "arxiv:1"}]}

    def index_paper(self, arxiv_id: str, *, force: bool = False):
        return {"job_id": "job_test", "arxiv_id": arxiv_id, "force": force}

    def sync_latest_papers(self):
        return {"job_id": "job_sync"}

    def get_results(self, result_set_id: str):
        return {"result_set_id": result_set_id, "papers": []}

    def grep_results(self, result_set_id: str, *, pattern: str, limit: int = 50):
        return {"result_set_id": "rs_grep", "parent_result_set_id": result_set_id, "pattern": pattern}

    def get_paper(self, paper_id: str):
        return {"paper_id": paper_id}

    def get_paper_manifest(self, paper_id: str):
        return {"paper_id": paper_id, "manifest": {"figure_count": 1}}

    def list_paper_files(self, paper_id: str):
        return {"paper_id": paper_id, "files": []}

    def read_paper_file(self, paper_id: str, path: str):
        return {"paper_id": paper_id, "path": path, "content": "hello"}

    def get_related_papers(self, paper_id: str):
        return [{"paper_id": paper_id}]

    def get_figure(self, paper_id: str, figure_id: str):
        return {"paper_id": paper_id, "figure_id": figure_id}

    def get_table(self, paper_id: str, table_id: str):
        return {"paper_id": paper_id, "table_id": table_id}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client_session(monkeypatch) -> AsyncGenerator[ClientSession]:
    monkeypatch.setattr(mcp_server, "get_client", lambda: StubClient())
    async with create_connected_server_and_client_session(mcp_server.mcp, raise_exceptions=True) as session:
        yield session


def test_manifests_reference_shared_mcp_config():
    plugin_root = Path(__file__).resolve().parents[1]
    codex_manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text())
    claude_manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())
    mcp_config = json.loads((plugin_root / ".mcp.json").read_text())

    assert codex_manifest["mcpServers"] == "./.mcp.json"
    assert claude_manifest["mcpServers"] == "./.mcp.json"
    assert "paperbase" in mcp_config["mcpServers"]


def test_main_uses_transport_from_environment(monkeypatch):
    calls = {}
    original_host = mcp_server.mcp.settings.host
    original_port = mcp_server.mcp.settings.port

    def stub_run(*, transport: str = "stdio", mount_path: str | None = None):
        calls["transport"] = transport
        calls["mount_path"] = mount_path

    monkeypatch.setenv("PAPERBASE_PLUGIN_TRANSPORT", "streamable-http")
    monkeypatch.setenv("PAPERBASE_PLUGIN_MOUNT_PATH", "/paperbase")
    monkeypatch.setenv("FASTMCP_HOST", "127.0.0.2")
    monkeypatch.setenv("FASTMCP_PORT", "8090")
    monkeypatch.setattr(mcp_server.mcp, "run", stub_run)

    mcp_server.main()

    assert calls == {
        "transport": "streamable-http",
        "mount_path": "/paperbase",
    }
    assert mcp_server.mcp.settings.host == "127.0.0.2"
    assert mcp_server.mcp.settings.port == 8090

    mcp_server.mcp.settings.host = original_host
    mcp_server.mcp.settings.port = original_port


@pytest.mark.anyio
async def test_mcp_server_exposes_tools(client_session: ClientSession):
    tools = await client_session.list_tools()
    tool_names = {tool.name for tool in tools.tools}

    assert {
        "index_paper",
        "sync_latest_papers",
        "search_papers",
        "get_results",
        "grep_results",
        "get_paper",
        "get_paper_manifest",
        "list_paper_files",
        "read_paper_file",
        "get_related_papers",
        "get_figure",
        "get_table",
    }.issubset(tool_names)

    result = await client_session.call_tool("search_papers", {"query": "transformers"})
    payload = result.structuredContent
    if payload is None:
        payload = json.loads(result.content[0].text)
    assert payload["result_set_id"] == "rs_test"
