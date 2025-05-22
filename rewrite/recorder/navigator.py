# navigator.py
import numpy as np
import json
import heapq
import math
from pathlib import Path
from typing import Optional, Union, List, Tuple
from data.map import MapIds
from environment import RedGymEnv
from pyboy.utils import WindowEvent
from global_map import local_to_global, global_to_local 
from data.tilesets import Tilesets 
from data.warps import WARP_DICT

# Define availability flags for warp data and map IDs
MAP_IDS_AVAILABLE = True
WARP_DATA_AVAILABLE = True

class InteractiveNavigator:
    def __init__(self, env_instance: RedGymEnv):
        self.env = env_instance
        self.pyboy = self.env.pyboy  # Direct access to pyboy via RedGymEnv
        self.tile_pair_collisions = {}
        
        # NEW SIMPLIFIED COORDINATE SYSTEM
        self.sequential_coordinates: List[Tuple[int, int]] = []  # Single list of all coordinates in sequence
        self.current_coordinate_index: int = 0  # Current position in the sequence
        
        # Keep existing systems for compatibility but simplify usage
        self.warp_path_data: dict[str, list[list[tuple[int,int]]]] = {}
        self._load_tile_pair_collisions()
        self.current_quest_id: Optional[int] = None
        self._load_all_recorded_paths_simplified()  # New simplified loader
        
        # Track last global position including map to detect changes
        self.last_position = None
        
        # Action mapping for navigation paths (UP, DOWN, LEFT, RIGHT strings)
        self.ACTION_MAPPING_STR_TO_INT = {
            "down": 0, "left": 1, "right": 2, "up": 3,
        }

        self.current_navigation_target_local_grid: Optional[tuple[int, int]] = None # (row, col) on 9x10 grid
        self.current_navigation_target_global: Optional[tuple[int, int]] = None # (gx, gy) global coords
        self.current_path_actions: list[str] = [] 
        self.navigation_status: str = "idle" # idle, planning, navigating, completed, failed
        self.last_failed_astar_target: Optional[tuple[int,int]] = None # For loop detection

        # For JSON path following and stagnation detection
        self.last_player_gx_on_json_path_advance: Optional[int] = None
        self.last_player_gy_on_json_path_advance: Optional[int] = None
        self.current_map_id_for_json_progress: Optional[str] = None
        self.current_json_path_target_idx: int = 0 # Index of the target coord in the current map's JSON path
        self.turns_since_json_path_advance: int = 0
        self.STAGNATION_THRESHOLD: int = 3

        # For A* segment loop detection (when A* path executes but player doesn't move globally)
        self.last_global_pos_before_astar_segment: Optional[tuple[int, int]] = None
        self.astar_segment_no_global_progress_count: int = 0
        self.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD: int = 2 # Allow one full A* segment retry with no global progress
        self.global_nav_short_history: list[tuple[int, int]] = [] # For detecting oscillations

        # Add warp handling attributes
        self.current_warp_segment: Optional[list[tuple[int,int]]] = None  # Warp path segment for map change transitions
        self.current_warp_index: int = 0  # Index within the warp path segment
        self.last_map_id: Optional[str] = None  # Track last map ID to detect map changes

        self.follow_path_steps_remaining: int = 0 # New attribute for follow_path

    def _load_tile_pair_collisions(self):
        # This path is relative to where play.py is run (project root)
        collisions_file_path_project_root = Path("./tile_pair_collisions.json") 
        # This path is relative to this navigator.py file
        collisions_file_path_local_dir = Path(__file__).parent / "tile_pair_collisions.json"
        
        chosen_path = None
        if collisions_file_path_project_root.exists():
            chosen_path = collisions_file_path_project_root
        elif collisions_file_path_local_dir.exists():
            chosen_path = collisions_file_path_local_dir
            
        if chosen_path:
            try:
                with open(chosen_path, 'r') as f:
                    self.tile_pair_collisions = json.load(f)
                print(f"InteractiveNavigator: Loaded {chosen_path} with {len(self.tile_pair_collisions)} tilesets defined.")
            except Exception as e:
                print(f"InteractiveNavigator: Error loading or parsing {chosen_path}: {e}")
                self.tile_pair_collisions = {}
        else:
            self.tile_pair_collisions = {}
            print(f"InteractiveNavigator: Warning: Tile pair collision file not found at {collisions_file_path_project_root} or {collisions_file_path_local_dir}. Tile pair collision checking will be permissive.")

    def _load_all_recorded_paths_simplified(self):
        """Load all coordinate paths and create a single sequential coordinate list"""
        self.warp_path_data = {} # Initialize/clear existing data
        self.sequential_coordinates = []
        
        base_dir = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
        if not base_dir.exists():
            print(f"Navigator: All recorded paths directory not found at {base_dir}")
            return
            
        # Collect all coordinate files and sort them numerically
        coord_files = []
        for quest_dir in base_dir.iterdir():
            if not quest_dir.is_dir():
                continue
            coords_file = quest_dir / f"{quest_dir.name}_coords.json"
            if coords_file.exists():
                try:
                    quest_id = int(quest_dir.name)
                    coord_files.append((quest_id, coords_file))
                except ValueError:
                    continue
        
        # Sort by quest ID to maintain order
        coord_files.sort(key=lambda x: x[0])
        
        total_coordinates_loaded = 0
        
        for quest_id, coords_file in coord_files:
            try:
                with open(coords_file, 'r') as f:
                    raw_quest_paths = json.load(f)
                
                # Process each map's coordinates in the quest
                for map_id_str, path_coords_list in raw_quest_paths.items():
                    if isinstance(path_coords_list, list):
                        # Add all coordinates from this map to the sequential list
                        for coord in path_coords_list:
                            if isinstance(coord, list) and len(coord) == 2:
                                self.sequential_coordinates.append(tuple(coord))
                                total_coordinates_loaded += 1
                        
                        # Also maintain the old structure for compatibility
                        if map_id_str not in self.warp_path_data:
                            self.warp_path_data[map_id_str] = []
                        current_segment = [tuple(coord) for coord in path_coords_list if isinstance(coord, list) and len(coord) == 2]
                        if current_segment:
                            self.warp_path_data[map_id_str].append(current_segment)
                            
            except Exception as e:
                print(f"Navigator: Failed to load/process recorded path from {coords_file}: {e}")
        
        print(f"Navigator: Loaded {total_coordinates_loaded} sequential coordinates from {len(coord_files)} quest files.")
        if self.sequential_coordinates:
            print(f"Navigator: First coordinate: {self.sequential_coordinates[0]}, Last coordinate: {self.sequential_coordinates[-1]}")

    def _get_player_global_coords(self) -> Optional[tuple[int, int]]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: env object does not have get_game_coords method.")
            return None
        try:
            player_local_x, player_local_y, current_map_id_int = self.env.get_game_coords()
            global_y, global_x = local_to_global(player_local_y, player_local_x, current_map_id_int)
            # Only print when the location (including map) changes
            coord = (int(global_y), int(global_x), current_map_id_int)
            if self.last_position is None or coord != self.last_position:
                print(f"Navigator: Location changed to global {coord}")
            self.last_position = coord
            # Return (gy, gx) to match internal usage - NOTE: This matches coordinate file format
            return int(global_y), int(global_x)
        except Exception as e:
            print(f"InteractiveNavigator: Error getting player global coords: {e}")
            return None

    def _manhattan_distance(self, pos1: tuple[int, int], pos2: tuple[int, int]) -> int:
        return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])

    def snap_to_nearest_coordinate(self) -> bool:
        """Snap player to the nearest coordinate in the sequential path and update index"""
        if not self.sequential_coordinates:
            print("Navigator: No sequential coordinates loaded to snap to.")
            return False
            
        current_pos = self._get_player_global_coords()
        if not current_pos:
            print("Navigator: Cannot get current player position for snapping.")
            return False
            
        # Find the nearest coordinate in the sequential list
        min_distance = float('inf')
        nearest_index = 0
        
        for i, coord in enumerate(self.sequential_coordinates):
            distance = self._manhattan_distance(current_pos, coord)
            if distance < min_distance:
                min_distance = distance
                nearest_index = i
                
        nearest_coord = self.sequential_coordinates[nearest_index]
        self.current_coordinate_index = nearest_index
        
        print(f"Navigator: Snapping to nearest coordinate {nearest_coord} at index {nearest_index} (distance: {min_distance})")
        
        # Move player to this coordinate
        return self._move_player_to_coordinate(nearest_coord)

    def move_to_next_coordinate(self) -> bool:
        """Move player to the next coordinate in the sequential path with enhanced error handling"""
        if not self.sequential_coordinates:
            print("Navigator: No sequential coordinates loaded.")
            return False
            
        # First ensure we're at a valid position in the sequence
        current_pos = self._get_player_global_coords()
        if not current_pos:
            print("Navigator: Cannot get current player position.")
            return False
            
        # Check if we need to snap to path first
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            print("Navigator: At end of coordinate sequence.")
            return False
            
        current_expected_coord = self.sequential_coordinates[self.current_coordinate_index]
        
        # Enhanced coordinate validation - if we're not at expected coordinate, try to recover
        if current_pos != current_expected_coord:
            distance_to_expected = self._manhattan_distance(current_pos, current_expected_coord)
            
            # If we're close to expected coordinate, just advance index
            if distance_to_expected <= 3:
                print(f"Navigator: Close to expected coordinate (distance: {distance_to_expected}), advancing index.")
                if self.current_coordinate_index + 1 < len(self.sequential_coordinates):
                    self.current_coordinate_index += 1
                    next_coord = self.sequential_coordinates[self.current_coordinate_index]
                    print(f"Navigator: Moving to next coordinate {next_coord} at index {self.current_coordinate_index}")
                    return self._move_player_to_coordinate(next_coord)
                else:
                    print("Navigator: Advanced to end of sequence.")
                    return False
            
            # If we're far from expected coordinate, try to find nearest coordinate in sequence
            elif distance_to_expected > 3:
                print(f"Navigator: Far from expected coordinate (distance: {distance_to_expected}). Finding nearest coordinate in sequence.")
                return self._recover_to_nearest_coordinate_and_advance()
        
        # Move to next coordinate (normal case)
        if self.current_coordinate_index + 1 < len(self.sequential_coordinates):
            self.current_coordinate_index += 1
            next_coord = self.sequential_coordinates[self.current_coordinate_index]
            print(f"Navigator: Moving to next coordinate {next_coord} at index {self.current_coordinate_index}")
            return self._move_player_to_coordinate(next_coord)
        else:
            print("Navigator: Already at the last coordinate in the sequence.")
            return False

    def _recover_to_nearest_coordinate_and_advance(self) -> bool:
        """Recovery mechanism: find nearest coordinate in sequence and advance from there"""
        current_pos = self._get_player_global_coords()
        if not current_pos:
            return False
            
        # Look for nearest coordinate within reasonable range of current index
        search_start = max(0, self.current_coordinate_index - 5)
        search_end = min(len(self.sequential_coordinates), self.current_coordinate_index + 10)
        
        min_distance = float('inf')
        best_index = self.current_coordinate_index
        
        for i in range(search_start, search_end):
            coord = self.sequential_coordinates[i]
            distance = self._manhattan_distance(current_pos, coord)
            if distance < min_distance:
                min_distance = distance
                best_index = i
                
        print(f"Navigator: Recovery found nearest coordinate at index {best_index} (distance: {min_distance})")
        self.current_coordinate_index = best_index
        
        # Now try to advance to next coordinate
        if self.current_coordinate_index + 1 < len(self.sequential_coordinates):
            self.current_coordinate_index += 1
            next_coord = self.sequential_coordinates[self.current_coordinate_index]
            print(f"Navigator: Recovery advancing to coordinate {next_coord} at index {self.current_coordinate_index}")
            return self._move_player_to_coordinate(next_coord)
        else:
            print("Navigator: Recovery reached end of sequence.")
            return False

    def _get_current_map_id(self) -> Optional[str]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: get_game_coords method not available on env.")
            return None
        try:
            _, _, map_n = self.env.get_game_coords()
            # Return map ID as string (un-padded) to match JSON keys
            return str(map_n)
        except Exception as e:
            print(f"InteractiveNavigator: Error getting current map ID via env: {e}")
            return None

    def _move_player_to_coordinate(self, target_coord: tuple[int, int]) -> bool:
        """Move player to the specified global coordinate with enhanced warp detection"""
        try:
            target_gy, target_gx = target_coord
            print(f"Navigator: Attempting to move to global coordinate {target_coord}")
            
            # Check if player is currently on a warp
            is_on_warp, warp_info = self._is_player_on_warp()
            if is_on_warp:
                print(f"Navigator: Player is currently on a warp tile. Manual navigation required to traverse warp.")
                print(f"Navigator: Warp leads to {warp_info.get('target_map_name', 'unknown')} (ID: {warp_info.get('target_map_id', 'unknown')})")
                return False
            
            # Check if target coordinate is on a warp
            is_target_warp, target_warp_info = self._is_coordinate_on_warp(target_gy, target_gx)
            if is_target_warp:
                print(f"Navigator: Target coordinate {target_coord} is a warp tile. Stopping navigation before warp.")
                print(f"Navigator: Target warp leads to {target_warp_info.get('target_map_name', 'unknown')} (ID: {target_warp_info.get('target_map_id', 'unknown')})")
                
                # Move to position adjacent to warp instead of on the warp
                adjacent_coord = self._get_adjacent_to_warp(target_gy, target_gx)
                if adjacent_coord:
                    target_gy, target_gx = adjacent_coord
                    print(f"Navigator: Redirecting to adjacent coordinate {adjacent_coord} to avoid automatic warp traversal.")
                else:
                    print(f"Navigator: Could not find suitable adjacent position to warp at {target_coord}")
                    return False
            
            # Get initial position
            initial_global_pos = self._get_player_global_coords()
            if not initial_global_pos:
                print("Navigator: Cannot get initial player position")
                return False
                
            initial_gy, initial_gx = initial_global_pos
            
            # Check if already at target
            if (initial_gy, initial_gx) == (target_gy, target_gx):
                print(f"Navigator: Already at target coordinate {target_coord}")
                return True
            
            # Try intelligent movement with map transition support
            return self._intelligent_move_to_target(target_gy, target_gx, max_attempts=75)
                
        except Exception as e:
            print(f"Navigator: Error moving player to coordinate {target_coord}: {e}")
            return False

    def _get_adjacent_to_warp(self, warp_gy: int, warp_gx: int) -> Optional[tuple[int, int]]:
        """Find a suitable adjacent coordinate to a warp tile"""
        # Try coordinates adjacent to the warp (up, down, left, right)
        adjacent_candidates = [
            (warp_gy - 1, warp_gx),  # Up
            (warp_gy + 1, warp_gx),  # Down  
            (warp_gy, warp_gx - 1),  # Left
            (warp_gy, warp_gx + 1)   # Right
        ]
        
        current_pos = self._get_player_global_coords()
        if not current_pos:
            return None
            
        current_gy, current_gx = current_pos
        
        # Prefer the adjacent position closest to current player position
        best_adjacent = None
        min_distance = float('inf')
        
        for adj_gy, adj_gx in adjacent_candidates:
            # Check if this adjacent position is also a warp (avoid warp-to-warp scenarios)
            is_adj_warp, _ = self._is_coordinate_on_warp(adj_gy, adj_gx)
            if is_adj_warp:
                continue
                
            distance = self._manhattan_distance((current_gy, current_gx), (adj_gy, adj_gx))
            if distance < min_distance:
                min_distance = distance
                best_adjacent = (adj_gy, adj_gx)
        
        return best_adjacent

    def _intelligent_move_to_target(self, target_gy: int, target_gx: int, max_attempts: int = 75) -> bool:
        """Intelligent movement that handles map transitions and gets unstuck with warp detection"""
        attempts = 0
        last_position = None
        stuck_counter = 0
        alternative_moves = 0
        oscillation_positions = []  # Track positions for oscillation detection
        
        # Early detection for cross-map targets
        if not self._is_target_on_current_map(target_gy, target_gx):
            print(f"Navigator: Target ({target_gy}, {target_gx}) appears to be on different map. Attempting cross-map navigation.")
            return self._attempt_cross_map_navigation(target_gy, target_gx)
        
        while attempts < max_attempts:
            attempts += 1
            
            # Check if player is on a warp before attempting movement
            is_on_warp, warp_info = self._is_player_on_warp()
            if is_on_warp:
                print(f"Navigator: Player is on warp tile during navigation. Stopping automatic movement.")
                print(f"Navigator: Manual input required to traverse warp to {warp_info.get('target_map_name', 'unknown')}")
                return False
                
            current_global_pos = self._get_player_global_coords()
            
            if not current_global_pos:
                print("Navigator: Lost player position during movement")
                return False
                
            current_gy, current_gx = current_global_pos
            
            # Check if we've reached the target
            if (current_gy, current_gx) == (target_gy, target_gx):
                print(f"Navigator: Successfully reached target ({target_gy}, {target_gx}) in {attempts} moves")
                return True
            
            # Enhanced oscillation detection
            oscillation_positions.append((current_gy, current_gx))
            if len(oscillation_positions) > 6:
                oscillation_positions.pop(0)
                
            # Check for oscillation pattern (going back and forth between same positions)
            if len(oscillation_positions) >= 6:
                unique_positions = set(oscillation_positions)
                if len(unique_positions) <= 2:  # Only 2 unique positions in last 6 moves = oscillation
                    print(f"Navigator: Detected oscillation pattern in positions {unique_positions}")
                    return False
            
            # Detect if we're stuck (same position for multiple attempts)
            if last_position == (current_gy, current_gx):
                stuck_counter += 1
                if stuck_counter >= 3:  # Stuck for 3+ moves
                    print(f"Navigator: Detected stuck at {current_global_pos}, trying alternative movement")
                    if not self._try_alternative_movement(target_gy, target_gx):
                        print(f"Navigator: All alternative movements failed, target may be unreachable")
                        return False
                    alternative_moves += 1
                    stuck_counter = 0  # Reset counter after alternative move
                    if alternative_moves >= 3:  # Reduced threshold for alternative moves
                        print(f"Navigator: Too many alternative movements, target likely unreachable")
                        return False
            else:
                stuck_counter = 0  # Reset counter when we move
                alternative_moves = 0  # Reset alternative moves when making progress
            
            last_position = (current_gy, current_gx)
            
            # Calculate direction to target
            delta_gy = target_gy - current_gy
            delta_gx = target_gx - current_gx
            
            # Enhanced distance check - if we're not getting closer, abort
            if attempts > 20:  # After reasonable attempt count
                initial_distance = abs(target_gy - current_gy) + abs(target_gx - current_gx)
                if initial_distance > 15:  # If still far from target
                    print(f"Navigator: Target appears unreachable. Distance: {initial_distance}")
                    return False
            
            # Choose movement direction (prioritize larger delta)
            action_to_take = None
            if abs(delta_gy) >= abs(delta_gx):
                if delta_gy > 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["down"]
                elif delta_gy < 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["up"]
            else:
                if delta_gx > 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["right"]
                elif delta_gx < 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["left"]
            
            if action_to_take is not None:
                # Execute the move
                obs, reward, terminated, truncated, info = self.env.step(action_to_take)
                
                # Check if map changed during movement
                new_global_pos = self._get_player_global_coords()
                if new_global_pos:
                    new_gy, new_gx = new_global_pos
                    if abs(new_gy - current_gy) > 20 or abs(new_gx - current_gx) > 20:
                        print(f"Navigator: Detected map transition from {current_global_pos} to {new_global_pos}")
                        # Continue with new position
                
                # Small delay to allow map transitions to process
                if attempts % 10 == 0:  # Every 10 moves, give a brief status
                    distance = abs(target_gy - current_gy) + abs(target_gx - current_gx)
                    print(f"Navigator: Move {attempts}/{max_attempts}, distance to target: {distance}")
            else:
                print(f"Navigator: No valid action determined for target ({target_gy}, {target_gx})")
                return False
        
        # Final check
        final_pos = self._get_player_global_coords()
        if final_pos and final_pos == (target_gy, target_gx):
            print(f"Navigator: Reached target on final check")
            return True
        
        print(f"Navigator: Failed to reach target ({target_gy}, {target_gx}) after {max_attempts} attempts. Final position: {final_pos}")
        return False

    def _is_target_on_current_map(self, target_gy: int, target_gx: int) -> bool:
        """Check if target coordinates are reachable on the current map"""
        try:
            current_map_id = self._get_current_map_id()
            if not current_map_id:
                return False
                
            # Try to convert target global coordinates to local coordinates for current map
            target_local_coords = global_to_local(target_gy, target_gx, int(current_map_id))
            return target_local_coords is not None
        except Exception as e:
            print(f"Navigator: Error checking if target is on current map: {e}")
            return False

    def _attempt_cross_map_navigation(self, target_gy: int, target_gx: int) -> bool:
        """Attempt to navigate toward a different map to reach target coordinates"""
        print(f"Navigator: Attempting cross-map navigation to reach ({target_gy}, {target_gx})")
        
        # Get current position
        current_pos = self._get_player_global_coords()
        if not current_pos:
            return False
            
        current_gy, current_gx = current_pos
        
        # For cross-map navigation, move toward the general direction of the target
        # This should eventually trigger map transitions
        delta_gy = target_gy - current_gy  
        delta_gx = target_gx - current_gx
        
        # Try to move in the general direction for a limited number of attempts
        max_cross_map_attempts = 20
        for attempt in range(max_cross_map_attempts):
            # Choose direction based on larger delta
            action_to_take = None
            if abs(delta_gy) >= abs(delta_gx):
                if delta_gy > 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["down"]
                elif delta_gy < 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["up"]
            else:
                if delta_gx > 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["right"]
                elif delta_gx < 0:
                    action_to_take = self.ACTION_MAPPING_STR_TO_INT["left"]
            
            if action_to_take is not None:
                # Execute movement
                obs, reward, terminated, truncated, info = self.env.step(action_to_take)
                
                # Check new position
                new_pos = self._get_player_global_coords()
                if not new_pos:
                    continue
                    
                new_gy, new_gx = new_pos
                
                # Check if we've reached the target after map transition
                if (new_gy, new_gx) == (target_gy, target_gx):
                    print(f"Navigator: Successfully reached cross-map target ({target_gy}, {target_gx})")
                    return True
                
                # Check if target is now on current map after transition
                if self._is_target_on_current_map(target_gy, target_gx):
                    print(f"Navigator: Target now accessible on current map after transition")
                    # Use normal navigation to reach target
                    return self._intelligent_move_to_target(target_gy, target_gx, 30)
                
                # Update deltas for next iteration
                delta_gy = target_gy - new_gy
                delta_gx = target_gx - new_gx
                
                # If we got significantly closer, continue
                new_distance = abs(delta_gy) + abs(delta_gx)
                if attempt > 0:  # After first attempt
                    prev_distance = abs(target_gy - current_gy) + abs(target_gx - current_gx)
                    if new_distance >= prev_distance:  # Not getting closer
                        break
                
                current_gy, current_gx = new_gy, new_gx
            else:
                break
                
        print(f"Navigator: Cross-map navigation failed to reach target ({target_gy}, {target_gx})")
        return False

    def _try_alternative_movement(self, target_gy: int, target_gx: int) -> bool:
        """Try alternative movements when stuck (diagonal, backward, etc.)"""
        current_pos = self._get_player_global_coords()
        if not current_pos:
            return False
            
        current_gy, current_gx = current_pos
        
        # Calculate direction to target for smarter alternative movement
        delta_gy = target_gy - current_gy
        delta_gx = target_gx - current_gx
        
        # Try movements in order of preference (toward target first, then others)
        preferred_directions = []
        
        # Add target-directed movements first
        if delta_gy > 0:
            preferred_directions.append((self.ACTION_MAPPING_STR_TO_INT["down"], "down"))
        elif delta_gy < 0:
            preferred_directions.append((self.ACTION_MAPPING_STR_TO_INT["up"], "up"))
            
        if delta_gx > 0:
            preferred_directions.append((self.ACTION_MAPPING_STR_TO_INT["right"], "right"))
        elif delta_gx < 0:
            preferred_directions.append((self.ACTION_MAPPING_STR_TO_INT["left"], "left"))
        
        # Add remaining directions
        all_directions = [
            (self.ACTION_MAPPING_STR_TO_INT["up"], "up"),
            (self.ACTION_MAPPING_STR_TO_INT["down"], "down"), 
            (self.ACTION_MAPPING_STR_TO_INT["left"], "left"),
            (self.ACTION_MAPPING_STR_TO_INT["right"], "right")
        ]
        
        for action_int, direction_name in all_directions:
            if (action_int, direction_name) not in preferred_directions:
                preferred_directions.append((action_int, direction_name))
        
        # Try each direction
        for action_int, direction_name in preferred_directions:
            print(f"Navigator: Trying alternative movement: {direction_name}")
            obs, reward, terminated, truncated, info = self.env.step(action_int)
            
            new_pos = self._get_player_global_coords()
            if new_pos and new_pos != current_pos:
                print(f"Navigator: Alternative movement {direction_name} successful, moved to {new_pos}")
                return True
        
        print(f"Navigator: All alternative movements failed")
        return False

    # Keep existing methods for compatibility but mark them as deprecated
    def _get_map_name_from_id(self, map_id: int) -> Optional[str]:
        """Convert map ID to map name for warp lookup using MapIds enum"""
        if not MAP_IDS_AVAILABLE:
            # Fallback to minimal hardcoded mapping if MapIds enum unavailable
            fallback_map_names = {
                0: 'PALLET_TOWN',
                1: 'VIRIDIAN_CITY', 
                2: 'PEWTER_CITY',
                3: 'CERULEAN_CITY',
                4: 'LAVENDER_TOWN',
                5: 'VERMILION_CITY',
                6: 'CELADON_CITY',
                7: 'FUCHSIA_CITY',
                8: 'CINNABAR_ISLAND',
                9: 'INDIGO_PLATEAU',
                10: 'SAFFRON_CITY',
                40: 'OAKS_LAB',
            }
            return fallback_map_names.get(map_id)
        
        try:
            # Use MapIds enum for comprehensive map ID to name conversion
            for map_enum in MapIds:
                if map_enum.value == map_id:
                    return map_enum.name
            
            # Return None if map ID not found in enum
            return None
            
        except Exception as e:
            print(f"Navigator: Error converting map ID {map_id} to name: {e}")
            return None


    def _find_adjacent_warp_for_navigation(self) -> tuple[bool, Optional[dict], Optional[int]]:
        """Find adjacent warp that should be traversed for navigation, with intelligent filtering"""
        if not WARP_DATA_AVAILABLE:
            return False, None, None
            
        try:
            # Get current player position and map
            player_local_x, player_local_y, current_map_id = self.env.get_game_coords()
            map_name = self._get_map_name_from_id(current_map_id)
            
            if not map_name or map_name not in WARP_DICT:
                return False, None, None
            
            # Get next coordinate in sequence to determine if warp traversal is needed
            next_coord_needed = None
            if (self.current_coordinate_index + 1 < len(self.sequential_coordinates)):
                next_coord_needed = self.sequential_coordinates[self.current_coordinate_index + 1]
            
            # Check all adjacent positions for warps (up, down, left, right)
            adjacent_checks = [
                (player_local_x, player_local_y - 1, 4),   # UP (direction 4)
                (player_local_x, player_local_y + 1, 0),   # DOWN (direction 0)  
                (player_local_x - 1, player_local_y, 8),   # LEFT (direction 8)
                (player_local_x + 1, player_local_y, 12)   # RIGHT (direction 12)
            ]
            
            print(f"Navigator: Checking adjacent tiles for warps around player at ({player_local_x}, {player_local_y})")
            
            # Check each adjacent position
            for check_x, check_y, required_direction in adjacent_checks:
                for warp in WARP_DICT[map_name]:
                    if warp['x'] == check_x and warp['y'] == check_y:
                        direction_name = {4: "UP", 0: "DOWN", 8: "LEFT", 12: "RIGHT"}[required_direction]
                        warp_target = warp['target_map_name']
                        
                        print(f"Navigator: Found adjacent warp at ({check_x}, {check_y}) - {warp_target} - requires {direction_name} movement")
                        
                        # Intelligence layer: Determine if this warp should be traversed
                        should_traverse = self._should_traverse_warp_for_navigation(warp, current_map_id, next_coord_needed)
                        
                        if should_traverse:
                            print(f"Navigator: Warp traversal recommended for navigation progress")
                            return True, warp, required_direction
                        else:
                            print(f"Navigator: Warp traversal not needed for current navigation objective")
            
            print(f"Navigator: No navigation-relevant adjacent warps found")
            return False, None, None
            
        except Exception as e:
            print(f"Navigator: Error checking adjacent warps: {e}")
            return False, None, None

    def _should_traverse_warp_for_navigation(self, warp: dict, current_map_id: int, next_coord: Optional[tuple[int, int]]) -> bool:
        """Determine if warp traversal is beneficial for navigation progress"""
        try:
            warp_target = warp.get('target_map_name', '')
            
            # Skip LAST_MAP warps unless we have a specific need to exit
            if warp_target == 'LAST_MAP':
                # Only traverse LAST_MAP warps if next coordinate requires different map
                if next_coord:
                    next_gy, next_gx = next_coord
                    # Check if next coordinate is accessible on current map
                    try:
                        from global_map import global_to_local
                        next_local = global_to_local(next_gy, next_gx, current_map_id)
                        if next_local is not None:
                            print(f"Navigator: Next coordinate {next_coord} accessible on current map - no exit needed")
                            return False
                        else:
                            print(f"Navigator: Next coordinate {next_coord} requires map change - exit warp traversal needed")
                            return True
                    except Exception:
                        # If coordinate conversion fails, assume map change needed
                        print(f"Navigator: Cannot determine coordinate accessibility - assuming exit needed")
                        return True
                else:
                    print(f"Navigator: No next coordinate available - skipping exit warp")
                    return False
            
            # For other warps, check if target map differs from current
            warp_target_id = warp.get('target_map_id')
            if warp_target_id and warp_target_id != current_map_id:
                print(f"Navigator: Warp leads to different map ({warp_target_id} vs {current_map_id}) - traversal beneficial")
                return True
            
            print(f"Navigator: Warp traversal not beneficial for navigation")
            return False
            
        except Exception as e:
            print(f"Navigator: Error determining warp traversal necessity: {e}")
            return False
    
    # def _find_adjacent_warp_for_navigation(self) -> tuple[bool, Optional[dict], Optional[int]]:
    #     """Find any adjacent warp that should be traversed for navigation, returning warp info and direction needed"""
    #     if not WARP_DATA_AVAILABLE:
    #         return False, None, None
            
    #     try:
    #         # Get current player position and map
    #         player_local_x, player_local_y, current_map_id = self.env.get_game_coords()
    #         map_name = self._get_map_name_from_id(current_map_id)
            
    #         if not map_name or map_name not in WARP_DICT:
    #             return False, None, None
            
    #         # Check all adjacent positions for warps (up, down, left, right)
    #         adjacent_checks = [
    #             (player_local_x, player_local_y - 1, 4),   # UP (direction 4)
    #             (player_local_x, player_local_y + 1, 0),   # DOWN (direction 0)  
    #             (player_local_x - 1, player_local_y, 8),   # LEFT (direction 8)
    #             (player_local_x + 1, player_local_y, 12)   # RIGHT (direction 12)
    #         ]
            
    #         print(f"Navigator: Checking adjacent tiles for warps around player at ({player_local_x}, {player_local_y})")
            
    #         # Check each adjacent position
    #         for check_x, check_y, required_direction in adjacent_checks:
    #             for warp in WARP_DICT[map_name]:
    #                 if warp['x'] == check_x and warp['y'] == check_y:
    #                     direction_name = {4: "UP", 0: "DOWN", 8: "LEFT", 12: "RIGHT"}[required_direction]
    #                     print(f"Navigator: Found adjacent warp at ({check_x}, {check_y}) - {warp['target_map_name']} - requires {direction_name} movement")
    #                     return True, warp, required_direction
            
    #         print(f"Navigator: No adjacent warps found around player position")
    #         return False, None, None
            
    #     except Exception as e:
    #         print(f"Navigator: Error checking adjacent warps: {e}")
    #         return False, None, None
    
    def _is_player_on_warp(self) -> tuple[bool, Optional[dict]]:
        """Check if player is currently standing on a warp tile"""
        if not WARP_DATA_AVAILABLE:
            return False, None
            
        try:
            # Get current player position and map
            player_local_x, player_local_y, current_map_id = self.env.get_game_coords()
            # Determine facing direction and compute the tile in front
            direction = self.env.read_m("wSpritePlayerStateData1FacingDirection")
            front_local_x, front_local_y = player_local_x, player_local_y
            if direction == 0:
                front_local_y += 1
            elif direction == 4:
                front_local_y -= 1
            elif direction == 8:
                front_local_x -= 1
            elif direction == 12:
                front_local_x += 1
            print(f"Navigator: Player local coords: ({player_local_x}, {player_local_y}), facing: {direction}, front tile: ({front_local_x}, {front_local_y})")
            map_name = self._get_map_name_from_id(current_map_id)
            print(f"Navigator: Current map name: {map_name}")
            
            if not map_name or map_name not in WARP_DICT:
                print(f"Navigator: Current map name not in WARP_DICT: {map_name}")
                return False, None
            
            # Check all warps on current map
            for warp in WARP_DICT[map_name]:
                print(f"Navigator: Checking warp: {warp}")
                # Warp under player
                if warp['x'] == player_local_x and warp['y'] == player_local_y:
                    print(f"Navigator: Found matching warp under player: {warp}")
                    return True, warp
                # Warp in front of player
                if warp['x'] == front_local_x and warp['y'] == front_local_y:
                    print(f"Navigator: Found warp in front of player: {warp}")
                    return True, warp
            
            print(f"Navigator: No matching warp found")
            return False, None
            
        except Exception as e:
            print(f"Navigator: Error checking warp status: {e}")
            return False, None

    def _is_coordinate_on_warp(self, target_gy: int, target_gx: int) -> tuple[bool, Optional[dict]]:
        """Check if target global coordinates correspond to a warp tile"""
        if not WARP_DATA_AVAILABLE:
            return False, None
            
        try:
            # Get current map for coordinate conversion
            _, _, current_map_id = self.env.get_game_coords()
            
            # Convert target global coordinates to local coordinates
            target_local_coords = global_to_local(target_gy, target_gx, current_map_id)
            if not target_local_coords:
                return False, None
                
            target_local_y, target_local_x = target_local_coords
            map_name = self._get_map_name_from_id(current_map_id)
            
            if not map_name or map_name not in WARP_DICT:
                return False, None
            
            # Check if target coordinates match any warp
            for warp in WARP_DICT[map_name]:
                if warp['x'] == target_local_x and warp['y'] == target_local_y:
                    return True, warp
                    
            return False, None
            
        except Exception as e:
            print(f"Navigator: Error checking coordinate warp status: {e}")
            return False, None

    def check_json_path_stagnation_and_assist(self):
        """Simplified version that just snaps to nearest coordinate"""
        print("Navigator: Using simplified snap-to-nearest-coordinate functionality.")
        return self.snap_to_nearest_coordinate()

    def set_active_quest(self, quest_id: int):
        # Only update and notify when the active quest changes
        if self.current_quest_id != quest_id:
            self.current_quest_id = quest_id
            print(f"Navigator: Active quest set to {quest_id}")

    def reset_navigation(self):
        print("Navigator: Resetting navigation state.")
        self.navigation_status = "idle"
        self.current_path_actions = []
        self.current_navigation_target_local_grid = None
        self.current_navigation_target_global = None 
        self.last_failed_astar_target = None
        self.last_global_pos_before_astar_segment = None
        self.follow_path_steps_remaining = 0

    # Maintain other existing methods for compatibility but they're not used in simplified system
    def _local_9x10_to_global(self, row: int, col: int) -> Optional[tuple[int, int]]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: get_game_coords not available for _local_9x10_to_global.")
            return None
        try:
            player_local_x, player_local_y, current_map_id_int = self.env.get_game_coords()
            delta_grid_row = row - 4
            delta_grid_col = col - 4
            delta_local_y_tiles = delta_grid_row * 2
            delta_local_x_tiles = delta_grid_col * 2
            target_local_y = player_local_y + delta_local_y_tiles
            target_local_x = player_local_x + delta_local_x_tiles
            global_y, global_x = local_to_global(target_local_y, target_local_x, current_map_id_int)
            return int(global_x), int(global_y)
        except Exception as e:
            print(f"InteractiveNavigator: Error in _local_9x10_to_global: {e}")
            return None

    def _global_to_local_9x10(self, target_gx: int, target_gy: int) -> Optional[tuple[int, int]]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: get_game_coords not available for _global_to_local_9x10.")
            return None
        try:
            player_local_x, player_local_y, current_map_id_int = self.env.get_game_coords()
            target_local_coords = global_to_local(target_gy, target_gx, current_map_id_int)
            if target_local_coords is None: return None
            target_local_y, target_local_x = target_local_coords
            
            delta_local_y_tiles = target_local_y - player_local_y
            delta_local_x_tiles = target_local_x - player_local_x
            
            delta_grid_row = delta_local_y_tiles / 2.0
            delta_grid_col = delta_local_x_tiles / 2.0

            row_offset = 0
            if delta_grid_row != 0:
                row_offset = int(delta_grid_row + math.copysign(0.5, delta_grid_row))
            
            col_offset = 0
            if delta_grid_col != 0:
                col_offset = int(delta_grid_col + math.copysign(0.5, delta_grid_col))

            target_grid_row = 4 + row_offset
            target_grid_col = 4 + col_offset
            
            if 0 <= target_grid_row < 9 and 0 <= target_grid_col < 10:
                return target_grid_row, target_grid_col
            return None 
        except Exception as e:
            print(f"InteractiveNavigator: Error in _global_to_local_9x10: {e}")
            return None

    def _heuristic(self, a: tuple[int,int], b: tuple[int,int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])
    
    def _downsample_array(self, array, factor=2):
        if array is None or not hasattr(array, 'shape'): return None
        if array.shape[0] < factor or array.shape[1] < factor: return array 
        return array[::factor, ::factor]

    def get_sprites(self) -> list[tuple[int, int]]:
        sprite_coords_on_grid = []
        for i in range(40): 
            try:
                sprite = self.env.pyboy.get_sprite(i)
                if sprite.on_screen:
                    sprite_center_x_px = sprite.x + (sprite.width // 2) 
                    sprite_center_y_px = sprite.y + (sprite.height // 2)
                    grid_col = int(sprite_center_x_px / 16) 
                    grid_row = int(sprite_center_y_px / 16)
                    grid_col = max(0, min(grid_col, 9))
                    grid_row = max(0, min(grid_row, 8))
                    sprite_coords_on_grid.append((grid_col, grid_row))
            except Exception:
                continue 
        return list(set(sprite_coords_on_grid))

    def _can_move_between_tiles(self, current_tile_id: int, neighbor_tile_id: int, tileset_name: Optional[str], direction: str) -> bool:
        if not tileset_name or not self.tile_pair_collisions or tileset_name not in self.tile_pair_collisions:
            return True 
        rules = self.tile_pair_collisions[tileset_name]
        impassable_pairs = rules.get("impassable_general", [])
        for tile1, tile2 in impassable_pairs:
            if (current_tile_id == tile1 and neighbor_tile_id == tile2) or \
               (current_tile_id == tile2 and neighbor_tile_id == tile1):
                return False
        one_way_passages = rules.get("one_way_passages", [])
        for p_from, p_to, p_allowed_dir in one_way_passages:
            if current_tile_id == p_to and neighbor_tile_id == p_from: return False 
            if current_tile_id == p_from and neighbor_tile_id == p_to and direction != p_allowed_dir: return False
        return True

    # Keep remaining methods for compatibility but they won't be used in the simplified system
    def make_path_to_target_row_col(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        return "Simplified navigation system - A* pathfinding disabled", []

    def find_the_navigational_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        return "Simplified navigation system - pathfinding disabled", []
            
    def set_navigation_goal_local_grid(self, target_grid_row: int, target_grid_col: int):
        print("Simplified navigation system - use snap_to_nearest_coordinate() and move_to_next_coordinate() instead")

    def set_navigation_goal_global(self, target_gx: int, target_gy: int):
        print("Simplified navigation system - use snap_to_nearest_coordinate() and move_to_next_coordinate() instead")

    def step(self) -> tuple[str, Optional[int], Optional[tuple]]:
        return "Simplified navigation system - step() not used", None, None

    def follow_path(self, steps: int):
        """Simplified path following - just move the specified number of steps forward"""
        print(f"Navigator: Following path for {steps} steps using simplified system.")
        
        for i in range(steps):
            print(f"Navigator: Step {i+1}/{steps}")
            if not self.move_to_next_coordinate():
                print(f"Navigator: Path following stopped at step {i+1} - end of path or error.")
                break
        
        print("Navigator: Path following completed.")

    def schedule_next_path_step(self) -> bool:
        """Simplified version - just move to next coordinate"""
        return self.move_to_next_coordinate()

    def obtain_path_for_this_map(self) -> tuple[str, Optional[list[list[tuple[int, int]]]]]:
        current_map_id_str = self._get_current_map_id()
        if not current_map_id_str: return "Nav.obtain_path: Failed to get current map ID.", None
        
        all_segments_for_map = self.warp_path_data.get(current_map_id_str)
        if not all_segments_for_map: 
            return f"Nav.obtain_path: No recorded path data for map ID {current_map_id_str}.", None
        
        valid_segments = [segment for segment in all_segments_for_map if segment]
        if not valid_segments:
             return f"Nav.obtain_path: Map ID {current_map_id_str} found, but all path segments are empty.", None

        return f"Nav.obtain_path: Retrieved {len(valid_segments)} segments for map {current_map_id_str}.", valid_segments

    def _compute_best_intermediate_json_coord(self, ultimate_target_gx: int, ultimate_target_gy: int) -> tuple[str, Optional[tuple[int, int]]]:
        return "Simplified navigation system - intermediate coordinate computation disabled", None

    def add_to_global_nav_history(self, pos: tuple[int, int]):
        """Adds a position to the short-term history for oscillation detection."""
        self.global_nav_short_history.append(pos)
        if len(self.global_nav_short_history) > 5: 
            self.global_nav_short_history.pop(0)

    def check_recent_oscillation(self, pos: tuple[int, int], count: int = 2) -> bool:
        """Checks if the given position has appeared 'count' times in recent history."""
        if not self.global_nav_short_history:
            return False
        return self.global_nav_short_history.count(pos) >= count