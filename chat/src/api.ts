import type {
  BrowserSessionResponse,
  ChatStreamEventMap,
  ChatThreadListResponse,
  ChatThreadResponse,
  FigureResponse,
  FileContentResponse,
  PaperResponse,
  SendChatMessageResponse,
  TableResponse,
  UploadListResponse,
} from "./types";

const RAW_API_BASE =
  (window as typeof window & { PAPERBASE_BASE_URL?: string }).PAPERBASE_BASE_URL ?? "";
const API_BASE =
  RAW_API_BASE && !RAW_API_BASE.startsWith("__")
    ? RAW_API_BASE
    : ((import.meta.env.VITE_PAPERBASE_BASE_URL as string | undefined) ?? "");

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
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: string }).detail ?? "Request failed")
        : String(payload ?? "Request failed");
    throw new Error(detail);
  }
  syncBrowserSession(payload);
  return payload as T;
}

export function googleLoginUrl() {
  const returnTo = encodeURIComponent(window.location.origin);
  return `${API_BASE}/v1/browser/auth/google/start?return_to=${returnTo}`;
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

export function loadThreads() {
  return api<ChatThreadListResponse>("/v1/browser/chats", { method: "GET" });
}

export function getThread(threadId: string) {
  return api<ChatThreadResponse>(`/v1/browser/chats/${threadId}`, { method: "GET" });
}

export function createThread(title = "") {
  return api<ChatThreadResponse>("/v1/browser/chats", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
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
) {
  const response = await fetch(`${API_BASE}/v1/browser/chats/${threadId}/messages/stream`, {
    method: "POST",
    body: chatRequestBody(payload),
    credentials: "include",
    cache: "no-store",
    headers: buildHeaders({ method: "POST" }),
  });

  if (!response.ok) {
    handleUnauthorized(response);
    const body = await parsePayload(response);
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail?: string }).detail ?? "Streaming request failed")
        : String(body ?? "Streaming request failed");
    throw new StreamingChatError(detail, true);
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
