# ADR-204: The pre-`main` promotion gate — a label, not a priority

**Status:** Accepted
**Created:** 2026-08-04
**Decided by:** Product OS review, 2026-08-04
**Linear:** [LUM-802](https://linear.app/lumogis/issue/LUM-802/pre-main-promotion-gate-gatepre-main-label-promoted-issues-trailer)
**Relates:** [ADR 203](203-lum-300-retire-linear-evidence-index.md) (derive, do not
register), LUM-789 (RC fixes stranding on `main`), LUM-799 (a gate that could only
pass on one machine)

## Context

`/verify-plan` classifies leftover gaps P0–P3 and files the survivors as Linear
children. Three things were wrong with that, and they compounded.

**1. Only P0 was enforced.** P0 blocked `✅ Complete` because `/verify-plan` checks
it. P1 said "before public release", P2 said "next cycle", P3 said "optional /
nightly only" — and **nothing read any of them.** A P1 was a P2 with a more urgent
adjective. 22 open P0-titled children existed despite Step 10a saying *"Never
create child issues for P0 gaps"*, which is what happens when a level's meaning is
carried by prose rather than by a check.

**2. The obvious fix does not work.** The natural carrier for "this blocks
release" is the Linear `priority` field. It cannot hold that state:
`/navigator sync` applies a **percentile priority across the whole backlog**, and
`linear_import.mjs --sync-priorities` rewrites it too. A `2 (High)` set by
`/verify-plan` on Monday is a `3 (Medium)` on Tuesday with nobody having decided
anything. Measured 2026-08-04, Linear priority and the `[P#]` title prefix
disagreed on **35%** of gap children.

**Labels are protected in the opposite direction.** `/navigator`'s own rules say
`issueUpdate` may set **priority only** — *"Never change status, labels, project,
or description."* The one field our automation is forbidden to touch is the one
field a gate can safely live in. The `gate:` namespace already existed
(`gate:learning-loop`).

**3. The promotion destroys its own evidence.** `main` receives squashes. The
2026-08-02 full dev promotion landed as `release: full dev promotion RC (scope D)
from origin/dev` — a single commit carrying **zero** LUM ids for **964 commits**
and roughly 300 tickets. After it landed, "did LUM-793 ship to `main`?" had no
answer short of replaying `dev`. So even if a gate existed, there was no durable
record of what a given promotion had covered.

## Decision

**A P-level is a claim about what the gap blocks, and each level gets a mechanism
that enforces it.**

| | Blocks | Enforced by |
| --- | --- | --- |
| **P0** | this chunk closing | `/verify-plan` cannot report ✅ Complete |
| **P1** | its parent reaching private `main` | `pre_main_gate.mjs` refuses the promotion |
| **P2** | nothing | — (cheapest outcome: `in-code:` / `fixed-now:`) |

Concretely:

* **New Linear label `gate:pre-main`.** `/verify-plan` Step 10a applies it to
  every P1 child and to nothing else. Priority is still set for triage ordering,
  and is explicitly documented as **advisory**.
* **New `scripts/linear/pre_main_gate.mjs`** (devtools, read-only). `--check`
  derives the promoted issue set from a git range and refuses (exit 1) if an open
  `gate:pre-main` issue names one of those issues as its parent — or is one of
  them. `--trailer` emits the commit trailer.
* **`Promoted-Issues:` git trailer** on the commit that lands on `main`, folded
  and readable by `git interpret-trailers --parse`. `git log origin/main --format=%B
  | git interpret-trailers --parse` then answers "which release carried LUM-###".
* **`/prepare-private-release-from-dev`** runs both before merging the RC. Hard
  rule 8, a hard-stop condition, and two lines in the final report template.
* **P3 is retired** as a classification. It meant "optional / nightly only" —
  P2 with extra ceremony and no enforcement.

### One query, not three hundred

The gate asks Linear for **all issues carrying `gate:pre-main`** (one paginated
query) and intersects their parents with the promoted set — rather than querying
children for each of ~300 promoted issues. Equivalent result; the cost does not
scale with the size of the promotion, which matters because the largest promotions
are exactly the ones most likely to skip a slow check.

### Deriving the id set

Union of two sources, measured on `origin/main..origin/dev` (2026-08-04):

| Source | Ids |
| --- | --- |
| Commit subjects (558 of 801 non-merge commits name one) | 301 |
| ADRs **added** in the range (filename + first id on the `**Linear:**` line) | +3 |
| **Union** | **304** |

Two exclusions are load-bearing, and both were bugs before they were rules:

* **ADRs are filtered to added (`--diff-filter=A`), not added-or-modified.**
  Allowing `M` pulled in LUM-35, LUM-199, LUM-248, LUM-302 and LUM-321 — every one
  an ADR owned by an already-shipped issue that this range merely *amended*.
* **Only the first id on a `**Linear:**` line counts.** That line carries
  parenthetical lineage — `**Linear:** [LUM-244](…) (child of [LUM-29](…);
  umbrella [LUM-51](…))` — and reading all of it promoted seven epics that shipped
  months earlier.

Both are the owned-vs-mentions distinction that `find_issue_artefacts.mjs`
(ADR-203) exists to get right, arriving in a second tool.

**Commit bodies are excluded** despite raising the count from 301 to 379. Bodies
say "Follow-up: LUM-800" and "Relates: LUM-762" — tickets *mentioned by* the work,
not shipped by it. The trailer's value is that it can be trusted; 78 maybe-shipped
ids cost more than the coverage buys.

The trailer is therefore a **floor**, and `--trailer` prints the 243 commits that
named nothing to stderr so the floor's height is visible.

### It fails closed

No `LINEAR_API_KEY` and no `--gated-issues` file is **exit 2**, not a pass. So is a
shallow clone, an unresolvable ref, and an unknown workflow-state type. A
`--gated-issues` file whose entries do not carry the label is rejected outright
rather than treated as gated.

This is a direct debt to LUM-799 (ADR-203 § Context): a check that cannot
distinguish *"could not run"* from *"nothing wrong"* teaches readers to skim past
it, and stayed broken for eleven weeks because of it.

## Alternatives considered

* **Use `priority` and stop the syncs from touching it.** Rejected: percentile
  priority is what makes `/navigator` useful for ordering a 900-issue backlog.
  Carving out an exception for gap children means the sync needs to know about
  gap children, which is more coupling than a label costs.
* **A `blocks` relation from the child to a release issue.** Closer to Linear's
  grain, but it needs a release issue to exist before the gap is found, and
  relations are the one thing `/linear-update` is already careful never to touch.
  A label is checkable with one query and one string.
* **Enforce at publish (private `main` → public) instead of at promotion.**
  Rejected: `main` is the release line. Something that reaches `main` with a known
  gap has already passed the last point where the gap was cheap to fix, and the
  publish step would be refusing work it cannot send back.
* **Parse `Promoted-Issues:` out of `dev` at publish time rather than writing a
  trailer.** That is what the gate does at promotion time — but the whole problem
  is that the squash *destroys* the range. After the squash there is nothing left
  to parse. The trailer has to be written before, or not at all.
* **Keep P3.** Rejected. Four levels where two are enforced and two are adjectives
  is how P1 came to mean nothing.

## Consequences

**Easier**

* "Which release carried LUM-###" is one `git log … | git interpret-trailers
  --parse` away, for the first time.
* A P1 now costs something to file and is worth something once filed, which is
  the pressure that keeps the label set small enough for a red gate to be worth
  reading.
* Three levels, three meanings, two mechanisms. `/verify-plan` no longer asks
  anyone to distinguish "next cycle" from "optional".

**Harder**

* **The gate needs Linear reachable at promotion time.** `/prepare-private-release-from-dev`
  was previously repo-only ("skills do not call the Linear API"), and this is the
  documented exception — read-only, no mutation. A maintainer without
  `LINEAR_API_KEY` must pass `--gated-issues`, and with neither the promotion
  stops. That is the intended cost of failing closed, but it is a new way for a
  release to be blocked by tooling rather than by code.
* **The trailer's floor is 70%, not 100%.** 243 of 801 commits in the measured
  range named no ticket and contributed nothing. Raising it means changing commit
  discipline, which is a separate decision this ADR deliberately does not make.
* **P3 is retired in the skills but not in the plumbing.** `run-workflow.mts`
  still types `priority` as `"P1" | "P2" | "P3"`, `/review-plan`'s contract still
  requires a `p3_gaps` integer, and `drift_check.mjs` still greps `\bP3\b` — all
  deliberately, because they read history. New work emits `p3_gaps: 0`. Anyone
  reading the type expecting it to reflect current practice will be briefly
  misled; the alternative was breaking replays of in-flight runs for nothing.

**Future work must know**

* **Do not carry governance state in `priority`.** It is a derived, resynced field.
  This is now the second measured instance of the general rule ADR-203 established
  in the other direction: state that automation rewrites cannot also be state that
  humans set meaningfully.
* **`gate:pre-main` is not a triage label.** If it starts appearing on more than a
  handful of open issues, the gate has become background noise and the P1
  definition has drifted back to "important". Count it, do not assume it.

## Baselining — and the labelling audit

**The open `[P1]` population is three issues, so the gate was backfilled, not
baselined.**

The first version of this section said the opposite. It argued the gate should
start empty because retro-labelling would mean "hand-judging several hundred
tickets". That was reasoning from the wrong number: **~300 is the size of the
promotion range**, not the size of the open P1 backlog. Measured across all 255
open issues on 2026-08-04:

| Open, bracket-titled | Count |
| --- | --- |
| `[P0]` | 0 |
| **`[P1]`** | **3** |
| `[P2]` | 18 |
| `[P3]` | 14 |

Three. All three were labelled, and **two of them block the current
`origin/main..origin/dev` range immediately**:

* **LUM-797** — "[P1] Run the LUM-793 failed-send recovery Playwright spec against
  a real stack", parent **LUM-793**, in range. A spec written, listed, and never
  executed. This is precisely what the gate is for, and without the label the
  promotion would have carried it silently.
* **LUM-455** — "[P1][A/B launch blocker] Wayland overlay re-summon", parent
  **LUM-430**, in range.
* **LUM-310** — "[P1] Attach clean-host first-run evidence for LUM-184" has **no
  parent**. It is labelled, and it can never block anything: both gate rules need
  a graph edge into the range, and LUM-310 names its subject in prose only.

### `--audit`, and why the gate needed a third mode

LUM-310 is the general case of a problem the first design missed. Step 10a said
*"every P1 child MUST carry `gate:pre-main`"* — and that was **prose an agent
follows**. A forgotten label yields a `[P1]` child the gate silently ignores:
green for the wrong reason, which is the failure this ADR opens by describing.

`pre_main_gate.mjs --audit` closes it. It reports three findings against all open
issues:

| Finding | Meaning |
| --- | --- |
| **MISSING** | open `[P1]`, no label — blocks nothing, so it is a P2 wearing a P1 title |
| **STRAY** | the label on a `[P2]`/`[P3]` — dilutes the gate until red stops being worth reading |
| **UNREACHABLE** | labelled, no parent, not itself in range — can never block *any* promotion |

`--check` warns about the UNREACHABLE case inline too, on green runs as well as
red ones. It is run from `/verify-plan` Step 10a.7 after any pass that files a P1,
and from `/navigator drift`.

**What the audit cannot see: unbracketed titles.** Plenty of genuine
release-relevant work carries no `[P1]` prefix — LUM-761, "Retry transient OAuth
token-refresh failures in place", is `source:verify-plan` and unprefixed. A clean
audit means *"nothing declared P1 is unlabelled"*, never *"nothing blocking is
unlabelled"*. Stating that limit is the difference between this and the register
ADR-203 retired for overclaiming.

## Revisit conditions

* **`gate:pre-main` exceeds ~10 open issues.** The gate is only useful while a red
  result is worth reading. At that point either P1 has drifted or promotions have
  stopped happening; both need a look, not a bigger allowlist.
* **Commit-subject coverage rises well above 70%** — e.g. a commit-msg hook. The
  ADR-added source could then be dropped, and the trailer stops being a floor.
* **More than one maintainer promotes to `main`.** The "run it before you merge"
  instruction is currently held by a skill, which is discipline. With a second
  promoter it should become a server-side hook or a CI job on the RC.

## Status history

- 2026-08-04: **Linear backfilled as LUM-802.** The ADR was written ahead of its
  issue and carried `**Linear:** _backfill required_` for half a day; the file was
  renamed to the `NNN-lum-###-<slug>` convention at the same time so
  `find_issue_artefacts.mjs` resolves it by filename as well as by this line.
- 2026-08-04: **Baselining corrected after measurement, and `--audit` added.** The
  original section argued against backfilling because it would mean hand-judging
  "several hundred tickets" — reasoning from the size of the promotion range, not
  the open-P1 backlog, which is **three**. All three were labelled and two block
  the current range. Correcting in place rather than quietly: an Accepted ADR that
  talked itself out of a cheap, correct action is exactly the error that gets
  cited later. The same pass added `--audit`, because a gate whose labelling is
  enforced only by prose is the failure mode this ADR was written about.
- 2026-08-04: **Accepted and implemented.** `gate:pre-main` created;
  `pre_main_gate.mjs` + tests added (12 mutations run, 12 killed);
  `/verify-plan`, `/prepare-private-release-from-dev`, `/create-plan`,
  `/linear-update`, `/record-retro` and `/review-plan` updated. Gate baselined
  empty.
