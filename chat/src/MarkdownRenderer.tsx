import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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

export function MarkdownRenderer({
  markdown,
  className,
  interactiveCitations = false,
  onCitationClick,
}: Props) {
  return (
    <ReactMarkdown
      className={className}
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
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
