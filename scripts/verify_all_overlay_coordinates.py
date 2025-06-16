#!/usr/bin/env python3
"""verify_all_overlay_coordinates.py

Validate that EVERY quest coordinate is drawn on an *overlay* PNG exactly at
its intended tile.  It works just like `overlay_player_on_global_map.verify_marker`
but iterates through **all** quest steps in `combined_quest_coordinates_continuous.json`.

Exit status:
  • 0 – All coordinates verified successfully.
  • 1 – One or more coordinates failed (details printed).

Example usage
-------------
    # After you generated overlay_test.png with build_overlay_map.py
    python verify_all_overlay_coordinates.py \
        --overlay grok_plays_pokemon/overlay_test.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Tuple, List

import numpy as np
from PIL import Image

TILE_SIZE = 16
PAD = 20  # tile padding baked into full_kanto_map.png

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_DATA_DIR = BASE_DIR / "environment" / "data" / "environment_data"
DEFAULT_MAP_PATH = ENV_DATA_DIR / "full_kanto_map.png"
COORDS_JSON = (
    BASE_DIR / "environment" / "environment_helpers" / "quest_paths" /
    "combined_quest_coordinates_continuous.json"
)

# ---------------------------------------------------------------------------
# Quest coordinate loader
# ---------------------------------------------------------------------------

def load_all_quest_coords() -> List[Tuple[int, int]]:
    """Return list of (gy, gx) pairs **without padding** for all quest steps."""
    try:
        with open(COORDS_JSON, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return [(int(gy), int(gx)) for gy, gx in data["coordinates"]]
    except Exception as exc:
        print(f"❌ Could not load quest-coordinate JSON: {exc}", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Verification helpers (re-using logic from overlay_player_on_global_map)
# ---------------------------------------------------------------------------

def centre_patch_diff(base_arr: np.ndarray, over_arr: np.ndarray,
                      tile_xy: Tuple[int, int], patch_rad: int = 4) -> bool:
    """Return True if *over_arr* differs from *base_arr* inside the tile centre."""
    tile_x, tile_y = tile_xy
    px_c = tile_x * TILE_SIZE + TILE_SIZE // 2
    py_c = tile_y * TILE_SIZE + TILE_SIZE // 2

    y0 = max(0, py_c - patch_rad)
    y1 = min(over_arr.shape[0], py_c + patch_rad)
    x0 = max(0, px_c - patch_rad)
    x1 = min(over_arr.shape[1], px_c + patch_rad)

    patch_base = base_arr[y0:y1, x0:x1]
    patch_over = over_arr[y0:y1, x0:x1]
    diff = np.any(patch_base != patch_over, axis=-1)
    return diff.any()

# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify every quest coordinate marker in an overlay PNG.")
    p.add_argument("--overlay", required=True, type=Path, help="Path to overlay PNG (with quest markers)")
    p.add_argument("--map", dest="map_path", default=DEFAULT_MAP_PATH, type=Path,
                   help="Pristine full_kanto_map.png (default: detected path)")
    p.add_argument("--limit", type=int, default=None,
                   help="Optionally verify only the first N coordinates (debug speed)")
    return p.parse_args()


def main() -> None:
    args = parse_cli()

    if not args.overlay.exists():
        print(f"Overlay file not found: {args.overlay}", file=sys.stderr)
        sys.exit(1)
    if not args.map_path.exists():
        print(f"Base map PNG not found: {args.map_path}", file=sys.stderr)
        sys.exit(1)

    # Load images as NumPy arrays (RGB)
    overlay_arr = np.asarray(Image.open(args.overlay).convert("RGB"))
    base_arr = np.asarray(Image.open(args.map_path).convert("RGB"))

    if overlay_arr.shape != base_arr.shape:
        print("❌ Overlay and base map dimensions differ – cannot verify.", file=sys.stderr)
        print(f"Overlay: {overlay_arr.shape}, Base: {base_arr.shape}", file=sys.stderr)
        sys.exit(1)

    quest_coords = load_all_quest_coords()
    if args.limit:
        quest_coords = quest_coords[: args.limit]

    failures: List[Tuple[int, int]] = []

    for idx, (gy, gx) in enumerate(quest_coords, start=1):
        tile_x = gx + PAD
        tile_y = gy + PAD
        ok = centre_patch_diff(base_arr, overlay_arr, (tile_x, tile_y))
        if not ok:
            failures.append((gy, gx))
        # Progress output every 100 checks
        if idx % 100 == 0 or not ok:
            status = "FAIL" if not ok else "ok"
            print(f"[{idx}/{len(quest_coords)}] ({gy},{gx}) -> tile ({tile_y},{tile_x}) … {status}")

    if failures:
        print(f"\n❌ {len(failures)} coordinate(s) missing marker:\n{failures[:20]}{' …' if len(failures) > 20 else ''}")
        sys.exit(1)
    else:
        print(f"✅ All {len(quest_coords)} quest coordinates verified present in overlay.")
        sys.exit(0)


if __name__ == "__main__":
    main() 