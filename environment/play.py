# play.py - CLEANED VERSION (showing only key functions)
import argparse
import numpy as np
import json
import pygame
import time
import sys
import math
import os
from pathlib import Path
from typing import Optional
from omegaconf import OmegaConf, DictConfig
import threading
import queue
import tkinter as tk
from tkinter import ttk
from environment.environment_helpers.trigger_evaluator import TriggerEvaluator
from environment.environment_helpers.quest_progression import QuestProgressionEngine
from queue import SimpleQueue
from datetime import datetime

from environment.wrappers.env_wrapper import EnvWrapper
from environment.wrappers.configured_env_wrapper import ConfiguredEnvWrapper
from environment.environment import VALID_ACTIONS, PATH_FOLLOW_ACTION
from pyboy.utils import WindowEvent
from environment.data.recorder_data.global_map import local_to_global
from environment.environment_helpers.navigator import InteractiveNavigator, diagnose_environment_coordinate_loading, debug_coordinate_system
from environment.environment_helpers.saver import save_initial_state, save_loop_state, save_final_state, load_latest_run, create_new_run
from environment.environment_helpers.warp_tracker import record_warp_step, backtrack_warp_sequence
from environment.environment_helpers.quest_manager import QuestManager, verify_quest_system_integrity, determine_starting_quest
from ui.quest_ui import start_quest_ui
from environment.environment_helpers.trigger_evaluator import TriggerEvaluator
from environment.grok_integration import extract_structured_game_state
from agent.simple_agent import SimpleAgent

project_root_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root_path))

# Load quest definitions
QUESTS_FILE = Path(__file__).parent / "environment_helpers" / "required_completions.json"
with open(QUESTS_FILE, 'r') as f:
    QUESTS = json.load(f)

# Diagnostic functions moved to navigator.py

# Quest functions moved to quest_manager.py

def execute_action_step(env, action, quest_manager=None, navigator=None, logger=None, total_steps=0):
    """
    Centralized action execution function that ensures all environment systems stay synchronized.
    This is the ONLY function that should call env.step() to prevent desynchronization.
    
    Args:
        env: The environment wrapper
        action: The action to execute
        quest_manager: Quest manager instance (optional)
        navigator: Navigator instance (optional) 
        logger: Logger instance (optional)
        total_steps: Current total step count
        
    Returns:
        tuple: (obs, reward, terminated, truncated, info, updated_total_steps)
    """
    try:
        # Execute the action in the environment - THE ONLY PLACE env.process_action() SHOULD BE CALLED
        obs, reward, terminated, truncated, info = env.process_action(action, source="PlayLoop")
        total_steps += 1
        
        # Update all environment systems that depend on the step
        # Note: update_after_step methods don't currently exist, but we'll check for them
        if quest_manager:
            try:
                if hasattr(quest_manager, 'update_after_step'):
                    quest_manager.update_after_step(obs, reward, terminated, truncated, info)
                # Fallback to existing methods
                elif hasattr(quest_manager, 'update_progress'):
                    quest_manager.update_progress()
            except Exception as e:
                if logger:
                    logger.warning(f"Quest manager update failed: {e}")
        
        if navigator:
            try:
                if hasattr(navigator, 'update_after_step'):
                    navigator.update_after_step(obs, reward, terminated, truncated, info)
                # Navigator might not need step-by-step updates
            except Exception as e:
                if logger:
                    logger.warning(f"Navigator update failed: {e}")
        
        # Log the action if logger is available
        if logger:
            logger.debug(f"Executed action {action} at step {total_steps}")
            
        return obs, reward, terminated, truncated, info, total_steps
        
    except Exception as e:
        if logger:
            logger.error(f"Error executing action {action}: {e}")
        # Return safe defaults
        return None, 0.0, True, True, {}, total_steps

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

def get_default_config(rom_path, initial_state_path, infinite_money=False, infinite_health=False, emulator_delay=11,
                       disable_wild_encounters=False, auto_teach_cut=True, auto_use_cut=True, auto_teach_surf=True, auto_use_surf=True,
                       auto_teach_strength=True, auto_use_strength=True, auto_solve_strength_puzzles=True,
                       auto_remove_all_nonuseful_items=False, auto_pokeflute=True, auto_next_elevator_floor=False,
                       skip_safari_zone=False, infinite_safari_steps=False, insert_saffron_guard_drinks=False,
                       save_video=False, fast_video=False, n_record=0, perfect_ivs=False, reduce_res=False,
                       log_frequency=1000, two_bit=False, auto_flash=False, required_tolerance=None,
                       disable_ai_actions=True, use_global_map=False, save_state=True, animate_scripts=True, exploration_inc=0.01, exploration_max=1.0,
                       max_steps_scaling=0.0, map_id_scalefactor=1.0):
    initial_state_name = None
    if initial_state_path:
        p = Path(initial_state_path)
        if p.suffix == '.state' and p.exists():
            initial_state_name = str(p)
        elif p.exists(): # a directory or stem name
            initial_state_name = p.stem
        # else: initial_state_name remains None, indicating path not found or not usable

    env_config = {
        "headless": False,
        "save_video": save_video,
        "fast_video": fast_video,
        "action_freq": 24,
        "init_state": initial_state_name,
        "state_dir": str(Path(__file__).parent / "states"),
        "video_dir": str(Path(__file__).parent / "replays" / "videos"),
        "gb_path": rom_path, # Will be overridden by YAML or CLI if provided
        "debug": False,
        "sim_frame_dist": 2_000_000.0,
        "max_steps": 2048 * 100,
        "save_final_state": True,
        "print_rewards": True,
        # "mapping_file": str(Path(__file__).resolve().parent.parent.parent / "mapping.txt"), # Ensure this path is correct if used
        "session_id": None, # To be set by _setup_configuration
        "emulator_delay": emulator_delay,
        "n_record": n_record,
        "perfect_ivs": perfect_ivs,
        "reduce_res": reduce_res,
        "log_frequency": log_frequency,
        "two_bit": two_bit,
        "auto_flash": auto_flash,
        "required_tolerance": required_tolerance,
        "disable_wild_encounters": disable_wild_encounters,
        "disable_ai_actions": disable_ai_actions,
        "auto_teach_cut": auto_teach_cut,
        "auto_teach_surf": auto_teach_surf,
        "auto_teach_strength": auto_teach_strength,
        "auto_use_cut": auto_use_cut,
        "auto_use_strength": auto_use_strength,
        "auto_use_surf": auto_use_surf,
        "auto_solve_strength_puzzles": auto_solve_strength_puzzles,
        "auto_remove_all_nonuseful_items": auto_remove_all_nonuseful_items,
        "auto_pokeflute": auto_pokeflute,
        "auto_next_elevator_floor": auto_next_elevator_floor,
        "skip_safari_zone": skip_safari_zone,
        "infinite_safari_steps": infinite_safari_steps,
        "insert_saffron_guard_drinks": insert_saffron_guard_drinks,
        "infinite_money": infinite_money,
        "infinite_health": infinite_health,
        "use_global_map": use_global_map,
        "save_state": save_state,
        "animate_scripts": animate_scripts,
        "exploration_inc": exploration_inc,
        "exploration_max": exploration_max,
        "max_steps_scaling": max_steps_scaling,
        "map_id_scalefactor": map_id_scalefactor,
        "record_replays": False
    }
    return OmegaConf.create(env_config)

def _setup_configuration(args, project_root_path):
    yaml_config = OmegaConf.load(args.config_path) if args.config_path and Path(args.config_path).exists() else OmegaConf.create()

    initial_state_from_yaml = yaml_config.get('env_config', {}).get('init_state')
    final_initial_state_path = args.initial_state_path
    if not final_initial_state_path and not initial_state_from_yaml:
        final_initial_state_path = str(project_root_path / "initial_states" / "init.state")
    elif initial_state_from_yaml and not args.initial_state_path:
        final_initial_state_path = initial_state_from_yaml
    
    default_cli_config = get_default_config(
        rom_path=args.rom_path,
        initial_state_path=final_initial_state_path,
        infinite_money=args.infinite_money,
        infinite_health=args.infinite_health,
        emulator_delay=args.emulator_delay,
        disable_wild_encounters=args.disable_wild_encounters,
        auto_teach_cut=args.auto_teach_cut,
        auto_use_cut=args.auto_use_cut,
        auto_teach_surf=args.auto_teach_surf,
        auto_use_surf=args.auto_use_surf,
        auto_teach_strength=args.auto_teach_strength,
        auto_use_strength=args.auto_use_strength,
        auto_solve_strength_puzzles=args.auto_solve_strength_puzzles,
        auto_remove_all_nonuseful_items=args.auto_remove_all_nonuseful_items,
        auto_pokeflute=args.auto_pokeflute,
        auto_next_elevator_floor=args.auto_next_elevator_floor,
        skip_safari_zone=args.skip_safari_zone,
        infinite_safari_steps=args.infinite_safari_steps,
        insert_saffron_guard_drinks=args.insert_saffron_guard_drinks,
        save_video=args.save_video,
        fast_video=args.fast_video,
        n_record=args.n_record,
        perfect_ivs=args.perfect_ivs,
        reduce_res=args.reduce_res,
        log_frequency=args.log_frequency,
        two_bit=args.two_bit,
        auto_flash=args.auto_flash,
        required_tolerance=args.required_tolerance,
        disable_ai_actions=args.disable_ai_actions,
        use_global_map=args.use_global_map,
        save_state=args.save_state,
        animate_scripts=args.animate_scripts,
        exploration_inc=args.exploration_inc,
        exploration_max=args.exploration_max,
        max_steps_scaling=args.max_steps_scaling,
        map_id_scalefactor=args.map_id_scalefactor
    )

    # Patch any None values in default_cli_config with true defaults
    _true_defaults = get_default_config(rom_path=args.rom_path, initial_state_path=final_initial_state_path)
    if default_cli_config.exploration_inc is None:
        default_cli_config.exploration_inc = _true_defaults.exploration_inc
    if default_cli_config.exploration_max is None:
        default_cli_config.exploration_max = _true_defaults.exploration_max
    if default_cli_config.max_steps_scaling is None:
        default_cli_config.max_steps_scaling = _true_defaults.max_steps_scaling
    if default_cli_config.map_id_scalefactor is None:
        default_cli_config.map_id_scalefactor = _true_defaults.map_id_scalefactor
    
    config_parts = [default_cli_config]
    # Merge YAML environment settings: prefer 'env' section if present, else 'env_config'
    if 'env' in yaml_config:
        config_parts.append(yaml_config.env)
    elif 'env_config' in yaml_config:
        config_parts.append(yaml_config.env_config)

    cli_overrides = {}
    for arg_name, arg_value in vars(args).items():
        # Only consider args that are explicitly set (not None) and are relevant to env_config
        if arg_value is not None and arg_name not in ["config_path", "required_completions_path", "max_total_steps", "interactive_mode", "run_dir", "grok_api_key"]: # Exclude non-env_config args
            mapped_key = arg_name
            if arg_name == "rom_path": # Map rom_path to gb_path for config
                mapped_key = "gb_path"
            elif arg_name == "initial_state_path": # Map initial_state_path to init_state
                mapped_key = "init_state"
            
            # Only add to overrides if it's a known key in default_cli_config or the mapped key is
            # and the arg_value is not None (meaning it was explicitly set by the user via CLI)
            if mapped_key in default_cli_config or arg_name in default_cli_config:
                 cli_overrides[mapped_key if mapped_key in default_cli_config else arg_name] = arg_value
    
    if cli_overrides:
        # Create a temporary config with env_config structure for merging
        override_conf = OmegaConf.create({"env_config": cli_overrides})
        config_parts.append(override_conf.env_config)
    
    final_config = OmegaConf.merge(*config_parts)

    # Ensure gb_path is correctly set, CLI > YAML > Default
    if args.rom_path:
        final_config.gb_path = args.rom_path
    elif yaml_config.get('env_config', {}).get('gb_path'):
        final_config.gb_path = yaml_config.env_config.gb_path
    # Default is already set by get_default_config if neither CLI nor YAML provides it

    # Final resolution for init_state from Path object to string if necessary
    # Priority: CLI > YAML > Default derived from final_initial_state_path
    if args.initial_state_path:
        p = Path(args.initial_state_path)
        if p.suffix == '.state' and p.exists(): final_config.init_state = str(p)
        elif p.exists(): final_config.init_state = p.stem
        else: final_config.init_state = None
    elif initial_state_from_yaml:
        p = Path(initial_state_from_yaml)
        if p.suffix == '.state' and p.exists(): final_config.init_state = str(p)
        elif p.exists(): final_config.init_state = p.stem
        else: final_config.init_state = None
    elif final_initial_state_path: # Default logic
        p = Path(final_initial_state_path)
        if p.suffix == '.state' and p.exists(): final_config.init_state = str(p)
        elif p.exists(): final_config.init_state = p.stem
        else: final_config.init_state = None


    if isinstance(final_config.get("init_state"), Path): # Should be string by now zz
        final_config.init_state = str(final_config.init_state)
    
    # Ensure session_id is present
    if 'session_id' not in final_config or not final_config.session_id:
        final_config.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    # Ensure mapping_file path is resolved correctly if used
    if 'mapping_file' in final_config and final_config.mapping_file:
        # Example: resolve relative to project_root_path if it's a relative path
        # This depends on how mapping_file is intended to be specified
        pass

    return final_config

ACTION_MAPPING_PYGAME_TO_INT = {
    pygame.K_DOWN: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN),
    pygame.K_LEFT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT),
    pygame.K_RIGHT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT),
    pygame.K_UP: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP),
    pygame.K_a: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A),
    pygame.K_s: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B),
    pygame.K_RETURN: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START),
    pygame.K_5: PATH_FOLLOW_ACTION, # Assuming PATH_FOLLOW_ACTION is defined
}

def main():
    parser = argparse.ArgumentParser()
    # Removed --run_dir argument - use config only
    parser.add_argument("--config_path", type=str, default=str(project_root_path / "config.yaml"))
    parser.add_argument("--rom_path", type=str, default=None) # Default to None, expect from YAML or error
    parser.add_argument("--initial_state_path", type=str, default="") # Default to empty, logic in _setup_configuration handles it
    parser.add_argument("--save_video", type=bool, default=None) # Use None to let config file or default take precedence
    parser.add_argument("--fast_video", type=bool, default=None)
    parser.add_argument("--n_record", type=int, default=None)
    parser.add_argument("--perfect_ivs", type=bool, default=None)
    parser.add_argument("--log_frequency", type=int, default=None)
    parser.add_argument("--two_bit", type=bool, default=None)
    parser.add_argument("--disable_ai_actions", type=bool, default=None)
    parser.add_argument("--use_global_map", type=bool, default=None)
    parser.add_argument("--save_state", type=bool, default=None)
    parser.add_argument("--required_completions_path", type=str, default=str(Path(__file__).parent / "environment_helpers" / "required_completions.json"))
    parser.add_argument("--max_total_steps", type=int, default=1_000_000)
    parser.add_argument("--interactive_mode", type=bool, default=True) # Default to True for manual testing
    parser.add_argument("--infinite_money", type=bool, default=None)
    parser.add_argument("--infinite_health", type=bool, default=None)
    parser.add_argument("--emulator_delay", type=int, default=None)
    parser.add_argument("--disable_wild_encounters", type=bool, default=None)
    parser.add_argument("--auto_teach_cut", type=bool, default=None)
    parser.add_argument("--auto_use_cut", type=bool, default=None)
    parser.add_argument("--auto_teach_surf", type=bool, default=None)
    parser.add_argument("--auto_use_surf", type=bool, default=None)
    parser.add_argument("--auto_teach_strength", type=bool, default=None)
    parser.add_argument("--auto_use_strength", type=bool, default=None)
    parser.add_argument("--auto_solve_strength_puzzles", type=bool, default=None)
    parser.add_argument("--auto_remove_all_nonuseful_items", type=bool, default=None)
    parser.add_argument("--auto_pokeflute", type=bool, default=None)
    parser.add_argument("--auto_next_elevator_floor", type=bool, default=None)
    parser.add_argument("--skip_safari_zone", type=bool, default=None)
    parser.add_argument("--infinite_safari_steps", type=bool, default=None)
    parser.add_argument("--insert_saffron_guard_drinks", type=bool, default=None)
    parser.add_argument("--reduce_res", type=bool, default=None)
    parser.add_argument("--auto_flash", type=bool, default=None)
    parser.add_argument("--required_tolerance", type=float, default=None)
    parser.add_argument("--animate_scripts", type=bool, default=None)
    parser.add_argument("--exploration_inc", type=float, default=None)
    parser.add_argument("--exploration_max", type=float, default=None)
    parser.add_argument("--max_steps_scaling", type=float, default=None)
    parser.add_argument("--map_id_scalefactor", type=float, default=None)
    parser.add_argument("--grok_api_key", type=str, default=None) # Keep for potential future use

    args = parser.parse_args()

    config = _setup_configuration(args, project_root_path)
    
    # Initialize logging system early
    import sys
    import os
    sys.path.append('/puffertank/grok_plays_pokemon')
    from utils.logging_config import setup_logging, get_pokemon_logger
    
    # Setup logging with overwrite mode (easier management per user request)
    logger = setup_logging(logs_dir="logs", overwrite_logs=True, redirect_stdout=False)
    logger.log_system_event("Play.py main() starting", {
        'config_path': args.config_path,
        'interactive_mode': args.interactive_mode
    })
    
    # If disable_ai_actions not set by CLI, use configuration default to disable AI actions
    if args.disable_ai_actions is None:
        args.disable_ai_actions = bool(config.get("disable_ai_actions", True))
    # If interactive_mode not set by CLI, use configuration default for interactive mode
    if args.interactive_mode is None:
        args.interactive_mode = bool(config.get("interactive_mode", True))

    # Initialize the environment using ConfiguredEnvWrapper
    # Pass the fully resolved config and original cli_args
    env = ConfiguredEnvWrapper(base_conf=config, cli_args=args) 

    # FIXED: Disable environment's automatic run creation since play.py manages it
    if hasattr(env, 'record_replays'):
        env.record_replays = False
    if hasattr(env, 'env') and hasattr(env.env, 'record_replays'):
        env.env.record_replays = False

    # FIXED: Load persistent step counter
    step_counter_file = Path("total_steps.json")
    total_steps = 0
    try:
        if step_counter_file.exists():
            with open(step_counter_file, 'r') as f:
                step_data = json.load(f)
                total_steps = step_data.get('total_steps', 0)
                print(f"Loaded persistent step counter: {total_steps}")
    except Exception as e:
        print(f"Could not load step counter: {e}")

    # Create or load run using RunManager - FIXED: Only create/load once
    from environment.environment_helpers.saver import create_new_run, load_latest_run
    
    # --- CRITICAL SECTION FOR INITIALIZATION ORDER ---
    
    # 1. Reset the environment FIRST to load state and get initial info
    # This call to env.reset() is where 'loaded_quest_statuses' and 
    # 'loaded_trigger_statuses' are populated in the env object's persisted attributes if a load occurs.
    obs, info = env.reset() 

    print(f"play.py: main(): Received info dictionary from env.reset(): {info}") # Keep for general debugging

    # Get current map ID after reset for run creation
    current_map_id_after_reset = env.get_game_coords()[2]

    # 2. Use the environment's persisted statuses for initialization.
    # These are populated by env.reset() if a state (and its quest/trigger data) was loaded.
    # If no load, they default to {} or what was set in env.__init__.
    initial_quest_statuses_from_save = env.persisted_loaded_quest_statuses if env.persisted_loaded_quest_statuses is not None else {}
    initial_trigger_statuses_from_save = env.persisted_loaded_trigger_statuses if env.persisted_loaded_trigger_statuses is not None else {}
    
    # FIX: Convert old list format to new dictionary format for backwards compatibility
    if isinstance(initial_trigger_statuses_from_save, list):
        print(f"play.py: Converting old list format trigger statuses to dictionary format")
        old_list = initial_trigger_statuses_from_save
        initial_trigger_statuses_from_save = {trigger_id: True for trigger_id in old_list}
        print(f"play.py: Converted {len(old_list)} trigger IDs from list to dictionary")
    
    print(f"play.py: Sourced initial_quest_statuses_from_save from env: {initial_quest_statuses_from_save}")
    print(f"play.py: Sourced initial_trigger_statuses_from_save from env: {initial_trigger_statuses_from_save}")

    # 3. Initialize other components like Pygame screen, status_queue, all_quest_ids BEFORE QuestProgressionEngine
    status_queue = SimpleQueue()
    all_quest_ids = [int(q["quest_id"]) for q in QUESTS]

    # 4. Initialize Navigator (must be initialized before use)
    navigator = InteractiveNavigator(env)
    if hasattr(env, 'set_navigator'):
        env.set_navigator(navigator)
    elif hasattr(env, 'env') and hasattr(env.env, 'set_navigator'):
        env.env.set_navigator(navigator)

    # 5. Initialize TriggerEvaluator
    trigger_evaluator = TriggerEvaluator(env)
    setattr(env, "trigger_evaluator", trigger_evaluator)
    print(f"[Setup] Created and attached trigger_evaluator to environment using global map tracking")

    # 6b. Initialize QuestManager now that run_dir is known
    # Load existing run or create a new one to get run_dir for QuestManager
    run_info = load_latest_run(env)
    if run_info is None:
        # Create a new run using current map name and map id
        current_map_name = env.get_map_name_by_id(current_map_id_after_reset)
        run_info = create_new_run(env, current_map_name, current_map_id_after_reset)
    run_dir = run_info.run_dir

    # Initialize QuestManager with run_dir for proper quest status synchronization
    quest_manager = QuestManager(env, run_dir=run_dir)
    env.quest_manager = quest_manager

    # 7. NOW, instantiate QuestProgressionEngine with the correctly loaded statuses (SINGLE INITIALIZATION)
    quest_progression_engine = QuestProgressionEngine(
        env=env,
        navigator=navigator,
        quest_manager=quest_manager,
        quests_definitions=QUESTS,
        quest_ids_all=all_quest_ids,
        status_queue=status_queue,
        run_dir=run_dir, 
        initial_quest_statuses=initial_quest_statuses_from_save, # This is critical
        initial_trigger_statuses=initial_trigger_statuses_from_save, # This is critical
        logger=logger # CRITICAL FIX: Pass logger to enable trigger evaluation logging
    )
    
    quest_manager.quest_progression_engine = quest_progression_engine

    # 8. Initialize NavigationSystemMonitor for comprehensive validation
    from environment.environment_helpers.navigation_system_monitor import NavigationSystemMonitor
    navigation_monitor = NavigationSystemMonitor(
        env=env,
        navigator=navigator,
        quest_manager=quest_manager,
        quest_progression_engine=quest_progression_engine,
        logger=logger
    )
    
    # Store reference in main components for easy access
    env.navigation_monitor = navigation_monitor
    navigator.navigation_monitor = navigation_monitor
    quest_manager.navigation_monitor = navigation_monitor
    
    # CRITICAL: Run startup verification to catch any initialization issues
    print("Running navigation system startup verification...")
    startup_results = navigation_monitor.check_at_startup()
    if startup_results.get("total_issues", 0) > 0:
        print(f"⚠️  Startup verification found {startup_results['total_issues']} issues")
    else:
        print("✅ Startup verification passed")
    
    # 9. Refresh QuestManager's current quest based on the now correctly initialized QPE
    quest_manager.get_current_quest() # This should pick up the correct starting quest
    status_queue.put(('__current_quest__', quest_manager.current_quest_id))

    screen = None
    if not config.get("headless", False):
        pygame.init()
        screen_width, screen_height = 640, 566
        screen = pygame.display.set_mode((screen_width, screen_height))
        pygame.display.set_caption("Pokemon Red")

    # Main game loop
    current_action = 0
    total_reward = 0.0
    
    # Action recording for replay functionality
    recorded_playthrough = []
    
    # Button repeat functionality
    last_key_pressed = None
    key_repeat_timer = 0
    key_repeat_delay = 0.15  # Initial delay before repeat starts
    key_repeat_rate = 0.05   # Rate of repeat
    
    # FIXED: UI update throttling for real-time NPC movement
    ui_update_counter = 0
    ui_update_frequency = 5  # Update UI every N ticks (adjust for performance)
    last_ui_update_time = time.time()
    ui_update_min_interval = 0.1  # Minimum seconds between UI updates (10 FPS max)
    
    if quest_manager.current_quest_id is not None:
         if navigator.active_quest_id != quest_manager.current_quest_id:
            navigator.load_coordinate_path(quest_manager.current_quest_id)
            print(f"Play.py: Loaded quest {quest_manager.current_quest_id} into navigator post-reset, aligning with QuestManager.")
    elif info and 'current_quest_id' in info and info['current_quest_id'] is not None:
        q_id = info['current_quest_id']
        if navigator.active_quest_id != q_id:
            navigator.load_coordinate_path(q_id)
            print(f"Play.py: Loaded quest {q_id} into navigator from env info post-reset.")
    else:
        print("Play.py: No initial quest ID available for navigator post-reset.")

    print("Starting game loop...")
    print(f"[DEBUG] Game loop initialization: quest_progression_engine={quest_progression_engine is not None}")
    print(f"[DEBUG] Game loop initialization: quest_manager.current_quest_id={quest_manager.current_quest_id}")
    start_time = time.time()
    running = True
    
    # Initialize these before the loop if they are used by QuestProgressionEngine or QuestManager init logic
    last_map_id = env.get_game_coords()[2]
    visited_warps = set()
    last_player_pos = env.get_game_coords()[:2]
    
    # CRITICAL: Initialize map tracking with current map
    logger.update_map_tracking(last_map_id)
    logger.log_environment_event("GAME_START", {
        'message': 'Game initialized',
        'starting_map_id': last_map_id,
        'map_name': env.get_map_name_by_id(last_map_id),
        'coordinates': env.get_game_coords()
    })

    # UI thread for quest status - ENABLED for testing
    ui_thread_started = False
    if not config.get("headless", False):
        try:
            ui_thread = threading.Thread(target=start_quest_ui, args=(QUESTS, status_queue), daemon=True)
            ui_thread.start()
            ui_thread_started = True
            print("Quest UI thread started successfully")
        except Exception as e:
            print(f"Failed to start quest UI thread: {e}")
    
    if config.get("save_state", True) and run_info: # Check config for save_state
        # Save the initial emulator state using RunManager
        save_initial_state(env, run_info)

    # Function to update quest UI with environment data
    def update_quest_ui():
        try:
            # Get current coordinates
            x, y, map_id = env.get_game_coords()
            map_name = env.get_map_name_by_id(map_id)
            
            # Send location data to UI - send LOCAL coordinates for UI to convert
            status_queue.put(('__location__', (x, y, map_id, map_name)))
            status_queue.put(('__local_location__', (x, y)))
            
            # Send current quest
            current_quest = quest_manager.current_quest_id
            status_queue.put(('__current_quest__', current_quest))
            
            # Send additional environment data
            try:
                dialog = env.read_dialog() or ""
                status_queue.put(('__dialog__', dialog.strip()))
            except:
                status_queue.put(('__dialog__', ""))
            
            # Send navigation status
            nav_status = getattr(navigator, 'navigation_status', 'unknown')
            status_queue.put(('__nav_status__', nav_status))
            
            # FIXED: Send proper run directory name only
            if run_info:
                run_dir_display = run_info.run_id
            elif run_dir:
                run_dir_display = run_dir.name
            else:
                run_dir_display = "None"
            status_queue.put(('__run_dir__', run_dir_display))
            
            # Send total steps
            status_queue.put(('__total_steps__', total_steps))
            
            # Send facing direction
            try:
                # Try to get direction from the environment instance directly
                # This assumes env object (or its wrapper) has a method to get facing direction string
                if hasattr(env, 'get_facing_direction_str'):
                    facing_str = env.get_facing_direction_str()
                else: # Fallback to reading memory if method doesn't exist
                    direction_byte = env.read_m("wSpritePlayerStateData1FacingDirection")
                    if direction_byte == 0: facing_str = "Down"
                    elif direction_byte == 4: facing_str = "Up"
                    elif direction_byte == 8: facing_str = "Left"
                    elif direction_byte == 12: facing_str = "Right"
                    else: facing_str = "Unknown"
                status_queue.put(('__facing_direction__', facing_str))
            except Exception as e:
                # print(f"Error getting facing direction for UI: {e}") # Avoid console spam
                status_queue.put(('__facing_direction__', "Error"))
            
            # Send path info
            if hasattr(navigator, 'sequential_coordinates') and navigator.sequential_coordinates:
                status_queue.put(('__path_index__', navigator.current_coordinate_index))
                status_queue.put(('__path_length__', len(navigator.sequential_coordinates)))
                # Send full navigator path coordinates
                status_queue.put(('__nav_path__', list(navigator.sequential_coordinates)))
                # Send full environment path coordinates if available
                if hasattr(env, 'combined_path') and env.combined_path:
                    status_queue.put(('__env_path__', list(env.combined_path)))
                else:
                    status_queue.put(('__env_path__', []))
            else:
                status_queue.put(('__path_index__', 0))
                status_queue.put(('__path_length__', 0))
                status_queue.put(('__nav_path__', []))
                status_queue.put(('__env_path__', []))
                
            # FIXED: Send proper status
            if dialog and dialog.strip():
                status = "Dialog Active"
            elif nav_status == "navigating":
                status = "Navigating"
            elif current_quest:
                status = f"Quest {current_quest:03d}"
            else:
                status = "Exploring"
            status_queue.put(('__status__', status))
            
            # FIXED: Send last action information
            if hasattr(env, 'action_taken') and env.action_taken is not None:
                try:
                    action_name = VALID_ACTIONS[env.action_taken].name if env.action_taken < len(VALID_ACTIONS) else f"Action_{env.action_taken}"
                except:
                    action_name = f"Action_{env.action_taken}"
            else:
                action_name = "None"
            status_queue.put(('__last_action__', action_name))
            
            # FIXED: Send action source
            if hasattr(update_quest_ui, 'last_action_source'):
                status_queue.put(('__action_source__', update_quest_ui.last_action_source))
            else:
                status_queue.put(('__action_source__', "Unknown"))
            
            # Send battle status
            try:
                in_battle = env.read_m(0xD057) > 0  # Battle type memory
                status_queue.put(('__in_battle__', in_battle))
            except:
                status_queue.put(('__in_battle__', False))

            # Send warp minimap data and debug info to UI
            try:
                obs = env._get_obs()
                warp_minimap_data = obs.get("minimap_warp_obs")
                if warp_minimap_data is not None:
                    # Get detailed debug info
                    warp_debug_info = env.get_warp_debug_info()
                    # Send both minimap data and debug info
                    combined_data = {
                        "minimap": warp_minimap_data,
                        "debug_info": warp_debug_info
                    }
                    status_queue.put(('__warp_minimap__', combined_data))
            except Exception as e:
                print(f"Error getting warp minimap data: {e}")

            # Send emulator screen data to UI
            try:
                raw_screen_frame = env.render() # This is a numpy array HxW or HxWx1 (grayscale)
                if raw_screen_frame is not None:
                    img_height, img_width = raw_screen_frame.shape[0], raw_screen_frame.shape[1]
                    # Convert to RGB bytes for Pillow Image.frombytes
                    if raw_screen_frame.ndim == 2: # Grayscale HxW
                        rgb_frame = np.stack((raw_screen_frame,)*3, axis=-1) # HxWx3
                        img_mode = "RGB"
                    elif raw_screen_frame.ndim == 3 and raw_screen_frame.shape[2] == 1: # Grayscale HxWx1
                        rgb_frame = np.concatenate([raw_screen_frame]*3, axis=2) # HxWx3
                        img_mode = "RGB"
                    elif raw_screen_frame.ndim == 3 and raw_screen_frame.shape[2] == 3: # Already RGB HxWx3
                        rgb_frame = raw_screen_frame
                        img_mode = "RGB"
                    else:
                        # print(f"Unsupported screen format: {raw_screen_frame.shape}") # Avoid console spam
                        rgb_frame = None

                    if rgb_frame is not None:
                        pixel_data_bytes = rgb_frame.tobytes()
                        status_queue.put(('__emulator_screen__', (pixel_data_bytes, img_width, img_height, img_mode)))

                        # Send collision overlay screen data
                        collision_overlay_frame = env.get_screenshot_with_overlay(alpha=128)
                        if collision_overlay_frame is not None:
                            collision_array = np.array(collision_overlay_frame)
                            if collision_array.ndim == 3 and collision_array.shape[2] == 3:  # RGB
                                collision_bytes = collision_array.tobytes()
                                collision_height, collision_width = collision_array.shape[0], collision_array.shape[1]
                                status_queue.put(('__collision_overlay_screen__', (collision_bytes, collision_width, collision_height, "RGB")))
                            elif collision_array.ndim == 3 and collision_array.shape[2] == 4:  # RGBA
                                # Convert RGBA to RGB
                                collision_rgb = collision_array[:, :, :3]
                                collision_bytes = collision_rgb.tobytes()
                                collision_height, collision_width = collision_rgb.shape[0], collision_rgb.shape[1]
                                status_queue.put(('__collision_overlay_screen__', (collision_bytes, collision_width, collision_height, "RGB")))
            except Exception as e:
                # print(f"Error generating screen data for UI: {e}") # Avoid console spam
                pass

        except Exception as e:
            print(f"Error in update_quest_ui: {e}")

    # FIXED: Throttled UI update function for real-time NPC movement
    def update_ui_if_needed():
        """Update UI only if enough time has passed or enough ticks have occurred"""
        nonlocal ui_update_counter, last_ui_update_time
        
        current_time = time.time()
        ui_update_counter += 1
        
        # Check if we should update based on time interval or tick count
        time_elapsed = current_time - last_ui_update_time
        
        if (ui_update_counter >= ui_update_frequency or 
            time_elapsed >= ui_update_min_interval):
            
            update_quest_ui()
            ui_update_counter = 0
            last_ui_update_time = current_time

    # FIXED: Call update_quest_ui immediately to initialize UI
    update_quest_ui()
    last_ui_update_time = time.time()  # Initialize the timer

    # Seed UI with persisted trigger and quest statuses
    # Use the loaded statuses from the save files
    for q_ui in QUESTS:
        qid = q_ui['quest_id']
        
        # Send quest completion status from loaded data
        quest_completed = initial_quest_statuses_from_save.get(qid, False)
        status_queue.put((qid, quest_completed))
        
        # Send trigger completion statuses from loaded data
        triggers = q_ui.get('event_triggers', [])
        for idx, trg_ui in enumerate(triggers):
            tid = f"{qid}_{idx}"
            trigger_completed = initial_trigger_statuses_from_save.get(tid, False)
            status_queue.put((tid, trigger_completed))

    # Initialize Grok agent if enabled
    grok_agent = None
    if config.get("agent", {}).get("grok_on", False):
        api_key = config.get("agent", {}).get("api_key") or os.getenv("GROK_API_KEY")
        if api_key:
            try:
                grok_agent = SimpleAgent(
                    reader=env,              # env is a RedGymEnv (via inheritance) 
                    quest_manager=quest_manager,
                    navigator=navigator,
                    env_wrapper=env,         # same env
                    xai_api_key=api_key
                )
                print("🤖 Grok agent initialized successfully")
            except Exception as e:
                print(f"❌ Failed to initialize Grok agent: {e}")
                grok_agent = None
        else:
            print("⚠️  Grok enabled but no API key found (GROK_API_KEY env var or config)")

    # CRITICAL FIX: Synchronize all quest systems BEFORE main loop
    print("\n=== CRITICAL QUEST SYSTEM SYNCHRONIZATION ===")
    
    # Get the current quest from quest manager
    current_quest = quest_manager.get_current_quest()
    print(f"✓ Quest Manager current quest: {current_quest}")
    
    # Ensure all systems are synchronized with this quest
    if current_quest is not None:
        # 1. Sync Navigator
        if navigator.active_quest_id != current_quest:
            print(f"⚠️  Navigator quest mismatch: {navigator.active_quest_id} != {current_quest}")
            navigator.load_coordinate_path(current_quest)
            print(f"✓ Navigator loaded quest {current_quest}")
        
        # 2. Sync Environment 
        if not hasattr(env, 'current_loaded_quest_id') or env.current_loaded_quest_id != current_quest:
            print(f"⚠️  Environment quest mismatch: {getattr(env, 'current_loaded_quest_id', None)} != {current_quest}")
            env.load_coordinate_path(current_quest)
            print(f"✓ Environment loaded quest {current_quest}")
        
        # 3. Verify all systems have coordinates
        if hasattr(navigator, 'sequential_coordinates') and navigator.sequential_coordinates:
            print(f"✓ Navigator has {len(navigator.sequential_coordinates)} coordinates")
        else:
            print("❌ Navigator has no coordinates loaded!")
            
        if hasattr(env, 'combined_path') and env.combined_path:
            print(f"✓ Environment has {len(env.combined_path)} coordinates")
        else:
            print("❌ Environment has no coordinates loaded!")
            
        # 4. Check quest definitions are available
        quest_def = quest_progression_engine.get_quest_data_by_id(current_quest)
        if quest_def:
            print(f"✓ Quest {current_quest} definition found: {quest_def.get('name', 'Unknown')}")
        else:
            print(f"❌ Quest {current_quest} definition MISSING!")
            
        # 5. Verify player position relative to quest coordinates
        player_x, player_y, map_id = env.get_game_coords()
        player_global = local_to_global(player_y, player_x, map_id)
        print(f"✓ Player at local ({player_x}, {player_y}) map {map_id} = global {player_global}")
        
        # Check if player is on any quest coordinate
        on_quest_node = False
        if navigator.sequential_coordinates:
            for i, coord in enumerate(navigator.sequential_coordinates):
                if coord == player_global:
                    print(f"✓ Player is on quest coordinate {i}: {coord}")
                    navigator.current_coordinate_index = i
                    on_quest_node = True
                    break
        
        if not on_quest_node:
            print(f"⚠️  Player is not on any quest coordinate")
            # Find closest quest coordinate
            if navigator.sequential_coordinates:
                distances = []
                for i, coord in enumerate(navigator.sequential_coordinates):
                    dist = abs(coord[0] - player_global[0]) + abs(coord[1] - player_global[1])
                    distances.append((i, coord, dist))
                
                closest = min(distances, key=lambda x: x[2])
                print(f"   Closest quest coordinate: index {closest[0]}, coord {closest[1]}, distance {closest[2]}")
    else:
        print("❌ No current quest found!")
    
    print("=== END QUEST SYSTEM SYNCHRONIZATION ===\n")
    
    # Store reference in environment
    env.quest_progression_engine = quest_progression_engine

    while running and total_steps < args.max_total_steps:
        current_time = time.time()
        current_action = None

        # Handle key repeat
        if last_key_pressed is not None and (current_time - key_repeat_timer) > key_repeat_delay:
            current_action = last_key_pressed
            key_repeat_timer = current_time
            key_repeat_delay = key_repeat_rate  # Switch to repeat rate after initial delay

        # Handle pygame events
        if not env.headless:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    # FIXED: Add escape key handling for save and quit
                    if event.key == pygame.K_ESCAPE:
                        print("Escape pressed - saving and quitting...")
                        running = False
                        break
                    # FIXED: Add Ctrl+S manual save
                    elif event.key == pygame.K_s and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                        print("Ctrl+S pressed - manual save...")
                        from environment.environment_helpers.saver import save_manual_state
                        import datetime
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        save_manual_state(env, run_dir, f"manual_{timestamp}")
                        continue
                    elif args.interactive_mode:
                        # Handle special keys first
                        if event.key == pygame.K_4:
                            # On '4' key: Snap navigator and environment to the nearest coordinate in the current quest path
                            raw_dialog = env.read_dialog() or ''
                            if raw_dialog.strip():
                                print("play.py: main(): '4' key: Navigation paused: dialog active, cannot snap to path.")
                            else:
                                print("play.py: main(): '4' key: Snapping to nearest coordinate on recorded path.")
                                
                                # FIXED: Ensure quest IDs are synchronized before snapping
                                # Get the current quest from quest_manager
                                current_quest = quest_manager.get_current_quest()
                                if current_quest is not None:
                                    # Sync quest ID to all components
                                    navigator.active_quest_id = current_quest
                                    env.current_loaded_quest_id = current_quest
                                    quest_manager.current_quest_id = current_quest
                                    
                                    # Load the quest path if not already loaded
                                    if not navigator.sequential_coordinates or navigator.active_quest_id != current_quest:
                                        print(f"play.py: Loading quest {current_quest} path for navigator")
                                        navigator.load_coordinate_path(current_quest)
                                    
                                    # Now snap to nearest coordinate
                                    if navigator.snap_to_nearest_coordinate():
                                        print(navigator.get_current_status())
                                    else:
                                        print("Navigator: Failed to snap to nearest coordinate.")
                                else:
                                    print("play.py: No active quest to snap to.")
                        elif event.key == pygame.K_5:
                            # On '5' key: Move to next coordinate (quest path)
                            raw_dialog = env.read_dialog() or ''
                            if raw_dialog.strip():
                                print(f"\nplay.py: main(): '5' key: Navigation paused: dialog active, cannot move to next coordinate.")
                            else:
                                print(f"\nplay.py: main(): '5' key: Using PATH_FOLLOW_ACTION")
                                
                                # CRITICAL FIX: Ensure environment loads the current quest coordinates
                                current_q = quest_manager.get_current_quest()
                                if current_q is not None:
                                    print(f"play.py: '5' key: Current quest is {current_q}")
                                    
                                    # Load quest coordinates in environment BEFORE triggering PATH_FOLLOW
                                    if not env.load_coordinate_path(current_q):
                                        print(f"play.py: '5' key: ERROR - Failed to load quest {current_q} coordinates")
                                    else:
                                        print(f"play.py: '5' key: Successfully loaded quest {current_q} coordinates")
                                    
                                    # Sync quest IDs across all components
                                    setattr(env, 'current_loaded_quest_id', current_q)
                                    quest_manager.current_quest_id = current_q
                                    navigator.active_quest_id = current_q
                                else:
                                    print("play.py: '5' key: WARNING - No current quest found")
                                
                                # Apply quest-specific overrides (e.g., force A press for quest 015)
                                desired = quest_manager.filter_action(PATH_FOLLOW_ACTION)
                                if desired == PATH_FOLLOW_ACTION:
                                    # Use PATH_FOLLOW_ACTION directly - let environment handle it
                                    current_action = PATH_FOLLOW_ACTION
                                else:
                                    # FIXED: Override with quest-specific emulator action using centralized execution
                                    current_obs, current_reward, current_terminated, current_truncated, current_info, total_steps = execute_action_step(
                                        env, desired, quest_manager, navigator, logger, total_steps
                                    )
                                    # Record the override action
                                    recorded_playthrough.append(desired)
                                    # Update observation and info for this frame
                                    obs, reward, terminated, truncated, info = current_obs, current_reward, current_terminated, current_truncated, current_info
                        elif event.key == pygame.K_6:
                            # "6" key: Manual warp trigger
                            print("Navigator: Manual input - Key 6 -> Manual warp trigger")
                            if navigator.manual_warp_trigger():
                                print("Navigator: Manual warp executed successfully")
                            else:
                                print("Navigator: Manual warp failed")
                            last_key_pressed = None  # Don't repeat warp action
                            continue
                        else:
                            # Regular key handling
                            key_action = navigator.handle_pygame_event(event)
                            if key_action is not None:
                                current_action = key_action # Store manual action
                                last_key_pressed = key_action
                                key_repeat_timer = current_time
                                key_repeat_delay = 0.15
                elif event.type == pygame.KEYUP:
                    # Stop key repeat when key is released
                    last_key_pressed = None

        # AI Action or Manual Action
        if current_action is None and not args.disable_ai_actions: # AI takes over if no manual input and AI enabled
            
            # 🤖 GROK INTEGRATION POINT - Clean integration using get_action method
            if grok_agent and not args.interactive_mode:
                try:
                    # Extract current game state for Grok
                    game_state = extract_structured_game_state(env_wrapper=env, reader=env, quest_manager=quest_manager)
                    
                    # Get action decision from Grok (no execution, just decision)
                    # This should be synchronous!!! We will wait for grok to get the prompt, think, and return a response.
                    # A tool call will likely be in there - this is how grok chooses an action.
                    current_action = grok_agent.get_action(game_state)
                    
                    if current_action is not None:
                        print(f"🤖 Grok decided on action: {current_action} ({VALID_ACTIONS[current_action] if current_action < len(VALID_ACTIONS) else 'INVALID'})")
                    
                except Exception as e:
                    print(f"❌ Grok error: {e}, falling back to quest system")
                    grok_agent = None  # Disable Grok on error to avoid spam
            
            # Existing quest/navigation fallback logic (unchanged)
            if current_action is None:
                # This is where your AI/agent logic would determine the action
                # For now, let's use a placeholder or random action if QuestManager doesn't provide one
                # quest_manager.get_current_quest() # Updates internal current_quest_id
                # current_action = quest_manager.get_next_action() # Needs to be robust
                
                # Simplified logic: If QuestManager provides an action, use it. Otherwise, random.
                # This needs to be fleshed out with proper agent logic.
                # The QuestManager's get_next_action() should be the primary source for quest-driven actions.
                # The QuestProgressionEngine updates quest states, which QuestManager reads via get_current_quest().
                
                # Ensure quest_manager has the latest current_quest_id
                _ = quest_manager.get_current_quest() # This updates quest_manager.current_quest_id

                if quest_manager.is_quest_active():
                    current_action = quest_manager.filter_action(None) # filter_action can decide or pass through
                    if current_action is None: # filter_action decided no specific action, or returned None to indicate default behavior
                         # Fallback to navigator or random if no quest action
                        if navigator.sequential_coordinates and navigator.navigation_status != "idle": # Check if navigator has a path and is not idle
                            current_action = navigator.get_next_action()
                        else:
                            # Random fallback: choose a valid action index
                            current_action = np.random.choice(len(VALID_ACTIONS))
                else: # No quest active
                    if navigator.sequential_coordinates and navigator.navigation_status != "idle": # Check if navigator has a path and is not idle
                        current_action = navigator.get_next_action()
                    else:
                        # Random fallback for non-quest, non-navigation: choose a valid action index
                        # Sloppy and could cause problems. Also, could be helpful if grok somehow gets stuck.
                        current_action = np.random.choice(len(VALID_ACTIONS))

        elif current_action is None and args.disable_ai_actions and not args.interactive_mode: # Replay/tick mode
            # No-OP mode: skip stepping environment when AI actions are disabled
            time.sleep(0.01)
            continue

        if current_action is None: # Still no action (e.g. interactive mode with no key press)
            if not env.headless: # Only tick if we need to render for interactive
                env.pyboy.tick()
                raw_frame = env.render()
                processed_frame_rgb = process_frame_for_pygame(raw_frame) # Process the frame
                update_screen(screen, processed_frame_rgb, screen_width, screen_height)
                # pygame.display.flip() # update_screen handles flip
                
                # FIXED: Update UI after game tick to show real-time NPC movement
                update_ui_if_needed()
                
                time.sleep(0.01) # Minimal delay
            else: # Headless, no action -> could be an issue or intentional pause
                # FIXED: Still tick and update UI in headless mode for NPC movement
                env.pyboy.tick()
                update_ui_if_needed()
                time.sleep(0.01)
            
            # CRITICAL FIX: Quest progression should always run, even without player actions
            # This was the main bug - quest triggers were never evaluated when idle!
            # print(f"[DEBUG] Quest progression idle check: quest_progression_engine={quest_progression_engine is not None}")
            
            if quest_progression_engine:
                current_map_id = env.get_game_coords()[2]
                
                # Try to check map history, but don't let failures block quest progression
                map_changed = False
                try:
                    if hasattr(env, 'map_history') and len(env.map_history) >= 2:
                        map_changed = env.map_history[-2] != env.map_history[-1]
                        print(f"[DEBUG] Map history check: changed={map_changed}, history={list(env.map_history)}")
                    else:
                        print(f"[DEBUG] Map history not available or too short (len={len(env.map_history) if hasattr(env, 'map_history') else 'N/A'})")
                except Exception as e:
                    print(f"[DEBUG] Map history check failed: {e}")
                
                # ALWAYS run quest progression, regardless of map history
                try:
                    if map_changed:
                        print(f"[DEBUG] Calling quest_progression_engine.step() in idle state at step {total_steps}")
                        quest_progression_engine.step(trigger_evaluator)
                        print(f"[DEBUG] Quest progression step completed in idle state")
                except Exception as e:
                    print(f"[ERROR] Quest progression failed in idle state: {e}")
                    logger.log_error("QUEST_PROGRESSION_IDLE", f"Error in idle quest progression: {str(e)}", {
                        'current_map_id': current_map_id,
                        'total_steps': total_steps
                    })
            continue

        # Apply quest manager filtering to ALL actions (manual, AI, navigator)
        if current_action is not None:
            original_action = current_action
            
            # PATH_FOLLOW_ACTION is now handled directly in environment.step()
            # No special handling needed here - let environment handle it
            
            current_action = quest_manager.filter_action(current_action)
            
            # Determine action source for recording
            if args.interactive_mode and original_action != current_action:
                action_source = 'manual_filtered'
            elif args.interactive_mode:
                action_source = 'manual'
            elif navigator.navigation_status == "navigating":
                action_source = 'navigator'
            else:
                action_source = 'ai'
            
            # Record action for replay functionality
            recorded_playthrough.append({
                'step': total_steps,
                'action': current_action,
                'original_action': original_action if original_action != current_action else None,
                'timestamp': time.time(),
                'source': action_source
            })

        # FIXED: Use centralized execution instead of direct env.step()
        obs, reward, terminated, truncated, info, total_steps = execute_action_step(
            env, current_action, quest_manager, navigator, logger, total_steps
        )
        total_reward += reward

        # Navigation System Monitoring - check after each step
        if navigation_monitor:
            try:
                navigation_monitor.check_at_quest_step()
            except Exception as e:
                print(f"NavigationMonitor error in step check: {e}")

        # FIXED: Save persistent step counter every 100 steps
        if total_steps % 100 == 0:
            try:
                with open(step_counter_file, 'w') as f:
                    json.dump({'total_steps': total_steps}, f)
            except Exception as e:
                print(f"Could not save step counter: {e}")

        # Warp Tracking Logic (simplified, ensure env methods are correct)
        new_map_id = env.get_game_coords()[2]
        # new_player_pos = env.get_player_coordinates() # Example
        if new_map_id != last_map_id: # Basic warp detection
            # CRITICAL: Update map tracking for logging context
            logger.update_map_tracking(new_map_id)
            
            # Log map transition with full context
            player_x, player_y, _ = env.get_game_coords()
            logger.log_environment_event("MAP_TRANSITION", {
                'message': f'Map transition from {last_map_id} to {new_map_id}',
                'from_map': last_map_id,
                'to_map': new_map_id,
                'from_map_name': env.get_map_name_by_id(last_map_id),
                'to_map_name': env.get_map_name_by_id(new_map_id),
                'coordinates': [player_x, player_y],
                'total_steps': total_steps
            })
            
            # record_warp_step(env, last_map_id, new_map_id, total_steps) # If function exists
            print(f"[MapTransition] Map ID changed from {last_map_id} to {new_map_id}")
            
            # Navigation System Monitoring - check at map transition
            if navigation_monitor:
                try:
                    navigation_monitor.check_at_map_transition()
                except Exception as e:
                    print(f"NavigationMonitor error in map transition check: {e}")
            
            last_map_id = new_map_id

        # FIXED: Update quest progression engine with proper trigger evaluator
        print(f"[DEBUG] Quest progression action check: quest_progression_engine={quest_progression_engine is not None}, action={current_action}, map_id={env.get_game_coords()[2]}")
        
        if quest_progression_engine:
            current_map_id = env.get_game_coords()[2]
            
            # Store previous quest for change detection
            previous_quest = quest_manager.current_quest_id
            
            # Call quest progression engine to evaluate triggers and progress quests
            try:
                print(f"[DEBUG] Calling quest_progression_engine.step() at step {total_steps}, map {current_map_id}, current_quest={previous_quest}")
                quest_progression_engine.step(trigger_evaluator)
                print(f"[DEBUG] Quest progression step completed successfully")
                
                # Check for quest transitions
                new_quest = quest_manager.current_quest_id
                if previous_quest != new_quest:
                    print(f"[DEBUG] Quest transition detected: {previous_quest} -> {new_quest}")
                    if navigation_monitor:
                        try:
                            navigation_monitor.check_at_quest_transition()
                        except Exception as e:
                            print(f"NavigationMonitor error in quest transition check: {e}")
                else:
                    print(f"[DEBUG] No quest transition, remained at quest {previous_quest}")
                        
            except Exception as e:
                print(f"[ERROR] Exception in quest progression: {str(e)}")
                import traceback
                print(f"[ERROR] Full traceback: {traceback.format_exc()}")
                logger.log_error("QUEST_PROGRESSION", f"Error in quest progression: {str(e)}", {
                    'current_quest_id': quest_manager.current_quest_id,
                    'current_map_id': current_map_id,
                    'traceback': traceback.format_exc()
                })
        else:
            print(f"[ERROR] quest_progression_engine is None! This should never happen after initialization.")

        # FIXED: Update quest UI after player actions (now throttled for performance)
        update_quest_ui()  # Always update after player actions for responsiveness

        if total_steps % config.get("log_frequency", 1000) == 0:
            elapsed_time = time.time() - start_time
            steps_per_sec = total_steps / elapsed_time if elapsed_time > 0 else 0
            current_quest_display = quest_manager.current_quest_id if quest_manager.current_quest_id is not None else "None"
            print(f"Step: {total_steps}, Reward: {total_reward:.2f}, Steps/sec: {steps_per_sec:.2f}, Current Quest: {current_quest_display}")
            if config.get("save_state", True) and run_info:
                # Save loop state (records action sequence) using recorded_playthrough
                save_loop_state(env, recorded_playthrough)
        
        if not env.headless:
            raw_frame = env.render()
            processed_frame_rgb = process_frame_for_pygame(raw_frame) # Process the frame
            update_screen(screen, processed_frame_rgb, screen_width, screen_height)
            # pygame.display.flip() # update_screen handles this

        if terminated or truncated:
            print("Game terminated or truncated.")
            running = False

    print(f"Finished after {total_steps} steps.")
    
    # FIXED: Save final persistent step counter
    try:
        with open(step_counter_file, 'w') as f:
            json.dump({'total_steps': total_steps}, f)
        print(f"Saved final step counter: {total_steps}")
    except Exception as e:
        print(f"Could not save final step counter: {e}")
    
    # Save recorded playthrough
    if recorded_playthrough and run_dir:
        try:
            playthrough_file = run_dir / "recorded_playthrough.json"
            with open(playthrough_file, 'w') as f:
                json.dump(recorded_playthrough, f, indent=2)
            print(f"Recorded playthrough saved to {playthrough_file}")
        except Exception as e:
            print(f"Failed to save recorded playthrough: {e}")
    
    if config.get("save_state", True) and run_info:
        # Save final state using RunManager
        save_final_state(env, run_info, recorded_playthrough)
    
    env.close()
    if not env.headless:
        pygame.quit()

if __name__ == "__main__":
    main()
    # print("play.py execution complete.")