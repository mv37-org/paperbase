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

async function loadApiModule(options: { bootstrapSession?: unknown; mv37AuthUrl?: string } = {}) {
  vi.resetModules();
  vi.stubEnv("VITE_MV37_AUTH_URL", "");
  (window as typeof window & { PAPERBASE_BASE_URL?: string }).PAPERBASE_BASE_URL = "";
  (window as typeof window & { PAPERBASE_MV37_AUTH_URL?: string }).PAPERBASE_MV37_AUTH_URL =
    options.mv37AuthUrl ?? "";
  (
    window as typeof window & { PAPERBASE_BOOTSTRAP_SESSION?: unknown }
  ).PAPERBASE_BOOTSTRAP_SESSION = options.bootstrapSession;
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

  it("hydrates CSRF headers from the bootstrapped browser session", async () => {
    const api = await loadApiModule({
      bootstrapSession: {
        authenticated: true,
        user: {
          user_id: "user_1",
          email: "user@example.com",
          display_name: "User",
        },
        has_openai_api_key: true,
        csrf_token: "csrf-bootstrap",
      },
    });

    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
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

    expect(api.getBootstrapSession()?.authenticated).toBe(true);

    await api.createThread("New chat");

    const [, init] = fetchMock.mock.calls[0] as FetchCall;
    expect(new Headers(init?.headers).get("X-Paperbase-CSRF")).toBe("csrf-bootstrap");
  });

  it("builds the local MV37 auth app login URL with a return target in dev", async () => {
    const api = await loadApiModule();

    const loginUrl = new URL(api.mv37LoginUrl());

    expect(loginUrl.origin).toBe("http://127.0.0.1:8081");
    expect(loginUrl.searchParams.get("return_to")).toBe(window.location.origin);
  });

  it("uses the runtime MV37 auth URL when the shell injects one", async () => {
    const api = await loadApiModule({
      mv37AuthUrl: "https://login.example.test/start?app=paperbase",
    });

    const loginUrl = new URL(api.mv37LoginUrl());

    expect(loginUrl.origin).toBe("https://login.example.test");
    expect(loginUrl.pathname).toBe("/start");
    expect(loginUrl.searchParams.get("app")).toBe("paperbase");
    expect(loginUrl.searchParams.get("return_to")).toBe(window.location.origin);
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
              { loc: ["body", "api_key"], msg: "Field required" },
              { loc: ["body", "token_secret"], msg: "Field required" },
            ],
          },
          { status: 422 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();
    const form = document.createElement("form");
    const apiKey = document.createElement("input");
    apiKey.name = "api_key";
    apiKey.value = "";
    const tokenSecret = document.createElement("input");
    tokenSecret.name = "token_secret";
    tokenSecret.value = "";
    form.append(apiKey, tokenSecret);

    await expect(api.postForm("/v1/browser/settings/openai-key", form)).rejects.toThrow(
      "api_key: Field required; token_secret: Field required",
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

  it("loads the trending feed and paper detail from the browser endpoints", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          date: "2026-04-21",
          sort: "upvotes",
          page: 3,
          page_size: 10,
          has_next: true,
          has_previous: true,
          papers: [
            {
              paper_id: "arxiv:2401.12345",
              arxiv_id: "2401.12345",
              title: "Benchmark Paper",
              authors: "Ada Lovelace",
              published: "2026-04-21",
              html_url: "https://arxiv.org/abs/2401.12345",
              pdf_url: "https://arxiv.org/pdf/2401.12345.pdf",
              image_url: "/v1/browser/trending/papers/arxiv%3A2401.12345/preview/content",
              upvote_count: 12,
              viewer_has_upvoted: false,
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          paper_id: "arxiv:2401.12345",
          arxiv_id: "2401.12345",
          title: "Benchmark Paper",
          authors: "Ada Lovelace",
          abstract: "Full benchmark abstract",
          tldr: "Benchmark TLDR",
          published: "2026-04-21",
          updated: "2026-04-22",
          primary_category: "cs.AI",
          categories: "cs.AI",
          doi: "10.1234/benchmark",
          comment: "Accepted",
          journal_ref: "Benchmark Journal",
          summary_short: "Technical summary",
          html_url: "https://arxiv.org/abs/2401.12345",
          pdf_url: "https://arxiv.org/pdf/2401.12345.pdf",
          image_url: "/v1/browser/trending/papers/arxiv%3A2401.12345/preview/content",
          citation_count: 99,
          influential_citation_count: 11,
          semantic_scholar_id: "s2-benchmark",
          code_url: "https://github.com/example/benchmark",
          code_url_source: "admin",
          code_index_status: "indexed",
          code_is_python: true,
          code_python_reason: "Python files found",
          venue: "ICLR",
          venue_year: 2026,
          venue_source: "s2_bulk",
          top_author_s2_id: "author-benchmark",
          source_type: "html",
          index_status: "indexed",
          indexed_at: "2026-04-22T00:00:00Z",
          upvote_count: 12,
          viewer_has_upvoted: false,
          comments: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const feed = await api.loadBrowserTrendingPapers("2026-04-21", "upvotes", 3, 10);
    const detail = await api.getBrowserTrendingPaper("arxiv:2401.12345");

    const [feedPath, feedInit] = fetchMock.mock.calls[0] as FetchCall;
    const feedUrl = new URL(String(feedPath), "http://localhost");
    expect(`${feedUrl.pathname}${feedUrl.search}`).toBe(
      "/v1/browser/trending?date=2026-04-21&sort=upvotes&page=3&page_size=10",
    );
    expect(feedInit?.method).toBe("GET");
    const [detailPath, detailInit] = fetchMock.mock.calls[1] as FetchCall;
    expect(requestPath(detailPath)).toBe("/v1/browser/trending/papers/arxiv%3A2401.12345");
    expect(detailInit?.method).toBe("GET");
    expect(feed.papers[0]?.upvote_count).toBe(12);
    expect(feed.has_next).toBe(true);
    expect(detail.summary_short).toBe("Technical summary");
    expect(detail.abstract).toBe("Full benchmark abstract");
    expect(detail.citation_count).toBe(99);
    expect(detail.code_url).toBe("https://github.com/example/benchmark");
  });

  it("loads generic paper discussion and attaches CSRF headers to paper write operations", async () => {
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
          paper_id: "arxiv:2401.12345",
          upvote_count: 8,
          viewer_has_upvoted: false,
          comments: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          paper_id: "arxiv:2401.12345",
          upvote_count: 9,
          viewer_has_upvoted: true,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          paper_id: "arxiv:2401.12345",
          upvote_count: 8,
          viewer_has_upvoted: false,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          paper_id: "arxiv:2401.12345",
          comments: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadSession();
    const discussion = await api.getBrowserPaperDiscussion("arxiv:2401.12345");
    await api.upvoteBrowserPaper("arxiv:2401.12345");
    await api.removeUpvoteBrowserPaper("arxiv:2401.12345");
    await api.createBrowserPaperComment("arxiv:2401.12345", {
      body: "Useful paper",
      parent_comment_id: null,
    });

    const [discussionPath, discussionInit] = fetchMock.mock.calls[1] as FetchCall;
    expect(requestPath(discussionPath)).toBe("/v1/browser/papers/arxiv%3A2401.12345/discussion");
    expect(discussionInit?.method).toBe("GET");
    expect(discussion.upvote_count).toBe(8);

    const [votePath, voteInit] = fetchMock.mock.calls[2] as FetchCall;
    expect(requestPath(votePath)).toBe("/v1/browser/papers/arxiv%3A2401.12345/upvote");
    expect(new Headers(voteInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
    expect(voteInit?.method).toBe("PUT");

    const [removeVotePath, removeVoteInit] = fetchMock.mock.calls[3] as FetchCall;
    expect(requestPath(removeVotePath)).toBe("/v1/browser/papers/arxiv%3A2401.12345/upvote");
    expect(new Headers(removeVoteInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
    expect(removeVoteInit?.method).toBe("DELETE");

    const [commentPath, commentInit] = fetchMock.mock.calls[4] as FetchCall;
    expect(requestPath(commentPath)).toBe("/v1/browser/papers/arxiv%3A2401.12345/comments");
    expect(new Headers(commentInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
    expect(commentInit?.method).toBe("POST");
  });

  it("attaches CSRF headers to trending write operations", async () => {
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
          paper_id: "arxiv:2401.12345",
          upvote_count: 9,
          viewer_has_upvoted: true,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          paper_id: "arxiv:2401.12345",
          comments: [],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadSession();
    await api.upvoteBrowserTrendingPaper("arxiv:2401.12345");
    await api.createBrowserTrendingPaperComment("arxiv:2401.12345", {
      body: "Useful paper",
      parent_comment_id: null,
    });

    const [, voteInit] = fetchMock.mock.calls[1] as FetchCall;
    expect(new Headers(voteInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
    expect(voteInit?.method).toBe("PUT");
    const [commentPath, commentInit] = fetchMock.mock.calls[2] as FetchCall;
    expect(requestPath(commentPath)).toBe(
      "/v1/browser/trending/papers/arxiv%3A2401.12345/comments",
    );
    expect(new Headers(commentInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");
    expect(commentInit?.method).toBe("POST");
  });

  it("falls back to the legacy admin jobs endpoint when overview is unavailable", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not Found" }, { status: 404 }))
      .mockResolvedValueOnce(
        jsonResponse([
          {
            job_id: "job_1",
            root_job_id: null,
            job_type: "index_paper",
            status: "completed",
            payload: { arxiv_id: "2401.12345" },
            error: null,
            attempts: 1,
            max_attempts: 3,
            next_run_at: null,
            last_attempted_at: null,
            priority: 10,
            created_at: "2026-04-21T00:00:00Z",
            updated_at: "2026-04-21T01:00:00Z",
            started_at: null,
            finished_at: null,
          },
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const overview = await api.loadBrowserAdminJobsOverview(200);

    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(requestPath(fetchMock.mock.calls[0]![0])).toBe("/v1/browser/admin/jobs/overview");
    expect(requestPath(fetchMock.mock.calls[1]![0])).toBe("/v1/browser/admin/jobs");
    expect(overview.worker).toBeNull();
    expect(overview.jobs[0]?.root_job_id).toBe("job_1");
    expect(overview.jobs[0]?.tracking_available).toBe(false);
    expect(overview.jobs[0]?.latest_status_summary).toBe("completed");
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

  it("calls browser-admin job endpoints with the browser session CSRF token", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          authenticated: true,
          user: {
            user_id: "user_1",
            email: "admin@example.com",
            display_name: "Admin",
            is_admin: true,
          },
          has_anthropic_api_key: false,
          csrf_token: "csrf-123",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          job_id: "job_admin_1",
          root_job_id: "job_admin_1",
          job_type: "index_paper",
          status: "pending",
          payload: { arxiv_id: "2401.12345", force: true },
          attempts: 0,
          max_attempts: 3,
          priority: -10,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ jobs: [], worker: null }))
      .mockResolvedValueOnce(
        jsonResponse({
          root_job_id: "job_admin_1",
          total_count: 0,
          offset: 0,
          limit: 100,
          has_more: false,
          papers: [],
        }),
      )
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadSession();
    await api.queueBrowserAdminPaperIndex("2401.12345", true);
    await api.loadBrowserAdminJobs("all", 75);
    await api.loadBrowserAdminJobsOverview(75);
    await api.loadBrowserAdminJobPapers("job_admin_1");
    await api.loadBrowserAdminPapers("benchmark");

    const [queuePath, queueInit] = fetchMock.mock.calls[1] as FetchCall;
    const queueUrl = new URL(String(queuePath), "http://localhost");
    expect(`${queueUrl.pathname}${queueUrl.search}`).toBe(
      "/v1/browser/admin/index/papers/arxiv/2401.12345?force=true",
    );
    expect(queueInit?.method).toBe("POST");
    expect(new Headers(queueInit?.headers).get("X-Paperbase-CSRF")).toBe("csrf-123");

    const [listPath, listInit] = fetchMock.mock.calls[2] as FetchCall;
    const listUrl = new URL(String(listPath), "http://localhost");
    expect(`${listUrl.pathname}${listUrl.search}`).toBe(
      "/v1/browser/admin/jobs?status=all&limit=75",
    );
    expect(listInit?.method).toBe("GET");

    const [overviewPath, overviewInit] = fetchMock.mock.calls[3] as FetchCall;
    const overviewUrl = new URL(String(overviewPath), "http://localhost");
    expect(`${overviewUrl.pathname}${overviewUrl.search}`).toBe(
      "/v1/browser/admin/jobs/overview?limit=75",
    );
    expect(overviewInit?.method).toBe("GET");

    const [papersPath, papersInit] = fetchMock.mock.calls[4] as FetchCall;
    const papersUrl = new URL(String(papersPath), "http://localhost");
    expect(`${papersUrl.pathname}${papersUrl.search}`).toBe(
      "/v1/browser/admin/jobs/job_admin_1/papers?offset=0&limit=100",
    );
    expect(papersInit?.method).toBe("GET");

    const [adminPapersPath, adminPapersInit] = fetchMock.mock.calls[5] as FetchCall;
    const adminPapersUrl = new URL(String(adminPapersPath), "http://localhost");
    expect(`${adminPapersUrl.pathname}${adminPapersUrl.search}`).toBe(
      "/v1/browser/admin/papers?q=benchmark&index_status=indexed&limit=50",
    );
    expect(adminPapersInit?.method).toBe("GET");
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

  it("creates local projects without a GitHub repository payload", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          project_id: "proj_2",
          name: "Local Lab",
          description: "Local only",
          is_default: false,
          session_count: 0,
          github_repo_url: "",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const created = await api.createProject("Local Lab", "Local only");

    const [createPath, createInit] = fetchMock.mock.calls[0] as FetchCall;
    expect(requestPath(createPath)).toBe("/v1/browser/projects");
    expect(createInit?.method).toBe("POST");
    expect(createInit?.body).toBe(
      JSON.stringify({ name: "Local Lab", description: "Local only", github_repo: null }),
    );
    expect(created.github_repo_url).toBe("");
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

  it("loads, starts, shares, and fetches project presentations", async () => {
    const presentation = {
      presentation_id: "pres_1",
      project_id: "proj_1",
      status: "completed",
      visual_code: "ReactDOM.createRoot(document.getElementById('root')).render('ok');",
      summary_markdown: "# Summary",
      slides: [
        {
          slide_id: "summary",
          title: "Summary",
          description: "A project presentation slide.",
          visual_type: "code",
          visual_code: "ReactDOM.createRoot(document.getElementById('root')).render('ok');",
          image_prompt: "",
          image_url: "",
        },
      ],
      audio_url: "/v1/browser/projects/proj_1/presentation/audio",
      audio_content_type: "audio/mpeg",
      is_public: false,
      share_token: null,
      share_url: "",
      error: "",
      job_id: null,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
      completed_at: "2026-04-21T00:00:00Z",
    };
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(jsonResponse(presentation))
      .mockResolvedValueOnce(jsonResponse({ ...presentation, status: "pending", job_id: "job_1" }))
      .mockResolvedValueOnce(jsonResponse({ ...presentation, status: "canceled", job_id: "job_1" }))
      .mockResolvedValueOnce(
        jsonResponse({
          ...presentation,
          is_public: true,
          share_token: "share_1",
          share_url: "http://127.0.0.1:8080/?presentation=share_1",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ ...presentation, is_public: true }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    await api.loadProjectPresentation("proj_1");
    await api.startProjectPresentation("proj_1", {
      explorations: [
        {
          exploration_id: "exp_1",
          paper_id: "paper_1",
          path: "papers/paper_1",
          title: "Paper",
          authors: "A",
          preview_image_url: "",
          pdf_url: "",
          annotations: [],
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
        },
      ],
      regenerate: true,
    });
    await api.cancelProjectPresentation("proj_1");
    await api.updateProjectPresentationShare("proj_1", true);
    await api.loadPublicProjectPresentation("share_1");

    expect(requestPath(fetchMock.mock.calls[0]?.[0] as RequestInfo)).toBe(
      "/v1/browser/projects/proj_1/presentation",
    );
    const [, startInit] = fetchMock.mock.calls[1] as FetchCall;
    expect(startInit?.method).toBe("POST");
    expect(startInit?.body).toContain('"regenerate":true');
    const [cancelPath, cancelInit] = fetchMock.mock.calls[2] as FetchCall;
    expect(requestPath(cancelPath)).toBe("/v1/browser/projects/proj_1/presentation/cancel");
    expect(cancelInit?.method).toBe("POST");
    const [sharePath, shareInit] = fetchMock.mock.calls[3] as FetchCall;
    expect(requestPath(sharePath)).toBe("/v1/browser/projects/proj_1/presentation/share");
    expect(shareInit?.body).toBe(JSON.stringify({ is_public: true }));
    expect(requestPath(fetchMock.mock.calls[4]?.[0] as RequestInfo)).toBe(
      "/v1/browser/project-presentations/public/share_1",
    );
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
                    'event: usage\ndata: {"model":"gpt-5.5","input_tokens":11234,"output_tokens":1,"total_tokens":11235,"context_window_tokens":1000000}\n\n' +
                    'event: item_delta\ndata: {"item_id":"msg_1","delta":"Ground',
                ),
              );
              controller.enqueue(
                encoder.encode(
                  'ed answer","delta_type":"output_text"}\n\nevent: item_completed\ndata: {"item":{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}}\n\nevent: turn_completed\ndata: {"thread":{"thread_id":"thread_1","project_id":"proj_1","project_name":"General","project_instructions":"","title":"New chat","created_at":"2026-04-21T00:00:00Z","updated_at":"2026-04-21T00:00:00Z","items":[{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}],"turns":[{"turn_id":"turn_1","thread_id":"thread_1","status":"completed","provider_response_id":"resp_1","started_at":"2026-04-21T00:00:00Z","completed_at":"2026-04-21T00:00:01Z","usage":null,"diff_summary":"","changed_paths":[],"exec_session_ids":[],"items":[{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}]}]},"turn":{"turn_id":"turn_1","thread_id":"thread_1","status":"completed","provider_response_id":"resp_1","started_at":"2026-04-21T00:00:00Z","completed_at":"2026-04-21T00:00:01Z","usage":null,"diff_summary":"","changed_paths":[],"exec_session_ids":[],"items":[{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"}]},"final_item":{"item_id":"msg_1","thread_id":"thread_1","turn_id":"turn_1","item_type":"assistant_message","role":"assistant","phase":"final_answer","call_id":null,"name":null,"content_text":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"},"has_openai_api_key":false,"state":"completed","usage":{"model":"gpt-5.5","input_tokens":11234,"output_tokens":27,"total_tokens":11261,"context_window_tokens":1000000}}\n\n',
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
          model: "gpt-5.5",
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
            model: "gpt-5.5",
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

  it("hydrates legacy chat threads from messages when item payloads are absent", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_legacy",
          project_id: "proj_1",
          project_name: "General",
          project_instructions: "",
          title: "Legacy thread",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          messages: [
            {
              message_id: "msg_legacy_1",
              role: "assistant",
              content_markdown: "Legacy grounded answer",
              citations: [],
              created_at: "2026-04-21T00:00:00Z",
              status: "completed",
            },
          ],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await loadApiModule();

    const thread = await api.getThread("thread_legacy");

    expect(thread.messages?.[0]?.content_markdown).toBe("Legacy grounded answer");
    expect(thread.items?.[0]?.content_text).toBe("Legacy grounded answer");
    expect(thread.turns?.[0]?.items[0]?.item_id).toBe("msg_legacy_1");
  });
});
