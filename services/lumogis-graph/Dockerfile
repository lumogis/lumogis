# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Multistage Dockerfile for the standalone lumogis-graph service.
#
# Build context: the REPO ROOT (NOT services/lumogis-graph/), so we can copy
# the canonical orchestrator/models/webhook.py if the operator runs the build
# without first invoking `make sync-vendored`. Compose targets pass this
# context explicitly via:
#   build:
#     context: .
#     dockerfile: services/lumogis-graph/Dockerfile
#
# Why WORKDIR /app and `uvicorn main:app`:
#   The directory `services/lumogis-graph` contains a HYPHEN, which is not a
#   legal Python module name. So we cannot reach the FastAPI app via a dotted
#   import like `services.lumogis-graph.main:app`. Instead we copy the service
#   tree to /app and run uvicorn FROM /app — `main` then resolves as a
#   top-level module exactly the same way Core's orchestrator container does.
#
# Stage layout (ORDER MATTERS — Docker defaults to building the LAST stage):
#   1. builder — installs runtime deps into /opt/venv
#   2. test    — builder + dev deps + tests/ (built only via --target test)
#   3. runtime — production image (default `docker build` target)
# The runtime stage is intentionally LAST so a bare `docker build .` produces
# the production artefact and never accidentally ships tests/.

# ---------------------------------------------------------------------------
# Stage 1 — builder: install Python deps into a venv we copy forward.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Build tools needed for psycopg2-binary's wheel selection on slim base.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY services/lumogis-graph/requirements.txt ./requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — test: builder venv + dev tools + tests/.
#
# Built only by `make compose-test-kg` (which uses `--target test`). Adds
# pytest/pytest-asyncio/ruff to the venv at build time so the test
# invocation does NOT do a fresh `pip install -r requirements-dev.txt`
# per run (the on-the-fly install pattern used by Core's `compose-test`
# hangs on this small service because requirements-dev.txt re-resolves
# the full runtime requirements through the pip dependency resolver).
#
# Runs as root by default so ad-hoc `pip install ipdb` etc. work inside
# the container during debugging.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS test

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Pinned versions match `requirements-dev.txt`; keep both in sync if the
# dev manifest moves.
RUN pip install --no-cache-dir \
        "pytest>=8.0.0" \
        "pytest-asyncio>=0.24.0" \
        "ruff>=0.4.0"

WORKDIR /app
COPY services/lumogis-graph/ /app/
# tests/ and conftest.py travel with the test image (the runtime stage
# below strips them so the production artefact stays lean).

# Default invocation: run the unit tests. Override with
# `docker run ... lumogis-graph:test python -m pytest tests/test_X.py`
# for narrower runs during debugging.
CMD ["python", "-m", "pytest", "tests", "-q", "--tb=short"]

# ---------------------------------------------------------------------------
# Stage 3 — runtime: minimal production image. Default `docker build` target.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Runtime essentials only:
#   - libpq5: shared lib psycopg2-binary links against
#   - curl:   used by the HEALTHCHECK below
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 lumogis \
    && useradd  --system --uid 1001 --gid lumogis --create-home --shell /sbin/nologin lumogis

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy the service tree. NOTE: the build context is the REPO ROOT, so Docker
# only consults the repo-root `.dockerignore`, NOT the
# `services/lumogis-graph/.dockerignore`. We therefore strip test artefacts
# explicitly below to keep the runtime image lean and avoid shipping pytest
# fixtures into a production deployment.
COPY --chown=lumogis:lumogis services/lumogis-graph/ /app/

RUN rm -rf /app/tests /app/conftest.py /app/.dockerignore /app/requirements-dev.txt

# The KG service is graph-only; bind only its private port. Compose maps it
# to the internal Docker network, never to the host (no `ports:` entry in
# docker-compose.premium.yml — see the plan §F2).
EXPOSE 8001

USER lumogis

# Healthcheck per the plan §F5: curl /health every 30s, 30s start grace, 3 retries.
# /health always returns 200 by contract, so a failure here is genuinely
# "uvicorn isn't accepting connections" rather than a transient app error.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8001/health > /dev/null || exit 1

# `main:app` — top-level module resolution from /app. NOT
# `services.lumogis-graph.main:app` (dir name has a hyphen, not importable).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
