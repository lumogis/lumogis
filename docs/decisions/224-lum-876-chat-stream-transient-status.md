# ADR-224: Transient chat stream status travels in `lumogis.status`, never in the answer text (LUM-876)
**Status:** Finalised
**Created:** 2026-11-06
**Last updated:** 2026-11-06
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-06 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/chat_stream_transient_status_retro.md`
**Draft mirror:** `.cursor/adrs/chat_stream_transient_status.md`
**Linear:** LUM-876

## Context

When a local model is cold, the orchestrator shows a hint the first time a process streams for that model: "Loading model on your machine — first time may take 1–2 minutes…". Until LUM-876 it was streamed as the first `delta.content`, wrapped in markdown asterisks. Two defects followed:

- Document chat renders the answer as plain text, so the asterisks showed.
- The web client concatenates every delta into the answer. So the hint stayed in the final message, in the stored thread, in the synced conversation, and in the history sent back with the next turn.

Seen on lumogis-dev on 2026-11-06 during the LUM-863 browser look.

## Decision

Anything a stream says that is not the model's words is sent in the `lumogis` extension object of an OpenAI `chat.completion.chunk`, never in `delta.content`.

- The orchestrator's `stream_completion` (`orchestrator/routes/chat.py`) sends the cold-model hint as one chunk with an **empty delta** and `lumogis.status = {code: "local_model_loading", message: "<plain text>"}`. Both streaming chat routes use it.
- `code` is stable for client logic. `message` is plain text the server owns, so wording changes need no client release.
- The web stream parser (`ChatStream.ts`) reports it via `onStatus`, separately from `onDelta`.
- `/chat` and document chat show the message only while the assistant bubble is empty, and drop it on the first token. It lives in page state, never in the message, so it is never stored, synced or sent back as history.
- The stream stays a valid OpenAI chunk sequence. No custom SSE `event:` type is used.

### As-implemented surface

- Server: `LOCAL_MODEL_LOADING_STATUS = "local_model_loading"`, `LOCAL_MODEL_LOADING_NOTE` (plain text), in `orchestrator/routes/chat.py`.
- Client types: `LumogisStreamStatus`, `LumogisChatExtensions.status` in `clients/lumogis-web/src/api/chat.ts`.
- Rendering: `.lumogis-chat__stream-status` (sibling of the typing dots, `role="status"`), `.lumogis-document-chat__status`.

## Alternatives considered

- **Keep it in `content` and strip it client-side.** Not chosen: the client would need to know the exact text, and every other consumer would still store it.
- **A custom SSE `event: status`.** Not chosen: OpenAI-compatible clients and our own parser treat a stream as `data:` chunks. A side channel inside the chunk keeps one parser and stays compatible.
- **Drop the hint.** Not chosen: a 1–2 minute silent wait on first use reads as a hang on a local-first install.

## Consequences

- Any future transient status (tool running, slow retrieval) has a slot that cannot leak into answers or history.
- Third-party OpenAI clients on `/v1/chat/completions` no longer see the hint. A cold model looks like a slow first token to them. This is accepted.
- Clients must tolerate chunks with an empty delta. The web parser already did.
- Only one status is shown at a time: a later status replaces an earlier one.

## Revisit conditions

- A second status code is added: decide replace vs. stack, and whether the web shows codes it does not know.
- A non-web client (lumogis-search, MCP) streams chat: it must handle or ignore `lumogis.status`.
- An external client needs the hint: add an opt-in, do not move it back into `content`.

## Linear linkage (Product OS)

- **Existing issue:** LUM-876
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

- Backend: `test_local_stream_includes_loading_note_only_first_time` in `orchestrator/tests/test_routes.py` checks there is exactly one `lumogis.status`, the hint is not in the content, and the note has no markdown.
- Web:
  - `ChatStream.test.ts` checks `onStatus` and that the hint never reaches `onDelta`.
  - `ChatPage.test.tsx` checks the status shows while waiting, is gone after, and is not in the bubble or the stored threads.
  - `DocumentChatPage.test.tsx` (new) checks the same on the plain-text surface.
- Mutation-checked: removing either page's `onStatus` handler fails its test.
- Gap: no live browser check on a cold local model at finalisation.

## Status history

- 2026-11-06: Finalised by /record-retro (retrospective).
