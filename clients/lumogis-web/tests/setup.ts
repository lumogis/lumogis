// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/* Polyfills IndexedDB for Vitest — must load before `idb-keyval` / draft tests. */
import "fake-indexeddb/auto";

import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

import { __resetClientStateBoundaryForTests } from "../src/auth/clientStateBoundary";

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
