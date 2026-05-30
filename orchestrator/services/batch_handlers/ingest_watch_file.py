# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handler: single-file ingest from ingest-path filesystem watcher."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field
from services.batch_queue import register_batch_handler
from services.ingest import ingest_file


class IngestWatchFilePayload(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)


@register_batch_handler("ingest_watch_file", IngestWatchFilePayload)
def handle(*, user_id: str, payload: IngestWatchFilePayload) -> None:
    ingest_file(payload.path, user_id=user_id)
