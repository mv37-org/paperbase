from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from paperbase.api import _resolve_bind_address, app
from paperbase.browser_code import (
    GitHubAccessibleRepo,
    GitHubCommitInfo,
    GitHubConflictFile,
    GitHubRepoBranch,
    GitHubRepoFileCandidate,
    GitHubRepoInfo,
    GitHubSyncError,
    GitHubSyncConflictError,
    ModalRunResult,
)
from paperbase.config import Settings, get_settings
from paperbase.extractors import ExtractionOutput, FigureArtifact, RawArtifact, SectionArtifact, TableArtifact
from paperbase.llm import (
    ChatModelStreamEvent,
    ChatModelUsage,
    Prompt,
    ResponseEnvelope,
    ResponseEvent,
    ResponseItem,
)
from paperbase.models import (
    ChatCitation,
    ChatThreadItemResponse,
    CreatePaperCommentRequest,
    CreateChatThreadRequest,
    CreateProjectGitHubRepoRequest,
    CreateProjectRequest,
    MoveChatThreadProjectRequest,
    PaperMetadata,
    ProjectRepoSyncRequest,
    SaveAnthropicKeyRequest,
    SaveModalCredentialsRequest,
    SaveOpenAIKeyRequest,
    SearchRequest,
    SearchSectionRequest,
    SendChatMessageRequest,
    SignupRequest,
    UpdateAppearanceRequest,
    UpdateNotificationsRequest,
    UpdateProjectRequest,
)
from paperbase.repository import MemoryPaperRepository
from paperbase.service import PaperbaseService, _sanitize_inline_citations, get_service
from paperbase.storage import MemoryObjectStore


class FakeEmailSender:
    def __init__(self) -> None:
        self.reset_emails: list[tuple[str, str]] = []

    def send_password_reset(self, *, email: str, reset_link: str) -> None:
        self.reset_emails.append((email, reset_link))


class FakeChatModel:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.stream_chunks: list[str] = ["Grounded ", "answer with citations [S1]"]
        self.fail_stream_after_chunks: int | None = None
        self.tool_round_usage = ChatModelUsage(input_tokens=84512, output_tokens=98)
        self.initial_stream_usage = ChatModelUsage(input_tokens=11234, output_tokens=1)
        self.final_stream_usage = ChatModelUsage(input_tokens=11234, output_tokens=27)
        self.count_calls: list[dict[str, object]] = []
        self.token_counts: list[int | None] = []
        self.compaction_summary = "## Compacted Conversation\n\nContinue from this compacted context."

    def count_message_tokens(
        self,
        *,
        api_key: str,
        messages,
        system_prompt: str,
        tools=None,
        tool_choice=None,
        parallel_tool_calls=None,
    ):
        self.count_calls.append(
            {
                "api_key": api_key,
                "messages": messages,
                "system_prompt": system_prompt,
                "tools": tools,
                "tool_choice": tool_choice,
                "parallel_tool_calls": parallel_tool_calls,
            }
        )
        if self.token_counts:
            return self.token_counts.pop(0)
        return 1

    def _legacy_messages_from_prompt(self, prompt: Prompt) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        for item in prompt.input:
            item_type = str(item.get("type") or "")
            if item_type == "message":
                content = item.get("content", "")
                if isinstance(content, list):
                    content_text = "\n".join(
                        str(block.get("text") or "")
                        for block in content
                        if isinstance(block, dict)
                    )
                else:
                    content_text = str(content or "")
                messages.append({"role": str(item.get("role") or "user"), "content": content_text})
                continue
            if item_type == "function_call":
                arguments = str(item.get("arguments") or "").strip()
                try:
                    parsed_arguments = json.loads(arguments) if arguments else {}
                except json.JSONDecodeError:
                    parsed_arguments = {}
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": str(item.get("id") or item.get("call_id") or "toolu_1"),
                                "name": str(item.get("name") or ""),
                                "input": parsed_arguments,
                            }
                        ],
                    }
                )
                continue
            if item_type == "function_call_output":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": str(item.get("call_id") or ""),
                                "content": str(item.get("output") or ""),
                            }
                        ],
                    }
                )
                continue
            messages.append({"role": "user", "content": json.dumps(item)})
        return messages

    def _legacy_kwargs_from_prompt(self, *, api_key: str, prompt: Prompt) -> dict[str, object]:
        tool_choice = prompt.tool_choice
        if tool_choice == "auto":
            tool_choice = {"type": "auto"}
        elif tool_choice == "required":
            tool_choice = {"type": "any"}
        return {
            "api_key": api_key,
            "messages": self._legacy_messages_from_prompt(prompt),
            "system_prompt": prompt.base_instructions,
            "tools": prompt.tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": prompt.parallel_tool_calls,
        }

    def _call_legacy(self, method_name: str, **kwargs):
        method = getattr(self, method_name)
        try:
            return method(**kwargs)
        except TypeError:
            kwargs.pop("parallel_tool_calls", None)
            return method(**kwargs)

    def count_prompt_tokens(self, *, api_key: str, prompt: Prompt) -> int | None:
        return self._call_legacy(
            "count_message_tokens",
            **self._legacy_kwargs_from_prompt(api_key=api_key, prompt=prompt),
        )

    def _response_items_from_legacy_response(
        self,
        response: dict[str, object],
        *,
        default_assistant_item_id: str = "msg_stream",
        default_assistant_text: str = "",
    ) -> list[ResponseItem]:
        items: list[ResponseItem] = []
        content_blocks = response.get("content")
        if not isinstance(content_blocks, list):
            return items
        for index, block in enumerate(content_blocks):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                text = str(block.get("text") or default_assistant_text)
                items.append(
                    ResponseItem(
                        item_id=default_assistant_item_id,
                        item_type="assistant_message",
                        role="assistant",
                        status="completed",
                        content_text=text,
                        raw=block,
                    )
                )
                continue
            if block_type == "tool_use":
                tool_id = str(block.get("id") or f"toolu_{index + 1}")
                items.append(
                    ResponseItem(
                        item_id=tool_id,
                        item_type="function_call",
                        role="assistant",
                        status="completed",
                        call_id=tool_id,
                        name=str(block.get("name") or ""),
                        content_json=block.get("input") or {},
                        raw=block,
                    )
                )
        return items

    def create_response(self, *, api_key: str, prompt: Prompt) -> ResponseEnvelope:
        response = self._call_legacy(
            "create_message",
            **self._legacy_kwargs_from_prompt(api_key=api_key, prompt=prompt),
        )
        if not isinstance(response, dict):
            raise TypeError("Fake chat model expected create_message to return a dict")
        usage = response.get("usage")
        if isinstance(usage, dict):
            chat_usage = ChatModelUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0)
                or int(usage.get("input_tokens") or 0)
                + int(usage.get("output_tokens") or 0),
            )
        else:
            chat_usage = None
        return ResponseEnvelope(
            response_id="resp_fake_create",
            items=self._response_items_from_legacy_response(response),
            usage=chat_usage,
            raw=response,
        )

    def create_message(
        self,
        *,
        api_key: str,
        messages,
        system_prompt: str,
        tools=None,
        tool_choice=None,
        parallel_tool_calls=None,
    ):
        self.create_calls.append(
            {
                "api_key": api_key,
                "messages": messages,
                "system_prompt": system_prompt,
                "tools": tools,
                "tool_choice": tool_choice,
                "parallel_tool_calls": parallel_tool_calls,
            }
        )
        if system_prompt == "" and messages and "Compact the conversation transcript" in str(messages[-1]):
            return {
                "content": [{"type": "text", "text": self.compaction_summary}],
                "stop_reason": "end_turn",
            }
        if tools:
            tool_names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tool_choice == {"type": "any"} or not has_tool_results:
                raw_query = next(
                    (
                        str(message.get("content"))
                        for message in reversed(messages)
                        if isinstance(message, dict)
                        and message.get("role") == "user"
                        and isinstance(message.get("content"), str)
                    ),
                    "grounding",
                )
                query_words = [
                    token.strip(".,!?():;").lower() for token in raw_query.split() if len(token.strip(".,!?():;")) > 3
                ]
                if any(word.startswith("ground") for word in query_words):
                    query = "grounding"
                elif "methods" in query_words:
                    query = "methods"
                else:
                    query = " ".join(query_words[:2]) or raw_query
                tool_name = (
                    "paperbase_search_sections"
                    if "paperbase_search_sections" in tool_names
                    else "paperbase_search"
                    if "paperbase_search" in tool_names
                    else str(tool_names[0])
                )
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_search_sections",
                            "name": tool_name,
                            "input": {"query": query, "limit": 3},
                        }
                    ],
                    "usage": {
                        "input_tokens": self.tool_round_usage.input_tokens,
                        "output_tokens": self.tool_round_usage.output_tokens,
                        "cache_creation_input_tokens": self.tool_round_usage.cache_creation_input_tokens,
                        "cache_read_input_tokens": self.tool_round_usage.cache_read_input_tokens,
                    },
                    "stop_reason": "tool_use",
                }
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Grounded answer with citations [S1]\n\nAPI key used: {api_key[:6]}",
                }
            ],
            "usage": {
                "input_tokens": self.final_stream_usage.input_tokens,
                "output_tokens": self.final_stream_usage.output_tokens,
                "cache_creation_input_tokens": self.final_stream_usage.cache_creation_input_tokens,
                "cache_read_input_tokens": self.final_stream_usage.cache_read_input_tokens,
            },
            "stop_reason": "end_turn",
        }

    def stream_message(
        self,
        *,
        api_key: str,
        messages,
        system_prompt: str,
        tools=None,
        tool_choice=None,
        parallel_tool_calls=None,
    ):
        self.stream_calls.append(
            {
                "api_key": api_key,
                "messages": messages,
                "system_prompt": system_prompt,
                "tools": tools,
                "tool_choice": tool_choice,
                "parallel_tool_calls": parallel_tool_calls,
            }
        )
        if tools:
            tool_names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if not has_tool_results:
                query = "grounding"
                tool_name = (
                    "paperbase_search_sections"
                    if "paperbase_search_sections" in tool_names
                    else "paperbase_search"
                    if "paperbase_search" in tool_names
                    else str(tool_names[0])
                )
                yield ChatModelStreamEvent(usage=self.tool_round_usage)
                yield ChatModelStreamEvent(
                    response={
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_search_sections",
                                "name": tool_name,
                                "input": {"query": query, "limit": 3},
                            }
                        ],
                        "usage": {
                            "input_tokens": self.tool_round_usage.input_tokens,
                            "output_tokens": self.tool_round_usage.output_tokens,
                            "cache_creation_input_tokens": self.tool_round_usage.cache_creation_input_tokens,
                            "cache_read_input_tokens": self.tool_round_usage.cache_read_input_tokens,
                        },
                        "stop_reason": "tool_use",
                    }
                )
                return
        yield ChatModelStreamEvent(usage=self.initial_stream_usage)
        for index, chunk in enumerate(self.stream_chunks):
            if self.fail_stream_after_chunks is not None and index >= self.fail_stream_after_chunks:
                raise RuntimeError("stream blew up")
            yield ChatModelStreamEvent(text=chunk)
        yield ChatModelStreamEvent(usage=self.final_stream_usage)
        yield ChatModelStreamEvent(
            response={
                "content": [{"type": "text", "text": "".join(self.stream_chunks)}],
                "usage": {
                    "input_tokens": self.final_stream_usage.input_tokens,
                    "output_tokens": self.final_stream_usage.output_tokens,
                    "cache_creation_input_tokens": self.final_stream_usage.cache_creation_input_tokens,
                    "cache_read_input_tokens": self.final_stream_usage.cache_read_input_tokens,
                },
                "stop_reason": "end_turn",
            }
        )

    def stream_response(self, *, api_key: str, prompt: Prompt):
        assistant_item_id = "msg_stream"
        response_id = "resp_fake_stream"
        assistant_started = False
        assistant_text = ""
        legacy_kwargs = self._legacy_kwargs_from_prompt(api_key=api_key, prompt=prompt)
        if self.__class__.stream_message is FakeChatModel.stream_message:
            legacy_events = [ChatModelStreamEvent(response=self._call_legacy("create_message", **legacy_kwargs))]
        else:
            def _stream_with_fallback():
                try:
                    yield from self._call_legacy("stream_message", **legacy_kwargs)
                except AssertionError:
                    yield ChatModelStreamEvent(response=self._call_legacy("create_message", **legacy_kwargs))

            legacy_events = _stream_with_fallback()

        for legacy_event in legacy_events:
            if legacy_event.usage is not None:
                yield ResponseEvent(
                    type="response.completed",
                    response_id=response_id,
                    usage=legacy_event.usage,
                )
            if legacy_event.text:
                if not assistant_started:
                    assistant_started = True
                    yield ResponseEvent(
                        type="response.output_item.added",
                        response_id=response_id,
                        item=ResponseItem(
                            item_id=assistant_item_id,
                            item_type="assistant_message",
                            role="assistant",
                            status="in_progress",
                        ),
                    )
                assistant_text += legacy_event.text
                yield ResponseEvent(
                    type="response.output_text.delta",
                    response_id=response_id,
                    item_id=assistant_item_id,
                    delta=legacy_event.text,
                )
            if legacy_event.response:
                response = legacy_event.response
                items = self._response_items_from_legacy_response(
                    response,
                    default_assistant_item_id=assistant_item_id,
                    default_assistant_text=assistant_text,
                )
                for item in items:
                    if item.item_type != "assistant_message" or not assistant_started:
                        yield ResponseEvent(
                            type="response.output_item.added",
                            response_id=response_id,
                            item=ResponseItem(
                                item_id=item.item_id,
                                item_type=item.item_type,
                                role=item.role,
                                status="in_progress" if item.item_type == "assistant_message" else item.status,
                                call_id=item.call_id,
                                name=item.name,
                                content_json=item.content_json,
                            ),
                        )
                    yield ResponseEvent(
                        type="response.output_item.done",
                        response_id=response_id,
                        item=item,
                    )
                usage = response.get("usage")
                if isinstance(usage, dict):
                    yield ResponseEvent(
                        type="response.completed",
                        response_id=response_id,
                        usage=ChatModelUsage(
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            total_tokens=int(usage.get("total_tokens") or 0)
                            or int(usage.get("input_tokens") or 0)
                            + int(usage.get("output_tokens") or 0),
                        ),
                        raw=response,
                    )


class FakeGitHubRepoManager:
    def __init__(self) -> None:
        self.ensure_repo_calls: list[dict[str, object]] = []
        self.create_repo_calls: list[dict[str, object]] = []
        self.get_repo_calls: list[dict[str, object]] = []
        self.list_repositories_calls: list[dict[str, object]] = []
        self.list_branches_calls: list[dict[str, object]] = []
        self.get_branch_head_calls: list[dict[str, object]] = []
        self.list_importable_files_calls: list[dict[str, object]] = []
        self.get_file_text_calls: list[dict[str, object]] = []
        self.sync_project_files_calls: list[dict[str, object]] = []
        self.next_sync_conflict: GitHubSyncConflictError | None = None
        self.repo_info = GitHubRepoInfo(
            repo_name="existing-repo",
            repo_url="https://github.com/octocat/existing-repo",
            default_branch="main",
            owner_login="octocat",
        )
        self.branches = [
            GitHubRepoBranch(name="main", commit_sha="main-sha"),
            GitHubRepoBranch(name="dev", commit_sha="dev-sha"),
        ]
        self.branch_heads: dict[str, str] = {"main": "main-sha", "dev": "dev-sha"}
        self.importable_files = [
            GitHubRepoFileCandidate(path="src/analysis.py", file_kind="python", size=13),
            GitHubRepoFileCandidate(path="notebooks/demo.ipynb", file_kind="notebook", size=320),
        ]
        self.accessible_repos = [
            GitHubAccessibleRepo(
                owner_login="octocat",
                repo_name="existing-repo",
                full_name="octocat/existing-repo",
                repo_url="https://github.com/octocat/existing-repo",
                default_branch="main",
                private=True,
            ),
            GitHubAccessibleRepo(
                owner_login="paperbase-labs",
                repo_name="shared-analysis",
                full_name="paperbase-labs/shared-analysis",
                repo_url="https://github.com/paperbase-labs/shared-analysis",
                default_branch="dev",
                private=False,
            ),
        ]
        self.file_texts: dict[tuple[str, str], str] = {
            ("src/analysis.py", "main"): "print('remote')\n",
        }

    def ensure_repo(self, **kwargs) -> GitHubRepoInfo:
        self.ensure_repo_calls.append(kwargs)
        return GitHubRepoInfo(
            repo_name="paperbase-general-123abc",
            repo_url="https://github.com/octocat/paperbase-general-123abc",
            default_branch="main",
            owner_login="octocat",
        )

    def create_repo(self, **kwargs) -> GitHubRepoInfo:
        self.create_repo_calls.append(kwargs)
        repo_name = str(kwargs["repo_name"])
        owner_login = str(kwargs.get("owner_login") or "octocat")
        return GitHubRepoInfo(
            repo_name=repo_name,
            repo_url=f"https://github.com/{owner_login}/{repo_name}",
            default_branch="main",
            owner_login=owner_login,
        )

    def get_repo(self, **kwargs) -> GitHubRepoInfo:
        self.get_repo_calls.append(kwargs)
        owner_login = str(kwargs.get("owner_login") or self.repo_info.owner_login)
        repo_name = str(kwargs.get("repo_name") or self.repo_info.repo_name)
        return GitHubRepoInfo(
            repo_name=repo_name,
            repo_url=f"https://github.com/{owner_login}/{repo_name}",
            default_branch=self.repo_info.default_branch,
            owner_login=owner_login,
        )

    def list_repositories(self, **kwargs) -> list[GitHubAccessibleRepo]:
        self.list_repositories_calls.append(kwargs)
        return list(self.accessible_repos)

    def list_branches(self, **kwargs) -> list[GitHubRepoBranch]:
        self.list_branches_calls.append(kwargs)
        return list(self.branches)

    def get_branch_head(self, **kwargs) -> str:
        self.get_branch_head_calls.append(kwargs)
        branch = str(kwargs.get("branch") or "main")
        return self.branch_heads[branch]

    def list_importable_files(self, **kwargs) -> list[GitHubRepoFileCandidate]:
        self.list_importable_files_calls.append(kwargs)
        return list(self.importable_files)

    def get_file_text(self, **kwargs) -> str:
        self.get_file_text_calls.append(kwargs)
        path = str(kwargs["path"])
        ref = str(kwargs.get("ref") or "main")
        try:
            return self.file_texts[(path, ref)]
        except KeyError as exc:
            raise GitHubSyncError(f"{path} not found") from exc

    def sync_project_files(self, **kwargs) -> GitHubCommitInfo:
        self.sync_project_files_calls.append(kwargs)
        if self.next_sync_conflict is not None:
            conflict = self.next_sync_conflict
            self.next_sync_conflict = None
            raise conflict
        return GitHubCommitInfo(
            repo_name="paperbase-general-123abc",
            repo_url="https://github.com/octocat/paperbase-general-123abc",
            default_branch="main",
            commit_sha="abc123",
            commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        )


class FakeModalRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, *, token_id: str, token_secret: str, code: str) -> ModalRunResult:
        self.calls.append({"token_id": token_id, "token_secret": token_secret, "code": code})
        started = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)
        finished = datetime(2026, 4, 22, 0, 0, 1, tzinfo=timezone.utc)
        return ModalRunResult(
            status="completed",
            stdout="runner output",
            stderr="",
            exit_code=0,
            started_at=started,
            finished_at=finished,
        )

    def run_project(
        self,
        *,
        token_id: str,
        token_secret: str,
        files: dict[str, str],
        entrypoint_path: str,
        run_mode: str,
    ) -> ModalRunResult:
        self.calls.append(
            {
                "token_id": token_id,
                "token_secret": token_secret,
                "files": files,
                "entrypoint_path": entrypoint_path,
                "run_mode": run_mode,
            }
        )
        started = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)
        finished = datetime(2026, 4, 22, 0, 0, 1, tzinfo=timezone.utc)
        return ModalRunResult(
            status="completed",
            stdout="runner output",
            stderr="",
            exit_code=0,
            started_at=started,
            finished_at=finished,
        )


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        event_name = ""
        payload = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
        if event_name and payload:
            events.append((event_name, json.loads(payload)))
    return events


def _event_items(
    events: list[tuple[str, dict]],
    *,
    event_name: str,
    item_type: str | None = None,
    phase: str | None = None,
) -> list[dict]:
    matches: list[dict] = []
    for current_name, payload in events:
        if current_name != event_name:
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        if item_type is not None and item.get("item_type") != item_type:
            continue
        if phase is not None and item.get("phase") != phase:
            continue
        matches.append(item)
    return matches


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().api_key}"}


def _connect_github(service: PaperbaseService, user) -> None:
    service.save_github_connection(
        user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )


def _save_browser_openai_key(service: PaperbaseService, user, api_key: str = "openai-user-key-999") -> None:
    service.save_user_openai_key(user, SaveOpenAIKeyRequest(api_key=api_key))


def _create_repo_project_request(
    name: str,
    description: str,
    *,
    repo_name: str | None = None,
) -> CreateProjectRequest:
    return CreateProjectRequest(
        name=name,
        description=description,
        github_repo=CreateProjectGitHubRepoRequest(
            mode="create",
            name=repo_name or name.lower().replace(" ", "-"),
            base_branch="main",
        ),
    )


def _notebook_json(source: str = "print('notebook')\n") -> str:
    return json.dumps(
        {
            "cells": [
                {
                    "id": "cell-1",
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": source.splitlines(keepends=True),
                }
            ],
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


@pytest.fixture
def service() -> PaperbaseService:
    settings = Settings(
        storage_backend="memory",
        database_url="memory://",
        api_key="test-token",
        summary_provider="heuristic",
    )
    return PaperbaseService(
        settings=settings,
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )


@pytest.fixture
def browser_service() -> tuple[PaperbaseService, FakeEmailSender, FakeChatModel]:
    email_sender = FakeEmailSender()
    chat_model = FakeChatModel()
    settings = Settings(
        storage_backend="memory",
        database_url="memory://",
        api_key="test-token",
        anthropic_api_key="product-key-123",
    )
    service = PaperbaseService(
        settings=settings,
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=email_sender,
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    return service, email_sender, chat_model


def _sample_paper(arxiv_id: str, *, title: str, abstract: str) -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors="Alice, Bob",
        abstract=abstract,
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-10",
        updated="2024-01-11",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        html_url=f"https://arxiv.org/html/{arxiv_id}",
    )


def _rich_extraction(*, text: str) -> ExtractionOutput:
    return ExtractionOutput(
        source_type="html",
        text_markdown=text,
        sections=[SectionArtifact(title="Introduction", slug="introduction", markdown=text)],
        figures=[
            FigureArtifact(
                figure_id="figure-1",
                caption="A model architecture.",
                file_extension="png",
                content_type="image/png",
                image_bytes=b"PNG",
                source_url="https://arxiv.org/html/figure-1.png",
            )
        ],
        tables=[
            TableArtifact(
                table_id="table-1",
                caption="Benchmark scores",
                rows=[["Model", "Score"], ["Paperbase", "1.0"]],
            )
        ],
        raw_artifacts=[RawArtifact(path="raw/source.tar.gz", data=b"TAR", content_type="application/gzip")],
    )


def _full_text_extraction(*, text: str) -> ExtractionOutput:
    return ExtractionOutput(
        source_type="html",
        text_markdown=text,
        sections=[SectionArtifact(title="Full Text", slug="full-text", markdown=text)],
        raw_artifacts=[RawArtifact(path="raw/source.tar.gz", data=b"TAR", content_type="application/gzip")],
    )


def test_index_writes_manifest_and_artifacts(monkeypatch, service: PaperbaseService):
    paper = _sample_paper("2401.12345", title="Transformer Paper", abstract="Attention and transformers.")
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="Attention layers")
    )

    indexed = service.index_arxiv_paper(paper.arxiv_id, paper=paper)

    assert indexed.paper_id == "arxiv:2401.12345"
    assert indexed.summary_short
    assert indexed.summary_eli5.startswith("In simple terms,")
    manifest = service.get_manifest(indexed.paper_id).manifest
    assert manifest["source_type"] == "html"
    assert manifest["figure_count"] == 1
    assert manifest["table_count"] == 1

    files = {item.path for item in service.list_files(indexed.paper_id).files}
    assert "manifest.json" in files
    assert "meta.json" in files
    assert "text/full.md" in files
    assert "sections/introduction.md" in files
    assert "figures/index.json" in files
    assert "figures/figure-1/caption.md" in files
    assert "figures/figure-1/image.png" in files
    assert "tables/index.json" in files
    assert "tables/table-1.json" in files
    assert "tables/table-1.csv" in files
    assert "raw/source.tar.gz" in files


def test_index_job_tracks_papers_for_admin_overview(monkeypatch, service: PaperbaseService):
    paper = _sample_paper("2401.77777", title="Tracked Paper", abstract="Tracked abstract.")
    monkeypatch.setattr("paperbase.service.sources.get_paper_details", lambda arxiv_id: paper)
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda current_paper: _rich_extraction(text="Tracked text"),
    )

    job = service.enqueue_index_job(paper.arxiv_id)
    completed = service.process_next_job()

    assert completed is not None
    assert completed.job_id == job.job_id
    assert completed.root_job_id == job.job_id

    overview = service.get_admin_jobs_overview(limit=10)
    tracked_row = next(row for row in overview.jobs if row.job_id == job.job_id)
    assert tracked_row.indexed_paper_count == 1
    assert tracked_row.paper_outcome_counts == {"indexed": 1}
    assert tracked_row.tracking_available is True

    job_papers = service.get_admin_job_papers(job.job_id)
    assert len(job_papers.papers) == 1
    assert job_papers.total_count == 1
    assert job_papers.offset == 0
    assert job_papers.limit == 100
    assert job_papers.has_more is False
    assert job_papers.papers[0].paper_id == "arxiv:2401.77777"
    assert job_papers.papers[0].outcome == "indexed"
    assert job_papers.outcome_counts == {"indexed": 1}


def test_index_job_records_skip_reason_for_already_indexed_paper(monkeypatch, service: PaperbaseService):
    paper = _sample_paper("2401.77778", title="Already Indexed Paper", abstract="Tracked abstract.")
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda current_paper: _rich_extraction(text="Tracked text"),
    )
    service.index_arxiv_paper(paper.arxiv_id, paper=paper)

    job = service.enqueue_index_job(paper.arxiv_id)
    completed = service.process_next_job()

    assert completed is not None
    job_papers = service.get_admin_job_papers(job.job_id)
    assert job_papers.outcome_counts == {"skipped": 1}
    assert job_papers.skipped_reason_counts == {"already_indexed": 1}
    assert job_papers.papers[0].outcome == "skipped"
    assert job_papers.papers[0].skip_reason == "already_indexed"


def test_skipped_job_papers_require_skip_reason(service: PaperbaseService):
    with pytest.raises(ValueError, match="skip_reason is required"):
        service.repository.record_job_paper(
            "job_missing_reason",
            "arxiv:2401.77779",
            outcome="skipped",
            arxiv_id="2401.77779",
            title="Skipped Without Reason",
        )


def test_admin_jobs_overview_counts_tracked_paper_outcomes(service: PaperbaseService):
    job = service.repository.create_job(
        "harvest_range",
        {
            "harvest_id": "harvest-test",
            "categories": ["cs.LG"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    )
    service.repository.jobs[job.job_id] = job.model_copy(update={"status": "completed"})
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.00001",
        outcome="indexed",
        arxiv_id="2401.00001",
        title="Indexed Paper",
    )
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.00002",
        outcome="skipped",
        arxiv_id="2401.00002",
        title="Skipped Paper",
        skip_reason="already_indexed",
    )
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.00003",
        outcome="failed",
        arxiv_id="2401.00003",
        title="Failed Paper",
    )

    overview = service.get_admin_jobs_overview(limit=10)
    row = next(row for row in overview.jobs if row.job_id == job.job_id)

    assert row.tracking_available is True
    assert row.indexed_paper_count == 1
    assert row.paper_outcome_counts == {"failed": 1, "indexed": 1, "skipped": 1}

    job_papers = service.get_admin_job_papers(job.job_id)
    skipped = next(paper for paper in job_papers.papers if paper.outcome == "skipped")
    assert skipped.skip_reason == "already_indexed"
    assert job_papers.outcome_counts == {"failed": 1, "indexed": 1, "skipped": 1}
    assert job_papers.skipped_reason_counts == {"already_indexed": 1}


def test_summarize_top_cited_admin_row_does_not_claim_index_tracking(service: PaperbaseService):
    job = service.enqueue_summarize_top_cited_job(limit=25)

    overview = service.get_admin_jobs_overview(limit=10)
    row = next(row for row in overview.jobs if row.job_id == job.job_id)

    assert row.job_type == "summarize_top_cited"
    assert row.tracking_available is False
    assert row.indexed_paper_count is None


def test_latest_sync_schedule_uses_latest_run_tracking_for_legacy_rootless_jobs():
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            worker_auto_sync_latest=True,
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    job = service.repository.create_job(
        "sync_latest",
        {
            "days": service.settings.latest_sync_days,
            "categories": service.settings.arxiv_categories,
            "limit_per_category": service.settings.latest_sync_limit_per_category,
        },
    )
    service.repository.jobs[job.job_id] = job.model_copy(
        update={
            "root_job_id": None,
            "status": "completed",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.88888",
        outcome="indexed",
        arxiv_id="2401.88888",
        title="Legacy Sync Paper",
    )

    overview = service.get_admin_jobs_overview(limit=10)
    schedule_row = next(row for row in overview.jobs if row.job_type == "latest_sync_schedule")

    assert schedule_row.root_job_id == job.job_id
    assert schedule_row.tracking_available is True
    assert schedule_row.indexed_paper_count == 1
    assert schedule_row.paper_outcome_counts == {"indexed": 1}

    job_papers = service.get_admin_job_papers(schedule_row.root_job_id)
    assert [paper.paper_id for paper in job_papers.papers] == ["arxiv:2401.88888"]


def test_admin_job_papers_response_is_paginated(service: PaperbaseService):
    job = service.repository.create_job("harvest_range", {"harvest_id": "harvest-page"})
    service.repository.jobs[job.job_id] = job.model_copy(update={"status": "completed"})
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.00001",
        outcome="indexed",
        arxiv_id="2401.00001",
        title="Paper One",
    )
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.00002",
        outcome="indexed",
        arxiv_id="2401.00002",
        title="Paper Two",
    )
    service.repository.record_job_paper(
        job.job_id,
        "arxiv:2401.00003",
        outcome="indexed",
        arxiv_id="2401.00003",
        title="Paper Three",
    )
    now = datetime.now(timezone.utc)
    service.repository.job_papers[(job.job_id, "arxiv:2401.00001")] = service.repository.job_papers[
        (job.job_id, "arxiv:2401.00001")
    ].model_copy(update={"updated_at": now.replace(microsecond=1)})
    service.repository.job_papers[(job.job_id, "arxiv:2401.00002")] = service.repository.job_papers[
        (job.job_id, "arxiv:2401.00002")
    ].model_copy(update={"updated_at": now.replace(microsecond=2)})
    service.repository.job_papers[(job.job_id, "arxiv:2401.00003")] = service.repository.job_papers[
        (job.job_id, "arxiv:2401.00003")
    ].model_copy(update={"updated_at": now.replace(microsecond=3)})

    page = service.get_admin_job_papers(job.job_id, offset=1, limit=1)

    assert page.root_job_id == job.job_id
    assert page.total_count == 3
    assert page.offset == 1
    assert page.limit == 1
    assert page.has_more is True
    assert [paper.paper_id for paper in page.papers] == ["arxiv:2401.00002"]


def test_reindex_replaces_stale_artifacts(monkeypatch, service: PaperbaseService):
    paper = _sample_paper("2401.33333", title="Transformer Paper", abstract="Attention and transformers.")
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)

    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _full_text_extraction(text="Old extraction")
    )
    service.index_arxiv_paper(paper.arxiv_id, paper=paper, force=True)

    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="New extraction")
    )
    service.index_arxiv_paper(paper.arxiv_id, paper=paper, force=True)

    files = {item.path for item in service.list_files("arxiv:2401.33333").files}

    assert "sections/introduction.md" in files
    assert "sections/full-text.md" not in files


def test_search_and_grep_return_result_handles(monkeypatch, service: PaperbaseService):
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(
            text=(
                "Transformer Study mentions attention and transformers"
                if "Transformer" in paper.title
                else "Vision Study mentions diffusion only"
            )
        ),
    )

    service.index_arxiv_paper(
        "2401.10001", paper=_sample_paper("2401.10001", title="Transformer Study", abstract="Transformers")
    )
    service.index_arxiv_paper(
        "2401.10002", paper=_sample_paper("2401.10002", title="Vision Study", abstract="Diffusion")
    )

    results = service.search(SearchRequest(query="transformer", include_live_sources=False))
    assert results.result_set_id.startswith("rs_")
    assert len(results.papers) == 1

    narrowed = service.grep_results(results.result_set_id, "attention", limit=10)
    assert narrowed.result_set_id != results.result_set_id
    assert narrowed.parent_result_set_id == results.result_set_id
    assert narrowed.papers[0].paper_id == "arxiv:2401.10001"

    section_hits = service.search_sections(
        SearchSectionRequest(query="attention", result_set_id=results.result_set_id, limit=10)
    )
    assert section_hits.sections
    assert section_hits.sections[0].paper_id == "arxiv:2401.10001"
    assert section_hits.sections[0].path == "sections/introduction.md"


def test_search_can_shortlist_from_generated_summaries(monkeypatch, service: PaperbaseService):
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="General workflow discussion")
    )

    summaries = {
        "2401.20001": (
            "This paper studies AI agents that use tools, plan, and act in environments.",
            "In simple terms, this paper explains how an AI agent can plan and use tools.",
        ),
        "2401.20002": (
            "This paper studies image generation systems.",
            "In simple terms, this paper explains diffusion for image generation.",
        ),
    }
    monkeypatch.setattr(
        "paperbase.service.build_paper_summaries",
        lambda paper, extraction, settings=None: summaries[paper.arxiv_id],
    )

    service.index_arxiv_paper(
        "2401.20001", paper=_sample_paper("2401.20001", title="Tool Use Study", abstract="Planning systems")
    )
    service.index_arxiv_paper(
        "2401.20002", paper=_sample_paper("2401.20002", title="Image Study", abstract="Vision systems")
    )

    results = service.search(SearchRequest(query="agent", include_live_sources=False))

    assert results.papers
    assert results.papers[0].paper_id == "arxiv:2401.20001"
    assert "AI agents" in results.papers[0].summary_short


def test_summarize_top_cited_uses_stable_paging(monkeypatch):
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            summary_provider="vertex",
            featured_tier_concurrency=1,
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    monkeypatch.setattr(
        "paperbase.summaries.generate_summary_on_demand",
        lambda paper, settings: (f"Short {paper.arxiv_id}", f"ELI5 {paper.arxiv_id}"),
    )

    for index in range(70):
        paper = _sample_paper(
            f"2401.{10000 + index}",
            title=f"Paper {index}",
            abstract=f"Abstract {index}.",
        )
        paper.index_status = "indexed"
        paper.citation_count = 70 - index
        paper.influential_citation_count = 70 - index
        service.repository.upsert_paper(paper)

    result = service.summarize_top_cited(limit=70)

    assert result == {"processed": 70, "updated": 70, "failed": 0}
    assert all(paper.summary_eli5.startswith("ELI5 ") for paper in service.repository.papers.values())


def test_api_flow_for_jobs_and_results(monkeypatch, service: PaperbaseService):
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    headers = _auth_headers()
    paper = _sample_paper("2401.55555", title="Queued Paper", abstract="Queued indexing")

    monkeypatch.setattr("paperbase.service.sources.get_paper_details", lambda arxiv_id: paper)
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="Queued attention result")
    )

    job = client.post("/v1/index/papers/arxiv/2401.55555", headers=headers).json()
    assert job["status"] == "pending"

    service.process_next_job()

    manifest = client.get("/v1/papers/arxiv:2401.55555/manifest", headers=headers).json()
    assert manifest["manifest"]["index_status"] == "indexed"

    search = client.post(
        "/v1/search",
        headers=headers,
        json={"query": "queued", "limit": 10, "include_live_sources": False},
    ).json()
    section_search = client.post(
        "/v1/search/sections",
        headers=headers,
        json={"query": "attention", "limit": 10, "paper_ids": ["arxiv:2401.55555"]},
    ).json()
    grep = client.post(
        f"/v1/results/{search['result_set_id']}/grep",
        headers=headers,
        json={"pattern": "attention", "limit": 10},
    ).json()
    figure = client.get("/v1/papers/arxiv:2401.55555/figures/figure-1", headers=headers).json()
    table = client.get("/v1/papers/arxiv:2401.55555/tables/table-1", headers=headers).json()

    assert grep["parent_result_set_id"] == search["result_set_id"]
    assert section_search["sections"][0]["paper_id"] == "arxiv:2401.55555"
    assert figure["figure_id"] == "figure-1"
    assert table["table_id"] == "table-1"

    app.dependency_overrides.clear()


def test_root_serves_built_chat_shell():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Paperbase | MV37" in response.text
    assert 'id="root"' in response.text
    assert 'href="/static/favicon.png"' in response.text
    assert "/static/app.js" in response.text
    assert "/static/app.css" in response.text
    assert "__PAPERBASE_BASE_URL__" not in response.text


def test_root_uses_forwarded_https_base_url():
    client = TestClient(app)

    response = client.get(
        "/",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "paperbase-api.example.test",
        },
    )

    assert response.status_code == 200
    assert 'window.PAPERBASE_BASE_URL = "https://paperbase-api.example.test";' in response.text


def test_static_chat_assets_are_served():
    client = TestClient(app)

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]

    stylesheet = client.get("/static/app.css")

    assert stylesheet.status_code == 200
    assert "css" in stylesheet.headers["content-type"]

    favicon = client.get("/static/favicon.png")

    assert favicon.status_code == 200


def test_api_bind_address_prefers_explicit_port_then_platform_port(monkeypatch):
    monkeypatch.delenv("PAPERBASE_API_HOST", raising=False)
    monkeypatch.delenv("PAPERBASE_API_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    assert _resolve_bind_address() == ("0.0.0.0", 8080)

    monkeypatch.setenv("PORT", "9000")
    assert _resolve_bind_address() == ("0.0.0.0", 9000)

    monkeypatch.setenv("PAPERBASE_API_HOST", "127.0.0.1")
    monkeypatch.setenv("PAPERBASE_API_PORT", "9100")
    assert _resolve_bind_address() == ("127.0.0.1", 9100)


def test_api_main_respects_reload_env(monkeypatch):
    import paperbase.api as api_module

    captured: dict[str, object] = {}

    monkeypatch.setattr(api_module, "get_settings", lambda: Settings(log_level="WARNING", log_format="json"))
    monkeypatch.setattr(
        api_module,
        "build_log_config",
        lambda *, component, level, log_format: {
            "component": component,
            "level": level,
            "format": log_format,
        },
    )
    monkeypatch.setenv("PAPERBASE_API_RELOAD", "1")

    def fake_uvicorn_run(app, *, host, port, reload, log_config, log_level):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload
        captured["log_config"] = log_config
        captured["log_level"] = log_level

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    api_module.main()

    assert captured["app"] == "paperbase.api:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8080
    assert captured["reload"] is True
    assert captured["log_level"] == "warning"


def test_probe_integrations_returns_database_and_storage_status(service: PaperbaseService):
    probe = service.probe_integrations()

    assert probe.ok is True
    assert probe.database.ok is True
    assert probe.storage.ok is True
    assert probe.storage.metadata["path"].startswith("system/probes/probe_")
    assert probe.storage.metadata["signed_url_generated"] is True


def test_probe_integrations_route_returns_503_on_storage_failure():
    class FailingObjectStore(MemoryObjectStore):
        def put_text(self, path: str, text: str, *, content_type: str = "text/plain; charset=utf-8") -> None:
            raise RuntimeError("write denied")

    service = PaperbaseService(
        settings=Settings(storage_backend="gcs", database_url="memory://", api_key="test-token"),
        repository=MemoryPaperRepository(),
        object_store=FailingObjectStore(),
    )
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    response = client.post("/v1/debug/integrations", headers=_auth_headers())

    assert response.status_code == 503
    assert response.json()["database"]["ok"] is True
    assert response.json()["storage"]["ok"] is False
    assert "write denied" in response.json()["storage"]["detail"]

    app.dependency_overrides.clear()


class _StubEmbeddings:
    """Deterministic fake that lets reindex tests assert exactly which papers
    and chunks are processed without loading sentence-transformers."""

    def __init__(self) -> None:
        self.paper_calls: list[list[str]] = []
        self.chunk_calls: list[list[str]] = []

    def encode(self, text: str) -> list[float] | None:
        return [float(len(text)), 0.0, 0.0]

    def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        self.chunk_calls.append(list(texts))
        return [[float(i), 0.0, 0.0] for i, _ in enumerate(texts, start=1)]

    def encode_paper_batch(self, pairs: list[tuple[str, str]]) -> list[list[float]] | None:
        # Record the flattened text the reindex loop would have sent through
        # the single-string encode_batch path previously, so existing
        # assertions that inspect paper_calls keep working.
        self.paper_calls.append(["\n".join(part for part in pair if part) for pair in pairs])
        return [[float(i), 0.0, 0.0] for i, _ in enumerate(pairs, start=1)]


def _seed_indexed_paper(
    repo: MemoryPaperRepository,
    arxiv_id: str,
    *,
    with_paper_embedding: bool,
    chunks_with_embedding: int,
    chunks_without_embedding: int,
) -> str:
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"Paper {arxiv_id}",
        abstract="Abstract text for the paper.",
        published="2024-01-01",
        index_status="indexed",
        citation_count=10,
    )
    stored = repo.upsert_paper(
        paper,
        search_document=paper.abstract,
        embedding=[0.1, 0.2, 0.3] if with_paper_embedding else None,
    )
    chunks: list[dict] = []
    for index in range(chunks_with_embedding):
        chunks.append(
            {
                "path": f"sections/sec-{index}.md",
                "chunk_kind": "section",
                "content": f"Section content {arxiv_id}/{index}",
                "embedding": [0.5, 0.5, 0.5],
            }
        )
    for index in range(chunks_without_embedding):
        chunks.append(
            {
                "path": f"sections/gap-{index}.md",
                "chunk_kind": "section",
                "content": f"Uncovered content {arxiv_id}/{index}",
            }
        )
    repo.replace_text_chunks(stored.paper_id, chunks)
    return stored.paper_id


def test_reindex_visits_chunk_only_candidates(service: PaperbaseService):
    # Paper A: fully embedded — should be ignored.
    _seed_indexed_paper(
        service.repository, "2401.00001", with_paper_embedding=True, chunks_with_embedding=2, chunks_without_embedding=0
    )
    # Paper B: paper embedding present, chunks missing — the regression case.
    paper_b = _seed_indexed_paper(
        service.repository, "2401.00002", with_paper_embedding=True, chunks_with_embedding=1, chunks_without_embedding=2
    )
    # Paper C: paper embedding missing — should also be picked up.
    paper_c = _seed_indexed_paper(
        service.repository,
        "2401.00003",
        with_paper_embedding=False,
        chunks_with_embedding=0,
        chunks_without_embedding=1,
    )

    service.embeddings = _StubEmbeddings()
    result = service.reindex_missing_embeddings(limit=100, include_chunks=True)

    # Both B and C need updates; A must stay untouched.
    assert result["chunks_updated"] == 3  # 2 from B + 1 from C
    # At minimum the paper missing its vector must be refreshed; we don't
    # require B to be re-embedded, but it's allowed.
    assert result["papers_updated"] >= 1
    assert service.repository.list_chunks_missing_embedding(paper_b) == []
    assert service.repository.list_chunks_missing_embedding(paper_c) == []


def test_reindex_processes_all_pages_without_skipping(service: PaperbaseService):
    # Seed 20 papers each with one missing chunk embedding. Backfill with a
    # small per-page budget so more than one round is required.
    paper_ids: list[str] = []
    for i in range(20):
        arxiv_id = f"2402.{i:05d}"
        paper_ids.append(
            _seed_indexed_paper(
                service.repository,
                arxiv_id,
                with_paper_embedding=True,
                chunks_with_embedding=0,
                chunks_without_embedding=1,
            )
        )

    service.embeddings = _StubEmbeddings()
    # Force multiple batches through the page floor.
    service.settings.chunk_max_chars = 1500
    result = service.reindex_missing_embeddings(limit=20, include_chunks=True)

    assert result["chunks_updated"] == 20
    for paper_id in paper_ids:
        assert service.repository.list_chunks_missing_embedding(paper_id) == []


def test_reindex_rewrites_existing_embeddings_when_requested(service: PaperbaseService):
    paper_id = _seed_indexed_paper(
        service.repository,
        "2402.99999",
        with_paper_embedding=True,
        chunks_with_embedding=2,
        chunks_without_embedding=0,
    )
    original_paper_embedding = list(service.repository.paper_embeddings[paper_id])
    original_chunk_embeddings = [
        list(chunk["embedding"])
        for chunk in service.repository.list_chunks_missing_embedding(paper_id, include_existing=True)
    ]

    service.embeddings = _StubEmbeddings()
    result = service.reindex_embeddings(limit=10, include_chunks=True, rewrite_existing=True)

    assert result == {"papers_updated": 1, "chunks_updated": 2}
    assert service.repository.paper_embeddings[paper_id] != original_paper_embedding
    updated_chunk_embeddings = [
        list(chunk["embedding"])
        for chunk in service.repository.list_chunks_missing_embedding(paper_id, include_existing=True)
    ]
    assert updated_chunk_embeddings != original_chunk_embeddings


def test_reindex_batches_large_chunk_rewrites(service: PaperbaseService):
    _seed_indexed_paper(
        service.repository,
        "2402.99998",
        with_paper_embedding=True,
        chunks_with_embedding=260,
        chunks_without_embedding=0,
    )
    embeddings = _StubEmbeddings()
    service.embeddings = embeddings

    result = service.reindex_embeddings(limit=10, include_chunks=True, rewrite_existing=True)

    assert result["chunks_updated"] == 260
    assert [len(batch) for batch in embeddings.chunk_calls] == [128, 128, 4]


def test_enqueue_reindex_embeddings_job_defaults_to_full_rewrite(service: PaperbaseService):
    job = service.enqueue_reindex_embeddings_job(limit=11, include_chunks=False)

    assert job.job_type == "reindex_embeddings"
    assert job.payload == {
        "limit": 11,
        "include_chunks": False,
        "rewrite_existing": True,
    }


def test_suggest_and_stats_endpoints(service: PaperbaseService):
    _seed_indexed_paper(
        service.repository, "2403.00001", with_paper_embedding=True, chunks_with_embedding=2, chunks_without_embedding=0
    )
    response = service.suggest_papers("paper 2403", limit=5)
    assert response, "suggest should return at least the matching seed paper"
    assert response[0]["matched_field"] in {"title", "title_prefix", "authors", "summary"}

    stats = service.get_stats()
    assert stats["indexed_papers"] >= 1
    assert stats["papers_with_embedding"] >= 1
    assert stats["total_chunks"] >= 2


def test_reindex_continues_past_failed_frontier_page(service: PaperbaseService):
    """Regression for the 'top frontier fails forever' starvation bug.

    Seed 20 papers missing chunk embeddings. First ~10 attempts fail; the
    remaining ones must still be reached. Before the query-time exclusion fix
    the loop stalled as soon as every row in the top frontier was in the
    client-side `attempted` set.
    """
    paper_ids: list[str] = []
    for i in range(20):
        paper_ids.append(
            _seed_indexed_paper(
                service.repository,
                f"2404.{i:05d}",
                with_paper_embedding=True,
                chunks_with_embedding=0,
                chunks_without_embedding=1,
            )
        )

    failing_paper_ids = set(paper_ids[:10])

    class SelectiveFailEmbeddings(_StubEmbeddings):
        def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
            # Every other call is a chunk call in our loop order; the chunk
            # loop iterates per-paper, so we flip based on paper identity via
            # a sentinel in the text payload. Simpler: fail if any text
            # mentions a known-to-fail paper id. (Test controls the payloads
            # via `_seed_indexed_paper`, which stamps ids into content.)
            if any(any(fid.replace("arxiv:", "") in text for fid in failing_paper_ids) for text in texts):
                return None
            return super().encode_batch(texts)

    service.embeddings = SelectiveFailEmbeddings()
    result = service.reindex_missing_embeddings(limit=20, include_chunks=True)

    # At least one of the non-failing papers past the first frontier must have
    # been reached. Exact count depends on interleaving with the paper loop,
    # but the critical property is that we advanced past the failing set.
    assert result["chunks_updated"] >= 1
    resolved_ids = [
        paper_id for paper_id in paper_ids[10:] if not service.repository.list_chunks_missing_embedding(paper_id)
    ]
    assert resolved_ids, "reindex should advance past the failing frontier"


def test_summarize_top_cited_continues_past_failed_frontier(service: PaperbaseService):
    """Regression for the same starvation pattern in summarize_top_cited.

    Seed 40 candidate papers. Stub summarization to fail for the first 20 and
    succeed for the rest. The loop must still reach and update the later
    papers.
    """
    service.settings.summary_provider = "vertex"
    service.settings.vertex_project = "stub"
    service.settings.featured_tier_concurrency = 4

    paper_ids: list[str] = []
    for i in range(40):
        paper_ids.append(
            _seed_indexed_paper(
                service.repository,
                f"2405.{i:05d}",
                with_paper_embedding=True,
                chunks_with_embedding=0,
                chunks_without_embedding=0,
            )
        )
        # Force needs-summary: wipe the heuristic summary the seed put there.
        service.repository.update_summaries(paper_ids[-1], summary_short="", summary_eli5="")

    failing = set(paper_ids[:20])

    import paperbase.summaries as summaries_module

    def fake_summary(paper, settings):
        if paper.paper_id in failing:
            return None
        return ("technical summary", "plain summary")

    original = summaries_module.generate_summary_on_demand
    summaries_module.generate_summary_on_demand = fake_summary  # type: ignore[attr-defined]
    try:
        result = service.summarize_top_cited(limit=40)
    finally:
        summaries_module.generate_summary_on_demand = original

    # Any later-ranked paper getting its summary proves the frontier advanced.
    later_updated = [
        pid
        for pid in paper_ids[20:]
        if (service.repository.get_paper(pid) or type("_", (), {"summary_eli5": ""})).summary_eli5
    ]
    assert later_updated, "summarize_top_cited should advance past failures"
    assert result["updated"] >= 1


def test_suggest_papers_matches_summary_text(service: PaperbaseService):
    """A paper whose term lives only in summary_short must still surface."""
    paper = PaperMetadata(
        arxiv_id="2406.00001",
        title="Totally unrelated headline",
        abstract="abstract text",
        summary_short="This paper investigates multi-agent cooperation under partial observability.",
        authors="Someone, Other",
        published="2024-06-01",
        index_status="indexed",
    )
    service.repository.upsert_paper(paper, search_document=paper.abstract)

    hits = service.suggest_papers("multi-agent", limit=5)
    assert hits, "summary_short match should surface the paper"
    assert hits[0]["matched_field"] == "summary"


def test_suggest_papers_returns_highlight_payload(service: PaperbaseService):
    paper = PaperMetadata(
        arxiv_id="2406.00002",
        title="Attention Is All You Need",
        abstract="abstract",
        authors="Vaswani et al.",
        published="2017-06-01",
        index_status="indexed",
    )
    service.repository.upsert_paper(paper, search_document=paper.abstract)

    hits = service.suggest_papers("Attention", limit=5)
    assert hits
    top = hits[0]
    assert "highlight_snippet" in top
    assert "Attention" in top["highlight_snippet"]


def test_suggest_fallback_papers_returns_indexed_ranked_papers(service: PaperbaseService):
    indexed_high = PaperMetadata(
        arxiv_id="2406.00003",
        title="Top Indexed Paper",
        abstract="abstract",
        summary_short="Top fallback summary",
        authors="Ada Lovelace",
        published="2026-04-01",
        citation_count=50,
        influential_citation_count=7,
        index_status="indexed",
    )
    indexed_low = PaperMetadata(
        arxiv_id="2406.00004",
        title="Lower Ranked Indexed Paper",
        abstract="abstract",
        authors="Grace Hopper",
        published="2025-04-01",
        citation_count=5,
        influential_citation_count=1,
        index_status="indexed",
    )
    pending = PaperMetadata(
        arxiv_id="2406.00005",
        title="Pending Paper",
        abstract="abstract",
        authors="Alan Turing",
        published="2026-04-02",
        citation_count=500,
        influential_citation_count=50,
        index_status="pending",
    )
    service.repository.upsert_paper(indexed_low, search_document=indexed_low.abstract)
    service.repository.upsert_paper(pending, search_document=pending.abstract)
    service.repository.upsert_paper(indexed_high, search_document=indexed_high.abstract)

    hits = service.suggest_fallback_papers(limit=5)

    assert [hit["title"] for hit in hits[:2]] == [
        "Top Indexed Paper",
        "Lower Ranked Indexed Paper",
    ]
    assert all(hit["matched_field"] == "fallback" for hit in hits)
    assert "Pending Paper" not in {hit["title"] for hit in hits}


def test_specter2_embedder_uses_text_pair_tokenization():
    """The SPECTER2 raw-transformers path must build `title [SEP] body` via
    the tokenizer's pair API, not a newline-joined single string. Regressing
    this silently drifts vectors away from the space SPECTER2 was trained on.
    """
    from paperbase.service import _TransformersCLSEmbedder

    tokenizer_calls: list[dict[str, object]] = []

    class _FakeBatch(dict):
        """Behaves like a tokenizer BatchEncoding: supports `.to()` and
        `**unpacking` into model call kwargs."""

        def to(self, device):
            return self

    class _FakeTokenizer:
        def __call__(self, *args, **kwargs):
            tokenizer_calls.append(dict(kwargs))
            return _FakeBatch(input_ids=[[0]])

    class _FakeHidden:
        def __init__(self, arr):
            self._arr = arr

        @property
        def last_hidden_state(self):
            return self

        def __getitem__(self, _idx):
            class _Tensor:
                def __init__(self, arr):
                    self._arr = arr

                def detach(self):
                    return self

                def cpu(self):
                    return self

                def numpy(self):
                    return self._arr

            return _Tensor(self._arr)

    class _FakeModel:
        def __call__(self, **_kwargs):
            class _Out:
                pass

            import numpy as np

            out = _Out()
            out.last_hidden_state = _FakeHiddenState(np.array([[0.1, 0.2, 0.3]], dtype="float32"))
            return out

        def eval(self):
            return self

        def to(self, device):
            return self

    class _FakeHiddenState:
        def __init__(self, arr):
            self._arr = arr

        def __getitem__(self, _idx):
            class _Row:
                def __init__(self, arr):
                    self._arr = arr

                def detach(self):
                    return self

                def cpu(self):
                    return self

                def numpy(self):
                    return self._arr

            return _Row(self._arr)

    embedder = _TransformersCLSEmbedder.__new__(_TransformersCLSEmbedder)
    import torch

    # Stub torch.nn.functional.normalize so _run doesn't execute real math
    # against our fake tensors.
    original_normalize = torch.nn.functional.normalize
    torch.nn.functional.normalize = lambda tensor, **_kw: tensor
    try:
        embedder._torch = torch
        embedder._tokenizer = _FakeTokenizer()
        embedder._model = _FakeModel()
        embedder._device = "cpu"

        # Pair input → tokenizer must be called with text= and text_pair=.
        embedder.encode_pairs(
            [
                ("Attention Is All You Need", "We propose a model based solely on attention."),
            ]
        )
        assert tokenizer_calls, "tokenizer should have been invoked"
        call = tokenizer_calls[-1]
        assert call.get("text") == ["Attention Is All You Need"]
        assert call.get("text_pair") == ["We propose a model based solely on attention."]
        assert call.get("max_length") == 512

        # Chunk input (no pair) → no text_pair kwarg, single-sequence path.
        tokenizer_calls.clear()
        embedder.encode(["Section: Introduction. We describe..."])
        assert tokenizer_calls
        call = tokenizer_calls[-1]
        assert "text_pair" not in call
    finally:
        torch.nn.functional.normalize = original_normalize


def test_encode_paper_drops_summary_short_from_paper_embedding_input(monkeypatch):
    """Paper-level vector input must not include summary_short. SPECTER2 was
    never trained on that heuristic-generated text; keeping it in the input
    drifts the vectors away from the training distribution.
    """
    from paperbase.config import Settings
    from paperbase.service import OptionalEmbeddingProvider

    settings = Settings(
        storage_backend="memory",
        database_url="memory://",
        api_key="test",
        summary_provider="none",
        embedding_model="allenai/specter2_base",
        embedding_dim=768,
    )
    provider = OptionalEmbeddingProvider(settings)

    received: list[tuple[str, str | None]] = []

    class _FakeSpecterModel:
        def encode_pairs(self, pairs, **_):
            received.extend(pairs)
            return [[0.0] * settings.embedding_dim for _ in pairs]

    monkeypatch.setattr(provider, "_model", lambda: _FakeSpecterModel())

    vector = provider.encode_paper(
        title="Attention Is All You Need",
        body="We propose a model based solely on attention mechanisms.",
    )
    assert vector is not None
    assert received == [("Attention Is All You Need", "We propose a model based solely on attention mechanisms.")]
    # Guard: caller builds the pair; heuristic summaries must not leak in.
    assert all("summary" not in (pair[0] or "").lower() for pair in received)
    assert all("summary" not in (pair[1] or "").lower() for pair in received)


def test_init_creates_trigram_indexes_for_summary_and_tldr():
    """Verify that SqlAlchemyPaperRepository.init() issues CREATE INDEX DDL
    for summary_short and tldr trigram ops. The suggest endpoint's widened
    ILIKE branch depends on those indexes to stay out of sequential-scan
    territory under typeahead load.
    """
    from paperbase.repository import Base, SqlAlchemyPaperRepository

    statements: list[str] = []

    class _FakeConn:
        def execute(self, stmt, *args, **kwargs):
            text_attr = getattr(stmt, "text", None)
            if text_attr is not None:
                statements.append(text_attr)

    class _FakeBegin:
        def __enter__(self):
            return _FakeConn()

        def __exit__(self, *exc):
            return False

    class _FakeEngine:
        url = None

        def begin(self):
            return _FakeBegin()

    repo = SqlAlchemyPaperRepository.__new__(SqlAlchemyPaperRepository)
    repo._embedding_dim = 768
    repo._engine = _FakeEngine()
    repo._session_factory = None  # type: ignore[assignment]
    repo._backfill_projects = lambda: None  # type: ignore[method-assign]

    original_create_all = Base.metadata.create_all
    Base.metadata.create_all = lambda engine: None
    try:
        repo.init()
    finally:
        Base.metadata.create_all = original_create_all

    combined = "\n".join(statements)
    assert "UPDATE jobs SET root_job_id = job_id WHERE root_job_id IS NULL AND job_type <> 'harvest_chunk'" in combined
    assert "ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS embedding VECTOR(768)" in combined
    assert "ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS embedding VECTOR(768)" in combined
    assert "papers_summary_short_trgm" in combined
    assert "summary_short gin_trgm_ops" in combined
    assert "papers_tldr_trgm" in combined
    assert "tldr gin_trgm_ops" in combined
    assert "text_chunks_content_tsv_gin" in combined
    assert "to_tsvector('english', coalesce(content, ''))" in combined


def test_search_local_uses_chunk_candidates_without_grouping_before_vector_limit():
    """Chunk vector recall keeps the pgvector nearest-neighbor shape.

    Fetch nearest chunk rows first, then aggregate those top candidates to
    papers. Grouping every chunk before LIMIT would force a broader scan.
    """
    from paperbase.repository import SqlAlchemyPaperRepository

    statements: list[str] = []

    class _FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, stmt, *_args, **_kwargs):
            text_attr = getattr(stmt, "text", None)
            if text_attr is not None:
                statements.append(" ".join(text_attr.split()))
            return _FakeResult()

    repo = SqlAlchemyPaperRepository.__new__(SqlAlchemyPaperRepository)
    repo._session = lambda: _FakeSession()  # type: ignore[method-assign]

    results = repo.search_local("caption query", limit=5, query_embedding=[0.1] * 768)

    assert results == []
    chunk_lexical_sql = next(
        stmt for stmt in statements if "chunk_score" in stmt and "chunk_vector_score" not in stmt
    )
    assert "tc.chunk_kind <> 'full_text'" in chunk_lexical_sql
    assert "JOIN papers ON papers.paper_id = scored.pid" in chunk_lexical_sql

    chunk_vector_sql = next(
        stmt for stmt in statements if "chunk_vector_score" in stmt and ") nearest" in stmt
    )
    assert (
        chunk_vector_sql.index("ORDER BY tc.embedding <=> CAST(:query_embedding AS vector)")
        < chunk_vector_sql.index("GROUP BY nearest.pid")
    )
    assert "GROUP BY tc.paper_id ORDER BY chunk_vector_score DESC LIMIT :candidate_pool" not in chunk_vector_sql


def test_browser_auth_and_password_reset_flow(browser_service):
    service, email_sender, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "user@example.com", "password": "topsecret123", "display_name": "User"},
    )
    assert signup.status_code == 200
    assert signup.json()["authenticated"] is True
    csrf_token = signup.json()["csrf_token"]
    assert csrf_token

    session = client.get("/v1/browser/session")
    assert session.json()["user"]["email"] == "user@example.com"
    assert session.json()["csrf_token"] == csrf_token

    forgot = client.post("/v1/browser/auth/forgot-password", json={"email": "user@example.com"})
    assert forgot.status_code == 200
    assert email_sender.reset_emails
    reset_link = email_sender.reset_emails[0][1]
    reset_token = parse_qs(urlparse(reset_link).query)["reset_token"][0]

    reset = client.post("/v1/browser/auth/reset-password", json={"token": reset_token, "password": "newsecret456"})
    assert reset.status_code == 200

    logout = client.post("/v1/browser/auth/logout", headers={"X-Paperbase-CSRF": csrf_token})
    assert logout.status_code == 200

    login = client.post("/v1/browser/auth/login", json={"email": "user@example.com", "password": "newsecret456"})
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert login.json()["csrf_token"]

    app.dependency_overrides.clear()


def test_browser_settings_round_trip(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="settings@example.com", password="topsecret123", display_name="Settings User")
    )
    assert session.user is not None

    anthropic = service.save_user_anthropic_key(
        session.user,
        SaveAnthropicKeyRequest(api_key="anthropic-user-key-999"),
    )
    modal_credentials = service.save_user_modal_credentials(
        session.user,
        SaveModalCredentialsRequest(
            token_id="modal-token-id-999",
            token_secret="modal-token-secret-999",
        ),
    )
    notifications = service.update_user_notification_preferences(
        session.user,
        UpdateNotificationsRequest(weekly_papers_review_email=True),
    )
    appearance = service.update_user_appearance(
        session.user,
        UpdateAppearanceRequest(theme="dark"),
    )
    github = service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    assert anthropic.has_anthropic_api_key is True
    assert anthropic.settings.anthropic_api_key_hint.startswith("anth")
    assert modal_credentials.settings.has_modal_credentials is True
    assert modal_credentials.settings.modal_token_id_hint.startswith("moda")
    assert notifications.settings.weekly_papers_review_email is True
    assert appearance.settings.appearance_theme == "dark"
    assert github.settings.has_github_connection is True
    assert github.settings.github_login == "octocat"

    cleared = service.delete_user_modal_credentials(session.user)
    disconnected = service.delete_github_connection(session.user)

    assert cleared.settings.has_modal_credentials is False
    assert disconnected.settings.has_github_connection is False


def test_browser_session_marks_admin_users():
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            admin_emails="admin@example.com",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=FakeChatModel(),
        github_repo_manager=FakeGitHubRepoManager(),
    )

    session, raw_token = service.register_browser_user(
        SignupRequest(email="admin@example.com", password="topsecret123", display_name="Admin User")
    )

    assert session.user is not None
    assert session.user.is_admin is True
    current_user = service.get_current_browser_user(raw_token)
    assert current_user is not None
    assert current_user.is_admin is True


def test_admin_paper_listing_filters_and_sorts(service):
    first = _sample_paper("2401.11111", title="Benchmark Systems", abstract="Grounding benchmark.")
    first.index_status = "indexed"
    first.indexed_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    second = _sample_paper("2401.22222", title="Other Work", abstract="Other abstract.")
    second.index_status = "discovered"
    service.repository.upsert_paper(first)  # type: ignore[attr-defined]
    service.repository.upsert_paper(second)  # type: ignore[attr-defined]

    results = service.list_papers(query="benchmark", index_status="indexed", limit=10)

    assert [paper.paper_id for paper in results] == ["arxiv:2401.11111"]


def test_trending_feed_filters_caps_sorts_and_handles_missing_preview(browser_service):
    service, _, _ = browser_service
    session_a, _ = service.register_browser_user(
        SignupRequest(email="alice@example.com", password="secret12345", display_name="Alice")
    )
    session_b, _ = service.register_browser_user(
        SignupRequest(email="bob@example.com", password="secret12345", display_name="Bob")
    )
    session_c, _ = service.register_browser_user(
        SignupRequest(email="carol@example.com", password="secret12345", display_name="Carol")
    )
    assert session_a.user is not None
    assert session_b.user is not None
    assert session_c.user is not None

    for index in range(52):
        arxiv_id = f"2402.{index:05d}"
        paper = _sample_paper(arxiv_id, title=f"Trending Paper {index}", abstract="Daily paper")
        paper.index_status = "indexed"
        paper.published = "2024-02-01"
        paper.indexed_at = datetime(2026, 4, 1, 0, index % 60, tzinfo=timezone.utc)
        service.repository.upsert_paper(paper)  # type: ignore[attr-defined]

    off_day = _sample_paper("2403.99999", title="Wrong Day", abstract="Ignore me")
    off_day.index_status = "indexed"
    off_day.published = "2024-02-02"
    off_day.indexed_at = datetime(2026, 4, 2, tzinfo=timezone.utc)
    service.repository.upsert_paper(off_day)  # type: ignore[attr-defined]

    service.object_store.put_bytes(  # type: ignore[attr-defined]
        "papers/arxiv:2402.00051/preview/first-page-top.webp",
        b"preview",
        content_type="image/webp",
    )

    service.set_browser_trending_paper_upvote("arxiv:2402.00000", user=session_a.user, upvoted=True)
    service.set_browser_trending_paper_upvote("arxiv:2402.00000", user=session_b.user, upvoted=True)
    service.set_browser_trending_paper_upvote("arxiv:2402.00000", user=session_c.user, upvoted=True)
    service.set_browser_trending_paper_upvote("arxiv:2402.00051", user=session_a.user, upvoted=True)

    default_feed = service.list_browser_trending_papers(
        date="2024-02-01",
        sort="default",
        viewer=session_a.user,
    )
    upvote_feed = service.list_browser_trending_papers(
        date="2024-02-01",
        sort="upvotes",
        viewer=session_a.user,
    )

    assert len(default_feed.papers) == 50
    assert default_feed.papers[0].paper_id == "arxiv:2402.00051"
    assert default_feed.papers[0].image_url is not None
    assert default_feed.papers[1].image_url is None
    assert all(paper.published == "2024-02-01" for paper in default_feed.papers)
    assert upvote_feed.papers[0].paper_id == "arxiv:2402.00000"
    assert upvote_feed.papers[0].upvote_count == 3
    assert upvote_feed.papers[0].viewer_has_upvoted is True


def test_trending_detail_builds_nested_comments_and_vote_summary(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="reader@example.com", password="secret12345", display_name="Reader")
    )
    assert session.user is not None
    paper = _sample_paper("2404.11111", title="Nested Replies", abstract="Threaded discussion")
    paper.index_status = "indexed"
    service.repository.upsert_paper(paper)  # type: ignore[attr-defined]

    service.object_store.put_bytes(  # type: ignore[attr-defined]
        "papers/arxiv:2404.11111/preview/first-page-top.webp",
        b"preview",
        content_type="image/webp",
    )
    service.set_browser_trending_paper_upvote("arxiv:2404.11111", user=session.user, upvoted=True)
    root_comments = service.create_browser_trending_paper_comment(
        "arxiv:2404.11111",
        user=session.user,
        request=CreatePaperCommentRequest(body="Root note"),
    )
    root_id = root_comments.comments[0].comment_id
    service.create_browser_trending_paper_comment(
        "arxiv:2404.11111",
        user=session.user,
        request=CreatePaperCommentRequest(body="Reply note", parent_comment_id=root_id),
    )

    detail = service.get_browser_trending_paper("arxiv:2404.11111", viewer=session.user)

    assert detail.upvote_count == 1
    assert detail.viewer_has_upvoted is True
    assert detail.image_url is not None
    assert len(detail.comments) == 1
    assert detail.comments[0].body == "Root note"
    assert detail.comments[0].replies[0].body == "Reply note"


def test_trending_comment_rejects_reply_target_from_another_paper(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="writer@example.com", password="secret12345", display_name="Writer")
    )
    assert session.user is not None

    first = _sample_paper("2405.11111", title="Paper One", abstract="First")
    first.index_status = "indexed"
    second = _sample_paper("2405.22222", title="Paper Two", abstract="Second")
    second.index_status = "indexed"
    service.repository.upsert_paper(first)  # type: ignore[attr-defined]
    service.repository.upsert_paper(second)  # type: ignore[attr-defined]

    created = service.create_browser_trending_paper_comment(
        "arxiv:2405.11111",
        user=session.user,
        request=CreatePaperCommentRequest(body="Root comment"),
    )
    root_id = created.comments[0].comment_id

    with pytest.raises(HTTPException) as exc:
        service.create_browser_trending_paper_comment(
            "arxiv:2405.22222",
            user=session.user,
            request=CreatePaperCommentRequest(body="Wrong reply", parent_comment_id=root_id),
        )

    assert exc.value.status_code == 400


def test_get_paper_asset_returns_browser_content_url_for_local_storage(service):
    paper = _sample_paper("2401.33333", title="Artifact Paper", abstract="Artifact abstract.")
    paper.index_status = "indexed"
    service.repository.upsert_paper(paper)  # type: ignore[attr-defined]
    service.object_store.put_bytes(  # type: ignore[attr-defined]
        "papers/arxiv:2401.33333/bundle.pdf",
        b"%PDF-1.4",
        content_type="application/pdf",
    )

    asset = service.get_paper_asset("arxiv:2401.33333", "bundle.pdf")

    assert asset.paper_id == "arxiv:2401.33333"
    assert asset.url == "http://127.0.0.1:8080/v1/browser/admin/papers/arxiv%3A2401.33333/asset-content/bundle.pdf"
    assert asset.content_type == "application/pdf"


def test_get_paper_preview_content_returns_bytes(service):
    paper = _sample_paper("2401.33334", title="Artifact Bytes", abstract="Artifact abstract.")
    paper.index_status = "indexed"
    service.repository.upsert_paper(paper)  # type: ignore[attr-defined]
    service.object_store.put_bytes(  # type: ignore[attr-defined]
        "papers/arxiv:2401.33334/preview/first-page-top.webp",
        b"preview-bytes",
        content_type="image/webp",
    )

    payload, content_type = service.get_paper_preview_content("arxiv:2401.33334")

    assert payload == b"preview-bytes"
    assert content_type == "image/webp"


def test_get_paper_preview_returns_browser_content_url_for_local_storage(service):
    paper = _sample_paper("2401.33335", title="Preview Paper", abstract="Artifact abstract.")
    paper.index_status = "indexed"
    service.repository.upsert_paper(paper)  # type: ignore[attr-defined]
    service.object_store.put_bytes(  # type: ignore[attr-defined]
        "papers/arxiv:2401.33335/preview/first-page-top.webp",
        b"preview-bytes",
        content_type="image/webp",
    )

    preview = service.get_paper_preview("arxiv:2401.33335")

    assert preview.paper_id == "arxiv:2401.33335"
    assert preview.image_url == "http://127.0.0.1:8080/v1/browser/papers/arxiv%3A2401.33335/preview/content"
    assert preview.content_type == "image/webp"


def test_invalid_saved_anthropic_key_is_cleared_and_gates_chat(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="stale-key@example.com", password="topsecret123", display_name="Stale Key User")
    )
    assert session.user is not None

    service.save_user_anthropic_key(
        session.user,
        SaveAnthropicKeyRequest(api_key="anthropic-user-key-999"),
    )
    service.repository.save_anthropic_api_key(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        encrypted_api_key="stale-invalid-token",
        hint="anth...999",
    )

    thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="New chat"),
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Please continue", tag_filters=[]),
    )
    service.repository.save_anthropic_api_key(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        encrypted_api_key="stale-invalid-token",
        hint="anth...999",
    )
    streamed = list(
        service.stream_chat_message(
            session.user,
            thread.thread_id,
            SendChatMessageRequest(content="Please continue again", tag_filters=[]),
        )
    )

    assert response.state == "api_key_invalid"
    assert response.has_anthropic_api_key is False
    assert service.repository.get_anthropic_api_key_record(session.user.user_id) is None  # type: ignore[attr-defined]
    assert [event_name for event_name, _ in streamed] == ["api_key_required"]
    assert streamed[0][1]["state"] == "api_key_invalid"
    assert response.thread.messages == []
    assert streamed[0][1]["thread"]["messages"] == []


def test_sanitize_inline_citations_preserves_markdown_structure():
    answer = """# Findings

- Keep this citation [S1]
- Remove this one [S9]

```python
if grounded:
    print("still indented")
```
"""

    sanitized = _sanitize_inline_citations(
        answer,
        [
            ChatCitation(
                source_id="S1",
                source_kind="paperbase_paper",
                title="Grounding Paper",
                path="sections/findings.md",
                excerpt="Grounded finding.",
            )
        ],
    )

    assert sanitized == """# Findings

- Keep this citation [S1]
- Remove this one 

```python
if grounded:
    print("still indented")
```"""


def test_python_file_creation_requires_github_and_normalizes_names(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="python@example.com", password="topsecret123", display_name="Python User")
    )
    assert session.user is not None

    project_id = service.list_projects(session.user).projects[0].project_id

    with pytest.raises(HTTPException) as excinfo:
        service.create_python_file(session.user, project_id)
    assert excinfo.value.status_code == 409

    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    first = service.create_python_file(session.user, project_id)
    second = service.create_python_file(session.user, project_id)
    renamed = service.update_python_file(
        session.user,
        first.file_id,
        filename="analysis",
        content="print('hello')",
    )

    assert first.filename == "something.py"
    assert second.filename == "something_2.py"
    assert renamed.filename == "analysis.py"
    assert renamed.content == "print('hello')"


def test_project_code_paths_allow_duplicate_basenames(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="paths@example.com", password="topsecret123", display_name="Path User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    train = service.create_project_python_file(
        session.user,
        project_id,
        repo_path="src/train.py",
        content="print('train')",
    )
    eval_file = service.create_project_python_file(
        session.user,
        project_id,
        repo_path="experiments/train.py",
        content="print('eval')",
    )

    files = service.list_project_code_files(session.user, project_id)

    assert train.filename == "train.py"
    assert eval_file.filename == "train.py"
    assert {item.repo_path for item in files} == {"experiments/train.py", "src/train.py"}


def test_project_code_read_grep_edit_and_delete(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="code-tools@example.com", password="topsecret123", display_name="Code User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    created = service.create_project_python_file(
        session.user,
        project_id,
        repo_path="train.py",
        content="import torch\n\nvalue = 1\nvalue = 1\n",
    )

    read_result = service.read_project_code_file(session.user, project_id, "train.py", start_line=1, end_line=2)
    grep_result = service.grep_project_code(session.user, project_id, pattern="torch", literal=True, limit=5)

    assert read_result["repo_path"] == "train.py"
    assert read_result["total_lines"] == 4
    assert read_result["content"] == "1: import torch\n2: "
    assert grep_result["matches"] == [
        {
            "file_id": created.file_id,
            "repo_path": "train.py",
            "filename": "train.py",
            "file_kind": "python",
            "line_number": 1,
            "line": "import torch",
        }
    ]

    with pytest.raises(HTTPException) as exc_info:
        service.edit_project_code_file(
            session.user,
            project_id,
            repo_path="train.py",
            old_string="value = 1",
            new_string="value = 2",
        )
    assert exc_info.value.status_code == 409

    edited = service.edit_project_code_file(
        session.user,
        project_id,
        repo_path="train.py",
        old_string="value = 1",
        new_string="value = 2",
        replace_all=True,
    )
    deleted = service.delete_project_code_file(session.user, project_id, repo_path="train.py")

    assert edited["match_count"] == 2
    assert "value = 2" in edited["content"]
    assert deleted == {
        "file_id": created.file_id,
        "repo_path": "train.py",
        "filename": "train.py",
        "file_kind": "python",
        "deleted": True,
    }


def test_project_code_edit_can_populate_an_existing_empty_file(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="empty-edit@example.com", password="topsecret123", display_name="Empty Edit")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    created = service.create_project_python_file(
        session.user,
        project_id,
        repo_path="src/linear_gd.py",
        content="",
    )

    edited = service.edit_project_code_file(
        session.user,
        project_id,
        repo_path="src/linear_gd.py",
        old_string="",
        new_string="def train_linear_gd():\n    return 1\n",
    )

    assert edited["file_id"] == created.file_id
    assert edited["match_count"] == 1
    assert edited["content"] == "def train_linear_gd():\n    return 1\n"


def test_project_code_edit_rejects_empty_population_with_empty_new_string(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="empty-edit-reject@example.com", password="topsecret123", display_name="Empty Reject")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    service.create_project_python_file(
        session.user,
        project_id,
        repo_path="src/linear_gd.py",
        content="",
    )

    with pytest.raises(HTTPException) as exc_info:
        service.edit_project_code_file(
            session.user,
            project_id,
            repo_path="src/linear_gd.py",
            old_string="",
            new_string="",
        )

    assert exc_info.value.status_code == 422


def test_project_chat_tools_are_advertised_for_coding_turns(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="coding-chat@example.com", password="topsecret123", display_name="Coding Chat")
    )
    assert session.user is not None
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))

    tools = service._chat_tool_definitions(
        SendChatMessageRequest(content="Create a PyTorch training script and run a smoke test"),
        thread=thread,
    )
    tool_names = {tool["name"] for tool in tools}
    tools_by_name = {tool["name"]: tool for tool in tools}

    assert {
        "paperbase_search",
        "paperbase_search_sections",
        "paperbase_list_uploaded_documents",
        "paperbase_search_uploaded_sections",
        "project_list_files",
        "project_read_file",
        "project_grep",
        "project_write_file",
        "project_apply_patch",
        "project_move",
        "project_delete",
        "project_git_status",
        "project_git_diff",
        "project_exec",
        "project_wait",
        "project_cancel",
        "project_logs",
        "project_test",
        "project_lint",
        "project_run",
    }.issubset(tool_names)
    assert "workspace text files" in tools_by_name["project_list_files"]["description"]
    assert "Create or replace one repo-relative workspace text file" in tools_by_name["project_write_file"]["description"]
    assert "exact-match text patch" in tools_by_name["project_apply_patch"]["description"]
    assert "Move or rename one workspace file" in tools_by_name["project_move"]["description"]
    assert "truly needs deletion" in tools_by_name["project_delete"]["description"]
    assert "long-running command" in tools_by_name["project_exec"]["description"]
    assert "last synced project repository state" in tools_by_name["project_git_status"]["description"]

    prompt = service._chat_tool_system_prompt(
        thread,
        include_research_tools=True,
        include_project_code_tools=True,
    )
    assert "Once the tool results are sufficient, stop calling tools and answer directly in the same turn." in prompt
    assert "Use them only when paper evidence" in prompt
    assert "Operate like a coding agent." in prompt
    assert "Project workspace tools operate on the current project's repo-relative text files." in prompt
    assert "Generated runnable code should support smoke-scale validation when practical." in prompt
    assert "Every tool-enabled turn must begin by leveraging these research tools" not in prompt
    assert "do not stop tool use until the relevant managed project Python files have actually been created or updated" not in prompt


def test_project_snippet_request_uses_auto_tools_and_persists_file():
    class ProjectSnippetChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tools and not has_tool_results:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_touch_file",
                            "name": "project_touch",
                            "input": {
                                "repo_path": "src/csv_loader.py",
                                "content": "import csv\n\n\ndef load_rows(path: str) -> list[dict[str, str]]:\n    with open(path, newline=\"\", encoding=\"utf-8\") as handle:\n        return list(csv.DictReader(handle))\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [{"type": "text", "text": "Saved the snippet to `src/csv_loader.py`."}],
                "stop_reason": "end_turn",
            }

    chat_model = ProjectSnippetChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="project-snippet@example.com", password="topsecret123", display_name="Project Snippet")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Give me a Python snippet that loads CSV rows into dictionaries"),
    )

    saved_file = service.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=thread.project_id,
        repo_path="src/csv_loader.py",
    )
    assert response.assistant_message is not None
    assert saved_file is not None
    assert chat_model.create_calls[0]["tool_choice"] == {"type": "auto"}
    assert len(chat_model.create_calls) == 2


def test_project_code_turn_can_read_then_write_then_answer_in_same_loop():
    class ProjectWriteChatModel(FakeChatModel):
        def __init__(self) -> None:
            super().__init__()
            self.round_index = 0

        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tools and not has_tool_results:
                self.round_index += 1
                return {
                    "content": [{"type": "tool_use", "id": "toolu_list_files", "name": "project_list_files", "input": {}}],
                    "stop_reason": "tool_use",
                }
            if tools and self.round_index == 1:
                self.round_index += 1
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_touch_real_file",
                            "name": "project_touch",
                            "input": {
                                "repo_path": "src/train.py",
                                "content": "def train():\n    return 1\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [{"type": "text", "text": "Saved the training module to `src/train.py`."}],
                "stop_reason": "end_turn",
            }

    chat_model = ProjectWriteChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="project-write@example.com", password="topsecret123", display_name="Project Write")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Create a PyTorch training script for this project"),
    )

    assert response.assistant_message is not None
    assert service.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=thread.project_id,
        repo_path="src/train.py",
    )
    assert [call["tool_choice"] for call in chat_model.create_calls[:2]] == [{"type": "auto"}, {"type": "auto"}]


def test_project_code_turn_can_recover_from_failed_create_with_edit():
    class FailedTouchThenEditChatModel(FakeChatModel):
        def __init__(self) -> None:
            super().__init__()
            self.round_index = 0

        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            if tools and self.round_index == 0:
                self.round_index += 1
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_bad_touch",
                            "name": "project_touch",
                            "input": {
                                "repo_path": "src/linear_gd.py",
                                "content": "def train_linear_gd():\n    return 1\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            if tools and self.round_index == 1:
                self.round_index += 1
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_fill_empty_file",
                            "name": "project_edit",
                            "input": {
                                "repo_path": "src/linear_gd.py",
                                "old_string": "",
                                "new_string": "def train_linear_gd():\n    return 1\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [{"type": "text", "text": "Saved the module to `src/linear_gd.py`."}],
                "stop_reason": "end_turn",
            }

    chat_model = FailedTouchThenEditChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="failed-touch@example.com", password="topsecret123", display_name="Failed Touch")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))
    service.create_project_python_file(
        session.user,
        thread.project_id,
        repo_path="src/linear_gd.py",
        content="",
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Write some code to train a small linear gradient descent with pytorch"),
    )

    saved_file = service.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=thread.project_id,
        repo_path="src/linear_gd.py",
    )
    assert response.assistant_message is not None
    assert saved_file is not None
    assert saved_file.content == "def train_linear_gd():\n    return 1\n"
    assert [call["tool_choice"] for call in chat_model.create_calls[:2]] == [{"type": "auto"}, {"type": "auto"}]


def test_project_thread_can_answer_with_python_without_persisted_write():
    class UnsavedPythonAnswerChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "```python\ndef recurse(items):\n    return items\n```",
                    }
                ],
                "stop_reason": "end_turn",
            }

    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=UnsavedPythonAnswerChatModel(),
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="unsaved-python@example.com", password="topsecret123", display_name="Unsaved Python")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Explain recursion briefly"),
    )

    assert response.assistant_message is not None
    assert response.assistant_message.content_markdown == "```python\ndef recurse(items):\n    return items\n```"
    assert len(service.chat_model.create_calls) == 1
    assert service.chat_model.create_calls[0]["tool_choice"] == {"type": "auto"}


def test_project_thread_allows_python_answer_after_reading_existing_file():
    class ReadOnlyPythonAnswerChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tools and not has_tool_results:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_read_file",
                            "name": "project_read_file",
                            "input": {"repo_path": "src/train.py"},
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Here is `src/train.py`:\n\n```python\ndef train():\n    return 1\n```",
                    }
                ],
                "stop_reason": "end_turn",
            }

    chat_model = ReadOnlyPythonAnswerChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="read-code@example.com", password="topsecret123", display_name="Read Code")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))
    service.create_project_python_file(
        session.user,
        thread.project_id,
        repo_path="src/train.py",
        content="def train():\n    return 1\n",
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Show me src/train.py"),
    )

    assert response.assistant_message is not None
    assert response.assistant_message.content_markdown == (
        "Here is `src/train.py`:\n\n```python\ndef train():\n    return 1\n```"
    )
    assert [call["tool_choice"] for call in chat_model.create_calls] == [{"type": "auto"}, {"type": "auto"}]


def test_project_confirmation_turn_keeps_project_tools_available():
    class ProjectConfirmationChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tools and not has_tool_results:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_touch_file",
                            "name": "project_touch",
                            "input": {
                                "repo_path": "src/linear_gd.py",
                                "content": "def train_linear_gd():\n    return 1\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [{"type": "text", "text": "Saved the module to `src/linear_gd.py`."}],
                "stop_reason": "end_turn",
            }

    chat_model = ProjectConfirmationChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="project-confirm@example.com", password="topsecret123", display_name="Project Confirm")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))
    service.repository.add_chat_message(  # type: ignore[attr-defined]
        thread_id=thread.thread_id,
        role="user",
        content_markdown="Write some code to train a small linear gradient descent with pytorch",
    )
    service.repository.add_chat_message(  # type: ignore[attr-defined]
        thread_id=thread.thread_id,
        role="assistant",
        content_markdown="Want me to save this into your project (e.g. as src/linear_gd.py)?",
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="yes"),
    )

    saved_file = service.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=thread.project_id,
        repo_path="src/linear_gd.py",
    )
    assert response.assistant_message is not None
    assert saved_file is not None
    assert saved_file.content == "def train_linear_gd():\n    return 1\n"
    assert chat_model.create_calls[0]["tool_choice"] == {"type": "auto"}
    tool_names = {tool["name"] for tool in chat_model.create_calls[0]["tools"]}
    assert "project_write_file" in tool_names


def test_project_retry_turn_keeps_project_tools_available():
    class ProjectRetryChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tools and not has_tool_results:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_fill_empty_file",
                            "name": "project_edit",
                            "input": {
                                "repo_path": "src/linear_gd.py",
                                "old_string": "",
                                "new_string": "def train_linear_gd():\n    return 1\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [{"type": "text", "text": "Saved the module to `src/linear_gd.py`."}],
                "stop_reason": "end_turn",
            }

    chat_model = ProjectRetryChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
        github_repo_manager=FakeGitHubRepoManager(),
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="project-retry@example.com", password="topsecret123", display_name="Project Retry")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Project chat"))
    service.repository.add_chat_message(  # type: ignore[attr-defined]
        thread_id=thread.thread_id,
        role="user",
        content_markdown="Write some code to train a small linear gradient descent with pytorch",
    )
    service.repository.add_chat_message(  # type: ignore[attr-defined]
        thread_id=thread.thread_id,
        role="assistant",
        content_markdown="The file exists but still needs to be updated.",
    )
    service.create_project_python_file(
        session.user,
        thread.project_id,
        repo_path="src/linear_gd.py",
        content="",
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="try again"),
    )

    saved_file = service.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=thread.project_id,
        repo_path="src/linear_gd.py",
    )
    assert response.assistant_message is not None
    assert saved_file is not None
    assert saved_file.content == "def train_linear_gd():\n    return 1\n"
    assert chat_model.create_calls[0]["tool_choice"] == {"type": "auto"}
    tool_names = {tool["name"] for tool in chat_model.create_calls[0]["tools"]}
    assert "project_apply_patch" in tool_names


def test_python_file_sync_project_repo_writes_metadata_and_files():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="sync@example.com", password="topsecret123", display_name="Sync User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('sync')",
    )

    synced = service.sync_project_repo(
        session.user,
        project.project_id,
        ProjectRepoSyncRequest(commit_message="Save analysis.py"),
    )

    assert synced.repo_exists is True
    assert synced.github_repo_name == "paperbase-general-123abc"
    assert synced.last_sync_commit_sha == "abc123"
    assert len(repo_manager.ensure_repo_calls) == 1
    assert len(repo_manager.sync_project_files_calls) == 1
    synced_files = repo_manager.sync_project_files_calls[0]["files"]
    assert synced_files["src/analysis.py"] == "print('sync')"
    assert ".paperbase" in synced_files
    manifest = json.loads(synced_files[".paperbase"])
    assert manifest["project_id"] == project.project_id
    assert manifest["project_name"] == project.name
    assert manifest["custom_instructions"] == project.description
    assert manifest["github_owner_login"] == "octocat"
    assert manifest["base_branch"] == "main"
    assert manifest["managed_paths"] == [".paperbase", "src/analysis.py"]
    assert manifest["files"][0]["file_kind"] == "python"
    assert manifest["files"][0]["repo_path"] == "src/analysis.py"
    assert repo_manager.sync_project_files_calls[0]["base_commit_sha"] is None
    assert repo_manager.sync_project_files_calls[0]["expected_remote_head_sha"] is None
    assert repo_manager.sync_project_files_calls[0]["previous_managed_paths"] == set()


def test_create_project_requires_github_repo_info_and_connection():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="new-project@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None

    with pytest.raises(HTTPException) as missing_repo:
        service.create_project(
            session.user,
            CreateProjectRequest(name="Agents", description="Study agent systems"),
        )
    assert missing_repo.value.status_code == 422

    with pytest.raises(HTTPException) as disconnected:
        service.create_project(
            session.user,
            _create_repo_project_request("Agents", "Study agent systems"),
        )
    assert disconnected.value.status_code == 409


def test_list_github_repositories_requires_connection_and_returns_accessible_repos():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="repo-list@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None

    with pytest.raises(HTTPException) as disconnected:
        service.list_github_repositories(session.user)
    assert disconnected.value.status_code == 409

    _connect_github(service, session.user)
    repos = service.list_github_repositories(session.user)

    assert [repo.full_name for repo in repos.repos] == [
        "octocat/existing-repo",
        "paperbase-labs/shared-analysis",
    ]
    assert repos.repos[0].private is True
    assert repos.repos[1].default_branch == "dev"
    assert repo_manager.list_repositories_calls == [{"access_token": "github-access-token"}]


def test_create_project_creates_private_github_repo_on_main():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="repo-create@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    project = service.create_project(
        session.user,
        _create_repo_project_request("Agents", "Study agent systems", repo_name="agent-lab"),
    )
    sync = service.get_project_repo_sync(session.user, project.project_id)

    assert repo_manager.create_repo_calls[0]["repo_name"] == "agent-lab"
    assert repo_manager.create_repo_calls[0]["owner_login"] == "octocat"
    assert sync.github_owner_login == "octocat"
    assert sync.github_repo_name == "agent-lab"
    assert sync.base_branch == "main"
    assert sync.default_branch == "main"


def test_create_project_imports_existing_repo_python_and_notebook_files():
    repo_manager = FakeGitHubRepoManager()
    repo_manager.file_texts[("src/analysis.py", "dev")] = "print('analysis')\n"
    repo_manager.file_texts[("notebooks/demo.ipynb", "dev")] = _notebook_json("print('demo')\n")
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="repo-import@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    project = service.create_project(
        session.user,
        CreateProjectRequest(
            name="Repo Import",
            description="Import selected files",
            github_repo=CreateProjectGitHubRepoRequest(
                mode="existing",
                owner="octocat",
                name="existing-repo",
                base_branch="dev",
                import_paths=["src/analysis.py", "notebooks/demo.ipynb"],
            ),
        ),
    )
    files = service.repository.list_project_python_files(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=project.project_id,
    )
    assert files is not None
    files_by_path = {item.repo_path: item for item in files}

    assert repo_manager.get_repo_calls[0]["owner_login"] == "octocat"
    assert repo_manager.get_branch_head_calls[0]["branch"] == "dev"
    assert service.get_project_repo_sync(session.user, project.project_id).base_branch == "dev"
    assert files_by_path["src/analysis.py"].file_kind == "python"
    assert files_by_path["src/analysis.py"].content == "print('analysis')\n"
    assert files_by_path["notebooks/demo.ipynb"].file_kind == "notebook"
    assert "# %%" in files_by_path["notebooks/demo.ipynb"].content
    assert "print('demo')" in files_by_path["notebooks/demo.ipynb"].content


def test_create_project_rejects_missing_existing_repo_branch():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="repo-branch@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    with pytest.raises(HTTPException) as exc_info:
        service.create_project(
            session.user,
            CreateProjectRequest(
                name="Bad Branch",
                description="Import selected files",
                github_repo=CreateProjectGitHubRepoRequest(
                    mode="existing",
                    owner="octocat",
                    name="existing-repo",
                    base_branch="missing",
                ),
            ),
        )
    assert exc_info.value.status_code == 422


def test_create_project_rejects_invalid_notebook_imports_and_unsafe_paths():
    repo_manager = FakeGitHubRepoManager()
    repo_manager.file_texts[("notebooks/demo.ipynb", "main")] = "{not-json"
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="bad-notebook@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    with pytest.raises(HTTPException) as unsafe_path:
        service.create_project(
            session.user,
            CreateProjectRequest(
                name="Unsafe",
                description="Import selected files",
                github_repo=CreateProjectGitHubRepoRequest(
                    mode="existing",
                    owner="octocat",
                    name="existing-repo",
                    base_branch="main",
                    import_paths=["../analysis.py"],
                ),
            ),
        )
    assert unsafe_path.value.status_code == 422

    with pytest.raises(HTTPException) as invalid_notebook:
        service.create_project(
            session.user,
            CreateProjectRequest(
                name="Bad Notebook",
                description="Import selected files",
                github_repo=CreateProjectGitHubRepoRequest(
                    mode="existing",
                    owner="octocat",
                    name="existing-repo",
                    base_branch="main",
                    import_paths=["notebooks/demo.ipynb"],
                ),
            ),
        )
    assert invalid_notebook.value.status_code == 422

    repo_manager.importable_files = [
        GitHubRepoFileCandidate(path="src/huge.py", file_kind="python", size=2_000_000),
    ]
    with pytest.raises(HTTPException) as oversized:
        service.create_project(
            session.user,
            CreateProjectRequest(
                name="Huge",
                description="Import selected files",
                github_repo=CreateProjectGitHubRepoRequest(
                    mode="existing",
                    owner="octocat",
                    name="existing-repo",
                    base_branch="main",
                    import_paths=["src/huge.py"],
                ),
            ),
        )
    assert oversized.value.status_code == 422


def test_import_project_notebook_upload_converts_colab_export_to_python_source():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="colab@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None
    _connect_github(service, session.user)
    project = service.create_project(
        session.user,
        _create_repo_project_request("Colab", "Import notebooks", repo_name="colab-lab"),
    )

    imported = service.import_project_notebook_upload(
        session.user,
        project.project_id,
        filename="colab_export.ipynb",
        data=_notebook_json("print('colab')\n").encode("utf-8"),
    )

    assert imported.file_kind == "notebook"
    assert imported.repo_path == "notebooks/colab_export.ipynb"
    assert "# %%" in imported.content
    assert "print('colab')" in imported.content

    service.sync_project_repo(
        session.user,
        project.project_id,
        ProjectRepoSyncRequest(commit_message="Save notebook"),
    )
    synced_files = repo_manager.sync_project_files_calls[0]["files"]
    regenerated_notebook = json.loads(synced_files["notebooks/colab_export.ipynb"])
    assert regenerated_notebook["nbformat"] == 4
    assert "print('colab')" in "".join(regenerated_notebook["cells"][0]["source"])


def test_sync_project_repo_tracks_previous_managed_paths_for_existing_repo_subset():
    repo_manager = FakeGitHubRepoManager()
    repo_manager.file_texts[(".paperbase", "main")] = json.dumps(
        {"managed_paths": [".paperbase", "src/old.py", "src/analysis.py"]}
    )
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="managed@example.com", password="topsecret123", display_name="Managed User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('current')",
    )
    service.repository.save_project_repo_sync(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=project.project_id,
        github_owner_login="octocat",
        github_repo_name="paperbase-general-123abc",
        github_repo_url="https://github.com/octocat/paperbase-general-123abc",
        base_branch="main",
        default_branch="main",
        last_sync_commit_sha="abc123",
        last_sync_commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        last_synced_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    service.sync_project_repo(
        session.user,
        project.project_id,
        ProjectRepoSyncRequest(commit_message="Save managed subset"),
    )

    assert repo_manager.sync_project_files_calls[0]["previous_managed_paths"] == {
        ".paperbase",
        "src/old.py",
        "src/analysis.py",
    }


def test_sync_project_repo_applies_conflict_resolutions_before_retry():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="resolve@example.com", password="topsecret123", display_name="Resolve User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    updated_file = service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('local')",
    )
    service.repository.save_project_repo_sync(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=project.project_id,
        github_repo_name="paperbase-general-123abc",
        github_repo_url="https://github.com/octocat/paperbase-general-123abc",
        default_branch="main",
        last_sync_commit_sha="abc123",
        last_sync_commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        last_synced_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    synced = service.sync_project_repo(
        session.user,
        project.project_id,
        ProjectRepoSyncRequest(
            commit_message="Resolve analysis.py",
            expected_remote_head_sha="def456",
            conflict_resolutions=[
                {
                    "path": "src/analysis.py",
                    "content": "print('merged')",
                }
            ],
        ),
    )

    assert synced.last_sync_commit_sha == "abc123"
    assert service.get_python_file(session.user, updated_file.file_id).content == "print('merged')"
    assert repo_manager.sync_project_files_calls[0]["files"]["src/analysis.py"] == "print('merged')"
    assert repo_manager.sync_project_files_calls[0]["base_commit_sha"] == "abc123"
    assert repo_manager.sync_project_files_calls[0]["expected_remote_head_sha"] == "def456"


def test_sync_project_repo_surfaces_merge_conflicts():
    repo_manager = FakeGitHubRepoManager()
    repo_manager.next_sync_conflict = GitHubSyncConflictError(
        "The project repository changed on GitHub and needs conflict resolution.",
        repo_name="paperbase-general-123abc",
        repo_url="https://github.com/octocat/paperbase-general-123abc",
        default_branch="main",
        base_commit_sha="abc123",
        remote_head_sha="def456",
        conflicts=[
            GitHubConflictFile(
                path="src/analysis.py",
                base_content="print('base')",
                local_content="print('local')",
                remote_content="print('remote')",
            )
        ],
    )
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="conflict@example.com", password="topsecret123", display_name="Conflict User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('local')",
    )
    service.repository.save_project_repo_sync(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=project.project_id,
        github_repo_name="paperbase-general-123abc",
        github_repo_url="https://github.com/octocat/paperbase-general-123abc",
        default_branch="main",
        last_sync_commit_sha="abc123",
        last_sync_commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        last_synced_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.sync_project_repo(
            session.user,
            project.project_id,
            ProjectRepoSyncRequest(commit_message="Save analysis.py"),
        )

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "github_merge_conflict"
    assert detail["remote_head_sha"] == "def456"
    assert detail["conflicts"][0]["path"] == "src/analysis.py"


def test_run_python_file_requires_modal_credentials_and_uses_saved_content():
    modal_runner = FakeModalRunner()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        modal_runner=modal_runner,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="run@example.com", password="topsecret123", display_name="Run User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    python_file = service.create_python_file(session.user, project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename=None,
        content="print('run')",
    )

    with pytest.raises(HTTPException) as excinfo:
        service.run_python_file(session.user, python_file.file_id)
    assert excinfo.value.status_code == 409

    service.save_user_modal_credentials(
        session.user,
        SaveModalCredentialsRequest(
            token_id="modal-token-id-999",
            token_secret="modal-token-secret-999",
        ),
    )
    result = service.run_python_file(session.user, python_file.file_id)

    assert result.status == "completed"
    assert result.stdout == "runner output"
    assert modal_runner.calls == [
        {
            "token_id": "modal-token-id-999",
            "token_secret": "modal-token-secret-999",
            "files": {"src/something.py": "print('run')"},
            "entrypoint_path": "src/something.py",
            "run_mode": "full",
        }
    ]


def test_run_project_python_file_materializes_workspace_and_threads_smoke_mode():
    modal_runner = FakeModalRunner()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        modal_runner=modal_runner,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="workspace@example.com", password="topsecret123", display_name="Workspace User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )
    service.save_user_modal_credentials(
        session.user,
        SaveModalCredentialsRequest(
            token_id="modal-token-id-999",
            token_secret="modal-token-secret-999",
        ),
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    service.create_project_python_file(session.user, project_id, repo_path="pkg/__init__.py", content="")
    service.create_project_python_file(
        session.user,
        project_id,
        repo_path="pkg/utils.py",
        content="MSG = 'hello'\n",
    )
    service.create_project_python_file(
        session.user,
        project_id,
        repo_path="train.py",
        content="from pkg.utils import MSG\nprint(MSG)\n",
    )

    result = service.run_project_python_file(session.user, project_id, repo_path="train.py", mode="smoke")

    assert result.status == "completed"
    assert modal_runner.calls == [
        {
            "token_id": "modal-token-id-999",
            "token_secret": "modal-token-secret-999",
            "files": {
                "pkg/__init__.py": "",
                "pkg/utils.py": "MSG = 'hello'\n",
                "train.py": "from pkg.utils import MSG\nprint(MSG)\n",
            },
            "entrypoint_path": "train.py",
            "run_mode": "smoke",
        }
    ]


def test_browser_uploads_and_chat_require_api_key_before_first_turn(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "chat@example.com", "password": "topsecret123", "display_name": "Chat User"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="Methods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.77777",
        paper=_sample_paper("2401.77777", title="Grounding Paper", abstract="A paper about grounded citations"),
    )

    upload = client.post(
        "/v1/browser/uploads",
        data={"tags": "methods,notes"},
        files={
            "file": ("notes.md", b"# Notes\n\nThese uploaded notes discuss methods and grounding.", "text/markdown")
        },
        headers=csrf_headers,
    )
    assert upload.status_code == 200
    assert upload.json()["tags"] == ["methods", "notes"]

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    gated = client.post(
        f"/v1/browser/chats/{thread_id}/messages",
        json={"content": "Explain the methods grounding with citations", "tag_filters": ["methods"]},
        headers=csrf_headers,
    )
    assert gated.status_code == 200
    assert gated.json()["state"] == "api_key_required"
    assert gated.json()["thread"]["messages"] == []

    save_key = client.put(
        "/v1/browser/settings/openai-key", json={"api_key": "openai-user-key-999"}, headers=csrf_headers
    )
    assert save_key.status_code == 200
    assert save_key.json()["has_openai_api_key"] is True
    csrf_headers = {"X-Paperbase-CSRF": save_key.json()["csrf_token"]}

    resumed = client.post(
        f"/v1/browser/chats/{thread_id}/messages",
        json={"content": "Explain the methods grounding with citations", "tag_filters": ["methods"]},
        headers=csrf_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "completed"
    assert resumed.json()["assistant_message"]["citations"]
    assert chat_model.create_calls
    assert chat_model.create_calls[0]["tool_choice"] == {"type": "auto"}
    assert chat_model.create_calls[0]["tools"]

    app.dependency_overrides.clear()


def test_non_grounded_chat_can_use_tools_without_forcing_research_posture(browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="casual@example.com", password="topsecret123", display_name="Casual User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )

    assert response.assistant_message is not None
    assert response.assistant_message.citations == []
    assert "[S" not in response.assistant_message.content_markdown
    assert len(chat_model.create_calls) == 2
    first_call = chat_model.create_calls[0]
    assert first_call["tool_choice"] == {"type": "auto"}
    assert first_call["tools"]
    first_tool_names = {
        tool["name"] for tool in first_call["tools"] if isinstance(tool, dict) and "name" in tool
    }
    assert "paperbase_search_sections" in first_tool_names
    assert "paperbase_search_uploaded_sections" in first_tool_names
    assert "For casual or non-evidence turns, answer directly when tools are unnecessary." in str(
        first_call["system_prompt"]
    )
    assert "Every tool-enabled turn must begin by leveraging these research tools" not in str(
        first_call["system_prompt"]
    )
    assert chat_model.create_calls[1]["tools"]
    assert chat_model.create_calls[1]["tool_choice"] == {"type": "auto"}
    assert "do not invent source ids" in str(chat_model.create_calls[-1]["system_prompt"])


def test_first_prompt_renames_new_chat_to_short_summary(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="title@example.com", password="topsecret123", display_name="Title User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    first_response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )

    assert first_response.thread.title == "Brainstorm Short Product Name"

    second_response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Now focus on a whimsical version"),
    )

    assert second_response.thread.title == "Brainstorm Short Product Name"


def test_chat_context_uses_full_active_thread_history(browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="history@example.com", password="topsecret123", display_name="History User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    for index in range(8):
        service.repository.add_chat_message(  # type: ignore[attr-defined]
            thread_id=thread.thread_id,
            role="user" if index % 2 == 0 else "assistant",
            content_markdown=f"Historical message {index}",
        )

    service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )

    answer_call = chat_model.create_calls[-1]
    sent_messages = answer_call["messages"]
    assert isinstance(sent_messages, list)
    assert len(sent_messages) == 11
    assert sent_messages[0]["content"] == "Historical message 0"
    assert sent_messages[7]["content"] == "Historical message 7"
    assert sent_messages[8]["content"] == "Help me brainstorm a short product name"


def test_chat_turn_context_skips_empty_reasoning_items(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="reasoning-context@example.com", password="topsecret123", display_name="Reasoning User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    resolved_user = service.repository.get_browser_user_by_id(session.user.user_id) or session.user  # type: ignore[attr-defined]
    prepared = service._prepare_chat_generation(
        user=session.user,
        thread_id=thread.thread_id,
        request=SendChatMessageRequest(content="Follow up on the existing plan"),
        resolved_user=resolved_user,
        api_key_value="openai-user-key-999",
    )
    reasoning_item = ChatThreadItemResponse(
        item_id="item_reasoning",
        thread_id=thread.thread_id,
        turn_id=prepared.turn.turn_id,
        item_type="reasoning",
        role="assistant",
        content_text="",
        content_json=None,
        citations=[],
        created_at=prepared.user_item.created_at,
        status="completed",
    )
    prepared = prepared.__class__(
        thread=prepared.thread.model_copy(update={"items": [*prepared.thread.items, reasoning_item]}),
        turn=prepared.turn,
        user_item=prepared.user_item,
        resolved_user=prepared.resolved_user,
        api_key_value=prepared.api_key_value,
    )

    context = service._chat_turn_context(prepared)

    assert not any(item.get("type") == "reasoning" for item in context.prompt_input)


def test_chat_input_function_calls_omit_internal_item_ids(browser_service):
    service, _, _ = browser_service
    item = ChatThreadItemResponse(
        item_id="item_internal",
        thread_id="thread_test",
        turn_id="turn_test",
        item_type="function_call",
        role="assistant",
        call_id="call_external",
        name="project_list_files",
        content_text="{}",
        content_json={},
        citations=[],
        created_at=datetime.now(timezone.utc),
        status="completed",
    )

    normalized = service._chat_input_item_from_thread_item(item)

    assert normalized == {
        "type": "function_call",
        "call_id": "call_external",
        "name": "project_list_files",
        "arguments": "{}",
    }


def test_stream_chat_compacts_mid_turn_after_tool_results(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    chat_model.token_counts = [1, 950_000, 1]
    session, _ = service.register_browser_user(
        SignupRequest(email="compact@example.com", password="topsecret123", display_name="Compact User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Methods\n\nMethods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.77777",
        paper=_sample_paper("2401.77777", title="Compaction Paper", abstract="A paper about tool results"),
    )

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    events = list(
        service.stream_chat_message(
            session.user,
            thread.thread_id,
            SendChatMessageRequest(content="Explain the methods grounding with citations", tag_filters=[]),
        )
    )

    function_outputs = _event_items(events, event_name="item_completed", item_type="function_call_output")
    compaction_items = _event_items(events, event_name="item_completed", item_type="compaction")
    assistant_final_items = _event_items(
        events,
        event_name="item_completed",
        item_type="assistant_message",
        phase="final_answer",
    )
    assert function_outputs
    assert compaction_items
    assert assistant_final_items
    first_function_output_index = next(
        index
        for index, (event_name, payload) in enumerate(events)
        if event_name == "item_completed" and payload.get("item", {}).get("item_type") == "function_call_output"
    )
    first_compaction_index = next(
        index
        for index, (event_name, payload) in enumerate(events)
        if event_name == "item_completed" and payload.get("item", {}).get("item_type") == "compaction"
    )
    first_final_answer_index = next(
        index
        for index, (event_name, payload) in enumerate(events)
        if event_name == "item_completed"
        and payload.get("item", {}).get("item_type") == "assistant_message"
        and payload.get("item", {}).get("phase") == "final_answer"
    )
    assert first_compaction_index > first_function_output_index
    assert first_compaction_index < first_final_answer_index
    assert compaction_items[0]["item_type"] == "compaction"
    assert "Compacted Conversation" in compaction_items[0]["content_text"]

    compaction_call = next(call for call in chat_model.create_calls if call["system_prompt"] == "")
    assert "Compact the conversation transcript" in str(compaction_call["messages"])
    assert "Active Source IDs" in str(compaction_call["messages"])
    assert "Explain the methods grounding" in str(compaction_call["messages"])

    final_sample_call = next(
        call
        for call in reversed(chat_model.create_calls)
        if call["system_prompt"] and call["system_prompt"] != ""
    )
    assert final_sample_call["messages"][0]["role"] == "developer"
    assert "Conversation compaction checkpoint" in final_sample_call["messages"][0]["content"]
    assert chat_model.create_calls

    refreshed = service.get_chat_thread(session.user, thread.thread_id)
    assert refreshed is not None
    assert any(message.kind == "compaction" for message in refreshed.messages)
    assert any(message.role == "user" and "Explain the methods grounding" in message.content_markdown for message in refreshed.messages)
    assert refreshed.messages[-1].citations


def test_search_only_tool_results_do_not_create_citations(monkeypatch):
    class SearchOnlyChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            if tools:
                has_tool_results = any(
                    message.get("role") == "user"
                    and isinstance(message.get("content"), list)
                    and any(block.get("type") == "tool_result" for block in message.get("content"))
                    for message in messages
                    if isinstance(message, dict)
                )
                if tool_choice == {"type": "any"} or not has_tool_results:
                    return {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_search_only",
                                "name": "paperbase_search",
                                "input": {"query": "grounding", "limit": 3},
                            }
                        ],
                        "stop_reason": "tool_use",
                    }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Grounded answer without evidence citations [S1].",
                    }
                ],
                "stop_reason": "end_turn",
            }

    chat_model = SearchOnlyChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="search-only@example.com", password="topsecret123", display_name="Search Only")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="Methods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2403.00001",
        paper=_sample_paper("2403.00001", title="Grounding Paper", abstract="A paper about grounded citations"),
    )

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Explain the methods grounding with citations"),
    )

    assert response.assistant_message is not None
    assert response.assistant_message.citations == []
    assert "[S" not in response.assistant_message.content_markdown


def test_link_requests_expose_metadata_guidance_for_paper_links(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="links@example.com", password="topsecret123", display_name="Links User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Methods\n\nGrounding methods and benchmark comparisons."),
    )
    service.index_arxiv_paper(
        "2402.42424",
        paper=_sample_paper("2402.42424", title="Grounding Links Paper", abstract="A paper with useful metadata"),
    )

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Give me links to papers about grounding"),
    )

    assert chat_model.create_calls[0]["tool_choice"] == {"type": "auto"}
    assert "html_url or pdf_url" in str(chat_model.create_calls[0]["system_prompt"])
    assert "When the user asks for paper links or reading lists" in str(chat_model.create_calls[0]["system_prompt"])


def test_project_defaults_and_thread_snapshots(browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="projects@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)

    initial_projects = service.list_projects(session.user).projects
    assert len(initial_projects) == 1
    assert initial_projects[0].name == "General"
    assert initial_projects[0].is_default is True
    assert initial_projects[0].session_count == 0

    project = service.create_project(
        session.user,
        _create_repo_project_request("Agents", "Use grounded citations only."),
    )
    assert project.github_repo_url == "https://github.com/octocat/agents"
    thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="Agent chat", project_id=project.project_id),
    )

    assert thread.project_id == project.project_id
    assert thread.project_name == "Agents"
    assert thread.project_instructions == "Use grounded citations only."

    projects_after_thread = service.list_projects(session.user).projects
    agents_project = next(item for item in projects_after_thread if item.project_id == project.project_id)
    assert agents_project.session_count == 1
    assert agents_project.github_repo_url == "https://github.com/octocat/agents"

    service.update_project(
        session.user,
        project.project_id,
        UpdateProjectRequest(name="Agents", description="Use different instructions now."),
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )

    assert response.thread.project_instructions == "Use grounded citations only."
    assert chat_model.create_calls
    assert "Use grounded citations only." in str(chat_model.create_calls[-1]["system_prompt"])
    assert "Use different instructions now." not in str(chat_model.create_calls[-1]["system_prompt"])


def test_project_session_paging_and_thread_deletion(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="paging@example.com", password="topsecret123", display_name="Paging User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    project = service.create_project(
        session.user,
        _create_repo_project_request("Benchmarks", "Track benchmark sessions"),
    )
    threads = [
        service.create_chat_thread(
            session.user,
            CreateChatThreadRequest(title=f"Thread {index}", project_id=project.project_id),
        )
        for index in range(6)
    ]

    first_page = service.list_project_chat_threads(session.user, project.project_id, limit=5, offset=0)
    second_page = service.list_project_chat_threads(session.user, project.project_id, limit=5, offset=5)

    assert len(first_page.threads) == 5
    assert first_page.total_count == 6
    assert first_page.has_more is True
    assert all(item.project_id == project.project_id for item in first_page.threads)
    assert len(second_page.threads) == 1
    assert second_page.has_more is False

    service.delete_chat_thread(session.user, threads[0].thread_id)

    remaining_page = service.list_project_chat_threads(session.user, project.project_id, limit=10, offset=0)
    assert remaining_page.total_count == 5
    assert threads[0].thread_id not in {item.thread_id for item in remaining_page.threads}
    remaining_project = next(
        item for item in service.list_projects(session.user).projects if item.project_id == project.project_id
    )
    assert remaining_project.session_count == 5


def test_project_items_can_page_by_kind(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="item-kinds@example.com", password="topsecret123", display_name="Item Kind User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    project = service.create_project(
        session.user,
        _create_repo_project_request("Mixed Items", "Track files and sessions"),
    )
    service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="Session 1", project_id=project.project_id),
    )
    service.create_python_file(session.user, project.project_id)
    service.create_python_file(session.user, project.project_id)

    code_page = service.list_project_items(
        session.user,
        project.project_id,
        limit=1,
        offset=0,
        item_kind="python_file",
    )
    session_page = service.list_project_items(
        session.user,
        project.project_id,
        limit=25,
        offset=0,
        item_kind="thread",
    )

    assert code_page.total_count == 2
    assert code_page.has_more is True
    assert len(code_page.items) == 1
    assert all(item.item_kind == "python_file" for item in code_page.items)
    assert session_page.total_count == 1
    assert session_page.items[0].item_kind == "thread"


def test_chat_answer_prompt_requires_strict_markdown(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="markdown@example.com", password="topsecret123", display_name="Markdown User")
    )
    assert session.user is not None

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="Markdown prompt"))

    with_evidence = service._chat_answer_system_prompt(thread=thread, has_evidence_sources=True)
    without_evidence = service._chat_answer_system_prompt(thread=thread, has_evidence_sources=False)

    assert "strict GitHub-flavored markdown" in with_evidence
    assert "valid markdown that a standard parser can render directly" in with_evidence
    assert "When tools are still needed, emit tool calls only" in with_evidence
    assert "Once the tool results are sufficient, stop calling tools and answer directly in the same turn." in with_evidence
    assert "strict GitHub-flavored markdown" in without_evidence
    assert "valid markdown that a standard parser can render directly" in without_evidence
    assert with_evidence == without_evidence


def test_empty_chat_can_move_between_projects(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="move-empty@example.com", password="topsecret123", display_name="Move Empty")
    )
    assert session.user is not None
    _save_browser_openai_key(service, session.user)
    _connect_github(service, session.user)

    project = service.create_project(
        session.user,
        _create_repo_project_request("Agents", "Study agent systems"),
    )
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))

    moved = service.move_chat_thread_to_project(
        session.user,
        thread.thread_id,
        MoveChatThreadProjectRequest(project_id=project.project_id),
    )

    assert moved.project_id == project.project_id
    assert moved.project_name == "Agents"
    assert moved.project_instructions == "Study agent systems"

    service.send_chat_message(
        session.user,
        moved.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )
    with pytest.raises(HTTPException) as exc:
        service.move_chat_thread_to_project(
            session.user,
            moved.thread_id,
            MoveChatThreadProjectRequest(project_id=project.project_id),
        )
    assert exc.value.status_code == 409


def test_delete_project_cascades_threads_files_and_allows_default_removal(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="cascade@example.com", password="topsecret123", display_name="Cascade User")
    )
    assert session.user is not None
    _connect_github(service, session.user)

    default_project = service.list_projects(session.user).projects[0]
    thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="Disposable thread", project_id=default_project.project_id),
    )
    python_file = service.create_python_file(session.user, default_project.project_id)

    service.delete_project(session.user, default_project.project_id)

    assert service.list_projects(session.user).projects == []
    with pytest.raises(HTTPException) as thread_exc:
        service.get_chat_thread(session.user, thread.thread_id)
    assert thread_exc.value.status_code == 404
    with pytest.raises(HTTPException) as file_exc:
        service.get_python_file(session.user, python_file.file_id)
    assert file_exc.value.status_code == 404
    with pytest.raises(HTTPException) as create_exc:
        service.create_chat_thread(
            session.user,
            CreateChatThreadRequest(title="Need a project"),
        )
    assert create_exc.value.status_code == 409

    replacement_project = service.create_project(
        session.user,
        _create_repo_project_request("Replacement", "Rebuild workspace"),
    )
    replacement_thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="Recovered chat"),
    )

    assert replacement_thread.project_id == replacement_project.project_id


def test_browser_chat_stream_emits_citations_and_completion(monkeypatch, browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream@example.com", "password": "topsecret123", "display_name": "Stream User"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}
    user = service.repository.get_browser_user_by_id(signup.json()["user"]["user_id"])  # type: ignore[attr-defined]
    assert user is not None
    _save_browser_openai_key(service, user)

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Methods\n\nMethods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.88888",
        paper=_sample_paper("2401.88888", title="Stream Grounding Paper", abstract="A paper about streaming citations"),
    )

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "Explain the methods grounding with citations", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    event_names = [event_name for event_name, _ in events]
    assert event_names[:3] == ["turn_started", "item_started", "item_completed"]
    function_call_items = _event_items(events, event_name="item_completed", item_type="function_call")
    function_output_items = _event_items(events, event_name="item_completed", item_type="function_call_output")
    assistant_final_items = _event_items(
        events,
        event_name="item_completed",
        item_type="assistant_message",
        phase="final_answer",
    )
    assert function_call_items
    assert function_call_items[0]["name"] == "paperbase_search_sections"
    assert function_call_items[0]["content_json"] == {"query": "grounding", "limit": 3}
    assert function_output_items
    assert '"sections": [{' in function_output_items[0]["content_text"]
    assert assistant_final_items
    assert "usage" in event_names
    assert event_names[-1] == "turn_completed"
    usage_indices = [index for index, event_name in enumerate(event_names) if event_name == "usage"]
    assert len(usage_indices) >= 2
    tool_usage_index = usage_indices[0]
    final_usage_index = usage_indices[-1]
    assert events[tool_usage_index][1]["input_tokens"] == 84512
    assert events[tool_usage_index][1]["output_tokens"] == 98
    assert events[tool_usage_index][1]["context_window_tokens"] > 0
    assert events[tool_usage_index][1]["context_window_tokens"] == events[final_usage_index][1]["context_window_tokens"]
    assert events[final_usage_index][1]["input_tokens"] == 11234
    assert assistant_final_items[0]["citations"]
    assert events[-1][1]["final_item"]["status"] == "completed"
    assert events[-1][1]["usage"]["output_tokens"] == 125
    assert service.chat_model.create_calls

    refreshed = client.get(f"/v1/browser/chats/{thread_id}").json()
    assert refreshed["messages"][-1]["status"] == "completed"
    assert refreshed["messages"][-1]["citations"]

    app.dependency_overrides.clear()


def test_browser_chat_stream_handles_api_key_required_without_placeholder(browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream-gated@example.com", "password": "topsecret123", "display_name": "Gated User"},
    )
    assert signup.status_code == 200
    payload = signup.json()
    csrf_headers = {"X-Paperbase-CSRF": payload["csrf_token"]}

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "One more answer please", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    assert [event_name for event_name, _ in events] == ["api_key_required"]
    assert events[0][1]["state"] == "api_key_required"
    assert events[0][1]["assistant_message"] is None
    assert events[0][1]["thread"]["messages"] == []

    app.dependency_overrides.clear()


def test_browser_chat_stream_falls_back_when_provider_stream_fails_before_text(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream-fail@example.com", "password": "topsecret123", "display_name": "Fail User"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}
    user = service.repository.get_browser_user_by_id(signup.json()["user"]["user_id"])  # type: ignore[attr-defined]
    assert user is not None
    _save_browser_openai_key(service, user)

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="Methods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.99999",
        paper=_sample_paper("2401.99999", title="Failure Paper", abstract="A paper about stream failure"),
    )
    chat_model.fail_stream_after_chunks = 0

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "Explain the methods grounding with citations", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    event_names = [event_name for event_name, _ in events]
    assert event_names[-1] == "turn_completed"
    assert "turn_failed" not in event_names
    assert chat_model.create_calls
    refreshed = client.get(f"/v1/browser/chats/{thread_id}").json()
    assert refreshed["messages"][-1]["status"] == "completed"

    app.dependency_overrides.clear()


def test_browser_chat_stream_allows_project_answer_without_persisted_write(browser_service):
    class ProjectAnswerChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            return {
                "content": [{"type": "text", "text": "```python\ndef recurse(items):\n    return items\n```"}],
                "stop_reason": "end_turn",
            }

        def stream_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            raise AssertionError("stream_message should not be used by the codex-style kernel")

    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=ProjectAnswerChatModel(),
        github_repo_manager=FakeGitHubRepoManager(),
    )
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream-code-answer@example.com", "password": "topsecret123", "display_name": "Stream Code"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}
    user = service.repository.get_browser_user_by_id(signup.json()["user"]["user_id"])  # type: ignore[attr-defined]
    assert user is not None
    _save_browser_openai_key(service, user)

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "Explain recursion briefly", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    assert events[-1][0] == "turn_completed"
    assert events[-1][1]["final_item"]["content_text"] == "```python\ndef recurse(items):\n    return items\n```"
    refreshed = client.get(f"/v1/browser/chats/{thread_id}").json()
    assert refreshed["messages"][-1]["content_markdown"] == events[-1][1]["final_item"]["content_text"]

    app.dependency_overrides.clear()


def test_browser_chat_stream_can_apply_project_edit_and_answer_in_same_loop():
    class ProjectEditChatModel(FakeChatModel):
        def __init__(self) -> None:
            super().__init__()
            self.used_tool = False

        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            if tools and not self.used_tool:
                self.used_tool = True
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_write_file",
                            "name": "project_write_file",
                            "input": {
                                "repo_path": "src/linear_gd.py",
                                "content": "def train_linear_gd():\n    return 1\n",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            return {
                "content": [{"type": "text", "text": "Saved the module to `src/linear_gd.py`."}],
                "stop_reason": "end_turn",
            }

        def stream_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.stream_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            if tools and not self.used_tool:
                self.used_tool = True
                yield ChatModelStreamEvent(
                    response={
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_write_file",
                                "name": "project_write_file",
                                "input": {
                                    "repo_path": "src/linear_gd.py",
                                    "content": "def train_linear_gd():\n    return 1\n",
                                },
                            }
                        ],
                        "stop_reason": "tool_use",
                    }
                )
                return
            yield ChatModelStreamEvent(text="Saved the module to `src/linear_gd.py`.")
            yield ChatModelStreamEvent(
                response={
                    "content": [{"type": "text", "text": "Saved the module to `src/linear_gd.py`."}],
                    "stop_reason": "end_turn",
                }
            )

    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=ProjectEditChatModel(),
        github_repo_manager=FakeGitHubRepoManager(),
    )
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream-project-write@example.com", "password": "topsecret123", "display_name": "Stream Write"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}
    user_id = signup.json()["user"]["user_id"]
    user = service.repository.get_browser_user_by_id(user_id)  # type: ignore[attr-defined]
    assert user is not None
    _save_browser_openai_key(service, user)
    _connect_github(service, user)

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]
    project_id = thread["project_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "Write some code to train a small linear gradient descent with pytorch", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    assert events[-1][0] == "turn_completed"
    saved_file = service.repository.get_project_python_file_by_path(  # type: ignore[attr-defined]
        user_id=user.user_id,
        project_id=project_id,
        repo_path="src/linear_gd.py",
    )
    assert saved_file is not None
    assert saved_file.content == "def train_linear_gd():\n    return 1\n"
    assert events[-1][1]["final_item"]["content_text"] == "Saved the module to `src/linear_gd.py`."

    app.dependency_overrides.clear()


def test_browser_paper_read_endpoints(monkeypatch, browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "papers@example.com", "password": "topsecret123", "display_name": "Paper User"},
    )
    assert signup.status_code == 200

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers, **_: None)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Introduction\n\nGrounded section content."),
    )
    indexed = service.index_arxiv_paper(
        "2402.00001",
        paper=_sample_paper("2402.00001", title="Drawer Paper", abstract="A paper for the drawer"),
    )

    paper = client.get(f"/v1/browser/papers/{indexed.paper_id}")
    assert paper.status_code == 200
    assert paper.json()["title"] == "Drawer Paper"

    file_response = client.get(f"/v1/browser/papers/{indexed.paper_id}/files/sections/introduction.md")
    assert file_response.status_code == 200
    assert "Grounded section content." in file_response.json()["content"]

    figure = client.get(f"/v1/browser/papers/{indexed.paper_id}/figures/figure-1")
    assert figure.status_code == 200
    assert figure.json()["caption"] == "A model architecture."

    table = client.get(f"/v1/browser/papers/{indexed.paper_id}/tables/table-1")
    assert table.status_code == 200
    assert table.json()["rows"][1] == ["Paperbase", "1.0"]

    app.dependency_overrides.clear()


def test_browser_write_endpoints_require_csrf(browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "csrf@example.com", "password": "topsecret123", "display_name": "CSRF User"},
    )
    assert signup.status_code == 200
    assert signup.json()["csrf_token"]

    missing_csrf = client.post("/v1/browser/chats", json={"title": "No token"})
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["detail"] == "Invalid CSRF token"

    wrong_origin = client.post(
        "/v1/browser/chats",
        json={"title": "Wrong origin"},
        headers={"X-Paperbase-CSRF": signup.json()["csrf_token"], "Origin": "https://evil.example"},
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["detail"] == "Cross-site browser requests are not allowed"

    app.dependency_overrides.clear()


def test_browser_google_start_redirects_back_to_return_to_when_not_configured(monkeypatch, service: PaperbaseService):
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    monkeypatch.setattr("paperbase.api.get_google_oauth_client", lambda: None)

    response = client.get(
        "/v1/browser/auth/google/start",
        params={"return_to": "http://127.0.0.1:8080"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://127.0.0.1:8080/?auth_error=google_not_configured"

    app.dependency_overrides.clear()


def test_chat_search_tool_threads_rerank_flag_into_search_request():
    """The paperbase_search tool must advertise a `rerank` input and pass it
    through to SearchRequest so chat/MCP flows can use the deep-search path."""
    from paperbase.service import ChatCitationRegistry

    service = PaperbaseService(
        settings=Settings(storage_backend="memory"),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()

    request = SendChatMessageRequest(content="find papers about attention", include_papers=True)
    tools = service._chat_tool_definitions(request)
    search_tool = next((t for t in tools if t["name"] == "paperbase_search"), None)
    assert search_tool is not None, "paperbase_search tool should be defined"
    assert "rerank" in search_tool["input_schema"]["properties"], (
        "paperbase_search tool schema must advertise the rerank input"
    )

    captured: list[bool] = []

    class RecordingService(type(service)):
        pass

    original_search = service.search

    def recording_search(req):
        captured.append(req.rerank)
        return original_search(req)

    service.search = recording_search  # type: ignore[method-assign]

    registry = ChatCitationRegistry()
    fake_user = type("U", (), {"user_id": "u1"})()

    service._execute_chat_tool(
        tool_name="paperbase_search",
        tool_input={"query": "attention", "rerank": True},
        user=fake_user,
        citation_registry=registry,
    )
    assert captured == [True], "rerank=True from tool_input did not reach SearchRequest"

    service._execute_chat_tool(
        tool_name="paperbase_search",
        tool_input={"query": "attention"},
        user=fake_user,
        citation_registry=registry,
    )
    assert captured == [True, False], "rerank default must be False when tool_input omits it"


def test_memory_repository_search_local_bakes_in_citation_and_recency_boosts():
    """MemoryPaperRepository.search_local must apply citation + recency boosts
    into the returned score, mirroring SqlAlchemyPaperRepository. service.search
    skips re-applying citation_boost to local hits, so memory mode would lose
    the boost entirely without this."""
    repo = MemoryPaperRepository()
    matched_query = "shared rare niche topic"

    def _meta(arxiv_id: str, *, cites: int, year: str) -> PaperMetadata:
        return PaperMetadata(
            arxiv_id=arxiv_id,
            title=f"Paper about {matched_query}",
            authors="A",
            abstract="abstract text",
            categories="cs.AI",
            primary_category="cs.AI",
            published=year,
            updated=year,
            pdf_url=f"https://example.test/{arxiv_id}.pdf",
            html_url=f"https://example.test/{arxiv_id}",
            citation_count=cites,
            index_status="indexed",
        )

    repo.upsert_paper(_meta("low/0001", cites=0, year="2018-01-01"), search_document="", embedding=None)
    repo.upsert_paper(_meta("high/0001", cites=10_000, year="2018-01-01"), search_document="", embedding=None)

    no_boost = repo.search_local(
        matched_query, limit=5, query_embedding=None, citation_boost_max=0.0, recency_boost_max=0.0
    )
    with_boost = repo.search_local(matched_query, limit=5, query_embedding=None)

    no_boost_by_id = {r.arxiv_id: r.score or 0 for r in no_boost}
    with_boost_by_id = {r.arxiv_id: r.score or 0 for r in with_boost}

    # With boosts disabled, both papers have the same lexical-only score.
    assert no_boost_by_id["low/0001"] == no_boost_by_id["high/0001"]
    # With boosts enabled, the highly-cited paper picks up additional score.
    assert with_boost_by_id["high/0001"] > with_boost_by_id["low/0001"]
    assert with_boost_by_id["high/0001"] > no_boost_by_id["high/0001"]


def test_memory_repository_search_local_surfaces_caption_only_match():
    repo = MemoryPaperRepository()
    query = "captiononlyneedle"
    paper = PaperMetadata(
        arxiv_id="caption/0001",
        title="Unrelated title",
        authors="A",
        abstract="abstract text",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="https://example.test/caption.pdf",
        html_url="https://example.test/caption",
        citation_count=0,
        index_status="indexed",
    )
    stored = repo.upsert_paper(paper, search_document="unrelated search document", embedding=None)
    repo.replace_text_chunks(
        stored.paper_id,
        [
            {
                "path": "figures/figure-1/caption.md",
                "chunk_kind": "figure_caption",
                "content": f"Caption text with {query}.",
            }
        ],
    )

    results = repo.search_local(query, limit=5, query_embedding=None)
    sections = repo.search_sections(query, limit=5, paper_ids=[stored.paper_id], query_embedding=None)

    assert [result.paper_id for result in results] == [stored.paper_id]
    assert sections
    assert sections[0].chunk_kind == "figure_caption"


def test_query_prefix_for_model_matches_bge_e5_and_leaves_others_unchanged():
    """Retrieval models like BGE / E5 need a model-specific prefix prepended
    to the query (but not the document) at encode time. SPECTER2 and generic
    models get no prefix. Regressing this silently loses retrieval recall."""
    from paperbase.service import _query_prefix_for_model

    assert _query_prefix_for_model("BAAI/bge-base-en-v1.5") == (
        "Represent this sentence for searching relevant passages: "
    )
    assert _query_prefix_for_model("BAAI/bge-large-en-v1.5") == (
        "Represent this sentence for searching relevant passages: "
    )
    assert _query_prefix_for_model("intfloat/e5-large-v2") == "query: "
    assert _query_prefix_for_model("intfloat/multilingual-e5-large") == "query: "
    assert _query_prefix_for_model("allenai/specter2_base") == ""
    assert _query_prefix_for_model("sentence-transformers/all-MiniLM-L6-v2") == ""
    assert _query_prefix_for_model(None) == ""
    assert _query_prefix_for_model("") == ""


def test_encode_prepends_query_prefix_for_bge_but_not_for_specter2(monkeypatch):
    """The model-aware prefix must be injected in encode() (query side) and
    must not affect encode_batch() callers (document side) or encode_paper*."""
    from paperbase.config import Settings
    from paperbase.service import OptionalEmbeddingProvider

    import numpy as np

    class _FakeModel:
        def __init__(self):
            self.calls: list[list[str]] = []

        def encode(self, texts, **_):
            # Record what actually reached the underlying model, so we can
            # verify the prefix arrived (or did not) exactly as intended.
            self.calls.append(list(texts))
            return np.zeros((len(texts), 768), dtype="float32")

    # BGE-base -> prefix applied in encode(), not in encode_batch()
    bge_settings = Settings(
        storage_backend="memory", database_url="memory://", api_key="t",
        summary_provider="none",
        embedding_model="BAAI/bge-base-en-v1.5", embedding_dim=768,
    )
    bge_provider = OptionalEmbeddingProvider(bge_settings)
    bge_model = _FakeModel()
    monkeypatch.setattr(bge_provider, "_model", lambda: bge_model)

    bge_provider.encode("vision transformer")
    assert bge_model.calls[-1] == [
        "Represent this sentence for searching relevant passages: vision transformer"
    ]

    bge_provider.encode_batch(["doc one", "doc two"])
    assert bge_model.calls[-1] == ["doc one", "doc two"], (
        "encode_batch is the document-side entry point and must not apply the query prefix"
    )

    # SPECTER2 -> no prefix
    specter_settings = Settings(
        storage_backend="memory", database_url="memory://", api_key="t",
        summary_provider="none",
        embedding_model="allenai/specter2_base", embedding_dim=768,
    )
    specter_provider = OptionalEmbeddingProvider(specter_settings)
    specter_model = _FakeModel()
    monkeypatch.setattr(specter_provider, "_model", lambda: specter_model)
    specter_provider.encode("vision transformer")
    assert specter_model.calls[-1] == ["vision transformer"]


# -- worker retry policy (#32) -----------------------------------------------


def _retry_service() -> PaperbaseService:
    settings = Settings(storage_backend="memory", database_url="memory://", api_key="t")
    service = PaperbaseService(
        settings=settings,
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()
    return service


def test_retryable_exception_requeues_with_future_next_run_at(monkeypatch):
    """RetryableError on the first attempt should leave the job in
    ``pending`` with ``next_run_at`` set to a future timestamp and
    ``attempts=1``."""
    from datetime import UTC, datetime, timedelta
    from paperbase.errors import RetryableError

    service = _retry_service()
    service.repository.create_job("index_paper", {"arxiv_id": "test/1"})

    def raise_retryable(arxiv_id, force=False):
        raise RetryableError("upstream 503")

    monkeypatch.setattr(service, "index_arxiv_paper", raise_retryable)

    processed = service.process_next_job()
    assert processed is not None
    assert processed.status == "pending"
    assert processed.attempts == 1
    assert processed.next_run_at is not None
    assert processed.next_run_at > datetime.now(UTC) + timedelta(seconds=10), (
        "first retry should be scheduled at least ~10s out, got "
        f"{processed.next_run_at}"
    )
    assert "upstream 503" in (processed.error or "")


def test_permanent_exception_dead_letters_immediately(monkeypatch):
    """PermanentError must promote the job to ``dead`` on first failure
    regardless of attempts remaining."""
    from paperbase.errors import PermanentError

    service = _retry_service()
    job = service.repository.create_job("index_paper", {"arxiv_id": "test/2"})
    assert job.max_attempts >= 2

    def raise_permanent(arxiv_id, force=False):
        raise PermanentError("arxiv 404 — paper does not exist")

    monkeypatch.setattr(service, "index_arxiv_paper", raise_permanent)

    processed = service.process_next_job()
    assert processed is not None
    assert processed.status == "dead"
    assert processed.attempts == 1
    assert processed.next_run_at is None
    assert "arxiv 404" in (processed.error or "")


def test_attempts_exhaustion_promotes_to_dead(monkeypatch):
    """A retryable failure on the final attempt must dead-letter, not loop
    forever. Drives the job all the way through max_attempts."""
    from datetime import UTC, datetime, timedelta
    from paperbase.errors import RetryableError

    service = _retry_service()
    job = service.repository.create_job("index_paper", {"arxiv_id": "test/3"}, max_attempts=2)
    assert job.max_attempts == 2

    def raise_retryable(arxiv_id, force=False):
        raise RetryableError("transient")

    monkeypatch.setattr(service, "index_arxiv_paper", raise_retryable)

    first = service.process_next_job()
    assert first is not None and first.status == "pending" and first.attempts == 1
    # Force any scheduled next_run_at into the past so the second claim picks
    # the job back up immediately. Production waits for the backoff window.
    service.repository.jobs[first.job_id] = service.repository.jobs[first.job_id].model_copy(
        update={"next_run_at": datetime.now(UTC) - timedelta(seconds=1)}
    )

    second = service.process_next_job()
    assert second is not None
    assert second.status == "dead"
    assert second.attempts == 2


def test_uncaught_exception_defaults_to_retryable(monkeypatch):
    """Per the errors-module docstring: an uncaught (non-Permanent) exception
    is treated as retryable. Safer to retry a bug than to silently drop work."""
    service = _retry_service()
    service.repository.create_job("index_paper", {"arxiv_id": "test/4"})

    def raise_random(arxiv_id, force=False):
        raise RuntimeError("oops, programmer error")

    monkeypatch.setattr(service, "index_arxiv_paper", raise_random)

    processed = service.process_next_job()
    assert processed is not None
    assert processed.status == "pending"
    assert processed.attempts == 1


def test_claim_skips_jobs_whose_next_run_at_is_in_the_future():
    """A job with a future ``next_run_at`` must not be claimable until the
    backoff window elapses, even if it's the only pending job in the queue."""
    from datetime import UTC, datetime, timedelta

    service = _retry_service()
    job = service.repository.create_job("index_paper", {"arxiv_id": "test/5"})
    service.repository.jobs[job.job_id] = service.repository.jobs[job.job_id].model_copy(
        update={"next_run_at": datetime.now(UTC) + timedelta(hours=1)}
    )

    assert service.repository.claim_pending_job() is None


def test_retry_dead_job_resets_attempts_and_unblocks_claim():
    """The admin retry path must reset attempts to 0, clear the error, and
    re-make the job claimable. Confirms the round-trip from dead → pending →
    running."""
    from paperbase.errors import PermanentError

    service = _retry_service()
    service.repository.create_job("index_paper", {"arxiv_id": "test/6"})

    def raise_permanent(arxiv_id, force=False):
        raise PermanentError("nope")

    import unittest.mock
    with unittest.mock.patch.object(service, "index_arxiv_paper", raise_permanent):
        dead = service.process_next_job()
    assert dead is not None and dead.status == "dead"

    requeued = service.retry_dead_job(dead.job_id)
    assert requeued is not None
    assert requeued.status == "pending"
    assert requeued.attempts == 0
    assert requeued.error is None

    claimed = service.repository.claim_pending_job()
    assert claimed is not None
    assert claimed.job_id == dead.job_id
    assert claimed.status == "running"
    assert claimed.attempts == 1


def test_retry_dead_job_returns_none_for_non_dead_job():
    """The admin retry path must refuse to clobber a still-running or
    completed job. Returning None lets the API translate to 404."""
    service = _retry_service()
    job = service.repository.create_job("index_paper", {"arxiv_id": "test/7"})
    assert service.retry_dead_job(job.job_id) is None
    assert service.retry_dead_job("job_nonexistent") is None


def test_list_dead_jobs_includes_legacy_failed_rows():
    """Rows written before the dead-letter state existed used terminal
    ``failed``. Operators should still discover them through the new
    dead-letter list instead of needing a separate status filter."""
    service = _retry_service()
    dead = service.repository.create_job("index_paper", {"arxiv_id": "test/dead"})
    legacy_failed = service.repository.create_job("index_paper", {"arxiv_id": "test/failed"})
    service.repository.jobs[dead.job_id] = dead.model_copy(update={"status": "dead", "error": "dead"})
    service.repository.jobs[legacy_failed.job_id] = legacy_failed.model_copy(
        update={"status": "failed", "error": "legacy failed"}
    )

    jobs = service.list_dead_jobs()

    assert {job.job_id for job in jobs} == {dead.job_id, legacy_failed.job_id}
    assert {job.status for job in jobs} == {"dead", "failed"}


def test_mark_job_completed_clears_stale_retry_metadata(monkeypatch):
    """Regression test for PR #36 review: a job that fails once, is
    requeued, and then succeeds must not keep the old ``error`` /
    ``next_run_at`` in its completed ``JobResponse``. Otherwise ``/jobs``,
    ``service.get_job``, and the worker's ``last_error`` report a stale
    transient failure long after the retry succeeded."""
    from paperbase.errors import RetryableError

    service = _retry_service()
    service.repository.create_job("index_paper", {"arxiv_id": "test/8"})

    # First attempt: fails transiently and gets requeued with next_run_at set.
    def fail_once(arxiv_id, force=False):
        raise RetryableError("upstream 503")

    monkeypatch.setattr(service, "index_arxiv_paper", fail_once)
    failed = service.process_next_job()
    assert failed is not None
    assert failed.status == "pending"
    assert failed.error == "upstream 503"
    assert failed.next_run_at is not None

    # Move next_run_at into the past so the retry claim fires immediately.
    from datetime import UTC, datetime, timedelta

    service.repository.jobs[failed.job_id] = service.repository.jobs[failed.job_id].model_copy(
        update={"next_run_at": datetime.now(UTC) - timedelta(seconds=1)}
    )

    # Second attempt succeeds.
    monkeypatch.setattr(service, "index_arxiv_paper", lambda arxiv_id, force=False: None)
    succeeded = service.process_next_job()
    assert succeeded is not None
    assert succeeded.status == "completed"
    assert succeeded.error is None, (
        f"completed job must not surface a stale transient error; got {succeeded.error!r}"
    )
    assert succeeded.next_run_at is None, (
        "completed job must not surface a stale next_run_at from the prior failure"
    )


def test_enrich_missing_metadata_returns_stats_when_s2_batches_fail(monkeypatch):
    """Direct operator calls must get a stats dict back even when some S2
    batches fail, so the CLI can print scanned/updated/batches_failed instead
    of ending with a RetryableError traceback."""
    from datetime import UTC, datetime, timedelta

    service = _retry_service()

    # Seed one indexed paper that needs enrichment, aged past the min_age
    # floor so the frontier picks it up.
    meta = PaperMetadata(
        arxiv_id="2401.99999",
        title="needs-enrichment",
        authors="A",
        abstract="x",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="",
        html_url="",
        citation_count=0,
        semantic_scholar_id=None,
        index_status="indexed",
        indexed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    service.repository.upsert_paper(meta, search_document="x", embedding=None)

    def fake_enrich(papers, *, batch_size=20, **_kwargs):
        # Pretend S2 returned empty + recorded one failed batch (what the
        # retry-aware client emits when the final attempt 429'd).
        from paperbase.sources import EnrichmentOutcome

        return EnrichmentOutcome(
            attempted=len(papers),
            enriched=0,
            batches_succeeded=0,
            batches_failed=1,
        )

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", fake_enrich)

    result = service.enrich_missing_metadata(limit=10, min_age_seconds=3600)

    assert result == {"papers_scanned": 1, "papers_updated": 0, "batches_failed": 1}


def test_worker_enrich_missing_metadata_requests_retry_when_s2_batches_fail(monkeypatch):
    """Regression test for PR #36 review (P2): when Semantic Scholar exhausts
    internal retries on some batches, the worker path must opt into
    ``RetryableError`` so the job is requeued instead of marked completed."""
    from datetime import UTC, datetime, timedelta

    service = _retry_service()

    meta = PaperMetadata(
        arxiv_id="2401.99998",
        title="needs-enrichment-worker",
        authors="A",
        abstract="x",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="",
        html_url="",
        citation_count=0,
        semantic_scholar_id=None,
        index_status="indexed",
        indexed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    service.repository.upsert_paper(meta, search_document="x", embedding=None)

    def fake_enrich(papers, *, batch_size=20, **_kwargs):
        from paperbase.sources import EnrichmentOutcome

        return EnrichmentOutcome(
            attempted=len(papers),
            enriched=0,
            batches_succeeded=0,
            batches_failed=1,
        )

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", fake_enrich)
    service.repository.create_job("enrich_missing_metadata", {"limit": 10, "min_age_seconds": 3600})

    processed = service.process_next_job()

    assert processed is not None
    assert processed.status == "pending"
    assert processed.attempts == 1
    assert "Semantic Scholar enrichment left 1 batch" in (processed.error or "")


def test_default_max_attempts_per_job_type():
    """Per-job-type defaults from the issue spec: index_paper=3,
    sync_latest=5, etc. Locks the table in so a future change has to be
    deliberate."""
    from paperbase.repository import default_max_attempts

    assert default_max_attempts("index_paper") == 3
    assert default_max_attempts("sync_latest") == 5
    assert default_max_attempts("enrich_missing_metadata") == 5
    assert default_max_attempts("reindex_embeddings") == 2
    assert default_max_attempts("summarize_top_cited") == 2
    assert default_max_attempts("brand_new_job_type") == 3


def test_create_job_records_per_type_max_attempts():
    """create_job consults default_max_attempts() so a freshly-enqueued job
    arrives with the right per-type budget. Caller can still override."""
    service = _retry_service()
    job_a = service.repository.create_job("sync_latest", {})
    job_b = service.repository.create_job("index_paper", {"arxiv_id": "x"})
    job_c = service.repository.create_job("index_paper", {"arxiv_id": "y"}, max_attempts=10)
    assert job_a.max_attempts == 5
    assert job_b.max_attempts == 3
    assert job_c.max_attempts == 10


def test_enrich_missing_metadata_backfills_papers_that_inline_enrichment_missed(monkeypatch):
    """An indexed paper with citation_count=0 and no S2 id (the shape left
    behind by a 429 during inline enrichment) gets picked up by
    enrich_missing_metadata, receives citations + S2 id, and is persisted."""
    from datetime import UTC, datetime, timedelta

    service = PaperbaseService(
        settings=Settings(storage_backend="memory", database_url="memory://", api_key="t"),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()

    stale = PaperMetadata(
        arxiv_id="2401.33333",
        title="Stale Paper",
        authors="Z",
        abstract="abstract",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="https://example.test/s.pdf",
        html_url="https://example.test/s",
        citation_count=0,
        semantic_scholar_id=None,
        index_status="indexed",
        indexed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    service.repository.upsert_paper(stale, search_document="", embedding=None)

    def fake_enrich(papers, **_kwargs):
        from paperbase.sources import EnrichmentOutcome
        for paper in papers:
            paper.citation_count = 4242
            paper.influential_citation_count = 12
            paper.semantic_scholar_id = f"s2-{paper.arxiv_id}"
        return EnrichmentOutcome(attempted=len(papers), enriched=len(papers), batches_succeeded=1)

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", fake_enrich)

    result = service.enrich_missing_metadata(limit=10)

    assert result["papers_scanned"] == 1
    assert result["papers_updated"] == 1
    stored = service.repository.get_paper_by_arxiv_id("2401.33333")
    assert stored is not None
    assert stored.citation_count == 4242
    assert stored.semantic_scholar_id == "s2-2401.33333"
    # Second pass: the paper is out of the queue now that it has citations.
    assert service.repository.list_papers_needing_enrichment(limit=10) == []


def test_list_papers_needing_enrichment_respects_min_age_floor():
    """Fresh papers (indexed less than min_age_seconds ago) must not be
    returned — inline enrichment may still be in flight for them, and the
    backfill job should not race with the index path."""
    from datetime import UTC, datetime, timedelta

    repo = MemoryPaperRepository()

    def _seed(arxiv: str, age_seconds: int) -> None:
        meta = PaperMetadata(
            arxiv_id=arxiv,
            title="t",
            authors="a",
            abstract="b",
            categories="cs.AI",
            primary_category="cs.AI",
            published="2024-01-01",
            updated="2024-01-01",
            pdf_url="p",
            html_url="h",
            citation_count=0,
            semantic_scholar_id=None,
            index_status="indexed",
            indexed_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        )
        repo.upsert_paper(meta, search_document="", embedding=None)

    _seed("fresh/0001", age_seconds=60)
    _seed("stale/0001", age_seconds=7200)

    eligible = repo.list_papers_needing_enrichment(limit=10, min_age_seconds=3600)

    assert [p.arxiv_id for p in eligible] == ["stale/0001"]


def test_list_papers_needing_enrichment_keyset_covers_rows_across_indexed_at_boundaries():
    """Regression test for the keyset-pagination bug flagged in PR #28 review:
    advancing by paper_id alone skips rows when later pages cross an
    ``indexed_at`` boundary. A correct cursor must mirror the full
    ``(indexed_at, paper_id)`` sort key.

    Seed sets this up deliberately:
      - earliest row: indexed_at=T-3h, paper_id="arxiv:zzz" (lexicographically large)
      - later row:    indexed_at=T-2h, paper_id="arxiv:aaa" (lexicographically small)

    With the broken cursor, page 1 returns the T-3h row and the cursor is
    set to ``paper_id > "arxiv:zzz"``, which silently excludes "arxiv:aaa"
    on page 2. The fix keeps both rows reachable."""
    from datetime import UTC, datetime, timedelta

    repo = MemoryPaperRepository()
    now = datetime.now(UTC)

    def _seed(arxiv: str, indexed_at):
        meta = PaperMetadata(
            arxiv_id=arxiv,
            title="t",
            authors="a",
            abstract="b",
            categories="cs.AI",
            primary_category="cs.AI",
            published="2024-01-01",
            updated="2024-01-01",
            pdf_url="p",
            html_url="h",
            citation_count=0,
            semantic_scholar_id=None,
            index_status="indexed",
            indexed_at=indexed_at,
        )
        return repo.upsert_paper(meta, search_document="", embedding=None)

    _seed("zzz/0001", indexed_at=now - timedelta(hours=3))
    _seed("aaa/0001", indexed_at=now - timedelta(hours=2))

    page1 = repo.list_papers_needing_enrichment(limit=1, min_age_seconds=3600)
    assert [p.arxiv_id for p in page1] == ["zzz/0001"], (
        "earliest indexed_at should come first under NULLS FIRST, ASC"
    )
    page2 = repo.list_papers_needing_enrichment(limit=1, min_age_seconds=3600, after=page1[-1])
    assert [p.arxiv_id for p in page2] == ["aaa/0001"], (
        "keyset must advance by (indexed_at, paper_id), not paper_id alone"
    )
    page3 = repo.list_papers_needing_enrichment(limit=1, min_age_seconds=3600, after=page2[-1])
    assert page3 == []


def test_enrichment_stats_counts_papers_with_s2_id_but_zero_citations_as_enriched():
    """A freshly-published paper that was successfully enriched but
    legitimately has zero citations must count as enriched. Using
    citation_count > 0 alone would warn the operator on a healthy corpus.
    Regression test for PR #28 review."""
    from datetime import UTC, datetime

    repo = MemoryPaperRepository()

    def _seed(arxiv: str, *, s2_id: str | None, citations: int):
        meta = PaperMetadata(
            arxiv_id=arxiv,
            title="t",
            authors="a",
            abstract="b",
            categories="cs.AI",
            primary_category="cs.AI",
            published="2024-01-01",
            updated="2024-01-01",
            pdf_url="p",
            html_url="h",
            citation_count=citations,
            semantic_scholar_id=s2_id,
            index_status="indexed",
            indexed_at=datetime.now(UTC),
        )
        repo.upsert_paper(meta, search_document="", embedding=None)

    _seed("cited/0001", s2_id="s2-1", citations=42)
    _seed("newly-enriched/0001", s2_id="s2-2", citations=0)
    _seed("missing/0001", s2_id=None, citations=0)

    stats = repo.enrichment_stats()

    assert stats["total_indexed"] == 3
    assert stats["with_citations"] == 1
    assert stats["with_semantic_scholar_id"] == 2
    # Both the cited paper AND the newly-enriched zero-citation paper count.
    # Only the paper with no S2 id and no citations is missing.
    assert stats["enriched"] == 2


def _make_service_with_one_paper() -> tuple[PaperbaseService, str]:
    service = PaperbaseService(
        settings=Settings(storage_backend="memory", database_url="memory://", api_key="t"),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()
    meta = PaperMetadata(
        arxiv_id="2401.99999",
        title="Logging Test Paper",
        authors="Z",
        abstract="An abstract mentioning attention.",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="p",
        html_url="h",
        citation_count=5,
        index_status="indexed",
    )
    paper = service.repository.upsert_paper(meta, search_document="attention", embedding=None)
    return service, paper.paper_id


def test_search_records_query_log_and_returns_query_id_by_default():
    """A default SearchRequest (log_query omitted) persists a row in
    search_logs and returns the row's query_id so the client can follow
    up with a click event."""
    service, paper_id = _make_service_with_one_paper()

    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))

    assert response.query_id is not None
    assert response.query_id.startswith("qry_")
    log = service.repository.search_logs[response.query_id]
    assert log["query_text"] == "attention"
    assert log["request_rerank"] is False
    assert log["top_paper_ids"] == [paper_id]
    assert log["result_set_id"] == response.result_set_id
    assert log["latency_ms"] is not None and log["latency_ms"] >= 0
    assert log["clicked_paper_id"] is None
    assert log["user_id"] is None


def test_search_skips_logging_when_log_query_false():
    """log_query=False is the opt-out path. No row is written and the
    response has query_id=None so clients know click tracking is off."""
    service, _ = _make_service_with_one_paper()

    response = service.search(
        SearchRequest(query="attention", limit=5, include_live_sources=False, log_query=False)
    )

    assert response.query_id is None
    assert service.repository.search_logs == {}


def test_search_attaches_user_id_when_service_caller_provides_one():
    """Callers that know the authenticated user can pass user_id so
    downstream analysis can group queries by user without the search layer
    needing to parse session cookies."""
    service, _ = _make_service_with_one_paper()

    response = service.search(
        SearchRequest(query="attention", limit=5, include_live_sources=False),
        user_id="usr_abc123",
    )

    assert response.query_id is not None
    assert service.repository.search_logs[response.query_id]["user_id"] == "usr_abc123"


def test_search_logging_failure_does_not_break_response(monkeypatch):
    """A storage-side failure in record_search_log must not fail the
    search — ranking quality matters more than observability in the hot
    path. query_id is nulled so the client knows click tracking is
    unavailable."""
    service, paper_id = _make_service_with_one_paper()

    def boom(**_kwargs):
        raise RuntimeError("db went away")

    monkeypatch.setattr(service.repository, "record_search_log", boom)

    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))

    assert [p.paper_id for p in response.papers] == [paper_id]
    assert response.query_id is None


def test_record_search_click_updates_log_and_404s_on_unknown_query_id():
    """The click endpoint is idempotent on the last click. Unknown query_id
    returns False so the API layer can translate to 404."""
    service, paper_id = _make_service_with_one_paper()
    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))
    assert response.query_id is not None

    assert service.record_search_click(response.query_id, paper_id) is True
    log = service.repository.search_logs[response.query_id]
    assert log["clicked_paper_id"] == paper_id
    assert log["clicked_at"] is not None

    assert service.record_search_click("qry_nonexistent", paper_id) is False


def test_chat_search_tool_does_not_write_search_log_row():
    """The paperbase_search chat tool runs LLM-generated reformulations, not
    real user queries, so its searches must never land in search_logs — they
    would pollute the #21 real-user-query benchmark. The tool path forces
    log_query=False so the response carries query_id=None and no row is
    persisted."""
    from paperbase.service import ChatCitationRegistry

    service, paper_id = _make_service_with_one_paper()
    registry = ChatCitationRegistry()
    fake_user = type("U", (), {"user_id": "u1"})()

    result = service._execute_chat_tool(
        tool_name="paperbase_search",
        tool_input={"query": "attention"},
        user=fake_user,
        citation_registry=registry,
    )

    assert service.repository.search_logs == {}
    # The tool result dict is the view the model sees — no query_id surfaces
    # because click attribution is meaningless for a synthetic reformulation.
    assert "query_id" not in result
    assert any(p["paper_id"] == paper_id for p in result.get("papers", []))


def test_record_search_click_rejects_paper_outside_logged_result_set():
    """The click endpoint only accepts clicks for papers that were actually in
    the logged top_paper_ids. A buggy or malicious caller that sends an
    arbitrary paper_id must not be able to poison the click-label dataset —
    the return is False so the API layer translates to 404."""
    service, returned_paper_id = _make_service_with_one_paper()

    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))
    assert response.query_id is not None
    assert [p.paper_id for p in response.papers] == [returned_paper_id]

    # Attaching a paper that was never in the logged top_paper_ids must be rejected.
    outsider_paper_id = "arxiv:9999.00000"
    assert service.record_search_click(response.query_id, outsider_paper_id) is False
    log = service.repository.search_logs[response.query_id]
    assert log["clicked_paper_id"] is None
    assert log["clicked_at"] is None

    # And the legitimate click for a paper that was returned still works.
    assert service.record_search_click(response.query_id, returned_paper_id) is True
    assert service.repository.search_logs[response.query_id]["clicked_paper_id"] == returned_paper_id


def test_search_log_does_not_persist_auth_tokens_or_secrets():
    """Belt-and-braces: even if a caller somehow passes a bearer token or
    API key as the query text, the SearchRequest schema has no field for
    auth material, so nothing but `query` lands in the log. Lock the
    persisted row's keys in place so a future migration can't silently
    add a token-bearing column."""
    service, _ = _make_service_with_one_paper()
    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))
    assert response.query_id is not None

    persisted = service.repository.search_logs[response.query_id]
    assert set(persisted.keys()) == {
        "query_id",
        "user_id",
        "query_text",
        "request_rerank",
        "top_paper_ids",
        "result_set_id",
        "latency_ms",
        "clicked_paper_id",
        "clicked_at",
        "created_at",
    }
    for forbidden in ("api_key", "token", "authorization", "bearer", "secret"):
        assert forbidden not in persisted, f"{forbidden!r} must never appear in search_logs"


# --------------------------------------------------------------------------- #
# Paper-code ingestion (#5) — worker dispatch + force / url-change refresh.
# --------------------------------------------------------------------------- #


def _seed_paper_with_code_url(service: PaperbaseService, *, code_url: str) -> str:
    paper = PaperMetadata(
        arxiv_id="2401.12345",
        title="Paper With Code",
        authors="A",
        abstract="x",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        code_url=code_url,
        index_status="indexed",
    )
    stored = service.repository.upsert_paper(paper, search_document="x", embedding=None)
    return stored.paper_id


def _build_python_tarball() -> bytes:
    import io as _io
    import tarfile as _tar

    buf = _io.BytesIO()
    with _tar.open(fileobj=buf, mode="w:gz") as tar:
        prefix = "owner-repo-abc123/"
        for name, payload in [
            (prefix + "README.md", b"# demo\npip install foo\n"),
            (prefix + "pyproject.toml", b"[project]\nname = 'foo'\n"),
            (prefix + "main.py", b"print('hi')\n"),
            (prefix + "src/mod.py", b"x = 1\n"),
        ]:
            info = _tar.TarInfo(name=name)
            info.size = len(payload)
            info.mode = 0o644
            tar.addfile(info, _io.BytesIO(payload))
    return buf.getvalue()


def test_ingest_repo_skips_when_code_url_is_missing(service: PaperbaseService) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="")
    result = service.ingest_repo(paper_id)
    assert result is not None
    assert result.code_index_status == "skipped"
    assert result.code_python_reason == "no_code_url"
    assert result.code_is_python is None


def test_ingest_repo_skips_unsupported_host(service: PaperbaseService) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://gitlab.com/x/y")
    result = service.ingest_repo(paper_id)
    assert result is not None
    assert result.code_index_status == "skipped"
    assert result.code_python_reason == "unsupported_host"
    assert result.code_manifest is not None
    assert result.code_manifest["source_kind"] == "unsupported"


def test_ingest_repo_writes_manifest_and_files_on_happy_path(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")

    from paperbase import code_ingest as ci

    def fake_fetch(ref, *, token, max_bytes, **_kwargs):
        return ci.FetchedArchive(data=_build_python_tarball(), resolved_sha="abc123" + "0" * 34, etag=None)

    monkeypatch.setattr("paperbase.service.code_ingest.fetch_github_archive", fake_fetch)

    result = service.ingest_repo(paper_id)
    assert result is not None
    assert result.code_index_status == "indexed"
    assert result.code_is_python is True
    assert result.code_python_reason == "python_source_with_manifest"
    manifest = result.code_manifest
    assert manifest is not None
    assert manifest["owner"] == "owner"
    assert manifest["repo"] == "repo"
    assert "main.py" in manifest["candidate_entrypoints"]
    assert "pyproject.toml" in manifest["detected_manifests"]
    assert "README.md" in manifest["readme_paths"]

    # Manifest and raw tarball must both land in the object store under the
    # paper's namespace so PR 2 can index without re-fetching.
    stored_paths = {obj.path for obj in service.object_store.list_prefix(f"papers/{paper_id}/code/")}
    assert f"papers/{paper_id}/code/manifest.json" in stored_paths
    assert f"papers/{paper_id}/code/raw/source.tar.gz" in stored_paths
    assert f"papers/{paper_id}/code/files/README.md" in stored_paths
    assert f"papers/{paper_id}/code/files/main.py" in stored_paths


def test_ingest_repo_short_circuits_on_matching_sha(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")
    sha = "abc123" + "0" * 34

    from paperbase import code_ingest as ci

    calls = {"fetch": 0, "extract": 0}

    def fake_fetch(ref, *, token, max_bytes, **_kwargs):
        calls["fetch"] += 1
        return ci.FetchedArchive(data=_build_python_tarball(), resolved_sha=sha, etag=None)

    real_extract = ci.extract_archive_safely

    def counting_extract(*args, **kwargs):
        calls["extract"] += 1
        return real_extract(*args, **kwargs)

    monkeypatch.setattr("paperbase.service.code_ingest.fetch_github_archive", fake_fetch)
    monkeypatch.setattr("paperbase.service.code_ingest.extract_archive_safely", counting_extract)

    first = service.ingest_repo(paper_id)
    assert first is not None
    assert first.code_index_status == "indexed"
    assert calls["extract"] == 1

    second = service.ingest_repo(paper_id)
    assert second is not None
    # No re-extract: same URL + same SHA + already indexed = no-op.
    assert second.code_index_status == "indexed"
    assert calls["extract"] == 1
    assert calls["fetch"] == 2  # fetch still happens to learn the SHA
    persisted = service.repository.get_paper(paper_id)
    assert persisted is not None
    assert persisted.code_index_status == "indexed"


def test_ingest_repo_force_refreshes_even_when_sha_matches(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")
    sha = "abc123" + "0" * 34

    from paperbase import code_ingest as ci

    calls = {"extract": 0}
    real_extract = ci.extract_archive_safely

    def fake_fetch(ref, *, token, max_bytes, **_kwargs):
        return ci.FetchedArchive(data=_build_python_tarball(), resolved_sha=sha, etag=None)

    def counting_extract(*args, **kwargs):
        calls["extract"] += 1
        return real_extract(*args, **kwargs)

    monkeypatch.setattr("paperbase.service.code_ingest.fetch_github_archive", fake_fetch)
    monkeypatch.setattr("paperbase.service.code_ingest.extract_archive_safely", counting_extract)

    service.ingest_repo(paper_id)
    service.ingest_repo(paper_id, force=True)
    # force=True must re-extract regardless of SHA match.
    assert calls["extract"] == 2


def test_ingest_repo_records_failed_status_on_unexpected_error(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")

    from paperbase import code_ingest as ci

    def fake_fetch(ref, *, token, max_bytes, **_kwargs):
        return ci.FetchedArchive(data=_build_python_tarball(), resolved_sha="abc123" + "0" * 34, etag=None)

    def broken_extract(*args, **kwargs):
        raise RuntimeError("bad archive")

    monkeypatch.setattr("paperbase.service.code_ingest.fetch_github_archive", fake_fetch)
    monkeypatch.setattr("paperbase.service.code_ingest.extract_archive_safely", broken_extract)

    result = service.ingest_repo(paper_id)
    assert result is not None
    assert result.code_index_status == "failed"
    assert result.code_python_reason == "ingest_failed:RuntimeError"
    assert result.code_manifest is not None
    assert result.code_manifest["code_python_reason"] == "ingest_failed:RuntimeError"

    persisted = service.repository.get_paper(paper_id)
    assert persisted is not None
    assert persisted.code_index_status == "failed"


def test_process_next_job_dispatches_ingest_repo(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")

    from paperbase import code_ingest as ci

    def fake_fetch(ref, *, token, max_bytes, **_kwargs):
        return ci.FetchedArchive(data=_build_python_tarball(), resolved_sha="sha-x" + "0" * 35, etag=None)

    monkeypatch.setattr("paperbase.service.code_ingest.fetch_github_archive", fake_fetch)

    job = service.enqueue_ingest_repo_job(paper_id)
    completed = service.process_next_job()
    assert completed is not None
    assert completed.job_id == job.job_id
    assert completed.status == "completed"

    refreshed = service.repository.get_paper(paper_id)
    assert refreshed is not None
    assert refreshed.code_index_status == "indexed"


def test_enqueue_ingest_repo_job_records_payload(service: PaperbaseService) -> None:
    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")
    job = service.enqueue_ingest_repo_job(paper_id, force=True)
    assert job.job_type == "ingest_repo"
    assert job.payload == {"paper_id": paper_id, "force": True}


# --------------------------------------------------------------------------- #
# Paper-code retrieval primitives (#5 PR 2)
# --------------------------------------------------------------------------- #


def _build_tarball_with(files: dict[str, bytes]) -> bytes:
    """Mini helper: build a GitHub-shaped tarball from a name→bytes map."""
    import io as _io
    import tarfile as _tar

    buf = _io.BytesIO()
    with _tar.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = _tar.TarInfo(name=f"owner-repo-abc/{name}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, _io.BytesIO(data))
    return buf.getvalue()


def _ingest_python_repo(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch, *, files: dict[str, bytes]
) -> str:
    """Seed a paper, stub the fetcher, and run ``ingest_repo`` end-to-end.
    Returns the paper_id so the caller can exercise the retrieval surface.
    """
    from paperbase import code_ingest as ci

    paper_id = _seed_paper_with_code_url(service, code_url="https://github.com/owner/repo")

    def fake_fetch(ref, *, token, max_bytes, **_kwargs):
        return ci.FetchedArchive(
            data=_build_tarball_with(files), resolved_sha="code2sha" + "0" * 32, etag=None
        )

    monkeypatch.setattr("paperbase.service.code_ingest.fetch_github_archive", fake_fetch)
    result = service.ingest_repo(paper_id)
    assert result is not None
    return paper_id


def test_list_paper_code_files_returns_files_for_eligible_repo(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Demo\npip install foo\n",
            "pyproject.toml": b"[project]\nname='foo'\n",
            "main.py": b'"""Entry."""\nprint(1)\n',
        },
    )
    response = service.list_paper_code_files(paper_id)
    assert response is not None
    assert response.eligibility.eligible is True
    assert response.eligibility.status == "indexed"
    assert response.resolved_sha is not None
    paths = {f.repo_path for f in response.files}
    assert {"README.md", "pyproject.toml", "main.py"} <= paths
    indexed = {f.repo_path for f in response.files if f.indexed}
    assert {"README.md", "pyproject.toml", "main.py"} <= indexed


def test_list_paper_code_files_ineligible_for_non_python(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# JS project\n",
            "package.json": b"{}\n",
        },
    )
    response = service.list_paper_code_files(paper_id)
    assert response is not None
    assert response.eligibility.eligible is False
    # Classifier landed on no_python_sources; status stays 'indexed' because
    # the repo WAS ingested — it just isn't Python.
    assert response.eligibility.reason == "no_python_sources"
    assert response.files == []


def test_read_paper_code_file_returns_content_for_indexed_file(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Hello\n",
            "pyproject.toml": b"[project]\nname='x'\n",
            "src/mod.py": b"x = 1\n",
        },
    )
    response = service.read_paper_code_file(paper_id, "src/mod.py")
    assert response is not None
    assert response.eligibility.eligible is True
    assert response.content == "x = 1\n"
    assert response.size_bytes == len(b"x = 1\n")
    assert response.repo_path == "src/mod.py"


def test_read_paper_code_file_rejects_path_traversal(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Hello\n",
            "pyproject.toml": b"[project]\n",
            "main.py": b"print(1)\n",
        },
    )
    for evil in ("../etc/passwd", "/absolute/path", "sub/../../evil.py"):
        response = service.read_paper_code_file(paper_id, evil)
        assert response is not None
        # Normalized path should not equal the raw traversal attempt and
        # content should be empty (no file served for rejected paths).
        assert response.content == ""


def test_normalize_code_path_rejects_absolute_and_drive_prefixes() -> None:
    """Regression test for the PR #44 review: ``_normalize_code_path``
    previously called ``lstrip("/")`` before the absolute-path check, which
    silently turned ``/absolute/path`` into ``absolute/path`` — the read
    endpoint would then serve it if a matching stored file happened to
    exist. Rejecting absolute paths before stripping is the fix; this
    lock-in prevents a regression via path-reconstruction refactors.

    Also locks in the narrower drive-prefix check from the follow-up review
    (``^[A-Za-z]:[/\\\\]`` rather than any second-character colon) so a file
    legitimately named ``a:b.txt`` remains readable.
    """
    normalize = PaperbaseService._normalize_code_path
    for evil in [
        "/absolute/path",
        "//server/share",
        "\\windows\\path",
        "C:/windows",
        "C:\\windows",
        "c:/lower",
        "../escape",
        "sub/../../escape",
        "./hidden",
        "",
        "   ",
    ]:
        assert normalize(evil) is None, f"{evil!r} must be rejected"
    assert normalize("README.md") == "README.md"
    assert normalize("src/mod.py") == "src/mod.py"
    assert normalize("  scripts/train.py  ") == "scripts/train.py"
    # Legitimate filenames whose second character is ``:`` must NOT be
    # rejected — GitHub / Git allow them and narrowing the drive-prefix
    # check from ``raw[1] == ':'`` to ``^[A-Za-z]:[/\\]`` is the point
    # of the follow-up review.
    for ok in ["a:b.txt", "v:1/notes.md", "docs/x:y.md"]:
        assert normalize(ok) == ok, f"{ok!r} must be accepted"


def test_read_paper_code_file_rejects_absolute_even_when_file_exists(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concrete-layout version of the path-traversal lock-in. Drops a
    blob at the exact object-store key a pre-fix ``lstrip`` would have
    constructed, then asserts the read primitive refuses to serve it —
    proving the input is rejected upstream rather than by accident."""
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Hello\n",
            "pyproject.toml": b"[project]\n",
            "main.py": b"print(1)\n",
        },
    )
    service.object_store.put_bytes(
        f"papers/{paper_id}/code/files/absolute/path",
        b"this should never be served",
    )
    response = service.read_paper_code_file(paper_id, "/absolute/path")
    assert response is not None
    assert response.content == ""
    assert response.size_bytes == 0


def test_list_paper_code_files_uses_metadata_not_content_reads(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the PR #44 review: the previous implementation
    called ``object_store.get_bytes()`` per file just to populate
    ``size_bytes``, making the list endpoint O(total repo bytes) of
    network I/O on GCS. The fix routes through ``list_prefix`` metadata,
    so ``get_bytes`` must never be touched during listing."""
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Demo\n",
            "pyproject.toml": b"[project]\nname='x'\n",
            "main.py": b'"""x."""\nprint(1)\n',
        },
    )

    real_get_bytes = service.object_store.get_bytes
    calls: list[str] = []

    def tracking_get_bytes(path: str) -> bytes:
        calls.append(path)
        return real_get_bytes(path)

    monkeypatch.setattr(service.object_store, "get_bytes", tracking_get_bytes)
    response = service.list_paper_code_files(paper_id)
    assert response is not None
    assert response.eligibility.eligible is True
    assert len(response.files) >= 3
    assert all(f.size_bytes > 0 for f in response.files), (
        f"every listed file should carry size metadata; got {[f for f in response.files]}"
    )
    code_file_reads = [p for p in calls if p.startswith(f"papers/{paper_id}/code/files/")]
    assert code_file_reads == [], (
        f"listing must not download code/files/* blobs; got {code_file_reads}"
    )


def test_read_paper_code_file_ineligible_for_non_python(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={"README.md": b"# JS\n", "package.json": b"{}\n"},
    )
    response = service.read_paper_code_file(paper_id, "README.md")
    assert response is not None
    assert response.eligibility.eligible is False
    assert response.content == ""


def test_search_paper_code_returns_readme_and_signature_hits(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Demo\nThis project trains a transformer model on IMDB.\n",
            "pyproject.toml": b"[project]\nname='demo'\n",
            "main.py": b'"""Run demo."""\n\ndef train_transformer(x):\n    """Train a transformer."""\n    return x\n',
        },
    )
    response = service.search_paper_code(paper_id, "transformer", limit=10)
    assert response is not None
    assert response.eligibility.eligible is True
    # At least the README and the signature chunk should hit "transformer".
    kinds = {h.chunk_kind for h in response.hits}
    assert "code_readme" in kinds
    assert "code_signature" in kinds
    # repo_path is the clean repo-relative path, stripped of any
    # "code/files/" storage prefix.
    assert all(not h.repo_path.startswith("code/files/") for h in response.hits)


def test_search_paper_code_ineligible_for_non_python(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={"README.md": b"# JS\n", "package.json": b"{}\n"},
    )
    response = service.search_paper_code(paper_id, "anything")
    assert response is not None
    assert response.eligibility.eligible is False
    assert response.hits == []


def test_get_paper_code_context_concatenates_top_chunks(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# Demo\nTrains a small transformer.\n",
            "pyproject.toml": b"[project]\nname='demo'\n",
            "main.py": b'"""Entry."""\n\ndef train_transformer():\n    """Train it."""\n    pass\n',
        },
    )
    response = service.get_paper_code_context(paper_id, "transformer", limit=3)
    assert response is not None
    assert response.eligibility.eligible is True
    assert "### README.md" in response.context or "### main.py" in response.context
    assert len(response.hits) >= 1


def test_paper_section_search_excludes_code_chunks(
    service: PaperbaseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the PR 2 namespace split: a token that appears in
    an indexed README must NOT surface in paper-section search, otherwise a
    user asking for a paper concept gets polluted by linked-repo code."""
    paper_id = _ingest_python_repo(
        service,
        monkeypatch,
        files={
            "README.md": b"# UniqueReadmeToken\nThis README mentions UniqueReadmeToken prominently.\n",
            "pyproject.toml": b"[project]\nname='demo'\n",
            "main.py": b"def f():\n    pass\n",
        },
    )
    # Sanity: the token IS in the indexed code chunks.
    code_response = service.search_paper_code(paper_id, "UniqueReadmeToken", limit=5)
    assert code_response is not None and code_response.hits, "expected code hit for the sentinel"

    # Paper-section search must not return it because it is only in code chunks.
    section_rows = service.repository.search_sections(
        "UniqueReadmeToken", limit=10
    )
    assert section_rows == [], (
        "paper-section search leaked code chunks: "
        f"{[(r.chunk_kind, r.path) for r in section_rows]}"
    )


def test_list_and_read_return_none_for_missing_paper(service: PaperbaseService) -> None:
    assert service.list_paper_code_files("paper_does_not_exist") is None
    assert service.read_paper_code_file("paper_does_not_exist", "README.md") is None
    assert service.search_paper_code("paper_does_not_exist", "x") is None
    assert service.get_paper_code_context("paper_does_not_exist", "x") is None
