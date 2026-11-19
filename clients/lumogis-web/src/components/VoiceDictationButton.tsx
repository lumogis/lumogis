// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — reusable push-to-talk dictation button. Thin view over
// useVoiceDictation; renders null when the browser lacks MediaRecorder/getUserMedia
// (capability gate). The transcribe function is injected by the host so this
// component stays context-free and host-agnostic (chat, email, future compose).
//
// LUM-995 — says what is happening in visible text (role="status"): "Listening…" with
// a level meter while recording, the reason when a transcription fails, and why the
// button is disabled on a plain-HTTP page (browsers withhold the microphone there).

import { useEffect } from "react";

import { cn } from "@/lib/utils";

import { useAudioLevel } from "../hooks/useAudioLevel";
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
  const { status, supported, insecureContext, stream, start, stop, error } = useVoiceDictation({
    transcribe,
    onTranscript,
    onError,
  });
  const level = useAudioLevel(stream);

  const busy = status === "recording" || status === "transcribing";

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  // Before the capability gate: an insecure page has no navigator.mediaDevices either,
  // and a disabled button with a reason beats a button that silently vanishes.
  if (insecureContext) {
    return (
      <span
        className={cn("inline-flex flex-wrap items-center gap-2", className)}
        data-testid="voice-dictation"
      >
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="Dictate"
          disabled
          title={title}
        >
          <MicIcon />
          Dictate
        </Button>
        <span role="status" className="text-sm text-muted-foreground">
          Voice input needs the secure address. Ask whoever runs Lumogis to turn it on in Admin
          → Secure address.
        </span>
      </span>
    );
  }
  if (!supported) return null;

  const recording = status === "recording";
  const transcribing = status === "transcribing";

  return (
    <span
      className={cn("inline-flex flex-wrap items-center gap-2", className)}
      data-testid="voice-dictation"
    >
      <Button
        type="button"
        variant="ghost"
        size="sm"
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
      {/* One live region that stays mounted, so screen readers announce what changes in it. */}
      <span
        role="status"
        aria-live="polite"
        className={cn(
          "inline-flex items-center gap-1.5 text-sm",
          recording ? "text-muted-foreground" : "text-destructive",
        )}
      >
        {recording ? (
          <>
            {level === null ? (
              <span
                aria-hidden
                data-testid="voice-level"
                data-static
                className="size-2 rounded-full bg-primary motion-safe:animate-pulse"
              />
            ) : (
              <span
                aria-hidden
                data-testid="voice-level"
                className="h-2 w-10 overflow-hidden rounded-full bg-muted"
              >
                <span
                  className="block h-full origin-left bg-primary transition-transform motion-reduce:transition-none"
                  style={{ transform: `scaleX(${level})` }}
                />
              </span>
            )}
            Listening…
          </>
        ) : (
          (error ?? null)
        )}
      </span>
    </span>
  );
}
