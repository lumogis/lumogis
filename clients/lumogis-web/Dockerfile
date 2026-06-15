# SPDX-License-Identifier: AGPL-3.0-only
# syntax=docker/dockerfile:1
# Multi-stage: node build -> nginx static serve.
# The container is fronted by Caddy (docker-compose.yml); nginx here
# only serves /usr/share/nginx/html. Same-origin routing happens at Caddy.

FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

COPY openapi.snapshot.json ./
COPY scripts/codegen.mjs scripts/codegen.mjs

COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

RUN npm run codegen

RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
