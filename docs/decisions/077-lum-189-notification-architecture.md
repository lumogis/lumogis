# ADR 077: Notification architecture — unified dispatcher, channel adapters, per-user preferences

**Status:** Finalised
**Created:** 2026-06-02
**Last updated:** 2026-06-02
**Decided by:** `/explore` + `/create-plan` + `/review-plan --arbitrate` (LUM-189)
**Linear issue:** LUM-189
**Exploration:** `.cursor/explorations/LUM-189-notification-architecture.md`
**Plan:** `.cursor/plans/LUM-189-notification-architecture.plan.md`

## Context

Lumogis ships three uncoordinated notification substrates today:

| Substrate | Location | Role today |
| --- | --- | --- |
| **ntfy push** | `ports/notifier.py`, `adapters/ntfy_notifier.py`, `services/ntfy_runtime.py` | Per-user delivery credentials (ADR 018/022); producers call `config.get_notifier().notify(...)` directly |
| **Web Push** | `services/webpush.py`, `routes/api_v1/notifications.py`, `webpush_subscriptions` (migration 019) | Parallel path **not** behind the `Notifier` port; `ROUTINE_ELEVATION_READY` hook sends approval templates |
| **In-app SSE** | `routes/events.py` (`GET /events`) | User-scoped live stream; fed by hook events but not treated as a notification channel |

Additional pieces: **signal digest** (`signals/digest.py`) and **signal processor** (`services/signal_processor.py`) call `get_notifier()` per user; **read-only status façade** (`GET /api/v1/me/notifications` via `services/me_notifications.py`) reports channel configuration but sends nothing. There is **no shared decision layer** for which **notification type** reaches which **user** on which **channel** under **quiet hours**. Preferences are split across ntfy credential rows and Web Push per-device columns (`notify_on_signals`, `notify_on_shared_scope`). Four backlog tickets (LUM-93, LUM-28, LUM-174, LUM-144) depend on these decisions.

**Constraints:** local-first (no mandatory cloud channel), in-process (no new Docker service without justification), reconcile two shipped substrates, **using Lumogis must not require the third-party ntfy app** (ntfy **server** remains an optional max-privacy channel).

Full exploration and evaluation: `.cursor/explorations/LUM-189-notification-architecture.md`, `cursor/evaluations/LUM-189-notification-architecture.assessment.md`.

## Decision

Introduce a **unified, in-process notification layer** at `orchestrator/services/notifications/`:

1. A **notification-type taxonomy** with **four priority tiers**.
2. A **per-user preference store** (non-secret routing prefs separate from ntfy **credentials** per ADR 022 scope).
3. A **dispatcher** that evaluates one decision sequence before fan-out to **channel adapters**.
4. **ntfy**, **Web Push**, and **in-app SSE** become uniform `NotificationChannel` adapters; the **notification inbox (LUM-174)** is the persistent log over the in-app tier.

**This ADR (LUM-189) records the architecture and programme contract only.** Implementation lands in sequenced child tickets: **LUM-93 → LUM-144 → LUM-28 → LUM-174**.

### ADR 022 scope clarification

ADR 022's rule *"do NOT introduce a parallel `user_notifier_prefs` table — extend the credential payload instead"* applies to **ntfy delivery credentials** (`url`, `topic`, `token` — secret, connector-scoped). **Routing preferences** (type × channel enablement, quiet-hours windows, timezone) are a **different concern** and use dedicated tables documented below. See [ADR 022](022-ntfy-runtime-per-user-shipped.md) — forward reference under Revisit conditions.

## Alternatives considered

| Option | Verdict | Summary |
| --- | --- | --- |
| **Option 1 — Unified in-process dispatcher + preference store** | **Accepted** | Preference store + dispatcher + channel adapters; matches five-concept model; fixes Web Push / ntfy asymmetry |
| **Option 2 — Self-hosted router (Apprise / Alphorn) as new Docker service** | Rejected | Solves fan-out only; prefs/quiet hours/tiers/inbox stay in Lumogis; adds ops weight. Apprise may become an *optional outbound adapter* later (LUM-198) |
| **Option 3 — Incremental per-ticket, no unified layer** | Rejected | Formalises fragmentation; guarantees rework across LUM-93/28/144/174 |
| **Option 4 — Web Push only, drop ntfy** | Rejected | Sacrifices max-privacy self-hosted channel; make ntfy **app-optional**, not deleted |

Detail: `.cursor/explorations/LUM-189-notification-architecture.md` § Options Considered.

## Taxonomy

### Enums (implemented in LUM-93 — `orchestrator/models/notifications.py`)

```python
class NotificationType(str, Enum):
    ROUTINE_ELEVATION = "routine_elevation"
    SIGNAL_RECEIVED = "signal_received"
    SIGNAL_DIGEST = "signal_digest"
    ACTION_EXECUTED = "action_executed"
    SECURITY_ALERT = "security_alert"       # future — LUM-424
    CONSOLIDATION_DONE = "consolidation_done"  # background tier example

class NotificationTier(str, Enum):
    URGENT = "urgent"
    ACTION_REQUIRED = "action_required"
    INFORMATIONAL = "informational"
    BACKGROUND = "background"

class ChannelId(str, Enum):
    NTFY = "ntfy"
    WEB_PUSH = "web_push"
    IN_APP = "in_app"
```

### Event → type → tier mapping

| Hook / producer | `NotificationType` | `NotificationTier` | Notes |
| --- | --- | --- | --- |
| `ROUTINE_ELEVATION_READY` | `routine_elevation` | `action_required` | Today also fires Web Push hook directly — migration target |
| `SIGNAL_RECEIVED` (`signal_processor`) | `signal_received` | `informational` | Per-user `get_notifier()` today |
| `signals/digest.py` | `signal_digest` | `informational` | Per-user fanout today |
| `ACTION_EXECUTED` | `action_executed` | `action_required` | **LUM-28** — deferred Web Push template |
| Storage / quota alerts | `security_alert` | `urgent` | **LUM-424** — future producer |
| Background consolidation jobs | `consolidation_done` | `background` | in-app only; example tier |

### Tier → ntfy `priority` float

Existing `Notifier.notify(..., priority: float, ...)` uses 0.0–1.0. The ntfy channel maps `NotificationTier`:

| `NotificationTier` | ntfy `priority` |
| --- | --- |
| `urgent` | `1.0` |
| `action_required` | `0.75` |
| `informational` | `0.5` |
| `background` | *(ntfy not in tier `default_channels` — channel skipped)* |

## Preference storage

### Schema contract (migration in LUM-93)

```sql
-- Per-user quiet-hours + timezone (sparse prefs do NOT carry these)
CREATE TABLE IF NOT EXISTS notification_user_settings (
    user_id            TEXT NOT NULL DEFAULT 'default',
    timezone           TEXT,           -- IANA; NULL → UTC + notification_timezone_fallback WARNING
    quiet_hours_start  TIME,           -- LUM-144 may populate / extend policy windows
    quiet_hours_end    TIME,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id)
);

-- Sparse channel enable/disable — keyed (user_id, notification_type, channel)
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id            TEXT NOT NULL DEFAULT 'default',
    notification_type  TEXT NOT NULL,
    channel            TEXT NOT NULL,  -- ntfy | web_push | in_app
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, notification_type, channel)
);
CREATE INDEX IF NOT EXISTS ix_notification_prefs_user
    ON notification_preferences (user_id);

-- Instance-level: which tiers may bypass quiet hours (admin config)
CREATE TABLE IF NOT EXISTS notification_tier_policy (
    tier               TEXT PRIMARY KEY,
    bypass_quiet_hours BOOLEAN NOT NULL DEFAULT FALSE,
    default_channels   TEXT[] NOT NULL  -- ordered fan-out list
);

INSERT INTO notification_tier_policy (tier, bypass_quiet_hours, default_channels) VALUES
  ('urgent', TRUE,  '{ntfy,web_push,in_app}'),
  ('action_required', FALSE, '{ntfy,web_push,in_app}'),
  ('informational', FALSE, '{ntfy,web_push,in_app}'),
  ('background', FALSE, '{in_app}')
ON CONFLICT (tier) DO NOTHING;
```

**Rules:**

- Routing prefs are **non-secret**; ntfy topic/token/url stay in `user_connector_credentials` only.
- Users may set `enabled=false` on a sparse row to **disable** a tier-listed channel; they **cannot** enable a channel outside `default_channels` (LUM-93 PATCH rejects or ignores).
- Missing sparse rows → tier defaults from `notification_tier_policy`.

### Web Push migration (LUM-93)

Collapse per-device `webpush_subscriptions.notify_on_*` into sparse `notification_preferences` using **OR across devices**:

- `notify_on_signals=true` on **any** device → seed `enabled=true` for `(user_id, signal_received, web_push)` and `(user_id, signal_digest, web_push)` unless an explicit sparse row exists.
- `notify_on_shared_scope=true` on **any** device → seed shared-scope types per mapping table in LUM-93 implementation.
- Per-device columns become **optional overrides** only (deprecated as source of truth).

**Rollback (LUM-93):** `DROP TABLE notification_preferences, notification_user_settings, notification_tier_policy`; restore producer direct `get_notifier()` calls.

## Dispatcher

### `TypedNotification` emit contract

```python
class TypedNotification(BaseModel):
    emit_id: str          # UUID — correlation for LUM-174 inbox / SSE dedup; dispatcher assigns if omitted
    user_id: str
    notification_type: NotificationType
    tier: NotificationTier  # resolved by dispatcher from type unless documented producer override (none in v1)
    title: str
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)  # no secrets; no credentials
```

Producers call `services.notifications.dispatcher.emit(TypedNotification(...))` instead of `get_notifier()` / `webpush.send_*` directly.

### Decision sequence

For each `TypedNotification` emit:

1. Assign `emit_id` (UUID) if missing; resolve `user_id` — **reject** emit if missing or empty.
2. Map `notification_type` → default `NotificationTier` (table above) unless producer supplied a documented v1 override (none planned).
3. Load `notification_user_settings`, sparse `notification_preferences` for `(user_id, notification_type, *)`, and `notification_tier_policy` for tier.
4. **Channel set capped by tier policy:** iterate every channel in `tier.default_channels` **in order** — **fan-out to all** eligible channels (not fallback). Users may disable via sparse `enabled=false`; cannot add channels outside tier list.
5. For each channel:
   - Skip if deployment channel unavailable (`NOTIFIER_BACKEND`, VAPID, ntfy server).
   - Skip if sparse preference `enabled=false`.
   - Skip if channel credentials not configured (ntfy) — log `connector_not_configured`, continue others.
6. Evaluate quiet hours (server-side) using `notification_user_settings.timezone` (see Quiet hours); absent row → UTC + `notification_timezone_fallback` WARNING:
   - If in quiet window **and** tier `bypass_quiet_hours=false` **and** channel is push (`ntfy` \| `web_push`): skip channel.
   - `in_app` is **not** suppressed by quiet hours unless preference disables it.
7. If tier `bypass_quiet_hours=true` and push would have been skipped by step 6: deliver anyway; emit **structured audit event** (see Security & audit).
8. Invoke channel adapter: `channel.deliver(notification)` — errors on one channel do not abort others.
9. **Urgent floor:** if `tier=urgent` and zero push channels would deliver after steps 5–8, still deliver `in_app` when available; log `WARNING` `notification_urgent_zero_push_channels` with `emit_id`, `user_id`, `notification_type`.
10. Return aggregate `DispatchResult`.

```python
class ChannelDeliveryResult(BaseModel):
    channel: ChannelId
    status: Literal["delivered", "skipped", "failed"]
    reason: str | None = None

class DispatchResult(BaseModel):
    emit_id: str
    user_id: str
    notification_type: NotificationType
    tier: NotificationTier
    channels: list[ChannelDeliveryResult]
    outcome: Literal["delivered", "partial", "all_skipped", "failed"]
```

### Performance

Preference lookup is **O(sparse rows)** per user per emit — acceptable at household scale. Channel sends may use existing `fire_background()` / thread pools (Web Push already uses `ThreadPoolExecutor`). No Redis/broker at household scale.

## Channel adapters

### Protocol (`orchestrator/ports/notification_channel.py` — LUM-93)

```python
@runtime_checkable
class NotificationChannel(Protocol):
    channel_id: ChannelId

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
        """Return success/skipped/failed; never raise for expected skips."""
        ...
```

Each adapter is a **singleton** bound to one `channel_id`; dispatcher calls `deliver(notification)` only.

| Channel | Implementation seam | Notes |
| --- | --- | --- |
| **ntfy** | Existing `Notifier` / `NtfyNotifier` internally | Per-user credentials via `ntfy_runtime`; tier→priority table above; HTTP 410 → `delivery_paused` unchanged |
| **web_push** | `services/webpush.py` → adapter | VAPID; minimal templates (ADR 030); no raw hook kwargs in payloads |
| **in_app** | `routes/events.py` SSE registry | Enqueues to existing `_connections`; LUM-174 inbox references `emit_id` |

`NOTIFIER_BACKEND` evolves from hard single-channel switch toward per-user channel availability; document deprecation path in LUM-93.

## Quiet hours

- **Gate:** server-side in dispatcher (not device DND alone).
- **Policy windows:** LUM-144 supplies/extends `quiet_hours_start` / `quiet_hours_end` on `notification_user_settings`.
- **Timezone:** IANA string on `notification_user_settings.timezone`. **Preferred source:** user profile/location when **LUM-221** (or equivalent) ships — dispatcher copies into settings. **Until then:** missing row or null column → **UTC** with `WARNING` `notification_timezone_fallback`. LUM-93 must **not** block on LUM-221.
- **Bypass:** only tiers with `notification_tier_policy.bypass_quiet_hours=true` (default: `urgent` only); set by **admin** tier policy, not per-message user flag.

## Security & audit

| Concern | Contract |
| --- | --- |
| **Auth** | Future prefs routes: `require_user`; admin tier policy: admin role |
| **SQL** | Parameterised queries only |
| **user_id isolation** | Every preference query filters `user_id`; dispatcher rejects missing `user_id` |
| **Secrets** | Routing prefs non-secret; credentials stay in `user_connector_credentials` |
| **Cross-user** | Dispatcher never fans out across users |
| **Web Push payloads** | Minimal templates — no raw hook kwargs (ADR 030 preserved) |
| **Quiet-hours bypass audit** | **Not** Postgres `audit_log` (action-execution-only per `actions/audit.py`). Every bypass emits **structlog** on logger **`lumogis.audit`** per [ADR 019](019-structured-audit-logging.md): event `notification.quiet_hours_bypass`; fields `emit_id`, `user_id`, `notification_type`, `tier`, `channels_delivered`, `occurred_at` (UTC ISO). **Must not** include `title`/`body`. Dedicated Postgres notification-audit table is **out of scope** for v1. |

## Producer migration map

| Current call site | Today | After LUM-93+ |
| --- | --- | --- |
| `services/signal_processor.py` | `config.get_notifier().notify(...)` | `dispatcher.emit(TypedNotification(SIGNAL_RECEIVED, ...))` |
| `signals/digest.py` | per-user `get_notifier().notify(...)` | `dispatcher.emit(SIGNAL_DIGEST, ...)` |
| `services/webpush.py` hook | direct `send_*` on `ROUTINE_ELEVATION_READY` | dispatcher → `web_push` channel |
| `routes/events.py` hooks | SSE fanout (keep) | dispatcher → `in_app` enqueues same `_connections` registry |

## Build programme

| Order | Ticket | Delivers | Primary paths |
| --- | --- | --- | --- |
| 1 | **LUM-93** | Migration, dispatcher, prefs API + Web UI, channel adapters | `postgres/migrations/`, `orchestrator/services/notifications/`, `orchestrator/models/notifications.py`, `routes/api_v1/notification_preferences.py`, `clients/lumogis-web/` |
| 2 | **LUM-144** | Quiet-hours policy → dispatcher inputs | `orchestrator/services/notifications/quiet_hours.py` (or policy module) |
| 3 | **LUM-28** | `ACTION_EXECUTED` Web Push under taxonomy | `services/webpush.py`, templates |
| 4 | **LUM-174** | Persistent inbox over SSE / in-app tier | `postgres/migrations/`, inbox routes |
| — | **LUM-198** | Optional messaging adapter (Apprise revisit) | new `Channel` impl |
| — | **LUM-424** | Storage alerts → `security_alert` / `urgent` | producer wiring |
| — | LUM-336 / LUM-329 | Tauri native ntfy/SSE client | `clients/lumogis-search/` |

**Next plan:** `/create-plan LUM-93`.

### Future API surfaces (LUM-93 — specified here, not implemented in LUM-189)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/me/notification-preferences` | Read effective prefs + defaults |
| PATCH | `/api/v1/me/notification-preferences` | Update sparse rows |
| GET | `/api/v1/admin/notification-tier-policy` | Read bypass-eligible tiers |
| PATCH | `/api/v1/admin/notification-tier-policy` | Set bypass + audit |

`GET /api/v1/me/notifications` remains read-only until LUM-93 extends it.

### Expected tests (LUM-93 — not this chunk)

| Module | Proves |
| --- | --- |
| `tests/test_notification_dispatcher.py` | Decision sequence; quiet hours; urgent bypass + `notification.quiet_hours_bypass`; urgent zero-push → in_app floor |
| `tests/test_notification_dispatcher.py` | Channel outside tier `default_channels` cannot be enabled |
| `tests/test_notification_preferences.py` | Sparse defaults; per-user isolation |
| `tests/test_notification_taxonomy.py` | Event→tier mapping stable |

## Environment variables

| Name | Default | Role |
| --- | --- | --- |
| `NOTIFIER_BACKEND` | `none` | Deployment ntfy availability; evolves to per-user enablement |
| `NTFY_URL` | `http://ntfy:80` | ntfy server default (unchanged) |
| VAPID_* | *(existing)* | Web Push (unchanged) |

Per-user channel enablement supersedes hard single-backend routing over time (ADR 022 revisit).

## Consequences

**Easier:** LUM-93/28/174/144 build against one schema; new channels (LUM-198) implement one adapter; producers emit typed notifications; quiet hours and auditable urgent-bypass have a single home; SSE reused for in-app/inbox.

**Harder:** Postgres migration + Web Push column reconciliation; coordinated producer refactor; `NOTIFIER_BACKEND` semantics evolve.

**Interoperability:**

- **LUM-93** must not store routing prefs in `user_connector_credentials`.
- **LUM-28** must use `NotificationType.ACTION_EXECUTED` + tier `action_required`.
- **LUM-174** inbox rows reference `emit_id` for dedup.
- **LUM-144** feeds quiet-hours windows; timezone from LUM-221 when available.
- **LUM-424** maps to `security_alert` / `urgent`.

## Revisit conditions

- **LUM-198 messaging channel:** revisit Apprise as optional outbound adapter behind dispatcher.
- **Household scale exceeds in-process assumptions:** revisit queue + preference-cache (Redis/broker).
- **Per-device preference granularity beyond Web Push:** add device dimension to preference key.
- **ntfy priority + device DND insufficient:** revisit centralising all gating server-side.
- **Maintainer drops ntfy server:** revisit Web-Push-only trade-off.
- **Dedicated Postgres notification-audit table:** only if queryability requirements exceed structlog mirror.

## Status history

- 2026-06-02: Draft from `/explore` (LUM-189); R1–R5 locked in exploration.
- 2026-06-02: Plan + `/review-plan --arbitrate` R1 — audit on `lumogis.audit` structlog (not `audit_log`); `notification_user_settings`; `emit_id` / `DispatchResult`; urgent in_app floor; Web Push OR-collapse rule.
- 2026-06-02: **Finalised by `/implement LUM-189`** — ADR 077 + docs sync (no runtime code).
- 2026-06-02: Finalised by `/verify-plan` — implementation confirmed decision; docs-only chunk complete.
