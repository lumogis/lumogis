# Pinned to a patch version, not the floating `3.12-slim` tag: CVE-2025-6069
# is quadratic behaviour in `html.parser`, which LUM-810's body extractor runs
# on sender-controlled input on a request thread. Fixed for 3.12 at 3.12.11, so
# a floating tag makes the patch level depend on build date.
FROM python:3.12.11-slim
# jq (LUM-337): required by scripts/doctor/run.sh in --json mode so an operator
# running `make doctor ARGS="--json"` inside the orchestrator container works
# (run.sh exits "DOCTOR_FATAL: jq not found" without it). The pytest suite stubs
# jq, so this is the operator-path fix rather than a test-pass fix.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils curl git jq nodejs && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
# Runtime user for the privilege drop (LUM-887). The entrypoint re-owns these
# paths to the actual target UID (the owner of the /project checkout) on boot;
# the chown here only gives the image-internal dirs a sane default. HF_HOME only
# - never SENTENCE_TRANSFORMERS_HOME (LUM-466): setting both keeps a second
# ~1.1 GB copy of the BGE weights.
RUN groupadd --system --gid 10001 lumogis \
 && useradd --system --uid 10001 --gid 10001 --no-create-home \
      --home-dir /var/lib/lumogis/home --shell /usr/sbin/nologin lumogis \
 && mkdir -p /var/lib/lumogis/home /var/lib/lumogis/hf-cache \
      /var/lib/lumogis/email-staging /opt/lumogis/data /caddy-home \
 && chown -R 10001:10001 /var/lib/lumogis /opt/lumogis/data /caddy-home
ENV HF_HOME=/var/lib/lumogis/hf-cache
WORKDIR /app
COPY orchestrator/requirements-core.txt orchestrator/requirements.txt .
# LUM-788 — the wheel cache lives on the BUILD HOST via a BuildKit cache mount,
# not in an image layer, so this costs nothing in image size. Same pattern
# clients/lumogis-web/Dockerfile already uses for npm.
#
# `--no-cache-dir` was here and was the expensive part. sentence-transformers
# pulls torch plus the nvidia_* CUDA wheels — ~1.5 GB — and discarding the
# cache meant re-downloading all of it over a domestic uplink every time this
# layer invalidated. Measured at 25 minutes on the self-hosted runner (run
# 31041055954), which is why doctor-integration never once finished.
#
# With the mount, a requirements change re-resolves from local disk instead of
# the network. Wheels are written to the cache as each one completes, so even
# an interrupted build leaves the cache warmer than it found it.
#
# LUM-310 — torch comes from PyTorch's CPU-only index, alone, before the rest.
# The PyPI torch wheel pulls the nvidia_* CUDA libraries (3.0 GB on x86-64,
# ~1.7 GB on arm64), and nothing here can use them: the GPU overlay
# (docker-compose.gpu.yml) gives a GPU to Ollama only, never to this container.
# `--index-url` applies to that one install, so no other package can come from
# the PyTorch index; `pip install -r` then keeps the CPU torch, which satisfies
# sentence-transformers.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
  && pip install --index-url https://download.pytorch.org/whl/cpu torch \
  && pip install -r requirements.txt \
  && python -c "import torch, sys; v = torch.__version__; sys.exit(0 if v.endswith('+cpu') else f'expected a CPU-only torch, got {v}')"
COPY orchestrator/ .
COPY config/ /opt/lumogis/config/
# LUM-980: the schema ships in the image. db_migrations.py prefers
# /project/postgres (a git checkout) and falls back to this copy (a release install).
COPY postgres/init.sql /opt/lumogis/postgres/init.sql
COPY postgres/migrations/ /opt/lumogis/postgres/migrations/
COPY orchestrator/docker-entrypoint.sh /docker-entrypoint.sh
# Strip CR so Windows CRLF checkouts do not break shebang (exec: no such file)
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh
# There is deliberately no USER: the entrypoint does the root-only one-shot
# init, then `setpriv` drops to the checkout owner (LUM-887). `docker compose
# exec` therefore still enters as root.
ENTRYPOINT ["/docker-entrypoint.sh"]
# --timeout-keep-alive 75 (LUM-875): uvicorn's default is 5 s, far shorter than
# Caddy's pooled upstream connections. When uvicorn closed an idle connection
# just as Caddy reused it, a POST got EOF / connection reset and Caddy answered
# 502 (it does not retry a POST); a failed /api/v1/auth/refresh signs the user
# out. The rule is that the proxy's idle timeout must be shorter than this one:
# docker/caddy/Caddyfile sets `keepalive 60s` on every orchestrator upstream.
# Keep docker-compose.dev.yml's `command:` in step;
# orchestrator/tests/test_uvicorn_keepalive_vs_caddy.py enforces both.
# --loop asyncio (LUM-903): uvicorn[standard] installs uvloop and picks it by
# default. tethered>=1.0 refuses activate() once uvloop is loaded (its libuv
# networking raises no audit events), so the egress ceiling could not boot.
# test_egress_ceiling.py enforces this on the CMD and the dev overlay.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75", "--loop", "asyncio"]

# LUM-980: the version this image was built as: dev for a local build, X.Y.Z for a
# release, the short commit for main. Last in the file so a new version rebuilds
# no other layer. Backups record it, and checks use it to tell pulled from built.
ARG LUMOGIS_IMAGE_VERSION=dev
ENV LUMOGIS_IMAGE_VERSION=$LUMOGIS_IMAGE_VERSION
# LUM-980: fingerprint of the release docker-compose.yml this image ships with
# (dev for local builds); the compose freshness check compares it with the
# header of the compose file in the install folder.
ARG LUMOGIS_COMPOSE_FINGERPRINT=dev
ENV LUMOGIS_COMPOSE_FINGERPRINT=$LUMOGIS_COMPOSE_FINGERPRINT
