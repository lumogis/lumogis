# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/diagnostics`` — read-only admin diagnostics façade."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from models.api_v1 import MeToolsItem
from models.api_v1 import MeToolsResponse
from models.api_v1 import MeToolsSummary
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-admin-diagnostics-access-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def _ct(name: str) -> CapabilityTool:
    return CapabilityTool(
        name=name,
        description="Discovered tool",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        output_schema={"type": "object"},
    )


def _manifest(mid: str) -> CapabilityManifest:
    return CapabilityManifest(
        name=mid,
        id=mid,
        version="0.1.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="Test cap",
        tools=[_ct(f"{mid}.tool")],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="t",
    )


def test_admin_diagnostics_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-admin-diagnostics-401-secret")
    r = client.get("/api/v1/admin/diagnostics")
    assert r.status_code == 401


def test_admin_diagnostics_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 403


def test_admin_diagnostics_200_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "generated_at" in body
    assert "core" in body
    assert body["core"]["auth_enabled"] is True
    assert "tool_catalog_enabled" in body["core"]
    assert body["core"]["core_version"]
    assert "mcp_enabled" in body["core"]
    assert "mcp_auth_required" in body["core"]
    names = [s["name"] for s in body["stores"]]
    assert names == ["postgres", "qdrant", "embedder", "graph"]
    cap = body["capabilities"]
    assert "total" in cap and "healthy" in cap and "unhealthy" in cap
    assert isinstance(cap["services"], list)
    tools = body["tools"]
    assert tools["total"] == tools["available"] + tools["unavailable"]
    assert isinstance(tools["by_source"], dict)
    assert isinstance(body["warnings"], list)
    assert len(body["warnings"]) >= 1
    fs = body["foundation_signals"]
    assert fs["tool_catalog"]["total_entries"] >= 0
    assert isinstance(fs["tool_catalog"]["entries_by_transport"], dict)
    assert isinstance(fs["tool_catalog"]["unavailable_entries_by_source"], dict)
    assert isinstance(fs["permissions"]["ask_do_module_import_ok"], bool)
    assert isinstance(fs["permissions"]["connector_mode_metadata_lookup_ok"], bool)
    assert fs["capability_registry"]["registered_services_total"] == cap["total"]
    st = body["speech_to_text"]
    assert "backend" in st and st["backend"] in ("none", "fake_stt", "whisper_sidecar")
    assert "transcribe_available" in st
    assert isinstance(st["max_audio_bytes"], int)
    assert isinstance(st["max_duration_sec"], int)
    assert st["endpoint"] == "/api/v1/voice/transcribe"


def test_admin_diagnostics_200_when_auth_disabled_default_user(client, monkeypatch) -> None:
    # VERIFY-PLAN: docker compose run loads repo .env — local smoke may set AUTH_ENABLED=true;
    # this test asserts the dev-mode façade; do not inherit host env.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    r = client.get("/api/v1/admin/diagnostics")
    assert r.status_code == 200
    assert r.json()["core"]["auth_enabled"] is False


def test_admin_diagnostics_safe_json_no_secret_like_keys(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin-2", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    forbidden = frozenset(
        {
            "api_key",
            "password",
            "ciphertext",
            "access_token",
            "refresh_token",
            "authorization",
            "bearer",
            "payload",
            "private_key",
        }
    )

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                assert kl not in forbidden
                assert not kl.endswith("_secret")
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)

    walk(body)
    raw = json.dumps(body)
    assert "BEGIN RSA" not in raw
    assert "sk-" not in raw  # common key prefix pattern

    # LUM-877 (T-D5): the tasks block carries aliases and reasons only.
    from models.api_v1 import AdminDiagnosticsTask

    import config

    assert body["tasks"], "tasks block missing"
    expected_keys = set(AdminDiagnosticsTask.model_fields)
    for row in body["tasks"]:
        assert set(row) == expected_keys
        assert not {"base_url", "proxy_url", "api_key_env"} & set(row)
    secret_values = {
        v
        for cfg in config.get_all_models_config().values()
        for k, v in cfg.items()
        if k in ("base_url", "api_key_env", "proxy_url") and v
    }
    strings: list[str] = []

    def collect(obj: object) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                collect(v)
        elif isinstance(obj, list):
            for i in obj:
                collect(i)
        elif isinstance(obj, str):
            strings.append(obj)

    collect(body["tasks"])
    assert not (set(strings) & secret_values)


def test_admin_diagnostics_capability_summary_counts(client, monkeypatch) -> None:
    fixed = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)

    class _Reg:
        def all_services(self):
            return [
                RegisteredService(
                    manifest=_manifest("svc-healthy"),
                    base_url="http://h:1",
                    registered_at=fixed,
                    healthy=True,
                    last_seen_healthy=fixed,
                ),
                RegisteredService(
                    manifest=_manifest("svc-sick"),
                    base_url="http://s:1",
                    registered_at=fixed,
                    healthy=False,
                ),
            ]

    monkeypatch.setattr(
        "services.admin_diagnostics.config.get_capability_registry",
        lambda: _Reg(),
    )

    hdr = _auth_header(monkeypatch, "admin-cap", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    cap = r.json()["capabilities"]
    assert cap["total"] == 2
    assert cap["healthy"] == 1
    assert cap["unhealthy"] == 1
    ids = [s["id"] for s in cap["services"]]
    assert ids == ["svc-healthy", "svc-sick"]
    assert cap["services"][0]["status"] == "healthy"
    assert cap["services"][0]["healthy"] is True
    assert cap["services"][1]["status"] == "unhealthy"
    assert cap["services"][1]["healthy"] is False


def test_admin_diagnostics_tools_summary_from_catalog_stub(client, monkeypatch) -> None:
    summary = SimpleNamespace(
        total=5,
        available=4,
        unavailable=1,
        by_source={"capability": 1, "core": 3, "mcp": 1},
    )

    def _fake_build(uid: str):
        assert uid == "admin-tools"
        return SimpleNamespace(summary=summary, tools=[])

    monkeypatch.setattr(
        "services.admin_diagnostics.me_tools_catalog_svc.build_me_tools_response",
        _fake_build,
    )
    hdr = _auth_header(monkeypatch, "admin-tools", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    t = r.json()["tools"]
    assert t["total"] == 5
    assert t["available"] == 4
    assert t["unavailable"] == 1
    assert t["by_source"] == {"capability": 1, "core": 3, "mcp": 1}
    assert r.json()["foundation_signals"]["tool_catalog"]["total_entries"] == 5


def test_admin_diagnostics_foundation_warns_on_unhealthy_capabilities(client, monkeypatch) -> None:
    fixed = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)

    class _Reg:
        def all_services(self):
            return [
                RegisteredService(
                    manifest=_manifest("svc-down"),
                    base_url="http://x:1",
                    registered_at=fixed,
                    healthy=False,
                ),
            ]

    monkeypatch.setattr(
        "services.admin_diagnostics.config.get_capability_registry",
        lambda: _Reg(),
    )
    hdr = _auth_header(monkeypatch, "admin-foundation-cap", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    codes = [w["code"] for w in r.json()["warnings"]]
    assert "capability_services_unhealthy" in codes


def test_admin_diagnostics_foundation_warns_connector_permission_unknown_row(
    client, monkeypatch
) -> None:
    resp = MeToolsResponse(
        tools=[
            MeToolsItem(
                name="probe_perm",
                label="Probe perm",
                source="plugin",
                transport="llm_loop",
                origin_tier="plugin",
                available=True,
                connector="ntfy",
                permission_mode="unknown",
            ),
        ],
        summary=MeToolsSummary(
            total=1,
            available=1,
            unavailable=0,
            by_source={"plugin": 1},
        ),
    )

    def _fake_build(uid: str) -> MeToolsResponse:
        assert uid == "admin-found-perm"
        return resp

    monkeypatch.setattr(
        "services.admin_diagnostics.me_tools_catalog_svc.build_me_tools_response",
        _fake_build,
    )
    hdr = _auth_header(monkeypatch, "admin-found-perm", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    fs = r.json()["foundation_signals"]
    assert fs["permissions"]["catalog_rows_with_connector_but_unknown_permission_mode"] == 1
    codes = [w["code"] for w in r.json()["warnings"]]
    assert "tool_catalog_permission_mode_unknown" in codes


# --- LUM-877: background model tasks ---------------------------------------


def _tasks_get(client, monkeypatch, user="admin-t"):
    hdr = _auth_header(monkeypatch, user, "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    return r.json()


def _task_setup(monkeypatch, *, models=None, tasks=None, dynamic=None, policy="allow_cloud"):
    from tests._task_models_support import install
    from tests._task_models_support import set_policy

    install(monkeypatch, models=models, tasks=tasks, dynamic=dynamic)
    set_policy(monkeypatch, policy)


def test_admin_diagnostics_tasks_default_config(client, monkeypatch) -> None:
    from services import task_models

    _task_setup(monkeypatch)
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: True)
    body = _tasks_get(client, monkeypatch)
    rows = body["tasks"]
    assert [r["task"] for r in rows] == list(task_models.TASKS)
    for r in rows:
        assert r["configured"] == []
        assert r["resolved_from"] == "default"
        # LUM-927: the temporal judge default resolves too.
        assert r["resolved_model"] == "llama"
        assert r["default_skip_reason"] is None


def test_admin_diagnostics_tasks_reasons(client, monkeypatch) -> None:
    from tests._task_models_support import CLOUD

    _task_setup(
        monkeypatch, models={"claude": dict(CLOUD)}, tasks={"email_classify": ["claude", "llama"]}
    )
    body = _tasks_get(client, monkeypatch)
    row = next(r for r in body["tasks"] if r["task"] == "email_classify")
    assert row["configured"] == [
        {"model": "claude", "skip_reason": "not_on_box"},
        {"model": "llama", "skip_reason": None},
    ]
    assert row["resolved_model"] == "llama" and row["resolved_from"] == "list"


def test_admin_diagnostics_local_only_offbox_default_reads_null(client, monkeypatch) -> None:
    from services import task_models

    _task_setup(monkeypatch)
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: False)
    by_task = {r["task"]: r for r in _tasks_get(client, monkeypatch)["tasks"]}
    for name in ("email_classify", "email_reply_draft", "email_compose_refine"):
        assert by_task[name]["resolved_model"] is None
        assert by_task[name]["resolved_from"] == "default"
    for name in ("entity_extraction", "routine_weekly_review"):
        assert by_task[name]["resolved_model"] == "llama"


def test_admin_diagnostics_tasks_failure_degrades(client, monkeypatch) -> None:
    from services import task_models

    _task_setup(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("explain failed")

    monkeypatch.setattr(task_models, "explain_task", boom)
    body = _tasks_get(client, monkeypatch)
    assert body["tasks"] == []
    assert "task_models_unavailable" in [w["code"] for w in body["warnings"]]


@pytest.mark.parametrize(
    "tasks,on_box",
    [
        ({}, True),
        ({"email_classify": ["lan"]}, True),
        ({"email_classify": ["claude"]}, True),
        ({"email_classify": ["lan"]}, False),
        ({"entity_extraction": ["claude", "llama"]}, True),
    ],
)
def test_admin_diagnostics_email_rows_match_resolve_on_box_model(
    client, monkeypatch, tasks, on_box
) -> None:
    from tests._task_models_support import CLOUD
    from tests._task_models_support import LLAMA

    from services import task_models

    lan = dict(LLAMA, base_url="http://gpu.lan:11434/ollama/v1")
    _task_setup(monkeypatch, models={"claude": dict(CLOUD), "lan": lan}, tasks=tasks)
    monkeypatch.setattr(task_models, "is_on_box_local", lambda name, **kw: on_box)
    by_task = {r["task"]: r for r in _tasks_get(client, monkeypatch, "admin-m")["tasks"]}
    for name, spec in task_models.TASKS.items():
        if spec.email_boundary:
            want = task_models.resolve_on_box_model(name, user_id="admin-m")
        elif spec.local_only:
            want = task_models.resolve_task_model(name, user_id="admin-m")
        else:
            continue
        assert by_task[name]["resolved_model"] == want, name


def test_admin_diagnostics_lists_ollama_once(client, monkeypatch) -> None:
    import ollama_client
    from tests._task_models_support import install
    from tests._task_models_support import set_policy

    install(
        monkeypatch,
        tasks={"session_summary": ["qwen"], "relation_extraction": ["phi"]},
        real_dynamic=True,
    )
    set_policy(monkeypatch, "allow_cloud")
    calls: list[int] = []
    monkeypatch.setattr(
        ollama_client,
        "list_local_models",
        lambda *a, **k: calls.append(1) or [{"name": "qwen:7b"}, {"name": "phi:3b"}],
    )
    before = len(calls)
    from services import admin_diagnostics

    rows, warns = admin_diagnostics._tasks_block("admin-o")
    assert warns == [] and len(rows) == 10
    assert len(calls) - before == 1
