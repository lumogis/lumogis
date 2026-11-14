# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-922 — scripts/update/rescue-stop-entities.sh against a fake ``docker``.

The script and ``common.sh`` are copied into a tmp checkout, so ``.env``,
``./ai-workspace`` and ``.lumogis-state`` are all inside ``tmp_path``. The fake
``docker`` answers ``compose ps``, ``inspect`` (env, mounts), ``diff`` and
``cp`` from env vars and a fake container filesystem. A real-container run is
in ``scripts/integration-backup-roundtrip.sh``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SRC = "/app/config/stop_entities.txt"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")

_FAKE_DOCKER = r"""#!/usr/bin/env bash
case "$1" in
  compose) [ -n "${FAKE_CID:-}" ] && echo "$FAKE_CID"; exit 0 ;;
  inspect)
    case "$3" in
      *Config.Env*) [ -n "${FAKE_ENV:-}" ] && printf '%s\n' "$FAKE_ENV"; exit 0 ;;
      *Mounts*) [ -n "${FAKE_MOUNTS:-}" ] && printf '%s\n' "$FAKE_MOUNTS"; exit 0 ;;
      *Config.Image*) echo "lumogis-orchestrator:new"; exit 0 ;;
      *.Image*) echo "sha256:fakeimage"; exit 0 ;;
    esac
    exit 1 ;;
  create)
    # never-started containers: the old image's files are FAKE_IMAGE_FS (unset =
    # the old image is gone, as on the containerd store once its tag moved); the
    # image the tag names now is FAKE_TAG_FS
    [ -n "${FAKE_IMAGE_FS:-}" ] && [ "$2" = "sha256:fakeimage" ] && echo pristine0000 && exit 0
    [ -n "${FAKE_TAG_FS:-}" ] && [ "$2" = "lumogis-orchestrator:new" ] && echo pristinetag && exit 0
    echo "Error response from daemon: No such image: $2" >&2
    exit 1 ;;
  rm) echo "$*" >> "$FAKE_LOG"; exit 0 ;;
  diff)
    [ "${FAKE_DIFF_FAIL:-0}" = 1 ] && exit 1
    [ -n "${FAKE_DIFF:-}" ] && printf '%s\n' "$FAKE_DIFF"
    exit 0 ;;
  cp)
    echo "$*" >> "$FAKE_LOG"
    src="${2#*:}"
    case "${2%%:*}" in
      pristine0000) fs="$FAKE_IMAGE_FS" ;;
      pristinetag) fs="$FAKE_TAG_FS" ;;
      *) fs="" ;;
    esac
    if [ -n "$fs" ]; then
      if [ -e "$fs$src" ]; then cp "$fs$src" "$3"; exit 0; fi
      echo "Error response from daemon: Could not find the file $src in container ${2%%:*}" >&2
      exit 1
    fi
    if [ -e "$FAKE_FS$src" ]; then cp "$FAKE_FS$src" "$3"; exit 0; fi
    echo "Error response from daemon: Could not find the file $src in container x" >&2
    exit 1 ;;
esac
exit 1
"""


class Checkout:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        upd = self.root / "scripts" / "update"
        upd.mkdir(parents=True)
        for name in ("rescue-stop-entities.sh", "common.sh"):
            shutil.copy(_ROOT / "scripts" / "update" / name, upd / name)
        self.script = upd / "rescue-stop-entities.sh"
        self.fs = tmp_path / "container-fs"
        (self.fs / "app" / "config").mkdir(parents=True)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(_FAKE_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "docker.log"
        self.dest = self.root / "ai-workspace" / "config" / "stop_entities.txt"
        self.state = self.root / ".lumogis-state"

    def container_list(self, text: str, path: str = _SRC) -> None:
        p = self.fs / path.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def run(self, **env: str) -> subprocess.CompletedProcess:
        full = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "FAKE_FS": str(self.fs),
            "FAKE_LOG": str(self.log),
            "FAKE_CID": "0123456789abcdef0123",
            "FAKE_DIFF": f"C /app/config\nC {_SRC}",
            "FAKE_MOUNTS": "/project\n/workspace\n/workspace/backups",
            **env,
        }
        full = {k: v for k, v in full.items() if v is not None}
        return subprocess.run(
            ["bash", str(self.script)], env=full, capture_output=True, text=True, timeout=60
        )


@pytest.fixture
def co(tmp_path: Path) -> Checkout:
    return Checkout(tmp_path)


def test_no_container_is_nothing_to_rescue(co):
    r = co.run(FAKE_CID="")
    assert r.returncode == 0, r.stderr
    assert "no orchestrator container" in r.stdout
    assert not co.dest.exists()


def test_edited_list_is_copied_into_the_workspace(co):
    co.container_list("my curated phrase\n")
    r = co.run()
    assert r.returncode == 0, r.stderr
    assert co.dest.read_text(encoding="utf-8") == "my curated phrase\n"
    assert "rescued the stop-entities list" in r.stdout
    # the staging folder is cleaned up
    assert [p.name for p in co.state.iterdir()] == []


def test_second_run_finds_it_already_there(co):
    co.container_list("my curated phrase\n")
    assert co.run().returncode == 0
    r = co.run()
    assert r.returncode == 0, r.stderr
    assert "already at" in r.stdout


def test_existing_different_list_is_never_overwritten(co):
    co.container_list("old container phrase\n")
    co.dest.parent.mkdir(parents=True)
    co.dest.write_text("newer workspace phrase\n", encoding="utf-8")
    r = co.run()
    assert r.returncode == 0, r.stderr
    assert co.dest.read_text(encoding="utf-8") == "newer workspace phrase\n"
    kept = co.state / "stop_entities.from-container-0123456789ab.txt"
    assert kept.read_text(encoding="utf-8") == "old container phrase\n"
    assert "it was kept" in r.stderr and str(kept) in r.stderr


def test_unedited_list_is_left_to_the_new_image(co):
    co.container_list("shipped phrase\n")
    r = co.run(FAKE_DIFF="C /app/config\nA /app/config/.stop_entities_x.tmp")
    assert r.returncode == 0, r.stderr
    assert "not edited in this container" in r.stdout
    assert not co.dest.exists()
    assert not co.log.exists()  # no docker cp at all


def _image_list(co, text: str, name: str = "image-fs") -> str:
    """An image's own copy of the list (what `docker create` exposes)."""
    img = co.fs.parent / name
    p = img / _SRC.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(img)


def test_owner_only_change_matches_the_image_and_is_not_rescued(co):
    # LUM-900: the LUM-887 entrypoint chowns /app/config at boot, so `docker
    # diff` reports the list as C even when nobody edited it. Its content equals
    # the image's copy: nothing to rescue, and no warning that would tell the
    # admin to overwrite their workspace list with the shipped default.
    co.container_list("shipped phrase\n")
    image_fs = _image_list(co, "shipped phrase\n")
    co.dest.parent.mkdir(parents=True)
    co.dest.write_text("shipped phrase\nmy edit\n", encoding="utf-8")
    r = co.run(FAKE_IMAGE_FS=image_fs)
    assert r.returncode == 0, r.stderr
    assert "matches its image" in r.stdout
    assert "it was kept" not in r.stderr
    assert co.dest.read_text(encoding="utf-8") == "shipped phrase\nmy edit\n"
    assert [p.name for p in co.state.iterdir()] == []
    assert "rm -v pristine0000" in co.log.read_text()


def test_old_image_gone_compares_with_the_image_its_tag_names(co):
    # On the containerd image store the old image ID stops resolving once
    # `make update` has pulled or built a new one under the same tag.
    co.container_list("shipped phrase\n")
    tag_fs = _image_list(co, "shipped phrase\n", "tag-fs")
    co.dest.parent.mkdir(parents=True)
    co.dest.write_text("shipped phrase\nmy edit\n", encoding="utf-8")
    r = co.run(FAKE_TAG_FS=tag_fs)
    assert r.returncode == 0, r.stderr
    assert "matches its image" in r.stdout
    assert "it was kept" not in r.stderr
    assert co.dest.read_text(encoding="utf-8") == "shipped phrase\nmy edit\n"
    assert "rm -v pristinetag" in co.log.read_text()


def test_no_image_copy_readable_keeps_the_never_overwrite_merge(co):
    co.container_list("old container phrase\n")
    co.dest.parent.mkdir(parents=True)
    co.dest.write_text("newer workspace phrase\n", encoding="utf-8")
    r = co.run()  # neither image can be created
    assert r.returncode == 0, r.stderr
    assert co.dest.read_text(encoding="utf-8") == "newer workspace phrase\n"
    assert "it was kept" in r.stderr


def test_content_edit_still_differs_from_the_image_and_is_rescued(co):
    co.container_list("shipped phrase\nedited in the container\n")
    image_fs = _image_list(co, "shipped phrase\n")
    r = co.run(FAKE_IMAGE_FS=image_fs)
    assert r.returncode == 0, r.stderr
    assert co.dest.read_text(encoding="utf-8") == "shipped phrase\nedited in the container\n"
    assert "rescued the stop-entities list" in r.stdout
    assert "rm -v pristine0000" in co.log.read_text()


def test_added_file_counts_as_edited(co):
    co.container_list("added phrase\n")
    r = co.run(FAKE_DIFF=f"A {_SRC}")
    assert r.returncode == 0, r.stderr
    assert co.dest.read_text(encoding="utf-8") == "added phrase\n"


def test_unreadable_diff_still_copies(co):
    co.container_list("phrase\n")
    r = co.run(FAKE_DIFF_FAIL="1")
    assert r.returncode == 0, r.stderr
    assert co.dest.read_text(encoding="utf-8") == "phrase\n"


def test_list_on_a_mount_is_nothing_to_rescue(co):
    co.container_list("dev overlay phrase\n")
    r = co.run(FAKE_MOUNTS="/app\n/workspace")
    assert r.returncode == 0, r.stderr
    assert "on a mounted path" in r.stdout
    assert not co.dest.exists()


def test_file_bind_mount_is_nothing_to_rescue(co):
    co.container_list("bind-mounted phrase\n")
    r = co.run(FAKE_MOUNTS=f"/workspace\n{_SRC}")
    assert r.returncode == 0, r.stderr
    assert "on a mounted path" in r.stdout
    assert not co.dest.exists()


def test_explicit_path_in_the_workspace_is_nothing_to_rescue(co):
    r = co.run(FAKE_ENV="FOO=1\nSTOP_ENTITIES_PATH=/workspace/config/stop_entities.txt")
    assert r.returncode == 0, r.stderr
    assert "on a mounted path" in r.stdout


def test_explicit_unmounted_path_is_copied_then_stops_the_update(co):
    co.container_list("pinned phrase\n", "/opt/kg/stop.txt")
    r = co.run(
        FAKE_ENV="STOP_ENTITIES_PATH=/opt/kg/stop.txt",
        FAKE_DIFF="A /opt/kg\nA /opt/kg/stop.txt",
    )
    assert r.returncode == 1
    assert co.dest.read_text(encoding="utf-8") == "pinned phrase\n"
    assert "remove STOP_ENTITIES_PATH from .env" in r.stderr


def test_missing_file_in_the_container_is_nothing_to_rescue(co):
    r = co.run()
    assert r.returncode == 0, r.stderr
    assert "does not exist in the container" in r.stdout
    assert [p.name for p in co.state.iterdir()] == []


def test_workspace_host_dir_from_dot_env(co):
    co.container_list("phrase\n")
    (co.root / ".env").write_text('WORKSPACE_HOST_DIR="./elsewhere"\n', encoding="utf-8")
    r = co.run()
    assert r.returncode == 0, r.stderr
    assert (co.root / "elsewhere" / "config" / "stop_entities.txt").is_file()
    assert not co.dest.exists()


def test_container_env_is_never_printed(co):
    co.container_list("phrase\n")
    r = co.run(FAKE_ENV="POSTGRES_PASSWORD=hunter2-secret\nAUTH_SECRET=s3cr3t")
    assert r.returncode == 0, r.stderr
    assert "hunter2" not in r.stdout + r.stderr
    assert "s3cr3t" not in r.stdout + r.stderr
