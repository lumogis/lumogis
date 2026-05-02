# ADR 017 — MCP Token-to-User Map (per-user MCP tokens for Lumogis Core)

**Status:** Finalised  
**Created:** 2026-04-19  
**Last updated:** 2026-04-20  
**Finalised:** 2026-04-20 by `/verify-plan` — implementation confirmed against code  
**Decided by:** composer-2 (Opus 4.7) via `/explore mcp_token_user_map`; revised during `/review-plan --arbitrate` R1 on 2026-04-20 to reconcile with plan binding decisions D2 (16-char prefix) and D6 (multi-user fail-closed for legacy `MCP_AUTH_TOKEN`).

**Draft preserved at:** *(maintainer-local only; not part of the tracked repository)* (historical record; final copy is this file.)

## Context

Audit B10 (`docs/private/MULTI-USER-AUDIT.md` §12 Phase B + §2 final paragraph; ranked #4 follow-up in `docs/private/MULTI-USER-AUDIT-RESPONSE.md` §6) identified the residual single-user collapse on the `/mcp/*` surface after the family-LAN floor shipped:

> *Before this ADR:* Phase 3.1 added per-request **JWT** Bearer wiring (`mcp_server._resolve_user_id` reads `Authorization: Bearer <jwt>` and the `sub` wins over `MCP_DEFAULT_USER_ID`). Static MCP clients used legacy shared `MCP_AUTH_TOKEN` → a single `MCP_DEFAULT_USER_ID`. **No per-user opaque token table.**

External MCP clients (Claude Desktop, Thunderbolt, `mcp-remote`, …) expect a single long-lived static bearer; they cannot mint Lumogis JWTs. Without a per-user opaque token store, every such client continued to funnel through one operator-configured `MCP_DEFAULT_USER_ID`.

Constraints shaping the option space:

- **Local-first**: no new Docker service, no cloud auth provider.
- **Modular**: ideally one new module + one migration, mirroring the `users` table pattern that family-LAN shipped.
- **Bi-state preservation**: `AUTH_ENABLED=false` single-user installs must keep working without any token table rows; the legacy `MCP_AUTH_TOKEN`+`MCP_DEFAULT_USER_ID` path must continue to function where documented.
- **MCP client compatibility**: tokens must be a single static string an MCP client can paste into its config.
- **Revocation and enumeration**: operators must list active tokens and revoke instantly.

## Decision

Introduce a Postgres table `mcp_tokens(id, user_id, token_prefix, token_hash, label, scopes, created_at, last_used_at, expires_at, revoked_at)` and an opaque prefixed bearer format `lmcp_<base32(28 random bytes)>` (50 chars total). Tokens are minted via `POST /api/v1/me/mcp-tokens` (plaintext returned **exactly once**) and stored as `sha256(token)` hex. The `/mcp/*` gate (`auth._check_mcp_bearer`) accepts any active row by **16-char `token_prefix` lookup** (plan D2) followed by constant-time `hmac.compare_digest` against the stored hash.

**`scopes`** is `TEXT[] NULL` in schema (plan D3): `NULL` = unrestricted v1 default; non-empty array = future allowlist; empty array = no access (distinct from `NULL`). v1 verification does not enforce scopes.

Legacy `MCP_AUTH_TOKEN` behaviour is **mode-conditional** (plan D6):

- In **`AUTH_ENABLED=false`**, the gate accepts the legacy shared-secret when set; missing bearer passes through only when `MCP_AUTH_TOKEN` is unset — when it **is** set, missing bearer yields **401** (`invalid mcp token`), matching the historical single-user MCP gate tests. Resolver falls back to `MCP_DEFAULT_USER_ID` for authorised requests.
- In **`AUTH_ENABLED=true`**, the gate is **fail-closed**: callers must present a Lumogis JWT or an `lmcp_…` bearer. A bare legacy `MCP_AUTH_TOKEN` match without JWT/`lmcp_…` is **401** with a once-per-process `CRITICAL` log pointing at the mint flow.

Verification happens **once per `/mcp/*` request** at the gate; `request.state` + ContextVars stash `mcp_token_id` / `mcp_user_id` so `mcp_server._resolve_user_id` does not re-call `verify()` (plan D8).

Disabling a user revokes every active `mcp_tokens` row in the **same** `MetadataStore.transaction()` as the disable flip when the adapter supports transactions; cascade audit rows emit **after** commit (plan D7/D14). `delete_user` retains revoked token rows by default for auditability.

The KG service `/mcp/*` mirror is **out of scope** (plan D1): KG keeps its legacy shared-token gate until a future Core-owned gateway chunk.

## Alternatives Considered

See *(maintainer-local only; not part of the tracked repository)*.

- **Option 2 — long-lived Lumogis JWT, no DB table.** Loses revocation, enumeration, `last_used_at`; does not satisfy audit B10’s “real per-token user table”.
- **Option 3 — per-user `MCP_AUTH_TOKEN_<USER_ID>` env vars.** Operational pain; restart to revoke.
- **Option 4 — OAuth 2.1 / DCR.** Too large for family LAN v1.
- **Option 5 — reverse-proxy header injection.** ADR-012 forbids delegating application auth to the proxy as primary.
- **Option 6 — mTLS.** Heavy operationally.
- **Option 7 — reuse refresh-token mechanism.** Conflicts with long-lived MCP tokens.

## Consequences

**Easier:** per-user MCP attribution for static-bearer clients; `last_used_at` hygiene; identifiable `lmcp_…` prefix for scanning.

**Harder:** migration + service + routes + dashboard tile; Postgres lookup on hot `/mcp/*` path (indexed; acceptable at family-LAN scale).

**Future chunks:** structured audit may add `audit_log.mcp_token_id`; KG gateway may call `services.mcp_tokens.verify()`; hosted OAuth can coexist with `mcp_tokens` as “device tokens”.

## Revisit conditions

See draft ADR *(maintainer-local only; not part of the tracked repository)* §Revisit conditions (unchanged).

## Status history

- 2026-04-19: Draft created by `/explore mcp_token_user_map`.
- 2026-04-20: Revised during `/review-plan --arbitrate` R1 — D2 prefix 16 chars; D6 mode-conditional legacy token; D8/D1/D7 callouts.
- 2026-04-20: **Finalised by `/verify-plan`** — implementation in `postgres/migrations/014-mcp-tokens.sql`, `orchestrator/services/mcp_tokens.py`, `orchestrator/auth.py`, `orchestrator/mcp_server.py`, `orchestrator/routes/mcp_tokens.py`, dashboard `orchestrator/web/index.html`, docs. Minor recorded deviations: (1) single-user missing-bearer when `MCP_AUTH_TOKEN` is set returns 401 (tightens early plan pseudocode; matches `test_mcp_endpoint_blocks_missing_token_when_token_required`); (2) inside an available transaction, `set_disabled` runs the user **disable UPDATE** before `cascade_revoke_for_user` — order differs from one plan paragraph but both steps share one transaction, preserving D7 atomicity; (3) dashboard mint dialog ships plaintext + copy affordance without a second “Claude Desktop snippet” textarea — Step 9d + README carry the client config example.
