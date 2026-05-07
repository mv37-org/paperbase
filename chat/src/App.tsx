import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  ChangeEvent,
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  UIEvent,
} from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";

import {
  api,
  createPythonFile,
  createProject,
  createThread,
  deleteProject,
  deletePythonFile,
  deleteThread,
  getBrowserPaper,
  getBrowserPaperPreview,
  getBrowserPaperFigure,
  getBrowserPaperFile,
  getBrowserPaperTable,
  getBootstrapSession,
  getProjectRepoSync,
  getPythonFile,
  githubLoginUrl,
  importProjectNotebook,
  getThread,
  loadGitHubRepositories,
  loadGitHubRepoImportCandidates,
  loadGitHubRepoMetadata,
  loadProjectItems,
  loadProjects,
  loadSession,
  loadUploads,
  moveThreadToProject,
  renameThread,
  logoutBrowserSession,
  postForm,
  runPythonFile,
  sendThreadMessage,
  sendThreadMessageStream,
  submitChatFeedback,
  suggestBrowserPapers,
  StreamingChatAbortError,
  StreamingChatError,
  syncProjectRepo,
  updateProject,
  updatePythonFile,
  uploadDocument,
} from "./api";
import { AdminOpsPanel } from "./AdminOpsPanel";
import {
  AppIcon,
  EmptyStateActionIcon,
  GoogleButton,
  MaskedAssetIcon,
  ModalFlash,
  SettingsSectionHeading,
} from "./app/AppChrome";
import { ChatSessionModals } from "./app/ChatSessionModals";
import { ChatThreadHeader } from "./app/ChatThreadHeader";
import { PdfDocumentViewer } from "./app/PdfDocumentViewer";
import { ThreadPapersView } from "./app/ThreadPapersView";
import type {
  AppearanceTheme,
  DeleteModalState,
  ExportFormat,
  ExportModalState,
  FeedbackModalState,
  FeedbackSentiment,
  ModalFlashKind,
  OpenTab,
  PaperActionTarget,
  PaperCitationPreviewState,
  PaperDrawerContent,
  PaperDrawerState,
  PaperExploration,
  PaperPdfModalState,
  ProjectItemState,
  ProjectModalState,
  ProjectSidebarCategoryKey,
  PythonFileDraft,
  PythonFileRunState,
  SaveIntent,
  SaveModalState,
  SettingsTab,
  SyncConflictState,
  ThreadPane,
  ThreadStreamState,
  ToolActivityEntry,
  WorkspaceView,
} from "./app/appTypes";
import {
  ACTIVE_TAB_KEY,
  apiKeyStateMessage,
  buildThreadDebugBundle,
  clearStoredActiveTab,
  consumePendingPrompt,
  contextUsageButtonLabel,
  contextUsagePercent,
  createLocalMessage,
  displayPythonFilePathContext,
  displayPythonFileTitle,
  displayThreadTitle,
  downloadTextFile,
  drawerStateFromUrl,
  duplicatePythonFilenameKeys,
  formatContextUsageSummary,
  formatContextUsageTokenSummary,
  formatThinkingLabel,
  formatThreadAge,
  formatToolActivityText,
  groupCitationsByPaper,
  DISMISSED_CHAT_SETUP_KEY,
  inferDrawerContentKind,
  isEmptyThread,
  isLocalDebugHost,
  isMessageListNearBottom,
  isStreamingMessageStatus,
  LEGACY_OPEN_TABS_KEY,
  localDateString,
  mentionedPaperIdsInText,
  mergeProjectItems,
  messageFromThreadItem,
  messageHasProjectCodeIntent,
  normalizeThreadForState,
  pickThinkingWord,
  projectFileKindLabel,
  projectItemFromPythonFile,
  projectItemFromThread,
  projectItemKindCount,
  projectRepoConflictFromError,
  pythonFilenameFromRepoPath,
  pythonRepoPathForFile,
  rememberPendingPrompt,
  restoreActiveTab,
  restoreDismissedChatSetupSignatures,
  restoreSelectedProjectId,
  restoreSidebarCollapsed,
  SELECTED_PROJECT_KEY,
  shouldShowThreadInSidebar,
  SIDEBAR_COLLAPSED_KEY,
  sourceCardId,
  staticAssetPath,
  summarizeThreadTitle,
  threadMarkdownExport,
  toolActivityCommandForItem,
  toolActivityHasDetails,
  toolActivityLabelForItem,
  uniqueCitationsForThread,
  updateDrawerUrl,
  updateTrendingBrowseUrl,
  upsertProjectItem,
  trendingBrowseStateFromUrl,
} from "./app/appUtils";
import { CredentialFormUsernameField } from "./CredentialFormUsernameField";
import { CodeSnippet, extractStreamingPythonPreview } from "./CodeSnippet";
import { LandingMentionEditor } from "./LandingMentionEditor";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { formatLocalTimestamp } from "./time";
import { TrendingPapersPage, type TrendingBrowseState } from "./TrendingPapersPage";
import type {
  AuthView,
  BrowserSessionResponse,
  ChatCitation,
  ChatMessageResponse,
  ChatStreamDoneEvent,
  ChatThreadItemResponse,
  ChatStreamUsageEvent,
  ChatThreadResponse,
  GitHubRepoImportCandidate,
  GitHubRepoMetadataResponse,
  GitHubRepoSummary,
  MentionSuggestion,
  MentionTag,
  PaperResponse,
  ProjectItemKind,
  ProjectRepoConflictResolution,
  ProjectSummary,
  PythonFileResponse,
  SendChatMessageResponse,
} from "./types";

function chatMessageRowId(messageId: string) {
  return `chat-message-${messageId.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

type ThreadRenameModalState = {
  threadId: string;
  title: string;
  draft: string;
  saving: boolean;
  error: string;
};

const RESUME_PROJECT_MODAL_KEY = "paperbase-resume-new-project";
const LANDING_TRENDING_PAGE_SIZE = 10;
const PROJECT_ITEM_PAGE_SIZE = 5;
const PROJECT_SIDEBAR_ITEM_KINDS: ProjectItemKind[] = ["python_file", "thread"];
const PAPER_EXPLORATIONS_KEY = "paperbase-paper-explorations";
const PAPER_EXPLORATION_OUTLINE_ITEMS = [
  "Abstract",
  "Introduction",
  "Method",
  "Experiments & results",
  "Conclusion",
  "References",
];
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
  openai_api_key_hint: "",
  anthropic_api_key_hint: "",
  has_modal_credentials: false,
  modal_token_id_hint: "",
  has_github_connection: false,
  github_login: "",
  github_oauth_available: false,
  github_manage_access_url: "https://github.com/settings/connections/applications",
  weekly_papers_review_email: false,
  appearance_theme: "light",
};

const BASE_SETTINGS_TABS: Array<{
  id: SettingsTab;
  label: string;
  caption: string;
}> = [
  {
    id: "inference",
    label: "Inference",
    caption: "Manage OpenAI inference API key.",
  },
  {
    id: "sandbox",
    label: "Sandbox",
    caption: "Manage Modal sandbox credentials.",
  },
  {
    id: "version-control",
    label: "GitHub",
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
const PROJECT_SIDEBAR_CATEGORY_KEYS: ProjectSidebarCategoryKey[] = ["notes", "sessions", "code"];
const buildProjectCategoryCollapseState = (collapsed: boolean) =>
  Object.fromEntries(PROJECT_SIDEBAR_CATEGORY_KEYS.map((key) => [key, collapsed])) as Partial<
    Record<ProjectSidebarCategoryKey, boolean>
  >;
const WORKSPACE_SIDEBAR_ITEMS = [
  { id: "trending", label: "Trending papers", asset: "docs.svg" },
  { id: "admin_jobs", label: "Control panel", asset: "settings.svg", adminOnly: true },
] satisfies Array<{
  id: "trending" | "admin_jobs";
  label: string;
  asset: string;
  adminOnly?: boolean;
}>;

function timestampMs(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatWorkedDuration(startMs: number | null, endMs: number | null) {
  if (startMs === null || endMs === null) {
    return "a moment";
  }
  const totalSeconds = Math.max(1, Math.round(Math.max(0, endMs - startMs) / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds} ${totalSeconds === 1 ? "second" : "seconds"}`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  if (totalMinutes < 60) {
    return remainingSeconds > 0
      ? `${totalMinutes} ${totalMinutes === 1 ? "min" : "mins"} ${remainingSeconds} sec`
      : `${totalMinutes} ${totalMinutes === 1 ? "min" : "mins"}`;
  }
  const totalHours = Math.floor(totalMinutes / 60);
  const remainingMinutes = totalMinutes % 60;
  return remainingMinutes > 0
    ? `${totalHours} ${totalHours === 1 ? "hour" : "hours"} ${remainingMinutes} min`
    : `${totalHours} ${totalHours === 1 ? "hour" : "hours"}`;
}

function paperExplorationIdFor(projectId: string, paperId: string) {
  return `paper-exploration:${projectId}:${paperId}`;
}

function splitPaperAuthors(authors: string) {
  return authors
    .split(/\s*(?:,|\band\b)\s*/i)
    .map((author) => author.trim())
    .filter(Boolean);
}

function authorInitials(author: string) {
  const parts = author
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
  return (parts[0]?.[0] ?? "").concat(parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "");
}

function normalizePaperExploration(value: unknown): PaperExploration | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const entry = value as Partial<Record<keyof PaperExploration, unknown>>;
  const explorationId = typeof entry.explorationId === "string" ? entry.explorationId : "";
  const projectId = typeof entry.projectId === "string" ? entry.projectId : "";
  const paperId = typeof entry.paperId === "string" ? entry.paperId : "";
  const path = typeof entry.path === "string" ? entry.path : "";
  const title = typeof entry.title === "string" ? entry.title : "";
  if (!explorationId || !projectId || !paperId || !title) {
    return null;
  }
  return {
    explorationId,
    projectId,
    paperId,
    path,
    title,
    authors: typeof entry.authors === "string" ? entry.authors : "",
    previewImageUrl: typeof entry.previewImageUrl === "string" ? entry.previewImageUrl : "",
    pdfUrl: typeof entry.pdfUrl === "string" ? entry.pdfUrl : "",
    createdAt: typeof entry.createdAt === "string" ? entry.createdAt : new Date().toISOString(),
    updatedAt: typeof entry.updatedAt === "string" ? entry.updatedAt : new Date().toISOString(),
  };
}

function restorePaperExplorations() {
  try {
    const value = localStorage.getItem(PAPER_EXPLORATIONS_KEY);
    if (!value) {
      return [];
    }
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .map((entry) => normalizePaperExploration(entry))
      .filter((entry): entry is PaperExploration => Boolean(entry));
  } catch {
    return [];
  }
}

function persistPaperExplorations(explorations: PaperExploration[]) {
  if (explorations.length === 0) {
    localStorage.removeItem(PAPER_EXPLORATIONS_KEY);
    return;
  }
  localStorage.setItem(PAPER_EXPLORATIONS_KEY, JSON.stringify(explorations));
}

function sourceCountLabel(count: number) {
  return `${count} ${count === 1 ? "source" : "sources"}`;
}

function paperSuggestionMeta(paper: MentionSuggestion) {
  const parts = [];
  if (paper.arxiv_id.trim()) {
    parts.push(`arXiv ${paper.arxiv_id}`);
  }
  if (paper.published) {
    parts.push(paper.published.slice(0, 4));
  }
  if (paper.citation_count > 0) {
    parts.push(`${paper.citation_count} cites`);
  }
  return parts.join(" · ");
}

export default function App() {
  const bootstrapSession = useMemo(() => getBootstrapSession(), []);
  const [session, setSession] = useState<BrowserSessionResponse | null>(bootstrapSession);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectItems, setProjectItems] = useState<Record<string, ProjectItemState>>({});
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(() =>
    restoreSelectedProjectId(),
  );
  const [expandedProjectIds, setExpandedProjectIds] = useState<string[]>(() => {
    const restoredProjectId = restoreSelectedProjectId();
    return restoredProjectId ? [restoredProjectId] : [];
  });
  const [collapsedProjectCategories, setCollapsedProjectCategories] = useState<
    Record<string, Partial<Record<ProjectSidebarCategoryKey, boolean>>>
  >({});
  const [threadMap, setThreadMap] = useState<Record<string, ChatThreadResponse>>({});
  const [pythonFileMap, setPythonFileMap] = useState<Record<string, PythonFileResponse>>({});
  const [pythonFileDrafts, setPythonFileDrafts] = useState<Record<string, PythonFileDraft>>({});
  const [pythonFileRuns, setPythonFileRuns] = useState<Record<string, PythonFileRunState>>({});
  const [activeTab, setActiveTab] = useState<OpenTab | null>(null);
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView | null>(null);
  const [projectMenuProjectId, setProjectMenuProjectId] = useState<string | null>(null);
  const [projectModalState, setProjectModalState] = useState<ProjectModalState | null>(null);
  const [projectFormName, setProjectFormName] = useState("");
  const [projectFormDescription, setProjectFormDescription] = useState("");
  const [projectFormError, setProjectFormError] = useState("");
  const [projectRepoMode, setProjectRepoMode] = useState<"" | "create" | "existing">("");
  const [projectRepoOwner, setProjectRepoOwner] = useState("");
  const [projectRepoName, setProjectRepoName] = useState("");
  const [projectRepoBranch, setProjectRepoBranch] = useState("main");
  const [projectRepoMetadata, setProjectRepoMetadata] = useState<GitHubRepoMetadataResponse | null>(
    null,
  );
  const [projectRepoCandidates, setProjectRepoCandidates] = useState<GitHubRepoImportCandidate[]>(
    [],
  );
  const [projectRepoSelectedPaths, setProjectRepoSelectedPaths] = useState<string[]>([]);
  const [projectRepoImportAll, setProjectRepoImportAll] = useState(true);
  const [projectRepoList, setProjectRepoList] = useState<GitHubRepoSummary[]>([]);
  const [projectRepoLoading, setProjectRepoLoading] = useState(false);
  const [projectRepoListLoading, setProjectRepoListLoading] = useState(false);
  const [projectRepoStatus, setProjectRepoStatus] = useState("");
  const [projectRepoDropdownOpen, setProjectRepoDropdownOpen] = useState(false);
  const [projectAdvancedOpen, setProjectAdvancedOpen] = useState(false);
  const [projectAdvancedTab, setProjectAdvancedTab] = useState<"inference" | "sandbox">(
    "inference",
  );
  const [projectAdvancedStatus, setProjectAdvancedStatus] = useState("");
  const [projectAnthropicKey, setProjectAnthropicKey] = useState("");
  const [projectModalTokenId, setProjectModalTokenId] = useState("");
  const [projectModalTokenSecret, setProjectModalTokenSecret] = useState("");
  const [projectAdvancedSaving, setProjectAdvancedSaving] = useState<
    "inference" | "sandbox" | null
  >(null);
  const [deleteModalState, setDeleteModalState] = useState<DeleteModalState>(null);
  const [saveModalState, setSaveModalState] = useState<SaveModalState>(null);
  const [threadDebugModalOpen, setThreadDebugModalOpen] = useState(false);
  const [threadPane, setThreadPane] = useState<ThreadPane>("chat");
  const [chatHeaderMenuOpen, setChatHeaderMenuOpen] = useState(false);
  const [threadRenameModal, setThreadRenameModal] = useState<ThreadRenameModalState | null>(null);
  const [exportModalState, setExportModalState] = useState<ExportModalState>(null);
  const [feedbackModalState, setFeedbackModalState] = useState<FeedbackModalState>(null);
  const [feedbackReasons, setFeedbackReasons] = useState<string[]>([]);
  const [feedbackNote, setFeedbackNote] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [syncConflictState, setSyncConflictState] = useState<SyncConflictState>(null);
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authReady, setAuthReady] = useState(Boolean(bootstrapSession));
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTab>("inference");
  const [appearanceThemeDraft, setAppearanceThemeDraft] = useState<AppearanceTheme>("light");
  const [flash, setFlash] = useState("");
  const [flashKind, setFlashKind] = useState<ModalFlashKind>("note");
  const [landingPrompt, setLandingPrompt] = useState("");
  const [composerInput, setComposerInput] = useState("");
  const [composerTags, setComposerTags] = useState<MentionTag[]>([]);
  const [landingTags, setLandingTags] = useState<MentionTag[]>([]);
  const [restoredComposerMentions, setRestoredComposerMentions] = useState<MentionTag[]>([]);
  const [composerStatus, setComposerStatus] = useState("");
  const [includeUploads] = useState(true);
  const [includePapers] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [paperExplorationSidebarCollapsed, setPaperExplorationSidebarCollapsed] = useState(true);
  const [paperExplorationSidebarPane, setPaperExplorationSidebarPane] = useState<
    "details" | "notes"
  >("details");
  const [paperExplorationNotes, setPaperExplorationNotes] = useState<Record<string, string>>({});
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [emptyStateProjectPickerOpen, setEmptyStateProjectPickerOpen] = useState(false);
  const [emptyStateProjectSearch, setEmptyStateProjectSearch] = useState("");
  const [dismissedChatSetupSignatures, setDismissedChatSetupSignatures] = useState<
    Record<string, string>
  >(() => restoreDismissedChatSetupSignatures());
  const notebookUploadInputRef = useRef<HTMLInputElement | null>(null);
  const [streamingThreadId, setStreamingThreadId] = useState<string | null>(null);
  const [threadStreamStates, setThreadStreamStates] = useState<Record<string, ThreadStreamState>>(
    {},
  );
  const [expandedCitationMessages, setExpandedCitationMessages] = useState<Record<string, boolean>>(
    {},
  );
  const [threadUsageSnapshots, setThreadUsageSnapshots] = useState<
    Record<string, ChatStreamUsageEvent>
  >({});
  const [toolActivities, setToolActivities] = useState<Record<string, ToolActivityEntry[]>>({});
  const [contextUsagePopoverOpen, setContextUsagePopoverOpen] = useState(false);
  const [paperDrawer, setPaperDrawer] = useState<PaperDrawerState>(() => drawerStateFromUrl());
  const [paperPdfModal, setPaperPdfModal] = useState<PaperPdfModalState | null>(null);
  const [paperExploreMenuOpen, setPaperExploreMenuOpen] = useState(false);
  const [paperExplorations, setPaperExplorations] = useState<PaperExploration[]>(() =>
    restorePaperExplorations(),
  );
  const [explorationPicker, setExplorationPicker] = useState<{
    projectId: string;
    projectName: string;
  } | null>(null);
  const [explorationSearch, setExplorationSearch] = useState("");
  const [explorationSuggestions, setExplorationSuggestions] = useState<MentionSuggestion[]>([]);
  const [explorationSearchStatus, setExplorationSearchStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [explorationSearchFallback, setExplorationSearchFallback] = useState(false);
  const [explorationSearchError, setExplorationSearchError] = useState("");
  const [explorationSelectedIndex, setExplorationSelectedIndex] = useState(0);
  const [explorationStartingPaperId, setExplorationStartingPaperId] = useState("");
  const [trendingBrowseState, setTrendingBrowseState] = useState<TrendingBrowseState | null>(() =>
    trendingBrowseStateFromUrl(),
  );
  const [landingTrendingBrowseState, setLandingTrendingBrowseState] = useState<TrendingBrowseState>(
    () => ({
      date: localDateString(),
      sort: "default",
      page: 1,
      paperId: null,
    }),
  );
  const [paperDrawerPaper, setPaperDrawerPaper] = useState<PaperResponse | null>(null);
  const [paperDrawerContent, setPaperDrawerContent] = useState<PaperDrawerContent | null>(null);
  const [paperDrawerStatus, setPaperDrawerStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [paperDrawerError, setPaperDrawerError] = useState("");
  const [paperCitationPreviews, setPaperCitationPreviews] = useState<
    Record<string, PaperCitationPreviewState>
  >({});
  const [messageListAtBottom, setMessageListAtBottom] = useState(true);

  const sessionRef = useRef<BrowserSessionResponse | null>(null);
  const projectsRef = useRef<ProjectSummary[]>([]);
  const selectedProjectIdRef = useRef<string | null>(restoreSelectedProjectId());
  const projectItemsRef = useRef<Record<string, ProjectItemState>>({});
  const paperCitationPreviewsRef = useRef<Record<string, PaperCitationPreviewState>>({});
  const paperExplorationsRef = useRef<PaperExploration[]>(paperExplorations);
  const paperCitationPreviewFetchesRef = useRef(new Set<string>());
  const threadMapRef = useRef<Record<string, ChatThreadResponse>>({});
  const pythonFileMapRef = useRef<Record<string, PythonFileResponse>>({});
  const pythonFileDraftsRef = useRef<Record<string, PythonFileDraft>>({});
  const activeTabStorageHydratedRef = useRef(false);
  const appUploadRef = useRef<HTMLInputElement | null>(null);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const contextUsageMenuRef = useRef<HTMLDivElement | null>(null);
  const emptyStateProjectPickerRef = useRef<HTMLDivElement | null>(null);
  const emptyStateProjectSearchInputRef = useRef<HTMLInputElement | null>(null);
  const explorationSearchInputRef = useRef<HTMLInputElement | null>(null);
  const projectMenuRef = useRef<HTMLDivElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const pendingTurnAnchorMessageIdRef = useRef<string | null>(null);
  const skipNextAutoBottomScrollRef = useRef(false);
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
    if (selectedProjectId) {
      localStorage.setItem(SELECTED_PROJECT_KEY, selectedProjectId);
      return;
    }
    localStorage.removeItem(SELECTED_PROJECT_KEY);
  }, [selectedProjectId]);

  useEffect(() => {
    projectItemsRef.current = projectItems;
  }, [projectItems]);

  useEffect(() => {
    paperCitationPreviewsRef.current = paperCitationPreviews;
  }, [paperCitationPreviews]);

  useEffect(() => {
    paperExplorationsRef.current = paperExplorations;
    persistPaperExplorations(paperExplorations);
  }, [paperExplorations]);

  useEffect(() => {
    if (!explorationPicker) {
      return;
    }
    window.requestAnimationFrame(() => {
      explorationSearchInputRef.current?.focus();
    });
  }, [explorationPicker]);

  useEffect(() => {
    if (!explorationPicker) {
      return;
    }

    const query = explorationSearch.trim();
    if (query.length < 2) {
      setExplorationSuggestions([]);
      setExplorationSearchStatus("idle");
      setExplorationSearchFallback(false);
      setExplorationSearchError("");
      setExplorationSelectedIndex(0);
      return;
    }

    let stale = false;
    setExplorationSearchStatus("loading");
    setExplorationSearchError("");
    const timeoutId = window.setTimeout(() => {
      suggestBrowserPapers(query, 8)
        .then((result) => {
          if (stale) {
            return;
          }
          setExplorationSuggestions(result.suggestions);
          setExplorationSearchFallback(result.fallback);
          setExplorationSearchStatus("ready");
          setExplorationSelectedIndex(0);
        })
        .catch((error) => {
          if (stale) {
            return;
          }
          setExplorationSuggestions([]);
          setExplorationSearchFallback(false);
          setExplorationSearchStatus("error");
          setExplorationSearchError(
            error instanceof Error ? error.message : "Paper search failed.",
          );
        });
    }, 140);

    return () => {
      stale = true;
      window.clearTimeout(timeoutId);
    };
  }, [explorationPicker, explorationSearch]);

  useEffect(() => {
    setExplorationSelectedIndex((current) =>
      explorationSuggestions.length === 0
        ? 0
        : Math.min(current, explorationSuggestions.length - 1),
    );
  }, [explorationSuggestions.length]);

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
    if (!activeTabStorageHydratedRef.current) {
      return;
    }
    if (activeTab) {
      localStorage.setItem(ACTIVE_TAB_KEY, JSON.stringify(activeTab));
    } else {
      localStorage.removeItem(ACTIVE_TAB_KEY);
    }
    localStorage.removeItem(LEGACY_OPEN_TABS_KEY);
  }, [activeTab]);

  useEffect(() => {
    if (activeTab !== null) {
      setWorkspaceView(null);
      if (trendingBrowseState) {
        setTrendingBrowseState(null);
        updateTrendingBrowseUrl(null);
      }
    }
  }, [activeTab, trendingBrowseState]);

  useEffect(() => {
    setSidebarCollapsed(restoreSidebarCollapsed());
  }, []);

  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (activeTab?.kind !== "paper_exploration") {
      setPaperExplorationSidebarCollapsed(true);
    }
  }, [activeTab?.kind]);

  useEffect(() => {
    localStorage.setItem(DISMISSED_CHAT_SETUP_KEY, JSON.stringify(dismissedChatSetupSignatures));
  }, [dismissedChatSetupSignatures]);

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
  const localThreadDebugEnabled = useMemo(() => isLocalDebugHost(), []);
  const activeThreadId = activeTab?.kind === "thread" ? activeTab.id : null;
  const activePythonFileId = activeTab?.kind === "python_file" ? activeTab.id : null;
  const activeProjectHomeId = activeTab?.kind === "project_home" ? activeTab.id : null;
  const activePaperExplorationId = activeTab?.kind === "paper_exploration" ? activeTab.id : null;
  const activeWorkspacePageId =
    workspaceView?.kind === "admin_jobs" || workspaceView?.kind === "admin_job_papers"
      ? "admin_jobs"
      : workspaceView?.kind === "trending" || trendingBrowseState
        ? "trending"
        : null;
  const activeThread = activeThreadId ? (threadMap[activeThreadId] ?? null) : null;
  const activePythonFile = activePythonFileId ? (pythonFileMap[activePythonFileId] ?? null) : null;
  const activeProjectHome = activeProjectHomeId
    ? (projects.find((project) => project.project_id === activeProjectHomeId) ?? null)
    : null;
  const activePaperExploration = activePaperExplorationId
    ? (paperExplorations.find(
        (exploration) => exploration.explorationId === activePaperExplorationId,
      ) ?? null)
    : null;
  const activePythonDraft = activePythonFileId
    ? (pythonFileDrafts[activePythonFileId] ?? null)
    : null;
  const activePythonRun = activePythonFileId
    ? (pythonFileRuns[activePythonFileId] ?? { running: false, result: null, error: "" })
    : { running: false, result: null, error: "" };
  const activeThreadPaperIds = useMemo(() => {
    if (!activeThread) {
      return [];
    }
    return [
      ...new Set(
        activeThread.messages
          .flatMap((message) => message.citations)
          .map((citation) => citation.paper_id ?? "")
          .filter((paperId) => paperId.trim().length > 0),
      ),
    ];
  }, [activeThread]);
  const activeThreadCitations = useMemo(
    () => uniqueCitationsForThread(activeThread),
    [activeThread],
  );
  const activeThreadDebugBundle = useMemo(
    () =>
      activeThread && localThreadDebugEnabled
        ? buildThreadDebugBundle(activeThread, session?.user?.user_id ?? null)
        : null,
    [activeThread, localThreadDebugEnabled, session?.user?.user_id],
  );
  const activePythonContent = activePythonDraft?.content ?? activePythonFile?.content ?? "";
  const activeProjectHomeItems = activeProjectHomeId
    ? (projectItems[activeProjectHomeId]?.items ?? [])
    : [];
  const activeProjectHomeCodeCount = activeProjectHomeItems.filter(
    (item) => item.item_kind === "python_file",
  ).length;
  const activeProjectHomeSessionCount = activeProjectHomeItems.filter(
    (item) => item.item_kind === "thread",
  ).length;
  const activeProjectHomeExplorationCount = activeProjectHomeId
    ? paperExplorations.filter((exploration) => exploration.projectId === activeProjectHomeId)
        .length
    : 0;
  const activePaperExplorationProject = activePaperExploration
    ? (projects.find((project) => project.project_id === activePaperExploration.projectId) ?? null)
    : null;
  const activePaperExplorationAuthors = useMemo(
    () => splitPaperAuthors(activePaperExploration?.authors ?? ""),
    [activePaperExploration?.authors],
  );
  const paperExplorationIdsByPaperId = useMemo(
    () =>
      Object.fromEntries(
        paperExplorations.map((exploration) => [exploration.paperId, exploration.explorationId]),
      ) as Record<string, string>,
    [paperExplorations],
  );
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
    activeTab == null
      ? workspaceView == null
      : activeTab.kind === "thread" && (!activeThread || activeThread.messages.length === 0);
  const showThreadDebugButton = Boolean(
    activeTab?.kind === "thread" && activeThreadDebugBundle && !isChatEmpty,
  );
  const paperExplorationSidebarOpen = Boolean(
    activeTab?.kind === "paper_exploration" &&
    activePaperExploration &&
    !paperExplorationSidebarCollapsed,
  );
  const paperDrawerOpen = Boolean(paperDrawer && !paperExplorationSidebarOpen);
  const hasProjects = projects.length > 0;
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
  const isWorkspaceView = workspaceView !== null;
  const isAdminWorkspaceView =
    workspaceView?.kind === "admin_jobs" || workspaceView?.kind === "admin_job_papers";
  const activeThreadHasAssistantReply = Boolean(
    activeThread?.messages.some(
      (message) => message.role === "assistant" && message.kind !== "compaction",
    ),
  );
  const showChatThreadHeader = Boolean(
    activeTab?.kind === "thread" && activeThread && activeThreadHasAssistantReply,
  );
  const showThreadStageChrome =
    !isWorkspaceView && activeTab?.kind === "thread" && threadPane === "chat" && !isChatEmpty;
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
    if (!showThreadDebugButton) {
      setThreadDebugModalOpen(false);
    }
  }, [showThreadDebugButton]);

  useEffect(() => {
    if (!threadDebugModalOpen) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setThreadDebugModalOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [threadDebugModalOpen]);

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
    if (authenticated) {
      return;
    }
    paperCitationPreviewFetchesRef.current.clear();
    setPaperCitationPreviews({});
  }, [authenticated]);

  useEffect(() => {
    if (!authenticated || activeThreadPaperIds.length === 0) {
      return;
    }
    let cancelled = false;
    for (const paperId of activeThreadPaperIds) {
      if (
        paperCitationPreviewsRef.current[paperId] ||
        paperCitationPreviewFetchesRef.current.has(paperId)
      ) {
        continue;
      }
      paperCitationPreviewFetchesRef.current.add(paperId);
      setPaperCitationPreviews((current) => ({
        ...current,
        [paperId]: {
          paper: null,
          preview: null,
          status: "loading",
          error: "",
        },
      }));
      void (async () => {
        try {
          const [paperResult, previewResult] = await Promise.allSettled([
            getBrowserPaper(paperId),
            getBrowserPaperPreview(paperId),
          ]);
          if (cancelled) {
            return;
          }
          if (paperResult.status !== "fulfilled") {
            throw paperResult.reason;
          }
          setPaperCitationPreviews((current) => ({
            ...current,
            [paperId]: {
              paper: paperResult.value,
              preview: previewResult.status === "fulfilled" ? previewResult.value : null,
              status: "ready",
              error: "",
            },
          }));
        } catch (error) {
          if (cancelled) {
            return;
          }
          setPaperCitationPreviews((current) => ({
            ...current,
            [paperId]: {
              paper: null,
              preview: null,
              status: "error",
              error: error instanceof Error ? error.message : "Paper preview could not be loaded.",
            },
          }));
        } finally {
          paperCitationPreviewFetchesRef.current.delete(paperId);
        }
      })();
    }
    return () => {
      cancelled = true;
    };
  }, [activeThreadPaperIds, authenticated]);

  useEffect(() => {
    setContextUsagePopoverOpen(false);
    setChatHeaderMenuOpen(false);
    setThreadPane("chat");
    setExpandedCitationMessages({});
  }, [activeThreadId]);

  useEffect(() => {
    if (!showChatThreadHeader && threadPane === "papers") {
      setThreadPane("chat");
    }
  }, [showChatThreadHeader, threadPane]);

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

  const scrollMessageIntoView = useCallback((messageId: string) => {
    const element = document.getElementById(chatMessageRowId(messageId));
    if (!element) {
      return;
    }
    element.scrollIntoView({ behavior: "smooth", block: "start", inline: "nearest" });
    setMessageListAtBottom(false);
  }, []);

  const handleMessageListScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    setMessageListAtBottom(isMessageListNearBottom(event.currentTarget));
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setPaperDrawer(drawerStateFromUrl());
      const nextTrendingBrowseState = trendingBrowseStateFromUrl();
      setTrendingBrowseState(nextTrendingBrowseState);
      setWorkspaceView((current) => {
        if (nextTrendingBrowseState) {
          return { kind: "trending" };
        }
        return current?.kind === "trending" ? null : current;
      });
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
    const pendingMessageId = pendingTurnAnchorMessageIdRef.current;
    if (
      !pendingMessageId ||
      !activeThread?.messages.some((message) => message.message_id === pendingMessageId)
    ) {
      return;
    }
    requestAnimationFrame(() => {
      scrollMessageIntoView(pendingMessageId);
      if (pendingTurnAnchorMessageIdRef.current === pendingMessageId) {
        pendingTurnAnchorMessageIdRef.current = null;
      }
    });
  }, [activeThread, scrollMessageIntoView]);

  useEffect(() => {
    if (!activeThread || !messageListAtBottom) {
      return;
    }
    if (skipNextAutoBottomScrollRef.current) {
      skipNextAutoBottomScrollRef.current = false;
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
      isAdmin: Boolean(session.user.is_admin),
      hasOpenAIApiKey: session.has_openai_api_key ?? session.has_anthropic_api_key ?? false,
      settings: session.settings ?? DEFAULT_BROWSER_SETTINGS,
    };
  }, [session]);
  const sessionSettings = sessionSummary?.settings ?? DEFAULT_BROWSER_SETTINGS;
  const settingsTabs = BASE_SETTINGS_TABS;
  const workspaceSidebarItems = useMemo(
    () => WORKSPACE_SIDEBAR_ITEMS.filter((item) => !item.adminOnly || sessionSummary?.isAdmin),
    [sessionSummary?.isAdmin],
  );
  const hasInferenceAccess = Boolean(sessionSummary?.hasOpenAIApiKey);
  const chatSetupProjectName = emptyStateProject?.name ?? activeThread?.project_name ?? "Project";
  const chatSetupItems = [
    {
      id: "github",
      provider: "github",
      label: "GitHub connection",
      description: "Needed for the agent to create, edit, and save project files.",
      actionLabel: sessionSettings.has_github_connection ? "Connected" : "Open GitHub setup",
      ready: sessionSettings.has_github_connection,
      required: true,
      settingsTab: "version-control" as const,
    },
    {
      id: "inference",
      provider: "openai",
      label: "OpenAI API key",
      description:
        "Needed for the agent to run turns. Add your OpenAI API key before starting chat.",
      actionLabel: hasInferenceAccess ? "Ready" : "Open OpenAI setup",
      ready: hasInferenceAccess,
      required: true,
      settingsTab: "inference" as const,
    },
    {
      id: "sandbox",
      provider: "modal",
      label: "Sandbox credentials",
      description: "Recommended for run, test, and long-running coding tools.",
      actionLabel: sessionSettings.has_modal_credentials ? "Ready" : "Open sandbox setup",
      ready: sessionSettings.has_modal_credentials,
      required: false,
      settingsTab: "sandbox" as const,
    },
  ];
  const missingChatSetupSignature = chatSetupItems
    .filter((item) => !item.ready)
    .map((item) => item.id)
    .sort()
    .join("|");
  const dismissedChatSetupSignature = emptyStateProject?.project_id
    ? dismissedChatSetupSignatures[emptyStateProject.project_id]
    : null;
  const showChatSetupCard =
    authenticated &&
    isChatEmpty &&
    hasProjects &&
    missingChatSetupSignature.length > 0 &&
    dismissedChatSetupSignature !== missingChatSetupSignature;
  const appliedAppearanceTheme = sessionSettings.appearance_theme === "dark" ? "dark" : "light";
  const authenticatedDarkThemeEnabled = authenticated && appliedAppearanceTheme === "dark";
  const activeSettingsConfig = useMemo(
    () => settingsTabs.find((tab) => tab.id === activeSettingsTab) ?? settingsTabs[0],
    [activeSettingsTab, settingsTabs],
  );
  const authCaption = authView === "settings" ? activeSettingsConfig.caption : authCopy.caption;

  useEffect(() => {
    if (!settingsTabs.some((tab) => tab.id === activeSettingsTab)) {
      setActiveSettingsTab("inference");
    }
  }, [activeSettingsTab, settingsTabs]);

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

  const closeExplorationPicker = useCallback(() => {
    setExplorationPicker(null);
    setExplorationSearch("");
    setExplorationSuggestions([]);
    setExplorationSearchStatus("idle");
    setExplorationSearchFallback(false);
    setExplorationSearchError("");
    setExplorationSelectedIndex(0);
    setExplorationStartingPaperId("");
  }, []);

  const openExplorationPicker = useCallback(
    (projectId: string) => {
      const project = projectsRef.current.find((entry) => entry.project_id === projectId);
      if (!project) {
        showErrorFlash("Select a project before starting an exploration.");
        return;
      }
      selectedProjectIdRef.current = projectId;
      setSelectedProjectId(projectId);
      setExpandedProjectIds((current) =>
        current.includes(projectId) ? current : [...current, projectId],
      );
      setCollapsedProjectCategories((current) => ({
        ...current,
        [projectId]: {
          ...current[projectId],
          notes: false,
        },
      }));
      setProjectMenuProjectId(null);
      setExplorationSearch("");
      setExplorationSuggestions([]);
      setExplorationSearchStatus("idle");
      setExplorationSearchFallback(false);
      setExplorationSearchError("");
      setExplorationSelectedIndex(0);
      setExplorationStartingPaperId("");
      setExplorationPicker({ projectId, projectName: project.name });
    },
    [showErrorFlash],
  );

  const closeAuthModal = useCallback(() => {
    setAuthModalOpen(false);
    clearFlash();
  }, [clearFlash]);

  const closePaperDrawer = useCallback(() => {
    setPaperDrawer(null);
    updateDrawerUrl(null);
  }, []);

  const updateTrendingBrowseState = useCallback((nextState: TrendingBrowseState | null) => {
    setTrendingBrowseState(nextState);
    updateTrendingBrowseUrl(nextState);
  }, []);

  const closePaperPdfModal = useCallback(() => {
    setPaperExploreMenuOpen(false);
    setPaperPdfModal(null);
  }, []);

  const openPaperPdfModal = useCallback((target: PaperActionTarget) => {
    setPaperExploreMenuOpen(false);
    const cachedPreview = paperCitationPreviewsRef.current[target.paperId];
    const paper = target.paper ?? cachedPreview?.paper ?? null;
    const pdfUrl = target.pdfUrl ?? paper?.pdf_url ?? "";
    setPaperPdfModal({
      paperId: target.paperId,
      path: target.path,
      paper,
      title: paper?.title ?? target.title ?? "Paper",
      authors: paper?.authors ?? target.authors ?? "",
      pdfUrl,
      status: paper || pdfUrl ? "ready" : "loading",
      error: "",
    });

    if (paper || pdfUrl) {
      return;
    }

    void (async () => {
      try {
        const fetchedPaper = await getBrowserPaper(target.paperId);
        setPaperPdfModal((current) =>
          current?.paperId === target.paperId && current.path === target.path
            ? {
                ...current,
                paper: fetchedPaper,
                title: fetchedPaper.title,
                authors: fetchedPaper.authors,
                pdfUrl: fetchedPaper.pdf_url,
                status: "ready",
                error: "",
              }
            : current,
        );
      } catch (error) {
        setPaperPdfModal((current) =>
          current?.paperId === target.paperId && current.path === target.path
            ? {
                ...current,
                status: "error",
                error: error instanceof Error ? error.message : "Paper PDF could not be loaded.",
              }
            : current,
        );
      }
    })();
  }, []);

  const openPaperExploration = useCallback((explorationId: string) => {
    const exploration = paperExplorationsRef.current.find(
      (entry) => entry.explorationId === explorationId,
    );
    if (!exploration) {
      return;
    }
    selectedProjectIdRef.current = exploration.projectId;
    setSelectedProjectId(exploration.projectId);
    setExpandedProjectIds((current) =>
      current.includes(exploration.projectId) ? current : [...current, exploration.projectId],
    );
    setCollapsedProjectCategories((current) => ({
      ...current,
      [exploration.projectId]: {
        ...current[exploration.projectId],
        notes: false,
      },
    }));
    setWorkspaceView(null);
    setMobileSidebarOpen(false);
    setAccountMenuOpen(false);
    setProjectMenuProjectId(null);
    setSidebarCollapsed(true);
    setPaperExplorationSidebarCollapsed(false);
    setPaperExplorationSidebarPane("details");
    setPaperDrawer(null);
    setPaperDrawerPaper(null);
    setPaperDrawerContent(null);
    setPaperDrawerStatus("idle");
    setPaperDrawerError("");
    updateDrawerUrl(null);
    setThreadPane("chat");
    setActiveTab({ kind: "paper_exploration", id: exploration.explorationId });
  }, []);

  const startPaperExploration = useCallback(
    async (target: PaperActionTarget, options?: { navigate?: boolean }) => {
      const shouldNavigate = options?.navigate ?? true;
      const preferredProjectId =
        target.projectId ??
        activeThread?.project_id ??
        activeProjectHomeId ??
        selectedProjectIdRef.current ??
        projectsRef.current[0]?.project_id ??
        null;
      const existingExploration =
        paperExplorationsRef.current.find(
          (entry) => entry.projectId === preferredProjectId && entry.paperId === target.paperId,
        ) ?? paperExplorationsRef.current.find((entry) => entry.paperId === target.paperId);
      if (existingExploration) {
        if (shouldNavigate) {
          openPaperExploration(existingExploration.explorationId);
        } else {
          setExpandedProjectIds((current) =>
            current.includes(existingExploration.projectId)
              ? current
              : [...current, existingExploration.projectId],
          );
          setCollapsedProjectCategories((current) => ({
            ...current,
            [existingExploration.projectId]: {
              ...current[existingExploration.projectId],
              notes: false,
            },
          }));
        }
        return true;
      }
      if (!preferredProjectId) {
        showErrorFlash("Create a project before starting a paper exploration.");
        return false;
      }

      const cachedPreview = paperCitationPreviewsRef.current[target.paperId];
      let paper = target.paper ?? cachedPreview?.paper ?? null;
      let previewImageUrl = target.previewImageUrl ?? cachedPreview?.preview?.image_url ?? "";

      if (!paper) {
        try {
          paper = await getBrowserPaper(target.paperId);
        } catch {
          paper = null;
        }
      }
      if (!previewImageUrl) {
        try {
          previewImageUrl = (await getBrowserPaperPreview(target.paperId)).image_url;
        } catch {
          previewImageUrl = "";
        }
      }

      const postFetchExisting =
        paperExplorationsRef.current.find(
          (entry) => entry.projectId === preferredProjectId && entry.paperId === target.paperId,
        ) ?? paperExplorationsRef.current.find((entry) => entry.paperId === target.paperId);
      if (postFetchExisting) {
        if (shouldNavigate) {
          openPaperExploration(postFetchExisting.explorationId);
        } else {
          setExpandedProjectIds((current) =>
            current.includes(postFetchExisting.projectId)
              ? current
              : [...current, postFetchExisting.projectId],
          );
          setCollapsedProjectCategories((current) => ({
            ...current,
            [postFetchExisting.projectId]: {
              ...current[postFetchExisting.projectId],
              notes: false,
            },
          }));
        }
        return true;
      }

      const now = new Date().toISOString();
      const exploration: PaperExploration = {
        explorationId: paperExplorationIdFor(preferredProjectId, target.paperId),
        projectId: preferredProjectId,
        paperId: target.paperId,
        path: target.path,
        title: paper?.title ?? target.title ?? target.paperId,
        authors: paper?.authors ?? target.authors ?? "",
        previewImageUrl: previewImageUrl ?? "",
        pdfUrl: paper?.pdf_url ?? target.pdfUrl ?? "",
        createdAt: now,
        updatedAt: now,
      };

      setPaperExplorations((current) => [exploration, ...current]);
      paperExplorationsRef.current = [exploration, ...paperExplorationsRef.current];
      setExpandedProjectIds((current) =>
        current.includes(preferredProjectId) ? current : [...current, preferredProjectId],
      );
      setCollapsedProjectCategories((current) => ({
        ...current,
        [preferredProjectId]: {
          ...current[preferredProjectId],
          notes: false,
        },
      }));
      if (shouldNavigate) {
        selectedProjectIdRef.current = preferredProjectId;
        setSelectedProjectId(preferredProjectId);
        setWorkspaceView(null);
        setMobileSidebarOpen(false);
        setAccountMenuOpen(false);
        setProjectMenuProjectId(null);
        setSidebarCollapsed(true);
        setPaperExplorationSidebarCollapsed(false);
        setPaperExplorationSidebarPane("details");
        setPaperDrawer(null);
        setPaperDrawerPaper(null);
        setPaperDrawerContent(null);
        setPaperDrawerStatus("idle");
        setPaperDrawerError("");
        updateDrawerUrl(null);
        setThreadPane("chat");
        setActiveTab({ kind: "paper_exploration", id: exploration.explorationId });
      }
      return true;
    },
    [activeProjectHomeId, activeThread?.project_id, openPaperExploration, showErrorFlash],
  );

  const startExplorationFromSuggestion = useCallback(
    async (paper: MentionSuggestion) => {
      if (!explorationPicker || explorationStartingPaperId) {
        return;
      }
      setExplorationStartingPaperId(paper.paper_id);
      try {
        const didStart = await startPaperExploration({
          paperId: paper.paper_id,
          path: paper.arxiv_id || paper.paper_id,
          title: paper.title,
          authors: paper.authors,
          projectId: explorationPicker.projectId,
        });
        if (didStart) {
          closeExplorationPicker();
        }
      } catch (error) {
        showErrorFlash(
          error instanceof Error ? error.message : "Exploration could not be started.",
        );
      } finally {
        setExplorationStartingPaperId((current) => (current === paper.paper_id ? "" : current));
      }
    },
    [
      closeExplorationPicker,
      explorationPicker,
      explorationStartingPaperId,
      showErrorFlash,
      startPaperExploration,
    ],
  );

  const handleExplorationPickerKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeExplorationPicker();
        return;
      }
      if (explorationSuggestions.length === 0) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setExplorationSelectedIndex((current) =>
          current >= explorationSuggestions.length - 1 ? 0 : current + 1,
        );
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setExplorationSelectedIndex((current) =>
          current <= 0 ? explorationSuggestions.length - 1 : current - 1,
        );
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const selectedPaper = explorationSuggestions[explorationSelectedIndex];
        if (selectedPaper) {
          void startExplorationFromSuggestion(selectedPaper);
        }
      }
    },
    [
      closeExplorationPicker,
      explorationSelectedIndex,
      explorationSuggestions,
      startExplorationFromSuggestion,
    ],
  );

  const deletePaperExploration = useCallback((explorationId: string) => {
    const deletedExploration = paperExplorationsRef.current.find(
      (entry) => entry.explorationId === explorationId,
    );
    setPaperExplorations((current) =>
      current.filter((entry) => entry.explorationId !== explorationId),
    );
    paperExplorationsRef.current = paperExplorationsRef.current.filter(
      (entry) => entry.explorationId !== explorationId,
    );
    setActiveTab((current) => {
      if (current?.kind !== "paper_exploration" || current.id !== explorationId) {
        return current;
      }
      return deletedExploration?.projectId
        ? { kind: "project_home", id: deletedExploration.projectId }
        : null;
    });
  }, []);

  useEffect(() => {
    if (!paperPdfModal) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closePaperPdfModal();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closePaperPdfModal, paperPdfModal]);

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
      updates: Partial<
        Pick<ToolActivityEntry, "finishedAt" | "isError" | "command" | "output">
      > = {},
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
                  command: updates.command ?? entry.command,
                  output: updates.output ?? entry.output,
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
      if (citation.paper_id) {
        const existingExploration = paperExplorationsRef.current.find(
          (entry) => entry.paperId === citation.paper_id,
        );
        if (existingExploration) {
          openPaperExploration(existingExploration.explorationId);
          return;
        }
        const paperPreview = paperCitationPreviewsRef.current[citation.paper_id];
        openPaperPdfModal({
          paperId: citation.paper_id,
          path: citation.path,
          paper: paperPreview?.paper ?? null,
          title: paperPreview?.paper?.title ?? citation.title,
          authors: paperPreview?.paper?.authors ?? "",
          previewImageUrl: paperPreview?.preview?.image_url ?? null,
          pdfUrl: paperPreview?.paper?.pdf_url ?? null,
          projectId: activeThread?.project_id ?? null,
        });
        return;
      }
      const sourceCard = document.getElementById(
        sourceCardId(message.message_id, citation.source_id),
      );
      const sourceDetails = sourceCard?.closest("details");
      if (sourceDetails instanceof HTMLDetailsElement) {
        sourceDetails.open = true;
      }
      sourceCard?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    },
    [activeThread?.project_id, openPaperExploration, openPaperPdfModal],
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
      setActiveTab((currentActive) => {
        if (!currentActive || currentActive.kind !== "thread" || !ids.has(currentActive.id)) {
          return currentActive;
        }
        return null;
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
          const totalCountByKind = { ...current.totalCountByKind };
          const nextThreadTotal = Math.max(
            0,
            (totalCountByKind.thread ?? projectItemKindCount(current.items, "thread")) -
              removedCount,
          );
          totalCountByKind.thread = nextThreadTotal;
          return {
            ...current,
            items: nextItems,
            totalCount: Math.max(0, current.totalCount - removedCount),
            totalCountByKind,
            hasMore:
              current.hasMore || nextItems.length < Math.max(0, current.totalCount - removedCount),
            hasMoreByKind: {
              ...current.hasMoreByKind,
              thread:
                Boolean(current.hasMoreByKind?.thread) ||
                projectItemKindCount(nextItems, "thread") < nextThreadTotal,
            },
          };
        });
      }
    },
    [updateProjectItemCache],
  );

  const removePythonFileIdsLocally = useCallback(
    (fileIds: string[], projectId?: string) => {
      const ids = new Set(fileIds.filter(Boolean));
      if (ids.size === 0) {
        return;
      }
      setPythonFileMap((current) => {
        const next = { ...current };
        for (const fileId of ids) {
          delete next[fileId];
        }
        return next;
      });
      setPythonFileDrafts((current) => {
        const next = { ...current };
        for (const fileId of ids) {
          delete next[fileId];
        }
        return next;
      });
      setPythonFileRuns((current) => {
        const next = { ...current };
        for (const fileId of ids) {
          delete next[fileId];
        }
        return next;
      });
      setActiveTab((currentActive) => {
        if (!currentActive || currentActive.kind !== "python_file" || !ids.has(currentActive.id)) {
          return currentActive;
        }
        return null;
      });
      if (projectId) {
        updateProjectItemCache(projectId, (current) => {
          if (!current) {
            return current;
          }
          const nextItems = current.items.filter(
            (item) => item.item_kind !== "python_file" || !ids.has(item.file_id ?? ""),
          );
          const removedCount = current.items.length - nextItems.length;
          const totalCountByKind = { ...current.totalCountByKind };
          const nextFileTotal = Math.max(
            0,
            (totalCountByKind.python_file ?? projectItemKindCount(current.items, "python_file")) -
              removedCount,
          );
          totalCountByKind.python_file = nextFileTotal;
          return {
            ...current,
            items: nextItems,
            totalCount: Math.max(0, current.totalCount - removedCount),
            totalCountByKind,
            hasMore:
              current.hasMore || nextItems.length < Math.max(0, current.totalCount - removedCount),
            hasMoreByKind: {
              ...current.hasMoreByKind,
              python_file:
                Boolean(current.hasMoreByKind?.python_file) ||
                projectItemKindCount(nextItems, "python_file") < nextFileTotal,
            },
          };
        });
      }
    },
    [updateProjectItemCache],
  );

  const fetchProjectItems = useCallback(
    async (projectId: string, append = false, itemKind?: ProjectItemKind) => {
      const existing = projectItemsRef.current[projectId];
      const itemKinds = itemKind ? [itemKind] : PROJECT_SIDEBAR_ITEM_KINDS;
      const offsets = new Map(
        itemKinds.map((kind) => [
          kind,
          append ? projectItemKindCount(existing?.items ?? [], kind) : 0,
        ]),
      );
      updateProjectItemCache(projectId, (current) => ({
        items: current?.items ?? [],
        totalCount: current?.totalCount ?? 0,
        hasMore: current?.hasMore ?? false,
        hasMoreByKind: current?.hasMoreByKind,
        loaded: current?.loaded ?? false,
        loading: append || itemKind ? false : true,
        loadingMore: append,
        loadingMoreByKind: itemKind
          ? {
              ...current?.loadingMoreByKind,
              [itemKind]: append,
            }
          : current?.loadingMoreByKind,
        totalCountByKind: current?.totalCountByKind,
        error: "",
      }));
      try {
        const pages = await Promise.all(
          itemKinds.map(async (kind) => ({
            kind,
            page: await loadProjectItems(
              projectId,
              PROJECT_ITEM_PAGE_SIZE,
              offsets.get(kind) ?? 0,
              kind,
            ),
          })),
        );
        updateProjectItemCache(projectId, (current) => {
          const currentItems = current?.items ?? [];
          const targetKinds = new Set(itemKinds);
          const retainedItems = append
            ? currentItems
            : currentItems.filter((item) => !targetKinds.has(item.item_kind));
          const items = mergeProjectItems(
            retainedItems,
            pages.flatMap(({ page }) => page.items),
          );
          const totalCountByKind = { ...current?.totalCountByKind };
          const hasMoreByKind = { ...current?.hasMoreByKind };
          const loadingMoreByKind = { ...current?.loadingMoreByKind };
          for (const { kind, page } of pages) {
            totalCountByKind[kind] = page.total_count;
            hasMoreByKind[kind] = page.has_more;
            loadingMoreByKind[kind] = false;
          }
          const knownTotalCount = PROJECT_SIDEBAR_ITEM_KINDS.reduce(
            (sum, kind) => sum + (totalCountByKind[kind] ?? projectItemKindCount(items, kind)),
            0,
          );
          return {
            items,
            totalCount: Math.max(knownTotalCount, items.length),
            hasMore: PROJECT_SIDEBAR_ITEM_KINDS.some((kind) => Boolean(hasMoreByKind[kind])),
            hasMoreByKind,
            loaded: true,
            loading: false,
            loadingMore: false,
            loadingMoreByKind,
            totalCountByKind,
            error: "",
          };
        });
        return pages[0]?.page ?? null;
      } catch (error) {
        updateProjectItemCache(projectId, (current) => ({
          items: current?.items ?? [],
          totalCount: current?.totalCount ?? 0,
          hasMore: current?.hasMore ?? false,
          hasMoreByKind: current?.hasMoreByKind,
          loaded: current?.loaded ?? false,
          loading: false,
          loadingMore: false,
          loadingMoreByKind: itemKind
            ? {
                ...current?.loadingMoreByKind,
                [itemKind]: false,
              }
            : current?.loadingMoreByKind,
          totalCountByKind: current?.totalCountByKind,
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
        const existed = current.items.some((item) => item.thread_id === thread.thread_id);
        const items = upsertProjectItem(current.items, projectItemFromThread(thread));
        const totalCountByKind = { ...current.totalCountByKind };
        totalCountByKind.thread = existed
          ? (totalCountByKind.thread ?? projectItemKindCount(items, "thread"))
          : (totalCountByKind.thread ?? projectItemKindCount(items, "thread") - 1) + 1;
        return {
          ...current,
          items,
          totalCount: Math.max(current.totalCount, items.length),
          totalCountByKind,
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
        const existed = current.items.some((item) => item.file_id === file.file_id);
        const items = upsertProjectItem(current.items, projectItemFromPythonFile(file));
        const totalCountByKind = { ...current.totalCountByKind };
        totalCountByKind.python_file = existed
          ? (totalCountByKind.python_file ?? projectItemKindCount(items, "python_file"))
          : (totalCountByKind.python_file ?? projectItemKindCount(items, "python_file") - 1) + 1;
        return {
          ...current,
          items,
          totalCount: Math.max(current.totalCount, items.length),
          totalCountByKind,
          hasMore: Math.max(current.totalCount, items.length) > items.length || current.hasMore,
        };
      });
    },
    [updateProjectItemCache],
  );

  const refreshLoadedProjectCodeItems = useCallback(
    async (projectId: string) => {
      if (!projectItemsRef.current[projectId]?.loaded) {
        return;
      }
      try {
        await fetchProjectItems(projectId, false, "python_file");
      } catch (error) {
        console.error(error);
      }
    },
    [fetchProjectItems],
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
          const totalCountByKind = { ...current.totalCountByKind };
          const nextThreadTotal = Math.max(
            0,
            (totalCountByKind.thread ?? projectItemKindCount(current.items, "thread")) - removed,
          );
          totalCountByKind.thread = nextThreadTotal;
          return {
            ...current,
            items: nextItems,
            totalCount: Math.max(0, current.totalCount - removed),
            totalCountByKind,
            hasMore:
              current.hasMore || nextItems.length < Math.max(0, current.totalCount - removed),
            hasMoreByKind: {
              ...current.hasMoreByKind,
              thread:
                Boolean(current.hasMoreByKind?.thread) ||
                projectItemKindCount(nextItems, "thread") < nextThreadTotal,
            },
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
        const totalCountByKind = { ...current.totalCountByKind };
        totalCountByKind.thread = existed
          ? (totalCountByKind.thread ?? projectItemKindCount(items, "thread"))
          : (totalCountByKind.thread ?? projectItemKindCount(items, "thread") - 1) + 1;
        return {
          ...current,
          items,
          totalCount,
          totalCountByKind,
          hasMore: current.hasMore || totalCount > items.length,
        };
      });
    },
    [updateProjectItemCache],
  );

  const fetchAndStoreThread = useCallback(
    async (threadId: string, selectProject = true) => {
      const thread = await getThread(threadId);
      setThreadMap((current) => ({ ...current, [thread.thread_id]: thread }));
      if (selectProject) {
        setSelectedProjectId(thread.project_id);
      }
      upsertThreadIntoProjectItems(thread);
      return thread;
    },
    [upsertThreadIntoProjectItems],
  );

  const openThread = useCallback(
    async (threadId: string) => {
      const thread = threadMapRef.current[threadId] ?? (await fetchAndStoreThread(threadId));
      setActiveTab({ kind: "thread", id: threadId });
      setSelectedProjectId(thread.project_id);
    },
    [fetchAndStoreThread],
  );

  const fetchAndStorePythonFile = useCallback(
    async (fileId: string, selectProject = true) => {
      const file = await getPythonFile(fileId);
      setPythonFileMap((current) => ({ ...current, [file.file_id]: file }));
      if (selectProject) {
        setSelectedProjectId(file.project_id);
      }
      setPythonFileDrafts((current) => ({
        ...current,
        [file.file_id]: current[file.file_id] ?? { content: file.content, dirty: false },
      }));
      upsertPythonFileIntoProjectItems(file);
      return file;
    },
    [upsertPythonFileIntoProjectItems],
  );

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

  const persistDirtyProjectDrafts = useCallback(
    async (projectId: string) => {
      const dirtyFileIds = Object.entries(pythonFileDraftsRef.current)
        .filter(([fileId, draft]) => {
          const pythonFile = pythonFileMapRef.current[fileId];
          return Boolean(draft.dirty && pythonFile && pythonFile.project_id === projectId);
        })
        .map(([fileId]) => fileId);
      for (const fileId of dirtyFileIds) {
        const pythonFile =
          pythonFileMapRef.current[fileId] ?? (await fetchAndStorePythonFile(fileId));
        const nextContent = pythonFileDraftsRef.current[fileId]?.content ?? pythonFile.content;
        await persistPythonFile(fileId, nextContent);
      }
    },
    [fetchAndStorePythonFile, persistPythonFile],
  );

  const openPythonFile = useCallback(
    async (fileId: string) => {
      const file = pythonFileMapRef.current[fileId] ?? (await fetchAndStorePythonFile(fileId));
      setActiveTab({ kind: "python_file", id: fileId });
      setSelectedProjectId(file.project_id);
    },
    [fetchAndStorePythonFile],
  );

  const openProjectHome = useCallback((projectId: string) => {
    if (!projectsRef.current.some((project) => project.project_id === projectId)) {
      return;
    }
    setActiveTab({ kind: "project_home", id: projectId });
    setSelectedProjectId(projectId);
  }, []);

  const openWorkspacePage = useCallback(
    (pageId: "trending" | "admin_jobs") => {
      if (pageId === "trending") {
        updateTrendingBrowseState({
          date: trendingBrowseState?.date ?? localDateString(),
          sort: trendingBrowseState?.sort ?? "default",
          page: trendingBrowseState?.page ?? 1,
          paperId: trendingBrowseState?.paperId ?? null,
        });
        setWorkspaceView({ kind: "trending" });
      } else {
        updateTrendingBrowseState(null);
        setWorkspaceView({ kind: "admin_jobs" });
      }
      setActiveTab(null);
      setMobileSidebarOpen(false);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
    },
    [trendingBrowseState, updateTrendingBrowseState],
  );

  const openAdminJobPapers = useCallback(
    (rootJobId: string) => {
      updateTrendingBrowseState(null);
      setWorkspaceView({ kind: "admin_job_papers", rootJobId });
      setActiveTab(null);
      setMobileSidebarOpen(false);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
    },
    [updateTrendingBrowseState],
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
      setActiveTab({ kind: "thread", id: thread.thread_id });
      setSelectedProjectId(thread.project_id);
      upsertThreadIntoProjectItems(thread);
      await refreshProjects();
      return thread;
    },
    [refreshProjects, upsertThreadIntoProjectItems],
  );

  const copyMessage = useCallback(async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
    } catch (error) {
      console.error(error);
    }
  }, []);

  const copyThreadDebugBundle = useCallback(
    async (bundle: ReturnType<typeof buildThreadDebugBundle>) => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(bundle, null, 2));
        showNoteFlash("Copied chat debug bundle.");
      } catch (error) {
        console.error(error);
      }
    },
    [showNoteFlash],
  );

  const exportActiveThread = useCallback(
    (format: ExportFormat) => {
      if (!activeThread) {
        return;
      }
      const title = displayThreadTitle(activeThread.title)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
      const basename = title || "paperbase-chat";
      const markdown = threadMarkdownExport(activeThread);
      if (format === "markdown") {
        downloadTextFile(`${basename}.md`, markdown, "text/markdown;charset=utf-8");
        return;
      }
      if (format === "docx") {
        const html = `<!doctype html><html><head><meta charset="utf-8"><title>${displayThreadTitle(activeThread.title)}</title></head><body><pre>${markdown.replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[char] ?? char)}</pre></body></html>`;
        downloadTextFile(
          `${basename}.docx`,
          html,
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        );
        return;
      }
      showNoteFlash("Use the print dialog to save this session as PDF.");
      window.print();
    },
    [activeThread, showNoteFlash],
  );

  const openHeaderRenameThread = useCallback(() => {
    if (!activeThread) {
      return;
    }
    const currentTitle = displayThreadTitle(activeThread.title);
    setThreadRenameModal({
      threadId: activeThread.thread_id,
      title: currentTitle,
      draft: currentTitle,
      saving: false,
      error: "",
    });
    setChatHeaderMenuOpen(false);
  }, [activeThread]);

  const closeThreadRenameModal = useCallback(() => {
    setThreadRenameModal((current) => (current?.saving ? current : null));
  }, []);

  const submitThreadRenameModal = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      if (!threadRenameModal || threadRenameModal.saving) {
        return;
      }
      const nextTitle = threadRenameModal.draft.trim();
      if (!nextTitle) {
        setThreadRenameModal((current) =>
          current ? { ...current, error: "Enter a title for this session." } : current,
        );
        return;
      }
      setThreadRenameModal((current) =>
        current ? { ...current, saving: true, error: "" } : current,
      );
      try {
        const renamed = await renameThread(threadRenameModal.threadId, nextTitle);
        setThreadMap((current) => ({ ...current, [renamed.thread_id]: renamed }));
        upsertThreadIntoProjectItems(renamed);
        setThreadRenameModal(null);
      } catch (error) {
        setThreadRenameModal((current) =>
          current && current.threadId === threadRenameModal.threadId
            ? {
                ...current,
                saving: false,
                error: error instanceof Error ? error.message : "Session could not be renamed.",
              }
            : current,
        );
      }
    },
    [threadRenameModal, upsertThreadIntoProjectItems],
  );

  const updateStreamingThreadTitle = useCallback(
    (threadId: string, title: string, fallback: ChatThreadResponse) => {
      const nextTitle = title.trim();
      if (!nextTitle) {
        return;
      }
      const updatedAt = new Date().toISOString();
      updateThread(threadId, (thread) => ({
        ...(thread ?? fallback),
        title: nextTitle,
        updated_at: updatedAt,
      }));
    },
    [updateThread],
  );

  const closeFeedbackModal = useCallback(() => {
    setFeedbackModalState(null);
    setFeedbackReasons([]);
    setFeedbackNote("");
    setFeedbackSubmitting(false);
  }, []);

  const openFeedbackModal = useCallback(
    (message: ChatMessageResponse, sentiment: FeedbackSentiment) => {
      setFeedbackModalState({ message, sentiment });
      setFeedbackReasons([]);
      setFeedbackNote("");
    },
    [],
  );

  const submitFeedbackModal = useCallback(async () => {
    if (!activeThread || !feedbackModalState) {
      return;
    }
    setFeedbackSubmitting(true);
    try {
      await submitChatFeedback(activeThread.thread_id, {
        message_id: feedbackModalState.message.message_id,
        sentiment: feedbackModalState.sentiment,
        reasons: feedbackReasons,
        note: feedbackNote,
        debug_bundle: activeThreadDebugBundle,
      });
      showNoteFlash("Feedback sent.");
      closeFeedbackModal();
    } catch (error) {
      setFeedbackSubmitting(false);
      showErrorFlash(error instanceof Error ? error.message : "Feedback could not be sent.");
    }
  }, [
    activeThread,
    activeThreadDebugBundle,
    closeFeedbackModal,
    feedbackModalState,
    feedbackNote,
    feedbackReasons,
    showErrorFlash,
    showNoteFlash,
  ]);

  const requireInferenceAccess = useCallback(
    (currentSession?: BrowserSessionResponse | null) => {
      const hasOpenAIApiKey = Boolean(
        currentSession?.has_openai_api_key ??
        currentSession?.has_anthropic_api_key ??
        sessionRef.current?.has_openai_api_key ??
        sessionRef.current?.has_anthropic_api_key,
      );
      if (hasOpenAIApiKey) {
        return true;
      }
      const message = apiKeyStateMessage("api_key_required");
      setComposerStatus(message);
      showErrorFlash(message);
      setActiveSettingsTab("inference");
      setAuthView("settings");
      setAuthModalOpen(true);
      return false;
    },
    [showErrorFlash],
  );

  const sendMessage = useCallback(
    async (
      threadId: string,
      content: string,
      currentSession?: BrowserSessionResponse | null,
      paperIds: string[] = [],
    ) => {
      if (!requireInferenceAccess(currentSession)) {
        return null;
      }
      const existingThread = threadMapRef.current[threadId];
      const projectIdForTurn =
        existingThread?.project_id ??
        selectedProjectIdRef.current ??
        projectsRef.current[0]?.project_id ??
        "";
      if (projectIdForTurn && messageHasProjectCodeIntent(content)) {
        try {
          await persistDirtyProjectDrafts(projectIdForTurn);
        } catch (error) {
          const message =
            error instanceof Error ? error.message : "Project drafts could not be saved.";
          setComposerStatus(message);
          if (message.toLowerCase().includes("github")) {
            showErrorFlash(message);
            setAuthView("settings");
            setAuthModalOpen(true);
          }
          return null;
        }
      }
      const optimisticUserMessage = createLocalMessage("user", content);
      const now = new Date().toISOString();
      const fallbackThread = existingThread;
      const turnIndex =
        (fallbackThread?.messages.filter((message) => message.role === "user").length ?? 0) + 1;
      const fallbackProjectId = projectIdForTurn;
      const fallbackProjectName =
        fallbackThread?.project_name ??
        projectsRef.current.find((project) => project.project_id === fallbackProjectId)?.name ??
        "";
      const fallbackProjectInstructions = fallbackThread?.project_instructions ?? "";
      let assistantStarted = false;
      let turnUsedProjectCodeTools = false;
      let donePayload: ChatStreamDoneEvent | null = null;
      let gatingPayload: SendChatMessageResponse | null = null;
      let streamError: string | null = null;
      let streamingMessageId: string | null = null;
      const streamAbortController = new AbortController();
      const optimisticTitle = summarizeThreadTitle(content);
      pendingTurnAnchorMessageIdRef.current = optimisticUserMessage.message_id;
      skipNextAutoBottomScrollRef.current = true;

      updateThread(threadId, (thread) => ({
        thread_id: thread?.thread_id ?? threadId,
        project_id: thread?.project_id ?? fallbackProjectId,
        project_name: thread?.project_name ?? fallbackProjectName,
        project_instructions: thread?.project_instructions ?? fallbackProjectInstructions,
        title:
          (thread?.title ?? "New chat") === "New chat"
            ? optimisticTitle
            : (thread?.title ?? "New chat"),
        created_at: thread?.created_at ?? now,
        updated_at: now,
        messages: [...(thread?.messages ?? []), optimisticUserMessage],
      }));

      const upsertThreadItemState = (
        thread: ChatThreadResponse | null,
        item: ChatThreadItemResponse,
      ): ChatThreadResponse => {
        const nextThread =
          thread ??
          ({
            thread_id: threadId,
            project_id: fallbackProjectId,
            project_name: fallbackProjectName,
            project_instructions: fallbackProjectInstructions,
            title: optimisticTitle,
            created_at: now,
            updated_at: now,
            items: [],
            turns: [],
            messages: [],
          } satisfies ChatThreadResponse);
        const existingItems = nextThread.items ?? [];
        const nextItems = existingItems.some((existing) => existing.item_id === item.item_id)
          ? existingItems.map((existing) => (existing.item_id === item.item_id ? item : existing))
          : [...existingItems, item];
        const derivedMessage = messageFromThreadItem(item);
        let nextMessages = nextThread.messages ?? [];
        if (derivedMessage) {
          nextMessages = nextMessages.some(
            (message) => message.message_id === derivedMessage.message_id,
          )
            ? nextMessages.map((message) =>
                message.message_id === derivedMessage.message_id ? derivedMessage : message,
              )
            : [...nextMessages, derivedMessage];
        }
        return {
          ...nextThread,
          updated_at: new Date().toISOString(),
          items: nextItems,
          messages: nextMessages,
        };
      };

      const appendItemDeltaState = (
        thread: ChatThreadResponse | null,
        itemId: string,
        delta: string,
      ): ChatThreadResponse => {
        const nextThread =
          thread ??
          ({
            thread_id: threadId,
            project_id: fallbackProjectId,
            project_name: fallbackProjectName,
            project_instructions: fallbackProjectInstructions,
            title: optimisticTitle,
            created_at: now,
            updated_at: now,
            items: [],
            turns: [],
            messages: [],
          } satisfies ChatThreadResponse);
        const nextItems = (nextThread.items ?? []).map((item) =>
          item.item_id === itemId
            ? { ...item, content_text: `${item.content_text}${delta}` }
            : item,
        );
        const nextMessages = (nextThread.messages ?? []).map((message) =>
          message.message_id === itemId
            ? { ...message, content_markdown: `${message.content_markdown}${delta}` }
            : message,
        );
        return {
          ...nextThread,
          updated_at: new Date().toISOString(),
          items: nextItems,
          messages: nextMessages,
        };
      };

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
            paper_ids: paperIds,
          },
          (eventName, payload) => {
            if (eventName === "thread_title") {
              updateStreamingThreadTitle(payload.thread_id, payload.title, {
                thread_id: threadId,
                project_id: fallbackProjectId,
                project_name: fallbackProjectName,
                project_instructions: fallbackProjectInstructions,
                title: optimisticTitle,
                created_at: now,
                updated_at: now,
                items: [],
                turns: [],
                messages: [optimisticUserMessage],
              });
              return;
            }

            if (eventName === "turn_started") {
              return;
            }

            if (eventName === "mention_indexing_started") {
              const title = payload.title || payload.arxiv_id || payload.paper_id;
              setComposerStatus(`Fetching paper: ${title}…`);
              return;
            }
            if (eventName === "mention_indexing_completed") {
              setComposerStatus("");
              return;
            }
            if (eventName === "mention_indexing_failed") {
              const title = payload.title || payload.arxiv_id || payload.paper_id;
              setComposerStatus(
                payload.detail
                  ? `Could not fetch ${title}: ${payload.detail}`
                  : `Could not fetch ${title}`,
              );
              return;
            }

            if (eventName === "item_started") {
              updateThread(threadId, (thread) => upsertThreadItemState(thread, payload.item));
              if (payload.item.item_type === "assistant_message") {
                assistantStarted = true;
                streamingMessageId = payload.item.item_id;
                markThreadAssistantStarted(threadId);
              }
              if (
                payload.item.item_type === "function_call" ||
                payload.item.item_type === "custom_tool_call"
              ) {
                const toolName = payload.item.name ?? "";
                if (toolName.startsWith("project_")) {
                  turnUsedProjectCodeTools = true;
                }
                const toolLabel = toolActivityLabelForItem(payload.item);
                upsertToolActivity(threadId, {
                  id: payload.item.call_id ?? payload.item.item_id,
                  turnIndex,
                  toolName,
                  label: toolLabel,
                  command: toolActivityCommandForItem(payload.item),
                  output: null,
                  status: "running",
                  startedAt: new Date().toISOString(),
                  finishedAt: null,
                  isError: false,
                });
              }
              return;
            }

            if (eventName === "item_delta") {
              updateThread(threadId, (thread) =>
                appendItemDeltaState(thread, payload.item_id, payload.delta),
              );
              return;
            }

            if (eventName === "item_completed") {
              updateThread(threadId, (thread) => upsertThreadItemState(thread, payload.item));
              if (
                payload.item.item_type === "function_call_output" ||
                payload.item.item_type === "custom_tool_call_output"
              ) {
                const toolName = payload.item.name ?? "";
                completeToolActivity(threadId, payload.item.call_id ?? payload.item.item_id, {
                  command: null,
                  output: payload.item.content_text ?? null,
                  isError:
                    typeof payload.item.content_json === "object" &&
                    payload.item.content_json !== null &&
                    "error" in (payload.item.content_json as Record<string, unknown>),
                });
                if (
                  toolName === "project_write_file" ||
                  toolName === "project_apply_patch" ||
                  toolName === "project_move" ||
                  toolName === "project_touch" ||
                  toolName === "project_edit" ||
                  toolName === "project_delete"
                ) {
                  void refreshLoadedProjectCodeItems(fallbackProjectId);
                }
              }
              if (
                payload.item.item_type === "function_call" &&
                String(payload.item.name ?? "").startsWith("project_")
              ) {
                turnUsedProjectCodeTools = true;
              }
              return;
            }

            if (eventName === "usage") {
              updateThreadStreamUsage(threadId, payload);
              setThreadUsageSnapshot(threadId, payload);
              return;
            }

            if (eventName === "turn_completed") {
              donePayload = payload;
              if (payload.usage) {
                updateThreadStreamUsage(threadId, payload.usage);
                setThreadUsageSnapshot(threadId, payload.usage);
              }
              setThreadMap((current) => ({
                ...current,
                [threadId]: normalizeThreadForState(payload.thread),
              }));
              return;
            }

            if (eventName === "api_key_required") {
              gatingPayload = payload;
              return;
            }

            if (eventName === "turn_failed") {
              streamError = payload.detail;
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
                title: optimisticTitle,
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
            paper_ids: paperIds,
          });
          const normalizedThread = normalizeThreadForState(payload.thread);
          setThreadMap((current) => ({ ...current, [threadId]: normalizedThread }));
          setSelectedProjectId(normalizedThread.project_id);
          upsertThreadIntoProjectItems(normalizedThread);
          setSession((current) =>
            current
              ? {
                  ...current,
                  has_openai_api_key:
                    payload.has_openai_api_key ?? payload.has_anthropic_api_key ?? false,
                  has_anthropic_api_key:
                    payload.has_openai_api_key ?? payload.has_anthropic_api_key ?? false,
                }
              : current,
          );
          setComposerStatus("");
          await refreshProjects();
          if (messageHasProjectCodeIntent(content)) {
            await refreshLoadedProjectCodeItems(normalizedThread.project_id);
          }
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
              title: optimisticTitle,
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
        const normalizedThread = normalizeThreadForState(gatingPayload.thread);
        setThreadMap((current) => ({ ...current, [threadId]: normalizedThread }));
        setSelectedProjectId(normalizedThread.project_id);
        upsertThreadIntoProjectItems(normalizedThread);
        setSession((current) =>
          current
            ? {
                ...current,
                has_openai_api_key:
                  gatingPayload.has_openai_api_key ?? gatingPayload.has_anthropic_api_key ?? false,
                has_anthropic_api_key:
                  gatingPayload.has_openai_api_key ?? gatingPayload.has_anthropic_api_key ?? false,
              }
            : current,
        );
        setComposerStatus("");
        await refreshProjects();
        if (turnUsedProjectCodeTools) {
          await refreshLoadedProjectCodeItems(normalizedThread.project_id);
        }
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
        if (turnUsedProjectCodeTools) {
          await refreshLoadedProjectCodeItems(refreshedThread.project_id);
        }
        return null;
      }

      if (donePayload) {
        setSession((current) =>
          current
            ? {
                ...current,
                has_openai_api_key:
                  donePayload.has_openai_api_key ?? donePayload.has_anthropic_api_key ?? false,
                has_anthropic_api_key:
                  donePayload.has_openai_api_key ?? donePayload.has_anthropic_api_key ?? false,
              }
            : current,
        );
        setComposerStatus("");
        const refreshedThread = await fetchAndStoreThread(threadId);
        upsertThreadIntoProjectItems(refreshedThread);
        await refreshProjects();
        if (turnUsedProjectCodeTools) {
          await refreshLoadedProjectCodeItems(refreshedThread.project_id);
        }
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
      persistDirtyProjectDrafts,
      refreshLoadedProjectCodeItems,
      refreshProjects,
      requireInferenceAccess,
      setThreadUsageSnapshot,
      showErrorFlash,
      startThreadStreamState,
      updateThreadStreamUsage,
      updateStreamingThreadTitle,
      upsertToolActivity,
      upsertThreadIntoProjectItems,
      updateThread,
    ],
  );

  const retryAssistantResponse = useCallback(
    async (messageId: string) => {
      if (!activeThread || streamingThreadId) {
        return;
      }
      const messageIndex = activeThread.messages.findIndex(
        (message) => message.message_id === messageId,
      );
      if (messageIndex < 0) {
        return;
      }
      const previousUser = activeThread.messages
        .slice(0, messageIndex)
        .reverse()
        .find((message) => message.role === "user" && message.kind !== "compaction");
      if (!previousUser?.content_markdown.trim()) {
        return;
      }
      setThreadPane("chat");
      await sendMessage(activeThread.thread_id, previousUser.content_markdown.trim());
    },
    [activeThread, sendMessage, streamingThreadId],
  );

  const sendRecommendedPrompt = useCallback(
    async (prompt: string) => {
      if (!activeThread || streamingThreadId) {
        return;
      }
      setThreadPane("chat");
      setComposerInput("");
      setComposerTags([]);
      await sendMessage(activeThread.thread_id, prompt);
    },
    [activeThread, sendMessage, streamingThreadId],
  );

  const bootstrapAuthenticatedUi = useCallback(
    async (currentSession?: BrowserSessionResponse | null) => {
      await refreshUploads();
      const loadedProjects = await refreshProjects();
      const persistedSelectedProjectId = restoreSelectedProjectId();
      const restoredSelectedProjectId =
        persistedSelectedProjectId &&
        loadedProjects.some((project) => project.project_id === persistedSelectedProjectId)
          ? persistedSelectedProjectId
          : null;
      const selectedProjectId = restoredSelectedProjectId ?? loadedProjects[0]?.project_id ?? null;
      selectedProjectIdRef.current = selectedProjectId;
      setSelectedProjectId(selectedProjectId);
      setExpandedProjectIds(restoredSelectedProjectId ? [restoredSelectedProjectId] : []);
      if (
        restoredSelectedProjectId &&
        !projectItemsRef.current[restoredSelectedProjectId]?.loaded &&
        !projectItemsRef.current[restoredSelectedProjectId]?.loading
      ) {
        await fetchProjectItems(restoredSelectedProjectId);
      }
      const {
        prompt: pendingPrompt,
        paperIds: pendingPaperIds,
        mentions: pendingMentions,
      } = consumePendingPrompt();
      const trimmedPendingPrompt = pendingPrompt.trim();
      activeTabStorageHydratedRef.current = true;
      if (trimmedPendingPrompt) {
        if (!requireInferenceAccess(currentSession)) {
          setActiveTab(null);
          clearStoredActiveTab();
          setComposerInput(trimmedPendingPrompt);
          setRestoredComposerMentions(pendingMentions);
          return;
        }
        const thread = await createNewThread(
          "",
          selectedProjectId ?? loadedProjects[0]?.project_id,
        );
        setLandingPrompt("");
        setLandingTags([]);
        setRestoredComposerMentions([]);
        await sendMessage(thread.thread_id, trimmedPendingPrompt, currentSession, pendingPaperIds);
        return;
      }

      const initialTrendingBrowseState = trendingBrowseStateFromUrl();
      if (initialTrendingBrowseState) {
        setActiveTab(null);
        clearStoredActiveTab();
        setTrendingBrowseState(initialTrendingBrowseState);
        setWorkspaceView({ kind: "trending" });
        return;
      }

      const restoredActiveTab = restoreActiveTab();
      let validRestoredActiveTab: OpenTab | null = null;
      if (restoredActiveTab) {
        try {
          if (restoredActiveTab.kind === "thread") {
            await fetchAndStoreThread(restoredActiveTab.id, false);
          } else if (restoredActiveTab.kind === "python_file") {
            await fetchAndStorePythonFile(restoredActiveTab.id, false);
          } else if (restoredActiveTab.kind === "paper_exploration") {
            const restoredExploration = restorePaperExplorations().find(
              (exploration) => exploration.explorationId === restoredActiveTab.id,
            );
            if (
              !restoredExploration ||
              !loadedProjects.some(
                (project) => project.project_id === restoredExploration.projectId,
              )
            ) {
              throw new Error("Stored paper exploration no longer exists.");
            }
            selectedProjectIdRef.current = restoredExploration.projectId;
            setSelectedProjectId(restoredExploration.projectId);
            setExpandedProjectIds((current) =>
              current.includes(restoredExploration.projectId)
                ? current
                : [...current, restoredExploration.projectId],
            );
            setCollapsedProjectCategories((current) => ({
              ...current,
              [restoredExploration.projectId]: {
                ...current[restoredExploration.projectId],
                notes: false,
              },
            }));
            setSidebarCollapsed(true);
            setPaperExplorationSidebarCollapsed(false);
            setPaperExplorationSidebarPane("details");
            setPaperDrawer(null);
            setPaperDrawerPaper(null);
            setPaperDrawerContent(null);
            setPaperDrawerStatus("idle");
            setPaperDrawerError("");
            updateDrawerUrl(null);
          } else if (
            !loadedProjects.some((project) => project.project_id === restoredActiveTab.id)
          ) {
            throw new Error("Stored project no longer exists.");
          }
          validRestoredActiveTab = restoredActiveTab;
        } catch (error) {
          console.error(error);
        }
      }
      setActiveTab(validRestoredActiveTab);
      if (!validRestoredActiveTab) {
        clearStoredActiveTab();
      }
    },
    [
      createNewThread,
      fetchAndStorePythonFile,
      fetchProjectItems,
      fetchAndStoreThread,
      refreshProjects,
      refreshUploads,
      requireInferenceAccess,
      sendMessage,
    ],
  );

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
      const nextSession = bootstrapSession ?? (await loadSession());
      setSession(nextSession);
      setAuthReady(true);
      if (
        authStatus === "github_connected" &&
        sessionStorage.getItem(RESUME_PROJECT_MODAL_KEY) === "1" &&
        nextSession.settings?.has_github_connection
      ) {
        sessionStorage.removeItem(RESUME_PROJECT_MODAL_KEY);
        setAuthModalOpen(false);
        setProjectModalState({ mode: "create" });
        setProjectRepoMode("");
        setProjectRepoOwner(nextSession.settings.github_login || "");
        setProjectRepoName("");
        setProjectRepoBranch("");
        setProjectRepoListLoading(true);
        try {
          const response = await loadGitHubRepositories();
          setProjectRepoList(response.repos);
        } catch (error) {
          setProjectRepoStatus(
            error instanceof Error ? error.message : "GitHub repositories could not be loaded.",
          );
        } finally {
          setProjectRepoListLoading(false);
        }
      }
      if (nextSession.authenticated) {
        await bootstrapAuthenticatedUi(nextSession);
      }
    })().catch((error: unknown) => {
      console.error(error);
      setComposerStatus("The chat app could not reach paperbase.");
      setAuthReady(true);
    });
  }, [bootstrapAuthenticatedUi, bootstrapSession, showErrorFlash, showNoteFlash]);

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

  const openChatSetupSettings = useCallback(
    (settingsTab: SettingsTab) => {
      openAuthModal("settings", settingsTab);
    },
    [openAuthModal],
  );

  const dismissChatSetupCard = useCallback(() => {
    if (!emptyStateProject?.project_id || !missingChatSetupSignature) {
      return;
    }
    setDismissedChatSetupSignatures((current) => ({
      ...current,
      [emptyStateProject.project_id]: missingChatSetupSignature,
    }));
  }, [emptyStateProject?.project_id, missingChatSetupSignature]);

  const handleAuthSuccess = useCallback(
    async (nextSession: BrowserSessionResponse) => {
      setSession(nextSession);
      closeAuthModal();
      await bootstrapAuthenticatedUi(nextSession);
    },
    [bootstrapAuthenticatedUi, closeAuthModal],
  );

  const handleLandingSubmit = useCallback(async () => {
    const prompt = landingPrompt.trim();
    if (!prompt) {
      return;
    }
    const mentions = landingTags.filter((tag) => tag.id);
    if (!sessionRef.current?.authenticated) {
      rememberPendingPrompt(prompt, mentions);
      openAuthModal("login");
      return;
    }
    if (!requireInferenceAccess()) {
      return;
    }
    const thread = await createNewThread();
    const paperIds = mentions.map((tag) => tag.id);
    setLandingPrompt("");
    setLandingTags([]);
    await sendMessage(thread.thread_id, prompt, undefined, paperIds);
  }, [
    createNewThread,
    landingPrompt,
    landingTags,
    openAuthModal,
    requireInferenceAccess,
    sendMessage,
  ]);

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
    if (!requireInferenceAccess()) {
      return;
    }
    const threadId = activeThreadId ?? (await createNewThread()).thread_id;
    const paperIds = [
      ...new Set([
        ...composerTags.map((tag) => tag.id),
        ...mentionedPaperIdsInText(content, restoredComposerMentions),
      ]),
    ];
    setComposerInput("");
    setComposerTags([]);
    setRestoredComposerMentions([]);
    await sendMessage(threadId, content, undefined, paperIds);
  }, [
    activeThreadId,
    composerInput,
    composerTags,
    createNewThread,
    requireInferenceAccess,
    restoredComposerMentions,
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
    setActiveTab(null);
    clearStoredActiveTab();
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
    setRestoredComposerMentions([]);
    setComposerStatus("");
    setMobileSidebarOpen(false);
    setPaperExplorationSidebarCollapsed(true);
    setPaperExplorationSidebarPane("details");
    setPaperExplorationNotes({});
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

  const openPaperExplorationSidebar = useCallback(() => {
    setAccountMenuOpen(false);
    setProjectMenuProjectId(null);
    setMobileSidebarOpen(false);
    setSidebarCollapsed(true);
    setPaperExplorationSidebarCollapsed(false);
  }, []);

  const closePaperExplorationSidebar = useCallback(() => {
    setPaperExplorationSidebarCollapsed(true);
  }, []);

  const handleSidebarToggle = useCallback(() => {
    setAccountMenuOpen(false);
    setProjectMenuProjectId(null);
    if (window.matchMedia("(max-width: 980px)").matches) {
      setMobileSidebarOpen((current) => {
        const next = !current;
        if (next) {
          setPaperExplorationSidebarCollapsed(true);
        }
        return next;
      });
      return;
    }
    setSidebarCollapsed((current) => {
      const next = !current;
      if (!next) {
        setPaperExplorationSidebarCollapsed(true);
      }
      return next;
    });
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
      if (expanded && expandedProjectIds.length === 1) {
        setExpandedProjectIds([]);
        return;
      }
      setExpandedProjectIds([projectId]);
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

  const toggleProjectCategory = useCallback(
    (projectId: string, category: ProjectSidebarCategoryKey) => {
      setCollapsedProjectCategories((current) => {
        const projectCategories = current[projectId] ?? {};
        return {
          ...current,
          [projectId]: {
            ...projectCategories,
            [category]: !projectCategories[category],
          },
        };
      });
    },
    [],
  );

  const toggleAllProjectCategories = useCallback(
    async (projectId: string) => {
      const expanded = expandedProjectIds.includes(projectId);
      const projectCategories = collapsedProjectCategories[projectId] ?? {};
      const allCollapsed = PROJECT_SIDEBAR_CATEGORY_KEYS.every((key) =>
        Boolean(projectCategories[key]),
      );

      if (!expanded || allCollapsed) {
        setExpandedProjectIds((current) =>
          current.includes(projectId) ? current : [...current, projectId],
        );
        setCollapsedProjectCategories((current) => ({
          ...current,
          [projectId]: buildProjectCategoryCollapseState(false),
        }));
        if (
          !projectItemsRef.current[projectId]?.loaded &&
          !projectItemsRef.current[projectId]?.loading
        ) {
          await fetchProjectItems(projectId);
        }
        return;
      }

      setCollapsedProjectCategories((current) => ({
        ...current,
        [projectId]: buildProjectCategoryCollapseState(true),
      }));
      setExpandedProjectIds((current) => current.filter((value) => value !== projectId));
    },
    [collapsedProjectCategories, expandedProjectIds, fetchProjectItems],
  );

  const loadProjectRepoDetails = useCallback(
    async (owner: string, repo: string, branchOverride?: string) => {
      const ownerLogin = owner.trim();
      const repoName = repo.trim();
      if (!ownerLogin || !repoName) {
        setProjectRepoStatus("Choose a GitHub repository.");
        return;
      }
      setProjectRepoLoading(true);
      setProjectRepoStatus("");
      try {
        const metadata = await loadGitHubRepoMetadata(ownerLogin, repoName);
        const desiredBranch =
          branchOverride?.trim() || metadata.default_branch || projectRepoBranch.trim();
        const selectedBranch = metadata.branches.some((branch) => branch.name === desiredBranch)
          ? desiredBranch
          : metadata.default_branch || metadata.branches[0]?.name || "main";
        setProjectRepoMetadata(metadata);
        setProjectRepoBranch(selectedBranch);
        const candidates = await loadGitHubRepoImportCandidates(
          ownerLogin,
          repoName,
          selectedBranch,
        );
        const candidatePaths = candidates.candidates.map((candidate) => candidate.path);
        setProjectRepoCandidates(candidates.candidates);
        setProjectRepoSelectedPaths((current) =>
          projectRepoImportAll
            ? candidatePaths
            : current.length
              ? current.filter((path) => candidatePaths.includes(path))
              : candidatePaths,
        );
        setProjectRepoStatus(
          candidates.candidates.length ? "" : "No importable Python or notebook files found.",
        );
      } catch (error) {
        setProjectRepoMetadata(null);
        setProjectRepoCandidates([]);
        setProjectRepoSelectedPaths([]);
        setProjectRepoStatus(
          error instanceof Error ? error.message : "Repository could not be loaded.",
        );
      } finally {
        setProjectRepoLoading(false);
      }
    },
    [projectRepoBranch, projectRepoImportAll],
  );

  const refreshProjectRepositories = useCallback(
    async (refreshSelected = true) => {
      if (!sessionSettings.has_github_connection) {
        return;
      }
      setProjectRepoListLoading(true);
      setProjectRepoStatus("Refreshing GitHub repositories...");
      try {
        const response = await loadGitHubRepositories();
        setProjectRepoList(response.repos);
        if (
          refreshSelected &&
          projectRepoMode === "existing" &&
          projectRepoOwner.trim() &&
          projectRepoName.trim()
        ) {
          await loadProjectRepoDetails(projectRepoOwner, projectRepoName, projectRepoBranch);
        } else {
          setProjectRepoStatus(
            response.repos.length
              ? `${response.repos.length} repositories available.`
              : "No accessible repositories found. Create a new repo to continue.",
          );
        }
      } catch (error) {
        setProjectRepoStatus(
          error instanceof Error ? error.message : "GitHub repositories could not be loaded.",
        );
      } finally {
        setProjectRepoListLoading(false);
      }
    },
    [
      loadProjectRepoDetails,
      projectRepoBranch,
      projectRepoMode,
      projectRepoName,
      projectRepoOwner,
      sessionSettings.has_github_connection,
    ],
  );

  const selectProjectRepo = useCallback(
    (repo: GitHubRepoSummary | null) => {
      setProjectRepoDropdownOpen(false);
      setProjectRepoSelectedPaths([]);
      setProjectRepoImportAll(true);
      if (repo === null) {
        setProjectRepoMode("create");
        setProjectRepoOwner(sessionSettings.github_login || "");
        if (projectRepoMode !== "create") {
          setProjectRepoName("");
        }
        setProjectRepoBranch("main");
        setProjectRepoMetadata(null);
        setProjectRepoCandidates([]);
        setProjectRepoStatus("");
        return;
      }
      setProjectRepoMode("existing");
      setProjectRepoOwner(repo.owner);
      setProjectRepoName(repo.name);
      setProjectRepoBranch(repo.default_branch || "main");
      void loadProjectRepoDetails(repo.owner, repo.name, repo.default_branch || "main");
    },
    [loadProjectRepoDetails, projectRepoMode, sessionSettings.github_login],
  );

  const openCreateProjectModal = useCallback(() => {
    setProjectModalState({ mode: "create" });
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
    setProjectRepoMode("");
    setProjectRepoOwner(sessionSettings.github_login || "");
    setProjectRepoName("");
    setProjectRepoBranch("");
    setProjectRepoMetadata(null);
    setProjectRepoCandidates([]);
    setProjectRepoSelectedPaths([]);
    setProjectRepoImportAll(true);
    setProjectRepoLoading(false);
    setProjectRepoDropdownOpen(false);
    setProjectAdvancedOpen(false);
    setProjectAdvancedTab("inference");
    setProjectAdvancedStatus("");
    setProjectAnthropicKey("");
    setProjectModalTokenId("");
    setProjectModalTokenSecret("");
    setProjectAdvancedSaving(null);
    setProjectRepoStatus("");
    setProjectMenuProjectId(null);
    setEmptyStateProjectPickerOpen(false);
    setEmptyStateProjectSearch("");
    if (sessionSettings.has_github_connection) {
      void refreshProjectRepositories(false);
    }
  }, [
    refreshProjectRepositories,
    sessionSettings.github_login,
    sessionSettings.has_github_connection,
  ]);

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
      setProjectRepoStatus("");
      setProjectMenuProjectId(null);
    },
    [projects],
  );

  const closeProjectModal = useCallback(() => {
    setProjectModalState(null);
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
    setProjectRepoMode("");
    setProjectRepoOwner("");
    setProjectRepoName("");
    setProjectRepoBranch("");
    setProjectRepoMetadata(null);
    setProjectRepoCandidates([]);
    setProjectRepoSelectedPaths([]);
    setProjectRepoImportAll(true);
    setProjectRepoLoading(false);
    setProjectRepoDropdownOpen(false);
    setProjectAdvancedOpen(false);
    setProjectAdvancedTab("inference");
    setProjectAdvancedStatus("");
    setProjectAnthropicKey("");
    setProjectModalTokenId("");
    setProjectModalTokenSecret("");
    setProjectAdvancedSaving(null);
    setProjectRepoStatus("");
  }, []);

  const loadExistingProjectRepo = useCallback(
    async (branchOverride?: string) => {
      await loadProjectRepoDetails(projectRepoOwner, projectRepoName, branchOverride);
    },
    [loadProjectRepoDetails, projectRepoName, projectRepoOwner],
  );

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
        if (!sessionSettings.has_github_connection) {
          setProjectFormError("Connect GitHub before creating a project.");
          return;
        }
        if (!projectRepoMode) {
          setProjectFormError("Choose a GitHub repository.");
          return;
        }
        const repoName = projectRepoName.trim();
        const existingRepoImportPaths = projectRepoImportAll
          ? projectRepoCandidates.map((candidate) => candidate.path)
          : projectRepoSelectedPaths;
        const githubRepo =
          projectRepoMode === "create"
            ? {
                mode: "create" as const,
                name: repoName,
                base_branch: "main",
                import_paths: [],
              }
            : {
                mode: "existing" as const,
                owner: projectRepoOwner.trim(),
                name: repoName,
                base_branch: projectRepoBranch.trim(),
                import_paths: existingRepoImportPaths,
              };
        if (!githubRepo.name) {
          setProjectFormError("GitHub repository name is required.");
          return;
        }
        if (githubRepo.mode === "existing" && (!githubRepo.owner || !githubRepo.base_branch)) {
          setProjectFormError("Existing repositories require an owner and base branch.");
          return;
        }
        const project = await createProject(name, projectFormDescription.trim(), githubRepo);
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
    projectRepoBranch,
    projectRepoCandidates,
    projectRepoImportAll,
    projectRepoMode,
    projectRepoName,
    projectRepoOwner,
    projectRepoSelectedPaths,
    refreshProjects,
    sessionSettings.has_github_connection,
  ]);

  const saveProjectInferenceKey = useCallback(async () => {
    const apiKey = projectAnthropicKey.trim();
    if (!apiKey) {
      setProjectAdvancedStatus("Enter an OpenAI API key.");
      return;
    }
    setProjectAdvancedSaving("inference");
    setProjectAdvancedStatus("");
    try {
      const nextSession = await api<BrowserSessionResponse>("/v1/browser/settings/openai-key", {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      });
      setSession(nextSession);
      setProjectAnthropicKey("");
      setProjectAdvancedStatus("Inference key saved.");
    } catch (error) {
      setProjectAdvancedStatus(error instanceof Error ? error.message : "Saving API key failed.");
    } finally {
      setProjectAdvancedSaving(null);
    }
  }, [projectAnthropicKey]);

  const saveProjectModalCredentials = useCallback(async () => {
    const tokenId = projectModalTokenId.trim();
    const tokenSecret = projectModalTokenSecret.trim();
    if (!tokenId || !tokenSecret) {
      setProjectAdvancedStatus("Enter both Modal credential fields.");
      return;
    }
    setProjectAdvancedSaving("sandbox");
    setProjectAdvancedStatus("");
    try {
      const nextSession = await api<BrowserSessionResponse>(
        "/v1/browser/settings/modal-credentials",
        {
          method: "PUT",
          body: JSON.stringify({ token_id: tokenId, token_secret: tokenSecret }),
        },
      );
      setSession(nextSession);
      setProjectModalTokenId("");
      setProjectModalTokenSecret("");
      setProjectAdvancedStatus("Sandbox credentials saved.");
    } catch (error) {
      setProjectAdvancedStatus(
        error instanceof Error ? error.message : "Saving Modal credentials failed.",
      );
    } finally {
      setProjectAdvancedSaving(null);
    }
  }, [projectModalTokenId, projectModalTokenSecret]);

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

  const openDeletePythonFileModal = useCallback(
    (projectId: string, fileId: string, title: string) => {
      setDeleteModalState({ kind: "python_file", projectId, fileId, title });
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
        removePythonFileIdsLocally([...new Set([...deletedFileIds, ...cachedFileIds])], projectId);
        setActiveTab((currentActive) => {
          if (
            !currentActive ||
            currentActive.kind !== "project_home" ||
            currentActive.id !== projectId
          ) {
            return currentActive;
          }
          return null;
        });
        setProjectItems((current) => {
          const next = { ...current };
          delete next[projectId];
          return next;
        });
        setExpandedProjectIds((current) => current.filter((value) => value !== projectId));
        await refreshProjects();
      } else if (deleteModalState.kind === "thread") {
        await deleteThread(deleteModalState.threadId);
        removeThreadIdsLocally([deleteModalState.threadId], deleteModalState.projectId);
        await refreshProjects();
      } else {
        await deletePythonFile(deleteModalState.fileId);
        removePythonFileIdsLocally([deleteModalState.fileId], deleteModalState.projectId);
        await refreshProjects();
      }
      closeDeleteModal();
    } catch (error) {
      setComposerStatus(error instanceof Error ? error.message : "Delete failed.");
    }
  }, [
    closeDeleteModal,
    deleteModalState,
    refreshProjects,
    removePythonFileIdsLocally,
    removeThreadIdsLocally,
  ]);

  const handleProjectStartChat = useCallback(
    async (projectId: string) => {
      setSelectedProjectId(projectId);
      setMobileSidebarOpen(false);
      setAccountMenuOpen(false);
      setProjectMenuProjectId(null);
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
    [activeThread, createNewThread, moveThreadBetweenProjectItems, refreshProjects],
  );

  const handleLoadMoreItems = useCallback(
    async (projectId: string, itemKind: ProjectItemKind) => {
      await fetchProjectItems(projectId, true, itemKind);
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

  const handleImportNotebookUpload = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] ?? null;
      event.target.value = "";
      if (!file) {
        return;
      }
      if (!sessionSettings.has_github_connection) {
        showErrorFlash("Connect GitHub before importing notebooks.");
        openAuthModal("settings", "version-control");
        return;
      }
      const projectId = selectedProjectIdRef.current ?? projectsRef.current[0]?.project_id;
      if (!projectId) {
        setComposerStatus("Create a project before importing a notebook.");
        return;
      }
      try {
        const imported = await importProjectNotebook(projectId, file);
        setPythonFileMap((current) => ({ ...current, [imported.file_id]: imported }));
        setPythonFileDrafts((current) => ({
          ...current,
          [imported.file_id]: { content: imported.content, dirty: false },
        }));
        setPythonFileRuns((current) => ({
          ...current,
          [imported.file_id]: { running: false, result: null, error: "" },
        }));
        upsertPythonFileIntoProjectItems(imported);
        setActiveTab({ kind: "python_file", id: imported.file_id });
        setSelectedProjectId(imported.project_id);
        await refreshProjects();
      } catch (error) {
        setComposerStatus(
          error instanceof Error ? error.message : "Notebook could not be imported.",
        );
      }
    },
    [
      openAuthModal,
      refreshProjects,
      sessionSettings.has_github_connection,
      showErrorFlash,
      upsertPythonFileIntoProjectItems,
    ],
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
          const resolvedContent = resolutionsByPath.get(pythonRepoPathForFile(pythonFile));
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
          const resolvedContent = resolutionsByPath.get(pythonRepoPathForFile(pythonFile));
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
        return { sync, conflict: null as ReturnType<typeof projectRepoConflictFromError> };
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
        const result = await runPythonFile(fileId, "full");
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
      const result = await runPythonFile(activePythonFileId, "full");
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
  const threadDebugAccessory =
    showThreadDebugButton && activeThreadDebugBundle ? (
      <button
        type="button"
        className="thread-debug-icon-button"
        aria-label="Open chat debug info"
        title="Open chat debug info"
        onClick={() => setThreadDebugModalOpen(true)}
      >
        <span aria-hidden="true">i</span>
      </button>
    ) : null;
  const composerActionAccessory =
    composerContextUsageAccessory || threadDebugAccessory ? (
      <>
        {composerContextUsageAccessory}
        {threadDebugAccessory}
      </>
    ) : null;

  const composerEditor = (
    <LandingMentionEditor
      actionAccessory={composerActionAccessory}
      attachButtonId="composer-upload-trigger"
      disabled={Boolean(streamingThreadId)}
      formId="composer-form"
      inputId="composer-input"
      onAttach={() => appUploadRef.current?.click()}
      onChange={setComposerInput}
      onStop={handleStopStreaming}
      onSubmit={() => void handleComposerSubmit()}
      onTagsChange={setComposerTags}
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

  const projectCreateBlocked =
    projectModalState?.mode === "create" && !sessionSettings.has_github_connection;
  const selectedRepoFullName =
    projectRepoMode === "existing" && projectRepoOwner && projectRepoName
      ? `${projectRepoOwner}/${projectRepoName}`
      : "";
  const projectRepoSelectorLabel =
    projectRepoMode === "create" ? "Create new repo" : selectedRepoFullName || "Select repository";
  const projectRepoBranchOptions =
    projectRepoMode === "create"
      ? [{ name: "main", commit_sha: "", is_default: true }]
      : projectRepoMode === "existing" && projectRepoMetadata?.branches.length
        ? projectRepoMetadata.branches
        : [{ name: "", commit_sha: "", is_default: false }];

  return (
    <>
      {!authenticated ? (
        trendingBrowseState ? (
          <main id="landing-page" className="landing-page landing-page-trending">
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
            <div className="landing-column landing-column-wide">
              <a
                className="brandline brandline-link"
                href="https://mv37.org"
                target="_blank"
                rel="noreferrer"
              >
                MV37
              </a>
              <TrendingPapersPage
                variant="public"
                authenticated={false}
                browseState={trendingBrowseState}
                onChangeBrowseState={updateTrendingBrowseState}
                onRequireAuth={() => openAuthModal("login")}
              />
            </div>
          </main>
        ) : (
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
              <div className="landing-intro">
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
                  onTagsChange={setLandingTags}
                  searchPapers={suggestBrowserPapers}
                  submitButtonId="landing-start-button"
                  submitLabel="Start"
                  value={landingPrompt}
                />
              </div>

              <section className="landing-paper-section">
                <TrendingPapersPage
                  variant="landing"
                  authenticated={false}
                  browseState={landingTrendingBrowseState}
                  onChangeBrowseState={setLandingTrendingBrowseState}
                  onRequireAuth={() => openAuthModal("login")}
                  feedTitle="Trending papers"
                  feedDescription=""
                  pageSize={LANDING_TRENDING_PAGE_SIZE}
                  showPagination={false}
                />
              </section>
            </div>
          </main>
        )
      ) : null}

      {authenticated ? (
        <div
          id="app-shell"
          className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}${mobileSidebarOpen ? " mobile-sidebar-open" : ""}${paperDrawerOpen ? " paper-drawer-open" : ""}${paperExplorationSidebarOpen ? " paper-exploration-sidebar-open" : ""}`}
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
                  type="button"
                  id="new-chat-button"
                  className="sidebar-quick-action"
                  onClick={() => (hasProjects ? void handleNewChat() : openCreateProjectModal())}
                >
                  <AppIcon
                    src={staticAssetPath(hasProjects ? "edit.svg" : "new-project.svg")}
                    className="sidebar-quick-action-icon"
                  />
                  <span className="sidebar-quick-action-label">
                    {hasProjects ? "New chat" : "Create project"}
                  </span>
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

            <section className="sidebar-section sidebar-card workspace-nav-section">
              <div className="sidebar-section-header">
                <div className="section-label sidebar-section-label">Browse</div>
              </div>
              <div className="workspace-nav-list">
                {workspaceSidebarItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`workspace-nav-item${activeWorkspacePageId === item.id ? " active" : ""}`}
                    onClick={() => openWorkspacePage(item.id)}
                  >
                    <AppIcon
                      src={staticAssetPath(item.asset)}
                      className="workspace-nav-item-icon"
                    />
                    <span className="workspace-nav-item-label">{item.label}</span>
                  </button>
                ))}
              </div>
            </section>

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
                {projects.length === 0 ? (
                  <div className="project-list-empty">
                    <p>No projects yet.</p>
                    <button type="button" className="sidebar-link" onClick={openCreateProjectModal}>
                      Create project
                    </button>
                  </div>
                ) : (
                  projects.map((project) => {
                    const expanded = expandedProjectIds.includes(project.project_id);
                    const itemState = projectItems[project.project_id];
                    const collapsedCategories =
                      collapsedProjectCategories[project.project_id] ?? {};
                    const visibleProjectItems = itemState?.items ?? [];
                    const codeItems = visibleProjectItems.filter(
                      (item) => item.item_kind === "python_file",
                    );
                    const sessionItems = visibleProjectItems.filter((item) => {
                      if (item.item_kind !== "thread") {
                        return false;
                      }
                      return shouldShowThreadInSidebar({
                        thread_id: item.thread_id ?? "",
                        project_id: item.project_id,
                        title: item.title,
                        created_at: item.created_at,
                        updated_at: item.updated_at,
                        last_message_preview: item.last_message_preview,
                      });
                    });
                    const projectExplorations = paperExplorations
                      .filter((exploration) => exploration.projectId === project.project_id)
                      .sort(
                        (left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt),
                      );
                    const codeHasMore = Boolean(itemState?.hasMoreByKind?.python_file);
                    const sessionsHasMore = Boolean(itemState?.hasMoreByKind?.thread);
                    const codeLoadingMore = Boolean(itemState?.loadingMoreByKind?.python_file);
                    const sessionsLoadingMore = Boolean(itemState?.loadingMoreByKind?.thread);
                    const allProjectCategoriesCollapsed = PROJECT_SIDEBAR_CATEGORY_KEYS.every(
                      (key) => Boolean(collapsedCategories[key]),
                    );
                    const expandProjectSections = !expanded || allProjectCategoriesCollapsed;
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
                              aria-label={
                                expandProjectSections
                                  ? `Expand all sections in ${project.name}`
                                  : `Collapse all sections in ${project.name}`
                              }
                              onMouseDown={(event) => event.stopPropagation()}
                              onClick={(event) => {
                                event.stopPropagation();
                                void toggleAllProjectCategories(project.project_id);
                              }}
                            >
                              <AppIcon
                                src={staticAssetPath(
                                  expandProjectSections
                                    ? "expand-projects.svg"
                                    : "collapse-projects.svg",
                                )}
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
                            {itemState?.loaded || projectExplorations.length > 0 ? (
                              <>
                                <div className="project-tree">
                                  <div className="project-tree-category">
                                    <div className="project-tree-category-header">
                                      <button
                                        type="button"
                                        className="project-tree-category-label"
                                        aria-expanded={!collapsedCategories.notes}
                                        onClick={() =>
                                          toggleProjectCategory(project.project_id, "notes")
                                        }
                                      >
                                        <span
                                          className="project-tree-category-chevron"
                                          aria-hidden="true"
                                        />
                                        <MaskedAssetIcon
                                          asset="notes.svg"
                                          className="project-tree-category-icon"
                                        />
                                        <span>Explorations</span>
                                      </button>
                                      <div className="project-tree-category-actions">
                                        <button
                                          type="button"
                                          className="sidebar-icon-button project-action-button"
                                          aria-label={`Start exploration in ${project.name}`}
                                          onClick={() => openExplorationPicker(project.project_id)}
                                        >
                                          <AppIcon
                                            src={staticAssetPath("new-project.svg")}
                                            className="project-action-icon"
                                          />
                                        </button>
                                      </div>
                                    </div>
                                    {!collapsedCategories.notes &&
                                    projectExplorations.length > 0 ? (
                                      <div className="thread-list project-thread-list project-category-list">
                                        {projectExplorations.map((exploration) => (
                                          <div
                                            key={exploration.explorationId}
                                            role="button"
                                            tabIndex={0}
                                            className={`thread-card paper-exploration-card${activePaperExplorationId === exploration.explorationId ? " active" : ""}`}
                                            title={exploration.title}
                                            onClick={() =>
                                              openPaperExploration(exploration.explorationId)
                                            }
                                            onKeyDown={(event) => {
                                              if (event.key === "Enter" || event.key === " ") {
                                                event.preventDefault();
                                                openPaperExploration(exploration.explorationId);
                                              }
                                            }}
                                          >
                                            <div className="thread-card-copy paper-exploration-card-copy">
                                              <strong className="thread-card-title">
                                                {exploration.title}
                                              </strong>
                                            </div>
                                            <span className="thread-card-age">
                                              {formatThreadAge(exploration.updatedAt)}
                                            </span>
                                            <button
                                              type="button"
                                              className="thread-card-delete"
                                              aria-label={`Delete exploration ${exploration.title}`}
                                              onClick={(event) => {
                                                event.stopPropagation();
                                                deletePaperExploration(exploration.explorationId);
                                              }}
                                            >
                                              <AppIcon
                                                src={staticAssetPath("delete-session.svg")}
                                                className="thread-card-delete-icon"
                                              />
                                            </button>
                                          </div>
                                        ))}
                                      </div>
                                    ) : !collapsedCategories.notes ? (
                                      <div className="project-tree-empty">No explorations yet.</div>
                                    ) : null}
                                  </div>
                                  <div className="project-tree-category">
                                    <div className="project-tree-category-header">
                                      <button
                                        type="button"
                                        className="project-tree-category-label"
                                        aria-expanded={!collapsedCategories.sessions}
                                        onClick={() =>
                                          toggleProjectCategory(project.project_id, "sessions")
                                        }
                                      >
                                        <span
                                          className="project-tree-category-chevron"
                                          aria-hidden="true"
                                        />
                                        <MaskedAssetIcon
                                          asset="sessions.svg"
                                          className="project-tree-category-icon"
                                        />
                                        <span>Conversations</span>
                                      </button>
                                      <div className="project-tree-category-actions">
                                        <button
                                          type="button"
                                          className="sidebar-icon-button project-action-button"
                                          aria-label={`Start conversation in ${project.name}`}
                                          onClick={() =>
                                            void handleProjectStartChat(project.project_id)
                                          }
                                        >
                                          <AppIcon
                                            src={staticAssetPath("edit.svg")}
                                            className="project-action-icon"
                                          />
                                        </button>
                                      </div>
                                    </div>
                                    {!collapsedCategories.sessions && sessionItems.length > 0 ? (
                                      <div className="thread-list project-thread-list project-category-list">
                                        {sessionItems.map((item) => {
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
                                                <strong className="thread-card-title">
                                                  {title}
                                                </strong>
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
                                        })}
                                      </div>
                                    ) : !collapsedCategories.sessions && itemState?.loaded ? (
                                      <div className="project-tree-empty">
                                        No conversations yet.
                                      </div>
                                    ) : null}
                                    {!collapsedCategories.sessions &&
                                    itemState?.loaded &&
                                    sessionsHasMore ? (
                                      <button
                                        type="button"
                                        className="project-show-more"
                                        disabled={sessionsLoadingMore}
                                        onClick={() =>
                                          void handleLoadMoreItems(project.project_id, "thread")
                                        }
                                      >
                                        {sessionsLoadingMore ? "Loading..." : "Show more"}
                                      </button>
                                    ) : null}
                                  </div>
                                  <div className="project-tree-category">
                                    <div className="project-tree-category-header">
                                      <button
                                        type="button"
                                        className="project-tree-category-label"
                                        aria-expanded={!collapsedCategories.code}
                                        onClick={() =>
                                          toggleProjectCategory(project.project_id, "code")
                                        }
                                      >
                                        <span
                                          className="project-tree-category-chevron"
                                          aria-hidden="true"
                                        />
                                        <MaskedAssetIcon
                                          asset="code.svg"
                                          className="project-tree-category-icon"
                                        />
                                        <span>Code</span>
                                      </button>
                                      <div className="project-tree-category-actions">
                                        {project.github_repo_url ? (
                                          <a
                                            href={project.github_repo_url}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="project-tree-repo-link"
                                            aria-label={`Open GitHub repository for ${project.name}`}
                                            title="Open GitHub repository"
                                            onClick={(event) => event.stopPropagation()}
                                          >
                                            <MaskedAssetIcon
                                              asset="git.svg"
                                              className="project-tree-repo-icon"
                                            />
                                          </a>
                                        ) : null}
                                      </div>
                                    </div>
                                    {!collapsedCategories.code && codeItems.length > 0 ? (
                                      <div className="thread-list project-thread-list project-category-list">
                                        {(() => {
                                          const duplicateKeys =
                                            duplicatePythonFilenameKeys(codeItems);
                                          return codeItems.map((item) => {
                                            const displayFile = {
                                              filename: item.filename || item.title,
                                              repo_path: item.repo_path,
                                            };
                                            const title = displayPythonFileTitle(
                                              displayFile,
                                              duplicateKeys,
                                            );
                                            const pathContext = displayPythonFilePathContext(
                                              displayFile,
                                              duplicateKeys,
                                            );
                                            return (
                                              <div
                                                key={item.item_id}
                                                role="button"
                                                tabIndex={0}
                                                className={`thread-card python-file-card${activePythonFileId === item.file_id ? " active" : ""}`}
                                                title={pathContext || title}
                                                onClick={() =>
                                                  void handlePythonFileSelection(item.file_id ?? "")
                                                }
                                                onKeyDown={(event) => {
                                                  if (event.key === "Enter" || event.key === " ") {
                                                    event.preventDefault();
                                                    void handlePythonFileSelection(
                                                      item.file_id ?? "",
                                                    );
                                                  }
                                                }}
                                              >
                                                <div className="thread-card-copy python-file-card-copy">
                                                  <AppIcon
                                                    src={staticAssetPath(
                                                      item.file_kind === "notebook"
                                                        ? "note.svg"
                                                        : "python.svg",
                                                    )}
                                                    className="python-file-card-icon"
                                                  />
                                                  <span className="python-file-card-text">
                                                    <strong className="thread-card-title">
                                                      {title}
                                                    </strong>
                                                    {pathContext ? (
                                                      <span className="thread-card-subtitle">
                                                        {pathContext}
                                                      </span>
                                                    ) : null}
                                                  </span>
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
                                                <button
                                                  type="button"
                                                  className="thread-card-delete"
                                                  aria-label={`Delete ${title}`}
                                                  onClick={(event) => {
                                                    event.stopPropagation();
                                                    openDeletePythonFileModal(
                                                      item.project_id,
                                                      item.file_id ?? "",
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
                                          });
                                        })()}
                                      </div>
                                    ) : !collapsedCategories.code && itemState?.loaded ? (
                                      <div className="project-tree-empty">No code files yet.</div>
                                    ) : null}
                                    {!collapsedCategories.code &&
                                    itemState?.loaded &&
                                    codeHasMore ? (
                                      <button
                                        type="button"
                                        className="project-show-more"
                                        disabled={codeLoadingMore}
                                        onClick={() =>
                                          void handleLoadMoreItems(
                                            project.project_id,
                                            "python_file",
                                          )
                                        }
                                      >
                                        {codeLoadingMore ? "Loading..." : "Show more"}
                                      </button>
                                    ) : null}
                                  </div>
                                  <button
                                    type="button"
                                    className={`project-tree-home${activeProjectHomeId === project.project_id ? " active" : ""}`}
                                    onClick={() => openProjectHome(project.project_id)}
                                  >
                                    <MaskedAssetIcon
                                      asset="home.svg"
                                      className="project-tree-category-icon"
                                    />
                                    <span>Home</span>
                                  </button>
                                </div>
                              </>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    );
                  })
                )}
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

          <section className="chat-stage">
            <button
              type="button"
              className="sidebar-icon-button chat-sidebar-toggle"
              aria-label={mobileSidebarOpen ? "Close sidebar" : "Open sidebar"}
              onClick={handleSidebarToggle}
            >
              <AppIcon
                src={staticAssetPath(mobileSidebarOpen ? "hide-sidebar.svg" : "show-sidebar.svg")}
                className="sidebar-toggle-icon"
              />
            </button>
            {showChatThreadHeader ? (
              <ChatThreadHeader
                threadPane={threadPane}
                chatRunning={activeStreamInView}
                menuOpen={chatHeaderMenuOpen}
                onSelectPane={setThreadPane}
                onToggleMenu={() => setChatHeaderMenuOpen((current) => !current)}
                onExportMarkdown={() => exportActiveThread("markdown")}
                onExportPdf={() => exportActiveThread("pdf")}
                onRename={openHeaderRenameThread}
                onDelete={() => {
                  setChatHeaderMenuOpen(false);
                  openDeleteThreadModal(
                    activeThread.project_id,
                    activeThread.thread_id,
                    displayThreadTitle(activeThread.title),
                  );
                }}
              />
            ) : null}

            <div
              ref={messageListRef}
              className={`chat-message-list${isChatEmpty ? " empty" : ""}${showChatThreadHeader ? " thread-header-visible" : ""}${activeTab?.kind === "python_file" ? " python-file-view" : ""}${activeTab?.kind === "paper_exploration" ? " paper-exploration-view" : ""}${isWorkspaceView ? " workspace-view" : ""}${isAdminWorkspaceView ? " workspace-admin-view" : ""}`}
              onScroll={handleMessageListScroll}
            >
              {workspaceView ? (
                workspaceView.kind === "trending" ? (
                  <div className="workspace-page workspace-page-wide">
                    <TrendingPapersPage
                      variant="workspace"
                      authenticated={authenticated}
                      browseState={
                        trendingBrowseState ?? {
                          date: localDateString(),
                          sort: "default",
                          page: 1,
                          paperId: null,
                        }
                      }
                      onChangeBrowseState={(nextState) => updateTrendingBrowseState(nextState)}
                      onRequireAuth={() => openAuthModal("login")}
                    />
                  </div>
                ) : sessionSummary?.isAdmin ? (
                  <div className="workspace-page workspace-page-admin">
                    <AdminOpsPanel
                      view={
                        workspaceView.kind === "admin_job_papers"
                          ? { kind: "papers", rootJobId: workspaceView.rootJobId }
                          : { kind: "jobs" }
                      }
                      onBackToJobs={() => setWorkspaceView({ kind: "admin_jobs" })}
                      onError={showErrorFlash}
                      onNote={showNoteFlash}
                      onOpenJobPapers={openAdminJobPapers}
                    />
                  </div>
                ) : (
                  <div className="workspace-page">
                    <div className="workspace-page-header">
                      <span>Control panel</span>
                      <h1>Admin access required</h1>
                      <p>This area is only available to allowlisted admin accounts.</p>
                    </div>
                  </div>
                )
              ) : activeTab?.kind === "paper_exploration" ? (
                activePaperExploration ? (
                  <div className="paper-exploration-page">
                    <header className="paper-exploration-header">
                      <div className="paper-exploration-heading">
                        <span>Exploration</span>
                        <h1>{activePaperExploration.title}</h1>
                        {activePaperExploration.authors ? (
                          <p>{activePaperExploration.authors}</p>
                        ) : null}
                      </div>
                      <div className="paper-exploration-header-actions">
                        <button
                          type="button"
                          className="paper-exploration-panel-toggle"
                          aria-label={
                            paperExplorationSidebarOpen
                              ? "Collapse paper details"
                              : "Open paper details"
                          }
                          aria-expanded={paperExplorationSidebarOpen}
                          onClick={
                            paperExplorationSidebarOpen
                              ? closePaperExplorationSidebar
                              : openPaperExplorationSidebar
                          }
                        >
                          <MaskedAssetIcon
                            asset="info.svg"
                            className="paper-exploration-action-icon"
                          />
                          <span>Paper details</span>
                        </button>
                        <button
                          type="button"
                          className="paper-exploration-delete"
                          onClick={() =>
                            deletePaperExploration(activePaperExploration.explorationId)
                          }
                        >
                          Delete exploration
                        </button>
                      </div>
                    </header>
                    {activePaperExploration.pdfUrl ? (
                      <PdfDocumentViewer
                        className="paper-exploration-frame"
                        title={`${activePaperExploration.title} PDF`}
                        url={activePaperExploration.pdfUrl}
                      />
                    ) : (
                      <div className="paper-exploration-empty">
                        No PDF URL is available for this paper.
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="paper-exploration-page missing">
                    <div className="paper-exploration-empty">Exploration not found.</div>
                  </div>
                )
              ) : activeTab?.kind === "project_home" ? (
                activeProjectHome ? (
                  <div className="project-home-page">
                    <div className="project-home-header">
                      <span>Project Home</span>
                      <h1>{activeProjectHome.name}</h1>
                      <p>
                        {activeProjectHome.description || "No project summary has been added yet."}
                      </p>
                    </div>
                    <div className="project-home-grid">
                      <div className="project-home-stat">
                        <span>Code</span>
                        <strong>{activeProjectHomeCodeCount}</strong>
                      </div>
                      <div className="project-home-stat">
                        <span>Sessions</span>
                        <strong>{activeProjectHomeSessionCount}</strong>
                      </div>
                      <div className="project-home-stat">
                        <span>Explorations</span>
                        <strong>{activeProjectHomeExplorationCount}</strong>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="project-home-page">
                    <div className="project-home-header">
                      <span>Project Home</span>
                      <h1>Project not found</h1>
                    </div>
                  </div>
                )
              ) : activeTab?.kind === "python_file" ? (
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
              ) : activeTab?.kind === "thread" &&
                showChatThreadHeader &&
                threadPane === "papers" ? (
                <ThreadPapersView
                  citations={activeThreadCitations}
                  paperCitationPreviews={paperCitationPreviews}
                  openPaperPdfModal={openPaperPdfModal}
                />
              ) : isChatEmpty ? (
                <div className="chat-stage-spacer">
                  {hasProjects ? (
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
                                  onChange={(event) =>
                                    setEmptyStateProjectSearch(event.target.value)
                                  }
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
                      {showChatSetupCard ? (
                        <section className="chat-setup-card" aria-label="Agent setup checklist">
                          <div className="chat-setup-header">
                            <div className="chat-setup-copy">
                              <h2 className="chat-setup-title">
                                Prepare the agent before the first chat
                              </h2>
                              <p className="chat-setup-note">
                                Add your OpenAI API key and connect anything else missing for{" "}
                                {chatSetupProjectName} so the first chat can run cleanly.
                              </p>
                            </div>
                            <button
                              type="button"
                              className="chat-setup-dismiss"
                              aria-label="Dismiss setup checklist"
                              onClick={dismissChatSetupCard}
                            >
                              ×
                            </button>
                          </div>
                          <div className="chat-setup-list">
                            {chatSetupItems.map((item) => (
                              <div
                                key={item.id}
                                className={`chat-setup-item${item.ready ? " ready" : ""}${item.required ? " required" : " optional"}`}
                              >
                                <div className="chat-setup-item-main">
                                  <div
                                    className={`chat-setup-provider-mark provider-${item.provider}`}
                                    aria-hidden="true"
                                  >
                                    {item.provider === "github" ? (
                                      <AppIcon
                                        src={staticAssetPath("github.svg")}
                                        className="chat-setup-provider-icon"
                                      />
                                    ) : item.provider === "openai" ? (
                                      <span className="chat-setup-provider-text">AI</span>
                                    ) : (
                                      <span className="chat-setup-provider-text">M</span>
                                    )}
                                  </div>
                                  <div className="chat-setup-item-copy">
                                    <div className="chat-setup-item-header">
                                      <strong>{item.label}</strong>
                                      {item.ready ? null : (
                                        <span className="chat-setup-state pending">
                                          {item.required ? "Required" : "Optional"}
                                        </span>
                                      )}
                                    </div>
                                    <p>{item.description}</p>
                                  </div>
                                </div>
                                {item.ready ? (
                                  <span
                                    className="chat-setup-check"
                                    aria-label={`${item.label} configured`}
                                    title="Configured"
                                  >
                                    ✓
                                  </span>
                                ) : (
                                  <button
                                    type="button"
                                    className="chat-setup-action"
                                    onClick={() => openChatSetupSettings(item.settingsTab)}
                                  >
                                    {item.actionLabel}
                                  </button>
                                )}
                              </div>
                            ))}
                          </div>
                        </section>
                      ) : null}
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
                        <button
                          type="button"
                          className="chat-empty-action-tag notes"
                          onClick={() => notebookUploadInputRef.current?.click()}
                        >
                          <span className="chat-empty-action-icon" aria-hidden="true">
                            <EmptyStateActionIcon asset="note.svg" />
                          </span>
                          <span className="chat-empty-action-label">Import notebook</span>
                        </button>
                        <button type="button" className="chat-empty-action-tag notes">
                          <span className="chat-empty-action-icon" aria-hidden="true">
                            <EmptyStateActionIcon asset="note.svg" />
                          </span>
                          <span className="chat-empty-action-label">Add exploration</span>
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="chat-empty-state chat-empty-projectless-state">
                      <a
                        className="brandline brandline-link chat-empty-brandline"
                        href="https://mv37.org"
                        target="_blank"
                        rel="noreferrer"
                      >
                        MV37
                      </a>
                      <h1 className="chat-empty-projectless-title">
                        Create a project to get started
                      </h1>
                      <p className="chat-empty-projectless-copy">
                        Projects hold your chats, Python files, and repo-backed code workspace.
                      </p>
                      <button
                        type="button"
                        className="primary-button chat-empty-projectless-action"
                        onClick={openCreateProjectModal}
                      >
                        Create project
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                <div className="chat-message-column">
                  {displayThreadTitle(activeThread.title) !== "New chat" ? (
                    <h1 className="chat-thread-inline-title">
                      {displayThreadTitle(activeThread.title)}
                    </h1>
                  ) : null}
                  {(() => {
                    const commentaryTurnIndexes = new Set<number>();
                    const itemByMessageId = new Map(
                      (activeThread.items ?? [])
                        .filter((item) =>
                          ["user_message", "assistant_message", "compaction"].includes(
                            item.item_type,
                          ),
                        )
                        .map((item) => [item.item_id, item]),
                    );
                    const turnIndexById = new Map<string, number>();
                    for (const [index, turn] of (activeThread.turns ?? []).entries()) {
                      turnIndexById.set(turn.turn_id, index + 1);
                    }
                    for (const item of activeThread.items ?? []) {
                      if (item.turn_id && !turnIndexById.has(item.turn_id)) {
                        turnIndexById.set(item.turn_id, turnIndexById.size + 1);
                      }
                    }
                    const messageTurnIndexes = new Map<string, number>();
                    const userMessagesByTurn = new Map<number, ChatMessageResponse[]>();
                    const commentaryMessagesByTurn = new Map<number, ChatMessageResponse[]>();
                    const completedFinalMessagesByTurn = new Map<number, ChatMessageResponse>();
                    let scannedTurnIndex = 0;
                    let currentTurnIndex = 0;
                    for (const candidate of activeThread.messages) {
                      if (candidate.kind === "compaction") {
                        continue;
                      }
                      const itemTurnId = itemByMessageId.get(candidate.message_id)?.turn_id;
                      const itemTurnIndex = itemTurnId ? turnIndexById.get(itemTurnId) : undefined;
                      if (candidate.role === "user" && candidate.kind !== "compaction") {
                        scannedTurnIndex += 1;
                        currentTurnIndex = itemTurnIndex ?? scannedTurnIndex;
                      }
                      const candidateTurnIndex =
                        itemTurnIndex ?? (currentTurnIndex || scannedTurnIndex || 1);
                      messageTurnIndexes.set(candidate.message_id, candidateTurnIndex);
                      if (candidate.role === "user") {
                        const existing = userMessagesByTurn.get(candidateTurnIndex) ?? [];
                        existing.push(candidate);
                        userMessagesByTurn.set(candidateTurnIndex, existing);
                      }
                      if (candidate.role === "assistant" && candidate.phase === "commentary") {
                        commentaryTurnIndexes.add(candidateTurnIndex);
                        const existing = commentaryMessagesByTurn.get(candidateTurnIndex) ?? [];
                        existing.push(candidate);
                        commentaryMessagesByTurn.set(candidateTurnIndex, existing);
                      }
                      if (
                        candidate.role === "assistant" &&
                        candidate.phase !== "commentary" &&
                        candidate.status === "completed"
                      ) {
                        completedFinalMessagesByTurn.set(candidateTurnIndex, candidate);
                      }
                    }
                    const latestRecommendedMessageId =
                      activeThread.messages
                        .slice()
                        .reverse()
                        .find(
                          (candidate) =>
                            candidate.role === "assistant" &&
                            candidate.kind !== "compaction" &&
                            candidate.status === "completed" &&
                            candidate.phase !== "commentary" &&
                            (candidate.recommended_prompts?.length ?? 0) > 0,
                        )?.message_id ?? null;
                    const renderToolActivityRow = (activity: ToolActivityEntry) => (
                      <div
                        key={activity.id}
                        className={`tool-activity-row status-${activity.status}${activity.isError ? " error" : ""}`}
                      >
                        {toolActivityHasDetails(activity) ? (
                          <details className="tool-activity-details">
                            <summary className="tool-activity-summary">
                              <span
                                className={`tool-activity-label${activity.status === "running" ? " thinking-shimmer" : ""}`}
                              >
                                {formatToolActivityText(activity)}
                              </span>
                              <span className="tool-activity-summary-hint">
                                Command{activity.output ? " and output" : ""}
                              </span>
                            </summary>
                            <div className="tool-activity-content">
                              {activity.command ? (
                                <div className="tool-activity-detail-block">
                                  <span className="tool-activity-detail-label">Command</span>
                                  <pre className="tool-activity-detail-payload">
                                    {activity.command}
                                  </pre>
                                </div>
                              ) : null}
                              {activity.output ? (
                                <div className="tool-activity-detail-block">
                                  <span className="tool-activity-detail-label">Output</span>
                                  <pre className="tool-activity-detail-payload">
                                    {activity.output}
                                  </pre>
                                </div>
                              ) : null}
                            </div>
                          </details>
                        ) : (
                          <span
                            className={`tool-activity-label${activity.status === "running" ? " thinking-shimmer" : ""}`}
                          >
                            {formatToolActivityText(activity)}
                          </span>
                        )}
                      </div>
                    );
                    const renderCollapsedTurnWork = (
                      turnIndex: number,
                      finalMessage: ChatMessageResponse,
                    ) => {
                      const commentaryMessages = commentaryMessagesByTurn.get(turnIndex) ?? [];
                      const turnActivities = activeThreadToolActivitiesByTurn.get(turnIndex) ?? [];
                      if (commentaryMessages.length === 0 && turnActivities.length === 0) {
                        return null;
                      }
                      const startCandidates = [
                        ...(userMessagesByTurn.get(turnIndex) ?? []).map((item) =>
                          timestampMs(item.created_at),
                        ),
                        ...commentaryMessages.map((item) => timestampMs(item.created_at)),
                        ...turnActivities.map((item) => timestampMs(item.startedAt)),
                      ].filter((value): value is number => value !== null);
                      const endCandidates = [
                        timestampMs(finalMessage.created_at),
                        ...turnActivities.map((item) => timestampMs(item.finishedAt)),
                      ].filter((value): value is number => value !== null);
                      const startMs =
                        startCandidates.length > 0 ? Math.min(...startCandidates) : null;
                      const endMs = endCandidates.length > 0 ? Math.max(...endCandidates) : null;
                      return (
                        <details className="turn-work-details">
                          <summary className="turn-work-summary">
                            Worked for {formatWorkedDuration(startMs, endMs)}
                          </summary>
                          <div className="turn-work-content">
                            {commentaryMessages.map((commentaryMessage) => (
                              <article
                                key={commentaryMessage.message_id}
                                className={`message-row assistant status-${commentaryMessage.status}`}
                              >
                                <div className="message-bubble assistant">
                                  <MarkdownRenderer
                                    markdown={commentaryMessage.content_markdown}
                                    className="message-markdown"
                                  />
                                </div>
                              </article>
                            ))}
                            {turnActivities.map(renderToolActivityRow)}
                          </div>
                        </details>
                      );
                    };
                    const renderedToolActivityTurnIndexes = new Set<number>();
                    let userTurnIndex = 0;
                    return activeThread.messages.map((message) => {
                      const isCompactionMessage = message.kind === "compaction";
                      const isCommentaryMessage =
                        message.role === "assistant" && message.phase === "commentary";
                      const isStreamingMessage = isStreamingMessageStatus(message.status);
                      const streamingCodePreview =
                        message.role === "assistant" && isStreamingMessage && !isCompactionMessage
                          ? extractStreamingPythonPreview(message.content_markdown)
                          : null;
                      const messageDisplayMarkdown =
                        streamingCodePreview?.displayMarkdown ?? message.content_markdown;
                      const shouldRenderMessageBubble = !(
                        message.role === "assistant" &&
                        !isCompactionMessage &&
                        streamingCodePreview &&
                        messageDisplayMarkdown.trim() === ""
                      );
                      if (message.role === "user" && !isCompactionMessage) {
                        userTurnIndex += 1;
                      }
                      const messageTurnIndex =
                        messageTurnIndexes.get(message.message_id) ?? (userTurnIndex || 1);
                      const latestToolActivityTurnIndex =
                        activeThreadToolActivitiesByTurn.size > 0
                          ? Math.max(...activeThreadToolActivitiesByTurn.keys())
                          : userTurnIndex;
                      const activityTurnIndex =
                        isCompactionMessage && activeThreadStreamState
                          ? activeThreadStreamState.turnIndex
                          : isCompactionMessage
                            ? latestToolActivityTurnIndex
                            : messageTurnIndex;
                      const completedFinalMessage =
                        completedFinalMessagesByTurn.get(activityTurnIndex) ?? null;
                      const turnHasCollapsedWork =
                        Boolean(completedFinalMessage) &&
                        ((commentaryMessagesByTurn.get(activityTurnIndex) ?? []).length > 0 ||
                          (activeThreadToolActivitiesByTurn.get(activityTurnIndex) ?? []).length >
                            0);
                      if (isCommentaryMessage && turnHasCollapsedWork) {
                        return null;
                      }
                      const isSessionFinalMessage =
                        completedFinalMessage?.message_id === message.message_id;
                      const shouldRenderTurnActivities =
                        !turnHasCollapsedWork &&
                        ((message.role === "user" &&
                          !isCompactionMessage &&
                          !commentaryTurnIndexes.has(activityTurnIndex)) ||
                          isCompactionMessage ||
                          isCommentaryMessage) &&
                        !renderedToolActivityTurnIndexes.has(activityTurnIndex);
                      const turnActivities = shouldRenderTurnActivities
                        ? (activeThreadToolActivitiesByTurn.get(activityTurnIndex) ?? [])
                        : [];
                      if (turnActivities.length > 0) {
                        renderedToolActivityTurnIndexes.add(activityTurnIndex);
                      }
                      const showThinkingPlaceholder =
                        message.role === "user" &&
                        !isCompactionMessage &&
                        activeStreamInView &&
                        activeThreadStreamState?.turnIndex === userTurnIndex &&
                        !activeThreadStreamState.assistantStarted &&
                        turnActivities.length === 0;
                      const hoverTime = formatLocalTimestamp(message.created_at, "");
                      const messagePaperGroups = groupCitationsByPaper(message.citations);
                      const messagePaperCount = messagePaperGroups.length;
                      const messageSourcesExpanded = Boolean(
                        expandedCitationMessages[message.message_id],
                      );
                      const shouldRenderSessionActions =
                        message.role === "assistant" &&
                        !isCompactionMessage &&
                        isSessionFinalMessage;
                      const recommendedPrompts =
                        shouldRenderSessionActions &&
                        message.message_id === latestRecommendedMessageId
                          ? (message.recommended_prompts ?? [])
                          : [];
                      return (
                        <Fragment key={message.message_id}>
                          {isSessionFinalMessage
                            ? renderCollapsedTurnWork(activityTurnIndex, message)
                            : null}
                          <article
                            id={chatMessageRowId(message.message_id)}
                            className={`message-row ${isCompactionMessage ? "compaction" : message.role === "assistant" ? "assistant" : "user"} status-${message.status}`}
                          >
                            {message.status !== "completed" && !isCompactionMessage ? (
                              <div className="message-meta-row">
                                <span
                                  className={`message-status-label${isStreamingMessage ? " thinking-shimmer" : ""}`}
                                >
                                  {isStreamingMessage
                                    ? formatThinkingLabel(activeThreadStreamState?.thinkingWord)
                                    : "Failed"}
                                </span>
                              </div>
                            ) : null}
                            {shouldRenderMessageBubble ? (
                              <div
                                className={`message-bubble ${isCompactionMessage ? "compaction" : message.role === "assistant" ? "assistant" : "user"}${isCompactionMessage && isStreamingMessage ? " thinking-shimmer" : ""}`}
                              >
                                {isCompactionMessage ? (
                                  <MarkdownRenderer
                                    markdown={messageDisplayMarkdown}
                                    className="message-markdown compaction-markdown"
                                  />
                                ) : message.role === "assistant" ? (
                                  <>
                                    <MarkdownRenderer
                                      markdown={messageDisplayMarkdown}
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
                                  </>
                                ) : (
                                  <div className="message-plain-text">
                                    {message.content_markdown}
                                  </div>
                                )}
                              </div>
                            ) : null}
                            {streamingCodePreview ? (
                              <CodeSnippet
                                code={streamingCodePreview.code}
                                language={streamingCodePreview.language}
                                className="preview thinking-shimmer"
                                headerTitle="Writing code"
                                headerMeta={streamingCodePreview.language}
                                dataTestId="streaming-code-preview"
                              />
                            ) : null}
                            {shouldRenderSessionActions ? (
                              <div className="message-actions">
                                <div className="message-actions-left">
                                  <button
                                    type="button"
                                    className="message-action-button"
                                    aria-label="Copy response"
                                    title="Copy response"
                                    onClick={() => void copyMessage(message.content_markdown)}
                                  >
                                    <img
                                      src={staticAssetPath("icons8-copy-50.svg")}
                                      alt=""
                                      aria-hidden="true"
                                    />
                                  </button>
                                  <button
                                    type="button"
                                    className="message-action-button"
                                    aria-label="Retry response"
                                    title="Retry response"
                                    disabled={Boolean(streamingThreadId)}
                                    onClick={() => void retryAssistantResponse(message.message_id)}
                                  >
                                    <img
                                      src={staticAssetPath("icons8-restart-50.svg")}
                                      alt=""
                                      aria-hidden="true"
                                    />
                                  </button>
                                  <button
                                    type="button"
                                    className="message-action-button"
                                    aria-label="Download reply"
                                    title="Download reply"
                                    onClick={() =>
                                      setExportModalState({
                                        source: "message",
                                        messageId: message.message_id,
                                      })
                                    }
                                  >
                                    <img
                                      src={staticAssetPath("icons8-download-50.svg")}
                                      alt=""
                                      aria-hidden="true"
                                    />
                                  </button>
                                  {messagePaperCount > 0 ? (
                                    <button
                                      type="button"
                                      className={`message-sources-button${messageSourcesExpanded ? " active" : ""}`}
                                      aria-expanded={messageSourcesExpanded}
                                      aria-controls={`message-citations-${message.message_id}`}
                                      onClick={() =>
                                        setExpandedCitationMessages((current) => ({
                                          ...current,
                                          [message.message_id]: !current[message.message_id],
                                        }))
                                      }
                                    >
                                      {sourceCountLabel(messagePaperCount)}
                                    </button>
                                  ) : null}
                                  {hoverTime ? (
                                    <span className="message-action-time">{hoverTime}</span>
                                  ) : null}
                                </div>
                                <div className="message-actions-right">
                                  <button
                                    type="button"
                                    className="message-action-button"
                                    aria-label="Like response"
                                    title="Like response"
                                    onClick={() => openFeedbackModal(message, "positive")}
                                  >
                                    <img
                                      src={staticAssetPath("icons8-thumbs-up-50.svg")}
                                      alt=""
                                      aria-hidden="true"
                                    />
                                  </button>
                                  <button
                                    type="button"
                                    className="message-action-button"
                                    aria-label="Dislike response"
                                    title="Dislike response"
                                    onClick={() => openFeedbackModal(message, "negative")}
                                  >
                                    <img
                                      src={staticAssetPath("icons8-thumbs-down-50.svg")}
                                      alt=""
                                      aria-hidden="true"
                                    />
                                  </button>
                                </div>
                              </div>
                            ) : null}
                            {recommendedPrompts.length > 0 ? (
                              <div className="recommended-reading">
                                <div className="recommended-reading-title">Recommended reading</div>
                                {recommendedPrompts.map((prompt) => (
                                  <button
                                    key={prompt}
                                    type="button"
                                    className="recommended-reading-prompt"
                                    disabled={Boolean(streamingThreadId)}
                                    onClick={() => void sendRecommendedPrompt(prompt)}
                                  >
                                    {prompt}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                            {message.role === "assistant" &&
                            messagePaperGroups.length > 0 &&
                            messageSourcesExpanded ? (
                              <div
                                id={`message-citations-${message.message_id}`}
                                className="message-citations citation-compact-list"
                              >
                                <div className="message-citations-items">
                                  {messagePaperGroups.map((group) => {
                                    const citation = group.citation;
                                    const paperPreview = citation.paper_id
                                      ? (paperCitationPreviews[citation.paper_id] ?? null)
                                      : null;
                                    const paper = paperPreview?.paper ?? null;
                                    const actionTarget: PaperActionTarget | null = citation.paper_id
                                      ? {
                                          paperId: citation.paper_id,
                                          path: citation.path,
                                          paper,
                                          title: paper?.title ?? citation.title,
                                          authors: paper?.authors ?? "",
                                          previewImageUrl: paperPreview?.preview?.image_url ?? null,
                                          pdfUrl: paper?.pdf_url ?? null,
                                          projectId: activeThread?.project_id ?? null,
                                        }
                                      : null;
                                    const hasExploration = Boolean(
                                      citation.paper_id &&
                                      paperExplorationIdsByPaperId[citation.paper_id],
                                    );
                                    const explorationId = citation.paper_id
                                      ? paperExplorationIdsByPaperId[citation.paper_id]
                                      : "";
                                    const openCitationPaper = () => {
                                      if (!actionTarget) {
                                        return;
                                      }
                                      if (explorationId) {
                                        openPaperExploration(explorationId);
                                        return;
                                      }
                                      openPaperPdfModal(actionTarget);
                                    };
                                    return (
                                      <div
                                        key={group.key}
                                        id={sourceCardId(message.message_id, citation.source_id)}
                                        className="citation-card citation-card-compact"
                                      >
                                        {group.citationCount > 1 ? (
                                          <span className="paper-citation-count-badge">
                                            {group.citationCount}
                                          </span>
                                        ) : null}
                                        <button
                                          type="button"
                                          className="citation-card-compact-main"
                                          onClick={openCitationPaper}
                                        >
                                          {paperPreview?.preview?.image_url ? (
                                            <img
                                              src={paperPreview.preview.image_url}
                                              alt={`${paper?.title ?? citation.title} preview`}
                                              className="citation-card-compact-image"
                                            />
                                          ) : (
                                            <span className="citation-card-compact-placeholder">
                                              {paperPreview?.status === "loading" ? "" : "PDF"}
                                            </span>
                                          )}
                                          <span className="citation-card-compact-copy">
                                            <span className="citation-card-title-line">
                                              <strong className="citation-card-title">
                                                {paper?.title ?? citation.title}
                                              </strong>
                                            </span>
                                            <span className="citation-card-preview-authors">
                                              {paper?.authors || "Author unavailable"}
                                            </span>
                                          </span>
                                        </button>
                                        {actionTarget ? (
                                          <button
                                            type="button"
                                            className="citation-card-action citation-card-compact-action"
                                            onClick={(event) => {
                                              event.stopPropagation();
                                              void startPaperExploration(actionTarget);
                                            }}
                                          >
                                            {hasExploration
                                              ? "Open exploration"
                                              : "Start exploration"}
                                          </button>
                                        ) : null}
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            ) : null}
                          </article>
                          {turnActivities.map(renderToolActivityRow)}
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

            {showThreadStageChrome ? (
              <div className="chat-composer-fade" aria-hidden="true" />
            ) : null}
            {showThreadStageChrome && showScrollToLatest ? (
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
            {showThreadStageChrome ? (
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
            <input
              ref={notebookUploadInputRef}
              id="project-notebook-upload-file"
              className="hidden-file-input"
              type="file"
              accept=".ipynb,application/x-ipynb+json,application/json"
              onChange={handleImportNotebookUpload}
            />
          </section>

          {paperExplorationSidebarOpen && activePaperExploration ? (
            <aside className="paper-exploration-sidebar" aria-label="Paper details">
              <div
                className="paper-exploration-sidebar-tabs"
                role="tablist"
                aria-label="Paper panel"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={paperExplorationSidebarPane === "details"}
                  className={`paper-exploration-sidebar-tab${paperExplorationSidebarPane === "details" ? " active" : ""}`}
                  onClick={() => setPaperExplorationSidebarPane("details")}
                >
                  Paper Details
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={paperExplorationSidebarPane === "notes"}
                  className={`paper-exploration-sidebar-tab${paperExplorationSidebarPane === "notes" ? " active" : ""}`}
                  onClick={() => setPaperExplorationSidebarPane("notes")}
                >
                  My Notes
                </button>
                <button
                  type="button"
                  className="paper-exploration-sidebar-close"
                  aria-label="Collapse paper details panel"
                  onClick={closePaperExplorationSidebar}
                >
                  ×
                </button>
              </div>
              <div className="paper-exploration-sidebar-body">
                {paperExplorationSidebarPane === "details" ? (
                  <>
                    <details className="paper-exploration-sidebar-section" open>
                      <summary>
                        <MaskedAssetIcon
                          asset="notes.svg"
                          className="paper-exploration-sidebar-section-icon"
                        />
                        <span>In this paper</span>
                        <span className="paper-exploration-sidebar-chevron" aria-hidden="true" />
                      </summary>
                      <ol className="paper-exploration-outline-list">
                        {PAPER_EXPLORATION_OUTLINE_ITEMS.map((item, index) => (
                          <li key={item}>
                            <span className={index === 0 ? "active" : ""}>{item}</span>
                          </li>
                        ))}
                      </ol>
                    </details>
                    <details className="paper-exploration-sidebar-section" open>
                      <summary>
                        <MaskedAssetIcon
                          asset="sessions.svg"
                          className="paper-exploration-sidebar-section-icon"
                        />
                        <span>Authors</span>
                        <span className="paper-exploration-sidebar-chevron" aria-hidden="true" />
                      </summary>
                      {activePaperExplorationAuthors.length > 0 ? (
                        <div className="paper-exploration-author-list">
                          {activePaperExplorationAuthors.slice(0, 8).map((author) => (
                            <div className="paper-exploration-author" key={author}>
                              <span className="paper-exploration-author-avatar" aria-hidden="true">
                                {authorInitials(author).slice(0, 2).toUpperCase()}
                              </span>
                              <span>{author}</span>
                            </div>
                          ))}
                          {activePaperExplorationAuthors.length > 8 ? (
                            <span className="paper-exploration-sidebar-note">
                              {"& "}
                              {activePaperExplorationAuthors.length - 8} more
                            </span>
                          ) : null}
                        </div>
                      ) : (
                        <p className="paper-exploration-sidebar-note">No authors listed.</p>
                      )}
                    </details>
                    <details className="paper-exploration-sidebar-section" open>
                      <summary>
                        <MaskedAssetIcon
                          asset="info.svg"
                          className="paper-exploration-sidebar-section-icon"
                        />
                        <span>Details</span>
                        <span className="paper-exploration-sidebar-chevron" aria-hidden="true" />
                      </summary>
                      <dl className="paper-exploration-detail-list">
                        <div>
                          <dt>Project</dt>
                          <dd>{activePaperExplorationProject?.name ?? "Project"}</dd>
                        </div>
                        <div>
                          <dt>Paper</dt>
                          <dd>{activePaperExploration.path || activePaperExploration.paperId}</dd>
                        </div>
                        <div>
                          <dt>Updated</dt>
                          <dd>{formatThreadAge(activePaperExploration.updatedAt)}</dd>
                        </div>
                      </dl>
                    </details>
                  </>
                ) : (
                  <details className="paper-exploration-sidebar-section" open>
                    <summary>
                      <MaskedAssetIcon
                        asset="note.svg"
                        className="paper-exploration-sidebar-section-icon"
                      />
                      <span>My notes</span>
                      <span className="paper-exploration-sidebar-chevron" aria-hidden="true" />
                    </summary>
                    <textarea
                      className="paper-exploration-notes-input"
                      aria-label="Exploration notes"
                      value={paperExplorationNotes[activePaperExploration.explorationId] ?? ""}
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        setPaperExplorationNotes((current) => ({
                          ...current,
                          [activePaperExploration.explorationId]: nextValue,
                        }));
                      }}
                    />
                  </details>
                )}
              </div>
            </aside>
          ) : null}

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
                {paperDrawerPaper ? (
                  <div className="paper-drawer-details">
                    {paperDrawerPaper.summary_short ? (
                      <section>
                        <span>Summary</span>
                        <p>{paperDrawerPaper.summary_short}</p>
                      </section>
                    ) : null}
                    {paperDrawerPaper.summary_eli5 ? (
                      <section>
                        <span>Simple summary</span>
                        <p>{paperDrawerPaper.summary_eli5}</p>
                      </section>
                    ) : null}
                    {paperDrawerPaper.abstract ? (
                      <section>
                        <span>Abstract</span>
                        <p>{paperDrawerPaper.abstract}</p>
                      </section>
                    ) : null}
                    <div className="paper-drawer-links">
                      {paperDrawerPaper.pdf_url ? (
                        <a href={paperDrawerPaper.pdf_url} target="_blank" rel="noreferrer">
                          PDF
                        </a>
                      ) : null}
                      {paperDrawerPaper.html_url ? (
                        <a href={paperDrawerPaper.html_url} target="_blank" rel="noreferrer">
                          Paper
                        </a>
                      ) : null}
                      {paperDrawerPaper.code_url ? (
                        <a href={paperDrawerPaper.code_url} target="_blank" rel="noreferrer">
                          Code
                        </a>
                      ) : null}
                    </div>
                    <div className="paper-drawer-facts">
                      <span>{paperDrawerPaper.index_status}</span>
                      <span>{paperDrawerPaper.citation_count} citations</span>
                      {paperDrawerPaper.code_index_status ? (
                        <span>Code {paperDrawerPaper.code_index_status}</span>
                      ) : null}
                    </div>
                  </div>
                ) : null}
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

      {explorationPicker ? (
        <div
          className="exploration-picker-shell"
          role="dialog"
          aria-modal="true"
          aria-labelledby="exploration-picker-title"
        >
          <button
            type="button"
            className="exploration-picker-backdrop"
            aria-label="Close exploration picker"
            onClick={closeExplorationPicker}
          />
          <div className="exploration-picker-panel" onKeyDown={handleExplorationPickerKeyDown}>
            <header className="exploration-picker-header">
              <div>
                <span>New exploration</span>
                <h2 id="exploration-picker-title">{explorationPicker.projectName}</h2>
              </div>
              <button
                type="button"
                className="exploration-picker-close"
                aria-label="Close exploration picker"
                onClick={closeExplorationPicker}
              >
                ×
              </button>
            </header>
            <div className="exploration-picker-search">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M10.5 18a7.5 7.5 0 1 1 5.3-12.8 7.5 7.5 0 0 1-5.3 12.8Z" />
                <path d="m16 16 4.5 4.5" />
              </svg>
              <input
                ref={explorationSearchInputRef}
                type="search"
                value={explorationSearch}
                onChange={(event) => setExplorationSearch(event.target.value)}
                placeholder="Search by paper title or arXiv ID"
                aria-label="Search papers for exploration"
              />
              {explorationSearchStatus === "loading" ? (
                <span className="exploration-picker-spinner" aria-hidden="true" />
              ) : null}
            </div>
            <div className="exploration-picker-list" role="listbox" aria-label="Paper results">
              {explorationSearchFallback ? (
                <div className="exploration-picker-banner">
                  No exact matches found. Showing nearby papers.
                </div>
              ) : null}
              {explorationSuggestions.map((paper, index) => {
                const selected = index === explorationSelectedIndex;
                const starting = explorationStartingPaperId === paper.paper_id;
                return (
                  <div
                    key={paper.paper_id}
                    role="option"
                    aria-selected={selected}
                    className={`exploration-picker-item${selected ? " selected" : ""}`}
                    onMouseEnter={() => setExplorationSelectedIndex(index)}
                  >
                    <div className="exploration-picker-item-copy">
                      <strong>{paper.title}</strong>
                      <span>{paper.authors || "Author unavailable"}</span>
                      {paperSuggestionMeta(paper) ? (
                        <small>{paperSuggestionMeta(paper)}</small>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="exploration-picker-item-action"
                      disabled={Boolean(explorationStartingPaperId)}
                      onClick={() => void startExplorationFromSuggestion(paper)}
                    >
                      {starting ? "Starting..." : "Explore"}
                    </button>
                  </div>
                );
              })}
              {explorationSearch.trim().length < 2 ? (
                <div className="exploration-picker-empty">
                  Type at least two characters to search indexed papers.
                </div>
              ) : null}
              {explorationSearchStatus === "ready" &&
              explorationSearch.trim().length >= 2 &&
              explorationSuggestions.length === 0 ? (
                <div className="exploration-picker-empty">No papers match this search.</div>
              ) : null}
              {explorationSearchStatus === "error" ? (
                <div className="exploration-picker-empty error">{explorationSearchError}</div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {paperPdfModal ? (
        <div
          className="paper-pdf-modal-shell"
          role="dialog"
          aria-modal="true"
          aria-label={`${paperPdfModal.title} PDF`}
        >
          <button
            type="button"
            className="paper-pdf-modal-backdrop"
            aria-label="Close paper PDF"
            onClick={closePaperPdfModal}
          />
          <div className="paper-pdf-modal">
            <header className="paper-pdf-modal-header">
              <div className="paper-pdf-modal-title">
                <strong>{paperPdfModal.title}</strong>
                {paperPdfModal.authors ? <span>{paperPdfModal.authors}</span> : null}
              </div>
              <div className="paper-pdf-modal-actions">
                <div className={`paper-pdf-explore-menu${paperExploreMenuOpen ? " open" : ""}`}>
                  <button
                    type="button"
                    className="paper-pdf-explore-primary"
                    onClick={() => {
                      setPaperExploreMenuOpen(false);
                      void startPaperExploration({
                        paperId: paperPdfModal.paperId,
                        path: paperPdfModal.path,
                        paper: paperPdfModal.paper,
                        title: paperPdfModal.title,
                        authors: paperPdfModal.authors,
                        pdfUrl: paperPdfModal.pdfUrl,
                      }).then((didStart) => {
                        if (didStart) {
                          closePaperPdfModal();
                        }
                      });
                    }}
                  >
                    Explore now
                  </button>
                  <button
                    type="button"
                    className="paper-pdf-explore-toggle"
                    aria-label="Explore options"
                    aria-expanded={paperExploreMenuOpen}
                    onClick={() => setPaperExploreMenuOpen((current) => !current)}
                  >
                    <span className="paper-pdf-explore-chevron" aria-hidden="true" />
                  </button>
                  {paperExploreMenuOpen ? (
                    <div className="paper-pdf-explore-popover" role="menu">
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setPaperExploreMenuOpen(false);
                          void startPaperExploration(
                            {
                              paperId: paperPdfModal.paperId,
                              path: paperPdfModal.path,
                              paper: paperPdfModal.paper,
                              title: paperPdfModal.title,
                              authors: paperPdfModal.authors,
                              pdfUrl: paperPdfModal.pdfUrl,
                            },
                            { navigate: false },
                          );
                        }}
                      >
                        Explore later
                      </button>
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  className="paper-pdf-modal-close"
                  aria-label="Close paper PDF"
                  onClick={closePaperPdfModal}
                >
                  ×
                </button>
              </div>
            </header>
            <div className="paper-pdf-modal-body">
              {paperPdfModal.status === "loading" ? (
                <div className="paper-pdf-modal-state">Loading PDF...</div>
              ) : paperPdfModal.status === "error" ? (
                <div className="paper-pdf-modal-state error">{paperPdfModal.error}</div>
              ) : paperPdfModal.pdfUrl ? (
                <PdfDocumentViewer
                  className="paper-pdf-frame"
                  title={`${paperPdfModal.title} PDF`}
                  url={paperPdfModal.pdfUrl}
                />
              ) : (
                <div className="paper-pdf-modal-state">No PDF URL is available.</div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      <div
        id="thread-debug-modal"
        className={`modal${threadDebugModalOpen && activeThreadDebugBundle ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="thread-debug-modal-title"
      >
        <div className="modal-backdrop" onClick={() => setThreadDebugModalOpen(false)} />
        <div className="modal-panel thread-debug-modal-panel">
          <button
            className="modal-close"
            type="button"
            aria-label="Close chat debug info"
            onClick={() => setThreadDebugModalOpen(false)}
          >
            ×
          </button>
          <div className="modal-heading modal-heading-copy">
            <h2 id="thread-debug-modal-title" className="modal-title">
              Chat debug
            </h2>
            <p className="modal-caption">Local identifiers for this thread.</p>
          </div>
          {activeThreadDebugBundle ? (
            <div className="thread-debug-modal-body" data-testid="thread-debug-modal-body">
              <dl className="thread-debug-modal-values">
                <div>
                  <dt>User</dt>
                  <dd>{activeThreadDebugBundle.user_id ?? "none"}</dd>
                </div>
                <div>
                  <dt>Project</dt>
                  <dd>{activeThreadDebugBundle.project_id}</dd>
                </div>
                <div>
                  <dt>Thread</dt>
                  <dd>{activeThreadDebugBundle.thread_id}</dd>
                </div>
                <div>
                  <dt>Last message</dt>
                  <dd>{activeThreadDebugBundle.latest_message_id ?? "none"}</dd>
                </div>
                <div>
                  <dt>Assistant</dt>
                  <dd>{activeThreadDebugBundle.latest_assistant_message_id ?? "none"}</dd>
                </div>
              </dl>
              <button
                type="button"
                className="secondary-button thread-debug-copy-button"
                onClick={() => void copyThreadDebugBundle(activeThreadDebugBundle)}
              >
                Copy debug bundle
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <ChatSessionModals
        exportModalState={exportModalState}
        onCloseExportModal={() => setExportModalState(null)}
        exportActiveThread={exportActiveThread}
        feedbackModalState={feedbackModalState}
        closeFeedbackModal={closeFeedbackModal}
        feedbackReasons={feedbackReasons}
        setFeedbackReasons={setFeedbackReasons}
        feedbackNote={feedbackNote}
        setFeedbackNote={setFeedbackNote}
        feedbackSubmitting={feedbackSubmitting}
        submitFeedbackModal={() => void submitFeedbackModal()}
      />

      <div
        id="thread-rename-modal"
        className={`modal${threadRenameModal ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="thread-rename-modal-title"
      >
        <div className="modal-backdrop" onClick={closeThreadRenameModal} />
        <div className="modal-panel thread-rename-modal-panel">
          <button
            className="modal-close"
            type="button"
            aria-label="Close rename session"
            onClick={closeThreadRenameModal}
          >
            ×
          </button>
          <div className="modal-heading modal-heading-copy">
            <h2 id="thread-rename-modal-title" className="modal-title">
              Rename session
            </h2>
            <p className="modal-caption">{threadRenameModal?.title ?? "Current session"}</p>
          </div>
          {threadRenameModal?.error ? (
            <div className="modal-flash warning" role="alert">
              <span className="modal-flash-text">{threadRenameModal.error}</span>
            </div>
          ) : null}
          <div className="modal-view visible">
            <form className="stack" onSubmit={(event) => void submitThreadRenameModal(event)}>
              <label className="rename-session-field" htmlFor="thread-rename-input">
                <span>Title</span>
                <input
                  id="thread-rename-input"
                  type="text"
                  value={threadRenameModal?.draft ?? ""}
                  onChange={(event) =>
                    setThreadRenameModal((current) =>
                      current ? { ...current, draft: event.target.value, error: "" } : current,
                    )
                  }
                />
              </label>
              <button className="primary-button" type="submit" disabled={threadRenameModal?.saving}>
                {threadRenameModal?.saving ? "Renaming..." : "Rename"}
              </button>
              <button className="secondary-button" type="button" onClick={closeThreadRenameModal}>
                Cancel
              </button>
            </form>
          </div>
        </div>
      </div>

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
        <div className="modal-panel project-modal-panel">
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
              <label
                className={`project-field${projectCreateBlocked ? " project-field-disabled" : ""}`}
                htmlFor="project-name-input"
              >
                <span>Project name</span>
                <input
                  id="project-name-input"
                  name="name"
                  type="text"
                  placeholder="Project name"
                  value={projectFormName}
                  onChange={(event) => setProjectFormName(event.target.value)}
                  disabled={projectCreateBlocked}
                  required
                />
              </label>
              <label
                className={`project-field${projectCreateBlocked ? " project-field-disabled" : ""}`}
                htmlFor="project-description-input"
              >
                <span>Description</span>
                <textarea
                  id="project-description-input"
                  name="description"
                  className="project-description-input"
                  placeholder="Description, goal, or custom instructions"
                  value={projectFormDescription}
                  onChange={(event) => setProjectFormDescription(event.target.value)}
                  disabled={projectCreateBlocked}
                  rows={4}
                />
              </label>
              {projectModalState?.mode === "create" ? (
                sessionSettings.has_github_connection ? (
                  <div className="project-repo-section">
                    <div className="project-github-heading">
                      <AppIcon
                        src={staticAssetPath("github.svg")}
                        className="project-github-icon"
                      />
                      <span>GitHub repository</span>
                    </div>
                    <div className="project-repo-grid">
                      <div className="project-field project-repo-selector-field">
                        <span>Repository</span>
                        <div className="project-repo-dropdown">
                          <button
                            type="button"
                            className="project-repo-trigger"
                            aria-label="Repository"
                            aria-haspopup="listbox"
                            aria-expanded={projectRepoDropdownOpen}
                            onClick={() => setProjectRepoDropdownOpen((open) => !open)}
                          >
                            <span>{projectRepoSelectorLabel}</span>
                          </button>
                          {projectRepoDropdownOpen ? (
                            <div className="project-repo-menu" role="listbox">
                              <button
                                type="button"
                                role="option"
                                aria-selected={projectRepoMode === "create"}
                                className={`project-repo-option${projectRepoMode === "create" ? " active" : ""}`}
                                onClick={() => selectProjectRepo(null)}
                              >
                                <span>Create new repo</span>
                                <small>
                                  Private repository under {sessionSettings.github_login}
                                </small>
                              </button>
                              {projectRepoList.map((repo) => (
                                <button
                                  key={repo.full_name}
                                  type="button"
                                  role="option"
                                  aria-selected={
                                    projectRepoMode === "existing" &&
                                    selectedRepoFullName === repo.full_name
                                  }
                                  className={`project-repo-option${
                                    projectRepoMode === "existing" &&
                                    selectedRepoFullName === repo.full_name
                                      ? " active"
                                      : ""
                                  }`}
                                  onClick={() => selectProjectRepo(repo)}
                                >
                                  <span>{repo.full_name}</span>
                                  <small>
                                    {repo.private ? "Private" : "Public"} · default{" "}
                                    {repo.default_branch || "main"}
                                  </small>
                                </button>
                              ))}
                              {projectRepoListLoading ? (
                                <div className="project-repo-menu-note">
                                  Loading repositories...
                                </div>
                              ) : null}
                              {!projectRepoListLoading && projectRepoList.length === 0 ? (
                                <div className="project-repo-menu-note">
                                  No accessible repositories found.
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      </div>
                      <label
                        className="project-field project-branch-field"
                        htmlFor="project-repo-branch-select"
                      >
                        <span>Base branch</span>
                        <select
                          id="project-repo-branch-select"
                          className="project-repo-branch-select"
                          aria-label="Base branch"
                          value={
                            projectRepoMode === "create"
                              ? "main"
                              : projectRepoMode === "existing"
                                ? projectRepoBranch
                                : ""
                          }
                          disabled={
                            !projectRepoMode ||
                            projectRepoMode === "create" ||
                            projectRepoLoading ||
                            (projectRepoMode === "existing" && !projectRepoMetadata)
                          }
                          onChange={(event) => {
                            const nextBranch = event.target.value;
                            setProjectRepoBranch(nextBranch);
                            void loadExistingProjectRepo(nextBranch);
                          }}
                        >
                          {projectRepoBranchOptions.map((branch) => (
                            <option key={branch.name} value={branch.name}>
                              {branch.name || "Select repo first"}
                              {branch.is_default ? " (default)" : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    {projectRepoMode === "create" ? (
                      <label
                        className="project-field project-new-repo-field"
                        htmlFor="project-repo-name-input"
                      >
                        <span>New repo name</span>
                        <input
                          id="project-repo-name-input"
                          name="repo_name"
                          type="text"
                          placeholder="New GitHub repo name"
                          value={projectRepoName}
                          onChange={(event) => setProjectRepoName(event.target.value)}
                          required
                        />
                      </label>
                    ) : projectRepoMode === "existing" ? (
                      <div className="project-repo-existing">
                        {projectRepoStatus ? (
                          <div className="project-repo-status">{projectRepoStatus}</div>
                        ) : null}
                        {projectRepoCandidates.length > 0 ? (
                          <div className="project-repo-import-options">
                            <div className="project-repo-import-choice">
                              <label className="project-repo-import-all">
                                <input
                                  type="checkbox"
                                  checked={projectRepoImportAll}
                                  onChange={(event) => {
                                    const checked = event.target.checked;
                                    setProjectRepoImportAll(checked);
                                    if (checked || projectRepoSelectedPaths.length === 0) {
                                      setProjectRepoSelectedPaths(
                                        projectRepoCandidates.map((candidate) => candidate.path),
                                      );
                                    }
                                  }}
                                />
                                <span>
                                  Import all {projectRepoCandidates.length} relevant{" "}
                                  {projectRepoCandidates.length === 1 ? "file" : "files"}
                                </span>
                              </label>
                              <span className="project-repo-info">
                                <button
                                  type="button"
                                  className="project-repo-info-button"
                                  aria-label="Relevant file types"
                                >
                                  <MaskedAssetIcon
                                    asset="info.svg"
                                    className="project-repo-info-icon"
                                  />
                                </button>
                                <span className="project-repo-info-popover" role="tooltip">
                                  Relevant files are the files Paperbase can currently import from
                                  GitHub: Python scripts and Jupyter notebooks (.py, .ipynb).
                                </span>
                              </span>
                            </div>
                            {projectRepoImportAll ? null : (
                              <div className="project-repo-import-list">
                                {projectRepoCandidates.map((candidate) => {
                                  const selected = projectRepoSelectedPaths.includes(
                                    candidate.path,
                                  );
                                  return (
                                    <label
                                      key={candidate.path}
                                      className="project-repo-import-item"
                                    >
                                      <input
                                        type="checkbox"
                                        checked={selected}
                                        onChange={(event) => {
                                          setProjectRepoSelectedPaths((current) =>
                                            event.target.checked
                                              ? [...new Set([...current, candidate.path])]
                                              : current.filter((path) => path !== candidate.path),
                                          );
                                        }}
                                      />
                                      <span>{candidate.path}</span>
                                      <em>{projectFileKindLabel(candidate.file_kind)}</em>
                                    </label>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {projectRepoMode === "create" && projectRepoStatus ? (
                      <div className="project-repo-status">{projectRepoStatus}</div>
                    ) : null}
                    <div className="project-repo-links">
                      <button
                        type="button"
                        className="project-repo-link"
                        disabled={projectRepoListLoading}
                        onClick={() => void refreshProjectRepositories()}
                      >
                        {projectRepoListLoading ? "Refreshing..." : "Refresh"}
                      </button>
                      <a
                        className="project-repo-link"
                        href={sessionSettings.github_manage_access_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Manage Paperbase access
                      </a>
                    </div>
                    <div className="project-advanced-section">
                      <button
                        type="button"
                        className="project-advanced-toggle"
                        aria-expanded={projectAdvancedOpen}
                        onClick={() => setProjectAdvancedOpen((open) => !open)}
                      >
                        Advanced
                        <span className="project-advanced-chevron" aria-hidden="true" />
                      </button>
                      {projectAdvancedOpen ? (
                        <div className="project-advanced-panel">
                          <div
                            className="project-advanced-tabs"
                            role="tablist"
                            aria-label="Advanced project settings"
                          >
                            <button
                              type="button"
                              role="tab"
                              aria-selected={projectAdvancedTab === "inference"}
                              className={`project-advanced-tab${
                                projectAdvancedTab === "inference" ? " active" : ""
                              }`}
                              onClick={() => setProjectAdvancedTab("inference")}
                            >
                              Inference
                            </button>
                            <button
                              type="button"
                              role="tab"
                              aria-selected={projectAdvancedTab === "sandbox"}
                              className={`project-advanced-tab${
                                projectAdvancedTab === "sandbox" ? " active" : ""
                              }`}
                              onClick={() => setProjectAdvancedTab("sandbox")}
                            >
                              Sandbox
                            </button>
                          </div>
                          {projectAdvancedTab === "inference" ? (
                            <section className="project-advanced-group" role="tabpanel">
                              {sessionSummary?.hasOpenAIApiKey ? (
                                <div className="settings-secret-row">
                                  <div className="settings-secret-copy">
                                    <div className="settings-secret-label">OpenAI API key</div>
                                    <div className="settings-secret-value">
                                      {sessionSettings.openai_api_key_hint ||
                                        sessionSettings.anthropic_api_key_hint ||
                                        "Saved"}
                                    </div>
                                  </div>
                                </div>
                              ) : (
                                <div className="project-advanced-form">
                                  <label className="project-field" htmlFor="project-anthropic-key">
                                    <span>OpenAI API key</span>
                                    <input
                                      id="project-anthropic-key"
                                      name="project_anthropic_key"
                                      type="password"
                                      autoComplete="new-password"
                                      placeholder="OpenAI API key"
                                      value={projectAnthropicKey}
                                      onChange={(event) =>
                                        setProjectAnthropicKey(event.target.value)
                                      }
                                    />
                                  </label>
                                  <button
                                    type="button"
                                    className="secondary-button project-advanced-save"
                                    disabled={projectAdvancedSaving === "inference"}
                                    onClick={() => void saveProjectInferenceKey()}
                                  >
                                    {projectAdvancedSaving === "inference"
                                      ? "Saving..."
                                      : "Save key"}
                                  </button>
                                </div>
                              )}
                            </section>
                          ) : (
                            <section className="project-advanced-group" role="tabpanel">
                              {sessionSettings.has_modal_credentials ? (
                                <div className="settings-secret-row">
                                  <div className="settings-secret-copy">
                                    <div className="settings-secret-label">Modal token ID</div>
                                    <div className="settings-secret-value">
                                      {sessionSettings.modal_token_id_hint || "Saved"}
                                    </div>
                                  </div>
                                </div>
                              ) : (
                                <div className="project-advanced-form">
                                  <label className="project-field" htmlFor="project-modal-token-id">
                                    <span>Modal token ID</span>
                                    <input
                                      id="project-modal-token-id"
                                      name="project_modal_token_id"
                                      type="password"
                                      autoComplete="new-password"
                                      placeholder="Modal token ID"
                                      value={projectModalTokenId}
                                      onChange={(event) =>
                                        setProjectModalTokenId(event.target.value)
                                      }
                                    />
                                  </label>
                                  <label
                                    className="project-field"
                                    htmlFor="project-modal-token-secret"
                                  >
                                    <span>Modal token secret</span>
                                    <input
                                      id="project-modal-token-secret"
                                      name="project_modal_token_secret"
                                      type="password"
                                      autoComplete="new-password"
                                      placeholder="Modal token secret"
                                      value={projectModalTokenSecret}
                                      onChange={(event) =>
                                        setProjectModalTokenSecret(event.target.value)
                                      }
                                    />
                                  </label>
                                  <button
                                    type="button"
                                    className="secondary-button project-advanced-save"
                                    disabled={projectAdvancedSaving === "sandbox"}
                                    onClick={() => void saveProjectModalCredentials()}
                                  >
                                    {projectAdvancedSaving === "sandbox"
                                      ? "Saving..."
                                      : "Save credentials"}
                                  </button>
                                </div>
                              )}
                            </section>
                          )}
                          {projectAdvancedStatus ? (
                            <div className="project-repo-status" role="status">
                              {projectAdvancedStatus}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <div className="project-github-required">
                    <button
                      className="primary-button project-github-connect-button"
                      type="button"
                      disabled={!sessionSettings.github_oauth_available}
                      onClick={() => {
                        sessionStorage.setItem(RESUME_PROJECT_MODAL_KEY, "1");
                        window.location.href = githubLoginUrl();
                      }}
                    >
                      <AppIcon
                        src={staticAssetPath("github.svg")}
                        className="project-github-icon"
                      />
                      Connect GitHub account
                    </button>
                    <p className="project-github-required-note">
                      You need to connect GitHub before creating a project. GitHub is the source of
                      truth for project files, and every change is committed there.
                    </p>
                    {!sessionSettings.github_oauth_available ? (
                      <div className="project-repo-status">
                        GitHub OAuth is not configured in this environment yet.
                      </div>
                    ) : null}
                  </div>
                )
              ) : null}
              <button
                className="primary-button project-submit-button"
                type="submit"
                disabled={projectCreateBlocked}
              >
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
        aria-labelledby="delete-modal-title"
      >
        <div className="modal-backdrop" onClick={closeDeleteModal} />
        <div className="modal-panel">
          <button className="modal-close" type="button" onClick={closeDeleteModal}>
            ×
          </button>
          <div className="modal-heading">
            <h2 id="delete-modal-title" className="modal-title">
              {deleteModalState?.kind === "project"
                ? "Delete project"
                : deleteModalState?.kind === "python_file"
                  ? "Delete file"
                  : "Delete session"}
            </h2>
            <p className="modal-caption">
              {deleteModalState?.kind === "project"
                ? `Delete ${deleteModalState.name} and all of its chats and code files?`
                : deleteModalState?.kind === "python_file"
                  ? `Delete ${deleteModalState.title}?`
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
                {deleteModalState?.kind === "project"
                  ? "Delete project"
                  : deleteModalState?.kind === "python_file"
                    ? "Delete file"
                    : "Delete session"}
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
                minLength={8}
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
                minLength={8}
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
              <CredentialFormUsernameField />
              <input
                name="token"
                type="text"
                placeholder="Reset token"
                defaultValue={new URLSearchParams(window.location.search).get("reset_token") ?? ""}
                autoComplete="one-time-code"
                minLength={8}
                required
              />
              <input
                name="password"
                type="password"
                placeholder="New password"
                autoComplete="new-password"
                minLength={8}
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
                {settingsTabs.map((tab) => (
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
                      caption="Manage OpenAI inference API key."
                    />
                    {sessionSummary?.hasOpenAIApiKey ? (
                      <div className="settings-secret-row">
                        <div className="settings-secret-copy">
                          <div className="settings-secret-label">OpenAI API key</div>
                          <div className="settings-secret-value">
                            {sessionSettings.openai_api_key_hint ||
                              sessionSettings.anthropic_api_key_hint ||
                              "Saved"}
                          </div>
                        </div>
                        <button
                          id="delete-anthropic-key-button"
                          className="api-key-delete-button"
                          type="button"
                          aria-label="Delete saved OpenAI API key"
                          title="Delete saved OpenAI API key"
                          onClick={async () => {
                            try {
                              const nextSession = await api<BrowserSessionResponse>(
                                "/v1/browser/settings/openai-key",
                                { method: "DELETE" },
                              );
                              setSession(nextSession);
                              showNoteFlash("OpenAI API key removed.");
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
                              "/v1/browser/settings/openai-key",
                              {
                                method: "PUT",
                                body: JSON.stringify(payload),
                              },
                            );
                            setSession(nextSession);
                            showNoteFlash("OpenAI API key saved.");
                          } catch (error) {
                            showErrorFlash(
                              error instanceof Error ? error.message : "Saving API key failed",
                            );
                          }
                        }}
                      >
                        <CredentialFormUsernameField email={session?.user?.email ?? ""} />
                        <div className="settings-field">
                          <label className="settings-field-label" htmlFor="settings-openai-api-key">
                            OpenAI API key
                          </label>
                          <input
                            id="settings-openai-api-key"
                            name="api_key"
                            type="password"
                            placeholder="OpenAI API key"
                            autoComplete="new-password"
                            aria-describedby="settings-openai-api-key-note"
                            required
                          />
                          <p id="settings-openai-api-key-note" className="settings-field-note">
                            Stored on this workspace and reused for chat and research turns.
                          </p>
                        </div>
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
                        <CredentialFormUsernameField email={session?.user?.email ?? ""} />
                        <div className="settings-field">
                          <label className="settings-field-label" htmlFor="settings-modal-token-id">
                            Modal token ID
                          </label>
                          <input
                            id="settings-modal-token-id"
                            name="token_id"
                            type="password"
                            placeholder="Paste token ID"
                            autoComplete="new-password"
                            aria-describedby="settings-modal-token-id-note"
                            required
                          />
                          <p id="settings-modal-token-id-note" className="settings-field-note">
                            The identifier paired with your sandbox token secret.
                          </p>
                        </div>
                        <div className="settings-field">
                          <label
                            className="settings-field-label"
                            htmlFor="settings-modal-token-secret"
                          >
                            Modal token secret
                          </label>
                          <input
                            id="settings-modal-token-secret"
                            name="token_secret"
                            type="password"
                            placeholder="Paste token secret"
                            autoComplete="new-password"
                            aria-describedby="settings-modal-token-secret-note"
                            required
                          />
                          <p id="settings-modal-token-secret-note" className="settings-field-note">
                            Stored for this signed-in workspace and used only for sandbox sessions.
                          </p>
                        </div>
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
                          closeAuthModal();
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
