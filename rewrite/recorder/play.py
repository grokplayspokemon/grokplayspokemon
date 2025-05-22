# play.py
import argparse
import numpy as np
import json
import pygame
import time
import sys # Added for sys.path manipulation
import math # Added for custom rounding
from pathlib import Path
from typing import Optional
from omegaconf import OmegaConf  # For loading YAML config overrides
import threading
import queue
import tkinter as tk
import tkinter.ttk as ttk
from trigger_evaluator import TriggerEvaluator
from datetime import datetime

from environment import RedGymEnv, VALID_ACTIONS
from pyboy.utils import WindowEvent
from global_map import local_to_global
from navigator import InteractiveNavigator # Added import for the navigator class

# Add project root for global_map import if play.py is in a subdirectory
# Assuming play.py is in DATAPlaysPokemon/rewrite/recorder/
project_root_path = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root_path))

# global_map is used by navigator.py now, but play.py's sys.path manipulation makes it available.
# from global_map import local_to_global, global_to_local # No longer directly used in play.py

# Load quest definitions
QUESTS_FILE = Path(__file__).parent / "required_completions.json"
with open(QUESTS_FILE, 'r') as f:
    QUESTS = json.load(f)
# Shared queue for UI updates
status_queue = queue.Queue()

# UI thread function
def start_quest_ui():
    root = tk.Tk()
    # Make window large and non-resizable
    root.title("Quest Progress")
    # Maximize window height to show as many quests as possible
    try:
        root.state('zoomed')  # Works on Windows and some Linux window managers
    except Exception:
        try:
            root.attributes('-zoomed', True)  # Fallback for other platforms
        except Exception:
            # Could not maximize, fall back to default size
            root.geometry("1000x700")
    root.minsize(800, 600)
    root.resizable(True, True)
    # Increase default font sizes
    import tkinter.font as tkfont
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(size=12)
    header_font = tkfont.nametofont("TkHeadingFont") if "TkHeadingFont" in tkfont.names() else default_font
    header_font.configure(size=12, weight="bold")
    # Apply style for Treeview
    style = ttk.Style(root)
    style.configure("Treeview", rowheight=24, font=(default_font.actual('family'), 12))
    style.configure("Treeview.Heading", font=(default_font.actual('family'), 12, 'bold'))
    tree = ttk.Treeview(root, columns=('status',), show='tree headings')
    tree.heading('#0', text='Description')
    tree.heading('status', text='Status')
    tree.column('status', width=100)
    # Configure tree columns and tags for better visibility
    tree.column('#0', width=120)
    tree.tag_configure('done', foreground='green')
    tree.tag_configure('pending', foreground='red')
    # Add scrollbars to handle long quest lists
    vsb = ttk.Scrollbar(root, orient='vertical', command=tree.yview)
    vsb.pack(side='right', fill='y')
    tree.configure(yscrollcommand=vsb.set)
    hbar = ttk.Scrollbar(root, orient='horizontal', command=tree.xview)
    hbar.pack(side='bottom', fill='x')
    tree.configure(xscrollcommand=hbar.set)
    tree.pack(fill='both', expand=True)
    # Label for current location
    location_label = tk.Label(root, text="Location: N/A")
    location_label.pack(side='top', fill='x')

    # Function to describe trigger criteria
    def describe_trigger(trg):
        ttype = trg.get('type')
        if ttype == 'current_map_id_is':
            return f"Map ID == {trg['map_id']}"
        elif ttype == 'previous_map_id_was':
            return f"Previous Map ID == {trg['map_id']}"
        elif ttype == 'dialog_contains_text':
            return f"Dialog contains \"{trg['text']}\""
        elif ttype == 'party_size_is':
            return f"Party size == {trg['size']}"
        elif ttype == 'battle_won':
            return f"Battle won vs {trg.get('opponent_identifier','')}"
        elif ttype == 'item_received_dialog':
            return f"Item received dialog \"{trg['text']}\""
        elif ttype == 'item_is_in_inventory':
            return f"Inventory has >= {trg.get('quantity_min',1)} x {trg.get('item_name','')}"
        elif ttype == 'party_hp_is_full':
            return "Party HP is full"
        else:
            return str(trg)

    # Populate rows with quests and their criteria
    for q in QUESTS:
        qid = q['quest_id']
        # Insert quest as parent with description
        parent_text = f"Quest {qid}: {q.get('begin_quest_text','')}"
        tree.insert('', 'end', iid=qid, text=parent_text, values=('Pending',))
        # Insert criteria as children
        for idx, trg in enumerate(q.get('event_triggers', [])):
            trigger_id = f"{qid}_{idx}"
            crit_text = describe_trigger(trg)
            tree.insert(qid, 'end', iid=trigger_id, text=crit_text, values=('Pending',))
    # Auto-expand all quests to show triggers
    for q in QUESTS:
        tree.item(q['quest_id'], open=True)

    # Snap to first pending quest helper
    def snap_to_current():
        # Snap only among quests still present
        root_ids = tree.get_children()
        for q in QUESTS:
            qid = q['quest_id']
            if qid not in root_ids:
                continue
            if tree.set(qid, 'status') == 'Pending':
                tree.see(qid)
                break
    # One-time snap flag for polling
    snapped = [False]

    # Poll update queue
    def poll():
        try:
            while True:
                item_id, data = status_queue.get_nowait()
                if item_id == '__location__':
                    gx, gy, map_id, map_name = data
                    location_label.config(text=f"Location: ({gx}, {gy}), Map ID: {map_id}, {map_name}")
                else:
                    # Remove fully completed quests from display
                    if '_' not in item_id and data:
                        try:
                            tree.delete(item_id)
                        except Exception:
                            pass
                    else:
                        status_text = 'Done' if data else 'Pending'
                        try:
                            tree.set(item_id, 'status', status_text)
                            tree.item(item_id, tags=('done',) if data else ('pending',))
                        except Exception:
                            pass
        except queue.Empty:
            pass
        # Perform one-time snap after initial statuses are set
        if not snapped[0]:
            snap_to_current()
            snapped[0] = True
        root.after(200, poll)
    root.after(200, poll)
    root.mainloop()

# Start the UI thread
threading.Thread(target=start_quest_ui, daemon=True).start()

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
    # For play.py, only use an initial state name if the file actually exists.
    init_state_name = None
    if initial_state_path:
        p = Path(initial_state_path)
        # Full path to a state file overrides default
        if p.is_file():  # Provided a full path to a .state file
            # Store full path so environment can detect and load directly
            init_state_name = str(p)
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
        "emulator_delay": emulator_delay,
        "headless": False, # play.py is interactive, so headless is False
        "state_dir": Path("./states/new"), # Relative to play.py
        "init_state": init_state_name, # Use provided state, or None
        "action_freq": 24,
        "max_steps": 1_000_000,
        "save_video": save_video,
        "fast_video": fast_video,
        "n_record": n_record,
        "perfect_ivs": perfect_ivs,
        "reduce_res": reduce_res,
        "gb_path": rom_path,
        "log_frequency": log_frequency,
        "two_bit": two_bit,
        "auto_flash": auto_flash,
        "required_tolerance": required_tolerance,
        "disable_wild_encounters": disable_wild_encounters,
        "disable_ai_actions": disable_ai_actions, # AI actions likely disabled for interactive play
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
    }

ACTION_MAPPING_PYGAME_TO_INT = {
    pygame.K_DOWN: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN),
    pygame.K_LEFT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT),
    pygame.K_RIGHT: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT),
    pygame.K_UP: VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP),
    pygame.K_a: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A),
    pygame.K_s: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B),
    pygame.K_RETURN: VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START),
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

def handle_follow_path_interactive(navigator: InteractiveNavigator):
    """Prompt for number of steps, return to path, and walk step-by-step."""
    try:
        print("\n== FOLLOW RECORDED QUEST PATH STEPS ==")
        steps = int(input("Enter number of steps to follow along recorded path: "))
        # Ensure we're back on the recorded path before stepping
        navigator.check_json_path_stagnation_and_assist()
        # Step through each move one at a time
        for i in range(steps):
            print(f"-- Follow step {i+1}/{steps} --")
            # Schedule next single step; stop if no more path
            if not navigator.schedule_next_path_step():
                break
            # Execute this segment until complete
            while navigator.navigation_status in ["planning", "navigating"]:
                msg, action_int, step_res = navigator.step()
                if msg:
                    print(msg)
        print("Navigator: Completed requested follow-path steps.")
    except Exception as e:
        print(f"Invalid input for follow_path: {e}")

def main():
    parser = argparse.ArgumentParser(description='Play Pokemon Red interactively and record actions')
    parser.add_argument('--rom', type=str, help='Path to the Game Boy ROM file', default="./PokemonRed.gb")
    parser.add_argument('--state', type=str, help='Path to the initial state file (e.g., xxx.state)', default="has_pokedex_nballs") # Default to name, RedGymEnv prepends dir
    parser.add_argument('--name', type=str, help='Name for the output JSON action file (without extension)', default=None)
    parser.add_argument('--infinite-money', action='store_true', help='Enable infinite money')
    parser.add_argument('--infinite-health', action='store_true', help='Enable infinite health')
    args = parser.parse_args()

    # Load YAML config overrides
    config_path = Path(__file__).parent.parent / 'config.yaml'
    try:
        yaml_cfg = OmegaConf.load(str(config_path))
        yaml_env = yaml_cfg.env
    except Exception:
        yaml_env = None

    # Determine infinite flags: YAML takes precedence unless CLI flag set
    infinite_money_flag = (args.infinite_money or (yaml_env.infinite_money if yaml_env and 'infinite_money' in yaml_env else False))
    infinite_health_flag = (args.infinite_health or (yaml_env.infinite_health if yaml_env and 'infinite_health' in yaml_env else False))

    # Set up run directory for quest/trigger persistence
    recordings_dir = Path(__file__).parent / "replays" / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    # Determine naming base: use provided --name or reuse latest numeric run
    if args.name:
        name_base = Path(args.name).stem
    else:
        existing_dirs = [d for d in recordings_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        if existing_dirs:
            max_id = max((int(d.name) for d in existing_dirs))
            name_base = f"{max_id:03d}"
            # If an end state exists from the last run, load it
            prev_end = recordings_dir / name_base / f"{name_base}_end.state"
            if prev_end.is_file():
                args.state = str(prev_end)
        else:
            # First run
            name_base = "001"
    # Create run directory named after name_base
    run_dir = recordings_dir / name_base
    run_dir.mkdir(parents=True, exist_ok=True)

    env_config_dict = get_default_config(
        args.rom,
        args.state,
        infinite_money=infinite_money_flag,
        infinite_health=infinite_health_flag,
    )
    
    # Convert to DictConfig for RedGymEnv if it expects it (optional based on RedGymEnv)
    # from omegaconf import DictConfig (add this import if needed)
    # env_config = DictConfig(env_config_dict)
    env_config = env_config_dict # Assuming RedGymEnv can take a dict

    env = RedGymEnv(env_config=env_config)
    # Disable automatic environment replay directory creation; manual run_dir will be used
    env.record_replays = False
    navigator = InteractiveNavigator(env) # Initialize navigator
    
    obs, info = env.reset() # Initial reset
    # Use our play.py run_dir for path trace recording AFTER reset has cleared env.current_run_dir
    env.current_run_dir = run_dir

    # Save initial state to run directory
    import io
    start_buf = io.BytesIO()
    env.pyboy.save_state(start_buf)
    start_buf.seek(0)
    # Name initial state file based on name_base
    start_state_file = run_dir / f"{name_base}_start.state"
    with open(start_state_file, "wb") as f:
        f.write(start_buf.read())
    print(f"Start state saved to {start_state_file}")
    # Log initial coordinate in path trace
    env.update_path_trace()

    recorded_playthrough = []
    # Initialize fresh completion state for this run (ignore JSON defaults)
    quest_completed = {q['quest_id']: False for q in QUESTS}
    trigger_completed = {}
    for q in QUESTS:
        qid = q['quest_id']
        for idx, trg in enumerate(q.get('event_triggers', [])):
            tid = f"{qid}_{idx}"
            trigger_completed[tid] = False
    # Clear completed flags in QUESTS definitions to reflect fresh run
    for q in QUESTS:
        q['completed'] = False
        for trg in q.get('event_triggers', []):
            trg['completed'] = False

    # Override with persisted statuses if available
    qstatus_file = run_dir / "quest_status.json"
    if qstatus_file.is_file():
        try:
            with open(qstatus_file) as f:
                loaded_q = json.load(f)
            for qid, val in loaded_q.items():
                if qid in quest_completed:
                    quest_completed[qid] = bool(val)
        except Exception:
            pass
    tstatus_file = run_dir / "trigger_status.json"
    if tstatus_file.is_file():
        try:
            with open(tstatus_file) as f:
                loaded_t = json.load(f)
            for tid, val in loaded_t.items():
                if tid in trigger_completed:
                    trigger_completed[tid] = bool(val)
        except Exception:
            pass

    # Sync in-memory QUESTS definitions with loaded statuses
    for q in QUESTS:
        qid = q['quest_id']
        q['completed'] = quest_completed.get(qid, False)
        for idx, trg in enumerate(q.get('event_triggers', [])):
            tid = f"{qid}_{idx}"
            trg['completed'] = trigger_completed.get(tid, False)

    # Seed UI with persisted trigger and quest statuses
    evaluator = TriggerEvaluator(env)
    evaluator.prev_map_id = env.get_game_coords()[2]
    for q in QUESTS:
        qid = q['quest_id']
        triggers = q.get('event_triggers', [])
        for idx, trg in enumerate(triggers):
            tid = f"{qid}_{idx}"
            status_queue.put((tid, trigger_completed.get(tid, False)))
        # Update quest status
        status_queue.put((qid, quest_completed.get(qid, False)))
    debounce_time = 0.1 # seconds for manual input debounce
    last_action_time = 0

    pygame.init()
    SCREENSHOTS_DIR = Path("./screenshots")
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
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
    print("Press 4 to return to recorded quest path.")
    print("Press 5 to follow path steps.")
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
                        screenshot_name = f"screenshot_{pygame.time.get_ticks()}.png"
                        screenshot_path = Path(SCREENSHOTS_DIR) / screenshot_name
                        pygame.image.save(screen, str(screenshot_path))
                        print(f"Screenshot saved to {screenshot_path}")

                    elif event.key == pygame.K_4: # Snap to nearest recorded path coordinate
                        print("Navigator: Snapping to nearest coordinate on recorded path.")
                        if navigator.snap_to_nearest_coordinate():
                            print("Navigator: Successfully snapped to nearest coordinate.")
                        else:
                            print("Navigator: Failed to snap to nearest coordinate.")

                    elif event.key == pygame.K_5: # Move to next coordinate in sequence with automatic warp traversal
                        raw_dialog = env.read_dialog() or ''
                        if raw_dialog.strip():
                            print("Navigation paused: dialog active, cannot move to next coordinate.")
                        else:
                            # First check for warps that need traversal (including adjacent ones)
                            is_adjacent_warp, adjacent_warp_info, required_direction = navigator._find_adjacent_warp_for_navigation()
                            
                            if is_adjacent_warp and adjacent_warp_info and required_direction is not None:
                                # Automatic adjacent warp traversal logic
                                try:
                                    # Map direction values to pygame keys and action descriptions
                                    direction_to_key_and_name = {
                                        0: (pygame.K_DOWN, "DOWN"),     # direction 0 = down
                                        4: (pygame.K_UP, "UP"),         # direction 4 = up  
                                        8: (pygame.K_LEFT, "LEFT"),     # direction 8 = left
                                        12: (pygame.K_RIGHT, "RIGHT")   # direction 12 = right
                                    }
                                    
                                    warp_target = adjacent_warp_info.get('target_map_name', 'unknown')
                                    
                                    if required_direction in direction_to_key_and_name:
                                        pygame_key, direction_name = direction_to_key_and_name[required_direction]
                                        print(f"Navigator: Adjacent warp detected. Moving {direction_name} to traverse warp to {warp_target}")
                                        
                                        if pygame_key in ACTION_MAPPING_PYGAME_TO_INT:
                                            warp_action = ACTION_MAPPING_PYGAME_TO_INT[pygame_key]
                                            obs, reward, terminated, truncated, info = env.step(warp_action)
                                            executed_action_this_frame = warp_action
                                            
                                            # Record the warp traversal action
                                            recorded_playthrough.append(executed_action_this_frame)
                                            
                                            print(f"Navigator: Adjacent warp traversal completed to {warp_target}")
                                            
                                            # Update navigator's position tracking after warp
                                            navigator.last_position = None  # Force position update on next check
                                        else:
                                            print(f"Navigator: Error - Direction key {pygame_key} not found in action mapping")
                                    else:
                                        print(f"Navigator: Error - Unknown required direction {required_direction}")
                                        
                                except Exception as e:
                                    print(f"Navigator: Error during adjacent warp traversal: {e}")
                                    # Fall back to normal navigation
                                    print("Navigator: Adjacent warp traversal failed. Attempting normal coordinate navigation.")
                                    is_adjacent_warp = False  # Force fallback to normal navigation
                            
                            # If no adjacent warp needs traversal, proceed with normal coordinate navigation
                            if not is_adjacent_warp:
                                # Check if Shift is held for multiple steps
                                mods = pygame.key.get_mods()
                                if mods & pygame.KMOD_SHIFT:
                                    # Shift+5: Move 5 steps forward
                                    steps = 5
                                    print(f"Navigator: Moving {steps} steps forward in coordinate sequence.")
                                    success_count = 0
                                    for i in range(steps):
                                        if navigator.move_to_next_coordinate():
                                            success_count += 1
                                            print(f"Navigator: Step {i+1}/{steps} completed.")
                                        else:
                                            print(f"Navigator: Step {i+1}/{steps} failed - stopping sequence.")
                                            break
                                    print(f"Navigator: Completed {success_count}/{steps} coordinate movements.")
                                else:
                                    # Regular 5: Move one step forward
                                    print("Navigator: Moving to next coordinate in sequence.")
                                    if navigator.move_to_next_coordinate():
                                        current_pos = navigator._get_player_global_coords()
                                        index = navigator.current_coordinate_index
                                        total = len(navigator.sequential_coordinates)
                                        print(f"Navigator: Successfully moved to coordinate {current_pos} (index {index}/{total}).")
                                    else:
                                        print("Navigator: Failed to move to next coordinate.")
                    
                    # Check for manual game control keys ONLY if navigation is not actively overriding
                    elif navigator.navigation_status in ["idle", "completed", "failed"]:
                        if current_time_sec - last_action_time > debounce_time: # Check debounce for manual keys
                            if event.key in ACTION_MAPPING_PYGAME_TO_INT:
                                manual_action_to_take = ACTION_MAPPING_PYGAME_TO_INT[event.key]
                                last_action_time = current_time_sec
                                keys_pressed_this_frame = True # Indicate a key was pressed for this type of input
                    # K_s: Save current game state manually
                    elif event.key == pygame.K_s and pygame.key.get_mods() & pygame.KMOD_CTRL:
                        state_name = f"manual_save_{datetime.now().strftime('%Y%m%d_%H%M%S')}.state"
                        state_path = env.current_run_dir / state_name if env.current_run_dir else Path(f"./{state_name}")
                        with open(state_path, "wb") as f_s:
                            env.pyboy.save_state(f_s)
                        print(f"Saved current game state to {state_path}")

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

            # SIMPLIFIED: Remove complex navigation status checking
            # Just handle manual actions when navigation is idle
            
            # --- Manual Action Step ---
            action_taken_by_player_this_turn = False
            if manual_action_to_take != -1:
                current_obs, current_reward, current_terminated, current_truncated, current_info = env.step(manual_action_to_take)
                executed_action_this_frame = manual_action_to_take
                action_taken_by_player_this_turn = True
            
            # Update global obs, info etc. based on what happened
            obs, reward, terminated, truncated, info = current_obs, current_reward, current_terminated, current_truncated, current_info

            # --- Record Action if one was taken ---
            if executed_action_this_frame is not None:
                recorded_playthrough.append(executed_action_this_frame)

            # # === Post-action Navigation Status Check ===
            # if navigator.navigation_status == "completed":
            #     nav_target_display = ""
            #     final_player_coords_str = "Player Coords: N/A"
            #     player_gx, player_gy = navigator._get_player_global_coords() or (None, None)
            #     if player_gx is not None and player_gy is not None:
            #         final_player_coords_str = f"Player Coords: ({player_gx},{player_gy})"

            #     current_global_target_at_completion = navigator.current_navigation_target_global

            #     if current_global_target_at_completion:
            #         nav_target_display = f"GLOBAL {current_global_target_at_completion}"
            #         glob_tgt_gx, glob_tgt_gy = current_global_target_at_completion
                    
            #         if player_gx is not None and abs(player_gx - glob_tgt_gx) <=1 and abs(player_gy - glob_tgt_gy) <=1: 
            #             print(f"Player has reached global target {current_global_target_at_completion}. {final_player_coords_str}. Resetting navigator.")
            #             navigator.astar_segment_no_global_progress_count = 0 # Reset for future goals
            #             navigator.reset_navigation() # This will clear current_navigation_target_global
            #         else:
            #             # Global target not reached
            #             increment_fail_counter = False
            #             current_player_coords = None
            #             if player_gx is not None and player_gy is not None:
            #                 current_player_coords = (player_gx, player_gy)
            #                 # Add to history if we are pursuing a global target and have valid current coordinates
            #                 if navigator.current_navigation_target_global: 
            #                     navigator.add_to_global_nav_history(current_player_coords)
                        
            #             # Condition 1: Check Manhattan distance progress
            #             if navigator.last_global_pos_before_astar_segment and current_global_target_at_completion and current_player_coords:
            #                 last_pos = navigator.last_global_pos_before_astar_segment
            #                 # current_player_coords is already (player_gx, player_gy)
            #                 global_target = current_global_target_at_completion

            #                 dist_before = navigator._manhattan_distance(last_pos, global_target)
            #                 dist_after = navigator._manhattan_distance(current_player_coords, global_target)

            #                 if dist_after >= dist_before:
            #                     print(f"Nav Info: No strict distance decrease. Dist before: {dist_before}, after: {dist_after}. Pos before: {last_pos}, after: {current_player_coords}")
            #                     increment_fail_counter = True
            #             elif not navigator.last_global_pos_before_astar_segment and current_player_coords:
            #                 # First segment attempt for this global goal, no 'before' distance to compare for this segment.
            #                 # However, oscillation can still be checked if it's the *very first* move for a new global target
            #                 # and it lands in a spot that was part of a previous failed attempt's history for the *same* target (if history wasn't cleared properly)
            #                 # But set_navigation_goal_global clears history, so this specific sub-case is less likely.
            #                 # The primary check here is for oscillations using the history that's now populated with current_player_coords.
            #                 pass 

            #             # Condition 2: Check for oscillation, even if distance seemed to improve or it was the first segment
            #             if current_player_coords and navigator.check_recent_oscillation(current_player_coords, count=2):
            #                 print(f"Nav Info: Oscillation detected for position {current_player_coords}. History: {navigator.global_nav_short_history}")
            #                 increment_fail_counter = True # Mark as failure if oscillating

            #             if increment_fail_counter:
            #                 navigator.astar_segment_no_global_progress_count += 1
            #                 print(f"A* segment completed with no significant progress or oscillation. Fail count: {navigator.astar_segment_no_global_progress_count}/{navigator.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD}")
            #                 if navigator.astar_segment_no_global_progress_count >= navigator.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD:
            #                     print(f"GLOBAL NAV FAILED: No progress/oscillation after {navigator.ASTAR_SEGMENT_NO_GLOBAL_PROGRESS_THRESHOLD} attempts for target {current_global_target_at_completion}. {final_player_coords_str}")
            #                     navigator.reset_navigation() # Full reset, clears global target and history
            #                 else:
            #                     print(f"Global target {current_global_target_at_completion} not yet reached. Re-initiating planning (retry {navigator.astar_segment_no_global_progress_count}). {final_player_coords_str}")
            #                     navigator.navigation_status = "planning" # Re-plan for the same global goal
            #             else:
            #                 # Progress was made (distance decreased AND no oscillation detected)
            #                 navigator.astar_segment_no_global_progress_count = 0 
            #                 print(f"Global target {current_global_target_at_completion} not yet reached. Re-initiating planning. {final_player_coords_str}")
            #                 navigator.navigation_status = "planning" # Re-plan
            #     elif navigator.current_navigation_target_local_grid: # Completed a local grid nav
            #         nav_target_display = f"LOCAL {navigator.current_navigation_target_local_grid}"
            #         print(f"Navigation path segment towards {nav_target_display} completed. {final_player_coords_str}")
            #         navigator.reset_navigation() 
            #     else: # Should not happen if a target was set
            #         nav_target_display = "UNKNOWN TARGET"
            #         print(f"Navigation path segment towards {nav_target_display} completed (no global/local target found?). {final_player_coords_str}")
            #         navigator.reset_navigation()
            
            # elif navigator.navigation_status == "failed":
            #     nav_target_display = ""
            #     final_player_coords_str = "Player Coords: N/A"
            #     player_gx, player_gy = navigator._get_player_global_coords() or (None, None)
            #     if player_gx is not None and player_gy is not None:
            #         final_player_coords_str = f"Player Coords: ({player_gx},{player_gy})"

            #     if navigator.current_navigation_target_global:
            #         nav_target_display = f"GLOBAL {navigator.current_navigation_target_global}"
            #     elif navigator.current_navigation_target_local_grid:
            #         nav_target_display = f"LOCAL {navigator.current_navigation_target_local_grid}"
            #     print(f"Navigation failed for {nav_target_display}. {final_player_coords_str}. Navigator reset to idle.")
            #     navigator.reset_navigation()

            # Render screen
            current_raw_frame = env.render()
            processed_frame_rgb = process_frame_for_pygame(current_raw_frame)
            update_screen(screen, processed_frame_rgb, screen_width, screen_height)

            # Print coordinates and map info only when changed
            player_gx, player_gy = navigator._get_player_global_coords() or (None, None)
            _, _, current_map_id = env.get_game_coords()  # local x, y, map_id
            current_map_name = env.get_map_name_by_id(current_map_id)
            # Track last printed global+map position
            pos3 = (player_gx, player_gy, current_map_id)
            if getattr(navigator, '_last_printed_position', None) != pos3:
                print(f"Global Coords: ({player_gx}, {player_gy}), Map ID: {current_map_id}, Map Name: {current_map_name}")
                navigator._last_printed_position = pos3
            # Update UI with current location
            status_queue.put(('__location__', (player_gx, player_gy, current_map_id, current_map_name)))

            # Prepare for 'previous_map_id_was' triggers by capturing the current map before evaluation
            current_map_id = env.get_game_coords()[2]

            # Catch-up logic: scan all triggers across all quests and mark them complete if detected
            for q in QUESTS:
                qid_q = q['quest_id']
                for idx, trg in enumerate(q.get('event_triggers', [])):
                    tid_q = f"{qid_q}_{idx}"
                    if not trigger_completed.get(tid_q, False) and evaluator.check_trigger(trg):
                        trigger_completed[tid_q] = True
                        status_queue.put((tid_q, True))
            # Update quest statuses based on completed triggers
            for q in QUESTS:
                qid_q = q['quest_id']
                tids_list = [f"{qid_q}_{i}" for i in range(len(q.get('event_triggers', [])))]
                if tids_list and all(trigger_completed.get(t, False) for t in tids_list):
                    if not quest_completed.get(qid_q, False):
                        quest_completed[qid_q] = True
                        status_queue.put((qid_q, True))
                    q['completed'] = True
            # Sync in-memory QUESTS definitions with updated statuses
            # Update trigger completion flags in QUESTS list
            for q in QUESTS:
                qid_q = q['quest_id']
                for idx, trg in enumerate(q.get('event_triggers', [])):
                    tid_q = f"{qid_q}_{idx}"
                    if trigger_completed.get(tid_q, False):
                        trg['completed'] = True
            # Update quest completion flags in QUESTS list
            for q in QUESTS:
                qid_q = q['quest_id']
                tids_list = [f"{qid_q}_{i}" for i in range(len(q.get('event_triggers', [])))]
                if tids_list and all(trigger_completed.get(t, False) for t in tids_list):
                    q['completed'] = True

            # After processing triggers and quests, update evaluator.prev_map_id for the next iteration
            evaluator.prev_map_id = current_map_id

            # Persist updated statuses to per-run status files
            try:
                with open(run_dir / "trigger_status.json", "w") as f:
                    json.dump(trigger_completed, f, indent=4)
                with open(run_dir / "quest_status.json", "w") as f:
                    json.dump(quest_completed, f, indent=4)
            except Exception as e:
                print(f"Error writing status files to {run_dir}: {e}")
            
            clock.tick(30) # FPS

            # Save recorded actions to JSON in the run directory
            if env.current_run_dir:
                actions_file_path = env.current_run_dir / f"{env.current_run_dir.name}_actions.json"
                try:
                    with open(actions_file_path, "w") as f_actions:
                        # Save the entire list of actions (integers or dicts)
                        json.dump(recorded_playthrough, f_actions, indent=4)
                    # print(f"Saved {len(recorded_playthrough)} actions to {actions_file_path}")
                except Exception as e:
                    print(f"Error saving actions to {actions_file_path}: {e}")

                # Save path trace data from environment if it exists and there's a run directory
                if hasattr(env, 'path_trace_data') and env.path_trace_data:
                    coords_file_path = env.current_run_dir / f"{env.current_run_dir.name}_coords.json"
                    try:
                        with open(coords_file_path, "w") as f_coords:
                            json.dump(env.path_trace_data, f_coords, indent=4)
                        # print(f"Saved path trace data to {coords_file_path}")
                    except Exception as e:
                        print(f"Error saving path trace data to {coords_file_path}: {e}")
            else:
                # print("Warning: env.current_run_dir is not set. Actions and path trace not saved to a run-specific directory.")
                pass # Not in a recording session, or current_run_dir not managed by env

    except KeyboardInterrupt:
        print("\nPlay session interrupted by user.")
    finally:
        # Capture trace data and final state before closing (env.close clears them)
        coords_data = env.path_trace_data.copy()
        # Capture final emulator state
        import io
        end_buf = io.BytesIO()
        env.pyboy.save_state(end_buf)
        end_buf.seek(0)
        # Close environment and pygame
        env.close()
        pygame.quit()

        # Saving playthrough actions, coordinates, and end state
        if recorded_playthrough:
            # Determine filenames based on --name override
            if args.name:
                # Ensure actions filename ends with .json and derive base name
                if args.name.lower().endswith('.json'):
                    actions_filename = args.name
                    name_base = Path(actions_filename).stem
                else:
                    actions_filename = args.name + '.json'
                    name_base = args.name
                actions_file = run_dir / actions_filename
                coords_file = run_dir / f"{name_base}_coords.json"
                end_state_file = run_dir / f"{name_base}_end.state"
            else:
                # Default numeric naming
                actions_file = run_dir / f"quest_id_{name_base}.json"
                coords_file = run_dir / f"{name_base}_coords.json"
                end_state_file = run_dir / f"{name_base}_end.state"
            with open(actions_file, "w") as f:
                json.dump(recorded_playthrough, f, indent=4)
            print(f"Playthrough actions saved to {actions_file}")
            # Save coordinates trace
            with open(coords_file, "w") as f:
                json.dump(coords_data, f, indent=4)
            print(f"Coordinates saved to {coords_file}")
            # Save end state
            with open(end_state_file, "wb") as f:
                f.write(end_buf.read())
            print(f"End state saved to {end_state_file}")
        else:
            print("No actions recorded.")

if __name__ == "__main__":
    main()
