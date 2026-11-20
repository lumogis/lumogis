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
// `AuthProvider.tsx` already makes.
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
import { transcribeVoice } from "../../api/voice";
import { Button } from "../../components/ui/button";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "../../components/ui/sheet";
import { InfoTip } from "../../components/page/InfoTip";
import { help } from "../../copy/help";
import { useShellState } from "../../api/meShell";
import { PanelLeft, Plus, SquarePen, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { AnswerText } from "./AnswerText";
import { ChatHome } from "./ChatHome";
import { ModelChip } from "./ModelChip";
import { EmptyState } from "../_shared/EmptyState";
import { PrivacyBlockedHint } from "../_shared/PrivacyBlockedHint";
import { catalogHasPrivacyBlock } from "../_shared/privacyBlockCopy";
import { ServiceDegradationBanner } from "../_shared/ServiceDegradationBanner";
import { useServiceHealth } from "../_shared/useServiceHealth";
import { useDocuments } from "../documents/useDocuments";
import { useDocumentsSseInvalidation } from "../documents/useDocumentsSseInvalidation";
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
import { sourceNames } from "./sourceNames";
import { ConversationSidebar, type PendingSummary } from "./ConversationSidebar";
import { messageIdForServerSync } from "./messageIdForServerSync";
import {
  useChatThreads,
  type ChatMessage,
  type ChatThread,
} from "./threadStore";
import { randomUuid } from "../../util/uuid";

const DEFAULT_MODEL = "claude";

function ChatLibraryEmptyState(): JSX.Element {
  return (
    <EmptyState
      className="rounded-xl border border-border py-6"
      title="No documents in your library yet"
      helperText="Chat answers from the documents in your library. Connect a document archive, feed or watched page, or upload a file, and Lumogis can answer from it."
      actions={[
        { label: "Connect a source", href: "/settings/sources", primary: true },
        { label: "Upload a file", href: "/documents" },
      ]}
    />
  );
}

export function ChatPage(): JSX.Element {
  const { client, tokens } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const serviceHealth = useServiceHealth(client);
  const refreshHealth = serviceHealth.refresh;
  const { models, modelError, refreshModels } = useModelCatalog(client);
  // LUM-310: start on a model that can answer. The server lists enabled models
  // first, but do not rely on the order.
  const initialModel = (models.find((m) => m.enabled) ?? models[0])?.id ?? DEFAULT_MODEL;

  // LUM-853: first run on a default install. The onboarding modal is dark when
  // auth is off (ADR 059 as amended), so an empty library is announced here,
  // on the first screen. "Empty" means the list LOADED with zero rows — never
  // while loading, never on error (no flash, no false claim).
  // (A Postgres outage reads as an empty library here, because services/documents.py
  // list_documents returns [] on a DB error — accepted, P2. A failed background
  // refetch keeps the last data, so the state persists while the server fails.)
  const documentsQuery = useDocuments(client, { pollWhileBusy: false });
  const libraryEmpty = documentsQuery.isSuccess && documentsQuery.data.documents.length === 0;
  useDocumentsSseInvalidation(tokens, { enabled: libraryEmpty });

  const threads = useChatThreads({ defaultModel: initialModel });
  const { state, dispatch, active, newThread, selectThread, deleteThread, setModel } = threads;

  // Set the model on the seed thread once the catalog resolves so the user
  // doesn't have to hand-pick on first load. A model that is listed but off
  // (no API key, not turned on, privacy mode) is replaced too, when one is on:
  // on a fresh install Lumogis Web often opens before the orchestrator answers,
  // the seed thread falls back to DEFAULT_MODEL, and "claude — no API key" stuck
  // although the local model was ready (LUM-310). The picker disables off
  // models, so this never overrides a choice the user could have made.
  useEffect(() => {
    if (active === null) return;
    if (models.length === 0) return;
    const current = models.find((m) => m.id === active.model);
    if (current?.enabled) return;
    const next = models.find((m) => m.enabled) ?? (current ? undefined : models[0]);
    if (next && next.id !== active.model) setModel(active.id, next.id);
  }, [active, models, setModel]);

  /** Tracks the `?session=<id>` we have already hydrated so it fires once. */
  const hydratedSessionRef = useRef<string | null>(null);

  const threadEmpty = active === null || active.messages.length === 0;

  const [input, setInput] = useState("");
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [streaming, setStreaming] = useState(false);
  // Transient stream status (LUM-876), e.g. "Loading model on your machine…".
  // Shown in the empty assistant bubble only; never stored with the answer.
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  // Phone: the chat list opens in a sheet (LUM-993).
  const [chatsOpen, setChatsOpen] = useState(false);
  const shell = useShellState();
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
      setStreamStatus(null);

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
        let sources: string[] = [];
        await consumeChatStream(res.body, {
          onContextCitations: (citations) => {
            sources = sourceNames(citations);
          },
          onStatus: (status) => {
            setStreamStatus(status.message);
          },
          onDelta: (delta: string) => {
            sawDelta = true;
            assistantContent += delta;
            setStreamStatus(null);
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
          sources,
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
        setStreamStatus(null);
      }
    },
    [active, client, dispatch, input, online, streaming, scheduleTurnSync, refreshHealth],
  );

  const historyNoteId = "lumogis-chat-history-note";

  const composer = (
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
      describedBy={historyNoteId}
      textareaRef={composerRef}
      // LUM-993 Decision 16: the microphone appears only while a speech engine runs.
      voiceTranscribe={
        shell?.voice_available === true ? (blob) => transcribeVoice(client, blob).then((r) => r.text) : undefined
      }
      modelSlot={
        <ModelChip
          models={models}
          value={active?.model ?? DEFAULT_MODEL}
          onChange={(id) => active && setModel(active.id, id)}
          disabled={streaming}
          error={modelError}
          onRetry={() => void refreshModels()}
        />
      }
      maxRows={8}
    />
  );

  const footer = (
    <>
      {catalogHasPrivacyBlock(models) ? <PrivacyBlockedHint surface="picker" /> : null}
      <p id={historyNoteId} className="m-0 flex items-center justify-center gap-0.5 text-center text-xs text-muted-foreground">
        {help.chat.history}
        <InfoTip about="chat history">{help.chat.historyAbout}</InfoTip>
      </p>
    </>
  );

  // Rendered in the wide-screen panel and, on phones, in the Chats sheet; while
  // the sheet is open both copies are mounted (each fetches the history once),
  // so ids carry the copy's name.
  const renderChatList = (where: "panel" | "sheet") => (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-3">
      <Button variant="outline" className="w-full justify-start" onClick={() => {
        handleNewThread();
        setChatsOpen(false);
      }}>
        <Plus aria-hidden="true" />
        New chat
      </Button>
      <section aria-labelledby={`lumogis-chat-open-heading-${where}`} className="flex flex-col gap-1">
        <h2 id={`lumogis-chat-open-heading-${where}`} className="m-0 px-2 text-xs font-medium text-muted-foreground">
          Open in this tab
        </h2>
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0" role="list">
          {state.threads.map((t) => (
            <li key={t.id}>
              <ThreadRow
                thread={t}
                active={t.id === state.activeId}
                onSelect={() => {
                  handleSelectThread(t.id);
                  setChatsOpen(false);
                }}
                onDelete={() => {
                  cancelStream();
                  deleteThread(t.id);
                }}
              />
            </li>
          ))}
        </ul>
      </section>
      <ConversationSidebar
        client={client}
        refreshToken={historyRefresh}
        onContinue={(c) => {
          setChatsOpen(false);
          return handleContinueFromHistory(c);
        }}
        pendingSummaries={pendingSummaries}
        onPendingResolved={handlePendingResolved}
      />
    </div>
  );

  // Decision 8: below an empty chat's composer, the library card when the
  // library is empty, otherwise the suggestion chips (WowGate renders nothing
  // when there are none).
  const emptyRegion = libraryEmpty ? <ChatLibraryEmptyState /> : <WowGate onPrefillComposer={applyComposerPrefill} />;

  return (
    // data-slot opts the page into the scoped reset (ui.css): no browser button and
    // textarea defaults inside it (LUM-993).
    <div className="flex h-full min-h-0" data-testid="chat-page" data-slot="chat-page">
      <aside
        aria-label="Conversations"
        className="hidden w-64 shrink-0 flex-col border-r border-border @min-[900px]/layout:flex"
      >
        {renderChatList("panel")}
      </aside>

      <section aria-label="Chat" className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 pb-2 @min-[900px]/layout:hidden">
          <Sheet open={chatsOpen} onOpenChange={setChatsOpen}>
            <SheetTrigger render={<Button variant="ghost" size="sm" />}>
              <PanelLeft aria-hidden="true" />
              Chats
            </SheetTrigger>
            <SheetContent side="left" className="w-[85%] max-w-xs gap-0 p-0">
              <SheetHeader>
                <SheetTitle>Chats</SheetTitle>
              </SheetHeader>
              {renderChatList("sheet")}
            </SheetContent>
          </Sheet>
          <Button variant="ghost" size="icon-sm" className="ml-auto" aria-label="Start a new chat" onClick={handleNewThread}>
            <SquarePen aria-hidden="true" />
          </Button>
        </div>

        <ServiceDegradationBanner health={serviceHealth} />

        {/* One transcript element for the life of the page: a live region that
            appears already holding content is often not announced, so the first
            message must arrive in a region that was already there (LUM-993). */}
        <div
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          aria-busy={streaming}
          aria-label="Conversation transcript"
          className={threadEmpty ? "sr-only" : "min-h-0 flex-1 overflow-y-auto"}
        >
          {threadEmpty ? null : (
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-1 py-4">
              {active.messages.map((m) => (
                <MessageBubble key={m.id} message={m} statusNote={m.status === "streaming" ? streamStatus : null} />
              ))}
            </div>
          )}
        </div>
        {threadEmpty ? (
          <ChatHome
            composer={
              <div className="flex flex-col gap-2">
                {composer}
                {submitError !== null ? <ChatError message={submitError} /> : null}
                {footer}
              </div>
            }
            below={emptyRegion}
          />
        ) : (
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 pt-2">
            {submitError !== null ? <ChatError message={submitError} /> : null}
            {composer}
            {footer}
          </div>
        )}
      </section>
    </div>
  );
}

function ChatError({ message }: { message: string }): JSX.Element {
  return (
    <Alert variant="destructive">
      <TriangleAlert aria-hidden="true" />
      <AlertDescription>{message}</AlertDescription>
    </Alert>
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
    <div
      className={cn(
        "group flex items-center gap-1 rounded-md pr-1",
        active ? "bg-accent-soft text-accent-text" : "hover:bg-muted",
      )}
    >
      <Button
        type="button"
        variant="ghost"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        className="h-auto justify-start whitespace-normal font-normal hover:bg-transparent flex min-h-9 min-w-0 flex-1 flex-col items-start rounded-md px-2 py-1.5 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 pointer-coarse:min-h-[44px]"
      >
        <span className="w-full truncate text-sm">{thread.title}</span>
        <span className={cn("text-xs", active ? "text-accent-text" : "text-muted-foreground")}>
          {thread.messages.length === 0 ? "Empty" : `${thread.messages.length} msg`}
        </span>
      </Button>
      <Button variant="ghost" size="icon-xs" onClick={onDelete} aria-label={`Delete ${thread.title}`} className="text-muted-foreground hover:text-destructive">
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}

function MessageBubble({
  message,
  statusNote = null,
}: {
  message: ChatMessage;
  /** Transient status shown while the assistant bubble is still empty (LUM-876). */
  statusNote?: string | null;
}): JSX.Element {
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const status = message.status ?? "complete";
  // LUM-993: your messages sit in a soft bubble on the right; Lumogis's answers
  // are plain text across the column, like the chat apps people know.
  return (
    <article
      aria-label={`${message.role} message`}
      data-status={isAssistant ? status : undefined}
      className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}
    >
      <header className="flex items-baseline gap-2 text-xs text-muted-foreground">
        <span className="font-medium">
          {isAssistant ? "Lumogis" : isUser ? "You" : "System"}
        </span>
        <time dateTime={new Date(message.createdAt).toISOString()}>{formatTimestamp(message.createdAt)}</time>
      </header>
      <div
        className={cn(
          "text-sm leading-6 break-words",
          // Your text as typed; Lumogis's answers are formatted (AnswerText).
          isUser ? "max-w-[85%] rounded-2xl bg-muted px-4 py-2 whitespace-pre-wrap" : "w-full",
          isAssistant && status === "error" && "text-muted-foreground",
        )}
      >
        {isAssistant && status === "streaming" && message.content.length === 0 ? (
          <>
            <span
              className="inline-flex items-center gap-1 py-2"
              role="status"
              aria-label="Lumogis is typing"
              data-testid="chat-typing-indicator"
            >
              <span className="size-1.5 animate-pulse rounded-full bg-muted-foreground" aria-hidden="true" />
              <span className="size-1.5 animate-pulse rounded-full bg-muted-foreground [animation-delay:150ms]" aria-hidden="true" />
              <span className="size-1.5 animate-pulse rounded-full bg-muted-foreground [animation-delay:300ms]" aria-hidden="true" />
            </span>
            {statusNote !== null ? (
              <span className="ml-2 text-xs text-muted-foreground" role="status" data-testid="chat-stream-status">
                {statusNote}
              </span>
            ) : null}
          </>
        ) : (
          <>
            {isAssistant ? <AnswerText text={message.content} /> : message.content}
            {isAssistant && status === "streaming" && (
              <span aria-hidden="true" className="ml-0.5 inline-block animate-pulse text-accent-text">▍</span>
            )}
          </>
        )}
      </div>
      {isAssistant && status === "complete" && message.sources !== undefined && message.sources.length > 0 && (
        <p className="m-0 flex flex-wrap items-center gap-1.5 pt-1 text-xs text-muted-foreground" data-testid="chat-sources">
          <span>Looked at:</span>
          {message.sources.map((name) => (
            <span key={name} className="rounded-full border border-border px-2 py-0.5 text-foreground">
              {name}
            </span>
          ))}
        </p>
      )}
      {isAssistant && status === "error" && message.errorDetail !== undefined && (
        <p className="m-0 text-sm text-destructive" role="status">
          {message.errorDetail}
        </p>
      )}
      {isAssistant && status === "aborted" && (
        <p className="m-0 text-xs text-muted-foreground" role="status">
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
  const tail = randomUuid();
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

