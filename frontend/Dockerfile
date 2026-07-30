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
