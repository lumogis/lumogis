// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Document-scoped chat — conversation pinned to one library document (LUM-175).

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link, useParams } from "react-router-dom";

import type { ChatMessageDTO, DocumentCitationDTO } from "../../api/chat";
import { CHAT_ERROR_LITERALS } from "../../api/chat";
import { useAuth } from "../../auth/AuthProvider";
import type { ModelDescriptor } from "../../api/models";
import { consumeChatStream } from "../chat/ChatStream";
import { EmptyState } from "../_shared/EmptyState";
import { ContextUsedStrip } from "./ContextUsedStrip";

const DEFAULT_MODEL = "claude";

interface TurnMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "streaming" | "complete" | "error";
  citations?: DocumentCitationDTO[];
}

function generateId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function DocumentChatPage(): JSX.Element {
  const { documentId } = useParams<{ documentId: string }>();
  const { client } = useAuth();
  const [title, setTitle] = useState<string | null>(null);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [models, setModels] = useState<ModelDescriptor[]>([]);
  const [messages, setMessages] = useState<TurnMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const numericId = useMemo(() => {
    const n = Number(documentId);
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [documentId]);

  useEffect(() => {
    void (async () => {
      try {
        const res = await client.fetch("/api/v1/models");
        if (!res.ok) return;
        const body = (await res.json()) as { models: ModelDescriptor[] };
        const enabled = body.models.filter((m) => m.enabled);
        setModels(enabled);
        if (enabled.length > 0) setModel(enabled[0].id);
      } catch {
        /* catalog optional for v1 */
      }
    })();
  }, [client]);

  useEffect(() => {
    if (numericId === null) {
      setTitleError("Invalid document id");
      return;
    }
    void (async () => {
      try {
        const res = await client.fetch(`/api/v1/documents/${numericId}`);
        if (res.status === 404) {
          setTitle(`Document #${numericId}`);
          setTitleError("Document details unavailable");
          return;
        }
        if (!res.ok) {
          setTitle(`Document #${numericId}`);
          return;
        }
        const body = (await res.json()) as { title?: string; file_path?: string };
        setTitle(body.title ?? body.file_path ?? `Document #${numericId}`);
        setTitleError(null);
      } catch {
        setTitle(`Document #${numericId}`);
      }
    })();
  }, [client, numericId]);

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  useEffect(() => () => cancelStream(), [cancelStream]);

  const onSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>): Promise<void> => {
      e.preventDefault();
      if (numericId === null || streaming) return;
      const text = input.trim();
      if (text.length === 0) return;

      const userId = generateId("u");
      const assistantId = generateId("a");
      setMessages((prev) => [
        ...prev,
        { id: userId, role: "user", content: text, status: "complete" },
        { id: assistantId, role: "assistant", content: "", status: "streaming" },
      ]);
      setInput("");
      setSubmitError(null);
      setStreaming(true);

      const wireMessages: ChatMessageDTO[] = [
        ...messages
          .filter((m) => m.status !== "streaming" && m.status !== "error")
          .map((m) => ({ role: m.role, content: m.content })),
        { role: "user", content: text },
      ];

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await client.fetch("/api/v1/chat/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({
            model,
            messages: wireMessages,
            stream: true,
            document_id: numericId,
          }),
          signal: controller.signal,
        });

        if (!res.ok) {
          const detail = await safeReadDetail(res);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, status: "error", content: humaniseDocumentChatError(res.status, detail) }
                : m,
            ),
          );
          setStreaming(false);
          return;
        }

        let citations: DocumentCitationDTO[] = [];
        await consumeChatStream(res.body, {
          onContextCitations: (c) => {
            citations = c;
          },
          onDelta: (delta) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + delta } : m,
              ),
            );
          },
          onError: (msg) => setSubmitError(msg),
          onDone: () => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, status: "complete", citations: citations.length > 0 ? citations : undefined }
                  : m,
              ),
            );
            setStreaming(false);
          },
        });
      } catch (err) {
        if (!isAbort(err)) {
          setSubmitError(err instanceof Error ? err.message : "send_failed");
        }
        setStreaming(false);
      } finally {
        abortRef.current = null;
      }
    },
    [client, input, messages, model, numericId, streaming],
  );

  if (numericId === null) {
    return <EmptyState title="Invalid document" helperText="The document id in the URL is not valid." />;
  }

  return (
    <div className="document-chat-page flex flex-col gap-4 p-4 max-w-3xl mx-auto w-full">
      <header className="flex flex-col gap-1">
        <Link to={`/documents/${numericId}`} className="text-sm text-muted">
          ← Back to document
        </Link>
        <h1 className="text-xl font-semibold">{title ?? `Document #${numericId}`}</h1>
        {titleError !== null ? <p className="text-sm text-muted">{titleError}</p> : null}
        <p className="text-sm text-muted">Chat grounded in this document only.</p>
      </header>

      <div className="flex flex-col gap-3 min-h-[12rem]">
        {messages.length === 0 ? (
          <EmptyState title="Ask about this document" helperText="Your questions use only chunks from this file." />
        ) : (
          messages.map((m) => (
            <div key={m.id} className={m.role === "user" ? "text-right" : "text-left"}>
              <div
                className={
                  m.role === "user"
                    ? "inline-block rounded-lg bg-accent px-3 py-2 text-left"
                    : "rounded-lg border px-3 py-2"
                }
              >
                <p className="whitespace-pre-wrap">{m.content}</p>
                {m.role === "assistant" && m.citations !== undefined ? (
                  <div className="mt-2 border-t pt-2">
                    <ContextUsedStrip citations={m.citations} />
                  </div>
                ) : null}
              </div>
            </div>
          ))
        )}
      </div>

      {submitError !== null ? <p className="text-sm text-danger">{submitError}</p> : null}

      <form onSubmit={onSubmit} className="flex gap-2 items-end">
        {models.length > 1 ? (
          <label className="flex flex-col text-sm">
            Model
            <select
              value={model}
              onChange={(ev) => setModel(ev.target.value)}
              disabled={streaming}
              className="rounded border px-2 py-1"
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <textarea
          value={input}
          onChange={(ev) => setInput(ev.target.value)}
          disabled={streaming}
          rows={2}
          className="flex-1 rounded border px-3 py-2 resize-y"
          placeholder="Ask about this document…"
        />
        {streaming ? (
          <button type="button" onClick={cancelStream} className="btn btn-secondary">
            Stop
          </button>
        ) : (
          <button type="submit" className="btn btn-primary" disabled={input.trim().length === 0}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}

async function safeReadDetail(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function humaniseDocumentChatError(status: number, detail: unknown): string {
  if (detail !== null && typeof detail === "object") {
    const d = detail as { error?: string; detail?: { error?: string } };
    const code = d.error ?? d.detail?.error;
    if (code === CHAT_ERROR_LITERALS.DOCUMENT_NOT_FOUND) return "Document not found.";
    if (code === CHAT_ERROR_LITERALS.DOCUMENT_CONTEXT_UNAVAILABLE) {
      return "No searchable content for this document yet.";
    }
    if (code === CHAT_ERROR_LITERALS.INVALID_DOCUMENT_ID) return "Invalid document id.";
  }
  return `Request failed (${status})`;
}

function isAbort(err: unknown): boolean {
  return err !== null && typeof err === "object" && (err as { name?: string }).name === "AbortError";
}
