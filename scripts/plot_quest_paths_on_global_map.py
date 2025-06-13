#!/usr/bin/env python3
"""plot_quest_paths_on_global_map.py

Overlay the first N quest-path node sets onto the full Kanto map, draw each
quest in a distinct colour, and render a legend explaining which colour
corresponds to which quest ID.

The coordinates come from JSON files like
`environment/environment_helpers/quest_paths/001/001_coords.json`. Each file
contains keys for quest steps, each mapping to a list of [y,x] padded global
coords.

Usage:
  python plot_quest_paths_on_global_map.py \
      --map-path grok_plays_pokemon/full_kanto_map.png \
      --quest-dir grok_plays_pokemon/environment/environment_helpers/quest_paths \
      --n 10 --output quests_overlay.png
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TILE_SIZE = 16
MAP_WIDTH_PX = 6976
MAP_HEIGHT_PX = 7104

try:
    from environment.data.recorder_data import global_map as gm  # type: ignore
except Exception as exc:  # pragma: no cover
    print("Cannot import global_map module; ensure you run inside project root.", file=sys.stderr)
    raise

PAD_ROW = gm.MAP_ROW_OFFSET  # 20
PAD_COL = gm.MAP_COL_OFFSET

# A set of visually distinct colours (RGB). Extend if needed.
COLOUR_PALETTE = [
    (255, 0, 0),       # red
    (0, 128, 255),     # blue
    (0, 200, 0),       # green
    (255, 128, 0),     # orange
    (255, 0, 255),     # magenta
    (128, 0, 255),     # violet
    (255, 255, 0),     # yellow
    (0, 255, 255),     # cyan
    (160, 82, 45),     # brown
    (128, 128, 128),   # grey
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_coords_for_quest(quest_dir: Path, quest_id: int) -> List[Tuple[int, int]]:
    """Return list of (gy, gx) padded global coords for quest_id (1-based)."""
    qid_str = f"{quest_id:03}"
    json_path = quest_dir / qid_str / f"{qid_str}_coords.json"

    if not json_path.exists():
        print(f"Coords file not found for quest {qid_str}: {json_path}", file=sys.stderr)
        return []

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    coords: List[Tuple[int, int]] = []
    for step_list in data.values():
        if not isinstance(step_list, list):
            continue
        for pair in step_list:
            if (
                isinstance(pair, list)
                and len(pair) == 2
                and all(isinstance(v, (int, float)) for v in pair)
            ):
                coords.append((int(pair[0]), int(pair[1])))  # (gy,gx)
    return coords


def padded_global_to_tile(g_y: int, g_x: int) -> Tuple[int, int]:
    """Convert padded global tile to PNG tile indices."""
    return g_x - PAD_COL, g_y - PAD_ROW


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Overlay quest paths on full map.")
    p.add_argument("--map-path", required=True, help="Path to full_kanto_map.png")
    p.add_argument("--quest-dir", required=True, help="Directory containing quest_paths/<id> dirs")
    p.add_argument("--n", type=int, default=10, help="Number of quests to plot starting from 1 (default 10)")
    default_out = Path(__file__).resolve().parent.parent / "web" / "static" / "images" / "kanto_map.png"
    p.add_argument("--output", default=str(default_out), help="Output PNG path (default replaces UI map)")
    p.add_argument("--no-show", action="store_true", help="Skip opening the resulting image viewer")

    # Player location options
    p.add_argument("--player-global-y", type=int, help="Player padded global Y tile index")
    p.add_argument("--player-global-x", type=int, help="Player padded global X tile index")
    p.add_argument("--player-sprite", type=str, help="Path to 16×16 PNG sprite for player (optional)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    base_img = Image.open(args.map_path).convert("RGB")
    if base_img.size != (MAP_WIDTH_PX, MAP_HEIGHT_PX):
        print("Unexpected map dimensions.", file=sys.stderr)
        sys.exit(1)

    draw = ImageDraw.Draw(base_img)

    quest_dir = Path(args.quest_dir)
    if not quest_dir.exists():
        # Try resolving relative to script directory's parent (project root)
        script_root = Path(__file__).resolve().parent.parent
        alt = (script_root / args.quest_dir).resolve()
        if alt.exists():
            quest_dir = alt
        else:
            print(f"Quest directory not found: {args.quest_dir}", file=sys.stderr)
            sys.exit(1)

    legend_entries: List[Tuple[str, Tuple[int, int, int]]] = []

    # Track extent of plotted tiles
    min_tx = MAP_WIDTH_PX // TILE_SIZE
    min_ty = MAP_HEIGHT_PX // TILE_SIZE
    max_tx = 0
    max_ty = 0

    for idx in range(1, args.n + 1):
        colour = COLOUR_PALETTE[idx % len(COLOUR_PALETTE)]
        padded_coords = load_coords_for_quest(quest_dir, idx)
        if not padded_coords:
            continue

        for gy, gx in padded_coords:
            tx, ty = padded_global_to_tile(gy, gx)
            if tx < 0 or ty < 0 or tx * TILE_SIZE >= MAP_WIDTH_PX or ty * TILE_SIZE >= MAP_HEIGHT_PX:
                continue  # out of bounds
            x0 = tx * TILE_SIZE
            y0 = ty * TILE_SIZE
            draw.rectangle([x0, y0, x0 + TILE_SIZE - 1, y0 + TILE_SIZE - 1], fill=colour)

            min_tx = min(min_tx, tx)
            min_ty = min(min_ty, ty)
            max_tx = max(max_tx, tx)
            max_ty = max(max_ty, ty)

        legend_entries.append((f"Quest {idx:03}", colour))

    # ------------------------------------------------------------------
    # Draw legend (key) – top-left corner on semi-transparent bg
    # ------------------------------------------------------------------
    legend_w = 200
    legend_h = 20 * len(legend_entries) + 10
    legend_bg = (0, 0, 0, 180)

    legend_img = Image.new("RGBA", (legend_w, legend_h), legend_bg)
    ldraw = ImageDraw.Draw(legend_img)
    font = ImageFont.load_default()

    for i, (label, colour) in enumerate(legend_entries):
        y = 5 + i * 20
        ldraw.rectangle([5, y, 15, y + 10], fill=colour + (255,))
        ldraw.text((22, y), label, fill=(255, 255, 255, 255), font=font)

    base_img = base_img.convert("RGBA")

    # ---------------------------------------------------------------
    # Player overlay
    # ---------------------------------------------------------------
    if args.player_global_y is not None and args.player_global_x is not None:
        p_tx, p_ty = padded_global_to_tile(args.player_global_y, args.player_global_x)

        if 0 <= p_tx < MAP_WIDTH_PX // TILE_SIZE and 0 <= p_ty < MAP_HEIGHT_PX // TILE_SIZE:
            px0 = p_tx * TILE_SIZE
            py0 = p_ty * TILE_SIZE

            if args.player_sprite and Path(args.player_sprite).exists():
                ps_img = Image.open(args.player_sprite).convert("RGBA").resize((TILE_SIZE, TILE_SIZE))
                base_img.paste(ps_img, (px0, py0), ps_img)
            else:
                # Red outline square fallback
                draw_fallback = ImageDraw.Draw(base_img)
                draw_fallback.rectangle([px0, py0, px0 + TILE_SIZE - 1, py0 + TILE_SIZE - 1], outline=(255, 0, 0, 255), width=2)

            # expand crop bounds
            min_tx = min(min_tx, p_tx)
            min_ty = min(min_ty, p_ty)
            max_tx = max(max_tx, p_tx)
            max_ty = max(max_ty, p_ty)
            legend_entries.append(("Player", (255, 0, 0)))

    # Recreate legend after possible new entry
    legend_h = 20 * len(legend_entries) + 10
    legend_img = Image.new("RGBA", (200, legend_h), (0, 0, 0, 180))
    ldraw = ImageDraw.Draw(legend_img)
    font = ImageFont.load_default()
    for i, (label, colour) in enumerate(legend_entries):
        y = 5 + i * 20
        ldraw.rectangle([5, y, 15, y + 10], fill=colour + (255,))
        ldraw.text((22, y), label, fill=(255, 255, 255, 255), font=font)

    # Crop around plotted area with padding
    if max_tx >= min_tx and max_ty >= min_ty:
        pad_tiles = 5
        x_left = max((min_tx - pad_tiles) * TILE_SIZE, 0)
        y_top = max((min_ty - pad_tiles) * TILE_SIZE, 0)
        x_right = min((max_tx + pad_tiles + 1) * TILE_SIZE, MAP_WIDTH_PX)
        y_bottom = min((max_ty + pad_tiles + 1) * TILE_SIZE, MAP_HEIGHT_PX)
        base_img = base_img.crop((x_left, y_top, x_right, y_bottom))

        # Move legend inside cropped area at fixed offset
        base_img.paste(legend_img, (10, 10), legend_img)
    else:
        # fallback whole map
        base_img.paste(legend_img, (10, 10), legend_img)

    base_img.save(args.output)
    print(f"Saved overlay to {args.output}")

    if not args.no_show:
        try:
            base_img.show()
        except Exception:
            pass


if __name__ == "__main__":
    main() 