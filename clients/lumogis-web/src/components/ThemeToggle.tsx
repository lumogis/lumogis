// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Cycles "system" → "light" → "dark" → "system" via theme.ts.

import { useEffect, useState } from "react";

import { getStoredTheme, setStoredTheme, type ThemeMode } from "../design/theme";

const ORDER: ReadonlyArray<ThemeMode> = ["system", "light", "dark"];
const LABEL: Record<ThemeMode, string> = {
  system: "Theme: system",
  light: "Theme: light",
  dark: "Theme: dark",
};

export function ThemeToggle(): JSX.Element {
  const [mode, setMode] = useState<ThemeMode>(() => getStoredTheme());

  useEffect(() => {
    setStoredTheme(mode);
  }, [mode]);

  return (
    <button
      type="button"
      onClick={() => {
        const idx = ORDER.indexOf(mode);
        const next = ORDER[(idx + 1) % ORDER.length]!;
        setMode(next);
      }}
      style={{
        background: "transparent",
        border: 0,
        color: "inherit",
        cursor: "pointer",
        font: "inherit",
        padding: "0.5rem 0.75rem",
      }}
      aria-label="Toggle theme"
    >
      {LABEL[mode]}
    </button>
  );
}
