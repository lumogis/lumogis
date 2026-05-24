# ADR 054: Paperless-ngx → Lumogis ingest (v0.1 Docker connector)

**Status:** Accepted
**Created:** 2026-05-21
**Last updated:** 2026-05-21
**Decided by:** `/explore --headless LUM-281` (draft); `/review-plan --arbitrate` R1; **`/verify-plan --headless` LUM-281** (finalisation)
**Linear:** [LUM-281](https://linear.app/lumogis/issue/LUM-281/feature-paperless-ngx-lumogis-ingest-v01-docker-hn-persona-a)

## Context

v0.1 ships a **read-only paperless-ngx** connector for self-hosted deployments: poll the paperless REST API with token auth, incremental **`added`** watermark in **`sources.poll_cursor`**, dedup via **`external_documents`**, and reuse the existing **sanitise → chunk → embed → Qdrant** path through **`ingest_external_document`**. Out-of scope: Lumogis Web onboarding (**LUM-282**), KG projection (**LUM-283**), webhooks-only ingest.

## Decision

1. Register **`paperless`** in **`orchestrator/connectors/registry.py`** with **`ConnectorSpec`** parity to other connectors.

2. **Credentials:** **`orchestrator/services/paperless_credentials.py`** — per-user encrypted **`{base_url, token}`**; **`validate_outbound_connector_base_url`** on save/load; env fallback **`PAPERLESS_BASE_URL` / `PAPERLESS_TOKEN`** for **`user_id="default"`** only when **`AUTH_ENABLED=false`**, mirroring CalDAV.

3. **Outbound URL policy:** shared **`orchestrator/services/outbound_http_url.py`** (**`validate_outbound_connector_base_url`**) with **`LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS`** and **`LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST`**; **CalDAV** calls the same helper in this release (symmetric trust posture vs arbitration D5.1).

4. **Adapter:** **`orchestrator/adapters/paperless_source.py`** — **`httpx`** pagination over **`/api/documents/`** with **`ordering=added`** and optional **`added__gt`**; body from JSON **`content`**; **`Authorization: Token …`** header; bounded retries for **429/5xx**.

5. **Ingest:** **`ingest_external_document`** in **`orchestrator/services/ingest.py`** shares **`_ingest_chunked_text`** with **`ingest_file`**; **`external_document_chunk_point_id(user_id, source_id, external_kind, external_document_id, chunk_index)`** in **`orchestrator/services/point_ids.py`** (uuid5, B11).

6. **Scheduler:** **`orchestrator/signals/feed_monitor.py`** — dispatcher **`_poll_source`**: paperless path **`_poll_paperless_source`** never calls **`_build_adapter`**, **`adapter.poll()`**, or **`process_signal`**.

7. **API:** **`POST /api/v1/sources`** accepts optional **`source_type`**; **`"paperless"`** branch skips RSS detection; **`confirm=false`** returns preview without live paperless.

8. **Persistence:** migration **`024-paperless-external-documents.sql`** — **`sources.poll_cursor`** + **`external_documents`** table as locked in plan (no **`user_id` DEFAULT** on **`external_documents`**).

9. **Public webhook contract:** additive **`ingestion_source_kind`** on **`DocumentIngestedPayload`** (**`"filesystem"` \| `"external"`**); **`graph_webhook_dispatcher`** unchanged — it builds payloads via **`DocumentIngestedPayload`** field filtering; lumogis-graph **`models/webhook.py`** vendored in lockstep (**`make sync-vendored`** discipline).

## Alternatives Considered

See **`.cursor/explorations/LUM-281-paperless-ngx-ingest.md`** (exploration) and draft **`.cursor/adrs/LUM-281-paperless-ngx-ingest.md`**: **`pypaperless`**, webhook-only, third-party MCP servers, **`document_exporter`** consumer — rejected for v0.1.

## Consequences

**Positive:** First external-document connector establishes the **`external_documents` + `ingest_external_document`** template for **LUM-177** / **LUM-170**; deterministic Qdrant ids; SSRF-aware outbound URL gate shared with CalDAV.

**Trade-offs / follow-ups:** Poll latency vs webhooks; **`test_two_user_isolation`** does not yet extend a **two-user paperless** Postgres-backed row (**P1** — tracked via **`VERIFY_RESULT.gaps`** for Linear child under **LUM-281**). Full compose CI against real paperless image remains deferred (plan testing table).

## Status history

- **2026-05-21:** Finalised by **`/verify-plan --headless` LUM-281** — implementation confirmed against plan; canonical copy under **`docs/decisions/`**.
