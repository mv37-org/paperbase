(function () {
  const API_BASE = window.PAPERBASE_BASE_URL;
  const OPEN_TABS_KEY = "paperbase-open-tabs";
  const PENDING_PROMPT_KEY = "paperbase-pending-prompt";
  const AUTH_VIEW_COPY = {
    login: { title: "Log in", caption: "Use your account to continue." },
    signup: { title: "Create account", caption: "Start a workspace for tracking research-backed product decisions." },
    "reset-request": { title: "Forgot password", caption: "We will send you a reset link if the account exists." },
    "reset-password": { title: "Reset password", caption: "Choose a new password for your account." },
    "api-key": { title: "Anthropic API key", caption: "Add your key after the free responses run out." },
  };

  const state = {
    session: null,
    threads: [],
    threadMap: new Map(),
    openTabs: [],
    activeThreadId: null,
    documents: [],
    authView: "login",
  };

  const el = {
    landingPage: document.getElementById("landing-page"),
    appShell: document.getElementById("app-shell"),
    landingPromptForm: document.getElementById("landing-prompt-form"),
    landingPromptInput: document.getElementById("landing-prompt-input"),
    landingUploadTrigger: document.getElementById("landing-upload-trigger"),
    exampleList: document.getElementById("example-list"),
    authModal: document.getElementById("auth-modal"),
    modalTitle: document.getElementById("auth-modal-title"),
    modalCaption: document.getElementById("auth-modal-caption"),
    modalFlash: document.getElementById("modal-flash"),
    sessionSummary: document.getElementById("session-summary"),
    quotaRow: document.getElementById("quota-row"),
    threadList: document.getElementById("thread-list"),
    newChatButton: document.getElementById("new-chat-button"),
    tabStrip: document.getElementById("tab-strip"),
    threadHeader: document.getElementById("thread-header"),
    messageList: document.getElementById("message-list"),
    composerForm: document.getElementById("composer-form"),
    composerInput: document.getElementById("composer-input"),
    composerStatus: document.getElementById("composer-status"),
    composerUploadTrigger: document.getElementById("composer-upload-trigger"),
    toggleUploads: document.getElementById("toggle-uploads"),
    togglePapers: document.getElementById("toggle-papers"),
    appUploadFile: document.getElementById("app-upload-file"),
    logoutButton: document.getElementById("logout-button"),
    manageKeyButton: document.getElementById("manage-key-button"),
    deleteApiKeyButton: document.getElementById("delete-api-key-button"),
    resetTokenInput: document.getElementById("reset-token-input"),
  };

  function authViews() {
    return Array.from(document.querySelectorAll("[data-auth-view]"));
  }

  async function api(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    if (response.status === 204) {
      return null;
    }
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail || JSON.stringify(payload) : String(payload);
      throw new Error(detail || "Request failed");
    }
    return payload;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function setFlash(message) {
    el.modalFlash.textContent = message || "";
  }

  function showAuthModal(view = "login") {
    state.authView = view;
    const copy = AUTH_VIEW_COPY[view] || AUTH_VIEW_COPY.login;
    el.modalTitle.textContent = copy.title;
    el.modalCaption.textContent = copy.caption;
    authViews().forEach((viewNode) => {
      viewNode.classList.toggle("visible", viewNode.dataset.authView === view);
    });
    el.authModal.classList.remove("hidden");
  }

  function hideAuthModal() {
    el.authModal.classList.add("hidden");
    setFlash("");
  }

  function rememberPendingPrompt(prompt) {
    if (prompt && prompt.trim()) {
      sessionStorage.setItem(PENDING_PROMPT_KEY, prompt.trim());
    }
  }

  function consumePendingPrompt() {
    const prompt = sessionStorage.getItem(PENDING_PROMPT_KEY);
    sessionStorage.removeItem(PENDING_PROMPT_KEY);
    return prompt;
  }

  function persistTabs() {
    localStorage.setItem(OPEN_TABS_KEY, JSON.stringify(state.openTabs));
  }

  function restoreTabs() {
    try {
      const parsed = JSON.parse(localStorage.getItem(OPEN_TABS_KEY) || "[]");
      state.openTabs = Array.isArray(parsed) ? parsed : [];
    } catch {
      state.openTabs = [];
    }
  }

  function renderRoot() {
    const authenticated = !!(state.session && state.session.authenticated);
    el.landingPage.classList.toggle("hidden", authenticated);
    el.appShell.classList.toggle("hidden", !authenticated);
  }

  function renderSessionSummary() {
    if (!state.session || !state.session.user) {
      el.sessionSummary.textContent = "";
      el.quotaRow.textContent = "";
      return;
    }
    el.sessionSummary.innerHTML = `<strong>${escapeHtml(state.session.user.display_name || state.session.user.email)}</strong><div class="muted">${escapeHtml(state.session.user.email)}</div>`;
    const free = state.session.remaining_free_responses;
    const keyText = state.session.has_anthropic_api_key ? "personal Anthropic key saved" : "no personal key saved";
    el.quotaRow.textContent = `${free} free responses left · ${keyText}`;
  }

  function renderThreads() {
    el.threadList.innerHTML = "";
    for (const thread of state.threads) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "thread-card" + (state.activeThreadId === thread.thread_id ? " active" : "");
      button.innerHTML = `
        <strong>${escapeHtml(thread.title)}</strong>
        <div class="muted">${escapeHtml(thread.last_message_preview || "")}</div>
      `;
      button.addEventListener("click", () => openThread(thread.thread_id));
      el.threadList.appendChild(button);
    }
  }

  function renderTabs() {
    el.tabStrip.innerHTML = "";
    for (const threadId of state.openTabs) {
      const thread = state.threadMap.get(threadId);
      if (!thread) {
        continue;
      }
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "tab" + (state.activeThreadId === threadId ? " active" : "");
      tab.textContent = thread.title;
      tab.addEventListener("click", () => openThread(threadId));
      el.tabStrip.appendChild(tab);
    }
  }

  function renderActiveThread() {
    const thread = state.threadMap.get(state.activeThreadId);
    if (!thread) {
      el.threadHeader.innerHTML = "<div class='muted'>Open a chat to begin.</div>";
      el.messageList.innerHTML = "";
      return;
    }
    el.threadHeader.innerHTML = `<strong>${escapeHtml(thread.title)}</strong><div class="muted">${thread.messages.length} messages</div>`;
    el.messageList.innerHTML = "";
    for (const message of thread.messages) {
      const article = document.createElement("article");
      article.className = `message ${message.role}`;
      article.innerHTML = `<div class="message-meta">${escapeHtml(message.role)}</div><div>${escapeHtml(message.content_markdown)}</div>`;
      if (message.citations && message.citations.length) {
        const citationList = document.createElement("div");
        citationList.className = "message-citations";
        for (const citation of message.citations) {
          const card = document.createElement("div");
          card.className = "citation-card";
          card.innerHTML = `
            <strong>${escapeHtml(citation.source_id)} · ${escapeHtml(citation.title)}</strong>
            <div class="muted">${escapeHtml(citation.path)}</div>
            <div class="muted">${escapeHtml(citation.excerpt)}</div>
          `;
          citationList.appendChild(card);
        }
        article.appendChild(citationList);
      }
      el.messageList.appendChild(article);
    }
    el.messageList.scrollTop = el.messageList.scrollHeight;
  }

  function syncOpenTabs() {
    state.openTabs = state.openTabs.filter((threadId) => state.threadMap.has(threadId));
    if (state.activeThreadId && !state.openTabs.includes(state.activeThreadId)) {
      state.openTabs.push(state.activeThreadId);
    }
    if (!state.activeThreadId && state.openTabs.length) {
      state.activeThreadId = state.openTabs[0];
    }
    persistTabs();
    renderTabs();
    renderActiveThread();
  }

  async function loadSession() {
    state.session = await api("/v1/browser/session", { method: "GET" });
    renderRoot();
    renderSessionSummary();
  }

  async function loadDocuments() {
    if (!state.session || !state.session.authenticated) {
      return;
    }
    const payload = await api("/v1/browser/uploads", { method: "GET" });
    state.documents = payload.documents || [];
  }

  async function loadThreads() {
    if (!state.session || !state.session.authenticated) {
      return;
    }
    const payload = await api("/v1/browser/chats", { method: "GET" });
    state.threads = payload.threads || [];
    renderThreads();
    for (const thread of state.threads) {
      if (state.openTabs.includes(thread.thread_id) && !state.threadMap.has(thread.thread_id)) {
        await fetchThread(thread.thread_id);
      }
    }
    syncOpenTabs();
  }

  async function fetchThread(threadId) {
    const thread = await api(`/v1/browser/chats/${threadId}`, { method: "GET" });
    state.threadMap.set(threadId, thread);
    return thread;
  }

  async function openThread(threadId) {
    if (!state.threadMap.has(threadId)) {
      await fetchThread(threadId);
    }
    if (!state.openTabs.includes(threadId)) {
      state.openTabs.push(threadId);
    }
    state.activeThreadId = threadId;
    persistTabs();
    renderThreads();
    renderTabs();
    renderActiveThread();
  }

  async function createThread(title = "") {
    const thread = await api("/v1/browser/chats", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    state.threadMap.set(thread.thread_id, thread);
    state.activeThreadId = thread.thread_id;
    if (!state.openTabs.includes(thread.thread_id)) {
      state.openTabs.push(thread.thread_id);
    }
    await loadThreads();
    renderTabs();
    renderActiveThread();
    return thread;
  }

  async function sendMessage(threadId, content) {
    el.composerStatus.textContent = "Thinking...";
    const payload = await api(`/v1/browser/chats/${threadId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        content,
        tag_filters: [],
        include_uploads: el.toggleUploads.checked,
        include_papers: el.togglePapers.checked,
      }),
    });
    state.threadMap.set(threadId, payload.thread);
    state.session.remaining_free_responses = payload.remaining_free_responses;
    state.session.has_anthropic_api_key = payload.has_anthropic_api_key;
    renderSessionSummary();
    renderThreads();
    renderTabs();
    renderActiveThread();
    el.composerStatus.textContent = "";
    if (payload.state === "api_key_required") {
      setFlash("You have used your 3 free responses. Add an Anthropic API key to continue.");
      showAuthModal("api-key");
    }
  }

  async function bootstrapAuthenticatedUi() {
    await loadDocuments();
    restoreTabs();
    await loadThreads();
    const pendingPrompt = consumePendingPrompt();
    if (pendingPrompt) {
      const thread = await createThread();
      await sendMessage(thread.thread_id, pendingPrompt);
    } else if (state.openTabs.length) {
      await openThread(state.openTabs[0]);
    } else if (state.threads.length) {
      await openThread(state.threads[0].thread_id);
    }
  }

  async function handleAuthSuccess() {
    await loadSession();
    hideAuthModal();
    await bootstrapAuthenticatedUi();
  }

  async function submitJsonForm(form, path) {
    const payload = Object.fromEntries(new FormData(form).entries());
    return api(path, { method: "POST", body: JSON.stringify(payload) });
  }

  async function uploadSelectedFile(fileInput) {
    const file = fileInput.files[0];
    if (!file) {
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${API_BASE}/v1/browser/uploads`, {
      method: "POST",
      body: formData,
      credentials: "include",
    });
    if (!response.ok) {
      throw new Error((await response.json()).detail || "Upload failed");
    }
    fileInput.value = "";
    await loadDocuments();
  }

  function bindEvents() {
    document.querySelectorAll("[data-close-modal='true']").forEach((node) => {
      node.addEventListener("click", hideAuthModal);
    });

    document.querySelectorAll("[data-auth-link]").forEach((node) => {
      node.addEventListener("click", () => showAuthModal(node.dataset.authLink));
    });

    el.landingPromptForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const prompt = el.landingPromptInput.value.trim();
      if (!prompt) {
        return;
      }
      if (!state.session || !state.session.authenticated) {
        rememberPendingPrompt(prompt);
        showAuthModal("login");
        return;
      }
      const thread = await createThread();
      el.landingPromptInput.value = "";
      await sendMessage(thread.thread_id, prompt);
    });

    el.exampleList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-prompt]");
      if (!button) {
        return;
      }
      const prompt = button.dataset.prompt || "";
      el.landingPromptInput.value = prompt;
      rememberPendingPrompt(prompt);
      if (!state.session || !state.session.authenticated) {
        showAuthModal("login");
      }
    });

    el.landingUploadTrigger.addEventListener("click", () => {
      if (!state.session || !state.session.authenticated) {
        showAuthModal("login");
        return;
      }
      el.appUploadFile.click();
    });

    el.composerUploadTrigger.addEventListener("click", () => {
      el.appUploadFile.click();
    });

    el.appUploadFile.addEventListener("change", async () => {
      const file = el.appUploadFile.files[0];
      if (!file) {
        return;
      }
      try {
        await uploadSelectedFile(el.appUploadFile);
        el.composerStatus.textContent = `Attached ${file.name}`;
      } catch (error) {
        el.composerStatus.textContent = error.message;
      }
    });

    el.newChatButton.addEventListener("click", async () => {
      await createThread();
    });

    el.composerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const content = el.composerInput.value.trim();
      if (!content) {
        return;
      }
      if (!state.activeThreadId) {
        const thread = await createThread();
        state.activeThreadId = thread.thread_id;
      }
      el.composerInput.value = "";
      await sendMessage(state.activeThreadId, content);
    });

    el.logoutButton.addEventListener("click", async () => {
      await api("/v1/browser/auth/logout", { method: "POST" });
      state.session = null;
      state.documents = [];
      state.threads = [];
      state.threadMap = new Map();
      state.openTabs = [];
      state.activeThreadId = null;
      renderRoot();
    });

    el.manageKeyButton.addEventListener("click", () => showAuthModal("api-key"));

    document.querySelectorAll("[data-google-login='true']").forEach((node) => {
      node.addEventListener("click", () => {
        window.location.href = `${API_BASE}/v1/browser/auth/google/start`;
      });
    });

    document.getElementById("login-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await submitJsonForm(event.target, "/v1/browser/auth/login");
        await handleAuthSuccess();
      } catch (error) {
        setFlash(error.message);
      }
    });

    document.getElementById("signup-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await submitJsonForm(event.target, "/v1/browser/auth/signup");
        await handleAuthSuccess();
      } catch (error) {
        setFlash(error.message);
      }
    });

    document.getElementById("forgot-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await submitJsonForm(event.target, "/v1/browser/auth/forgot-password");
        setFlash("If that account exists, a reset link has been sent.");
      } catch (error) {
        setFlash(error.message);
      }
    });

    document.getElementById("reset-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await submitJsonForm(event.target, "/v1/browser/auth/reset-password");
        setFlash("Password reset complete. You can now log in.");
        showAuthModal("login");
      } catch (error) {
        setFlash(error.message);
      }
    });

    document.getElementById("api-key-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const payload = Object.fromEntries(new FormData(event.target).entries());
        state.session = await api("/v1/browser/settings/anthropic-key", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        renderSessionSummary();
        setFlash("Anthropic API key saved.");
      } catch (error) {
        setFlash(error.message);
      }
    });

    el.deleteApiKeyButton.addEventListener("click", async () => {
      try {
        state.session = await api("/v1/browser/settings/anthropic-key", { method: "DELETE" });
        renderSessionSummary();
        setFlash("Anthropic API key removed.");
      } catch (error) {
        setFlash(error.message);
      }
    });
  }

  async function init() {
    bindEvents();
    const params = new URLSearchParams(window.location.search);
    if (params.get("reset_token")) {
      el.resetTokenInput.value = params.get("reset_token");
      showAuthModal("reset-password");
    }
    if (params.get("auth_error")) {
      setFlash("Google login failed. Try email/password instead.");
      showAuthModal("login");
    }
    await loadSession();
    if (state.session && state.session.authenticated) {
      await bootstrapAuthenticatedUi();
    }
  }

  init().catch((error) => {
    console.error(error);
    el.composerStatus.textContent = "The chat app could not reach paperbase.";
  });
})();
