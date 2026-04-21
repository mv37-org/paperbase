import { useCallback, useEffect, useMemo, useState } from "react";
import type { ComponentProps, KeyboardEvent } from "react";
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
  value: string;
  onChange: (value: string) => void;
};

export function LandingMentionEditor({ value: plainText, onChange }: Props) {
  const [editor] = useState(() => withMentions(withReact(createEditor())));
  const [value, setValue] = useState<Descendant[]>(plainTextToValue(plainText));
  const [target, setTarget] = useState<BaseRange | null>(null);
  const [search, setSearch] = useState("");
  const [index, setIndex] = useState(0);
  const [suggestions, setSuggestions] = useState<(typeof MOCK_PAPERS)>([]);

  const tags = useMemo(() => extractMentionTags(value), [value]);

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
      const before = Editor.before(editor, start, { unit: "word" });
      const beforeRange = before && Editor.range(editor, before, start);
      const beforeText = beforeRange ? Editor.string(editor, beforeRange) : "";
      const beforeMatch = beforeText.match(/^@([\w.-]{2,})$/);
      const after = Editor.after(editor, start);
      const afterRange = after && Editor.range(editor, start, after);
      const afterText = afterRange ? Editor.string(editor, afterRange) : "";
      const afterMatch = afterText.match(/^(\s|$)/);

      if (beforeMatch && afterMatch && beforeRange) {
        setTarget(beforeRange);
        setSearch(beforeMatch[1]);
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
    return (
      <span {...props.attributes} className="mention-editor-placeholder">
        {props.children}
      </span>
    );
  }, []);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
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

  return (
    <div className="mention-editor">
      <div className="mention-editor-surface">
        <Slate editor={editor} initialValue={INITIAL_VALUE} onChange={handleChange}>
          <Editable
            className="mention-editor-input"
            renderElement={renderElement}
            renderPlaceholder={renderPlaceholder}
            onKeyDown={onKeyDown}
            placeholder="Ask about agent loops, eval design, memory systems, or inference architecture. Type @ to tag a paper."
            spellCheck={false}
            autoFocus={false}
          />
        </Slate>
        {target && suggestions.length > 0 ? (
          <div className="mention-suggestions">
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
          </div>
        ) : null}
      </div>
      {tags.length > 0 ? (
        <div className="mention-tags">
          {tags.map((tag) => (
            <span key={tag.id} className="mention-tag">
              {tag.title}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
