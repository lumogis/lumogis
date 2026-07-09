// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Conversation history API client (LUM-162).

import type { ChatMessageDTO } from "./chat";
import type { ApiClient } from "./client";

// LUM-582 Rung 1 — household conversation sharing (synchronous publish).
export interface ConversationShareFields {
  share_status?: "personal" | "shared";
  is_owner?: boolean;
  can_share?: boolean;
  shared_summary?: string | null;
}

export interface ConversationSummary extends ConversationShareFields {
  conversation_id: string;
  title: string;
  summary: string;
  ended_at: string;
  scope: string;
  message_count: number | null;
}

export interface ConversationMessage {
  message_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  model: string | null;
}

export interface ConversationDetail extends ConversationShareFields {
  conversation_id: string;
  title: string;
  summary: string;
  topics: string[];
  entities: string[];
  ended_at: string;
  scope: string;
  messages: ConversationMessage[];
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
}

export interface ConversationDeleteResponse {
  deleted: boolean;
  conversation_id: string;
  partial: boolean;
}

export interface ConversationContinueResponse {
  seed_messages: ChatMessageDTO[];
  conversation_id: string | null;
}

export async function listConversations(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<ConversationListResponse> {
  return client.getJson<ConversationListResponse>("/api/v1/conversations", { signal });
}

export async function getConversation(
  client: ApiClient,
  conversationId: string,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  return client.getJson<ConversationDetail>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
    { signal },
  );
}

export async function deleteConversation(
  client: ApiClient,
  conversationId: string,
): Promise<ConversationDeleteResponse> {
  return client.delete<ConversationDeleteResponse>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
  );
}

// LUM-582 Rung 1 — share a conversation with the household (synchronous). The
// conversation id IS the backing session id; the publish route is keyed by
// session. ``shared_summary`` is the editable household-facing summary.
export async function publishConversation(
  client: ApiClient,
  conversationId: string,
  body: { shared_summary?: string } = {},
): Promise<unknown> {
  return client.postJson<{ scope: "shared"; shared_summary?: string }, unknown>(
    `/api/v1/sessions/${encodeURIComponent(conversationId)}/publish`,
    { scope: "shared", ...body },
  );
}

export async function unpublishConversation(
  client: ApiClient,
  conversationId: string,
): Promise<void> {
  return client.delete<void>(
    `/api/v1/sessions/${encodeURIComponent(conversationId)}/publish`,
  );
}

export async function continueConversation(
  client: ApiClient,
  conversationId: string,
  body?: { model?: string },
): Promise<ConversationContinueResponse> {
  return client.postJson<{ model?: string }, ConversationContinueResponse>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/continue`,
    body ?? {},
  );
}

export async function appendConversationMessage(
  client: ApiClient,
  conversationId: string,
  body: {
    message_id: string;
    role: "user" | "assistant" | "system";
    content: string;
    model?: string;
  },
): Promise<ConversationMessage> {
  return client.postJson<
    { message_id: string; role: "user" | "assistant" | "system"; content: string; model?: string },
    ConversationMessage
  >(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, body);
}

export async function upsertWebConversation(
  client: ApiClient,
  conversationId: string,
  body: { title?: string; model?: string },
): Promise<ConversationSummary> {
  return client.putJson<{ title?: string; model?: string }, ConversationSummary>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
    body,
  );
}
