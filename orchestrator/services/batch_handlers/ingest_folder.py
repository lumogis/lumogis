# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handler: durable folder ingest."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field
from services.batch_queue import register_batch_handler
from services.ingest import ingest_folder
from services.path_containment import _resolved_path_under_any_root

import config

_log = logging.getLogger(__name__)


class IngestFolderPayload(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)


class IngestFolderRejected(ValueError):
    """The job's path or user is not allowed to start a library scan."""


@register_batch_handler("ingest_folder", IngestFolderPayload)
def handle(*, user_id: str, payload: IngestFolderPayload, job_id: int) -> None:
    """Scan a library folder, after re-validating the job (LUM-884).

    Second line of defence behind the admin-only Rescan route, so the queue
    cannot be fed another way. The path must sit under an explicitly configured
    library folder (the implicit ``$HOME`` fallback never qualifies) and, when a
    library owner is configured, the job must belong to that owner. This also
    applies to jobs queued before the upgrade. A rejected job raises before any
    file is read; the queue then retries it (failing fast each time) until it is
    ``dead``. The batch queue has no permanent-failure type for jobs.
    """
    resolved = Path(payload.path).expanduser().resolve(strict=False)
    roots = config.get_configured_ingest_paths()
    owner = config.get_ingest_paths_owner_user_id()
    if not _resolved_path_under_any_root(resolved, roots) or (owner and user_id != owner):
        _log.error(
            "ingest_folder job rejected job_id=%s user_id=%s path=%s",
            job_id,
            user_id,
            payload.path,
        )
        raise IngestFolderRejected(
            "ingest_folder job rejected: path is outside the configured library "
            "folders or the user is not the library owner"
        )
    ingest_folder(payload.path, user_id=user_id)
