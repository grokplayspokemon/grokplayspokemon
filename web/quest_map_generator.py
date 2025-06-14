from pathlib import Path
from typing import List, Tuple

import json
from PIL import Image, ImageDraw, ImageFont

# Constants
TILE_SIZE = 16
MAP_WIDTH_PX = 6976
MAP_HEIGHT_PX = 7104

# Padding offsets from global_map
from environment.data.recorder_data.global_map import MAP_ROW_OFFSET as PAD_ROW, MAP_COL_OFFSET as PAD_COL

# Colour palette for quests
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

FRAME_NAMES = []  # unused


def load_coords_for_quest(quest_dir: Path, quest_id: int) -> List[Tuple[int, int]]:
    qid_str = f"{quest_id:03}"
    json_path = quest_dir / qid_str / f"{qid_str}_coords.json"
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text())
    coords = []
    for step_list in data.values():
        for pair in step_list:
            if isinstance(pair, list) and len(pair) == 2:
                coords.append((int(pair[0]), int(pair[1])))
    return coords


def padded_global_to_tile(g_y: int, g_x: int) -> Tuple[int, int]:
    return g_x - PAD_COL, g_y - PAD_ROW


def generate(output_path: Path, quest_dir: Path, n: int = 10, crop: bool = True) -> None:
    """Generate quest-overlay map at output_path.

    Args:
        output_path: Location to save resulting PNG
        quest_dir: Directory containing individual quest coordinate JSON files
        n: Number of quests to overlay (1..n)
        crop: Whether to crop the image around the coloured quest tiles. Set to
              False to always export the full original map dimensions – useful
              for web-UIs that rely on fixed sizing.
    """
    # Load the full Kanto base map from environment data
    base_img_path = Path(__file__).resolve().parent.parent / 'environment' / 'data' / 'environment_data' / 'full_kanto_map.png'
    base_img = Image.open(base_img_path).convert('RGBA')
    draw = ImageDraw.Draw(base_img)
    min_tx, min_ty = MAP_WIDTH_PX // TILE_SIZE, MAP_HEIGHT_PX // TILE_SIZE
    max_tx = max_ty = 0
    legend_entries = []

    for idx in range(1, n+1):
        colour = COLOUR_PALETTE[idx % len(COLOUR_PALETTE)]
        padded_coords = load_coords_for_quest(quest_dir, idx)
        for gy, gx in padded_coords:
            tx, ty = padded_global_to_tile(gy, gx)
            if 0 <= tx < MAP_WIDTH_PX//TILE_SIZE and 0 <= ty < MAP_HEIGHT_PX//TILE_SIZE:
                x0, y0 = tx*TILE_SIZE, ty*TILE_SIZE
                draw.rectangle([x0,y0,x0+TILE_SIZE-1,y0+TILE_SIZE-1], fill=colour)
                min_tx, min_ty = min(min_tx, tx), min(min_ty, ty)
                max_tx, max_ty = max(max_tx, tx), max(max_ty, ty)
        legend_entries.append((f"Quest {idx:03}", colour))

    # Optionally crop the image around quest overlays to reduce size.
    if crop and max_tx >= min_tx and max_ty >= min_ty:
        pad_tiles = 5
        x_left = max((min_tx - pad_tiles) * TILE_SIZE, 0)
        y_top = max((min_ty - pad_tiles) * TILE_SIZE, 0)
        x_right = min((max_tx + pad_tiles + 1) * TILE_SIZE, MAP_WIDTH_PX)
        y_bot = min((max_ty + pad_tiles + 1) * TILE_SIZE, MAP_HEIGHT_PX)
        cropped = base_img.crop((x_left, y_top, x_right, y_bot))
    else:
        cropped = base_img

    # Legend
    legend_w, legend_h = 200, 20*len(legend_entries)+10
    legend = Image.new('RGBA',(legend_w,legend_h),(0,0,0,180))
    ld = ImageDraw.Draw(legend)
    font = ImageFont.load_default()
    for i,(label,col) in enumerate(legend_entries):
        y=5+i*20
        ld.rectangle([5,y,15,y+10],fill=col+(255,))
        ld.text((22,y),label,fill=(255,255,255,255),font=font)
    out = cropped.copy()
    out.paste(legend,(10,10),legend)
    out.save(output_path) 