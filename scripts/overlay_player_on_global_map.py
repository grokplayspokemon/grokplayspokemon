#!/usr/bin/env python3
"""
overlay_player_on_global_map.py

Standalone utility script to overlay the player's current location onto the
full Kanto map (6976 px × 7104 px) and **objectively verify** that the marker
was drawn at the intended global‐tile coordinate.

Usage (examples):
  python overlay_player_on_global_map.py \
      --map-path grok_plays_pokemon/full_kanto_map.png \
      --tile-x 10 --tile-y 5

  python overlay_player_on_global_map.py \
      --map-path /abs/path/to/full_kanto_map.png \
      --tile-x 123 --tile-y 87 \
      --output overlay.png --no-show

Arguments:
  --map-path   Path to the untouched `full_kanto_map.png` (required).
  --tile-x     Horizontal global tile index (0-based). Each tile is 16 px.
  --tile-y     Vertical   global tile index (0-based).
  --output     Where to save the overlaid image (default: map dir + "_with_player.png").
  --no-show    Skip opening the resulting image with the system viewer.
  --marker     Choose marker style: "square" (default) or "cross".

Verification logic
------------------
1.  Load the pristine map into a NumPy array (`base_arr`).
2.  Overlay the marker **in memory** and store as `overlay_arr`.
3.  Compute the boolean difference mask: `diff = overlay_arr != base_arr`.
4.  The union of changed pixels must lie *exactly* within the 16×16 pixel
    region corresponding to (tile_x, tile_y). If any pixel outside that
    region differs, verification fails.
5.  The script prints a pass/fail result and exits `1` on failure so it can be
    used in automated checks.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Tuple

import numpy as np
from PIL import Image, ImageDraw

TILE_SIZE = 16  # pixels
MAP_WIDTH_PX = 6976
MAP_HEIGHT_PX = 7104

# The game→global coordinate translator from the environment
try:
    # import inside try-except so the script can still run standalone for quick tests
    from environment.data.recorder_data import global_map as gm  # type: ignore
    _HAS_GLOBAL_MAP = True
except Exception:
    _HAS_GLOBAL_MAP = False


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def overlay_marker(img: Image.Image, tile_xy: Tuple[int, int], *, style: str = "square", color=(255, 0, 0)) -> Image.Image:
    """Return a copy of *img* with a marker drawn at the given global tile.

    Parameters
    ----------
    img : PIL.Image.Image
        Base map image. **Must** be MAP_WIDTH_PX × MAP_HEIGHT_PX.
    tile_xy : (int, int)
        (tile_x, tile_y) indices, 0-based, where (0,0) is top-left.
    style : str
        "square" (filled rectangle) or "cross" (two diagonals).
    color : tuple
        RGB color of the marker.
    """
    tile_x, tile_y = tile_xy
    x0 = tile_x * TILE_SIZE
    y0 = tile_y * TILE_SIZE
    x1 = x0 + TILE_SIZE - 1
    y1 = y0 + TILE_SIZE - 1

    if img.width != MAP_WIDTH_PX or img.height != MAP_HEIGHT_PX:
        raise ValueError(
            f"Unexpected map size {img.width}×{img.height}. "
            f"Expected {MAP_WIDTH_PX}×{MAP_HEIGHT_PX}.")

    out = img.copy().convert("RGB")
    draw = ImageDraw.Draw(out)

    if style == "square":
        draw.rectangle([x0, y0, x1, y1], outline=color, fill=color)
    elif style == "cross":
        # Draw two diagonals across the tile.
        draw.line([x0, y0, x1, y1], fill=color, width=2)
        draw.line([x0, y1, x1, y0], fill=color, width=2)
    else:
        raise ValueError("Unknown marker style: " + style)

    return out


def verify_marker(base: Image.Image, overlay: Image.Image, tile_xy: Tuple[int, int]) -> bool:
    """Verify that *overlay* differs from *base* **only** inside the target tile.

    Returns True on success, False otherwise.
    """
    base_arr = np.asarray(base.convert("RGB"))
    over_arr = np.asarray(overlay.convert("RGB"))

    if base_arr.shape != over_arr.shape:
        print("Shape mismatch between base and overlay images.", file=sys.stderr)
        return False

    diff_mask = np.any(base_arr != over_arr, axis=-1)  # (H, W) boolean
    changed_indices = np.argwhere(diff_mask)

    if changed_indices.size == 0:
        print("No difference detected between images.", file=sys.stderr)
        return False

    tile_x, tile_y = tile_xy
    x0 = tile_x * TILE_SIZE
    y0 = tile_y * TILE_SIZE
    x1 = x0 + TILE_SIZE
    y1 = y0 + TILE_SIZE

    # Build boolean mask of expected region
    in_region = (
        (changed_indices[:, 1] >= x0) & (changed_indices[:, 1] < x1) &
        (changed_indices[:, 0] >= y0) & (changed_indices[:, 0] < y1)
    )

    if not np.all(in_region):
        # Some changed pixels lie outside the expected tile
        offending = changed_indices[~in_region]
        print(f"Verification failed: {len(offending)} pixels outside expected tile were modified.", file=sys.stderr)
        return False

    return True


# ---------------------------------------------------------------------------
# Cropping utility
# ---------------------------------------------------------------------------

def crop_around_tile(img: Image.Image, tile_xy: Tuple[int, int], *, width_tiles: int = 106, height_tiles: int = 113) -> Image.Image:
    """Return a crop of *img* centered on *tile_xy* with the given tile span.

    The player tile will be as close to the center as parity allows. The crop
    area is clamped to stay within image bounds.
    """
    tile_x, tile_y = tile_xy

    crop_w_px = width_tiles * TILE_SIZE
    crop_h_px = height_tiles * TILE_SIZE

    # Compute desired top-left corner in pixel space
    center_x_px = tile_x * TILE_SIZE + TILE_SIZE // 2
    center_y_px = tile_y * TILE_SIZE + TILE_SIZE // 2

    left = center_x_px - crop_w_px // 2
    top = center_y_px - crop_h_px // 2

    # Clamp so crop lies within map bounds
    left = max(0, min(left, MAP_WIDTH_PX - crop_w_px))
    top = max(0, min(top, MAP_HEIGHT_PX - crop_h_px))

    right = left + crop_w_px
    bottom = top + crop_h_px

    return img.crop((left, top, right, bottom))


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overlay player marker on full Kanto map and verify placement.")
    parser.add_argument("--map-path", required=True, help="Path to full_kanto_map.png (pristine).")
    parser.add_argument("--tile-x", type=int, help="Global tile X index (0-based).")
    parser.add_argument("--tile-y", type=int, help="Global tile Y index (0-based).")
    parser.add_argument("--output", default=None, help="Output image path (default: <map dir>/full_kanto_map_with_player.png).")
    parser.add_argument("--no-show", action="store_true", help="Do not open the resulting image with the default viewer.")
    parser.add_argument("--marker", default="square", choices=["square", "cross"], help="Marker style to use.")

    # Optional direct game-coordinate inputs
    parser.add_argument("--map-id", type=int, help="Game map_id (required with --local-r/--local-c).")
    parser.add_argument("--local-r", type=int, help="Row in current map (player's y tile, 0-based).")
    parser.add_argument("--local-c", type=int, help="Col in current map (player's x tile, 0-based).")

    # Optional padded‐global coordinates (from global_map.py; y, x order)
    parser.add_argument("--global-y", type=int, help="Padded global Y tile index (0-based).")
    parser.add_argument("--global-x", type=int, help="Padded global X tile index (0-based).")

    # Raw string of coordinate pairs (e.g. "[338, 84] [345,78]")
    parser.add_argument("--coords", type=str, help="String containing one or more 'y,x' pairs; whitespace/newlines ignored.")
    parser.add_argument("--coord-index", type=int, default=0, help="If --coords has multiple pairs, which pair (0-based) to use.")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # ------------------------------------------------------------------
    # 0) Parse raw string of coordinates into --global-y/--global-x
    # ------------------------------------------------------------------
    coords_list_global: list[tuple[int, int]] = []  # store padded global pairs

    if args.coords is not None:
        import re

        matches = re.findall(r"(-?\d+)\s*,\s*(-?\d+)", args.coords)
        if not matches:
            print("Could not parse any 'y,x' pairs from --coords string.", file=sys.stderr)
            sys.exit(1)

        idx = args.coord_index
        if idx < 0 or idx >= len(matches):
            print(f"--coord-index {idx} out of range; only {len(matches)} pairs found.", file=sys.stderr)
            sys.exit(1)

        # Convert all valid matches to ints
        coords_list_global = [(int(y), int(x)) for y, x in matches]

        y_str, x_str = matches[idx]
        args.global_y = int(y_str)
        args.global_x = int(x_str)

    # ------------------------------------------------------------------
    # 1) Game local → global translation
    # ------------------------------------------------------------------
    if args.local_r is not None or args.local_c is not None or args.map_id is not None:
        if not _HAS_GLOBAL_MAP:
            print("global_map module not available; cannot translate game coords.", file=sys.stderr)
            sys.exit(1)
        if args.map_id is None or args.local_r is None or args.local_c is None:
            print("When using --map-id/--local-r/--local-c you must supply all three.", file=sys.stderr)
            sys.exit(1)

        gy_padded, gx_padded = gm.local_to_global(args.local_r, args.local_c, args.map_id)

        # Remove the padding (20 tiles) used inside gm so it aligns to the full_kanto_map image
        tile_y = gy_padded - gm.MAP_ROW_OFFSET  # type: ignore[attr-defined]
        tile_x = gx_padded - gm.MAP_COL_OFFSET  # type: ignore[attr-defined]

        if tile_x < 0 or tile_y < 0:
            print("Computed negative tile coordinates after removing padding; check inputs.", file=sys.stderr)
            sys.exit(1)

        args.tile_x = tile_x
        args.tile_y = tile_y

    # ------------------------------------------------------------------
    # 2) Padded global (gy,gx) → tile indices
    # ------------------------------------------------------------------
    if args.global_y is not None or args.global_x is not None:
        if args.global_y is None or args.global_x is None:
            print("Provide both --global-y and --global-x.", file=sys.stderr)
            sys.exit(1)

        if not _HAS_GLOBAL_MAP:
            print("global_map module not available; cannot translate padded global coords.", file=sys.stderr)
            sys.exit(1)

        tile_y_pad_removed = args.global_y - gm.MAP_ROW_OFFSET  # type: ignore[attr-defined]
        tile_x_pad_removed = args.global_x - gm.MAP_COL_OFFSET  # type: ignore[attr-defined]

        if tile_x_pad_removed < 0 or tile_y_pad_removed < 0:
            print("Global coordinates translate outside PNG bounds; check inputs.", file=sys.stderr)
            sys.exit(1)

        args.tile_x = tile_x_pad_removed
        args.tile_y = tile_y_pad_removed

        # Convert other coords (if provided) into tile indices list_tiles
        if coords_list_global:
            other_global_pairs = coords_list_global.copy()
            # Remove player pair at idx
            other_global_pairs.pop(args.coord_index)

            other_tiles: list[tuple[int, int]] = []
            for gy, gx in other_global_pairs:
                ty = gy - gm.MAP_ROW_OFFSET  # type: ignore[attr-defined]
                tx = gx - gm.MAP_COL_OFFSET  # type: ignore[attr-defined]
                if tx < 0 or ty < 0 or tx * TILE_SIZE >= MAP_WIDTH_PX or ty * TILE_SIZE >= MAP_HEIGHT_PX:
                    # skip out-of-bounds pairs
                    continue
                other_tiles.append((tx, ty))
        else:
            other_tiles = []
    else:
        other_tiles = []

    # Ensure one coordinate pathway succeeded
    if args.tile_x is None or args.tile_y is None:
        print("You must supply either (--tile-x & --tile-y) **or** --coords / --global-* **or** the trio --map-id, --local-r, --local-c.", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Load base map
    # ------------------------------------------------------------------
    try:
        base_img = Image.open(args.map_path)
    except FileNotFoundError:
        print(f"Map not found: {args.map_path}", file=sys.stderr)
        sys.exit(1)

    # Overlay marker using the resolved tile indices
    overlay_full = overlay_marker(base_img, (args.tile_x, args.tile_y), style=args.marker, color=(255,0,0))  # red player

    # Verification
    ok = verify_marker(base_img, overlay_full, (args.tile_x, args.tile_y))
    if not ok:
        print("❌ Verification failed — marker placement may be incorrect.")
        sys.exit(1)
    else:
        print("✅ Verification passed — marker placed correctly.")

    # Overlay additional markers (blue)
    for tx, ty in other_tiles:
        overlay_full = overlay_marker(overlay_full, (tx, ty), style=args.marker, color=(0, 0, 255))

    # Save & optionally open
    if args.output is None:
        dirname, fname = os.path.split(args.map_path)
        name, ext = os.path.splitext(fname)
        args.output = os.path.join(dirname, f"{name}_with_player{ext}")

    # Crop around player
    overlay_crop = crop_around_tile(overlay_full, (args.tile_x, args.tile_y))

    overlay_crop.save(args.output)
    print(f"Cropped overlay saved to {args.output}")

    if not args.no_show:
        try:
            overlay_crop.show()
        except Exception as exc:
            print(f"Could not open image viewer: {exc}")


if __name__ == "__main__":
    main() 