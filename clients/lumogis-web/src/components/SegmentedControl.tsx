// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

export interface SegmentedOption {
  value: string;
  label: string;
  disabled?: boolean;
  title?: string;
}

export interface SegmentedControlProps {
  options: ReadonlyArray<SegmentedOption>;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
}

export function SegmentedControl({
  options,
  value,
  onChange,
  ariaLabel,
  className,
}: SegmentedControlProps): JSX.Element {
  // LUM-993: built on the shadcn ToggleGroup (Base UI): same props and the same
  // semantics as before (a labelled group of buttons with aria-pressed).
  return (
    <ToggleGroup
      variant="outline"
      size="sm"
      spacing={0}
      aria-label={ariaLabel}
      className={className}
      value={[value]}
      onValueChange={(next: unknown[]) => {
        const picked = next[0];
        // Pressing the selected option again would clear the group; keep it selected.
        if (typeof picked === "string") onChange(picked);
      }}
    >
      {options.map((opt) => (
        <ToggleGroupItem key={opt.value} value={opt.value} disabled={opt.disabled} title={opt.title}>
          {opt.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
