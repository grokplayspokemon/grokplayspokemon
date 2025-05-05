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

# Make sure PokemonRedReader and Tileset are correctly imported
from agent.memory_reader import PokemonRedReader, StatusCondition, Tileset
from PIL import Image, ImageDraw, ImageFont
from pyboy import PyBoy

# Import WARP_DICT for door detection
from game_data.constants import WARP_DICT, MAP_ID_REF, MAP_DICT
from config import SAVE_STATE_DIR
from game_data.events import (
    EVENT_FLAGS_START,
    EVENTS_FLAGS_LENGTH,
    MUSEUM_TICKET,
    REQUIRED_EVENTS,
    EventFlags,
)
from game_data.items import (
    HM_ITEMS,
    KEY_ITEMS,
    MAX_ITEM_CAPACITY,
    REQUIRED_ITEMS,
    USEFUL_ITEMS,
    Items,
)
from game_data.map import (
    MAP_ID_COMPLETION_EVENTS,
    MapIds,
)
from game_data.missable_objects import MissableFlags
from game_data.flags import Flags
from game_data.global_map import GLOBAL_MAP_SHAPE, local_to_global

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
        self.events = EventFlags(self.pyboy)
        # Removed self.required_events = set() - will be updated in step()
        # Removed self.required_items = set() - will be updated in step()
        self.base_event_flags = sum(
                self.read_m(i).bit_count()
                for i in range(EVENT_FLAGS_START, EVENT_FLAGS_START + EVENTS_FLAGS_LENGTH)
            )

        self.essential_map_locations = {
            v: i for i, v in enumerate([40, 0, 12, 1, 13, 51, 2, 54, 14, 59, 60, 61, 15, 3, 65])
        }
        self.seen_hidden_objs = {} # Potentially useful later
        self.old_seen_signs = {} # Tracks interacted signs (map_id, sign_id) -> 1.0
        self.old_seen_npcs = {} # Tracks interacted sprites (map_id, sprite_id) -> 1.0
        # Stores counts per tileset: {tileset_id: {(x, y, map_n): count}}
        self.seen_coords: Dict[int, Dict[Tuple[int, int, int], float]] = {}
        self.max_map_progress = 0
        # Tracks the set of REQUIRED_EVENTS currently met according to game memory
        self.current_required_events_met: Set[str] = set()
        self.missables = MissableFlags(self.pyboy)
        self.flags = Flags(self.pyboy)
        self.last_10_moves: deque[str] = deque(maxlen=10) # Stores last 10 move directions (e.g., "↑")
        # Stores visit count per world coordinate: {(x, y): count}
        self.visited_counts: Dict[Tuple[int, int], int] = {}
        self.exploration_max = 10.0 # Set a non-zero max for seen_coords increment
        self.prev_coordinates: Optional[Tuple[int, int]] = None # Store previous world coords 

    def read_m(self, addr: str | int) -> int:
        if isinstance(addr, str):
            return self.pyboy.memory[self.pyboy.symbol_lookup(addr)[1]]
        return self.pyboy.memory[addr]
    
    def read_bit(self, addr: str | int, bit: int) -> bool:
        # add padding so zero will read '0b100000000' instead of '0b0'
        return bool(int(self.read_m(addr)) & (1 << bit))

    def read_event_bits(self):
        _, addr = self.pyboy.symbol_lookup("wEventFlags")
        return self.pyboy.memory[addr : addr + EVENTS_FLAGS_LENGTH]
    
    def tick(self, frames):
        """Advance the emulator by the specified number of frames."""
        for _ in range(frames):
            self.pyboy.tick()
        # Auto NPC tracking (if enabled) - Keep this part
        if self._npc_track_distance is not None:
            self.update_seen_npcs(self._npc_track_distance) # Assumes update_seen_npcs uses get_npcs_in_range which uses get_sprites

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
            
            logger.info(f"INITIALIZATION: Tracked initial coordinates {initial_coords}")
        
        # Initialize state
        self.update_state_variables()
        self.pyboy.set_emulation_speed(1)
                    
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
        # self.seen_npcs[(map_id, sprite_id)] = 1.0 if self.scale_map_id(map_id) else 0.0
        self.old_seen_npcs[(map_id, sprite_id)] = 1.0

    def sign_hook(self, *args, **kwargs):
        sign_id = self.read_m("hSpriteIndexOrTextID")
        map_id = self.read_m("wCurMap")
        # self.seen_signs[(map_id, sign_id)] = 1.0 if self.scale_map_id(map_id) else 0.0
        self.old_seen_signs[(map_id, sign_id)] = 1.0
        
    def update_map_progress(self):
        map_idx = self.reader.read_current_map_id() # Use reader
        self.max_map_progress = max(0, self.max_map_progress, self.get_map_progress(map_idx))


    def get_map_progress(self, map_idx):
        return self.essential_map_locations.get(map_idx, -1)


    def get_required_events(self) -> Set[str]:
        """
        Checks the game's memory for currently completed required event flags.
        Returns a set of strings representing the met events.
        NOTE: This reflects the *live game state*, not necessarily all events
        the agent has ever completed in this session.
        """
        met_events = set()
        try:
            # Check standard event flags from the REQUIRED_EVENTS list
            event_values = self.events.get_events(REQUIRED_EVENTS) # Ensure EventFlags class works correctly
            for event_name, is_set in zip(REQUIRED_EVENTS, event_values):
                if is_set:
                    met_events.add(event_name)

            # Special non-standard event checks
            # SS Anne Rival Battle: Check script progress (wSSAnne2FCurScript == 4 means battle done)
            if self.read_m("wSSAnne2FCurScript") == 4:
                 met_events.add("rival3") # Assuming "rival3" is the correct name for this event

            # Game Corner Rocket: Check missable object flag
            if self.missables.get_missable("HS_GAME_CORNER_ROCKET"): # Check constant name
                 met_events.add("game_corner_rocket")

            # Saffron Guard Drink: Check flag
            if self.flags.get_bit("BIT_GAVE_SAFFRON_GUARDS_DRINK"): # Check constant name
                 met_events.add("saffron_guard")

            # Got Lapras: Check flag
            if self.flags.get_bit("BIT_GOT_LAPRAS"): # Check constant name
                 met_events.add("lapras")

        except Exception as e:
            logger.error(f"Error reading required events: {e}")

        return met_events


    def get_required_items(self) -> Set[str]:
        """Gets the set of REQUIRED_ITEMS currently in the player's bag."""
        try:
            # wNumBagItems = self.read_m("wNumBagItems")
            # _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
            # bag_item_ids = self.pyboy.memory[wBagItems : wBagItems + wNumBagItems * 2 : 2]

            # Use reader method
            items_with_qty = self.reader.read_items() # Returns list of (item_name, qty)
            current_item_names = {item_name for item_name, qty in items_with_qty}

            required_item_names = {item.name for item in REQUIRED_ITEMS}

            return current_item_names.intersection(required_item_names)
        except Exception as e:
            logger.error(f"Error reading required items: {e}")
            return set()

    def get_events_sum(self):
        # adds up all event flags, exclude museum ticket
        # This seems like a custom metric, ensure it's what you want.
        try:
            current_event_flags = sum(
                self.read_m(i).bit_count()
                for i in range(EVENT_FLAGS_START, EVENT_FLAGS_START + EVENTS_FLAGS_LENGTH)
            )
            museum_ticket_set = self.read_bit(*MUSEUM_TICKET)
            # Calculate difference from base, remove museum ticket if set
            return max(0, current_event_flags - self.base_event_flags - int(museum_ticket_set))
        except Exception as e:
             logger.error(f"Error calculating events sum: {e}")
             return 0

    def get_screenshot(self):
        """Get the current screenshot."""
        return Image.fromarray(self.pyboy.screen.ndarray)

    # not used by grok because grok can't do images
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
        # logger.warning("get_screenshot_with_overlay needs review for collision data format")
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
        Dev / UI sends button strings here.  After each press we:
        • advance tracking counters
        • print full state for the dev
        """
        out = []
        for b in buttons:
            if b not in ["a","b","start","select","up","down","left","right"]:
                out.append(f"Invalid button: {b}")
                continue

            # press / release
            self.pyboy.button_press(b);   self.tick(10)
            self.pyboy.button_release(b); self.tick(120 if wait else 10)
            out.append(f"Pressed {b}")

            # -------- manual‑input tracking -----------
            cur = self.get_standard_coords()
            if cur and cur != (0,0,0):
                x_loc, y_loc, map_id = cur
                g_y, g_x = local_to_global(y_loc, x_loc, map_id)
                g_key = (g_x, g_y)
                self.visited_counts[g_key] = self.visited_counts.get(g_key, 0) + 1
                self.update_seen_coords_direct(cur)
                self.update_state_variables()
                print(self.get_state_from_memory())     # dev sees every step

        return "\n".join(out)


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

    # ---------------------------------------------------------------------------
    # get_collision_map  –  fixed «warps» section (no AttributeError anymore)
    # ---------------------------------------------------------------------------
    def get_collision_map(self):
        """
        Build a structured 9 × 10 collision snapshot with entities, visit counters,
        player direction, recent moves and current‑map warp list.
        """
        try:
            # ---------- 1. base terrain ----------
            collision_18x20 = self.pyboy.game_area_collision()        # 18 × 20 bool
            downsampled      = self._downsample_array(collision_18x20) # 9 × 10 float

            # ---------- 2. player position ----------
            world_x, world_y = self.reader.read_coordinates()
            pr, pc           = self._get_player_grid_position()

            # ---------- 3. sprites / signs ----------
            npc_grid_coords  = {(c, r) for c, r in self.get_sprites("9x10")}
            sign_grid_coords = self.get_signs("9x10")

            # ---------- 4. warps ----------
            map_id      = self.reader.read_current_map_id()
            map_key     = MAP_ID_REF.get(map_id)
            current_warps = WARP_DICT.get(map_key, [])         # ← LIST, not dict

            warps_list = [
                {
                    "id": idx,
                    "x":  w["x"],
                    "y":  w["y"],
                    "target_map":  w.get("target_map_name", w.get("target_map")),
                    "target_warp_id": w.get("target_warp_id", -1),
                }
                for idx, w in enumerate(current_warps)
            ]

            # grid locations of those warps (entry tiles only)
            warp_grid_coords = set()
            for w in current_warps:
                rel_x = w["x"] - world_x
                rel_y = w["y"] - world_y
                gc    = rel_x + 4   # player centre col
                gr    = rel_y + 4   # player centre row
                if 0 <= gr < 9 and 0 <= gc < 10:
                    warp_grid_coords.add((gc, gr))

            # ---------- 5. facing arrow ----------
            facing = self._get_direction(self.pyboy.game_area())

            # ---------- 6. assemble 9 × 10 grid ----------
            grid = []
            for r in range(9):
                row = []
                for c in range(10):
                    gx = world_x + (c - pc)      # local → world coords
                    gy = world_y + (r - pr)

                    entity = entity_id = None
                    if (c, r) in warp_grid_coords:
                        entity = "Warp"
                    elif (c, r) in npc_grid_coords:
                        entity = "NPC"
                    elif (c, r) in sign_grid_coords:
                        entity = "Sign"

                    row.append({
                        "x": gx,
                        "y": gy,
                        "walkable": bool(downsampled[r, c] > 0.5),
                        "entity": entity,
                        "entity_id": entity_id,
                    })
                grid.append(row)

            return {
                "collision_map":    grid,
                "player_position":  {"x": world_x, "y": world_y, "direction": facing},
                "grid_position":    {"row": pr, "col": pc},
                "recent_directions": list(self.last_10_moves),
                "warps":            warps_list,
            }

        except Exception as e:
            logger.error("get_collision_map failed", exc_info=True)
            # minimal stub to keep agent alive
            return {
                "collision_map": [[{"x":0,"y":0,"walkable":False,"entity":None,"entity_id":None}]*10]*9,
                "player_position": {"x":-1,"y":-1,"direction":"?"},
                "grid_position": {"row":-1,"col":-1},
                "recent_directions": [],
                "warps": [],
            }


    # # working
    # def get_collision_map(self):
    #     """
    #     Returns a structured representation of the current screen collision map:
    #       - collision_map: 9×10 grid of cells with world coords, walkability, entity type, entity_id
    #       - player_position: world (x,y) + facing arrow
    #       - grid_position: (row,col) in the 9×10 grid
    #       - recent_directions: last 10 moves as arrows
    #       - warps: list of warp points with local x,y and target map
    #     """
    #     # 1) Base collision and downsample
    #     collision_18x20 = self.pyboy.game_area_collision()      # 18×20 array
    #     downsampled = self._downsample_array(collision_18x20)   # 9×10 float array

    #     # 2) Sprites / NPCs / Signs on 9×10 grid
    #     sprites_9x10 = set(self.get_sprites(grid_type='9x10'))
    #     npcs_raw = self.get_npcs_in_range()  # may not include "id"
    #     # Build NPC map, using id if present
    #     npc_map = { (n["grid_col"], n["grid_row"]): n.get("id") for n in npcs_raw }
    #     sign_cells   = self.get_signs(grid_type='9x10')

    #     # 3) Player world coords & grid position
    #     reader           = PokemonRedReader(self.pyboy.memory)
    #     world_x, world_y = reader.read_coordinates()
    #     pr, pc           = self._get_player_grid_position()

    #     # 4) Facing direction as arrow
    #     full_map = self.pyboy.game_area()
    #     dir_map  = {"up":"↑","down":"↓","left":"←","right":"→"}
    #     facing   = dir_map.get(self._get_direction(full_map), "?")

    #     # 5) Warps on this map
    #     map_id       = reader.read_current_map_id()
    #     map_key      = MAP_ID_REF.get(map_id)
    #     current_warps = WARP_DICT.get(map_key, [])
    #     warps_list = [
    #         {
    #             "id": idx,
    #             "x": w["x"],
    #             "y": w["y"],
    #             "target": w.get("target_map_name", w.get("target_map"))
    #         }
    #         for idx, w in enumerate(current_warps)
    #     ]
    #     # Also mark the two warp‐cells (entry+exit)
    #     warp_cells = set()
    #     for w in current_warps:
    #         rx, ry = w["x"], w["y"]
    #         # convert to collision‐grid coords
    #         cx, cy = rx + (collision_center := (4,4))[0] - world_x, ry + collision_center[1] - world_y
    #         if 0 <= cy < 9 and 0 <= cx < 10:
    #             warp_cells.add((cy, cx))
    #         if 0 <= cy+1 < 9 and 0 <= cx < 10:
    #             warp_cells.add((cy+1, cx))

    #     # 6) Build structured grid
    #     collision_map = []
    #     for r in range(9):
    #         row = []
    #         for c in range(10):
    #             # compute absolute world coords
    #             global_x = world_x + (c - pc)
    #             global_y = world_y + (r - pr)

    #             # determine entity and id
    #             if (r, c) in warp_cells:
    #                 entity    = "Warp"
    #                 entity_id = None
    #             elif (c, r) in npc_map:
    #                 entity    = "NPC"
    #                 entity_id = npc_map[(c, r)]
    #             elif (c, r) in sign_cells:
    #                 entity    = "Sign"
    #                 entity_id = None
    #             elif (c, r) in sprites_9x10:
    #                 entity    = "Sprite"
    #                 entity_id = None
    #             else:
    #                 entity    = None
    #                 entity_id = None

    #             cell = {
    #                 "x": global_x,
    #                 "y": global_y,
    #                 "walkable": bool(downsampled[r][c]),
    #                 "entity": entity,
    #                 "entity_id": entity_id
    #             }
    #             row.append(cell)
    #         collision_map.append(row)

    #     return {
    #         "collision_map":    collision_map,
    #         "player_position":  {"x": world_x, "y": world_y, "direction": facing},
    #         "grid_position":    {"row": pr, "col": pc},
    #         "recent_directions": list(self.last_10_moves),
    #         "warps":            warps_list
    #     }



    # def get_collision_map(self):
    #     """
    #     Returns an ASCII representation of the current screen collision map.
    #     """
    #     collision_18x20 = self.pyboy.game_area_collision()
    #     downsampled = self._downsample_array(collision_18x20)
    #     sprites_9x10 = self.get_sprites(grid_type='9x10')
    #     pr, pc = self._get_player_grid_position()

    #     reader = PokemonRedReader(self.pyboy.memory)
    #     player_world_x, player_world_y = reader.read_coordinates()
    #     map_id = reader.read_current_map_id()
    #     map_key = MAP_ID_REF.get(map_id)
    #     current_warps = WARP_DICT.get(map_key, [])

    #     full_map = self.pyboy.game_area()
    #     direction = self._get_direction(full_map)
    #     if direction == "no direction found":
    #         return None

    #     dir_codes = {"up": 3, "down": 4, "left": 5, "right": 6}
    #     player_dir = dir_codes.get(direction, 3)

    #     # Debugging direction and sprite info
    #     # print(f"DEBUG: Player direction: {direction} ({player_dir})")
    #     # print(f"DEBUG: Sprites: {sprites_9x10}")

    #     base = []
    #     for r in range(9):
    #         row = []
    #         for c in range(10):
    #             row.append("0" if downsampled[r][c] else "1")
    #         base.append(row)

    #     collision_center = (4, 4)
    #     offset_x = collision_center[0] - player_world_x
    #     offset_y = collision_center[1] - player_world_y

    #     warp_cells = set()
    #     for warp in current_warps:
    #         warp_collision_x = warp["x"] + offset_x
    #         warp_collision_y = warp["y"] + offset_y
    #         if 0 <= warp_collision_y < 9 and 0 <= warp_collision_x < 10:
    #             warp_cells.add((warp_collision_y, warp_collision_x))
    #             # print(f"DEBUG: Warp at collision ({warp_collision_y},{warp_collision_x})")

    #         warp_below_y = warp_collision_y + 1
    #         if 0 <= warp_below_y < 9 and 0 <= warp_collision_x < 10:
    #             warp_cells.add((warp_below_y, warp_collision_x))
    #             # print(f"DEBUG: Warp below at collision ({warp_below_y},{warp_collision_x})")

    #     for (r, c) in warp_cells:
    #         base[r][c] = "W"

    #     # # Place sprites (taken directly from their code)
    #     # for sprite_pos in sprites_9x10:
    #     #     sprite_c, sprite_r = sprite_pos
    #     #     if 0 <= sprite_r < 9 and 0 <= sprite_c < 10:
    #     #         if (sprite_r, sprite_c) not in warp_cells:
    #     #             base[sprite_r][sprite_c] = "2"
    #     #             # print(f"DEBUG: Placed sprite at collision map ({sprite_r},{sprite_c})")
        
    #     # ------------------------------------------------------------
    #     # Place NPCs, signs, and generic sprites on the 9×10 grid
    #     # precedence: player > warp > NPC > sign > generic sprite
    #     # ------------------------------------------------------------
    #     npc_cells   = {(c, r) for c, r, _ in                                    # already 9×10 coords
    #                    [(n["grid_col"], n["grid_row"], n["distance"])
    #                     for n in self.get_npcs_in_range()]}
    #     sign_cells  = self.get_signs(grid_type='9x10')
    #     sprite_cells = set(sprites_9x10)           # generic sprites (includes NPCs but harmless)

    #     for (c, r) in sprite_cells:
    #         if not (0 <= r < 9 and 0 <= c < 10):
    #             continue
    #         if (r, c) in warp_cells or (r, c) == (pr, pc):
    #             continue  # warp or player already occupies the cell

    #         if (c, r) in npc_cells:
    #             base[r][c] = "N"
    #         elif (c, r) in sign_cells:
    #             base[r][c] = "S"
    #         else:
    #             base[r][c] = "2"            # generic / unknown sprite

    #     # Player placement (direct from their code)
    #     base[pr][pc] = str(player_dir)
    #     # print(f"DEBUG: Placed player at collision map ({pr},{pc}) direction {player_dir}")

    #     # Build the collision map with coordinates
    #     lines = []
    #     # Add column headers
    #     lines.append("   " + " ".join(str(i) for i in range(10)))
    #     # Add rows with row numbers
    #     for r in range(9):
    #         row_str = " ".join(base[r])
    #         lines.append(f"{r}  {row_str}")

    #     # lines += [
    #     #     "",
    #     #     "Legend:",
    #     #     "W  – warp (entry + exit square)",
    #     #     "0  – walkable",
    #     #     "1  – unwalkable",
    #     #     "2  – other sprite",
    #     #     "N  – NPC",
    #     #     "S  – sign / notice board",
    #     #     "3  – player facing up",
    #     #     "4  – player facing down",
    #     #     "5  – player facing left",
    #     #     "6  – player facing right",
    #     # ]

    #     if current_warps:
    #         lines += ["", "Warps:"]
    #         for w in current_warps:
    #             tgt = w.get("target_map_name", w.get("target_map"))
    #             lines.append(f"x: {w['x']} y: {w['y']} target: {tgt}")

    #     return "\n".join(lines)

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



    # # get_valid_moves() ascii
    # def get_valid_moves(self):
    #     """Return list of valid cardinal directions based on the ASCII collision map overlay."""
    #     ascii_map = self.get_collision_map()
    #     if not ascii_map:
    #         return []
    #     lines = ascii_map.splitlines()
    #     # Expect header + 9 rows of map data
    #     if len(lines) < 10:
    #         return []
    #     # Parse the 9×10 grid rows (skip header)
    #     grid_rows = [line.split()[1:] for line in lines[1:10]]
    #     # Locate player tile (codes '3','4','5','6')
    #     pr = pc = None
    #     for r, row in enumerate(grid_rows):
    #         for c, ch in enumerate(row):
    #             if ch in ('3', '4', '5', '6'):
    #                 pr, pc = r, c
    #                 break
    #         if pr is not None:
    #             break
    #     if pr is None:
    #         return []
    #     # Check adjacent cells for walkability ('1' is unwalkable)
    #     valid = []
    #     directions = {'up': (-1, 0), 'down': (1, 0), 'left': (0, -1), 'right': (0, 1)}
    #     for d, (dr, dc) in directions.items():
    #         nr, nc = pr + dr, pc + dc
    #         if 0 <= nr < len(grid_rows) and 0 <= nc < len(grid_rows[0]):
    #             if grid_rows[nr][nc] != '1':
    #                 valid.append(d)
    #     return valid
    
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
        """
        Adds newly observed NPCs (using 9x10 grid coords) to `self.seen_npcs`.
        This tracks *visual* detection based on grid position, not interactions.
        Returns count of newly seen NPCs in this update.
        """
        newly_seen_count = 0
        try:
            current_map_id: int = self.reader.read_current_map_id()
            initial_seen_count = len(self.seen_npcs)

            # Use get_npcs_in_range which works with the 9x10 grid
            for npc_info in self.get_npcs_in_range(max_distance):
                # Store based on map and 9x10 grid coordinates
                npc_key = (current_map_id, npc_info["grid_row"], npc_info["grid_col"])
                if npc_key not in self.seen_npcs:
                    self.seen_npcs.add(npc_key)
                    newly_seen_count += 1

        except Exception as e:
            logger.error(f"Error updating seen NPCs: {e}")

    def get_seen_npcs(self) -> Set[Tuple[int, int, int]]:
        """Return an immutable view of all *visually detected* NPCs recorded so far."""
        return frozenset(self.seen_npcs)


    def enable_auto_npc_tracking(self, max_distance: int | None = None):
        """Call once to automatically track NPCs every frame via `tick()`."""
        self._npc_track_distance = max_distance
        logger.info(f"Auto NPC tracking enabled (max_distance={max_distance}).")

    # _original_tick = tick - Remove complex hooking for now
    
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
        return (self.read_m("wXCoord"), self.read_m("wYCoord"), self.read_m("wCurMap"))
    
    def update_seen_coords(self):
        """Updates the dictionary tracking visited coordinates per tileset."""
        try:
            inc = 1.0 # Increment value
            x_pos, y_pos, map_n = self.get_game_coords()
            cur_map_tileset = self.reader.read_tileset() # Use reader

            if cur_map_tileset not in self.seen_coords:
                self.seen_coords[cur_map_tileset] = {}

            coord_key = (x_pos, y_pos, map_n)
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
        # if debug:
        #     print(f"DEBUG: On-screen sprites: {len(on_screen_sprites)}")
        #     for idx, sx, sy, gx, gy in on_screen_sprites:
        #         print(f"  Sprite {idx}: screen (x={sx}, y={sy}) -> grid (x={gx}, y={gy})")
        #     print(f"DEBUG: Mapped sprite positions: {sprite_positions}")

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
        # logger.warning("find_path uses its own collision logic, potentially inconsistent with get_valid_moves.")

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
        # Update required items/events based on current memory
        # self.required_items = self.get_required_items() # Only if needed elsewhere frequently
        self.current_required_events_met = self.get_required_events()
        self.update_seen_coords()
        
    def get_standard_coords(self):
        """
        CRITICAL FUNCTION: Standardized coordinate retrieval protocol.
        Returns current player coordinates in consistent (x, y, map_id) format.
        
        This is the single source of truth for coordinate acquisition.
        """
        # Direct memory access for maximum reliability
        x_pos = self.read_m("wXCoord")
        y_pos = self.read_m("wYCoord")
        map_id = self.read_m("wCurMap")
        
        # Validate coordinates (critical to prevent tracking invalid positions)
        if not (0 <= x_pos <= 255 and 0 <= y_pos <= 255 and 0 <= map_id <= 255):
            logger.warning(f"INVALID COORDINATES DETECTED: ({x_pos}, {y_pos}, {map_id})")
            return None
        
        return (x_pos, y_pos, map_id)
            
    def step(self):
        """Advance one frame and update all trackers (auto‑loop mode)."""
        try:
            prev = self.prev_coordinates
            self.tick(1)                                # one frame

            # ---- current local coord ---------------------------------------------------
            cur = self.get_standard_coords()            # (x_loc,y_loc,map_id) or None
            if cur is None:
                return
            x_loc, y_loc, map_id = cur

            # skip the dummy power‑on state (0,0,0)
            if (x_loc, y_loc, map_id) == (0, 0, 0):
                return

            # ---- global key ------------------------------------------------------------
            g_y, g_x = local_to_global(y_loc, x_loc, map_id)
            g_key = (g_x, g_y)

            # visit counter
            self.visited_counts[g_key] = self.visited_counts.get(g_key, 0) + 1

            # movement arrows
            if prev and prev != cur:
                px, py, _ = prev
                mv = self.calculate_move_direction(px, py, x_loc, y_loc)
                if mv:
                    self.last_10_moves.append(mv)

            # per‑tileset exploration + other trackers
            self.update_seen_coords_direct(cur)
            self.update_state_variables()

            # developer read‑out every frame (auto mode)
            print(self.get_state_from_memory())

            self.prev_coordinates = cur

        except Exception as e:
            logger.error(f"[step] fatal error: {e}", exc_info=True)

    # update_seen_coords_direct – replace whole function body
    def update_seen_coords_direct(self, coords):
        """
        Track unique *global* coordinates per tileset (key = (g_x, g_y)).
        """
        if coords is None:
            return

        x_local, y_local, map_id = coords
        # convert to world map coordinates
        g_y, g_x = local_to_global(y_local, x_local, map_id)

        tileset = self.reader.read_tileset()
        tile_dict = self.seen_coords.setdefault(tileset, {})

        key = (g_x, g_y)                      # global key
        tile_dict[key] = min(tile_dict.get(key, 0.0) + 1.0, self.exploration_max)


    def get_state_from_memory(self) -> str:
        """Return formatted live snapshot (debug heavy)."""
        try:
            rdr = self.reader
            x_loc, y_loc, map_id = self.get_standard_coords() or (-1,-1,-1)
            g_y, g_x             = (-1,-1) if map_id == -1 else local_to_global(y_loc,x_loc,map_id)

            # header ---------------------------------------------------------------
            s  = "# Current Game State\n\n(...)\n\n"
            s += f"Player: {rdr.read_player_name()}\n"
            s += f"Location: {rdr.read_location()} (Map ID: {map_id}) Map Coords: ({x_loc}, {y_loc})\n"
            s += f"Current Global Coordinates (X, Y): ({g_x}, {g_y})\n"

            dims = MAP_DICT.get(MAP_ID_REF.get(map_id), {})
            s += f"Local Map Dimensions (Width, Height): ({dims.get('width','?')}, {dims.get('height','?')})\n"

            # moves ---------------------------------------------------------------
            vm = self.get_valid_moves()
            s += f"Valid Immediate Moves: {', '.join(vm) if vm else 'None'}\n---\nDEBUG INFO:\n"

            # debug block ---------------------------------------------------------
            s += f"  Interacted NPCs/Sprites (map_id, sprite_id): {self.old_seen_npcs}\n"
            s += f"  Interacted Signs (map_id, text_id): {self.old_seen_signs}\n"
            s += f"  Max Map Progress Index: {self.max_map_progress}\n"
            s += f"  Current Required Events Met (Live): {self.current_required_events_met}\n"

            ts = rdr.read_tileset()
            s += f"  Seen Coords Count (Current Tileset {ts}): {len(self.seen_coords.get(ts,{}))}\n"

            visit_cnt = self.visited_counts.get((g_x, g_y), 0)
            s += f"  Visited Count (Current Global Coord ({g_x}, {g_y})): {visit_cnt}\n"
            s += f"  Total Visited Coordinates: {len(self.visited_counts)}\n"

            # omit the power‑on dummy entry when dumping
            clean_dict = {k:v for k,v in self.visited_counts.items() if k != (0,0)}
            s += f"  All Visited Coordinates: {clean_dict}\n---\n"

            dlg = rdr.read_dialog() or "None"
            s += f"Dialog: {dlg}\n"

            # collision + warps ----------------------------------------------------
            cm = self.get_collision_map()
            s += f"{self.format_collision_map_with_counts(cm)}\n\n"
            s += self.format_warp_info(cm)
            return s

        except Exception as e:
            logger.error("get_state_from_memory failed", exc_info=True)
            return f"# Error generating game state string: {e} #"



    def format_collision_map_ascii(self, data, interacted_npcs_map):
        """Enhanced collision map formatter with improved visit count visualization."""
        lines = []
        lines.append("Collision Map (9x10 grid - Your relative view):")
        player_pos = data.get("player_position", {})
        grid_pos = data.get("grid_position", {})
        map_grid = data.get("collision_map", [])
        current_map_id = self.reader.read_current_map_id()
        
        player_grid_r, player_grid_c = grid_pos.get("row", -1), grid_pos.get("col", -1)
        
        if player_grid_r == -1 or not map_grid:
            lines.append("  Error: Map/Player data unavailable.")
            return "\n".join(lines)
        
        for r, row_data in enumerate(map_grid):
            line = []
            for c, cell in enumerate(row_data):
                symbol = "?" # Default error
                if isinstance(cell, dict):
                    # Default symbol is unwalkable
                    symbol = "#"
                    
                    if cell.get("walkable", False):
                        # Get world coordinates from the cell
                        global_x = cell.get("x", -1)
                        global_y = cell.get("y", -1)
                        
                        # Create keys in BOTH formats for consistency with step()
                        full_key = (global_x, global_y, current_map_id)
                        simple_key = (global_x, global_y)
                        
                        # Try both key formats, preferring the full key
                        visit_count = self.visited_counts.get(full_key,
                                        self.visited_counts.get(simple_key, 0))
                        
                        # Display visit count (0 will show as dot)
                        if visit_count > 0:
                            symbol = str(visit_count % 10)
                        else:
                            symbol = "."
                    
                    entity = cell.get("entity")
                    entity_id = cell.get("entity_id")
                    
                    # Entity symbols override visit counts
                    if r == player_grid_r and c == player_grid_c:
                        symbol = "P"
                    elif entity == "Warp":
                        symbol = "W"
                    elif entity == "Sign":
                        symbol = "S"
                    elif entity == "NPC":
                        # Check if this NPC has been interacted with
                        npc_key = (current_map_id, entity_id)
                        if npc_key in interacted_npcs_map:
                            symbol = "X" # Interacted
                        else:
                            symbol = "N" # Not interacted
                line.append(symbol)
            lines.append(" ".join(line))
        
        lines.append(f"Player Facing: {player_pos.get('direction', '?')}")
        recent_dirs = data.get('recent_directions', [])
        moves_str = ' '.join(recent_dirs) if recent_dirs else "None yet"
        lines.append(f"Recent Moves: {moves_str}")
        return "\n".join(lines)

    def format_collision_map_with_counts(self, data):
        """
        ASCII collision map with per‑tile visit counts (0 ➙ ‘.’, 1‑9 digits).
        Uses GLOBAL coordinate keys stored in self.visited_counts.
        """
        lines = ["Collision Map (9x10 grid - Your relative view):"]

        player_pos = data.get("player_position", {})
        grid_pos   = data.get("grid_position", {})
        map_grid   = data.get("collision_map", [])
        cur_map_id = self.reader.read_current_map_id()

        if not map_grid or "row" not in grid_pos:
            lines.append("  Error: Map data unavailable.")
            return "\n".join(lines)

        p_r, p_c = grid_pos["row"], grid_pos["col"]

        for r, row_data in enumerate(map_grid):
            line = []
            for c, cell in enumerate(row_data):
                symbol = "#"                                   # default unwalkable

                if cell.get("walkable", False):
                    # local ➙ global conversion
                    g_y, g_x = local_to_global(cell["y"], cell["x"], cur_map_id)
                    v_cnt = self.visited_counts.get((g_x, g_y), 0)

                    symbol = str(min(v_cnt, 9)) if v_cnt else "."

                # entity overrides
                if r == p_r and c == p_c:
                    symbol = "P"
                else:
                    ent = cell.get("entity")
                    if ent == "Warp":
                        symbol = "W"
                    elif ent == "Sign":
                        symbol = "S"
                    elif ent == "NPC":
                        symbol = "N"

                line.append(symbol)
            lines.append(" ".join(line))

        lines.append(f"Player Facing: {player_pos.get('direction','?')}")
        moves = data.get("recent_directions", [])
        lines.append(f"Recent Moves: {' '.join(moves) if moves else 'None yet'}")
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
            
    # def get_state_from_memory(self) -> str:
    #     """
    #     Reads the game state and returns a comprehensive string representation for the LLM.
    #     """
    #     try:
    #         # Use the single reader instance
    #         reader = self.reader
    #         memory_str = "# Current Game State\n\nThis information is direct from the emulator at the present moment along with your collision map. Use the information below to make decisions about what to do and where to go next.\n\n"

    #         name = reader.read_player_name()
    #         name = name if name != "NINTEN" else "Not yet set"
    #         # rival_name = reader.read_rival_name() # Optional
    #         # rival_name = rival_name if rival_name != "SONY" else "Not yet set" # Optional

    #         # Current Location & Coords
    #         location = reader.read_location()
    #         map_id = reader.read_current_map_id()
    #         map_key = MAP_ID_REF.get(map_id)
    #         dims = MAP_DICT.get(map_key, {})
    #         width = dims.get('width', 'unknown')
    #         height = dims.get('height', 'unknown')
    #         cur_coords = reader.read_coordinates()

    #         memory_str += f"Player: {name}\n"
    #         memory_str += f"Location: {location} (Map ID: {map_id})\n"
    #         memory_str += f"Local Map Dimensions (x, y): ({width}, {height})\n"
    #         memory_str += f"Current World Coordinates (x, y): {cur_coords}\n" # Use world coords

    #         # Valid Moves & Pathing (Example - pathing not implemented here)
    #         valid_moves = self.get_valid_moves()
    #         valid_moves_str = ", ".join(valid_moves) if valid_moves else "None"
    #         memory_str += f"Valid Immediate Moves: {valid_moves_str}\n"
    #         # Example of adding path instruction (replace with actual pathfinding logic if available)
    #         # target_x, target_y = (4, 5) # Example target
    #         # path_instr = self.calculate_direction_to_coord(target_x, target_y) # Assumes simple direct path
    #         # memory_str += f"Keystroke Sequence to Coord ({target_x}, {target_y}): {path_instr}\n"


    #         # --- TESTING / Debug Info ---
    #         memory_str += "---\n" # Separator for debug info
    #         memory_str += f"DEBUG: Interacted NPCs (map_id, sprite_id): {self.old_seen_npcs}\n"
    #         memory_str += f"DEBUG: Interacted Signs (map_id, sign_id): {self.old_seen_signs}\n"
    #         memory_str += f"DEBUG: Max Map Progress Index: {self.max_map_progress}\n"
    #         memory_str += f"DEBUG: Current Required Events Met (Live): {self.current_required_events_met}\n" # Renamed variable

    #         # Seen Coords Info (can be large, show summary)
    #         current_tileset = reader.read_tileset()
    #         seen_coords_on_tileset = self.seen_coords.get(current_tileset, {})
    #         memory_str += f"DEBUG: Seen Coords Count (Current Tileset {current_tileset}): {len(seen_coords_on_tileset)}\n"
    #         # memory_str += f"DEBUG: Full Seen Coords: {self.seen_coords}\n" # Potentially too large

    #         memory_str += f"DEBUG: Essential Map Locations (map_id: progress_idx): {self.essential_map_locations}\n"
    #         # get_required_events() is used to populate current_required_events_met, no need to call again
    #         # memory_str += f"DEBUG: get_required_events() call result: {self.get_required_events()}\n"
    #         cur_visited_count = self.visited_counts.get(cur_coords, 0)
    #         memory_str += f"DEBUG: Visited Count (Current Coord {cur_coords}): {cur_visited_count}\n"
    #         # memory_str += f"DEBUG: All Visited Counts: {self.visited_counts}\n" # Potentially too large
    #         memory_str += "---\n"

    #         # --- Live Game Data ---
    #         # Inventory (Optional - uncomment if needed)
    #         # memory_str += "Inventory:\n"
    #         # items = reader.read_items()
    #         # if items:
    #         #     for item, qty in items:
    #         #         memory_str += f"  {item} x {qty}\n"
    #         # else:
    #         #     memory_str += "  Empty\n"

    #         # Dialog
    #         dialog = reader.read_dialog()
    #         memory_str += f"Dialog: {dialog if dialog else 'None'}\n"

    #         # --- Visual Map Representation ---
    #         collision_map_data = self.get_collision_map()

    #         # ASCII Map
    #         def format_collision_map_ascii(data):
    #             lines = []
    #             lines.append("Collision Map (9x10 grid - Your relative view):")
    #             player_world_x = data["player_position"]["x"]
    #             player_world_y = data["player_position"]["y"]
    #             player_grid_r, player_grid_c = data["grid_position"]["row"], data["grid_position"]["col"]

    #             for r, row_data in enumerate(data["collision_map"]):
    #                 line = []
    #                 for c, cell in enumerate(row_data):
    #                     symbol = "#" # Default: Not Walkable
    #                     if cell["walkable"]:
    #                         symbol = "." # Walkable

    #                     # Overlay entities - Prioritize Player
    #                     if r == player_grid_r and c == player_grid_c:
    #                          symbol = "P" # Player position takes precedence
    #                     elif cell["entity"] == "Warp":
    #                         symbol = "W"
    #                     elif cell["entity"] == "NPC": # Represents any sprite
    #                         symbol = "X" # Changed from N to X
    #                     elif cell["entity"] == "Sign":
    #                         symbol = "S"

    #                     line.append(symbol)
    #                 lines.append(" ".join(line))
    #             lines.append(f"Player Facing: {data['player_position']['direction']}")
    #             # Format recent moves with default
    #             moves_str = ' '.join(data['recent_directions']) if data['recent_directions'] else "None yet"
    #             lines.append(f"Recent Moves: {moves_str}")
    #             return "\n".join(lines)

    #         memory_str += f"{format_collision_map_ascii(collision_map_data)}\n\n"

    #         # Structured Warp Info
    #         memory_str += "Warps on Current Map:\n"
    #         if collision_map_data["warps"]:
    #             for w in collision_map_data["warps"]:
    #                 memory_str += f"  - ID {w['id']}: At ({w['x']}, {w['y']}) -> Target Map '{w['target_map']}', Warp ID {w['target_warp_id']}\n"
    #         else:
    #             memory_str += "  None detected on this map.\n"

    #         return memory_str

    #     except Exception as e:
    #         logger.error(f"Error generating state string: {e}", exc_info=True)
    #         return "# Error generating game state string #"

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
        
        logger.info("RESET: Internal emulator trackers reset")

# Helper function (outside class or make static) used by find_path
def heuristic(a, b):
    """Manhattan distance heuristic for A*."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
