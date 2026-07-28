"use client";

/** Analytics — cost, latency and answer quality.
 *
 * The quality panel is the one that matters and the one most products omit.
 * Refusal rate and the bottom-decile groundedness are the signals that tell you
 * whether the system is being honest; a mean hides exactly the answers you care
 * about (TDD §26.3).
 */

import { useEffect, useState } from "react";
import { useSession } from "@/components/Session";
import { api } from "@/lib/api";
import type {
  DocLeaderboardEntry,
  Overview,
  QualityMetrics,
  SystemStatus,
  UsagePoint,
} from "@/lib/types";

export default function AnalyticsPage() {
  const { workspace } = useSession();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [quality, setQuality] = useState<QualityMetrics | null>(null);
  const [usage, setUsage] = useState<UsagePoint[]>([]);
  const [docs, setDocs] = useState<DocLeaderboardEntry[]>([]);
  const [system, setSystem] = useState<SystemStatus | null>(null);

  useEffect(() => {
    if (!workspace) return;
    const id = workspace.id;
    Promise.allSettled([
      api.overview(id).then(setOverview),
      api.quality(id).then(setQuality),
      api.usage(id).then(setUsage),
      api.docLeaderboard(id).then(setDocs),
      api.system(id).then(setSystem),
    ]);
  }, [workspace]);

  const peak = Math.max(1, ...usage.map((u) => u.input_tokens + u.output_tokens));

  return (
    <div className="h-full scroll">
      <div className="max-w-5xl mx-auto px-5 lg:px-8 py-8">
        <p className="eyebrow mb-2">{workspace?.name}</p>
        <h1 className="display text-[22px]">Analytics</h1>

        {/* Headline */}
        <div className="mt-6 grid grid-cols-2 lg:grid-cols-4 gap-px bg-rule border hairline border-[1px] rounded-[4px] overflow-hidden">
          <Tile label="Documents" value={overview?.documents_ready ?? "—"} sub={`${overview?.chunks?.toLocaleString() ?? 0} chunks`} />
          <Tile label="Answers" value={overview?.answered ?? "—"} sub={`${overview?.conversations ?? 0} threads`} />
          <Tile
            label="First token p50"
            value={overview?.ttft_p50_ms ? `${overview.ttft_p50_ms}ms` : "—"}
            sub={overview?.latency_p95_ms ? `p95 total ${overview.latency_p95_ms}ms` : ""}
          />
          <Tile
            label="Cost"
            value={overview ? `$${overview.total_cost_usd.toFixed(4)}` : "—"}
            sub={overview ? `$${overview.cost_per_answer_usd.toFixed(5)}/answer` : ""}
          />
        </div>

        <div className="mt-6 grid lg:grid-cols-2 gap-4">
          {/* Honesty */}
          <section className="panel p-4">
            <p className="eyebrow mb-3">Honesty</p>

            <Bar
              label="Refusal rate"
              value={overview?.refusal_rate ?? 0}
              display={`${((overview?.refusal_rate ?? 0) * 100).toFixed(1)}%`}
              colour="var(--color-signal)"
            />
            <p className="text-[11px] leading-relaxed text-muted mt-2 mb-4">
              How often the corpus did not cover the question and the system said
              so. A rate of exactly zero usually means the gate is broken, not
              that the answers are perfect.
            </p>

            <Bar
              label="Groundedness p50"
              value={quality?.groundedness_p50 ?? 0}
              display={(quality?.groundedness_p50 ?? 0).toFixed(3)}
              colour="var(--color-verified)"
            />
            <Bar
              label="Groundedness p10"
              value={quality?.groundedness_p10 ?? 0}
              display={(quality?.groundedness_p10 ?? 0).toFixed(3)}
              colour="var(--color-dense)"
            />
            <p className="text-[11px] leading-relaxed text-muted mt-2">
              The bottom decile is where fabrication would show up first. Tracking
              the mean alone hides precisely the answers worth looking at.
            </p>

            <dl className="mt-4 pt-3 border-t hairline border-t-[1px] grid grid-cols-2 gap-y-1.5">
              <Row label="Citations" value={quality?.total_citations ?? 0} />
              <Row label="Per answer" value={quality?.citations_per_answer ?? 0} />
              <Row label="Feedback" value={quality?.feedback_count ?? 0} />
              <Row
                label="Positive"
                value={
                  quality?.satisfaction_rate !== null && quality?.satisfaction_rate !== undefined
                    ? `${(quality.satisfaction_rate * 100).toFixed(0)}%`
                    : "—"
                }
              />
            </dl>
          </section>

          {/* Usage */}
          <section className="panel p-4">
            <p className="eyebrow mb-3">Token usage · 14 days</p>
            {usage.length === 0 ? (
              <p className="text-[11px] text-faint">No usage recorded yet.</p>
            ) : (
              <div className="flex items-end gap-[3px] h-32">
                {usage.map((point) => {
                  const total = point.input_tokens + point.output_tokens;
                  const inputPct = (point.input_tokens / peak) * 100;
                  const outputPct = (point.output_tokens / peak) * 100;
                  return (
                    <div
                      key={point.date}
                      className="flex-1 flex flex-col justify-end gap-px"
                      title={`${point.date}: ${total.toLocaleString()} tokens · $${point.cost_usd.toFixed(5)}`}
                    >
                      <div
                        style={{
                          height: `${outputPct}%`,
                          background: "var(--color-signal)",
                        }}
                      />
                      <div
                        style={{
                          height: `${inputPct}%`,
                          background: "var(--color-dense)",
                        }}
                      />
                    </div>
                  );
                })}
              </div>
            )}
            <div className="flex gap-3 mt-3">
              <Legend colour="var(--color-dense)" label="input" />
              <Legend colour="var(--color-signal)" label="output" />
            </div>

            <dl className="mt-4 pt-3 border-t hairline border-t-[1px] grid grid-cols-2 gap-y-1.5">
              <Row label="Input tokens" value={(overview?.input_tokens ?? 0).toLocaleString()} />
              <Row label="Output tokens" value={(overview?.output_tokens ?? 0).toLocaleString()} />
            </dl>
          </section>
        </div>

        {/* Which documents earn their place */}
        <section className="mt-4 panel p-4">
          <p className="eyebrow mb-1">Most-cited documents</p>
          <p className="text-[11px] text-muted mb-3">
            Citations are a real table, so this is a GROUP BY rather than a guess —
            and it answers the question every knowledge base eventually raises:
            which of these is actually earning its place.
          </p>
          {docs.length === 0 ? (
            <p className="text-[11px] text-faint">No citations yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {docs.map((doc) => {
                const max = Math.max(1, ...docs.map((d) => d.citations));
                return (
                  <li key={doc.document_id} className="flex items-center gap-3">
                    <span className="text-[12px] truncate flex-1">{doc.title}</span>
                    <div className="w-32 h-[3px] bg-rule rounded-full overflow-hidden shrink-0">
                      <div
                        className="h-full sweep"
                        style={{
                          width: `${(doc.citations / max) * 100}%`,
                          background: "var(--color-signal)",
                        }}
                      />
                    </div>
                    <span className="num text-[11px] w-8 text-right shrink-0">
                      {doc.citations}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Configuration */}
        {system && (
          <section className="mt-4 panel p-4">
            <p className="eyebrow mb-3">Runtime configuration</p>
            <dl className="grid sm:grid-cols-3 gap-y-1.5 gap-x-6">
              <Row label="Environment" value={system.environment} />
              <Row label="LLM provider" value={system.llm_provider} />
              <Row label="Chat model" value={system.chat_model} />
              <Row label="Embeddings" value={system.embedding_model} />
              <Row label="Dimensions" value={system.embedding_dimensions} />
              <Row label="Vectors" value={system.vector_count.toLocaleString()} />
              <Row label="Refusal floor" value={system.relevance_floor} />
              <Row label="Top-k" value={system.retrieval_top_k} />
              <Row
                label="Chunk / overlap"
                value={`${system.chunk_size_chars} / ${system.chunk_overlap_chars}`}
              />
              <Row label="Queue pending" value={system.queue_pending} />
              <Row label="Queue running" value={system.queue_processing} />
              <Row label="Key configured" value={system.llm_configured ? "yes" : "no"} />
            </dl>
          </section>
        )}
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="bg-ink-panel p-4">
      <p className="eyebrow !text-[8px]">{label}</p>
      <p className="num text-[22px] mt-1 leading-none">{value}</p>
      {sub && <p className="text-[10px] text-faint mt-1.5">{sub}</p>}
    </div>
  );
}

function Bar({
  label,
  value,
  display,
  colour,
}: {
  label: string;
  value: number;
  display: string;
  colour: string;
}) {
  return (
    <div className="mb-2">
      <div className="flex items-baseline justify-between mb-1">
        <span className="eyebrow !text-[8px]">{label}</span>
        <span className="num text-[11px]" style={{ color: colour }}>
          {display}
        </span>
      </div>
      <div className="h-[3px] bg-rule rounded-full overflow-hidden">
        <div
          className="h-full sweep"
          style={{ width: `${Math.min(100, value * 100)}%`, background: colour }}
        />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="eyebrow !text-[8px]">{label}</dt>
      <dd className="num text-[11px] text-muted truncate">{value}</dd>
    </div>
  );
}

function Legend({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-2 h-2 rounded-[1px]" style={{ background: colour }} />
      <span className="eyebrow !text-[8px]">{label}</span>
    </span>
  );
}
