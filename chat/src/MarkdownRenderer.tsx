import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeSnippet, flattenCodeChildren } from "./CodeSnippet";

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};

type Props = {
  markdown: string;
  className?: string;
  interactiveCitations?: boolean;
  onCitationClick?: (sourceId: string) => void;
};

const CITATION_PATTERN = /(\[(S\d+)\])/g;
const FENCE_PATTERN = /^(```|~~~)/;
const TABLE_DELIMITER_PATTERN = /^\|?(?:\s*:?-{3,}:?\s*\|)+(?:\s*:?-{3,}:?\s*)\|?$/;

function countPipes(value: string) {
  return (value.match(/\|/g) ?? []).length;
}

function isTableDelimiter(line: string) {
  return TABLE_DELIMITER_PATTERN.test(line.trim());
}

function isTableRow(line: string) {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && countPipes(trimmed) >= 2;
}

function splitLegacyTableHeader(line: string) {
  const firstPipe = line.indexOf("|");
  if (firstPipe <= 0) {
    return null;
  }
  const prefix = line.slice(0, firstPipe).trimEnd();
  const header = line.slice(firstPipe).trim();
  if (!prefix || !isTableRow(header)) {
    return null;
  }
  return { prefix, header };
}

function splitLegacyTrailingTableRow(line: string) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || countPipes(trimmed) < 2) {
    return null;
  }
  const lastPipe = line.lastIndexOf("|");
  if (lastPipe < 0) {
    return null;
  }
  const row = line.slice(0, lastPipe + 1).trim();
  const trailing = line.slice(lastPipe + 1).trim();
  if (!row || !trailing || !isTableRow(row)) {
    return null;
  }
  return { row, trailing };
}

function repairLegacyMarkdown(markdown: string) {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const repaired: string[] = [];
  let inFence = false;
  let inTable = false;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const trimmed = line.trim();
    const next = lines[index + 1] ?? "";
    const nextTrimmed = next.trim();

    if (FENCE_PATTERN.test(trimmed)) {
      inFence = !inFence;
      repaired.push(line);
      continue;
    }

    if (inFence) {
      repaired.push(line);
      continue;
    }

    if (!inTable) {
      const header = splitLegacyTableHeader(line);
      if (header && isTableDelimiter(nextTrimmed)) {
        repaired.push(header.prefix, "", header.header);
        inTable = true;
        continue;
      }

      if (isTableRow(trimmed) && isTableDelimiter(nextTrimmed)) {
        if (repaired.length > 0 && repaired[repaired.length - 1] !== "") {
          repaired.push("");
        }
        repaired.push(trimmed);
        inTable = true;
        continue;
      }

      repaired.push(line);
      continue;
    }

    const trailingRow = splitLegacyTrailingTableRow(line);
    if (trailingRow) {
      repaired.push(trailingRow.row, "", trailingRow.trailing);
      inTable = false;
      continue;
    }

    repaired.push(line);

    if (nextTrimmed === "") {
      inTable = false;
      continue;
    }

    if (
      !isTableRow(nextTrimmed) &&
      !isTableDelimiter(nextTrimmed) &&
      !splitLegacyTrailingTableRow(next)
    ) {
      repaired.push("");
      inTable = false;
    }
  }

  return repaired.join("\n");
}

function citationPlugin() {
  const transformNode = (node: MarkdownNode) => {
    if (!Array.isArray(node.children) || ["link", "linkReference"].includes(node.type)) {
      return;
    }
    const nextChildren: MarkdownNode[] = [];
    for (const child of node.children) {
      if (child.type === "text" && child.value) {
        let lastIndex = 0;
        for (const match of child.value.matchAll(CITATION_PATTERN)) {
          const fullMatch = match[1];
          const sourceId = match[2];
          const matchIndex = match.index ?? 0;
          if (matchIndex > lastIndex) {
            nextChildren.push({ type: "text", value: child.value.slice(lastIndex, matchIndex) });
          }
          nextChildren.push({
            type: "link",
            url: `paperbase-citation:${sourceId}`,
            children: [{ type: "text", value: fullMatch }],
          });
          lastIndex = matchIndex + fullMatch.length;
        }
        if (lastIndex === 0) {
          nextChildren.push(child);
          continue;
        }
        if (lastIndex < child.value.length) {
          nextChildren.push({ type: "text", value: child.value.slice(lastIndex) });
        }
        continue;
      }
      transformNode(child);
      nextChildren.push(child);
    }
    node.children = nextChildren;
  };

  return (tree: MarkdownNode) => {
    transformNode(tree);
  };
}

function flattenMarkdownCodeChildren(children: ReactNode) {
  return flattenCodeChildren(children);
}

export function MarkdownRenderer({
  markdown,
  className,
  interactiveCitations = false,
  onCitationClick,
}: Props) {
  const wrapperClassName = ["markdown-renderer", className].filter(Boolean).join(" ");
  const normalizedMarkdown = repairLegacyMarkdown(markdown);

  return (
    <div className={wrapperClassName}>
      <ReactMarkdown
        remarkPlugins={interactiveCitations ? [remarkGfm, citationPlugin] : [remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("paperbase-citation:")) {
              const sourceId = href.slice("paperbase-citation:".length);
              return (
                <button
                  type="button"
                  className="citation-inline-button"
                  onClick={() => onCitationClick?.(sourceId)}
                >
                  {children}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          table: ({ children }) => (
            <div className="markdown-table-wrap">
              <table>{children}</table>
            </div>
          ),
          pre: ({ children }) => <>{children}</>,
          code: ({ inline, className, children }) => {
            if (inline) {
              return <code className={className}>{children}</code>;
            }
            const languageMatch = /language-([A-Za-z0-9_+-]+)/.exec(className ?? "");
            const code = flattenMarkdownCodeChildren(children).replace(/\n$/, "");
            const lines = code.replace(/\r\n?/g, "\n").split("\n");
            return (
              <CodeSnippet
                code={code}
                language={languageMatch?.[1]}
                className="message-code-snippet"
                collapsible
                defaultCollapsed={lines.length > 12}
              />
            );
          },
        }}
      >
        {normalizedMarkdown}
      </ReactMarkdown>
    </div>
  );
}
