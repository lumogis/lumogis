// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/* Polyfills IndexedDB for Vitest — must load before `idb-keyval` / draft tests. */
import "fake-indexeddb/auto";

import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
