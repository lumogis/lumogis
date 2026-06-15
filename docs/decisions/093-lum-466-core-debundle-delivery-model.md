# ADR-093: Core debundle delivery model — native installer + thin clients vs fused Hub

**Status:** Finalised
**Created:** 2026-06-10
**Last updated:** 2026-06-11
**Decided by:** `/explore LUM-466` (claude-opus-4-8); finalised by `/verify-plan LUM-466` (Phase 1 shipped)

> Scope is the **Lumogis Hub** (private/commercial appliance) delivery model + Core (AGPL) packaging implications; stripped from the public AGPL export, like ADR-076/081. Draft mirror retained at `.cursor/adrs/core-debundle-delivery-model.md`.

## Context

The fused slim PyInstaller Hub (LUM-396 / LUM-447 / LUM-460) keeps a small footprint by **excluding** torch (`sentence-transformers` / BGE reranker), so heavy-dependency capabilities cannot ship in the Hub as they do in Docker. LUM-466 asks whether a different delivery model would make Hub↔Docker capability parity hold **by construction** instead of by discipline: ship the same Core everywhere and make clients thin.

Two facts from the current code shape the decision:

1. The Hub is **already** an internally client-server, supervised-sidecar Tauri app (`apps/lumogis-server/src-tauri/src/bundled/`), not a single frozen binary. Core is a PyInstaller one-dir sidecar; Qdrant + embedded Postgres are binary sidecars; Ollama is detect+guide. A Rust supervisor handles ordered start, `/healthz` polling, crash-restart, and clean shutdown.
2. The torch divergence is driven by **one** optional dependency. The 6.7 GB figure (LUM-447) was PyInstaller dev-venv bloat, fixed by LUM-460 (~101 MB). A native installer *can* include torch without the PyInstaller fight, but the honest CPU-torch footprint (~200–600 MB) remains.

The exploration (`.cursor/explorations/LUM-466-core-debundle-delivery-model.md`) evaluated three options: **A** status-quo fused slim Hub (parity by discipline), **B** full debundle with no fused UI (fails Persona C seam-hiding), **C** hybrid native installer where Core runs as an OS-managed local service under a thin client (one-app feel, parity by construction).

## Decision

**Adopt a refined Option B: two Docker-free products with independent update streams — a Lumogis Core *server* installer and a thin *client* — carrying the full capability set (including `torch`/BGE reranker). Drop the fused, seamless single-app Persona C experience (Option C) as unnecessary complexity.** (Operator decision, 2026-06-10.) Concretely:

1. **Two products, two update streams.** A **Lumogis Core server** native installer (Docker-free) and a **client** installer (Lumogis Search overlay) or zero-install Lumogis Web in a browser.
2. **Keep `torch`/BGE in Core.** Search quality is existential; do not gate v1 on the not-yet-stable Ollama rerank. The in-process cross-encoder ships in the server everywhere.
3. **One Core build, two delivery vehicles.** The same full Core (with `torch`) ships as Docker (Personas A/B) and as the native server installer (Persona C) ⇒ **parity by construction**.
4. **Server packaging:** embedded `python-build-standalone` interpreter installing the **full `orchestrator/requirements.txt`**; Qdrant + zonky embedded Postgres reused from `stage-sidecar-binaries.sh`; Ollama detect+guide unchanged.
5. **Reposition, do not discard, LUM-396.** The supervisor lifecycle logic moves into the server product; only its client-fusion coupling is rework.
6. **Demote LUM-461** (GGUF/llama-server rerank) to optional future slimming — not v1-blocking.
7. **Manage the one new cost:** client/server version compatibility across two streams, via a server-advertised min-client-version handshake (deferred until a second client exists).

This is **not** on the HN-launch critical path (Docker + Web ship that).

## Alternatives Considered

- **Option A — fused slim Hub (status quo):** lowest cost, but leaves parity to discipline *and* forces the `torch` exclusion that degrades reranking. Rejected.
- **Option C — fused, seamless single-app (hidden OS-managed service):** technically elegant but the auto-start/auto-connect/single-update machinery is complexity the operator chose to avoid. Rejected.
- **Nuitka one-file Core / bundling Ollama+weights / Qdrant-local+SQLite shrink:** deferred or orthogonal (ADR-076, LUM-359).

Full per-axis matrix: `.cursor/explorations/LUM-466-core-debundle-delivery-model.md`.

## Consequences

**Easier:**
- Capability parity across personas holds by construction (one full Core build, including `torch`/BGE, shipped two ways).
- Reliable reranking everywhere — no bet on the not-yet-stable Ollama rerank for v1.
- Far less Persona C plumbing than Option C: two ordinary installers, no hidden auto-start service, no unified update.
- LUM-396 supervisor work is reused inside the server product, not lost.

**Harder / foreclosed:**
- **Two update streams ⇒ client/server version compatibility** must be managed — **warn**-style min-client-version handshake (deferred until a second client exists).
- The server installer must do the Docker-free multi-component job — ship a **pinned, tested Core+Qdrant+Postgres triple per release**; Ollama stays detect+guide.
- Larger server install (CPU `torch` + Qdrant + Postgres + pulled models) — accepted for quality.
- **Supervisor rework is real, not a rename:** Core moves from a **PyInstaller freeze** to running **from a full on-disk venv** (torch/BGE), realised via **Mechanism B** — the Tauri `orchestrator` sidecar **slot** is retained as a thin shim that `exec`s the staged venv python, reusing the existing `sidecar-exec`/wrap/deb seam with no shell-scope widening.
- Forecloses the fused single-binary appliance and the slim-torch-excluded Hub freeze as the Persona C strategy.

**As-built (LUM-466 Phase 1, Linux x64 — verified 2026-06-11):**
- **Mechanism B implemented.** The staged `orchestrator-<triple>.real` is a shell shim that `exec`s `core-venv/python/bin/python core-venv/bundled-core-launcher.py`; `supervisor.rs` keeps the `sidecar("orchestrator")` seam (raised Core readiness timeout to 300 s for BGE cold load; clearer degrade message). **No `capabilities/bundled.json` change** — least privilege preserved.
- **CPU-only torch pin** (`TORCH_CPU_INDEX`, default `download.pytorch.org/whl/cpu`): `torch 2.12.0+cpu` (~371 MB), confirming the ADR's ~200–600 MB CPU-torch expectation. The default PyPI wheel is CUDA (`2.12.0+cu130`, ~5–6 GB) and would have bloated the appliance ~4×.
- **BGE provisioning:** `BAAI/bge-reranker-base` pre-staged offline (revision `2cfc18c9…`); snapshot trimmed to config + tokenizer + **safetensors** only (drop `pytorch_model.bin`/TF/Flax/ONNX); `HF_HOME` only (not `SENTENCE_TRANSFORMERS_HOME`, which kept a duplicate ~1.1 GB copy).
- **Measured sizes:** staged `core-venv/` **2.2 GB** (python+deps 1.12 GB incl torch 371 MB; BGE cache 1.07 GB). Release `.deb` **2.6 GB** compressed (installed 4.3 GB). BGE peak RSS **≈ 1.36 GB** (under the ~2 GB working figure).
- **Parity gate proven:** offline BGE `.rerank()` returns correctly ordered results in the standalone venv (launcher import path) and after relocating the tree to a different absolute path. Full release `.deb` built end-to-end on Linux x64.
- **Build-system fix (folded into LUM-466):** `lumogis-search`'s `build.rs` (path-dep'd from the hub since LUM-435) ran `tauri_build()` against the hub's leaked `TAURI_CONFIG` and broke the deb build; now skips when a foreign `com.lumogis.hub` config is present.

**Future chunks must know:**
- One Core build with `torch` ships everywhere; LUM-460's slim approach is Docker-image hygiene only.
- LUM-396 supervisor logic is the seed for the server product's stack manager (venv-spawn via the sidecar-shim seam). Relationship: **related**, not blocked.
- **LUM-446 is narrowed, not obsolete:** overlay first-run-visibility / Wayland global-hotkey **lockout (P0)** survives if the Search overlay ships in v1. Relationship: **related**.
- LUM-462 simplifies (no Class-A/B split; torch is present in the server) — the one true **blocks** edge.
- LUM-461 is **closed (Done)**: keep torch/BGE; reopen only if Ollama rerank matures.
- **Recall vs precision:** the reranker secures *precision*; the *recall* failure is fixed by hybrid retrieval (LUM-289 / LUM-295) — must not vanish behind this decision.
- Persona C v1 surface = **server + browser to Web** (with a desktop shortcut/thin launcher); Search overlay is a fast-follow.

## Revisit conditions

- **Server-installer `torch` footprint or cross-OS packaging proves unworkable** → revisit LUM-461 (retire `torch`) or a per-OS fallback.
- **Two-stream version skew causes real breakage** → tighten to a hard min-client-version block.
- **Ollama native rerank stabilises with parity to BGE** → LUM-461 can slim the server later.
- **Household auth/multi-user (v0.2) ships** → enables richer sign-in/role capability gating.

## Status history
- 2026-06-10: Draft created by `/explore LUM-466` (initial recommendation: Option C).
- 2026-06-10: **Revised after operator steer** — decision changed to refined **Option B** (two Docker-free products, `torch` retained, Option C dropped). LUM-461 demoted.
- 2026-06-10: **Relationship + closure corrections** — blocks LUM-462; related LUM-396; related LUM-446 (narrowed). LUM-461 closed (Done). Recall fix (LUM-289/295) named.
- 2026-06-10: **LUM-466 repurposed exploration → feature** (milestone:v1.0, kept Backlog).
- 2026-06-11: **Revised during `/review-plan --arbitrate` R1** — softened the supervisor consequence to allow Mechanism B (sidecar shim).
- 2026-06-11: **Finalised by `/verify-plan LUM-466`** — Phase 1 (Linux x64) shipped: Mechanism B sidecar shim, on-disk venv with CPU torch + offline BGE, parity gate + relocation proven, full release `.deb` built. Implementation confirmed the decision (refined Option B, torch retained); CPU-torch footprint matched the ADR estimate. Implementation notes recorded under Consequences → As-built.
