# Multi-stage. The runtime image carries the standalone server output only —
# no source, no dev dependencies, no build toolchain (TDD §20).

FROM node:22-alpine AS deps
WORKDIR /app
# Lockfile first so a source edit does not invalidate the dependency layer.
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 \
    BUILD_STANDALONE=true
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Never root. A container escape should not begin with uid 0.
RUN addgroup -g 10001 -S app && adduser -u 10001 -S app -G app

# `output: standalone` emits a minimal server plus only the modules it imports.
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
COPY --from=builder --chown=app:app /app/public ./public

USER app
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
