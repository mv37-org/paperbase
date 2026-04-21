import React, { createElement as h, useCallback, useEffect, useMemo, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import { createEditor, Editor, Element as SlateElement, Node, Range, Text, Transforms } from "https://esm.sh/slate@0.103.0";
import { Editable, ReactEditor, Slate, withReact } from "https://esm.sh/slate-react@0.103.0";

const MOCK_PAPERS = [
  { id: "paper-1", title: "Self-Reflective Language Agents", venue: "arXiv" },
  { id: "paper-2", title: "AgentBench: Evaluating LLMs as Agents", venue: "ICLR" },
  { id: "paper-3", title: "Voyager: An Open-Ended Embodied Agent", venue: "arXiv" },
  { id: "paper-4", title: "Generative Agents: Interactive Simulacra", venue: "UIST" },
  { id: "paper-5", title: "MemGPT: Towards LLMs as Operating Systems", venue: "arXiv" },
  { id: "paper-6", title: "Reflexion: Language Agents with Verbal Reinforcement Learning", venue: "NeurIPS" },
  { id: "paper-7", title: "A Survey of Large Language Model Based Autonomous Agents", venue: "TMLR" },
  { id: "paper-8", title: "Toolformer: Language Models Can Teach Themselves to Use Tools", venue: "NeurIPS" },
];

const INITIAL_VALUE = [
  {
    type: "paragraph",
    children: [{ text: "" }],
  },
];

function withMentions(editor) {
  const { isInline, isVoid } = editor;
  editor.isInline = (element) => (element.type === "mention" ? true : isInline(element));
  editor.isVoid = (element) => (element.type === "mention" ? true : isVoid(element));
  return editor;
}

function serializeNode(node) {
  if (Text.isText(node)) {
    return node.text;
  }
  if (node.type === "mention") {
    return `@${node.title}`;
  }
  return node.children.map(serializeNode).join("");
}

function serialize(nodes) {
  return nodes.map(serializeNode).join("\n");
}

function extractMentionTags(nodes) {
  const tags = [];
  const seen = new Set();
  for (const [node] of Node.descendants({ children: nodes })) {
    if (!Text.isText(node) && node.type === "mention" && !seen.has(node.paperId)) {
      tags.push({ id: node.paperId, title: node.title });
      seen.add(node.paperId);
    }
  }
  return tags;
}

function mockSearchPapers(query) {
  return new Promise((resolve) => {
    const normalized = query.trim().toLowerCase();
    window.setTimeout(() => {
      resolve(
        MOCK_PAPERS.filter((paper) => paper.title.toLowerCase().includes(normalized)).slice(0, 5),
      );
    }, 140);
  });
}

function insertMention(editor, target, paper) {
  const mention = {
    type: "mention",
    paperId: paper.id,
    title: paper.title,
    children: [{ text: "" }],
  };
  Transforms.select(editor, target);
  Transforms.insertNodes(editor, [mention, { text: " " }]);
  Transforms.move(editor);
}

function MentionElement({ attributes, children, element }) {
  return h(
    "span",
    {
      ...attributes,
      contentEditable: false,
      className: "mention-token",
      "data-paper-id": element.paperId,
    },
    `@${element.title}`,
    children,
  );
}

function DefaultElement({ attributes, children }) {
  return h("p", attributes, children);
}

function renderElement(props) {
  if (props.element.type === "mention") {
    return h(MentionElement, props);
  }
  return h(DefaultElement, props);
}

function LandingMentionEditor() {
  const [editor] = useState(() => withMentions(withReact(createEditor())));
  const [value, setValue] = useState(INITIAL_VALUE);
  const [target, setTarget] = useState(null);
  const [search, setSearch] = useState("");
  const [index, setIndex] = useState(0);
  const [suggestions, setSuggestions] = useState([]);

  const tags = useMemo(() => extractMentionTags(value), [value]);

  const setPlainText = useCallback((text) => {
    const nextValue =
      text
        ? [
            {
              type: "paragraph",
              children: [{ text }],
            },
          ]
        : INITIAL_VALUE;
    editor.children = nextValue;
    editor.selection = {
      anchor: Editor.end(editor, []),
      focus: Editor.end(editor, []),
    };
    editor.onChange();
    setValue(nextValue);
    setTarget(null);
    setSearch("");
    setSuggestions([]);
    setIndex(0);
  }, [editor]);

  useEffect(() => {
    window.PaperbaseLandingEditor = {
      getPlainText: () => serialize(value).trimEnd(),
      setPlainText,
      clear: () => setPlainText(""),
    };
    window.__resolvePaperbaseEditorsReady?.();
    return () => {
      delete window.PaperbaseLandingEditor;
    };
  }, [setPlainText, value]);

  useEffect(() => {
    if (!target || search.length < 2) {
      setSuggestions([]);
      return;
    }
    let stale = false;
    mockSearchPapers(search).then((results) => {
      if (!stale) {
        setSuggestions(results);
        setIndex(0);
      }
    });
    return () => {
      stale = true;
    };
  }, [search, target]);

  const onChange = useCallback(
    (nextValue) => {
      setValue(nextValue);
      const { selection } = editor;
      if (!selection || !Range.isCollapsed(selection)) {
        setTarget(null);
        return;
      }

      const [start] = Range.edges(selection);
      const before = Editor.before(editor, start, { unit: "word" });
      const beforeRange = before && Editor.range(editor, before, start);
      const beforeText = beforeRange ? Editor.string(editor, beforeRange) : "";
      const beforeMatch = beforeText.match(/^@([\w.-]{2,})$/);
      const after = Editor.after(editor, start);
      const afterRange = after && Editor.range(editor, start, after);
      const afterText = afterRange ? Editor.string(editor, afterRange) : "";
      const afterMatch = afterText.match(/^(\s|$)/);

      if (beforeMatch && afterMatch) {
        setTarget(beforeRange);
        setSearch(beforeMatch[1]);
        setIndex(0);
        return;
      }

      setTarget(null);
    },
    [editor],
  );

  const chooseSuggestion = useCallback(
    (paper) => {
      if (!target) {
        return;
      }
      insertMention(editor, target, paper);
      setTarget(null);
      setSuggestions([]);
      setSearch("");
      ReactEditor.focus(editor);
    },
    [editor, target],
  );

  const onKeyDown = useCallback(
    (event) => {
      if (!target || suggestions.length === 0) {
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        setIndex((current) => (current >= suggestions.length - 1 ? 0 : current + 1));
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        setIndex((current) => (current <= 0 ? suggestions.length - 1 : current - 1));
        return;
      }

      if (event.key === "Tab" || event.key === "Enter") {
        event.preventDefault();
        chooseSuggestion(suggestions[index]);
        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        setTarget(null);
      }
    },
    [chooseSuggestion, index, suggestions, target],
  );

  return h(
    "div",
    { className: "mention-editor" },
    h(
      "div",
      { className: "mention-editor-surface" },
      h(
        Slate,
        { editor, initialValue: INITIAL_VALUE, onChange },
        h(Editable, {
          className: "mention-editor-input",
          renderElement,
          onKeyDown,
          placeholder: "Ask about agent loops, eval design, memory systems, or inference architecture. Type @ to tag a paper.",
          spellCheck: false,
          autoFocus: false,
        }),
      ),
      target && suggestions.length > 0
        ? h(
            "div",
            { className: "mention-suggestions" },
            suggestions.map((paper, suggestionIndex) =>
              h(
                "button",
                {
                  key: paper.id,
                  type: "button",
                  className: `mention-suggestion${suggestionIndex === index ? " active" : ""}`,
                  onMouseDown: (event) => {
                    event.preventDefault();
                    chooseSuggestion(paper);
                  },
                },
                h("div", { className: "mention-suggestion-title" }, paper.title),
                h("div", { className: "mention-suggestion-meta" }, `${paper.venue} research paper`),
              ),
            ),
          )
        : null,
    ),
    tags.length > 0
      ? h(
          "div",
          { className: "mention-tags" },
          tags.map((tag) => h("span", { key: tag.id, className: "mention-tag" }, tag.title)),
        )
      : null,
  );
}

const mountNode = document.getElementById("landing-editor-root");
if (mountNode) {
  createRoot(mountNode).render(h(LandingMentionEditor));
} else {
  window.__resolvePaperbaseEditorsReady?.();
}
