# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Subprocess tests for LUM-377 scripts/debug runners."""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEBUG = REPO / "scripts" / "debug"


def _run(
    script: str,
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        [str(DEBUG / script), *args],
        cwd=cwd or REPO,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_list_includes_fast_suites() -> None:
    proc = _run("cli.sh", "list")
    assert proc.returncode == 0
    out = proc.stdout
    assert "unit" in out and "lint" in out and "web" in out and "rust" in out
    assert "verify-public-rc" in out
    assert "none" in out


def test_cli_list_includes_release_umbrella() -> None:
    proc = _run("cli.sh", "list")
    assert proc.returncode == 0
    assert "verify-public-rc" in proc.stdout
    assert "verify-public-rc-full" in proc.stdout


def test_cli_list_marks_private_tree_only() -> None:
    proc = _run("cli.sh", "list")
    assert proc.returncode == 0
    assert "private_tree_only" in proc.stdout
    # cli.sh omits inventory rows where private_tree_only=1 (test-kg, mock-capability).
    assert "test-kg" not in proc.stdout
    assert "mock-capability" not in proc.stdout
    visible = [
        ln
        for ln in proc.stdout.splitlines()
        if ln.startswith("| ") and "---" not in ln and not ln.startswith("| id |")
    ]
    assert visible, "expected at least one public inventory row"
    assert all(ln.rstrip().endswith("| 0 |") for ln in visible)


def test_logs_sh_last_missing_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "empty-logs"
    log_dir.mkdir()
    proc = _run("logs.sh", "last", env={"LUMOGIS_DEBUG_LOG_DIR": str(log_dir)})
    assert proc.returncode != 0
    assert "no log" in (proc.stdout + proc.stderr).lower()


def test_integration_refuses_without_heavy() -> None:
    proc = _run("integration.sh", "graph-parity")
    assert proc.returncode == 2
    assert "heavy" in (proc.stdout + proc.stderr).lower()


def test_web_e2e_refuses_without_heavy() -> None:
    proc = _run("web.sh", "e2e")
    assert proc.returncode == 2
    assert "heavy" in (proc.stdout + proc.stderr).lower()


def test_rust_skips_when_desktop_absent(tmp_path: Path) -> None:
    fake = tmp_path / "fake-repo"
    (fake / "scripts" / "debug").mkdir(parents=True)
    for name in ("_common.sh", "rust.sh"):
        src = DEBUG / name
        dst = fake / "scripts" / "debug" / name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR)
    proc = subprocess.run(
        [str(fake / "scripts" / "debug" / "rust.sh")],
        cwd=fake,
        env={**os.environ, "LUMOGIS_DEBUG_LOG_DIR": str(tmp_path / "logs")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "SKIP" in proc.stdout


def test_rust_skips_when_cargo_absent(tmp_path: Path) -> None:
    fake = tmp_path / "fake-repo"
    search = fake / "clients" / "lumogis-search" / "src-tauri"
    search.mkdir(parents=True)
    (search / "Cargo.toml").write_text('[package]\nname = "stub"\n', encoding="utf-8")
    dbg = fake / "scripts" / "debug"
    dbg.mkdir(parents=True)
    for name in ("_common.sh", "rust.sh"):
        dst = dbg / name
        dst.write_text((DEBUG / name).read_text(encoding="utf-8"), encoding="utf-8")
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    proc = subprocess.run(
        [str(dbg / "rust.sh")],
        cwd=fake,
        env={
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "LUMOGIS_DEBUG_LOG_DIR": str(tmp_path / "logs"),
            "PATH": f"/usr/bin:/bin:{empty_bin}",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "cargo not on path" in proc.stdout.lower()


def _stub_bin(tmp_path: Path, *, digest: bool = False) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "record.txt"
    record.write_text("", encoding="utf-8")

    make_sh = bindir / "make"
    make_sh.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "make $*" >> "{record}"
            echo "PYTEST_ADDOPTS=${{PYTEST_ADDOPTS:-}}" >> "{record}"
            if [[ " $* " == *" test "* ]]; then
              exit ${{LUMOGIS_STUB_MAKE_RC:-0}}
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    make_sh.chmod(make_sh.stat().st_mode | stat.S_IXUSR)

    pytest_sh = bindir / "python3"
    if digest:
        pytest_body = """\
#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then exit 0; fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pytest" && "${3:-}" == "--help" ]]; then
  echo "--agent-digest"
  exit 0
fi
exit 0
"""
    else:
        pytest_body = """\
#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then exit 0; fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pytest" ]]; then exit 0; fi
exit 0
"""
    pytest_sh.write_text(pytest_body, encoding="utf-8")
    pytest_sh.chmod(pytest_sh.stat().st_mode | stat.S_IXUSR)
    return bindir


def test_unit_stub_passes_verbose(tmp_path: Path) -> None:
    bindir = _stub_bin(tmp_path)
    log_dir = tmp_path / "logs"
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "LUMOGIS_DEBUG_LOG_DIR": str(log_dir),
    }
    proc = _run("unit.sh", "--verbose", env=env)
    assert proc.returncode == 0
    assert list(log_dir.glob("unit-*.log"))
    record = tmp_path / "record.txt"
    assert " test" in record.read_text(encoding="utf-8")


def test_unit_stub_failure_still_summarizes(tmp_path: Path) -> None:
    bindir = _stub_bin(tmp_path)
    log_dir = tmp_path / "logs"
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "LUMOGIS_DEBUG_LOG_DIR": str(log_dir),
        "LUMOGIS_STUB_MAKE_RC": "1",
    }
    proc = _run("unit.sh", env=env)
    assert proc.returncode == 1
    assert "--- summary" in proc.stdout
    assert list(log_dir.glob("unit-*.log"))


def test_unit_builds_digest_argv(tmp_path: Path) -> None:
    bindir = _stub_bin(tmp_path, digest=True)
    log_dir = tmp_path / "logs"
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "LUMOGIS_DEBUG_LOG_DIR": str(log_dir),
    }
    _run("unit.sh", env=env)
    record = (tmp_path / "record.txt").read_text(encoding="utf-8")
    assert "--agent-digest=file" in record
    assert "--agent-digest-file=" in record
    assert str(log_dir) in record


def test_log_rotation_drops_oldest(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for i in range(55):
        (log_dir / f"unit-20260101-{i:04d}.log").write_text("x", encoding="utf-8")
        (log_dir / f"unit-20260101-{i:04d}.digest.md").write_text("d", encoding="utf-8")
    subprocess.run(
        [
            "bash",
            "-c",
            f'source "{DEBUG / "_common.sh"}"; '
            f'LUMOGIS_DEBUG_LOG_DIR="{log_dir}"; '
            f'LOG_DIR="{log_dir}"; '
            "lumogis_debug_rotate unit 50",
        ],
        cwd=REPO,
        check=True,
    )
    assert len(list(log_dir.glob("unit-*.log"))) <= 50
    assert len(list(log_dir.glob("unit-*.digest.md"))) <= 50


def test_debug_log_dir_rejects_traversal() -> None:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{DEBUG / "_common.sh"}"; '
            "LUMOGIS_DEBUG_LOG_DIR=/etc; "
            "lumogis_debug_validate_log_dir",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1


def test_cli_nosuch_exits_2() -> None:
    proc = _run("cli.sh", "nosuch")
    assert proc.returncode == 2
