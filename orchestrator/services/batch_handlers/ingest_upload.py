# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handler: push-ingest file uploaded via ``POST /api/v1/ingest/upload``.

The stored file is **not** deleted after ingest (dedup, re-open on server).
Failed jobs are terminal for this kind — no automatic retry.
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field
from services.batch_queue import register_batch_handler
from services.ingest import ingest_file


class IngestUploadPayload(BaseModel):
    stored_path: str = Field(..., min_length=1, max_length=4096)
    file_id: str = Field(..., min_length=1, max_length=64)
    original_filename: str | None = Field(default=None, max_length=512)


@register_batch_handler("ingest_upload", IngestUploadPayload)
def handle(*, user_id: str, payload: IngestUploadPayload) -> None:
    ingest_file(payload.stored_path, user_id=user_id)
