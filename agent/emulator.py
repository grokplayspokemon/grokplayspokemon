# emulator.py
import logging
logger = logging.getLogger(__name__)

import io
import numpy as np
import pickle
from collections import deque
import heapq
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Set, Tuple, Optional # Added Optional
import json

# Make sure PokemonRedReader and Tileset are correctly imported
from agent.memory_reader import PokemonRedReader, StatusCondition, Tileset
from PIL import Image, ImageDraw, ImageFont
from pyboy import PyBoy

# Import WARP_DICT for door detection
from game_data.constants import WARP_DICT, MAP_ID_REF, MAP_DICT
from config import SAVE_STATE_DIR
from game_data.global_map import GLOBAL_MAP_SHAPE, local_to_global

# Event tracking constants
EVENT_FLAGS_START = 0xD747
EVENTS_FLAGS_LENGTH = 0x140  # = 320
MUSEUM_TICKET_ADDR = (0xD754, 7)  # Address and bit position

# List of event IDs to ignore for reward calculations
IGNORED_EVENT_IDS = [
    # Add specific event IDs that should be ignored for rewards
    # For example: museum ticket and other initial/unimportant events
]

# -- Sign posts / notice boards / PC Billboards, by tileset
SIGN_TILE_IDS_BY_TILESET = {
    Tileset.OVERWORLD:   {0x2F},  # "NOTICE" sign on routes / towns
    Tileset.FOREST:      {0x3C},
    Tileset.GYM:         {0x5F},
    Tileset.MART:        {0x21},
    Tileset.POKECENTER:  {0x21},
    Tileset.GATE:        {0x3C},
}


class Emulator:
    def __init__(self, rom_path, headless=True, sound=False):
        self.rom_path = rom_path  # Store the ROM path
        self.headless = headless  # Store headless state
        self.sound = sound  # Store sound state
        try:
            # First try with cgb=True
            if headless:
                self.pyboy = PyBoy(rom_path, window="null", cgb=True, symbols="pokered.sym")
            else:
                self.pyboy = PyBoy(rom_path, sound=sound, cgb=True, symbols="pokered.sym")
        except Exception as e:
            logger.info(f"Failed to initialize in CGB mode ({e}), falling back to GB mode")
            # If that fails, try with cgb=False
            if headless:
                self.pyboy = PyBoy(rom_path, window="null", cgb=False, symbols="pokered.sym")
            else:
                self.pyboy = PyBoy(rom_path, sound=sound, cgb=False, symbols="pokered.sym")

        self.reader = PokemonRedReader(self.pyboy.memory) # Initialize reader once

        self.seen_npcs: Set[Tuple[int, int, int]] = set()  # (map_id, grid_row, grid_col)
        self._npc_track_distance: int | None = None  # default: track all

        self.essential_map_locations = {
            v: i for i, v in enumerate([40, 0, 12, 1, 13, 51, 2, 54, 14, 59, 60, 61, 15, 3, 65])
        }
        self.seen_hidden_objs = {} # Potentially useful later
        self.old_seen_signs = {} # Tracks interacted signs (map_id, sign_id) -> 1.0
        self.old_seen_npcs = {} # Tracks interacted sprites (map_id, sprite_id) -> 1.0
        # Stores counts per tileset: {tileset_id: {(x, y, map_n): count}}
        self.seen_coords: Dict[int, Dict[Tuple[int, int, int], float]] = {}
        self.max_map_progress = 0
        self.last_10_moves: deque[str] = deque(maxlen=10) # Stores last 10 move directions (e.g., "↑")
        # Stores visit count per world coordinate: {(x, y): count}
        self.visited_counts: Dict[Tuple[int, int], int] = {}
        self.last_walk_dir: Dict[Tuple[int, int], str] = {}  # Track last move direction per global coord
        self.exploration_max = 10.0 # Set a non-zero max for seen_coords increment
        self.prev_coordinates: Optional[Tuple[int, int]] = None # Store previous world coords 
        
        # Rewards system
        self.current_step_reward = 0.0
        self.current_episode_reward = 0.0
        self.episode_rewards = []
        self.step_counter = 0
        self.visited_tiles = set()  # Track visited tiles for reward calculation
        self.interacted_npcs = set()  # Track NPCs that have been interacted with
        self.npc_penalty_count = 0  # Count of NPC interaction penalties
        self._all_events_string = ''  # Cache for event flags string
        self.overworld_maps = [i for i in range(0, 37)]

    def read_m(self, addr: str | int) -> int:
        if isinstance(addr, str):
            return self.pyboy.memory[self.pyboy.symbol_lookup(addr)[1]]
        return self.pyboy.memory[addr]
    
    def read_bit(self, addr: str | int, bit: int) -> bool:
        # add padding so zero will read '0b100000000' instead of '0b0'
        return bool(int(self.read_m(addr)) & (1 << bit))
    
    def tick(self, frames):
        """Advance the emulator by the specified number of frames."""
        for _ in range(frames):
            self.pyboy.tick()
        # Auto NPC tracking (if enabled) - Keep this part
        if self._npc_track_distance is not None:
            self.update_seen_npcs(self._npc_track_distance) # Assumes update_seen_npcs uses get_npcs_in_range which uses get_sprites
            
    def calculate_exploration_reward(self):
        """Calculate exploration rewards based on visited tiles."""
        reward = 0.0
        
        # Get current position
        coords = self.get_standard_coords() # (y, x, map_id)
        if coords is None or coords == (0, 0, 0):
            return 0.0
            
        # Check if this is a new tile
        y_loc, x_loc, map_id = coords
        position_key = (y_loc, x_loc, map_id)
        
        if position_key not in self.visited_tiles:
            reward += 0.01  # Reward for new tile
            self.visited_tiles.add(position_key)
        else:
            reward -= 0.02  # Penalty for revisiting
            
        return reward
        
    def check_npc_interaction_penalty(self):
        """Check if there's a penalty for interacting with the same NPC twice."""
        penalty = 0.0
        
        # Get the latest NPC interaction (if any)
        latest_npc = None
        for map_id, sprite_id in list(self.old_seen_npcs.keys()):
            latest_npc = (map_id, sprite_id)
            
        # Check if this NPC was already interacted with
        if latest_npc and latest_npc in self.interacted_npcs:
            penalty -= 1.0
            self.npc_penalty_count += 1
        elif latest_npc:
            self.interacted_npcs.add(latest_npc)
            
        return penalty
    
    def update_reward_state(self):
        """Update the reward state based on exploration and NPC interactions."""
        # Calculate the exploration reward
        self.current_step_reward = self.calculate_exploration_reward()
        
        # Add any NPC interaction penalties
        npc_penalty = self.check_npc_interaction_penalty()
        self.current_step_reward += npc_penalty
        
        # Update the episode reward
        self.current_episode_reward += self.current_step_reward
        
        # # Increment step counter
        # self.step_counter += 1
        
        # Check if we should reset the episode (every 30 steps)
        if self.step_counter >= 30:
            self.episode_rewards.append(self.current_episode_reward)
            self.current_episode_reward = 0.0
            self.step_counter = 0

    def initialize(self):
        """Enhanced initialization with coordinate tracking validation."""
        self.pyboy.set_emulation_speed(0)
        
        # Initialize emulator
        for _ in range(60):
            self.tick(1)
        
        # Get and validate initial coordinates
        initial_coords = self.get_standard_coords()
        if initial_coords is None:
            logger.warning("INITIALIZATION WARNING: Invalid initial coordinates")
            self.prev_coordinates = None
        else:
            self.prev_coordinates = initial_coords
            
            # Force initial coordinate tracking
            self.visited_counts[initial_coords] = 1
            simple_coords = (initial_coords[0], initial_coords[1])
            self.visited_counts[simple_coords] = 1
            
            # Add to visited tiles for reward tracking
            self.visited_tiles.add(initial_coords)
            
            logger.info(f"INITIALIZATION: Tracked initial coordinates {initial_coords}")
        
        # Initialize state
        self.update_state_variables()
        self.pyboy.set_emulation_speed(1)
        
        # Reset reward tracking values
        self.current_step_reward = 0.0
        self.current_episode_reward = 0.0
        self.step_counter = 0
                    
    def _force_coordinate_tracking(self):
        """Force the current coordinate to be tracked right now."""
        coord_tuple = self.get_game_coords()
        if coord_tuple[0] != -1 and coord_tuple[1] != -1:
            # Track with full tuple (x, y, map_id)
            self.visited_counts[coord_tuple] = 1
            
            # Also track with just (x, y) for backward compatibility
            simple_coord = (coord_tuple[0], coord_tuple[1])
            self.visited_counts[simple_coord] = 1
            
            # Log the forced tracking
            logger.info(f"FORCE-TRACKED coordinate {coord_tuple} with count 1")        

    def register_hooks(self):
        """Register hooks for the emulator."""
        self.pyboy.hook_register(None, "DisplayTextID.spriteHandling", self.sprite_hook, None)
        signBank, signAddr = self.pyboy.symbol_lookup("IsSpriteOrSignInFrontOfPlayer.retry")
        self.pyboy.hook_register(
            signBank,
            signAddr - 1,
            self.sign_hook,
            None,
        )

    def sprite_hook(self, *args, **kwargs):
        sprite_id = self.pyboy.memory[self.pyboy.symbol_lookup("hSpriteIndexOrTextID")[1]]
        map_id = self.pyboy.memory[self.pyboy.symbol_lookup("wCurMap")[1]]
        if map_id == 2 and sprite_id == 3:    # guide in every gym
            self.seen_npcs.add((map_id, -1, -1))              # sentinel

        self.old_seen_npcs[(map_id, sprite_id)] = 1.0
        
        # Check for NPC penalties when interacting with sprites
        self.check_npc_interaction_penalty()

    def sign_hook(self, *args, **kwargs):
        sign_id = self.read_m("hSpriteIndexOrTextID")
        map_id = self.read_m("wCurMap")
        self.old_seen_signs[(map_id, sign_id)] = 1.0
        
    def update_map_progress(self):
        map_idx = self.reader.read_current_map_id() # Use reader
        self.max_map_progress = max(0, self.max_map_progress, self.get_map_progress(map_idx))

    def get_map_progress(self, map_idx):
        return self.essential_map_locations.get(map_idx, -1)

    def has_badge(self, name: str) -> bool:
        """
        True if *name* (case‑insensitive, with or without the string "BADGE")
        is present in the badge byte ($D356).
        """
        cleaned = name.upper().replace("BADGE", "").strip()
        return cleaned in self.reader.read_badges()

    def press_buttons(self, buttons: List[str], wait: bool = True) -> str:
        """
        Dev / UI sends button strings here.  After each press we:
        • advance tracking counters
        • print full state for the dev
        """
        out = []
        for b in buttons:
            if b not in ["a","b","start","select","up","down","left","right"]:
                out.append(f"Invalid button: {b}")
                continue

            # Record source for movement arrow mapping
            if b in ["up", "down", "left", "right"]:
                prev_coord = self.get_standard_coords()
            else:
                prev_coord = None
            # Correct button press sequence
            self.pyboy.button_press(b)
            # Short press duration
            self.tick(8)
            # Release the button
            self.pyboy.button_release(b)
            # Wait duration after release based on 'wait'
            self.tick(120 if wait else 10)
            out.append(f"Pressed {b}")
            self.step_counter += 1
            logger.info(f"press_buttons():Step counter: {self.step_counter}")

            # -------- manual-input tracking -----------
            cur = self.get_standard_coords()
            if cur and cur != (0,0,0):
                y_loc, x_loc, map_id = cur
                g_y, g_x = local_to_global(y_loc, x_loc, map_id)
                g_key = (g_x, g_y)
                self.visited_counts[g_key] = self.visited_counts.get(g_key, 0) + 1
                # Track last movement direction per global coord: record at source tile if moved
                if prev_coord and prev_coord != (0,0,0) and cur != prev_coord:
                    y0, x0, map0 = prev_coord
                    gy0, gx0 = local_to_global(y0, x0, map0)
                    self.last_walk_dir[(gx0, gy0)] = b
                self.update_seen_coords_direct(cur)
                self.update_state_variables()
                # Developer print disabled to prevent duplication
                # Developer state print disabled to prevent spamming
                # print(self.get_state_from_memory())     # dev sees every step

        return "\n".join(out)

    def get_coordinates(self) -> Tuple[int, int]:
        """
        Returns the player's current coordinates from game memory.

        Returns:
            (x, y) tuple
        """
        reader = PokemonRedReader(self.pyboy.memory)
        x_loc, y_loc = reader.read_coordinates() # reader likely returns (x, y)
        return (y_loc, x_loc) # Return as (y, x)

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
                    return "↓"
                elif list(grid) == [4, 5, 6, 7]:
                    return "↑"
                elif list(grid) == [9, 8, 11, 10]:
                    return "→"
                elif list(grid) == [8, 9, 10, 11]:
                    return "←"

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
        """
        Build a 9×10 snapshot with visit counters, entities and *two* warp cells:
        • real warp tile  → player must finish on it
        • approach tile   → one step below; lets Grok walk ▾ through the door
        """
        try:
            # ---------- terrain ----------
            base18  = self.pyboy.game_area_collision()
            ds9x10  = self._downsample_array(base18)

            # ---------- player ----------
            wx, wy  = self.reader.read_coordinates()
            pr, pc  = self._get_player_grid_position()

            # ---------- entities ----------
            sign_cells  = self.get_signs("9x10")

            # Get detailed sprite information
            sprite_data_list = self.get_sprites(grid_type="9x10")
            # Create a set of NPC grid coordinates for basic collision marking
            npc_cells = {(npc["grid_col"], npc["grid_row"]) for npc in sprite_data_list}

            # ---------- warps & approach tiles ----------
            map_id      = self.reader.read_current_map_id()
            map_key     = MAP_ID_REF.get(map_id)
            warp_data   = WARP_DICT.get(map_key, [])                  # list

            warp_cells, approach_cells = set(), set()
            for w in warp_data:
                rel_x = w["x"] - wx;  rel_y = w["y"] - wy
                # Assuming player is centered on the 9x10 grid at (4, 4) relative to local map coords
                # The grid coords are (row, col) -> (y, x)
                # Player's local (x, y) is at grid (pr, pc).
                # Target local (x, y) relative to player is (rel_x, rel_y).
                # Target grid (col, row) relative to player grid position (pc, pr)
                grid_col = pc + rel_x // 2 # Simplified division by 2 for 9x10 downsampling
                grid_row = pr + rel_y // 2 # Simplified division by 2
                
                # Ensure grid coordinates are within bounds (9 rows, 10 columns for the grid)
                if 0 <= grid_row < 9 and 0 <= grid_col < 10:
                    warp_cells.add((grid_col, grid_row)) # Store as (col, row) for consistency with grid iteration
                    # Approach cell is one row below the warp cell
                    if grid_row + 1 < 9:
                        approach_cells.add((grid_col, grid_row + 1))

            # list returned to Grok (unchanged)
            warps_list = [
                {"id": i, "x": w["x"], "y": w["y"],
                "target_map": w.get("target_map_name", w.get("target_map")),
                "target_warp_id": w.get("target_warp_id", -1)}
                for i, w in enumerate(warp_data)
            ]

            # ---------- grid assembly ----------
            grid = []
            for r in range(9):
                row = []
                for c in range(10):
                    # Get standardized local coords (y, x, map_id)
                    y_loc, x_loc, player_map_id = self.get_standard_coords()
                    # Compute global player position (global_y, global_x)
                    glob_y_p, glob_x_p = local_to_global(y_loc, x_loc, player_map_id)
                    
                    # Calculate the relative offset of the current grid cell (r, c) from the player's grid position (pr, pc)
                    rel_grid_col = c - pc
                    rel_grid_row = r - pr
                    
                    # Convert relative grid offset to relative global offset.
                    # This conversion factor depends on the game's tiling and grid mapping.
                    # Assuming a simple 1:2 mapping (each 9x10 grid cell covers 2x2 local tiles, approx 1:1 global tile)
                    # This is a simplification and may need adjustment based on how the 9x10 grid view is determined.
                    # Let's assume, for now, that a relative grid offset of (dr, dc) corresponds to a relative global offset of (dr, dc).
                    # *** THIS MAPPING NEEDS VERIFICATION BASED ON GAME MECHANICS ***
                    
                    # Apply the relative global offset to the player's global position
                    glob_y = glob_y_p + rel_grid_row
                    glob_x = glob_x_p + rel_grid_col
                    
                    ent = ent_id = None
                    if (c, r) in warp_cells or (c, r) in approach_cells:
                        ent = "Warp"
                    elif (c,r) in npc_cells:
                        ent = "NPC"
                    elif (c,r) in sign_cells:
                        ent = "Sign"

                    row.append({"x": glob_x, "y": glob_y, "global_x": glob_x, "global_y": glob_y,
                                "walkable": bool(ds9x10[r, c] > 0.5),
                                "entity": ent, "entity_id": ent_id})
                grid.append(row)

            # Get player's facing direction for the LLM
            player_facing = self._get_direction(self.pyboy.game_area())

            # Format the output data with consistent glob_y, glob_x naming
            return {
                "collision_map": grid,
                "player_position": {
                    "local_x": wx,
                    "local_y": wy,
                    "glob_x": glob_x_p,
                    "glob_y": glob_y_p,
                    "direction": player_facing
                },
                "grid_position": {"row": pr, "col": pc},
                "recent_directions": list(self.last_10_moves),
                "warps": warps_list, # Keep original warp list format for now unless specified otherwise
                "sprite_data": sprite_data_list # Include detailed sprite data (already uses grid_row/col and map_id)
            }

        except Exception:
            logger.error("get_collision_map failed", exc_info=True)
            return {"collision_map": [[{"x":0,"y":0,"walkable":False,"entity":None,"entity_id":None}]*10]*9,
                    "player_position": {"x":-1,"y":-1,"direction":"?"},
                    "grid_position": {"row":-1,"col":-1},
                    "recent_directions": [], "warps": [], "sprite_data": []}

    # get_valid_moves() structured dict
    def get_valid_moves(self):
        data = self.get_collision_map()
        if not data: return []
        grid = data["collision_map"]
        pos = data.get("grid_position", {})
        pr, pc = pos.get("row"), pos.get("col")
        if pr is None or pc is None: return []
        moves = []
        for d,(dr,dc) in {"up":(-1,0),"down":(1,0),"left":(0,-1),"right":(0,1)}.items():
            r,c = pr+dr, pc+dc
            if 0<=r<len(grid) and 0<=c<len(grid[0]) and grid[r][c]["walkable"]:
                moves.append(d)
        return moves
    
    def _get_player_grid_position(self) -> Tuple[int, int]:
        """Return the player's (row, col) in the 9x10 down-sampled grid."""
        try:
            # Ensure game_area() returns a numpy array
            full_map = self.pyboy.game_area()
            if not isinstance(full_map, np.ndarray):
                 logger.error("pyboy.game_area() did not return a numpy array for player position check.")
                 return 4, 4 # Default center

            raw_r, raw_c = self._get_player_center(full_map) # Gets bottom-right screen row/col

            grid_r = raw_r // 2
            grid_c = raw_c // 2
            grid_r = max(0, min(8, grid_r))
            grid_c = max(0, min(9, grid_c))
            return grid_r, grid_c
        except Exception as e:
             logger.error(f"Error getting player grid position: {e}")
             return 4, 4 # Default to center

    # ---------------------------------------------------------------------
    # NPC helpers
    # ---------------------------------------------------------------------
    
    def get_npcs_in_range(self, max_distance: int | None = None, grid_type: str = "9x10") -> List[Dict]:
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
        # sprite_locations_9x10 is a list of dictionaries returned by get_sprites
        for sprite_info in sprite_locations_9x10:
            # Safely access grid coordinates from the dictionary
            row = sprite_info.get("grid_row")
            col = sprite_info.get("grid_col")

            # Ensure row and col are valid integers
            if isinstance(row, int) and isinstance(col, int):
                dist = abs(row - pr_9x10) + abs(col - pc_9x10);
                # Check if within max_distance or if max_distance is None
                if max_distance is None or dist <= max_distance:
                    # Append the dictionary directly, ensuring all info is kept
                    # Add distance to the dictionary for convenience
                    sprite_info["distance"] = dist;
                    npcs.append(sprite_info);
            else:
                # Log a warning if unexpected data is found
                logger.warning(f"Skipping sprite_info with invalid grid coordinates: {sprite_info}");

        logger.debug(f"get_npcs_in_range returned {len(npcs)} items: {npcs}")
        return npcs

    def update_seen_npcs(self, max_distance: int | None = None) -> int:
        """
        Adds newly observed NPCs (using 9x10 grid coords) to self.seen_npcs.
        This tracks *visual* detection based on grid position, not interactions.
        Returns count of newly seen NPCs in this update.
        """
        newly_seen_count = 0
        try:
            current_map_id: int = self.reader.read_current_map_id()
            initial_seen_count = len(self.seen_npcs)

            # Explicitly get NPCs in the dictionary format for the 9x10 grid
            # and iterate over the detailed dictionaries
            npc_list = self.get_npcs_in_range(max_distance, grid_type='9x10')
            logger.debug(f"update_seen_npcs received {len(npc_list)} items from get_npcs_in_range.")
            for i, npc_data in enumerate(npc_list):
                logger.debug(f"Processing item {i} in update_seen_npcs: type={type(npc_data)}, data={npc_data}")
                # Ensure npc_data is a dictionary before processing
                if not isinstance(npc_data, dict):
                    logger.warning(f"Skipping unexpected data format in get_npcs_in_range: {npc_data}")
                    continue

                # Access grid_row, grid_col, and sprite_index from the dictionary
                grid_row = npc_data.get("grid_row")
                grid_col = npc_data.get("grid_col")
                sprite_index = npc_data.get("sprite_index")

                # Check if essential data is present
                if grid_row is not None and grid_col is not None and sprite_index is not None:
                    # Store seen NPCs based on map and their grid coordinates
                    npc_key = (current_map_id, grid_row, grid_col)

                    if npc_key not in self.seen_npcs:
                        self.seen_npcs.add(npc_key)
                        newly_seen_count += 1

        except Exception as e:
            logger.error(f"Error updating seen NPCs: {e}")

    def get_seen_npcs(self) -> Set[Tuple[int, int, int]]:
        """Return an immutable view of all *visually detected* NPCs recorded so far."""
        return frozenset(self.seen_npcs)

    def enable_auto_npc_tracking(self, max_distance: int | None = None):
        """Call once to automatically track NPCs every frame via tick()."""
        self._npc_track_distance = max_distance
        logger.info(f"Auto NPC tracking enabled (max_distance={max_distance}).")
    
    # -----------------------------------------------------------------
    # Sign helpers
    # -----------------------------------------------------------------
    def get_signs(self, grid_type: str = "9x10") -> Set[Tuple[int, int]]:
        """
        Return on‑screen sign positions either as 18×20 (col,row) tuples
        or down‑sampled 9×10 grid coords.

        grid_type: "18x20" | "9x10"
        """
        reader = PokemonRedReader(self.pyboy.memory)
        tileset_enum = reader.read_tileset_enum()
        sign_ids = SIGN_TILE_IDS_BY_TILESET.get(tileset_enum, set())
        if not sign_ids:
            return set()

        screen_tiles = self.pyboy.game_wrapper._get_screen_background_tilemap()  # 18×20
        signs: Set[Tuple[int, int]] = set()
        for r in range(18):
            for c in range(20):
                if screen_tiles[r][c] in sign_ids:
                    if grid_type == "18x20":
                        signs.add((c, r))          # (col,row)
                    else:                          # "9x10"
                        signs.add((c // 2, r // 2))
        return signs

    def get_game_coords(self):
        """
        Returns the player's current coordinates from game memory.

        Returns:
            (y, x, map_id) tuple
        """
        reader = PokemonRedReader(self.pyboy.memory)
        x_loc, y_loc = reader.read_coordinates() # reader likely returns (x, y)
        return (y_loc, x_loc, self.read_m("wCurMap")) # Return as (y, x, map_id)
    
    def update_seen_coords(self):
        """Updates the dictionary tracking visited coordinates per tileset."""
        try:
            inc = 1.0 # Increment value
            x_pos, y_pos, map_id = self.get_game_coords()
            cur_map_tileset = self.reader.read_tileset() # Use reader

            if cur_map_tileset not in self.seen_coords:
                self.seen_coords[cur_map_tileset] = {}

            coord_key = (x_pos, y_pos, map_id)
            current_val = self.seen_coords[cur_map_tileset].get(coord_key, 0.0)
            # Use exploration_max as a ceiling
            new_val = min(current_val + inc, self.exploration_max)
            self.seen_coords[cur_map_tileset][coord_key] = new_val
        except Exception as e:
             logger.error(f"Error updating seen coordinates: {e}")

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

    def get_sprites(self, grid_type='9x10', debug=False):
        """
        Get the location of all sprites on the screen, mapped to a 9x10 grid.
        Returns a set of coordinates (column, row) representing sprite positions.
        """
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
                current_map_id = self.reader.read_current_map_id() # Get current map ID
                if 0 <= x < 10 and 0 <= y < 9:
                    on_screen_sprites.append({
                        "sprite_index": i,
                        "map_id": current_map_id,
                        "grid_row": y,
                        "grid_col": x
                    })

        # Debugging output
        if debug:
            print(f"DEBUG: On-screen sprites ({len(on_screen_sprites)}):")
            for npc_info in on_screen_sprites:
                print(f"  Sprite Index: {npc_info['sprite_index']}, Map ID: {npc_info['map_id']}, Grid Pos: ({npc_info['grid_row']}, {npc_info['grid_col']})")

        return on_screen_sprites

    # find_path should ideally use the refined collision logic from get_valid_moves
    # This would involve replacing its internal checks with calls to is_tile_walkable
    # or replicating the logic carefully. Needs significant refactoring.
    def find_path(self, target_glob_y: int, target_glob_x: int) -> tuple[str, list[str]] | tuple[str, None]:
        """
        Finds the most efficient path from the player's current position to the target global coordinates.
        Uses the 9x10 downsampled grid for pathfinding nodes, converting global target to grid target.
        NOTE: This function's internal collision checks may differ from get_valid_moves.
        """
        # Get collision map (downsampled), terrain, and sprites (9x10 grid)
        collision_map_data = self.get_collision_map()
        if not collision_map_data:
            logger.error("find_path: Could not get collision map data.")
            return "Failure: Could not retrieve map data for target conversion.", None

        # Player start position on 9x10 grid
        start_node = self._get_player_grid_position() # (row, col)

        # Convert target global coordinates to target 9x10 grid coordinates
        # Need player's local coordinates and map ID to perform the conversion.
        # Get standardized local coords (y, x, map_id)
        y_loc, x_loc, player_map_id = self.get_standard_coords()
        # Compute global player position (global_y, global_x)
        glob_y_p, glob_x_p = local_to_global(y_loc, x_loc, player_map_id)

        # Calculate the relative offset from the player's local position to the target global position
        # Then convert this relative position to the 9x10 grid coordinates.
        # This conversion logic needs to be accurate based on how the 9x10 grid maps to local/global.
        # Assuming the 9x10 grid is centered around the player's local 18x20 position,
        # the target global coords need to be related back to the player's local frame of reference.
        # A simplified conversion: Calculate the target's position relative to the player's global position,
        # then convert that relative global offset to a relative grid offset.
        # This is complex and highly dependent on the game's specific coordinate system and grid mapping.
        # For now, let's use a placeholder/simplified conversion. A robust solution requires understanding
        # exactly how global coords map to the 9x10 grid viewer relative to the player.
        
        # Placeholder Conversion: This is a simplified approximation and might not be accurate.
        # It assumes a direct mapping from a global coordinate to a single grid cell,
        # which is unlikely to be correct given the player-centric, limited 9x10 view.
        # A proper conversion would involve finding which 9x10 cell corresponds to the target global (Y,X)
        # based on the player's current local (x,y,map_id) and grid position.
        
        # *** THIS CONVERSION LOGIC NEEDS REVIEW AND CORRECTION BASED ON ACTUAL GAME MECHANICS ***
        # As a temporary measure, let's try to find the grid cell whose *global* coordinates are closest to the target.
        # This requires iterating through all 9x10 grid cells and calculating their global coordinates.
        min_dist = float('inf')
        target_grid_node = None

        for r_grid in range(9):
             for c_grid in range(10):
                  cell_data = collision_map_data["collision_map"][r_grid][c_grid]
                  cell_glob_x = cell_data.get("global_x")
                  cell_glob_y = cell_data.get("global_y")

                  if cell_glob_x is not None and cell_glob_y is not None:
                       dist = abs(cell_glob_y - target_glob_y) + abs(cell_glob_x - target_glob_x)
                       if dist < min_dist:
                            min_dist = dist
                            target_grid_node = (r_grid, c_grid)

        if target_grid_node is None:
             logger.error(f"find_path: Could not find a corresponding grid cell for global target ({target_glob_y}, {target_glob_x})")
             return f"Failure: Target global coordinates ({target_glob_y}, {target_glob_x}) are not visible or accessible in the current view.", None

        end_node = target_grid_node # Use the found grid node as the end node

        logger.info(f"find_path: Converted global target ({target_glob_y}, {target_glob_x}) to grid target ({end_node[0]}, {end_node[1]})")

        # Validate target position (on 9x10 grid)
        if not (0 <= end_node[0] < 9 and 0 <= end_node[1] < 10):
            return "Invalid target coordinates (must be 0-8 for row, 0-9 for col)", None

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
                if collision_map_data["collision_map"][neighbor_r][neighbor_c]["walkable"]:
                    # Allow moving onto a walkable tile
                    tentative_g_score = g_score.get(current_node, float('inf')) + 1

                    if tentative_g_score < g_score.get(neighbor_node, float('inf')):
                        # Found a better path to neighbor
                        came_from[neighbor_node] = current_node
                        g_score[neighbor_node] = tentative_g_score
                        f_score[neighbor_node] = tentative_g_score + heuristic(neighbor_node, end_node)
                        heapq.heappush(open_set, (f_score[neighbor_node], neighbor_node))
                else:
                    # Blocked tile, check if it's the final target
                    if neighbor_node != end_node:
                        continue

                # --- End Walkability Check ---

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
            is_target_wall = collision_map_data["collision_map"][end_node[0]][end_node[1]]["walkable"] == False
            is_target_sprite = (end_node[1], end_node[0]) in collision_map_data["sprite_data"]
            if is_target_wall or is_target_sprite:
                 block_type = "wall/obstacle" if is_target_wall else "sprite"
                 return (
                      f"Partial Success: Target ({end_node[0]}, {end_node[1]}) is a {block_type}. Path leads adjacent.",
                      path[:-1] if path else [] # Return path *excluding* final step onto block
                 )
            else:
                 return (f"Success: Found path to target at ({end_node[0]}, {end_node[1]}).", path)

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
                 
    def calculate_move_direction(self, old_x, old_y, new_x, new_y):
        """Calculates the single step direction taken between two adjacent coordinates."""
        if new_x > old_x: return "→" # Right
        if new_x < old_x: return "←" # Left
        if new_y > old_y: return "↓" # Down
        if new_y < old_y: return "↑" # Up
        return None # No change

    def update_state_variables(self):
        """Centralized function to update various state trackers."""
        self.update_map_progress()
        self.update_seen_npcs() # Update visually detected NPCs
        self.update_seen_coords()
        # Update rewards
        self.update_reward_state()
            
    def get_standard_coords(self):
        """
        CRITICAL FUNCTION: Standardized coordinate retrieval protocol.
        Returns current player coordinates in consistent (y, x, map_id) format.
        
        This is the single source of truth for coordinate acquisition.
        """
        # Direct memory access for maximum reliability
        y_pos = self.read_m("wYCoord")
        x_pos = self.read_m("wXCoord")
        map_id = self.read_m("wCurMap")
        
        # Validate coordinates (critical to prevent tracking invalid positions)
        if not (0 <= y_pos <= 255 and 0 <= x_pos <= 255 and 0 <= map_id <= 255):
            logger.warning(f"INVALID COORDINATES DETECTED: ({x_pos}, {y_pos}, {map_id})")
            return None
        
        return (y_pos, x_pos, map_id)
            
    def step(self):
        """Advance one frame and update all trackers (auto‑loop mode)."""
        try:
            prev = self.prev_coordinates
            self.tick(24)                                # one frame

            # ---- current local coord ---------------------------------------------------
            cur = self.get_standard_coords()            # (x_loc,y_loc,map_id) or None
            if cur is None:
                return
            y_loc, x_loc, map_id = cur

            # skip the dummy power-on state (0,0,0)
            if (y_loc, x_loc, map_id) == (0, 0, 0):
                return

            # ---- global key ------------------------------------------------------------
            g_y, g_x = local_to_global(y_loc, x_loc, map_id)
            g_key = (g_x, g_y)

            # visit counter
            self.visited_counts[g_key] = self.visited_counts.get(g_key, 0) + 1

            # movement arrows
            if prev and prev != cur:
                y0, x0, map0 = prev
                mv = self.calculate_move_direction(x0, y0, x_loc, y_loc)
                if mv:
                    self.last_10_moves.append(mv)

            # per‑tileset exploration + other trackers
            self.update_seen_coords_direct(cur)
            self.update_state_variables()

            # --- NEW NPC Interaction Logic based on Dialog and Facing ---
            dialog = self.get_active_dialog()
            if dialog:
                player_grid_row, player_grid_col = self._get_player_grid_position()
                player_facing = self._get_direction(self.pyboy.game_area()) # Get facing from game area

                target_npc_grid_pos = None
                if player_facing == "↑":
                    target_npc_grid_pos = (player_grid_row - 1, player_grid_col)
                elif player_facing == "↓":
                    target_npc_grid_pos = (player_grid_row + 1, player_grid_col)
                elif player_facing == "←":
                    target_npc_grid_pos = (player_grid_row, player_grid_col - 1)
                elif player_facing == "→":
                    target_npc_grid_pos = (player_grid_row, player_grid_col + 1)

                if target_npc_grid_pos:
                    # Check if there's an NPC at the calculated position
                    npcs_in_range = self.get_sprites(grid_type='9x10')
                    for npc_info in npcs_in_range:
                        if (npc_info.get("grid_row"), npc_info.get("grid_col")) == target_npc_grid_pos:
                            npc_key = (npc_info.get("map_id"), npc_info.get("sprite_index"))
                            if npc_key[0] is not None and npc_key[1] is not None and npc_key not in self.interacted_npcs:
                                self.interacted_npcs.add(npc_key)
                                logger.info(f"Identified and marked interacted NPC at grid {target_npc_grid_pos} with key {npc_key}")
                            break # Found the NPC, no need to check others

            # -------------------------------------------------------------

            self.prev_coordinates = cur

        except Exception as e:
            logger.error(f"[step] fatal error: {e}", exc_info=True)

    # update_seen_coords_direct – replace whole function body
    def update_seen_coords_direct(self, coords):
        """
        Track unique *global* coordinates per tileset (key = (g_y, g_x)).
        """
        if coords is None:
            return

        y_local, x_local, map_id = coords # Expecting (y, x, map_id) from get_standard_coords
        # convert to world map coordinates
        glob_y, glob_x = local_to_global(y_local, x_local, map_id) # local_to_global should return (Y, X)

        tileset = self.reader.read_tileset() # Get tileset for per-tileset tracking
        tileset_dict = self.seen_coords.setdefault(tileset, {}) # Get or create dict for this tileset

        key = (glob_y, glob_x) # Global key is (Y, X)
        tileset_dict[key] = min(tileset_dict.get(key, 0.0) + 1.0, self.exploration_max) # Increment count

    def get_screenshot(self):
        """Get the current screenshot."""
        return Image.fromarray(self.pyboy.screen.ndarray)

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

    def get_state_from_memory(self) -> str:
        """Return formatted live snapshot with comprehensive logging."""
        try:
            # Get logger
            logger = logging.getLogger("game")
            
            # Build the state string as before
            rdr = self.reader
            # Unpack local coordinates correctly as (y, x)
            y_loc, x_loc, map_id = self.get_standard_coords() or (-1,-1,-1)
            # Compute global coordinates using correct order
            g_y, g_x = (-1,-1) if map_id == -1 else local_to_global(y_loc, x_loc, map_id)

            # header ---------------------------------------------------------------
            s  = "# Current Game State\n\n(...)\n\n"
            s += f"Player: {rdr.read_player_name()}\n"
            s += f"Location: {rdr.read_location()} (Map ID: {map_id}) Map Coords: ({y_loc}, {x_loc})\n"
            s += f"Current Global Coordinates (Y, X): (glob_y={g_y}, glob_x={g_x})\n"

            dims = MAP_DICT.get(MAP_ID_REF.get(map_id), {})
            s += f"Local Map Dimensions (Width, Height): ({dims.get('width','?')}, {dims.get('height','?')})\n"

            # moves ---------------------------------------------------------------
            vm = self.get_valid_moves()
            s += f"Valid Immediate Moves: {', '.join(vm) if vm else 'None'}\n"
            
            # ADDED: LLM Visibility Information Section ----------------------------
            s += "\n=== LLM VISIBILITY DATA (FOR TESTING) ===\n"
            
            # Dialog state - this affects boundary checking
            dialog = self.get_active_dialog()
            s += f"Dialog Active: {dialog is not None}\n"
            if dialog:
                s += f"Dialog Text: \"{dialog}\"\n"
            
            # Rest of the state information
            if not dialog and map_id not in self.overworld_maps:
                s += f"NPCs in range: {self.get_npcs_in_range()}\n"
                s += f"Seen NPCs: {self.get_seen_npcs()}\n"
                s += f"Interacted NPCs: {self.interacted_npcs}\n"
            
            # Log the state through the logger (in addition to returning it)
            logger.debug(f"Emulator memory state snapshot:\n{s[:500]}...")
            
            return s

        except Exception as e:
            # Log the error properly
            logger = logging.getLogger("game")
            logger.error("get_state_from_memory failed", exc_info=True)
            return f"# Error generating game state string: {e} #"
        
    def format_collision_map_with_counts(self, data):
        """
        ASCII collision map with detailed global info per cell.
        Each cell shows (glob_y,glob_x,flag,value). Walkable floor: count,direction; Player: P,face; NPC: N,interacted; Unwalkable: ##,##
        """
        # Build a fixed-width multi-line grid: each cell is 2 lines with padded 3-digit fields
        map_grid = data.get("collision_map", [])
        if not map_grid:
            return "Error: Map data unavailable."
        player_pos = data.get("player_position", {})
        grid_pos = data.get("grid_position", {})
        cur_map_id = self.reader.read_current_map_id()
        # Gather interacted NPCs
        interacted_npcs = set()
        if hasattr(self, 'app') and hasattr(self.app.state, 'agent'):
            interacted_npcs = self.app.state.agent.interacted_npcs
        sprite_data_list = data.get("sprite_data", [])
        arrow_map = {'up':'↑','down':'↓','left':'←','right':'→'}
        pr, pc = grid_pos.get("row", -1), grid_pos.get("col", -1)
        # Prepare ASCII grid borders for pure-text output
        rows = len(map_grid)
        cols = len(map_grid[0]) if rows > 0 else 0
        cell_w = 9
        sep = '+' + '+'.join(['-' * cell_w for _ in range(cols)]) + '+'
        lines = [sep]
        for r, row in enumerate(map_grid):
            top_cells = []
            bot_cells = []
            for c, cell in enumerate(row):
                if not cell.get("walkable", False):
                    # Unwalkable tile: use #### placeholders for 4-width fields
                    c1, c2 = "(###,###,", " ###,###)"
                else:
                    gy = cell.get("y")
                    gx = cell.get("x")
                    # Format coordinates with right padding but no space before comma
                    gy_s = f"{gy:4d}".replace(" ", "")
                    gx_s = f"{gx:4d}".replace(" ", "")
                    # Assign flag and direction based on entity
                    if r == pr and c == pc:
                        flag_raw = "P"
                        # Show actual facing arrow glyph from player_position
                        dir_raw = player_pos.get("direction", "?")
                    elif cell.get("entity") == "NPC":
                        npc = next((s for s in sprite_data_list if s.get("grid_row") == r and s.get("grid_col") == c), None)
                        key = (npc.get("map_id"), npc.get("sprite_index")) if npc else (None, None)
                        flag_raw = "N"
                        dir_raw = "T" if key in interacted_npcs else "F"
                    else:
                        cnt = self.visited_counts.get((gx, gy), 0)
                        flag_raw = str(cnt)
                        dir_raw = arrow_map.get(self.last_walk_dir.get((gx, gy), ""), "...")
                    # Format values with right padding but no space before comma
                    flag_s = f"{flag_raw:2}".replace(" ", " ")
                    dir_s = f"{dir_raw:4}".replace(" ", " ")
                    # Compose cell lines with fixed 11-char width
                    c1 = f"({gy_s},{gx_s},"
                    c2 = f" {flag_s},{dir_s})"
                top_cells.append(c1)
                bot_cells.append(c2)
            # Append bordered row
            lines.append('|' + '|'.join(top_cells) + '|')
            lines.append('|' + '|'.join(bot_cells) + '|')
            lines.append(sep)
        return "\n".join(lines)

    def format_warp_info(self, collision_map_data):
        """Format warp information from collision map data."""
        warp_info = "Warps on Current Map:\n"
        warp_list = collision_map_data.get("warps", [])
        
        if warp_list:
            for w in warp_list:
                warp_id = w.get('id', '?')
                warp_x = w.get('x', '?')
                warp_y = w.get('y', '?')
                target_map = w.get('target_map', '?')
                target_warp_id = w.get('target_warp_id', '?')
                warp_info += f"  - ID {warp_id}: At ({warp_x}, {warp_y}) -> Target Map '{target_map}', Warp ID {target_warp_id}\n"
        else:
            warp_info += "  None detected or loaded for this map.\n"
        
        return warp_info

    def format_collision_map_simple(self, data):
        """
        Simple text grid for LLM consumption.
        Legend: #: wall, .: floor, P^ Pv P< P>: player, N: NPC, W: warp
        """
        arrow_map = {"up":"^","down":"v","left":"<","right":">"}
        grid = data.get("collision_map", [])
        pr = data.get("grid_position", {}).get("row", -1)
        pc = data.get("grid_position", {}).get("col", -1)
        player_dir = data.get("player_position", {}).get("direction", "")
        lines = []
        for r, row in enumerate(grid):
            line = []
            for c, cell in enumerate(row):
                if r == pr and c == pc:
                    sym = arrow_map.get(player_dir, "?")
                    line.append(sym)
                elif cell.get("entity") == "NPC":
                    line.append("N")
                elif cell.get("entity") == "Warp":
                    line.append("W")
                else:
                    line.append("." if cell.get("walkable") else "#")
            lines.append("".join(line))
        return "\n".join(lines)

    def format_collision_map_json(self, data):
        """
        Return JSON string of collision map for LLM consumption, including spatial layout and movement history.
        """
        cur_map_id = self.reader.read_current_map_id()
        grid_json = []
        for r, row in enumerate(data.get("collision_map", [])):
            row_json = []
            for c, cell in enumerate(row):
                cell_json = {
                    "row": r,
                    "col": c,
                    "local_x": cell["x"],
                    "local_y": cell["y"],
                    "glob_y": cell.get("y"),
                    "glob_x": cell.get("x"),
                    "walkable": cell.get("walkable", False),
                    "entity": cell.get("entity"),
                    "last_direction": self.last_walk_dir.get((cell.get("y"), cell.get("x")))
                }
                row_json.append(cell_json)
            grid_json.append(row_json)
        output = {
            "grid": grid_json,
            "player": {
                "row": data.get("grid_position", {}).get("row"),
                "col": data.get("grid_position", {}).get("col"),
                "local_x": data.get("player_position", {}).get("local_x"),
                "local_y": data.get("player_position", {}).get("local_y"),
                "glob_y": data.get("player_position", {}).get("glob_y"),
                "glob_x": data.get("player_position", {}).get("glob_x"),
                "direction": data.get("player_position", {}).get("direction")
            },
            "recent_directions": data.get("recent_directions", []),
            "warps": data.get("warps", [])
        }
        return json.dumps(output)

    def stop(self):
        self.pyboy.stop()

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
    
    @property
    def all_events_string(self):
        """
        Reads all event flags from memory and returns them as a binary string.
        Caches the result for performance until explicitly cleared.
        """
        if not self._all_events_string:
            result = ''
            for i in range(EVENT_FLAGS_START, EVENT_FLAGS_START + EVENTS_FLAGS_LENGTH):
                result += bin(self.read_m(i))[2:].zfill(8)  # Convert to binary and pad to 8 bits
            self._all_events_string = result
        return self._all_events_string
    
    def get_base_event_flags(self):
        """
        Calculate the baseline number of event flags that are already set,
        which should be excluded from reward calculations.
        """
        # Count the number of '1's in the all_events_string but exclude ignored events
        n_ignored_events = 0
        for event_id in IGNORED_EVENT_IDS:
            if self.all_events_string[event_id] == '1':
                n_ignored_events += 1
        
        return max(self.all_events_string.count('1') - n_ignored_events, 0)
    
    def get_all_events_reward(self):
        """
        Calculate rewards for events that have been newly triggered.
        Updates the rewarded_events_string to track rewarded events.
        """
        if self.all_events_string != self.past_events_string:
            # Check each bit position for new events
            first_i = -1
            for i in range(len(self.all_events_string)):
                # If event is active, not already rewarded, and not in ignore list
                if (self.all_events_string[i] == '1' and 
                    self.rewarded_events_string[i] == '0' and 
                    i not in IGNORED_EVENT_IDS):
                    # Mark as rewarded
                    self.rewarded_events_string = (
                        self.rewarded_events_string[:i] + 
                        '1' + 
                        self.rewarded_events_string[i+1:]
                    )
                    if first_i == -1:
                        first_i = i
        
        # Calculate total rewarded events minus the baseline
        return self.rewarded_events_string.count('1') - self.base_event_flags
    
    def update_max_event_rew(self):
        """Update the maximum event reward earned so far."""
        cur_rew = self.get_all_events_reward()
        self.max_event_rew = max(cur_rew, self.max_event_rew)
        return self.max_event_rew
        
    def get_game_progression_status(self):
        """
        Compiles a comprehensive event tracking report for the LLM, focusing on:
        1. Completed events
        2. In-progress events
        3. Next recommended actions based on current state
        
        Returns:
            Dict: Structured game progression information
        """
        # Get current location to determine relevant events
        current_location = self.reader.read_location()
        current_map_id = self.reader.read_current_map_id()
        
        # Initialize progression data
        progression = {
            "current_location": current_location,
            "map_id": current_map_id,
            "badges": self.reader.read_badges(),
            "completed_events": {},
            "in_progress": {},
            "recommended_next_steps": []
        }
        
        # Track all game areas based on current state
        # Gyms
        progression["gyms"] = {
            "gym1": self.gym1(),  # Pewter Gym
            "gym2": self.gym2(),  # Cerulean Gym
            "gym3": self.monitor_gym3_events(),
            "gym4": self.monitor_gym4_events(),
            "gym5": self.monitor_gym5_events(),
            "gym6": self.monitor_gym6_events(),
            "gym7": self.monitor_gym7_events(),
            "gym8": self.monitor_gym8_events(),
        }
        
        # Major areas
        progression["major_areas"] = {
            "silph_co": self.monitor_silph_co_events(),
            "rock_tunnel": self.monitor_rock_tunnel_events(),
            "poke_tower": self.monitor_poke_tower_events(),
            "rocket_hideout": self.monitor_hideout_events(),
            "mansion": self.monitor_mansion_events(),
            "safari_zone": self.monitor_safari_events(),
            "dojo": self.monitor_dojo_events(),
            "lab": self.monitor_lab_events()
        }
        
        # Key items and events
        progression["key_progression"] = {
            "hms_tms": self.monitor_hmtm_events(),
            "snorlax": self.monitor_snorlax_events()
        }
        
        # Generate next steps based on current state
        progression["recommended_next_steps"] = self.determine_next_steps(progression)
        
        return progression

    def determine_next_steps(self, progression):
        """
        Analyzes current game state to determine recommended next actions.
        Based on game progression logic and badge requirements.
        
        Args:
            progression: Dict of current game progression state
            
        Returns:
            List: Ordered list of recommended next actions
        """
        recommendations = []
        badges = progression["badges"]
        
        # Early game recommendations
        if "BOULDER" not in badges:
            recommendations.append("Defeat Brock in Pewter Gym to obtain the Boulder Badge")
        elif "CASCADE" not in badges:
            recommendations.append("Defeat Misty in Cerulean Gym to obtain the Cascade Badge")
        elif "THUNDER" not in badges:
            recommendations.append("Defeat Lt. Surge in Vermilion Gym to obtain the Thunder Badge")
        
        # Mid-game recommendations based on badges and key events
        if "THUNDER" in badges and "RAINBOW" not in badges:
            if not progression["key_progression"]["hms_tms"].get("got_hm01_cut", 0):
                recommendations.append("Obtain HM01 (Cut) on the S.S. Anne")
            else:
                recommendations.append("Defeat Erika in Celadon Gym to obtain the Rainbow Badge")
        
        # Check for Rocket Hideout progress if player has appropriate badges
        if "RAINBOW" in badges and not any(progression["major_areas"]["rocket_hideout"].values()):
            recommendations.append("Investigate the Game Corner in Celadon City")
        
        # Check Pokémon Tower progress when player has reached that point
        if "RAINBOW" in badges and not progression["major_areas"]["poke_tower"].get("beat_ghost_marowak", 0):
            recommendations.append("Obtain the Silph Scope and investigate the Pokémon Tower in Lavender Town")
        
        # Continue with more sophisticated logic based on game progression...
        
        return recommendations[:3]  # Return top 3 recommendations
    
    def calculate_direction_to_coord(self, target_x: int, target_y: int) -> str:
        """
        Calculates simple directional button presses (no obstacles) to reach target world coordinates.
        """
        # Use reader for current position
        player_x, player_y = self.reader.read_coordinates()

        x_delta = target_x - player_x
        y_delta = target_y - player_y

        instructions = []
        if x_delta != 0:
            direction = "right" if x_delta > 0 else "left"
            presses = abs(x_delta)
            plural = "s" if presses != 1 else ""
            instructions.append(f"press {direction} button {presses} time{plural}")

        if y_delta != 0:
            direction = "down" if y_delta > 0 else "up"
            presses = abs(y_delta)
            plural = "s" if presses != 1 else ""
            instructions.append(f"press {direction} button {presses} time{plural}")

        if not instructions:
            return "already at target coordinates"
        else:
            # Join instructions with ", then "
            return ", then ".join(instructions)
    
    def reset_trackers(self):
        """Enhanced tracker reset with coordinate validation."""
        # Clear all trackers
        self.seen_npcs.clear()
        self.old_seen_signs.clear()
        self.old_seen_npcs.clear()
        self.seen_coords.clear()
        self.last_10_moves.clear()
        self.visited_counts.clear()
        
        # Get current coordinates using standardized method
        current_coords = self.get_standard_coords()
        if current_coords is None:
            logger.warning("RESET WARNING: Invalid coordinates during reset")
            self.prev_coordinates = None
        else:
            self.prev_coordinates = current_coords
            
            # Force current coordinate tracking
            self.visited_counts[current_coords] = 1
            simple_coords = (current_coords[0], current_coords[1])
            self.visited_counts[simple_coords] = 1
            
            logger.info(f"RESET: Tracked coordinates {current_coords} after reset")
        
        # Reset other state
        self.max_map_progress = 0
        self.update_state_variables()
        
        # Reset reward system
        self.visited_tiles.clear()
        if current_coords:
            self.visited_tiles.add(current_coords)
        self.interacted_npcs.clear()
        self.current_step_reward = 0.0
        self.current_episode_reward = 0.0
        self.step_counter = 0
        self.npc_penalty_count = 0
        
        # Store previous episode reward if we have one
        if self.current_episode_reward != 0.0:
            self.episode_rewards.append(self.current_episode_reward)
        
        logger.info("RESET: Internal emulator trackers reset")


# Helper function (outside class or make static) used by find_path
def heuristic(a, b):
    """Manhattan distance heuristic for A*."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
