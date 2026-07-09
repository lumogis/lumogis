# ADR-130: MCP token scope selection (least-privilege at mint)

**Status:** Finalised
**Created:** 2026-06-24
**Last updated:** 2026-06-24 (amended by LUM-531 — route default now least-privilege)
**Decided by:** `/create-plan` → `/review-plan --self` (R1+R2) → `/review-plan --critique sonnet` → `/review-plan --arbitrate` → `/implement` → `/verify-plan`
**Finalised by:** /verify-plan 2026-06-24
**Linear:** [LUM-527](https://linear.app/lumogis/issue/LUM-527) (epic [LUM-284](https://linear.app/lumogis/issue/LUM-284))
**Plan:** `.cursor/plans/LUM-527-mcp-token-scope-selection.plan.md`
**Extends:** [ADR 128](128-lum-291-mcp-memory-write-surface.md) (MCP write surface + `mcp:write` scope), [ADR 017](017-mcp-token-user-map.md) (per-user MCP tokens + scopes)

## Context

ADR 128 added an optional `scopes` parameter to `mcp_tokens.mint()` and made the
`/mcp/` write tools enforce the **`mcp:write`** scope, but it flagged a **fail-open
default**: tokens minted through the product (the `POST /api/v1/me/mcp-tokens`
route and the Web UI) always passed `scopes=NULL` (unrestricted), because the
route never accepted a scope choice and there was no UI to make one. Write
protection was therefore opt-in only by writing code. LUM-527 closes that gap.

A structural fact shapes the design: **scopes are write-gating only.**
`mcp_server._require_scope("mcp:write")` is called solely by the write tools;
read tools never gate. So `["mcp:read"]` means "read, no write", `["mcp:write"]`
(or `["mcp:read","mcp:write"]`) means read+write, and `[]` is meaningless.

## Decision

Let the scope be **chosen at mint time**, with least-privilege as the default in
the surface a human touches.

1. **Validated `scopes` on the mint request.** `MintMcpTokenRequest` gains
   `scopes: list[str] | None`. A module constant `KNOWN_MCP_SCOPES =
   ("mcp:read", "mcp:write")` is the **single source of truth allowlist** (lives
   in `models/mcp_token.py` — not imported into `mcp_server`, so no cycle). The
   validator: returns `None` untouched (the **None-guard is the explicit first
   line** — Pydantic v2 runs the validator on explicit JSON `null`, so iterating
   without it would `TypeError`); rejects any element not in the allowlist
   (422 `value_error`); rejects `[]` (ambiguous "no access"); de-dupes preserving
   canonical allowlist order. `extra="forbid"` is retained.
2. **The allowlist is a security control, not cosmetics.** The store column is
   `text[]`; without server-side allowlisting a client could persist an
   arbitrary/typo'd scope string into a credential row that would then never
   match `_require_scope`. Validation blocks that.
3. **Least-privilege in the product surface; back-compat in the programmatic
   surface.** The **Web UI always sends an explicit `scopes`** (default
   `["mcp:read"]`; an opt-in radio adds `mcp:write`), so UI-minted tokens are
   never silently unrestricted. The **service `mint(scopes=None)` default and the
   model's `scopes=None` default stay unrestricted**, so existing programmatic
   and raw-API callers (and the `test_mint_inserts_scopes_as_null_not_empty_array`
   contract) are unaffected. The residual raw-API omitted-scopes fail-open is a
   deliberate, documented follow-up, not part of this chunk.
4. **Display, not just selection.** Both the Me and Admin token lists render each
   token's access ("Read-only" / "Read + write" / "Unrestricted (legacy)" for the
   `NULL` rows). Legacy tokens are shown as-is — **no backfill** (it would silently
   change access for existing integrations).
5. **Scope is added to the mint audit** (`__mcp_token__.minted` `input_summary`)
   so an operator can see what access a token was granted. No secret material is
   logged.

## Alternatives considered

- **Default the route/model to least-privilege too** (reject omitted scopes or
  treat them as read-only) — rejected for this chunk: it would change behaviour
  for existing programmatic callers that rely on the omitted-scopes unrestricted
  path. Tracked as a follow-up to tighten once no caller depends on it.
- **A free-text scope field** — rejected: invites typos/forgery; the allowlist +
  a binary UI control is the safe shape (the only enforced distinction is "can
  write?").
- **Enforcing `mcp:read` as a deny-read gate / treating `[]` as no-access** —
  rejected: read tools are intentionally ungated, so `[]` is meaningless; the
  validator rejects it rather than minting a confusing token.
- **Backfilling legacy `NULL` tokens** — rejected (silent access change);
  surfaced as "Unrestricted (legacy)" instead.
- **Adding an admin mint-on-behalf route** — out of scope (D12 keeps admins to
  list/revoke). The Web admin view's pre-existing Mint button currently 405s (no
  backend route); removing/wiring it is a follow-up.

## Consequences

- **Positive:** UI-minted tokens are read-only by default — the fail-open default
  is closed where humans mint. The allowlist hardens the credential row. No
  migration (the column predates this). Back-compat fully preserved for
  programmatic callers.
- **Negative / watch:** a direct (non-UI) API call that omitted `scopes`
  originally still minted unrestricted — that residue is **closed by LUM-531**
  (the route now defaults omitted/null `scopes` to read-only `["mcp:read"]` and
  never mints `NULL`). The Web admin Mint button was a pre-existing 405 (dead
  control) — **removed by LUM-530**. *(Originally tracked as follow-ups; both
  resolved.)* Remaining watch item: `KNOWN_MCP_SCOPES` and the UI selector must
  evolve together if a new scope is ever added.

## Revisit conditions

- ~~A decision to default the **route/model** (not just the UI) to least-privilege,
  closing the raw-API omitted-scopes residue.~~ **Resolved by LUM-531** (2026-06-24)
  — the mint route now defaults omitted/null `scopes` to read-only `["mcp:read"]`
  and never mints a `NULL` token; see status history.
- A new MCP scope is introduced (extend the allowlist + selector together).
- True read-scope enforcement (`_require_scope("mcp:read")` on read tools) is
  pursued — would give `["mcp:read"]`/`[]` real read-deny meaning.

## Status history

- **2026-06-24:** Finalised by /verify-plan — implementation confirmed the
  decision. Route + model validator + Web selector shipped; scope enforcement
  proven end-to-end over `/mcp/` with a real minted `lmcp_` token (read-scoped
  denied / write-scoped allowed). Self-review (R1+R2) + Sonnet critique R1 +
  arbitration closed all P1/P2 before implementation; adversarial code+security
  review at verify found no critical/high issues.
- **2026-06-24 (LUM-531):** First "Revisit condition" **resolved** — the mint
  **route** now defaults omitted/`null` `scopes` to least-privilege read-only
  (`["mcp:read"]`) and never mints a `NULL` (unrestricted) token, closing the
  raw-API fail-open residue end-to-end. The **service** `mint(scopes=None)` seam
  (`NULL` = unrestricted) is intentionally retained for internal callers; the
  model's parse behaviour is unchanged (the route interprets `None`). A deliberate
  breaking change for raw-API callers that omitted `scopes` expecting write. See
  the LUM-531 plan + `### Security` CHANGELOG entry.
