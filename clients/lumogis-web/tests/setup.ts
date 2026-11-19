// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/* Polyfills IndexedDB for Vitest — must load before `idb-keyval` / draft tests. */
import "fake-indexeddb/auto";

import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

import { __resetClientStateBoundaryForTests } from "../src/auth/clientStateBoundary";

// jsdom has no PointerEvent; Base UI's Switch dispatches one (LUM-993 Pass 3).
// A MouseEvent with the pointer fields is enough for the components under test.
if (typeof window !== "undefined" && typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
      this.pointerType = init.pointerType ?? "mouse";
      this.isPrimary = init.isPrimary ?? true;
    }
  }
  (window as unknown as { PointerEvent: unknown }).PointerEvent = PointerEventPolyfill;
}

afterEach(() => {
  cleanup();
  // Tests may clear storage wholesale; product code may not (LUM-886).
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch {
    /* node-environment suites have no storage */
  }
  __resetClientStateBoundaryForTests();
});
