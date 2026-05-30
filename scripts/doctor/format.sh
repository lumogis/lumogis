#!/usr/bin/env bash
# Lumogis doctor — format aggregated TSV stream (LUM-199, LUM-320).
set -euo pipefail
set -o pipefail
MODE="${1:?}"
STREAM="${2:?}"
DOCTOR_EXIT_FILE="${DOCTOR_EXIT_FILE:?}"

DOCTOR_JSON="${DOCTOR_JSON:-0}"
DOCTOR_FIX="${DOCTOR_FIX:-0}"
DOCTOR_REPAIRS_JSON="${DOCTOR_REPAIRS_JSON:-}"

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
      (NF == 5 || NF == 7) {
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
    if [ "${DOCTOR_FIX:-0}" = "1" ] && [ -n "${DOCTOR_REPAIRS_JSON:-}" ] && [ -f "$DOCTOR_REPAIRS_JSON" ]; then
      echo ""
      echo "repairs (see JSON with --json --fix for machine contract):"
      python3 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); rs=d if isinstance(d,list) else (d.get('repairs') or [] if isinstance(d,dict) else []); [print(' -', r.get('kind'), r.get('outcome'), r.get('command_display', r.get('command',''))) for r in rs]" "$DOCTOR_REPAIRS_JSON"
    fi
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
    if [ "$DOCTOR_FIX" = "1" ]; then
      if [ -z "$DOCTOR_REPAIRS_JSON" ] || [ ! -f "$DOCTOR_REPAIRS_JSON" ]; then
        echo "DOCTOR_FATAL: --json --fix requires DOCTOR_REPAIRS_JSON" >&2
        echo 3 >"$DOCTOR_EXIT_FILE"
        exit 3
      fi
      set +e
      python3 - "$STREAM" "$DOCTOR_EXIT_FILE" "$GEN" "$HOST" "$PROJ" "$DOCTOR_REPAIRS_JSON" <<'PY' | jq .
import json
import sys
from pathlib import Path

stream = Path(sys.argv[1])
exit_path = Path(sys.argv[2])
generated_at = sys.argv[3]
host = sys.argv[4]
proj = sys.argv[5]
repairs_path = Path(sys.argv[6])

MAXF = 2000

checks = []
for raw in stream.read_text(encoding="utf-8", errors="replace").splitlines():
    parts = raw.split("\t")
    if len(parts) not in (5, 7):
        continue
    cat, name, status, msg, rem = parts[:5]
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

blob = json.loads(repairs_path.read_text(encoding="utf-8"))
if isinstance(blob, dict):
    repairs = blob.get("repairs") or []
    apply_requested = bool(blob.get("apply_requested", False))
    any_applied = bool(blob.get("any_applied", False))
elif isinstance(blob, list):
    repairs = blob
    apply_requested = False
    any_applied = any(
        isinstance(r, dict) and r.get("outcome") == "applied" for r in blob
    )
else:
    repairs = []
    apply_requested = False
    any_applied = False
if not isinstance(repairs, list):
    repairs = []
dry_run = not apply_requested

doc = {
    "version": 2,
    "apply_requested": apply_requested,
    "any_applied": any_applied,
    "dry_run": dry_run,
    "generated_at": generated_at,
    "summary": summary,
    "checks": checks,
    "meta": {"compose_project": proj, "hostname": host},
    "repairs": repairs,
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
    else
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
    if len(parts) not in (5, 7):
        continue
    cat, name, status, msg, rem = parts[:5]
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
    fi
    ;;
  *)
    echo "DOCTOR_FATAL: unknown format mode" >&2
    echo 3 >"$DOCTOR_EXIT_FILE"
    exit 3
    ;;
esac

exit 0
