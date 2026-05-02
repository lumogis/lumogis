# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Session-related Pydantic models shared by routes and batch handlers."""

from __future__ import annotations

from pydantic import BaseModel


class SessionMessage(BaseModel):
    role: str
    content: str


class SessionEndPayload(BaseModel):
    """Queue payload — keep aligned with ``routes/data.py::SessionEndRequest``."""

    session_id: str
    messages: list[SessionMessage]
