import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ChangeEvent, UIEvent } from "react";

import {
  api,
  createProject,
  createThread,
  deleteProject,
  deleteThread,
  getBrowserPaper,
  getBrowserPaperFigure,
  getBrowserPaperFile,
  getBrowserPaperTable,
  getThread,
  googleLoginUrl,
  loadProjectSessions,
  loadProjects,
  loadSession,
  loadUploads,
  logoutBrowserSession,
  postForm,
  sendThreadMessage,
  sendThreadMessageStream,
  suggestBrowserPapers,
  StreamingChatError,
  updateProject,
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
  ChatThreadResponse,
  ChatThreadSummary,
  FigureResponse,
  FileContentResponse,
  PaperResponse,
  ProjectSummary,
  TableResponse,
} from "./types";

const OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
const SIDEBAR_COLLAPSED_KEY = "paperbase-sidebar-collapsed";
const FREE_RESPONSE_LIMIT = 3;
const MAX_OPEN_TABS = 8;
const MESSAGE_LIST_BOTTOM_THRESHOLD = 40;
type ModalFlashKind = "error" | "note";
type PaperDrawerState = { paperId: string; path: string } | null;
type PaperDrawerContent =
  | { kind: "markdown"; path: string; file: FileContentResponse }
  | { kind: "figure"; path: string; figure: FigureResponse }
  | { kind: "table"; path: string; table: TableResponse };
type ProjectSessionState = {
  threads: ChatThreadSummary[];
  totalCount: number;
  hasMore: boolean;
  loaded: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string;
};
type ProjectModalState = { mode: "create" } | { mode: "edit"; projectId: string };
type DeleteModalState =
  | { kind: "project"; projectId: string; name: string }
  | { kind: "thread"; projectId: string; threadId: string; title: string }
  | null;

const AUTH_VIEW_COPY: Record<AuthView, { title: string; caption: string }> = {
  login: { title: "Log in", caption: "Use your account to continue." },
  signup: { title: "Create account", caption: "Create your account." },
  "reset-request": { title: "Forgot password", caption: "Get reset link." },
  "reset-password": { title: "Reset password", caption: "Choose a new password for your account." },
  settings: {
    title: "Settings",
    caption: "Manage inference, sandbox, version control, and notifications.",
  },
};

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

function staticAssetPath(name: string) {
  return `${import.meta.env.BASE_URL}${name.replace(/^\/+/, "")}`;
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
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string").slice(-MAX_OPEN_TABS)
      : [];
  } catch {
    return [];
  }
}

function appendOpenTab(current: string[], threadId: string) {
  if (current.includes(threadId)) {
    return current;
  }
  return [...current, threadId].slice(-MAX_OPEN_TABS);
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

function shouldShowThreadInSidebar(thread: ChatThreadSummary) {
  return thread.title.trim().length > 0 || thread.last_message_preview.trim().length > 0;
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

function upsertThreadSummary(
  current: ChatThreadSummary[],
  summary: ChatThreadSummary,
): ChatThreadSummary[] {
  return [summary, ...current.filter((item) => item.thread_id !== summary.thread_id)].sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  );
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

export default function App() {
  const [session, setSession] = useState<BrowserSessionResponse | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectSessions, setProjectSessions] = useState<Record<string, ProjectSessionState>>({});
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [expandedProjectIds, setExpandedProjectIds] = useState<string[]>([]);
  const [threadMap, setThreadMap] = useState<Record<string, ChatThreadResponse>>({});
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [projectMenuProjectId, setProjectMenuProjectId] = useState<string | null>(null);
  const [projectModalState, setProjectModalState] = useState<ProjectModalState | null>(null);
  const [projectFormName, setProjectFormName] = useState("");
  const [projectFormDescription, setProjectFormDescription] = useState("");
  const [projectFormError, setProjectFormError] = useState("");
  const [deleteModalState, setDeleteModalState] = useState<DeleteModalState>(null);
  const [projectIconKeys, setProjectIconKeys] = useState<Record<string, number>>({});
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authReady, setAuthReady] = useState(false);
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
  const [streamingThreadId, setStreamingThreadId] = useState<string | null>(null);
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
  const projectSessionsRef = useRef<Record<string, ProjectSessionState>>({});
  const threadMapRef = useRef<Record<string, ChatThreadResponse>>({});
  const tabsStorageHydratedRef = useRef(false);
  const appUploadRef = useRef<HTMLInputElement | null>(null);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const projectMenuRef = useRef<HTMLDivElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);

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
    projectSessionsRef.current = projectSessions;
  }, [projectSessions]);

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

  const authCopy = AUTH_VIEW_COPY[authView];
  const authenticated = Boolean(session?.authenticated);
  const activeThread = activeThreadId ? (threadMap[activeThreadId] ?? null) : null;
  const isChatEmpty = !activeThread || activeThread.messages.length === 0;
  const paperDrawerOpen = Boolean(paperDrawer);
  const allProjectsExpanded = useMemo(
    () =>
      projects.length > 0 &&
      projects.every((project) => expandedProjectIds.includes(project.project_id)),
    [expandedProjectIds, projects],
  );
  const openTabItems = useMemo(
    () =>
      openTabs.map((threadId) => {
        const loadedThread = threadMap[threadId];
        const title = loadedThread?.title ?? "New chat";
        return {
          threadId,
          title: displayThreadTitle(title),
          tabTitle: truncateTabTitle(title),
        };
      }),
    [openTabs, threadMap],
  );
  const hasOpenTabs = openTabItems.length > 0;
  const tabStripStyle = useMemo(
    () =>
      ({
        "--chat-tab-count": Math.max(1, openTabItems.length),
      }) as CSSProperties,
    [openTabItems.length],
  );
  const activeStreamInView = streamingThreadId !== null && streamingThreadId === activeThreadId;
  const showScrollToLatest = !isChatEmpty && !messageListAtBottom;

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
    setProjects(payload.projects);
    setSelectedProjectId((current) => {
      if (current && payload.projects.some((project) => project.project_id === current)) {
        return current;
      }
      return payload.projects[0]?.project_id ?? null;
    });
    return payload.projects;
  }, []);

  const updateProjectSessionCache = useCallback(
    (
      projectId: string,
      updater: (current: ProjectSessionState | null) => ProjectSessionState | null,
    ) => {
      setProjectSessions((current) => {
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
      setOpenTabs((current) => {
        const nextTabs = current.filter((value) => !ids.has(value));
        setActiveThreadId((currentActive) => {
          if (!currentActive || !ids.has(currentActive)) {
            return currentActive;
          }
          return nextTabs[nextTabs.length - 1] ?? null;
        });
        return nextTabs;
      });
      if (projectId) {
        updateProjectSessionCache(projectId, (current) => {
          if (!current) {
            return current;
          }
          const nextThreads = current.threads.filter((thread) => !ids.has(thread.thread_id));
          const removedCount = current.threads.length - nextThreads.length;
          return {
            ...current,
            threads: nextThreads,
            totalCount: Math.max(0, current.totalCount - removedCount),
            hasMore:
              current.hasMore ||
              nextThreads.length < Math.max(0, current.totalCount - removedCount),
          };
        });
      }
    },
    [updateProjectSessionCache],
  );

  const fetchProjectSessions = useCallback(
    async (projectId: string, append = false) => {
      const existing = projectSessionsRef.current[projectId];
      const offset = append ? (existing?.threads.length ?? 0) : 0;
      updateProjectSessionCache(projectId, (current) => ({
        threads: current?.threads ?? [],
        totalCount: current?.totalCount ?? 0,
        hasMore: current?.hasMore ?? false,
        loaded: current?.loaded ?? false,
        loading: append ? false : true,
        loadingMore: append,
        error: "",
      }));
      try {
        const page = await loadProjectSessions(projectId, 5, offset);
        updateProjectSessionCache(projectId, (current) => {
          const threads = append
            ? [
                ...(current?.threads ?? []),
                ...page.threads.filter(
                  (thread) =>
                    !(current?.threads ?? []).some((item) => item.thread_id === thread.thread_id),
                ),
              ]
            : page.threads;
          return {
            threads,
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
        updateProjectSessionCache(projectId, (current) => ({
          threads: current?.threads ?? [],
          totalCount: current?.totalCount ?? 0,
          hasMore: current?.hasMore ?? false,
          loaded: current?.loaded ?? false,
          loading: false,
          loadingMore: false,
          error: error instanceof Error ? error.message : "Sessions could not be loaded.",
        }));
        throw error;
      }
    },
    [updateProjectSessionCache],
  );

  const upsertThreadIntoProjectSessions = useCallback(
    (thread: ChatThreadResponse) => {
      updateProjectSessionCache(thread.project_id, (current) => {
        if (!current?.loaded) {
          return current;
        }
        const summary = threadSummaryFromThread(thread);
        const threads = upsertThreadSummary(current.threads, summary);
        return {
          ...current,
          threads,
          totalCount: Math.max(current.totalCount, threads.length),
          hasMore: Math.max(current.totalCount, threads.length) > threads.length || current.hasMore,
        };
      });
    },
    [updateProjectSessionCache],
  );

  const fetchAndStoreThread = useCallback(
    async (threadId: string) => {
      const thread = await getThread(threadId);
      setThreadMap((current) => ({ ...current, [thread.thread_id]: thread }));
      setSelectedProjectId(thread.project_id);
      upsertThreadIntoProjectSessions(thread);
      return thread;
    },
    [upsertThreadIntoProjectSessions],
  );

  const openThread = useCallback(
    async (threadId: string) => {
      const thread = threadMapRef.current[threadId] ?? (await fetchAndStoreThread(threadId));
      setOpenTabs((current) => appendOpenTab(current, threadId));
      setActiveThreadId(threadId);
      setSelectedProjectId(thread.project_id);
    },
    [fetchAndStoreThread],
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
      setOpenTabs((current) => appendOpenTab(current, thread.thread_id));
      setActiveThreadId(thread.thread_id);
      setSelectedProjectId(thread.project_id);
      upsertThreadIntoProjectSessions(thread);
      await refreshProjects();
      return thread;
    },
    [refreshProjects, upsertThreadIntoProjectSessions],
  );

  const closeThreadTab = useCallback((threadId: string) => {
    setOpenTabs((current) => {
      const nextTabs = current.filter((value) => value !== threadId);
      setActiveThreadId((currentActive) => {
        if (currentActive !== threadId) {
          return currentActive;
        }
        const closingIndex = current.indexOf(threadId);
        const fallbackId =
          nextTabs[Math.max(0, closingIndex - 1)] ?? nextTabs[closingIndex] ?? null;
        return fallbackId;
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

      setStreamingThreadId(threadId);
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
            if (eventName === "assistant_start") {
              assistantStarted = true;
              streamingMessageId = payload.assistant_message.message_id;
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
        );
      } catch (error) {
        if (error instanceof StreamingChatError && error.canFallback && !assistantStarted) {
          const payload = await sendThreadMessage(threadId, {
            content,
            include_uploads: includeUploads,
            include_papers: includePapers,
          });
          setThreadMap((current) => ({ ...current, [threadId]: payload.thread }));
          setSelectedProjectId(payload.thread.project_id);
          upsertThreadIntoProjectSessions(payload.thread);
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
          if (payload.state === "api_key_required") {
            showErrorFlash(
              "You have used your 3 free responses. Add an Anthropic API key to continue.",
            );
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
        setStreamingThreadId((current) => (current === threadId ? null : current));
      }

      if (gatingPayload) {
        setThreadMap((current) => ({ ...current, [threadId]: gatingPayload.thread }));
        setSelectedProjectId(gatingPayload.thread.project_id);
        upsertThreadIntoProjectSessions(gatingPayload.thread);
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
        showErrorFlash(
          "You have used your 3 free responses. Add an Anthropic API key to continue.",
        );
        setAuthView("settings");
        setAuthModalOpen(true);
        return gatingPayload;
      }

      if (streamError) {
        setComposerStatus(streamError);
        const refreshedThread = await fetchAndStoreThread(threadId);
        upsertThreadIntoProjectSessions(refreshedThread);
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
        upsertThreadIntoProjectSessions(refreshedThread);
        await refreshProjects();
        return donePayload;
      }

      setComposerStatus("");
      return null;
    },
    [
      fetchAndStoreThread,
      includePapers,
      includeUploads,
      refreshProjects,
      showErrorFlash,
      upsertThreadIntoProjectSessions,
      updateThread,
    ],
  );

  const bootstrapAuthenticatedUi = useCallback(async () => {
    await refreshUploads();
    const loadedProjects = await refreshProjects();
    const restoredTabs = restoreTabs();
    const validRestoredTabs: string[] = [];
    for (const threadId of restoredTabs) {
      try {
        await fetchAndStoreThread(threadId);
        validRestoredTabs.push(threadId);
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
      setActiveThreadId(firstOpen);
      setSelectedProjectId(
        threadMapRef.current[firstOpen]?.project_id ?? loadedProjects[0]?.project_id ?? null,
      );
    } else if (loadedProjects[0]) {
      setSelectedProjectId(loadedProjects[0].project_id);
    }
  }, [createNewThread, fetchAndStoreThread, refreshProjects, refreshUploads, sendMessage]);

  useEffect(() => {
    void (async () => {
      const params = new URLSearchParams(window.location.search);
      if (params.get("reset_token")) {
        setAuthView("reset-password");
        setAuthModalOpen(true);
      }
      const authStatus = params.get("auth");
      const authError = params.get("auth_error");
      const settingsSection = params.get("settings");
      if (authStatus === "github_connected") {
        showNoteFlash("GitHub connected.");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError === "github_not_configured") {
        showErrorFlash("GitHub OAuth is not configured yet.");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError === "github_auth_required") {
        showErrorFlash("Log in again before connecting GitHub.");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError === "github_callback_failed") {
        showErrorFlash("GitHub connection failed. Try again.");
        setAuthView("settings");
        setAuthModalOpen(true);
      } else if (authError) {
        showErrorFlash("Google login failed. Try email/password instead.");
        setAuthView("login");
        setAuthModalOpen(true);
      } else if (settingsSection === "version-control") {
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
      if (nextSession.authenticated) {
        await bootstrapAuthenticatedUi();
      }
      setAuthReady(true);
    })().catch((error: unknown) => {
      console.error(error);
      setComposerStatus("The chat app could not reach paperbase.");
      setAuthReady(true);
    });
  }, [bootstrapAuthenticatedUi, showErrorFlash, showNoteFlash]);

  const openAuthModal = useCallback(
    (view: AuthView) => {
      clearFlash();
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
    setProjectSessions({});
    setSelectedProjectId(null);
    setExpandedProjectIds([]);
    setThreadMap({});
    setOpenTabs([]);
    setActiveThreadId(null);
    setProjectMenuProjectId(null);
    setProjectModalState(null);
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
    setDeleteModalState(null);
    setLandingPrompt("");
    setComposerInput("");
    setComposerStatus("");
    setWeeklyPapersReviewEmail(false);
    setMobileSidebarOpen(false);
    setAccountMenuOpen(false);
    setStreamingThreadId(null);
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

  const handleNewChat = useCallback(async () => {
    setMobileSidebarOpen(false);
    setAccountMenuOpen(false);
    setProjectMenuProjectId(null);
    return createNewThread();
  }, [createNewThread]);

  const handleProjectSelection = useCallback(
    async (projectId: string) => {
      setSelectedProjectId(projectId);
      setProjectIconKeys((current) => ({
        ...current,
        [projectId]: (current[projectId] ?? 0) + 1,
      }));
      const expanded = expandedProjectIds.includes(projectId);
      if (expanded) {
        setExpandedProjectIds((current) => current.filter((value) => value !== projectId));
        return;
      }
      setExpandedProjectIds((current) => [...current, projectId]);
      if (
        !projectSessionsRef.current[projectId]?.loaded &&
        !projectSessionsRef.current[projectId]?.loading
      ) {
        await fetchProjectSessions(projectId);
      }
    },
    [expandedProjectIds, fetchProjectSessions],
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
        .filter((projectId) => !projectSessionsRef.current[projectId]?.loaded)
        .map((projectId) => fetchProjectSessions(projectId)),
    );
  }, [allProjectsExpanded, fetchProjectSessions, projects]);

  const openCreateProjectModal = useCallback(() => {
    setProjectModalState({ mode: "create" });
    setProjectFormName("");
    setProjectFormDescription("");
    setProjectFormError("");
    setProjectMenuProjectId(null);
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
        await fetchProjectSessions(project.project_id);
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
    fetchProjectSessions,
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
        const cachedThreadIds =
          projectSessionsRef.current[projectId]?.threads.map((thread) => thread.thread_id) ?? [];
        await deleteProject(projectId);
        removeThreadIdsLocally([...new Set([...deletedThreadIds, ...cachedThreadIds])], projectId);
        setProjectSessions((current) => {
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
      await createNewThread("", projectId);
    },
    [createNewThread],
  );

  const handleLoadMoreSessions = useCallback(
    async (projectId: string) => {
      await fetchProjectSessions(projectId, true);
    },
    [fetchProjectSessions],
  );

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
                  const sessionState = projectSessions[project.project_id];
                  return (
                    <div
                      key={project.project_id}
                      className={`project-group${expanded ? " expanded" : ""}`}
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
                            key={`${project.project_id}-${projectIconKeys[project.project_id] ?? 0}`}
                            src={staticAssetPath("project.apng.png")}
                            className="project-card-icon"
                          />
                          <strong className="project-card-title">{project.name}</strong>
                        </div>
                        <div
                          className="project-card-actions"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            className="sidebar-icon-button project-action-button"
                            aria-label={`Start chat in ${project.name}`}
                            onClick={() => void handleProjectStartChat(project.project_id)}
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
                          >
                            <button
                              type="button"
                              className="sidebar-icon-button project-action-button"
                              aria-label={`Open project menu for ${project.name}`}
                              onClick={() => {
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
                          {sessionState?.loading ? (
                            <div className="project-session-state">Loading sessions…</div>
                          ) : null}
                          {sessionState?.error ? (
                            <div className="project-session-state error">{sessionState.error}</div>
                          ) : null}
                          {sessionState?.loaded ? (
                            <>
                              {sessionState.threads.length > 0 ? (
                                <div className="thread-list project-thread-list">
                                  {sessionState.threads
                                    .filter(shouldShowThreadInSidebar)
                                    .map((thread) => {
                                      const title = displayThreadTitle(thread.title);
                                      return (
                                        <div
                                          key={thread.thread_id}
                                          role="button"
                                          tabIndex={0}
                                          className={`thread-card${activeThreadId === thread.thread_id ? " active" : ""}`}
                                          title={title}
                                          onClick={() =>
                                            void handleThreadSelection(thread.thread_id)
                                          }
                                          onKeyDown={(event) => {
                                            if (event.key === "Enter" || event.key === " ") {
                                              event.preventDefault();
                                              void handleThreadSelection(thread.thread_id);
                                            }
                                          }}
                                        >
                                          <div className="thread-card-copy">
                                            <strong className="thread-card-title">{title}</strong>
                                          </div>
                                          <span className="thread-card-age">
                                            {formatThreadAge(thread.updated_at)}
                                          </span>
                                          <button
                                            type="button"
                                            className="thread-card-delete"
                                            aria-label={`Delete ${title}`}
                                            onClick={(event) => {
                                              event.stopPropagation();
                                              openDeleteThreadModal(
                                                project.project_id,
                                                thread.thread_id,
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
                              ) : (
                                <div className="project-session-state">No sessions yet.</div>
                              )}
                              {sessionState.hasMore ? (
                                <button
                                  type="button"
                                  className="project-show-more"
                                  disabled={sessionState.loadingMore}
                                  onClick={() => void handleLoadMoreSessions(project.project_id)}
                                >
                                  {sessionState.loadingMore ? "Loading…" : "Show more"}
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
                        onClick={() => openAuthModal("settings")}
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
                        key={tab.threadId}
                        role="tab"
                        tabIndex={0}
                        aria-selected={activeThreadId === tab.threadId}
                        className={`chat-tab${activeThreadId === tab.threadId ? " active" : ""}`}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            void handleThreadSelection(tab.threadId);
                          }
                        }}
                      >
                        <button
                          type="button"
                          className="chat-tab-trigger"
                          title={tab.title}
                          onClick={() => void handleThreadSelection(tab.threadId)}
                        >
                          <span className="chat-tab-title">{tab.tabTitle}</span>
                        </button>
                        <button
                          type="button"
                          aria-label={`Close ${tab.title}`}
                          className="chat-tab-close"
                          onClick={(event) => {
                            event.stopPropagation();
                            closeThreadTab(tab.threadId);
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
              className={`chat-message-list${isChatEmpty ? " empty" : ""}`}
              onScroll={handleMessageListScroll}
            >
              {isChatEmpty ? (
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
                    <div className="chat-empty-eyebrow">Empty chat</div>
                    {activeThread ? (
                      <strong>Start the conversation</strong>
                    ) : (
                      <div className="chat-empty-project-row">
                        <strong>Start new research in</strong>
                        <select
                          id="empty-state-project-select"
                          className="chat-empty-project-select"
                          value={selectedProjectId ?? ""}
                          onChange={(event) => setSelectedProjectId(event.target.value)}
                        >
                          {projects.map((project) => (
                            <option key={project.project_id} value={project.project_id}>
                              {project.name}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                    <p>
                      {activeThread
                        ? "Ask about papers, compare ideas, or pull out patterns for the system you are building."
                        : "Choose a project, start a new chat in it, or open an existing session to begin."}
                    </p>
                  </div>
                </div>
              ) : (
                <div className="chat-message-column">
                  {activeThread.messages.map((message) => {
                    const hoverTime = formatTodayTimeLabel(message.created_at);
                    return (
                      <article
                        key={message.message_id}
                        className={`message-row ${message.role === "assistant" ? "assistant" : "user"} status-${message.status}`}
                      >
                        {message.status !== "completed" ? (
                          <div className="message-meta-row">
                            <span className="message-status-label">
                              {message.status === "streaming" ? "Streaming" : "Failed"}
                            </span>
                          </div>
                        ) : null}
                        <div
                          className={`message-bubble ${message.role === "assistant" ? "assistant" : "user"}`}
                        >
                          {message.role === "assistant" ? (
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
                        {message.role === "assistant" ? (
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
                                  {citation.source_kind === "paperbase_paper" ? "Paper" : "Upload"}{" "}
                                  · {citation.path}
                                </div>
                                <p className="citation-card-excerpt">{citation.excerpt}</p>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                  {activeStreamInView ? (
                    <div className="message-streaming-status">Working...</div>
                  ) : null}
                </div>
              )}
            </div>

            <div className="chat-composer-fade" aria-hidden="true" />
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
            {composerStatus ? <div className="composer-status">{composerStatus}</div> : null}
            <LandingMentionEditor
              attachButtonId="composer-upload-trigger"
              disabled={Boolean(streamingThreadId)}
              formId="composer-form"
              inputId="composer-input"
              onAttach={() => appUploadRef.current?.click()}
              onChange={setComposerInput}
              onSubmit={() => void handleComposerSubmit()}
              placeholder="Ask about the latest agent and AI systems research. Type @ to tag a paper."
              searchPapers={suggestBrowserPapers}
              submitButtonId="composer-send"
              submitLabel={streamingThreadId ? "Thinking..." : "Send"}
              submitLoading={Boolean(streamingThreadId)}
              submitOnEnter
              suggestionsPlacement="above"
              value={composerInput}
              variant="chat"
            />
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
          <div className="modal-heading">
            {authView === "settings" ? (
              <AppIcon src={staticAssetPath("settings.svg")} className="modal-heading-icon" />
            ) : null}
            <h2 id="auth-modal-title" className="modal-title">
              {authCopy.title}
            </h2>
            <p id="auth-modal-caption" className="modal-caption">
              {authCopy.caption}
            </p>
          </div>

          {flash ? (
            <div
              id="modal-flash"
              className={`modal-flash${flashKind === "error" ? " warning" : " note"}`}
              role={flashKind === "error" ? "alert" : "status"}
            >
              {flashKind === "error" ? (
                <span className="modal-flash-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M12 4 3.75 18h16.5L12 4Z" />
                    <path d="M12 9v4" />
                    <path d="M12 16.5h.01" />
                  </svg>
                </span>
              ) : null}
              <span className="modal-flash-text">{flash}</span>
            </div>
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
              <div className="settings-sections">
                <section className="settings-section">
                  <div className="settings-section-header">
                    <h3>Inference</h3>
                    {sessionSummary?.hasAnthropicApiKey ? (
                      <span className="settings-status-pill">Connected</span>
                    ) : null}
                  </div>
                  <p className="settings-section-copy">
                    Add the Anthropic key Paperbase should use for model inference after the free
                    responses run out.
                  </p>
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
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                          <path d="M4 7h16" />
                          <path d="M9 7V4h6v3" />
                          <path d="M8 7l1 13h6l1-13" />
                          <path d="M10.5 11v5" />
                          <path d="M13.5 11v5" />
                        </svg>
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
                      <button className="primary-button settings-submit-button" type="submit">
                        Save key
                      </button>
                    </form>
                  )}
                </section>

                <section className="settings-section">
                  <div className="settings-section-header">
                    <h3>Sandbox</h3>
                    {sessionSettings.has_daytona_api_key ? (
                      <span className="settings-status-pill">Connected</span>
                    ) : null}
                  </div>
                  <p className="settings-section-copy">
                    Add the Daytona API key Paperbase should use for sandbox execution.
                  </p>
                  {sessionSettings.has_daytona_api_key ? (
                    <div className="settings-secret-row">
                      <div className="settings-secret-copy">
                        <div className="settings-secret-label">Daytona API key</div>
                        <div className="settings-secret-value">
                          {sessionSettings.daytona_api_key_hint || "Saved"}
                        </div>
                      </div>
                      <button
                        id="delete-daytona-key-button"
                        className="api-key-delete-button"
                        type="button"
                        aria-label="Delete saved Daytona API key"
                        title="Delete saved Daytona API key"
                        onClick={async () => {
                          try {
                            const nextSession = await api<BrowserSessionResponse>(
                              "/v1/browser/settings/daytona-key",
                              { method: "DELETE" },
                            );
                            setSession(nextSession);
                            showNoteFlash("Daytona API key removed.");
                          } catch (error) {
                            showErrorFlash(
                              error instanceof Error ? error.message : "Deleting Daytona key failed",
                            );
                          }
                        }}
                      >
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                          <path d="M4 7h16" />
                          <path d="M9 7V4h6v3" />
                          <path d="M8 7l1 13h6l1-13" />
                          <path d="M10.5 11v5" />
                          <path d="M13.5 11v5" />
                        </svg>
                      </button>
                    </div>
                  ) : (
                    <form
                      id="settings-daytona-form"
                      className="settings-form"
                      onSubmit={async (event) => {
                        event.preventDefault();
                        try {
                          const payload = Object.fromEntries(
                            new FormData(event.currentTarget).entries(),
                          );
                          const nextSession = await api<BrowserSessionResponse>(
                            "/v1/browser/settings/daytona-key",
                            {
                              method: "PUT",
                              body: JSON.stringify(payload),
                            },
                          );
                          setSession(nextSession);
                          showNoteFlash("Daytona API key saved.");
                        } catch (error) {
                          showErrorFlash(
                            error instanceof Error ? error.message : "Saving Daytona key failed",
                          );
                        }
                      }}
                    >
                      <input
                        name="api_key"
                        type="password"
                        placeholder="Daytona API key"
                        autoComplete="new-password"
                        required
                      />
                      <button className="primary-button settings-submit-button" type="submit">
                        Save key
                      </button>
                    </form>
                  )}
                </section>

                <section className="settings-section">
                  <div className="settings-section-header">
                    <h3>Version control</h3>
                    {sessionSettings.has_github_connection ? (
                      <span className="settings-status-pill">Connected</span>
                    ) : null}
                  </div>
                  <p className="settings-section-copy">
                    Connect GitHub so Paperbase can create repositories, pull repositories, and
                    push commits on your behalf.
                  </p>
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
                              error instanceof Error ? error.message : "Disconnecting GitHub failed",
                            );
                          }
                        }}
                      >
                        Disconnect
                      </button>
                    </div>
                  ) : (
                    <div className="settings-actions">
                      <button
                        type="button"
                        className="secondary-button settings-connect-button"
                        disabled={!sessionSettings.github_oauth_available}
                        onClick={() => {
                          window.location.href = githubLoginUrl();
                        }}
                      >
                        Connect GitHub account
                      </button>
                      {!sessionSettings.github_oauth_available ? (
                        <p className="settings-inline-note">
                          GitHub OAuth is not configured in this environment yet.
                        </p>
                      ) : null}
                    </div>
                  )}
                </section>

                <section className="settings-section">
                  <div className="settings-section-header">
                    <h3>Notifications</h3>
                  </div>
                  <p className="settings-section-copy">
                    Choose whether you want the weekly papers review email.
                  </p>
                  <form
                    id="settings-notifications-form"
                    className="settings-form notifications-form"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      try {
                        const nextSession = await api<BrowserSessionResponse>(
                          "/v1/browser/settings/notifications",
                          {
                            method: "PUT",
                            body: JSON.stringify({
                              weekly_papers_review_email: weeklyPapersReviewEmail,
                            }),
                          },
                        );
                        setSession(nextSession);
                        showNoteFlash("Notification preferences saved.");
                      } catch (error) {
                        showErrorFlash(
                          error instanceof Error
                            ? error.message
                            : "Saving notification preferences failed",
                        );
                      }
                    }}
                  >
                    <label className="settings-checkbox-row">
                      <input
                        id="weekly-papers-review-email"
                        name="weekly_papers_review_email"
                        type="checkbox"
                        checked={weeklyPapersReviewEmail}
                        onChange={(event) => setWeeklyPapersReviewEmail(event.target.checked)}
                      />
                      <span>Weekly papers review email</span>
                    </label>
                    <button className="primary-button settings-submit-button" type="submit">
                      Save notifications
                    </button>
                  </form>
                </section>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
