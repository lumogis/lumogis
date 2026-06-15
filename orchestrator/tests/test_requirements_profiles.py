# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Layered orchestrator requirements profiles (LUM-460)."""

from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parent.parent
CORE = ORCHESTRATOR / "requirements-core.txt"
FULL = ORCHESTRATOR / "requirements.txt"
DOCKERFILE = ORCHESTRATOR / "Dockerfile"

ML_PINS = ("torch", "transformers", "nvidia", "triton", "sentence-transformers")


def _non_comment_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_requirements_core_exists() -> None:
    assert CORE.is_file()
    lines = _non_comment_lines(CORE)
    assert not any("sentence-transformers" in line for line in lines)


def test_requirements_full_includes_core() -> None:
    text = FULL.read_text(encoding="utf-8")
    assert "-r requirements-core.txt" in text


def test_requirements_full_has_sentence_transformers() -> None:
    lines = _non_comment_lines(FULL)
    st_lines = [line for line in lines if "sentence-transformers" in line]
    assert len(st_lines) == 1


def test_requirements_core_excludes_ml_pins() -> None:
    lines = _non_comment_lines(CORE)
    for pin in ML_PINS:
        assert not any(pin in line for line in lines), f"unexpected {pin} in requirements-core.txt"


def test_dockerfile_copies_both_requirements_files() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "requirements-core.txt" in text
    assert "requirements.txt" in text
    copy_idx = text.index("COPY")
    pip_idx = text.index("pip install")
    assert copy_idx < pip_idx
