# ADR-059: First-run quickstart — `docs/deployment/quickstart.md` anchored on published GHCR images

**Status:** Finalised
**Created:** 2026-05-22
**Last updated:** 2026-05-22

## Context

LUM-184 requires a tested first-run setup guide at `docs/deployment/quickstart.md`: a short path from a new host to a working instance, with explicit guidance on Ollama model pull, Postgres migrations, a smoke test, and common errors. `README.md` already carries two quickstart blocks (published images + build from source); the new doc must stay aligned with the GHCR block.

## Decision

Ship `docs/deployment/quickstart.md` as a **docs-only artefact** anchored on the **published-images path** (`docker-compose.yml` + `docker-compose.ghcr.yml`, `docker compose up -d --pull always`). It documents already-shipped runtime behaviour: `OLLAMA_EXTRA_MODELS` auto-pull (comma-separated names; entrypoint `IFS=','`), automatic Postgres migrations via `orchestrator/docker-entrypoint.sh` (non-zero exit → **WARNING** and continue), and `curl -fsS http://localhost:8000/health` as the primary smoke step with `make health` as the secondary (`curl -s` without `-f`). README §Getting started keeps its two-block structure and links to the full walkthrough. **No** new orchestrator code, Compose service, or Makefile target in LUM-184 scope.

## Alternatives considered

- **Source-build path as canonical quickstart** — rejected for first-run time budget; CONTRIBUTING remains the source-build path.
- **`make first-run` / dedicated `firstrun` profile** — rejected (maintenance and LUM-43 surface); revisit only with usability evidence.
- **Third-party `curl | bash` installers** — rejected on supply-chain and persona grounds.

## Consequences

- LUM-195 / LUM-168 gain a stable public path to operator steps; LUM-158 can plug in `docs/deployment/remote-access.md` without restructuring this doc.
- Any change to `docker-entrypoint.sh` migration behaviour, embedder/extra model pull semantics, or README GHCR commands should update `docs/deployment/quickstart.md` in the same change set.

## Status history

- 2026-05-22: Draft created by `/explore --headless` (LUM-184) — `.cursor/adrs/LUM-184-first-run-quickstart.md`
- 2026-05-22: Finalised by `/verify-plan --headless` — implementation confirmed; canonical copy this file
