// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-tab chat-thread state (parent plan §"Phase 1 Pass 1.2 item 8" +
// §Implementation sequence step 8: "v1 persistence model — chat threads are
// ephemeral and per-tab — they live only in the React useReducer for that
// browser tab, are mirrored to sessionStorage so a reload of the same tab
// restores the thread, and are NOT persisted server-side. Closing the tab
// drops the thread.").
//
// The reducer is intentionally a pure function so React 18 strict-mode double
// invocation is harmless and Vitest can exercise it without the rendering
// stack. Persistence to `sessionStorage` happens at the call-site (the
// `useChatThreads` hook below), not inside the reducer, so reducer outputs
// stay deterministic.
//
// Why sessionStorage and not localStorage:
//
//  * sessionStorage is per-tab — opening Lumogis in two tabs gives two
//    independent thread sets, matching the parent plan's "ephemeral and
//    per-tab" contract.
//  * localStorage would survive tab close, contradicting the explicit
//    "closing the tab drops the thread" pin.
//
// Why no server persistence in v1:
//
//  * Plan: "Lumogis's 'memory' is the KG + Qdrant documents/signals, not the
//    chat log."
//  * Recorded as Open Question #12 (`web_conversations` table — deferred).

import { useCallback, useEffect, useReducer, useRef } from "react";
import type { Dispatch } from "react";

import type { ChatMessageDTO } from "../../api/chat";

const SESSION_STORAGE_KEY = "lumogis-chat-threads";
const SESSION_VERSION = 1;
/** Hard cap on persisted threads to keep sessionStorage small. */
const MAX_PERSISTED_THREADS = 50;

export type AssistantStatus = "streaming" | "complete" | "error" | "aborted";

export interface ChatMessage extends ChatMessageDTO {
  /** Stable id for React keys; not sent on the wire. */
  id: string;
  /** Wall-clock instant the message was added (epoch ms). */
  createdAt: number;
  /**
   * Set on the assistant bubble while the SSE is still streaming so the UI can
   * render a caret / "stop" affordance. Undefined for user/system messages.
   */
  status?: AssistantStatus;
  /** Populated when the stream errors so the UI can show what went wrong. */
  errorDetail?: string;
}

export interface ChatThread {
  id: string;
  /**
   * Title is derived from the first user message ("New chat" until then).
   * Pure-function derivation; no LLM summarisation in v1.
   */
  title: string;
  /** Wall-clock instant the thread was created (epoch ms). */
  createdAt: number;
  /** Wall-clock instant of the most recent message (epoch ms). */
  updatedAt: number;
  /** Model id selected for this thread; used for the next request. */
  model: string;
  messages: ChatMessage[];
}

export interface ThreadState {
  threads: ChatThread[];
  /** id of the active thread; never null after the first NEW_THREAD. */
  activeId: string | null;
}

export type ThreadAction =
  | { type: "LOAD"; state: ThreadState }
  | { type: "NEW_THREAD"; id: string; model: string; createdAt: number }
  | { type: "SELECT_THREAD"; id: string }
  | { type: "DELETE_THREAD"; id: string; fallbackId: string; fallbackModel: string; fallbackCreatedAt: number }
  | { type: "SET_MODEL"; id: string; model: string }
  | { type: "APPEND_USER"; threadId: string; messageId: string; content: string; createdAt: number }
  | {
      type: "BEGIN_ASSISTANT";
      threadId: string;
      messageId: string;
      createdAt: number;
    }
  | { type: "APPEND_ASSISTANT_DELTA"; threadId: string; messageId: string; delta: string }
  | { type: "FINISH_ASSISTANT"; threadId: string; messageId: string }
  | { type: "ABORT_ASSISTANT"; threadId: string; messageId: string }
  | { type: "FAIL_ASSISTANT"; threadId: string; messageId: string; detail: string };

export const initialThreadState: ThreadState = { threads: [], activeId: null };

export function threadReducer(state: ThreadState, action: ThreadAction): ThreadState {
  switch (action.type) {
    case "LOAD":
      return action.state;

    case "NEW_THREAD": {
      const thread: ChatThread = {
        id: action.id,
        title: "New chat",
        createdAt: action.createdAt,
        updatedAt: action.createdAt,
        model: action.model,
        messages: [],
      };
      return { threads: [thread, ...state.threads], activeId: thread.id };
    }

    case "SELECT_THREAD":
      if (!state.threads.some((t) => t.id === action.id)) return state;
      return { ...state, activeId: action.id };

    case "DELETE_THREAD": {
      const remaining = state.threads.filter((t) => t.id !== action.id);
      if (remaining.length > 0) {
        const nextActive =
          state.activeId === action.id ? remaining[0]!.id : state.activeId;
        return { threads: remaining, activeId: nextActive };
      }
      const fallback: ChatThread = {
        id: action.fallbackId,
        title: "New chat",
        createdAt: action.fallbackCreatedAt,
        updatedAt: action.fallbackCreatedAt,
        model: action.fallbackModel,
        messages: [],
      };
      return { threads: [fallback], activeId: fallback.id };
    }

    case "SET_MODEL":
      return mapThread(state, action.id, (t) => ({ ...t, model: action.model }));

    case "APPEND_USER": {
      const next = mapThread(state, action.threadId, (t) => {
        const messages = [
          ...t.messages,
          {
            id: action.messageId,
            role: "user" as const,
            content: action.content,
            createdAt: action.createdAt,
          },
        ];
        return {
          ...t,
          title: t.messages.length === 0 ? deriveTitle(action.content) : t.title,
          updatedAt: action.createdAt,
          messages,
        };
      });
      return next;
    }

    case "BEGIN_ASSISTANT":
      return mapThread(state, action.threadId, (t) => ({
        ...t,
        updatedAt: action.createdAt,
        messages: [
          ...t.messages,
          {
            id: action.messageId,
            role: "assistant" as const,
            content: "",
            createdAt: action.createdAt,
            status: "streaming" as const,
          },
        ],
      }));

    case "APPEND_ASSISTANT_DELTA":
      return mapThread(state, action.threadId, (t) => ({
        ...t,
        updatedAt: Date.now(),
        messages: t.messages.map((m) =>
          m.id === action.messageId ? { ...m, content: m.content + action.delta } : m,
        ),
      }));

    case "FINISH_ASSISTANT":
      return mapThread(state, action.threadId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === action.messageId ? { ...m, status: "complete" as const } : m,
        ),
      }));

    case "ABORT_ASSISTANT":
      return mapThread(state, action.threadId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === action.messageId ? { ...m, status: "aborted" as const } : m,
        ),
      }));

    case "FAIL_ASSISTANT":
      return mapThread(state, action.threadId, (t) => ({
        ...t,
        messages: t.messages.map((m) =>
          m.id === action.messageId
            ? { ...m, status: "error" as const, errorDetail: action.detail }
            : m,
        ),
      }));

    default: {
      const _exhaustive: never = action;
      void _exhaustive;
      return state;
    }
  }
}

function mapThread(
  state: ThreadState,
  id: string,
  fn: (t: ChatThread) => ChatThread,
): ThreadState {
  let touched = false;
  const threads = state.threads.map((t) => {
    if (t.id !== id) return t;
    touched = true;
    return fn(t);
  });
  if (!touched) return state;
  return { ...state, threads };
}

/** Derive a thread title from the first user message (truncate to 80 chars). */
export function deriveTitle(firstMessage: string): string {
  const trimmed = firstMessage.trim().replace(/\s+/g, " ");
  if (trimmed.length === 0) return "New chat";
  return trimmed.length > 80 ? `${trimmed.slice(0, 79)}…` : trimmed;
}

interface PersistedShape {
  v: number;
  state: ThreadState;
}

export interface SessionPersistence {
  read(): ThreadState | null;
  write(state: ThreadState): void;
}

/** Build the default sessionStorage-backed persistence; tests inject a stub. */
export function createSessionPersistence(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = readableSessionStorage(),
): SessionPersistence {
  return {
    read(): ThreadState | null {
      if (storage === null) return null;
      const raw = storage.getItem(SESSION_STORAGE_KEY);
      if (raw === null) return null;
      try {
        const parsed = JSON.parse(raw) as PersistedShape;
        if (parsed?.v !== SESSION_VERSION) return null;
        if (!parsed.state || !Array.isArray(parsed.state.threads)) return null;
        return parsed.state;
      } catch {
        return null;
      }
    },
    write(state: ThreadState): void {
      if (storage === null) return;
      const trimmed: ThreadState = {
        ...state,
        threads: state.threads.slice(0, MAX_PERSISTED_THREADS),
      };
      const payload: PersistedShape = { v: SESSION_VERSION, state: trimmed };
      try {
        storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(payload));
      } catch {
        /* QuotaExceeded — drop persistence silently; in-memory state is the
           source of truth, so the UI keeps working. */
      }
    },
  };
}

function readableSessionStorage(): Storage | null {
  try {
    return globalThis.sessionStorage;
  } catch {
    /* SSR or sandboxed iframe with storage disabled. */
    return null;
  }
}

export interface UseChatThreadsOptions {
  defaultModel: string;
  persistence?: SessionPersistence;
  /** Inject a clock for deterministic tests. */
  now?: () => number;
  /** Inject an id factory for deterministic tests. */
  newId?: () => string;
}

export interface UseChatThreadsResult {
  state: ThreadState;
  dispatch: Dispatch<ThreadAction>;
  active: ChatThread | null;
  newThread(): string;
  selectThread(id: string): void;
  deleteThread(id: string): void;
  setModel(threadId: string, model: string): void;
}

/**
 * React hook that mounts the reducer + sessionStorage mirror. The first render
 * either hydrates from sessionStorage or seeds an empty thread so the user
 * never sees a blank state.
 */
export function useChatThreads(opts: UseChatThreadsOptions): UseChatThreadsResult {
  const persistenceRef = useRef(opts.persistence ?? createSessionPersistence());
  const nowRef = useRef(opts.now ?? (() => Date.now()));
  const idRef = useRef(opts.newId ?? defaultId);

  const [state, dispatch] = useReducer(threadReducer, undefined, () => {
    const restored = persistenceRef.current.read();
    if (restored !== null && restored.threads.length > 0) {
      const activeId =
        restored.threads.some((t) => t.id === restored.activeId)
          ? restored.activeId
          : restored.threads[0]!.id;
      return { threads: restored.threads, activeId };
    }
    const created = nowRef.current();
    const seed: ChatThread = {
      id: idRef.current(),
      title: "New chat",
      createdAt: created,
      updatedAt: created,
      model: opts.defaultModel,
      messages: [],
    };
    return { threads: [seed], activeId: seed.id };
  });

  useEffect(() => {
    persistenceRef.current.write(state);
  }, [state]);

  const newThread = useCallback((): string => {
    const id = idRef.current();
    const createdAt = nowRef.current();
    const model = state.threads.find((t) => t.id === state.activeId)?.model ?? opts.defaultModel;
    dispatch({ type: "NEW_THREAD", id, model, createdAt });
    return id;
  }, [state.threads, state.activeId, opts.defaultModel]);

  const selectThread = useCallback((id: string): void => {
    dispatch({ type: "SELECT_THREAD", id });
  }, []);

  const deleteThread = useCallback(
    (id: string): void => {
      dispatch({
        type: "DELETE_THREAD",
        id,
        fallbackId: idRef.current(),
        fallbackModel: opts.defaultModel,
        fallbackCreatedAt: nowRef.current(),
      });
    },
    [opts.defaultModel],
  );

  const setModel = useCallback((threadId: string, model: string): void => {
    dispatch({ type: "SET_MODEL", id: threadId, model });
  }, []);

  const active = state.threads.find((t) => t.id === state.activeId) ?? null;

  return { state, dispatch, active, newThread, selectThread, deleteThread, setModel };
}

function defaultId(): string {
  // crypto.randomUUID is available in modern jsdom; fall back to a Math.random
  // based id for super-old environments so the hook still mounts in tests.
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `t_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
}
