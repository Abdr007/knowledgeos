"use client";

/** The source inspector.
 *
 * Sources arrive in the SSE `meta` frame BEFORE the first token, so this panel
 * fills while the answer is still being written. The user sees what the answer
 * will be based on as it forms — which makes retrieval legible instead of magic.
 */

import type { StreamSource } from "@/lib/types";

interface Props {
  sources: StreamSource[];
  highlighted?: number | null;
  onHighlight?: (marker: number | null) => void;
}

export function SourceList({ sources, highlighted, onHighlight }: Props) {
  if (!sources.length) {
    return (
      <p className="text-[11px] text-faint leading-relaxed">
        Sources appear here the moment retrieval completes — before the answer
        starts writing.
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {sources.map((source, i) => {
        const active = highlighted === source.marker;
        return (
          <li
            key={source.chunk_id}
            id={`source-${source.marker}`}
            className="panel p-2.5 rise cursor-default transition-colors"
            style={{
              animationDelay: `${i * 45}ms`,
              borderColor: active ? "var(--color-signal)" : undefined,
            }}
            onMouseEnter={() => onHighlight?.(source.marker)}
            onMouseLeave={() => onHighlight?.(null)}
          >
            <div className="flex items-start gap-2">
              <span className="cite shrink-0 !cursor-default">{source.marker}</span>
              <div className="min-w-0 flex-1">
                <p className="text-[12px] font-medium truncate" title={source.document_title}>
                  {source.document_title}
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  {source.page_label && (
                    <span className="num text-[10px] text-faint">p{source.page_label}</span>
                  )}
                  {source.section && (
                    <span className="text-[10px] text-faint truncate">{source.section}</span>
                  )}
                </div>
              </div>
              <span className="num text-[10px] text-muted shrink-0">
                {source.score.toFixed(2)}
              </span>
            </div>

            <p className="mt-1.5 text-[11px] leading-relaxed text-muted line-clamp-3">
              {source.snippet}
            </p>

            {/* Which retriever found it — violet dense, cyan sparse. */}
            <div className="mt-1.5 flex items-center gap-1.5">
              {source.found_by_both ? (
                <>
                  <Channel colour="var(--color-dense)" label="vector" />
                  <Channel colour="var(--color-sparse)" label="keyword" />
                </>
              ) : (
                <Channel colour="var(--color-dense)" label="vector" />
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Channel({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className="inline-block w-1 h-1 rounded-full"
        style={{ background: colour }}
      />
      <span className="eyebrow !text-[8px]" style={{ color: colour }}>
        {label}
      </span>
    </span>
  );
}
