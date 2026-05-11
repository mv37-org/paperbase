import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ForwardedRef } from "react";
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
    apiResourceUrl: vi.fn((path: string) => path),
    browserAdminPaperAssetContentUrl: vi.fn(
      (paperId: string, path: string) =>
        `/v1/browser/admin/papers/${encodeURIComponent(paperId)}/asset-content/${path}`,
    ),
    browserPaperPreviewContentUrl: vi.fn(
      (paperId: string) => `/v1/browser/papers/${encodeURIComponent(paperId)}/preview/content`,
    ),
    createPythonFile: vi.fn(),
    createProject: vi.fn(),
    createThread: vi.fn(),
    createBrowserPaperComment: vi.fn(),
    createBrowserTrendingPaperComment: vi.fn(),
    deleteProject: vi.fn(),
    deletePythonFile: vi.fn(),
    deleteThread: vi.fn(),
    getBrowserTrendingPaper: vi.fn(),
    getBrowserAdminPaperDetail: vi.fn(),
    getBrowserAdminPaperAsset: vi.fn(),
    getBrowserAdminPaperFile: vi.fn(),
    getBrowserPaper: vi.fn(),
    getBrowserPaperDiscussion: vi.fn(),
    getBrowserPaperPreview: vi.fn(),
    getBrowserPaperFigure: vi.fn(),
    getBrowserPaperFile: vi.fn(),
    getBrowserPaperTable: vi.fn(),
    getBootstrapSession: vi.fn(),
    getProjectRepoSync: vi.fn(),
    getPythonFile: vi.fn(),
    getThread: vi.fn(),
    githubLoginUrl: vi.fn(() => "/v1/browser/auth/github/start"),
    googleLoginUrl: vi.fn(() => "/v1/browser/auth/google/start"),
    importProjectNotebook: vi.fn(),
    loadGitHubRepositories: vi.fn(),
    loadGitHubRepoImportCandidates: vi.fn(),
    loadGitHubRepoMetadata: vi.fn(),
    loadBrowserAdminJobPapers: vi.fn(),
    loadBrowserAdminJobs: vi.fn(),
    loadBrowserAdminJobsOverview: vi.fn(),
    loadBrowserAdminPaperFiles: vi.fn(),
    loadBrowserAdminPapers: vi.fn(),
    loadBrowserTrendingPapers: vi.fn(),
    loadPublicProjectPresentation: vi.fn(),
    loadProjectItems: vi.fn(),
    loadProjectPresentation: vi.fn(),
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
    renameThread: vi.fn(),
    logoutBrowserSession: vi.fn(),
    postForm: vi.fn(),
    queueBrowserAdminEnrichMissingMetadata: vi.fn(),
    queueBrowserAdminHarvestRange: vi.fn(),
    queueBrowserAdminHarvestTopCited: vi.fn(),
    queueBrowserAdminHarvestVenue: vi.fn(),
    queueBrowserAdminPaperIndex: vi.fn(),
    retryBrowserAdminJob: vi.fn(),
    removeUpvoteBrowserPaper: vi.fn(),
    removeUpvoteBrowserTrendingPaper: vi.fn(),
    runPythonFile: vi.fn(),
    sendThreadMessage: vi.fn(),
    sendThreadMessageStream: vi.fn(),
    startProjectPresentation: vi.fn(),
    submitChatFeedback: vi.fn(),
    suggestBrowserPapers: vi.fn(),
    StreamingChatAbortError: MockStreamingChatAbortError,
    StreamingChatError: MockStreamingChatError,
    syncProjectRepo: vi.fn(),
    updateProjectPresentationShare: vi.fn(),
    upvoteBrowserPaper: vi.fn(),
    upvoteBrowserTrendingPaper: vi.fn(),
    updateProject: vi.fn(),
    updatePythonFile: vi.fn(),
    uploadDocument: vi.fn(),
  };
});

const pdfViewerMocks = vi.hoisted(() => ({
  clearSelection: vi.fn(),
  scrollToAnnotation: vi.fn(),
  scrollToSection: vi.fn(),
}));

vi.mock("./api", () => ({
  ApiError: apiMocks.ApiError,
  api: apiMocks.api,
  apiResourceUrl: apiMocks.apiResourceUrl,
  browserAdminPaperAssetContentUrl: apiMocks.browserAdminPaperAssetContentUrl,
  browserPaperPreviewContentUrl: apiMocks.browserPaperPreviewContentUrl,
  createBrowserPaperComment: apiMocks.createBrowserPaperComment,
  createBrowserTrendingPaperComment: apiMocks.createBrowserTrendingPaperComment,
  createPythonFile: apiMocks.createPythonFile,
  createProject: apiMocks.createProject,
  createThread: apiMocks.createThread,
  deleteProject: apiMocks.deleteProject,
  deletePythonFile: apiMocks.deletePythonFile,
  deleteThread: apiMocks.deleteThread,
  getBrowserAdminPaperAsset: apiMocks.getBrowserAdminPaperAsset,
  getBrowserAdminPaperDetail: apiMocks.getBrowserAdminPaperDetail,
  getBrowserAdminPaperFile: apiMocks.getBrowserAdminPaperFile,
  getBrowserPaper: apiMocks.getBrowserPaper,
  getBrowserPaperDiscussion: apiMocks.getBrowserPaperDiscussion,
  getBrowserPaperPreview: apiMocks.getBrowserPaperPreview,
  getBrowserPaperFigure: apiMocks.getBrowserPaperFigure,
  getBrowserPaperFile: apiMocks.getBrowserPaperFile,
  getBrowserPaperTable: apiMocks.getBrowserPaperTable,
  getBrowserTrendingPaper: apiMocks.getBrowserTrendingPaper,
  getBootstrapSession: apiMocks.getBootstrapSession,
  getProjectRepoSync: apiMocks.getProjectRepoSync,
  getPythonFile: apiMocks.getPythonFile,
  getThread: apiMocks.getThread,
  githubLoginUrl: apiMocks.githubLoginUrl,
  googleLoginUrl: apiMocks.googleLoginUrl,
  importProjectNotebook: apiMocks.importProjectNotebook,
  loadGitHubRepositories: apiMocks.loadGitHubRepositories,
  loadGitHubRepoImportCandidates: apiMocks.loadGitHubRepoImportCandidates,
  loadGitHubRepoMetadata: apiMocks.loadGitHubRepoMetadata,
  loadBrowserAdminJobPapers: apiMocks.loadBrowserAdminJobPapers,
  loadBrowserAdminJobs: apiMocks.loadBrowserAdminJobs,
  loadBrowserAdminJobsOverview: apiMocks.loadBrowserAdminJobsOverview,
  loadBrowserAdminPaperFiles: apiMocks.loadBrowserAdminPaperFiles,
  loadBrowserAdminPapers: apiMocks.loadBrowserAdminPapers,
  loadBrowserTrendingPapers: apiMocks.loadBrowserTrendingPapers,
  loadPublicProjectPresentation: apiMocks.loadPublicProjectPresentation,
  loadProjectItems: apiMocks.loadProjectItems,
  loadProjectPresentation: apiMocks.loadProjectPresentation,
  loadProjectSessions: apiMocks.loadProjectSessions,
  loadProjects: apiMocks.loadProjects,
  loadSession: apiMocks.loadSession,
  loadUploads: apiMocks.loadUploads,
  moveThreadToProject: apiMocks.moveThreadToProject,
  renameThread: apiMocks.renameThread,
  logoutBrowserSession: apiMocks.logoutBrowserSession,
  postForm: apiMocks.postForm,
  queueBrowserAdminEnrichMissingMetadata: apiMocks.queueBrowserAdminEnrichMissingMetadata,
  queueBrowserAdminHarvestRange: apiMocks.queueBrowserAdminHarvestRange,
  queueBrowserAdminHarvestTopCited: apiMocks.queueBrowserAdminHarvestTopCited,
  queueBrowserAdminHarvestVenue: apiMocks.queueBrowserAdminHarvestVenue,
  queueBrowserAdminPaperIndex: apiMocks.queueBrowserAdminPaperIndex,
  removeUpvoteBrowserPaper: apiMocks.removeUpvoteBrowserPaper,
  removeUpvoteBrowserTrendingPaper: apiMocks.removeUpvoteBrowserTrendingPaper,
  retryBrowserAdminJob: apiMocks.retryBrowserAdminJob,
  runPythonFile: apiMocks.runPythonFile,
  sendThreadMessage: apiMocks.sendThreadMessage,
  sendThreadMessageStream: apiMocks.sendThreadMessageStream,
  startProjectPresentation: apiMocks.startProjectPresentation,
  submitChatFeedback: apiMocks.submitChatFeedback,
  suggestBrowserPapers: apiMocks.suggestBrowserPapers,
  StreamingChatAbortError: apiMocks.StreamingChatAbortError,
  StreamingChatError: apiMocks.StreamingChatError,
  syncProjectRepo: apiMocks.syncProjectRepo,
  updateProjectPresentationShare: apiMocks.updateProjectPresentationShare,
  upvoteBrowserPaper: apiMocks.upvoteBrowserPaper,
  upvoteBrowserTrendingPaper: apiMocks.upvoteBrowserTrendingPaper,
  updateProject: apiMocks.updateProject,
  updatePythonFile: apiMocks.updatePythonFile,
  uploadDocument: apiMocks.uploadDocument,
}));

vi.mock("./LandingMentionEditor", () => ({
  LandingMentionEditor: apiMocks.landingMentionEditor,
}));

vi.mock("./MarkdownRenderer", () => ({
  MarkdownRenderer: ({
    markdown,
    interactiveCitations,
    onCitationClick,
  }: {
    markdown: string;
    interactiveCitations?: boolean;
    onCitationClick?: (sourceId: string) => void;
  }) => {
    if (!interactiveCitations) {
      return <div>{markdown}</div>;
    }
    return (
      <div>
        {markdown.split(/(\[S\d+\])/g).map((part, index) => {
          const match = part.match(/^\[(S\d+)\]$/);
          if (!match) {
            return <span key={`${part}:${index}`}>{part}</span>;
          }
          const sourceId = match[1] ?? "";
          return (
            <button
              key={`${part}:${index}`}
              type="button"
              onClick={() => onCitationClick?.(sourceId)}
            >
              {part}
            </button>
          );
        })}
      </div>
    );
  },
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

vi.mock("echarts-for-react", () => ({
  default: ({
    className,
    option,
    style,
  }: {
    className?: string;
    option?: {
      dataZoom?: unknown;
      grid?: unknown;
      series?: unknown;
      xAxis?: unknown;
      yAxis?: unknown;
    };
    style?: { height?: string; width?: string };
  }) => {
    const grid = option?.grid && typeof option.grid === "object" ? option.grid : {};
    const xAxis = option?.xAxis && typeof option.xAxis === "object" ? option.xAxis : {};
    const yAxis = option?.yAxis && typeof option.yAxis === "object" ? option.yAxis : {};
    const series = Array.isArray(option?.series) ? option.series : [];
    const seriesObjects = series.filter(
      (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
    );
    const xAxisLabel =
      (xAxis as Record<string, unknown>).axisLabel &&
      typeof (xAxis as Record<string, unknown>).axisLabel === "object"
        ? ((xAxis as Record<string, unknown>).axisLabel as Record<string, unknown>)
        : {};
    const xAxisTick =
      (xAxis as Record<string, unknown>).axisTick &&
      typeof (xAxis as Record<string, unknown>).axisTick === "object"
        ? ((xAxis as Record<string, unknown>).axisTick as Record<string, unknown>)
        : {};
    return (
      <div
        data-testid="admin-jobs-chart-inner"
        className={className}
        data-grid-bottom={String((grid as Record<string, unknown>).bottom ?? "")}
        data-grid-left={String((grid as Record<string, unknown>).left ?? "")}
        data-has-data-zoom={option && "dataZoom" in option ? "true" : "false"}
        data-x-axis-data-count={String(
          Array.isArray((xAxis as Record<string, unknown>).data)
            ? ((xAxis as Record<string, unknown>).data as unknown[]).length
            : 0,
        )}
        data-x-axis-label-align-max={String(xAxisLabel.alignMaxLabel ?? "")}
        data-x-axis-label-align-min={String(xAxisLabel.alignMinLabel ?? "")}
        data-x-axis-label-font-size={String(xAxisLabel.fontSize ?? "")}
        data-x-axis-label-interval={String(xAxisLabel.interval ?? "")}
        data-x-axis-label-show={String(xAxisLabel.show ?? "")}
        data-x-axis-tick-show={String(xAxisTick.show ?? "")}
        data-x-axis-tick-interval={String(xAxisTick.interval ?? "")}
        data-series-colors={seriesObjects
          .map((seriesItem) => {
            const itemStyle = seriesItem.itemStyle;
            return itemStyle && typeof itemStyle === "object"
              ? String((itemStyle as Record<string, unknown>).color ?? "")
              : "";
          })
          .join(",")}
        data-series-names={seriesObjects
          .map((seriesItem) => String(seriesItem.name ?? ""))
          .join(",")}
        data-series-stacks={seriesObjects
          .map((seriesItem) => String(seriesItem.stack ?? ""))
          .join(",")}
        data-y-max={String((yAxis as Record<string, unknown>).max ?? "")}
        data-y-split-number={String((yAxis as Record<string, unknown>).splitNumber ?? "")}
        style={style}
      />
    );
  },
}));

vi.mock("lottie-web/build/player/lottie_light", () => ({
  default: {
    loadAnimation: vi.fn(() => ({
      destroy: vi.fn(),
      goToAndStop: vi.fn(),
      play: vi.fn(),
    })),
  },
}));

vi.mock("./app/PdfDocumentViewer", async () => {
  const React = await import("react");
  const PdfDocumentViewer = React.forwardRef(
    (
      props: {
        className?: string;
        onSectionsChange?: (
          sections: Array<{
            id: string;
            title: string;
            pageNumber: number;
            pageYOffsetRatio: number;
          }>,
        ) => void;
        title: string;
        url: string;
        paperId?: string;
        annotations?: Array<{ annotationId: string; note: string }>;
        annotationsHidden?: boolean;
        onSelectionAction?: (selection: {
          paper_id: string;
          page_number: number;
          action: "explain" | "challenge";
          selected_text?: string | null;
          nearby_text?: string | null;
          region?: {
            points: Array<{ x: number; y: number }>;
            bbox: { x: number; y: number; width: number; height: number };
            image_base64?: string | null;
            content_type?: "image/png" | "image/webp" | null;
          };
        }) => void;
        onAddNote?: (target: {
          paperId: string;
          pageNumber: number;
          selectedText: string;
          rects: Array<{ x: number; y: number; width: number; height: number }>;
        }) => void;
      },
      ref: ForwardedRef<{
        clearSelection: () => void;
        scrollToAnnotation: (annotationId: string) => void;
        scrollToSection: (sectionId: string) => void;
      }>,
    ) => {
      React.useImperativeHandle(ref, () => ({
        clearSelection: pdfViewerMocks.clearSelection,
        scrollToAnnotation: pdfViewerMocks.scrollToAnnotation,
        scrollToSection: pdfViewerMocks.scrollToSection,
      }));
      React.useEffect(() => {
        if (!props.className?.includes("paper-exploration-frame")) {
          return;
        }
        props.onSectionsChange?.([
          { id: "abstract-1", title: "Abstract", pageNumber: 1, pageYOffsetRatio: 0 },
          { id: "introduction-1", title: "Introduction", pageNumber: 1, pageYOffsetRatio: 0.2 },
        ]);
      }, [props.className, props.onSectionsChange]);
      return React.createElement(
        "div",
        {
          "aria-label": props.title,
          className: `pdf-document-viewer${props.className ? ` ${props.className}` : ""}`,
          "data-url": props.url,
        },
        React.createElement(
          "span",
          { "data-testid": "mock-pdf-annotation-count" },
          `Mock annotations ${props.annotations?.length ?? 0}`,
        ),
        props.annotationsHidden
          ? React.createElement("span", null, "Mock highlights hidden")
          : null,
        props.onAddNote && props.paperId
          ? React.createElement(
              "button",
              {
                type: "button",
                onClick: () =>
                  props.onAddNote?.({
                    paperId: props.paperId!,
                    pageNumber: 1,
                    selectedText: "Selected benchmark text",
                    rects: [{ x: 0.1, y: 0.2, width: 0.3, height: 0.04 }],
                  }),
              },
              "Mock PDF add note",
            )
          : null,
        props.onSelectionAction && props.paperId
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement(
                "button",
                {
                  type: "button",
                  onClick: () =>
                    props.onSelectionAction?.({
                      paper_id: props.paperId!,
                      page_number: 1,
                      action: "explain",
                      selected_text: "Selected benchmark text",
                    }),
                },
                "Mock PDF explain selection",
              ),
              React.createElement(
                "button",
                {
                  type: "button",
                  onClick: () =>
                    props.onSelectionAction?.({
                      paper_id: props.paperId!,
                      page_number: 1,
                      action: "challenge",
                      selected_text: null,
                      nearby_text: "Area page context",
                      region: {
                        points: [
                          { x: 0.1, y: 0.2 },
                          { x: 0.4, y: 0.5 },
                          { x: 0.2, y: 0.6 },
                        ],
                        bbox: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
                        image_base64: "ZmFrZS1hcmVh",
                        content_type: "image/png",
                      },
                    }),
                },
                "Mock PDF challenge area",
              ),
            )
          : null,
      );
    },
  );
  PdfDocumentViewer.displayName = "MockPdfDocumentViewer";
  return { PdfDocumentViewer };
});

import App from "./App";

const ACTIVE_TAB_KEY = "paperbase-active-tab";
const LEGACY_OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
const PAPER_EXPLORATIONS_KEY = "paperbase-paper-explorations";
const SELECTED_PROJECT_KEY = "paperbase-selected-project";

function authenticatedSession(
  overrides: Partial<{
    has_openai_api_key: boolean;
    has_anthropic_api_key: boolean;
    csrf_token: string | null;
    is_admin: boolean;
    settings: Partial<{
      openai_api_key_hint: string;
      anthropic_api_key_hint: string;
      has_elevenlabs_api_key: boolean;
      elevenlabs_api_key_hint: string;
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
      is_admin: overrides.is_admin ?? false,
    },
    has_openai_api_key: overrides.has_openai_api_key ?? overrides.has_anthropic_api_key ?? true,
    has_anthropic_api_key: overrides.has_anthropic_api_key ?? overrides.has_openai_api_key ?? true,
    settings: {
      openai_api_key_hint: "",
      anthropic_api_key_hint: "",
      has_elevenlabs_api_key: false,
      elevenlabs_api_key_hint: "",
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

function threadItemResponse(
  itemId: string,
  itemType: string,
  role: string,
  contentText = "",
  overrides: Partial<{
    thread_id: string;
    turn_id: string;
    phase: "commentary" | "final_answer" | null;
    call_id: string | null;
    name: string | null;
    content_json: unknown;
    citations: unknown[];
    created_at: string;
    status: string;
  }> = {},
) {
  return {
    item_id: itemId,
    thread_id: overrides.thread_id ?? "thread_1",
    turn_id: overrides.turn_id ?? "turn_1",
    item_type: itemType,
    role,
    phase: overrides.phase ?? null,
    call_id: overrides.call_id ?? null,
    name: overrides.name ?? null,
    content_text: contentText,
    content_json: overrides.content_json,
    citations: overrides.citations ?? [],
    created_at: overrides.created_at ?? "2026-04-21T00:00:00Z",
    status: overrides.status ?? "completed",
  };
}

function turnResponse(
  turnId: string,
  threadId: string,
  items: ReturnType<typeof threadItemResponse>[] = [],
  overrides: Partial<{
    status: string;
    provider_response_id: string;
    started_at: string;
    completed_at: string | null;
    title: string;
    usage: Record<string, unknown> | null;
    diff_summary: string;
    changed_paths: string[];
    exec_session_ids: string[];
  }> = {},
) {
  return {
    turn_id: turnId,
    thread_id: threadId,
    title: overrides.title ?? "",
    status: overrides.status ?? "completed",
    provider_response_id: overrides.provider_response_id ?? "resp_1",
    started_at: overrides.started_at ?? "2026-04-21T00:00:00Z",
    completed_at: overrides.completed_at ?? "2026-04-21T00:00:01Z",
    usage: overrides.usage ?? null,
    diff_summary: overrides.diff_summary ?? "",
    changed_paths: overrides.changed_paths ?? [],
    exec_session_ids: overrides.exec_session_ids ?? [],
    items,
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

function projectPresentationResponse(projectId = "proj_1", status = "completed") {
  return {
    presentation_id: "pres_1",
    project_id: projectId,
    status,
    visual_code: "ReactDOM.createRoot(document.getElementById('root')).render('ok');",
    summary_markdown: "# Project summary\n\n| Item | Count |\n| --- | ---: |\n| Notes | 1 |",
    slides: [
      {
        slide_id: "project-summary",
        title: "Project summary",
        description: "A concise slide about the project research narrative.",
        visual_type: "code",
        visual_code: "ReactDOM.createRoot(document.getElementById('root')).render('ok');",
        image_prompt: "",
        image_url: "",
      },
    ],
    audio_url: "",
    audio_content_type: "audio/mpeg",
    is_public: false,
    share_token: null,
    share_url: "",
    error: "",
    job_id: status === "pending" ? "job_1" : null,
    progress: {
      phase_key: status === "completed" ? "complete" : "queued",
      phase_label: status === "completed" ? "Presentation ready" : "Queued presentation workflow",
      detail:
        status === "completed"
          ? "The presentation is ready to view and share."
          : "Preparing the project history for the presentation agent.",
      percent: status === "completed" ? 100 : 4,
      estimated_seconds_remaining: status === "completed" ? 0 : 360,
      can_leave: true,
      email_notice: "We will email you once it is done. You can leave this page.",
      updated_at: "2026-04-21T00:00:00Z",
    },
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    completed_at: status === "completed" ? "2026-04-21T00:00:00Z" : null,
  };
}

function threadResponse(
  threadId: string,
  title: string,
  content = "Grounded answer",
  projectId = "proj_1",
  projectName = "General",
) {
  const assistantItem = threadItemResponse("msg_1", "assistant_message", "assistant", content, {
    thread_id: threadId,
    turn_id: "turn_1",
    phase: "final_answer",
  });
  return {
    thread_id: threadId,
    project_id: projectId,
    project_name: projectName,
    project_instructions: "",
    title,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    items: [assistantItem],
    turns: [turnResponse("turn_1", threadId, [assistantItem])],
    messages: [
      {
        message_id: "msg_1",
        role: "assistant",
        content_markdown: content,
        citations: [],
        created_at: "2026-04-21T00:00:00Z",
        status: "completed",
        phase: "final_answer",
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
    items: [],
    turns: [],
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
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    apiMocks.getBootstrapSession.mockReturnValue(null);
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
    apiMocks.loadProjectPresentation.mockResolvedValue({
      ...projectPresentationResponse("proj_1", "missing"),
      presentation_id: "",
      visual_code: "",
      summary_markdown: "",
    });
    apiMocks.startProjectPresentation.mockResolvedValue(projectPresentationResponse("proj_1"));
    apiMocks.updateProjectPresentationShare.mockResolvedValue({
      ...projectPresentationResponse("proj_1"),
      is_public: true,
      share_token: "share_1",
      share_url: "http://127.0.0.1:8080/?presentation=share_1",
    });
    apiMocks.loadPublicProjectPresentation.mockResolvedValue({
      ...projectPresentationResponse("proj_1"),
      is_public: true,
      share_token: "share_1",
      share_url: "http://127.0.0.1:8080/?presentation=share_1",
    });
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
      categories: "cs.AI",
      primary_category: "cs.AI",
      published: "2026-04-21",
      updated: "2026-04-21",
      pdf_url: "https://example.test/2401.12345.pdf",
      html_url: "https://example.test/2401.12345",
      citation_count: 7,
      influential_citation_count: 2,
      code_url: "https://github.com/example/drawer-paper",
      code_index_status: "indexed",
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
    apiMocks.getBrowserPaperPreview.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      image_url: "https://example.test/preview.webp",
      content_type: "image/webp",
    });
    apiMocks.getBrowserPaperDiscussion.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 4,
      viewer_has_upvoted: false,
      comments: [
        {
          comment_id: "pcmt_1",
          paper_id: "arxiv:2401.12345",
          parent_comment_id: null,
          author: { user_id: "user_2", display_name: "Existing Reader" },
          body: "Existing paper comment",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          replies: [
            {
              comment_id: "pcmt_1_reply",
              paper_id: "arxiv:2401.12345",
              parent_comment_id: "pcmt_1",
              author: { user_id: "user_3", display_name: "Reply Reader" },
              body: "Existing paper reply",
              created_at: "2026-04-21T00:02:00Z",
              updated_at: "2026-04-21T00:02:00Z",
              replies: [],
            },
          ],
        },
      ],
    });
    apiMocks.upvoteBrowserPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 5,
      viewer_has_upvoted: true,
    });
    apiMocks.removeUpvoteBrowserPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 4,
      viewer_has_upvoted: false,
    });
    apiMocks.createBrowserPaperComment.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      comments: [
        {
          comment_id: "pcmt_2",
          paper_id: "arxiv:2401.12345",
          parent_comment_id: null,
          author: { user_id: "user_1", display_name: "Ada Lovelace" },
          body: "New paper comment",
          created_at: "2026-04-21T00:01:00Z",
          updated_at: "2026-04-21T00:01:00Z",
          replies: [],
        },
      ],
    });
    apiMocks.loadBrowserTrendingPapers.mockResolvedValue({
      date: "2026-04-21",
      sort: "default",
      page: 1,
      page_size: 20,
      has_next: false,
      has_previous: false,
      papers: [
        {
          paper_id: "arxiv:2401.12345",
          arxiv_id: "2401.12345",
          title: "Trending Paper",
          authors: "Ada Lovelace",
          published: "2026-04-21",
          html_url: "https://example.test/2401.12345",
          pdf_url: "https://example.test/2401.12345.pdf",
          image_url: "https://example.test/trending-preview.webp",
          upvote_count: 12,
          viewer_has_upvoted: false,
        },
      ],
    });
    apiMocks.getBrowserTrendingPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      arxiv_id: "2401.12345",
      title: "Trending Paper",
      authors: "Ada Lovelace",
      published: "2026-04-21",
      primary_category: "cs.AI",
      categories: "cs.AI",
      html_url: "https://example.test/2401.12345",
      pdf_url: "https://example.test/2401.12345.pdf",
      image_url: "https://example.test/trending-preview.webp",
      upvote_count: 12,
      viewer_has_upvoted: false,
      comments: [],
    });
    apiMocks.upvoteBrowserTrendingPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 13,
      viewer_has_upvoted: true,
    });
    apiMocks.removeUpvoteBrowserTrendingPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 12,
      viewer_has_upvoted: false,
    });
    apiMocks.createBrowserTrendingPaperComment.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      comments: [],
    });
    apiMocks.loadBrowserAdminJobPapers.mockResolvedValue({
      root_job_id: "job_root_1",
      total_count: 0,
      offset: 0,
      limit: 100,
      has_more: false,
      papers: [],
    });
    apiMocks.loadBrowserAdminJobs.mockResolvedValue([]);
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [],
      worker: null,
    });
    apiMocks.loadBrowserAdminPapers.mockResolvedValue([]);
    apiMocks.getBrowserAdminPaperDetail.mockResolvedValue({
      paper: {
        paper_id: "arxiv:2401.12345",
        arxiv_id: "2401.12345",
        title: "Drawer Paper",
        authors: "Drawer Author",
        abstract: "Drawer abstract",
        summary_short: "Drawer summary",
        categories: "cs.AI",
        primary_category: "cs.AI",
        published: "2026-04-21",
        updated: "2026-04-21",
        pdf_url: "https://example.test/2401.12345.pdf",
        html_url: "https://example.test/2401.12345",
        citation_count: 7,
        influential_citation_count: 2,
        index_status: "indexed",
      },
      jobs: [],
      files: [],
      code_files: null,
    });
    apiMocks.loadBrowserAdminPaperFiles.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      files: [],
    });
    apiMocks.getBrowserAdminPaperFile.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      path: "text/full.md",
      content: "full text",
    });
    apiMocks.getBrowserAdminPaperAsset.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      path: "preview/first-page-top.webp",
      url: "https://example.test/preview.webp",
      content_type: "image/webp",
    });
    apiMocks.queueBrowserAdminPaperIndex.mockResolvedValue({
      job_id: "job_admin_1",
      job_type: "index_paper",
      status: "pending",
      payload: {},
      attempts: 0,
      max_attempts: 3,
      priority: -10,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.queueBrowserAdminHarvestRange.mockResolvedValue({
      job_id: "job_admin_range",
      job_type: "harvest_range",
      status: "pending",
      payload: {},
      attempts: 0,
      max_attempts: 3,
      priority: 10,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.queueBrowserAdminHarvestTopCited.mockResolvedValue({
      job_id: "job_admin_top",
      job_type: "harvest_top_cited",
      status: "pending",
      payload: {},
      attempts: 0,
      max_attempts: 3,
      priority: 10,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.queueBrowserAdminHarvestVenue.mockResolvedValue({
      job_id: "job_admin_venue",
      job_type: "harvest_venue",
      status: "pending",
      payload: {},
      attempts: 0,
      max_attempts: 3,
      priority: 10,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.queueBrowserAdminEnrichMissingMetadata.mockResolvedValue({
      job_id: "job_admin_enrich",
      job_type: "enrich_missing_metadata",
      status: "pending",
      payload: {},
      attempts: 0,
      max_attempts: 3,
      priority: 10,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.retryBrowserAdminJob.mockResolvedValue({
      job_id: "job_admin_retry",
      job_type: "index_paper",
      status: "pending",
      payload: { retried: true },
      attempts: 0,
      max_attempts: 3,
      priority: -10,
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.syncProjectRepo.mockResolvedValue(repoSyncResponse("proj_1", true));
    apiMocks.renameThread.mockImplementation(async (threadId: string, title: string) => ({
      ...threadResponse(threadId, title),
      title,
    }));
    apiMocks.submitChatFeedback.mockResolvedValue({
      feedback_id: "feedback_1",
      thread_id: "thread_1",
      message_id: "msg_1",
      sentiment: "positive",
      reasons: ["Accurate"],
      note: "",
      debug_bundle: {},
      created_at: "2026-04-21T00:00:00Z",
    });
    apiMocks.updatePythonFile.mockImplementation(
      async (fileId: string, payload: { filename?: string | null; content?: string | null }) =>
        pythonFileResponse(fileId, payload.filename ?? "something.py", payload.content ?? ""),
    );
  });

  afterEach(() => {
    document.documentElement.classList.remove("authenticated-theme-dark");
    document.body.classList.remove("authenticated-theme-dark");
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("shows the landing page when the browser session is not authenticated", async () => {
    apiMocks.loadSession.mockResolvedValue({
      authenticated: false,
      user: null,
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

  it("shows top landing papers without landing vote controls", async () => {
    apiMocks.loadSession.mockResolvedValue({
      authenticated: false,
      user: null,
      has_anthropic_api_key: false,
      csrf_token: null,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Trending papers" })).toBeTruthy();
    });
    await waitFor(() => {
      expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenCalledWith(
        expect.any(String),
        "default",
        1,
        10,
      );
    });
    expect(screen.queryByRole("button", { name: "Browse trending papers" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Hottest" }));

    await waitFor(() => {
      expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenLastCalledWith(
        expect.any(String),
        "upvotes",
        1,
        10,
      );
    });
    expect(screen.getByRole("heading", { name: "Trending papers" })).toBeTruthy();

    expect(screen.queryByRole("button", { name: "Upvote 12" })).toBeNull();
    expect(apiMocks.upvoteBrowserTrendingPaper).not.toHaveBeenCalled();
  });

  it("opens the public trending page from a shared browse URL", async () => {
    window.history.replaceState({}, "", "/?browse=trending");
    apiMocks.loadSession.mockResolvedValue({
      authenticated: false,
      user: null,
      has_anthropic_api_key: false,
      csrf_token: null,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("trending-papers-page")).toBeTruthy();
    });
    await waitFor(() => {
      expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenCalledWith(
        expect.any(String),
        "default",
        1,
        20,
      );
    });
  });

  it("boots from the server session payload without refetching the browser session", async () => {
    apiMocks.getBootstrapSession.mockReturnValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#account-menu-button")).toBeTruthy();
    });

    expect(apiMocks.loadSession).not.toHaveBeenCalled();
  });

  it("boots from the server session payload without refetching the browser session", async () => {
    apiMocks.getBootstrapSession.mockReturnValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#account-menu-button")).toBeTruthy();
    });

    expect(apiMocks.loadSession).not.toHaveBeenCalled();
  });

  it("restores the active resource for an authenticated session", async () => {
    localStorage.setItem(LEGACY_OPEN_TABS_KEY, JSON.stringify(["thread_1"]));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Restored research thread"));

    render(<App />);

    await waitFor(() => {
      expect(apiMocks.getThread).toHaveBeenCalledWith("thread_1");
    });
    expect(apiMocks.loadProjects).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("tablist", { name: "Open tabs" })).toBeNull();
    expect(document.querySelector(".chat-stage-header")).toBeNull();
    await waitFor(() => {
      expect(localStorage.getItem(LEGACY_OPEN_TABS_KEY)).toBeNull();
    });
    expect(localStorage.getItem(ACTIVE_TAB_KEY)).toBe(
      JSON.stringify({ kind: "thread", id: "thread_1" }),
    );
    expect(screen.queryByTestId("thread-debug-strip")).toBeNull();
    const debugButton = screen.getByRole("button", { name: "Open chat debug info" });
    expect(debugButton).toBeTruthy();

    fireEvent.click(debugButton);

    const debugModal = screen.getByRole("dialog", { name: "Chat debug" });
    expect(within(debugModal).getByText("user_1")).toBeTruthy();
    expect(within(debugModal).getByText("proj_1")).toBeTruthy();
    expect(within(debugModal).getByText("thread_1")).toBeTruthy();
    expect(within(debugModal).getAllByText("msg_1").length).toBeGreaterThan(0);
    expect(within(debugModal).getByRole("button", { name: "Copy debug bundle" })).toBeTruthy();
  });

  it("restores the previously selected project and keeps only that tree expanded", async () => {
    localStorage.setItem(SELECTED_PROJECT_KEY, "proj_2");
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
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
    expect(categoryLabels).toEqual(["Explorations", "Conversations", "Code"]);
    expect(document.querySelectorAll(".project-tree-category-count")).toHaveLength(0);
    expect(screen.getByText("Home")).toBeTruthy();
    expect(screen.getByText("Explorations")).toBeTruthy();
    expect(screen.getByText("Conversations")).toBeTruthy();
    expect(screen.getByText("Code")).toBeTruthy();
    expect(screen.getByLabelText("Collapse all sections in General")).toBeTruthy();

    fireEvent.click(screen.getByText("Conversations"));
    expect(screen.queryByText("Recovered thread")).toBeNull();
    fireEvent.click(screen.getByText("Conversations"));
    expect(await screen.findByText("Recovered thread")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Collapse all sections in General"));
    expect(screen.queryByText("Recovered thread")).toBeNull();
    expect(screen.queryByText("Explorations")).toBeNull();
    expect(screen.queryByText("Home")).toBeNull();
    expect(screen.getByLabelText("Expand all sections in General")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Expand all sections in General"));
    expect(await screen.findByText("Explorations")).toBeTruthy();
    expect(await screen.findByText("Recovered thread")).toBeTruthy();

    fireEvent.click(screen.getByText("Home"));
    expect(await screen.findByRole("button", { name: "Create presentation" })).toBeTruthy();
  });

  it("starts a project presentation from the project home tab", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.startProjectPresentation.mockResolvedValue({
      ...projectPresentationResponse("proj_1"),
      audio_url: "/v1/browser/projects/proj_1/presentation/audio",
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByText("Home"));
    fireEvent.click(await screen.findByRole("button", { name: "Create presentation" }));

    await waitFor(() => {
      expect(apiMocks.startProjectPresentation).toHaveBeenCalledWith("proj_1", {
        explorations: [],
        regenerate: false,
      });
    });
    expect(await screen.findByRole("button", { name: "Regenerate presentation" })).toBeTruthy();
    expect(screen.queryByText("Project Presentation")).toBeNull();
    expect(await screen.findByText("Project summary")).toBeTruthy();
    const audio = await screen.findByLabelText("Presentation audio walkthrough");
    expect(audio.getAttribute("src")).toContain("/v1/browser/projects/proj_1/presentation/audio");
  });

  it("shows live project presentation progress with an ETA", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectPresentation.mockResolvedValue({
      ...projectPresentationResponse("proj_1", "running"),
      progress: {
        phase_key: "making_bullet_points",
        phase_label: "Making bullet points",
        detail: "Reading papers, notes, annotations, chats, and code.",
        percent: 12,
        estimated_seconds_remaining: 125,
        can_leave: true,
        email_notice: "We will email you once it is done. You can leave this page.",
        updated_at: new Date(Date.now()).toISOString(),
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByText("Home"));

    expect(await screen.findByText("Making bullet points")).toBeTruthy();
    expect(screen.getByText("Reading papers, notes, annotations, chats, and code.")).toBeTruthy();
    expect(screen.getByText("12%")).toBeTruthy();
    expect(screen.getByText(/remaining/)).toBeTruthy();
    expect(
      screen.getByText("We will email you once it is done. You can leave this page."),
    ).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("12");
  });

  it("lets the project home recover from a failed presentation", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.loadProjectPresentation.mockResolvedValue({
      ...projectPresentationResponse("proj_1", "failed"),
      error: "('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))",
      slides: [],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByText("Home"));

    expect(await screen.findByRole("button", { name: "Create presentation" })).toBeTruthy();
    expect(screen.queryByText(/Connection reset by peer/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Create presentation" }));
    await waitFor(() => {
      expect(apiMocks.startProjectPresentation).toHaveBeenCalledWith("proj_1", {
        explorations: [],
        regenerate: false,
      });
    });
  });

  it("starts a paper exploration from the project exploration picker", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.suggestBrowserPapers.mockResolvedValue({
      fallback: false,
      suggestions: [
        {
          paper_id: "arxiv:2402.22222",
          arxiv_id: "2402.22222",
          title: "Benchmark Paper",
          authors: "Ada Lovelace",
          summary_short: "Benchmark summary",
          citation_count: 9,
          published: "2026-02-02",
          score: 0.92,
          matched_field: "title",
          highlight_snippet: "",
        },
      ],
    });
    apiMocks.getBrowserPaper.mockResolvedValue({
      paper_id: "arxiv:2402.22222",
      arxiv_id: "2402.22222",
      title: "Benchmark Paper",
      authors: "Ada Lovelace",
      abstract: "Benchmark abstract",
      summary_short: "Benchmark summary",
      categories: "cs.AI",
      primary_category: "cs.AI",
      published: "2026-02-02",
      updated: "2026-02-02",
      pdf_url: "",
      html_url: "https://example.test/2402.22222",
      citation_count: 9,
      influential_citation_count: 1,
      index_status: "indexed",
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByLabelText("Start exploration in General"));

    const dialog = screen.getByRole("dialog", { name: "New exploration" });
    expect(within(dialog).queryByText("New exploration")).toBeNull();
    expect(
      within(dialog).queryByText("Type at least two characters to search indexed papers."),
    ).toBeNull();
    const searchInput = within(dialog).getByLabelText("Search papers for exploration");
    await waitFor(() => {
      expect(document.activeElement).toBe(searchInput);
    });
    fireEvent.change(searchInput, {
      target: { value: "benchmark" },
    });

    await waitFor(() => {
      expect(apiMocks.suggestBrowserPapers).toHaveBeenCalledWith("benchmark", 8);
    });
    expect(await screen.findByText("Benchmark Paper")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Explore" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New exploration" })).toBeNull();
    });
    expect(await screen.findByRole("heading", { name: "Benchmark Paper" })).toBeTruthy();
    expect(screen.getByText("No PDF URL is available for this paper.")).toBeTruthy();
  });

  it("keeps PDF selections inside the paper exploration sidebar chat", async () => {
    const explorationId = "paper-exploration:proj_1:arxiv:2401.12345";
    localStorage.setItem(
      ACTIVE_TAB_KEY,
      JSON.stringify({ kind: "paper_exploration", id: explorationId }),
    );
    localStorage.setItem(
      PAPER_EXPLORATIONS_KEY,
      JSON.stringify([
        {
          explorationId,
          projectId: "proj_1",
          paperId: "arxiv:2401.12345",
          path: "sections/introduction.md",
          title: "Benchmark Paper",
          authors: "Ada Lovelace",
          previewImageUrl: "",
          pdfUrl: "https://example.test/2401.12345.pdf",
          createdAt: "2026-04-21T00:00:00Z",
          updatedAt: "2026-04-21T00:00:00Z",
        },
      ]),
    );
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_paper_chat"));
    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        const toolItem = threadItemResponse(
          "tool_paper_search",
          "function_call",
          "assistant",
          JSON.stringify({ query: "grounding" }),
          {
            thread_id: threadId,
            turn_id: "turn_paper",
            call_id: "call_paper_search",
            name: "paperbase_search_sections",
            content_json: { query: "grounding" },
          },
        );
        onEvent("item_started", { item: toolItem });
        onEvent("item_completed", { item: toolItem });
        onEvent("item_completed", {
          item: threadItemResponse(
            "tool_paper_search_output",
            "function_call_output",
            "tool",
            "Found selected paper context.",
            {
              thread_id: threadId,
              turn_id: "turn_paper",
              call_id: "call_paper_search",
              name: "paperbase_search_sections",
            },
          ),
        });
        const commentaryItem = threadItemResponse(
          "msg_paper_commentary",
          "assistant_message",
          "assistant",
          "Checking the selected crop.",
          {
            thread_id: threadId,
            turn_id: "turn_paper",
            phase: "commentary",
            status: "completed",
          },
        );
        onEvent("item_started", { item: commentaryItem });
        onEvent("item_completed", { item: commentaryItem });
        const assistantItem = threadItemResponse(
          "msg_paper_assistant",
          "assistant_message",
          "assistant",
          "",
          {
            thread_id: threadId,
            turn_id: "turn_paper",
            phase: "final_answer",
            status: "in_progress",
          },
        );
        onEvent("item_started", { item: assistantItem });
        onEvent("item_delta", {
          item_id: "msg_paper_assistant",
          delta: "Paper streamed answer",
          delta_type: "output_text",
        });
        onEvent("item_completed", {
          item: {
            ...assistantItem,
            content_text: "Paper streamed answer",
            status: "completed",
          },
        });
      },
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Benchmark Paper" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Chat" })).toBeTruthy();
    const appShell = document.querySelector("#app-shell") as HTMLElement;
    const sidebarResizer = screen.getByRole("separator", {
      name: "Resize paper details panel",
    });
    expect(appShell.style.getPropertyValue("--paper-exploration-sidebar-width")).toBe("310px");
    fireEvent.pointerDown(sidebarResizer, {
      clientX: window.innerWidth - 500,
      pointerId: 7,
    });
    const growResizeEvent = new Event("pointermove", { bubbles: true, cancelable: true });
    Object.defineProperties(growResizeEvent, {
      clientX: { value: window.innerWidth - 900 },
      pointerId: { value: 7 },
    });
    fireEvent(document, growResizeEvent);
    expect(appShell.style.getPropertyValue("--paper-exploration-sidebar-width")).toBe("620px");
    const shrinkResizeEvent = new Event("pointermove", { bubbles: true, cancelable: true });
    Object.defineProperties(shrinkResizeEvent, {
      clientX: { value: window.innerWidth - 100 },
      pointerId: { value: 7 },
    });
    fireEvent(document, shrinkResizeEvent);
    expect(appShell.style.getPropertyValue("--paper-exploration-sidebar-width")).toBe("310px");
    const endResizeEvent = new Event("pointerup", { bubbles: true, cancelable: true });
    Object.defineProperty(endResizeEvent, "pointerId", { value: 7 });
    fireEvent(document, endResizeEvent);
    fireEvent.click(await screen.findByRole("button", { name: "Mock PDF explain selection" }));

    await waitFor(() => {
      expect(apiMocks.createThread).toHaveBeenCalledWith("Paper chat: Benchmark Paper", "proj_1");
    });
    await waitFor(() => {
      expect(apiMocks.sendThreadMessageStream).toHaveBeenCalledTimes(1);
    });
    expect(apiMocks.sendThreadMessageStream.mock.calls[0]?.[0]).toBe("thread_paper_chat");
    expect(apiMocks.sendThreadMessageStream.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        content: "Explain this PDF selection in context.",
        paper_ids: ["arxiv:2401.12345"],
        paper_selection: expect.objectContaining({
          action: "explain",
          paper_id: "arxiv:2401.12345",
          selected_text: "Selected benchmark text",
        }),
      }),
    );
    expect(screen.getByRole("tab", { name: "Chat" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByText("You are now focused on this paper.")).toBeNull();
    expect(screen.getByText("Explain this PDF selection in context.")).toBeTruthy();
    expect(await screen.findByText("Checking the selected crop.")).toBeTruthy();
    expect(await screen.findByText(/Did section search for "grounding"/)).toBeTruthy();
    expect(await screen.findByText("Paper streamed answer")).toBeTruthy();
    expect(document.querySelector(".pdf-document-viewer.paper-exploration-frame")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Benchmark Paper" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Mock PDF challenge area" }));
    await waitFor(() => {
      expect(apiMocks.sendThreadMessageStream).toHaveBeenCalledTimes(2);
    });
    expect(apiMocks.sendThreadMessageStream.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        content: "Challenge this PDF selection and check whether the paper supports it.",
        paper_selection: expect.objectContaining({
          action: "challenge",
          paper_id: "arxiv:2401.12345",
          selected_text: null,
          region: expect.objectContaining({
            image_base64: "ZmFrZS1hcmVh",
            content_type: "image/png",
          }),
        }),
      }),
    );
  });

  it("adds, edits, scrolls to, and deletes paper exploration notes", async () => {
    const explorationId = "paper-exploration:proj_1:arxiv:2401.12345";
    localStorage.setItem(
      ACTIVE_TAB_KEY,
      JSON.stringify({ kind: "paper_exploration", id: explorationId }),
    );
    localStorage.setItem(
      PAPER_EXPLORATIONS_KEY,
      JSON.stringify([
        {
          explorationId,
          projectId: "proj_1",
          paperId: "arxiv:2401.12345",
          path: "sections/introduction.md",
          title: "Benchmark Paper",
          authors: "Ada Lovelace",
          previewImageUrl: "",
          pdfUrl: "https://example.test/2401.12345.pdf",
          createdAt: "2026-04-21T00:00:00Z",
          updatedAt: "2026-04-21T00:00:00Z",
        },
      ]),
    );
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Benchmark Paper" })).toBeTruthy();
    expect(screen.getByTestId("mock-pdf-annotation-count").textContent).toBe("Mock annotations 0");
    fireEvent.click(screen.getByRole("button", { name: "Mock PDF add note" }));
    const noteDialog = await screen.findByRole("dialog", { name: "Add note" });
    fireEvent.change(within(noteDialog).getByLabelText("Paper note"), {
      target: { value: "Important benchmark note" },
    });
    fireEvent.click(within(noteDialog).getByRole("button", { name: "Add note" }));

    expect(await screen.findByText("Important benchmark note")).toBeTruthy();
    expect(screen.getByTestId("mock-pdf-annotation-count").textContent).toBe("Mock annotations 1");
    expect(pdfViewerMocks.clearSelection).toHaveBeenCalled();
    fireEvent.click(screen.getByText("Important benchmark note"));
    expect(pdfViewerMocks.scrollToAnnotation).toHaveBeenCalledWith(
      expect.stringMatching(/^paper-note-/),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const editDialog = await screen.findByRole("dialog", { name: "Edit note" });
    fireEvent.change(within(editDialog).getByLabelText("Paper note"), {
      target: { value: "Edited benchmark note" },
    });
    fireEvent.click(within(editDialog).getByRole("button", { name: "Save note" }));
    expect(await screen.findByText("Edited benchmark note")).toBeTruthy();
    expect(screen.queryByText("Important benchmark note")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Zen" }));
    expect(await screen.findByText("Mock highlights hidden")).toBeTruthy();
    expect(screen.queryByLabelText("Paper details")).toBeNull();
    expect(document.querySelector("#app-shell")?.classList.contains("sidebar-collapsed")).toBe(
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Zen" }));
    fireEvent.click(screen.getByLabelText("Open paper details"));

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const deleteDialog = await screen.findByRole("dialog", { name: "Delete note" });
    fireEvent.click(within(deleteDialog).getByRole("button", { name: "Delete note" }));
    await waitFor(() => {
      expect(screen.queryByText("Edited benchmark note")).toBeNull();
    });
    expect(screen.getByTestId("mock-pdf-annotation-count").textContent).toBe("Mock annotations 0");
  });

  it("clears exploration search before closing from the inline close button", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.suggestBrowserPapers.mockResolvedValue({
      fallback: false,
      suggestions: [],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByLabelText("Start exploration in General"));

    const dialog = screen.getByRole("dialog", { name: "New exploration" });
    const searchInput = within(dialog).getByLabelText(
      "Search papers for exploration",
    ) as HTMLInputElement;
    fireEvent.change(searchInput, { target: { value: "bench" } });

    fireEvent.click(within(dialog).getByRole("button", { name: "Clear exploration search" }));

    await waitFor(() => {
      expect(searchInput.value).toBe("");
    });
    expect(screen.getByRole("dialog", { name: "New exploration" })).toBeTruthy();

    const closeButton = screen
      .getAllByRole("button", { name: "Close exploration picker" })
      .find((button) => button.classList.contains("exploration-picker-close"));
    expect(closeButton).toBeTruthy();
    fireEvent.click(closeButton!);

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New exploration" })).toBeNull();
    });
  });

  it("scrolls selected exploration suggestions during keyboard navigation", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.suggestBrowserPapers.mockResolvedValue({
      fallback: false,
      suggestions: [
        {
          paper_id: "arxiv:2402.00001",
          arxiv_id: "2402.00001",
          title: "First Result",
          authors: "Ada Lovelace",
          summary_short: "",
          citation_count: 1,
          published: "2026-02-01",
          score: 0.9,
          matched_field: "title",
          highlight_snippet: "",
        },
        {
          paper_id: "arxiv:2402.00002",
          arxiv_id: "2402.00002",
          title: "Second Result",
          authors: "Grace Hopper",
          summary_short: "",
          citation_count: 2,
          published: "2026-02-02",
          score: 0.8,
          matched_field: "title",
          highlight_snippet: "",
        },
        {
          paper_id: "arxiv:2402.00003",
          arxiv_id: "2402.00003",
          title: "Third Result",
          authors: "Katherine Johnson",
          summary_short: "",
          citation_count: 3,
          published: "2026-02-03",
          score: 0.7,
          matched_field: "title",
          highlight_snippet: "",
        },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("General")[0]).toBeTruthy();
    });
    fireEvent.click(screen.getAllByText("General")[0]!);
    fireEvent.click(await screen.findByLabelText("Start exploration in General"));

    const dialog = screen.getByRole("dialog", { name: "New exploration" });
    const searchInput = within(dialog).getByLabelText("Search papers for exploration");
    fireEvent.change(searchInput, {
      target: { value: "result" },
    });
    expect(await screen.findByText("Third Result")).toBeTruthy();

    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    scrollIntoView.mockClear();
    fireEvent.keyDown(searchInput, { key: "ArrowDown" });

    await waitFor(() => {
      expect(within(dialog).getAllByRole("option")[1]?.getAttribute("aria-selected")).toBe("true");
      expect(scrollIntoView).toHaveBeenCalledWith({
        block: "nearest",
        inline: "nearest",
      });
    });
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

  it("shows repo path context for duplicate Python basenames in the sidebar", async () => {
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "python_file", id: "file_2" }));
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
      expect(apiMocks.getPythonFile).toHaveBeenCalledWith("file_2");
    });
    expect(apiMocks.getPythonFile).not.toHaveBeenCalledWith("file_1");
    expect(screen.queryByRole("tablist", { name: "Open tabs" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Close .*train\.py/ })).toBeNull();

    fireEvent.click(screen.getAllByText("General")[0]!);

    await waitFor(() => {
      expect(document.querySelectorAll(".python-file-card .thread-card-title")).toHaveLength(2);
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

  it("collapses and expands the desktop sidebar from the sidebar toggle", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByLabelText("Collapse sidebar")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Collapse sidebar"));

    await waitFor(() => {
      expect(document.querySelector("#app-shell")?.classList.contains("sidebar-collapsed")).toBe(
        true,
      );
    });
    expect(screen.getByLabelText("Expand sidebar")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Expand sidebar"));

    await waitFor(() => {
      expect(document.querySelector("#app-shell")?.classList.contains("sidebar-collapsed")).toBe(
        false,
      );
    });
  });

  it("expands the collapsed sidebar before running clicked sidebar actions", async () => {
    localStorage.setItem("paperbase-sidebar-collapsed", "true");
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(document.querySelector("#app-shell")?.classList.contains("sidebar-collapsed")).toBe(
        true,
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "Read docs" }));

    await waitFor(() => {
      expect(document.querySelector("#app-shell")?.classList.contains("sidebar-collapsed")).toBe(
        false,
      );
    });
    expect(window.open).toHaveBeenCalledWith(
      new URL("/docs", window.location.origin).toString(),
      "_blank",
      "noopener,noreferrer",
    );
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
      expect(screen.getByLabelText("Start conversation in Agents")).toBeTruthy();
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
    expect(screen.getByRole("button", { name: "Add exploration" })).toBeTruthy();
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
    await waitFor(() => {
      expect(localStorage.getItem(ACTIVE_TAB_KEY)).toBe(
        JSON.stringify({ kind: "python_file", id: "file_2" }),
      );
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
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
      expect(screen.getByLabelText("Start conversation in General")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("Start conversation in General"));

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
      expect(screen.getByLabelText("Start conversation in Agents")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("Start conversation in Agents"));

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

  it("shows a first-chat setup checklist when project agent prerequisites are missing", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Prepare the agent before the first chat")).toBeTruthy();
    });
    const checklist = screen.getByLabelText("Agent setup checklist");
    expect(
      within(checklist).getByText(
        /Add your OpenAI API key and connect anything else missing for .* so the first chat can run cleanly\./,
      ),
    ).toBeTruthy();
    expect(within(checklist).getByText("GitHub connection")).toBeTruthy();
    expect(within(checklist).getByText("OpenAI API key")).toBeTruthy();
    expect(within(checklist).getByText("Sandbox credentials")).toBeTruthy();
    expect(checklist.querySelector(".chat-setup-provider-mark.provider-modal")).toBeTruthy();
    expect(checklist.querySelector(".chat-setup-provider-mark.modal")).toBeNull();
    expect(screen.getByRole("button", { name: "Open GitHub setup" })).toBeTruthy();
    expect(screen.getByLabelText("OpenAI API key configured")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open sandbox setup" })).toBeTruthy();
  });

  it("dismisses the first-chat setup checklist", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Prepare the agent before the first chat")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Dismiss setup checklist" }));

    await waitFor(() => {
      expect(screen.queryByText("Prepare the agent before the first chat")).toBeNull();
    });
  });

  it("opens settings from the first-chat setup checklist", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Prepare the agent before the first chat")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Open GitHub setup" }));

    await waitFor(() => {
      expect(screen.getByText("Manage GitHub connection for repositories.")).toBeTruthy();
    });
  });

  it("collapses completed assistant commentary behind a worked summary", async () => {
    const commentaryItem = threadItemResponse(
      "item_commentary",
      "assistant_message",
      "assistant",
      "I'm checking the repo layout before I edit anything.",
      {
        thread_id: "thread_1",
        turn_id: "turn_1",
        phase: "commentary",
      },
    );
    const finalItem = threadItemResponse(
      "item_final",
      "assistant_message",
      "assistant",
      "Final answer",
      {
        thread_id: "thread_1",
        turn_id: "turn_1",
        phase: "final_answer",
      },
    );
    apiMocks.getThread.mockResolvedValue({
      thread_id: "thread_1",
      project_id: "proj_1",
      project_name: "General",
      project_instructions: "",
      title: "Recovered thread",
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
      items: [commentaryItem, finalItem],
      turns: [turnResponse("turn_1", "thread_1", [commentaryItem, finalItem])],
      messages: [
        {
          message_id: "item_commentary",
          role: "assistant",
          content_markdown: "I'm checking the repo layout before I edit anything.",
          citations: [],
          created_at: "2026-04-21T00:00:00Z",
          status: "completed",
          phase: "commentary",
        },
        {
          message_id: "item_final",
          role: "assistant",
          content_markdown: "Final answer",
          citations: [],
          created_at: "2026-04-21T00:00:01Z",
          status: "completed",
          phase: "final_answer",
        },
      ],
    });
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));

    render(<App />);

    const workSummary = await screen.findByText(/^Worked for /);
    const workDetails = workSummary.closest("details");
    const finalAnswer = screen.getByText("Final answer");
    expect(workDetails?.open).toBe(false);
    expect(screen.queryByText("Commentary")).toBeNull();
    expect(document.querySelector(".message-bubble.assistant.commentary")).toBeNull();
    expect(
      workSummary.compareDocumentPosition(finalAnswer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Retry response" })).toHaveLength(1);

    fireEvent.click(workSummary);
    expect(workDetails?.open).toBe(true);
    expect(screen.getByText("I'm checking the repo layout before I edit anything.")).toBeTruthy();
  });

  it("hides the chat tabs until an assistant reply exists", async () => {
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.getThread.mockResolvedValue(emptyThreadResponse("thread_1"));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Start new research in")).toBeTruthy();
    });
    expect(screen.queryByRole("tab", { name: "Conversation" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Research papers" })).toBeNull();
    expect(document.querySelector(".chat-message-list.thread-header-visible")).toBeNull();
  });

  it("keeps the chat tabs hidden while only the user prompt is visible", async () => {
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.getThread.mockResolvedValue({
      ...emptyThreadResponse("thread_1"),
      title: "Pending chat",
      messages: [
        {
          message_id: "user_1",
          role: "user",
          content_markdown: "Summarize recent planning papers",
          citations: [],
          created_at: "2026-04-21T00:00:00Z",
          status: "completed",
        },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Summarize recent planning papers")).toBeTruthy();
    });
    expect(screen.queryByRole("tab", { name: "Conversation" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Research papers" })).toBeNull();
    expect(document.querySelector(".chat-message-list.thread-header-visible")).toBeNull();
  });

  it("renders each reply title after its matching user prompt", async () => {
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    const userOne = threadItemResponse(
      "user_1",
      "user_message",
      "user",
      "Compare planning agents",
      {
        turn_id: "turn_1",
      },
    );
    const assistantOne = threadItemResponse(
      "assistant_1",
      "assistant_message",
      "assistant",
      "First answer",
      {
        turn_id: "turn_1",
        phase: "final_answer",
      },
    );
    const userTwo = threadItemResponse("user_2", "user_message", "user", "Now focus on memory", {
      turn_id: "turn_2",
    });
    const assistantTwo = threadItemResponse(
      "assistant_2",
      "assistant_message",
      "assistant",
      "Second answer",
      {
        turn_id: "turn_2",
        phase: "final_answer",
      },
    );
    apiMocks.getThread.mockResolvedValue({
      ...emptyThreadResponse("thread_1"),
      title: "Planning Agent Comparisons",
      items: [userOne, assistantOne, userTwo, assistantTwo],
      turns: [
        turnResponse("turn_1", "thread_1", [userOne, assistantOne], {
          title: "Planning Agents Compared",
        }),
        turnResponse("turn_2", "thread_1", [userTwo, assistantTwo], {
          title: "Memory Shapes Agent Behavior",
        }),
      ],
      messages: [
        {
          message_id: "user_1",
          role: "user",
          content_markdown: "Compare planning agents",
          citations: [],
          created_at: "2026-04-21T00:00:00Z",
          status: "completed",
        },
        {
          message_id: "assistant_1",
          role: "assistant",
          content_markdown: "First answer",
          citations: [],
          created_at: "2026-04-21T00:00:01Z",
          status: "completed",
          phase: "final_answer",
        },
        {
          message_id: "user_2",
          role: "user",
          content_markdown: "Now focus on memory",
          citations: [],
          created_at: "2026-04-21T00:01:00Z",
          status: "completed",
        },
        {
          message_id: "assistant_2",
          role: "assistant",
          content_markdown: "Second answer",
          citations: [],
          created_at: "2026-04-21T00:01:01Z",
          status: "completed",
          phase: "final_answer",
        },
      ],
    });

    render(<App />);

    const firstUser = await screen.findByText("Compare planning agents");
    const firstTitle = screen.getByRole("heading", {
      name: "Planning Agents Compared",
      level: 2,
    });
    const firstAnswer = screen.getByText("First answer");
    const secondUser = screen.getByText("Now focus on memory");
    const secondTitle = screen.getByRole("heading", {
      name: "Memory Shapes Agent Behavior",
      level: 2,
    });
    const secondAnswer = screen.getByText("Second answer");

    expect(
      firstUser.compareDocumentPosition(firstTitle) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      firstTitle.compareDocumentPosition(firstAnswer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      secondUser.compareDocumentPosition(secondTitle) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      secondTitle.compareDocumentPosition(secondAnswer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("anchors a newly submitted prompt at the top of the chat viewport", async () => {
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_1", "Recovered thread"));
    apiMocks.sendThreadMessageStream.mockImplementation(async () => undefined);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Grounded answer")).toBeTruthy();
    });
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    scrollIntoView.mockClear();

    fireEvent.change(screen.getByLabelText("mention-input-chat"), {
      target: { value: "Compare recent retrieval agents" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(apiMocks.sendThreadMessageStream).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "smooth",
        block: "start",
        inline: "nearest",
      });
    });
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
    expect(screen.getByText("Drawer summary")).toBeTruthy();
    expect(
      within(screen.getByLabelText("Cited paper drawer"))
        .getByRole("link", { name: "Code" })
        .getAttribute("href"),
    ).toBe("https://github.com/example/drawer-paper");
  });

  it("shows inline paper previews for cited research papers", async () => {
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    const benchmarkCitations = [
      {
        source_id: "S1",
        source_kind: "paperbase_paper",
        title: "Benchmark Paper",
        path: "sections/introduction.md",
        excerpt: "Agents plan and use tools.",
        paper_id: "arxiv:2401.12345",
        tags: [],
      },
      {
        source_id: "S2",
        source_kind: "paperbase_paper",
        title: "Benchmark Paper",
        path: "sections/results.md",
        excerpt: "Agents are evaluated on benchmarks.",
        paper_id: "arxiv:2401.12345",
        tags: [],
      },
    ];
    apiMocks.getThread.mockResolvedValue({
      thread_id: "thread_1",
      project_id: "proj_1",
      project_name: "General",
      project_instructions: "",
      title: "Preview thread",
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
      items: [
        threadItemResponse(
          "msg_1",
          "assistant_message",
          "assistant",
          "Read this paper [S1] and compare [S2].",
          {
            thread_id: "thread_1",
            turn_id: "turn_1",
            phase: "final_answer",
            citations: benchmarkCitations,
          },
        ),
      ],
      turns: [
        turnResponse("turn_1", "thread_1", [
          threadItemResponse(
            "msg_1",
            "assistant_message",
            "assistant",
            "Read this paper [S1] and compare [S2].",
            {
              thread_id: "thread_1",
              turn_id: "turn_1",
              phase: "final_answer",
              citations: benchmarkCitations,
            },
          ),
        ]),
      ],
      messages: [
        {
          message_id: "msg_1",
          role: "assistant",
          content_markdown: "Read this paper [S1] and compare [S2].",
          citations: benchmarkCitations,
          recommended_prompts: ["Compare tool use benchmarks across recent planning agents"],
          created_at: "2026-04-21T00:00:00Z",
          status: "completed",
        },
      ],
    });
    apiMocks.getBrowserPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      arxiv_id: "2401.12345",
      title: "Benchmark Paper",
      authors: "Ada Lovelace",
      abstract: "Grounded benchmark paper.",
      summary_short: "Short grounded benchmark summary.",
      categories: "cs.AI",
      primary_category: "cs.AI",
      published: "2026-04-01",
      updated: "2026-04-02",
      pdf_url: "https://example.test/2401.12345.pdf",
      html_url: "https://example.test/2401.12345",
      citation_count: 12,
      influential_citation_count: 3,
      index_status: "indexed",
    });
    apiMocks.getBrowserPaperPreview.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      image_url: "https://example.test/preview.webp",
      content_type: "image/webp",
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "[S1]" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "[S1]" }));
    const pdfDialog = await screen.findByRole("dialog", { name: "Benchmark Paper PDF" });
    expect(pdfDialog).toBeTruthy();
    expect(pdfDialog.querySelector(".pdf-document-viewer.paper-pdf-frame")).toBeTruthy();
    expect(pdfDialog.querySelector("iframe")).toBeNull();
    fireEvent.click(screen.getAllByLabelText("Close paper PDF")[0]!);

    const sourceButton = screen.getByRole("button", { name: "1 source" });
    fireEvent.click(sourceButton);
    expect(await screen.findByText("Benchmark Paper")).toBeTruthy();
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Benchmark Paper preview" })).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.queryByText("Cited papers (1)")).toBeNull();
    expect(screen.queryByText("Short grounded benchmark summary.")).toBeNull();
    expect(screen.queryByRole("link", { name: "HTML" })).toBeNull();
    expect(screen.queryByRole("link", { name: "PDF" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Conversation" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Research papers" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open chat actions" }));
    const headerMenu = screen.getByRole("menu");
    expect(within(headerMenu).queryByRole("menuitem", { name: "Export summary" })).toBeNull();
    expect(
      within(headerMenu).getByRole("menuitem", { name: "Export summary as markdown" }),
    ).toBeTruthy();
    expect(
      within(headerMenu).getByRole("menuitem", { name: "Export summary as PDF" }),
    ).toBeTruthy();
    fireEvent.click(within(headerMenu).getByRole("menuitem", { name: "Rename" }));
    const renameDialog = await screen.findByRole("dialog", { name: "Rename session" });
    expect((within(renameDialog).getByLabelText("Title") as HTMLInputElement).value).toBe(
      "Preview thread",
    );
    fireEvent.click(within(renameDialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Open chat actions" }));
    fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "Delete" }));
    expect(await screen.findByRole("heading", { name: "Delete session" })).toBeTruthy();
    expect(screen.getByText("Delete Preview thread?")).toBeTruthy();
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Delete session" })).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(screen.getByRole("button", { name: "Retry response" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Download reply" })).toBeTruthy();
    expect(screen.getByText("Recommended reading")).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Compare tool use benchmarks across recent planning agents",
      }),
    ).toBeTruthy();

    fireEvent.click(sourceButton);
    expect(screen.queryByText("Ada Lovelace")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Research papers" }));
    expect(await screen.findByText("Benchmark Paper")).toBeTruthy();
    expect(screen.queryByText("Short grounded benchmark summary.")).toBeNull();
    expect(screen.queryByRole("link", { name: "HTML" })).toBeNull();
    expect(screen.queryByRole("link", { name: "PDF" })).toBeNull();
    fireEvent.click(screen.getByRole("heading", { name: "Benchmark Paper" }));
    const secondPdfDialog = await screen.findByRole("dialog", { name: "Benchmark Paper PDF" });
    expect(secondPdfDialog).toBeTruthy();
    expect(screen.getByRole("button", { name: "Explore now" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Explore options" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Explore later" }));
    expect(screen.getByRole("dialog", { name: "Benchmark Paper PDF" })).toBeTruthy();
    fireEvent.click(screen.getAllByLabelText("Close paper PDF")[0]!);
    expect(screen.queryByText("Read this paper [S1] and compare [S2].")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Conversation" }));
    fireEvent.click(screen.getByRole("button", { name: "Like response" }));
    expect(
      await screen.findByRole("heading", {
        name: "What did you like about this response? (optional)",
      }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accurate" }));
    fireEvent.change(screen.getByLabelText("Feedback details"), {
      target: { value: "Looks right" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send feedback" }));
    await waitFor(() => {
      expect(apiMocks.submitChatFeedback).toHaveBeenCalledWith(
        "thread_1",
        expect.objectContaining({
          message_id: "msg_1",
          sentiment: "positive",
          reasons: ["Accurate"],
          note: "Looks right",
          debug_bundle: expect.objectContaining({ thread_id: "thread_1" }),
        }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "1 source" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open exploration" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Benchmark Paper" })).toBeTruthy();
    });
    expect(screen.queryByText("Exploration")).toBeNull();
    expect(document.querySelector(".pdf-document-viewer.paper-exploration-frame")).toBeTruthy();
    expect(document.querySelector("iframe")).toBeNull();
    expect(screen.getByText("Explorations")).toBeTruthy();
    expect(screen.queryByText("Details")).toBeNull();
    expect(screen.getByRole("tab", { name: "Info" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Chat" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Notes" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Social" })).toBeTruthy();
    const abstractSection = await screen.findByRole("button", { name: "Abstract" });
    const introductionSection = screen.getByRole("button", { name: "Introduction" });
    expect(abstractSection.classList.contains("active")).toBe(false);
    expect(introductionSection.classList.contains("active")).toBe(false);
    fireEvent.click(introductionSection);
    expect(pdfViewerMocks.scrollToSection).toHaveBeenCalledWith("introduction-1");
    expect(introductionSection.classList.contains("active")).toBe(true);
    await waitFor(() => {
      expect(apiMocks.getBrowserPaperDiscussion).toHaveBeenCalledWith("arxiv:2401.12345");
    });
    fireEvent.click(await screen.findByRole("button", { name: "Upvote 4" }));
    await waitFor(() => {
      expect(apiMocks.upvoteBrowserPaper).toHaveBeenCalledWith("arxiv:2401.12345");
    });
    expect(await screen.findByRole("button", { name: "Upvoted 5" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Social" }));
    expect(await screen.findByText("Existing paper comment")).toBeTruthy();
    expect(screen.queryByText("Existing paper reply")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "View 1 reply" }));
    expect(await screen.findByText("Existing paper reply")).toBeTruthy();
    apiMocks.createBrowserPaperComment.mockResolvedValueOnce({
      paper_id: "arxiv:2401.12345",
      comments: [
        {
          comment_id: "pcmt_1",
          paper_id: "arxiv:2401.12345",
          parent_comment_id: null,
          author: { user_id: "user_2", display_name: "Existing Reader" },
          body: "Existing paper comment",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          replies: [
            {
              comment_id: "pcmt_1_reply",
              paper_id: "arxiv:2401.12345",
              parent_comment_id: "pcmt_1",
              author: { user_id: "user_3", display_name: "Reply Reader" },
              body: "Existing paper reply",
              created_at: "2026-04-21T00:02:00Z",
              updated_at: "2026-04-21T00:02:00Z",
              replies: [],
            },
            {
              comment_id: "pcmt_1_reply_2",
              paper_id: "arxiv:2401.12345",
              parent_comment_id: "pcmt_1",
              author: { user_id: "user_1", display_name: "Ada Lovelace" },
              body: "Nested reply",
              created_at: "2026-04-21T00:03:00Z",
              updated_at: "2026-04-21T00:03:00Z",
              replies: [],
            },
          ],
        },
      ],
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Reply" })[0]);
    const replyInput = screen.getByLabelText("Reply to Existing Reader");
    fireEvent.change(replyInput, { target: { value: "Nested reply" } });
    fireEvent.click(
      within(replyInput.closest("form") as HTMLFormElement).getByRole("button", {
        name: "Reply",
      }),
    );
    await waitFor(() => {
      expect(apiMocks.createBrowserPaperComment).toHaveBeenCalledWith("arxiv:2401.12345", {
        body: "Nested reply",
        parent_comment_id: "pcmt_1",
      });
    });
    expect(await screen.findByText("Nested reply")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Paper comment"), {
      target: { value: "New paper comment" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));
    await waitFor(() => {
      expect(apiMocks.createBrowserPaperComment).toHaveBeenCalledWith("arxiv:2401.12345", {
        body: "New paper comment",
        parent_comment_id: null,
      });
    });
    expect(await screen.findByText("New paper comment")).toBeTruthy();
    expect(document.querySelector(".paper-exploration-card-image")).toBeNull();
    fireEvent.click(screen.getByLabelText("Delete exploration Benchmark Paper"));
    const deleteExplorationDialog = await screen.findByRole("dialog", {
      name: "Delete exploration",
    });
    expect(within(deleteExplorationDialog).getByText("Delete Benchmark Paper?")).toBeTruthy();
    fireEvent.click(
      within(deleteExplorationDialog).getByRole("button", { name: "Delete exploration" }),
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create presentation" })).toBeTruthy();
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

  it("preserves pending landing mentions when an API key gate restores the prompt into the chat composer", async () => {
    sessionStorage.setItem(
      PENDING_PROMPT_KEY,
      JSON.stringify({
        prompt: "Summarize @Mentioned Paper",
        paperIds: ["arxiv:2401.12345"],
        mentions: [{ id: "arxiv:2401.12345", title: "Mentioned Paper" }],
      }),
    );
    apiMocks.loadSession.mockResolvedValue(
      authenticatedSession({ has_openai_api_key: false, has_anthropic_api_key: false }),
    );
    apiMocks.api.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.sendThreadMessageStream.mockResolvedValue(undefined);

    render(<App />);

    await waitFor(() => {
      expect((screen.getByLabelText("mention-input-chat") as HTMLInputElement).value).toBe(
        "Summarize @Mentioned Paper",
      );
    });
    await waitFor(() => {
      expect(document.querySelector("#settings-anthropic-form")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("OpenAI API key"), {
      target: { value: "sk-test-pending-mention" },
    });
    fireEvent.submit(document.querySelector("#settings-anthropic-form") as HTMLFormElement);

    await waitFor(() => {
      expect(apiMocks.api).toHaveBeenCalledWith("/v1/browser/settings/openai-key", {
        method: "PUT",
        body: JSON.stringify({
          username: "user@example.com",
          api_key: "sk-test-pending-mention",
        }),
      });
    });
    await waitFor(() => {
      expect(document.querySelector("#settings-anthropic-form")).toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(apiMocks.sendThreadMessageStream).toHaveBeenCalledTimes(1);
    });
    expect(apiMocks.sendThreadMessageStream.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        content: "Summarize @Mentioned Paper",
        paper_ids: ["arxiv:2401.12345"],
      }),
    );
  });

  it("shows a re-enter-key message when the saved OpenAI key is invalid", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.sendThreadMessageStream.mockImplementation(
      async (
        _threadId: string,
        _payload: unknown,
        onEvent: (eventName: string, payload: unknown) => void,
      ) => {
        onEvent("api_key_required", {
          thread: emptyThreadResponse("thread_2"),
          turn: turnResponse("turn_invalid", "thread_2", [], { status: "failed" }),
          final_item: null,
          has_openai_api_key: false,
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
          "Your saved OpenAI API key could not be decrypted. Add it again in Settings.",
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
        onEvent("reply_title", {
          thread_id: "thread_2",
          turn_id: "turn_2",
          title: "Grounding Methods Under Review",
        });
        onEvent("thread_title", {
          thread_id: "thread_2",
          title: "Grounding Methods Under Review",
        });
        onEvent("turn_started", {
          turn: turnResponse("turn_2", "thread_2", [], {
            title: "Grounding Methods Under Review",
            status: "running",
            completed_at: null,
          }),
        });
        onEvent("usage", {
          model: "claude-opus-4-7",
          input_tokens: 117900,
          output_tokens: 100,
          total_tokens: 118000,
          context_window_tokens: 258000,
        });
        const commentaryItem = threadItemResponse(
          "msg_commentary",
          "assistant_message",
          "assistant",
          "",
          {
            thread_id: "thread_2",
            turn_id: "turn_2",
            phase: "commentary",
            status: "in_progress",
          },
        );
        onEvent("item_started", { item: commentaryItem });
        onEvent("item_delta", {
          item_id: "msg_commentary",
          delta: "I will search the paper index first.",
          delta_type: "output_text",
        });
        onEvent("item_completed", {
          item: {
            ...commentaryItem,
            content_text: "I will search the paper index first.",
            status: "completed",
          },
        });
        onEvent("item_started", {
          item: threadItemResponse("item_tool_1", "function_call", "assistant", "", {
            thread_id: "thread_2",
            turn_id: "turn_2",
            call_id: "tool_1",
            name: "paperbase_search",
            content_json: { query: "grounding" },
            status: "in_progress",
          }),
        });
        onEvent("item_completed", {
          item: threadItemResponse("item_tool_1", "function_call", "assistant", "", {
            thread_id: "thread_2",
            turn_id: "turn_2",
            call_id: "tool_1",
            name: "paperbase_search",
            content_json: { query: "grounding" },
          }),
        });
        onEvent("item_started", {
          item: threadItemResponse("item_tool_1_output", "function_call_output", "tool", "", {
            thread_id: "thread_2",
            turn_id: "turn_2",
            call_id: "tool_1",
            name: "paperbase_search",
            status: "in_progress",
          }),
        });
        onEvent("item_completed", {
          item: threadItemResponse(
            "item_tool_1_output",
            "function_call_output",
            "tool",
            '{\n  "result_set_id": "rs_1"\n}',
            {
              thread_id: "thread_2",
              turn_id: "turn_2",
              call_id: "tool_1",
              name: "paperbase_search",
              content_json: { result_set_id: "rs_1" },
            },
          ),
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
    expect(screen.queryByRole("tablist", { name: "Open tabs" })).toBeNull();
    const userText = screen.getByText("Find the grounding methods");
    const commentaryText = await screen.findByText("I will search the paper index first.");
    const replyTitle = screen.getByRole("heading", {
      name: "Grounding Methods Under Review",
      level: 2,
    });
    expect(
      userText.compareDocumentPosition(replyTitle) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      replyTitle.compareDocumentPosition(commentaryText) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const toolActivityText = await screen.findByText('Did paper search for "grounding"');
    expect(screen.queryByText("Commentary")).toBeNull();
    expect(
      commentaryText.compareDocumentPosition(toolActivityText) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
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

    randomSpy.mockRestore();
  });

  it("shows project coding activity copy and a live Python preview while code streams", async () => {
    const streamingMarkdown = "Here is the script:\n\n```python\ndef train():\n    return 1\n";
    const completedMarkdown = `${streamingMarkdown}\`\`\``;
    apiMocks.loadSession.mockResolvedValue(authenticatedSession());
    apiMocks.createThread.mockResolvedValue(emptyThreadResponse("thread_2"));
    apiMocks.getThread.mockResolvedValue(threadResponse("thread_2", "New chat", completedMarkdown));

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
        const toolItem = threadItemResponse("item_patch", "function_call", "assistant", "", {
          thread_id: "thread_2",
          turn_id: "turn_project_preview",
          call_id: "tool_1",
          name: "project_apply_patch",
          content_json: {
            repo_path: "src/train.py",
            old_string: "",
            new_string: "def train():\n    return 1\n",
          },
          status: "in_progress",
        });
        const assistantItem = threadItemResponse("msg_2", "assistant_message", "assistant", "", {
          thread_id: "thread_2",
          turn_id: "turn_project_preview",
          status: "in_progress",
        });
        onEvent("turn_started", {
          turn: turnResponse("turn_project_preview", "thread_2", [], {
            status: "running",
            completed_at: null,
          }),
        });
        onEvent("item_started", { item: toolItem });
        onEvent("item_started", { item: assistantItem });
        onEvent("item_delta", {
          item_id: "msg_2",
          delta: streamingMarkdown,
          delta_type: "output_text",
        });
        await streamDone;
        onEvent("item_completed", {
          item: { ...toolItem, status: "completed" },
        });
        onEvent("item_started", {
          item: threadItemResponse("item_patch_output", "function_call_output", "tool", "", {
            thread_id: "thread_2",
            turn_id: "turn_project_preview",
            call_id: "tool_1",
            name: "project_apply_patch",
            status: "in_progress",
          }),
        });
        onEvent("item_completed", {
          item: threadItemResponse(
            "item_patch_output",
            "function_call_output",
            "tool",
            '{\n  "status": "ok"\n}',
            {
              thread_id: "thread_2",
              turn_id: "turn_project_preview",
              call_id: "tool_1",
              name: "project_apply_patch",
              content_json: { status: "ok" },
            },
          ),
        });
        const completedAssistantItem = threadItemResponse(
          "msg_2",
          "assistant_message",
          "assistant",
          completedMarkdown,
          {
            thread_id: "thread_2",
            turn_id: "turn_project_preview",
            phase: "final_answer",
          },
        );
        onEvent("item_completed", {
          item: completedAssistantItem,
        });
        const completedThread = {
          ...threadResponse("thread_2", "New chat", completedMarkdown),
          items: [completedAssistantItem],
          turns: [turnResponse("turn_project_preview", "thread_2", [completedAssistantItem])],
        };
        onEvent("turn_completed", {
          thread: completedThread,
          turn: turnResponse("turn_project_preview", "thread_2", [completedAssistantItem]),
          final_item: completedAssistantItem,
          has_openai_api_key: false,
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
    expect(screen.getByText("Here is the script:")).toBeTruthy();
    expect(within(preview).getByText("Writing code")).toBeTruthy();
    expect(preview.textContent).toContain("python");
    expect(preview.textContent).toContain("1");
    expect(preview.textContent).toContain("2");
    expect(preview.textContent).toContain("def train():");
    expect(document.querySelectorAll(".code-snippet-shell")).toHaveLength(1);
    expect(document.querySelector(".message-code-snippet")).toBeNull();
    expect(screen.queryByRole("button", { name: "Collapse code block" })).toBeNull();
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
        const assistantItem = threadItemResponse("msg_2", "assistant_message", "assistant", "", {
          thread_id: "thread_2",
          turn_id: "turn_fence",
          status: "in_progress",
        });
        onEvent("turn_started", {
          turn: turnResponse("turn_fence", "thread_2", [], {
            status: "running",
            completed_at: null,
          }),
        });
        onEvent("item_started", { item: assistantItem });
        onEvent("item_delta", {
          item_id: "msg_2",
          delta: "```python\ndef train():\n    return 1\n",
          delta_type: "output_text",
        });
        await waitForFenceClose;
        onEvent("item_delta", {
          item_id: "msg_2",
          delta: "```\n\nThis script is ready to run.",
          delta_type: "output_text",
        });
        await streamDone;
        const completedAssistantItem = threadItemResponse(
          "msg_2",
          "assistant_message",
          "assistant",
          "```python\ndef train():\n    return 1\n```\n\nThis script is ready to run.",
          {
            thread_id: "thread_2",
            turn_id: "turn_fence",
            phase: "final_answer",
          },
        );
        onEvent("item_completed", { item: completedAssistantItem });
        onEvent("turn_completed", {
          thread: {
            ...threadResponse(
              "thread_2",
              "New chat",
              "```python\ndef train():\n    return 1\n```\n\nThis script is ready to run.",
            ),
            items: [completedAssistantItem],
            turns: [turnResponse("turn_fence", "thread_2", [completedAssistantItem])],
          },
          turn: turnResponse("turn_fence", "thread_2", [completedAssistantItem]),
          final_item: completedAssistantItem,
          has_openai_api_key: false,
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
        const toolItem = threadItemResponse("item_write", "function_call", "assistant", "", {
          thread_id: "thread_2",
          turn_id: "turn_refresh",
          call_id: "tool_1",
          name: "project_write_file",
          content_json: { repo_path: "src/trainer.py", content: "print('trainer')" },
          status: "in_progress",
        });
        onEvent("turn_started", {
          turn: turnResponse("turn_refresh", "thread_2", [], {
            status: "running",
            completed_at: null,
          }),
        });
        onEvent("item_started", { item: toolItem });
        onEvent("item_completed", {
          item: { ...toolItem, status: "completed" },
        });
        pythonItems = [projectItemFile("file_2", "trainer.py", "proj_1", "src/trainer.py")];
        onEvent("item_started", {
          item: threadItemResponse("item_write_output", "function_call_output", "tool", "", {
            thread_id: "thread_2",
            turn_id: "turn_refresh",
            call_id: "tool_1",
            name: "project_write_file",
            status: "in_progress",
          }),
        });
        onEvent("item_completed", {
          item: threadItemResponse(
            "item_write_output",
            "function_call_output",
            "tool",
            '{\n  "repo_path": "src/trainer.py"\n}',
            {
              thread_id: "thread_2",
              turn_id: "turn_refresh",
              call_id: "tool_1",
              name: "project_write_file",
              content_json: { repo_path: "src/trainer.py" },
            },
          ),
        });
        const finalItem = threadItemResponse(
          "msg_2",
          "assistant_message",
          "assistant",
          "Created trainer.py",
          {
            thread_id: "thread_2",
            turn_id: "turn_refresh",
            phase: "final_answer",
          },
        );
        onEvent("item_completed", { item: finalItem });
        onEvent("turn_completed", {
          thread: {
            ...threadResponse("thread_2", "New chat", "Created trainer.py"),
            items: [finalItem],
            turns: [turnResponse("turn_refresh", "thread_2", [finalItem])],
          },
          turn: turnResponse("turn_refresh", "thread_2", [finalItem]),
          final_item: finalItem,
          has_openai_api_key: false,
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
        const assistantItem = threadItemResponse(
          "msg_stream",
          "assistant_message",
          "assistant",
          "",
          {
            thread_id: "thread_2",
            turn_id: "turn_usage",
            status: "in_progress",
          },
        );
        onEvent("turn_started", {
          turn: turnResponse("turn_usage", "thread_2", [], {
            status: "running",
            completed_at: null,
          }),
        });
        onEvent("item_started", { item: assistantItem });
        onEvent("usage", {
          model: "claude-opus-4-7",
          input_tokens: 117900,
          output_tokens: 100,
          total_tokens: 118000,
          context_window_tokens: 258000,
        });
        onEvent("item_delta", {
          item_id: "msg_stream",
          delta: "Grounded streamed answer",
          delta_type: "output_text",
        });
        const finalItem = threadItemResponse(
          "msg_stream",
          "assistant_message",
          "assistant",
          "Grounded streamed answer",
          {
            thread_id: "thread_2",
            turn_id: "turn_usage",
            phase: "final_answer",
          },
        );
        onEvent("item_completed", { item: finalItem });
        onEvent("turn_completed", {
          thread: {
            ...threadResponse("thread_2", "New chat", "Grounded streamed answer"),
            items: [finalItem],
            turns: [turnResponse("turn_usage", "thread_2", [finalItem])],
          },
          turn: turnResponse("turn_usage", "thread_2", [finalItem]),
          final_item: finalItem,
          has_openai_api_key: false,
          has_anthropic_api_key: false,
          state: "completed",
          usage: {
            model: "claude-opus-4-7",
            input_tokens: 117900,
            output_tokens: 100,
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
        const compactionItem = threadItemResponse(
          "msg_compact",
          "compaction",
          "assistant",
          "Compacting conversation...",
          {
            thread_id: "thread_compact",
            turn_id: "turn_compact",
            status: "in_progress",
          },
        );
        onEvent("turn_started", {
          turn: turnResponse("turn_compact", "thread_compact", [], {
            status: "running",
            completed_at: null,
          }),
        });
        onEvent("item_started", { item: compactionItem });
        const completedCompactionItem = threadItemResponse(
          "msg_compact",
          "compaction",
          "assistant",
          "## Compacted Conversation\n\nPreserved the current task.",
          {
            thread_id: "thread_compact",
            turn_id: "turn_compact",
          },
        );
        onEvent("item_completed", {
          item: completedCompactionItem,
        });
        const toolItem = threadItemResponse(
          "item_after_compact",
          "function_call",
          "assistant",
          "",
          {
            thread_id: "thread_compact",
            turn_id: "turn_compact",
            call_id: "tool_after_compact",
            name: "paperbase_search",
            content_json: { query: "context" },
            status: "in_progress",
          },
        );
        onEvent("item_started", {
          item: toolItem,
        });
        onEvent("item_completed", {
          item: { ...toolItem, status: "completed" },
        });
        onEvent("item_started", {
          item: threadItemResponse(
            "item_after_compact_output",
            "function_call_output",
            "tool",
            "",
            {
              thread_id: "thread_compact",
              turn_id: "turn_compact",
              call_id: "tool_after_compact",
              name: "paperbase_search",
              status: "in_progress",
            },
          ),
        });
        onEvent("item_completed", {
          item: threadItemResponse(
            "item_after_compact_output",
            "function_call_output",
            "tool",
            '{\n  "result_set_id": "rs_after_compact"\n}',
            {
              thread_id: "thread_compact",
              turn_id: "turn_compact",
              call_id: "tool_after_compact",
              name: "paperbase_search",
              content_json: { result_set_id: "rs_after_compact" },
            },
          ),
        });
        onEvent("item_started", {
          item: threadItemResponse("msg_answer", "assistant_message", "assistant", "", {
            thread_id: "thread_compact",
            turn_id: "turn_compact",
            status: "in_progress",
          }),
        });
        const finalItem = threadItemResponse(
          "msg_answer",
          "assistant_message",
          "assistant",
          "Answer after compaction",
          {
            thread_id: "thread_compact",
            turn_id: "turn_compact",
            phase: "final_answer",
          },
        );
        onEvent("item_completed", {
          item: finalItem,
        });
        onEvent("turn_completed", {
          thread: {
            ...threadResponse("thread_compact", "New chat", "Answer after compaction"),
            items: [completedCompactionItem, finalItem],
            turns: [
              turnResponse("turn_compact", "thread_compact", [completedCompactionItem, finalItem]),
            ],
          },
          turn: turnResponse("turn_compact", "thread_compact", [
            completedCompactionItem,
            finalItem,
          ]),
          final_item: finalItem,
          has_openai_api_key: false,
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
    expect(screen.getByRole("button", { name: "Trending papers" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Control panel" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Trending papers" }));
    await waitFor(() => {
      expect(screen.getByTestId("trending-papers-page")).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "Close Trending papers" })).toBeNull();

    fireEvent.click(document.querySelector("#account-menu-button") as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Inference", selected: true })).toBeTruthy();
    });
    expect(screen.getByRole("tab", { name: "Sandbox" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Audio" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "GitHub" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Appearance" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Admin" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Notifications" })).toBeNull();
    expect(document.querySelector("#auth-modal-title")).toBeNull();
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Inference");
    expect(screen.getAllByText("Manage OpenAI inference API key.")).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "Audio" }));
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Audio");
    expect(screen.getAllByText("Manage ElevenLabs presentation audio.")).toHaveLength(1);
    expect(screen.getByLabelText("ElevenLabs API key")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Sandbox" }));
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Sandbox");
    expect(screen.getAllByText("Manage Modal sandbox credentials.")).toHaveLength(1);
    expect(screen.getByLabelText("Modal token ID")).toBeTruthy();
    expect(screen.getByLabelText("Modal token secret")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Appearance" }));
    expect(document.querySelector(".settings-section-title")?.textContent).toBe("Appearance");
    expect(screen.getAllByText("Choose the theme for signed-in pages.")).toHaveLength(1);
    expect(screen.getByRole("radio", { name: /Light/i })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Dark/i })).toBeTruthy();
  });

  it("opens the sidebar admin panel for admin users and keeps it out of settings", async () => {
    const dateNowSpy = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-04-21T12:00:00Z").getTime());
    const longSkippedTitle =
      "This Paper Title Is Deliberately Long Enough To Need Truncation In The Admin Paper Table";
    const truncatedSkippedTitle = `${longSkippedTitle.slice(0, 60).trimEnd()}...`;
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "job_root_1",
          root_job_id: "job_root_1",
          job_type: "harvest_range",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:00:00Z",
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 2,
          paper_outcome_counts: { indexed: 2, skipped: 1, failed: 0 },
          latest_status_summary: "1 completed · 1 total",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });
    apiMocks.loadBrowserAdminJobPapers.mockResolvedValue({
      root_job_id: "job_root_1",
      total_count: 3,
      offset: 0,
      limit: 100,
      has_more: false,
      outcome_counts: { indexed: 2, skipped: 1 },
      skipped_reason_counts: { already_indexed: 1 },
      papers: [
        {
          root_job_id: "job_root_1",
          job_id: "job_chunk_1",
          paper_id: "arxiv:2401.12345",
          arxiv_id: "2401.12345",
          title: "Benchmark Paper",
          outcome: "indexed",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:10:00Z",
          paper: null,
        },
        {
          root_job_id: "job_root_1",
          job_id: "job_chunk_1",
          paper_id: "arxiv:2401.12346",
          arxiv_id: "2401.12346",
          title: longSkippedTitle,
          outcome: "skipped",
          skip_reason: "already_indexed",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:11:00Z",
          paper: null,
        },
      ],
    });
    localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify({ kind: "thread", id: "thread_1" }));
    try {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
      });
      expect(screen.queryByRole("tablist", { name: "Open tabs" })).toBeNull();
      expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
      expect(screen.getByText("Grounded answer")).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
      });
      expect(screen.queryByRole("tablist", { name: "Open tabs" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
      expect(screen.queryByText("Grounded answer")).toBeNull();
      expect(await screen.findByTestId("admin-jobs-chart")).toBeTruthy();
      const chart = screen.getByTestId("admin-jobs-chart-inner");
      expect(chart.getAttribute("data-grid-bottom")).toBe("26");
      expect(chart.getAttribute("data-grid-left")).toBe("0");
      expect(chart.getAttribute("data-has-data-zoom")).toBe("false");
      expect(chart.getAttribute("data-x-axis-label-align-min")).toBe("left");
      expect(chart.getAttribute("data-x-axis-label-align-max")).toBe("right");
      expect(chart.getAttribute("data-x-axis-label-font-size")).toBe("8");
      expect(chart.getAttribute("data-x-axis-label-show")).toBe("true");
      expect(chart.getAttribute("data-x-axis-tick-show")).toBe("true");
      expect(chart.getAttribute("data-x-axis-tick-interval")).toBe("0");
      expect(Number(chart.getAttribute("data-x-axis-data-count"))).toBeGreaterThanOrEqual(80);
      expect(chart.getAttribute("data-series-names")).toBe("Indexed,Updated,Skipped,Failed");
      expect(chart.getAttribute("data-series-colors")).toBe("#432818,#2563eb,#99582a,#b42318");
      expect(chart.getAttribute("data-series-stacks")).toBe(
        "paper outcomes,paper outcomes,paper outcomes,paper outcomes",
      );
      expect((chart as HTMLElement).style.height).toBe("320px");
      expect(
        within(screen.getByRole("table")).getByRole("columnheader", { name: "Trigger" }),
      ).toBeTruthy();
      expect(screen.getAllByText("Requested").length).toBeGreaterThan(0);
      expect(screen.queryByRole("button", { name: "Close Control panel" })).toBeNull();
      expect(apiMocks.loadBrowserAdminJobsOverview).toHaveBeenCalledWith(200);

      const jobRow = screen.getByText("job_root_1").closest("tr");
      expect(jobRow).toBeTruthy();
      fireEvent.click(jobRow as HTMLTableRowElement);
      await waitFor(() => {
        expect(screen.getByText("Latest status: 1 completed · 1 total")).toBeTruthy();
      });

      fireEvent.click(within(screen.getByRole("table")).getByRole("button", { name: "2" }));
      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Papers in job_root_1" })).toBeTruthy();
      });
      const backButton = screen.getByRole("button", { name: "Back to jobs" });
      expect(backButton.textContent).toBe("←");
      expect(screen.getByText("1-2 of 3")).toBeTruthy();
      expect(screen.queryAllByText("job_root_1")).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Previous page" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Next page" })).toBeTruthy();
      const statusSummary = screen.getByRole("group", { name: "Tracked paper outcomes" });
      expect(within(statusSummary).getByText("Indexed")).toBeTruthy();
      expect(within(statusSummary).getByText("Skipped: Already indexed")).toBeTruthy();
      expect(screen.getByText(truncatedSkippedTitle)).toBeTruthy();
      expect(screen.queryByText(longSkippedTitle)).toBeNull();
      expect(screen.getByText("Skipped").getAttribute("title")).toBe("Skipped: Already indexed");
      expect(screen.getAllByText(/\d{1,2} \w{3} at \d{1,2}:\d{2} [AP]M/).length).toBeGreaterThan(0);
      expect(screen.queryByRole("button", { name: "Close Control panel" })).toBeNull();
      expect(apiMocks.loadBrowserAdminJobPapers).toHaveBeenCalledWith("job_root_1", 0, 100);

      fireEvent.click(document.querySelector("#account-menu-button") as HTMLElement);
      fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));

      await waitFor(() => {
        expect(screen.queryByRole("tab", { name: "Admin" })).toBeNull();
      });
    } finally {
      dateNowSpy.mockRestore();
    }
  });

  it("opens tracked-paper admin drilldowns from internal jobs", async () => {
    const dateNowSpy = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-04-21T12:00:00Z").getTime());
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "job_reindex_1",
          root_job_id: "job_reindex_1",
          job_type: "reindex_embeddings",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 2,
          priority: 0,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:00:00Z",
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 0,
          tracked_paper_count: 1,
          paper_outcome_counts: { updated: 1 },
          latest_status_summary: "1 completed · 1 total",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });
    apiMocks.loadBrowserAdminJobPapers.mockResolvedValue({
      root_job_id: "job_reindex_1",
      total_count: 1,
      offset: 0,
      limit: 100,
      has_more: false,
      outcome_counts: { updated: 1 },
      skipped_reason_counts: {},
      papers: [
        {
          root_job_id: "job_reindex_1",
          job_id: "job_reindex_1",
          paper_id: "arxiv:2401.22222",
          arxiv_id: "2401.22222",
          title: "Reindexed Paper",
          outcome: "updated",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:10:00Z",
          paper: null,
        },
      ],
    });

    try {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
      });

      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      const jobRow = (await within(screen.getByRole("table")).findByText("job_reindex_1")).closest(
        "tr",
      );
      expect(jobRow).toBeTruthy();
      const trackedPapersButton = within(jobRow as HTMLElement).getByRole("button", {
        name: "1",
      });
      fireEvent.click(trackedPapersButton);

      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Papers in job_reindex_1" })).toBeTruthy();
      });
      const statusSummary = screen.getByRole("group", { name: "Tracked paper outcomes" });
      expect(within(statusSummary).getByText("Updated")).toBeTruthy();
      expect(screen.getByText("Reindexed Paper")).toBeTruthy();
      expect(screen.getAllByText("Updated").length).toBeGreaterThan(0);
      expect(apiMocks.loadBrowserAdminJobPapers).toHaveBeenCalledWith("job_reindex_1", 0, 100);
    } finally {
      dateNowSpy.mockRestore();
    }
  });

  it("shows an empty admin jobs table when the selected window has no jobs", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [],
      worker: null,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
    });
    const table = screen.getByRole("table");
    expect(screen.getAllByText("No tracked papers in this window").length).toBeGreaterThan(0);
    expect(screen.getByTestId("admin-jobs-chart-skeleton")).toBeTruthy();
    expect(screen.queryByTestId("admin-jobs-table-skeleton")).toBeNull();
    expect(within(table).getByRole("columnheader", { name: "Type" })).toBeTruthy();
    expect(within(table).getByText(/No jobs in this window/)).toBeTruthy();
  });

  it("shows why each admin job ran", async () => {
    const dateNowSpy = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-04-21T12:00:00Z").getTime());
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "env:latest_sync",
          root_job_id: "job_sync_1",
          job_type: "latest_sync_schedule",
          status: "scheduled",
          display_status: "scheduled",
          payload: {},
          attempts: 0,
          max_attempts: 0,
          priority: 0,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:00:00Z",
          is_synthetic: true,
          tracking_available: false,
          indexed_paper_count: null,
          latest_status_summary: "Runs on the worker schedule.",
          child_job_count: 0,
          details: {},
        },
        {
          job_id: "job_range_1",
          root_job_id: "job_range_1",
          job_type: "harvest_range",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:00:00Z",
          is_synthetic: false,
          tracking_available: false,
          indexed_paper_count: null,
          latest_status_summary: "1 completed · 1 total",
          child_job_count: 0,
          details: {},
        },
        {
          job_id: "job_reindex_1",
          root_job_id: "job_reindex_1",
          job_type: "reindex_embeddings",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 2,
          priority: 0,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:00:00Z",
          is_synthetic: false,
          tracking_available: false,
          indexed_paper_count: null,
          latest_status_summary: "completed",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });

    try {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
      });

      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      await waitFor(() => {
        expect(screen.getByTitle("Env scheduled · latest sync")).toBeTruthy();
      });
      expect(
        within(screen.getByRole("table")).getByRole("columnheader", { name: "Trigger" }),
      ).toBeTruthy();
      expect(screen.getByTitle("On-demand request · date range")).toBeTruthy();
      expect(screen.getByTitle("System generated · reindex embeddings")).toBeTruthy();
    } finally {
      dateNowSpy.mockRestore();
    }
  });

  it("keeps zero-valued admin job timelines fixed with denser grid lines", async () => {
    const dateNowSpy = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-04-21T12:00:00Z").getTime());
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "job_zero",
          root_job_id: "job_zero",
          job_type: "harvest_range",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T01:00:00Z",
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 0,
          paper_outcome_counts: { indexed: 0, skipped: 0, failed: 0 },
          latest_status_summary: "1 completed · 1 total",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });

    try {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
      });

      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      await waitFor(() => {
        expect(screen.getByTestId("admin-jobs-chart-inner")).toBeTruthy();
      });
      const chart = screen.getByTestId("admin-jobs-chart-inner");
      expect(chart.getAttribute("data-y-max")).toBe("8");
      expect(chart.getAttribute("data-y-split-number")).toBe("8");
      expect(screen.queryByTestId("admin-jobs-chart-skeleton")).toBeNull();
    } finally {
      dateNowSpy.mockRestore();
    }
  });

  it("filters admin jobs by preset and custom time windows", async () => {
    const dateNowSpy = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-04-21T12:00:00Z").getTime());
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "job_recent",
          root_job_id: "job_recent",
          job_type: "index_paper",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: "2026-04-21T11:00:00Z",
          updated_at: "2026-04-21T11:30:00Z",
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 3,
          latest_status_summary: "completed",
          child_job_count: 0,
          details: {},
        },
        {
          job_id: "job_old",
          root_job_id: "job_old",
          job_type: "harvest_range",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: "2026-04-15T11:00:00Z",
          updated_at: "2026-04-15T12:00:00Z",
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 12,
          latest_status_summary: "completed",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });

    try {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
      });

      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
      });

      expect(screen.getByText("job_recent")).toBeTruthy();
      expect(screen.getByText("job_old")).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Jobs window" }));
      fireEvent.click(screen.getByRole("option", { name: "Last hour" }));

      await waitFor(() => {
        expect(screen.getByText("job_recent")).toBeTruthy();
      });
      expect(screen.queryByText("job_old")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Jobs window" }));
      fireEvent.click(screen.getByRole("option", { name: "Custom range" }));
      fireEvent.change(screen.getByLabelText("Custom range start"), {
        target: { value: "2026-04-15T00:00" },
      });
      fireEvent.change(screen.getByLabelText("Custom range end"), {
        target: { value: "2026-04-15T23:59" },
      });

      await waitFor(() => {
        expect(screen.getByText("job_old")).toBeTruthy();
      });
      expect(screen.queryByText("job_recent")).toBeNull();
    } finally {
      dateNowSpy.mockRestore();
    }
  });

  it("auto-refreshes admin jobs using the selected refresh interval", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [],
      worker: null,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
    });

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
      await act(async () => {
        await Promise.resolve();
      });
      expect(apiMocks.loadBrowserAdminJobsOverview).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByRole("button", { name: "Jobs refresh interval" }));
      expect(screen.getByRole("option", { name: "5 seconds" })).toBeTruthy();
      expect(screen.getByRole("option", { name: "30 seconds" })).toBeTruthy();
      expect(screen.getByRole("option", { name: "1 min" })).toBeTruthy();
      fireEvent.click(screen.getByRole("option", { name: "5 seconds" }));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4999);
      });
      expect(apiMocks.loadBrowserAdminJobsOverview).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(apiMocks.loadBrowserAdminJobsOverview).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("paginates indexed papers in the admin drilldown", async () => {
    const dateNowSpy = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-05-04T12:00:00Z").getTime());
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "job_root_1",
          root_job_id: "job_root_1",
          job_type: "harvest_range",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: "2026-05-04T00:00:00Z",
          updated_at: "2026-05-04T01:00:00Z",
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 140,
          paper_outcome_counts: { indexed: 140 },
          latest_status_summary: "completed",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });
    apiMocks.loadBrowserAdminJobPapers
      .mockResolvedValueOnce({
        root_job_id: "job_root_1",
        total_count: 140,
        offset: 0,
        limit: 100,
        has_more: true,
        papers: [
          {
            root_job_id: "job_root_1",
            job_id: "job_chunk_1",
            paper_id: "arxiv:2401.00001",
            arxiv_id: "2401.00001",
            title: "Page One Paper",
            outcome: "indexed",
            created_at: "2026-04-21T00:00:00Z",
            updated_at: "2026-04-21T01:10:00Z",
            paper: null,
          },
        ],
      })
      .mockResolvedValueOnce({
        root_job_id: "job_root_1",
        total_count: 140,
        offset: 100,
        limit: 100,
        has_more: false,
        papers: [
          {
            root_job_id: "job_root_1",
            job_id: "job_chunk_2",
            paper_id: "arxiv:2401.00002",
            arxiv_id: "2401.00002",
            title: "Page Two Paper",
            outcome: "indexed",
            created_at: "2026-04-21T00:00:00Z",
            updated_at: "2026-04-21T02:10:00Z",
            paper: null,
          },
        ],
      });

    try {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
      });

      fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
      });

      const indexedPapersButton = await within(screen.getByRole("table")).findByRole("button", {
        name: "140",
      });
      fireEvent.click(indexedPapersButton);

      await waitFor(() => {
        expect(screen.getByRole("heading", { name: "Papers in job_root_1" })).toBeTruthy();
      });
      expect(apiMocks.loadBrowserAdminJobPapers).toHaveBeenNthCalledWith(1, "job_root_1", 0, 100);
      expect(screen.getByText("Page One Paper")).toBeTruthy();
      expect(screen.getAllByText("1-1 of 140").length).toBeGreaterThan(0);

      fireEvent.click(screen.getByRole("button", { name: "Next page" }));

      await waitFor(() => {
        expect(apiMocks.loadBrowserAdminJobPapers).toHaveBeenNthCalledWith(
          2,
          "job_root_1",
          100,
          100,
        );
      });
      await waitFor(() => {
        expect(screen.getByText("Page Two Paper")).toBeTruthy();
      });
      expect(screen.getAllByText("101-101 of 140").length).toBeGreaterThan(0);
    } finally {
      dateNowSpy.mockRestore();
    }
  });

  it("shows arXiv links and raw asset previews in the admin paper modal", async () => {
    const adminJobCreatedAt = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const adminJobUpdatedAt = new Date(Date.now() - 30 * 60 * 1000).toISOString();
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        {
          job_id: "job_root_modal",
          root_job_id: "job_root_modal",
          job_type: "harvest_range",
          status: "completed",
          display_status: "completed",
          payload: {},
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          created_at: adminJobCreatedAt,
          updated_at: adminJobUpdatedAt,
          is_synthetic: false,
          tracking_available: true,
          indexed_paper_count: 1,
          paper_outcome_counts: { indexed: 1 },
          latest_status_summary: "completed",
          child_job_count: 0,
          details: {},
        },
      ],
      worker: null,
    });
    apiMocks.loadBrowserAdminJobPapers.mockResolvedValue({
      root_job_id: "job_root_modal",
      total_count: 1,
      offset: 0,
      limit: 100,
      has_more: false,
      papers: [
        {
          root_job_id: "job_root_modal",
          job_id: "job_chunk_modal",
          paper_id: "arxiv:2401.00001",
          arxiv_id: "2401.00001",
          title: "Modal Paper",
          outcome: "indexed",
          created_at: adminJobCreatedAt,
          updated_at: adminJobUpdatedAt,
          paper: null,
        },
      ],
    });
    const modalPaper = {
      paper_id: "arxiv:2401.00001",
      arxiv_id: "2401.00001",
      title: "Modal Paper",
      authors: "Modal Author",
      abstract: "Modal abstract",
      summary_short: "Modal summary",
      categories: "cs.AI",
      primary_category: "cs.AI",
      published: "2026-05-01",
      updated: "2026-05-02",
      pdf_url: "https://example.test/2401.00001.pdf",
      html_url: "https://arxiv.org/abs/2401.00001",
      citation_count: 12,
      influential_citation_count: 3,
      code_url: "https://github.com/example/modal-paper",
      code_index_status: "indexed",
      index_status: "indexed",
    };
    apiMocks.getBrowserAdminPaperDetail.mockResolvedValue({
      paper: modalPaper,
      jobs: [
        {
          root_job_id: "job_root_modal",
          job_id: "job_chunk_modal",
          job_type: "harvest_chunk",
          status: "completed",
          display_status: "completed",
          payload: { arxiv_id: "2401.00001" },
          outcome: "indexed",
          attempts: 1,
          max_attempts: 3,
          priority: 10,
          record_created_at: adminJobCreatedAt,
          record_updated_at: adminJobUpdatedAt,
          created_at: adminJobCreatedAt,
          updated_at: adminJobUpdatedAt,
        },
      ],
      files: [
        {
          path: "preview/first-page-top.webp",
          size: 1234,
          content_type: "image/webp",
        },
      ],
      code_files: {
        paper_id: "arxiv:2401.00001",
        eligibility: { eligible: true, status: "indexed" },
        resolved_sha: "abc123",
        files: [{ repo_path: "README.md", size_bytes: 42, indexed: true }],
      },
    });
    apiMocks.getBrowserPaperPreview.mockResolvedValue({
      paper_id: "arxiv:2401.00001",
      image_url: "https://example.test/preview.webp",
      content_type: "image/webp",
    });
    apiMocks.getBrowserAdminPaperAsset.mockResolvedValue({
      paper_id: "arxiv:2401.00001",
      path: "preview/first-page-top.webp",
      url: "https://example.test/preview.webp",
      content_type: "image/webp",
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(await within(screen.getByRole("table")).findByRole("button", { name: "1" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Papers in job_root_modal" })).toBeTruthy();
    });

    fireEvent.click(screen.getByText("Modal Paper"));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Modal Paper" })).toBeTruthy();
    });

    expect(screen.getByRole("link", { name: "Open arXiv 2401.00001" }).getAttribute("href")).toBe(
      "https://arxiv.org/abs/2401.00001",
    );
    expect(screen.getByText("Modal summary")).toBeTruthy();
    expect(screen.getByText("https://github.com/example/modal-paper")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Jobs" }));
    expect(screen.getByText("Harvest chunk")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "File structure" }));

    await waitFor(() => {
      expect(apiMocks.getBrowserAdminPaperDetail).toHaveBeenCalledWith("arxiv:2401.00001");
    });

    const previewFolderButton = screen.getByRole("button", { name: "preview" });
    expect(previewFolderButton).toBeTruthy();
    expect(screen.queryByRole("button", { name: /first-page-top\.webp/i })).toBeNull();

    fireEvent.click(previewFolderButton);
    fireEvent.click(screen.getByRole("button", { name: /first-page-top\.webp/i }));

    await waitFor(() => {
      expect(apiMocks.getBrowserAdminPaperAsset).toHaveBeenCalledWith(
        "arxiv:2401.00001",
        "preview/first-page-top.webp",
      );
    });

    expect(screen.getByRole("link", { name: "Open raw file" }).getAttribute("href")).toBe(
      "/v1/browser/admin/papers/arxiv%3A2401.00001/asset-content/preview/first-page-top.webp",
    );
    expect(screen.getByAltText("preview/first-page-top.webp").getAttribute("src")).toBe(
      "/v1/browser/admin/papers/arxiv%3A2401.00001/asset-content/preview/first-page-top.webp",
    );
  });

  it("lets admins switch job types from the create-job modal before queueing", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create job" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Create job" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Single paper/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Change type" })).toBeTruthy();
    });

    const actionButtons = Array.from(
      document.querySelectorAll(".admin-create-job-actions button"),
    ).map((button) => button.textContent);
    expect(actionButtons).toEqual(["Change type", "Queue job"]);

    fireEvent.click(screen.getByRole("button", { name: "Change type" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Single paper/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Single paper/i }));
    fireEvent.change(screen.getByLabelText("arXiv ID"), {
      target: { value: "2401.12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Queue job" }));

    await waitFor(() => {
      expect(apiMocks.queueBrowserAdminPaperIndex).toHaveBeenCalledWith("2401.12345", false);
    });
  });

  it("prevents duplicate create-job submissions while queueing", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));
    let resolveQueue: (value: unknown) => void = () => {};
    apiMocks.queueBrowserAdminPaperIndex.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveQueue = resolve;
        }),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    fireEvent.click(screen.getByRole("button", { name: /Single paper/i }));
    fireEvent.change(screen.getByLabelText("arXiv ID"), {
      target: { value: "2401.12345" },
    });

    const form = document.querySelector(".admin-create-job-form") as HTMLFormElement;
    fireEvent.submit(form);
    fireEvent.submit(form);

    await waitFor(() => {
      expect(apiMocks.queueBrowserAdminPaperIndex).toHaveBeenCalledTimes(1);
    });
    expect(form.getAttribute("aria-busy")).toBe("true");
    expect((screen.getByRole("button", { name: "Queuing..." }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    await act(async () => {
      resolveQueue({
        job_id: "job_admin_1",
        job_type: "index_paper",
        status: "pending",
        payload: {},
        attempts: 0,
        max_attempts: 3,
        priority: -10,
        created_at: "2026-04-21T00:00:00Z",
        updated_at: "2026-04-21T00:00:00Z",
      });
    });

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Queuing..." })).toBeNull();
    });
  });

  it("shows field help for create-job forms and falls back to optional defaults", async () => {
    apiMocks.loadSession.mockResolvedValue(authenticatedSession({ is_admin: true }));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Control panel" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Control panel" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    fireEvent.click(screen.getByRole("button", { name: /Single paper/i }));

    const arxivIdInput = screen.getByLabelText("arXiv ID") as HTMLInputElement;
    expect(arxivIdInput.required).toBe(true);
    expect(screen.getByLabelText("Explain arXiv ID")).toBeTruthy();
    expect(
      screen.getByText(
        "The identifier for the single paper you want the worker to fetch and index. Required.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Re-run the indexing pipeline even if this paper already exists in storage. Optional. Default: Off, so existing papers are left alone.",
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Change type" }));
    fireEvent.click(screen.getByRole("button", { name: /Date range/i }));

    expect((screen.getByLabelText("Categories") as HTMLTextAreaElement).required).toBe(false);
    expect((screen.getByLabelText("Start date") as HTMLInputElement).required).toBe(true);
    expect((screen.getByLabelText("End date") as HTMLInputElement).required).toBe(true);
    expect(
      screen.getByText(
        "The arXiv subjects to scan, like cs.LG or cs.CL, so the backfill stays in the area you care about. Optional. Default: The server's configured AI/ML arXiv categories.",
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Change type" }));
    fireEvent.click(screen.getByRole("button", { name: /Venue year/i }));

    expect((screen.getByLabelText("Venue") as HTMLInputElement).required).toBe(true);
    expect((screen.getByLabelText("Year") as HTMLInputElement).required).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Change type" }));
    fireEvent.click(screen.getByRole("button", { name: /Top cited/i }));

    fireEvent.change(screen.getByLabelText("Minimum citations"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Chunk size"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Queue job" }));

    await waitFor(() => {
      expect(apiMocks.queueBrowserAdminHarvestTopCited).toHaveBeenCalledWith({
        min_citations: 100,
        max_papers: null,
        chunk_size: 50,
      });
    });
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
      expect(document.body.classList.contains("authenticated-theme-dark")).toBe(true);
    });
    await waitFor(() => {
      expect(document.querySelector("#auth-modal")?.classList.contains("hidden")).toBe(true);
    });
    expect(document.querySelector("#auth-modal.hidden")).toBeTruthy();
  });

  it("sets explicit autocomplete hints on auth and settings key inputs", async () => {
    apiMocks.loadSession.mockResolvedValueOnce({
      authenticated: false,
      user: null,
      has_anthropic_api_key: false,
      settings: {
        openai_api_key_hint: "",
        anthropic_api_key_hint: "",
        has_elevenlabs_api_key: false,
        elevenlabs_api_key_hint: "",
        has_modal_credentials: false,
        modal_token_id_hint: "",
        has_github_connection: false,
        github_login: "",
        github_oauth_available: false,
        github_manage_access_url: "https://github.com/settings/connections/applications",
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
      document.querySelector("#login-form input[name='password']")?.getAttribute("minlength"),
    ).toBe("8");
    expect(
      document
        .querySelector("#signup-form input[name='display_name']")
        ?.getAttribute("autocomplete"),
    ).toBe("name");
    expect(
      document.querySelector("#signup-form input[name='password']")?.getAttribute("autocomplete"),
    ).toBe("new-password");
    expect(
      document.querySelector("#signup-form input[name='password']")?.getAttribute("minlength"),
    ).toBe("8");
    expect(
      document.querySelector("#reset-form input[name='token']")?.getAttribute("autocomplete"),
    ).toBe("one-time-code");
    expect(
      document.querySelector("#reset-form input[name='token']")?.getAttribute("minlength"),
    ).toBe("8");
    expect(
      document.querySelector("#reset-form input[name='password']")?.getAttribute("autocomplete"),
    ).toBe("new-password");
    expect(
      document.querySelector("#reset-form input[name='password']")?.getAttribute("minlength"),
    ).toBe("8");

    unmount();

    apiMocks.loadSession.mockResolvedValueOnce(
      authenticatedSession({ has_openai_api_key: false, has_anthropic_api_key: false }),
    );

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

    fireEvent.click(screen.getByRole("tab", { name: "Audio" }));

    await waitFor(() => {
      expect(document.querySelector("#settings-elevenlabs-form")).toBeTruthy();
    });

    expect(
      document
        .querySelector("#settings-elevenlabs-form input[name='api_key']")
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
