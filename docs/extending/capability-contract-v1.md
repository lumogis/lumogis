# Capability Contract v1 — author guide

**Status:** normative · **Contract version:** `1.0` · **Audience:** developers building
an out-of-process **HTTP capability** for Lumogis (primary); in-process plugin
authors (secondary — see [Shipped examples](extending-the-stack.md#shipped-examples-in-this-repository)).

A **capability** is a small HTTP service that adds tools to the Lumogis agent
loop. It runs as a container on the operator's own machine, on the same network
as the orchestrator ("Core"). Core discovers it, health-checks it, and calls its
tools when the LLM asks for them. You do not modify Core to add a capability —
you publish a manifest and Core does the rest.

The reference implementation you can fork is
[`services/lumogis-mock-capability/`](../../services/lumogis-mock-capability/) —
a ~110-line FastAPI service that implements this whole contract. Read it
alongside this document.

> This guide is self-contained. The field tables are authoritative from
> `orchestrator/models/capability.py` and `orchestrator/models/capability_invoke.py`;
> the design rationale lives in ADR-169 (`docs/decisions/`), but you do not need
> it to build a conforming capability.

---

## 1. What you implement

A capability service exposes exactly three HTTP endpoints:

| Method & path | Purpose |
| --- | --- |
| `GET /capabilities` | Return your **manifest** (JSON). Core polls this to discover your tools. |
| `GET /health` | Return `200` when ready. Core probes it for liveness. |
| `POST {invoke.path}` | Invoke one tool. Default path is `/tools/{tool_name}`; you may override it per tool (see [§4](#4-invocation)). |

That's the entire surface. No callbacks into Core are required for a read-only
tool. (A tool may call Core's public API back over HTTP if it needs to — see
[Calling Core back](extending-the-stack.md#calling-core-back-from-a-capability-service).)

---

## 2. The manifest

`GET /capabilities` returns a `CapabilityManifest`. Fields (authoritative:
`orchestrator/models/capability.py`):

| Field | Type | Notes |
| --- | --- | --- |
| `name` | str | Human-readable service name. |
| `id` | str | **Stable identifier.** Core dedupes services by this and **derives your bearer env var from it** (see [§5](#5-authentication)). Choose it once and never change it. |
| `version` | str | Your service's own version. |
| `type` | `"service" \| "plugin" \| "adapter"` | Use `"service"` for an HTTP capability. |
| `transport` | `"http" \| "mcp"` | `"http"` for this contract. |
| `license_mode` | `"community" \| "commercial"` | See [§8](#8-license-mode). |
| `maturity` | `"experimental" \| "preview" \| "stable"` | Advisory. |
| `description` | str | One-line summary. |
| `tools` | list of `CapabilityTool` | See below. |
| `health_endpoint` | str | Path Core probes for liveness. Use `"/health"`. |
| `capabilities_endpoint` | str | **Must be `"/capabilities"`.** Core discovers at that fixed path and **rejects** a manifest declaring anything else (it is validated, not documentary). |
| `contract_version` | str | This contract's version. Use `"1.0"`. See [§7](#7-versioning). |
| `auth` | `CapabilityAuth` | `{mode, credential_ref}` — see [§5](#5-authentication). |
| `permissions_required` | list[str] | Least-privilege scopes Core enforces at invoke (**ADR 171** / LUM-612). Each string is `area:verb` (e.g. `memory:read`). Use `[]` only when no scopes are needed. Grant via **`PUT /api/v1/me/permissions/capability.{id}`**. |
| `config_schema` | JSON Schema | Reserved. Use `{"type": "object"}`. |
| `min_core_version` | str | Minimum Core version. **See the rc footgun below.** |
| `maintainer` | str | Contact. |
| `management_url` | str \| null | Optional absolute URL to an operator admin page. |

### `CapabilityTool`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | str | — | The LLM-facing tool name (e.g. `weather.forecast`). Decoupled from the HTTP route — see `invoke`. |
| `description` | str | — | Shown to the LLM; write it as a prompt. |
| `license_mode` | `"community" \| "commercial"` | — | Per-tool tier. |
| `input_schema` | JSON Schema | — | Arguments the LLM must produce. Core does **not** validate input against this (the LLM is guided by it); your handler should validate. |
| `output_schema` | JSON Schema | — | **Core validates your `output` against this when it is non-trivial** — see [§4](#4-invocation). Declare `{"type": "object"}` / `{"type": "string"}` / `{}` for "any" (skips validation). |
| `is_write` | bool | `false` | Declare `true` if the tool mutates state. Feeds Core's Ask/Do permission model. Declare honestly. |
| `idempotent` | bool | `true` | Whether a retry is safe. Advisory in v1. |
| `timeout_ms` | int \| null | `null` | Requested per-invoke budget. **Core clamps it to its own ceiling** — you cannot request an unbounded budget. |
| `invoke` | `{method, path}` | `{method:"POST", path:null}` | How Core reaches this tool. `method` is `POST` in v1. `path` defaults to `/tools/{name}`; set it to map a different LLM `name` onto a fixed route (or many tools onto one endpoint). |

### The `min_core_version` rc footgun

Core compares `min_core_version` with `packaging.version.Version`, where a
release candidate sorts **before** its release:
`Version("0.3.0rc1") < Version("0.3.0")`. If you write `"0.3.0"` but the
operator runs a `0.3.0rc*` build, Core marks you **incompatible** and silently
skips your service. Match the rc suffix the Core you target actually uses
(the reference mock declares `"0.3.0rc1"` for exactly this reason).

### Minimal manifest

```json
{
  "name": "Weather capability",
  "id": "com.example.weather",
  "version": "0.1.0",
  "type": "service",
  "transport": "http",
  "license_mode": "community",
  "maturity": "preview",
  "description": "Local weather lookups.",
  "contract_version": "1.0",
  "auth": { "mode": "bearer", "credential_ref": "reserved" },
  "tools": [
    {
      "name": "weather.forecast",
      "description": "Return the forecast for a place.",
      "license_mode": "community",
      "input_schema": { "type": "object", "properties": { "place": { "type": "string" } }, "required": ["place"] },
      "output_schema": { "type": "object", "properties": { "summary": { "type": "string" } }, "required": ["summary"] },
      "is_write": false,
      "idempotent": true,
      "invoke": { "method": "POST", "path": "/tools/weather.forecast" }
    }
  ],
  "health_endpoint": "/health",
  "capabilities_endpoint": "/capabilities",
  "permissions_required": [],
  "config_schema": { "type": "object" },
  "min_core_version": "0.3.0rc1",
  "maintainer": "you@example.com"
}
```

---

## 3. Discovery & registration

The operator adds your base URL to Core's `.env`:

```bash
CAPABILITY_SERVICE_URLS=http://weather:8001,http://other-capability:8002
```

On startup (and every 5 minutes thereafter) Core:

1. `GET {base_url}/capabilities` and validates the body as a `CapabilityManifest`.
2. **Refuses** the service if `min_core_version` > Core's version, if
   `capabilities_endpoint` ≠ `/capabilities`, or if `contract_version`'s **major**
   is one Core doesn't speak (an unknown **minor** is accepted —
   forward-compatible).
3. Probes `{base_url}{health_endpoint}` immediately, then every 60 seconds.

Failure to reach a service is a logged warning — Core boots cleanly even when
every declared service is down. A registered service appears under
`capability_services` in `GET /` and on the dashboard.

`LUMOGIS_TOOL_CATALOG_ENABLED` defaults to **`true`**, so your tools reach the
LLM loop as soon as the service registers and a bearer is configured — **no flag
required**.

---

## 4. Invocation

To call a tool, Core POSTs a **request envelope** to `{base_url}{invoke.path}`:

```json
{
  "contract_version": "1.0",
  "tool": "weather.forecast",
  "arguments": { "place": "Berlin", "user_id": "default" },
  "meta": { "user": "default", "request_id": "b1c2…" }
}
```

Your handler reads **`arguments`**. Respond with a **response envelope**:

```json
{ "ok": true, "output": { "summary": "Sunny, 22°C" } }
```

or, on failure:

```json
{ "ok": false, "error": { "code": "timeout", "message": "upstream slow", "retryable": true } }
```

`output` may be any JSON value (including `null`). Exactly one of `output` /
`error` is present, keyed off `ok`.

### `user_id` vs `meta.user`

- **`arguments.user_id`** — the **functional** scope. Core injects the current
  user's id into `arguments`; read it to isolate per-user data. A user-supplied
  `user_id` in the arguments is overwritten by Core, so you can trust it.
- **`meta.user`** — **attribution only** (mirrors the `X-Lumogis-User` header).
  **Never** use `meta.user` for authorization or scoping.

### Error codes

| `code` | Meaning |
| --- | --- |
| `invalid_arguments` | The arguments were malformed for this tool. |
| `unauthorized` | Auth failed. |
| `not_found` | The tool or target does not exist. |
| `timeout` | The tool exceeded its budget (`retryable: true` is typical). |
| `unavailable` | A dependency is down (often retryable). |
| `internal` | An unexpected server error. |
| `invalid_output` | **Core-side only** — your `output` failed its `output_schema` or exceeded the size cap. You will not send this; Core produces it. |

### How Core reads your response

Core parses the body as a v1 envelope **on every HTTP status** and honours a
valid `{ok:false,error}` verbatim regardless of status. **Return tool-level
failures as HTTP `200` with the error envelope** so `retryable` reaches the LLM.
Only a **non-envelope** body falls back to a coarse status map:
`401/403 → unauthorized`, `404 → not_found`, `503 → unavailable`,
`504 → timeout`, anything else → `internal`. A bare `500` with no envelope
therefore becomes an opaque `internal` — always return the envelope.

### Output validation & size

When a tool declares a **non-trivial** `output_schema` (anything beyond a bare
`type`), Core validates `output` against it and returns `invalid_output` on
mismatch — an unenforced schema is not the goal, so declare a real one or declare
`{"type": "object"}` for "any". Core also caps the raw response at
`LUMOGIS_INVOKE_OUTPUT_MAX_BYTES` (1 MiB default), applied to **every** response
regardless of schema; oversize responses become `invalid_output`.

---

## 5. Authentication

Service trust is a **bearer token**:

- Core sends `Authorization: Bearer <secret>` on every invoke. Verify it and
  reject (`401`) when it is missing or wrong.
- Core also sends `X-Lumogis-User: <id>` — **attribution only, not
  authentication.** Never accept it in place of the bearer.

**Where the secret comes from (important):** Core resolves the bearer from an
environment variable **derived from your manifest `id`**:

```
LUMOGIS_CAPABILITY_BEARER_<SANITIZED_UPPER_ID>
```

Non-alphanumerics in the id become `_`. For `id: "com.example.weather"` the
operator sets `LUMOGIS_CAPABILITY_BEARER_COM_EXAMPLE_WEATHER`. If that variable
is unset, Core **fail-closes**: your tools are omitted from the LLM catalog and
no invoke is attempted (no error is surfaced to you — the tool simply never
appears). This is why your `id` must be stable.

> **`auth.credential_ref` is reserved and not yet read by Core.** Declare
> `auth: {mode: "bearer", credential_ref: "reserved"}`. Do **not** expect Core to
> read a custom `credential_ref` to find your secret — the env var above (derived
> from `id`) is the only binding today. `credential_ref` is a forward-looking
> field for the plugin permission/signing work (LUM-507).

Set `auth.mode: "none"` only for a genuinely public tool; Core then invokes
without a bearer. Prefer `"bearer"`.

---

## 6. Data handling & local-first

Lumogis is local-first and zero-telemetry: everything runs on the operator's own
hardware and nothing about the household leaves it unless the operator opts in.
**A capability is part of that trust boundary.** Your service receives real user
data in `arguments` (including `arguments.user_id`) and returns real content in
`output`.

- **Do not exfiltrate** `arguments`, `user_id`, or `output` to any third-party
  service. If your tool must reach an external API to do its job, that egress is
  the **operator's** decision to make and audit — document exactly what leaves
  the box and why.
- **Do not phone home**, collect analytics, or log user content off the machine.
- Treat outbound network access as privileged. Core's tethered-egress allowlist
  (LUM-553), capability permission scopes (**ADR 171** / LUM-612), and the
  community-dispatch gate (**ADR 172** / LUM-613) enforce this for untrusted
  capabilities; declare honest **`external_endpoints`** and expect dispatch to be
  refused until an operator opts in.

A capability that quietly relays household data to the cloud breaks the one
promise Lumogis makes to its users. Don't ship one.

---

## 7. Versioning

`contract_version` is `MAJOR.MINOR`.

- **Additive, backward-compatible changes bump the minor** (new optional fields).
  Core accepts an unknown minor forward-compatibly, so a capability built for
  `1.0` keeps working against a Core that speaks `1.3`.
- **Breaking changes bump the major.** Core **refuses** a manifest whose major it
  does not speak. Build to `"1.0"`.

A future **v2** will add fields **additively** (e.g. a signed-manifest block) and
remain backward-compatible with v1 capabilities.

---

## 8. `license_mode`

- **`community`** is the norm for capabilities built on the AGPL core. This guide,
  and the mock reference, are community.
- **`commercial`** capabilities exist and ship separately (e.g. Lumogis's own
  knowledge-graph service). They speak the **same wire contract** — the only
  difference is distribution and licensing. The privacy claim rests on the AGPL
  core pipeline being inspectable; commercial capabilities are value-add services
  that still run on the operator's hardware.

Set the tier honestly on both the manifest and each tool.

---

## 9. Hook events (optional)

If you also ship an **in-process plugin** (secondary audience), you can subscribe
to Core's hook events. v1 documents the constants only — payloads are subject to
change until a typed appendix lands. Constants live in `orchestrator/events.py`
(`class Event`); subscribe with `hooks.register(Event.X, fn)`.

| Constant | String | Fires when |
| --- | --- | --- |
| `TOOL_REGISTERED` | `on_tool_registered` | A tool is registered (how a capability tool enters the loop). |
| `DOCUMENT_INGESTED` | `on_document_ingested` | A document finishes ingest. |
| `ENTITY_CREATED` / `ENTITY_MERGED` | `on_entity_created` / `on_entity_merged` | Entity lifecycle. |
| `SESSION_ENDED` | `on_session_ended` | A chat session ends. |
| `CONTEXT_BUILDING` | `on_context_building` | Context is being assembled for an LLM call (kwargs contract documented inline in `events.py`). |
| `ACTION_EXECUTED` / `ACTION_REGISTERED` | `on_action_executed` / `on_action_registered` | Action lifecycle. |
| `NOTE_CAPTURED`, `AUDIO_TRANSCRIBED`, `SIGNAL_RECEIVED`, `FEEDBACK_RECEIVED`, `INJECTION_FLAGGED`, … | see `events.py` | Other lifecycle points. |

For the exact payload of any event, read its emitting site — `events.py` carries
normative kwargs comments for the load-bearing ones.

An HTTP capability does **not** use hooks; this section is only for in-process
plugins.

---

## 10. MCP surface (optional)

When the MCP SDK is installed, Core also mounts a Model Context Protocol server at
`/mcp`. It is an alternative surface for MCP clients, not part of this HTTP
contract. If it is not installed, `/mcp` is absent and capabilities work
unchanged. See the MCP sections of the reference manual for that surface.

---

## 11. Fork this — checklist

Start from [`services/lumogis-mock-capability/`](../../services/lumogis-mock-capability/)
and:

1. **Rename** the service `id` to your own stable reverse-DNS id
   (`com.example.mytool`) in the manifest.
2. **Declare your tools** in `tools[]` with real `input_schema` / `output_schema`
   and honest `is_write`. Set each `invoke.path` (or rely on the `/tools/{name}`
   default).
3. **Implement each `POST {invoke.path}`** to read `arguments`, do the work, and
   return `{"ok": true, "output": …}` (or a `{"ok": false, "error": …}` envelope
   at HTTP 200 on failure).
4. **Verify the bearer** on every invoke; reject with `401` when missing/wrong.
5. **Set the operator env var** `LUMOGIS_CAPABILITY_BEARER_<YOUR_SANITIZED_ID>`
   and add your base URL to `CAPABILITY_SERVICE_URLS`.
6. **Honour local-first** ([§6](#6-data-handling--local-first)) — no exfiltration.
7. Run your service's tests (the mock ships a `tests/test_app.py` you can adapt)
   and confirm the manifest validates and `mock.echo_ping` round-trips.

---

## See also

- [`extending-the-stack.md`](extending-the-stack.md) — Compose overlays, backends,
  and where capabilities fit in the broader extension story.
- [`docs/capabilities.md`](../capabilities.md) — the *user-facing* view of which
  capabilities are available (this guide is the *author-facing* "how to build one").
- `orchestrator/models/capability.py`, `orchestrator/models/capability_invoke.py` —
  the authoritative models behind every field above.
