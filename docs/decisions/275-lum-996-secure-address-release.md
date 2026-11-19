# ADR-275: Secure address ready for a release: release registration value, rotation, and the Pass 8 fixes (LUM-996)
**Status:** Finalised (revised: implementation deviations recorded in "As built")
**Created:** 2026-11-18
**Last updated:** 2026-11-18
**Decided by:** /explore LUM-996 (Claude Code); owner decision Thomas 2026-11-18 (a public release value, a private test value, a list of values in the name service)
**Finalised by:** /verify-plan 2026-11-18 (Claude Code)
**Plan:** `.cursor/plans/LUM-996-secure-address-release.plan.md`
**Draft mirror:** `.cursor/adrs/LUM-996-secure_address_release.md`
**Amends:** ADR 115 (registration value), the LUM-331 secure-address design; builds on ADR 145
**Exploration:** `.cursor/explorations/LUM-996-secure_address_release.md`
**Linear:** LUM-996

## Context

LUM-331's secure address worked live on 2026-11-18, but Pass 8 found four problems:
1. A cached DNS miss right after registration was reported as rebind filtering.
2. No address detection or Docker Desktop note appeared before Turn on.
3. Switching between the staging and production CA needed Turn off / on.
4. Staging certificates cannot be tested in a browser on `.app`, because `.app` is HSTS-preloaded.

A clean install from the Release files also had no registration value, so it could not register. Thomas decided on 2026-11-18:
- the release ships a fresh **public** anti-spam value;
- his test value stays private;
- the name service accepts several values, so the release value can rotate.

## Decision

**Release registration value.**
- Core carries the release value as a code default, `DEFAULT_REGISTRATION_SECRET`, beside `DEFAULT_SERVER_URL`. `LUMOGIS_HOME_DNS_REGISTRATION_SECRET` still overrides it.
- The value is public by design and appears in the AGPL source.
- A rotation reaches installed boxes with the next image pull.

**Several values in the name service.**
- The Worker accepts every value in `REGISTRATION_SECRETS` (comma-separated) plus the legacy `REGISTRATION_SECRET`.
- Each value is compared in constant time, with no early exit.
- The Worker logs which list entry matched, never the value itself.
- Rotation works in four steps: add the new value, ship it, watch the log until the old value is no longer used, then remove it.

**The name is new.**
- For 35 minutes after a (re)registration, the status exposes `name_new_until`. The window starts at the later of the registration and the first A-record write after it (`name_published_at`). A CA switch, an address change or a re-apply never restarts it, because the name was already published.
- During that window, a resolver miss or a failed device check shows "the name is new, give it up to 30 minutes" instead of `router_blocked`.
- After it, `router_blocked` needs a resolver verdict taken after the window ended; a verdict from inside the window never counts.
- The rule uses time, not the DNS response code, because rebind filters answer differently.

**Detection before Turn on.**
- A new admin route, `POST /api/v1/admin/secure-address/detect`, runs the existing stack-control helper when the card opens in the `off` or `needs_address` state. `GET` stays read-only.
- An RFC 1918 request `Host` is preferred as the suggestion, because the admin's device has just reached Lumogis at it. The detected address (the default route's source, which a VPN, a second NIC or a Docker route can make unreachable from a phone) fills in when `Host` is not an RFC 1918 literal, for example `localhost`. So the address source becomes `detected` only where there was no usable `Host`, or where both agree.
- A `detected` address follows the host only within the same /24 (a DHCP lease change). A move to another network (a VPN, a second NIC) only warns `address_mismatch`, because following it would repoint the public A record to an address phones may not reach.
- On Docker Desktop, the note explains how to find the address on macOS and on Windows.

**Snippet fingerprint.**
- Core stores a hash of the rendered Caddy snippet.
- In `refresh_job` (step 5b), and in a one-off run 60 s after boot when a setting changed while Core was down, Core re-applies the snippet in place when the hash differs. This covers a change of CA, name-service URL, HTTPS port or template, which reach Core when it is recreated (`docker compose up -d`; `restart` keeps the old environment).
- The A record is not touched.
- A failed reload during a re-apply puts the previous block back on disk when Caddy refused the new config (`ok: false`); Caddy is still running the old one, so the site keeps working. A reload that times out or cannot reach stack-control does not prove which config runs, so the block is removed and the normal enable path re-applies it on the next run (see As built).
- The certificate probe reads the issuer. A certificate from the other CA than the one asked for counts as no certificate **once the re-applied block asks for the new CA**, so a CA switch shows `issuing` until the new certificate is served. Before that (or when the re-apply fails) the old CA's certificate still counts, so a working site never shows `certificate_pending`.
- The CA switch is proven on a `dev` stack before any rc (how Caddy behaves when the issuer changes is the one thing unit tests cannot show).

**Docs.** They state that staging certificates can only be tested with `curl -k` or `openssl`, never in a browser on `.app`.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-996-secure_address_release.md`.

- **Value in `docker/release/example.env` or as a compose default:** a rotation would never reach installed boxes, because updates do not rewrite `.env` and rarely replace the compose file.
- **Value as a build argument from a GitHub secret:** gains no secrecy (`docker inspect` shows it), and checkouts would not register.
- **Two named Worker secrets:** a rotation needs three values at once.
- **Values in KV:** a data-store read on every `/register` for a public value.
- **Telling a cache miss from rebind filtering by DNS response code:** unreliable. Fritz!Box times out, unbound strips the answer or returns SERVFAIL, dnsmasq discards it, and Docker's DNS sits in between.
- **A control name pointing at a private address:** needs a third party or an ops record. Kept as a Later diagnostic.
- **Detection from `GET`, or detection in `refresh_job` while off:** the first gives a read endpoint side effects; the second runs containers on installs that never use the feature.
- **Storing only the staging flag:** misses URL, port and template changes at the same cost.

## Consequences

**Easier:**
- A clean release install registers with no setting.
- The release value rotates with an image update.
- Admins on Docker Desktop and on `localhost` see a detected address or clear instructions.
- Switching CA needs only `docker compose up -d`, no Turn off / on.
- Brand-new names no longer send people into router settings.

**Harder or impossible:**
- The release value is permanently public. Anyone can register through the Worker within its rate limits, its RFC 1918 rule and its certificate budgets. Those limits stop the name service being used for public sites (ADR 145), not the value.
- Those limits are shared, so they are also an **availability** target: someone with a few dozen source addresses can fill the 180 live households or the 35 weekly first certificates, and real households then get `service_full` or `budget_exhausted`. Accepted. Rotation or removal of a public value is not containment. The operator's levers, with no release, are the Worker's `MAX_LIVE_HOUSEHOLDS` / `FIRST_CERT_BUDGET` overrides, with its `HIGH_WATER` log as the signal (RUNBOOK).
- A real rebind block on a brand-new name is reported up to 35 minutes late. The router guide stays available.

**Future work must know:**
- The Worker must accept a release value **before** any image carrying it is published. `make check-home-dns-registration` checks it without side effects (the Worker checks the value before any key material). It runs before the export push (a `gate:pre-main` issue carries it to the release lane), and `release-assets.yml` runs it again on the tag tree and refuses to publish the Release if the value is refused.
- Thomas's test value lives only in the Worker secret and his own `.env` files. It never enters either repo.
- Re-registration restarts the "name is new" window.

## Revisit conditions

- Resolvers are seen caching misses longer than 35 minutes, or the zone's SOA `min_ttl` changes (for example, lowered to 300 s). Then re-derive `NAME_NEW_WINDOW`.
- Registration abuse shows up in the Worker logs or CT monitoring despite the budgets. Then consider a retired-value list (`410 retired`) or a non-public registration step.
- Docker Desktop gains a supported way for containers to read the host's LAN address. Then drop the manual step from the `docker_desktop` note.
- Core gains a per-request hook for other reasons. Then add "last RFC 1918 `Host` seen" as a suggestion source.

## As built (verified 2026-11-18, LUM-996)

- **Code:** `orchestrator/services/home_dns/{worker_client,state,service}.py`, `routes/admin_secure_address.py` (`POST /detect`; `POST /retry` also queues `refresh_when_free`), `models/api_v1.py` (`name_new_until`); Lumogis Web admin card, copy module and API client; `scripts/release/check_home_dns_registration.py` + `make check-home-dns-registration` + a step in `.github/workflows/release-assets.yml` before every publish step. The name service's list compare (`REGISTRATION_SECRETS`) is a separate commit in the operated name-service repository.
- **Deviation — reload timeout.** The plan wrote the old block back on any reload failure. As built, the write-back happens only on Caddy's refusal (`caddy_reload_failed`). A timeout or an unreachable sidecar (`caddy_unreachable`) removes the block, clears `snippet_fingerprint` and `snippet_applied_at`, and the enable path re-applies after the backoff (`test_reapply_reload_timeout_is_not_treated_as_a_refusal`). Reason: after a timeout Core cannot tell whether Caddy kept the old config. Cost: in that rare case the site is down until the next unbacked-off run.
- **Deviation — start-up check.** It is a one-off scheduler job 60 s after boot, not an inline re-apply at start-up: Caddy may not be up when Core boots.
- **Release-check script:** refuses a name-service URL that is not `http(s)://`, so `urlopen` never opens `file:` or a custom scheme (bandit B310; `test_a_non_http_url_is_cannot_tell_and_sends_nothing`).
- **Amends ADR 115:** the registration value is now a list in the name service, and the release value ships in Core and rotates with a Core release.
- **Not yet proven live:** the CA switch on a real Caddy, the name-service deploy with the value list, a live `make check-home-dns-registration` and the release-install proof. These are owner-only steps tracked as a `gate:pre-main` child of LUM-996.

## Status history
- 2026-11-18: Draft created by /explore (headless, LUM-996)
- 2026-11-18: Revised during /review-plan --arbitrate R1 — `Host` now beats detection as the suggestion (critique D2-1: detection can pick an address phones cannot reach); `router_blocked` after the window needs a post-window verdict; failed re-apply reload restores the old block; issuer-aware probe for CA switches; `docker compose up -d`, not restart; availability consequence of the public value and the release-ordering check recorded.
- 2026-11-18: Revised during /review-plan --arbitrate R2 — the "name is new" window anchors on the first A-record write after a registration, not on the snippet (a CA switch restarted it); a detected address follows only within its /24 (a VPN could repoint the A record); an `other_ca` certificate is dropped only once the new block is applied (a failed re-apply showed `certificate_pending` on a working site); the registration check also runs in `release-assets.yml`; the CA switch is proven on `dev` before the rc.
- 2026-11-18: Finalised by /verify-plan — implementation confirmed the decision, with two deviations recorded in As built (reload timeout removes the block; start-up check as a one-off job).
