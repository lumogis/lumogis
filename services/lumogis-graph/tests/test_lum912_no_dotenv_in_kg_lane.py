# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""No ``.env`` file reaches the KG unit lane, wherever pytest is launched (LUM-912).

``services/lumogis-graph/main.py``'s lifespan calls a bare ``load_dotenv()``.
python-dotenv then walks up from ``main.py``'s folder through **every parent**
until it finds a ``.env``. A git worktree under ``.claude/worktrees/`` sits
inside the main checkout, so the walk reaches the operator's live
``/…/lumogis-app/.env``. Core's unit lane hit exactly this (LUM-905); the KG
lane had the same call and no guard.

The service-root ``conftest.py`` now sets ``PYTHON_DOTENV_DISABLED=1`` so the
file is never read at all; these tests pin that, including against a
python-dotenv too old to honour the flag.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import dotenv

_SENTINEL = "LUM912_SENTINEL_FROM_PARENT_DOTENV"


def _parent_dotenv_tree(tmp_path: Path) -> Path:
    """Model the worktree layout: a ``.env`` several folders above the script.

    Returns the probe script, which calls a bare ``load_dotenv()`` exactly as
    ``main.py``'s lifespan does, so the search starts from the script's folder.
    """
    (tmp_path / ".env").write_text(f"{_SENTINEL}=leaked\nGRAPH_BACKEND=postgres\n")
    script_dir = tmp_path / ".claude" / "worktrees" / "wt" / "services" / "lumogis-graph"
    script_dir.mkdir(parents=True)
    probe = script_dir / "probe.py"
    probe.write_text(
        textwrap.dedent(
            f"""
            import os
            from dotenv import load_dotenv
            load_dotenv(override=False)
            print(os.environ.get({_SENTINEL!r}, "<absent>"))
            print(os.environ.get("GRAPH_BACKEND", "<absent>"))
            """
        )
    )
    return probe


def _run_probe(probe: Path, env: dict[str, str]) -> list[str]:
    out = subprocess.run(
        [sys.executable, str(probe)],
        cwd=probe.parent,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return out.stdout.split()


def test_dotenv_loading_is_disabled_in_the_kg_lane():
    assert os.environ.get("PYTHON_DOTENV_DISABLED") == "1"


def test_load_dotenv_is_a_no_op_here(tmp_path):
    """The installed python-dotenv honours the flag (it needs >= 1.2.0).

    An older one ignores ``PYTHON_DOTENV_DISABLED`` silently, which would make
    the conftest line decoration. This is the test that says so.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(f"{_SENTINEL}=leaked\n")
    assert dotenv.load_dotenv(env_file) is False, (
        "python-dotenv ignored PYTHON_DOTENV_DISABLED; upgrade to >= 1.2.0 or the "
        "KG lane will read the operator's .env again (LUM-912)"
    )
    assert _SENTINEL not in os.environ


def test_a_parent_folder_dotenv_does_not_leak(tmp_path):
    """The worktree layout end to end, in a subprocess that inherits this lane's env."""
    probe = _parent_dotenv_tree(tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in (_SENTINEL, "GRAPH_BACKEND")}
    assert _run_probe(probe, env) == ["<absent>", "<absent>"]


def test_the_probe_would_see_the_leak_without_the_flag(tmp_path):
    """Control: without the flag the same probe *does* load the parent ``.env``.

    Proves the test above is not passing because the walk never reached the file.
    """
    probe = _parent_dotenv_tree(tmp_path)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in (_SENTINEL, "GRAPH_BACKEND", "PYTHON_DOTENV_DISABLED")
    }
    assert _run_probe(probe, env) == ["leaked", "postgres"]
