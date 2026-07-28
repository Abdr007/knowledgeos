"use client";

/** Session and workspace context.
 *
 * The access token lives in module memory (lib/api), so a page reload has no
 * token — `resume()` exchanges the httpOnly refresh cookie for a new one on
 * mount. That is what makes a refresh feel seamless without ever putting a
 * credential somewhere a script could read it.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api } from "@/lib/api";
import type { Me, Workspace } from "@/lib/types";

interface SessionValue {
  me: Me | null;
  workspaces: Workspace[];
  workspace: Workspace | null;
  setWorkspaceId: (id: string) => void;
  refreshWorkspaces: () => Promise<void>;
  signOut: () => Promise<void>;
  loading: boolean;
}

const Ctx = createContext<SessionValue | null>(null);

export function useSession() {
  const value = useContext(Ctx);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}

export function SessionProvider({
  children,
  onUnauthenticated,
}: {
  children: React.ReactNode;
  onUnauthenticated: () => void;
}) {
  const [me, setMe] = useState<Me | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshWorkspaces = useCallback(async () => {
    const list = await api.workspaces();
    setWorkspaces(list);
    setWorkspaceId((current) => current ?? list[0]?.id ?? null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const session = await api.resume();
      if (cancelled) return;
      if (!session) {
        setLoading(false);
        onUnauthenticated();
        return;
      }
      setMe(session);
      try {
        await refreshWorkspaces();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signOut = useCallback(async () => {
    await api.logout();
    setMe(null);
    onUnauthenticated();
  }, [onUnauthenticated]);

  const value = useMemo<SessionValue>(
    () => ({
      me,
      workspaces,
      workspace: workspaces.find((w) => w.id === workspaceId) ?? null,
      setWorkspaceId,
      refreshWorkspaces,
      signOut,
      loading,
    }),
    [me, workspaces, workspaceId, refreshWorkspaces, signOut, loading],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
