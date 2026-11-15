# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tell an install from the release files that its docker-compose.yml is old (LUM-980).

A household updates with ``docker compose pull && docker compose up -d``. That
refreshes the images but not ``docker-compose.yml``, which they downloaded once.
When a release changes the compose file, the new images may expect a service,
mount or variable the old file lacks.

``scripts/release/build_release_compose.py`` writes ``# lumogis-compose: <fp>``
into the header of the release file, and ``publish-image.yml`` bakes the same
fingerprint into the orchestrator image as ``LUMOGIS_COMPOSE_FINGERPRINT``. This
module compares the two. It is read-only, never blocks start-up, and any error
counts as ``not_applicable``.

Results:
  * ``not_applicable`` — no header line (a git checkout), or a local build (``dev``);
  * ``ok`` — the values match;
  * ``stale`` — they differ: logged as ``compose_file_stale`` and shown in
    Admin → Diagnostics.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

_log = logging.getLogger(__name__)

COMPOSE_PATH = Path("/project/docker-compose.yml")
HEADER = re.compile(r"^# lumogis-compose: ([0-9a-f]{16})\s*$")
# The fingerprint is in the generated header, well inside the first lines.
_HEADER_LINES = 40

STALE_MESSAGE = (
    "Your docker-compose.yml does not match this release's images. Download the "
    "docker-compose.yml of the release you run (keep your .env): see "
    "docs/deployment/upgrading.md on GitHub."
)

Status = Literal["ok", "stale", "not_applicable"]


@dataclass(frozen=True)
class ComposeFreshness:
    status: Status
    file_fingerprint: str | None = None
    image_fingerprint: str | None = None


def _file_fingerprint(path: Path) -> str | None:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for _ in range(_HEADER_LINES):
            line = fh.readline()
            if not line:
                break
            m = HEADER.match(line)
            if m:
                return m.group(1)
    return None


def check(path: Path | None = None, image_fingerprint: str | None = None) -> ComposeFreshness:
    """Compare the install's compose header with the image's fingerprint."""
    path = path if path is not None else COMPOSE_PATH
    image_fp = (
        image_fingerprint
        if image_fingerprint is not None
        else os.environ.get("LUMOGIS_COMPOSE_FINGERPRINT", "dev")
    ).strip()
    if not image_fp or image_fp == "dev":
        return ComposeFreshness("not_applicable", image_fingerprint=image_fp or None)
    try:
        file_fp = _file_fingerprint(path)
    except OSError:
        return ComposeFreshness("not_applicable", image_fingerprint=image_fp)
    if file_fp is None:
        return ComposeFreshness("not_applicable", image_fingerprint=image_fp)
    status: Status = "ok" if file_fp == image_fp else "stale"
    return ComposeFreshness(status, file_fingerprint=file_fp, image_fingerprint=image_fp)


@lru_cache(maxsize=1)
def startup_result() -> ComposeFreshness:
    """Checked once per process; the compose file cannot change under a running container."""
    try:
        result = check()
    except Exception:  # noqa: BLE001 - advisory only, never breaks start-up
        _log.exception("compose freshness check failed")
        return ComposeFreshness("not_applicable")
    if result.status == "stale":
        _log.warning(
            "compose_file_stale: docker-compose.yml fingerprint %s, this image expects %s. %s",
            result.file_fingerprint,
            result.image_fingerprint,
            STALE_MESSAGE,
        )
    return result
