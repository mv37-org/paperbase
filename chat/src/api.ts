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
    : (import.meta.env.VITE_PAPERBASE_BASE_URL as string | undefined) ?? "http://127.0.0.1:8080";

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
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    ...init,
  });
  const payload = await parsePayload(response);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: string }).detail ?? "Request failed")
        : String(payload ?? "Request failed");
    throw new Error(detail);
  }
  return payload as T;
}

export function googleLoginUrl() {
  const returnTo = encodeURIComponent(window.location.origin);
  return `${API_BASE}/v1/browser/auth/google/start?return_to=${returnTo}`;
}

export function loadSession() {
  return api<BrowserSessionResponse>("/v1/browser/session", { method: "GET" });
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
  const response = await fetch(`${API_BASE}/v1/browser/uploads`, {
    method: "POST",
    body,
    credentials: "include",
  });
  const payload = await parsePayload(response);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: string }).detail ?? "Upload failed")
        : String(payload ?? "Upload failed");
    throw new Error(detail);
  }
  return payload;
}

export function postForm(path: string, form: HTMLFormElement) {
  const payload = Object.fromEntries(new FormData(form).entries());
  return api<BrowserSessionResponse | { status: string }>(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
