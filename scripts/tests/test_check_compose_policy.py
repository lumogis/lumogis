"""Pytest harness for scripts/check_compose_policy.py (LUM-272 / LUM-43 / LUM-620)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="PyYAML required by check_compose_policy.py")

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_SCRIPT = REPO_ROOT / "scripts" / "check_compose_policy.py"
MAIN_COMPOSE = REPO_ROOT / "docker-compose.yml"

ADVERSARIAL_CASES = [
    pytest.param(
        REPO_ROOT / "docker-compose.test-policy-adversarial.yml",
        "POSTGRES_PASSWORD",
        id="forbidden_postgres_password",
    ),
    pytest.param(
        REPO_ROOT / "docker-compose.test-policy-adversarial-envfile.yml",
        "env_file",
        id="env_file_ban",
    ),
]


def _run_policy(*compose_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(POLICY_SCRIPT), *compose_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("overlay", "expect_substring"), ADVERSARIAL_CASES)
def test_adversarial_overlay_violation_reported(
    overlay: Path, expect_substring: str
) -> None:
    """Pass A must fail before docker compose; stderr names the policy breach."""
    if not overlay.is_file():
        pytest.skip(
            f"Missing adversarial fixture {overlay.name} (gitignored local artefact; "
            "see Makefile compose-policy-check-adversarial targets)"
        )
    if not MAIN_COMPOSE.is_file():
        pytest.fail(f"Expected main compose at {MAIN_COMPOSE}")

    proc = _run_policy(
        "--core-file",
        str(MAIN_COMPOSE.relative_to(REPO_ROOT)),
        "-f",
        str(overlay.relative_to(REPO_ROOT)),
    )
    assert proc.returncode == 1, (proc.stderr, proc.stdout)
    assert expect_substring in (proc.stderr or ""), proc.stderr


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Pass B runs `docker compose config`"
)
@pytest.mark.skipif(
    not (REPO_ROOT / ".env").is_file(),
    reason="docker compose config needs .env (CI: cp .env.example .env)",
)
def test_minimal_known_good_stack_passes() -> None:
    """Base + FalkorDB overlay, both first-party core (LUM-620: both define allowlisted
    services, so both are --core-file). The FalkorDB overlay is not standalone any more:
    it extends the base `backup` service, which has no image of its own there."""
    compose = REPO_ROOT / "docker-compose.falkordb.yml"
    assert compose.is_file()
    proc = _run_policy(
        "--core-file",
        str(MAIN_COMPOSE.relative_to(REPO_ROOT)),
        "--core-file",
        str(compose.relative_to(REPO_ROOT)),
    )
    assert proc.returncode == 0, (proc.stderr, proc.stdout)



# ── LUM-620: core/overlay name-squat check ────────────────────────────────────

NAME_SQUAT_OVERLAY = REPO_ROOT / "docker-compose.test-policy-name-squat.yml"


def _load_policy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_compose_policy", POLICY_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _allowlist() -> frozenset[str]:
    return _load_policy_module().load_allowlist(
        REPO_ROOT / "scripts" / "compose_core_allowlist.txt"
    )


def _pass_a(files: list[Path], core: list[Path]) -> list[str]:
    mod = _load_policy_module()
    violations: list[str] = []
    mod.pass_a_scan(files, _allowlist(), violations, mod.core_closure(core))
    return violations


def test_name_squat_overlay_rejected() -> None:
    """A non-core overlay defining allowlisted `backup` + `playwright` fails in Pass A
    (before docker), naming each squatted service."""
    proc = _run_policy(
        "--core-file", "docker-compose.yml", "-f", NAME_SQUAT_OVERLAY.name
    )
    assert proc.returncode == 1, (proc.stderr, proc.stdout)
    for svc in ("backup", "playwright"):
        assert f"name-squat: service {svc!r}" in proc.stderr, proc.stderr
    # The core file's own `backup` must not be the one reported.
    assert proc.stderr.count("Pass A: name-squat:") == 2, proc.stderr


def test_name_squat_inverse_core_definition_passes_pass_a() -> None:
    """Inverse regression guard: the same definitions are legitimate in a core file."""
    assert _pass_a([NAME_SQUAT_OVERLAY], core=[NAME_SQUAT_OVERLAY]) == []


def test_real_core_file_defining_allowlisted_services_passes_pass_a() -> None:
    """docker-compose.yml defines backup/postgres/qdrant/...: fine as --core-file."""
    main = REPO_ROOT / "docker-compose.yml"
    assert _pass_a([main], core=[main]) == []


def test_no_core_file_treats_every_file_as_overlay(tmp_path: Path) -> None:
    """Fail-closed default: without --core-file, the base file itself is an overlay."""
    proc = _run_policy("-f", "docker-compose.yml")
    assert proc.returncode == 1, (proc.stderr, proc.stdout)
    assert "name-squat: service 'backup'" in proc.stderr, proc.stderr
    assert "no --core-file given" in proc.stderr, proc.stderr


def test_first_party_overlay_overriding_core_service_is_squat_unless_core() -> None:
    """An image-override overlay (ghcr.yml redefines orchestrator) is exactly the merge
    attack when it comes from an untrusted file — it must be marked core to pass."""
    main = REPO_ROOT / "docker-compose.yml"
    ghcr = REPO_ROOT / "docker-compose.ghcr.yml"
    as_overlay = _pass_a([main, ghcr], core=[main])
    assert any("'orchestrator'" in v and "name-squat" in v for v in as_overlay), as_overlay
    assert _pass_a([main, ghcr], core=[main, ghcr]) == []


def test_empty_service_body_in_overlay_is_still_a_definition(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yml"
    overlay.write_text("services:\n  backup:\n", encoding="utf-8")
    main = REPO_ROOT / "docker-compose.yml"
    violations = _pass_a([main, overlay], core=[main])
    assert any("name-squat: service 'backup'" in v for v in violations), violations


def test_include_provenance(tmp_path: Path) -> None:
    """Files a core file includes are core; files an overlay includes are not."""
    shared = tmp_path / "shared.yml"
    shared.write_text("services:\n  redis:\n    image: redis:7\n", encoding="utf-8")
    core = tmp_path / "core.yml"
    core.write_text("include:\n  - shared.yml\n", encoding="utf-8")
    assert _pass_a([core], core=[core]) == []

    squat = tmp_path / "squat.yml"
    squat.write_text("services:\n  redis:\n    image: evil/redis\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yml"
    overlay.write_text("include:\n  - squat.yml\n", encoding="utf-8")
    violations = _pass_a([core, overlay], core=[core])
    assert len(violations) == 1 and "squat.yml" in violations[0], violations


def test_core_file_also_given_as_f_is_core(tmp_path: Path) -> None:
    """`--core-file X -f X` composes X once and keeps it core (Pass A only: a
    non-allowlisted forbidden key in a sibling overlay stops before docker)."""
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "services:\n  widget:\n    environment:\n      DATABASE_URL: x\n", encoding="utf-8"
    )
    proc = _run_policy(
        "--core-file", "docker-compose.yml", "-f", "docker-compose.yml", "-f", str(bad)
    )
    assert proc.returncode == 1, (proc.stderr, proc.stdout)
    assert "name-squat" not in proc.stderr, proc.stderr
    assert "DATABASE_URL" in proc.stderr, proc.stderr


def test_yaml_parse_error_is_tool_error_not_violation(tmp_path: Path) -> None:
    """Fail-closed: a broken file exits 2 — never 1, which adversarial Make targets
    would accept as 'violation caught'."""
    broken = tmp_path / "broken.yml"
    broken.write_text("services:\n  backup: [unclosed\n", encoding="utf-8")
    for args in (("--core-file", "docker-compose.yml", "-f", str(broken)),
                 ("--core-file", str(broken))):
        proc = _run_policy(*args)
        assert proc.returncode == 2, (args, proc.stderr, proc.stdout)
        assert "cannot parse YAML" in proc.stderr, proc.stderr


def test_missing_core_file_is_tool_error(tmp_path: Path) -> None:
    proc = _run_policy("--core-file", str(tmp_path / "nope.yml"))
    assert proc.returncode == 2, (proc.stderr, proc.stdout)


def test_no_compose_files_is_usage_error() -> None:
    proc = _run_policy()
    assert proc.returncode == 2, (proc.stderr, proc.stdout)
