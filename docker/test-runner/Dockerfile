# syntax=docker/dockerfile:1
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Test-runner image (LUM-894 layer 1). One image that can run the whole
# offline test surface of this tree: `make test`, `make test-kg`,
# `make test-lumogis-mcp`, ruff, the web codegen check, and the Lumogis Web
# test, lint and build.
#
# Built by `scripts/test-env.sh build`, never by hand: the script assembles a
# tiny build context holding only the files COPY'd below and tags the image
# `lumogis-test-runner:<hash of those files>`, so it rebuilds only when a
# requirements file, the web lockfile or this Dockerfile changes.
#
# The image carries DEPENDENCIES only, never the source. The worktree is
# bind-mounted at its own host path when a test runs, and the tests run with
# no network (tier 1: `--network none`; tier 2: an internal compose network
# holding only the throwaway databases).

# Node 20.19+ is required by clients/lumogis-web (package.json "engines").
# Debian's nodejs package has no npm, so take the official build instead.
FROM node:22.20.0-bookworm-slim AS node

# Same base as orchestrator/Dockerfile, so tests see the interpreter that ships.
FROM python:3.12.11-slim

# tesseract-ocr, poppler-utils, curl, git, jq: as orchestrator/Dockerfile.
# make: the Makefile targets run inside the container (no `make -n` expansion).
# rsync: tests/unit/test_backup_files.sh (LUM-892) needs it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils curl git jq make rsync \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

WORKDIR /opt/test-runner/src
COPY orchestrator/ orchestrator/
COPY stack-control/ stack-control/
COPY services/lumogis-graph/ services/lumogis-graph/
COPY clients/lumogis-mcp/ clients/lumogis-mcp/

# CPU-only torch FIRST. sentence-transformers (orchestrator/requirements.txt)
# otherwise resolves the CUDA build of torch from PyPI plus ~1.5 GB of nvidia_*
# wheels, which is what made the improvised verify venvs 6 GB and made their
# downloads fail. With torch already satisfied, the later installs keep it.
#
# Install order mirrors .github/workflows/ci.yml (lint-and-test) exactly:
# stack-control pins fastapi/pydantic/uvicorn and the last install wins, so a
# different order would test a different dependency set from CI.
#
# setuptools: `make test-lumogis-mcp` runs `pip install -e '.[dev]'`, which at
# test time has no network, so it needs the build backend already installed.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r orchestrator/requirements.txt \
 && pip install -r orchestrator/requirements-dev.txt \
 && pip install -r stack-control/requirements-dev.txt \
 && pip install -r services/lumogis-graph/requirements.txt \
 && pip install -r services/lumogis-graph/requirements-dev.txt \
 && pip install "setuptools>=68" wheel \
 && pip install "./clients/lumogis-mcp[dev]" \
 && pip uninstall -y lumogis-mcp

# Fail the build, not a later test run, if CUDA crept back in.
RUN if pip list 2>/dev/null | grep -i '^nvidia-'; then \
      echo "test-runner: nvidia_* wheels installed - torch is not the CPU build" >&2; exit 1; \
    fi \
 && python -c "import torch, sys; sys.exit(1 if torch.version.cuda else 0)" \
 && rm -rf /opt/test-runner/src

# Lumogis Web dependencies, resolved from the lockfile at build time. At test
# time scripts/test-env.sh mounts a copy of this tree over the worktree's
# clients/lumogis-web/node_modules, so the host's own node_modules (if any) is
# never used or modified.
WORKDIR /opt/lumogis-web
COPY clients/lumogis-web/package.json clients/lumogis-web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund \
 && chmod -R a+rX /opt/lumogis-web

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LUMOGIS_OPENAPI_PYTHON=python3 \
    npm_config_update_notifier=false \
    npm_config_audit=false \
    npm_config_fund=false

WORKDIR /
