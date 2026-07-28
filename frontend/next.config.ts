import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8730";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Emits a minimal server bundle with only the modules actually imported,
  // which is what lets the runtime image ship without node_modules (§20).
  output: "standalone",

  // Proxy the API through this origin.
  //
  // Not a convenience: the refresh token is an httpOnly SameSite=Lax cookie, and
  // localhost:3000 -> 127.0.0.1:8730 is cross-site, so the browser would never
  // send it. Proxying means the browser only ever talks to one origin, cookies
  // behave, and no CORS credentials dance is required. It also mirrors
  // production, where a single reverse proxy fronts both (TDD §19).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
};

export default nextConfig;
