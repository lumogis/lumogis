# ADR-188: Voice dictation — reusable `VoiceDictationButton` into the composer
**Status:** Finalised
**Created:** 2026-07-24
**Last updated:** 2026-07-24
**Decided by:** /explore (claude-opus-4-8); finalised by /verify-plan (LUM-653)

## Context
The backend for voice dictation already ships in **core** — `POST /api/v1/voice/transcribe` (whisper-sidecar STT, ADR-031) — and browser-side MediaRecorder logic already lived in `QuickCapturePage`, but that logic was **welded to capture creation** (records an audio *attachment* on a saved capture and transcribes via the *capture* route). LUM-653 is a **pure frontend extraction**: make the recording flow a reusable unit that drops into the chat composer and the LUM-652 email composer, backed by the standalone voice route, with **no orchestrator change**. The proprietary boundary is already resolved: push-to-talk local STT is core per ADR-031; the continuous "Jarvis" loop + wake word (`conversational_voice`) is the app-scope concern and is out of scope here.

## Decision
Ship a **headless `useAudioRecorder` primitive** (record → `Blob`, mime fallback webm/opus→webm→mp4, refuse-on-empty-mime, `onerror` wired, mic-track cleanup) + a **`useVoiceDictation`** hook that composes it with an **injected** `transcribe` function → text + a thin presentational **`VoiceDictationButton`** (emits `onTranscript(text)`, null-when-unsupported), plus the one missing web client `api/voice.ts::transcribeVoice(client, blob)` (multipart FormData → `/api/v1/voice/transcribe`). The backend MIME allowlist already accepts `audio/mp4`, so Safari/iOS works with **no backend change**. Mount in `ChatComposer` (insert-at-cursor with post-commit caret restore, `voiceTranscribe` prop supplied by ChatPage/DocumentChatPage) and `EmailComposer` (`ComposerApi.transcribeVoice` supplied by EmailInboxView, append to body + Review gated while transcribing). Zero new dependency, Docker service, env var, or migration.

**Key design decision — dependency injection over context:** the transcribe capability is **injected** (a `transcribe(blob)=>Promise<string>` function), never pulled from React context. There is no `useApiClient()` in the codebase, and `useAuth().client` throws outside `<AuthProvider>` — but `EmailComposer` is deliberately auth-free (injected `ComposerApi`, "unit-testable without a live client"). Injection keeps the hook/button context-free and preserves EmailComposer's bare testability.

## Alternatives Considered
- **Monolithic button (logic inside the component)** — un-testable in isolation, un-reusable headless. Rejected for the hook/thin-view split.
- **Adopt an npm library (`react-media-recorder` / `react-audio-recorder`)** — a new dependency for logic already in-tree; MP3-encoder path (~73 KB gzipped) encodes formats the backend doesn't need. Rejected on dependency hygiene.
- **Pull the client from React context inside the shared hook** — would crash EmailComposer's bare tests and reverse its documented design. Rejected for dependency injection.
- **Backend/streaming changes** — forbidden by the ticket and unnecessary; the shipped one-shot route suffices. Live/streaming voice stays app-scope.
- Full detail: `cursor/explorations/LUM-653-voice-dictation.md`.

## Consequences
**Easier:**
- A single reusable dictation unit any text field can adopt via a callback — chat, email (LUM-652), and future agentic compose (LUM-654) get voice for free.
- `useAudioRecorder` is one source of truth for the MediaRecorder mechanism; the dictation layer stays thin and injection-clean.
- Testable without a real mic (a `MockMediaRecorder` fake) with a CI-gated live path deferred.

**Harder / foreclosed:**
- Commits to **push-to-talk one-shot** dictation and **plain-text** insertion against the existing `/voice/transcribe` contract; live/streaming STT and the conversational loop remain a separate app-scope effort.
- Hosts must honour the graceful-degrade posture (button hidden when the capability is absent; shown-disabled with a reason on runtime permission-deny/STT 503).

**One deviation (intent preserved):** the QuickCapture migration used the plan-authorised **C2 fallback** — QuickCapture shares only `pickAudioMimeType` (the codec helper) from `useAudioRecorder` and keeps its own capture-attachment recording flow, rather than fully adopting the hook (C1). Reason: QuickCapture's `recording`/`recorderMime` state gates the recording UI (`!recorderMime` hides the recorder in favour of file upload) in a way `useAudioRecorder.supported` cannot express, so a full C1 migration risked changing a shipped page's behaviour. The single-source-of-truth goal is partly met (the mime helper is de-duplicated); the recorder mechanism is intentionally duplicated to avoid regression. A functional QuickCapture record→stop→upload test guards the page regardless.

## Revisit conditions
- **The sidecar gains streaming transcription** → revisit one-shot record→stop→transcribe for a live partial-transcript UX.
- **QuickCapture is next refactored** → revisit folding its recorder onto `useAudioRecorder` (C1) once its UI-gating on `recorderMime` is reworked.
- **The conversational voice loop lands** → confirm it reuses this hook/route rather than re-implementing capture, and that the core/app boundary still holds.

## Status history
- 2026-07-24: Draft created by /explore (LUM-653).
- 2026-07-24: Finalised by /verify-plan (LUM-653) — implementation confirmed the decision (headless `useAudioRecorder` + `useVoiceDictation` + `VoiceDictationButton` + `api/voice.ts`; DI transcribe; ChatComposer + EmailComposer mounts). One deviation: QuickCapture used the plan-authorised **C2** (share `pickAudioMimeType` only) rather than C1, to avoid a behaviour change on a shipped page. Finalised copy at docs/decisions/188-voice-dictation.md.
