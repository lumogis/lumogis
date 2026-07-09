# ADR-124: PBS all-triples selector + mac/win Server build spike — slice 1 (LUM-468)

**Status:** Finalised (partial slice — ticket remains open)

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-468](https://linear.app/lumogis/issue/LUM-468) — **not Done** (slice 1 only)

**Related:** [ADR-093](093-lum-466-core-debundle-delivery-model.md), [ADR-097](097-lum-470-pip-dependency-hash-pinning.md), [ADR-116](116-lum-492-server-build-ci.md), epic LUM-519

## Context

LUM-466 proved Linux x64 core-venv. LUM-468 extends toward macOS/Windows. Slice 1 lifts PBS interpreter resolution to all five triples and adds a `workflow_dispatch` CI spike; per-OS venv staging and packaging remain open.

## Decision (slice 1)

- **`apps/lumogis-server/scripts/pbs-asset.sh`** — resolves `python-build-standalone` `install_only` tarball for all five `TARGET_TRIPLE` values (rejects unknown triples).
- **`refresh-pbs-sha256.sh`** — portable `sha256sum`/`shasum`; per-OS pin refresh on matching runner.
- **`server-build.yml`** — `spike-server-build-macos-windows` matrix (`workflow_dispatch` only); sidecar staging expected green; `stage-core-venv.sh` + dmg/nsis marked `continue-on-error`.

**Not shipped:** per-OS `stage-core-venv.sh`, offline BGE `.rerank()` proof, dmg/nsis packages (LUM-474), LUM-468 ticket closure.

## Consequences

- **Easier:** PBS naming uniform across triples; spike documents next blockers on real runners.
- **Harder:** Full cross-OS Server still blocked on native torch/BGE wheels + signing.

## Status history

- 2026-06-22: Slice 1 recorded by `/record-retro` — on `dev` @ `c1d30c1d4`. Parent LUM-468 remains **In Review**.
