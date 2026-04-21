import type {
  BrowserSessionResponse,
  ChatThreadListResponse,
  ChatThreadResponse,
  SendChatMessageResponse,
  UploadListResponse,
} from "./types";

const RAW_API_BASE = (window as typeof window & { PAPERBASE_BASE_URL?: string }).PAPERBASE_BASE_URL ?? "";
const API_BASE =
  RAW_API_BASE && !RAW_API_BASE.startsWith("__")
    ? RAW_API_BASE
    : (import.meta.env.VITE_PAPERBASE_BASE_URL as string | undefined) ?? "";
let browserCsrfToken: string | null = null;

function shouldAttachCsrf(method?: string) {
  const normalized = (method ?? "GET").toUpperCase();
  return !["GET", "HEAD", "OPTIONS"].includes(normalized);
}

function syncBrowserSession(payload: unknown) {
  if (!payload || typeof payload !== "object" || !("authenticated" in payload)) {
    return;
  }
  const session = payload as BrowserSessionResponse;
  browserCsrfToken = session.authenticated ? session.csrf_token ?? null : null;
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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (shouldAttachCsrf(init.method) && browserCsrfToken && !headers.has("X-Paperbase-CSRF")) {
    headers.set("X-Paperbase-CSRF", browserCsrfToken);
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers,
  });
  const payload = await parsePayload(response);
  if (!response.ok) {
    if (response.status === 401) {
      clearBrowserSessionToken();
      window.dispatchEvent(new CustomEvent("paperbase:auth-expired"));
    }
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
  return api<{ status: string }>("/v1/browser/auth/logout", { method: "POST" }).finally(clearBrowserSessionToken);
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

export function sendThreadMessage(
  threadId: string,
  payload: { content: string; include_uploads: boolean; include_papers: boolean },
) {
  return api<SendChatMessageResponse>(`/v1/browser/chats/${threadId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      content: payload.content,
      tag_filters: [],
      include_uploads: payload.include_uploads,
      include_papers: payload.include_papers,
    }),
  });
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
