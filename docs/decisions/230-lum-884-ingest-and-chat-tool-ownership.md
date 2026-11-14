# ADR-230: Chat file tools follow search visibility, and `/ingest` becomes an admin-only Rescan (LUM-884)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** LUM-884 Linear decisions comment (2026-11-07), Basic Memory decision "Family library — shared Family space plus private My documents" (2026-11-07), and the `/create-plan` clarifications recorded in the plan; revised in `/review-plan` arbitration R1
**Finalised by:** /verify-plan 2026-11-07 (claude-opus-5-5) — implementation confirmed decision; no draft ADR preceded this record
**Plan:** `.cursor/plans/LUM-884-ingest-and-chat-tool-ownership.plan.md`
**Linear:** LUM-884 (child of LUM-883)

## Context

A security audit found four ways a logged-in household member could reach another member's files:

- `POST /ingest` accepted any folder path from any member and indexed it under the caller.
- Chat `read_file` checked only that a path sat under a library root, and the filename fallback of search walked the library folders on disk. Neither looked at who owned a file.
- `GET /browse` listed directories with no auth dependency.
- `run_tool` and `ToolExecutor.execute_inprocess` retried a tool handler without `user_id` when it raised `TypeError`, and the graph `query_graph` handler took `user_id` from its model-supplied arguments, defaulting to `"default"`.

## Decision

1. **Chat sees what search sees.** A file is readable through `read_file`, and nameable through the filename fallback, only when a `file_index` row for its resolved path passes `visibility.visible_filter` for the caller. Both build the caller context through `services.search._caller_context`, the same helper search uses. There is no admin exception, unindexed files are not readable even by their owner, missing and hidden files get the same error, and a lookup error fails closed. The filename fallback queries `file_index`; it no longer walks the disk.
2. **`POST /ingest` is an admin-only Rescan with no folder argument.** Any body field is a 422. It queues one `ingest_folder` job per configured library folder, filed under `INGEST_PATHS_OWNER_USER_ID` (falling back to `INBOX_OWNER_USER_ID`), otherwise under the calling admin, and skips a folder that already has a pending or running job for that owner.
3. **Only explicitly configured folders can start a scan.** `config.get_configured_ingest_paths()` returns `INGEST_PATHS` or `FILESYSTEM_ROOT`, and `[]` when neither is set. Rescan, the startup scan and the `ingest_folder` batch handler use it. `get_effective_ingest_paths()` keeps its `$HOME` fallback for read-side callers only. The batch handler re-checks every job's path and, when an owner is configured, its user, so the queue cannot be fed another way.
4. **`GET /browse` requires admin.**
5. **Every tool handler takes keyword-only `user_id`.** Neither `run_tool` nor `ToolExecutor.execute_inprocess` falls back to a one-argument call. `query_graph_tool(input_, *, user_id)` ignores any `user_id` key in its arguments; the graph service route pops `arguments.user_id` (set by Core's bearer-protected proxy) and refuses a call without it.

## Alternatives Considered

- **Admins may read any member's file through chat:** rejected. Search grants no such access, and documents injected into chat could exploit it.
- **Owners may read unindexed files under the library:** rejected, to keep one visibility rule.
- **Silently ignore old `/ingest` body fields:** rejected. A caller could believe it had indexed a folder it had not.
- **409 when no library owner is configured:** rejected. There is always an admin, and filing the scan under them is within admin rights.
- **Keep the one-argument fallback with a warning:** rejected. A handler that cannot see the caller cannot isolate their data.
- **A permanent-failure type so a rejected `ingest_folder` job dies at once:** rejected for this chunk. Each retry fails before reading a file, and the handler cannot mark itself dead because the queue's `complete()` would overwrite it.
- **Limit the filename fallback to rows under the library folders:** rejected. Search already returns the same `file_path` values to the same caller, so the fallback discloses nothing new.

## Consequences

- **Easier:** chat, search and the filename fallback share one visibility definition. A new chat file tool reuses `_caller_context` and `visible_filter` instead of inventing a rule.
- **Breaking:** `POST /ingest` no longer takes `{path, user_id}`. The OpenAPI breaking-change gate flags this on purpose. A plugin tool handler written against the one-argument signature now raises `TypeError`.
- **Known limits:**
  - With no `INGEST_PATHS_OWNER_USER_ID`, two different admins clicking Rescan index the library twice, once per admin (code note at `services/ingest.py` `enqueue_library_rescan`). LUM-891 reworks library-folder ownership.
  - Coalescing is check-then-insert, not atomic. Two concurrent Rescans can both enqueue; unchanged files are hash-skipped, so it is harmless.
  - The filename fallback now depends on Postgres. During a DB outage it logs a warning and returns no results.
  - `file_index` rows written before ingest stored resolved paths deny their owners in `read_file`; re-ingest is the remedy.
  - The graph service's kg_mcp `graph_query_*` tools still take `user_id` from the MCP client (code note at `services/lumogis-graph/kg_mcp/server.py`). This is the same class of problem over a different transport and is tracked in LUM-883.
  - `/bundled/start-library-index` has no loopback check yet (LUM-883).
- **Future chunks must know:** anything that *starts* a library scan uses `get_configured_ingest_paths()`. LUM-891 changes who library documents belong to in one place, the Rescan route plus `enqueue_initial_ingest_scan`, and must not add a path argument back.

## Revisit conditions

- When LUM-891 introduces a shared Family library, revisit decision 2's owner resolution.
- If a batch-queue permanent-failure type is added, use it for rejected `ingest_folder` jobs.
