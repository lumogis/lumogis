# ADR-190: OAuth2 provider connect (Gmail + Outlook) — unlocks Outlook
**Status:** Finalised
**Created:** 2026-07-24
**Last updated:** 2026-07-24
**Decided by:** /explore
**Finalised copy:** docs/decisions/190-oauth2-provider-connect.md

> **Finalisation note (674b / LUM-687):** implemented with **one scoped deviation
> from "Providers v1 (D)"** — **Gmail OAuth is NOT in v1**. Google's Device
> Authorization Grant is documented-incompatible with the `https://mail.google.com/`
> IMAP scope (returns `verification_url` not `_uri`; requires a `client_secret` at
> the token poll, which the no-secret public-client model of decision A1 does not
> supply; and excludes `mail.google.com` from the device-flow scope allowlist — an
> `invalid_scope` at *start*). So **v1 OAuth = Outlook (Microsoft, `/consumers/`
> personal accounts) only**; Gmail stays app-password and Gmail OAuth moves to the
> auth-code + loopback (RFC 8252) follow-up (which supports the secret + restricted
> scope). This tightens — not reverses — the decision: BYO client (A1), device flow
> (B1), and the Fernet token/transport wiring (C) all stand as decided; only the
> Gmail *device-flow* provider was found un-shippable and deferred.

## Context
Setup v2 (ADR-182) shipped app-password IMAP and reserved-but-rejected `auth_type="xoauth2"`; Outlook ships shown-but-disabled because Microsoft retired Basic Auth. This chunk is the deferred OAuth path. The two hard decisions for a **self-hosted** app are (1) where OAuth client credentials come from, and (2) how authorization completes with **no stable public callback URL**. A ticket-title correction: **iCloud has no OAuth2 for IMAP** (app-specific passwords only), so the effective OAuth targets are **Gmail + Outlook**.

## Decision
Ship OAuth2 email connect in **core** with:
- **Bring-your-own OAuth app (A1):** the operator registers their own Google/Microsoft app and sets an installation-wide `client_id` via env (device-flow public clients need no secret). This is effectively **forced** for Gmail — its `https://mail.google.com/` is a *restricted* scope requiring an annual CASA Tier-2 assessment for a *published* client, whose "data on a server" compliance model does not fit a self-hosted publisher. BYO keeps no shared secret in the AGPL binary and matches the self-hosted ethos.
- **Device Authorization Grant (B1):** Core requests a device code, the web shows "visit {uri} + enter {code}", Core polls the token endpoint. **No redirect URI** — the only flow that works on a LAN/headless household server. Google + Microsoft both support it for the IMAP scopes; `offline_access` yields the refresh token.
- **Token/transport wiring (C):** tokens ride the existing Fernet-encrypted `user_connector_credentials` store as the reserved `xoauth2` payload; un-gate `xoauth2` at load + wire; a token service refreshes the ~1 h access token on demand; IMAP branches to `imapclient.oauth2_login`, SMTP to `AUTH XOAUTH2`; the provider OAuth hosts join the `build_allowlist` egress choke point + ceiling.
- **Providers v1 (D):** Gmail + Outlook OAuth (Outlook promoted to enabled); **iCloud stays app-password** (no OAuth2 IMAP); Fastmail stays app-password (optional later).

Recommend **splitting** at plan time: **674a** (provider-agnostic `xoauth2` credential + IMAP/SMTP + token-refresh + egress wiring, fake-endpoint tests) → **674b** (device-flow service + Google/MS descriptors + `email/oauth/*` routes + Outlook promotion + web device-code panel), with a **PoC spike** against real Gmail + Outlook before 674b and a CI-gated live-prove child.

## Alternatives Considered
- **Shared Lumogis-published client (Thunderbird model)** — Google restricted-scope CASA/verification is untenable for a small self-hosted AGPL publisher; rejected for Gmail (a shared MS-only client is technically possible but adds a confusing second model + project liability).
- **Loopback (RFC 8252) / operator-HTTPS redirect** — assume a callback a household LAN server doesn't have; loopback rejected for v1, redirect a later advanced opt-in.
- **Client-credentials grant** — tenant-admin app access, not personal mailboxes.
- **iCloud/Apple OAuth** — no consumer OAuth2 IMAP exists; app-password only.
- **New secrets vault** — the existing Fernet store already fits the token shape.
- Full detail: `cursor/explorations/LUM-674-oauth2-provider-connect.md`.

## Consequences
**Easier:** unlocks Outlook; reuses the existing encrypted credential store, the single IMAP/SMTP auth branch points, and the `build_allowlist` egress choke point; no shared secret in the binary; deployment-independent (no public callback).
**Harder / foreclosed:** commits self-hosters to registering their own OAuth app (guided doc mitigates); token refresh is on-demand (no scheduler v1); redirect/auth-code flow deferred; the live provider path can't be sandbox-verified (PoC + CI-gated e2e).

## Revisit conditions
- **BYO registration friction proves too high** → revisit a shared MS client and/or dynamic client registration.
- **An operator wants browser SSO at a real domain** → add the auth-code + HTTPS-redirect flow (B3).
- **A provider changes device-flow/scopes** (esp. personal Outlook.com IMAP) → revisit per-provider handling; the PoC is the early-warning.

## Status history
- 2026-07-24: Draft created by /explore (LUM-674).
- 2026-07-24: **674a implemented** (LUM-686) — the provider-agnostic credential/transport/token-refresh wiring (C from the decision) landed and verified (`/verify-plan`), all against fake endpoints. ADR stays **Draft**: the full decision (BYO client A1 + device-flow B1 + providers D) is only half-implemented; it finalises when **674b (LUM-687)** lands the device-authorization flow, the Google/Outlook descriptors, the `email/oauth/*` routes, Outlook promotion, and the web device-code panel (with a live-prove PoC first). Plan: `cursor/plans/archived/LUM-686-oauth2-xoauth2-wiring.plan.md`.
- 2026-07-24: **Finalised by /verify-plan — 674b implemented (LUM-687)**, completing the decision. Shipped the Device Authorization Grant state machine (`email_oauth` — owner-scoped in-process `_FLOWS` TTL store; device_code + refresh_token never reach the browser), the two `/me/email-sources/oauth/{provider}/device/{start,poll}` routes (live IMAP-verify → credential-first persist, 3-branch soft-save on proxied deployments), the Microsoft provider descriptor + dynamic Outlook gating, and the web device-code panel. **Deviation recorded (see Finalisation note):** Gmail OAuth dropped from v1 (device-flow / restricted-scope incompatibility) → **Outlook is the only v1 OAuth provider**; Gmail OAuth deferred to auth-code + loopback. All mock-tested; live-proof is CI-gated **LUM-688**. Plan: `cursor/plans/archived/LUM-687-oauth2-device-flow-providers-web.plan.md`.
