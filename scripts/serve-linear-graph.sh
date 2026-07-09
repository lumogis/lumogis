#!/usr/bin/env bash
# Serve tools/linear-graph.html locally (Linear API key is pasted in the browser).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${LINEAR_GRAPH_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/tools/linear-graph.html"
echo "Linear backlog graph: ${URL}"
echo "Paste lin_api_… in the API menu → Load (stored in this browser only)."
cd "${ROOT}"
exec python3 -m http.server "${PORT}" --bind 127.0.0.1
