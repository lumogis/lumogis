// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

// Thin entry: boots the shared overlay factory with default (no) hooks.
import { createOverlayApp } from "./app";

void createOverlayApp().boot();
