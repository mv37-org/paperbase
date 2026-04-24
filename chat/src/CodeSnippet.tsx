import { useMemo, useState } from "react";

export type StreamingCodePreview = {
  language: "python";
  code: string;
};

type CodeSnippetProps = {
  code: string;
  language?: string;
  className?: string;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  headerTitle?: string;
  headerMeta?: string;
  dataTestId?: string;
};

const PYTHON_FENCE_OPEN_RE = /^(```|~~~)\s*([A-Za-z0-9_+-]*)\s*$/;
const PYTHON_KEYWORD_TOKEN_RE =
  /\b(?:and|as|assert|async|await|break|case|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|match|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\b/;
const PYTHON_TOKEN_RE =
  /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|#.*$|@\w+|\b(?:and|as|assert|async|await|break|case|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|match|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g;

function looksLikePythonCode(code: string) {
  return /\b(?:def|class|import|from|return|torch|nn\.Module|if __name__)\b/.test(code);
}

function normalizeCodeLanguage(value: string | undefined, code: string) {
  const normalized = (value ?? "").trim().toLowerCase();
  if (normalized === "py" || normalized === "python") {
    return "python";
  }
  if (!normalized && looksLikePythonCode(code)) {
    return "python";
  }
  return normalized || "text";
}

function classifyPythonToken(token: string) {
  if (token.startsWith("#")) {
    return "comment";
  }
  if (token.startsWith("@")) {
    return "decorator";
  }
  if (token.startsWith('"') || token.startsWith("'")) {
    return "string";
  }
  if (/^\d/.test(token)) {
    return "number";
  }
  if (PYTHON_KEYWORD_TOKEN_RE.test(token)) {
    return "keyword";
  }
  return "";
}

function highlightPythonLine(line: string) {
  const tokens: Array<{ text: string; className: string }> = [];
  let lastIndex = 0;
  let match = PYTHON_TOKEN_RE.exec(line);

  while (match) {
    const token = match[0];
    const tokenIndex = match.index;
    if (tokenIndex > lastIndex) {
      tokens.push({ text: line.slice(lastIndex, tokenIndex), className: "" });
    }
    tokens.push({ text: token, className: classifyPythonToken(token) });
    lastIndex = tokenIndex + token.length;
    if (token.startsWith("#")) {
      break;
    }
    match = PYTHON_TOKEN_RE.exec(line);
  }

  if (lastIndex < line.length) {
    tokens.push({ text: line.slice(lastIndex), className: "" });
  }
  if (tokens.length === 0) {
    tokens.push({ text: line || " ", className: "" });
  }
  PYTHON_TOKEN_RE.lastIndex = 0;
  return tokens;
}

function highlightCodeLine(line: string, language: string) {
  if (language === "python") {
    return highlightPythonLine(line);
  }
  return [{ text: line || " ", className: "" }];
}

export function extractStreamingPythonPreview(markdown: string): StreamingCodePreview | null {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  let fence: string | null = null;
  let language = "";
  let buffer: string[] = [];
  let lastBlock: { language: string; code: string } | null = null;

  for (const line of lines) {
    if (!fence) {
      const openMatch = line.match(PYTHON_FENCE_OPEN_RE);
      if (openMatch) {
        fence = openMatch[1];
        language = (openMatch[2] ?? "").trim();
        buffer = [];
      }
      continue;
    }

    if (line.trim() === fence) {
      lastBlock = { language, code: buffer.join("\n") };
      fence = null;
      language = "";
      buffer = [];
      continue;
    }

    buffer.push(line);
  }

  if (fence) {
    lastBlock = { language, code: buffer.join("\n") };
  }
  if (!lastBlock) {
    return null;
  }

  const code = lastBlock.code.replace(/\s+$/, "");
  if (!code.trim()) {
    return null;
  }

  return normalizeCodeLanguage(lastBlock.language, code) === "python"
    ? { language: "python", code }
    : null;
}

export function flattenCodeChildren(children: unknown): string {
  if (typeof children === "string") {
    return children;
  }
  if (Array.isArray(children)) {
    return children.map((child) => flattenCodeChildren(child)).join("");
  }
  if (
    children &&
    typeof children === "object" &&
    "props" in children &&
    children.props &&
    typeof children.props === "object" &&
    "children" in children.props
  ) {
    return flattenCodeChildren(children.props.children);
  }
  return "";
}

export function CodeSnippet({
  code,
  language,
  className = "",
  collapsible = false,
  defaultCollapsed = false,
  headerTitle,
  headerMeta,
  dataTestId,
}: CodeSnippetProps) {
  const normalizedCode = code.replace(/\n$/, "");
  const resolvedLanguage = normalizeCodeLanguage(language, normalizedCode);
  const lines = useMemo(() => normalizedCode.replace(/\r\n?/g, "\n").split("\n"), [normalizedCode]);
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const title = headerTitle ?? resolvedLanguage;
  const meta = headerMeta ?? `${lines.length} line${lines.length === 1 ? "" : "s"}`;
  const shellClassName = ["code-snippet-shell", className].filter(Boolean).join(" ");

  return (
    <div className={shellClassName} data-testid={dataTestId}>
      <div className="code-snippet-header">
        <div className="code-snippet-header-copy">
          <strong className="code-snippet-title">{title}</strong>
          <span className="code-snippet-meta">{meta}</span>
        </div>
        {collapsible ? (
          <button
            type="button"
            className="code-snippet-toggle"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((current) => !current)}
          >
            {collapsed ? "Show code" : "Hide code"}
          </button>
        ) : null}
      </div>
      {!collapsed ? (
        <pre className="code-snippet-body">
          <code>
            {lines.map((line, index) => (
              <span className="code-snippet-line" key={`${index}:${line}`}>
                <span className="code-snippet-line-number">{index + 1}</span>
                <span className="code-snippet-line-content">
                  {highlightCodeLine(line, resolvedLanguage).map((token, tokenIndex) => (
                    <span
                      key={`${index}:${tokenIndex}:${token.text}`}
                      className={
                        token.className
                          ? `code-snippet-token ${token.className}`
                          : "code-snippet-token"
                      }
                    >
                      {token.text}
                    </span>
                  ))}
                </span>
                {index < lines.length - 1 ? "\n" : null}
              </span>
            ))}
          </code>
        </pre>
      ) : null}
    </div>
  );
}
