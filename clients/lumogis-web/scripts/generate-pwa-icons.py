#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Regenerates PWA launcher icons under public/icons/.
# Requires: Pillow (`pip install pillow` / system python3-PIL).

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Align with src/design/tokens.css default light-theme accents / shell.
BG = "#0b1020"
ACCENT = "#2747d3"


def _draw_icon(size: int):
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)
    margin = max(size // 5, 12)
    thick = max(size // 10, 6)
    x0, y0 = margin, margin
    x1, y1 = size - margin, size - margin
    # Simple geometric "L" — calm, readable at launcher sizes.
    draw.rectangle([x0, y0, x0 + thick, y1], fill=ACCENT)
    draw.rectangle([x0, y1 - thick, x1, y1], fill=ACCENT)
    return img


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "public" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon-192", 192), ("icon-512", 512)):
        path = out_dir / f"{name}.png"
        _draw_icon(size).save(path, format="PNG", optimize=True)
        print(f"wrote {path.relative_to(root)}")


if __name__ == "__main__":
    main()
