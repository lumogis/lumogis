"""
Filesystem MCP server for Lumogis.

Uses the orchestrator's semantic search API (GET /search) for
content-aware file retrieval instead of pure filename matching.
"""

import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "filesystem-mcp",
    description="Filesystem access under a configurable root for Lumogis.",
)

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000")
FILESYSTEM_ROOT = Path(os.environ.get("FILESYSTEM_ROOT", "/data")).resolve()


@mcp.tool()
def search_files(query: str, limit: int = 5) -> str:
    """Search files by content or name using semantic search."""
    try:
        r = httpx.get(
            f"{ORCHESTRATOR_URL}/search",
            params={"q": query, "limit": limit},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.text
    except Exception as e:
        return f"Search failed: {e}"


@mcp.tool()
def read_file(path: str) -> str:
    """Read the first 3000 characters of a file within FILESYSTEM_ROOT."""
    try:
        resolved = Path(path).resolve()
        if not str(resolved).startswith(str(FILESYSTEM_ROOT)):
            return f"Error: path is outside the allowed root ({FILESYSTEM_ROOT})"
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(3000)
        truncated = len(content) >= 3000
        return f"{'[truncated] ' if truncated else ''}{content}"
    except Exception as e:
        return f"Error reading {path}: {e}"
