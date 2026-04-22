import type {
  BrowserSessionResponse,
  ChatStreamEventMap,
  ChatThreadListResponse,
  ChatThreadResponse,
  FigureResponse,
  FileContentResponse,
  MentionSuggestionResult,
  PaperResponse,
  ProjectItemPageResponse,
  ProjectRepoConflictResolution,
  ProjectRepoSyncResponse,
  ProjectListResponse,
  ProjectResponse,
  ProjectSessionPageResponse,
  PythonFileResponse,
  RunPythonFileResponse,
  SendChatMessageResponse,
  TableResponse,
  UploadListResponse,
} from "./types";

const RAW_API_BASE =
  (window as typeof window & { PAPERBASE_BASE_URL?: string }).PAPERBASE_BASE_URL ?? "";
const CONFIGURED_API_BASE =
  RAW_API_BASE && !RAW_API_BASE.startsWith("__")
    ? RAW_API_BASE
    : ((import.meta.env.VITE_PAPERBASE_BASE_URL as string | undefined) ?? "");
const API_BASE = resolveApiBase(CONFIGURED_API_BASE);

let browserCsrfToken: string | null = null;

export type SendThreadMessagePayload = {
  content: string;
  include_uploads: boolean;
  include_papers: boolean;
};

type StreamEventHandler = <T extends keyof ChatStreamEventMap>(
  eventName: T,
  payload: ChatStreamEventMap[T],
) => void;

export class StreamingChatError extends Error {
  canFallback: boolean;

  constructor(message: string, canFallback: boolean) {
    super(message);
    this.name = "StreamingChatError";
    this.canFallback = canFallback;
  }
}

export class StreamingChatAbortError extends Error {
  constructor(message = "Streaming request aborted") {
    super(message);
    this.name = "StreamingChatAbortError";
  }
}

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export function resolveApiBase(rawBase: string, currentPageUrl = window.location.href) {
  if (!rawBase) {
    return "";
  }

  try {
    const currentPage = new URL(currentPageUrl);
    const resolved = new URL(rawBase, currentPage.origin);
    if (currentPage.protocol === "https:" && resolved.protocol === "http:") {
      resolved.protocol = "https:";
      if (resolved.port === "80") {
        resolved.port = "";
      }
    }
    return resolved.toString().replace(/\/+$/, "");
  } catch {
    if (currentPageUrl.startsWith("https://") && rawBase.startsWith("http://")) {
      return `https://${rawBase.slice("http://".length)}`.replace(/\/+$/, "");
    }
    return rawBase.replace(/\/+$/, "");
  }
}

function shouldAttachCsrf(method?: string) {
  const normalized = (method ?? "GET").toUpperCase();
  return !["GET", "HEAD", "OPTIONS"].includes(normalized);
}

function syncBrowserSession(payload: unknown) {
  if (!payload || typeof payload !== "object" || !("authenticated" in payload)) {
    return;
  }
  const session = payload as BrowserSessionResponse;
  browserCsrfToken = session.authenticated ? (session.csrf_token ?? null) : null;
}

function clearBrowserSessionToken() {
  browserCsrfToken = null;
}

async function parsePayload(response: Response) {
  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function buildHeaders(init: RequestInit = {}) {
  const headers = new Headers(init.headers ?? {});
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (shouldAttachCsrf(init.method) && browserCsrfToken && !headers.has("X-Paperbase-CSRF")) {
    headers.set("X-Paperbase-CSRF", browserCsrfToken);
  }
  return headers;
}

function handleUnauthorized(response: Response) {
  if (response.status !== 401) {
    return;
  }
  clearBrowserSessionToken();
  window.dispatchEvent(new CustomEvent("paperbase:auth-expired"));
}

function chatRequestBody(payload: SendThreadMessagePayload) {
  return JSON.stringify({
    content: payload.content,
    tag_filters: [],
    include_uploads: payload.include_uploads,
    include_papers: payload.include_papers,
  });
}

function parseSseEvent(block: string) {
  let eventName = "";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (!eventName || dataLines.length === 0) {
    return null;
  }
  return {
    eventName,
    payload: JSON.parse(dataLines.join("\n")) as ChatStreamEventMap[keyof ChatStreamEventMap],
  };
}

function normalizeErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "string") {
    const trimmed = payload.trim();
    return trimmed || fallback;
  }

  if (payload == null) {
    return fallback;
  }

  if (Array.isArray(payload)) {
    const messages = payload
      .map((item) => normalizeErrorMessage(item, ""))
      .filter((message) => message.trim().length > 0);
    return messages.length > 0 ? messages.join("; ") : fallback;
  }

  if (typeof payload === "object") {
    if ("detail" in payload) {
      return normalizeErrorMessage((payload as { detail?: unknown }).detail, fallback);
    }
    if ("message" in payload) {
      return normalizeErrorMessage((payload as { message?: unknown }).message, fallback);
    }
    if ("error" in payload) {
      return normalizeErrorMessage((payload as { error?: unknown }).error, fallback);
    }
    if ("msg" in payload) {
      const detail = payload as { msg?: unknown; loc?: unknown };
      const message = normalizeErrorMessage(detail.msg, fallback);
      const location = Array.isArray(detail.loc)
        ? detail.loc
            .map((part) =>
              typeof part === "string" || typeof part === "number" ? String(part) : "",
            )
            .filter(Boolean)
            .filter((part, index) => !(index === 0 && ["body", "query", "path"].includes(part)))
            .join(".")
        : "";
      return location ? `${location}: ${message}` : message;
    }
    try {
      const encoded = JSON.stringify(payload);
      return encoded && encoded !== "{}" ? encoded : fallback;
    } catch {
      return fallback;
    }
  }

  return String(payload);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = buildHeaders(init);
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers,
  });
  const payload = await parsePayload(response);
  if (!response.ok) {
    handleUnauthorized(response);
    throw new ApiError(normalizeErrorMessage(payload, "Request failed"), response.status, payload);
  }
  syncBrowserSession(payload);
  return payload as T;
}

export function googleLoginUrl() {
  const returnTo = encodeURIComponent(window.location.origin);
  return `${API_BASE}/v1/browser/auth/google/start?return_to=${returnTo}`;
}

export function githubLoginUrl() {
  const returnTo = encodeURIComponent(`${window.location.origin}${window.location.pathname}`);
  return `${API_BASE}/v1/browser/auth/github/start?return_to=${returnTo}`;
}

export function loadSession() {
  return api<BrowserSessionResponse>("/v1/browser/session", { method: "GET" });
}

export function logoutBrowserSession() {
  return api<{ status: string }>("/v1/browser/auth/logout", { method: "POST" }).finally(
    clearBrowserSessionToken,
  );
}

export function loadUploads() {
  return api<UploadListResponse>("/v1/browser/uploads", { method: "GET" });
}

export async function suggestBrowserPapers(query: string, limit = 5) {
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
  });
  return api<MentionSuggestionResult>(`/v1/browser/papers/suggest?${params.toString()}`, {
    method: "GET",
  });
}

export function loadThreads() {
  return api<ChatThreadListResponse>("/v1/browser/chats", { method: "GET" });
}

export function loadProjects() {
  return api<ProjectListResponse>("/v1/browser/projects", { method: "GET" });
}

export function createProject(name: string, description = "") {
  return api<ProjectResponse>("/v1/browser/projects", {
    method: "POST",
    body: JSON.stringify({ name, description }),
  });
}

export function updateProject(projectId: string, name: string, description = "") {
  return api<ProjectResponse>(`/v1/browser/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify({ name, description }),
  });
}

export function deleteProject(projectId: string) {
  return api<{ status: string }>(`/v1/browser/projects/${projectId}`, { method: "DELETE" });
}

export function loadProjectSessions(projectId: string, limit = 5, offset = 0) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return api<ProjectSessionPageResponse>(
    `/v1/browser/projects/${projectId}/sessions?${params.toString()}`,
    { method: "GET" },
  );
}

export function loadProjectItems(projectId: string, limit = 25, offset = 0) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return api<ProjectItemPageResponse>(
    `/v1/browser/projects/${projectId}/items?${params.toString()}`,
    { method: "GET" },
  );
}

export function createPythonFile(projectId: string) {
  return api<PythonFileResponse>(`/v1/browser/projects/${projectId}/python-files`, {
    method: "POST",
  });
}

export function getPythonFile(fileId: string) {
  return api<PythonFileResponse>(`/v1/browser/python-files/${fileId}`, { method: "GET" });
}

export function updatePythonFile(
  fileId: string,
  payload: { filename?: string | null; content?: string | null },
) {
  return api<PythonFileResponse>(`/v1/browser/python-files/${fileId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function runPythonFile(fileId: string) {
  return api<RunPythonFileResponse>(`/v1/browser/python-files/${fileId}/run`, {
    method: "POST",
  });
}

export function getProjectRepoSync(projectId: string) {
  return api<ProjectRepoSyncResponse>(`/v1/browser/projects/${projectId}/repo-sync`, {
    method: "GET",
  });
}

export function syncProjectRepo(
  projectId: string,
  payload: {
    commitMessage: string;
    expectedRemoteHeadSha?: string | null;
    conflictResolutions?: ProjectRepoConflictResolution[];
  },
) {
  return api<ProjectRepoSyncResponse>(`/v1/browser/projects/${projectId}/repo-sync`, {
    method: "POST",
    body: JSON.stringify({
      commit_message: payload.commitMessage,
      expected_remote_head_sha: payload.expectedRemoteHeadSha ?? null,
      conflict_resolutions: payload.conflictResolutions ?? [],
    }),
  });
}

export function getThread(threadId: string) {
  return api<ChatThreadResponse>(`/v1/browser/chats/${threadId}`, { method: "GET" });
}

export function createThread(title = "", projectId?: string) {
  return api<ChatThreadResponse>("/v1/browser/chats", {
    method: "POST",
    body: JSON.stringify({ title, project_id: projectId ?? null }),
  });
}

export function moveThreadToProject(threadId: string, projectId: string) {
  return api<ChatThreadResponse>(`/v1/browser/chats/${threadId}/project`, {
    method: "PATCH",
    body: JSON.stringify({ project_id: projectId }),
  });
}

export function deleteThread(threadId: string) {
  return api<{ status: string }>(`/v1/browser/chats/${threadId}`, { method: "DELETE" });
}

export function sendThreadMessage(threadId: string, payload: SendThreadMessagePayload) {
  return api<SendChatMessageResponse>(`/v1/browser/chats/${threadId}/messages`, {
    method: "POST",
    body: chatRequestBody(payload),
  });
}

export async function sendThreadMessageStream(
  threadId: string,
  payload: SendThreadMessagePayload,
  onEvent: StreamEventHandler,
  options: { signal?: AbortSignal } = {},
) {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/v1/browser/chats/${threadId}/messages/stream`, {
      method: "POST",
      body: chatRequestBody(payload),
      credentials: "include",
      cache: "no-store",
      headers: buildHeaders({ method: "POST" }),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new StreamingChatAbortError();
    }
    throw error;
  }

  if (!response.ok) {
    handleUnauthorized(response);
    const body = await parsePayload(response);
    throw new StreamingChatError(normalizeErrorMessage(body, "Streaming request failed"), true);
  }

  if (!response.body) {
    throw new StreamingChatError("Streaming response body was empty.", true);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawEvent = false;

  const emitBlock = (block: string) => {
    const parsed = parseSseEvent(block);
    if (!parsed) {
      return;
    }
    sawEvent = true;
    onEvent(parsed.eventName as keyof ChatStreamEventMap, parsed.payload);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (block) {
          emitBlock(block);
        }
        boundary = buffer.indexOf("\n\n");
      }

      if (done) {
        break;
      }
    }

    const trailing = buffer.trim();
    if (trailing) {
      emitBlock(trailing);
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new StreamingChatAbortError();
    }
    throw new StreamingChatError(
      error instanceof Error ? error.message : "Streaming request failed",
      !sawEvent,
    );
  } finally {
    reader.releaseLock();
  }
}

export async function uploadDocument(file: File) {
  const body = new FormData();
  body.append("file", file);
  return api("/v1/browser/uploads", { method: "POST", body });
}

export function postForm(path: string, form: HTMLFormElement) {
  const payload = Object.fromEntries(new FormData(form).entries());
  return api<BrowserSessionResponse | { status: string }>(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getBrowserPaper(paperId: string) {
  return api<PaperResponse>(`/v1/browser/papers/${paperId}`, { method: "GET" });
}

export function getBrowserPaperFile(paperId: string, path: string) {
  return api<FileContentResponse>(`/v1/browser/papers/${paperId}/files/${path}`, { method: "GET" });
}

export function getBrowserPaperFigure(paperId: string, figureId: string) {
  return api<FigureResponse>(`/v1/browser/papers/${paperId}/figures/${figureId}`, {
    method: "GET",
  });
}

export function getBrowserPaperTable(paperId: string, tableId: string) {
  return api<TableResponse>(`/v1/browser/papers/${paperId}/tables/${tableId}`, { method: "GET" });
}
