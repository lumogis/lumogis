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
    force: bool = False
    batch_id: str | None = Field(default=None, max_length=64)


@register_batch_handler("ingest_upload", IngestUploadPayload)
def handle(*, user_id: str, payload: IngestUploadPayload, job_id: int) -> None:
    from services import ingest_progress as ip

    def on_progress(stage: str, pct: int | None, msg: str | None) -> None:
        ip.update_ingest_job_progress(
            job_id=job_id,
            user_id=user_id,
            stage=stage,  # type: ignore[arg-type]
            progress_pct=pct,
            status_message=msg,
        )

    result = ingest_file(
        payload.stored_path,
        user_id=user_id,
        force=payload.force,
        on_progress=on_progress,
    )

    # LUM-157: if this source has an active shared projection, re-mirror its
    # chunks so household members see the new content (never stale). Runs AFTER
    # ingest (which wiped the old chunks). Skip unchanged (skipped) re-ingests.
    if result is not None and not getattr(result, "skipped", False):
        from services.projection import reproject_shared_on_reingest

        reproject_shared_on_reingest(
            user_id,
            result.file_path,
            removed_entity_ids=result.removed_document_entity_ids,
        )
