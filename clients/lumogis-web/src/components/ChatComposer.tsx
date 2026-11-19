// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type FormEvent,
  type ReactNode,
  type Ref,
} from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "./ui/button";
import { VoiceDictationButton } from "./VoiceDictationButton";
import { cn } from "@/lib/utils";

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
  /** Left side of the composer's bottom row, e.g. the model chip (LUM-993 Decision 9). */
  modelSlot?: ReactNode;
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
  placeholder = "Message Lumogis",
  label = "Message",
  showLabel = false,
  minRows = 1,
  maxRows = 8,
  textareaRef,
  describedBy,
  className,
  voiceTranscribe,
  modelSlot,
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

  const canSend = !sendDisabled && !disabled && value.trim().length > 0;

  // LUM-993: one rounded card. The textarea grows to `maxRows`; the bottom row
  // holds the model chip on the left and Dictate plus Send (Stop while
  // streaming) on the right. Enter sends, Shift+Enter makes a new line.
  return (
    <form
      data-slot="chat-composer"
      className={cn(
        "flex w-full flex-col gap-1 rounded-2xl border border-input bg-card p-2 shadow-sm transition-shadow focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30",
        className,
      )}
      onSubmit={handleSubmit}
    >
      <label htmlFor={id} className={showLabel ? "px-2 pt-1 text-xs font-medium text-muted-foreground" : "sr-only"}>
        {label}
      </label>
      <textarea
        ref={ref}
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={minRows}
        disabled={disabled || streaming}
        aria-describedby={describedBy}
        className="w-full resize-none border-0 bg-transparent px-2 py-1.5 text-base leading-6 text-foreground outline-none placeholder:text-muted-foreground disabled:opacity-60 md:text-sm"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (!streaming && !disabled && !sendDisabled && value.trim().length > 0) {
              e.currentTarget.form?.requestSubmit();
            }
          }
        }}
      />
      <div className="flex min-w-0 items-center gap-2">
        <div className="flex min-w-0 flex-1 items-center">{modelSlot}</div>
        {voiceTranscribe && !streaming ? (
          <VoiceDictationButton transcribe={voiceTranscribe} onTranscript={insertTranscript} disabled={disabled} />
        ) : null}
        {streaming ? (
          <Button type="button" variant="outline" size="icon" className="rounded-full" onClick={onStop} aria-label="Stop">
            <Square className="fill-current" aria-hidden="true" />
          </Button>
        ) : (
          <Button type="submit" size="icon" className="rounded-full" disabled={!canSend} aria-label="Send">
            <ArrowUp aria-hidden="true" />
          </Button>
        )}
      </div>
    </form>
  );
}
