# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Lumogis Caddy (LUM-980): the stock Caddy image with Lumogis's Caddyfile and
# start-up preflight baked in, so an install from the two release files needs no
# source checkout. A git checkout still bind-mounts both files over these copies
# (docker-compose.yml), so editing the Caddyfile applies without a rebuild.
#
# Pinned like postgres:16.8 and ollama/ollama: a published image is a long-lived
# artefact, so its base must not float.
FROM caddy:2.11.9-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY preflight.sh /etc/caddy/preflight.sh

# The entrypoint (/bin/sh /etc/caddy/preflight.sh) and the command stay in the
# compose service definition, as before.

# LUM-980: the version this image was built as: dev for a local build, X.Y.Z for a
# release, the short commit for main. Last in the file so a new version rebuilds
# no other layer. Backups record it, and checks use it to tell pulled from built.
ARG LUMOGIS_IMAGE_VERSION=dev
ENV LUMOGIS_IMAGE_VERSION=$LUMOGIS_IMAGE_VERSION
