# ADR-145: lumogis-cloud home-dns credential hardening + LUM-508 deploy CI

**Status:** Finalised

**Created:** 2026-06-29

**Last updated:** 2026-06-29

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-29 (Composer)

**Plan:** none — shipped on `claude/persona-c-household-launch-ly74ed` without a separate verify-plan for this slice

**Exploration:** `.cursor/explorations/lumogis_cloud_home_dns_hardening_lum508_ci_retro.md`

**Draft mirror:** `.cursor/adrs/lumogis_cloud_home_dns_hardening_lum508_ci.md`

**Builds on:** [ADR-115](115-lum-521-operated-household-cert-dns01.md) (LUM-521 Worker), [ADR-102](102-repo-topology-cloud-services-boundary.md) (lumogis-cloud boundary)

**Linear:** [LUM-521](https://linear.app/lumogis/issue/LUM-521), [LUM-508](https://linear.app/lumogis/issue/LUM-508/bootstrap-lumogis-cloud-operated-services-repo-relay-entitlement)

## Context

After LUM-521 landed the home-dns Worker on `lumogis-cloud` @ `86bceef`, a persona-c review pass (`318478a`) and a CI completion commit (`be00f0f`) on `claude/persona-c-household-launch-ly74ed` hardened credential rotation against KV partial-failure lockout and finished the **LUM-508** deploy-workflow slice (concurrency + README contract). This retro records that as-built state before LUM-473 Chunk B consumes the Worker API.

## Decision

**Home-dns credential paths (extends ADR-115):**

- **`POST /register`** and **`POST /rotate`** persist the **new** bearer reverse-index (`tok:`) and household record **before** revoking the old index — a single KV failure mid-update must not strand the household without a working bearer during ACME renewal.
- **`registrationSecretOk`** uses **constant-time** comparison (anti-spam boundary, consistency with bearer/PoP paths).
- **Bearer middleware** rejects tokens longer than **256** characters before hashing.
- **`POST /rotate`** requires decoded Ed25519 pubkey length **32** bytes.

**LUM-508 deploy CI (partial — relay/registry still design-gated):**

- Path-filtered GitHub Actions workflows per service run **typecheck + vitest** on PR and on `main` push; **`wrangler deploy`** runs only on **`main`** push after tests pass.
- **`concurrency`** groups per service+ref cancel stale PR jobs but **do not** cancel in-flight **`main`** deploys.
- **Secrets:** runtime Worker secrets via **`wrangler secret put`**; CI uses **`CLOUDFLARE_API_TOKEN`** in GitHub Actions only.

**Repo evidence:** `github.com/lumogis/lumogis-cloud` **`main` @ `5a54258`** (2026-06-29).

## Alternatives considered

- **Delete-old-bearer-first rotation** — rejected: KV partial failure locks household out (review C-1/C-2).
- **Plain string compare for registration secret** — rejected for consistency with other constant-time paths.
- **Cancel-in-progress main deploys on rapid merges** — rejected: risks half-deployed Worker revision.

## Consequences

- **Easier:** Safer credential lifecycle for box-side LUM-473 consumer; CI behaviour documented and guarded against double-deploy races.
- **Harder / cost:** May leave harmless dangling `tok:` entries until GC; KV register throttle remains eventually consistent (documented).
- **Future chunks must know:** Operator LE-staging proof (RUNBOOK §6) and LUM-522/LUM-523 provisioning still block production; entitlement-issuer business logic remains LUM-263.

## Testing retrospective

- **`npm test`** @ repo root: **29/29** (home-dns **25**, entitlement-issuer **4**); **`npm run typecheck`** clean (2026-06-29, pre-merge on `claude/persona-c-household-launch-ly74ed`).

## Revisit conditions

- Adopt Cloudflare Rate Limiting binding if KV throttle abuse appears in production.
- Split concurrency groups if staging/prod Workers deploy from the same workflow.

## Status history

- 2026-06-29: Finalised by `/record-retro` — merged `claude/persona-c-household-launch-ly74ed` → `lumogis-cloud` `main` @ `5a54258`.
