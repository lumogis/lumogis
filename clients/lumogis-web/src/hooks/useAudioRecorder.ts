// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — headless push-to-talk audio recorder (mic → Blob). Factored out of
// QuickCapturePage so both capture upload and voice dictation share one
// MediaRecorder implementation. Records only; transcription lives in
// useVoiceDictation. Behaviour is preserved from QuickCapture, including the
// "no supported mime → refuse with guidance" path.

import { useCallback, useEffect, useRef, useState } from "react";

export type RecorderStatus = "idle" | "recording" | "error";

/** Ordered codec candidates; first supported wins. Empty string = none supported
 * (QuickCapture's convention — the caller refuses rather than recording). */
export function pickAudioMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (const c of candidates) {
    if (MediaRecorder.isTypeSupported(c)) return c;
  }
  return "";
}

const UNSUPPORTED_MSG =
  "Recording is not supported in this browser. Use an audio file instead.";

export interface UseAudioRecorder {
  status: RecorderStatus;
  /** MediaRecorder + getUserMedia are present in this environment. */
  supported: boolean;
  start: () => Promise<void>;
  /** Stop and resolve the recorded Blob (null when nothing was captured). */
  stop: () => Promise<Blob | null>;
  cancel: () => void;
  error: string | null;
}

export function useAudioRecorder(
  opts: { onError?: (msg: string) => void } = {},
): UseAudioRecorder {
  const { onError } = opts;
  const [status, setStatus] = useState<RecorderStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeRef = useRef<string>("");
  // Synchronous guard against a double start/stop (state is async, so the
  // closure's `status` can still read "idle" on a second call in the same tick).
  const activeRef = useRef(false);

  const supported =
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia;

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const fail = useCallback(
    (msg: string) => {
      activeRef.current = false;
      stopTracks();
      recorderRef.current = null;
      setStatus("error");
      setError(msg);
      onError?.(msg);
    },
    [onError, stopTracks],
  );

  const start = useCallback(async () => {
    if (activeRef.current) return; // synchronous guard against double-start
    activeRef.current = true;
    setError(null);
    const mime = pickAudioMimeType();
    if (!mime) {
      // Preserve QuickCapture's refuse-with-guidance behaviour — do not silently
      // fall back to a browser-default container the sidecar might reject.
      fail(UNSUPPORTED_MSG);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      mimeRef.current = mime;
      const mr = new MediaRecorder(stream, { mimeType: mime });
      recorderRef.current = mr;
      mr.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      mr.onerror = () => fail("Recording failed.");
      mr.start();
      setStatus("recording");
    } catch {
      fail("Microphone permission denied or recording failed.");
    }
  }, [fail]);

  const stop = useCallback(async (): Promise<Blob | null> => {
    activeRef.current = false;
    const mr = recorderRef.current;
    if (!mr || mr.state === "inactive") {
      setStatus("idle");
      return null;
    }
    // Await the final ondataavailable flush before reading chunks — assembling
    // synchronously after stop() drops the last chunk.
    await new Promise<void>((resolve) => {
      mr.addEventListener("stop", () => resolve(), { once: true });
      mr.stop();
    });
    stopTracks();
    recorderRef.current = null;
    setStatus("idle");
    const blob = new Blob(chunksRef.current, { type: mimeRef.current });
    chunksRef.current = [];
    return blob.size === 0 ? null : blob;
  }, [stopTracks]);

  const cancel = useCallback(() => {
    activeRef.current = false;
    const mr = recorderRef.current;
    if (mr && mr.state !== "inactive") {
      try {
        mr.stop();
      } catch {
        /* already stopped */
      }
    }
    stopTracks();
    recorderRef.current = null;
    chunksRef.current = [];
    setStatus("idle");
    setError(null);
  }, [stopTracks]);

  // Stop any live mic on unmount so a recording never outlives the component.
  useEffect(() => {
    return () => {
      const mr = recorderRef.current;
      if (mr && mr.state !== "inactive") {
        try {
          mr.stop();
        } catch {
          /* noop */
        }
      }
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  return { status, supported, start, stop, cancel, error };
}
