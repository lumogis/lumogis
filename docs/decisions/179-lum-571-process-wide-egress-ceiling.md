# ADR-179: Process-wide egress ceiling — locked in-process cap (LUM-571)

**Status:** Finalised
**Created:** 2026-07-20
**Last updated:** 2026-11-10 (amended for LUM-637 and LUM-903)
**Decided by:** `/create-plan` + 2 plan-review rounds (Fable, Sonnet 5) + an adversarial code/security review; finalised by `/verify-plan`
**Extends:** **ADR-153** (LUM-553 in-process `tethered` allowlist) — the process-wide ceiling deferred from LUM-553 v1
**Related:** **ADR-178** (LUM-625 email-IMAP containment — the ceiling must remain a superset of its IMAP scope)

## Context

ADR-153 (LUM-553) shipped an opt-in in-process egress allowlist using `tethered` PEP-578 audit
hooks, but scoped it to a **single door**: a per-call `tethered.scope()` around cloud-LLM adapter
calls. ADR-178 (LUM-625) added a second scoped door around the IMAP connect. Both are per-call and
cover only their own subsystem. The **process-wide ceiling** — a `tethered.activate(locked=True)`
policy covering *every* outbound socket from *any* code path — was explicitly deferred from LUM-553
v1 (arbitration R1: the bootstrap needed a lifespan seam and locked-hook test isolation that were
out of scope then). LUM-571 delivers it.

The value over the scoped doors: a whole-process cap catches accidental or injected-but-not-fully-
compromised egress from subsystems that have **no** scope of their own (signal sources, connectors,
schedulers), and on a local-only instance it excludes cloud hosts entirely — blocking cloud egress
process-wide, not only inside the LLM wrapper.

Two mechanics of `tethered==0.5.1` shaped the design (both verified empirically):
- **`activate()` and `scope()` compose by intersection** — a scope can only *narrow* the global
  policy. So the ceiling's allowlist **must be a superset** of every legitimate scope and every host
  any subsystem dials, or that egress is blocked.
- **`activate()` serialises the full allowlist into `os.environ["_TETHERED_CHILD_POLICY"]`** so
  Python children inherit the policy. The allowlist is therefore **not secret at the process
  boundary** — a fact the honesty copy states rather than contradicts.

## Decision

Install a **process-wide egress ceiling** as an **opt-in, default-off** layer, gated by an
independent `LUMOGIS_FF_EGRESS_GUARD_CEILING` flag.

**Activation.** `egress_guard.activate_ceiling()` is called once in `main.py::lifespan`,
**immediately after the store health-check loop** and before the rest of startup, so the whole
running process (embedding, plugins, signal monitors, schedulers, connectors) runs under the ceiling.
It calls `tethered.activate(allow=sorted(build_allowlist(user_id=None, for_ceiling=True)),
allow_localhost=True, locked=True, lock_token=<module object()>, ~~fail_closed=False~~,
external_subprocess_policy="warn")`.

**Amended 2026-11-10 (LUM-637): the ceiling passes `fail_closed=True`.** The struck-through
`fail_closed=False` above is the v1 decision; it is kept for the record. In `tethered==0.5.1` the
`fail_closed` branch catches exceptions from one call only, the allowlist check itself, so it fires
only on a `tethered` bug. DNS failures, malformed hosts and IPv6 forms never reach it. Failing open
there would silently switch the ceiling off, with one WARNING line per connection as the only
signal; failing closed blocks the connection with the usual `EgressBlocked` and an ERROR log. For an
opt-in layer whose only job is to cap egress, a loud outage beats a silent bypass, the same call the
import-failure rule below already makes. Evidence (the LUM-637 assessment): 840,966 fuzzed policy
checks raised 0 exceptions, and the 405 egress-related tests passed unchanged with `fail_closed=True`
forced on every `activate()` and `scope()`. The `fc_policy_error` / `fo_policy_error` subprocess
tests pin the branch on the real locked hook. **Escape hatch:** turn
`LUMOGIS_FF_EGRESS_GUARD_CEILING` off and restart Core (the same restart every ceiling change
already needs); no separate fail-open switch was added. **The `scope()` doors are unchanged:** the
LLM, IMAP, SMTP and OAuth scopes keep the library default (fail open), as ADR-153 and ADR-178 chose;
since every scope runs inside the ceiling, a `tethered` bug is now caught at the ceiling whenever it
is on.

**Superset allowlist.** `build_allowlist(for_ceiling=True)` unions the existing LLM/backend hosts
with `_ceiling_extra_hosts()`: the household IMAP **mail** hosts **and** the configured IMAP
**proxy** host (unconditionally — the LUM-625 scope target is the proxy under Layer 2, and
proxy-ineligible hosts take the direct path even when a proxy is set), plus the infra hosts
`CAPABILITY_SERVICE_URLS`, `STACK_CONTROL_URL`, `STT_SIDECAR_URL`, and single-user
`CALENDAR_CALDAV_URL`. Cloud hosts are included only when the **instance** policy (widest in the
household) is `ALLOW_CLOUD`, so a local-only instance blocks cloud egress process-wide.

**Locked + baked at boot.** The policy is `locked=True` (a non-interned module `object()` token, so
`deactivate()` is refused without it) and the allowlist is frozen at startup, so any allowlist-
affecting change (privacy toggle, new mailbox/model, connector host) needs a **Core restart**. If the
flag is on but `tethered` cannot be imported, `activate_ceiling()` raises `RuntimeError` and Core
**refuses to boot** (fail-closed — the operator asked for the guarantee).

**Honest posture (binding, mirrors ADR-153/178).** The ceiling is an in-process PEP-578 audit hook —
**bypassable** by C extensions / `ctypes` / raw syscalls / non-Python child processes (under
`external_subprocess_policy="warn"`) / a fully-compromised process. *Added 2026-11-10 (LUM-637):*
it is also bypassable by in-process code that passes a socket host as `bytes` rather than `str`,
which `tethered==0.5.1` does not check (the same in-process evasion class as `ctypes`; normal
clients pass `str`, so this is not a surprise-block risk). And the lock does not cover all of
`tethered`'s matching code: in-process code that patches one of its unsnapshotted helpers can widen
the locked policy without tripping the tamper guard, so the lock remains casual-mistake protection,
not attacker protection. `fail_closed=True` does not change either; it defends against `tethered`
regressions, not against in-process code. *Updated 2026-11-10 (LUM-903):* both of these
routes are closed by the upgrade to `tethered==1.1.1`, which checks `bytes`/`bytearray` hosts
(treating unreadable ones as not allowed) and pins all of its policy helpers under the lock. It is **not** an OS-level
firewall and is never described as "impossible egress"; it blocks Python-socket-layer egress with
those caveats. It is **opt-in strict mode**: deployments using web-search / arbitrary-feed
connectors, web-push, multi-user CalDAV, an HF-hub cold-cache model download, or the update check
must add those hosts to `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST` or leave the ceiling off (these are
named casualties, not silent breaks). Activation logs the host **count only** (never the list in our
logs); the `_TETHERED_CHILD_POLICY` env-boundary exposure is documented, not denied.

## Alternatives Considered

- **Dynamic/live reload of the ceiling** (re-`activate()` on privacy/mailbox change). Rejected for
  v1: it fights the locked, tamper-resistant design and needs a control plane — deferred follow-up.
- **Auto-folding every per-user connector host** (web-push endpoints, per-user CalDAV). Rejected for
  v1: needs new per-store enumerators and widens what sits in the leaky `_TETHERED_CHILD_POLICY`
  env; named as casualties with a follow-up instead.
- ~~**`fail_closed=True`** for the ceiling. Deferred: v1 matches the shipped `scope()` posture (the
  flag only affects behaviour on unexpected internal policy-check errors); a strict-fail evaluation
  needs a surprise-block risk assessment.~~ **Adopted 2026-11-10 (LUM-637).** The surprise-block
  risk assessment ran and found no input that reaches the branch outside a `tethered` bug — see the
  amendment under § Decision.
- **Host-level nftables per-port egress policy.** The only route to a native hard wall, but not
  hostname-aware and needs `NET_ADMIN` (rejected as primary in ADR-173); remains a deferred option.

## Consequences

**Easier:** whole-process egress containment (not just the LLM/IMAP doors); a real process-wide
cloud block on local-only instances; reuse of the ADR-153 `tethered` mechanism and the ADR-178
household host union.

**Harder / committed:** the ceiling is **opt-in strict mode** — some features are named casualties
unless allowlisted; the allowlist is **baked at boot** (restart to change); the superset must track
every new legitimate egress path (a future scoped door must ensure its hosts are in the ceiling); the
allowlist is not secret at the process boundary (`_TETHERED_CHILD_POLICY`).

**Future chunks must know:** `egress_guard.activate_ceiling` / `_ceiling_extra_hosts` /
`build_allowlist(for_ceiling=True)` are the contract; the ceiling must remain a **superset** of every
scope and every dialed host; do **not** reduce the IMAP fold to a single `imap_egress_target_host()`
call (it omits `_proxy_eligible`, so the unconditional mail+proxy double-fold is what keeps the
proxy-ineligible direct path safe); never activate the locked ceiling in-process in the shared test
suite (subprocess isolation only).

## Revisit conditions

- **A dynamic reload control plane** — if restart-to-change proves too manual at scale.
- ~~**`fail_closed=True`** — if a strict ceiling should hard-fail on internal policy-check errors.~~
  **Discharged 2026-11-10 (LUM-637)** — adopted; see § Decision.
- **Per-user connector egress in strict mode** — fold web-push / per-user CalDAV hosts once
  enumerators exist (weigh the `_TETHERED_CHILD_POLICY` exposure).
- **A native (non-Docker) hard-containment path** — the ceiling is in-process on every persona but
  bypassable; a host-firewall story would be a different layer.
- **ECH (encrypted ClientHello) mainstream** — does not affect the ceiling directly (it keys on
  hostnames pre-resolution), but is the ADR-173/178 revisit for the Layer-2 SNI path it composes with.

## Status history
- 2026-07-20: Plan authored (`/create-plan LUM-571`); 2 review rounds — Fable R1 found a P0 (ceiling
  ∩ LUM-625 Layer-2 IMAP-proxy scope = ∅ → all email blocked) + 4 P1s; Sonnet R2 confirmed the core
  design and caught 3 more P1s (the `imap_egress_target_host` oracle mismatch, a false "CalDAV
  auto-folded" claim, and a deterministic test-order bug). All accepted; plan promoted `ready`.
- 2026-07-20: Implemented on `claude/devtools-memory-skills-review-85zmn1` (`f3addef`); adversarial
  code/security review re-verified the P0/P1 fixes against the real `tethered==0.5.1` source — no
  findings above P3.
- 2026-07-20: **Finalised by /verify-plan** — 79 targeted tests green (23 new: 18 unit + 5
  subprocess-isolation), ruff clean. Full-lifespan boot-with-ceiling proof is CI/before-public-release
  (no image pulls in the dev sandbox).
- 2026-11-10: Amended (LUM-637) — the ceiling now passes `fail_closed=True` (Decision, Alternatives
  and Revisit conditions annotated; v1 text kept struck through). The honest-posture list gains two
  in-process routes the flip does not close: `bytes` socket hosts skip `tethered`'s checks, and
  locked mode does not snapshot all of `tethered`'s matching helpers. Evidence: the LUM-637
  assessment. Full-lifespan boot-with-ceiling proof is still open.
- 2026-11-10: Amended (LUM-903) — `tethered` upgraded from 0.5.1 to 1.1.1, which closes the two
  in-process routes recorded under LUM-637 (`bytes` hosts; unsnapshotted helpers). `tethered>=1.0`
  refuses `activate()` once uvloop is loaded (libuv sockets raise no audit events), so Core now runs
  uvicorn with `--loop asyncio` (Dockerfile CMD, the dev overlay and every other launch path), enforced by
  `test_egress_ceiling.py`. Mentions of `tethered==0.5.1` above describe the version this ADR was
  written against.
- 2026-11-11: Full-lifespan boot-with-ceiling proof done (LUM-910), on images built from `dev`
  `655eacbb8` (`tethered==1.1.1`, `--loop asyncio`). With `LUMOGIS_FF_EGRESS_GUARD_CEILING=true`,
  Core logged `tethered: activated with 8 rules` and reached `Application startup complete`. Nothing
  was blocked at boot, and `/healthz` and `/health` returned 200. The loop was asyncio, and uvloop was
  not loaded. Inside the running app, Postgres, Qdrant and FalkorDB connected, and an off-list host
  raised `EgressBlocked`. The same host was reachable from a process without the ceiling.
  `make egress-guard-test` passed (6), and `make caddy-keepalive-check` passed after its stub was made
  to catch the race under asyncio. Not yet covered: graph `service` mode and an Ollama-backed boot.
