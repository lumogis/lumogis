// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Recipient field with address completion (LUM-818).
//
// Wraps a plain comma-separated recipient input — the shape To/Cc/Bcc already
// use — and adds a suggestion list drawn from correspondents the user already
// has. Deliberately not a "chip"/token control: that would be a larger change
// to how recipients are read back out, and `splitAddrs` on the parent already
// defines the contract.
//
// Three behaviours carry decisions:
//
//  * **Only the segment being typed is queried.** The field holds
//    "alice@x.com, bo" — completing against the whole string would match
//    nothing and the feature would look broken exactly when it is being used.
//  * **The bare addr-spec is inserted**, never the rendered "Name <addr>"
//    form. The backend's `_validate_recipient` rejects whitespace, so the
//    pretty form fails as a 422 at proposal-create with nothing on screen to
//    connect it to what the user picked.
//  * **A suggestion is a memory aid, not a validator.** Picking one does not
//    mean the address is deliverable, and the first-contact confirm still
//    fires. The UI says nothing that implies otherwise.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { JSX, KeyboardEvent } from "react";

import { Input } from "@/components/ui/input";
import type { RecipientSuggestion } from "../../api/emailRecipients";
import { MIN_QUERY_CHARS } from "../../api/emailRecipients";

const DEBOUNCE_MS = 250;
/* Raised from 150ms (LUM-818 review). The endpoint's bucket is 90/min and a
   composer has three recipient fields, so a fast typist working across To, Cc
   and Bcc could otherwise burn the bucket and hit 429 mid-compose. The client
   swallows failures silently, so that shows up as suggestions mysteriously
   stopping — the worst failure mode available. Slowing the ask is the fix that
   does not weaken the enumeration bound; raising the bucket would. */

/** The text after the last comma — what the user is typing right now. */
function activeSegment(value: string): string {
  const idx = value.lastIndexOf(",");
  return (idx === -1 ? value : value.slice(idx + 1)).trim();
}

/** Replace the segment being typed with `address`, keeping earlier recipients. */
function replaceActiveSegment(value: string, address: string): string {
  const idx = value.lastIndexOf(",");
  const head = idx === -1 ? "" : value.slice(0, idx + 1);
  return `${head}${head ? " " : ""}${address}, `;
}

export type SuggestRecipients = (
  query: string,
  signal?: AbortSignal,
) => Promise<RecipientSuggestion[]>;

export interface RecipientInputProps {
  /** Injected, like the rest of ComposerApi, so this is testable without a client. */
  suggest: SuggestRecipients;
  value: string;
  onChange: (next: string) => void;
  label: string;
  placeholder: string;
  /** Optional extra classes for the wrapper. */
  className?: string;
}

export function RecipientInput(props: RecipientInputProps): JSX.Element {
  const { suggest, value, onChange, label, placeholder, className } = props;
  const listId = useId();

  const [suggestions, setSuggestions] = useState<RecipientSuggestion[]>([]);
  const [highlighted, setHighlighted] = useState(0);
  // Dismissed with Escape, or just after picking. Reset when typing resumes, so
  // Escape hides the list without disabling the feature for the rest of the edit.
  const [dismissed, setDismissed] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const segment = activeSegment(value);
  const open = !dismissed && suggestions.length > 0 && segment.length >= MIN_QUERY_CHARS;

  useEffect(() => {
    if (segment.length < MIN_QUERY_CHARS || dismissed) {
      setSuggestions([]);
      return;
    }
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    const timer = setTimeout(() => {
      suggest(segment, controller.signal)
        .then((found) => {
          if (!controller.signal.aborted) {
            setSuggestions(found);
            setHighlighted(0);
          }
        })
        // Suggestions are an enhancement: a failed lookup must leave the user
        // typing normally, never surface an error over a compose field.
        .catch(() => {
          if (!controller.signal.aborted) setSuggestions([]);
        });
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [suggest, segment, dismissed]);

  const pick = useCallback(
    (s: RecipientSuggestion) => {
      onChange(replaceActiveSegment(value, s.address));
      setSuggestions([]);
      setDismissed(true);
    },
    [onChange, value],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => (h + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => (h - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter") {
      // Only swallow Enter when a suggestion is actually showing — otherwise
      // this would eat the keystroke in a field the user has finished with.
      e.preventDefault();
      // Defensive: `highlighted` is reset to 0 by the fetch effect, so it is
      // in range in practice (probed — a shrinking list does not crash today).
      // But the invariant is maintained in a DIFFERENT function from the one
      // relying on it, so a future edit to that reset would turn this into a
      // crash on `undefined.address` inside a keystroke handler.
      const chosen = suggestions[highlighted];
      if (chosen) pick(chosen);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setDismissed(true);
    }
  };

  return (
    <div className={`relative ${className ?? ""}`.trim()}>
      <Input
        aria-label={label}
        placeholder={placeholder}
        value={value}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-controls={listId}
        onChange={(e) => {
          setDismissed(false);
          onChange(e.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      {open ? (
        <ul
          className="absolute inset-x-0 top-full z-10 mt-1 max-h-48 list-none overflow-y-auto rounded-lg border bg-popover p-1 text-sm text-popover-foreground shadow-md"
          id={listId}
          role="listbox"
          data-testid={`${label.toLowerCase()}-suggestions`}
        >
          {suggestions.map((s, i) => (
            <li
              key={s.address}
              role="option"
              aria-selected={i === highlighted}
              className="cursor-pointer rounded-md px-2 py-1.5 aria-selected:bg-accent aria-selected:text-accent-foreground"
              // onMouseDown, not onClick: click fires after blur, by which point
              // the list has closed and the pick is lost.
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
            >
              {s.display_name ? (
                <>
                  <span className="font-medium">{s.display_name}</span>{" "}
                  <span className="text-muted-foreground">{s.address}</span>
                </>
              ) : (
                <span className="text-muted-foreground">{s.address}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
