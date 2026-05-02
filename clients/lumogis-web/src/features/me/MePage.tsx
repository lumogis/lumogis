// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { Outlet } from "react-router-dom";
import { MeNav } from "./MeNav";

export function MePage(): JSX.Element {
  return (
    <div className="lumogis-subshell lumogis-subshell--me">
      <MeNav />
      <div className="lumogis-subshell__content">
        <Outlet />
      </div>
    </div>
  );
}
