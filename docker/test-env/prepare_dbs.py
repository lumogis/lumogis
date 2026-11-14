# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Wait for the tier-2 throwaway databases, then apply every migration (LUM-894).

Run by ``scripts/test-env.sh`` inside the test-runner image, on the run's
internal network, before the test command starts. It reads the same variables
the tests read (``POSTGRES_*``, ``QDRANT_URL``, ``FALKORDB_URL``), so a run
that gets past this step is pointed at exactly what was prepared.

Exits non-zero, and the run fails, when a backend never becomes ready or when
``db_migrations.py`` applies nothing: on a fresh database that means it read
the wrong directory, which CI's LUM-718 step treats as fatal too.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse


def _wait(what: str, probe, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            probe()
            print(f"[prepare] {what} ready", flush=True)
            return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(1)
    raise SystemExit(f"[prepare] {what} not ready after {timeout_s:.0f}s: {last}")


def _postgres() -> None:
    import psycopg2

    psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        connect_timeout=3,
    ).close()


def _qdrant() -> None:
    url = os.environ["QDRANT_URL"].rstrip("/") + "/readyz"
    with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - internal URL
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")


def _falkordb() -> None:
    from falkordb import FalkorDB

    parsed = urlparse(os.environ["FALKORDB_URL"])
    if not FalkorDB(host=parsed.hostname, port=parsed.port or 6379).connection.ping():
        raise RuntimeError("PING returned false")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orchestrator", required=True, help="path to orchestrator/")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    _wait("postgres", _postgres, args.timeout)
    _wait("qdrant", _qdrant, args.timeout)
    _wait("falkordb", _falkordb, args.timeout)

    proc = subprocess.run(
        [sys.executable, "db_migrations.py"],
        cwd=args.orchestrator,
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print(f"[prepare] db_migrations.py exited {proc.returncode}", file=sys.stderr)
        return proc.returncode
    m = re.search(r"applied (\d+) new migration", proc.stdout)
    if not m or int(m.group(1)) == 0:
        migrations_dir = os.environ.get("LUMOGIS_MIGRATIONS_DIR")
        print(
            "[prepare] db_migrations.py applied no migrations to a fresh database; "
            f"LUMOGIS_MIGRATIONS_DIR={migrations_dir!r} is probably wrong",
            file=sys.stderr,
        )
        return 1
    print(f"[prepare] {m.group(1)} migrations applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
