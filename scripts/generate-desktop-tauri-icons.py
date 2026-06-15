#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis
#
# Regenerate Tauri bundle + tray PNG/ICO assets from branding/logo-mark.svg
# (full constellation — core + satellites). Transparent background throughout.
#
# Requires: ImageMagick `convert` on PATH.

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANDING = ROOT / "branding"
LOGO_MARK = BRANDING / "logo-mark.svg"

ICON_DIRS = (
    ROOT / "clients" / "lumogis-search" / "src-tauri" / "icons",
    ROOT / "apps" / "lumogis-hub" / "src-tauri" / "icons",
)

# All sizes rasterized from the same SVG (tray uses 32x32).
SIZES_MARK = (16, 32, 64, 128, 256, 512)


def raster_logo_mark(size: int, out: Path) -> None:
    if not LOGO_MARK.is_file():
        raise SystemExit(f"missing {LOGO_MARK}")
    convert = shutil.which("convert")
    if not convert:
        raise SystemExit("ImageMagick `convert` not found — install imagemagick")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Extra density on small tray sizes keeps satellite lines readable.
    density = max(384, size * 12)
    subprocess.run(
        [
            convert,
            "-background",
            "none",
            "-density",
            str(density),
            str(LOGO_MARK),
            "-resize",
            f"{size}x{size}",
            f"PNG32:{out}",
        ],
        check=True,
    )


def write_ico(sources: list[tuple[int, Path]], out: Path) -> None:
    convert = shutil.which("convert")
    if not convert:
        raise SystemExit("ImageMagick `convert` not found for icon.ico")
    args = [convert]
    for _size, path in sources:
        args.append(str(path))
    args.append(str(out))
    subprocess.run(args, check=True)


def generate_for_dir(icon_dir: Path) -> None:
    icon_dir.mkdir(parents=True, exist_ok=True)
    tmp = icon_dir / ".gen-tmp"
    tmp.mkdir(exist_ok=True)

    mark_paths: dict[int, Path] = {}
    for size in SIZES_MARK:
        path = tmp / f"mark-{size}.png"
        raster_logo_mark(size, path)
        mark_paths[size] = path

    # Tauri bundle set (see tauri.conf.json `bundle.icon`)
    shutil.copy2(mark_paths[32], icon_dir / "32x32.png")
    shutil.copy2(mark_paths[128], icon_dir / "128x128.png")
    shutil.copy2(mark_paths[256], icon_dir / "128x128@2x.png")
    shutil.copy2(mark_paths[512], icon_dir / "icon.png")
    shutil.copy2(mark_paths[64], icon_dir / "64x64.png")
    shutil.copy2(mark_paths[256], icon_dir / "256x256.png")
    shutil.copy2(mark_paths[512], icon_dir / "512x512.png")

    write_ico(
        [
            (16, mark_paths[16]),
            (32, mark_paths[32]),
            (48, mark_paths[64]),
            (64, mark_paths[64]),
            (128, mark_paths[128]),
            (256, mark_paths[256]),
        ],
        icon_dir / "icon.ico",
    )

    shutil.rmtree(tmp)
    print(f"updated {icon_dir.relative_to(ROOT)}")


def main() -> None:
    for d in ICON_DIRS:
        generate_for_dir(d)
    print("done — rebuild with make search-build / hub-build to pick up icons")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"icon generation failed: {exc}", file=sys.stderr)
        sys.exit(1)
