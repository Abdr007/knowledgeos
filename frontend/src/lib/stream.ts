"use client";

/** SSE client for the chat stream (TDD §15).
 *
 * Uses fetch + a ReadableStream reader rather than `EventSource`, because
 * EventSource is GET-only and cannot send an Authorization header — the question
 * would have to travel in the URL, where it hits length limits and lands in every
 * proxy access log (§8). The trade is that reconnection and frame parsing are
 * ours to implement; both are below.
 */

import { ensureFreshToken } from "./api";
import type { StreamCitations, StreamMeta, StreamUsage } from "./types";

export interface StreamHandlers {
  onMeta?: (meta: StreamMeta) => void;
  onToken?: (delta: string) => void;
  onCitations?: (c: StreamCitations) => void;
  onUsage?: (u: StreamUsage) => void;
  onDone?: (finishReason: string) => void;
  onError?: (detail: string) => void;
}

export interface StreamHandle {
  /** Cooperative cancel — closes the response, which the server sees as a
   *  disconnect and uses to stop billing tokens for an abandoned answer. */
  abort: () => void;
  done: Promise<void>;
}

export function askStream(
  conversationId: string,
  content: string,
  handlers: StreamHandlers,
): StreamHandle {
  const controller = new AbortController();

  const done = (async () => {
    const token = await ensureFreshToken();

    let res: Response;
    try {
      res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError?.("Could not reach the server.");
      }
      return;
    }

    if (!res.ok || !res.body) {
      let detail = `Request failed (${res.status}).`;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
      } catch {
        /* non-JSON */
      }
      handlers.onError?.(detail);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { done: finished, value } = await reader.read();
        if (finished) break;

        buffer += decoder.decode(value, { stream: true });

        // Frames are separated by a blank line. Anything after the last "\n\n"
        // is a partial frame and stays in the buffer — a token can and does
        // arrive split across TCP reads.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          if (!frame.trim() || frame.startsWith(":")) continue; // heartbeat

          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7).trim();
            else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
          }
          if (!dataLines.length) continue;

          let payload: unknown;
          try {
            payload = JSON.parse(dataLines.join("\n"));
          } catch {
            continue;
          }

          switch (event) {
            case "meta":
              handlers.onMeta?.(payload as StreamMeta);
              break;
            case "token":
              handlers.onToken?.((payload as { delta: string }).delta);
              break;
            case "citations":
              handlers.onCitations?.(payload as StreamCitations);
              break;
            case "usage":
              handlers.onUsage?.(payload as StreamUsage);
              break;
            case "done":
              handlers.onDone?.((payload as { finish_reason: string }).finish_reason);
              break;
            case "error":
              handlers.onError?.((payload as { detail: string }).detail);
              break;
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError?.("The stream ended unexpectedly.");
      }
    } finally {
      reader.releaseLock();
    }
  })();

  return { abort: () => controller.abort(), done };
}
