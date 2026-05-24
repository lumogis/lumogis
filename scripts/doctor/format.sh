#!/usr/bin/env bash
# Lumogis doctor — format aggregated TSV stream (LUM-199).
set -euo pipefail
set -o pipefail
MODE="${1:?}"
STREAM="${2:?}"
DOCTOR_EXIT_FILE="${DOCTOR_EXIT_FILE:?}"

if [ ! -f "$STREAM" ]; then
  echo "DOCTOR_FATAL: missing doctor stream" >&2
  echo 3 >"$DOCTOR_EXIT_FILE"
  exit 3
fi

case "$MODE" in
  human)
    awk -F '\t' -v exitfile="$DOCTOR_EXIT_FILE" '
      BEGIN { e = 0; w = 0; o = 0; s = 0; n = 0 }
      function clean(s) { gsub(/\r/,"",s); gsub(/\n/," ",s); gsub(/\t/," ",s); return s }
      NF == 5 {
        st = $3
        if (st == "error") e++
        else if (st == "warn") w++
        else if (st == "ok") o++
        else if (st == "skipped") s++
        n++
        c[n] = $1; nm[n] = $2; stt[n] = st; msg[n] = clean($4); rem[n] = clean($5)
      }
      END {
        if (e > 0) code = 2
        else if (w > 0) code = 1
        else code = 0
        printf "%d", code > exitfile
        close(exitfile)
        wc = length("category"); wn = length("name"); ws = length("status")
        for (i = 1; i <= n; i++) {
          if (length(c[i]) > wc) wc = length(c[i])
          if (length(nm[i]) > wn) wn = length(nm[i])
          if (length(stt[i]) > ws) ws = length(stt[i])
        }
        print ""
        printf "%-" wc "s  %-" wn "s  %-" ws "s  %s\n", "category", "name", "status", "message"
        for (j = 1; j <= wc + wn + ws + 22; j++) printf "-"
        print ""
        for (i = 1; i <= n; i++) {
          printf "%-" wc "s  %-" wn "s  %-" ws "s  %s\n", c[i], nm[i], stt[i], msg[i]
          if (rem[i] != "") {
            sp = wc + wn + ws + 6
            for (j = 1; j <= sp; j++) printf " "
            print "=> " rem[i]
          }
        }
        print ""
        printf "summary: errors=%d warnings=%d ok=%d skipped=%d\n", e + 0, w + 0, o + 0, s + 0
      }
    ' "$STREAM"
    ;;
  json)
    GEN="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    HOST="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)"
    PROJ="${COMPOSE_PROJECT_NAME:-unknown}"
    if ! command -v jq >/dev/null 2>&1; then
      echo "DOCTOR_FATAL: jq not found (required for --json)" >&2
      echo 3 >"$DOCTOR_EXIT_FILE"
      exit 3
    fi
    set +e
    python3 - "$STREAM" "$DOCTOR_EXIT_FILE" "$GEN" "$HOST" "$PROJ" <<'PY' | jq .
import json
import sys
from pathlib import Path

stream = Path(sys.argv[1])
exit_path = Path(sys.argv[2])
generated_at = sys.argv[3]
host = sys.argv[4]
proj = sys.argv[5]

MAXF = 2000

checks = []
for raw in stream.read_text(encoding="utf-8", errors="replace").splitlines():
    parts = raw.split("\t")
    if len(parts) != 5:
        continue
    cat, name, status, msg, rem = parts
    checks.append(
        {
            "category": cat,
            "name": name,
            "status": status,
            "message": msg[:MAXF],
            "remediation": rem[:MAXF],
        }
    )

summary = {"errors": 0, "warnings": 0, "ok": 0, "skipped": 0}
for ch in checks:
    st = ch["status"]
    if st == "error":
        summary["errors"] += 1
    elif st == "warn":
        summary["warnings"] += 1
    elif st == "ok":
        summary["ok"] += 1
    elif st == "skipped":
        summary["skipped"] += 1

if summary["errors"] > 0:
    code = 2
elif summary["warnings"] > 0:
    code = 1
else:
    code = 0
exit_path.write_text(str(code), encoding="utf-8")

doc = {
    "version": 1,
    "generated_at": generated_at,
    "summary": summary,
    "checks": checks,
    "meta": {"compose_project": proj, "hostname": host},
}
print(json.dumps(doc, ensure_ascii=False))
PY
    pipe_ret=$?
    set -e
    if [ "$pipe_ret" != "0" ]; then
      echo "DOCTOR_FATAL: JSON assembly failed (pipeline exit $pipe_ret)" >&2
      echo 3 >"$DOCTOR_EXIT_FILE"
      exit 3
    fi
    ;;
  *)
    echo "DOCTOR_FATAL: unknown format mode" >&2
    echo 3 >"$DOCTOR_EXIT_FILE"
    exit 3
    ;;
esac

exit 0
