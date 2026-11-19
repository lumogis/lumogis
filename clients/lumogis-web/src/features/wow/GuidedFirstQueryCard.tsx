// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { JSX } from "react";

import { Button } from "../../components/ui/button";

import { CHIP } from "./chip";

const GUIDED_PROMPTS = [
  "What deadlines or commitments appear across my documents?",
  "Which people or organisations show up in more than one place, and how are they connected?",
  "Summarise recurring themes across everything Lumogis has ingested so far.",
  "What should I follow up on based on notes and files Lumogis knows about?",
] as const;

export interface GuidedFirstQueryCardProps {
  onPickPrompt: (prompt: string) => void;
  /** Kept for callers; the chips need no separate "type my own" action (LUM-993). */
  onTypeOwn?: () => void;
  onDismiss: () => void;
  isDismissing?: boolean;
}

export function GuidedFirstQueryCard({
  onPickPrompt,
  onDismiss,
  isDismissing = false,
}: GuidedFirstQueryCardProps): JSX.Element {
  // LUM-993: suggestion chips under the composer; picking one fills it in.
  return (
    <section data-testid="wow-guided-card" aria-labelledby="wow-guided-title" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 id="wow-guided-title" className="m-0 text-xs font-medium text-muted-foreground">
          Try asking
        </h2>
        <Button variant="ghost" size="xs" onClick={() => void onDismiss()} disabled={isDismissing}>
          Dismiss
        </Button>
      </div>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" role="list">
        {GUIDED_PROMPTS.map((prompt) => (
          <li key={prompt} className="min-w-0">
            <Button type="button" variant="outline" className={CHIP} onClick={() => onPickPrompt(prompt)}>
              {prompt}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
