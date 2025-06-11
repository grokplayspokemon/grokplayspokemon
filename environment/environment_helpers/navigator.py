# navigator.py - CONSOLIDATED NAVIGATION SYSTEM
# ALL navigation logic consolidated here - no navigation code should exist elsewhere

from __future__ import annotations

import json
import time
import random
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING, Dict, Any
from collections import defaultdict, deque

from pyboy.utils import WindowEvent
            
from environment.data.environment_data.constants import WARP_DICT
from environment.data.environment_data.map import MapIds
from environment.data.environment_data.items import Items
from environment.data.environment_data.events import EventFlags
from environment.data.environment_data.flags import Flags
from environment.data.recorder_data.global_map import local_to_global, global_to_local

if TYPE_CHECKING:
    from environment import RedGymEnv

# Import logging system
import sys
import os
sys.path.append('/puffertank/grok_plays_pokemon')
from utils.logging_config import get_pokemon_logger

class ConsolidatedNavigator:
    """
    CONSOLIDATED NAVIGATION SYSTEM - All navigation logic is now in this single class.
    
    This class handles:
    - PATH_FOLLOW_ACTION conversion (moved from StageManager)
    - Quest path following (moved from QuestPathFollower)  
    - Warp detection and handling (moved from WarpTracker)
    - Map transition tracking (moved from TriggerEvaluator)
    - Quest-based navigation decisions (moved from QuestManager)
    - Coordinate management and validation
    - Movement execution and collision detection
    - Direction and pathfinding logic
    """
    
    def __init__(self, env_instance: RedGymEnv):
        self.env: RedGymEnv = env_instance
        self.pyboy = self.env.pyboy
        
        # Initialize logger
        self.logger = get_pokemon_logger()

        # =========================
        # CORE NAVIGATION STATE
        # =========================
        self.sequential_coordinates: List[Tuple[int, int]] = []
        self.coord_map_ids: List[int] = []
        self.current_coordinate_index: int = 0
        self.active_quest_id: Optional[int] = None
        self._last_loaded_quest_id: Optional[int] = None
        self.using_placeholder_map_ids: bool = True
        
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

        # =========================
        # CONSOLIDATED WARP SYSTEM (from WarpTracker + Navigator)
        # =========================
        self.door_warp = False
        self.last_warp_time = 0.0
        self.WARP_COOLDOWN_SECONDS = 0.5
        self.last_warp_origin_map: Optional[int] = None
        self._post_warp_exit_pos: Optional[Tuple[int, int]] = None
        self._left_home = False
        self._last_map_id = None
        
        # Enhanced warp blocking state
        self._blocked_warps: dict[tuple[int, int], float] = {}  # (from_map, to_map) -> expiry_time
        self.WARP_BLOCK_DURATION = 2.0  # Seconds to block reverse warps
        
        # House stair tracking to prevent infinite loops
        self._house_stair_warp_count = 0
        self._max_house_stair_warps = 1
        self._house_stair_timer = 0.0
        self._house_stair_cooldown = 10.0
        self._last_stair_direction = None
        self._recent_maps = []  # Track recently visited maps
        self._max_recent_maps = 3
        self._map_cooldown_period = 3.0
        self._map_visit_times = {}
        self._prevent_immediate_return = True
        self._last_warp_origin = None
        self._last_warp_target = None
        self._warp_delay_timer = 0.0
        self._post_warp_delay = 1.0
        
        # Warp step tracking (from WarpTracker)
        self.warp_steps = deque(maxlen=20)  # Store last 20 positions
        self.last_warp_step_map = None
        
        # =========================
        # MAP TRANSITION TRACKING (from TriggerEvaluator)
        # =========================
        self.map_history = deque(maxlen=10)  # Consolidated map tracking
        self._trigger_cooldowns = {}
        self._trigger_cooldown_duration = 2.0
        self._max_trigger_count = 2
        
        # =========================
        # QUEST SYSTEM (from QuestManager + QuestPathFollower)
        # =========================
        self.quest_definitions: List[Dict[str, Any]] = []
        self.quests_by_location: Dict[int, List[int]] = {}
        self.quest_completed_status: Dict[str, bool] = {}
        self.current_quest_id: Optional[int] = None
        
        # Quest path following (from QuestPathFollower)
        self.quest_paths: Dict[int, List[Tuple[int, int]]] = {}
        self.current_quest_path: List[Tuple[int, int]] = []
        self.current_path_index: int = 0
        self.path_following_active: bool = False
        
        # Quest action management
        self.pending_b_presses = 0
        self.pressed_button_dict: Dict[int, Dict[Tuple[int, int], Dict[int, int]]] = {}
        
        # =========================
        # PATH_FOLLOW_ACTION CONVERSION (from StageManager)
        # =========================
        # This is now the ONLY place PATH_FOLLOW_ACTION is converted
        self._path_follow_action_value = 6  # The discrete action index
        
        # =========================
        # ACTION AND MOVEMENT MAPPINGS
        # =========================
        self.ACTION_MAPPING_STR_TO_INT = {
            "down": 0,
            "left": 1,
            "right": 2,
            "up": 3,
        }
        
        # Quest action mapping
        self.quest_action_mapping = {
            'down': 0,
            'left': 1, 
            'right': 2,
            'up': 3,
            'a': 4,
            'b': 5,
            'path': 6,
            'start': 7
        }

        # Path-recovery queue generated by A* (env.find_path).  When populated we
        # consume one direction per frame before falling back to the normal
        # path-following heuristics.
        self._recovery_steps: List[str] = []

        # =========================
        # INITIALIZE SUBSYSTEMS
        # =========================
        self._load_quest_system()
        self._initialize_map_tracking()

    # =========================
    # QUEST SYSTEM INTEGRATION
    # =========================
    
    def _load_quest_system(self):
        """Load quest definitions and initialize quest tracking"""
        try:
            required_completions_path = Path(__file__).parent / "required_completions.json"
            if not required_completions_path.exists():
                print(f"ConsolidatedNavigator: Quest definitions file not found: {required_completions_path}")
                return
                
            with required_completions_path.open('r') as f:
                loaded_data = json.load(f)
            
            if not isinstance(loaded_data, list):
                print(f"ConsolidatedNavigator: Invalid quest definitions format")
                return
                
            self.quest_definitions = loaded_data
            
            # Build location mapping
            quests_by_location: Dict[int, List[int]] = defaultdict(list)
            for q_def in self.quest_definitions:
                try:
                    loc = int(q_def["location_id"])
                    quest_id_raw = q_def["quest_id"]
                    if isinstance(quest_id_raw, str):
                        quest_id_int = int(quest_id_raw.lstrip('0') or '0')
                    else:
                        quest_id_int = int(quest_id_raw)
                    quests_by_location[loc].append(quest_id_int)
                except (ValueError, TypeError, KeyError) as e:
                    continue
                    
            for loc, qlist in quests_by_location.items():
                qlist.sort()
            self.quests_by_location = dict(quests_by_location)
            
            print(f"ConsolidatedNavigator: Loaded {len(self.quest_definitions)} quest definitions")
            
        except Exception as e:
            print(f"ConsolidatedNavigator: Failed to load quest system: {e}")
            self.quest_definitions = []
            self.quests_by_location = {}

    def _load_quest_paths(self):
        """Load quest coordinate paths from combined file"""
        coords_file = Path(__file__).parent / "quest_paths" / "combined_quest_coordinates_continuous.json"
        
        if not coords_file.exists():
            print(f"ConsolidatedNavigator: Combined coordinate file not found: {coords_file}")
            return
            
        try:
            with open(coords_file, 'r') as f:
                data = json.load(f)
            
            quest_start_indices = data.get("quest_start_indices", {})
            all_coordinates = data.get("coordinates", [])
            
            # Extract coordinates for each quest
            for quest_id_str, start_idx in quest_start_indices.items():
                quest_id = int(quest_id_str)
                
                # Find end index
                end_idx = len(all_coordinates)
                sorted_quest_ids = sorted([int(k) for k in quest_start_indices.keys()])
                
                current_quest_idx = sorted_quest_ids.index(quest_id)
                if current_quest_idx + 1 < len(sorted_quest_ids):
                    next_quest_id = sorted_quest_ids[current_quest_idx + 1]
                    end_idx = quest_start_indices[str(next_quest_id)]
                
                quest_coords = all_coordinates[start_idx:end_idx]
                self.quest_paths[quest_id] = [(coord[0], coord[1]) for coord in quest_coords]
            
            print(f"ConsolidatedNavigator: Loaded {len(self.quest_paths)} quest paths")
            
        except Exception as e:
            print(f"ConsolidatedNavigator: Error loading quest paths: {e}")
            self.quest_paths = {}

    def get_current_quest(self) -> Optional[int]:
        """
        Get the current active quest from the quest manager.
        FIXED: Always delegate to quest manager instead of independent determination.
        """
        # CRITICAL FIX: Always use quest manager's current quest
        # The navigator should NOT determine quests independently
        if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
            current_quest = self.env.quest_manager.get_current_quest()
            if current_quest is not None:
                return current_quest
        # Get current location for quest filtering
        try:
            curr_map = self.env.get_game_coords()[2]
        except:
            return None
            
        # Get quests available at current location
        location_quests = self.quests_by_location.get(curr_map, [])
        if not location_quests:
            # Try to find any incomplete quest
            for q_def in self.quest_definitions:
                quest_id = int(q_def["quest_id"])
                if not self._is_quest_completed(quest_id):
                    return quest_id
            return None
        
        # Find first incomplete quest at this location
        for quest_id in location_quests:
            if not self._is_quest_completed(quest_id):
                return quest_id
                
        return None

    def _is_quest_completed(self, quest_id: int) -> bool:
        """Check if a quest is completed"""
        quest_key = str(quest_id).zfill(3)
        return self.quest_completed_status.get(quest_key, False)

    # =========================
    # MAP TRANSITION TRACKING
    # =========================
    
    def _initialize_map_tracking(self):
        """Initialize map tracking system"""
        try:
            current_map = self.env.get_game_coords()[2]
            self.map_history.append(current_map)
        except:
            pass

    def update_map_history(self):
        """Update map history when map changes"""
        try:
            current_map = self.env.get_game_coords()[2]
            if not self.map_history or self.map_history[-1] != current_map:
                self.map_history.append(current_map)
                if self.logger:
                    self.logger.log_navigation_event("MAP_TRANSITION", {
                        'message': f'Map changed to {current_map}',
                        'previous_map': self.map_history[-2] if len(self.map_history) >= 2 else None,
                        'current_map': current_map,
                        'map_history': list(self.map_history)
                    })
        except:
            pass

    def _get_map_history(self) -> Tuple[Optional[int], int]:
        """Get previous and current map IDs"""
        try:
            current_map_id = self.env.get_game_coords()[2]
            if len(self.map_history) >= 2:
                previous_map_id = self.map_history[-2]
            else:
                previous_map_id = current_map_id
            return previous_map_id, current_map_id
        except:
            return None, 0

    # =========================
    # WARP TRACKING SYSTEM
    # =========================
    
    def record_warp_step(self):
        """Record current position for warp tracking"""
        try:
            global_pos = self._get_player_global_coords()
            current_map = self.env.get_game_coords()[2]
            
            if global_pos:
                self.warp_steps.append({
                    'position': global_pos,
                    'map_id': current_map,
                    'timestamp': time.time()
                })
                
            # Track map changes for warp detection
            if self.last_warp_step_map != current_map:
                self.last_warp_step_map = current_map
                self.update_map_history()
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Error recording warp step: {e}")

    def can_backtrack_warp(self) -> bool:
        """Check if we can backtrack from current position"""
        if len(self.warp_steps) < 2:
            return False
            
        current_pos = self._get_player_global_coords()
        if not current_pos:
            return False
            
        # Look for a previous position that's different from current
        for step in reversed(list(self.warp_steps)[:-1]):  # Exclude current position
            if step['position'] != current_pos:
                return True
                
        return False

    def execute_backtrack(self) -> bool:
        """Execute backtrack to previous position"""
        if not self.can_backtrack_warp():
            return False
            
        current_pos = self._get_player_global_coords()
        
        # Find the most recent different position
        for step in reversed(list(self.warp_steps)[:-1]):
            if step['position'] != current_pos:
                target_pos = step['position']
                target_map = step['map_id']
                
                print(f"ConsolidatedNavigator: Backtracking to {target_pos} on map {target_map}")
                
                # Implement backtrack logic - for now, just update target
                if len(self.sequential_coordinates) > 0:
                    # Find closest coordinate to backtrack target
                    distances = [self._manhattan(target_pos, coord) for coord in self.sequential_coordinates]
                    closest_idx = distances.index(min(distances))
                    self.current_coordinate_index = closest_idx
                    
                return True
                
        return False

    # =========================
    # PATH_FOLLOW_ACTION CONVERSION (CONSOLIDATED)
    # =========================
    def convert_path_follow_to_movement_action(self, original_action: int) -> int:
        """
        AGGRESSIVE PATH FOLLOWING - SIMPLIFIED VERSION

        This human path was recorded by a player who completed the game!
        It MUST be followable! Remove all safety checks and just follow the damn coordinates!
        """
        if original_action != self._path_follow_action_value:
            return original_action

        # -------------------------------------------------------------
        # 1.  QUICK EXIT – If we previously generated an A* recovery
        #     path (stored in ``_recovery_steps``) then simply pop the
        #     next move from that queue until it is empty.  This lets
        #     the navigator walk itself back onto the recorded quest
        #     path before resuming normal node-to-node following.
        # -------------------------------------------------------------
        if self._recovery_steps:
            next_dir = self._recovery_steps.pop(0)
            # Convert textual direction to action integer (defaults to 3/up)
            return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)

        # — Pre-conversion sync & debug
        try:
            x, y, map_id = self.env.get_game_coords()
            current_global = local_to_global(y, x, map_id)
            current_quest = self.get_current_quest()
            if not self.sequential_coordinates or self.active_quest_id != current_quest:
                self.load_coordinate_path(current_quest)

            # Debug: show current and upcoming target nodes
            node_idx = self.current_coordinate_index
            on_node = (node_idx < len(self.sequential_coordinates)
                       and current_global == self.sequential_coordinates[node_idx])
            curr_node = self.sequential_coordinates[node_idx] if node_idx < len(self.sequential_coordinates) else None
            curr_map = self.coord_map_ids[node_idx] if node_idx < len(self.coord_map_ids) else None
            next_idx = node_idx + 1
            next_node = self.sequential_coordinates[next_idx] if next_idx < len(self.sequential_coordinates) else None
            next_map = self.coord_map_ids[next_idx] if next_idx < len(self.coord_map_ids) else None
            print(f"next_map: {next_map}, map_id: {map_id}")
            warp_needed = (next_map is not None and next_map != map_id)
            print(f"\n1️⃣ quest_id={current_quest},"
                  f"\n coords_loaded_count={len(self.sequential_coordinates)},"
                  f"\n current_map={map_id}, local=({x},{y}), global={current_global},"
                  f"\n on_node={on_node}, node_idx={node_idx},"  
                  f"\n curr_node={curr_node}@{curr_map},"  
                  f"\n 5️⃣ next_node={next_node}@{next_map}, warp_needed={warp_needed}\n")
            try:
                quest_dir = f"{current_quest:03d}"
                with (Path(__file__).parent / "quest_paths" / quest_dir / f"{quest_dir}_coords.json").open() as f:
                    data = json.load(f)
                verify_msgs = []
                file_coords, file_maps = [], []
                for seg_key, seg in data.items():
                    mid = int(seg_key.split('_')[0])
                    for coord in seg:
                        file_coords.append((coord[0], coord[1]))
                        file_maps.append(mid)
                for idx, ((lc, lm), (fc, fm)) in enumerate(zip(zip(self.sequential_coordinates, self.coord_map_ids),
                                                            zip(file_coords, file_maps))):
                    mark = '' if (lc == fc and lm == fm) else ' ❌'
                    verify_msgs.append(f"{idx}: loaded={lc}@{lm}, file={fc}@{fm}{mark}")
                print("coord_verify:", "; ".join(verify_msgs))
            except Exception as e:
                print(f"Navigator coords verification error: {e}")
        except Exception as e:
            print(f"Navigator logging error: {e}")

        # — Snap to nearest before movement logic
        try:
            old_idx = self.current_coordinate_index
            self.snap_to_nearest_coordinate()
            print(f"ConsolidatedNavigator: pre-conversion snap from index {old_idx} to {self.current_coordinate_index}")
        except Exception as e:
            print(f"ConsolidatedNavigator: pre-conversion snap error: {e}")

        # — Main movement / warp logic
        try:
            x, y, map_id = self.env.get_game_coords()
            current_global = local_to_global(y, x, map_id)

            current_quest = self.get_current_quest()
            if not current_quest:
                return 3
            if not self.sequential_coordinates or self.active_quest_id != current_quest:
                if not self.load_coordinate_path(current_quest) or not self.sequential_coordinates:
                    return 3

            # validate index bounds
            if self.current_coordinate_index < 0:
                self.current_coordinate_index = 0
            elif self.current_coordinate_index >= len(self.sequential_coordinates):
                return original_action

            # — CRITICAL FIX: bump index if we're already on the warp-tile coordinate —
            target_coord = self.sequential_coordinates[self.current_coordinate_index]
            print(f"target_coord: {target_coord}")
            print(f'current_global: {current_global}')
            target_map   = self.coord_map_ids[self.current_coordinate_index + 1]
            if current_global == target_coord:
                self.current_coordinate_index += 1
                if self.current_coordinate_index >= len(self.sequential_coordinates):
                    return original_action
                target_coord = self.sequential_coordinates[self.current_coordinate_index]
                target_map   = self.coord_map_ids[self.current_coordinate_index]

            # 🚀 Debug current target after bump
            print(f"🚀 active_target_idx={self.current_coordinate_index + 1}, "
                f"active_target={target_coord}, active_map={target_map}")

            # Ensure horizontal (dx) and vertical (dy) deltas are always defined so the
            # direction-selection logic below can run even when we stay on the same map.
            # In global coordinate space:  +dy ⇒ move DOWN, +dx ⇒ move RIGHT.
            dy = target_coord[0] - current_global[0]
            dx = target_coord[1] - current_global[1]

            # ---------------------------------------------------------
            # 2.  RECOVERY LOGIC – If we are on the correct map but the
            #     target node is off-screen (|dx| or |dy| > 4) OR we
            #     snapped failed earlier, attempt to generate a local
            #     A* route using the environment's ``find_path`` helper.
            #     This works in the 9×10 down-sampled grid where the
            #     player is fixed at (4,4).
            # ---------------------------------------------------------
            if not self._recovery_steps and target_map == map_id:
                # Only attempt if target is within roughly a screen so
                # find_path can see it.
                if abs(dx) <= 8 and abs(dy) <= 8:
                    target_row = 4 + dy
                    target_col = 4 + dx
                    # Sanity clamp to grid limits 0-8 / 0-9
                    target_row = max(0, min(8, target_row))
                    target_col = max(0, min(9, target_col))
                    try:
                        status_msg, path_dirs = self.env.find_path(int(target_row), int(target_col))
                        if path_dirs:
                            print(f"Navigator recovery: {status_msg} → {path_dirs}")
                            # Store and immediately use first move next frame
                            self._recovery_steps = path_dirs.copy()
                            next_dir = self._recovery_steps.pop(0)
                            return self.ACTION_MAPPING_STR_TO_INT.get(next_dir, 3)
                    except Exception as e:
                        print(f"Navigator recovery error: {e}")

            # — WARP HANDLING: handle explicit map transitions via warp tiles —
            if target_map != map_id:
                local_pos = (x, y)
                # only consider warp entries that lead to the desired map
                warps_full = WARP_DICT.get(MapIds(map_id).name, [])
                # Accept warps that go directly to the desired map or that
                # use the special 255 "LAST_MAP" sentinel (meaning they lead
                # back to whatever map we came from – which, for a recorded
                # human path, will be the desired one).
                warps = []
                for e in warps_full:
                    tmid = e.get('target_map_id')
                    if tmid == target_map or tmid == 255:
                        warps.append(e)
                if warps:
                    # find nearest warp tile
                    best = None
                    best_dist = None
                    for entry in warps:
                        wx, wy = entry.get('x'), entry.get('y')
                        if wx is None or wy is None:
                            continue
                        d = abs(wx - local_pos[0]) + abs(wy - local_pos[1])
                        if best_dist is None or d < best_dist:
                            best_dist, best = d, (wx, wy)
                    if best is not None:
                        # door warp: step onto warp tile, then press DOWN to warp
                        if local_pos == best:
                            return 0
                        # otherwise, walk toward the warp tile
                        dx, dy = best[0] - x, best[1] - y
                        # Prefer vertical motion first when approaching a warp tile.  For
                        # indoor exits the critical step is usually to **step down onto the
                        # bottom-row door tile**; choosing horizontal first can nudge the
                        # avatar in front of an NPC and block the warp indefinitely.
                        if dy != 0:
                            return 0 if dy > 0 else 3  # DOWN / UP
                        elif dx != 0:
                            return 2 if dx > 0 else 1  # RIGHT / LEFT
                        else:
                            return 0  # default DOWN (shouldn't occur)
                # no explicit warp entries for this transition: fall through to normal movement

            # Evaluate both axes, prioritising the one with greater distance but
            # *only if* that direction is considered walkable; otherwise swap.
            primary_first = 'horiz' if abs(dx) >= abs(dy) else 'vert'

            # Determine preferred directions (primary then secondary)
            horiz_dir = 2 if dx > 0 else 1 if dx < 0 else None  # RIGHT or LEFT
            vert_dir  = 0 if dy > 0 else 3 if dy < 0 else None  # DOWN  or UP

            # -----------------------------------------------------------------
            # 3.  COLLISION-AWARE CHOICE – consult the environment's live
            #     collision grid so we don't keep bonking into fences / signs.
            # -----------------------------------------------------------------

            try:
                valid_moves = set(self.env.get_valid_moves())  # strings: up/down/left/right
            except Exception:
                valid_moves = {"up", "down", "left", "right"}

            def dir_walkable(d):
                if d is None:
                    return False
                str_dir = {0: "down", 1: "left", 2: "right", 3: "up"}.get(d)
                return str_dir in valid_moves

            order = [primary_first, 'vert' if primary_first=='horiz' else 'horiz']
            for axis in order:
                chosen = horiz_dir if axis=='horiz' else vert_dir
                if chosen is not None and dir_walkable(chosen):
                    return chosen

            # If neither preferred direction is walkable fall back to any that is
            for cand in [0,1,2,3]:
                if dir_walkable(cand):
                    return cand

            # If we get here, assume blocked; return original action to keep game ticking
            return original_action

        except Exception:
            return 3

    def _get_noop_action(self) -> int:
        """Get a no-op action value"""
        return 3  # UP as safe fallback

    # =========================
    # COORDINATE AND MOVEMENT UTILITIES
    # =========================
    
    def _get_player_global_coords(self) -> Optional[Tuple[int, int]]:
        """Get player's current global coordinates"""
        if not hasattr(self.env, "get_game_coords"):
            print("ConsolidatedNavigator: env lacks get_game_coords()")
            return None
        try:
            lx, ly, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(ly, lx, map_id)
            
            if lx < 0 or ly < 0 or map_id < 0:
                print(f"ConsolidatedNavigator: Invalid coordinates: local=({lx},{ly}), map={map_id}")
                return None
                
            return gy, gx
        except Exception as e:
            print(f"ConsolidatedNavigator: Error reading coords: {e}")
            return None

    @staticmethod
    def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
        """Calculate Manhattan distance between two points"""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _execute_movement(self, action: int, bypass_collision: bool = False) -> bool:
        """Execute a movement action with collision detection"""
        print(f"ConsolidatedNavigator: Executing movement action {action}")
        
        # Safety checks
        if self.env.read_m("wIsInBattle") != 0:
            print("ConsolidatedNavigator: Movement blocked - in battle")
            return False
            
        dialog = self.env.read_dialog()
        if dialog and dialog.strip():
            print(f"ConsolidatedNavigator: Movement blocked - dialog active")
            return False
        
        # Collision check (unless bypassing for warp activation)
        if not bypass_collision:
            direction_map = {0: "down", 1: "left", 2: "right", 3: "up"}
            direction = direction_map.get(action)
            
            if direction and not self._is_direction_walkable(direction):
                print(f"ConsolidatedNavigator: Movement blocked - direction {direction} not walkable")
                return False
        
        # Store position before movement
        pos_before = self.env.get_game_coords()
        
        # Execute the movement
        obs, reward, done, truncated, info = self.env.process_action(action, source="ConsolidatedNavigator")
        
        # Check if position changed
        pos_after = self.env.get_game_coords()
        moved = pos_before != pos_after
        
        print(f"ConsolidatedNavigator: Movement result - Before: {pos_before}, After: {pos_after}, Moved: {moved}")
        
        # Track movement failures
        if not moved:
            self.movement_failure_count += 1
        else:
            self.movement_failure_count = 0
            self.record_warp_step()  # Record successful movement
        
        return moved

    def _is_direction_walkable(self, direction: str) -> bool:
        """Check if a direction is walkable using collision detection"""
        try:
            # Get collision map
            collision_grid = self._get_collision_map_grid()
            if not collision_grid:
                return True  # Default to walkable if can't get collision data
                
            # Get player center in collision grid
            player_row, player_col = self._get_player_collision_position()
            
            # Calculate target position
            direction_deltas = {
                "up": (-1, 0),
                "down": (1, 0),
                "left": (0, -1),
                "right": (0, 1)
            }
            
            delta_row, delta_col = direction_deltas.get(direction, (0, 0))
            target_row = player_row + delta_row
            target_col = player_col + delta_col
            
            # Check bounds
            if target_row < 0 or target_row >= len(collision_grid) or target_col < 0 or target_col >= len(collision_grid[0]):
                return False
                
            # Check if target cell is walkable (non-zero values are walkable in this system)
            return collision_grid[target_row][target_col] != 0
            
        except Exception as e:
            print(f"ConsolidatedNavigator: Error checking walkability: {e}")
            return True  # Default to walkable on error

    def _get_collision_map_grid(self) -> Optional[list]:
        """Get the collision map as a grid"""
        try:
            return self.env.pyboy.game_area_collision()
        except Exception as e:
            print(f"ConsolidatedNavigator: Error getting collision map: {e}")
            return None

    def _get_player_collision_position(self) -> Tuple[int, int]:
        """Get player position in collision grid coordinates"""
        try:
            # This is a simplified version - may need adjustment based on actual coordinate system
            local_x, local_y, _ = self.env.get_game_coords()
            # Convert to collision grid coordinates (this may need refinement)
            return local_y, local_x
        except:
            return 9, 8  # Default center position

    # =========================
    # COORDINATE PATH MANAGEMENT
    # =========================
    
    def load_coordinate_path(self, quest_id: int) -> bool:
        """Load coordinate path for a quest with aggressive debugging"""
        
        # AGGRESSIVE DEBUG: Don't skip loading if we already have the same quest loaded

        quest_dir_name = f"{quest_id:03d}"
        quest_file_name = f"{quest_dir_name}_coords.json"
        file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name

        if not file_path.exists():
            return False


        try:
            with open(file_path, 'r') as f:
                quest_data = json.load(f)
        except Exception as e:
            return False


        # Flatten all coordinates across all map segments
        all_coordinates = []
        map_ids = []
        
        # Prioritize current map first
        try:
            current_map = self.env.get_game_coords()[2]
        except:
            current_map = None
        
        
        def parse_segment_key(key):
            if '_' in key:
                parts = key.split('_')
                return (int(parts[0]), int(parts[1]))
            else:
                return (int(key), 0)
        
        # Use file order of segments (JSON preserves insertion order)
        sorted_segments = list(quest_data.keys())
        
        for segment_key in sorted_segments:
            segment_coords = quest_data[segment_key]
            
            if '_' in segment_key:
                map_id = int(segment_key.split('_')[0])
            else:
                map_id = int(segment_key)
            
            for coord in segment_coords:
                all_coordinates.append((coord[0], coord[1]))
                map_ids.append(map_id)

        if not all_coordinates:
            return False

        # AGGRESSIVE RESET: Always start from index 0 for now
        # We'll use snap_to_nearest_coordinate to find the right position
        
        # Update state
        self.sequential_coordinates = all_coordinates
        self.coord_map_ids = map_ids
        self.current_coordinate_index = 0  # Always start from beginning
        self.active_quest_id = quest_id
        self._last_loaded_quest_id = quest_id
        self.using_placeholder_map_ids = True
        # Do NOT automatically switch to navigating; that is triggered only
        # when the player (or Grok) explicitly issues a PATH_FOLLOW_ACTION
        # via key "5"/action 6.  This prevents the navigator from taking
        # autonomous steps when the user simply loads a quest for reference.

        
        # Try to snap to nearest coordinate
        try:
            self.snap_to_nearest_coordinate()
        except Exception as e:
            return True
        return True

    def snap_to_nearest_coordinate(self) -> bool:
        """Snap the navigator index to the nearest coordinate based on current position"""
        if not self.sequential_coordinates:
            if not self._ensure_quest_loaded():
                return False

        try:
            cur_pos = self._get_player_global_coords()
            if not cur_pos:
                return False

            # Find nearest coordinate
            distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
            nearest_i = distances.index(min(distances))
            dist = distances[nearest_i]
            
            # Only snap if distance is reasonable
            if dist <= 13:
                old_index = self.current_coordinate_index
                # Only snap forward, avoid rewinding index on path with repeating coords
                if nearest_i > old_index:
                    self.current_coordinate_index = nearest_i
                    print(f"ConsolidatedNavigator: Snapped from index {old_index} to {nearest_i}")
                else:
                    # retain existing index when nearest coordinate is behind or same
                    pass
                
                self.movement_failure_count = 0
                if self.current_coordinate_index >= len(self.sequential_coordinates):
                    self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)

                return True
            else:
                print(f"ConsolidatedNavigator: Distance too large ({dist}), not snapping")
                return False
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Error in snap_to_nearest_coordinate: {e}")
            return False

    def _ensure_quest_loaded(self) -> bool:
        """Ensure a quest is loaded"""
        current_quest = self.get_current_quest()
        if current_quest:
            return self.load_coordinate_path(current_quest)
        return False

    # =========================
    # WARP HANDLING
    # =========================
    
    def warp_tile_handler(self) -> bool:
        """Handle warp tile interactions"""
        print(f"ConsolidatedNavigator: warp_tile_handler called")
        
        # Check cooldown
        if (time.time() - self.last_warp_time) < self.WARP_COOLDOWN_SECONDS:
            return False

        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            local_pos = (local_x, local_y)
            
            warp_entries_for_cur_map = WARP_DICT.get(MapIds(cur_map).name, [])
            if not warp_entries_for_cur_map:
                return False

            # Find nearest warp
            nearest_warp_tile_local = None
            for entry in warp_entries_for_cur_map:
                warp_x, warp_y = entry.get("x"), entry.get("y")
                if warp_x is not None and warp_y is not None:
                    distance = self._manhattan(local_pos, (warp_x, warp_y))
                    if distance <= 1:  # Adjacent or on warp tile
                        nearest_warp_tile_local = (warp_x, warp_y)
                        break

            if not nearest_warp_tile_local:
                return False
            
            # Get active warp entry
            active_warp_entry = next((e for e in warp_entries_for_cur_map 
                                    if (e.get("x"), e.get("y")) == nearest_warp_tile_local), None)
            if not active_warp_entry:
                return False
                
            target_map_id = active_warp_entry.get("target_map_id")
            if target_map_id is None:
                return False

            # Handle LAST_MAP special case
            print(f'target_map_id: {target_map_id}')
            if target_map_id == 255:
                return True
                # if len(self.map_history) >= 2:
                #     target_map_id = self.map_history[-2]
                # else:
                #     target_map_id = 0  # Fallback

            print(f"ConsolidatedNavigator: Executing warp to map {target_map_id}")
            
            # Calculate direction to warp
            dx = nearest_warp_tile_local[0] - local_pos[0]
            dy = nearest_warp_tile_local[1] - local_pos[1]
            
            # Determine movement action
            if abs(dy) > abs(dx):
                movement_action = 0 if dy > 0 else 3  # DOWN or UP
            elif dx != 0:
                movement_action = 2 if dx > 0 else 1  # RIGHT or LEFT
            else:
                movement_action = 0  # DOWN as default
            
            # Execute warp movement
            moved = self._execute_movement(movement_action, bypass_collision=True)
            
            if moved:
                self.last_warp_time = time.time()
                self.record_warp_step()
                return True
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Error in warp_tile_handler: {e}")
            
        return False

    def get_available_warps_on_current_map(self) -> List[Dict]:
        """Get available warps on the current map"""
        try:
            _, _, cur_map = self.env.get_game_coords()
            warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
            
            available_warps = []
            for entry in warp_entries:
                warp_x, warp_y = entry.get("x"), entry.get("y")
                target_map_id = entry.get("target_map_id")
                
                if warp_x is not None and warp_y is not None and target_map_id is not None:
                    available_warps.append({
                        'local_coords (x, y)': (warp_x, warp_y),
                        'target_map_id': target_map_id,
                        'position (x, y)': (warp_x, warp_y),  # Legacy compatibility
                        'destination_map': target_map_id  # Legacy compatibility
                    })
            
            return available_warps
            
        except Exception as e:
            print(f"ConsolidatedNavigator: Error getting available warps: {e}")
            return []

    def is_warp_aligned_with_path(self, warp_info: Dict) -> bool:
        """Check if a warp is aligned with the current quest path"""
        if warp_info.get('target_map_id') == 255:
            return True
        
        if not self.sequential_coordinates or not self.coord_map_ids:
            return False
            
        target_map_id = warp_info.get('target_map_id')
        if target_map_id is None:
            return False
        
        # Look ahead in the path for coordinates on the target map
        for i in range(self.current_coordinate_index, len(self.coord_map_ids)):
            if self.coord_map_ids[i] == target_map_id:
                return True
                
        return False

    # =========================
    # NAVIGATION CONTROL METHODS
    # =========================
    
    def navigate_to_coordinate(self) -> bool:
        """Navigate to the current target coordinate"""
        if not self.sequential_coordinates:
            return False
            
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            return False
            
        target_coord = self.sequential_coordinates[self.current_coordinate_index]
        cur_pos = self._get_player_global_coords()
        
        if not cur_pos:
            return False
            
        # Check if already at target
        if cur_pos == target_coord:
            self.current_coordinate_index += 1
            return True
            
        # Calculate movement direction
        dy = target_coord[0] - cur_pos[0]
        dx = target_coord[1] - cur_pos[1]
        
        # Choose direction based on larger delta
        if abs(dy) > abs(dx):
            action = 0 if dy > 0 else 3  # DOWN or UP
        elif dx != 0:
            action = 2 if dx > 0 else 1  # RIGHT or LEFT
        else:
            return True  # Already at target
            
        # Execute movement
        return self._execute_movement(action)

    def get_current_status(self) -> str:
        """Get comprehensive navigator status"""
        pos = self._get_player_global_coords()
        try:
            cur_map = self.env.get_game_coords()[2]
        except:
            cur_map = "?"
            
        s = ["\n*** CONSOLIDATED NAVIGATOR STATUS ***"]
        s.append(f"Quest ID       : {self.active_quest_id}")
        s.append(f"Current pos    : {pos}")
        s.append(f"Current map    : {cur_map}")
        s.append(f"Path length    : {len(self.sequential_coordinates)}")
        s.append(f"Current index  : {self.current_coordinate_index}")
        s.append(f"Direction      : {self._direction}")

        if self.sequential_coordinates and 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
            tgt = self.sequential_coordinates[self.current_coordinate_index]
            dist = self._manhattan(pos, tgt) if pos else "?"
            s.append(f"Current target : {tgt} (dist {dist})")
            
        # Show available warps
        warps = self.get_available_warps_on_current_map()
        if warps:
            s.append(f"Available warps: {len(warps)}")
            for warp in warps[:3]:
                aligned = self.is_warp_aligned_with_path(warp)
                s.append(f"  -> Map {warp['target_map_id']} ({'ALIGNED' if aligned else 'not aligned'})")

        return "\n".join(s)

    def handle_pygame_event(self, event) -> Optional[int]:
        """
        Handle pygame keyboard events and return corresponding action indices.
        Simple converter that maps pygame events to action integers.
        Navigation logic is handled elsewhere in the system.
        
        Args:
            event: pygame event object
            
        Returns:
            int: Action index for the pressed key, or None for unhandled events
        """
        # Import pygame here to avoid import issues if pygame is not available
        try:
            import pygame
        except ImportError:
            return None
            
        if event.type != pygame.KEYDOWN:
            return None
            
        # Import constants from environment module
        try:
            from environment.environment import VALID_ACTIONS, PATH_FOLLOW_ACTION
            from pyboy.utils import WindowEvent
        except ImportError:
            return None
        
        # Simple action mapping - mirrors the one defined in play.py
        ACTION_MAPPING = {
            pygame.K_DOWN: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN),
            pygame.K_LEFT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT),
            pygame.K_RIGHT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT),
            pygame.K_UP: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP),
            pygame.K_a: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A),
            pygame.K_s: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B),
            pygame.K_RETURN: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START),
            pygame.K_5: PATH_FOLLOW_ACTION,
        }
        
        # Return the mapped action or None for unhandled keys
        return ACTION_MAPPING.get(event.key)

    def _reset_state(self):
        """Reset navigator state"""
        self.sequential_coordinates.clear()
        self.coord_map_ids.clear()
        self.current_coordinate_index = 0
        self.quest_locked = False
        self.navigation_status = "idle"
        self.active_quest_id = None
        self.movement_failure_count = 0

    def reset_quest_state(self):
        """Reset quest-related navigation state (called from QuestWarpBlocker)"""
        print(f"ConsolidatedNavigator: Resetting quest state")
        self._reset_state()

    # Add update_after_step hook to sync coordinates after every action step
    def update_after_step(self, obs, reward, terminated, truncated, info):
        """Synchronize navigator position after any environment step"""
        print(f"navigator.py: update_after_step(): ConsolidatedNavigator: update_after_step called")
        try:
            # Snap to nearest coordinate to reflect actual player position
            print(f"navigator.py: update_after_step(): ConsolidatedNavigator: try success; player location: {self.env.get_game_coords()}")
            self.snap_to_nearest_coordinate()
            print(f"navigator.py: update_after_step(): ConsolidatedNavigator: snap_to_nearest_coordinate called; player location: {self.env.get_game_coords()}")
        except Exception as e:
            print(f"ConsolidatedNavigator: update_after_step error: {e}")

    def manual_warp_trigger(self) -> bool:
        """Legacy compatibility wrapper.
        This method used to exist in the old Navigator implementation and is still
        referenced from play.py.  The new consolidated navigator already exposes
        all warp-handling logic via ``warp_tile_handler`` so we simply forward
        the call.  Any exceptions are caught and logged and ``False`` is
        returned to indicate the warp did not execute.
        """
        try:
            return self.warp_tile_handler()
        except Exception as e:
            print(f"ConsolidatedNavigator: manual_warp_trigger error: {e}")
            return False

    # ------------------------------------------------------------------
    # Legacy path-following helper
    # ------------------------------------------------------------------
    def get_next_action(self) -> Optional[int]:
        """Return the next movement action when following a quest path.

        This is a lightweight wrapper around
        ``convert_path_follow_to_movement_action`` which is responsible for
        translating the special PATH_FOLLOW_ACTION token (value ``6``) into an
        actual movement action (0-3, or occasionally 4/5 for A/B presses) based
        on the pre-recorded human coordinate traces.

        play.py expects this method to return either an **integer** action that
        can be fed directly to the environment, or ``None`` if no movement is
        required.  We therefore invoke the converter with the stored
        ``_path_follow_action_value`` and only return the result if it produced
        a *real* action.
        """
        # Only produce a move when the navigator is actively following a path
        # (set to "navigating" by an explicit PATH_FOLLOW_ACTION trigger).
        if self.navigation_status != "navigating":
            return None

        try:
            action = self.convert_path_follow_to_movement_action(self._path_follow_action_value)
            # If the converter could not determine a concrete move it will
            # return the original PATH_FOLLOW_ACTION value – treat that as
            # *no* action so that the caller can decide how to proceed.
            if action is None or action == self._path_follow_action_value:
                return None
            return action
        except Exception as e:
            print(f"ConsolidatedNavigator: get_next_action error: {e}")
            return None

# =========================
# LEGACY COMPATIBILITY WRAPPER
# =========================

# Provide backward compatibility
InteractiveNavigator = ConsolidatedNavigator

# =========================
# EXTERNAL INTERFACE FUNCTIONS
# =========================

def record_warp_step(env, navigator):
    """Legacy interface for warp step recording"""
    if hasattr(navigator, 'record_warp_step'):
        navigator.record_warp_step()

def backtrack_warp_sequence(env, navigator):
    """Legacy interface for warp backtracking"""
    if hasattr(navigator, 'execute_backtrack'):
        return navigator.execute_backtrack()
    return False
