# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The one place Core reads and writes the install folder's ``.env`` (LUM-989).

Before this module, ``routes/admin.py`` and the RC seed script each carried a copy
of ``_rewrite_host_env_key`` and wrote the file with a plain ``write_text`` (not
atomic, and free to change its mode). Every writer now goes through here:

* **One lock** around every read-modify-write, so two admin actions cannot lose
  each other's change.
* **Atomic**: the new content replaces the file in one step; a failure leaves the
  old file.
* **Mode and owner kept**: ``.env`` holds secrets and is ``0640`` on a checkout;
  Core runs as root in some deployments and must not leave a household's file
  root-owned or world-readable.
* **Line endings kept**: a CRLF file stays CRLF.
* **One parse rule** for reads, the way Compose reads an env file (a superset of
  the entrypoint's ``_lum_env_value``, which only trims and strips a pair of
  quotes): the last assignment wins; ``export `` is accepted; a trailing CR is
  dropped; surrounding whitespace is trimmed; one matching pair of quotes is
  stripped; ``#`` starts a comment only after whitespace when the value is
  unquoted. ``os.replace`` swaps a symlinked ``.env`` for a regular file.

Read-only parsers elsewhere (``services/stack_control.py``, stack-control's own)
are left alone on purpose: they do not write, and the sidecar is a separate image.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
from pathlib import Path
from typing import Callable

from compose_ingest_binds import project_env_writable
from services._atomic_file import atomic_write_text

_log = logging.getLogger(__name__)

ENV_PATH = Path("/project/.env")

# The entrypoint's ``_lum_is_placeholder`` set plus the sample the LUM-937 copy
# showed for a hand-set graph secret.
PLACEHOLDERS = frozenset(
    {"", "change-me-in-production", "__GENERATE_ME__", "<a long random value>"}
)

_lock = threading.Lock()


class EnvUnwritable(OSError):
    """``.env`` is missing or its folder is not writable from this container."""


def _path(path: Path | str | None) -> Path:
    return Path(path) if path is not None else ENV_PATH


def _read_text(path: Path) -> str:
    """Read without newline translation, so a CRLF file stays CRLF."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


# --------------------------------------------------------------------- parsing

_ASSIGN = r"^[ \t]*{export}{key}[ \t]*=.*(?:\r?\n)?"


def _assign_re(key: str, *, export: bool) -> re.Pattern[str]:
    prefix = r"(?:export[ \t]+)?" if export else ""
    return re.compile(_ASSIGN.format(export=prefix, key=re.escape(key)), re.MULTILINE)


def parse_value(raw: str) -> str:
    """Normalise the text after ``=`` the way the entrypoint (and Compose) do."""
    v = raw.rstrip("\r\n").strip()
    if v[:1] in ("'", '"'):
        quote = v[0]
        end = v.find(quote, 1)
        if end != -1:
            return v[1:end]
        return v  # an unclosed quote is left as written
    m = re.search(r"\s#", v)
    if m:
        v = v[: m.start()]
    return v.strip()


def _read_value(content: str, key: str) -> str | None:
    pattern = re.compile(rf"^[ \t]*(?:export[ \t]+)?{re.escape(key)}[ \t]*=(.*)$", re.MULTILINE)
    found = pattern.findall(content)
    return parse_value(found[-1]) if found else None


def read_key(key: str, path: Path | str | None = None) -> str | None:
    """The value of ``key`` in ``.env`` (last assignment wins), or None.

    None when the file is missing, unreadable, or does not assign the key. An empty
    assignment is ``""``.
    """
    try:
        text = _read_text(_path(path))
    except OSError:
        return None
    return _read_value(text, key)


def get_value(content: str, key: str) -> str | None:
    """Like :func:`read_key` for content already in hand."""
    return _read_value(content, key)


def _eol(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def rewrite_key(content: str, key: str, value: str) -> str:
    """Strip every ``key=...`` line (flexible whitespace) and append one canonical line.

    A strict ``^KEY=`` regex misses ``KEY = value`` and duplicate lines. Appending
    then leaves an older assignment in place; Compose can keep RERANKER_BACKEND=bge
    while app_settings says false, so the dashboard shows the wrong state after
    restart. Moved from ``routes/admin.py``; unchanged for LF files, and a CRLF file
    stays CRLF. A commented ``# KEY=...`` line is not an assignment and is kept.
    """
    eol = _eol(content)
    content = _assign_re(key, export=False).sub("", content).rstrip()
    if content:
        content += eol
    content += f"{key}={value}{eol}"
    return content


def remove_key(content: str, key: str) -> str:
    """Strip every assignment of ``key`` (``export`` form too); append nothing."""
    return _assign_re(key, export=True).sub("", content)


def set_key(content: str, key: str, value: str) -> str:
    """Replace every assignment of ``key`` (``export`` form too) with one canonical line.

    Unlike :func:`rewrite_key` (kept byte-compatible with the old admin helper) this
    also removes ``export KEY=...`` lines, so a hand-set value and a later write
    leave exactly one line. A value with a line break is refused: it would write a
    second assignment.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("an .env value cannot contain a line break")
    eol = _eol(content)
    content = remove_key(content, key).rstrip()
    if content:
        content += eol
    return content + f"{key}={value}{eol}"


# ------------------------------------------------------------------ list values


def split_list(value: str | None) -> list[str]:
    """Comma list: items trimmed, empty items dropped, order kept, no duplicates."""
    seen: list[str] = []
    for item in (value or "").split(","):
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def list_with(value: str | None, item: str) -> str:
    """``value`` with ``item`` added once. ``*`` (every profile) is left as it is."""
    items = split_list(value)
    if "*" in items or item in items:
        return ",".join(items)
    return ",".join([*items, item])


def list_without(value: str | None, item: str) -> str:
    """``value`` with ``item`` removed. ``*`` is left as it is."""
    items = split_list(value)
    if "*" in items:
        return ",".join(items)
    return ",".join(i for i in items if i != item)


# --------------------------------------------------------------------- writing


def writable(path: Path | str | None = None) -> bool:
    """True when ``.env`` exists and its folder is writable (the existing predicate)."""
    return project_env_writable(_path(path))


def _write_locked(path: Path, content: str) -> None:
    """Atomic write keeping the file's mode, then its owner (best effort)."""
    try:
        before = os.stat(path)
    except OSError as exc:
        raise EnvUnwritable(f"{path} is not readable: {exc}") from exc
    try:
        atomic_write_text(path, content, mode=stat.S_IMODE(before.st_mode))
    except OSError as exc:
        raise EnvUnwritable(f"{path} could not be written: {exc}") from exc
    try:
        after = os.stat(path)
        if (after.st_uid, after.st_gid) != (before.st_uid, before.st_gid):
            os.chown(path, before.st_uid, before.st_gid)
    except OSError as exc:
        # Not fatal (not root, or a filesystem without owners); the content is written.
        _log.warning(
            "host_env: could not restore the owner of %s: %s", path, exc.__class__.__name__
        )


def update(fn: Callable[[str], str], path: Path | str | None = None) -> bool:
    """Locked read-modify-write: ``fn(content) -> content``. True when it wrote.

    Raises :class:`EnvUnwritable` when the file is missing or cannot be written. No
    write happens when ``fn`` returns the content unchanged.
    """
    target = _path(path)
    with _lock:
        try:
            content = _read_text(target)
        except OSError as exc:
            raise EnvUnwritable(f"{target} is not readable: {exc}") from exc
        new = fn(content)
        if new == content:
            return False
        _write_locked(target, new)
        return True


def write_keys(updates: dict[str, str | None], path: Path | str | None = None) -> bool:
    """Set (``str``) or remove (``None``) several keys in one atomic write."""

    def apply(content: str) -> str:
        for key, value in updates.items():
            content = remove_key(content, key) if value is None else set_key(content, key, value)
        return content

    return update(apply, path)
