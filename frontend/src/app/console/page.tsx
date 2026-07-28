"use client";

/** Ask — the centrepiece.
 *
 * Three columns: conversation history, the answer stream, and the inspector.
 * The inspector is the point of the whole interface: sources arrive before the
 * first token, and the Grounding Meter shows the retrieval score against the
 * refusal floor, so the model's decision is visible rather than implied.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Answer } from "@/components/Answer";
import { GroundingMeter } from "@/components/GroundingMeter";
import { RetrievalTrace } from "@/components/RetrievalTrace";
import { useSession } from "@/components/Session";
import { SourceList } from "@/components/SourceList";
import { api } from "@/lib/api";
import { askStream, type StreamHandle } from "@/lib/stream";
import type {
  ChatMessage,
  Conversation,
  StreamCitations,
  StreamMeta,
  StreamSource,
  StreamUsage,
  SystemStatus,
} from "@/lib/types";

type Phase = "idle" | "embedding" | "retrieving" | "fusing" | "generating" | "done";

interface Turn {
  id: string;
  question: string;
  answer: string;
  sources: StreamSource[];
  meta: StreamMeta | null;
  usage: StreamUsage | null;
  citations: StreamCitations | null;
  error: string | null;
  streaming: boolean;
  refused: boolean;
  topScore: number | null;
}

const SUGGESTIONS = [
  "Why was Reciprocal Rank Fusion chosen instead of a weighted score blend?",
  "How is tenant isolation enforced?",
  "What happens when retrieval finds nothing relevant?",
  "How does the ingestion pipeline handle a worker crash?",
];

export default function AskPage() {
  const { workspace } = useSession();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const streamRef = useRef<StreamHandle | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const active = turns[turns.length - 1] ?? null;
  const busy = phase !== "idle" && phase !== "done";

  useEffect(() => {
    if (!workspace) return;
    setTurns([]);
    setConversation(null);
    api.conversations(workspace.id).then(setConversations).catch(() => {});
    api.system(workspace.id).then(setSystem).catch(() => {});
  }, [workspace]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, active?.answer]);

  const loadConversation = useCallback(async (conv: Conversation) => {
    setConversation(conv);
    const history: ChatMessage[] = await api.messages(conv.id);
    const restored: Turn[] = [];
    for (let i = 0; i < history.length; i += 1) {
      const message = history[i];
      if (message.role !== "USER") continue;
      const reply = history[i + 1]?.role === "ASSISTANT" ? history[i + 1] : null;
      restored.push({
        id: reply?.id ?? message.id,
        question: message.content,
        answer: reply?.content ?? "",
        sources: (reply?.citations ?? []).map((c) => ({
          marker: c.marker,
          chunk_id: c.chunk_id ?? "",
          document_id: c.document_id ?? "",
          document_title: c.document_title ?? "Source",
          page_label: c.page_label,
          section: null,
          score: c.score ?? 0,
          found_by_both: false,
          snippet: c.snippet ?? "",
        })),
        meta: null,
        usage: reply
          ? {
              input_tokens: reply.prompt_tokens,
              output_tokens: reply.completion_tokens,
              ttft_ms: reply.ttft_ms,
              latency_ms: reply.latency_ms ?? 0,
              model: reply.model ?? "",
            }
          : null,
        citations: null,
        error: null,
        streaming: false,
        refused: reply?.finish_reason === "REFUSED",
        topScore: null,
      });
    }
    setTurns(restored);
    setPhase("done");
  }, []);

  async function ask(question: string) {
    if (!workspace || !question.trim() || busy) return;

    let conv = conversation;
    if (!conv) {
      conv = await api.createConversation(workspace.id);
      setConversation(conv);
      setConversations((prev) => [conv!, ...prev]);
    }

    const turn: Turn = {
      id: crypto.randomUUID(),
      question: question.trim(),
      answer: "",
      sources: [],
      meta: null,
      usage: null,
      citations: null,
      error: null,
      streaming: true,
      refused: false,
      topScore: null,
    };
    setTurns((prev) => [...prev, turn]);
    setInput("");
    setPhase("embedding");

    const patch = (changes: Partial<Turn>) =>
      setTurns((prev) =>
        prev.map((t) => (t.id === turn.id ? { ...t, ...changes } : t)),
      );

    // Phase hints. Retrieval completes before `meta`, so the first two steps are
    // timed rather than reported — the honest alternative would be no feedback
    // at all during the ~100ms before sources land.
    const toRetrieving = setTimeout(() => setPhase("retrieving"), 120);

    streamRef.current = askStream(conv.id, question.trim(), {
      onMeta: (meta) => {
        clearTimeout(toRetrieving);
        setPhase(meta.refused ? "done" : "fusing");
        patch({
          meta,
          sources: meta.sources,
          refused: meta.refused,
          // The gate compares raw cosine, not the fused score, so the meter
          // plots the same number the decision was made on.
          topScore: meta.relevance,
        });
        if (!meta.refused) setTimeout(() => setPhase("generating"), 90);
      },
      onToken: (delta) =>
        setTurns((prev) =>
          prev.map((t) => (t.id === turn.id ? { ...t, answer: t.answer + delta } : t)),
        ),
      onCitations: (citations) => patch({ citations }),
      onUsage: (usage) => patch({ usage }),
      onDone: () => {
        patch({ streaming: false });
        setPhase("done");
      },
      onError: (detail) => {
        patch({ error: detail, streaming: false });
        setPhase("done");
      },
    });
  }

  function stop() {
    streamRef.current?.abort();
    setTurns((prev) =>
      prev.map((t, i) => (i === prev.length - 1 ? { ...t, streaming: false } : t)),
    );
    setPhase("done");
  }

  const ready = workspace ? workspace.ready_document_count > 0 : false;

  return (
    <div className="h-full grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] xl:grid-cols-[220px_minmax(0,1fr)_340px]">
      {/* Conversations */}
      <aside className="hidden xl:flex flex-col border-r hairline border-r-[1px] min-h-0">
        <div className="p-3 border-b hairline border-b-[1px]">
          <button
            className="btn w-full"
            onClick={() => {
              setConversation(null);
              setTurns([]);
              setPhase("idle");
            }}
          >
            New thread
          </button>
        </div>
        <div className="scroll flex-1 p-2 space-y-0.5">
          {conversations.length === 0 && (
            <p className="eyebrow p-2">No threads yet</p>
          )}
          {conversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => loadConversation(conv)}
              className="w-full text-left px-2.5 py-2 rounded-[3px] transition-colors hover:bg-white/[0.03]"
              style={{
                background:
                  conversation?.id === conv.id ? "rgba(255,176,32,0.07)" : undefined,
              }}
            >
              <span className="block text-[12px] truncate">{conv.title}</span>
              <span className="num text-[10px] text-faint">
                {conv.message_count} messages
              </span>
            </button>
          ))}
        </div>
      </aside>

      {/* Stream */}
      <section className="flex flex-col min-h-0 border-r hairline border-r-[1px]">
        <div className="scroll flex-1 px-5 lg:px-8 py-6">
          <div className="max-w-3xl mx-auto">
            {turns.length === 0 && (
              <div className="pt-8">
                <p className="eyebrow mb-3">
                  {workspace?.name ?? "Workspace"} ·{" "}
                  {workspace?.ready_document_count ?? 0} documents indexed
                </p>
                <h1 className="display text-[26px] leading-tight">
                  Ask anything your documents can answer.
                </h1>
                <p className="mt-3 text-[13px] text-muted max-w-lg leading-relaxed">
                  {ready
                    ? "Every claim comes back with a citation you can open. If the corpus doesn't cover it, you get told that instead of a guess."
                    : "Upload a document first — the Documents tab takes PDF, DOCX, PPTX, Markdown or a URL."}
                </p>

                {ready && (
                  <div className="mt-8 grid sm:grid-cols-2 gap-2">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        onClick={() => ask(s)}
                        className="panel text-left p-3 text-[12px] leading-snug text-muted hover:text-paper transition-colors"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="space-y-10">
              {turns.map((turn) => (
                <article key={turn.id} className="rise">
                  <div className="flex gap-3 mb-5">
                    <span className="eyebrow pt-1 shrink-0">You</span>
                    <p className="text-[15px] leading-relaxed">{turn.question}</p>
                  </div>

                  <div className="flex gap-3">
                    <span
                      className="eyebrow pt-1 shrink-0"
                      style={{
                        color: turn.refused
                          ? "var(--color-refused)"
                          : "var(--color-signal)",
                      }}
                    >
                      {turn.refused ? "No answer" : "KOS"}
                    </span>

                    <div className="min-w-0 flex-1">
                      {turn.error ? (
                        <p
                          className="text-[13px]"
                          style={{ color: "var(--color-refused)" }}
                        >
                          {turn.error}
                        </p>
                      ) : turn.answer ? (
                        <Answer
                          text={turn.answer}
                          sources={turn.sources}
                          streaming={turn.streaming}
                          onCite={(marker) => {
                            setHighlighted(marker);
                            document
                              .getElementById(`source-${marker}`)
                              ?.scrollIntoView({ behavior: "smooth", block: "center" });
                          }}
                        />
                      ) : (
                        <span className="eyebrow pulse">Retrieving</span>
                      )}

                      {!turn.streaming && turn.usage && !turn.refused && (
                        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1">
                          <Metric
                            label="first token"
                            value={turn.usage.ttft_ms ? `${turn.usage.ttft_ms}ms` : "—"}
                          />
                          <Metric label="total" value={`${turn.usage.latency_ms}ms`} />
                          <Metric
                            label="tokens"
                            value={`${turn.usage.input_tokens}→${turn.usage.output_tokens}`}
                          />
                          {turn.citations && (
                            <Metric
                              label="cited"
                              value={`${turn.citations.validated.length}`}
                              tone="verified"
                            />
                          )}
                          {turn.citations && turn.citations.stripped.length > 0 && (
                            <Metric
                              label="stripped"
                              value={`${turn.citations.stripped.length}`}
                              tone="refused"
                            />
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Composer */}
        <div className="shrink-0 border-t hairline border-t-[1px] p-4">
          <form
            className="max-w-3xl mx-auto flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
          >
            <input
              className="field font-sans"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                ready ? "Ask a question about your documents…" : "Upload a document to begin"
              }
              disabled={busy || !ready}
            />
            {busy ? (
              <button type="button" className="btn shrink-0" onClick={stop}>
                Stop
              </button>
            ) : (
              <button className="btn btn-primary shrink-0" disabled={!input.trim() || !ready}>
                Ask
              </button>
            )}
          </form>
        </div>
      </section>

      {/* Inspector */}
      <aside className="hidden lg:flex flex-col min-h-0">
        <div className="scroll flex-1 p-4 space-y-4">
          <RetrievalTrace
            phase={phase}
            retrievalMs={active?.meta?.retrieval_ms}
            contextTokens={active?.meta?.context_tokens}
          />

          {active && active.topScore !== null && (
            <div className="panel p-3">
              <GroundingMeter
                score={active.topScore}
                floor={active.meta?.floor ?? system?.relevance_floor ?? 0.58}
                refused={active.refused}
              />
            </div>
          )}

          {active?.refused && (
            <div
              className="panel p-3 border-l-2"
              style={{ borderLeftColor: "var(--color-refused)" }}
            >
              <p className="eyebrow mb-1.5" style={{ color: "var(--color-refused)" }}>
                Refusal gate fired
              </p>
              <p className="text-[11px] leading-relaxed text-muted">
                Nothing in this workspace cleared the confidence floor, so no
                request was sent to the model at all. A model given no context
                still answers — from memory — and that is where confident wrong
                answers come from.
              </p>
            </div>
          )}

          <div>
            <p className="eyebrow mb-2">
              Sources {active?.sources.length ? `· ${active.sources.length}` : ""}
            </p>
            <SourceList
              sources={active?.sources ?? []}
              highlighted={highlighted}
              onHighlight={setHighlighted}
            />
          </div>
        </div>

        {system && (
          <div className="shrink-0 border-t hairline border-t-[1px] p-3 space-y-1">
            <Row label="Model" value={system.chat_model} />
            <Row label="Embeddings" value={`${system.embedding_model.split("/").pop()}`} />
            <Row label="Vectors" value={system.vector_count.toLocaleString()} />
            {system.llm_provider === "scripted" && (
              <p
                className="text-[10px] leading-snug pt-1"
                style={{ color: "var(--color-signal)" }}
              >
                Offline provider: answers are quoted verbatim from sources, not
                generated. Set LLM_PROVIDER=anthropic for prose.
              </p>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "verified" | "refused";
}) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="eyebrow !text-[8px]">{label}</span>
      <span
        className="num text-[11px]"
        style={{ color: tone ? `var(--color-${tone})` : "var(--color-muted)" }}
      >
        {value}
      </span>
    </span>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="eyebrow !text-[8px]">{label}</span>
      <span className="num text-[10px] text-muted truncate">{value}</span>
    </div>
  );
}
