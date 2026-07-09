# ADR-142: Native Core household auth on loopback — LUM-473 Chunk A (as-shipped)

**Status:** Finalised

**Created:** 2026-06-29

**Last updated:** 2026-06-29

**Decided by:** as-shipped implementation (retrospective after branch verify + merge to `dev`)

**Finalised by:** /record-retro 2026-06-29 (Composer)

**Plan:** `.cursor/plans/LUM-473-native-core-auth-loopback.plan.md` (verified on feature branch; merged to `dev` at `294b069ce`)

**Exploration:** `.cursor/explorations/lum_473_native_core_auth_loopback_chunk_a_retro.md`

**Draft mirror:** `.cursor/adrs/lum_473_native_core_auth_loopback_chunk_a.md`

**Programme ADR (Chunk B still draft):** `.cursor/adrs/native-core-household-exposure.md`

**Linear:** [LUM-473](https://linear.app/lumogis/issue/LUM-473) — **In Review** (Chunk A on `dev`; Chunk B LAN/Caddy/DNS-01 open, blocked by LUM-508)

**Extends:** [ADR-112](112-lum-334-household-auth-rbac-chunk-a.md) (LUM-334 RBAC); LUM-466 appliance debundle delivery model

## Context

The Lumogis Server appliance (LUM-466 Phase 1) shipped **single-user, loopback-only**: bundled Core ran with `AUTH_ENABLED=false` and uvicorn bound to `127.0.0.1`. LUM-473 programmes **household exposure** — multi-user auth plus eventual LAN reachability with real TLS / TLS. **Chunk A** delivers the auth half **on loopback only**, opt-in, so Persona C can run authenticated multi-user Core without Mac/Windows hardware or operated DNS.

Work was planned, verified on `origin/claude/lum-473-native-core-multiuser-lan-bind`, then merged to `dev` on 2026-06-29. This ADR finalises **Chunk A only**. The programme draft ADR (`native-core-household-exposure`) remains **Draft** until Chunk B (Caddy, DNS-01, `{household}.homes.lumogis.app`) lands.

## Decision

**Ship opt-in household sharing on the native appliance that flips Core to `AUTH_ENABLED=true` on loopback, with secrets provisioning, bootstrap admin seeding, and a Core boot guard that refuses non-loopback bind without auth.**

Concrete as-built surface:

- **App (`apps/lumogis-server`):**
  - `SharingState` (`Off` / `Pending` / `On`) in `server-profile.json` — commit-after-verify: `On` only after a healthy auth boot proves admin seeding (`sharing.rs`, `supervisor.rs`).
  - On enable: mint `AUTH_SECRET`, Fernet `LUMOGIS_CREDENTIAL_KEY`, bootstrap admin email/password (password in supervisor memory only while `Pending`).
  - UI: `householdSharing.ts` + tests for first-enable flow.
  - Default-user remap target respects `INBOX_OWNER` when set (`db_default_user_remap` path via launcher env).
- **Core (`orchestrator/main.py`):**
  - `_bind_host_is_loopback()` + lifespan guard: if `LUMOGIS_BIND_HOST` is non-loopback and `AUTH_ENABLED=false`, refuse boot (dormant on default loopback/Docker; active for Chunk B).
  - Docker no-regression: unset `LUMOGIS_BIND_HOST` + auth off still boots.

**Explicit non-goals (Chunk A):** LAN bind, Caddy sidecar, DNS-01, operated household name, DDNS, relay integration. Core **stays** on `127.0.0.1`.

## Alternatives considered

- **Enable auth without commit-after-verify:** rejected — persisted `On` with failed bootstrap bricks the box (`AUTH_ENABLED=true`, empty users).
- **Bind Core to LAN directly in Chunk A:** rejected — programme ADR keeps Core loopback; Caddy terminates TLS in Chunk B.
- **Defer exposure guard to Chunk B:** rejected — guard ships dormant now so Chunk B cannot accidentally expose unauthenticated Core.

## Consequences

**Easier:**

- Persona C appliance can run LUM-334 multi-user auth locally without cloud DNS.
- Chunk B inherits mandatory-auth invariant before any LAN exposure work.

**Harder:**

- On-state owner-UUID / ingest watcher wiring is a graceful no-op residual (plan deviation).
- Rust Server build remains CI-gated (no local webkit2gtk on maintainer host).

**Future chunks must know:**

- **Chunk B** (`LUM-473-native-core-lan-exposure.plan.md`): Caddy + DNS-01 + operated zone — **blocked by LUM-508**.
- **LUM-548:** bootstrap password transport hardening.
- Programme name+cert identity is location-independent for future relay (LUM-506) — see draft programme ADR.

## Revisit conditions

- Operator Cloudflare LE staging cert proof + LUM-508 merge → unblock Chunk B.
- If On-state ingest owner resolution becomes user-visible gap → dedicated LUM child.
- If non-loopback guard needs hostname allowlist beyond loopback/private/link-local → revisit with LUM-331 Caddy pattern.

## Linear linkage (Product OS)

- **LUM-473** — recommend **`/linear-update comment LUM-473`** with merge SHA `294b069ce`, ADR-142 link, test summary; **keep In Review** until Chunk B closes programme scope.

## Testing retrospective

| Item | Detail |
| --- | --- |
| Tests added/changed | `test_auth_phase1.py` (exposure guard), `test_default_user_remap_target.py`, `householdSharing.test.ts`, Rust unit tests in `sharing.rs` |
| Commands | `.venv/bin/python -m pytest orchestrator/tests/test_auth_phase1.py orchestrator/tests/test_default_user_remap_target.py` (84 passed); `orchestrator/tests/integration/test_two_user_isolation.py` (7 passed); `apps/lumogis-server npm test` (41 passed); live `lumogis-test` stack `/healthz` on `:8000` |
| Full orchestrator | 836 passed, 1 failed (`test_check_coverage_matrix_script.py` — pre-existing matrix drift, unrelated to LUM-473), 25 skipped |
| Gaps | LUM-307 full-stack integration CI-gated on branch; Rust integration not run locally (CI gate); coverage matrix rows deferred to dev-merge |
| Follow-ups | Chunk B integration; LUM-548 transport; wire On-state owner-UUID resolution |
| Docs | Reference manual refresh deferred (plan Step 7a); matrix audit at merge time |

## Status history

- 2026-06-29: Finalised by /record-retro (Chunk A merged to `dev` at `294b069ce`).
