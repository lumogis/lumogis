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

# Runs as uid 101 (LUM-887). `listen 80` is kept: Docker sets
# net.ipv4.ip_unprivileged_port_start=0 inside the container (verified on Docker
# 29.9.2). If a runtime refuses, move to 8080 together with the Caddyfile
# (lumogis-web:80), `expose` and the compose healthcheck.
FROM nginxinc/nginx-unprivileged:1.31-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
