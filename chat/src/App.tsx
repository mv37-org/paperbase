import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, RefObject } from "react";

import {
  api,
  createThread,
  getThread,
  googleLoginUrl,
  loadSession,
  loadThreads,
  loadUploads,
  postForm,
  sendThreadMessage,
  uploadDocument,
} from "./api";
import { LandingMentionEditor } from "./LandingMentionEditor";
import type {
  AuthView,
  BrowserSessionResponse,
  ChatThreadResponse,
  ChatThreadSummary,
} from "./types";

const OPEN_TABS_KEY = "paperbase-open-tabs";
const PENDING_PROMPT_KEY = "paperbase-pending-prompt";

const AUTH_VIEW_COPY: Record<AuthView, { title: string; caption: string }> = {
  login: { title: "Log in", caption: "Use your account to continue." },
  signup: { title: "Create account", caption: "Start a workspace for tracking research-backed product decisions." },
  "reset-request": { title: "Forgot password", caption: "We will send you a reset link if the account exists." },
  "reset-password": { title: "Reset password", caption: "Choose a new password for your account." },
  "api-key": { title: "Anthropic API key", caption: "Add your key after the free responses run out." },
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

function useAutosizeTextarea(ref: RefObject<HTMLTextAreaElement | null>, value: string) {
  useEffect(() => {
    if (!ref.current) {
      return;
    }
    ref.current.style.height = "0px";
    ref.current.style.height = `${Math.min(ref.current.scrollHeight, 160)}px`;
  }, [ref, value]);
}

export default function App() {
  const [session, setSession] = useState<BrowserSessionResponse | null>(null);
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [threadMap, setThreadMap] = useState<Record<string, ChatThreadResponse>>({});
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [flash, setFlash] = useState("");
  const [landingPrompt, setLandingPrompt] = useState("");
  const [composerInput, setComposerInput] = useState("");
  const [composerStatus, setComposerStatus] = useState("");
  const [includeUploads, setIncludeUploads] = useState(true);
  const [includePapers, setIncludePapers] = useState(true);

  const sessionRef = useRef<BrowserSessionResponse | null>(null);
  const threadMapRef = useRef<Record<string, ChatThreadResponse>>({});
  const openTabsRef = useRef<string[]>([]);
  const appUploadRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  useAutosizeTextarea(composerRef, composerInput);

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

  const authCopy = AUTH_VIEW_COPY[authView];
  const activeThread = activeThreadId ? threadMap[activeThreadId] ?? null : null;
  const authenticated = Boolean(session?.authenticated);

  const sessionSummary = useMemo(() => {
    if (!session?.user) {
      return null;
    }
    return {
      displayName: session.user.display_name || session.user.email,
      email: session.user.email,
      quota: `${session.remaining_free_responses} free responses left · ${
        session.has_anthropic_api_key ? "personal Anthropic key saved" : "no personal key saved"
      }`,
    };
  }, [session]);

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

  const sendMessage = useCallback(
    async (threadId: string, content: string) => {
      setComposerStatus("Thinking...");
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
        setFlash("You have used your 3 free responses. Add an Anthropic API key to continue.");
        setAuthView("api-key");
        setAuthModalOpen(true);
      }
      return payload;
    },
    [includePapers, includeUploads, refreshThreads],
  );

  const bootstrapAuthenticatedUi = useCallback(async () => {
    await refreshUploads();
    const restoredTabs = restoreTabs();
    setOpenTabs(restoredTabs);
    const loadedThreads = await refreshThreads();
    const availableIds = new Set(loadedThreads.map((thread) => thread.thread_id));
    for (const threadId of restoredTabs) {
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

    const firstOpen = restoredTabs.find((threadId) => availableIds.has(threadId));
    if (firstOpen) {
      await openThread(firstOpen);
      return;
    }

    if (loadedThreads.length > 0) {
      await openThread(loadedThreads[0].thread_id);
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
        setFlash("Google login failed. Try email/password instead.");
        setAuthView("login");
        setAuthModalOpen(true);
      }
      const nextSession = await loadSession();
      setSession(nextSession);
      if (nextSession.authenticated) {
        await bootstrapAuthenticatedUi();
      }
    })().catch((error: unknown) => {
      console.error(error);
      setComposerStatus("The chat app could not reach paperbase.");
    });
  }, [bootstrapAuthenticatedUi]);

  const openAuthModal = useCallback((view: AuthView) => {
    setFlash("");
    setAuthView(view);
    setAuthModalOpen(true);
  }, []);

  const handleAuthSuccess = useCallback(
    async (nextSession: BrowserSessionResponse) => {
      setSession(nextSession);
      setAuthModalOpen(false);
      setFlash("");
      await bootstrapAuthenticatedUi();
    },
    [bootstrapAuthenticatedUi],
  );

  const handleLandingSubmit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
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
    async (event: FormEvent) => {
      event.preventDefault();
      const content = composerInput.trim();
      if (!content) {
        return;
      }
      const threadId = activeThreadId ?? (await createNewThread()).thread_id;
      setComposerInput("");
      await sendMessage(threadId, content);
    },
    [activeThreadId, composerInput, createNewThread, sendMessage],
  );

  return (
    <>
      {!authenticated ? (
        <main id="landing-page" className="landing-page">
          <div className="landing-column">
            <a className="brandline brandline-link" href="https://mv37.org" target="_blank" rel="noreferrer">
              MV37
            </a>
            <h1 className="landing-title">Paperbase</h1>
            <p className="landing-subtitle">Research patterns for building better AI agents and systems.</p>
            <div className="landing-copy">
              <p>Paperbase helps AI researchers and agent builders keep up with fast-moving work without losing track of the original sources, so they can ask broad questions, compare ideas across papers, and pull out the design patterns that matter when building agents and AI systems.</p>
            </div>

            <form className="prompt-shell" onSubmit={handleLandingSubmit}>
              <button
                type="button"
                className="prompt-clip"
                id="landing-upload-trigger"
                aria-label="Attach document"
                onClick={() => {
                  if (!sessionRef.current?.authenticated) {
                    openAuthModal("login");
                    return;
                  }
                  appUploadRef.current?.click();
                }}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M16.5 6.5 9 14a3 3 0 1 0 4.24 4.24l7.01-7.02a5 5 0 0 0-7.07-7.07L5.46 11.9a7 7 0 1 0 9.9 9.9l6.01-6.01" />
                </svg>
              </button>
              <div className="landing-editor-root">
                <LandingMentionEditor value={landingPrompt} onChange={setLandingPrompt} />
              </div>
              <button id="landing-start-button" className="prompt-button" type="submit">
                Start
              </button>
            </form>

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
        <div id="app-shell" className="app-shell">
          <aside className="sidebar">
            <div className="sidebar-top">
              <div className="brandline">paperbase</div>
              <button id="new-chat-button" className="sidebar-button" onClick={() => void createNewThread()}>
                New chat
              </button>
            </div>

            <section className="sidebar-section">
              <div className="section-label">Account</div>
              {sessionSummary ? (
                <>
                  <div id="session-summary" className="session-summary">
                    <strong>{sessionSummary.displayName}</strong>
                    <div className="muted">{sessionSummary.email}</div>
                  </div>
                  <div className="quota-row" id="quota-row">
                    {sessionSummary.quota}
                  </div>
                </>
              ) : null}
              <button id="manage-key-button" className="sidebar-link" onClick={() => openAuthModal("api-key")}>
                Manage Anthropic key
              </button>
              <button
                id="logout-button"
                className="sidebar-link"
                onClick={async () => {
                  await api("/v1/browser/auth/logout", { method: "POST" });
                  setSession(null);
                  setThreads([]);
                  setThreadMap({});
                  setOpenTabs([]);
                  setActiveThreadId(null);
                  setLandingPrompt("");
                }}
              >
                Log out
              </button>
            </section>

            <section className="sidebar-section history-section">
              <div className="section-label">Previous sessions</div>
              <div id="thread-list" className="thread-list">
                {threads.map((thread) => (
                  <button
                    key={thread.thread_id}
                    type="button"
                    className={`thread-card${activeThreadId === thread.thread_id ? " active" : ""}`}
                    onClick={() => void openThread(thread.thread_id)}
                  >
                    <strong>{thread.title}</strong>
                    <div className="muted">{thread.last_message_preview}</div>
                  </button>
                ))}
              </div>
            </section>
          </aside>

          <section className="chat-stage">
            <div id="tab-strip" className="tab-strip">
              {openTabs.map((threadId) => {
                const thread = threadMap[threadId];
                if (!thread) {
                  return null;
                }
                return (
                  <button
                    key={threadId}
                    type="button"
                    className={`tab${activeThreadId === threadId ? " active" : ""}`}
                    onClick={() => void openThread(threadId)}
                  >
                    {thread.title}
                  </button>
                );
              })}
            </div>
            <div id="thread-header" className="thread-header">
              {activeThread ? (
                <>
                  <strong>{activeThread.title}</strong>
                  <div className="muted">{activeThread.messages.length} messages</div>
                </>
              ) : (
                <div className="muted">Open a chat to begin.</div>
              )}
            </div>
            <div id="message-list" className="message-list">
              {activeThread?.messages.map((message) => (
                <article key={message.message_id} className={`message ${message.role}`}>
                  <div className="message-meta">{message.role}</div>
                  <div>{message.content_markdown}</div>
                  {message.citations.length > 0 ? (
                    <div className="message-citations">
                      {message.citations.map((citation) => (
                        <div key={`${message.message_id}-${citation.source_id}`} className="citation-card">
                          <strong>
                            {citation.source_id} · {citation.title}
                          </strong>
                          <div className="muted">{citation.path}</div>
                          <div className="muted">{citation.excerpt}</div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
            <form id="composer-form" className="composer-form" onSubmit={handleComposerSubmit}>
              <div className="composer-meta">
                <label>
                  <input type="checkbox" checked={includeUploads} onChange={(event) => setIncludeUploads(event.target.checked)} /> uploaded docs
                </label>
                <label>
                  <input type="checkbox" checked={includePapers} onChange={(event) => setIncludePapers(event.target.checked)} /> indexed papers
                </label>
              </div>
              <div className="composer-shell">
                <button
                  type="button"
                  className="prompt-clip"
                  id="composer-upload-trigger"
                  aria-label="Attach document"
                  onClick={() => appUploadRef.current?.click()}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M16.5 6.5 9 14a3 3 0 1 0 4.24 4.24l7.01-7.02a5 5 0 0 0-7.07-7.07L5.46 11.9a7 7 0 1 0 9.9 9.9l6.01-6.01" />
                  </svg>
                </button>
                <textarea
                  ref={composerRef}
                  id="composer-input"
                  rows={1}
                  placeholder="Ask about the latest agent and AI systems research..."
                  value={composerInput}
                  onChange={(event) => setComposerInput(event.target.value)}
                />
                <button id="composer-send" className="prompt-button" type="submit">
                  Send
                </button>
              </div>
              <input ref={appUploadRef} id="app-upload-file" className="hidden-file-input" type="file" onChange={onUploadChange} />
              <div className="composer-actions">
                <div id="composer-status" className="muted">
                  {composerStatus}
                </div>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      <div id="auth-modal" className={`modal${authModalOpen ? "" : " hidden"}`} role="dialog" aria-modal="true">
        <div className="modal-backdrop" data-close-modal="true" onClick={() => setAuthModalOpen(false)} />
        <div className="modal-panel">
          <button className="modal-close" type="button" data-close-modal="true" onClick={() => setAuthModalOpen(false)}>
            ×
          </button>
          <div className="modal-heading">
            <h2 id="auth-modal-title" className="modal-title">
              {authCopy.title}
            </h2>
            <p id="auth-modal-caption" className="modal-caption">
              {authCopy.caption}
            </p>
          </div>

          <div id="modal-flash" className="modal-flash">
            {flash}
          </div>

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
                  setFlash(error instanceof Error ? error.message : "Login failed");
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
                  setFlash(error instanceof Error ? error.message : "Sign up failed");
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
                  setFlash("If that account exists, a reset link has been sent.");
                } catch (error) {
                  setFlash(error instanceof Error ? error.message : "Password reset failed");
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
                  setFlash("Password reset complete. You can now log in.");
                  setAuthView("login");
                } catch (error) {
                  setFlash(error instanceof Error ? error.message : "Password reset failed");
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
            <p className="muted">You get 3 free assistant responses. After that, add your own Anthropic API key to continue chatting.</p>
            <form
              id="api-key-form"
              className="stack"
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
                  const nextSession = await api<BrowserSessionResponse>("/v1/browser/settings/anthropic-key", {
                    method: "PUT",
                    body: JSON.stringify(payload),
                  });
                  setSession(nextSession);
                  setFlash("Anthropic API key saved.");
                } catch (error) {
                  setFlash(error instanceof Error ? error.message : "Saving API key failed");
                }
              }}
            >
              <input name="api_key" type="password" placeholder="Anthropic API key" required />
              <button className="primary-button" type="submit">
                Save key
              </button>
            </form>
            <button
              id="delete-api-key-button"
              className="danger-link"
              type="button"
              onClick={async () => {
                try {
                  const nextSession = await api<BrowserSessionResponse>("/v1/browser/settings/anthropic-key", { method: "DELETE" });
                  setSession(nextSession);
                  setFlash("Anthropic API key removed.");
                } catch (error) {
                  setFlash(error instanceof Error ? error.message : "Deleting API key failed");
                }
              }}
            >
              Delete saved key
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
