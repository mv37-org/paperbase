import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ComponentProps, CSSProperties, FormEvent, KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { createEditor, Editor, Element as SlateElement, Node, Range, Text, Transforms } from "slate";
import type { BaseRange, Descendant } from "slate";
import { Editable, ReactEditor, Slate, withReact } from "slate-react";

import type { MentionTag } from "./types";

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

type MentionElement = {
  type: "mention";
  paperId: string;
  title: string;
  children: EmptyText[];
};

type ParagraphElement = {
  type: "paragraph";
  children: CustomText[];
};

type EmptyText = {
  text: "";
};

type CustomText = {
  text: string;
};

declare module "slate" {
  interface CustomTypes {
    Editor: Editor & ReactEditor;
    Element: MentionElement | ParagraphElement;
    Text: CustomText | EmptyText;
  }
}

function createInitialValue(): Descendant[] {
  return [
    {
      type: "paragraph",
      children: [{ text: "" }],
    },
  ];
}

function withMentions(editor: Editor & ReactEditor) {
  const { isInline, isVoid } = editor;
  editor.isInline = (element) => (SlateElement.isElement(element) && element.type === "mention" ? true : isInline(element));
  editor.isVoid = (element) => (SlateElement.isElement(element) && element.type === "mention" ? true : isVoid(element));
  return editor;
}

function serializeNode(node: Node): string {
  if (Text.isText(node)) {
    return node.text;
  }
  if (SlateElement.isElement(node) && node.type === "mention") {
    return `@${node.title}`;
  }
  return node.children.map(serializeNode).join("");
}

function serialize(nodes: Descendant[]) {
  return nodes.map((node) => serializeNode(node)).join("\n");
}

function extractMentionTags(nodes: Descendant[]): MentionTag[] {
  const tags: MentionTag[] = [];
  const seen = new Set<string>();

  const visit = (currentNodes: Descendant[]) => {
    for (const node of currentNodes) {
      if (SlateElement.isElement(node) && node.type === "mention" && !seen.has(node.paperId)) {
        tags.push({ id: node.paperId, title: node.title });
        seen.add(node.paperId);
      }
      if (SlateElement.isElement(node)) {
        visit(node.children as Descendant[]);
      }
    }
  };

  visit(nodes);
  return tags;
}

function mockSearchPapers(query: string) {
  return new Promise<typeof MOCK_PAPERS>((resolve) => {
    const normalized = query.trim().toLowerCase();
    window.setTimeout(() => {
      resolve(MOCK_PAPERS.filter((paper) => paper.title.toLowerCase().includes(normalized)).slice(0, 5));
    }, 140);
  });
}

function plainTextToValue(text: string): Descendant[] {
  if (!text.trim()) {
    return createInitialValue();
  }
  return [
    {
      type: "paragraph",
      children: [{ text }],
    },
  ];
}

function insertMention(editor: Editor & ReactEditor, target: BaseRange, paper: (typeof MOCK_PAPERS)[number]) {
  const mention: MentionElement = {
    type: "mention",
    paperId: paper.id,
    title: paper.title,
    children: [{ text: "" }],
  };
  Transforms.select(editor, target);
  Transforms.insertNodes(editor, [mention, { text: " " }]);
  Transforms.move(editor);
}

type Props = {
  attachButtonId?: string;
  disabled?: boolean;
  formId?: string;
  inputId?: string;
  onAttach: () => void;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  submitOnEnter?: boolean;
  submitButtonId?: string;
  submitLabel?: string;
  submitLoading?: boolean;
  suggestionsPlacement?: "above" | "below";
  variant?: "landing" | "chat";
};

export function LandingMentionEditor({
  attachButtonId,
  disabled = false,
  formId,
  inputId,
  onAttach,
  value: plainText,
  onChange,
  onSubmit,
  placeholder = "Ask about agents, evals, or inference. Type @ to tag a paper.",
  submitOnEnter = false,
  submitButtonId,
  submitLabel = "Start",
  submitLoading = false,
  suggestionsPlacement = "below",
  variant = "landing",
}: Props) {
  const [editor] = useState(() => withMentions(withReact(createEditor())));
  const [value, setValue] = useState<Descendant[]>(plainTextToValue(plainText));
  const [target, setTarget] = useState<BaseRange | null>(null);
  const [search, setSearch] = useState("");
  const [index, setIndex] = useState(0);
  const [suggestions, setSuggestions] = useState<(typeof MOCK_PAPERS)>([]);
  const [popoverStyle, setPopoverStyle] = useState<CSSProperties | null>(null);
  const shellRef = useRef<HTMLFormElement | null>(null);

  const tags = useMemo(() => extractMentionTags(value), [value]);
  const showSuggestions = Boolean(target && suggestions.length > 0);
  const composerClassName = variant === "chat" ? "chat-composer" : "landing-composer";
  const shellClassName = variant === "chat" ? "prompt-shell composer-form chat-prompt-shell" : "prompt-shell landing-prompt-shell";

  useEffect(() => {
    const nextPlainText = serialize(value).trimEnd();
    if (plainText !== nextPlainText) {
      const nextValue = plainTextToValue(plainText);
      editor.children = nextValue;
      editor.selection = {
        anchor: Editor.end(editor, []),
        focus: Editor.end(editor, []),
      };
      editor.onChange();
      setValue(nextValue);
    }
  }, [editor, plainText, value]);

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

  const handleChange = useCallback(
    (nextValue: Descendant[]) => {
      setValue(nextValue);
      onChange(serialize(nextValue).trimEnd());
      const { selection } = editor;
      if (!selection || !Range.isCollapsed(selection)) {
        setTarget(null);
        return;
      }

      const [start] = Range.edges(selection);
      const blockEntry = Editor.above(editor, {
        at: start,
        match: (node) => SlateElement.isElement(node) && Editor.isBlock(editor, node),
      });
      const blockStart = blockEntry ? Editor.start(editor, blockEntry[1]) : null;
      const beforeRange = blockStart ? Editor.range(editor, blockStart, start) : null;
      const beforeText = beforeRange ? Editor.string(editor, beforeRange) : "";
      const beforeMatch = beforeText.match(/(^|\s)@([\w.-]{2,})$/);
      const after = Editor.after(editor, start);
      const afterRange = after && Editor.range(editor, start, after);
      const afterText = afterRange ? Editor.string(editor, afterRange) : "";
      const afterMatch = afterText.match(/^(\s|$)/);

      if (beforeMatch && afterMatch) {
        const query = beforeMatch[2];
        const mentionStart = Editor.before(editor, start, { unit: "character", distance: query.length + 1 });
        const mentionRange = mentionStart ? Editor.range(editor, mentionStart, start) : null;
        if (!mentionRange) {
          setTarget(null);
          return;
        }
        setTarget(mentionRange);
        setSearch(query);
        setIndex(0);
        return;
      }

      setTarget(null);
    },
    [editor, onChange],
  );

  const chooseSuggestion = useCallback(
    (paper: (typeof MOCK_PAPERS)[number]) => {
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

  const renderElement = useCallback((props: Parameters<NonNullable<ComponentProps<typeof Editable>["renderElement"]>>[0]) => {
    if (SlateElement.isElement(props.element) && props.element.type === "mention") {
      return (
        <span
          {...props.attributes}
          contentEditable={false}
          className="mention-token"
          data-paper-id={props.element.paperId}
        >
          @{props.element.title}
          {props.children}
        </span>
      );
    }

    return <p {...props.attributes}>{props.children}</p>;
  }, []);

  const renderPlaceholder = useCallback((props: Parameters<NonNullable<ComponentProps<typeof Editable>["renderPlaceholder"]>>[0]) => {
    const attributes =
      variant === "chat"
        ? {
            ...props.attributes,
            style: {
              ...props.attributes.style,
              top: 6,
            },
          }
        : props.attributes;

    return (
      <span {...attributes} className="mention-editor-placeholder">
        {props.children}
      </span>
    );
  }, [variant]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (!target || suggestions.length === 0) {
        if (event.key === "Enter" && event.shiftKey && !disabled) {
          event.preventDefault();
          Editor.insertText(editor, "\n");
          return;
        }
        if (submitOnEnter && event.key === "Enter" && !event.shiftKey && !disabled && !event.nativeEvent.isComposing) {
          event.preventDefault();
          onSubmit();
        }
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
    [chooseSuggestion, disabled, editor, index, onSubmit, submitOnEnter, suggestions, target],
  );

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      onSubmit();
    },
    [onSubmit],
  );

  const updatePopoverPosition = useCallback(() => {
    if (!shellRef.current) {
      setPopoverStyle(null);
      return;
    }
    const rect = shellRef.current.getBoundingClientRect();
    const gutter = 16;
    const width = Math.min(rect.width, window.innerWidth - gutter * 2);
    const left = Math.min(Math.max(rect.left, gutter), window.innerWidth - gutter - width);
    setPopoverStyle({
      position: "fixed",
      left,
      width,
      zIndex: 30,
      ...(suggestionsPlacement === "above"
        ? { bottom: window.innerHeight - rect.top + 4 }
        : { top: rect.bottom + 4 }),
    });
  }, [suggestionsPlacement]);

  useLayoutEffect(() => {
    if (!showSuggestions) {
      setPopoverStyle(null);
      return;
    }
    updatePopoverPosition();
    const handleViewportChange = () => updatePopoverPosition();
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            updatePopoverPosition();
          })
        : null;
    if (observer && shellRef.current) {
      observer.observe(shellRef.current);
    }
    return () => {
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
      observer?.disconnect();
    };
  }, [showSuggestions, updatePopoverPosition]);

  return (
    <div className={composerClassName}>
      {tags.length > 0 ? (
        <div className="mention-tags">
          {tags.map((tag) => (
            <span key={tag.id} className="mention-tag">
              {tag.title}
            </span>
          ))}
        </div>
      ) : null}
      <form ref={shellRef} id={formId} className={shellClassName} onSubmit={handleSubmit}>
        {variant === "landing" ? (
          <button
            type="button"
            id={attachButtonId}
            className="prompt-clip"
            aria-label="Attach document"
            onClick={onAttach}
            disabled={disabled}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M16.5 6.5 9 14a3 3 0 1 0 4.24 4.24l7.01-7.02a5 5 0 0 0-7.07-7.07L5.46 11.9a7 7 0 1 0 9.9 9.9l6.01-6.01" />
            </svg>
          </button>
        ) : null}
        <div className="landing-editor-root">
          <div className="mention-editor">
            <Slate editor={editor} initialValue={value} onChange={handleChange}>
              <Editable
                id={inputId}
                className="mention-editor-input"
                renderElement={renderElement}
                renderPlaceholder={renderPlaceholder}
                onKeyDown={onKeyDown}
                placeholder={placeholder}
                spellCheck={false}
                autoFocus={false}
                readOnly={disabled}
              />
            </Slate>
          </div>
        </div>
        {variant === "chat" ? (
          <div className="chat-prompt-actions">
            <button
              type="button"
              id={attachButtonId}
              className="prompt-clip"
              aria-label="Attach document"
              onClick={onAttach}
              disabled={disabled}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M16.5 6.5 9 14a3 3 0 1 0 4.24 4.24l7.01-7.02a5 5 0 0 0-7.07-7.07L5.46 11.9a7 7 0 1 0 9.9 9.9l6.01-6.01" />
              </svg>
            </button>
            <button
              id={submitButtonId}
              className={`prompt-button${submitLoading ? " loading" : ""}`}
              type="submit"
              disabled={disabled}
              aria-busy={submitLoading}
              title={submitOnEnter ? "Enter to send. Shift + Enter for new line." : undefined}
            >
              {submitLoading ? <span className="prompt-button-spinner" aria-hidden="true" /> : null}
              <span className="prompt-button-label">{submitLabel}</span>
              {submitOnEnter && !submitLoading ? (
                <span className="prompt-button-shortcut prompt-button-shortcut-enter" aria-hidden="true">
                  <svg viewBox="0 0 16 16">
                    <path d="M12.5 3.5v6H4.5" />
                    <path d="m7.5 6.5-3 3 3 3" />
                  </svg>
                </span>
              ) : null}
            </button>
          </div>
        ) : (
          <button id={submitButtonId} className="prompt-button" type="submit" disabled={disabled}>
            {submitLabel}
          </button>
        )}
      </form>
      {showSuggestions && popoverStyle && typeof document !== "undefined"
        ? createPortal(
            <div className="mention-suggestions mention-suggestions-popover" style={popoverStyle}>
              {suggestions.map((paper, suggestionIndex) => (
                <button
                  key={paper.id}
                  type="button"
                  className={`mention-suggestion${suggestionIndex === index ? " active" : ""}`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    chooseSuggestion(paper);
                  }}
                >
                  <div className="mention-suggestion-title">{paper.title}</div>
                  <div className="mention-suggestion-meta">{paper.venue} research paper</div>
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
