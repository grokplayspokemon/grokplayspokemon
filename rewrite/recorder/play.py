import argparse
import numpy as np
import json
import pygame
import time
import sys # Added for sys.path manipulation
import math # Added for custom rounding
from pathlib import Path
from typing import Optional

from environment import RedGymEnv
from pyboy.utils import WindowEvent
from global_map import local_to_global
from navigator import InteractiveNavigator # Added import for the navigator class

# Add project root for global_map import if play.py is in a subdirectory
# Assuming play.py is in DATAPlaysPokemon/rewrite/recorder/
project_root_path = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root_path))

# global_map is used by navigator.py now, but play.py's sys.path manipulation makes it available.
# from global_map import local_to_global, global_to_local # No longer directly used in play.py


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
    # For play.py, only use an initial state name if the file actually exists.
    init_state_name = None
    if initial_state_path:
        p = Path(initial_state_path)
        # Full path to a state file overrides default
        if p.is_file():  # Provided a full path to a .state file
            init_state_name = p.stem
        else:
            # Check default state directory for state file
            state_dir = Path("./states/new")
            state_file = state_dir / f"{initial_state_path}.state"
            if state_file.is_file():
                init_state_name = initial_state_path
            else:
                print(f"Initial state file not found at {state_file}, starting new game.")
                init_state_name = None


    return {
        "video_dir": Path("./videos/play_sessions/"),
        "emulator_delay": 11,
        "headless": False, # play.py is interactive, so headless is False
        "state_dir": Path("./states/new"), # Relative to play.py
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

# InteractiveNavigator CLASS DEFINITION SHOULD BE ENTIRELY REMOVED FROM HERE #
# (Ensuring the diff removes the whole class from its original start to end)

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
    # Capture the run directory for saving action JSON
    run_dir = env.current_run_dir

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

            # === Action Decision and Execution ===
            executed_action_this_frame: Optional[int] = None
            current_obs = obs # Preserve previous observation by default
            current_info = info # Preserve previous info
            current_reward, current_terminated, current_truncated = 0, False, False

            # --- Navigation Step ---
            if navigator.navigation_status not in ["idle", "completed", "failed"]:
                nav_status_msg, nav_executed_action_int, nav_step_results = navigator.step()
                if nav_status_msg: print(nav_status_msg)

                if nav_executed_action_int is not None:
                    executed_action_this_frame = nav_executed_action_int
                    if nav_step_results:
                        current_obs, current_reward, current_terminated, current_truncated, current_info = nav_step_results
                    action_taken_by_navigator = True # Mark that navigator took an action
            else:
                action_taken_by_navigator = False # Navigator was idle/completed/failed

            # --- Manual Action Step ---
            action_taken_by_player_this_turn = False
            if not action_taken_by_navigator and manual_action_to_take != -1:
                current_obs, current_reward, current_terminated, current_truncated, current_info = env.step(manual_action_to_take)
                executed_action_this_frame = manual_action_to_take
                action_taken_by_player_this_turn = True
            
            # Update global obs, info etc. based on what happened
            obs, reward, terminated, truncated, info = current_obs, current_reward, current_terminated, current_truncated, current_info

            # --- Record Action if one was taken ---
            if executed_action_this_frame is not None:
                recorded_playthrough.append(executed_action_this_frame)
                # print(f"Recorded action: {executed_action_this_frame}") # Optional: for debugging

            # === Post-action Navigation Status Check ===
            if navigator.navigation_status == "completed":
                nav_target_display = ""
                final_player_coords_str = "Player Coords: N/A"
                player_gx, player_gy = navigator._get_player_global_coords() or (None, None)
                if player_gx is not None and player_gy is not None:
                    final_player_coords_str = f"Player Coords: ({player_gx},{player_gy})"

                current_global_target_at_completion = navigator.current_navigation_target_global

                if current_global_target_at_completion:
                    nav_target_display = f"GLOBAL {current_global_target_at_completion}"
                    glob_tgt_gx, glob_tgt_gy = current_global_target_at_completion
                    
                    if player_gx is not None and abs(player_gx - glob_tgt_gx) <=1 and abs(player_gy - glob_tgt_gy) <=1: 
                        print(f"Player has reached global target {current_global_target_at_completion}. {final_player_coords_str}. Resetting navigator.")
                        navigator.astar_segment_no_global_progress_count = 0 # Reset for future goals
                        navigator.reset_navigation() # This will clear current_navigation_target_global
                    else:
                        # Global target not reached
                        increment_fail_counter = False
                        current_player_coords = None
                        if player_gx is not None and player_gy is not None:
                            current_player_coords = (player_gx, player_gy)
                            # Add to history if we are pursuing a global target and have valid current coordinates
                            if navigator.current_navigation_target_global: 
                                navigator.add_to_global_nav_history(current_player_coords)
                        
                        # Condition 1: Check Manhattan distance progress
                        if navigator.last_global_pos_before_astar_segment and current_global_target_at_completion and current_player_coords:
                            last_pos = navigator.last_global_pos_before_astar_segment
                            # current_player_coords is already (player_gx, player_gy)
                            global_target = current_global_target_at_completion

                            dist_before = navigator._manhattan_distance(last_pos, global_target)
                            dist_after = navigator._manhattan_distance(current_player_coords, global_target)

                            if dist_after >= dist_before:
                                print(f"Nav Info: No strict distance decrease. Dist before: {dist_before}, after: {dist_after}. Pos before: {last_pos}, after: {current_player_coords}")
                                increment_fail_counter = True
                        elif not navigator.last_global_pos_before_astar_segment and current_player_coords:
                            # First segment attempt for this global goal, no 'before' distance to compare for this segment.
                            # However, oscillation can still be checked if it's the *very first* move for a new global target
                            # and it lands in a spot that was part of a previous failed attempt's history for the *same* target (if history wasn't cleared properly)
                            # But set_navigation_goal_global clears history, so this specific sub-case is less likely.
                            # The primary check here is for oscillations using the history that's now populated with current_player_coords.
                            pass 

                        # Condition 2: Check for oscillation, even if distance seemed to improve or it was the first segment
                        if current_player_coords and navigator.check_recent_oscillation(current_player_coords, count=2):
                            print(f"Nav Info: Oscillation detected for position {current_player_coords}. History: {navigator.global_nav_short_history}")
                            increment_fail_counter = True # Mark as failure if oscillating

                        if increment_fail_counter:
                            navigator.astar_segment_no_global_progress_count += 1
                            print(f"A* segment completed with no significant progress or oscillation. Fail count: {navigator.astar_segment_no_global_progress_count}/{navigator.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD}")
                            if navigator.astar_segment_no_global_progress_count >= navigator.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD:
                                print(f"GLOBAL NAV FAILED: No progress/oscillation after {navigator.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD} attempts for target {current_global_target_at_completion}. {final_player_coords_str}")
                                navigator.reset_navigation() # Full reset, clears global target and history
                            else:
                                print(f"Global target {current_global_target_at_completion} not yet reached. Re-initiating planning (retry {navigator.astar_segment_no_global_progress_count}). {final_player_coords_str}")
                                navigator.navigation_status = "planning" # Re-plan for the same global goal
                        else:
                            # Progress was made (distance decreased AND no oscillation detected)
                            navigator.astar_segment_no_global_progress_count = 0 
                            print(f"Global target {current_global_target_at_completion} not yet reached. Re-initiating planning. {final_player_coords_str}")
                            navigator.navigation_status = "planning" # Re-plan
                elif navigator.current_navigation_target_local_grid: # Completed a local grid nav
                    nav_target_display = f"LOCAL {navigator.current_navigation_target_local_grid}"
                    print(f"Navigation path segment towards {nav_target_display} completed. {final_player_coords_str}")
                    navigator.reset_navigation() 
                else: # Should not happen if a target was set
                    nav_target_display = "UNKNOWN TARGET"
                    print(f"Navigation path segment towards {nav_target_display} completed (no global/local target found?). {final_player_coords_str}")
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
    
    # Saving playthrough actions
    if recorded_playthrough:
        if run_dir:
            # Save actions into the run directory using the CLI-specified name
            actions_file = run_dir / args.name
            # Ensure run directory exists
            run_dir.mkdir(parents=True, exist_ok=True)
            with open(actions_file, "w") as f:
                json.dump(recorded_playthrough, f, indent=4)
            print(f"Playthrough actions saved to {actions_file}")
        else:
            # Fallback to CLI name in current directory
            output_path = Path(args.name)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(recorded_playthrough, f, indent=4)
            print(f"Playthrough actions saved to {output_path}")
    else:
        print("No actions recorded.")

if __name__ == "__main__":
    main()
