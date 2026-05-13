# ADR 036 — Docker image CI: GHCR publish (multi-arch) + compose overlay

**Status:** Finalised  
**Date:** 2026-05-11  
**Issue:** [LUM-192](https://linear.app/lumogis/issue/LUM-192/docker-image-ci-publishing-ghcrio-multi-platform-images-on-tag-and)  
**Related:** LUM-168 (install narrative); LUM-43 (compose policy checker)

## Context

Operators need a **pull-based** install path for **`orchestrator`** and **`lumogis-web`** (`linux/amd64` + `linux/arm64`) without building from source on every host. The default **`docker-compose.yml`** remains contributor-oriented (**`build:`** + bind mounts).

## Decision

1. **Registry:** **`ghcr.io/lumogis/`** — images **`lumogis-orchestrator`** and **`lumogis-web`** only (no Docker Hub in this chunk).

2. **CI:** **`.github/workflows/publish-image.yml`** — matrix build/push with **`docker/metadata-action`** (**`type=raw` → `latest`** on **`main`** pushes; **`type=semver`** on git tags **`v*`**). **`workflow_dispatch`** supported; **never** **`pull_request`**. **`GITHUB_TOKEN`** with **`packages: write`**. Canonical repo + ref guards on the job.

3. **Compose overlay:** **`docker-compose.ghcr.yml`**, merged **after** **`docker-compose.yml`**, sets **`image:`** for those two services. **Merge semantics (verified Docker Compose v5.1+):** plain **`build: null` does not remove** an inherited **`build:`** block; the overlay uses **`build: !reset null`** so the effective service model is **image-only pull**.

4. **Policy gate:** **`make compose-policy-check`** runs **`scripts/check_compose_policy.py`** on **`docker-compose.yml`** + **`docker-compose.ghcr.yml`**. The checker’s Pass A YAML loader registers **`!reset`** so the overlay file parses.

5. **Docs / UX:** **`README.md`** lists the published-image quickstart first; **`.env.example`** comments the **`COMPOSE_FILE`** merge pattern.

## Alternatives (deferred)

- **Native amd64 + arm64 builders** (skip QEMU) — follow-up child issue if build time/flakiness exceeds maintainer tolerance.
- **`cache: type=gha`** on **`docker/build-push-action`** — optional savings; not required for v1.

## Consequences

- **Self-hosters:** Shorter time-to-running stack when images are public and tags exist.
- **Maintainers:** Must set new GHCR packages to **Public** after first push; tag discipline (`v*`) aligns with **`metadata-action`** semver output (image tags without leading **`v`** for **`type=semver`**).
- **Tooling:** Operators on **Compose versions that lack `!reset` merge** need to upgrade Compose or run without this overlay merge pattern.

## Implementation notes (verification)

| Topic | Evidence |
| --- | --- |
| **`build: !reset null`** vs **`build: null`** | Compose **v5.1.3** merged config retains **`build`** with plain null; **`!reset`** clears inherited **`build`** for **`orchestrator`** / **`lumogis-web`**. |
| **Regression** | **`check_compose_policy.py`** **`docker-compose.yml` only** exits **0** after **`!reset`** SafeLoader handler. |

## Status history

- **2026-05-11:** Draft from `/explore` (`.cursor/adrs/docker_image_ci_ghcr.md`).
- **2026-05-11:** Finalised by `/verify-plan` — implementation matched decision; deviations documented inline above.
