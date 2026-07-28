"use client";

/** API client.
 *
 * The access token lives in module memory, never in localStorage — a script that
 * can read storage can exfiltrate the session (TDD §18). The refresh token is an
 * httpOnly cookie the browser handles and JavaScript cannot touch.
 *
 * A 401 triggers exactly one refresh attempt, and concurrent 401s share it: five
 * parallel requests must not each rotate the refresh token, because rotation is
 * single-use and the losers would trip reuse detection and revoke the family.
 */

import type {
  ChatMessage,
  Conversation,
  Doc,
  DocLeaderboardEntry,
  Me,
  Overview,
  QualityMetrics,
  SearchResponse,
  SystemStatus,
  UsagePoint,
  Workspace,
} from "./types";

/** Upper bound on session restore. Generous enough for a free instance waking
 *  from cold, short enough that a user is never left staring at a blank page. */
const REFRESH_TIMEOUT_MS = 20_000;

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export function setToken(token: string | null) {
  accessToken = token;
}
export function getToken() {
  return accessToken;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function refresh(): Promise<boolean> {
  // Shared promise: concurrent callers await the same rotation.
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        // Bounded. A hanging request must never be able to wedge the caller —
        // the landing page awaits this before it can decide what to render, and
        // a promise that never settles leaves a blank screen with no recourse.
        // A cold backend takes seconds; anything past this is not coming back.
        const res = await fetch("/api/v1/auth/refresh", {
          method: "POST",
          signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
        });
        if (!res.ok) return false;
        const body = await res.json();
        accessToken = body.access_token;
        return true;
      } catch {
        return false;
      } finally {
        // Cleared on the next tick so late arrivals still see this result.
        setTimeout(() => (refreshInFlight = null), 0);
      }
    })();
  }
  return refreshInFlight;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api/v1${path}`, { ...init, headers });

  if (res.status === 401 && retry) {
    if (await refresh()) return request<T>(path, init, false);
  }

  if (!res.ok) {
    let code = "error";
    let detail = res.statusText;
    try {
      const body = await res.json();
      code = body.error ?? code;
      detail = body.detail ?? detail;
      if (Array.isArray(detail)) {
        detail = detail.map((d: { msg?: string }) => d.msg ?? "invalid").join("; ");
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  // ── auth ──────────────────────────────────────────────────────────────
  async register(email: string, password: string, fullName: string, org?: string) {
    const body = await request<{ access_token: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        full_name: fullName,
        organization_name: org || null,
      }),
    });
    accessToken = body.access_token;
    return body;
  },

  async login(email: string, password: string) {
    const body = await request<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    accessToken = body.access_token;
    return body;
  },

  async logout() {
    try {
      await request("/auth/logout", { method: "POST" });
    } finally {
      accessToken = null;
    }
  },

  /** Restore a session from the refresh cookie on a cold page load. */
  async resume(): Promise<Me | null> {
    if (!accessToken && !(await refresh())) return null;
    try {
      return await request<Me>("/auth/me");
    } catch {
      return null;
    }
  },

  me: () => request<Me>("/auth/me"),

  // ── workspaces ────────────────────────────────────────────────────────
  workspaces: () => request<Workspace[]>("/workspaces"),
  workspace: (id: string) => request<Workspace>(`/workspaces/${id}`),
  createWorkspace: (orgId: string, name: string, description?: string) =>
    request<Workspace>(`/organizations/${orgId}/workspaces`, {
      method: "POST",
      body: JSON.stringify({ name, description: description ?? null }),
    }),

  // ── documents ─────────────────────────────────────────────────────────
  documents: (wsId: string) => request<Doc[]>(`/workspaces/${wsId}/documents`),
  document: (id: string) => request<Doc>(`/documents/${id}`),

  upload: (wsId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ document: Doc; duplicate: boolean }>(
      `/workspaces/${wsId}/documents`,
      { method: "POST", body: form },
    );
  },

  ingestUrl: (wsId: string, url: string) =>
    request<{ document: Doc; duplicate: boolean }>(
      `/workspaces/${wsId}/documents/url`,
      { method: "POST", body: JSON.stringify({ url, title: null }) },
    ),

  deleteDocument: (id: string) =>
    request<{ detail: string }>(`/documents/${id}`, { method: "DELETE" }),

  chunks: (id: string) =>
    request<
      {
        id: string;
        ordinal: number;
        content: string;
        token_count: number;
        page_from: number | null;
        page_to: number | null;
        section: string | null;
      }[]
    >(`/documents/${id}/chunks`),

  // ── search ────────────────────────────────────────────────────────────
  search: (wsId: string, query: string, topK?: number) =>
    request<SearchResponse>(`/workspaces/${wsId}/search`, {
      method: "POST",
      body: JSON.stringify({ query, top_k: topK ?? null, document_ids: null }),
    }),

  // ── chat ──────────────────────────────────────────────────────────────
  conversations: (wsId: string) =>
    request<Conversation[]>(`/workspaces/${wsId}/conversations`),
  createConversation: (wsId: string) =>
    request<Conversation>(`/workspaces/${wsId}/conversations`, {
      method: "POST",
      body: JSON.stringify({ title: null }),
    }),
  messages: (convId: string) =>
    request<ChatMessage[]>(`/conversations/${convId}/messages`),
  deleteConversation: (convId: string) =>
    request<{ detail: string }>(`/conversations/${convId}`, { method: "DELETE" }),
  feedback: (messageId: string, rating: number, comment?: string) =>
    request<{ detail: string }>(`/messages/${messageId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating, comment: comment ?? null }),
    }),

  // ── analytics ─────────────────────────────────────────────────────────
  overview: (wsId: string) =>
    request<Overview>(`/workspaces/${wsId}/analytics/overview`),
  quality: (wsId: string) =>
    request<QualityMetrics>(`/workspaces/${wsId}/analytics/quality`),
  usage: (wsId: string, days = 14) =>
    request<UsagePoint[]>(`/workspaces/${wsId}/analytics/usage?days=${days}`),
  docLeaderboard: (wsId: string) =>
    request<DocLeaderboardEntry[]>(`/workspaces/${wsId}/analytics/documents`),
  system: (wsId: string) =>
    request<SystemStatus>(`/workspaces/${wsId}/admin/system`),
};

/** Ensure a fresh access token before opening a stream that cannot retry. */
export async function ensureFreshToken(): Promise<string | null> {
  if (!accessToken) await refresh();
  return accessToken;
}
