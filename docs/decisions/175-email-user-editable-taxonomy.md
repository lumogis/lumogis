# ADR-175: User-editable email category taxonomy + classification-rules store (LUM-629)

**Status:** Finalised (with deviations recorded)
**Created:** 2026-07-17
**Last updated:** 2026-07-17
**Decided by:** /explore (Opus 4.8) → /review-plan (Fable + Sonnet 5 critique) → finalised by /verify-plan
**Draft/source:** `.cursor/adrs/LUM-629-email-user-editable-taxonomy.md`

## Context

LUM-170 (ADR-174) shipped on-box email classification with a **compile-time** taxonomy (`EMAIL_CATEGORIES`) and a
static keyword rule pass in `orchestrator/services/email_classifier.py`. Households need their own categories
("custom labels") and a way to author the rules that route mail into them — without sending email content to a
cloud LLM and without adding a heavy dependency to a local-first stack. The repo had no per-household user-editable
settings store (only encrypted credentials and global key/value tables), but a clean per-user-prefs precedent
exists (`notification_preferences`, migration 031) and a load-and-cache ruleset precedent (`secrets_scanner`). No
consumer depends on the fixed enum — routes project `signals.category` but never filter on it — so widening the
taxonomy is low blast-radius.

## Decision

Add a **per-household (user-scoped) DB-backed taxonomy + JSON classification-rule store**, read at runtime by the
classifier, with an in-process deterministic matcher generalising the existing `classify_rules`. Migration `055`
creates `email_category_taxonomy` and `email_classification_rule` (keyed `user_id` default `'default'`, mirroring
`notification_preferences`). A new `orchestrator/services/email_taxonomy.py` loads+caches them as a **sparse
overlay** over the built-in defaults — **absence of a row means "use the default"** (no seed-on-first-read, no
`is_default` column; `is_custom`/`is_protected` derived, not stored) — so an untouched household behaves exactly as
today with zero write path. `email_classifier` takes the taxonomy + ruleset as parameters (with defaults equal to
today's behaviour); `signal_processor` passes the loaded set with a DB-failure fallback to defaults;
`require_local_model` (the privacy boundary) is unchanged. User-scoped routes (`/api/v1/me/email-taxonomy`,
`/api/v1/me/email-classification-rules`, `.../{slug}/reclassify`) back a React "Email labels" editor under
`clients/lumogis-web/src/features/me/`. No new dependency, no Docker service, no new network surface. Rejected: a
third-party rules engine (disproportionate; `durable_rules` stale since 2020), file/env-only config (no per-user
rows, no UI — kept only as a possible seed source), and an aliases-only half-measure.

## Alternatives Considered

- **Third-party Python rules engine** (`rule-engine` / `json-rule-engine` / `durable_rules`) — forward-chaining
  business logic disproportionate to keyword+header matching; `durable_rules` stale; adds a dependency for ~20 lines.
- **File/env config** (extend `secrets_scanner._PATTERN_FILE`) — no per-household rows, no UI.
- **Fixed taxonomy + aliases/tags only** — cheapest, but users still can't create their own categories or rules.

Full detail: `.cursor/explorations/LUM-629-email-user-editable-taxonomy.md`.

## Consequences

**Easier:** households get true custom labels + an editable rule layer with zero new dependencies/services; the
change is additive (defaults → per-household overrides), so existing behaviour and existing `signals.category`
values stay valid; reuses two in-repo patterns so implementation risk is low.

**Harder / committed:** commits the taxonomy to a **per-user/household** key (per-mailbox override becomes a key
extension, not a rewrite) and to a **structured-JSON in-house matcher**. The stored `signals.category` is a **slug**
resolved to a label via the taxonomy — downstream consumers (LUM-623 write-back, any future category filter) must
resolve slug→label through `email_taxonomy`, never assume the fixed six. `other` is an undeletable floor so
`coerce_category` always resolves.

**Future chunks must know:** custom labels are **rules-only in v1** — the email LLM prompt has no `category` key, so
the LLM residual never proposes a custom slug (UI copy states this). Cross-user slug→label resolution on
household-shared signals is out of v1 (unresolvable slugs render raw). Both are follow-ups under LUM-513.

## Implementation deviations (recorded at finalisation)

The core decision held (in-house DB store + in-process matcher, local-only classification, sparse overlay). Three
refinements emerged during review/implementation and are part of the record:

1. **Route placement → `/api/v1` façade.** The plan placed the routes in `routes/signals.py` (root-mounted). The
   vite/nginx proxy forwards `/api` — not root `/me` — to the orchestrator, so a Labels page could not reach them.
   Routes live in `routes/api_v1/email_taxonomy.py` (prefix `/api/v1/me`), mirroring `notification_preferences`.
2. **Reclassify SQL cast + transactional sweep.** `signals.source_id` is `TEXT` and `sources.id` is `UUID`
   (`postgres/init.sql`), so the imap-scoping join casts `id::text` (matching `signal_processor._persist`). The
   disable/delete orphan-sweep and the taxonomy-row write run in one `PostgresStore.transaction()` (all-or-nothing).
   Both were caught by the Sonnet 5 critique (R2).
3. **Rule category validation.** `put_rules` validates a rule's category against **all** known slugs (enabled or
   disabled), not just enabled ones — dead rules (`category_disabled`) are intentionally kept and re-submitted by
   the web full-replace PUT, so validating against enabled-only would 422 every subsequent save. The classify path
   (`load_rules`) still skips disabled-category rules. (Caught by /code-review.)

## Revisit conditions

- **Per-mailbox taxonomies become a requirement** → extend the store key from `user_id` to `(user_id, source_id)`.
- **Rule complexity outgrows keyword/sender/header JSON** → revisit the in-house matcher (possibly a library).
- **A consumer needs to filter/group signals by category at query time** → add a `category` index/filter to
  `list_signals`.
- **Historical sender/header-rule reclassification demanded** → persist a minimal sender/header subset on email
  signals (v1 reclassify is subject-only because headers are transient).

## Status history
- 2026-07-17: Draft created by /explore (LUM-629). Recommendation: in-house DB store + matcher (Option 1); High confidence.
- 2026-07-17: Revised during /review-plan --arbitrate R1 (Fable) — sparse overlay (no seed rows/`is_default`).
- 2026-07-17: **Finalised by /verify-plan** — implementation confirmed the decision; three deviations recorded
  above (/api/v1 façade, reclassify cast + transactional sweep, rule-category validation against all known slugs).
  Tests green (100 across LUM-629 + regression).
- 2026-07-17: **Follow-ups shipped** (LUM-630 + LUM-631, plan `LUM-630-631-email-classification-depth`) — the two
  pre-registered follow-ups under "Future chunks must know": **body-keyword rule conditions** (`RuleMatch.body_contains`,
  full-ingest gated, best-effort — 50 bodies/tick cap, plain-text only) and a **taxonomy-aware local LLM prompt**
  (`_EMAIL_PROMPT_TEMPLATE` enumerates the user's enabled slugs so the on-box model proposes a category; rules still
  first, `coerce_category` still floors). No decision change; the privacy boundary is untouched. So "custom labels
  are rules-only in v1" (Consequences) is now superseded — the local model can also propose them.
- 2026-07-18: **Follow-up shipped** (LUM-632, plan `LUM-632-cross-user-slug-label-resolution`) — the registered D8
  follow-up ("cross-user slug→label resolution"): `GET /signals` now returns a resolved `category_label` per row via
  the signal **owner's** (`signals.user_id`) taxonomy (batched per distinct owner; raw-slug fallback; `""` only for
  empty category; `general` resolves normally with no special-case; owner-load error degrades to the raw slug, list
  still 200). `user_id` is used server-side only and is never serialized. A **prerequisite** fix propagates the
  category slug onto shared/system projections (`projection.project_signal` now carries `category`, forward-only) —
  without it the feature is a no-op. No decision change; no new visibility (resolution runs only over rows
  `visibility.visible_filter` already returns) and personal signals never resolve cross-user. Verified via two
  adversarial plan rounds (Fable + Sonnet, 0 functional/security P0s). Web rendering of `category_label` remains
  deferred to LUM-627.
