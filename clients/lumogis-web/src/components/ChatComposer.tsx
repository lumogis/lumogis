// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type FormEvent,
  type Ref,
} from "react";

import { Button } from "./Button";
import { VoiceDictationButton } from "./VoiceDictationButton";

export interface ChatComposerProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  streaming?: boolean;
  onStop?: () => void;
  disabled?: boolean;
  sendDisabled?: boolean;
  placeholder?: string;
  label?: string;
  showLabel?: boolean;
  minRows?: number;
  maxRows?: number;
  textareaRef?: Ref<HTMLTextAreaElement>;
  describedBy?: string;
  className?: string;
  /** When provided, a dictation button appears in the actions row and inserts
   * the transcript at the caret. The host supplies the transcribe fn (bound to
   * its ApiClient) so this shared component stays context-free (LUM-653). */
  voiceTranscribe?: (blob: Blob) => Promise<string>;
}

export function ChatComposer({
  id = "lumogis-chat-input",
  value,
  onChange,
  onSubmit,
  streaming = false,
  onStop,
  disabled = false,
  sendDisabled = false,
  placeholder = "Ask Lumogis…",
  label = "Message",
  showLabel = true,
  minRows = 3,
  maxRows = 8,
  textareaRef,
  describedBy,
  className,
  voiceTranscribe,
}: ChatComposerProps): JSX.Element {
  const localRef = useRef<HTMLTextAreaElement | null>(null);
  const ref = textareaRef ?? localRef;
  // Caret to restore AFTER the controlled value commits — a synchronous
  // selectionStart set right after onChange is overwritten on re-render.
  const pendingCaretRef = useRef<number | null>(null);

  const elFromRef = useCallback(
    (): HTMLTextAreaElement | null =>
      ref && typeof ref === "object" && "current" in ref ? ref.current : null,
    [ref],
  );

  const insertTranscript = useCallback(
    (text: string) => {
      if (text.length === 0) return;
      const el = elFromRef();
      // Splice into the LIVE textarea value (not the closure's `value`) so a
      // transcript that resolves after the user kept typing during the async
      // transcription doesn't clobber those edits.
      const current = el?.value ?? value;
      if (el && el.selectionStart != null) {
        const start = el.selectionStart;
        const end = el.selectionEnd ?? start;
        const next = current.slice(0, start) + text + current.slice(end);
        pendingCaretRef.current = start + text.length;
        onChange(next);
      } else {
        onChange(current + text);
      }
    },
    [value, onChange, elFromRef],
  );

  useLayoutEffect(() => {
    const caret = pendingCaretRef.current;
    if (caret == null) return;
    pendingCaretRef.current = null;
    const el = elFromRef();
    if (el) {
      el.focus();
      try {
        el.setSelectionRange(caret, caret);
      } catch {
        /* selection unsupported (jsdom edge) */
      }
    }
    // Runs after the controlled value has committed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const resize = useCallback(() => {
    const el =
      ref && typeof ref === "object" && "current" in ref ? ref.current : null;
    if (!el) return;
    el.style.height = "auto";
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 22;
    const maxHeight = lineHeight * maxRows;
    const minHeight = lineHeight * minRows;
    const next = Math.min(Math.max(el.scrollHeight, minHeight), maxHeight);
    el.style.height = `${next}px`;
  }, [maxRows, minRows, ref]);

  useEffect(() => {
    resize();
  }, [value, resize]);

  const handleSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (streaming) return;
    if (value.trim().length === 0 || disabled || sendDisabled) return;
    onSubmit();
  };

  return (
    <form
      className={["lumogis-chat__compose", className].filter(Boolean).join(" ")}
      onSubmit={handleSubmit}
    >
      {showLabel ? (
        <label htmlFor={id} className="lumogis-chat__compose-label">
          {label}
        </label>
      ) : null}
      <textarea
        ref={ref}
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={minRows}
        disabled={disabled || streaming}
        aria-describedby={describedBy}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!streaming && !disabled && !sendDisabled && value.trim().length > 0) {
              e.currentTarget.form?.requestSubmit();
            }
          }
        }}
      />
      <div className="lumogis-chat__compose-actions">
        {voiceTranscribe && !streaming ? (
          <VoiceDictationButton
            transcribe={voiceTranscribe}
            onTranscript={insertTranscript}
            disabled={disabled}
          />
        ) : null}
        {streaming ? (
          <Button type="button" variant="danger-solid" size="md" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={sendDisabled || disabled || value.trim().length === 0}
          >
            Send
          </Button>
        )}
      </div>
    </form>
  );
}
