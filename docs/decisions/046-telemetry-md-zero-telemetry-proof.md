# ADR 046: TELEMETRY.md zero-telemetry proof and Makefile guard

> Status: Active (numbering conflict)
> Last reviewed: 2026-05-24
> Verified against commit: 50f43b8
> Notes: **`046-lum-35-fp017-per-user-backup-followups.md`** also claims **ADR 046** in its title. Resolve by renumbering one document and sweeping references. Filename prefixes **053–064** are already taken (several duplicate clusters, including **061** / **063** / **064**). Pick a **non-colliding** new slug (for example **`065-*.md`**) when renumbering—coordinate with any **`034-linear-evidence-index.md`** rename in the same pass—see `docs/_librarian/docs-inventory.md` and `docs/_librarian/2026-05-24-docs-librarian-report.md`.

**Status:** Finalised
**Created:** 2026-05-16
**Last updated:** 2026-05-16
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-16 (GPT-5.2-codex)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/telemetry_md_zero_telemetry_proof_retro.md`
**Draft mirror:** `.cursor/adrs/telemetry_md_zero_telemetry_proof.md`

## Context

Self-hosted and AGPL audiences need a **durable, cloneable** statement that Lumogis **does not implement** third-party product analytics in Core, plus a **lightweight** way to re-check the orchestrator tree. Work shipped under **LUM-217** (Public AGPL Release programme; parent **LUM-163**) without a prior **`/create-plan`** file for this slice.

**Evidence:** public **`lumogis/lumogis`** commit **`9049d62`** (branch `lum-217-telemetrymd-zero-telemetry-proof-document-with-verification`); private **`lumogis-app`** **`dev`** commit **`c612880cd`** (manual port of **`TELEMETRY.md`**, **`Makefile`** guard, **`SECURITY.md`** link). Public-only in that commit: **`README.md`** TELEMETRY pointer.

## Decision

1. Publish **`TELEMETRY.md`** at the **repository root** describing the **zero third-party analytics** posture (proof framing, manual verification including AGPL source location, optional network observation, and explicit carve-outs for **operator-chosen** cloud LLM APIs and **Tailscale** coordination behaviour).
2. Add **`make verify-no-telemetry`**: fail if **`grep -r "posthog\|mixpanel\|amplitude" orchestrator/`** finds matches; otherwise print **`OK`** and succeed. Register the target as **`.PHONY`**. The automated pattern **does not** include bare **`segment`** (avoids false positives from unrelated “segment” English in STT and path handling); human steps in **`TELEMETRY.md`** may still suggest a broader search.
3. Cross-link from **`SECURITY.md`** (after **Security Design Notes**) to **`TELEMETRY.md`** for telemetry policy and verification.

**Explicit non-goals for this ADR’s slice:** no changes to orchestrator runtime, database schema, auth, or web client behaviour; no new outbound endpoints; no README change on private **`dev`** in **`c612880cd`** (public README carried the extra link).

### As-implemented surface (verified 2026-05-16)

- **`TELEMETRY.md`** — sections: *Lumogis collects nothing*, *How to verify*, *What does leave your machine*, *Comparison* table.
- **`Makefile`** — **`.PHONY`** includes **`verify-no-telemetry`**; recipe placed after **`changelog-check`**, before **`compose-lint`** on private **`dev`**.
- **`SECURITY.md`** — line after Security Design Notes: `→ See [TELEMETRY.md](TELEMETRY.md) for telemetry policy and verification.`

## Alternatives considered

- **Cherry-pick only** — Private **`Makefile`** / **`SECURITY.md`** had diverged from public; a straight cherry-pick conflicted; **manual port** was chosen for **`lumogis-app`**.
- **Depend only on prose (no Makefile guard)** — Simpler but weaker reproducibility; rejected for this slice.
- **Broaden grep to `segment`/`analytics`** — Rejected for **automation**: high false-positive rate against existing Core wording; narrower guard + richer **human** grep in **`TELEMETRY.md`** balances signal and noise.

## Consequences

- Operators and auditors can **`make verify-no-telemetry`** after clone; failures imply an intentional or accidental **`orchestrator/`** analytics SDK string regression.
- **Future** **`/create-plan`** work that touches outbound telemetry or analytics **must** update **`TELEMETRY.md`** and this guard (**or** formally supersede ADR **046**).
- **Portfolio / Linear:** **LUM-217** remains the active issue for programme closure; this ADR is the **repo evidence** anchor.

## Revisit conditions

- **Guard false positive or false negative** discovered in practice → adjust patterns and document in **`TELEMETRY.md`** + **Status history** here.
- **Product direction** changes to allow first-party telemetry → supersede this ADR with explicit collection design, consent, and retention.
- **Optional README parity** on private tree if maintainers want the same top-level navigation as public.

## Linear linkage (Product OS)

- **LUM-217** — existing; covers this shipped slice (no backfill required).
- **LUM-163** — parent (contributor experience umbrella).
- **New Linear issue for this retro:** not required.

## Testing retrospective

- **Tests added/changed:** none (documentation + Makefile only).
- **Commands run:** **`make verify-no-telemetry`** (public + private) — exit **0**; **`git diff --check`** — clean where run; secret-pattern greps on touched docs — no hits.
- **Gaps:** Guard scans **`orchestrator/`** only; other trees not in scope for **046**.
- **`docs/testing/automated-test-strategy.md`:** no update required.
- **Release / export skills:** no instruction change; promote via normal private → public export when ready.

## Status history

- 2026-05-16: Finalised by `/record-retro` (retrospective) — documents as-shipped **`TELEMETRY.md`**, **`verify-no-telemetry`**, and **`SECURITY.md`** cross-link commits above.
