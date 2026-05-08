---
status: extraction
implemented: (scoping only — no product code)
extracted: 2026-04-29
source_exploration: *(maintainer-local only; not part of the tracked repository)*
verified_artefact: docs/architecture/lumogis-speech-to-text-foundation-plan.md
---

# Lumogis Speech-to-Text foundation (implementation plan)

**Slug:** Reusable **local-first** speech-to-text capability for the AGPL orchestrator — **not** owned by Capture alone.

**Date:** 2026-04-29

**Kind:** Strategic **architecture / implementation plan** derived from **Exploration: Voice transcription (push-to-talk) and private wake word** (*(maintainer-local only; not part of the tracked repository)*). **Not** an ADR. **Planning-only** — **no** product code is added by authoring this document.

**Related:** `docs/architecture/cross-device-web-phase-5-capture-plan.md` (Capture **consumes** this foundation); `docs/decisions/030-cross-device-client-architecture.md` (umbrella); *(maintainer-local only; not part of the tracked repository)*.

**STT‑2B shipped (2026‑04‑30):** **`docker-compose.stt.yml`** is an **optional Compose overlay** (merged with **`COMPOSE_FILE=docker-compose.yml:docker-compose.stt.yml`**), **not** a **`profiles: ["stt"]`** service on the base file. The sidecar is **`ghcr.io/speaches-ai/speaches:0.8.3-cpu`** (digest-pinned, MIT licence): **Speaches** is the selected **batch / local STT** HTTP backend for **`POST /api/v1/voice/transcribe`**; **wake word, VAD, TTS, barge-in, and full conversational voice remain outside STT-2B**. Named volume **`lumogis_stt_models`**, healthcheck **`GET /health`**, internal URL **`http://lumogis-stt:8000`**, transcribe **`POST /v1/audio/transcriptions`**.

**STT‑2C live smoke (2026‑04‑30, dev workstation, `lumogis` Compose project):** Speaches image pulled; sidecar **`GET /health`** → **`OK`** (HTTP 200). **Speaches v0.8.x:** health can pass **before** an ASR model is installed; **`STT_MODEL=base`** alone is **not** accepted for transcription until a model is fetched. Use a **HuggingFace faster‑whisper model id** in **`STT_MODEL`** (e.g. **`Systran/faster-whisper-base.en`**) **or** prime the sidecar with **`POST /v1/models/{model_id}`** (path-encode **`/`** as **`%2F`**) then **`POST /api/ps/{model_id}`** (load into memory). **Direct sidecar** multipart **`POST /v1/audio/transcriptions`** verified with **`Systran/faster-whisper-base.en`** and OpenAI sample audio. **Orchestrator e2e** **`POST /api/v1/voice/transcribe`** verified (`provider` **`whisper_sidecar`**, non‑empty **`text`**, **`segments`** array present — may be empty for non‑verbose JSON). **`GET /api/v1/admin/diagnostics`** → **`speech_to_text.transcribe_available: true`**. **Operational note:** the orchestrator process uses code baked into the image at **`/app`**; after `git pull`, run **`docker compose build orchestrator`** (or equivalent) so new routes (e.g. voice) exist — bind‑mount is **`/project`**, not **`/app`**. Model cache under **`lumogis_*_lumogis_stt_models`**. **Caveat:** default docs **`STT_MODEL=base`** align with RAM tiers but **Speaches expects HF-style ids** for the `model` multipart field unless the operator pre-installs an alias; README and **`.env.example`** updated accordingly.

**STT‑2A shipped (2026‑04‑29):** **`STT_BACKEND=whisper_sidecar`** wires **`orchestrator/adapters/whisper_sidecar_stt.py`** (`httpx`) to a **compatible HTTP** sidecar — **no** in-repo Compose service in that chunk, **no** pinned upstream image yet, **no** in-process **faster-whisper** (that value still raises at config read). Operators supply **`STT_SIDECAR_URL`** and path overrides; SSRF-style host policy is enforced by default. **STT‑2B** adds the optional **`docker-compose.stt.yml`** overlay.

**STT‑1 shipped (2026‑04‑29):** The repository includes the `SpeechToText` port, `fake_stt` adapter, `services/speech_to_text.py` façade, **`POST /api/v1/voice/transcribe`**, env-driven config, admin **`speech_to_text`** diagnostics slice, tests, and the OpenAPI snapshot under `clients/lumogis-web/openapi.snapshot.json`. **Not in STT‑1:** faster‑whisper / whisper.cpp adapters, Capture routes, dashboard or chat microphone UI — see §Non-goals.

---

## 1. Scope

- **Push-to-talk** and **uploaded-audio** transcription: client sends an audio blob (e.g. browser **`MediaRecorder`** or file upload); server returns an **editable** transcript payload.
- **Local-first by default:** audio is processed **on the Lumogis host**; **no** third-party STT unless **explicit opt-in** (post-MVP product decision).
- **Reusable service:** same **port + facade** serve **Capture** (voice notes), **Chat push-to-talk**, and future voice UI — **not** a Capture-only backend.
- **Explicit routes only:** transcription runs only for requests that hit the **authenticated** transcription API or an internal caller (e.g. Capture orchestration) that invokes the same port — **no** silent ingestion of arbitrary files.

---

## 2. Non-goals

| Non-goal | Rationale |
|----------|-----------|
| **Wake word / always-listening** | Companion/native/service track; **openWakeWord** etc. out of scope — see exploration |
| **Always-on browser microphone** | **User gesture** required for recording; no background mic in web MVP |
| **Cloud STT by default** | Contradicts local-first; **opt-in only** when product adds keys + disclosure |
| **Live streaming STT** | MVP is **batch / file** transcription only |
| **Capture UI** | Lives under Phase **5** Capture plan |
| **Memory indexing** | STT returns text; **callers** decide persistence and **when** to index (Capture uses **`POST …/index`** separately) |
| **Transcription of arbitrary paths** without policy | No blanket “transcribe any server file”; callers pass bytes or approved attachment handles |

---

## 3. Architecture

**Pattern:** routes → **services** → **ports** ← **adapters** (per `ARCHITECTURE.md` convention in exploration).

| Component | Path / responsibility |
|-----------|----------------------|
| **Port (protocol)** | `orchestrator/ports/speech_to_text.py` — abstract **`SpeechToText`** interface, e.g. `transcribe(audio: bytes, mime_type: str, *, language: str | None) -> TranscriptionResult` |
| **Adapter — local primary** | `orchestrator/adapters/faster_whisper_stt.py` — **faster-whisper** / CTranslate2 (**exploration primary recommendation**) |
| **Adapter — sidecar (optional)** | `orchestrator/adapters/whisper_cpp_sidecar_stt.py` — HTTP client to **whisper.cpp** (or compatible) service when core image size / CPU isolation requires it |
| **Adapter — fake / dev** | `orchestrator/adapters/fake_stt.py` — deterministic transcript for tests |
| **Service facade** | `orchestrator/services/speech_to_text.py` (or `transcription.py` as **thin** re-export) — **validate audio** (size, duration cap, MIME), resolve **`STT_BACKEND`**, call port, map errors → HTTP-safe results |
| **Config factory** | `orchestrator/config.py` (or dedicated `stt_config.py`) — build port from env; **`STT_BACKEND=none`** registers **disabled** adapter |
| **HTTP route** | `orchestrator/routes/api_v1/voice.py` (recommended) — **`POST /api/v1/voice/transcribe`** — see §4 |

**Callers:**

- **Chat / generic push-to-talk:** may call **`POST /api/v1/voice/transcribe`** directly with `multipart` audio.
- **Capture:** **`POST /api/v1/captures/{id}/transcribe`** (Phase 5) **orchestrates**: verify capture + attachment ownership, **read bytes** from **`capture_attachments`** storage, call **`SpeechToText` port** via the **same** service facade as the HTTP route — **no** second Whisper stack.

---

## 4. Canonical HTTP path

**Chosen:** **`POST /api/v1/voice/transcribe`**

**Why not `/api/v1/transcription/transcribe`:** avoids implying document or bulk “transcription” pipelines; **voice** matches user mental model (push-to-talk, mic); aligns with exploration’s **`POST /voice/transcribe`** while staying under the **v1** façade. **Transcription** as a noun remains in types/docs.

**Contract (planning level):**

- **Auth:** `require_user` when `AUTH_ENABLED` — **401** if missing.
- **Body:** `multipart/form-data` with **`file`** (or pinned field name in OpenAPI).
- **Limits:** **`STT_MAX_AUDIO_BYTES`**, **`STT_MAX_DURATION_SEC`** (enforce as feasible — duration may require ffprobe-style helper later).
- **MIME:** Allowlist aligned with Capture audio policy where practical: **`audio/webm`** (primary for **MediaRecorder**), **`audio/mp4`**, **`audio/mpeg`**, **`audio/wav`** — exact list pinned in implementation + shared constants with Capture if useful.

**When `STT_BACKEND=none`:** route returns **503** with stable code (e.g. `stt_disabled`) or **501** — pick one in **STT-1** and test.

---

## 5. Default provider decision (from exploration)

| Decision | Choice |
|----------|--------|
| **Private default** | **Local STT** on Lumogis host |
| **Primary local adapter** | **faster-whisper** |
| **Sidecar** | **whisper.cpp** (or compatible HTTP server) **conditional** if image size / worker isolation demands |
| **Later fallback** | **Vosk** behind same port |
| **Web Speech API** | **Not** private default; optional client path only with **explicit** cloud/disclosure UX if ever added |
| **Cloud STT** | **Opt-in only** — explicit operator/user configuration; **not** MVP default |

**Default `STT_BACKEND`:** **`none`** in conservative deployments to avoid surprise RAM/GPU use until operators enable **`faster_whisper`** — align with exploration’s “default none to avoid RAM surprise”; product may later choose **`faster_whisper`** as default for single-user appliances (document in release notes).

---

## 6. Configuration (`.env`)

| Variable | Values | Purpose |
|----------|--------|---------|
| **`STT_BACKEND`** | `none` \| `faster_whisper` \| `whisper_cpp_sidecar` | Port factory |
| **`STT_MODEL`** | `base`, `small`, … | Adapter-specific model id |
| **`STT_LANGUAGE`** | optional ISO code | Auto-detect if unset (adapter permitting) |
| **`STT_MAX_AUDIO_BYTES`** | e.g. align ~25 MiB with Capture or stricter | Upload limit |
| **`STT_MAX_DURATION_SEC`** | e.g. 600 | Ceiling for transcription requests |
| **Sidecar URL / token** | if `whisper_cpp_sidecar` | HTTP client config |

**Capture-specific** vars (**`CAPTURE_*`**, media paths) remain in **`docs/architecture/cross-device-web-phase-5-capture-plan.md`** — not duplicated here.

---

## 7. API response shape

Stable JSON (callers persist subsets as needed; Capture maps into **`capture_transcripts`**):

```json
{
  "text": "...",
  "language": "en",
  "duration_seconds": 12.34,
  "provider": "faster_whisper",
  "model": "base",
  "segments": [
    { "start": 0.0, "end": 2.5, "text": "..." }
  ]
}
```

- **`segments`:** optional; omit or `null` if adapter does not supply word/segment timing.
- **Errors:** structured problem JSON — **no** raw stack traces or secrets.

---

## 8. Privacy / security

- **Raw audio** on the **Lumogis host** for **local** adapters — **no** third-party STT unless **explicit opt-in** cloud path exists and is selected. **Before upload**, Capture may hold audio **only** on the **client** (IndexedDB) when server unreachable — outside this service’s scope until bytes hit **`transcribe()`**.
- **Authentication** required on **`POST /api/v1/voice/transcribe`**.
- **Size / duration** limits; **MIME** allowlist + **magic-byte** sniff where feasible.
- **No secrets** in logs; log **request ids** only.
- **No** automatic indexing of transcripts — callers own persistence.
- **No hidden microphone** — browser recording remains **gesture-gated** in **consumer** UIs (Capture plan, Chat).
- **Audio bytes** for Capture: stored as **caller-managed** attachments; STT foundation validates **input** to **`transcribe()`** only.

---

## 9. Tests (plan)

- **Fake adapter:** contract tests for port + facade (no model load).
- **Route:** **401** unauthenticated; **413** / **415** oversize / bad MIME; **200** with transcript on happy path (fake adapter).
- **`STT_BACKEND=none`:** route fails **cleanly** with documented status + code.
- **Local default tests:** **no** real outbound cloud calls — use fake or recorded fixtures.
- **Integration (optional STT-2):** smoke faster-whisper in CI only if acceptable cost; else mark manual/optional job.

---

## 10. Implementation chunks

| Chunk | Scope |
|-------|--------|
| **STT-0** | Plan review + ADR note (optional **`/verify-plan`** when implementation ships) |
| **STT-1** | **`SpeechToText` port** + **fake adapter** + **`POST /api/v1/voice/transcribe`** route + OpenAPI + tests |
| **STT-2** | **Real local STT** — **`docker-compose.stt.yml`** overlay + **`whisper_sidecar`** adapter (see ***(maintainer-local only; not part of the tracked repository)***); **in-process** **`faster_whisper`** deferrable |
| **STT-3** | Config docs, `.env.example`, operator runbook, boundary tests |
| **STT-4** | **Optional** consumer smoke: wire **one** UI path (e.g. Chat push-to-talk **or** Capture **after** **5D**) — can merge with consumer plans instead of standalone |

**Dependency:** Phase **5** chunk **5D** (Capture **server** STT integration) **requires** **STT-1** (port + route) for **`POST …/transcribe`**. **Local-only** voice recording (mobile, server down) **does not** require STT — see §11; **5F** outbox ships before server transcription is needed.

**STT‑2 planning:** Implementation details live in ***(maintainer-local only; not part of the tracked repository)*** (sidecar-first, **`docker-compose.stt.yml`** overlay, env matrix, tests, runbook). **STT‑2B** shipped as documented in that plan’s implementation log.

---

## 11. Mobile standalone voice modes

Cross-reference: **`docs/architecture/cross-device-web-phase-5-capture-plan.md`** §5, §12.6, §13.

### A. Server-backed STT (private default after sync)

- Browser records or uploads audio **to self-hosted Lumogis**; **`capture_attachments`** (Capture) or multipart to **`POST /api/v1/voice/transcribe`** (generic).
- Lumogis transcribes on the **host** via **`SpeechToText` port** (local **faster-whisper** by default when enabled) — **no** third-party STT unless **explicit opt-in** cloud path exists.
- **`POST …/captures/{id}/transcribe`** and **`POST /api/v1/voice/transcribe`** share the **same** facade.

### B. Server-unreachable local capture (Capture MVP client behaviour)

- **Mobile** **user-initiated** recording **without** contacting Lumogis succeeds: audio stays **on device** (IndexedDB / persistent storage) with **caps** and **disclosure** — see Capture §5.1 / §13.
- **No** immediate STT required; **`server_stt` transcripts** do not exist until after **manual sync** + optional **`POST …/transcribe`**.
- **No** silent upload, **no** Background Sync, **no** Lumogis Cloud relay.

### C. Mobile-capable STT (optional future — **not** Capture MVP default)

- **On-device** STT (e.g. **WASM** / heavy in-tab models) **or** **direct-to-third-party** STT from the **client** (explicit opt-in credentials, **no** Lumogis relay) could produce a **local transcript** while offline.
- **Web Speech API** is **not** the private default (often vendor-backed).
- **Direct-to-provider** STT must align with **mobile cloud fallback** / credential UX planning — **separate** from this foundation’s **server** adapters.
- If implemented, client syncs transcript into **`capture_transcripts`** with **`transcript_provenance`** **`mobile_local_stt`** or **`mobile_direct_provider_stt`** (Capture §12.1) — **FP-TBD-5.15–5.17**.

**Wake word / always-listening:** remains **out of** this document and **Phase 5** Capture.

---

## 12. Confirmation

This document is **planning-only**. **No** migrations, routes, adapters, dependencies, or UI were implemented as part of authoring **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`**.

---

## 13. Exploration traceability

Recommendations **§133–§138** of *(maintainer-local only; not part of the tracked repository)* (push-to-talk + **faster-whisper**, sidecar conditional, wake word out of core) are **reflected** above. **Wake word** and **openWakeWord** remain **out of** this foundation **and** out of Phase **5** Capture per Capture plan.
