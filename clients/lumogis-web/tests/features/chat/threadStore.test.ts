// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — chat thread reducer + sessionStorage mirror.
// Parent plan §"Pass 1.2 item 8" + §Implementation sequence step 8
// (ephemeral per-tab persistence, sessionStorage round-trip).

import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import {
  createSessionPersistence,
  deriveTitle,
  initialThreadState,
  threadReducer,
  useChatThreads,
  type ChatThread,
  type ThreadAction,
  type ThreadState,
} from "../../../src/features/chat/threadStore";

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    get length() {
      return map.size;
    },
  };
}

function step(state: ThreadState, action: ThreadAction): ThreadState {
  return threadReducer(state, action);
}

const T0 = 1_700_000_000_000;

describe("deriveTitle", () => {
  it("returns 'New chat' for an empty or whitespace-only first message", () => {
    expect(deriveTitle("")).toBe("New chat");
    expect(deriveTitle("   \n\t  ")).toBe("New chat");
  });

  it("collapses whitespace and uses the first 79 chars + ellipsis when long", () => {
    const long = "a".repeat(120);
    const title = deriveTitle(long);
    expect(title.endsWith("…")).toBe(true);
    expect(title.length).toBe(80);
  });

  it("returns the trimmed message verbatim when short enough", () => {
    expect(deriveTitle("  hi there\n\nfriend  ")).toBe("hi there friend");
  });
});

describe("threadReducer", () => {
  it("creates a new thread with placeholder title", () => {
    const next = step(initialThreadState, {
      type: "NEW_THREAD",
      id: "t1",
      model: "claude",
      createdAt: T0,
    });
    expect(next.threads).toHaveLength(1);
    expect(next.threads[0]?.title).toBe("New chat");
    expect(next.threads[0]?.model).toBe("claude");
    expect(next.activeId).toBe("t1");
  });

  it("derives the thread title from the first user message", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, {
      type: "APPEND_USER",
      threadId: "t1",
      messageId: "u1",
      content: "How does memory scope work?",
      createdAt: T0 + 1,
    });
    expect(s.threads[0]?.title).toBe("How does memory scope work?");
    expect(s.threads[0]?.messages).toHaveLength(1);
  });

  it("does NOT overwrite the title on subsequent user messages", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, { type: "APPEND_USER", threadId: "t1", messageId: "u1", content: "first", createdAt: T0 });
    s = step(s, { type: "APPEND_USER", threadId: "t1", messageId: "u2", content: "second", createdAt: T0 + 1 });
    expect(s.threads[0]?.title).toBe("first");
  });

  it("appends assistant deltas in order and ignores deltas for unknown messages", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, { type: "APPEND_USER", threadId: "t1", messageId: "u1", content: "q", createdAt: T0 });
    s = step(s, { type: "BEGIN_ASSISTANT", threadId: "t1", messageId: "a1", createdAt: T0 + 1 });
    s = step(s, { type: "APPEND_ASSISTANT_DELTA", threadId: "t1", messageId: "a1", delta: "Hel" });
    s = step(s, { type: "APPEND_ASSISTANT_DELTA", threadId: "t1", messageId: "a1", delta: "lo" });
    s = step(s, { type: "APPEND_ASSISTANT_DELTA", threadId: "t1", messageId: "ghost", delta: "X" });
    s = step(s, { type: "FINISH_ASSISTANT", threadId: "t1", messageId: "a1" });

    const assistant = s.threads[0]?.messages.find((m) => m.id === "a1");
    expect(assistant?.content).toBe("Hello");
    expect(assistant?.status).toBe("complete");
  });

  it("marks the assistant message as 'aborted' when the user stops the stream", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, { type: "BEGIN_ASSISTANT", threadId: "t1", messageId: "a1", createdAt: T0 });
    s = step(s, { type: "APPEND_ASSISTANT_DELTA", threadId: "t1", messageId: "a1", delta: "partial" });
    s = step(s, { type: "ABORT_ASSISTANT", threadId: "t1", messageId: "a1" });
    expect(s.threads[0]?.messages[0]?.status).toBe("aborted");
    expect(s.threads[0]?.messages[0]?.content).toBe("partial");
  });

  it("records errorDetail on FAIL_ASSISTANT", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, { type: "BEGIN_ASSISTANT", threadId: "t1", messageId: "a1", createdAt: T0 });
    s = step(s, {
      type: "FAIL_ASSISTANT",
      threadId: "t1",
      messageId: "a1",
      detail: "llm_provider_unavailable",
    });
    expect(s.threads[0]?.messages[0]?.status).toBe("error");
    expect(s.threads[0]?.messages[0]?.errorDetail).toBe("llm_provider_unavailable");
  });

  it("DELETE_THREAD removes the row and seeds a fallback when the last thread is deleted", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, {
      type: "DELETE_THREAD",
      id: "t1",
      fallbackId: "t2",
      fallbackModel: "claude-sonnet",
      fallbackCreatedAt: T0 + 5,
    });
    expect(s.threads).toHaveLength(1);
    expect(s.threads[0]?.id).toBe("t2");
    expect(s.threads[0]?.model).toBe("claude-sonnet");
    expect(s.activeId).toBe("t2");
  });

  it("DELETE_THREAD on the active row promotes the next thread to active", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m", createdAt: T0 });
    s = step(s, { type: "NEW_THREAD", id: "t2", model: "m", createdAt: T0 + 1 });
    expect(s.activeId).toBe("t2");
    s = step(s, {
      type: "DELETE_THREAD",
      id: "t2",
      fallbackId: "fallback",
      fallbackModel: "m",
      fallbackCreatedAt: T0 + 2,
    });
    expect(s.threads.map((t) => t.id)).toEqual(["t1"]);
    expect(s.activeId).toBe("t1");
  });

  it("SET_MODEL updates only the targeted thread's model", () => {
    let s = step(initialThreadState, { type: "NEW_THREAD", id: "t1", model: "m1", createdAt: T0 });
    s = step(s, { type: "NEW_THREAD", id: "t2", model: "m1", createdAt: T0 + 1 });
    s = step(s, { type: "SET_MODEL", id: "t1", model: "m2" });
    expect(s.threads.find((t) => t.id === "t1")?.model).toBe("m2");
    expect(s.threads.find((t) => t.id === "t2")?.model).toBe("m1");
  });
});

describe("createSessionPersistence", () => {
  it("round-trips state through the storage backend", () => {
    const storage = memoryStorage();
    const persistence = createSessionPersistence(storage);
    const state: ThreadState = {
      threads: [
        {
          id: "t1",
          title: "T1",
          createdAt: T0,
          updatedAt: T0,
          model: "claude",
          messages: [
            { id: "u1", role: "user", content: "hi", createdAt: T0 },
            {
              id: "a1",
              role: "assistant",
              content: "hello",
              createdAt: T0 + 1,
              status: "complete",
            },
          ],
        } satisfies ChatThread,
      ],
      activeId: "t1",
    };
    persistence.write(state);
    expect(persistence.read()).toEqual(state);
  });

  it("returns null when the persisted shape is unrecognised", () => {
    const storage = memoryStorage();
    storage.setItem("lumogis-chat-threads", JSON.stringify({ v: 999, state: {} }));
    expect(createSessionPersistence(storage).read()).toBeNull();
  });

  it("returns null when JSON is invalid", () => {
    const storage = memoryStorage();
    storage.setItem("lumogis-chat-threads", "not-json");
    expect(createSessionPersistence(storage).read()).toBeNull();
  });

  it("returns null when there is no entry", () => {
    expect(createSessionPersistence(memoryStorage()).read()).toBeNull();
  });

  it("returns null when storage is null (sandboxed iframes / SSR)", () => {
    expect(createSessionPersistence(null).read()).toBeNull();
  });

  it("swallows quota-exceeded write failures without throwing", () => {
    const storage: Storage = {
      ...memoryStorage(),
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    } as Storage;
    expect(() =>
      createSessionPersistence(storage).write({ ...initialThreadState }),
    ).not.toThrow();
  });
});

describe("useChatThreads", () => {
  it("seeds an empty thread on first mount when storage is empty", () => {
    const persistence = createSessionPersistence(memoryStorage());
    let counter = 0;
    const { result } = renderHook(() =>
      useChatThreads({
        defaultModel: "claude",
        persistence,
        now: () => T0,
        newId: () => `id-${counter++}`,
      }),
    );

    expect(result.current.state.threads).toHaveLength(1);
    expect(result.current.state.threads[0]?.title).toBe("New chat");
    expect(result.current.state.threads[0]?.model).toBe("claude");
    expect(result.current.active?.id).toBe(result.current.state.activeId);
  });

  it("hydrates from the persistence backend when one exists", () => {
    const storage = memoryStorage();
    const persistence = createSessionPersistence(storage);
    const seeded: ThreadState = {
      threads: [
        {
          id: "saved",
          title: "Saved",
          createdAt: T0,
          updatedAt: T0,
          model: "claude",
          messages: [],
        },
      ],
      activeId: "saved",
    };
    persistence.write(seeded);

    const { result } = renderHook(() =>
      useChatThreads({ defaultModel: "claude", persistence, now: () => T0, newId: () => "new" }),
    );

    expect(result.current.state.threads[0]?.id).toBe("saved");
    expect(result.current.active?.id).toBe("saved");
  });

  it("newThread creates a thread, persists, and switches active to it", () => {
    const writes = vi.fn();
    const persistence = {
      read: () => null,
      write: writes,
    };
    let i = 0;
    const { result } = renderHook(() =>
      useChatThreads({
        defaultModel: "m",
        persistence,
        now: () => T0,
        newId: () => `id-${i++}`,
      }),
    );

    act(() => {
      const created = result.current.newThread();
      expect(created).toBe("id-1");
    });

    expect(result.current.state.threads).toHaveLength(2);
    expect(result.current.active?.id).toBe("id-1");
    expect(writes).toHaveBeenCalled();
  });

  it("deleteThread on the only thread seeds a fresh fallback (UI never sees an empty list)", () => {
    let i = 0;
    const { result } = renderHook(() =>
      useChatThreads({
        defaultModel: "m",
        persistence: { read: () => null, write: () => {} },
        now: () => T0,
        newId: () => `id-${i++}`,
      }),
    );
    const seedId = result.current.state.threads[0]!.id;
    act(() => result.current.deleteThread(seedId));
    expect(result.current.state.threads).toHaveLength(1);
    expect(result.current.state.threads[0]?.id).not.toBe(seedId);
  });

  it("setModel updates the active thread's model", () => {
    let i = 0;
    const { result } = renderHook(() =>
      useChatThreads({
        defaultModel: "m1",
        persistence: { read: () => null, write: () => {} },
        now: () => T0,
        newId: () => `id-${i++}`,
      }),
    );
    const id = result.current.state.threads[0]!.id;
    act(() => result.current.setModel(id, "m2"));
    expect(result.current.state.threads[0]?.model).toBe("m2");
  });
});
