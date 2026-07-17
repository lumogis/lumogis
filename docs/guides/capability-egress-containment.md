# Wiring a community capability for egress containment (LUM-618)

**Status:** v1 (reference pattern + CI proof). Docker-Compose only.

An untrusted **community** capability runs in its own container and makes its own
outbound calls, so Core cannot constrain its egress in-process (LUM-613 honestly
deferred the hard guarantee here). This guide shows how to contain one at the
**container-network layer** so it can reach **only** the hosts it declared.

## The model

- The capability joins an **`internal: true`** Docker network
  (`community-egress-internal`). Docker gives that network **no route to the
  internet** (and we set `enable_ipv6: false` so there is no IPv6 path either).
- Its **only** egress path is a dedicated **Squid** forward proxy
  (`egress-proxy`), dual-homed on the isolated network and an external one. The
  proxy allowlists **only** the capability's declared hosts: HTTPS by TLS **SNI**
  (`peek`+`splice` — no decryption, no MITM, end-to-end TLS preserved), HTTP by
  `dstdomain`. Everything else is denied by default.
- Core dispatches to a community capability only if its id is in
  `orchestrator/contained_capabilities.txt` (the **containment marker**), which
  the compose-policy **Pass C** check verifies matches the actual wiring in CI.

```
  ┌─────────────┐   inbound dispatch   ┌────────────────────────┐
  │ orchestrator│─────────────────────▶│ community capability   │
  │ (Core)      │  community-egress-    │ (isolated net ONLY —   │
  │ default +   │  internal (internal)  │  no internet route)    │
  │ internal    │                       └───────────┬────────────┘
  └─────┬───────┘                                   │ HTTP(S)_PROXY
        │ internet (default)                        ▼
        │                              ┌────────────────────────┐
        │                              │ egress-proxy (Squid)   │
        │                              │ deny-all; SNI-splice    │
        │                              │ only declared hosts     │
        └──────────────────────────────┴───────────┬────────────┘
                                                    ▼ community-egress-external
                                                  internet (allowed hosts only)
```

## Steps

1. **Declare `external_endpoints`** in the capability manifest — bare hostnames /
   IPv4 only (no scheme, port, wildcard, IPv6, or IDN). This is the allowlist.

2. **Generate the proxy allow file** from the manifest:

   ```bash
   python -m scripts.gen_capability_egress_acl --manifest path/to/manifest.json
   # or: python -m scripts.gen_capability_egress_acl --id my.cap --endpoints api.example.com
   ```

   This validates the id (must be `[a-z0-9][a-z0-9._-]{0,42}` — it becomes a
   filename and a Squid ACL name) and the endpoints, then writes
   `docker/egress-proxy/allow/<id>.txt`. It fails closed on any invalid input.

3. **Wire the container** onto `community-egress-internal` with a **static IP**
   (the proxy scopes each capability to its own allow file by source IP) and set
   its `HTTP_PROXY` / `HTTPS_PROXY` to `http://egress-proxy:3128`. Do **not** put
   the allowed host in `NO_PROXY` — it must go through the proxy. See
   `docker-compose.egress.yml` for the reference wiring (the mock capability).

4. **Mark it contained:** add the capability id to the marker file, then
   **restart the orchestrator** (the marker is read once at startup in v1). The
   image default (`orchestrator/contained_capabilities.txt`) is **empty**
   (fail-closed); the egress overlay supplies the marker by pointing
   `LUMOGIS_CONTAINED_CAPABILITIES_FILE` at a file it mounts
   (`docker/egress-proxy/contained_capabilities.rc.txt`) — so the "contained"
   assertion travels with the wiring that provides it and can't be left dangling
   without the isolated network. To contain your own capability, add its id to
   that overlay-mounted file (or set `LUMOGIS_CONTAINED_CAPABILITIES_FILE` to your
   own).

5. **Compose + activate the profile:**

   ```bash
   COMPOSE_FILE=...:docker-compose.egress.yml COMPOSE_PROFILES=community-egress \
     docker compose up
   ```

## Verify

```bash
make compose-policy-check-egress   # Pass C: fires on a violation, passes on the RC render
make egress-containment-test       # live: allowed spliced / denied refused / bypass no-route
```

## Honest limits (read these)

- **Docker only.** Native (non-Docker) deployments cannot contain a separate
  container's egress; community capabilities stay **refused** there (fail-closed
  on the LUM-613 dispatch gate).
- **Only the shipped overlay is supported.** CI verifies the compose YAML, not
  the artifact you actually deploy. Hand-editing the overlay, or deploying
  without it while still marking a capability contained, **voids the guarantee**.
- **SNI allowlisting has a known limit — shared infrastructure.** Because the
  proxy never decrypts (splice), it matches the TLS SNI, not the HTTP `Host`. A
  capability that declares a hostname on **shared/multi-tenant infrastructure**
  (CDN edges, serverless/edge-function hosts, object-storage front doors) could,
  via Host-header routing behind the same edge IP, reach a different tenant. This
  does **not** defeat the no-route guarantee, but "reaches only declared hosts"
  is weaker than "cannot reach anything else" for such hosts. Prefer dedicated
  (non-shared-IP) endpoints where it matters. (ECH would similarly hide the SNI —
  both are recorded ADR revisit triggers.)
- **Editing the marker no longer needs a restart; editing a manifest needs a regenerate.**
  The `contained_capabilities.txt` marker is **mtime-reloaded** on the next OOP
  dispatch (LUM-621). The static allow file does not auto-track a manifest whose
  `external_endpoints` changed — regenerate it and run
  `make check-egress-acl-divergence`.
- The egress proxy is the single trusted egress node — default image is
  **digest-pinned** (linux/amd64); keep it patched.
- **Deny signal (LUM-621):** with the community-egress overlay, Core tails Squid's
  access log and emits structured `egress.denied` WARNINGs (logger
  `lumogis.egress`). Spike outcome **A** (2026-07-17): HTTPS CONNECT denials log
  as `TCP_DENIED` under `lumogis_egress` format. Alert on that event name;
  hostname-only (no raw URLs). Dedup window:
  `LUMOGIS_EGRESS_DENY_DEDUP_SECONDS` (default 60).
