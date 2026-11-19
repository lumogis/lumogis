# ADR-273: Sign-in on by default: the first person to open Lumogis creates the admin account (LUM-981)
**Status:** Finalised (revised: implementation deviations recorded in "As built")
**Created:** 2026-11-17
**Last updated:** 2026-11-17
**Decided by:** /explore LUM-981 (Claude Code); owner decisions Thomas 2026-11-17 (open claim with a claim-now warning, the phone fix in scope, one Web UI for one person and a family)
**Finalised by:** /verify-plan 2026-11-17 (Claude Code)
**Plan:** `.cursor/plans/LUM-981-first-admin-setup.plan.md`
**Draft mirror:** `.cursor/adrs/LUM-981-first-admin-setup.md`
**Supersedes:** ADR 247's rejection of "sign-in on by default" and its Web decisions 1, 2 and 6 (see Decision 7)
**Exploration:** `.cursor/explorations/LUM-981-first-admin-setup.md`
**Linear:** LUM-981

## Context

A default install runs with sign-in off (ADR 247). Anyone who can reach its address uses Lumogis as its admin. LUM-980 makes the install two files and `docker compose up -d`, so new households will rarely read `.env`.

ADR 247 rejected sign-in on by default on 2026-11-09 for two reasons:
1. It needed a credential generated and shown at first start.
2. The single-user Server had no household to sign in to.

A first-visitor setup page removes the first reason. The Server pause (2026-11-14) removes the second.

Today, turning sign-in on means typing an admin email and password, `AUTH_SECRET`, a distinct refresh secret and a Fernet credential key into `.env`. Core refuses to boot without each of them (`main.py:314-411`). Also, a phone that opens Lumogis at `http://<lan-ip>` is refused by the CSRF origin pin (`LUMOGIS_PUBLIC_ORIGIN=http://localhost`), and it loses the `Secure` refresh cookie over plain HTTP.

## Decision

1. **New installs get sign-in on through the templates, not the code.** The changes:
   - `docker/release/example.env` and `.env.example` ship `AUTH_ENABLED=true`.
   - `auth_enabled()` keeps "unset means off", and the test default is unchanged.
   - Existing installs keep whatever their `.env` says.
2. **The first visitor creates the admin, the Immich way, whenever no user exists** (owner decision 2026-11-17; arbitration R1):
   - With sign-in on, no users and no bootstrap env, Core boots into a setup state instead of refusing. An install that already holds `user_id='default'` rows also opens. Those rows move to the new admin when the claim's remap runs, as they would for a bootstrap admin (Decision 5). A "holds data" refusal was dropped: it also counted audit, action and session rows written while setup waits, so a restart before the claim would have bricked a fresh install.
   - Boot refuses only when migration 085 is missing, when setup was completed but no user remains, or when the bootstrap env is set but produced no user.
   - Lumogis Web learns the state from an unauthenticated `GET /api/v1/setup/status`, which reads a process flag only, and shows "Create the admin account". While Core is unreachable (first start pulls models for minutes), the Web says "still starting" and asks again, instead of showing a sign-in form for an install with no accounts.
   - `POST /api/v1/setup/claim` takes the email and password. It is rate-limited and same-origin, and it is accepted only for a private `Host` (Decision 9).
   - It runs in one transaction: an advisory lock; a re-check of the `instance_setup` row and the user count; the admin user; then the singleton row (`id = 1`, `INSERT … ON CONFLICT DO NOTHING RETURNING`), rolling back when no row comes back. The user comes first because the row references it.
   - It returns 409 once claimed, and 403 `setup_host_not_allowed` for a public `Host`. It mints a session the way login and invite redemption do (one shared helper).
   - Setup completion is that durable row. Deleting the admin never reopens setup.
   - `GET /api/v1/auth/me` is unchanged: 401 with sign-in on, the synthetic admin with it off.
   - **A claim-now warning** tells the installer to create the account straight after `docker compose up -d`, and what to remove if someone beat them to it. It appears in the quickstart, the env templates, a repeating orchestrator log banner while setup is open, and on the setup page.
3. **`LUMOGIS_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD[_FILE]` stay as an optional override.** When any of them is set, setup never opens: a complete set creates the admin at start, and an incomplete set refuses to boot. This covers CI, scripted installs and the native Server.
4. **Secrets are generated on first start into `.env`.** The entrypoint:
   - generates `AUTH_SECRET` (with sign-in on) and `LUMOGIS_CREDENTIAL_KEY` (in both modes, unless `LUMOGIS_CREDENTIAL_KEYS` is set) when each is missing or a placeholder;
   - keeps today's LUM-885 behaviour for the refresh secret: it writes `JWT_REFRESH_SECRET`, and a set `LUMOGIS_JWT_REFRESH_SECRET` still wins;
   - writes each value to the bind-mounted `.env`, exports it for the current run, and re-exports values already in `.env` on a restart. Other services read `.env` only when Compose creates them, so they get generated values after the next `docker compose up -d`, and the entrypoint says so;
   - never replaces a value that is already real.

   When stored credentials exist but no loaded key matches their key version, Core **warns** at boot (it does not refuse): the household re-enters those passwords, or puts the old key back. Losing saved passwords is a bounded loss; refusing to boot would be a total outage.
5. **The default-user remap gains one target tier** (ADR 152): after `INBOX_OWNER_USER_ID` and `LUMOGIS_BOOTSTRAP_ADMIN_EMAIL`, the admin recorded in `instance_setup`. The claim runs the post-bootstrap remap in the background, so `default` rows move at once.
6. **Plain-HTTP LAN access works without an origin setting** (owner: in scope, 2026-11-17):
   - When `LUMOGIS_PUBLIC_ORIGIN` is unset, the CSRF check requires a present `Origin` to equal the request's scheme plus its `Host` (default port dropped, any other port kept, a trailing `.` kept as the browser sends it). The scheme comes from `X-Forwarded-Proto` only from a `LUMOGIS_TRUSTED_PROXIES` peer. This generalises the LUM-331 secure-zone rule.
   - An `https` Origin for the same host and port is also accepted, because a TLS terminator in front of Caddy (Tailscale Serve, a Cloudflare Tunnel) makes Caddy report `http`. Only a page served over https from that exact host can carry it.
   - Caddy's own forwarded-header defaults are relied on, and must not be overridden: no `header_up Host` (it would strip the port), and no `header_up X-Forwarded-Proto` (it would override a trusted upstream).
   - The refresh cookie drops `Secure` **only** when a trusted proxy positively reports `http`, no pinned origin is `https://`, and the request's `Origin` is not `https://`. Any doubt keeps `Secure` (it fails closed).
   - Compose stops defaulting the origin to `http://localhost`. An explicit origin still wins.
   - Emailed links (password reset, system mail) still need a pinned origin. Invite links shown in Lumogis Web are made absolute by the browser.
7. **Lumogis Web has one UI for one person and for a family** (owner decision 2026-11-17). Single-user mode is removed from the Web: the `single_user` boot state, the "sign-in is off" banner, and the hidden Sign out, Change password and Admin → Users controls. `AUTH_ENABLED=false` remains a developer and test setting for Core only. With it, Lumogis Web shows the sign-in form with "sign-in is turned off on this server". The unauthenticated `/auth/me` answer is unchanged, because Lumogis Search depends on it.
8. **When no origin is pinned, the same-origin check accepts a request with no `Origin` header.** Browsers always send `Origin` on cross-site POSTs, and the refresh cookie is `SameSite=Strict`, so CSRF protection holds. When an `Origin` is present, it must equal the scheme plus `Host`. A pinned `LUMOGIS_PUBLIC_ORIGIN` is still compared exactly. Requests with only a `Referer` (some privacy extensions strip `Origin`) are allowed too; for them `SameSite=Strict` is the only CSRF guard.
9. **The claim accepts only a rebinding-safe `Host`** (self-review, arbitrations R1 and R2). A rebinding attack controls both `Host` and `Origin`, and the claim needs no cookie, so an origin check alone cannot stop a web page on an attacker's domain from claiming an install through a household member's browser.
   - **Allowed:**
     - any IP literal (rebinding cannot produce one; this is deliberately wider than the preflight's private-IP rule, and it is not an internet-claim defence);
     - a private name: `csrf.PRIVATE_NAME_SUFFIXES`, the same list as the Caddy preflight (`localhost` and `*.localhost`, `.local`, `.lan`, `.home.arpa`, `.internal`, `.test` and `.ts.net`, and single-label names), pinned both ways by a test;
     - the secure-address zone;
     - a pinned origin's host.
   - **Why the secure zone is safe:** the Home DNS Worker accepts RFC1918 targets only, so no attacker page can run under a zone name. If that ever changes, the zone leaves this list.

This supersedes ADR 247's rejection of "sign-in on by default", and its Web decisions 1, 2 and 6 (single-user mode, the banner, the "turn on sign-in" docs). Its backend parts stay as inert auth-off behaviour: the synthetic `/auth/me`, the onboarding `completed_at`, and the wow dismissal key. Its chat empty-library state (Decision 4) stays, because it is keyed on documents, not on the auth mode.

## Alternatives Considered

Full detail is in `.cursor/explorations/LUM-981-first-admin-setup.md`.

- **A one-time setup code (Jupyter, Authentik):** declined by the owner. A hijacked new install holds nothing; you delete it and start again. Kept as a Later option.
- **Refusing installs that already hold data** (arbitration R1): rejected. It also counted audit, action and session rows written while setup waits, so a restart before the claim would brick a fresh install. Accepted cost instead: on such an install, the first visitor inherits the data (owner: no such installs exist).
- **A setup time window (Portainer):** still first-come, and the install breaks when the window closes.
- **Claim only from the local network:** "local" cannot be detected reliably behind Caddy and Docker, and the bypass is spoofable (Sonarr).
- **A generated admin password in the logs (ADR 247 Path 2):** a credential in the logs plus a forced change. Kept only as the env override.
- **Secrets in a workspace file:** it would put the decryption key into the same backup as the ciphertext.
- **Secrets in a database row:** the key would sit next to the ciphertext it protects.
- **Changing the code default to "unset means on":** it flips existing checkout installs with no admin or secrets, and the whole test suite, for no gain.
- **Documenting the LAN origin and cookie settings instead of D1:** a zero-edit install would fail silently on phones.

## Consequences

**Easier:**
- A new install is two files, `up -d`, then opening the page and creating the admin. No secret is typed or copied.
- The onboarding modal (ADR 059) is the new-install first run again.
- Saving LLM keys and mail logins works on a stock install, because the credential key always exists.
- Phones on the home network can sign in over plain HTTP.

**Harder or closed off:**
- `.env` now holds generated secrets that the household must keep a copy of. This was already the advice for the credential key.
- With a re-copied `.env`, Core warns at boot that saved passwords cannot be read. Recovery is restoring the old key, or re-entering the passwords.
- Setup cannot be re-run after the admin is deleted. Recovery is the env override (boot says so).
- Until the admin is created, anyone who can reach a new install can create it. The warning, and the fact that a new install holds nothing, are the mitigation.
- An auth-off install with data that switches sign-in on opens setup, and its data goes to whoever claims it first (owner: no such installs exist).
- Problems that were "auth-on only" become default-install problems: LUM-960 (legacy pages), LUM-938 (`/related` scored only for `default`), LUM-959 (weekly review across members).
- With sign-in on, `/mcp` needs a per-user `lmcp_…` token from Lumogis Web.

**Future chunks must know:**
- Lumogis Web does not run with sign-in off. The paused Lumogis Server, which bundles the Web with sign-in off while sharing is off, must run sign-in on when it resumes (LUM-473).
- Never answer "needs setup" from `/auth/me`. Lumogis Search treats a 200 there as "sign-in off".
- `instance_setup` is the setup-complete marker. Do not infer it from `count_admins()`.
- Anything new that reads `AUTH_ENABLED` must keep "unset means off".

## Revisit conditions

- A takeover of an unclaimed install is reported, or scanners are seen probing `/api/v1/setup`. Add the setup code (A2).
- A browser starts keeping `Secure` cookies on plain-HTTP LAN addresses (Pass 0, 2026-11-17, showed Chromium drops them), or the derived origin breaks a proxy setup. Then re-decide Decision 6.
- The nightly backup becomes encrypted. Moving generated secrets into the backed-up workspace (B2) becomes attractive.
- Lumogis Server resumes. Decide whether its sharing switch uses the setup page instead of the password file (LUM-473).

## As built (2026-11-17)

Implementation confirmed the decisions above. These deviations are recorded here, so the record matches the code:

- **Migration `085-instance-setup.sql`**, not 084: LUM-965 and LUM-966 took 083 and 084 on the same day. The table is **not** in `postgres/init.sql`, because `users` is created by migration 010 and the foreign key would fail at initdb. Tables that reference `users` live only in migrations.
- **A trailing dot is kept** in the expected origin (`csrf._origin_for`). Browsers keep it in `Origin`, and stripping it, as the plan first said, refused every same-origin POST on such a host. Found by the implementation code review.
- **Any bootstrap setting means "bootstrap intended"** (`services.users.bootstrap_env_intended`, mirrored in the remap script). A password without an email no longer leaves setup open.
- **One shared remap wrapper** (`db_default_user_remap.run_post_bootstrap_safely`) serves the boot pass and the post-claim pass, and logs a non-zero exit on both.
- **Generated secrets reach other services after the next `docker compose up -d`.** The entrypoint prints that line whenever it generated a value. The optional graph service verifies tokens with `AUTH_SECRET`.
- **Evidence:**
  - Pass 0: a Chromium probe kept a `Secure` cookie at `http://localhost` and dropped it at a non-localhost name.
  - Pass 11, on a fresh checkout with an unedited `.env`:
    - secrets were generated in the same run;
    - 2 `default` rows appeared while setup waited, and a restart kept setup open;
    - the claim moved those rows to the new admin;
    - Playwright passed at 1280 px, 390 px and a LAN-style name;
    - the `curl` contract checks matched.
  - The live race tests passed (21 with the migration baseline).
  - Full details are in the plan's Implementation Log.

## Status history

- 2026-11-17: Finalised by /verify-plan. Implementation confirmed the decision; the deviations are recorded in "As built" (migration 085, no `init.sql` entry, trailing dot kept, any bootstrap setting closes setup, the shared remap wrapper, generated secrets reaching other services after the next `up`).
- 2026-11-17: Draft created by /explore LUM-981.
- 2026-11-17: Revised during /review-plan --arbitrate R2:
  - Caddy's forwarded-header defaults kept (no `header_up` lines; R1's explicit lines stripped the port and overrode trusted upstreams);
  - a same-host `https` Origin is accepted (TLS terminators in front of Caddy);
  - the cookie stays `Secure` for a pinned `https://` origin or an `https` Origin;
  - the Web waits while Core is still starting;
  - Decision 9 renamed to rebinding-safe hosts, with the secure-zone rationale;
  - the "data" alternative reworded as an accepted cost.
- 2026-11-17: Revised during /review-plan --arbitrate R1:
  - dropped the "holds data" refusal (it would brick a restart before the claim), so setup opens whenever no user exists and the claim's remap moves `default` rows;
  - the remap gains the setup-admin tier;
  - the credential-key mismatch warns instead of refusing;
  - the `Secure` flag fails closed;
  - added the private-`Host` rule for the claim (Decision 9);
  - corrected the transaction order and the refresh-secret name;
  - recorded the email-link and invite-link consequences of removing the origin default.
- 2026-11-17: Revised by /create-plan: single-user mode removed from Lumogis Web (owner: one UI for one person and a family); no-`Origin` requests allowed when no origin is pinned.
- 2026-11-17: Revised after owner decisions: open claim (not a setup code) on empty installs only, a claim-now warning, the credential key generated in both modes, the phone fix in scope; installs with data keep the env bootstrap.
