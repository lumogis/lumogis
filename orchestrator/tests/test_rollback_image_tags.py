# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-984 — `make rollback` restores the previous images after `make update`.

`make update` rebuilds the Lumogis images under the same compose tag, so the old
image lost its only name. The update now tags every running service's image
``lumogis-rollback/<project>/<service>:<UTC time>`` first, and `make rollback`
pins that local tag (``pull_policy: never``).

Two kinds of test live here:

* **F-tests** run the real shell functions in ``scripts/update/`` against a fake
  ``docker`` on ``PATH``. They prove script logic (which refs are tagged, kept,
  pulled, pruned) and never image-store behaviour (LUM-900 lesson).
* **D-tests** run against the real Docker daemon: build v1, capture, rebuild v2,
  and check the override still runs v1. In CI an unreachable daemon is a
  *failure* (ADR 257), locally a skip.

Known gap (vacuous-test principle): the classic (non-containerd) image store and
macOS / Docker Desktop are not exercised; ``test_*`` D-tests print the storage
driver so a CI log shows which store was covered. Portability of the new bash is
guarded statically (``test_deployment_hardening_config.py``).

Leaks: the fixture's container is labelled ``lumogis.test=lum984`` and runs
``sleep 3600``, so a run killed before its finaliser leaves a container that stops
itself within an hour. Manual sweep (no automatic one: the CI runners share one
daemon, so a start-of-session sweep could delete the other runner's live test)::

    docker ps -aq --filter label=lumogis.test=lum984 | xargs -r docker rm -f
    docker image ls --format '{{.Repository}}:{{.Tag}}' \
      | grep -E '^(lumogis-lum984-test/|lumogis-rollback/lum984-)' | xargs -r docker rmi
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_UPDATE_DIR = _ROOT / "scripts" / "update"
# Step 1 of the plan runs D1 against the pre-change script (git show HEAD:...).
_COMMON_SH = Path(os.environ.get("LUM984_COMMON_SH", str(_UPDATE_DIR / "common.sh")))
_WRITER = _UPDATE_DIR / "write_rollback_override.py"
_ROLLBACK_SH = _UPDATE_DIR / "rollback.sh"

_TS = "20261101T101500Z"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _run(args, timeout=120, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(args, timeout=timeout, **kw)


def _base_image() -> str:
    for line in (_ROOT / "orchestrator" / "Dockerfile").read_text().splitlines():
        if line.startswith("FROM "):
            return line.split()[1]
    raise AssertionError("FROM line not found in orchestrator/Dockerfile")


# ----------------------------------------------------------------------------
# Fake docker on PATH (script logic only; never image-store behaviour)
# ----------------------------------------------------------------------------

_FAKE_DOCKER = r"""#!/usr/bin/env bash
d="$FAKE_DIR"
echo "$*" >>"$d/log"
case "$1 $2" in
  "compose version") exit 0 ;;
  "compose config") cat "$d/services" 2>/dev/null; exit 0 ;;
  "compose ps") cat "$d/ps_$4" 2>/dev/null; exit 0 ;;
  "compose port") exit 0 ;;
  "compose exec") echo "database is up to date"; exit 0 ;;
  "compose logs") exit 0 ;;
  "compose run") cat "$d/run_out" 2>/dev/null; exit 0 ;;
  "compose up")
    echo "COMPOSE_FILE=$COMPOSE_FILE" >>"$d/log"
    exit "$(cat "$d/up_rc" 2>/dev/null || echo 0)" ;;
esac
case "$1" in
  inspect)
    if [[ -f "$d/inspect_fail" && "${@: -1}" == "$(cat "$d/inspect_fail")" ]]; then
      echo "Error response from daemon: No such object: ${@: -1}" >&2; exit 1
    fi
    cat "$d/inspect_${@: -1}"; exit 0 ;;
  tag)
    n=$(( $(cat "$d/tag_n" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$d/tag_n"
    if [[ -f "$d/tag_fail_at" && "$n" == "$(cat "$d/tag_fail_at")" ]]; then
      cat "$d/tag_err" >&2; exit 1
    fi
    exit 0 ;;
  image)
    case "$2" in
      inspect) [[ -f "$d/image_inspect_fail" ]] && exit 1; exit 0 ;;
      ls) cat "$d/image_ls" 2>/dev/null; exit 0 ;;
    esac ;;
  pull) exit 0 ;;
  rmi) [[ -f "$d/rmi_fail" ]] && exit 1; exit 0 ;;
esac
echo "unexpected docker call: $*" >&2
exit 99
"""

_FAKE_CURL = '#!/usr/bin/env bash\nexit "$(cat "$FAKE_DIR/curl_rc" 2>/dev/null || echo 0)"\n'


class Fake:
    """A fake `docker` + `curl` on PATH, driven by files in one directory."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("docker", _FAKE_DOCKER), ("curl", _FAKE_CURL)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        self.state = self.state_dir / "previous-images.txt"
        self.override = self.state_dir / "rollback-compose.override.yml"
        self.marker = self.state_dir / "update-in-progress"
        self.home = tmp_path

    def set(self, name: str, text: str = "") -> None:
        (self.dir / name).write_text(text)

    def services(self, *names: str) -> None:
        self.set("services", "".join(f"{n}\n" for n in names))

    def running(self, svc, image="ghcr.io/x/y:dev", image_id=None, project="lumogis") -> None:
        cid = f"cid-{svc}"
        self.set(f"ps_{svc}", cid + "\n")
        self.set(f"inspect_{cid}", f"{image}\t{image_id or 'sha256:' + svc}\t{project}\n")

    def log(self) -> list[str]:
        f = self.dir / "log"
        return f.read_text().splitlines() if f.exists() else []

    def env(self, **extra) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_DIR": str(self.dir),
            "HOME": str(self.home),
            "LUMOGIS_HEALTH_URL": "http://127.0.0.1:9/healthz",
            "ROOT": str(self.home),
        }
        # CI's setup-python python3 needs its libpython from LD_LIBRARY_PATH;
        # without it rollback.sh's python3 call fails on the self-hosted runner.
        if "LD_LIBRARY_PATH" in os.environ:
            env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
        env.update(extra)
        return env

    def bash(self, script: str, *args, flags="set -euo pipefail", **env):
        return _run(
            ["bash", "-c", f'{flags}\nsource "{_COMMON_SH}"\n{script}', "_", *args],
            env=self.env(**env),
            cwd=self.home,
            timeout=60,
        )

    def capture(self, **env):
        return self.bash(
            'capture_rollback_state "$1" "$2"; echo "kept=${ROLLBACK_KEPT_FROM:-}"',
            str(self.state),
            str(self.override),
            **env,
        )

    def rollback(self, **env):
        return _run(
            ["bash", str(_ROLLBACK_SH)],
            env=self.env(
                LUMOGIS_ASSUME_YES="1",
                LUMOGIS_UPDATE_STATE_DIR=str(self.state_dir),
                LUMOGIS_BACKUP_MAX_AGE_HOURS="1",
                **env,
            ),
            cwd=self.home,
            timeout=60,
        )


@pytest.fixture
def fake(tmp_path):
    return Fake(tmp_path)


def _ref(project="lumogis", svc="orchestrator", ts=_TS):
    return f"lumogis-rollback/{project}/{svc}:{ts}"


def _ref_re() -> re.Pattern:
    out = _run(["bash", "-c", f'source "{_COMMON_SH}"; printf %s "$ROLLBACK_REF_RE"']).stdout
    return re.compile(out)


def _lines(text: str) -> list[list[str]]:
    return [ln.split("\t") for ln in text.splitlines() if ln]


# ----------------------------------------------------------------------------
# F1-F3, F8-F10, F13 — capture
# ----------------------------------------------------------------------------


def test_capture_tags_each_running_image_and_records_the_tag(fake):
    fake.services("orchestrator", "postgres", "ollama")  # ollama is not running
    fake.running("orchestrator", image_id="sha256:aaa")
    fake.running("postgres", image="postgres:16.8", image_id="sha256:bbb")
    res = fake.capture()
    assert res.returncode == 0, res.stderr
    rows = _lines(fake.state.read_text())
    assert [r[0] for r in rows] == ["orchestrator", "postgres"]
    assert re.fullmatch(r"lumogis-rollback/lumogis/orchestrator:\d{8}T\d{6}Z", rows[0][2])
    assert re.fullmatch(r"lumogis-rollback/lumogis/postgres:\d{8}T\d{6}Z", rows[1][2])
    assert rows[0][2].rsplit(":", 1)[1] == rows[1][2].rsplit(":", 1)[1], "one timestamp per capture"
    assert rows[0][1] == "ghcr.io/x/y:dev"
    assert f"tag sha256:aaa {rows[0][2]}" in fake.log()
    assert fake.state.stat().st_mode & 0o777 == 0o644
    assert not fake.override.exists(), "capture writes no override (D6)"
    assert fake.marker.exists()
    assert [
        p.name for p in fake.state_dir.iterdir() if p.name.startswith("previous-images.txt.")
    ] == []
    assert "kept=\n" in res.stdout


def test_capture_normalises_project_and_service_names(fake):
    fake.services("Lumogis_Web")
    fake.running("Lumogis_Web", project="My_Proj.")
    assert fake.capture().returncode == 0
    ref = _lines(fake.state.read_text())[0][2]
    assert _ref_re().fullmatch(ref), ref
    _, project, rest = ref.split("/")
    assert re.fullmatch(r"my-proj-\d+", project), ref
    assert re.fullmatch(r"lumogis-web-\d+:\d{8}T\d{6}Z", rest), ref


def test_capture_stops_and_keeps_previous_state_when_tag_fails(fake):
    fake.services("orchestrator", "postgres")
    fake.running("orchestrator")
    fake.running("postgres")
    fake.set("tag_fail_at", "2")
    fake.set("tag_err", "Error response from daemon: No such image: sha256:dead\n")
    before_state = f"orchestrator\timg\t{_ref()}\n"
    fake.state.write_text(before_state)
    fake.override.write_text("# old override\n")
    res = fake.capture()
    assert res.returncode != 0
    for needle in (
        "could not keep the running",
        "No such image: sha256:dead",
        "Most likely",
        "cannot be rolled back to",
        "make build",
    ):
        assert needle in res.stderr, (needle, res.stderr)
    assert "docker compose up -d" not in res.stderr
    assert any(ln.startswith("rmi lumogis-rollback/lumogis/orchestrator:") for ln in fake.log())
    assert fake.state.read_text() == before_state
    assert fake.override.read_text() == "# old override\n"
    assert [
        p.name for p in fake.state_dir.iterdir() if p.name.startswith("previous-images.txt.")
    ] == []
    assert not fake.marker.exists()


def test_capture_cleans_up_when_a_container_vanishes_before_inspect(fake):
    fake.services("orchestrator", "postgres")
    fake.running("orchestrator")
    fake.running("postgres")
    fake.set("inspect_fail", "cid-postgres")
    before = f"orchestrator\timg\t{_ref()}\n"
    fake.state.write_text(before)
    res = fake.capture()
    assert res.returncode != 0
    assert "could not read the running postgres container" in res.stderr, res.stderr
    assert any(ln.startswith("rmi lumogis-rollback/lumogis/orchestrator:") for ln in fake.log())
    assert fake.state.read_text() == before
    assert [
        p.name for p in fake.state_dir.iterdir() if p.name.startswith("previous-images.txt.")
    ] == []


def test_names_that_normalise_alike_stay_distinct(fake):
    fake.services("a_b", "a-b")
    fake.running("a_b")
    fake.running("a-b")
    assert fake.capture().returncode == 0
    refs = {r[0]: r[2] for r in _lines(fake.state.read_text())}
    assert refs["a_b"] != refs["a-b"]
    rx = _ref_re()
    assert rx.fullmatch(refs["a_b"]) and rx.fullmatch(refs["a-b"])
    assert refs["a-b"].startswith("lumogis-rollback/lumogis/a-b:"), "already valid: unsuffixed"
    res = fake.bash(
        'a="$(_rollback_name_part lumogis_test)"; b="$(_rollback_name_part lumogis-test)"; '
        '[[ "$a" != "$b" ]] && echo "$a $b"'
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.split()[1] == "lumogis-test"


def test_capture_keeps_the_state_of_an_unfinished_update_while_unhealthy(fake):
    fake.services("orchestrator")
    fake.running("orchestrator")
    before = f"orchestrator\timg\t{_ref()}\n"
    fake.state.write_text(before)
    fake.marker.write_text("")
    fake.set("curl_rc", "7")
    res = fake.capture()
    assert res.returncode == 0, res.stderr
    assert not any(ln.startswith("inspect ") or ln.startswith("tag ") for ln in fake.log())
    assert fake.state.read_text() == before
    assert fake.marker.exists()
    assert _TS in res.stdout and str(fake.marker) in res.stdout
    assert res.stdout.rstrip().endswith(f"kept={_TS}")


def test_capture_records_running_images_when_the_unfinished_update_is_healthy_now(fake):
    fake.services("orchestrator")
    fake.running("orchestrator")
    fake.state.write_text(f"orchestrator\timg\t{_ref()}\n")
    fake.marker.write_text("")
    res = fake.capture()  # curl exits 0
    assert res.returncode == 0, res.stderr
    assert any(ln.startswith("inspect ") for ln in fake.log())
    assert any(ln.startswith("tag ") for ln in fake.log())
    ref = _lines(fake.state.read_text())[0][2]
    assert ref.startswith("lumogis-rollback/lumogis/orchestrator:") and not ref.endswith(_TS)
    assert fake.marker.exists()
    assert "healthy now" in res.stdout
    assert res.stdout.rstrip().endswith("kept=")


def test_capture_with_no_running_services_removes_the_rollback_state(fake):
    fake.services("orchestrator", "postgres")  # nothing has a container
    fake.state.write_text(f"orchestrator\timg\t{_ref()}\n")
    fake.override.write_text("# old override\n")
    res = fake.capture()
    assert res.returncode == 0, res.stderr
    assert "WARN" in res.stderr and "nothing to roll back to" in res.stderr
    assert not fake.state.exists() and not fake.override.exists() and not fake.marker.exists()


# ----------------------------------------------------------------------------
# F4-F6, F11, F12 — pull filter and prune
# ----------------------------------------------------------------------------

_T1, _T2 = "20261116T090000Z", "20261116T101500Z"


def test_rollback_pulls_only_registry_refs(fake):
    fake.state.write_text(
        f"orchestrator\timg\t{_ref(ts=_T2)}\nlumogis-web\timg\tghcr.io/lumogis/lumogis-web@sha256:def\n"
    )
    res = fake.bash('pull_rollback_registry_refs "$1"', str(fake.state))
    assert res.returncode == 0, res.stderr
    assert [ln for ln in fake.log() if ln.startswith("pull ")] == [
        "pull ghcr.io/lumogis/lumogis-web@sha256:def"
    ]


def test_prune_only_touches_this_projects_old_tags(fake):
    fake.state.write_text(f"orchestrator\timg\t{_ref(ts=_T2)}\n")
    fake.set(
        "image_ls",
        "\n".join(
            [
                _ref(ts=_T2),
                _ref(ts=_T1),
                _ref(project="lumogis-test", ts=_T1),
                "ghcr.io/lumogis/lumogis-orchestrator:dev-lumogis",
            ]
        )
        + "\n",
    )
    res = fake.bash('prune_rollback_tags "$1"', str(fake.state))
    assert res.returncode == 0, res.stderr
    assert [ln for ln in fake.log() if ln.startswith("rmi ")] == [f"rmi {_ref(ts=_T1)}"]
    fake.set("rmi_fail")
    failing = fake.bash('prune_rollback_tags "$1"', str(fake.state))
    assert failing.returncode == 0
    assert "WARN" in failing.stderr


def test_prune_with_old_style_state_removes_nothing(fake):
    fake.state.write_text("orchestrator\timg\tghcr.io/lumogis/lumogis-orchestrator@sha256:abc\n")
    assert fake.bash('prune_rollback_tags "$1"', str(fake.state)).returncode == 0
    assert not any(ln.startswith(("image ls", "rmi ")) for ln in fake.log())


@pytest.mark.parametrize("content", [None, ""])
def test_prune_without_state_does_nothing(fake, content):
    path = fake.state_dir / "missing.txt"
    if content is not None:
        path.write_text(content)
    res = fake.bash('prune_rollback_tags "$1"; echo after', str(path))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "after"
    assert fake.log() == []


def test_prune_ignores_refs_outside_the_grammar(fake):
    fake.state.write_text(
        f"orchestrator\timg\tlumogis-rollback/orchestrator:{_T2}\n"
        f"lumogis-web\timg\tlumogis-rollback//x:{_T2}\n"
    )
    res = fake.bash('prune_rollback_tags "$1"', str(fake.state))
    assert res.returncode == 0, res.stderr
    assert not any(ln.startswith(("image ls", "rmi ")) for ln in fake.log())


# ----------------------------------------------------------------------------
# F7, F14, F15 — the real rollback.sh
# ----------------------------------------------------------------------------


def test_rollback_stops_on_missing_tag_before_the_backup_check(fake):
    fake.state.write_text(f"orchestrator\timg\t{_ref()}\n")
    fake.set("image_inspect_fail")
    res = fake.rollback()
    assert res.returncode != 0
    assert "orchestrator" in res.stderr and "prune -a" in res.stderr, res.stderr
    log = fake.log()
    assert not any(ln.startswith(("compose run", "pull ", "compose up")) for ln in log), log


def _rollback_fixture(fake):
    fake.state.write_text(
        f"orchestrator\timg\t{_ref()}\npostgres\tpostgres:16.8\tpostgres@sha256:abc\n"
    )
    fake.marker.write_text("")
    fake.set("run_out", "LUMOGIS_BACKUP_AGE_MIN=5\n")


def test_rollback_runs_to_completion_on_rollback_tags(fake):
    _rollback_fixture(fake)
    assert not fake.override.exists()
    res = fake.rollback()
    assert res.returncode == 0, res.stdout + res.stderr
    assert "rolling back to" in res.stdout and "datastore" in res.stdout
    assert _ref() in res.stdout and "postgres@sha256:abc" in res.stdout
    assert [ln for ln in fake.log() if ln.startswith("pull ")] == ["pull postgres@sha256:abc"]
    up = [ln for ln in fake.log() if ln.startswith("COMPOSE_FILE=")]
    assert up and up[-1].endswith(str(fake.override)), up
    assert fake.override.stat().st_mode & 0o777 == 0o644
    text = fake.override.read_text()
    orch = text.split("  orchestrator:")[1].split("  postgres:")[0]
    pg = text.split("  postgres:")[1]
    assert "pull_policy: never" in orch and "pull_policy: !reset null" not in orch
    assert "pull_policy: !reset null" in pg
    assert not fake.marker.exists()


def test_rollback_up_failure_names_a_mixed_stack_and_keeps_the_marker(fake):
    _rollback_fixture(fake)
    fake.set("up_rc", "1")
    res = fake.rollback()
    assert res.returncode != 0
    for needle in ("starting the previous images failed", "docker compose ps", "make restore"):
        assert needle in res.stderr, (needle, res.stderr)
    assert fake.marker.exists()


# ----------------------------------------------------------------------------
# Real Docker daemon fixture
# ----------------------------------------------------------------------------


def _docker_env(project: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME", "BUILDX_BUILDER")
    }
    env["COMPOSE_PROJECT_NAME"] = project
    # The docker-driver builder sees the local base image; a docker-container
    # builder would ask Docker Hub for it. On Docker Desktop there is no `default`.
    if _run(["docker", "buildx", "inspect", "default"]).returncode == 0:
        env["BUILDX_BUILDER"] = "default"
    return env


def _docker_gate() -> None:
    """Skip locally / fail in CI when no usable daemon or Compose is available."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    problem = None
    if _run(["docker", "info"], timeout=60).returncode != 0:
        problem = "docker daemon unreachable"
    else:
        ver = _run(["docker", "compose", "version", "--short"], timeout=60)
        if ver.returncode != 0:
            problem = "docker compose plugin missing"
        else:
            m = re.match(r"v?(\d+)\.(\d+)", ver.stdout.strip())
            if not m or (int(m.group(1)), int(m.group(2))) < (2, 24):
                problem = f"docker compose {ver.stdout.strip()!r} is older than 2.24 (no !reset)"
    if problem:
        if os.environ.get("CI"):
            pytest.fail(problem)
        pytest.skip(problem)


class Stack:
    def __init__(self, root: Path, project: str, env: dict[str, str]):
        self.root = root
        self.project = project
        self.env = env
        self.state = root / "state" / "previous-images.txt"
        self.override = root / "state" / "rollback-compose.override.yml"
        self.compose_tag = f"lumogis-lum984-test/svc:dev-{project}"

    def write_dockerfile(self, version: str) -> None:
        (self.root / "Dockerfile").write_text(f"FROM {_base_image()}\nRUN echo {version} > /v\n")

    def compose(self, *args, files=(), timeout=180):
        cmd = ["docker", "compose"]
        for f in files:
            cmd += ["-f", str(f)]
        return _run([*cmd, *args], cwd=self.root, env=self.env, timeout=timeout)

    def container_image_id(self) -> str:
        cid = self.compose("ps", "-q", "svc").stdout.strip().splitlines()[0]
        return _run(["docker", "inspect", "--format", "{{.Image}}", cid]).stdout.strip()

    def bash(self, script: str, *args, common=None):
        """Run bash with common.sh sourced, in the stack dir, with the stack's env."""
        env = dict(self.env)
        env["ROOT"] = str(self.root)
        return _run(
            ["bash", "-c", f'source "{common or _COMMON_SH}"\n{script}', "_", *args],
            cwd=self.root,
            env=env,
            timeout=120,
        )

    def capture(self):
        """capture_rollback_state, then (post-change) the rollback-time override.

        Always passes three arguments: a bash function ignores extras, so the same
        call works on the old three-argument and the new two-argument function.
        The override is generated by ``write_rollback_override_from_state`` when
        ``common.sh`` defines it (as ``rollback.sh`` does, D6); before the change
        capture itself wrote it.
        """
        self.state.parent.mkdir(parents=True, exist_ok=True)
        res = self.bash(
            'capture_rollback_state "$1" "$2" "$3" || exit $?\n'
            'if [ "$(type -t write_rollback_override_from_state)" = function ]; then\n'
            '  write_rollback_override_from_state "$1" "$2" "$3" || exit $?\n'
            "fi",
            str(self.state),
            str(self.override),
            str(_WRITER),
        )
        assert res.returncode == 0, res.stdout + res.stderr
        return res

    def rollback_tags(self) -> list[str]:
        out = _run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"]).stdout
        return [t for t in out.splitlines() if t.startswith(f"lumogis-rollback/{self.project}")]


@pytest.fixture
def compose_stack(tmp_path):
    _docker_gate()
    project = f"lum984-{uuid.uuid4().hex[:8]}"
    env = _docker_env(project)
    base = _base_image()
    if _run(["docker", "image", "inspect", base]).returncode != 0:
        pull = _run(["docker", "pull", base], timeout=300)
        if pull.returncode != 0:
            msg = f"cannot pull base image {base}: {pull.stderr.strip()}"
            if os.environ.get("CI"):
                pytest.fail(msg)
            pytest.skip(msg)
    root = tmp_path / "stack"
    root.mkdir()
    stack = Stack(root, project, env)
    stack.write_dockerfile("v1")
    (root / "docker-compose.yml").write_text(
        "services:\n"
        "  svc:\n"
        f"    image: {stack.compose_tag}\n"
        "    build: .\n"
        "    pull_policy: build\n"
        "    init: true\n"
        '    command: ["sleep", "3600"]\n'
        "    labels:\n"
        "      lumogis.test: lum984\n"
    )
    print(
        "docker storage:",
        _run(["docker", "info", "--format", "{{.Driver}} {{json .DriverStatus}}"]).stdout.strip(),
        file=sys.stderr,
    )
    yield stack
    stack.compose("down", "-v", "--rmi", "all", "--timeout", "1")
    for tag in stack.rollback_tags():
        _run(["docker", "rmi", "-f", tag])
    _run(["docker", "rmi", "-f", f"lumogis-rollback/{project}-other/svc:19700101T000000Z"])
    _run(["docker", "rmi", "-f", stack.compose_tag])


# ----------------------------------------------------------------------------
# D1 — the issue's Done-when, on the host's real image store
# ----------------------------------------------------------------------------


def test_rollback_tag_survives_rebuild_under_same_tag(compose_stack):
    s = compose_stack
    assert s.compose("build").returncode == 0
    assert s.compose("up", "-d", "svc").returncode == 0
    v1_id = s.container_image_id()

    s.capture()
    state_line = s.state.read_text().splitlines()[0].split("\t")
    assert state_line[0] == "svc"
    assert state_line[2].startswith(f"lumogis-rollback/{s.project}/svc:"), state_line
    override_text = s.override.read_text()
    assert "pull_policy: never" in override_text

    # the update: rebuild v2 under the same compose tag (deliberately no --pull)
    s.write_dockerfile("v2")
    assert s.compose("build").returncode == 0

    # negative control: the compose tag moved, so this is not a vacuous test
    moved = s.compose("run", "--rm", "--pull", "never", "svc", "cat", "/v")
    assert moved.stdout.strip().endswith("v2"), moved.stdout + moved.stderr

    # no --pull flag: the override's own pull_policy governs
    ran = s.compose("run", "--rm", "svc", "cat", "/v", files=["docker-compose.yml", s.override])
    assert ran.stdout.strip().endswith("v1"), ran.stdout + ran.stderr

    # the real rollback.sh path: recreate on v2, then recreate with the override
    assert s.compose("up", "-d", "svc").returncode == 0
    assert s.container_image_id() != v1_id
    up = s.compose("up", "-d", "svc", files=["docker-compose.yml", s.override])
    assert up.returncode == 0, up.stdout + up.stderr
    assert s.container_image_id() == v1_id


# ----------------------------------------------------------------------------
# D2-D4 — prune, presence check, pull_policy: never on the real daemon
# ----------------------------------------------------------------------------


def test_prune_keeps_current_set_and_other_projects(compose_stack):
    s = compose_stack
    assert s.compose("build").returncode == 0
    assert s.compose("up", "-d", "svc").returncode == 0
    v1_id = s.container_image_id()
    s.capture()
    current = s.state.read_text().splitlines()[0].split("\t")[2]
    old = f"lumogis-rollback/{s.project}/svc:19700101T000000Z"
    other = f"lumogis-rollback/{s.project}-other/svc:19700101T000000Z"
    for tag in (old, other):
        assert _run(["docker", "tag", v1_id, tag]).returncode == 0

    res = s.bash('prune_rollback_tags "$1"', str(s.state))
    assert res.returncode == 0, res.stdout + res.stderr

    def exists(ref):
        return _run(["docker", "image", "inspect", ref]).returncode == 0

    assert not exists(old), "an old tag of this project must be pruned"
    assert exists(current), "the current set must stay"
    assert exists(other), "another project's tag must stay"


def test_rollback_check_stops_when_the_tag_is_gone(compose_stack):
    s = compose_stack
    assert s.compose("build").returncode == 0
    assert s.compose("up", "-d", "svc").returncode == 0
    # No rebuild: the compose tag still names v1, so the rollback tag is not its last name.
    s.capture()
    current = s.state.read_text().splitlines()[0].split("\t")[2]

    ok = s.bash('check_rollback_images_present "$1"', str(s.state))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert _run(["docker", "rmi", current]).returncode == 0  # a plain untag

    gone = s.bash('check_rollback_images_present "$1"', str(s.state))
    assert gone.returncode != 0
    assert "svc" in gone.stderr and "prune -a" in gone.stderr, gone.stderr


def test_pull_policy_never_is_honoured_for_a_missing_tag(compose_stack):
    s = compose_stack
    assert s.compose("build").returncode == 0
    assert s.compose("up", "-d", "svc").returncode == 0
    s.capture()
    current = s.state.read_text().splitlines()[0].split("\t")[2]
    assert s.compose("down", "--timeout", "1").returncode == 0
    assert _run(["docker", "rmi", current]).returncode == 0  # the compose tag still names v1

    files = ["docker-compose.yml", s.override]
    cfg = s.compose("config", "--format", "json", files=files)
    assert cfg.returncode == 0, cfg.stderr
    svc = json.loads(cfg.stdout)["services"]["svc"]
    assert svc["pull_policy"] == "never"
    assert "build" not in svc
    assert svc["image"] == current

    up = s.compose("up", "-d", "--no-build", "svc", files=files)
    out = up.stdout + up.stderr
    assert up.returncode != 0, out
    for registry_word in (
        "pull access denied",
        "repository does not exist",
        "denied:",
        "Pulling",
        "unauthorized",
    ):
        assert registry_word not in out, f"Compose asked a registry ({registry_word!r}): {out}"
