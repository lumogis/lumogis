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
import { ChevronDown } from "lucide-react";

import { EmptyState } from "../../components/page/EmptyState";
import { PageHeader } from "../../components/page/PageHeader";
import { PageSection } from "../../components/page/PageSection";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Textarea } from "../../components/ui/textarea";
import { CaptureInboxView } from "./CaptureInboxView";
import { CaptureArchiveView } from "./CaptureArchiveView";
import {
  addAudioOutboxItem,
  addTextUrlOutboxItem,
  claimLegacyOutboxItem,
  discardOutboxItem,
  getOutboxStats,
  listLegacyOutboxItems,
  listOutboxItems,
  MAX_LOCAL_VOICE_BYTES_TOTAL,
  MAX_PENDING_VOICE_CLIPS,
  outboxErrorToMessage,
  syncOutbox,
  type CaptureOutboxItemMeta,
} from "../../pwa/captureOutbox";
import { getDraft, makeCaptureDraftKey, setDraft } from "../../pwa/drafts";
import { useOnlineStatus } from "../../pwa/useOnlineStatus";
import { pickAudioMimeType } from "../../hooks/useAudioRecorder";
import { formatDateTime } from "../../util/formatTimestamp";
import { randomUuid } from "../../util/uuid";

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

// LUM-653: the codec-fallback helper is now shared with useAudioRecorder (the
// single source of truth for MediaRecorder mime selection). QuickCapture keeps
// its own capture-attachment recording flow (C2 — see the plan); only this
// helper is de-duplicated, so behaviour is unchanged.

/** Short, recognisable label for a legacy (ownerless) row: title, text start, URL host, else "Voice clip". */
function legacyPreview(item: CaptureOutboxItemMeta): string {
  if (item.title) return item.title.slice(0, 40);
  if (item.text) return item.text.slice(0, 40);
  if (item.url) {
    try {
      return new URL(item.url).host;
    } catch {
      return item.url.slice(0, 40);
    }
  }
  return "Voice clip";
}

function errorToMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return formatCaptureErrorMessage(err.status, err.detail);
  }
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

export function QuickCapturePage(): JSX.Element {
  const { client, user } = useAuth();
  // The page renders only inside RequireAuth after the sign-in reconcile (LUM-886), so
  // this is the reconciled member whenever a handler can run.
  const ownerId = user?.id ?? "";
  const online = useOnlineStatus();
  // LUM-606: Compose · Inbox · Outbox tabs. The device-only outbox stays a
  // separate surface from the server-backed inbox.
  const [captureTab, setCaptureTab] = useState<CaptureTab>("compose");
  /** Outbox item waiting for the "Discard this item?" confirmation. */
  const [discardId, setDiscardId] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const clientIdRef = useRef<string | null>(null);
  if (clientIdRef.current === null) {
    clientIdRef.current = randomUuid();
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
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mountedRef = useRef(true);
  const chunksRef = useRef<Blob[]>([]);

  const [outboxItems, setOutboxItems] = useState<CaptureOutboxItemMeta[]>([]);
  /** Rows written before LUM-886 (no owner): shown apart, claim-or-discard per item. */
  const [legacyItems, setLegacyItems] = useState<CaptureOutboxItemMeta[]>([]);
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
    const [items, stats, legacy] = await Promise.all([
      listOutboxItems(ownerId),
      getOutboxStats(ownerId),
      listLegacyOutboxItems(),
    ]);
    setOutboxItems(items);
    setOutboxStats(stats);
    setLegacyItems(legacy);
  }, [ownerId]);

  useEffect(() => {
    setRecorderMime(pickAudioMimeType());
  }, []);

  useEffect(() => {
    void refreshOutbox();
  }, [refreshOutbox]);

  // LUM-886: leaving the page while recording (sign-out, navigation) must release the
  // microphone and save nothing — the next member's session must not inherit a live mic.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const mr = mediaRecorderRef.current;
      mediaRecorderRef.current = null;
      if (mr) {
        mr.ondataavailable = null;
        try {
          if (mr.state !== "inactive") mr.stop();
        } catch {
          /* already stopped */
        }
      }
      mediaStreamRef.current?.getTracks().forEach((tr) => tr.stop());
      mediaStreamRef.current = null;
      chunksRef.current = [];
    };
  }, []);

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
        ownerUserId: ownerId,
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
      const r = await syncOutbox(client, ownerId);
      await refreshOutbox();
      if (r.stopped) {
        setInfo("Sync stopped because you signed out.");
      } else if (r.synced > 0 && r.failed === 0) {
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
      await discardOutboxItem(localCaptureId, ownerId);
      await refreshOutbox();
      setInfo("Local item discarded.");
    } catch (e) {
      setError(outboxErrorToMessage(e));
    } finally {
      setBusy(false);
    }
  };

  /** Legacy row: give it to the signed-in member, then sync just that row. */
  const handleClaimLegacy = async (localCaptureId: string) => {
    setError(null);
    setInfo(null);
    if (!online) {
      setError("Connect to Lumogis, then sync this capture to your account.");
      return;
    }
    setBusy(true);
    try {
      if (!(await claimLegacyOutboxItem(localCaptureId, ownerId))) {
        setError("That capture is no longer available to claim.");
      } else {
        const r = await syncOutbox(client, ownerId, { onlyIds: [localCaptureId] });
        if (r.stopped) setInfo("Sync stopped because you signed out.");
        else if (r.failed > 0) setError(r.errors.filter(Boolean).join(" — ") || "Sync failed.");
        else setInfo("Synced to your account.");
      }
      await refreshOutbox();
    } catch (e) {
      setError(errorToMessage(e));
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
      await uploadCaptureAttachment(client, captureId, file, randomUuid());
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
        ownerUserId: ownerId,
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
      if (!mountedRef.current) {
        // The page went away while the permission prompt was pending: release the mic.
        stream.getTracks().forEach((tr) => tr.stop());
        return;
      }
      mediaStreamRef.current = stream;
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
          ownerUserId: ownerId,
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
        await uploadCaptureAttachment(client, captureId, file, randomUuid());
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
  const recorderSupported = typeof MediaRecorder !== "undefined" && !!recorderMime;
  const nothingOnDevice = outboxItems.length === 0 && legacyItems.length === 0;

  // One recorder control for whichever place it shows in: local while offline, the
  // saved capture while online (same gating as before).
  const recorderUi = !recorderSupported ? (
    <p data-testid="quick-capture-recorder-disabled" className="m-0 text-sm text-muted-foreground">
      Recording is not supported in this browser. Use an audio file instead.
    </p>
  ) : !online ? (
    <div className="flex flex-wrap items-center gap-3">
      {!recording ? (
        <Button
          type="button"
          variant="outline"
          data-testid="quick-capture-record-local-start"
          onClick={() => void startRecording(true)}
          disabled={busy}
        >
          Start recording (local)
        </Button>
      ) : (
        <Button
          type="button"
          variant="outline"
          data-testid="quick-capture-record-local-stop"
          onClick={() => void stopRecording(true)}
          disabled={busy}
        >
          Stop &amp; save locally
        </Button>
      )}
      <span className="text-sm text-muted-foreground">
        {recording ? "Recording…" : "Stored only on this device until Sync now."}
      </span>
    </div>
  ) : (
    <div className="flex flex-wrap items-center gap-3">
      {!recording ? (
        <Button type="button" variant="outline" onClick={() => void startRecording(false)} disabled={busy || !online}>
          Start recording
        </Button>
      ) : (
        <Button type="button" variant="outline" onClick={() => void stopRecording(false)} disabled={busy}>
          Stop &amp; upload
        </Button>
      )}
      <span className="text-sm text-muted-foreground">
        {recording ? "Recording…" : "Starts only when you tap; uploads to the server."}
      </span>
    </div>
  );

  const addToMemoryTitle = !online
    ? "Requires a connection"
    : !detail || detail.status === "indexed"
      ? detail?.status === "indexed"
        ? "Already in memory"
        : "Save a capture first"
      : !canAddToMemory
        ? "Add text, a link, or finish transcription for audio"
        : "Add this capture to searchable memory";

  return (
    <div data-testid="quick-capture-page" className="pb-8">
      <PageHeader
        title="Quick capture"
        description="Jot something down. It stays in your inbox until you add it to memory."
      />

      <Tabs value={captureTab} onValueChange={(v) => setCaptureTab(v as CaptureTab)} className="mb-4">
        <TabsList aria-label="Capture views" data-testid="capture-tabs">
          {CAPTURE_TABS.map((t) => (
            <TabsTrigger key={t.id} value={t.id} data-testid={`capture-tab-${t.id}`}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Connectivity indicator stays global across tabs (LUM-606). */}
      {!online && (
        <Alert role="status" data-testid="quick-capture-offline" className="mb-4 max-w-2xl">
          <AlertDescription>
            You seem offline or unreachable. Saving to the server is off; save on this device and
            sync from the Outbox tab later. Photo upload stays off until you are online.
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert variant="destructive" role="alert" data-testid="quick-capture-error" className="mb-4 max-w-2xl">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {info && (
        <Alert role="status" data-testid="quick-capture-info" className="mb-4 max-w-2xl">
          <AlertDescription>{info}</AlertDescription>
        </Alert>
      )}

      {captureTab === "inbox" && <CaptureInboxView />}

      {captureTab === "archive" && <CaptureArchiveView />}

      {captureTab === "outbox" && (
        <>
          <div data-testid="capture-outbox-panel">
            <PageSection
              title="On this device"
              description={
                <>
                  Nothing leaves this device until you tap <strong>Sync now</strong>.
                </>
              }
            >
              <div className="flex flex-col gap-3">
                <p data-testid="capture-outbox-stats" className="m-0 text-sm">
                  Pending: <strong>{outboxStats.pendingCount}</strong> · Voice clips:{" "}
                  <strong>{outboxStats.pendingAudioCount}</strong> · Local voice ~ <strong>{audioMiB}</strong> MiB /{" "}
                  {MAX_LOCAL_VOICE_BYTES_TOTAL / 1024 / 1024} MiB
                </p>
                <div>
                  <Button
                    type="button"
                    data-testid="quick-capture-sync-outbox"
                    disabled={busy || !online || outboxStats.pendingCount === 0}
                    onClick={() => void handleSyncOutbox()}
                  >
                    Sync now
                  </Button>
                </div>
                {outboxItems.length > 0 && (
                  <ul data-testid="capture-outbox-list" className="m-0 flex list-none flex-col p-0">
                    {outboxItems.map((item) => (
                      <li
                        key={item.local_capture_id}
                        className="flex flex-wrap items-center justify-between gap-2 border-b py-2 last:border-b-0"
                      >
                        <OutboxItemSummary item={item} />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={busy}
                          onClick={() => setDiscardId(item.local_capture_id)}
                        >
                          Discard
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                {nothingOnDevice && (
                  <EmptyState
                    className="py-6"
                    title="Nothing waiting on this device"
                    helperText="Notes and voice clips you save while offline wait here until you sync them."
                    actions={[{ label: "Write a note", onClick: () => setCaptureTab("compose") }]}
                  />
                )}
              </div>
            </PageSection>
          </div>

          {legacyItems.length > 0 && (
            <div data-testid="capture-outbox-legacy-panel">
              <PageSection
                title="Saved on this device before an update"
                description="These were saved before Lumogis recorded who saved them, so anyone who signs in on this device can see them. Sync one to your account only if it is yours."
              >
                <ul data-testid="capture-outbox-legacy-list" className="m-0 flex list-none flex-col p-0">
                  {legacyItems.map((item) => (
                    <li
                      key={item.local_capture_id}
                      className="flex flex-wrap items-center justify-between gap-2 border-b py-2 last:border-b-0"
                    >
                      <OutboxItemSummary item={item} preview={legacyPreview(item)} />
                      <span className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          disabled={busy || !online}
                          onClick={() => void handleClaimLegacy(item.local_capture_id)}
                        >
                          Sync to my account
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={busy}
                          onClick={() => setDiscardId(item.local_capture_id)}
                        >
                          Discard
                        </Button>
                      </span>
                    </li>
                  ))}
                </ul>
              </PageSection>
            </div>
          )}

          <Collapsible className="max-w-2xl">
            <CollapsibleTrigger
              render={<Button type="button" variant="ghost" size="sm" className="group/trigger -ml-2" />}
            >
              <ChevronDown
                aria-hidden="true"
                className="transition-transform group-aria-expanded/trigger:rotate-180"
              />
              Why does the outbox wait for me?
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-2 text-sm text-muted-foreground">
              <p className="m-0">
                Pending captures can live only on this device until you choose to sync. Local voice clips use storage (
                {MAX_PENDING_VOICE_CLIPS} clips max, {MAX_LOCAL_VOICE_BYTES_TOTAL / 1024 / 1024} MiB total). Nothing
                uploads automatically when you go online — no background sync. Add to memory runs only when you tap it.
              </p>
            </CollapsibleContent>
          </Collapsible>

          <AlertDialog open={discardId !== null} onOpenChange={(open) => (open ? undefined : setDiscardId(null))}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Discard this item?</AlertDialogTitle>
                <AlertDialogDescription>
                  It is stored only on this device and has not been uploaded. Discarding it deletes it for good.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructiveSolid"
                  onClick={() => {
                    const id = discardId;
                    setDiscardId(null);
                    if (id) void handleDiscardOutbox(id);
                  }}
                >
                  Discard
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}

      {captureTab === "compose" && (
        <>
          <PageSection title="New capture">
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="qc-title">Title (optional)</Label>
                <Input id="qc-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="qc-note">Note</Label>
                <Textarea
                  id="qc-note"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  disabled={busy}
                  rows={5}
                  placeholder="Short note (required unless you add a URL below)"
                  className="min-h-32"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="qc-url">URL (optional)</Label>
                <Input
                  id="qc-url"
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={busy}
                  placeholder="https://…"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="qc-tags">Tags (optional, comma-separated)</Label>
                <Input id="qc-tags" type="text" value={tagsRaw} onChange={(e) => setTagsRaw(e.target.value)} disabled={busy} />
              </div>

              {/* One Save. Online it saves to the server; offline the same button keeps the
                  capture on this device. The other route can never work in the current state
                  (the handlers refuse it), so it is not offered as a second button. */}
              <div className="flex flex-wrap items-center gap-3">
                {online ? (
                  <Button
                    type="button"
                    variant={captureId ? "outline" : "default"}
                    data-testid="quick-capture-save-server"
                    onClick={() => void handleCreate()}
                    disabled={busy}
                  >
                    Save capture
                  </Button>
                ) : (
                  <Button
                    type="button"
                    data-testid="quick-capture-save-local"
                    onClick={() => void handleSaveLocalOutbox()}
                    disabled={busy}
                  >
                    Save on this device
                  </Button>
                )}
                <span className="text-sm text-muted-foreground">
                  {online
                    ? "Saves to your household server."
                    : "Stays on this device until you tap Sync now in the Outbox tab."}
                </span>
              </div>

              {online && !captureId && (
                <div className="flex flex-col gap-2 text-sm text-muted-foreground">
                  <p data-testid="quick-capture-attachments-locked" className="m-0">
                    Save the capture first to add photos or voice recordings to it.
                  </p>
                  {!recorderSupported && recorderUi}
                </div>
              )}
            </div>
          </PageSection>

          {(captureId || !online) && (
            <PageSection title="Photos and audio">
              <div className="flex flex-col gap-4">
                {captureId && (
                  <>
                    <div className="flex flex-col gap-1.5">
                      <Label htmlFor="qc-image">
                        Photo (JPEG, PNG, WebP)
                        {!online && <span className="text-xs font-normal text-muted-foreground"> — needs a connection</span>}
                      </Label>
                      <Input
                        id="qc-image"
                        type="file"
                        data-testid="quick-capture-image-input"
                        accept="image/jpeg,image/png,image/webp"
                        disabled={busy || !online}
                        onChange={(e) => void handleImagePick(e.target.files)}
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Label htmlFor="qc-audio">Upload an audio file</Label>
                      <Input
                        id="qc-audio"
                        type="file"
                        accept="audio/*,audio/webm,video/webm"
                        disabled={busy || !online}
                        onChange={(e) => void handleAudioFilePick(e.target.files)}
                      />
                    </div>
                  </>
                )}
                {!online && (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="qc-audio-local">Audio file for this device only</Label>
                    <Input
                      id="qc-audio-local"
                      type="file"
                      data-testid="quick-capture-offline-audio-file"
                      accept="audio/*,audio/webm,video/webm"
                      disabled={busy}
                      onChange={(e) => void handleOfflineAudioFile(e.target.files)}
                    />
                  </div>
                )}

                <div data-testid="quick-capture-recorder-ui">{recorderUi}</div>

                {detail && detail.attachments.length > 0 && (
                  <ul data-testid="quick-capture-attachment-list" className="m-0 flex list-none flex-col p-0">
                    {detail.attachments.map((a) => (
                      <li
                        key={a.id}
                        className="flex flex-wrap items-center justify-between gap-2 border-b py-2 text-sm last:border-b-0"
                      >
                        <span className="font-medium">{a.original_filename ?? attachmentLabel(a.attachment_type)}</span>
                        <span className="text-muted-foreground">
                          {attachmentLabel(a.attachment_type)} · {a.mime_type} · {(a.size_bytes / 1024).toFixed(1)} KiB
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </PageSection>
          )}

          {captureId && (lastAudioAttachment || transcripts.length > 0) && (
            <PageSection title="Transcript">
              <div className="flex flex-col gap-3">
                {lastAudioAttachment && (
                  <div>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void handleTranscribe(lastAudioAttachment.id)}
                      disabled={busy || !online}
                      data-testid="quick-capture-transcribe-btn"
                    >
                      Transcribe audio
                    </Button>
                  </div>
                )}
                {transcripts.length > 0 && (
                  <ul data-testid="quick-capture-transcript-list" className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
                    {transcripts.map((tr) => (
                      <li key={tr.id}>
                        {tr.transcript_text ? tr.transcript_text : `Transcription ${tr.transcript_status}`}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </PageSection>
          )}

          {captureId && detail && (
            <PageSection title="Saved capture">
              <div data-testid="quick-capture-detail" className="flex flex-col gap-3">
                <p className="m-0 text-sm">
                  <Badge variant={detail.status === "indexed" ? "default" : detail.status === "failed" ? "destructive" : "secondary"}>
                    {captureStatusLabel(detail.status)}
                  </Badge>
                </p>
                <div>
                  <Button
                    type="button"
                    disabled={busy || !canAddToMemory}
                    data-testid="quick-capture-add-memory"
                    title={addToMemoryTitle}
                    onClick={() => void handleAddToMemory()}
                  >
                    Add to memory
                  </Button>
                </div>
                <Collapsible>
                  <CollapsibleTrigger
                    render={<Button type="button" variant="ghost" size="sm" className="group/trigger -ml-2" />}
                  >
                    <ChevronDown
                      aria-hidden="true"
                      className="transition-transform group-aria-expanded/trigger:rotate-180"
                    />
                    Details
                  </CollapsibleTrigger>
                  <CollapsibleContent className="pt-2 text-sm text-muted-foreground">
                    <p className="m-0">
                      Type: {detail.capture_type}
                      {detail.note_id ? ` · Note: ${detail.note_id}` : ""} · Attachments: {detail.attachments.length}
                    </p>
                  </CollapsibleContent>
                </Collapsible>
              </div>
            </PageSection>
          )}
        </>
      )}
    </div>
  );
}

type CaptureTab = "compose" | "inbox" | "archive" | "outbox";

const CAPTURE_TABS: { id: CaptureTab; label: string }[] = [
  { id: "compose", label: "Compose" },
  { id: "inbox", label: "Inbox" },
  { id: "archive", label: "Archive" },
  { id: "outbox", label: "Outbox" },
];

function attachmentLabel(type: string): string {
  if (type === "image") return "Photo";
  if (type === "audio") return "Audio";
  return type;
}

function captureStatusLabel(status: string): string {
  if (status === "indexed") return "In memory";
  if (status === "failed") return "Adding to memory failed";
  return "Saved, not in memory yet";
}

const OUTBOX_KIND_LABEL: Record<string, string> = { text: "Note", url: "Link", audio: "Voice clip" };
const OUTBOX_STATUS_LABEL: Record<string, string> = {
  local_pending: "On this device",
  syncing: "Syncing",
  synced: "Synced",
  failed: "Failed",
};

function OutboxItemSummary({ item, preview }: { item: CaptureOutboxItemMeta; preview?: string }): JSX.Element {
  return (
    <span className="flex min-w-0 flex-col gap-0.5 text-sm">
      <span className="font-medium">
        {OUTBOX_KIND_LABEL[item.kind] ?? item.kind}
        {preview ? ` “${preview}”` : ""}
        {item.kind === "audio" ? ` · ${(item.size_bytes / 1024).toFixed(0)} KiB` : ""}
      </span>
      <span className="text-muted-foreground">
        {formatDateTime(item.created_at)} · {OUTBOX_STATUS_LABEL[item.status] ?? item.status}
        {item.last_error ? ` — ${item.last_error}` : ""}
      </span>
    </span>
  );
}
