# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""OpenAPI snapshot drift guard.

Plan ``cross_device_lumogis_web`` Pass 0.3 step 19 — the v1 client
codegen consumes ``clients/lumogis-web/openapi.snapshot.json``. CI must
fail loudly when the live ``app.openapi()`` drifts from the committed
snapshot so contracts cannot silently regress.

Regenerate the snapshot with::

    cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys \\
        --out ../clients/lumogis-web/openapi.snapshot.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "clients" / "lumogis-web" / "openapi.snapshot.json"

# Every plan-mandated path must appear in the snapshot. Listed explicitly
# (rather than derived) so a future router refactor that accidentally
# drops a path triggers a concrete diff in this list, not a vague
# "snapshot drifted" failure.
REQUIRED_V1_PATHS = frozenset({
    # Auth (shipped, included for codegen completeness)
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/api/v1/auth/refresh",
    # New v1 web façade — plan §API routes
    "/api/v1/chat/completions",
    "/api/v1/models",
    "/api/v1/memory/search",
    "/api/v1/memory/recent",
    "/api/v1/kg/entities/{entity_id}",
    "/api/v1/kg/entities/{entity_id}/related",
    "/api/v1/kg/search",
    "/api/v1/approvals/pending",
    "/api/v1/approvals/connector/{connector}/mode",
    "/api/v1/approvals/elevate",
    "/api/v1/audit",
    "/api/v1/audit/{reverse_token}/reverse",
    "/api/v1/captures",
    "/api/v1/captures/text",
    "/api/v1/captures/upload",
    "/api/v1/captures/{capture_id}",
    "/api/v1/captures/{capture_id}/attachments",
    "/api/v1/captures/{capture_id}/attachments/{attachment_id}",
    "/api/v1/captures/{capture_id}/transcribe",
    "/api/v1/captures/{capture_id}/index",
    "/api/v1/notifications/vapid-public-key",
    "/api/v1/notifications/subscribe",
    "/api/v1/notifications/subscriptions",
    "/api/v1/notifications/subscriptions/{subscription_id}",
    "/api/v1/notifications/test",
    "/api/v1/events",
    # Shipped surfaces the plan asks the snapshot to cover (§Pass 0.3.19)
    "/api/v1/me/permissions",
    "/api/v1/me/tools",
    "/api/v1/me/llm-providers",
    "/api/v1/me/notifications",
    "/api/v1/me/connector-credentials",
    "/api/v1/me/mcp-tokens",
    "/api/v1/me/password",
    "/api/v1/admin/users",
    "/api/v1/admin/users/{user_id}/password",
    "/api/v1/admin/permissions",
    "/api/v1/admin/diagnostics",
    # Speech-to-text foundation (STT-1)
    "/api/v1/voice/transcribe",
})


@pytest.fixture(scope="module")
def live_spec() -> dict:
    """Build the OpenAPI spec from the live FastAPI app."""
    from scripts.dump_openapi import _build_openapi
    from scripts.dump_openapi import _normalise
    return _normalise(_build_openapi())


def test_openapi_snapshot_exists():
    assert SNAPSHOT_PATH.exists(), (
        f"Missing OpenAPI snapshot at {SNAPSHOT_PATH}. "
        "Generate with: cd orchestrator && python -m scripts.dump_openapi "
        "--pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json"
    )


def test_required_v1_paths_present_in_live_spec(live_spec):
    """Plan-mandated v1 paths must appear in the live OpenAPI."""
    paths = set(live_spec.get("paths") or {})
    missing = REQUIRED_V1_PATHS - paths
    assert not missing, f"OpenAPI is missing plan-required v1 paths: {sorted(missing)}"


def test_snapshot_paths_match_live_spec(live_spec):
    """Catch additions/removals/renames before they reach the SPA codegen."""
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    snapshot_paths = set(snapshot.get("paths") or {})
    live_paths = set(live_spec.get("paths") or {})

    added = live_paths - snapshot_paths
    removed = snapshot_paths - live_paths
    assert not (added or removed), (
        "OpenAPI surface drift between live app and committed snapshot.\n"
        f"  Added (new in live): {sorted(added)}\n"
        f"  Removed (gone from live): {sorted(removed)}\n"
        "If intentional, regenerate with:\n"
        "  cd orchestrator && python -m scripts.dump_openapi --pretty "
        "--sort-keys --out ../clients/lumogis-web/openapi.snapshot.json"
    )


def test_v1_audit_schema_named_AuditEntry(live_spec):
    """Plan §Data contracts pins the audit row schema name to ``AuditEntry``.

    The DTO is exposed under both ``AuditEntry`` and ``AuditEntryDTO``;
    one of them must register as a component schema so codegen can
    resolve ``AuditListResponse.audit[].$ref``.
    """
    schemas = (live_spec.get("components") or {}).get("schemas") or {}
    assert (
        "AuditEntry" in schemas or "AuditEntryDTO" in schemas
    ), "Expected AuditEntry / AuditEntryDTO schema component in OpenAPI."
