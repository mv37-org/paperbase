import { ApiError } from "../api";
import type { TrendingBrowseState } from "../TrendingPapersPage";
import type {
  ChatCitation,
  ChatMessageResponse,
  ChatStreamUsageEvent,
  ChatThreadItemResponse,
  ChatThreadResponse,
  ChatThreadSummary,
  MentionTag,
  PaperResponse,
  ProjectFileKind,
  ProjectItemKind,
  ProjectItemSummary,
  ProjectRepoConflictDetail,
  PythonFileResponse,
} from "../types";
import type { OpenTab, PaperDrawerState, PendingPromptState, ToolActivityEntry } from "./appTypes";

export const ACTIVE_TAB_KEY = "paperbase-active-tab";
export const LEGACY_OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
export const SIDEBAR_COLLAPSED_KEY = "paperbase-sidebar-collapsed";
export const SELECTED_PROJECT_KEY = "paperbase-selected-project";
export const DISMISSED_CHAT_SETUP_KEY = "paperbase-dismissed-chat-setup";
const MESSAGE_LIST_BOTTOM_THRESHOLD = 40;
const PUBLIC_ASSET_CACHE_KEY = `pb-${Date.now().toString(36)}`;

const CHAT_TITLE_STOP_WORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "but",
  "by",
  "can",
  "could",
  "for",
  "from",
  "help",
  "i",
  "if",
  "in",
  "into",
  "is",
  "it",
  "me",
  "my",
  "of",
  "on",
  "or",
  "our",
  "please",
  "show",
  "tell",
  "that",
  "the",
  "this",
  "to",
  "us",
  "we",
  "with",
  "would",
]);

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

const PROJECT_CODE_ACTION_RE =
  /\b(write|create|make|add|implement|edit|modify|update|refactor|fix|rename|delete|remove|read|show|open|grep|search|run)\b/i;
const PROJECT_CODE_TARGET_RE =
  /\b(code|python|pytorch|torch|script|module|file|files|function|class|training|train|evaluation|eval|workflow|repo|project)\b/i;
const PROJECT_CODE_DIRECT_TOOL_RE =
  /\b(project_(?:list_files|read_file|grep|touch|edit|delete|run))\b/i;
const PROJECT_CODE_PATH_HINT_RE = /(?:^|[\s`'"])([A-Za-z0-9_./-]+\.py)(?:$|[\s`'",.:;!?])/i;
const TOOL_ACTIVITY_TARGET_RE = /"([^"]+)"/;

export function staticAssetPath(name: string) {
  const path = `${import.meta.env.BASE_URL}${name.replace(/^\/+/, "")}`;
  return `${path}${path.includes("?") ? "&" : "?"}v=${PUBLIC_ASSET_CACHE_KEY}`;
}

export function pythonRepoPathForFile(file: Pick<PythonFileResponse, "filename" | "repo_path">) {
  return file.repo_path || `src/${file.filename}`;
}

export function pythonFilenameFromRepoPath(path: string) {
  return path.startsWith("src/") ? path.slice("src/".length) : null;
}

export function projectRepoConflictFromError(error: unknown): ProjectRepoConflictDetail | null {
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

export function createLocalMessage(
  role: string,
  content_markdown: string,
  options: Partial<Pick<ChatMessageResponse, "citations" | "status" | "message_id" | "phase">> = {},
): ChatMessageResponse {
  return {
    message_id: options.message_id ?? `local-${role}-${Math.random().toString(36).slice(2)}`,
    role,
    content_markdown,
    citations: options.citations ?? [],
    created_at: new Date().toISOString(),
    status: options.status ?? "completed",
    phase: options.phase ?? null,
  };
}

export function messageFromThreadItem(item: ChatThreadItemResponse): ChatMessageResponse | null {
  if (!["user_message", "assistant_message", "compaction"].includes(item.item_type)) {
    return null;
  }
  const message: ChatMessageResponse = {
    message_id: item.item_id,
    role: item.role,
    content_markdown: item.content_text,
    citations: item.citations,
    created_at: item.created_at,
    status: item.status,
    kind: item.item_type === "compaction" ? "compaction" : "default",
    phase: item.phase ?? null,
  };
  if (item.recommended_prompts?.length) {
    message.recommended_prompts = item.recommended_prompts;
  }
  return message;
}

export function normalizeThreadForState(thread: ChatThreadResponse): ChatThreadResponse {
  const items = [...(thread.items ?? [])];
  if (items.length === 0) {
    return thread;
  }
  return {
    ...thread,
    items,
    messages: items
      .map(messageFromThreadItem)
      .filter((message): message is ChatMessageResponse => Boolean(message)),
  };
}

export function drawerStateFromUrl(): PaperDrawerState {
  const params = new URLSearchParams(window.location.search);
  const paperId = params.get("paper");
  const path = params.get("paperPath");
  if (!paperId || !path) {
    return null;
  }
  return { paperId, path };
}

export function localDateString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function trendingBrowseStateFromUrl(): TrendingBrowseState | null {
  const params = new URLSearchParams(window.location.search);
  if (params.get("browse") !== "trending") {
    return null;
  }
  const date = params.get("trendingDate");
  const page = Number(params.get("trendingPage") ?? "1");
  return {
    date: date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : localDateString(),
    sort: params.get("trendingSort") === "upvotes" ? "upvotes" : "default",
    page: Number.isInteger(page) && page > 0 ? page : 1,
    paperId: params.get("trendingPaper"),
  };
}

export function updateTrendingBrowseUrl(nextState: TrendingBrowseState | null) {
  const params = new URLSearchParams(window.location.search);
  if (nextState) {
    params.set("browse", "trending");
    params.set("trendingDate", nextState.date);
    params.set("trendingSort", nextState.sort);
    if (nextState.page > 1) {
      params.set("trendingPage", String(nextState.page));
    } else {
      params.delete("trendingPage");
    }
    if (nextState.paperId) {
      params.set("trendingPaper", nextState.paperId);
    } else {
      params.delete("trendingPaper");
    }
  } else {
    params.delete("browse");
    params.delete("trendingDate");
    params.delete("trendingSort");
    params.delete("trendingPage");
    params.delete("trendingPaper");
  }
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.pushState({}, "", nextUrl);
}

export function updateDrawerUrl(nextState: PaperDrawerState) {
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

export function inferDrawerContentKind(path: string) {
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

export function sourceCardId(messageId: string, sourceId: string) {
  return `source-card-${messageId}-${sourceId}`;
}

function normalizePendingPromptMentions(value: unknown): MentionTag[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const mentions: MentionTag[] = [];
  const seen = new Set<string>();
  for (const entry of value) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const id = typeof entry.id === "string" ? entry.id : "";
    if (!id || seen.has(id)) {
      continue;
    }
    seen.add(id);
    mentions.push({
      id,
      title: typeof entry.title === "string" ? entry.title : "",
    });
  }
  return mentions;
}

export function mentionedPaperIdsInText(text: string, mentions: MentionTag[]) {
  const paperIds: string[] = [];
  const seen = new Set<string>();
  for (const mention of mentions) {
    if (!mention.id || seen.has(mention.id)) {
      continue;
    }
    if (mention.title.trim() && !text.includes(`@${mention.title}`)) {
      continue;
    }
    seen.add(mention.id);
    paperIds.push(mention.id);
  }
  return paperIds;
}

export function consumePendingPrompt(): PendingPromptState {
  const raw = sessionStorage.getItem(PENDING_PROMPT_KEY);
  sessionStorage.removeItem(PENDING_PROMPT_KEY);
  if (!raw) {
    return { prompt: "", paperIds: [], mentions: [] };
  }
  if (raw.startsWith("{")) {
    try {
      const parsed = JSON.parse(raw);
      const prompt = typeof parsed?.prompt === "string" ? parsed.prompt : "";
      const legacyPaperIds = Array.isArray(parsed?.paperIds)
        ? parsed.paperIds.filter((id: unknown): id is string => typeof id === "string")
        : [];
      const mentions = normalizePendingPromptMentions(parsed?.mentions);
      const paperIds = [...new Set([...legacyPaperIds, ...mentions.map((mention) => mention.id)])];
      return {
        prompt,
        paperIds,
        mentions: mentions.length ? mentions : paperIds.map((id) => ({ id, title: "" })),
      };
    } catch {
      return { prompt: "", paperIds: [], mentions: [] };
    }
  }
  return { prompt: raw, paperIds: [], mentions: [] };
}

export function rememberPendingPrompt(prompt: string, mentions: MentionTag[] = []) {
  const trimmed = prompt.trim();
  if (!trimmed) {
    return;
  }
  const normalizedMentions = normalizePendingPromptMentions(mentions);
  sessionStorage.setItem(
    PENDING_PROMPT_KEY,
    JSON.stringify({
      prompt: trimmed,
      paperIds: normalizedMentions.map((mention) => mention.id),
      mentions: normalizedMentions,
    }),
  );
}

function normalizeStoredTab(value: unknown): OpenTab | null {
  if (typeof value === "string") {
    return { kind: "thread", id: value };
  }
  if (
    value &&
    typeof value === "object" &&
    "kind" in value &&
    "id" in value &&
    typeof value.id === "string" &&
    (value.kind === "thread" ||
      value.kind === "python_file" ||
      value.kind === "project_home" ||
      value.kind === "paper_exploration")
  ) {
    return { kind: value.kind, id: value.id };
  }
  return null;
}

export function clearStoredActiveTab() {
  localStorage.removeItem(ACTIVE_TAB_KEY);
  localStorage.removeItem(LEGACY_OPEN_TABS_KEY);
}

export function restoreActiveTab() {
  try {
    const activeValue = localStorage.getItem(ACTIVE_TAB_KEY);
    if (activeValue) {
      return normalizeStoredTab(JSON.parse(activeValue));
    }
    const legacyValue = localStorage.getItem(LEGACY_OPEN_TABS_KEY);
    if (!legacyValue) {
      return null;
    }
    const parsedLegacy = JSON.parse(legacyValue);
    if (Array.isArray(parsedLegacy)) {
      for (let index = parsedLegacy.length - 1; index >= 0; index -= 1) {
        const tab = normalizeStoredTab(parsedLegacy[index]);
        if (tab) {
          return tab;
        }
      }
      return null;
    }
    return normalizeStoredTab(parsedLegacy);
  } catch {
    return null;
  }
}

export function isMessageListNearBottom(
  element: HTMLDivElement,
  threshold = MESSAGE_LIST_BOTTOM_THRESHOLD,
) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= threshold;
}

export function restoreSidebarCollapsed() {
  return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
}

export function restoreSelectedProjectId() {
  const value = localStorage.getItem(SELECTED_PROJECT_KEY)?.trim();
  return value ? value : null;
}

export function restoreDismissedChatSetupSignatures() {
  try {
    const parsed = JSON.parse(localStorage.getItem(DISMISSED_CHAT_SETUP_KEY) ?? "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([key, value]) => typeof key === "string" && typeof value === "string",
      ),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

function formatChatTitleWord(word: string) {
  if (!word) {
    return word;
  }
  if (word === word.toUpperCase()) {
    return word;
  }
  if (/[A-Z]/.test(word.slice(1))) {
    return word;
  }
  return `${word[0]?.toUpperCase() ?? ""}${word.slice(1).toLowerCase()}`;
}

export function summarizeThreadTitle(content: string) {
  const tokens = content.match(/[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*/g) ?? [];
  if (tokens.length === 0) {
    return "New chat";
  }
  const informativeTokens = tokens.filter(
    (token) => !CHAT_TITLE_STOP_WORDS.has(token.toLowerCase()),
  );
  const sourceTokens = informativeTokens.length >= 3 ? informativeTokens : tokens;
  const desiredCount = Math.min(5, Math.max(3, sourceTokens.length));
  let selectedTokens = sourceTokens.slice(0, desiredCount);
  if (selectedTokens.length < 3 && tokens.length >= 3) {
    selectedTokens = tokens.slice(0, 3);
  }
  return (
    selectedTokens
      .map((token) => formatChatTitleWord(token))
      .join(" ")
      .trim() || "New chat"
  );
}

export function displayThreadTitle(title: string) {
  return title.trim() || "New chat";
}

export function pickThinkingWord(previous?: string | null) {
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

export function formatThinkingLabel(word?: string | null) {
  const trimmed = word?.trim();
  const label = trimmed ? trimmed.replace(/\.+$/, "") : "Thinking";
  return `${label}...`;
}

export function formatTokenCount(value: number) {
  return new Intl.NumberFormat("en-US").format(Math.max(0, Math.round(value)));
}

export function formatCompactTokenCount(value: number) {
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

export function contextUsagePercent(usage: ChatStreamUsageEvent) {
  if (usage.context_window_tokens <= 0) {
    return 0;
  }
  return Math.min(100, (usage.total_tokens / usage.context_window_tokens) * 100);
}

export function formatContextUsagePercentValue(usage: ChatStreamUsageEvent) {
  const usagePercent = contextUsagePercent(usage);
  if (usagePercent > 0 && usagePercent < 1) {
    return "<1";
  }
  return String(Math.round(usagePercent));
}

export function formatContextUsageRemainingPercentValue(usage: ChatStreamUsageEvent) {
  const usagePercent = contextUsagePercent(usage);
  const roundedUsed = Math.round(usagePercent);
  const remaining = Math.max(0, 100 - roundedUsed);
  if (remaining === 0 && usagePercent < 100) {
    return "<1";
  }
  return String(remaining);
}

export function formatContextUsageSummary(usage: ChatStreamUsageEvent) {
  return `${formatContextUsagePercentValue(usage)}% used (${formatContextUsageRemainingPercentValue(usage)}% left)`;
}

export function formatContextUsageTokenSummary(usage: ChatStreamUsageEvent) {
  return `${formatCompactTokenCount(usage.total_tokens)} / ${formatCompactTokenCount(usage.context_window_tokens)} tokens used`;
}

export function contextUsageButtonLabel(usage: ChatStreamUsageEvent) {
  return `Context window: ${formatContextUsageSummary(usage)}, ${formatTokenCount(usage.total_tokens)} of ${formatTokenCount(usage.context_window_tokens)} tokens used`;
}

export function apiKeyStateMessage(state: string) {
  if (state === "api_key_invalid") {
    return "Your saved OpenAI API key could not be decrypted. Add it again in Settings.";
  }
  return "Add an OpenAI API key in Settings before starting a chat.";
}

export function shouldShowThreadInSidebar(thread: ChatThreadSummary) {
  return thread.title.trim().length > 0 || thread.last_message_preview.trim().length > 0;
}

export function isEmptyThread(thread: ChatThreadResponse | null | undefined) {
  return Boolean(thread) && thread.messages.length === 0;
}

export function formatThreadAge(value: string) {
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

export function threadSummaryFromThread(thread: ChatThreadResponse): ChatThreadSummary {
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

export function projectItemFromThread(thread: ChatThreadResponse): ProjectItemSummary {
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
    file_kind: "python",
    repo_path: "",
  };
}

export function projectItemFromPythonFile(file: PythonFileResponse): ProjectItemSummary {
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
    file_kind: file.file_kind ?? "python",
    repo_path: file.repo_path ?? `src/${file.filename}`,
  };
}

export function upsertProjectItem(
  current: ProjectItemSummary[],
  item: ProjectItemSummary,
): ProjectItemSummary[] {
  return [item, ...current.filter((entry) => entry.item_id !== item.item_id)].sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  );
}

export function mergeProjectItems(
  current: ProjectItemSummary[],
  incoming: ProjectItemSummary[],
): ProjectItemSummary[] {
  const itemsById = new Map(current.map((item) => [item.item_id, item]));
  for (const item of incoming) {
    itemsById.set(item.item_id, item);
  }
  return [...itemsById.values()].sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  );
}

export function projectItemKindCount(items: ProjectItemSummary[], itemKind: ProjectItemKind) {
  return items.filter((item) => item.item_kind === itemKind).length;
}

export function displayPythonFilename(filename: string) {
  return filename.trim() || "something.py";
}

export function pythonFilenameKey(filename: string) {
  return displayPythonFilename(filename);
}

export function duplicatePythonFilenameKeys(
  files: Array<
    Pick<ProjectItemSummary, "filename" | "title"> | Pick<PythonFileResponse, "filename">
  >,
) {
  const counts = new Map<string, number>();
  for (const file of files) {
    const key = pythonFilenameKey("filename" in file ? file.filename : file.title);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([key]) => key));
}

export function displayPythonFileTitle(
  file: Pick<PythonFileResponse, "filename" | "repo_path">,
  _duplicateKeys: Set<string>,
) {
  return displayPythonFilename(file.filename);
}

export function displayPythonFilePathContext(
  file: Pick<PythonFileResponse, "filename" | "repo_path">,
  duplicateKeys: Set<string>,
) {
  const filename = displayPythonFilename(file.filename);
  return duplicateKeys.has(filename) ? pythonRepoPathForFile(file) : "";
}

export function messageHasProjectCodeIntent(content: string) {
  if (!content.trim()) {
    return false;
  }
  if (PROJECT_CODE_DIRECT_TOOL_RE.test(content)) {
    return true;
  }
  if (PROJECT_CODE_PATH_HINT_RE.test(content) && /(read|edit|run)/i.test(content)) {
    return true;
  }
  return PROJECT_CODE_ACTION_RE.test(content) && PROJECT_CODE_TARGET_RE.test(content);
}

export function isLocalDebugHost() {
  try {
    const { hostname } = new URL(window.location.href);
    return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
  } catch {
    return false;
  }
}

export function latestThreadMessageId(
  thread: ChatThreadResponse,
  predicate: (message: ChatMessageResponse) => boolean,
) {
  for (let index = thread.messages.length - 1; index >= 0; index -= 1) {
    const message = thread.messages[index];
    if (message && predicate(message)) {
      return message.message_id;
    }
  }
  return null;
}

export function summarizeCitationPreview(paper: PaperResponse | null) {
  if (!paper) {
    return "";
  }
  const source = paper.summary_short || paper.summary_eli5 || paper.abstract || "";
  const trimmed = source.trim();
  if (trimmed.length <= 220) {
    return trimmed;
  }
  return `${trimmed.slice(0, 217).trimEnd()}...`;
}

export function buildThreadDebugBundle(thread: ChatThreadResponse, userId: string | null) {
  const latestMessage = thread.messages[thread.messages.length - 1] ?? null;
  return {
    user_id: userId,
    project_id: thread.project_id,
    project_name: thread.project_name,
    thread_id: thread.thread_id,
    thread_title: thread.title,
    message_count: thread.messages.length,
    latest_message_id: latestMessage?.message_id ?? null,
    latest_user_message_id: latestThreadMessageId(thread, (message) => message.role === "user"),
    latest_assistant_message_id: latestThreadMessageId(
      thread,
      (message) => message.role === "assistant" && message.kind !== "compaction",
    ),
    latest_compaction_message_id: latestThreadMessageId(
      thread,
      (message) => message.kind === "compaction",
    ),
  };
}

export function uniqueCitationsForThread(thread: ChatThreadResponse | null) {
  if (!thread) {
    return [];
  }
  const seen = new Set<string>();
  const citations: ChatCitation[] = [];
  for (const message of thread.messages) {
    for (const citation of message.citations) {
      const key = citation.paper_id || `${citation.source_kind}:${citation.title}:${citation.path}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      citations.push(citation);
    }
  }
  return citations;
}

export function uniquePaperCitationCount(citations: ChatCitation[]) {
  return new Set(
    citations
      .map((citation) => citation.paper_id || citation.title)
      .filter((value) => value.trim().length > 0),
  ).size;
}

export function threadMarkdownExport(thread: ChatThreadResponse) {
  const parts = [`# ${displayThreadTitle(thread.title)}`, ""];
  for (const message of thread.messages) {
    if (message.kind === "compaction") {
      continue;
    }
    parts.push(`## ${message.role === "user" ? "User" : "Assistant"}`);
    parts.push("");
    parts.push(message.content_markdown.trim());
    if (message.citations.length > 0) {
      parts.push("", "Sources:");
      for (const citation of message.citations) {
        parts.push(`- [${citation.source_id}] ${citation.title} (${citation.path})`);
      }
    }
    parts.push("");
  }
  return parts.join("\n");
}

export function downloadTextFile(filename: string, text: string, type: string) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function quotedToolActivityTarget(label: string) {
  return label.match(TOOL_ACTIVITY_TARGET_RE)?.[1] ?? "";
}

export function toolActivityInputForItem(
  item: Pick<ChatThreadItemResponse, "content_json" | "content_text">,
) {
  if (
    item.content_json &&
    typeof item.content_json === "object" &&
    !Array.isArray(item.content_json)
  ) {
    return item.content_json as Record<string, unknown>;
  }
  if (item.content_text.trim()) {
    try {
      const parsed = JSON.parse(item.content_text);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return null;
    }
  }
  return null;
}

export function toolActivityLabelForItem(
  item: Pick<ChatThreadItemResponse, "name" | "content_json" | "content_text">,
) {
  const toolName = item.name ?? "";
  const toolInput = toolActivityInputForItem(item);
  const detail = (value: unknown, limit = 56) => {
    const text = String(value ?? "").trim();
    if (!text) {
      return null;
    }
    if (text.length > limit) {
      return `${text.slice(0, limit - 1).trimEnd()}…`;
    }
    return text;
  };

  const query = detail(toolInput?.query);
  const path = detail(toolInput?.path) ?? detail(toolInput?.repo_path);
  const newPath = detail(toolInput?.new_repo_path);
  const pattern = detail(toolInput?.pattern);
  const figureId = detail(toolInput?.figure_id);
  const tableId = detail(toolInput?.table_id);
  const execId = detail(toolInput?.exec_id);

  if (toolName === "paperbase_search") {
    return query ? `paper search for "${query}"` : "paper search";
  }
  if (toolName === "paperbase_search_sections") {
    return query ? `section search for "${query}"` : "section search";
  }
  if (toolName === "paperbase_get_results") {
    return "result lookup";
  }
  if (toolName === "paperbase_grep_results") {
    return pattern ? `result filtering for "${pattern}"` : "result filtering";
  }
  if (toolName === "paperbase_get_paper") {
    return "paper lookup";
  }
  if (toolName === "paperbase_list_files") {
    return "file listing";
  }
  if (toolName === "paperbase_read_file") {
    return path ? `file read for "${path}"` : "file read";
  }
  if (toolName === "paperbase_get_figure") {
    return figureId ? `figure lookup for "${figureId}"` : "figure lookup";
  }
  if (toolName === "paperbase_get_table") {
    return tableId ? `table lookup for "${tableId}"` : "table lookup";
  }
  if (toolName === "paperbase_get_related_papers") {
    return "related-paper lookup";
  }
  if (toolName === "paperbase_get_references") {
    return "reference lookup";
  }
  if (toolName === "paperbase_list_uploaded_documents") {
    return "upload listing";
  }
  if (toolName === "paperbase_search_uploaded_sections") {
    return query ? `uploaded-note search for "${query}"` : "uploaded-note search";
  }
  if (toolName === "project_list_files") {
    return "project file listing";
  }
  if (toolName === "project_read_file") {
    return path ? `project file read for "${path}"` : "project file read";
  }
  if (toolName === "project_grep") {
    return pattern ? `project grep for "${pattern}"` : "project grep";
  }
  if (toolName === "project_write_file" || toolName === "project_touch") {
    return path ? `project file write for "${path}"` : "project file write";
  }
  if (toolName === "project_apply_patch" || toolName === "project_edit") {
    return path ? `project file patch for "${path}"` : "project file patch";
  }
  if (toolName === "project_move") {
    if (path && newPath) {
      return `project file move from "${path}" to "${newPath}"`;
    }
    return "project file move";
  }
  if (toolName === "project_delete") {
    return path ? `project file delete for "${path}"` : "project file delete";
  }
  if (toolName === "project_git_status") {
    return "project git status";
  }
  if (toolName === "project_git_diff") {
    return "project git diff";
  }
  if (toolName === "project_exec") {
    return "project exec";
  }
  if (toolName === "project_wait") {
    return execId ? `project exec wait for "${execId}"` : "project exec wait";
  }
  if (toolName === "project_cancel") {
    return execId ? `project exec cancel for "${execId}"` : "project exec cancel";
  }
  if (toolName === "project_logs") {
    return execId ? `project exec logs for "${execId}"` : "project exec logs";
  }
  if (toolName === "project_test") {
    return "project test";
  }
  if (toolName === "project_lint") {
    return "project lint";
  }
  if (toolName === "project_run") {
    return path ? `project run for "${path}"` : "project run";
  }
  return toolName || "tool call";
}

export function toolActivityCommandForItem(
  item: Pick<ChatThreadItemResponse, "name" | "content_json" | "content_text">,
) {
  const toolName = item.name ?? "";
  const toolInput = toolActivityInputForItem(item);
  if (!toolName) {
    return item.content_text || null;
  }
  if (!toolInput) {
    return item.content_text || null;
  }
  return `${toolName}(${JSON.stringify(toolInput, null, 2)})`;
}

export function formatToolActivityText(
  activity: Pick<ToolActivityEntry, "label" | "status" | "toolName">,
) {
  const running = activity.status === "running";
  const target = quotedToolActivityTarget(activity.label);
  switch (activity.toolName) {
    case "project_list_files":
      return running ? "Listing files..." : "Listed files";
    case "project_read_file":
      return running ? `Reading ${target || "file"}...` : `Read ${target || "file"}`;
    case "project_grep":
      return running
        ? `Searching files${target ? ` for ${target}` : ""}...`
        : `Searched files${target ? ` for ${target}` : ""}`;
    case "project_write_file":
    case "project_touch":
      return running ? `Writing ${target || "file"}...` : `Wrote ${target || "file"}`;
    case "project_apply_patch":
    case "project_edit":
      return running ? `Patching ${target || "file"}...` : `Patched ${target || "file"}`;
    case "project_move":
      return running ? "Moving file..." : "Moved file";
    case "project_delete":
      return running ? `Deleting ${target || "file"}...` : `Deleted ${target || "file"}`;
    case "project_git_status":
      return running ? "Checking project status..." : "Checked project status";
    case "project_git_diff":
      return running ? "Diffing project files..." : "Diffed project files";
    case "project_exec":
      return running ? "Starting project command..." : "Started project command";
    case "project_wait":
      return running ? "Waiting on project command..." : "Updated project command status";
    case "project_cancel":
      return running ? "Canceling project command..." : "Canceled project command";
    case "project_logs":
      return running ? "Reading project logs..." : "Read project logs";
    case "project_test":
      return running ? "Running project tests..." : "Ran project tests";
    case "project_lint":
      return running ? "Running project lint..." : "Ran project lint";
    case "project_run":
      return running ? `Running ${target || "file"}...` : `Ran ${target || "file"}`;
    default:
      return running ? `Doing ${activity.label}...` : `Did ${activity.label}`;
  }
}

export function toolActivityHasDetails(activity: Pick<ToolActivityEntry, "command" | "output">) {
  return Boolean(activity.command || activity.output);
}

export function isStreamingMessageStatus(status: string) {
  return status === "streaming" || status === "in_progress";
}

export function projectFileKindLabel(fileKind: ProjectFileKind | undefined) {
  if (fileKind === "notebook") {
    return "Notebook";
  }
  if (fileKind === "text") {
    return "Text";
  }
  return "Python";
}
