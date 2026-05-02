// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written types for the chat-completions wire contract.
//
// Pinned against the shipped server in:
//
//  * `orchestrator/models/api_v1.py:46–80`
//    (`ChatMessageDTO`, `ChatCompletionRequest`, `ChatCompletionResponse`,
//     `ModelDescriptor`, `ModelsResponse`).
//  * `orchestrator/routes/api_v1/chat.py` (request → loop.ask_stream wiring).
//  * `orchestrator/routes/chat.py:177–225` (`_sse_chunk`, `stream_completion`)
//    — the SSE chunk shape this client parses.
//
// We hand-write these instead of relying on `openapi-typescript` output for the
// same reason `auth.ts` does: the file ships before `npm run codegen` is run,
// and the parent plan §"Pass 1.2 Chat" handoff line 74 explicitly says
// "Pass 1.2 hand-writes its DTOs the same way `src/api/auth.ts` does, then
//  `codegen` lands when the OpenAPI surface is consumed in volume."
//
// If a future shipped change drifts these shapes, the orchestrator-side
// snapshot test (`tests/test_api_v1_openapi_snapshot.py`, parent plan
// Phase 0 DoD) will catch it before any client work breaks.

export type ChatRole = "system" | "user" | "assistant";

export interface ChatMessageDTO {
  role: ChatRole;
  content: string;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessageDTO[];
  /** Default true on the wire; we always set it explicitly so it is auditable. */
  stream: boolean;
}

/**
 * Single SSE `data:` payload. Mirrors the OpenAI chat-completion-chunk shape
 * emitted by `routes/chat.py::_sse_chunk` (the v1 façade reuses the same
 * generator — see `routes/api_v1/chat.py:140`).
 *
 * Note: `delta` may be sparse — the first chunk carries `{ role: "assistant",
 * content: "" }`, every text chunk carries `{ content: "<delta>" }`, and the
 * final chunk carries `{}` with `finish_reason: "stop"`.
 */
export interface ChatCompletionChunk {
  id: string;
  object: "chat.completion.chunk";
  created: number;
  model: string;
  choices: Array<{
    index: number;
    delta: { role?: ChatRole; content?: string };
    finish_reason: "stop" | "length" | null;
  }>;
}

/** Wire-literal error details verified against shipped `routes/api_v1/chat.py`. */
export const CHAT_ERROR_LITERALS = {
  LAST_MESSAGE_MUST_BE_USER: "last_message_must_be_user",
  SYSTEM_MESSAGE_POSITION: "system_message_position",
  /** `detail` for 503: `{error:"llm_provider_unavailable", model}` */
  LLM_PROVIDER_UNAVAILABLE: "llm_provider_unavailable",
  /** `detail` for 503: `{error:"llm_provider_key_missing", model}` */
  LLM_PROVIDER_KEY_MISSING: "llm_provider_key_missing",
  /** `detail` prefixed with `invalid_model:` then the model id */
  INVALID_MODEL_PREFIX: "invalid_model:",
} as const;
