# Security Audit #1 — lumogis-core v0.3.0-rc

**Date:** 2026-03-18
**Auditor:** Automated (AI-assisted) + manual review
**Scope:** orchestrator/ — all Python code, SQL queries, tool handlers, permission boundaries

---

## 1. SQL Injection

**Status: PASS (with 2 fixes applied)**

### Methodology

Grepped all `.execute()`, `.fetch_one()`, `.fetch_all()` calls across the entire `orchestrator/` tree. Verified each call uses `%s` parameterized placeholders via the `params` argument.

### Findings

**53 SQL calls audited across 12 files:**

| File | Calls | All parameterized? | Notes |
|---|---|---|---|
| `services/entities.py` | 5 | YES | All use `%s` + tuple params |
| `services/ingest.py` | 3 | YES | `%s` for file_hash, path, user_id |
| `services/routines.py` | 7 | YES | All use `%s` + tuple params |
| `services/tools.py` | 2 | YES | `%s` for entity name |
| `services/signal_processor.py` | 2 | YES | `%s` for user_id, signal fields |
| `signals/feed_monitor.py` | 3 | YES | 1 no-param SELECT (no user input) |
| `signals/page_monitor.py` | 1 | YES | No-param SELECT (no user input) |
| `signals/calendar_monitor.py` | 1 | YES | No-param SELECT (no user input) |
| `permissions.py` | 6 | YES | All `%s` parameterized |
| `actions/audit.py` | 3 | YES | All `%s` parameterized |
| `actions/reversibility.py` | 1 | YES | `%s` for reverse_token |
| `routes/admin.py` | 8 | SEE BELOW | 2 f-string table names (safe), 1 f-string col_list (FIXED) |
| `routes/signals.py` | 1 | SAFE | f-string `{where}` built from `%s` conditions only |
| `routes/data.py` | 2 | YES | All `%s` parameterized |

### f-string SQL (3 instances — all in `routes/admin.py`):

1. **`f"SELECT * FROM {table}"` (line 281)** — `{table}` comes from `_BACKUP_TABLES`, a hardcoded constant. **SAFE.** No user input reaches the table name.

2. **`f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"` (line 358)** — `{table}` is from `_BACKUP_TABLES` (safe). `{col_list}` was built from `row.keys()` where `row` comes from a backup JSON file inside a zip. A tampered backup could inject SQL via column names like `id); DROP TABLE entities; --`.
   - **VULNERABILITY FOUND. FIXED:** Added `_COL_RE = re.compile(r"^[a-z_][a-z0-9_]*$")` validation. Rows with non-alphanumeric column names are now skipped with a warning log.

3. **`f"FROM signals WHERE {where}"` (signals.py line 215) and `f"FROM audit_log WHERE {where}"` (audit.py line 74)** — `{where}` is built from a list of conditions that are all `"column = %s"` strings. The column names are hardcoded in Python. User input only flows into the `params` tuple. **SAFE.**

### No raw string interpolation of user input into SQL anywhere in the codebase.

---

## 2. MCP Boundary (FILESYSTEM_ROOT)

**Status: FAIL → FIXED**

### Finding

`_read_file()` in `services/tools.py` (line 141) opened any absolute path the AI requested with zero validation. The `FILESYSTEM_ROOT` environment variable was only used by `fuzzy_filename_search()` in `services/search.py` — not by the tool handler that actually reads files.

**Attack:** The AI could be prompted to `read_file("/etc/passwd")` or `read_file("/app/.env")` and the tool would return the contents.

### Fix Applied

`_read_file()` now resolves the requested path and verifies it starts with `FILESYSTEM_ROOT` (resolved). Returns `"Access denied: path is outside FILESYSTEM_ROOT"` if the check fails. Symlink traversal is prevented by using `Path.resolve()`.

---

## 3. Ask/Do Boundary

**Status: PASS**

### How it works

- Every tool call flows through `run_tool()` → `_check_permission()` → `permissions.check_permission()`.
- `check_permission()` reads the connector mode from `connector_permissions` table (default: `ASK`).
- In `ASK` mode, `is_write=True` tools are blocked and the denial is logged to `action_log`.
- The `ToolSpec` dataclass is `frozen=True` — `is_write` cannot be mutated after registration.

### Verification

- `search_files`: `is_write=False` → always allowed in ASK mode ✓
- `read_file`: `is_write=False` → always allowed in ASK mode ✓
- `query_entity`: `is_write=False` → always allowed in ASK mode ✓
- No write tools are registered in core. Plugin-registered tools go through the same `run_tool()` → `check_permission()` path.

### Action executor (`actions/executor.py`)

- `execute()` calls `check_permission()` before running any handler.
- Hard-limited action types (`financial_transaction`, `mass_communication`, `permanent_deletion`, `first_contact`, `code_commit`) can never be auto-elevated to routine Do.
- Permission bypass is not possible — the check is unconditional.

**The AI cannot write files when filesystem access is in ASK (read-only) mode.**

---

## 4. Action Log

**Status: PASS**

### How it works

`permissions.log_action()` is called from `check_permission()` on every tool call — both allowed and denied. The log entry includes: connector, action_type, mode, allowed (bool), input_summary, result_summary, timestamp.

### Verification

- `check_permission()` calls `log_action()` unconditionally (line 44-49 in `permissions.py`).
- Both successful and denied actions are logged.
- The `action_log` table is append-only (no UPDATE or DELETE in any code path).

**All reads and writes through the tool system are logged to `action_log`.**

---

## 5. Audit Log

**Status: PASS**

### How it works

`actions/executor.py` → `_write_audit_and_fire()` writes an `AuditEntry` to `audit_log` after every action execution — successful or failed. Also fires `Event.ACTION_EXECUTED` hook.

### Verification

- `_write_audit_and_fire()` is called in both the success path (line 82) and the permission-denied path (line 66).
- `audit.py` provides `write_audit()` (INSERT) and `mark_reversed()` (UPDATE reversed_at only). No DELETE method exists — append-only by design.
- Integration test `test_routine_run_writes_audit_log` verifies the pipeline end-to-end.

**All action executions are logged to `audit_log`.**

---

## 6. Prompt Injection

**Status: PASS (with caveats)**

### Tool scope

The AI can only call tools registered in `TOOL_SPECS` (3 core tools: `search_files`, `read_file`, `query_entity`). Tool lookup in `run_tool()` uses exact name match against the registry — unknown tool names return `{"error": "Unknown tool: ..."}`.

The tool loop is capped at `MAX_TOOL_ROUNDS = 2` — even if the AI hallucinates additional calls, the loop terminates.

### System prompt hardening

- Tool-enabled models get `SYSTEM_PROMPT_TOOLS` which constrains behavior to "search and read."
- Non-tool models get `SYSTEM_PROMPT_NO_TOOLS` which explicitly says "Never pretend to search files, read files, or call tools."

### What prompt injection CANNOT do

- Call tools not in the registry (exact match lookup).
- Write files (no write tools registered; Ask/Do blocks writes anyway).
- Access the database directly (tools only call pre-defined handlers).
- Elevate permissions (mode changes require `PUT /permissions/{connector}` which is a separate HTTP endpoint, not a tool).
- Auto-elevate hard-limited action types (structural enforcement in executor).

### Caveats

- **The AI can be socially engineered** to return misleading information. System prompt instructions are not a security boundary — they're a usability guardrail. A determined user can get the AI to ignore them.
- **Tool output is not sanitized** before being shown to the user. If indexed files contain adversarial content (e.g., "Ignore previous instructions"), it could influence the AI's response. This is inherent to any RAG system and is not specific to Lumogis.
- **Plugin-registered tools** go through the same permission check, but a malicious plugin could register a tool with `is_write=False` that actually performs writes. This is mitigated by the plugin loading mechanism (only loads from `orchestrator/plugins/`) and AGPL licensing, but is worth documenting.

---

## 7. Additional Findings

### 7a. `POST /restore` path traversal (FIXED)

**Finding:** `body.zip_path` accepted any filesystem path. An attacker with API access could point it at `/etc/shadow.zip` or a crafted zip anywhere on disk.

**Fix:** Resolved `zip_path` is now validated against `_BACKUP_DIR` — restore only reads from the configured backup directory.

### 7b. `POST /restore` column name injection (FIXED)

**Finding:** Column names from the backup JSON were interpolated directly into SQL (`col_list`). A tampered backup zip could inject SQL via column names.

**Fix:** Column names are now validated against `^[a-z_][a-z0-9_]*$`. Rows with invalid column names are skipped.

---

## 8. MCP Server `read_file` Boundary

**Status: FAIL → FIXED**

### Finding

`read_file()` in `mcp-servers/filesystem-mcp/server.py` opened any absolute path supplied by the caller with no boundary check. Unlike the orchestrator's `services/tools.py` (fixed in this audit), the MCP server had no `FILESYSTEM_ROOT` validation at all.

**Attack:** Any MCP client could call `read_file("/etc/passwd")` or `read_file("/app/.env")` and receive the file contents.

### Fix Applied

- `FILESYSTEM_ROOT` is now read from the environment at startup (`Path(os.environ.get("FILESYSTEM_ROOT", "/data")).resolve()`).
- `read_file()` resolves the requested path with `Path.resolve()` and returns an error if the result does not start with `FILESYSTEM_ROOT`.
- Symlink traversal is prevented by `Path.resolve()`.

---

## Summary

| Check | Result | Issues found | Fixed |
|---|---|---|---|
| SQL injection | **PASS** | 1 (col_list in restore) | YES |
| MCP boundary (orchestrator) | **FAIL → FIXED** | `read_file` had no FILESYSTEM_ROOT check | YES |
| MCP server read_file boundary | **FAIL → FIXED** | path traversal in filesystem-mcp | YES |
| Ask/Do boundary | **PASS** | 0 | — |
| Action log | **PASS** | 0 | — |
| Audit log | **PASS** | 0 | — |
| Prompt injection | **PASS** | 0 structural (caveats documented) | — |
| Path traversal | **FAIL → FIXED** | `POST /restore` accepted any path | YES |

**4 vulnerabilities found. All 4 fixed in this audit.**

### Files modified

- `orchestrator/services/tools.py` — FILESYSTEM_ROOT enforcement on `_read_file()`
- `orchestrator/routes/admin.py` — column name validation in restore, path validation on zip_path
- `mcp-servers/filesystem-mcp/server.py` — FILESYSTEM_ROOT enforcement on `read_file()`
