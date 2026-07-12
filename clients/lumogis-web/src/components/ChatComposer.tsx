// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import {
  useCallback,
  useEffect,
  useRef,
  type FormEvent,
  type Ref,
} from "react";

import { Button } from "./Button";

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
}: ChatComposerProps): JSX.Element {
  const localRef = useRef<HTMLTextAreaElement | null>(null);
  const ref = textareaRef ?? localRef;

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
