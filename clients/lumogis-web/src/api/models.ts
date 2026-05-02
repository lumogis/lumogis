// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written types for `GET /api/v1/models` (parent plan §API routes l. 708).
// Pinned against `orchestrator/models/api_v1.py:69–80` and the shipped
// `orchestrator/routes/api_v1/chat.py:183–200` enumeration logic.

export interface ModelDescriptor {
  id: string;
  label: string;
  is_local: boolean;
  enabled: boolean;
  provider: string;
}

export interface ModelsResponse {
  models: ModelDescriptor[];
}
