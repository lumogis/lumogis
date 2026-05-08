# ADR 033: Plan ↔ Linear linkage (Cursor plans in devtools)

**Status:** Finalised  
**Created:** 2026-05-03  
**Last updated:** 2026-05-03  
**Decided by:** `.cursor/plans/LUM-9-plan-linear-linkage.plan.md` + `/verify-plan`  
**Finalised by:** /verify-plan 2026-05-03  
**Linear:** [LUM-9](https://linear.app/lumogis/issue/LUM-9/link-active-cursor-plans-to-linear-issues) (CSV **GOV-004**)  
**Plan:** `.cursor/plans/LUM-9-plan-linear-linkage.plan.md`  
**Exploration:** `.cursor/explorations/LUM-9-plan-linear-linkage.md`  
**Draft mirror:** `.cursor/adrs/plan_linear_linkage.md` (pointer to this file)

## Context

Team **LUM** is the active backlog (`cursor/backlog/linear-operating-model.md`). Cursor implementation plans in **`cursor/plans/*.plan.md`** (maintained in **lumogis-devtools**) must stay traceable to Linear issues so humans and automation can resolve **issue ↔ evidence** without duplicating backlog truth in git prose alone.

## Decision

1. **Evidence model:** Plans use **`evidence_role`** (`active`, `historical`, `coverage`, `superseded`, `repo-truth`) plus canonical **`linear_issue_id`** / **`linear_url`** where required (see plan §Classification rules and §Frontmatter shape).
2. **Grandfather registry:** Committed YAML **`cursor/reports/plan-linear-grandfather-registry.yaml`** holds **`metadata`** (`strict_after`, `linkage_phase`) and per-plan rows (**`linear_follow_up`** required; registry is authoritative on **`REGISTRY_DRIFT`** vs plan frontmatter).
3. **Read-only checker:** **`scripts/linear/check_plan_linear_linkage.mjs`** validates plans + registry (no Linear API, no network). Modes include **`--strict`**, **`--no-auto-strict`**, **`--require-classified`**, **`--json`** (see `scripts/linear/README.md`).
4. **Phasing:** **B2** (optional frontmatter backfill on historical/active where allowed) and **pre-commit/CI** are explicitly **out of this chunk** — tracked in the plan **Follow-up register**.

## Alternatives considered

- **Convention-only** — rejected as sole approach (drift).  
- **StrictDoc / ReqDB** — rejected for cost (per exploration).  
- **CI-only without registry** — rejected; would false-fail before classification exists.

## Consequences

- **Positive:** Machine-checkable Product OS compliance for plan files; aligns with **`/create-plan`** / **`/verify-plan`** frontmatter conventions.
- **Negative:** Ongoing governance edits when roles or LUM mappings change; optional fixtures directory from plan not implemented (**P2** test gap).

## Revisit conditions

- Linear workflow or frontmatter schema changes materially.  
- **`metadata.strict_after`** is set when the team enables default strict behaviour.  
- Navigator or export-based drift checks supersede or complement the checker.

## Status history

- **2026-05-03:** Finalised by /verify-plan — implementation confirmed: registry (**35** plans), checker, README; devtools checkpoint commit `chore: link Cursor plans to Linear evidence`; product repo receives this ADR only.
