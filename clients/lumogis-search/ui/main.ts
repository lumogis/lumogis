// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

// Thin entry: boots the shared overlay factory with default (no) hooks.
// E2E builds alias this file to main.e2e.ts via vite.config.ts (VITE_WDIO_E2E=true).
import { createOverlayApp } from "./app";

void createOverlayApp().boot();
