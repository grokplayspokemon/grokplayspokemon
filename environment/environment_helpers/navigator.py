# navigator.py

from __future__ import annotations

import json
import time
import random
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING, Dict, Any

from pyboy.utils import WindowEvent
            
from environment.data.environment_data.constants import WARP_DICT
from environment.data.environment_data.map import MapIds

if TYPE_CHECKING:
    from environment import RedGymEnv

from environment.data.recorder_data.global_map import local_to_global, global_to_local

# Import logging system
import sys
import os
sys.path.append('/puffertank/grok_plays_pokemon')
from utils.logging_config import get_pokemon_logger

class InteractiveNavigator:
    def __init__(self, env_instance: RedGymEnv):
        self.env: RedGymEnv = env_instance
        self.pyboy = self.env.pyboy
        
        # Initialize logger
        self.logger = get_pokemon_logger()

        # Full flattened quest path coords and their map IDs
        self.sequential_coordinates: List[Tuple[int, int]] = []
        self.coord_map_ids: List[int] = []
        self.current_coordinate_index: int = 0

        self.active_quest_id: Optional[int] = None
        self._last_loaded_quest_id: Optional[int] = None

        # Multi-segment load tracking
        self.map_segment_count: dict[int, int] = {}

        # Raw local coords placeholder
        self.current_coords = None

        # Fallback/resume logic
        self.last_position = None
        self.quest_locked = False
        self._direction = 1                # 1 = forward, -1 = backward
        self._fallback_mode = False
        self._original_quest_id: Optional[int] = None

        # Movement tracking
        self.movement_failure_count = 0
        self.max_movement_failures = 10
        self.navigation_status = "idle"

        # FIXED: Enhanced warp tracking with proper blocking
        self.door_warp = False
        self.last_warp_time = 0.0
        self.WARP_COOLDOWN_SECONDS = 0.5
        self.last_warp_origin_map: Optional[int] = None
        self._post_warp_exit_pos: Optional[Tuple[int, int]] = None
        self._left_home = False
        # Track last map for warp gating; managed on warp events
        self._last_map_id = None
        
        # FIXED: Add proper warp blocking state
        self._blocked_warps: dict[tuple[int, int], float] = {}  # Stores (from_map, to_map) -> expiry_time
        self.WARP_BLOCK_DURATION = 2.0  # Seconds to block reverse warps
        
        # FIXED: Add tracking for house stairs to prevent infinite loops
        self._house_stair_warp_count = 0
        self._max_house_stair_warps = 1  # Only allow one stair warp before blocking
        self._house_stair_timer = 0.0
        self._house_stair_cooldown = 10.0  # Longer cooldown for house stairs
        self._last_stair_direction = None  # Track whether last stair was up or down
        self._recent_maps = []  # Track recently visited maps
        self._max_recent_maps = 3  # Only track the last 3 maps
        self._map_cooldown_period = 3.0  # Time before allowing return to a recent map
        self._map_visit_times = {}  # Track when maps were last visited
        self._prevent_immediate_return = True  # Prevent immediate warp back to origin map
        self._last_warp_origin = None  # Track origin of last warp
        self._last_warp_target = None  # Track target of last warp
        self._warp_delay_timer = 0.0  # Timer to enforce delay between warps
        self._post_warp_delay = 1.0  # Seconds to delay after a warp
        
        # Action mapping
        self.ACTION_MAPPING_STR_TO_INT = {
            "down": 0,
            "left": 1,
            "right": 2,
            "up": 3,
        }

    # ...............................................................
    #  U T I L I T I E S
    # ...............................................................
    def _get_player_global_coords(self) -> Optional[Tuple[int, int]]:
        if not hasattr(self.env, "get_game_coords"):
            print("Navigator: env lacks get_game_coords()")
            return None
        try:
            lx, ly, map_id = self.env.get_game_coords() # returns tuple of (local_x, local_y, map_id)
            gy, gx = local_to_global(ly, lx, map_id)
            pos3 = (gy, gx, map_id)
            print(f'navigator.py: _get_player_global_coords(): global_coords (y,x)=({gy},{gx}), map_id={map_id}, local_coords=(y={ly},x={lx})')
            
            # FIXED: Enhanced position tracking with validation
            if lx < 0 or ly < 0 or map_id < 0:
                print(f"Navigator: Invalid coordinates detected: local=({lx},{ly}), map={map_id}")
                return None
                
            if self.last_position != pos3:
                # Log navigation position changes
                if self.logger:
                    self.logger.log_navigation_event("POSITION_UPDATE", {
                        'message': f'Player position updated',
                        'global_coordinates': [gy, gx],
                        'local_coordinates': [lx, ly],
                        'map_id': map_id,
                        'quest_id': self.active_quest_id
                    })
                
                print(
                    f"navigator.py: _get_player_global_coords(): "
                    f"global_coords=({gy},{gx}), map_id={map_id}, "
                    f"local_coords=(y={ly},x={lx})"
                )
            self.last_position = pos3
            return gy, gx
        except Exception as e:
            print(f"Navigator: ERROR reading coords: {e}")
            return None

    @staticmethod
    def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    # FIXED: Add method to ensure quest is loaded
    def _ensure_quest_loaded(self) -> bool:
        """Ensure a quest is loaded by using the current quest ID from quest manager"""
        # PRIORITY 1: Get current quest from quest manager if available (most reliable)
        quest_manager = getattr(self.env, "quest_manager", None)
        if quest_manager is not None:
            current_quest = quest_manager.get_current_quest()
            if current_quest is not None:
                print(f"Navigator: Loading quest {current_quest} from quest manager (priority 1)")
                
                # Force reload if different quest or no coordinates
                if self.active_quest_id != current_quest or not self.sequential_coordinates:
                    if self.load_coordinate_path(current_quest):
                        setattr(self.env, "current_loaded_quest_id", current_quest)
                        return True
                    else:
                        print(f"Navigator: Failed to load quest {current_quest} from quest manager")
                else:
                    print(f"Navigator: Quest {current_quest} already loaded with {len(self.sequential_coordinates)} coordinates")
                    return True
        
        # PRIORITY 2: Check if environment has a quest ID set
        env_qid = getattr(self.env, "current_loaded_quest_id", None)
        if env_qid is not None:
            print(f"Navigator: Loading quest {env_qid} from environment (priority 2)")
            if self.load_coordinate_path(env_qid):
                return True
        
        # PRIORITY 3: Check if we already have a valid quest loaded
        if self.active_quest_id is not None and self.sequential_coordinates:
            print(f"Navigator: Using already loaded quest {self.active_quest_id} (priority 3)")
            return True
        
        # LAST RESORT: Find nearest quest path (this should rarely be needed now)
        print("Navigator: No current quest available, finding nearest available quest path (last resort)")
        cur_map = self.env.get_game_coords()[2]
        if self._fallback_to_nearest_path(cur_map):
            print(f"Navigator: Successfully loaded nearest quest path (quest {self.active_quest_id})")
            setattr(self.env, "current_loaded_quest_id", self.active_quest_id)
            return True
            
        print("Navigator: Failed to find any nearby quest path")
        return False

    # ...............................................................
    #  S N A P
    # ...............................................................
    def snap_to_nearest_coordinate(self) -> bool:
        """FIXED: Enhanced coordinate snapping with map bounds filtering"""
        print(f"[SNAP_DEBUG] snap_to_nearest_coordinate() called")
        print(f"[SNAP_DEBUG] Current coordinates loaded: {len(self.sequential_coordinates)}")
        print(f"[SNAP_DEBUG] Current quest ID: {self.active_quest_id}")
        
        if not self.sequential_coordinates:
            print("Navigator: snap_to_nearest_coordinate: No coordinates loaded, attempting to load quest...")
            # FIXED: Use proper quest loading instead of old segmented files
            if not self._ensure_quest_loaded():
                print(f"Navigator: snap_to_nearest_coordinate: Failed to load any quest")
                return False

        try:
            # Get current player position and map
            local_x, local_y, cur_map = self.env.get_game_coords()
            cur_pos = self._get_player_global_coords()
            if not cur_pos:
                print("Navigator: snap_to_nearest_coordinate: Cannot get player position")
                return False

            print(f"[SNAP_DEBUG] Player at {cur_pos} on map {cur_map} (local {local_x}, {local_y})")
            print(f"[SNAP_DEBUG] First 5 coordinates in path: {self.sequential_coordinates[:5]}")
            print(f"[SNAP_DEBUG] Using placeholder map IDs: {self.using_placeholder_map_ids}")

            # FIXED: Filter coordinates by current map bounds using MAP_DATA
            valid_indices = []
            
            if self.using_placeholder_map_ids:
                # Import MAP_DATA (already available in environment)
                from environment.data.recorder_data.global_map import MAP_DATA
                
                if cur_map in MAP_DATA:
                    # Get map bounds from map_data.json
                    map_x, map_y = MAP_DATA[cur_map]["coordinates"]  # Top-left corner (x, y)
                    tile_width, tile_height = MAP_DATA[cur_map]["tileSize"]  # (width, height)
                    
                    # Calculate global bounds for this map
                    # Remember: local_to_global returns (global_y, global_x)
                    top_left_global = local_to_global(0, 0, cur_map)  # Returns (gy, gx)
                    bottom_right_global = local_to_global(tile_height-1, tile_width-1, cur_map)
                    
                    print(f"[SNAP_DEBUG] Map {cur_map} bounds: top_left={top_left_global}, bottom_right={bottom_right_global}")
                    
                    # Filter coordinates that fall within this map's bounds
                    for i, coord in enumerate(self.sequential_coordinates):
                        coord_gy, coord_gx = coord  # coordinate is (global_y, global_x)
                        if (top_left_global[0] <= coord_gy <= bottom_right_global[0] and 
                            top_left_global[1] <= coord_gx <= bottom_right_global[1]):
                            valid_indices.append(i)
                    
                    print(f"[SNAP_DEBUG] Found {len(valid_indices)} coordinates on current map {cur_map}")
                    if valid_indices:
                        print(f"[SNAP_DEBUG] Valid coordinates on map {cur_map}: {[self.sequential_coordinates[i] for i in valid_indices[:5]]}...")
                else:
                    print(f"[SNAP_DEBUG] Map {cur_map} not found in MAP_DATA, using all coordinates")
                    valid_indices = list(range(len(self.sequential_coordinates)))
            else:
                # Use map IDs from coordinate data (non-placeholder case)
                valid_indices = [i for i, map_id in enumerate(self.coord_map_ids) if map_id == cur_map]
                print(f"[SNAP_DEBUG] Found {len(valid_indices)} coordinates with matching map ID {cur_map}")
                if valid_indices:
                    print(f"[SNAP_DEBUG] Coordinates with map ID {cur_map}: {[(i, self.sequential_coordinates[i]) for i in valid_indices[:5]]}...")

            if valid_indices:
                # Find nearest coordinate from valid indices only
                distances = [self._manhattan(cur_pos, self.sequential_coordinates[i]) for i in valid_indices]
                nearest_local_i = distances.index(min(distances))
                nearest_i = valid_indices[nearest_local_i]
                dist = distances[nearest_local_i]
                
                print(f"[SNAP_DEBUG] Nearest coordinate: index {nearest_i}, coord {self.sequential_coordinates[nearest_i]}, distance {dist}")
            else:
                # Fallback to all coordinates if no map-specific coordinates found
                print(f"[SNAP_DEBUG] No coordinates found on map {cur_map}, using nearest overall")
                distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
                nearest_i = distances.index(min(distances))
                dist = distances[nearest_i]
                print(f"[SNAP_DEBUG] Nearest overall coordinate: index {nearest_i}, coord {self.sequential_coordinates[nearest_i]}, distance {dist}")
                
            # Only snap if distance is reasonable
            if dist <= 13:
                old_index = self.current_coordinate_index
                self.current_coordinate_index = nearest_i
                print(f"[SNAP_DEBUG] ✅ Snapped from index {old_index} to {nearest_i}, coord {self.sequential_coordinates[nearest_i]}, distance {dist}")
                
                # FIXED: Enhanced state reset after snapping
                self.movement_failure_count = 0
                if self.current_coordinate_index >= len(self.sequential_coordinates):
                    self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)
                    print("[SNAP_DEBUG] Adjusted coordinate index to stay within bounds")

                print("[SNAP_DEBUG] snap_to_nearest_coordinate: SNAP COMPLETE")
                return True
            else:
                print(f"[SNAP_DEBUG] ❌ Distance too large ({dist}), not snapping")
                return False
                
        except Exception as e:
            print(f"[SNAP_DEBUG] ❌ Error in snap_to_nearest_coordinate: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _emergency_snap_to_path(self) -> bool:
        """Emergency fallback snapping with extended distance threshold for when regular snapping fails"""
        print(f"Navigator: _emergency_snap_to_path: Attempting emergency coordinate snapping...")
        
        if not self.sequential_coordinates:
            print(f"Navigator: _emergency_snap_to_path: No coordinates loaded")
            return False

        try:
            cur_pos = self._get_player_global_coords()
            if not cur_pos:
                print("Navigator: _emergency_snap_to_path: Cannot get player position")
                return False

            print(f"Navigator: _emergency_snap_to_path: Player at {cur_pos}")

            # Find the absolutely nearest coordinate regardless of map bounds or distance
            distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
            nearest_i = distances.index(min(distances))
            nearest_dist = distances[nearest_i]
            nearest_coord = self.sequential_coordinates[nearest_i]
            
            print(f"Navigator: _emergency_snap_to_path: Nearest coordinate is index {nearest_i}, coord {nearest_coord}, distance {nearest_dist}")
            
            # Use a much larger threshold for emergency snapping
            emergency_threshold = 50  # Allow much larger distances
            
            if nearest_dist <= emergency_threshold:
                old_index = self.current_coordinate_index
                self.current_coordinate_index = nearest_i
                print(f"Navigator: _emergency_snap_to_path: Emergency snapped from index {old_index} to {nearest_i}")
                
                # Reset state
                self.movement_failure_count = 0
                if self.current_coordinate_index >= len(self.sequential_coordinates):
                    self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)
                    print("Navigator: _emergency_snap_to_path: Adjusted coordinate index to stay within bounds")

                print("Navigator: _emergency_snap_to_path: EMERGENCY SNAP COMPLETE")
                return True
            else:
                print(f"Navigator: _emergency_snap_to_path: Even emergency distance too large ({nearest_dist}), cannot snap")
                return False
                
        except Exception as e:
            print(f"Navigator: Error in _emergency_snap_to_path: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ...............................................................
    #  W A R P   H A N D L E R
    # ...............................................................
    def warp_tile_handler(self) -> bool:
        print(f"\n=== WARP_TILE_HANDLER START ===")
        
        # ENHANCED: Check for warp instability first
        if hasattr(self.env, 'is_warping') and self.env.is_warping:
            print("warp_tile_handler: DIAGNOSTIC - Warp transition in progress - skipping warp handling")
            print(f"warp_tile_handler: DIAGNOSTIC - env.is_warping = {self.env.is_warping}")
            return False
            
        # Debug: initial warp handler state
        # print(f"warp_tile_handler: current_coordinate_index={self.current_coordinate_index}, coord_map_ids snippet={self.coord_map_ids[self.current_coordinate_index:self.current_coordinate_index+3] if self.coord_map_ids else []}")

        if (time.time() - self.last_warp_time) < self.WARP_COOLDOWN_SECONDS:
            print(f"warp_tile_handler: DIAGNOSTIC - COOLDOWN - {time.time() - self.last_warp_time:.2f}s < {self.WARP_COOLDOWN_SECONDS}s")
            return False

        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            cur_global_pos = self._get_player_global_coords() # Get current global position
            if cur_global_pos is None:
                print("warp_tile_handler: DIAGNOSTIC - Cannot get current player global position.")
                return False
            local_pos = (local_x, local_y)
            print(f"warp_tile_handler: DIAGNOSTIC - Player at local {local_pos}, global {cur_global_pos}, map {cur_map}")
        except Exception as e:
            print(f"Navigator: DIAGNOSTIC - Could not get game coords in warp_tile_handler: {e}")
            return False

        warp_entries_for_cur_map = WARP_DICT.get(MapIds(cur_map).name, [])
        if not warp_entries_for_cur_map:
            print(f"warp_tile_handler: DIAGNOSTIC - No warp tiles defined for current map {cur_map} ({MapIds(cur_map).name})")
            return False

        print(f"warp_tile_handler: DIAGNOSTIC - Found {len(warp_entries_for_cur_map)} warp entries for map {cur_map}")
        for i, entry in enumerate(warp_entries_for_cur_map):
            warp_x, warp_y = entry.get("x"), entry.get("y")
            target_map_id = entry.get("target_map_id")
            print(f"warp_tile_handler: DIAGNOSTIC - Warp {i}: ({warp_x}, {warp_y}) -> Map {target_map_id}")

        # Find the specific warp entry the player is on/next to
        # A player is "next to" a warp if their local coords are 1 Manhattan distance away
        # A player is "on" a warp if their local coords match a warp tile (for single tile warps)
        
        nearest_warp_tile_local = None
        warp_distance = None
        for entry in warp_entries_for_cur_map:
            warp_x, warp_y = entry.get("x"), entry.get("y")
            if warp_x is not None and warp_y is not None:
                distance = self._manhattan(local_pos, (warp_x, warp_y))
                print(f"warp_tile_handler: DIAGNOSTIC - Distance to warp ({warp_x}, {warp_y}): {distance}")
                if distance == 1: # Player is adjacent
                    nearest_warp_tile_local = (warp_x, warp_y)
                    warp_distance = distance
                    print(f"warp_tile_handler: DIAGNOSTIC - Player ADJACENT to warp tile {nearest_warp_tile_local}")
                    break
                elif distance == 0: # Player is ON the warp tile
                    nearest_warp_tile_local = (warp_x, warp_y)
                    warp_distance = distance
                    print(f"warp_tile_handler: DIAGNOSTIC - Player ON warp tile {nearest_warp_tile_local}")
                    break
                # For some configurations, player might be directly on the warp tile if it's a single-tile trigger
                # This needs careful handling as most warps trigger on step *into* them.
                # However, if the game logic places the player *on* a tile that immediately warps,
                # this might be relevant. For now, standard is stepping *towards* an adjacent warp.

        if not nearest_warp_tile_local:
            print(f"warp_tile_handler: DIAGNOSTIC - Not adjacent to or on any warp tile. Local pos: {local_pos}")
            for entry in warp_entries_for_cur_map:
                warp_x, warp_y = entry.get("x"), entry.get("y")
                if warp_x is not None and warp_y is not None:
                    distance = self._manhattan(local_pos, (warp_x, warp_y))
                    print(f"warp_tile_handler: DIAGNOSTIC - Distance to warp ({warp_x}, {warp_y}): {distance}")
            return False
        
        active_warp_entry = next((e for e in warp_entries_for_cur_map if (e.get("x"), e.get("y")) == nearest_warp_tile_local), None)
        if not active_warp_entry:
            print(f"warp_tile_handler: DIAGNOSTIC - Could not find active_warp_entry for tile {nearest_warp_tile_local} on map {cur_map}")
            return False
            
        target_map_id = active_warp_entry.get("target_map_id")
        target_map_name = active_warp_entry.get("target_map_name", "")
        
        print(f"warp_tile_handler: DIAGNOSTIC - Active warp entry: {active_warp_entry}")
        print(f"warp_tile_handler: DIAGNOSTIC - Target map ID: {target_map_id}, Target map name: {target_map_name}")
        
        if target_map_id is None:
            print(f"warp_tile_handler: DIAGNOSTIC - Warp entry {active_warp_entry} has no target_map_id.")
            return False

        # FIXED: Handle LAST_MAP special case (target_map_id = 255)
        original_target_map_id = target_map_id
        if target_map_id == 255 and target_map_name == "LAST_MAP":
            # Resolve to the previous map ID using environment's centralized map tracking
            prev_map_id = None
            if hasattr(self.env, 'map_history') and len(self.env.map_history) >= 2:
                prev_map_id = self.env.map_history[-2]
            
            if prev_map_id is not None and prev_map_id != cur_map:
                target_map_id = prev_map_id
                print(f"warp_tile_handler: DIAGNOSTIC - Resolved LAST_MAP warp from {cur_map} to {target_map_id}")
            else:
                # Fallback: assume came from Pallet Town if in Oak's Lab
                if cur_map == 40:  # Oak's Lab
                    target_map_id = 0  # Pallet Town
                    print(f"warp_tile_handler: DIAGNOSTIC - Fallback - Oak's Lab warp to Pallet Town (map 0)")
                else:
                    print(f"warp_tile_handler: DIAGNOSTIC - Warning - Cannot resolve LAST_MAP for warp on map {cur_map}")
                    target_map_id = 0  # Default fallback

        print(f"warp_tile_handler: DIAGNOSTIC - Final target map ID: {target_map_id}")
        print(f"warp_tile_handler: DIAGNOSTIC - Player at {local_pos} (global {cur_global_pos}) on map {cur_map}, warp distance {warp_distance} to warp {nearest_warp_tile_local}, targeting map {target_map_id}")
        
        # CRITICAL: Check if this warp is aligned with the current quest path
        if self.sequential_coordinates and self.coord_map_ids:
            warp_info = {
                "target_map_id": target_map_id,
                "local_coords": nearest_warp_tile_local
            }
            
            is_path_aligned = self.is_warp_aligned_with_path(warp_info)
            print(f"warp_tile_handler: DIAGNOSTIC - Warp is {'ALIGNED' if is_path_aligned else 'NOT ALIGNED'} with quest path")
            
            # Only execute warps that are aligned with the quest path or if we're at the end of the path
            at_end_of_path = (self.current_coordinate_index >= len(self.sequential_coordinates) - 1)
            should_execute_warp = is_path_aligned or at_end_of_path
            
            if not should_execute_warp:
                print(f"warp_tile_handler: DIAGNOSTIC - Skipping warp - not aligned with path and not at end of quest")
                return False
        else:
            print(f"warp_tile_handler: DIAGNOSTIC - No path loaded, allowing warp execution")
        
        # EXECUTE THE WARP: Move toward the warp tile to trigger it
        print(f"warp_tile_handler: EXECUTION - About to execute warp to map {target_map_id}")
        
        # Calculate direction to move toward warp tile
        dx = nearest_warp_tile_local[0] - local_pos[0]  # x difference
        dy = nearest_warp_tile_local[1] - local_pos[1]  # y difference
        
        print(f"warp_tile_handler: EXECUTION - Delta to warp tile: dx={dx}, dy={dy}")
        
        # FIXED: Determine movement action with special handling for door warps
        movement_action = None
        
        if warp_distance == 0:
            # Already on warp tile - for door warps, always try DOWN to activate
            print(f"warp_tile_handler: EXECUTION - Player on warp tile, using DOWN to activate door warp")
            movement_action = 0  # DOWN
            direction_name = "DOWN"
        else:
            # Player adjacent to warp tile - prioritize getting TO the warp tile first
            # But for Oak's Lab door warps, we need to approach from a specific direction
            
            # SPECIAL CASE: Oak's Lab door warps (map 40) work best with DOWN movement
            if cur_map == 40:  # Oak's Lab
                print(f"warp_tile_handler: EXECUTION - Oak's Lab detected, prioritizing DOWN movement for door warp")
                # Try to get to the warp tile first, then activate with DOWN
                if dy > 0:
                    movement_action = 0  # DOWN
                    direction_name = "DOWN"
                elif dy < 0:
                    movement_action = 3  # UP  
                    direction_name = "UP"
                elif dx > 0:
                    movement_action = 2  # RIGHT
                    direction_name = "RIGHT"
                elif dx < 0:
                    movement_action = 1  # LEFT
                    direction_name = "LEFT"
                else:
                    # Default to DOWN for door activation
                    movement_action = 0  # DOWN
                    direction_name = "DOWN"
            else:
                # General case for other maps
                if abs(dy) > abs(dx):
                    if dy > 0:
                        movement_action = 0  # DOWN
                        direction_name = "DOWN"
                    else:
                        movement_action = 3  # UP
                        direction_name = "UP"
                elif dx != 0:
                    if dx > 0:
                        movement_action = 2  # RIGHT
                        direction_name = "RIGHT"
                    else:
                        movement_action = 1  # LEFT
                        direction_name = "LEFT"
                else:
                    # Should not happen, but fallback
                    movement_action = 0  # DOWN
                    direction_name = "DOWN"
        
        print(f"warp_tile_handler: EXECUTION - Executing movement {direction_name} (action {movement_action}) to trigger warp")
        
        # Store pre-warp state for tracking
        pre_warp_map = cur_map
        pre_warp_pos = cur_global_pos
        
        # Execute the movement to trigger the warp (bypass collision for warp activation)
        moved = self._execute_movement(movement_action, bypass_collision=True)
        
        if not moved:
            print(f"warp_tile_handler: EXECUTION - Movement failed, cannot trigger warp")
            return False
        
        print(f"warp_tile_handler: EXECUTION - Movement executed, checking for warp completion...")
        
        # Allow some time for warp to complete
        time.sleep(0.1)  # Small delay to allow warp transition
        
        # Check if warp actually occurred by comparing map IDs
        try:
            new_local_x, new_local_y, new_map = self.env.get_game_coords()
            new_global_pos = self._get_player_global_coords()
            
            print(f"warp_tile_handler: EXECUTION - Post-movement: map {new_map}, pos {new_global_pos}")
            
            warp_occurred = (new_map != pre_warp_map)
            
            # ENHANCED: If warp didn't trigger and we're on a warp tile, try DOWN movement for door activation
            if not warp_occurred and new_map == pre_warp_map:
                new_local_pos = (new_local_x, new_local_y)
                # Check if we're now ON a warp tile
                on_warp_tile = any(
                    self._manhattan(new_local_pos, (entry.get("x"), entry.get("y"))) == 0
                    for entry in warp_entries_for_cur_map
                    if entry.get("x") is not None and entry.get("y") is not None
                )
                
                if on_warp_tile and cur_map == 40:  # Oak's Lab special handling
                    print(f"warp_tile_handler: RETRY - Player on warp tile but warp didn't trigger, trying multiple activation methods")
                    
                    # Try multiple directions to activate the warp, bypassing collision detection for warp activation
                    activation_directions = [3, 0, 1, 2]  # UP, DOWN, LEFT, RIGHT
                    direction_names = ["UP", "DOWN", "LEFT", "RIGHT"]
                    
                    for i, direction in enumerate(activation_directions):
                        direction_name = direction_names[i]
                        print(f"warp_tile_handler: RETRY - Trying {direction_name} activation (bypassing collision)")
                        
                        # Bypass collision detection for warp activation by calling environment directly
                        try:
                            print(f"warp_tile_handler: RETRY - Executing {direction_name} movement directly on emulator")
                            obs, reward, done, truncated, info = self.env.process_action(direction, source="Navigator-WarpRetry")
                            time.sleep(0.1)
                            
                            # Check if warp triggered
                            retry_local_x, retry_local_y, retry_map = self.env.get_game_coords()
                            retry_global_pos = self._get_player_global_coords()
                            
                            print(f"warp_tile_handler: RETRY - After {direction_name}: map {retry_map}, pos {retry_global_pos}")
                            warp_occurred = (retry_map != pre_warp_map)
                            
                            if warp_occurred:
                                # Update the "new" values for the success path below
                                new_local_x, new_local_y, new_map = retry_local_x, retry_local_y, retry_map
                                new_global_pos = retry_global_pos
                                print(f"warp_tile_handler: RETRY - SUCCESS! Warp activated with {direction_name} movement")
                                break
                            else:
                                print(f"warp_tile_handler: RETRY - {direction_name} movement didn't trigger warp, trying next direction")
                                
                        except Exception as e:
                            print(f"warp_tile_handler: RETRY - {direction_name} movement failed with error: {e}")
                            continue
                    
                    if not warp_occurred:
                        print(f"warp_tile_handler: RETRY - All activation directions failed")
            
            if warp_occurred:
                print(f"warp_tile_handler: SUCCESS - Warp completed! Moved from map {pre_warp_map} to map {new_map}")
                
                # Update warp tracking state
                self.last_warp_time = time.time()
                self._last_warp_origin = pre_warp_map
                self._last_warp_target = new_map
                self._warp_delay_timer = time.time() + self._post_warp_delay
                
                # Environment state is automatically updated through map_history in environment
                
                # CRITICAL: Advance navigator coordinate index to continue path on destination map
                if self.sequential_coordinates and self.coord_map_ids:
                    print(f"warp_tile_handler: PATH_ADVANCE - Looking for coordinates on destination map {new_map}")
                    
                    # Find the next coordinate that's on the destination map
                    old_index = self.current_coordinate_index
                    found_destination_coord = False
                    
                    # Look ahead in the path for coordinates on the destination map
                    search_limit = min(len(self.sequential_coordinates), self.current_coordinate_index + 50)
                    for next_idx in range(self.current_coordinate_index, search_limit):
                        if next_idx < len(self.coord_map_ids):
                            if self.using_placeholder_map_ids:
                                # Use MAP_DATA to check if coordinate is on destination map
                                coord = self.sequential_coordinates[next_idx]
                                coord_map = self._determine_map_id_for_coordinate(coord[0], coord[1])
                                if coord_map == new_map:
                                    self.current_coordinate_index = next_idx
                                    found_destination_coord = True
                                    print(f"warp_tile_handler: PATH_ADVANCE - Advanced from index {old_index} to {next_idx} for destination map {new_map}")
                                    break
                            else:
                                # Use actual map IDs
                                if self.coord_map_ids[next_idx] == new_map:
                                    self.current_coordinate_index = next_idx
                                    found_destination_coord = True
                                    print(f"warp_tile_handler: PATH_ADVANCE - Advanced from index {old_index} to {next_idx} for destination map {new_map}")
                                    break
                    
                    if not found_destination_coord:
                        print(f"warp_tile_handler: PATH_ADVANCE - No coordinates found on destination map {new_map}, using snap-to-nearest")
                        # Try to snap to the nearest coordinate on the new map
                        if self.snap_to_nearest_coordinate():
                            print(f"warp_tile_handler: PATH_ADVANCE - Successfully snapped to coordinate index {self.current_coordinate_index}")
                        else:
                            print(f"warp_tile_handler: PATH_ADVANCE - Snap failed, advancing index by 1")
                            self.current_coordinate_index += self._direction
                    
                    # Ensure index stays within bounds
                    if self.current_coordinate_index >= len(self.sequential_coordinates):
                        self.current_coordinate_index = len(self.sequential_coordinates) - 1
                        print(f"warp_tile_handler: PATH_ADVANCE - Adjusted index to stay within bounds: {self.current_coordinate_index}")
                    elif self.current_coordinate_index < 0:
                        self.current_coordinate_index = 0
                        print(f"warp_tile_handler: PATH_ADVANCE - Adjusted index to stay within bounds: {self.current_coordinate_index}")
                
                print(f"warp_tile_handler: SUCCESS - Warp execution and path advancement complete")
                print(f"=== WARP_TILE_HANDLER END (SUCCESS) ===\n")
                return True
                
            else:
                print(f"warp_tile_handler: EXECUTION - Movement completed but no warp detected (still on map {new_map})")
                # This could happen if the movement didn't actually reach the warp tile
                # or if the warp requires additional steps
                return False
                
        except Exception as e:
            print(f"warp_tile_handler: ERROR - Exception while checking warp completion: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        print(f"=== WARP_TILE_HANDLER END ===\n")
        return False

    def manual_warp_trigger(self) -> bool:
        """Manually trigger a warp when standing next to a warp tile (for testing)"""
        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            local = (local_x, local_y)
        except Exception:
            print("Navigator: Could not get game coords for manual warp.")
            return False

        warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
        warp_tiles = [(e["x"], e["y"]) for e in warp_entries if e.get("x") is not None]
        if not warp_tiles:
            print("Navigator: No warp tiles on current map.")
            return False

        nearest_warp = next((wt for wt in warp_tiles if self._manhattan(local, wt) == 1), None)
        if not nearest_warp:
            print("Navigator: Not standing next to a warp tile.")
            return False

        # Temporarily disable restrictions for manual warp
        original_coord_map_ids = self.coord_map_ids
        original_coordinates = self.sequential_coordinates
        self.coord_map_ids = []
        self.sequential_coordinates = []
        
        # Trigger warp
        result = self.warp_tile_handler()
        
        # Restore original state
        self.coord_map_ids = original_coord_map_ids
        self.sequential_coordinates = original_coordinates
        
        return result

    # ...............................................................
    #  R O A M   I N   G R A S S  (Quest 23)
    # ...............................................................
    def roam_in_grass(self) -> bool:
        direction_str = random.choice(list(self.ACTION_MAPPING_STR_TO_INT.keys()))
        action = self.ACTION_MAPPING_STR_TO_INT[direction_str]
        moved = self._execute_movement(action)
        if moved:
            print(f"Navigator: Roaming in grass: moved {direction_str}")
        else:
            print(f"Navigator: Roaming in grass: movement {direction_str} failed")
        return True

    # ...............................................................
    #  L O C A L   C O O R D S
    # ...............................................................
    def get_current_local_coords(self):
        return (
            self.env.get_game_coords()[0],
            self.env.get_game_coords()[1],
            self.env.get_game_coords()[2],
        )

    # ...............................................................
    #  M O V E   T O   N E X T   C O O R D I N A T E
    # ...............................................................
    def move_to_next_coordinate(self) -> bool:
        """Enhanced coordinate navigation with intelligent direction and comprehensive logging"""
        if not self._ensure_quest_loaded():
            if self.logger:
                self.logger.log_error("NAVIGATION", "No quest loaded for coordinate navigation", {
                    'active_quest_id': self.active_quest_id,
                    'coordinates_count': len(self.sequential_coordinates)
                })
            return False
            
        # INTELLIGENT: Ensure direction is set intelligently before any movement
        print(f"Navigator: DIAGNOSTIC - About to ensure intelligent direction...")
        print(f"Navigator: DIAGNOSTIC - Current direction before intelligence: {self._direction}")
        self._ensure_intelligent_direction()
        print(f"Navigator: DIAGNOSTIC - Current direction after intelligence: {self._direction}")
        
        # DIAGNOSTIC: Log detailed path information every step
        self._log_next_coordinates_detailed()
        
        # ENHANCED: Load quest if needed
        env_qid = getattr(self.env, "current_loaded_quest_id", None)
        if env_qid is not None and self.active_quest_id != env_qid:
            print(f"Navigator: DIAGNOSTIC - Quest ID changed from {self.active_quest_id} to {env_qid}, loading new quest")
            if not self.load_coordinate_path(env_qid):
                print(f"Navigator: DIAGNOSTIC - Failed to load quest {env_qid}")
                return False
        
        # If quest ID changed, reset state
        if hasattr(self, "_last_loaded_quest_id") and self._last_loaded_quest_id != env_qid:
            self._reset_state()

        # Resume original quest after fallback when re-entering its map
        if self._fallback_mode and self._original_quest_id is not None:
            cur_map = self.env.get_game_coords()[2]
            base = Path(__file__).parent / "quest_paths"
            orig_id = self._original_quest_id
            file_path = base / f"{orig_id:03d}" / f"{orig_id:03d}_coords.json"
            try:
                data = json.loads(file_path.read_text())
                if str(cur_map) in data:
                    self.load_coordinate_path(orig_id)
                    self._fallback_mode = False
                    self._direction = 1
                    print(f"Navigator: DIAGNOSTIC - Fallback complete, resumed quest {orig_id:03d}")
            except Exception:
                pass

        self.navigation_status = "navigating"

        # Get current position for navigation
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            print("Navigator: Cannot get current position for navigation")
            self.navigation_status = "error"
            return False

        # Check if we've reached the end of the path
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            print("Navigator: Reached end of coordinate path")
            self.navigation_status = "completed"
            if self.logger:
                self.logger.log_navigation_event("PATH_COMPLETED", {
                    'message': 'Reached end of coordinate path',
                    'quest_id': self.active_quest_id,
                    'final_index': self.current_coordinate_index,
                    'total_coordinates': len(self.sequential_coordinates),
                    'current_position': cur_pos
                })
            return False

        target_coord = self.sequential_coordinates[self.current_coordinate_index]
        target_map = self._safe_coord_map_id(self.current_coordinate_index)

        # Log navigation attempt
        if self.logger:
            self.logger.log_navigation_event("COORDINATE_NAVIGATION", {
                'message': f'Navigating to coordinate {target_coord}',
                'current_position': cur_pos,
                'target_coordinate': target_coord,
                'target_map_id': target_map,
                'coordinate_index': self.current_coordinate_index,
                'total_coordinates': len(self.sequential_coordinates),
                'quest_id': self.active_quest_id,
                'direction': self._direction
            })

        # Check if we're already at the target
        if cur_pos == target_coord:
            print(f"Navigator: Already at target coordinate {target_coord}, advancing to next")
            self.current_coordinate_index += self._direction
            if self.logger:
                self.logger.log_navigation_event("COORDINATE_REACHED", {
                    'message': f'Reached target coordinate {target_coord}',
                    'coordinate': target_coord,
                    'quest_id': self.active_quest_id,
                    'new_index': self.current_coordinate_index
                })
            return True

        # Calculate movement direction
        dy = target_coord[0] - cur_pos[0]
        dx = target_coord[1] - cur_pos[1]
        distance = abs(dy) + abs(dx)

        print(f"Navigator: Current: {cur_pos}, Target: {target_coord}, Distance: {distance}")

        # Determine action
        if abs(dy) > abs(dx):
            action = 0 if dy > 0 else 3  # down or up
            direction_name = "down" if dy > 0 else "up"
        else:
            action = 2 if dx > 0 else 1  # right or left
            direction_name = "right" if dx > 0 else "left"

        print(f"Navigator: Moving {direction_name} (action {action}) toward {target_coord}")

        # FIXED: Use environment's action execution instead of direct step
        try:
            obs, reward, done, truncated, info = self.env.process_action(action, source="Navigator-Coordinate")
            
            # Log movement execution
            if self.logger:
                self.logger.log_navigation_event("MOVEMENT_EXECUTED", {
                    'message': f'Executed movement {direction_name}',
                    'action': action,
                    'direction': direction_name,
                    'target_coordinate': target_coord,
                    'quest_id': self.active_quest_id,
                    'step_info': info
                })
            
            # Check if we reached the target after movement
            new_pos = self._get_player_global_coords()
            if new_pos == target_coord:
                print(f"Navigator: Successfully reached target coordinate {target_coord}")
                self.current_coordinate_index += self._direction
                self.movement_failure_count = 0
                
                if self.logger:
                    self.logger.log_navigation_event("COORDINATE_REACHED", {
                        'message': f'Successfully reached target coordinate {target_coord}',
                        'coordinate': target_coord,
                        'quest_id': self.active_quest_id,
                        'new_index': self.current_coordinate_index,
                        'movements_taken': 1
                    })
                return True
            else:
                print(f"Navigator: Movement executed but not at target. New position: {new_pos}")
                self.movement_failure_count += 1
                return True

        except Exception as e:
            print(f"Navigator: Error during movement: {e}")
            if self.logger:
                self.logger.log_error("NAVIGATION", f"Error during movement execution: {e}", {
                    'action': action,
                    'direction': direction_name,
                    'target_coordinate': target_coord,
                    'quest_id': self.active_quest_id
                })
            self.movement_failure_count += 1
            return False

    def _diagnose_warp_instability(self) -> None:
        """DIAGNOSTIC: Analyze why env.is_warping is constantly True"""
        print(f"\n=== WARP INSTABILITY DIAGNOSIS ===")
        
        # Check environment attributes related to warping
        warp_attrs = ['is_warping', '_warping', 'warping', 'warp_state', '_warp_state']
        for attr in warp_attrs:
            if hasattr(self.env, attr):
                value = getattr(self.env, attr)
                print(f"WARP DIAG: env.{attr} = {value} (type: {type(value)})")
        
        # Check if there's a warp timer or cooldown
        timer_attrs = ['warp_timer', '_warp_timer', 'last_warp_time', '_last_warp_time']
        for attr in timer_attrs:
            if hasattr(self.env, attr):
                value = getattr(self.env, attr)
                print(f"WARP DIAG: env.{attr} = {value}")
        
        # Check environment step or update methods for warp logic
        if hasattr(self.env, 'update_warping_status'):
            print(f"WARP DIAG: Environment has update_warping_status method")
        if hasattr(self.env, '_check_for_warp'):
            print(f"WARP DIAG: Environment has _check_for_warp method")
        
        # ENHANCED: Direct memory inspection
        print(f"\nWARP DIAG: === DIRECT MEMORY INSPECTION ===")
        try:
            # Check key memory addresses directly
            wd736 = self.env.read_m(0xD736)
            print(f"WARP DIAG: wd736 (0xD736) = 0x{wd736:02X} ({wd736}) - bit 2 = {bool(wd736 & 0b00000100)}")
            
            warp_pad = self.env.read_m(0xD7EB)
            print(f"WARP DIAG: wStandingOnWarpPadOrHole (0xD7EB) = {warp_pad}")
            
            cur_map = self.env.read_m("wCurMap")
            print(f"WARP DIAG: wCurMap = {cur_map}")
            
            h_newmap = self.env.read_m(0xFF8B)
            print(f"WARP DIAG: H_NEWMAP (0xFF8B) = {h_newmap}")
            
            # Check warp entries
            n_warps = self.env.read_m(0xD3AE)
            print(f"WARP DIAG: wNumberOfWarps (0xD3AE) = {n_warps}")
            
            if n_warps > 0:
                print(f"WARP DIAG: Warp entries (first 3):")
                warp_entries_addr = 0xD3B1
                for i in range(min(n_warps, 3)):
                    warp_addr = warp_entries_addr + i * 4
                    warp_y = self.env.read_m(warp_addr + 0)
                    warp_x = self.env.read_m(warp_addr + 1)
                    warp_point = self.env.read_m(warp_addr + 2)
                    warp_dest = self.env.read_m(warp_addr + 3)
                    print(f"WARP DIAG:   Warp {i}: ({warp_x}, {warp_y}) -> point {warp_point}, map {warp_dest}")
            
        except Exception as e:
            print(f"WARP DIAG: Error reading memory: {e}")
        
        # Get current position and check for warp tiles
        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            print(f"WARP DIAG: Current position: local=({local_x},{local_y}), map={cur_map}")

            warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
            print(f"WARP DIAG: WARP_DICT entries for map {cur_map} ({MapIds(cur_map).name}): {len(warp_entries)}")
            for i, entry in enumerate(warp_entries[:3]):  # Show first 3
                warp_x, warp_y = entry.get("x"), entry.get("y")
                target_map = entry.get("target_map_id")
                print(f"WARP DIAG:   Dict warp {i}: ({warp_x}, {warp_y}) -> map {target_map}")
                if warp_x == local_x and warp_y == local_y:
                    print(f"WARP DIAG: Player is standing ON warp tile ({warp_x}, {warp_y}) -> Map {target_map}")
                elif self._manhattan((local_x, local_y), (warp_x, warp_y)) == 1:
                    print(f"WARP DIAG: Player is ADJACENT to warp tile ({warp_x}, {warp_y}) -> Map {target_map}")
                    
        except Exception as e:
            print(f"WARP DIAG: Error checking position: {e}")
        
        # Check for recent warp activity
        current_time = time.time()
        if hasattr(self, 'last_warp_time'):
            time_since_warp = current_time - self.last_warp_time
            print(f"WARP DIAG: Time since last warp: {time_since_warp:.2f}s (cooldown: {self.WARP_COOLDOWN_SECONDS}s)")
        
        # Check if warp delay timer is active
        if hasattr(self, '_warp_delay_timer'):
            delay_remaining = self._warp_delay_timer - current_time
            print(f"WARP DIAG: Warp delay timer: {delay_remaining:.2f}s remaining")
        
        # ENHANCED: Check for any method that might be setting is_warping
        print(f"\nWARP DIAG: === CHECKING CALL STACK ===")
        import traceback
        print(f"WARP DIAG: Call stack when checking warp instability:")
        for line in traceback.format_stack()[-5:]:  # Show last 5 stack frames
            print(f"WARP DIAG: {line.strip()}")
        
        print(f"=== END WARP INSTABILITY DIAGNOSIS ===\n")

    # ...............................................................
    #  S T E P  T O W A R D S
    # ...............................................................
    def _step_towards(self, target: Tuple[int, int]) -> bool:
        print(f"\n=== _STEP_TOWARDS START ===")
        print(f"Action Planning: DIAGNOSTIC - Target: {target}")
        
        # ENHANCED: Check for warp instability before attempting movement
        if hasattr(self.env, 'is_warping') and self.env.is_warping:
            print("Action Planning: DIAGNOSTIC - Warp transition detected - allowing movement attempt")
            print(f"Action Planning: DIAGNOSTIC - env.is_warping = {self.env.is_warping}")
            # FIXED: Don't block movement - let the execution layer handle warp detection
            # return True  # Return success but don't move during warp
            
        cur = self._get_player_global_coords()
        if cur is None:
            print("Action Planning: DIAGNOSTIC - Cannot get current player position.")
            return False

        print(f"Action Planning: DIAGNOSTIC - Current pos: {cur}, Target coord: {target}")

        if cur == target:
            print(f"Action Planning: DIAGNOSTIC - Already at target {target}. Incrementing index.")
            self.current_coordinate_index += self._direction
            return True

        dy = target[0] - cur[0]
        dx = target[1] - cur[1]
        print(f"Action Planning: DIAGNOSTIC - Movement vector: dy={dy}, dx={dx}")
        
        moved = False
        
        # FIXED: Use intelligent direction selection with collision detection
        direction_str = self._select_intelligent_direction(dy, dx)
        
        if direction_str:
            action_int = self.ACTION_MAPPING_STR_TO_INT[direction_str]
            print(f"Action Planning: DIAGNOSTIC - Attempting intelligent direction: {direction_str} (action {action_int})")
            moved = self._execute_movement(action_int)
            print(f"Action Planning: DIAGNOSTIC - Direction {direction_str} result: {'SUCCESS' if moved else 'FAILED'}")
        else:
            print(f"Action Planning: DIAGNOSTIC - No walkable direction available for target {target}")
            
            # FALLBACK: Try to find any walkable direction to avoid being completely stuck
            walkable_directions = self._get_walkable_directions()
            if walkable_directions:
                fallback_direction = walkable_directions[0]
                action_int = self.ACTION_MAPPING_STR_TO_INT[fallback_direction]
                print(f"Action Planning: DIAGNOSTIC - Using fallback direction: {fallback_direction}")
                moved = self._execute_movement(action_int)
                print(f"Action Planning: DIAGNOSTIC - Fallback direction {fallback_direction} result: {'SUCCESS' if moved else 'FAILED'}")
            else:
                print(f"Action Planning: DIAGNOSTIC - No walkable directions available at all!")
        
        new_pos = self._get_player_global_coords()
        print(f"Action Planning: DIAGNOSTIC - Position after movement: {new_pos} (was {cur})")
        
        if moved and new_pos != cur:
            if new_pos == target:
                print(f"Action Planning: DIAGNOSTIC - Reached exact target! Incrementing index.")
                self.current_coordinate_index += self._direction
            else:
                print(f"Action Planning: DIAGNOSTIC - Moved towards target but not at exact position yet.")
            print(f"=== _STEP_TOWARDS END (SUCCESS) ===\n")
            return True

        # Skip deadlocked coordinate only after trying all directions
        if not moved:
            print(f"Action Planning: DIAGNOSTIC - All movement attempts failed! Skipping to next coordinate.")
            print(f"Action Planning: DIAGNOSTIC - Failed to move toward target {target} from {cur}. Skipping to next coordinate.")
            self.current_coordinate_index += self._direction
            self.movement_failure_count = 0
            
        print(f"=== _STEP_TOWARDS END (SKIP) ===\n")
        return True

    # ...............................................................
    #  E X E C U T E  M O V E M E N T
    # ...............................................................
    def _execute_movement(self, action: int, bypass_collision: bool = False) -> bool:
        """Execute a movement action in the environment with enhanced collision detection
        
        Args:
            action: Movement action (0=down, 1=left, 2=right, 3=up)
            bypass_collision: If True, skip collision checks (used for warp activation)
        """
        
        print(f"Navigator: _execute_movement called with action = {action}, bypass_collision = {bypass_collision}")
        print(f"Navigator: Current location: {self.env.get_game_coords()}")
        
        # BASIC SAFETY: Only block if actually in battle or dialog is active
        if self.env.read_m("wIsInBattle") != 0:
            print("Navigator: Movement blocked - in battle")
            return False
            
        dialog = self.env.read_dialog()
        if dialog and dialog.strip():
            print(f"Navigator: Movement blocked - dialog active: {dialog[:50]}...")
            return False
        
        # COLLISION CHECK: Skip collision detection if bypassing for warp activation
        if not bypass_collision:
            direction_map = {0: "down", 1: "left", 2: "right", 3: "up"}
            direction = direction_map.get(action)
            
            if direction and not self._is_direction_walkable(direction):
                print(f"Navigator: Movement blocked - direction {direction} not walkable")
                return False
        else:
            direction_map = {0: "down", 1: "left", 2: "right", 3: "up"}
            direction = direction_map.get(action)
            print(f"Navigator: BYPASSING collision check for {direction} (warp activation)")
        
        print(f"Navigator: Executing movement action {action} ({direction})")
        
        # Store position before movement
        pos_before = self.env.get_game_coords()
        
        # Execute the movement
        obs, reward, done, truncated, info = self.env.process_action(action, source="Navigator-Movement")
        
        # Check if position changed
        pos_after = self.env.get_game_coords()
        moved = pos_before != pos_after
        
        print(f"Navigator: Movement result - Before: {pos_before}, After: {pos_after}, Moved: {moved}")
        
        # ENHANCED: Track movement failures for intelligent recovery
        if not moved:
            self.movement_failure_count += 1
            print(f"Navigator: Movement failed (count: {self.movement_failure_count}/{self.max_movement_failures})")
        else:
            self.movement_failure_count = 0  # Reset on successful movement
        
        return moved

    # ...............................................................
    #  L O A D   Q U E S T   P A T H
    # ...............................................................
    def load_coordinate_path(self, quest_id: int) -> bool:
        """Load coordinate path for a given quest ID"""
        if quest_id is None:
            print(f"Navigator: Cannot load path for None quest_id")
            return False

        print(f"Navigator: Loading coordinate path for quest {quest_id:03d}")
        
        # Log quest path loading attempt
        if self.logger:
            self.logger.log_navigation_event("QUEST_PATH_LOADING", {
                'message': f'Attempting to load coordinate path for quest {quest_id}',
                'quest_id': quest_id,
                'previous_quest_id': self.active_quest_id
            })

        quest_dir_name = f"{quest_id:03d}"
        quest_file_name = f"{quest_dir_name}_coords.json"
        file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name

        if not file_path.exists():
            print(f"Navigator: Quest file does not exist: {file_path}")
            if self.logger:
                self.logger.log_error("NAVIGATION", f"Quest coordinate file not found: {file_path}", {
                    'quest_id': quest_id,
                    'file_path': str(file_path)
                })
            return False

        try:
            with open(file_path, 'r') as f:
                quest_data = json.load(f)
        except Exception as e:
            print(f"Navigator: Failed to parse JSON file {file_path}: {e}")
            if self.logger:
                self.logger.log_error("NAVIGATION", f"Failed to parse quest coordinate file: {e}", {
                    'quest_id': quest_id,
                    'file_path': str(file_path)
                })
            return False

        # Flatten all coordinates across all map segments
        all_coordinates = []
        map_ids = []
        
        # FIXED: Start with current map, then follow quest logical order
        def parse_segment_key(key):
            if '_' in key:
                # Format: "map_id_segment" like "38_0", "38_1"
                parts = key.split('_')
                return (int(parts[0]), int(parts[1]))
            else:
                # Format: just "map_id" like "38", "37", "0"
                return (int(key), 0)
        
        # Get current map to prioritize it first
        try:
            current_map = self.env.get_game_coords()[2]
            print(f"Navigator: Current map is {current_map}, prioritizing coordinates on this map first")
        except:
            current_map = None
            print(f"Navigator: Could not determine current map, using standard sorting")
        
        # CRITICAL FIX: Custom sorting to start with current map
        def quest_logical_sort(key):
            map_id, segment = parse_segment_key(key)
            
            # Priority 1: Current map goes first
            if current_map is not None and map_id == current_map:
                return (0, map_id, segment)  # Highest priority
            
            # Priority 2: Other maps in numerical order  
            return (1, map_id, segment)
        
        sorted_segments = sorted(quest_data.keys(), key=quest_logical_sort)
        
        print(f"Navigator: Coordinate loading order: {sorted_segments}")
        
        for segment_key in sorted_segments:
            segment_coords = quest_data[segment_key]
            
            # Extract map_id from key
            if '_' in segment_key:
                map_id = int(segment_key.split('_')[0])
            else:
                map_id = int(segment_key)
            
            for coord in segment_coords:
                all_coordinates.append((coord[0], coord[1]))
                map_ids.append(map_id)

        if not all_coordinates:
            print(f"Navigator: No coordinates found in quest {quest_id}")
            if self.logger:
                self.logger.log_error("NAVIGATION", f"No coordinates found in quest file", {
                    'quest_id': quest_id,
                    'segments': list(quest_data.keys())
                })
            return False

        # Update navigator state
        self.sequential_coordinates = all_coordinates
        self.coord_map_ids = map_ids
        self.current_coordinate_index = 0
        self.active_quest_id = quest_id
        self.using_placeholder_map_ids = True

        # Log successful loading
        if self.logger:
            self.logger.log_navigation_event("QUEST_PATH_LOADED", {
                'message': f'Successfully loaded coordinate path for quest {quest_id}',
                'quest_id': quest_id,
                'total_coordinates': len(all_coordinates),
                'unique_maps': len(set(map_ids)),
                'first_coordinate': all_coordinates[0] if all_coordinates else None,
                'first_map_id': map_ids[0] if map_ids else None
            })

        print(f"Navigator: Loaded {len(all_coordinates)} coordinates for quest {quest_id:03d}")
        print(f"Navigator: Quest uses {len(set(map_ids))} unique maps: {sorted(set(map_ids))}")
        
        self._log_next_coordinates_detailed()

        setattr(self.env, "current_loaded_quest_id", quest_id) # Sync with environment
        return True

    def _determine_map_id_for_coordinate(self, gy: int, gx: int) -> int:
        """Determine which map a global coordinate belongs to using MAP_DATA"""
        try:
            from environment.data.recorder_data.global_map import MAP_DATA
            
            for map_id, map_info in MAP_DATA.items():
                if map_id == -1:  # Skip Kanto overview
                    continue
                    
                # Get map bounds
                map_x, map_y = map_info["coordinates"]  # Top-left corner (x, y)
                tile_width, tile_height = map_info["tileSize"]  # (width, height)
                
                # Calculate global bounds for this map
                top_left_global = local_to_global(0, 0, map_id)  # Returns (gy, gx)
                bottom_right_global = local_to_global(tile_height-1, tile_width-1, map_id)
                
                # Check if coordinate falls within this map's bounds
                if (top_left_global[0] <= gy <= bottom_right_global[0] and 
                    top_left_global[1] <= gx <= bottom_right_global[1]):
                    return map_id
                    
            # If no map found, return 0 as fallback
            print(f"Navigator: WARNING - Could not determine map ID for coordinate ({gy}, {gx}), using fallback 0")
            return 0
            
        except Exception as e:
            print(f"Navigator: ERROR determining map ID for coordinate ({gy}, {gx}): {e}")
            return 0

    def _log_next_coordinates_detailed(self):
        """DIAGNOSTIC: Log detailed information about next 5 coordinates"""
        print(f"\n=== NAVIGATOR PATH ANALYSIS ===")
        print(f"Current Index: {self.current_coordinate_index}/{len(self.sequential_coordinates)}")
        print(f"Current Direction: {self._direction} ({'forward' if self._direction == 1 else 'backward' if self._direction == -1 else 'unknown'})")
        
        # Get current player info
        try:
            cur_pos = self._get_player_global_coords()
            cur_map = self.env.get_game_coords()[2]
            print(f"Player Position: {cur_pos} on Map {cur_map}")
        except:
            print(f"Player Position: Unable to determine")
            cur_map = None
        
        # Show next 5 coordinates
        print(f"Next 5 coordinates:")
        for i in range(5):
            idx = self.current_coordinate_index + i
            if 0 <= idx < len(self.sequential_coordinates):
                coord = self.sequential_coordinates[idx]
                map_id = self.coord_map_ids[idx] if idx < len(self.coord_map_ids) else "?"
                
                # Check if this coordinate requires a warp
                warp_required = "WARP REQUIRED" if cur_map is not None and map_id != cur_map and map_id != "?" else "SAME MAP"
                
                # Distance from current position
                distance = self._manhattan(cur_pos, coord) if cur_pos else "?"
                
                # Current target indicator
                current_indicator = " <-- CURRENT TARGET" if idx == self.current_coordinate_index else ""
                
                print(f"  [{idx:2d}] {coord} Map:{map_id} Dist:{distance} {warp_required}{current_indicator}")
            else:
                print(f"  [{idx:2d}] END OF PATH")
                break
        
        # Check for available warps
        try:
            warps = self.get_available_warps_on_current_map()
            print(f"Available warps on current map: {len(warps)}")
            for warp in warps:
                aligned = self.is_warp_aligned_with_path(warp)
                print(f"  Warp to Map {warp['target_map_id']} at {warp['local_coords']} ({'ALIGNED' if aligned else 'NOT ALIGNED'})")
        except Exception as e:
            print(f"Error checking warps: {e}")
        
        print(f"=== END PATH ANALYSIS ===\n")

    # ...............................................................
    #  L O A D   S E G M E N T  F O R   C U R R E N T  M A P
    # ...............................................................
    def load_segment_for_current_map(self) -> None:
        env_qid = getattr(self.env, "current_loaded_quest_id", None)
        if env_qid is not None:
            self.active_quest_id = env_qid

        map_id = self.env.get_game_coords()[2]
        base = Path(__file__).parent / "quest_paths"

        for qid in range(self.active_quest_id, 0, -1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            if not fp.exists():
                continue
            try:
                data = json.loads(fp.read_text())
                segment_keys = [k for k in data.keys() if int(k.split("_")[0]) == map_id]
                if not segment_keys:
                    continue
                count = self.map_segment_count.get(map_id, 0)
                idx = count if count < len(segment_keys) else len(segment_keys) - 1
                self.map_segment_count[map_id] = count + 1

                selected = segment_keys[idx]
                coords = data[selected]
                self.sequential_coordinates = [(c[0], c[1]) for c in coords]
                self.coord_map_ids = [map_id] * len(coords)
                self.current_coordinate_index = 0
                self.active_quest_id = qid
                print(
                    f"Navigator: Loaded quest {qid:03d} segment '{selected}' "
                    f"on map {map_id} ({len(coords)} steps)"
                )
                # Sync quest ID back to environment
                setattr(self.env, "current_loaded_quest_id", qid)
                return
            except Exception:
                continue

        raise RuntimeError(f"Navigator: No quest file with map id {map_id}")

    # ...............................................................
    #  F A L L B A C K   T O   N E A R E S T   P A T H
    # ...............................................................
    def _fallback_to_nearest_path(self, cur_map: int) -> bool:
        base = Path(__file__).parent / "quest_paths"
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            return False

        orig_id = self.active_quest_id or 0
        self._original_quest_id = orig_id
        self._fallback_mode = True

        entries: list[tuple[int, int, tuple[int, int]]] = []
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            try:
                qid = int(d.name)
                fpath = d / f"{d.name}_coords.json"
                if not fpath.exists():
                    continue
                data = json.loads(fpath.read_text())
                for key, coord_list in data.items():
                    mid = int(key.split("_")[0])
                    if mid != cur_map:
                        continue
                    for idx, (gy, gx) in enumerate(coord_list):
                        entries.append((qid, idx, (gy, gx)))
            except Exception:
                continue

        if not entries:
            print(f"Navigator: fallback: No path entries for map {cur_map}")
            return False

        dists = [(self._manhattan(cur_pos, coord), qid, idx, coord) for qid, idx, coord in entries]
        min_dist = min(dists, key=lambda x: x[0])[0]
        nearest = [(qid, idx, coord) for dist, qid, idx, coord in dists if dist == min_dist]
        sel_qid, sel_idx, sel_coord = min(nearest, key=lambda x: abs(x[0] - orig_id))
        print(
            f"Navigator: fallback: selected quest {sel_qid:03d} idx {sel_idx} "
            f"coord {sel_coord} with dist {min_dist}"
        )

        if not self.load_coordinate_path(sel_qid):
            print(f"Navigator: fallback: Failed to load quest {sel_qid:03d}")
            return False

        diff = sel_qid - orig_id
        self._direction = 1 if diff >= 0 else -1
        self.current_coordinate_index = sel_idx
        return True

    # ...............................................................
    #  S A F E   C O O R D   M A P   I D
    # ...............................................................
    def _safe_coord_map_id(self, idx: int) -> str | int:
        return self.coord_map_ids[idx] if 0 <= idx < len(self.coord_map_ids) else "?"

    # ...............................................................
    #  S T A T U S
    # ...............................................................
    def get_current_status(self) -> str:
        """Get comprehensive navigator status including new robust features"""
        pos = self._get_player_global_coords()
        try:
            cur_map = self.env.get_game_coords()[2]
        except:
            cur_map = "?"
            
        s = ["\n*** ENHANCED NAVIGATOR STATUS ***"]
        s.append(f"Quest ID       : {self.active_quest_id}")
        s.append(f"Current pos    : {pos}")
        s.append(f"Current map    : {cur_map}")
        s.append(f"Path length    : {len(self.sequential_coordinates)}")
        s.append(f"Current index  : {self.current_coordinate_index}")
        s.append(f"Direction      : {self._direction} ({'forward' if self._direction == 1 else 'backward' if self._direction == -1 else 'unknown'})")
        s.append(f"Using placeholder map IDs: {getattr(self, 'using_placeholder_map_ids', False)}")

        if self.sequential_coordinates and 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
            tgt = self.sequential_coordinates[self.current_coordinate_index]
            dist = self._manhattan(pos, tgt) if pos else "?"
            s.append(f"Current target : {tgt} (dist {dist})")
            s.append(f"Target map-id  : {self._safe_coord_map_id(self.current_coordinate_index)}")
            
            # Show path direction
            path_dir = self.get_path_direction_at_index(self.current_coordinate_index)
            s.append(f"Expected direction: {path_dir}")
            
            # Show next few coordinates
            next_coords = []
            for i in range(1, 4):
                next_idx = self.current_coordinate_index + i
                if next_idx < len(self.sequential_coordinates):
                    next_coords.append(str(self.sequential_coordinates[next_idx]))
            if next_coords:
                s.append(f"Next 3 coords  : {', '.join(next_coords)}")
        else:
            s.append(
                "At end of path – quest complete"
                if self.sequential_coordinates else
                "No path loaded"
            )

        # Show available warps
        try:
            warps = self.get_available_warps_on_current_map()
            if warps:
                s.append(f"Available warps: {len(warps)}")
                for warp in warps[:3]:  # Show first 3
                    aligned = self.is_warp_aligned_with_path(warp)
                    s.append(f"  -> Map {warp['target_map_id']} at {warp['local_coords']} ({'ALIGNED' if aligned else 'not aligned'})")
            else:
                s.append("Available warps: None")
        except Exception as e:
            s.append(f"Warp detection error: {e}")

        # Show state validation
        try:
            state = self.validate_navigator_state()
            if state["errors"]:
                s.append(f"ERRORS: {', '.join(state['errors'])}")
            if state["warnings"]:
                s.append(f"WARNINGS: {', '.join(state['warnings'])}")
        except Exception as e:
            s.append(f"State validation error: {e}")

        return "\n".join(s)

    # ...............................................................
    #  R E S E T
    # ...............................................................
    def _reset_state(self):
        self.sequential_coordinates.clear()
        self.coord_map_ids.clear()
        self.current_coordinate_index = 0
        self.quest_locked = False
        self.navigation_status = "idle"
        self.active_quest_id = None
        self.movement_failure_count = 0
        self._house_stair_warp_count = 0 # Reset house stair tracking
        self._recent_maps = []  # Clear map history
        self._map_visit_times = {}  # Clear visit times

    _reset_quest_state = _reset_state  # legacy alias

    # ...............................................................
    #  F O L L O W   P A T H   F O R   C U R R E N T   M A P
    # ...............................................................
    def follow_path_for_current_map(self) -> None:
        """
        Chain per-map segments across quest JSONs.
        Raises RuntimeError if any move fails.
        """
        if self.active_quest_id is None:
            raise RuntimeError("Navigator: no active quest to follow")
        start_id = self.active_quest_id
        map_id = self.env.get_game_coords()[2]
        base = Path(__file__).parent / "quest_paths"

        # 1. search backward for first JSON containing map_id
        found_id = None
        for qid in range(start_id, 0, -1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            if not fp.exists():
                continue
            data = json.loads(fp.read_text())
            if str(map_id) in data:
                found_id = qid
                break
        if found_id is None:
            raise RuntimeError(f"Navigator: no path JSON contains map {map_id}")

        # 2. chain forward from found_id to start_id
        for qid in range(found_id, start_id + 1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            data = json.loads(fp.read_text())
            arr = data.get(str(map_id), [])
            if not arr:
                continue
            self.sequential_coordinates = [(gy, gx) for gy, gx in arr]
            self.coord_map_ids = [map_id] * len(arr)
            self.current_coordinate_index = 0
            print(
                f"Navigator: following quest {qid:03d} segment on map {map_id} "
                f"({len(arr)} steps)"
            )
            self.snap_to_nearest_coordinate()
            while self.current_coordinate_index < len(self.sequential_coordinates):
                ok = self.move_to_next_coordinate()
                if not ok:
                    raise RuntimeError(f"Failed to step to next coordinate in quest {qid:03d}")


            # after finishing this file, continue to next JSON, keep same map_id

    # NOTE: Navigation and quest path workflow
    # - env.current_loaded_quest_id tracks the active quest and is synced on load_coordinate_path
    # - load_coordinate_path loads the full multi-map sequence for a quest and snaps to start
    # - load_segment_for_current_map handles per-map segment loading on map changes
    # - move_to_next_coordinate automatically advances steps, handles warps, and auto-loads next quest or segment
    # - Multi-map quests: flattened coordinates include segments on different maps; warp_tile_handler plus snap_to_nearest_coordinate ensure that when a warp occurs, we realign to the next map's coordinates seamlessly

    # ...............................................................
    #  G E T   N E X T   A C T I O N   (Added Method)
    # ...............................................................
    def get_next_action(self) -> Optional[int]:
        """FIXED: Enhanced pathfinding with collision detection and intelligent direction selection"""
        print(f"\n=== GET_NEXT_ACTION START ===")
        
        # Ensure we have a quest loaded
        if not self._ensure_quest_loaded():
            print("Navigator: get_next_action: No quest loaded and cannot load one")
            return None

        # Get current player position
        current_player_pos = self._get_player_global_coords()
        if current_player_pos is None:
            print("Navigator: get_next_action: Cannot get current player position")
            return None

        # Check if we're at the end of the path
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            print("Navigator: get_next_action: Reached end of path")
            self.navigation_status = "idle"
            return None

        # Get target coordinate
        target_coordinate = self.sequential_coordinates[self.current_coordinate_index]
        print(f"Navigator: Current pos: {current_player_pos}, Target: {target_coordinate}")

        # If player is at the current target coordinate
        if current_player_pos == target_coordinate:
            self.current_coordinate_index += self._direction # Advance to the next coordinate
            self.movement_failure_count = 0 # Reset failure count as we reached a target
            # Check if we've reached the end of the new path
            if self.current_coordinate_index >= len(self.sequential_coordinates):
                print("Navigator: Reached end of path.")
                self.navigation_status = "idle"
                return None
            # Update target to the new current coordinate
            target_coordinate = self.sequential_coordinates[self.current_coordinate_index]
            # If the new target is the same as current position (e.g. single point path segment or already there)
            if current_player_pos == target_coordinate:
                 print(f"Navigator: Advanced index, new target {target_coordinate} is current pos. No move.")
                 self.navigation_status = "at_target"
                 return None # No movement needed for this step

        # Calculate delta to the target coordinate
        delta_y = target_coordinate[0] - current_player_pos[0]
        delta_x = target_coordinate[1] - current_player_pos[1]

        # FIXED: Use intelligent direction selection instead of vertical preference
        direction_str = self._select_intelligent_direction(delta_y, delta_x)
        
        action_id: Optional[int] = None
        if direction_str:
            action_id = self.ACTION_MAPPING_STR_TO_INT[direction_str]
            print(f"Navigator: Suggesting action {action_id} ({direction_str}) towards {target_coordinate}")
            self.navigation_status = "navigating"
        else:
            # This might happen if no walkable direction is available
            print(f"Navigator: No walkable direction available. Target: {target_coordinate}, Current: {current_player_pos}")
            self.navigation_status = "stuck"
            
            # Increment failure count and potentially skip coordinate
            self.movement_failure_count += 1
            if self.movement_failure_count >= self.max_movement_failures:
                print(f"Navigator: Max failures reached, skipping coordinate {target_coordinate}")
                self.current_coordinate_index += self._direction
                self.movement_failure_count = 0
            
        print(f"=== GET_NEXT_ACTION END ===\n")
        return action_id

    # ...............................................................
    #  H A N D L E   P Y G A M E   E V E N T S
    # ...............................................................
    def handle_pygame_event(self, event) -> Optional[int]:
        """
        Handle pygame keyboard events and return appropriate action ID for manual player input.
        This method is called from play.py when interactive_mode is enabled.
        
        Args:
            event: pygame event object
            
        Returns:
            Optional[int]: Action ID if a valid key was pressed, None otherwise
        """
        import pygame
        from environment.environment import VALID_ACTIONS
        from pyboy.utils import WindowEvent
        
        if event.type == pygame.KEYDOWN:
            # Import PATH_FOLLOW_ACTION for the "5" key functionality
            from environment.environment import PATH_FOLLOW_ACTION
            
            # Map pygame keys to action IDs
            key_to_action = {
                pygame.K_DOWN: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN),
                pygame.K_LEFT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT),
                pygame.K_RIGHT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT),
                pygame.K_UP: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP),
                pygame.K_a: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A),
                pygame.K_s: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B),
                pygame.K_RETURN: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START),
                pygame.K_5: PATH_FOLLOW_ACTION,  # Critical "5" key path following functionality
            }
            
            # Handle special key combinations and functions
            if event.key == pygame.K_p:
                # P key: Screenshot functionality
                self._take_screenshot()
                return None
            elif event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                # Ctrl+S: Manual save state
                self._manual_save_state()
                return None
            elif event.key == pygame.K_7:
                # 7 key: Backtrack warp sequence
                self._backtrack_warp_sequence()
                return None
            elif event.key == pygame.K_d:
                # D key: Debug coordinate system
                self._debug_coordinate_system()
                return None
            elif event.key == pygame.K_6:
                # 6 key: Manual warp trigger
                print("Navigator: Manual input - Key 6 -> Manual warp trigger")
                if self.manual_warp_trigger():
                    print("Navigator: Manual warp executed successfully")
                else:
                    print("Navigator: Manual warp failed")
                return None
            
            action_id = key_to_action.get(event.key)
            if action_id is not None:
                print(f"Navigator: Manual input - Key {pygame.key.name(event.key)} -> Action {action_id}")
                return action_id
        
        return None

    # ...............................................................
    #  S P E C I A L   K E Y   F U N C T I O N S
    # ...............................................................
    def _take_screenshot(self) -> None:
        """Take a screenshot when P key is pressed"""
        try:
            import pygame
            from datetime import datetime
            
            # Get current frame from environment
            frame = self.env.render()
            if frame is not None:
                # Convert to pygame surface
                if frame.ndim == 2:  # Grayscale
                    frame_rgb = np.stack((frame,) * 3, axis=-1)
                elif frame.ndim == 3 and frame.shape[2] == 1:  # Grayscale with channel dim
                    frame_rgb = np.concatenate([frame] * 3, axis=2)
                else:  # Already RGB
                    frame_rgb = frame
                
                surface = pygame.surfarray.make_surface(frame_rgb.transpose(1, 0, 2))
                
                # Create screenshots directory if it doesn't exist
                screenshots_dir = Path(__file__).parent.parent.parent / "screenshots"
                screenshots_dir.mkdir(exist_ok=True)
                
                # Save with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = screenshots_dir / f"screenshot_{timestamp}.png"
                pygame.image.save(surface, str(filename))
                print(f"Navigator: Screenshot saved to {filename}")
            else:
                print("Navigator: Could not capture screenshot - no frame available")
        except Exception as e:
            print(f"Navigator: Screenshot failed: {e}")

    def _manual_save_state(self) -> None:
        """Manually save game state when Ctrl+S is pressed"""
        try:
            from environment.environment_helpers.saver import save_manual_state
            
            # Get current run directory
            run_dir = getattr(self.env, 'current_run_dir', None)
            if run_dir is None:
                print("Navigator: No run directory available for manual save")
                return
            
            # Save state with timestamp
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_name = f"manual_save_{timestamp}"
            
            # Use saver module if available, otherwise basic save
            try:
                save_manual_state(self.env, run_dir, save_name)
                print(f"Navigator: Manual save completed: {save_name}")
            except (ImportError, AttributeError):
                # Fallback to basic pyboy save
                save_path = run_dir / f"{save_name}.state"
                self.env.pyboy.save_state(str(save_path))
                print(f"Navigator: Manual save completed: {save_path}")
                
        except Exception as e:
            print(f"Navigator: Manual save failed: {e}")

    def _backtrack_warp_sequence(self) -> None:
        """Backtrack warp sequence when 7 key is pressed"""
        try:
            from environment.environment_helpers.warp_tracker import backtrack_warp_sequence
            
            print("Navigator: Initiating backtrack warp sequence...")
            success = backtrack_warp_sequence(self.env)
            if success:
                print("Navigator: Backtrack warp sequence completed")
                # Re-snap to nearest coordinate after warping
                self.snap_to_nearest_coordinate()
            else:
                print("Navigator: Backtrack warp sequence failed or no warps to backtrack")
                
        except (ImportError, AttributeError) as e:
            print(f"Navigator: Backtrack warp functionality not available: {e}")
        except Exception as e:
            print(f"Navigator: Backtrack warp failed: {e}")

    def _debug_coordinate_system(self) -> None:
        """Debug coordinate system when D key is pressed"""
        try:
            print("Navigator: Running coordinate system debug...")
            print(self.get_current_status())
            
            # Additional debug information
            print(f"\n--- COORDINATE SYSTEM DEBUG ---")
            print(f"Environment coordinates loaded: {len(getattr(self.env, 'combined_path', []))}")
            print(f"Navigator coordinates loaded: {len(self.sequential_coordinates)}")
            
            if hasattr(self.env, 'combined_path') and self.env.combined_path:
                print(f"Environment first coordinate: {self.env.combined_path[0]}")
                print(f"Environment current index: {getattr(self.env, 'current_path_target_index', 'N/A')}")
            
            if self.sequential_coordinates:
                print(f"Navigator first coordinate: {self.sequential_coordinates[0]}")
                print(f"Navigator current index: {self.current_coordinate_index}")
                
            # Check coordinate file accessibility
            if self.active_quest_id:
                quest_dir_name = f"{self.active_quest_id:03d}"
                quest_file_name = f"{quest_dir_name}_coords.json"
                file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name
                print(f"Quest file exists: {file_path.exists()}")
                
        except Exception as e:
            print(f"Navigator: Debug coordinate system failed: {e}")

    def is_on_path_for_quest(self, quest_id: int) -> bool:
        """
        Check if the current player position is on the path for the specified quest.
        Used by quest_manager to determine if PATH_FOLLOW_ACTION is appropriate.
        """
        try:
            # Get current player position
            current_pos = self._get_player_global_coords()
            if current_pos is None:
                return False
            
            # If this quest is currently loaded and we're on one of its coordinates
            if (self.active_quest_id == quest_id and 
                self.sequential_coordinates and 
                current_pos in self.sequential_coordinates):
                return True
            
            # Check if current position is in the quest's coordinate file
            base = Path(__file__).parent / "quest_paths"
            file_path = base / f"{quest_id:03d}" / f"{quest_id:03d}_coords.json"
            
            if not file_path.exists():
                return False
                
            try:
                data = json.loads(file_path.read_text())
                # Check all coordinates in the quest file
                for coord_list in data.values():
                    if current_pos in [(gy, gx) for gy, gx in coord_list]:
                        return True
            except Exception:
                return False
                
            return False
            
        except Exception as e:
            print(f"Navigator: Error checking if on path for quest {quest_id}: {e}")
            return False

    def _get_adjacent_warp_targets(self) -> List[int]:
        """Get a list of map IDs that are accessible via adjacent warp tiles"""
        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            local_pos = (local_x, local_y)
        except Exception:
            return []
            
        warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
        target_maps = []
        
        for entry in warp_entries:
            warp_x, warp_y = entry.get("x"), entry.get("y")
            if warp_x is not None and warp_y is not None:
                # If we're adjacent to this warp tile
                if self._manhattan(local_pos, (warp_x, warp_y)) <= 1:
                    target_map_id = entry.get("target_map_id")
                    if target_map_id is not None:
                        target_maps.append(target_map_id)
                        
        return target_maps
        
    def _try_continue_on_current_map(self) -> bool:
        """When in a warp loop, try to find a path on the current map to follow instead"""
        cur_map = self.env.get_game_coords()[2]
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            return False
            
        print(f"Navigator: Attempting to find path on current map {cur_map} from position {cur_pos}")
        
        # Try to load a segment specifically for this map
        try:
            self.load_segment_for_current_map()
            print(f"Navigator: Successfully loaded segment for map {cur_map}")
            
            # FIXED: Actually take a step along the loaded path
            # This is critical to ensure we don't just reload the same segment over and over
            if self.sequential_coordinates and self.current_coordinate_index < len(self.sequential_coordinates):
                target = self.sequential_coordinates[self.current_coordinate_index]
                print(f"Navigator: Now moving towards coordinate {target} after loading segment")
                
                # Force movement in the direction of the target
                if self._step_towards(target):
                    # Disable warp handling for a bit to ensure we actually move along the path
                    self._last_warp_origin = None  # Clear the loop detection flag
                    print(f"Navigator: Successfully moved along loaded path")
                    return True
                
            return True
        except RuntimeError:
            print(f"Navigator: No segment found for map {cur_map}")
            
        return False

    # ...............................................................
    #  W A R P   D E T E C T I O N
    # ...............................................................
    def get_available_warps_on_current_map(self) -> List[Dict]:
        """Get all warp tiles and their targets on the current map"""
        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            cur_pos = (local_x, local_y)
        except Exception as e:
            print(f"Navigator: Could not get game coords for warp detection: {e}")
            return []

        warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
        if not warp_entries:
            return []

        available_warps = []
        for entry in warp_entries:
            warp_x, warp_y = entry.get("x"), entry.get("y")
            target_map_id = entry.get("target_map_id")
            target_map_name = entry.get("target_map_name")
            
            if warp_x is not None and warp_y is not None and target_map_id is not None:
                # FIXED: Handle LAST_MAP special case (target_map_id = 255)
                resolved_target_map_id = target_map_id
                if target_map_id == 255 and target_map_name == "LAST_MAP":
                    # Resolve to the previous map ID
                    prev_map_id = getattr(self.env, "prev_map_id", None)
                    if prev_map_id is not None and prev_map_id != cur_map:
                        resolved_target_map_id = prev_map_id
                        print(f"Navigator: Resolved LAST_MAP warp to map {prev_map_id}")
                    else:
                        # Fallback: assume came from Pallet Town if in Oak's Lab
                        if cur_map == 40:  # Oak's Lab
                            resolved_target_map_id = 0  # Pallet Town
                            print(f"Navigator: Fallback - Oak's Lab warp to Pallet Town (map 0)")
                        else:
                            print(f"Navigator: Warning - Cannot resolve LAST_MAP for warp on map {cur_map}")
                            resolved_target_map_id = 0  # Default fallback
                
                distance = self._manhattan(cur_pos, (warp_x, warp_y))
                warp_info = {
                    "local_coords": (warp_x, warp_y),
                    "target_map_id": resolved_target_map_id,
                    "original_target_map_id": target_map_id,
                    "target_map_name": target_map_name,
                    "distance_from_player": distance,
                    "is_adjacent": distance == 1,
                    "global_coords": local_to_global(warp_y, warp_x, cur_map) if hasattr(self, 'local_to_global') else None
                }
                available_warps.append(warp_info)
        
        # Sort by distance from player
        available_warps.sort(key=lambda x: x["distance_from_player"])
        return available_warps

    def is_warp_aligned_with_path(self, warp_info: Dict) -> bool:
        """Check if a warp leads toward the quest path direction"""
        if not self.sequential_coordinates or self.current_coordinate_index >= len(self.sequential_coordinates):
            return False
            
        target_map_id = warp_info["target_map_id"]
        
        # Look ahead in the path to see if we need to go to the target map
        look_ahead_window = 20
        for i in range(look_ahead_window):
            next_idx = self.current_coordinate_index + (i * self._direction)
            if 0 <= next_idx < len(self.coord_map_ids):
                if self.using_placeholder_map_ids:
                    # Use MAP_DATA to check if coordinate is on target map
                    from environment.data.recorder_data.global_map import MAP_DATA
                    if target_map_id in MAP_DATA:
                        coord = self.sequential_coordinates[next_idx]
                        map_bounds = self._get_map_bounds(target_map_id)
                        if map_bounds and self._coord_in_bounds(coord, map_bounds):
                            return True
                else:
                    # Use actual map IDs
                    if self.coord_map_ids[next_idx] == target_map_id:
                        return True
            else:
                break
        return False

    def _get_map_bounds(self, map_id: int) -> Optional[Dict]:
        """Get global coordinate bounds for a map"""
        try:
            from environment.data.recorder_data.global_map import MAP_DATA
            if map_id not in MAP_DATA:
                return None
                
            map_x, map_y = MAP_DATA[map_id]["coordinates"]
            tile_width, tile_height = MAP_DATA[map_id]["tileSize"]
            
            top_left = local_to_global(0, 0, map_id)
            bottom_right = local_to_global(tile_height-1, tile_width-1, map_id)
            
            return {
                "top_left": top_left,
                "bottom_right": bottom_right,
                "map_id": map_id
            }
        except Exception as e:
            print(f"Navigator: Error getting map bounds for {map_id}: {e}")
            return None

    def _coord_in_bounds(self, coord: Tuple[int, int], bounds: Dict) -> bool:
        """Check if a coordinate is within map bounds"""
        coord_gy, coord_gx = coord
        top_left = bounds["top_left"]
        bottom_right = bounds["bottom_right"]
        return (top_left[0] <= coord_gy <= bottom_right[0] and 
                top_left[1] <= coord_gx <= bottom_right[1])

    # ...............................................................
    #  P A T H   D I R E C T I O N A L I T Y
    # ...............................................................
    def get_path_direction_at_index(self, index: int) -> Optional[str]:
        """Get the movement direction needed from current index to next index"""
        if (index < 0 or index >= len(self.sequential_coordinates) - 1):
            return None
            
        current_coord = self.sequential_coordinates[index]
        next_coord = self.sequential_coordinates[index + 1]
        
        dy = next_coord[0] - current_coord[0]
        dx = next_coord[1] - current_coord[1]
        
        # Determine primary direction
        if abs(dy) > abs(dx):
            return "down" if dy > 0 else "up"
        elif abs(dx) > abs(dy):
            return "right" if dx > 0 else "left"
        else:
            return None  # Diagonal or same position

    def validate_path_direction(self) -> bool:
        """Validate that current movement is following the path direction"""
        if (not self.sequential_coordinates or 
            self.current_coordinate_index >= len(self.sequential_coordinates) - 1):
            return True  # No validation needed at end of path
            
        expected_direction = self.get_path_direction_at_index(self.current_coordinate_index)
        if expected_direction is None:
            return True  # No clear direction expected
            
        # Check if we're moving in the right direction
        cur_pos = self._get_player_global_coords()
        target_pos = self.sequential_coordinates[self.current_coordinate_index]
        
        if cur_pos == target_pos:
            return True  # Already at target
            
        # Calculate actual movement direction needed
        dy = target_pos[0] - cur_pos[0]
        dx = target_pos[1] - cur_pos[1]
        
        actual_direction = None
        if abs(dy) > abs(dx):
            actual_direction = "down" if dy > 0 else "up"
        elif abs(dx) > abs(dy):
            actual_direction = "right" if dx > 0 else "left"
            
        return actual_direction == expected_direction

    def enforce_path_direction(self) -> bool:
        """ENHANCED: Force the current path direction to be appropriate for the situation with proper directionality maintenance"""
        if not self.sequential_coordinates or len(self.sequential_coordinates) < 2:
            return False
            
        current_pos = self._get_player_global_coords()
        if not current_pos:
            return False
            
        # Find the nearest coordinate and determine appropriate direction
        distances = [self._manhattan(current_pos, coord) for coord in self.sequential_coordinates]
        nearest_idx = distances.index(min(distances))
        
        # ENHANCED: Improved direction determination logic
        # Check if we have a previous direction preference
        if hasattr(self, '_direction_preference'):
            preferred_direction = self._direction_preference
        else:
            preferred_direction = 1  # Default forward
            
        # ENHANCED: Determine direction based on quest state and position
        if nearest_idx == 0:
            # At beginning of path - should go forward
            self._direction = 1
            print(f"Navigator: At beginning of path (idx {nearest_idx}), enforcing forward direction")
        elif nearest_idx >= len(self.sequential_coordinates) - 2:
            # At end of path - determine based on quest completion status
            cur_map = self.env.get_game_coords()[2]
            if cur_map == 40 and self.active_quest_id == 3:  # Special case for Oak's Lab
                self._direction = -1  # Go backward to exit
                print(f"Navigator: At end of Quest 3 in Oak's Lab, enforcing backward direction to exit")
            else:
                self._direction = -1  # Generally go backward from end
                print(f"Navigator: At end of path (idx {nearest_idx}), enforcing backward direction")
        else:
            # In middle of path - maintain current direction or use preference
            if abs(self._direction) == 1:
                # Keep existing direction if valid
                print(f"Navigator: In middle of path (idx {nearest_idx}), maintaining direction {self._direction}")
            else:
                # Set to preferred direction
                self._direction = preferred_direction
                print(f"Navigator: In middle of path (idx {nearest_idx}), setting to preferred direction {self._direction}")
            
        # ENHANCED: Validate direction makes sense for current index
        next_idx = nearest_idx + self._direction
        if next_idx < 0:
            self._direction = 1  # Can't go backward from beginning
            print(f"Navigator: Corrected direction to forward (can't go backward from index {nearest_idx})")
        elif next_idx >= len(self.sequential_coordinates):
            self._direction = -1  # Can't go forward from end
            print(f"Navigator: Corrected direction to backward (can't go forward from index {nearest_idx})")
            
        self.current_coordinate_index = nearest_idx
        print(f"Navigator: Enforced direction {self._direction} ({'forward' if self._direction == 1 else 'backward'}) at index {nearest_idx}")
        return True

    # REMOVED: set_direction_preference() method  
    # Direction is now automatically determined by intelligent analysis
    # Manual direction setting is dangerous and should never be used

    # ...............................................................
    #  R O B U S T   P A T H   F O L L O W I N G
    # ...............................................................
    def validate_navigator_state(self) -> Dict[str, Any]:
        """Comprehensive validation of navigator state"""
        state = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": {}
        }
        
        # Check basic state
        state["info"]["quest_id"] = self.active_quest_id
        state["info"]["coordinate_index"] = self.current_coordinate_index
        state["info"]["total_coordinates"] = len(self.sequential_coordinates)
        state["info"]["using_placeholder_map_ids"] = getattr(self, 'using_placeholder_map_ids', False)
        state["info"]["direction"] = self._direction
        
        # Validate coordinates loaded
        if not self.sequential_coordinates:
            state["errors"].append("No coordinates loaded")
            state["valid"] = False
            
        # Validate index bounds
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            state["errors"].append(f"Index {self.current_coordinate_index} out of bounds (max: {len(self.sequential_coordinates) - 1})")
            state["valid"] = False
            
        # Check player position
        cur_pos = self._get_player_global_coords()
        if cur_pos is None:
            state["errors"].append("Cannot get player position")
            state["valid"] = False
        else:
            state["info"]["player_position"] = cur_pos
            
        # Check if on correct map
        try:
            cur_map = self.env.get_game_coords()[2]
            state["info"]["current_map"] = cur_map
            
            if self.sequential_coordinates and 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
                target_coord = self.sequential_coordinates[self.current_coordinate_index]
                state["info"]["target_coordinate"] = target_coord
                
                if cur_pos:
                    distance = self._manhattan(cur_pos, target_coord)
                    state["info"]["distance_to_target"] = distance
                    if distance > 50:  # Arbitrary large distance
                        state["warnings"].append(f"Very far from target coordinate (distance: {distance})")
                        
        except Exception as e:
            state["errors"].append(f"Error checking map state: {e}")
            
        # Validate path direction
        if not self.validate_path_direction():
            state["warnings"].append("Path direction validation failed")
            
        if not self.enforce_path_direction():
            state["warnings"].append("Path direction had to be corrected")
            
        return state

    def _validate_path_direction_for_current_position(self) -> bool:
        """Check if the current path direction is valid for the current position"""
        if not self.sequential_coordinates:
            return False
            
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            return False
            
        # Find nearest coordinate on path
        distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
        nearest_idx = distances.index(min(distances))
        
        # Check if we're reasonably close to the path
        if distances[nearest_idx] <= 5:
            self.current_coordinate_index = nearest_idx
            print(f"Navigator: Validated position at index {nearest_idx}, distance {distances[nearest_idx]}")
            return True
            
        return False

    # ...............................................................
    #  I N T E L L I G E N T   D I R E C T I O N   D E T E R M I N A T I O N
    # ...............................................................
    def determine_optimal_direction(self) -> int:
        """INTELLIGENT: Determine optimal movement direction based on quest completion requirements"""
        print(f"\n=== DETERMINE_OPTIMAL_DIRECTION START ===")
        
        # Default to forward
        direction = 1
        
        try:
            # FIXED: Check if player is on an active quest path node - if so, ALWAYS go forwards
            current_pos = self._get_player_global_coords()
            if current_pos and self.sequential_coordinates:
                print(f"Direction Intelligence: DIAGNOSTIC - Checking position {current_pos} against {len(self.sequential_coordinates)} path coordinates")
                
                # Check if current position is exactly on one of the quest path coordinates
                if current_pos in self.sequential_coordinates:
                    direction = 1  # Always forward when on active quest path
                    path_index = self.sequential_coordinates.index(current_pos)
                    print(f"Direction Intelligence: DIAGNOSTIC - Player is ON active quest path node {current_pos} (index {path_index})")
                    print(f"Direction Intelligence: DIAGNOSTIC - FORCING FORWARD direction (1) because on quest path")
                    print(f"Direction Intelligence: DIAGNOSTIC - Final direction decision: {direction}")
                    print(f"=== DETERMINE_OPTIMAL_DIRECTION END ===\n")
                    return direction
                
                # ENHANCED: Check proximity with graduated distance thresholds
                for coord in self.sequential_coordinates:
                    distance = self._manhattan(current_pos, coord)
                    # Use stricter distance for more precise navigation
                    if distance <= 1:  # Within 1 tile - very close to quest path
                        direction = 1  # Always forward when very close to quest path
                        print(f"Direction Intelligence: DIAGNOSTIC - Player is VERY CLOSE to active quest path node {coord} (distance: {distance})")
                        print(f"Direction Intelligence: DIAGNOSTIC - FORCING FORWARD direction (1) because very close to quest path")
                        print(f"Direction Intelligence: DIAGNOSTIC - Final direction decision: {direction}")
                        print(f"=== DETERMINE_OPTIMAL_DIRECTION END ===\n")
                        return direction
                    elif distance <= 2:  # Within 2 tiles - moderately close
                        # For moderate proximity, still prefer forward but check if we should adjust
                        current_index = getattr(self, 'current_coordinate_index', 0)
                        if 0 <= current_index < len(self.sequential_coordinates):
                            target_coord = self.sequential_coordinates[current_index]
                            distance_to_target = self._manhattan(current_pos, target_coord)
                            if distance_to_target <= distance:  # Target is as close or closer than this path node
                                direction = 1
                                print(f"Direction Intelligence: DIAGNOSTIC - Player moderately close to quest path node {coord} (distance: {distance})")
                                print(f"Direction Intelligence: DIAGNOSTIC - Target coordinate {target_coord} is equally close (distance: {distance_to_target})")
                                print(f"Direction Intelligence: DIAGNOSTIC - PREFERRING FORWARD direction (1)")
                                print(f"Direction Intelligence: DIAGNOSTIC - Final direction decision: {direction}")
                                print(f"=== DETERMINE_OPTIMAL_DIRECTION END ===\n")
                                return direction
                
                print(f"Direction Intelligence: DIAGNOSTIC - Player not on/near active quest path, checking requirements...")
            else:
                print(f"Direction Intelligence: DIAGNOSTIC - Unable to get player position or no quest path loaded")
            
            # Only proceed with complex requirement checking if NOT on/near quest path
            print(f"Direction Intelligence: DIAGNOSTIC - Player not on active quest path, checking requirements...")
            
            # Check quest completion requirements
            requirements_check = self._check_quest_completion_requirements()
            print(f"Direction Intelligence: DIAGNOSTIC - Quest requirements check: {requirements_check}")
            
            if requirements_check['should_reverse']:
                direction = -1
                print(f"Direction Intelligence: DIAGNOSTIC - Setting direction to BACKWARD (-1) due to: {requirements_check['reason']}")
            else:
                print(f"Direction Intelligence: DIAGNOSTIC - Setting direction to FORWARD (1): {requirements_check['reason']}")
                
            # Additional logic for end-of-path scenarios
            if (self.sequential_coordinates and 
                self.current_coordinate_index >= len(self.sequential_coordinates) - 1):
                
                try:
                    cur_map = self.env.get_game_coords()[2]
                    # Special case: Quest 3 in Oak's Lab should exit backward
                    if self.active_quest_id == 3 and cur_map == 40:
                        direction = -1
                        print(f"Direction Intelligence: DIAGNOSTIC - Quest 3 Oak's Lab special case: BACKWARD (-1)")
                    # ENHANCED: General end-of-path handling for quests that need to exit
                    elif cur_map in [40, 37, 38]:  # Oak's Lab, Red's House floors
                        # Check if there's a clear exit path
                        available_warps = self.get_available_warps_on_current_map()
                        if available_warps:
                            direction = -1  # Move backward to reach exit warp
                            print(f"Direction Intelligence: DIAGNOSTIC - End of quest in indoor map {cur_map} with warps available: BACKWARD (-1)")
                except Exception as e:
                    print(f"Direction Intelligence: DIAGNOSTIC - Error in end-of-path logic: {e}")
                    
        except Exception as e:
            print(f"Direction Intelligence: DIAGNOSTIC - Error in direction determination: {e}")
            import traceback
            traceback.print_exc()
            
        print(f"Direction Intelligence: DIAGNOSTIC - Final direction decision: {direction}")
        print(f"=== DETERMINE_OPTIMAL_DIRECTION END ===\n")
        return direction

    def _check_quest_completion_requirements(self) -> Dict[str, Any]:
        """Check if current quest's prerequisites are met"""
        print(f"Requirements Check: DIAGNOSTIC - Checking requirements for quest {self.active_quest_id}")
        
        result = {
            'should_reverse': False,
            'reason': 'No specific requirements found',
            'missing_prerequisites': [],
            'location_mismatch': False
        }
        
        if not self.active_quest_id:
            result['reason'] = 'No active quest'
            print(f"Requirements Check: DIAGNOSTIC - {result['reason']}")
            return result
            
        try:
            # Load quest requirements
            requirements_file = Path(__file__).parent.parent / "data" / "quest_data" / "required_completions.json"
            if not requirements_file.exists():
                result['reason'] = 'Requirements file not found'
                print(f"Requirements Check: DIAGNOSTIC - {result['reason']}")
                return result
                
            with open(requirements_file, 'r') as f:
                requirements_data = json.load(f)
                
            quest_key = f"quest_{self.active_quest_id:03d}"
            if quest_key not in requirements_data:
                result['reason'] = f'No requirements found for {quest_key}'
                print(f"Requirements Check: DIAGNOSTIC - {result['reason']}")
                return result
                
            quest_requirements = requirements_data[quest_key]
            print(f"Requirements Check: DIAGNOSTIC - Found requirements: {quest_requirements}")
            
            # Check prerequisites
            prerequisites = quest_requirements.get('prerequisites', [])
            print(f"Requirements Check: DIAGNOSTIC - Prerequisites: {prerequisites}")
            
            if prerequisites:
                # Check completion status via quest progression engine
                quest_progression = getattr(self.env, 'quest_progression_engine', None)
                if quest_progression:
                    missing_prereqs = []
                    for prereq in prerequisites:
                        if isinstance(prereq, int):
                            if not quest_progression.is_quest_complete(prereq):
                                missing_prereqs.append(prereq)
                                print(f"Requirements Check: DIAGNOSTIC - Missing prerequisite quest {prereq}")
                        elif isinstance(prereq, str):
                            # Handle trigger-based prerequisites
                            trigger_complete = quest_progression.is_trigger_complete(prereq)
                            if not trigger_complete:
                                missing_prereqs.append(prereq)
                                print(f"Requirements Check: DIAGNOSTIC - Missing prerequisite trigger {prereq}")
                    
                    if missing_prereqs:
                        result['should_reverse'] = True
                        result['reason'] = f'Missing prerequisites: {missing_prereqs}'
                        result['missing_prerequisites'] = missing_prereqs
                        print(f"Requirements Check: DIAGNOSTIC - Should reverse due to missing prerequisites")
                        return result
                else:
                    print(f"Requirements Check: DIAGNOSTIC - No quest progression engine available")
            
            # Check location requirements for triggers
            triggers = quest_requirements.get('triggers', [])
            if triggers:
                location_check = self._check_location_mismatch_for_triggers(triggers)
                print(f"Requirements Check: DIAGNOSTIC - Location check: {location_check}")
                if location_check['mismatch']:
                    result['should_reverse'] = True
                    result['reason'] = f'Location mismatch: {location_check["reason"]}'
                    result['location_mismatch'] = True
                    return result
                    
        except Exception as e:
            result['reason'] = f'Error checking requirements: {e}'
            print(f"Requirements Check: DIAGNOSTIC - Error: {e}")
            
        print(f"Requirements Check: DIAGNOSTIC - Final result: {result}")
        return result

    def _check_location_mismatch_for_triggers(self, triggers: List[Dict]) -> Dict[str, Any]:
        """
        Check if current location is incompatible with quest triggers.
        This helps detect when player is ahead of where they should be.
        """
        result = {"mismatch": False, "reason": ""}
        
        try:
            cur_map = self.env.get_game_coords()[2]
            cur_pos = self._get_player_global_coords()
            
            # Analyze triggers to see if they can be completed at current location
            for trigger in triggers:
                trigger_type = trigger.get("type", "")
                
                # Map-specific triggers
                if trigger_type == "current_map_id":
                    required_map = trigger.get("current_map_id")
                    if required_map and required_map != cur_map:
                        result["mismatch"] = True
                        result["reason"] = f"Need to be on map {required_map}, currently on {cur_map}"
                        return result
                        
                elif trigger_type == "current_map_is_previous_map_was":
                    required_current_map = trigger.get("current_map_id")
                    required_previous_map = trigger.get("previous_map_id")
                    if required_current_map != cur_map:
                        result["mismatch"] = True
                        result["reason"] = f"Need to transition TO map {required_current_map}, currently on {cur_map}"
                        return result
                    if required_previous_map != self.env.map_history[-2]:
                        result["mismatch"] = True
                        result["reason"] = f"Need to have been on map {required_previous_map} before current. map_history: {self.env.map_history}"
                        return result
                        
                # Item-based triggers - check if we're in a location where we can't get the item
                elif trigger_type == "item_is_in_inventory":
                    item_name = trigger.get("item_name", "")
                    # For now, assume items can be obtained on any map
                    # Could add more sophisticated logic here
                    pass
                    
                # Event-based triggers - check if we're in correct location for event
                elif trigger_type == "event_completed":
                    event_name = trigger.get("event_name", "")
                    # Some events are location-specific
                    if "OAK" in event_name and cur_map != 40:  # Oak's Lab events
                        result["mismatch"] = True
                        result["reason"] = f"Oak-related event {event_name} requires Oak's Lab (map 40), currently on {cur_map}"
                        return result
                        
        except Exception as e:
            print(f"Navigator: Error checking location mismatch: {e}")
            result["reason"] = f"Error: {e}"
            
        return result

    def _should_reverse_direction_for_requirements(self) -> bool:
        """
        Determine if we should reverse direction to complete missing requirements.
        This is the core intelligence that prevents getting stuck ahead of requirements.
        """
        try:
            completion_status = self._check_quest_completion_requirements()
            
            # If we're ahead of requirements, we should reverse
            if completion_status["ahead_of_requirements"]:
                missing = completion_status["missing_prerequisites"]
                print(f"Navigator: Should reverse direction - missing prerequisites: {missing}")
                return True
                
            # Additional check: if we're in a location where we can't complete current quest
            if not completion_status["requirements_met"]:
                # Check if moving backward would help us reach a location where we can complete requirements
                analysis = completion_status["analysis"]
                if "location mismatch" in analysis.lower() or "map" in analysis.lower():
                    print(f"Navigator: Should reverse direction - location mismatch: {analysis}")
                    return True
                    
            return False
            
        except Exception as e:
            print(f"Navigator: Error determining if should reverse: {e}")
            return False

    # REMOVED: set_direction_preference() - direction is now automatically determined
    # Manual direction setting is dangerous and should never be used
    
    def get_intelligent_direction(self) -> int:
        """
        Get the intelligently determined direction for path following.
        This replaces any manual direction setting with automatic analysis.
        """
        # Always use intelligent determination
        optimal_direction = self.determine_optimal_direction()
        
        # Update internal direction if it differs
        if self._direction != optimal_direction:
            old_direction = self._direction
            self._direction = optimal_direction
            print(f"Navigator: INTELLIGENT: Direction changed from {old_direction} to {optimal_direction}")
            print(f"Navigator: INTELLIGENT: {'FORWARD' if optimal_direction == 1 else 'BACKWARD'} movement to handle quest requirements")
            
        return self._direction

    def _ensure_intelligent_direction(self) -> None:
        """INTELLIGENT: Ensure direction is set intelligently before any movement"""
        print(f"Direction Intelligence: DIAGNOSTIC - Current stored direction: {self._direction}")
        
        intelligent_direction = self.determine_optimal_direction()
        print(f"Direction Intelligence: DIAGNOSTIC - Intelligent direction determined: {intelligent_direction}")
        
        if self._direction != intelligent_direction:
            print(f"Direction Intelligence: DIAGNOSTIC - Direction changed from {self._direction} to {intelligent_direction}")
            self._direction = intelligent_direction
        else:
            print(f"Direction Intelligence: DIAGNOSTIC - Direction remains unchanged: {self._direction}")

    def get_quick_status(self) -> str:
        """Get a concise status summary for quick diagnostics"""
        try:
            pos = self._get_player_global_coords()
            cur_map = self.env.get_game_coords()[2]
            
            # Basic state
            status_parts = []
            status_parts.append(f"Quest:{self.active_quest_id or 'None'}")
            status_parts.append(f"Map:{cur_map}")
            status_parts.append(f"Pos:{pos}")
            status_parts.append(f"Dir:{'F' if self._direction == 1 else 'B' if self._direction == -1 else '?'}")
            
            # Path status
            if self.sequential_coordinates:
                status_parts.append(f"Path:{self.current_coordinate_index}/{len(self.sequential_coordinates)}")
                if 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
                    target = self.sequential_coordinates[self.current_coordinate_index]
                    distance = self._manhattan(pos, target) if pos else "?"
                    status_parts.append(f"Target:{target}")
                    status_parts.append(f"Dist:{distance}")
                else:
                    status_parts.append("Target:END")
            else:
                status_parts.append("Path:NONE")
            
            # Issues check
            issues = []
            if not self.sequential_coordinates:
                issues.append("NO_PATH")
            if not self.active_quest_id:
                issues.append("NO_QUEST")
            if hasattr(self.env, 'is_warping') and self.env.is_warping:
                issues.append("WARPING")
            
            if issues:
                status_parts.append(f"Issues:{','.join(issues)}")
            
            return " | ".join(status_parts)
            
        except Exception as e:
            return f"Navigator Status Error: {e}"

    def validate_and_repair_state(self) -> bool:
        """Quick validation and repair of navigator state"""
        print(f"\n=== NAVIGATOR STATE VALIDATION AND REPAIR ===")
        
        try:
            # Check 1: Quest loaded
            if not self.active_quest_id or not self.sequential_coordinates:
                print(f"STATE REPAIR: No quest/path loaded, attempting to load...")
                if self._ensure_quest_loaded():
                    print(f"STATE REPAIR: ✅ Successfully loaded quest {self.active_quest_id}")
                else:
                    print(f"STATE REPAIR: ❌ Failed to load any quest")
                    return False
            
            # Check 2: Valid coordinate index
            if self.current_coordinate_index < 0 or self.current_coordinate_index >= len(self.sequential_coordinates):
                print(f"STATE REPAIR: Invalid coordinate index {self.current_coordinate_index}, attempting snap...")
                if self.snap_to_nearest_coordinate():
                    print(f"STATE REPAIR: ✅ Successfully snapped to index {self.current_coordinate_index}")
                else:
                    print(f"STATE REPAIR: ❌ Failed to snap to valid coordinate")
                    return False
            
            # Check 3: Direction validity
            if self._direction not in [-1, 1]:
                print(f"STATE REPAIR: Invalid direction {self._direction}, resetting to forward...")
                self._direction = 1
                print(f"STATE REPAIR: ✅ Reset direction to forward (1)")
            
            # Check 4: Map ID consistency
            try:
                cur_map = self.env.get_game_coords()[2]
                if self.current_coordinate_index < len(self.coord_map_ids):
                    expected_map = self.coord_map_ids[self.current_coordinate_index]
                    if expected_map != cur_map and expected_map != 0:  # 0 is placeholder
                        print(f"STATE REPAIR: Map mismatch - current:{cur_map}, expected:{expected_map}")
                        print(f"STATE REPAIR: This may indicate a warp is needed")
            except Exception as e:
                print(f"STATE REPAIR: Could not check map consistency: {e}")
            
            # Check 5: Quest progression logic
            optimal_direction = self.determine_optimal_direction()
            if self._direction != optimal_direction:
                print(f"STATE REPAIR: Direction optimization - changing from {self._direction} to {optimal_direction}")
                self._direction = optimal_direction
                print(f"STATE REPAIR: ✅ Optimized direction")
            
            print(f"STATE REPAIR: ✅ Navigator state validation complete")
            print(f"STATE REPAIR: Current status - {self.get_quick_status()}")
            print(f"=== END STATE VALIDATION ===\n")
            return True
            
        except Exception as e:
            print(f"STATE REPAIR: ❌ Error during state validation: {e}")
            import traceback
            traceback.print_exc()
            return False

    # NEW: Add collision detection integration
    def _get_collision_map_grid(self) -> Optional[list]:
        """Get the current collision map as a 2D grid for pathfinding - using SAME method as UI"""
        try:
            # FIXED: Use the EXACT same method as the UI - call env.get_collision_map()
            collision_map_str = self.env.get_collision_map()
            if not collision_map_str:
                return None
            
            # Parse the numeric collision map
            lines = collision_map_str.strip().split('\n')
            grid = []
            
            for line in lines:
                line = line.strip()
                # Skip legend lines and empty lines
                if line.startswith('Legend:') or not line or not all(c in '0123456789 ' for c in line):
                    continue
                
                # Parse numeric values
                row = []
                for value in line.split():
                    try:
                        row.append(int(value))
                    except ValueError:
                        continue
                
                if row:  # Only add non-empty rows
                    grid.append(row)
            
            return grid if grid else None
            
        except Exception as e:
            print(f"Navigator: Error getting collision map grid: {e}")
            return None

    def _is_direction_walkable(self, direction: str) -> bool:
        """
        Check if a direction is walkable using collision detection.
        
        Args:
            direction: One of "up", "down", "left", "right"
        
        Returns:
            bool: True if direction is walkable, False otherwise
        """
        try:
            print(f"Navigator: COLLISION_CHECK - Testing direction: {direction}")
            
            # ENHANCED: Use the SAME collision detection as the UI
            grid = self._get_collision_map_grid()
            if not grid:
                print(f"Navigator: COLLISION_CHECK - No collision grid available, defaulting to ALLOW movement")
                return True  # Default to allowing movement if check fails
            
            print(f"Navigator: COLLISION_CHECK - Got collision grid: {len(grid)}x{len(grid[0]) if grid else 0}")
            
            # Player is assumed to be at center of 9x10 grid (position 4,4)
            player_row, player_col = 4, 4
            
            # Direction offsets
            offsets = {
                "up": (-1, 0),
                "down": (1, 0),
                "left": (0, -1),
                "right": (0, 1)
            }
            
            if direction not in offsets:
                print(f"Navigator: COLLISION_CHECK - Invalid direction: {direction}")
                return False
            
            dr, dc = offsets[direction]
            target_row = player_row + dr
            target_col = player_col + dc
            
            print(f"Navigator: COLLISION_CHECK - Player at ({player_row},{player_col}), target: ({target_row},{target_col})")
            
            # Check bounds
            if not (0 <= target_row < len(grid) and 0 <= target_col < len(grid[0])):
                print(f"Navigator: COLLISION_CHECK - Target out of bounds")
                return False
            
            target_tile = grid[target_row][target_col]
            print(f"Navigator: COLLISION_CHECK - Target tile value: {target_tile}")
            
            # Collision interpretation:
            # 0 = walkable path
            # 1 = wall/obstacle
            # 2 = sprite (blocked)
            # 3-6 = player positions (consider walkable as they're previous positions)
            if target_tile == 0:
                print(f"Navigator: COLLISION_CHECK - Direction {direction} is WALKABLE (tile=0)")
                return True
            elif target_tile == 1:
                print(f"Navigator: COLLISION_CHECK - Direction {direction} is BLOCKED (wall, tile=1)")
                return False
            elif target_tile == 2:
                print(f"Navigator: COLLISION_CHECK - Direction {direction} is BLOCKED (sprite, tile=2)")
                return False
            elif 3 <= target_tile <= 6:
                print(f"Navigator: COLLISION_CHECK - Direction {direction} is WALKABLE (player position, tile={target_tile})")
                return True
            else:
                print(f"Navigator: COLLISION_CHECK - Direction {direction} unknown tile value {target_tile}, defaulting to BLOCKED")
                return False
                
        except Exception as e:
            print(f"Navigator: COLLISION_CHECK - Error checking direction {direction}: {e}")
            import traceback
            traceback.print_exc()
            return True  # Default to allowing movement if check fails

    def _get_walkable_directions(self) -> list[str]:
        """Get list of all currently walkable directions"""
        print(f"Navigator: COLLISION_DEBUG - Getting walkable directions...")
        
        directions = ["up", "down", "left", "right"]
        walkable = []
        
        for direction in directions:
            is_walkable = self._is_direction_walkable(direction)
            if is_walkable:
                walkable.append(direction)
            print(f"Navigator: COLLISION_DEBUG - Direction {direction}: {'WALKABLE' if is_walkable else 'BLOCKED'}")
        
        print(f"Navigator: COLLISION_DEBUG - Final walkable directions: {walkable}")
        return walkable

    def _select_intelligent_direction(self, delta_y: int, delta_x: int) -> Optional[str]:
        """
        Intelligently select movement direction based on target and walkability.
        Fixes the vertical movement preference bug by prioritizing the optimal direction.
        """
        print(f"Navigator: INTELLIGENT_DIRECTION - Called with delta_y={delta_y}, delta_x={delta_x}")
        
        # Get all walkable directions
        walkable_directions = self._get_walkable_directions()
        if not walkable_directions:
            print("Navigator: INTELLIGENT_DIRECTION - No walkable directions available")
            return None
        
        # Determine preferred directions based on distance
        preferred_directions = []
        
        # FIXED: Prioritize the direction with larger distance to avoid getting stuck
        abs_dy = abs(delta_y)
        abs_dx = abs(delta_x)
        
        print(f"Navigator: INTELLIGENT_DIRECTION - Distances: abs_dy={abs_dy}, abs_dx={abs_dx}")
        
        if abs_dy > abs_dx:
            # Vertical movement is more important
            if delta_y > 0:
                preferred_directions.append("down")
            elif delta_y < 0:
                preferred_directions.append("up")
            if delta_x > 0:
                preferred_directions.append("right")
            elif delta_x < 0:
                preferred_directions.append("left")
        elif abs_dx > abs_dy:
            # Horizontal movement is more important  
            if delta_x > 0:
                preferred_directions.append("right")
            elif delta_x < 0:
                preferred_directions.append("left")
            if delta_y > 0:
                preferred_directions.append("down")
            elif delta_y < 0:
                preferred_directions.append("up")
        else:
            # Equal distance - try both, prioritizing horizontal to fix vertical preference bug
            if delta_x > 0:
                preferred_directions.append("right")
            elif delta_x < 0:
                preferred_directions.append("left")
            if delta_y > 0:
                preferred_directions.append("down")
            elif delta_y < 0:
                preferred_directions.append("up")
        
        print(f"Navigator: INTELLIGENT_DIRECTION - Preferred directions (in order): {preferred_directions}")
        print(f"Navigator: INTELLIGENT_DIRECTION - Available walkable directions: {walkable_directions}")
        
        # Find the first preferred direction that is walkable
        for direction in preferred_directions:
            if direction in walkable_directions:
                print(f"Navigator: INTELLIGENT_DIRECTION - Selected direction: {direction}")
                return direction
        
        # If no preferred direction is walkable, try any walkable direction
        if walkable_directions:
            fallback = walkable_directions[0]
            print(f"Navigator: INTELLIGENT_DIRECTION - No preferred direction walkable, using fallback: {fallback}")
            return fallback
        
        return None

def diagnose_environment_coordinate_loading(env, navigator):
    """Diagnostic protocol for environment coordinate loading accuracy"""
    
    print("\n" + "="*60)
    print("ENVIRONMENT COORDINATE LOADING DIAGNOSTIC")
    print("="*60)
    
    quest_ids_to_test = [12, 13, 14]
    
    for quest_id in quest_ids_to_test:
        print(f"\n--- QUEST {quest_id:03d} ENVIRONMENT LOADING TEST ---")
        
        # STEP 1: Direct file content reading
        quest_dir_name = f"{quest_id:03d}"
        quest_file_name = f"{quest_dir_name}_coords.json"
        file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name
        
        if file_path.exists():
            with open(file_path, 'r') as f:
                file_content = json.load(f)
            
            # Flatten file coordinates for comparison
            file_coordinates = []
            for map_coords in file_content.values():
                file_coordinates.extend([tuple(coord) for coord in map_coords])
            
            print(f"File content: {len(file_coordinates)} coordinates")
            print(f"  Maps: {list(file_content.keys())}")
            print(f"  First coordinate: {file_coordinates[0] if file_coordinates else 'None'}")
            print(f"  Last coordinate: {file_coordinates[-1] if file_coordinates else 'None'}")
            
            # STEP 2: Environment loading test
            print(f"\nTesting environment loading...")
            
            # Store current environment state
            original_path = getattr(env, 'combined_path', []).copy()
            original_quest_id = getattr(env, 'current_loaded_quest_id', None)
            original_index = getattr(env, 'current_path_target_index', 0)
            
            # Test environment coordinate loading
            env_load_success = env.load_coordinate_path(quest_id)
            
            if env_load_success and hasattr(env, 'combined_path') and env.combined_path:
                env_coordinates = [tuple(coord) for coord in env.combined_path]
                
                print(f"Environment loaded: {len(env_coordinates)} coordinates")
                print(f"  First coordinate: {env_coordinates[0] if env_coordinates else 'None'}")
                print(f"  Last coordinate: {env_coordinates[-1] if env_coordinates else 'None'}")
                
                # STEP 3: Content comparison
                if file_coordinates == env_coordinates:
                    print(f"  ✓ MATCH: Environment loaded coordinates match file content exactly")
                else:
                    print(f"  ⚠ MISMATCH: Environment coordinates differ from file content")
                    print(f"    File coords: {len(file_coordinates)}")
                    print(f"    Env coords: {len(env_coordinates)}")
                    
                    # Show first few differences
                    for i, (file_coord, env_coord) in enumerate(zip(file_coordinates, env_coordinates)):
                        if file_coord != env_coord:
                            print(f"    Diff at index {i}: File={file_coord}, Env={env_coord}")
                            if i >= 3:  # Limit output
                                break
            else:
                print(f"  ✗ Environment loading failed")
            
            # STEP 4: Navigator loading test
            print(f"\nTesting navigator loading...")
            
            # Store current navigator state
            nav_original_coords = navigator.sequential_coordinates.copy() if navigator.sequential_coordinates else []
            nav_original_index = navigator.current_coordinate_index
            nav_original_quest_id = getattr(navigator, 'active_quest_id', None)
            
            # Test navigator coordinate loading
            nav_load_success = navigator.load_coordinate_path(quest_id)
            
            if nav_load_success and navigator.sequential_coordinates:
                nav_coordinates = [tuple(coord) for coord in navigator.sequential_coordinates]
                
                print(f"Navigator loaded: {len(nav_coordinates)} coordinates")
                print(f"  First coordinate: {nav_coordinates[0] if nav_coordinates else 'None'}")
                print(f"  Last coordinate: {nav_coordinates[-1] if nav_coordinates else 'None'}")
                
                # STEP 5: Navigator-Environment comparison
                if hasattr(env, 'combined_path') and env.combined_path:
                    if nav_coordinates == env_coordinates:
                        print(f"  ✓ Navigator-Environment coordination: SYNCHRONIZED")
                    else:
                        print(f"  ⚠ Navigator-Environment coordination: DESYNCHRONIZED")
                
                # STEP 6: Navigator-File comparison
                if nav_coordinates == file_coordinates:
                    print(f"  ✓ Navigator-File accuracy: ACCURATE")
                else:
                    print(f"  ⚠ Navigator-File accuracy: INACCURATE")
            else:
                print(f"  ✗ Navigator loading failed")
            
            # Restore states
            env.combined_path = original_path
            env.current_loaded_quest_id = original_quest_id
            env.current_path_target_index = original_index
            
            navigator.sequential_coordinates = nav_original_coords
            navigator.current_coordinate_index = nav_original_index
            navigator.active_quest_id = nav_original_quest_id
            
        else:
            print(f"✗ Coordinate file not found: {file_path}")
    
    print("\n" + "="*60)
    print("DIAGNOSTIC COMPLETE")
    print("="*60)

def debug_coordinate_system(env, navigator):
    """Comprehensive debug of the coordinate system with synchronized path checking"""
    from environment.data.recorder_data.global_map import local_to_global
    
    print("\n" + "="*60)
    print("COORDINATE SYSTEM DEBUG")
    print("="*60)
    
    # Get current player position
    try:
        player_x, player_y, map_n = env.get_game_coords()
        current_gy, current_gx = local_to_global(player_y, player_x, map_n)
        print(f"Current Player Position: ({current_gy}, {current_gx}) on map {map_n}")
    except Exception as e:
        print(f"ERROR getting player position: {e}")
        return
    
    # Check navigator coordinates
    print(f"\n--- NAVIGATOR STATUS ---")
    print(f"Navigator coordinates loaded: {len(navigator.sequential_coordinates)}")
    if navigator.sequential_coordinates:
        print(f"Navigator first coordinate: {navigator.sequential_coordinates[0]}")
        print(f"Navigator current index: {navigator.current_coordinate_index}")
        if navigator.current_coordinate_index < len(navigator.sequential_coordinates):
            target = navigator.sequential_coordinates[navigator.current_coordinate_index]
            print(f"Navigator current target: {target}")
            distance = abs(target[0] - current_gy) + abs(target[1] - current_gx)
            print(f"Distance to navigator target: {distance}")
    else:
        print("Navigator: NO COORDINATES LOADED!")
    
    # Check environment coordinates
    print(f"\n--- ENVIRONMENT STATUS ---")
    if hasattr(env, 'combined_path') and env.combined_path:
        print(f"Environment path length: {len(env.combined_path)}")
        print(f"Environment first coordinate: {env.combined_path[0]}")
        print(f"Environment current index: {env.current_path_target_index}")
        if env.current_path_target_index < len(env.combined_path):
            target = env.combined_path[env.current_path_target_index]
            print(f"Environment current target: {target}")
            distance = abs(target[0] - current_gy) + abs(target[1] - current_gx)
            print(f"Distance to environment target: {distance}")
    else:
        print("Environment: NO PATH LOADED!")
    
    # Check coordinate file accessibility using navigator's actual search paths
    print(f"\n--- COORDINATE FILE VALIDATION ---")
    quest_ids_to_check = [12, 13, 14]
    
    for quest_id in quest_ids_to_check:
        quest_dir_name = f"{quest_id:03d}"
        quest_file_name = f"{quest_dir_name}_coords.json"
        
        # Use same path logic as navigator
        primary_quest_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name
        
        file_status = "EXISTS" if primary_quest_path.exists() else "MISSING"
        print(f"Quest file {quest_file_name}: {file_status}")
        
        # Additional content validation if file exists
        if primary_quest_path.exists():
            try:
                with open(primary_quest_path, 'r') as f:
                    content = json.load(f)
                map_keys = list(content.keys())
                total_coords = sum(len(coords) for coords in content.values())
                print(f"  → Content: {len(map_keys)} maps, {total_coords} total coordinates")
                print(f"  → Maps: {map_keys}")
            except Exception as e:
                print(f"  → Content Error: {e}")
