# ADR: Shared / system connector credential scopes
**Status:** Finalised
**Created:** 2026-04-21
**Last updated:** 2026-04-22
**Decided by:** composer-2 via /explore; finalised by /verify-plan after the `credential_scopes_shared_system` plan landed.
**Plan:** *(maintainer-local only; not part of the tracked repository)*
**Working draft:** *(maintainer-local only; not part of the tracked repository)*

## Context

Per-user connector credential storage is **shipped and finalised** (`docs/decisions/018-per-user-connector-credentials.md`): `user_connector_credentials` with `(user_id, connector)` PK, Fernet/MultiFernet at rest, `resolve()` semantics, export omission, and explicit **SCOPE-EXEMPT** status relative to memory visibility (ADR 015 — `personal` / `shared` / `system` **memory** scopes must not be conflated with connector secrets).

Per-user connector **permissions** also **shipped in flight** (`models/connector_permission.py`, `routes/connector_permissions.py`, `postgres/migrations/016-per-user-connector-permissions.sql`, ADR/plan `per_user_connector_permissions`) — `connector_permissions` is now keyed `(user_id, connector)` with composite UNIQUE and a lazy `_DEFAULT_MODE='ASK'` fallback. **Credential resolution and ASK/DO permission decisions are independent calls**, not a joined query.

Households on a single Lumogis instance still need:
1. **Household-shared connector material** — one canonical secret for N users (e.g. shared API token).
2. **Instance (system) connector material** — operator-owned secrets not tied to a single human account.

Without a deliberate design, teams will misuse admin-on-behalf writes, duplicate rows per user, overload memory `scope`, or pollute identity columns with sentinel values — each option creates security or semantics debt.

## Decision (recommended direction — not implemented)

**Keep `user_connector_credentials` unchanged as the per-user tier.** Add **two separate** credential stores for **household** and **instance system** tiers (illustrative names: `household_connector_credentials`, `instance_system_connector_credentials`; PKs `(connector)`-only in v2 for single-household single-tenant alignment with ADR 012). Use the **same crypto primitives and key material family** as ADR 018 unless a future decision intentionally splits keys.

**Runtime resolution default (new entrypoint):** introduce `resolve_runtime_credential(caller_user_id, connector)` that walks the tiers in this order:
1. **user** row for `(caller_user_id, connector)`;
2. else **household** row for `(connector)`;
3. else **system** row for `(connector)`;
4. else fall through to existing ADR 018 env-fallback rules inside `resolve()` (still **forbidden** when `AUTH_ENABLED=true`).

The existing `resolve(user_id, connector, ...)` function stays unchanged for callers that intentionally want only the personal tier.

**Governance — admin-only writes for both new tiers in v2.** No new `household_operator` role; `is_admin()` (per ADR 012) remains the sole write gate. Ordinary users **never** receive PUT/DELETE on household or system tiers; they may only **consume** household/system material at runtime where the product allows.

**Visibility — admin-only metadata for both new tiers in v2.** Non-admin members do not see that household/system credentials exist, when they were last rotated, or which connectors are configured at those tiers. They experience them solely through successful runtime resolution. Concretely: household/system listings live exclusively under admin routes (e.g. `/api/v1/admin/credentials/household`, `/api/v1/admin/credentials/system`); `/api/v1/me/credentials` is **not** extended to enumerate non-personal tiers.

**Export — household and system tier rows are omitted from every export.** Operator backup/restore for these tiers remains infrastructure-level (`pg_dump`), preserving the ADR 018 threat model. No new admin sealed-export workflow, no new crypto/restore/audit branch in v2. Extend `services/user_export.py`'s omission allowlist and `tests/test_user_export_tables_exhaustive.py` accordingly.

**Audit — real acting admin's `user_id` for tier writes.** `audit_log.user_id` always carries the **real actor's** id; tier discrimination lives in `input_summary` as `tier: "user"|"household"|"system"` (and the consumer-side action audit notes `credential_tier=...` when a non-personal tier supplied the secret). No reserved partition strings (no `__household__` / `__instance_system__`) appear in `user_id` columns. If tier-based audit filtering becomes hot, add a first-class indexed discriminator column then — not now.

**Permissions remain orthogonal.** `connector_permissions` (per migration 016) stays the sole source of ASK/DO truth, keyed `(user_id, connector)`. No `(user_id, connector, tier)` ACL matrix is introduced. Credential tier resolution answers *which secret material applies*; per-user connector permissions answer *whether this user may perform ASK/DO on that connector*. Per-user permission rows already let kids be more restricted than parents without a tier-aware permission table.

## Alternatives Considered

See *(maintainer-local only; not part of the tracked repository)* (and *(maintainer-local only; not part of the tracked repository)* for v1 history).

- **Option A — `scope` column on `user_connector_credentials`** — rejected: vocabulary collision with ADR 015 and breaks the `(user_id, connector)` PK semantics.
- **Option C — sentinel `user_id` rows** (`'__household__'` / `'__system__'` in the existing table) — **explicitly out of scope in v2**: identity columns must not carry pseudo-user partition strings (this is the same principle the v2 audit decision applies to `audit_log.user_id`).
- **Option D — external vault / broker** (Vault, 1Password Connect, etc.) — deferred / opt-in plugin for advanced homelabs; adds Docker-service weight unsuited to default local-first installs.
- **(Considered and rejected for v2) Tier-aware ASK/DO permission matrix** — would reopen migration 016 with a `(user_id, connector, tier)` table; orthogonal-resolution model is sufficient.

## Consequences

**Easier:**
- Household operators stop duplicating secrets per user.
- System integrations get a first-class home that doesn't borrow a human's identity.
- ADR 018 remains the authoritative per-user contract — **zero changes** to its tables, routes, tests, or service module's existing surface.
- Operator mental model is one sentence: *"Lumogis tries the user's secret first, then the household's, then the instance's, then the environment."*
- Audit semantics stay clean: `audit_log.user_id` always means "the real actor".
- Permissions story stays clean: migration 016 is **not** touched by this chunk.

**Harder:**
- Rotation walks three tables instead of one (existing rotation script generalises, but tests must cover all three).
- Every connector consumer must call **the single resolver helper** (`resolve_runtime_credential`) — forking precedence rules in adapters is a regression to be caught in code review.
- Documentation must explain precedence to self-hosters in plain language.
- Admin UI eventually surfaces three credential tiers; needs information architecture work to avoid overwhelming non-admin members (who never see two of them anyway).

**Future chunks must know:**
- `resolve(user_id, …)` is **per-user only** by design — for runtime use that should consult tiers, call `resolve_runtime_credential(caller_user_id, connector)` instead.
- New connector migrations from env → DB should target the **correct tier** explicitly (user vs household vs system) rather than implicitly inheriting behaviour.
- Audit consumers should read `tier` from `input_summary` for credential events; `audit_log.user_id` will always be a real user id.
- Export consumers should treat the new tier tables as **hard omitted** from all per-user export bundles, mirroring `user_connector_credentials`.

## Revisit conditions

- Product requires **subset-of-household** ACL on a specific secret (only some members may consume it) — this would justify either tier-aware permissions or per-secret ACL.
- Product requires **different encryption keys per tier** (e.g. household key escrow, sealed admin backup of system tier).
- Lumogis pivots to **true multi-tenant** hosting — revisit whether `household_id` dimension is required on the household and system tier PKs, and whether memory `scope` and credential `tier` should converge.
- A "household_operator" role becomes a real ask (delegated household secret management without full admin) — currently rejected by decision #1 in v2.
- Tier-based audit filtering becomes a hot query path — promote `tier` from `input_summary` JSON to an indexed first-class column (cheap migration, no semantic change).
- A planning chunk decides `(connector)`-only PK is insufficient (e.g. forward-compat for multi-household) — switch to `(household_id, connector)` with a constant `'default'` household_id; v2 recommends `(connector)`-only.

## Status history

- **2026-04-21**: Draft created by /explore — recommendation Option B (additive tables + precedence). Four open questions deferred to user.
- **2026-04-22**: Refreshed by /explore (v2). User locked all four open questions:
  admin-only writes, admin-only metadata, omit from every export, real acting admin `user_id` in audit + `tier` in `input_summary`. Recommendation unchanged (Option B). Confidence raised Medium → High. Per-user connector permissions chunk (migration 016, ADR/plan `per_user_connector_permissions`) confirmed shipped and orthogonal — **not** reopened by this design. Option C demoted to out-of-scope. Resolver name recommended: `resolve_runtime_credential(caller_user_id, connector)`. PK shape recommended: `(connector)`-only in v2.
- **2026-04-22**: Finalised by /verify-plan — implementation confirmed decision. Migration `018-household-and-instance-system-connector-credentials.sql` shipped with `(connector)`-only PKs; `services/credential_tiers.py` owns the new tier CRUD and `resolve_runtime_credential(caller_user_id, connector)` resolver (user → household → system → env, fail-fast on decrypt error); admin-only routes at `/api/v1/admin/connector-credentials/{household,system}`; audit `user_id` carries the real acting admin (or `"default"` for the `system` actor sentinel) with `tier` in `input_summary` for every `__connector_credential__.*` event; tier rows omitted from per-user export via `_OMITTED_NON_USER_TABLES`; rotation script generalised to walk all three tables with an aggregated `by_tier` summary; diagnostics endpoint returns the new per-tier nested shape (BREAKING — operator-only, called out in CHANGELOG). 1343 tests pass / 12 skipped (PG-required migration regressions skipped in dev). Permissions remain orthogonal — migration 016 untouched. Finalised copy at `docs/decisions/027-credential_scopes_shared_system.md`.
