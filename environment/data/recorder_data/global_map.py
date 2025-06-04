# adapted from https://github.com/thatguy11325/pokemonred_puffer/blob/main/pokemonred_puffer/global_map.py

from environment.data.recorder_data.map_data import map_data

PAD = 20
GLOBAL_MAP_SHAPE = (444 + PAD * 2, 436 + PAD * 2)
MAP_ROW_OFFSET = PAD
MAP_COL_OFFSET = PAD

MAP_DATA = map_data["regions"]
MAP_DATA = {int(e["id"]): e for e in MAP_DATA}

# Handle KeyErrors
def local_to_global(r: int, c: int, map_n: int):
    try:
        map_coords = MAP_DATA[map_n]["coordinates"]
        map_x_offset = map_coords[0]
        map_y_offset = map_coords[1]
        gx = c + map_x_offset + MAP_COL_OFFSET
        gy = r + map_y_offset + MAP_ROW_OFFSET
        if 0 <= gy < GLOBAL_MAP_SHAPE[0] and 0 <= gx < GLOBAL_MAP_SHAPE[1]:
            return gy, gx
        print(f"coord out of bounds! global: ({gx}, {gy}) game: ({r}, {c}, {map_n})")
        return GLOBAL_MAP_SHAPE[0] // 2, GLOBAL_MAP_SHAPE[1] // 2
    except KeyError:
        print(f"Map id {map_n} not found in map_data.py.")
        return GLOBAL_MAP_SHAPE[0] // 2, GLOBAL_MAP_SHAPE[1] // 2

def global_to_local(gy_global: int, gx_global: int, map_id: int) -> tuple[int, int] | None:
    """Converts global coordinates to local coordinates for a given map_id.

    Args:
        gy_global: The global y-coordinate.
        gx_global: The global x-coordinate.
        map_id: The ID of the map for which to find local coordinates.

    Returns:
        A tuple (local_r, local_c) if conversion is successful, otherwise None.
    """
    try:
        map_x_origin, map_y_origin = MAP_DATA[map_id]["coordinates"]
        
        local_r = gy_global - map_y_origin - MAP_ROW_OFFSET
        local_c = gx_global - map_x_origin - MAP_COL_OFFSET
        
        return local_r, local_c
    except KeyError:
        print(f"Map id {map_id} not found in MAP_DATA for global_to_local conversion.")
        return None