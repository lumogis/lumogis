# ADR-173: Container-network egress enforcement for untrusted community capabilities (LUM-618)

**Status:** Finalised
**Created:** 2026-07-16
**Last updated:** 2026-07-17
**Decided by:** /explore (Opus 4.8); finalised by /verify-plan
**Finalised copy:** docs/decisions/173-lum-618-container-egress-enforcement.md

> **Implementation note (2026-07-17, /verify-plan):** implemented as decided, with two
> review-driven refinements: (1) the "containment present" marker is **overlay-provided**
> — the image default `contained_capabilities.txt` is empty (fail-closed) and the egress
> overlay supplies the marker via `LUMOGIS_CONTAINED_CAPABILITIES_FILE`, so the marker
> cannot be asserted without the wiring that backs it; (2) Pass C's "sole bridge" is
> enforced as *"every community service has no external network leg"* (keyed on the
> `internal` flag) rather than forbidding all dual-homing, because Core legitimately joins
> the isolated network for inbound dispatch and does not forward. IPv4-literal
> `external_endpoints` are deferred (need a separate IP-only allow file for a Squid `dst`
> ACL). The live Squid peek/splice run remains the implementation PoC (validated in CI).
>
> **Ops closure (2026-07-17, LUM-621 /verify-plan):** no ADR change — digest-pinned
> Squid default, marker mtime-reload + ACL `--check` divergence gate, and Core
> `egress.denied` tailer (spike outcome **A**) ship as operational closure under this
> decision. Security residuals (IPv4 `dst`, ECH / domain-fronting, native non-Docker,
> LUM-614 signing) stay deferred as listed in Revisit.

## Context

LUM-507 pillar b-hard: an untrusted "community" out-of-process capability runs in its **own container** and makes its **own** outbound calls, so Core's in-process controls (LUM-613's dispatch gate, LUM-553's `tethered.scope()`) cannot constrain its egress — ADR-153 names OS/container network policy as the only hard layer. The marketplace posture (LUM-507) is open self-serve with open source merely *encouraged*, so the sandbox is the entire security load and this containment **gates the public community launch**. Today there is **no network segmentation at all** — every container shares one bridge with full internet egress. LUM-613 already ships the input this needs: `external_endpoints` (bare hostnames/IPv4, no ports/wildcards) + `is_community` on `RegisteredService`.

## Decision

Enforce community-capability egress at the **container-network layer**: each untrusted community capability runs on an **`internal: true` Docker network** (Docker gives it no route to the internet), and its only egress path is a dedicated dual-homed **Squid forward proxy** that allows **only** the capability's declared `external_endpoints`. HTTPS is allowlisted by **SNI using Squid `peek`+`splice` (no MITM / no TLS decryption / no CA injection)**; HTTP by `dstdomain`; per-source ACLs scope each capability to its own allow file. The per-capability allow file + network wiring are generated at install time from the manifest (dynamic Core-push deferred). A new `check_compose_policy.py` pass asserts community services join the isolated network and never an internet-facing one; a CI integration test proves a community container can reach an allowed host and **cannot** reach `evil.com`. Core's dispatch gate consumes a per-capability **"containment present"** marker (replacing the blunt global `LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES` flag). Hard enforcement is **Docker-Compose-only**; native deployments keep the LUM-613 fail-closed dispatch gate (community refused).

## Alternatives Considered

- **tinyproxy** (lighter forward proxy) — BRE-regex allowlist is a security footgun and per-capability scoping is clumsier than Squid's typed ACLs. Viable lightweight fallback. See exploration.
- **Envoy** (SNI-native L7 egress) — right capability, too heavy (xDS config, image size) for the self-hoster/NUC persona.
- **Per-container iptables/nftables** — not hostname-aware; brittle with CDN IP churn; needs `NET_ADMIN`. Possible belt-and-braces layer, not the primary mechanism.
- **nginx-per-domain / DNS-only filter / Caddy forward_proxy reuse** — don't scale, bypassable, or conflate trust zones.

Full detail: `.cursor/explorations/LUM-618-container-egress-enforcement.md`.

## Consequences

**Easier:** a real "cannot reach evil.com" guarantee for untrusted capabilities (Docker-enforced no-route + auditable hostname allowlist); the community marketplace can open publicly; end-to-end TLS preserved (SNI-splice, Core never sees plaintext); `external_endpoints` gets a concrete enforcement consumer.
**Harder / committed:** introduces the first network segmentation + a new (profile-gated) Docker service; the hard guarantee is **Docker-only** (native = community refused); trust is anchored to operator-controlled compose/network wiring until LUM-614 signing; install-time allowlist generation is a manual/tooling step until a dynamic control plane is built.
**Future chunks must know:** the isolated-network + egress-proxy topology and the per-capability allow-file format are the contract a marketplace install flow (LUM-171) must generate; the "containment present" marker is the new gate signal (retire the global opt-in flag).

## Revisit conditions

- **ECH (encrypted ClientHello) becomes mainstream** — SNI would no longer be visible to `splice`, breaking SNI-based allowlisting; revisit toward a different enforcement point. (Also confirm at v1 that the reference allowed host does not already negotiate ECH — it would fail-closed today.)
- **SNI/Host divergence ("domain fronting") on shared multi-tenant infrastructure** — because `splice` never decrypts, the Host header (or HTTP/2-coalesced streams) is invisible; a capability declaring a shared-infra hostname (CDN edge, serverless/edge-function host, object-storage front door) could reach a different tenant behind the same edge IP with an allowed SNI. Same root cause as ECH (SNI-only visibility), distinct trigger. The no-route guarantee is unaffected; "reaches only declared hosts" is weaker than "cannot reach evil.com" for such hosts. Hard mitigation (per-capability dedicated egress IP, or L7 Host inspection which conflicts with no-MITM) is a deferred follow-up; v1 documents the residual and steers curation toward dedicated endpoints.
- **Authors routinely declare IP-literal `external_endpoints`** — add/confirm a Squid `dst` IP ACL branch.
- **The marketplace ships at scale** — if install-time static allowlist generation proves too manual, build the dynamic Core-push reload control plane (deferred follow-up).
- **A non-Docker (native/bundled) untrusted-capability path is ever wanted** — this ADR's mechanism cannot cover it; a separate containment story would be needed.

## Status history
- 2026-07-16: Draft created by /explore (LUM-618). Recommendation: internal network + Squid SNI-splice egress proxy; High confidence; small PoC (mock capability + containment test) recommended and doubles as the v1 deliverable.
- 2026-07-16: Revised during /review-plan --arbitrate R1 (Opus 4.8 on Sonnet 5 critique) — added **SNI/Host domain-fronting** as a second, independent Revisit condition alongside ECH (both stem from SNI-only visibility; the plan now names it as a documented residual with a deferred hard mitigation). Core no-route/no-MITM decision unchanged. Also captured (in the plan, not the decision): `enable_ipv6: false` on both networks, the SslBump `bump.pem` custody rule, the CI-verifies-repo-not-runtime marker-drift residual ("only the shipped overlay is supported"), and Pass C proven-fires-on-violation.
- 2026-07-17: **Finalised by /verify-plan** — implementation confirmed the decision (commits 988c9e7 + ab84f5c on `claude/lum-618-container-egress-enforcement`). Two review-driven refinements recorded in the implementation note above (overlay-provided marker closing the runtime marker-drift; Pass C sole-bridge scoped to community-service external membership). Finalised copy at docs/decisions/173-lum-618-container-egress-enforcement.md.
- 2026-07-17: **Ops closure LUM-621** (/verify-plan) — ADR decision unchanged; digest pin + marker mtime + ACL divergence check + `egress.denied` (spike A) documented as ops under ADR-173.
