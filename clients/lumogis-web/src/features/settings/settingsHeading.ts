// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shared by SettingsPage and the views it renders (LUM-993 Pass 3). Its own
// file so a view can use it without importing SettingsPage, which imports
// settingsNav, which imports the views (no import cycle).

import { createContext, useContext, type RefObject } from "react";

export const ADMIN_ONLY_TOAST = "Only household admins can open that page.";
export const HOUSEHOLD_VIEW_TOAST = "Only household admins can see everyone's.";

/** The page title's element, for views that move focus to it (SourcesView, LUM-282). */
export const SettingsHeadingContext = createContext<RefObject<HTMLHeadingElement> | null>(null);

export function useSettingsHeading(): RefObject<HTMLHeadingElement> | null {
  return useContext(SettingsHeadingContext);
}
