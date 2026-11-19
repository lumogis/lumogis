// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useEffect, useState } from "react";

import { getStoredTheme, setStoredTheme, type ThemeMode } from "../design/theme";
import { SegmentedControl } from "./SegmentedControl";

const ORDER: ReadonlyArray<ThemeMode> = ["system", "light", "dark"];
const LABEL: Record<ThemeMode, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

export interface ThemeToggleProps {
  /** Kept for callers; the old cycle button was removed in LUM-993 Pass 8 (unused). */
  variant?: "segment";
}

export function ThemeToggle(_props: ThemeToggleProps): JSX.Element {
  const [mode, setMode] = useState<ThemeMode>(() => getStoredTheme());

  useEffect(() => {
    setStoredTheme(mode);
  }, [mode]);

  // LUM-993: one segmented-control implementation for the whole app.
  return (
    <SegmentedControl
      ariaLabel="Theme"
      value={mode}
      onChange={(v) => setMode(v as ThemeMode)}
      options={ORDER.map((m) => ({ value: m, label: LABEL[m] }))}
    />
  );
}
