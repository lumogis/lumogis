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
    assert "another doctor --fix --apply is already running" not in proc.stderr
