// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { Outlet } from "react-router-dom";
import { MeSubshell } from "./MeSubshell";

export function MePage(): JSX.Element {
  return (
    <MeSubshell>
      <Outlet />
    </MeSubshell>
  );
}
