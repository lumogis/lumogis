# ADR-136: LUM-263 — lumogis-cloud entitlement issuer (Coding Graph / Household Graph JWT)

**Status:** Finalised
**Created:** 2026-06-25
**Last updated:** 2026-06-25
**Decided by:** /verify-plan LUM-263
**Linear:** [LUM-263](https://linear.app/lumogis/issue/LUM-263/lumogis-cloud-stripe-webhook-licence-jwt-issuance-and) (child of LUM-260)
**Repo:** `lumogis-cloud/services/entitlement-issuer/` @ verify-plan 2026-06-25
**Related:** [ADR-101](101-lum-442-commercialisation-ecosystem-model.md) §9, [ADR-102](102-repo-topology-cloud-services-boundary.md), LUM-260 exploration

## Context

Commercial packaging splits graph access into **Coding Graph** (`coding_graph`) and **Household Graph** (`household_graph`) on the ADR-101 JWS entitlement rail. Purchases must produce offline-verifiable licences without placing signing keys in `lumogis-app`.

## Decision

Ship the **lumogis-cloud entitlement issuer** Worker:

| Route | Purpose |
| --- | --- |
| `POST /stripe/webhook` | `checkout.session.completed` → issue JWT (Ed25519 JWS) |
| `POST /api/license/issue` | Admin/manual issue (`ADMIN_API_TOKEN`) |
| `GET /api/license/validate` | Online revocation + expiry (no `instance_id` in public response) |
| `POST /api/license/revoke` | Admin revoke by `jti` |
| `GET /.well-known/jwks.json` | Public verify keys |

**JWT claims (v1):** `jti`, `instance_id`, `plan: one_off`, `entitlements[]` (`coding_graph` \| `household_graph`), `iat`, `exp`, `kid`.

**Stripe:** two Price IDs → one entitlement each; unknown price / multi-line-item → `stripe_event_failures` + HTTP 200 (no retry storm); API/signing failures → 500.

**Storage:** D1 (`issued_licences`, `revoked_jti`, `customer_instances`, `stripe_event_failures`); KV idempotency (7d TTL) + D1 `stripe_event_id` UNIQUE backstop.

Private signing material stays in Wrangler secrets only (ADR-102).

## Out of scope (this ADR)

- LUM-262 runtime gates in `lumogis-graph`
- LUM-96 Core offline verifier in `lumogis-app`
- LUM-271/LUM-265 storefront / Stripe product creation
- LUM-510 production key ceremony

## Consequences

**Easier:** Frozen `coding_graph` / `household_graph` claim enum; consumers can implement offline verify via JWKS.

**Harder:** Dual purchase = two JWTs; LUM-262 must union entitlements (FP-LUM-263-06).

## Status history

- 2026-06-25: Finalised by /verify-plan — implementation in `lumogis-cloud` @ main; 27 issuer vitest + monorepo green.
