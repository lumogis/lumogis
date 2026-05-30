"""
Filesystem MCP server for Lumogis.

Uses the orchestrator's semantic search API (GET /search) for
content-aware file retrieval instead of pure filename matching.
"""

import json
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "filesystem-mcp",
    description="Filesystem access under a configurable root for Lumogis.",
)

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000")


def _resolved_path_under_root(resolved: Path, root: Path) -> bool:
    root_resolved = root.expanduser().resolve(strict=False)
    path_resolved = resolved.expanduser().resolve(strict=False)
    root_str = str(root_resolved)
    path_str = str(path_resolved)
    return path_str == root_str or path_str.startswith(root_str + os.sep)


def _resolved_path_under_any_root(resolved: Path, roots: list[Path]) -> bool:
    if not roots:
        return False
    path_resolved = resolved.expanduser().resolve(strict=False)
    return any(_resolved_path_under_root(path_resolved, root) for root in roots)


def _effective_ingest_roots() -> list[Path]:
    raw = os.environ.get("INGEST_PATHS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                out = [Path(str(p)).expanduser().resolve(strict=False) for p in parsed if str(p).strip()]
                if out:
                    return out
        except json.JSONDecodeError:
            pass
    return [Path(os.environ.get("FILESYSTEM_ROOT", "/data")).expanduser().resolve(strict=False)]


INGEST_ROOTS = _effective_ingest_roots()


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
    """Read the first 3000 characters of a file within configured ingest roots."""
    try:
        resolved = Path(path).resolve()
        if not _resolved_path_under_any_root(resolved, INGEST_ROOTS):
            return f"Error: path is outside the allowed ingest roots"
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(3000)
        truncated = len(content) >= 3000
        return f"{'[truncated] ' if truncated else ''}{content}"
    except Exception as e:
        return f"Error reading {path}: {e}"
