# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Atomic text-file replacement (LUM-331).

Moved from ``services/email_egress_acl.write_allow_file`` so new writers do not
grow a fourth copy. ``routes/admin.py`` (settings writes) and ``config.py`` still
carry their own older copies; converge them here when those files are next
touched.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

_umask_lock = threading.Lock()


def _current_umask() -> int:
    """The process umask, without changing it where the platform allows."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    # Portable fallback: set and restore. Process-wide, so keep it short and locked.
    with _umask_lock:
        mask = os.umask(0o022)
        os.umask(mask)
    return mask


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Replace ``path`` with ``text`` atomically; a failure leaves the old file intact.

    The content goes to a uniquely named temp file in the same directory, is
    fsync'd, given its mode, then ``os.replace``'d over ``path`` (atomic on POSIX),
    so a reader never sees a half-written file and a mid-write crash (disk full,
    OOM-kill) never truncates the previous one.

    ``mode=None`` gives the file the permissions a plain ``open(path, "w")`` would
    (``0o666`` minus the umask). Callers whose file is read by another container
    rely on that — the Squid allow file is one. Pass an explicit mode (for example
    ``0o600``) for files holding secrets.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        try:
            fh = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode if mode is not None else (0o666 & ~_current_umask()))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
