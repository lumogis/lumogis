# ADR-238: Read models follow the runtime tier walk, so a disabled connector is not advertised (LUM-850)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/connector_disabled_read_models_retro.md`
**Draft mirror:** `.cursor/adrs/connector_disabled_read_models.md`
**Linear:** LUM-850

## Context

LUM-834 chunk B (ADR-219) gated every path that **uses** a credential on `enabled`, and no path that **advertises** one. Three surfaces still showed a switched-off connector as available: the ntfy channel on the notifications screen, the LLM providers screen, and `/v1/models`, which kept offering a model whose only credential was disabled until selecting it returned 424. `/v1/models` was not a one-line filter: in the runtime resolver a disabled user row stops the tier walk, so a household or system key must not bring the model back.

## Decision

Read models decide what is available with `credential_tiers.runtime_tier_from_records`, the resolver's tier order on metadata only.

- A user row returns `"user"` when enabled and `"disabled"` when not; `"disabled"` stops the walk. Otherwise household → system → env → `"none"`. `SERVING_TIERS` are the tiers that would serve a credential.
- `llm_connector_map.has_credential` and `get_user_credentials_snapshot` (behind `/v1/models`) use it. The snapshot reads the user tier through `connector_credentials.list_records` instead of raw SQL.
- `me_llm_providers` and `me_notifications` report a disabled row as `not_configured`, with the reason in `why_not_available`; `active_tier` stays `"user"`.
- A parity test runs every combination through the helper and the resolver. Changing one walk without the other fails it.

## Alternatives considered

- **`WHERE enabled` on the user-tier query.** Not chosen: a disabled row becomes an absent one and the walk continues to household/system, which is the defect.
- **Call the resolver from read models.** Not chosen: it decrypts, and read models must not.
- **A new `disabled` status value.** Not chosen now: it is a wire change (OpenAPI snapshot, web types) and the reason text already says it.

## Consequences

- Switching a connector off shows as off and hides its model, whatever household or system keys exist.
- No raw SQL over `user_connector_credentials` remains outside the credential service.
- `model_disabled_reason` still says `no_credentials` for a disabled key.

## Revisit conditions

- Per-mailbox disable ships: `email_credentials.mailbox_records` ignores `enabled` and must use the helper.
- A client needs to tell "switched off" from "never configured".
- The resolver's walk changes.

## Linear linkage (Product OS)

- **Existing issue:** LUM-850
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

31 new cases, including the 24-case parity table; 1,230 neighbouring tests pass; 10 of 10 mutations killed.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
