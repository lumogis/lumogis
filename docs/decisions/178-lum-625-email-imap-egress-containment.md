# ADR-178: Email-IMAP egress containment — two honest layers (LUM-625)

**Status:** Finalised
**Created:** 2026-07-20
**Last updated:** 2026-07-20
**Decided by:** `/create-plan` + 2 review rounds (Fable, Sonnet 5) + 2 adversarial code/security reviews (Sonnet, Fable) across Pass 1/2 and Pass 3/4; finalised by `/verify-plan`
**Extends:** **ADR-173** (LUM-618 container-network egress) and **ADR-153** (LUM-553 in-process `tethered` allowlist)
**Closes:** **ADR-174** deviation #3 (email IMAP shipped with the in-process SSRF guard only)

## Context

The email connector (**ADR-174**, LUM-170) polls per-user IMAP mailboxes and does on-box
classification. It shipped with **only** an in-process SSRF guard (`_validate_imap_host`:
link-local/metadata always blocked; loopback/private gated; TLS enforced) — **ADR-174 deviation #3**
scoped LUM-618 containment *out*, because LUM-618's Squid path is HTTP(S)/SNI-oriented and IMAP hosts
are **per-user runtime data**, not install-time manifest endpoints.

That left IMAP as the one signal source without egress containment parity. The threat is the standard
one for a connector that ingests attacker-authored content (email bodies): a prompt-injection or a
compromised code path tricking Core into opening an outbound connection to an attacker host
(exfiltration) or an internal/metadata address (SSRF/DNS-rebinding). LUM-618/ADR-173 built the
containment machinery for untrusted **community capabilities**; LUM-625 extends equivalent, honestly
scoped containment to the **first-party** IMAP connector — without regressing the LUM-618 guarantee on
the shared proxy, and without breaking email for native (non-Docker) deployments.

A key asymmetry drove the design: the community-capability model assumes the contained process has
**no** internet route except the proxy. **Core is different** — it is dual-homed by design (it must
reach LLM APIs, ntfy, feeds), and it is Core itself (not a sandboxed sidecar) that opens the IMAP
socket. So an "external no-route wall" is not automatically available for Core's own egress.

## Decision

Contain email-IMAP egress in **two honest layers**, ordered so Persona A (Docker) and Persona C
(native) get equal *baseline* protection.

**Layer 1 — in-process allowlist (primary; every deployment).** Extend the ADR-153 `tethered`
mechanism beyond LLM calls: wrap the **single** `imapclient.IMAPClient` construction site
(`EmailImapSource._connect`, through which all four paths funnel — `poll`, `fetch_bodies`,
`apply_writeback`, `reverse_writeback`) in `tethered.scope(allow=[<egress target host>])`, gated by an
**independent** `LUMOGIS_FF_IMAP_EGRESS_GUARD` flag. `tethered` matches on the **hostname** passed to
`getaddrinfo` (verified by spike), so the scope permits exactly the one host Core dials. Because
hostname-matching cannot catch a DNS rebind to an internal IP, `_connect` additionally re-validates
the resolved IPs against the SSRF policy immediately before the socket opens (direct path). Runs
identically on Docker and native. **Honest posture (mirrors ADR-153):** in-process ⇒ **bypassable by
a fully-compromised Core** — defense-in-depth and a correctness gate, not an external wall. Fails
safe-to-functional: flag off or `tethered` unavailable ⇒ email keeps working under the SSRF guard.

**Layer 2 — Docker-only egress proxy (network wall for the IMAP path).** When
`LUMOGIS_IMAP_EGRESS_PROXY` is set, Core routes IMAP(S) through the LUM-618 Squid proxy via HTTP
`CONNECT` (a small subclass of `imapclient`'s TLS transport opens the tunnel, then negotiates TLS
end-to-end with `server_hostname = mail host`, so the certificate and the spliced SNI are the real
host — no MITM). The proxy allowlists mail hosts by SNI from a **household-union allow file**
regenerated from declared mailboxes (static regen on credential change via the connector-credential
change-listener; applied by proxy restart / `squid -k reconfigure`). The `CONNECT` allow is
**source-scoped to Core's static IP** (`core_egress_src 172.31.0.3`) so a contained community
capability on the *shared* proxy cannot use it. Two properties Layer 2 delivers that Layer 1 cannot:
(1) **network-level enforcement of the IMAP transport path** — the proxy refuses any undeclared
`CONNECT`, constraining legitimate/injected-but-not-fully-compromised IMAP egress at the network layer;
(2) the **DNS-rebinding TOCTOU is closed** — Core connects to the *proxy* and never resolves the mail
host, so the proxy owns resolution + allowlisting. **Honest scope (do not overstate):** this is **not**
a hard wall against a *fully-compromised* Core — Core is **dual-homed** (it keeps a direct internet leg
for LLM/ntfy/feeds), so a compromised process can open a socket that bypasses the proxy entirely,
exactly as it can bypass Layer 1. Both layers are bypassable by full compromise; Layer 2's gain is
network-layer enforcement of the actual IMAP path + the rebinding closure, not an independent
guarantee against arbitrary compromise (`docker/egress-proxy/squid.conf` states the same).

Mailbox hosts are arbitrary user data, so a host the manifest normaliser can't express (IDN, IPv6,
nonstandard port) is **skipped-and-logged** — never a hard failure; Layer 1 still contains it, and when
a proxy is configured such a host takes the **direct** path (it can't be allowlisted at the proxy, so
proxying it would hard-fail every connect). The
allow file is written **atomically** (temp + `fsync` + `os.replace`) and never empty (a
never-matching RFC-2606 sentinel) so a mid-write or empty ACL can't take down the shared proxy.

## Alternatives Considered

- **Move IMAP polling into a contained no-route sidecar** (the community-capability model). Rejected:
  a large architecture change and a new failure mode for a first-party feature, and it cannot help
  native. Layer 1 gives cross-persona enforcement without it.
- **A dedicated IMAP-aware egress proxy** instead of reusing the LUM-618 Squid. Rejected: a second
  containment mechanism to secure and maintain; the SNI-splice `CONNECT` path reuses the existing one.
- **Host-level nftables per-port egress policy on Core's default leg.** Rejected as primary in
  ADR-173 (not hostname-aware; CDN churn; `NET_ADMIN`); remains the only route to a *native* hard
  wall and stays a deferred follow-up.
- **Refusing email on native** (the untrusted-plugin resolution). Rejected: email is first-party and
  must work on Persona C.

## Consequences

**Easier:** egress-containment parity for the last uncovered signal source; network-level enforcement
of Core's IMAP transport path on Docker (bypassable only by full compromise, like Layer 1); the
DNS-rebinding TOCTOU closed on the proxy path; `external_endpoints`-style SNI allowlisting reused for a
first-party connector.

**Harder / committed:** the hard layer is **Docker-only** (native keeps Layer 1 + the SSRF guard,
documented honestly); the household-union allow file needs **static regen + operator reload** until a
dynamic control plane exists (the deferred half of ADR-174 deviation #3); the `CONNECT` shim couples
to `imapclient`'s transport internals (`_create_socket` / `_create_IMAP4`); the shared `squid.conf`
now carries a Core-source-scoped rule that must stay scoped (guarded by
`scripts/check_email_imap_egress_wiring.py` + a community-source-denied integration test).

**Future chunks must know:** `egress_guard.imap_egress_scope` / `imap_egress_target_host` and the
`allow/lumogis-email-imap.txt` path + `core_egress_src` ACL are the contract; Layer 1 must remain the
primary, persona-parity layer and Layer 2 strictly additive.

## Revisit conditions

- **ECH (encrypted ClientHello) mainstream** — SNI would be invisible to `splice`, breaking the
  Layer-2 allowlist; revisit the enforcement point. (Same root cause as ADR-173's ECH revisit.)
- **A native (non-Docker) hard-containment path is wanted** — Layer 2 cannot cover it; a host-firewall
  story would be needed.
- **A dynamic Core-push reload control plane** — if static regen + operator reload proves too manual
  at scale (also an ADR-173 residual).
- **Per-user egress discrimination** — Squid `src` ACLs key on source IP and all users share Core's
  IP, so true per-user Layer-2 scoping needs proxy-auth; per-user tightening otherwise lives in
  Layer 1.
- **Authors/operators routinely need IPv4-literal or nonstandard-port mailbox hosts** — extend the
  allow-file generator + a `dst`/port ACL branch (today skipped-and-logged).

## Status history
- 2026-07-20: Plan authored (`/create-plan LUM-625`); 2 review rounds (Fable R1 found the Layer-2
  "survives compromise" overclaim → reframed as cooperative audit; Sonnet R2 verified and closed the
  spike/UX-claim gaps). Plan `ready`.
- 2026-07-20: Implemented in 3 passes on `claude/devtools-memory-skills-review-85zmn1` — Layer 1
  (`03b9068`), Layer 2 allow-file + audit (`589c881`), Pass 3 CONNECT-proxy routing (`234ef6f`).
- 2026-07-20: Adversarial code/security review (Sonnet + Fable) confirmed Layer 1 sound and found a P0
  (unsourced shared-proxy `CONNECT` rule) + P2s — all fixed (`2fed5c0`): source-scoped ACL,
  no-DNS-on-enumerate, atomic allow-file write, distinct `ImapHostBlockedError` → permanent write-back
  classification.
- 2026-07-20: Pass 3/4 adversarial review (Sonnet + Fable) caught a re-introduced "wall against a
  compromised Core" overclaim (reworded to the honest posture) + fail-open-on-bad-config,
  proxy-ineligible hard-fail, unbounded timeout, and a status-parse bug — all fixed (`3d29aea`).
- 2026-07-20: **Finalised by /verify-plan** — implementation confirmed the decision across all four
  passes; unit/functional suites green (98 tests), ruff + wiring guard clean. ADR-174 deviation #3
  closed. The Docker network proofs (`make egress-imap-test`, `egress-containment-test`) run in CI
  (image pulls unavailable in the dev sandbox) — recorded as a before-public-release gate, not a
  `dev`-landing blocker.
