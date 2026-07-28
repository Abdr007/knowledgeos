"use client";

/** Live view of the two retrievers firing and fusing.
 *
 * The dense and sparse halves run concurrently (TDD §3.2) and most products hide
 * that entirely. Showing it is the point: violet is the vector retriever, cyan is
 * the lexical one, and a chunk found by both carries both marks. That is a legend
 * you can read at a glance instead of a decorative animation.
 */

import { useEffect, useState } from "react";

type Phase = "idle" | "embedding" | "retrieving" | "fusing" | "generating" | "done";

interface Props {
  phase: Phase;
  denseCount?: number;
  sparseCount?: number;
  retrievalMs?: number;
  contextTokens?: number;
}

const STEPS: { key: Phase; label: string }[] = [
  { key: "embedding", label: "Embed query" },
  { key: "retrieving", label: "Dense + sparse" },
  { key: "fusing", label: "RRF fuse" },
  { key: "generating", label: "Generate" },
];

const ORDER: Phase[] = ["idle", "embedding", "retrieving", "fusing", "generating", "done"];

export function RetrievalTrace({ phase, retrievalMs, contextTokens }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (phase === "idle" || phase === "done") return;
    const started = performance.now();
    const id = setInterval(() => setElapsed(performance.now() - started), 60);
    return () => clearInterval(id);
  }, [phase]);

  if (phase === "idle") return null;

  const currentIndex = ORDER.indexOf(phase);

  return (
    <div className="panel px-3 py-2.5 rise">
      <div className="flex items-center justify-between mb-2">
        <span className="eyebrow">Pipeline</span>
        <span className="num text-[10px] text-faint">
          {phase === "done" && retrievalMs !== undefined
            ? `retrieval ${retrievalMs}ms`
            : `${(elapsed / 1000).toFixed(1)}s`}
        </span>
      </div>

      <div className="flex items-center gap-1.5">
        {STEPS.map((step, i) => {
          const stepIndex = ORDER.indexOf(step.key);
          const state =
            phase === "done" || stepIndex < currentIndex
              ? "complete"
              : stepIndex === currentIndex
                ? "active"
                : "pending";
          return (
            <div key={step.key} className="flex items-center gap-1.5 flex-1 min-w-0">
              <div className="flex-1 min-w-0">
                <div
                  className={`h-[2px] rounded-full ${state === "active" ? "pulse" : ""}`}
                  style={{
                    background:
                      state === "pending"
                        ? "var(--color-rule)"
                        : i === 1
                          ? "linear-gradient(90deg, var(--color-dense), var(--color-sparse))"
                          : "var(--color-signal)",
                  }}
                />
                <span
                  className="eyebrow !text-[8px] mt-1 block truncate"
                  style={{
                    color:
                      state === "pending" ? "var(--color-faint)" : "var(--color-muted)",
                  }}
                >
                  {step.label}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {contextTokens !== undefined && phase === "done" && (
        <div className="mt-2 pt-2 border-t hairline border-t-[1px] flex justify-between">
          <span className="eyebrow !text-[8px]">Context</span>
          <span className="num text-[10px] text-muted">
            {contextTokens.toLocaleString()} tokens
          </span>
        </div>
      )}
    </div>
  );
}
