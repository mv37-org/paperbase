"""Inline HTML shell for the lightweight paper chat product."""

from __future__ import annotations


def render_app_shell() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>paperbase chat</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: rgba(255, 252, 247, 0.88);
      --panel-strong: #fffaf2;
      --text: #1f1a17;
      --muted: #65584f;
      --accent: #bc5b34;
      --accent-soft: rgba(188, 91, 52, 0.12);
      --line: rgba(31, 26, 23, 0.08);
      --shadow: 0 18px 50px rgba(70, 41, 22, 0.12);
      --radius: 22px;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(230, 179, 132, 0.35), transparent 30%),
        radial-gradient(circle at bottom right, rgba(66, 140, 126, 0.18), transparent 28%),
        linear-gradient(180deg, #f8f3eb 0%, #efe7db 100%);
    }

    .app-shell {
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      gap: 24px;
      min-height: 100vh;
      padding: 24px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }

    .sidebar {
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .brand {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .eyebrow {
      font-size: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }

    h1, h2, h3, p {
      margin: 0;
    }

    h1 {
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      font-size: 32px;
      line-height: 1.05;
    }

    .search-form, .composer {
      display: flex;
      gap: 10px;
    }

    input, textarea, button {
      font: inherit;
    }

    input, textarea {
      width: 100%;
      border: 1px solid rgba(31, 26, 23, 0.12);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.82);
      color: var(--text);
      padding: 14px 16px;
      outline: none;
      transition: border-color 160ms ease, transform 160ms ease, background 160ms ease;
    }

    input:focus, textarea:focus {
      border-color: rgba(188, 91, 52, 0.6);
      background: #fffdf9;
      transform: translateY(-1px);
    }

    button {
      border: 0;
      border-radius: 16px;
      padding: 13px 16px;
      background: linear-gradient(135deg, #c96740 0%, #aa4b2e 100%);
      color: white;
      font-weight: 700;
      cursor: pointer;
      transition: transform 140ms ease, box-shadow 140ms ease, opacity 140ms ease;
      box-shadow: 0 12px 30px rgba(169, 75, 46, 0.22);
    }

    button:hover {
      transform: translateY(-1px);
    }

    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
    }

    .paper-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow: auto;
      padding-right: 4px;
    }

    .paper-card {
      width: 100%;
      border: 1px solid transparent;
      border-radius: 18px;
      background: rgba(255, 248, 238, 0.84);
      padding: 14px;
      cursor: pointer;
      color: var(--text);
      text-align: left;
      box-shadow: none;
      transition: transform 140ms ease, border-color 140ms ease, background 140ms ease;
    }

    .paper-card:hover {
      transform: translateY(-1px);
      border-color: rgba(188, 91, 52, 0.24);
      background: #fffaf4;
    }

    .paper-card.active {
      border-color: rgba(188, 91, 52, 0.42);
      background: linear-gradient(180deg, rgba(255, 246, 235, 0.98), rgba(252, 238, 223, 0.98));
    }

    .paper-meta {
      color: var(--muted);
      font-size: 13px;
      margin-top: 6px;
    }

    .main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: 0;
    }

    .hero {
      padding: 24px 24px 18px 24px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      animation: fade-up 320ms ease;
    }

    .hero-copy {
      max-width: 70ch;
      color: var(--muted);
      line-height: 1.55;
    }

    .selected-paper {
      display: none;
      gap: 8px;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel-strong);
    }

    .selected-paper.visible {
      display: grid;
    }

    .chat-log {
      margin-top: 18px;
      min-height: 0;
      overflow: auto;
      padding: 0 24px 24px 24px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .message {
      max-width: min(760px, 100%);
      padding: 16px 18px;
      border-radius: 20px;
      white-space: pre-wrap;
      line-height: 1.6;
      border: 1px solid rgba(31, 26, 23, 0.08);
      animation: fade-up 220ms ease;
    }

    .message.user {
      align-self: flex-end;
      background: linear-gradient(180deg, #fff3ea, #fbe5d6);
    }

    .message.assistant {
      align-self: flex-start;
      background: rgba(255, 252, 247, 0.95);
    }

    .message-label {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 10px;
    }

    .evidence-list {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }

    .evidence-item {
      padding: 12px 14px;
      border-radius: 16px;
      background: var(--accent-soft);
      border: 1px solid rgba(188, 91, 52, 0.14);
    }

    .composer-wrap {
      padding: 0 24px 24px 24px;
    }

    textarea {
      min-height: 88px;
      resize: vertical;
    }

    .status {
      color: var(--muted);
      font-size: 14px;
      min-height: 22px;
    }

    @keyframes fade-up {
      from {
        opacity: 0;
        transform: translateY(8px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    @media (max-width: 920px) {
      .app-shell {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="panel sidebar">
      <div class="brand">
        <div class="eyebrow">paperbase chat</div>
        <h1>Talk to an indexed paper.</h1>
        <p class="hero-copy">Search the papers already stored in paperbase, pick one, then ask grounded follow-up questions against its extracted sections.</p>
      </div>
      <form class="search-form" id="search-form">
        <input id="paper-query" type="text" placeholder="Search title, topic, or arXiv id" autocomplete="off" />
        <button id="search-button" type="submit">Find</button>
      </form>
      <div class="status" id="search-status">Search for an indexed paper to begin.</div>
      <div class="paper-list" id="paper-list"></div>
    </aside>

    <main class="panel main">
      <section class="hero">
        <div class="selected-paper" id="selected-paper"></div>
      </section>

      <section class="chat-log" id="chat-log">
        <div class="message assistant">
          <div class="message-label">assistant</div>
          Pick a paper on the left. I will answer with evidence pulled from the sections stored in paperbase.
        </div>
      </section>

      <section class="composer-wrap">
        <form class="composer" id="composer">
          <textarea id="question-input" placeholder="Ask about the methods, results, assumptions, or limitations..." disabled></textarea>
          <button id="ask-button" type="submit" disabled>Ask</button>
        </form>
      </section>
    </main>
  </div>

  <script>
    const state = {
      papers: [],
      selectedPaper: null,
      history: [],
      searching: false,
      asking: false,
    };

    const elements = {
      searchForm: document.getElementById("search-form"),
      searchButton: document.getElementById("search-button"),
      paperQuery: document.getElementById("paper-query"),
      searchStatus: document.getElementById("search-status"),
      paperList: document.getElementById("paper-list"),
      selectedPaper: document.getElementById("selected-paper"),
      chatLog: document.getElementById("chat-log"),
      composer: document.getElementById("composer"),
      questionInput: document.getElementById("question-input"),
      askButton: document.getElementById("ask-button"),
    };

    function setSearchStatus(message) {
      elements.searchStatus.textContent = message;
    }

    function renderPaperList() {
      elements.paperList.innerHTML = "";
      for (const paper of state.papers) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "paper-card" + (state.selectedPaper && state.selectedPaper.paper_id === paper.paper_id ? " active" : "");
        card.innerHTML = `
          <strong>${escapeHtml(paper.title || paper.paper_id)}</strong>
          <div class="paper-meta">${escapeHtml(paper.arxiv_id || paper.paper_id)}</div>
          <div class="paper-meta">${escapeHtml((paper.summary_short || paper.abstract || "Indexed paper in paperbase").slice(0, 180))}</div>
        `;
        card.addEventListener("click", () => {
          state.selectedPaper = paper;
          state.history = [];
          renderSelectedPaper();
          resetChat();
          renderPaperList();
          elements.questionInput.disabled = false;
          elements.askButton.disabled = false;
          elements.questionInput.focus();
        });
        elements.paperList.appendChild(card);
      }
    }

    function renderSelectedPaper() {
      if (!state.selectedPaper) {
        elements.selectedPaper.classList.remove("visible");
        elements.selectedPaper.innerHTML = "";
        return;
      }
      const paper = state.selectedPaper;
      elements.selectedPaper.classList.add("visible");
      elements.selectedPaper.innerHTML = `
        <div class="eyebrow">selected paper</div>
        <h2>${escapeHtml(paper.title || paper.paper_id)}</h2>
        <div class="paper-meta">${escapeHtml(paper.arxiv_id || paper.paper_id)}</div>
        <p class="hero-copy">${escapeHtml(paper.summary_short || paper.abstract || "This paper is indexed and ready for grounded chat.")}</p>
      `;
    }

    function resetChat() {
      elements.chatLog.innerHTML = `
        <div class="message assistant">
          <div class="message-label">assistant</div>
          Ask about the paper's core idea, method, evidence, assumptions, or limitations.
        </div>
      `;
    }

    function appendMessage(role, content, evidence) {
      const container = document.createElement("div");
      container.className = "message " + role;

      const label = document.createElement("div");
      label.className = "message-label";
      label.textContent = role;
      container.appendChild(label);

      const body = document.createElement("div");
      body.textContent = content;
      container.appendChild(body);

      if (role === "assistant" && evidence && evidence.length) {
        const evidenceList = document.createElement("div");
        evidenceList.className = "evidence-list";
        for (const item of evidence) {
          const panel = document.createElement("div");
          panel.className = "evidence-item";
          panel.innerHTML = `
            <strong>${escapeHtml(item.path)}</strong>
            <div class="paper-meta">${escapeHtml(item.excerpt)}</div>
          `;
          evidenceList.appendChild(panel);
        }
        container.appendChild(evidenceList);
      }

      elements.chatLog.appendChild(container);
      elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
    }

    async function searchPapers(query) {
      state.searching = true;
      elements.searchButton.disabled = true;
      setSearchStatus("Searching indexed papers...");
      try {
        const response = await fetch("/v1/papers/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query }),
        });
        if (!response.ok) {
          throw new Error("Search failed");
        }
        const payload = await response.json();
        state.papers = payload.papers || [];
        if (state.papers.length) {
          setSearchStatus("Choose a paper to start chatting.");
        } else {
          setSearchStatus("No indexed papers matched that query.");
        }
        renderPaperList();
      } catch (error) {
        setSearchStatus("The chat app could not reach paperbase.");
      } finally {
        state.searching = false;
        elements.searchButton.disabled = false;
      }
    }

    async function askQuestion(question) {
      if (!state.selectedPaper) {
        return;
      }
      state.asking = true;
      elements.askButton.disabled = true;
      appendMessage("user", question);
      const outgoingHistory = state.history.slice();
      state.history.push({ role: "user", content: question });
      elements.questionInput.value = "";
      try {
        const response = await fetch("/v1/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            paper_id: state.selectedPaper.paper_id,
            question,
            history: outgoingHistory,
          }),
        });
        if (!response.ok) {
          throw new Error("Chat failed");
        }
        const payload = await response.json();
        appendMessage("assistant", payload.answer, payload.evidence || []);
        state.history.push({ role: "assistant", content: payload.answer });
      } catch (error) {
        appendMessage("assistant", "I could not complete that lookup against paperbase.");
      } finally {
        state.asking = false;
        elements.askButton.disabled = false;
      }
    }

    function escapeHtml(value) {
      return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    elements.searchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = elements.paperQuery.value.trim();
      if (!query) {
        return;
      }
      searchPapers(query);
    });

    elements.composer.addEventListener("submit", (event) => {
      event.preventDefault();
      const question = elements.questionInput.value.trim();
      if (!question || state.asking || !state.selectedPaper) {
        return;
      }
      askQuestion(question);
    });
  </script>
</body>
</html>
"""
