import cv2
import numpy as np
import json

# ─── CONFIGURATION ─────────────────────────────────────────────────────────────
# Path to the full‐Kanto background PNG (16px per tile)
KANTO_BG_PATH = "environment/data/environment_data/full_kanto_map.png"
OUTPUT_PATH    = "kanto_marked.png"

MAP_PATH = __file__.rstrip('a_map_practice.py') + '/environment/data/environment_data/map_data.json'

MAP_DATA = json.load(open(MAP_PATH, 'r'))['regions']
MAP_DATA = {int(e['id']): e for e in MAP_DATA}

def local_to_global(r, c, map_n):
    try:
        map_x, map_y,= MAP_DATA[map_n]['coordinates']
        return r + map_y, c + map_x
    except KeyError:
        print(f'Map id {map_n} not found in map_data.json.')
        return r + 0, c + 0

map_id = 38
local_x_list = [7, 6, 5, 4, 4, 4, 4]
local_y_list = [5, 5, 5, 4, 3, 2, 1]
local_x_y_list_zip = list(zip(local_x_list, local_y_list))

print(f'local_to_global(7,7,38) == {local_to_global(7,7,38)}')

coords_to_mark = []
for local_x, local_y in local_x_y_list_zip:
    global_y, global_x = local_to_global(local_y, local_x, map_id)
    coords_to_mark.append((global_y, global_x))

# ─── LOAD BACKGROUND ────────────────────────────────────────────────────────────
bg = cv2.imread(KANTO_BG_PATH)
if bg is None:
    raise FileNotFoundError(f"Could not load '{KANTO_BG_PATH}'")

# ─── DRAW A RED 16×16 SQUARE AT EACH COORDINATE ─────────────────────────────────
for (glob_y, glob_x) in coords_to_mark:
    x0 = glob_x * 16
    y0 = glob_y * 16
    x1 = x0 + 16
    y1 = y0 + 16
    cv2.rectangle(bg, (x0, y0), (x1, y1), color=(0, 0, 255), thickness=-1)

# ─── SAVE RESULT ────────────────────────────────────────────────────────────────
cv2.imwrite(OUTPUT_PATH, bg)
print(f"Saved marked map to '{OUTPUT_PATH}'")
