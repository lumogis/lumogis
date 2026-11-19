// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The "…" menu at the end of a row or card (LUM-993): the actions beyond the
// one primary one. `label` is the trigger's accessible name and must name the
// row ("More actions for Work mail").
import { MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface RowAction {
  label: string;
  onSelect: () => void;
  destructive?: boolean;
  disabled?: boolean;
}

export function RowActions({ label, items }: { label: string; items: RowAction[] }): JSX.Element {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button type="button" variant="ghost" size="icon" aria-label={label} />}
      >
        <MoreHorizontal aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-auto min-w-48">
        {items.map((it) => (
          <DropdownMenuItem
            key={it.label}
            variant={it.destructive ? "destructive" : "default"}
            disabled={it.disabled}
            onClick={it.onSelect}
          >
            {it.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
