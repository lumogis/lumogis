// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "Show developer tools" (LUM-993 Pass 3): a per-browser display choice, off by
// default, that shows the For developers group in Settings. Developer pages
// still open from a direct link when it is off. Stored like the sidebar
// choice; every storage access is wrapped, so private mode just means "off".

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "lumogis-dev-tools";
const CHANGE_EVENT = "lumogis:dev-tools";

export function getDevToolsShown(): boolean {
  try {
    if (typeof localStorage === "undefined") return false;
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setDevToolsShown(on: boolean): void {
  try {
    if (typeof localStorage !== "undefined") {
      if (on) localStorage.setItem(STORAGE_KEY, "1");
      else localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: on }));
}

/** The current choice, kept in step across components and other tabs. */
export function useDevToolsShown(): [boolean, (on: boolean) => void] {
  const [shown, setShown] = useState(getDevToolsShown);
  useEffect(() => {
    const onChange = (e: Event) => setShown(Boolean((e as CustomEvent<boolean>).detail));
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY || e.key === null) setShown(getDevToolsShown());
    };
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  const set = useCallback((on: boolean) => {
    setShown(on);
    setDevToolsShown(on);
  }, []);
  return [shown, set];
}
