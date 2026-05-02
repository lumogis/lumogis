# ADR: First-party connector credential management UX

**Status:** Finalised  
**Finalised copy:** `docs/decisions/020-credential-management-ux.md`  
**Created:** 2026-04-21  
**Last updated:** 2026-04-21 (verify-plan — implementation confirmed decision)  
**Decided by:** /explore (credential_management_ux), arbitrated against critique-round-1-composer2, verified by /verify-plan

## Context

Per-user connector credential **storage and CRUD APIs** are implemented (`user_connector_credentials`, ADR 018). Real operators still lack a **first-party web experience** for listing metadata, replacing encrypted payloads, and deleting rows — including admin actions on behalf of another user. The product must remain **safe-by-default**: no plaintext reshow, no new secret-exfiltration vectors, no in-browser household key rotation that could mis-lead operators.

## Decision

Ship a **minimal Lumogis Web UI** (same-origin, Bearer session + existing CSRF rules for cookie flows) that exercises **only** the existing CRUD endpoints for connector credentials; present **metadata** (`connector`, timestamps, `created_by`/`updated_by`, `key_version`) and treat credential JSON as **write-only blind replacement** (textarea form with identical PUT semantics — replace-only, no merge). Add two small **read-only** HTTP helpers — both spec'd in the locked plan and wire-frozen for downstream chunks:

1. **`GET /api/v1/me/connector-credentials/registry`** — authenticated (admin or user); returns `{"items": [{"id": "<connector>", "description": "<text>"}, …]}` ordered by `id`. Powers the modal's connector dropdown and (separately) the UI's stale-row detection. Object-shape was chosen over bare strings so the wire can grow new fields (`doc_url`, `category`, …) without a breaking change.
2. **`GET /api/v1/admin/diagnostics/credential-key-fingerprint`** — admin-only; returns `{"current_key_version": <int>, "rows_by_key_version": {"<int-as-string>": <count>, …}}`. The structured shape (current + per-`key_version` count) was chosen over a bare fingerprint so operators can directly see whether rotation is still incomplete (rows still sealed by an older key). **Counts include rows whose `connector` is not in the registry** — diagnostics is registry-blind by design. The endpoint never returns ciphertext, plaintext, or key bytes.

The fingerprint endpoint lives in a **new** `orchestrator/routes/admin_diagnostics.py` (router prefix `/api/v1/admin/diagnostics`, `Depends(require_admin)` at the router level), establishing the natural home for future read-only operator diagnostics. The registry endpoint stays in `orchestrator/routes/connector_credentials.py` and is registered **before** the existing `/{connector}` route so `/registry` is not parsed as a connector id.

**Additive evolution rule (frozen).** Future chunks may add new top-level keys to either response, but MUST NOT change the type or name of `current_key_version`, `rows_by_key_version`, or `items`, or the inner key encoding (string-of-int) inside `rows_by_key_version`.

## Alternatives Considered

- **Secrets console / Vault-class product** — rejected: new operational surface and deps; violates “minimal” and local-first simplicity for v1 (`see *(maintainer-local only; not part of the tracked repository)*`).
- **HTTP-triggered ciphertext rotation** — rejected here: contradicts shipped operator runbook + ADR posture; remains script + restart.
- **Reveal/mask endpoints** — rejected: expands attack surface without user benefit given replace-only UX.

## Consequences

**Easier:** Family-LAN users and admins can manage credentials without curl; support burden drops as real connectors (`ntfy`, CalDAV, …) migrate off env.

**Harder:** UI copy must constantly reinforce “cannot show stored secret”; risk of users pasting secrets into screenshots — mitigate with clear warnings only (no new data model).

**Future chunks must know:** Any **payload schema** validation belongs with each connector consumer, not necessarily the credential store UI — avoid coupling generic UI to one connector’s JSON shape unless explicitly documented.

## Revisit conditions

- First connector ships a **machine-readable schema** small enough to warrant a guided form instead of raw JSON.
- Operators request **audited** admin-only migration of credentials between household keys beyond existing rotation script (would be a different ADR).

## Status history

- 2026-04-21: Draft created by /explore (`credential_management_ux`).
- 2026-04-21: Draft revised by /review-plan --arbitrate (round 1) — locked the structured fingerprint response shape, the registry response shape, the new `routes/admin_diagnostics.py` placement, and the additive-evolution rule in the Decision section so the ADR no longer trails the plan.
- 2026-04-21: Finalised by /verify-plan — implementation confirmed decision. Both endpoints ship with the locked wire shapes; `routes/admin_diagnostics.py` is registered in `main.py`; the registry endpoint sits in `routes/connector_credentials.py` ahead of the `/{connector}` route; `services.connector_credentials.count_rows_by_key_version()` is the single GROUP BY backing the diagnostic; the UI tile + modal in `orchestrator/web/index.html` carries every D21–D25 hardening (force-close on admin picker switch, target-aware delete confirm, registry-failure explainer, focus management + ARIA + Escape-polyfill parity, registry cache invalidation on `unknown_connector`). Full orchestrator suite green (1035 passed / 6 skipped). Finalised copy at `docs/decisions/020-credential-management-ux.md`.
