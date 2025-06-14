# play.py
import sys
from pathlib import Path

# Ensure the project root is in the Python path
project_root_path = Path(__file__).resolve().parent.parent
if str(project_root_path) not in sys.path:
    sys.path.insert(0, str(project_root_path))

import argparse
import numpy as np
import json
import pygame
import time
import math
import os
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
from environment.environment import VALID_ACTIONS, PATH_FOLLOW_ACTION, VALID_ACTIONS_STR
from pyboy.utils import WindowEvent
from environment.data.recorder_data.global_map import local_to_global
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.environment_helpers.saver import save_initial_state, save_loop_state, save_final_state, load_latest_run, create_new_run
from environment.environment_helpers.warp_tracker import record_warp_step, backtrack_warp_sequence
from environment.environment_helpers.quest_manager import QuestManager, verify_quest_system_integrity, determine_starting_quest, describe_trigger
from ui.quest_ui import start_quest_ui
from environment.environment_helpers.trigger_evaluator import TriggerEvaluator
from environment.grok_integration import extract_structured_game_state
from agent.simple_agent import SimpleAgent
from environment.data.environment_data.item_handler import ItemHandler
from shared import game_started, grok_enabled
from environment.data.environment_data.species import Species
from environment.data.environment_data.battle import StatusCondition
VALID_ACTIONS_STRVALID_ACTIONS_STR = ["down", "left", "right", "up", "a", "b", "path", "start"]

# Load quest definitions
QUESTS_FILE = Path(__file__).parent / "environment_helpers" / "required_completions.json"
with open(QUESTS_FILE, 'r') as f:
    QUESTS = json.load(f)

# Diagnostic functions moved to navigator.py

# Quest functions moved to quest_manager.py

# Import StageManager for bootstrapping intro skip
from environment.environment_helpers.stage_helper import StageManager

def run_intro_bootstrap(env, executor, quest_manager, navigator, logger, max_steps=20000):
    """
    Advance the game from the title screen to the custom-name entry screen by
    simply feeding *noop* actions into the environment and letting the *single*
    StageManager instance attached to the environment handle the required
    START/B/A overrides.

    Using the existing `env.stage_manager` avoids creating a second
    StageManager that would clash with the one embedded in `env.process_action`.
    """
    mgr = getattr(env, 'stage_manager', None)
    if mgr is None:
        # Should never happen – the environment attaches one in its __init__
        mgr = StageManager(env)

    total = 0
    for _ in range(max_steps):
        # Update stage logic (normally called internally each step, but we run
        # it explicitly here prior to the first action so the very first frame
        # already has scripted movements active).
        mgr.update_stage_manager()

        # Feed a neutral action (0 – DOWN). StageManager will transform it to
        # START / B / A as dictated by the scripted rules.
        obs, reward, done, truncated, info, total = executor(
            env, 0, quest_manager, navigator, logger, total)

        # ------------------------------------------------------------------
        # NEW: Rate-limit bootstrap stepping
        # ------------------------------------------------------------------
        # Without any user interaction the intro-skip loop can blast through
        # hundreds of emulator frames per second which is not helpful for
        # debugging and makes it appear as if the screen is just "black".
        # Capping the rate to ~2 Hz (0.5 s per frame) keeps the intro visible
        # while still progressing automatically.
        time.sleep(0.5)
        
        # Break as soon as we hit the preset-name dialog («NEW NAME») or the
        # "YOUR NAME?" prompt signalling the custom name screen.
        dialog = (env.get_active_dialog() or '')
        if 'NEW NAME' in dialog or 'YOUR NAME?' in dialog:
            mgr.clear_scripted_movements()
            break

        if done or truncated:
            break

    return total

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
            import traceback
            # Get the full traceback with line numbers, function names, and files
            tb_str = traceback.format_exc()
            logger.error(f"Error executing action {action}: {e}\n"
                        f"Full traceback:\n{tb_str}")
        # Return safe defaults
        return None, 0.0, True, True, {}, total_steps

def process_frame_for_pygame(frame_from_env_render):
    # Ensure frame has shape (H, W, 3) for Pygame surface
    if frame_from_env_render.ndim == 2:
        # Grayscale -> replicate channels
        frame_rgb = np.stack((frame_from_env_render,) * 3, axis=-1)
    elif frame_from_env_render.ndim == 3:
        ch = frame_from_env_render.shape[2]
        if ch == 1:
            # Single-channel grayscale -> replicate
            frame_rgb = np.concatenate([frame_from_env_render] * 3, axis=2)
        else:
            # RGB or RGBA: take first three channels (drop alpha)
            frame_rgb = frame_from_env_render[:, :, :3]
    else:
        # Fallback: convert to 3-channel grayscale
        gray = frame_from_env_render.astype(np.uint8)
        frame_rgb = np.stack((gray,) * 3, axis=-1)
    return frame_rgb

def update_screen(screen, frame_rgb, target_width, target_height):
    obs_surface = pygame.surfarray.make_surface(frame_rgb.transpose(1,0,2))
    obs_surface = pygame.transform.scale(obs_surface, (target_width, target_height))
    screen.blit(obs_surface, (0, 0))
    pygame.display.flip()


def get_default_config(
    gb_path,
    state_dir,
    override_init_state=None,
    init_from_last_ending_state=True,
    interactive_mode=True,
    record_replays=True,
    headless=False,
    video_dir="video",
    emulator_delay=11,
    action_freq=24,
    save_video=False,
    fast_video=False,
    n_record=10,
    perfect_ivs=True,
    auto_flash=False,
    disable_wild_encounters=True,
    auto_teach_cut=True,
    auto_use_cut=True,
    auto_teach_surf=True,
    auto_use_surf=True,
    auto_teach_strength=True,
    auto_use_strength=True,
    auto_solve_strength_puzzles=True,
    auto_remove_all_nonuseful_items=False,
    auto_pokeflute=True,
    auto_next_elevator_floor=True,
    skip_safari_zone=False,
    infinite_safari_steps=False,
    insert_saffron_guard_drinks=False,
    infinite_money=True,
    infinite_health=True,
    animate_scripts=True,
):
    env_config = {
        "gb_path": gb_path,
        "state_dir": state_dir,
        "override_init_state": override_init_state,
        "init_from_last_ending_state": init_from_last_ending_state,
        "interactive_mode": interactive_mode,
        "record_replays": record_replays,
        "headless": headless,
        "video_dir": video_dir,
        "emulator_delay": emulator_delay,
        "action_freq": action_freq,
        "save_video": save_video,
        "fast_video": fast_video,
        "n_record": n_record,
        "perfect_ivs": perfect_ivs,
        "auto_flash": auto_flash,
        "disable_wild_encounters": disable_wild_encounters,
        "auto_teach_cut": auto_teach_cut,
        "auto_use_cut": auto_use_cut,
        "auto_teach_surf": auto_teach_surf,
        "auto_use_surf": auto_use_surf,
        "auto_teach_strength": auto_teach_strength,
        "auto_use_strength": auto_use_strength,
        "auto_solve_strength_puzzles": auto_solve_strength_puzzles,
        "auto_remove_all_nonuseful_items": auto_remove_all_nonuseful_items,
        "auto_pokeflute": auto_pokeflute,
        "auto_next_elevator_floor": auto_next_elevator_floor,
        "skip_safari_zone": skip_safari_zone,
        "infinite_safari_steps": infinite_safari_steps,
        "insert_saffron_guard_drinks": insert_saffron_guard_drinks,
        "infinite_money": infinite_money,
        "infinite_health": infinite_health,
        "animate_scripts": animate_scripts,
    }
    return OmegaConf.create(env_config)

def _setup_configuration(args, project_root_path):
    yaml_path = Path(args.config_path)
    yaml_config = OmegaConf.load(yaml_path) if yaml_path.exists() else OmegaConf.create()

    cli_args = {k: v for k, v in vars(args).items() if v is not None}

    # Extract env settings from YAML
    env_yaml = yaml_config.get('env', {})

    # Build defaults from YAML + hardcoded defaults
    defaults = get_default_config(
        gb_path=cli_args.get('rom_path', env_yaml.get('gb_path')),
        state_dir=env_yaml.get('state_dir'),
        override_init_state=env_yaml.get('override_init_state'),
        init_from_last_ending_state=env_yaml.get('init_from_last_ending_state'),
        interactive_mode=env_yaml.get('interactive_mode'),
        record_replays=env_yaml.get('record_replays'),
        headless=env_yaml.get('headless'),
        video_dir=env_yaml.get('video_dir'),
        emulator_delay=env_yaml.get('emulator_delay'),
        action_freq=env_yaml.get('action_freq'),
        save_video=env_yaml.get('save_video'),
        fast_video=env_yaml.get('fast_video'),
        n_record=env_yaml.get('n_record'),
        perfect_ivs=env_yaml.get('perfect_ivs'),
        auto_flash=env_yaml.get('auto_flash'),
        disable_wild_encounters=env_yaml.get('disable_wild_encounters'),
        auto_teach_cut=env_yaml.get('auto_teach_cut'),
        auto_use_cut=env_yaml.get('auto_use_cut'),
        auto_teach_surf=env_yaml.get('auto_teach_surf'),
        auto_use_surf=env_yaml.get('auto_use_surf'),
        auto_teach_strength=env_yaml.get('auto_teach_strength'),
        auto_use_strength=env_yaml.get('auto_use_strength'),
        auto_solve_strength_puzzles=env_yaml.get('auto_solve_strength_puzzles'),
        auto_remove_all_nonuseful_items=env_yaml.get('auto_remove_all_nonuseful_items'),
        auto_pokeflute=env_yaml.get('auto_pokeflute'),
        auto_next_elevator_floor=env_yaml.get('auto_next_elevator_floor'),
        skip_safari_zone=env_yaml.get('skip_safari_zone'),
        infinite_safari_steps=env_yaml.get('infinite_safari_steps'),
        insert_saffron_guard_drinks=env_yaml.get('insert_saffron_guard_drinks'),
        infinite_money=env_yaml.get('infinite_money'),
        infinite_health=env_yaml.get('infinite_health'),
        animate_scripts=env_yaml.get('animate_scripts'),
    )

    # Merge defaults + YAML + CLI
    final_config = OmegaConf.merge(defaults, yaml_config.get('env', {}), OmegaConf.create(cli_args))

    # Validate and resolve paths
    if not final_config.gb_path:
        raise ValueError("ROM path ('gb_path') cannot be None.")
    resolved_rom = Path(final_config.gb_path)
    if not resolved_rom.is_absolute():
        resolved_rom = project_root_path / resolved_rom
    final_config.gb_path = str(resolved_rom)

    # Sync agent settings
    if 'agent' in yaml_config:
        final_config.agent = OmegaConf.merge(yaml_config.agent)
        final_config.grok_on = final_config.agent.get('grok_on')

    # Session ID
    if not final_config.get('session_id'):
        final_config.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    return final_config


# def _setup_configuration(args, project_root_path):
#     # Load YAML config if it exists
#     yaml_path = Path(args.config_path)
#     yaml_config = OmegaConf.load(yaml_path) if yaml_path.exists() else OmegaConf.create()

#     # Get CLI arguments, filtering out None values
#     cli_args = {k: v for k, v in vars(args).items() if v is not None}

#     # Manually determine rom_path and init_state with clear priority: CLI > YAML > Default
#     rom_path = cli_args.get('rom_path', yaml_config.get('env', {}).get('gb_path'))
#     init_state = cli_args.get('initial_state_path', yaml_config.get('env', {}).get('init_state', "initial_states/init.state"))

#     # Get the full default config dictionary
#     defaults = get_default_config(gb_path=rom_path, initial_state_path=init_state)

#     # Create the final config, starting with defaults and merging YAML and CLI
#     final_config = defaults
#     if 'env' in yaml_config:
#         final_config = OmegaConf.merge(final_config, yaml_config.env)
#     if 'agent' in yaml_config:
#         final_config.agent = OmegaConf.merge(final_config.get('agent', {}), yaml_config.agent)
#     final_config = OmegaConf.merge(final_config, OmegaConf.create(cli_args))
    
#     # --- Final Path and Value Resolution ---
#     # ROM Path
#     if not final_config.gb_path:
#         raise ValueError("ROM path ('gb_path') cannot be None after configuration merge.")
#     resolved_rom_path = Path(final_config.gb_path)
#     if not resolved_rom_path.is_absolute():
#         resolved_rom_path = project_root_path / resolved_rom_path
#     if not resolved_rom_path.exists():
#         raise FileNotFoundError(f"ROM file not found at resolved path: {resolved_rom_path}")
#     final_config.gb_path = str(resolved_rom_path)

#     # Initial State Path
#     resolved_init_state = Path(final_config.init_state)
#     if not resolved_init_state.is_absolute():
#         resolved_init_state = project_root_path / resolved_init_state
#     final_config.init_state = str(resolved_init_state)
    

#     # Sync agent's grok_on to top-level so every module (and shared flags) sees it
#     if 'agent' in final_config and 'grok_on' in final_config.agent:
#         final_config.grok_on = final_config.agent.grok_on

#     # Session ID
#     if 'session_id' not in final_config or not final_config.session_id:
#         final_config.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

#     return final_config

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
    parser.add_argument("--headless", action="store_true", default=None, help="Run without Pygame display to reduce CPU usage")
    # Removed --run_dir argument - use config only
    parser.add_argument("--config_path", type=str, default=str(project_root_path / "config.yaml"))
    parser.add_argument("--rom_path", type=str, default=None) # Default to None, expect from YAML or error
    parser.add_argument("--initial_state_path", type=str, default=None, help="Initial state file path (optional)")
    parser.add_argument("--save_video", type=bool, default=None)
    parser.add_argument("--fast_video", type=bool, default=None)
    parser.add_argument("--n_record", type=int, default=None)
    parser.add_argument("--perfect_ivs", type=bool, default=None)
    parser.add_argument("--use_global_map", type=bool, default=None)
    parser.add_argument("--save_state", type=bool, default=None)
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
    parser.add_argument("--auto_flash", type=bool, default=None)
    parser.add_argument("--animate_scripts", type=bool, default=None)
    parser.add_argument("--grok_api_key", type=str, default=None) # Keep for potential future use

    args = parser.parse_args()

    config = _setup_configuration(args, project_root_path)
    import shared
    # Set or clear the Grok enabled event based on config
    if config.grok_on:
        shared.grok_enabled.set()
    else:
        shared.grok_enabled.clear()
    # Ensure game_started is cleared before starting
    shared.game_started.clear()
    
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
    
    # If interactive_mode not set by CLI, use configuration default for interactive mode
    if args.interactive_mode is None:
        args.interactive_mode = bool(config.get("interactive_mode", True))

    # Initialize the environment using ConfiguredEnvWrapper
    # Pass the fully resolved config and original cli_args
    env = ConfiguredEnvWrapper(base_conf=config, cli_args=args) 
    # Propagate headless config to environment wrapper
    if hasattr(env, 'headless'):
        env.headless = config.get('headless', False)
    else:
        setattr(env, 'headless', config.get('headless', False))

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
    # Load (or reuse) an existing run so QuestManager can use its run_dir
    # Priority order:
    #   1. A run the environment just created during `env.reset()`
    #   2. The latest previously-saved run on disk (via `load_latest_run`)
    #   3. Create a brand-new run if neither of the above exist

    # 1️⃣ Prefer a run that the environment may have already created. This
    #     prevents a second directory (e.g. "002-…") from being made a few
    #     frames later.
    run_info = getattr(env, "current_run_info", None)

    # 2️⃣ If the environment didn't create one (e.g. running from a saved
    #     state), fall back to whatever the RunManager thinks is latest.
    if run_info is None:
        run_info = load_latest_run(env)

    # 3️⃣ If still None, we really do need a fresh run directory.
    if run_info is None:
        current_map_name = env.get_map_name_by_id(current_map_id_after_reset)
        run_info = create_new_run(env, current_map_name, current_map_id_after_reset)

    run_dir = run_info.run_dir

    # Initialize QuestManager with run_dir for proper quest status synchronization
    quest_manager = QuestManager(env, run_dir=run_dir)
    env.quest_manager = quest_manager
    
    # PREVENTION: Validate quest system integrity at startup
    try:
        from environment.environment_helpers.quest_validator import validate_quest_system
        print("Validating quest coordinate files...")
        validation_results = validate_quest_system(output_report=False)
        
        if not validation_results.get('validation_passed'):
            critical_count = validation_results.get('total_critical_errors', 0)
            warning_count = validation_results.get('total_warnings', 0)
            
            if critical_count > 0:
                print(f"⚠️  QUEST VALIDATION: {critical_count} critical errors found in quest files!")
                print("This may cause navigation issues. Check quest_validation_report.txt for details.")
                
                # Log the validation issues
                logger.log_system_event("QUEST_VALIDATION_FAILED", {
                    'critical_errors': critical_count,
                    'warnings': warning_count,
                    'failed_quests': validation_results.get('quests_with_errors', 0)
                })
            else:
                print(f"✅ Quest validation passed with {warning_count} warnings")
        else:
            print("✅ All quest coordinate files validated successfully")
            
    except Exception as e:
        print(f"Warning: Could not validate quest files: {e}")
        logger.log_error("QUEST_VALIDATION", f"Quest validation failed: {e}", {})

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
    
    # 9. Refresh QuestManager's current quest and load coordinates into navigator
    quest_manager.get_current_quest() # This should pick up the correct starting quest
    if quest_manager.current_quest_id is not None:
        navigator.load_coordinate_path(quest_manager.current_quest_id)
        print(f"Play.py: Loaded quest {quest_manager.current_quest_id} into navigator post-reset, aligning with QuestManager.")

    status_queue.put(('__current_quest__', quest_manager.current_quest_id))
    
    # Propagate configs to web_server
    status_queue.put(('__config__', OmegaConf.to_container(config, resolve=True)))

    screen = None
    if not config.get("headless", False):
        pygame.init()
        screen_width, screen_height = 640, 566
        screen = pygame.display.set_mode((screen_width, screen_height))
        pygame.display.set_caption("Pokemon Red")
        # Limit main loop to 30 FPS when rendering
        loop_clock = pygame.time.Clock()

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
    
    # UI update throttling: only by time interval (2 FPS max)
    last_ui_update_time = time.time()
    ui_update_min_interval = 0.5  # Minimum seconds between UI updates (2 FPS max)
    
    current_quest = None
    action_source = "human"

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
  
    # UI thread for web server
    ui_thread_started = False
    if not config.get("headless", False):
        try:
            # Import and start the Flask web server
            from web.web_server import start_server
            
            ui_thread = threading.Thread(
                target=start_server, 
                args=(status_queue, '0.0.0.0', 8080), 
                daemon=True
            )
            ui_thread.start()
            ui_thread_started = True
            print("Web UI server started on http://localhost:8080")
            # Signal that the game has started
            shared.game_started.set()
            
        except Exception as e:
            print(f"Failed to start web UI server: {e}")
            raise
    
    if config.get("save_state", True) and run_info: # Check config for save_state
        # Save the initial emulator state using RunManager
        save_initial_state(env, run_info)

    # Function to update quest UI with environment data
    def update_quest_ui():
        nonlocal total_steps, action_source
        try:
            # Update basic stats
            status_queue.put(('__total_steps__', total_steps))

            # Get current coordinates
            x, y, map_id = env.get_game_coords()
            map_name = env.get_map_name_by_id(map_id)

            global_y, global_x = local_to_global(y, x, map_id)

            # Send location data with proper global coords
            status_queue.put(('__location__', {
                'x': x, 'y': y,
                'map_id': map_id,
                'map_name': map_name,
                'gx': global_x,
                'gy': global_y
            }))


            # Send current quest info
            if quest_manager and quest_manager.current_quest_id:
                status_queue.put(('__current_quest__', quest_manager.current_quest_id))

            # Send additional environment data
            try:
                dialog = env.read_dialog() or ""
                status_queue.put(('__dialog__', dialog.strip()))
            except:
                status_queue.put(('__dialog__', ""))

            # Send Pokemon team data
            try:
                party_data = []
                party_size = env.read_m("wPartyCount")
                for i in range(party_size):
                    species = env.read_m(f"wPartyMon{i+1}Species")
                    if species == 0:
                        continue
                    # Map memory code to human-friendly name
                    species_name = Species(species).name.title()
                    # Status condition
                    status_code = env.read_m(f"wPartyMon{i+1}Status")
                    status_name = StatusCondition(status_code).get_status_name()
                    # Experience (3-byte field)
                    bank, exp_addr = env.pyboy.symbol_lookup(f"wPartyMon{i+1}Exp")
                    exp0 = env.read_m(exp_addr)
                    exp1 = env.read_m(exp_addr + 1)
                    exp2 = env.read_m(exp_addr + 2)
                    exp_val = exp0 + (exp1 << 8) + (exp2 << 16)
                    party_data.append({
                        'slot': i,
                        'id': species,
                        'speciesName': species_name,
                        'status': status_name,
                        'experience': exp_val,
                        'level': env.read_m(f"wPartyMon{i+1}Level"),
                        'hp': env.read_short(f"wPartyMon{i+1}HP"),
                        'maxHp': env.read_short(f"wPartyMon{i+1}MaxHP"),
                    })
                status_queue.put(('__pokemon_team__', party_data))
            except Exception as e:
                logger.debug(f"Failed to get pokemon team for UI: {e}")

            # Send game statistics
            try:
                stats = {
                    'money': env.item_handler.read_money(),
                    'badges': bin(env.read_m("wObtainedBadges")).count('1'),
                    'pokedex_seen': sum(bin(env.read_m(0xD2F7 + i)).count('1') for i in range(19)),
                    'pokedex_caught': sum(bin(env.read_m(0xD2E3 + i)).count('1') for i in range(19)),
                    'steps': total_steps # Using the loop's total_steps
                }
                for stat_name, value in stats.items():
                    status_queue.put((f'__stats_{stat_name}__', value))
            except Exception as e:
                logger.debug(f"Failed to get game stats for UI: {e}")

            # Send quest data
            if quest_manager and quest_manager.quest_progression_engine:
                quest_statuses = quest_manager.quest_progression_engine.get_quest_status()
                trigger_statuses = quest_manager.quest_progression_engine.get_trigger_status()
                
                # Combine statuses into a single object for the UI
                all_statuses = {
                    "quests": quest_statuses,
                    "triggers": trigger_statuses
                }
                
                status_queue.put(('__quest_data__', all_statuses))

            # Send navigation status
            nav_status = getattr(navigator, 'navigation_status', 'unknown')
            status_queue.put(('__nav_status__', nav_status))
            
            # FIXED: Send proper status
            if dialog and dialog.strip():
                status = "Dialog Active"
            elif nav_status == "navigating":
                status = "Navigating"
            elif quest_manager.current_quest_id:
                status = f"Quest {quest_manager.current_quest_id:03d}"
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
                        "minimap": warp_minimap_data.tolist(),
                        "debug_info": warp_debug_info
                    }
                    status_queue.put(('__warp_minimap__', combined_data))
            except Exception as e:
                print(f"Error getting warp minimap data: {e}")

            # Send Grok status
            status_queue.put(('__grok_enabled__', grok_enabled.is_set()))
            
            # Send emulator screen data to UI
            try:
                raw_screen_frame = env.render() # This is a numpy array HxW or HxWx1 (grayscale)
                if raw_screen_frame is not None:
                    img_height, img_width = raw_screen_frame.shape[0], raw_screen_frame.shape[1]
                    # Convert to RGB bytes for Pillow Image.frombytes
                    if raw_screen_frame.ndim == 2:
                        # Grayscale -> replicate channels
                        rgb_frame = np.stack((raw_screen_frame,) * 3, axis=-1)
                    elif raw_screen_frame.ndim == 3:
                        # RGB or RGBA -> keep first three channels
                        rgb_frame = raw_screen_frame[:, :, :3]
                    else:
                        rgb_frame = None
                    img_mode = "RGB"

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
        """Update UI only if enough time has passed (based on ui_update_min_interval)"""
        nonlocal last_ui_update_time
        current_time = time.time()
        # Throttle to 2 FPS max
        if current_time - last_ui_update_time >= ui_update_min_interval:
            update_quest_ui()
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
        print(f'play.py: mains(): GROK ON: config.agent.grok_on={config.agent.grok_on}\n')
        # Determine API key: check config, then GROK_API_KEY or XAI_API_KEY env vars, then CLI arg
        env_key = os.getenv("GROK_API_KEY")
        xai_key = os.getenv("XAI_API_KEY")
        api_key = (
            config.get("agent", {}).get("api_key")
            or env_key
            or xai_key
            or args.grok_api_key
        )
        if api_key:
            try:
                grok_agent = SimpleAgent(
                    reader=env,
                    quest_manager=quest_manager,
                    navigator=navigator,
                    env_wrapper=env,
                    xai_api_key=api_key,
                    status_queue=status_queue
                )
                grok_enabled.set()  # Activate AI control based on config
                print("🤖 Grok agent initialized and enabled")
                logger.log_system_event("GROK_INITIALIZED", {
                    'message': 'Grok agent initialized',
                    'model': 'grok-3-mini'
                })
            except Exception as e:
                print(f"❌ Failed to initialize Grok agent: {e}")
                logger.log_error("GROK_INIT_FAILED", f"Failed to initialize Grok: {str(e)}", {})
                grok_agent = None
        else:
            print("⚠️  Grok enabled but no API key found (use --grok_api_key, GROK_API_KEY or XAI_API_KEY env var, or config)")
            raise Exception("Grok enabled but no API key found (use --grok_api_key, GROK_API_KEY or XAI_API_KEY env var, or config)")

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
            navigator.load_coordinate_path(current_quest)
            print(f"✓ Navigator loaded quest {current_quest} (syncing environment)")
        
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
    
    # Store item handler reference in environment
    env.item_handler = ItemHandler(env)
    
    # Store reference in environment
    env.quest_progression_engine = quest_progression_engine

    # Grok toggle control: when True Grok runs each tick, when False disabled
    grok_active = False
    # Initialize non-blocking Grok action fetch
    grok_thread = None
    grok_action = None
    def start_grok_thread():
        nonlocal grok_thread, grok_action
        if grok_agent is None:
            return
        def fetch_action():
            nonlocal grok_action
            try:
                state = extract_structured_game_state(env_wrapper=env, reader=env, quest_manager=quest_manager)
                grok_action = grok_agent.get_action(state)
            except Exception as e:
                print(f"Error fetching Grok action: {e}")
                grok_action = None
        grok_thread = threading.Thread(target=fetch_action, daemon=True)
        grok_thread.start()


    # Enter main loop
    while running:
        # Wait until user clicks Start before stepping the environment or running quest progression
        if not game_started.is_set():
            if not env.headless:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                # Tick emulator and render frame even when not started
                env.pyboy.tick()
                raw_frame = env.render()
                processed_frame = process_frame_for_pygame(raw_frame)
                update_screen(screen, processed_frame, screen_width, screen_height)
                # Update UI while idle
                update_ui_if_needed()
                # Cap to 5 FPS
                loop_clock.tick(5)
            else:
                # Headless mode: tick emulator and update UI
                env.pyboy.tick()
                update_ui_if_needed()
                time.sleep(0.1)
            continue

        if env.map_history[-1] != env.read_m("wCurMap"):
            print(f'line 986 of play.py in main(): player location: {env.get_game_coords()}')
        
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
                                    if not navigator.load_coordinate_path(current_q):
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
                            raise Exception("play.py: main(): '6' key: Manual warp trigger")
                            # "6" key: Manual warp trigger
                            print("Navigator: Manual input - Key 6 -> Manual warp trigger")
                            if navigator.manual_warp_trigger():
                                print("Navigator: Manual warp executed successfully")
                            else:
                                print("Navigator: Manual warp failed")
                            last_key_pressed = None  # Don't repeat warp action
                            continue
                        elif event.key == pygame.K_SPACE and args.interactive_mode and grok_enabled.is_set():
                            grok_active = not grok_active
                            print(f"Grok {'enabled' if grok_active else 'disabled'} by toggle")
                            if grok_active:
                                start_grok_thread()
                            continue
                        else:
                            # Regular key handling
                            print(f"play.py: main(): keydown event: {event.key}; current player location: {env.get_game_coords()}\n\n\n\n")
                            key_action = navigator.handle_pygame_event(event)
                            if key_action is not None:
                                current_action = key_action # Store manual action
                                last_key_pressed = key_action
                                key_repeat_timer = current_time
                elif event.type == pygame.KEYUP:
                    # Stop key repeat when key is released
                    last_key_pressed = None

        if total_steps % 100 == 0:  # Every 100 steps
            # print(f"🔍 [DEBUG] Step {total_steps}: current_action={current_action}, grok_enabled={grok_enabled.is_set()}, grok_agent={grok_agent is not None}")
            pass

        # GROK_INTEGRATION_POINT: only call Grok when toggled on
        if current_action is None and grok_agent and grok_enabled.is_set() and grok_active:
            # Non-blocking Grok action fetch
            if grok_thread is None:
                start_grok_thread()
            elif not grok_thread.is_alive():
                # Retrieve the Grok action from background thread
                retrieved = grok_action
                grok_thread = None
                grok_action = None
                # If Grok returned PATH_FOLLOW_ACTION, replicate manual '5' key behavior
                if retrieved == PATH_FOLLOW_ACTION:
                    raw_dialog = env.read_dialog() or ''
                    if raw_dialog.strip():
                        # Dialog active: override to B button
                        print("Dialog active; overriding Grok PATH_FOLLOW_ACTION to B press")
                        current_action = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B)
                    else:
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
                            if not navigator.load_coordinate_path(current_q):
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
                    
                else:
                    # Regular non-path-follow Grok action
                    current_action = retrieved
        
        if env.quest_manager.current_quest_id == 1:
            search_strings = ["Welcome to the", "My", "People", "inhabited", "creatures", "Fo", "Others", "First"]
            if any(s in env.read_dialog() for s in search_strings) and not env.never_run_again:
                print(f"play.py: main(): is our a key thing triggering??? matching items {any(s in env.read_dialog() for s in search_strings)} in {env.read_dialog()}")
                noop_action = getattr(env, "a", 4)
                print(f"play.py: main(): noop_action: {noop_action}")
                # Advance with a
                obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                    env,
                    noop_action,
                    quest_manager,
                    navigator,
                    logger,
                    total_steps,
                )
                print(f"play.py: main(): noop_action: got past the execute_action_step {noop_action}")
                env.pyboy.tick()
                raw_frame = env.render()
                processed_frame_rgb = process_frame_for_pygame(raw_frame)  # Process the frame
                update_screen(screen, processed_frame_rgb, screen_width, screen_height)
                # Update UI without advancing game state
                update_ui_if_needed()
                loop_clock.tick(30)

                time.sleep(0.05)
                continue
        
        if current_action is None:
            # When no explicit player/AI action is available we must still
            # advance the emulator so that StageManager, quest triggers and
            # other time-based systems can execute.  Feed a known *noop*
            # button index (defaults to 4 if the environment hasn't defined
            # one) through the unified `execute_action_step` helper so state
            # progression remains fully synchronised.

            # intro handler. messy, but it works.
            if "na..." in env.read_dialog():
                print(f"play.py: main(): na... in dialog")
                noop_action = getattr(env, 'a', 4)
                obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                    env,
                    noop_action,
                    quest_manager,
                    navigator,
                    logger,
                    total_steps,
                )

                print(f"play.py: main(): pressing a single a button: pressing single button: {noop_action}")
                env.pyboy.tick()
                raw_frame = env.render()
                processed_frame_rgb = process_frame_for_pygame(raw_frame)  # Process the frame
                update_screen(screen, processed_frame_rgb, screen_width, screen_height)
                # Update UI without advancing game state
                update_ui_if_needed()
                loop_clock.tick(30)

                time.sleep(0.05)
                continue
            
            # noops to generate dummy actions, which are then filtered by stage_helper.
            if env.quest_manager.current_quest_id == 1 and "NAME" not in env.read_dialog():
                noop_action = getattr(env, 'noop_button_index', 8)

                # Advance one frame with the noop
                obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                    env,
                    noop_action,
                    quest_manager,
                    navigator,
                    logger,
                    total_steps,
                )

                # Prevent a second manual tick/render later this iteration
                already_stepped = True

                # NOTE: We deliberately do *not* add the noop to `recorded_playthrough`
                # to avoid cluttering replays with frames that carry no semantic
                # intent.

            # look right to charmander, press a a bunch until naming screen
            if env.quest_manager.current_quest_id == 4 and env.get_game_coords() == (5, 3, 40):
                noop_action = None
                if not env._get_direction(env.pyboy.game_area()) == "right" and env.read_dialog() == '' and env.party_size < 1:
                    noop_action = getattr(env, 'right', 2)
                    obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                        env,
                        noop_action,
                        quest_manager,
                        navigator,
                        logger,
                        total_steps,
                    )
                elif env._get_direction(env.pyboy.game_area()) == "right" and env.read_dialog() == '' and env.party_size < 1:
                    noop_action = getattr(env, 'a', 4)
                    obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                        env,
                        noop_action,
                        quest_manager,
                        navigator,
                        logger,
                        total_steps,
                    )
                elif env._get_direction(env.pyboy.game_area()) == "right" and env.read_dialog() != '' and env.party_size < 1:
                    noop_action = getattr(env, 'a', 4)
                    obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                        env,
                        noop_action,
                        quest_manager,
                        navigator,
                        logger,
                        total_steps,
                    )
                elif "►YES\nNO\ngive a nickname" in env.read_dialog() or "Do you want to" in env.read_dialog():
                    noop_action = getattr(env, 'a', 4)
                    obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                        env,
                        noop_action,
                        quest_manager,
                        navigator,
                        logger,
                        total_steps,
                    )
                elif "A B C" in env.read_dialog() or "T U V" in env.read_dialog():
                    continue
                elif env.party_size > 0 and env.read_dialog() == '':
                    noop_action = getattr(env, 'a', 4)
                    obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                        env,
                        noop_action,
                        quest_manager,
                        navigator,
                        logger,
                        total_steps,
                    )

                # print(f"play.py: main(): CHARMANDER:pressing a single '{noop_action}' button: pressing single button: {noop_action}")
                env.pyboy.tick()
                raw_frame = env.render()
                processed_frame_rgb = process_frame_for_pygame(raw_frame)  # Process the frame
                update_screen(screen, processed_frame_rgb, screen_width, screen_height)
                # Update UI without advancing game state
                update_ui_if_needed()
                loop_clock.tick(30)

                time.sleep(0.1)
                continue
                
        if grok_enabled.is_set() and current_action is None and env.headless:
            time.sleep(0.01)  # Very short sleep when waiting for Grok
        
        if current_action is None: # Still no action (e.g. interactive mode with no key press)
            if not env.headless: # Only render UI in interactive, do not advance game state
                # Always tick the emulator even without action
                env.pyboy.tick()
                raw_frame = env.render()
                processed_frame_rgb = process_frame_for_pygame(raw_frame)  # Process the frame
                update_screen(screen, processed_frame_rgb, screen_width, screen_height)
                # Update UI without advancing game state
                update_ui_if_needed()
                loop_clock.tick(30)
            else: # Headless, no action -> could be intentional pause
                # Always tick the emulator in headless mode too
                env.pyboy.tick()
                # Render UI update without game state advancement
                update_ui_if_needed()
                # Slow down headless idle loop without advancing game state
                time.sleep(0.05)
        
        # CRITICAL FIX: Quest progression should always run, even without player actions
        # This was the main bug - quest triggers were never evaluated when idle!
        # BUT: Don't spam it every tick - only check when map actually changes
        if quest_progression_engine:
            current_map_id = env.get_game_coords()[2]
            
            # Try to check map history, but don't let failures block quest progression
            map_changed = False
            try:
                if hasattr(env, 'map_history') and len(env.map_history) >= 2:
                    map_changed = env.map_history[-2] != env.map_history[-1]
                    # print(f"[DEBUG] Map history check: changed={map_changed}, history={list(env.map_history)}")
                else:
                    # print(f"[DEBUG] Map history not available or too short (len={len(env.map_history) if hasattr(env, 'map_history') else 'N/A'})")
                    pass
            except Exception as e:
                # print(f"[DEBUG] Map history check failed: {e}")
                pass
            
            # ONLY run quest progression when map actually changes (to prevent spam)
            try:
                if map_changed:
                    # print(f"[DEBUG] Calling quest_progression_engine.step() in idle state at step {total_steps}")
                    quest_progression_engine.step(trigger_evaluator)
                    # print(f"[DEBUG] Quest progression step completed in idle state")
            except Exception as e:
                print(f"[ERROR] Quest progression failed in idle state: {e}")
                logger.log_error("QUEST_PROGRESSION_IDLE", f"Error in idle quest progression: {str(e)}", {
                    'current_map_id': current_map_id,
                    'total_steps': total_steps
                })

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
            elif grok_agent and current_action is not None and hasattr(grok_agent, 'last_response'):
                action_source = 'grok'
            elif navigator.navigation_status == "navigating":
                action_source = 'navigator'
            else:
                action_source = 'ai'

            # Store for UI update
            update_quest_ui.last_action_source = action_source
            
            # Record action for replay functionality
            recorded_playthrough.append({
                'step': total_steps,
                'action': current_action,
                'original_action': original_action if original_action != current_action else None,
                'timestamp': time.time(),
                'source': action_source
            })

        # Execute environment step only if we have a valid action
        if current_action is not None:
            obs, reward, terminated, truncated, info, total_steps = execute_action_step(
                env, current_action, quest_manager, navigator, logger, total_steps
            )
            total_reward += reward
        else:
            # No action: skip stepping the environment to prevent NoneType errors
            obs, reward, terminated, truncated, info = None, 0.0, False, False, {}

        # # Navigation System Monitoring - check after each step
        # if navigation_monitor:
        #     try:
        #         navigation_monitor.check_at_quest_step()
        #     except Exception as e:
        #         print(f"NavigationMonitor error in step check: {e}")

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
            # print(f"[MapTransition] Map ID changed from {last_map_id} to {new_map_id}")
            
            # Navigation System Monitoring - check at map transition
            if navigation_monitor:
                try:
                    navigation_monitor.check_at_map_transition()
                except Exception as e:
                    print(f"NavigationMonitor error in map transition check: {e}")
            
            last_map_id = new_map_id

        # FIXED: Update quest progression engine with proper trigger evaluator
        # print(f"[DEBUG] Quest progression action check: quest_progression_engine={quest_progression_engine is not None}, action={current_action}, map_id={env.get_game_coords()[2]}")
        
        if quest_progression_engine:
            current_map_id = env.get_game_coords()[2]
            
            # Store previous quest for change detection
            previous_quest = quest_manager.current_quest_id
            
            # Call quest progression engine to evaluate triggers and progress quests
            try:
                # print(f"[DEBUG] Calling quest_progression_engine.step() at step {total_steps}, map {current_map_id}, current_quest={previous_quest}")  # Suppressed verbose debug
                quest_progression_engine.step(trigger_evaluator)
                # print(f"[DEBUG] Quest progression step completed successfully")  # Suppressed verbose debug
                
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
                    # print(f"[DEBUG] No quest transition, remained at quest {previous_quest}")  # Suppressed verbose debug
                    pass
                        
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

        # Throttled UI update for SSE messages and statuses (2 FPS max)
        update_ui_if_needed()

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
            processed_frame_rgb = process_frame_for_pygame(raw_frame)  # Process the frame
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