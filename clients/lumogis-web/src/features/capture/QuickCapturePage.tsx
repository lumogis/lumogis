// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// QuickCapture — Phase 5E connected flow + Phase 5F local outbox / manual sync.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  createCapture,
  formatCaptureErrorMessage,
  getCapture,
  indexCapture,
  transcribeCapture,
  uploadCaptureAttachment,
  type CaptureDetail,
} from "../../api/captures";
import { useAuth } from "../../auth/AuthProvider";
import {
  addAudioOutboxItem,
  addTextUrlOutboxItem,
  discardOutboxItem,
  getOutboxStats,
  listOutboxItems,
  MAX_LOCAL_VOICE_BYTES_TOTAL,
  MAX_PENDING_VOICE_CLIPS,
  outboxErrorToMessage,
  syncOutbox,
  type CaptureOutboxItemMeta,
} from "../../pwa/captureOutbox";
import { getDraft, makeCaptureDraftKey, setDraft } from "../../pwa/drafts";
import { useOnlineStatus } from "../../pwa/useOnlineStatus";

const DRAFT_KEY = makeCaptureDraftKey("__quickcapture__");

/** Align with capture API caps (`CapturePatchRequest` / plan §12.4). */
const MAX_CAPTURE_TITLE = 256;
const MAX_CAPTURE_TEXT = 32_000;
const MAX_CAPTURE_URL = 2048;

function clampCaptureField(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max);
}

/** Only http(s) — matches create/patch expectations; drops opaque or dangerous schemes. */
function sanitizeShareTargetUrl(raw: string | null): string {
  if (raw == null) return "";
  const t = raw.trim();
  if (!t) return "";
  const head = t.slice(0, MAX_CAPTURE_URL);
  const m = head.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):/);
  if (!m) return "";
  const scheme = m[1].toLowerCase();
  if (scheme !== "http" && scheme !== "https") return "";
  return head;
}

function parseTagsInput(raw: string): string[] | null {
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  if (parts.length === 0) return null;
  return parts;
}

function pickAudioMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (const c of candidates) {
    if (MediaRecorder.isTypeSupported(c)) return c;
  }
  return "";
}

function errorToMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return formatCaptureErrorMessage(err.status, err.detail);
  }
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

export function QuickCapturePage(): JSX.Element {
  const { client } = useAuth();
  const online = useOnlineStatus();
  const [searchParams, setSearchParams] = useSearchParams();

  const clientIdRef = useRef<string | null>(null);
  if (clientIdRef.current === null) {
    clientIdRef.current = globalThis.crypto?.randomUUID?.() ?? `cap-${Date.now()}`;
  }

  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");

  const [captureId, setCaptureId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaptureDetail | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const [recorderMime, setRecorderMime] = useState<string>("");
  const [recording, setRecording] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const [outboxItems, setOutboxItems] = useState<CaptureOutboxItemMeta[]>([]);
  const [outboxStats, setOutboxStats] = useState({
    pendingCount: 0,
    pendingAudioCount: 0,
    audioBytes: 0,
  });

  /** Skip draft hydrate when Web Share Target (or manual) pre-filled via `?title=&text=&url=`. */
  const sharePrefillConsumedRef = useRef(false);

  useLayoutEffect(() => {
    const t = searchParams.get("title");
    const x = searchParams.get("text");
    const u = searchParams.get("url");
    if (!t && !x && !u) return;

    sharePrefillConsumedRef.current = true;

    if (t) setTitle(clampCaptureField(t, MAX_CAPTURE_TITLE));
    if (x) setText(clampCaptureField(x, MAX_CAPTURE_TEXT));
    const urlOk = sanitizeShareTargetUrl(u);
    if (urlOk) setUrl(urlOk);

    setSearchParams(new URLSearchParams(), { replace: true });
    setError(null);
    setInfo("Prefilled from share or link — nothing is saved until you tap Save.");
  }, [searchParams, setSearchParams]);

  const refreshOutbox = useCallback(async () => {
    const [items, stats] = await Promise.all([listOutboxItems(), getOutboxStats()]);
    setOutboxItems(items);
    setOutboxStats(stats);
  }, []);

  useEffect(() => {
    setRecorderMime(pickAudioMimeType());
  }, []);

  useEffect(() => {
    void refreshOutbox();
  }, [refreshOutbox]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (sharePrefillConsumedRef.current) return;
      const raw = await getDraft(DRAFT_KEY);
      if (!raw || cancelled) return;
      try {
        const o = JSON.parse(raw) as {
          title?: string;
          text?: string;
          url?: string;
          tagsRaw?: string;
        };
        if (sharePrefillConsumedRef.current || cancelled) return;
        if (typeof o.title === "string") setTitle(o.title);
        if (typeof o.text === "string") setText(o.text);
        if (typeof o.url === "string") setUrl(o.url);
        if (typeof o.tagsRaw === "string") setTagsRaw(o.tagsRaw);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => {
      const payload = JSON.stringify({ title, text, url, tagsRaw });
      void setDraft(DRAFT_KEY, payload);
    }, 400);
    return () => window.clearTimeout(t);
  }, [title, text, url, tagsRaw]);

  const refreshDetail = useCallback(
    async (id: string) => {
      const d = await getCapture(client, id);
      setDetail(d);
    },
    [client],
  );

  const handleCreate = async () => {
    setError(null);
    setInfo(null);
    if (!online) {
      setError("You are offline. Use “Save locally to outbox” below — nothing uploads until you tap Sync now.");
      return;
    }
    const t = text.trim();
    const u = url.trim();
    if (!t && !u) {
      setError("Enter some text or a URL to create the capture (or use the note field).");
      return;
    }
    setBusy(true);
    try {
      const tags = parseTagsInput(tagsRaw);
      const created = await createCapture(client, {
        client_id: clientIdRef.current!,
        title: title.trim() || null,
        text: t || null,
        url: u || null,
        tags,
      });
      setCaptureId(created.capture_id);
      await refreshDetail(created.capture_id);
      setInfo("Capture saved.");
      await setDraft(DRAFT_KEY, "");
    } catch (e) {
      setError(errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSaveLocalOutbox = async () => {
    setError(null);
    setInfo(null);
    if (online) {
      setError('“Save locally” is for offline use. While online, use “Save capture” (server).');
      return;
    }
    const t = text.trim();
    const u = url.trim();
    if (!t && !u) {
      setError("Enter a note or URL to store on this device.");
      return;
    }
    setBusy(true);
    try {
      const tags = parseTagsInput(tagsRaw);
      await addTextUrlOutboxItem({
        kind: u && !t ? "url" : "text",
        title: title.trim() || undefined,
        text: t || undefined,
        url: u || undefined,
        tags,
      });
      await refreshOutbox();
      setInfo("Saved on this device only. Tap Sync now when you have a connection — we never upload automatically.");
    } catch (e) {
      setError(outboxErrorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSyncOutbox = async () => {
    setError(null);
    setInfo(null);
    if (!online) {
      setError("Connect to Lumogis, then tap Sync now. No background or silent upload.");
      return;
    }
    setBusy(true);
    try {
      const r = await syncOutbox(client);
      await refreshOutbox();
      if (r.synced > 0 && r.failed === 0) {
        setInfo(`Synced ${r.synced} local item(s) to the server. Transcribe from the capture list if needed.`);
      } else if (r.failed > 0) {
        setError(r.errors.filter(Boolean).join(" — ") || "Some items failed to sync.");
      } else {
        setInfo("Nothing to sync.");
      }
    } catch (e) {
      setError(errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDiscardOutbox = async (localCaptureId: string) => {
    setError(null);
    setBusy(true);
    try {
      await discardOutboxItem(localCaptureId);
      await refreshOutbox();
      setInfo("Local item discarded.");
    } catch (e) {
      setError(outboxErrorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleImagePick = async (files: FileList | null) => {
    setError(null);
    setInfo(null);
    if (!files?.length || !captureId) return;
    if (!online) {
      setError("Photos need a connection (offline photo staging is not in MVP).");
      return;
    }
    const file = files[0]!;
    setBusy(true);
    try {
      await uploadCaptureAttachment(client, captureId, file);
      await refreshDetail(captureId);
      setInfo("Image uploaded.");
    } catch (e) {
      setError(errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleAudioFilePick = async (files: FileList | null) => {
    setError(null);
    setInfo(null);
    if (!files?.length || !captureId || !online) return;
    const file = files[0]!;
    setBusy(true);
    try {
      await uploadCaptureAttachment(client, captureId, file, globalThis.crypto?.randomUUID?.());
      await refreshDetail(captureId);
      setInfo("Audio uploaded. You can transcribe below.");
    } catch (e) {
      setError(errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleOfflineAudioFile = async (files: FileList | null) => {
    setError(null);
    setInfo(null);
    if (!files?.length || online) return;
    const file = files[0]!;
    setBusy(true);
    try {
      await addAudioOutboxItem({
        blob: file,
        mimeType: file.type || "audio/webm",
        title: title.trim() || undefined,
        text: text.trim() || undefined,
        url: url.trim() || undefined,
        tags: parseTagsInput(tagsRaw),
      });
      await refreshOutbox();
      setInfo("Voice clip stored on this device. Sync when online — transcription is a separate step after upload.");
    } catch (e) {
      setError(outboxErrorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const startRecording = async (forLocal: boolean) => {
    setError(null);
    setInfo(null);
    if (!forLocal && (!captureId || !online)) return;
    if (!recorderMime) {
      setError("Recording is not supported in this browser. Use an audio file instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream, { mimeType: recorderMime });
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      mr.onerror = () => {
        setError("Recording failed.");
        setRecording(false);
      };
      mr.onstop = () => {
        stream.getTracks().forEach((tr) => tr.stop());
      };
      mr.start();
      setRecording(true);
    } catch {
      setError("Microphone permission denied or recording failed.");
    }
  };

  const stopRecording = async (forLocal: boolean) => {
    const mr = mediaRecorderRef.current;
    if (!mr || mr.state === "inactive") {
      setRecording(false);
      return;
    }
    setBusy(true);
    await new Promise<void>((resolve) => {
      mr.addEventListener("stop", () => resolve(), { once: true });
      mr.stop();
    });
    setRecording(false);
    mediaRecorderRef.current = null;
    try {
      const blob = new Blob(chunksRef.current, { type: recorderMime });
      chunksRef.current = [];
      if (blob.size === 0) {
        setBusy(false);
        return;
      }
      const ext = recorderMime.includes("webm") ? "webm" : "m4a";
      const file = new File([blob], `recording.${ext}`, { type: recorderMime });

      if (forLocal) {
        if (online) {
          setBusy(false);
          return;
        }
        await addAudioOutboxItem({
          blob: file,
          mimeType: recorderMime,
          title: title.trim() || undefined,
          text: text.trim() || undefined,
          url: url.trim() || undefined,
          tags: parseTagsInput(tagsRaw),
        });
        await refreshOutbox();
        setInfo("Recording saved on this device. Sync when online.");
      } else {
        if (!captureId) {
          setBusy(false);
          return;
        }
        await uploadCaptureAttachment(client, captureId, file, globalThis.crypto?.randomUUID?.());
        await refreshDetail(captureId);
        setInfo("Recording uploaded. You can transcribe below.");
      }
    } catch (e) {
      setError(forLocal ? outboxErrorToMessage(e) : errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleTranscribe = async (attachmentId?: string) => {
    setError(null);
    setInfo(null);
    if (!captureId) return;
    if (!online) {
      setError("Transcription requires a connection to the server.");
      return;
    }
    setBusy(true);
    try {
      await transcribeCapture(client, captureId, attachmentId ? { attachment_id: attachmentId } : {});
      await refreshDetail(captureId);
      setInfo("Transcription updated.");
    } catch (e) {
      setError(errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const handleAddToMemory = async () => {
    setError(null);
    setInfo(null);
    if (!captureId || !online) return;
    setBusy(true);
    try {
      await indexCapture(client, captureId);
      await refreshDetail(captureId);
      setInfo("Added to memory. Status is indexed — open Search to find this note.");
    } catch (e) {
      setError(errorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const transcripts = useMemo(() => {
    const xs = [...(detail?.transcripts ?? [])];
    xs.sort((a, b) => a.created_at.localeCompare(b.created_at));
    return xs;
  }, [detail?.transcripts]);

  const lastAudioAttachment = useMemo(() => {
    const list = detail?.attachments ?? [];
    const audios = list.filter((a) => a.attachment_type === "audio");
    return audios.length ? audios[audios.length - 1] : null;
  }, [detail?.attachments]);

  const detailHasAudio = useMemo(
    () => !!(detail?.attachments ?? []).some((a) => a.attachment_type === "audio"),
    [detail?.attachments],
  );
  const detailHasCompletedTranscript = useMemo(
    () =>
      (detail?.transcripts ?? []).some(
        (t) => t.transcript_status === "complete" && (t.transcript_text?.trim()?.length ?? 0) > 0,
      ),
    [detail?.transcripts],
  );
  const detailHasIndexableSurface = useMemo(
    () =>
      !!(
        (detail?.title?.trim()?.length ?? 0) > 0 ||
        (detail?.text?.trim()?.length ?? 0) > 0 ||
        (detail?.url?.trim()?.length ?? 0) > 0
      ),
    [detail?.title, detail?.text, detail?.url],
  );

  const canAddToMemory = useMemo(() => {
    if (!online || !captureId || !detail || detail.status === "indexed") return false;
    if (detailHasAudio) {
      return detailHasCompletedTranscript;
    }
    return detailHasIndexableSurface;
  }, [
    online,
    captureId,
    detail,
    detailHasAudio,
    detailHasCompletedTranscript,
    detailHasIndexableSurface,
  ]);

  const audioMiB = (outboxStats.audioBytes / (1024 * 1024)).toFixed(2);

  return (
    <div
      data-testid="quick-capture-page"
      style={{ maxWidth: "40rem", margin: "0 auto", padding: "1rem", fontSize: "1.05rem" }}
    >
      <h1 style={{ fontSize: "1.5rem" }}>Quick capture</h1>
      <p style={{ opacity: 0.85, lineHeight: 1.4 }}>
        Pending items can live <strong>only on this device</strong> until you tap <strong>Sync now</strong>. Local voice clips
        use storage ({MAX_PENDING_VOICE_CLIPS} clips max, {MAX_LOCAL_VOICE_BYTES_TOTAL / 1024 / 1024} MiB total).{" "}
        <strong>Nothing uploads automatically</strong> when you go online — no Background Sync, no service worker caching.{" "}
        <strong>Add to memory</strong> runs only when you tap it — nothing indexes on save, upload, sync, or transcription. Off-device STT is not used in this flow.
      </p>

      {!online && (
        <p role="status" data-testid="quick-capture-offline" style={{ padding: "0.75rem", background: "var(--lumogis-surface-2, #2a2a32)", borderRadius: 8 }}>
          You seem offline or unreachable. Use the local outbox below; photo upload stays disabled until you are online.
        </p>
      )}

      <section data-testid="capture-outbox-panel" style={{ marginTop: "1rem", padding: "0.75rem", border: "1px solid rgba(128,128,128,0.35)", borderRadius: 8 }}>
        <h2 style={{ fontSize: "1.15rem", marginTop: 0 }}>Local outbox</h2>
        <p data-testid="capture-outbox-stats" style={{ margin: "0.25rem 0" }}>
          Pending: <strong>{outboxStats.pendingCount}</strong> · Voice clips: <strong>{outboxStats.pendingAudioCount}</strong> ·
          Local voice ~ <strong>{audioMiB}</strong> MiB / {MAX_LOCAL_VOICE_BYTES_TOTAL / 1024 / 1024} MiB
        </p>
        <button
          type="button"
          data-testid="quick-capture-sync-outbox"
          disabled={busy || !online || outboxStats.pendingCount === 0}
          onClick={() => void handleSyncOutbox()}
          style={{ padding: "0.65rem 1rem", fontSize: "1rem", marginRight: "0.5rem" }}
        >
          Sync now
        </button>
        <span style={{ fontSize: "0.9rem", opacity: 0.85 }}>Manual sync only — safe when you choose.</span>
        {outboxItems.length > 0 && (
          <ul data-testid="capture-outbox-list" style={{ paddingLeft: "1.2rem", marginTop: "0.75rem" }}>
            {outboxItems.map((item) => (
              <li key={item.local_capture_id} style={{ marginBottom: "0.5rem" }}>
                <strong>{item.kind}</strong> · {new Date(item.created_at).toLocaleString()} · {item.status}
                {item.kind === "audio" ? ` · ${(item.size_bytes / 1024).toFixed(0)} KiB` : ""}
                {item.last_error ? ` — ${item.last_error}` : ""}
                <button
                  type="button"
                  style={{ marginLeft: "0.5rem", fontSize: "0.9rem" }}
                  disabled={busy}
                  onClick={() => void handleDiscardOutbox(item.local_capture_id)}
                >
                  Discard
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {error && (
        <p role="alert" data-testid="quick-capture-error" style={{ color: "var(--lumogis-danger, #f66)", marginTop: "0.75rem" }}>
          {error}
        </p>
      )}
      {info && (
        <p role="status" data-testid="quick-capture-info" style={{ marginTop: "0.5rem" }}>
          {info}
        </p>
      )}

      <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginTop: "1rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          Title (optional)
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={busy}
            style={{ fontSize: "1rem", padding: "0.65rem", minHeight: "2.75rem" }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          Note
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={busy}
            rows={5}
            placeholder="Short note (required unless you add a URL below)"
            style={{ fontSize: "1rem", padding: "0.65rem", minHeight: "8rem" }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          URL (optional)
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={busy}
            placeholder="https://…"
            style={{ fontSize: "1rem", padding: "0.65rem", minHeight: "2.75rem" }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          Tags (optional, comma-separated)
          <input
            type="text"
            value={tagsRaw}
            onChange={(e) => setTagsRaw(e.target.value)}
            disabled={busy}
            style={{ fontSize: "1rem", padding: "0.65rem", minHeight: "2.75rem" }}
          />
        </label>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          <button
            type="button"
            data-testid="quick-capture-save-server"
            onClick={() => void handleCreate()}
            disabled={busy || !online}
            style={{ padding: "0.85rem", fontSize: "1.05rem", fontWeight: 600 }}
          >
            Save capture (server)
          </button>
          <button
            type="button"
            data-testid="quick-capture-save-local"
            onClick={() => void handleSaveLocalOutbox()}
            disabled={busy || online}
            style={{ padding: "0.85rem", fontSize: "1.05rem", fontWeight: 600 }}
          >
            Save locally to outbox
          </button>
        </div>

        <hr style={{ margin: "1rem 0", opacity: 0.3 }} />

        <h2 style={{ fontSize: "1.2rem" }}>Attachments (online)</h2>
        {!captureId ? (
          <p data-testid="quick-capture-attachments-locked">Save the capture to the server first to attach files here.</p>
        ) : (
          <>
            <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
              Image (JPEG, PNG, WebP) {(!online || !captureId) && <span style={{ fontSize: "0.85rem" }}>— requires connection</span>}
              <input
                type="file"
                data-testid="quick-capture-image-input"
                accept="image/jpeg,image/png,image/webp"
                disabled={busy || !online}
                onChange={(e) => void handleImagePick(e.target.files)}
                style={{ fontSize: "1rem" }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
              Audio file → server
              <input
                type="file"
                accept="audio/*,audio/webm,video/webm"
                disabled={busy || !online || !captureId}
                onChange={(e) => void handleAudioFilePick(e.target.files)}
                style={{ fontSize: "1rem" }}
              />
            </label>
          </>
        )}
        {!online && (
          <label style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            Audio file → this device only
            <input
              type="file"
              data-testid="quick-capture-offline-audio-file"
              accept="audio/*,audio/webm,video/webm"
              disabled={busy}
              onChange={(e) => void handleOfflineAudioFile(e.target.files)}
              style={{ fontSize: "1rem" }}
            />
          </label>
        )}

        <div data-testid="quick-capture-recorder-ui" style={{ marginTop: "0.5rem" }}>
          {typeof MediaRecorder === "undefined" || !recorderMime ? (
            <p data-testid="quick-capture-recorder-disabled">
              Recording is not supported in this browser. Use an audio file instead.
            </p>
          ) : online && !captureId ? (
            <p data-testid="quick-capture-recorder-needs-save">Save the capture to the server first to record into that capture.</p>
          ) : !online ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center" }}>
              {!recording ? (
                <button
                  type="button"
                  data-testid="quick-capture-record-local-start"
                  onClick={() => void startRecording(true)}
                  disabled={busy}
                  style={{ padding: "0.75rem 1rem", fontSize: "1rem" }}
                >
                  Start recording (local)
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="quick-capture-record-local-stop"
                  onClick={() => void stopRecording(true)}
                  disabled={busy}
                  style={{ padding: "0.75rem 1rem", fontSize: "1rem" }}
                >
                  Stop &amp; save locally
                </button>
              )}
              <span style={{ opacity: 0.8, fontSize: "0.9rem" }}>
                {recording ? "Recording…" : "Stored only on this device until Sync now."}
              </span>
            </div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center" }}>
              {!recording ? (
                <button
                  type="button"
                  onClick={() => void startRecording(false)}
                  disabled={busy || !online}
                  style={{ padding: "0.75rem 1rem", fontSize: "1rem" }}
                >
                  Start recording
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void stopRecording(false)}
                  disabled={busy}
                  style={{ padding: "0.75rem 1rem", fontSize: "1rem" }}
                >
                  Stop &amp; upload
                </button>
              )}
              <span style={{ opacity: 0.8, fontSize: "0.9rem" }}>
                {recording ? "Recording…" : "Gesture-gated; uploads to the server."}
              </span>
            </div>
          )}
        </div>

        <h2 style={{ fontSize: "1.2rem" }}>Transcription</h2>
        {!captureId ? (
          <p>Save a server capture and upload audio first, or sync local voice then open the capture.</p>
        ) : (
          <>
            <button
              type="button"
              onClick={() => void handleTranscribe(lastAudioAttachment?.id)}
              disabled={busy || !online || !lastAudioAttachment}
              data-testid="quick-capture-transcribe-btn"
              style={{ padding: "0.85rem", fontSize: "1.05rem" }}
            >
              Transcribe audio
            </button>
            {!lastAudioAttachment && <p>No audio attachment yet.</p>}
          </>
        )}

        <h2 style={{ fontSize: "1.2rem" }}>Transcripts</h2>
        {transcripts.length === 0 ? (
          <p data-testid="quick-capture-no-transcript">No transcript yet</p>
        ) : (
          <ul data-testid="quick-capture-transcript-list" style={{ paddingLeft: "1.2rem" }}>
            {transcripts.map((tr) => (
              <li key={tr.id} style={{ marginBottom: "0.75rem" }}>
                <strong>{tr.transcript_status}</strong>
                {tr.transcript_text ? ` — ${tr.transcript_text}` : null}
              </li>
            ))}
          </ul>
        )}

        <h2 style={{ fontSize: "1.2rem" }}>Capture detail</h2>
        {!detail ? (
          <p data-testid="quick-capture-detail-empty">No server capture loaded.</p>
        ) : (
          <div data-testid="quick-capture-detail">
            <p>
              <strong>Status:</strong> {detail.status} · <strong>Type:</strong> {detail.capture_type}
              {detail.note_id ? (
                <>
                  {" "}
                  · <strong>Note:</strong> {detail.note_id}
                </>
              ) : null}
            </p>
            <p>
              <strong>Attachments:</strong> {detail.attachments.length}
            </p>
            <ul data-testid="quick-capture-attachment-list" style={{ paddingLeft: "1.2rem" }}>
              {detail.attachments.map((a) => (
                <li key={a.id}>
                  {a.attachment_type} · {a.mime_type} · {(a.size_bytes / 1024).toFixed(1)} KiB
                </li>
              ))}
            </ul>
          </div>
        )}

        <button
          type="button"
          disabled={busy || !canAddToMemory}
          data-testid="quick-capture-add-memory"
          title={
            !online
              ? "Requires a connection"
              : !detail || detail.status === "indexed"
                ? detail?.status === "indexed"
                  ? "Already in memory"
                  : "Save a capture first"
                : !canAddToMemory
                  ? "Add text, a link, or finish transcription for audio"
                  : "Add this capture to searchable memory"
          }
          onClick={() => void handleAddToMemory()}
          style={{ padding: "0.85rem", fontSize: "1.05rem" }}
        >
          Add to memory
        </button>
      </section>
    </div>
  );
}
