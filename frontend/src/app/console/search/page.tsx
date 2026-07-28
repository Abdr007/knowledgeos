"use client";

/** Search — retrieval with no model in the loop.
 *
 * This is the debug surface for the whole RAG system. Each hit shows which
 * retriever found it and at what rank, so "why did this chunk win" is answerable
 * by looking rather than guessing. Most products hide this; exposing it is the
 * difference between a demo and an instrument.
 */

import { useState } from "react";
import { useSession } from "@/components/Session";
import { api } from "@/lib/api";
import type { SearchResponse } from "@/lib/types";

export default function SearchPage() {
  const { workspace } = useSession();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!workspace || !query.trim()) return;
    setBusy(true);
    try {
      setResult(await api.search(workspace.id, query.trim(), 12));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full scroll">
      <div className="max-w-4xl mx-auto px-5 lg:px-8 py-8">
        <p className="eyebrow mb-2">Retrieval only · no generation</p>
        <h1 className="display text-[22px]">Hybrid search</h1>
        <p className="mt-2 text-[13px] text-muted max-w-xl leading-relaxed">
          A vector search and a keyword search run concurrently, then fuse by
          reciprocal rank. Each result below shows which half found it — because
          the two find genuinely different things.
        </p>

        <form onSubmit={run} className="mt-6 flex gap-2">
          <input
            className="field font-sans"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the corpus…"
          />
          <button className="btn btn-primary shrink-0" disabled={busy || !query.trim()}>
            {busy ? "…" : "Search"}
          </button>
        </form>

        {result && (
          <>
            <div className="mt-6 grid grid-cols-2 sm:grid-cols-4 gap-px bg-rule border hairline border-[1px] rounded-[4px] overflow-hidden">
              <Stat label="Vector" value={result.dense_candidates} tone="dense" />
              <Stat label="Keyword" value={result.sparse_candidates} tone="sparse" />
              <Stat label="After fusion" value={result.fused_candidates} />
              <Stat label="Latency" value={`${result.took_ms}ms`} />
            </div>

            <ol className="mt-6 space-y-2">
              {result.hits.map((hit, i) => (
                <li
                  key={hit.chunk_id}
                  className="panel p-3.5 rise"
                  style={{ animationDelay: `${i * 35}ms` }}
                >
                  <div className="flex items-start gap-3">
                    <span className="num text-[11px] text-faint pt-0.5 w-5 shrink-0">
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <span className="text-[12.5px] font-medium">
                          {hit.document_title}
                        </span>
                        {hit.page_label && (
                          <span className="num text-[10px] text-faint">
                            p{hit.page_label}
                          </span>
                        )}
                        {hit.section && (
                          <span className="text-[10px] text-faint truncate">
                            {hit.section}
                          </span>
                        )}
                      </div>

                      <p className="mt-1.5 text-[12px] leading-relaxed text-muted">
                        {hit.content.length > 420
                          ? `${hit.content.slice(0, 420)}…`
                          : hit.content}
                      </p>

                      <div className="mt-2 flex items-center gap-3 flex-wrap">
                        {hit.dense_rank !== null && (
                          <Rank
                            colour="var(--color-dense)"
                            label="vector"
                            rank={hit.dense_rank}
                          />
                        )}
                        {hit.sparse_rank !== null && (
                          <Rank
                            colour="var(--color-sparse)"
                            label="keyword"
                            rank={hit.sparse_rank}
                          />
                        )}
                        {hit.found_by_both && (
                          <span
                            className="eyebrow !text-[8px]"
                            style={{ color: "var(--color-verified)" }}
                          >
                            agreed by both
                          </span>
                        )}
                      </div>
                    </div>

                    <span className="num text-[11px] shrink-0" title="Fused RRF score">
                      {hit.score.toFixed(3)}
                    </span>
                  </div>
                </li>
              ))}
            </ol>

            {result.hits.length === 0 && (
              <p className="mt-6 text-[12px] text-faint">
                Nothing matched. Either the corpus doesn&apos;t cover this, or the
                wording is far from the documents&apos; own language.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "dense" | "sparse";
}) {
  return (
    <div className="bg-ink-panel p-3">
      <p className="eyebrow !text-[8px]" style={{ color: tone ? `var(--color-${tone})` : undefined }}>
        {label}
      </p>
      <p className="num text-[17px] mt-0.5">{value}</p>
    </div>
  );
}

function Rank({ colour, label, rank }: { colour: string; label: string; rank: number }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-1 h-1 rounded-full" style={{ background: colour }} />
      <span className="eyebrow !text-[8px]" style={{ color: colour }}>
        {label} #{rank}
      </span>
    </span>
  );
}
