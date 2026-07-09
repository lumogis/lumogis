# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Subprocess tests for LUM-199 `scripts/doctor/run.sh` (isolated fixtures)."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import textwrap
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "scripts" / "doctor" / "run.sh"
SCHEMA = REPO / "scripts" / "doctor" / "schema.v1.json"
SCHEMA_V2 = REPO / "scripts" / "doctor" / "schema.v2.json"
REPAIR_SH = REPO / "scripts" / "doctor" / "repair.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content.lstrip("\n"), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _minimal_compose() -> str:
    return textwrap.dedent(
        """\
        services:
          orchestrator:
            image: example/orchestrator:test
          ollama:
            image: example/ollama:test
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
            "orchestrator": {"ports": [{"published": "8000", "target": 8000, "protocol": "tcp"}]},
            "ollama": {"image": "ollama/ollama:test"},
        }
    }
    return json.dumps(doc) + "\n"


def _config_with_falkor() -> str:
    doc = {
        "services": {
            "orchestrator": {"ports": [{"published": "8000", "target": 8000, "protocol": "tcp"}]},
            "falkordb": {"image": "falkordb/falkordb:test"},
            "ollama": {"image": "ollama/ollama:test"},
        }
    }
    return json.dumps(doc) + "\n"


def _config_with_postgres() -> str:
    doc = {
        "services": {
            "orchestrator": {"ports": [{"published": "8000", "target": 8000, "protocol": "tcp"}]},
            "postgres": {"image": "postgres:test"},
            "ollama": {"image": "ollama/ollama:test"},
        }
    }
    return json.dumps(doc) + "\n"


def _config_with_librechat() -> str:
    doc = {
        "services": {
            "orchestrator": {"ports": [{"published": "8000", "target": 8000, "protocol": "tcp"}]},
            "librechat": {"image": "librechat/librechat:test"},
            "ollama": {"image": "ollama/ollama:test"},
        }
    }
    return json.dumps(doc) + "\n"


def _config_without_qdrant() -> str:
    return _config_port_8000()


def _unhealthy_librechat_ps() -> str:
    return (
        '{"Service":"librechat","State":"running","Health":"unhealthy"}\n'
        '{"Service":"orchestrator","State":"running","Health":"healthy"}\n'
    )


def _unhealthy_qdrant_ps() -> str:
    return (
        '{"Service":"qdrant","State":"running","Health":"unhealthy"}\n'
        '{"Service":"orchestrator","State":"running","Health":"healthy"}\n'
    )


def _starting_orchestrator_ps() -> str:
    return '{"Service":"orchestrator","State":"running","Health":"starting"}\n'


def _docker_stub(ps_path: Path, config_path: Path, root: Path) -> str:
    state = root / "_stub_state"
    ps_up = root / "_ps_up.json"
    return textwrap.dedent(
        f"""\
        #!/bin/sh
        if [ "$1" != "compose" ]; then exit 1; fi
        shift
        case "$1" in
          version) echo "Docker Compose version v2.0.0-stub"; exit 0 ;;
          ps)
            if [ "$(cat "{state}" 2>/dev/null)" = "up" ] && [ -f "{ps_up}" ]; then
              cat "{ps_up}"
            else
              cat "{ps_path}"
            fi ;;
          config) cat "{config_path}" ;;
          up) echo up >"{state}"; exit 0 ;;
          restart)
            echo stub-restart-ok >>"{root}/_stub_exec.log"
            echo up >"{state}"
            exit 0 ;;
          exec)
            shift
            _args="$*"
            case "$_args" in
              *ollama*pull*)
                echo "stub-pull-ok" >>"{root}/_stub_exec.log"
                exit 0 ;;
              *ollama*list*)
                if [ "${{LUMOGIS_DOCTOR_STUB_LIST:-}}" = "partial" ]; then
                  printf '%s\\n' "NAME    ID    SIZE    MODIFIED" "llama3.2:3b    b    0    now"
                else
                  printf '%s\\n' "NAME    ID    SIZE    MODIFIED" \\
                    "nomic-embed-text:latest    a    0    now" \\
                    "llama3.2:3b    b    0    now"
                fi
                exit 0 ;;
            esac
            exit 1 ;;
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


def _jq_stub() -> str:
    # Doctor JSON mode pipes python stdout through `jq .`; passthrough is enough for tests.
    return textwrap.dedent(
        """\
        #!/bin/sh
        case "${1:-.}" in
          .) cat ;;
          *) cat ;;
        esac
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
    (root / "_stub_state").write_text("", encoding="utf-8")
    (root / "_stub_exec.log").write_text("", encoding="utf-8")
    (root / "_ps_up.json").write_text(_healthy_ps_ndjson(), encoding="utf-8")
    bindir = root / "bin"
    bindir.mkdir()
    _write_executable(bindir / "docker", _docker_stub(ps_file, cfg_file, root))
    _write_executable(bindir / "curl", curl_stub or _curl_ok_stub())
    _write_executable(bindir / "jq", _jq_stub())
    return root


# Host exports must not leak into isolated fixture runs (live compose project,
# BACKUP_HOST_DIR from config/test.env.example, etc.).
_DOCTOR_HOST_ENV_BLOCKLIST = frozenset(
    {
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "BACKUP_HOST_DIR",
        "BACKUP_DIR",
    }
)


def _run(
    root: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
    path_prefix: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _DOCTOR_HOST_ENV_BLOCKLIST}
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    if extra_env:
        env.update(extra_env)
    base_path = path_prefix or str(root / "bin")
    env["PATH"] = f"{base_path}:/usr/local/bin:/usr/bin:/bin"
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


def test_doctor_json_without_fix_stays_v1(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root, "--json")
    doc = json.loads(proc.stdout)
    assert doc["version"] == 1
    assert "repairs" not in doc


def test_doctor_json_fix_dry_run_v2(tmp_path):
    import jsonschema

    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(root, "--json", "--fix")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["version"] == 2
    assert doc["dry_run"] is True
    assert "repairs" in doc
    schema = json.loads(SCHEMA_V2.read_text(encoding="utf-8"))
    jsonschema.validate(instance=doc, schema=schema)
    log = (root / "_stub_exec.log").read_text(encoding="utf-8")
    assert "stub-pull-ok" not in log


def test_doctor_json_fix_apply_compose_service(tmp_path):
    import jsonschema

    root = _fixture_repo(
        tmp_path,
        ps_body=_stack_down_ps(),
        curl_stub=_curl_fail_orchestrator_stub(),
    )
    proc = _run(root, "--json", "--fix", "--apply", "--yes")
    assert proc.returncode in (0, 1, 2), proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc["version"] == 2
    assert any(r.get("outcome") == "applied" for r in doc["repairs"])
    assert doc["dry_run"] is False
    jsonschema.validate(instance=doc, schema=json.loads(SCHEMA_V2.read_text(encoding="utf-8")))
    assert (root / "_stub_state").read_text(encoding="utf-8").strip() == "up"


def test_doctor_json_fix_apply_refreshes_compose_ps(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_stack_down_ps(),
        curl_stub=_curl_fail_orchestrator_stub(),
    )
    proc = _run(root, "--json", "--fix", "--apply", "--yes")
    doc = json.loads(proc.stdout)
    orch = [c for c in doc["checks"] if c.get("name") == "orchestrator"]
    assert orch and orch[0]["status"] != "warn"


def test_doctor_json_fix_apply_mkdir_backup_dir(tmp_path):
    import jsonschema

    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        env_extra="BACKUP_DIR=backups/missing-backup-dir",
    )
    (root / "backups").mkdir(parents=True)
    proc = _run(root, "--json", "--fix", "--apply", "--yes")
    assert proc.returncode in (0, 1, 2)
    doc = json.loads(proc.stdout)
    assert any(
        r.get("kind") == "mkdir_backup_dir" and r.get("outcome") == "applied"
        for r in doc["repairs"]
    )
    assert (root / "backups" / "missing-backup-dir").is_dir()
    jsonschema.validate(instance=doc, schema=json.loads(SCHEMA_V2.read_text(encoding="utf-8")))


def test_doctor_json_fix_apply_ollama_pull(tmp_path):
    import jsonschema

    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    proc = _run(
        root,
        "--json",
        "--fix",
        "--apply",
        "--yes",
        extra_env={"LUMOGIS_DOCTOR_STUB_LIST": "partial"},
    )
    assert proc.returncode in (0, 1, 2)
    doc = json.loads(proc.stdout)
    assert any(
        r.get("kind") == "ollama_pull_model" and r.get("outcome") == "applied"
        for r in doc["repairs"]
    )
    log = (root / "_stub_exec.log").read_text(encoding="utf-8")
    assert "stub-pull-ok" in log
    jsonschema.validate(instance=doc, schema=json.loads(SCHEMA_V2.read_text(encoding="utf-8")))


def test_doctor_apply_refused_without_yes_non_tty(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_stack_down_ps(),
        curl_stub=_curl_fail_orchestrator_stub(),
    )
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    env["PATH"] = f"{root / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    proc = subprocess.run(
        ["bash", str(RUN_SH), "--fix", "--apply"],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    assert proc.returncode == 4


def test_doctor_json_fix_apply_refused_empty_stdout(tmp_path):
    """VERIFY-PLAN: --json --fix --apply without --yes on non-TTY must refuse.

    Refuse before JSON emit (slice-1 contract).
    """
    root = _fixture_repo(
        tmp_path,
        ps_body=_stack_down_ps(),
        curl_stub=_curl_fail_orchestrator_stub(),
    )
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    env["PATH"] = f"{root / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    proc = subprocess.run(
        ["bash", str(RUN_SH), "--json", "--fix", "--apply"],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
        text=True,
        check=False,
    )
    assert proc.returncode == 4
    assert "DOCTOR_REFUSED:" in proc.stderr
    assert proc.stdout.strip() == ""


def test_doctor_apply_refused_exit4_no_audit_mutations(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_stack_down_ps(),
        curl_stub=_curl_fail_orchestrator_stub(),
    )
    audit = tmp_path / "audit"
    audit.mkdir()
    env = dict(os.environ)
    env["LUMOGIS_DOCTOR_REPO_ROOT"] = str(root)
    env["PATH"] = f"{root / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    env["LUMOGIS_DOCTOR_AUDIT_DIR"] = str(audit)
    proc = subprocess.run(
        ["bash", str(RUN_SH), "--fix", "--apply"],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
        text=True,
        check=False,
    )
    assert proc.returncode == 4
    assert "DOCTOR_REFUSED:" in proc.stderr
    assert not any(audit.iterdir())


def test_doctor_audit_redacts_long_secret(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        env_extra=(
            "POSTGRES_PASSWORD=supersecret_value_at_least_eight\n"
            "BACKUP_DIR=backups/missing-audit-dir"
        ),
    )
    (root / "backups").mkdir(parents=True)
    audit = tmp_path / "audit"
    audit.mkdir()
    proc = _run(
        root,
        "--json",
        "--fix",
        "--apply",
        "--yes",
        extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)},
    )
    assert proc.returncode in (0, 1, 2)
    nd = audit / "repair.ndjson"
    assert nd.is_file()
    blob = nd.read_text(encoding="utf-8")
    assert "supersecret_value_at_least_eight" not in blob
    assert "***REDACTED***" in blob or "REDACTED" in blob


def test_doctor_mkdir_backup_dir_policy_escape(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        env_extra="BACKUP_DIR=../../etc/evil",
    )
    proc = _run(root, "--json", "--fix", "--dry-run")
    doc = json.loads(proc.stdout)
    bad = [r for r in doc["repairs"] if r.get("kind") == "mkdir_backup_dir"]
    assert bad and all(r.get("outcome") == "error" for r in bad)


def test_doctor_unhealthy_emits_restart_fix_row_dry_run(tmp_path):
    import jsonschema

    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_postgres_ps(),
        config_body=_config_with_postgres(),
    )
    proc = _run(root, "--json", "--fix")
    assert proc.returncode == 2, proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc["version"] == 2
    restarts = [r for r in doc["repairs"] if r.get("kind") == "compose_restart_service"]
    assert len(restarts) == 1
    assert restarts[0]["outcome"] == "dry_run"
    assert restarts[0]["target"] == {"service": "postgres"}
    assert restarts[0]["command_argv"]
    assert "restart" in restarts[0]["command_argv"]
    jsonschema.validate(instance=doc, schema=json.loads(SCHEMA_V2.read_text(encoding="utf-8")))


def test_doctor_json_fix_apply_compose_restart_service(tmp_path):
    import jsonschema

    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_postgres_ps(),
        config_body=_config_with_postgres(),
    )
    proc = _run(root, "--json", "--fix", "--apply", "--yes")
    assert proc.returncode in (0, 1, 2), proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc["version"] == 2
    assert any(
        r.get("kind") == "compose_restart_service" and r.get("outcome") == "applied"
        for r in doc["repairs"]
    )
    assert doc["any_applied"] is True
    applied = [r for r in doc["repairs"] if r.get("kind") == "compose_restart_service"]
    assert applied[0]["command_argv"]
    assert "restart" in applied[0]["command_argv"]
    assert "postgres" in applied[0]["command_argv"]
    log = (root / "_stub_exec.log").read_text(encoding="utf-8")
    assert "stub-restart-ok" in log
    jsonschema.validate(instance=doc, schema=json.loads(SCHEMA_V2.read_text(encoding="utf-8")))


def test_doctor_json_fix_apply_restart_refreshes_compose_ps(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_postgres_ps(),
        config_body=_config_with_postgres(),
    )
    proc = _run(root, "--json", "--fix", "--apply", "--yes")
    doc = json.loads(proc.stdout)
    pg = [c for c in doc["checks"] if c.get("name") == "postgres"]
    assert pg and pg[0]["status"] not in ("error", "warn")


def test_doctor_restart_skipped_when_not_in_K(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_librechat_ps(),
        config_body=_config_with_librechat(),
    )
    proc = _run(root, "--json", "--fix")
    doc = json.loads(proc.stdout)
    restarts = [r for r in doc["repairs"] if r.get("kind") == "compose_restart_service"]
    assert len(restarts) == 1
    assert restarts[0]["outcome"] == "skipped"
    assert "allowlist" in restarts[0].get("message", "")


def test_doctor_restart_error_when_not_in_S(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_qdrant_ps(),
        config_body=_config_without_qdrant(),
    )
    proc = _run(root, "--json", "--fix")
    doc = json.loads(proc.stdout)
    restarts = [r for r in doc["repairs"] if r.get("kind") == "compose_restart_service"]
    assert len(restarts) == 1
    assert restarts[0]["outcome"] == "error"
    assert restarts[0].get("message") == "service not in compose config (S)"
    assert restarts[0].get("command_argv") == []


def test_doctor_exited_does_not_emit_restart(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_stack_down_ps())
    proc = _run(root, "--json", "--fix")
    doc = json.loads(proc.stdout)
    kinds = {r.get("kind") for r in doc.get("repairs", [])}
    assert "compose_up_service" in kinds
    assert "compose_restart_service" not in kinds


def test_doctor_health_starting_does_not_emit_restart(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_starting_orchestrator_ps())
    proc = _run(root, "--json", "--fix")
    doc = json.loads(proc.stdout)
    assert not any(r.get("kind") == "compose_restart_service" for r in doc.get("repairs", []))
    orch = [c for c in doc["checks"] if c.get("name") == "orchestrator"]
    assert orch and orch[0]["status"] == "warn"


def test_doctor_unhealthy_fix_dry_run_exit_code_2(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_postgres_ps(),
        config_body=_config_with_postgres(),
    )
    proc = _run(root, "--json", "--fix")
    assert proc.returncode == 2


def test_doctor_repair_direct_compose_restart_bad_target_json(tmp_path):
    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        config_body=_config_with_postgres(),
    )
    stream = tmp_path / "s.tsv"
    stream.write_text(
        "services\tpostgres\terror\th\tm\tcompose_restart_service\t{bad json\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    env = dict(os.environ)
    env.update(
        {
            "LUMOGIS_REPO_ROOT": str(root),
            "DOCTOR_STREAM_PATH": str(stream),
            "DOCTOR_REPAIR_RESULT_PATH": str(out),
            "DOCTOR_CONFIG_CACHE": str(root / "_cfg.json"),
            "DOCTOR_APPLY_MUTATIONS": "0",
            "DOCTOR_YES": "0",
            "DOCTOR_FULL_ARGV_JSON": "[]",
        }
    )
    proc = subprocess.run(
        ["bash", str(REPAIR_SH)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    blob = json.loads(out.read_text(encoding="utf-8"))
    repairs = blob["repairs"] if isinstance(blob, dict) else blob
    assert repairs and repairs[0].get("outcome") == "error"
    assert "stub-restart-ok" not in (root / "_stub_exec.log").read_text(encoding="utf-8")


def test_doctor_repair_direct_malicious_model_argv(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    stream = tmp_path / "s.tsv"
    stream.write_text(
        "models\tx\twarn\tm\tm\tollama_pull_model\t" + json.dumps({"model": "--evil"}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    env = dict(os.environ)
    env.update(
        {
            "LUMOGIS_REPO_ROOT": str(root),
            "DOCTOR_STREAM_PATH": str(stream),
            "DOCTOR_REPAIR_RESULT_PATH": str(out),
            "DOCTOR_CONFIG_CACHE": str(root / "_cfg.json"),
            "DOCTOR_APPLY_MUTATIONS": "0",
            "DOCTOR_YES": "0",
            "DOCTOR_FULL_ARGV_JSON": "[]",
        }
    )
    proc = subprocess.run(
        ["bash", str(REPAIR_SH)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    blob = json.loads(out.read_text(encoding="utf-8"))
    repairs = blob["repairs"] if isinstance(blob, dict) else blob
    assert repairs and repairs[0].get("outcome") == "error"
    assert "stub-pull-ok" not in (root / "_stub_exec.log").read_text(encoding="utf-8")


def test_doctor_repair_stage_fatal_missing_config(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    stream = tmp_path / "s.tsv"
    stream.write_text(
        "models\tx\twarn\tm\tm\tollama_pull_model\t"
        + json.dumps({"model": "nomic-embed-text"})
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    env = dict(os.environ)
    env.update(
        {
            "LUMOGIS_REPO_ROOT": str(root),
            "DOCTOR_STREAM_PATH": str(stream),
            "DOCTOR_REPAIR_RESULT_PATH": str(out),
            "DOCTOR_CONFIG_CACHE": str(tmp_path / "nope.json"),
            "DOCTOR_APPLY_MUTATIONS": "0",
            "DOCTOR_YES": "0",
            "DOCTOR_FULL_ARGV_JSON": "[]",
        }
    )
    proc = subprocess.run(
        ["bash", str(REPAIR_SH)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 3
    assert "DOCTOR_FATAL:" in proc.stderr


def _stream_mkdir_backup(rel_path: str) -> str:
    return "storage\tx\twarn\tm\tm\tmkdir_backup_dir\t" + json.dumps({"path": rel_path}) + "\n"


def _run_repair_direct(
    root: Path,
    stream: Path,
    out: Path,
    *,
    apply: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "LUMOGIS_REPO_ROOT": str(root),
            "DOCTOR_STREAM_PATH": str(stream),
            "DOCTOR_REPAIR_RESULT_PATH": str(out),
            "DOCTOR_CONFIG_CACHE": str(root / "_cfg.json"),
            "DOCTOR_APPLY_MUTATIONS": "1" if apply else "0",
            "DOCTOR_YES": "1" if apply else "0",
            "DOCTOR_FULL_ARGV_JSON": "[]",
            "DOCTOR_ARGV_WANTS_APPLY": "1" if apply else "0",
        }
    )
    if extra_env:
        env.update(extra_env)
    env["PATH"] = f"{root / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return subprocess.run(
        ["bash", str(REPAIR_SH)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _fixture_mkdir_repair(
    tmp_path: Path,
    audit: Path,
    *,
    backup_rel: str = "backups/missing-audit-rotate",
) -> tuple[Path, Path, Path]:
    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        env_extra=f"BACKUP_DIR={backup_rel}",
    )
    (root / "backups").mkdir(parents=True, exist_ok=True)
    stream = tmp_path / "repair-stream.tsv"
    stream.write_text(_stream_mkdir_backup(backup_rel), encoding="utf-8")
    out = tmp_path / "repair-out.json"
    return root, stream, out


def test_doctor_audit_rotates_when_at_size_cap(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    nd = audit / "repair.ndjson"
    padding = "x" * 300
    nd.write_text(padding + "\n", encoding="utf-8")
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_AUDIT_MAX_BYTES": "256",
            "LUMOGIS_DOCTOR_AUDIT_MAX_FILES": "5",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert (audit / "repair.ndjson.1").is_file()
    assert padding not in nd.read_text(encoding="utf-8")
    assert nd.read_text(encoding="utf-8").count("\n") == 1
    assert (nd.stat().st_mode & 0o777) == 0o600
    assert (audit / "repair.ndjson.1").stat().st_mode & 0o777 == 0o600


def test_doctor_audit_rotation_prunes_oldest_generation(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    nd = audit / "repair.ndjson"
    nd.write_text("ACTIVE" + "a" * 300 + "\n", encoding="utf-8")
    (audit / "repair.ndjson.1").write_text("MARKER_ONE\n", encoding="utf-8")
    (audit / "repair.ndjson.2").write_text("MARKER_TWO_DROP\n", encoding="utf-8")
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_AUDIT_MAX_BYTES": "256",
            "LUMOGIS_DOCTOR_AUDIT_MAX_FILES": "3",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert not (audit / "repair.ndjson.3").exists()
    rotated = list(audit.glob("repair.ndjson*"))
    assert len(rotated) == 3
    assert "MARKER_TWO_DROP" not in "\n".join(
        p.read_text(encoding="utf-8") for p in rotated if p.is_file()
    )


def test_doctor_audit_no_rotation_on_dry_run(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    nd = audit / "repair.ndjson"
    nd.write_text("x" * 400 + "\n", encoding="utf-8")
    before = nd.read_bytes()
    proc = _run_repair_direct(
        root,
        stream,
        out,
        apply=False,
        extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)},
    )
    assert proc.returncode == 0, proc.stderr
    assert not (audit / "repair.ndjson.1").exists()
    assert nd.read_bytes() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="rotation failure injection unreliable as root")
def test_doctor_audit_rotation_failure_still_appends(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    nd = audit / "repair.ndjson"
    nd.write_text("x" * 400 + "\n", encoding="utf-8")
    lines_before = len(nd.read_text(encoding="utf-8").splitlines())
    (audit / "repair.ndjson.1").mkdir()
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_AUDIT_MAX_BYTES": "256",
            "LUMOGIS_DOCTOR_AUDIT_MAX_FILES": "2",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "audit rotation failed" in proc.stderr
    lines_after = len(nd.read_text(encoding="utf-8").splitlines())
    assert lines_after == lines_before + 1


def test_doctor_audit_invalid_limits_warn_on_apply(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_AUDIT_MAX_BYTES": "0",
            "LUMOGIS_DOCTOR_AUDIT_MAX_FILES": "1",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "invalid" in proc.stderr.lower() or "default" in proc.stderr.lower()


def test_doctor_audit_explicit_limits_still_rotate(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    nd = audit / "repair.ndjson"
    nd.write_text("x" * 300 + "\n", encoding="utf-8")
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_AUDIT_MAX_BYTES": "256",
            "LUMOGIS_DOCTOR_AUDIT_MAX_FILES": "5",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert (audit / "repair.ndjson.1").is_file()


@contextmanager
def _hold_repair_lock(audit_dir: Path):
    audit_dir.mkdir(parents=True, exist_ok=True)
    lock_path = audit_dir / "repair.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock(1) required")
def test_doctor_repair_apply_refused_when_lock_held(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    with _hold_repair_lock(audit):
        proc = _run_repair_direct(
            root,
            stream,
            out,
            extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)},
        )
    assert proc.returncode == 4, proc.stderr
    assert "DOCTOR_REFUSED:" in proc.stderr
    assert "another doctor --fix --apply is already running" in proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["repairs"] == []
    assert payload["any_applied"] is False
    assert payload["apply_requested"] is True
    assert (audit / "repair.lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock(1) required")
def test_doctor_repair_dry_run_ok_when_lock_held(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_mkdir_repair(tmp_path, audit)
    with _hold_repair_lock(audit):
        proc = _run_repair_direct(
            root,
            stream,
            out,
            apply=False,
            extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)},
        )
    assert proc.returncode == 0, proc.stderr


# --- LUM-494: restart-loop guard for compose_restart_service ----------------


def _stream_compose_restart(service: str = "postgres") -> str:
    return (
        "services\t"
        + service
        + "\terror\th\tm\tcompose_restart_service\t"
        + json.dumps({"service": service})
        + "\n"
    )


def _fixture_restart_repair(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = _fixture_repo(
        tmp_path,
        ps_body=_unhealthy_postgres_ps(),
        config_body=_config_with_postgres(),
    )
    stream = tmp_path / "restart-stream.tsv"
    stream.write_text(_stream_compose_restart("postgres"), encoding="utf-8")
    out = tmp_path / "restart-out.json"
    return root, stream, out


def _seed_restart_audit(
    audit: Path, n: int, *, service: str = "postgres", age_seconds: int = 0
) -> Path:
    audit.mkdir(parents=True, exist_ok=True)
    nd = audit / "repair.ndjson"
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with nd.open("a", encoding="utf-8") as fh:
        for _ in range(n):
            fh.write(
                json.dumps(
                    {
                        "ts": ts,
                        "kind": "compose_restart_service",
                        "target": {"service": service},
                        "outcome": "applied",
                    }
                )
                + "\n"
            )
    return nd


def _restart_rows(out: Path) -> list[dict]:
    blob = json.loads(out.read_text(encoding="utf-8"))
    repairs = blob["repairs"] if isinstance(blob, dict) else blob
    return [r for r in repairs if r.get("kind") == "compose_restart_service"]


def test_doctor_restart_loop_guard_refuses_at_threshold(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    _seed_restart_audit(audit, 3)  # default LUMOGIS_DOCTOR_RESTART_LOOP_MAX=3
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "skipped"
    assert "restart-loop guard" in rows[0].get("message", "")
    # No further restart was executed.
    assert "stub-restart-ok" not in (root / "_stub_exec.log").read_text(encoding="utf-8")
    # Skipped repairs are not audited: still exactly the 3 seeded rows.
    seeded = [
        ln
        for ln in (audit / "repair.ndjson").read_text(encoding="utf-8").splitlines()
        if '"compose_restart_service"' in ln
    ]
    assert len(seeded) == 3


def test_doctor_restart_loop_guard_allows_below_threshold(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    _seed_restart_audit(audit, 2)
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"
    assert "stub-restart-ok" in (root / "_stub_exec.log").read_text(encoding="utf-8")


def test_doctor_restart_loop_guard_window_excludes_old(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    # 4 restarts but all older than the default 3600s window -> not counted.
    _seed_restart_audit(audit, 4, age_seconds=7200)
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_restart_loop_guard_counts_only_target_service(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    # Loop history for a different service must not block postgres.
    _seed_restart_audit(audit, 5, service="qdrant")
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_restart_loop_guard_counts_only_applied_rows(tmp_path):
    # Only outcome=="applied" rows are real restarts. dry_run/failed/skipped
    # rows in the audit must NOT count toward the loop threshold.
    audit = tmp_path / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (audit / "repair.ndjson").open("w", encoding="utf-8") as fh:
        for outcome in ("dry_run", "failed", "skipped", "dry_run"):
            fh.write(
                json.dumps(
                    {
                        "ts": ts,
                        "kind": "compose_restart_service",
                        "target": {"service": "postgres"},
                        "outcome": outcome,
                    }
                )
                + "\n"
            )
    root, stream, out = _fixture_restart_repair(tmp_path)
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_restart_loop_guard_tolerates_malformed_audit(tmp_path):
    # Garbage / non-dict / missing-ts / bad-ts rows must neither crash the
    # reader nor inflate the count. Only the 2 valid applied rows count, so
    # the next restart (default limit 3) is still allowed.
    audit = tmp_path / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    valid = json.dumps(
        {
            "ts": ts,
            "kind": "compose_restart_service",
            "target": {"service": "postgres"},
            "outcome": "applied",
        }
    )
    lines = [
        "this is not json at all",
        "[1, 2, 3]",  # valid json, not a dict
        json.dumps(
            {
                "kind": "compose_restart_service",
                "target": {"service": "postgres"},
                "outcome": "applied",
            }
        ),  # missing ts
        json.dumps(
            {
                "ts": "not-a-timestamp",
                "kind": "compose_restart_service",
                "target": {"service": "postgres"},
                "outcome": "applied",
            }
        ),  # unparseable ts
        valid,
        "",  # blank line
        valid,
    ]
    (audit / "repair.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")
    root, stream, out = _fixture_restart_repair(tmp_path)
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_restart_loop_guard_disabled_by_zero(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    _seed_restart_audit(audit, 5)
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_RESTART_LOOP_MAX": "0",
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_restart_loop_guard_surfaces_in_dry_run(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    _seed_restart_audit(audit, 3)
    proc = _run_repair_direct(
        root,
        stream,
        out,
        apply=False,
        extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)},
    )
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "skipped"
    assert "restart-loop guard" in rows[0].get("message", "")


# --- LUM-340: versioned core-service allowlist (K) --------------------------


def _write_core_manifest(tmp_path: Path, services: list[str], *, raw: str | None = None) -> Path:
    path = tmp_path / "core-services.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(json.dumps({"version": 1, "services": services}), encoding="utf-8")
    return path


def test_doctor_core_allowlist_shipped_manifest_matches_fallback(tmp_path):
    # The shipped manifest must agree with the in-script safety fallback so the
    # externalised K does not silently drift from the documented set.
    manifest = json.loads(
        (REPO / "scripts" / "doctor" / "core-services.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == 1
    assert set(manifest["services"]) == {
        "orchestrator",
        "postgres",
        "caddy",
        "lumogis-web",
        "ollama",
        "qdrant",
        "falkordb",
        "redis",
        "paperless-ngx",
        "gotenberg",
    }


def test_doctor_core_allowlist_manifest_excludes_service(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    manifest = _write_core_manifest(tmp_path, ["orchestrator"])  # postgres removed from K
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_CORE_SERVICES_FILE": str(manifest),
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "skipped"
    assert "allowlist" in rows[0].get("message", "")
    assert "stub-restart-ok" not in (root / "_stub_exec.log").read_text(encoding="utf-8")


def test_doctor_core_allowlist_manifest_includes_service(tmp_path):
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    manifest = _write_core_manifest(tmp_path, ["postgres", "orchestrator"])
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_CORE_SERVICES_FILE": str(manifest),
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"
    assert "stub-restart-ok" in (root / "_stub_exec.log").read_text(encoding="utf-8")


def test_doctor_core_allowlist_script_local_beats_repo_relative(tmp_path):
    # With no override, the manifest shipped beside repair.sh (DOCTOR_SELF_DIR,
    # which includes postgres) takes precedence over a repo-relative manifest
    # under the operator checkout root. So a repo-relative manifest that omits
    # postgres must NOT block the restart.
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    repo_manifest_dir = root / "scripts" / "doctor"
    repo_manifest_dir.mkdir(parents=True, exist_ok=True)
    (repo_manifest_dir / "core-services.json").write_text(
        json.dumps({"version": 1, "services": ["orchestrator"]}), encoding="utf-8"
    )
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_core_allowlist_invalid_entries_rejected(tmp_path):
    # A manifest whose only entries are invalid service names yields an empty
    # valid set, so the loader falls through to the shipped manifest (which
    # contains postgres) rather than adopting a bogus/empty K. Asserting the
    # restart is applied proves the invalid override did not become K.
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    manifest = _write_core_manifest(tmp_path, ["INVALID NAME", "../escape", ""])
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_CORE_SERVICES_FILE": str(manifest),
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"


def test_doctor_core_allowlist_malformed_manifest_falls_back(tmp_path):
    # Malformed override is skipped; the loader falls through to the shipped
    # manifest (which still contains postgres), so the restart proceeds.
    audit = tmp_path / "audit"
    root, stream, out = _fixture_restart_repair(tmp_path)
    manifest = _write_core_manifest(tmp_path, [], raw="{not json")
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_CORE_SERVICES_FILE": str(manifest),
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = _restart_rows(out)
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"
    assert "another doctor --fix --apply is already running" not in proc.stderr


# --- LUM-344: backfill remaining LUM-320 plan §Test cases --------------------
#
# Plan §Test cases vs current coverage (audited 2026-06-19):
#   present: json_without_fix_stays_v1, json_fix_dry_run_v2,
#     json_fix_apply_compose_service, json_fix_apply_mkdir_backup_dir,
#     json_fix_apply_ollama_pull, apply_refused_without_yes_non_tty,
#     json_fix_apply_refused_empty_stdout, fix_apply_refreshes_compose_ps,
#     mkdir_backup_dir_policy_escape, audit_redacts_long_secret,
#     repair_stage_fatal (missing_config).
#   7-field-TSV-without-fix populating checks[] is covered by
#     test_doctor_stack_down_json (services rows present under plain --json).
#   backfilled below: apply+dry_run dry-wins, security-mode refuses apply,
#     audit-file record shape, compose_up non-zero -> outcome failed.
#
# Explicit repo-truth deferral (LUM-344): the plan's "ollama pull timeout"
# case is NOT added — exercising it requires a real `timeout(1)` to fire on a
# long-running stub, which is inherently slow/flaky under the PATH-stub
# subprocess harness. The timeout wrapper itself (TIMEOUT_PULL) is covered by
# construction in repair.sh; firing behaviour is left to manual/integration.


def test_doctor_apply_plus_dry_run_dry_wins(tmp_path):
    # Both --apply and --dry-run present -> --dry-run wins (order-independent):
    # apply_requested=false, no mutation, stderr warns once.
    for order in (("--apply", "--dry-run"), ("--dry-run", "--apply")):
        root = _fixture_repo(
            tmp_path / "_".join(order).replace("-", ""),
            ps_body=_stack_down_ps(),
            curl_stub=_curl_fail_orchestrator_stub(),
        )
        proc = _run(root, "--json", "--fix", *order)
        assert proc.returncode in (0, 1, 2), proc.stderr + proc.stdout
        assert "DOCTOR_WARN: --dry-run overrides --apply" in proc.stderr
        doc = json.loads(proc.stdout)
        assert doc["version"] == 2
        assert doc["apply_requested"] is False
        assert doc["dry_run"] is True
        assert doc["any_applied"] is False
        assert all(r.get("outcome") != "applied" for r in doc["repairs"])
        assert (root / "_stub_state").read_text(encoding="utf-8").strip() != "up"


def test_doctor_security_mode_refuses_apply(tmp_path):
    # DOCTOR_SECURITY (via LUMOGIS_DOCTOR_RUN_SECURITY=1) + --fix --apply --yes
    # -> exit 4, DOCTOR_REFUSED, empty stdout (no partial v2 JSON), no mutation.
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    # Pre-stage a bandit stub so security.sh does NOT bootstrap a venv (network),
    # and a fast `make` stub so `make audit-local` is deterministic.
    bandit_dir = root / ".venv-bandit-check" / "bin"
    bandit_dir.mkdir(parents=True)
    _write_executable(bandit_dir / "bandit", "#!/bin/sh\nexit 0\n")
    _write_executable(root / "bin" / "make", "#!/bin/sh\nexit 0\n")
    proc = _run(
        root,
        "--json",
        "--fix",
        "--apply",
        "--yes",
        extra_env={"LUMOGIS_DOCTOR_RUN_SECURITY": "1"},
    )
    assert proc.returncode == 4, proc.stderr + proc.stdout
    assert "DOCTOR_REFUSED: security audit mode cannot apply repairs" in proc.stderr
    assert proc.stdout.strip() == ""


def test_doctor_audit_file_created(tmp_path):
    # One applied repair -> one NDJSON audit line carrying the full record shape.
    root = _fixture_repo(
        tmp_path,
        ps_body=_healthy_ps_ndjson(),
        env_extra="BACKUP_DIR=backups/new-audit-dir",
    )
    (root / "backups").mkdir(parents=True)
    audit = tmp_path / "audit"
    audit.mkdir()
    proc = _run(
        root,
        "--json",
        "--fix",
        "--apply",
        "--yes",
        extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)},
    )
    assert proc.returncode in (0, 1, 2), proc.stderr
    nd = audit / "repair.ndjson"
    assert nd.is_file()
    lines = [ln for ln in nd.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 1
    rec = json.loads(lines[0])
    required = {
        "ts",
        "user",
        "hostname",
        "cwd",
        "argv",
        "kind",
        "target",
        "command_argv",
        "command_display",
        "exit_status",
        "outcome",
    }
    assert required <= set(rec.keys())
    assert rec["kind"] == "mkdir_backup_dir"
    assert rec["outcome"] in ("applied", "failed")
    assert isinstance(rec["argv"], list)
    assert isinstance(rec["command_argv"], list)


def _docker_stub_up_fails() -> str:
    return textwrap.dedent(
        """\
        #!/bin/sh
        if [ "$1" != "compose" ]; then exit 1; fi
        shift
        case "$1" in
          version) echo "Docker Compose version v2.0.0-stub"; exit 0 ;;
          up) echo "compose up failed (stub)" >&2; exit 1 ;;
          *) exit 1 ;;
        esac
        """
    )


def test_doctor_compose_up_nonzero_outcome_failed(tmp_path):
    # docker compose up returning non-zero -> repair outcome "failed", audited,
    # any_applied stays false. repair.sh process itself still exits 0.
    audit = tmp_path / "audit"
    root = _fixture_repo(tmp_path, ps_body=_stack_down_ps(), config_body=_config_with_postgres())
    _write_executable(root / "bin" / "docker", _docker_stub_up_fails())
    stream = tmp_path / "up-stream.tsv"
    stream.write_text(
        "services\tpostgres\terror\th\tm\tcompose_up_service\t"
        + json.dumps({"service": "postgres"})
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "up-out.json"
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    blob = json.loads(out.read_text(encoding="utf-8"))
    ups = [r for r in blob["repairs"] if r.get("kind") == "compose_up_service"]
    assert len(ups) == 1 and ups[0]["outcome"] == "failed"
    assert blob["any_applied"] is False
    nd = audit / "repair.ndjson"
    assert nd.is_file()
    assert any(
        json.loads(ln)["outcome"] == "failed"
        for ln in nd.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    )


# --- LUM-341: slice-2 .env config-edit safelist (set_env_key) ----------------
#
# Design: docs/decisions/065-...md § Amendment — slice 2. Append-only,
# non-secret, opt-in via LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1, manifest-driven
# (scripts/doctor/env-safelist.json) with a hard secret-name denylist.


def _stream_set_env_key(key: str) -> str:
    return (
        "config\t"
        + key
        + "\twarn\th\tm\tset_env_key\t"
        + json.dumps({"key": key, "value": "ignored-from-stream"})
        + "\n"
    )


def _set_env_fixture(tmp_path):
    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    return root, (root / ".env").read_text(encoding="utf-8")


def test_doctor_env_edit_disabled_by_default(tmp_path):
    # Without LUMOGIS_DOCTOR_ALLOW_ENV_EDITS, a set_env_key row is skipped even
    # under --apply, and .env is untouched.
    audit = tmp_path / "audit"
    root, original = _set_env_fixture(tmp_path)
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("GRAPH_MODE"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(root, stream, out, extra_env={"LUMOGIS_DOCTOR_AUDIT_DIR": str(audit)})
    assert proc.returncode == 0, proc.stderr
    blob = json.loads(out.read_text(encoding="utf-8"))
    rows = [r for r in blob["repairs"] if r["kind"] == "set_env_key"]
    assert len(rows) == 1 and rows[0]["outcome"] == "skipped"
    assert "not enabled" in rows[0].get("message", "")
    assert (root / ".env").read_text(encoding="utf-8") == original
    assert blob["any_applied"] is False


def test_doctor_env_edit_appends_missing_key(tmp_path):
    audit = tmp_path / "audit"
    root, original = _set_env_fixture(tmp_path)
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("GRAPH_MODE"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
        },
    )
    assert proc.returncode == 0, proc.stderr
    blob = json.loads(out.read_text(encoding="utf-8"))
    rows = [r for r in blob["repairs"] if r["kind"] == "set_env_key"]
    assert len(rows) == 1 and rows[0]["outcome"] == "applied"
    assert rows[0]["target"]["key"] == "GRAPH_MODE"
    new = (root / ".env").read_text(encoding="utf-8")
    # Append-only: original content is a prefix; exactly one GRAPH_MODE added.
    assert new.startswith(original)
    assert new.count("GRAPH_MODE=") == 1
    assert "added by lumogis doctor (LUM-341)" in new
    # Backup created with 0600.
    backups = list(root.glob(".env.bak-*"))
    assert len(backups) == 1
    assert oct(backups[0].stat().st_mode & 0o777) == "0o600"
    assert backups[0].read_text(encoding="utf-8") == original


def test_doctor_env_edit_refuses_existing_key(tmp_path):
    # EMBEDDING_MODEL is already in the fixture .env -> append-only refuses.
    audit = tmp_path / "audit"
    root, original = _set_env_fixture(tmp_path)
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("EMBEDDING_MODEL"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = [r for r in json.loads(out.read_text())["repairs"] if r["kind"] == "set_env_key"]
    assert len(rows) == 1 and rows[0]["outcome"] == "skipped"
    assert "append-only" in rows[0].get("message", "")
    assert (root / ".env").read_text(encoding="utf-8") == original


def test_doctor_env_edit_refuses_secret_shaped_name(tmp_path):
    # A secret-shaped key name is hard-refused before any manifest lookup.
    audit = tmp_path / "audit"
    root, original = _set_env_fixture(tmp_path)
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("SOME_API_TOKEN"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = [r for r in json.loads(out.read_text())["repairs"] if r["kind"] == "set_env_key"]
    assert len(rows) == 1 and rows[0]["outcome"] == "error"
    assert "secret-shaped" in rows[0].get("message", "")
    assert (root / ".env").read_text(encoding="utf-8") == original


def test_doctor_env_edit_key_not_in_safelist(tmp_path):
    audit = tmp_path / "audit"
    root, original = _set_env_fixture(tmp_path)
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("TOTALLY_UNLISTED_FLAG"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = [r for r in json.loads(out.read_text())["repairs"] if r["kind"] == "set_env_key"]
    assert len(rows) == 1 and rows[0]["outcome"] == "error"
    assert "not in env safelist" in rows[0].get("message", "")
    assert (root / ".env").read_text(encoding="utf-8") == original


def test_doctor_env_edit_manifest_override_and_secret_filtered(tmp_path):
    # An override manifest controls the editable set; a secret-shaped entry in
    # it is dropped by the loader (never editable).
    audit = tmp_path / "audit"
    root, _ = _set_env_fixture(tmp_path)
    manifest = tmp_path / "env-safelist.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "keys": {
                    "CUSTOM_FLAG": {"default": "on"},
                    "SNEAKY_SECRET": {"default": "leak"},
                },
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "o.json"
    # CUSTOM_FLAG -> appendable
    s1 = tmp_path / "s1.tsv"
    s1.write_text(_stream_set_env_key("CUSTOM_FLAG"), encoding="utf-8")
    p1 = _run_repair_direct(
        root,
        s1,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
            "LUMOGIS_DOCTOR_ENV_SAFELIST_FILE": str(manifest),
        },
    )
    assert p1.returncode == 0, p1.stderr
    r1 = [r for r in json.loads(out.read_text())["repairs"] if r["kind"] == "set_env_key"]
    assert r1[0]["outcome"] == "applied" and r1[0]["target"]["value"] == "on"
    # SNEAKY_SECRET -> filtered out of the safelist (secret-shaped name).
    s2 = tmp_path / "s2.tsv"
    s2.write_text(_stream_set_env_key("SNEAKY_SECRET"), encoding="utf-8")
    out2 = tmp_path / "o2.json"
    p2 = _run_repair_direct(
        root,
        s2,
        out2,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
            "LUMOGIS_DOCTOR_ENV_SAFELIST_FILE": str(manifest),
        },
    )
    assert p2.returncode == 0, p2.stderr
    r2 = [r for r in json.loads(out2.read_text())["repairs"] if r["kind"] == "set_env_key"]
    assert r2[0]["outcome"] == "error"
    assert "secret-shaped" in r2[0].get("message", "")


def test_doctor_env_edit_unsafe_value_rejected(tmp_path):
    # A manifest default with shell-breaking chars (quote/space/$) must be
    # dropped at load time so it can never corrupt .env (holistic-review fix).
    audit = tmp_path / "audit"
    root, original = _set_env_fixture(tmp_path)
    manifest = tmp_path / "env-safelist.json"
    manifest.write_text(
        json.dumps({"version": 1, "keys": {"CUSTOM_FLAG": {"default": 'a b"c $X'}}}),
        encoding="utf-8",
    )
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("CUSTOM_FLAG"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(
        root,
        stream,
        out,
        extra_env={
            "LUMOGIS_DOCTOR_AUDIT_DIR": str(audit),
            "LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1",
            "LUMOGIS_DOCTOR_ENV_SAFELIST_FILE": str(manifest),
        },
    )
    assert proc.returncode == 0, proc.stderr
    rows = [r for r in json.loads(out.read_text())["repairs"] if r["kind"] == "set_env_key"]
    # Manifest yields no valid keys -> falls back to built-in; CUSTOM_FLAG is not
    # in it -> refused, not written.
    assert len(rows) == 1 and rows[0]["outcome"] == "error"
    assert (root / ".env").read_text(encoding="utf-8") == original


def test_doctor_env_edit_dry_run_no_mutation(tmp_path):
    root, original = _set_env_fixture(tmp_path)
    stream = tmp_path / "s.tsv"
    stream.write_text(_stream_set_env_key("GRAPH_MODE"), encoding="utf-8")
    out = tmp_path / "o.json"
    proc = _run_repair_direct(
        root,
        stream,
        out,
        apply=False,
        extra_env={"LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    rows = [r for r in json.loads(out.read_text())["repairs"] if r["kind"] == "set_env_key"]
    assert len(rows) == 1 and rows[0]["outcome"] == "dry_run"
    assert (root / ".env").read_text(encoding="utf-8") == original
    assert not list(root.glob(".env.bak-*"))


def test_doctor_env_edit_end_to_end_via_config_detection(tmp_path):
    # Full run.sh path: config.sh detects missing safelisted keys (opt-in) and
    # repair.sh appends them; output validates against schema v2.
    import jsonschema

    root = _fixture_repo(tmp_path, ps_body=_healthy_ps_ndjson())
    original = (root / ".env").read_text(encoding="utf-8")
    assert "GRAPH_MODE" not in original
    proc = _run(
        root,
        "--json",
        "--fix",
        "--apply",
        "--yes",
        extra_env={"LUMOGIS_DOCTOR_ALLOW_ENV_EDITS": "1"},
    )
    assert proc.returncode in (0, 1, 2), proc.stderr
    doc = json.loads(proc.stdout)
    jsonschema.validate(instance=doc, schema=json.loads(SCHEMA_V2.read_text(encoding="utf-8")))
    env_repairs = [r for r in doc["repairs"] if r["kind"] == "set_env_key"]
    assert any(
        r["target"]["key"] == "GRAPH_MODE" and r["outcome"] == "applied" for r in env_repairs
    )
    assert "GRAPH_MODE=" in (root / ".env").read_text(encoding="utf-8")
