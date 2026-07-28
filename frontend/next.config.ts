import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Standalone output is for the DOCKER image only — it emits a minimal server
  // bundle so the runtime layer ships without node_modules (§20). Vercel builds
  // its own output format and does not want it, so it is opt-in via the flag
  // the Dockerfile sets.
  ...(process.env.BUILD_STANDALONE === "true" ? { output: "standalone" as const } : {}),

  // NOTE: the API proxy is NOT a rewrite. Next evaluates this file at build
  // time and serialises the result into the standalone bundle, so a rewrite
  // destination read from the environment gets frozen at whatever was set
  // during `docker build`. The proxy lives in src/app/api/[...path]/route.ts,
  // which reads the environment per request — see that file.
};

export default nextConfig;
