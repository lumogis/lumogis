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

import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

import type { ChatMessageDTO, DocumentCitationDTO } from "../../api/chat";
import { CHAT_ERROR_LITERALS } from "../../api/chat";
import { useAuth } from "../../auth/AuthProvider";
import { ChatComposer } from "../../components/ChatComposer";
import { transcribeVoice } from "../../api/voice";
import { useShellState } from "../../api/meShell";
import { MetadataCaption } from "../../components/MetadataCaption";
import {
  documentMetadataCaption,
  humanizeStoredName,
} from "../../util/humanizeStoredName";
import { consumeChatStream } from "../chat/ChatStream";
import { EmptyState } from "../_shared/EmptyState";
import { PrivacyBlockedHint } from "../_shared/PrivacyBlockedHint";
import { catalogHasPrivacyBlock } from "../_shared/privacyBlockCopy";
import { useModelCatalog } from "../chat/ChatPage";
import { AnswerText } from "../chat/AnswerText";
import { ModelChip } from "../chat/ModelChip";
import { ContextUsedStrip } from "./ContextUsedStrip";
import { randomUuid } from "../../util/uuid";

const DEFAULT_MODEL = "claude";

interface TurnMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "streaming" | "complete" | "error";
  citations?: DocumentCitationDTO[];
}

function generateId(prefix: string): string {
  return `${prefix}-${randomUuid()}`;
}

export function DocumentChatPage(): JSX.Element {
  const { documentId } = useParams<{ documentId: string }>();
  const { client } = useAuth();
  const shell = useShellState();
  const [storedName, setStoredName] = useState<string | null>(null);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [model, setModel] = useState(DEFAULT_MODEL);
  // `modelError` is ignored on purpose: the catalog stays optional for v1.
  const { models: catalog } = useModelCatalog(client);
  const models = useMemo(() => catalog.filter((m) => m.enabled), [catalog]);
  const [messages, setMessages] = useState<TurnMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  // Transient stream status (LUM-876); shown in the empty assistant bubble only.
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
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

  // Same rule as before: the first enabled model replaces DEFAULT_MODEL.
  useEffect(() => {
    if (models.length > 0) setModel(models[0].id);
  }, [models]);

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
    setStreamStatus(null);

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
        onStatus: (status) => setStreamStatus(status.message),
        onDelta: (delta) => {
          setStreamStatus(null);
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
      setStreamStatus(null);
    }
  }, [client, input, messages, model, numericId, streaming]);

  if (numericId === null) {
    return <EmptyState title="Invalid document" helperText="The document id in the URL is not valid." />;
  }

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-var(--lumogis-shell-header-h)-var(--lumogis-shell-bottomnav-h)-2*var(--lumogis-space-4))] w-full max-w-4xl min-w-0 flex-col gap-3 @min-[720px]/layout:min-h-[calc(100dvh-var(--lumogis-shell-header-h)-2*var(--lumogis-space-4))]" data-testid="document-chat-page">
      <header className="shrink-0">
        <Link to={`/documents/${numericId}`} className="text-sm text-accent-text underline underline-offset-2">
          ← Back to document
        </Link>
        <h1 className="mt-2 mb-0 text-xl font-bold">{displayTitle}</h1>
        <MetadataCaption
          value={documentMetadataCaption(numericId, storedName, filePath)}
          label="Copy id"
        />
        {titleError !== null ? <p className="m-0 text-sm text-destructive">{titleError}</p> : null}
        {catalogHasPrivacyBlock(catalog) ? <PrivacyBlockedHint surface="picker" /> : null}
      </header>

      <div className="grid min-h-0 flex-1 grid-rows-[1fr_auto] gap-3 rounded-xl border bg-card p-4">
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-1" role="log" aria-live="polite">
          {messages.length === 0 ? (
            <p className="m-auto max-w-[42ch] text-center text-sm text-muted-foreground">
              Ask about this document — your questions use only chunks from this file.
            </p>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={cn(
                  "max-w-[min(80ch,90%)] rounded-lg border p-3 break-words whitespace-pre-wrap",
                  m.role === "user" ? "self-end bg-muted" : "self-start",
                )}
              >
                {m.role === "assistant" &&
                m.status === "streaming" &&
                m.content.length === 0 &&
                streamStatus !== null ? (
                  <span
                    className="text-muted-foreground italic"
                    role="status"
                    data-testid="document-chat-stream-status"
                  >
                    {streamStatus}
                  </span>
                ) : m.role === "assistant" ? (
                  // Formatted like the chat page's answers (LUM-993 follow-up).
                  <AnswerText text={m.content} />
                ) : (
                  m.content
                )}
                {m.role === "assistant" && m.citations !== undefined ? (
                  <div className="mt-2 border-t pt-2">
                    <ContextUsedStrip citations={m.citations} />
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>

        {submitError !== null ? (
          <Alert variant="destructive">
            <AlertDescription>{submitError}</AlertDescription>
          </Alert>
        ) : null}

        <ChatComposer
          id="lumogis-document-chat-input"
          // LUM-993: the model choice sits inside the composer, as on the chat page.
          modelSlot={
            models.length > 1 ? (
              <ModelChip models={models} value={model} onChange={setModel} disabled={streaming} error={null} onRetry={() => undefined} />
            ) : undefined
          }
          value={input}
          onChange={setInput}
          onSubmit={() => void sendMessage()}
          streaming={streaming}
          onStop={cancelStream}
          placeholder="Ask about this document…"
          showLabel={false}
          minRows={2}
          maxRows={8}
          textareaRef={composerRef}
          // LUM-993 Decision 16: the microphone appears only while a speech engine runs.
          voiceTranscribe={
            shell?.voice_available === true ? (blob) => transcribeVoice(client, blob).then((r) => r.text) : undefined
          }
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
