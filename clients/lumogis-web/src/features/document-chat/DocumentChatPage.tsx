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
} from "react";
import { Link, useParams } from "react-router-dom";

import type { ChatMessageDTO, DocumentCitationDTO } from "../../api/chat";
import { CHAT_ERROR_LITERALS } from "../../api/chat";
import { useAuth } from "../../auth/AuthProvider";
import type { ModelDescriptor } from "../../api/models";
import { ChatComposer } from "../../components/ChatComposer";
import { MetadataCaption } from "../../components/MetadataCaption";
import {
  documentMetadataCaption,
  humanizeStoredName,
} from "../../util/humanizeStoredName";
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
  const [storedName, setStoredName] = useState<string | null>(null);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [models, setModels] = useState<ModelDescriptor[]>([]);
  const [messages, setMessages] = useState<TurnMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  const numericId = useMemo(() => {
    const n = Number(documentId);
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [documentId]);

  const displayTitle = useMemo(
    () => humanizeStoredName(storedName, filePath),
    [storedName, filePath],
  );

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
          setStoredName(null);
          setFilePath(null);
          setTitleError("Document details unavailable");
          return;
        }
        if (!res.ok) {
          setTitleError(null);
          return;
        }
        const body = (await res.json()) as { title?: string; file_path?: string; display_name?: string };
        setStoredName(body.display_name ?? body.title ?? body.file_path ?? null);
        setFilePath(body.file_path ?? null);
        setTitleError(null);
      } catch {
        setTitleError(null);
      }
    })();
  }, [client, numericId]);

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  useEffect(() => () => cancelStream(), [cancelStream]);

  const sendMessage = useCallback(async (): Promise<void> => {
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
  }, [client, input, messages, model, numericId, streaming]);

  if (numericId === null) {
    return <EmptyState title="Invalid document" helperText="The document id in the URL is not valid." />;
  }

  return (
    <div className="lumogis-document-chat" data-testid="document-chat-page">
      <header className="lumogis-document-chat__head">
        <Link to={`/documents/${numericId}`} className="lumogis-document-chat__back">
          ← Back to document
        </Link>
        <h1 className="lumogis-document-chat__title">{displayTitle}</h1>
        <MetadataCaption
          value={documentMetadataCaption(numericId, storedName, filePath)}
          label="Copy id"
        />
        {titleError !== null ? <p className="lumogis-document-chat__error">{titleError}</p> : null}
        {models.length > 1 ? (
          <label className="lumogis-chat__model-picker" style={{ marginTop: "0.5rem" }}>
            Model
            <select
              value={model}
              onChange={(ev) => setModel(ev.target.value)}
              disabled={streaming}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </header>

      <div className="lumogis-document-chat__surface">
        <div className="lumogis-document-chat__messages" role="log" aria-live="polite">
          {messages.length === 0 ? (
            <p className="lumogis-document-chat__empty">
              Ask about this document — your questions use only chunks from this file.
            </p>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={[
                  "lumogis-document-chat__bubble",
                  m.role === "user" ? "lumogis-document-chat__bubble--user" : "lumogis-document-chat__bubble--assistant",
                ].join(" ")}
              >
                {m.content}
                {m.role === "assistant" && m.citations !== undefined ? (
                  <div style={{ marginTop: "0.5rem", borderTop: "1px solid var(--lumogis-border)", paddingTop: "0.5rem" }}>
                    <ContextUsedStrip citations={m.citations} />
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>

        {submitError !== null ? (
          <p role="alert" className="lumogis-document-chat__error">
            {submitError}
          </p>
        ) : null}

        <ChatComposer
          id="lumogis-document-chat-input"
          className="lumogis-document-chat__compose"
          value={input}
          onChange={setInput}
          onSubmit={() => void sendMessage()}
          streaming={streaming}
          onStop={cancelStream}
          placeholder="Ask about this document…"
          showLabel={false}
          minRows={3}
          maxRows={8}
          textareaRef={composerRef}
        />
      </div>
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
