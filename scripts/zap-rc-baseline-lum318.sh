#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# LUM-318 / LUM-190 — OWASP ZAP baseline against the public-RC stack (operator one-shot).
#
# Usage (from repo root, after release pipeline / verify-public-rc-full):
#   make zap-rc-baseline-lum318
#   CLOSE_LUM318=1 make zap-rc-baseline-lum318   # also posts Linear closure on LUM-318
#
# Brings up the RC compose stack, runs pinned ZAP against http://127.0.0.1/, writes
# docs/security-audit/zap-baseline-2026.json, refreshes the ZAP header + DAST-001 row
# in docs/security/pre-launch-audit-2026.md, then tears the stack down.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ZAP_IMAGE="${ZAP_IMAGE:-ghcr.io/zaproxy/zaproxy@sha256:8770b23f9e8b49038f413cb2b10c58c901e5b6717be221a22b1bcab5c9771b8a}"
RC_URL="${ZAP_RC_URL:-http://127.0.0.1/}"
JSON_OUT="$ROOT/docs/security-audit/zap-baseline-2026.json"
FINDINGS_DOC="$ROOT/docs/security/pre-launch-audit-2026.md"
WRK="$ROOT/tmp-zap-wrk-lum318"
STACK_STARTED=0

_cleanup() {
  if [ "$STACK_STARTED" -eq 1 ]; then
    echo "[zap-rc-baseline-lum318] tearing down RC stack" >&2
    bash "$ROOT/scripts/integration-public-rc.sh" gate-end || true
  fi
  rm -rf "$WRK"
}
trap _cleanup EXIT

echo "[zap-rc-baseline-lum318] starting public-RC stack" >&2
bash "$ROOT/scripts/integration-public-rc.sh" gate-start
STACK_STARTED=1

echo "[zap-rc-baseline-lum318] waiting for ${RC_URL}" >&2
ready=0
for _ in $(seq 1 90); do
  if curl -sf -o /dev/null "${RC_URL}" 2>/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "[zap-rc-baseline-lum318] ERROR: RC not reachable at ${RC_URL}" >&2
  exit 1
fi

mkdir -p "$WRK"
json_name="zap-baseline-2026.json"
echo "[zap-rc-baseline-lum318] running ZAP baseline (${ZAP_IMAGE})" >&2
# --network host: ZAP must reach Caddy on the host loopback (127.0.0.1:80), not container-local localhost.
docker run --rm --network host \
  -v "${WRK}:/zap/wrk:rw" \
  "$ZAP_IMAGE" \
  zap-baseline.py -t "${RC_URL}" -J "${json_name}" -I 2>&1 | tee "${WRK}/zap.log" || true

if [ ! -f "${WRK}/${json_name}" ]; then
  echo "[zap-rc-baseline-lum318] ERROR: ZAP did not produce ${json_name}" >&2
  exit 1
fi

python3 - "$WRK/$json_name" "$JSON_OUT" "$FINDINGS_DOC" "$RC_URL" <<'PY'
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
doc_path = Path(sys.argv[3])
rc_url = sys.argv[4]

raw = src.read_text(encoding="utf-8")
redacted = re.sub(
    r'"(Authorization|Cookie|Set-Cookie|X-Api-Key|api_key|token)"\s*:\s*"[^"]*"',
    r'"\1": "[REDACTED]"',
    raw,
    flags=re.I,
)
data = json.loads(redacted)
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

created = data.get("created") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
version = data.get("@version", "?")
fail = warn = info = 0

log_path = src.parent / "zap.log"
if log_path.is_file():
    log = log_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"FAIL-NEW:\s*(\d+).*WARN-NEW:\s*(\d+).*INFO:\s*(\d+)", log, re.S)
    if m:
        fail, warn, info = int(m.group(1)), int(m.group(2)), int(m.group(3))

scan_utc = created if "T" in str(created) else f"{created}Z"
doc = doc_path.read_text(encoding="utf-8")
replacements = [
    (r"\| \*\*Scan date \(UTC\)\*\* \| `[^`]*` \(from JSON `created`\) \|", f"| **Scan date (UTC)** | `{scan_utc}` (from JSON `created`) |"),
    (r"\| \*\*Target base URL\*\* \| `[^`]*` \|", f"| **Target base URL** | `{rc_url.rstrip('/')}/` |"),
    (r"\| \*\*ZAP program version \(JSON `@version`\)\*\* \| `[^`]*` \|", f"| **ZAP program version (JSON `@version`)** | `{version}` |"),
    (r"\| \*\*Alert counts \(risk\)\*\* \| \*\*FAIL:\*\* [^|]* \|", f"| **Alert counts (risk)** | **FAIL:** {fail}, **WARN:** {warn}, **INFO:** {info} (from scan stdout summary) |"),
]
for pat, sub in replacements:
    doc, n = re.subn(pat, sub, doc, count=1)
    if n == 0:
        print(f"[zap-rc-baseline-lum318] WARN: could not patch doc field matching {pat}", file=sys.stderr)

doc = re.sub(
    r"(\| DAST-001 \| Passive DAST \(ZAP baseline\) \| MITIGATED \| `zap-baseline-2026\.json`; ZAP header above \| )[^|]*( \| — \| Composer / )",
    rf"\1Passive baseline against RC URL `{rc_url.rstrip('/')}/` (auth none); see ZAP header.\2",
    doc,
    count=1,
)
doc_path.write_text(doc, encoding="utf-8")
print(f"[zap-rc-baseline-lum318] wrote {dst} and updated {doc_path}", file=sys.stderr)
PY

echo "[zap-rc-baseline-lum318] OK — artefacts updated:" >&2
echo "  - ${JSON_OUT}" >&2
echo "  - ${FINDINGS_DOC}" >&2

if [ "${CLOSE_LUM318:-0}" = "1" ]; then
  if [ -z "${LINEAR_API_KEY:-}" ]; then
    echo "[zap-rc-baseline-lum318] CLOSE_LUM318=1 but LINEAR_API_KEY unset — skip Linear" >&2
    exit 0
  fi
  sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  body="$(cat <<EOF
**LUM-318 closed by \`make zap-rc-baseline-lum318\`**

- RC stack: \`scripts/integration-public-rc.sh gate-start\`
- ZAP target: \`${RC_URL}\`
- Artefacts: \`docs/security-audit/zap-baseline-2026.json\`, \`docs/security/pre-launch-audit-2026.md\` (ZAP header + DAST-001)
- Product SHA at run: \`${sha}\` (commit/push these files if not already on \`main\`)

\`make verify-public-rc-full\` is satisfied by the release pipeline; this run completes the RC-target ZAP evidence for LUM-190.
EOF
)"
  python3 - "$body" <<'PY'
import json, os, sys, urllib.request

body = sys.argv[1]
key = os.environ["LINEAR_API_KEY"]
issue_id = "5e6709ac-a6a1-439c-800e-4fc4b9d78861"  # LUM-318

def gql(query, variables):
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": key},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

gql(
    "mutation($id:String!,$body:String!){ commentCreate(input:{issueId:$id,body:$body}){ success } }",
    {"id": issue_id, "body": body},
)
team = gql(
    'query{ issue(id:"5e6709ac-a6a1-439c-800e-4fc4b9d78861"){ team{ states{ nodes{ id name type } } } } }',
    {},
)["data"]["issue"]["team"]["states"]["nodes"]
done_id = next(s["id"] for s in team if s["type"] == "completed" or s["name"].lower() == "done")
gql(
    "mutation($id:String!,$state:String!){ issueUpdate(id:$id,input:{stateId:$state}){ success } }",
    {"id": issue_id, "state": done_id},
)
print("[zap-rc-baseline-lum318] LUM-318 → Done (comment posted)", file=sys.stderr)
PY
fi
