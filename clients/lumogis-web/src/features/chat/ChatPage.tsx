// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Streaming chat surface — parent plan §"Phase 1 Pass 1.2 item 7" + item 8.
//
// Wires the per-tab `useChatThreads` reducer to the SSE stream parser
// (`ChatStream.consumeChatStream`) and the typed `ApiClient` (Pass 1.1) so
// every chat request:
//
//  * carries `Authorization: Bearer <jwt>` + `credentials: "include"`,
//  * benefits from the single-flight 401-refresh interceptor,
//  * can be aborted by the user (the AbortController lives in the React state
//    so the "Stop" button can call `controller.abort()`),
//  * surfaces shipped error literals (`llm_provider_key_missing`,
//    `llm_provider_unavailable`, `last_message_must_be_user`,
//    `system_message_position`, `invalid_model:<id>`) verbatim so the toast
//    and inline error copy can match the wire literal in tests.
//
// We intentionally co-locate the `ChatPage` component, the `useModelCatalog`
// hook, and a couple of pure helpers (`formatTimestamp`, `messageToDto`) in
// the same file. The react-refresh `only-export-components` rule degrades HMR
// slightly when components and helpers share a module — same DX trade-off
// `AuthProvider.tsx` and `BottomNav.tsx` already make.
/* eslint-disable react-refresh/only-export-components */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { patchMeWowDismissed } from "../../api/meWow";
import { useAuth } from "../../auth/AuthProvider";
import { ChatComposer } from "../../components/ChatComposer";
import { Button } from "../../components/Button";
import { ServiceDegradationBanner } from "../_shared/ServiceDegradationBanner";
import { useServiceHealth } from "../_shared/useServiceHealth";
import type { ChatPrefillState } from "../wow/askAboutEntity";
import { WowGate } from "../wow/WowGate";
import type { ChatMessageDTO } from "../../api/chat";
import {
  appendConversationMessage,
  continueConversation,
  upsertWebConversation,
} from "../../api/conversations";
import type { ApiClient } from "../../api/client";
import { postSessionEnd } from "../../api/session";
import type { ModelDescriptor, ModelsResponse } from "../../api/models";
import {
  deleteDraft,
  getDraft,
  makeChatDraftKey,
  setDraft,
} from "../../pwa/drafts";
import { useOnlineStatus } from "../../pwa/useOnlineStatus";
import { consumeChatStream } from "./ChatStream";
import { ConversationSidebar, type PendingSummary } from "./ConversationSidebar";
import { messageIdForServerSync } from "./messageIdForServerSync";
import {
  useChatThreads,
  type ChatMessage,
  type ChatThread,
} from "./threadStore";

const DEFAULT_MODEL = "claude";

export function ChatPage(): JSX.Element {
  const { client } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const serviceHealth = useServiceHealth(client);
  const refreshHealth = serviceHealth.refresh;
  const { models, modelError, refreshModels } = useModelCatalog(client);
  const initialModel = models[0]?.id ?? DEFAULT_MODEL;

  const threads = useChatThreads({ defaultModel: initialModel });
  const { state, dispatch, active, newThread, selectThread, deleteThread, setModel } = threads;

  // Set the model on the seed thread once the catalog resolves so the user
  // doesn't have to hand-pick on first load.
  useEffect(() => {
    if (active === null) return;
    if (models.length === 0) return;
    if (models.some((m) => m.id === active.model)) return;
    setModel(active.id, models[0]!.id);
  }, [active, models, setModel]);

  /** Tracks the `?session=<id>` we have already hydrated so it fires once. */
  const hydratedSessionRef = useRef<string | null>(null);

  const [input, setInput] = useState("");
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [pendingSummaries, setPendingSummaries] = useState<PendingSummary[]>([]);
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Mirrors composer text for IndexedDB flush on thread switches / unload. */
  const inputMirrorRef = useRef("");
  const draftTimerRef = useRef<number | null>(null);
  const prevActiveIdRef = useRef<string | null>(null);
  const submissionBackupRef = useRef("");
  const lastThreadIdRef = useRef<string | null>(null);
  const wowDismissOnSendRef = useRef(false);
  const suppressDraftHydrateRef = useRef(false);

  useEffect(() => {
    inputMirrorRef.current = input;
  }, [input]);

  const applyComposerPrefill = useCallback(
    (text: string, options?: { wowDismissOnSend?: boolean }) => {
      wowDismissOnSendRef.current = options?.wowDismissOnSend === true;
      suppressDraftHydrateRef.current = true;
      setInput(text);
      inputMirrorRef.current = text;
      composerRef.current?.focus();
    },
    [],
  );

  useEffect(() => {
    const state = location.state as ChatPrefillState | null;
    if (state === null || state === undefined || typeof state.prefill !== "string") {
      return;
    }
    wowDismissOnSendRef.current = state.wowDismissOnSend === true;
    applyComposerPrefill(state.prefill);
    void navigate(".", { replace: true, state: {} });
  }, [applyComposerPrefill, location.state, navigate]);

  useEffect(() => {
    lastThreadIdRef.current = active?.id ?? null;
  }, [active?.id]);

  const cancelStream = useCallback((): void => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const endSessionForThread = useCallback(
    (thread: ChatThread | null | undefined) => {
      if (thread === null || thread === undefined) return;
      if (thread.messages.length === 0) return;
      postSessionEnd(client, {
        session_id: thread.id,
        messages: thread.messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map(messageToDto),
      });
      // Show a "Summarising…" placeholder until the batch job writes the
      // sessions row, so the history list never looks broken-empty (LUM-417).
      setPendingSummaries((prev) =>
        prev.some((p) => p.conversationId === thread.id)
          ? prev
          : [...prev, { conversationId: thread.id, title: thread.title }],
      );
      setHistoryRefresh((n) => n + 1);
    },
    [client],
  );

  const handlePendingResolved = useCallback((conversationIds: string[]) => {
    setPendingSummaries((prev) =>
      prev.filter((p) => !conversationIds.includes(p.conversationId)),
    );
  }, []);

  const registerServerThread = useCallback(
    (threadId: string, model: string, title: string) => {
      void upsertWebConversation(client, threadId, { model, title }).catch(() => {
        /* Best-effort slice-2 sync; ephemeral tab state remains authoritative. */
      });
    },
    [client],
  );

  useEffect(() => {
    const threadId = active?.id;
    const model = active?.model;
    const title = active?.title;
    if (threadId === undefined || model === undefined || title === undefined) return;
    registerServerThread(threadId, model, title);
  }, [active?.id, active?.model, active?.title, registerServerThread]);

  const scheduleTurnSync = useCallback(
    (threadId: string, model: string, messages: ChatMessage[]) => {
      if (messages.length === 0) return;
      if (syncTimerRef.current !== null) clearTimeout(syncTimerRef.current);
      syncTimerRef.current = setTimeout(() => {
        for (const message of messages) {
          void appendConversationMessage(client, threadId, {
            message_id: messageIdForServerSync(message.id),
            role: message.role,
            content: message.content,
            model,
          }).catch(() => undefined);
        }
      }, 400);
    },
    [client],
  );

  const handleNewThread = useCallback(() => {
    cancelStream();
    endSessionForThread(active);
    const id = newThread();
    registerServerThread(id, active?.model ?? initialModel, "New chat");
  }, [active, cancelStream, endSessionForThread, initialModel, newThread, registerServerThread]);

  const handleSelectThread = useCallback(
    (id: string) => {
      cancelStream();
      if (active !== null && active.id !== id) {
        endSessionForThread(active);
      }
      selectThread(id);
    },
    [active, cancelStream, endSessionForThread, selectThread],
  );

  const handleContinueFromHistory = useCallback(
    (seedMessages: ChatMessageDTO[]) => {
      endSessionForThread(active);
      const id = newThread();
      const createdAt = Date.now();
      registerServerThread(id, active?.model ?? initialModel, "Continued chat");
      dispatch({
        type: "LOAD_SEED_MESSAGES",
        threadId: id,
        messages: seedMessages,
        createdAt,
      });
    },
    [active, dispatch, endSessionForThread, initialModel, newThread, registerServerThread],
  );

  // Slice-2 server restore (LUM-414): when the page is opened at
  // `/chat?session=<id>`, hydrate the conversation transcript from the server
  // by reusing the existing continue-from-history mechanism
  // (`continueConversation` -> `LOAD_SEED_MESSAGES`). The mechanism itself is
  // unchanged; this only wires it from the URL param.
  const hydrateFromSession = useCallback(
    async (sessionId: string): Promise<void> => {
      try {
        const res = await continueConversation(client, sessionId);
        handleContinueFromHistory(res.seed_messages);
      } catch {
        /* Unknown/invalid session id: leave the fresh thread intact. */
      }
    },
    [client, handleContinueFromHistory],
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get("session");
    if (sessionId === null || sessionId.length === 0) return;
    if (hydratedSessionRef.current === sessionId) return;
    hydratedSessionRef.current = sessionId;
    void hydrateFromSession(sessionId);
    // Drop the param so a reload/remount does not re-trigger hydration and the
    // URL stays clean once the transcript is loaded.
    params.delete("session");
    const query = params.toString();
    const cleaned = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", cleaned);
  }, [hydrateFromSession]);

  // Phase 3C: hydrate composer from IndexedDB per thread; flush outgoing thread draft before loading the next.
  useEffect(() => {
    const currentId = active?.id ?? null;
    const prevId = prevActiveIdRef.current;

    if (prevId !== null && prevId !== currentId) {
      if (draftTimerRef.current !== null) {
        clearTimeout(draftTimerRef.current);
        draftTimerRef.current = null;
      }
      void setDraft(makeChatDraftKey(prevId), inputMirrorRef.current);
    }

    prevActiveIdRef.current = currentId;

    let cancelled = false;
    void (async () => {
      if (currentId === null) return;
      const mirrorBeforeHydrate = inputMirrorRef.current;
      const d = await getDraft(makeChatDraftKey(currentId));
      if (cancelled) return;
      // Still on this thread (user did not switch away).
      if (lastThreadIdRef.current !== currentId) return;
      if (suppressDraftHydrateRef.current) {
        suppressDraftHydrateRef.current = false;
        return;
      }
      // If the user typed while IndexedDB was slow, do not stomp live input.
      if (inputMirrorRef.current !== mirrorBeforeHydrate) return;
      setInput(d ?? "");
      inputMirrorRef.current = d ?? "";
    })();

    return () => {
      cancelled = true;
    };
  }, [active?.id]);

  useEffect(() => {
    return () => {
      if (draftTimerRef.current !== null) {
        clearTimeout(draftTimerRef.current);
        draftTimerRef.current = null;
      }
      const id = lastThreadIdRef.current;
      if (!id) return;
      void setDraft(makeChatDraftKey(id), inputMirrorRef.current);
    };
  }, []);

  const schedulePersistDraft = useCallback((raw: string) => {
    if (active === null) return;
    const key = makeChatDraftKey(active.id);
    if (draftTimerRef.current !== null) {
      clearTimeout(draftTimerRef.current);
      draftTimerRef.current = null;
    }
    draftTimerRef.current = window.setTimeout(() => {
      draftTimerRef.current = null;
      void setDraft(key, raw);
    }, 400);
  }, [active]);

  // Cancel any in-flight stream when the active thread changes or the
  // component unmounts so we don't spill deltas into the wrong bubble.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [state.activeId]);

  const onSubmit = useCallback(
    async (e?: FormEvent<HTMLFormElement>): Promise<void> => {
      e?.preventDefault();
      if (!online) return;
      if (active === null || streaming) return;
      const text = input.trim();
      if (text.length === 0) return;

      const draftKey = makeChatDraftKey(active.id);
      submissionBackupRef.current = input;
      if (draftTimerRef.current !== null) {
        clearTimeout(draftTimerRef.current);
        draftTimerRef.current = null;
      }

      const restoreComposer = (): void => {
        const backup = submissionBackupRef.current;
        setInput(backup);
        void setDraft(draftKey, backup);
      };

      const userMessageId = generateId("u");
      const assistantMessageId = generateId("a");
      const created = Date.now();
      dispatch({
        type: "APPEND_USER",
        threadId: active.id,
        messageId: userMessageId,
        content: text,
        createdAt: created,
      });
      dispatch({
        type: "BEGIN_ASSISTANT",
        threadId: active.id,
        messageId: assistantMessageId,
        createdAt: created,
      });
      setInput("");
      setSubmitError(null);
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      const wireMessages: ChatMessageDTO[] = [
        ...active.messages.filter((m) => m.role !== "assistant" || m.status === "complete").map(messageToDto),
        { role: "user", content: text },
      ];

      try {
        const res = await client.fetch("/api/v1/chat/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({
            model: active.model,
            messages: wireMessages,
            stream: true,
          }),
          signal: controller.signal,
        });

        if (!res.ok) {
          const detail = await safeReadDetail(res);
          dispatch({
            type: "FAIL_ASSISTANT",
            threadId: active.id,
            messageId: assistantMessageId,
            detail: humaniseChatError(res.status, detail),
          });
          setSubmitError(humaniseChatError(res.status, detail));
          restoreComposer();
          // A non-OK status (e.g. 503 when Ollama is down) is the strongest
          // live signal a service is unavailable — reconcile the banner now.
          refreshHealth();
          return;
        }

        let sawDelta = false;
        let assistantContent = "";
        let streamError: string | null = null;
        await consumeChatStream(res.body, {
          onDelta: (delta: string) => {
            sawDelta = true;
            assistantContent += delta;
            dispatch({
              type: "APPEND_ASSISTANT_DELTA",
              threadId: active.id,
              messageId: assistantMessageId,
              delta,
            });
          },
          onError: (msg: string) => {
            streamError = msg;
          },
        });

        if (controller.signal.aborted) {
          dispatch({
            type: "ABORT_ASSISTANT",
            threadId: active.id,
            messageId: assistantMessageId,
          });
          restoreComposer();
          return;
        }

        if (streamError !== null) {
          const detail = humaniseStreamError(streamError);
          dispatch({
            type: "FAIL_ASSISTANT",
            threadId: active.id,
            messageId: assistantMessageId,
            detail,
          });
          setSubmitError(detail);
          restoreComposer();
          // The request is the source of truth — reconcile the health banner now.
          refreshHealth();
          return;
        }

        if (!sawDelta) {
          dispatch({
            type: "FAIL_ASSISTANT",
            threadId: active.id,
            messageId: assistantMessageId,
            detail: "The model returned no content.",
          });
          restoreComposer();
          return;
        }

        dispatch({
          type: "FINISH_ASSISTANT",
          threadId: active.id,
          messageId: assistantMessageId,
        });
        scheduleTurnSync(active.id, active.model, [
          {
            id: userMessageId,
            role: "user",
            content: text,
            createdAt: created,
          },
          {
            id: assistantMessageId,
            role: "assistant",
            content: assistantContent,
            createdAt: created,
            status: "complete",
          },
        ]);
        await deleteDraft(draftKey);
        if (wowDismissOnSendRef.current) {
          wowDismissOnSendRef.current = false;
          void patchMeWowDismissed(client).catch(() => undefined);
        }
      } catch (err) {
        if (controller.signal.aborted) {
          dispatch({
            type: "ABORT_ASSISTANT",
            threadId: active.id,
            messageId: assistantMessageId,
          });
          restoreComposer();
        } else {
          const detail = err instanceof Error ? err.message : "request_failed";
          dispatch({
            type: "FAIL_ASSISTANT",
            threadId: active.id,
            messageId: assistantMessageId,
            detail,
          });
          setSubmitError(detail);
          restoreComposer();
          refreshHealth();
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setStreaming(false);
      }
    },
    [active, client, dispatch, input, online, streaming, scheduleTurnSync, refreshHealth],
  );

  return (
    <div className="lumogis-chat" data-testid="chat-page">
      <aside className="lumogis-chat__threads" aria-label="Conversations">
        <div className="lumogis-chat__threads-head">
          <h2 className="lumogis-chat__heading">Conversations</h2>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={handleNewThread}
            className="lumogis-chat__new-thread"
          >
            + New chat
          </Button>
        </div>
        <ConversationSidebar
          client={client}
          refreshToken={historyRefresh}
          onContinue={handleContinueFromHistory}
          pendingSummaries={pendingSummaries}
          onPendingResolved={handlePendingResolved}
        />
        <ul className="lumogis-chat__thread-list" role="list">
          {state.threads.map((t) => (
            <li key={t.id}>
              <ThreadRow
                thread={t}
                active={t.id === state.activeId}
                onSelect={() => handleSelectThread(t.id)}
                onDelete={() => {
                  cancelStream();
                  deleteThread(t.id);
                }}
              />
            </li>
          ))}
        </ul>
        <p className="lumogis-chat__threads-note" id="lumogis-chat-ephemeral-note">
          Active tab threads are ephemeral. Ended chats appear in History after summarization.
        </p>
      </aside>

      <section className="lumogis-chat__main" aria-label="Chat">
        <header className="lumogis-chat__main-head">
          <ModelPicker
            models={models}
            value={active?.model ?? DEFAULT_MODEL}
            onChange={(id) => active && setModel(active.id, id)}
            disabled={streaming}
            error={modelError}
            onRetry={() => void refreshModels()}
          />
        </header>

        <ServiceDegradationBanner health={serviceHealth} />

        <div
          className="lumogis-chat__messages"
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          aria-busy={streaming}
          aria-label="Conversation transcript"
        >
          {active === null || active.messages.length === 0 ? (
            <p className="lumogis-chat__empty">
              Lumogis chats are ephemeral and live only in this browser tab. Closing the tab
              discards them — use Capture or Search when you want durable notes.
            </p>
          ) : (
            active.messages.map((m) => <MessageBubble key={m.id} message={m} />)
          )}
        </div>

        {submitError !== null && (
          <p role="alert" className="lumogis-chat__error">
            {submitError}
          </p>
        )}

        <WowGate onPrefillComposer={applyComposerPrefill} />

        <ChatComposer
          value={input}
          onChange={(next) => {
            setInput(next);
            schedulePersistDraft(next);
          }}
          onSubmit={() => void onSubmit()}
          streaming={streaming}
          onStop={() => cancelStream()}
          disabled={streaming}
          sendDisabled={!online}
          placeholder="Ask Lumogis…"
          describedBy="lumogis-chat-ephemeral-note"
          textareaRef={composerRef}
          minRows={3}
          maxRows={8}
        />
        <p id="lumogis-chat-ephemeral-note" className="lumogis-chat__threads-note">
          Ephemeral — closing this tab discards the thread.
        </p>
      </section>
    </div>
  );
}

interface ThreadRowProps {
  thread: ChatThread;
  active: boolean;
  onSelect(): void;
  onDelete(): void;
}

function ThreadRow({ thread, active, onSelect, onDelete }: ThreadRowProps): JSX.Element {
  return (
    <div className={`lumogis-chat__thread${active ? " lumogis-chat__thread--active" : ""}`}>
      <button
        type="button"
        onClick={onSelect}
        className="lumogis-chat__thread-button"
        aria-current={active ? "true" : undefined}
      >
        <span className="lumogis-chat__thread-title">{thread.title}</span>
        <span className="lumogis-chat__thread-meta">
          {thread.messages.length === 0 ? "Empty" : `${thread.messages.length} msg`}
        </span>
      </button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={onDelete}
        aria-label={`Delete ${thread.title}`}
        className="lumogis-chat__thread-delete"
      >
        ×
      </Button>
    </div>
  );
}

interface ModelPickerProps {
  models: ModelDescriptor[];
  value: string;
  onChange(id: string): void;
  disabled: boolean;
  error: string | null;
  onRetry(): void;
}

function ModelPicker({
  models,
  value,
  onChange,
  disabled,
  error,
  onRetry,
}: ModelPickerProps): JSX.Element {
  if (error !== null) {
    return (
      <div className="lumogis-chat__model-error" role="alert">
        <span>Unable to load models: {error}.</span>
        <Button type="button" variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (models.length === 0) {
    return (
      <label className="lumogis-chat__model-picker">
        <span>Model</span>
        <span className="lumogis-chat__model-loading" aria-busy="true">
          <span className="lumogis-chat__model-spinner" aria-hidden />
          Loading…
        </span>
      </label>
    );
  }
  return (
    <label className="lumogis-chat__model-picker">
      <span>Model</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        aria-label="Model"
      >
        {models.map((m) => (
          <option key={m.id} value={m.id} disabled={!m.enabled}>
            {m.label}
            {m.is_local ? " (local)" : ""}
            {m.enabled ? "" : " — unavailable"}
          </option>
        ))}
      </select>
    </label>
  );
}

function MessageBubble({ message }: { message: ChatMessage }): JSX.Element {
  const isAssistant = message.role === "assistant";
  const status = message.status ?? "complete";
  const className = [
    "lumogis-chat__bubble",
    `lumogis-chat__bubble--${message.role}`,
    isAssistant && status !== "complete" ? `lumogis-chat__bubble--${status}` : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <article className={className} aria-label={`${message.role} message`}>
      <header className="lumogis-chat__bubble-meta">
        <span className="lumogis-chat__bubble-role">
          {message.role === "assistant" ? "Lumogis" : message.role === "user" ? "You" : "System"}
        </span>
        <time className="lumogis-chat__bubble-time" dateTime={new Date(message.createdAt).toISOString()}>
          {formatTimestamp(message.createdAt)}
        </time>
      </header>
      <div className="lumogis-chat__bubble-content">
        {isAssistant && status === "streaming" && message.content.length === 0 ? (
          <span
            className="lumogis-chat__typing"
            role="status"
            aria-label="Lumogis is typing"
            data-testid="chat-typing-indicator"
          >
            <span className="lumogis-chat__typing-dot" aria-hidden="true" />
            <span className="lumogis-chat__typing-dot" aria-hidden="true" />
            <span className="lumogis-chat__typing-dot" aria-hidden="true" />
          </span>
        ) : (
          <>
            {message.content}
            {isAssistant && status === "streaming" && (
              <span aria-hidden="true" className="lumogis-chat__caret">▍</span>
            )}
          </>
        )}
      </div>
      {isAssistant && status === "error" && message.errorDetail !== undefined && (
        <p className="lumogis-chat__bubble-error" role="status">
          {message.errorDetail}
        </p>
      )}
      {isAssistant && status === "aborted" && (
        <p className="lumogis-chat__bubble-error" role="status">
          Stopped.
        </p>
      )}
    </article>
  );
}

interface ModelCatalogResult {
  models: ModelDescriptor[];
  modelError: string | null;
  refreshModels(): Promise<void>;
}

export function useModelCatalog(client: ApiClient): ModelCatalogResult {
  const [models, setModels] = useState<ModelDescriptor[]>([]);
  const [modelError, setModelError] = useState<string | null>(null);
  const refreshModels = useCallback(async (): Promise<void> => {
    setModelError(null);
    try {
      const res = await client.getJson<ModelsResponse>("/api/v1/models");
      setModels(res.models);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "models_unavailable");
      setModels([]);
    }
  }, [client]);

  useEffect(() => {
    void refreshModels();
  }, [refreshModels]);

  return useMemo(
    () => ({ models, modelError, refreshModels }),
    [models, modelError, refreshModels],
  );
}

function generateId(prefix: string): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  const tail = c?.randomUUID?.() ?? `${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
  return `${prefix}_${tail}`;
}

export function messageToDto(m: ChatMessage): ChatMessageDTO {
  return { role: m.role, content: m.content };
}

export function formatTimestamp(epochMs: number): string {
  const d = new Date(epochMs);
  const hh = d.getHours().toString().padStart(2, "0");
  const mm = d.getMinutes().toString().padStart(2, "0");
  return `${hh}:${mm}`;
}

async function safeReadDetail(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    const d = body?.detail;
    if (typeof d === "string") return d;
    if (d !== null && typeof d === "object") {
      const err = (d as { error?: unknown }).error;
      if (typeof err === "string") return err;
      return JSON.stringify(d);
    }
    return res.statusText || "request_failed";
  } catch {
    return res.statusText || "request_failed";
  }
}

/** Map shipped wire literals to user-facing copy. */
export function humaniseChatError(status: number, detail: string): string {
  if (status === 503 && detail === "llm_provider_key_missing") {
    return "No LLM provider key is configured. Add one in Settings → LLM Providers.";
  }
  if (status === 503 && detail === "llm_provider_unavailable") {
    return "The model is unavailable right now. Try again in a moment.";
  }
  if (status === 400 && detail === "last_message_must_be_user") {
    return "The last message must be from you. Reload the conversation and try again.";
  }
  if (status === 400 && detail === "system_message_position") {
    return "System messages must appear before any user message.";
  }
  if (status === 400 && detail.startsWith("invalid_model:")) {
    return `Selected model is not available: ${detail.slice("invalid_model:".length)}.`;
  }
  if (status === 401) {
    return "Your session expired. Please sign in again.";
  }
  return detail.length > 0 ? `Chat failed: ${detail}` : `Chat failed (HTTP ${status}).`;
}

function humaniseStreamError(literal: string): string {
  if (literal === "malformed_chunk") return "The server sent an unreadable chunk.";
  if (literal === "empty_response_body") return "The server returned an empty stream.";
  return `Stream error: ${literal}`;
}
