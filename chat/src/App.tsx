import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import {
  api,
  createThread,
  getBrowserPaper,
  getBrowserPaperFigure,
  getBrowserPaperFile,
  getBrowserPaperTable,
  getThread,
  googleLoginUrl,
  loadSession,
  loadThreads,
  loadUploads,
  logoutBrowserSession,
  postForm,
  sendThreadMessage,
  sendThreadMessageStream,
  StreamingChatError,
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
  TableResponse,
} from "./types";

const OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
const SIDEBAR_COLLAPSED_KEY = "paperbase-sidebar-collapsed";
const FREE_RESPONSE_LIMIT = 3;
type ModalFlashKind = "error" | "note";
type PaperDrawerState = { paperId: string; path: string } | null;
type PaperDrawerContent =
  | { kind: "markdown"; path: string; file: FileContentResponse }
  | { kind: "figure"; path: string; figure: FigureResponse }
  | { kind: "table"; path: string; table: TableResponse };

const AUTH_VIEW_COPY: Record<AuthView, { title: string; caption: string }> = {
  login: { title: "Log in", caption: "Use your account to continue." },
  signup: { title: "Create account", caption: "Create your account." },
  "reset-request": { title: "Forgot password", caption: "Get reset link." },
  "reset-password": { title: "Reset password", caption: "Choose a new password for your account." },
  "api-key": { title: "Anthropic API key", caption: "Use your own key." },
};

const EXAMPLES = [
  {
    label: "Reliable multi-step agents",
    description: "See which papers actually support planning, reflection, and recovery patterns in real agent loops.",
    prompt: "What patterns are emerging for reliable multi-step agents, and which papers actually validate them?",
  },
  {
    label: "Practical agent eval stack",
    description: "Compare evaluation approaches and translate them into a lightweight stack for a product team.",
    prompt: "Compare recent approaches to agent evals and recommend a practical stack for a small product team.",
  },
  {
    label: "Retrieval, memory, and tool use",
    description: "Survey how current systems combine context retrieval, memory, and tools in production settings.",
    prompt: "Summarize recent research on retrieval, memory, and tool use for production AI systems.",
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
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : [];
  } catch {
    return [];
  }
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

function GoogleButton() {
  return (
    <button className="oauth-button" data-google-login="true" type="button" onClick={() => (window.location.href = googleLoginUrl())}>
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path fill="#4285F4" d="M17.64 9.2c0-.63-.06-1.24-.16-1.82H9v3.44h4.84a4.14 4.14 0 0 1-1.8 2.71v2.25h2.9c1.7-1.57 2.7-3.88 2.7-6.58Z" />
        <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.25c-.8.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.96v2.32A9 9 0 0 0 9 18Z" />
        <path fill="#FBBC05" d="M3.95 10.71A5.4 5.4 0 0 1 3.67 9c0-.6.1-1.19.28-1.71V4.97H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.03l2.99-2.32Z" />
        <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.43 1.33l2.57-2.57C13.46.9 11.42 0 9 0A9 9 0 0 0 .96 4.97l2.99 2.32C4.66 5.17 6.65 3.58 9 3.58Z" />
      </svg>
      <span>Continue with Google</span>
    </button>
  );
}

function AppIcon({ src, className = "" }: { src: string; className?: string }) {
  return <img src={src} alt="" aria-hidden="true" className={className ? `icon-image ${className}` : "icon-image"} />;
}

export default function App() {
  const [session, setSession] = useState<BrowserSessionResponse | null>(null);
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [threadMap, setThreadMap] = useState<Record<string, ChatThreadResponse>>({});
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [flash, setFlash] = useState("");
  const [flashKind, setFlashKind] = useState<ModalFlashKind>("note");
  const [landingPrompt, setLandingPrompt] = useState("");
  const [composerInput, setComposerInput] = useState("");
  const [composerStatus, setComposerStatus] = useState("");
  const [includeUploads, setIncludeUploads] = useState(true);
  const [includePapers, setIncludePapers] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [streamingThreadId, setStreamingThreadId] = useState<string | null>(null);
  const [paperDrawer, setPaperDrawer] = useState<PaperDrawerState>(() => drawerStateFromUrl());
  const [paperDrawerPaper, setPaperDrawerPaper] = useState<PaperResponse | null>(null);
  const [paperDrawerContent, setPaperDrawerContent] = useState<PaperDrawerContent | null>(null);
  const [paperDrawerStatus, setPaperDrawerStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [paperDrawerError, setPaperDrawerError] = useState("");

  const sessionRef = useRef<BrowserSessionResponse | null>(null);
  const threadMapRef = useRef<Record<string, ChatThreadResponse>>({});
  const openTabsRef = useRef<string[]>([]);
  const appUploadRef = useRef<HTMLInputElement | null>(null);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    threadMapRef.current = threadMap;
  }, [threadMap]);

  useEffect(() => {
    openTabsRef.current = openTabs;
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

  const authCopy = AUTH_VIEW_COPY[authView];
  const authenticated = Boolean(session?.authenticated);
  const sidebarThreads = useMemo(() => threads.filter(shouldShowThreadInSidebar), [threads]);
  const activeThread = activeThreadId ? threadMap[activeThreadId] ?? null : null;
  const paperDrawerOpen = Boolean(paperDrawer);
  const openTabItems = useMemo(
    () =>
      openTabs.map((threadId) => {
        const loadedThread = threadMap[threadId];
        const summary = threads.find((thread) => thread.thread_id === threadId);
        const title = loadedThread?.title ?? summary?.title ?? "New chat";
        return {
          threadId,
          title: displayThreadTitle(title),
          tabTitle: truncateTabTitle(title),
        };
      }),
    [openTabs, threadMap, threads],
  );
  const hasOpenTabs = openTabItems.length > 0;

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
        setPaperDrawerError(error instanceof Error ? error.message : "Paper content could not be loaded.");
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
    messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
  }, [activeThread]);

  const sessionSummary = useMemo(() => {
    if (!session?.user) {
      return null;
    }
    return {
      displayName: session.user.display_name || session.user.email,
      email: session.user.email,
      remainingFreeResponses: session.remaining_free_responses,
      hasAnthropicApiKey: session.has_anthropic_api_key,
    };
  }, [session]);

  const freeResponseSegments = useMemo(() => {
    const remaining = Math.max(0, Math.min(FREE_RESPONSE_LIMIT, sessionSummary?.remainingFreeResponses ?? FREE_RESPONSE_LIMIT));
    return Array.from({ length: FREE_RESPONSE_LIMIT }, (_, index) => ({
      key: index,
      active: index < remaining,
    }));
  }, [sessionSummary]);

  const maskedApiKey = "sk-ant-••••••••••••••";

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

  const updateThread = useCallback((threadId: string, updater: (thread: ChatThreadResponse | null) => ChatThreadResponse) => {
    setThreadMap((current) => ({
      ...current,
      [threadId]: updater(current[threadId] ?? null),
    }));
  }, []);

  const handleCitationClick = useCallback(
    (message: ChatMessageResponse, citation: ChatCitation) => {
      document.getElementById(sourceCardId(message.message_id, citation.source_id))?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
      if (citation.paper_id) {
        openPaperDrawer(citation.paper_id, citation.path);
      }
    },
    [openPaperDrawer],
  );

  const refreshThreads = useCallback(async () => {
    const payload = await loadThreads();
    setThreads(payload.threads);
    return payload.threads;
  }, []);

  const refreshUploads = useCallback(async () => {
    if (!sessionRef.current?.authenticated) {
      return;
    }
    await loadUploads();
  }, []);

  const fetchAndStoreThread = useCallback(async (threadId: string) => {
    const thread = await getThread(threadId);
    setThreadMap((current) => ({ ...current, [thread.thread_id]: thread }));
    return thread;
  }, []);

  const openThread = useCallback(
    async (threadId: string) => {
      if (!threadMapRef.current[threadId]) {
        await fetchAndStoreThread(threadId);
      }
      setOpenTabs((current) => (current.includes(threadId) ? current : [...current, threadId]));
      setActiveThreadId(threadId);
    },
    [fetchAndStoreThread],
  );

  const createNewThread = useCallback(
    async (title = "") => {
      const thread = await createThread(title);
      setThreadMap((current) => ({ ...current, [thread.thread_id]: thread }));
      setOpenTabs((current) => (current.includes(thread.thread_id) ? current : [...current, thread.thread_id]));
      setActiveThreadId(thread.thread_id);
      await refreshThreads();
      return thread;
    },
    [refreshThreads],
  );

  const closeThreadTab = useCallback((threadId: string) => {
    setOpenTabs((current) => {
      const nextTabs = current.filter((value) => value !== threadId);
      setActiveThreadId((currentActive) => {
        if (currentActive !== threadId) {
          return currentActive;
        }
        const closingIndex = current.indexOf(threadId);
        const fallbackId = nextTabs[Math.max(0, closingIndex - 1)] ?? nextTabs[closingIndex] ?? null;
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
      let assistantStarted = false;
      let donePayload: ChatStreamDoneEvent | null = null;
      let gatingPayload: SendChatMessageResponse | null = null;
      let streamError: string | null = null;
      let streamingMessageId: string | null = null;

      updateThread(threadId, (thread) => ({
        thread_id: thread?.thread_id ?? threadId,
        title: (thread?.title ?? "New chat") === "New chat" ? content.slice(0, 60) : thread?.title ?? "New chat",
        created_at: thread?.created_at ?? now,
        updated_at: now,
        messages: [...(thread?.messages ?? []), optimisticUserMessage],
      }));

      setStreamingThreadId(threadId);
      setComposerStatus("Streaming...");

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
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: new Date().toISOString(),
                messages: (thread?.messages ?? []).map((message) =>
                  message.message_id === payload.message_id
                    ? { ...message, content_markdown: `${message.content_markdown}${payload.delta}` }
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
                  title: content.slice(0, 60),
                  created_at: now,
                  updated_at: now,
                  messages: [],
                }),
                updated_at: new Date().toISOString(),
                messages: (thread?.messages ?? []).map((message) =>
                  message.message_id === payload.assistant_message.message_id ? payload.assistant_message : message,
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
          await refreshThreads();
          if (payload.state === "api_key_required") {
            showErrorFlash("You have used your 3 free responses. Add an Anthropic API key to continue.");
            setAuthView("api-key");
            setAuthModalOpen(true);
          }
          return payload;
        }

        const detail = error instanceof Error ? error.message : "Streaming request failed.";
        if (streamingMessageId) {
          updateThread(threadId, (thread) => ({
            ...(thread ?? {
              thread_id: threadId,
              title: content.slice(0, 60),
              created_at: now,
              updated_at: now,
              messages: [],
            }),
            messages: (thread?.messages ?? []).map((message) =>
              message.message_id === streamingMessageId ? { ...message, status: "failed" } : message,
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
        await refreshThreads();
        showErrorFlash("You have used your 3 free responses. Add an Anthropic API key to continue.");
        setAuthView("api-key");
        setAuthModalOpen(true);
        return gatingPayload;
      }

      if (streamError) {
        setComposerStatus(streamError);
        await fetchAndStoreThread(threadId);
        await refreshThreads();
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
        await fetchAndStoreThread(threadId);
        await refreshThreads();
        return donePayload;
      }

      setComposerStatus("");
      return null;
    },
    [fetchAndStoreThread, includePapers, includeUploads, refreshThreads, showErrorFlash, updateThread],
  );

  const bootstrapAuthenticatedUi = useCallback(async () => {
    await refreshUploads();
    const restoredTabs = restoreTabs();
    const loadedThreads = await refreshThreads();
    const availableIds = new Set(loadedThreads.map((thread) => thread.thread_id));
    const validRestoredTabs = restoredTabs.filter((threadId) => availableIds.has(threadId));
    setOpenTabs(validRestoredTabs);
    for (const threadId of validRestoredTabs) {
      if (availableIds.has(threadId) && !threadMapRef.current[threadId]) {
        await fetchAndStoreThread(threadId);
      }
    }

    const pendingPrompt = consumePendingPrompt().trim();
    if (pendingPrompt) {
      const thread = await createNewThread();
      setLandingPrompt("");
      await sendMessage(thread.thread_id, pendingPrompt);
      return;
    }

    const firstOpen = validRestoredTabs[0];
    if (firstOpen) {
      await openThread(firstOpen);
    }
  }, [createNewThread, fetchAndStoreThread, openThread, refreshThreads, refreshUploads, sendMessage]);

  useEffect(() => {
    void (async () => {
      const params = new URLSearchParams(window.location.search);
      if (params.get("reset_token")) {
        setAuthView("reset-password");
        setAuthModalOpen(true);
      }
      if (params.get("auth_error")) {
        showErrorFlash("Google login failed. Try email/password instead.");
        setAuthView("login");
        setAuthModalOpen(true);
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
  }, [bootstrapAuthenticatedUi, showErrorFlash]);

  const openAuthModal = useCallback((view: AuthView) => {
    clearFlash();
    setAuthView(view);
    setAuthModalOpen(true);
    setAccountMenuOpen(false);
  }, [clearFlash]);

  const handleAuthSuccess = useCallback(
    async (nextSession: BrowserSessionResponse) => {
      setSession(nextSession);
      closeAuthModal();
      await bootstrapAuthenticatedUi();
    },
    [bootstrapAuthenticatedUi, closeAuthModal],
  );

  const handleLandingSubmit = useCallback(
    async () => {
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
    },
    [createNewThread, landingPrompt, openAuthModal, sendMessage],
  );

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

  const handleUpload = useCallback(async (file: File) => {
    await uploadDocument(file);
    await refreshUploads();
    setComposerStatus(`Attached ${file.name}`);
  }, [refreshUploads]);

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

  const handleComposerSubmit = useCallback(
    async () => {
      if (streamingThreadId) {
        return;
      }
      const content = composerInput.trim();
      if (!content) {
        return;
      }
      const threadId = activeThreadId ?? (await createNewThread()).thread_id;
      setComposerInput("");
      await sendMessage(threadId, content);
    },
    [activeThreadId, composerInput, createNewThread, sendMessage, streamingThreadId],
  );

  const resetAuthenticatedState = useCallback(() => {
    setSession(null);
    setThreads([]);
    setThreadMap({});
    setOpenTabs([]);
    setActiveThreadId(null);
    setLandingPrompt("");
    setComposerInput("");
    setComposerStatus("");
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
    },
    [openThread],
  );

  const handleNewChat = useCallback(async () => {
    setMobileSidebarOpen(false);
    setAccountMenuOpen(false);
    return createNewThread();
  }, [createNewThread]);

  const handleReadDocs = useCallback(() => {
    window.open(new URL("/docs", window.location.origin).toString(), "_blank", "noopener,noreferrer");
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
            <a className="brandline brandline-link" href="https://mv37.org" target="_blank" rel="noreferrer">
              MV37
            </a>
            <h1 className="landing-title">Paperbase</h1>
            <p className="landing-subtitle">Research patterns for building better AI agents and systems.</p>
            <div className="landing-copy">
              <p>Paperbase helps AI researchers and agent builders keep up with fast-moving work without losing track of the original sources, so they can ask broad questions, compare ideas across papers, and pull out the design patterns that matter when building agents and AI systems.</p>
            </div>
            <LandingMentionEditor
              attachButtonId="landing-upload-trigger"
              onAttach={() => openAuthModal("login")}
              onChange={setLandingPrompt}
              onSubmit={() => void handleLandingSubmit()}
              submitButtonId="landing-start-button"
              submitLabel="Start"
              value={landingPrompt}
            />

            <section className="examples">
              <div className="section-label">Examples</div>
              <div className="example-list">
                {EXAMPLES.map((example) => (
                  <button key={example.label} type="button" className="example-pill" onClick={() => void handleExample(example.prompt)}>
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
                    src={staticAssetPath(sidebarCollapsed ? "show-sidebar.svg" : "hide-sidebar.svg")}
                    className="sidebar-toggle-icon"
                  />
                </button>
              </div>
              <div className="sidebar-quick-actions">
                <button id="new-chat-button" className="sidebar-quick-action" onClick={() => void handleNewChat()}>
                  <AppIcon src={staticAssetPath("edit.svg")} className="sidebar-quick-action-icon" />
                  <span className="sidebar-quick-action-label">New chat</span>
                </button>
                <button type="button" className="sidebar-quick-action" onClick={handleReadDocs}>
                  <AppIcon src={staticAssetPath("docs.svg")} className="sidebar-quick-action-icon" />
                  <span className="sidebar-quick-action-label">Read docs</span>
                </button>
              </div>
            </div>

            <section className="sidebar-section sidebar-card history-section">
              <div className="section-label sidebar-section-label">Previous sessions</div>
              {sidebarThreads.length > 0 ? (
                <div id="thread-list" className="thread-list">
                  {sidebarThreads.map((thread) => {
                    const title = displayThreadTitle(thread.title);
                    return (
                      <button
                        key={thread.thread_id}
                        type="button"
                        className={`thread-card${activeThreadId === thread.thread_id ? " active" : ""}`}
                        title={title}
                        onClick={() => void handleThreadSelection(thread.thread_id)}
                      >
                        <div className="thread-card-copy">
                          <strong className="thread-card-title">{title}</strong>
                        </div>
                        <span className="thread-card-age">{formatThreadAge(thread.updated_at)}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
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
                    <AppIcon src={staticAssetPath("settings.svg")} className="sidebar-settings-icon" />
                    <span>Settings</span>
                  </button>
                  {accountMenuOpen ? (
                    <div className="account-menu" role="menu" aria-label="Account actions">
                      <div className="account-menu-progress">
                        <div className="account-menu-label-row">
                          <span>Free responses</span>
                          <span>
                            {sessionSummary.remainingFreeResponses}/{FREE_RESPONSE_LIMIT} left
                          </span>
                        </div>
                        <div className="usage-meter" aria-label={`${sessionSummary.remainingFreeResponses} of ${FREE_RESPONSE_LIMIT} free responses left`}>
                          {freeResponseSegments.map((segment) => (
                            <span
                              key={segment.key}
                              className={`usage-meter-segment${segment.active ? " active" : ""}`}
                            />
                          ))}
                        </div>
                      </div>
                      <button
                        type="button"
                        id="manage-key-button"
                        className="account-menu-item"
                        role="menuitem"
                        onClick={() => openAuthModal("api-key")}
                      >
                        <AppIcon
                          src={staticAssetPath("claude.svg")}
                          className="account-menu-item-icon account-menu-item-icon-claude"
                        />
                        {sessionSummary.hasAnthropicApiKey ? "Manage API key" : "Add API key"}
                      </button>
                      <button
                        type="button"
                        id="logout-button"
                        className="account-menu-item danger"
                        role="menuitem"
                        onClick={() => void handleLogout()}
                      >
                        <AppIcon src={staticAssetPath("logout.svg")} className="account-menu-item-icon" />
                        Log out
                      </button>
                    </div>
                  ) : null}
                </div>
              </section>
            ) : null}
          </aside>

          <section className="chat-stage">
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
                      src={staticAssetPath(mobileSidebarOpen ? "hide-sidebar.svg" : "show-sidebar.svg")}
                      className="sidebar-toggle-icon"
                    />
                  </button>
                  <div className="chat-tab-strip" role="tablist" aria-label="Open tabs">
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

            <div ref={messageListRef} className="chat-message-list">
              {!activeThread || activeThread.messages.length === 0 ? (
                <div className="chat-stage-spacer">
                  {!hasOpenTabs ? (
                    <button
                      type="button"
                      className="sidebar-icon-button chat-sidebar-toggle empty-state-sidebar-toggle"
                      aria-label={mobileSidebarOpen ? "Close sidebar" : "Open sidebar"}
                      onClick={handleSidebarToggle}
                    >
                      <AppIcon
                        src={staticAssetPath(mobileSidebarOpen ? "hide-sidebar.svg" : "show-sidebar.svg")}
                        className="sidebar-toggle-icon"
                      />
                    </button>
                  ) : null}
                  <div className="chat-empty-state">
                    <div className="chat-empty-eyebrow">Empty chat</div>
                    <strong>{activeThread ? "Start the conversation" : "Open a chat tab"}</strong>
                    <p>
                      {activeThread
                        ? "Ask about papers, compare ideas, or pull out patterns for the system you are building."
                        : "Choose a previous session or start a new chat to begin."}
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
                        <div className={`message-bubble ${message.role === "assistant" ? "assistant" : "user"}`}>
                          {message.role === "assistant" ? (
                            <MarkdownRenderer
                              markdown={message.content_markdown}
                              className="message-markdown"
                              interactiveCitations
                              onCitationClick={(sourceId) => {
                                const citation = message.citations.find((item) => item.source_id === sourceId);
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
                            {hoverTime ? <span className="assistant-hover-time">{hoverTime}</span> : null}
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
                                    <span className="citation-card-source-id">{citation.source_id}</span>
                                    <strong className="citation-card-title">{citation.title}</strong>
                                  </div>
                                  {citation.paper_id ? (
                                    <button
                                      type="button"
                                      className="citation-card-action"
                                      onClick={() => openPaperDrawer(citation.paper_id!, citation.path)}
                                    >
                                      Open paper
                                    </button>
                                  ) : null}
                                </div>
                                <div className="citation-card-path">
                                  {citation.source_kind === "paperbase_paper" ? "Paper" : "Upload"} · {citation.path}
                                </div>
                                <p className="citation-card-excerpt">{citation.excerpt}</p>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="composer-area">
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
                submitButtonId="composer-send"
                submitLabel={streamingThreadId ? "Thinking..." : "Send"}
                submitOnShiftEnter
                suggestionsPlacement="above"
                value={composerInput}
                variant="chat"
              />
              <input ref={appUploadRef} id="app-upload-file" className="hidden-file-input" type="file" onChange={onUploadChange} />
            </div>
          </section>

          {paperDrawerOpen ? (
            <aside className="paper-drawer" aria-label="Cited paper drawer">
              <div className="paper-drawer-header">
                <div>
                  <div className="paper-drawer-eyebrow">Cited paper</div>
                  <strong className="paper-drawer-title">{paperDrawerPaper?.title ?? "Loading paper"}</strong>
                  {paperDrawer ? <div className="paper-drawer-path">{paperDrawer.path}</div> : null}
                </div>
                <button type="button" className="paper-drawer-close" aria-label="Close paper drawer" onClick={closePaperDrawer}>
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
                {paperDrawerStatus === "loading" ? <div className="paper-drawer-placeholder">Loading cited paper…</div> : null}
                {paperDrawerStatus === "error" ? <div className="paper-drawer-error">{paperDrawerError}</div> : null}
                {paperDrawerStatus === "ready" && paperDrawerContent?.kind === "markdown" ? (
                  <MarkdownRenderer markdown={paperDrawerContent.file.content} className="paper-drawer-markdown" />
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
                    <MarkdownRenderer markdown={paperDrawerContent.figure.caption} className="paper-drawer-markdown" />
                  </div>
                ) : null}
                {paperDrawerStatus === "ready" && paperDrawerContent?.kind === "table" ? (
                  <div className="paper-drawer-table-wrap">
                    {paperDrawerContent.table.caption ? (
                      <MarkdownRenderer markdown={paperDrawerContent.table.caption} className="paper-drawer-markdown" />
                    ) : null}
                    <div className="paper-drawer-table-scroll">
                      <table className="paper-drawer-table">
                        <tbody>
                          {paperDrawerContent.table.rows.map((row, rowIndex) => (
                            <tr key={`${paperDrawerContent.table.table_id}-${rowIndex}`}>
                              {row.map((cell, cellIndex) => (
                                <td key={`${paperDrawerContent.table.table_id}-${rowIndex}-${cellIndex}`}>{cell}</td>
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

      <div id="auth-modal" className={`modal${authModalOpen ? "" : " hidden"}`} role="dialog" aria-modal="true">
        <div className="modal-backdrop" data-close-modal="true" onClick={closeAuthModal} />
        <div className="modal-panel">
          <button className="modal-close" type="button" data-close-modal="true" onClick={closeAuthModal}>
            ×
          </button>
          <div className="modal-heading">
            {authView === "api-key" ? (
              <AppIcon src={staticAssetPath("claude.svg")} className="modal-heading-icon modal-heading-icon-claude" />
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

          <div className={`modal-view${authView === "login" ? " visible" : ""}`} data-auth-view="login">
            <form
              id="login-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  const nextSession = (await postForm("/v1/browser/auth/login", event.currentTarget)) as BrowserSessionResponse;
                  await handleAuthSuccess(nextSession);
                } catch (error) {
                  showErrorFlash(error instanceof Error ? error.message : "Login failed");
                }
              }}
            >
              <input name="email" type="email" placeholder="Email" required />
              <input name="password" type="password" placeholder="Password" required />
              <button className="primary-button" type="submit">
                Log in
              </button>
            </form>
            <GoogleButton />
            <div className="auth-links">
              <button type="button" className="auth-link" onClick={() => setAuthView("signup")}>
                Create a new account
              </button>
              <button type="button" className="auth-link" onClick={() => setAuthView("reset-request")}>
                Forgot password
              </button>
            </div>
          </div>

          <div className={`modal-view${authView === "signup" ? " visible" : ""}`} data-auth-view="signup">
            <form
              id="signup-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  const nextSession = (await postForm("/v1/browser/auth/signup", event.currentTarget)) as BrowserSessionResponse;
                  await handleAuthSuccess(nextSession);
                } catch (error) {
                  showErrorFlash(error instanceof Error ? error.message : "Sign up failed");
                }
              }}
            >
              <input name="display_name" type="text" placeholder="Name" />
              <input name="email" type="email" placeholder="Email" required />
              <input name="password" type="password" placeholder="Password" required />
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

          <div className={`modal-view${authView === "reset-request" ? " visible" : ""}`} data-auth-view="reset-request">
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
              <input name="email" type="email" placeholder="Email" required />
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

          <div className={`modal-view${authView === "reset-password" ? " visible" : ""}`} data-auth-view="reset-password">
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
              <input name="token" type="text" placeholder="Reset token" defaultValue={new URLSearchParams(window.location.search).get("reset_token") ?? ""} required />
              <input name="password" type="password" placeholder="New password" required />
              <button className="primary-button" type="submit">
                Reset password
              </button>
            </form>
          </div>

          <div className={`modal-view${authView === "api-key" ? " visible" : ""}`} data-auth-view="api-key">
            <div className="api-key-panel">
              <div className="api-key-meter">
                <div className="api-key-meter-header">
                  <span>Free responses</span>
                  <span>
                    {sessionSummary?.remainingFreeResponses ?? FREE_RESPONSE_LIMIT}/{FREE_RESPONSE_LIMIT} left
                  </span>
                </div>
                <div
                  className="usage-meter api-key-usage-meter"
                  aria-label={`${sessionSummary?.remainingFreeResponses ?? FREE_RESPONSE_LIMIT} of ${FREE_RESPONSE_LIMIT} free responses left`}
                >
                  {freeResponseSegments.map((segment) => (
                    <span
                      key={segment.key}
                      className={`usage-meter-segment${segment.active ? " active" : ""}`}
                    />
                  ))}
                </div>
              </div>

              {sessionSummary?.hasAnthropicApiKey ? (
                <div className="api-key-saved-row">
                  <div className="api-key-saved-copy">
                    <div className="api-key-saved-label">Saved key</div>
                    <div className="api-key-saved-value">{maskedApiKey}</div>
                  </div>
                  <button
                    id="delete-api-key-button"
                    className="api-key-delete-button"
                    type="button"
                    aria-label="Delete saved key"
                    title="Delete saved key"
                    onClick={async () => {
                      try {
                        const nextSession = await api<BrowserSessionResponse>("/v1/browser/settings/anthropic-key", { method: "DELETE" });
                        setSession(nextSession);
                        showNoteFlash("Anthropic API key removed.");
                      } catch (error) {
                        showErrorFlash(error instanceof Error ? error.message : "Deleting API key failed");
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
                  id="api-key-form"
                  className="api-key-form"
                  onSubmit={async (event) => {
                    event.preventDefault();
                    try {
                      const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
                      const nextSession = await api<BrowserSessionResponse>("/v1/browser/settings/anthropic-key", {
                        method: "PUT",
                        body: JSON.stringify(payload),
                      });
                      setSession(nextSession);
                      showNoteFlash("Anthropic API key saved.");
                    } catch (error) {
                      showErrorFlash(error instanceof Error ? error.message : "Saving API key failed");
                    }
                  }}
                >
                  <input name="api_key" type="password" placeholder="Anthropic API key" required />
                  <button className="primary-button api-key-save-button" type="submit">
                    Save key
                  </button>
                </form>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
