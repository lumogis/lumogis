# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-310 — the DR backup image must build and run on arm64 as well as amd64.

The default ``docker compose up -d`` builds the backup image, so an x86-64-only
step there broke the whole first run on Apple Silicon (found by the LUM-310
clean-host walkthrough). Two steps were x86-64 only: the vendored
``redis-check-rdb`` loader and the supercronic binary. The real builds are
proven on each architecture by hand; these guards keep the assumptions out.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "docker" / "backup" / "Dockerfile"
_INSTALLER = _ROOT / "docker" / "backup" / "install-redis-check-rdb-v13.sh"
_COMMON = _ROOT / "scripts" / "backup" / "lib" / "common.sh"

_LOADER = "/opt/falkordb-redis-check/ld.so"


def _code(path: Path) -> str:
    """The file without comment lines."""
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#"))


def test_supercronic_is_chosen_by_target_arch_with_a_checksum_each():
    text = _code(_DOCKERFILE)
    assert re.search(r"^ARG TARGETARCH$", text, re.M), "the Dockerfile must declare TARGETARCH"
    for arch, sha in (
        ("amd64", "71b0d58cc53f6bd72cf2f293e09e294b79c666d8"),
        ("arm64", "e0f0c06ebc5627e43b25475711e694450489ab00"),
    ):
        assert re.search(
            rf"{arch}\) SUPERCRONIC=supercronic-linux-{arch} SUPERCRONIC_SHA1SUM={sha}", text
        ), f"no pinned supercronic binary and checksum for {arch}"
    assert "sha1sum -c" in text
    assert "ENV SUPERCRONIC_URL" not in text, "a fixed ENV URL pins one architecture"


def test_redis_check_loader_is_picked_per_architecture():
    text = _code(_INSTALLER)
    assert re.search(
        r"x86_64\)\s+TRIPLET=\"x86_64-linux-gnu\"\s+DONOR_LOADER=\"/lib64/ld-linux-x86-64\.so\.2\"",
        text,
    )
    assert re.search(
        r"aarch64 \| arm64\)\s+TRIPLET=\"aarch64-linux-gnu\"\s+"
        r"DONOR_LOADER=\"/lib/ld-linux-aarch64\.so\.1\"",
        text,
    )
    assert f'LOADER="{_LOADER}"' in text
    # Outside its own case arm, no line may name one architecture's loader or libs.
    rest = re.sub(r"case \"\$\(uname -m\)\" in.*?\nesac", "", text, flags=re.S)
    assert "x86_64" not in rest and "x86-64" not in rest and "aarch64" not in rest, rest


def test_dockerfile_check_and_runtime_use_the_same_loader_path():
    assert f"&& {_LOADER} \\" in _code(_DOCKERFILE)
    assert f"FALKORDB_REDIS_CHECK_RDB_LOADER:-{_LOADER}" in _code(_COMMON)
    for path in (_DOCKERFILE, _COMMON):
        assert "ld-linux-x86-64" not in _code(path), f"{path.name} still names the x86-64 loader"
