#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Live check for LUM-875: POSTs through Caddy must not get 502 when Caddy reuses
# a pooled orchestrator connection that uvicorn closes because of its keep-alive
# timeout.
#
# What it starts (plain `docker run` on a throwaway network; no compose project,
# and the running stack is never touched):
#   * `orchestrator`: a stub ASGI app run by the orchestrator image's own uvicorn,
#     with the uvicorn flags from orchestrator/Dockerfile's CMD (or --uvicorn-args);
#   * `caddy`: caddy:2-alpine with docker/caddy/Caddyfile (or --caddyfile),
#     CADDY_DOMAIN=:80;
#   * a probe that, for each route, primes the pooled upstream connection, idles
#     for about --around seconds (default 5, uvicorn's default keep-alive), then
#     POSTs again. Any 5xx fails the check.
#
# How the race window is widened: on an idle host the race is under a millisecond
# wide. Before writing this, 288 parallel attempts at 4.990-5.010 s gaps through
# the real Caddyfile gave no 502. In production the orchestrator's event loop is
# busy (sync work, GIL contention), and the Playwright trace shows the failing
# refresh 5.2 s after the previous response. So the stub blocks its own event
# loop in two steps around uvicorn's keep-alive deadline T:
#   1. an I/O callback blocks from T-0.10 to T+0.05, so uvicorn's keep-alive
#      timer comes due while the loop is stuck;
#   2. a timer due at T-0.02 then blocks for another 0.3 s.
# uvloop runs due timers before it polls for I/O, so after step 1 it runs the
# step-2 timer and then uvicorn's, with every request that arrived meanwhile
# still unread. asyncio polls first, so step 1 alone is not enough there (it
# reads the request that arrived during the stall, which cancels the keep-alive
# timer: LUM-910 found the check passing a 5 s keep-alive under --loop asyncio).
# But asyncio runs all callbacks that are due in one batch after the poll, and
# the step-2 timer sorts before uvicorn's, so a request that arrives during step
# 2 is still unread when uvicorn's timer runs. Either way uvicorn closes the
# connection with a request unread, and Caddy logs EOF or "connection reset by
# peer" and returns 502. With a keep-alive longer than the gap, the same stalls
# are harmless. Core runs --loop asyncio since LUM-903; both loops are covered.
#
# Usage:
#   scripts/check-caddy-upstream-keepalive.sh                # shipped config: expect PASS
#   scripts/check-caddy-upstream-keepalive.sh --uvicorn-args "--host 0.0.0.0 --port 8000 --loop asyncio"
#                                                            # uvicorn default 5 s: expect FAIL
#                                                            # (same with --loop uvloop)
#   scripts/check-caddy-upstream-keepalive.sh --around 70 --caddyfile OLD_CADDYFILE
#                                                            # Caddy's 2 min default vs 75 s
# Env: ORCH_IMAGE (default: the newest local *-orchestrator:latest image; build one
#      with `docker compose build orchestrator`), CADDY_IMAGE (default caddy:2-alpine).
# Exit: 0 no 5xx; 1 at least one 5xx; 2 usage or setup error.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="$ROOT/docker/caddy/Caddyfile"
AROUND=5
UVICORN_ARGS=""
ROUTES="/api/v1/auth/refresh /v1/chat/completions /mcp/ /events /ingest /settings"

while [ $# -gt 0 ]; do
  case "$1" in
    --uvicorn-args) UVICORN_ARGS="$2"; shift 2 ;;
    --caddyfile) CADDYFILE="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
    --around) AROUND="$2"; shift 2 ;;
    --routes) ROUTES="$2"; shift 2 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "check-caddy-upstream-keepalive: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$UVICORN_ARGS" ]; then
  # Everything after `uvicorn main:app` in the Dockerfile's JSON-form CMD.
  UVICORN_ARGS="$(python3 -c '
import json, sys
cmd = [l for l in open(sys.argv[1]) if l.startswith("CMD ")][-1][4:]
argv = json.loads(cmd)
assert argv[:2] == ["uvicorn", "main:app"], argv
print(" ".join(argv[2:]))
' "$ROOT/orchestrator/Dockerfile")"
fi

ORCH_IMAGE="${ORCH_IMAGE:-$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '(^|-)orchestrator:latest$' | head -1 || true)}"
CADDY_IMAGE="${CADDY_IMAGE:-caddy:2-alpine}"
if [ -z "$ORCH_IMAGE" ]; then
  echo "check-caddy-upstream-keepalive: no orchestrator image; set ORCH_IMAGE or run 'docker compose build orchestrator'" >&2
  exit 2
fi
[ -f "$CADDYFILE" ] || { echo "check-caddy-upstream-keepalive: no Caddyfile at $CADDYFILE" >&2; exit 2; }

NAME="lum-keepalive-check-$$"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/lum-keepalive-check.XXXXXX")"
cleanup() {
  docker rm -f "$NAME-orchestrator" "$NAME-caddy" >/dev/null 2>&1 || true
  docker network rm "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

STALL_AT="$(python3 -c "print(max(float('$AROUND') - 0.1, 0.0))")"

cat >"$WORK/app.py" <<'PY'
import asyncio, os, socket, time

STALL_AT = float(os.environ["PROBE_STALL_AT"])  # T - 0.10, T = keep-alive deadline
STALL_FOR = float(os.environ.get("PROBE_STALL_FOR", "0.15"))  # step 1 ends at T + 0.05
BATCH_AT = STALL_AT + 0.08  # T - 0.02: due before uvicorn's timer, runs after step 1
BATCH_FOR = float(os.environ.get("PROBE_BATCH_STALL_FOR", "0.3"))
_pair = None


def _stall():
    _pair[0].recv(64)
    time.sleep(STALL_FOR)  # step 1: block the loop inside an I/O callback


def _batch_stall():
    time.sleep(BATCH_FOR)  # step 2: block inside a timer that shares uvicorn's batch


async def app(scope, receive, send):
    global _pair
    if scope["type"] != "http":
        return
    loop = asyncio.get_running_loop()
    if _pair is None:
        _pair = socket.socketpair()
        _pair[0].setblocking(False)
        loop.add_reader(_pair[0].fileno(), _stall)
    while (await receive()).get("more_body"):
        pass
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json")]})
    # uvicorn arms its keep-alive timer inside this send, so both deadlines
    # below are measured from a few microseconds after it.
    await send({"type": "http.response.body", "body": b'{"detail":"keepalive probe"}'})
    loop.call_later(STALL_AT, _pair[1].send, b"x")
    loop.call_later(BATCH_AT, _batch_stall)
PY

cat >"$WORK/probe.py" <<'PY'
import sys, time, urllib.error, urllib.request

base, around, routes = sys.argv[1], float(sys.argv[2]), sys.argv[3].split()
gaps = [round(around + d, 3) for d in (-0.1, -0.05, 0.0, 0.05, 0.1, 0.2)]


def post(path):
    req = urllib.request.Request(base + path, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


bad = 0
for path in routes:
    got = []
    for gap in gaps:
        post(path)  # prime: a fresh upstream response arms uvicorn's keep-alive timer
        time.sleep(gap)
        got.append(post(path))
    fails = sum(1 for s in got if s >= 500)
    bad += fails
    print(f"{'FAIL' if fails else 'ok  '} POST {path:28s} gaps {gaps} -> {got}", flush=True)
print(f"SUMMARY 5xx={bad} probes={len(routes) * len(gaps)}")
sys.exit(1 if bad else 0)
PY
chmod a+r "$WORK/app.py" "$WORK/probe.py"

echo "check-caddy-upstream-keepalive: image=$ORCH_IMAGE uvicorn=[$UVICORN_ARGS] caddyfile=$CADDYFILE around=${AROUND}s"
docker network create "$NAME" >/dev/null
# shellcheck disable=SC2086  # UVICORN_ARGS is a flag list
docker run -d --name "$NAME-orchestrator" --network "$NAME" --network-alias orchestrator \
  -e PROBE_STALL_AT="$STALL_AT" -v "$WORK/app.py:/probe/app.py:ro" -w /probe \
  --entrypoint uvicorn "$ORCH_IMAGE" app:app $UVICORN_ARGS --no-access-log >/dev/null
docker run -d --name "$NAME-caddy" --network "$NAME" --network-alias caddy \
  -e CADDY_DOMAIN=:80 -v "$CADDYFILE:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null

for _ in $(seq 1 30); do
  if docker run --rm --network "$NAME" --entrypoint curl "$ORCH_IMAGE" -s -o /dev/null -X POST http://caddy/health 2>/dev/null; then
    break
  fi
  sleep 1
done

set +e
docker run --rm --network "$NAME" -v "$WORK/probe.py:/probe/probe.py:ro" \
  --entrypoint python "$ORCH_IMAGE" /probe/probe.py http://caddy "$AROUND" "$ROUTES"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  echo "--- Caddy upstream errors:"
  docker logs "$NAME-caddy" 2>&1 | grep '"logger":"http.log.error' | sed -E 's/.*"msg":"([^"]*)".*"uri":"([^"]*)".*/  \2: \1/' | head -20
  echo "check-caddy-upstream-keepalive: FAIL (uvicorn closed connections Caddy was reusing)"
  exit 1
fi
echo "check-caddy-upstream-keepalive: PASS"
