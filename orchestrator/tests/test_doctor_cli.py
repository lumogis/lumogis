# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Subprocess tests for LUM-199 `scripts/doctor/run.sh` (isolated fixtures)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "scripts" / "doctor" / "run.sh"
SCHEMA = REPO / "scripts" / "doctor" / "schema.v1.json"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content.lstrip("\n"), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _minimal_compose() -> str:
    return textwrap.dedent(
        """\
        services:
          orchestrator:
            image: example/orchestrator:test
        """
    )


def _healthy_ps_ndjson() -> str:
    lines = [
        '{"Service":"postgres","State":"running","Health":"healthy"}',
        '{"Service":"orchestrator","State":"running","Health":"healthy"}',
        '{"Service":"caddy","State":"running","Health":"healthy"}',
        '{"Service":"lumogis-web","State":"running","Health":"healthy"}',
        '{"Service":"ollama","State":"running","Health":"healthy"}',
    ]
    return "\n".join(lines) + "\n"


def _healthy_ps_array() -> str:
    raw = [json.loads(x) for x in _healthy_ps_ndjson().strip().splitlines()]
    return json.dumps(raw) + "\n"


def _unhealthy_postgres_ps() -> str:
    return (
        '{"Service":"postgres","State":"running","Health":"unhealthy"}\n'
        '{"Service":"orchestrator","State":"running","Health":"healthy"}\n'
    )


def _stack_down_ps() -> str:
    return (
        '{"Service":"orchestrator","State":"exited","Health":""}\n'
        '{"Service":"postgres","State":"exited","Health":""}\n'
    )


def _config_port_8000() -> str:
    doc = {
        "services": {
            "orchestrator": {
                "ports": [{"published": "8000", "target": 8000, "protocol": "tcp"}]
            }
        }
    }
    return json.dumps(doc) + "\n"


def _config_with_falkor() -> str:
    doc = {
        "services": {
            "orchestrator": {
                "ports": [{"published": "8000", "target": 8000, "protocol": "tcp"}]
            },
            "falkordb": {"image": "falkordb/falkordb:test"},
        }
    }
    return json.dumps(doc) + "\n"


def _docker_stub(ps_path: Path, config_path: Path) -> str:
    return textwrap.dedent(
        f"""\
        #!/bin/sh
        if [ "$1" != "compose" ]; then exit 1; fi
        shift
        case "$1" in
          version) echo "Docker Compose version v2.0.0-stub"; exit 0 ;;
          ps) cat "{ps_path}" ;;
          config) cat "{config_path}" ;;
          exec) printf '%s\\n' "NAME    ID    SIZE    MODIFIED" \\
            "nomic-embed-text:latest    a    0    now" \\
            "llama3.2:3b    b    0    now" ;;
          *) exit 1 ;;
        esac
        """
    )


def _curl_ok_stub() -> str:
    return textwrap.dedent(
        """\
        #!/bin/sh
        exit 0
        """
    )


def _curl_fail_orchestrator_stub() -> str:
    return textwrap.dedent(
        """\
        #!/bin/sh
        for a in "$@"; do
          case "$a" in
            *127.0.0.1:8000*) exit 1 ;;
          esac
        done
        exit 0
        """
    )


def _fixture_repo(
    tmp_path: Path,
    *,
    ps_body: str,
    config_body: str | None = None,
    env_extra: str = "",
    curl_stub: str | None = None,
) -> Path:
    root = tmp_path / "lum"
    root.mkdir(parents=True)
    (root / "docker-compose.yml").write_text(_minimal_compose(), encoding="utf-8")
    ps_file = root / "_ps.json"
    ps_file.write_text(ps_body, encoding="utf-8")
    cfg_file = root / "_cfg.json"
    cfg_file.write_text(config_body or _config_port_8000(), encoding="utf-8")
    env_lines = [
        "COMPOSE_PROJECT_NAME=lumogis-test-doctor",
        "COMPOSE_FILE=docker-compose.yml",
        "EMBEDDING_MODEL=nomic-embed-text",
        "LUMOGIS_DEFAULT_LLM=llama3.2:3b",
    ]
    if env_extra:
        env_lines.append(env_extra.strip())
    (root / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    bindir = root / "bin"
    bindir.mkdir()
    _write_executable(bindir / "docker", _docker_stub(ps_file, cfg_file))
    _write_executable(bindir / "curl", curl_stub or _curl_ok_stub())
    return root


def _run(
    root: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
    path_prefix: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    if extra_env:
        env.update(extra_env)
    base_path = path_prefix or str(root / "bin")
    env["PATH"] = f"{base_path}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return subprocess.run(
        ["bash", str(RUN_SH), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_doctor_json_schema_version(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root, "--json")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc["version"] == 1
    for ch in doc["checks"]:
        assert set(ch.keys()) >= {"category", "name", "status", "message", "remediation"}


def test_doctor_exit_code_all_ok(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root)
    assert proc.returncode == 0


def test_doctor_exit_code_error(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_unhealthy_postgres_ps())
    proc = _run(root)
    assert proc.returncode == 2


def test_doctor_missing_jq_json(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    bindir = root / "bin"
    minimal = f"{bindir}:/usr/bin:/bin:/sbin"
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    env["PATH"] = minimal
    if shutil.which("jq", path=minimal):
        pytest.skip("jq present under minimal PATH")
    proc = subprocess.run(
        ["bash", str(RUN_SH), "--json"],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 3
    assert "DOCTOR_FATAL:" in proc.stderr
    assert proc.stdout.strip() == ""


def test_doctor_human_mode_without_jq(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    bindir = root / "bin"
    minimal = f"{bindir}:/usr/bin:/bin:/sbin"
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    env["PATH"] = minimal
    if shutil.which("jq", path=minimal):
        pytest.skip("jq present under minimal PATH")
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    assert "summary:" in proc.stdout


def test_doctor_compose_ps_ndjson_vs_array(tmp_path):
    root_a = _fixture_repo(tmp_path / "a", ps_body=_healthy_ps_ndjson())
    root_b = _fixture_repo(tmp_path / "b", ps_body=_healthy_ps_array())
    out_a = _run(root_a, "--json").stdout
    out_b = _run(root_b, "--json").stdout
    ja = json.loads(out_a)
    jb = json.loads(out_b)
    names_a = sorted((c["category"], c["name"], c["status"]) for c in ja["checks"])
    names_b = sorted((c["category"], c["name"], c["status"]) for c in jb["checks"])
    assert names_a == names_b


def test_doctor_compose_file_merge(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        config_body=_config_with_falkor(),
        env_extra="GRAPH_MODE=service",
    )
    proc = _run(root, "--json")
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    names = [c["name"] for c in doc["checks"] if c["category"] == "services"]
    assert "compose-graph" not in names


def test_doctor_no_bootstrap_venvs_default(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root)
    assert proc.returncode == 0
    assert not (root / ".venv-audit").exists()
    assert not (root / ".venv-bandit-check").exists()


def test_doctor_jsonschema_validates(tmp_path):
    import jsonschema

    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root, "--json")
    doc = json.loads(proc.stdout)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(instance=doc, schema=schema)


@pytest.mark.parametrize(
    "extra_line",
    [
        'export MY_SECRET_TOKEN="supersecret_value_xyz"',
        "MY_SECRET_TOKEN=supersecret_value_xyz",
        "MONKEY=banana",
        'WEIRD="a=b"',
    ],
)
def test_doctor_env_redaction_parametrized(tmp_path, extra_line):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    envp = root / ".env"
    envp.write_text(envp.read_text(encoding="utf-8") + "\n" + extra_line + "\n", encoding="utf-8")
    proc = _run(root, "--json")
    blob = proc.stdout + proc.stderr
    assert "supersecret_value_xyz" not in blob


def test_doctor_fatal_prereq_stderr_only(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    env["PATH"] = "/usr/bin:/bin"
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if shutil.which("docker", path=env["PATH"]):
        pytest.skip("docker on minimal PATH")
    assert proc.returncode == 3
    assert "DOCTOR_FATAL:" in proc.stderr
    assert proc.stdout.strip() == ""


def test_doctor_stack_down_json(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_stack_down_ps(),
        curl_stub=_curl_fail_orchestrator_stub(),
    )
    proc = _run(root, "--json")
    assert proc.returncode in (0, 1, 2)
    doc = json.loads(proc.stdout)
    assert doc["version"] == 1
    assert any(c["category"] == "services" for c in doc["checks"])


def test_doctor_no_orchestrator_imports_in_shell():
    proc = subprocess.run(
        ["grep", "-R", "from orchestrator", str(REPO / "scripts" / "doctor")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1


def test_doctor_malformed_ps_json_emits_error_not_fatal(tmp_path):
    root = _fixture_repo(tmp_path, ps_body="not-json-at-all\n")
    proc = _run(root, "--json")
    assert proc.returncode in (1, 2)
    assert proc.stdout.strip().startswith("{")
    doc = json.loads(proc.stdout)
    assert any(c.get("name") == "compose-ps-json" for c in doc["checks"])


def test_doctor_security_opt_in_skipped(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root, "--json")
    doc = json.loads(proc.stdout)
    sec = [c for c in doc["checks"] if c["category"] == "security"]
    assert sec and sec[0]["status"] == "skipped"
