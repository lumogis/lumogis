// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useEffect, useState } from "react";

import { getStoredTheme, setStoredTheme, type ThemeMode } from "../design/theme";

const ORDER: ReadonlyArray<ThemeMode> = ["system", "light", "dark"];
const LABEL: Record<ThemeMode, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

export interface ThemeToggleProps {
  /** Compact segmented control (default) or legacy cycle button. */
  variant?: "segment" | "button";
}

export function ThemeToggle({ variant = "segment" }: ThemeToggleProps): JSX.Element {
  const [mode, setMode] = useState<ThemeMode>(() => getStoredTheme());

  useEffect(() => {
    setStoredTheme(mode);
  }, [mode]);

  if (variant === "button") {
    const idx = ORDER.indexOf(mode);
    const next = ORDER[(idx + 1) % ORDER.length]!;
    return (
      <button
        type="button"
        className="lumogis-theme-button"
        onClick={() => setMode(next)}
        aria-label={`Theme: ${LABEL[mode]}. Click to switch.`}
      >
        {LABEL[mode]}
      </button>
    );
  }

  return (
    <div className="lumogis-theme-segment" role="group" aria-label="Theme">
      {ORDER.map((m) => (
        <button
          key={m}
          type="button"
          className="lumogis-theme-segment__btn"
          aria-pressed={mode === m}
          onClick={() => setMode(m)}
        >
          {LABEL[m]}
        </button>
      ))}
    </div>
  );
}
