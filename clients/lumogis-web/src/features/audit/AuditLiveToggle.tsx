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
      aria-pressed={enabled}
      onClick={() => onChange(!enabled)}
    >
      {enabled ? "Live (on)" : "Live (off)"}
    </button>
  );
}
