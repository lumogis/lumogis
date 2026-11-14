# ADR-246: Deployment hardening — orchestrator on loopback, Caddy-resolved client IP, non-root containers
**Status:** Finalised
**Created:** 2026-11-08
**Last updated:** 2026-11-09
**Decided by:** claude-opus-5-5 (/explore --headless LUM-887), revised at /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-09 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-887-deployment-hardening.plan.md`
**Exploration:** `.cursor/explorations/LUM-887-deployment-hardening.md`
**Draft mirror:** `.cursor/adrs/LUM-887-deployment-hardening.md`
**Linear:** LUM-887 (child of LUM-883, security audit #2)

## Context

Security audit #2 (umbrella LUM-883) found three defaults that widen a default compose install's attack surface:

- **INFRA-001 (Medium).** The orchestrator's port 8000 was published on every host interface, which bypasses Caddy's headers.
- **ID-007 (Low).** `LUMOGIS_TRUSTED_PROXIES` and Caddy's `trusted_proxies` trusted every private range, so any LAN peer could choose the IP its per-IP rate limits keyed on.
- **INFRA-003 (Low).** The orchestrator and web containers ran as root.

Two constraints shaped the options. First, Caddy reaches the orchestrator over the Docker bridge, not loopback, and the recommended remote path (Tailscale Serve to `127.0.0.1:80`) arrives at Caddy from the compose gateway, so naive narrowing collapses per-IP rate limiting. Second, the orchestrator writes to host bind mounts (`/project/.env`, `config/librechat.yaml`, `ai-workspace`) at runtime, not only at first boot, so a plain `USER` line breaks upgraded installs.

## Decision

1. **Loopback publish.** Compose publishes the orchestrator as `127.0.0.1:${ORCHESTRATOR_HOST_PORT:-8000}:8000`, the same shape as Qdrant.
2. **Caddy decides the client IP; Core trusts only Caddy.**
   - Caddy's `trusted_proxies static` comes from `LUMOGIS_CADDY_TRUSTED_PROXIES` (default `127.0.0.1/32`, i.e. no upstream; space-separated CIDRs) with `trusted_proxies_strict`.
   - Every orchestrator route goes through one `(to_orchestrator)` snippet that sets `header_up X-Forwarded-For {client_ip}` and strips a client `X-Real-IP` (`header_up -X-Real-IP`). The `/events*` block carries the same two lines.
   - `LUMOGIS_TRUSTED_PROXIES` accepts hostnames as well as IPs and CIDRs, and compose defaults it to `caddy`. Hostnames are resolved with `getaddrinfo`, cached for 30 s (also after a failure), single-flight and never under the lock. An unresolvable name trusts nothing, and a failed refresh drops the previous answer. A static private or CGNAT range wider than /24 (IPv4) or /64 (IPv6) logs a WARNING once per value and is still honoured.
3. **Non-root orchestrator.** The entrypoint runs its one-shot init as root, then drops with `setpriv` to the owner of the `/project` checkout. `LUMOGIS_RUN_UID` / `LUMOGIS_RUN_GID` override this and `0` keeps root; a root-owned or unreadable checkout falls back to the image's `lumogis` UID 10001. Before the drop it:
   - repairs ownership. Named volumes and image-internal dirs at their image default paths are made to match the target UID. Host-visible binds (`ai-workspace` minus `backups/snapshots/`, `backups/` itself, `backup_*.zip`, `users/`, `.env`, `config/librechat.yaml`, `docker-compose.override.yml`, moved paths, the egress allow dir, and `/app/config` when it is on the root filesystem) only have **root-owned** entries handed over, so files a person owns are never re-owned. The walk is `find -P` + `-execdir chown -h`, and multiply-linked files the rule selects are skipped with a WARNING.
   - adds a passwd and group entry for the target UID when the image has none, and exports `USER` / `LOGNAME` / `HOME`.
   - warns (never fails) when the target UID cannot read `/data`, `/data-N` or the egress access log.

   Under a user-namespaced runtime (rootless Docker, Podman, userns-remap) the entrypoint neither drops nor repairs, because container root is not host root there. The image has deliberately no `USER`, so `docker compose exec` still enters as root.
4. **Non-root web.** The web SPA is served from `nginxinc/nginx-unprivileged` (uid 101) on the unchanged port 80. Both the orchestrator and `lumogis-web` set `no-new-privileges`.
5. **Ride-along.** `stack-control` compares its restart token with `hmac.compare_digest` over bytes.

## Alternatives Considered

See `.cursor/explorations/LUM-887-deployment-hardening.md`.
- `127.0.0.1/32` as Core's trusted proxy: collapses every client onto Caddy's address.
- Fixed compose subnet + static Caddy IP: collides with side-by-side stacks (`lumogis-test`) and host networks.
- Trusting the discovered compose subnet: trusts every container and the host.
- Shared-secret client-IP header: a new secret for a Low finding.
- Static `USER` / compose `user:` / init container: they break runtime writes or cannot repair root-owned volumes on upgrade.
- userns-remap / rootless Docker as the fix: host-level; documented as optional only.
- Re-owning anything not owned by the target UID on host-visible trees (rejected at arbitration R1): it would rewrite a person's own and group-shared files.
- Accepting commas in `LUMOGIS_CADDY_TRUSTED_PROXIES` through a wrapper script: one more moving part in the front door's start. Docs, a quoted example, a `caddy validate` pre-check and a static `.env.example` guard instead.
- Deleting Core's `X-Real-IP` fallback: Caddy strips the header instead; the fallback is only read from a trusted peer and may serve native Core behind an operator proxy.

## Consequences

- **Easier:** the trusted upstream is named, not ranged, and operators behind Tailscale Serve, Cloudflare Tunnel or a host proxy have one documented knob (`LUMOGIS_CADDY_TRUSTED_PROXIES`). The orchestrator no longer leaves root-owned files in the checkout.
- **Harder:**
  - MCP bridges and Search overlays on other machines can no longer use `http://<server>:8000`; they use the Caddy origin.
  - Until an operator sets the Caddy knob, Tailscale Serve and tunnel devices share one per-IP bucket. Per-email limits are unchanged.
  - A malformed knob (a comma, a typo) stops Caddy and with it the web UI. `caddy validate` (not `caddy adapt`, which accepts commas) is the documented pre-check, and `make caddy-config-check` pins it.
  - Make targets that `pip install` through the entrypoint pass `LUMOGIS_RUN_UID=0`.
- **Future chunks must know:**
  - Any new orchestrator volume or writable path must be added to the entrypoint's ownership repair (first: LUM-892). `test_every_writable_orchestrator_mount_is_repaired` enforces this for compose mounts.
  - Every new Caddy route to the orchestrator must `import to_orchestrator`; `test_every_orchestrator_proxy_sets_client_ip` enforces this.
  - Docker < 28 lets L2 neighbours reach `127.0.0.1` publishes (same residual as Qdrant).
  - `cap_drop` is deferred (LUM-883 "Later"); the root init needs CHOWN, DAC_OVERRIDE, FOWNER, SETUID and SETGID.

## Implementation notes (as built)

- **Comma check.** `caddy adapt` accepts a comma-separated knob and only builds a bad range; `caddy validate` refuses it. `scripts/check-caddy-config.sh` case 3 and the docs therefore use `validate`.
- **Hard-link warning.** The ownership repair reports a hard-linked file as skipped only when the repair rule would otherwise have changed it. As first implemented it also reported a person's own hard links on shared trees; this was fixed at /verify-plan.
- **Root-tool tests in CI.** `orchestrator/tests/test_entrypoint_privdrop_root.py` needs root, and the CI pytest runs as a non-root user. `ci.yml` therefore runs it in a root `python:3.12.11-slim` container, the orchestrator's base image, and a static test keeps the two images equal.
- **Gateway lookup.** `ip route show default` inside the Caddy container (BusyBox) also prints the connected subnet. The gateway is the address after `via` on the `default` line, and the docs say so.
- **Live proof** on an isolated stack (2026-11-08/09):
  - The orchestrator ran as the checkout owner (uid 1000).
  - Root-owned fixtures were handed over; files owned by another user and `backups/snapshots/` were left alone.
  - `getpass.getuser()` worked under the dropped UID.
  - `POST /kg/stop-entities` returned 200.
  - Port 8000 was bound to `127.0.0.1` and refused from the host's LAN IP.
  - 31 spoofed `X-Forwarded-For` requests through Caddy hit 429 at request 31; the old config never did.
  - With the knob set to the gateway, requests from the host kept per-device buckets.
  - The egress-overlay access log was readable as the dropped UID.
  - `lumogis-web` ran as uid 101.

## Revisit conditions

- ~~If the live proof shows Caddy's `header_up X-Forwarded-For {client_ip}` does not replace the header, switch the hand-off to `X-Real-IP`.~~ Resolved: proven live on Caddy v2.11.7 (planning) and through the shipped Caddyfile (`make caddy-client-ip-prove`).
- If an orchestrator volume or bind mount is added whose host owner can differ from the checkout owner, revisit the "drop to checkout owner" rule.
- If Tailscale Serve users report shared rate-limit lockouts, adopt zero-config trust of the compose gateway in Caddy (excluding Docker Desktop).
- If a supported runtime (for example rootless Podman) refuses a non-root bind of port 80, move the web container to 8080 and change the Caddyfile and healthcheck together.

## Status history
- 2026-11-08: Draft created by /explore
- 2026-11-08: Revised during /review-plan --arbitrate R1. Critique R1 showed that the broad "anything not owned by the target UID" repair would re-own a person's own and group-shared files (and, under rootless Docker, hand them to a sub-UID), and that the usual checkout UID has no passwd entry. The decision now limits host-visible repair to root-owned entries, skips drop and repair under a user namespace, and adds a passwd entry. The "drop to checkout owner" rule itself is unchanged.
- 2026-11-09: Finalised by /verify-plan. The implementation confirmed the decision; the as-built notes above record `caddy validate` over `adapt`, the narrowed hard-link warning, the CI root-test step and the gateway wording.
