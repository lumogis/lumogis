# ADR 084: CONTRIBUTING-BEGINNERS public export onboarding (LUM-378)

**Status:** Finalised
**Created:** 2026-06-06
**Last updated:** 2026-06-06
**Decided by:** /verify-plan LUM-378 (Composer)
**Plan:** `.cursor/plans/LUM-378-contributing-beginners.plan.md`
**Exploration:** `.cursor/explorations/LUM-378-contributing-beginners.md`
**Draft mirror:** `.cursor/adrs/LUM-378-contributing-beginners.md`

## Context

First-time contributors to `lumogis/lumogis` need gentler onboarding than `CONTRIBUTING.md` and a copy-paste prompt for Cursor/Codex-style agents. Parent programme **LUM-163** closed core contributor infra; **LUM-378** ships the deferred beginners slice. **LUM-376** already substitutes public-safe agent docs via `scripts/create-upstream-export-tree.sh` + `scripts/check-public-export.sh` — but not human step-by-step setup or a fenced agent prompt.

The private `CONTRIBUTING.md` § *How to write a new extractor* had drifted from the live `@extractor(".ext")` registration in `orchestrator/config.py` (`extractor()` / `get_extractors()` auto-import of `adapters/` modules; canonical example `orchestrator/adapters/pdf_extractor.py`).

## Decision

1. **Ship `CONTRIBUTING-BEGINNERS.md`** as a maintainer template at `docs/public-export/CONTRIBUTING-BEGINNERS.md`, copied to the **repository root** in the AGPL export tree by `scripts/create-upstream-export-tree.sh`, with **presence and forbidden-pattern checks** in `scripts/check-public-export.sh` (LUM-378 contract block).
2. **No** tracked root `CONTRIBUTING-BEGINNERS.md` or symlink in the private product tree — root copy exists only in export output; mechanics documented in `docs/public-export/README.md` (stripped on export).
3. **Cross-link** from `CONTRIBUTING.md` (public-safe beginners line + Public CI parity entry) and `README.md` (one-line pointer). **LUM-180** must preserve the README link during HN polish.
4. **Rewrite** `CONTRIBUTING.md` § *How to write a new extractor* to document `@extractor(".ext")` from `config`, minimal epub example, and `pdf_extractor.py` as reference — docs-only; no runtime changes.
5. **Paste-prompt fresh-session PoC** remains a **human acceptance gate** before Linear **Done** (recorded PENDING-MANUAL at verify).

### Forbidden content (export contract)

Shared with LUM-376 plus `githubusercontent` / `raw.githubusercontent.com`: `lumogis-app`, `lumogis-devtools`, `LUMOGIS_CONTEXT_PACK`, `linear.app`, `/linear-update`, `/update-context-pack`, `/navigator`, `.cursor/skills`, `Product OS`.

### Paste prompt contract

- Local paths primary: `CONTRIBUTING-BEGINNERS.md`, `CONTRIBUTING.md`, `AGENTS.md`, `docs/LUMOGIS_AGENT_ORIENTATION.md`, `ARCHITECTURE.md`.
- Only remote URL: GitHub good-first-issue label query.
- No private maintainer doc references in the fenced block.

## Alternatives considered

- Root-only public file without template gate — rejected (leakage risk).
- Fold into `LUMOGIS_AGENT_ORIENTATION.md` — wrong audience.
- `contributing-ai-agents.md` patch only — fails separate-file acceptance.
- External wiki — off-repo drift.

Full comparison: `.cursor/explorations/LUM-378-contributing-beginners.md`

## Consequences

**Easier:**

- HN and good-first-issue contributors get a single entry point and agent prompt.
- Export pipeline enforces the same public/private boundary as LUM-376.
- CONTRIBUTING extractor guidance matches live code.

**Harder:**

- Export script, check script, and pytest export tests gain another required path.
- `/update-public-export` skill substitution table must add beginners row (fold into LUM-378 closure — no new issue).

**Future chunks must know:**

- Beginners content is **export-templated**, not edited only on public `main`.
- `create-upstream-export-tree.sh` builds file bodies from **git HEAD** (`read-tree` + `checkout-index`); `docs/public-export/` templates are overlaid from the working tree. Commit product changes before export verification reflects staged `CONTRIBUTING.md` edits.

## Revisit conditions

- GitHub community standard for contributor agent prompts supersedes fenced markdown — revisit prompt format only.
- Export stops using substitution (byte mirror) — collapse template to root authoring.
- Good-first-issue workflow moves off GitHub Issues — update links and prompt.

## Status history

- 2026-06-06: Draft created by /explore LUM-378
- 2026-06-06: Finalised by /verify-plan LUM-378 (export tests green; paste-prompt PoC PENDING-MANUAL)
