import argparse
import numpy as np
import json
import pygame
import time
import heapq # Added for A*
import sys # Added for sys.path manipulation
import math # Added for custom rounding
from pathlib import Path
from typing import Optional, Union, Any # Added for type hinting

from environment import RedGymEnv
from pyboy.utils import WindowEvent

# Add project root for global_map import if play.py is in a subdirectory
# Assuming play.py is in DATAPlaysPokemon/rewrite/recorder/
project_root_path = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root_path))

from global_map import local_to_global, global_to_local


def process_frame_for_pygame(frame_from_env_render):
    if frame_from_env_render.ndim == 2: # Grayscale
        frame_rgb = np.stack((frame_from_env_render,) * 3, axis=-1)
    elif frame_from_env_render.ndim == 3 and frame_from_env_render.shape[2] == 1: # Grayscale with channel dim
        frame_rgb = np.concatenate([frame_from_env_render] * 3, axis=2)
    else: # Already RGB or other format, use as is (might need adjustment)
        frame_rgb = frame_from_env_render
    
    return frame_rgb

def update_screen(screen, frame_rgb, target_width, target_height):
    obs_surface = pygame.surfarray.make_surface(frame_rgb.transpose(1,0,2))
    obs_surface = pygame.transform.scale(obs_surface, (target_width, target_height))
    screen.blit(obs_surface, (0, 0))
    pygame.display.flip()

def get_default_config(rom_path, initial_state_path):
    # For play.py, the initial_state_path from args.state is directly used.
    # If args.state is None or file doesn't exist, RedGymEnv handles it.
    init_state_name = None
    if initial_state_path:
        p = Path(initial_state_path)
        if p.is_file(): # Check if it's a file path
            init_state_name = p.stem
        else: # Assume it might be just a name without extension
            init_state_name = initial_state_path


    return {
        "video_dir": Path("./videos/play_sessions/"),
        "emulator_delay": 11,
        "headless": False, # play.py is interactive, so headless is False
        "state_dir": Path("./states/"), # Relative to play.py
        "init_state": init_state_name, # Use provided state, or None
        "action_freq": 24,
        "max_steps": 1_000_000,
        "save_video": False,
        "fast_video": False,
        "n_record": 0,
        "perfect_ivs": False,
        "reduce_res": False,
        "gb_path": rom_path,
        "log_frequency": 1000,
        "two_bit": False,
        "auto_flash": False,
        "required_tolerance": None,
        "disable_wild_encounters": False,
        "disable_ai_actions": True, # AI actions likely disabled for interactive play
        "auto_teach_cut": False,
        "auto_teach_surf": False,
        "auto_teach_strength": False,
        "auto_use_cut": False,
        "auto_use_strength": False,
        "auto_use_surf": False,
        "auto_solve_strength_puzzles": False,
        "auto_remove_all_nonuseful_items": False,
        "auto_pokeflute": False,
        "auto_next_elevator_floor": False,
        "skip_safari_zone": False,
        "infinite_safari_steps": False,
        "insert_saffron_guard_drinks": False,
        "infinite_money": False,
        "infinite_health": False,
        "use_global_map": False,
        "save_state": False,
        "animate_scripts": True,
        "exploration_inc": 0.01,
        "exploration_max": 1.0,
        "max_steps_scaling": 0.0,
        "map_id_scalefactor": 1.0,
    }

VALID_ACTIONS_MANUAL = [ # Renamed to avoid conflict if navigator has its own
    WindowEvent.PRESS_ARROW_DOWN, WindowEvent.PRESS_ARROW_LEFT,
    WindowEvent.PRESS_ARROW_RIGHT, WindowEvent.PRESS_ARROW_UP,
    WindowEvent.PRESS_BUTTON_A, WindowEvent.PRESS_BUTTON_B,
    WindowEvent.PRESS_BUTTON_START,
]

ACTION_MAPPING_PYGAME_TO_INT = {
    pygame.K_DOWN: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_ARROW_DOWN),
    pygame.K_LEFT: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_ARROW_LEFT),
    pygame.K_RIGHT: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_ARROW_RIGHT),
    pygame.K_UP: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_ARROW_UP),
    pygame.K_a: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_BUTTON_A),
    pygame.K_s: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_BUTTON_B),
    pygame.K_RETURN: VALID_ACTIONS_MANUAL.index(WindowEvent.PRESS_BUTTON_START),
}

class InteractiveNavigator:
    def __init__(self, env_instance: RedGymEnv):
        self.env = env_instance
        self.pyboy = self.env.pyboy # Direct access to pyboy via RedGymEnv
        self.tile_pair_collisions = {}
        self.warp_path_data: dict[str, list[tuple[int,int]]] = {} # map_id_str to list of (gx,gy) tuples
        self._load_warp_paths()
        self._load_tile_pair_collisions()
        
        # Action mapping for navigation paths (UP, DOWN, LEFT, RIGHT strings)
        self.ACTION_MAPPING_STR_TO_INT = {
            "down": 0, "left": 1, "right": 2, "up": 3,
        }

        self.current_navigation_target_local_grid: Optional[tuple[int, int]] = None # (row, col) on 9x10 grid
        self.current_navigation_target_global: Optional[tuple[int, int]] = None # (gx, gy) global coords
        self.current_path_actions: list[str] = [] 
        self.navigation_status: str = "idle" # idle, planning, navigating, completed, failed
        self.last_failed_astar_target: Optional[tuple[int,int]] = None # For loop detection

    def _load_warp_paths(self):
        base_path = Path(__file__).parent 
        warp_file_path = base_path / "replays" / "recordings" / "squirtle_start_to_entering_gym_2_cerulean.json"

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
        collisions_file_path = Path("./tile_pair_collisions.json") # Relative to project root
        if not collisions_file_path.exists():
            # If not in root, try next to play.py (common for local dev/testing)
            collisions_file_path = Path(__file__).parent / "tile_pair_collisions.json"
            
        if collisions_file_path.exists():
            try:
                with open(collisions_file_path, 'r') as f:
                    self.tile_pair_collisions = json.load(f)
                print(f"InteractiveNavigator: Loaded {collisions_file_path} with {len(self.tile_pair_collisions)} tilesets defined.")
            except Exception as e:
                print(f"InteractiveNavigator: Error loading or parsing {collisions_file_path}: {e}")
                self.tile_pair_collisions = {}
        else:
            self.tile_pair_collisions = {}
            print(f"InteractiveNavigator: Warning: Tile pair collision file not found at ./tile_pair_collisions.json or {Path(__file__).parent / 'tile_pair_collisions.json'}. Tile pair collision checking will be permissive.")

    def _get_player_global_coords(self) -> Optional[tuple[int, int]]:
        if not hasattr(self.env, 'get_game_coords'):
            print("InteractiveNavigator: env object does not have get_game_coords method.")
            return None
        try:
            # get_game_coords returns: player_x_local, player_y_local, current_map_id_int
            player_local_x, player_local_y, current_map_id_int = self.env.get_game_coords()
            
            # local_to_global expects (y, x, map_id)
            global_y, global_x = local_to_global(player_local_y, player_local_x, current_map_id_int)
            return int(global_x), int(global_y) # Return as (gx, gy)
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

            # Round delta_grid values: 0.5 away from zero
            # e.g., 0.5 -> 1, -0.5 -> -1, 0.4 -> 0, -0.4 -> 0, 1.0 -> 1
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
            return None # Ensure None is returned if out of bounds
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
        # Access pyboy via self.env.pyboy
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
            # If tileset_name is None or not in our collision data, default to permissive
            # print(f"Warning: Tileset '{tileset_name}' not found in collision data, or no tileset name. Permitting move.")
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
        # Access game_wrapper via self.env.pyboy.game_wrapper
        if not self.env.pyboy or not hasattr(self.env.pyboy, 'game_wrapper') or \
           not hasattr(self.env.pyboy.game_wrapper, 'game_area_collision') or \
           not hasattr(self.env.pyboy.game_wrapper, '_get_screen_background_tilemap'):
            return "Failure: PyBoy game_wrapper or required methods not available.", []

        collision_map_full_res = self.env.pyboy.game_wrapper.game_area_collision()
        if collision_map_full_res is None: return "Failure: Could not get game_area_collision()", []
        
        terrain = self._downsample_array(collision_map_full_res) 
        if terrain is None: return "Failure: Could not downsample collision map", []

        sprite_locations_9x10 = self.get_sprites() 
        full_map_tile_ids = self.env.pyboy.game_wrapper._get_screen_background_tilemap() # (H, W)
        if full_map_tile_ids is None: return "Failure: Could not get _get_screen_background_tilemap()", []
            
        tileset_name_str: Optional[str] = None
        try:
            # Use the memory reading logic provided by the user
            tileset_id = self.pyboy.memory[0xD367] # wTilesetID
            from data.tilesets import Tilesets # Changed to plural Tilesets
            tileset_name_str = Tilesets(tileset_id).name.replace("_", " ") # Changed to plural Tilesets
            if tileset_name_str:
                # print(f"InteractiveNavigator: Successfully read tileset: {tileset_name_str} (ID: {tileset_id})")
                pass # No need to print every time if successful
            else:
                # This case should ideally not happen if Tileset enum is comprehensive
                print(f"InteractiveNavigator: Warning: Tileset ID {tileset_id} resulted in an empty name.")
        except ImportError:
            print("InteractiveNavigator: Warning: Could not import 'Tilesets' from data.tilesets. Tileset-specific collision rules will be disabled.")
        except KeyError: # PyBoy memory access error
            print(f"InteractiveNavigator: Warning: Could not read tileset ID from memory location 0xD367. Tileset-specific collision rules will be disabled.")
        except Exception as e: # Catch other potential errors (e.g., Tileset enum not having the ID)
            print(f"InteractiveNavigator: Warning: Error processing tileset ID: {e}. Tileset-specific collision rules will be disabled.")

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

        while open_set:
            _, current = heapq.heappop(open_set)
            if current == end:
                path = reconstruct_path(current)
                is_wall_at_target = terrain[end[0]][end[1]] == 0 
                return (f"Nav.A*: Success: Path to {'wall' if is_wall_at_target else 'target'} ({target_row},{target_col}).", path)

            current_h_dist = self._heuristic(current, end)
            if current_h_dist < min_distance_to_target:
                closest_point = current
                min_distance_to_target = current_h_dist

            # If current is adjacent to end, and end is a wall, path to current is the best we can do.
            if (abs(current[0] - end[0]) + abs(current[1] - end[1])) == 1 and terrain[end[0]][end[1]] == 0:
                path_to_current = reconstruct_path(current) # Path to the tile *before* the wall
                # No additional step into the wall cell 'end' should be added here.
                return (f"Nav.A*: Success: Path to adjacent to wall ({target_row},{target_col}).", path_to_current)

            for dr, dc, direction_str in [(1, 0, "down"), (-1, 0, "up"), (0, 1, "right"), (0, -1, "left")]:
                neighbor = (current[0] + dr, current[1] + dc)
                nrow, ncol = neighbor
                if not (0 <= nrow < 9 and 0 <= ncol < 10): continue

                # Stricter NPC check: do not allow pathing into any NPC-occupied tile.
                if (ncol, nrow) in sprite_locations_9x10:
                    # If the neighbor is an NPC, and it's also the END goal, still don't go there.
                    # The A* should find a path to a tile *adjacent* to the NPC-occupied end goal.
                    # If the only way to the end goal is through another NPC, that path is invalid.
                    continue 

                # Collision check for terrain (allow moving into 'end' even if it's a wall, A* will handle cost/pathing)
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

        if closest_point != start:
            path = reconstruct_path(closest_point)
            return (f"Nav.A*: Target Unreachable; Path to closest ({closest_point[0]},{closest_point[1]}).", path)
        return ("Nav.A*: Target Unreachable; No path found.", [])

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

        w1 = 0.4  # Weight for distance from player to JSON point
        w2 = 0.6  # Weight for distance from JSON point to ultimate target

        for segment in all_json_segments_for_map: # segment is list[tuple[int,int]]
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
        if not path_segment_for_map: # Handles None or empty list if stored that way
            return f"Nav.obtain_path: No warp path data for map ID {current_map_id_str}.", None
        
        # path_segment_for_map is List[Tuple[int,int]]
        # The expected return type is list[list[tuple[int, int]]] to support multiple segments later if needed.
        # So we wrap the single segment into a list.
        all_segments_on_map = [path_segment_for_map] 
        
        if not all_segments_on_map or not all_segments_on_map[0]: # Check if the list or its first element is empty
             return f"Nav.obtain_path: Map ID {current_map_id_str} found, but path segment is empty.", None

        return f"Nav.obtain_path: Retrieved 1 segment for map {current_map_id_str}.", all_segments_on_map

    def find_the_navigational_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        # THIS METHOD IS LARGELY REPLACED BY THE PLANNING LOGIC IN step()
        # It's kept here for now as a reference or if direct local grid planning (without global context) is called.
        # However, step() should be the primary planner.
        
        # For direct A* to local grid, without complex global/JSON pre-computation:
        status_msg_direct_astar, direct_astar_path_actions = self.make_path_to_target_row_col(target_row, target_col)
        if not direct_astar_path_actions: return f"Nav.find_path (direct A*): Failed to ({target_row},{target_col}). {status_msg_direct_astar}", []
        return f"Nav.find_path (direct A*): Path to ({target_row},{target_col}). {status_msg_direct_astar}", direct_astar_path_actions
            
    def set_navigation_goal_local_grid(self, target_grid_row: int, target_grid_col: int):
        if not (0 <= target_grid_row < 9 and 0 <= target_grid_col < 10):
            print(f"Error: Target local grid coordinates ({target_grid_row}, {target_grid_col}) out of 9x10 bounds.")
            self.navigation_status = "failed"
            return
        self.current_navigation_target_local_grid = (target_grid_row, target_grid_col)
        self.current_navigation_target_global = None # Clear global target
        self.current_path_actions = []
        self.navigation_status = "planning"
        print(f"Navigation goal set to LOCAL GRID: ({target_grid_row}, {target_grid_col}). Status: {self.navigation_status}")

    def set_navigation_goal_global(self, target_gx: int, target_gy: int):
        self.current_navigation_target_global = (target_gx, target_gy)
        self.current_navigation_target_local_grid = None # Clear local grid target
        self.current_path_actions = []
        self.navigation_status = "planning"
        print(f"Navigation goal set to GLOBAL: ({target_gx}, {target_gy}). Status: {self.navigation_status}")

    def step(self) -> str:
        status_message = f"Nav step. Status: {self.navigation_status}."
        
        if self.navigation_status == "planning":
            # Log player's starting global coords for this planning attempt
            player_starting_gx, player_starting_gy = self._get_player_global_coords() or (None, None)
            log_start_coords = f"Player Start Coords: ({player_starting_gx},{player_starting_gy}). " if player_starting_gx is not None else "Player Start Coords: N/A. "

            path_generated = False
            current_planning_log = log_start_coords # Initialize with starting coords
            path_actions_found: list[str] = []
            final_astar_target_rg: Optional[tuple[int, int]] = None # (row, col) for A*
            plan_status_astar = "No A* run" # Default status

            if self.current_navigation_target_global:
                ultimate_gx, ultimate_gy = self.current_navigation_target_global
                current_planning_log += f"Global target ({ultimate_gx},{ultimate_gy})."

                # 1. Try to find an intermediate JSON point that is on-screen
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
                
                # 2. If no on-screen JSON point found or used, try A* directly to the global target if it's on-screen
                if not final_astar_target_rg:
                    direct_local_target_from_global = self._global_to_local_9x10(ultimate_gx, ultimate_gy)
                    if direct_local_target_from_global:
                        final_astar_target_rg = direct_local_target_from_global
                        current_planning_log += f" Attempting direct A* to global target (local: {final_astar_target_rg})."
                    else:
                        current_planning_log += f" Global target ({ultimate_gx},{ultimate_gy}) is also off-screen. Cannot determine on-screen A* target."
                        
            elif self.current_navigation_target_local_grid:
                # Local grid target is set.
                local_target_r, local_target_c = self.current_navigation_target_local_grid
                current_planning_log += f"Local grid target ({local_target_r},{local_target_c})."
                
                # Convert local grid target to global to use with _compute_best_intermediate_json_coord for consistency
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
                
                if not final_astar_target_rg: # If no JSON guidance or it was off-screen
                    final_astar_target_rg = self.current_navigation_target_local_grid # Fallback to direct local target
                    current_planning_log += f" Attempting direct A* to local grid target {final_astar_target_rg}."

            # Perform A* if a local target (final_astar_target_rg) has been determined
            if final_astar_target_rg:
                target_r_astar, target_c_astar = final_astar_target_rg
                plan_status_astar, path_actions_found = self.make_path_to_target_row_col(target_r_astar, target_c_astar)
                current_planning_log += f" A* to ({target_r_astar},{target_c_astar}): {plan_status_astar}."
                
                # Path is generated if A* reports any kind of success OR a path to closest with actual moves
                if ("Success" in plan_status_astar) or \
                   ("Path to closest" in plan_status_astar and path_actions_found):
                    path_generated = True
            else:
                current_planning_log += " No on-screen A* target could be determined."
            
            print(f"Nav Planning Log: {current_planning_log}") # Print detailed log

            # Check for immediate stuck condition from A* leading to a loop
            is_stuck_at_astar_target = (
                "Success" in plan_status_astar and 
                not path_actions_found and 
                final_astar_target_rg is not None and # Ensure we have an A* target to compare
                ("adjacent to wall" in plan_status_astar or "Target Unreachable" in plan_status_astar or "closest" in plan_status_astar)
            )

            if is_stuck_at_astar_target:
                if self.last_failed_astar_target == final_astar_target_rg:
                    print(f"Nav.step: Detected loop for A* target {final_astar_target_rg}. Failing global goal {self.current_navigation_target_global}.")
                    self.navigation_status = "failed"
                    status_message = f"Navigation failed: Stuck in loop for A* target {final_astar_target_rg} towards global {self.current_navigation_target_global}."
                    self.last_failed_astar_target = None # Clear after failing
                else:
                    # Record the A* target we are stuck on, to detect a loop on the *next* planning cycle.
                    self.last_failed_astar_target = final_astar_target_rg
                    # Allow path_generated to remain true if A* reported success, to transition to "completed"
                    # path_generated is already true if "Success" was in plan_status_astar
            else:
                # If not stuck in the specific A* 0-move scenario, or if pathing was successful with moves,
                # clear any previous stuck tracking for a *different* A* target.
                self.last_failed_astar_target = None

            if self.navigation_status != "failed": # If not already failed by loop detection
                if path_generated and final_astar_target_rg is not None: # Make sure final_astar_target_rg is not None
                    self.current_path_actions = path_actions_found
                    self.navigation_status = "navigating"
                    if not path_actions_found: 
                        next_move_msg = "Already at A* target or no path segment moves needed."
                    else:
                        next_move_msg = f"Next: {path_actions_found[0]}"
                    status_message = f"Path planned ({len(path_actions_found)} moves to A* target {final_astar_target_rg}). {next_move_msg}"
                else:
                    self.navigation_status = "failed"
                    # Use the A* status if available, otherwise a generic message
                    astar_detail = f"(A* status: {plan_status_astar})" if plan_status_astar != "No A* run" else ""
                    if not final_astar_target_rg and plan_status_astar == "No A* run":
                        status_message = "Path planning failed: No on-screen A* target could be determined."
                    else:
                        status_message = f"Path planning failed. {astar_detail}"
        
        elif self.navigation_status == "navigating":
            if not self.current_path_actions:
                self.navigation_status = "completed"
                nav_target_str = f"global {self.current_navigation_target_global}" if self.current_navigation_target_global else f"local {self.current_navigation_target_local_grid}"
                return f"Nav to {nav_target_str} path segment presumably completed. Overall goal may not be reached if intermediate."
            
            # Peek at the next move and validate it
            move_action_str_peek = self.current_path_actions[0]
            action_int_peek = self.ACTION_MAPPING_STR_TO_INT.get(move_action_str_peek.lower())

            if action_int_peek is None:
                self.navigation_status = "failed"; self.current_path_actions = []
                return f"Nav failed: Unknown action '{move_action_str_peek}' in path queue."

            player_astar_row, player_astar_col = 4, 4 # Player is always at (4,4) in A* local grid for planning relative moves
            next_astar_row, next_astar_col = player_astar_row, player_astar_col
            if move_action_str_peek == "down": next_astar_row += 1
            elif move_action_str_peek == "up": next_astar_row -=1
            elif move_action_str_peek == "right": next_astar_col += 1
            elif move_action_str_peek == "left": next_astar_col -=1
            
            current_sprites_9x10 = self.get_sprites() # Get fresh sprite locations (grid_col, grid_row)
            if (next_astar_col, next_astar_row) in current_sprites_9x10:
                self.navigation_status = "planning" # Force re-planning due to obstruction
                self.current_path_actions = [] # Clear the old, obstructed path
                # Optionally, store this as a minor type of failure for the current A* target to influence future _compute_best_intermediate_json_coord
                # For now, simple re-plan is fine.
                self.last_failed_astar_target = (next_astar_row, next_astar_col) # Log that this specific A* step failed due to dynamic obstacle
                return f"Nav obstructed: Next step ({move_action_str_peek}) to A* grid ({next_astar_row},{next_astar_col}) blocked by NPC. Re-planning."

            # If move is clear, pop and execute
            move_action_str = self.current_path_actions.pop(0)
            action_int = self.ACTION_MAPPING_STR_TO_INT.get(move_action_str.lower()) # Should be same as peeked
            
            if action_int is None: # Should be caught by peek, but as safeguard
                self.navigation_status = "failed"; self.current_path_actions = []
                return f"Nav failed: Unknown action '{move_action_str}' during pop."
            if not self.env:
                self.navigation_status = "failed"; return "Nav failed: env not available."
            
            try:
                # IMPORTANT: env.step() will be called here for the navigation action
                self.env.step(action_int) 
                status_message = f"Executed nav action: {move_action_str} (int: {action_int}). Remaining: {len(self.current_path_actions)}."
                if not self.current_path_actions: # Path depleted
                    self.navigation_status = "completed" 
                    status_message += f" Current path segment complete."
            except Exception as e:
                self.navigation_status = "failed"
                return f"Nav failed during env.step() for {move_action_str}: {e}"
        return status_message

    def reset_navigation(self):
        self.current_navigation_target_local_grid = None
        self.current_navigation_target_global = None
        self.current_path_actions = []
        self.navigation_status = "idle"
        self.last_failed_astar_target = None # Reset for loop detection
        print("Navigation reset to idle.")

# Function to handle navigation input, to be called from main Pygame loop
def handle_navigation_input_interactive(navigator: InteractiveNavigator):
    try:
        print("\n== SET NAVIGATION TARGET (GLOBAL COORDINATES) ==")
        gx_str = input("Enter target GLOBAL X coordinate: ")
        gy_str = input("Enter target GLOBAL Y coordinate: ")
        target_gx = int(gx_str)
        target_gy = int(gy_str)
        
        # Basic validation if needed, e.g., are they reasonable numbers?
        # For now, we'll assume valid integers.
        
        navigator.set_navigation_goal_global(target_gx, target_gy)
        # The set_navigation_goal_global method will print its own confirmation.

    except ValueError:
        print("Invalid input. Please enter numbers for global X and Y coordinates.")
    except Exception as e:
        print(f"Error setting global navigation target: {e}")


def main():
    parser = argparse.ArgumentParser(description='Play Pokemon Red interactively and record actions')
    parser.add_argument('--rom', type=str, help='Path to the Game Boy ROM file', default="./PokemonRed.gb")
    parser.add_argument('--state', type=str, help='Path to the initial state file (e.g., xxx.state)', default="has_pokedex_nballs") # Default to name, RedGymEnv prepends dir
    parser.add_argument('--name', type=str, help='Name for the output JSON action file', default="playthrough.json")
    args = parser.parse_args()

    env_config_dict = get_default_config(args.rom, args.state)
    
    # Convert to DictConfig for RedGymEnv if it expects it (optional based on RedGymEnv)
    # from omegaconf import DictConfig (add this import if needed)
    # env_config = DictConfig(env_config_dict)
    env_config = env_config_dict # Assuming RedGymEnv can take a dict

    env = RedGymEnv(env_config=env_config)
    navigator = InteractiveNavigator(env) # Initialize navigator
    
    obs, info = env.reset() # Initial reset

    recorded_playthrough = []
    debounce_time = 0.1 # seconds for manual input debounce
    last_action_time = 0

    pygame.init()
    scale_factor = 3
    # Assuming env.render() gives a numpy array HxW or HxWx1 (grayscale) or HxWx3 (RGB)
    # Let's get the shape from an actual render call to be sure
    initial_frame_for_shape = env.render()
    rendered_frame_shape = initial_frame_for_shape.shape 
    
    screen_width = rendered_frame_shape[1] * scale_factor
    screen_height = rendered_frame_shape[0] * scale_factor
    
    screen = pygame.display.set_mode((screen_width, screen_height))
    pygame.display.set_caption('Pokemon Red - Interactive Play & Navigation')
    clock = pygame.time.Clock()

    current_raw_frame = initial_frame_for_shape # Use the frame we already got
    processed_frame_rgb = process_frame_for_pygame(current_raw_frame)
    update_screen(screen, processed_frame_rgb, screen_width, screen_height)

    print("Ready to play Pokemon Red!")
    print("Controls: Arrow keys, A, S (for B), Enter (for Start)")
    print("Press P to take a screenshot.")
    print("Press N to set Navigation Target.")
    print("Press X to reset/cancel Navigation.")
    print("Press ESC to quit and save.")

    running = True
    try:
        while running:
            manual_action_to_take = -1 # Action from player keyboard input
            current_time_sec = pygame.time.get_ticks() / 1000.0
            keys_pressed_this_frame = False

            # Handle Pygame events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_p:
                        # Screenshot logic (remains the same)
                        timestamp = int(time.time())
                        Path("./screenshots_play").mkdir(parents=True, exist_ok=True)
                        screenshot_filename = f"./screenshots_play/screenshot_{timestamp}.png"
                        raw_ss_frame = env.render()
                        processed_ss_frame = process_frame_for_pygame(raw_ss_frame).transpose(1,0,2)
                        ss_surface = pygame.surfarray.make_surface(processed_ss_frame)
                        pygame.image.save(ss_surface, screenshot_filename)
                        print(f"Screenshot saved to {screenshot_filename}")
                    elif event.key == pygame.K_n: # Set Navigation Target
                        handle_navigation_input_interactive(navigator)
                    elif event.key == pygame.K_x: # Reset/Cancel Navigation
                        navigator.reset_navigation()
                        print("Navigation manually reset.")
                    # Check for manual game control keys ONLY if navigation is not actively overriding
                    elif navigator.navigation_status in ["idle", "completed", "failed"]:
                        if current_time_sec - last_action_time > debounce_time: # Check debounce for manual keys
                            if event.key in ACTION_MAPPING_PYGAME_TO_INT:
                                manual_action_to_take = ACTION_MAPPING_PYGAME_TO_INT[event.key]
                                last_action_time = current_time_sec
                                keys_pressed_this_frame = True # Indicate a key was pressed for this type of input

            # Fallback for held keys if not captured by KEYDOWN and debounced
            if not keys_pressed_this_frame and navigator.navigation_status in ["idle", "completed", "failed"]:
                 if current_time_sec - last_action_time > debounce_time:
                    pygame.event.pump() # Ensure queue is processed for get_pressed()
                    keys = pygame.key.get_pressed()
                    for key_code, mapped_action_int in ACTION_MAPPING_PYGAME_TO_INT.items():
                        if keys[key_code]:
                            manual_action_to_take = mapped_action_int
                            last_action_time = current_time_sec
                            break
            
            if not running: break

            # === Navigation Step ===
            action_taken_by_navigator = False
            if navigator.navigation_status not in ["idle", "completed", "failed"]:
                nav_status_msg = navigator.step() # navigator.step() calls env.step()
                if nav_status_msg: print(nav_status_msg)
                # Determine if navigator actually took a game step.
                # The current navigator.step() either plans or executes one env.step().
                # If it executed env.step(), it means an action was taken.
                # We can infer this if status changed from navigating or if path_actions was consumed.
                # For simplicity, if navigator is active, assume it might take an action.
                # More robust: navigator.step() could return a flag if it called env.step().
                if "Executed nav action" in nav_status_msg or "Path planned" in nav_status_msg : # crude check
                     action_taken_by_navigator = True # Assume navigator took control for this frame

            # === Manual Action Step ===
            if not action_taken_by_navigator and manual_action_to_take != -1:
                obs, reward, terminated, truncated, info = env.step(manual_action_to_take)
                done = terminated or truncated
                
                # Recording logic (remains the same)
                current_x, current_y, current_map_id = env.get_game_coords()
                g_y, g_x = local_to_global(current_y, current_x, current_map_id)
                recorded_playthrough.append({
                    "action": manual_action_to_take, "map_id": current_map_id,
                    "local_coords": {"x": current_x, "y": current_y},
                    "global_coords": {"gx": g_x, "gy": g_y},
                })
                if done: print("Episode finished."); running = False
            
            # === Post-action Navigation Status Check ===
            if navigator.navigation_status == "completed":
                nav_target_display = ""
                final_player_coords_str = "Player Coords: N/A"
                player_gx, player_gy = navigator._get_player_global_coords() or (None, None)
                if player_gx is not None and player_gy is not None:
                    final_player_coords_str = f"Player Coords: ({player_gx},{player_gy})"

                if navigator.current_navigation_target_global:
                    nav_target_display = f"GLOBAL {navigator.current_navigation_target_global}"
                    print(f"Navigation path segment towards {nav_target_display} completed. {final_player_coords_str}")
                    # Decide if re-planning is needed or if truly idle
                    # Check if player is at the global target (or close enough)
                    glob_tgt_gx, glob_tgt_gy = navigator.current_navigation_target_global
                    if player_gx is not None and abs(player_gx - glob_tgt_gx) <=1 and abs(player_gy - glob_tgt_gy) <=1: # Arbitrary threshold for "at target"
                        print(f"Player has reached global target {navigator.current_navigation_target_global}. Resetting navigator.")
                        navigator.reset_navigation()
                    else:
                        print(f"Global target {navigator.current_navigation_target_global} not yet reached (at {player_gx},{player_gy}). Re-initiating planning.")
                        navigator.navigation_status = "planning" # Trigger re-planning in the next loop
                elif navigator.current_navigation_target_local_grid:
                    nav_target_display = f"LOCAL {navigator.current_navigation_target_local_grid}"
                    print(f"Navigation path segment towards {nav_target_display} completed. {final_player_coords_str}")
                    navigator.reset_navigation() # Path to local grid target complete, so reset.
                else:
                    nav_target_display = "UNKNOWN TARGET"
                    print(f"Navigation path segment towards {nav_target_display} completed. {final_player_coords_str}")
                    navigator.reset_navigation()
            
            elif navigator.navigation_status == "failed":
                nav_target_display = ""
                final_player_coords_str = "Player Coords: N/A"
                player_gx, player_gy = navigator._get_player_global_coords() or (None, None)
                if player_gx is not None and player_gy is not None:
                    final_player_coords_str = f"Player Coords: ({player_gx},{player_gy})"

                if navigator.current_navigation_target_global:
                    nav_target_display = f"GLOBAL {navigator.current_navigation_target_global}"
                elif navigator.current_navigation_target_local_grid:
                    nav_target_display = f"LOCAL {navigator.current_navigation_target_local_grid}"
                print(f"Navigation failed for {nav_target_display}. {final_player_coords_str}. Navigator reset to idle.")
                navigator.reset_navigation()

            # Render screen
            current_raw_frame = env.render()
            processed_frame_rgb = process_frame_for_pygame(current_raw_frame)
            update_screen(screen, processed_frame_rgb, screen_width, screen_height)
            clock.tick(30) # FPS

    except KeyboardInterrupt:
        print("\nPlay session interrupted by user.")
    finally:
        env.close() # Ensure environment is closed
        pygame.quit()
    
    # Saving playthrough (remains the same)
    if recorded_playthrough:
        output_path = Path(args.name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(recorded_playthrough, f, indent=4)
        print(f"Playthrough actions saved to {output_path}")
    else:
        print("No actions recorded.")

if __name__ == "__main__":
    main()
