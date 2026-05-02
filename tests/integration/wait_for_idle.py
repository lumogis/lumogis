#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Block until graph projection has drained.

The parity test (`test_graph_parity.py`) ingests the Ada Lovelace
fixture in two consecutive runs (inprocess mode, then service mode) and
diffs the resulting FalkorDB snapshots. Both runs need a deterministic
"ingest is fully projected" gate — `sleep N` is not deterministic,
because the projection latency depends on machine load, FalkorDB cold-
start, and (in service mode) the in-flight webhook queue depth.

Two modes:

  --mode kg --url http://localhost:8001 [--timeout 60]
      Polls the KG service's `GET /health`. Returns 0 when
      `pending_webhook_tasks == 0` for two consecutive 1-second polls.
      Used after `GRAPH_MODE=service` ingest to wait for the KG queue
      to drain.

  --mode core --url http://localhost:8000 [--timeout 60]
      Polls Core's `GET /graph/health`. Returns 0 when the
      `(nodes, edges)` tuple is stable for two consecutive 2-second
      polls (i.e. nothing new projected in the last 2 s). Sidesteps
      the fact that Core's `/health` does not currently expose its
      `fire_background` queue depth — extending Core's `/health` is
      deliberately out of scope for the extraction plan.

On timeout (both modes): print to stderr the full last response body,
the elapsed seconds, and the last three sampled values, then exit 1.
A flapping parity run thereby leaves a self-explanatory artifact in CI
logs instead of a bare "exit 1".

Stdlib only — no third-party deps so this script can run from any CI
runner without pip-install.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _fetch(url: str) -> tuple[int, dict | None, str]:
    """Fetch URL. Returns (status, parsed_json_or_none, raw_body)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw), raw
            except json.JSONDecodeError:
                return resp.status, None, raw
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return exc.code, None, raw
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return 0, None, f"{type(exc).__name__}: {exc}"


def _print_failure(samples: list, elapsed: float) -> None:
    last_three = samples[-3:] if samples else []
    print(
        f"wait_for_idle: TIMEOUT after {elapsed:.1f}s. Last 3 samples:",
        file=sys.stderr,
    )
    for s in last_three:
        print(json.dumps(s, default=str, indent=2), file=sys.stderr)


def wait_kg(url: str, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    samples: list[dict] = []
    consecutive_idle = 0

    while time.monotonic() < deadline:
        status, parsed, raw = _fetch(f"{url}/health")
        sample = {
            "ts": time.time(),
            "status": status,
            "body": parsed if parsed is not None else raw[:500],
        }
        samples.append(sample)

        pending = (parsed or {}).get("pending_webhook_tasks") if parsed else None
        if status == 200 and pending == 0:
            consecutive_idle += 1
            if consecutive_idle >= 2:
                return 0
        else:
            consecutive_idle = 0

        time.sleep(1.0)

    _print_failure(samples, timeout)
    return 1


def wait_core(url: str, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    samples: list[dict] = []
    last_tuple: tuple[int, int] | None = None
    consecutive_stable = 0

    while time.monotonic() < deadline:
        status, parsed, raw = _fetch(f"{url}/graph/health")
        sample = {
            "ts": time.time(),
            "status": status,
            "body": parsed if parsed is not None else raw[:500],
        }
        samples.append(sample)

        if status == 200 and parsed is not None:
            current = (
                int(parsed.get("nodes", -1)),
                int(parsed.get("edges", -1)),
            )
            if last_tuple == current:
                consecutive_stable += 1
                if consecutive_stable >= 2:
                    return 0
            else:
                consecutive_stable = 0
                last_tuple = current
        else:
            consecutive_stable = 0
            last_tuple = None

        time.sleep(2.0)

    _print_failure(samples, timeout)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=["kg", "core"])
    parser.add_argument("--url", required=True, help="Base URL of the service")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    url = args.url.rstrip("/")
    if args.mode == "kg":
        return wait_kg(url, args.timeout)
    return wait_core(url, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
