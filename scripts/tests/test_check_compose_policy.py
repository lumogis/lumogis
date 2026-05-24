"""Pytest harness for scripts/check_compose_policy.py (LUM-272 / LUM-43)."""

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
        "-f",
        str(MAIN_COMPOSE.relative_to(REPO_ROOT)),
        "-f",
        str(overlay.relative_to(REPO_ROOT)),
    )
    assert proc.returncode == 1, (proc.stderr, proc.stdout)
    assert expect_substring in (proc.stderr or ""), proc.stderr


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Pass B runs `docker compose config`"
)
def test_minimal_known_good_stack_passes() -> None:
    """Smallest standalone compose overlay in-repo: FalkorDB only (allowlisted core)."""
    compose = REPO_ROOT / "docker-compose.falkordb.yml"
    assert compose.is_file()
    proc = _run_policy("-f", str(compose.relative_to(REPO_ROOT)))
    assert proc.returncode == 0, (proc.stderr, proc.stdout)

