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
import logging

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

# =========================
# NAVIGATION DEBUG LOGGER
# =========================

class NavigationDebugLogger:
    """Dedicated logger for navigation debugging"""
    
    def __init__(self, log_file="navigation_debug.log"):
        self.log_file = Path(__file__).parent.parent.parent / "logs" / log_file
        self.log_file.parent.mkdir(exist_ok=True)
        
        # Setup dedicated logger
        self.logger = logging.getLogger('nav_debug')
        self.logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # Add file handler
        handler = logging.FileHandler(self.log_file)
        formatter = logging.Formatter('%(asctime)s - NAV_DEBUG - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        
        # Prevent propagation to avoid duplicate logs
        self.logger.propagate = False
        
    def log(self, message: str, data: dict = None):
        """Log navigation debug message"""
        if data:
            self.logger.debug(f"{message} | DATA: {data}")
        else:
            self.logger.debug(message)
            
    def log_movement_attempt(self, step: int, player_pos: tuple, target_coord: tuple, 
                           action: int, moved: bool, failure_count: int):
        """Log movement attempt details"""
        self.log("MOVEMENT_ATTEMPT", {
            'step': step,
            'player_pos': player_pos, 
            'target_coord': target_coord,
            'action': action,
            'moved': moved,
            'failure_count': failure_count
        })
        
    def log_comprehensive_movement(self, step: int, player_pos: tuple, target_coord: tuple,
                                 action: int, moved: bool, failure_count: int, 
                                 quest_id: int = None, navigation_status: str = "idle",
                                 current_map: int = None, coord_index: int = None,
                                 astar_active: bool = False, reload_triggered: bool = False):
        """Log comprehensive movement attempt with full context"""
        self.log("COMPREHENSIVE_MOVEMENT", {
            'step': step,
            'player_pos': player_pos,
            'target_coord': target_coord,
            'action': action,
            'moved': moved,
            'failure_count': failure_count,
            'quest_id': quest_id,
            'navigation_status': navigation_status,
            'current_map': current_map,
            'coord_index': coord_index,
            'astar_active': astar_active,
            'reload_triggered': reload_triggered
        })
        
    def log_quest_load(self, quest_id: int, coord_count: int, current_index: int):
        """Log quest coordinate loading"""
        self.log("QUEST_LOAD", {
            'quest_id': quest_id,
            'coord_count': coord_count,
            'current_index': current_index
        })
        
    def log_node_selection(self, method: str, player_pos: tuple, selected_node: tuple, 
                          selected_index: int, distance: int = None):
        """Log node selection details"""
        self.log("NODE_SELECTION", {
            'method': method,
            'player_pos': player_pos,
            'selected_node': selected_node,
            'selected_index': selected_index,
            'distance': distance
        })
        
    def log_fallback_trigger(self, trigger_type: str, failure_count: int, details: dict = None):
        """Log fallback system triggers"""
        self.log("FALLBACK_TRIGGER", {
            'type': trigger_type,
            'failure_count': failure_count,
            'details': details or {}
        })

# =========================
# A* PATHFINDING IMPLEMENTATION
# =========================

class AStarNavigator:
    """A* pathfinding for fallback navigation"""
    
    def __init__(self, env):
        self.env = env
        
    def find_path(self, start: tuple, goal: tuple, max_distance: int = 50) -> List[tuple]:
        """Find A* path from start to goal coordinates"""
        try:
            from heapq import heappush, heappop
            
            # A* implementation
            open_set = [(0, start)]
            came_from = {}
            g_score = {start: 0}
            f_score = {start: self._heuristic(start, goal)}
            
            while open_set:
                current = heappop(open_set)[1]
                
                if current == goal:
                    # Reconstruct path
                    path = []
                    while current in came_from:
                        path.append(current)
                        current = came_from[current]
                    path.append(start)
                    return path[::-1]  # Reverse to get start->goal
                
                for neighbor in self._get_neighbors(current):
                    tentative_g = g_score[current] + 1
                    
                    if neighbor not in g_score or tentative_g < g_score[neighbor]:
                        came_from[neighbor] = current
                        g_score[neighbor] = tentative_g
                        f_score[neighbor] = tentative_g + self._heuristic(neighbor, goal)
                        heappush(open_set, (f_score[neighbor], neighbor))
                        
                        # Limit search distance
                        if tentative_g > max_distance:
                            break
            
            return []  # No path found
            
        except Exception as e:
            print(f"AStarNavigator: Error in A* pathfinding: {e}")
            return []
    
    def _heuristic(self, a: tuple, b: tuple) -> int:
        """Manhattan distance heuristic"""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])
    
    def _get_neighbors(self, pos: tuple) -> List[tuple]:
        """Get valid neighboring positions"""
        y, x = pos
        neighbors = [
            (y-1, x), (y+1, x),  # up, down
            (y, x-1), (y, x+1)   # left, right
        ]
        
        # Basic bounds checking - you may want to add collision detection here
        valid_neighbors = []
        for ny, nx in neighbors:
            if 0 <= ny < 1000 and 0 <= nx < 1000:  # Reasonable map bounds
                valid_neighbors.append((ny, nx))
        
        return valid_neighbors

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
        
        # Initialize loggers
        self.logger = get_pokemon_logger()
        self.debug_logger = NavigationDebugLogger()

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
        self._fallback_searched = False    # Track if fallback search has been performed

        # ENHANCED MOVEMENT TRACKING WITH FALLBACK SYSTEM
        self.movement_failure_count = 0
        self.max_movement_failures = 5  # Reduced from 10 to 5 as user mentioned
        self.reload_coordinates_triggered = False
        self.astar_failure_count = 0  # Additional counter for A* fallback
        self.max_astar_failures = 3  # A* kicks in after 3 more failures
        self.astar_navigator = AStarNavigator(self.env)
        self.astar_path: List[tuple] = []
        self.astar_path_index = 0
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
        self.current_index_history: List[int] = []
        
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
        
    def get_current_local_coords(self) -> Tuple[int, int, int]:
        """Get current local coordinates"""
        return self.env.get_game_coords()
    
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
        Convert PATH_FOLLOW_ACTION to concrete movement actions.
        This is the main path-following logic that handles quest navigation.
        """
        import traceback
        import sys
        
        # LOG: Track when this method is called
        print(f"ConsolidatedNavigator: convert_path_follow_to_movement_action() CALLED with original_action={original_action}")
        self.debug_logger.log("CONVERT_PATH_FOLLOW_CALLED", {
            'original_action': original_action,
            'path_follow_action_value': self._path_follow_action_value,
            'navigation_status': self.navigation_status
        })
        
        if original_action != self._path_follow_action_value:
            print(f"ConsolidatedNavigator: convert_path_follow_to_movement_action() RETURNING {original_action} - not PATH_FOLLOW_ACTION")
            self.debug_logger.log("CONVERT_PATH_FOLLOW_RETURNED_ORIGINAL", {
                'reason': 'not_path_follow_action',
                'returned_action': original_action
            })
            return original_action
        
        try:
            # Get current quest from quest manager
            current_quest = self.get_current_quest()
            print(f"ConsolidatedNavigator: convert_path_follow_to_movement_action() - current_quest={current_quest}")
            
            # CRITICAL: Reset fallback search if quest has changed
            if hasattr(self, '_last_processed_quest') and self._last_processed_quest != current_quest:
                print(f"ConsolidatedNavigator: Quest changed from {self._last_processed_quest} to {current_quest} - resetting fallback search and path")
                self._fallback_searched = False
                self._fallback_mode = False
                self._original_quest_id = None
                self.quest_locked = False
                # CRITICAL FIX: Reset the current path and index when quest changes
                self.sequential_coordinates = []
                self.coord_map_ids = []
                self.current_coordinate_index = 0
                self.debug_logger.log("QUEST_CHANGE_RESET", {
                    'old_quest': self._last_processed_quest,
                    'new_quest': current_quest,
                    'reset_index': self.current_coordinate_index,
                    'reset_path_length': len(self.sequential_coordinates)
                })
            self._last_processed_quest = current_quest
            
            # Get current player position
            current_global = self._get_player_global_coords()
            if not current_global:
                print("ConsolidatedNavigator: Could not get player global coordinates")
                self.debug_logger.log("CONVERT_PATH_FOLLOW_ERROR", {
                    'error': 'no_player_global_coords',
                    'returned_action': self._get_noop_action()
                })
                return self._get_noop_action()
            
            print(f"CURRENT POSITION: local={self.env.get_game_coords()}, map={self.env.get_game_coords()[2]}, global={current_global}")
            print(f"CURRENT QUEST: {current_quest}")
            
            # Check if fallback navigation should be completed (original quest is now reachable)
            if self._check_fallback_completion():
                print(f"ConsolidatedNavigator: Fallback completed - resuming normal navigation")
                self.debug_logger.log("FALLBACK_COMPLETED", {
                    'active_quest_id': self.active_quest_id,
                    'navigation_status': self.navigation_status
                })
                # Continue with normal navigation flow
            
            # Attempt fallback navigation if current quest is unreachable
            if self._attempt_fallback_navigation():
                print(f"ConsolidatedNavigator: Fallback navigation activated - continuing with fallback path")
                self.debug_logger.log("FALLBACK_ACTIVATED", {
                    'active_quest_id': self.active_quest_id,
                    'navigation_status': self.navigation_status
                })
                # Continue with normal navigation flow using the fallback quest path
            
            # Load quest if needed - BUT NOT if we're in fallback mode
            if not self._fallback_mode and (not self.sequential_coordinates or self.active_quest_id != current_quest):
                print(f"LOADING QUEST PATH for quest {current_quest}")
                if not self.load_coordinate_path(current_quest):
                    print("FATAL: Failed to load quest path")
                    traceback.print_stack()
                    sys.exit(1)
                print(f"LOADED {len(self.sequential_coordinates)} coordinates")
                self.debug_logger.log("PATH_LOADED", {
                    'quest_id': current_quest,
                    'active_quest_id': self.active_quest_id,
                    'coordinate_count': len(self.sequential_coordinates)
                })
            elif self._fallback_mode:
                print(f"SKIPPING FORCE LOAD - navigator locked on fallback path (quest {self.active_quest_id})")
                self.debug_logger.log("SKIPPING_FORCE_LOAD", {
                    'active_quest_id': self.active_quest_id,
                    'navigation_status': self.navigation_status,
                    'fallback_mode': self._fallback_mode
                })

            # CRITICAL: Find where we actually are in the path
            print(f"CHECKING PATH POSITION")
            self.debug_logger.log("CHECKING_PATH_POSITION", {
                'active_quest_id': self.active_quest_id,
                'current_coordinate_index': self.current_coordinate_index,
                'sequential_coordinates_length': len(self.sequential_coordinates) if self.sequential_coordinates else 0
            })
            next_idx = self.current_coordinate_index + 1 if self.current_coordinate_index + 1 < len(self.sequential_coordinates) else None
            
            if current_global in self.sequential_coordinates:
                # Handle possible duplicates gracefully
                matching_indices = [idx for idx, c in enumerate(self.sequential_coordinates) if c == current_global]
                forward_indices = [idx for idx in matching_indices if idx >= self.current_coordinate_index]
                # Prefer the nearest forward match; if none, advance to the next path index instead of rewinding
                if forward_indices:
                    chosen_idx = min(forward_indices)
                else:
                    next_idx = self.current_coordinate_index + 1
                    chosen_idx = next_idx if next_idx < len(self.sequential_coordinates) else self.current_coordinate_index
                
                try:
                    print(f"chosen_idx: {chosen_idx}")
                    print(f"next_idx: {next_idx}")
                    print(f"self.current_coordinate_index: {self.current_coordinate_index}")

                    if next_idx is not None and next_idx < len(self.sequential_coordinates):
                        print(f"next_idx node: {self.sequential_coordinates[next_idx]}")
                    else:
                        print("next_idx node: <out of bounds>")

                    print(f"target coordinate index: {self.sequential_coordinates[self.current_coordinate_index]}")
                    slice_end = min(self.current_coordinate_index + 3, len(self.sequential_coordinates))
                    print(
                        f" local path nodes,: {self.sequential_coordinates[self.current_coordinate_index:slice_end]}"
                    )

                except Exception as e:
                    print(f"Error printing path nodes: {e}")
                    traceback.print_exc()
                    # Do NOT crash the whole emulator for a logging issue.
                    pass

                print(f" current coords: {current_global}, current_idx: {self.current_coordinate_index}")
                if chosen_idx != self.current_coordinate_index:
                    print(
                        f"RESYNC: player at coord present at indices {matching_indices}; "
                        f"choosing {chosen_idx} to maintain forward progress (was {self.current_coordinate_index})"
                    )
                    self.current_coordinate_index = chosen_idx

            # Get target coordinate - ALWAYS THE NEXT ONE IN SEQUENCE
            if self.current_coordinate_index >= len(self.sequential_coordinates):
                print("PATH COMPLETE - No more coordinates")
                self.debug_logger.log("CONVERT_PATH_FOLLOW_PATH_COMPLETE", {
                    'current_coordinate_index': self.current_coordinate_index,
                    'sequential_coordinates_length': len(self.sequential_coordinates)
                })
                if self.warp_tile_handler():
                    # If a warp movement was issued, return NOOP to let the warp happen
                    return self._get_noop_action()
                print("No warp triggered, path complete")
                return self._get_noop_action()
                
            target_coord = self.sequential_coordinates[self.current_coordinate_index]
            target_map = self.coord_map_ids[self.current_coordinate_index]
            print(f"CURRENT INDEX: {self.current_coordinate_index}")
            
            # LOG: Track target coordinate info
            self.debug_logger.log("CONVERT_PATH_FOLLOW_TARGET_INFO", {
                'target_coord': target_coord,
                'target_map': target_map,
                'current_coordinate_index': self.current_coordinate_index,
                'player_pos': current_global
            })
            
            # ensure index history is ONLY tracked when NOT IN A DIALOG/battle!!
            in_battle = self.env.read_m("wIsInBattle") > 0
            in_dialog = self.env.get_active_dialog()
            print(f"in_battle: {in_battle}, in_dialog: {in_dialog}")
            if not in_battle and in_dialog == None:
                # FIXED: Only track index history when NOT IN A DIALOG/battle
                # Track current index history to detect oscillation and stucks
                self.current_index_history.append(self.current_coordinate_index)
                if len(self.current_index_history) > 10:
                    self.current_index_history.pop(0)
                
                if self.current_index_history.count(self.current_coordinate_index) > 6:
                    print(f"CURRENT INDEX {self.current_coordinate_index} REPEATED {self.current_index_history.count(self.current_coordinate_index)} TIMES")
                    return self.reset_quest_state()
            
            print(f"TARGET COORDINATE: {target_coord} on map {target_map}")
            print(f"current_global: {current_global}, target_coord: {target_coord}")
            
            # Check if we're already at target
            if current_global == target_coord:
                print("ALREADY AT TARGET – evaluating next step")
                self.debug_logger.log("CONVERT_PATH_FOLLOW_AT_TARGET", {
                    'player_pos': current_global,
                    'target_coord': target_coord,
                    'current_coordinate_index': self.current_coordinate_index
                })

                def _within_one(a, b):
                    return self._manhattan(a, b) == 1

                next_idx = self.current_coordinate_index + 1 if self.current_coordinate_index + 1 < len(self.sequential_coordinates) else None

                if next_idx is not None:
                    next_coord = self.sequential_coordinates[next_idx]
                    next_map  = self.coord_map_ids[next_idx]

                    print(f"Next coordinate: {next_coord} on map {next_map}, current map: {self.env.get_game_coords()[2]}")
                    
                    if _within_one(current_global, next_coord):
                        # Normal case – proceed to the next node that is exactly one tile away
                        print(f"ADVANCING TO NEXT PATH INDEX {next_idx} (node {next_coord}) – 1-tile step")
                        self.current_coordinate_index = next_idx
                        target_coord = next_coord
                        target_map   = next_map
                        # CRITICAL FIX: Set navigation status to "navigating" so get_next_action() returns a movement action
                        self.navigation_status = "navigating"
                        self.debug_logger.log("CONVERT_PATH_FOLLOW_ADVANCED_1TILE", {
                            'old_index': self.current_coordinate_index - 1,
                            'new_index': self.current_coordinate_index,
                            'new_target': target_coord,
                            'navigation_status': self.navigation_status
                        })
                    elif next_map != self.env.get_game_coords()[2]:
                        # We are standing on a warp tile (index+1 is on another map).
                        # CRITICAL FIX: Advance the coordinate index first, then invoke the warp handler
                        print(f"ADVANCING TO NEXT PATH INDEX {next_idx} (node {next_coord}) – map transition from {self.env.get_game_coords()[2]} to {next_map}")
                        self.current_coordinate_index = next_idx
                        target_coord = next_coord
                        target_map = next_map
                        
                        # CRITICAL FIX: Set navigation status to "navigating" so get_next_action() returns a movement action
                        self.navigation_status = "navigating"
                        self.debug_logger.log("CONVERT_PATH_FOLLOW_ADVANCED_MAP_TRANSITION", {
                            'old_index': self.current_coordinate_index - 1,
                            'new_index': self.current_coordinate_index,
                            'new_target': target_coord,
                            'navigation_status': self.navigation_status
                        })
                        
                        # Now invoke the dedicated warp handler so it can perform the
                        # required one-step nudge and let the transition happen.
                        print("Standing on warp tile – invoking warp_tile_handler() to trigger transition")
                        if self.warp_tile_handler():
                            # warp_tile_handler executed a move; its own movement
                            # action will be returned up the stack.
                            return self._get_noop_action()
                        else:
                            # FIXED: If warp_tile_handler didn't execute a movement, 
                            # continue with normal movement calculation instead of nooping
                            print("warp_tile_handler returned False – continuing with normal movement calculation")
                            # Don't return noop - let the function continue to calculate movement
                            # The movement calculation will happen below after this if-elif-else block
                    else:
                        # Next coord is too far but same map - calculate movement toward it
                        print(f"Next path coordinate is not adjacent ({next_coord}) - calculating movement direction")
                        
                        # CRITICAL FIX: Advance the coordinate index first, then calculate movement
                        print(f"ADVANCING TO NEXT PATH INDEX {next_idx} (node {next_coord}) – non-adjacent step")
                        self.current_coordinate_index = next_idx
                        target_coord = next_coord
                        target_map = next_map
                        
                        # CRITICAL FIX: Set navigation status to "navigating" so get_next_action() returns a movement action
                        self.navigation_status = "navigating"
                        self.debug_logger.log("CONVERT_PATH_FOLLOW_ADVANCED_NON_ADJACENT", {
                            'old_index': self.current_coordinate_index - 1,
                            'new_index': self.current_coordinate_index,
                            'new_target': target_coord,
                            'navigation_status': self.navigation_status
                        })
                        
                        # Calculate direction to next coordinate
                        dy = next_coord[0] - current_global[0]
                        dx = next_coord[1] - current_global[1]
                        
                        # Limit to single step movement
                        if abs(dx) > abs(dy):
                            # Prefer horizontal movement
                            dx = 1 if dx > 0 else -1
                            dy = 0
                        else:
                            # Prefer vertical movement
                            dy = 1 if dy > 0 else -1
                            dx = 0
                        
                        # Determine movement action
                        action = 8  # default NOOP/SELECT
                        if dy == -1:
                            action = 3  # UP
                        elif dy == 1:
                            action = 0  # DOWN
                        elif dx == 1:
                            action = 2  # RIGHT
                        elif dx == -1:
                            action = 1  # LEFT
                        
                        print(f"Moving toward next coordinate: action={action}, dx={dx}, dy={dy}")
                        self.debug_logger.log("CONVERT_PATH_FOLLOW_RETURNED_MOVEMENT", {
                            'action': action,
                            'dx': dx,
                            'dy': dy,
                            'reason': 'non_adjacent_movement'
                        })
                        return action
                else:
                    # No next index → path complete
                    print("FINAL NODE REACHED – checking for possible warp")
                    if self.warp_tile_handler():
                        # If a warp movement was issued, return NOOP to let the warp happen
                        return self._get_noop_action()
                    print("No warp triggered, path complete")
                    return self._get_noop_action()

                # Re-compute target after potential index change
                # (only executed if we advanced to a 1-tile neighbour)
                
            # Calculate movement needed
            dy = target_coord[0] - current_global[0]  # Y difference (first value)
            dx = target_coord[1] - current_global[1]  # X difference (second value)
            print(f"DELTA: dx={dx}, dy={dy}")
            
            # LOG: Track movement calculation
            self.debug_logger.log("CONVERT_PATH_FOLLOW_MOVEMENT_CALC", {
                'player_pos': current_global,
                'target_coord': target_coord,
                'dx': dx,
                'dy': dy,
                'distance': abs(dy) + abs(dx)
            })

            # NEW: Ensure we only move one tile per frame and avoid choosing a
            # direction that would immediately hit a wall when both axes differ.
            distance_to_target = abs(dy) + abs(dx)
            if distance_to_target > 1:
                # Prefer horizontal movement first – this matches how most
                # human-recorded paths enter door tiles (→ then ↑, etc.).
                if dx != 0:
                    dx = 1 if dx > 0 else -1
                    dy = 0
                else:
                    dy = 1 if dy > 0 else -1
                print(f"LIMITED TO SINGLE STEP: dx={dx}, dy={dy}")

            # Determine movement action based on limited dx/dy
            action = 8  # default NOOP/SELECT
            if dy == -1:
                action = 3  # UP
            elif dy == 1:
                action = 0  # DOWN
            elif dx == 1:
                action = 2  # RIGHT
            elif dx == -1:
                action = 1  # LEFT
            
            print(f"ConsolidatedNavigator: navigate_to_coordinate: action={action}, target_coord={target_coord}, cur_pos={current_global}, dy={dy}, dx={dx}")
            
            # LOG: Track final action decision
            self.debug_logger.log("CONVERT_PATH_FOLLOW_FINAL_ACTION", {
                'action': action,
                'dx': dx,
                'dy': dy,
                'target_coord': target_coord,
                'player_pos': current_global,
                'reason': 'normal_movement_calculation'
            })
                
            if self.current_coordinate_index == 0:
                # First frame on a freshly-loaded quest – align precisely once
                self.snap_to_nearest_coordinate()
            
            # CRITICAL FIX: Set navigation status to "navigating" so get_next_action() returns a movement action
            self.navigation_status = "navigating"
            
            return action
            
        except Exception as e:
            print(f"FATAL EXCEPTION: {e}")
            self.debug_logger.log("CONVERT_PATH_FOLLOW_FATAL_ERROR", {
                'error': str(e),
                'traceback': traceback.format_exc()
            })
            traceback.print_exc()
            sys.exit(1)
    
    def _get_noop_action(self) -> int:
        """Return the environment's configured NOOP action, falling back to 4 (standard noop)."""
        return getattr(self.env, 'noop_button_index', 4)

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
        """Execute a movement action with collision detection and fallback system"""
        print(f"ConsolidatedNavigator: Executing movement action {action}")
        
        # Store position before movement for debug logging
        pos_before = self.env.get_game_coords()
        cur_pos = self._get_player_global_coords()
        target_coord = None
        if (self.sequential_coordinates and 
            0 <= self.current_coordinate_index < len(self.sequential_coordinates)):
            target_coord = self.sequential_coordinates[self.current_coordinate_index]
        
        # Get current map for enhanced logging
        try:
            current_map = self.env.get_game_coords()[2]
        except:
            current_map = None
        
        # Safety checks
        if self.env.read_m("wIsInBattle") != 0:
            print("ConsolidatedNavigator: Movement blocked - in battle")
            return False
            
        dialog = self.env.read_dialog()
        if dialog and dialog.strip():
            print(f"ConsolidatedNavigator: Movement blocked - dialog active")
            return False
        
        # ENHANCED LOGIC: Skip collision check for human-recorded paths
        # Human-recorded quest paths are guaranteed walkable, so collision checking
        # only causes problems when coordinates don't perfectly match the collision grid
        should_skip_collision = (
            self.sequential_coordinates and 
            self.active_quest_id is not None and 
            not bypass_collision  # Only skip for normal path following, not warp activation
        )
        
        if not should_skip_collision and not bypass_collision:
            direction_map = {0: "down", 1: "left", 2: "right", 3: "up"}
            direction = direction_map.get(action)
            
            if direction and not self._is_direction_walkable(direction):
                print(f"ConsolidatedNavigator: Movement blocked - direction {direction} not walkable")
                # For human-recorded paths, log but don't block - the path should be walkable
                if self.sequential_coordinates and self.active_quest_id is not None:
                    print(f"ConsolidatedNavigator: WARNING - Collision detected for human-recorded path. Proceeding anyway.")
                else:
                    return False
        
        # Check if we're in A* mode and should follow A* path
        if self.navigation_status == "astar_active" and self.astar_path:
            if self.astar_path_index < len(self.astar_path):
                target_pos = self.astar_path[self.astar_path_index]
                # Calculate action to reach A* target
                dy = target_pos[0] - cur_pos[0]
                dx = target_pos[1] - cur_pos[1]
                
                if dy <= -1:
                    action = 3  # up
                elif dx >= 1:
                    action = 2  # right  
                elif dx <= -1:
                    action = 1  # left
                elif dy >= 1:
                    action = 0  # down
                else:
                    # Already at A* target, advance to next
                    self.astar_path_index += 1
                    if self.astar_path_index >= len(self.astar_path):
                        # A* path completed, return to normal navigation
                        self.navigation_status = "idle"
                        self.astar_path = []
                        self.astar_path_index = 0
                        print("ConsolidatedNavigator: A* path completed")
                    return True
                
                print(f"ConsolidatedNavigator: Following A* path to {target_pos} with action {action}")
        
        # Execute the movement
        obs, reward, done, truncated, info = self.env.process_action(action, source="ConsolidatedNavigator")
        
        # Check if position changed
        pos_after = self.env.get_game_coords()
        moved = pos_before != pos_after
        
        print(f"ConsolidatedNavigator: Movement result - Before: {pos_before}, After: {pos_after}, Moved: {moved}")
        
        # FIXED: Enhanced debug logging BEFORE incrementing failure count
        self.debug_logger.log_comprehensive_movement(
            step=getattr(self, '_step_count', 0),
            player_pos=cur_pos,
            target_coord=target_coord,
            action=action,
            moved=moved,
            failure_count=self.movement_failure_count,  # Current count BEFORE increment
            quest_id=self.active_quest_id,
            navigation_status=self.navigation_status,
            current_map=current_map,
            coord_index=self.current_coordinate_index,
            astar_active=(self.navigation_status == "astar_active"),
            reload_triggered=self.reload_coordinates_triggered
        )
        
        # Track movement failures and trigger fallback systems
        if not moved:
            self.movement_failure_count += 1
            print(f"ConsolidatedNavigator: Movement failure {self.movement_failure_count}/{self.max_movement_failures}")

            # TRIGGER RELOAD COORDINATES FALLBACK
            if (self.movement_failure_count >= self.max_movement_failures and 
                not self.reload_coordinates_triggered):
                print("ConsolidatedNavigator: TRIGGERING RELOAD COORDINATES FALLBACK")
                self.debug_logger.log_fallback_trigger(
                    trigger_type="RELOAD_COORDINATES",
                    failure_count=self.movement_failure_count,
                    details={'player_pos': cur_pos, 'target_coord': target_coord, 'quest_id': self.active_quest_id, 'map': current_map}
                )
                if self._trigger_reload_coordinates_fallback():
                    self.reload_coordinates_triggered = True
                    self.movement_failure_count = 0  # Reset failure count after successful reload
                    # Log explicitly that only A* is allowed as next fallback
                    print("[FALLBACK CHAIN] Next fallback will be A* pathfinding if stuck again. No button fallback allowed.")
                    return True

            # TRIGGER A* FALLBACK (never use A/B button as fallback)
            elif (self.reload_coordinates_triggered and 
                  self.movement_failure_count >= self.max_movement_failures):
                self.astar_failure_count += 1
                print(f"ConsolidatedNavigator: A* failure count: {self.astar_failure_count}/{self.max_astar_failures}")

                if self.astar_failure_count >= self.max_astar_failures:
                    print("ConsolidatedNavigator: TRIGGERING A* FALLBACK (A-star pathfinding, never button fallback)")
                    self.debug_logger.log_fallback_trigger(
                        trigger_type="ASTAR_PATHFINDING",
                        failure_count=self.astar_failure_count,
                        details={'player_pos': cur_pos, 'target_coord': target_coord, 'quest_id': self.active_quest_id, 'map': current_map}
                    )
                    if self._trigger_astar_fallback():
                        self.astar_failure_count = 0
                        self.movement_failure_count = 0
                        print("[FALLBACK CHAIN] A* fallback triggered. If A* fails, agent will only NOOP, never press A/B.")
                        return True
        else:
            # Reset failure counts on successful movement
            self.movement_failure_count = 0
            self.astar_failure_count = 0
            self.reload_coordinates_triggered = False
            self.record_warp_step()  # Record successful movement

            # If in A* mode, advance A* path index
            if self.navigation_status == "astar_active" and self.astar_path:
                self.astar_path_index += 1
                if self.astar_path_index >= len(self.astar_path):
                    # A* path completed
                    self.navigation_status = "idle"
                    self.astar_path = []
                    self.astar_path_index = 0
                    print("ConsolidatedNavigator: A* path completed successfully")
        
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
        """Load coordinate path for a quest with aggressive debugging
        
        CRITICAL BUG HISTORY & FIX DOCUMENTATION:
        ==========================================
        
        OSCILLATION BUG (FIXED):
        - ISSUE: Player was oscillating between coordinates (297,118) and (298,118) 
          when pressing '5' key for PATH_FOLLOW_ACTION
        - ROOT CAUSE: Every '5' key press called load_coordinate_path(), which:
          1. Reset current_coordinate_index to 0
          2. Called snap_to_nearest_coordinate()
          3. Snap found either index 64 or 65 depending on exact positioning
          4. Created endless oscillation between these indices
        - SYMPTOM: Player moved back and forth instead of progressing through path
        
        THE FIX:
        - Added check to prevent reloading when quest is already loaded
        - Avoids unnecessary index reset and re-snapping
        - Allows natural forward progression through path sequences
        
        IMPORTANT NOTES FOR FUTURE DEVELOPERS:
        - The path (301→300→299→298→297→298→299→300→301) is NOT an oscillation
        - It represents walking TO the Poke Center counter, then LEAVING
        - The navigation system must allow this legitimate back-and-forth movement
        - NEVER add logic that prevents "backtracking" - it breaks legitimate sequences
        - The key is avoiding unnecessary reloading/re-snapping, not preventing movement
        
        DEBUGGING TIPS:
        - If you see coordinate oscillation, check if load_coordinate_path is being called repeatedly
        - Look for index jumping between nearby values (e.g., 64 ↔ 65)
        - The snap logic should only run when actually needed (new quest load, after warps)
        - PATH_FOLLOW_ACTION should NOT trigger quest reloading if quest is already loaded
        """
        
        # CRITICAL FIX: Don't reload if quest is already loaded to prevent oscillation
        if (self.active_quest_id == quest_id and 
            self.sequential_coordinates and 
            self._last_loaded_quest_id == quest_id):
            return True

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
               
        # Always load in file order of segments (JSON preserves insertion order)
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

        # Update navigator internal state
        self.sequential_coordinates = all_coordinates
        self.coord_map_ids = map_ids
        # Only reset the coordinate index if this is a *new* quest load.
        if self.active_quest_id != quest_id:
            self.current_coordinate_index = 0
        self.active_quest_id = quest_id
        self._last_loaded_quest_id = quest_id
        self.using_placeholder_map_ids = True

        # NO AUTOMATIC SNAPPING HERE – caller can decide if recovery is required.
        return True

    def snap_to_nearest_coordinate(self) -> bool:
        """Snap the navigator index to the nearest coordinate based on current position
        
        BLACKOUT RECOVERY FIX:
        ======================
        This method now includes enhanced logic for blackout recovery scenarios where
        the agent can be teleported far from the quest path (30+ units away).
        
        The method now uses adaptive distance thresholds:
        - Normal snapping: 13 units (for typical on-path navigation)
        - Blackout recovery: 50 units (for when agent is far from path)
        - Emergency recovery: 100 units (for extreme cases)
        
        USAGE GUIDELINES:
        ================
        This method should ONLY be called when:
        1. Loading a new quest for the first time
        2. After a warp/map transition where position might be off-track
        3. When the player is genuinely lost or off-path
        4. During blackout recovery scenarios
        
        DO NOT call this method:
        - On every PATH_FOLLOW_ACTION (causes oscillation)
        - During normal path progression
        - In update_after_step (interferes with movement)
        
        The oscillation bug was caused by calling this method too frequently,
        which prevented normal forward progression through legitimate path sequences.
        """
        if not self.sequential_coordinates:
            if not self._ensure_quest_loaded():
                return False

        try:
            cur_pos = self._get_player_global_coords()
            if not cur_pos:
                return False

            # Find nearest coordinate
            distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
            print(f"DEBUG_NAVIGATOR_SNAP: cur_pos={cur_pos}")
            nearest_i = distances.index(min(distances))
            dist = distances[nearest_i]

            # Adaptive distance thresholds for different scenarios
            NORMAL_THRESHOLD = 13        # Normal on-path navigation
            BLACKOUT_THRESHOLD = 50      # Blackout recovery scenarios
            EMERGENCY_THRESHOLD = 100    # Emergency/extreme cases
            
            # Determine appropriate threshold based on distance
            if dist <= NORMAL_THRESHOLD:
                threshold = NORMAL_THRESHOLD
                recovery_type = "normal"
            elif dist <= BLACKOUT_THRESHOLD:
                threshold = BLACKOUT_THRESHOLD
                recovery_type = "blackout_recovery"
            elif dist <= EMERGENCY_THRESHOLD:
                threshold = EMERGENCY_THRESHOLD
                recovery_type = "emergency_recovery"
            else:
                print(f"ConsolidatedNavigator: Distance extremely large ({dist}), attempting emergency snap")
                threshold = EMERGENCY_THRESHOLD
                recovery_type = "emergency_recovery"
            
            # Only snap if distance is within threshold
            if dist <= threshold:
                old_index = self.current_coordinate_index
                
                # For blackout/emergency recovery, be more flexible with index selection
                if recovery_type in ["blackout_recovery", "emergency_recovery"]:
                    # In recovery scenarios, allow snapping to any nearest coordinate
                    # (not just forward progression) to get back on track
                    self.current_coordinate_index = nearest_i
                    print(f"ConsolidatedNavigator: {recovery_type.upper()} - Snapped from index {old_index} to {nearest_i} (distance: {dist})")
                else:
                    # Normal snapping - only snap forward to avoid rewinding
                    if nearest_i > old_index:
                        self.current_coordinate_index = nearest_i
                        print(f"ConsolidatedNavigator: Normal snap from index {old_index} to {nearest_i}")
                    else:
                        # retain existing index when nearest coordinate is behind or same
                        pass
                
                self.movement_failure_count = 0
                if self.current_coordinate_index >= len(self.sequential_coordinates):
                    self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)

                # For recovery scenarios, also try to generate A* recovery path
                if recovery_type in ["blackout_recovery", "emergency_recovery"]:
                    self._attempt_recovery_path_generation()

                return True
            else:
                print(f"ConsolidatedNavigator: Distance too large ({dist}), not snapping (threshold: {threshold})")
                # For very large distances, attempt emergency navigation
                if dist > EMERGENCY_THRESHOLD:
                    return self._attempt_emergency_navigation(cur_pos, dist)
                return False
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Error in snap_to_nearest_coordinate: {e}")
            return False

    def _attempt_recovery_path_generation(self) -> bool:
        """Attempt to generate A* recovery path when far from quest coordinates"""
        try:
            if not self.sequential_coordinates or self.current_coordinate_index >= len(self.sequential_coordinates):
                return False
                
            target_coord = self.sequential_coordinates[self.current_coordinate_index]
            cur_pos = self._get_player_global_coords()
            
            if not cur_pos:
                return False
                
            # Check if we're on the same map as the target
            try:
                _, _, current_map = self.env.get_game_coords()
                target_map = self.coord_map_ids[self.current_coordinate_index] if self.coord_map_ids else current_map
                
                if current_map == target_map:
                    # Calculate relative position for A* pathfinding
                    dy = target_coord[0] - cur_pos[0]
                    dx = target_coord[1] - cur_pos[1]
                    
                    # Only attempt if target is within reasonable A* range
                    if abs(dx) <= 8 and abs(dy) <= 8:
                        target_row = 4 + dy
                        target_col = 4 + dx
                        target_row = max(0, min(8, target_row))
                        target_col = max(0, min(9, target_col))
                        
                        status_msg, path_dirs = self.env.find_path(int(target_row), int(target_col))
                        if path_dirs:
                            print(f"ConsolidatedNavigator: Recovery path generated: {status_msg} → {path_dirs}")
                            self._recovery_steps = path_dirs.copy()
                            return True
                            
            except Exception as e:
                print(f"ConsolidatedNavigator: Error generating recovery path: {e}")
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Error in recovery path generation: {e}")
            
        return False

    def _attempt_emergency_navigation(self, cur_pos: tuple, distance: int) -> bool:
        """Attempt emergency navigation when extremely far from quest path"""
        try:
            print(f"ConsolidatedNavigator: EMERGENCY NAVIGATION - Player at {cur_pos}, distance {distance} from quest path")
            
            # Check if we need to warp to get closer to the quest
            current_quest = self.get_current_quest()
            if not current_quest:
                return False
                
            # Get current map and check if target quest coordinates are on a different map
            try:
                _, _, current_map = self.env.get_game_coords()
                
                # Find the first coordinate in the quest that's on a different map
                target_map = None
                target_coord_index = 0
                
                if self.coord_map_ids:
                    for i, map_id in enumerate(self.coord_map_ids):
                        if map_id != current_map:
                            target_map = map_id
                            target_coord_index = i
                            break
                
                if target_map and target_map != current_map:
                    print(f"ConsolidatedNavigator: Emergency - Need to navigate from map {current_map} to map {target_map}")
                    
                    # Check if there are warps available on current map that could help
                    available_warps = self.get_available_warps_on_current_map()
                    for warp in available_warps:
                        if warp.get('target_map_id') == target_map:
                            print(f"ConsolidatedNavigator: Found direct warp to target map {target_map}")
                            # Set coordinate index to the target map's first coordinate
                            self.current_coordinate_index = target_coord_index
                            return True
                            
                    # If no direct warp, try to find a path through intermediate maps
                    print(f"ConsolidatedNavigator: No direct warp found, will need multi-step navigation")
                    
                # For same-map emergency navigation, snap to nearest coordinate regardless of distance
                if not target_map or target_map == current_map:
                    print(f"ConsolidatedNavigator: Emergency same-map navigation - forcing snap to nearest coordinate")
                    distances = [self._manhattan(cur_pos, coord) for coord in self.sequential_coordinates]
                    nearest_i = distances.index(min(distances))
                    self.current_coordinate_index = nearest_i
                    print(f"ConsolidatedNavigator: Emergency snap to index {nearest_i}")
                    return True
                    
            except Exception as e:
                print(f"ConsolidatedNavigator: Error in emergency navigation map analysis: {e}")
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Error in emergency navigation: {e}")
            
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
            # get_game_coords returns (x, y, map_id)!!! X FIRST!!!
            local_x, local_y, cur_map = self.env.get_game_coords()  # x, y, map
            local_pos = (local_x, local_y) # note: this is (x, y) !!!
            
            warp_entries_for_cur_map = WARP_DICT.get(MapIds(cur_map).name, [])
            if not warp_entries_for_cur_map:
                return False

            # Check ALL warps to see if player is ON any warp tile first
            player_on_warp = False
            nearest_adjacent_warp = None
            min_distance = float('inf')
            
            for entry in warp_entries_for_cur_map:
                warp_x, warp_y = entry.get("x"), entry.get("y")
                print(f"ConsolidatedNavigator: warp_tile_handler: checking warp at ({warp_x}, {warp_y})")
                if warp_x is not None and warp_y is not None:
                    distance = self._manhattan(local_pos, (warp_x, warp_y))
                    print(f"ConsolidatedNavigator: distance to warp ({warp_x}, {warp_y}): {distance}")
                    
                    if distance == 0:
                        # Player is ON this warp tile
                        player_on_warp = True
                        nearest_adjacent_warp = (warp_x, warp_y)
                        break
                    elif distance == 1 and distance < min_distance:
                        # Player is adjacent to this warp tile
                        nearest_adjacent_warp = (warp_x, warp_y)
                        min_distance = distance
            
            if not player_on_warp and not nearest_adjacent_warp:
                return False
            
            # Get active warp entry (use the warp tile we found)
            active_warp_entry = next((e for e in warp_entries_for_cur_map 
                                    if (e.get("x"), e.get("y")) == nearest_adjacent_warp), None)
            if not active_warp_entry:
                return False
                
            target_map_id = active_warp_entry.get("target_map_id")
            if target_map_id is None:
                return False

            # Log special-case but continue – we still need to *step* so the warp triggers.
            print(f'target_map_id: {target_map_id}')
            if target_map_id == 255:
                print("LAST_MAP (255) warp – will still issue a movement onto the warp tile to trigger transition")

            print(f"ConsolidatedNavigator: Executing warp to map {target_map_id}")
            
            if player_on_warp:
                # Player is ON a warp tile - send "down" command to trigger warp
                print(f"ConsolidatedNavigator: Player ON warp tile at {nearest_adjacent_warp} - sending DOWN command")
                action = 0  # DOWN
            else:
                # Player is ADJACENT to warp tile - walk onto it
                print(f"ConsolidatedNavigator: Player adjacent to warp tile at {nearest_adjacent_warp} - walking onto it")
                # Calculate movement direction to step onto warp tile
                dy = nearest_adjacent_warp[1] - local_pos[1]
                dx = nearest_adjacent_warp[0] - local_pos[0]
                
                action = 8  # select - which does nothing
                if dy == -1: 
                    action = 3  # move up
                elif dx == 1:
                    action = 2  # move right
                elif dx == -1:
                    action = 1  # left
                elif dy == 1:
                    action = 0  # move down
            
            # Execute warp movement
            moved = self._execute_movement(action, bypass_collision=True)
            
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
        """Navigate to the current target coordinate with enhanced debugging"""
        if not self.sequential_coordinates:
            self.debug_logger.log("NAVIGATE_FAILED", {"reason": "no_coordinates"})
            return False
            
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            self.debug_logger.log("NAVIGATE_FAILED", {"reason": "index_out_of_bounds", "index": self.current_coordinate_index, "length": len(self.sequential_coordinates)})
            return False
            
        target_coord = self.sequential_coordinates[self.current_coordinate_index]
        cur_pos = self._get_player_global_coords()
        
        if not cur_pos:
            self.debug_logger.log("NAVIGATE_FAILED", {"reason": "no_player_position"})
            return False
        
        # Get current map for debugging
        try:
            current_map = self.env.get_game_coords()[2]
        except:
            current_map = "unknown"
            
        # FIXED: Check if already at target - advance immediately, don't try to move
        if cur_pos == target_coord:
            old_index = self.current_coordinate_index
            self.current_coordinate_index += 1
            self.debug_logger.log("COORDINATE_REACHED", {
                "old_index": old_index,
                "new_index": self.current_coordinate_index,
                "coordinate": target_coord,
                "map": current_map,
                "quest_id": self.active_quest_id
            })
            print(f"ConsolidatedNavigator: Already at coordinate {target_coord}, advancing to index {self.current_coordinate_index}")
            
            # Check if we've reached the end of the path
            if self.current_coordinate_index >= len(self.sequential_coordinates):
                self.debug_logger.log("PATH_COMPLETED", {
                    "quest_id": self.active_quest_id,
                    "final_coordinate": target_coord,
                    "map": current_map
                })
                print(f"ConsolidatedNavigator: Quest {self.active_quest_id} path completed!")
                
            return True
            
        # Calculate movement direction
        dy = target_coord[0] - cur_pos[0]
        dx = target_coord[1] - cur_pos[1]
        distance = abs(dy) + abs(dx)  # Manhattan distance
        
        # ENHANCED LOGIC: Check if target coordinate is too far away
        # This might indicate wrong node selection
        if distance > 10:  # Arbitrary threshold - coordinates should be close
            self.debug_logger.log("COORDINATE_TOO_FAR", {
                "player_pos": cur_pos,
                "target_coord": target_coord,
                "distance": distance,
                "map": current_map,
                "index": self.current_coordinate_index,
                "quest_id": self.active_quest_id
            })
            print(f"ConsolidatedNavigator: WARNING - Target coordinate {target_coord} is {distance} tiles away. Checking if we need to snap to nearest.")
            
            # Try to snap to nearest coordinate on current map
            if self._snap_to_nearest_on_current_map():
                # Re-get target after snapping
                if self.current_coordinate_index < len(self.sequential_coordinates):
                    target_coord = self.sequential_coordinates[self.current_coordinate_index]
                    dy = target_coord[0] - cur_pos[0]
                    dx = target_coord[1] - cur_pos[1]
                    distance = abs(dy) + abs(dx)
                    print(f"ConsolidatedNavigator: After snapping, new target {target_coord}, distance {distance}")
    
        # FIXED: Determine movement action - use noop (4) as default instead of action 8
        action = 4  # noop action (no movement)
        if dy <= -1: 
            action = 3  # move up
        elif dx >= 1:
            action = 2  # move right
        elif dx <= -1:
            action = 1  # left
        elif dy >= 1:
            action = 0  # move down
        # If dy == 0 and dx == 0, we should have caught this in the "already at target" check above
        
        # Log movement attempt with comprehensive details
        self.debug_logger.log("MOVEMENT_DECISION", {
            "action": action,
            "target_coord": target_coord,
            "cur_pos": cur_pos,
            "dy": dy,
            "dx": dx,
            "distance": distance,
            "map": current_map,
            "index": self.current_coordinate_index,
            "quest_id": self.active_quest_id,
            "navigation_status": self.navigation_status,
            "movement_failures": self.movement_failure_count,
            "reload_triggered": self.reload_coordinates_triggered
        })
        
        print(f"ConsolidatedNavigator: navigate_to_coordinate: action={action}, target_coord={target_coord}, cur_pos={cur_pos}, dy={dy}, dx={dx}, dist={distance}")
            
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
        self.current_index_history.clear()
        self._reset_state()
        
        # Reset fallback navigation flags
        self._fallback_mode = False
        self._fallback_searched = False
        self._original_quest_id = None
        self.quest_locked = False
        print(f"ConsolidatedNavigator: Fallback navigation flags reset")

    # Add update_after_step hook to sync coordinates after every action step
    def update_after_step(self, obs, reward, terminated, truncated, info):
        """Synchronize navigator position after any environment step"""
        print(f"navigator.py: update_after_step(): ConsolidatedNavigator: update_after_step called")
        try:
            current_coords = self._get_player_global_coords()
            print(f"navigator.py: update_after_step(): ConsolidatedNavigator: player location: {current_coords}")

            # Detect "phantom" index advancement where the player did not actually
            # move a tile but the path index was incremented in the previous frame.
            #   – This happens when the user triggers PATH_FOLLOW_ACTION (key `5`)
            #     while already standing on the current target coordinate.
            #   – convert_path_follow_to_movement_action() optimistically increments
            #     the index before the movement finishes. If the chosen action ends
            #     up being a NO-OP the index becomes out-of-sync.
            if (
                hasattr(self, "_last_global_pos")
                and self._last_global_pos is not None
                and current_coords == self._last_global_pos  # No tile movement
                and self.current_coordinate_index > 0
            ):
                # If the *previous* coordinate in the sequence matches the actual
                # player position we know we advanced the index prematurely – roll back.
                prev_idx = self.current_coordinate_index - 1
                if (
                    prev_idx < len(self.sequential_coordinates)
                    and current_coords == self.sequential_coordinates[prev_idx]
                ):
                    print(
                        "Phantom advance detected – reverting current_coordinate_index "
                        f"from {self.current_coordinate_index} to {prev_idx}"
                    )
                    self.current_coordinate_index = prev_idx

            # Update last position tracker for the next tick
            self._last_global_pos = current_coords

            # DO NOT SNAP! Automatic snapping caused erratic index jumps.
            # Simply record the current position so warp detection remains accurate.
            self.record_warp_step()
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
        # LOG: Track when get_next_action is called
        print(f"ConsolidatedNavigator: get_next_action() CALLED - navigation_status: {self.navigation_status}")
        self.debug_logger.log("GET_NEXT_ACTION_CALLED", {
            'navigation_status': self.navigation_status,
            'active_quest_id': self.active_quest_id,
            'current_coordinate_index': self.current_coordinate_index,
            'sequential_coordinates_length': len(self.sequential_coordinates)
        })
        
        # Only produce a move when the navigator is actively following a path
        # (set to "navigating" by an explicit PATH_FOLLOW_ACTION trigger).
        if self.navigation_status != "navigating":
            print(f"ConsolidatedNavigator: get_next_action() RETURNING None - status not 'navigating'")
            self.debug_logger.log("GET_NEXT_ACTION_RETURNED_NONE", {
                'reason': 'navigation_status_not_navigating',
                'navigation_status': self.navigation_status
            })
            return None

        try:
            print(f"ConsolidatedNavigator: get_next_action() calling convert_path_follow_to_movement_action")
            action = self.convert_path_follow_to_movement_action(self._path_follow_action_value)
            # If the converter could not determine a concrete move it will
            # return the original PATH_FOLLOW_ACTION value – treat that as
            # *no* action so that the caller can decide how to proceed.
            if action is None or action == self._path_follow_action_value:
                print(f"ConsolidatedNavigator: get_next_action() RETURNING None - converter returned {action}")
                self.debug_logger.log("GET_NEXT_ACTION_RETURNED_NONE", {
                    'reason': 'converter_returned_none_or_original',
                    'action': action,
                    'path_follow_action_value': self._path_follow_action_value
                })
                return None
            print(f"ConsolidatedNavigator: get_next_action() RETURNING action {action}")
            self.debug_logger.log("GET_NEXT_ACTION_RETURNED_ACTION", {
                'action': action,
                'navigation_status': self.navigation_status
            })
            return action
        except Exception as e:
            print(f"ConsolidatedNavigator: get_next_action error: {e}")
            self.debug_logger.log("GET_NEXT_ACTION_ERROR", {
                'error': str(e),
                'navigation_status': self.navigation_status
            })
            return None

    # =========================
    # FALLBACK NAVIGATION SYSTEM
    # =========================
    
    def _find_nearest_completed_quest_coord(self, max_quest_id: Optional[int] = None) -> Tuple[Optional[int], int]:
        """
        Find the nearest coordinate from any completed quest path.
        Returns (quest_id, coordinate_index) or (None, 0) if none found.
        """
        try:
            current_pos = self._get_player_global_coords()
            if not current_pos:
                return None, 0
                
            nearest_quest = None
            nearest_coord_idx = 0
            min_distance = float('inf')
            
            # Search through all quest paths to find the nearest coordinate
            # CRITICAL: Only search up to max_quest_id to prevent locking onto future quests
            search_range_end = (max_quest_id + 1) if max_quest_id is not None else 100
            for quest_id in range(1, search_range_end):
                quest_dir_name = f"{quest_id:03d}"
                quest_file_name = f"{quest_dir_name}_coords.json"
                file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name
                
                if not file_path.exists():
                    continue
                    
                try:
                    with open(file_path, 'r') as f:
                        quest_data = json.load(f)
                        
                    # Flatten all coordinates across all map segments
                    all_coordinates = []
                    for segment_key in quest_data.keys():
                        segment_coords = quest_data[segment_key]
                        for coord in segment_coords:
                            all_coordinates.append((coord[0], coord[1]))
                    
                    # Find nearest coordinate in this quest
                    for i, coord in enumerate(all_coordinates):
                        distance = self._manhattan(current_pos, coord)
                        if distance < min_distance:
                            min_distance = distance
                            nearest_quest = quest_id
                            nearest_coord_idx = i
                            
                except Exception as e:
                    continue
                    
            return nearest_quest, nearest_coord_idx
            
        except Exception as e:
            print(f"ConsolidatedNavigator: Error finding nearest quest coordinate: {e}")
            return None, 0
    
    def _attempt_fallback_navigation(self, max_quest_id: Optional[int] = None) -> bool:
        """
        Attempt fallback navigation when current quest is unreachable.
        This implements the "lock onto closest completed quest path and proceed forward" logic.
        """
        # Only run fallback search once per quest/location change, not every frame
        if self._fallback_searched:
            return False

        try:
            current_quest = self.get_current_quest()

            # CRITICAL: Do not attempt fallback if current quest is higher than MAX_FALLBACK_QUEST_ID
            # This prevents locking onto future quests incorrectly.
            MAX_FALLBACK_QUEST_ID = current_quest # Default to current quest only

            # Special case for Quest 3 -> Quest 4 transition
            if current_quest == 3:
                MAX_FALLBACK_QUEST_ID = 4 # Allow fallback to Quest 4 if current is Quest 3

            # CASE 1: No active quest - find nearest quest path
            if current_quest is None:
                nearest_q, nearest_coord_idx = self._find_nearest_completed_quest_coord()
                if nearest_q is not None:
                    if self.load_coordinate_path(nearest_q):
                        self.current_coordinate_index = nearest_coord_idx
                        self.active_quest_id = nearest_q
                        self.quest_locked = True
                        self._fallback_mode = True
                        print(f"ConsolidatedNavigator: Fallback to quest {nearest_q} at index {nearest_coord_idx} – quest locked")
                        return True

            # CASE 2: Current quest exists but is unreachable from current position
            elif current_quest is not None:
                # Attempt to load current quest path (this also updates sequential_coordinates)
                if self.load_coordinate_path(current_quest):
                    # Check if current quest is reachable from current position
                    x, y, map_id = self.env.get_game_coords()
                    current_global = local_to_global(y, x, map_id)

                    # Check if any coordinate in current quest path is on the same map
                    current_quest_reachable = False
                    for i, coord in enumerate(self.sequential_coordinates):
                        coord_map = self.coord_map_ids[i] if i < len(self.coord_map_ids) else None
                        if coord_map == map_id:
                            current_quest_reachable = True
                            break

                    # If current quest is NOT reachable (all coordinates on different maps)
                    # then find nearest quest path to proceed forward
                    if not current_quest_reachable:
                        print(
                            f"ConsolidatedNavigator: Current quest {current_quest} unreachable from map {map_id} - seeking fallback path"
                        )
                        nearest_q, nearest_coord_idx = self._find_nearest_completed_quest_coord(
                            max_quest_id=MAX_FALLBACK_QUEST_ID # Pass the max allowed quest ID
                        )
                        if nearest_q is not None and nearest_q != current_quest:
                            # CRITICAL FIX: Don't lock onto a quest that's very far from the target quest
                            # This prevents locking onto quest 25 when QuestManager wants quest 6
                            quest_gap = abs(nearest_q - current_quest)
                            if quest_gap > 10:  # Don't lock if more than 10 quests apart
                                print(f"ConsolidatedNavigator: SKIP FALLBACK - Quest gap too large: {nearest_q} vs {current_quest} (gap: {quest_gap})")
                                print(f"ConsolidatedNavigator: Will attempt direct navigation to quest {current_quest} instead")
                                # Try to load the target quest anyway and see if we can make progress
                                if self.load_coordinate_path(current_quest):
                                    self.current_coordinate_index = 0
                                    self.active_quest_id = current_quest
                                    self.quest_locked = False  # Don't lock - allow normal progression
                                    self._fallback_mode = False
                                    self._original_quest_id = None
                                elif self.load_coordinate_path(nearest_q):
                                    self.current_coordinate_index = nearest_coord_idx
                                    self.active_quest_id = nearest_q
                                    # CRITICAL: Lock onto this quest path and proceed forward
                                    # until we can reach the current quest destination
                                    self.quest_locked = True
                                    self._fallback_mode = True
                                    self._original_quest_id = current_quest
                                    print(
                                        f"ConsolidatedNavigator: CROSS-MAP FALLBACK - Locked onto quest {nearest_q} at index {nearest_coord_idx} to proceed forward until quest {current_quest} becomes reachable"
                                    )
                                    print(f"ConsolidatedNavigator: 🔒 QUEST LOCKED = {self.quest_locked} 🔒")
                                    return True
                    # This `except` block was causing indentation issues, removed for now
                    # except Exception as e:
                    #     print(f"ConsolidatedNavigator: Error checking quest reachability: {e}")

            self._fallback_searched = True
            return False

        except Exception as e:
            print(f"ConsolidatedNavigator: Error in fallback navigation: {e}")
            self._fallback_searched = True
            return False
    
    def _check_fallback_completion(self) -> bool:
        """
        Check if fallback navigation should be completed (original quest is now reachable).
        Fallback will *only* conclude when BOTH of the following hold:
          1. The player is on the same map as at least one coordinate of the
             original quest path.
          2. The player is within a small Manhattan distance (<= 10 tiles) of
             one of those on-map coordinates.
        This prevents premature quest switching that previously occurred
        immediately after a map transition, even when the player was still far
        from the original quest path, causing the coordinate index to reset
        and the navigator to oscillate between quests.
        """
        # REQUIREMENTS TO RUN THE CHECK
        if not self._fallback_mode or not self._original_quest_id:
            return False

        try:
            from pathlib import Path
            import json

            # Player's current global coordinate + map id
            x, y, map_id = self.env.get_game_coords()
            current_global = local_to_global(y, x, map_id)

            # ---------- LOAD ORIGINAL QUEST COORDINATES WITHOUT MUTATING STATE ----------
            quest_dir = f"{self._original_quest_id:03d}"
            quest_file = Path(__file__).parent / "quest_paths" / quest_dir / f"{quest_dir}_coords.json"

            if not quest_file.exists():
                return False  # Can't complete – file missing

            try:
                with open(quest_file, "r") as jf:
                    quest_json = json.load(jf)
            except Exception as e:
                print(f"ConsolidatedNavigator: Failed to read quest file {quest_file}: {e}")
                return False

            # Flatten coordinates while retaining global index + map ids
            full_coords: list[tuple] = []
            full_map_ids: list[int] = []
            for seg_key, seg_coords in quest_json.items():
                try:
                    seg_map_id = int(seg_key.split("_")[0])
                except ValueError:
                    seg_map_id = None
                for coord in seg_coords:
                    full_coords.append((coord[0], coord[1]))
                    full_map_ids.append(seg_map_id)

            # Identify the nearest coordinate on the *current* map
            nearest_idx = None
            nearest_dist = float("inf")
            for idx, (coord, c_map) in enumerate(zip(full_coords, full_map_ids)):
                if c_map != map_id:
                    continue
                dist = self._manhattan(current_global, coord)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_idx = idx

            MAX_COMPLETION_DISTANCE = 10  # tiles
            if nearest_idx is None or nearest_dist > MAX_COMPLETION_DISTANCE:
                # Still too far – keep following fallback quest
                return False

            # ---------- READY TO SWITCH BACK TO ORIGINAL QUEST ----------
            print(
                f"ConsolidatedNavigator: Fallback complete – within {nearest_dist} tiles of original quest {self._original_quest_id} (index {nearest_idx}). Switching back."
            )

            # Officially load the original quest path and resume
            if not self.load_coordinate_path(self._original_quest_id):
                # Unexpected failure – remain in fallback
                print("ConsolidatedNavigator: Unexpected failure loading original quest during fallback completion")
                return False

            # Align to the nearest coordinate we determined
            self.current_coordinate_index = nearest_idx
            self.active_quest_id = self._original_quest_id
            self.quest_locked = False
            self._fallback_mode = False
            self._original_quest_id = None
            self._fallback_searched = False
            return True
        except Exception as e:
            print(f"ConsolidatedNavigator: Error checking fallback completion: {e}")
            return False

    def _trigger_reload_coordinates_fallback(self) -> bool:
        """Reload coordinates fallback: Find nearest quest on current map and snap to nearest node"""
        try:
            cur_pos = self._get_player_global_coords()
            if not cur_pos:
                return False
                
            try:
                current_map = self.env.get_game_coords()[2]
            except:
                return False
            
            print(f"ConsolidatedNavigator: RELOAD COORDINATES - Player at {cur_pos} on map {current_map}")
            
            # Find the nearest quest that has coordinates on the current map
            best_quest_id = None
            best_distance = float('inf')
            
            for quest_id in range(1, 100):  # Check common quest range
                quest_dir = f"{quest_id:03d}"
                quest_file = Path(__file__).parent / "quest_paths" / quest_dir / f"{quest_dir}_coords.json"
                
                if not quest_file.exists():
                    continue
                    
                try:
                    with open(quest_file, 'r') as f:
                        quest_data = json.load(f)
                    
                    # Check if this quest has coordinates on current map
                    has_current_map = False
                    nearest_coord = None
                    nearest_distance = float('inf')
                    
                    for segment_key, segment_coords in quest_data.items():
                        if '_' in segment_key:
                            map_id = int(segment_key.split('_')[0])
                        else:
                            map_id = int(segment_key)
                        
                        if map_id == current_map:
                            has_current_map = True
                            for coord in segment_coords:
                                coord_tuple = (coord[0], coord[1])
                                distance = self._manhattan(cur_pos, coord_tuple)
                                if distance < nearest_distance:
                                    nearest_distance = distance
                                    nearest_coord = coord_tuple
                    
                    if has_current_map and nearest_distance < best_distance:
                        best_distance = nearest_distance
                        best_quest_id = quest_id
                        
                except Exception as e:
                    continue
            
            if best_quest_id:
                print(f"ConsolidatedNavigator: RELOAD COORDINATES - Loading quest {best_quest_id} (distance: {best_distance})")
                self.debug_logger.log_fallback_trigger(
                    trigger_type="RELOAD_COORDINATES_SUCCESS",
                    failure_count=self.movement_failure_count,
                    details={
                        'selected_quest': best_quest_id,
                        'distance_to_nearest': best_distance,
                        'current_map': current_map,
                        'player_pos': cur_pos
                    }
                )
                
                # Load the quest and snap to nearest coordinate
                if self.load_coordinate_path(best_quest_id):
                    # Use improved node selection logic
                    return self._snap_to_nearest_on_current_map()
            
            return False
            
        except Exception as e:
            print(f"ConsolidatedNavigator: RELOAD COORDINATES fallback error: {e}")
            return False
    
    def _trigger_astar_fallback(self) -> bool:
        """A* fallback: Generate A* path to target coordinate"""
        try:
            cur_pos = self._get_player_global_coords()
            if not cur_pos:
                return False
                
            if (not self.sequential_coordinates or 
                self.current_coordinate_index >= len(self.sequential_coordinates)):
                return False
            
            target_coord = self.sequential_coordinates[self.current_coordinate_index]
            
            print(f"ConsolidatedNavigator: A* FALLBACK - From {cur_pos} to {target_coord}")
            
            # Generate A* path
            path = self.astar_navigator.find_path(cur_pos, target_coord, max_distance=20)
            
            if path and len(path) > 1:
                self.astar_path = path[1:]  # Exclude starting position
                self.astar_path_index = 0
                self.navigation_status = "astar_active"
                
                self.debug_logger.log_fallback_trigger(
                    trigger_type="ASTAR_FALLBACK_SUCCESS",
                    failure_count=self.astar_failure_count,
                    details={
                        'start_pos': cur_pos,
                        'target_coord': target_coord,
                        'path_length': len(self.astar_path),
                        'path_preview': self.astar_path[:5]  # First 5 steps
                    }
                )
                
                print(f"ConsolidatedNavigator: A* path generated: {len(self.astar_path)} steps")
                return True
            else:
                print(f"ConsolidatedNavigator: A* path generation failed")
                return False
                
        except Exception as e:
            print(f"ConsolidatedNavigator: A* fallback error: {e}")
            return False
    
    def _snap_to_nearest_on_current_map(self) -> bool:
        """Improved snap logic: Find nearest node on current map ID"""
        try:
            cur_pos = self._get_player_global_coords()
            if not cur_pos or not self.sequential_coordinates:
                return False
                
            try:
                current_map = self.env.get_game_coords()[2]
            except:
                return False
            
            # Find nearest coordinate that's on the current map
            best_index = None
            best_distance = float('inf')
            
            for i, coord in enumerate(self.sequential_coordinates):
                # Check if this coordinate is on the current map
                if i < len(self.coord_map_ids) and self.coord_map_ids[i] == current_map:
                    distance = self._manhattan(cur_pos, coord)
                    if distance < best_distance:
                        best_distance = distance
                        best_index = i
            
            if best_index is not None:
                old_index = self.current_coordinate_index
                self.current_coordinate_index = best_index
                
                self.debug_logger.log_node_selection(
                    method="SNAP_TO_NEAREST_ON_MAP",
                    player_pos=cur_pos,
                    selected_node=self.sequential_coordinates[best_index],
                    selected_index=best_index,
                    distance=best_distance
                )
                
                print(f"ConsolidatedNavigator: Snapped from index {old_index} to {best_index} (distance: {best_distance}) on map {current_map}")
                return True
            else:
                print(f"ConsolidatedNavigator: No coordinates found on current map {current_map}")
                return False
                
        except Exception as e:
            print(f"ConsolidatedNavigator: Snap to nearest on current map error: {e}")
            return False

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
