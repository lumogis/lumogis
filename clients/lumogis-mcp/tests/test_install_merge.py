# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""Installer merge tests for scripts/install-cursor-mcp.sh."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-cursor-mcp.sh"


def test_install_preserves_other_servers(tmp_path):
    home = tmp_path / "home"
    cursor_dir = home / ".cursor"
    cursor_dir.mkdir(parents=True)
    existing = {
        "mcpServers": {
            "other-server": {"type": "stdio", "command": "echo", "args": ["hi"]},
        }
    }
    (cursor_dir / "mcp.json").write_text(json.dumps(existing, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LUMOGIS_MCP_TOKEN"] = "lmcp_testtoken"
    subprocess.run(["bash", str(INSTALL_SCRIPT)], check=True, env=env, capture_output=True)

    merged = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))
    assert "other-server" in merged["mcpServers"]
    assert "lumogis" in merged["mcpServers"]
    lumogis = merged["mcpServers"]["lumogis"]
    assert lumogis["type"] == "stdio"
    assert lumogis["command"] == "uvx"
    assert lumogis["env"]["LUMOGIS_MCP_TOKEN"] == "${env:LUMOGIS_MCP_TOKEN}"
    assert any("git+https://github.com/lumogis/lumogis@" in arg for arg in lumogis["args"])

    mode = (cursor_dir / "mcp.json").stat().st_mode & 0o777
    assert mode == 0o600
