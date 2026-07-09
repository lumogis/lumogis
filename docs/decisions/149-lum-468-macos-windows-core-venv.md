# ADR-149: macOS/Windows Core-venv staging + offline BGE proof — Apple-Silicon-only macOS (LUM-468)

**Status:** Finalised

**Created:** 2026-07-01

**Last updated:** 2026-07-01

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-07-01 (claude-opus-4-8)

**Issue:** [LUM-468](https://linear.app/lumogis/issue/LUM-468) — In Review (venv/BGE gate met on supported targets)

**Plan:** `cursor/plans/LUM-468-macos-windows-core-venv.plan.md` (devtools) — authored mid-flight; shipped via runner-driven iteration, not the formal verify cycle

**Exploration:** `.cursor/explorations/lum_468_macos_windows_core_venv_retro.md`

**Draft mirror:** `.cursor/adrs/lum_468_macos_windows_core_venv.md`

**Related:** [ADR-093](093-lum-466-core-debundle-delivery-model.md), [ADR-097](097-lum-470-pip-dependency-hash-pinning.md), [ADR-116](116-lum-492-server-build-ci.md), [ADR-124](124-lum-468-pbs-all-triples-slice1.md); epic LUM-519

## Context

ADR-124 (LUM-468 slice 1) lifted the `python-build-standalone` interpreter selector to all five triples and added a `workflow_dispatch` CI spike, but per-OS venv staging — the native torch/BGE wheel install plus the offline BGE cross-encoder `.rerank()` proof — remained open. This ADR records the as-shipped completion of that work: the bundled Core now stages a relocatable `core-venv/` and proves the offline reranker on each supported OS on real CI runners.

## Decision

The bundled Core (`apps/lumogis-server/`) supports **Linux x64/arm64 + Apple-Silicon macOS (`aarch64-apple-darwin`) + Windows x64 (`x86_64-pc-windows-msvc`)**. On each, `stage-core-venv.sh` extracts the pinned CPython, installs the per-OS hash-pinned lock (CPU torch), pre-stages the BGE reranker, and verifies an **offline `.rerank()` ordering gate on CPU** — capability parity by construction.

**Intel macOS (`x86_64-apple-darwin`) is unsupported (decision A):** PyTorch publishes no `x86_64`-macOS wheel past 2.2.2 (a NumPy-1-era build) while Core pins NumPy 2, so torch cannot import there (proven on a `macos-15-intel` runner: `Failed to initialize NumPy: _ARRAY_API not found`). It is rejected with a clear message in `stage-core-venv.sh` and `compile-bundled-core-lock.sh` and absent from the CI matrix.

Key cross-platform properties established:
- Per-OS interpreter layout (Windows `python/python.exe`; Unix `python/bin/python3`).
- Per-OS CPU torch source (linux/windows `+cpu` from the pytorch CPU index; macOS from PyPI, no `+cpu` tag).
- **CPU-only reranker** (`CrossEncoder(device="cpu")`) — macOS otherwise selects the MPS GPU backend and OOMs.
- Scripts are **bash 3.2 / BSD-userland safe** (no `mapfile`, no empty-array-under-`set -u`, `shasum` fallback for `sha256sum`).
- Per-OS locks are **minted on the matching runner** (native wheels hash per-platform) and uploaded as CI artifacts to commit into `orchestrator/locks-bundled/`.
- The `sidecar-exec` shim/wrap supervisor seam is **Linux-only** for now (macOS libc cross-build + Windows `.exe` launcher are LUM-474/LUM-396).

## Alternatives considered

- **Not chosen at ship time — Intel-Mac support via a `numpy<2` + `torch==2.2.2` profile:** would conflict with the NumPy-2 Core stack and diverge Intel Mac from every other target; rejected in favour of dropping Intel Mac (aligns with Apple's and PyTorch's own direction).
- **Not chosen — freeze/PyInstaller payload:** excluded by ADR-093 (relocatable on-disk venv is the delivery model).

## Consequences

- macOS (Apple Silicon) + Windows bundled Core stage a working offline reranker; Intel Macs are out of scope and installer/marketing must say Apple-Silicon-only for macOS.
- Staging scripts are now genuinely cross-platform; future edits must preserve bash-3.2/BSD portability.
- A full mac/Windows Server **installer** is still not buildable — sidecar (`sidecar-exec`, Qdrant-on-Windows) and dmg/nsis packaging remain open (LUM-396/LUM-474).

## Revisit conditions

- PyTorch resumes NumPy-2-compatible `x86_64`-macOS wheels → reconsider Intel-Mac support.
- Qdrant ships a native Windows binary → Windows sidecar leg can go green (LUM-396).
- `sidecar-exec` cross-builds on macOS / a Windows `.exe` launcher lands → extend the shim/wrap beyond Linux (LUM-474).

## Linear linkage (Product OS)

- **Existing issue:** LUM-468 (In Review) — the venv/BGE gate is met on supported targets; closure is Thomas's call via `/linear-update`.
- **New issue needed:** no for LUM-468. Out-of-scope findings map to LUM-396 (Qdrant-on-Windows) and LUM-474 (sidecar-exec macOS + tauri packaging).
- **Historical evidence only:** no.

## As-implemented surface

- Scripts: `apps/lumogis-server/scripts/{stage-core-venv,compile-bundled-core-lock,compute-bundled-lock-inputs-sha256,stage-reranker-model,check-core-venv-size}.sh`.
- Locks: `orchestrator/locks-bundled/<triple>.lock.txt` + `<triple>.pbs-sha256` (Linux committed; Windows/macOS-arm64 minted in CI, commit pending).
- CI: `.github/workflows/server-build.yml` — `workflow_dispatch` `triples` input over `windows-x64`,`macos-arm64`; hard gates on lock-mint + venv-stage.
- Seam: `apps/lumogis-server/src-tauri/src/bundled/paths.rs::core_venv_python()` remains the single venv-resolution point (ADR-093).

## Testing retrospective

- Verified by the `server-build.yml` per-OS CI spike (private repo): windows-x64 PASS (2.6 GB), macos-arm64 PASS (relocatable venv + offline BGE `.rerank` on CPU); macos-x64 FAIL → upstream (dropped). Linux validated locally to the sandbox network boundary.
- Follow-ups shipped on `dev` (`9c878c88c`, `9a321c32b`): `prove-core-venv-relocation.sh` hard gate; runtime reranker CPU-pin on macOS (`adapters/bge_reranker.py`); Qdrant Windows `.zip` archive selection (LUM-396 facet).
- Remaining gap: commit per-OS lock artifacts from CI uploads to `orchestrator/locks-bundled/` (operator/CI download).

## Status history

- 2026-07-01: Finalised by /record-retro (retrospective).
- 2026-07-02: Follow-up batch merged (`9c878c88c` relocation proof + CPU-pin; `9a321c32b` LUM-474 npm ci / macOS `prctl` gate).
