// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The (i) help popover (LUM-993). Help text style: say what it does, then why
// or what follows, in at most two short sentences.
import { Info } from "lucide-react";
import type { ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function InfoTip({ about, children }: { about: string; children: ReactNode }): JSX.Element {
  return (
    <Popover>
      <PopoverTrigger
        aria-label={`About ${about}`}
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring pointer-coarse:size-[44px]"
      >
        <Info className="size-4" aria-hidden="true" />
      </PopoverTrigger>
      <PopoverContent align="start" className="leading-snug">
        {children}
      </PopoverContent>
    </Popover>
  );
}
