import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type FetchCall = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(payload: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
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
            free_responses_used: 0,
          },
          remaining_free_responses: 3,
          has_anthropic_api_key: false,
          csrf_token: "csrf-123",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_1",
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
            free_responses_used: 0,
          },
          remaining_free_responses: 3,
          has_anthropic_api_key: false,
          csrf_token: "csrf-123",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: "Authentication required" }, { status: 401 }))
      .mockResolvedValueOnce(
        jsonResponse({
          thread_id: "thread_2",
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

  it("loads paper mention suggestions from the browser endpoint", async () => {
    const fetchMock = vi
      .fn<[RequestInfo | URL, RequestInit | undefined], Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({
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

    const suggestions = await api.suggestBrowserPapers("benchmark paper", 7);

    const [path, init] = fetchMock.mock.calls[0] as FetchCall;
    expect(path).toBe("/v1/browser/papers/suggest?q=benchmark+paper&limit=7");
    expect(init?.method).toBe("GET");
    expect(suggestions).toHaveLength(1);
    expect(suggestions[0]?.paper_id).toBe("arxiv:2401.12345");
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
                  'event: assistant_start\ndata: {"assistant_message":{"message_id":"msg_1","role":"assistant","content_markdown":"","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"streaming"}}\n\n' +
                    'event: delta\ndata: {"message_id":"msg_1","delta":"Ground',
                ),
              );
              controller.enqueue(
                encoder.encode(
                  'ed answer"}\n\nevent: done\ndata: {"assistant_message":{"message_id":"msg_1","role":"assistant","content_markdown":"Grounded answer","citations":[],"created_at":"2026-04-21T00:00:00Z","status":"completed"},"remaining_free_responses":2,"has_anthropic_api_key":false,"state":"completed"}\n\n',
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
        "assistant_start",
        {
          assistant_message: {
            message_id: "msg_1",
            role: "assistant",
            content_markdown: "",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "streaming",
          },
        },
      ],
      ["delta", { message_id: "msg_1", delta: "Grounded answer" }],
      [
        "done",
        {
          assistant_message: {
            message_id: "msg_1",
            role: "assistant",
            content_markdown: "Grounded answer",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          remaining_free_responses: 2,
          has_anthropic_api_key: false,
          state: "completed",
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
});
