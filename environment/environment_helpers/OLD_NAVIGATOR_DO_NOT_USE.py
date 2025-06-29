# # navigator.py - CONSOLIDATED NAVIGATION SYSTEM
# # ALL navigation logic consolidated here - no navigation code should exist elsewhere

# from __future__ import annotations

# import json
# import time
# import random
# import numpy as np
# from pathlib import Path
# from typing import List, Optional, Tuple, TYPE_CHECKING, Dict, Any
# from collections import defaultdict, deque

# from pyboy.utils import WindowEvent
            
# from environment.data.environment_data.constants import WARP_DICT
# from environment.data.environment_data.map import MapIds
# from environment.data.environment_data.items import Items
# from environment.data.environment_data.events import EventFlags
# from environment.data.environment_data.flags import Flags
# from environment.data.recorder_data.global_map import local_to_global, global_to_local

# if TYPE_CHECKING:
#     from environment import RedGymEnv

# # Import logging system
# import sys
# import os
# sys.path.append('/puffertank/grok_plays_pokemon')
# from utils.logging_config import get_pokemon_logger


    # def convert_path_follow_to_movement_action(self, original_action: int) -> int:
    #     """
    #     AGGRESSIVE PATH FOLLOWING - SIMPLIFIED VERSION

    #     This human path was recorded by a player who completed the game!
    #     It MUST be followable! Remove all safety checks and just follow the damn coordinates!
    #     """
    #     if original_action != self._path_follow_action_value:
    #         return original_action

    #     # -------------------------------------------------------------
    #     # 1.  QUICK EXIT – If we previously generated an A* recovery
    #     #     path (stored in ``_recovery_steps``) then simply pop the
    #     #     next move from that queue until it is empty.  This lets
    #     #     the navigator walk itself back onto the recorded quest
    #     #     path before resuming normal node-to-node following.
    #     # -------------------------------------------------------------
    #     if self._recovery_steps:
    #         next_dir = self._recovery_steps.pop(0)
    #         # Convert textual direction to action integer (defaults to 3/up)
    #         return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)

    #     # — Pre-conversion sync & debug
    #     try:
    #         x, y, map_id = self.env.get_game_coords()
    #         current_global = local_to_global(y, x, map_id)
    #         current_quest = self.get_current_quest()
    #         if not self.sequential_coordinates or self.active_quest_id != current_quest:
    #             self.load_coordinate_path(current_quest)

    #         # Debug: show current and upcoming target nodes
    #         node_idx = self.current_coordinate_index
    #         on_node = (node_idx < len(self.sequential_coordinates)
    #                    and current_global == self.sequential_coordinates[node_idx])
    #         curr_node = self.sequential_coordinates[node_idx] if node_idx < len(self.sequential_coordinates) else None
    #         curr_map = self.coord_map_ids[node_idx] if node_idx < len(self.coord_map_ids) else None
    #         next_idx = node_idx + 1
    #         next_node = self.sequential_coordinates[next_idx] if next_idx < len(self.sequential_coordinates) else None
    #         next_map = self.coord_map_ids[next_idx] if next_idx < len(self.coord_map_ids) else None
    #         print(f"next_map: {next_map}, map_id: {map_id}")
    #         warp_needed = (next_map is not None and next_map != map_id)
    #         print(f"\n1️⃣ quest_id={current_quest},"
    #               f"\n coords_loaded_count={len(self.sequential_coordinates)},"
    #               f"\n current_map={map_id}, local=({x},{y}), global={current_global},"
    #               f"\n on_node={on_node}, node_idx={node_idx},"  
    #               f"\n curr_node={curr_node}@{curr_map},"  
    #               f"\n 5️⃣ next_node={next_node}@{next_map}, warp_needed={warp_needed}\n")
    #         try:
    #             quest_dir = f"{current_quest:03d}"
    #             with (Path(__file__).parent / "quest_paths" / quest_dir / f"{quest_dir}_coords.json").open() as f:
    #                 data = json.load(f)
    #             verify_msgs = []
    #             file_coords, file_maps = [], []
    #             for seg_key, seg in data.items():
    #                 mid = int(seg_key.split('_')[0])
    #                 for coord in seg:
    #                     file_coords.append((coord[0], coord[1]))
    #                     file_maps.append(mid)
    #             for idx, ((lc, lm), (fc, fm)) in enumerate(zip(zip(self.sequential_coordinates, self.coord_map_ids),
    #                                                         zip(file_coords, file_maps))):
    #                 mark = '' if (lc == fc and lm == fm) else ' ❌'
    #                 verify_msgs.append(f"{idx}: loaded={lc}@{lm}, file={fc}@{fm}{mark}")
    #             print("coord_verify:", "; ".join(verify_msgs))
    #         except Exception as e:
    #             print(f"Navigator coords verification error: {e}")
    #     except Exception as e:
    #         print(f"Navigator logging error: {e}")

    #     # — Snap to nearest before movement logic
    #     try:
    #         old_idx = self.current_coordinate_index
    #         self.snap_to_nearest_coordinate()
    #         print(f"ConsolidatedNavigator: pre-conversion snap from index {old_idx} to {self.current_coordinate_index}")
    #     except Exception as e:
    #         print(f"ConsolidatedNavigator: pre-conversion snap error: {e}")

    #     # — Generate A* subpath to nearest node if off-node —
    #     try:
    #         cur_global = current_global
    #         # Compute Manhattan distances to each path node
    #         distances = [self._manhattan(cur_global, coord) for coord in self.sequential_coordinates]
    #         nearest_i = distances.index(min(distances))
    #         # If nearest node differs from current index, route to it
    #         if nearest_i != self.current_coordinate_index:
    #             dy = self.sequential_coordinates[nearest_i][0] - cur_global[0]
    #             dx = self.sequential_coordinates[nearest_i][1] - cur_global[1]
    #             # Convert global delta to grid coords for find_path
    #             target_row = max(0, min(8, 4 + dy))
    #             target_col = max(0, min(9, 4 + dx))
    #             status_msg, path_dirs = self.env.find_path(int(target_row), int(target_col))
    #             if path_dirs:
    #                 print(f"ConsolidatedNavigator: A* to nearest node[{nearest_i}]: {path_dirs}")
    #                 # Queue recovery steps and update node index
    #                 self._recovery_steps = path_dirs.copy()
    #                 self.current_coordinate_index = nearest_i
    #                 next_dir = self._recovery_steps.pop(0)
    #                 return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)
    #     except Exception as e:
    #         print(f"ConsolidatedNavigator: A* nearest-node recovery failed: {e}")

    #     # — Main movement / warp logic
    #     try:
    #         x, y, map_id = self.env.get_game_coords()
    #         current_global = local_to_global(y, x, map_id)

    #         current_quest = self.get_current_quest()
    #         if not current_quest:
    #             return 3
    #         if not self.sequential_coordinates or self.active_quest_id != current_quest:
    #             if not self.load_coordinate_path(current_quest) or not self.sequential_coordinates:
    #                 return 3

    #         # validate index bounds
    #         if self.current_coordinate_index < 0:
    #             self.current_coordinate_index = 0
    #         elif self.current_coordinate_index >= len(self.sequential_coordinates):
    #             return original_action

    #         # — CRITICAL FIX: bump index if we're already on the warp-tile coordinate —
    #         target_coord = self.sequential_coordinates[self.current_coordinate_index]
    #         # Safely retrieve the map id that corresponds to the *next* path node.
    #         # When we are on the final node, fall back to the current node's map id
    #         # instead of raising an IndexError.
    #         if self.current_coordinate_index + 1 < len(self.coord_map_ids):
    #             target_map = self.coord_map_ids[self.current_coordinate_index + 1]
    #         else:
    #             target_map = self.coord_map_ids[self.current_coordinate_index]
    #         if current_global == target_coord:
    #             self.current_coordinate_index += 1
    #             if self.current_coordinate_index >= len(self.sequential_coordinates):
    #                 return original_action
    #             target_coord = self.sequential_coordinates[self.current_coordinate_index]
    #             target_map   = self.coord_map_ids[self.current_coordinate_index]

    #         # 🚀 Debug current target after bump
    #         print(f"🚀 active_target_idx={self.current_coordinate_index + 1}, "
    #             f"active_target={target_coord}, active_map={target_map}")

    #         # Ensure horizontal (dx) and vertical (dy) deltas are always defined so the
    #         # direction-selection logic below can run even when we stay on the same map.
    #         # In global coordinate space:  +dy ⇒ move DOWN, +dx ⇒ move RIGHT.
    #         dy = target_coord[0] - current_global[0]
    #         dx = target_coord[1] - current_global[1]

    #         # ---------------------------------------------------------
    #         # 2.  RECOVERY LOGIC – If we are on the correct map but the
    #         #     target node is off-screen (|dx| or |dy| > 4) OR we
    #         #     snapped failed earlier, attempt to generate a local
    #         #     A* route using the environment's ``find_path`` helper.
    #         #     This works in the 9×10 down-sampled grid where the
    #         #     player is fixed at (4,4).
    #         # ---------------------------------------------------------
    #         if not self._recovery_steps and target_map == map_id:
    #             # Only attempt if target is within roughly a screen so
    #             # find_path can see it.
    #             if abs(dx) <= 8 and abs(dy) <= 8:
    #                 target_row = 4 + dy
    #                 target_col = 4 + dx
    #                 # Sanity clamp to grid limits 0-8 / 0-9
    #                 target_row = max(0, min(8, target_row))
    #                 target_col = max(0, min(9, target_col))
    #                 try:
    #                     status_msg, path_dirs = self.env.find_path(int(target_row), int(target_col))
    #                     if path_dirs:
    #                         print(f"Navigator recovery: {status_msg} → {path_dirs}")
    #                         # Store and immediately use first move next frame
    #                         self._recovery_steps = path_dirs.copy()
    #                         next_dir = self._recovery_steps.pop(0)
    #                         return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)
    #                 except Exception as e:
    #                     print(f"Navigator recovery error: {e}")

    #         # — WARP HANDLING: handle explicit map transitions via warp tiles —
    #         if target_map != map_id:
    #             local_pos = (x, y)
    #             # only consider warp entries that lead to the desired map
    #             warps_full = WARP_DICT.get(MapIds(map_id).name, [])
    #             # Accept warps that go directly to the desired map or that
    #             # use the special 255 "LAST_MAP" sentinel (meaning they lead
    #             # back to whatever map we came from – which, for a recorded
    #             # human path, will be the desired one).
    #             warps = []
    #             for e in warps_full:
    #                 tmid = e.get('target_map_id')
    #                 if tmid == target_map or tmid == 255:
    #                     warps.append(e)
    #             if warps:
    #                 # find nearest warp tile
    #                 best = None
    #                 best_dist = None
    #                 for entry in warps:
    #                     wx, wy = entry.get('x'), entry.get('y')
    #                     if wx is None or wy is None:
    #                         continue
    #                     d = abs(wx - local_pos[0]) + abs(wy - local_pos[1])
    #                     if best_dist is None or d < best_dist:
    #                         best_dist, best = d, (wx, wy)
    #                 if best is not None:
    #                     # door warp: step onto warp tile, then press DOWN to warp
    #                     if local_pos == best:
    #                         return 0
    #                     # otherwise, walk toward the warp tile
    #                     dx, dy = best[0] - x, best[1] - y
    #                     # Prefer vertical motion first when approaching a warp tile.  For
    #                     # indoor exits the critical step is usually to **step down onto the
    #                     # bottom-row door tile**; choosing horizontal first can nudge the
    #                     # avatar in front of an NPC and block the warp indefinitely.
    #                     if dy != 0:
    #                         return 0 if dy > 0 else 3  # DOWN / UP
    #                     elif dx != 0:
    #                         return 2 if dx > 0 else 1  # RIGHT / LEFT
    #                     else:
    #                         return 0  # default DOWN (shouldn't occur)
    #             # no explicit warp entries for this transition: fall through to normal movement

    #         # Evaluate both axes, prioritising the one with greater distance but
    #         # *only if* that direction is considered walkable; otherwise swap.
    #         primary_first = 'horiz' if abs(dx) >= abs(dy) else 'vert'

    #         # Determine preferred directions (primary then secondary)
    #         horiz_dir = 2 if dx > 0 else 1 if dx < 0 else None  # RIGHT or LEFT
    #         vert_dir  = 0 if dy > 0 else 3 if dy < 0 else None  # DOWN  or UP

    #         # -----------------------------------------------------------------
    #         # 3.  COLLISION-AWARE CHOICE – consult the environment's live
    #         #     collision grid so we don't keep bonking into fences / signs.
    #         # -----------------------------------------------------------------

    #         try:
    #             valid_moves = set(self.env.get_valid_moves())  # strings: up/down/left/right
    #         except Exception:
    #             valid_moves = {"up", "down", "left", "right"}

    #         def dir_walkable(d):
    #             if d is None:
    #                 return False
    #             str_dir = {0: "down", 1: "left", 2: "right", 3: "up"}.get(d)
    #             return str_dir in valid_moves

    #         order = [primary_first, 'vert' if primary_first=='horiz' else 'horiz']
    #         for axis in order:
    #             chosen = horiz_dir if axis=='horiz' else vert_dir
    #             if chosen is not None and dir_walkable(chosen):
    #                 return chosen

    #         # If neither preferred direction is walkable fall back to any that is
    #         for cand in [0,1,2,3]:
    #             if dir_walkable(cand):
    #                 return cand

    #         # If we get here, assume blocked; return original action to keep game ticking
    #         return original_action

    #     except Exception:
    #         return 3



# # =============================================================================
# # ANTI-OSCILLATION COMPREHENSIVE PROTECTION SYSTEM
# # =============================================================================
# # 
# # This navigator implements multiple layers of protection against quest 
# # oscillation where the system rapidly switches between quests, causing the
# # player to get stuck or move erratically.
# #
# # PROTECTION LAYERS:
# #
# # 1. QUEST LOCK MECHANISM (quest_locked flag):
# #    - When locked onto a fallback quest path, the navigator REFUSES to 
# #      switch to any other quest until the current path is completed or
# #      the target quest becomes reachable on the current map
# #
# # 2. FORCE LOADING PREVENTION (environment.py):
# #    - Environment won't force-reload quest coordinates when navigator is locked
# #
# # 3. LOAD BLOCKING (load_coordinate_path):
# #    - Method refuses to load different quest when locked
# #
# # 4. RELOAD PROTECTION (main conversion logic):
# #    - Prevents quest reloading in the main path following logic when locked
# #
# # 5. PROGRESSION CONTROL (quest completion logic):
# #    - Only allows quest switching in locked mode for natural progression
# #      (current_quest + 1) or when reaching target quest
# #
# # 6. FALLBACK STATE PRESERVATION:
# #    - Preserves _fallback_searched state when locked to prevent re-searching
# #
# # This comprehensive approach ensures that once the navigator locks onto a
# # fallback quest path, it will CONSISTENTLY follow that path forward until
# # reaching a point where the target quest becomes accessible, preventing
# # all forms of oscillation.
# # =============================================================================

# class ConsolidatedNavigator:
#     """
#     CONSOLIDATED NAVIGATION SYSTEM - All navigation logic is now in this single class.
    
#     This class handles:
#     - PATH_FOLLOW_ACTION conversion (moved from StageManager)
#     - Quest path following (moved from QuestPathFollower)  
#     - Warp detection and handling (moved from WarpTracker)
#     - Map transition tracking (moved from TriggerEvaluator)
#     - Quest-based navigation decisions (moved from QuestManager)
#     - Coordinate management and validation
#     - Movement execution and collision detection
#     - Direction and pathfinding logic
#     """
    
#     # ================================================================================
#     # CRITICAL NAVIGATION REQUIREMENT - DO NOT FORGET THIS!!!
#     # ================================================================================
#     # The navigation system MUST "lock onto the closest completed quest path and 
#     # PROCEED FORWARD" until it reaches the current quest destination. When the 
#     # current quest path is unavailable or the player is off-path, the system should 
#     # find the nearest quest path (completed or not) and follow it forward 
#     # sequentially until the current quest path becomes reachable. This prevents 
#     # getting stuck trying to navigate directly to unreachable coordinates on 
#     # different maps.
#     # ================================================================================
    
#     def __init__(self, env_instance: RedGymEnv):
#         self.env: RedGymEnv = env_instance
#         self.pyboy = self.env.pyboy
        
#         # Initialize logger
#         self.logger = get_pokemon_logger()

#         # =========================
#         # CORE NAVIGATION STATE
#         # =========================
#         self.sequential_coordinates: List[Tuple[int, int]] = []
#         self.coord_map_ids: List[int] = []
#         self.current_coordinate_index: int = 0
#         self.active_quest_id: Optional[int] = None
#         self._last_loaded_quest_id: Optional[int] = None
#         self.using_placeholder_map_ids: bool = True
        
#         # Multi-segment load tracking
#         self.map_segment_count: dict[int, int] = {}

#         # Raw local coords placeholder
#         self.current_coords = None

#         # Fallback/resume logic
#         self.last_position = None
#         self.quest_locked = False
#         self._direction = 1                # 1 = forward, -1 = backward
#         self._fallback_mode = False
#         self._original_quest_id: Optional[int] = None

#         # Movement tracking
#         self.movement_failure_count = 0
#         self.max_movement_failures = 10
#         self.navigation_status = "idle"

#         # ------------------------------------------------------------------
#         # STUCK DETECTION
#         # ------------------------------------------------------------------
#         # When the navigator is locked onto a quest path but repeatedly issues a
#         # movement that is *not walkable* (e.g. bumping against a wall on the
#         # edge of the current map) the avatar can become soft-locked forever.
#         # We keep a lightweight counter that increments whenever the chosen
#         # direction is blocked and resets on any successful step.  Once the
#         # threshold is exceeded we invoke a small recovery routine that tries
#         # to (1) attach to the nearest path node on the current map or, as a
#         # last resort, unlock the quest so the normal fallback search kicks
#         # in on the next PATH_FOLLOW_ACTION.
#         #
#         # The threshold is intentionally low (≈ ½ second of real-time at the
#         # default 60 FPS) so Grok reacts quickly without noticeable delay but
#         # still filters out the occasional harmless bump.
#         self._stuck_counter: int = 0
#         self._max_stuck_frames: int = 30  # 30 frames ≈ 0.5 s at 60 FPS

#         # =========================
#         # CONSOLIDATED WARP SYSTEM (from WarpTracker + Navigator)
#         # =========================
#         self.door_warp = False
#         self.last_warp_time = 0.0
#         self.WARP_COOLDOWN_SECONDS = 0.5
#         self.last_warp_origin_map: Optional[int] = None
#         self._post_warp_exit_pos: Optional[Tuple[int, int]] = None
#         self._left_home = False
#         self._last_map_id = None
        
#         # Enhanced warp blocking state
#         self._blocked_warps: dict[tuple[int, int], float] = {}  # (from_map, to_map) -> expiry_time
#         self.WARP_BLOCK_DURATION = 2.0  # Seconds to block reverse warps
        
#         # House stair tracking to prevent infinite loops
#         self._house_stair_warp_count = 0
#         self._max_house_stair_warps = 1
#         self._house_stair_timer = 0.0
#         self._house_stair_cooldown = 10.0
#         self._last_stair_direction = None
#         self._recent_maps = []  # Track recently visited maps
#         self._max_recent_maps = 3
#         self._map_cooldown_period = 3.0
#         self._map_visit_times = {}
#         self._prevent_immediate_return = True
#         self._last_warp_origin = None
#         self._last_warp_target = None
#         self._warp_delay_timer = 0.0
#         self._post_warp_delay = 1.0
        
#         # Warp step tracking (from WarpTracker)
#         self.warp_steps = deque(maxlen=20)  # Store last 20 positions
#         self.last_warp_step_map = None
        
#         # =========================
#         # MAP TRANSITION TRACKING (from TriggerEvaluator)
#         # =========================
#         self.map_history = deque(maxlen=10)  # Consolidated map tracking
#         self._trigger_cooldowns = {}
#         self._trigger_cooldown_duration = 2.0
#         self._max_trigger_count = 2
        
#         # =========================
#         # QUEST SYSTEM (from QuestManager + QuestPathFollower)
#         # =========================
#         self.quest_definitions: List[Dict[str, Any]] = []
#         self.quests_by_location: Dict[int, List[int]] = {}
#         self.quest_completed_status: Dict[str, bool] = {}
#         self.current_quest_id: Optional[int] = None
        
#         # Quest path following (from QuestPathFollower)
#         self.quest_paths: Dict[int, List[Tuple[int, int]]] = {}
#         self.current_quest_path: List[Tuple[int, int]] = []
#         self.current_path_index: int = 0
#         self.path_following_active: bool = False
        
#         # Quest action management
#         self.pending_b_presses = 0
#         self.pressed_button_dict: Dict[int, Dict[Tuple[int, int], Dict[int, int]]] = {}
        
#         # =========================
#         # PATH_FOLLOW_ACTION CONVERSION (from StageManager)
#         # =========================
#         # This is now the ONLY place PATH_FOLLOW_ACTION is converted
#         self._path_follow_action_value = 6  # The discrete action index
        
#         # =========================
#         # ACTION AND MOVEMENT MAPPINGS
#         # =========================
#         self.ACTION_MAPPING_STR_TO_INT = {
#             "down": 0,
#             "left": 1,
#             "right": 2,
#             "up": 3,
#         }
        
#         # Quest action mapping
#         self.quest_action_mapping = {
#             'down': 0,
#             'left': 1, 
#             'right': 2,
#             'up': 3,
#             'a': 4,
#             'b': 5,
#             'path': 6,
#             'start': 7
#         }

#         # Path-recovery queue generated by A* (env.find_path).  When populated we
#         # consume one direction per frame before falling back to the normal
#         # path-following heuristics.
#         self._recovery_steps: List[str] = []

#         # =========================
#         # INITIALIZE SUBSYSTEMS
#         # =========================
#         self._load_quest_system()
#         self._initialize_map_tracking()

#         # Maximum Manhattan distance allowed when snapping the navigator index
#         # to the nearest recorded coordinate.  The previous hard-coded value of
#         # 13 turned out to be too strict when the game is loaded from a save
#         # state that puts the avatar far away from the first node of the
#         # *current* quest path.  A larger – yet still sane – radius lets the
#         # navigator "catch" the closest node and avoids the soft-lock where the
#         # agent repeatedly presses the PATH_FOLLOW key but never moves.
#         #
#         # Empirically a value of 40 tiles (≈ 4 screens horizontally) strikes a
#         # good balance between robustness and safety.  Exposed as an instance
#         # attribute so it can be tweaked from unit-tests if needed.
#         self.max_snap_distance: int = 40

#     # =========================
#     # QUEST SYSTEM INTEGRATION
#     # =========================
    
#     def get_current_local_coords(self):
#         """Get current local coordinates"""
#         return self.env.get_game_coords()
    
#     def _load_quest_system(self):
#         """Load quest definitions and initialize quest tracking"""
#         try:
#             required_completions_path = Path(__file__).parent / "required_completions.json"
#             if not required_completions_path.exists():
#                 print(f"ConsolidatedNavigator: Quest definitions file not found: {required_completions_path}")
#                 return
                
#             with required_completions_path.open('r') as f:
#                 loaded_data = json.load(f)
            
#             if not isinstance(loaded_data, list):
#                 print(f"ConsolidatedNavigator: Invalid quest definitions format")
#                 return
                
#             self.quest_definitions = loaded_data
            
#             # Build location mapping
#             quests_by_location: Dict[int, List[int]] = defaultdict(list)
#             for q_def in self.quest_definitions:
#                 try:
#                     loc = int(q_def["location_id"])
#                     quest_id_raw = q_def["quest_id"]
#                     if isinstance(quest_id_raw, str):
#                         quest_id_int = int(quest_id_raw.lstrip('0') or '0')
#                     else:
#                         quest_id_int = int(quest_id_raw)
#                     quests_by_location[loc].append(quest_id_int)
#                 except (ValueError, TypeError, KeyError) as e:
#                     continue
                    
#             for loc, qlist in quests_by_location.items():
#                 qlist.sort()
#             self.quests_by_location = dict(quests_by_location)
            
#             print(f"ConsolidatedNavigator: Loaded {len(self.quest_definitions)} quest definitions")
            
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Failed to load quest system: {e}")
#             self.quest_definitions = []
#             self.quests_by_location = {}

#     def _load_quest_paths(self):
#         """Load quest coordinate paths from combined file"""
#         coords_file = Path(__file__).parent / "quest_paths" / "combined_quest_coordinates_continuous.json"
        
#         if not coords_file.exists():
#             print(f"ConsolidatedNavigator: Combined coordinate file not found: {coords_file}")
#             return
            
#         try:
#             with open(coords_file, 'r') as f:
#                 data = json.load(f)
            
#             quest_start_indices = data.get("quest_start_indices", {})
#             all_coordinates = data.get("coordinates", [])
            
#             # Extract coordinates for each quest
#             for quest_id_str, start_idx in quest_start_indices.items():
#                 quest_id = int(quest_id_str)
                
#                 # Find end index
#                 end_idx = len(all_coordinates)
#                 sorted_quest_ids = sorted([int(k) for k in quest_start_indices.keys()])
                
#                 current_quest_idx = sorted_quest_ids.index(quest_id)
#                 if current_quest_idx + 1 < len(sorted_quest_ids):
#                     next_quest_id = sorted_quest_ids[current_quest_idx + 1]
#                     end_idx = quest_start_indices[str(next_quest_id)]
                
#                 quest_coords = all_coordinates[start_idx:end_idx]
#                 self.quest_paths[quest_id] = [(coord[0], coord[1]) for coord in quest_coords]
            
#             print(f"ConsolidatedNavigator: Loaded {len(self.quest_paths)} quest paths")
            
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error loading quest paths: {e}")
#             self.quest_paths = {}

#     def get_current_quest(self) -> Optional[int]:
#         """Return the quest the navigator should currently follow.

#         If ``quest_locked`` is *True* we stay on ``self.active_quest_id`` even
#         when QuestManager has already advanced – this allows the avatar to walk
#         along a **previously-completed** quest path until it naturally merges
#         back into the storyline.  When ``quest_locked`` is *False* we delegate
#         to QuestManager as usual.
#         """
#         # --- Persistent fallback handling ---------------------------------
#         if self.quest_locked and self.active_quest_id is not None:
#             return self.active_quest_id

#         # Normal behaviour – follow QuestManager
#         if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
#             current_quest = self.env.quest_manager.get_current_quest()
#             if current_quest is not None:
#                 return current_quest
#         # Get current location for quest filtering
#         try:
#             curr_map = self.env.get_game_coords()[2]
#         except:
#             return None
            
#         # Get quests available at current location
#         location_quests = self.quests_by_location.get(curr_map, [])
#         if not location_quests:
#             # Try to find any incomplete quest
#             for q_def in self.quest_definitions:
#                 quest_id = int(q_def["quest_id"])
#                 if not self._is_quest_completed(quest_id):
#                     return quest_id
#             return None
        
#         # Find first incomplete quest at this location
#         for quest_id in location_quests:
#             if not self._is_quest_completed(quest_id):
#                 return quest_id
                
#         return None

#     def _is_quest_completed(self, quest_id: int) -> bool:
#         """Return True if the specified quest is marked completed.

#         The ConsolidatedNavigator keeps its own lightweight cache
#         (``self.quest_completed_status``) but the authoritative source
#         of truth lives inside ``env.quest_manager``.  We therefore query
#         QuestManager *first* and only fall back to the local cache when
#         that information is unavailable.  This prevents the navigator
#         from mistakenly treating an **already-completed** quest as
#         unfinished – the root cause of the recent infinite loop where
#         the avatar kept snapping back to quest 44 even after finishing
#         it and loading quest 45.
#         """

#         quest_key = str(quest_id).zfill(3)

#         # 1️⃣  Ask QuestManager (authoritative)
#         qm = getattr(self.env, "quest_manager", None)
#         if qm and hasattr(qm, "quest_completed_status"):
#             status = qm.quest_completed_status.get(quest_key)
#             if status is not None:
#                 return bool(status)

#         # 2️⃣  Fallback to the navigator's own cache
#         return bool(self.quest_completed_status.get(quest_key, False))

#     # =========================
#     # MAP TRANSITION TRACKING
#     # =========================
    
#     def _initialize_map_tracking(self):
#         """Initialize map tracking system"""
#         try:
#             current_map = self.env.get_game_coords()[2]
#             self.map_history.append(current_map)
#         except:
#             pass

#     def update_map_history(self):
#         """Update map history when map changes"""
#         try:
#             current_map = self.env.get_game_coords()[2]
#             if not self.map_history or self.map_history[-1] != current_map:
#                 self.map_history.append(current_map)
#                 if self.logger:
#                     self.logger.log_navigation_event("MAP_TRANSITION", {
#                         'message': f'Map changed to {current_map}',
#                         'previous_map': self.map_history[-2] if len(self.map_history) >= 2 else None,
#                         'current_map': current_map,
#                         'map_history': list(self.map_history)
#                     })
#         except:
#             pass

#     def _get_map_history(self) -> Tuple[Optional[int], int]:
#         """Get previous and current map IDs"""
#         try:
#             current_map_id = self.env.get_game_coords()[2]
#             if len(self.map_history) >= 2:
#                 previous_map_id = self.map_history[-2]
#             else:
#                 previous_map_id = current_map_id
#             return previous_map_id, current_map_id
#         except:
#             return None, 0

#     # =========================
#     # WARP TRACKING SYSTEM
#     # =========================
    
#     def record_warp_step(self):
#         """Record current position for warp tracking"""
#         try:
#             global_pos = self._get_player_global_coords()
#             current_map = self.env.get_game_coords()[2]
            
#             if global_pos:
#                 self.warp_steps.append({
#                     'position': global_pos,
#                     'map_id': current_map,
#                     'timestamp': time.time()
#                 })
                
#             # Track map changes for warp detection
#             if self.last_warp_step_map != current_map:
#                 self.last_warp_step_map = current_map
#                 self.update_map_history()
                
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error recording warp step: {e}")

#     def can_backtrack_warp(self) -> bool:
#         """Check if we can backtrack from current position"""
#         if len(self.warp_steps) < 2:
#             return False
            
#         current_pos = self._get_player_global_coords()
#         if not current_pos:
#             return False
            
#         # Look for a previous position that's different from current
#         for step in reversed(list(self.warp_steps)[:-1]):  # Exclude current position
#             if step['position'] != current_pos:
#                 return True
                
#         return False

#     def execute_backtrack(self) -> bool:
#         """Execute backtrack to previous position"""
#         if not self.can_backtrack_warp():
#             return False
            
#         current_pos = self._get_player_global_coords()
        
#         # Find the most recent different position
#         for step in reversed(list(self.warp_steps)[:-1]):
#             if step['position'] != current_pos:
#                 target_pos = step['position']
#                 target_map = step['map_id']
                
#                 print(f"ConsolidatedNavigator: Backtracking to {target_pos} on map {target_map}")
                
#                 # Implement backtrack logic - for now, just update target
#                 if len(self.sequential_coordinates) > 0:
#                     # Find closest coordinate to backtrack target
#                     distances = [self._manhattan(target_pos, coord) for coord in self.sequential_coordinates]
#                     closest_idx = distances.index(min(distances))
#                     self.current_coordinate_index = closest_idx
                    
#                 return True
                
#         return False

#     # =========================
#     # PATH_FOLLOW_ACTION CONVERSION (CONSOLIDATED)
#     # =========================
#     def convert_path_follow_to_movement_action(self, original_action: int) -> int:
#         """Main entry point for converting PATH_FOLLOW actions to movement"""
#         # DEBUG: log invocation and internal state
#         print(f"DEBUG Navigator.convert_path_follow_to_movement_action called: original_action={original_action}, active_quest_id={self.active_quest_id}, coords_loaded={len(self.sequential_coordinates)}, current_index={self.current_coordinate_index}, quest_locked={getattr(self, 'quest_locked', False)}")
#         # Guard against infinite recursion - track recursive depth
#         if not hasattr(self, "_recursion_depth"):
#             self._recursion_depth = 0
        
#         self._recursion_depth += 1
        
#         # Prevent excessive recursion that can cause infinite loops
#         if self._recursion_depth > 3:
#             print(f"ConsolidatedNavigator: Breaking infinite recursion at depth {self._recursion_depth}")
#             self._recursion_depth = 0
#             return original_action
        
#         try:
#             result = self._convert_path_follow_to_movement_action_inner(original_action)
#             self._recursion_depth = 0  # Reset on successful completion
#             return result
#         except Exception as e:
#             self._recursion_depth = 0  # Reset on error
#             raise e

#     def _convert_path_follow_to_movement_action_inner(self, original_action: int) -> int:
#         """Internal implementation of convert_path_follow_to_movement_action"""
#         # DEBUG: log inner invocation and internal state
#         print(f"DEBUG Navigator._convert_path_follow_to_movement_action_inner start: original_action={original_action}, active_quest_id={self.active_quest_id}, coords_loaded={len(self.sequential_coordinates)}, current_index={self.current_coordinate_index}, quest_locked={getattr(self, 'quest_locked', False)}")
#         # 🔒 ANTI-OSCILLATION GUARD: If we're locked onto a quest path, 
#         # NEVER change quests until we reach the end or target becomes reachable
#         if hasattr(self, 'quest_locked') and self.quest_locked:
#             print(f"ConsolidatedNavigator: 🔒 QUEST LOCKED - Skipping all quest switching logic, staying on quest {self.active_quest_id}")
#             # Skip ALL quest evaluation and switching logic - just follow the locked path
#             return self._execute_locked_quest_navigation(original_action)
        
#         # 0️⃣  EARLY RESCUE & CROSS-MAP FALLBACK
#         # =====================================================================
#         # CRITICAL: The navigation system MUST "lock onto the closest completed 
#         # quest path and PROCEED FORWARD" until it reaches the current quest 
#         # destination. This prevents getting stuck trying to navigate directly 
#         # to unreachable coordinates on different maps.
#         # =====================================================================
#         if not hasattr(self, "_fallback_searched"):
#             self._fallback_searched = False  # type: ignore

#         # Only run fallback search once per quest/location change, not every frame
#         if not self._fallback_searched:
#             current_quest_tmp = self.get_current_quest()
            
#             # CASE 1: No active quest - find nearest quest path
#             if current_quest_tmp is None:
#                 nearest_q, nearest_coord_idx = self._find_nearest_completed_quest_coord()
#                 if nearest_q is not None:
#                     if self.load_coordinate_path(nearest_q):
#                         self.current_coordinate_index = nearest_coord_idx
#                         self.active_quest_id = nearest_q
#                         self.quest_locked = True
#                         print(f"ConsolidatedNavigator: Fallback to quest {nearest_q} at index {nearest_coord_idx} – quest locked")
            
#             # CASE 2: Current quest exists but is unreachable from current position
#             elif current_quest_tmp is not None:
#                 # Try to load current quest path
#                 if self.load_coordinate_path(current_quest_tmp):
#                     # Check if current quest is reachable from current position
#                     try:
#                         x, y, map_id = self.env.get_game_coords()
#                         current_global = local_to_global(y, x, map_id)
                        
#                         # Check if any coordinate in current quest path is on the same map
#                         current_quest_reachable = False
#                         for i, coord in enumerate(self.sequential_coordinates):
#                             coord_map = self.coord_map_ids[i] if i < len(self.coord_map_ids) else None
#                             if coord_map == map_id:
#                                 current_quest_reachable = True
#                                 break
                        
#                         # If current quest is NOT reachable (all coordinates on different maps)
#                         # then find nearest quest path to proceed forward
#                         if not current_quest_reachable:
#                             print(f"ConsolidatedNavigator: Current quest {current_quest_tmp} unreachable from map {map_id} - seeking fallback path")
#                             nearest_q, nearest_coord_idx = self._find_nearest_completed_quest_coord()
#                             if nearest_q is not None and nearest_q != current_quest_tmp:
#                                 # CRITICAL FIX: Don't lock onto a quest that's very far from the target quest
#                                 # This prevents locking onto quest 25 when QuestManager wants quest 6
#                                 quest_gap = abs(nearest_q - current_quest_tmp)
#                                 if quest_gap > 10:  # Don't lock if more than 10 quests apart
#                                     print(f"ConsolidatedNavigator: SKIP FALLBACK - Quest gap too large: {nearest_q} vs {current_quest_tmp} (gap: {quest_gap})")
#                                     print(f"ConsolidatedNavigator: Will attempt direct navigation to quest {current_quest_tmp} instead")
#                                     # Try to load the target quest anyway and see if we can make progress
#                                     if self.load_coordinate_path(current_quest_tmp):
#                                         self.current_coordinate_index = 0
#                                         self.active_quest_id = current_quest_tmp
#                                         self.quest_locked = False  # Don't lock - allow normal progression
#                                 elif self.load_coordinate_path(nearest_q):
#                                     self.current_coordinate_index = nearest_coord_idx
#                                     self.active_quest_id = nearest_q
#                                     # CRITICAL: Lock onto this quest path and proceed forward
#                                     # until we can reach the current quest destination
#                                     self.quest_locked = True
#                                     print(f"ConsolidatedNavigator: CROSS-MAP FALLBACK - Locked onto quest {nearest_q} at index {nearest_coord_idx} to proceed forward until quest {current_quest_tmp} becomes reachable")
#                                     print(f"ConsolidatedNavigator: 🔒 QUEST LOCKED = {self.quest_locked} 🔒")
#                     except Exception as e:
#                         print(f"ConsolidatedNavigator: Error checking quest reachability: {e}")
                
#             self._fallback_searched = True

#             # NEW: If we still have no path loaded, attach to nearest node on current map
#             if not self.sequential_coordinates:
#                 attached = self._attach_to_nearest_path_node()
#                 if attached:
#                     print("ConsolidatedNavigator: Attached to nearest path node for recovery.")

#         # -------------------------------------------------------------
#         # 1.  QUICK EXIT – If we previously generated an A* recovery
#         #     path (stored in ``_recovery_steps``) then simply pop the
#         #     next move from that queue until it is empty.  This lets
#         #     the navigator walk itself back onto the recorded quest
#         #     path before resuming normal node-to-node following.
#         # -------------------------------------------------------------
#         if self._recovery_steps:
#             next_dir = self._recovery_steps.pop(0)
#             # Convert textual direction to action integer (defaults to 3/up)
#             return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)

#         # — Pre-conversion sync & debug
#         try:
#             x, y, map_id = self.env.get_game_coords()
#             current_global = local_to_global(y, x, map_id)
#             current_quest = self.get_current_quest()
#             print(f"🔍 DEBUG: PRE-SYNC - index={self.current_coordinate_index}, coords_loaded={len(self.sequential_coordinates)}")
#             if not self.sequential_coordinates or self.active_quest_id != current_quest:
#                 print(f"🔍 DEBUG: RELOADING PATH - sequential_coordinates empty: {not self.sequential_coordinates}, active_quest_id mismatch: {self.active_quest_id} != {current_quest}")
#                 # 🔒 Don't reload when locked - use existing locked path
#                 if not (hasattr(self, 'quest_locked') and self.quest_locked):
#                     self.load_coordinate_path(current_quest)
#                 else:
#                     print(f"🔒 BLOCKED quest reload - staying locked on quest {self.active_quest_id}")
#                 print(f"🔍 DEBUG: AFTER RELOAD - index={self.current_coordinate_index}, coords_loaded={len(self.sequential_coordinates)}")

#             # Debug: show current and upcoming target nodes
#             node_idx = self.current_coordinate_index
#             on_node = (node_idx < len(self.sequential_coordinates)
#                        and current_global == self.sequential_coordinates[node_idx])
#             curr_node = self.sequential_coordinates[node_idx] if node_idx < len(self.sequential_coordinates) else None
#             curr_map = self.coord_map_ids[node_idx] if node_idx < len(self.coord_map_ids) else None
#             next_idx = node_idx + 1
#             next_node = self.sequential_coordinates[next_idx] if next_idx < len(self.sequential_coordinates) else None
#             next_map = self.coord_map_ids[next_idx] if next_idx < len(self.coord_map_ids) else None
#             print(f"🔍 DEBUG: BEFORE MAIN LOGIC - index={self.current_coordinate_index}, coord at index={self.sequential_coordinates[self.current_coordinate_index] if self.current_coordinate_index < len(self.sequential_coordinates) else 'OUT_OF_BOUNDS'}")
#             print(f"next_map: {next_map}, map_id: {map_id}")
#             warp_needed = (next_map is not None and next_map != map_id)
#             print(f"\n1️⃣ quest_id={current_quest},"
#                   f"\n coords_loaded_count={len(self.sequential_coordinates)},"
#                   f"\n current_map={map_id}, local=({x},{y}), global={current_global},"
#                   f"\n on_node={on_node}, node_idx={node_idx},"  
#                   f"\n curr_node={curr_node}@{curr_map},"  
#                   f"\n 5️⃣ next_node={next_node}@{next_map}, warp_needed={warp_needed}\n")
#             try:
#                 quest_dir = f"{current_quest:03d}"
#                 with (Path(__file__).parent / "quest_paths" / quest_dir / f"{quest_dir}_coords.json").open() as f:
#                     data = json.load(f)
#                 verify_msgs = []
#                 file_coords, file_maps = [], []
#                 for seg_key, seg in data.items():
#                     # Handle both key formats: plain map IDs ("68") and underscore-separated ("68_0")
#                     if '_' in seg_key:
#                         mid = int(seg_key.split('_')[0])
#                     else:
#                         mid = int(seg_key)
#                     for coord in seg:
#                         file_coords.append((coord[0], coord[1]))
#                         file_maps.append(mid)
#                 for idx, ((lc, lm), (fc, fm)) in enumerate(zip(zip(self.sequential_coordinates, self.coord_map_ids),
#                                                             zip(file_coords, file_maps))):
#                     mark = '' if (lc == fc and lm == fm) else ' ❌'
#                     verify_msgs.append(f"{idx}: loaded={lc}@{lm}, file={fc}@{fm}{mark}")
#                 print("coord_verify:", "; ".join(verify_msgs))
#             except Exception as e:
#                 print(f"Navigator coords verification error: {e}")
#         except Exception as e:
#             print(f"Navigator logging error: {e}")

#         # — Snap to nearest before movement logic
#         try:
#             old_idx = self.current_coordinate_index
            
#             # 🎯 OSCILLATION FIX: Only snap to nearest if we're actually lost or in a bad state
#             # Don't snap on every step as this overwrites valid index progression
#             current_global = local_to_global(*self.env.get_game_coords()[:2], self.env.get_game_coords()[2])
            
#             # Check if we need to snap at all
#             should_snap = False
            
#             # Snap if we have no coordinates loaded
#             if not self.sequential_coordinates:
#                 should_snap = True
#                 print("🎯 DEBUG: Should snap - no coordinates loaded")
            
#             # Snap if our current index is invalid
#             elif (self.current_coordinate_index < 0 or 
#                   self.current_coordinate_index >= len(self.sequential_coordinates)):
#                 should_snap = True
#                 print(f"🎯 DEBUG: Should snap - invalid index {self.current_coordinate_index} (max: {len(self.sequential_coordinates)-1})")
            
#             # Snap if we're very far from our current target (suggests we're lost)
#             elif self.current_coordinate_index < len(self.sequential_coordinates):
#                 target_coord = self.sequential_coordinates[self.current_coordinate_index] 
#                 distance = self._manhattan(current_global, target_coord)
#                 if distance > self.max_snap_distance * 2:  # Use 2x normal snap distance as threshold for "lost"
#                     should_snap = True
#                     print(f"🎯 DEBUG: Should snap - very far from target (distance: {distance}, threshold: {self.max_snap_distance * 2})")
            
#             if should_snap:
#                 print(f"🎯 DEBUG: SNAPPING - old index {old_idx}")
#                 self.snap_to_nearest_coordinate()
#                 print(f"🎯 DEBUG: SNAPPED - new index {self.current_coordinate_index}")
#             else:
#                 print(f"🎯 DEBUG: SKIPPING SNAP - staying at index {self.current_coordinate_index}")
                
#         except Exception as e:
#             print(f"ConsolidatedNavigator: pre-conversion snap error: {e}")
#             # Fallback to original behavior on error
#             try:
#                 self.snap_to_nearest_coordinate()
#             except:
#                 pass

#         # — Main movement / warp logic
#         try:
#             x, y, map_id = self.env.get_game_coords()
#             current_global = local_to_global(y, x, map_id)

#             current_quest = self.get_current_quest()
#             if not current_quest:
#                 return 3
#             if not self.sequential_coordinates or self.active_quest_id != current_quest:
#                 if not self.load_coordinate_path(current_quest) or not self.sequential_coordinates:
#                     return 3

#             # validate index bounds
#             if self.current_coordinate_index < 0:
#                 self.current_coordinate_index = 0
#             elif self.current_coordinate_index >= len(self.sequential_coordinates):
#                 return original_action

#             # Fetch the map ID that corresponds to the *current* target coordinate.
#             # Using +1 here can raise an IndexError when we are on the **last** node of
#             # the recorded path (current_coordinate_index == len(coords) - 1).  That
#             # uncaught error forces the outer try-except to trigger and the navigator
#             # falls back to returning the hard-coded action "3" (UP) which causes the
#             # avatar to walk into walls.  By referencing the same index we guarantee a
#             # valid lookup for every coordinate on the path.

#             target_coord = self.sequential_coordinates[self.current_coordinate_index]
#             print(f"🔍 DEBUG: TARGET ASSIGNMENT - index={self.current_coordinate_index}, target_coord={target_coord}")
#             print(f"target_coord: {target_coord}")
#             print(f'current_global: {current_global}')
#             # =============================================================
#             # DISTANCE-BASED FALLBACK
#             # -------------------------------------------------------------
#             # If the avatar is *far* from the active-quest node (beyond the
#             # normal snap radius) we may actually be standing on – or very
#             # close to – a node from an earlier quest.  Rather than forcing
#             # a long march toward the distant target, attempt to attach to
#             # the nearest recorded coordinate on the current map.  This is
#             # exactly the situation seen in Route 3 where the avatar loads
#             # on quest 43 node 44 but QuestManager says quest 44 is active.
#             # =============================================================
#             try:
#                 dist_to_target = self._manhattan(current_global, target_coord)
#                 if dist_to_target > self.max_snap_distance:
#                     if self._attach_to_nearest_path_node():
#                         # Re-enter the converter with the newly attached path.
#                         print(
#                             f"ConsolidatedNavigator: Active target is {dist_to_target} tiles away – "
#                             "switching to nearest path node for recovery."
#                         )
#                         return self.convert_path_follow_to_movement_action(original_action)
#             except Exception as e:
#                 print(f"ConsolidatedNavigator: distance-fallback error: {e}")

#             # --- 1️⃣  BUMP INDEX WHEN WE ARRIVE ON A NODE ----------------------
#             print(f"🔍 DEBUG: Before index bump check - current_global={current_global}, target_coord={target_coord}, index={self.current_coordinate_index}")
#             if current_global == target_coord:
#                 print(f"🔍 DEBUG: ARRIVED AT TARGET! current_global={current_global} == target_coord={target_coord}")
#                 print(f"🔍 DEBUG: Before duplicate skip - index={self.current_coordinate_index}")
                
#                 # Skip over any duplicate coordinates that appear consecutively in
#                 # the trace (these typically represent moments where the human
#                 # paused).  Advancing past them prevents oscillation between two
#                 # tiles.
#                 original_index = self.current_coordinate_index
#                 while (
#                     self.current_coordinate_index + 1 < len(self.sequential_coordinates)
#                     and self.sequential_coordinates[self.current_coordinate_index + 1] == current_global
#                 ):
#                     print(f"🔍 DEBUG: Skipping duplicate at index {self.current_coordinate_index + 1}: {self.sequential_coordinates[self.current_coordinate_index + 1]}")
#                     self.current_coordinate_index += 1
                
#                 if self.current_coordinate_index != original_index:
#                     print(f"🔍 DEBUG: Skipped {self.current_coordinate_index - original_index} duplicates, now at index {self.current_coordinate_index}")

#                 # Move to the *next* node (if one exists)
#                 print(f"🔍 DEBUG: About to advance index from {self.current_coordinate_index} to {self.current_coordinate_index + 1}")
#                 if self.current_coordinate_index + 1 < len(self.sequential_coordinates):
#                     self.current_coordinate_index += 1
#                     target_coord = self.sequential_coordinates[self.current_coordinate_index]
#                     print(f"🔍 DEBUG: ADVANCED INDEX! New index={self.current_coordinate_index}, new target={target_coord}")
#                 else:
#                     # 🏁 Reached the final node for the current quest.

#                     # 1️⃣  Mark the quest complete in QuestManager (if present) so
#                     #     get_current_quest() will advance to the next one.
#                     try:
#                         qm = getattr(self.env, 'quest_manager', None)
#                         if qm and hasattr(qm, 'quest_completed_status'):
#                             qm.quest_completed_status[str(current_quest).zfill(3)] = True
#                             # Also update the cached quest status and force the next
#                             # lookup to refresh so get_current_quest() advances to
#                             # the subsequent quest without waiting for the 100 ms
#                             # cache-expiry window.  These attributes are created on
#                             # demand inside QuestManager so guard them defensively.
#                             if hasattr(qm, '_cached_quest_status'):
#                                 qm._cached_quest_status[str(current_quest).zfill(3)] = True
#                             # Reset the last-check timestamp to force a refresh on
#                             # the very next call (the one we make immediately
#                             # below).  This avoids the one-frame stall that was
#                             # preventing the quest from advancing.
#                             if hasattr(qm, '_last_status_check'):
#                                 qm._last_status_check = 0
#                     except Exception as e:
#                         print(f"ConsolidatedNavigator: Could not mark quest {current_quest} complete: {e}")

#                     # 2️⃣  Attempt to load the next quest path automatically.  If
#                     #     none is available we gracefully stop navigating.
#                     next_quest = self.get_current_quest()
#                     if next_quest and next_quest != current_quest:
#                         # 🔒 Don't switch when locked unless it's the expected progression
#                         if not (hasattr(self, 'quest_locked') and self.quest_locked) or next_quest == current_quest + 1:
#                             if self.load_coordinate_path(next_quest):
#                                 print(f"ConsolidatedNavigator: Quest {current_quest} finished. Switching to quest {next_quest}.")
#                                 self.current_coordinate_index = 0
#                                 # Reset fallback flag when transitioning to a new quest
#                                 self._fallback_searched = False
#                                 # Re-enter the converter to generate the first move of the
#                                 # new quest.  A single recursive call is safe because the
#                                 # new path definitely contains ≥1 coordinate and we just
#                                 # reset the index.
#                                 return self.convert_path_follow_to_movement_action(original_action)

#                     # CRITICAL: PROCEED FORWARD LOGIC - Lock onto the closest quest path 
#                     # and proceed forward until the current quest path is found
#                     # This implements the user's core requirement for navigation
#                     if not next_quest or next_quest == current_quest:
#                         # Check if we can reach the actual current quest now
#                         actual_current_quest = self.get_current_quest()
#                         if actual_current_quest and actual_current_quest != current_quest:
#                             # Check if the actual current quest is now reachable
#                             # 🔒 Only switch if not locked or this is the target quest
#                             if not (hasattr(self, 'quest_locked') and self.quest_locked) and self.load_coordinate_path(actual_current_quest):
#                                 try:
#                                     x, y, map_id = self.env.get_game_coords()
                                    
#                                     # Check if any coordinate in actual current quest path is on the same map
#                                     actual_quest_reachable = False
#                                     for i, coord in enumerate(self.sequential_coordinates):
#                                         coord_map = self.coord_map_ids[i] if i < len(self.coord_map_ids) else None
#                                         if coord_map == map_id:
#                                             actual_quest_reachable = True
#                                             break
                                    
#                                     if actual_quest_reachable:
#                                         print(f"ConsolidatedNavigator: Quest {current_quest} finished. Actual current quest {actual_current_quest} is now reachable - switching to it.")
#                                         self.current_coordinate_index = 0
#                                         self.quest_locked = False  # Unlock since we found the target quest
#                                         self._fallback_searched = False
#                                         return self.convert_path_follow_to_movement_action(original_action)
#                                 except Exception as e:
#                                     print(f"ConsolidatedNavigator: Error checking actual quest reachability: {e}")
                        
#                         # Try to load the next sequential quest (current_quest + 1) to proceed forward
#                         sequential_next = current_quest + 1
#                         if self.load_coordinate_path(sequential_next):
#                             print(f"ConsolidatedNavigator: Quest {current_quest} finished. Proceeding forward to quest {sequential_next} (locked path until current quest {actual_current_quest} becomes reachable).")
#                             self.current_coordinate_index = 0
#                             self.quest_locked = True  # Keep locked - we're still proceeding forward
#                             self._fallback_searched = False
#                             return self.convert_path_follow_to_movement_action(original_action)

#                     # No further path to follow – path finished.  Unlock quest
#                     # so normal QuestManager progression can resume.
#                     self.quest_locked = False
#                     # No further path to follow – set navigator idle.
#                     self.navigation_status = "idle"
#                     return None

#             # After potential index bump compute the map for forthcoming node.
#             # Use the map ID of the *current* target coordinate.  Looking one
#             # step ahead caused the navigator to attempt warp logic too early
#             # (e.g. while still several tiles away from a Pokémon Center door),
#             # resulting in incorrect vertical movement into walls.  Relying on
#             # the present node's map ensures we only search for a warp when we
#             # are actually standing on—or adjacent to—the recorded warp tile.
#             target_map = self.coord_map_ids[self.current_coordinate_index]

#             # 🚀 Debug current target after bump
#             print(f"🔍 DEBUG: AFTER ALL INDEX LOGIC - final index={self.current_coordinate_index}, final target={target_coord}")
#             print(f"🚀 active_target_idx={self.current_coordinate_index + 1}, "
#                 f"active_target={target_coord}, active_map={target_map}")

#             # Ensure horizontal (dx) and vertical (dy) deltas are always defined so the
#             # direction-selection logic below can run even when we stay on the same map.
#             # In global coordinate space:  +dy ⇒ move DOWN, +dx ⇒ move RIGHT.
#             dy = target_coord[0] - current_global[0]
#             dx = target_coord[1] - current_global[1]

#             # # ============================= NEW LOOK-AHEAD LOGIC =============================
#             # # If the target coordinate is on the SAME MAP as the player but further than
#             # # the 8-tile radius that the built-in ``find_path`` helper can see, we advance
#             # # the path index until the *next* coordinate that (1) is still on the current
#             # # map and (2) lies within the 8-tile Manhattan window.  This lets the navigator
#             # # gradually 'reel in' distant targets without getting stuck against long
#             # # horizontal cliff faces like Route 3.
#             # if target_map == map_id and (abs(dx) > 8 or abs(dy) > 8):
#             #     original_idx = self.current_coordinate_index
#             #     max_lookahead = 15  # Scan ahead at most this many nodes to stay O(1)
#             #     for step_ahead in range(1, max_lookahead + 1):
#             #         look_idx = original_idx + step_ahead * self._direction
#             #         if look_idx < 0 or look_idx >= len(self.sequential_coordinates):
#             #             break  # Ran off the end of the path
#             #         # Skip nodes that belong to a *different* map segment – we would need a
#             #         # warp to reach those and that logic is handled separately further down.
#             #         if self.coord_map_ids[look_idx] != map_id:
#             #             break
#             #         cand = self.sequential_coordinates[look_idx]
#             #         if self._manhattan(current_global, cand) <= 8:
#             #             # Found a closer, on-screen target – jump the index and recompute dx/dy.
#             #             print(f"Navigator look-ahead: advancing from idx {original_idx} to {look_idx} (dist={self._manhattan(current_global, cand)})")
#             #             self.current_coordinate_index = look_idx
#             #             target_coord = cand
#             #             dy = target_coord[0] - current_global[0]
#             #             dx = target_coord[1] - current_global[1]
#             #             break
#             # # =========================== END LOOK-AHEAD LOGIC =============================

#             # =====================================================================
#             # SIMPLE PATH FOLLOWING MODE - When quest is locked, just follow the path!
#             # =====================================================================
#             if self.quest_locked:
#                 print(f"ConsolidatedNavigator: LOCKED QUEST MODE - Simple path following to {target_coord}")
#                 print(f"ConsolidatedNavigator: Current: {current_global}, Target: {target_coord}, dx={dx}, dy={dy}")
                
#                 # NOTE: Index advancement is already handled by the main logic above (line ~697)
#                 # when current_global == target_coord. We don't need to advance again here.
#                 # Just check if we've reached the end of the path.
#                 if dx == 0 and dy == 0:
#                     print(f"ConsolidatedNavigator: LOCKED MODE - At target coordinate, staying put")
#                     return original_action
                
#                 # Simple movement selection - just move toward target
#                 if dx != 0:
#                     action = 2 if dx > 0 else 1  # RIGHT or LEFT
#                     print(f"ConsolidatedNavigator: LOCKED MODE - Moving {'RIGHT' if dx > 0 else 'LEFT'} (action {action})")
#                     # ----- STUCK GUARD ----------------------------------------------------
#                     chosen_dir = 'right' if dx > 0 else 'left'
#                     if not self._is_direction_walkable(chosen_dir):
#                         self._stuck_counter += 1
#                         if self._stuck_counter >= self._max_stuck_frames:
#                             if self._recover_from_stuck():
#                                 # Recovery routine produced a new action – abort current
#                                 # move so play.py will call us again next frame.
#                                 return original_action
#                     else:
#                         # Successful direction – reset counter
#                         self._stuck_counter = 0
#                     return action
#                 elif dy != 0:
#                     action = 0 if dy > 0 else 3  # DOWN or UP  
#                     print(f"ConsolidatedNavigator: LOCKED MODE - Moving {'DOWN' if dy > 0 else 'UP'} (action {action})")
#                     chosen_dir = 'down' if dy > 0 else 'up'
#                     if not self._is_direction_walkable(chosen_dir):
#                         self._stuck_counter += 1
#                         if self._stuck_counter >= self._max_stuck_frames:
#                             if self._recover_from_stuck():
#                                 return original_action
#                     else:
#                         self._stuck_counter = 0
#                     return action
#                 else:
#                     # This shouldn't happen since we handled dx=0,dy=0 above
#                     print(f"ConsolidatedNavigator: LOCKED MODE - No movement needed")
#                     return original_action
            
#             # =====================================================================
#             # END SIMPLE PATH FOLLOWING MODE
#             # =====================================================================


#             # ---------------------------------------------------------
#             # 2.  RECOVERY LOGIC  –  Always attempt a *local* A* search towards an
#             #     intermediate point that lies on-screen (within the 9 × 10 grid)
#             #     in the general direction of the active target.  This replaces
#             #     the previous implementation which ran only when |dx| and |dy|
#             #     were already ≤ 8 and therefore failed to fire in precisely the
#             #     situations where we need it most (e.g. long horizontal corridors
#             #     blocked by a single decorative tile).  The new heuristic first
#             #     clamps the desired displacement to ±4 tiles so it always falls
#             #     inside the grid, then lets ``env.find_path`` generate a legal
#             #     route that can include side-steps around obstacles.
#             # ---------------------------------------------------------

#             if not self._recovery_steps and target_map == map_id:
#                 # Clamp desired offset so the intermediate goal is guaranteed to
#                 # be visible to ``find_path``.
#                 step_row = 4 + max(-4, min(4, dy))
#                 step_col = 4 + max(-4, min(4, dx))

#                 # Sanity-clamp again to grid boundaries just in case.
#                 step_row = max(0, min(8, step_row))
#                 step_col = max(0, min(9, step_col))

#                 # Skip if already centred on that tile (nothing to plan).
#                 if (step_row, step_col) != (4, 4):
#                     try:
#                         status_msg, path_dirs = self.env.find_path(int(step_row), int(step_col))
#                         if path_dirs:
#                             print(f"Navigator recovery: {status_msg} → {path_dirs}")
#                             # Queue the recovery directions; execute the first one
#                             self._recovery_steps = path_dirs.copy()
#                             next_dir = self._recovery_steps.pop(0)
#                             return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)
#                     except Exception as e:
#                         print(f"Navigator recovery error: {e}")

#             # — WARP HANDLING: handle explicit map transitions via warp tiles —
#             if target_map != map_id:
#                 local_pos = (x, y)
#                 # Only consider warp entries that lead to the desired map
#                 warps_full = WARP_DICT.get(MapIds(map_id).name, [])
#                 # Accept warps that go directly to the desired map or that use the
#                 # special 255 "LAST_MAP" sentinel (meaning they lead back to whatever
#                 # map we came from – which, for a recorded human path, will be the
#                 # desired one).
#                 warps = []
#                 for e in warps_full:
#                     tmid = e.get('target_map_id')
#                     if tmid == target_map or tmid == 255:
#                         warps.append(e)

#                 if warps:
#                     # Find nearest warp tile to current position
#                     best_q: Optional[int] = None
#                     best_idx: Optional[int] = None
#                     best_dist: Optional[int] = None
#                     for entry in warps:
#                         wx, wy = entry.get('x'), entry.get('y')
#                         if wx is None or wy is None:
#                             continue
#                         d = abs(wx - local_pos[0]) + abs(wy - local_pos[1])
#                         if best_dist is None or d < best_dist:
#                             best_dist, best = d, (wx, wy)

#                     if best is not None:
#                         # Compute delta to warp tile
#                         wx, wy = best
#                         dx_warp = wx - local_pos[0]
#                         dy_warp = wy - local_pos[1]

#                         # If we are not yet on the warp tile, step toward it
#                         if dx_warp != 0 or dy_warp != 0:
#                             if abs(dy_warp) > abs(dx_warp):
#                                 return 0 if dy_warp > 0 else 3  # DOWN / UP
#                             else:
#                                 return 2 if dx_warp > 0 else 1   # RIGHT / LEFT

#                         # We are standing on the warp tile – choose the press
#                         # direction based on which map edge the tile touches.
#                         #   • y == 0   → UP   (north edge)
#                         #   • y >= 6   → DOWN (south edge)
#                         #   • x == 0   → LEFT (west edge)
#                         #   • x >= 8   → RIGHT (east edge)
#                         # Fallbacks default to UP which is safe for most gates.
#                         if wy == 0:
#                             return 3  # UP
#                         if wy >= 6:
#                             return 0  # DOWN
#                         if wx == 0:
#                             return 1  # LEFT
#                         if wx >= 8:
#                             return 2  # RIGHT

#                         # Default: press UP
#                         return 3

#             # Evaluate both axes, prioritising the one with greater distance but
#             # *only if* that direction is considered walkable; otherwise swap.
#             primary_first = 'horiz' if abs(dx) >= abs(dy) else 'vert'

#             # Determine preferred directions (primary then secondary)
#             horiz_dir = 2 if dx > 0 else 1 if dx < 0 else None  # RIGHT or LEFT
#             vert_dir  = 0 if dy > 0 else 3 if dy < 0 else None  # DOWN  or UP

#             # -----------------------------------------------------------------
#             # 3.  COLLISION-AWARE CHOICE – consult the environment's live
#             #     collision grid so we don't keep bonking into fences / signs.
#             # -----------------------------------------------------------------

#             try:
#                 valid_moves = set(self.env.get_valid_moves())  # strings: up/down/left/right
#             except Exception:
#                 valid_moves = {"up", "down", "left", "right"}

#             def dir_walkable(d):
#                 """Robust walkability predicate used by the navigation heuristics.

#                 A direction is considered walkable **only if** the *full* tile-pair &
#                 collision-byte check passes.  We intentionally **ignore** the quick
#                 helper ``env.get_valid_moves()`` because it has proven to be overly
#                 optimistic in several edge-cases (e.g. bottom row of Pokémon
#                 Centers where the avatar appears to have a lateral escape that is
#                 actually blocked by decorative tiles).
#                 """

#                 if d is None:
#                     return False

#                 direction_map = {0: "down", 1: "left", 2: "right", 3: "up"}
#                 str_dir = direction_map.get(d)
#                 if str_dir is None:
#                     return False

#                 # Perform the authoritative collision check.  This covers:
#                 #   • basic 18×20 collision grid (solid vs walkable)
#                 #   • tile-pair exclusions (ledges, one-way gates, etc.)
#                 #   • map boundaries / off-screen movement
#                 return self._is_direction_walkable(str_dir)

#             order = [primary_first, 'vert' if primary_first=='horiz' else 'horiz']
#             for axis in order:
#                 chosen = horiz_dir if axis=='horiz' else vert_dir
#                 if chosen is None:
#                     continue

#                 if dir_walkable(chosen):
#                     print(f"Navigator choose {chosen} via {axis} axis (dx={dx}, dy={dy})")
#                     return chosen

#                 # --- Lenient fallback ---
#                 # If the chosen direction would move us **towards** the target
#                 # by exactly one tile on that axis, take it anyway and let the
#                 # game engine / collision map decide.  This prevents soft-locks
#                 # where the helper refuses to step onto perfectly valid tiles
#                 # that happen to lie on the screen boundary.
#                 if axis == 'horiz' and dx != 0 and abs(dx) == 1:
#                     return chosen
#                 if axis == 'vert' and dy != 0 and abs(dy) == 1:
#                     return chosen

#             # ================= NEW SMART FALLBACK =================
#             # At this point none of the preferred axes were passable.  As a more
#             # aggressive recovery strategy we evaluate *all* cardinal directions
#             # that are walkable this frame and pick the one that **most reduces**
#             # the Manhattan distance to the active target coordinate.  This lets
#             # the agent sidestep lateral obstacles (e.g. Route 3 cliff edges)
#             # without requiring a full-map A* search.
#             best_dir = None
#             best_dist = None
#             # Mapping from action int → (dy, dx)
#             DELTAS = {0: (1, 0), 1: (0, -1), 2: (0, 1), 3: (-1, 0)}
#             for cand in [0, 1, 2, 3]:
#                 if not dir_walkable(cand):
#                     continue
#                 dy_c, dx_c = DELTAS[cand]
#                 cand_pos = (current_global[0] + dy_c, current_global[1] + dx_c)
#                 dist = self._manhattan(cand_pos, target_coord)
#                 if best_dist is None or dist < best_dist:
#                     best_dist = dist
#                     best_dir = cand
#             if best_dir is not None:
#                 print(f"Navigator smart-fallback choose {best_dir} (new dist={best_dist})")
#                 return best_dir
#             # =============== END SMART FALLBACK ==================

#             # ---------------- EXPANSIVE EXPLORATORY FALLBACK ----------------
#             # We arrive here when *no* walkable step would bring us closer to the
#             # current target coordinate (best_dir is None) – typically because the
#             # avatar is pressed against a long obstacle (e.g. cliff, building
#             # wall) that blocks horizontal progress.  Instead of giving up and
#             # spamming PATH_FOLLOW_ACTION we deliberately take **any** walkable
#             # step that yields the *smallest* possible increase in Manhattan
#             # distance.  Over successive frames this lets the agent "slide"
#             # alongside the obstacle until the primary axis becomes walkable
#             # again, at which point normal heuristics resume.
#             least_bad_dir = None
#             least_bad_dist = None
#             for cand in [0, 1, 2, 3]:  # DOWN, LEFT, RIGHT, UP
#                 if not dir_walkable(cand):
#                     continue
#                 dy_c, dx_c = DELTAS[cand]
#                 cand_pos = (current_global[0] + dy_c, current_global[1] + dx_c)
#                 dist = self._manhattan(cand_pos, target_coord)
#                 if least_bad_dist is None or dist < least_bad_dist:
#                     least_bad_dist = dist
#                     least_bad_dir = cand

#             if least_bad_dir is not None:
#                 print(
#                     f"Navigator exploratory-fallback choose {least_bad_dir} "
#                     f"(dist={least_bad_dist})"
#                 )
#                 return least_bad_dir

#             # If we get here, assume blocked; return original action to keep game ticking
#             print("Navigator: No walkable direction found, returning PATH_FOLLOW_ACTION unchanged")
#             return original_action

#         except Exception:
#             return 3

#     def _get_noop_action(self) -> int:
#         """Get a no-op action value"""
#         return 3  # UP as safe fallback
    
#     def _execute_locked_quest_navigation(self, original_action: int) -> int:
#         """Execute navigation when locked onto a quest path - NO quest switching allowed"""
#         try:
#             print(f"ConsolidatedNavigator: 🔒 LOCKED NAVIGATION - quest {self.active_quest_id}, index {self.current_coordinate_index}")
            
#             # Ensure we have coordinates loaded for the locked quest
#             if not self.sequential_coordinates:
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - No coordinates loaded, attempting to load quest {self.active_quest_id}")
#                 if not self.load_coordinate_path(self.active_quest_id):
#                     print(f"ConsolidatedNavigator: 🔒 LOCKED - Failed to load quest {self.active_quest_id}, unlocking")
#                     self.quest_locked = False
#                     return original_action
            
#             # Validate index bounds
#             if self.current_coordinate_index < 0:
#                 self.current_coordinate_index = 0
#             elif self.current_coordinate_index >= len(self.sequential_coordinates):
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Reached end of path, checking if target quest is now reachable")
#                 return self._check_target_quest_reachable(original_action)
            
#             # Get current position and target
#             x, y, map_id = self.env.get_game_coords()
#             current_global = local_to_global(y, x, map_id)
#             target_coord = self.sequential_coordinates[self.current_coordinate_index]
            
#             # CRITICAL FIX: Check if target coordinate is on a different map
#             target_map_id = None
#             if self.current_coordinate_index < len(self.coord_map_ids):
#                 target_map_id = self.coord_map_ids[self.current_coordinate_index]
            
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - Current: {current_global} (map {map_id}), Target: {target_coord} (map {target_map_id})")
            
#             # CRITICAL FIX: When locked, NEVER unlock due to cross-map detection
#             # The locked quest path should be followed regardless of map differences
#             # The warp system will handle map transitions automatically
#             if target_map_id is not None and target_map_id != map_id and target_map_id != 999999:
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Target on different map ({target_map_id} vs {map_id}) - STAYING LOCKED and navigating toward target")
#                 # Don't unlock - stay locked and navigate toward the target coordinate
#                 # The warp system will handle map transitions automatically
            
#             # Check if we've reached the current target node
#             if current_global == target_coord:
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Reached target node, advancing index")
#                 if self.current_coordinate_index + 1 < len(self.sequential_coordinates):
#                     self.current_coordinate_index += 1
#                     target_coord = self.sequential_coordinates[self.current_coordinate_index]
#                     print(f"ConsolidatedNavigator: 🔒 LOCKED - Advanced to index {self.current_coordinate_index}, new target: {target_coord}")
#                 else:
#                     print(f"ConsolidatedNavigator: 🔒 LOCKED - Reached end of locked path")
#                     return self._check_target_quest_reachable(original_action)
            
#             # Simple movement toward target
#             # FIXED: Global coordinates are (y, x) format, so:
#             # - target_coord[0] is Y (vertical)
#             # - target_coord[1] is X (horizontal) 
#             # - current_global[0] is Y (vertical)
#             # - current_global[1] is X (horizontal)
#             dy = target_coord[0] - current_global[0]  # Y difference (vertical)
#             dx = target_coord[1] - current_global[1]  # X difference (horizontal)
            
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - Movement: dy={dy} (vertical), dx={dx} (horizontal)")
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - Target Y={target_coord[0]}, Current Y={current_global[0]}")
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - Target X={target_coord[1]}, Current X={current_global[1]}")
            
#             # Move toward target (prioritize vertical movement)
#             if dy != 0:
#                 action = 0 if dy > 0 else 3  # DOWN or UP
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Moving {'DOWN' if dy > 0 else 'UP'} (action {action})")
#                 return action
#             elif dx != 0:
#                 action = 2 if dx > 0 else 1  # RIGHT or LEFT
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Moving {'RIGHT' if dx > 0 else 'LEFT'} (action {action})")
#                 return action
#             else:
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Already at target, staying put")
#                 return original_action
                
#         except Exception as e:
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - Error in locked navigation: {e}")
#             self.quest_locked = False  # Unlock on error
#             return original_action
    
#     def _check_target_quest_reachable(self, original_action: int) -> int:
#         """Check if the target quest is now reachable from current position"""
#         try:
#             current_quest = self.get_current_quest()
#             if not current_quest:
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - No current quest, unlocking")
#                 self.quest_locked = False
#                 return original_action
            
#             # CRITICAL FIX: Always unlock if QuestManager indicates a different quest
#             # Don't wait for the target quest to be reachable - let the fallback logic handle it
#             if current_quest != self.active_quest_id:
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - QuestManager wants quest {current_quest}, currently locked on {self.active_quest_id}")
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - FORCE UNLOCKING to allow quest switch")
#                 self.quest_locked = False
#                 self._fallback_searched = False  # Reset fallback flag to allow new search
#                 return self.convert_path_follow_to_movement_action(original_action)
            
#             # If we're locked on the same quest as QuestManager wants, continue forward
#             # Try to continue forward to next quest in sequence
#             next_quest = self.active_quest_id + 1
#             if self.load_coordinate_path(next_quest):
#                 print(f"ConsolidatedNavigator: 🔒 LOCKED - Continuing forward to quest {next_quest}")
#                 self.active_quest_id = next_quest
#                 self.current_coordinate_index = 0
#                 return self.convert_path_follow_to_movement_action(original_action)
            
#             # No further navigation possible
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - No further navigation possible, unlocking")
#             self.quest_locked = False
#             return original_action
            
#         except Exception as e:
#             print(f"ConsolidatedNavigator: 🔒 LOCKED - Error checking target quest: {e}")
#             self.quest_locked = False
#             return original_action

#     # =========================
#     # COORDINATE AND MOVEMENT UTILITIES
#     # =========================
    
#     def _get_player_global_coords(self) -> Optional[Tuple[int, int]]:
#         """Get player's current global coordinates"""
#         if not hasattr(self.env, "get_game_coords"):
#             print("ConsolidatedNavigator: env lacks get_game_coords()")
#             return None
#         try:
#             lx, ly, map_id = self.env.get_game_coords()
#             gy, gx = local_to_global(ly, lx, map_id)
            
#             if lx < 0 or ly < 0 or map_id < 0:
#                 print(f"ConsolidatedNavigator: Invalid coordinates: local=({lx},{ly}), map={map_id}")
#                 return None
                
#             return gy, gx
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error reading coords: {e}")
#             return None

#     @staticmethod
#     def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
#         """Calculate Manhattan distance between two points"""
#         return abs(a[0] - b[0]) + abs(a[1] - b[1])

#     def _execute_movement(self, action: int, bypass_collision: bool = False) -> bool:
#         """Execute a movement action with collision detection"""
#         print(f"ConsolidatedNavigator: Executing movement action {action}")
        
#         # Safety checks
#         if self.env.read_m("wIsInBattle") != 0:
#             print("ConsolidatedNavigator: Movement blocked - in battle")
#             return False
            
#         dialog = self.env.read_dialog()
#         if dialog and dialog.strip():
#             print(f"ConsolidatedNavigator: Movement blocked - dialog active")
#             return False
        
#         # Collision check (unless bypassing for warp activation)
#         if not bypass_collision:
#             direction_map = {0: "down", 1: "left", 2: "right", 3: "up"}
#             direction = direction_map.get(action)
            
#             if direction and not self._is_direction_walkable(direction):
#                 print(f"ConsolidatedNavigator: Movement blocked - direction {direction} not walkable")
#                 return False
        
#         # Store position before movement
#         pos_before = self.env.get_game_coords()
        
#         # Execute the movement
#         obs, reward, done, truncated, info = self.env.process_action(action, source="ConsolidatedNavigator")
        
#         # Check if position changed
#         pos_after = self.env.get_game_coords()
#         moved = pos_before != pos_after
        
#         print(f"ConsolidatedNavigator: Movement result - Before: {pos_before}, After: {pos_after}, Moved: {moved}")
        
#         # Track movement failures
#         if not moved:
#             self.movement_failure_count += 1
#         else:
#             self.movement_failure_count = 0
#             self._stuck_counter = 0
#             self.record_warp_step()  # Record successful movement
        
#         return moved

#     def _is_direction_walkable(self, direction: str) -> bool:
#         """Check if a direction is walkable using collision detection"""
#         try:
#             # -------------------------------------------------------------
#             # FAST-PATH: use the environment's built-in helper first.  In all
#             # field-testing so far the list returned by ``env.get_valid_moves``
#             # has proven reliable for *basic* passability even though it does
#             # not perform the deeper tile-pair check.  Relying on it as the
#             # primary predicate prevents the navigator from falsely treating
#             # perfectly valid tiles as blocked – the root cause of the avatar
#             # freeze the user keeps experiencing.  Only if the helper either
#             # (a) is unavailable **or** (b) does **not** list the queried
#             # direction do we fall back to the slower, byte-level logic.
#             # -------------------------------------------------------------
#             try:
#                 quick_moves = set(self.env.get_valid_moves())  # {"up", "down", ...}
#                 if direction in quick_moves:
#                     return True
#             except Exception:
#                 # Helper not available – continue with detailed check
#                 pass

#             # 1️⃣  Obtain the live collision grid (18×20, values 0/1)
#             collision_grid = self._get_collision_map_grid()
#             # Handle numpy arrays correctly – evaluating them in a boolean
#             # context raises "ambiguous truth value" errors.  Use explicit
#             # checks for *None* or empty instead.
#             if collision_grid is None:
#                 return True  # Fail-open – cannot determine, assume walkable

#             # ``game_area_collision`` occasionally returns an **empty** array
#             # for a frame when the window has just been resized or the PyBoy
#             # back-buffer is being refreshed.  Treat that the same as "no
#             # data" so we do not crash the navigator.
#             try:
#                 if hasattr(collision_grid, "size") and collision_grid.size == 0:  # numpy.ndarray
#                     return True
#             except Exception:
#                 # Not a numpy array – leave existing behaviour untouched
#                 pass

#             # 2️⃣  Locate the player in that grid (screen-relative coordinates)
#             full_map = self.env.pyboy.game_wrapper._get_screen_background_tilemap()
#             player_row, player_col = self.env._get_player_center(full_map)

#             # 3️⃣  Compute target tile coordinates one step in the requested direction
#             deltas = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
#             d_row, d_col = deltas.get(direction, (0, 0))
#             tgt_row = player_row + d_row
#             tgt_col = player_col + d_col

#             # Bounds check
#             if tgt_row < 0 or tgt_row >= len(collision_grid) or tgt_col < 0 or tgt_col >= len(collision_grid[0]):
#                 return False

#             # 4️⃣  Tile-pair collision (ledge / one-way) check – requires tile ids
#             try:
#                 tileset = self.env.read_tileset()
#                 curr_tile = full_map[player_row][player_col]
#                 next_tile = full_map[tgt_row][tgt_col]
#                 if not self.env._can_move_between_tiles(curr_tile, next_tile, tileset):
#                     return False
#             except Exception as e:
#                 print(f"ConsolidatedNavigator: tile-pair check failed ({e}) – falling back to collision byte")

#             # 5️⃣  Basic collision byte semantics appear to be the reverse of
#             # what we originally assumed.  In-game testing shows that tiles
#             # whose *collision byte* is **non-zero** are impassable, while a
#             # value of **0** means the tile can be stepped on.  (We verified
#             # this by seeing the navigator report RIGHT as walkable – because
#             # the byte was 1 – even though the avatar was unable to move.)

#             # Therefore we flip the predicate so the navigator only allows a
#             # movement when the target byte is **zero**.
#             return collision_grid[tgt_row][tgt_col] == 0
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error checking walkability: {e}")
#             return True  # Default to walkable on error
#     def _get_collision_map_grid(self) -> Optional[list]:
#         """Get the collision map as a grid"""
#         try:
#             return self.env.pyboy.game_area_collision()
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error getting collision map: {e}")
#             return None

#     def _get_player_collision_position(self) -> Tuple[int, int]:
#         """Get player position in collision grid coordinates"""
#         try:
#             # Use the environment's helper to locate the player's 2×2 sprite
#             # block within the **screen-relative 18×20 collision grid**.
#             full_map = self.env.pyboy.game_wrapper._get_screen_background_tilemap()
#             pr, pc = self.env._get_player_center(full_map)
#             return pr, pc
#         except:
#             return 9, 8  # Default center position

#     # =========================
#     # COORDINATE PATH MANAGEMENT
#     # =========================
    
#     def load_coordinate_path(self, quest_id: int) -> bool:
#         """Load coordinate sequence for quest_id from JSON file"""
#         if quest_id is None:
#             return False

#         # --- Early exit: quest already loaded with coordinates -------------
#         if self.active_quest_id == quest_id and self.sequential_coordinates:
#             # Nothing to do; keep current index/progress intact.
#             return True

#         # 🔒 ANTI-OSCILLATION GUARD: Don't allow quest switching when locked
#         if hasattr(self, 'quest_locked') and self.quest_locked and hasattr(self, 'active_quest_id') and self.active_quest_id != quest_id:
#             print(f"ConsolidatedNavigator: 🔒 BLOCKED quest switch from {self.active_quest_id} to {quest_id} (quest locked)")
#             return False

#         # Reset stale navigation state when switching quests
#         if hasattr(self, 'active_quest_id') and self.active_quest_id != quest_id:
#             self._reset_navigation_state()

#         # Clear direction tracking
#         self._direction = 1  # 1=forward, -1=backward

#         self.active_quest_id = quest_id
#         coord_path = Path(__file__).parent / "quest_paths" / f"{quest_id:03d}" / f"{quest_id:03d}_coords.json"
#         if not coord_path.exists():
#             print(f"ConsolidatedNavigator: No coordinate file found for quest {quest_id}")
#             return False

#         try:
#             with coord_path.open() as f:
#                 data = json.load(f)
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error loading quest {quest_id} coordinates: {e}")
#             return False

#         # CRITICAL FIX: Preserve recording order instead of sorting numerically
#         # Quest coordinates must be loaded in the order they were recorded,
#         # not in numerical map order. Sorting destroys the intended sequence.
#         # JSON dictionaries in Python 3.7+ preserve insertion order.
#         def parse_segment_key(key):
#             # Handle both formats: plain map IDs ("68", "15") and underscore-separated ("68_0", "15_1")
#             if '_' in key:
#                 # Format: "map_id_segment" like "68_0"
#                 parts = key.split('_')
#                 try:
#                     map_id = int(parts[0])
#                     segment_id = int(parts[1]) if len(parts) > 1 else 0
#                     return (map_id, segment_id)
#                 except (ValueError, IndexError):
#                     print(f"ConsolidatedNavigator: WARNING - Invalid segment key format: '{key}', skipping")
#                     return None
#             else:
#                 # Format: just "map_id" like "68"
#                 try:
#                     map_id = int(key)
#                     return (map_id, 0)  # Default segment_id to 0
#                 except ValueError:
#                     print(f"ConsolidatedNavigator: WARNING - Invalid map key format: '{key}', skipping")
#                     return None

#         # Use keys in their original recording order (JSON preserves insertion order)
#         ordered_keys = list(data.keys())

#         # Flatten coordinates from all segments in recording order
#         self.sequential_coordinates = []
#         self.coord_map_ids = []
#         for key in ordered_keys:
#             parsed_key = parse_segment_key(key)
#             if parsed_key is None:
#                 continue  # Skip invalid keys
#             map_id = parsed_key[0]
#             coords = data[key]
#             for coord in coords:
#                 self.sequential_coordinates.append((coord[0], coord[1]))
#                 self.coord_map_ids.append(map_id)

#         # Note: Removed oscillation guard cleaning to preserve raw coordinate sequence

#         if not self.sequential_coordinates:
#             print(f"ConsolidatedNavigator: No coordinates loaded for quest {quest_id}")
#             return False

#         print(f"ConsolidatedNavigator: Loaded {len(self.sequential_coordinates)} coordinates for quest {quest_id} after oscillation filter")
#         # Reset index when loading a new path
#         self.current_coordinate_index = 0
#         self.navigation_status = "active"
#         return True

#     def _reset_navigation_state(self):
#         """Reset navigation state flags to prevent stale state issues"""
#         # 🎯 OSCILLATION FIX: Don't reset fallback search when we're locked on a quest path
#         # This prevents constant re-fallback that causes oscillation
#         if not (hasattr(self, 'quest_locked') and self.quest_locked):
#             if hasattr(self, '_fallback_searched'):
#                 self._fallback_searched = False
#                 print("ConsolidatedNavigator: Reset _fallback_searched = False (not locked)")
#         else:
#             print("ConsolidatedNavigator: KEEPING _fallback_searched state (quest locked)")
#         if hasattr(self, '_recursion_depth'):
#             self._recursion_depth = 0
#         self.movement_failure_count = 0
#         self._recovery_steps = []
#         print("ConsolidatedNavigator: Reset navigation state")

#     def snap_to_nearest_coordinate(self) -> bool:
#         """Snap the navigator index to the nearest coordinate based on current position"""
#         if not self.sequential_coordinates:
#             if not self._ensure_quest_loaded():
#                 return False

#         try:
#             cur_pos = self._get_player_global_coords()
#             if not cur_pos:
#                 return False

#             # ==================== CROSS-QUEST SNAP LOGIC (FIXED) ====================
#             # The previous cross-quest snap logic was causing oscillation between quests
#             # when the player was standing on coordinates that existed in multiple quest paths.
#             # NEW LOGIC: Only snap to a different quest if:
#             # 1. Current quest is completed AND no path is loaded, OR
#             # 2. Current quest path is invalid/missing, OR  
#             # 3. Distance to alternative quest is significantly better (not just minimally better)
            
#             try:
#                 current_map_id = self.env.get_game_coords()[2]
#             except Exception:
#                 current_map_id = None

#             if current_map_id is not None:
#                 # Get the quest that should be active according to quest manager
#                 quest_manager_current = None
#                 if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
#                     quest_manager_current = self.env.quest_manager.get_current_quest()
                
#                 # Check if we should consider cross-quest snapping at all
#                 should_cross_quest_snap = False
                
#                 # Case 1: No quest currently loaded in navigator
#                 if not self.sequential_coordinates or self.active_quest_id is None:
#                     should_cross_quest_snap = True
#                     print("ConsolidatedNavigator: Cross-quest snap enabled - no quest loaded")
                
#                 # Case 2: Current quest is completed but still loaded
#                 elif (self.active_quest_id is not None and 
#                       self._is_quest_completed(self.active_quest_id)):
#                     should_cross_quest_snap = True
#                     print(f"ConsolidatedNavigator: Cross-quest snap enabled - quest {self.active_quest_id} completed")
                
#                 # Case 3: Quest manager indicates different quest AND we're locked on wrong quest
#                 elif (quest_manager_current is not None and 
#                       quest_manager_current != self.active_quest_id and 
#                       self.quest_locked):
#                     # Check distance to current quest vs quest manager's quest
#                     current_quest_dist = None
#                     if self.sequential_coordinates:
#                         distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
#                         current_quest_dist = min(distances) if distances else None
                    
#                     # Only snap if significantly far from current quest (> 5 tiles)
#                     if current_quest_dist is None or current_quest_dist > 5:
#                         should_cross_quest_snap = True
#                         print(f"ConsolidatedNavigator: Cross-quest snap enabled - quest manager wants {quest_manager_current}, locked on {self.active_quest_id}, distance {current_quest_dist}")
                
#                 if should_cross_quest_snap:
#                     nearest_q = None
#                     nearest_idx = None
#                     nearest_dist = None

#                     quest_root = Path(__file__).parent / "quest_paths"
#                     for qdir in quest_root.iterdir():
#                         if not qdir.is_dir():
#                             continue
#                         try:
#                             qid = int(qdir.name)
#                         except ValueError:
#                             continue

#                         qfile = qdir / f"{qdir.name}_coords.json"
#                         if not qfile.exists():
#                             continue
#                         try:
#                             qdata = json.load(qfile.open())
#                         except Exception:
#                             continue

#                         flat_i = 0
#                         for seg_key, seg in qdata.items():
#                             try:
#                                 seg_map = int(seg_key.split("_")[0])
#                             except Exception:
#                                 seg_map = None
#                             for coord in seg:
#                                 if seg_map == current_map_id:
#                                     d = self._manhattan(cur_pos, (coord[0], coord[1]))
#                                     if nearest_dist is None or d < nearest_dist:
#                                         nearest_dist = d
#                                         nearest_q = qid
#                                         nearest_idx = flat_i
#                                 flat_i += 1

#                     # Apply stricter criteria for cross-quest snapping
#                     if (nearest_q is not None and 
#                         nearest_dist is not None and 
#                         nearest_dist <= self.max_snap_distance):
                        
#                         # Prefer quest manager's choice if available and close enough
#                         if quest_manager_current is not None:
#                             quest_manager_dist = None
#                             quest_manager_idx = None
                            
#                             # Check distance to quest manager's preferred quest
#                             qm_file = quest_root / f"{quest_manager_current:03d}" / f"{quest_manager_current:03d}_coords.json"
#                             if qm_file.exists():
#                                 try:
#                                     qm_data = json.load(qm_file.open())
#                                     flat_i = 0
#                                     for seg_key, seg in qm_data.items():
#                                         try:
#                                             seg_map = int(seg_key.split("_")[0])
#                                         except Exception:
#                                             seg_map = None
#                                         for coord in seg:
#                                             if seg_map == current_map_id:
#                                                 d = self._manhattan(cur_pos, (coord[0], coord[1]))
#                                                 if quest_manager_dist is None or d < quest_manager_dist:
#                                                     quest_manager_dist = d
#                                                     quest_manager_idx = flat_i
#                                             flat_i += 1
                                    
#                                     # Use quest manager's quest if it's reasonably close (within 3 tiles of nearest)
#                                     if (quest_manager_dist is not None and 
#                                         quest_manager_dist <= self.max_snap_distance and
#                                         quest_manager_dist <= nearest_dist + 3):
#                                         nearest_q = quest_manager_current
#                                         nearest_idx = quest_manager_idx
#                                         nearest_dist = quest_manager_dist
#                                         print(f"ConsolidatedNavigator: Preferring quest manager's choice {quest_manager_current} (dist={quest_manager_dist})")
#                                 except Exception:
#                                     pass
                        
#                         # Only snap if it's a different quest
#                         if nearest_q != self.active_quest_id:
#                             if self.load_coordinate_path(nearest_q):
#                                 # Guard: if we just snapped to the *final* node of a quest that is already
#                                 # marked completed, do *not* lock onto it
#                                 if (nearest_idx is not None and 
#                                     nearest_idx >= len(self.sequential_coordinates) - 1 and 
#                                     self._is_quest_completed(nearest_q)):
#                                     print(f"ConsolidatedNavigator: Skipping snap – nearest coordinate is the final node of already-completed quest {nearest_q}.")
                                    
#                                     # Try next quest instead
#                                     next_q_candidate = nearest_q + 1
#                                     loaded_next = False
#                                     try:
#                                         if self.load_coordinate_path(next_q_candidate):
#                                             self.current_coordinate_index = 0
#                                             self.quest_locked = False
#                                             print(f"ConsolidatedNavigator: Loaded next quest {next_q_candidate} after completed {nearest_q}.")
#                                             loaded_next = True
#                                     except Exception:
#                                         pass

#                                     if not loaded_next and quest_manager_current and quest_manager_current != nearest_q:
#                                         self.load_coordinate_path(quest_manager_current)
#                                 else:
#                                     self.current_coordinate_index = nearest_idx or 0
#                                     # Only lock if we're not following quest manager's active quest
#                                     if quest_manager_current is None or nearest_q != quest_manager_current:
#                                         self.quest_locked = True
#                                     else:
#                                         self.quest_locked = False  # Allow normal progression
#                                     self.movement_failure_count = 0
#                                     print(f"ConsolidatedNavigator: Cross-quest snap → quest {nearest_q} at index {self.current_coordinate_index} (dist={nearest_dist}) – quest_locked={self.quest_locked}")
#                                     return True
#             # ==================== END CROSS-QUEST SNAP LOGIC ====================

#             # Prefer snapping to coordinates on the **current map** to avoid the
#             # situation where we warp into a building and then immediately
#             # snap to a (slightly) closer coordinate that belongs to the *old*
#             # map outside – this caused an infinite in-out loop for Pewter Gym
#             # (Quest 039).  We therefore:
#             #   1. Build two distance lists – one filtered to the current map
#             #      and the other across *all* coordinates.
#             #   2. If we have at least one coordinate on the current map we
#             #      choose the nearest among *those*; otherwise we fall back to
#             #      the global nearest (across maps) to preserve previous
#             #      behaviour for single-map quests.

#             cur_map_id = self.env.get_game_coords()[2]

#             distances_all = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]

#             # Filter indices that are on the same map as the player
#             same_map_indices = [i for i, m_id in enumerate(self.coord_map_ids) if m_id == cur_map_id]

#             if same_map_indices:
#                 distances_same_map = [distances_all[i] for i in same_map_indices]
#                 nearest_same_map_i = same_map_indices[distances_same_map.index(min(distances_same_map))]
#                 nearest_i = nearest_same_map_i
#                 dist = distances_all[nearest_i]
#             else:
#                 # Fallback to global nearest
#                 nearest_i = distances_all.index(min(distances_all))
#                 dist = distances_all[nearest_i]

#             # Only snap if distance is reasonable
#             if dist <= self.max_snap_distance:
#                 # If the nearest coordinate is the *final* node we need to decide whether this is the
#                 # *initial* path alignment (player starts outside the destination) **or** we have *just
#                 # finished* walking the path.  Resetting back to the beginning in the latter case causes
#                 # an infinite in-out loop (observed for Quest 039 Pewter Gym).

#                 if nearest_i >= len(self.sequential_coordinates) - 1:
#                     # Add guard to prevent infinite loop when quest is already completed
#                     if (hasattr(self, 'active_quest_id') and self.active_quest_id is not None 
#                         and self._is_quest_completed(self.active_quest_id)):
#                         print(
#                             f"ConsolidatedNavigator: Final node snap blocked - quest {self.active_quest_id} already completed"
#                         )
#                         return False
                        
#                     # CASE 1 – Initial alignment: navigator has not advanced along the path yet
#                     # (index still at 0).  We *do* want to rewind so that the agent walks the
#                     # pre-recorded path from the start and enters the building.
#                     if self.current_coordinate_index == 0:
#                         print(
#                             "ConsolidatedNavigator: Nearest coordinate is the final node on initial alignment – "
#                             "resetting index to 0 to follow the full path forward."
#                         )
#                         self.current_coordinate_index = 0
#                         self.movement_failure_count = 0
#                         return True

#                     # CASE 2 – Path already executed and we have legitimately reached the last node.
#                     # Keep the index at the final coordinate so the navigator recognises completion and
#                     # does *not* restart the path.
#                     print(
#                         "ConsolidatedNavigator: Reached final coordinate – maintaining index at end of path."
#                     )
#                     self.current_coordinate_index = nearest_i
#                     self.movement_failure_count = 0
#                     return True

#                 old_index = self.current_coordinate_index
#                 # Only snap forward, avoid rewinding index on path with repeating coords
#                 if nearest_i > old_index:
#                     self.current_coordinate_index = nearest_i
#                     print(f"ConsolidatedNavigator: Snapped from index {old_index} to {nearest_i}")
#                 else:
#                     # retain existing index when nearest coordinate is behind or same
#                     pass
                
#                 self.movement_failure_count = 0
#                 if self.current_coordinate_index >= len(self.sequential_coordinates):
#                     self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)

#                 # Additional safeguard: after the initial reset we may be called again *before* any movement
#                 # occurs.  In that situation we are still standing outside (nearest == final) **but** the
#                 # index is already 0.  Advancing to `nearest_i` would immediately skip the whole path.  If
#                 # we detect this exact scenario, simply keep the index at 0 and return.
#                 if self.current_coordinate_index == 0 and nearest_i >= len(self.sequential_coordinates) - 1:
#                     return True

#                 return True
#             else:
#                 # -------------------------------------------------------------
#                 # 🌟 MAP-LOCAL FALLBACK 🌟
#                 # We are too far from *any* node of the *current* quest path.
#                 # Instead of giving up, scan **all** recorded quest traces for
#                 # the *nearest* coordinate that sits on the *current map*.
#                 # This lets the avatar re-attach to earlier/future quest paths
#                 # (e.g. Route 3 → Route 4 transition) and walk itself back to
#                 # the intended storyline without manual intervention.
#                 # -------------------------------------------------------------

#                 best_q: Optional[int] = None
#                 best_idx: Optional[int] = None
#                 best_d: Optional[int] = None

#                 quest_base = Path(__file__).parent / "quest_paths"
#                 for qdir in quest_base.iterdir():
#                     if not qdir.is_dir():
#                         continue
#                     try:
#                         qid = int(qdir.name)
#                     except ValueError:
#                         continue

#                     qfile = qdir / f"{qdir.name}_coords.json"
#                     if not qfile.exists():
#                         continue

#                     try:
#                         qdata = json.load(qfile.open())
#                     except Exception:
#                         continue

#                     # Iterate over segments that belong to *current_map_id*
#                     for seg_key, seg_coords in qdata.items():
#                         seg_map_id = int(seg_key.split("_")[0])
#                         if seg_map_id != cur_map_id:
#                             continue

#                         for idx_rel, (gy, gx) in enumerate(seg_coords):
#                             d_tmp = self._manhattan(cur_pos, (gy, gx))
#                             if best_d is None or d_tmp < best_d:
#                                 best_d = d_tmp
#                                 best_q = qid
#                                 # Index within *flattened* path after load
#                                 best_idx = None  # fill later after loading

#                 # If we found a usable coordinate within the normal snap radius
#                 if best_q is not None and best_d is not None and best_d <= self.max_snap_distance:
#                     if self.load_coordinate_path(best_q):
#                         # Determine absolute index now that path is loaded
#                         try:
#                             # Recompute distances against the *loaded* list –
#                             # this is cheaper than tracking index during scan.
#                             dists_tmp = [self._manhattan(cur_pos, c) for c in self.sequential_coordinates]
#                             self.current_coordinate_index = dists_tmp.index(min(dists_tmp))
#                             print(
#                                 f"ConsolidatedNavigator: Fallback snapped to quest {best_q} at index {self.current_coordinate_index} (dist={best_d})"
#                             )
#                             self.active_quest_id = best_q
#                             self.movement_failure_count = 0
#                             return True
#                         except Exception as e:
#                             print(f"ConsolidatedNavigator: fallback snap error: {e}")
#                             # Fall through to original behaviour
#                 print(f"ConsolidatedNavigator: Distance too large ({dist}), not snapping")
#                 return False
                
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error in snap_to_nearest_coordinate: {e}")
#             return False

#     def _ensure_quest_loaded(self) -> bool:
#         """Ensure a quest is loaded"""
#         current_quest = self.get_current_quest()
#         if current_quest:
#             return self.load_coordinate_path(current_quest)
#         return False

#     # =========================
#     # WARP HANDLING
#     # =========================
    
#     def warp_tile_handler(self) -> bool:
#         """Handle warp tile interactions"""
#         print(f"ConsolidatedNavigator: warp_tile_handler called")
        
#         # Check cooldown
#         if (time.time() - self.last_warp_time) < self.WARP_COOLDOWN_SECONDS:
#             return False

#         try:
#             local_x, local_y, cur_map = self.env.get_game_coords()
#             local_pos = (local_x, local_y)
            
#             warp_entries_for_cur_map = WARP_DICT.get(MapIds(cur_map).name, [])
#             if not warp_entries_for_cur_map:
#                 return False

#             # Find nearest warp
#             nearest_warp_tile_local = None
#             for entry in warp_entries_for_cur_map:
#                 warp_x, warp_y = entry.get("x"), entry.get("y")
#                 if warp_x is not None and warp_y is not None:
#                     distance = self._manhattan(local_pos, (warp_x, warp_y))
#                     if distance <= 1:  # Adjacent or on warp tile
#                         nearest_warp_tile_local = (warp_x, warp_y)
#                         break

#             if not nearest_warp_tile_local:
#                 return False
            
#             # Get active warp entry
#             active_warp_entry = next((e for e in warp_entries_for_cur_map 
#                                     if (e.get("x"), e.get("y")) == nearest_warp_tile_local), None)
#             if not active_warp_entry:
#                 return False
                
#             target_map_id = active_warp_entry.get("target_map_id")
#             if target_map_id is None:
#                 return False

#             # Handle LAST_MAP special case
#             print(f'target_map_id: {target_map_id}')
#             if target_map_id == 255:
#                 return True
#                 # if len(self.map_history) >= 2:
#                 #     target_map_id = self.map_history[-2]
#                 # else:
#                 #     target_map_id = 0  # Fallback

#             print(f"ConsolidatedNavigator: Executing warp to map {target_map_id}")
            
#             # Calculate direction to warp
#             dx = nearest_warp_tile_local[0] - local_pos[0]
#             dy = nearest_warp_tile_local[1] - local_pos[1]
            
#             # Determine movement action
#             if abs(dy) > abs(dx):
#                 movement_action = 0 if dy > 0 else 3  # DOWN or UP
#             elif dx != 0:
#                 movement_action = 2 if dx > 0 else 1  # RIGHT or LEFT
#             else:
#                 movement_action = 0  # DOWN as default
            
#             # Execute warp movement
#             moved = self._execute_movement(movement_action, bypass_collision=True)
            
#             if moved:
#                 self.last_warp_time = time.time()
#                 self.record_warp_step()
#                 return True
                
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error in warp_tile_handler: {e}")
            
#         return False

#     def get_available_warps_on_current_map(self) -> List[Dict]:
#         """Get available warps on the current map"""
#         try:
#             _, _, cur_map = self.env.get_game_coords()
#             warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
            
#             available_warps = []
#             for entry in warp_entries:
#                 warp_x, warp_y = entry.get("x"), entry.get("y")
#                 target_map_id = entry.get("target_map_id")
                
#                 if warp_x is not None and warp_y is not None and target_map_id is not None:
#                     available_warps.append({
#                         'local_coords (x, y)': (warp_x, warp_y),
#                         'target_map_id': target_map_id,
#                         'position (x, y)': (warp_x, warp_y),  # Legacy compatibility
#                         'destination_map': target_map_id  # Legacy compatibility
#                     })
            
#             return available_warps
            
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error getting available warps: {e}")
#             return []

#     def is_warp_aligned_with_path(self, warp_info: Dict) -> bool:
#         """Check if a warp is aligned with the current quest path"""
#         if warp_info.get('target_map_id') == 255:
#             return True
        
#         if not self.sequential_coordinates or not self.coord_map_ids:
#             return False
            
#         target_map_id = warp_info.get('target_map_id')
#         if target_map_id is None:
#             return False
        
#         # Look ahead in the path for coordinates on the target map
#         for i in range(self.current_coordinate_index, len(self.coord_map_ids)):
#             if self.coord_map_ids[i] == target_map_id:
#                 return True
                
#         return False

#     # =========================
#     # NAVIGATION CONTROL METHODS
#     # =========================
    
#     def navigate_to_coordinate(self) -> bool:
#         """Navigate to the current target coordinate"""
#         if not self.sequential_coordinates:
#             return False
            
#         if self.current_coordinate_index >= len(self.sequential_coordinates):
#             return False
            
#         target_coord = self.sequential_coordinates[self.current_coordinate_index]
#         cur_pos = self._get_player_global_coords()
        
#         if not cur_pos:
#             return False
            
#         # Check if already at target
#         if cur_pos == target_coord:
#             self.current_coordinate_index += 1
#             return True
            
#         # Calculate movement direction
#         dy = target_coord[0] - cur_pos[0]
#         dx = target_coord[1] - cur_pos[1]
        
#         # Choose direction based on larger delta
#         if abs(dy) > abs(dx):
#             action = 0 if dy > 0 else 3  # DOWN or UP
#         elif dx != 0:
#             action = 2 if dx > 0 else 1  # RIGHT or LEFT
#         else:
#             return True  # Already at target
            
#         # Execute movement
#         return self._execute_movement(action)

#     def get_current_status(self) -> str:
#         """Get comprehensive navigator status"""
#         pos = self._get_player_global_coords()
#         try:
#             cur_map = self.env.get_game_coords()[2]
#         except:
#             cur_map = "?"
            
#         s = ["\n*** CONSOLIDATED NAVIGATOR STATUS ***"]
#         s.append(f"Quest ID       : {self.active_quest_id}")
#         s.append(f"Current pos    : {pos}")
#         s.append(f"Current map    : {cur_map}")
#         s.append(f"Path length    : {len(self.sequential_coordinates)}")
#         s.append(f"Current index  : {self.current_coordinate_index}")
#         s.append(f"Direction      : {self._direction}")

#         if self.sequential_coordinates and 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
#             tgt = self.sequential_coordinates[self.current_coordinate_index]
#             dist = self._manhattan(pos, tgt) if pos else "?"
#             s.append(f"Current target : {tgt} (dist {dist})")
            
#         # Show available warps
#         warps = self.get_available_warps_on_current_map()
#         if warps:
#             s.append(f"Available warps: {len(warps)}")
#             for warp in warps[:3]:
#                 aligned = self.is_warp_aligned_with_path(warp)
#                 s.append(f"  -> Map {warp['target_map_id']} ({'ALIGNED' if aligned else 'not aligned'})")

#         return "\n".join(s)

#     def handle_pygame_event(self, event) -> Optional[int]:
#         """
#         Handle pygame keyboard events and return corresponding action indices.
#         Simple converter that maps pygame events to action integers.
#         Navigation logic is handled elsewhere in the system.
        
#         Args:
#             event: pygame event object
            
#         Returns:
#             int: Action index for the pressed key, or None for unhandled events
#         """
#         # Import pygame here to avoid import issues if pygame is not available
#         try:
#             import pygame
#         except ImportError:
#             return None
            
#         if event.type != pygame.KEYDOWN:
#             return None
            
#         # Import constants from environment module
#         try:
#             from environment.environment import VALID_ACTIONS, PATH_FOLLOW_ACTION
#             from pyboy.utils import WindowEvent
#         except ImportError:
#             return None
        
#         # Simple action mapping - mirrors the one defined in play.py
#         ACTION_MAPPING = {
#             pygame.K_DOWN: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN),
#             pygame.K_LEFT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT),
#             pygame.K_RIGHT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT),
#             pygame.K_UP: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP),
#             pygame.K_a: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A),
#             pygame.K_s: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B),
#             pygame.K_RETURN: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START),
#             pygame.K_5: PATH_FOLLOW_ACTION,
#         }
        
#         # Return the mapped action or None for unhandled keys
#         return ACTION_MAPPING.get(event.key)

#     def _reset_state(self):
#         """Reset navigator state"""
#         self.sequential_coordinates.clear()
#         self.coord_map_ids.clear()
#         self.current_coordinate_index = 0
#         self.quest_locked = False
#         self.navigation_status = "idle"
#         self.active_quest_id = None
#         self.movement_failure_count = 0
#         self._stuck_counter = 0

#     def reset_quest_state(self):
#         """Reset quest-related navigation state (called from QuestWarpBlocker)"""
#         print(f"ConsolidatedNavigator: Resetting quest state")
#         self._reset_state()

#     # Add update_after_step hook to sync coordinates after every action step
#     def update_after_step(self, obs, reward, terminated, truncated, info):
#         """Update navigator state after each step"""
#         try:
#             self.update_map_history()
            
#             # Check if we need to reset fallback search due to significant position/quest change
#             try:
#                 current_quest = self.get_current_quest()
#                 x, y, map_id = self.env.get_game_coords()
#                 current_global = local_to_global(y, x, map_id)
                
#                 # Reset fallback search if:
#                 # 1. Quest changed
#                 # 2. Map changed significantly 
#                 # 3. Position changed by more than snap distance
#                 reset_fallback = False
                
#                 if hasattr(self, '_last_fallback_quest') and self._last_fallback_quest != current_quest:
#                     reset_fallback = True
#                     print(f"ConsolidatedNavigator: Resetting fallback search - quest changed from {self._last_fallback_quest} to {current_quest}")
                
#                 if hasattr(self, '_last_fallback_map') and self._last_fallback_map != map_id:
#                     reset_fallback = True
#                     print(f"ConsolidatedNavigator: Resetting fallback search - map changed from {self._last_fallback_map} to {map_id}")
                
#                 if hasattr(self, '_last_fallback_pos'):
#                     if self._manhattan(self._last_fallback_pos, current_global) > self.max_snap_distance:
#                         reset_fallback = True
#                         print(f"ConsolidatedNavigator: Resetting fallback search - position changed significantly")
                
#                 if reset_fallback:
#                     self._fallback_searched = False
                
#                 # Update tracking variables
#                 self._last_fallback_quest = current_quest
#                 self._last_fallback_map = map_id
#                 self._last_fallback_pos = current_global
                
#             except Exception as e:
#                 print(f"ConsolidatedNavigator: Error in fallback reset logic: {e}")
                
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error in update_after_step: {e}")

#     def manual_warp_trigger(self) -> bool:
#         """Legacy compatibility wrapper.
#         This method used to exist in the old Navigator implementation and is still
#         referenced from play.py.  The new consolidated navigator already exposes
#         all warp-handling logic via ``warp_tile_handler`` so we simply forward
#         the call.  Any exceptions are caught and logged and ``False`` is
#         returned to indicate the warp did not execute.
#         """
#         try:
#             return self.warp_tile_handler()
#         except Exception as e:
#             print(f"ConsolidatedNavigator: manual_warp_trigger error: {e}")
#             return False

#     # ------------------------------------------------------------------
#     # Legacy path-following helper
#     # ------------------------------------------------------------------
#     def get_next_action(self) -> Optional[int]:
#         """Return the next movement action when following a quest path.

#         This is a lightweight wrapper around
#         ``convert_path_follow_to_movement_action`` which is responsible for
#         translating the special PATH_FOLLOW_ACTION token (value ``6``) into an
#         actual movement action (0-3, or occasionally 4/5 for A/B presses) based
#         on the pre-recorded human coordinate traces.

#         play.py expects this method to return either an **integer** action that
#         can be fed directly to the environment, or ``None`` if no movement is
#         required.  We therefore invoke the converter with the stored
#         ``_path_follow_action_value`` and only return the result if it produced
#         a *real* action.
#         """
#         # Only produce a move when the navigator is actively following a path
#         # (set to "navigating" by an explicit PATH_FOLLOW_ACTION trigger).
#         if self.navigation_status != "navigating":
#             return None

#         try:
#             action = self.convert_path_follow_to_movement_action(self._path_follow_action_value)
#             # If the converter could not determine a concrete move it will
#             # return the original PATH_FOLLOW_ACTION value – treat that as
#             # *no* action so that the caller can decide how to proceed.
#             if action is None or action == self._path_follow_action_value:
#                 return None
#             return action
#         except Exception as e:
#             print(f"ConsolidatedNavigator: get_next_action error: {e}")
#             return None

#     # =========================
#     # SIMPLE PUBLIC HELPERS EXPECTED BY TOOLS
#     # =========================
#     def move_in_direction(self, direction: str, steps: int = 1):
#         """Move the player a short distance in a specified cardinal direction.

#         This helper is a lightweight wrapper that existing Grok tools expect. It translates
#         human-friendly direction strings (e.g. "n", "up", "left") into the action indices
#         used by ConsolidatedNavigator and repeatedly invokes the internal _execute_movement
#         routine.  It returns a tuple (success: bool, message: str) mirroring the contract
#         used in grok_tool_implementations.navigate_to().
#         """
#         # ---- Canonicalise direction -------------------------------------------------
#         dir_map = {
#             "n": "up", "north": "up", "u": "up", "up": "up",
#             "s": "down", "south": "down", "d": "down", "down": "down",
#             "e": "right", "east": "right", "r": "right", "right": "right",
#             "w": "left", "west": "left", "l": "left", "left": "left",
#         }
#         canonical = dir_map.get(direction.lower()) if isinstance(direction, str) else None
#         if canonical is None or canonical not in self.ACTION_MAPPING_STR_TO_INT:
#             return False, f"Invalid direction: {direction}"

#         action_int = self.ACTION_MAPPING_STR_TO_INT[canonical]

#         moved_steps = 0
#         for _ in range(max(1, steps)):
#             if self._execute_movement(action_int):
#                 moved_steps += 1
#             else:
#                 # Stop immediately if movement blocked to avoid infinite loops
#                 return False, f"Movement blocked after {moved_steps} successful step(s) while moving {canonical}."
#         return True, f"Moved {moved_steps} step(s) {canonical}."

#     # =========================
#     # FALLBACK: NEAREST COMPLETED QUEST
#     # =========================
#     def _find_nearest_completed_quest_coord(self) -> Tuple[Optional[int], Optional[int]]:
#         """Search quest coordinate files and return (quest_id, coord_index) for the
#         best fallback quest path. IMPROVED ALGORITHM: Instead of just finding the
#         globally nearest coordinate, this prioritizes:
        
#         1. Quest progression order (prefer quests closer to target quest number)
#         2. Directional progress toward target quest location
#         3. Distance as final tiebreaker
        
#         This prevents falling back to very early quests (like quest 8) when the 
#         target quest (like quest 25) is much further in the progression.
#         """
#         try:
#             cur_pos = self._get_player_global_coords()
#             if cur_pos is None:
#                 return None, None

#             # Get the current target quest from quest manager
#             target_quest = None
#             if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
#                 target_quest = self.env.quest_manager.get_current_quest()
            
#             print(f"ConsolidatedNavigator: Fallback search - target quest: {target_quest}, current pos: {cur_pos}")

#             best_q: Optional[int] = None
#             best_idx: Optional[int] = None
#             best_score: Optional[float] = None

#             quest_paths_dir = Path(__file__).parent / "quest_paths"
            
#             # First, get target quest location for directional scoring
#             target_quest_center = None
#             if target_quest:
#                 target_file = quest_paths_dir / f"{target_quest:03d}" / f"{target_quest:03d}_coords.json"
#                 if target_file.exists():
#                     try:
#                         target_data = json.load(target_file.open())
#                         target_coords = []
#                         for seg in target_data.values():
#                             target_coords.extend([(c[0], c[1]) for c in seg])
#                         if target_coords:
#                             # Calculate center of target quest path
#                             avg_y = sum(c[0] for c in target_coords) / len(target_coords)
#                             avg_x = sum(c[1] for c in target_coords) / len(target_coords)
#                             target_quest_center = (avg_y, avg_x)
#                             print(f"ConsolidatedNavigator: Target quest {target_quest} center: {target_quest_center}")
#                     except Exception as e:
#                         print(f"ConsolidatedNavigator: Error loading target quest {target_quest}: {e}")

#             for quest_dir in quest_paths_dir.iterdir():
#                 if not quest_dir.is_dir():
#                     continue
#                 try:
#                     qid = int(quest_dir.name)
#                 except ValueError:
#                     continue

#                 # Skip if this is the target quest itself (we're looking for fallbacks)
#                 if qid == target_quest:
#                     continue

#                 qfile = quest_dir / f"{quest_dir.name}_coords.json"
#                 if not qfile.exists():
#                     continue
#                 try:
#                     data = json.load(qfile.open())
#                 except Exception:
#                     continue

#                 coords_flat: List[Tuple[int,int]] = []
#                 for seg in data.values():
#                     coords_flat.extend([(c[0], c[1]) for c in seg])

#                 for idx, c in enumerate(coords_flat):
#                     # Skip if coordinate is exactly where we stand (prevents oscillation)
#                     if c == cur_pos:
#                         continue
                    
#                     distance = self._manhattan(cur_pos, c)
                    
#                     # Only consider coordinates within reasonable snap range
#                     if distance > self.max_snap_distance:
#                         continue
                    
#                     # Calculate composite score (lower is better)
#                     score = self._calculate_fallback_score(
#                         qid, c, distance, target_quest, target_quest_center, cur_pos
#                     )
                    
#                     if best_score is None or score < best_score:
#                         best_score = score
#                         best_q = qid
#                         best_idx = idx
#                         print(f"ConsolidatedNavigator: New best fallback - quest {qid} at distance {distance}, score {score:.2f}")

#             if best_q is not None and best_score is not None:
#                 print(f"ConsolidatedNavigator: Selected fallback quest {best_q} with score {best_score:.2f} - will proceed forward until quest {target_quest} becomes reachable")
#                 return best_q, best_idx
            
#             print(f"ConsolidatedNavigator: No suitable fallback quest found within snap range")
#             return None, None
            
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error in _find_nearest_completed_quest_coord: {e}")
#             return None, None

#     def _calculate_fallback_score(self, quest_id: int, coord: Tuple[int, int], distance: int, 
#                                 target_quest: Optional[int], target_quest_center: Optional[Tuple[int, int]], 
#                                 current_pos: Tuple[int, int]) -> float:
#         """Calculate a composite score for fallback quest selection.
#         Lower scores are better. Considers:
#         1. Quest progression gap (prefer quests closer to target quest number)
#         2. Directional progress (prefer quests that move toward target quest location)  
#         3. Distance (prefer closer coordinates as tiebreaker)
#         """
        
#         # Base score from distance (normalized to 0-100 range)
#         distance_score = min(distance / self.max_snap_distance * 100, 100)
        
#         # Quest progression penalty (heavily penalize going backwards in quest order)
#         progression_penalty = 0
#         if target_quest is not None:
#             quest_gap = abs(target_quest - quest_id)
            
#             # Heavily penalize quests that are far behind the target quest
#             if quest_id < target_quest:
#                 backwards_gap = target_quest - quest_id
#                 # Exponential penalty for going backwards - quest 8 vs quest 25 = massive penalty
#                 progression_penalty = min(backwards_gap ** 1.5 * 10, 1000)
#             else:
#                 # Mild penalty for quests ahead of target (shouldn't normally happen)
#                 progression_penalty = quest_gap * 5
        
#         # Directional progress bonus/penalty
#         directional_penalty = 0
#         if target_quest_center is not None:
#             # Calculate if this coordinate moves us toward or away from target quest center
#             current_to_target_dist = self._manhattan(current_pos, target_quest_center)
#             coord_to_target_dist = self._manhattan(coord, target_quest_center)
            
#             # Penalty if this coordinate is further from target than our current position
#             if coord_to_target_dist > current_to_target_dist:
#                 directional_penalty = (coord_to_target_dist - current_to_target_dist) * 2
#             else:
#                 # Small bonus for coordinates that move us closer to target
#                 directional_penalty = -(current_to_target_dist - coord_to_target_dist) * 0.5
        
#         total_score = distance_score + progression_penalty + directional_penalty
        
#         # Debug logging for score breakdown
#         print(f"ConsolidatedNavigator: Score breakdown for quest {quest_id}: "
#               f"distance={distance_score:.1f}, progression_penalty={progression_penalty:.1f}, "
#               f"directional_penalty={directional_penalty:.1f}, total={total_score:.1f}")
        
#         return total_score

#     # =========================
#     # NEW: NEAREST PATH NODE ATTACHMENT
#     # =========================
#     def _find_nearest_path_node_on_current_map(self) -> Tuple[Optional[int], Optional[int]]:
#         """Return (quest_id, coord_index) of the closest recorded coordinate **on the
#         player\'s current map**, irrespective of completion status. Returns (None,
#         None) if none are within ``max_snap_distance``."""
#         # 🚧  Guard: if we are already on an *in-progress* quest, do NOT look for
#         #       alternate quests on the same map – this avoids the ping-pong
#         #       where the navigator keeps snapping back to a *completed* quest
#         #       that happens to share the final coordinate with the current
#         #       quest (e.g. quest 44 ➔ 45 hand-off at (165,174)).
#         if self.active_quest_id is not None and not self._is_quest_completed(self.active_quest_id):
#             return None, None
        
#         # Initialise nearest-path search variables so best_dist is defined before the loops
#         best_q: Optional[int] = None
#         best_idx: Optional[int] = None
#         best_dist: Optional[int] = None
 
#         try:
#             cur_pos = self._get_player_global_coords()
#             if cur_pos is None:
#                 return None, None
#             _, _, cur_map = self.env.get_game_coords()

#             quest_root = Path(__file__).parent / "quest_paths"
#             for quest_dir in quest_root.iterdir():
#                 if not quest_dir.is_dir():
#                     continue
#                 try:
#                     qid = int(quest_dir.name)
#                 except ValueError:
#                     continue

#                 qfile = quest_dir / f"{quest_dir.name}_coords.json"
#                 if not qfile.exists():
#                     continue
#                 try:
#                     data = json.load(qfile.open())
#                 except Exception:
#                     continue

#                 flat_idx = 0
#                 for seg_key, seg_coords in data.items():
#                     try:
#                         # Handle both key formats: plain map IDs ("68") and underscore-separated ("68_0")
#                         if '_' in seg_key:
#                             seg_map_id = int(seg_key.split('_')[0])
#                         else:
#                             seg_map_id = int(seg_key)
#                     except Exception:
#                         seg_map_id = None
#                     for coord in seg_coords:
#                         if seg_map_id == cur_map:
#                             dist = self._manhattan(cur_pos, (coord[0], coord[1]))
#                             if best_dist is None or dist < best_dist:
#                                 best_dist = dist
#                                 best_q = qid
#                                 best_idx = flat_idx
#                         flat_idx += 1

#             if best_dist is not None and best_dist <= self.max_snap_distance:
#                 return best_q, best_idx
#             return None, None
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error in _find_nearest_path_node_on_current_map: {e}")
#             return None, None

#     def _attach_to_nearest_path_node(self) -> bool:
#         """Attach the navigator to the closest recorded path node on the current
#         map and enqueue an A* recovery path to reach it. Returns True if a path
#         was successfully attached."""
#         try:
#             qid, idx = self._find_nearest_path_node_on_current_map()
#             if qid is None:
#                 return False
#             if not self.load_coordinate_path(qid):
#                 return False
#             # After loading, snap to nearest again to update index safely
#             self.snap_to_nearest_coordinate()
#             # Ensure current_coordinate_index is valid
#             if 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
#                 target_coord = self.sequential_coordinates[self.current_coordinate_index]
#                 gy, gx = self._get_player_global_coords()
#                 if gy is None:
#                     return False
#                 dy = target_coord[0] - gy
#                 dx = target_coord[1] - gx
#                 target_row = 4 + dy
#                 target_col = 4 + dx
#                 if 0 <= target_row < 9 and 0 <= target_col < 10:
#                     try:
#                         msg, dirs = self.env.find_path(int(target_row), int(target_col))
#                         if dirs:
#                             print(f"ConsolidatedNavigator: Recovery path to nearest node – {msg}")
#                             self._recovery_steps = dirs.copy()
#                     except Exception as e:
#                         print(f"ConsolidatedNavigator: A* path generation failed: {e}")
#                 return True
#             return False
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error in _attach_to_nearest_path_node: {e}")
#             return False

#     def is_on_path_for_quest(self, quest_id: int) -> bool:
#         """Check if player is currently on the path for the specified quest"""
#         if quest_id != self.active_quest_id:
#             return False
            
#         if not self.sequential_coordinates:
#             return False
            
#         try:
#             # Get current player position
#             x, y, map_id = self.env.get_game_coords()
#             global_y, global_x = local_to_global(y, x, map_id)
#             current_global = (global_y, global_x)
            
#             # Check if current position matches any coordinate in the quest path
#             return current_global in self.sequential_coordinates
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Error checking if on path for quest {quest_id}: {e}")
#             return False

#     # ============================================================
#     # STUCK RECOVERY ROUTINE
#     # ============================================================
#     def _recover_from_stuck(self) -> bool:
#         """Attempt to recover when the avatar is stuck bumping into a wall.

#         The strategy is conservative – we try the least disruptive options
#         first and only escalate if the simple fixes fail.
#         Returns True if a recovery action was taken (caller should skip the
#         current frame) or False to continue as-is.
#         """
#         print("ConsolidatedNavigator: ⚠️  Detected navigation stall – initiating recovery")

#         # 1️⃣  Try attaching to the nearest path node on the current map.
#         try:
#             if self._attach_to_nearest_path_node():
#                 self._stuck_counter = 0
#                 print("ConsolidatedNavigator: Re-attached to nearest path node on current map")
#                 return True
#         except Exception as e:
#             print(f"ConsolidatedNavigator: Recovery – nearest-node attach failed: {e}")

#         # 2️⃣  As a fallback unlock the quest so the existing fallback/snapping
#         #     logic can choose an alternative path on the next frame.
#         self.quest_locked = False
#         self._stuck_counter = 0
#         print("ConsolidatedNavigator: Recovery – unlocked quest to allow fallback re-planning")
#         # Force a snap search so we do not wait a whole frame.
#         try:
#             self.snap_to_nearest_coordinate()
#         except Exception:
#             pass
#         # Let caller know we modified internal state.
#         return True

# # =========================
# # LEGACY COMPATIBILITY WRAPPER
# # =========================

# # Provide backward compatibility
# InteractiveNavigator = ConsolidatedNavigator

# # =========================
# # EXTERNAL INTERFACE FUNCTIONS
# # =========================

# def record_warp_step(env, navigator):
#     """Legacy interface for warp step recording"""
#     if hasattr(navigator, 'record_warp_step'):
#         navigator.record_warp_step()

# def backtrack_warp_sequence(env, navigator):
#     """Legacy interface for warp backtracking"""
#     if hasattr(navigator, 'execute_backtrack'):
#         return navigator.execute_backtrack()
#     return False







