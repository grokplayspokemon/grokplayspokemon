#!/usr/bin/env python3
"""
verify_js_global_mapping.py

Standalone verification script that emulates the **browser-side**
`updatePlayerGlobal()` logic (after our recent fix) and checks that the
computed tile aligns with the authoritative test used by
`overlay_player_on_global_map.py`.

This does **NOT** modify the original overlay test – it is a separate,
self-contained check so we can safely run it in CI or locally.

Usage (will exit 0 on success, 1 on failure):

    python verify_js_global_mapping.py 349 110

If no arguments are given the default padded-global coordinate pair
(349, 110) is used.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

from PIL import Image

# Re-use the trusted overlay verification helpers
from scripts import overlay_player_on_global_map as op

# ---------------------------------------------------------------------------
# Constants (must mirror the JS/front-end values exactly)
# ---------------------------------------------------------------------------
TILE_SIZE: int = op.TILE_SIZE                     # 16 px
PAD: int = op.gm.MAP_COL_OFFSET                  # 20-tile padding (same for rows)
MAP_PATH: Path = (
    Path(__file__).resolve().parent.parent /
    "environment" / "data" / "environment_data" / "full_kanto_map.png"
)

# ---------------------------------------------------------------------------
# Helper to replicate JS coordinate → pixel conversion
# ---------------------------------------------------------------------------

def padded_global_to_tile(g_y: int, g_x: int) -> Tuple[int, int]:
    """Convert padded global tile (gy, gx) to *unpadded* tile indices (tx, ty)."""
    tile_x = g_x - PAD
    tile_y = g_y - PAD
    if tile_x < 0 or tile_y < 0:
        raise ValueError("Coordinate translates outside base map bounds.")
    return tile_x, tile_y


# ---------------------------------------------------------------------------
# Main verification routine
# ---------------------------------------------------------------------------

def main(gy: int, gx: int, output: Path | None = None) -> None:
    tile_x, tile_y = padded_global_to_tile(gy, gx)

    if not MAP_PATH.exists():
        print(f"Map image not found at {MAP_PATH}.", file=sys.stderr)
        sys.exit(1)

    base_img: Image.Image = Image.open(MAP_PATH)

    # Overlay a *green* square so we can visually inspect results if needed
    overlay_full = op.overlay_marker(base_img, (tile_x, tile_y), style="square", color=(0, 255, 0))

    # Produce a centred crop identical to overlay_player_on_global_map.py for easier viewing
    overlay_crop = op.crop_around_tile(overlay_full, (tile_x, tile_y))

    # Determine output location
    if output is None:
        out_name = f"js_mapping_test_y{gy}_x{gx}.png"
        output = MAP_PATH.parent / out_name
    overlay_crop.save(output)
    print(f"🖼️  Saved overlay preview to {output}")

    ok = op.verify_marker(base_img, overlay_full, (tile_x, tile_y))
    if ok:
        print("✅ JS global-coordinate mapping verified – marker placed correctly.")
        sys.exit(0)
    else:
        print("❌ Verification failed – JS global-coordinate mapping is incorrect.")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify JS global→pixel mapping and save overlay preview.")
    parser.add_argument("global_y", type=int, nargs="?", default=349,
                        help="Padded global Y coordinate (default: 349)")
    parser.add_argument("global_x", type=int, nargs="?", default=110,
                        help="Padded global X coordinate (default: 110)")
    parser.add_argument("--output", "-o", type=Path,
                        help="Optional output PNG path (defaults to js_mapping_test_y<gy>_x<gx>.png alongside map)")

    args = parser.parse_args()

    main(args.global_y, args.global_x, args.output) 