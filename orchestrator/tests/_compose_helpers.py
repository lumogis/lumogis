"""Shared compose-file and Caddyfile helpers for static config guards.

Single responsibility: locate the repo root, read a compose ``ports`` entry,
load compose files that use ``!reset`` / ``!override`` tags, and extract a
brace-balanced Caddyfile block. Used by the LiteLLM loopback test (LUM-880)
and the deployment hardening guards (LUM-887).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_VAR = re.compile(r"\$\{[^}]*\}")


def strip_vars(text: str) -> str:
    """Replace compose ``${VAR:-default}`` interpolations (colons and all) with ``X``."""
    return _VAR.sub("X", text)


def find_repo_root(marker: str = "docker-compose.yml") -> Path:
    """Walk up to the repo root; fall back to the ``/project`` dev mount."""
    for candidate in (*Path(__file__).resolve().parents, Path("/project")):
        if (candidate / marker).is_file():
            return candidate
    raise RuntimeError(f"Unable to locate {marker} walking up from this test file and at /project.")


def host_ip(entry: object) -> str | None:
    """Return the host IP of a compose ``ports`` entry, or None if unbound."""
    if isinstance(entry, dict):  # long syntax
        return entry.get("host_ip")
    parts = strip_vars(str(entry)).split(":")
    # "HOST_IP:HOST_PORT:CONTAINER_PORT" has three parts; anything shorter
    # binds every interface.
    return parts[0] if len(parts) == 3 else None


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts compose's ``!reset`` / ``!override`` tags."""


def _plain_value(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!reset", _plain_value)
_ComposeLoader.add_multi_constructor("!override", _plain_value)


def load_compose(path: Path) -> dict:
    """Load a compose file; ``!reset`` / ``!override`` yield their plain value."""
    return yaml.load(path.read_text(), Loader=_ComposeLoader) or {}  # noqa: S506 - SafeLoader subclass


def caddy_block_after(text: str, start: int) -> str:
    """Return the brace-balanced body of the block opening at or after ``start``.

    Returns ``""`` when the next non-space character on the line is not ``{``
    (a one-liner such as ``reverse_proxy orchestrator:8000``). Braces inside
    ``#`` comments are ignored; ``{client_ip}`` and ``{$VAR:...}`` balance on
    their own.
    """
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    tail = text[start:line_end]
    # Only look at the part of the line before any comment.
    code = tail.split("#", 1)[0]
    if "{" not in code:
        return ""
    open_at = start + code.index("{")
    # Everything between ``start`` and the brace must be whitespace-free of
    # other tokens only for the one-liner test; here the first ``{`` on the
    # line opens the block.
    depth = 0
    body_start = open_at + 1
    i = open_at
    in_comment = False
    while i < len(text):
        ch = text[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
        elif ch == "#":
            in_comment = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:i]
        i += 1
    return text[body_start:]
