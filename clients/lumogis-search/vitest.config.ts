// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { defineConfig } from "vitest/config";

export default defineConfig({
  root: ".",
  test: {
    environment: "node",
    include: ["ui/**/*.test.ts"],
  },
});
