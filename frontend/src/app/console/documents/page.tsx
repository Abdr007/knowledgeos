"use client";

/** Documents — upload, watch the pipeline, inspect what retrieval sees.
 *
 * Ingestion is asynchronous, so status is polled while anything is in flight.
 * Showing PENDING → PROCESSING → READY honestly is better than a spinner that
 * implies the work is instant.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "@/components/Session";
import { api, ApiError } from "@/lib/api";
import type { Doc, DocumentStatus } from "@/lib/types";

const STATUS_TONE: Record<DocumentStatus, string> = {
  PENDING: "var(--color-muted)",
  PROCESSING: "var(--color-signal)",
  READY: "var(--color-verified)",
  FAILED: "var(--color-refused)",
  QUARANTINED: "var(--color-refused)",
  DELETED: "var(--color-faint)",
};

export default function DocumentsPage() {
  const { workspace, refreshWorkspaces } = useSession();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [dragging, setDragging] = useState(false);
  const [inspecting, setInspecting] = useState<Doc | null>(null);
  const [chunks, setChunks] = useState<
    { id: string; ordinal: number; content: string; page_from: number | null; section: string | null }[]
  >([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!workspace) return;
    try {
      setDocs(await api.documents(workspace.id));
    } catch {
      /* transient */
    }
  }, [workspace]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll only while something is actually in flight — a dashboard that polls
  // forever is a self-inflicted load problem.
  useEffect(() => {
    const inFlight = docs.some((d) => d.status === "PENDING" || d.status === "PROCESSING");
    if (!inFlight) return;
    const id = setInterval(() => {
      load();
      refreshWorkspaces().catch(() => {});
    }, 1500);
    return () => clearInterval(id);
  }, [docs, load, refreshWorkspaces]);

  async function upload(files: FileList | File[]) {
    if (!workspace) return;
    setBusy(true);
    setError(null);
    try {
      for (const file of Array.from(files)) {
        const result = await api.upload(workspace.id, file);
        if (result.duplicate) {
          setError(`"${file.name}" is already in this workspace — nothing re-ingested.`);
        }
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function ingestUrl(e: React.FormEvent) {
    e.preventDefault();
    if (!workspace || !url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.ingestUrl(workspace.id, url.trim());
      setUrl("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not fetch that URL.");
    } finally {
      setBusy(false);
    }
  }

  async function inspect(doc: Doc) {
    setInspecting(doc);
    setChunks(await api.chunks(doc.id));
  }

  async function remove(doc: Doc) {
    if (!confirm(`Delete "${doc.title}"? Its chunks and vectors go with it.`)) return;
    await api.deleteDocument(doc.id);
    if (inspecting?.id === doc.id) setInspecting(null);
    await load();
    await refreshWorkspaces();
  }

  return (
    <div className="h-full grid lg:grid-cols-[minmax(0,1fr)_400px]">
      <section className="scroll p-5 lg:p-8">
        <div className="max-w-3xl">
          <p className="eyebrow mb-2">Corpus</p>
          <h1 className="display text-[22px]">Documents</h1>
          <p className="mt-2 text-[13px] text-muted max-w-lg leading-relaxed">
            Parsed, chunked with overlap, embedded locally, and indexed. Page
            numbers travel with every chunk so citations can point at them.
          </p>

          {/* Drop zone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
            }}
            className="mt-6 panel p-8 text-center transition-colors"
            style={{
              borderStyle: "dashed",
              borderColor: dragging ? "var(--color-signal)" : undefined,
              background: dragging ? "rgba(255,176,32,0.04)" : undefined,
            }}
          >
            <p className="text-[13px]">
              Drop files here, or{" "}
              <button
                className="underline underline-offset-2"
                style={{ color: "var(--color-signal)" }}
                onClick={() => fileRef.current?.click()}
                disabled={busy}
              >
                browse
              </button>
            </p>
            <p className="eyebrow mt-2">PDF · DOCX · PPTX · Markdown · TXT · up to 50 MB</p>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              accept=".pdf,.docx,.pptx,.md,.txt"
              onChange={(e) => e.target.files && upload(e.target.files)}
            />
          </div>

          <form onSubmit={ingestUrl} className="mt-3 flex gap-2">
            <input
              className="field"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="…or paste a URL to ingest a web page"
              type="url"
            />
            <button className="btn shrink-0" disabled={busy || !url.trim()}>
              Fetch
            </button>
          </form>

          {error && (
            <p className="mt-3 text-[12px]" style={{ color: "var(--color-signal)" }}>
              {error}
            </p>
          )}

          {/* Table */}
          <div className="mt-8">
            <div className="flex items-baseline justify-between mb-3">
              <p className="eyebrow">{docs.length} documents</p>
              <p className="num text-[10px] text-faint">
                {docs.reduce((n, d) => n + d.chunk_count, 0).toLocaleString()} chunks indexed
              </p>
            </div>

            {docs.length === 0 ? (
              <p className="text-[12px] text-faint">
                Nothing here yet. The first upload takes a moment longer while the
                embedding model loads.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {docs.map((doc) => (
                  <li key={doc.id} className="panel p-3 flex items-center gap-3">
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{
                        background: STATUS_TONE[doc.status],
                        animation:
                          doc.status === "PROCESSING"
                            ? "pulse-dot 1.1s ease-in-out infinite"
                            : undefined,
                      }}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] truncate">{doc.title}</p>
                      <div className="flex items-center gap-2.5 mt-0.5">
                        <span
                          className="eyebrow !text-[8px]"
                          style={{ color: STATUS_TONE[doc.status] }}
                        >
                          {doc.status}
                        </span>
                        {doc.status === "READY" && (
                          <span className="num text-[10px] text-faint">
                            {doc.page_count ?? 0}p · {doc.chunk_count} chunks ·{" "}
                            {doc.token_count.toLocaleString()} tokens
                          </span>
                        )}
                        {doc.status === "FAILED" && doc.error_message && (
                          <span
                            className="text-[10px] truncate"
                            style={{ color: "var(--color-refused)" }}
                            title={doc.error_message}
                          >
                            {doc.error_message}
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      className="eyebrow hover:text-paper transition-colors shrink-0"
                      onClick={() => inspect(doc)}
                      disabled={doc.status !== "READY"}
                    >
                      Inspect
                    </button>
                    <button
                      className="eyebrow hover:text-paper transition-colors shrink-0"
                      onClick={() => remove(doc)}
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      {/* Chunk inspector */}
      <aside className="hidden lg:flex flex-col min-h-0 border-l hairline border-l-[1px]">
        <div className="p-4 border-b hairline border-b-[1px]">
          <p className="eyebrow">What retrieval sees</p>
          <p className="text-[12px] mt-1 truncate">
            {inspecting ? inspecting.title : "Select a document"}
          </p>
        </div>
        <div className="scroll flex-1 p-3 space-y-2">
          {!inspecting && (
            <p className="text-[11px] text-faint leading-relaxed">
              The fastest way to debug a wrong answer is to read the text the
              model was actually given — not the prompt that requested it.
            </p>
          )}
          {chunks.map((chunk) => (
            <div key={chunk.id} className="panel p-2.5">
              <div className="flex items-baseline justify-between mb-1">
                <span className="num text-[10px] text-faint">#{chunk.ordinal}</span>
                {chunk.page_from && (
                  <span className="num text-[10px] text-faint">p{chunk.page_from}</span>
                )}
              </div>
              {chunk.section && (
                <p className="eyebrow !text-[8px] mb-1 truncate">{chunk.section}</p>
              )}
              <p className="text-[11px] leading-relaxed text-muted whitespace-pre-wrap">
                {chunk.content}
              </p>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
