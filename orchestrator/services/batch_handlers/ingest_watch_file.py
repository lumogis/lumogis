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
    force: bool = False


@register_batch_handler("ingest_watch_file", IngestWatchFilePayload)
def handle(*, user_id: str, payload: IngestWatchFilePayload, job_id: int) -> None:
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
        payload.path,
        user_id=user_id,
        force=payload.force,
        on_progress=on_progress,
    )

    # LUM-157: re-mirror shared chunks after a re-ingest of a shared source.
    if result is not None and not getattr(result, "skipped", False):
        from services.projection import reproject_shared_on_reingest

        reproject_shared_on_reingest(
            user_id,
            result.file_path,
            removed_entity_ids=result.removed_document_entity_ids,
        )
