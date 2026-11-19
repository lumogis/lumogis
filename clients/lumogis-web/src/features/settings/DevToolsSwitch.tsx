// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "Show developer tools" (LUM-993 Pass 3), admins only. At the foot of the wide
// Settings menu and of the Settings index, so it is reachable on a phone too.
// Not a <label>: it would forward the click to the switch's hidden input and
// toggle it back; the text names the switch through aria-labelledby.

import { useId } from "react";

import { InfoTip } from "../../components/page/InfoTip";
import { Switch } from "../../components/ui/switch";
import { help } from "../../copy/help";
import { useDevToolsShown } from "../../design/devTools";
import { cn } from "@/lib/utils";

export function DevToolsSwitch({ className }: { className?: string }): JSX.Element {
  const [devTools, setDevTools] = useDevToolsShown();
  const labelId = useId();
  return (
    <div className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}>
      <Switch checked={devTools} onCheckedChange={(on: boolean) => setDevTools(on)} aria-labelledby={labelId} />
      <span id={labelId} className="flex-1">
        Show developer tools
      </span>
      <InfoTip about="developer tools">{help.devTools.about}</InfoTip>
    </div>
  );
}
