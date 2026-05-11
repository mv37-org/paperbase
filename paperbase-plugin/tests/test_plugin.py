from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from paperbase_plugin import mcp_server


class StubClient:
    def paperbase_shell(self, command: str):
        return {"command": command, "stdout": "ok", "result_set_id": "rs_test", "papers": []}

    def search_papers(self, **kwargs):
        query = kwargs["query"].lower()
        if "agent" in query or "tool use" in query or "reasoning" in query:
            return {
                "result_set_id": "rs_agent",
                "papers": [
                    {
                        "paper_id": "arxiv:1",
                        "arxiv_id": "2401.00001",
                        "title": "Agent Paper",
                        "summary_short": "This paper studies agents that plan, act, and use tools.",
                        "citation_count": 25,
                        "published": "2024-01-01",
                        "score": 3.0,
                    }
                ],
            }
        return {"result_set_id": "rs_test", "papers": [{"paper_id": "arxiv:2", "title": "Other Paper", "score": 1.0}]}

    def index_paper(self, arxiv_id: str, *, force: bool = False):
        return {"job_id": "job_test", "arxiv_id": arxiv_id, "force": force}

    def sync_latest_papers(self):
        return {"job_id": "job_sync"}

    def get_job(self, job_id: str):
        return {"job_id": job_id, "status": "completed"}

    def cancel_job(self, job_id: str):
        return {"job_id": job_id, "status": "canceled", "effective_status": "canceled"}

    def get_results(self, result_set_id: str):
        return {"result_set_id": result_set_id, "papers": []}

    def search_sections(self, *, query: str, limit: int = 20, result_set_id: str | None = None, paper_ids: list[str] | None = None):
        if paper_ids and "arxiv:1" in paper_ids:
            return {
                "query": query,
                "sections": [
                    {
                        "paper_id": "arxiv:1",
                        "arxiv_id": "2401.00001",
                        "title": "Agent Paper",
                        "path": "sections/introduction.md",
                        "chunk_kind": "section",
                        "preview": "Agents plan, act, and use tools to interact with environments.",
                        "score": 2.5,
                    }
                ],
            }
        return {"query": query, "sections": []}

    def grep_results(self, result_set_id: str, *, pattern: str, limit: int = 50):
        return {"result_set_id": "rs_grep", "parent_result_set_id": result_set_id, "pattern": pattern}

    def get_paper(self, paper_id: str):
        return {"paper_id": paper_id}

    def get_paper_manifest(self, paper_id: str):
        return {"paper_id": paper_id, "manifest": {"figure_count": 1}}

    def list_paper_files(self, paper_id: str):
        return {"paper_id": paper_id, "files": []}

    def read_paper_file(self, paper_id: str, path: str):
        return {
            "paper_id": paper_id,
            "path": path,
            "content": "Agents plan, act, and use external tools to solve tasks in environments.",
        }

    def get_related_papers(self, paper_id: str):
        return [{"paper_id": paper_id}]

    def get_figure(self, paper_id: str, figure_id: str):
        return {"paper_id": paper_id, "figure_id": figure_id}

    def get_table(self, paper_id: str, table_id: str):
        return {"paper_id": paper_id, "table_id": table_id}

    def expand_query(self, query: str, *, variants: int = 4) -> list[str]:
        # Server-side expansion unavailable in tests; fall back to the
        # client-contract behavior of returning just the original query.
        return [query]

    def get_paper_references(self, paper_id: str, *, refresh: bool = False) -> list[dict]:
        return []


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
        "get_job",
        "cancel_job",
        "paperbase_shell",
    }.issubset(tool_names)
    assert {
        "search_papers",
        "search_sections",
        "get_results",
        "grep_results",
        "get_paper",
        "get_paper_manifest",
        "list_paper_files",
        "read_paper_file",
        "get_related_papers",
        "get_figure",
        "get_table",
        "research_qa",
    }.isdisjoint(tool_names)

    result = await client_session.call_tool("paperbase_shell", {"command": 'search "transformers" -n 20'})
    payload = result.structuredContent
    if payload is None:
        payload = json.loads(result.content[0].text)
    assert payload["result_set_id"] == "rs_test"
    assert payload["command"] == 'search "transformers" -n 20'


def test_research_qa_trusts_server_pass_through(monkeypatch):
    """Short-query server response like [question] must be treated as
    authoritative — research_qa should not fall back into the heuristic
    rewriter. Also verifies the reverse: an empty response *does* fall back.
    """
    from paperbase_plugin import research_qa as research_qa_module

    class PassThroughClient(StubClient):
        def __init__(self) -> None:
            self.expand_calls: list[str] = []

        def expand_query(self, query: str, *, variants: int = 4) -> list[str]:
            self.expand_calls.append(query)
            # Mirror the server's short-query behavior.
            return [query]

    heuristic_calls: list[str] = []
    original = research_qa_module.rewrite_queries

    def instrumented_rewrite(question: str, *, max_queries: int = 6) -> list[str]:
        heuristic_calls.append(question)
        return original(question, max_queries=max_queries)

    monkeypatch.setattr(research_qa_module, "rewrite_queries", instrumented_rewrite)

    client = PassThroughClient()
    result = research_qa_module.run_research_qa(client, question="agents")
    assert result["rewritten_queries"] == ["agents"]
    assert heuristic_calls == [], "heuristic should not run when server responded"
    assert client.expand_calls == ["agents"]

    # Empty server response *does* fall back to heuristic.
    class EmptyClient(PassThroughClient):
        def expand_query(self, query: str, *, variants: int = 4) -> list[str]:
            self.expand_calls.append(query)
            return []

    empty_client = EmptyClient()
    heuristic_calls.clear()
    research_qa_module.run_research_qa(empty_client, question="how do you build an agent?")
    assert heuristic_calls, "heuristic must run when server declines"
