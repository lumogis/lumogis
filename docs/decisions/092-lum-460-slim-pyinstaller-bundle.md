# ADR-092: Slim PyInstaller Core bundle for Lumogis Hub

**Status:** Finalised  
**Created:** 2026-06-10  
**Last updated:** 2026-06-10  
**Decided by:** `/explore LUM-447`; implemented **LUM-460**; finalised by `/verify-plan LUM-460`

Parent packaging ADR: [076-lum-396-bundled-sidecar-process-manager.md](076-lum-396-bundled-sidecar-process-manager.md).

## Context

The Lumogis Hub Persona C `.deb` (~6.7 GB on Linux x64, June 2026) was dominated by a PyInstaller Core sidecar built from the **full developer virtualenv**, freezing CUDA torch, transformers, and related ML wheels even though bundled runtime sets `RERANKER_BACKEND=none` and uses **Ollama** for embeddings (ADR-076). This blocked reasonable household distribution.

Exploration: `.cursor/explorations/LUM-447-slim-pyinstaller-bundle.md` (**LUM-447**).

## Decision

Shrink the bundled Core sidecar using a **layered requirements + packaging strategy** (keep PyInstaller per ADR-076). Shipped in **LUM-460**:

1. **`orchestrator/requirements-core.txt`** — base runtime deps **without** `sentence-transformers`; single source of truth for bundled freeze and Docker base install context.
2. **`orchestrator/requirements.txt`** — BGE-profile extension: `-r requirements-core.txt` + `sentence-transformers` (Docker/Compose full profile).
3. **`build-orchestrator-sidecar.sh`** — isolated `apps/lumogis-server/.venv-bundled-build/` from core only; **refuses** active repo dev `VIRTUAL_ENV`; recreates venv when `requirements-core.txt` SHA changes.
4. **`orchestrator-bundled.spec`** — `excludes` for **reranker ML stack only** (`torch`, `triton`, `nvidia*`, `transformers`, `sentence_transformers`); never exclude splink/pandas dedup-search stack.
5. **`check-orchestrator-bundle-size.sh`** + **`smoke-bundled-sidecar-contents.sh`** — CI/local guards on bundle size and contents (dedup stack present, ML wheels absent).

Add-back framework (Class A via Ollama only) and GGUF reranker path remain **separate** tickets (**LUM-462**, **LUM-461**). Nuitka and download-on-first-run Core remain **out of scope**.

## Alternatives Considered

- **Parallel bundled requirements subset** — drift trap vs Docker deps; rejected in favour of layered core + extension.
- **PyInstaller excludes only** — insufficient when build venv still contains CUDA wheels; rejected as sole fix.
- **Excluding pandas/scipy/sklearn** — breaks splink entity dedup; rejected.
- **Nuitka** — ADR-076 deferred; high compile/CI cost; rejected for this chunk.
- **Docker-stage PyInstaller** — good reproducibility story; deferred until slim venv PoC proves target size.
- **Download-on-first-run Core** — rejected; breaks offline-first Persona C install.

## Consequences

- **Easier:** Persona C installer size becomes viable; maintainers get CI regression protection on bundle size; one requirements base avoids Docker/bundled drift; public AGPL export ships layered requirements for Compose installs.
- **Harder:** Layered requirements must stay documented; build script must enforce core-only venv discipline; smoke tests must cover dedup/search path; `ORCHESTRATOR_BUNDLE_MAX_MB` must be tuned after first slim PoC measurement.
- **Future chunks must know:** Do not re-point `build-orchestrator-sidecar.sh` at dev `.venv`; reranker remains Compose/advanced until **LUM-461** GGUF path lands.

## Revisit conditions

- Slim core venv + excludes PoC still exceeds budget on Linux x64 after dedup/search smoke green → revisit optional pruning or Docker-stage build.
- PoC exceeds budget with functional regressions in search/dedup → audit import graph before considering Nuitka (ADR-076 revisit).
- Ollama/llama.cpp rerank matures → **LUM-461** may retire sentence-transformers from Docker image entirely.

## Status history

- 2026-06-10: Draft created by /explore LUM-447
- 2026-06-10: Updated after review — layered requirements, reranker-only excludes, LUM-460/461/462 split
- 2026-06-10: Finalised by /verify-plan LUM-460 — implementation confirmed
