import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    createProject: vi.fn(),
    createThread: vi.fn(),
    deleteProject: vi.fn(),
    deleteThread: vi.fn(),
    getBrowserPaper: vi.fn(),
    getBrowserPaperFigure: vi.fn(),
    getBrowserPaperFile: vi.fn(),
    getBrowserPaperTable: vi.fn(),
    getThread: vi.fn(),
    githubLoginUrl: vi.fn(() => "/v1/browser/auth/github/start"),
    googleLoginUrl: vi.fn(() => "/v1/browser/auth/google/start"),
    loadProjectSessions: vi.fn(),
    loadProjects: vi.fn(),
    loadSession: vi.fn(),
    loadUploads: vi.fn(),
    landingMentionEditor: vi.fn(() => <div data-testid="mention-editor" />),
    moveThreadToProject: vi.fn(),
    logoutBrowserSession: vi.fn(),
    postForm: vi.fn(),
    sendThreadMessage: vi.fn(),
    sendThreadMessageStream: vi.fn(),
    suggestBrowserPapers: vi.fn(),
    StreamingChatError: MockStreamingChatError,
    updateProject: vi.fn(),
    uploadDocument: vi.fn(),
  };
});

vi.mock("./api", () => ({
  api: apiMocks.api,
  createProject: apiMocks.createProject,
  createThread: apiMocks.createThread,
  deleteProject: apiMocks.deleteProject,
  deleteThread: apiMocks.deleteThread,
  getBrowserPaper: apiMocks.getBrowserPaper,
  getBrowserPaperFigure: apiMocks.getBrowserPaperFigure,
  getBrowserPaperFile: apiMocks.getBrowserPaperFile,
  getBrowserPaperTable: apiMocks.getBrowserPaperTable,
  getThread: apiMocks.getThread,
  githubLoginUrl: apiMocks.githubLoginUrl,
  googleLoginUrl: apiMocks.googleLoginUrl,
  loadProjectSessions: apiMocks.loadProjectSessions,
  loadProjects: apiMocks.loadProjects,
  loadSession: apiMocks.loadSession,
  loadUploads: apiMocks.loadUploads,
  moveThreadToProject: apiMocks.moveThreadToProject,
  logoutBrowserSession: apiMocks.logoutBrowserSession,
  postForm: apiMocks.postForm,
  sendThreadMessage: apiMocks.sendThreadMessage,
  sendThreadMessageStream: apiMocks.sendThreadMessageStream,
  suggestBrowserPapers: apiMocks.suggestBrowserPapers,
  StreamingChatError: apiMocks.StreamingChatError,
  updateProject: apiMocks.updateProject,
  uploadDocument: apiMocks.uploadDocument,
}));

vi.mock("./LandingMentionEditor", () => ({
  LandingMentionEditor: apiMocks.landingMentionEditor,
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
    settings: {
      anthropic_api_key_hint: "",
      has_daytona_api_key: false,
      daytona_api_key_hint: "",
      has_github_connection: false,
      github_login: "",
      github_oauth_available: true,
      weekly_papers_review_email: false,
    },
    csrf_token: "csrf-123",
  };
}

function projectSummary(projectId: string, name: string, sessionCount = 1) {
  return {
    project_id: projectId,
    name,
    description: "",
    is_default: name === "General",
    session_count: sessionCount,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
  };
}

function threadSummary(threadId: string, title: string, projectId = "proj_1") {
  return {
    thread_id: threadId,
    project_id: projectId,
    title,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    last_message_preview: "Latest note",
  };
}

function threadResponse(
  threadId: string,
  title: string,
  content = "Grounded answer",
  projectId = "proj_1",
  projectName = "General",
) {
  return {
    thread_id: threadId,
    project_id: projectId,
    project_name: projectName,
    project_instructions: "",
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

function emptyThreadResponse(threadId: string, projectId = "proj_1", projectName = "General") {
  return {
    thread_id: threadId,
    project_id: projectId,
    project_name: projectName,
    project_instructions: "",
    title: "New chat",
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    messages: [],
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
    apiMocks.loadProjects.mockResolvedValue({
      projects: [projectSummary("proj_1", "General")],
    });
    apiMocks.loadProjectSessions.mockResolvedValue({
      project_id: "proj_1",
      threads: [threadSummary("thread_1", "Recovered thread")],
      offset: 0,
      limit: 5,
      total_count: 1,
      has_more: false,
    });
    apiMocks.createProject.mockResolvedValue(projectSummary("proj_2", "Agents", 0));
    apiMocks.createThread.mockResolvedValue(threadResponse("thread_2", "New chat"));
    apiMocks.deleteProject.mockResolvedValue({ status: "deleted" });
    apiMocks.deleteThread.mockResolvedValue({ status: "deleted" });
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Recovered thread"));
    apiMocks.moveThreadToProject.mockResolvedValue(
      emptyThreadResponse("thread_2", "proj_2", "Agents"),
    );
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
    expect(apiMocks.landingMentionEditor).toHaveBeenCalled();
  });

  it("restores previously open tabs for an authenticated session", async () => {
    localStorage.setItem(OPEN_TABS_KEY, JSON.stringify(["thread_1"]));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Restored research thread"));

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Restored research thread").length).toBeGreaterThan(0);
    });
    expect(apiMocks.getThread).toHaveBeenCalledWith("thread_1");
    expect(apiMocks.loadProjects).toHaveBeenCalledTimes(1);
  });

  it("loads project sessions lazily when a project is expanded", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    expect(apiMocks.loadProjectSessions).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(apiMocks.loadProjectSessions).toHaveBeenCalledWith("proj_1", 5, 0);
    });
    await waitFor(() => {
      expect(screen.getByText("Recovered thread")).toBeTruthy();
    });
  });

  it("creates a project from the sidebar modal", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjects
      .mockResolvedValueOnce({ projects: [projectSummary("proj_1", "General")] })
      .mockResolvedValueOnce({
        projects: [projectSummary("proj_1", "General"), projectSummary("proj_2", "Agents", 0)],
      });
    apiMocks.loadProjectSessions.mockResolvedValue({
      project_id: "proj_2",
      threads: [],
      offset: 0,
      limit: 5,
      total_count: 0,
      has_more: false,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Create project"));
    fireEvent.change(screen.getByPlaceholderText("Project name"), {
      target: { value: "Agents" },
    });
    fireEvent.change(screen.getByPlaceholderText("Description, goal, or custom instructions"), {
      target: { value: "Investigate reliable agent systems." },
    });
    fireEvent.submit(document.querySelector("#project-modal form")!);

    await waitFor(() => {
      expect(apiMocks.createProject).toHaveBeenCalledWith(
        "Agents",
        "Investigate reliable agent systems.",
      );
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Start chat in Agents")).toBeTruthy();
    });
  });

  it("does not create a duplicate empty chat for the same project", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByText("New chat"));

    await waitFor(() => {
      expect(screen.getByText("Start the conversation")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Start chat in General"));

    expect(apiMocks.createThread).toHaveBeenCalledTimes(1);
    expect(apiMocks.moveThreadToProject).not.toHaveBeenCalled();
  });

  it("moves the current empty chat when starting in another project", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjects.mockResolvedValue({
      projects: [projectSummary("proj_1", "General"), projectSummary("proj_2", "Agents", 0)],
    });
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.moveThreadToProject.mockResolvedValue(
      emptyThreadResponse("thread_2", "proj_2", "Agents"),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Agents")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByText("New chat"));

    await waitFor(() => {
      expect(screen.getByText("Start the conversation")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Start chat in Agents"));

    await waitFor(() => {
      expect(apiMocks.moveThreadToProject).toHaveBeenCalledWith("thread_2", "proj_2");
    });
    expect(apiMocks.createThread).toHaveBeenCalledTimes(1);
  });

  it("opens the project menu from the action button", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Open project menu for General"));

    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: "Rename project" })).toBeTruthy();
    });
  });

  it("deletes a session from the project list after confirmation", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(screen.getByText("Recovered thread")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Delete Recovered thread"));
    fireEvent.click(screen.getByRole("button", { name: "Delete session" }));

    await waitFor(() => {
      expect(apiMocks.deleteThread).toHaveBeenCalledWith("thread_1");
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
  });

  it("falls back to the non-streaming send path when the stream fails before any events", async () => {
    sessionStorage.setItem(PENDING_PROMPT_KEY, "Summarize the benchmark papers");
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(threadResponse("thread_2", "New chat"));
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

    render(<App />);

    await waitFor(() => {
      expect(apiMocks.sendThreadMessage).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.getByText("Fallback grounded answer")).toBeTruthy();
    });
  });

  it("opens the settings modal from the account menu and saves notification preferences", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.api.mockResolvedValue({
      ...authenticatedSession(),
      settings: {
        ...authenticatedSession().settings,
        weekly_papers_review_email: true,
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#account-menu-button")).toBeTruthy();
    });

    fireEvent.click(document.querySelector("#account-menu-button") as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Inference", selected: true })).toBeTruthy();
    });
    expect(screen.getByRole("tab", { name: "Sandbox" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Version control" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Notifications" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Notifications" }));
    fireEvent.click(screen.getByLabelText("Weekly papers review email"));
    fireEvent.submit(document.querySelector("#settings-notifications-form") as HTMLFormElement);

    await waitFor(() => {
      expect(apiMocks.api).toHaveBeenCalledWith("/v1/browser/settings/notifications", {
        method: "PUT",
        body: JSON.stringify({ weekly_papers_review_email: true }),
      });
    });
  });

  it("sets explicit autocomplete hints on auth and settings key inputs", async () => {
    apiMocks.loadSession.mockResolvedValueOnce({
      authenticated: false,
      user: null,
      remaining_free_responses: 3,
      has_anthropic_api_key: false,
      settings: {
        anthropic_api_key_hint: "",
        has_daytona_api_key: false,
        daytona_api_key_hint: "",
        has_github_connection: false,
        github_login: "",
        github_oauth_available: false,
        weekly_papers_review_email: false,
      },
      csrf_token: null,
    });
    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(apiMocks.loadSession).toHaveBeenCalledTimes(1);
    });

    expect(
      document.querySelector("#login-form input[name='email']")?.getAttribute("autocomplete"),
    ).toBe("email");
    expect(
      document.querySelector("#login-form input[name='password']")?.getAttribute("autocomplete"),
    ).toBe("current-password");
    expect(
      document
        .querySelector("#signup-form input[name='display_name']")
        ?.getAttribute("autocomplete"),
    ).toBe("name");
    expect(
      document.querySelector("#signup-form input[name='password']")?.getAttribute("autocomplete"),
    ).toBe("new-password");
    expect(
      document.querySelector("#reset-form input[name='token']")?.getAttribute("autocomplete"),
    ).toBe("one-time-code");
    expect(
      document.querySelector("#reset-form input[name='password']")?.getAttribute("autocomplete"),
    ).toBe("new-password");

    unmount();

    apiMocks.loadSession.mockResolvedValueOnce(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#account-menu-button")).toBeTruthy();
    });

    fireEvent.click(document.querySelector("#account-menu-button") as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));

    await waitFor(() => {
      expect(document.querySelector("#settings-anthropic-form")).toBeTruthy();
    });

    expect(
      document
        .querySelector("#settings-anthropic-form input[name='api_key']")
        ?.getAttribute("autocomplete"),
    ).toBe("new-password");

    fireEvent.click(screen.getByRole("tab", { name: "Sandbox" }));

    await waitFor(() => {
      expect(document.querySelector("#settings-daytona-form")).toBeTruthy();
    });

    expect(
      document
        .querySelector("#settings-daytona-form input[name='api_key']")
        ?.getAttribute("autocomplete"),
    ).toBe("new-password");
  });
});
