# ADR 022: ntfy runtime per-user (rollout step 2 of ADR 018)

**Status:** Finalised
**Created:** 2026-04-21
**Last updated:** 2026-04-21
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-04-21 (Claude Opus 4.7)
**Plan:** none — shipped before formal plan / verify cycle for this slice
**Exploration:** *(maintainer-local only; not part of the tracked repository)*
**Draft mirror:** *(maintainer-local only; not part of the tracked repository)*

## Context

ADR 018 (`per_user_connector_credentials`) shipped the substrate (encrypted `user_connector_credentials` table, registry, CRUD routes, audit, rotation, backup-export omission, boot-time refusal). Its §D5 locked a rollout order onto real connectors — **testconnector → ntfy → CalDAV → LLM provider keys** — each chunk to be its own plan.

**Step 2 (ntfy)** shipped without going through `/create-plan` → `/verify-plan`. The earlier `/explore per_user_notifier_targets` (2026-04-18) had recommended a different design (Option 2: separate `user_notifier_prefs` sibling table with `NTFY_TOPIC_PREFIX` env), but by the time the work happened the ADR 018 substrate was live, making the sibling table redundant. The chunk landed directly against the substrate.

This ADR is the **retrospective** record: it locks the as-built decision so future planners do not re-litigate it, supersedes the prior exploration's Option 2 design, and gives steps 3 (CalDAV — already shipped) and 4 (LLM provider keys — pending) a documented prior-art reference.

Audit: `docs/private/MULTI-USER-AUDIT.md` row **B5** ("one ntfy topic per user"), priority #8 in `MULTI-USER-AUDIT-RESPONSE.md` §6.

## Decision

The ntfy notifier resolves its delivery config per call from the ADR 018 `user_connector_credentials` table under connector id `"ntfy"`, NOT from a dedicated `user_notifier_prefs` sibling table. The `Notifier.notify` Protocol gains a keyword-only required `user_id: str` parameter. The signal digest fans out per active user (one notification per user with in-window signals) instead of emitting a single household-global notification. Legacy `NTFY_TOPIC` / `NTFY_TOKEN` env vars become single-user-dev fallbacks only (consulted iff `AUTH_ENABLED=false`); `NTFY_URL` survives as a non-secret deployment-wide default. No new schema migration, no new HTTP routes, no new env vars, no new pip dependencies.

### As-implemented surface (verified 2026-04-21)

- **Connector id:** `ntfy`, registered in `orchestrator/connectors/registry.py::CONNECTORS` with description `"ntfy push-notification connector — per-user url/topic/token."` (the constant `NTFY = "ntfy"` is exported for typed callers).
- **Per-user payload (sealed JSON):** `{"url": "<server URL — optional>", "topic": "<required>", "token": "<bearer — optional>"}`. Extra keys tolerated for forward-compat; `_validate_payload` is implicit in `services.connector_credentials.get_payload` (registry-strictness only).
- **Resolver:** `orchestrator/services/ntfy_runtime.py::load_ntfy_runtime_config(user_id) -> NtfyRuntimeConfig` (TypedDict `{url, topic, token}`).
  - Row present (either auth mode): payload values win; missing `topic` → `ConnectorNotConfigured`; missing `url` → `NTFY_URL` env, then `http://ntfy:80`.
  - `AUTH_ENABLED=true` + no row → `ConnectorNotConfigured`. `NTFY_TOPIC` / `NTFY_TOKEN` are NOT consulted.
  - `AUTH_ENABLED=false` + no row → env-fallback; empty `NTFY_TOPIC` → `ConnectorNotConfigured`.
  - Decrypt failure → `CredentialUnavailable` propagated from `services.connector_credentials`.
- **Notifier port:** `orchestrator/ports/notifier.py::Notifier.notify(self, title, message, priority, *, user_id: str) -> bool`. Keyword-only required.
- **Adapter:** `orchestrator/adapters/ntfy_notifier.py::NtfyNotifier.notify` resolves config per call (no `__init__` env caching). `ConnectorNotConfigured` → `_log.info(... code=connector_not_configured ...)` + `return False`. `CredentialUnavailable` → `_log.warning(... code=credential_unavailable ...)` + `return False`. HTTP 200/201 → `True`; non-2xx / network error → `WARNING` log + `False`. `Authorization: Bearer <token>` header set iff `cfg["token"]` is non-empty.
- **Null adapter:** `orchestrator/adapters/null_notifier.py::NullNotifier.notify` accepts `user_id` to satisfy the Protocol; logs at DEBUG and returns `True`.
- **Call sites (exhaustive):**
  - `orchestrator/services/signal_processor.py::_notify(signal)` calls `notifier.notify(signal.title, signal.content_summary, signal.importance_score, user_id=signal.user_id)`.
  - `orchestrator/signals/digest.py::_send_digest()` enumerates `SELECT DISTINCT user_id FROM signals WHERE created_at >= %s AND user_id IS NOT NULL ORDER BY user_id`, then for each active user calls `_fetch_top_signals_for_user(user_id, since)` (top `SIGNAL_DIGEST_COUNT` rows, ordered by relevance/importance) and `notifier.notify(title, message, priority=0.5, user_id=user_id)`. `SIGNAL_DIGEST_COUNT` is now per user, not household-global.
- **Env surface (no additions):**
  - `NOTIFIER_BACKEND=ntfy` — pre-existing deployment switch; selects this adapter.
  - `NTFY_URL` — non-secret default ntfy server URL (operator infrastructure). Honoured in both auth modes when a payload omits `url`.
  - `NTFY_TOPIC`, `NTFY_TOKEN` — `AUTH_ENABLED=false` fallback only.
  - `SIGNAL_DIGEST_ENABLED`, `SIGNAL_DIGEST_INTERVAL`, `SIGNAL_DIGEST_COUNT` — pre-existing; `_COUNT` semantics now per user.
- **HTTP surface (no additions):** the substrate's `PUT/GET/DELETE /api/v1/me/connector-credentials/ntfy` and admin-on-behalf `/api/v1/admin/users/{user_id}/connector-credentials/ntfy` are the operator-facing CRUD; no per-connector specialisation.
- **Tests:** `orchestrator/tests/test_ntfy_runtime.py` (auth-mode matrix, env fallback, decrypt-failure propagation), `orchestrator/tests/test_ntfy_notifier.py` (per-call resolution, domain-failure mapping, HTTP success/non-2xx/network-error, Authorization gating), `orchestrator/tests/test_signal_digest.py` (per-user fanout enumeration, empty-window skip), `orchestrator/tests/test_signal_processor.py` extended to pin `notify(..., user_id=signal.user_id)` wiring.
- **Docs:** `docs/extending-the-stack.md` ntfy section rewritten lead-with-per-user; `.env.example` ntfy block reframed with explicit `# AUTH_ENABLED=false fallback only` annotations on `NTFY_TOPIC` / `NTFY_TOKEN`.

### What was NOT changed (explicitly deferred)

- No new Postgres table (the prior exploration's `user_notifier_prefs` and `postgres/migrations/011-per-user-notifier-prefs.sql` are dropped; the substrate's `user_connector_credentials` is sufficient).
- No new HTTP routes (`GET/PUT /api/v1/me/notifier`, `PATCH /api/v1/admin/users/{id}/notifier` from the prior exploration are dropped — substrate routes cover the surface).
- No new env vars (`NTFY_TOPIC_PREFIX` from the prior exploration is dropped).
- No new pip dependency.
- No host-default topic derivation (`f"{NTFY_TOPIC_PREFIX}-{user_id[:12]}"` from prior exploration is dropped — operators set `topic` explicitly per user, matching the substrate's deliberate "no household defaults" stance from ADR 018).
- No QR-code helper for mobile ntfy app subscription (out of scope; deferred to a future UX chunk against ADR 020).
- No connector-specialised UI form in `web/index.html` — operators set the credential via the generic credential-management modal from ADR 020.
- No fanout for non-digest household-batch jobs (`services/routines.weekly_review`, etc.) — audit B7 stays separate.
- No multi-channel notifier (Apprise / Web Push / Matrix) — the `Notifier` Protocol still assumes one channel per `NOTIFIER_BACKEND` deployment switch.
- No household-broadcast / `user_id="__system__"` sentinel (would need a household-default credential row or env var; not implemented).

## Alternatives considered

- **Option 2 from prior exploration — `user_notifier_prefs` sibling table.** Rejected at ship time: the ADR 018 substrate had landed first; a sibling table would have duplicated encryption, audit, admin-on-behalf, rotation, backup-export, and registry. Cost-of-change was higher than the UX gain of a dedicated `/api/v1/me/notifier` route.
- **Option 1 from prior exploration — derived topic only, no override.** Rejected at exploration time as UX-hostile (can't share a single ntfy topic across two devices on purpose); not reconsidered at ship.
- **Option 3 — declarative ntfy ACL/tokens.** Rejected at exploration time (couples Core to the ntfy server's user model + restart-on-mutation); not reconsidered.
- **Option 4 — Apprise facade.** Rejected at exploration time (credential-at-rest + per-user URL SSRF concerns); the multi-channel future is `multi_channel_notifier`, a separate chunk.
- **Option 5 — Web-Push only.** Rejected at exploration time (does not answer audit B5; gated on `cross_device_lumogis_web`); not reconsidered.
- **Not chosen at ship time:** keeping `Notifier.notify`'s signature unchanged and resolving `user_id` from a `ContextVar`. Rejected because both call sites already had the `user_id` in hand (signal owner / digest enumerator) and a Protocol-level keyword-only required parameter forces every implementation to be updated in the same patch — type-safety beats `ContextVar` magic for a two-call-site surface.

## Consequences

- **Easier:** Each subsequent connector rollout (CalDAV — ADR 021; LLM provider keys — pending) follows the same recipe: register an id in `connectors.registry.CONNECTORS`, add a thin `services/<connector>_runtime.py` (or `_credentials.py`) wrapping `services.connector_credentials.get_payload`, route domain errors to graceful skips at the consumer layer. `services/ntfy_runtime.py` is the canonical small example; `services/caldav_credentials.py` is the canonical larger example with payload validation.
- **Easier:** Operators see one secret surface (`user_connector_credentials`), one rotation script (`orchestrator/scripts/rotate_credential_key.py`), one backup-export omission rule, one boot-time refusal — each ntfy/CalDAV/LLM rollout inherits all of it for free.
- **Harder:** Adding a household-broadcast notification path now needs an explicit sentinel `user_id` (e.g. `"__system__"` matching ADR 015 scope vocabulary) plus a household-default credential row or env var; the Protocol's keyword-only required `user_id` deliberately forbids "broadcast to anyone" defaults.
- **Harder:** `signal_digest` no longer collapses into one notification per tick. A 10-user household with active signals receives 10 notifications. `SIGNAL_DIGEST_COUNT` semantics changed from "max signals per digest" to "max signals per user per digest". Operators upgrading must understand the change.
- **Future chunks must know:** New ntfy payload fields (additional auth schemes, custom headers, topic-routing hints) are additive on the `payload` dict; the resolver tolerates unknown keys for forward-compat. The wire shape `{url?, topic, token?}` is the locked minimum. Do NOT introduce a parallel `user_notifier_prefs` table — extend the credential payload instead.
- **Future chunks must know:** Closing audit row B5 in `docs/private/MULTI-USER-AUDIT-RESPONSE.md` requires a one-line ✅ flip pointing at this ADR. Not done in this retro to keep the audit-response sweep batched.

## Revisit conditions

- If a second household-batch job (`services/routines.weekly_review`, etc.) needs per-user fanout, factor digest's "enumerate distinct active `user_id`" SQL into a shared helper rather than duplicating it. This is the natural moment to open audit B7.
- If a second notification channel ships (Web Push, Matrix, Apprise), revisit whether `NOTIFIER_BACKEND` should remain a deployment switch or become a per-user choice. The current contract assumes one channel per deployment.
- If household-broadcast notifications become a product requirement, define the `user_id="__system__"` sentinel resolution path (most likely: a credential row keyed on the sentinel, falling back to a `NTFY_HOUSEHOLD_TOPIC` env var). Do NOT add `user_id: str | None` to the Protocol.
- If `NTFY_URL` ever has to vary per user (e.g. household runs two ntfy servers for redundancy), drop the `payload.url or NTFY_URL` precedence chain and require `payload.url` explicitly under `AUTH_ENABLED=true`.
- If operators want a friendlier ntfy-specific form (separate `topic` / `token` fields, "open in ntfy app" QR helper, mobile-friendly setup walk-through), build it as a connector-specialised view in `orchestrator/web/index.html` against the ADR 020 credential-management surface — not a new substrate.

## Status history

- 2026-04-21: Finalised by /record-retro (retrospective). Records as-shipped chunk D from the topic-area work that landed alongside ADRs 017–021. Supersedes the prior `per_user_notifier_targets` exploration's Option 2 design (sibling table) — the as-shipped chunk uses the ADR 018 substrate instead. Audit B5 closure in `docs/private/MULTI-USER-AUDIT-RESPONSE.md` flagged as a follow-up bookkeeping task; not done in this retro.
