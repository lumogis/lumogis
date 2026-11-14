# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""GRAPH_MODE parity test — Rescan to projection, then inprocess vs. service.

Boots Core over the same `tests/fixtures/ada_lovelace.md` corpus in its own
compose project (`lumogis-parity`), once with the out-of-process `lumogis-graph`
service and once with the in-process plugin, and measures FalkorDB itself.

Two tests:

  * `test_graph_service_rescan_projects_fixtures` — the service-mode proof the
    LUM-884 coverage row needs: a body-less Rescan of `/fixtures` ends with a
    Document node, at least one entity node, and `graph_projected_at` stamped.
  * `test_graph_parity_inprocess_vs_service` — switching `GRAPH_MODE` must not
    change the deterministic part of projection (graph names and the Document
    count). Entity and edge counts come from two independent LLM runs, so they
    are recorded side by side but not compared.

The snapshot is read from FalkorDB (`_SNAPSHOT_PY`, run inside the orchestrator
container). Core `/graph/health` reports Postgres quality metrics only and was
what the first version of this test compared: it held none of the compared
fields, so the diff always passed (LUM-929).

Marked `@pytest.mark.integration` and `@pytest.mark.slow` — skipped by default in
CI. Run explicitly:

    make test-graph-parity

Isolation (LUM-929): every compose call goes through `_compose`, which pins
`-p lumogis-parity`; host ports are `LUMOGIS_PARITY_*` (18000/16334/16380/18011);
the host data dirs are tmp dirs. The base compose file still mounts the checkout
(`.:/project`) and several services read its `.env`, so the test refuses to run
unless the checkout's `.env` is the test template (`_assert_test_checkout`). On a
failure the container logs are written to `.cache/lum929/` before teardown.

Skipped (not failed) if `docker` is not on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS = Path(__file__).resolve().parent
CACHE_DIR = REPO_ROOT / ".cache" / "lum929"

PARITY_PROJECT = "lumogis-parity"
OLLAMA_CACHE_VOLUME = "lumogis-parity-ollama"
FIXTURE_SUFFIX = "/fixtures/ada_lovelace.md"

CORE_BASE_OVERLAY = "docker-compose.yml:docker-compose.falkordb.yml:docker-compose.parity.yml"
# docker-compose.parity-premium.yml only overrides the lumogis-graph service, so it can
# ONLY be layered when the premium overlay is present (otherwise the service has no
# image/build context).
SERVICE_OVERLAY = (
    "docker-compose.yml:docker-compose.falkordb.yml"
    ":docker-compose.premium.yml:docker-compose.parity.yml"
    ":docker-compose.parity-premium.yml"
)


def _parity_ports() -> dict[str, str]:
    """Host ports for the parity stack; independent of any other stack on the host."""
    return {
        "core": os.environ.get("LUMOGIS_PARITY_CORE_PORT", "18000"),
        "qdrant": os.environ.get("LUMOGIS_PARITY_QDRANT_PORT", "16334"),
        "falkordb": os.environ.get("LUMOGIS_PARITY_FALKORDB_PORT", "16380"),
        "kg": os.environ.get("LUMOGIS_PARITY_KG_PORT", "18011"),
    }


# LUMOGIS_API_URL / LUMOGIS_KG_URL are deliberately NOT read: under the RC lane they
# point at the RC stack, and parity must poll its own.
CORE_URL = f"http://127.0.0.1:{_parity_ports()['core']}"
KG_URL = f"http://127.0.0.1:{_parity_ports()['kg']}"

INGEST_TIMEOUT_S = float(os.environ.get("LUMOGIS_PARITY_INGEST_TIMEOUT", "180"))
DRAIN_TIMEOUT_S = float(os.environ.get("LUMOGIS_PARITY_DRAIN_TIMEOUT", "600"))
UP_TIMEOUT_S = int(os.environ.get("LUMOGIS_PARITY_UP_TIMEOUT_S", "900"))
POLL_INTERVAL_S = 10
STABLE_SNAPSHOTS = 4


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a subprocess; surface stderr in pytest output on failure."""
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        sys.stderr.write(f"\n+ {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}\n")
        raise AssertionError(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc


def _compose(
    args: list[str], env: dict[str, str], *, check: bool = True
) -> subprocess.CompletedProcess:
    """Every compose call goes through here, so `-p lumogis-parity` can never be omitted."""
    return _run(["docker", "compose", "-p", PARITY_PROJECT, *args], env=env, check=check)


def _compose_env(compose_file: str, graph_mode: str, host_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    ports = _parity_ports()
    env["COMPOSE_FILE"] = compose_file
    env["COMPOSE_PROJECT_NAME"] = PARITY_PROJECT
    env["GRAPH_MODE"] = graph_mode
    env["ORCHESTRATOR_HOST_PORT"] = ports["core"]
    env["QDRANT_HOST_PORT"] = ports["qdrant"]
    env["FALKORDB_HOST_PORT"] = ports["falkordb"]
    env["KG_HOST_PORT"] = ports["kg"]
    # No household path is bind-mounted: all three host dirs live under tmp.
    env["WORKSPACE_HOST_DIR"] = str(host_root / "workspace")
    env["FILESYSTEM_ROOT"] = str(host_root / "data")
    env["BACKUP_HOST_DIR"] = str(host_root / "backups")
    for key in ("WORKSPACE_HOST_DIR", "FILESYSTEM_ROOT", "BACKUP_HOST_DIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _assert_test_checkout(repo_root: Path) -> None:
    """Fail (not skip) unless the checkout's `.env` is the test template.

    The parity stack mounts this checkout at /project read-write and several
    services read its `.env`; from a household checkout that is real secrets and
    data on an auth-off stack.
    """
    env = _parse_env_file(repo_root / ".env")
    template = _parse_env_file(repo_root / "config" / "test.env.example")
    if (
        env.get("COMPOSE_PROJECT_NAME") != "lumogis-test"
        or not env.get("AUTH_SECRET")
        or env.get("AUTH_SECRET") != template.get("AUTH_SECRET")
    ):
        pytest.fail(
            "parity mounts this checkout at /project and reads its .env; run it from a "
            "worktree whose .env is a copy of config/test.env.example"
        )


def _wait_for_http(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    import urllib.error
    import urllib.request

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2.0)
    raise AssertionError(f"timeout waiting for {url} to respond 200 within {timeout}s")


def _ingest(url: str) -> None:
    """POST /ingest (body-less Rescan) to Core.

    /ingest takes no folder any more (LUM-884): it rescans the configured
    library folders. docker-compose.parity.yml declares /fixtures as one via
    INGEST_PATHS. /fixtures is the bind-mount path defined there; it
    deliberately sits OUTSIDE /data because the base compose mounts the
    host data root at /data:ro, which prevents creating sub-mountpoints
    inside it.
    """
    import urllib.request

    req = urllib.request.Request(f"{url.rstrip('/')}/ingest", method="POST")
    with urllib.request.urlopen(req, timeout=INGEST_TIMEOUT_S) as resp:
        assert resp.status in (200, 202), f"ingest returned HTTP {resp.status}"


# Run inside the orchestrator container. `FalkorDB.from_url` does not exist in the pinned
# client (see adapters/falkordb_store.py), so the URL is parsed by the adapter's own
# helper and the constructor takes host/port. Counts are summed over every graph, so a
# per-bank naming difference cannot hide nodes; the sorted graph names are compared too.
_SNAPSHOT_PY = r"""
import json, os
import falkordb
from adapters.falkordb_store import _parse_url

host, port = _parse_url(os.environ["FALKORDB_URL"])
db = falkordb.FalkorDB(host=host, port=port)
if hasattr(db, "list_graphs"):
    names = list(db.list_graphs())
else:
    names = db.connection.execute_command("GRAPH.LIST")
names = sorted(n.decode() if isinstance(n, bytes) else str(n) for n in names)
nodes = edges = 0
by_label, by_type = {}, {}
for name in names:
    graph = db.select_graph(name)
    for label, count in graph.query("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c").result_set:
        by_label[label] = by_label.get(label, 0) + int(count)
        nodes += int(count)
    for rel, count in graph.query("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c").result_set:
        by_type[rel] = by_type.get(rel, 0) + int(count)
        edges += int(count)
print(json.dumps({"graphs": names, "nodes": nodes, "edges": edges,
                  "nodes_by_label": by_label, "edges_by_type": by_type}))
"""

# Read-only; the path fragment travels as argv[1] and is bound as a %s parameter.
_STAMP_PY = r"""
import os, sys
import psycopg2

conn = psycopg2.connect(
    host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
    user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ["POSTGRES_DB"], connect_timeout=5,
)
try:
    cur = conn.cursor()
    cur.execute(
        "SELECT graph_projected_at IS NOT NULL FROM file_index "
        "WHERE position(%s in file_path) > 0 ORDER BY updated_at DESC LIMIT 1",
        (sys.argv[1],),
    )
    row = cur.fetchone()
finally:
    conn.close()
print("none" if row is None else ("true" if row[0] else "false"))
"""


def _snapshot_falkordb(env: dict[str, str]) -> dict:
    proc = _compose(["exec", "-T", "orchestrator", "python", "-c", _SNAPSHOT_PY], env, check=False)
    if proc.returncode != 0:
        raise AssertionError(f"FalkorDB snapshot failed (exit {proc.returncode}): {proc.stderr}")
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"FalkorDB snapshot output is not JSON ({exc}): "
            f"{proc.stdout[:500]!r} {proc.stderr[:500]!r}"
        ) from exc


def _file_index_projected(env: dict[str, str], suffix: str) -> bool | None:
    """True/False for the newest matching `file_index` row's stamp; None if no row."""
    proc = _compose(
        ["exec", "-T", "orchestrator", "python", "-c", _STAMP_PY, suffix], env, check=False
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"file_index stamp query failed (exit {proc.returncode}): {proc.stderr}"
        )
    return {"true": True, "false": False, "none": None}[proc.stdout.strip()]


def _wait_for_projection(
    env: dict[str, str],
    timeout: float | None = None,
    *,
    snapshot: Callable[[dict[str, str]], dict] = _snapshot_falkordb,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Poll the FalkorDB snapshot until entity extraction has landed and it is stable.

    Returns once a label other than `Document` exists and four consecutive
    snapshots are identical; raises with the last snapshot on timeout.
    """
    timeout = DRAIN_TIMEOUT_S if timeout is None else timeout
    deadline = clock() + timeout
    previous: dict | None = None
    stable = 0
    last: dict = {}
    while True:
        last = snapshot(env)
        has_entity = any(label != "Document" for label in last.get("nodes_by_label", {}))
        if not has_entity:
            stable = 0
        elif previous is not None and last == previous:
            stable += 1
        else:
            stable = 1
        previous = last
        if stable >= STABLE_SNAPSHOTS:
            return last
        if clock() >= deadline:
            raise AssertionError(
                f"projection did not stabilise within {timeout:.0f}s "
                f"(entity label present: {has_entity}); last snapshot: {last}"
            )
        sleep(POLL_INTERVAL_S)


def _wait_for_idle(mode: str, url: str) -> None:
    helper = HELPERS / "wait_for_idle.py"
    _run(
        [
            sys.executable,
            str(helper),
            "--mode",
            mode,
            "--url",
            url,
            "--timeout",
            str(int(DRAIN_TIMEOUT_S)),
        ]
    )


def _compose_down(env: dict[str, str]) -> None:
    _compose(["down", "-v", "--remove-orphans"], env, check=False)


def _ensure_ollama_cache_volume() -> None:
    """External volume, so `down -v` cannot discard pulled models. Idempotent.

    Known gap (LUM-929 verify, 2026-11-11): the models are pulled by the
    orchestrator entrypoint, which only warns when a pull fails. A part-pulled
    volume then gives no `documents` collection, every Rescan 404s, and the run
    waits out DRAIN_TIMEOUT_S before failing; the cause is in the saved
    container logs. If that happens, seed the volume from a stack that has both
    models (e.g. copy `lumogis-test_ollama_data`) and re-run.
    """
    _run(["docker", "volume", "create", OLLAMA_CACHE_VOLUME])


def _save_logs(env: dict[str, str], phase: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"parity-{phase}-logs.txt"
    proc = _compose(
        ["logs", "--no-color", "--tail", "2000", "orchestrator", "lumogis-graph", "falkordb"],
        env,
        check=False,
    )
    dest.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    return dest


# Services we need healthy before the parity assertion can run. We deliberately
# omit `librechat` because it's a chat UI with no role in `/ingest` or
# `/graph/health`, yet `docker compose up --wait` would block on its (slow,
# heavy) healthcheck.
_PARITY_CORE_SERVICES = (
    "orchestrator",
    "falkordb",
    "postgres",
    "qdrant",
    "mongodb",
    "ollama",
    "stack-control",
)
_PARITY_PREMIUM_EXTRA = ("lumogis-graph",)


def _compose_up(env: dict[str, str], *, include_premium: bool = False) -> None:
    services = list(_PARITY_CORE_SERVICES)
    if include_premium:
        services.extend(_PARITY_PREMIUM_EXTRA)
    # The first run waits for the entrypoint to pull the embedder and the extraction
    # model into the empty `lumogis-parity-ollama` volume.
    _compose(["up", "-d", "--wait", "--wait-timeout", str(UP_TIMEOUT_S), *services], env)


def _deterministic_view(snapshot: dict) -> dict:
    """The part of a snapshot that does not depend on LLM output."""
    return {
        "graphs": snapshot["graphs"],
        "nodes_by_label": {"Document": snapshot["nodes_by_label"].get("Document", 0)},
    }


def _run_phase(
    phase: str, compose_file: str, graph_mode: str, host_root: Path, *, premium: bool
) -> tuple[dict, bool | None]:
    """down -v, up, Rescan, wait for projection, snapshot, down -v. Logs saved on failure."""
    env = _compose_env(compose_file, graph_mode, host_root)
    _ensure_ollama_cache_volume()
    try:
        _compose_down(env)
        _compose_up(env, include_premium=premium)
        _wait_for_http(f"{CORE_URL}/")
        if premium:
            _wait_for_http(f"{KG_URL}/health")
        _ingest(CORE_URL)
        if premium:
            _wait_for_idle("kg", KG_URL)
        snapshot = _wait_for_projection(env)
        projected = _file_index_projected(env, FIXTURE_SUFFIX)
        return snapshot, projected
    except BaseException as exc:
        logs = _save_logs(env, phase)
        raise AssertionError(
            f"parity {phase} phase failed ({exc}); container logs: {logs}"
        ) from exc
    finally:
        _compose_down(env)


def _write_snapshot(phase: str, snapshot: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"parity-{phase}-snapshot.json"
    dest.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return dest


@pytest.fixture(scope="module")
def parity_fixture_present():
    fixture = REPO_ROOT / "tests" / "fixtures" / "ada_lovelace.md"
    if not fixture.exists():
        pytest.fail(f"missing fixture {fixture} — required by parity test")
    return fixture


@pytest.fixture(scope="module")
def service_phase(tmp_path_factory, parity_fixture_present):
    """Run the service-mode phase once; yields (snapshot_path, snapshot, projected)."""
    if not _have_docker():
        pytest.skip("docker CLI not available")
    _assert_test_checkout(REPO_ROOT)
    host_root = tmp_path_factory.mktemp("parity-host-service")
    snapshot, projected = _run_phase("service", SERVICE_OVERLAY, "service", host_root, premium=True)
    path = _write_snapshot("service", snapshot)
    return path, snapshot, projected


@pytest.mark.skipif(not _have_docker(), reason="docker CLI not available")
def test_graph_service_rescan_projects_fixtures(service_phase):
    """Rescan of /fixtures ends in FalkorDB: a Document node, an entity, a stamped row."""
    _, snapshot, projected = service_phase
    assert snapshot["nodes"] >= 1, f"no nodes in FalkorDB: {snapshot}"
    assert snapshot["nodes_by_label"].get("Document", 0) >= 1, (
        f"no Document node: {snapshot['nodes_by_label']}"
    )
    assert any(label != "Document" for label in snapshot["nodes_by_label"]), (
        f"no entity label beside Document: {snapshot['nodes_by_label']}"
    )
    assert projected is not None, f"no file_index row for {FIXTURE_SUFFIX}"
    assert projected is True, f"graph_projected_at not stamped for {FIXTURE_SUFFIX}"


@pytest.mark.manual
@pytest.mark.skipif(not _have_docker(), reason="docker CLI not available")
def test_graph_parity_inprocess_vs_service(service_phase, tmp_path, tmp_path_factory):
    """Switching GRAPH_MODE MUST NOT change graph names or the Document count.

    Entity and edge counts come from two independent LLM runs, so they are printed
    side by side and written next to the junit report, but not compared.

    ``manual`` (LUM-929 fallback 3): ``make test-graph-parity`` deselects this
    test. On the first live run (2026-11-11) the in-process plugin loaded and
    projected the Document, but llama3.2:3b returned unparsable extraction JSON,
    so no entity label appeared within ``DRAIN_TIMEOUT_S``. The projection wait
    needs an entity label in both phases, so the two phases cannot be compared
    deterministically. The service-mode Rescan proof above gates on its own.
    Whether the in-process half is retired or made deterministic is tracked in
    LUM-855's Later section. Run it by hand with ``-m manual``.
    """
    _, service_snapshot, _ = service_phase
    host_root = tmp_path_factory.mktemp("parity-host-inprocess")
    inprocess_snapshot, _ = _run_phase(
        "inprocess", CORE_BASE_OVERLAY, "inprocess", host_root, premium=False
    )
    _write_snapshot("inprocess", inprocess_snapshot)
    sys.stderr.write(
        f"\nparity service   : {json.dumps(service_snapshot, sort_keys=True)}"
        f"\nparity inprocess : {json.dumps(inprocess_snapshot, sort_keys=True)}\n"
    )

    a = tmp_path / "view_inprocess.json"
    b = tmp_path / "view_service.json"
    a.write_text(json.dumps(_deterministic_view(inprocess_snapshot)), encoding="utf-8")
    b.write_text(json.dumps(_deterministic_view(service_snapshot)), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(HELPERS / "diff_snapshots.py"), str(a), str(b)],
        capture_output=True,
        text=True,
    )
    sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, (
        f"GRAPH_MODE parity broken — diff_snapshots reported:\n{proc.stdout}"
    )
