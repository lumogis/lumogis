// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

export function AuditLiveToggle({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (next: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      className="lumogis-audit-live-toggle"
      aria-pressed={enabled}
      title={enabled ? "Live tail on" : "Live tail off"}
      onClick={() => onChange(!enabled)}
    >
      Live
    </button>
  );
}
