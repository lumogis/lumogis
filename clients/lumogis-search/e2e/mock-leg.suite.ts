// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** Single WDIO worker for all mock-leg specs — avoids embedded driver restarts between files. */
import "./admin-ingest-paths.spec.js";
import "./login.spec.js";
import "./restart-banner.spec.js";
import "./search-session.spec.js";
