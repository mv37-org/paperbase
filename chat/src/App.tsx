import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ChangeEvent, UIEvent } from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";

import {
  ApiError,
  api,
  createPythonFile,
  createProject,
  createThread,
  deleteProject,
  deleteThread,
  getBrowserPaper,
  getBrowserPaperFigure,
  getBrowserPaperFile,
  getBrowserPaperTable,
  getProjectRepoSync,
  getPythonFile,
  githubLoginUrl,
  getThread,
  googleLoginUrl,
  loadProjectItems,
  loadProjects,
  loadSession,
  loadUploads,
  moveThreadToProject,
  logoutBrowserSession,
  postForm,
  runPythonFile,
  sendThreadMessage,
  sendThreadMessageStream,
  suggestBrowserPapers,
  StreamingChatAbortError,
  StreamingChatError,
  syncProjectRepo,
  updateProject,
  updatePythonFile,
  uploadDocument,
} from "./api";
import { LandingMentionEditor } from "./LandingMentionEditor";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type {
  AuthView,
  BrowserSessionResponse,
  ChatCitation,
  ChatMessageResponse,
  ChatStreamDoneEvent,
  ChatStreamUsageEvent,
  ChatThreadResponse,
  ChatThreadSummary,
  FigureResponse,
  FileContentResponse,
  PaperResponse,
  ProjectRepoConflictDetail,
  ProjectRepoConflictFile,
  ProjectRepoConflictResolution,
  ProjectItemSummary,
  ProjectRepoSyncResponse,
  ProjectSummary,
  PythonFileResponse,
  RunPythonFileResponse,
  SendChatMessageResponse,
  TableResponse,
} from "./types";

const OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
const SIDEBAR_COLLAPSED_KEY = "paperbase-sidebar-collapsed";
const MAX_OPEN_TABS = 8;
const MESSAGE_LIST_BOTTOM_THRESHOLD = 40;
type ModalFlashKind = "error" | "note";
type PaperDrawerState = { paperId: string; path: string } | null;
type PaperDrawerContent =
  | { kind: "markdown"; path: string; file: FileContentResponse }
  | { kind: "figure"; path: string; figure: FigureResponse }
  | { kind: "table"; path: string; table: TableResponse };
type OpenTab = { kind: "thread"; id: string } | { kind: "python_file"; id: string };
type ProjectItemState = {
  items: ProjectItemSummary[];
  totalCount: number;
  hasMore: boolean;
  loaded: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string;
};
type PythonFileDraft = {
  content: string;
  dirty: boolean;
};
type PythonFileRunState = {
  running: boolean;
  result: RunPythonFileResponse | null;
  error: string;
};
type SaveIntent = "save" | "run";
type SaveModalState = {
  fileId: string;
  commitMessage: string;
  saving: boolean;
  error: string;
  repoSync: ProjectRepoSyncResponse | null;
  success: ProjectRepoSyncResponse | null;
} | null;
type SyncConflictState = {
  fileId: string;
  projectId: string;
  commitMessage: string;
  expectedRemoteHeadSha: string;
  repoName: string;
  repoUrl: string;
  defaultBranch: string;
  baseCommitSha: string;
  conflicts: ProjectRepoConflictFile[];
  selectedPath: string;
  resolutions: Record<string, string>;
  afterSaveAction: SaveIntent;
  saving: boolean;
  error: string;
} | null;
type ProjectModalState = { mode: "create" } | { mode: "edit"; projectId: string };
type DeleteModalState =
  | { kind: "project"; projectId: string; name: string }
  | { kind: "thread"; projectId: string; threadId: string; title: string }
  | null;
type ToolActivityEntry = {
  id: string;
  turnIndex: number;
  label: string;
  status: "running" | "completed";
  startedAt: string;
  finishedAt: string | null;
  isError: boolean;
};
type ThreadStreamState = {
  turnIndex: number;
  thinkingWord: string;
  assistantStarted: boolean;
  usage: ChatStreamUsageEvent | null;
};

const AUTH_VIEW_COPY: Record<AuthView, { title: string; caption: string }> = {
  login: { title: "Log in", caption: "Use your account to continue." },
  signup: { title: "Create account", caption: "Create your account." },
  "reset-request": { title: "Forgot password", caption: "Get reset link." },
  "reset-password": { title: "Reset password", caption: "Choose a new password for your account." },
  settings: {
    title: "Settings",
    caption: "Manage inference, sandbox, repos, and appearance.",
  },
};

const DEFAULT_BROWSER_SETTINGS: NonNullable<BrowserSessionResponse["settings"]> = {
  anthropic_api_key_hint: "",
  has_modal_credentials: false,
  modal_token_id_hint: "",
  has_github_connection: false,
  github_login: "",
  github_oauth_available: false,
  weekly_papers_review_email: false,
  appearance_theme: "light",
};

type AppearanceTheme = "light" | "dark";
type SettingsTab = "inference" | "sandbox" | "version-control" | "appearance";

const SETTINGS_TABS: Array<{ id: SettingsTab; label: string; caption: string }> = [
  {
    id: "inference",
    label: "Inference",
    caption: "Manage Anthropic inference API key.",
  },
  {
    id: "sandbox",
    label: "Sandbox",
    caption: "Manage Modal sandbox credentials.",
  },
  {
    id: "version-control",
    label: "Version control",
    caption: "Manage GitHub connection for repositories.",
  },
  {
    id: "appearance",
    label: "Appearance",
    caption: "Choose the theme for signed-in pages.",
  },
];

const normalizeSettingsTab = (value: string | null): SettingsTab | null => {
  if (
    value === "inference" ||
    value === "sandbox" ||
    value === "version-control" ||
    value === "appearance"
  ) {
    return value;
  }
  return null;
};

const APPEARANCE_OPTIONS: Array<{
  id: AppearanceTheme;
  label: string;
  caption: string;
}> = [
  {
    id: "light",
    label: "Light",
    caption: "Keeps the signed-in workspace bright and minimal.",
  },
  {
    id: "dark",
    label: "Dark",
    caption: "Uses a higher-contrast dark workspace across the app shell.",
  },
];

const AUTHENTICATED_DARK_THEME_CLASS = "authenticated-theme-dark";

function SettingsSectionHeading({ title, caption }: { title: string; caption: string }) {
  return (
    <div className="settings-section-heading">
      <h3 className="settings-section-title">{title}</h3>
      <p className="settings-section-copy">{caption}</p>
    </div>
  );
}

function ModalFlash({
  message,
  kind,
  className = "",
}: {
  message: string;
  kind: ModalFlashKind;
  className?: string;
}) {
  return (
    <div
      id="modal-flash"
      className={`modal-flash${kind === "error" ? " warning" : " note"}${className ? ` ${className}` : ""}`}
      role={kind === "error" ? "alert" : "status"}
    >
      {kind === "error" ? (
        <span className="modal-flash-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 4 3.75 18h16.5L12 4Z" />
            <path d="M12 9v4" />
            <path d="M12 16.5h.01" />
          </svg>
        </span>
      ) : null}
      <span className="modal-flash-text">{message}</span>
    </div>
  );
}

const EXAMPLES = [
  {
    label: "Reliable multi-step agents",
    description:
      "See which papers actually support planning, reflection, and recovery patterns in real agent loops.",
    prompt:
      "What patterns are emerging for reliable multi-step agents, and which papers actually validate them?",
  },
  {
    label: "Practical agent eval stack",
    description:
      "Compare evaluation approaches and translate them into a lightweight stack for a product team.",
    prompt:
      "Compare recent approaches to agent evals and recommend a practical stack for a small product team.",
  },
  {
    label: "Retrieval, memory, and tool use",
    description:
      "Survey how current systems combine context retrieval, memory, and tools in production settings.",
    prompt:
      "Summarize recent research on retrieval, memory, and tool use for production AI systems.",
  },
];

const THINKING_WORDS = [
  "Thinking",
  "Pondering",
  "Brainstorming",
  "Reasoning",
  "Considering",
  "Exploring",
  "Reflecting",
  "Analyzing",
  "Evaluating",
  "Inspecting",
  "Tracing",
  "Mapping",
  "Digging",
  "Unpacking",
  "Drafting",
  "Refining",
  "Weighing",
  "Composing",
  "Calculating",
  "Synthesizing",
];

function staticAssetPath(name: string) {
  return `${import.meta.env.BASE_URL}${name.replace(/^\/+/, "")}`;
}

function pythonRepoPathForFilename(filename: string) {
  return `src/${filename}`;
}

function pythonFilenameFromRepoPath(path: string) {
  return path.startsWith("src/") ? path.slice("src/".length) : null;
}

function projectRepoConflictFromError(error: unknown): ProjectRepoConflictDetail | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  const payload = error.payload;
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return null;
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const conflict = detail as Partial<ProjectRepoConflictDetail>;
  if (conflict.code !== "github_merge_conflict" || !Array.isArray(conflict.conflicts)) {
    return null;
  }
  return conflict as ProjectRepoConflictDetail;
}

function createLocalMessage(
  role: string,
  content_markdown: string,
  options: Partial<Pick<ChatMessageResponse, "citations" | "status" | "message_id">> = {},
): ChatMessageResponse {
  return {
    message_id: options.message_id ?? `local-${role}-${Math.random().toString(36).slice(2)}`,
    role,
    content_markdown,
    citations: options.citations ?? [],
    created_at: new Date().toISOString(),
    status: options.status ?? "completed",
  };
}

function drawerStateFromUrl(): PaperDrawerState {
  const params = new URLSearchParams(window.location.search);
  const paperId = params.get("paper");
  const path = params.get("paperPath");
  if (!paperId || !path) {
    return null;
  }
  return { paperId, path };
}

function updateDrawerUrl(nextState: PaperDrawerState) {
  const params = new URLSearchParams(window.location.search);
  if (nextState) {
    params.set("paper", nextState.paperId);
    params.set("paperPath", nextState.path);
  } else {
    params.delete("paper");
    params.delete("paperPath");
  }
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.pushState({}, "", nextUrl);
}

function inferDrawerContentKind(path: string) {
  const figureMatch = path.match(/^figures\/([^/]+)\/caption\.md$/);
  if (figureMatch) {
    return { kind: "figure" as const, id: figureMatch[1] };
  }
  const tableMatch = path.match(/^tables\/([^/.]+)\.json$/);
  if (tableMatch) {
    return { kind: "table" as const, id: tableMatch[1] };
  }
  return { kind: "markdown" as const };
}

function sourceCardId(messageId: string, sourceId: string) {
  return `source-card-${messageId}-${sourceId}`;
}

function consumePendingPrompt() {
  const prompt = sessionStorage.getItem(PENDING_PROMPT_KEY);
  sessionStorage.removeItem(PENDING_PROMPT_KEY);
  return prompt ?? "";
}

function rememberPendingPrompt(prompt: string) {
  if (prompt.trim()) {
    sessionStorage.setItem(PENDING_PROMPT_KEY, prompt.trim());
  }
}

function restoreTabs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(OPEN_TABS_KEY) ?? "[]");
    if (!Array.isArray(parsed)) {
      return [];
    }
    const restored: OpenTab[] = [];
    for (const value of parsed) {
      if (typeof value === "string") {
        restored.push({ kind: "thread", id: value });
        continue;
      }
      if (
        value &&
        typeof value === "object" &&
        "kind" in value &&
        "id" in value &&
        (value.kind === "thread" || value.kind === "python_file") &&
        typeof value.id === "string"
      ) {
        restored.push({ kind: value.kind, id: value.id });
      }
    }
    return restored.slice(-MAX_OPEN_TABS);
  } catch {
    return [];
  }
}

function appendOpenTab(current: OpenTab[], tab: OpenTab) {
  if (current.some((item) => item.kind === tab.kind && item.id === tab.id)) {
    return current;
  }
  return [...current, tab].slice(-MAX_OPEN_TABS);
}

function isMessageListNearBottom(
  element: HTMLDivElement,
  threshold = MESSAGE_LIST_BOTTOM_THRESHOLD,
) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= threshold;
}

function restoreSidebarCollapsed() {
  return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
}

function displayThreadTitle(title: string) {
  return title.trim() || "New chat";
}

function truncateTabTitle(title: string) {
  const words = displayThreadTitle(title).split(/\s+/).filter(Boolean);
  if (words.length <= 4) {
    return words.join(" ");
  }
  return `${words.slice(0, 4).join(" ")}…`;
}

function formatTodayTimeLabel(value: string) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return null;
  }
  const now = new Date();
  const isToday =
    timestamp.getFullYear() === now.getFullYear() &&
    timestamp.getMonth() === now.getMonth() &&
    timestamp.getDate() === now.getDate();
  if (!isToday) {
    return null;
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(timestamp);
}

function pickThinkingWord(previous?: string | null) {
  if (THINKING_WORDS.length === 0) {
    return "Thinking";
  }
  if (THINKING_WORDS.length === 1) {
    return THINKING_WORDS[0]!;
  }
  let next = THINKING_WORDS[Math.floor(Math.random() * THINKING_WORDS.length)] ?? "Thinking";
  if (previous && next === previous) {
    const currentIndex = THINKING_WORDS.indexOf(next);
    next = THINKING_WORDS[(currentIndex + 1) % THINKING_WORDS.length] ?? next;
  }
  return next;
}

function formatThinkingLabel(word?: string | null) {
  const trimmed = word?.trim();
  const label = trimmed ? trimmed.replace(/\.+$/, "") : "Thinking";
  return `${label}...`;
}

function formatTokenCount(value: number) {
  return new Intl.NumberFormat("en-US").format(Math.max(0, Math.round(value)));
}

function formatCompactTokenCount(value: number) {
  const rounded = Math.max(0, Math.round(value));
  if (rounded < 1000) {
    return formatTokenCount(rounded);
  }
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: rounded < 100_000 ? 1 : 0,
  })
    .format(rounded)
    .replace(/[A-Z]/g, (match) => match.toLowerCase());
}

function contextUsagePercent(usage: ChatStreamUsageEvent) {
  if (usage.context_window_tokens <= 0) {
    return 0;
  }
  return Math.min(100, (usage.total_tokens / usage.context_window_tokens) * 100);
}

function formatContextUsagePercentValue(usage: ChatStreamUsageEvent) {
  const usagePercent = contextUsagePercent(usage);
  if (usagePercent > 0 && usagePercent < 1) {
    return "<1";
  }
  return String(Math.round(usagePercent));
}

function formatContextUsageRemainingPercentValue(usage: ChatStreamUsageEvent) {
  const usagePercent = contextUsagePercent(usage);
  const roundedUsed = Math.round(usagePercent);
  const remaining = Math.max(0, 100 - roundedUsed);
  if (remaining === 0 && usagePercent < 100) {
    return "<1";
  }
  return String(remaining);
}

function formatContextUsageSummary(usage: ChatStreamUsageEvent) {
  return `${formatContextUsagePercentValue(usage)}% used (${formatContextUsageRemainingPercentValue(usage)}% left)`;
}

function formatContextUsageTokenSummary(usage: ChatStreamUsageEvent) {
  return `${formatCompactTokenCount(usage.total_tokens)} / ${formatCompactTokenCount(usage.context_window_tokens)} tokens used`;
}

function contextUsageButtonLabel(usage: ChatStreamUsageEvent) {
  return `Context window: ${formatContextUsageSummary(usage)}, ${formatTokenCount(usage.total_tokens)} of ${formatTokenCount(usage.context_window_tokens)} tokens used`;
}

function apiKeyStateMessage(state: string) {
  if (state === "api_key_invalid") {
    return "Your saved Anthropic API key could not be decrypted. Add it again in Settings.";
  }
  return "You have used your 3 free responses. Add an Anthropic API key to continue.";
}

function shouldShowThreadInSidebar(thread: ChatThreadSummary) {
  return thread.title.trim().length > 0 || thread.last_message_preview.trim().length > 0;
}

function isEmptyThread(thread: ChatThreadResponse | null | undefined) {
  return Boolean(thread) && thread.messages.length === 0;
}

function formatThreadAge(value: string) {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "";
  }
  const deltaMs = Math.max(0, Date.now() - timestamp);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const week = 7 * day;
  const month = 30 * day;
  const year = 365 * day;

  if (deltaMs < hour) {
    return `${Math.max(1, Math.floor(deltaMs / minute))}m`;
  }
  if (deltaMs < day) {
    return `${Math.floor(deltaMs / hour)}h`;
  }
  if (deltaMs < 14 * day) {
    return `${Math.floor(deltaMs / day)}d`;
  }
  if (deltaMs < 8 * week) {
    return `${Math.floor(deltaMs / week)}w`;
  }
  if (deltaMs < year) {
    return `${Math.floor(deltaMs / month)}mo`;
  }
  return `${Math.floor(deltaMs / year)}y`;
}

function threadSummaryFromThread(thread: ChatThreadResponse): ChatThreadSummary {
  const lastMessage = thread.messages[thread.messages.length - 1];
  return {
    thread_id: thread.thread_id,
    project_id: thread.project_id,
    title: thread.title,
    created_at: thread.created_at,
    updated_at: thread.updated_at,
    last_message_preview: lastMessage?.content_markdown.slice(0, 120) ?? "",
  };
}

function projectItemFromThread(thread: ChatThreadResponse): ProjectItemSummary {
  const summary = threadSummaryFromThread(thread);
  return {
    item_id: summary.thread_id,
    item_kind: "thread",
    project_id: summary.project_id,
    title: summary.title,
    created_at: summary.created_at,
    updated_at: summary.updated_at,
    thread_id: summary.thread_id,
    file_id: null,
    last_message_preview: summary.last_message_preview,
    filename: "",
  };
}

function projectItemFromPythonFile(file: PythonFileResponse): ProjectItemSummary {
  return {
    item_id: file.file_id,
    item_kind: "python_file",
    project_id: file.project_id,
    title: file.filename,
    created_at: file.created_at,
    updated_at: file.updated_at,
    thread_id: null,
    file_id: file.file_id,
    last_message_preview: "",
    filename: file.filename,
  };
}

function upsertProjectItem(
  current: ProjectItemSummary[],
  item: ProjectItemSummary,
): ProjectItemSummary[] {
  return [item, ...current.filter((entry) => entry.item_id !== item.item_id)].sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  );
}

function displayPythonFilename(filename: string) {
  return filename.trim() || "something.py";
}

function GoogleButton() {
  return (
    <button
      className="oauth-button"
      data-google-login="true"
      type="button"
      onClick={() => (window.location.href = googleLoginUrl())}
    >
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path
          fill="#4285F4"
          d="M17.64 9.2c0-.63-.06-1.24-.16-1.82H9v3.44h4.84a4.14 4.14 0 0 1-1.8 2.71v2.25h2.9c1.7-1.57 2.7-3.88 2.7-6.58Z"
        />
        <path
          fill="#34A853"
          d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.25c-.8.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.96v2.32A9 9 0 0 0 9 18Z"
        />
        <path
          fill="#FBBC05"
          d="M3.95 10.71A5.4 5.4 0 0 1 3.67 9c0-.6.1-1.19.28-1.71V4.97H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.03l2.99-2.32Z"
        />
        <path
          fill="#EA4335"
          d="M9 3.58c1.32 0 2.5.45 3.43 1.33l2.57-2.57C13.46.9 11.42 0 9 0A9 9 0 0 0 .96 4.97l2.99 2.32C4.66 5.17 6.65 3.58 9 3.58Z"
        />
      </svg>
      <span>Continue with Google</span>
    </button>
  );
}

function AppIcon({ src, className = "" }: { src: string; className?: string }) {
  return (
    <img
      src={src}
      alt=""
      aria-hidden="true"
      className={className ? `icon-image ${className}` : "icon-image"}
    />
  );
}

function MaskedAssetIcon({ asset, className }: { asset: string; className: string }) {
  return (
    <span
      className={className}
      aria-hidden="true"
      style={
        {
          "--icon-url": `url("${staticAssetPath(asset)}")`,
        } as CSSProperties
      }
    />
  );
}

function EmptyStateActionIcon({ asset }: { asset: string }) {
  return <MaskedAssetIcon asset={asset} className="chat-empty-action-icon-glyph" />;
}

export default function App() {
  const [session, setSession] = useState<BrowserSessionResponse | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectItems, setProjectItems] = useState<Record<string, ProjectItemState>>({});
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [expandedProjectIds, setExpandedProjectIds] = useState<string[]>([]);
  const [threadMap, setThreadMap] = useState<Record<string, ChatThreadResponse>>({});
  const [pythonFileMap, setPythonFileMap] = useState<Record<string, PythonFileResponse>>({});
  const [pythonFileDrafts, setPythonFileDrafts] = useState<Record<string, PythonFileDraft>>({});
  const [pythonFileRuns, setPythonFileRuns] = useState<Record<string, PythonFileRunState>>({});
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([]);
  const [activeTab, setActiveTab] = useState<OpenTab | null>(null);
  const [projectMenuProjectId, setProjectMenuProjectId] = useState<string | null>(null);
  const [projectModalState, setProjectModalState] = useState<ProjectModalState | null>(null);
  const [projectFormName, setProjectFormName] = useState("");
  const [projectFormDescription, setProjectFormDescription] = useState("");
  const [projectFormError, setProjectFormError] = useState("");
  const [deleteModalState, setDeleteModalState] = useState<DeleteModalState>(null);
  const [saveModalState, setSaveModalState] = useState<SaveModalState>(null);
  const [syncConflictState, setSyncConflictState] = useState<SyncConflictState>(null);
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTab>("inference");
  const [appearanceThemeDraft, setAppearanceThemeDraft] = useState<AppearanceTheme>("light");
  const [flash, setFlash] = useState("");
  const [flashKind, setFlashKind] = useState<ModalFlashKind>("note");
  const [landingPrompt, setLandingPrompt] = useState("");
  const [composerInput, setComposerInput] = useState("");
  const [composerStatus, setComposerStatus] = useState("");
  const [includeUploads] = useState(true);
  const [includePapers] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [emptyStateProjectPickerOpen, setEmptyStateProjectPickerOpen] = useState(false);
  const [emptyStateProjectSearch, setEmptyStateProjectSearch] = useState("");
  const [streamingThreadId, setStreamingThreadId] = useState<string | null>(null);
  const [threadStreamStates, setThreadStreamStates] = useState<Record<string, ThreadStreamState>>(
    {},
  );
  const [threadUsageSnapshots, setThreadUsageSnapshots] = useState<
    Record<string, ChatStreamUsageEvent>
  >({});
  const [toolActivities, setToolActivities] = useState<Record<string, ToolActivityEntry[]>>({});
  const [contextUsagePopoverOpen, setContextUsagePopoverOpen] = useState(false);
  const [paperDrawer, setPaperDrawer] = useState<PaperDrawerState>(() => drawerStateFromUrl());
  const [paperDrawerPaper, setPaperDrawerPaper] = useState<PaperResponse | null>(null);
  const [paperDrawerContent, setPaperDrawerContent] = useState<PaperDrawerContent | null>(null);
  const [paperDrawerStatus, setPaperDrawerStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [paperDrawerError, setPaperDrawerError] = useState("");
  const [messageListAtBottom, setMessageListAtBottom] = useState(true);

  const sessionRef = useRef<BrowserSessionResponse | null>(null);
  const projectsRef = useRef<ProjectSummary[]>([]);
  const selectedProjectIdRef = useRef<string | null>(null);
  const projectItemsRef = useRef<Record<string, ProjectItemState>>({});
  const threadMapRef = useRef<Record<string, ChatThreadResponse>>({});
  const pythonFileMapRef = useRef<Record<string, PythonFileResponse>>({});
  const pythonFileDraftsRef = useRef<Record<string, PythonFileDraft>>({});
  const tabsStorageHydratedRef = useRef(false);
  const appUploadRef = useRef<HTMLInputElement | null>(null);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const contextUsageMenuRef = useRef<HTMLDivElement | null>(null);
  const emptyStateProjectPickerRef = useRef<HTMLDivElement | null>(null);
  const emptyStateProjectSearchInputRef = useRef<HTMLInputElement | null>(null);
  const projectMenuRef = useRef<HTMLDivElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const streamAbortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    threadMapRef.current = threadMap;
  }, [threadMap]);

  useEffect(() => {
    projectsRef.current = projects;
  }, [projects]);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProjectId;
  }, [selectedProjectId]);

  useEffect(() => {
    projectItemsRef.current = projectItems;
  }, [projectItems]);

  useEffect(() => {
    pythonFileMapRef.current = pythonFileMap;
  }, [pythonFileMap]);

  useEffect(() => {
    pythonFileDraftsRef.current = pythonFileDrafts;
  }, [pythonFileDrafts]);

  useEffect(
    () => () => {
      streamAbortControllerRef.current?.abort();
      streamAbortControllerRef.current = null;
    },
    [],
  );

  useEffect(() => {
    if (!tabsStorageHydratedRef.current) {
      tabsStorageHydratedRef.current = true;
      if (openTabs.length === 0) {
        return;
      }
    }
    localStorage.setItem(OPEN_TABS_KEY, JSON.stringify(openTabs));
  }, [openTabs]);

  useEffect(() => {
    setSidebarCollapsed(restoreSidebarCollapsed());
  }, []);

  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (!mobileSidebarOpen) {
      document.body.style.overflow = "";
      return;
    }
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileSidebarOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [mobileSidebarOpen]);

  useEffect(() => {
    if (!accountMenuOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAccountMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [accountMenuOpen]);

  useEffect(() => {
    if (!projectMenuProjectId) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (projectMenuRef.current && !projectMenuRef.current.contains(event.target as Node)) {
        setProjectMenuProjectId(null);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setProjectMenuProjectId(null);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [projectMenuProjectId]);

  useEffect(() => {
    if (!emptyStateProjectPickerOpen) {
      setEmptyStateProjectSearch("");
      return;
    }
    window.requestAnimationFrame(() => {
      emptyStateProjectSearchInputRef.current?.focus();
    });
    const handlePointerDown = (event: MouseEvent) => {
      if (
        emptyStateProjectPickerRef.current &&
        !emptyStateProjectPickerRef.current.contains(event.target as Node)
      ) {
        setEmptyStateProjectPickerOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setEmptyStateProjectPickerOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [emptyStateProjectPickerOpen]);

  const authCopy = AUTH_VIEW_COPY[authView];
  const authenticated = Boolean(session?.authenticated);
  const activeThreadId = activeTab?.kind === "thread" ? activeTab.id : null;
  const activePythonFileId = activeTab?.kind === "python_file" ? activeTab.id : null;
  const activeThread = activeThreadId ? (threadMap[activeThreadId] ?? null) : null;
  const activePythonFile = activePythonFileId ? (pythonFileMap[activePythonFileId] ?? null) : null;
  const activePythonDraft = activePythonFileId
    ? (pythonFileDrafts[activePythonFileId] ?? null)
    : null;
  const activePythonRun = activePythonFileId
    ? (pythonFileRuns[activePythonFileId] ?? { running: false, result: null, error: "" })
    : { running: false, result: null, error: "" };
  const activePythonContent = activePythonDraft?.content ?? activePythonFile?.content ?? "";
  const selectedSyncConflict = syncConflictState
    ? (syncConflictState.conflicts.find(
        (conflict) => conflict.path === syncConflictState.selectedPath,
      ) ??
      syncConflictState.conflicts[0] ??
      null)
    : null;
  const selectedSyncConflictResolution = selectedSyncConflict
    ? (syncConflictState?.resolutions[selectedSyncConflict.path] ??
      selectedSyncConflict.local_content)
    : "";
  const isChatEmpty =
    activeTab == null ||
    (activeTab.kind === "thread" && (!activeThread || activeThread.messages.length === 0));
  const paperDrawerOpen = Boolean(paperDrawer);
  const emptyStateProject =
    projects.find((project) => project.project_id === selectedProjectId) ?? projects[0] ?? null;
  const filteredEmptyStateProjects = useMemo(() => {
    const query = emptyStateProjectSearch.trim().toLowerCase();
    if (!query) {
      return projects;
    }
    return projects.filter((project) => {
      const searchableText = `${project.name} ${project.description}`.toLowerCase();
      return searchableText.includes(query);
    });
  }, [emptyStateProjectSearch, projects]);
  const allProjectsExpanded = useMemo(
    () =>
      projects.length > 0 &&
      projects.every((project) => expandedProjectIds.includes(project.project_id)),
    [expandedProjectIds, projects],
  );
  const openTabItems = useMemo(
    () =>
      openTabs.map((tab) => {
        if (tab.kind === "thread") {
          const loadedThread = threadMap[tab.id];
          const title = loadedThread?.title ?? "New chat";
          return {
            key: `thread:${tab.id}`,
            kind: tab.kind,
            id: tab.id,
            title: displayThreadTitle(title),
            tabTitle: truncateTabTitle(title),
          };
        }
        const loadedFile = pythonFileMap[tab.id];
        const title = displayPythonFilename(loadedFile?.filename ?? "something.py");
        return {
          key: `python_file:${tab.id}`,
          kind: tab.kind,
          id: tab.id,
          title,
          tabTitle: truncateTabTitle(title),
        };
      }),
    [openTabs, pythonFileMap, threadMap],
  );
  const hasOpenTabs = openTabItems.length > 0;
  const tabStripStyle = useMemo(
    () =>
      ({
        "--chat-tab-count": Math.max(1, openTabItems.length),
      }) as CSSProperties,
    [openTabItems.length],
  );
  const activeThreadStreamState = activeThreadId
    ? (threadStreamStates[activeThreadId] ?? null)
    : null;
  const activeStreamUsage = activeThreadStreamState?.usage ?? null;
  const activePersistedUsage = activeThreadId
    ? (threadUsageSnapshots[activeThreadId] ?? null)
    : null;
  const activeContextUsage = activeStreamUsage ?? activePersistedUsage;
  const activeThreadToolActivities = useMemo(
    () =>
      activeThreadId
        ? [...(toolActivities[activeThreadId] ?? [])].sort((left, right) =>
            left.turnIndex === right.turnIndex
              ? Date.parse(left.startedAt) - Date.parse(right.startedAt)
              : left.turnIndex - right.turnIndex,
          )
        : [],
    [activeThreadId, toolActivities],
  );
  const activeThreadToolActivitiesByTurn = useMemo(() => {
    const grouped = new Map<number, ToolActivityEntry[]>();
    for (const activity of activeThreadToolActivities) {
      const existing = grouped.get(activity.turnIndex) ?? [];
      existing.push(activity);
      grouped.set(activity.turnIndex, existing);
    }
    return grouped;
  }, [activeThreadToolActivities]);
  const activeStreamInView = streamingThreadId !== null && streamingThreadId === activeThreadId;
  const showScrollToLatest = activeTab?.kind === "thread" && !isChatEmpty && !messageListAtBottom;
  const activeContextUsagePercent = activeContextUsage
    ? contextUsagePercent(activeContextUsage)
    : 0;

  useEffect(() => {
    if (!contextUsagePopoverOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (
        contextUsageMenuRef.current &&
        !contextUsageMenuRef.current.contains(event.target as Node)
      ) {
        setContextUsagePopoverOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setContextUsagePopoverOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [contextUsagePopoverOpen]);

  useEffect(() => {
    setContextUsagePopoverOpen(false);
  }, [activeThreadId]);

  useEffect(() => {
    if (!activeContextUsage) {
      setContextUsagePopoverOpen(false);
    }
  }, [activeContextUsage]);

  const scrollMessageListToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    if (!messageListRef.current) {
      return;
    }
    messageListRef.current.scrollTo({ top: messageListRef.current.scrollHeight, behavior });
    setMessageListAtBottom(true);
  }, []);

  const handleMessageListScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    setMessageListAtBottom(isMessageListNearBottom(event.currentTarget));
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setPaperDrawer(drawerStateFromUrl());
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (!authenticated || !paperDrawer) {
      setPaperDrawerPaper(null);
      setPaperDrawerContent(null);
      setPaperDrawerError("");
      setPaperDrawerStatus("idle");
      return;
    }

    let cancelled = false;
    setPaperDrawerStatus("loading");
    setPaperDrawerError("");

    void (async () => {
      try {
        const paper = await getBrowserPaper(paperDrawer.paperId);
        if (cancelled) {
          return;
        }
        setPaperDrawerPaper(paper);

        const target = inferDrawerContentKind(paperDrawer.path);
        if (target.kind === "figure") {
          const figure = await getBrowserPaperFigure(paperDrawer.paperId, target.id);
          if (cancelled) {
            return;
          }
          setPaperDrawerContent({ kind: "figure", path: paperDrawer.path, figure });
          setPaperDrawerStatus("ready");
          return;
        }

        if (target.kind === "table") {
          const table = await getBrowserPaperTable(paperDrawer.paperId, target.id);
          if (cancelled) {
            return;
          }
          setPaperDrawerContent({ kind: "table", path: paperDrawer.path, table });
          setPaperDrawerStatus("ready");
          return;
        }

        const file = await getBrowserPaperFile(paperDrawer.paperId, paperDrawer.path);
        if (cancelled) {
          return;
        }
        setPaperDrawerContent({ kind: "markdown", path: paperDrawer.path, file });
        setPaperDrawerStatus("ready");
      } catch (error) {
        if (cancelled) {
          return;
        }
        setPaperDrawerStatus("error");
        setPaperDrawerError(
          error instanceof Error ? error.message : "Paper content could not be loaded.",
        );
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [authenticated, paperDrawer]);

  useEffect(() => {
    if (!messageListRef.current) {
      return;
    }
    setMessageListAtBottom(true);
    requestAnimationFrame(() => {
      scrollMessageListToBottom();
    });
  }, [activeThreadId, scrollMessageListToBottom]);

  useEffect(() => {
    if (!activeThread || !messageListAtBottom) {
      return;
    }
    requestAnimationFrame(() => {
      scrollMessageListToBottom();
    });
  }, [activeThread, messageListAtBottom, scrollMessageListToBottom]);

  const sessionSummary = useMemo(() => {
    if (!session?.user) {
      return null;
    }
    return {
      email: session.user.email,
      hasAnthropicApiKey: session.has_anthropic_api_key,
      settings: session.settings ?? DEFAULT_BROWSER_SETTINGS,
    };
  }, [session]);
  const sessionSettings = sessionSummary?.settings ?? DEFAULT_BROWSER_SETTINGS;
  const appliedAppearanceTheme = sessionSettings.appearance_theme === "dark" ? "dark" : "light";
  const authenticatedDarkThemeEnabled = authenticated && appliedAppearanceTheme === "dark";
  const activeSettingsConfig = useMemo(
    () => SETTINGS_TABS.find((tab) => tab.id === activeSettingsTab) ?? SETTINGS_TABS[0],
    [activeSettingsTab],
  );
  const authCaption = authView === "settings" ? activeSettingsConfig.caption : authCopy.caption;

  useEffect(() => {
    setAppearanceThemeDraft(appliedAppearanceTheme);
  }, [appliedAppearanceTheme]);

  useEffect(() => {
    document.documentElement.classList.toggle(
      AUTHENTICATED_DARK_THEME_CLASS,
      authenticatedDarkThemeEnabled,
    );
    document.body.classList.toggle(AUTHENTICATED_DARK_THEME_CLASS, authenticatedDarkThemeEnabled);
    return () => {
      document.documentElement.classList.remove(AUTHENTICATED_DARK_THEME_CLASS);
      document.body.classList.remove(AUTHENTICATED_DARK_THEME_CLASS);
    };
  }, [authenticatedDarkThemeEnabled]);

  const clearFlash = useCallback(() => {
    setFlash("");
    setFlashKind("note");
  }, []);

  const showErrorFlash = useCallback((message: string) => {
    setFlash(message);
    setFlashKind("error");
  }, []);

  const showNoteFlash = useCallback((message: string) => {
    setFlash(message);
    setFlashKind("note");
  }, []);

  const closeAuthModal = useCallback(() => {
    setAuthModalOpen(false);
    clearFlash();
  }, [clearFlash]);

  const closePaperDrawer = useCallback(() => {
    setPaperDrawer(null);
    updateDrawerUrl(null);
  }, []);

  const openPaperDrawer = useCallback((paperId: string, path: string) => {
    const nextState = { paperId, path };
    setPaperDrawer(nextState);
    updateDrawerUrl(nextState);
  }, []);

  const updateThread = useCallback(
    (threadId: string, updater: (thread: ChatThreadResponse | null) => ChatThreadResponse) => {
      setThreadMap((current) => ({
        ...current,
        [threadId]: updater(current[threadId] ?? null),
      }));
    },
    [],
  );

  const startThreadStreamState = useCallback((threadId: string, turnIndex: number) => {
    setThreadStreamStates((current) => ({
      ...current,
      [threadId]: {
        turnIndex,
        thinkingWord: pickThinkingWord(current[threadId]?.thinkingWord ?? null),
        assistantStarted: false,
        usage: null,
      },
    }));
  }, []);

  const markThreadAssistantStarted = useCallback((threadId: string) => {
    setThreadStreamStates((current) => {
      const existing = current[threadId];
      if (!existing) {
        return current;
      }
      return {
        ...current,
        [threadId]: {
          ...existing,
          assistantStarted: true,
        },
      };
    });
  }, []);

  const updateThreadStreamUsage = useCallback((threadId: string, usage: ChatStreamUsageEvent) => {
    setThreadStreamStates((current) => {
      const existing = current[threadId];
      if (!existing) {
        return current;
      }
      return {
        ...current,
        [threadId]: {
          ...existing,
          usage,
        },
      };
    });
  }, []);

  const setThreadUsageSnapshot = useCallback((threadId: string, usage: ChatStreamUsageEvent) => {
    setThreadUsageSnapshots((current) => ({
      ...current,
      [threadId]: usage,
    }));
  }, []);

  const clearThreadUsageSnapshot = useCallback((threadId: string) => {
    setThreadUsageSnapshots((current) => {
      if (!(threadId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[threadId];
      return next;
    });
  }, []);

  const clearThreadStreamState = useCallback((threadId: string) => {
    setThreadStreamStates((current) => {
      if (!(threadId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[threadId];
      return next;
    });
  }, []);

  const upsertToolActivity = useCallback((threadId: string, nextEntry: ToolActivityEntry) => {
    setToolActivities((current) => {
      const existingEntries = current[threadId] ?? [];
      const nextEntries = existingEntries.some((entry) => entry.id === nextEntry.id)
        ? existingEntries.map((entry) => (entry.id === nextEntry.id ? nextEntry : entry))
        : [...existingEntries, nextEntry];
      return {
        ...current,
        [threadId]: nextEntries,
      };
    });
  }, []);

  const completeToolActivity = useCallback(
    (
      threadId: string,
      toolId: string,
      updates: Partial<Pick<ToolActivityEntry, "finishedAt" | "isError">> = {},
    ) => {
      setToolActivities((current) => {
        const existingEntries = current[threadId] ?? [];
        if (!existingEntries.some((entry) => entry.id === toolId)) {
          return current;
        }
        return {
          ...current,
          [threadId]: existingEntries.map((entry) =>
            entry.id === toolId
              ? {
                  ...entry,
                  status: "completed",
                  finishedAt: updates.finishedAt ?? new Date().toISOString(),
                  isError: updates.isError ?? entry.isError,
                }
              : entry,
          ),
        };
      });
    },
    [],
  );

  const completeRunningToolActivitiesForTurn = useCallback(
    (threadId: string, turnIndex: number) => {
      setToolActivities((current) => {
        const existingEntries = current[threadId] ?? [];
        if (
          !existingEntries.some(
            (entry) => entry.turnIndex === turnIndex && entry.status === "running",
          )
        ) {
          return current;
        }
        return {
          ...current,
          [threadId]: existingEntries.map((entry) =>
            entry.turnIndex === turnIndex && entry.status === "running"
              ? {
                  ...entry,
                  status: "completed",
                  finishedAt: new Date().toISOString(),
                }
              : entry,
          ),
        };
      });
    },
    [],
  );

  const handleCitationClick = useCallback(
    (message: ChatMessageResponse, citation: ChatCitation) => {
      document
        .getElementById(sourceCardId(message.message_id, citation.source_id))
        ?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      if (citation.paper_id) {
        openPaperDrawer(citation.paper_id, citation.path);
      }
    },
    [openPaperDrawer],
  );

  const refreshUploads = useCallback(async () => {
    if (!sessionRef.current?.authenticated) {
      return;
    }
    await loadUploads();
  }, []);

  const refreshProjects = useCallback(async () => {
    const payload = await loadProjects();
    const loadedProjects = payload.projects;
    projectsRef.current = loadedProjects;
    const fallbackProjectId = loadedProjects[0]?.project_id ?? null;
    const immediateProjectId =
      selectedProjectIdRef.current &&
      loadedProjects.some((project) => project.project_id === selectedProjectIdRef.current)
        ? selectedProjectIdRef.current
        : fallbackProjectId;
    selectedProjectIdRef.current = immediateProjectId;
    setProjects(loadedProjects);
    setSelectedProjectId((current) => {
      if (current && loadedProjects.some((project) => project.project_id === current)) {
        return current;
      }
      return fallbackProjectId;
    });
    return loadedProjects;
  }, []);

  const updateProjectItemCache = useCallback(
    (projectId: string, updater: (current: ProjectItemState | null) => ProjectItemState | null) => {
      setProjectItems((current) => {
        const nextValue = updater(current[projectId] ?? null);
        if (nextValue === null) {
          const nextState = { ...current };
          delete nextState[projectId];
          return nextState;
        }
        return {
          ...current,
          [projectId]: nextValue,
        };
      });
    },
    [],
  );

  const removeThreadIdsLocally = useCallback(
    (threadIds: string[], projectId?: string) => {
      const ids = new Set(threadIds);
      if (ids.size === 0) {
        return;
      }
      setThreadMap((current) => {
        const next = { ...current };
        for (const threadId of ids) {
          delete next[threadId];
        }
        return next;
      });
      setThreadStreamStates((current) => {
        const next = { ...current };
        for (const threadId of ids) {
          delete next[threadId];
        }
        return next;
      });
      setToolActivities((current) => {
        const next = { ...current };
        for (const threadId of ids) {
          delete next[threadId];
        }
        return next;
      });
      setOpenTabs((current) => {
        const nextTabs = current.filter((value) => value.kind !== "thread" || !ids.has(value.id));
        setActiveTab((currentActive) => {
          if (!currentActive || currentActive.kind !== "thread" || !ids.has(currentActive.id)) {
            return currentActive;
          }
          return nextTabs[nextTabs.length - 1] ?? null;
        });
        return nextTabs;
      });
      if (projectId) {
        updateProjectItemCache(projectId, (current) => {
          if (!current) {
            return current;
          }
          const nextItems = current.items.filter(
            (item) => item.item_kind !== "thread" || !ids.has(item.thread_id ?? ""),
          );
          const removedCount = current.items.length - nextItems.length;
          return {
            ...current,
            items: nextItems,
            totalCount: Math.max(0, current.totalCount - removedCount),
            hasMore:
              current.hasMore || nextItems.length < Math.max(0, current.totalCount - removedCount),
          };
        });
      }
    },
    [updateProjectItemCache],
  );

  const fetchProjectItems = useCallback(
    async (projectId: string, append = false) => {
      const existing = projectItemsRef.current[projectId];
      const offset = append ? (existing?.items.length ?? 0) : 0;
      updateProjectItemCache(projectId, (current) => ({
        items: current?.items ?? [],
        totalCount: current?.totalCount ?? 0,
        hasMore: current?.hasMore ?? false,
        loaded: current?.loaded ?? false,
        loading: append ? false : true,
        loadingMore: append,
        error: "",
      }));
      try {
        const page = await loadProjectItems(projectId, 25, offset);
        updateProjectItemCache(projectId, (current) => {
          const items = append
            ? [
                ...(current?.items ?? []),
                ...page.items.filter(
                  (item) => !(current?.items ?? []).some((entry) => entry.item_id === item.item_id),
                ),
              ]
            : page.items;
          return {
            items,
            totalCount: page.total_count,
            hasMore: page.has_more,
            loaded: true,
            loading: false,
            loadingMore: false,
            error: "",
          };
        });
        return page;
      } catch (error) {
        updateProjectItemCache(projectId, (current) => ({
          items: current?.items ?? [],
          totalCount: current?.totalCount ?? 0,
          hasMore: current?.hasMore ?? false,
          loaded: current?.loaded ?? false,
          loading: false,
          loadingMore: false,
          error: error instanceof Error ? error.message : "Project items could not be loaded.",
        }));
        throw error;
      }
    },
    [updateProjectItemCache],
  );

  const upsertThreadIntoProjectItems = useCallback(
    (thread: ChatThreadResponse) => {
      updateProjectItemCache(thread.project_id, (current) => {
        if (!current?.loaded) {
          return current;
        }
        const items = upsertProjectItem(current.items, projectItemFromThread(thread));
        return {
          ...current,
          items,
          totalCount: Math.max(current.totalCount, items.length),
          hasMore: Math.max(current.totalCount, items.length) > items.length || current.hasMore,
        };
      });
    },
    [updateProjectItemCache],
  );

  const upsertPythonFileIntoProjectItems = useCallback(
    (file: PythonFileResponse) => {
      updateProjectItemCache(file.project_id, (current) => {
        if (!current?.loaded) {
          return current;
        }
        const items = upsertProjectItem(current.items, projectItemFromPythonFile(file));
        return {
          ...current,
          items,
          totalCount: Math.max(current.totalCount, items.length),
          hasMore: Math.max(current.totalCount, items.length) > items.length || current.hasMore,
        };
      });
    },
    [updateProjectItemCache],
  );

  const moveThreadBetweenProjectItems = useCallback(
    (thread: ChatThreadResponse, previousProjectId: string) => {
      if (previousProjectId && previousProjectId !== thread.project_id) {
        updateProjectItemCache(previousProjectId, (current) => {
          if (!current?.loaded) {
            return current;
          }
          const nextItems = current.items.filter((item) => item.thread_id !== thread.thread_id);
          const removed = nextItems.length !== current.items.length ? 1 : 0;
          return {
            ...current,
            items: nextItems,
            totalCount: Math.max(0, current.totalCount - removed),
            hasMore:
              current.hasMore || nextItems.length < Math.max(0, current.totalCount - removed),
          };
        });
      }
      updateProjectItemCache(thread.project_id, (current) => {
        if (!current?.loaded) {
          return current;
        }
        const existed = current.items.some((item) => item.thread_id === thread.thread_id);
        const items = upsertProjectItem(current.items, projectItemFromThread(thread));
        const totalCount = existed
          ? current.totalCount
          : Math.max(current.totalCount + 1, items.length);
        return {
          ...current,
          items,
          totalCount,
          hasMore: current.hasMore || totalCount > items.length,
        };
      });
    },
    [updateProjectItemCache],
  );

  const fetchAndStoreThread = useCallback(
    async (threadId: string) => {
      const thread = await getThread(threadId);
      setThreadMap((current) => ({ ...current, [thread.thread_id]: thread }));
      setSelectedProjectId(thread.project_id);
      upsertThreadIntoProjectItems(thread);
      return thread;
    },
    [upsertThreadIntoProjectItems],
  );

  const openThread = useCallback(
    async (threadId: string) => {
      const thread = threadMapRef.current[threadId] ?? (await fetchAndStoreThread(threadId));
      setOpenTabs((current) => appendOpenTab(current, { kind: "thread", id: threadId }));
      setActiveTab({ kind: "thread", id: threadId });
      setSelectedProjectId(thread.project_id);
    },
    [fetchAndStoreThread],
  );

  const fetchAndStorePythonFile = useCallback(
    async (fileId: string) => {
      const file = await getPythonFile(fileId);
      setPythonFileMap((current) => ({ ...current, [file.file_id]: file }));
      setSelectedProjectId(file.project_id);
      setPythonFileDrafts((current) => ({
        ...current,
        [file.file_id]: current[file.file_id] ?? { content: file.content, dirty: false },
      }));
      upsertPythonFileIntoProjectItems(file);
      return file;
    },
    [upsertPythonFileIntoProjectItems],
  );

  const openPythonFile = useCallback(
    async (fileId: string) => {
      const file = pythonFileMapRef.current[fileId] ?? (await fetchAndStorePythonFile(fileId));
      setOpenTabs((current) => appendOpenTab(current, { kind: "python_file", id: fileId }));
      setActiveTab({ kind: "python_file", id: fileId });
      setSelectedProjectId(file.project_id);
    },
    [fetchAndStorePythonFile],
  );

  const createNewThread = useCallback(
    async (title = "", explicitProjectId?: string) => {
      const projectId =
        explicitProjectId ?? selectedProjectIdRef.current ?? projectsRef.current[0]?.project_id;
      if (!projectId) {
        throw new Error("Create a project before starting a chat.");
      }
      const thread = await createThread(title, projectId);
      setThreadMap((current) => ({ ...current, [thread.thread_id]: thread }));
      setOpenTabs((current) => appendOpenTab(current, { kind: "thread", id: thread.thread_id }));
      setActiveTab({ kind: "thread", id: thread.thread_id });
      setSelectedProjectId(thread.project_id);
      upsertThreadIntoProjectItems(thread);
      await refreshProjects();
      return thread;
    },
    [refreshProjects, upsertThreadIntoProjectItems],
  );

  const closeTab = useCallback((tab: OpenTab) => {
    setOpenTabs((current) => {
      const nextTabs = current.filter((value) => value.kind !== tab.kind || value.id !== tab.id);
      setActiveTab((currentActive) => {
        if (!currentActive || currentActive.kind !== tab.kind || currentActive.id !== tab.id) {
          return currentActive;
        }
        const closingIndex = current.findIndex(
          (value) => value.kind === tab.kind && value.id === tab.id,
        );
        return nextTabs[Math.max(0, closingIndex - 1)] ?? nextTabs[closingIndex] ?? null;
      });
      return nextTabs;
    });
  }, []);

  const copyMessage = useCallback(async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
    } catch (error) {
      console.error(error);
    }
  }, []);

  const sendMessage = useCallback(
    async (threadId: string, content: string) => {
      const optimisticUserMessage = createLocalMessage("user", content);
      const now = new Date().toISOString();
      const fallbackThread = threadMapRef.current[threadId];
      const turnIndex =
        (fallbackThread?.messages.filter((message) => message.role === "user").length ?? 0) + 1;
      const fallbackProjectId =
        fallbackThread?.project_id ??
        selectedProjectIdRef.current ??
        projectsRef.current[0]?.project_id ??
        "";
      const fallbackProjectName =
        fallbackThread?.project_name ??
        projectsRef.current.find((project) => project.project_id === fallbackProjectId)?.name ??
        "";
      const fallbackProjectInstructions = fallbackThread?.project_instructions ?? "";
      let assistantStarted = false;
      let donePayload: ChatStreamDoneEvent | null = null;
      let gatingPayload: SendChatMessageResponse | null = null;
      let streamError: string | null = null;
      let streamingMessageId: string | null = null;
      const streamAbortController = new AbortController();

      updateThread(threadId, (thread) => ({
        thread_id: thread?.thread_id ?? threadId,
        project_id: thread?.project_id ?? fallbackProjectId,
        project_name: thread?.project_name ?? fallbackProjectName,
        project_instructions: thread?.project_instructions ?? fallbackProjectInstructions,
        title:
          (thread?.title ?? "New chat") === "New chat"
            ? content.slice(0, 60)
            : (thread?.title ?? "New chat"),
        created_at: thread?.created_at ?? now,
        updated_at: now,
        messages: [...(thread?.messages ?? []), optimisticUserMessage],
      }));

      streamAbortControllerRef.current = streamAbortController;
      setStreamingThreadId(threadId);
      clearThreadUsageSnapshot(threadId);
      setContextUsagePopoverOpen(false);
      startThreadStreamState(threadId, turnIndex);
      setComposerStatus("");

      try {
        await sendThreadMessageStream(
          threadId,
          {
            content,
            include_uploads: includeUploads,
            include_papers: includePapers,
          },
          (eventName, payload) => {
            if (eventName === "compaction_start") {
              updateThread(threadId, (thread) => ({
                ...(thread ?? {
                  thread_id: threadId,
                  project_id: fallbackProjectId,
                  project_name: fallbackProjectName,
                  project_instructions: fallbackProjectInstructions,
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: new Date().toISOString(),
                messages: (thread?.messages ?? []).some(
                  (message) => message.message_id === payload.compaction_message.message_id,
                )
                  ? (thread?.messages ?? []).map((message) =>
                      message.message_id === payload.compaction_message.message_id
                        ? payload.compaction_message
                        : message,
                    )
                  : [...(thread?.messages ?? []), payload.compaction_message],
              }));
              return;
            }

            if (eventName === "compaction_done") {
              updateThread(threadId, (thread) => ({
                ...(thread ?? {
                  thread_id: threadId,
                  project_id: fallbackProjectId,
                  project_name: fallbackProjectName,
                  project_instructions: fallbackProjectInstructions,
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: new Date().toISOString(),
                messages: payload.messages,
              }));
              return;
            }

            if (eventName === "tool_start") {
              upsertToolActivity(threadId, {
                id: payload.tool_call_id,
                turnIndex,
                label: payload.label,
                status: "running",
                startedAt: new Date().toISOString(),
                finishedAt: null,
                isError: false,
              });
              return;
            }

            if (eventName === "tool_done") {
              completeToolActivity(threadId, payload.tool_call_id, {
                isError: payload.is_error,
              });
              return;
            }

            if (eventName === "assistant_start") {
              assistantStarted = true;
              streamingMessageId = payload.assistant_message.message_id;
              markThreadAssistantStarted(threadId);
              updateThread(threadId, (thread) => ({
                ...(thread ?? {
                  thread_id: threadId,
                  project_id: fallbackProjectId,
                  project_name: fallbackProjectName,
                  project_instructions: fallbackProjectInstructions,
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: now,
                messages: [...(thread?.messages ?? []), payload.assistant_message],
              }));
              return;
            }

            if (eventName === "usage") {
              updateThreadStreamUsage(threadId, payload);
              setThreadUsageSnapshot(threadId, payload);
              return;
            }

            if (eventName === "delta") {
              updateThread(threadId, (thread) => ({
                ...(thread ?? {
                  thread_id: threadId,
                  project_id: fallbackProjectId,
                  project_name: fallbackProjectName,
                  project_instructions: fallbackProjectInstructions,
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: new Date().toISOString(),
                messages: (thread?.messages ?? []).map((message) =>
                  message.message_id === payload.message_id
                    ? {
                        ...message,
                        content_markdown: `${message.content_markdown}${payload.delta}`,
                      }
                    : message,
                ),
              }));
              return;
            }

            if (eventName === "done") {
              donePayload = payload;
              if (payload.usage) {
                updateThreadStreamUsage(threadId, payload.usage);
                setThreadUsageSnapshot(threadId, payload.usage);
              }
              updateThread(threadId, (thread) => ({
                ...(thread ?? {
                  thread_id: threadId,
                  project_id: fallbackProjectId,
                  project_name: fallbackProjectName,
                  project_instructions: fallbackProjectInstructions,
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: new Date().toISOString(),
                messages: (thread?.messages ?? []).map((message) =>
                  message.message_id === payload.assistant_message.message_id
                    ? payload.assistant_message
                    : message,
                ),
              }));
              return;
            }

            if (eventName === "api_key_required") {
              gatingPayload = payload;
              return;
            }

            if (eventName === "error") {
              streamError = payload.detail;
            }
          },
          { signal: streamAbortController.signal },
        );
      } catch (error) {
        if (error instanceof StreamingChatAbortError) {
          completeRunningToolActivitiesForTurn(threadId, turnIndex);
          if (streamingMessageId) {
            updateThread(threadId, (thread) => ({
              ...(thread ?? {
                thread_id: threadId,
                project_id: fallbackProjectId,
                project_name: fallbackProjectName,
                project_instructions: fallbackProjectInstructions,
                title: content.slice(0, 60),
                created_at: now,
                updated_at: now,
                messages: [],
              }),
              updated_at: new Date().toISOString(),
              messages: (thread?.messages ?? []).map((message) =>
                message.message_id === streamingMessageId
                  ? { ...message, status: "completed" }
                  : message,
              ),
            }));
          }
          setComposerStatus("");
          return null;
        }

        if (error instanceof StreamingChatError && error.canFallback && !assistantStarted) {
          const payload = await sendThreadMessage(threadId, {
            content,
            include_uploads: includeUploads,
            include_papers: includePapers,
          });
          setThreadMap((current) => ({ ...current, [threadId]: payload.thread }));
          setSelectedProjectId(payload.thread.project_id);
          upsertThreadIntoProjectItems(payload.thread);
          setSession((current) =>
            current
              ? {
                  ...current,
                  remaining_free_responses: payload.remaining_free_responses,
                  has_anthropic_api_key: payload.has_anthropic_api_key,
                }
              : current,
          );
          setComposerStatus("");
          await refreshProjects();
          if (payload.state === "api_key_required" || payload.state === "api_key_invalid") {
            showErrorFlash(apiKeyStateMessage(payload.state));
            setAuthView("settings");
            setAuthModalOpen(true);
          }
          return payload;
        }

        const detail = error instanceof Error ? error.message : "Streaming request failed.";
        if (streamingMessageId) {
          updateThread(threadId, (thread) => ({
            ...(thread ?? {
              thread_id: threadId,
              project_id: fallbackProjectId,
              project_name: fallbackProjectName,
              project_instructions: fallbackProjectInstructions,
              title: content.slice(0, 60),
              created_at: now,
              updated_at: now,
              messages: [],
            }),
            messages: (thread?.messages ?? []).map((message) =>
              message.message_id === streamingMessageId
                ? { ...message, status: "failed" }
                : message,
            ),
          }));
        }
        setComposerStatus(detail);
        throw error;
      } finally {
        clearThreadStreamState(threadId);
        if (streamAbortControllerRef.current === streamAbortController) {
          streamAbortControllerRef.current = null;
        }
        setStreamingThreadId((current) => (current === threadId ? null : current));
      }

      if (gatingPayload) {
        setThreadMap((current) => ({ ...current, [threadId]: gatingPayload.thread }));
        setSelectedProjectId(gatingPayload.thread.project_id);
        upsertThreadIntoProjectItems(gatingPayload.thread);
        setSession((current) =>
          current
            ? {
                ...current,
                remaining_free_responses: gatingPayload.remaining_free_responses,
                has_anthropic_api_key: gatingPayload.has_anthropic_api_key,
              }
            : current,
        );
        setComposerStatus("");
        await refreshProjects();
        showErrorFlash(apiKeyStateMessage(gatingPayload.state));
        setAuthView("settings");
        setAuthModalOpen(true);
        return gatingPayload;
      }

      if (streamError) {
        setComposerStatus(streamError);
        const refreshedThread = await fetchAndStoreThread(threadId);
        upsertThreadIntoProjectItems(refreshedThread);
        await refreshProjects();
        return null;
      }

      if (donePayload) {
        setSession((current) =>
          current
            ? {
                ...current,
                remaining_free_responses: donePayload.remaining_free_responses,
                has_anthropic_api_key: donePayload.has_anthropic_api_key,
              }
            : current,
        );
        setComposerStatus("");
        const refreshedThread = await fetchAndStoreThread(threadId);
        upsertThreadIntoProjectItems(refreshedThread);
        await refreshProjects();
        return donePayload;
      }

      setComposerStatus("");
      return null;
    },
    [
      clearThreadUsageSnapshot,
      clearThreadStreamState,
      completeRunningToolActivitiesForTurn,
      completeToolActivity,
      fetchAndStoreThread,
      includePapers,
      includeUploads,
      markThreadAssistantStarted,
      refreshProjects,
      setThreadUsageSnapshot,
      showErrorFlash,
      startThreadStreamState,
      updateThreadStreamUsage,
      upsertToolActivity,
      upsertThreadIntoProjectItems,
      updateThread,
    ],
  );

  const bootstrapAuthenticatedUi = useCallback(async () => {
    await refreshUploads();
    const loadedProjects = await refreshProjects();
    const restoredTabs = restoreTabs();
    const validRestoredTabs: OpenTab[] = [];
    for (const tab of restoredTabs) {
      try {
        if (tab.kind === "thread") {
          await fetchAndStoreThread(tab.id);
        } else {
          await fetchAndStorePythonFile(tab.id);
        }
        validRestoredTabs.push(tab);
      } catch (error) {
        console.error(error);
      }
    }
    setOpenTabs(validRestoredTabs);

    const pendingPrompt = consumePendingPrompt().trim();
    if (pendingPrompt) {
      const thread = await createNewThread("", loadedProjects[0]?.project_id);
      setLandingPrompt("");
      await sendMessage(thread.thread_id, pendingPrompt);
      return;
    }

    const firstOpen = validRestoredTabs[0];
    if (firstOpen) {
      setActiveTab(firstOpen);
      setSelectedProjectId(
        firstOpen.kind === "thread"
          ? (threadMapRef.current[firstOpen.id]?.project_id ??
              loadedProjects[0]?.project_id ??
              null)
          : (pythonFileMapRef.current[firstOpen.id]?.project_id ??
              loadedProjects[0]?.project_id ??
              null),
      );
    } else if (loadedProjects[0]) {
      setSelectedProjectId(loadedProjects[0].project_id);
    }
  }, [
    createNewThread,
    fetchAndStorePythonFile,
    fetchAndStoreThread,
    refreshProjects,
    refreshUploads,
    sendMessage,
  ]);

  useEffect(() => {
    void (async () => {
      const params = new URLSearchParams(window.location.search);
      if (params.get("reset_token")) {
        setAuthView("reset-password");
        setAuthModalOpen(true);
      }
      const authStatus = params.get("auth");
      const authError = params.get("auth_error");
      const settingsSection = normalizeSettingsTab(params.get("settings"));
      if (authStatus === "github_connected") {
        showNoteFlash("GitHub connected.");
        setActiveSettingsTab("version-control");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError === "github_not_configured") {
        showErrorFlash("GitHub OAuth is not configured yet.");
        setActiveSettingsTab("version-control");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError === "github_auth_required") {
        showErrorFlash("Log in again before connecting GitHub.");
        setActiveSettingsTab("version-control");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError === "github_callback_failed") {
        showErrorFlash("GitHub connection failed. Try again.");
        setActiveSettingsTab("version-control");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError) {
        showErrorFlash("Google login failed. Try email/password instead.");
        setAuthView("login");
        setAuthModalOpen(true);
      } else if (settingsSection) {
        setActiveSettingsTab(settingsSection);
        setAuthView("settings");
        setAuthModalOpen(true);
      }
      if (authStatus || authError || settingsSection) {
        params.delete("auth");
        params.delete("auth_error");
        params.delete("settings");
        const query = params.toString();
        const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
        window.history.replaceState({}, "", nextUrl);
      }
      const nextSession = await loadSession();
      setSession(nextSession);
      setAuthReady(true);
      if (nextSession.authenticated) {
        await bootstrapAuthenticatedUi();
      }
    })().catch((error: unknown) => {
      console.error(error);
      setComposerStatus("The chat app could not reach paperbase.");
      setAuthReady(true);
    });
  }, [bootstrapAuthenticatedUi, showErrorFlash, showNoteFlash]);

  const openAuthModal = useCallback(
    (view: AuthView, settingsTab: SettingsTab = "inference") => {
      clearFlash();
      if (view === "settings") {
        setActiveSettingsTab(settingsTab);
      }
      setAuthView(view);
      setAuthModalOpen(true);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
    },
    [clearFlash],
  );

  const handleAuthSuccess = useCallback(
    async (nextSession: BrowserSessionResponse) => {
      setSession(nextSession);
      closeAuthModal();
      await bootstrapAuthenticatedUi();
    },
    [bootstrapAuthenticatedUi, closeAuthModal],
  );

  const handleLandingSubmit = useCallback(async () => {
    const prompt = landingPrompt.trim();
    if (!prompt) {
      return;
    }
    if (!sessionRef.current?.authenticated) {
      rememberPendingPrompt(prompt);
      openAuthModal("login");
      return;
    }
    const thread = await createNewThread();
    setLandingPrompt("");
    await sendMessage(thread.thread_id, prompt);
  }, [createNewThread, landingPrompt, openAuthModal, sendMessage]);

  const handleExample = useCallback(
    async (prompt: string) => {
      setLandingPrompt(prompt);
      rememberPendingPrompt(prompt);
      if (!sessionRef.current?.authenticated) {
        openAuthModal("login");
      }
    },
    [openAuthModal],
  );

  const handleUpload = useCallback(
    async (file: File) => {
      await uploadDocument(file);
      await refreshUploads();
      setComposerStatus(`Attached ${file.name}`);
    },
    [refreshUploads],
  );

  const onUploadChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) {
        return;
      }
      try {
        await handleUpload(file);
      } catch (error) {
        setComposerStatus(error instanceof Error ? error.message : "Upload failed");
      } finally {
        event.target.value = "";
      }
    },
    [handleUpload],
  );

  const handleStopStreaming = useCallback(() => {
    streamAbortControllerRef.current?.abort();
  }, []);

  const handleComposerSubmit = useCallback(async () => {
    if (streamingThreadId) {
      return;
    }
    const content = composerInput.trim();
    if (!content) {
      return;
    }
    const threadId = activeThreadId ?? (await createNewThread()).thread_id;
    setComposerInput("");
    scrollMessageListToBottom();
    await sendMessage(threadId, content);
  }, [
    activeThreadId,
    composerInput,
    createNewThread,
    scrollMessageListToBottom,
    sendMessage,
    streamingThreadId,
  ]);

  const resetAuthenticatedState = useCallback(() => {
    setSession(null);
    setProjects([]);
    setProjectItems({});
    setSelectedProjectId(null);
    setExpandedProjectIds([]);
    setThreadMap({});
    setPythonFileMap({});
    setPythonFileDrafts({});
    setPythonFileRuns({});
    setOpenTabs([]);
    setActiveTab(null);
    setProjectMenuProjectId(null);
    setProjectModalState(null);
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
    setDeleteModalState(null);
    setSaveModalState(null);
    setSyncConflictState(null);
    setLandingPrompt("");
    setComposerInput("");
    setComposerStatus("");
    setMobileSidebarOpen(false);
    setAccountMenuOpen(false);
    setStreamingThreadId(null);
    setThreadStreamStates({});
    setThreadUsageSnapshots({});
    setContextUsagePopoverOpen(false);
    setToolActivities({});
    streamAbortControllerRef.current?.abort();
    streamAbortControllerRef.current = null;
    setPaperDrawer(null);
    setPaperDrawerPaper(null);
    setPaperDrawerContent(null);
    setPaperDrawerStatus("idle");
    setPaperDrawerError("");
    updateDrawerUrl(null);
  }, []);

  const handleSidebarToggle = useCallback(() => {
    setAccountMenuOpen(false);
    setProjectMenuProjectId(null);
    if (window.matchMedia("(max-width: 980px)").matches) {
      setMobileSidebarOpen((current) => !current);
      return;
    }
    setSidebarCollapsed((current) => !current);
  }, []);

  const handleThreadSelection = useCallback(
    async (threadId: string) => {
      await openThread(threadId);
      setMobileSidebarOpen(false);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
    },
    [openThread],
  );

  const handlePythonFileSelection = useCallback(
    async (fileId: string) => {
      await openPythonFile(fileId);
      setMobileSidebarOpen(false);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
    },
    [openPythonFile],
  );

  const handleNewChat = useCallback(async () => {
    setMobileSidebarOpen(false);
    setAccountMenuOpen(false);
    setProjectMenuProjectId(null);
    return createNewThread();
  }, [createNewThread]);

  const handleProjectSelection = useCallback(
    async (projectId: string) => {
      setSelectedProjectId(projectId);
      const expanded = expandedProjectIds.includes(projectId);
      if (expanded) {
        setExpandedProjectIds((current) => current.filter((value) => value !== projectId));
        return;
      }
      setExpandedProjectIds((current) => [...current, projectId]);
      if (
        !projectItemsRef.current[projectId]?.loaded &&
        !projectItemsRef.current[projectId]?.loading
      ) {
        await fetchProjectItems(projectId);
      }
    },
    [expandedProjectIds, fetchProjectItems],
  );

  const handleToggleAllProjects = useCallback(async () => {
    if (allProjectsExpanded) {
      setExpandedProjectIds([]);
      return;
    }
    const nextExpanded = projects.map((project) => project.project_id);
    setExpandedProjectIds(nextExpanded);
    await Promise.all(
      nextExpanded
        .filter((projectId) => !projectItemsRef.current[projectId]?.loaded)
        .map((projectId) => fetchProjectItems(projectId)),
    );
  }, [allProjectsExpanded, fetchProjectItems, projects]);

  const openCreateProjectModal = useCallback(() => {
    setProjectModalState({ mode: "create" });
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
    setProjectMenuProjectId(null);
    setEmptyStateProjectPickerOpen(false);
    setEmptyStateProjectSearch("");
  }, []);

  const openEditProjectModal = useCallback(
    (projectId: string) => {
      const project = projects.find((item) => item.project_id === projectId);
      if (!project) {
        return;
      }
      setProjectModalState({ mode: "edit", projectId });
      setProjectFormName(project.name);
      setProjectFormDescription(project.description);
      setProjectFormError("");
      setProjectMenuProjectId(null);
    },
    [projects],
  );

  const closeProjectModal = useCallback(() => {
    setProjectModalState(null);
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
  }, []);

  const submitProjectModal = useCallback(async () => {
    const name = projectFormName.trim();
    if (!name) {
      setProjectFormError("Project name is required.");
      return;
    }
    if (!projectModalState) {
      return;
    }
    try {
      if (projectModalState.mode === "create") {
        const project = await createProject(name, projectFormDescription.trim());
        await refreshProjects();
        setSelectedProjectId(project.project_id);
        setExpandedProjectIds((current) =>
          current.includes(project.project_id) ? current : [...current, project.project_id],
        );
        await fetchProjectItems(project.project_id);
      } else {
        await updateProject(projectModalState.projectId, name, projectFormDescription.trim());
        await refreshProjects();
      }
      closeProjectModal();
    } catch (error) {
      setProjectFormError(error instanceof Error ? error.message : "Project could not be saved.");
    }
  }, [
    closeProjectModal,
    fetchProjectItems,
    projectFormDescription,
    projectFormName,
    projectModalState,
    refreshProjects,
  ]);

  const openDeleteProjectModal = useCallback((projectId: string, name: string) => {
    setDeleteModalState({ kind: "project", projectId, name });
    setProjectMenuProjectId(null);
  }, []);

  const openDeleteThreadModal = useCallback(
    (projectId: string, threadId: string, title: string) => {
      setDeleteModalState({ kind: "thread", projectId, threadId, title });
    },
    [],
  );

  const closeDeleteModal = useCallback(() => {
    setDeleteModalState(null);
  }, []);

  const confirmDeleteModal = useCallback(async () => {
    if (!deleteModalState) {
      return;
    }
    try {
      if (deleteModalState.kind === "project") {
        const projectId = deleteModalState.projectId;
        const deletedThreadIds = Object.values(threadMapRef.current)
          .filter((thread) => thread.project_id === projectId)
          .map((thread) => thread.thread_id);
        const cachedItems = projectItemsRef.current[projectId]?.items ?? [];
        const cachedThreadIds = cachedItems
          .filter((item) => item.item_kind === "thread")
          .map((item) => item.thread_id ?? "");
        const deletedFileIds = Object.values(pythonFileMapRef.current)
          .filter((pythonFile) => pythonFile.project_id === projectId)
          .map((pythonFile) => pythonFile.file_id);
        const cachedFileIds = cachedItems
          .filter((item) => item.item_kind === "python_file")
          .map((item) => item.file_id ?? "");
        await deleteProject(projectId);
        removeThreadIdsLocally([...new Set([...deletedThreadIds, ...cachedThreadIds])], projectId);
        setPythonFileMap((current) => {
          const next = { ...current };
          for (const fileId of [...deletedFileIds, ...cachedFileIds]) {
            delete next[fileId];
          }
          return next;
        });
        setPythonFileDrafts((current) => {
          const next = { ...current };
          for (const fileId of [...deletedFileIds, ...cachedFileIds]) {
            delete next[fileId];
          }
          return next;
        });
        setPythonFileRuns((current) => {
          const next = { ...current };
          for (const fileId of [...deletedFileIds, ...cachedFileIds]) {
            delete next[fileId];
          }
          return next;
        });
        setOpenTabs((current) => {
          const ids = new Set([...deletedFileIds, ...cachedFileIds]);
          const nextTabs = current.filter((tab) => tab.kind !== "python_file" || !ids.has(tab.id));
          setActiveTab((currentActive) => {
            if (
              !currentActive ||
              currentActive.kind !== "python_file" ||
              !ids.has(currentActive.id)
            ) {
              return currentActive;
            }
            return nextTabs[nextTabs.length - 1] ?? null;
          });
          return nextTabs;
        });
        setProjectItems((current) => {
          const next = { ...current };
          delete next[projectId];
          return next;
        });
        setExpandedProjectIds((current) => current.filter((value) => value !== projectId));
        await refreshProjects();
      } else {
        await deleteThread(deleteModalState.threadId);
        removeThreadIdsLocally([deleteModalState.threadId], deleteModalState.projectId);
        await refreshProjects();
      }
      closeDeleteModal();
    } catch (error) {
      setComposerStatus(error instanceof Error ? error.message : "Delete failed.");
    }
  }, [closeDeleteModal, deleteModalState, refreshProjects, removeThreadIdsLocally]);

  const handleProjectStartChat = useCallback(
    async (projectId: string) => {
      setSelectedProjectId(projectId);
      setMobileSidebarOpen(false);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
      const existingEmptyThread = openTabs
        .filter((tab) => tab.kind === "thread")
        .map((tab) => threadMap[tab.id] ?? null)
        .find((thread) => isEmptyThread(thread) && thread.project_id === projectId);
      if (existingEmptyThread) {
        setActiveTab({ kind: "thread", id: existingEmptyThread.thread_id });
        return;
      }
      if (isEmptyThread(activeThread)) {
        if (activeThread.project_id === projectId) {
          return;
        }
        const movedThread = await moveThreadToProject(activeThread.thread_id, projectId);
        setThreadMap((current) => ({ ...current, [movedThread.thread_id]: movedThread }));
        moveThreadBetweenProjectItems(movedThread, activeThread.project_id);
        setActiveTab({ kind: "thread", id: movedThread.thread_id });
        await refreshProjects();
        return;
      }
      await createNewThread("", projectId);
    },
    [
      activeThread,
      createNewThread,
      moveThreadBetweenProjectItems,
      openTabs,
      refreshProjects,
      threadMap,
    ],
  );

  const handleLoadMoreItems = useCallback(
    async (projectId: string) => {
      await fetchProjectItems(projectId, true);
    },
    [fetchProjectItems],
  );

  const handleCreatePythonFile = useCallback(async () => {
    if (!sessionSettings.has_github_connection) {
      showErrorFlash("Connect GitHub before creating Python files.");
      openAuthModal("settings", "version-control");
      return;
    }
    const projectId = selectedProjectIdRef.current ?? projectsRef.current[0]?.project_id;
    if (!projectId) {
      setComposerStatus("Create a project before adding a Python file.");
      return;
    }
    try {
      const pythonFile = await createPythonFile(projectId);
      setPythonFileMap((current) => ({ ...current, [pythonFile.file_id]: pythonFile }));
      setPythonFileDrafts((current) => ({
        ...current,
        [pythonFile.file_id]: { content: pythonFile.content, dirty: false },
      }));
      setPythonFileRuns((current) => ({
        ...current,
        [pythonFile.file_id]: { running: false, result: null, error: "" },
      }));
      upsertPythonFileIntoProjectItems(pythonFile);
      setOpenTabs((current) =>
        appendOpenTab(current, { kind: "python_file", id: pythonFile.file_id }),
      );
      setActiveTab({ kind: "python_file", id: pythonFile.file_id });
      setSelectedProjectId(pythonFile.project_id);
      await refreshProjects();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Python file could not be created.";
      if (message.toLowerCase().includes("github")) {
        showErrorFlash(message);
        openAuthModal("settings", "version-control");
        return;
      }
      setComposerStatus(message);
    }
  }, [
    openAuthModal,
    refreshProjects,
    sessionSettings.has_github_connection,
    showErrorFlash,
    upsertPythonFileIntoProjectItems,
  ]);

  const persistPythonFile = useCallback(
    async (fileId: string, content: string) => {
      const updated = await updatePythonFile(fileId, { content });
      setPythonFileMap((current) => ({ ...current, [updated.file_id]: updated }));
      setPythonFileDrafts((current) => ({
        ...current,
        [updated.file_id]: { content: updated.content, dirty: false },
      }));
      upsertPythonFileIntoProjectItems(updated);
      await refreshProjects();
      return updated;
    },
    [refreshProjects, upsertPythonFileIntoProjectItems],
  );

  const applyConflictResolutionsLocally = useCallback(
    (projectId: string, resolutions: ProjectRepoConflictResolution[]) => {
      if (resolutions.length === 0) {
        return;
      }
      const resolutionsByPath = new Map(
        resolutions.map((resolution) => [resolution.path, resolution.content]),
      );
      setPythonFileMap((current) => {
        let changed = false;
        const next = { ...current };
        for (const pythonFile of Object.values(current)) {
          if (pythonFile.project_id !== projectId) {
            continue;
          }
          const resolvedContent = resolutionsByPath.get(
            pythonRepoPathForFilename(pythonFile.filename),
          );
          if (resolvedContent == null || resolvedContent === pythonFile.content) {
            continue;
          }
          changed = true;
          next[pythonFile.file_id] = {
            ...pythonFile,
            content: resolvedContent,
            updated_at: new Date().toISOString(),
          };
        }
        return changed ? next : current;
      });
      setPythonFileDrafts((current) => {
        let changed = false;
        const next = { ...current };
        for (const fileId of Object.keys(current)) {
          const pythonFile = pythonFileMapRef.current[fileId];
          if (!pythonFile || pythonFile.project_id !== projectId) {
            continue;
          }
          const resolvedContent = resolutionsByPath.get(
            pythonRepoPathForFilename(pythonFile.filename),
          );
          if (resolvedContent == null) {
            continue;
          }
          changed = true;
          next[fileId] = { content: resolvedContent, dirty: false };
          if (pythonFileMapRef.current[fileId]) {
            pythonFileMapRef.current[fileId] = {
              ...pythonFileMapRef.current[fileId],
              content: resolvedContent,
            };
          }
        }
        return changed ? next : current;
      });
    },
    [],
  );

  const syncProjectToGitHub = useCallback(
    async ({
      fileId,
      projectId,
      commitMessage,
      afterSaveAction,
      expectedRemoteHeadSha,
      conflictResolutions = [],
    }: {
      fileId: string;
      projectId: string;
      commitMessage: string;
      afterSaveAction: SaveIntent;
      expectedRemoteHeadSha?: string | null;
      conflictResolutions?: ProjectRepoConflictResolution[];
    }) => {
      try {
        const sync = await syncProjectRepo(projectId, {
          commitMessage,
          expectedRemoteHeadSha: expectedRemoteHeadSha ?? null,
          conflictResolutions,
        });
        if (conflictResolutions.length > 0) {
          applyConflictResolutionsLocally(projectId, conflictResolutions);
        }
        return { sync, conflict: null as ProjectRepoConflictDetail | null };
      } catch (error) {
        const conflict = projectRepoConflictFromError(error);
        if (!conflict) {
          throw error;
        }
        setSyncConflictState({
          fileId,
          projectId,
          commitMessage,
          expectedRemoteHeadSha: conflict.remote_head_sha,
          repoName: conflict.github_repo_name,
          repoUrl: conflict.github_repo_url,
          defaultBranch: conflict.default_branch,
          baseCommitSha: conflict.base_commit_sha,
          conflicts: conflict.conflicts,
          selectedPath: conflict.conflicts[0]?.path ?? "",
          resolutions: Object.fromEntries(
            conflict.conflicts.map((fileConflict) => [
              fileConflict.path,
              fileConflict.local_content,
            ]),
          ),
          afterSaveAction,
          saving: false,
          error: "",
        });
        return { sync: null, conflict };
      }
    },
    [applyConflictResolutionsLocally],
  );

  const openSaveModal = useCallback(
    async (fileId: string) => {
      if (!sessionSettings.has_github_connection) {
        showErrorFlash("Connect GitHub before saving Python files.");
        openAuthModal("settings", "version-control");
        return;
      }
      const file = pythonFileMapRef.current[fileId] ?? (await fetchAndStorePythonFile(fileId));
      const repoSync = await getProjectRepoSync(file.project_id);
      setSaveModalState({
        fileId,
        commitMessage: `Save ${file.filename}`,
        saving: false,
        error: "",
        repoSync,
        success: null,
      });
    },
    [fetchAndStorePythonFile, openAuthModal, sessionSettings.has_github_connection, showErrorFlash],
  );

  const closeSaveModal = useCallback(() => {
    setSaveModalState(null);
  }, []);

  const closeSyncConflictModal = useCallback(() => {
    setSyncConflictState(null);
  }, []);

  const confirmSaveModal = useCallback(async () => {
    if (!saveModalState) {
      return;
    }
    const fileId = saveModalState.fileId;
    const currentFile = pythonFileMapRef.current[fileId] ?? (await fetchAndStorePythonFile(fileId));
    const nextContent = pythonFileDraftsRef.current[fileId]?.content ?? currentFile.content;
    setSaveModalState((current) => (current ? { ...current, saving: true, error: "" } : current));
    try {
      const savedFile = await persistPythonFile(fileId, nextContent);
      const outcome = await syncProjectToGitHub({
        fileId,
        projectId: savedFile.project_id,
        commitMessage: saveModalState.commitMessage,
        afterSaveAction: "save",
      });
      if (outcome.conflict) {
        setSaveModalState(null);
        return;
      }
      const sync = outcome.sync;
      if (!sync) {
        return;
      }
      setSaveModalState((current) =>
        current ? { ...current, saving: false, success: sync, repoSync: sync } : current,
      );
    } catch (error) {
      setSaveModalState((current) =>
        current
          ? {
              ...current,
              saving: false,
              error: error instanceof Error ? error.message : "Project could not be saved.",
            }
          : current,
      );
    }
  }, [fetchAndStorePythonFile, persistPythonFile, saveModalState, syncProjectToGitHub]);

  const confirmSyncConflictResolution = useCallback(async () => {
    if (!syncConflictState) {
      return;
    }
    const conflictResolutions = syncConflictState.conflicts.map((conflict) => ({
      path: conflict.path,
      content: syncConflictState.resolutions[conflict.path] ?? conflict.local_content,
    }));
    setSyncConflictState((current) =>
      current ? { ...current, saving: true, error: "" } : current,
    );
    try {
      const outcome = await syncProjectToGitHub({
        fileId: syncConflictState.fileId,
        projectId: syncConflictState.projectId,
        commitMessage: syncConflictState.commitMessage,
        afterSaveAction: syncConflictState.afterSaveAction,
        expectedRemoteHeadSha: syncConflictState.expectedRemoteHeadSha,
        conflictResolutions,
      });
      if (outcome.conflict || !outcome.sync) {
        return;
      }
      const sync = outcome.sync;
      const fileId = syncConflictState.fileId;
      const commitMessage = syncConflictState.commitMessage;
      const afterSaveAction = syncConflictState.afterSaveAction;
      setSyncConflictState(null);
      if (afterSaveAction === "save") {
        setSaveModalState({
          fileId,
          commitMessage,
          saving: false,
          error: "",
          repoSync: sync,
          success: sync,
        });
        return;
      }
      setPythonFileRuns((current) => ({
        ...current,
        [fileId]: { running: true, result: null, error: "" },
      }));
      try {
        const result = await runPythonFile(fileId);
        setPythonFileRuns((current) => ({
          ...current,
          [fileId]: { running: false, result, error: "" },
        }));
      } catch (error) {
        setPythonFileRuns((current) => ({
          ...current,
          [fileId]: {
            running: false,
            result: null,
            error: error instanceof Error ? error.message : "Python file could not be run.",
          },
        }));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Project could not be saved.";
      setSyncConflictState((current) =>
        current ? { ...current, saving: false, error: message } : current,
      );
      if (syncConflictState.afterSaveAction === "run") {
        setPythonFileRuns((current) => ({
          ...current,
          [syncConflictState.fileId]: {
            running: false,
            result: null,
            error: message,
          },
        }));
      }
    }
  }, [syncConflictState, syncProjectToGitHub]);

  const handleRenamePythonFile = useCallback(
    async (fileId: string) => {
      const currentFile =
        pythonFileMapRef.current[fileId] ?? (await fetchAndStorePythonFile(fileId));
      const nextName = window.prompt("Rename Python file", currentFile.filename);
      if (!nextName) {
        return;
      }
      try {
        const updated = await updatePythonFile(fileId, { filename: nextName });
        setPythonFileMap((current) => ({ ...current, [updated.file_id]: updated }));
        setPythonFileDrafts((current) => ({
          ...current,
          [updated.file_id]: current[updated.file_id] ?? {
            content: updated.content,
            dirty: false,
          },
        }));
        upsertPythonFileIntoProjectItems(updated);
        await refreshProjects();
      } catch (error) {
        setComposerStatus(
          error instanceof Error ? error.message : "Python file could not be renamed.",
        );
      }
    },
    [fetchAndStorePythonFile, refreshProjects, upsertPythonFileIntoProjectItems],
  );

  const handleRunActivePythonFile = useCallback(async () => {
    if (!activePythonFileId) {
      return;
    }
    if (!sessionSettings.has_github_connection) {
      showErrorFlash("Connect GitHub before running Python files.");
      openAuthModal("settings", "version-control");
      return;
    }
    if (!sessionSettings.has_modal_credentials) {
      return;
    }
    try {
      const currentFile =
        pythonFileMapRef.current[activePythonFileId] ??
        (await fetchAndStorePythonFile(activePythonFileId));
      const nextContent =
        pythonFileDraftsRef.current[activePythonFileId]?.content ?? currentFile.content;
      setPythonFileRuns((current) => ({
        ...current,
        [activePythonFileId]: { running: true, result: null, error: "" },
      }));
      const savedFile = await persistPythonFile(activePythonFileId, nextContent);
      const outcome = await syncProjectToGitHub({
        fileId: activePythonFileId,
        projectId: savedFile.project_id,
        commitMessage: `Run ${savedFile.filename}`,
        afterSaveAction: "run",
      });
      if (outcome.conflict) {
        setPythonFileRuns((current) => ({
          ...current,
          [activePythonFileId]: { running: false, result: null, error: "" },
        }));
        return;
      }
      const result = await runPythonFile(activePythonFileId);
      setPythonFileRuns((current) => ({
        ...current,
        [activePythonFileId]: { running: false, result, error: "" },
      }));
    } catch (error) {
      setPythonFileRuns((current) => ({
        ...current,
        [activePythonFileId]: {
          running: false,
          result: null,
          error: error instanceof Error ? error.message : "Python file could not be run.",
        },
      }));
    }
  }, [
    activePythonFileId,
    fetchAndStorePythonFile,
    openAuthModal,
    persistPythonFile,
    sessionSettings.has_github_connection,
    sessionSettings.has_modal_credentials,
    showErrorFlash,
    syncProjectToGitHub,
  ]);

  const handleReadDocs = useCallback(() => {
    window.open(
      new URL("/docs", window.location.origin).toString(),
      "_blank",
      "noopener,noreferrer",
    );
  }, []);

  const handleLogout = useCallback(async () => {
    await logoutBrowserSession();
    resetAuthenticatedState();
  }, [resetAuthenticatedState]);

  useEffect(() => {
    const onAuthExpired = () => {
      resetAuthenticatedState();
      showNoteFlash("Your session expired. Log in again.");
      setAuthView("login");
      setAuthModalOpen(true);
    };
    window.addEventListener("paperbase:auth-expired", onAuthExpired);
    return () => window.removeEventListener("paperbase:auth-expired", onAuthExpired);
  }, [resetAuthenticatedState, showNoteFlash]);

  if (!authReady) {
    return null;
  }

  const contextUsagePopoverId = activeThreadId
    ? `context-usage-popover-${activeThreadId}`
    : "context-usage-popover";
  const composerContextUsageAccessory =
    activeTab?.kind === "thread" && activeContextUsage ? (
      <div
        ref={contextUsageMenuRef}
        className={`context-usage-menu${contextUsagePopoverOpen ? " open" : ""}`}
      >
        <button
          type="button"
          className={`context-usage-button${activeStreamInView ? " live" : ""}`}
          aria-label={contextUsageButtonLabel(activeContextUsage)}
          aria-haspopup="dialog"
          aria-expanded={contextUsagePopoverOpen}
          aria-controls={contextUsagePopoverOpen ? contextUsagePopoverId : undefined}
          title={contextUsageButtonLabel(activeContextUsage)}
          onClick={() => setContextUsagePopoverOpen((current) => !current)}
        >
          <span
            className="context-usage-button-ring"
            style={
              {
                "--context-usage-percent": `${activeContextUsagePercent}%`,
              } as CSSProperties
            }
            aria-hidden="true"
          />
        </button>
        {contextUsagePopoverOpen ? (
          <div
            id={contextUsagePopoverId}
            className="context-usage-popover"
            role="dialog"
            aria-label="Context window usage details"
          >
            <div className="context-usage-popover-header">
              <span className="context-usage-popover-eyebrow">Context window:</span>
              <strong>{formatContextUsageSummary(activeContextUsage)}</strong>
            </div>
            <div className="context-usage-popover-summary">
              {formatContextUsageTokenSummary(activeContextUsage)}
            </div>
            <p className="context-usage-popover-note">Paperbase compacts context near the limit</p>
          </div>
        ) : null}
      </div>
    ) : null;

  const composerEditor = (
    <LandingMentionEditor
      actionAccessory={composerContextUsageAccessory}
      attachButtonId="composer-upload-trigger"
      disabled={Boolean(streamingThreadId)}
      formId="composer-form"
      inputId="composer-input"
      onAttach={() => appUploadRef.current?.click()}
      onChange={setComposerInput}
      onStop={handleStopStreaming}
      onSubmit={() => void handleComposerSubmit()}
      placeholder="Ask about the latest agent and AI systems research. Type @ to tag a paper."
      searchPapers={suggestBrowserPapers}
      submitButtonId="composer-send"
      submitLabel={streamingThreadId ? "Stop" : "Send"}
      submitMode={streamingThreadId ? "stop" : "submit"}
      submitLoading={false}
      submitOnEnter
      suggestionsPlacement="above"
      value={composerInput}
      variant="chat"
    />
  );

  return (
    <>
      {!authenticated ? (
        <main id="landing-page" className="landing-page">
          <button
            id="landing-login-button"
            className="landing-login-button"
            type="button"
            onClick={() => openAuthModal("login")}
          >
            <span className="landing-login-icon" aria-hidden="true">
              <AppIcon src={staticAssetPath("login.svg")} className="landing-login-icon-image" />
            </span>
            <span className="landing-login-label">Log in</span>
          </button>
          <div className="landing-column">
            <a
              className="brandline brandline-link"
              href="https://mv37.org"
              target="_blank"
              rel="noreferrer"
            >
              MV37
            </a>
            <h1 className="landing-title">Paperbase</h1>
            <p className="landing-subtitle">
              Research patterns for building better AI agents and systems.
            </p>
            <div className="landing-copy">
              <p>
                Paperbase helps AI researchers and agent builders keep up with fast-moving work
                without losing track of the original sources, so they can ask broad questions,
                compare ideas across papers, and pull out the design patterns that matter when
                building agents and AI systems.
              </p>
            </div>
            <LandingMentionEditor
              attachButtonId="landing-upload-trigger"
              onAttach={() => openAuthModal("login")}
              onChange={setLandingPrompt}
              onSubmit={() => void handleLandingSubmit()}
              searchPapers={suggestBrowserPapers}
              submitButtonId="landing-start-button"
              submitLabel="Start"
              value={landingPrompt}
            />

            <section className="examples">
              <div className="section-label">Examples</div>
              <div className="example-list">
                {EXAMPLES.map((example) => (
                  <button
                    key={example.label}
                    type="button"
                    className="example-pill"
                    onClick={() => void handleExample(example.prompt)}
                  >
                    <span className="example-title">{example.label}</span>
                    <span className="example-description">{example.description}</span>
                  </button>
                ))}
              </div>
            </section>
          </div>
        </main>
      ) : null}

      {authenticated ? (
        <div
          id="app-shell"
          className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}${mobileSidebarOpen ? " mobile-sidebar-open" : ""}${paperDrawerOpen ? " paper-drawer-open" : ""}`}
        >
          <button
            type="button"
            className="sidebar-scrim"
            aria-label="Close sidebar"
            onClick={() => {
              setMobileSidebarOpen(false);
              setAccountMenuOpen(false);
            }}
          />
          <aside className="sidebar">
            <div className="sidebar-top">
              <div className="sidebar-brand-row">
                <div className="sidebar-title">Paperbase</div>
                <button
                  type="button"
                  className="sidebar-icon-button sidebar-toggle"
                  aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                  aria-expanded={!sidebarCollapsed}
                  onClick={handleSidebarToggle}
                >
                  <AppIcon
                    src={staticAssetPath(
                      sidebarCollapsed ? "show-sidebar.svg" : "hide-sidebar.svg",
                    )}
                    className="sidebar-toggle-icon"
                  />
                </button>
              </div>
              <div className="sidebar-quick-actions">
                <button
                  id="new-chat-button"
                  className="sidebar-quick-action"
                  onClick={() => void handleNewChat()}
                >
                  <AppIcon
                    src={staticAssetPath("edit.svg")}
                    className="sidebar-quick-action-icon"
                  />
                  <span className="sidebar-quick-action-label">New chat</span>
                </button>
                <button type="button" className="sidebar-quick-action" onClick={handleReadDocs}>
                  <AppIcon
                    src={staticAssetPath("docs.svg")}
                    className="sidebar-quick-action-icon"
                  />
                  <span className="sidebar-quick-action-label">Read docs</span>
                </button>
              </div>
            </div>

            <section className="sidebar-section sidebar-card history-section">
              <div className="sidebar-section-header">
                <div className="section-label sidebar-section-label">Projects</div>
                <div className="sidebar-section-actions">
                  <button
                    type="button"
                    id="toggle-projects-button"
                    className="sidebar-icon-button"
                    aria-label={
                      allProjectsExpanded ? "Collapse all projects" : "Expand all projects"
                    }
                    onClick={() => void handleToggleAllProjects()}
                  >
                    <AppIcon
                      src={staticAssetPath(
                        allProjectsExpanded ? "collapse-projects.svg" : "expand-projects.svg",
                      )}
                      className="sidebar-section-action-icon"
                    />
                  </button>
                  <button
                    type="button"
                    id="new-project-button"
                    className="sidebar-icon-button"
                    aria-label="Create project"
                    onClick={openCreateProjectModal}
                  >
                    <AppIcon
                      src={staticAssetPath("new-project.svg")}
                      className="sidebar-section-action-icon"
                    />
                  </button>
                </div>
              </div>
              <div id="project-list" className="project-list">
                {projects.map((project) => {
                  const expanded = expandedProjectIds.includes(project.project_id);
                  const itemState = projectItems[project.project_id];
                  return (
                    <div
                      key={project.project_id}
                      className={`project-group${expanded ? " expanded" : ""}${projectMenuProjectId === project.project_id ? " menu-open" : ""}`}
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        className={`project-card${selectedProjectId === project.project_id ? " active" : ""}`}
                        onClick={() => void handleProjectSelection(project.project_id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            void handleProjectSelection(project.project_id);
                          }
                        }}
                        title={project.name}
                      >
                        <div className="project-card-main">
                          <AppIcon
                            src={staticAssetPath(
                              expanded ? "project-opened.svg" : "project-closed.svg",
                            )}
                            className="project-card-icon"
                          />
                          <strong className="project-card-title">{project.name}</strong>
                        </div>
                        <div
                          className="project-card-actions"
                          onMouseDown={(event) => event.stopPropagation()}
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            className="sidebar-icon-button project-action-button"
                            aria-label={`Start chat in ${project.name}`}
                            onMouseDown={(event) => event.stopPropagation()}
                            onClick={(event) => {
                              event.stopPropagation();
                              void handleProjectStartChat(project.project_id);
                            }}
                          >
                            <AppIcon
                              src={staticAssetPath("edit.svg")}
                              className="project-action-icon"
                            />
                          </button>
                          <div
                            ref={
                              projectMenuProjectId === project.project_id ? projectMenuRef : null
                            }
                            className="project-menu-wrap"
                            onMouseDown={(event) => event.stopPropagation()}
                          >
                            <button
                              type="button"
                              className="sidebar-icon-button project-action-button"
                              aria-label={`Open project menu for ${project.name}`}
                              onMouseDown={(event) => event.stopPropagation()}
                              onClick={(event) => {
                                event.stopPropagation();
                                setSelectedProjectId(project.project_id);
                                setProjectMenuProjectId((current) =>
                                  current === project.project_id ? null : project.project_id,
                                );
                              }}
                            >
                              <AppIcon
                                src={staticAssetPath("menu.svg")}
                                className="project-action-icon"
                              />
                            </button>
                            {projectMenuProjectId === project.project_id ? (
                              <div className="project-menu-popover" role="menu">
                                <button
                                  type="button"
                                  className="project-menu-item"
                                  role="menuitem"
                                  onClick={() => openEditProjectModal(project.project_id)}
                                >
                                  Rename project
                                </button>
                                <button
                                  type="button"
                                  className="project-menu-item danger"
                                  role="menuitem"
                                  disabled={project.is_default}
                                  onClick={() =>
                                    openDeleteProjectModal(project.project_id, project.name)
                                  }
                                >
                                  Delete project
                                </button>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </div>
                      {expanded ? (
                        <div className="project-session-section">
                          {itemState?.loading ? (
                            <div className="project-session-state">Loading items…</div>
                          ) : null}
                          {itemState?.error ? (
                            <div className="project-session-state error">{itemState.error}</div>
                          ) : null}
                          {itemState?.loaded ? (
                            <>
                              {itemState.items.length > 0 ? (
                                <div className="thread-list project-thread-list">
                                  {itemState.items.map((item) => {
                                    if (item.item_kind === "thread") {
                                      if (
                                        !shouldShowThreadInSidebar({
                                          thread_id: item.thread_id ?? "",
                                          project_id: item.project_id,
                                          title: item.title,
                                          created_at: item.created_at,
                                          updated_at: item.updated_at,
                                          last_message_preview: item.last_message_preview,
                                        })
                                      ) {
                                        return null;
                                      }
                                      const title = displayThreadTitle(item.title);
                                      return (
                                        <div
                                          key={item.item_id}
                                          role="button"
                                          tabIndex={0}
                                          className={`thread-card${activeThreadId === item.thread_id ? " active" : ""}`}
                                          title={title}
                                          onClick={() =>
                                            void handleThreadSelection(item.thread_id ?? "")
                                          }
                                          onKeyDown={(event) => {
                                            if (event.key === "Enter" || event.key === " ") {
                                              event.preventDefault();
                                              void handleThreadSelection(item.thread_id ?? "");
                                            }
                                          }}
                                        >
                                          <div className="thread-card-copy">
                                            <strong className="thread-card-title">{title}</strong>
                                          </div>
                                          <span className="thread-card-age">
                                            {formatThreadAge(item.updated_at)}
                                          </span>
                                          <button
                                            type="button"
                                            className="thread-card-delete"
                                            aria-label={`Delete ${title}`}
                                            onClick={(event) => {
                                              event.stopPropagation();
                                              openDeleteThreadModal(
                                                project.project_id,
                                                item.thread_id ?? "",
                                                title,
                                              );
                                            }}
                                          >
                                            <AppIcon
                                              src={staticAssetPath("delete-session.svg")}
                                              className="thread-card-delete-icon"
                                            />
                                          </button>
                                        </div>
                                      );
                                    }
                                    const title = displayPythonFilename(
                                      item.filename || item.title,
                                    );
                                    return (
                                      <div
                                        key={item.item_id}
                                        role="button"
                                        tabIndex={0}
                                        className={`thread-card python-file-card${activePythonFileId === item.file_id ? " active" : ""}`}
                                        title={title}
                                        onClick={() =>
                                          void handlePythonFileSelection(item.file_id ?? "")
                                        }
                                        onKeyDown={(event) => {
                                          if (event.key === "Enter" || event.key === " ") {
                                            event.preventDefault();
                                            void handlePythonFileSelection(item.file_id ?? "");
                                          }
                                        }}
                                      >
                                        <div className="thread-card-copy python-file-card-copy">
                                          <AppIcon
                                            src={staticAssetPath("python.svg")}
                                            className="python-file-card-icon"
                                          />
                                          <strong className="thread-card-title">{title}</strong>
                                        </div>
                                        <span className="thread-card-age">
                                          {formatThreadAge(item.updated_at)}
                                        </span>
                                        <button
                                          type="button"
                                          className="thread-card-delete thread-card-rename"
                                          aria-label={`Rename ${title}`}
                                          onClick={(event) => {
                                            event.stopPropagation();
                                            void handleRenamePythonFile(item.file_id ?? "");
                                          }}
                                        >
                                          <AppIcon
                                            src={staticAssetPath("edit.svg")}
                                            className="thread-card-delete-icon"
                                          />
                                        </button>
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : (
                                <div className="project-session-state">No items yet.</div>
                              )}
                              {itemState.hasMore ? (
                                <button
                                  type="button"
                                  className="project-show-more"
                                  disabled={itemState.loadingMore}
                                  onClick={() => void handleLoadMoreItems(project.project_id)}
                                >
                                  {itemState.loadingMore ? "Loading…" : "Show more"}
                                </button>
                              ) : null}
                            </>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </section>

            {sessionSummary ? (
              <section className="sidebar-settings-footer">
                <div ref={accountMenuRef} className="sidebar-menu-wrap">
                  <button
                    type="button"
                    id="account-menu-button"
                    className="sidebar-settings-button"
                    aria-label="Open settings"
                    aria-haspopup="menu"
                    aria-expanded={accountMenuOpen}
                    onClick={() => setAccountMenuOpen((current) => !current)}
                  >
                    <AppIcon
                      src={staticAssetPath("settings.svg")}
                      className="sidebar-settings-icon"
                    />
                    <span>Settings</span>
                  </button>
                  {accountMenuOpen ? (
                    <div className="account-menu" role="menu" aria-label="Account actions">
                      <div className="account-menu-user">
                        <span className="account-menu-user-icon" aria-hidden="true">
                          <svg viewBox="0 0 24 24">
                            <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z" />
                            <path d="M4.5 19a7.5 7.5 0 0 1 15 0" />
                          </svg>
                        </span>
                        <span className="account-menu-user-email">{sessionSummary.email}</span>
                      </div>
                      <div className="account-menu-divider" />
                      <button
                        type="button"
                        id="settings-menu-item"
                        className="account-menu-item"
                        role="menuitem"
                        onClick={() => openAuthModal("settings", "inference")}
                      >
                        <AppIcon
                          src={staticAssetPath("settings.svg")}
                          className="account-menu-item-icon"
                        />
                        Settings
                      </button>
                      <button
                        type="button"
                        id="logout-button"
                        className="account-menu-item danger"
                        role="menuitem"
                        onClick={() => void handleLogout()}
                      >
                        <AppIcon
                          src={staticAssetPath("logout.svg")}
                          className="account-menu-item-icon"
                        />
                        Log out
                      </button>
                    </div>
                  ) : null}
                </div>
              </section>
            ) : null}
          </aside>

          <section className={`chat-stage${hasOpenTabs ? "" : " no-tabs"}`}>
            {hasOpenTabs ? (
              <header className="chat-stage-header">
                <div className="chat-stage-header-inner">
                  <button
                    type="button"
                    className="sidebar-icon-button chat-sidebar-toggle"
                    aria-label={mobileSidebarOpen ? "Close sidebar" : "Open sidebar"}
                    onClick={handleSidebarToggle}
                  >
                    <AppIcon
                      src={staticAssetPath(
                        mobileSidebarOpen ? "hide-sidebar.svg" : "show-sidebar.svg",
                      )}
                      className="sidebar-toggle-icon"
                    />
                  </button>
                  <div
                    className="chat-tab-strip"
                    role="tablist"
                    aria-label="Open tabs"
                    style={tabStripStyle}
                  >
                    {openTabItems.map((tab) => (
                      <div
                        key={tab.key}
                        role="tab"
                        tabIndex={0}
                        aria-selected={activeTab?.kind === tab.kind && activeTab.id === tab.id}
                        className={`chat-tab${activeTab?.kind === tab.kind && activeTab.id === tab.id ? " active" : ""}`}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            if (tab.kind === "thread") {
                              void handleThreadSelection(tab.id);
                            } else {
                              void handlePythonFileSelection(tab.id);
                            }
                          }
                        }}
                      >
                        <button
                          type="button"
                          className="chat-tab-trigger"
                          title={tab.title}
                          onClick={() => {
                            if (tab.kind === "thread") {
                              void handleThreadSelection(tab.id);
                            } else {
                              void handlePythonFileSelection(tab.id);
                            }
                          }}
                        >
                          <span className="chat-tab-title">{tab.tabTitle}</span>
                        </button>
                        <button
                          type="button"
                          aria-label={`Close ${tab.title}`}
                          className="chat-tab-close"
                          onClick={(event) => {
                            event.stopPropagation();
                            closeTab({ kind: tab.kind, id: tab.id });
                          }}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </header>
            ) : null}

            <div
              ref={messageListRef}
              className={`chat-message-list${isChatEmpty ? " empty" : ""}${activeTab?.kind === "python_file" ? " python-file-view" : ""}`}
              onScroll={handleMessageListScroll}
            >
              {activeTab?.kind === "python_file" ? (
                activePythonFile ? (
                  <div className="python-file-stage">
                    <div className="python-editor-shell">
                      <div className="python-file-toolbar">
                        {activePythonDraft?.dirty ? (
                          <span className="python-file-dirty">Unsaved changes</span>
                        ) : null}
                        <button
                          type="button"
                          className="python-file-save-button"
                          onClick={() => void openSaveModal(activePythonFile.file_id)}
                        >
                          <span className="python-file-action-icon" aria-hidden="true">
                            <MaskedAssetIcon
                              asset="github.svg"
                              className="python-file-action-glyph"
                            />
                          </span>
                          <span className="python-file-action-label">Save</span>
                        </button>
                        <button
                          type="button"
                          className={`python-file-run-button${sessionSettings.has_modal_credentials ? "" : " disabled"}`}
                          disabled={
                            !sessionSettings.has_modal_credentials || activePythonRun.running
                          }
                          onClick={() => void handleRunActivePythonFile()}
                        >
                          <span className="python-file-action-icon" aria-hidden="true">
                            <MaskedAssetIcon
                              asset="play.svg"
                              className="python-file-action-glyph"
                            />
                          </span>
                          <span className="python-file-action-label">
                            {activePythonRun.running ? "Running..." : "Run"}
                          </span>
                        </button>
                      </div>
                      <Editor
                        height="100%"
                        language="python"
                        theme="hc-black"
                        value={activePythonContent}
                        onChange={(value) => {
                          const nextContent = value ?? "";
                          setPythonFileDrafts((current) => ({
                            ...current,
                            [activePythonFile.file_id]: {
                              content: nextContent,
                              dirty:
                                nextContent !==
                                (pythonFileMapRef.current[activePythonFile.file_id]?.content ?? ""),
                            },
                          }));
                        }}
                        options={{
                          automaticLayout: true,
                          fontSize: 14,
                          minimap: { enabled: false },
                          padding: {
                            top: 36,
                            bottom: activePythonRun.result || activePythonRun.error ? 168 : 10,
                          },
                          scrollBeyondLastLine: false,
                          wordWrap: "on",
                        }}
                      />
                      {activePythonRun.error ? (
                        <div className="python-run-error">{activePythonRun.error}</div>
                      ) : null}
                      {activePythonRun.result ? (
                        <div className="python-run-panel">
                          <div className="python-run-panel-header">
                            <strong>
                              {activePythonRun.result.status === "completed"
                                ? "Latest run"
                                : "Run failed"}
                            </strong>
                            <span>Exit code {activePythonRun.result.exit_code}</span>
                          </div>
                          {activePythonRun.result.stdout ? (
                            <div className="python-run-block">
                              <div className="python-run-block-label">stdout</div>
                              <pre>{activePythonRun.result.stdout}</pre>
                            </div>
                          ) : null}
                          {activePythonRun.result.stderr ? (
                            <div className="python-run-block">
                              <div className="python-run-block-label">stderr</div>
                              <pre>{activePythonRun.result.stderr}</pre>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <div className="python-file-stage python-file-stage-loading">
                    Loading Python file...
                  </div>
                )
              ) : isChatEmpty ? (
                <div className="chat-stage-spacer">
                  {!hasOpenTabs ? (
                    <button
                      type="button"
                      className="sidebar-icon-button chat-sidebar-toggle empty-state-sidebar-toggle"
                      aria-label={mobileSidebarOpen ? "Close sidebar" : "Open sidebar"}
                      onClick={handleSidebarToggle}
                    >
                      <AppIcon
                        src={staticAssetPath(
                          mobileSidebarOpen ? "hide-sidebar.svg" : "show-sidebar.svg",
                        )}
                        className="sidebar-toggle-icon"
                      />
                    </button>
                  ) : null}
                  <div className="chat-empty-state">
                    <a
                      className="brandline brandline-link chat-empty-brandline"
                      href="https://mv37.org"
                      target="_blank"
                      rel="noreferrer"
                    >
                      MV37
                    </a>
                    <div className="chat-empty-project-row">
                      <strong>Start new research in</strong>
                      <div ref={emptyStateProjectPickerRef} className="chat-empty-project-picker">
                        <button
                          type="button"
                          id="empty-state-project-select-trigger"
                          className="chat-empty-project-trigger"
                          aria-label="Open empty state project picker"
                          aria-haspopup="dialog"
                          aria-expanded={emptyStateProjectPickerOpen}
                          onClick={() => setEmptyStateProjectPickerOpen((current) => !current)}
                        >
                          <span>{emptyStateProject?.name ?? "Project"}</span>
                        </button>
                        {emptyStateProjectPickerOpen ? (
                          <div
                            className="chat-empty-project-menu"
                            role="dialog"
                            aria-label="Choose project"
                          >
                            <div className="chat-empty-project-search-shell">
                              <input
                                ref={emptyStateProjectSearchInputRef}
                                type="text"
                                className="chat-empty-project-search"
                                placeholder="Search projects"
                                aria-label="Search projects"
                                value={emptyStateProjectSearch}
                                onChange={(event) => setEmptyStateProjectSearch(event.target.value)}
                              />
                            </div>
                            <div
                              className="chat-empty-project-options"
                              role="listbox"
                              aria-label="Choose project"
                            >
                              {filteredEmptyStateProjects.length > 0 ? (
                                filteredEmptyStateProjects.map((project) => (
                                  <button
                                    key={project.project_id}
                                    type="button"
                                    role="option"
                                    aria-selected={
                                      emptyStateProject?.project_id === project.project_id
                                    }
                                    className={`chat-empty-project-option${emptyStateProject?.project_id === project.project_id ? " active" : ""}`}
                                    onClick={() => {
                                      setSelectedProjectId(project.project_id);
                                      setEmptyStateProjectPickerOpen(false);
                                    }}
                                  >
                                    {project.name}
                                  </button>
                                ))
                              ) : (
                                <div className="chat-empty-project-empty">
                                  No projects match this search.
                                </div>
                              )}
                            </div>
                            <button
                              type="button"
                              className="chat-empty-project-create"
                              onClick={openCreateProjectModal}
                            >
                              <AppIcon
                                src={staticAssetPath("new-project.svg")}
                                className="chat-empty-project-create-icon"
                              />
                              <span>Create new project</span>
                            </button>
                          </div>
                        ) : null}
                      </div>
                    </div>
                    {composerStatus ? (
                      <div className="composer-status inline">{composerStatus}</div>
                    ) : null}
                    {composerEditor}
                    <div className="chat-empty-actions">
                      <button
                        type="button"
                        className="chat-empty-action-tag python"
                        onClick={() => void handleCreatePythonFile()}
                      >
                        <span className="chat-empty-action-icon" aria-hidden="true">
                          <EmptyStateActionIcon asset="python.svg" />
                        </span>
                        <span className="chat-empty-action-label">Add python file</span>
                      </button>
                      <button type="button" className="chat-empty-action-tag notes">
                        <span className="chat-empty-action-icon" aria-hidden="true">
                          <EmptyStateActionIcon asset="note.svg" />
                        </span>
                        <span className="chat-empty-action-label">Add notes</span>
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="chat-message-column">
                  {(() => {
                    let userTurnIndex = 0;
                    return activeThread.messages.map((message) => {
                      const isCompactionMessage = message.kind === "compaction";
                      if (message.role === "user" && !isCompactionMessage) {
                        userTurnIndex += 1;
                      }
                      const latestToolActivityTurnIndex =
                        activeThreadToolActivitiesByTurn.size > 0
                          ? Math.max(...activeThreadToolActivitiesByTurn.keys())
                          : userTurnIndex;
                      const activityTurnIndex =
                        isCompactionMessage && activeThreadStreamState
                          ? activeThreadStreamState.turnIndex
                          : isCompactionMessage
                            ? latestToolActivityTurnIndex
                            : userTurnIndex;
                      const turnActivities =
                        message.role === "user" || isCompactionMessage
                          ? (activeThreadToolActivitiesByTurn.get(activityTurnIndex) ?? [])
                          : [];
                      const showThinkingPlaceholder =
                        message.role === "user" &&
                        !isCompactionMessage &&
                        activeStreamInView &&
                        activeThreadStreamState?.turnIndex === userTurnIndex &&
                        !activeThreadStreamState.assistantStarted &&
                        turnActivities.length === 0;
                      const hoverTime = formatTodayTimeLabel(message.created_at);
                      return (
                        <Fragment key={message.message_id}>
                          <article
                            className={`message-row ${isCompactionMessage ? "compaction" : message.role === "assistant" ? "assistant" : "user"} status-${message.status}`}
                          >
                            {message.status !== "completed" && !isCompactionMessage ? (
                              <div className="message-meta-row">
                                <span
                                  className={`message-status-label${message.status === "streaming" ? " thinking-shimmer" : ""}`}
                                >
                                  {message.status === "streaming"
                                    ? formatThinkingLabel(activeThreadStreamState?.thinkingWord)
                                    : "Failed"}
                                </span>
                              </div>
                            ) : null}
                            <div
                              className={`message-bubble ${isCompactionMessage ? "compaction" : message.role === "assistant" ? "assistant" : "user"}${isCompactionMessage && message.status === "streaming" ? " thinking-shimmer" : ""}`}
                            >
                              {isCompactionMessage ? (
                                <MarkdownRenderer
                                  markdown={message.content_markdown}
                                  className="message-markdown compaction-markdown"
                                />
                              ) : message.role === "assistant" ? (
                                <MarkdownRenderer
                                  markdown={message.content_markdown}
                                  className="message-markdown"
                                  interactiveCitations
                                  onCitationClick={(sourceId) => {
                                    const citation = message.citations.find(
                                      (item) => item.source_id === sourceId,
                                    );
                                    if (citation) {
                                      handleCitationClick(message, citation);
                                    }
                                  }}
                                />
                              ) : (
                                <div className="message-plain-text">{message.content_markdown}</div>
                              )}
                            </div>
                            {message.role === "assistant" && !isCompactionMessage ? (
                              <div className="assistant-hover-actions">
                                {hoverTime ? (
                                  <span className="assistant-hover-time">{hoverTime}</span>
                                ) : null}
                                <button
                                  type="button"
                                  className="assistant-copy-button"
                                  aria-label="Copy response"
                                  title="Copy response"
                                  onClick={() => void copyMessage(message.content_markdown)}
                                >
                                  <svg viewBox="0 0 24 24" aria-hidden="true">
                                    <path d="M9 9h9v11H9z" />
                                    <path d="M6 15H5V4h9v1" />
                                  </svg>
                                </button>
                              </div>
                            ) : null}
                            {message.role === "assistant" && message.citations.length > 0 ? (
                              <div className="message-citations">
                                {message.citations.map((citation) => (
                                  <div
                                    key={citation.source_id}
                                    id={sourceCardId(message.message_id, citation.source_id)}
                                    className="citation-card"
                                  >
                                    <div className="citation-card-header">
                                      <div>
                                        <span className="citation-card-source-id">
                                          {citation.source_id}
                                        </span>
                                        <strong className="citation-card-title">
                                          {citation.title}
                                        </strong>
                                      </div>
                                      {citation.paper_id ? (
                                        <button
                                          type="button"
                                          className="citation-card-action"
                                          onClick={() =>
                                            openPaperDrawer(citation.paper_id!, citation.path)
                                          }
                                        >
                                          Open paper
                                        </button>
                                      ) : null}
                                    </div>
                                    <div className="citation-card-path">
                                      {citation.source_kind === "paperbase_paper"
                                        ? "Paper"
                                        : "Upload"}{" "}
                                      · {citation.path}
                                    </div>
                                    <p className="citation-card-excerpt">{citation.excerpt}</p>
                                  </div>
                                ))}
                              </div>
                            ) : null}
                          </article>
                          {turnActivities.map((activity) => (
                            <div
                              key={activity.id}
                              className={`tool-activity-row status-${activity.status}${activity.isError ? " error" : ""}`}
                            >
                              <span
                                className={`tool-activity-label${activity.status === "running" ? " thinking-shimmer" : ""}`}
                              >
                                {activity.status === "running"
                                  ? `Doing ${activity.label}...`
                                  : `Did ${activity.label}`}
                              </span>
                            </div>
                          ))}
                          {showThinkingPlaceholder ? (
                            <div className="tool-activity-row status-running thinking-only">
                              <div className="tool-activity-content">
                                <span className="tool-activity-label thinking-shimmer">
                                  {formatThinkingLabel(activeThreadStreamState?.thinkingWord)}
                                </span>
                              </div>
                            </div>
                          ) : null}
                        </Fragment>
                      );
                    });
                  })()}
                </div>
              )}
            </div>

            {activeTab?.kind === "thread" && !isChatEmpty ? (
              <div className="chat-composer-fade" aria-hidden="true" />
            ) : null}
            {showScrollToLatest ? (
              <button
                type="button"
                className="chat-scroll-to-latest"
                aria-label="Scroll to latest message"
                onClick={() => scrollMessageListToBottom("smooth")}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 5v12" />
                  <path d="m7 12 5 5 5-5" />
                </svg>
              </button>
            ) : null}
            {activeTab?.kind === "thread" && !isChatEmpty ? (
              <>
                {composerStatus ? <div className="composer-status">{composerStatus}</div> : null}
                {composerEditor}
              </>
            ) : null}
            <input
              ref={appUploadRef}
              id="app-upload-file"
              className="hidden-file-input"
              type="file"
              onChange={onUploadChange}
            />
          </section>

          {paperDrawerOpen ? (
            <aside className="paper-drawer" aria-label="Cited paper drawer">
              <div className="paper-drawer-header">
                <div>
                  <div className="paper-drawer-eyebrow">Cited paper</div>
                  <strong className="paper-drawer-title">
                    {paperDrawerPaper?.title ?? "Loading paper"}
                  </strong>
                  {paperDrawer ? <div className="paper-drawer-path">{paperDrawer.path}</div> : null}
                </div>
                <button
                  type="button"
                  className="paper-drawer-close"
                  aria-label="Close paper drawer"
                  onClick={closePaperDrawer}
                >
                  ×
                </button>
              </div>

              {paperDrawerPaper ? (
                <div className="paper-drawer-meta">
                  <div>{paperDrawerPaper.authors}</div>
                  <div>
                    {paperDrawerPaper.primary_category || paperDrawerPaper.categories}
                    {paperDrawerPaper.published ? ` · ${paperDrawerPaper.published}` : ""}
                  </div>
                </div>
              ) : null}

              <div className="paper-drawer-body">
                {paperDrawerStatus === "loading" ? (
                  <div className="paper-drawer-placeholder">Loading cited paper…</div>
                ) : null}
                {paperDrawerStatus === "error" ? (
                  <div className="paper-drawer-error">{paperDrawerError}</div>
                ) : null}
                {paperDrawerStatus === "ready" && paperDrawerContent?.kind === "markdown" ? (
                  <MarkdownRenderer
                    markdown={paperDrawerContent.file.content}
                    className="paper-drawer-markdown"
                  />
                ) : null}
                {paperDrawerStatus === "ready" && paperDrawerContent?.kind === "figure" ? (
                  <div className="paper-drawer-figure">
                    {paperDrawerContent.figure.image_url ? (
                      <img
                        className="paper-drawer-figure-image"
                        src={paperDrawerContent.figure.image_url}
                        alt={paperDrawerContent.figure.caption}
                      />
                    ) : null}
                    <MarkdownRenderer
                      markdown={paperDrawerContent.figure.caption}
                      className="paper-drawer-markdown"
                    />
                  </div>
                ) : null}
                {paperDrawerStatus === "ready" && paperDrawerContent?.kind === "table" ? (
                  <div className="paper-drawer-table-wrap">
                    {paperDrawerContent.table.caption ? (
                      <MarkdownRenderer
                        markdown={paperDrawerContent.table.caption}
                        className="paper-drawer-markdown"
                      />
                    ) : null}
                    <div className="paper-drawer-table-scroll">
                      <table className="paper-drawer-table">
                        <tbody>
                          {paperDrawerContent.table.rows.map((row, rowIndex) => (
                            <tr key={`${paperDrawerContent.table.table_id}-${rowIndex}`}>
                              {row.map((cell, cellIndex) => (
                                <td
                                  key={`${paperDrawerContent.table.table_id}-${rowIndex}-${cellIndex}`}
                                >
                                  {cell}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}
              </div>
            </aside>
          ) : null}
        </div>
      ) : null}

      <div
        id="python-save-modal"
        className={`modal${saveModalState ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-backdrop" onClick={closeSaveModal} />
        <div className="modal-panel python-save-modal-panel">
          <button className="modal-close" type="button" onClick={closeSaveModal}>
            ×
          </button>
          <div className="modal-heading">
            <h2 className="modal-title">Save Python file</h2>
            <p className="modal-caption">
              {saveModalState?.repoSync?.repo_exists
                ? "Paperbase will commit the current project files to the linked project repository."
                : "Paperbase will create a dedicated GitHub repository for this project and commit the current files."}
            </p>
          </div>
          {saveModalState?.error ? (
            <div className="modal-flash warning" role="alert">
              <span className="modal-flash-text">{saveModalState.error}</span>
            </div>
          ) : null}
          <div className="modal-view visible">
            {saveModalState?.success ? (
              <div className="stack python-save-success">
                <div className="python-save-success-line">
                  Saved to{" "}
                  <a href={saveModalState.success.github_repo_url} target="_blank" rel="noreferrer">
                    {saveModalState.success.github_repo_name}
                  </a>
                  .
                </div>
                {saveModalState.success.last_sync_commit_url ? (
                  <div className="python-save-success-line">
                    <a
                      href={saveModalState.success.last_sync_commit_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      View commit
                    </a>
                  </div>
                ) : null}
                <button className="primary-button" type="button" onClick={closeSaveModal}>
                  Close
                </button>
              </div>
            ) : (
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault();
                  void confirmSaveModal();
                }}
              >
                <input
                  id="python-save-message-input"
                  name="commit_message"
                  type="text"
                  placeholder="Commit message"
                  value={saveModalState?.commitMessage ?? ""}
                  onChange={(event) =>
                    setSaveModalState((current) =>
                      current ? { ...current, commitMessage: event.target.value } : current,
                    )
                  }
                  required
                />
                <button className="primary-button" type="submit" disabled={saveModalState?.saving}>
                  {saveModalState?.saving ? "Saving..." : "Save to GitHub"}
                </button>
              </form>
            )}
          </div>
        </div>
      </div>

      <div
        id="python-conflict-modal"
        className={`modal${syncConflictState ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-backdrop" onClick={closeSyncConflictModal} />
        <div className="modal-panel python-conflict-modal-panel">
          <button className="modal-close" type="button" onClick={closeSyncConflictModal}>
            ×
          </button>
          <div className="modal-heading modal-heading-copy">
            <h2 className="modal-title">Resolve GitHub conflicts</h2>
            <p className="modal-caption">
              Paperbase fetched a newer upstream commit in{" "}
              <a href={syncConflictState?.repoUrl} target="_blank" rel="noreferrer">
                {syncConflictState?.repoName || "the project repository"}
              </a>
              . Review each conflicting file, edit the merged result, then save again.
            </p>
          </div>
          {syncConflictState?.error ? (
            <div className="modal-flash warning" role="alert">
              <span className="modal-flash-text">{syncConflictState.error}</span>
            </div>
          ) : null}
          <div className="python-conflict-layout">
            <div
              className="python-conflict-file-list"
              role="tablist"
              aria-label="Conflicting files"
            >
              {syncConflictState?.conflicts.map((conflict) => (
                <button
                  key={conflict.path}
                  type="button"
                  role="tab"
                  aria-selected={syncConflictState.selectedPath === conflict.path}
                  className={`python-conflict-file-tab${syncConflictState.selectedPath === conflict.path ? " active" : ""}`}
                  onClick={() =>
                    setSyncConflictState((current) =>
                      current ? { ...current, selectedPath: conflict.path } : current,
                    )
                  }
                >
                  {pythonFilenameFromRepoPath(conflict.path) ?? conflict.path}
                </button>
              ))}
            </div>
            {selectedSyncConflict ? (
              <div className="python-conflict-panel">
                <div className="python-conflict-summary">
                  <strong>{selectedSyncConflict.path}</strong>
                  <span>
                    Base {syncConflictState?.baseCommitSha.slice(0, 7) || "unknown"} vs upstream{" "}
                    {syncConflictState?.expectedRemoteHeadSha.slice(0, 7) || "latest"}
                  </span>
                </div>
                <div className="python-conflict-diff-shell">
                  <DiffEditor
                    height="240px"
                    language="python"
                    theme="hc-black"
                    original={selectedSyncConflict.remote_content}
                    modified={selectedSyncConflict.local_content}
                    options={{
                      automaticLayout: true,
                      minimap: { enabled: false },
                      readOnly: true,
                      renderSideBySide: true,
                      wordWrap: "on",
                    }}
                  />
                </div>
                <div className="python-conflict-merge-toolbar">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      setSyncConflictState((current) =>
                        current
                          ? {
                              ...current,
                              resolutions: {
                                ...current.resolutions,
                                [selectedSyncConflict.path]: selectedSyncConflict.local_content,
                              },
                            }
                          : current,
                      )
                    }
                  >
                    Use local
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      setSyncConflictState((current) =>
                        current
                          ? {
                              ...current,
                              resolutions: {
                                ...current.resolutions,
                                [selectedSyncConflict.path]: selectedSyncConflict.remote_content,
                              },
                            }
                          : current,
                      )
                    }
                  >
                    Use remote
                  </button>
                </div>
                <div className="python-conflict-merge-shell">
                  <Editor
                    height="220px"
                    language="python"
                    theme="hc-black"
                    value={selectedSyncConflictResolution}
                    onChange={(value) =>
                      setSyncConflictState((current) =>
                        current
                          ? {
                              ...current,
                              resolutions: {
                                ...current.resolutions,
                                [selectedSyncConflict.path]: value ?? "",
                              },
                            }
                          : current,
                      )
                    }
                    options={{
                      automaticLayout: true,
                      minimap: { enabled: false },
                      scrollBeyondLastLine: false,
                      wordWrap: "on",
                    }}
                  />
                </div>
              </div>
            ) : null}
          </div>
          <div className="python-conflict-actions">
            <button className="secondary-button" type="button" onClick={closeSyncConflictModal}>
              Cancel
            </button>
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                void confirmSyncConflictResolution();
              }}
              disabled={syncConflictState?.saving}
            >
              {syncConflictState?.saving
                ? "Saving..."
                : syncConflictState?.afterSaveAction === "run"
                  ? "Save and run"
                  : "Save merge"}
            </button>
          </div>
        </div>
      </div>

      <div
        id="project-modal"
        className={`modal${projectModalState ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-backdrop" onClick={closeProjectModal} />
        <div className="modal-panel">
          <button className="modal-close" type="button" onClick={closeProjectModal}>
            ×
          </button>
          <div className="modal-heading">
            <h2 className="modal-title">
              {projectModalState?.mode === "edit" ? "Rename project" : "New project"}
            </h2>
            <p className="modal-caption">
              Add a project name and optional goal or custom instructions for new chats.
            </p>
          </div>
          {projectFormError ? (
            <div className="modal-flash warning" role="alert">
              <span className="modal-flash-text">{projectFormError}</span>
            </div>
          ) : null}
          <div className="modal-view visible">
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                void submitProjectModal();
              }}
            >
              <input
                id="project-name-input"
                name="name"
                type="text"
                placeholder="Project name"
                value={projectFormName}
                onChange={(event) => setProjectFormName(event.target.value)}
                required
              />
              <textarea
                id="project-description-input"
                name="description"
                className="project-description-input"
                placeholder="Description, goal, or custom instructions"
                value={projectFormDescription}
                onChange={(event) => setProjectFormDescription(event.target.value)}
                rows={4}
              />
              <button className="primary-button" type="submit">
                {projectModalState?.mode === "edit" ? "Save project" : "Create project"}
              </button>
            </form>
          </div>
        </div>
      </div>

      <div
        id="delete-modal"
        className={`modal${deleteModalState ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-backdrop" onClick={closeDeleteModal} />
        <div className="modal-panel">
          <button className="modal-close" type="button" onClick={closeDeleteModal}>
            ×
          </button>
          <div className="modal-heading">
            <h2 className="modal-title">
              {deleteModalState?.kind === "project" ? "Delete project" : "Delete session"}
            </h2>
            <p className="modal-caption">
              {deleteModalState?.kind === "project"
                ? `Delete ${deleteModalState.name} and all of its sessions?`
                : `Delete ${deleteModalState?.title ?? "this session"}?`}
            </p>
          </div>
          <div className="modal-view visible">
            <div className="stack">
              <button
                className="primary-button danger-button"
                type="button"
                onClick={() => void confirmDeleteModal()}
              >
                {deleteModalState?.kind === "project" ? "Delete project" : "Delete session"}
              </button>
              <button className="secondary-button" type="button" onClick={closeDeleteModal}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      </div>

      <div
        id="auth-modal"
        className={`modal${authModalOpen ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-backdrop" data-close-modal="true" onClick={closeAuthModal} />
        <div className={`modal-panel${authView === "settings" ? " settings-modal-panel" : ""}`}>
          <button
            className="modal-close"
            type="button"
            data-close-modal="true"
            onClick={closeAuthModal}
          >
            ×
          </button>
          {authView === "settings" ? null : (
            <div className="modal-heading">
              <div className="modal-heading-copy">
                <h2 id="auth-modal-title" className="modal-title">
                  {authCopy.title}
                </h2>
                <p id="auth-modal-caption" className="modal-caption">
                  {authCaption}
                </p>
              </div>
            </div>
          )}

          {flash && authView !== "settings" ? (
            <ModalFlash message={flash} kind={flashKind} />
          ) : null}

          <div
            className={`modal-view${authView === "login" ? " visible" : ""}`}
            data-auth-view="login"
          >
            <form
              id="login-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  const nextSession = (await postForm(
                    "/v1/browser/auth/login",
                    event.currentTarget,
                  )) as BrowserSessionResponse;
                  await handleAuthSuccess(nextSession);
                } catch (error) {
                  showErrorFlash(error instanceof Error ? error.message : "Login failed");
                }
              }}
            >
              <input name="email" type="email" placeholder="Email" autoComplete="email" required />
              <input
                name="password"
                type="password"
                placeholder="Password"
                autoComplete="current-password"
                required
              />
              <button className="primary-button" type="submit">
                Log in
              </button>
            </form>
            <GoogleButton />
            <div className="auth-links">
              <button type="button" className="auth-link" onClick={() => setAuthView("signup")}>
                Create a new account
              </button>
              <button
                type="button"
                className="auth-link"
                onClick={() => setAuthView("reset-request")}
              >
                Forgot password
              </button>
            </div>
          </div>

          <div
            className={`modal-view${authView === "signup" ? " visible" : ""}`}
            data-auth-view="signup"
          >
            <form
              id="signup-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  const nextSession = (await postForm(
                    "/v1/browser/auth/signup",
                    event.currentTarget,
                  )) as BrowserSessionResponse;
                  await handleAuthSuccess(nextSession);
                } catch (error) {
                  showErrorFlash(error instanceof Error ? error.message : "Sign up failed");
                }
              }}
            >
              <input name="display_name" type="text" placeholder="Name" autoComplete="name" />
              <input name="email" type="email" placeholder="Email" autoComplete="email" required />
              <input
                name="password"
                type="password"
                placeholder="Password"
                autoComplete="new-password"
                required
              />
              <button className="primary-button" type="submit">
                Create account
              </button>
            </form>
            <GoogleButton />
            <div className="auth-links">
              <button type="button" className="auth-link" onClick={() => setAuthView("login")}>
                Already have an account? Log in
              </button>
            </div>
          </div>

          <div
            className={`modal-view${authView === "reset-request" ? " visible" : ""}`}
            data-auth-view="reset-request"
          >
            <form
              id="forgot-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  await postForm("/v1/browser/auth/forgot-password", event.currentTarget);
                  showNoteFlash("If that account exists, a reset link has been sent.");
                } catch (error) {
                  showErrorFlash(error instanceof Error ? error.message : "Password reset failed");
                }
              }}
            >
              <input name="email" type="email" placeholder="Email" autoComplete="email" required />
              <button className="primary-button" type="submit">
                Send reset link
              </button>
            </form>
            <div className="auth-links">
              <button type="button" className="auth-link" onClick={() => setAuthView("login")}>
                Back to log in
              </button>
            </div>
          </div>

          <div
            className={`modal-view${authView === "reset-password" ? " visible" : ""}`}
            data-auth-view="reset-password"
          >
            <form
              id="reset-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  await postForm("/v1/browser/auth/reset-password", event.currentTarget);
                  showNoteFlash("Password reset complete. You can now log in.");
                  setAuthView("login");
                } catch (error) {
                  showErrorFlash(error instanceof Error ? error.message : "Password reset failed");
                }
              }}
            >
              <input
                name="token"
                type="text"
                placeholder="Reset token"
                defaultValue={new URLSearchParams(window.location.search).get("reset_token") ?? ""}
                autoComplete="one-time-code"
                required
              />
              <input
                name="password"
                type="password"
                placeholder="New password"
                autoComplete="new-password"
                required
              />
              <button className="primary-button" type="submit">
                Reset password
              </button>
            </form>
          </div>

          <div
            className={`modal-view${authView === "settings" ? " visible" : ""}`}
            data-auth-view="settings"
          >
            <div className="settings-panel">
              <div className="settings-tabs" role="tablist" aria-label="Settings sections">
                {SETTINGS_TABS.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    id={`settings-tab-${tab.id}`}
                    className="settings-tab"
                    aria-selected={activeSettingsTab === tab.id}
                    aria-controls={`settings-panel-${tab.id}`}
                    tabIndex={activeSettingsTab === tab.id ? 0 : -1}
                    onClick={() => setActiveSettingsTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              <div className="settings-tab-panels">
                {flash ? (
                  <ModalFlash message={flash} kind={flashKind} className="settings-modal-flash" />
                ) : null}
                {activeSettingsTab === "inference" ? (
                  <section
                    className="settings-section"
                    role="tabpanel"
                    id="settings-panel-inference"
                    aria-labelledby="settings-tab-inference"
                  >
                    <SettingsSectionHeading
                      title="Inference"
                      caption="Manage Anthropic inference API key."
                    />
                    {sessionSummary?.hasAnthropicApiKey ? (
                      <div className="settings-secret-row">
                        <div className="settings-secret-copy">
                          <div className="settings-secret-label">Anthropic API key</div>
                          <div className="settings-secret-value">
                            {sessionSettings.anthropic_api_key_hint || "Saved"}
                          </div>
                        </div>
                        <button
                          id="delete-anthropic-key-button"
                          className="api-key-delete-button"
                          type="button"
                          aria-label="Delete saved Anthropic API key"
                          title="Delete saved Anthropic API key"
                          onClick={async () => {
                            try {
                              const nextSession = await api<BrowserSessionResponse>(
                                "/v1/browser/settings/anthropic-key",
                                { method: "DELETE" },
                              );
                              setSession(nextSession);
                              showNoteFlash("Anthropic API key removed.");
                            } catch (error) {
                              showErrorFlash(
                                error instanceof Error ? error.message : "Deleting API key failed",
                              );
                            }
                          }}
                        >
                          <AppIcon
                            src={staticAssetPath("delete-session.svg")}
                            className="api-key-delete-icon"
                          />
                        </button>
                      </div>
                    ) : (
                      <form
                        id="settings-anthropic-form"
                        className="settings-form"
                        onSubmit={async (event) => {
                          event.preventDefault();
                          try {
                            const payload = Object.fromEntries(
                              new FormData(event.currentTarget).entries(),
                            );
                            const nextSession = await api<BrowserSessionResponse>(
                              "/v1/browser/settings/anthropic-key",
                              {
                                method: "PUT",
                                body: JSON.stringify(payload),
                              },
                            );
                            setSession(nextSession);
                            showNoteFlash("Anthropic API key saved.");
                          } catch (error) {
                            showErrorFlash(
                              error instanceof Error ? error.message : "Saving API key failed",
                            );
                          }
                        }}
                      >
                        <input
                          name="api_key"
                          type="password"
                          placeholder="Anthropic API key"
                          autoComplete="new-password"
                          required
                        />
                        <div className="settings-footer-actions">
                          <button className="primary-button settings-submit-button" type="submit">
                            Save key
                          </button>
                        </div>
                      </form>
                    )}
                  </section>
                ) : null}

                {activeSettingsTab === "sandbox" ? (
                  <section
                    className="settings-section"
                    role="tabpanel"
                    id="settings-panel-sandbox"
                    aria-labelledby="settings-tab-sandbox"
                  >
                    <SettingsSectionHeading
                      title="Sandbox"
                      caption="Manage Modal sandbox credentials."
                    />
                    {sessionSettings.has_modal_credentials ? (
                      <div className="settings-secret-row">
                        <div className="settings-secret-copy">
                          <div className="settings-secret-label">Modal token ID</div>
                          <div className="settings-secret-value">
                            {sessionSettings.modal_token_id_hint || "Saved"}
                          </div>
                        </div>
                        <button
                          id="delete-modal-credentials-button"
                          className="api-key-delete-button"
                          type="button"
                          aria-label="Delete saved Modal credentials"
                          title="Delete saved Modal credentials"
                          onClick={async () => {
                            try {
                              const nextSession = await api<BrowserSessionResponse>(
                                "/v1/browser/settings/modal-credentials",
                                { method: "DELETE" },
                              );
                              setSession(nextSession);
                              showNoteFlash("Modal credentials removed.");
                            } catch (error) {
                              showErrorFlash(
                                error instanceof Error
                                  ? error.message
                                  : "Deleting Modal credentials failed",
                              );
                            }
                          }}
                        >
                          <AppIcon
                            src={staticAssetPath("delete-session.svg")}
                            className="api-key-delete-icon"
                          />
                        </button>
                      </div>
                    ) : (
                      <form
                        id="settings-modal-form"
                        className="settings-form"
                        onSubmit={async (event) => {
                          event.preventDefault();
                          try {
                            const payload = Object.fromEntries(
                              new FormData(event.currentTarget).entries(),
                            );
                            const nextSession = await api<BrowserSessionResponse>(
                              "/v1/browser/settings/modal-credentials",
                              {
                                method: "PUT",
                                body: JSON.stringify(payload),
                              },
                            );
                            setSession(nextSession);
                            showNoteFlash("Modal credentials saved.");
                          } catch (error) {
                            showErrorFlash(
                              error instanceof Error
                                ? error.message
                                : "Saving Modal credentials failed",
                            );
                          }
                        }}
                      >
                        <input
                          name="token_id"
                          type="password"
                          placeholder="Modal token ID"
                          autoComplete="new-password"
                          required
                        />
                        <input
                          name="token_secret"
                          type="password"
                          placeholder="Modal token secret"
                          autoComplete="new-password"
                          required
                        />
                        <div className="settings-footer-actions">
                          <button className="primary-button settings-submit-button" type="submit">
                            Save credentials
                          </button>
                        </div>
                      </form>
                    )}
                  </section>
                ) : null}

                {activeSettingsTab === "version-control" ? (
                  <section
                    className="settings-section"
                    role="tabpanel"
                    id="settings-panel-version-control"
                    aria-labelledby="settings-tab-version-control"
                  >
                    <SettingsSectionHeading
                      title="Version control"
                      caption="Manage GitHub connection for repositories."
                    />
                    {sessionSettings.has_github_connection ? (
                      <div className="settings-connected-card">
                        <div>
                          <div className="settings-secret-label">GitHub account</div>
                          <div className="settings-secret-value">
                            {sessionSettings.github_login || "Connected"}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={async () => {
                            try {
                              const nextSession = await api<BrowserSessionResponse>(
                                "/v1/browser/settings/github",
                                { method: "DELETE" },
                              );
                              setSession(nextSession);
                              showNoteFlash("GitHub disconnected.");
                            } catch (error) {
                              showErrorFlash(
                                error instanceof Error
                                  ? error.message
                                  : "Disconnecting GitHub failed",
                              );
                            }
                          }}
                        >
                          Disconnect
                        </button>
                      </div>
                    ) : (
                      <div className="settings-actions">
                        <div className="settings-footer-actions">
                          <button
                            type="button"
                            className="primary-button settings-connect-button"
                            disabled={!sessionSettings.github_oauth_available}
                            onClick={() => {
                              window.location.href = githubLoginUrl();
                            }}
                          >
                            Connect GitHub account
                          </button>
                        </div>
                        {!sessionSettings.github_oauth_available ? (
                          <p className="settings-inline-note">
                            GitHub OAuth is not configured in this environment yet.
                          </p>
                        ) : null}
                      </div>
                    )}
                  </section>
                ) : null}

                {activeSettingsTab === "appearance" ? (
                  <section
                    className="settings-section"
                    role="tabpanel"
                    id="settings-panel-appearance"
                    aria-labelledby="settings-tab-appearance"
                  >
                    <SettingsSectionHeading
                      title="Appearance"
                      caption="Choose the theme for signed-in pages."
                    />
                    <form
                      className="settings-form"
                      onSubmit={async (event) => {
                        event.preventDefault();
                        try {
                          const nextSession = await api<BrowserSessionResponse>(
                            "/v1/browser/settings/appearance",
                            {
                              method: "PUT",
                              body: JSON.stringify({ theme: appearanceThemeDraft }),
                            },
                          );
                          setSession(nextSession);
                          showNoteFlash(
                            appearanceThemeDraft === "dark"
                              ? "Dark mode enabled."
                              : "Light mode enabled.",
                          );
                        } catch (error) {
                          showErrorFlash(
                            error instanceof Error
                              ? error.message
                              : "Saving appearance settings failed",
                          );
                        }
                      }}
                    >
                      <div
                        className="settings-appearance-grid"
                        role="radiogroup"
                        aria-label="Appearance theme"
                      >
                        {APPEARANCE_OPTIONS.map((option) => {
                          const selected = appearanceThemeDraft === option.id;
                          return (
                            <button
                              key={option.id}
                              type="button"
                              role="radio"
                              aria-checked={selected}
                              className={`settings-appearance-option${selected ? " selected" : ""}`}
                              onClick={() => setAppearanceThemeDraft(option.id)}
                            >
                              <span
                                className={`settings-appearance-preview ${option.id}`}
                                aria-hidden="true"
                              >
                                <span className="settings-appearance-preview-header" />
                                <span className="settings-appearance-preview-body">
                                  <span className="settings-appearance-preview-sidebar" />
                                  <span className="settings-appearance-preview-main" />
                                </span>
                              </span>
                              <span className="settings-appearance-copy">
                                <span className="settings-appearance-label">{option.label}</span>
                                <span className="settings-appearance-caption">
                                  {option.caption}
                                </span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                      <p className="settings-inline-note">
                        Applies only to the signed-in workspace and its modals.
                      </p>
                      <div className="settings-footer-actions">
                        <button
                          className="primary-button settings-submit-button"
                          type="submit"
                          disabled={appearanceThemeDraft === appliedAppearanceTheme}
                        >
                          Save appearance
                        </button>
                      </div>
                    </form>
                  </section>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
