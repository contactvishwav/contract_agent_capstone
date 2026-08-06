FROM node:23-alpine AS base

WORKDIR /app

COPY package*.json ./

# ---------------------------------------------------------------------------
# dev: used by docker-compose.yml for local development. Installs all deps
# (including devDependencies - vite, eslint, ...), runs the Vite dev server
# with hot-reload.
# ---------------------------------------------------------------------------
FROM base AS dev

RUN npm install

COPY . .

EXPOSE 5173

# Reliability/observability audit finding #9: points at the backend's real
# health endpoint (backend/api/monitoring_api.py's /api/monitoring/health)
# through this server's own /api proxy (vite.config.ts's server.proxy in
# dev, nginx.conf.template's location /api/ in production) - proves both
# that this container is serving requests and that it can actually reach
# the backend, not just that the process is alive. `wget` is BusyBox
# (already in this alpine base image), no extra package needed.
#
# 127.0.0.1, not localhost: found live on a real Ubuntu Docker host
# (docs/DEPLOYMENT.md) - this Alpine image's resolver returns ::1 for
# "localhost" before 127.0.0.1, and nothing here actually listens on the
# IPv6 loopback, so `wget http://localhost/...` gets a real, immediate
# "Connection refused" even though the exact same request against
# 127.0.0.1 succeeds. Never reproduced locally (different Docker Desktop
# IPv6 configuration) - only surfaced against this real deployment target.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:5173/api/monitoring/health || exit 1

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---------------------------------------------------------------------------
# build: compiles the production static bundle (tsc + vite build -> dist/).
# ---------------------------------------------------------------------------
FROM base AS build

RUN npm ci

COPY . .

RUN npm run build

# ---------------------------------------------------------------------------
# production: static assets served by nginx. No Node/npm, no dev
# dependencies, no dev server - just the compiled bundle plus an nginx
# config template that reverse-proxies /api/ to the backend service
# (mirrors vite.config.ts's dev-server proxy, which only exists in dev).
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS production

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

ENV BACKEND_HOST=backend \
    BACKEND_PORT=8000

EXPOSE 80

# 127.0.0.1, not localhost - see the `dev` target's HEALTHCHECK comment
# above for why (found live in the real GCP deployment, docs/DEPLOYMENT.md).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q --spider http://127.0.0.1/api/monitoring/health || exit 1
