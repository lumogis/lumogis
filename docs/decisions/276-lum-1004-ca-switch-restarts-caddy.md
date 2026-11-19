# ADR 276 — A CA switch restarts Caddy; "the name is new" only after a first registration

- **Status:** Finalised
- **Created:** 2026-11-18
- **Last updated:** 2026-11-18
- **Decided by:** as-shipped implementation (retrospective)
- **Finalised by:** /record-retro 2026-11-18 (claude-opus-5-5)
- **Plan:** none — shipped before formal plan / verify cycle for this chunk
- **Exploration:** `.cursor/explorations/LUM-1004-secure_address_ca_switch_restart_retro.md`
- **Draft mirror:** `.cursor/adrs/LUM-1004-secure_address_ca_switch_restart.md`
- **Amends:** ADR 275 (in-place re-apply on a CA change)

## Context

ADR 275 (LUM-996) made a change of `LUMOGIS_HOME_DNS_ACME_STAGING` re-render the household's Caddy site and reload Caddy, with no Turn off / Turn on. The live check in LUM-998 step 0 (2026-11-18, a Mac on Docker Desktop holding a production certificate) showed the reload is not enough: the re-rendered site named the staging CA and Caddy logged "load complete", but kept serving the production certificate from its in-memory cache, so the card waited in issuing. A manual `docker compose restart caddy` served the staging certificate at once. The same run showed "The name is new" after an address change on a box whose state file predated LUM-996.

## Decision

- **Restart Caddy when the other CA's certificate is still served.** Core's refresh/follow-issuance job restarts Caddy when the certificate probe returns `other_ca` while the block for the requested CA is already in place, at most once every 10 minutes. Because it follows the probe, the restart happens only after the new block is on disk.
- **Through a dedicated stack-control endpoint.** `POST /caddy/restart` is token-guarded, takes no input, runs `docker compose restart caddy`, and returns only `{ok, code}`; `caddy` stays out of the generic restart allow-list.
- **The name-new window belongs to a first registration.** When a state file has no `name_published_at` (written before LUM-996), the registration is older than the window and the site was already applied, the publish time is the registration time.
- **Docker Desktop:** the card recommends a DHCP reservation, because Docker Desktop cannot follow an address change.

### As-implemented surface

- stack-control: `POST /caddy/restart` → `{"ok": true, "code": "restarted"}` or `{"ok": false, "code": "restart_failed"}`; timeout 60 s.
- Core: `services/home_dns/service.py` `_restart_caddy()`, `_restart_for_other_ca()`, `_CA_RESTART_EVERY_SEC = 600`; `a_published` legacy rule.
- Web: `secureAddressCopy.ts` `docker_desktop` text.

## Alternatives considered

- Not chosen: restart Caddy on every settings-driven re-apply. It would also restart for a name-service URL or port change that a reload handles, and it fires before the result is known.
- Not chosen: add `caddy` to stack-control's generic restart allow-list (LUM-331 kept it out on purpose).
- Not chosen: rely on Caddy renewing at the next expiry; certificates are valid for months.

## Consequences

- A CA switch completes with `docker compose up -d` alone, at the cost of one Caddy restart (seconds without HTTPS on that box).
- If Caddy cannot obtain the requested CA's certificate, the box restarts Caddy every 10 minutes while the card stays in issuing.
- Upgraded boxes do not show "the name is new" for an old name.

## Revisit conditions

- Caddy gains per-certificate cache eviction on reload.
- The probe's `other_ca` rule (the "(STAGING)" issuer prefix) changes.
- Restart loops are seen in the field.

## Linear linkage (Product OS)

- **Issue:** LUM-1004 (child of LUM-996); live evidence on LUM-998.
- **Backfill needed:** no. **Historical only:** no.

## Testing retrospective

Five Core tests and three stack-control tests added, one Web assertion; the three behaviour tests fail with the fixes removed. Tier 1: orchestrator 7824 passed, home_dns 163, stack-control 77, Web admin 220, `make lint` and `make changelog-check` green, also on merged `dev`. Live: the Mac flipped staging ↔ production with no manual restart. Not run: tier 2 (no database code touched).

## Status history

- 2026-11-18: Finalised by /record-retro (retrospective).
