# ADR 021: CalDAV per-user connector credentials migration

**Status:** Finalised
**Created:** 2026-04-21
**Last updated:** 2026-04-21
**Decided by:** /explore + /create-plan + /review-plan (composer-2 / Claude Opus 4.7)
**Finalised by:** /verify-plan 2026-04-21 (composer-2 / Claude Opus 4.7)
**Plan:** *(maintainer-local only; not part of the tracked repository)*
**Draft mirror:** *(maintainer-local only; not part of the tracked repository)*

## Context

CalDAV today (pre-chunk) used three deployment-wide environment variables
(`CALENDAR_CALDAV_URL`, `CALENDAR_USERNAME`, `CALENDAR_PASSWORD`) inside
`adapters/calendar_adapter.CalendarAdapter`. Multi-user work (B11 / ADR
013) had already namespaced the deterministic Qdrant `signal_id` by
`user_id` via `services/point_ids.caldav_signal_id(user_id, uid)`, but
the credential-resolution path was still single-user. ADR 018
(`per-user-connector-credentials`) established the substrate
(`user_connector_credentials` table + `services/connector_credentials.py`
+ Fernet/MultiFernet) and locked the rollout order
`testconnector → ntfy → caldav → llm provider keys`. This chunk is the
**third** consumer in that order.

Shared/system credentials and household-wide CalDAV are explicitly out
of scope and tracked under the separate `credential_scopes_shared_system`
exploration.

## Decision

Store each user's CalDAV connection parameters as a single encrypted
JSON payload under **`connector = "caldav"`** in
`user_connector_credentials`, registered in `connectors.registry` via
`CONNECTORS[CALDAV] = ConnectorSpec(id=CALDAV, description=…)`.

**Wire payload (v1):**

```json
{
  "base_url": "https://nextcloud.example.com/remote.php/dav/",
  "username": "alice",
  "password": "<secret>"
}
```

All three fields are required, must be non-empty strings, and `base_url`
must satisfy `urllib.parse.urlparse(value).scheme in {"http", "https"}`
(case-insensitive) AND non-empty `netloc.strip()` AND no leading/
trailing whitespace. Extra top-level keys are tolerated for
forward-compat (e.g. future `auth_type`).

**Resolution module:** new `orchestrator/services/caldav_credentials.py`
mirrors the `services/ntfy_runtime.py` precedent. Public surface:

* `@dataclass(frozen=True) CaldavConnection(base_url, username, password)`
* `load_connection(user_id) -> CaldavConnection`

Resolution order:

1. `services.connector_credentials.get_payload(user_id, "caldav")` —
   registry-strict via `require_registered`.
2. Row present → `_validate_payload(...)` → `CaldavConnection(...)`.
3. Row absent + `AUTH_ENABLED=true` → raise `ConnectorNotConfigured`
   (no env fallback at request time — D9 fail-loud, no auto-migrator).
4. Row absent + `AUTH_ENABLED=false` → read `CALENDAR_CALDAV_URL` /
   `CALENDAR_USERNAME` / `CALENDAR_PASSWORD` env trio as the legacy
   `"default"` user's connection (D10).

**Adapter:** `CalendarAdapter` reads `self._config.user_id` and calls
`load_connection` once per `CalendarAdapter` instance (per-poll cache).
`ping` and `poll` never raise out of the module — every resolution
failure path returns `False` / `[]` after a structured WARNING log
whose fields are exactly `{user_id, connector, code}`. The exception
object is **never** included in the log record (no `exc_info=`, no
`%r` / `%s` of `exc`, no `repr`/`str` interpolation — security:
`caldav` / `requests` / `urllib3` can carry credential URLs in
`repr(exc)`). Pinned by
`test_adapter_skip_log_does_not_leak_credentials`.

**Domain code mapping** (per ADR 018 D6 conflation ban):

| Failure | Code |
|---------|------|
| no row + no env fallback usable | `connector_not_configured` |
| `UnknownConnector` (defensive — registry regression) | `connector_not_configured` |
| substrate decrypt failure (`CredentialUnavailable` from `get_payload`) | `credential_unavailable` |
| structurally malformed payload (`CredentialUnavailable` from `_validate_payload`) | `credential_unavailable` |
| empty required string OR `base_url` URL-shape failure (`ValueError`) | `credential_unavailable` |
| upstream CalDAV 401/403 | NOT remapped — existing `_log.error` path |

`connector_access_denied` is **never** raised on the CalDAV path (it is
read-only today; permission gating arrives only when calendar **write**
tools land).

**Background polling:**

* Canonical multi-user path = DB `sources` rows with
  `source_type='caldav'` polled by `signals/feed_monitor.py`. Per-source
  `sources.poll_interval` (default 3600 s, `postgres/init.sql:177`) is
  the canonical multi-user poll-cadence knob.
* Legacy single-user path = `signals/calendar_monitor.py`. Refuses to
  schedule under `AUTH_ENABLED=true` and emits an idempotent operator-
  facing INFO log on first `start()` call (module-level
  `_AUTH_DISABLED_LOGGED` guard, reset by `stop()`). Under
  `AUTH_ENABLED=false` it reads `CALENDAR_CALDAV_URL` /
  `CALENDAR_POLL_INTERVAL` at call-time (not at module import) and
  writes signals under the `_LEGACY_USER_ID = "default"` constant.

**Folded-in fix:** `routes/signals.py::add_or_preview_source` previously
constructed `SourceConfig(...)` without passing `user_id`, silently
defaulting to `"default"` for **all** source types (rss, page,
playwright, caldav). Fix: `user_id=user.user_id` is now passed
explicitly at the construction site that flows into `schedule_source`.

**Connector permissions:** `permissions.get_connector_mode("caldav", …)`
is unchanged — CalDAV is read-only, `connector_access_denied` does not
arise. Per-user permissions inherit from the future
`per_user_connector_permissions` chunk for free.

**v1 known limitation (documented, not fixed):** CalDAV
`Signal.url=""` (`adapters/calendar_adapter._event_to_signal`)
disables URL-based deduplication in `feed_monitor._is_duplicate` and
collapses the `process_signal` `_score_cache` (keyed on
`md5(url.encode())`) to a single key. Every poll re-runs the full LLM
pipeline for every event in the lookahead window. The DB row is
saved-once via `INSERT … ON CONFLICT (signal_id) DO NOTHING`
(`signal_id = caldav_signal_id(user_id, uid)`), so DB cost is bounded
but **LLM cost is not**. Mitigation in v1 is operator-facing
documentation (`docs/connect-and-verify.md` recommends
`sources.poll_interval >= 1800 s` for `caldav` rows). The structural
fix is gated on a follow-up signal-contract ADR and tracked as plan
Open question #6.

## Alternatives Considered

- **Env-only forever** — rejected: violates ADR 018 D3 for
  multi-user `AUTH_ENABLED=true` deployments; secrets visible in
  process env to every connector.
- **Split URL plaintext / password encrypted** — rejected for v1:
  unnecessary complexity; the substrate already encrypts opaque JSON
  payloads ≤ 64 KiB and treats the whole payload as opaque.
- **Dedicated Postgres columns for CalDAV** — rejected: contradicts
  the single-table substrate established by ADR 018.
- **Inline resolver in `CalendarAdapter`** — rejected: the resolver
  has its own error-mapping contract (`ConnectorNotConfigured` vs
  `CredentialUnavailable` vs `ValueError`) that needs to be
  centralised so the adapter's `_get_connection` can map domain
  code → log line in one place. Also matches the `ntfy_runtime.py`
  precedent.
- **Auto-migrate env → row at boot** — rejected: shared-credential
  territory (which user owns the env-set value?) and deferred to
  ADR 018 D7. If operators complain post-rollout, a follow-up
  opt-in `python -m scripts.bootstrap_caldav_from_env --user-id <id>`
  script can be added without re-litigating this decision.
- **Drop `payload.base_url`, use `sources.url`** — kept in payload
  for v1 (matches `ntfy_runtime.py` precedent and lets a future
  runtime "test connection" route work without a `sources` row).
  `sources.url` is **operator-facing label only** for `caldav` rows;
  the adapter reads `payload.base_url` exclusively. Long-term
  cleanup tracked as plan Open question #5.

Full analysis: *(maintainer-local only; not part of the tracked repository)*

## Consequences

**Easier:**
- One connector id (`caldav`) for docs, tests, and admin UX.
- Rotation, audit, and export-omission inherit substrate behaviour
  (no `__caldav__.*` audit row needed — `__connector_credential__.*`
  already covers the lifecycle).
- Multi-user attribution gap closes for **all** source types as a
  side effect of the `routes/signals.py` `user_id` fix.
- `feed_monitor` already threads `source.user_id` through to the
  adapter — no scheduler changes needed for the canonical path.

**Harder:**
- Operators on multi-user deployments must migrate env config to
  per-user `PUT /api/v1/me/connector-credentials/caldav` calls.
- Background `calendar_monitor` (legacy `__caldav__` job) is now
  silent under `AUTH_ENABLED=true` — operators relying on it must
  add a `sources` row instead.
- Pre-existing CalDAV `Signal.url=""` LLM-cost behaviour is now
  more visible as a per-source cadence knob; mitigation is
  operator policy (documented `>= 1800 s` floor) until the
  signal-contract ADR follow-up lands.

**Future chunks must know:**
- Shared / household / system CalDAV credentials need a separate
  ADR (see `credential_scopes_shared_system` exploration). Never
  special-case the `_LEGACY_USER_ID = "default"` env path to fake
  a household tier — the legacy path is single-user dev only.
- `connector_access_denied` is for permission checks, not upstream
  HTTP 401. Future calendar **write** tools (`caldav.create_event`,
  etc.) will use `permissions.check_permission(connector="caldav", …)`
  and the existing `actions.executor` Ask/Do gating; they will
  reuse `caldav_credentials.load_connection(user_id)` unchanged.
- `Signal.url=""` for CalDAV is a known v1 limitation — the
  follow-up signal-contract ADR (Open question #6) needs to decide
  what value to use (likely `caldav_signal_id` or a
  `caldav://<host>/<uid>` deep-link) and wire either URL-based or
  `signal_id`-based dedupe into `_is_duplicate`. Until then,
  operators with paid LLM backends should keep
  `sources.poll_interval >= 1800 s`.
- The `register(name, *, description)` registry API is the only
  way to add a new connector; the description-less overload was
  deliberately removed in the `credential_management_ux` chunk so
  that "registered without description" is a structural
  impossibility.

## Revisit conditions

- Explicit product request for household-wide or shared-calendar
  credentials → triggers the `credential_scopes_shared_system`
  follow-up plan.
- CalDAV write tools or MCP tools that need distinct HTTP error
  mapping to 424 → triggers the deferred runtime "test connection"
  route work (plan Open question #3).
- `per_user_connector_permissions` lands → re-verify Ask/Do for
  any future `caldav` write actions.
- Operator demand for live-CalDAV CI coverage → spin up Radicale
  in `docker-compose.test.yml` (mirrors ADR 013 mocked-only
  precedent; out of scope for v1).
- Signal-contract ADR for `Signal.url` semantics on calendar
  events → unblocks the LLM-cost fix tracked under plan Open
  question #6 (`caldav_signal_url_and_dedupe`).
- A future runtime route wants to distinguish "row exists but
  malformed" (422) from "row exists but undecryptable" (503)
  → introduces `CaldavPayloadInvalid(ValueError)` per plan Open
  question #3.

## Status history

- 2026-04-21: Draft created by /explore (`caldav_connector_credentials`).
- 2026-04-21: /create-plan locked 14 binding decisions D1–D14 from user-supplied answers to 8 structured open questions + 6 free-text follow-ups.
- 2026-04-21: /review-plan --self → 5 in-place SR fixes (registry description, `payload.base_url` vs `sources.url` precedence, `ntfy_runtime.py` precedent, call-time env reads, `_IsolationStore` fixture preflight). ADR unchanged.
- 2026-04-21: /review-plan --critique (composer-2-fast) → ⚠️ Ready with fixes (headline: `Signal.url=""` LLM-cost amplifier; `ValueError` code mapping gap; URL validation precision; idempotent INFO log; raw exception logging ban). ADR unchanged.
- 2026-04-21: /review-plan --arbitrate → 9 accepted, 1 partial (Signal.url=""` accepted as v1 non-goal + Open question #6), 2 rejected. ADR unchanged.
- 2026-04-21: Implemented + verified by /verify-plan. Tests 1102 passing / 0 failing / 9 skipped (full orchestrator suite). Architectural decision matches implementation 1:1; finalised at this path.
