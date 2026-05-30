# ADR-063: Stack-control pytest parity on CI and documented venv (LUM-248 closure)

**Status:** Finalised
**Created:** 2026-05-23
**Last updated:** 2026-05-23
**Decided by:** `/verify-plan --headless` (evidence closure) for **LUM-248**

## Context

**LUM-248** asked for **stack-control** unit tests to run **in CI** **or** via a **documented local venv**, so claims about test parity in the Makefile and contributor docs remain honest when `pytest` is not on a bare host `PATH`.

Constraints:

- The orchestrator **runtime** image deliberately excludes **pytest**; parity is not achieved by bloating runtime images.
- Acceptance is **either-or**: CI coverage **or** documented venv (both may exist).

## Decision

**Close LUM-248 as already satisfied — no product code change required for this ticket.**

Evidence at verification time (worktree **`0d3cfced75f610c5fb007dfa1265943c22a2522f`** on branch **`agent/lum-248`**):

1. **CI —** `.github/workflows/ci.yml` job **`lint-and-test`** installs `stack-control/requirements-dev.txt`, then runs `cd stack-control && python -m pytest test_main.py -q` on every **pull_request** and **push** to **`main`** / **`master`** (after orchestrator install + lint + orchestrator tests).
2. **Host venv —** `Makefile` target **`test:`** runs the orchestrator suite, then the same stack-control command via **`$(PYTHON)`** (default **`python3`**). **`CONTRIBUTING.md`** § *Python dependencies for tests and lint* and § *Running tests (local venv)* document installing **`stack-control/requirements-dev.txt`** and invoking **`make test`** (orchestrator + stack-control).
3. **Docker without a host venv —** **`make compose-test-stack-control`** mounts **`stack-control/`** into the orchestrator service container, installs **`/sc/requirements-dev.txt`**, and runs **`pytest test_main.py`**. This target is defined in the **`Makefile`**; **`CONTRIBUTING.md`** § *Running tests (Docker only, no local venv)* documents **`make compose-test`** for orchestrator tests and points readers to other compose targets via the **`Makefile`** — it does **not** spell out **`compose-test-stack-control`** by name (optional discoverability polish → contributor-docs backlog, e.g. **LUM-321**).

Residual contributor symptom — **`pytest` missing** when someone runs **`make test`** without installing dev deps first — is **onboarding / UX**, not missing **either-or** acceptance for LUM-248. Optional **`make test`** preflight messaging is explicitly out of scope here; track under **LUM-321** or a small **P3** if desired.

## Alternatives considered

- **Makefile preflight when `pytest` is missing** — deferred; different acceptance than LUM-248’s parity criterion (**LUM-321** / child **P3**).
- **Dedicated path-gated CI job for stack-control only** — rejected: unnecessary CI surface; current step is cheap and already unconditional inside **`lint-and-test`**.
- **Bake pytest into the runtime image** — rejected (runtime stays thin).

## Consequences

**Easier**

- LUM-248 closes on durable repo evidence (CI + Makefile + CONTRIBUTING) without rework.
- Future tickets should cite this ADR rather than re-litigating the same parity question.

**Harder / unchanged**

- Contributors who skip the documented venv install still see **`No module named pytest`** (or similar) until they follow **`CONTRIBUTING.md`**.

**Future work must preserve**

- An **unconditional** stack-control pytest step in default CI (or an explicit ADR if **`lint-and-test`** is split/gated).
- Runtime images without test-only tooling unless an ADR changes that posture.

## Revisit conditions

Revisit if any of the following become true:

- Stack-control tests require services not present on **`ubuntu-latest`** (may justify compose-based CI or a gated job).
- **`lint-and-test`** is removed or path-gated in a way that drops stack-control coverage.
- **`CONTRIBUTING.md`** no longer documents the venv path for **`make test`**.
- Reports show **`make test`** still fails after following documented install steps.

## Status history

- 2026-05-23: Draft rationale in **`.cursor/adrs/LUM-248-stack-control-pytest-host-ci.md`** (exploration).
- 2026-05-23: **Finalised** as **ADR-063** by **`/verify-plan --headless` LUM-248** — implementation on disk matches decision; exploration **§Codebase Findings** sentence that **`CONTRIBUTING.md`** names **`make compose-test-stack-control`** was **not** carried forward (that line was inaccurate vs shipped **`CONTRIBUTING.md`**).
- 2026-05-30: **LUM-326** closed the optional discoverability gap — **`CONTRIBUTING.md`** § *Running tests (Docker only, no local venv)* now documents **`make compose-test-stack-control`**; reference manual §15 commands table updated.
