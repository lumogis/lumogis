# Per-user export archive format

Reference for the ZIP archive produced by `POST /api/v1/me/export` and
consumed by `POST /api/v1/admin/user-imports`. Owned by the per-user
backup export plan; this document is the wire-level contract.

## Top-level layout

```
manifest.json                      ← required, parsed first
users/<exporting_user_id>.json     ← required, credentials redacted
postgres/<table>.json              ← one per user-scoped Postgres table
qdrant/<collection>.json           ← one per Qdrant collection (vectors omitted)
falkordb/nodes.json                ← FalkorDB nodes the user authored
falkordb/edges.json                ← FalkorDB intra-user edges
```

Every entry lives at a relative path; absolute paths, Windows drive
prefixes, NUL bytes, and any path containing `..` are rejected at
import time as `archive_unsafe_entry_names`.

## `manifest.json`

```json
{
  "format_version": 1,
  "exported_at": "2026-04-18T10:00:00+00:00",
  "exporting_user_id": "u_a1b2c3...",
  "exported_user_email": "alice@example.com",
  "exported_user_role": "user",
  "scope_filter": "authored_by_me",
  "falkordb_edge_policy": "personal_intra_user_authored",
  "sections": [
    {"name": "postgres/notes.json", "kind": "postgres", "row_count": 42},
    {"name": "qdrant/documents.json", "kind": "qdrant", "row_count": 17},
    {"name": "falkordb/nodes.json", "kind": "falkordb", "row_count": 9},
    {"name": "users/u_a1b2c3.json", "kind": "user_record", "row_count": 1}
  ],
  "falkordb_external_edge_count": 0,
  "warnings": []
}
```

`format_version` is `1` for this release. The orchestrator refuses to
import archives carrying any other version (`unsupported_format_version`).

`scope_filter` is always `authored_by_me` in v1: every section contains
only rows the exporting user authored, where authorship is
`(scope IN ('personal','shared') AND user_id = $me)` for tables with a
`scope` column and `user_id = $me` otherwise. Shared rows authored by
another user are intentionally excluded — they belong to that other
user's export.

`falkordb_edge_policy` is always `personal_intra_user_authored`: edges
where either endpoint belongs to a different user are recorded only as
a count in `falkordb_external_edge_count`, never serialised.

## Credential redaction (D5)

`users/<exporting_user_id>.json` contains the row from the `users`
table with every credential-shaped column blanked to `null`:

* `password_hash`
* `refresh_token_jti`
* anything else ending in `_secret`, `_token`, `_credential`, or `_jti`

The destination instance mints a fresh password during import via
`NewUserSpec.password`; the source-instance credential never leaves
the originating database.

## Per-table format

Each `postgres/<table>.json` is a JSON array of row dicts as Postgres
returned them — column names verbatim, values JSON-encoded with
`default=str` (timestamps as ISO-8601 strings, UUIDs as plain strings,
JSONB as parsed objects).

The hard-coded allowlist of per-user tables lives in
`orchestrator/services/user_export.py::_USER_EXPORT_TABLES`. The
regression test
`orchestrator/tests/test_user_export_tables_exhaustive.py` fails when a
new migration adds a `user_id`-bearing table without listing it.

## UUID PK collision handling (D4 + arbitration F4)

UUID primary keys are preserved across the round-trip. Two tiers:

* **Parent tables** (`entities`, `sessions`, `notes`, `audio_memos`,
  `signals`, `sources`, `deduplication_runs`) — a single existing UUID
  in the destination database refuses the entire import with
  `409 uuid_collision_on_parent_table`. Parent tables are FK targets;
  silently skipping a parent row would leave child rows orphaned or
  cascade-dropped.
* **Leaf tables** — collisions are recorded under
  `ImportReceipt.leaf_pk_collisions_per_table` and skipped via
  `INSERT … ON CONFLICT DO NOTHING`. SERIAL/BIGSERIAL `id` columns are
  stripped before insert so Postgres allocates fresh sequence values.

## Qdrant + FalkorDB

Qdrant points are exported with their payload only (no vectors). The
import path re-embeds `documents` payloads via the configured embedder
and writes a zero-vector for every other collection or any document
where embedding fails (counted as
`ImportReceipt.qdrant_zero_vector_count` so operators can re-embed
later).

FalkorDB nodes are MERGEd by `(labels, lumogis_id)` with `user_id`
re-pointed to the freshly minted user. v1 does **not** restore edges —
operators are expected to re-derive the graph by re-ingesting; the
receipt carries a `falkordb_edges_not_restored` warning when edge
data was present.

## Refusal reason → HTTP status

| `refusal_reason`                    | HTTP |
| ----------------------------------- | ---- |
| `archive_too_large`                 | 413  |
| `archive_integrity_failed`          | 400  |
| `archive_unsafe_entry_names`        | 400  |
| `manifest_invalid`                  | 400  |
| `missing_user_record`               | 400  |
| `manifest_section_count_mismatch`   | 400  |
| `missing_sections`                  | 400  |
| `unsupported_format_version`        | 400  |
| `forbidden_path`                    | 403  |
| `email_exists`                      | 409  |
| `uuid_collision_on_parent_table`    | 409  |

The mapping is canonical in `orchestrator/routes/admin_users.py::_REFUSAL_TO_STATUS`.

Refusals always carry a structured detail body of the form
`{"refusal_reason": "...", "payload": {...}}` so operators can grep on
`refusal_reason` without parsing free text.

## Audit lifecycle (refused vs failed)

The import service distinguishes two failure-shaped outcomes that
operators care about for very different reasons:

| Audit `action_name`                          | Stage              | What it means                                                                |
| -------------------------------------------- | ------------------ | ---------------------------------------------------------------------------- |
| `__user_import__.dry_run_requested`          | dry-run entry      | Manifest parsed cleanly; about to evaluate preconditions.                    |
| `__user_import__.dry_run_validation_passed`  | dry-run exit       | `would_succeed=true`. No writes happened.                                    |
| `__user_import__.dry_run_validation_failed`  | dry-run exit       | `would_succeed=false`. No writes happened.                                   |
| `__user_import__.started`                    | real-import entry  | All preconditions passed; about to begin writes inside `MetadataStore.transaction()`. |
| `__user_import__.completed`                  | real-import exit   | Writes committed. Receipt was returned.                                      |
| `__user_import__.refused`                    | any precondition   | **Clean rollback. No writes happened.** Emitted by the public refusal-catch wrapper for every `ImportRefused` raised at any stage (forbidden_path, archive_too_large, archive_unsafe_entry_names, manifest_invalid, missing_user_record, missing_sections, manifest_section_count_mismatch, unsupported_format_version, email_exists, uuid_collision_on_parent_table, archive_integrity_failed). |
| `__user_import__.failed`                     | post-write only    | An uncaught exception escaped *after* writes had begun. **Investigate** — partial state on the destination is possible. Distinct event from `.refused` so operators can grep on action name alone. |

Implementation note: `services/user_export.py` keeps the `_impl`
functions free of refusal-side audit emission; the public
`dry_run_import` / `import_user` wrappers catch `ImportRefused` and
write the `__user_import__.refused` row exactly once. This is the
single source of truth for the contract — adding a new refusal reason
needs no changes to audit-emission code, and the `.failed` lifecycle
event stays unambiguously reserved for the partial-state case.

## HTTP success semantics

| Operation                                                | Status                                | Body                              |
| -------------------------------------------------------- | ------------------------------------- | --------------------------------- |
| `POST /api/v1/me/export` self                            | `200 OK`                              | `application/zip` stream          |
| `POST /api/v1/me/export` admin-on-behalf, target unknown | `404 Not Found`                       | `{"detail": {"error": "user not found", "target_user_id": "..."}}` |
| `POST /api/v1/me/export` non-admin, target ≠ self        | `403 Forbidden`                       | "admin role required to export another user" |
| `POST /api/v1/admin/user-imports` `dry_run=true` ok      | `200 OK`                              | `ImportPlan` JSON (non-mutating). |
| `POST /api/v1/admin/user-imports` `dry_run=false` ok     | **`201 Created` + `Location` header** | `ImportReceipt` JSON; `Location: /api/v1/admin/users/{new_user_id}` points at the freshly-minted account. |

Dry-run is intentionally **not** 201 — it creates no resource, so
returning 201 + `Location` would be a lie. Only the real import path
mints a `users` row.

## Legacy `GET /api/v1/admin/export` — `410 Gone`

The pre-existing NDJSON dumper at `GET /api/v1/admin/export` (mounted
at `/export` because the admin router has no path prefix) returns
`410 Gone` in this build. The original ADR draft kept it byte-for-byte
unchanged for one release; this build superseded that per direct
instruction. The 410 detail body always points at the successor:

```json
{
  "detail": {
    "error": "deprecated",
    "successor": "POST /api/v1/me/export",
    "see": "*(maintainer-local only; not part of the tracked repository)*"
  }
}
```

Pinned by `tests/test_user_export_routes.py::test_legacy_admin_export_returns_410_with_successor`.

## CSRF / Bearer posture (v1)

`POST /api/v1/me/export` and `POST /api/v1/admin/user-imports` carry
`Depends(require_same_origin)`. **Bearer-authenticated calls bypass
the dep by design** in v1; the bypass is documented and pinned by
`tests/test_user_export_routes.py::test_export_route_with_bearer_skips_csrf_intentionally`.
A counterpart unit test
(`test_csrf_dependency_still_enforces_for_non_bearer_writes`) proves
`require_same_origin` itself still 403s on the cookie / no-Bearer
path so the bypass remains a *narrow* exception, not a CSRF off-switch.

The dep becomes the active CSRF defence the moment cookie-session
auth ships in `cross_device_lumogis_web`. Both regression tests must
change at the same time as that posture change.
