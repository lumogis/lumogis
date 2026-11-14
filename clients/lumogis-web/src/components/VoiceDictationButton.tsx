// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — reusable push-to-talk dictation button. Thin view over
// useVoiceDictation; renders null when the browser lacks MediaRecorder/getUserMedia
// (capability gate). The transcribe function is injected by the host so this
// component stays context-free and host-agnostic (chat, email, future compose).

import { useEffect } from "react";

import { useVoiceDictation } from "../hooks/useVoiceDictation";
import { Button } from "./Button";

function MicIcon(): JSX.Element {
  return (
    <svg
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}

export interface VoiceDictationButtonProps {
  transcribe: (blob: Blob) => Promise<string>;
  onTranscript: (text: string) => void;
  onError?: (msg: string) => void;
  /** Fired with true while recording or transcribing, false when idle — lets a
   * host gate other actions (e.g. an email composer's Review button). */
  onBusyChange?: (busy: boolean) => void;
  disabled?: boolean;
  title?: string;
  className?: string;
}

export function VoiceDictationButton({
  transcribe,
  onTranscript,
  onError,
  onBusyChange,
  disabled = false,
  title,
  className,
}: VoiceDictationButtonProps): JSX.Element | null {
  const { status, supported, start, stop, error } = useVoiceDictation({
    transcribe,
    onTranscript,
    onError,
  });

  const busy = status === "recording" || status === "transcribing";

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  if (!supported) return null;

  const recording = status === "recording";
  const transcribing = status === "transcribing";

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={className}
      aria-pressed={recording}
      aria-label={recording ? "Stop dictation" : "Dictate"}
      title={title ?? error ?? undefined}
      disabled={disabled || transcribing}
      onClick={() => {
        if (recording) void stop();
        else void start();
      }}
    >
      <MicIcon />
      {recording ? "Stop" : transcribing ? "…" : "Dictate"}
    </Button>
  );
}
