# ADR: What `orchestrator/tests/integration/` means
**Status:** Accepted
**Created:** 2026-07-30
**Last updated:** 2026-07-30
**Decided by:** claude-opus-5 (`/explore` LUM-753)

## Context

`orchestrator/tests/integration/` holds 33 modules and 124 test items, and nothing states what putting a test there promises.

`docs/testing/automated-test-strategy.md` names the directory exactly once, in a CI note. Its **Layers** table has an "Integration" row, but that row describes `make test-integration` → the **repo-root** `tests/integration` (HTTP against a live Core), a different directory. So `orchestrator/tests/integration/` is only ever executed as part of the **Orchestrator unit tests** layer, whose written contract is *"no requirement for Postgres/Qdrant/Ollama to be up on the host."*

With no enforced rule, the contents drifted in both directions at once. Measured on a laptop with no backends:

- **94 of 124 items (~76%) skip** — they need a backend the default lane does not provide (LUM-750, and LUM-720 for the CI equivalent).
- **30 items pass with no backend at all**, across six files — they need nothing, yet sit in a directory whose name claims otherwise (LUM-748).

Three tickets were filed about three consequences of the same silence, each proposing a local fix that a directory-wide rule would undo. One affected group carries a **privacy contract**: the cross-user isolation tests assert that one household member cannot retrieve another's documents, and LUM-720 records that they run on no watched gate.

Two further facts shaped the option space. The `TEST-COVERAGE-MATRIX-core.md` rows have **already stopped trusting the directory** — of 19 rows citing files under it, only 2 label the layer `integration`, and row 1.6.3 classifies one as `unit`. And LUM-309 (landed 2026-07-30) introduced a `paperless_live` marker deselected from `make test`, which is one answer to this question already adopted for a single lane.

## Decision

Membership of `orchestrator/tests/integration/` means **this test requires a real external backend**, and that rule is enforced mechanically rather than stated and hoped for: a `pytest_collection_modifyitems` hook in the directory's own `conftest.py` applies the `integration` marker to everything collected beneath it, so location and classification cannot drift apart again. The six files that need no backend move out — including a split of `test_household_sharing.py`, 15 of whose 18 tests are fake-backed. The rule is written into the strategy doc's Layers table, which currently has no row for this directory, and the matrix `layer` vocabulary is aligned with the marker names.

Existing mechanisms are kept unchanged: the `_pgguard` skip-on-laptop / fail-when-promised guard (LUM-718) and the `pytest_terminal_summary` skip reporter both stay, because they already do the right thing.

### The four decisions

Resolved 2026-07-30, after the exploration; these replace the open questions it carried.

**1. The directory keeps its name.** No rename. In-process tests against real Postgres and Qdrant *are* integration tests by any standard definition — the name is accurate, and what was broken is that membership was never enforced. Fix membership and the name becomes true; renaming would paper over the real defect while appearing to fix it. It would also bundle mechanical path-churn across 19 matrix rows into the same diff as the semantic change, making neither reviewable. **If the collision still bites afterwards, the wrongly-named directory is the other one:** the repo-root `tests/integration/` (22 modules) drives HTTP against a *running* Core via `LUMOGIS_API_URL` / `httpx.Client(base_url=…)` — an API/e2e smoke lane. Renaming *that* is the accurate fix, and is a separate follow-up.

**2. `make test` keeps collecting the directory. Deselect none.** This is the decisive answer to LUM-750, and it takes that ticket's second option. The `pytest_terminal_summary` reporter is the only standing signal that this coverage is not running; deselecting makes the suite quieter and less honest, which is the exact trade LUM-718 was built to reverse. The skip count is a metric to drive down, which is what LUM-720's "Done looks like" already says. Decisive factor: LUM-726 means CI runs nothing on `dev`, so deselecting would leave **no surface anywhere** that these tests exist. Cost of keeping them is ~14s and one summary line.

**3. `paperless_live` keeps its marker and loses its deselection.** LUM-309 added `-m "not paperless_live"` to `make test` and `compose-test`. Under decision 2 that is inconsistent — those tests need a compose fixture exactly like the other 16 `*_live.py` modules, none of which are deselected, so the paperless lane was made an outlier. The principle: **a marker is for the gate to select with, never for hiding from the default lane.** The marker stays so `make compose-test-paperless` can target it; the deselection goes, and the tests skip and are counted like every other live test. They already skip cleanly with no fixture present (verified).

**4. `test_household_sharing.py` moves wholesale; its three stubs are deleted.** *(Amended 2026-07-30 after review — the original text, preserved below, rested on a premise two reviewers disproved.)* All three "skipped" tests were unconditional `@pytest.mark.skip` decorators on `pass` bodies: no fixtures, no I/O, nothing that could assert. There were no backend-dependent tests to split out. They are deleted and the §2.15 coverage gap is tracked as LUM-754. **Superseded original reasoning:** The file is 83% hermetic, so relocating three tests is far less churn than relocating fifteen. Splitting beats exempting because an exemption is a precedent with no stopping rule. **But the three are not homogeneous**, and that changes the work: §2.15 backup/restore needs live Postgres, §2.15 entity-relations needs the lumogis-graph service plus FalkorDB — while the §2.15 MCP `memory.get_recent` harness skips because a fixture *was never wired* ("tracked separately"). Only two are backend problems. The third is an unfinished test wearing a skip as a disguise — the same defect family as LUM-748, one layer down. It does not move into the integration tree; it becomes an `xfail` with a tracked issue, or is deleted.

## Alternatives Considered

- **Rename to `orchestrator/tests/live/`** — ends the genuine name collision with the repo-root `tests/integration`, but changes nothing about membership; a no-backend test dropped in next week is just as wrong. Folded into the decision as an open question rather than adopted alone.
- **Markers only, applied by hand** — matches how the matrix already behaves, but hand-applied markers are exactly what drifts. This is the recommended option minus the enforcement, and enforcement is the load-bearing part.
- **Make the matrix `layer` column authoritative and leave the tree alone** — already true in practice, but the matrix is prose maintained at closure time; `pytest` cannot select on it, so LUM-720 gains nothing. Adopted as a complement, not the answer.
- **Document the directory as deliberately mixed** — would close LUM-750 by fiat while leaving the privacy-contract tests ungated, and legitimise the very property that made LUM-748 possible.
- **`pytest-error-for-skips`, `pytest-integration`, `pytest-external-blockers`** — new dependencies for a rule expressible in a few lines of `conftest.py`; a blanket skips-are-failures policy would also break the laptop workflow the guards exist to protect.

Full comparison: `.cursor/explorations/LUM-753-integration-test-taxonomy.md`.

## Consequences

**Easier.** A test's location becomes evidence again: if it is in that directory, it met a real backend, and if it skipped, the reporter says so. LUM-720 gets a marker to select on instead of a path. LUM-748 and LUM-750 collapse from independent judgement calls into mechanical consequences of one rule. LUM-309's `paperless_live` stops being a private convention the next connector copies or ignores at random.

**Harder.** A test that is only partly hermetic must now be split rather than filed under a hedge — `test_household_sharing.py` is the first case and will not be the last. Nineteen matrix rows and `docs/LUMOGIS_REFERENCE_MANUAL.md:218` cite these paths, so any rename multiplies the churn.

**Future chunks must know.** New live lanes follow the rule and inherit the marker automatically; they should not invent a per-lane marker. Anything relying on `orchestrator/tests/integration/` being runnable without services will break by design — that is the point.

**Binds locally on `dev`, and in CI on the release line — by design.** CI runs on the **`dev` → private `main` RC promotion**, not on `dev` pushes, because **Actions minutes are a hard budget** (`ci.yml` restricts to `branches: [main, master]` for that stated reason). So this rule is checked by `make test` on every developer machine and again at the RC gate.

*Corrected 2026-07-31.* An earlier revision of this ADR called that a gap and recommended LUM-726 land first to "make the others bind". That was wrong: it read an absent `dev` trigger as an oversight rather than a deliberate constraint, and the recommendation would have spent minutes the project does not have. LUM-726 **documents** the model; it is not a prerequisite for this one. The check being wired into `make test` — rather than into a CI job — is what makes it bind under the real constraint, and that is now the design rather than a consolation.

The same mistake shipped in `paperless-integration.yml`, which was written with `paths:` but no branch filter and therefore fired on `dev` pushes twice (~11 minutes) before it was caught. Fixed; branch filters restored, weekly cron removed.

**LUM-720 is deliberately not blocked on this.** It gates a privacy contract — cross-user isolation with no watched gate, the LUM-305 defect class — and its service-container work is valid whichever way the taxonomy lands; only the selector changes, which is a one-line edit. The block edge registered during `/explore` was removed on 2026-07-30 for that reason. Blocks remain on LUM-748 and LUM-750, where the dependency is genuine.

## Revisit conditions

- **If the repo-root `tests/integration` is retired or merged**, the name collision disappears and the rename question (open question 1) should be closed rather than carried.
- **If a third mixed-hermeticity file appears** after `test_household_sharing.py`, the "split, never exempt" stance needs re-argument — two instances is a pattern, and the cost may exceed the honesty gained.
- **If LUM-726 lands a `dev` CI lane**, revisit whether `make test` should keep collecting the directory (open question 2), since the skip reporter's signal would then have a gate behind it.
- **If `config._instances` autouse injection in `orchestrator/tests/conftest.py` is removed or reordered**, the mechanism that let fakes masquerade as integration coverage changes shape, and the enforcement hook should be re-checked against the new arrangement.

## Status history
- 2026-07-30: Draft created by `/explore` (LUM-753)
- 2026-07-30: Two adversarial critiques (fable-5, sonnet-5) disproved Decision 4's factual premise — the three "backend-dependent" tests were `pass`-body stubs. Decision 4 amended: no split, stubs deleted, gap tracked as LUM-754. A second check (no unconditional skips) was added, because the first check was structurally blind to exactly that class.
- 2026-07-30: **Accepted** — implemented and verified on `dev` (`d95a00d7b`, `0e7d3f30a`).
