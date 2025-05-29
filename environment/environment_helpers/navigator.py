# # navigator.py - CLEANED VERSION
# import numpy as np
# import json
# import heapq
# import math
# from pathlib import Path
# from typing import Optional, Union, List, Tuple
# from data.map import MapIds
# from environment import RedGymEnv
# from pyboy.utils import WindowEvent
# from global_map import local_to_global, global_to_local, MAP_DATA
# from data.tilesets import Tilesets 
# from data.warps import WARP_DICT

# # Define availability flags for warp data and map IDs
# MAP_IDS_AVAILABLE = True
# WARP_DATA_AVAILABLE = True

# class InteractiveNavigator:
#     def __init__(self, env_instance: RedGymEnv):
#         self.env = env_instance
#         self.pyboy = self.env.pyboy  # Direct access to pyboy via RedGymEnv
        
#         # quest_coords holds the original full list for the active quest
#         self.quest_coords: List[Tuple[int, int]] = []    # Raw quest path coordinates (gy, gx)
#         # sequential_coordinates is the current active path the navigator is following
#         self.sequential_coordinates: List[Tuple[int, int]] = []  # Active path coordinates
#         self.current_coordinate_index: int = 0  # Index into sequential_coordinates for next step
#         self.active_quest_id: Optional[int] = None # Initialize to None
        
#         # Track last global position including map to detect changes
#         self.last_position = None
        
#         # Quest state protection
#         self.quest_locked = False  # Prevent external quest changes during traversal
#         self.movement_failure_count = 0  # Track consecutive movement failures
#         self.max_movement_failures = 10  # Maximum failures before abandoning quest
        
#         # Action mapping for navigation paths (UP, DOWN, LEFT, RIGHT strings)
#         self.ACTION_MAPPING_STR_TO_INT = {
#             "down": 0, "left": 1, "right": 2, "up": 3,
#         }

#         # Keep minimal compatibility attributes
#         self.navigation_status: str = "idle"

#     def _get_player_global_coords(self) -> Optional[tuple[int, int]]:
#         """Get player's current global coordinates with detailed logging"""
#         if not hasattr(self.env, 'get_game_coords'):
#             print("Navigator: ERROR - env object does not have get_game_coords method")
#             return None
#         try:
#             player_local_x, player_local_y, current_map_id_int = self.env.get_game_coords()
#             global_y, global_x = local_to_global(player_local_y, player_local_x, current_map_id_int)
            
#             # Only print when the location (including map) changes
#             coord = (int(global_y), int(global_x), current_map_id_int)
#             if self.last_position is None or coord != self.last_position:
#                 print(f"navigator.py: _get_player_global_coords(): global_coords=({global_y},{global_x}), map_id={current_map_id_int}, local_coords=(y={player_local_y},x={player_local_x})")
#             self.last_position = coord
            
#             # Return (gy, gx) to match coordinate format - CONFIRMED: Y first, X second
#             return int(global_y), int(global_x)
#         except Exception as e:
#             print(f"Navigator: ERROR getting player global coords: {e}")
#             return None

#     def _manhattan_distance(self, pos1: tuple[int, int], pos2: tuple[int, int]) -> int:
#         return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])

#     def snap_to_nearest_coordinate(self) -> bool:
#         """Find the nearest coordinate in quest sequence and set it as current index"""
#         if not self.sequential_coordinates:
#             print("Navigator: ERROR - No path coordinates loaded")
#             return False
            
#         # Check if dialog/battle is active - pause navigation if so
#         try:
#             raw_dialog = self.env.read_dialog() or ''
#             if raw_dialog.strip():
#                 print("Navigator: Navigation paused - dialog/battle active, cannot snap")
#                 return False
#         except Exception:
#             pass
            
#         current_pos = self._get_player_global_coords()
#         if not current_pos:
#             print("Navigator: ERROR - Cannot get current player position for snapping")
#             return False
            
#         print(f"Navigator: *** SNAPPING TO PATH COORDINATE ***")
#         print(f"Navigator: Path coordinates: {self.sequential_coordinates}")
#         # Filter path coordinates to those on the current map
#         cur_map = self.env.get_game_coords()[2]
#         tile_size = MAP_DATA.get(cur_map, {}).get("tileSize")
#         if tile_size:
#             width, height = tile_size[0], tile_size[1]
#             valid_indices = []
#             for idx, (gy, gx) in enumerate(self.sequential_coordinates):
#                 local = global_to_local(gy, gx, cur_map)
#                 if local is not None:
#                     r, c = local
#                     if 0 <= r < height and 0 <= c < width:
#                         valid_indices.append(idx)
#             indices_to_search = valid_indices if valid_indices else list(range(len(self.sequential_coordinates)))
#         else:
#             indices_to_search = list(range(len(self.sequential_coordinates)))
        
#         # Find the nearest coordinate among candidates
#         min_distance = float('inf')
#         nearest_index = 0
#         for i in indices_to_search:
#             coord = self.sequential_coordinates[i]
#             distance = self._manhattan_distance(current_pos, coord)
#             print(f"Navigator: Distance to coordinate {i} {coord}: {distance}")
#             if distance < min_distance:
#                 min_distance = distance
#                 nearest_index = i
                
#         nearest_coord = self.sequential_coordinates[nearest_index]
#         self.current_coordinate_index = nearest_index
        
#         print(f"navigator.py: snap_to_nearest_coordinate(): nearest_idx={nearest_index}, coord={nearest_coord}, distance={min_distance}")
#         
#         # After snapping, reset movement failures and validate index
#         self.movement_failure_count = 0
#         if self.current_coordinate_index >= len(self.sequential_coordinates):
#             self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)
        
#         print(f"navigator.py: snap_to_nearest_coordinate(): SNAP COMPLETE")
#         return True

#     def move_to_next_coordinate(self) -> bool:
#         """
#         Move player to the next coordinate in quest sequence, always following the correct path for the current map.
#         This is called when the user presses 5, or by the auto-navigator.
#         - Always snap to the nearest coordinate for the current map before moving.
#         - If the path is exhausted, search forward for the next quest with a segment for the current map.
#         - If no such segment exists, stop navigation and do nothing.
#         - After a warp, always reload the segment for the new map.
#         """
#         # Check for dialog/battle and pause navigation if so
#         try:
#             if (self.env.read_dialog() or "").strip():
#                 print("Navigator: Navigation paused - dialog/battle active")
#                 return False
#         except Exception:
#             pass
#
#         # Pause here to check to see if a warp occurred. If so, reload the segment for the new map.
#         if self.env.get_game_coords()[2] != self.env.prev_map_id:
#             print(f"navigator.py: move_to_next_coordinate(): WARP detected! Reloading segment for map {self.env.get_game_coords()[2]}")
#             self.load_segment_for_current_map()
#             return True
#         
#         # Immediately handle warp tile if adjacent to a warp
#         if self.warp_tile_handler():
#             print("navigator.py: move_to_next_coordinate(): Early warp handled.")
#             return True
#
#         # Always snap to the nearest coordinate for the current map before moving
#         # This ensures we're aligned with the correct path segment for the current map
#         snapped = self.snap_to_nearest_coordinate()
#         if not snapped:
#             print("Navigator: Failed to snap to nearest coordinate, cannot move.")
#             return False
#
#         # After snapping, if there are no coordinates, try to load them.
#         # This can happen if snap_to_nearest_coordinate itself called load_segment_for_current_map
#         # but no segment was found for the *initial* active_quest_id.
#         if not self.sequential_coordinates:
#             try:
#                 self.load_segment_for_current_map()
#                 # If loading a segment succeeded, we MUST re-snap to ensure we're on *that* segment's path.
#                 snapped_again = self.snap_to_nearest_coordinate()
#                 if not snapped_again:
#                     print("Navigator: Failed to snap after loading new segment.")
#                     return False
#             except RuntimeError as e:
#                 print(f"Navigator: No path available after trying to load segment: {e}")
#                 return False
#         
#         # If, after all snapping and loading attempts, there's still no path, we can't move.
#         if not self.sequential_coordinates:
#             print("Navigator: CRITICAL - No sequential coordinates available to follow.")
#             return False
#
#         # Loop to handle advancing index if already at target, and to load next quest if path ends.
#         # This loop continues as long as we are at the end of the current segment or successfully move.
#         while True:
#             # Check if the current path segment is exhausted
#             if self.current_coordinate_index >= len(self.sequential_coordinates):
#                 print(f"Navigator: Path segment for quest {self.active_quest_id} on map {self.env.get_game_coords()[2]} exhausted.")
#                 map_id = self.env.get_game_coords()[2]
#                 base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
#                 # Check for additional map segments in the same quest
#                 quest_id_str = f"{self.active_quest_id:03d}"
#                 quest_file = base / quest_id_str / f"{quest_id_str}_coords.json"
#                 try:
#                     if quest_file.exists():
#                         data = json.loads(quest_file.read_text())
#                         keys = list(data.keys())
#                         curr_key = str(map_id)
#                         if curr_key in keys:
#                             idx = keys.index(curr_key)
#                             if idx < len(keys) - 1:
#                                 next_map_str = keys[idx + 1]
#                                 next_map_id = int(next_map_str)
#                                 coords = data[next_map_str]
#                                 if coords:
#                                     self.sequential_coordinates = [(c[0], c[1]) for c in coords]
#                                     self.coord_map_ids = [next_map_id] * len(coords)
#                                     self.current_coordinate_index = 0
#                                     print(f"navigator.py: move_to_next_coordinate(): loaded segment for current quest {self.active_quest_id:03d} on map {next_map_id} ({len(coords)} steps)")
#                                     if not self.snap_to_nearest_coordinate():
#                                         print(f"navigator.py: move_to_next_coordinate(): failed snapping to new segment for quest {self.active_quest_id:03d}")
#                                         return False
#                                     # Continue loop to process new segment
#                                     continue
#                 except Exception as e:
#                     print(f"navigator.py: move_to_next_coordinate(): error loading next segment: {e}")
#                 # Search FORWARD for the next quest that has coordinates for the CURRENT map_id
#                 found_next_segment = False
#                 for qid in range(self.active_quest_id + 1, 47): # Max quest ID + 1
#                     print(f"navigator.py: move_to_next_coordinate(): qid={qid:03d} searching for next quest segment on map {map_id}")
#                     fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
#                     if not fp.exists():
#                         continue
#                     try:
#                         data = json.loads(fp.read_text())
#                         if str(map_id) in data:
#                             coords = data[str(map_id)]
#                             if coords: # Ensure there are coordinates for this map
#                                 self.sequential_coordinates = [(c[0], c[1]) for c in coords]
#                                 self.coord_map_ids = [map_id] * len(coords)
#                                 self.current_coordinate_index = 0
#                                 self.active_quest_id = qid
#                                 print(f"navigator.py: move_to_next_coordinate(): loaded next quest segment quest {qid:03d} for map {map_id} ({len(coords)} steps)")
#                                 if not self.snap_to_nearest_coordinate():
#                                     print(f"navigator.py: move_to_next_coordinate(): failed snapping to new quest segment for quest {qid:03d}")
#                                     return False
#                                 found_next_segment = True
#                                 break
#                     except Exception as e:
#                         print(f"navigator.py: move_to_next_coordinate(): error reading quest {qid:03d}: {e}")
#                         continue
#                 if not found_next_segment:
#                     print(f"navigator.py: move_to_next_coordinate(): no further segments for map {map_id} after quest {self.active_quest_id}, stopping navigation")
#                     return False
#
#             # If, after attempting to load next segment, we still have no coordinates or index is bad
#             if not self.sequential_coordinates or self.current_coordinate_index >= len(self.sequential_coordinates):
#                 print("Navigator: Path invalid or exhausted even after trying to load next segment.")
#                 return False
#
#             # Handle warp tiles. If a warp occurs, snap_to_nearest_coordinate will be called
#             # by warp_tile_handler, and we should restart the move logic.
#             # The warp_tile_handler itself will call load_segment_for_current_map if needed.
#             if self.warp_tile_handler():
#                 print("Navigator: Warp handled. Restarting move_to_next_coordinate logic.")
#                 # After a warp, the map might have changed, so a new snap and path load is essential.
#                 # Re-invoke self.move_to_next_coordinate() or ensure the loop continues correctly.
#                 # For now, simply returning True and letting the next game tick handle it is safer.
#                 # The snap called within warp_tile_handler should suffice for realignment.
#                 return True 
#
#             current_pos = self._get_player_global_coords()
#             if not current_pos:
#                 print("Navigator: ERROR - Cannot get current player position for movement.")
#                 return False
#
#             # Ensure index is valid before accessing sequential_coordinates
#             if self.current_coordinate_index >= len(self.sequential_coordinates):
#                 print(f"Navigator: Index {self.current_coordinate_index} out of bounds for path length {len(self.sequential_coordinates)}. Attempting to reload/resnap.")
#                 # This state should ideally be caught by the exhaustion check above.
#                 # Attempt to recover by trying to load the segment again.
#                 try:
#                     self.load_segment_for_current_map()
#                     if not self.snap_to_nearest_coordinate(): return False
#                     if self.current_coordinate_index >= len(self.sequential_coordinates): # Still bad
#                         return False
#                 except RuntimeError:
#                     return False
#
#             target_coord = self.sequential_coordinates[self.current_coordinate_index]
#             print(f"Navigator: Attempting move from {current_pos} to {target_coord} (idx {self.current_coordinate_index})")
#
#             if current_pos == target_coord:
#                 print(f"Navigator: Already at target {target_coord}. Advancing index.")
#                 self.current_coordinate_index += 1
#                 # Loop back to check if new index is end of path or needs next segment
#                 continue 
#             
#             # If not at target, attempt to move one step.
#             # _step_towards will increment current_coordinate_index if it reaches the target.
#             step_success = self._step_towards(target_coord)
#             return step_success # Return status of this single step attempt

#     def _step_towards(self, target: Tuple[int, int]) -> bool:
#         cur = self._get_player_global_coords()
#         if cur is None:
#             return False
#
#         # If already at target, advance index and continue
#         if cur == target:
#             self.current_coordinate_index += 1
#             return True
#
#         dy, dx = target[0] - cur[0], target[1] - cur[1]
#
#         # Vertical first movement with collision detection
#         moved = False
#         if dy != 0:
#             direction = "down" if dy > 0 else "up"
#             action = self.ACTION_MAPPING_STR_TO_INT[direction]
#             moved = self._execute_movement(action)
#         
#         # Only attempt horizontal movement if vertical didn't work
#         if not moved and dx != 0:
#             direction = "right" if dx > 0 else "left"
#             action = self.ACTION_MAPPING_STR_TO_INT[direction]
#             moved = self._execute_movement(action)
#
#         # Detect possible warp by checking map change
#         try:
#             post_map_id = self.env.get_game_coords()[2]
#         except Exception:
#             post_map_id = None
#         # If map changed, snap to nearest coordinate on new map and return success
#         try:
#             prev_map_id = self.env.prev_map_id
#         except Exception:
#             prev_map_id = None
#         if post_map_id is not None and prev_map_id is not None and post_map_id != prev_map_id:
#             # Realign on new map
#             self.snap_to_nearest_coordinate()
#             # Update prev_map_id
#             setattr(self.env, 'prev_map_id', post_map_id)
#             return True
#         # Update position and handle success/failure
#         new_pos = self._get_player_global_coords()
#         if moved and new_pos != cur:
#             print(f"Navigator: Moved to {new_pos}")
#             if new_pos == target:
#                 self.current_coordinate_index += 1
#             return True
#         
#         # Movement failed: skip this coordinate to avoid deadlock
#         print(f"Navigator: Movement failed at target {target}, skipping coordinate")
#         # Advance index and reset failure counter
#         self.current_coordinate_index += 1
#         self.movement_failure_count = 0
#         return True

#     def _execute_movement(self, action: int) -> bool:
#         """Execute movement and return True if position changed"""
#         pre_pos = self._get_player_global_coords()
#         self.env.run_action_on_emulator(action)
#         for _ in range(5):  # Reduced tick count for more responsive movement
#             self.pyboy.tick(self.env.action_freq)
#         post_pos = self._get_player_global_coords()
#         return post_pos != pre_pos

#     def get_current_status(self) -> str:
#         """Get detailed status information for debugging"""
#         current_pos = self._get_player_global_coords()
#         
#         status = f"\n*** NAVIGATOR PATH STATUS ***\n"
#         status += f"Current Position: {current_pos}\n"
#         status += f"Path Coordinates Loaded: {len(self.sequential_coordinates)}\n"
#         status += f"Current Index: {self.current_coordinate_index}\n"
#         
#         if self.sequential_coordinates:
#             if self.current_coordinate_index < len(self.sequential_coordinates):
#                 target = self.sequential_coordinates[self.current_coordinate_index]
#                 status += f"Current Target: {target}\n"
#                 if current_pos:
#                     distance = self._manhattan_distance(current_pos, target)
#                     status += f"Distance to Target: {distance}\n"
#             else:
#                 status += f"Status: Path completed!\n"
#         else:
#             status += f"ERROR: No path coordinates loaded\n"
#             
#         return status

#     def load_coordinate_path(self, quest_id: int) -> bool:
#         """Enhanced coordinate loading with comprehensive validation"""
#         # Quest state protection: Prevent loading if actively following another quest
#         if self.quest_locked and self.sequential_coordinates and self.current_coordinate_index < len(self.sequential_coordinates):
#             print(f"Navigator: BLOCKED - Currently following Quest {self.active_quest_id}, cannot load Quest {quest_id}")
#             return False
#             
#         print(f"Navigator: Attempting to load coordinates for Quest {str(quest_id).zfill(3)} via environment.")
#         
#         # VALIDATED PATH PROTOCOL: Confirmed directory structure and filename pattern
#         quest_dir_name = f"{str(quest_id).zfill(3)}"
#         quest_file_name = f"{quest_dir_name}_coords.json"
#         
#         # PRIMARY SEARCH PATH: Confirmed structure - replays/recordings/paths_001_through_046/{quest_id:03d}/{quest_id:03d}_coords.json
#         primary_quest_path = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / quest_dir_name / quest_file_name
#         
#         # COMPREHENSIVE PATH ARRAY: Primary target with legacy compatibility fallbacks
#         expected_paths = [
#             # PRIORITY 1: Validated actual file location
#             primary_quest_path,
#             
#             # PRIORITY 2: Legacy compatibility paths for backward integration
#             Path(__file__).parent / quest_file_name,
#             Path(__file__).parent.parent / quest_file_name,
#             Path(__file__).parent / "coordinates" / quest_file_name,
#             
#             # PRIORITY 3: Alternative directory structure fallbacks
#             Path(__file__).parent / "replays" / "recordings" / quest_file_name,
#             Path(__file__).parent / quest_dir_name / quest_file_name
#         ]
#         
#         quest_file_exists = any(path.exists() for path in expected_paths)
#         if not quest_file_exists:
#             print(f"Navigator: ERROR - Coordinate file {quest_file_name} not found in expected locations:")
#             for path in expected_paths:
#                 print(f"  - {path} (exists: {path.exists()})")
#             return False
#         
#         # Call the environment's method to load the path for the given quest_id
#         if self.env.load_coordinate_path(quest_id):
#             # COORDINATE VALIDATION: Ensure environment loaded coordinates successfully
#             if hasattr(self.env, 'combined_path') and self.env.combined_path:
#                 self.quest_coords = list(self.env.combined_path)
#                 self.sequential_coordinates = list(self.env.combined_path)
#                 
#                 # QUEST STATE SYNCHRONIZATION
#                 try:
#                     self.active_quest_id = int(self.env.current_loaded_quest_id)
#                 except Exception:
#                     self.active_quest_id = quest_id
#                     
#                 self.current_coordinate_index = 0
#                 
#                 # COORDINATE VALIDATION METRICS
#                 coord_count = len(self.sequential_coordinates)
#                 current_map = self.env.get_game_coords()[2] if hasattr(self.env, 'get_game_coords') else 'unknown'
#                 
#                 print(f"Navigator: Successfully synced {coord_count} coordinates from environment for Quest {str(quest_id).zfill(3)}.")
#                 print(f"Navigator: Quest loaded for current map: {current_map}")
#                 print(f"Navigator: First coordinate: {self.sequential_coordinates[0] if self.sequential_coordinates else 'None'}")
#                 print(f"Navigator: Last coordinate: {self.sequential_coordinates[-1] if self.sequential_coordinates else 'None'}")
#                 
#                 # POSITION ALIGNMENT PROTOCOL
#                 self.snap_to_nearest_coordinate()
#                 self.navigation_status = "idle"
#                 
#                 # Activate quest lock for state protection
#                 self.quest_locked = True
#                 self.movement_failure_count = 0  # Reset failure counter for new quest
#                 print(f"Navigator: Quest {quest_id} LOCKED - preventing external quest changes")
#                 
#                 # Sync environment's current_loaded_quest_id for UI and QuestManager
#                 setattr(self.env, 'current_loaded_quest_id', quest_id)
#                 
#                 return True
#             else:
#                 print(f"Navigator: Environment reported success for Quest {str(quest_id).zfill(3)}, but no coordinates were loaded.")
#                 self._reset_quest_state(quest_id)
#                 return False
#         else:
#             print(f"Navigator: Environment failed to load coordinate file for Quest {str(quest_id).zfill(3)}.")
#             self._reset_quest_state(quest_id)
#             return False

#     def _reset_quest_state(self, attempted_quest_id: int):
#         """Reset navigator state after failed quest load"""
#         self.active_quest_id = attempted_quest_id
#         self.sequential_coordinates = []
#         self.quest_coords = []
#         self.current_coordinate_index = 0
#         self.navigation_status = "idle"
#         self.quest_locked = False  # Unlock quest state
#         self.movement_failure_count = 0  # Reset failure counter



# navigator.py
# --------------------------------------------------------------------
# Clean, fully‑featured, map‑aware navigator for the LLM‑plays‑pokemon
# project.  Vertical‑first movement restored.
# --------------------------------------------------------------------
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import List, Optional, Tuple
import random

from pyboy.utils import WindowEvent

from data.map import MapIds
from data.warps import WARP_DICT
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from environment import RedGymEnv
from grok_plays_pokemon.recorder.data.recorder_data.global_map import local_to_global, global_to_local
# --------------------------------------------------------------------


class InteractiveNavigator:
    # ...............................................................
    def __init__(self, env_instance: RedGymEnv):
        self.env: RedGymEnv = env_instance
        self.pyboy = self.env.pyboy

        self.sequential_coordinates: List[Tuple[int, int]] = []
        self.coord_map_ids:          List[int] = []
        self.current_coordinate_index: int = 0

        self.active_quest_id: Optional[int] = None

        self.last_position = None
        self.quest_locked = False
        self.movement_failure_count = 0
        self.max_movement_failures = 10
        self.navigation_status = "idle"

        self.ACTION_MAPPING_STR_TO_INT = {
            "down": 0,
            "left": 1,
            "right": 2,
            "up": 3,
        }
        # Track how many times each map's segment has been loaded (for multi-segment maps)
        self.map_segment_count: dict[int, int] = {}

        self.door_warp = False
        self.last_warp_time = 0.0  # timestamp of last warp to enforce cooldown
        self.WARP_COOLDOWN_SECONDS = 0.5  # seconds between warp attempts (shorter to avoid bounce)
        self.last_warp_origin_map: Optional[int] = None  # map id we warped from to avoid bounce
        # Track where we landed after a warp so we do not immediately warp back
        self._post_warp_exit_pos: Optional[Tuple[int, int]] = None  # global (gy,gx) where player appeared after last warp

        # Once we leave the player's house, never go back inside during this session
        self._left_home = False
        
        # Coords stuff
        # local (x, y)
        self.current_coords = None

    # ...............................................................
    #  U T I L I T I E S
    # ...............................................................
    def _get_player_global_coords(self) -> Optional[Tuple[int, int]]:
        if not hasattr(self.env, "get_game_coords"):
            print("Navigator: env lacks get_game_coords()")
            return None
        try:
            lx, ly, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(ly, lx, map_id)
            pos3 = (gy, gx, map_id)
            if self.last_position != pos3:
                print(f"navigator.py: _get_player_global_coords(): global_coords=({gy},{gx}), map_id={map_id}, local_coords=(y={ly},x={lx})")
            self.last_position = pos3
            return gy, gx
        except Exception as e:
            print(f"Navigator: ERROR reading coords: {e}")
            return None

    @staticmethod
    def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    # ...............................................................
    #  S N A P
    # ...............................................................
    def snap_to_nearest_coordinate(self) -> bool:
        """
        Snap to the closest coordinate on the current map, loading the correct segment if needed.
        This is called when the user presses 4, or after a warp, to realign the navigator to the closest point on the path for the current map.
        If no path is found for the current map, this function returns False and does nothing.
        """
        if not self.sequential_coordinates:
            try:
                self.load_segment_for_current_map()
            except RuntimeError as e:
                print(f"Navigator: snap_to_nearest_coordinate: {e}")
                return False
        
        cur_map = self.env.get_game_coords()[2]
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            print("Navigator: snap_to_nearest_coordinate: Cannot get player position")
            return False
        candidate_ids = [i for i, m in enumerate(self.coord_map_ids) if m == cur_map]
        if not candidate_ids:
            print(f"Navigator: No path points on current map {cur_map}, attempting to load segment for current map")
            try:
                self.load_segment_for_current_map()
            except RuntimeError as e:
                print(f"Navigator: snap_to_nearest_coordinate: {e}")
                return False
            # Recompute candidate_ids after loading segment
            candidate_ids = [i for i, m in enumerate(self.coord_map_ids) if m == cur_map]
            if not candidate_ids:
                print(f"Navigator: No path points on current map {cur_map} after loading segment")
                return False
        
        # Find the closest coordinate to the player
        nearest_i = min(candidate_ids, key=lambda i: self._manhattan(cur_pos, self.sequential_coordinates[i]))
        dist = self._manhattan(cur_pos, self.sequential_coordinates[nearest_i])
        self.current_coordinate_index = nearest_i
        print(f"navigator.py: snap_to_nearest_coordinate(): nearest_idx={nearest_i}, coord={self.sequential_coordinates[nearest_i]}, distance={dist}")
        
        # After snapping, reset movement failures and validate index
        self.movement_failure_count = 0
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)
        
        print(f"navigator.py: snap_to_nearest_coordinate(): SNAP COMPLETE")
        return True

    def warp_tile_handler(self):
        """
        Handles player movement near warp tiles to trigger warps
        without getting stuck in a loop.
        """
        # --- 1. Basic Checks and Cooldown ---
        cur = self._get_player_global_coords()
        if cur is None:
            return False

        # Cooldown guard: skip if warping too soon
        if (time.time() - self.last_warp_time) < self.WARP_COOLDOWN_SECONDS:
            return False

        # Bounce-back guard: if we just warped and are still standing next to the exit tile, do
        # NOT allow another warp until we have moved at least one tile away.  This prevents the
        # navigator from oscillating between the inside and outside of a doorway.
        if self._post_warp_exit_pos is not None:
            if self._manhattan(cur, self._post_warp_exit_pos) <= 1:
                return False  # still too close – skip warp attempt
            else:
                # We have moved far enough away; clear the bounce guard for future warps.
                self._post_warp_exit_pos = None
                self.last_warp_origin_map = None

        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            local = (local_x, local_y)
        except Exception:
            print("Navigator: Could not get game coords.")
            return False

        # --- 2. Get Warp Tiles ---
        warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
        warp_tiles = []
        for entry in warp_entries:
            x, y = entry.get('x'), entry.get('y')
            if x is not None and y is not None:
                warp_tiles.append((x, y))

        if not warp_tiles:
            return False

        # --- 3. Check if 1 Tile Away from Any Warp ---
        nearest_warp = None
        for wt in warp_tiles:
            if self._manhattan(local, wt) == 1:
                nearest_warp = wt
                break # Found one we are 1 step away from

        if nearest_warp is None:
            return False # Not adjacent to any warp

        # Prevent bouncing back to the map we just warped from
        # Identify warp entry for nearest warp
        warp_entry = next((e for e in warp_entries if (e.get('x'), e.get('y')) == nearest_warp), None)
        if warp_entry:
            # Block any attempt to go back inside player's house after we've left it once
            if self._left_home and warp_entry.get('target_map_id') in {37, 38}:
                print(f"navigator.py: warp_tile_handler(): BLOCKED re-entry to home: left_home={self._left_home}, cur_map={cur_map}, target={warp_entry.get('target_map_id')}")
                return False
            target_map_id = warp_entry.get('target_map_id')
            if target_map_id == self.last_warp_origin_map:
                return False

            # Block any warp to a map not in the current quest's path
            base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
            quest_file = base / f"{self.active_quest_id:03d}" / f"{self.active_quest_id:03d}_coords.json"
            try:
                quest_data = json.loads(quest_file.read_text())
                allowed_maps = {int(k.split('_')[0]) for k in quest_data.keys()}
            except Exception as e:
                print(f"navigator.py: warp_tile_handler(): could not load quest {self.active_quest_id:03d} JSON: {e}")
                allowed_maps = set()
            if target_map_id not in allowed_maps:
                print(f"navigator.py: warp_tile_handler(): skipping warp to map {target_map_id} (not on quest {self.active_quest_id:03d} route)")
                return False

            # Detect if this is a door warp (two adjacent warp tiles)
            door_warp = any(
                self._manhattan(nearest_warp, wt2) == 1
                for wt2 in warp_tiles
                if wt2 != nearest_warp
            )

            # New: allow tile warps into any map specified in the current quest JSON
            if not door_warp:
                base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
                quest_id = self.active_quest_id
                file_path = base / f"{quest_id:03d}" / f"{quest_id:03d}_coords.json"
                try:
                    data = json.loads(file_path.read_text())
                except Exception:
                    data = {}
                if target_map_id not in {int(k.split('_')[0]) for k in data.keys()}:
                    print(f"navigator.py: warp_tile_handler(): warp to map {target_map_id} not in quest {quest_id:03d} JSON, skipping warp")
                    return False

        # --- 4. Determine Warp Type (Door or Tile) for the Nearest Warp ---
        is_door = False
        for wt_other in warp_tiles:
            if wt_other != nearest_warp and self._manhattan(nearest_warp, wt_other) == 1:
                is_door = True
                print(f"Navigator: Near a potential door warp ({nearest_warp}).")
                break
        if not is_door:
            print(f"Navigator: Near a tile warp ({nearest_warp}).")


        # --- 5. Calculate Direction to Move Onto the Warp ---
        dx = nearest_warp[0] - local[0]
        dy = nearest_warp[1] - local[1]

        if dx == 0 and dy == -1: direction_to_step = "up"
        elif dx == 0 and dy == 1: direction_to_step = "down"
        elif dx == -1 and dy == 0: direction_to_step = "left"
        elif dx == 1 and dy == 0: direction_to_step = "right"
        else:
            print(f"Error: Min dist is 1, but no cardinal direction to {nearest_warp}")
            return False

        # --- 6. Execute Warp ---
        prev_map = cur_map # Store map *before* moving
        # record origin to prevent immediate reverse
        self.last_warp_origin_map = prev_map

        if is_door:
            # Door Warp Logic: Step onto tile, then press DOWN.
            # Check user note: "up is NOT NEEDED" - If 'up' is needed, abort.
            if direction_to_step == "up":
                print("Navigator: Near door, but needs UP step. Aborting based on note.")
                return False

            print(f"Navigator: Door warp - Step 1: Moving {direction_to_step} onto {nearest_warp}")
            self.env.run_action_on_emulator(self.ACTION_MAPPING_STR_TO_INT[direction_to_step])
            for _ in range(15): self.pyboy.tick(self.env.action_freq) # Wait slightly longer

            print(f"Navigator: Door warp - Step 2: Pressing DOWN")
            self.env.run_action_on_emulator(self.ACTION_MAPPING_STR_TO_INT["down"])
            for _ in range(15): self.pyboy.tick(self.env.action_freq)

        else: # Tile Warp
            # Tile Warp Logic: Simply step onto the tile.
            # We ignore 'direction_facing' and just make the step.
            print(f"Navigator: Tile warp - Moving {direction_to_step} onto {nearest_warp}")
            self.env.run_action_on_emulator(self.ACTION_MAPPING_STR_TO_INT[direction_to_step])
            for _ in range(20): self.pyboy.tick(self.env.action_freq) # Wait for potential warp

        # --- 7. Check for Map Change & Set Cooldown ---
        try:
            post_step_map_id = self.env.get_game_coords()[2]
        except Exception:
            post_step_map_id = None

        if post_step_map_id is not None and post_step_map_id != prev_map:
            print(f"Navigator: Detected warp from map {prev_map} to {post_step_map_id}")
            # If we just exited Red's house (37 -> 0), set the flag so we never re-enter
            if prev_map == 37 and post_step_map_id == 0:
                self._left_home = True
                print("navigator.py: warp_tile_handler(): left_home set=True after exiting home (37->0)")
            self.last_warp_time = time.time() # Start cooldown!
            setattr(self.env, 'prev_map_id', post_step_map_id) # Update prev_map
            # Remember where we landed so we do not bounce right back
            landed = self._get_player_global_coords()
            if landed:
                self._post_warp_exit_pos = landed
            # Only realign if there are still path points to follow
            if self.current_coordinate_index < len(self.sequential_coordinates):
                self.snap_to_nearest_coordinate()
            return True  # Warp successful
        else:
            # We moved but didn't warp. This might happen if we stepped onto
            # a door tile but didn't press down yet (though the code tries both).
            # Or if a tile warp didn't trigger.
            # Returning False prevents trying again immediately without the cooldown.
            print(f"Navigator: Attempted warp but map did not change (Prev:{prev_map}, Post:{post_step_map_id}).")
            return False

    def roam_in_grass(self) -> bool:
        """Roam randomly in grass area for quest 23."""
        direction_str = random.choice(list(self.ACTION_MAPPING_STR_TO_INT.keys()))
        action = self.ACTION_MAPPING_STR_TO_INT[direction_str]
        moved = self._execute_movement(action)
        if moved:
            print(f"Navigator: Roaming in grass: moved {direction_str}")
        else:
            print(f"Navigator: Roaming in grass: movement {direction_str} failed")
        return True

    # (x, y, map_id)
    def get_current_local_coords(self):
        return (self.env.get_game_coords()[0], self.env.get_game_coords()[1], self.env.get_game_coords()[2])

    # ...............................................................
    #  M O V E
    # ...............................................................
    def move_to_next_coordinate(self) -> bool:
        """
        Simplified quest path following:
        Load and flatten the full coordinate list for the current quest, then step sequentially.
        Automatically load the next quest's path on completion.
        """
        # If the quest ID changed since we last loaded, reset so we load the new path
        env_qid = getattr(self.env, 'current_loaded_quest_id', None)
        if hasattr(self, '_last_loaded_quest_id') and self._last_loaded_quest_id != env_qid:
            self._reset_state()
        # Ensure path loaded for current quest; if none for this map, fallback to previous quest segment
        if not self.sequential_coordinates:
            env_qid = getattr(self.env, 'current_loaded_quest_id', None)
            if env_qid is None:
                return False
            # Try loading the quest's full coordinate list for this map
            if not self.load_coordinate_path(env_qid):
                # No full-quest path for this map, try loading per-map segment from most recent quest
                try:
                    self.load_segment_for_current_map()
                    return True
                except RuntimeError:
                    return False
        # For Quest 012, once you reach Oak's spot, halt movement so player can press A
        if self.active_quest_id == 12:
            pos = self._get_player_global_coords()
            # global (gy, gx) for Oak interaction tile
            if pos == (348, 110):
                return False
        # If current quest path complete, advance to next quest based on the last loaded quest
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            if self.active_quest_id == 23:
                return self.roam_in_grass()
            # New: attempt to load next map segment for the same quest
            base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
            quest_id = self.active_quest_id
            file_path = base / f"{quest_id:03d}" / f"{quest_id:03d}_coords.json"
            try:
                data = json.loads(file_path.read_text())
                # Preserve segment order as in the JSON file
                ordered_keys = list(data.keys())
                cur_map_str = str(self.env.get_game_coords()[2])
                if cur_map_str in ordered_keys:
                    idx = ordered_keys.index(cur_map_str)
                    if idx < len(ordered_keys) - 1:
                        next_map_str = ordered_keys[idx+1]
                        coords = data[next_map_str]
                        self.sequential_coordinates = [(gy, gx) for gy, gx in coords]
                        # Normalize segment key to integer map id
                        norm_key = next_map_str.split('_')[0]
                        self.coord_map_ids = [int(norm_key)] * len(coords)
                        self.current_coordinate_index = 0
                        print(f"Navigator: loaded next segment for Quest {quest_id:03d} on map {next_map_str} ({len(coords)} steps)")
                        return True
            except Exception as e:
                print(f"Navigator: error loading next segment for Quest {quest_id:03d}: {e}")
            # fall back to manual movement: stop nav when no next segment
            self.quest_locked = False
            return False

        # Handle multi-map quests: if map has changed (e.g., warp to new map segment), reload that segment
        cur_map = self.env.get_game_coords()[2]
        prev_map = getattr(self.env, 'prev_map_id', None)
        if prev_map is not None and cur_map != prev_map:
            print(f"navigator.py: move_to_next_coordinate(): MAP CHANGE detected for quest {self.active_quest_id}, loading segment for map {cur_map}")
            self.load_segment_for_current_map()
            # Sync prev_map_id to avoid reloading the same segment repeatedly
            # NOTE: After loading the segment, we update prev_map_id here so that
            # on the next call, move_to_next_coordinate won't detect the same map change
            # and reload the segment endlessly, which would block actual movement steps.
            setattr(self.env, 'prev_map_id', cur_map)
            return True
        # Block stepping onto the Pallet Town door warp after leaving home
        try:
            cur_map = self.env.get_game_coords()[2]
            # If we've left home and we're back in Pallet Town, skip any coordinate
            # that exactly lands on the house-entrance warp tile to prevent re-entry
            if self._left_home and cur_map == MapIds.PALLET_TOWN.value:
                # Find any warp entries in this map that lead back to house
                for entry in WARP_DICT.get(MapIds(cur_map).name, []):
                    if entry.get('target_map_id') == 37:
                        x, y = entry.get('x'), entry.get('y')
                        if x is not None and y is not None:
                            global_warp = local_to_global(y, x, cur_map)
                            # If the next target matches the warp tile, skip it
                            if self.sequential_coordinates[self.current_coordinate_index] == global_warp:
                                print(f"navigator.py: move_to_next_coordinate(): SKIPPING warp-entry coord {global_warp} after leaving home")
                                self.current_coordinate_index += 1
                                return True
        except Exception:
            pass
        # Early warp handling - check and perform warp before movement
        if self.warp_tile_handler():
            return True
        # Move toward current target
        target = self.sequential_coordinates[self.current_coordinate_index]
        cur_pos = self._get_player_global_coords()
        if cur_pos is None:
            return False
        # Advance index if already at target
        if cur_pos == target:
            self.current_coordinate_index += 1
            return True
        # Execute movement step
        return self._step_towards(target)

    # ................................................................
    def _step_towards(self, target: Tuple[int, int]) -> bool:
        cur = self._get_player_global_coords()
        if cur is None:
            return False

        # If already at target, advance index and continue
        if cur == target:
            self.current_coordinate_index += 1
            return True

        dy, dx = target[0] - cur[0], target[1] - cur[1]

        # Vertical first movement with collision detection
        moved = False
        if dy != 0:
            direction = "down" if dy > 0 else "up"
            action = self.ACTION_MAPPING_STR_TO_INT[direction]
            moved = self._execute_movement(action)
        
        # Only attempt horizontal movement if vertical didn't work
        if not moved and dx != 0:
            direction = "right" if dx > 0 else "left"
            action = self.ACTION_MAPPING_STR_TO_INT[direction]
            moved = self._execute_movement(action)

        # Update position and handle success/failure
        new_pos = self._get_player_global_coords()
        if moved and new_pos != cur:
            print(f"Navigator: Moved to {new_pos}")
            if new_pos == target:
                self.current_coordinate_index += 1
            return True
        
        # Movement failed: skip this coordinate to avoid deadlock
        print(f"Navigator: Movement failed at target {target}, skipping coordinate")
        # Advance index and reset failure counter
        self.current_coordinate_index += 1
        self.movement_failure_count = 0
        return True

    def _execute_movement(self, action: int) -> bool:
        """Execute movement and return True if position changed"""
        pre_pos = self._get_player_global_coords()
        self.env.run_action_on_emulator(action)
        for _ in range(5):  # Reduced tick count for more responsive movement
            self.pyboy.tick(self.env.action_freq)
        post_pos = self._get_player_global_coords()
        return post_pos != pre_pos

    # ...............................................................
    #  L O A D
    # ...............................................................
    def load_coordinate_path(self, quest_id: int) -> bool:
        if self.quest_locked and self.current_coordinate_index < len(self.sequential_coordinates):
            print(f"Navigator: Quest {self.active_quest_id} locked; can't switch")
            return False

        # Reset per-map segment counters on full quest load
        self.map_segment_count = {}
        base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
        file_path = base / f"{quest_id:03d}" / f"{quest_id:03d}_coords.json"
        if not file_path.exists():
            print(f"Navigator: coord file missing → {file_path}")
            return False

        try:
            with file_path.open() as f:
                data = json.load(f)
            # If this quest has no segment for the current map, skip automatic navigation
            cur_map = self.env.get_game_coords()[2]
            # JSON keys are mapID or mapID_segment; compare integer prefix
            if not any(int(k.split('_')[0]) == cur_map for k in data.keys()):
                print(f"Navigator: quest {quest_id:03d} has no path for map {cur_map}, not loading")
                return False
        except Exception as e:
            print(f"Navigator: failed to read coord file: {e}")
            return False

        coords: List[Tuple[int, int]] = []
        map_ids: List[int] = []
        for map_id_str, coord_list in data.items():
            # Normalize key: use integer part before any underscore
            mid = int(map_id_str.split('_')[0])
            for gy, gx in coord_list:
                coords.append((gy, gx))
                map_ids.append(mid)

        self.sequential_coordinates = coords
        self.coord_map_ids = map_ids
        self.current_coordinate_index = 0
        self.active_quest_id = quest_id
        # Record the actual quest we loaded for internal use
        self._last_loaded_quest_id = quest_id
        self.quest_locked = True
        self.movement_failure_count = 0

        print(f"Navigator: loaded quest {quest_id:03d}: "
              f"{len(coords)} points on {len(set(map_ids))} maps")
        self.snap_to_nearest_coordinate()
        # Sync environment's current_loaded_quest_id for UI and QuestManager
        setattr(self.env, 'current_loaded_quest_id', quest_id)
        return True

    def load_segment_for_current_map(self) -> None:
        """
        Attempt to load the path segment for the current map by searching backward from the current quest id.
        Only load a path if the coordinate file contains the current map id.
        This function is the ONLY place that loads a segment for the current map.
        """
        # Always update active quest ID from environment, fallback to default if absent
        env_qid = getattr(self.env, 'current_loaded_quest_id', None)
        if env_qid is not None:
            self.active_quest_id = env_qid
        # elif self.active_quest_id is None:
        #     self.active_quest_id = 12  # Fallback to default
        # # TODO: make a fallback to default coords on the map
            
        map_id = self.env.get_game_coords()[2]
        base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
        
        # Search backward from current quest id for a file with this map id
        for qid in range(self.active_quest_id, 0, -1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            if not fp.exists():
                continue
            try:
                data = json.loads(fp.read_text())
                # DEBUG: Quest 26 segment parsing
                if qid == 26:
                    print(f"DEBUG: Quest {qid:03d} - load_segment_for_current_map: map_id={map_id}, data.keys()={list(data.keys())}")
                # Collect all segment keys for this map, ordered by integer prefix then insertion
                # Preserve the order of segments as they appear in the JSON file
                segment_keys = [k for k in data.keys() if int(k.split('_')[0]) == map_id]
                if qid == 26:
                    print(f"DEBUG: Quest 026 - segment_keys = {segment_keys}")
                if not segment_keys:
                    continue
                # Determine which segment to load based on how many times we've loaded this map
                count = self.map_segment_count.get(map_id, 0)
                if qid == 26:
                    print(f"DEBUG: Quest 026 - current count for map {map_id} = {count}")
                idx = count if count < len(segment_keys) else len(segment_keys) - 1
                if qid == 26:
                    print(f"DEBUG: Quest 026 - idx = {idx}")
                selected_key = segment_keys[idx]
                if qid == 26:
                    print(f"DEBUG: Quest 026 - selected_key = '{selected_key}'")
                # Increment counter for next time
                self.map_segment_count[map_id] = count + 1
                coords = data[selected_key]
                if qid == 26:
                    print(f"DEBUG: Quest 026 - coords for selected_key '{selected_key}' first 5 entries = {coords[:5]}, total entries = {len(coords)}")
                self.sequential_coordinates = [(c[0], c[1]) for c in coords]
                self.coord_map_ids = [map_id] * len(coords)
                self.current_coordinate_index = 0
                self.active_quest_id = qid
                print(f"Navigator: Loaded quest {qid:03d} segment '{selected_key}' on map {map_id} ({len(coords)} steps)")
                return
            except Exception as e:
                print(f"Navigator: Error reading quest {qid:03d}: {e}")
                continue
        
        raise RuntimeError(f"Navigator: No quest file with map id {map_id}")

    # ...............................................................
    #  S T A T U S
    # ...............................................................
    def _safe_coord_map_id(self, idx: int) -> str | int:
        return self.coord_map_ids[idx] if 0 <= idx < len(self.coord_map_ids) else "?"

    def get_current_status(self) -> str:
        pos = self._get_player_global_coords()
        s = ["\n*** NAVIGATOR STATUS ***"]
        s.append(f"Quest          : {self.active_quest_id}")
        s.append(f"Current pos    : {pos}")
        s.append(f"Path length    : {len(self.sequential_coordinates)}")
        s.append(f"Current index  : {self.current_coordinate_index}")

        if self.sequential_coordinates and self.current_coordinate_index < len(self.sequential_coordinates):
            tgt = self.sequential_coordinates[self.current_coordinate_index]
            dist = self._manhattan(pos, tgt) if pos else "?"
            s.append(f"Current target : {tgt} (dist {dist})")
            s.append(f"Target map‑id  : {self._safe_coord_map_id(self.current_coordinate_index)}")
        else:
            s.append("At end of path – quest complete"
                     if self.sequential_coordinates else
                     "No path loaded")

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

    _reset_quest_state = _reset_state  # legacy alias

    def follow_path_for_current_map(self) -> None:
        """
        Chain per-map segments across quest JSONs.
        Raises RuntimeError if any move fails.
        """
        if self.active_quest_id is None:
            raise RuntimeError("Navigator: no active quest to follow")
        start_id = self.active_quest_id
        map_id = self.env.get_game_coords()[2]
        base = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
        # 1. search backward for first JSON containing map_id
        found_id = None
        for qid in range(start_id, 0, -1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            data = json.loads(fp.read_text()) if fp.exists() else {}
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
            # prepare sequential list for this segment
            self.sequential_coordinates = [(gy, gx) for gy, gx in arr]
            self.coord_map_ids = [map_id] * len(arr)
            self.current_coordinate_index = 0
            print(f"Navigator: following quest {qid:03d} segment on map {map_id} ({len(arr)} steps)")
            self.snap_to_nearest_coordinate()
            # step through
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
