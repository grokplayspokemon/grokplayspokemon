#!/usr/bin/env python3
"""build_quest_overlay.py

Generate a PNG of the full Kanto map with **only the coordinates for one
specific quest** overlaid as solid-blue squares.  Useful for spot-checking
path alignment.

Default behaviour builds quest 001 (using `001_coords.json`).  Supply a
different quest id with `--quest-id 005` etc.  The script automatically
finds the coordinate JSON in `environment/environment_helpers/quest_paths/<id>/`.

Example
-------
    python build_quest_overlay.py           # quest 001 → overlay_quest_001.png
    python build_quest_overlay.py --quest-id 005 --output o5.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw

TILE_SIZE = 16
PAD = 20  # tiles of padding baked into full map PNG

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_DATA_DIR = BASE_DIR / "environment" / "data" / "environment_data"
MAP_PATH = ENV_DATA_DIR / "full_kanto_map.png"
QUEST_PATHS_ROOT = BASE_DIR / "environment" / "environment_helpers" / "quest_paths"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_quest_coords(quest_id: str) -> List[Tuple[int, int]]:
    """Return list of (gy, gx) pairs (WITHOUT padding) for the given quest."""
    quest_id = quest_id.zfill(3)
    coords_file = QUEST_PATHS_ROOT / quest_id / f"{quest_id}_coords.json"
    if not coords_file.exists():
        raise FileNotFoundError(f"Quest coordinate file not found: {coords_file}")

    coords: List[Tuple[int, int]] = []
    with open(coords_file, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    # Each key is a frame index; value is list of coordinate pairs.
    for step_pairs in data.values():
        for gy, gx in step_pairs:
            coords.append((int(gy), int(gx)))
    return coords


def overlay_blue_squares(base_img: Image.Image, coords: List[Tuple[int, int]]) -> Image.Image:
    draw = ImageDraw.Draw(base_img)
    blue = (0, 0, 255)

    for gy, gx in coords:
        # translate to padded global tile indices
        tile_x = gx + PAD
        tile_y = gy + PAD
        x0 = tile_x * TILE_SIZE
        y0 = tile_y * TILE_SIZE
        x1 = x0 + TILE_SIZE - 1
        y1 = y0 + TILE_SIZE - 1
        draw.rectangle([x0, y0, x1, y1], fill=blue)
    return base_img

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a quest-specific overlay map.")
    p.add_argument("--quest-id", default="001", help="Quest id (numeric, default 001)")
    p.add_argument("--output", type=Path, default=None, help="Output PNG filename (default overlay_quest_<id>.png)")
    return p.parse_args()


def main() -> None:
    args = parse_cli()

    base_img = Image.open(MAP_PATH).convert("RGB")
    coords = load_quest_coords(args.quest_id)
    overlay_img = overlay_blue_squares(base_img, coords)

    out_path = args.output
    if out_path is None:
        out_path = Path(f"overlay_quest_{args.quest_id.zfill(3)}.png")
    overlay_img.save(out_path, "PNG")
    print(f"✅ Saved overlay to {out_path} (quest {args.quest_id}) – {len(coords)} coordinates drawn.")


if __name__ == "__main__":
    main() 