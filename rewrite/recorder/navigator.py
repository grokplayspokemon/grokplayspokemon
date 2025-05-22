import numpy as np
import json
import heapq
import math
from pathlib import Path
from typing import Optional, Union

# Assuming play.py (and thus this file) is in DATAPlaysPokemon/rewrite/recorder/
# Adjust relative path to environment.py which is likely in DATAPlaysPokemon/
# If RedGymEnv is in DATAPlaysPokemon/pokegym/pokegym/environment.py, then it's more complex.
# Based on play.py's current structure, RedGymEnv is imported as `from environment import RedGymEnv`
# This implies environment.py is on the PYTHONPATH or in a discoverable location.
# For a sibling directory structure like:
# DATAPlaysPokemon/
#   rewrite/
#     recorder/
#       play.py
#       navigator.py <--- here
#   environment.py <--- this would be one level up from rewrite
# So, an import like `from ...environment import RedGymEnv` might be needed if environment.py is top-level
# OR if play.py's sys.path modification handles it, then a direct import might work.

# Let's try with a relative import assuming environment.py is at project root,
# and sys.path in play.py has added the project root.
# However, RedGymEnv is likely within the pokegym structure or similar.
# The original play.py just did `from environment import RedGymEnv`.
# This suggests environment.py is in a place accessible via PYTHONPATH, or play.py's sys.path made it so.
# If `play.py` has `sys.path.insert(0, str(project_root_path))`, and `environment.py` is at `project_root_path/environment.py` (unlikely for RedGymEnv)
# or `project_root_path/pokegym/pokegym/environment.py`.

# Let's assume the existing sys.path modifications in play.py make these accessible.
from environment import RedGymEnv # This might need adjustment based on actual file location
from pyboy.utils import WindowEvent
from global_map import local_to_global, global_to_local # Assuming play.py's sys.path allows this
from data.tilesets import Tilesets # Assuming play.py's sys.path allows this
# from agent.memory_reader import GameState # No longer importing GameState directly here


class InteractiveNavigator:
    def __init__(self, env_instance: RedGymEnv):
        self.env = env_instance
        self.pyboy = self.env.pyboy  # Direct access to pyboy via RedGymEnv
        self.tile_pair_collisions = {}
        self.warp_path_data: dict[str, list[tuple[int,int]]] = {}  # map_id_str to list of (gx,gy) tuples
        self._load_warp_paths()
        self._load_tile_pair_collisions()
        # Load quest coordinate paths for quests
        self.quest_path_data: dict[str, dict[str, list[tuple[int,int]]]] = {}
        self.current_quest_id: Optional[int] = None
        self._load_quest_paths()
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
        self.STAGNATION_THRESHOLD: int = 2

        # For A* segment loop detection (when A* path executes but player doesn't move globally)
        self.last_global_pos_before_astar_segment: Optional[tuple[int, int]] = None
        self.astar_segment_no_global_progress_count: int = 0
        self.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD: int = 2 # Allow one full A* segment retry with no global progress
        self.global_nav_short_history: list[tuple[int, int]] = [] # For detecting oscillations

        # Add warp handling attributes
        self.current_warp_segment: Optional[list[tuple[int,int]]] = None  # Warp path segment for map change transitions
        self.current_warp_index: int = 0  # Index within the warp path segment
        self.last_map_id: Optional[str] = None  # Track last map ID to detect map changes

    def _load_warp_paths(self):
        base_path = Path(__file__).parent 
        warp_file_path = base_path / "replays" / "recordings" / "originals" / "squirtle_start_to_entering_gym_2_cerulean.json"

        if warp_file_path.exists():
            try:
                with open(warp_file_path, 'r') as f:
                    raw_data = json.load(f)
                
                if isinstance(raw_data, dict):
                    self.warp_path_data = {}
                    for map_id_str, path_coords_list in raw_data.items():
                        if isinstance(path_coords_list, list):
                            formatted_path = [tuple(coord) for coord in path_coords_list if isinstance(coord, list) and len(coord) == 2]
                            if formatted_path: # Only add if path is not empty
                                self.warp_path_data[map_id_str] = formatted_path
                            else:
                                print(f"InteractiveNavigator: Warning: Empty path data for map ID '{map_id_str}' in {warp_file_path}.")
                        else:
                            print(f"InteractiveNavigator: Warning: Path data for map ID '{map_id_str}' in {warp_file_path} is not a list.")
                    print(f"InteractiveNavigator: Loaded warp paths from {warp_file_path} for {len(self.warp_path_data)} map IDs.")
                else:
                    print(f"InteractiveNavigator: Warning: Unexpected JSON structure in {warp_file_path}. Expected a dictionary of map_id:path_list.")
                    self.warp_path_data = {}
            except Exception as e:
                print(f"InteractiveNavigator: Error loading or parsing {warp_file_path}: {e}")
                self.warp_path_data = {}
        else:
            self.warp_path_data = {}
            print(f"InteractiveNavigator: CRITICAL WARNING: Specified warp path file not found at {warp_file_path}. JSON-guided navigation will not work.")

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

    def _load_quest_paths(self):
        base_dir = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046"
        if not base_dir.exists():
            print(f"Navigator: Quest paths directory not found at {base_dir}")
            return
        for quest_dir in base_dir.iterdir():
            if not quest_dir.is_dir():
                continue
            coords_file = quest_dir / f"{quest_dir.name}_coords.json"
            if coords_file.exists():
                try:
                    raw = json.load(open(coords_file))
                    formatted = {map_id: [tuple(pt) for pt in pts] for map_id, pts in raw.items()}
                    self.quest_path_data[quest_dir.name] = formatted
                    total = sum(len(v) for v in formatted.values())
                    print(f"Navigator: Loaded quest path for {quest_dir.name} with {total} points")
                except Exception as e:
                    print(f"Navigator: Failed to load quest path for {quest_dir.name}: {e}")

    def _get_player_global_coords(self) -> Optional[tuple[int, int]]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: env object does not have get_game_coords method.")
            return None
        try:
            player_local_x, player_local_y, current_map_id_int = self.env.get_game_coords()
            global_y, global_x = local_to_global(player_local_y, player_local_x, current_map_id_int)
            # Only print when the location (including map) changes
            coord = (int(global_x), int(global_y), current_map_id_int)
            if self.last_position is None or coord != self.last_position:
                print(f"Navigator: Location changed to global {coord}")
            self.last_position = coord
            return int(global_x), int(global_y)
        except Exception as e:
            print(f"InteractiveNavigator: Error getting player global coords: {e}")
            return None

    def _get_current_map_id(self) -> Optional[str]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: get_game_coords method not available on env.")
            return None
        try:
            _, _, map_n = self.env.get_game_coords()
            return str(map_n)
        except Exception as e:
            print(f"InteractiveNavigator: Error getting current map ID via env: {e}")
            return None

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

    def _manhattan_distance(self, pos1: tuple[int, int], pos2: tuple[int, int]) -> int:
        return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])
    
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

    def make_path_to_target_row_col(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        if not self.env.pyboy or not hasattr(self.env.pyboy, 'game_wrapper') or \
           not hasattr(self.env.pyboy.game_wrapper, 'game_area_collision') or \
           not hasattr(self.env.pyboy.game_wrapper, '_get_screen_background_tilemap'):
            return "Failure: PyBoy game_wrapper or required methods not available.", []

        collision_map_full_res = self.env.pyboy.game_wrapper.game_area_collision()
        if collision_map_full_res is None: return "Failure: Could not get game_area_collision()", []
        
        terrain = self._downsample_array(collision_map_full_res) 
        if terrain is None: return "Failure: Could not downsample collision map", []

        sprite_locations_9x10 = self.get_sprites() 
        full_map_tile_ids = self.env.pyboy.game_wrapper._get_screen_background_tilemap()
        if full_map_tile_ids is None: return "Failure: Could not get _get_screen_background_tilemap()", []
            
        tileset_name_str: Optional[str] = None
        try:
            tileset_id = self.pyboy.memory[0xD367] 
            tileset_name_str = Tilesets(tileset_id).name.replace("_", " ")
        except ImportError:
            print("InteractiveNavigator: Warning: Could not import 'Tilesets'. Tileset-specific rules disabled.")
        except KeyError: 
            print(f"InteractiveNavigator: Warning: Could not read tileset ID. Tileset-specific rules disabled.")
        except Exception as e: 
            print(f"InteractiveNavigator: Warning: Error processing tileset ID: {e}. Tileset-specific rules disabled.")

        start = (4, 4) 
        end = (target_row, target_col)
        if not (0 <= target_row < 9 and 0 <= target_col < 10):
            return "Invalid target coordinates for 9x10 grid", []

        open_set = []
        heapq.heappush(open_set, (0, start))
        came_from = {}
        g_score = {start: 0}
        f_score = {start: self._heuristic(start, end)} 
        closest_point = start
        min_distance_to_target = self._heuristic(start, end)

        def reconstruct_path(current_node_in_path):
            path_actions = []
            while current_node_in_path in came_from:
                prev_node = came_from[current_node_in_path]
                if prev_node[0] < current_node_in_path[0]: path_actions.append("down")
                elif prev_node[0] > current_node_in_path[0]: path_actions.append("up")
                elif prev_node[1] < current_node_in_path[1]: path_actions.append("right")
                else: path_actions.append("left")
                current_node_in_path = prev_node
            path_actions.reverse()
            return path_actions

        # Determine if the 'end' tile itself is walkable before starting A*
        is_target_terrain_wall = terrain[end[0]][end[1]] == 0
        is_target_npc_occupied = (end[1], end[0]) in sprite_locations_9x10 # (col, row) for sprites
        is_target_walkable = not is_target_terrain_wall and not is_target_npc_occupied

        while open_set:
            _, current = heapq.heappop(open_set)

            # Success Condition 1: Reached a walkable 'end' target directly.
            if current == end and is_target_walkable:
                path = reconstruct_path(current)
                return (f"Nav.A*: Success: Path to walkable target ({target_row},{target_col}).", path)

            # Update closest point found so far (to a potentially unwalkable 'end')
            current_h_dist = self._heuristic(current, end)
            if current_h_dist < min_distance_to_target:
                closest_point = current
                min_distance_to_target = current_h_dist
            
            # Success Condition 2: Reached a tile 'current' that is walkable AND adjacent to an UNWALKABLE 'end'.
            if not is_target_walkable and (abs(current[0] - end[0]) + abs(current[1] - end[1])) == 1:
                # 'current' must be walkable itself for this to be a valid standing spot.
                # terrain check is for current's own walkability if needed, but A* explores walkable.
                # sprite check for 'current' is implicitly handled by NPC check for neighbors later.
                current_is_wall = terrain[current[0]][current[1]] == 0
                current_is_npc = (current[1], current[0]) in sprite_locations_9x10
                if not current_is_wall and not current_is_npc: # 'current' itself must be a valid spot to stand
                    path_to_current_adjacent_to_unwalkable_end = reconstruct_path(current)
                    # Avoid 0-move success if player is already at 'current' (start) and 'end' is the unwalkable adjacent.
                    if not (current == start and not path_to_current_adjacent_to_unwalkable_end):
                         return (f"Nav.A*: Success: Path to {current} adjacent to unwalkable target ({target_row},{target_col}).", path_to_current_adjacent_to_unwalkable_end)

            for dr, dc, direction_str in [(1, 0, "down"), (-1, 0, "up"), (0, 1, "right"), (0, -1, "left")]:
                neighbor = (current[0] + dr, current[1] + dc)
                nrow, ncol = neighbor
                if not (0 <= nrow < 9 and 0 <= ncol < 10): continue

                if (ncol, nrow) in sprite_locations_9x10:
                    continue 

                if terrain[nrow][ncol] == 0 and neighbor != end: continue
                
                current_tile_on_fullmap_row = current[0] * 2
                current_tile_on_fullmap_col = current[1] * 2
                neighbor_tile_on_fullmap_row = nrow * 2
                neighbor_tile_on_fullmap_col = ncol * 2
                current_tile_id_val = full_map_tile_ids[current_tile_on_fullmap_row][current_tile_on_fullmap_col]
                neighbor_tile_id_val = full_map_tile_ids[neighbor_tile_on_fullmap_row][neighbor_tile_on_fullmap_col]

                if not self._can_move_between_tiles(current_tile_id_val, neighbor_tile_id_val, tileset_name_str, direction_str):
                    continue

                tentative_g_score = g_score[current] + 1
                if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g_score
                    f_score[neighbor] = tentative_g_score + self._heuristic(neighbor, end)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))

        # Fallback: Target is unreachable or no path exists. Path to the closest reachable point.
        # This triggers if 'end' was walkable but no path, or 'end' was unwalkable and no path to adjacent.
        if closest_point != start: # If we moved at all from the start
            path = reconstruct_path(closest_point)
            # Check if closest_point is the same as start AND no actual path segments were generated.
            # This can happen if start is already the "closest" to an unreachable target.
            if closest_point == start and not path:
                 return (f"Nav.A*: Target ({target_row},{target_col}) Unreachable; No improvement from start. Player already at best spot or surrounded.", [])
            return (f"Nav.A*: Target ({target_row},{target_col}) Unreachable; Path to closest ({closest_point[0]},{closest_point[1]}).", path)
        
        return (f"Nav.A*: Target ({target_row},{target_col}) Unreachable; No path found from start.", [])

    def _compute_best_intermediate_json_coord(self, ultimate_target_gx: int, ultimate_target_gy: int) -> tuple[str, Optional[tuple[int, int]]]:
        player_glob_coords_xy = self._get_player_global_coords()
        if not player_glob_coords_xy: return "Nav.JSONGuide: Failed to get player global coords.", None
        player_gx, player_gy = player_glob_coords_xy

        status_msg_obtain_path, all_json_segments_for_map = self.obtain_path_for_this_map()
        if not all_json_segments_for_map: return f"Nav.JSONGuide: Could not obtain JSON paths. ({status_msg_obtain_path})", None

        best_json_coord_overall: Optional[tuple[int,int]] = None
        lowest_score_overall = float('inf')
        
        best_json_coord_onscreen: Optional[tuple[int,int]] = None
        lowest_score_onscreen = float('inf')

        w1 = 0.4 
        w2 = 0.6 

        for segment in all_json_segments_for_map: 
            for json_gx, json_gy in segment:
                dist_player_to_json = self._heuristic((player_gx, player_gy), (json_gx, json_gy))
                dist_json_to_ultimate = self._heuristic((json_gx, json_gy), (ultimate_target_gx, ultimate_target_gy))
                current_score = (w1 * dist_player_to_json) + (w2 * dist_json_to_ultimate)

                is_on_screen = self._global_to_local_9x10(json_gx, json_gy) is not None

                if current_score < lowest_score_overall:
                    lowest_score_overall = current_score
                    best_json_coord_overall = (json_gx, json_gy)

                if is_on_screen:
                    if current_score < lowest_score_onscreen:
                        lowest_score_onscreen = current_score
                        best_json_coord_onscreen = (json_gx, json_gy)
        
        if best_json_coord_onscreen:
            return f"Nav.JSONGuide: Selected on-screen JSON coord ({best_json_coord_onscreen[0]},{best_json_coord_onscreen[1]}) score {lowest_score_onscreen:.2f}.", best_json_coord_onscreen
        elif best_json_coord_overall:
            return f"Nav.JSONGuide: Selected off-screen JSON coord ({best_json_coord_overall[0]},{best_json_coord_overall[1]}) score {lowest_score_overall:.2f} (no on-screen found).", best_json_coord_overall
        
        return "Nav.JSONGuide: No suitable JSON path coord found on current map.", None

    def obtain_path_for_this_map(self) -> tuple[str, Optional[list[list[tuple[int, int]]]]]:
        current_map_id_str = self._get_current_map_id()
        if not current_map_id_str: return "Nav.obtain_path: Failed to get current map ID.", None
        
        path_segment_for_map = self.warp_path_data.get(current_map_id_str)
        if not path_segment_for_map: 
            return f"Nav.obtain_path: No warp path data for map ID {current_map_id_str}.", None
        
        all_segments_on_map = [path_segment_for_map] 
        
        if not all_segments_on_map or not all_segments_on_map[0]: 
             return f"Nav.obtain_path: Map ID {current_map_id_str} found, but path segment is empty.", None

        return f"Nav.obtain_path: Retrieved 1 segment for map {current_map_id_str}.", all_segments_on_map

    def find_the_navigational_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        status_msg_direct_astar, direct_astar_path_actions = self.make_path_to_target_row_col(target_row, target_col)
        if not direct_astar_path_actions: return f"Nav.find_path (direct A*): Failed to ({target_row},{target_col}). {status_msg_direct_astar}", []
        return f"Nav.find_path (direct A*): Path to ({target_row},{target_col}). {status_msg_direct_astar}", direct_astar_path_actions
            
    def set_navigation_goal_local_grid(self, target_grid_row: int, target_grid_col: int):
        if not (0 <= target_grid_row < 9 and 0 <= target_grid_col < 10):
            print(f"Error: Target local grid coordinates ({target_grid_row}, {target_grid_col}) out of 9x10 bounds.")
            self.navigation_status = "failed"
            return
        self.current_navigation_target_local_grid = (target_grid_row, target_grid_col)
        self.current_navigation_target_global = None 
        self.current_path_actions = []
        self.navigation_status = "planning"
        print(f"Navigation goal set to LOCAL GRID: ({target_grid_row}, {target_grid_col}). Status: {self.navigation_status}")

    def set_navigation_goal_global(self, target_gx: int, target_gy: int):
        self.current_navigation_target_global = (target_gx, target_gy)
        self.current_navigation_target_local_grid = None 
        self.current_path_actions = []
        self.navigation_status = "planning"
        self.last_failed_astar_target = None 
        self.last_global_pos_before_astar_segment = None
        self.astar_segment_no_global_progress_count = 0
        self.global_nav_short_history = [] # Reset history for new global goal
        print(f"Navigation goal set to GLOBAL: ({target_gx}, {target_gy}). Status: {self.navigation_status}")

    def step(self) -> tuple[str, Optional[int], Optional[tuple]]:
        # Check for dialog/script state first
        try:
            if hasattr(self.env, 'read_dialog'):
                dialog_text = self.env.read_dialog()
                if dialog_text and dialog_text.strip():
                    if self.navigation_status == "navigating" and self.current_path_actions:
                        self.current_path_actions = []
                        print(f"Navigator: Dialog detected, clearing current path. Dialog: '{dialog_text[:50]}...'")
                    return f"Navigation paused: Dialog active ('{dialog_text[:50]}...').", None, None
            else:
                print("Navigator: env does not have read_dialog method or env is not set.")
        except Exception as e:
            print(f"Navigator: Error calling self.env.read_dialog(): {e}. Proceeding with navigation step.")

        status_message = f"Nav step. Status: {self.navigation_status}."
        executed_action_int_for_return: Optional[int] = None
        step_results_for_return: Optional[tuple] = None
        
        if self.navigation_status == "planning":
            player_starting_gx, player_starting_gy = self._get_player_global_coords() or (None, None)
            log_start_coords = f"Player Start Coords: ({player_starting_gx},{player_starting_gy}). " if player_starting_gx is not None else "Player Start Coords: N/A. "

            path_generated = False
            current_planning_log = log_start_coords 
            path_actions_found: list[str] = []
            final_astar_target_rg: Optional[tuple[int, int]] = None 
            plan_status_astar = "No A* run" 

            if self.current_navigation_target_global:
                ultimate_gx, ultimate_gy = self.current_navigation_target_global
                current_planning_log += f"Global target ({ultimate_gx},{ultimate_gy})."

                json_status_msg, best_json_intermediate_global_coord = self._compute_best_intermediate_json_coord(ultimate_gx, ultimate_gy)
                current_planning_log += f" {json_status_msg}"

                if best_json_intermediate_global_coord:
                    json_inter_gx, json_inter_gy = best_json_intermediate_global_coord
                    local_grid_target_from_json = self._global_to_local_9x10(json_inter_gx, json_inter_gy)
                    if local_grid_target_from_json:
                        final_astar_target_rg = local_grid_target_from_json
                        current_planning_log += f" Using JSON intermediate {best_json_intermediate_global_coord} (local: {final_astar_target_rg})."
                    else:
                        current_planning_log += f" JSON intermediate {best_json_intermediate_global_coord} is off-screen."
                
                if not final_astar_target_rg:
                    direct_local_target_from_global = self._global_to_local_9x10(ultimate_gx, ultimate_gy)
                    if direct_local_target_from_global:
                        final_astar_target_rg = direct_local_target_from_global
                        current_planning_log += f" Attempting direct A* to global target (local: {final_astar_target_rg})."
                    else:
                        current_planning_log += f" Global target ({ultimate_gx},{ultimate_gy}) is also off-screen. Cannot determine on-screen A* target."
                        
            elif self.current_navigation_target_local_grid:
                local_target_r, local_target_c = self.current_navigation_target_local_grid
                current_planning_log += f"Local grid target ({local_target_r},{local_target_c})."
                
                ultimate_target_for_json_guidance_global = self._local_9x10_to_global(local_target_r, local_target_c)

                if ultimate_target_for_json_guidance_global:
                    ult_gx_for_json, ult_gy_for_json = ultimate_target_for_json_guidance_global
                    json_status_msg, best_json_intermediate_global_coord = self._compute_best_intermediate_json_coord(ult_gx_for_json, ult_gy_for_json)
                    current_planning_log += f" {json_status_msg}"
                    
                    if best_json_intermediate_global_coord:
                        json_inter_gx, json_inter_gy = best_json_intermediate_global_coord
                        local_grid_target_from_json = self._global_to_local_9x10(json_inter_gx, json_inter_gy)
                        if local_grid_target_from_json:
                            final_astar_target_rg = local_grid_target_from_json
                            current_planning_log += f" Using JSON intermediate {best_json_intermediate_global_coord} (local: {final_astar_target_rg})."
                        else:
                            current_planning_log += f" JSON intermediate {best_json_intermediate_global_coord} for local target is off-screen."
                
                if not final_astar_target_rg: 
                    final_astar_target_rg = self.current_navigation_target_local_grid 
                    current_planning_log += f" Attempting direct A* to local grid target {final_astar_target_rg}."

            if final_astar_target_rg:
                target_r_astar, target_c_astar = final_astar_target_rg
                plan_status_astar, path_actions_found = self.make_path_to_target_row_col(target_r_astar, target_c_astar)
                current_planning_log += f" A* to ({target_r_astar},{target_c_astar}): {plan_status_astar}."
                
                if ("Success" in plan_status_astar) or \
                   ("Path to closest" in plan_status_astar and path_actions_found):
                    path_generated = True
            else:
                current_planning_log += " No on-screen A* target could be determined."
            
            print(f"Nav Planning Log: {current_planning_log}") 

            is_stuck_at_astar_target = (
                "Success" in plan_status_astar and 
                not path_actions_found and 
                final_astar_target_rg is not None and 
                ("adjacent to wall" in plan_status_astar or "Target Unreachable" in plan_status_astar or "closest" in plan_status_astar)
            )

            if is_stuck_at_astar_target:
                if self.last_failed_astar_target == final_astar_target_rg:
                    print(f"Nav.step: Detected loop for A* target {final_astar_target_rg}. Failing global goal {self.current_navigation_target_global}.")
                    self.navigation_status = "failed"
                    status_message = f"Navigation failed: Stuck in loop for A* target {final_astar_target_rg} towards global {self.current_navigation_target_global}."
                    self.last_failed_astar_target = None 
                else:
                    self.last_failed_astar_target = final_astar_target_rg
            else:
                self.last_failed_astar_target = None

            if self.navigation_status != "failed": 
                if path_generated and final_astar_target_rg is not None: 
                    self.current_path_actions = path_actions_found
                    self.navigation_status = "navigating"
                    self.last_global_pos_before_astar_segment = self._get_player_global_coords()

                    if not path_actions_found: 
                        next_move_msg = "Already at A* target or no path segment moves needed."
                    else:
                        next_move_msg = f"Next: {path_actions_found[0]}"
                    status_message = f"Path planned ({len(path_actions_found)} moves to A* target {final_astar_target_rg}). {next_move_msg}"
                else:
                    self.navigation_status = "failed"
                    astar_detail = f"(A* status: {plan_status_astar})" if plan_status_astar != "No A* run" else ""
                    if not final_astar_target_rg and plan_status_astar == "No A* run":
                        status_message = "Path planning failed: No on-screen A* target could be determined."
                    else:
                        status_message = f"Path planning failed. {astar_detail}"
            return status_message, None, None
        
        elif self.navigation_status == "navigating":
            if not self.current_path_actions:
                self.navigation_status = "completed"
                nav_target_str = f"global {self.current_navigation_target_global}" if self.current_navigation_target_global else f"local {self.current_navigation_target_local_grid}"
                return f"Nav to {nav_target_str} path segment presumably completed. Overall goal may not be reached if intermediate.", None, None
            
            move_action_str_peek = self.current_path_actions[0]
            action_int_peek = self.ACTION_MAPPING_STR_TO_INT.get(move_action_str_peek.lower())

            if action_int_peek is None:
                self.navigation_status = "failed"; self.current_path_actions = []
                return f"Nav failed: Unknown action '{move_action_str_peek}' in path queue.", None, None

            player_astar_row, player_astar_col = 4, 4 
            next_astar_row, next_astar_col = player_astar_row, player_astar_col
            if move_action_str_peek == "down": next_astar_row += 1
            elif move_action_str_peek == "up": next_astar_row -=1
            elif move_action_str_peek == "right": next_astar_col += 1
            elif move_action_str_peek == "left": next_astar_col -=1
            
            current_sprites_9x10 = self.get_sprites() 
            if (next_astar_col, next_astar_row) in current_sprites_9x10:
                self.navigation_status = "planning" 
                self.current_path_actions = [] 
                self.last_failed_astar_target = (next_astar_row, next_astar_col) 
                return f"Nav obstructed: Next step ({move_action_str_peek}) to A* grid ({next_astar_row},{next_astar_col}) blocked by NPC. Re-planning.", None, None

            move_action_str = self.current_path_actions.pop(0)
            action_int = self.ACTION_MAPPING_STR_TO_INT.get(move_action_str.lower()) 
            
            if action_int is None: 
                self.navigation_status = "failed"; self.current_path_actions = []
                return f"Nav failed: Unknown action '{move_action_str}' during pop.", None, None
            if not self.env:
                self.navigation_status = "failed"
                return "Nav failed: env not available.", None, None
            
            try:
                obs, reward, terminated, truncated, info = self.env.step(action_int)
                
                status_message = f"Executed nav action: {move_action_str} (int: {action_int}). Remaining: {len(self.current_path_actions)}."
                executed_action_int_for_return = action_int
                step_results_for_return = (obs, reward, terminated, truncated, info)

                if not self.current_path_actions: 
                    self.navigation_status = "completed" 
                    status_message += f" Current path segment complete."
            except Exception as e:
                self.navigation_status = "failed"
                status_message = f"Nav failed during env.step() for {move_action_str}: {e}"
            
            return status_message, executed_action_int_for_return, step_results_for_return
        
        return status_message, None, None

    def reset_navigation(self):
        self.current_navigation_target_local_grid = None
        self.current_navigation_target_global = None 
        self.current_path_actions = []
        self.navigation_status = "idle"
        self.last_failed_astar_target = None 
        
        self.last_global_pos_before_astar_segment = None
        self.astar_segment_no_global_progress_count = 0
        self.global_nav_short_history = [] # Reset history on full reset
        
        print("Navigation (A* segment and global goal) reset to idle.")

    def check_json_path_stagnation_and_assist(self):
        # Skip assistance if navigation is not idle
        if self.navigation_status != "idle":
            return
        # Do not trigger when dialog is present
        try:
            if hasattr(self.env, 'read_dialog'):
                dialog_text = self.env.read_dialog()
                if dialog_text and dialog_text.strip():
                    return
        except Exception:
            pass
        # Ensure active quest is set
        if self.current_quest_id is None:
            return
        player_gx, player_gy = self._get_player_global_coords()
        current_map_id_str = self._get_current_map_id()
        if player_gx is None or current_map_id_str is None:
            return
        # Fetch quest path data for this quest and map
        quest_data = self.quest_path_data.get(str(self.current_quest_id))
        if not quest_data:
            print(f"Navigator: No path data for quest {self.current_quest_id}")
            return
        current_coords = quest_data.get(current_map_id_str)
        if not current_coords:
            return
        # Log quest and path info
        print(f"Navigator: Quest {self.current_quest_id} on map {current_map_id_str}, path length {len(current_coords)}")
        # Find nearest recorded point (swap pt order: stored as (gy, gx))
        distances = [self._manhattan_distance((player_gx, player_gy), (pt[1], pt[0])) for pt in current_coords]
        nearest_idx = distances.index(min(distances))
        nearest_pt = current_coords[nearest_idx]
        print(f"Navigator: Nearest path point idx {nearest_idx}, raw coords {nearest_pt}, player at ({player_gx},{player_gy}), dist {distances[nearest_idx]}")
        # Debug: log full quest path for this map
        print(f"Navigator: Full path for quest {self.current_quest_id} on map {current_map_id_str}: {current_coords}")
        # Navigate back to nearest path point with correct (gx,gy) ordering
        self.current_json_path_target_idx = nearest_idx
        # nearest_pt stored as (map_y, map_x); swap for global coordinates
        target_y, target_x = nearest_pt
        target_gx, target_gy = target_x, target_y
        print(f"Navigator: Setting navigation goal to nearest path point (gx,gy)=({target_gx},{target_gy})")
        self.set_navigation_goal_global(target_gx, target_gy)

    def add_to_global_nav_history(self, pos: tuple[int, int]):
        """Adds a position to the short-term history for oscillation detection."""
        self.global_nav_short_history.append(pos)
        # Keep history to a manageable size, e.g., last 5 moves for the current global target pursuit
        if len(self.global_nav_short_history) > 5: 
            self.global_nav_short_history.pop(0)

    def check_recent_oscillation(self, pos: tuple[int, int], count: int = 2) -> bool:
        """Checks if the given position has appeared 'count' times in recent history."""
        if not self.global_nav_short_history:
            return False
        return self.global_nav_short_history.count(pos) >= count

    def set_active_quest(self, quest_id: int):
        # Only update and notify when the active quest changes
        if self.current_quest_id != quest_id:
            self.current_quest_id = quest_id
            print(f"Navigator: Active quest set to {quest_id}")

    def follow_path(self, steps: int):
        """Follow the recorded quest path for the given number of steps."""
        # Ensure navigation is idle before starting
        if self.navigation_status != "idle":
            print("Navigator: Cannot follow path, navigation in progress.")
            return
        # Snap back to nearest recorded path point if off-path
        self.check_json_path_stagnation_and_assist()
        executed = 0
        # Follow path across maps for the requested number of steps
        for _ in range(steps):
            if not self.schedule_next_path_step():
                print(f"Navigator: No more path steps at step {executed+1}.")
                break
            executed += 1
            # Execute this scheduled step until complete
            while self.navigation_status in ("planning", "navigating"):
                msg, action_int, step_res = self.step()
                if msg:
                    print(msg)
        print(f"Navigator: Completed follow_path for {executed} steps.")

    def schedule_next_path_step(self) -> bool:
        """Plan a single step along the recorded quest path. Returns True if planned, False if no more steps."""
        # Ensure active quest is set
        if self.current_quest_id is None:
            print("Navigator: No active quest set.")
            return False
        # Load current map and path coordinates
        current_map_id_str = self._get_current_map_id()
        coords = self.quest_path_data.get(str(self.current_quest_id), {}).get(current_map_id_str, [])
        if not coords:
            warp = self.warp_path_data.get(current_map_id_str)
            if warp:
                # Initialize warp segment if needed
                if self.current_warp_segment is None or self.current_warp_segment is not warp:
                    self.current_warp_segment = warp
                    self.current_warp_index = 0
                # Schedule next warp step
                if self.current_warp_index < len(self.current_warp_segment):
                    raw_pt = self.current_warp_segment[self.current_warp_index]
                    self.current_warp_index += 1
                    # Use raw x,y in correct order
                    target_gx, target_gy = raw_pt[0], raw_pt[1]
                    local_grid = self._global_to_local_9x10(target_gx, target_gy)
                    if local_grid:
                        # Direct A* planning for warp movement
                        status_msg, path_actions = self.find_the_navigational_path(local_grid[0], local_grid[1])
                        if path_actions:
                            self.current_path_actions = path_actions
                            self.navigation_status = "navigating"
                        else:
                            print(f"Navigator: Warp A* planning failed to {local_grid}. {status_msg}")
                            self.navigation_status = "failed"
                    else:
                        # Fallback to global guidance for off-screen warp target
                        self.set_navigation_goal_global(target_gx, target_gy)
                    return True
                # Completed warp segment; clear and reset JSON index
                self.current_warp_segment = None
                self.current_warp_index = 0
                self.current_json_path_target_idx = -1
                # Reload coords after warp
                coords = self.quest_path_data.get(str(self.current_quest_id), {}).get(current_map_id_str, [])
                if not coords:
                    print(f"Navigator: No path data for quest {self.current_quest_id} on map {current_map_id_str} after warp")
                    return False
            else:
                print(f"Navigator: No path data for quest {self.current_quest_id} on map {current_map_id_str}")
                return False
        # Reset JSON path and warp when map changes
        if self.last_map_id != current_map_id_str:
            self.current_json_path_target_idx = -1  # so next idx+1 = 0
            self.current_warp_segment = None
            self.current_warp_index = 0
            self.last_map_id = current_map_id_str
        # Determine next index
        idx = self.current_json_path_target_idx or 0
        # Handle warp segment if at end of recorded path
        if idx + 1 >= len(coords):
            warp = self.warp_path_data.get(current_map_id_str)
            if warp:
                if self.current_warp_segment is None:
                    self.current_warp_segment = warp
                    self.current_warp_index = 0
                if self.current_warp_index < len(self.current_warp_segment):
                    raw_pt = self.current_warp_segment[self.current_warp_index]
                    self.current_warp_index += 1
                    # Use raw x,y in correct order
                    target_gx, target_gy = raw_pt[0], raw_pt[1]
                    local_grid = self._global_to_local_9x10(target_gx, target_gy)
                    if local_grid:
                        # Direct A* planning for warp movement
                        status_msg, path_actions = self.find_the_navigational_path(local_grid[0], local_grid[1])
                        if path_actions:
                            self.current_path_actions = path_actions
                            self.navigation_status = "navigating"
                        else:
                            print(f"Navigator: Warp A* planning failed to {local_grid}. {status_msg}")
                            self.navigation_status = "failed"
                    else:
                        # Fallback to global guidance for off-screen warp target
                        self.set_navigation_goal_global(target_gx, target_gy)
                    return True
                else:
                    # Completed warp path; clear warp and reset JSON index for next map
                    self.current_warp_segment = None
                    self.current_warp_index = 0
                    self.current_json_path_target_idx = -1
            # Transition to next map's first point if available
            quest_data = self.quest_path_data.get(str(self.current_quest_id), {})
            map_keys = sorted(quest_data.keys(), key=int)
            try:
                current_index = map_keys.index(current_map_id_str)
            except ValueError:
                current_index = -1
            if current_index + 1 < len(map_keys):
                next_map_id_str = map_keys[current_index + 1]
                next_coords = quest_data.get(next_map_id_str, [])
                if next_coords:
                    # Schedule navigation to first coordinate of next map
                    self.current_json_path_target_idx = -1  # so next idx+1 = 0
                    print(f"Navigator: Transitioning to next map {next_map_id_str}")
                    raw_pt = next_coords[0]
                    target_gx, target_gy = raw_pt[1], raw_pt[0]
                    local_grid = self._global_to_local_9x10(target_gx, target_gy)
                    if local_grid:
                        self.set_navigation_goal_local_grid(*local_grid)
                    else:
                        self.set_navigation_goal_global(target_gx, target_gy)
                    return True
                else:
                    print(f"Navigator: No path data for next map {next_map_id_str}")
                    return False
            # No further maps: end of full path
            print(f"Navigator: Reached end of recorded path for quest {self.current_quest_id}.")
            return False
        raw_pt = coords[idx + 1]
        # stored as (gy, gx)
        target_gx, target_gy = raw_pt[1], raw_pt[0]
        # Plan navigation to next point using local grid if possible
        local_grid = self._global_to_local_9x10(target_gx, target_gy)
        if local_grid:
            self.set_navigation_goal_local_grid(*local_grid)
        else:
            self.set_navigation_goal_global(target_gx, target_gy)
        # Advance target index
        self.current_json_path_target_idx = idx + 1
        return True
