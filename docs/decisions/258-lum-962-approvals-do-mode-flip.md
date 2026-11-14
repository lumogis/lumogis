# ADR-258: Approvals offers DO-mode flips for exactly the connectors Ask/Do enforces (LUM-962)

**Status:** Finalised
**Created:** 2026-11-11
**Last updated:** 2026-11-11
**Decided by:** claude-opus-5-5 (/explore --headless LUM-962; revised in /review-plan arbitration R1)
**Finalised by:** /verify-plan 2026-11-11 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-962-approvals-do-mode-flip.plan.md`
**Exploration:** `.cursor/explorations/LUM-962-approvals-do-mode-flip.md`
**Draft mirror:** `.cursor/adrs/LUM-962-approvals-do-mode-flip.md`
**Relates:** [ADR 220](220-lum-849-autonomy-surface.md) (the Ask/Do connector set must be one set; this ADR applies it to Approvals)

## Context

`POST /api/v1/approvals/connector/{connector}/mode` accepts only connectors that have registered **actions**. Two other parts of the system use a wider set, the action registry plus `capability.{id}` (LUM-612):

- `PUT /api/v1/me/permissions/{connector}`
- the enforcement path, `check_permission` via `may_execute`

ADR 220 requires these sets to be the same.

A denied `capability.{id}` write is reachable on any stack with a capability service. The Approvals page shows it with "Switch to DO mode" and "Always allow this action type", and both 404. The `public_rc` approvals test seeded a denied `filesystem-mcp` row. That state cannot happen, because every in-process ToolSpec is a read and `check_permission` only denies writes. The test then flipped that connector and got the same 404. Once LUM-929 made the test fixture sign in, this turned `verify-public-rc` red and blocked the 0.12.0 cut.

## Decision

A DO-mode flip is supported for **every connector the Ask/Do gate enforces, and no other**: registered action connectors (`smtp`, `imap`) and known `capability.{id}` connectors. Read-only tool connectors (`filesystem-mcp`, `lumogis-memory`, `lumogis-graph`) stay a 404, because DO would change nothing for them.

One helper module (`services/permission_connectors.py`) defines which connectors are settable, and both `PUT /me/permissions` and `approvals.set_mode` call it. Approvals reads one registry snapshot per connector (`connector_gate`); `set_mode` and `GET /approvals/pending` reach their verdict through it, so a row offered a Switch is one `set_mode` accepts.

- **Outage policy.** Capability connectors fail closed on both routes. For plain connectors, `PUT /me/permissions` keeps its degrade-allow with `Warning: 199`, while Approvals fails closed: `set_mode` and `elevate` return 404 when the registry cannot be read (both were an unhandled 500). The connector *sets* are equal (ADR 220); the outage policy differs by route.
- **Hard limits.** Approvals refuses DO (403 `hard_limited_connector`) when **any** of a connector's gated action types is hard-limited, for both kinds: an action connector's gated types are its registered action types, a capability's are its write-tool names. `check_permission` skips hard limits under DO, so this keeps the Approvals button from un-gating a hard-limited tool. It is a guard on the Approvals path only: `PUT /me/permissions` applies no hard-limit refusal (pre-existing; LUM-914 AI-005).
- **Pending.** `GET /approvals/pending` reports a row that cannot be flipped, or would be refused, as `suggested_action: explain_only`, and reports `elevation_eligible: false` unless the row's `(connector, action_type)` pair is a registered action. Lumogis Web renders "Switch to DO mode" only when `suggested_action` is not `explain_only`.

The RC seed and test move to `smtp` / `email_send`, a denial the product really produces. The test restores ASK afterwards.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-962-approvals-do-mode-flip.md`.

- **Retarget the test only.** The gate goes green, but reachable capability rows keep two dead buttons.
- **Accept tool-only connectors in `set_mode`.** That stores a DO setting with no effect, and widens the accepted set past the enforced set.
- **Drop connector validation.** That undoes LUM-612's fail-closed capability grants.
- **Hide the button for `capability.` rows in the Web only.** That puts a server rule in the client and removes the only control a person has until LUM-851.
- **Quarantine the test.** That deletes the only live proof of the Approvals façade.

## Consequences

- Easier:
  - A denied capability write can be switched to DO from the place a person meets it.
  - "Settable connector" is defined once. LUM-851's list union, a third mode (LUM-191) or a finer key (LUM-131) extend one helper.
- No wire change: `suggested_action` and `elevation_eligible` already exist on `DeniedActionItem`. The OpenAPI snapshot is unchanged.
- No new privilege: the same user could already write the same `connector_permissions` row through `PUT /me/permissions/capability.{id}`.
- Harder: a mixed capability (one hard-limited write tool among others) cannot be switched from Approvals, and no other Web surface offers it until LUM-851. The row says "This can't be switched from here."
- DO does not satisfy a capability's required scopes (LUM-612); those still need a grant.
- The live RC gate proves only the action-connector path, because the mock capability has no write tool. The capability path is proven offline.
- Nothing should compare a connector against `list_actions()` again to decide whether it is settable.
- Not changed here: "Always allow this action type" still writes `routine_do_tracking.auto_approved`, which no enforcement path reads. That belongs to LUM-191.

## Revisit conditions

- A third Ask/Do mode, or per-action keys, lands (LUM-191, LUM-131). The helper's return type then grows beyond a boolean.
- `check_permission` starts gating reads, or a ToolSpec gains `is_write=True`. Tool-only connectors would then become settable, and the 404 rule must change.
- The RC mock capability gains a write tool. The live gate should then also flip a `capability.{id}` row.
- Capability Ask/Do moves off the `connector_permissions` table, for example onto the scope-grant model. `set_mode` would then need a second write path.

## Status history
- 2026-11-11: Draft created by /explore (LUM-962)
- 2026-11-11: Revised during /review-plan --arbitrate R1 — hard-limit refusal is "any gated type" for both connector kinds (exploration said "all", capability only); Approvals reads one registry snapshot and fails closed (404, was 500) on outage while `PUT` keeps degrade-allow; `elevation_eligible` is pair-level; the refusal is recorded as an Approvals-path guard, with the `PUT` gap tracked in LUM-914 AI-005
- 2026-11-11: Finalised by /verify-plan — implementation confirmed the decision (ADR-258). Live `scripts/integration-public-rc.sh full-cycle`: 17 passed, 0 skipped
