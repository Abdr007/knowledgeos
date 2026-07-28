"use client";

/** Entry: sign in, or create an account and land in a working workspace. */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

export default function Gate() {
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "create">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [org, setOrg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(true);
  const emailRef = useRef<HTMLInputElement>(null);

  // An existing refresh cookie means the session can be resumed silently.
  useEffect(() => {
    (async () => {
      const session = await api.resume();
      if (session) router.replace("/console");
      else setChecking(false);
    })();
  }, [router]);

  useEffect(() => {
    if (!checking) emailRef.current?.focus();
  }, [checking]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signin") await api.login(email, password);
      else await api.register(email, password, fullName, org);
      router.replace("/console");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Something went wrong. Try again.",
      );
      setBusy(false);
    }
  }

  if (checking) {
    return (
      <main className="relative z-10 h-screen grid place-items-center">
        <span className="eyebrow pulse">Restoring session</span>
      </main>
    );
  }

  return (
    <main className="relative z-10 h-screen overflow-auto scroll grid lg:grid-cols-[1.15fr_1fr]">
      {/* Left: the thesis. What this system does that a chatbot does not. */}
      <section className="hidden lg:flex flex-col justify-between p-12 border-r hairline border-r-[1px]">
        <div>
          <div className="flex items-center gap-2.5">
            <Mark />
            <span className="display text-[15px]">KnowledgeOS</span>
          </div>
        </div>

        <div className="max-w-xl">
          <p className="eyebrow mb-5">Retrieval-augmented knowledge platform</p>
          <h1 className="display text-[clamp(2.1rem,3.4vw,3.1rem)]">
            It tells you
            <br />
            <span style={{ color: "var(--color-signal)" }}>when it doesn&apos;t know.</span>
          </h1>
          <p className="mt-6 text-[14px] leading-relaxed text-muted max-w-md">
            Answers are drawn only from your own documents, every claim carries a
            citation back to the page it came from, and when retrieval falls below
            a measured confidence threshold the system refuses instead of
            inventing an answer.
          </p>

          <dl className="mt-10 grid grid-cols-3 gap-px bg-rule border hairline border-[1px] rounded-[4px] overflow-hidden">
            {[
              ["Hybrid", "vector + keyword, rank-fused"],
              ["Cited", "verified, never fabricated"],
              ["Bounded", "refuses below 0.58 cosine"],
            ].map(([term, detail]) => (
              <div key={term} className="bg-ink-panel p-4">
                <dt className="display text-[13px]">{term}</dt>
                <dd className="mt-1 text-[11px] leading-snug text-faint">{detail}</dd>
              </div>
            ))}
          </dl>
        </div>

        <p className="eyebrow">Local embeddings · no document text leaves the deployment</p>
      </section>

      {/* Right: the form. */}
      <section className="flex items-center justify-center p-6 sm:p-12">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <Mark />
            <span className="display text-[15px]">KnowledgeOS</span>
          </div>

          <h2 className="display text-[20px]">
            {mode === "signin" ? "Sign in" : "Create an account"}
          </h2>
          <p className="mt-1.5 text-[12px] text-muted">
            {mode === "signin"
              ? "Continue to your workspaces."
              : "You get an organization and a workspace, ready to use."}
          </p>

          <form onSubmit={submit} className="mt-7 space-y-3">
            {mode === "create" && (
              <>
                <Field label="Your name">
                  <input
                    className="field"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Abdul Rahman"
                    required
                    autoComplete="name"
                  />
                </Field>
                <Field label="Organization" hint="optional">
                  <input
                    className="field"
                    value={org}
                    onChange={(e) => setOrg(e.target.value)}
                    placeholder="Acme Group"
                    autoComplete="organization"
                  />
                </Field>
              </>
            )}

            <Field label="Email">
              <input
                ref={emailRef}
                className="field"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                autoComplete="email"
              />
            </Field>

            <Field
              label="Password"
              hint={mode === "create" ? "12 characters minimum" : undefined}
            >
              <input
                className="field"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                required
                minLength={mode === "create" ? 12 : 1}
                autoComplete={mode === "create" ? "new-password" : "current-password"}
              />
            </Field>

            {error && (
              <p
                className="text-[12px] leading-snug"
                style={{ color: "var(--color-refused)" }}
                role="alert"
              >
                {error}
              </p>
            )}

            <button className="btn btn-primary w-full !py-2.5" disabled={busy}>
              {busy ? "Working…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
          </form>

          <button
            className="mt-5 text-[12px] text-muted hover:text-paper transition-colors"
            onClick={() => {
              setMode(mode === "signin" ? "create" : "signin");
              setError(null);
            }}
          >
            {mode === "signin"
              ? "No account? Create one →"
              : "← Already have an account"}
          </button>
        </div>
      </section>
    </main>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="eyebrow flex items-baseline justify-between mb-1.5">
        {label}
        {hint && <span className="normal-case tracking-normal text-[10px]">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Mark() {
  // Three stacked bars of decreasing length: a rank-ordered result set, which is
  // literally what the product produces.
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
      <rect x="1" y="3" width="16" height="2.5" rx="1" fill="var(--color-signal)" />
      <rect x="1" y="7.75" width="11" height="2.5" rx="1" fill="var(--color-dense)" />
      <rect x="1" y="12.5" width="6" height="2.5" rx="1" fill="var(--color-sparse)" />
    </svg>
  );
}
