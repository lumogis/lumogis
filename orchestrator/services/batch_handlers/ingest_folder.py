# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handler: durable folder ingest."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field

from services.batch_queue import register_batch_handler
from services.ingest import ingest_folder


class IngestFolderPayload(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)


@register_batch_handler("ingest_folder", IngestFolderPayload)
def handle(*, user_id: str, payload: IngestFolderPayload) -> None:
    ingest_folder(payload.path, user_id=user_id)
