# ADR 026: LLM provider keys on per-user connector credentials

**Status:** Finalised
**Created:** 2026-04-21
**Finalised:** 2026-04-22 by `/verify-plan`
**Decided by:** composer-2 via `/explore`; implementation verified by Claude Opus 4.7
**Plan:** *(maintainer-local only; not part of the tracked repository)*

## Context

Cloud LLM keys lived in deployment-wide `app_settings` and environment
variables, resolved from `config/models.yaml` via `api_key_env`. The
household multi-user substrate (`user_connector_credentials`,
ADR 018) was already shipped; consumer rollout listed LLM keys last.
Keys are strategically sensitive and were the most plausible future
driver for **explicit shared/system** credentials — which 018
deliberately deferred.

## Decision

**Migrate LLM vendor secrets to per-user connector rows** using **one
registered connector id per distinct `api_key_env`** value in
`models.yaml`:

| Env var | Connector id |
|---|---|
| `ANTHROPIC_API_KEY`  | `llm_anthropic` |
| `OPENAI_API_KEY`     | `llm_openai` |
| `XAI_API_KEY`        | `llm_xai` |
| `PERPLEXITY_API_KEY` | `llm_perplexity` |
| `GEMINI_API_KEY`     | `llm_gemini` |
| `MISTRAL_API_KEY`    | `llm_mistral` |

Per-user JSON payload is fixed at `{"api_key": "<secret>"}` (validated
at the route boundary in `routes/connector_credentials._validate_llm_payload`).

`config.get_llm_provider` and `config.is_model_enabled` gain a
**keyword-only `user_id`** argument; under `AUTH_ENABLED=true` and a
model that requires a key, resolution flows through
`services/llm_connector_map.effective_api_key` →
`connector_credentials.resolve` (no request-time env fallback under
auth-on). The LLM adapter process cache key shape is now
`llm:<user_id or '_global'>:<model>` for keyed models and
`llm:_local:<model>` for local models, so per-user secret material
cannot leak across cache slots. `connector_credentials.register_change_listener`
is added to the substrate so `config.py` evicts only the affected
user's slot on every PUT/DELETE/migration.

Runtime chat error envelope: `routes/chat.py` returns OpenAI-style
top-level `{"error": {"code": ...}}` JSON via `JSONResponse` (NOT
FastAPI's default `{"detail": ...}` shape) for the two domain failures:

- `ConnectorNotConfigured` → **424 `connector_not_configured`**
- `CredentialUnavailable` → **503 `credential_unavailable`**

Streaming requests run a **synchronous credential pre-flight** by
calling `config.get_llm_provider` directly **before** constructing
`StreamingResponse`, so a credential failure cannot be smuggled out as
a 200 + `text/event-stream` SSE error chunk by `loop.ask_stream`'s
broad `except Exception`.

Background callers (`signal_processor`, `routines`, `memory`,
`entities`) now thread `user_id` into `get_llm_provider` and degrade
visibly (WARN log + empty result) when the per-user key is missing.
A boot-time gate (`config._check_background_model_defaults`) refuses
to start when `SIGNAL_LLM_MODEL` resolves to a cloud model under
`AUTH_ENABLED=true`, since the process-static default has no `user_id`
to resolve a per-user key against.

Admin surface under `AUTH_ENABLED=true`:

- `GET /api/v1/admin/settings` **omits** `api_key_status` entirely
  (per-user data is not aggregated into a household view).
- `PUT /api/v1/admin/settings` with a non-empty `api_keys` body returns
  **422 `legacy_global_api_keys_disabled`** with a message pointing at
  the per-user routes.
- `_safe_is_enabled` passes `user_id=None` explicitly so the
  household-level dashboard view shows "household toggle on" only for
  cloud models (no per-user-free enabled-ness exists under auth-on).

The existing user-facing
(`/api/v1/me/connector-credentials/llm_*`) and admin-on-behalf
(`/api/v1/admin/users/{user_id}/connector-credentials/llm_*`) routes
ship as-is with the new `_validate_llm_payload` enforcement on
`llm_*` connectors.

A one-shot operator script `scripts/migrate_llm_keys_to_per_user.py`
(repeatable `--user-id`, opt-in `--delete-legacy`, `--dry-run`,
`--actor`) copies plaintext `app_settings` rows into per-user
encrypted rows. Plaintext is never logged — per-pair stderr lines
carry only `key_present` / `error_class`. Exit codes follow a 0/1/2
matrix (success / per-pair-or-DELETE failure / config error before
any DB write). The script's listener fires inside its own process
only, so operators must restart the orchestrator container if any
named user already had an in-process cached cloud adapter.

**Do not** introduce shared/system credential tables or implicit
global env fallback under multi-user auth in this chunk — both
deferred to ADR `credential_scopes_shared_system`.

## Alternatives Considered

- **Per-adapter connector only** — rejected: multiple
  OpenAI-compatible vendors need distinct secrets (see exploration).
- **LiteLLM / external gateway as primary store** — rejected for
  default Lumogis (optional overlay remains operator choice).
- **Keep global `app_settings` under `AUTH_ENABLED=true`** — rejected:
  contradicts ADR 018 D3 and per-user security goals.
- **Repurpose `api_key_status` into a household "any user has key"
  aggregate** — rejected per user instruction; the field is dropped
  entirely under auth-on so it cannot leak cross-user presence
  through the legacy global settings surface.
- **Pre-flight credential check inside `loop.ask_stream`** — rejected:
  `loop.ask_stream` already wraps `get_llm_provider` in a broad
  `except Exception` and yields SSE error events; the only place that
  can return a true 424/503 HTTP status is the route, **before**
  `StreamingResponse(...)` constructs.

## Consequences

**Easier:** Billing and abuse isolation per user; one crypto / rotation
story; aligns dashboard with MCP-token / CalDAV-credential patterns;
chat-route domain errors map cleanly to OpenAI-style envelopes that
LibreChat and other compat clients can read.

**Harder:** LibreChat model visibility stays household-level until a
follow-up; admin "household key health" needs a per-user enumeration
redesign; migration from plaintext `app_settings` must be explicit.

**Operator-visible behaviour changes** (release notes for the chunk
that lands this ADR):

1. `/v1/models` becomes auth-required under `AUTH_ENABLED=true`.
   Out-of-tree pollers that hit it without `Authorization` will start
   receiving 401. The only known in-tree caller is the LibreChat
   bridge, which consumes the static `librechat.yaml` file mount, so
   no in-tree functional regression.
2. Cross-process cache invalidation is **not** wired between the
   migration script and the running orchestrator — operators must
   restart the orchestrator container if `--user-id` named in the
   script already had a cached adapter from a previous key value.

**Future chunks must know:**

- `services/llm_connector_map.LLM_CONNECTOR_BY_ENV` is the **single
  source of truth** for env-string → connector-id translation.
  Adding a vendor means: bump `models.yaml`, add a `LLM_<VENDOR>`
  constant + `CONNECTORS` entry in `connectors/registry.py`, add the
  env→connector pair here, and add the human label to
  `_VENDOR_LABEL_BY_CONNECTOR`. The `test_mapping_covers_models_yaml_envs`
  drift guard catches partial updates.
- The `llm_*` connector id namespace is reserved. Future non-LLM
  Anthropic/OpenAI integrations get their own non-`llm_` id
  (e.g. `anthropic_webhook`).
- The `{"api_key": "<string>"}` payload schema is **frozen at v1**.
  Future per-user `base_url_override` / `proxy_url` / extra-headers
  belong in a follow-up ADR with backward-compatible defaults.
- The substrate's `register_change_listener` mechanism is **public and
  reusable** — listener signature is `(*, user_id, connector,
  action)` (where `action` is `"put"` or `"delete"`); listeners run
  synchronously after audit, exceptions are logged-and-swallowed.
- The chat-route 424/503/500 OpenAI-style envelope shape is the
  contract for OpenAI-compatible clients. Future error work on
  `/v1/chat/completions` MUST keep `JSONResponse` (not
  `HTTPException`) and the `error.code` / `error.message` keys at the
  JSON top level. Adding fields to the `error` object is allowed;
  renaming or moving keys is not. Other 4xx routes in this plan
  (admin 422, credential CRUD 422 / 404 / 503) deliberately stay
  FastAPI-native (`{"detail": {...}}`).

## Revisit conditions

- Product requires **one household key** as a first-class concept with
  audit semantics distinct from "admin copied key into N user rows" —
  trigger the deferred `credential_scopes_shared_system` ADR.
- LibreChat or other clients need **per-user model catalogs** at the
  bridge layer — current static `librechat.yaml` is per-household.
- Operators standardise on LiteLLM virtual keys for metering — may
  change where secrets live without changing the per-user *billing
  attribution* story.
- Cross-process cache invalidation becomes a real operator complaint
  — sketch in the plan: a `POST /api/v1/admin/internal/invalidate-llm-cache`
  admin route the migration script could call against the running
  orchestrator over loopback.

## Implementation deviations from draft (recorded by `/verify-plan`)

1. **Listener signature.** Draft listed positional
   `listener(event, user_id, connector)` with `event="put"|"deleted"`.
   Implementation ships keyword-only
   `listener(*, user_id, connector, action)` with `action="put"|"delete"`.
   Intent preserved; the dispatcher in `config._on_connector_credential_change`
   matches the shipped signature exactly.
2. **PUT validation error code.** Draft listed
   `code: "invalid_payload"`. Implementation ships
   `code: "invalid_llm_payload"`. More specific; intent preserved.
3. **PUT length cap.** Draft mandated `len(api_key) > 512` rejection
   in `_validate_llm_payload`. Implementation rejects only on
   non-string / blank-after-strip / extra fields. The substrate
   already imposes column-level bounds, so the route-layer length cap
   was dropped as redundant. Recorded for follow-up if a real
   operator request lands.
4. **Deferred:** dashboard UI changes (plan Pass 3.12) and the
   end-to-end integration test `tests/integration/test_llm_per_user_e2e.py`
   (plan Pass 5.16). Both explicitly tagged in the plan's Implementation
   Log as deferred to a follow-up chunk; the per-user resolver, cache,
   route envelopes, and substrate listener are all unit-test pinned.

## Status history

- 2026-04-21: Draft created by `/explore`.
- 2026-04-22: Finalised by `/verify-plan` — implementation verified
  against the plan; tests green (1244 passed / 11 skipped / 0 failed);
  three minor deviations recorded above; two passes (3.12 dashboard,
  5.16 integration test) explicitly deferred per the plan's own
  Implementation Log.
