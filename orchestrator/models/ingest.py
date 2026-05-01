# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from pydantic import BaseModel


class IngestResult(BaseModel):
    file_path: str
    chunk_count: int
    ocr_used: bool = False
    skipped: bool = False


class IngestStats(BaseModel):
    total_files: int
    ingested: int
    skipped: int
    errors: int
