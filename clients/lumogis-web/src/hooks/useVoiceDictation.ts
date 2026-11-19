// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — dictation composition: useAudioRecorder + an INJECTED transcribe
// function → text. The transcribe fn is injected (not pulled from context) so
// hosts supply it from a client they already hold; this keeps the hook usable
// inside the deliberately auth-free EmailComposer (which is rendered bare in
// tests). Never calls useAuth / any React context.

import { useCallback, useState } from "react";

import { dictationErrorMessage } from "../api/voice";
import { useAudioRecorder } from "./useAudioRecorder";

export type DictationStatus = "idle" | "recording" | "transcribing" | "error";

export interface UseVoiceDictation {
  status: DictationStatus;
  supported: boolean;
  /** Plain-HTTP page: the browser withholds the microphone (LUM-995). */
  insecureContext: boolean;
  /** The live microphone stream while recording (for a level meter), else null. */
  stream: MediaStream | null;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  cancel: () => void;
  error: string | null;
}

export function useVoiceDictation(opts: {
  transcribe: (blob: Blob) => Promise<string>;
  onTranscript: (text: string) => void;
  onError?: (msg: string) => void;
}): UseVoiceDictation {
  const { transcribe, onTranscript, onError } = opts;
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recorder = useAudioRecorder({
    onError: (msg) => {
      setError(msg);
      onError?.(msg);
    },
  });

  const { start: recorderStart } = recorder;
  const start = useCallback(async () => {
    setError(null); // a new attempt clears the last failure's message
    await recorderStart();
  }, [recorderStart]);

  const stop = useCallback(async () => {
    const blob = await recorder.stop();
    if (!blob) return; // nothing captured — back to idle
    setTranscribing(true);
    setError(null);
    try {
      const text = await transcribe(blob);
      if (text.length > 0) onTranscript(text);
    } catch (err) {
      const msg = dictationErrorMessage(err);
      setError(msg);
      onError?.(msg);
    } finally {
      setTranscribing(false);
    }
  }, [recorder, transcribe, onTranscript, onError]);

  const status: DictationStatus = transcribing
    ? "transcribing"
    : recorder.status === "recording"
      ? "recording"
      : recorder.status === "error"
        ? "error"
        : "idle";

  return {
    status,
    supported: recorder.supported,
    insecureContext: recorder.insecureContext,
    stream: recorder.stream,
    start,
    stop,
    cancel: recorder.cancel,
    error: error ?? recorder.error,
  };
}
