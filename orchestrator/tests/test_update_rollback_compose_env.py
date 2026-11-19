# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-990 MS-1 fixes (2026-11-18): two failures only the live run showed.

1. ``make rollback`` exported ``COMPOSE_FILE=docker-compose.yml:<override>`` when the
   shell had no ``COMPOSE_FILE``, which overrides the one in ``.env`` and drops every
   overlay a household set there. ``compose_with_rollback_override`` now carries the
   ``.env`` value over, read the way Compose reads it.
2. ``make backup`` ran ``docker compose run``, which rebuilds the backup image on a
   checkout; on the containerd image store that deletes the image the running backup
   container came from, and ``make update`` then refused. It now ``exec``s into the
   running container and only falls back to ``run`` when none is running.

Both are F-tests: real shell and make, fake ``docker`` on ``PATH``. The live proof is
the MS-1 run recorded on LUM-990.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_COMMON_SH = _ROOT / "scripts" / "update" / "common.sh"
_MAKEFILE = _ROOT / "Makefile"
_PRINT_COMPOSE_FILE = (
    f'source "{_COMMON_SH}"; compose_with_rollback_override "$1"; printf %s "$COMPOSE_FILE"'
)


def _compose_file(tmp_path: Path, dotenv: str | None, env: dict[str, str] | None = None) -> str:
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv)
    override = tmp_path / "override.yml"
    override.write_text("services: {}\n")
    clean = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    clean.update(env or {})
    out = subprocess.run(
        ["bash", "-c", _PRINT_COMPOSE_FILE, "_", str(override)],
        cwd=tmp_path,
        env=clean,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.removesuffix(f":{override}").removesuffix(f";{override}")


@pytest.mark.parametrize(
    ("dotenv", "expected"),
    [
        (None, "docker-compose.yml"),
        ("POSTGRES_USER=lumogis\n", "docker-compose.yml"),
        (
            "COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml\n",
            "docker-compose.yml:docker-compose.ghcr.yml",
        ),
        (
            'COMPOSE_FILE="docker-compose.yml:docker-compose.gpu.yml"\n',
            "docker-compose.yml:docker-compose.gpu.yml",
        ),
        ("COMPOSE_FILE='a.yml:b.yml'\n", "a.yml:b.yml"),
        ("export COMPOSE_FILE=a.yml:b.yml\n", "a.yml:b.yml"),
        ("COMPOSE_FILE=a.yml:b.yml   # overlays\n", "a.yml:b.yml"),
        ("# COMPOSE_FILE=old.yml\nCOMPOSE_FILE=a.yml\nCOMPOSE_FILE=a.yml:b.yml\n", "a.yml:b.yml"),
    ],
)
def test_rollback_keeps_the_overlays_set_in_dotenv(tmp_path, dotenv, expected):
    assert _compose_file(tmp_path, dotenv) == expected


def test_shell_compose_file_still_wins_over_dotenv(tmp_path):
    got = _compose_file(
        tmp_path, "COMPOSE_FILE=from-dotenv.yml\n", {"COMPOSE_FILE": "from-shell.yml"}
    )
    assert got == "from-shell.yml"


def test_path_separator_from_dotenv_is_used(tmp_path):
    override = tmp_path / "override.yml"
    _compose_file(tmp_path, "COMPOSE_PATH_SEPARATOR=;\nCOMPOSE_FILE=a.yml;b.yml\n")
    out = subprocess.run(
        ["bash", "-c", _PRINT_COMPOSE_FILE, "_", str(override)],
        cwd=tmp_path,
        env={k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout == f"a.yml;b.yml;{override}"


def _make_backup(tmp_path: Path, running: bool) -> list[str]:
    if shutil.which("make") is None:
        pytest.skip("make not installed")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    (bindir / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'if [ "$2" = ps ]; then {"echo abc123" if running else "true"}; fi\n'
    )
    (bindir / "docker").chmod(0o755)
    subprocess.run(
        ["make", "-s", "-f", str(_MAKEFILE), "backup"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=True,
    )
    return log.read_text().splitlines()


def test_make_backup_execs_into_the_running_backup_container(tmp_path):
    calls = _make_backup(tmp_path, running=True)
    assert calls[-1] == "compose exec -T backup /scripts/backup/backup.sh run"
    assert not any(c.startswith("compose run") for c in calls)


def test_make_backup_falls_back_to_run_when_no_backup_container_runs(tmp_path):
    calls = _make_backup(tmp_path, running=False)
    assert calls[-1] == "compose run --rm backup /scripts/backup/backup.sh run"
