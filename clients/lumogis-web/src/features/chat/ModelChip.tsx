// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The model choice inside the chat composer (LUM-993 Decision 9). A native
// <select> styled as a chip, not a custom list: phones then show their own
// picker, and it stays the combobox named "Model" that the chat tests use.
// A model that cannot be used is listed but disabled, with the reason after
// its name (LUM-881).

import { ChevronDown, LoaderCircle } from "lucide-react";

import type { ModelDescriptor } from "../../api/models";
import { Button } from "../../components/ui/button";
import { cn } from "@/lib/utils";

export interface ModelChipProps {
  models: ModelDescriptor[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  error: string | null;
  onRetry: () => void;
  className?: string;
}

/** Option suffix for a disabled model; an unknown or missing reason reads "unavailable". */
function disabledSuffix(m: ModelDescriptor): string {
  if (m.enabled) return "";
  switch (m.disabled_reason) {
    case "privacy_mode":
      return " — off (privacy mode)";
    case "no_credentials":
      return " — no API key";
    case "optional_disabled":
      return " — not turned on";
    default:
      return " — unavailable";
  }
}

export function ModelChip({ models, value, onChange, disabled = false, error, onRetry, className }: ModelChipProps): JSX.Element {
  if (error !== null) {
    return (
      <div className={cn("flex min-w-0 items-center gap-1 text-xs text-destructive", className)} role="alert">
        <span className="truncate">Unable to load models: {error}.</span>
        <Button variant="ghost" size="xs" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (models.length === 0) {
    return (
      <span className={cn("inline-flex items-center gap-1.5 px-2 text-xs text-muted-foreground", className)} aria-busy="true">
        <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
        Loading models…
      </span>
    );
  }
  return (
    <span className={cn("relative inline-flex min-w-0 max-w-full", className)}>
      <select
        aria-label="Model"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="h-8 max-w-full min-w-0 cursor-pointer appearance-none truncate rounded-full border border-border bg-transparent py-0 pr-7 pl-3 text-xs font-medium text-foreground outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-default disabled:opacity-60 pointer-coarse:h-[44px]"
      >
        {models.map((m) => (
          <option key={m.id} value={m.id} disabled={!m.enabled}>
            {m.label}
            {m.is_local ? " (local)" : ""}
            {disabledSuffix(m)}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
    </span>
  );
}
