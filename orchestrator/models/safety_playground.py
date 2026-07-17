# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Data contracts for the safety playground (LUM-141).

Enums live here (pure, importable by both the service and the routes without a
cycle). The service (:mod:`services.safety_playground`) owns the adversarial
suite + runner; these models are the wire shapes for ``/api/v1/admin/safety/*``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel
from pydantic import Field


class InjectionVector(str, Enum):
    """The defensive surface a case exercises (maps to a pure primitive)."""

    DOCUMENT_INGEST = "document_ingest"
    SESSION_CONTEXT = "session_context"
    TOOL_RESULT = "tool_result"
    USER_CONFIG = "user_config"
    ACTION_EXECUTION = "action_execution"


class ExpectedOutcome(str, Enum):
    """What a defence is expected to do with a payload."""

    SANITISED = "sanitised"
    FLAGGED = "flagged"
    ORIGIN_TAGGED = "origin_tagged"
    BLOCKED = "blocked"
    PASSED = "passed"  # control: the defence intentionally does not act


class SafetyCaseResult(BaseModel):
    name: str
    vector: str
    expected: str
    actual: str
    passed: bool
    known_gap: bool
    detail: str


class SafetySuiteResult(BaseModel):
    total: int
    passed: int
    failed: int  # hard failures (non-passing, NOT known_gap) — the CI gate
    warnings: int  # non-passing known_gap cases (tracked, do not red the build)
    ran_at: str  # ISO-8601, stamped by the caller (route), not the service
    results: list[SafetyCaseResult] = Field(default_factory=list)


class SafetyCaseInfo(BaseModel):
    name: str
    vector: str
    expected: str
    known_gap: bool


class SafetyCaseList(BaseModel):
    items: list[SafetyCaseInfo] = Field(default_factory=list)


class SafetyProbeRequest(BaseModel):
    vector: InjectionVector
    payload: str = Field(default="", max_length=20000)
    action_type: str = Field(default="", max_length=200)
    expected: ExpectedOutcome | None = None


class SafetyProbeResult(BaseModel):
    vector: str
    expected: str | None
    actual: str
    passed: bool | None  # None when the caller supplied no `expected`
    detail: str
