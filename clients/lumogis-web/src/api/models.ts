// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written types for `GET /api/v1/models` (parent plan §API routes l. 708).
// Pinned against `orchestrator/models/api_v1.py::ModelDescriptor` and the
// enumeration in `orchestrator/routes/api_v1/chat.py::list_models`.

/**
 * Why a model is off (LUM-881). Mirrors `ModelDisabledReason` in
 * `orchestrator/models/api_v1.py`; `tests/api/modelsContract.test.ts` ties it
 * to `openapi.snapshot.json`. Treat any other value like an omitted field.
 */
export const MODEL_DISABLED_REASONS = ["privacy_mode", "optional_disabled", "no_credentials"] as const;
export type ModelDisabledReason = (typeof MODEL_DISABLED_REASONS)[number];

export interface ModelDescriptor {
  id: string;
  label: string;
  is_local: boolean;
  enabled: boolean;
  provider: string;
  /** `null` iff enabled. Optional: older servers and test mocks omit it. */
  disabled_reason?: ModelDisabledReason | null;
}

export interface ModelsResponse {
  models: ModelDescriptor[];
}
