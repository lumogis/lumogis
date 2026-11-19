# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Lumogis Caddy (LUM-980): Caddy with Lumogis's Caddyfile and start-up preflight
# baked in, so an install from the two release files needs no source checkout. A
# git checkout still bind-mounts both files over these copies (docker-compose.yml),
# so editing the Caddyfile applies without a rebuild.
#
# LUM-331: built with the caddy-dns/acmedns module, so Caddy can get and renew the
# household's secure-address certificate (DNS-01 through Lumogis Home DNS).
#
# Pinned like postgres:16.8 and ollama/ollama: a published image is a long-lived
# artefact, so neither its base nor its module may float.

# Cross-compile on the build machine's own platform: the arm64 image needs no
# emulation, because no RUN executes on the target platform.
FROM --platform=$BUILDPLATFORM caddy:2.11.9-builder-alpine AS builder
ARG TARGETOS
ARG TARGETARCH
# caddy-dns/acmedns v0.7.0 (2026-06-02, on libdns/acmedns v0.5.0): the newest tag
# on 2026-11-16 (`go list -m -versions github.com/caddy-dns/acmedns`). Its
# username / password / subdomain / server_url options are what
# orchestrator/services/home_dns/snippet.py writes.
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    xcaddy build v2.11.9 --output /out/caddy \
      --with github.com/caddy-dns/acmedns@v0.7.0 \
  && mkdir -p /out/home.d

FROM caddy:2.11.9-alpine

COPY --from=builder /out/caddy /usr/bin/caddy
# The household's secure-address site (written by Core into the caddy-home
# volume, mounted here read-only). An empty directory is a valid import.
COPY --from=builder /out/home.d /etc/caddy/home.d
COPY Caddyfile /etc/caddy/Caddyfile
COPY preflight.sh /etc/caddy/preflight.sh
COPY validate-home.sh /etc/caddy/validate-home.sh

# The entrypoint (/bin/sh /etc/caddy/preflight.sh) and the command stay in the
# compose service definition, as before.

# LUM-980: the version this image was built as: dev for a local build, X.Y.Z for a
# release, the short commit for main. Last in the file so a new version rebuilds
# no other layer. Backups record it, and checks use it to tell pulled from built.
ARG LUMOGIS_IMAGE_VERSION=dev
ENV LUMOGIS_IMAGE_VERSION=$LUMOGIS_IMAGE_VERSION
