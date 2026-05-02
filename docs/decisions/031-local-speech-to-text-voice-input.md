# ADR: Local speech-to-text (push-to-talk) and companion wake word

**Status:** Finalised (STT‑1 verified **2026‑04‑29**)
**Created:** 2026-04-10
**Last updated:** 2026-04-30
**Decided by:** /explore (Composer)
**Finalised by:** `/verify-plan` — *(maintainer-local only; not part of the tracked repository)* (port + `fake_stt` + `POST /api/v1/voice/transcribe`)

**Draft mirror:** *(maintainer-local only; not part of the tracked repository)* (kept in sync with status history)

### Implementation staging note (verified with STT‑1 — 2026‑04‑29)

The **committed default when STT is “on” remains `faster-whisper`** once STT‑2 ships. **`STT_BACKEND=fake_stt`** exists for **foundation scaffolding, CI, and operators who enable a non-heavyweight path** — it **does not** satisfy the eventual “local heavyweight STT” intent on its own. Plans, **`VoiceTranscribeResponse.provider`**, and **admin diagnostics** must label **`fake_stt`** as **development/test scaffolding**, not a substitute for **`faster-whisper`** production behaviour.

**Topology vs adapter (clarified 2026-04-29, /review-plan --arbitrate R2):** **`faster-whisper`** names the **default in-process Python adapter** on the **`SpeechToText` port** when heavyweight local STT runs inside the orchestrator process. **`whisper_sidecar`** (HTTP companion) in ***(maintainer-local only; not part of the tracked repository)*** is an **alternate deployment path** for the **same port** — local Whisper-class transcription without embedding weights in the core image. **Either** satisfies **“local Whisper-class when genuinely enabled”**; choose per deployment. Sidecar-first STT-2 does **not** contradict this Decision.

---

## Context

Users need **microphone dictation**: record while holding a button (or explicit control), obtain **transcribed text**, and **edit** before sending. They also want an optional **wake word** that runs **privately** (no cloud STT). Lumogis is local-first; the orchestrator today has **no** STT implementation, though **Phase 3** plans **Whisper-based audio ingestion** (M7). Wake word detection is **not** a natural fit inside a headless FastAPI worker and raises **licence** issues for some pre-trained open models (e.g. non-commercial creative commons weights).

## Decision

Adopt a **`SpeechToText` port** with a **local `faster-whisper` adapter** as the **default** path for **push-to-talk** transcription: the **client** uploads short audio to a **new HTTP endpoint**; the server returns **plain text** for display in an **editable** field. Keep STT **disabled by default** (`STT_BACKEND=none`) until the user enables it, to avoid unexpected RAM/GPU use. Treat **private wake word** as a **separate companion process** (or future native app) that performs **on-device** detection and **signals** the orchestrator to start listening — **not** as part of the core web server. Prefer **openWakeWord** with **licence-compatible custom models** for that companion; proprietary SDKs (e.g. Porcupine) remain **app/plugin** scope.

## Alternatives Considered

- **Web Speech API as default** — often cloud-backed; poor fit for private-by-default positioning (see *(maintainer-local only; not part of the tracked repository)*).  
- **whisper.cpp sidecar** — viable when isolating CPU/memory from the main image.  
- **Vosk** — lighter CPU; lower dictation quality for general prose.  
- **Browser Wasm Whisper** — maximum client-side privacy; high cost/complexity.  
- **Cloud STT** — acceptable only as explicit opt-in, out of scope for this ADR’s private path.  

## Consequences

**Easier:** One STT backend can serve **both** interactive push-to-talk and **Phase 3** file/audio transcription if the same port is reused.  
**Harder:** Container image size and startup time increase when STT enabled; wake word requires **extra packaging** and **platform mic permissions**.  
**Future chunks must know:** Wake word **never** assumes browser background capture; **model licences** must be checked before shipping default weights.

## Revisit conditions

- If **browser APIs** gain **universal** reliable offline recognition with stable permissions, revisit client-side STT for push-to-talk.  
- If **openWakeWord** (or successor) ships **Apache/MIT-compatible default models** suitable for commercial use, revisit default wake word packaging.  
- If Phase 3 M7 chooses **whisper.cpp only**, align the adapter implementation to avoid two incompatible STT stacks.

## Status history

- 2026-04-10: Draft created by /explore
- 2026-04-29: Draft (**revised**, text-only) — /review-plan --arbitrate R1 **`voice_input.plan`** — added **implementation staging note** distinguishing **`fake_stt`** scaffolding from **`faster-whisper`** end state (**Decision unchanged** — local default when genuinely enabled remains **`faster-whisper`** in STT‑2+)
- 2026-04-29: Draft (**revised**, text-only) — /review-plan --arbitrate R2 **`voice_input.plan`** — clarified **`faster-whisper`** (in-process adapter) vs **`whisper_sidecar`** HTTP deployment as alternate topology on same **`SpeechToText` port** (**Decision unchanged**)
- 2026-04-29: Finalised by `/verify-plan` — STT‑1 implementation (`SpeechToText` port, `fake_stt`, `POST /api/v1/voice/transcribe`, diagnostics) matches staging note and recorded decision; canonical copy in this file.
- 2026-04-30: /verify-plan — *(maintainer-local only; not part of the tracked repository)* — STT‑2A/B/C (**`whisper_sidecar`** HTTP adapter, optional **`docker-compose.stt.yml`** overlay with digest-pinned Speaches, operator runbook, tests) confirms **`whisper_sidecar`** as the recorded alternate deployment topology on the **`SpeechToText` port`; **Decision unchanged** (in-process **`faster-whisper`** remains the documented default **label** when heavyweight STT runs in orchestrator — still deferred as STT‑2D).
