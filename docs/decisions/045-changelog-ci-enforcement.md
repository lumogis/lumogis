# ADR-045: CHANGELOG.md PR-time CI enforcement

**Status:** Finalised
**Created:** 2026-05-15
**Finalised:** 2026-05-15 (`/verify-plan` LUM-193 — implementation confirmed)
**Decided by:** Exploration LUM-193 + plan `LUM-193-changelog-ci-enforcement`; `/review-plan` arbitrate R1 2026-05-15

## Context

`CHANGELOG.md` exists at the repository root in Keep a Changelog form and ships into the public export tree. Contributors had no automated signal when product paths changed without a changelog edit. **LUM-193** mandates a CI check with maintainer bypasses (**`Skip-Changelog`** label and **`[skip changelog]`** in the PR body) and documentation in **`CONTRIBUTING.md`**.

Constraints include AGPL-friendly third-party tooling, preserving curated changelog narrative (no commit-derived replacement), sensible path scopes, **`pull_request`** (not **`pull_request_target`**) posture, and private-**first** rollout with optional mirroring to the public repo after **LUM-227** CODEOWNERS work.

## Decision

1. **Workflow:** `.github/workflows/changelog.yml` on **`pull_request`** to **`dev`**, **`main`**, and **`master`**, scoped with workflow-level **`paths`** (OR semantics across globs). Third-party **`dangoslen/changelog-enforcer`** pin is **immutable by digest**, wrapped by a **`grep -qiF '[skip changelog]'`** step that reads the PR body **only via an `env:` indirection** (never interpolated into `run:`). **`permissions:`** restrict `contents` and `pull-requests` to **read**; **`concurrency`** groups per PR; job **`timeout-minutes: 5`**.
2. **Mock-capability carve-out:** GitHub Actions does not support pairing top-level **`paths`** with **`paths-ignore`** on the same event; **`lumogis-app`** ships the carve-out as a **negated path** **`!services/lumogis-mock-capability/**`** inside the `paths` filter. Local **`make changelog-check`** mirrors semantics via **`scripts/changelog-gate-paths.txt`** plus logic in **`scripts/check-changelog-touched.sh`** that skips when **only** that subtree triggers.
3. **Contributor docs:** **`CONTRIBUTING.md`** documents when `CHANGELOG.md` must appear in the PR diff, bypasses, fork **first-run workflow approval**, the **workflow-level paths + required check** caveats, and public vs private enforcement lag until mirror work lands.
4. **README:** Adds a **`CHANGELOG.md`** shortcut on the primary nav line.
5. **Lint:** The same workflow file runs **`rhysd/actionlint`** via digest pin against **`changelog.yml`** (minimal surface).
6. **Follow-up:** Mirror the workflow file into **`lumogis/lumogis`** when **LUM-227** clears CODEOWNERS for public **`.github/workflows/`** — tracked as Linear child outcome under **LUM-193** + **LUM-227**.

## Alternatives considered

See `.cursor/explorations/LUM-193-changelog_ci_enforcement.md` — summary: **`towncrier`** fragments (defer), custom **`dorny/paths-filter`** shim (defer), **`release-please`** (reject for narrative churn), Makefile-only enforcement (reject vs explicit CI criterion).

## Consequences

**Easier:**

- Contributors get a deterministic PR failure when product paths ship without changelog evidence in the GitHub/Git diff contract enforced by **`changelog-enforcer`**.
- **One** YAML workflow plus **CONTRIBUTING.md** (+ optional **`make changelog-check`**) aligns local and CI intuition when paths stay synced.

**Harder:**

- Maintainer bypass visibility (`Skip-Changelog` / body text).
- Operational awareness: workflow-level **`paths`** + required branch protection remains a documented footgun until an optional job-level **`paths-filter`** + always-green reporting lands (future chunk).

## Revisit conditions

Captured in `.cursor/adrs/changelog_ci_enforcement.md` **Revisit conditions** (towncrier at higher cadence, action deprecation → custom script, conventional commits mandate, new top-level surfaces outside globs).

## Status history

- **2026-05-15:** Draft in `.cursor/adrs/changelog_ci_enforcement.md` (exploration + plan R1).
- **2026-05-15:** Finalised — `docs/decisions/045-changelog-ci-enforcement.md` (`/verify-plan` LUM-193).
