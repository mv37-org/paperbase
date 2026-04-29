import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  class MockStreamingChatAbortError extends Error {
    constructor(message = "Streaming request aborted") {
      super(message);
      this.name = "StreamingChatAbortError";
    }
  }

  class MockApiError extends Error {
    status: number;
    payload: unknown;

    constructor(message: string, status: number, payload: unknown) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  return {
    ApiError: MockApiError,
    api: vi.fn(),
    createPythonFile: vi.fn(),
    createProject: vi.fn(),
    createThread: vi.fn(),
    deleteProject: vi.fn(),
    deletePythonFile: vi.fn(),
    deleteThread: vi.fn(),
    getBrowserPaper: vi.fn(),
    getBrowserPaperFigure: vi.fn(),
    getBrowserPaperFile: vi.fn(),
    getBrowserPaperTable: vi.fn(),
    getProjectRepoSync: vi.fn(),
    getPythonFile: vi.fn(),
    getThread: vi.fn(),
    githubLoginUrl: vi.fn(() => "/v1/browser/auth/github/start"),
    googleLoginUrl: vi.fn(() => "/v1/browser/auth/google/start"),
    importProjectNotebook: vi.fn(),
    loadGitHubRepositories: vi.fn(),
    loadGitHubRepoImportCandidates: vi.fn(),
    loadGitHubRepoMetadata: vi.fn(),
    loadProjectItems: vi.fn(),
    loadProjectSessions: vi.fn(),
    loadProjects: vi.fn(),
    loadSession: vi.fn(),
    loadUploads: vi.fn(),
    landingMentionEditor: vi.fn((props: Record<string, unknown>) => (
      <div data-testid={`mention-editor-${String(props.variant ?? "landing")}`}>
        <input
          aria-label={`mention-input-${String(props.variant ?? "landing")}`}
          value={String(props.value ?? "")}
          onChange={(event) =>
            (props.onChange as ((value: string) => void) | undefined)?.(event.target.value)
          }
        />
        {props.actionAccessory ? (
          <div data-testid="mention-action-accessory">{props.actionAccessory as any}</div>
        ) : null}
        {props.submitButtonId ? (
          <button
            id={String(props.submitButtonId)}
            type="button"
            disabled={props.submitMode === "stop" ? false : Boolean(props.disabled)}
            onClick={() => {
              if (props.submitMode === "stop") {
                (props.onStop as (() => void) | undefined)?.();
                return;
              }
              (props.onSubmit as (() => void) | undefined)?.();
            }}
          >
            {String(props.submitLabel ?? "Start")}
          </button>
        ) : null}
      </div>
    )),
    moveThreadToProject: vi.fn(),
    logoutBrowserSession: vi.fn(),
    postForm: vi.fn(),
    runPythonFile: vi.fn(),
    sendThreadMessage: vi.fn(),
    sendThreadMessageStream: vi.fn(),
    suggestBrowserPapers: vi.fn(),
    StreamingChatAbortError: MockStreamingChatAbortError,
    StreamingChatError: MockStreamingChatError,
    syncProjectRepo: vi.fn(),
    updateProject: vi.fn(),
    updatePythonFile: vi.fn(),
    uploadDocument: vi.fn(),
  };
});

vi.mock("./api", () => ({
  ApiError: apiMocks.ApiError,
  api: apiMocks.api,
  createPythonFile: apiMocks.createPythonFile,
  createProject: apiMocks.createProject,
  createThread: apiMocks.createThread,
  deleteProject: apiMocks.deleteProject,
  deletePythonFile: apiMocks.deletePythonFile,
  deleteThread: apiMocks.deleteThread,
  getBrowserPaper: apiMocks.getBrowserPaper,
  getBrowserPaperFigure: apiMocks.getBrowserPaperFigure,
  getBrowserPaperFile: apiMocks.getBrowserPaperFile,
  getBrowserPaperTable: apiMocks.getBrowserPaperTable,
  getProjectRepoSync: apiMocks.getProjectRepoSync,
  getPythonFile: apiMocks.getPythonFile,
  getThread: apiMocks.getThread,
  githubLoginUrl: apiMocks.githubLoginUrl,
  googleLoginUrl: apiMocks.googleLoginUrl,
  importProjectNotebook: apiMocks.importProjectNotebook,
  loadGitHubRepositories: apiMocks.loadGitHubRepositories,
  loadGitHubRepoImportCandidates: apiMocks.loadGitHubRepoImportCandidates,
  loadGitHubRepoMetadata: apiMocks.loadGitHubRepoMetadata,
  loadProjectItems: apiMocks.loadProjectItems,
  loadProjectSessions: apiMocks.loadProjectSessions,
  loadProjects: apiMocks.loadProjects,
  loadSession: apiMocks.loadSession,
  loadUploads: apiMocks.loadUploads,
  moveThreadToProject: apiMocks.moveThreadToProject,
  logoutBrowserSession: apiMocks.logoutBrowserSession,
  postForm: apiMocks.postForm,
  runPythonFile: apiMocks.runPythonFile,
  sendThreadMessage: apiMocks.sendThreadMessage,
  sendThreadMessageStream: apiMocks.sendThreadMessageStream,
  suggestBrowserPapers: apiMocks.suggestBrowserPapers,
  StreamingChatAbortError: apiMocks.StreamingChatAbortError,
  StreamingChatError: apiMocks.StreamingChatError,
  syncProjectRepo: apiMocks.syncProjectRepo,
  updateProject: apiMocks.updateProject,
  updatePythonFile: apiMocks.updatePythonFile,
  uploadDocument: apiMocks.uploadDocument,
}));

vi.mock("./LandingMentionEditor", () => ({
  LandingMentionEditor: apiMocks.landingMentionEditor,
}));

vi.mock("./MarkdownRenderer", () => ({
  MarkdownRenderer: ({ markdown }: { markdown: string }) => <div>{markdown}</div>,
}));

vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (value: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-editor"
      value={value ?? ""}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
  DiffEditor: ({ original, modified }: { original?: string; modified?: string }) => (
    <div data-testid="monaco-diff-editor">
      <span>{original ?? ""}</span>
      <span>{modified ?? ""}</span>
    </div>
  ),
}));

import App from "./App";

const OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
const SELECTED_PROJECT_KEY = "paperbase-selected-project";

function authenticatedSession(
  overrides: Partial<{
    remaining_free_responses: number;
    has_anthropic_api_key: boolean;
    csrf_token: string | null;
    settings: Partial<{
      anthropic_api_key_hint: string;
      has_modal_credentials: boolean;
      modal_token_id_hint: string;
      has_github_connection: boolean;
      github_login: string;
      github_oauth_available: boolean;
      github_manage_access_url: string;
      weekly_papers_review_email: boolean;
      appearance_theme: "light" | "dark";
    }>;
  }> = {},
) {
  return {
    authenticated: true,
    user: {
      user_id: "user_1",
      email: "user@example.com",
      display_name: "User Example",
      free_responses_used: 0,
    },
    remaining_free_responses: overrides.remaining_free_responses ?? 3,
    has_anthropic_api_key: overrides.has_anthropic_api_key ?? false,
    settings: {
      anthropic_api_key_hint: "",
      has_modal_credentials: false,
      modal_token_id_hint: "",
      has_github_connection: false,
      github_login: "",
      github_oauth_available: true,
      github_manage_access_url: "https://github.com/settings/connections/applications",
      weekly_papers_review_email: false,
      appearance_theme: "light",
      ...overrides.settings,
    },
    csrf_token: overrides.csrf_token ?? "csrf-123",
  };
}

function projectSummary(projectId: string, name: string, sessionCount = 1, githubRepoUrl = "") {
  return {
    project_id: projectId,
    name,
    description: "",
    is_default: name === "General",
    session_count: sessionCount,
    github_repo_url: githubRepoUrl,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
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

function projectItemThread(threadId: string, title: string, projectId = "proj_1") {
  return {
    item_id: threadId,
    item_kind: "thread",
    project_id: projectId,
    title,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    thread_id: threadId,
    file_id: null,
    last_message_preview: "Latest note",
    filename: "",
    file_kind: "python",
    repo_path: "",
  };
}

function pythonFileResponse(
  fileId: string,
  filename = "something.py",
  content = "",
  projectId = "proj_1",
  repoPath?: string,
) {
  return {
    file_id: fileId,
    project_id: projectId,
    filename,
    file_kind: filename.endsWith(".ipynb") ? "notebook" : "python",
    repo_path:
      repoPath ?? (filename.endsWith(".ipynb") ? `notebooks/${filename}` : `src/${filename}`),
    content,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
  };
}

function projectItemFile(
  fileId: string,
  filename = "something.py",
  projectId = "proj_1",
  repoPath?: string,
) {
  return {
    item_id: fileId,
    item_kind: "python_file",
    project_id: projectId,
    title: filename,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    thread_id: null,
    file_id: fileId,
    last_message_preview: "",
    filename,
    file_kind: filename.endsWith(".ipynb") ? "notebook" : "python",
    repo_path:
      repoPath ?? (filename.endsWith(".ipynb") ? `notebooks/${filename}` : `src/${filename}`),
  };
}

function projectItemsPage(
  projectId: string,
  items: Array<ReturnType<typeof projectItemThread> | ReturnType<typeof projectItemFile>>,
) {
  return {
    project_id: projectId,
    items,
    offset: 0,
    limit: 25,
    total_count: items.length,
    has_more: false,
  };
}

function repoSyncResponse(projectId = "proj_1", repoExists = false) {
  return {
    project_id: projectId,
    repo_exists: repoExists,
    github_owner_login: repoExists ? "octocat" : "",
    github_repo_name: repoExists ? "paperbase-general-123abc" : "",
    github_repo_url: repoExists ? "https://github.com/octocat/paperbase-general-123abc" : "",
    base_branch: "main",
    default_branch: "main",
    last_sync_commit_sha: repoExists ? "abc123" : "",
    last_sync_commit_url: repoExists
      ? "https://github.com/octocat/paperbase-general-123abc/commit/abc123"
      : "",
    last_synced_at: repoExists ? "2026-04-21T00:00:00Z" : null,
  };
}

function repoConflictError(projectId = "proj_1") {
  return new apiMocks.ApiError("Resolve GitHub conflicts", 409, {
    detail: {
      code: "github_merge_conflict",
      message: "Resolve GitHub conflicts",
      project_id: projectId,
      repo_exists: true,
      github_owner_login: "octocat",
      github_repo_name: "paperbase-general-123abc",
      github_repo_url: "https://github.com/octocat/paperbase-general-123abc",
      base_branch: "main",
      default_branch: "main",
      base_commit_sha: "abc1234",
      remote_head_sha: "def5678",
      conflicts: [
        {
          path: "src/something.py",
          file_kind: "python",
          base_content: "print('base')",
          local_content: "print('local')",
          remote_content: "print('remote')",
        },
      ],
    },
  });
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
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems = [projectItemThread("thread_1", "Recovered thread", projectId)];
        const filteredItems = itemKind
          ? allItems.filter((item) => item.item_kind === itemKind)
          : allItems;
        const pageItems = filteredItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: filteredItems.length,
          has_more: offset + pageItems.length < filteredItems.length,
        };
      },
    );
    apiMocks.createProject.mockResolvedValue(projectSummary("proj_2", "Agents", 0));
    apiMocks.createThread.mockResolvedValue(threadResponse("thread_2", "New chat"));
    apiMocks.createPythonFile.mockResolvedValue(pythonFileResponse("file_1"));
    apiMocks.deleteProject.mockResolvedValue({ status: "deleted" });
    apiMocks.deletePythonFile.mockResolvedValue({
      file_id: "file_1",
      project_id: "proj_1",
      filename: "something.py",
      deleted: true,
    });
    apiMocks.deleteThread.mockResolvedValue({ status: "deleted" });
    apiMocks.importProjectNotebook.mockResolvedValue(
      pythonFileResponse("file_2", "notebook.ipynb", "# %%\nprint('notebook')\n"),
    );
    apiMocks.loadGitHubRepositories.mockResolvedValue({
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
    });
    apiMocks.loadGitHubRepoImportCandidates.mockResolvedValue({ candidates: [] });
    apiMocks.loadGitHubRepoMetadata.mockResolvedValue({
      owner: "octocat",
      name: "existing-repo",
      html_url: "https://github.com/octocat/existing-repo",
      default_branch: "main",
      branches: [{ name: "main", commit_sha: "abc123", is_default: true }],
    });
    apiMocks.getProjectRepoSync.mockResolvedValue(repoSyncResponse());
    apiMocks.getPythonFile.mockResolvedValue(pythonFileResponse("file_1"));
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Recovered thread"));
    apiMocks.moveThreadToProject.mockResolvedValue(
      emptyThreadResponse("thread_2", "proj_2", "Agents"),
    );
    apiMocks.runPythonFile.mockResolvedValue({
      file_id: "file_1",
      status: "completed",
      stdout: "ran",
      stderr: "",
      exit_code: 0,
      started_at: "2026-04-21T00:00:00Z",
      finished_at: "2026-04-21T00:00:01Z",
    });
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
    apiMocks.syncProjectRepo.mockResolvedValue(repoSyncResponse("proj_1", true));
    apiMocks.updatePythonFile.mockImplementation(
      async (fileId: string, payload: { filename?: string | null; content?: string | null }) =>
        pythonFileResponse(fileId, payload.filename ?? "something.py", payload.content ?? ""),
    );
  });

  afterEach(() => {
    document.documentElement.classList.remove("authenticated-theme-dark");
    document.body.classList.remove("authenticated-theme-dark");
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
    const tabElements = [...document.querySelectorAll<HTMLElement>(".chat-tab")];
    const restoredTab = tabElements.find((element) =>
      element.textContent?.includes("Restored research thread"),
    );
    expect(restoredTab?.querySelector(".chat-tab-thread-indicator")).toBeNull();
    expect(document.querySelectorAll(".chat-tab-thread-indicator")).toHaveLength(0);
    const debugStrip = screen.getByTestId("thread-debug-strip");
    expect(debugStrip.textContent).toContain("user user_1");
    expect(debugStrip.textContent).toContain("project proj_1");
    expect(debugStrip.textContent).toContain("thread thread_1");
    expect(debugStrip.textContent).toContain("assistant msg_1");
    expect(screen.getByRole("button", { name: "Copy chat debug bundle" })).toBeTruthy();
  });

  it("restores the previously selected project and keeps only that tree expanded", async () => {
    localStorage.setItem(SELECTED_PROJECT_KEY, "proj_2");
    localStorage.setItem(OPEN_TABS_KEY, JSON.stringify(["thread_1"]));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjects.mockResolvedValue({
      projects: [projectSummary("proj_1", "General"), projectSummary("proj_2", "Agents", 0)],
    });
    apiMocks.getThread.mockResolvedValue(
      threadResponse(
        "thread_1",
        "Restored research thread",
        "Grounded answer",
        "proj_1",
        "General",
      ),
    );
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems =
          projectId === "proj_2"
            ? [projectItemThread("thread_2", "Agents thread", projectId)]
            : [projectItemThread("thread_1", "General thread", projectId)];
        const filteredItems = itemKind
          ? allItems.filter((item) => item.item_kind === itemKind)
          : allItems;
        const pageItems = filteredItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: filteredItems.length,
          has_more: offset + pageItems.length < filteredItems.length,
        };
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Agents thread")).toBeTruthy();
    });
    expect(screen.queryByText("General thread")).toBeNull();
    expect(apiMocks.loadProjectItems).toHaveBeenCalledWith("proj_2", 5, 0, "python_file");
    expect(apiMocks.loadProjectItems).toHaveBeenCalledWith("proj_2", 5, 0, "thread");
    expect(apiMocks.loadProjectItems).not.toHaveBeenCalledWith("proj_1", 5, 0, "python_file");
    expect(apiMocks.loadProjectItems).not.toHaveBeenCalledWith("proj_1", 5, 0, "thread");

    const activeProject = document.querySelector<HTMLElement>(".project-card.active");
    expect(activeProject?.textContent).toContain("Agents");
    const expandedProjects = [...document.querySelectorAll<HTMLElement>(".project-group.expanded")];
    expect(expandedProjects).toHaveLength(1);
    expect(expandedProjects[0]?.textContent).toContain("Agents");
  });

  it("loads project sessions lazily when a project is expanded", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    expect(apiMocks.loadProjectItems).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(apiMocks.loadProjectItems).toHaveBeenCalledWith("proj_1", 5, 0, "python_file");
      expect(apiMocks.loadProjectItems).toHaveBeenCalledWith("proj_1", 5, 0, "thread");
    });
    await waitFor(() => {
      expect(screen.getByText("Recovered thread")).toBeTruthy();
    });
    const categoryLabels = [
      ...document.querySelectorAll<HTMLElement>(".project-tree-category-label"),
    ].map((element) => element.textContent?.trim());
    expect(categoryLabels).toEqual([
      "Workflows",
      "Experiments",
      "Data",
      "Notes",
      "Sessions",
      "Code",
    ]);
    expect(document.querySelectorAll(".project-tree-category-count")).toHaveLength(2);
    expect(screen.getByText("Home")).toBeTruthy();
    expect(screen.getByText("Workflows")).toBeTruthy();
    expect(screen.getByText("Experiments")).toBeTruthy();
    expect(screen.getByText("Notes")).toBeTruthy();
    expect(screen.getByText("Data")).toBeTruthy();
    expect(screen.getByText("Sessions")).toBeTruthy();
    expect(screen.getByText("Code")).toBeTruthy();
    expect(screen.getByLabelText("Collapse all sections in General")).toBeTruthy();

    fireEvent.click(screen.getByText("Sessions"));
    expect(screen.queryByText("Recovered thread")).toBeNull();
    fireEvent.click(screen.getByText("Sessions"));
    expect(await screen.findByText("Recovered thread")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Collapse all sections in General"));
    expect(screen.queryByText("Recovered thread")).toBeNull();
    expect(screen.queryByText("Workflows")).toBeNull();
    expect(screen.queryByText("Home")).toBeNull();
    expect(screen.getByLabelText("Expand all sections in General")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Expand all sections in General"));
    expect(await screen.findByText("Workflows")).toBeTruthy();
    expect(await screen.findByText("Recovered thread")).toBeTruthy();

    fireEvent.click(screen.getByText("Home"));
    expect(await screen.findByText("Project Home")).toBeTruthy();
  });

  it("paginates project sidebar code inside the code section and links to GitHub", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjects.mockResolvedValue({
      projects: [
        projectSummary(
          "proj_1",
          "General",
          3,
          "https://github.com/octocat/paperbase-general-123abc",
        ),
      ],
    });
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems =
          itemKind === "python_file"
            ? [
                projectItemFile("file_1", "analysis-1.py", projectId),
                projectItemFile("file_2", "analysis-2.py", projectId),
                projectItemFile("file_3", "analysis-3.py", projectId),
                projectItemFile("file_4", "analysis-4.py", projectId),
                projectItemFile("file_5", "analysis-5.py", projectId),
                projectItemFile("file_6", "analysis-6.py", projectId),
              ]
            : [projectItemThread("thread_1", "Recovered thread", projectId)];
        const pageItems = allItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: allItems.length,
          has_more: offset + pageItems.length < allItems.length,
        };
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);

    const repoLink = await screen.findByLabelText("Open GitHub repository for General");
    expect(repoLink.getAttribute("href")).toBe(
      "https://github.com/octocat/paperbase-general-123abc",
    );
    expect(await screen.findByText("analysis-1.py")).toBeTruthy();
    expect(screen.getByText("analysis-5.py")).toBeTruthy();
    expect(screen.queryByText("analysis-6.py")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show more" }));

    await waitFor(() => {
      expect(apiMocks.loadProjectItems).toHaveBeenCalledWith("proj_1", 5, 5, "python_file");
    });
    expect(await screen.findByText("analysis-6.py")).toBeTruthy();
  });

  it("shows repo path context for duplicate Python basenames in the sidebar and tabs", async () => {
    localStorage.setItem(
      OPEN_TABS_KEY,
      JSON.stringify([
        { kind: "python_file", id: "file_1" },
        { kind: "python_file", id: "file_2" },
      ]),
    );
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems =
          itemKind === "python_file"
            ? [
                projectItemFile("file_1", "train.py", projectId, "src/models/train.py"),
                projectItemFile("file_2", "train.py", projectId, "experiments/models/train.py"),
              ]
            : [projectItemThread("thread_1", "Recovered thread", projectId)];
        const pageItems = allItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: allItems.length,
          has_more: offset + pageItems.length < allItems.length,
        };
      },
    );
    apiMocks.getPythonFile.mockImplementation(async (fileId: string) => {
      if (fileId === "file_1") {
        return pythonFileResponse(
          "file_1",
          "train.py",
          "print('a')\n",
          "proj_1",
          "src/models/train.py",
        );
      }
      return pythonFileResponse(
        "file_2",
        "train.py",
        "print('b')\n",
        "proj_1",
        "experiments/models/train.py",
      );
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Close src/models/train.py" })).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "Close experiments/models/train.py" })).toBeTruthy();

    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(screen.getAllByText("train.py")).toHaveLength(2);
    });
    expect(screen.getAllByText("src/models/train.py").length).toBeGreaterThan(0);
    expect(screen.getAllByText("experiments/models/train.py").length).toBeGreaterThan(0);
  });

  it("paginates project sessions inside the sessions section", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems =
          itemKind === "thread"
            ? [
                projectItemThread("thread_1", "Session 1", projectId),
                projectItemThread("thread_2", "Session 2", projectId),
                projectItemThread("thread_3", "Session 3", projectId),
                projectItemThread("thread_4", "Session 4", projectId),
                projectItemThread("thread_5", "Session 5", projectId),
                projectItemThread("thread_6", "Session 6", projectId),
              ]
            : [projectItemFile("file_1", "analysis.py", projectId)];
        const pageItems = allItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: allItems.length,
          has_more: offset + pageItems.length < allItems.length,
        };
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);

    expect(await screen.findByText("Session 1")).toBeTruthy();
    expect(screen.getByText("Session 5")).toBeTruthy();
    expect(screen.queryByText("Session 6")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show more" }));

    await waitFor(() => {
      expect(
        apiMocks.loadProjectItems.mock.calls.some(
          (call) => call[0] === "proj_1" && call[2] > 0 && call[3] === "thread",
        ),
      ).toBe(true);
    });
    expect(await screen.findByText("Session 6")).toBeTruthy();
  });

  it("cache-busts public icon asset urls", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByLabelText("Open settings")).toBeTruthy();
    });

    const settingsButton = screen.getByLabelText("Open settings");
    const settingsIcon = settingsButton.querySelector("img");
    expect(settingsIcon?.getAttribute("src")).toMatch(/\/settings\.svg\?v=pb-/);
  });

  it("creates a project from the sidebar modal", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ settings: { has_github_connection: true, github_login: "octocat" } }),
    );
    apiMocks.loadProjects
      .mockResolvedValueOnce({ projects: [projectSummary("proj_1", "General")] })
      .mockResolvedValueOnce({
        projects: [projectSummary("proj_1", "General"), projectSummary("proj_2", "Agents", 0)],
      });
    apiMocks.loadProjectItems.mockResolvedValue(projectItemsPage("proj_2", []));

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
    expect(screen.queryByPlaceholderText("New GitHub repo name")).toBeNull();
    fireEvent.submit(document.querySelector("#project-modal form")!);
    expect(await screen.findByText("Choose a GitHub repository.")).toBeTruthy();
    expect(apiMocks.createProject).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Repository" }));
    fireEvent.click(screen.getByRole("option", { name: /Create new repo/ }));
    fireEvent.change(screen.getByPlaceholderText("New GitHub repo name"), {
      target: { value: "agent-lab" },
    });
    fireEvent.submit(document.querySelector("#project-modal form")!);

    await waitFor(() => {
      expect(apiMocks.createProject).toHaveBeenCalledWith(
        "Agents",
        "Investigate reliable agent systems.",
        {
          mode: "create",
          name: "agent-lab",
          base_branch: "main",
          import_paths: [],
        },
      );
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Start chat in Agents")).toBeTruthy();
    });
  });

  it("searches the custom empty-state project picker and switches projects", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjects.mockResolvedValue({
      projects: [
        projectSummary("proj_1", "General"),
        projectSummary("proj_2", "Agents", 0),
        projectSummary("proj_3", "Biology", 0),
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "Add python file" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add notes" })).toBeTruthy();
    expect(screen.queryByTestId("thread-debug-strip")).toBeNull();

    fireEvent.click(screen.getByLabelText("Open empty state project picker"));

    await waitFor(() => {
      expect(screen.getByRole("listbox", { name: "Choose project" })).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Search projects"), {
      target: { value: "Age" },
    });

    expect(screen.getByRole("option", { name: "Agents" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "General" })).toBeNull();

    fireEvent.click(screen.getByRole("option", { name: "Agents" }));

    await waitFor(() => {
      expect(document.querySelector("#empty-state-project-select-trigger")?.textContent).toContain(
        "Agents",
      );
    });
  });

  it("opens the new project modal from the empty-state project picker", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ settings: { has_github_connection: true, github_login: "octocat" } }),
    );
    apiMocks.loadProjects
      .mockResolvedValueOnce({
        projects: [projectSummary("proj_1", "General"), projectSummary("proj_2", "Agents", 0)],
      })
      .mockResolvedValueOnce({
        projects: [
          projectSummary("proj_1", "General"),
          projectSummary("proj_2", "Agents", 0),
          projectSummary("proj_3", "Benchmarks", 0),
        ],
      });
    apiMocks.loadProjectItems.mockResolvedValue(projectItemsPage("proj_3", []));
    apiMocks.createProject.mockResolvedValue(projectSummary("proj_3", "Benchmarks", 0));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Open empty state project picker"));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create new project" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create new project" }));

    await waitFor(() => {
      expect(screen.getByText("New project")).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText("Project name"), {
      target: { value: "Benchmarks" },
    });
    fireEvent.change(screen.getByPlaceholderText("Description, goal, or custom instructions"), {
      target: { value: "Track benchmark runs and profiling." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Repository" }));
    fireEvent.click(screen.getByRole("option", { name: /Create new repo/ }));
    fireEvent.change(screen.getByPlaceholderText("New GitHub repo name"), {
      target: { value: "benchmarks" },
    });
    fireEvent.submit(document.querySelector("#project-modal form")!);

    await waitFor(() => {
      expect(apiMocks.createProject).toHaveBeenCalledWith(
        "Benchmarks",
        "Track benchmark runs and profiling.",
        {
          mode: "create",
          name: "benchmarks",
          base_branch: "main",
          import_paths: [],
        },
      );
    });
    await waitFor(() => {
      expect(document.querySelector("#empty-state-project-select-trigger")?.textContent).toContain(
        "Benchmarks",
      );
    });
  });

  it("blocks new project creation until GitHub is connected", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Create project"));

    await waitFor(() => {
      expect(screen.getByText("Connect GitHub account")).toBeTruthy();
    });
    expect((screen.getByLabelText("Project name") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Description") as HTMLTextAreaElement).disabled).toBe(true);
    expect(screen.getByText(/GitHub is the source of truth for project files/)).toBeTruthy();
    expect(
      (document.querySelector("#project-modal button[type='submit']") as HTMLButtonElement | null)
        ?.disabled,
    ).toBe(true);
    expect(apiMocks.createProject).not.toHaveBeenCalled();
  });

  it("refreshes the repository dropdown from GitHub", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ settings: { has_github_connection: true, github_login: "octocat" } }),
    );
    apiMocks.loadGitHubRepositories.mockResolvedValueOnce({ repos: [] });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Create project"));
    await waitFor(() => {
      expect(apiMocks.loadGitHubRepositories).toHaveBeenCalledTimes(1);
    });

    const refreshedRepos = {
      repos: [
        {
          owner: "octocat",
          name: "newly-added",
          full_name: "octocat/newly-added",
          html_url: "https://github.com/octocat/newly-added",
          default_branch: "main",
          private: true,
        },
      ],
    };
    let resolveRefresh: (value: typeof refreshedRepos) => void = () => {};
    apiMocks.loadGitHubRepositories.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByRole("button", { name: "Refreshing..." })).toBeTruthy();

    await act(async () => {
      resolveRefresh(refreshedRepos);
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
    });
    expect(apiMocks.loadGitHubRepositories).toHaveBeenCalledTimes(2);
  });

  it("defaults existing repo project creation to import all relevant files", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ settings: { has_github_connection: true, github_login: "octocat" } }),
    );
    apiMocks.loadGitHubRepoMetadata.mockResolvedValue({
      owner: "octocat",
      name: "existing-repo",
      html_url: "https://github.com/octocat/existing-repo",
      default_branch: "main",
      branches: [{ name: "main", commit_sha: "abc123", is_default: true }],
    });
    apiMocks.loadGitHubRepoImportCandidates.mockResolvedValue({
      owner: "octocat",
      name: "existing-repo",
      base_branch: "main",
      candidates: [
        { path: "src/analysis.py", file_kind: "python", size: 18 },
        { path: "notebooks/demo.ipynb", file_kind: "notebook", size: 128 },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Create project"));
    fireEvent.change(screen.getByPlaceholderText("Project name"), {
      target: { value: "Repo Import" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Repository" }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /octocat\/existing-repo/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("option", { name: /octocat\/existing-repo/ }));

    const importAllCheckbox = await screen.findByLabelText("Import all 2 relevant files");
    expect((importAllCheckbox as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/Python scripts and Jupyter notebooks/)).toBeTruthy();
    expect(screen.queryByText("2 importable files found.")).toBeNull();
    expect(screen.queryByText("notebooks/demo.ipynb")).toBeNull();

    fireEvent.submit(document.querySelector("#project-modal form")!);

    await waitFor(() => {
      expect(apiMocks.createProject).toHaveBeenCalledWith("Repo Import", "", {
        mode: "existing",
        owner: "octocat",
        name: "existing-repo",
        base_branch: "main",
        import_paths: ["src/analysis.py", "notebooks/demo.ipynb"],
      });
    });
  });

  it("creates a project from an existing repo with branch and custom import selection", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ settings: { has_github_connection: true, github_login: "octocat" } }),
    );
    apiMocks.loadGitHubRepoMetadata.mockResolvedValue({
      owner: "octocat",
      name: "existing-repo",
      html_url: "https://github.com/octocat/existing-repo",
      default_branch: "main",
      branches: [
        { name: "main", commit_sha: "abc123", is_default: true },
        { name: "dev", commit_sha: "def456", is_default: false },
      ],
    });
    apiMocks.loadGitHubRepoImportCandidates.mockResolvedValue({
      owner: "octocat",
      name: "existing-repo",
      base_branch: "dev",
      candidates: [
        { path: "src/analysis.py", file_kind: "python", size: 18 },
        { path: "notebooks/demo.ipynb", file_kind: "notebook", size: 128 },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Create project"));
    fireEvent.change(screen.getByPlaceholderText("Project name"), {
      target: { value: "Repo Import" },
    });
    fireEvent.change(screen.getByPlaceholderText("Description, goal, or custom instructions"), {
      target: { value: "Import selected files." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Repository" }));
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /octocat\/existing-repo/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("option", { name: /octocat\/existing-repo/ }));

    await waitFor(() => {
      expect(screen.getByLabelText("Import all 2 relevant files")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Base branch"), {
      target: { value: "dev" },
    });
    await waitFor(() => {
      expect(apiMocks.loadGitHubRepoImportCandidates).toHaveBeenLastCalledWith(
        "octocat",
        "existing-repo",
        "dev",
      );
    });
    fireEvent.click(screen.getByLabelText("Import all 2 relevant files"));
    await waitFor(() => {
      expect(screen.getByText("notebooks/demo.ipynb")).toBeTruthy();
    });
    const analysisCheckbox = screen
      .getByText("src/analysis.py")
      .closest("label")
      ?.querySelector("input");
    expect(analysisCheckbox).toBeTruthy();
    fireEvent.click(analysisCheckbox!);
    const notebookCheckbox = screen
      .getByText("notebooks/demo.ipynb")
      .closest("label")
      ?.querySelector("input");
    expect(notebookCheckbox).toBeTruthy();
    expect(notebookCheckbox!.checked).toBe(true);
    fireEvent.submit(document.querySelector("#project-modal form")!);

    await waitFor(() => {
      expect(apiMocks.createProject).toHaveBeenCalledWith("Repo Import", "Import selected files.", {
        mode: "existing",
        owner: "octocat",
        name: "existing-repo",
        base_branch: "dev",
        import_paths: ["notebooks/demo.ipynb"],
      });
    });
  });

  it("imports uploaded notebooks into the selected project", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ settings: { has_github_connection: true, github_login: "octocat" } }),
    );
    apiMocks.importProjectNotebook.mockResolvedValue(
      pythonFileResponse("file_2", "colab_export.ipynb", "# %%\nprint('colab')\n"),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    const input = document.querySelector("#project-notebook-upload-file") as HTMLInputElement;
    const file = new File(['{"nbformat":4,"cells":[]}'], "colab_export.ipynb", {
      type: "application/x-ipynb+json",
    });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(apiMocks.importProjectNotebook).toHaveBeenCalledWith("proj_1", file);
    });
    expect(screen.getByText("colab_export.ipynb")).toBeTruthy();
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
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getAllByText("General")[0]!);
    await waitFor(() => {
      expect(screen.getByLabelText("Start chat in General")).toBeTruthy();
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
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getAllByText("Agents")[0]!);
    await waitFor(() => {
      expect(screen.getByLabelText("Start chat in Agents")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("Start chat in Agents"));

    await waitFor(() => {
      expect(apiMocks.moveThreadToProject).toHaveBeenCalledWith("thread_2", "proj_2");
    });
    expect(apiMocks.createThread).toHaveBeenCalledTimes(1);
  });

  it("requires GitHub connection before creating a Python file", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Add python file" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Connect GitHub account" })).toBeTruthy();
    });
    expect(apiMocks.createPythonFile).not.toHaveBeenCalled();
  });

  it("creates a Python file and saves it explicitly to GitHub", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({
        settings: {
          has_github_connection: true,
          github_login: "octocat",
        },
      }),
    );
    apiMocks.createPythonFile.mockResolvedValue(
      pythonFileResponse("file_1", "something.py", "", "proj_1"),
    );
    apiMocks.getProjectRepoSync.mockResolvedValue(repoSyncResponse("proj_1", false));
    apiMocks.syncProjectRepo.mockResolvedValue(repoSyncResponse("proj_1", true));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Add python file" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    });
    expect(screen.queryByText("Python file")).toBeNull();

    fireEvent.change(screen.getByTestId("monaco-editor"), {
      target: { value: "print('hello')" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.getByText("Save Python file")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "Save to GitHub" }));

    await waitFor(() => {
      expect(apiMocks.updatePythonFile).toHaveBeenCalledWith("file_1", {
        content: "print('hello')",
      });
    });
    expect(apiMocks.syncProjectRepo).toHaveBeenCalledWith("proj_1", {
      commitMessage: "Save something.py",
      expectedRemoteHeadSha: null,
      conflictResolutions: [],
    });
    await waitFor(() => {
      expect(screen.getByText(/Saved to/i)).toBeTruthy();
    });
  });

  it("opens a Monaco merge flow when GitHub save hits a conflict", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({
        settings: {
          has_github_connection: true,
          github_login: "octocat",
        },
      }),
    );
    apiMocks.createPythonFile.mockResolvedValue(
      pythonFileResponse("file_1", "something.py", "", "proj_1"),
    );
    apiMocks.getProjectRepoSync.mockResolvedValue(repoSyncResponse("proj_1", true));
    apiMocks.syncProjectRepo
      .mockRejectedValueOnce(repoConflictError("proj_1"))
      .mockResolvedValueOnce(repoSyncResponse("proj_1", true));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Add python file" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("monaco-editor"), {
      target: { value: "print('local')" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.getByText("Save Python file")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "Save to GitHub" }));

    await waitFor(() => {
      expect(screen.getByText("Resolve GitHub conflicts")).toBeTruthy();
    });
    expect(screen.getByTestId("monaco-diff-editor")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Use remote" }));
    fireEvent.change(screen.getAllByTestId("monaco-editor")[1], {
      target: { value: "print('merged')" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save merge" }));

    await waitFor(() => {
      expect(apiMocks.syncProjectRepo).toHaveBeenLastCalledWith("proj_1", {
        commitMessage: "Save something.py",
        expectedRemoteHeadSha: "def5678",
        conflictResolutions: [
          {
            path: "src/something.py",
            content: "print('merged')",
          },
        ],
      });
    });
  });

  it("runs a Python file after saving the current draft to GitHub", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({
        settings: {
          has_modal_credentials: true,
          has_github_connection: true,
          github_login: "octocat",
        },
      }),
    );
    apiMocks.createPythonFile.mockResolvedValue(
      pythonFileResponse("file_1", "something.py", "", "proj_1"),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Add python file" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Run" })).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("monaco-editor"), {
      target: { value: "print('run')" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => {
      expect(apiMocks.updatePythonFile).toHaveBeenCalledWith("file_1", {
        content: "print('run')",
      });
    });
    expect(apiMocks.syncProjectRepo).toHaveBeenCalledWith("proj_1", {
      commitMessage: "Run something.py",
      expectedRemoteHeadSha: null,
      conflictResolutions: [],
    });
    expect(apiMocks.runPythonFile).toHaveBeenCalledWith("file_1", "full");
    await waitFor(() => {
      expect(screen.getByText("Latest run")).toBeTruthy();
    });
  });

  it("persists dirty project drafts before project-code chat turns", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems =
          itemKind === "python_file"
            ? [projectItemFile("file_1", "agent.py", projectId, "src/agent.py")]
            : [projectItemThread("thread_1", "Recovered thread", projectId)];
        const pageItems = allItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: allItems.length,
          has_more: false,
        };
      },
    );
    apiMocks.getPythonFile.mockResolvedValue(
      pythonFileResponse("file_1", "agent.py", "print('base')\n", "proj_1", "src/agent.py"),
    );
    apiMocks.updatePythonFile.mockResolvedValue(
      pythonFileResponse("file_1", "agent.py", "print('dirty')\n", "proj_1", "src/agent.py"),
    );
    apiMocks.sendThreadMessageStream.mockRejectedValue(
      new apiMocks.StreamingChatError("stream failed", true),
    );
    apiMocks.sendThreadMessage.mockResolvedValue({
      thread: threadResponse("thread_1", "Recovered thread", "Code answer"),
      assistant_message: {
        message_id: "msg_2",
        role: "assistant",
        content_markdown: "Code answer",
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
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByText("agent.py"));

    await waitFor(() => {
      expect(screen.getByTestId("monaco-editor")).toBeTruthy();
    });
    fireEvent.change(screen.getByTestId("monaco-editor"), {
      target: { value: "print('dirty')\n" },
    });

    fireEvent.click(screen.getAllByText("Recovered thread")[0]!);

    await waitFor(() => {
      expect(screen.getByLabelText("mention-input-chat")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "edit src/agent.py to add a torch smoke run" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(apiMocks.updatePythonFile).toHaveBeenCalledWith("file_1", {
        content: "print('dirty')\n",
      });
    });
    await waitFor(() => {
      expect(apiMocks.sendThreadMessage).toHaveBeenCalledTimes(1);
    });
  });

  it("does not persist dirty project drafts for non-coding chat turns", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems =
          itemKind === "python_file"
            ? [projectItemFile("file_1", "agent.py", projectId, "src/agent.py")]
            : [projectItemThread("thread_1", "Recovered thread", projectId)];
        const pageItems = allItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: allItems.length,
          has_more: false,
        };
      },
    );
    apiMocks.getPythonFile.mockResolvedValue(
      pythonFileResponse("file_1", "agent.py", "print('base')\n", "proj_1", "src/agent.py"),
    );
    apiMocks.sendThreadMessageStream.mockRejectedValue(
      new apiMocks.StreamingChatError("stream failed", true),
    );
    apiMocks.sendThreadMessage.mockResolvedValue({
      thread: threadResponse("thread_1", "Recovered thread", "Research answer"),
      assistant_message: {
        message_id: "msg_2",
        role: "assistant",
        content_markdown: "Research answer",
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
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByText("agent.py"));

    await waitFor(() => {
      expect(screen.getByTestId("monaco-editor")).toBeTruthy();
    });
    fireEvent.change(screen.getByTestId("monaco-editor"), {
      target: { value: "print('dirty')\n" },
    });

    fireEvent.click(screen.getAllByText("Recovered thread")[0]!);

    await waitFor(() => {
      expect(screen.getByLabelText("mention-input-chat")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "Summarize the benchmark papers" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(apiMocks.sendThreadMessage).toHaveBeenCalledTimes(1);
    });
    expect(apiMocks.updatePythonFile).not.toHaveBeenCalled();
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
      expect(apiMocks.loadProjectItems).toHaveBeenCalledWith("proj_1", 5, 0, "thread");
      expect(screen.getByText("Recovered thread")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Delete Recovered thread"));
    fireEvent.click(screen.getByRole("button", { name: "Delete session" }));

    await waitFor(() => {
      expect(apiMocks.deleteThread).toHaveBeenCalledWith("thread_1");
    });
  });

  it("shows rename and delete actions for code files and removes deleted files", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems = [
          projectItemThread("thread_1", "Recovered thread", projectId),
          projectItemFile("file_1", "agent.py", projectId),
        ];
        const filteredItems = itemKind
          ? allItems.filter((item) => item.item_kind === itemKind)
          : allItems;
        const pageItems = filteredItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: filteredItems.length,
          has_more: offset + pageItems.length < filteredItems.length,
        };
      },
    );
    apiMocks.deletePythonFile.mockResolvedValue({
      file_id: "file_1",
      project_id: "proj_1",
      filename: "agent.py",
      deleted: true,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(screen.getByText("agent.py")).toBeTruthy();
    });

    expect(screen.getByLabelText("Rename agent.py")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Delete agent.py"));
    fireEvent.click(screen.getByRole("button", { name: "Delete file" }));

    await waitFor(() => {
      expect(apiMocks.deletePythonFile).toHaveBeenCalledWith("file_1");
    });
    await waitFor(() => {
      expect(screen.queryByText("agent.py")).toBeNull();
    });
  });

  it("can delete the General project and falls back to a create-project-only state", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjects
      .mockResolvedValueOnce({
        projects: [projectSummary("proj_1", "General")],
      })
      .mockResolvedValueOnce({
        projects: [],
      });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Open project menu for General"));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete project" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete project" }));

    await waitFor(() => {
      expect(apiMocks.deleteProject).toHaveBeenCalledWith("proj_1");
    });
    await waitFor(() => {
      expect(screen.getByText("Create a project to get started")).toBeTruthy();
    });
    expect(screen.queryByLabelText("mention-input-chat")).toBeNull();
    expect(screen.queryByText("Add python file")).toBeNull();
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

  it("shows a re-enter-key message when the saved Anthropic key is invalid", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("api_key_required", {
          thread: {
            ...emptyThreadResponse("thread_2"),
            messages: [
              {
                message_id: "msg_user",
                role: "user",
                content_markdown: "Need help",
                citations: [],
                created_at: "2026-04-21T00:00:00Z",
                status: "completed",
              },
            ],
          },
          assistant_message: null,
          remaining_free_responses: 0,
          has_anthropic_api_key: false,
          state: "api_key_invalid",
        });
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "Need help" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(
        screen.getByText(
          "Your saved Anthropic API key could not be decrypted. Add it again in Settings.",
        ),
      ).toBeTruthy();
    });
  });

  it("shows tool activity rows, uses a thinking label, and aborts on stop", async () => {
    const randomSpy = vi.spyOn(Math, "random").mockReturnValue(0);
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));

    let streamSignal: AbortSignal | undefined;
    let releaseStream: (() => void) | null = null;
    const streamDone = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });

    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
        options?: { signal?: AbortSignal },
      ) => {
        streamSignal = options?.signal;
        onEvent("usage", {
          model: "claude-opus-4-7",
          input_tokens: 117900,
          output_tokens: 100,
          cache_creation_input_tokens: 0,
          cache_read_input_tokens: 0,
          total_tokens: 118000,
          context_window_tokens: 258000,
        });
        onEvent("tool_start", {
          tool_call_id: "tool_1",
          tool_name: "paperbase_search",
          label: 'paper search for "grounding"',
          command: 'paperbase_search({\n  "query": "grounding"\n})',
        });
        onEvent("tool_done", {
          tool_call_id: "tool_1",
          tool_name: "paperbase_search",
          label: 'paper search for "grounding"',
          command: 'paperbase_search({\n  "query": "grounding"\n})',
          output: '{\n  "result_set_id": "rs_1"\n}',
          is_error: false,
        });
        await streamDone;
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "Find the grounding methods" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    });
    await waitFor(() => {
      expect(document.querySelectorAll(".chat-tab-thread-indicator")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.getByText('Did paper search for "grounding"')).toBeTruthy();
    });
    const toolActivitySummary = screen
      .getByText('Did paper search for "grounding"')
      .closest("summary");
    expect(toolActivitySummary?.textContent).toContain("Command and output");
    fireEvent.click(toolActivitySummary!);
    expect(screen.getByText("Command")).toBeTruthy();
    expect(screen.getByText("Output")).toBeTruthy();
    expect(screen.getByText(/paperbase_search\(\{/)).toBeTruthy();
    expect(screen.getByText(/"result_set_id": "rs_1"/)).toBeTruthy();
    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: "Context window: 46% used (54% left), 118,000 of 258,000 tokens used",
        }),
      ).toBeTruthy();
    });
    expect(screen.queryByText("Streaming")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Context window: 46% used (54% left), 118,000 of 258,000 tokens used",
      }),
    );
    expect(screen.getByText("Context window:")).toBeTruthy();
    expect(screen.getByText("46% used (54% left)")).toBeTruthy();
    expect(screen.getByText("118k / 258k tokens used")).toBeTruthy();
    expect(screen.getByText("Paperbase compacts context near the limit")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    });

    await waitFor(() => {
      expect(streamSignal?.aborted).toBe(true);
    });

    await act(async () => {
      releaseStream?.();
      await streamDone;
    });

    await waitFor(() => {
      expect(document.querySelectorAll(".chat-tab-thread-indicator")).toHaveLength(0);
    });
    randomSpy.mockRestore();
  });

  it("shows project coding activity copy and a live Python preview while code streams", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.getThread.mockResolvedValue(
      threadResponse("thread_2", "New chat", "```python\ndef train():\n    return 1\n```"),
    );

    let releaseStream: (() => void) | null = null;
    const streamDone = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });

    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("tool_start", {
          tool_call_id: "tool_1",
          tool_name: "project_apply_patch",
          label: 'project file patch for "src/train.py"',
        });
        onEvent("assistant_start", {
          assistant_message: {
            message_id: "msg_2",
            role: "assistant",
            content_markdown: "",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "streaming",
          },
        });
        onEvent("delta", {
          message_id: "msg_2",
          delta: "```python\ndef train():\n    return 1\n",
        });
        await streamDone;
        onEvent("tool_done", {
          tool_call_id: "tool_1",
          tool_name: "project_apply_patch",
          label: 'project file patch for "src/train.py"',
          is_error: false,
        });
        onEvent("done", {
          thread: threadResponse(
            "thread_2",
            "New chat",
            "```python\ndef train():\n    return 1\n```",
          ),
          assistant_message: {
            message_id: "msg_2",
            role: "assistant",
            content_markdown: "```python\ndef train():\n    return 1\n```",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          remaining_free_responses: 2,
          has_anthropic_api_key: false,
          state: "completed",
        });
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "write a training script" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByText("Patching src/train.py...")).toBeTruthy();
    });
    const preview = await screen.findByTestId("streaming-code-preview");
    expect(within(preview).getByText("Writing code")).toBeTruthy();
    expect(preview.textContent).toContain("python");
    expect(preview.textContent).toContain("1");
    expect(preview.textContent).toContain("2");
    expect(preview.textContent).toContain("def train():");
    expect(document.querySelector(".code-snippet-token.keyword")?.textContent).toBe("def");

    await act(async () => {
      releaseStream?.();
    });

    await waitFor(() => {
      expect(screen.queryByTestId("streaming-code-preview")).toBeNull();
    });
  });

  it("hides the live code preview once the fenced block closes during the stream", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));

    let allowFenceClose: (() => void) | null = null;
    const waitForFenceClose = new Promise<void>((resolve) => {
      allowFenceClose = resolve;
    });
    let releaseStream: (() => void) | null = null;
    const streamDone = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });

    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("assistant_start", {
          assistant_message: {
            message_id: "msg_2",
            role: "assistant",
            content_markdown: "",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "streaming",
          },
        });
        onEvent("delta", {
          message_id: "msg_2",
          delta: "```python\ndef train():\n    return 1\n",
        });
        await waitForFenceClose;
        onEvent("delta", {
          message_id: "msg_2",
          delta: "```\n\nThis script is ready to run.",
        });
        await streamDone;
        onEvent("done", {
          assistant_message: {
            message_id: "msg_2",
            role: "assistant",
            content_markdown:
              "```python\ndef train():\n    return 1\n```\n\nThis script is ready to run.",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          remaining_free_responses: 2,
          has_anthropic_api_key: false,
          state: "completed",
        });
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "write a training script with a short explanation" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const preview = await screen.findByTestId("streaming-code-preview");
    expect(preview.textContent).toContain("def train():");

    await act(async () => {
      allowFenceClose?.();
    });

    await waitFor(() => {
      expect(screen.queryByTestId("streaming-code-preview")).toBeNull();
    });

    await act(async () => {
      releaseStream?.();
    });
  });

  it("refreshes project code items after coding-tool turns complete", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_2", "New chat", "Created trainer"));

    let pythonItems: Array<ReturnType<typeof projectItemFile>> = [];
    apiMocks.loadProjectItems.mockImplementation(
      async (projectId: string, limit = 25, offset = 0, itemKind?: "thread" | "python_file") => {
        const allItems = [
          projectItemThread("thread_1", "Recovered thread", projectId),
          ...pythonItems,
        ];
        const filteredItems = itemKind
          ? allItems.filter((item) => item.item_kind === itemKind)
          : allItems;
        const pageItems = filteredItems.slice(offset, offset + limit);
        return {
          ...projectItemsPage(projectId, pageItems),
          offset,
          limit,
          total_count: filteredItems.length,
          has_more: offset + pageItems.length < filteredItems.length,
        };
      },
    );
    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("tool_start", {
          tool_call_id: "tool_1",
          tool_name: "project_write_file",
          label: 'project file write for "src/trainer.py"',
        });
        pythonItems = [projectItemFile("file_2", "trainer.py", "proj_1", "src/trainer.py")];
        onEvent("done", {
          assistant_message: {
            message_id: "msg_2",
            role: "assistant",
            content_markdown: "Created trainer.py",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          remaining_free_responses: 2,
          has_anthropic_api_key: false,
          state: "completed",
        });
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(screen.getByText("No code files yet.")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "create a trainer module" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByText("trainer.py")).toBeTruthy();
    });
  });

  it("keeps the context usage control in the composer after streaming completes", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("assistant_start", {
          assistant_message: {
            message_id: "msg_stream",
            role: "assistant",
            content_markdown: "",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "streaming",
          },
        });
        onEvent("usage", {
          model: "claude-opus-4-7",
          input_tokens: 117900,
          output_tokens: 100,
          cache_creation_input_tokens: 0,
          cache_read_input_tokens: 0,
          total_tokens: 118000,
          context_window_tokens: 258000,
        });
        onEvent("done", {
          assistant_message: {
            message_id: "msg_stream",
            role: "assistant",
            content_markdown: "Grounded streamed answer",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          remaining_free_responses: 2,
          has_anthropic_api_key: false,
          state: "completed",
          usage: {
            model: "claude-opus-4-7",
            input_tokens: 117900,
            output_tokens: 100,
            cache_creation_input_tokens: 0,
            cache_read_input_tokens: 0,
            total_tokens: 118000,
            context_window_tokens: 258000,
          },
        });
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "Summarize retrieval strategies" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByText("Grounded streamed answer")).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    });

    const usageButton = screen.getByRole("button", {
      name: "Context window: 46% used (54% left), 118,000 of 258,000 tokens used",
    });
    expect(usageButton).toBeTruthy();

    fireEvent.click(usageButton);

    expect(screen.getByText("Context window:")).toBeTruthy();
    expect(screen.getByText("46% used (54% left)")).toBeTruthy();
    expect(screen.getByText("118k / 258k tokens used")).toBeTruthy();
    expect(screen.getByText("Paperbase compacts context near the limit")).toBeTruthy();
  });

  it("renders compaction checkpoints during a stream and keeps the summary", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_compact"));
    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("compaction_start", {
          compaction_message: {
            message_id: "msg_compact",
            role: "user",
            kind: "compaction",
            content_markdown: "Compacting conversation...",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "streaming",
          },
        });
        onEvent("compaction_done", {
          compaction_message: {
            message_id: "msg_compact",
            role: "user",
            kind: "compaction",
            content_markdown: "## Compacted Conversation\n\nPreserved the current task.",
            citations: [],
            created_at: "2026-04-21T00:00:00Z",
            status: "completed",
          },
          messages: [
            {
              message_id: "msg_compact",
              role: "user",
              kind: "compaction",
              content_markdown: "## Compacted Conversation\n\nPreserved the current task.",
              citations: [],
              created_at: "2026-04-21T00:00:00Z",
              status: "completed",
            },
          ],
        });
        onEvent("tool_start", {
          tool_call_id: "tool_after_compact",
          tool_name: "paperbase_search",
          label: 'paper search for "context"',
        });
        onEvent("tool_done", {
          tool_call_id: "tool_after_compact",
          tool_name: "paperbase_search",
          label: 'paper search for "context"',
          is_error: false,
        });
        onEvent("assistant_start", {
          assistant_message: {
            message_id: "msg_answer",
            role: "assistant",
            content_markdown: "",
            citations: [],
            created_at: "2026-04-21T00:00:01Z",
            status: "streaming",
          },
        });
        onEvent("done", {
          assistant_message: {
            message_id: "msg_answer",
            role: "assistant",
            content_markdown: "Answer after compaction",
            citations: [],
            created_at: "2026-04-21T00:00:01Z",
            status: "completed",
          },
          remaining_free_responses: 2,
          has_anthropic_api_key: false,
          state: "completed",
          usage: null,
        });
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "Find context compaction papers" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByText(/Compacted Conversation/)).toBeTruthy();
    });
    expect(screen.queryByText("Compacting conversation...")).toBeNull();
    expect(screen.getByText('Did paper search for "context"')).toBeTruthy();
    expect(screen.getByText("Answer after compaction")).toBeTruthy();
  });

  it("opens the settings modal with appearance controls and no notifications tab", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

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
    expect(screen.getByRole("tab", { name: "Appearance" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Notifications" })).toBeNull();
    expect(document.querySelector("#auth-modal-title")).toBeNull();
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Inference");
    expect(screen.getAllByText("Manage Anthropic inference API key.")).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "Sandbox" }));
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Sandbox");
    expect(screen.getAllByText("Manage Modal sandbox credentials.")).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "Appearance" }));
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Appearance");
    expect(screen.getAllByText("Choose the theme for signed-in pages.")).toHaveLength(1);
    expect(screen.getByRole("radio", { name: /Light/i })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Dark/i })).toBeTruthy();
  });

  it("applies dark mode only after saving the appearance setting", async () => {
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({
        settings: {
          appearance_theme: "light",
        },
      }),
    );
    apiMocks.api.mockResolvedValueOnce(
      authenticatedSession({
        settings: {
          appearance_theme: "dark",
        },
      }),
    );

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#account-menu-button")).toBeTruthy();
    });

    expect(document.body.classList.contains("authenticated-theme-dark")).toBe(false);

    fireEvent.click(document.querySelector("#account-menu-button") as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Appearance" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("tab", { name: "Appearance" }));
    fireEvent.click(screen.getByRole("radio", { name: /Dark/i }));
    fireEvent.click(screen.getByRole("button", { name: "Save appearance" }));

    await waitFor(() => {
      expect(apiMocks.api).toHaveBeenCalledWith("/v1/browser/settings/appearance", {
        method: "PUT",
        body: JSON.stringify({ theme: "dark" }),
      });
    });
    await waitFor(() => {
      expect(screen.getByText("Dark mode enabled.")).toBeTruthy();
    });
    expect(document.querySelector(".settings-tab-panels > #modal-flash")).toBeTruthy();
    expect(document.querySelector(".modal-panel > #modal-flash")).toBeNull();
    await waitFor(() => {
      expect(document.body.classList.contains("authenticated-theme-dark")).toBe(true);
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
        has_modal_credentials: false,
        modal_token_id_hint: "",
        has_github_connection: false,
        github_login: "",
        github_oauth_available: false,
        weekly_papers_review_email: false,
        appearance_theme: "light",
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
      expect(document.querySelector("#settings-modal-form")).toBeTruthy();
    });

    expect(
      document
        .querySelector("#settings-modal-form input[name='token_id']")
        ?.getAttribute("autocomplete"),
    ).toBe("new-password");
    expect(
      document
        .querySelector("#settings-modal-form input[name='token_secret']")
        ?.getAttribute("autocomplete"),
    ).toBe("new-password");
  });
});
