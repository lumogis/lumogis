# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Backup/restore repo-root ``.env`` and guard ``config/test.env.example`` for restart E2E."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

FORBIDDEN_TEST_ENV_KEYS = frozenset(
    {
        "FILESYSTEM_ROOT_HOST",
        "INGEST_PATHS",
        "INGEST_PATHS_HOST",
        "INBOX_OWNER_USER_ID",
    }
)


def assert_test_env_example_guard(repo_root: Path) -> None:
    """Fail fast when RC env example would shadow admin-written .env keys."""
    example = repo_root / "config" / "test.env.example"
    if not example.is_file():
        raise AssertionError(f"missing {example}")
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in FORBIDDEN_TEST_ENV_KEYS:
            raise AssertionError(
                f"config/test.env.example must not define {key!r} — "
                "it would break ingest_paths restart E2E env_file merge"
            )


def backup_project_env(repo_root: Path) -> Path:
    """Copy repo-root ``.env`` to a sibling backup file; return backup path."""
    env_path = repo_root / ".env"
    backup = repo_root / ".env.lum400-restart-e2e.bak"
    if env_path.is_file():
        shutil.copy2(env_path, backup)
    else:
        backup.write_text("", encoding="utf-8")
    return backup


def restore_project_env(backup_path: Path, *, repo_root: Path | None = None) -> None:
    """Restore repo-root ``.env`` from backup (or remove if backup was empty)."""
    root = repo_root or backup_path.parent
    env_path = root / ".env"
    if not backup_path.is_file():
        return
    content = backup_path.read_text(encoding="utf-8")
    if content.strip():
        env_path.write_text(content, encoding="utf-8")
    elif env_path.is_file():
        env_path.unlink()


def rewrite_env_key(content: str, key: str, value: str) -> str:
    """Strip every ``key=...`` line and append one canonical assignment."""
    pattern = re.compile(
        rf"^[ \t]*{re.escape(key)}[ \t]*=.*(?:\r?\n)?",
        re.MULTILINE,
    )
    content = pattern.sub("", content).rstrip()
    if content:
        content += "\n"
    content += f"{key}={value}\n"
    return content


def reset_ingest_settings(*, compose_project: str = "lumogis-test") -> None:
    """Delete persisted ingest-path overrides from ``app_settings`` for test isolation.

    The Postgres volume survives ``docker compose down`` (no ``-v``), and
    ``get_effective_ingest_paths`` reads the DB-stored ``ingest_paths`` ahead of the
    ``INGEST_PATHS``/``FILESYSTEM_ROOT`` env fallback. Without this, a prior test's (or
    prior run's) ``PUT /settings`` leaves an override that masks the env-fallback path
    under test (e.g. the malformed-JSON fallback case). Best-effort: a non-zero return or
    missing container is ignored so the test still runs (and fails loudly on the real
    assertion) if the DB cannot be reached.
    """
    sql = (
        "DELETE FROM app_settings WHERE key IN "
        "('ingest_paths','pending_ingest_paths','pending_prune');"
    )
    subprocess.run(
        [
            "docker",
            "exec",
            f"{compose_project}-postgres-1",
            "sh",
            "-c",
            f'psql -U "${{POSTGRES_USER:-lumogis}}" -d "${{POSTGRES_DB:-lumogis}}" -c "{sql}"',
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def orchestrator_container_marker(
    repo_root: Path,
    *,
    compose_project: str = "lumogis-test",
) -> str:
    """Return ``container_id:started_at`` for recreate proof."""
    q = subprocess.run(
        ["docker", "compose", "-p", compose_project, "ps", "-q", "orchestrator"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if q.returncode != 0 or not q.stdout.strip():
        raise RuntimeError(
            f"orchestrator container not found for project {compose_project!r}: {q.stderr.strip()}"
        )
    cid = q.stdout.strip().splitlines()[0].strip()
    ins = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}:{{.State.StartedAt}}", cid],
        capture_output=True,
        text=True,
        check=True,
    )
    return ins.stdout.strip()
