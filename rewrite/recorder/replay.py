import argparse
# import heapq # No longer needed for A* here
import json
from pathlib import Path
# import pprint # No longer needed
# from typing import Optional, Union, Any # No longer needed
import numpy as np
import pygame
# import io # No longer needed
from datetime import datetime
import yaml
from omegaconf import DictConfig

import sys
# Add project root (DATAPlaysPokemon) to sys.path
# Assuming replay.py is in DATAPlaysPokemon/rewrite/recorder/
project_root_path = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root_path))

# from map_data import map_locations # Removed unused import
from rewrite.recorder.environment import RedGymEnv
# from pyboy.utils import WindowEvent # Not directly used now
from stats_wrapper import StatsWrapper # Keep if used independently
# from global_map import local_to_global, global_to_local # No longer needed here


def print_info_nicely(info):
    # Find the longest key length for proper alignment
    if not info:
        print("Info: (empty)")
        return
    max_key_len = max(len(key) for key in info.keys())
    
    print("--- Game Info ---")
    for key, value in info.items():
        if isinstance(value, dict):
            print(f"{key:<{max_key_len}}: (see below)")
            for sub_key, sub_value in value.items():
                print(f"  {sub_key:<{max_key_len-2}}: {sub_value}")
        elif isinstance(value, list):
            print(f"{key:<{max_key_len}}: (see below)")
            for i, item in enumerate(value):
                print(f"  [{i}]: {item}")
        else:
            print(f"{key:<{max_key_len}}: {value}")
    print("-----------------")

def get_default_config(rom_path, initial_state_path):
    # If initial_state_path is None, default to "has_pokedex_nballs"
    # Otherwise, use the stem of the provided path.
    default_init_state_name = "has_pokedex_nballs"
    current_init_state_name = Path(initial_state_path).stem if initial_state_path else default_init_state_name

    return {
        "video_dir": Path("./videos/play_sessions/"),
        "emulator_delay": 11,
        "headless": False,
        "state_dir": Path("./states/"),
        "init_state": current_init_state_name,
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
        "disable_ai_actions": True,
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

def update_screen(screen, frame_surface, screen_width, screen_height):
    # The new frame_surface is already a Pygame surface
    screen.blit(pygame.transform.scale(frame_surface, (screen_width, screen_height)), (0, 0))
    pygame.display.flip()

def process_frame_for_pygame(frame_from_env_render, target_resolution=(160,144)):
    # env.render() returns a 2D numpy array (H, W) of type uint8 (grayscale)
    # Pygame needs a 3D array (H, W, 3) for color, or use a grayscale palette.
    
    if not isinstance(frame_from_env_render, np.ndarray):
        # print(f"Warning: frame is not a numpy array, type: {type(frame_from_env_render)}")
        # Attempt to handle if it's already an image or surface (though env.render() should be numpy)
        if hasattr(frame_from_env_render, 'convert_alpha'): # Pygame surface
            return frame_from_env_render
        try:
            # Try to convert from PIL image if that's what came through
            frame_from_env_render = np.array(frame_from_env_render)
        except:
            # Fallback: create a black frame
            print("Error: Could not convert frame to numpy array. Using black frame.")
            black_frame = np.zeros((target_resolution[1], target_resolution[0]), dtype=np.uint8)
            frame_rgb = np.stack((black_frame,) * 3, axis=-1)
            return pygame.surfarray.make_surface(frame_rgb.swapaxes(0, 1))


    if frame_from_env_render.ndim == 2: # Grayscale H, W
        frame_rgb = np.stack((frame_from_env_render,) * 3, axis=-1) # H, W, 3
    elif frame_from_env_render.ndim == 3 and frame_from_env_render.shape[2] == 1: # Grayscale H, W, 1
        frame_rgb = np.concatenate([frame_from_env_render] * 3, axis=-2) # H, W, 3
    elif frame_from_env_render.ndim == 3 and frame_from_env_render.shape[2] == 3: # Already RGB H, W, 3
        frame_rgb = frame_from_env_render
    else:
        # Fallback: create a black frame if format is unexpected
        print(f"Error: Unexpected frame format: {frame_from_env_render.shape}. Using black frame.")
        black_frame = np.zeros((target_resolution[1], target_resolution[0]), dtype=np.uint8) # H, W
        frame_rgb = np.stack((black_frame,) * 3, axis=-1) # H, W, 3

    # Pygame wants (width, height, channels) for make_surface from array,
    # but surfarray.make_surface expects array with shape (W,H) or (W,H,3) where W is index 0
    # Numpy arrays are typically (H,W,C). So, we need to swap axes.
    frame_surface = pygame.surfarray.make_surface(frame_rgb.swapaxes(0, 1))
    return frame_surface

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=str, help="Path to the replay run directory")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument('--wait', type=int, default=0, help='Time to wait between frames in ms')
    parser.add_argument('--config_file', type=str, default='../config.yaml')

    args = parser.parse_args()

    # Determine run directory and files
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"Run directory not found: {run_dir}")
        return
    run_name = run_dir.name

    # Load initial state if available
    state_file = run_dir / f"{run_name}.state"
    if state_file.is_file():
        try:
            state_bytes = state_file.read_bytes()
            options = {"state": state_bytes}
        except Exception as e:
            print(f"Error reading initial state file {state_file}: {e}. Starting new game.")
            options = {}
    else:
        print(f"Initial state file not found at {state_file}. Starting new game.")
        options = {}

    # Load recorded actions JSON: look for playthrough.json, then specific or generic fallbacks
    candidates = [
        run_dir / "playthrough.json",
        run_dir / f"{run_name}_actions.json",
        run_dir / "actions.json",
    ]
    actions_file = None
    for candidate in candidates:
        if candidate.is_file():
            actions_file = candidate
            break
    if not actions_file:
        print(f"Actions file not found in run directory: {run_dir}. Checked: {', '.join(str(c) for c in candidates)}")
        return
    with open(actions_file, "r") as f:
        action_list = json.load(f)

    # 1. Establish base configuration from replay.py's own defaults
    # Use args.init_state for initial_state_path and a default for rom_path.
    # The actual gb_path used by RedGymEnv will be resolved from the final merged config.
    default_rom_for_base_config = "./PokemonRed.gb" # A sensible default for generating base config
    
    base_env_config_dict = get_default_config(
        rom_path=default_rom_for_base_config,
        initial_state_path=None
    )

    # 2. Load configuration from YAML file, if specified
    env_config_from_yaml = {}
    config_path = Path(args.config_file)
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                yaml_full_config = yaml.safe_load(f)
            if yaml_full_config and isinstance(yaml_full_config.get('env_config'), dict):
                env_config_from_yaml = yaml_full_config['env_config']
            elif yaml_full_config:
                print(f"Warning: 'env_config' section not found or not a dict in {config_path}. Using base defaults and CLI args.")
            else:
                print(f"Warning: Config file {config_path} is empty. Using base defaults and CLI args.")
        except Exception as e:
            print(f"Error loading or parsing config file {config_path}: {e}. Using base defaults and CLI args.")
    else:
        print(f"Warning: Config file {config_path} not found. Using base defaults and CLI arguments.")

    # 3. Merge configurations: Start with base, then update with YAML overrides
    final_merged_config_dict = base_env_config_dict.copy()
    final_merged_config_dict.update(env_config_from_yaml)

    # 4. Apply specific CLI argument overrides (highest precedence for these)
    if args.headless is not None: # Check if specified, as action="store_true" defaults to False if not present
        final_merged_config_dict['headless'] = args.headless
    
    # For replay, we derive state solely from options, so disable config init_state
    final_merged_config_dict['init_state'] = None
    # Disable recording for replay mode
    final_merged_config_dict['record_replays'] = False
    
    # gb_path precedence: YAML > default from get_default_config.
    # No direct CLI arg for gb_path in replay.py.

    env_config_for_redgymenv = DictConfig(final_merged_config_dict)

    # Initialize Pygame and Environment
    # Use the 'headless' value from the final merged config for consistency
    if not final_merged_config_dict.get('headless', False): 
        pygame.init()
        screen_width = 3 * 160  # Scaled screen width
        screen_height = 3 * 144  # Scaled screen height
        screen = pygame.display.set_mode((screen_width, screen_height))
        pygame.display.set_caption("Pokemon Red Replay")
        clock = pygame.time.Clock()
    else:
        screen = None # No screen if headless
        clock = None

    # env = RedGymEnv(config={"headless": args.headless, "init_state": args.init_state, "gb_path": args.rom}) # Old
    env = RedGymEnv(env_config=env_config_for_redgymenv) 
    # ReplayExtender instance removed: # extender = ReplayExtender(pyboy_instance=env.pyboy, env_instance=env)

    # stats_wrapper = StatsWrapper(pyboy) # Old, pyboy is now env.pyboy
    stats_wrapper = StatsWrapper(env.pyboy) # Updated if StatsWrapper is still needed

    current_action_index = 0
    paused = False
    running = True
    
    # Initial observation and screen render
    obs, info = env.reset(options=options)
    if not args.headless and screen:
        raw_frame = env.render()
        frame_surface = process_frame_for_pygame(raw_frame)
        update_screen(screen, frame_surface, screen_width, screen_height)

    try:
        while running and current_action_index < len(action_list):
            if not args.headless and screen: # Ensure screen exists before processing events
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            running = False
                        elif event.key == pygame.K_SPACE:
                            paused = not paused
                            print("Paused" if paused else "Resumed")
                        elif event.key == pygame.K_RIGHT and paused: 
                            if current_action_index < len(action_list):
                                action_item_paused = action_list[current_action_index]
                                if isinstance(action_item_paused, dict) and "action" in action_item_paused:
                                    action_to_send_paused = action_item_paused["action"]
                                elif isinstance(action_item_paused, int):
                                    action_to_send_paused = action_item_paused
                                else:
                                    print(f"Error: Paused step: Action item at index {current_action_index} has unexpected format: {action_item_paused}")
                                    running = False
                                    continue
                                
                                print(f"Step Replay: Action {action_to_send_paused} ({type(action_to_send_paused)})")
                                obs, reward, terminated, truncated, info = env.step(action_to_send_paused) # new gymnasium, expects 5 values
                                current_action_index += 1
                                if not args.headless:
                                    raw_frame = env.render()
                                    frame_surface = process_frame_for_pygame(raw_frame)
                                    update_screen(screen, frame_surface, screen_width, screen_height)
                                    print_info_nicely(info if info else {})
                                if terminated or truncated:
                                    print("Environment terminated or truncated during replay.")
                                    running = False
                            else:
                                print("End of replay reached.")

            if not paused and running:
                # === Automated Navigation Step === REMOVED
                # if extender.navigation_status not in ["idle", "completed", "failed"]:
                #    nav_status_msg = extender.step()
                #    if nav_status_msg:
                #        print(nav_status_msg)
                # elif extender.navigation_status == "completed":
                #    print(f"Nav completed for {extender.current_navigation_target_grid}. Waiting for new goal. (Press W)")
                #    extender.reset_navigation() 
                # elif extender.navigation_status == "failed":
                #    print(f"Nav failed for {extender.current_navigation_target_grid}. Resetting. (Press W for new goal)")
                #    extender.reset_navigation()
                
                # === Replay Action Step ===
                # Only execute replayed action (navigation status check is removed)
                if current_action_index < len(action_list):
                    action_item = action_list[current_action_index]
                    if isinstance(action_item, dict) and "action" in action_item:
                        action_to_send = action_item["action"]
                    elif isinstance(action_item, int): # Handles older formats if they just store int
                        action_to_send = action_item
                    else:
                        print(f"Error: Action item at index {current_action_index} has unexpected format: {action_item}")
                        running = False # Stop replay if action format is wrong
                        continue

                    # print(f"Replay: Action {action_to_send}")
                    
                    obs, reward, terminated, truncated, info = env.step(action_to_send) # new gymnasium, expects 5 values
                    
                    current_action_index += 1

                    if not args.headless:
                        # print_info_nicely(info if info else {}) # Print after render
                        pass

                    if terminated or truncated:
                        print("Environment terminated or truncated during replay.")
                        running = False
                elif current_action_index >= len(action_list):
                    print("End of replay and no active navigation. Press ESC to quit.")
                    # Keep running for interactive navigation post-replay
                    paused = True # Pause to allow user to interact or quit


            if not args.headless:
                raw_frame = env.render()
                frame_surface = process_frame_for_pygame(raw_frame)
                update_screen(screen, frame_surface, screen_width, screen_height)
                if not paused and info: # Only print game info if not paused
                     print_info_nicely(info if info else {})
                clock.tick(60) # Limit FPS
            
            if args.wait > 0 and not paused : # only wait if not paused
                pygame.time.wait(args.wait)


    except KeyboardInterrupt:
        print("Replay interrupted by user.")
    finally:
        print("Stopping environment...")
        env.close()
        if not args.headless:
            pygame.quit()
        print("Replay finished.")
        # Save ending game state to run directory
        try:
            timestamp = datetime.now().strftime("%m%d%Y_%H%M%S")
            end_state_name = f"{run_name}__{timestamp}.state"
            end_state_path = run_dir / end_state_name
            run_dir.mkdir(parents=True, exist_ok=True)
            with open(end_state_path, "wb") as f_end:
                env.pyboy.save_state(f_end)
            print(f"Saved ending game state to {end_state_path}")
        except Exception as e:
            print(f"Error saving ending game state: {e}")

if __name__ == "__main__":
    main()
