import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type FetchCall = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(payload: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

function requestPath(input: RequestInfo | URL) {
  return new URL(String(input), "http://localhost").pathname;
}

async function loadApiModule() {
  vi.resetModules();
  (window as typeof window & { PAPERBASE_BASE_URL?: string }).PAPERBASE_BASE_URL = "";
  return import("./api");
}

describe("chat api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches CSRF headers after the browser session is loaded", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          authenticated: true,
          user: {
            user_id: "user_1",
            email: "user@example.com",
            display_name: "User",
          },
          has_anthropic_api_key: false,
          csrf_token: "csrf-123",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_1",
          project_id: "proj_1",
          project_name: "General",
          project_instructions: "",
          title: "New chat",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          messages: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadSession();
    await api.createThread("New chat");

    const [, init] = fetchMock.mock.calls[1] as FetchCall;
    expect(new Headers(init?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
  });

  it("dispatches auth-expired on 401 and clears the cached CSRF token", async () => {
    const authExpired = vi.fn();
    window.addEventListener("paperbase:auth-expired", authExpired);

    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          authenticated: true,
          user: {
            user_id: "user_1",
            email: "user@example.com",
            display_name: "User",
          },
          has_anthropic_api_key: false,
          csrf_token: "csrf-123",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: "Authentication required" }, { status: 401 }))
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_2",
          project_id: "proj_1",
          project_name: "General",
          project_instructions: "",
          title: "Recovered",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          messages: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadSession();
    await expect(api.createThread("Should fail")).rejects.toThrow("Authentication required");
    await api.createThread("Recovered");

    const [, recoveredInit] = fetchMock.mock.calls[2] as FetchCall;
    expect(authExpired).toHaveBeenCalledTimes(1);
    expect(new Headers(recoveredInit?.headers).get("X-Paperbase-CSRF")).toBeNull();
    window.removeEventListener("paperbase:auth-expired", authExpired);
  });

  it("formats structured validation errors into readable text", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            detail: [
              { loc: ["body", "email"], msg: "Field required" },
              { loc: ["body", "password"], msg: "Field required" },
            ],
          },
          { status: 422 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();
    const form = document.createElement("form");
    const email = document.createElement("input");
    email.name = "email";
    email.value = "";
    const password = document.createElement("input");
    password.name = "password";
    password.value = "";
    form.append(email, password);

    await expect(api.postForm("/v1/browser/auth/login", form)).rejects.toThrow(
      "email: Field required; password: Field required",
    );
  });

  it("upgrades insecure configured API bases when the page is HTTPS", async () => {
    const api = await loadApiModule();

    expect(
      api.resolveApiBase(
        "http://paperbase-api.example.test/",
        "https://paperbase-api.example.test/login",
      ),
    ).toBe("https://paperbase-api.example.test");
    expect(api.resolveApiBase("/api/", "https://paperbase-api.example.test/login")).toBe(
      "https://paperbase-api.example.test/api",
    );
  });

  it("loads paper mention suggestions from the browser endpoint", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          fallback: false,
          suggestions: [
            {
              paper_id: "arxiv:2401.12345",
              arxiv_id: "2401.12345",
              title: "Benchmark Paper",
              authors: "Ada Lovelace",
              summary_short: "Grounded benchmark paper.",
              citation_count: 12,
              published: "2026-04-01",
              score: 0.9,
              matched_field: "title",
              highlight_snippet: "Benchmark Paper",
            },
          ],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const result = await api.suggestBrowserPapers("benchmark paper", 7);

    const [path, init] = fetchMock.mock.calls[0] as FetchCall;
    const requestUrl = new URL(String(path), "http://localhost");
    expect(`${requestUrl.pathname}${requestUrl.search}`).toBe(
      "/v1/browser/papers/suggest?q=benchmark+paper&limit=7",
    );
    expect(init?.method).toBe("GET");
    expect(result.fallback).toBe(false);
    expect(result.suggestions).toHaveLength(1);
    expect(result.suggestions[0]?.paper_id).toBe("arxiv:2401.12345");
  });

  it("returns fallback mention suggestions when the backend reports no direct matches", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          fallback: true,
          suggestions: [
            {
              paper_id: "arxiv:2409.99999",
              arxiv_id: "2409.99999",
              title: "Fallback Paper",
              authors: "Ada Lovelace",
              summary_short: "Suggested fallback paper.",
              citation_count: 30,
              published: "2026-04-01",
              score: 0,
              matched_field: "fallback",
              highlight_snippet: "Suggested fallback paper.",
            },
          ],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const result = await api.suggestBrowserPapers("nomatch", 6);

    expect(result.fallback).toBe(true);
    expect(result.suggestions[0]?.matched_field).toBe("fallback");
  });

  it("creates, moves, and deletes project chats with CSRF", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          authenticated: true,
          user: {
            user_id: "user_1",
            email: "user@example.com",
            display_name: "User",
          },
          has_anthropic_api_key: false,
          csrf_token: "csrf-123",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_3",
          project_id: "proj_9",
          project_name: "Agents",
          project_instructions: "",
          title: "Project chat",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          messages: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_3",
          project_id: "proj_2",
          project_name: "Agents",
          project_instructions: "",
          title: "Project chat",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          messages: [],
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ status: "deleted" }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadSession();
    await api.createThread("Project chat", "proj_9");
    await api.moveThreadToProject("thread_3", "proj_2");
    await api.deleteThread("thread_3");

    const [, createInit] = fetchMock.mock.calls[1] as FetchCall;
    const [movePath, moveInit] = fetchMock.mock.calls[2] as FetchCall;
    const [, deleteInit] = fetchMock.mock.calls[3] as FetchCall;
    expect(createInit?.body).toBe(JSON.stringify({ title: "Project chat", project_id: "proj_9" }));
    expect(requestPath(movePath)).toBe("/v1/browser/chats/thread_3/project");
    expect(moveInit?.body).toBe(JSON.stringify({ project_id: "proj_2" }));
    expect(new Headers(deleteInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
  });

  it("loads and mutates projects through the browser endpoints", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          projects: [
            {
              project_id: "proj_1",
              name: "General",
              description: "",
              is_default: true,
              session_count: 1,
              github_repo_url: "https://github.com/octocat/general",
              created_at: "2026-04-21T00:00:00Z",
              updated_at: "2026-04-21T00:00:00Z",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          repos: [
            {
              owner: "octocat",
              name: "existing-repo",
              full_name: "octocat/existing-repo",
              html_url: "https://github.com/octocat/existing-repo",
              default_branch: "main",
              private: true,
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          project_id: "proj_2",
          name: "Agents",
          description: "Agent eval work",
          is_default: false,
          session_count: 0,
          github_repo_url: "https://github.com/octocat/agent-evals",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ status: "deleted" }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const projects = await api.loadProjects();
    const repos = await api.loadGitHubRepositories();
    const githubRepo = {
      mode: "create" as const,
      name: "agent-evals",
      base_branch: "main",
      import_paths: [],
    };
    const created = await api.createProject("Agents", "Agent eval work", githubRepo);
    await api.deleteProject("proj_2");

    const [loadPath] = fetchMock.mock.calls[0] as FetchCall;
    const [repoPath, repoInit] = fetchMock.mock.calls[1] as FetchCall;
    const [createPath, createInit] = fetchMock.mock.calls[2] as FetchCall;
    expect(requestPath(loadPath)).toBe("/v1/browser/projects");
    expect(requestPath(repoPath)).toBe("/v1/browser/github/repositories");
    expect(repoInit?.method).toBe("GET");
    expect(requestPath(createPath)).toBe("/v1/browser/projects");
    expect(createInit?.method).toBe("POST");
    expect(createInit?.body).toBe(
      JSON.stringify({ name: "Agents", description: "Agent eval work", github_repo: githubRepo }),
    );
    expect(projects.projects[0]?.project_id).toBe("proj_1");
    expect(repos.repos[0]?.full_name).toBe("octocat/existing-repo");
    expect(created.project_id).toBe("proj_2");
  });

  it("passes project item kind filters", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          project_id: "proj_1",
          items: [],
          offset: 25,
          limit: 25,
          total_count: 40,
          has_more: false,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadProjectItems("proj_1", 25, 25, "python_file");

    const [path, init] = fetchMock.mock.calls[0] as FetchCall;
    expect(requestPath(path)).toBe("/v1/browser/projects/proj_1/items");
    expect(new URL(String(path), "http://localhost").searchParams.get("item_kind")).toBe(
      "python_file",
    );
    expect(init?.method).toBe("GET");
  });

  it("sends structured GitHub sync payloads for project saves", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          project_id: "proj_1",
          repo_exists: true,
          github_repo_name: "paperbase-general-123abc",
          github_repo_url: "https://github.com/octocat/paperbase-general-123abc",
          default_branch: "main",
          last_sync_commit_sha: "abc123",
          last_sync_commit_url: "https://github.com/octocat/paperbase-general-123abc/commit/abc123",
          last_synced_at: "2026-04-21T00:00:00Z",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.syncProjectRepo("proj_1", {
      commitMessage: "Save something.py",
      expectedRemoteHeadSha: "def5678",
      conflictResolutions: [
        {
          path: "src/something.py",
          content: "print('merged')",
        },
      ],
    });

    const [path, init] = fetchMock.mock.calls[0] as FetchCall;
    expect(requestPath(path)).toBe("/v1/browser/projects/proj_1/repo-sync");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({
        commit_message: "Save something.py",
        expected_remote_head_sha: "def5678",
        conflict_resolutions: [
          {
            path: "src/something.py",
            content: "print('merged')",
          },
        ],
      }),
    );
  });

  it("parses streamed SSE events even when chunk boundaries split the payload", async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValue(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                encoder.encode(
                  'event: turn_started\ndata: {"turn":{"turn_id":"turn_1","thread_id":"thread_1","status":"running","provider_response_id":"resp_1","started_at":"2026-04-21T00:00:00Z","completed_at":null,"usage":null,"diff_summary":"","changed_paths":[],"exec_session_ids":[],"items":[]}}\n\n' +
                    'event: item_started\ndata: {"item":{"item_id":"item_tool_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"function_call","role":"assistant","phase":null,"call_id":"tool_1","name":"paperbase_search","content_text":"","content_json":{"query":"grounding"},"citations":[],"created_at":"2026-04-21T00:00:00Z","status":"in_progress"}}\n\n' +
                    'event: item_completed\ndata: {"item":{"item_id":"item_tool_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"function_call","role":"assistant","phase":null,"call_id":"tool_1","name":"paperbase_search","content_text":"","content_json":{"query":"grounding"},"citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}}\n\n' +
                    'event: item_started\ndata: {"item":{"item_id":"item_tool_1_output","thread_id":"thread_1","turn_id":"turn_1","item_type":"function_call_output","role":"tool","phase":null,"call_id":"tool_1","name":"paperbase_search","content_text":"","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"in_progress"}}\n\n' +
                    'event: item_completed\ndata: {"item":{"item_id":"item_tool_1_output","thread_id":"thread_1","turn_id":"turn_1","item_type":"function_call_output","role":"tool","phase":null,"call_id":"tool_1","name":"paperbase_search","content_text":"{\\n  \\"result_set_id\\": \\"rs_1\\"\\n}","content_json":{"result_set_id":"rs_1"},"citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}}\n\n' +
                    'event: item_started\ndata: {"item":{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":null,"call_id":null,"name":null,"content_text":"","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"in_progress"}}\n\n' +
                    'event: usage\ndata: {"model":"gpt-5","input_tokens":11234,"output_tokens":1,"total_tokens":11235,"context_window_tokens":1000000}\n\n' +
                    'event: item_delta\ndata: {"item_id":"msg_1","delta":"Ground',
                ),
              );
              controller.enqueue(
                encoder.encode(
                  'ed answer","delta_type":"output_text"}\n\nevent: item_completed\ndata: {"item":{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}}\n\nevent: turn_completed\ndata: {"thread":{"thread_id":"thread_1","project_id":"proj_1","project_name":"General","project_instructions":"","title":"New chat","created_at":"2026-04-21T00:00:00Z","updated_at":"2026-04-21T00:00:00Z","items":[{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}],"turns":[{"turn_id":"turn_1","thread_id":"thread_1","status":"completed","provider_response_id":"resp_1","started_at":"2026-04-21T00:00:00Z","completed_at":"2026-04-21T00:00:01Z","usage":null,"diff_summary":"","changed_paths":[],"exec_session_ids":[],"items":[{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}]}]},"turn":{"turn_id":"turn_1","thread_id":"thread_1","status":"completed","provider_response_id":"resp_1","started_at":"2026-04-21T00:00:00Z","completed_at":"2026-04-21T00:00:01Z","usage":null,"diff_summary":"","changed_paths":[],"exec_session_ids":[],"items":[{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}]},"final_item":{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"},"has_openai_api_key":false,"state":"completed","usage":{"model":"gpt-5","input_tokens":11234,"output_tokens":27,"total_tokens":11261,"context_window_tokens":1000000}}\n\n',
                ),
              );
              controller.close();
            },
          }),
          {
            status: 200,
            headers: { "content-type": "text/event-stream" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();
    const events: Array<[string, unknown]> = [];

    await api.sendThreadMessageStream(
      "thread_1",
      { content: "Ground me", include_papers: true, include_uploads: true },
      (eventName, payload) => {
        events.push([eventName, payload]);
      },
    );

    expect(events).toEqual([
      [
        "turn_started",
        {
          turn: {
            turn_id: "turn_1",
            thread_id: "thread_1",
            status: "running",
            provider_response_id: "resp_1",
            started_at: "2026-04-21T00:00:00Z",
            completed_at: null,
            usage: null,
            diff_summary: "",
            changed_paths: [],
            exec_session_ids: [],
            items: [],
          },
        },
      ],
      [
        "item_started",
        {
          item: {
            item_id: "item_tool_1",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "function_call",
            role: "assistant",
            phase: null,
            call_id: "tool_1",
            name: "paperbase_search",
            content_text: "",
            content_json: { query: "grounding" },
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "in_progress",
          },
        },
      ],
      [
        "item_completed",
        {
          item: {
            item_id: "item_tool_1",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "function_call",
            role: "assistant",
            phase: null,
            call_id: "tool_1",
            name: "paperbase_search",
            content_text: "",
            content_json: { query: "grounding" },
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
        },
      ],
      [
        "item_started",
        {
          item: {
            item_id: "item_tool_1_output",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "function_call_output",
            role: "tool",
            phase: null,
            call_id: "tool_1",
            name: "paperbase_search",
            content_text: "",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "in_progress",
          },
        },
      ],
      [
        "item_completed",
        {
          item: {
            item_id: "item_tool_1_output",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "function_call_output",
            role: "tool",
            phase: null,
            call_id: "tool_1",
            name: "paperbase_search",
            content_text: '{\n  "result_set_id": "rs_1"\n}',
            content_json: { result_set_id: "rs_1" },
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
        },
      ],
      [
        "item_started",
        {
          item: {
            item_id: "msg_1",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "assistant_message",
            role: "assistant",
            phase: null,
            call_id: null,
            name: null,
            content_text: "",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "in_progress",
          },
        },
      ],
      [
        "usage",
        {
          model: "gpt-5",
          input_tokens: 11234,
          output_tokens: 1,
          total_tokens: 11235,
          context_window_tokens: 1000000,
        },
      ],
      ["item_delta", { item_id: "msg_1", delta: "Grounded answer", delta_type: "output_text" }],
      [
        "item_completed",
        {
          item: {
            item_id: "msg_1",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "assistant_message",
            role: "assistant",
            phase: "final_answer",
            call_id: null,
            name: null,
            content_text: "Grounded answer",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
        },
      ],
      [
        "turn_completed",
        {
          thread: {
            thread_id: "thread_1",
            project_id: "proj_1",
            project_name: "General",
            project_instructions: "",
            title: "New chat",
            created_at: "2026-04-21T00:00:00Z",
            updated_at: "2026-04-21T00:00:00Z",
            items: [
              {
                item_id: "msg_1",
                thread_id: "thread_1",
                turn_id: "turn_1",
                item_type: "assistant_message",
                role: "assistant",
                phase: "final_answer",
                call_id: null,
                name: null,
                content_text: "Grounded answer",
                citations: [],
                created_at: "2026-04-21T00:00:00Z",
                status: "completed",
              },
            ],
            turns: [
              {
                turn_id: "turn_1",
                thread_id: "thread_1",
                status: "completed",
                provider_response_id: "resp_1",
                started_at: "2026-04-21T00:00:00Z",
                completed_at: "2026-04-21T00:00:01Z",
                usage: null,
                diff_summary: "",
                changed_paths: [],
                exec_session_ids: [],
                items: [
                  {
                    item_id: "msg_1",
                    thread_id: "thread_1",
                    turn_id: "turn_1",
                    item_type: "assistant_message",
                    role: "assistant",
                    phase: "final_answer",
                    call_id: null,
                    name: null,
                    content_text: "Grounded answer",
                    citations: [],
                    created_at: "2026-04-21T00:00:00Z",
                    status: "completed",
                  },
                ],
              },
            ],
            messages: [
              {
                message_id: "msg_1",
                role: "assistant",
                content_markdown: "Grounded answer",
                citations: [],
                created_at: "2026-04-21T00:00:00Z",
                status: "completed",
                kind: "default",
                phase: "final_answer",
              },
            ],
          },
          turn: {
            turn_id: "turn_1",
            thread_id: "thread_1",
            status: "completed",
            provider_response_id: "resp_1",
            started_at: "2026-04-21T00:00:00Z",
            completed_at: "2026-04-21T00:00:01Z",
            usage: null,
            diff_summary: "",
            changed_paths: [],
            exec_session_ids: [],
            items: [
              {
                item_id: "msg_1",
                thread_id: "thread_1",
                turn_id: "turn_1",
                item_type: "assistant_message",
                role: "assistant",
                phase: "final_answer",
                call_id: null,
                name: null,
                content_text: "Grounded answer",
                citations: [],
                created_at: "2026-04-21T00:00:00Z",
                status: "completed",
              },
            ],
          },
          final_item: {
            item_id: "msg_1",
            thread_id: "thread_1",
            turn_id: "turn_1",
            item_type: "assistant_message",
            role: "assistant",
            phase: "final_answer",
            call_id: null,
            name: null,
            content_text: "Grounded answer",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          assistant_message: {
            message_id: "msg_1",
            role: "assistant",
            content_markdown: "Grounded answer",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
            kind: "default",
            phase: "final_answer",
          },
          has_openai_api_key: false,
          has_anthropic_api_key: false,
          state: "completed",
          usage: {
            model: "gpt-5",
            input_tokens: 11234,
            output_tokens: 27,
            total_tokens: 11261,
            context_window_tokens: 1000000,
          },
        },
      ],
    ]);
  });

  it("marks pre-event stream failures as fallback-safe", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValue(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.error(new Error("stream blew up"));
            },
          }),
          {
            status: 200,
            headers: { "content-type": "text/event-stream" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await expect(
      api.sendThreadMessageStream(
        "thread_1",
        { content: "fallback please", include_papers: true, include_uploads: true },
        () => undefined,
      ),
    ).rejects.toMatchObject({ canFallback: true, message: "stream blew up" });
  });

  it("raises a dedicated abort error when the stream request is cancelled", async () => {
    const fetchMock = vi.fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>(
      async (_path, init) => {
        await new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        });
        throw new Error("unreachable");
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();
    const controller = new AbortController();
    const streamPromise = api.sendThreadMessageStream(
      "thread_1",
      { content: "cancel me", include_papers: true, include_uploads: true },
      () => undefined,
      { signal: controller.signal },
    );

    controller.abort();

    await expect(streamPromise).rejects.toMatchObject({
      name: "StreamingChatAbortError",
      message: "Streaming request aborted",
    });
  });

  it("posts the requested run mode when running a Python file", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          file_id: "file_1",
          status: "completed",
          stdout: "smoke",
          stderr: "",
          exit_code: 0,
          started_at: "2026-04-21T00:00:00Z",
          finished_at: "2026-04-21T00:00:01Z",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.runPythonFile("file_1", "smoke");

    const [path, init] = fetchMock.mock.calls[0] as FetchCall;
    expect(requestPath(path)).toBe("/v1/browser/python-files/file_1/run");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ mode: "smoke" }));
  });
});
