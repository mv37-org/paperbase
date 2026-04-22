import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => {
  class MockStreamingChatError extends Error {
    canFallback: boolean;

    constructor(message: string, canFallback: boolean) {
      super(message);
      this.name = "StreamingChatError";
      this.canFallback = canFallback;
    }
  }

  return {
    api: vi.fn(),
    createThread: vi.fn(),
    getBrowserPaper: vi.fn(),
    getBrowserPaperFigure: vi.fn(),
    getBrowserPaperFile: vi.fn(),
    getBrowserPaperTable: vi.fn(),
    getThread: vi.fn(),
    googleLoginUrl: vi.fn(() => "/v1/browser/auth/google/start"),
    loadSession: vi.fn(),
    loadThreads: vi.fn(),
    loadUploads: vi.fn(),
    logoutBrowserSession: vi.fn(),
    postForm: vi.fn(),
    sendThreadMessage: vi.fn(),
    sendThreadMessageStream: vi.fn(),
    suggestBrowserPapers: vi.fn(),
    StreamingChatError: MockStreamingChatError,
    uploadDocument: vi.fn(),
  };
});

vi.mock("./api", () => ({
  api: apiMocks.api,
  createThread: apiMocks.createThread,
  getBrowserPaper: apiMocks.getBrowserPaper,
  getBrowserPaperFigure: apiMocks.getBrowserPaperFigure,
  getBrowserPaperFile: apiMocks.getBrowserPaperFile,
  getBrowserPaperTable: apiMocks.getBrowserPaperTable,
  getThread: apiMocks.getThread,
  googleLoginUrl: apiMocks.googleLoginUrl,
  loadSession: apiMocks.loadSession,
  loadThreads: apiMocks.loadThreads,
  loadUploads: apiMocks.loadUploads,
  logoutBrowserSession: apiMocks.logoutBrowserSession,
  postForm: apiMocks.postForm,
  sendThreadMessage: apiMocks.sendThreadMessage,
  sendThreadMessageStream: apiMocks.sendThreadMessageStream,
  suggestBrowserPapers: apiMocks.suggestBrowserPapers,
  StreamingChatError: apiMocks.StreamingChatError,
  uploadDocument: apiMocks.uploadDocument,
}));

vi.mock("./LandingMentionEditor", () => ({
  LandingMentionEditor: () => <div data-testid="mention-editor" />,
}));

vi.mock("./MarkdownRenderer", () => ({
  MarkdownRenderer: ({ markdown }: { markdown: string }) => <div>{markdown}</div>,
}));

import App from "./App";

const OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";

function authenticatedSession() {
  return {
    authenticated: true,
    user: {
      user_id: "user_1",
      email: "user@example.com",
      display_name: "User Example",
      free_responses_used: 0,
    },
    remaining_free_responses: 3,
    has_anthropic_api_key: false,
    csrf_token: "csrf-123",
  };
}

function threadSummary(threadId: string, title: string) {
  return {
    thread_id: threadId,
    title,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    last_message_preview: "Latest note",
  };
}

function threadResponse(threadId: string, title: string, content = "Grounded answer") {
  return {
    thread_id: threadId,
    title,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    messages: [
      {
        message_id: "msg_1",
        role: "assistant",
        content_markdown: content,
        citations: [],
        created_at: "2026-04-21T00:00:00Z",
        status: "completed",
      },
    ],
  };
}

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    window.open = vi.fn();
    window.matchMedia = vi.fn().mockImplementation(() => ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    window.requestAnimationFrame = vi.fn((callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
    apiMocks.loadUploads.mockResolvedValue({ documents: [], available_tags: [] });
    apiMocks.loadThreads.mockResolvedValue({ threads: [] });
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Recovered thread"));
    apiMocks.getBrowserPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      arxiv_id: "2401.12345",
      title: "Drawer Paper",
      authors: "Drawer Author",
      abstract: "Drawer abstract",
      summary_short: "Drawer summary",
      summary_eli5: "Drawer simple summary",
      categories: "cs.AI",
      primary_category: "cs.AI",
      published: "2026-04-21",
      updated: "2026-04-21",
      pdf_url: "https://example.test/2401.12345.pdf",
      html_url: "https://example.test/2401.12345",
      citation_count: 7,
      influential_citation_count: 2,
      index_status: "indexed",
    });
    apiMocks.getBrowserPaperFile.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      path: "sections/intro.md",
      content: "Drawer content",
    });
    apiMocks.getBrowserPaperFigure.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      figure_id: "figure-1",
      caption: "Figure caption",
    });
    apiMocks.getBrowserPaperTable.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      table_id: "table-1",
      caption: "Table caption",
      rows: [["Metric", "Value"]],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the landing page when the browser session is not authenticated", async () => {
    apiMocks.loadSession.mockResolvedValue({
      authenticated: false,
      user: null,
      remaining_free_responses: 0,
      has_anthropic_api_key: false,
      csrf_token: null,
    });

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#landing-login-button")).toBeTruthy();
    });
    expect(document.querySelector("#app-shell")).toBeNull();
  });

  it("restores previously open tabs for an authenticated session", async () => {
    localStorage.setItem(OPEN_TABS_KEY, JSON.stringify(["thread_1"]));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadThreads.mockResolvedValue({
      threads: [threadSummary("thread_1", "Restored research thread")],
    });
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Restored research thread"));

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Restored research thread").length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(apiMocks.getThread).toHaveBeenCalledWith("thread_1");
    });
  });

  it("opens the cited-paper drawer from the URL state", async () => {
    window.history.replaceState({}, "", "/?paper=arxiv:2401.12345&paperPath=sections%2Fintro.md");
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Cited paper")).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByText("Drawer content")).toBeTruthy();
    });
    expect(apiMocks.getBrowserPaper).toHaveBeenCalledWith("arxiv:2401.12345");
    expect(apiMocks.getBrowserPaperFile).toHaveBeenCalledWith(
      "arxiv:2401.12345",
      "sections/intro.md",
    );
  });

  it("falls back to the non-streaming send path when the stream fails before any events", async () => {
    sessionStorage.setItem(PENDING_PROMPT_KEY, "Summarize the benchmark papers");
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue({
      thread_id: "thread_2",
      title: "New chat",
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
      messages: [],
    });
    apiMocks.sendThreadMessageStream.mockRejectedValue(
      new apiMocks.StreamingChatError("stream failed", true),
    );
    apiMocks.sendThreadMessage.mockResolvedValue({
      thread: threadResponse("thread_2", "New chat", "Fallback grounded answer"),
      assistant_message: {
        message_id: "msg_2",
        role: "assistant",
        content_markdown: "Fallback grounded answer",
        citations: [],
        created_at: "2026-04-21T00:00:00Z",
        status: "completed",
      },
      remaining_free_responses: 2,
      has_anthropic_api_key: false,
      state: "completed",
    });
    apiMocks.loadThreads.mockResolvedValue({
      threads: [threadSummary("thread_2", "New chat")],
    });

    render(<App />);

    await waitFor(() => {
      expect(apiMocks.sendThreadMessage).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.getByText("Fallback grounded answer")).toBeTruthy();
    });
    expect(apiMocks.sendThreadMessageStream).toHaveBeenCalledTimes(1);
  });
});
