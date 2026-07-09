# ADR-115: Operated household-cert service — Cloudflare-proxy Worker + box-side DNS-01 (LUM-521)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** `/explore` (LUM-521) + `/review-plan --arbitrate` R1; implementation in `lumogis/lumogis-cloud` @ `a554b87`

**Issue:** [LUM-521](https://linear.app/lumogis/issue/LUM-521/operated-household-cert-service-lumogis-cloud-dns-zone-dns-01-issuance)

**Related:** [ADR-102](102-repo-topology-cloud-services-boundary.md) (operated-services boundary), LUM-473 (box-side Caddy/libdns), LUM-506 (relay reuses household name), LUM-522 (PSL), LUM-523 (zone/CAA/CT).

## Context

LUM-473 decided that the Lumogis Server appliance gets a **real Let's Encrypt cert** on a permanent `{household}.homes.lumogis.app` via **DNS-01** (the only ACME method for an inbound-less box), with the **box holding its own cert key** so Lumogis can't MITM — serving laptop Web **and** mobile PWA on the LAN with no per-device CA install, and reused verbatim by the relay later. That requires an **operated DNS + DNS-01 service** which is **not** in LUM-508's scope (relay/issuer/registry). Two verified constraints: Cloudflare API tokens are **zone-scoped, not subdomain-scoped** (a box can't safely hold one), and issuance must be **box-side** (to keep the key on the box). Platform is Cloudflare (ADR-102).

## Decision

Build **"Lumogis Home DNS"** — a **Cloudflare Worker** in `lumogis-cloud` that holds the zone-scoped Cloudflare token **server-side** and exposes a small **authenticated, per-household-scoped** API the appliance calls for **its own subdomain only**: (1) **allocate** an opaque `{household}.homes.lumogis.app` at first-run; (2) **A-record DDNS** (box posts its LAN IP); (3) **DNS-01 TXT** (box posts the ACME challenge token). The **box runs its own ACME client** (Caddy + a small custom **libdns** provider targeting the Worker) and **holds its own key**. The Worker enforces the per-subdomain scoping that Cloudflare tokens cannot. The service is **free, carries no entitlement** (separate from LUM-263), and the directory stores only `name → IP` (no household data). Harden the zone-control trust point with a **CAA** record (Let's Encrypt only) and **CT-log monitoring** (Cert Spotter / Cloudflare CT) alerting `security@lumogis.app`. Keep **acme-dns** (with the off-the-shelf `caddy-dns/acmedns` provider) as the documented **PoC/fallback**.

## Alternatives Considered

- **acme-dns (self-hosted) + separate DDNS** — mature, off-the-shelf Caddy provider, per-device least-privilege; but a **non-CF persistent Go service** + a **second** A-record/DDNS system. Kept as PoC/fallback, not the platform-coherent end state.
- **Box holds a Cloudflare API token** — ruled out: CF tokens are zone-wide → any box could edit every household's DNS.
- **Cloud-side issuance (cloud holds the cert)** — ruled out: breaks box-holds-key (MITM-capable); that's the relay/Nabu-Casa model, right for *remote*, wrong for the LAN.
- **Per-household NS delegation** — ruled out: too heavy for Persona C/ops. **Wildcard single cert** — ruled out: one shared key across households.

Full comparison: `cursor/explorations/archived/LUM-521-operated-household-cert-dns01.md` (lumogis-devtools).

## Consequences

- **Easier:** CF-serverless (matches the whole `lumogis-cloud` platform); one authenticated API does both DDNS (A) and DNS-01 (TXT) scoped per household; the Cloudflare token never leaves the cloud; the **box holds its own key** (every LUM-473 privacy pillar holds); the name/cert are reused by the relay (LUM-506) later — build once.
- **Harder / cost:** we write+maintain a small **box-side libdns provider** and a **box↔Worker auth/registration** contract; the Worker becomes the per-household authz point and must be solid; an operated Cloudflare zone + CAA + CT monitoring to run.
- **Future chunks must know:** **LUM-473** builds its Caddy/DDNS/ACME wiring against this contract; **LUM-331** (Docker Caddy) should converge; **LUM-506** reuses the household name + cert; this is a **free** service distinct from the entitlement issuer (LUM-263).
- **Public Suffix List is a hard prerequisite (surfaced in plan review):** Let's Encrypt computes its **~50 certs / registered-domain / week** limit via the PSL, so by default *all* `{household}.homes.lumogis.app` certs share one `lumogis.app` ceiling — a fleet-scaling wall and a shared-pool DoS vector. **`homes.lumogis.app` must be added to the PSL (PRIVATE section)** so each household is its own registered domain (own 50/week bucket) — the same mechanism `workers.dev`/`pages.dev`/dynamic-DNS providers use. **Weeks of lead time → submit early;** also sets the cookie/registrable-domain boundary (privacy bonus). Tracked as blocking child **LUM-522**.

## Implementation (as shipped)

- **Repo:** `github.com/lumogis/lumogis-cloud` — first operated-service code Worker; `main` @ `a554b877cd92ec79c780ec7e05bf2e4174e5f5ff` (2026-06-22).
- **Worker** (`services/home-dns/`): Hono router; endpoints `POST /register`, `PUT /a`, `PUT /txt`, `DELETE /txt`, `POST /rotate`, `GET /healthz`; deterministic HMAC slug (`slug.ts`); KV registry with hashed bearer; Ed25519 proof-of-possession on register/rotate; Cloudflare DNS client with list-by-name orphan reconcile; multi-value TXT append/delete; `MAX_TXT_RECORDS=10`; per-IP `/register` rate limit via KV counter.
- **CI:** `.github/workflows/deploy-home-dns.yml` — test + typecheck on PR/push; `wrangler deploy` on `main` push (requires `CLOUDFLARE_API_TOKEN` + Wrangler secrets — not yet configured for live deploy).
- **Tests:** 22 vitest unit tests (`test/home-dns.test.ts`) with mocked Cloudflare — scoping, multi-TXT, POP/rotate, orphan-reconcile, bounds, CF-429→502.
- **Not shipped in this chunk (tracked children):** PSL submission (**LUM-522**), zone + CAA + CT provisioning (**LUM-523**), live staging PoC with LE, box-side libdns (**LUM-473**).

## Revisit conditions

- If the custom box-side libdns provider proves slow, ship the **acme-dns fallback** (off-the-shelf provider) to unblock LUM-473, then swap to the Worker provider.
- If Cloudflare ever adds **subdomain-scoped tokens**, reconsider whether the proxy Worker is still needed (likely still yes, for registration/registry).
- If the relay (LUM-506) lands first with a LAN-capable mode, re-weigh whether box-side LAN certs are still the primary path.
- If a household needs **bring-your-own-domain**, expose the same box-side ACME against the user's DNS (Persona A option from LUM-473).

## Status history

- 2026-06-22: Draft created by `/explore` (LUM-521). Recommendation: Cloudflare-proxy Worker ("Lumogis Home DNS") for per-household-scoped A + DNS-01 TXT, box-side ACME (box holds key), free/no-entitlement, CAA + CT-monitoring; acme-dns as PoC/fallback.
- 2026-06-22: Refined during `/review-plan --arbitrate` R1 (LUM-521). **No decision overturned** — added PSL prerequisite, deterministic HMAC slug, exact CAA/CT contracts, byte-exact Ed25519 canonicalisation, `MAX_TXT_RECORDS` cap, input bounds, KV-default/DO-fallback.
- 2026-06-22: Finalised by `/verify-plan` — Worker implementation confirmed in `lumogis-cloud`; P1 ops dependencies (PSL, zone/CAA/CT, live deploy) remain on children LUM-522/LUM-523.
