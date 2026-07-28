"use client";

/** The console shell: navigation rail, workspace switcher, session status. */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback } from "react";
import { SessionProvider, useSession } from "@/components/Session";

const NAV = [
  { href: "/console", label: "Ask", key: "ask" },
  { href: "/console/documents", label: "Documents", key: "documents" },
  { href: "/console/search", label: "Search", key: "search" },
  { href: "/console/analytics", label: "Analytics", key: "analytics" },
];

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const onUnauthenticated = useCallback(() => router.replace("/"), [router]);

  return (
    <SessionProvider onUnauthenticated={onUnauthenticated}>
      <Shell>{children}</Shell>
    </SessionProvider>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { me, workspaces, workspace, setWorkspaceId, signOut, loading } = useSession();
  const pathname = usePathname();

  if (loading) {
    return (
      <div className="relative z-10 h-screen grid place-items-center">
        <span className="eyebrow pulse">Loading workspace</span>
      </div>
    );
  }

  return (
    <div className="relative z-10 h-screen flex flex-col">
      <header className="shrink-0 h-12 border-b hairline border-b-[1px] flex items-center gap-4 px-4">
        <Link href="/console" className="flex items-center gap-2.5 shrink-0">
          <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden>
            <rect x="1" y="3" width="16" height="2.5" rx="1" fill="var(--color-signal)" />
            <rect x="1" y="7.75" width="11" height="2.5" rx="1" fill="var(--color-dense)" />
            <rect x="1" y="12.5" width="6" height="2.5" rx="1" fill="var(--color-sparse)" />
          </svg>
          <span className="display text-[13px] hidden sm:inline">KnowledgeOS</span>
        </Link>

        <nav className="flex items-center gap-0.5 ml-2">
          {NAV.map((item) => {
            const active =
              item.href === "/console"
                ? pathname === "/console"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.key}
                href={item.href}
                className="px-2.5 py-1 rounded-[3px] text-[11px] font-mono uppercase tracking-[0.08em] transition-colors"
                style={{
                  color: active ? "var(--color-signal)" : "var(--color-muted)",
                  background: active ? "rgba(255,176,32,0.08)" : "transparent",
                }}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex-1" />

        {workspaces.length > 0 && (
          <label className="flex items-center gap-2">
            <span className="eyebrow hidden md:inline">Workspace</span>
            <select
              className="field !w-auto !py-1 !text-[12px] font-mono"
              value={workspace?.id ?? ""}
              onChange={(e) => setWorkspaceId(e.target.value)}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id} style={{ background: "#11161f" }}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="flex items-center gap-3 pl-3 border-l hairline border-l-[1px]">
          <span className="text-[11px] text-muted hidden lg:inline truncate max-w-[16ch]">
            {me?.user.email}
          </span>
          <button
            className="eyebrow hover:text-paper transition-colors"
            onClick={signOut}
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="flex-1 min-h-0">{children}</main>
    </div>
  );
}
