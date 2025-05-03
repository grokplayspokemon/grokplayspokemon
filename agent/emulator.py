# import io
# import logging
# import pickle
# from collections import deque
# import heapq
# import os
# from pathlib import Path
# from datetime import datetime
# from typing import List, Dict, Set, Tuple

# from agent.memory_reader import PokemonRedReader, StatusCondition, Tileset
# from PIL import Image, ImageDraw, ImageFont
# from pyboy import PyBoy

# # Import WARP_DICT for door detection
# from game_data.constants import WARP_DICT, MAP_ID_REF, MAP_DICT
# from config import SAVE_STATE_DIR

# logger = logging.getLogger(__name__)

# # Door Tile IDs mapped from tileset_constants.asm and door_tile_ids.asm
# DOOR_TILE_IDS_BY_TILESET = {
#     Tileset.OVERWORLD: {0x1B, 0x58},
#     Tileset.FOREST: {0x3A},
#     Tileset.MART: {0x5E},
#     Tileset.POKECENTER: {0x5E}, # Assuming PokeCenter uses Mart tileset doors
#     Tileset.GYM: {0x54}, # Assuming Gym uses House tileset doors
#     Tileset.HOUSE: {0x54},
#     Tileset.REDS_HOUSE_1: {0x54}, # Assuming Red's house uses House tileset doors
#     Tileset.REDS_HOUSE_2: {0x54}, # Assuming Red's house uses House tileset doors
#     Tileset.FOREST_GATE: {0x3B},
#     Tileset.MUSEUM: {0x3B},
#     Tileset.GATE: {0x3B},
#     Tileset.SHIP: {0x1E},
#     Tileset.LOBBY: {0x1C, 0x38, 0x1A},
#     Tileset.MANSION: {0x1A, 0x1C, 0x53},
#     Tileset.LAB: {0x34},
#     Tileset.FACILITY: {0x43, 0x58, 0x1B},
#     Tileset.PLATEAU: {0x3B, 0x1B},
#     # Add other tilesets if needed, mapping them or defaulting
# }

# # Placeholder Stair Tile IDs - Adjust as needed!
# STAIR_TILE_IDS_BY_TILESET = {
#     Tileset.REDS_HOUSE_2: {0x55}, # GUESSING!
#     Tileset.REDS_HOUSE_1: {0x55}, # Placeholder for 1F - GUESSING!
#     # Add other tilesets/stairs IDs if known
# }

# class Emulator:
#     def __init__(self, rom_path, headless=True, sound=False):
#         self.rom_path = rom_path  # Store the ROM path
#         self.headless = headless  # Store headless state
#         self.sound = sound  # Store sound state
#         try:
#             # First try with cgb=True
#             if headless:
#                 self.pyboy = PyBoy(rom_path, window="null", cgb=True)
#             else:
#                 self.pyboy = PyBoy(rom_path, sound=sound, cgb=True)
#         except Exception as e:
#             logger.info("Failed to initialize in CGB mode, falling back to GB mode")
#             # If that fails, try with cgb=False
#             if headless:
#                 self.pyboy = PyBoy(rom_path, window="null", cgb=False)
#             else:
#                 self.pyboy = PyBoy(rom_path, sound=sound, cgb=False)

#         self.seen_npcs: Set[Tuple[int, int, int]] = set()  # (map_id, row, col)
#         self._npc_track_distance: int | None = None  # default: track all

#     def tick(self, frames):
#         """Advance the emulator by the specified number of frames."""
#         for _ in range(frames):
#             self.pyboy.tick()

#     def initialize(self):
#         """Initialize the emulator."""
#         # Run the emulator for a short time to make sure it's ready
#         self.pyboy.set_emulation_speed(0)
#         for _ in range(60):
#             self.tick(60)
#         self.pyboy.set_emulation_speed(1)

#     def get_screenshot(self):
#         """Get the current screenshot."""
#         return Image.fromarray(self.pyboy.screen.ndarray)

#     def get_screenshot_with_overlay(self, alpha=128):
#         """
#         Get the current screenshot with a tile overlay showing walkable/unwalkable areas.
        
#         Args:
#             alpha (int): Transparency value for the overlay (0-255)
            
#         Returns:
#             PIL.Image: Screenshot with tile overlay
#         """
#         from tile_visualizer import overlay_on_screenshot
#         screenshot = self.get_screenshot()
#         collision_map = self.get_collision_map()
#         return overlay_on_screenshot(screenshot, collision_map, alpha)

#     def load_state(self, state_filename):
#         """
#         Load a PyBoy save state file into the emulator.
        
#         Args:
#             state_filename: Path to the PyBoy .state file
#         """
#         try:
#             with open(state_filename, 'rb') as f:
#                 state_data = f.read()
#                 state_io = io.BytesIO(state_data)
#                 self.pyboy.load_state(state_io)
#         except Exception as e:
#             # If direct loading fails, try with pickle
#             try:
#                 with open(state_filename, 'rb') as f:
#                     state_data = pickle.load(f)
#                     if "pyboy_state" in state_data:
#                         pyboy_state_io = io.BytesIO(state_data["pyboy_state"])
#                         self.pyboy.load_state(pyboy_state_io)
#                     else:
#                         raise ValueError("Invalid save state format")
#             except Exception as e2:
#                 logger.error(f"Failed to load save state: {e2}")
#                 raise

#     def save_state(self, filename_prefix="auto_save"):
#         """Saves the current emulator state to a timestamped file."""
#         # Use configured save directory
#         saves_dir = Path(SAVE_STATE_DIR)
#         saves_dir.mkdir(exist_ok=True)
        
#         timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#         filename = f"{filename_prefix}_{timestamp}.state"
#         filepath = saves_dir / filename
        
#         try:
#             with open(filepath, "wb") as f:
#                 self.pyboy.save_state(f)
#             logger.info(f"Saved state to {filepath}")
#             return str(filepath)
#         except Exception as e:
#             logger.error(f"Failed to save state to {filepath}: {e}")
#             return None

#     def press_buttons(self, buttons, wait=True):
#         """Press a sequence of buttons on the Game Boy.
        
#         Args:
#             buttons (list[str]): List of buttons to press in sequence
#             wait (bool): Whether to wait after each button press
            
#         Returns:
#             str: Result of the button presses
#         """
#         results = []
        
#         for button in buttons:
#             if button not in ["a", "b", "start", "select", "up", "down", "left", "right"]:
#                 results.append(f"Invalid button: {button}")
#                 continue
                
#             self.pyboy.button_press(button)
#             self.tick(10)   # Press briefly
#             self.pyboy.button_release(button)
            
#             if wait:
#                 self.tick(120) # Wait longer after button release
#             else:
#                 self.tick(10)   # Brief pause between button presses
                
#             results.append(f"Pressed {button}")
        
#         return "\n".join(results)

#     def get_coordinates(self):
#         """
#         Returns the player's current coordinates from game memory.
#         Returns:
#             tuple[int, int]: (x, y) coordinates
#         """
#         reader = PokemonRedReader(self.pyboy.memory)
#         return reader.read_coordinates()

#     def get_active_dialog(self):
#         """
#         Returns the active dialog text from game memory.
#         Returns:
#             str: Dialog text
#         """
#         reader = PokemonRedReader(self.pyboy.memory)
#         dialog = reader.read_dialog()
#         if dialog:
#             return dialog
#         return None

#     def get_location(self):
#         """
#         Returns the player's current location name from game memory.
#         Returns:
#             str: Location name
#         """
#         reader = PokemonRedReader(self.pyboy.memory)
#         return reader.read_location()

#     def _get_direction(self, array):
#         """Determine the player's facing direction from the sprite pattern."""
#         # Look through the array for any 2x2 grid containing numbers 0-3
#         rows, cols = array.shape

#         for i in range(rows - 1):
#             for j in range(cols - 1):
#                 # Extract 2x2 grid
#                 grid = array[i : i + 2, j : j + 2].flatten()

#                 # Check for each direction pattern
#                 if list(grid) == [0, 1, 2, 3]:
#                     return "down"
#                 elif list(grid) == [4, 5, 6, 7]:
#                     return "up"
#                 elif list(grid) == [9, 8, 11, 10]:
#                     return "right"
#                 elif list(grid) == [8, 9, 10, 11]:
#                     return "left"

#         return "no direction found"

#     def _get_player_center(self, array):
#         """Locate the 2×2 sprite block that represents the player and return
#         the centre (row, col) within the 18×20 screen grid.  Falls back to
#         (9,8) if the pattern is not found.
#         """
#         rows, cols = array.shape

#         patterns = [
#             ([0, 1, 2, 3], "down"),   # facing down
#             ([4, 5, 6, 7], "up"),     # facing up
#             ([9, 8, 11, 10], "right"),
#             ([8, 9, 10, 11], "left"),
#         ]

#         for i in range(rows - 1):
#             for j in range(cols - 1):
#                 block = array[i : i + 2, j : j + 2].flatten().tolist()
#                 for pattern, _ in patterns:
#                     if block == pattern:
#                         return i + 1, j + 1  # centre of 2×2 block
#         # Fallback to assumed centre of screen
#         return 9, 8

#     def _downsample_array(self, arr):
#         """Downsample an 18x20 array to 9x10 by averaging 2x2 blocks."""
#         # Ensure input array is 18x20
#         if arr.shape != (18, 20):
#             raise ValueError("Input array must be 18x20")

#         # Reshape to group 2x2 blocks and take mean
#         return arr.reshape(9, 2, 10, 2).mean(axis=(1, 3))

#     def get_collision_map(self):
#         """
#         Creates a simple ASCII map showing player position, direction, terrain and sprites.
#         Takes into account tile pair collisions for more accurate walkability.
#         Returns:
#             str: A string representation of the ASCII map with legend
#         """
#         # Get the terrain and movement data
#         full_map = self.pyboy.game_area()
#         collision_map = self.pyboy.game_area_collision()
#         downsampled_terrain = self._downsample_array(collision_map)

#         # Get sprite locations
#         sprite_locations = self.get_sprites()

#         # Get character direction from the full map
#         direction = self._get_direction(full_map)
#         if direction == "no direction found":
#             return None

#         # Prepare collision lookup
#         reader = PokemonRedReader(self.pyboy.memory)
#         # Determine tileset for collision checks
#         tileset = reader.read_tileset()
#         full_tilemap = self.pyboy.game_wrapper._get_screen_background_tilemap()

#         # Determine if a warp is under the player
#         player_x, player_y = reader.read_coordinates()
#         map_id = reader.read_current_map_id()
#         map_key = MAP_ID_REF.get(map_id)
#         current_warps = WARP_DICT.get(map_key, [])
#         warp_under_player = any(warp['x'] == player_x and warp['y'] == player_y for warp in current_warps)

#         # Numeric codes: 0=walkable, 1=wall, 2=sprite, 3=player up, 4=player down, 5=player left, 6=player right
#         dir_codes = {"up": 3, "down": 4, "left": 5, "right": 6}
#         player_code = dir_codes.get(direction, 3)

#         # Build initial collision grid (walkable, sprites, player-coded), without warp overlay
#         grid = []
#         for i in range(9):
#             row = []
#             for j in range(10):
#                 # Base terrain and sprite logic
#                 if i == 4 and j == 4:
#                     row.append(str(player_code))
#                 elif (j, i) in sprite_locations:
#                     row.append('2')
#                 else:
#                     walkable = downsampled_terrain[i][j] != 0 and self._can_move_between_tiles(
#                         full_tilemap[9][8], full_tilemap[i*2+1][j*2], tileset
#                     )
#                     row.append('0' if walkable else '1')
#             grid.append(row)

#         # Overlay all warp entries and exit-adjacent cells
#         pr, pc = self._get_player_grid_position()
#         # Mark warps: 'W' if under player, 'D' otherwise
#         for warp in current_warps:
#             gr = warp['y'] // 2
#             gc = warp['x'] // 2
#             if 0 <= gr < len(grid) and 0 <= gc < len(grid[0]):
#                 grid[gr][gc] = 'W' if (gr == pr and gc == pc) else 'D'
#         # Highlight exit-adjacent cells below warp entries
#         for warp in current_warps:
#             gr = warp['y'] // 2
#             gc = warp['x'] // 2
#             er = gr + 1
#             if 0 <= er < len(grid) and 0 <= gc < len(grid[0]):
#                 grid[er][gc] = '0'

#         # Prepare output lines
#         lines = []
#         for row in grid:
#             lines.append(" ".join(row))

#         # Legend for grid codes (including warps/doors)
#         lines.extend([
#             "",
#             "Legend:",
#             "0 - walkable path",
#             "1 - wall / obstacle / unwalkable",
#             "2 - sprite (NPC)",
#             "D - door/warp",
#             "W - player standing on door/warp",
#             "3 - player (facing up)",
#             "4 - player (facing down)",
#             "5 - player (facing left)",
#             "6 - player (facing right)",
#         ])
#         # Append warp entries for current map
#         current_warps = WARP_DICT.get(map_key, [])
#         if current_warps:
#             lines.append("")
#             lines.append("Warps:")
#             for warp in current_warps:
#                 lines.append(f"- x: {warp['x']} y: {warp['y']} target: {warp['target_map_name']}")
#         return "\n".join(lines)

#     def get_valid_moves(self):
#         """Return list of valid cardinal directions for the player this frame.

#         Uses the full 18×20 collision grid so single-tile warps/doors are not
#         lost in down-sampling. Additionally, certain tile IDs are treated as
#         walkable even if the collision byte is 0 (warp/door tiles in Pokémon Red).
#         """

#         # 18×20 collision grid (0=blocked, non-zero=walkable)
#         collision = self.pyboy.game_area_collision()
#         # Background tilemap for warp tile detection
#         full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()

#         # Known warp/door tile IDs
#         WARP_TILE_IDS = {
#             0x0A, 0x0B,  # stair warps
#             0x4E, 0x4F,  # interior doors
#             0x50, 0x51, 0x52, 0x53,  # exterior single-door
#             0x5E, 0x5F, 0x6E, 0x6F,
#             0x70, 0x71, 0x72, 0x73,
#         }

#         def is_walkable(r: int, c: int) -> bool:
#             if not (0 <= r < 18 and 0 <= c < 20):
#                 return False
#             if collision[r][c] != 0:
#                 return True
#             return full_map[r][c] in WARP_TILE_IDS

#         # Locate player in the raw grid
#         pr, pc = self._get_player_center(full_map)
#         directions = {
#             "up":    (pr - 1, pc),
#             "down":  (pr + 1, pc),
#             "left":  (pr, pc - 1),
#             "right": (pr, pc + 1),
#         }

#         valid = [d for d, (r, c) in directions.items() if is_walkable(r, c)]
#         # Always include 'down' when standing on a warp entry
#         reader2 = PokemonRedReader(self.pyboy.memory)
#         px, py = reader2.read_coordinates()
#         map_key2 = MAP_ID_REF.get(reader2.read_current_map_id())
#         if any(warp['x'] == px and warp['y'] == py for warp in WARP_DICT.get(map_key2, [])):
#             if 'down' not in valid:
#                 valid.append('down')
#         return valid

#     def _get_player_grid_position(self) -> Tuple[int, int]:
#         """Return the player's (row, col) in the 9×10 down-sampled grid."""
#         # Determine raw sprite block centre and convert to 9×10 grid
#         full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
#         raw_r, raw_c = self._get_player_center(full_map)
#         # Each downsample cell covers 2×2 raw cells
#         return raw_r // 2, raw_c // 2

#     # ---------------------------------------------------------------------
#     # NPC helpers
#     # ---------------------------------------------------------------------
#     def get_npcs_in_range(self, max_distance: int | None = None) -> List[Dict]:
#         """Return NPCs within *max_distance* Manhattan steps of the player.

#         Args:
#             max_distance: Optional maximum Manhattan distance.  If *None*,
#                           every on‑screen sprite is returned.
#         Returns:
#             List of dicts: {"grid_row", "grid_col", "distance"}
#         """
#         sprites = self.get_sprites()  # (col, row) pairs from 9×10 grid
#         pr, pc = self._get_player_grid_position()
#         npcs: List[Dict] = []
#         for col, row in sprites:
#             dist = abs(row - pr) + abs(col - pc)
#             if max_distance is None or dist <= max_distance:
#                 npcs.append({
#                     "grid_row": row,
#                     "grid_col": col,
#                     "distance": dist,
#                 })
#         return npcs

#     def update_seen_npcs(self, max_distance: int | None = None) -> int:
#         """Add newly observed NPCs to `self.seen_npcs` and return its size."""
#         current_map_id: int = self.pyboy.memory[0xD35E]  # wCurMapID
#         for npc in self.get_npcs_in_range(max_distance):
#             self.seen_npcs.add((current_map_id, npc["grid_row"], npc["grid_col"]))
#         return len(self.seen_npcs)

#     def get_seen_npcs(self) -> Set[Tuple[int, int, int]]:
#         """Return an immutable view of all NPCs recorded so far."""
#         return frozenset(self.seen_npcs)

#     def enable_auto_npc_tracking(self, max_distance: int | None = None):
#         """Call once to automatically track NPCs every frame via `tick()`."""
#         self._npc_track_distance = max_distance

#     # Hook the existing tick() so auto‑tracking runs transparently
#     _original_tick = tick  # preserve reference to original method

#     def tick(self, frames):  # type: ignore[override]
#         for _ in range(frames):
#             self._original_tick(1)
#             if self._npc_track_distance is not None:
#                 self.update_seen_npcs(self._npc_track_distance)


#     def _can_move_between_tiles(self, tile1: int, tile2: int, tileset: str) -> bool:
#         """
#         Check if movement between two tiles is allowed based on tile pair collision data.

#         Args:
#             tile1: The tile being moved from
#             tile2: The tile being moved to
#             tileset: The current tileset name

#         Returns:
#             bool: True if movement is allowed, False if blocked
#         """
#         # Tile pair collision data
#         TILE_PAIR_COLLISIONS_LAND = [
#             ("CAVERN", 288, 261),
#             ("CAVERN", 321, 261),
#             ("FOREST", 304, 302),
#             ("CAVERN", 298, 261),
#             ("CAVERN", 261, 289),
#             ("FOREST", 338, 302),
#             ("FOREST", 341, 302),
#             ("FOREST", 342, 302),
#             ("FOREST", 288, 302),
#             ("FOREST", 350, 302),
#             ("FOREST", 351, 302),
#         ]

#         TILE_PAIR_COLLISIONS_WATER = [
#             ("FOREST", 276, 302),
#             ("FOREST", 328, 302),
#             ("CAVERN", 276, 261),
#         ]

#         # Check both land and water collisions
#         for ts, t1, t2 in TILE_PAIR_COLLISIONS_LAND + TILE_PAIR_COLLISIONS_WATER:
#             if ts == tileset:
#                 # Check both directions since collisions are bidirectional
#                 if (tile1 == t1 and tile2 == t2) or (tile1 == t2 and tile2 == t1):
#                     return False

#         return True

#     def get_sprites(self, debug=False):
#         """
#         Get the location of all of the sprites on the screen.
#         returns set of coordinates that are (column, row)
#         """
#         # Group sprites by their exact Y coordinate
#         sprites_by_y = {}

#         for i in range(40):
#             sp = self.pyboy.get_sprite(i)
#             if sp.on_screen:
#                 x = int(sp.x / 160 * 10)
#                 y = int(sp.y / 144 * 9)
#                 orig_y = sp.y

#                 if orig_y not in sprites_by_y:
#                     sprites_by_y[orig_y] = []
#                 sprites_by_y[orig_y].append((x, y, i))

#         # Sort Y coordinates
#         y_positions = sorted(sprites_by_y.keys())
#         bottom_sprite_tiles = set()

#         if debug:
#             print("\nSprites grouped by original Y:")
#             for orig_y in y_positions:
#                 sprites = sprites_by_y[orig_y]
#                 print(f"Y={orig_y}:")
#                 for x, grid_y, i in sprites:
#                     print(f"  Sprite {i}: x={x}, grid_y={grid_y}")

#         SPRITE_HEIGHT = 8

#         # First, group sprites by X coordinate for each Y level
#         for i in range(len(y_positions) - 1):
#             y1 = y_positions[i]
#             y2 = y_positions[i + 1]

#             if y2 - y1 == SPRITE_HEIGHT:
#                 # Group sprites by X coordinate at each Y level
#                 sprites_at_y1 = {s[0]: s for s in sprites_by_y[y1]}  # x -> sprite info
#                 sprites_at_y2 = {s[0]: s for s in sprites_by_y[y2]}

#                 # Only match sprites that share the same X coordinate
#                 for x in sprites_at_y2:
#                     if x in sprites_at_y1:  # If there's a matching top sprite at this X
#                         bottom_sprite = sprites_at_y2[x]
#                         bottom_sprite_tiles.add((x, bottom_sprite[1]))
#                         if debug:
#                             print(f"\nMatched sprites at x={x}, Y1={y1}, Y2={y2}")

#         # Filter out the player's own sprite (centered) to avoid treating it as an NPC
#         pr, pc = self._get_player_grid_position()
#         bottom_sprite_tiles.discard((pc, pr))
#         return bottom_sprite_tiles

#     def find_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
#         """
#         Finds the most efficient path from the player's current position (4,4) to the target position.
#         If the target is unreachable, finds path to nearest accessible spot.
#         Allows ending on a wall tile if that's the target.
#         Takes into account terrain, sprite collisions, and tile pair collisions.

#         Args:
#             target_row: Row index in the 9x10 downsampled map (0-8)
#             target_col: Column index in the 9x10 downsampled map (0-9)

#         Returns:
#             tuple[str, list[str]]: Status message and sequence of movements
#         """
#         # Get collision map, terrain, and sprites
#         collision_map = self.pyboy.game_wrapper.game_area_collision()
#         terrain = self._downsample_array(collision_map)
#         sprite_locations = self.get_sprites()

#         # Get full map for tile values and current tileset
#         full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
#         reader = PokemonRedReader(self.pyboy.memory)
#         tileset = reader.read_tileset()

#         # Start at player position (always 4,4 in the 9x10 grid)
#         start = (4, 4)
#         end = (target_row, target_col)

#         # Validate target position
#         if not (0 <= target_row < 9 and 0 <= target_col < 10):
#             return "Invalid target coordinates", []

#         # A* algorithm
#         def heuristic(a, b):
#             return abs(a[0] - b[0]) + abs(a[1] - b[1])

#         open_set = []
#         heapq.heappush(open_set, (0, start))
#         came_from = {}
#         g_score = {start: 0}
#         f_score = {start: heuristic(start, end)}

#         # Track closest reachable point
#         closest_point = start
#         min_distance = heuristic(start, end)

#         def reconstruct_path(current):
#             path = []
#             while current in came_from:
#                 prev = came_from[current]
#                 if prev[0] < current[0]:
#                     path.append("down")
#                 elif prev[0] > current[0]:
#                     path.append("up")
#                 elif prev[1] < current[1]:
#                     path.append("right")
#                 else:
#                     path.append("left")
#                 current = prev
#             path.reverse()
#             return path

#         while open_set:
#             _, current = heapq.heappop(open_set)

#             # Check if we've reached target
#             if current == end:
#                 path = reconstruct_path(current)
#                 is_wall = terrain[end[0]][end[1]] == 0
#                 if is_wall:
#                     return (
#                         f"Partial Success: Your target location is a wall. In case this is intentional, attempting to navigate there.",
#                         path,
#                     )
#                 else:
#                     return (
#                         f"Success: Found path to target at ({target_row}, {target_col}).",
#                         path,
#                     )

#             # Track closest point
#             current_distance = heuristic(current, end)
#             if current_distance < min_distance:
#                 closest_point = current
#                 min_distance = current_distance

#             # If we're next to target and target is a wall, we can end here
#             if (abs(current[0] - end[0]) + abs(current[1] - end[1])) == 1 and terrain[
#                 end[0]
#             ][end[1]] == 0:
#                 path = reconstruct_path(current)
#                 # Add final move onto wall
#                 if end[0] > current[0]:
#                     path.append("down")
#                 elif end[0] < current[0]:
#                     path.append("up")
#                 elif end[1] > current[1]:
#                     path.append("right")
#                 else:
#                     path.append("left")
#                 return (
#                     f"Success: Found path to position adjacent to wall at ({target_row}, {target_col}).",
#                     path,
#                 )

#             # Check all four directions
#             for dr, dc, direction in [
#                 (1, 0, "down"),
#                 (-1, 0, "up"),
#                 (0, 1, "right"),
#                 (0, -1, "left"),
#             ]:
#                 neighbor = (current[0] + dr, current[1] + dc)

#                 # Check bounds
#                 if not (0 <= neighbor[0] < 9 and 0 <= neighbor[1] < 10):
#                     continue
#                 # Skip walls unless it's the final destination
#                 if terrain[neighbor[0]][neighbor[1]] == 0 and neighbor != end:
#                     continue
#                 # Skip sprites unless it's the final destination
#                 if (neighbor[1], neighbor[0]) in sprite_locations and neighbor != end:
#                     continue

#                 # Check tile pair collisions
#                 # Get bottom-left tile of each 2x2 block
#                 current_tile = full_map[current[0] * 2 + 1][
#                     current[1] * 2
#                 ]  # Bottom-left tile of current block
#                 neighbor_tile = full_map[neighbor[0] * 2 + 1][
#                     neighbor[1] * 2
#                 ]  # Bottom-left tile of neighbor block
#                 if not self._can_move_between_tiles(
#                     current_tile, neighbor_tile, tileset
#                 ):
#                     continue

#                 tentative_g_score = g_score[current] + 1
#                 if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
#                     came_from[neighbor] = current
#                     g_score[neighbor] = tentative_g_score
#                     f_score[neighbor] = tentative_g_score + heuristic(neighbor, end)
#                     heapq.heappush(open_set, (f_score[neighbor], neighbor))

#         # If target unreachable, return path to closest point
#         if closest_point != start:
#             path = reconstruct_path(closest_point)
#             return (
#                 f"Partial Success: Could not reach the exact target, but found a path to the closest reachable point.",
#                 path,
#             )

#         return (
#             "Failure: No path is visible to the chosen location. You may need to explore a totally different path to get where you're trying to go.",
#             [],
#         )

#     def get_state_from_memory(self) -> str:
#         """
#         Reads the game state from memory and returns a string representation of it.
#         """
#         reader = PokemonRedReader(self.pyboy.memory)
#         memory_str = ""

#         name = reader.read_player_name()
#         if name == "NINTEN":
#             name = "Not yet set"
#         rival_name = reader.read_rival_name()
#         if rival_name == "SONY":
#             rival_name = "Not yet set"

#         # Get valid moves
#         valid_moves = self.get_valid_moves()
#         valid_moves_str = ", ".join(valid_moves) if valid_moves else "None"

#         memory_str += f"Player: {name}\n"
#         # memory_str += f"Rival: {rival_name}\n"
#         # memory_str += f"Money: ${reader.read_money()}\n"
#         memory_str += f"Location: {reader.read_location()}\n"
#         # Include current map dimensions to assist navigation
#         map_id = reader.read_current_map_id()
#         map_key = MAP_ID_REF.get(map_id)
#         dims = MAP_DICT.get(map_key, {})
#         memory_str += f"Map Dimensions: {dims.get('width', 'unknown')} x {dims.get('height', 'unknown')}\n"
#         memory_str += f"Coordinates: {reader.read_coordinates()}\n"
#         memory_str += f"Valid Moves: {valid_moves_str}\n"
#         # memory_str += f"Badges: {', '.join(reader.read_badges())}\n"

#         # Inventory
#         # memory_str += "Inventory:\n"
#         # for item, qty in reader.read_items():
#         #     memory_str += f"  {item} x{qty}\n"

#         # Dialog
#         dialog = reader.read_dialog()
#         if dialog:
#             memory_str += f"Dialog: {dialog}\n"
#         else:
#             memory_str += "Dialog: None\n"

#         # Party Pokemon
#         # memory_str += "\nPokemon Party:\n"
#         # for pokemon in reader.read_party_pokemon():
#         #     memory_str += f"\n{pokemon.nickname} ({pokemon.species_name}):\n"
#         #     memory_str += f"Level {pokemon.level} - HP: {pokemon.current_hp}/{pokemon.max_hp}\n"
#         #     memory_str += f"Types: {pokemon.type1.name}{', ' + pokemon.type2.name if pokemon.type2 else ''}\n"
#         #     for move, pp in zip(pokemon.moves, pokemon.move_pp, strict=True):
#         #         memory_str += f"- {move} (PP: {pp})\n"
#         #     if pokemon.status != StatusCondition.NONE:
#         #         memory_str += f"Status: {pokemon.status.get_status_name()}\n"

#         return memory_str

#     def stop(self):
#         self.pyboy.stop()

#     def is_warp_tile(self, grid_row: int, grid_col: int) -> bool:
#         """
#         Check if the specified downsampled grid cell corresponds to a warp tile.
#         """
#         # Use raw map ID and MAP_ID_REF to lookup warp entries exactly
#         map_id = self.pyboy.memory[0xD35E]  # wCurMapID
#         map_key = MAP_ID_REF.get(map_id)
#         current_map_warps = WARP_DICT.get(map_key, [])
#         for warp in current_map_warps:
#             if warp['y'] // 2 == grid_row and warp['x'] // 2 == grid_col:
#                 return True
#         return False
    

# emulator.py
import logging
logger = logging.getLogger(__name__)

import io
import pickle
from collections import deque
import heapq
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Set, Tuple

# Make sure PokemonRedReader and Tileset are correctly imported
from agent.memory_reader import PokemonRedReader, StatusCondition, Tileset
from PIL import Image, ImageDraw, ImageFont
from pyboy import PyBoy

# Import WARP_DICT for door detection
from game_data.constants import WARP_DICT, MAP_ID_REF, MAP_DICT
from config import SAVE_STATE_DIR

# Door Tile IDs mapped from tileset_constants.asm and door_tile_ids.asm
# Ensure this dictionary uses the Tileset enum members as keys
DOOR_TILE_IDS_BY_TILESET = {
    Tileset.OVERWORLD: {0x1B, 0x58},
    Tileset.FOREST: {0x3A},
    Tileset.MART: {0x5E},
    Tileset.POKECENTER: {0x5E},
    Tileset.GYM: {0x54},
    Tileset.HOUSE: {0x54},
    Tileset.REDS_HOUSE_1: {0x54},
    Tileset.REDS_HOUSE_2: {0x54},
    Tileset.FOREST_GATE: {0x3B},
    Tileset.MUSEUM: {0x3B},
    Tileset.GATE: {0x3B},
    Tileset.SHIP: {0x1E},
    Tileset.LOBBY: {0x1C, 0x38, 0x1A},
    Tileset.MANSION: {0x1A, 0x1C, 0x53},
    Tileset.LAB: {0x34},
    Tileset.FACILITY: {0x43, 0x58, 0x1B},
    Tileset.PLATEAU: {0x3B, 0x1B},
    # Add mappings for other Tileset members as needed
    # Example: Tileset.CAVERN: { ... }, Tileset.POWER_PLANT: { ... }, etc.
}

# Placeholder Stair Tile IDs - Adjust as needed!
# Ensure this dictionary uses the Tileset enum members as keys
STAIR_TILE_IDS_BY_TILESET = {
    Tileset.REDS_HOUSE_2: {0x55}, # Example
    Tileset.REDS_HOUSE_1: {0x55}, # Example
    # Add mappings for other Tileset members with stairs
    # Example: Tileset.MT_MOON_1: {0xXX, 0xYY}, Tileset.ROCKET_HIDEOUT_B1F: {0xZZ}, etc.
}

class Emulator:
    def __init__(self, rom_path, headless=True, sound=False):
        self.rom_path = rom_path  # Store the ROM path
        self.headless = headless  # Store headless state
        self.sound = sound  # Store sound state
        try:
            # First try with cgb=True
            if headless:
                self.pyboy = PyBoy(rom_path, window="null", cgb=True)
            else:
                self.pyboy = PyBoy(rom_path, sound=sound, cgb=True)
        except Exception as e:
            logger.info("Failed to initialize in CGB mode, falling back to GB mode")
            # If that fails, try with cgb=False
            if headless:
                self.pyboy = PyBoy(rom_path, window="null", cgb=False)
            else:
                self.pyboy = PyBoy(rom_path, sound=sound, cgb=False)

        self.seen_npcs: Set[Tuple[int, int, int]] = set()  # (map_id, row, col) - Using 9x10 grid coords for now
        self._npc_track_distance: int | None = None  # default: track all

    def tick(self, frames):
        """Advance the emulator by the specified number of frames."""
        for _ in range(frames):
            self.pyboy.tick()
        # Auto NPC tracking (if enabled) - keep using original tick for this part
        if self._npc_track_distance is not None:
             self.update_seen_npcs(self._npc_track_distance) # Assumes update_seen_npcs uses get_npcs_in_range which uses get_sprites

    # Hook the original tick method for auto-tracking if needed
    # This seems complex; let's simplify and call update_seen_npcs inside the loop if needed
    # Simpler approach:
    # def tick(self, frames):
    #     for _ in range(frames):
    #         self.pyboy.tick()
    #         if self._npc_track_distance is not None:
    #             # Careful: calling this every tick might be slow
    #             self.update_seen_npcs(self._npc_track_distance)


    def initialize(self):
        """Initialize the emulator."""
        # Run the emulator for a short time to make sure it's ready
        self.pyboy.set_emulation_speed(0)
        # Use the modified tick method if auto-tracking is desired per-tick
        for _ in range(60):
             self.tick(60) # Or self.pyboy.tick() if tracking isn't needed here
        self.pyboy.set_emulation_speed(1)

    def get_screenshot(self):
        """Get the current screenshot."""
        return Image.fromarray(self.pyboy.screen.ndarray)

    def get_screenshot_with_overlay(self, alpha=128):
        """
        Get the current screenshot with a tile overlay showing walkable/unwalkable areas.

        Args:
            alpha (int): Transparency value for the overlay (0-255)

        Returns:
            PIL.Image: Screenshot with tile overlay
        """
        from tile_visualizer import overlay_on_screenshot
        screenshot = self.get_screenshot()
        # collision_map here refers to the ASCII representation from get_collision_map
        collision_map_str = self.get_collision_map()
        # Need to adapt overlay_on_screenshot or provide the actual collision grid
        # For now, assuming overlay_on_screenshot can handle the string or needs adjustment
        # Placeholder:
        logger.warning("get_screenshot_with_overlay needs review for collision data format")
        # Let's pass the actual collision grid if tile_visualizer supports it
        collision_grid_numeric = self.pyboy.game_area_collision() # 18x20 numpy array
        # return overlay_on_screenshot(screenshot, collision_grid_numeric, alpha) # If it accepts numpy array
        return overlay_on_screenshot(screenshot, collision_map_str, alpha) # If it accepts the string map

    def load_state(self, state_filename):
        """
        Load a PyBoy save state file into the emulator.

        Args:
            state_filename: Path to the PyBoy .state file
        """
        try:
            with open(state_filename, 'rb') as f:
                state_data = f.read()
                state_io = io.BytesIO(state_data)
                self.pyboy.load_state(state_io)
        except Exception:
            # If direct loading fails, try with pickle
            try:
                with open(state_filename, 'rb') as f:
                    state_data = pickle.load(f)
                    if "pyboy_state" in state_data:
                        pyboy_state_io = io.BytesIO(state_data["pyboy_state"])
                        self.pyboy.load_state(pyboy_state_io)
                    else:
                        raise ValueError("Invalid save state format")
            except Exception as e2:
                logger.error(f"Failed to load save state: {e2}")
                raise

    def save_state(self, filename_prefix="auto_save"):
        """Saves the current emulator state to a timestamped file."""
        saves_dir = Path(SAVE_STATE_DIR)
        saves_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_prefix}_{timestamp}.state"
        filepath = saves_dir / filename

        try:
            with open(filepath, "wb") as f:
                self.pyboy.save_state(f)
            logger.info(f"Saved state to {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to save state to {filepath}: {e}")
            return None

    def press_buttons(self, buttons: List[str], wait: bool = True) -> str:
        """
        Press a sequence of buttons on the Game Boy.

        Args:
            buttons: List of buttons to press in sequence
            wait: Whether to wait after each button press

        Returns:
            str: Result of the button presses
        """
        results = []
        for button in buttons:
            if button not in ["a", "b", "start", "select", "up", "down", "left", "right"]:
                results.append(f"Invalid button: {button}")
                continue

            self.pyboy.button_press(button)
            self.tick(10)
            self.pyboy.button_release(button)

            self.tick(120 if wait else 10)
            results.append(f"Pressed {button}")

        return "\n".join(results)

    def get_coordinates(self) -> Tuple[int, int]:
        """
        Returns the player's current coordinates from game memory.

        Returns:
            (x, y) tuple
        """
        reader = PokemonRedReader(self.pyboy.memory)
        return reader.read_coordinates()

    def get_active_dialog(self) -> str | None:
        """
        Returns the active dialog text from game memory.

        Returns:
            Dialog string or None if no dialog
        """
        reader = PokemonRedReader(self.pyboy.memory)
        dialog = reader.read_dialog()
        return dialog if dialog else None


    def get_location(self):
        """
        Returns the player's current location name from game memory.
        Returns:
            str: Location name
        """
        reader = PokemonRedReader(self.pyboy.memory)
        return reader.read_location()
    
    def _get_direction(self, array):
        """Determine the player's facing direction from the sprite pattern."""
        # Look through the array for any 2x2 grid containing numbers 0-3
        rows, cols = array.shape

        for i in range(rows - 1):
            for j in range(cols - 1):
                # Extract 2x2 grid
                grid = array[i : i + 2, j : j + 2].flatten()

                # Check for each direction pattern
                if list(grid) == [0, 1, 2, 3]:
                    return "down"
                elif list(grid) == [4, 5, 6, 7]:
                    return "up"
                elif list(grid) == [9, 8, 11, 10]:
                    return "right"
                elif list(grid) == [8, 9, 10, 11]:
                    return "left"

        return "no direction found"


    def _get_player_center(self, array):
        """Locate the 2×2 sprite block that represents the player and return
        the centre (row, col) within the 18×20 screen grid. Falls back to
        (9,8) if the pattern is not found.

        Returns:
            Tuple[int, int]: (row, col) of the bottom-right tile of the player sprite.
        """
        rows, cols = array.shape

        patterns = [
            ([0, 1, 2, 3], "down"),   # facing down
            ([4, 5, 6, 7], "up"),     # facing up
            ([9, 8, 11, 10], "right"),# Corrected right pattern?
            ([8, 9, 10, 11], "left"), # Corrected left pattern?
        ]

        for i in range(rows - 1):
            for j in range(cols - 1):
                block = array[i : i + 2, j : j + 2].flatten().tolist()
                for pattern, _ in patterns:
                    if block == pattern:
                        # (i, j) is the top-left corner.
                        # Return bottom-right corner coords: (i+1, j+1)
                        return i + 1, j + 1
        # Fallback to assumed centre of screen (bottom-right perspective)
        # Default screen center is roughly row 8/9, col 9/10.
        # If default player is 2x2 at center, bottom-right is approx (9, 10)
        # The original code returned (9, 8). Let's analyze.
        # If player is at screen center (8,8) top-left -> (9,9) bottom-right?
        # Let's stick to original fallback for consistency unless proven wrong.
        # logger.warning("Player sprite pattern not found, using fallback center (9, 8).")
        return 9, 8

    def _downsample_array(self, arr):
        """Downsample an 18x20 array to 9x10 by averaging 2x2 blocks."""
        # Ensure input array is 18x20
        if arr.shape != (18, 20):
            # Pad or crop if necessary? Or raise error.
            logger.error(f"Input array shape {arr.shape} is not 18x20 for downsampling.")
            # Attempt to pad/crop (simple approach, might be incorrect for some arrays)
            if arr.shape[0] < 18 or arr.shape[1] < 20:
                 padded_arr = np.zeros((18, 20), dtype=arr.dtype) # Requires numpy import: import numpy as np
                 h, w = arr.shape
                 padded_arr[:h, :w] = arr
                 arr = padded_arr
            else:
                 arr = arr[:18, :20] # Crop
            # raise ValueError("Input array must be 18x20") # Original strict check

        # Reshape to group 2x2 blocks and take mean
        try:
             # Requires numpy
             import numpy as np
             return arr.reshape(9, 2, 10, 2).mean(axis=(1, 3))
        except ImportError:
             logger.error("Numpy is required for _downsample_array.")
             # Fallback: return original array or None?
             return arr # Return original if numpy fails

    def get_collision_map(self):
        collision_18x20 = self.pyboy.game_area_collision()
        downsampled = self._downsample_array(collision_18x20)
        sprites_9x10 = self.get_sprites(grid_type='9x10')
        pr, pc = self._get_player_grid_position()

        reader = PokemonRedReader(self.pyboy.memory)
        player_world_x, player_world_y = reader.read_coordinates()
        map_id = reader.read_current_map_id()
        map_key = MAP_ID_REF.get(map_id)
        current_warps = WARP_DICT.get(map_key, [])

        full_map = self.pyboy.game_area()
        direction = self._get_direction(full_map)
        if direction == "no direction found":
            return None

        dir_codes = {"up": 3, "down": 4, "left": 5, "right": 6}
        player_dir = dir_codes.get(direction, 3)

        # Debugging direction and sprite info
        print(f"DEBUG: Player direction: {direction} ({player_dir})")
        print(f"DEBUG: Sprites: {sprites_9x10}")

        base = []
        for r in range(9):
            row = []
            for c in range(10):
                row.append("0" if downsampled[r][c] else "1")
            base.append(row)

        collision_center = (4, 4)
        offset_x = collision_center[0] - player_world_x
        offset_y = collision_center[1] - player_world_y

        warp_cells = set()
        for warp in current_warps:
            warp_collision_x = warp["x"] + offset_x
            warp_collision_y = warp["y"] + offset_y
            if 0 <= warp_collision_y < 9 and 0 <= warp_collision_x < 10:
                warp_cells.add((warp_collision_y, warp_collision_x))
                print(f"DEBUG: Warp at collision ({warp_collision_y},{warp_collision_x})")

            warp_below_y = warp_collision_y + 1
            if 0 <= warp_below_y < 9 and 0 <= warp_collision_x < 10:
                warp_cells.add((warp_below_y, warp_collision_x))
                print(f"DEBUG: Warp below at collision ({warp_below_y},{warp_collision_x})")

        for (r, c) in warp_cells:
            base[r][c] = "W"

        # Place sprites (taken directly from their code)
        for sprite_pos in sprites_9x10:
            sprite_c, sprite_r = sprite_pos
            if 0 <= sprite_r < 9 and 0 <= sprite_c < 10:
                if (sprite_r, sprite_c) not in warp_cells:
                    base[sprite_r][sprite_c] = "2"
                    print(f"DEBUG: Placed sprite at collision map ({sprite_r},{sprite_c})")

        # Player placement (direct from their code)
        base[pr][pc] = str(player_dir)
        print(f"DEBUG: Placed player at collision map ({pr},{pc}) direction {player_dir}")

        # Build the collision map with coordinates
        lines = []
        # Add column headers
        lines.append("   " + " ".join(str(i) for i in range(10)))
        # Add rows with row numbers
        for r in range(9):
            row_str = " ".join(base[r])
            lines.append(f"{r}  {row_str}")

        lines += [
            "",
            "Legend:",
            "W  – warp (entry + exit square)",
            "0  – walkable",
            "1  – unwalkable", 
            "2  – sprite",
            "3  – player facing up",
            "4  – player facing down",
            "5  – player facing left", 
            "6  – player facing right"
        ]

        if current_warps:
            lines += ["", "Warps:"]
            for w in current_warps:
                tgt = w.get("target_map_name", w.get("target_map"))
                lines.append(f"- x: {w['x']} y: {w['y']} target: {tgt}")

        return "\n".join(lines)


    # <<< MODIFIED get_valid_moves >>>
    def get_valid_moves(self):
        """Return list of valid cardinal directions for the player this frame.

        Uses the full 18×20 collision grid, checks for known door/stair tiles,
        tile pair collisions, sprite collisions (on 18x20 grid), and handles warp exit logic.
        """
        reader = PokemonRedReader(self.pyboy.memory)
        px, py = reader.read_coordinates()
        map_id = reader.read_current_map_id()
        map_key = MAP_ID_REF.get(map_id)
        dims = MAP_DICT.get(map_key, {})
        map_width = dims.get('width')
        map_height = dims.get('height')
        current_warps = WARP_DICT.get(map_key, [])
        # Use the new helper to get the Tileset enum member
        tileset_enum = reader.read_tileset_enum()

        collision_18x20 = self.pyboy.game_area_collision() # 18x20 grid (0=blocked, non-zero=walkable)
        full_map_tiles = self.pyboy.game_wrapper._get_screen_background_tilemap() # 18x20 grid of tile IDs
        pr, pc = self._get_player_center(full_map_tiles) # Player bottom-right row/col on 18x20 screen grid

        # Get sprite collision locations on the 18x20 screen grid
        sprite_collision_tiles = self.get_sprites(grid_type='18x20') # Request 18x20 collision coords

        # Get current tileset's door and stair IDs
        current_door_ids = DOOR_TILE_IDS_BY_TILESET.get(tileset_enum, set())
        current_stair_ids = STAIR_TILE_IDS_BY_TILESET.get(tileset_enum, set())
        walkable_override_ids = current_door_ids.union(current_stair_ids)

        # Player's current collision tile ID (approximated as bottom-left tile)
        # Player bottom-right is (pr, pc). Top-left is (pr-1, pc-1). Bottom-left is (pr, pc-1).
        player_bl_row, player_bl_col = pr, pc - 1
        try:
            # Check bounds before accessing full_map_tiles
            if 0 <= player_bl_row < 18 and 0 <= player_bl_col < 20:
                current_tile_id = full_map_tiles[player_bl_row][player_bl_col]
            else:
                 # Player position seems invalid, maybe mid-transition?
                 logger.warning(f"Player bottom-left ({player_bl_row}, {player_bl_col}) out of map bounds.")
                 current_tile_id = -1 # Indicate invalid current tile
        except IndexError:
            logger.warning(f"IndexError accessing player tile at ({player_bl_row}, {player_bl_col}).")
            current_tile_id = -1


        def is_tile_walkable(target_r: int, target_c: int) -> bool:
            # 1. Check Screen Bounds
            if not (0 <= target_r < 18 and 0 <= target_c < 20):
                # logger.debug(f"Move target ({target_r},{target_c}) out of screen bounds.")
                return False

            try:
                target_tile_id = full_map_tiles[target_r][target_c]

                # 2. Check Basic Collision Data (0 = blocked)
                is_collision_walkable = collision_18x20[target_r][target_c] != 0

                # 3. Check Tile ID Overrides (Doors, Stairs are walkable even if collision=0)
                is_override_walkable = target_tile_id in walkable_override_ids

                # 4. Check Sprite Collision (Using 18x20 screen grid)
                # Target (r,c) must not be one of the NPC collision tiles
                is_sprite_blocking = (target_r, target_c) in sprite_collision_tiles

                # 5. Check Tile Pair Collisions
                tileset_str = tileset_enum.name # Get string name for _can_move_between_tiles
                allows_move_pair = True
                if current_tile_id != -1: # Only check if player's current tile is valid
                    allows_move_pair = self._can_move_between_tiles(current_tile_id, target_tile_id, tileset_str)
                else:
                    # Cannot determine tile pair collision if current tile is unknown
                    allows_move_pair = False # Be conservative? Or assume true? Let's assume true.
                    allows_move_pair = True
                    # logger.debug(f"Skipping tile pair check due to invalid current tile.")


                # Determine final walkability:
                walkable = not is_sprite_blocking and \
                           allows_move_pair and \
                           (is_collision_walkable or is_override_walkable)

                # Debug Logging (optional, can be verbose)
                # logger.debug(
                #     f"Checking move to ({target_r},{target_c}): TileID={target_tile_id}, "
                #     f"CollisionOK={is_collision_walkable}, OverrideOK={is_override_walkable}, "
                #     f"SpriteBlock={is_sprite_blocking}, TilePairOK={allows_move_pair} => Walkable={walkable}"
                # )

                return walkable

            except IndexError:
                 # Target coords likely out of bounds of the map arrays
                 logger.warning(f"IndexError checking walkability for target ({target_r},{target_c}).")
                 return False


        valid = []
        # Define moves relative to player's *collision* tile (bottom-left: pr, pc-1)
        # Or should it be relative to center? Let's stick to center (pr, pc) being bottom-right
        # and check adjacent tiles relative to that.
        # Up: (pr-1, pc) | Down: (pr+1, pc) | Left: (pr, pc-1) | Right: (pr, pc+1)
        # These are potential target *bottom-right* coordinates if movement maintains player center.
        # Let's test movement based on where the *collision point* (bottom-left) would land.
        # Current collision point: (p_row, p_col) = (pr, pc-1)
        # Move Up: Target collision point (p_row-1, p_col) = (pr-1, pc-1)
        # Move Down: Target collision point (p_row+1, p_col) = (pr+1, pc-1)
        # Move Left: Target collision point (p_row, p_col-1) = (pr, pc-2)
        # Move Right: Target collision point (p_row, p_col+1) = (pr, pc) <-- This is the center tile!

        # Let's simplify and use the original approach: check tiles adjacent to the *center* tile (pr, pc).
        # This assumes the game checks the tile you are trying to *enter* with your center/leading edge.
        directions = {
            "up":    (pr - 1, pc), # Tile directly above player's bottom-right
            "down":  (pr + 1, pc), # Tile directly below player's bottom-right
            "left":  (pr, pc - 1), # Tile directly left of player's bottom-right (player's bottom-left)
            "right": (pr, pc + 1), # Tile directly right of player's bottom-right
        }

        for direction, (target_r, target_c) in directions.items():
            if is_tile_walkable(target_r, target_c):
                valid.append(direction)

        # --- Special Warp Exit Logic ---
        on_warp = any(warp['x'] == px and warp['y'] == py for warp in current_warps)

        if on_warp and map_width is not None and map_height is not None:
            # Player is on a warp tile at the edge of the map, allow moving "off" the map
            if py == map_height - 1 and 'down' not in valid:
                valid.append('down') # At bottom edge, allow exiting down
            elif py == 0 and 'up' not in valid:
                 valid.append('up') # At top edge, allow exiting up

            if px == map_width - 1 and 'right' not in valid:
                 valid.append('right') # At right edge, allow exiting right
            elif px == 0 and 'left' not in valid:
                 valid.append('left') # At left edge, allow exiting left

        # Remove duplicates and return
        return sorted(list(set(valid))) # Sort for consistent output order


    def _get_player_grid_position(self) -> Tuple[int, int]:
        """Return the player's (row, col) in the 9×10 down-sampled grid."""
        # Determine raw sprite block centre (bottom-right tile)
        full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
        raw_r, raw_c = self._get_player_center(full_map) # Gets bottom-right row/col
        # Convert bottom-right screen coords to 9x10 grid coords
        # Grid cell (r, c) covers screen rows (r*2, r*2+1) and screen cols (c*2, c*2+1)
        # Player's bottom-right tile (raw_r, raw_c) falls into grid cell:
        grid_r = raw_r // 2
        grid_c = raw_c // 2
        return grid_r, grid_c

    # ---------------------------------------------------------------------
    # NPC helpers
    # ---------------------------------------------------------------------
    def get_npcs_in_range(self, max_distance: int | None = None) -> List[Dict]:
        """Return NPCs within *max_distance* Manhattan steps of the player.
           Uses the 9x10 grid for coordinates and distance calculation.

        Args:
            max_distance: Optional maximum Manhattan distance on the 9x10 grid.
                          If *None*, every on‑screen sprite is returned.
        Returns:
            List of dicts: {"grid_row", "grid_col", "distance"} (in 9x10 grid coords)
        """
        # Get NPC locations on the 9x10 grid
        sprite_locations_9x10 = self.get_sprites(grid_type='9x10') # Request 9x10 grid coords
        # Player position on the 9x10 grid
        pr_9x10, pc_9x10 = self._get_player_grid_position()

        npcs: List[Dict] = []
        # sprite_locations_9x10 contains (col, row) tuples
        for col, row in sprite_locations_9x10:
            dist = abs(row - pr_9x10) + abs(col - pc_9x10)
            if max_distance is None or dist <= max_distance:
                npcs.append({
                    "grid_row": row,
                    "grid_col": col,
                    "distance": dist,
                })
        return npcs

    def update_seen_npcs(self, max_distance: int | None = None) -> int:
        """Add newly observed NPCs (using 9x10 grid coords) to `self.seen_npcs`."""
        current_map_id: int = self.pyboy.memory[0xD35E]  # wCurMapID
        # Use get_npcs_in_range which works with the 9x10 grid
        for npc in self.get_npcs_in_range(max_distance):
            # Store based on 9x10 grid coordinates
            self.seen_npcs.add((current_map_id, npc["grid_row"], npc["grid_col"]))
        return len(self.seen_npcs)

    def get_seen_npcs(self) -> Set[Tuple[int, int, int]]:
        """Return an immutable view of all NPCs recorded so far (9x10 grid coords)."""
        return frozenset(self.seen_npcs)

    def enable_auto_npc_tracking(self, max_distance: int | None = None):
        """Call once to automatically track NPCs every frame via `tick()`."""
        self._npc_track_distance = max_distance
        logger.info(f"Auto NPC tracking enabled (max_distance={max_distance}). Modify tick() method if per-tick updates are needed.")

    # _original_tick = tick - Remove complex hooking for now


    def _can_move_between_tiles(self, tile1: int, tile2: int, tileset_str: str) -> bool:
        """
        Check if movement between two tiles is allowed based on tile pair collision data.
        Uses string representation of the tileset name.

        Args:
            tile1: The tile ID being moved from
            tile2: The tile ID being moved to
            tileset_str: The current tileset name (string)

        Returns:
            bool: True if movement is allowed, False if blocked
        """
        # Tile pair collision data (Ensure tileset names match enum names or provided strings)
        # Example: If tileset_str is 'REDS_HOUSE_1', these checks work.
        TILE_PAIR_COLLISIONS_LAND = [
            ("CAVERN", 288, 261), ("CAVERN", 321, 261), ("FOREST", 304, 302),
            ("CAVERN", 298, 261), ("CAVERN", 261, 289), ("FOREST", 338, 302),
            ("FOREST", 341, 302), ("FOREST", 342, 302), ("FOREST", 288, 302),
            ("FOREST", 350, 302), ("FOREST", 351, 302),
        ]
        TILE_PAIR_COLLISIONS_WATER = [
            ("FOREST", 276, 302), ("FOREST", 328, 302), ("CAVERN", 276, 261),
        ]

        # Combine lists for checking
        all_collisions = TILE_PAIR_COLLISIONS_LAND + TILE_PAIR_COLLISIONS_WATER

        for ts_name, t1_block, t2_block in all_collisions:
            if ts_name == tileset_str:
                # Check both directions
                if (tile1 == t1_block and tile2 == t2_block) or \
                   (tile1 == t2_block and tile2 == t1_block):
                    # logger.debug(f"Tile pair collision blocked: {tile1} <-> {tile2} in {tileset_str}")
                    return False # Blocked

        return True # Movement allowed


    # # <<< MODIFIED get_sprites >>>
    # def get_sprites(self, grid_type='9x10', debug=False):
    #     """
    #     Get the collision coordinates of visible sprites (NPCs), excluding the player.

    #     Args:
    #         grid_type (str): '9x10' for downsampled grid coords (col, row),
    #                          '18x20' for screen grid coords (row, col) of the collision tile.
    #         debug (bool): If True, print debugging information.

    #     Returns:
    #         Set[Tuple[int, int]]: Set of coordinate pairs based on grid_type.
    #                               For '9x10': (column, row)
    #                               For '18x20': (row, column) of the bottom-left collision tile.
    #     """
    #     npc_coords = set() # Will store coords based on grid_type

    #     # --- Get Player's collision tile (18x20 grid) to exclude it ---
    #     full_map_for_player = self.pyboy.game_wrapper._get_screen_background_tilemap()
    #     pr, pc = self._get_player_center(full_map_for_player) # Player bottom-right row/col
    #     # Player's collision tile is bottom-left: (pr, pc-1)
    #     player_collision_row, player_collision_col = pr, pc - 1
    #     player_collision_coord_18x20 = (player_collision_row, player_collision_col)

    #     if debug:
    #          print(f"Player collision tile (to exclude): {player_collision_coord_18x20}")
    #     # --- End Player Exclusion ---

    #     # Read sprite positions directly from memory (wSpriteStateData1)
    #     for i in range(16): # Iterate through sprite slots (0-15 typically used for map sprites)
    #         # Check if sprite is active/on screen (wSpriteStateData1 + $E, bit 7 = active)
    #         # Or check wSpriteIsMoving (0xD05E - bit i set if moving)? Simpler to just check coords.
    #         # $C100: sprite 0, $C110: sprite 1, ..., $C1F0: sprite 15
    #         base_addr = 0xC100 + i * 16

    #         # Check if sprite Y coord indicates it's active (e.g., < $90)
    #         raw_y_pixel = self.pyboy.memory[base_addr + 0] # Screen Y position + 16
    #         if raw_y_pixel == 0 or raw_y_pixel >= 160: # Inactive or off-screen y? Check this threshold
    #              continue # Skip inactive/off-screen sprite

    #         # Get tile coordinates (Top-left tile of the 2x2 sprite)
    #         # y = (Memory[base + 5] - 4)
    #         # x = (Memory[base + 6] - 4)
    #         sprite_tl_row = self.pyboy.memory[base_addr + 5] - 4 # Top tile row
    #         sprite_tl_col = self.pyboy.memory[base_addr + 6] - 4 # Left tile col

    #         # Calculate bottom-left collision tile coordinates (row, col)
    #         sprite_bl_row = sprite_tl_row + 1
    #         sprite_bl_col = sprite_tl_col

    #         sprite_collision_coord_18x20 = (sprite_bl_row, sprite_bl_col)

    #         # Exclude if it's the player's collision tile
    #         if sprite_collision_coord_18x20 == player_collision_coord_18x20:
    #              if debug:
    #                   print(f"Sprite {i} at {sprite_collision_coord_18x20} matches player - excluding.")
    #              continue

    #         # Exclude if coordinates are invalid (e.g., negative after subtraction)
    #         if sprite_bl_row < 0 or sprite_bl_col < 0:
    #              if debug:
    #                   print(f"Sprite {i} has invalid calculated coords: {sprite_collision_coord_18x20} - excluding.")
    #              continue

    #         # Add coordinate based on requested grid_type
    #         if grid_type == '18x20':
    #             npc_coords.add(sprite_collision_coord_18x20) # Add (row, col) for 18x20 grid
    #             if debug:
    #                  print(f"NPC Sprite {i}: Collision Tile (18x20): {sprite_collision_coord_18x20}")
    #         elif grid_type == '9x10':
    #             # Convert 18x20 bottom-left (row, col) to 9x10 grid (col, row)
    #             grid_9x10_r = sprite_bl_row // 2
    #             grid_9x10_c = sprite_bl_col // 2
    #             npc_coords.add((grid_9x10_c, grid_9x10_r)) # Add (col, row) for 9x10 grid
    #             if debug:
    #                  print(f"NPC Sprite {i}: Grid Coord (9x10): ({grid_9x10_c}, {grid_9x10_r})")
    #         else:
    #              raise ValueError(f"Invalid grid_type requested in get_sprites: {grid_type}")


    #     if debug:
    #          print(f"Final NPC Coords ({grid_type}): {npc_coords}")

    #     return npc_coords

    def get_sprites(self, grid_type='9x10', debug=False):
        """
        Get the location of all sprites on the screen, mapped to a 9x10 grid.
        Returns a set of coordinates (column, row) representing sprite positions.
        """
        sprite_positions = set()
        on_screen_sprites = []

        # Detect all on-screen sprites
        for i in range(40):
            sp = self.pyboy.get_sprite(i)
            if sp.on_screen:
                # Map sprite screen coordinates (0-159, 0-143) to 9x10 grid
                # Screen is 160x144 pixels, grid is 10 cols x 9 rows, so each cell is ~16x16 pixels
                x = int(sp.x / 160 * 10)  # 0 to 9
                # For y, assume collision is at the base of the sprite
                # If 8x16 sprite, base is at sp.y + 8; adjust by sprite height
                y_base = sp.y + 8  # Default to 8x8 sprite height; adjust if needed
                y = int(y_base / 144 * 9)  # 0 to 8
                on_screen_sprites.append((i, sp.x, sp.y, x, y))
                if 0 <= x < 10 and 0 <= y < 9:
                    sprite_positions.add((x, y))

        # Debugging output
        if debug:
            print(f"DEBUG: On-screen sprites: {len(on_screen_sprites)}")
            for idx, sx, sy, gx, gy in on_screen_sprites:
                print(f"  Sprite {idx}: screen (x={sx}, y={sy}) -> grid (x={gx}, y={gy})")
            print(f"DEBUG: Mapped sprite positions: {sprite_positions}")

        return sprite_positions


    # find_path should ideally use the refined collision logic from get_valid_moves
    # This would involve replacing its internal checks with calls to is_tile_walkable
    # or replicating the logic carefully. Needs significant refactoring.
    def find_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        """
        Finds the most efficient path from the player's current position to the target.
        Uses the 9x10 downsampled grid for pathfinding nodes.
        NOTE: This function's internal collision checks may differ from get_valid_moves.
              Consider refactoring for consistency.

        Args:
            target_row: Row index in the 9x10 downsampled map (0-8)
            target_col: Column index in the 9x10 downsampled map (0-9)

        Returns:
            tuple[str, list[str]]: Status message and sequence of movements
        """
        logger.warning("find_path uses its own collision logic, potentially inconsistent with get_valid_moves.")

        # Get collision map (downsampled), terrain, and sprites (9x10 grid)
        collision_map_18x20 = self.pyboy.game_wrapper.game_area_collision()
        terrain_9x10 = self._downsample_array(collision_map_18x20) # 0 = blocked
        sprite_locations_9x10 = self.get_sprites(grid_type='9x10') # Set of (col, row)

        # Get full map for tile values and current tileset for pair collisions
        full_map_tiles = self.pyboy.game_wrapper._get_screen_background_tilemap()
        reader = PokemonRedReader(self.pyboy.memory)
        tileset_enum = reader.read_tileset_enum()
        tileset_str = tileset_enum.name # String name for _can_move_between_tiles

        # Player start position on 9x10 grid
        start_node = self._get_player_grid_position() # (row, col)
        end_node = (target_row, target_col)

        # Validate target position (on 9x10 grid)
        if not (0 <= target_row < 9 and 0 <= target_col < 10):
            return "Invalid target coordinates (must be 0-8 for row, 0-9 for col)", []

        # --- A* Algorithm Setup ---
        open_set = []
        heapq.heappush(open_set, (0, start_node)) # (f_score, node)
        came_from = {} # node -> previous_node
        g_score = {start_node: 0} # node -> cost from start
        f_score = {start_node: heuristic(start_node, end_node)} # node -> g_score + heuristic

        closest_point = start_node
        min_heuristic = heuristic(start_node, end_node)
        found_path = False

        while open_set:
            current_f, current_node = heapq.heappop(open_set)

            # Optimization: If we pop a node already processed with a lower f_score, skip
            if current_f > f_score.get(current_node, float('inf')):
                 continue

            # Check heuristic distance for closest point tracking
            current_h = heuristic(current_node, end_node)
            if current_h < min_heuristic:
                 min_heuristic = current_h
                 closest_point = current_node

            # Goal check
            if current_node == end_node:
                 found_path = True
                 break # Exit loop, path found

            # Explore neighbors (up, down, left, right on 9x10 grid)
            for dr, dc, move in [( -1, 0, "up"), ( 1, 0, "down"), ( 0,-1, "left"), ( 0, 1, "right")]:
                neighbor_node = (current_node[0] + dr, current_node[1] + dc)
                neighbor_r, neighbor_c = neighbor_node

                # 1. Check Grid Bounds (9x10)
                if not (0 <= neighbor_r < 9 and 0 <= neighbor_c < 10):
                    continue

                # --- Check Walkability (find_path's internal logic) ---
                # a. Basic Terrain Collision (downsampled)
                if terrain_9x10[neighbor_r][neighbor_c] == 0: # Blocked tile
                     # Allow moving onto a blocked tile ONLY if it's the final target
                     if neighbor_node != end_node:
                          continue

                # b. Sprite Collision (9x10 grid)
                # Check if the target *node* contains a sprite
                if (neighbor_c, neighbor_r) in sprite_locations_9x10:
                     # Allow moving onto a sprite tile ONLY if it's the final target
                     if neighbor_node != end_node:
                          continue

                # c. Tile Pair Collisions (Needs 18x20 context)
                # Get representative tile IDs for current and neighbor nodes (e.g., bottom-left)
                try:
                     # Current node's bottom-left tile ID (18x20 grid)
                     curr_bl_r, curr_bl_c = current_node[0]*2 + 1, current_node[1]*2
                     curr_tile_id = full_map_tiles[curr_bl_r][curr_bl_c]

                     # Neighbor node's bottom-left tile ID (18x20 grid)
                     neigh_bl_r, neigh_bl_c = neighbor_node[0]*2 + 1, neighbor_node[1]*2
                     neigh_tile_id = full_map_tiles[neigh_bl_r][neigh_bl_c]

                     if not self._can_move_between_tiles(curr_tile_id, neigh_tile_id, tileset_str):
                          # Blocked by tile pair collision
                          continue
                except IndexError:
                     # Tile coords out of bounds, cannot check pair collision
                     logger.warning(f"IndexError checking tile pair for {current_node} -> {neighbor_node}")
                     continue # Block move if we can't check

                # --- End Walkability Check ---


                # If walkable, process with A*
                tentative_g_score = g_score.get(current_node, float('inf')) + 1

                if tentative_g_score < g_score.get(neighbor_node, float('inf')):
                    # Found a better path to neighbor
                    came_from[neighbor_node] = current_node
                    g_score[neighbor_node] = tentative_g_score
                    f_score[neighbor_node] = tentative_g_score + heuristic(neighbor_node, end_node)
                    heapq.heappush(open_set, (f_score[neighbor_node], neighbor_node))

        # --- Path Reconstruction & Status ---
        if found_path:
            # Reconstruct path from end_node
            path = []
            temp = end_node
            while temp in came_from:
                prev = came_from[temp]
                if prev[0] < temp[0]: path.append("down")
                elif prev[0] > temp[0]: path.append("up")
                elif prev[1] < temp[1]: path.append("right")
                else: path.append("left")
                temp = prev
            path.reverse()

            # Check if target was a wall/sprite (potentially intended)
            is_target_wall = terrain_9x10[end_node[0]][end_node[1]] == 0
            is_target_sprite = (end_node[1], end_node[0]) in sprite_locations_9x10
            if is_target_wall or is_target_sprite:
                 block_type = "wall/obstacle" if is_target_wall else "sprite"
                 return (
                      f"Partial Success: Target ({target_row}, {target_col}) is a {block_type}. Path leads adjacent.",
                      path[:-1] if path else [] # Return path *excluding* final step onto block
                      # Or return full path if movement onto the block is desired?
                      # Let's return full path for now, tool can decide to shorten.
                      # f"Success: Path found to target {block_type} at ({target_row}, {target_col}).", path
                 )
            else:
                 return (f"Success: Found path to target at ({target_row}, {target_col}).", path)

        else:
            # Path not found to target, try path to closest reachable point
            if closest_point != start_node:
                 path = []
                 temp = closest_point
                 while temp in came_from:
                      prev = came_from[temp]
                      if prev[0] < temp[0]: path.append("down")
                      elif prev[0] > temp[0]: path.append("up")
                      elif prev[1] < temp[1]: path.append("right")
                      else: path.append("left")
                      temp = prev
                 path.reverse()
                 return (
                      f"Partial Success: Target unreachable. Path to closest point ({closest_point[0]}, {closest_point[1]}) found.",
                      path
                 )
            else:
                 # No path found at all
                 return (
                      "Failure: No path found from start.",
                      []
                 )

    def get_state_from_memory(self) -> str:
        """
        Reads the game state from memory and returns a string representation of it.
        Includes accurately calculated valid moves.
        """
        reader = PokemonRedReader(self.pyboy.memory)
        memory_str = ""

        name = reader.read_player_name()
        if name == "NINTEN":
            name = "Not yet set"
        # rival_name = reader.read_rival_name()
        # if rival_name == "SONY":
        #     rival_name = "Not yet set"

        # Get valid moves using the refined function
        valid_moves = self.get_valid_moves() # Calls the new logic
        valid_moves_str = ", ".join(valid_moves) if valid_moves else "None"

        memory_str += f"Player: {name}\n"
        # memory_str += f"Rival: {rival_name}\n"
        # memory_str += f"Money: ${reader.read_money()}\n"
        location = reader.read_location()
        memory_str += f"Location: {location}\n"

        # Include current map dimensions
        map_id = reader.read_current_map_id()
        map_key = MAP_ID_REF.get(map_id)
        dims = MAP_DICT.get(map_key, {})
        width = dims.get('width', 'unknown')
        height = dims.get('height', 'unknown')
        memory_str += f"Map Dimensions: {width} x {height}\n"
        coords = reader.read_coordinates()
        memory_str += f"Coordinates: {coords}\n"
        memory_str += f"Valid Moves: {valid_moves_str}\n" # Uses the accurate list
        # memory_str += f"Badges: {', '.join(reader.read_badges())}\n"

        # Inventory (optional)
        # memory_str += "Inventory:\n"
        # for item, qty in reader.read_items():
        #     memory_str += f"  {item} x{qty}\n"

        # Dialog
        dialog = reader.read_dialog()
        memory_str += f"Dialog: {dialog if dialog else 'None'}\n"

        return memory_str

    def stop(self):
        self.pyboy.stop()

    # is_warp_tile - Keep original or remove if not used elsewhere?
    # It was used in the old collision map logic. The new one calculates warps directly.
    # Keep for potential future use or remove if confirmed unused.
    def is_warp_tile(self, grid_row: int, grid_col: int) -> bool:
        """
        Check if the specified downsampled grid cell (9x10) corresponds to a warp tile.
        """
        map_id = self.pyboy.memory[0xD35E]  # wCurMapID
        map_key = MAP_ID_REF.get(map_id)
        current_map_warps = WARP_DICT.get(map_key, [])
        for warp in current_map_warps:
            # Convert warp's absolute coords to 9x10 grid coords
            warp_grid_row = warp['y'] // 2
            warp_grid_col = warp['x'] // 2
            if warp_grid_row == grid_row and warp_grid_col == grid_col:
                return True
        return False

# Helper function (outside class or make static) used by find_path
def heuristic(a, b):
    """Manhattan distance heuristic for A*."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
