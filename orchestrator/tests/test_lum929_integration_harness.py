# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Offline tests for the LUM-929 live-lane harness (no Docker, no network).

Covers ``tests/integration/_smoke_auth.py``, ``scripts/junit_skip_guard.py``,
the ``integration-public-rc.sh`` preflight/refusals, the parity helpers and
compose overlays, and ``diff_snapshots.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
INTEG = REPO / "tests" / "integration"
SCRIPT = REPO / "scripts" / "integration-public-rc.sh"
GUARD = REPO / "scripts" / "junit_skip_guard.py"
DIFF = INTEG / "diff_snapshots.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sa = _load("lum929_smoke_auth", INTEG / "_smoke_auth.py")
tgp = _load("lum929_graph_parity", INTEG / "test_graph_parity.py")

BASE = "http://core.test"


@pytest.fixture(autouse=True)
def _smoke_env(monkeypatch):
    monkeypatch.setenv("LUMOGIS_WEB_SMOKE_EMAIL", "smoke@example.com")
    monkeypatch.setenv("LUMOGIS_WEB_SMOKE_PASSWORD", "ChangeMe12345!")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)


def _transport(handler):
    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- _smoke_auth


def test_open_api_client_auth_off_returns_plain_client():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    client, reason = sa.open_api_client(BASE, transport=_transport(handler))
    assert client is not None and reason is None
    assert not any(r.url.path == "/api/v1/auth/login" for r in seen)


def test_open_api_client_auth_on_logs_in_and_sets_bearer():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "t1"})
        if req.headers.get("authorization") == "Bearer t1":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401)

    client, reason = sa.open_api_client(BASE, transport=_transport(handler))
    assert client is not None and reason is None
    client.get("/health")
    assert seen[-1].headers["authorization"] == "Bearer t1"
    login = next(r for r in seen if r.url.path == "/api/v1/auth/login")
    assert login.headers["origin"] == "http://127.0.0.1"


def test_open_api_client_auth_on_without_creds_returns_skip_reason(monkeypatch):
    monkeypatch.delenv("LUMOGIS_WEB_SMOKE_EMAIL")
    monkeypatch.delenv("LUMOGIS_WEB_SMOKE_PASSWORD")
    client, reason = sa.open_api_client(BASE, transport=_transport(lambda r: httpx.Response(401)))
    assert client is None
    assert "LUMOGIS_WEB_SMOKE_EMAIL" in reason


def test_open_api_client_bad_creds_fails_not_skips():
    with pytest.raises(AssertionError):
        sa.open_api_client(BASE, transport=_transport(lambda r: httpx.Response(401)))


def _refresh_transport(always_401: bool):
    calls = {"login": 0}

    def handler(req):
        if req.url.path == "/api/v1/auth/login":
            calls["login"] += 1
            return httpx.Response(200, json={"access_token": f"t{calls['login']}"})
        if not always_401 and req.headers.get("authorization") == "Bearer t2":
            return httpx.Response(200)
        return httpx.Response(401)

    return calls, _transport(handler)


def test_smoke_bearer_auth_relogs_once_on_401():
    calls, transport = _refresh_transport(always_401=False)
    client = httpx.Client(
        base_url=BASE, transport=transport, auth=sa.SmokeBearerAuth(BASE, transport=transport)
    )
    assert client.get("/x").status_code == 200
    assert calls["login"] == 2


def test_smoke_bearer_auth_does_not_loop():
    calls, transport = _refresh_transport(always_401=True)
    client = httpx.Client(
        base_url=BASE, transport=transport, auth=sa.SmokeBearerAuth(BASE, transport=transport)
    )
    assert client.get("/x").status_code == 401
    assert calls["login"] == 2


def test_login_access_token_returns_none_on_503():
    assert sa.login_access_token(BASE, transport=_transport(lambda r: httpx.Response(503))) is None


def test_smoke_credentials_rejects_short_password(monkeypatch):
    monkeypatch.setenv("LUMOGIS_WEB_SMOKE_PASSWORD", "x" * 11)
    assert sa.smoke_credentials() is None
    monkeypatch.setenv("LUMOGIS_WEB_SMOKE_PASSWORD", "x" * 12)
    assert sa.smoke_credentials() == ("smoke@example.com", "x" * 12)


def test_workspace_inbox_dir_resolves_relative_against_repo_root(tmp_path):
    root = tmp_path / "repo"
    rel = sa.workspace_inbox_dir(root, {"WORKSPACE_HOST_DIR": "./ai-workspace/workspace-test"})
    assert rel == root / "ai-workspace/workspace-test" / "inbox"
    assert sa.workspace_inbox_dir(root, {"WORKSPACE_HOST_DIR": "/tmp/x"}) == Path("/tmp/x/inbox")
    assert sa.workspace_inbox_dir(root, {}) == root / "ai-workspace" / "inbox"


def test_llm_wait_s_env_override(monkeypatch):
    monkeypatch.setenv("LUMOGIS_INTEG_LLM_TIMEOUT_S", "300")
    assert sa.llm_wait_s(90) == 300.0
    monkeypatch.delenv("LUMOGIS_INTEG_LLM_TIMEOUT_S")
    assert sa.llm_wait_s(90) == 90.0


def test_current_user_id_reads_auth_me():
    ok = httpx.Client(
        base_url=BASE, transport=_transport(lambda r: httpx.Response(200, json={"id": "u-1"}))
    )
    assert sa.current_user_id(ok) == "u-1"
    bad = httpx.Client(base_url=BASE, transport=_transport(lambda r: httpx.Response(401)))
    with pytest.raises(AssertionError):
        sa.current_user_id(bad)


def test_file_index_row_uses_bound_parameter():
    recorded = {}

    class Cur:
        def __init__(self, row):
            self._row = row

        def execute(self, sql, params):
            recorded["sql"], recorded["params"] = sql, params

        def fetchone(self):
            return self._row

    class Conn:
        def __init__(self, row):
            self._row = row

        def cursor(self):
            return Cur(self._row)

        def close(self):
            recorded["closed"] = True

    row = sa.file_index_row("graphinteg_ab", connect=lambda **kw: Conn((True, "u-1")))
    assert row == {"projected": True, "user_id": "u-1"}
    assert recorded["params"] == ("graphinteg_ab",)
    assert "position(%s in file_path)" in recorded["sql"]
    assert "LIKE" not in recorded["sql"].upper()
    assert "graphinteg" not in recorded["sql"]
    assert recorded["closed"]
    assert sa.file_index_row("x", connect=lambda **kw: Conn(None)) is None


# --------------------------------------------------------------------------- junit guard


def _junit(path: Path, cases: list[str], root: str = "testsuites") -> Path:
    body = f'<testsuite name="s" tests="{len(cases)}">{"".join(cases)}</testsuite>'
    path.write_text(f"<{root}>{body}</{root}>" if root == "testsuites" else body, encoding="utf-8")
    return path


def _guard(path, *extra):
    return subprocess.run(
        [sys.executable, str(GUARD), str(path), *extra], capture_output=True, text=True
    )


def _case(name="t", skipped: str | None = None) -> str:
    inner = f'<skipped message="{skipped}"/>' if skipped is not None else ""
    return f'<testcase classname="mod.Cls" name="{name}">{inner}</testcase>'


def test_junit_guard_zero_skips_passes(tmp_path):
    p = _junit(tmp_path / "j.xml", [_case("a"), _case("b"), _case("c")])
    r = _guard(p, "--min-tests", "1")
    assert r.returncode == 0
    assert "skipped=0" in r.stdout


def test_junit_guard_skip_fails_and_names_test(tmp_path):
    p = _junit(tmp_path / "j.xml", [_case("a", skipped="inbox watcher not running")])
    r = _guard(p)
    assert r.returncode == 1
    assert "mod.Cls::a" in r.stdout and "inbox watcher not running" in r.stdout


def test_junit_guard_too_few_tests_fails(tmp_path):
    p = _junit(tmp_path / "j.xml", [_case("a"), _case("b")])
    assert _guard(p, "--min-tests", "23").returncode == 1


def test_junit_guard_bare_testsuite_and_testsuites_root(tmp_path):
    cases = [_case("a"), _case("b")]
    a = _guard(_junit(tmp_path / "a.xml", cases, root="testsuites"))
    b = _guard(_junit(tmp_path / "b.xml", cases, root="testsuite"))
    assert a.returncode == b.returncode == 0
    assert a.stdout == b.stdout and "tests=2" in a.stdout


def test_junit_guard_missing_or_bad_file_exit_2(tmp_path):
    assert _guard(tmp_path / "nope.xml").returncode == 2
    bad = tmp_path / "bad.xml"
    bad.write_text("<not xml", encoding="utf-8")
    assert _guard(bad).returncode == 2


# --------------------------------------------------------------------------- shell script


def _scratch_env(**extra: str) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), **extra}


def test_preflight_ports_exclude_daily_qdrant():
    def ports(**extra):
        r = subprocess.run(
            [str(SCRIPT), "print-preflight-ports"],
            capture_output=True,
            text=True,
            env=_scratch_env(**extra),
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.split()

    assert ports() == ["8000", "6335"]
    assert "6334" not in ports() and "6333" not in ports()
    assert ports(ORCHESTRATOR_HOST_PORT="18000")[0] == "18000"


def _layout(tmp_path: Path, *, dot_env: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "config").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "integration-public-rc.sh")
    shutil.copy(REPO / "scripts" / "rc_test_env_defaults.py", root / "scripts")
    shutil.copy(REPO / "config" / "test.env.example", root / "config" / "test.env.example")
    if dot_env == "template":
        shutil.copy(REPO / "config" / "test.env.example", root / ".env")
    elif dot_env is not None:
        (root / ".env").write_text(dot_env, encoding="utf-8")
    return root


def _shims(tmp_path: Path, docker_ps_output: str = "") -> tuple[Path, Path]:
    stub = tmp_path / "stub"
    stub.mkdir()
    log = tmp_path / "shim.log"
    (stub / "docker").write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\n'
        f'if [ "$1" = ps ]; then printf "%s\\n" "{docker_ps_output}"; fi\n',
        encoding="utf-8",
    )
    (stub / "pip").write_text(f'#!/bin/sh\necho "pip $@" >> "{log}"\n', encoding="utf-8")
    for f in stub.iterdir():
        f.chmod(0o755)
    return stub, log


def _run_lane(root: Path, stub: Path, sub: str, **env):
    return subprocess.run(
        [str(root / "scripts" / "integration-public-rc.sh"), sub],
        capture_output=True,
        text=True,
        # python3 comes from /usr/bin on a host and on CI. The orchestrator image
        # (compose-test, verify-public-rc) has none there, only /usr/local/bin, so the
        # running interpreter's folder is a LAST resort: first, CI's toolcache Python
        # would fail without the LD_LIBRARY_PATH this scratch env drops.
        env=_scratch_env(PATH=f"{stub}:/usr/bin:/bin:{Path(sys.executable).parent}", **env),
    )


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"COMPOSE_PROJECT_NAME": "lumogis-dev"}, "COMPOSE_PROJECT_NAME"),
        ({"AUTH_ENABLED": "false"}, "AUTH_ENABLED"),
    ],
    ids=["non_rc_project", "auth_off"],
)
def test_ingest_live_refuses_before_any_venv_or_docker(tmp_path, overrides, message):
    root = _layout(tmp_path)
    template = (REPO / "config" / "test.env.example").read_text(encoding="utf-8")
    lines = [ln for ln in template.splitlines() if ln.split("=", 1)[0] not in overrides] + [
        f"{k}={v}" for k, v in overrides.items()
    ]
    env_file = tmp_path / "custom.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stub, log = _shims(tmp_path)
    r = _run_lane(root, stub, "ingest-live-pytest", INTEGRATION_ENV_FILE=str(env_file))
    assert r.returncode == 2, r.stderr
    assert message in r.stderr
    assert not log.exists(), log.read_text()
    assert not (root / ".venv").exists()


def test_ingest_live_preflight_refuses_daily_env(tmp_path):
    root = _layout(tmp_path, dot_env="COMPOSE_PROJECT_NAME=lumogis-dev\nAUTH_SECRET=real-secret\n")
    before = (root / ".env").read_bytes()
    stub, _ = _shims(tmp_path)
    r = _run_lane(root, stub, "ingest-live-preflight")
    assert r.returncode == 2
    assert "not the RC test template" in r.stderr
    assert not re.search(r"\brm\b|delete", r.stderr, re.IGNORECASE)
    assert (root / ".env").read_bytes() == before
    assert "real-secret" not in r.stderr + r.stdout


def test_ingest_live_preflight_refuses_foreign_project_owner(tmp_path):
    root = _layout(tmp_path, dot_env="template")
    stub, _ = _shims(tmp_path, docker_ps_output="/some/other/worktree")
    r = _run_lane(root, stub, "ingest-live-preflight")
    assert r.returncode == 2
    assert "/some/other/worktree" in r.stderr

    root2 = _layout(tmp_path / "two", dot_env="template")
    stub2, _ = _shims(tmp_path / "two", docker_ps_output=str(root2.resolve()))
    ok = _run_lane(root2, stub2, "ingest-live-preflight")
    assert ok.returncode == 0, ok.stderr


def test_ingest_live_preflight_does_not_start_anything(tmp_path):
    root = _layout(tmp_path, dot_env="template")
    stub, log = _shims(tmp_path, docker_ps_output=str(root.resolve()))
    r = _run_lane(root, stub, "ingest-live-preflight")
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 1 and calls[0].startswith("ps")


# --------------------------------------------------------------------------- parity helpers


def _fake_run(monkeypatch, stdout="", returncode=0):
    argvs = []

    def fake(cmd, **kw):
        argvs.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    return argvs


def test_parity_compose_always_pins_project(monkeypatch, tmp_path):
    argvs = _fake_run(monkeypatch)
    env = tgp._compose_env(tgp.SERVICE_OVERLAY, "service", tmp_path)
    tgp._compose_down(env)
    tgp._compose_up(env, include_premium=True)
    assert len(argvs) == 2
    for argv in argvs:
        assert argv[:4] == ["docker", "compose", "-p", "lumogis-parity"]


def test_parity_env_ignores_rc_api_url_and_pins_ports(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMOGIS_API_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "lumogis-dev")
    monkeypatch.setenv("LUMOGIS_KG_URL", "http://127.0.0.1:8001")
    for key in ("CORE", "QDRANT", "FALKORDB", "KG"):
        monkeypatch.delenv(f"LUMOGIS_PARITY_{key}_PORT", raising=False)
    env = tgp._compose_env(tgp.SERVICE_OVERLAY, "service", tmp_path)
    assert env["COMPOSE_PROJECT_NAME"] == "lumogis-parity"
    assert env["ORCHESTRATOR_HOST_PORT"] == "18000"
    assert env["FALKORDB_HOST_PORT"] == "16380"
    for key in ("WORKSPACE_HOST_DIR", "FILESYSTEM_ROOT", "BACKUP_HOST_DIR"):
        assert Path(env[key]).is_relative_to(tmp_path)
    assert tgp.CORE_URL == "http://127.0.0.1:18000"
    assert tgp.KG_URL == "http://127.0.0.1:18011"


def test_parity_overlay_sets_mode_auth_and_graph_mount():
    doc = yaml.safe_load((REPO / "docker-compose.parity.yml").read_text(encoding="utf-8"))
    env = doc["services"]["orchestrator"]["environment"]
    assert env["AUTH_ENABLED"] == "false"
    assert env["GRAPH_MODE"].startswith("${GRAPH_MODE")
    assert env["GRAPH_BACKEND"] == "falkordb"
    assert env["INBOX_OWNER_USER_ID"] == ""
    assert (
        "./services/lumogis-graph:/services/lumogis-graph:ro"
        in (doc["services"]["orchestrator"]["volumes"])
    )
    vol = doc["volumes"]["parity_ollama"]
    assert vol == {"external": True, "name": "lumogis-parity-ollama"}


def test_parity_premium_kg_port_loopback_and_overridable():
    prem = yaml.safe_load((REPO / "docker-compose.parity-premium.yml").read_text(encoding="utf-8"))
    kg = prem["services"]["lumogis-graph"]
    assert kg["ports"] == ["127.0.0.1:${KG_HOST_PORT:-8001}:8001"]
    assert kg["environment"]["POSTGRES_HOST"] == "postgres"
    core = yaml.safe_load((REPO / "docker-compose.parity.yml").read_text(encoding="utf-8"))
    cenv = core["services"]["orchestrator"]["environment"]
    assert cenv["POSTGRES_HOST"] == "postgres" and cenv["POSTGRES_PORT"] == "5432"


def test_diff_snapshots_vacuous_input_fails(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for p in (a, b):
        p.write_text(json.dumps({"duplicate_candidate_count": 0}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(DIFF), str(a), str(b)], capture_output=True, text=True)
    assert r.returncode == 1
    assert "VACUOUS" in r.stdout + r.stderr


def _diff(tmp_path, left: dict, right: dict) -> int:
    a, b = tmp_path / "l.json", tmp_path / "r.json"
    a.write_text(json.dumps(left), encoding="utf-8")
    b.write_text(json.dumps(right), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(DIFF), str(a), str(b)], capture_output=True
    ).returncode


def test_diff_snapshots_compares_graphs_field(tmp_path):
    base = {"nodes": 1, "edges": 0}
    assert _diff(tmp_path, {**base, "graphs": ["a"]}, {**base, "graphs": ["b"]}) == 1
    assert _diff(tmp_path, {**base, "graphs": ["a"]}, {**base, "graphs": ["a"]}) == 0


SNAP = {
    "graphs": ["g"],
    "nodes": 3,
    "edges": 1,
    "nodes_by_label": {"Document": 1, "Person": 2},
    "edges_by_type": {"MENTIONS": 1},
}


def test_parity_snapshot_runs_in_pinned_orchestrator(monkeypatch, tmp_path):
    argvs = _fake_run(monkeypatch, stdout=json.dumps(SNAP))
    env = tgp._compose_env(tgp.SERVICE_OVERLAY, "service", tmp_path)
    assert tgp._snapshot_falkordb(env) == SNAP
    assert argvs[0][:9] == [
        "docker",
        "compose",
        "-p",
        "lumogis-parity",
        "exec",
        "-T",
        "orchestrator",
        "python",
        "-c",
    ]
    _fake_run(monkeypatch, stdout="not json")
    with pytest.raises(AssertionError):
        tgp._snapshot_falkordb(env)


def test_parity_stamp_query_is_parameterised(monkeypatch, tmp_path):
    argvs = _fake_run(monkeypatch, stdout="none\n")
    env = tgp._compose_env(tgp.SERVICE_OVERLAY, "service", tmp_path)
    evil = "/fixtures/x'; DROP TABLE file_index;--"
    assert tgp._file_index_projected(env, evil) is None
    assert argvs[0][-1] == evil and argvs[0][-2] == tgp._STAMP_PY
    assert "%s" in tgp._STAMP_PY and evil not in tgp._STAMP_PY


def test_wait_for_projection_requires_entity_label_and_stability():
    doc_only = {**SNAP, "nodes_by_label": {"Document": 1}}
    calls = []

    def snapshot(env):
        calls.append(1)
        return doc_only if len(calls) <= 3 else SNAP

    now = [0.0]
    result = tgp._wait_for_projection(
        {},
        600,
        snapshot=snapshot,
        clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + 10),
    )
    assert result is SNAP and len(calls) == 7  # 3 document-only + 4 identical with an entity

    now[0] = 0.0
    with pytest.raises(AssertionError):
        tgp._wait_for_projection(
            {},
            600,
            snapshot=lambda env: doc_only,
            clock=lambda: now[0],
            sleep=lambda s: now.__setitem__(0, now[0] + 10),
        )


def _fake_falkordb(has_list_graphs: bool, record: dict):
    class Result:
        def __init__(self, rows):
            self.result_set = rows

    class Graph:
        def query(self, q):
            if "labels(n)" in q:
                return Result([["Document", 1], ["Person", 2]])
            return Result([["MENTIONS", 3]])

    class Conn:
        def execute_command(self, cmd):
            record["fallback"] = cmd
            return [b"g1", b"g2"]

    class FalkorDB:
        def __init__(self, host=None, port=None):
            record["args"] = (host, port)
            self.connection = Conn()

        def select_graph(self, name):
            return Graph()

    if has_list_graphs:
        FalkorDB.list_graphs = lambda self: ["g2", "g1"]
    mod = types.ModuleType("falkordb")
    mod.FalkorDB = FalkorDB
    return mod


@pytest.mark.parametrize("has_list_graphs", [True, False])
def test_snapshot_program_runs_against_fake_falkordb(monkeypatch, capsys, has_list_graphs):
    assert "from_url" not in tgp._SNAPSHOT_PY
    compile(tgp._SNAPSHOT_PY, "<snap>", "exec")
    store = _load("lum929_falkordb_store", REPO / "orchestrator" / "adapters" / "falkordb_store.py")
    pkg = types.ModuleType("adapters")
    pkg.__path__ = []
    record: dict = {}
    monkeypatch.setitem(sys.modules, "falkordb", _fake_falkordb(has_list_graphs, record))
    monkeypatch.setitem(sys.modules, "adapters", pkg)
    monkeypatch.setitem(sys.modules, "adapters.falkordb_store", store)
    monkeypatch.setenv("FALKORDB_URL", "redis://falkordb:6379")
    exec(compile(tgp._SNAPSHOT_PY, "<snap>", "exec"), {})  # noqa: S102 — fixed constant
    out = json.loads(capsys.readouterr().out)
    assert record["args"] == ("falkordb", 6379)
    assert out["graphs"] == ["g1", "g2"]
    assert out["nodes"] == 6 and out["edges"] == 6
    assert out["nodes_by_label"] == {"Document": 2, "Person": 4}
    assert ("fallback" in record) is (not has_list_graphs)


def test_deterministic_view_ignores_entity_counts(tmp_path):
    a = SNAP
    b = {**SNAP, "nodes_by_label": {"Document": 1, "Person": 9, "Org": 4}, "edges": 40}
    assert tgp._deterministic_view(a) == tgp._deterministic_view(b)
    assert _diff(tmp_path, tgp._deterministic_view(a), tgp._deterministic_view(b)) == 0
    c = {**SNAP, "nodes_by_label": {"Document": 2, "Person": 2}}
    assert _diff(tmp_path, tgp._deterministic_view(a), tgp._deterministic_view(c)) == 1
    d = {**SNAP, "graphs": ["other"]}
    assert _diff(tmp_path, tgp._deterministic_view(a), tgp._deterministic_view(d)) == 1


def test_parity_checkout_guard(tmp_path):
    (tmp_path / "config").mkdir()
    template = REPO / "config" / "test.env.example"
    shutil.copy(template, tmp_path / "config" / "test.env.example")

    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=lumogis-dev\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        tgp._assert_test_checkout(tmp_path)

    shutil.copy(template, tmp_path / ".env")
    tgp._assert_test_checkout(tmp_path)

    text = template.read_text(encoding="utf-8")
    (tmp_path / ".env").write_text(
        re.sub(r"^AUTH_SECRET=.*$", "AUTH_SECRET=changed", text, flags=re.MULTILINE),
        encoding="utf-8",
    )
    with pytest.raises(pytest.fail.Exception):
        tgp._assert_test_checkout(tmp_path)
