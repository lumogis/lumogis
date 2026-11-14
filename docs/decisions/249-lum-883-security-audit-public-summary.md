# ADR-249: Security audits get a public summary of fixed findings only (LUM-883)
**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-10 (owner decision: fixed findings only)
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none — shipped as a targeted `exec:claude` docs session without a plan / verify cycle
**Exploration:** `.cursor/explorations/security_audit_2_public_summary_retro.md`
**Draft mirror:** `.cursor/adrs/security_audit_2_public_summary.md`
**Linear:** LUM-883

## Context

The second security audit of Lumogis Core and Lumogis Web (2026-11-06) produced a full report with file locations, request shapes and fix designs for 31 findings (0 Critical, 2 High, 10 Medium, 11 Low, 8 Info). Publishing that report while findings were open would have handed out exploit recipes, so it was not published. LUM-883 closes when all four remediation children are done and a summary is published in `docs/security/`, next to the pre-launch audit ([ADR 060](060-lum-190-pre-launch-security-audit-methodology.md)), whose `revisit-by 2026-08-22` rows were overdue.

By 2026-11-10 every Medium finding and one of the two High findings were fixed (ADRs 230, 242, 243, 246). The rest were still open and tracked.

## Decision

A security audit is published in `docs/security/` as a **summary**, not as the full report.

- **Shape.** Scope, method, tooling results, severity counts per area, a table of fixed findings, then what held. It follows the shape and tone of `docs/security/pre-launch-audit-2026.md`.
- **Fixed findings** are described concretely: what was wrong, what changed, the ADR, and the release (here, the first release after 0.11.0, matching the `CHANGELOG.md` `[Unreleased]` → Security entries).
- **Open findings are not published** (owner decision, Thomas, 2026-11-10). The severity counts include them, so the totals stay honest, but no row describes them, not even by class. Each moves to the fixed table when its fix ships. Operator guidance stays generic (run with sign-in on whenever Lumogis is reachable beyond its own machine) and does not point at a specific finding.
- **References.** Finding IDs, ADR numbers and `LUM-###` issue IDs as plain text, as `CHANGELOG.md` already does. No issue-tracker URLs, internal plan paths or non-public file paths.
- **Revisits.** When an audit revisits an earlier audit's `revisit-by` rows, the earlier audit's master table records the outcome in place: the evidence, a new status or a new `revisit-by` date, and the reviewer and date. For audit #2: SAST-001 and CSRF-001 stay ACCEPTED, both due again 2027-02-06; MCP-001 becomes MITIGATED (ADR 132, ADR 243). Revisit notes follow the same rule as the summary: a revisit that finds an open gap says where the hardening is tracked, not what the gap is.

As built: `docs/security/security-audit-2026-11.md` (new) and `docs/security/pre-launch-audit-2026.md` (three rows revised, link to the new summary).

## Alternatives Considered

- Not chosen: **publish the full report once every finding is fixed.** The Low/Info tail has no deadline, so the audit would stay invisible to public readers indefinitely.
- Not chosen: **describe open findings by class and mitigation** (the first draft). Even a class description tells a reader which installs to aim at, and the most common install is the one most exposed. The generic sign-in-on recommendation gives operators the mitigation without naming any gap.
- Not chosen: **link Linear issues.** The workspace is not public, so the links would be dead for public readers; plain IDs match the changelog.
- Not chosen: **cite `dev` commit hashes for fixes.** Public and private histories have different hashes, so a hash would not resolve for public readers. The release and the ADR identify a fix in both.

## Consequences

- Public readers can see what was audited, how much was found and what was fixed. They cannot see what is open beyond the counts.
- The summary must be updated when an open finding is fixed (move the row to the fixed table, add the ADR) and when a new audit supersedes it.
- A finding stays unpublished until it is fixed, so a long-open Low item is invisible publicly for as long as it is open. That is accepted; the counts show that open items exist.
- The summary cites ADRs 230, 242, 243 and 246, so those must stay in the public tree.

## Revisit conditions

- An open finding is fixed → move its row to the fixed table with its ADR and release.
- An open finding tied to a pre-launch audit row is fixed → update that row (for example CSRF-001) along with the fixed table.
- 2027-02-06 → the SAST-001 and CSRF-001 `revisit-by` date in the pre-launch audit.
- The next security audit → write a new summary in this shape and revisit the open rows of this one.
- A finding is ever exploited in the wild or reported externally → the coordinated-disclosure process in `SECURITY.md` overrides this class-only rule for that finding.

## Linear linkage (Product OS)

- **Existing issue:** LUM-883 (umbrella; children LUM-884 to LUM-887 Done).
- **New issue needed:** no.
- **Historical evidence only:** no; this closes LUM-883's second "Done when" bullet.
- **Follow-ups:** the open findings already live in LUM-883's Later section, LUM-886's Later section and LUM-31.

## Testing retrospective

Docs only; no tests added. Checks run: boundary greps on the new and edited files, a relative-link check, `git diff --check`, `make changelog-check`, `make adr-check`, and `scripts/check-public-export.sh` on an export tree built by `scripts/create-upstream-export-tree.sh` into a temporary directory, which confirmed that the summary and every ADR it cites ship. `docs/testing/automated-test-strategy.md`: no change. Release skills: no change.

## Status history

- 2026-11-10: Finalised by /record-retro (retrospective).
- 2026-11-10: Amended before merge on the owner's decision: open findings are not published; the open-findings table was removed, and the CSRF-001 revisit note no longer describes the open gap.
