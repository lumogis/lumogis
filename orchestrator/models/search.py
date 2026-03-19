# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
from pydantic import BaseModel


class SearchRequest(BaseModel):
    q: str
    limit: int = 5


class SearchResult(BaseModel):
    file_path: str
    score: float
    chunk_text: str
    metadata: dict = {}
