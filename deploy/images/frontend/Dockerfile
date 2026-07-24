# syntax=docker/dockerfile:1.7
# Multi-stage build for the cubeplex Next.js frontend.
#
# Stage 1 (builder): pnpm install + build all workspace packages, produce
#   the Next.js standalone output (gated by NEXT_OUTPUT=standalone in
#   next.config.ts).
# Stage 2 (runtime): node-alpine + standalone bundle. No pnpm or sources.
#
# Build-time mirror knobs (default to upstream sources; override at build
# time when behind a slow / partitioned network):
#
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
#
# (`build-and-push.sh` reads this from the operator's environment and
# passes it through.)

ARG NODE_VERSION=20
ARG NPM_REGISTRY=""


FROM node:${NODE_VERSION}-alpine AS builder

ARG NPM_REGISTRY

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@10.23.0 --activate \
    && if [ -n "${NPM_REGISTRY}" ]; then \
        npm config set registry "${NPM_REGISTRY}"; \
        pnpm config set registry "${NPM_REGISTRY}"; \
       fi

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/packages/core/package.json ./packages/core/
COPY frontend/packages/web/package.json ./packages/web/

RUN pnpm install --frozen-lockfile

COPY frontend/packages ./packages
COPY frontend/scripts ./scripts

RUN pnpm --filter @cubeplex/core build || true
RUN pnpm --filter web build


FROM node:${NODE_VERSION}-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

RUN addgroup --system --gid 1001 cubeplex \
    && adduser --system --uid 1001 --ingroup cubeplex cubeplex

COPY --from=builder --chown=cubeplex:cubeplex /app/packages/web/.next/standalone ./
COPY --from=builder --chown=cubeplex:cubeplex /app/packages/web/.next/static ./packages/web/.next/static
COPY --from=builder --chown=cubeplex:cubeplex /app/packages/web/public ./packages/web/public

# Rewrites the baked /api/* proxy destination with the runtime CUBEPLEX_API_URL
# on start (Next bakes rewrites() at build time — see the script).
COPY --chown=cubeplex:cubeplex deploy/images/frontend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER cubeplex

EXPOSE 3000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["node", "packages/web/server.js"]
