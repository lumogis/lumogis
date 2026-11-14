# ADR-244: With auth off, model availability reads the credential tiers first (LUM-889)
**Status:** Finalised
**Created:** 2026-11-08
**Last updated:** 2026-11-08
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-08 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/auth_off_web_llm_key_retro.md`
**Draft mirror:** `.cursor/adrs/auth_off_web_llm_key.md`
**Linear:** LUM-889

## Context

With `AUTH_ENABLED=false` (the default install), Lumogis Web saves an LLM key with `PUT /api/v1/me/connector-credentials/llm_<vendor>`. That writes `user_connector_credentials` for the single auth-off user, `"default"`. The chat-time resolver, `llm_connector_map.effective_api_key`, already walked user → household → system → env for that user, so the key could be used. `config.is_model_enabled` did not walk the tiers, though. Under auth off it read only `app_settings[api_key_env]` and the process environment. The chat gate in `routes/api_v1/chat.py` therefore answered `400 invalid_model`, and `GET /api/v1/models` reported `no_credentials`.

LUM-24 had fixed the same split for auth-on installs. The auth-off branch was never brought in line. LUM-881 (ADR 227) made it more visible: an admin who saved a key and allowed cloud models was shown an accurate-looking `no_credentials` reason. The gap was found during the LUM-881 exploration.

## Decision

Under auth off, a caller with a user id gets the same tier check as auth on. If no tier holds a key, the legacy path is consulted as before.

- `_model_availability_reason` (the one copy of the key and optional-toggle rules behind both `is_model_enabled` and `model_disabled_reason`) calls a new helper, `config._tier_key_present`, in both auth modes. It uses the `_credentials_present` snapshot hint when given, else `has_credential`. Under auth off, a hit returns "available". A miss falls through to `app_settings` then the env, unchanged.
- A caller with no user id under auth off (the admin dashboard's `_safe_is_enabled`, the LibreChat config, `admin_ollama`, the legacy `/v1/models`) still reads only the legacy path. That matches `effective_api_key(None, …)`, which skips the per-user tier.
- `GET /api/v1/models` now passes `get_user_credentials_snapshot(user_id)` in both auth modes, so auth off keeps the LUM-24 cost contract: a fixed number of queries per request, not one per model.
- The household `optional_<model>` toggle is still checked first, and a switched-off user row still reads `no_credentials` when no env or `app_settings` key exists (LUM-850).
- No restart is needed: both checks read the credential rows on every call.

## Alternatives considered

- **Mirror the resolver exactly under auth off: tier walk, then env, dropping `app_settings`.** Not chosen: `app_settings` keys are still written by the auth-off dashboard (`PUT /settings`), and dropping them would switch existing installs' models off. That path has its own gap (see Consequences).
- **Treat `user_id=None` as `"default"` under auth off.** Not chosen: `get_llm_provider(…, user_id=None)` resolves without the per-user tier, so availability would claim a key the resolver then refuses.
- **Let a disabled user row block the env and `app_settings` fallback too, as the resolver does.** Not chosen in this change, because it changes behaviour for existing env-key installs beyond the issue. Recorded as an in-code note and a Later item.

## Consequences

- With auth off, a key saved in Lumogis Web turns its model on in the picker and at the chat gate as soon as it is saved. Household and system keys count the same way.
- `is_model_enabled` and `model_disabled_reason` still share one rule. The LUM-881 characterization table passes unchanged.
- Three auth-off gaps remain. All existed before this change, and all are P2:
  - A switched-off web row plus an env key still reads enabled, while the resolver refuses it.
  - A key held only in `app_settings` reads enabled, but `effective_api_key` never reads `app_settings`, so chat fails with `503 llm_provider_unavailable`.
  - Callers with no user id do not see a household or system key, although `effective_api_key(None, …)` uses one.

## Revisit conditions

- The auth-off dashboard stops writing `app_settings` keys, or a migration moves them into the tiers: drop the legacy branch and use the resolver's order.
- Auth off becomes a multi-user mode, or its synthetic user stops being `"default"`: re-check which user the web save writes and which the gate reads.
- A new availability reason (for example a breaker state) is added: it must sit in `_model_availability_reason` for both auth modes.

## Linear linkage (Product OS)

- **Existing issue:** LUM-889
- **New issue needed:** no
- **Historical evidence only:** no
- **Follow-ups:** the three P2 gaps above go to the LUM-889 "Later" section (`/linear-update fold`). The disabled-row gap is also an in-code note in `orchestrator/config.py`.

## Testing retrospective

- **Added:** `orchestrator/tests/test_auth_off_web_saved_llm_key.py` (9 cases). It saves a key through the web route, then checks that the picker and the chat gate both flip with no restart. It also covers a household key, a switched-off row, the optional toggle, and the `is_model_enabled` / `model_disabled_reason` agreement with and without the snapshot hint. 5 of the 9 fail on the unfixed code. The other 4 are guard cases that pass either way.
- **Run:** the new file plus 14 related modules: 405 passed. The full non-integration suite: 6262 passed, 93 skipped, 3 xfailed, 6 failed. All 6 failures are in `tests/test_debug_runners.py`, fail the same way on the base commit, and pass with the default `TMPDIR` (the scripts require a log dir under `/tmp`). `make lint` passes.
- **Not run:** a live check on a running stack with auth off. The route tests cover the save → picker → gate path in one process.

## Status history

- 2026-11-08: Finalised by /record-retro (retrospective).
