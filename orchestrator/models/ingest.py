# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from pydantic import BaseModel
from pydantic import Field


class IngestResult(BaseModel):
    file_path: str
    chunk_count: int
    ocr_used: bool = False
    skipped: bool = False
    # LUM-281: paperless poller advances ``sources.poll_cursor`` only when True.
    advance_external_poll_cursor: bool = False
    # LUM-604: personal entity ids whose DOCUMENT relation was pruned on this
    # re-ingest (prior extraction minus current). Consumed by
    # ``reproject_shared_on_reingest`` to diff-retract doc-origin shared rows.
    removed_document_entity_ids: list[str] = Field(default_factory=list)


class IngestStats(BaseModel):
    total_files: int
    ingested: int
    skipped: int
    errors: int
