# Security audit #2 (2026-11) — Lumogis Core and Lumogis Web, public summary

**Audit type:** Internal read-only code review (not an external penetration test).

**Audit date:** 2026-11-06. **Summary published:** 2026-11-10.

**Tracking issue:** LUM-883 (umbrella), remediation in LUM-884, LUM-885, LUM-886 and LUM-887.

**Previous audit:** [Pre-launch security audit (2026-05)](pre-launch-audit-2026.md). Its overdue `revisit-by` rows (SAST-001, CSRF-001, MCP-001) were revisited in this audit; the outcomes are recorded in that file's master table.

This page is a summary. It gives the scope, the method, the severity counts and the findings that are fixed. Findings that are still open are not described here: each one is added to the fixed table when its fix ships. Reports of new issues follow [`SECURITY.md`](../../SECURITY.md).

## Scope

| In scope | Out of scope |
| --- | --- |
| Lumogis Core (`orchestrator/`): authentication, authorisation, CSRF and rate-limit primitives, routes and services, the MCP server, the Docker entrypoint and compose files | Knowledge-graph service internals, beyond tracing one in-process graph tool |
| Lumogis Web (`clients/lumogis-web/`): SPA source, PWA and service worker, build, nginx, the Caddy front door | The LibreChat profile |
| Dependency and static-analysis tooling (pip-audit, npm audit, Bandit, a secret-pattern scan) | External penetration testing, a live DAST run (the ZAP baseline needs a running RC stack) |

## Method

- Read-only review of the `dev` branch on 2026-11-06, following the hybrid methodology of [ADR 060](../decisions/060-lum-190-pre-launch-security-audit-methodology.md). No exploits were run and no live stack was used.
- The code was split into five areas (identity and admin; email, ingest and outbound requests; AI, MCP and tool boundary; infrastructure and auth plumbing; Web). An AI-assisted reviewer covered each area and traced every finding from route to sink. The lead reviewer re-read every High finding and most Medium findings, ran the tooling, and reviewed compose, Dockerfiles, the entrypoint, the Caddyfile, nginx and the auth primitives directly.
- Two reviewers found the ingest-path and chat-file-tool findings independently, which raises confidence in those.
- No fixes were applied during the audit. Each finding was then given a tracked outcome.

**Severity scale.** **Critical:** unauthenticated remote compromise, or cross-user data access by design. **High:** cross-user data access or privilege escalation that needs an account. **Medium:** depends on misconfiguration or a shared device, or a defence-in-depth gap with real impact. **Low:** hardening. **Info:** documentation or hygiene.

## Tooling results

| Check | Result |
| --- | --- |
| Python dependencies (pip-audit, every requirements file) | 0 known vulnerabilities |
| Web production dependencies (`npm audit --omit=dev`) | 0 |
| Web all dependencies (`npm audit` with the repository allow-list) | 4 low, all one allow-listed development-only chain with no fixed release; the gate passes |
| Bandit (Core, medium severity and confidence) | 13 hits, 0 true positives (constant SQL fragments with parameterised values, an allow-list constant, a deliberate Unicode blocklist) |
| Secret-pattern scan over tracked files | 1 hit, a deliberate test fixture |

## Summary

| Area | Critical | High | Medium | Low | Info |
| --- | --- | --- | --- | --- | --- |
| Core — identity, sessions, admin | 0 | 2 | 4 | 2 | 2 |
| Core — email, ingest, outbound requests | 0 | 0 | 1 | 3 | 0 |
| Core — AI, MCP, tool boundary | 0 | 0 | 2 | 2 | 1 |
| Core — infrastructure and auth plumbing | 0 | 0 | 2 | 2 | 1 |
| Web | 0 | 0 | 1 | 2 | 3 |
| Dependencies and static analysis | 0 | 0 | 0 | 0 | 1 |
| **Total** | **0** | **2** | **10** | **11** | **8** |

When the stack runs as documented with sign-in on (`AUTH_ENABLED=true`, behind Caddy), no finding gave an unauthenticated remote attacker another member's data. Most findings were gaps between household members (ownership of ingested files and chat file tools), account-lifecycle gaps (credentials that outlived a deletion, demotion, password change or sign-out), or deployment defaults.

**Status on 2026-11-10:** every Medium finding and one of the two High findings are fixed and listed below. The remaining findings are tracked and will be listed here as they are fixed.

**Recommendation for operators:** run with sign-in on (`AUTH_ENABLED=true`) whenever Lumogis can be reached from anything other than the machine it runs on, and keep to the documented Caddy front door. See [Turn on sign-in](../deployment/quickstart.md#turn-on-sign-in).

## Findings — fixed

Fixes are merged, listed under `[Unreleased]`, and ship in the first release after 0.11.0. The [`CHANGELOG.md`](../../CHANGELOG.md) `[Unreleased]` → **Security** section describes the operator impact of each fix.

| ID | Severity | Finding | Fix |
| --- | --- | --- | --- |
| ID-005 | High | Folder ingest accepted any path from any household member, so a member could index folders they should not see (including other members' uploads) into their own search scope. | Folder ingest is an admin-only rescan of the configured library folders, re-checked by the background job. [ADR 230](../decisions/230-lum-884-ingest-and-chat-tool-ownership.md) |
| ID-004 | Medium | Directory browsing was not admin-only, so any member could list folder names. | Admin-only. [ADR 230](../decisions/230-lum-884-ingest-and-chat-tool-ownership.md) |
| DATA-001 | Medium | The chat file-read tool did not check which member owns a file. | A file is readable only when it is indexed and visible to the caller, the same rule as search. [ADR 230](../decisions/230-lum-884-ingest-and-chat-tool-ownership.md) |
| AI-001 | Medium | Search's filename fallback was not filtered by user, so it revealed file names. | The fallback uses the file index with the same visibility rule. [ADR 230](../decisions/230-lum-884-ingest-and-chat-tool-ownership.md) |
| AI-002 | Medium | In the in-process graph mode, a graph tool could take the user id from the model's tool arguments. | Every tool handler takes the caller's id from Core; the one-argument fallback is removed. [ADR 230](../decisions/230-lum-884-ingest-and-chat-tool-ownership.md) |
| ID-001 | Medium | Deleting a user left their MCP tokens valid. | Account-state changes revoke every credential in one step, and MCP tokens are checked against their owner at use. [ADR 243](../decisions/243-lum-885-account-state-token-cascade.md) |
| ID-002 | Medium | Demoting an admin did not invalidate their current access token, which was enough to re-promote themselves. | As ID-001. [ADR 243](../decisions/243-lum-885-account-state-token-cascade.md) |
| ID-006 | Medium | A password change and "sign out everywhere" left MCP tokens valid. | As ID-001. [ADR 243](../decisions/243-lum-885-account-state-token-cascade.md) |
| AUTH-002 | Medium | When no separate refresh secret was configured, access and refresh tokens shared a signing secret and carried no type, so one could stand in for the other. | Tokens carry a type checked by each verifier; a distinct refresh secret is required when sign-in is on. [ADR 243](../decisions/243-lum-885-account-state-token-cascade.md) |
| WEB-001 | Medium | On a shared browser, chat threads, drafts and the offline capture queue survived sign-out and could appear for, or be synced by, the next member. | Sign-out and member switch clear per-member client state; offline captures are owned by the member who saved them. [ADR 242](../decisions/242-lum-886-web-signout-client-state.md) |
| WEB-002 | Low | The in-memory query cache was not cleared at sign-out. | Cleared. [ADR 242](../decisions/242-lum-886-web-signout-client-state.md) |
| WEB-003 | Low | A browser push subscription survived sign-out. | Removed at sign-out. [ADR 242](../decisions/242-lum-886-web-signout-client-state.md) |
| INFRA-001 | Medium | Compose published Core's port on every host interface, a second front door without Caddy's security headers. | Published on `127.0.0.1` only. [ADR 246](../decisions/246-lum-887-deployment-hardening.md) |
| ID-007 | Low | The default trusted-proxy list covered every private range, so a LAN host could choose its own rate-limit IP. | Core trusts only the Caddy container, and Caddy resolves the client IP. [ADR 246](../decisions/246-lum-887-deployment-hardening.md) |
| INFRA-003 | Low | The Core and web containers ran as root. | Both run as unprivileged users with `no-new-privileges`. [ADR 246](../decisions/246-lum-887-deployment-hardening.md) |
| INFRA-002 | Low | The stack-control restart token was compared in non-constant time. | Constant-time comparison. [ADR 246](../decisions/246-lum-887-deployment-hardening.md) |
| ID-008 | Low | A token refresh already in progress when a password change, deletion, demotion or "sign out everywhere" landed could leave one session alive. | The refresh now re-checks the account and the session inside its transaction and refuses if either changed. [ADR 252](../decisions/252-lum-896-refresh-rotation-revoke-race.md) |

## What held

The audit re-checked the earlier findings and the main controls and found no regression of AUTH-001, API-001, CRED-001, ISO-001, ISO-002 or DEPCVE-001 from the [pre-launch audit](pre-launch-audit-2026.md). Highlights:

- **Identity:** generic login errors with constant-time checks, a dual per-IP and per-email limiter, HttpOnly SameSite=Strict refresh cookies with reuse detection, single-use hashed password-reset and invite tokens, reset links built only from the configured public origin, and owner or admin binding on every account and session route.
- **Data:** every dynamic SQL statement is built from constants, allow-lists or placeholders; mail headers reject CR/LF; email bodies are returned as text, with inline images sniffed and capped; uploads and attachments have size caps, owner checks and contained paths; feed redirects are re-validated at every hop.
- **AI and MCP:** every MCP tool resolves the user from the request, never from an argument; write tools need the write scope; email send and other side effects run only as owner-approved proposals; credentials are encrypted at rest and never returned by read routes.
- **Web:** no raw-HTML rendering anywhere in the SPA, access tokens kept in memory only, no open redirects, a service worker that caches hashed assets only, and admin gating enforced by the server.

## Related

- [ADR 060](../decisions/060-lum-190-pre-launch-security-audit-methodology.md): audit methodology
- [ADR 132](../decisions/132-lum-296-mcp-origin-dns-rebinding-guard-shipped.md): MCP Origin and DNS-rebinding guard
- ADRs [230](../decisions/230-lum-884-ingest-and-chat-tool-ownership.md), [242](../decisions/242-lum-886-web-signout-client-state.md), [243](../decisions/243-lum-885-account-state-token-cascade.md) and [246](../decisions/246-lum-887-deployment-hardening.md): the fixes from this audit
