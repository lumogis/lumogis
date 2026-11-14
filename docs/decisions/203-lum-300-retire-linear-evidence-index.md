# ADR-203: Retire the Linear evidence index

**Status:** Accepted
**Created:** 2026-08-04
**Decided by:** LUM-300 (retirement audit 2026-05-19, executed 2026-08-04)
**Linear:** [LUM-300](https://linear.app/lumogis/issue/LUM-300/retire-linear-evidence-index-remove-register-scripts-skill-gates)
**Supersedes:** [ADR 034](034-linear-evidence-index.md) — Linear ↔ repo evidence index
**Relates:** LUM-86 (introduced it), LUM-87 (the reconciler that ran it), LUM-9 / LUM-10 (the linkage checkers that replace it), LUM-799 (the failure that prompted execution)

## Context

`cursor/evidence/linear-evidence-index.md` was introduced by LUM-86 (ADR 034) as a
join register mapping Linear issues to repo evidence paths. It was **manually
maintained by design**: `seed_evidence_index_from_csv.mjs` and
`suggest_evidence_index_rows.mjs` print suggestions to stdout and never write, and
`/verify-plan` is explicitly forbidden from touching the file it validates.

That design depended on a human merging suggested rows on every plan and
exploration. It did not happen. The register carried a deprecation banner from
2026-05-19 — *"no longer maintained, has drifted, cannot be kept current without
automation that does not exist"* — and stayed a required gate for another eleven
weeks.

Everything it recorded is now held somewhere that maintains itself:

| What the index held | Where it lives now |
| --- | --- |
| Issue status | `cursor/reports/linear-issues-export.json` |
| Plan ↔ issue linkage | `check_plan_linear_linkage.mjs` — reads frontmatter directly |
| Exploration ↔ issue linkage | `check_exploration_linear_linkage.mjs` |
| Repo evidence | `docs/decisions/` ADR files, cited from the issue |
| Drift | `drift_check.mjs` |

**What finally forced the decision** was not the drift. It was LUM-799: row 21
pointed at `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md`, a
**regenerated artefact** deliberately untracked in `e129074` and matched by a
`.gitignore` rule. The file is present on the machine that generates it and absent
from every fresh checkout — so the gate was **green for its author and red for
everyone else, permanently**.

That is the worst failure mode a governance check can have. It is not a false
positive (the path really is missing) and not a real defect (nothing is broken).
It is a check that can only be satisfied by being the right person at the right
terminal, and its steady state is teaching readers to skim past a red gate.

## Decision

**Delete the register, its four scripts, and its gates. `drift_check.mjs` is the
sole Product OS linkage enforcement layer.**

Concretely:

* `cursor/evidence/` is removed — the index, its README, and the draft ADR mirror
  at `cursor/adrs/linear_evidence_index.md`.
* Four scripts go: `check_linear_evidence_index.mjs`,
  `seed_evidence_index_from_csv.mjs`, `suggest_evidence_index_rows.mjs`,
  `linear_evidence_index.test.mjs`, plus their fixture directory.
* `product_os_reconcile.mjs` becomes a **drift-only** harness. Its `--json`
  envelope, cwd guard and `--` passthrough are unchanged; `steps[]` now has one
  entry rather than two.
* `/create-plan`, `/verify-plan` and `/navigator` lose their index steps.

**`--strict-docs` is consumed with a deprecation warning rather than deleted.** It
existed only to forward to the index checker, so it is now meaningless — but
letting it fall through to `drift_check` as an unrecognised flag would turn a
stale habit into a confusing failure. One line on stderr is cheaper than that.

## Alternatives considered

* **Fix row 21 and keep the register.** Done first, as LUM-799 — and it is why this
  ADR exists rather than another repair. The row was repointed at a tracked
  artefact and every other row audited (89 `cursor/` rows: none gitignored, all
  tracked). The register was *correct* for the first time in months, and still
  nothing writes to it. Repair addressed the instance and left the mechanism.
* **Harden the checker to require tracked-ness, not just existence.** This closes
  the LUM-799 class properly and was the recommendation left on that ticket. It
  makes a better gate around a register nobody maintains — the wrong thing, done
  well.
* **Un-deprecate and staff it.** Rejected. The 2026-05-19 banner was itself the
  conclusion of an audit; nothing since has changed the economics of hand-merging
  rows on every plan.
* **Keep the file, drop the gate.** Rejected as the worst of both: a stale register
  that still looks authoritative to anyone who finds it, with nothing to say so.

## Consequences

**Easier**

* `product_os_reconcile.mjs` and `check_linear_evidence_index.mjs` stop failing for
  reasons unrelated to the work being verified. Both now exit 0.
* One fewer manual step in `/create-plan` and `/verify-plan`, and one fewer
  "suggested rows" block that plans carried and nobody actioned.
* The remaining Product OS gates read source of truth directly — frontmatter and
  the filesystem — rather than a hand-copied projection of it.

**Harder**

* **The Linear → repo direction is no longer *indexed*, but it is still
  *derivable* — and better than it was.** This bullet originally called it "a real
  loss and not merely a cleanup." **Measured 2026-08-04, that was wrong**, and the
  correction is worth more than the original claim:

  | | Distinct issues resolvable |
  | --- | --- |
  | The deleted index | **89** (median LUM-49 — it captured the earliest era, then stopped) |
  | Filename + frontmatter lookup | **467** |

  624 of 719 plans/explorations/ADRs resolve to a `LUM-###` mechanically (86%).
  The index covered **19%** of that. The 95 that do not resolve are pre-Linear
  artefacts (`caldav_connector_credentials.plan.md`) — the same set the linkage
  checkers already grandfather, and absent from the index too.

  It works because the naming conventions are **enforced** where the register was
  not: `cursor/plans/LUM-###-<slug>.plan.md` is a `/create-plan` hard rule,
  explorations and `cursor/reviews/LUM-###-<slug>/` follow it, `docs/decisions/`
  carries `NNN-lum-###-<slug>.md`, and `linear_issue_id` frontmatter is parsed by
  `check_plan_linear_linkage.mjs` on every run. Slug drift is harmless — LUM-762's
  exploration and plan have different slugs and a `LUM-762-*` glob finds both.

  **What is genuinely lost is the curation**, not the linkage: a hand-written
  Notes column, and a `Role` marking evidence `active` / `historical` /
  `superseded`. Nothing derives that. It applied to 89 issues and had not been
  updated since the earliest ones.
* **Historical rows are gone with the file.** 94 rows of issue → evidence mapping,
  mostly for work closed in May 2026, exist only in git history. Recoverable, not
  browsable.

**Future work must know**

* **Do not reintroduce a hand-maintained register.** This is the second time the
  cost has been established. If the Linear → repo direction is needed again,
  generate it from the export and the checkers — never from human discipline.
* **A gate that can only pass on one machine is worse than no gate.** LUM-799 is
  the worked example; if a check reads a path, that path must be tracked.

## Revisit conditions

* **A generated Linear → repo map becomes cheap** — e.g. `linear_import.mjs` gains
  an export mode that can emit it as a build artefact. Then the answer is a
  regenerated report, gitignored and never gated.
* **Multiple maintainers** — the "human discipline does not scale" argument was
  made for a single maintainer, and a team might change it. It would still need
  the generation story above.

## Status history

- 2026-08-04: **Consequences corrected after measurement.** The "Linear → repo is a real loss" bullet was written from intuition and is wrong: a filename + frontmatter lookup resolves 467 distinct issues against the index's 89. Corrected in place rather than quietly, because an Accepted ADR overstating the cost of its own decision is the kind of error that gets cited later.
- 2026-08-04: **Accepted and executed.** Register, scripts, fixtures and gates
  removed; `product_os_reconcile` reduced to drift-only; three skills rewritten;
  ADR 034 superseded. Triggered by LUM-799 — one repaired row was enough to show
  that repairing rows was the wrong loop.
