# Secure address at home (`services/home_dns`)

Admin → Secure address gives this Lumogis a free `https://<id>.homes.lumogis.app`
name for the home network (LUM-331, ADR 115). The name's public DNS A record
points at the computer's private home-network address; Caddy gets and renews a
Let's Encrypt certificate for it with a DNS-01 challenge through Lumogis's name
service. Nothing here runs until an admin turns it on.

## Modules

- **`validate.py`** — every value that reaches DNS, Caddy config or the CSRF
  check is validated here with `fullmatch`: household id (26 base32 characters),
  bearer, name-service URL, home-network IPv4 (RFC 1918 only), and
  `is_secure_zone_host`, which `csrf.py` uses on every write request (so the
  package `__init__` stays empty and cheap to import).
- **`snippet.py`** — pure: renders the household's Caddy site block
  (`tls { dns acmedns {…} }` + `import lumogis_routes`), refusing any invalid
  input, so a brace or newline can never become server configuration.
- **`state.py`** — `state.json` (`0600` in a `0700` directory under the
  workspace, so DR backups carry it) and `derive`, the pure table that turns the
  saved state into what the page shows (`off`, `needs_address`, `registering`,
  `issuing`, `ready`, `router_blocked`, `error` + code). Sticky errors stop
  progress until the admin acts; progress errors only overlay.
- **`worker_client.py`** — the name-service client: Ed25519-signed
  `POST /register` (a fresh bearer each time, the same name for the same device
  key), `PUT /a`, `GET /budget`, `DELETE /register`, with HTTP errors mapped to
  the page's error codes. `DEFAULT_REGISTRATION_SECRET` is a public anti-spam
  value, not a credential (ADR 115 / ADR 145): it ships in this source, the
  name service accepts a list of values, and a rotation is a Core release.
  `LUMOGIS_HOME_DNS_REGISTRATION_SECRET` overrides it for a self-hosted name
  service.
- **`service.py`** — the state machine. Admin actions (`enable`,
  `set_lan_address`, `retry`, `disable`) run under `route_lock` and bump a
  **generation**; background work (`_advance`, the 10-minute `refresh_job`, the
  30-second job while issuing) commits only if the generation is unchanged, so
  an admin's Turn off always wins over a reply that arrives later. The Caddy
  block is written to `home.caddy.pending`, validated by stack-control, then
  placed under the lock (re-checking the generation) and reloaded. Network and
  Caddy calls never run while `_lock` is held; `_progress_lock` keeps the jobs
  and a route's background task from running the sequence twice at once.

**Detection and re-apply (LUM-996).** `detect_now` (`POST …/detect`) runs the
host-address helper on demand, one at a time, and only records the result;
`GET` stays read-only. The site block's sha256 is kept as `snippet_fingerprint`;
step 5b of the 10-minute job re-renders the block and, when the CA, name-service
URL, port or template changed, applies it in place (a refused or failed re-apply
keeps the working block). A one-off run 60 s after boot catches a change made
while Core was down. Re-create Core with `docker compose up -d` to pick up a
changed `.env` value; a plain `restart` keeps the old environment.

**Why "the name is new" is time-based.** A resolver can cache "no such name"
from before the A record existed, and rebind filters answer differently (a
Fritz!Box times out, unbound strips or SERVFAILs, dnsmasq discards), so a
verdict cannot tell a cached miss from a router that hides the name. For 35 min
after the first A-record write (`name_new_until`, anchored on `registered_at` /
`name_published_at`) a device failure reads "the name is new", and a resolver
verdict taken inside that window never becomes `router_blocked`.

**CA and staging.** Let's Encrypt staging certificates cannot be tested in a
browser: every `.app` name is HSTS-preloaded. Check with `curl -k` or `openssl`.

**Where the bearer lives:** `state.json` (`0600`), the Caddy site in the
`caddy-home` volume (`0640`), and Caddy's own autosave of its running config
(`/config/caddy/autosave.json` in the `caddy_config` volume, root `0600`). It is
never returned by an API, logged, or written to audit rows.

## Name-service contract (summary)

| Call | Auth | Result |
| --- | --- | --- |
| `POST /register` `{pubkey, ts, sig, registration_secret}` | Ed25519 proof of the device key over `register\|{pubkey}\|{ts}` (±300 s) | `201 {household_id, name, token}` |
| `PUT /a` `{ip}` | Bearer | `200`; RFC 1918 only, else `400 bad_input` |
| `GET /budget` | Bearer | `{first_cert_needed, available, scope, retry_after_seconds}` |
| `POST /update` (acme-dns compatible, used by Caddy) | acme-dns headers: `X-Api-User` = household id, `X-Api-Key` = bearer | sets the `_acme-challenge` TXT |
| `DELETE /register` | Bearer | `204`; removes the records, keeps the name for the same key |

Errors: `403` refused, `410` retired, `429` rate limited (`Retry-After`), `5xx`
unreachable or full. A `Date` header more than 120 s off this computer's clock is
reported as `clock_skew`.
