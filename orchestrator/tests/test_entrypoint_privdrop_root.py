# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Privilege-drop section run with the REAL find / chown / setpriv (LUM-887).

``test_entrypoint_privdrop.py`` stubs ``chown`` and ``setpriv`` and, as a
non-root user, cannot create root-owned files. This file runs the same section
as root over a real tree, so it proves what the stubs cannot: the one-pass
``find`` selection (prune / hard-link / ``-user 0`` precedence), ``chown -h``
symlink safety, the readability probe and a passwd lookup under the dropped UID.

It needs root and ``setpriv``. The CI pytest runs as a non-root user, so this
file skips there; ci.yml runs it in a root ``python:3.12.11-slim`` container
(the orchestrator base image), and ``make compose-test`` runs it as root too.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from tests.test_secret_sentinels import _REPO_ROOT

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("setpriv") is None,
    reason="needs root and setpriv; runs in the CI root container and make compose-test",
)

_ENTRYPOINT = _REPO_ROOT / "orchestrator" / "docker-entrypoint.sh"
U = 4242


def _section() -> str:
    text = _ENTRYPOINT.read_text()
    m = re.search(r"^# >>> privdrop-section\n(.*?)^# <<< privdrop-section$", text, re.S | re.M)
    assert m, "privdrop-section markers missing"
    return m.group(1)


class Tree:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.project = base / "project"
        self.home = base / "home"
        self.hf = base / "hf"
        self.staging = base / "staging"
        self.data_dir = base / "optdata"
        self.ws = base / "ws"
        self.appcfg = base / "appcfg"
        for d in (
            self.project,
            self.home,
            self.hf,
            self.staging,
            self.data_dir,
            self.ws,
            self.appcfg,
        ):
            d.mkdir()
        self.uid_map = base / "uid_map"
        self.uid_map.write_text("0 0 4294967295\n")
        self.passwd = base / "passwd"
        self.group = base / "group"
        self.passwd.write_text("root:x:0:0:root:/root:/bin/bash\n")
        self.group.write_text("root:x:0:\n")

    def make(
        self, path: Path, uid: int, gid: int, *, is_dir: bool = False, mode: int | None = None
    ) -> Path:
        if is_dir:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        os.lchown(path, uid, gid)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def run(self, *, real_passwd: bool = False, **extra: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.base),
            "TMPDIR": str(self.base),
            "LUMOGIS_RUN_UID": str(U),
            "_LUM_PROJECT_DIR": str(self.project),
            "_LUM_HOME_DIR": str(self.home),
            "_LUM_APP_CONFIG_DIR": str(self.appcfg),
            "_LUM_ROOTFS_DIR": str(self.base),
            "_LUM_UID_MAP_FILE": str(self.uid_map),
            "_LUM_DATA_GLOB": str(self.base / "data-none"),
            "_LUM_DEFAULT_HF_HOME": str(self.hf),
            "_LUM_DEFAULT_STAGING": str(self.staging),
            "_LUM_DEFAULT_DATA_DIR": str(self.data_dir),
            "WORKSPACE_PATH": str(self.ws),
            "HF_HOME": str(self.hf),
            "EMAIL_ATTACHMENT_STAGING_ROOT": str(self.staging),
            "LUMOGIS_DATA_DIR": str(self.data_dir),
            **extra,
        }
        if not real_passwd:
            env["_LUM_PASSWD_FILE"] = str(self.passwd)
            env["_LUM_GROUP_FILE"] = str(self.group)
        script = _section() + (
            '\nif [ -n "${CHECK_LOOKUP:-}" ]; then\n'
            '  env -u USER -u LOGNAME -u LNAME -u USERNAME "${_LUM_DROP[@]}" "$PY" -c '
            "'import getpass; print(getpass.getuser())'\n"
            "fi\n"
        )
        env["PY"] = sys.executable
        return subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120
        )


@pytest.fixture
def tree():
    # Not pytest's tmp_path: its parent is 0700, so the dropped UID could not
    # traverse to the fixtures and the readability probe would be meaningless.
    base = Path(tempfile.mkdtemp(prefix="lum887-"))
    base.chmod(0o755)
    try:
        yield Tree(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def owner(path: Path) -> tuple[int, int]:
    st = os.lstat(path)
    return st.st_uid, st.st_gid


def test_real_shared_tree_repairs_only_root_owned(tree):
    ws, project = tree.ws, tree.project
    tree.make(project, 1234, 1234, is_dir=True)
    root_txt = tree.make(ws / "root.txt", 0, 0)
    mine = tree.make(ws / "mine.txt", 1234, 1234)
    shared = tree.make(ws / "shared", 0, 5000, is_dir=True, mode=0o2775)
    shared_f = tree.make(ws / "shared" / "f", 1234, 5000)
    snap = tree.make(ws / "backups" / "snapshots" / "s1" / "x", 0, 0)
    for d in (ws / "backups", ws / "backups" / "snapshots", ws / "backups" / "snapshots" / "s1"):
        os.lchown(d, 0, 0)
    zip1 = tree.make(ws / "backups" / "backup_1.zip", 0, 0)
    users = tree.make(ws / "backups" / "users", 0, 0, is_dir=True)
    uzip = tree.make(ws / "backups" / "users" / "u.zip", 0, 0)
    env_f = tree.make(project / ".env", 0, 0)
    lib = tree.make(project / "config" / "librechat.yaml", 0, 0)
    override = tree.make(project / "docker-compose.override.yml", 0, 0)
    readme = tree.make(project / "README.md", 0, 0)

    proc = tree.run()
    assert proc.returncode == 0, proc.stderr

    for p in (root_txt, shared, zip1, users, uzip, env_f, lib, override, ws / "backups"):
        assert owner(p) == (U, U), p
    assert owner(readme) == (0, 0)
    assert owner(mine) == (1234, 1234)
    assert owner(shared_f) == (1234, 5000)
    assert owner(snap) == (0, 0)
    assert owner(project) == (1234, 1234)


def test_real_owned_tree_repairs_any_mismatch(tree):
    a = tree.make(tree.home / "a", 1234, 1234)
    proc = tree.run()
    assert proc.returncode == 0, proc.stderr
    assert owner(a) == (U, U)


def test_real_symlink_target_untouched(tree):
    target = tree.make(tree.base / "outside" / "target", 0, 0)
    link = tree.home / "link"
    link.symlink_to("../outside/target")
    os.lchown(link, 0, 0)
    proc = tree.run()
    assert proc.returncode == 0, proc.stderr
    assert owner(link) == (U, U)
    assert owner(target) == (0, 0)


def test_real_hard_link_skipped(tree):
    h1 = tree.make(tree.home / "h1", 0, 0)
    h2 = tree.home / "h2"
    os.link(h1, h2)
    proc = tree.run()
    assert proc.returncode == 0, proc.stderr
    assert owner(h1) == (0, 0) and owner(h2) == (0, 0)
    assert "skipped 2 hard-linked file(s)" in proc.stderr


def test_real_prune_precedence(tree):
    deep_snap = tree.make(tree.ws / "backups" / "snapshots" / "deep" / "a" / "b" / "c", 0, 0)
    deep_in = tree.make(tree.ws / "inbox" / "deep" / "a" / "b" / "c", 0, 0)
    proc = tree.run()
    assert proc.returncode == 0, proc.stderr
    assert owner(deep_snap) == (0, 0)
    assert owner(deep_in) == (U, U)


def test_real_unreadable_data_dir_warns(tree):
    d = tree.make(tree.base / "data-1", 0, 0, is_dir=True, mode=0o700)
    proc = tree.run(_LUM_DATA_GLOB=str(d))
    assert f"uid {U} cannot read {d}" in proc.stderr
    d.chmod(0o755)
    proc = tree.run(_LUM_DATA_GLOB=str(d))
    assert "cannot read" not in proc.stderr


@pytest.mark.skipif(
    not os.path.exists("/.dockerenv"),
    reason="edits the real /etc/passwd; ephemeral containers only",
)
def test_real_passwd_lookup_after_drop(tree):
    proc = tree.run(real_passwd=True, LUMOGIS_RUN_UID="4243", CHECK_LOOKUP="1")
    assert proc.returncode == 0, proc.stderr
    assert "lumogis-4243" in proc.stdout.splitlines()[-1]
