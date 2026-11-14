# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Behavioural tests of the entrypoint's privilege-drop section (LUM-887).

The section (``# >>> privdrop-section`` ... ``# <<< privdrop-section``) is run
under ``bash`` with PATH stubs for ``id``, ``stat`` (project dir only),
``chown`` and ``setpriv``. ``find`` is the real one, so the selection logic
runs for real; ``chown`` only records what it was asked to do. A non-root test
user cannot create root-owned files, so these tests prove the *owned* rule
(mismatch -> chown) and that the *shared* rule leaves non-root files alone; the
positive shared-tree case is proven with real tools in
``test_entrypoint_privdrop_root.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import stat as statmod
import subprocess
from pathlib import Path

import pytest
from tests.test_secret_sentinels import _REPO_ROOT

_ENTRYPOINT = _REPO_ROOT / "orchestrator" / "docker-entrypoint.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("find") and shutil.which("mktemp")),
    reason="needs bash, find and mktemp",
)

IS_ROOT = os.geteuid() == 0
_NOT_ROOT = 1234  # fixture owner when the test process itself is root


def section() -> str:
    text = _ENTRYPOINT.read_text()
    m = re.search(r"^# >>> privdrop-section\n(.*?)^# <<< privdrop-section$", text, re.S | re.M)
    assert m, "privdrop-section markers missing from docker-entrypoint.sh"
    return m.group(1)


_REAL_FIND = shutil.which("find")
_REAL_STAT = shutil.which("stat")

_STUBS = {
    "id": '#!/bin/sh\necho "${STUB_UID:-0}"\n',
    "stat": f"""#!/bin/sh
for last; do :; done
if [ -n "${{STUB_STAT+x}}" ] && [ "$last" = "$_LUM_PROJECT_DIR" ]; then
  printf '%s\\n' "$STUB_STAT"
  [ -n "$STUB_STAT" ] || exit 1
  exit 0
fi
exec {_REAL_STAT} "$@"
""",
    # find -execdir runs `chown -h U:G ./name ...` from the entry's directory;
    # log absolute paths so tests can name them.
    "chown": """#!/bin/sh
[ "${STUB_CHOWN_FAIL:-0}" = "1" ] && exit 1
line=""
for a in "$@"; do
  case "$a" in
    ./*) line="$line $PWD/${a#./}" ;;
    *) line="$line $a" ;;
  esac
done
echo "$line" >> "$LOGDIR/chown.log"
""",
    "setpriv": """#!/bin/sh
echo "$@" >> "$LOGDIR/setpriv.log"
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--" ]; then shift; break; fi
  shift
done
[ "$#" -gt 0 ] && exec "$@"
exit 0
""",
    "find": f"""#!/bin/sh
echo "$2" >> "$LOGDIR/find.log"
exec {_REAL_FIND} "$@"
""",
}


class Result:
    def __init__(self, proc: subprocess.CompletedProcess, root: Path) -> None:
        self.proc = proc
        self.root = root
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.returncode = proc.returncode

    def _read(self, name: str) -> str:
        p = self.root / name
        return p.read_text() if p.exists() else ""

    @property
    def drop(self) -> str:
        return self._read("drop.txt").strip()

    @property
    def home(self) -> str:
        return self._read("home.txt")

    @property
    def user(self) -> str:
        return self._read("user.txt")

    @property
    def chown_log(self) -> str:
        return self._read("chown.log")

    @property
    def setpriv_log(self) -> str:
        return self._read("setpriv.log")

    @property
    def find_log(self) -> str:
        return self._read("find.log")


class Env:
    """A tmp tree with every hook of the section pointed inside it."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = Path(os.path.realpath(tmp_path))
        r = self.root
        self.project = r / "project"
        self.home = r / "home"
        self.hf = r / "hf"
        self.staging = r / "staging"
        self.data_dir = r / "optdata"
        self.ws = r / "ws"
        self.appcfg = r / "appcfg"
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
        (r / "bin").mkdir()
        (r / "logs").mkdir()
        self.passwd = r / "passwd"
        self.group = r / "group"
        self.passwd.write_text("root:x:0:0:root:/root:/bin/bash\n")
        self.group.write_text("root:x:0:\n")
        self.uid_map = r / "uid_map"
        self.uid_map.write_text("         0          0 4294967295\n")
        self.vars: dict[str, str] = {
            "_LUM_PROJECT_DIR": str(self.project),
            "_LUM_HOME_DIR": str(self.home),
            "_LUM_APP_CONFIG_DIR": str(self.appcfg),
            "_LUM_ROOTFS_DIR": str(r),
            "_LUM_UID_MAP_FILE": str(self.uid_map),
            "_LUM_PASSWD_FILE": str(self.passwd),
            "_LUM_GROUP_FILE": str(self.group),
            "_LUM_DATA_GLOB": str(r / "data-none"),
            "_LUM_DEFAULT_HF_HOME": str(self.hf),
            "_LUM_DEFAULT_STAGING": str(self.staging),
            "_LUM_DEFAULT_DATA_DIR": str(self.data_dir),
            "WORKSPACE_PATH": str(self.ws),
            "HF_HOME": str(self.hf),
            "EMAIL_ATTACHMENT_STAGING_ROOT": str(self.staging),
            "LUMOGIS_DATA_DIR": str(self.data_dir),
        }

    def disown(self, *paths: Path) -> None:
        """When the suite runs as root, give fixtures a non-root owner so the
        ``-user 0`` shared rule must leave them alone."""
        if not IS_ROOT:
            return
        for base in paths:
            for dirpath, dirnames, filenames in os.walk(base):
                for n in [*dirnames, *filenames]:
                    os.lchown(os.path.join(dirpath, n), _NOT_ROOT, _NOT_ROOT)
            os.lchown(base, _NOT_ROOT, _NOT_ROOT)

    def run(self, **extra: str) -> Result:
        r = self.root
        wanted = set(extra.pop("_STUBS", "").split(","))
        for name, body in _STUBS.items():
            if name == "find" and "find" not in wanted:
                continue  # the real find unless a test wants its path logged
            p = r / "bin" / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | statmod.S_IXUSR | statmod.S_IXGRP | statmod.S_IXOTH)
        script = (
            section()
            + f"""
printf '%s ' "${{_LUM_DROP[@]}}" > "{r}/drop.txt"
printf %s "$HOME" > "{r}/home.txt"
printf %s "${{USER-}}" > "{r}/user.txt"
"""
        )
        env = {
            "PATH": f"{r / 'bin'}:{os.environ['PATH']}",
            "HOME": str(r),
            "TMPDIR": str(r),
            "LOGDIR": str(r / "logs"),
            **self.vars,
            **extra,
        }
        proc = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60
        )
        res = Result(proc, r)
        # logs live in logs/; expose them under the plain names the tests read
        for name in ("chown.log", "setpriv.log", "find.log"):
            src = r / "logs" / name
            if src.exists():
                (r / name).write_text(src.read_text())
        return res


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


DROP_1000 = "setpriv --reuid=1000 --regid=1000 --clear-groups --"


def test_drops_to_checkout_owner(env):
    r = env.run(STUB_STAT="1000 1000")
    assert r.drop == DROP_1000
    assert r.home == str(env.home)


def test_root_owned_checkout_drops_to_10001_and_logs_info(env):
    r = env.run(STUB_STAT="0 0")
    assert r.drop == "setpriv --reuid=10001 --regid=10001 --clear-groups --"
    assert "owned by root; running as uid 10001" in r.stdout


def test_missing_project_dir_uses_10001(env):
    shutil.rmtree(env.project)
    r = env.run()
    assert r.drop == "setpriv --reuid=10001 --regid=10001 --clear-groups --"
    assert "owned by root" not in r.stdout


def test_explicit_uid_overrides_detection(env):
    r = env.run(STUB_STAT="1000 1000", LUMOGIS_RUN_UID="1234")
    assert r.drop == "setpriv --reuid=1234 --regid=1234 --clear-groups --"
    r = env.run(STUB_STAT="1000 1000", LUMOGIS_RUN_UID="1234", LUMOGIS_RUN_GID="99")
    assert r.drop == "setpriv --reuid=1234 --regid=99 --clear-groups --"


def test_run_gid_alone_overrides_detected_group(env):
    r = env.run(STUB_STAT="1000 1000", LUMOGIS_RUN_GID="99")
    assert r.drop == "setpriv --reuid=1000 --regid=99 --clear-groups --"


def test_run_uid_trailing_cr_is_trimmed(env):
    r = env.run(LUMOGIS_RUN_UID="1234\r")
    assert r.drop == "setpriv --reuid=1234 --regid=1234 --clear-groups --"
    assert "FATAL" not in r.stderr


@pytest.mark.parametrize("stat_out", ["", "? ?"])
def test_unparseable_project_owner_falls_back_to_10001(env, stat_out):
    r = env.run(STUB_STAT=stat_out)
    assert r.returncode == 0
    assert r.drop == "setpriv --reuid=10001 --regid=10001 --clear-groups --"
    assert "could not read the owner" in r.stderr


@pytest.mark.parametrize("uid", [None, "1234"])
def test_user_namespace_skips_drop_and_repair(env, uid):
    env.uid_map.write_text("         0       1000          1\n         1     100000      65536\n")
    extra = {"STUB_STAT": "1000 1000"}
    if uid:
        extra["LUMOGIS_RUN_UID"] = uid  # an explicit uid does not bypass the guard
    r = env.run(**extra)
    assert r.drop == ""
    assert r.chown_log == ""
    assert "user namespace detected" in r.stdout


def test_passwd_and_group_lines_added_once(env):
    r = env.run(STUB_STAT="1000 1000")
    lines = [ln for ln in env.passwd.read_text().splitlines() if ln.startswith("lumogis-1000:")]
    assert lines == [f"lumogis-1000:x:1000:1000:Lumogis runtime user:{env.home}:/usr/sbin/nologin"]
    assert [ln for ln in env.group.read_text().splitlines() if ln.startswith("lumogis-1000:")] == [
        "lumogis-1000:x:1000:"
    ]
    assert r.user == "lumogis-1000"
    env.run(STUB_STAT="1000 1000")
    assert env.passwd.read_text().count("lumogis-1000:") == 1
    assert env.group.read_text().count("lumogis-1000:") == 1


def test_existing_passwd_entry_is_reused(env):
    env.passwd.write_text(
        "root:x:0:0:root:/root:/bin/bash\nlumogis:x:10001:10001::/var/lib/lumogis/home:/usr/sbin/nologin\n"
    )
    env.group.write_text("root:x:0:\nlumogis:x:10001:\n")
    before = (env.passwd.read_text(), env.group.read_text())
    r = env.run(STUB_STAT="0 0")
    assert (env.passwd.read_text(), env.group.read_text()) == before
    assert r.user == "lumogis"


def test_run_uid_zero_keeps_root_and_warns(env):
    r = env.run(STUB_STAT="1000 1000", LUMOGIS_RUN_UID="0")
    assert r.drop == ""
    assert r.chown_log == ""
    assert "runs as root" in r.stderr


def test_non_numeric_run_uid_is_fatal(env):
    r = env.run(LUMOGIS_RUN_UID="thomas")
    assert r.returncode == 1
    assert "FATAL" in r.stderr


def test_already_non_root_skips_everything(env):
    r = env.run(STUB_UID="1000", STUB_STAT="1000 1000")
    assert r.drop == ""
    assert r.chown_log == ""
    assert r.setpriv_log == ""


def test_owned_trees_repaired_on_mismatch(env):
    files = []
    for d in (env.home, env.hf, env.staging, env.data_dir):
        f = d / "f.txt"
        f.write_text("x")
        files.append(str(f))
    r = env.run(STUB_STAT="4242 4242")
    assert r.drop.startswith("setpriv --reuid=4242 --regid=4242")
    for f in files:
        assert f in r.chown_log, f
    assert "-h 4242:4242" in r.chown_log


def test_shared_trees_leave_non_root_files_alone(env):
    (env.ws / "inbox").mkdir()
    (env.ws / "inbox" / "a.txt").write_text("x")
    (env.ws / "backups" / "users").mkdir(parents=True)
    (env.ws / "backups" / "users" / "u.zip").write_text("x")
    (env.ws / "backups" / "backup_1.zip").write_text("x")
    (env.project / ".env").write_text("A=1\n")
    (env.appcfg / "stop_entities.txt").write_text("x")
    env.disown(env.ws, env.project, env.appcfg)
    r = env.run(STUB_STAT="4242 4242")
    assert r.drop.startswith("setpriv --reuid=4242")
    for name in ("a.txt", "u.zip", "backup_1.zip", ".env", "stop_entities.txt"):
        assert name not in r.chown_log, name


def test_moved_owned_path_is_treated_as_shared(env):
    moved = env.root / "moved-hf"
    moved.mkdir()
    (moved / "w.bin").write_text("x")
    env.disown(moved)
    r = env.run(STUB_STAT="4242 4242", HF_HOME=str(moved))
    assert "w.bin" not in r.chown_log


def test_app_config_on_other_device_is_skipped(env):
    (env.appcfg / "stop_entities.txt").write_text("x")
    r = env.run(STUB_STAT="4242 4242", _STUBS="find", _LUM_ROOTFS_DIR="/proc")
    assert str(env.appcfg) not in r.find_log
    r = env.run(STUB_STAT="4242 4242", _STUBS="find")
    assert str(env.appcfg) in r.find_log


def test_stop_entities_list_is_repaired_at_its_workspace_default(env):
    # LUM-922: the admin's list moved from /app/config to <workspace>/config.
    target = env.ws / "config" / "stop_entities.txt"
    target.parent.mkdir()
    target.write_text("x")
    r = env.run(STUB_STAT="4242 4242", _STUBS="find")
    assert str(target) in r.find_log.splitlines()


def test_moved_stop_entities_path_repairs_the_file_not_its_folder(env):
    folder = env.root / "etc-like"
    folder.mkdir()
    (folder / "other.conf").write_text("x")
    moved = folder / "stop_entities.txt"
    moved.write_text("x")
    r = env.run(STUB_STAT="4242 4242", _STUBS="find", STOP_ENTITIES_PATH=str(moved))
    walked = r.find_log.splitlines()
    assert str(moved) in walked
    assert str(folder) not in walked
    assert "other.conf" not in r.chown_log


def test_repair_does_not_follow_symlinks(env):
    (env.home / "link").symlink_to("/etc/hostname")
    r = env.run(STUB_STAT="4242 4242")
    link_lines = [ln for ln in r.chown_log.splitlines() if f"{env.home}/link" in ln]
    assert link_lines and all(" -h " in f" {ln} " for ln in link_lines)
    assert "/etc/hostname" not in r.chown_log


def test_repair_skips_hard_linked_files(env):
    (env.home / "h1").write_text("x")
    os.link(env.home / "h1", env.home / "h2")
    (env.home / "p").write_text("x")
    r = env.run(STUB_STAT="4242 4242")
    assert f"{env.home}/p" in r.chown_log
    assert f"{env.home}/h1" not in r.chown_log
    assert f"{env.home}/h2" not in r.chown_log
    assert r.stderr.count("skipped 2 hard-linked file(s) under") == 1
    assert r.drop.startswith("setpriv --reuid=4242")


def test_hard_links_the_repair_would_not_touch_do_not_warn(env):
    # VERIFY-PLAN: added. A person's own hard links in a shared tree are never
    # re-owned (shared rule = root-owned only), so they must not be reported as
    # "skipped" either: that WARNING would ask them to fix an owner that is fine.
    (env.ws / "k1").write_text("x")
    os.link(env.ws / "k1", env.ws / "k2")
    env.disown(env.ws)
    r = env.run(STUB_STAT="4242 4242")
    assert "hard-linked" not in r.stderr
    assert "k1" not in r.chown_log and "k2" not in r.chown_log
    assert r.drop.startswith("setpriv --reuid=4242")


def test_chown_failure_warns_and_continues(env):
    (env.home / "f").write_text("x")
    r = env.run(STUB_STAT="4242 4242", STUB_CHOWN_FAIL="1")
    assert "could not repair ownership" in r.stderr
    assert r.drop.startswith("setpriv --reuid=4242")


@pytest.mark.skipif(
    IS_ROOT, reason="root reads a chmod 000 dir; the real probe is in the root file"
)
def test_unreadable_data_dir_warns(env):
    d = env.root / "data-1"
    d.mkdir()
    d.chmod(0)
    try:
        r = env.run(STUB_STAT="1000 1000", _LUM_DATA_GLOB=str(d))
    finally:
        d.chmod(0o755)
    assert "cannot read" in r.stderr
    assert r.drop == DROP_1000


def test_final_exec_uses_drop_prefix():
    lines = [
        ln
        for ln in _ENTRYPOINT.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert lines[-1] == 'exec "${_LUM_DROP[@]}" "$@"'
    text = _ENTRYPOINT.read_text()
    assert text.index("# >>> privdrop-section") > text.index("end OLLAMA_SKIP_WAIT guard")
