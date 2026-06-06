# ADR-046: LUM-35 FP-017 per-user backup follow-ups reconciliation

> Status: Needs update
> Last reviewed: 2026-06-04
> Verified against commit: 0380ce81a
> Notes: **Duplicate ADR number:** `docs/decisions/046-telemetry-md-zero-telemetry-proof.md` is also **ADR 046**. Renumber this file to a **non-colliding** ADR filename (prefixes **053–080** are already used in `docs/decisions/`, including duplicate clusters on **061** / **063** / **064** / **072** / **074**, plus **`065-lum-320-*.md`** through **`080-lum-430-lumogis-search-public-export.md`**) in a coordinated maintainer pass (together with any **`034-linear`** / **072** rename) and update inbound links (e.g. `docs/LUMOGIS_REFERENCE_MANUAL.md`). Example next-free integer at **2026-06-04** HEAD: **`081-*.md`** — re-verify before rename. Do not delete either decision record.

**Status:** Finalised
**Created:** 2026-05-16
**Last updated:** 2026-05-16
**Decided by:** `/explore --headless LUM-35` (Claude Opus 4.7), `/review-plan --arbitrate` R1 (GPT-5.2), `/verify-plan --headless` (Composer)

## Context

`LUM-35` carries the three deferred items emitted by ADR **016**
("per-user backup / restore boundaries") at its 2026-04-18 closure
pass and recorded in `cursor/follow-up-portfolio.md` as **FP-017**
(legacy `BL-017`):

1. **CSRF un-skip** — `csrf.require_same_origin` is mounted on every
   new admin POST in ADR 016, but bypass #3 (Bearer) keeps it dormant
   under v1 auth. ADR 016 commits to un-skipping the deferred CSRF
   blocking tests once `cross_device_lumogis_web` lands cookie sessions.
2. **Erasure** — `per_user_account_erasure` was carved out of the B8
   chunk specifically to avoid bundling anonymise-vs-hard-delete and
   retention-exception decisions into per-user backup.
3. **410 vs 507** — the legacy `GET /api/v1/admin/export` already
   returns `410 Gone` with a successor pointer (intentional ADR-016
   deviation, regression-pinned); `archive_too_large` is mapped to
   `413` in `_REFUSAL_TO_STATUS`. The BL-017 "410/507" text is a
   half-remembered open question about storage-vs-policy semantics,
   not a defect in shipped code.

Constraints shaping the option space: ADR 016's
`format_version: 1` schema and refusal-reason → HTTP mapping are
**public contracts**; the Bearer bypass is intentional v1 behaviour
pinned by two regression tests; reverting the 410 deviation would
walk back a recorded direct user instruction.

## Decision

Treat LUM-35 as a **reconciliation ticket** with three durable
outcomes, shipped via a small **doc-only plan** plus **Linear hygiene**
(routing erasure follow-up to **LUM-188** by default):

- **CSRF un-skip — defer.** Add an inline note in
  `orchestrator/csrf.py` at bypass **#3** (after the numbered bypass
  list, before the lazy-consult paragraph) pointing at
  **`cross_device_lumogis_web`** (cookie-session phase) so the un-skip
  is contractually visible in AGPL source **without** embedding
  `LUM-*` Linear IDs. Keep the existing regression pins
  (`test_export_route_with_bearer_skips_csrf_intentionally`,
  `test_csrf_dependency_still_enforces_for_non_bearer_writes`)
  unchanged. Un-skip itself lands with cookie auth, not here.
  Maintainer checklist comments on **LUM-44** / cookie-session children
  remain Product OS closure hygiene, not shipped in `csrf.py`.
- **Erasure — route to existing export issue by default.** Treat
  **LUM-188** (“Data export … with **right-to-erasure** flow”) as the
  **primary** Linear owner for the FP-017 erasure strand — refine scope
  or add a **child under LUM-188**. Only if a fresh Linear export proves
  **LUM-188** genuinely excludes this slice should a **new** issue be
  opened (avoid duplicating right-to-erasure acceptance criteria under
  a separate LUM-21 child).
- **410 / 507 — document, do not change.** Keep `410` on legacy
  `GET /api/v1/admin/export`; keep `archive_too_large → 413`
  (RFC/MDN-correct for policy caps). Add one paragraph to
  `docs/guides/per-user-export-format.md` clarifying that `507
  Insufficient Storage` is reserved for genuine `ENOSPC`-class
  failures on the host writing the archive and is not the right
  status for the configured `_MAX_ARCHIVE_BYTES` policy cap.

LUM-35 closes via `/verify-plan` once the doc-only plan ships, with
closure bullets matching: **(a)** CSRF deferral visible in `csrf.py`
via **`cross_device_lumogis_web`**, **(b)** erasure strand acknowledged
on **LUM-188** (default) or documented fallback issue, **(c)** 410/507
semantics clarified in the format guide.

## Alternatives Considered

- **Bundle CSRF + erasure into a single chunk.** Works against the
  explicit ADR 016 carve-out and inflates the review surface
  (anonymise-vs-hard-delete deserves its own arbitration round).
  Full evaluation in
  `.cursor/explorations/LUM-35-per-user-backup-followups.md` § Option 2.
- **Revert the 410 deviation; restore byte-for-byte legacy
  NDJSON with `DeprecationWarning`.** Walks back a documented
  intentional deviation with no external client demand and risks
  the credential-redaction guarantee that the modern path enforces.
  Ruled out (Option 3).
- **Map `archive_too_large` from 413 → 507.** Direct conflict with
  RFC 7231 / RFC 9110 / RFC 4918 semantics (413 = policy cap, 507 =
  physical storage exhaustion). Ruled out (Option 4).
- **Strip the Bearer bypass now.** Breaks every legitimate non-cookie
  caller in v1; only viable after cookie auth lands.

Full evaluation, web-search evidence, and the impact-scan caveat are
in `.cursor/explorations/LUM-35-per-user-backup-followups.md`.

## Consequences

**Easier:**

- LUM-35 closes with three durable Linear outcomes instead of an
  open three-item TODO.
- Future contributors find the CSRF deferral inline at `csrf.py`
  bypass #3 instead of having to read ADR 016 + plan archive.
- The 507 boundary is documented before anyone is tempted to
  collapse it into the 413 policy cap.
- `per_user_account_erasure` exploration → plan → arbitration
  cycle is **superseded** when **LUM-188** absorbs the erasure strand;
  only open a distinct erasure issue if **LUM-188** is proven out of
  scope.

**Harder / impossible:**

- LUM-35 alone does not actually un-skip CSRF tests or ship
  erasure; those land via their own follow-up chunks. The
  follow-up portfolio must record them as new active rows, not
  as "FP-017 still open".
- Any future framing of "FP-017 = a single chunk" is now closed
  by this ADR's split.

**What future chunks must know:**

- `cross_device_lumogis_web` is responsible for un-skipping the
  Bearer bypass on the per-user-backup admin POSTs and adding the
  cookie-mode blocking tests; ADR 016 + this ADR are the contract.
- Right-to-erasure / account erasure implementation (tracked under
  **LUM-188** by default) reuses ADR 016's
  `_USER_EXPORT_TABLES`, `authored_by_filter`, Qdrant payload
  filters, and the FalkorDB user-node MERGE policy. It must add
  its own `__user_erasure__.*` audit prefix family parallel to
  ADR 016's `__user_export__.*` / `__user_import__.*`.
- The `format_version: 1` refusal-reason → HTTP mapping is now
  pinned at:
  `archive_too_large → 413`, `*_unsafe_*` / `_invalid` /
  `missing_*` / `unsupported_format_version` → 400,
  `forbidden_path → 403`, `email_exists` /
  `uuid_collision_on_parent_table` → 409, default → 400. New
  refusal reasons must be added to `_REFUSAL_TO_STATUS` and to
  `docs/guides/per-user-export-format.md` together.

## Revisit conditions

- **`cross_device_lumogis_web` lands cookie-session auth** → un-skip
  the deferred CSRF blocking tests on per-user-backup admin POSTs
  and re-evaluate whether `require_same_origin` bypass #3 (Bearer)
  should be narrowed to per-route opt-in.
- **A real `ENOSPC`-class failure is observed on a production
  household deployment** → add a 507 branch around the `zipfile`
  write path in `services/user_export.py` with a regression test;
  update the format guide to reflect the new shipped contract.
- **GDPR or analogous regulation becomes legally relevant to a
  Lumogis deployment** → fast-track erasure work on **LUM-188** (or
  successor issue carved from it) and add anonymise-vs-hard-delete +
  cooling-off ADR.
- **External clients of the legacy `GET /api/v1/admin/export`
  surface materialise** → revisit the 410 disposition; today no
  external dependency is known.
- **A `user_merge` or cross-instance migration use case appears**
  → returns ADR 016 's revisit condition; not part of FP-017.

## Status history

- 2026-05-16: Draft created by `/explore --headless LUM-35`
  (Claude Opus 4.7).
- 2026-05-16: Revised during `/review-plan --arbitrate` R1 — erasure
  strand defaults to **LUM-188** (avoid duplicate spin-out); AGPL
  `csrf.py` deferral uses **`cross_device_lumogis_web`** only (no
  `LUM-*` in shipped source); closure bullets updated.
- 2026-05-16: Finalised by `/verify-plan --headless` — implementation
  confirmed: `orchestrator/csrf.py` docstring deferral + guide section
  **Policy cap vs host storage exhaustion (413 vs 507)**; portfolio
  **FP-017** closed-by-reconciliation.
