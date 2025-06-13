#!/usr/bin/env python3
"""extract_player_sprites.py

Slice the 10-frame 16×16 sprite sheet
`pokemon_red_player_spritesheet.png` into individual PNGs with transparency.

The sheet layout (left→right, 1-pixel white separators):
 index 0   stepping_down
 index 1   standing_down
 index 2   stepping_down_2
 index 3   stepping_up
 index 4   standing_up
 index 5   stepping_up_2
 index 6   standing_left
 index 7   stepping_left
 index 8   standing_right
 index 9   stepping_right

Orange background (#ff7f00) is converted to full transparency.

Usage
-----
python extract_player_sprites.py \
    --sheet grok_plays_pokemon/environment/data/environment_data/pokemon_red_player_spritesheet.png \
    --out-dir grok_plays_pokemon/web/static/images/player_frames
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageColor

ORANGE = ImageColor.getrgb("#ff7f00")  # exact bg colour used in sheet
FRAME_W = 16
SEP = 1  # white separator between frames
FRAME_NAMES = [
    "stepping_down",
    "standing_down",
    "stepping_down_2",
    "stepping_up",
    "standing_up",
    "stepping_up_2",
    "standing_left",
    "stepping_left",
    "standing_right",
    "stepping_right",
]


def make_transparent(frame: Image.Image) -> Image.Image:
    """Return a copy of *frame* with ORANGE pixels turned transparent."""
    frame = frame.convert("RGBA")
    datas = frame.getdata()
    new_data = []
    for r, g, b, a in datas:
        if (r, g, b) == ORANGE:
            new_data.append((0, 0, 0, 0))
        else:
            new_data.append((r, g, b, a))
    frame.putdata(new_data)
    return frame


def slice_sheet(sheet_path: Path, out_dir: Path) -> None:
    sheet = Image.open(sheet_path).convert("RGBA")
    w, h = sheet.size

    # Compute expected frame count
    n_frames = (w + SEP) // (FRAME_W + SEP)
    if n_frames != 10:
        print(f"Warning: expected 10 frames but detected {n_frames} based on width.")

    out_dir.mkdir(parents=True, exist_ok=True)

    for idx in range(n_frames):
        x0 = idx * (FRAME_W + SEP)
        tile = sheet.crop((x0, 0, x0 + FRAME_W, FRAME_W))
        tile = make_transparent(tile)
        name = FRAME_NAMES[idx] if idx < len(FRAME_NAMES) else f"player_{idx:02}"
        tile.save(out_dir / f"{name}.png")
        print(f"Saved {name}.png")


def parse_args():
    p = argparse.ArgumentParser(description="Slice the player spritesheet into frames.")
    p.add_argument("--sheet", required=True, help="Path to pokemon_red_player_spritesheet.png")
    p.add_argument("--out-dir", required=True, help="Directory to write individual frame PNGs")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    slice_sheet(Path(args.sheet), Path(args.out_dir)) 