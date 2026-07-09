// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { ReactNode } from "react";
import { MeNav } from "./MeNav";

export function MeSubshell({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="lumogis-subshell lumogis-subshell--me">
      <MeNav />
      <div className="lumogis-subshell__content">{children}</div>
    </div>
  );
}
