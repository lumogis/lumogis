#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Merge Lumogis stdio MCP bridge stanza into ~/.cursor/mcp.json (LUM-292).
set -euo pipefail

# Pin a git ref that contains clients/lumogis-mcp/. Bump after public AGPL export.
LUMOGIS_MCP_GIT_REF="${LUMOGIS_MCP_GIT_REF:-dev}"

CURSOR_DIR="${HOME}/.cursor"
MCP_JSON="${CURSOR_DIR}/mcp.json"

if [[ -z "${LUMOGIS_MCP_TOKEN:-}" ]]; then
  echo "Warning: LUMOGIS_MCP_TOKEN is unset." >&2
  echo "  Mint lmcp_… via Lumogis Web → MCP tokens or POST /api/v1/me/mcp-tokens" >&2
  echo "  (see docs/private/ops/connect-and-verify.md Step 9d), export the token," >&2
  echo "  then restart Cursor when Core requires auth." >&2
fi

mkdir -p "${CURSOR_DIR}"

if [[ -f "${MCP_JSON}" ]]; then
  backup="${MCP_JSON}.bak.$(date +%Y%m%d%H%M%S)"
  cp "${MCP_JSON}" "${backup}"
  chmod 600 "${backup}"
fi

export LUMOGIS_MCP_GIT_REF
python3 - <<'PY'
import json
import os
from pathlib import Path

cursor_dir = Path(os.environ["HOME"]) / ".cursor"
mcp_json = cursor_dir / "mcp.json"
git_ref = os.environ["LUMOGIS_MCP_GIT_REF"]

data: dict = {"mcpServers": {}}
if mcp_json.exists():
    loaded = json.loads(mcp_json.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        data = loaded
servers = data.setdefault("mcpServers", {})
servers["lumogis"] = {
    "type": "stdio",
    "command": "uvx",
    "args": [
        "--from",
        f"git+https://github.com/lumogis/lumogis@{git_ref}#subdirectory=clients/lumogis-mcp",
        "lumogis-mcp",
    ],
    "env": {
        "LUMOGIS_MCP_TOKEN": "${env:LUMOGIS_MCP_TOKEN}",
    },
}
mcp_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

chmod 600 "${MCP_JSON}"

cat <<EOF

Lumogis MCP bridge installed under mcpServers.lumogis in ${MCP_JSON}.

Restart Cursor to pick up the server. Export LUMOGIS_MCP_TOKEN in the environment
Cursor inherits (Linux desktop launchers may not load your shell profile).

EOF
