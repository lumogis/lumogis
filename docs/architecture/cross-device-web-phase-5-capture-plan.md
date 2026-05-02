---
status: closed-mvp
implemented: 5A–5I (Phase 5 Capture / QuickCapture MVP closed 2026-05-01)
extracted: 2026-04-29
closed_mvp: 2026-05-01
verified_artefact: docs/architecture/cross-device-web-phase-5-capture-plan.md
verify_plan: 2026-04-29
test_result: passing
done_checklist: 9/9
adr_status: umbrella 030 extended (Phase 5 confirmation — no new ADR)
---

<!-- VERIFICATION SUMMARY — updated 2026-04-29 -->
## Implementation Summary

| | |
|---|---|
| **Status** | ✅ Complete |
| **Verified** | 2026-04-29 — Composer (`/verify-plan`) |
| **Review rounds** | 0 external critique/arbitrate loops on this doc (planning extraction + self-review Rounds 1–2 in §Self-Review Log only) |
| **Tests** | Orchestrator **1714** passed / **9** skipped / **0** failed (`docker compose run … pytest tests`, same gate as `make compose-test`); web **241** passed + `lint` + `verify:pwa-dist`; host `make test` unavailable (`pytest` not on PATH) |
| **Files** | Chunks **5A–5I** as-built per §21.1 — ✅ |
| **Code review** | Capture + `user_export` paths: parameterized SQL ✅; attachment paths via `media_storage` + zip entry validation ✅; user_id / isolation ✅; 🚨 **none** |
| **Definition of done** | **9/9** chunk closeouts in §21.1 |
| **ADR** | **`docs/decisions/030-cross-device-client-architecture.md`** — status history extended for Phase 5 Capture MVP (**no** new `docs/decisions/NNN`) |

**What was built**

Phase 5 Capture / QuickCapture MVP (**5A–5I**): server **`captures`** family with media on disk, **`POST …/transcribe`** via the verified STT facade, explicit **`POST …/index`** into personal **`notes`** + Qdrant **`conversations`** only, **`/capture`** UI with IndexedDB outbox and manual sync, PWA shortcut + GET share-target prefill, and per-user export/import including **`captures/media/`** binaries with import-time blob restore.

**Deviations**

None — matches §21.1 as-built notes.

**Security findings**

None.

**ADR notes**

Umbrella cross-device ADR **030** records Phase 5 confirmation; deferred capture follow-ups remain in §21 (FP-TBD-5.*).

**Discoveries**

Contributors without a local Python venv can rely on **`docker compose run`** orchestrator pytest (documented `make compose-test` equivalent).

**Next steps**

1. Parent roadmap: **Phase 6 (Tauri)** and §21 follow-ups (**FP-TBD-5.*** / portfolio) as prioritised.
<!-- END SUMMARY -->

# Cross-device Web — Phase 5 Capture / QuickCapture (implementation plan)

**Slug:** Parent topic `cross_device_lumogis_web` — Phase **5** (Capture / QuickCapture).

**Date:** 2026-04-29

**Kind:** Strategic **architecture / extraction** from *(maintainer-local only; not part of the tracked repository)* and codebase inspection. **Not** an ADR. **Planning-only updates** (2026-04-29+): MVP **text + URL + photo + voice**; **server** transcription via the **verified Speech-to-Text foundation** (`docs/architecture/lumogis-speech-to-text-foundation-plan.md` — **STT-1**, **STT-2A**, **STT-2B**, **STT-2C** **closed**). **Phase 5 Capture** (**5D**) **consumes** that foundation only — **no** new STT adapters, sidecars, or `STT_*` wiring in Capture chunks. **Mobile** **voice recording + local audio staging** when **self-hosted Lumogis is unreachable** (**manual sync**, no silent upload); **no** product code is added by editing this document.

**Roadmap position:** **Phase 2** (mobile UX), **Phase 3** (PWA installability + bounded client storage), and **Phase 4** (Web Push) are **closed** as programme milestones. **Mobile Cloud Fallback + Sync** (*(maintainer-local only; not part of the tracked repository)*) is **orthogonal** — Chunk **0** is planning/spike only; Chunks **1–5** **parked**. Phase **5** must **not** import fallback crypto, offline sessions, or `POST /api/v1/sync/offline-sessions`. Shared themes only: **no silent sync**, **no SW API caching**, explicit user actions for anything that touches server state. <!-- SELF-REVIEW: D1 — removed stray leading space before *(maintainer-local only; not part of the tracked repository)*. -->

**Invariant decisions (carry forward from prior hardening):** Capture is **user-initiated** and **staged**: **nothing** is indexed into **`notes` / Qdrant / entities / graph** on save alone — only on explicit **“Add to memory” / `POST …/index`**. MVP index path remains **personal `notes` + Qdrant `conversations` only** — **no** **`documents`** (**FP-TBD-5.1** = **`memory/search` parity**). **`DELETE`** of **indexed** capture → **409** `indexed_capture_requires_memory_delete`; **no** cascade from capture DELETE. **No** SW API caching, **no** Background Sync; offline = **local outbox + manual sync** only. Mobile Cloud Fallback remains a **separate programme**.

**ADR alignment (`docs/decisions/030-cross-device-client-architecture.md`):** Lumogis remains **server-brained** — **no** offline graph, **no** offline semantic search, **no** silent sync. **Bounded** IndexedDB staging of **capture text/URL + voice blobs** is **transport/state persistence** for user-initiated manual sync, **not** a “local brain” or intelligence layer. Indexing and STT (when used) still run **only** after explicit user actions against a **reachable** orchestrator. <!-- SELF-REVIEW: D2/D8 — resolved tension with ADR “no offline-first intelligence”. -->

**Related:** `docs/architecture/lumogis-speech-to-text-foundation-plan.md` — **verified**; **STT-1 / STT-2A / STT-2B / STT-2C** **complete** (**SpeechToText** port, **`POST /api/v1/voice/transcribe`**). `clients/lumogis-web/src/pwa/README.md` (Phase 3C–3E + 4); `docs/architecture/cross-device-web-phase-4-web-push-plan.md`; *(maintainer-local only; not part of the tracked repository)*; `docs/decisions/030-cross-device-client-architecture.md` (umbrella ADR — Phase **5** detail belongs here until a future `/verify-plan` closes Phase 5).

---

## Executive summary

Phase **5** delivers a **mobile-useful QuickCapture** product: **text notes**, **optional URLs**, **photo/image** (camera **or** gallery/file picker), and **voice notes with transcription** — all **user-initiated**, **staged** on the server before memory, with **explicit** **Add to memory / Index**.

- **Photos/images** are stored as **`capture_attachments`** (`attachment_type = image`); binary lives on disk under **`LUMOGIS_DATA_DIR`** (§12.2). **No** OCR or image understanding in MVP unless added in a later follow-up (**FP-TBD-5.9**).
- **Voice — connected:** Audio uploads to Lumogis as **`capture_attachments`**; **transcription** uses the **verified Speech-to-Text foundation** (**`SpeechToText` port**, **`POST /api/v1/voice/transcribe`**, **`POST …/captures/{id}/transcribe`**); Capture **does not** ship STT adapters — only **5D** orchestration. **`capture_transcripts`** holds STT output for review.
- **Voice — self-hosted server unreachable (mobile):** User-initiated **recording still works**; **audio is stored locally** (IndexedDB **Blob/File** or browser-supported persistent storage) with **strict caps** and **clear disclosure** — see §5 / §13. **No** silent upload; **no** Background Sync; **manual “Sync now”** when Lumogis is reachable again. **Immediate transcription while offline** is **optional / future** (on-device STT or explicit direct-to-provider STT — **not** Capture MVP default; **no** Lumogis Cloud relay).
- **Transcript is user-reviewable** before indexing (when a transcript exists); **no** auto-index when server STT completes.
- **`POST …/index`** builds **one** personal **`notes.text`** from **reviewed** combined text (§9). **Core MVP** excludes arbitrary PDFs, Web Share Target (→**5H**), **live streaming STT**, **background** mic. **Wake word** — **FP-TBD-5.13**.

**MVP product slice (scope, not chunk ids):** **Text/URL** local outbox + **voice audio** local staging on mobile when server unreachable (§13 caps); **photo** remains **online-first** (offline photo staging **follow-up**, **FP-TBD-5.10b**). When **online**: multipart uploads; **`POST …/transcribe`** (**5D**) calls the **verified** server-backed STT foundation (**no** Capture-owned adapters). **Optional** offline transcript (mobile-capable STT) — **FP-TBD-5.15–5.17**, not required for MVP closure. **No** Qdrant **`documents`**; **FP-TBD-5.1** = **`memory/search`**. **No** `runtimeCaching`, **no** Background Sync, **no** silent upload.

---

## 1. What Phase 5 should build

| Layer | Outcome |
|--------|---------|
| **Product** | QuickCapture: text, URL, **photo** (online-first; camera/gallery when online), **voice** (**online** upload + STT **or** **offline** local audio staging on mobile — §5/§13); staged list; explicit **Index**; discard. |
| **Server** | **`captures`** + **`capture_attachments`** + **`capture_transcripts`**; media on disk; CRUD + **`POST …/attachments`**, **`POST …/transcribe`** (orchestrates **SpeechToText** port — §12.6), **`POST …/index`**. |
| **Client** | `MediaRecorder` (gesture-gated), **`<input type="file" accept="image/*" capture>`** + file-picker fallback; upload progress; transcript review UI. |
| **PWA** | Optional shortcut; **share_target** = chunk **5H** (not core MVP). SW unchanged (precache + push only). |

---

## 2. Terms: “Capture” vs “QuickCapture”

| Term | Meaning |
|------|---------|
| **Capture** | The **domain**: staging rows, **attachments**, **transcripts**, API, export, and **index** into **`notes` + `conversations`**. |
| **QuickCapture** | The **UX surface**: **mobile-first** (**not** mobile-only) entry for text/URL/photo/voice without full chat — **Lumogis Web** on phone + desktop (§3.1). |

---

## 3. Capture modes — MVP vs later

| Mode | MVP product slice | Later (deferred) |
|------|-------------------|------------------|
| **Text quick note** | **Yes** | — |
| **URL / link** | **Yes** (store URL; **no** server-side scrape in MVP) | Rich link preview / scraping |
| **Photo / image** (camera **or** gallery / file picker) | **Yes** (attachment + optional caption on **`captures.text`**) | OCR / image understanding (**FP-TBD-5.9**) |
| **Voice note + transcription** | **Yes** (audio attachment + **`capture_transcripts`**) | Advanced audio workflows, streaming STT |
| **Arbitrary file / PDF** | **No** | General upload / document ingestion |
| **Web Share Target** | **No** (chunk **5H** if desired) | OS share sheet integration |
| **Video** | **No** | — |
| **Auto-classification / auto-index** | **No** | Product-gated follow-ups |

**Why not `ingest_folder` for MVP media:** Browser uploads need **multipart**, **MIME/size enforcement**, and **per-capture** staging — not server path ingest (**`services/ingest.py`** remains for filesystem bulk ingest).

### 3.1 Cross-device scope and photo/gallery (MVP)

- **Capture** is part of **Lumogis Web** and targets **mobile and desktop**, not phone-only.
- **QuickCapture** is **mobile-first** — keyboard, large hit targets, camera/gallery affordances — but **desktop** gets the same flows via **file picker** and **mic / audio upload** where the browser allows.
- **Mobile:** camera **and** **gallery** / photo library selection where supported; **desktop:** file picker + optional recording stack per browser.
- After **server sync**, captures can be **listed, reviewed, indexed, and deleted** from **another signed-in device** (same user) — no device-tied locks in MVP.

**Photo / gallery clarification:** **Photo/image capture** means **taking a new photo** **or** **choosing an existing image** from the gallery / filesystem. **No** OCR or image “understanding” in MVP. **Index** uses **user caption / text** on the capture only (§9), not image semantics.

---

## 4. Where data lives

| State | Location | Notes |
|--------|-----------|-------|
| **Draft** (typing) | **IndexedDB** via `idb-keyval` pattern — either **reuse** `getDraft`/`setDraft` with `makeCaptureDraftKey(localId)` or a **dedicated store name** for richer capture composer state | Must stay **separate** from `makeChatDraftKey` and from **`lumogis:query-cache`** |
| **Outbox** (saved offline, unsynced) | **IndexedDB** — **text/URL** rows + **voice audio** blobs (§13); **never** service worker **Cache Storage** | **No** Cache API for captures |
| **Synced capture** | **Postgres** **`captures`** — parent row: text, URL, title, **`status`** (**`pending` \| `failed` \| `indexed`** — server only), **`local_client_id`** / wire **`client_id`**, **`note_id`**, timestamps | Staging before memory (**draft** = IndexedDB only — not a server status) |
| **Attachments** | **Postgres** **`capture_attachments`** + **files on disk** under **`LUMOGIS_DATA_DIR/captures/<user_id>/<capture_id>/<attachment_id>/`** (§12.2) — **no** raw binaries in Postgres for MVP | `attachment_type`: **`image` \| `audio`** |
| **Transcripts** | **Postgres** **`capture_transcripts`** — one row per STT result, FK **`attachment_id`** to audio attachment | Status-driven UX (review before index) |
| **Indexed memory** | **Personal `notes` row** + **Qdrant `conversations`** only (MVP); **not** **`documents`** (**FP-TBD-5.1**) | Only after **`POST …/index`** |

**Server has no row for purely local audio** until the user runs sync; **`local_capture_id` / `local_attachment_id`** tie client state to idempotent **`POST /captures`** + **`POST …/attachments`** (§7).

---

## 5. Offline capture semantics

**Text + URL (MVP):** Structured rows in **IndexedDB**; sync via **explicit** “Sync now”. **No** Background Sync.

**Voice / audio (MVP — mobile QuickCapture):** When the **self-hosted Lumogis server is unreachable**, **user-initiated** recording **must still work**. Audio is stored **locally** (IDB **Blob/File** or supported persistent storage) under **strict** count/byte caps (§13), with **prominent disclosure** that recordings live on device. **Sync** only after **manual** user action when online — **no** silent upload. **Private default when offline:** store audio only → **transcribe later** with **server-backed STT** (**shared foundation**) after upload. **Immediate offline transcription** is **not** required for MVP: it depends on **optional** **mobile-capable STT** (on-device or explicit **direct-to-third-party** STT from the client — **opt-in**, **no** Lumogis Cloud relay; see **`lumogis-speech-to-text-foundation-plan.md`** §11 and **FP-TBD-5.15–5.17**).

**Implementation resilience:** If storage is **unavailable** or **quota exceeded**, UI **fails safely** with copy explaining that **local save** is not possible.

**Photo / image (MVP):** **Online-first** — no requirement to stage photos offline in MVP. **Offline photo** IDB staging = **follow-up** unless product later elevates it (**FP-TBD-5.10b**). When offline, UI may **disable** photo capture or show **“requires connection”** — unlike voice.

**Not:** Offline chat, offline search, offline tool execution, queued admin actions, **wake word**, **always-listening** mic.

**Alignment:** Same contract as cross-device hardening: **no silent sync**; orthogonal to **Mobile Cloud Fallback** (no **`offline-sessions`**, no relay).

---

### 5.1 Local voice outbox record (planning shape)

Client-side structure (not a Postgres table):

```json
{
  "local_capture_id": "<uuid>",
  "local_attachment_id": "<uuid>",
  "kind": "audio",
  "mime_type": "audio/webm",
  "size_bytes": 12345,
  "duration_seconds": 8.2,
  "blob_ref": "idb:…",
  "created_at": "<iso8601>",
  "status": "local_pending",
  "last_error": null
}
```

- **`status`:** **`local_pending`** \| **`syncing`** \| **`synced`** \| **`failed`**.
- **`blob_ref` / stored_blob:** implementation detail (opaque handle to Blob in IndexedDB).
- Optional **local transcript** (future): separate object keyed by **`local_attachment_id`**, synced into **`capture_transcripts`** with **`transcript_provenance`** (§12.1).

---

## 6. Relation to Phase 3 IndexedDB patterns

Already shipped:

- **`src/pwa/drafts.ts`**: text-only, `DRAFT_MAX_CHARS = 32_768`, `makeCaptureDraftKey(captureId)` reserved.
- **`src/pwa/queryPersistence.ts`**: allowlist **empty**; mutations never persisted.
- **`src/pwa/useOnlineStatus.ts`**: `OfflineBanner`; TanStack `onlineManager`; **no** mutation replay.

Phase **5** should **add**:

- **`captureOutbox`** (or extend drafts) for **text/URL** **and** **voice audio** local rows (§5.1), with **hard caps** (§13).
- **Disclose** in UI: **pending text and local voice audio** on device; **never** use **Cache Storage** for captures; user can **discard** local voice.

**Rules:**

- Keys **must not** embed JWTs or emails.
- Surface **Settings** / Capture: **local pending captures** (including **audio byte size** estimate where feasible).

---

## 7. Sync / upload to orchestrator

**Recommended API shape** — full route table and OpenAPI rules: **§12.4**.

1. **`POST /api/v1/captures`** — **canonical** create; body includes optional **`client_id`** (UUID — same value as outbox **`local_capture_id`** when syncing §5.1). **`Idempotency-Key`** header — **deferred** post-MVP (MVP: **`client_id`** in body only — §7).
2. **`POST /api/v1/captures/text`** — **backwards-compatible alias** only: forwards to the same handler as (1), returns the **same response model** as **`POST /api/v1/captures`** (OpenAPI may list both paths; behaviour must not diverge).
3. **`PATCH /api/v1/captures/{id}`** — edit **pending** capture (text, title, url, **user-edited transcript fields** if modeled on parent row — prefer transcript on **`capture_transcripts`** with PATCH on transcript id in a later OpenAPI revision).
4. **`POST /api/v1/captures/{id}/attachments`** — **multipart** image/audio upload — **canonical** for binary (**§12.4**).
5. **`DELETE /api/v1/captures/{id}`** — see §10 (pending/failed only; indexed → **409**).

**Canonical routes & OpenAPI (chunks 5A–5C):** Any change to capture **paths**, **request/response schema**, or **status codes** requires regenerating **`clients/lumogis-web/openapi.snapshot.json`** (and client **`npm run codegen`**) in the **same PR** as the orchestrator change — same bar as other `/api/v1/*` facades.

**`POST /api/v1/captures/upload` (stub today):** Keep **`501 Not Implemented`** with **clear error body** directing clients to **`POST /api/v1/captures/{id}/attachments`** (multipart, after capture create) — avoids two competing upload surfaces. Optionally later **`410`** once legacy clients are gone.

**Auth:** `require_user`; **`user_id` only from JWT** — reject any body field that claims another user (mirror `POST /ingest` admin override **only** if product explicitly wants capture-on-behalf; default **no**).

**Idempotency (MVP — frozen):** Canonical key is **`client_id`** in the **JSON body** only; **`Idempotency-Key`** — **out of MVP** (follow-up). For the **same** authenticated **user**, **same** **`client_id`**, and **the same** normalised payload (**`text`**, **`title`**, **`url`**, and any MVP create fields that affect capture semantics) → **`200 OK`** with the **same** server **`id`** as the first successful create — **not** `201` on replay. **Conflict:** same **user** + same **`client_id`** + **different** payload → **`409 Conflict`**, stable code **`idempotency_key_conflict`** — **never** a second capture. **`409`** is **not** used for successful idempotent replay. Omit **`client_id`** → **no** idempotency (each request may create a new row). <!-- MVP freeze 2026-04-29: body-only idempotency. -->

**Partial sync (server state):** **`POST /captures`** may succeed while **`POST …/attachments`** fails (network, **413**/**415**, disk full). Server may hold a **`pending`** capture **without** attachments — **valid**; client marks outbox **`failed`** / **`syncing`** and **retries attachment** with the **same** **`client_attachment_id`** (idempotent). **No** automatic server-side deletion of the orphan row in MVP (user can **discard** pending capture from UI). <!-- SELF-REVIEW: D4 — avoids “silent inconsistent” ambiguity. -->

**Local → server sync:** For outbox voice rows, **manual sync** performs **`POST /api/v1/captures`** with **`client_id` = `local_capture_id`**, then **`POST …/attachments`** with **`client_attachment_id` = `local_attachment_id`** (field names pinned in OpenAPI in **5B/5C**) so retries are **idempotent**. If a **local transcript** exists (optional future STT), **`capture_transcripts`** is created/updated with **`transcript_provenance`** ∈ **`mobile_local_stt`** \| **`mobile_direct_provider_stt`** and appropriate **`transcript_status`**. If **no** local transcript, **before** **`POST …/transcribe`**: **no** transcript row **or** **`transcript_status=pending`** only — **`unavailable`**/**`failed`** apply **after** a transcription attempt (§12.4), not while simply waiting.

**Non-goals (MVP):** Background Sync, SW-mediated upload, **multi-GB** resumable uploads.

**CSRF / cookie auth:** Lumogis Web mutations use **Bearer** access tokens on `/api/v1/*`; **cookie-only `POST` without Bearer** remains subject to `require_same_origin` on refresh — capture routes **must** stay **Bearer-only** like other v1 façades so Share Target / future forms cannot become an accidental CSRF surface without an explicit design. <!-- SELF-REVIEW: D5 — captured CSRF posture for future `share_target` POST. -->

---

## 8. Review / classification before indexing

**Recommended safe default:**

1. **Save (server)** → row in **`captures`** with **`status = pending`** (or **`failed`** when promotion/indexing fails — recoverable per §10 / §12.3). **Server statuses:** **`pending` \| `failed` \| `indexed`** only — **`draft` is client/IndexedDB only** (§4). **Attachments** + **transcripts** use **child** tables.
2. **Transcription** (voice, **server reachable**) → after audio is a **server** **`capture_attachment`**, user triggers **`POST …/transcribe`**; handler calls **`SpeechToText` facade** (foundation plan) — **not** required while server was unreachable (local audio only). **Offline** capture does **not** require STT at record time.
3. **Index / Add to memory** → user **reviews** combined textual content (§9); server transitions to **`indexed`**, builds **`notes.text`** from that reviewed bundle, upserts **`conversations`** with the **MVP payload contract** in §9 — **no** `documents` upsert; queues **`entities_extract`**; fires **`Event.NOTE_CAPTURED`** (or equivalent) for graph **`Note`** — **same ordering concern as `projection`**: Postgres + Qdrant + hook failures must leave **`captures.status`** recoverable (`failed` + last error) and **must not** claim `indexed` until durable commit criteria are met. <!-- SELF-REVIEW: D2/D4 -->
4. **Discard** → delete or tombstone per retention policy (metadata + on-disk media + transcript rows — §10).

**Do not** auto-run heavy extraction on every autosave unless product signs an explicit “aggressive” default **and** UX discloses it.

**Provenance:** Postgres **`notes.source = lumogis_web_capture`** on index insert — details in **§9**.

**Audit:** Consider `write_audit` on **index** and **delete** (mirror other user data mutations).

---

## 9. What is indexed immediately vs staged

| Action | Indexed / extracted? |
|--------|----------------------|
| **Local draft** | **No** |
| **Sync to server `captures`** | **No** Qdrant/graph from capture row alone |
| **`POST …/transcribe`** (Capture) | **No** indexing — on **success**, updates **`capture_transcripts`**; **503** + foundation code when STT off — **no** **`complete`** empty row (§12.4) |
| **`POST …/index`** | **Yes** — **personal `notes` row** + **`conversations`** upsert per payload table below + entity/graph pipeline; **no** `documents` in MVP |

**Combined textual content (MVP — indexing input only):** Build **one** string (implementer chooses deterministic join order and separators) from **user-reviewed** fields only:

| Capture flavour | What enters **`notes.text` / embed `summary`** |
|-----------------|-----------------------------------------------|
| **Text** | `captures.text` (and `title` if present) |
| **URL** | `title`, `text` (user note), **`url`** as metadata — **not** scraped page body |
| **Voice** | **Approved `capture_transcripts.transcript_text`** (user may edit before index) |
| **Photo** | **`captures.text`** as **caption** only — **no** image bytes, **no** OCR in MVP |

If several fields coexist on one capture, **concatenate** `text`, `title`, `url`, **`transcript_text`**, caption — **dedupe** empty parts.

**Provenance (Postgres `notes`):** Set **`source = lumogis_web_capture`** on insert (distinct from migration default **`quick_capture`** where product wants traceability). Keep **`captures.note_id`** (or audit JSON) linking back to the staging row.

**Qdrant `conversations` payload (MVP — pinned):** On index, embed the **same** combined string as **`note.text`**. Upsert payload fields **must** include:

| Field | Value |
|--------|--------|
| `session_id` | `str(note_id)` |
| `summary` | `note.text` |
| `user_id` | owner |
| `scope` | `"personal"` |
| `note_id` | `str(note_id)` |
| `source` | `"lumogis_web_capture"` |

This matches **`retrieve_context()`** (`services/memory.py`), which reads **`session_id`** + **`summary`** only. **MVP does not** upsert the **`documents`** collection; **`GET /api/v1/memory/search`** (semantic search) therefore **does not** surface indexed captures until **FP-TBD-5.1** (search parity) ships.

**Prior “options (a/b/c)” retired:** Search/conversations parity is **out of MVP** except chat context via **`retrieve_context`**; **FP-TBD-5.1** owns **`memory/search`** / **`documents`** follow-up.

**Tests:** **5G** asserts Qdrant payload keys above (or integration test via `retrieve_context` hit).

---

## 10. Isolation, permissions, retention, deletion, export

| Topic | Approach |
|--------|-----------|
| **Isolation** | All SQL `WHERE user_id = %s` from JWT; 404 on cross-tenant id probe |
| **Permissions** | Standard user: own captures only; **no** capture admin queue |
| **Retention** | Default **indefinite** until user deletes. Optional operator TTL (**`CAPTURE_MAX_PENDING_DAYS`**) is **deferred** — no automated pruning job in early chunks; document only when/if a later chunk introduces pruning. <!-- Hardening 2026-04-29: avoid implying 5A ships retention sweeps. --> |
| **Deletion** | **Local:** outbox discard wipes IDB rows + **voice Blobs** + text payloads. **Server:** see below. |
| **Deletion (server) — capture** | **`DELETE /api/v1/captures/{id}`** when **`status ∈ {pending, failed}`**: delete **`captures`** row, **`capture_attachments`** metadata, **`capture_transcripts`** rows, and **on-disk files** under **`LUMOGIS_DATA_DIR/captures/...`**. When **`status = indexed`**, **`409 Conflict`** + **`indexed_capture_requires_memory_delete`** (same wire shape as hardening). **No** cascade to **`notes`** / Qdrant / entities / graph — **FP-TBD-5.5**. |
| **Deletion (server) — attachment** | **`DELETE …/attachments/{attachment_id}`** allowed only while capture **`pending`/`failed`** (not **`indexed`**). Removes metadata + file; transcript row(s) for that **`attachment_id`** — **failed STT does not block** keeping audio until user deletes attachment or capture. |
| **Export** | **`user_export`**: register **`captures`**, **`capture_attachments`**, **`capture_transcripts`** in **`_USER_EXPORT_TABLES`** (JSON/CSV as per existing export). **Binary media:** include files in ZIP under **`captures/media/`** (**preferred**) — paths + manifest consistent with metadata export; **if** first implementation pass omits binaries, use **`omissions`** with explicit rationale and **FP-TBD-5.11** (less ideal). |

---

## 11. Existing codebase inventory (integration points)

### 11.1 Server — ingestion / memory / notes

| Asset | Location / behaviour |
|-------|---------------------|
| **Folder ingest** | `routes/data.py` `POST /ingest` → `batch_queue.enqueue(… kind="ingest_folder")` — **path on server filesystem**, not browser upload |
| **File ingest** | `services/ingest.py` `ingest_file` / `ingest_folder` — **`file_index`**, Qdrant **`documents`**, per-user scoped |
| **Entity extraction queue** | `routes/data.py` `POST /entities/extract` → `entities_extract`; payload `evidence_type` **`Literal["SESSION","DOCUMENT"]`** only (`services/batch_handlers/entities_extract.py`) |
| **Notes table** | `postgres/migrations/003-sessions-notes-audio-graph-tracking.sql` — `source` default **`quick_capture`**; scope columns from **`013-memory-scopes.sql`** |
| **Note projection** | `services/projection.py` `project_note` — INSERT **projection** `notes` + Qdrant **`conversations`** for shared/system |
| **Publish personal → shared** | `routes/scope.py` `/api/v1/notes/{id}/publish` |
| **Graph hook** | `Event.NOTE_CAPTURED` / `on_note_captured` — wired for graph service; orchestrator does not need duplicate handler if **webhook dispatcher** already fires on promotion |
| **Memory search** | `routes/api_v1/memory.py` → `semantic_search` → Qdrant **`documents`** only |
| **Chat context** | `services/memory.py` `retrieve_context` → Qdrant **`conversations`** |
| **Capture routes** | **`routes/api_v1/captures.py`**: stubs; **5B–5C** + **5D** (transcribe **via verified STT foundation** only) + UI **5E** per **§12** — **`POST /upload`** remains **501**. **No** Phase 5 STT adapter/sidecar work. |

**Gap:** No shipped REST handler inserts a **personal** `notes` row for QuickCapture; Phase **5** **index** step should centralise that INSERT (+ vector + hooks) to avoid duplicating `projection` logic incorrectly.

### 11.2 Client — PWA baseline

| Asset | Behaviour |
|-------|-----------|
| **`sw.ts`** | Precache + push/click only — **no** `runtimeCaching` |
| **`manifest.webmanifest`** | **No** `share_target`; **shortcuts** Chat + Search only |
| **`drafts.ts`** | `makeCaptureDraftKey` **reserved** |
| **`vite.config.ts`** | `injectManifest`; **`manifest: false`** |

### 11.3 User export

| Asset | Behaviour |
|-------|-----------|
| **`services/user_export.py`** | `_USER_EXPORT_TABLES` includes **`notes`**; **`webpush_subscriptions`** in **`_OMITTED_USER_TABLES`**. Add **`captures`**, **`capture_attachments`**, **`capture_transcripts`** + **ZIP `captures/media/`** policy per **§17**. |

---

## 12. Backend architecture (proposed)

### 12.1 Data model — three tables (preferred over generic `capture_items`)

**Why three tables:** Explicit **`capture_attachments`** vs **`capture_transcripts`** keeps queries and FK intent clear; avoids polymorphic JSON blobs for MVP.

**`captures`** — parent row (staging / indexed lifecycle):

| Column | Responsibility |
|--------|----------------|
| **`id`** | PK (UUID) |
| **`user_id`** | Owner — repeated on children for isolation (§10) |
| **`status`** | **`pending` \| `failed` \| `indexed`** — **only** these values on **`captures`** (MVP freeze); CHECK in migration |
| **`capture_type`** or **`primary_kind`** | Product discriminator (`text`, `url`, `photo`, `voice`, `mixed`) — optional if inferred from attachments |
| **`title`**, **`text`**, **`url`** | User-visible fields; **`text`** doubles as **caption** for photos |
| **`local_client_id`** | Client idempotency key — **wire field** in create body/OpenAPI is **`client_id`** (UUID); **DB column** stays **`local_client_id`** for clarity in SQL |
| **`note_id`** | Nullable → **`notes`** after index |
| **`source_channel`** | e.g. `lumogis_web` |
| **`created_at`**, **`updated_at`** | Server timestamps |
| **`captured_at`**, **`synced_at`**, **`indexed_at`** | Optional audit (nullable until event) |
| **`last_error`** | Nullable text on **`failed`** |
| **`tags`** | Optional **`TEXT[]`** / JSONB — align with `CaptureTextRequest` |

**`capture_attachments`** — binary metadata (**image \| audio**):

| Column | Responsibility |
|--------|----------------|
| **`id`**, **`capture_id`**, **`user_id`** | PK + FK **`capture_id` → `captures(id)` ON DELETE CASCADE** (recommended); **`user_id`** denormalized |
| **`attachment_type`** | `image` \| `audio` (check constraint) |
| **`storage_key`** / path fragment | Server-relative segment under **`LUMOGIS_DATA_DIR`** tree (§12.2) |
| **`original_filename`** | Sanitized echo for display only |
| **`mime_type`**, **`size_bytes`**, **`sha256`** | Integrity + quotas |
| **`created_at`** | Insert time |
| **`metadata`** | JSONB (dimensions, duration — optional) |
| **`processing_status`** | e.g. `stored` / `failed` — distinct from transcript lifecycle |
| **`client_attachment_id`** | Optional — stable id from client (**`local_attachment_id`**) for **idempotent** **`POST …/attachments`** replay; **UNIQUE (user_id, capture_id, client_attachment_id)** partial where **NOT NULL** |

**`capture_transcripts`** — STT output for **audio** attachments:

| Column | Responsibility |
|--------|----------------|
| **`id`**, **`capture_id`**, **`attachment_id`**, **`user_id`** | FK **`attachment_id` → `capture_attachments(id)`**; app layer enforces **`attachment_type = audio`** |
| **`provider`**, **`model`** | Echo **STT result** from foundation response — not Capture-owned config |
| **`transcript_text`** | Nullable until complete |
| **`transcript_status`** | **`pending` \| `processing` \| `complete` \| `failed` \| `unavailable`** — **exactly** this set (MVP freeze); pin CHECK in migration + OpenAPI |
| **`transcript_provenance`** | **`server_stt`** (default, from Lumogis foundation) \| **`mobile_local_stt`** \| **`mobile_direct_provider_stt`** — latter two when sync uploads a **client-generated** transcript (**FP-TBD-5.17**) |
| **`language`**, **`confidence`** | Optional |
| **`created_at`**, **`updated_at`** | |
| **`error`** | Nullable on **`failed`** |

**Rules:** A capture may have **text + URL + multiple attachments**; **failed transcription does not delete** the audio blob.

**DDL style:** **`user_id`** on children matches **`notes.user_id`** (TEXT, **no FK** to **`users`** unless a house-wide FK programme lands) — consistent with migration **010** commentary.

**Idempotency:** **`local_client_id`**: nullable; **`UNIQUE (user_id, local_client_id)`** partial index where `local_client_id IS NOT NULL`.

**`captures.text` validation:** Allow **empty** `text` when **`url` IS NOT NULL** or **at least one** attachment exists — avoid forcing dummy text for photo-only captures.

**`note_id`:** optional **`REFERENCES notes(note_id) ON DELETE SET NULL`** — verify **`user_export`** import order.

**Alternative rejected for MVP:** single **`capture_items`** polymorphic table.

### 12.2 Media storage policy

**Location:** **`CAPTURE_MEDIA_ROOT`** defaulting to **`{LUMOGIS_DATA_DIR}/captures`**. Per-object path: `{root}/{user_id}/{capture_id}/{attachment_id}/` + **server-generated filename** (UUID + safe extension).

**Postgres:** Metadata only — **no** raw upload bytes in rows for MVP.

**Ingest hygiene:** Sanitize display names; **MIME allowlist** + **magic-byte** sniff where feasible; **reject** mismatch; **compute `sha256`**; **`Path.resolve()` + `is_relative_to(root)`** — **no** path traversal; **enforce size limits** during streaming write.

**Serving:** **No** anonymous static URLs. **`GET …/attachments/{attachment_id}`** streams with **`require_user`**. Short-lived signed URLs = **optional** follow-up only.

**MVP limits (defaults — tune via env, not TBD):**

| Kind | Max | MIME |
|------|-----|------|
| **Image** | **10 MiB** | `image/jpeg`, `image/png`, `image/webp` |
| **Audio** | **25 MiB** | `audio/webm`, `audio/mp4`, `audio/mpeg`, `audio/wav` — drop any type **MediaRecorder** cannot produce without QA |

Oversize → **`413`**; bad MIME → **`415`**.

### 12.3 Qdrant / vector (personal index promotion)

| Field | Contract |
|--------|----------|
| **Collection** | `conversations` (same as session summaries + published note mirrors) |
| **Point id** | New helper in `services/point_ids.py`, e.g. **`note_conversation_point_id(user_id, note_id)`** — **must** namespace `user_id` per B11 in `point_ids.py` module docstring — **no** ad-hoc `uuid5` at call sites. <!-- SELF-REVIEW: D2/D3 — B11 deterministic ids. --> |
| **Vector** | `config.get_embedder().embed(...)` — dimension is whatever the deployment embedder uses (same as `projection._embed_for_projection`). |
| **Payload** | **Exactly** the MVP contract in **§9** (`session_id`, `summary`, `user_id`, `scope`, `note_id`, `source`); **no** `documents` writes in MVP. |

**Partial failure:** If Qdrant upsert fails after Postgres `notes` INSERT — mark capture **`failed`**, log WARNING, expose **retry** path; consider **best-effort Qdrant delete** on rollback if a transaction wrapper is not available (metadata store autocommit — see `projection` commentary). <!-- SELF-REVIEW: D4 -->

### 12.4 API routes

**MVP create boundary (frozen):** **`POST /api/v1/captures`** (and **`POST /captures/text`**) create **personal** staging captures only. **No** shared/system capture create on the capture API; **no** short-circuit to **`projection` shared** paths from Create. After **`POST …/index`**, **`/api/v1/notes/{id}/publish`** remains the **only** supported path to widen visibility (**FP-TBD-5.3** = this rule in DTO/schema — reject or omit non-personal `scope` on create).

**Create / alias:**

| Route | Semantics |
|--------|-----------|
| **`POST /api/v1/captures`** | **Canonical** create. |
| **`POST /api/v1/captures/text`** | **Thin alias** — same handler + response as **`POST /captures`**. |
| **`POST /api/v1/captures/upload`** | **`501`** — error body directs to **`POST /api/v1/captures/{id}/attachments`** (multipart, after create). |

**OpenAPI:** Regenerate **`openapi.snapshot.json`** + **`npm run codegen`** when capture routes/schemas change (**5A–5C** minimum).

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/captures` | Create **personal** parent row (`CaptureTextRequest` + optional `url`, `client_id`, … — §12.4 MVP create boundary) |
| `GET` | `/api/v1/captures` | List mine (pagination) |
| `GET` | `/api/v1/captures/{id}` | Detail (+ attachment + transcript summaries in DTO) |
| `PATCH` | `/api/v1/captures/{id}` | Update **pending** fields |
| `DELETE` | `/api/v1/captures/{id}` | **Pending/failed** only; **indexed** → **409** (§10) |
| `POST` | `/api/v1/captures/{id}/attachments` | **Multipart** — creates **`capture_attachments`** + writes file; **does not** index |
| `GET` | `/api/v1/captures/{id}/attachments/{attachment_id}` | **Authenticated** download |
| `DELETE` | `/api/v1/captures/{id}/attachments/{attachment_id}` | **Pending/failed** capture only (§10) |
| `POST` | `/api/v1/captures/{id}/transcribe` | Start STT for **`attachment_id`** in body **or** all **pending** audio on capture if omitted |
| `POST` | `/api/v1/captures/{id}/index` | **Promote** reviewed combined text (§9) → **`notes` + `conversations`** |

**`POST …/attachments`:** **`multipart/form-data`** — field names pinned in OpenAPI: at minimum **`file`** (binary) and optional **`client_attachment_id`** (string UUID from **`local_attachment_id`**). **Idempotency:** replay with the same **`(user_id, capture_id, client_attachment_id)`** after success → **`200 OK`** returning the **same** **`attachment_id`** and metadata (mirror capture create). **No** automatic transcription on upload.

**`POST …/transcribe`:** **Capture orchestration** — verify ownership, load attachment bytes from disk, call **`SpeechToText` port** via **`orchestrator/services/speech_to_text.py`** (facade — **same** as **`POST /api/v1/voice/transcribe`**); map **`TranscriptionResult`** → **`capture_transcripts`**. **No** duplicate adapter logic in Capture. Safe to retry on **`failed`** transcripts.

**`POST …/transcribe` — errors (pin in OpenAPI):** **`404`** unknown capture / attachment or wrong **`user_id`**; **`422`** if resolved attachment **`attachment_type ≠ audio`** or body **`attachment_id`** not on this capture; **`409`** if capture **`status = indexed`** (default **no** transcription after promotion). **`502`**/**`503`** for transient failures (disk read, STT runtime).

**`POST …/transcribe` when STT is disabled or unavailable (MVP — frozen):** Align with **`POST /api/v1/voice/transcribe`** — respond **`503 Service Unavailable`** with the **same stable STT error code** (and compatible **`detail`**) as the **verified** foundation. **Do not** return **`200`** with a **`complete`** transcript; **do not** persist **`transcript_status=complete`** with **empty** **`transcript_text`**. Whether a **`processing`/`pending`** row may be left behind on **503** vs **no row** until success — pick **one** behaviour in **5D** and test it (**no** fake **complete** either way). <!-- MVP freeze 2026-04-29: 503 + no empty complete. -->

**Generic push-to-talk (Chat, etc.):** may call **`POST /api/v1/voice/transcribe`** directly with multipart audio — see **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`**.

**`POST …/index`:** **Pin in 5G:** default **require** `transcript_status=complete` for captures with audio **unless** explicit product escape hatch (“index without transcript”).

**Pagination:** `GET /captures?limit=&offset=` — default **20**, max **100**; **`updated_at DESC`**.

**List vs detail payloads:** **`GET /captures`** returns **summary rows** (ids, status, timestamps, optional **counts** of attachments/transcripts); **`GET /captures/{id}`** returns **full** nested attachment + transcript metadata for review. **N+1** on list is acceptable for MVP **if** each row stays O(1) extra fields; avoid per-row heavy joins — batch counts in **5B** when needed. <!-- SELF-REVIEW: D2/D7 — list payload shape + N+1 guard. -->

**Payload caps:** Text **32k**; **`url` ≤ 2048**; **`title` ≤ 256** — **422**.

**Rate limit:** Mirror **`routes/api_v1/approvals.py`** — **~60** mutating ops/min/user; **`transcribe`** may carry higher weight — document in router. **`GET …/attachments/{id}`** (download): authenticated — **inherit** general API / per-route throttling if present; add a **read** quota only if abuse appears.

**Latency:** **`POST …/transcribe`** may run **seconds** — prefer **synchronous `200`** while under deployment **HTTP timeout**; if local STT exceeds that, introduce **`202`** + poll or batch (align with **`CAPTURE_INDEX_TIMEOUT_SEC`** / **`lumogis-speech-to-text-foundation-plan.md`** before **5D** ships). <!-- SELF-REVIEW: D7 -->

**Errors:** **401** / **404** / **422** / **409** / **413** / **415** / **429** / **502** / **503** (transient STT, disk read, downstream I/O).

### 12.5 Runtime configuration (Capture-only `.env`)

Capture **does not** own **`STT_*`** or Whisper model toggles — those live in **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`**. Capture code may **read** STT **availability** (e.g. whether facade is enabled) to set UX hints; **media** limits remain Capture-owned.

| Variable | Default | Purpose |
|----------|---------|---------|
| `CAPTURE_POST_LIMIT_PER_MIN` | `60` | Token bucket for mutating capture routes |
| `CAPTURE_MAX_IMAGE_BYTES` | `10485760` | Image ceiling (10 MiB) |
| `CAPTURE_MAX_AUDIO_BYTES` | `26214400` | Audio ceiling (25 MiB) |
| `CAPTURE_MEDIA_ROOT` | `{LUMOGIS_DATA_DIR}/captures` | Blob root |
| `CAPTURE_MAX_PENDING_DAYS` | *(unset / deferred)* | Optional TTL — **no** worker in early chunks |
| `CAPTURE_INDEX_TIMEOUT_SEC` | *(optional)* | Sync index vs **202** job — measure first |

### 12.6 Capture ↔ Speech-to-Text foundation (integration)

**Foundation status (frozen for Phase 5 planning):** **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`** is **closed and verified** — **STT-1**, **STT-2A**, **STT-2B**, **STT-2C** **complete**. **`POST /api/v1/voice/transcribe`** and the **`SpeechToText`** facade are **production paths**. **Phase 5 Capture must not** implement STT **adapters**, **sidecars**, **env-driven backend wiring** beyond **reading** availability for UX if needed — **5D** only **invokes** the facade from **`POST …/captures/{id}/transcribe`**.

**Connected (server reachable):**

- **Server-backed STT** uses the **shared** **`SpeechToText`** foundation only (**`POST …/captures/{id}/transcribe`** reads bytes, calls facade — §12.4).

**Server unreachable:**

- **No** STT required at record time — **local audio only** (§5). **Transcription** happens **after** user **syncs** audio to Lumogis, then **`POST …/transcribe`** (or optional future **mobile** STT — below).

**Optional future — mobile-capable STT** (**not** Capture MVP default):

- **On-device** or **client direct-to-provider** (explicit opt-in, **no** Lumogis Cloud relay) may produce a **local transcript** object; on sync, **`capture_transcripts`** is populated with **`transcript_provenance`** **`mobile_local_stt`** or **`mobile_direct_provider_stt`** (**FP-TBD-5.15–5.17**). Credential and compliance story align with **mobile cloud fallback** / product STT planning — **not** implemented in Capture MVP.

**Owned by Capture (Phase 5):**

- **Recording / uploading** the **audio** attachment (**client** — gesture-gated; **server** — **`POST …/attachments`**); **local-only** audio in §5.1 **before** sync.
- **Associating** transcript rows with **`attachment_id`** + **`transcript_provenance`**.
- **Transcript review / edit** UX **before** index.
- **Policy** for **`POST …/index`** (e.g. require **`complete`** transcript when audio present — **5G**).

**Owned by Speech-to-Text foundation** (**verified — not Phase 5 work**):

- **`SpeechToText` port**, all **adapters**, **`STT_*`**, **`POST /api/v1/voice/transcribe`**, server-side validation of bytes for **`transcribe()`**.

**Wake word / always-listening** — **FP-TBD-5.13** (see also foundation plan).

### 12.7 Photo / image capture architecture

- **Client:** **`<input type="file" accept="image/*" capture="environment">`** for **new photo** where supported; **same input** **without** `capture` (or explicit gallery affordance) for **existing images** / file picker — **desktop** uses file picker.
- **Server:** **`attachment_type=image`**; optional **caption** in **`captures.text`**.
- **MVP:** **No** OCR / vision / auto-description — index uses **caption + title + URL** only (§9).
- **Privacy:** EXIF policy in §16.

---

## 13. Local outbox / IndexedDB (if offline in scope)

| Item | Proposal |
|------|----------|
| **Text / URL** | **Yes** — structured rows; manual sync (**MVP**) |
| **Voice / audio** | **Yes** — **mobile** QuickCapture when **server unreachable**; **Blob/File** in IndexedDB (or supported persistent storage); shape §5.1 (**MVP**) |
| **Photo / image** | **Deferred** / **online-first** in MVP — **FP-TBD-5.10b** unless product elevates |
| **Cache Storage** | **Never** for capture blobs or API payloads |
| **Caps (defaults)** | e.g. **≤ 10** pending voice clips **or** **≤ 100 MiB** total local voice payload — **whichever stricter**; tune in **5F** |
| **Quota / failure** | If IDB **full** or **unsupported**, show **blocking** error — cannot save locally |
| **Disclosure** | Settings / Capture: **local audio** may reside on device; approximate storage use |
| **Discard** | User can **delete** local voice rows + revoke Blobs |
| **Sync** | **Manual “Sync now”** only — **no** Background Sync |

**Implementation:** Module e.g. **`captureOutbox.ts`**; **`local_capture_id`** = `crypto.randomUUID()` per pending capture; **`local_attachment_id`** per audio blob.

---

## 14. PWA share target / shortcuts

| Item | MVP | Follow-up |
|------|-----|-----------|
| **Manifest shortcut “Quick capture”** | **Recommended** — points to `/capture` or `/capture/new` | — |
| **`share_target`** | **Defer chunk 5H** (not core MVP) | `POST` handler + CSRF-safe pattern; may accept shared **text/URL** before files |
| **Service worker** | **No changes** beyond Phase 4 | No fetch handler |

---

## 15. UX flows (mobile-first, cross-device)

1. **Enter QuickCapture** — FAB, header button, or shortcut (**5H**).  
2. **Compose — online** — text/URL/photo/**voice** per mode; **`POST /captures`** + **`…/attachments`** as today.  
3. **Compose — server unreachable (mobile)** — **text/URL:** save to outbox; **voice:** **record + save local audio** (§5.1); **photo:** **disabled or “requires connection”** (online-first).  
4. **When back online** — surface **pending local** captures (badge/list); user taps **Sync now** (no auto-upload).  
5. **After voice sync** — server has **`captures` + `capture_attachments`**; user may **transcribe** via **`POST …/transcribe`** (shared STT) **or** if **optional** mobile STT existed, transcript may already sync — §12.6.  
6. **Transcript review** — edit before **Index** when transcript exists.  
7. **Index** — **Add to memory** per §9; disclose **`retrieve_context`** vs **`memory/search`** (**FP-TBD-5.1**).  
8. **Discard** — local or server pending per §10 / outbox rules.

**A11y:** ≥ **44px** targets; **`aria-live`** for sync errors; keyboard-safe layout per Phase 2 patterns.

---

## 16. Security / privacy boundaries

| Risk | Mitigation |
|------|------------|
| **Cross-user access** | Authz + user-scoped queries |
| **Body `user_id` trust** | Ignore for non-admin; admin capture-on-behalf **off** unless explicit |
| **XSS from captured text** | React text nodes; never `dangerouslySetInnerHTML`; sanitize display where markdown might arrive later |
| **Open redirect via URL** | Allowlist schemes (`http`/`https`); block `javascript:` |
| **SW caching API secrets** | **Forbidden** — keep Phase 3 policy |
| **Logging** | Log capture **ids** only; truncate previews |
| **Microphone / camera abuse** | **Permissions only** from **direct user gestures** (tap record, tap camera); **no** idle capture |
| **Upload abuse** | **Size limits** (§12.2); **MIME allowlist**; **magic-byte** sniff when feasible; **server-generated** filenames; **no** path traversal |
| **Media exfiltration** | **Authenticated** download routes only; **no** public `/static` capture blobs; **`Content-Disposition: attachment`** (or equivalent) on **`GET …/attachments/...`** to reduce **inline** execution assumptions in edge browsers |
| **EXIF / location leakage** | **Strip EXIF on ingest** when a lightweight library is available; **if** not ship-ready in **5C**, store original but **do not** surface EXIF in UI or index text — track strip-hard requirement as **FP-TBD-5.12**. |
| **Uploads** | Same as §12.2 — **413** / **415** |
| **Indexing consent** | **Explicit** index action or documented default + Settings toggle (**FP-TBD-5.2**) |

---

## 17. User export / deletion (recommendation)

- **Metadata:** Export **`captures`**, **`capture_attachments`**, **`capture_transcripts`** alongside **`notes`** — **pending** and **indexed** staging rows are user data.  
- **Media binaries (preferred):** Include files in ZIP under **`captures/media/`** with stable relative paths referenced from JSON — **FP-TBD-5.11** only if first PR must omit binaries (explicit **`omissions`** rationale).  
- **Deletion:** Per **§10** — pending capture delete removes Postgres children + disk files; **indexed** capture → **409** until **FP-TBD-5.5**.

---

## 18. Tests (plan)

### Backend

- CRUD isolation alice/bob  
- Idempotent `POST` with **`client_id`** (body)  
- **Idempotency conflict:** second `POST` with same **`client_id`** but **different** `text`/`title`/`url` → **`409`** `idempotency_key_conflict` (**`Idempotency-Key`** header **not** in MVP)  
- **Partial sync:** capture created, attachment fails, then **retry** `POST …/attachments` with same **`client_attachment_id`** → **`200`** same **`attachment_id`**  
- **`POST …/transcribe`:** **422** non-audio attachment; **409** when capture **`indexed`**; **STT disabled/unavailable** → **`503`** + foundation stable code (**no** **`complete`** empty transcript — §12.4); **on success**, facade + **`capture_transcripts`** lifecycle (**failed** → retry)  
- **Personal-only create:** **`POST /captures`** with non-personal **`scope`** (if exposed) → **`422`** or **`409`** per OpenAPI — §12.4  
- **Upload** image — allowed MIME + under limit → **200**; disallowed MIME / oversize → **415** / **413**  
- **Upload** audio — same  
- **STT foundation** — **`POST /api/v1/voice/transcribe`** tested per **`lumogis-speech-to-text-foundation-plan.md`** (not duplicated here)
- Alice **cannot** read Bob’s attachment / transcript / download URL  
- **`DELETE` pending** capture — attachment metadata + **file removed** from disk  
- **`DELETE` indexed** capture → **409** `indexed_capture_requires_memory_delete`  
- **`user_export`** includes `captures`, `capture_attachments`, `capture_transcripts` **and** **`captures/media/`** policy (**test_user_export_tables_exhaustive** gate)  
- **`POST /api/v1/captures/text`** alias wire shape matches **`POST /captures`**  
- Second **`POST …/index`** when **`indexed`** → **409**  
- **`entities_extract`** evidence convention (**FP-TBD-5.4**)

### Frontend

- Image **camera / file** input path  
- **Audio:** permission prompt flow **mocked** in tests; **`MediaRecorder` unsupported** state  
- Upload progress + error UI  
- Transcript review + edit before **Index**  
- **Index** uses reviewed transcript  
- Offline / server-unreachable: **voice local save** + **outbox UX**; **quota exceeded** safe failure; **sync** manual

### PWA

- `npm run verify:pwa-dist` — refresh **`manifest.test.ts`** if **5H** edits shortcut / `share_target`

---

## 19. Documentation updates (per chunk)

| Doc | When |
|-----|------|
| **`docs/architecture/cross-device-web-phase-5-capture-plan.md`** | Living extraction — update during implementation |
| **`clients/lumogis-web/src/pwa/README.md`** | After IDB outbox lands |
| **`clients/lumogis-web/README.md`** | Capture UX + offline statement |
| **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`** | **Verified** — **STT-1**/**2A**/**2B**/**2C**; Capture **5D** cross-link only |
| ***(maintainer-local only; not part of the tracked repository)*** | Phase 5 pointer + STT dependency |
| **Follow-up portfolio** | **Do not** edit manually per workspace rules — promote **§21 FP-TBD-5.*** rows via **`/verify-plan`** / portfolio skill only. <!-- SELF-REVIEW: D8 — aligned doc reference with new §21 title. -->

---

## 20. Implementation chunks (recommended)

Parent plan **Pass 5.1–5.3** (“wire into `services.ingest`”, “background-sync queue”) are **superseded**: browser capture uses **multipart + `captures` family** — **not** **`ingest_folder`**; **no** Background Sync.

**Speech-to-Text foundation** is **closed and verified** (**STT-1**, **STT-2A**, **STT-2B**, **STT-2C** per **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`**). **5D** **consumes** the existing facade — **no** Phase 5 work on STT adapters or sidecars. **Local-only** voice recording (**5E**/**5F**) does **not** need STT on the device.

| Chunk | Scope | Likely files | Acceptance criteria | Validation | Non-goals |
|-------|--------|--------------|---------------------|--------------|-----------|
| **5A** | **Migrations** (**`captures` / `capture_attachments` / `capture_transcripts`** — statuses frozen §12.1); **DTO + OpenAPI skeleton** (schema for new models/paths; handlers may stay **501** until **5B**/**5C**); **service + media storage foundation** (e.g. `services/captures.py` stub, `services/media_storage.py` or inline) with **path-safety + MIME/size guard** **unit tests** touching the storage layer deliverable in this chunk | `postgres/migrations/*.sql`, `services/captures.py`, `services/media_storage.py` (or inline), `models/api_v1.py`, `routes/api_v1/captures.py` | Migrations apply; **pytest** proves path safety / allocation under **`CAPTURE_MEDIA_ROOT`** (and related guards) per plan; OpenAPI snapshot + codegen **if** schemas change | `pytest` subset | **Full CRUD (5B)**; **multipart upload (5C)**; **`POST …/transcribe` (5D)**; **`POST …/index` (5G)**; **QuickCapture UI (5E)**; **local outbox (5F)**; **any STT adapter/sidecar** |
| **5B** | **Text/URL CRUD** — `POST/GET/PATCH/DELETE /api/v1/captures`, **`POST /text`** alias | `routes/api_v1/captures.py`, `test_api_v1_captures.py`, OpenAPI | Idempotency + isolation | `pytest` + codegen | Attachments, index |
| **5C** | **Attachment API** — `POST/GET/DELETE …/attachments` | Same + file I/O tests | **413/415**; alice/bob; **client_attachment_id** idempotency | `pytest` | STT |
| **5D** | **Capture voice integration:** **`POST …/captures/{id}/transcribe`** → **verified** **`SpeechToText`** facade only; **`capture_transcripts`** lifecycle | `routes/api_v1/captures.py`, `services/captures.py` | **200** + real transcript when STT available; **`503`** + foundation stable code when STT disabled/unavailable; **never** **`complete`** + empty text (§12.4) | `pytest` | **STT adapters**, **sidecars**, **`STT_*`**, Whisper binaries |
| **5E** | **QuickCapture UI** — text/link/photo + **voice online and offline (local save)**; transcript review when transcript exists | `src/features/captures/*`, nav | Connected + **server-down** voice record path | `npm test` | Photo offline, share target |
| **5F** | **Local outbox:** **text/URL** + **voice audio** staging (**§5.1**, caps §13) + **manual sync** | `captureOutbox.ts`, README | Quota failure UX; sync idempotency | Vitest | Background Sync, photo offline |
| **5G** | **`POST …/index`** — **`notes` + `conversations`** (**no** `documents`) | `services/captures.py`, `point_ids.py`, handlers | **`retrieve_context`** | Integration tests | `documents` |
| **5H** | **PWA** shortcut / optional **`share_target`** | `manifest.webmanifest` | `verify:pwa-dist` | `npm run verify:pwa-dist` | SW fetch changes |
| **5I** | **`user_export`** + docs + **`/verify-plan`** | `user_export.py`, architecture docs | Export gate | `compose-test` | Tauri, fallback |

**Chunk ordering rationale:** **5A → 5B → 5C → 5D** (STT foundation **already verified**); **5E** + **5F** same milestone for offline voice; **5G–5I** as before. Ordering **5E** before **5F** in the table is **dependency-light** only. <!-- STT parallel superseded — foundation closed. -->

---

## 21. Follow-up register (single deferred list)

Per cross-device review discipline: **all** unresolved items live here (no duplicate “Open questions” top-level sections). <!-- SELF-REVIEW: D8 -->

| ID | Item | Owner / phase |
|----|------|----------------|
| **FP-TBD-5.1** | **`memory/search` parity:** surface indexed captures (**`documents`** and/or **`semantic_search`**) — **explicitly out of MVP** | Follow-up |
| **FP-TBD-5.2** | **Default index policy:** always explicit vs Settings “index on sync” | Product |
| **FP-TBD-5.3** | **Personal-only capture create (MVP — frozen):** OpenAPI/DTO **rejects or omits** non-personal `scope` on **`POST /captures`**; sharing only via **`/api/v1/notes/{id}/publish`** after index — §12.4 | **5B** |
| **FP-TBD-5.4** | **`entities_extract` evidence_type:** extend Literal vs reuse **`DOCUMENT`** | Follow-up (not addressed in **5G**) |
| **FP-TBD-5.5** | **Explicit memory purge:** remove **`notes` + Qdrant + entity/graph`** for an indexed capture — **not** implemented as cascade from **`DELETE /captures`** (**409** until this flow exists) | Post-MVP |
| **FP-TBD-5.6** | **Admin capture-on-behalf:** mirror **`POST /ingest`** or forbid | Product |
| **FP-TBD-5.7** | **Daily quota:** 100/day soft cap enforcement vs defer | **5B** |
| **FP-TBD-5.8** | **LibreChat** deprecation dependency check | Docs |
| **FP-TBD-5.9** | **OCR / image understanding** — out of MVP | Follow-up |
| **FP-TBD-5.10** | **Offline voice/audio local staging (mobile)** — **MVP requirement** (**IndexedDB**, caps, manual sync) | **5E / 5F** |
| **FP-TBD-5.10b** | **Offline photo/image IDB staging** — **not** MVP; online-first for photos | Post-MVP unless product elevates |
| **FP-TBD-5.11** | **Export ZIP includes `captures/media/`** binaries — **5I** (**closed** for MVP); omission path was fallback only | **5I** |
| **FP-TBD-5.12** | **EXIF strip** — harden ingest if **5C** ships without strip | Follow-up |
| **FP-TBD-5.13** | **Wake word / always-listening / openWakeWord / native daemon** — **out of Phase 5**; companion/service programme | Future |
| **FP-TBD-5.14** | **Cloud STT opt-in** (third-party keys + disclosure) — **not** local-first default | Future product |
| **FP-TBD-5.15** | **On-device / WASM STT** on client while server unreachable | Future |
| **FP-TBD-5.16** | **Direct-to-provider STT** from mobile client (explicit opt-in, credential UX, **no** Lumogis Cloud relay) | Future; see mobile fallback programme |
| **FP-TBD-5.17** | **Local transcript → server `capture_transcripts`** (API + **`transcript_provenance`**) when **5.15/5.16** ship | Follow-up |

### 21.1 Chunk implementation status (as-built)

Living status for implementers; keep aligned with §20 chunk table.

| Chunk | Status | Closeout notes |
|-------|--------|----------------|
| **5A** | Shipped | Migrations + DTOs + media helpers + route skeletons. |
| **5B** | **Closed** (2026-04-30) | Capture **metadata CRUD** only (`POST/GET/PATCH/DELETE` + `POST /text`). **No** attachments, STT, index, or Web UI. **`POST /upload`** remains **501** per §12.4 (canonical surface is `POST /{id}/attachments`). |
| **5C** | **Closed** (2026-04-30) | **In scope:** `POST/GET/DELETE …/attachments` only — **no** STT/transcription, Qdrant/index, Agentic Core, Team/Owner Inbox, or Lumogis Web UI. Bytes under `CAPTURE_MEDIA_ROOT/{user_id}/{capture_id}/{attachment_id}/blob.{ext}`; storage keys relative; MIME/size + path traversal guards; cross-user **404**; indexed captures block attach/delete (**409**). **`POST /upload`** remains **501**; **`POST …/transcribe`** is **5D**; **`POST …/index`** is **5G**. |
| **5D** | **Closed** (2026-04-30) | **`POST /api/v1/captures/{capture_id}/transcribe`** is **live** — uses the **verified SpeechToText** facade (`transcribe_blob`) **only**; **no** new STT adapters, sidecars, or **`STT_*`** wiring. Persists **`capture_transcripts`**. **Out of scope:** Agentic Core, Team/Owner Inbox. **Indexing** is **5G** (`POST …/index`). |
| **5E** | **Closed** (2026-04-29) | **`/capture` route and nav** are live. **QuickCapture:** text/link create (`POST /api/v1/captures`), photo and audio upload when online, **`POST …/transcribe`**, transcript display; **Add to memory** wired in **5G** (explicit tap → **`POST …/index`**). **Offline / server-unreachable:** honest UX via + banner + copy — **no silent sync**. **5E** only persisted **local text draft** fields via `makeCaptureDraftKey("__quickcapture__")`; **no** full IndexedDB outbox (**5F**). **Backend/OpenAPI:** unchanged in 5E alone. **MediaRecorder** start/stop in real browsers remains **manual QA** if not automated. |
| **5F** | **Closed** (2026-04-29) | **Local outbox** in **`captureOutbox.ts`**: **text/URL** + **voice** (local `Blob`/`File`) via **idb-keyval only** — **no** Cache Storage, **no** Service Worker API caching for payload, **no** Background Sync, **manual sync only** (nothing on save/transcribe/automatic replay). **Sync** → `POST /api/v1/captures` with `client_id` = local capture id, then `POST …/attachments` with `client_attachment_id`; **no** transcription during sync; **success** removes the local row + blob; **failure** keeps the item with **`last_error`**. **Caps:** ≤10 pending voice clips, ≤100 MiB total pending voice bytes, ~25 MiB/clip. **Discard** removes metadata + audio blob. **Offline photo** staging **deferred**; **no** offline STT; **no** indexing/Qdrant from the client; **no** Agentic Core, Team Inbox, or Owner Inbox. **5F** did **not** change backend/OpenAPI. |
| **5G** | **Closed** (2026-04-30) | **`POST /api/v1/captures/{capture_id}/index` is live.** **Add to memory** = explicit user action only (no auto-index on save, upload, sync, or transcription). Inserts **one** personal **`notes`** row (`source=lumogis_web_capture`, `scope=personal`); **Qdrant `conversations` collection only** — **`documents` not touched.** Payload: `session_id`, `summary`, `user_id`, `scope`, `note_id`, `source`. Combined text order: title, text, `URL:`, complete non-empty transcripts; **audio** requires complete transcripts per attachment (**422** `capture_transcript_required`); empty **422** `capture_no_indexable_content`; **409** if already indexed. **Failed index:** best-effort note delete, capture **`failed`** + **`last_error`**, **503** `index_memory_unavailable`, retry from **`failed`**. **`Event.NOTE_CAPTURED`** + **`write_audit`** (`capture_index`, id-only summaries). **Lumogis Web:** Add to memory wired when valid and online; **offline indexing disabled.** **PATCH** clears fields when JSON **`null`** is explicitly sent (`model_fields_set`). |
| **5H** | **Closed** (2026-05-01) | **Shortcut:** “Quick capture” → **`/capture`** (existing Chat/Search shortcuts unchanged). **`share_target`:** **`GET`** **`/capture`** with `title` / `text` / `url` — QuickCapture **prefills only**; user must tap **Save** / local save / **Add to memory**. **No** **POST** share target, **no** file/media share, **no** SW **`fetch`** handler, **no** SW Cache API / runtime caching for shares, **no** Background Sync, **no** silent server mutation / sync / index. **Backend/OpenAPI unchanged.** |
| **5I** | **Closed** (2026-05-01) | **`user_export`:** **`captures`**, **`capture_attachments`**, **`capture_transcripts`** already in **`_USER_EXPORT_TABLES`**; **5I** adds **capture attachment binaries** in the ZIP under **`captures/media/{storage_key}`** (same relative layout as **`CAPTURE_MEDIA_ROOT`**), plus **`captures/media/index.json`** and manifest **`capture_media`** (**omissions** for missing/oversized/bad keys). **Import** rewrites **`storage_key`** / **`user_id`** for **`capture_attachments`** and restores blobs after the Postgres transaction. **`SectionSummary.kind`** adds **`capture_media`** (receipt / dry-run) — **OpenAPI snapshot** + **web codegen** refreshed. Verification: **`make compose-test`**, web **lint/build** as touched. **No** new capture product features.

**5B follow-up (scope filtering, MVP):** `GET /captures` default/`personal` returns all rows for `user_id`; `shared`/`system` return an empty page until family/shared capture semantics exist (distinguish personal vs shared/Family vs system).

**5C follow-ups:** EXIF strip (**FP-TBD-5.12**); export **`captures/media/`** binaries — **5I** (was **FP-TBD-5.11** if omitted); optional **`200`** OpenAPI body schema for idempotent attachment replay to match **201** (regenerate `openapi.snapshot.json` after router tweaks); **`capture_type`** after last attachment delete (**deferred**); scope/list **MVP** behaviour (**5B** follow-up aligns).

**5D follow-ups:** Qdrant / Agentic Core / inboxes remain out of scope for **5D**; indexing shipped in **5G**.

**5E / 5F boundary (2026-04-29):** **5E** shipped **connected** QuickCapture (`/capture`), **local text draft** only while editing, and messaging that **full** outbox is **5F**. **5F** shipped **IndexedDB** outbox + **manual sync** (no silent upload).

**5F follow-ups (explicit):** “Server unreachable” while **`navigator.onLine`** / API health says up — tighter detection deferred; **MediaRecorder** gesture UX — manual QA if not fully automated; **offline photo** staging remains **FP-TBD-5.10b** / deferred.

**Post-Phase-5 Capture follow-ups:** **`memory/search` parity** / **`documents`** (**FP-TBD-5.1**); **explicit memory purge** for indexed captures (**FP-TBD-5.5**); **`entities_extract` evidence** (**FP-TBD-5.4**); **server vs `navigator.onLine`** health; **MediaRecorder** real-browser QA; **file/media** and **POST** **Web Share Target** (deferred). **`user_export` / `captures/media/`** shipped in **5I** — **FP-TBD-5.11** closed for MVP export.

---

## 22. Final recommendation

- **Shipped** **5A–5I** (see §21.1); **5D** consumes the **verified** STT foundation — **no** new STT adapters in Phase 5. **5E–5F** deliver **mobile voice** when **self-hosted Lumogis is unreachable**: **local audio**, **manual sync**, **then** server **`POST …/transcribe`** (**optional** future mobile STT — **FP-TBD-5.15–5.17**).
- **Photo offline** remains **out of MVP** (**FP-TBD-5.10b**). **No** Background Sync / silent upload / SW API caching.
- **Phase 5 status (2026-05-01):** **Closed as MVP** — chunks **5A–5I** shipped; verification reported orchestrator **1714 passed / 9 skipped**, web **241 passed**, lint clean, build clean, **`verify:pwa-dist`** OK, **`npm run codegen`** applied, OpenAPI snapshot updated.

### 22.1 MVP closure summary (2026-05-01)

Phase 5 Capture / QuickCapture is **closed as an MVP programme milestone**. The MVP slice delivers:

- **QuickCapture** supports **text / link / photo / voice** via **`/capture`** (mobile-first, desktop-capable; nav entry present).
- **Voice transcription** uses the **verified `SpeechToText` facade only** (`POST /api/v1/captures/{id}/transcribe` → `transcribe_blob`); **no** Capture-owned STT adapters, sidecars, or **`STT_*`** wiring were introduced.
- **Offline local outbox** for **text / link / voice** (**`captureOutbox.ts`** via `idb-keyval`) with **manual “Sync now”** only — **no** Background Sync, **no** Cache Storage / SW API caching, **no** silent sync, **no** automatic replay on online transitions.
- **Add to memory** (**`POST /api/v1/captures/{id}/index`**) is **explicit user action only** and indexes into **personal `notes`** + Qdrant **`conversations`** **only** — Qdrant **`documents` is not touched** (search parity = **FP-TBD-5.1**).
- **`user_export` / `user_import`** include **`captures` / `capture_attachments` / `capture_transcripts`** metadata **and** capture media binaries under **`captures/media/`** + **`capture_media`** manifest section (5I).
- **No auto-index** on save / upload / sync / transcription.
- **No silent server mutation** from PWA share target (**`GET /capture`** prefill only — no **`POST`** share target, no file/media share target, no SW **`fetch`** handler).
- **No Agentic Core, Team Inbox, or Owner Inbox** in Phase 5.

**Out of scope and preserved as deferred follow-ups (not blockers):** **FP-TBD-5.1** (memory/search / `documents` parity), **FP-TBD-5.5** (explicit memory purge for indexed captures), **FP-TBD-5.4** (`entities_extract` evidence convention), **FP-TBD-5.9** (OCR / image understanding), **FP-TBD-5.10b** (offline photo staging), **FP-TBD-5.12** (EXIF stripping hard-strip), **FP-TBD-5.13** (wake word / always-listening), **FP-TBD-5.14–5.17** (optional STT extensions), **server-vs-`navigator.onLine`** health-check UX, **MediaRecorder** real-browser QA, **file/media** Web Share Target and **POST** share target. **FP-TBD-5.11** (export ZIP media) is **closed for MVP** — `captures/media/` export/import shipped in **5I**.

---

## 23. Confirmation

This document was produced by **codebase inspection + architecture extraction** only. **No** product/application code, migrations, or client capture features were implemented as part of authoring **`docs/architecture/cross-device-web-phase-5-capture-plan.md`**.

---

## 24. Implementation-readiness freeze (2026-04-29)

Summary of contracts **frozen** for MVP implementation (**planning-only** edit):

1. **STT:** Speech-to-Text foundation **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`** is **closed and verified** (**STT-1**, **STT-2A**, **STT-2B**, **STT-2C**). **5D** **calls** the facade only; **Phase 5 Capture must not** add STT **adapters**, **sidecars**, or backend **`STT_*`** wiring.
2. **Server `captures.status`:** **`pending` \| `failed` \| `indexed`** only. **`draft`** = **client / IndexedDB** only — **not** a server value.
3. **`capture_transcripts.transcript_status`:** **`pending` \| `processing` \| `complete` \| `failed` \| `unavailable`** — **exactly** this set (CHECK + OpenAPI).
4. **Create idempotency:** Canonical field **`client_id`** in **JSON body**; **`Idempotency-Key`** **deferred**. Same **user** + same **`client_id`** + same normalised payload → **200** same capture; **different** payload → **409** `idempotency_key_conflict`.
5. **Create scope:** **Personal-only** **`POST /captures`**; **sharing** only after **`POST …/index`** via **`/api/v1/notes/{id}/publish`**.
6. **`POST …/transcribe` when STT disabled/unavailable:** **503** + **stable STT error code** aligned with **`POST /api/v1/voice/transcribe`**; **no** **`complete`** transcript with **empty** text.
7. **Chunk 5A:** Includes migrations, DTO/OpenAPI skeleton, capture/media **service skeleton**, storage foundation + path-safety tests; **excludes** full CRUD, multipart, transcribe, index, UI, local outbox (§20 table).

---

## Self-Review Log
**Model:** GPT-5.2  
**Date:** 2026-04-29  
**Plan:** `cross-device-web-phase-5-capture-plan` (architecture extraction at `docs/architecture/cross-device-web-phase-5-capture-plan.md`)  
**ADR consulted:** *(maintainer-local only; not part of the tracked repository)* (mirror — aligns with server-brained PWA, bounded persistence, no offline-first brain)

### Dimension findings
| Dim | Verdict | Note |
|-----|---------|------|
| **D1** | ⚠️→✅ | Closed **`retrieve_context` vs note payload** mismatch; stub route **OpenAPI** migration; chunk label collision (**5F** vs post-5E upload); idempotency **200** pinned. |
| **D2** | ✅ | **`point_ids.py`** B11 helper; **`projection`-style** ordering called out; **`config.get_embedder()`** pattern. |
| **D3** | ⚠️→✅ | Fixed **`REFERENCES users`** contradiction with migration 010; **composite UNIQUE** for `local_client_id`; **pagination** + payload caps; **`.env`** table; **`CaptureCreated`** alignment still owned by **5A** OpenAPI pass. |
| **D4** | ⚠️→✅ | Index **partial failure**, **409** on re-index, **idempotent POST** vs errors clarified. |
| **D5** | ⚠️→✅ | **Bearer-first CSRF** note for future share target; **no FK** default matches shipped schema style. |
| **D6** | ⚠️→✅ | Added **`user_export` exhaustive** test expectation + index idempotence test bullet. |
| **D7** | ⚠️→✅ | **Approvals-style** rate bucket; list **limits**; optional **async 202** deferral named. |
| **D8** | ⚠️→✅ | **Single §21 Follow-up register**; OpenAPI **alias vs replace** for `/captures/text`. |

### Changes Made
1. Pinned **idempotent `POST` → 200** and removed conflicting **409 duplicate `client_id`** error row. <!-- SELF-REVIEW aggregate -->
2. Replaced free-form **Open questions** with **`## 21. Follow-up register`** table (**FP-TBD-5.x** placeholders for portfolio promotion later).  
3. Corrected **`captures.user_id`** DDL to **no FK** (consistent with **`notes`** + migration 010 commentary).  
4. Specified **`UNIQUE (user_id, local_client_id)`** partial index semantics.  
5. Added **§12.3 Qdrant** contract (historically numbered §12.2 before media split): **`note_conversation_point_id`**, payload **normalization** for **`retrieve_context`**.  
6. Added **rate-limit** implementation guidance (mirror **`approvals.py`** deque bucket).  
7. Added **§12.5** env vars **`CAPTURE_*`** (historically §12.4 before §12.6/12.7).  
8. Added **pagination + payload caps**, **index 409**, **`user_export` exhaustive** test gate, **OpenAPI stub migration** rules.  
9. Fixed typo / markdown stray `**` in `text` CHECK line; removed stray *(maintainer-local only; not part of the tracked repository)* leading space.  

### Remaining Uncertainties
- Exact **`CaptureCreated`** / **`CaptureResponse`** field set for **list/detail** (implementer completes in **5A** OpenAPI).  
- Whether **`POST /index`** stays synchronous **200** vs **202 + batch job** at scale — **measure before** adding `CAPTURE_INDEX_TIMEOUT_SEC` behaviour.  
- **STT:** sync vs queued transcription, and **`STT_BACKEND` default** (`none` vs `faster_whisper`) — **`lumogis-speech-to-text-foundation-plan.md`**.  
- **`FP-TBD-5.x`** re-key to **`FP-###`** via **`/verify-plan`** — **do not** hand-edit ***(maintainer-local only; not part of the tracked repository)***.  

### Planning-only pass — offline mobile voice (2026-04-29)

**Mobile QuickCapture** must **record + store voice locally** when **self-hosted Lumogis is unreachable** — **manual sync**, **no** silent upload, **no** Background Sync. **Server STT** (foundation) runs **after** upload; **immediate offline transcription** optional/future (**FP-TBD-5.15–5.17**). **Photo** stays **online-first** (**FP-TBD-5.10b**). Updated §5, §13, §15, §12.1 (**`client_attachment_id`**, **`transcript_provenance`**), §12.6, §20–§21.

### Planning-only pass — STT foundation split (2026-04-29)

Extracted **Speech-to-Text** into **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`**; Capture **depends** on **`SpeechToText` port** + **`POST /api/v1/voice/transcribe`**; removed Capture-owned STT adapter/env prose; chunks **5D** = integration only; **STT-*** parallel/prerequisite; **no** application code.

### Planning-only pass — MVP media + transcription (2026-04-29)

Expanded **MVP** to **text + URL + photo + voice (STT)**; **`captures` + `capture_attachments` + `capture_transcripts`**; **§12.2** media on disk; **§12.4** routes including **`…/attachments`**, **`…/transcribe`**, **`…/index`**; **`POST /captures/upload`** stays **501** → use **`POST …/attachments`**; **offline voice** on mobile promoted in later pass (**FP-TBD-5.10**); **offline photo** = **FP-TBD-5.10b**; chunks **5A–5I**; **no** application code in this edit.

### Hardening pass — 2026-04-29 (post self-review)
Canonical **`POST /captures`** + **`/text`** alias + **`/upload`** **501**; **OpenAPI** regeneration in **5A/5B**; **MVP index** = **`notes` + `conversations` only** with pinned **`retrieve_context`** payload; **no** **`documents`**; **FP-TBD-5.1** for **`memory/search`**; **DELETE indexed** → **409** `indexed_capture_requires_memory_delete`; **no** cascade; **FP-TBD-5.5** purge; roadmap wording; **MVP product slice** vs chunk **5A** disambiguation; **`CAPTURE_MAX_PENDING_DAYS`** deferred (no **5A** pruning).

### Codebase Context (for subsequent reviewers)
Lumogis is a **FastAPI orchestrator** (`orchestrator/`) with **v1 façade** under **`routes/api_v1/`** using **`require_user`** + **`get_user`**. Capture stubs live in **`routes/api_v1/captures.py`** (**501**). Memory search reads **`documents`** only; **chat context** reads **`conversations`** with **`ContextHit(session_id, summary)`** shapes. **`projection.project_note`** targets **shared/system** copies; **personal** note INSERT + **Qdrant** for notes is a **Phase 5** gap this plan fills. Client PWA (`clients-lumogis-web`) has **IDB drafts** (`drafts.ts`) and **precache-only SW**. Per-user export allowlist is **`services/user_export._USER_EXPORT_TABLES`** with an **exhaustive pytest gate**.

---

## Self-Review Log — Round 2
**Model:** Composer  
**Date:** 2026-04-29  
**Plan:** `cross-device-web-phase-5-capture-plan` (`docs/architecture/cross-device-web-phase-5-capture-plan.md`)  
**ADR consulted:** *(maintainer-local only; not part of the tracked repository)* (plus finalised umbrella `docs/decisions/030-cross-device-client-architecture.md` cited in Related)

### Dimension findings
| Dim | Verdict | Note |
|-----|---------|------|
| **D1** | ✅ | Clarified **5E/5F** as same-milestone for offline E2E; **`POST …/transcribe`** timeout vs **202** deferred to **5D** + STT plan. |
| **D2** | ✅ | **ADR alignment** paragraph: bounded IDB staging ≠ local brain; matches umbrella decision. |
| **D3** | ⚠️→✅ | **`client_id` ↔ `local_client_id`** mapping; multipart **`client_attachment_id`**; attachment **idempotent 200**; **`POST …/transcribe`** error matrix; **idempotency payload conflict → 409**. |
| **D4** | ⚠️→✅ | **Partial sync** + **STT off → 503** (frozen §12.4; superseded Round 2 optional **200** path). |
| **D5** | ⚠️→✅ | **`Content-Disposition: attachment`** on media download. |
| **D6** | ⚠️→✅ | New backend test bullets: idempotency conflict, partial-sync retry, transcribe errors. |
| **D7** | ⚠️→✅ | **List vs detail** payloads + N+1 guard; download throttling note; transcribe latency. |
| **D8** | ✅ | **STT-off** wording cross-linked to foundation to avoid divergent HTTP contracts. |

### Changes Made
1. Added **ADR alignment** callout under invariant decisions (offline staging vs server-brained).  
2. Pinned **idempotent replay with conflicting body → 409** `idempotency_key_conflict`.  
3. Documented **partial sync** orphan pending capture + attachment retry.  
4. **`local_client_id`** row: explicit **wire `client_id` → column** mapping.  
5. **`POST …/attachments`:** **`file`** + **`client_attachment_id`**; **idempotent 200** same `attachment_id`.  
6. **`POST …/transcribe`:** full **404/422/409/502/503** matrix + **indexed** capture guard + **STT disabled** foundation alignment.  
7. **List vs detail** DTOs + **N+1** guidance; **502**/**503** in error list; **download** throttling note; **transcribe** latency.  
8. **§16:** **`Content-Disposition: attachment`** for downloads.  
9. **§18:** new pytest bullets (conflict, partial sync, transcribe edge cases).  
10. **§20:** **5E+5F** same-milestone clarification.

### Remaining Uncertainties
- **`POST …/transcribe`** when STT is off: **503** + foundation code — **frozen** in §12.4 (Round 2 “pick **503** vs **200**” **superseded**).  
- **`Event.NOTE_CAPTURED`** payload fields for graph — still confirm against **`orchestrator/events.py`** + graph consumer in **5G** (existing plan uncertainty carries).

### Codebase Context (for subsequent reviewers)
Unchanged from Round 1: **FastAPI** orchestrator, **`routes/api_v1/captures.py`** stubs, **`events.Event.NOTE_CAPTURED`**, **`services/memory.retrieve_context`** expects **`conversations`** payload per §9. Voice façade **`routes/api_v1/voice.py`** exists (`require_user`). Round 2 did not re-audit **`conftest.py`** / **`config.py`** beyond cross-checking STT foundation wording.

---

## Implementation Log
**Verified by:** Composer  
**Date:** 2026-04-29  
**Plan:** `docs/architecture/cross-device-web-phase-5-capture-plan.md` (Phase 5 Capture / QuickCapture MVP)  
**Critique rounds:** 0 (formal critique/arbitrate not run on this architecture doc)  
**Tests:** **1714** passing / **9** skipped / **0** failed (orchestrator, docker compose pytest); **241** web Vitest + lint + **`verify:pwa-dist`**; **0** fixed in this pass  
**Files:** all §20 **5A–5I** surfaces present per §21.1  
**Done checklist:** **9/9** (§21.1 chunk rows **Closed**)  
**ADR:** umbrella extended — **`docs/decisions/030-cross-device-client-architecture.md`** (status history); **no** new numbered ADR for Phase 5 alone

### What matched the plan
1. **5A–5I** shipped per §21.1 — CRUD, attachments + **`CAPTURE_MEDIA_ROOT`**, transcribe + **`capture_transcripts`**, QuickCapture UI, outbox + manual sync, **`POST …/index`** to **`notes`** + **`conversations`** only, PWA shortcut + GET share prefill, **`user_export` / import** with **`captures/media/`** binaries + import blob restore.
2. **Non-goals** preserved: no Agentic Core / Team Inbox in chunk; no Qdrant **`documents`**; no SW **`runtimeCaching`** for captures; no POST share target in 5H scope.
3. OpenAPI / codegen aligned where **`SectionSummary.kind`** gained **`capture_media`**.

### Deviations (intent preserved)
None.

### Implementation errors
None.

### Critical violations
None.

### ADR notes
**030** amended with Phase 5 confirmation line; *(maintainer-local only; not part of the tracked repository)* mirror aligned.

### Security findings
None.

### Test quality issues
None.

### Test fixes applied
None.

### Potential regressions
None observed — full orchestrator + web suites green on verification run.

### Noteworthy discoveries
Host **`make test`** failed (`No module named pytest`); **`docker compose run`** orchestrator test run used as DoD gate (same as **`make compose-test`** intent).

### Recommended next steps
1. **Phase 6 (Tauri)** or parent-plan remaining items per **`cross_device_lumogis_web`**.  
2. Triage §21 **FP-TBD-5.*** into portfolio / future plans as needed (**FP-001** Notes updated).

