// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { Toggle } from "../../components/ui/toggle";

export function AuditLiveToggle({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (next: boolean) => void;
}): JSX.Element {
  return (
    <Toggle
      variant="outline"
      size="sm"
      pressed={enabled}
      onPressedChange={onChange}
      title={enabled ? "Live updates on" : "Live updates off"}
    >
      Live
    </Toggle>
  );
}
