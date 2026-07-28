/** Runtime API proxy.
 *
 * Replaces a `rewrites()` entry, which does not work for this. Next evaluates
 * `next.config.ts` at BUILD time and serialises the result into a standalone
 * bundle, so `process.env.BACKEND_ORIGIN` is read while the image is being
 * built — when it is unset — and the fallback is frozen in. The container then
 * proxies to whatever the developer's machine used, forever. Passing it as a
 * build arg would "fix" it by baking an environment into the image, which
 * breaks promoting one artifact from staging to production (TDD §19).
 *
 * A Route Handler reads the environment on every request, so the same image
 * runs anywhere.
 *
 * Why proxy at all: the refresh token is an httpOnly SameSite=Lax cookie.
 * Talking to the backend on another origin means the browser never sends it.
 * Routing everything through this origin makes the cookie same-site and removes
 * the need for CORS credentials entirely.
 */

import type { NextRequest } from "next/server";

const BACKEND = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8730";

// Never prerender or cache: every call is user-specific and many are streams.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Hop-by-hop headers must not be forwarded; `host` must be rewritten. */
const STRIP_REQUEST = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  // Let undici negotiate its own encoding with the backend; forwarding the
  // browser's Accept-Encoding can yield a compressed body we then pass through
  // with the wrong Content-Length.
  "accept-encoding",
  "content-length",
]);

const STRIP_RESPONSE = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding",
  "content-length",
]);

async function proxy(request: NextRequest): Promise<Response> {
  const incoming = new URL(request.url);
  const target = `${BACKEND}${incoming.pathname}${incoming.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIP_REQUEST.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = !["GET", "HEAD"].includes(request.method);

  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      // Streamed rather than buffered, so a 50 MB upload is not held in memory
      // here. `duplex: "half"` is required by fetch for a streaming body.
      body: hasBody ? request.body : undefined,
      // @ts-expect-error — `duplex` is standard in undici, not yet in lib.dom
      duplex: "half",
      redirect: "manual",
      // No timeout: chat responses are Server-Sent Event streams that stay open
      // for the length of the answer.
      signal: request.signal,
    });
  } catch (error) {
    const detail =
      error instanceof Error && error.name === "AbortError"
        ? "Request cancelled."
        : "The API is unreachable.";
    return Response.json(
      { error: "dependency_unavailable", detail },
      { status: 502 },
    );
  }

  const outgoing = new Headers();
  response.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE.has(key.toLowerCase())) outgoing.set(key, value);
  });
  // Headers.forEach folds duplicates; Set-Cookie must survive as separate
  // entries or a multi-cookie response collapses into one malformed header.
  const cookies = response.headers.getSetCookie?.() ?? [];
  if (cookies.length) {
    outgoing.delete("set-cookie");
    for (const cookie of cookies) outgoing.append("set-cookie", cookie);
  }
  // Belt and braces for SSE: no buffering anywhere between backend and browser.
  if (outgoing.get("content-type")?.includes("text/event-stream")) {
    outgoing.set("Cache-Control", "no-cache, no-transform");
    outgoing.set("X-Accel-Buffering", "no");
  }

  // response.body is passed straight through, so tokens reach the client as
  // they arrive rather than after the stream completes.
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: outgoing,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
