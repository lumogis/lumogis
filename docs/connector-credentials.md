# Connector credentials

Lumogis stores per-user credentials for external services
(ntfy, future calendar/IMAP/etc.) in an encrypted Postgres table
called `user_connector_credentials`. This page is the practical
operator/user reference for the dashboard UI and the matching HTTP
APIs.

For the underlying design rationale see
[`docs/decisions/018-per-user-connector-credentials.md`](decisions/018-per-user-connector-credentials.md)
and
[`docs/decisions/019-structured-audit-logging.md`](decisions/019-structured-audit-logging.md).

## What you'll see in the dashboard

After signing in to the Lumogis Web dashboard, expand the
**Connector credentials** tile (just below **MCP tokens**). The tile
lists every credential row stored for the signed-in user, with two
actions per row:

* **Replace** — opens a modal to overwrite the encrypted JSON
  payload. The plaintext is never re-displayed by the server, so
  paste the full new payload.
* **Delete** — removes the row entirely. A confirmation dialog
  names both the connector and the target user, e.g. *Delete
  credential 'ntfy' for alice@home.lan?*.

Above the list, **Add or replace credential** opens the same modal
for a fresh row. A connector dropdown is populated from the
server-side registry so you can only create credentials for
connectors Lumogis actually knows how to use; if the registry
endpoint is unavailable, the modal falls back to a free-text
connector field with the explanation
*"Connector list unavailable right now. Enter the connector id
manually."*

### Admin "manage on behalf of"

If you signed in as an admin, the tile shows an extra **Manage on
behalf of** picker populated from `GET /api/v1/admin/users`. Pick a
user to switch every CRUD call onto the admin prefix
(`/api/v1/admin/users/{user_id}/connector-credentials`); the audit
trail captures `actor=admin:<your-id>` so operator interventions
are distinguishable from self-service writes. An amber acting
banner ("*Acting as: alice@home.lan — actions audited as
admin:&lt;your-id&gt;*") stays visible until you switch back to
**Myself**.

Switching the picker mid-modal automatically force-closes and
resets the Add/Replace dialog so a payload typed for one user can
never be saved against another.

## Security posture

* **Plaintext is never re-shown.** The server stores ciphertext
  (Fernet, key from `LUMOGIS_CREDENTIAL_KEY[S]`) and a metadata
  fingerprint (`key_version`); there is no GET endpoint that
  returns the decrypted payload. To rotate or fix a credential,
  paste the full new JSON payload via Replace.
* **Per-user isolation.** Rows are keyed by `(user_id, connector)`.
  The user-facing endpoints scope every read/write to the calling
  user; only admins can read/write rows owned by other users, and
  every such call is audited.
* **No secret-reveal endpoint.** The dashboard exposes metadata
  only (created_at, updated_at, created_by, updated_by, and an
  admin-only `key_version`). Operators that need plaintext for
  emergency recovery should run the rotation script (see below).
* **Audit logs.** Every PUT and DELETE writes a row to `audit_log`
  with `action_name` `__connector_credential__.put` /
  `.deleted`, the actor (`self` or `admin:<id>`), and the
  `key_version` at the time of the write. Diagnostics GETs
  (the registry list and the admin fingerprint endpoint) are
  intentionally **not** audited, mirroring other read-only admin
  GETs in the codebase.

## Stale rows ("unregistered connector")

If a connector id is removed from the registry but rows still
exist in the table, the dashboard surfaces them with a red
**unregistered connector** badge. **Replace** is disabled for
stale rows (Lumogis will not encrypt new payloads for unknown
connector ids), but **Delete** still works so operators can clean
up after a connector deprecation. PUT requests against stale ids
return `422 unknown_connector`.

## Key rotation diagnostic (admin only)

The admin view of the tile shows two extra pieces of information:

* A footer line summarising the current key fingerprint and a
  per-`key_version` row count, e.g.
  *current key#1234567890 — key#1234567890: 4   key#3735928559: 2*.
* Per-row badges labelling each row as **current key** or
  **older key — re-encrypt via rotation script**.

The data comes from
`GET /api/v1/admin/diagnostics/credential-key-fingerprint` and is
also useful from the command line:

```bash
curl -s -H "Authorization: Bearer $ADMIN_BEARER" \
  https://lumogis.example/api/v1/admin/diagnostics/credential-key-fingerprint
# {
#   "current_key_version": 1234567890,
#   "rows_by_key_version": { "1234567890": 4, "3735928559": 2 }
# }
```

This endpoint never returns ciphertext, plaintext, or key bytes —
only the integer fingerprints (`SHA256(key)[:4]` as an unsigned
32-bit int) and counts.

To actually re-encrypt older rows to the current primary key,
operators run the dedicated rotation script:

```bash
python -m scripts.rotate_credential_key
# rotated=2 skipped=4 failed=0
```

`LUMOGIS_CREDENTIAL_KEYS` (CSV, newest first) is the rotation
substrate: prepend a freshly-generated key, restart the
orchestrator, then run the script. See
`docs/decisions/018-per-user-connector-credentials.md` §"Operator
key rotation" for the full sequence.

## HTTP API quick reference

All routes require an authenticated session; mutating verbs
additionally require either a same-origin browser request OR a
bearer token (CSRF rules in `orchestrator/csrf.py` apply).

### User-facing — `/api/v1/me/connector-credentials`

| Verb     | Path             | Notes |
|----------|------------------|-------|
| `GET`    | `""`             | List my rows (metadata only). |
| `GET`    | `/registry`      | List registered connector ids + descriptions. |
| `GET`    | `/{connector}`   | Read one row's metadata. |
| `PUT`    | `/{connector}`   | Create or replace; body is `{"payload": {...}}`. |
| `DELETE` | `/{connector}`   | Remove the row (idempotent: 404 when missing). |

### Admin — `/api/v1/admin/users/{user_id}/connector-credentials`

Same four CRUD verbs as above, but operating on another user's
rows. `actor` becomes `admin:<caller-id>` so the audit trail
distinguishes operator interventions from self-service.

### Admin diagnostics — `/api/v1/admin/diagnostics`

| Verb  | Path                                | Notes |
|-------|-------------------------------------|-------|
| `GET` | `/credential-key-fingerprint`       | Current key fingerprint + per-`key_version` row counts. |

## Error codes

Server responses use a small, frozen vocabulary inside the
`detail.code` field. The dashboard maps them to friendly messages;
clients should do the same.

| Code                        | HTTP | Meaning |
|-----------------------------|------|---------|
| `bad_connector_id`          | 400  | Connector id failed format check (`^[a-z0-9_]{1,64}$`). |
| `unknown_connector`         | 422  | Format is fine but the connector is not in the canonical `CONNECTORS` mapping in `orchestrator/connectors/registry.py`. PUT only. |
| `connector_not_configured`  | 404  | No row exists for `(user_id, connector)`. |
| `credential_unavailable`    | 503  | The server can't seal/decrypt right now (key issue). Operator-actionable. |
| `user_not_found`            | 404  | Admin route given a `user_id` that doesn't exist. |

PUT bodies that fail Pydantic validation (non-object payload,
empty payload, payload >64 KiB) come back as standard FastAPI 422
responses; the dashboard surfaces a generic *"object required,
non-empty, ≤ 64 KiB"* message in that case.

## Out of scope

Not addressed here (and intentionally not in the dashboard):

* No secret-reveal / "show plaintext" endpoint.
* No HTTP rotation trigger — rotation stays operator-driven via
  `python -m scripts.rotate_credential_key`.
* No shared/system credentials — every row is owned by exactly one
  user.
