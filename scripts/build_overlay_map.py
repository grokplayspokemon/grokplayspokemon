#!/usr/bin/env python3
"""build_overlay_map.py

Generates a quest-overlay Kanto map **with a facing-correct player sprite**
for any arbitrary game position, using only the existing Python utilities
already present in the repo.  This is the proof-of-concept replacement for
the JS positioning logic – everything happens in Python.

It relies entirely on:
    • grok_plays_pokemon/ui/render_to_global.py   (full map rendering)
    • environment/data/environment_data/*.png     (base map & sprite sheet)
    • environment/environment_helpers/quest_paths (quest coordinates)

The produced PNG is written next to the script (or at a path you supply).

Usage:
    python build_overlay_map.py --map-id 0 --local-x 10 --local-y 5 --facing Down \
           --output my_overlay.png

You can also feed already-padded global coordinates – exactly the same pair
used in our earlier tests – via --global-x --global-y.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Tuple

from PIL import Image

# Render helper module (already contains everything we need)
from ui import render_to_global as rg

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def extract_sprite_frame(spritesheet: Image.Image, facing: str) -> Image.Image:
    """Return a 16×16 RGBA player sprite frame for the given *facing* direction."""
    index_map = {'Down': 1,  # standing_down (index 1)
                 'Up': 4,    # standing_up   (index 4)
                 'Left': 6,  # standing_left (index 6)
                 'Right': 8} # standing_right(index 8)
    idx = index_map.get(facing, 1)
    x0 = idx * 17  # spritesheet has 1-px separators
    frame = spritesheet.crop((x0, 0, x0 + 16, 16)).convert('RGBA')

    # Remove orange BG ➔ transparency (same logic as load_resources)
    datas = frame.getdata()
    out = []
    for r, g, b, a in datas:
        if (r, g, b) in ((255, 165, 0), (255, 184, 77)):
            out.append((255, 255, 255, 0))
        else:
            out.append((r, g, b, a))
    frame.putdata(out)
    return frame


def build_dummy_canvas() -> SimpleNamespace:
    """Create an object with the attributes render_to_global expects."""
    c = SimpleNamespace()
    rg.initialize_map_canvas(c)  # set attribute placeholders
    # Load resources (map image, quest coords, sprite sheet)
    rg.load_resources(c)
    # ensure map data globals are ready
    rg.load_map_data()
    rg.prepare_map_data_for_conversion()
    return c


def overlay_and_save(local_x: int, local_y: int, map_id: int, facing: str,
                     output: Path) -> Path:
    canvas = build_dummy_canvas()

    # Overwrite sprite frame with the exact facing frame so orientation is correct
    spritesheet_path = (
        Path(__file__).resolve().parent.parent /
        "environment" / "data" / "environment_data" / "pokemon_red_player_spritesheet.png"
    )
    sheet_img = Image.open(spritesheet_path)
    frame_img = extract_sprite_frame(sheet_img, facing)
    canvas.sprite_frames = [frame_img] * 10  # dummy list so index access works

    out_path_str = rg.save_full_map_with_overlays(canvas, local_x, local_y, map_id, facing)
    if not out_path_str:
        raise RuntimeError("Failed to generate overlay map")

    out_path = Path(out_path_str)
    if output != out_path:
        # keep original and copy to desired name
        try:
            import shutil
            shutil.copy(out_path, output)
        except Exception as e:
            print(f"Warning: could not copy to {output}: {e}")
    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Kanto overlay map with quests + player sprite.")

    p.add_argument("--map-id", type=int, help="Map ID (integer) of current map")
    p.add_argument("--local-x", type=int, help="Player local X (col) tile in map")
    p.add_argument("--local-y", type=int, help="Player local Y (row) tile in map")

    p.add_argument("--global-x", type=int, help="Padded global X tile index (alternative to local coords)")
    p.add_argument("--global-y", type=int, help="Padded global Y tile index (alternative to local coords)")

    p.add_argument("--facing", default="Down", choices=["Down", "Up", "Left", "Right"],
                   help="Facing direction for sprite (default Down)")
    p.add_argument("--output", type=Path, default=Path("overlay_map.png"),
                   help="Output PNG filename (default overlay_map.png)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if (args.global_x is not None) ^ (args.global_y is not None):
        print("Provide both --global-x and --global-y, or neither (to use local coords)", file=sys.stderr)
        sys.exit(1)

    if args.global_x is not None:
        # Convert padded global ➔ local using rg.global_to_local equivalent
        # We need map_id in this case as well, so enforce it.
        if args.map_id is None:
            print("--map-id required when using global coordinates", file=sys.stderr)
            sys.exit(1)
        # translate padded global back to local (for internal call)
        local_y = args.global_y - rg.PAD  # type: ignore[attr-defined]
        local_x = args.global_x - rg.PAD  # type: ignore[attr-defined]
    else:
        if None in (args.map_id, args.local_x, args.local_y):
            print("Provide --map-id, --local-x, --local-y (or use global coords)", file=sys.stderr)
            sys.exit(1)
        local_x = args.local_x
        local_y = args.local_y

    print(f"Generating overlay for map_id={args.map_id}, local=({local_y},{local_x}), facing={args.facing}")

    output_path = overlay_and_save(local_x, local_y, args.map_id, args.facing, args.output)
    print(f"✅ Overlay saved to {output_path}")


if __name__ == "__main__":
    main() 