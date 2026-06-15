# ADR-097: Bundled Core venv pip hash-pinning (LUM-470)

**Status:** Finalised
**Created:** 2026-06-13
**Last updated:** 2026-06-14 (Docker image build-time dependency pinning accepted gap documented)
**Decided by:** `/explore LUM-470` + `/review-plan --arbitrate` R1 + `/verify-plan` reconcile; LUM-480 amend at `/verify-plan`
**Plan:** `.cursor/plans/LUM-470-pip-dependency-hash-pinning.plan.md` (archived); LUM-480 amend: `.cursor/plans/archived/LUM-480-mandatory-pbs-sha256-pin.plan.md`
**Exploration:** `.cursor/explorations/LUM-470-pip-dependency-hash-pinning.md`
**Draft mirror:** `.cursor/adrs/LUM-470-pip-dependency-hash-pinning.md` (superseded pointer — this `docs/decisions/097` file is canonical)
**Parent:** ADR-093 (LUM-466 Core debundle delivery)

## Context

LUM-466 Phase 1 (ADR **093**) stages a full Core Python environment for the bundled appliance using `python-build-standalone` and unpinned `pip install -r orchestrator/requirements.txt`. CPython and the BGE model revision are pinned; **PyPI packages are not**, leaving a supply-chain gap. LUM-470 closes the pip gap with per-platform hashed locks and `pip install --require-hashes --no-deps`. **LUM-480** closes the remaining interpreter tarball gap with a committed per-triple sha256 pin verified on every download.

CPU-only `torch` must come from `download.pytorch.org/whl/cpu`, never the default PyPI CUDA build. Two facts shaped the as-built design:

1. **`uv pip compile` omits find-links** unless **`--emit-find-links`** is set, and it emits hashes for *both* the PyPI (CUDA) and CPU `torch` wheels in its multi-index closure.
2. **pip consults PyPI before a `--find-links` location.** A single self-contained `pip install --require-hashes -r <lock>` therefore lets pip prefer the PyPI CUDA `torch` wheel; under `--require-hashes` that either installs the wrong (CUDA) build or aborts on a hash that does not match the intended CPU wheel. `--find-links` alone does **not** force the CPU build.

These two facts are why the implementation diverged from the originally-planned single self-contained `--require-hashes` install (see **As-built divergence** below).

## Decision

Adopt **`uv pip compile --generate-hashes --emit-find-links --python-version 3.12`** as the **build-time-only** lock generator, storing **per-target-triple hashed lock files** under **`orchestrator/locks-bundled/`** (Core-owned; **stripped from public AGPL export**), compiled from **`orchestrator/requirements-bundled.in`** (`-r requirements.txt` + `torch` with `--find-links` to the CPU index). On a mixed PyPI + pytorch index resolution failure the compile retries with **`--index-strategy unsafe-best-match`**.

**Compile-time torch narrowing (load-bearing):** after `uv pip compile`, `compile-bundled-core-lock.sh` downloads the CPU `torch` wheel from `--index-url https://download.pytorch.org/whl/cpu` (`--only-binary=:all: --no-deps`), computes its hash with `pip hash`, and **rewrites the torch stanza to a single `torch==<version>` requirement pinned to that one CPU-wheel hash**. The committed lock therefore records `torch==2.12.0` (no `+cpu` local segment on the requirement line) bound to exactly the CPU wheel's hash, so a hashed install cannot resolve to a CUDA wheel.

**Two-step install (as-built):** `stage-core-venv.sh` splits the committed lock into (a) the `torch==` stanza and (b) the remainder, then runs **two** hashed installs:

1. `"${PY}" -m pip install --quiet --require-hashes --no-deps --index-url https://download.pytorch.org/whl/cpu -r <torch-stanza>` — torch first, from the CPU index, hash from the committed lock.
2. `"${PY}" -m pip install --quiet --require-hashes --no-deps -r <remainder>` — everything else (the remainder still carries the lock's `--find-links` header line).

A post-install guard asserts `torch.__version__` contains `+cpu` and aborts otherwise. **Both** installs pass `--require-hashes --no-deps`; there is **no** bare/unhashed `pip install torch` and **no** `pip install -U pip` on the hashed path.

**`--emit-find-links` and the `--find-links` grep guards remain load-bearing** even though the torch-first `--index-url` step (not `--find-links`) is what now forces the CPU build at install time. They enforce the lock **format contract** asserted by both the compile post-guard and `check-bundled-core-lock.sh` (lock must contain `^--find-links https://download.pytorch.org/whl/cpu$`), document CPU-index provenance, and keep the lock valid for the LUM-468 per-OS extension. Removing them would require relaxing two fail-closed guards; they are kept deliberately.

**Binding fail-closed gate:** `server-stage-core-venv` depends on **`server-check-core-lock`** in `Makefile.server.mk` (local verify; bash-only, no uv). `hub-build.yml (retired; see deprecated/lumogis-hub-fused/)` includes the same check as a **dormant** step (path triggers wired) until Actions quota is restored.

Build uv pin: **`apps/lumogis-server/scripts/requirements-appliance-build.txt`** (`uv==0.11.21`, ≥ the CVE-2025-13327 fix in `uv 0.9.6`).

**Fallback:** pip-tools `pip-compile --generate-hashes` with identical on-disk contract if uv is blocked.

### CPython tarball pin (LUM-480)

The **python-build-standalone** CPython tarball is a separate supply-chain surface from pip wheels. **LUM-480** commits a per-target-triple sha256 pin beside the pip lock under **`orchestrator/locks-bundled/<triple>.pbs-sha256`** (single lowercase hex line). **`apps/lumogis-server/scripts/pbs-asset.sh`** is the sole source for the PBS asset name and download URL (shared by **`stage-core-venv.sh`** and **`refresh-pbs-sha256.sh`**).

**Mandatory fail-closed verify:** `stage-core-venv.sh` **errors** if the pin file is missing or invalid (64-char hex); after `curl` download it **always** runs `sha256sum -c` against the committed pin. The optional `PBS_SHA256` env bypass is **removed** — the committed file is the only source.

**Binding gate:** `server-check-core-lock` / `check-bundled-core-lock.sh` require a valid `.pbs-sha256` before staging. **`server-refresh-pbs-pin`** (`Makefile.server.mk`) regenerates the pin via curl + sha256sum when `PYTHON_VERSION` or `PYTHON_BUILD_STANDALONE_VERSION` changes.

**Rebuild-skip contract:** `manifest.json` records `pbs_sha256` and `python_build_standalone`; staging skips rebuild only when lock, pip inputs, python version, PBS pin, and PBS release tag all match.

**v1 scope:** `x86_64-unknown-linux-gnu` only. macOS/Windows pins ship with **LUM-468** / **LUM-474** on each target OS. Docker Compose interpreter path remains unpinned.

## As-built divergence (recorded at `/verify-plan`)

The plan and the original draft ADR specified a **single** self-contained `pip install --require-hashes --no-deps -r <lock>` with torch resolved purely via the lock's `--find-links` line, and removal of the `TORCH_CPU_INDEX` override. Implementation revealed that pip's PyPI-before-find-links ordering selects the CUDA `torch` wheel under that shape. The accepted, verified divergence is:

- **Reintroduced** an explicit CPU index for torch (`TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"`) used **only** with `--require-hashes --no-deps` against the lock-derived torch stanza.
- **Two hashed installs** (torch-first, then remainder) instead of one.

Intent is fully preserved: every package, including torch, installs only with `--require-hashes` from the committed Core-owned lock. The divergence changes *how* the CPU build is forced, not *whether* installs are hash-pinned.

## Alternatives considered

- **uv.lock + uv sync** — rejected; over-scoped without improving staged-pip install.
- **pip wheel vendoring** — rejected; size + duplicate hash benefits.
- **pip freeze** — rejected; no hashes.
- **PEP 751 pylock.toml** — deferred; immature for pytorch multi-index.

## Consequences

**Easier:** tamper-evident bundled Core venv (interpreter tarball + pip packages, torch included); clear regen when `requirements-core.txt` or PBS release changes; LUM-468 inherits per-platform layout for both pip locks and PBS pins.

**Harder:** locks and PBS pins regenerated on target OS; lock/pin PRs need human security review; Docker path stays unpinned until a follow-up; the two-step install adds a torch-stanza split step and depends on the compile-time CPU-hash narrowing staying in `compile-bundled-core-lock.sh`; PBS pin must be refreshed in lockstep with `PYTHON_VERSION` / `PYTHON_BUILD_STANDALONE_VERSION` bumps.

## Public/private boundary

Hashed locks and `requirements-bundled.in` are **private build artefacts** stripped from the AGPL export. Public corresponding source remains `orchestrator/` + unpinned `requirements.txt` / `requirements-core.txt` via Docker Compose. Supply-chain guarantees apply to the **signed distributed appliance binary**, not to public AGPL rebuilds.

## Known gap: Docker image build-time dependency pinning (accepted)

The hash-pinning in LUM-470 / LUM-480 covers the bundled/native Core venv only. The Docker Compose path (`orchestrator/Dockerfile`) still installs from the unpinned `>=` ranges in `requirements.txt`, so the dependencies baked into the public image are not hash-verified at build time.

This is an accepted gap, not an oversight:

- The image is built only in controlled CI from verified public `main` (LUM-225), not from arbitrary input.
- Published GHCR images carry SLSA provenance attestation (LUM-228), so a consumer can verify an image was built by the expected workflow from the expected source commit. That is tamper-evidence at the distribution layer.
- The residual window is narrow: a build-time dependency swap would require PyPI serving a malicious in-range version during the CI build. Attestation attests whatever was built, so it would not catch that one case, but the exposure is small and the build is controlled.

Closing it would mean `--require-hashes` in the Dockerfile against a Docker-specific lock — a different dependency surface from the bundled venv, so its own lock, not the shared one. That is defence-in-depth on top of attestation plus a controlled build, not a structural hole. Out of scope for v1.0 and untracked. Revisit only if the public Docker supply-chain story is wanted at parity with the shipped appliance, at which point it becomes its own work.

## Status history

- 2026-06-13: Draft in `.cursor/adrs/` from `/explore LUM-470`
- 2026-06-13: Revised at `/review-plan --arbitrate` R1 (`--emit-find-links`, public strip)
- 2026-06-13: Implemented at `/implement LUM-470`
- 2026-06-13: Reconciled at `/verify-plan` — recorded the as-built **two-step** install (torch-first from the CPU index, then remainder; both `--require-hashes`) and the compile-time CPU-hash narrowing. The single self-contained `--require-hashes` install in the original decision was abandoned because pip prefers the PyPI CUDA `torch` wheel ahead of `--find-links`.
- 2026-06-13: Amended at `/verify-plan` **LUM-480** — committed per-triple **CPython tarball sha256 pin** (`orchestrator/locks-bundled/<triple>.pbs-sha256`), mandatory `sha256sum -c` in `stage-core-venv.sh`, shared `pbs-asset.sh`, `server-refresh-pbs-pin`, extended `server-check-core-lock` / `hub-test-locks`. **Status** finalised.
- 2026-06-14: Documented accepted gap — Docker image build-time pip installs remain unpinned (`orchestrator/Dockerfile`); mitigated by controlled CI (LUM-225) and SLSA attestation (LUM-228); Docker-specific lock deferred.
