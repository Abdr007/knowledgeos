import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Emits a minimal server bundle with only the modules actually imported,
  // which is what lets the runtime image ship without node_modules (§20).
  output: "standalone",

  // NOTE: the API proxy is NOT a rewrite. Next evaluates this file at build
  // time and serialises the result into the standalone bundle, so a rewrite
  // destination read from the environment gets frozen at whatever was set
  // during `docker build`. The proxy lives in src/app/api/[...path]/route.ts,
  // which reads the environment per request — see that file.
};

export default nextConfig;
