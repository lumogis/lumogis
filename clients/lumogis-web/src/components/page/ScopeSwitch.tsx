// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "Mine / Household" switch for pages that exist for one person and for the whole
// household (LUM-993). Controlled: the page that renders it owns the `?view` key.
// Members never see it; household views still call admin APIs that refuse them.

import { useUser } from "@/auth/AuthProvider";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { help } from "@/copy/help";

import { InfoTip } from "./InfoTip";

export type Scope = "mine" | "household";

export function ScopeSwitch({
  value,
  onChange,
}: {
  value: Scope;
  onChange: (next: Scope) => void;
}): JSX.Element | null {
  const user = useUser();
  if (user?.role !== "admin") return null;
  return (
    <div className="flex items-center gap-1">
      <ToggleGroup
        variant="outline"
        aria-label="Whose to show"
        value={[value]}
        onValueChange={(next: unknown[]) => {
          const picked = next[0];
          if (picked === "mine" || picked === "household") onChange(picked);
        }}
      >
        <ToggleGroupItem value="mine">Mine</ToggleGroupItem>
        <ToggleGroupItem value="household">Household</ToggleGroupItem>
      </ToggleGroup>
      <InfoTip about="Mine and Household">{help.scope.about}</InfoTip>
    </div>
  );
}
