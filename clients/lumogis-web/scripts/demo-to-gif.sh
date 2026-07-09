#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Convert the Playwright demo recordings (LUM-181) into one launch GIF.
# The demo spec writes two .webm files (admin scenes, then member scenes) into
# a video dir; this concatenates them in recording order and produces a GIF
# under the 5 MB README budget.
#
# Usage:
#   ./scripts/demo-to-gif.sh [VIDEO_DIR] [OUT_GIF]
#   ./scripts/demo-to-gif.sh test-results/demo/video docs/assets/demo.gif
#
# Requires ffmpeg. For higher quality at the same size, install `gifski` and
# set USE_GIFSKI=1.
set -euo pipefail

VIDEO_DIR="${1:-test-results/demo/video}"
OUT_GIF="${2:-docs/assets/demo.gif}"
FPS="${FPS:-13}"          # 12–15 reads well and keeps size down
WIDTH="${WIDTH:-1120}"    # scale down from 1280 to hit <5 MB; bump if you have budget
USE_GIFSKI="${USE_GIFSKI:-0}"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found — install it (brew install ffmpeg / apt install ffmpeg)"; exit 1; }

# Collect the .webm clips in recording (mtime) order: admin scenes, then member.
mapfile -t CLIPS < <(ls -1tr "$VIDEO_DIR"/*.webm 2>/dev/null || true)
[ "${#CLIPS[@]}" -gt 0 ] || { echo "No .webm files in $VIDEO_DIR — run the demo spec first."; exit 1; }
echo "Clips (in order):"; printf '  %s\n' "${CLIPS[@]}"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
CONCAT="$TMP/concat.txt"; : > "$CONCAT"
for c in "${CLIPS[@]}"; do echo "file '$(cd "$(dirname "$c")" && pwd)/$(basename "$c")'" >> "$CONCAT"; done

# 1) Concatenate the clips into one mp4 (re-encode so timestamps are clean).
ffmpeg -y -f concat -safe 0 -i "$CONCAT" -vf "fps=$FPS,scale=$WIDTH:-2:flags=lanczos" "$TMP/joined.mp4" >/dev/null 2>&1

mkdir -p "$(dirname "$OUT_GIF")"

if [ "$USE_GIFSKI" = "1" ] && command -v gifski >/dev/null; then
  # 2a) gifski path (best quality/size).
  ffmpeg -y -i "$TMP/joined.mp4" "$TMP/f%05d.png" >/dev/null 2>&1
  gifski --fps "$FPS" --width "$WIDTH" -o "$OUT_GIF" "$TMP"/f*.png
else
  # 2b) ffmpeg palettegen path (no extra deps).
  ffmpeg -y -i "$TMP/joined.mp4" -vf "palettegen=stats_mode=diff" "$TMP/palette.png" >/dev/null 2>&1
  ffmpeg -y -i "$TMP/joined.mp4" -i "$TMP/palette.png" \
    -lavfi "paletteuse=dither=bayer:bayer_scale=3" "$OUT_GIF" >/dev/null 2>&1
fi

SIZE="$(du -h "$OUT_GIF" | cut -f1)"
echo "Wrote $OUT_GIF ($SIZE)."
echo "If it's over 5 MB: lower FPS (FPS=10), narrow WIDTH (WIDTH=960), or trim the beats in the spec."
