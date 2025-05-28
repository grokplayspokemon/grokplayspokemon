# play.py - CLEANED VERSION (showing only key functions)
import argparse
import numpy as np
import json
import pygame
import time
import sys
import math
from pathlib import Path
from typing import Optional
from omegaconf import OmegaConf
import threading
import queue
import tkinter as tk
import tkinter.ttk as ttk
from trigger_evaluator import TriggerEvaluator
from datetime import datetime

from environment import RedGymEnv, VALID_ACTIONS, PATH_FOLLOW_ACTION
from pyboy.utils import WindowEvent
from global_map import local_to_global
from navigator import InteractiveNavigator
from saver import save_initial_state, save_loop_state, save_final_state
from warp_tracker import record_warp_step, backtrack_warp_sequence
from quest_manager import QuestManager

# Add project root for global_map import if play.py is in a subdirectory
project_root_path = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root_path))

# Load quest definitions
QUESTS_FILE = Path(__file__).parent / "required_completions.json"
with open(QUESTS_FILE, 'r') as f:
    QUESTS = json.load(f)

# Shared queue for UI updates
status_queue = queue.Queue()

def diagnose_environment_coordinate_loading(env, navigator):
    """Diagnostic protocol for environment coordinate loading accuracy"""
    
    print("\n" + "="*60)
    print("ENVIRONMENT COORDINATE LOADING DIAGNOSTIC")
    print("="*60)
    
    quest_ids_to_test = [12, 13, 14]
    
    for quest_id in quest_ids_to_test:
        print(f"\n--- QUEST {quest_id:03d} ENVIRONMENT LOADING TEST ---")
        
        # STEP 1: Direct file content reading
        quest_dir_name = f"{quest_id:03d}"
        quest_file_name = f"{quest_dir_name}_coords.json"
        file_path = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / quest_dir_name / quest_file_name
        
        if file_path.exists():
            with open(file_path, 'r') as f:
                file_content = json.load(f)
            
            # Flatten file coordinates for comparison
            file_coordinates = []
            for map_coords in file_content.values():
                file_coordinates.extend([tuple(coord) for coord in map_coords])
            
            print(f"File content: {len(file_coordinates)} coordinates")
            print(f"  Maps: {list(file_content.keys())}")
            print(f"  First coordinate: {file_coordinates[0] if file_coordinates else 'None'}")
            print(f"  Last coordinate: {file_coordinates[-1] if file_coordinates else 'None'}")
            
            # STEP 2: Environment loading test
            print(f"\nTesting environment loading...")
            
            # Store current environment state
            original_path = getattr(env, 'combined_path', []).copy()
            original_quest_id = getattr(env, 'current_loaded_quest_id', None)
            original_index = getattr(env, 'current_path_target_index', 0)
            
            # Test environment coordinate loading
            env_load_success = env.load_coordinate_path(quest_id)
            
            if env_load_success and hasattr(env, 'combined_path') and env.combined_path:
                env_coordinates = [tuple(coord) for coord in env.combined_path]
                
                print(f"Environment loaded: {len(env_coordinates)} coordinates")
                print(f"  First coordinate: {env_coordinates[0] if env_coordinates else 'None'}")
                print(f"  Last coordinate: {env_coordinates[-1] if env_coordinates else 'None'}")
                
                # STEP 3: Content comparison
                if file_coordinates == env_coordinates:
                    print(f"  ✓ MATCH: Environment loaded coordinates match file content exactly")
                else:
                    print(f"  ⚠ MISMATCH: Environment coordinates differ from file content")
                    print(f"    File coords: {len(file_coordinates)}")
                    print(f"    Env coords: {len(env_coordinates)}")
                    
                    # Show first few differences
                    for i, (file_coord, env_coord) in enumerate(zip(file_coordinates, env_coordinates)):
                        if file_coord != env_coord:
                            print(f"    Diff at index {i}: File={file_coord}, Env={env_coord}")
                            if i >= 3:  # Limit output
                                break
            else:
                print(f"  ✗ Environment loading failed")
            
            # STEP 4: Navigator loading test
            print(f"\nTesting navigator loading...")
            
            # Store current navigator state
            nav_original_coords = navigator.sequential_coordinates.copy() if navigator.sequential_coordinates else []
            nav_original_index = navigator.current_coordinate_index
            nav_original_quest_id = getattr(navigator, 'active_quest_id', None)
            
            # Test navigator coordinate loading
            nav_load_success = navigator.load_coordinate_path(quest_id)
            
            if nav_load_success and navigator.sequential_coordinates:
                nav_coordinates = [tuple(coord) for coord in navigator.sequential_coordinates]
                
                print(f"Navigator loaded: {len(nav_coordinates)} coordinates")
                print(f"  First coordinate: {nav_coordinates[0] if nav_coordinates else 'None'}")
                print(f"  Last coordinate: {nav_coordinates[-1] if nav_coordinates else 'None'}")
                
                # STEP 5: Navigator-Environment comparison
                if hasattr(env, 'combined_path') and env.combined_path:
                    if nav_coordinates == env_coordinates:
                        print(f"  ✓ Navigator-Environment coordination: SYNCHRONIZED")
                    else:
                        print(f"  ⚠ Navigator-Environment coordination: DESYNCHRONIZED")
                
                # STEP 6: Navigator-File comparison
                if nav_coordinates == file_coordinates:
                    print(f"  ✓ Navigator-File accuracy: ACCURATE")
                else:
                    print(f"  ⚠ Navigator-File accuracy: INACCURATE")
            else:
                print(f"  ✗ Navigator loading failed")
            
            # Restore states
            env.combined_path = original_path
            env.current_loaded_quest_id = original_quest_id
            env.current_path_target_index = original_index
            
            navigator.sequential_coordinates = nav_original_coords
            navigator.current_coordinate_index = nav_original_index
            navigator.active_quest_id = nav_original_quest_id
            
        else:
            print(f"✗ Coordinate file not found: {file_path}")
    
    print("\n" + "="*60)
    print("DIAGNOSTIC COMPLETE")
    print("="*60)

def debug_coordinate_system(env, navigator):
    """Comprehensive debug of the coordinate system with synchronized path checking"""
    print("\n" + "="*60)
    print("COORDINATE SYSTEM DEBUG")
    print("="*60)
    
    # Get current player position
    try:
        player_x, player_y, map_n = env.get_game_coords()
        current_gy, current_gx = local_to_global(player_y, player_x, map_n)
        print(f"Current Player Position: ({current_gy}, {current_gx}) on map {map_n}")
    except Exception as e:
        print(f"ERROR getting player position: {e}")
        return
    
    # Check navigator coordinates
    print(f"\n--- NAVIGATOR STATUS ---")
    print(f"Navigator coordinates loaded: {len(navigator.sequential_coordinates)}")
    if navigator.sequential_coordinates:
        print(f"Navigator first coordinate: {navigator.sequential_coordinates[0]}")
        print(f"Navigator current index: {navigator.current_coordinate_index}")
        if navigator.current_coordinate_index < len(navigator.sequential_coordinates):
            target = navigator.sequential_coordinates[navigator.current_coordinate_index]
            print(f"Navigator current target: {target}")
            distance = abs(target[0] - current_gy) + abs(target[1] - current_gx)
            print(f"Distance to navigator target: {distance}")
    else:
        print("Navigator: NO COORDINATES LOADED!")
    
    # Check environment coordinates
    print(f"\n--- ENVIRONMENT STATUS ---")
    if hasattr(env, 'combined_path') and env.combined_path:
        print(f"Environment path length: {len(env.combined_path)}")
        print(f"Environment first coordinate: {env.combined_path[0]}")
        print(f"Environment current index: {env.current_path_target_index}")
        if env.current_path_target_index < len(env.combined_path):
            target = env.combined_path[env.current_path_target_index]
            print(f"Environment current target: {target}")
            distance = abs(target[0] - current_gy) + abs(target[1] - current_gx)
            print(f"Distance to environment target: {distance}")
    else:
        print("Environment: NO PATH LOADED!")
    
    # CORRECTED: Check coordinate file accessibility using navigator's actual search paths
    print(f"\n--- COORDINATE FILE VALIDATION ---")
    quest_ids_to_check = [12, 13, 14]
    
    for quest_id in quest_ids_to_check:
        quest_dir_name = f"{quest_id:03d}"
        quest_file_name = f"{quest_dir_name}_coords.json"
        
        # Use same path logic as navigator
        primary_quest_path = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / quest_dir_name / quest_file_name
        
        file_status = "EXISTS" if primary_quest_path.exists() else "MISSING"
        print(f"Quest file {quest_file_name}: {file_status}")
        
        # Additional content validation if file exists
        if primary_quest_path.exists():
            try:
                with open(primary_quest_path, 'r') as f:
                    content = json.load(f)
                map_keys = list(content.keys())
                total_coords = sum(len(coords) for coords in content.values())
                print(f"  → Content: {len(map_keys)} maps, {total_coords} total coordinates")
                print(f"  → Maps: {map_keys}")
            except Exception as e:
                print(f"  → Content Error: {e}")

def verify_quest_system_integrity(env, navigator):
    """Comprehensive quest system validation protocol with content verification"""
    
    # Phase 1: File System Verification with correct paths
    quest_files = ['012_coords.json', '013_coords.json', '014_coords.json']
    print("=== COORDINATE FILE ACCESSIBILITY VERIFICATION ===")
    
    for quest_id, file_name in zip([12, 13, 14], quest_files):
        quest_dir_name = f"{quest_id:03d}"
        file_path = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / quest_dir_name / file_name
        
        status = "EXISTS" if file_path.exists() else "MISSING"
        print(f"Quest file {file_name}: {status}")
        
        if file_path.exists():
            # Validate content structure
            try:
                with open(file_path, 'r') as f:
                    content = json.load(f)
                print(f"  → Structure: {list(content.keys())} maps")
                for map_id, coords in content.items():
                    print(f"    Map {map_id}: {len(coords)} coordinates")
                    if coords:
                        print(f"      First: {coords[0]}, Last: {coords[-1]}")
            except Exception as e:
                print(f"  → Content validation error: {e}")
    
    # Phase 2: Quest Load Sequence Testing with content verification
    print(f"\n=== QUEST LOADING CONTENT VERIFICATION ===")
    for quest_id in [12, 13, 14]:
        print(f"\n--- TESTING QUEST {quest_id:03d} LOAD ---")
        
        # Store original navigator state
        original_coords = navigator.sequential_coordinates.copy() if navigator.sequential_coordinates else []
        original_index = navigator.current_coordinate_index
        original_quest_id = getattr(navigator, 'active_quest_id', None)
        
        # Test quest loading
        success = navigator.load_coordinate_path(quest_id)
        if success:
            print(f"✓ Quest {quest_id:03d}: {len(navigator.sequential_coordinates)} coordinates loaded")
            if navigator.sequential_coordinates:
                print(f"  First: {navigator.sequential_coordinates[0]}")
                print(f"  Last: {navigator.sequential_coordinates[-1]}")
                
                # Content uniqueness verification
                coord_set = set(navigator.sequential_coordinates)
                print(f"  Unique coordinates: {len(coord_set)}/{len(navigator.sequential_coordinates)}")
                
                # Validate against expected content
                quest_dir_name = f"{quest_id:03d}"
                quest_file_name = f"{quest_dir_name}_coords.json"
                file_path = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / quest_dir_name / quest_file_name
                
                if file_path.exists():
                    with open(file_path, 'r') as f:
                        expected_content = json.load(f)
                    
                    # Flatten expected coordinates for comparison
                    expected_coords = []
                    for map_coords in expected_content.values():
                        expected_coords.extend([tuple(coord) for coord in map_coords])
                    
                    loaded_coords = [tuple(coord) for coord in navigator.sequential_coordinates]
                    
                    if loaded_coords == expected_coords:
                        print(f"  ✓ Content matches file exactly")
                    else:
                        print(f"  ⚠ Content mismatch detected")
                        print(f"    Expected: {len(expected_coords)} coords")
                        print(f"    Loaded: {len(loaded_coords)} coords")
        else:
            print(f"✗ Quest {quest_id:03d}: LOAD FAILED")
        
        # Restore original navigator state
        navigator.sequential_coordinates = original_coords
        navigator.current_coordinate_index = original_index
        navigator.active_quest_id = original_quest_id
    
    # Phase 3: Position Alignment Verification  
    current_pos = navigator._get_player_global_coords()
    print(f"\nCurrent player position: {current_pos}")
    
    return True

def determine_starting_quest(player_pos, map_id, completed_quests, quest_ids_all):
    """Determine the most appropriate starting quest based on player position and game state"""
    
    if map_id == 40:  # Oak's Lab
        # If standing on Quest 12 return path in Lab, prioritize it
        if player_pos and player_pos in [(356, 110), (355, 110), (354, 110), (353, 110), (352, 110), (351, 110), (350, 110), (349, 110), (348, 110)]:
            if not completed_quests.get(12, False):
                return 12
            elif not completed_quests.get(13, False):
                return 13
        # Default Oak's Lab quests: try Quest 11 first, then 12 and 13
        for quest_id in [11, 12, 13]:
            if not completed_quests.get(quest_id, False):
                return quest_id
    
    elif map_id == 0:  # Pallet Town
        # Check if player is on quest 13 or 14 coordinates
        if not completed_quests.get(13, False):
            return 13
        elif not completed_quests.get(14, False):
            return 14
    
    # Fallback: First uncompleted quest
    for quest_id in quest_ids_all:
        if not completed_quests.get(quest_id, False):
            return quest_id
    
    return quest_ids_all[0]  # Ultimate fallback

def start_quest_ui():
    """Start the quest progress UI in a separate thread"""
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
    # Label for displaying current quest ID
    quest_label = tk.Label(root, text="Current Quest: N/A")
    quest_label.pack(side='top', fill='x')

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

    # Populate rows with quests, subquests, and their criteria
    for q in QUESTS:
        qid = q['quest_id']
        # Insert quest as parent with description
        parent_text = f"Quest {qid}: {q.get('begin_quest_text','')}"
        tree.insert('', 'end', iid=qid, text=parent_text, values=('Pending',))
        # Insert subquests (step descriptions) as children
        for sidx, step in enumerate(q.get('subquest_list', [])):
            sub_id = f"{qid}_step_{sidx}"
            sub_text = f"Step {sidx+1}: {step}"
            tree.insert(qid, 'end', iid=sub_id, text=sub_text, values=('Pending',))
        # Insert criteria as children
        for idx, trg in enumerate(q.get('event_triggers', [])):
            trigger_id = f"{qid}_{idx}"
            crit_text = describe_trigger(trg)
            tree.insert(qid, 'end', iid=trigger_id, text=crit_text, values=('Pending',))
    # Auto-expand all quests to show triggers and steps
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
            print(f"play.py: snap_to_current(): quest_id={qid} (checking)")
            if tree.set(qid, 'status') == 'Pending':
                tree.see(qid)
                print(f"play.py: snap_to_current(): quest_id={qid} (snapped)")
                status_queue.put(('__current_quest__', qid))
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
                elif item_id == '__current_quest__':
                    print(f"play.py: poll(): data: Current quest: {data}")
                    # Always display a zero-padded quest ID or N/A if None
                    quest_str = str(data).zfill(3) if data is not None else 'N/A'
                    quest_label.config(text=f"Current Quest: {quest_str}")
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
    pygame.K_5: PATH_FOLLOW_ACTION,
}

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
    # Track previous run directory for status loading
    prev_dir = None
    # Determine naming base: use provided --name or reuse latest numeric run
    if args.name:
        name_base = Path(args.name).stem
    else:
        existing_dirs = [d for d in recordings_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        if existing_dirs:
            max_id = max(int(d.name) for d in existing_dirs)
            # Increment to create a new run ID
            next_id = max_id + 1
            name_base = f"{next_id:03d}"
            # Remember the previous run directory for status loading
            prev_dir = recordings_dir / f"{max_id:03d}"
            # If an end state exists from the last run, load it from the previous ID
            prev_end = recordings_dir / f"{max_id:03d}" / f"{max_id:03d}_end.state"
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
    # Make navigator available to QuestManager for current quest tracking
    env.navigator = navigator
    # Initialize quest manager to enforce quest-specific rules
    quest_manager = QuestManager(env)

    obs, info = env.reset() # Initial reset
    # Use our play.py run_dir for path trace recording AFTER reset has cleared env.current_run_dir
    env.current_run_dir = run_dir

    # Save initial state to run directory
    save_initial_state(env, run_dir, name_base)
    # Log initial coordinate in path trace
    env.update_path_trace()

    recorded_playthrough = []
    # Track quest completion by integer quest IDs
    quest_completed = {int(q['quest_id']): False for q in QUESTS}
    trigger_completed = {}
    for q in QUESTS:
        qid = q['quest_id']
        for idx, _ in enumerate(q.get('event_triggers', [])):
            tid = f"{qid}_{idx}"
            trigger_completed[tid] = False
    # Determine status directory: previous run dir if exists, else current run dir
    status_dir = prev_dir if prev_dir is not None else run_dir
    # Load persisted quest statuses
    qstatus_file = status_dir / "quest_status.json"
    if qstatus_file.is_file():
        try:
            loaded_q = json.load(qstatus_file.open())
            for qid_str, val in loaded_q.items():
                qid_int = int(qid_str)
                if qid_int in quest_completed:
                    quest_completed[qid_int] = bool(val)
        except Exception:
            pass
    # Load persisted trigger statuses
    tstatus_file = status_dir / "trigger_status.json"
    if tstatus_file.is_file():
        try:
            loaded_t = json.load(tstatus_file.open())
            for tid, val in loaded_t.items():
                if tid in trigger_completed:
                    trigger_completed[tid] = bool(val)
        except Exception:
            pass
    # Reflect loaded statuses in QUESTS definitions
    for q in QUESTS:
        qid_str = q['quest_id']
        qid_int = int(qid_str)
        q['completed'] = quest_completed.get(qid_int, False)
        for idx, trg in enumerate(q.get('event_triggers', [])):
            tid = f"{qid_str}_{idx}"
            trg['completed'] = trigger_completed.get(tid, False)

    # ------------------------------------------------------------------
    # CORRECTED: Determine and initialize the current quest based on completion and map availability
    # Capture current map for initial quest selection
    current_map_id_for_init = env.get_game_coords()[2]
    current_player_pos = None
    try:
        player_x, player_y, _ = env.get_game_coords()
        current_player_pos = local_to_global(player_y, player_x, current_map_id_for_init)
    except Exception:
        current_player_pos = None

    # Dynamically determine all quest IDs from definitions
    quest_ids_all = sorted(int(q['quest_id']) for q in QUESTS)

    # CORRECTED: Intelligent quest selection with position analysis
    recommended_quest = determine_starting_quest(current_player_pos, current_map_id_for_init, quest_completed, quest_ids_all)
    # Persist quest across reloads: if resuming from a previous run, resume next pending quest
    if prev_dir is not None:
        for qid in quest_ids_all:
            if not quest_completed.get(qid, False):
                recommended_quest = qid
                print(f"Resuming quest: {recommended_quest:03d} from previous session")
                break
    start_checking_from_idx = quest_ids_all.index(recommended_quest) if recommended_quest in quest_ids_all else 0

    print("Attempting to find and load initial quest path...")
    print(f"Player position: {current_player_pos} on map {current_map_id_for_init}")
    print(f"Recommended starting quest: {recommended_quest:03d}")
    print(f"Starting quest search from Quest {quest_ids_all[start_checking_from_idx]:03d}")

    # ENHANCED: Primary quest loading sequence with comprehensive validation and content verification
    # Track which quest we actually load for UI tracking
    initial_loaded_quest_id = None
    for i in range(start_checking_from_idx, len(quest_ids_all)):
        q_id_int = quest_ids_all[i]
        quest_detail = next((q for q in QUESTS if q['quest_id'] == q_id_int), None)

        if quest_detail and not quest_completed.get(q_id_int, False):
            print(f"Attempting to load coordinates for Quest {q_id_int:03d}...")
            
            # VALIDATION: Pre-check coordinate file existence with correct path structure
            quest_dir_name = f"{q_id_int:03d}"
            quest_file_name = f"{quest_dir_name}_coords.json"
            coord_file_path = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / quest_dir_name / quest_file_name
            
            if not coord_file_path.exists():
                print(f"WARNING: Coordinate file {quest_file_name} not found at {coord_file_path}")
                continue
                
            # CONTENT VALIDATION: Verify coordinate file structure before loading
            try:
                with open(coord_file_path, 'r') as f:
                    coord_content = json.load(f)
                
                total_coords = sum(len(coords) for coords in coord_content.values())
                map_keys = list(coord_content.keys())
                print(f"  → Coordinate file structure: {len(map_keys)} maps, {total_coords} coordinates")
                
                if total_coords == 0:
                    print(f"  → WARNING: No coordinates found in file")
                    continue
                    
            except Exception as e:
                print(f"  → ERROR: Failed to validate coordinate file content: {e}")
                continue
                
            # Attempt coordinate loading with validated file
            if navigator.load_coordinate_path(q_id_int):
                # Remember this as the loaded quest for correct ID tracking
                initial_loaded_quest_id = q_id_int
                print(f"Successfully loaded initial path for Quest {q_id_int:03d} on map {current_map_id_for_init}.")
                
                # CONTENT VERIFICATION: Ensure loaded coordinates match file content
                loaded_coord_count = len(navigator.sequential_coordinates)
                if loaded_coord_count == total_coords:
                    print(f"  ✓ Coordinate count verification passed: {loaded_coord_count} coordinates")
                else:
                    print(f"  ⚠ Coordinate count mismatch: loaded {loaded_coord_count}, expected {total_coords}")
                
                break
            else:
                print(f"Could not load or process coordinate file for Quest {q_id_int:03d} on map {current_map_id_for_init}. Trying next quest.")

    # Ensure we always have a quest ID for UI
    if initial_loaded_quest_id is None:
        initial_loaded_quest_id = recommended_quest
    # Sync to environment, navigator, and QuestManager
    env.current_loaded_quest_id = initial_loaded_quest_id
    navigator.active_quest_id = initial_loaded_quest_id
    quest_manager.current_quest_id = initial_loaded_quest_id
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
        status_queue.put((qid, quest_completed.get(int(qid), False)))
    # Launch the Quest Progress HUD in its own thread
    threading.Thread(target=start_quest_ui, daemon=True).start()
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
    print("Press 7 to backtrack warp sequence.")

    running = True
    try:
        while running:
            manual_action_to_take = -1 # Action from player keyboard input
            current_time_sec = pygame.time.get_ticks() / 1000.0
            keys_pressed_this_frame = False

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

                    elif event.key == pygame.K_7:
                        backtrack_warp_sequence(env, navigator)
                        

                    elif event.key == pygame.K_d:  # NEW: Debug coordinate system
                        print("\n" + "="*60)
                        print("DEBUG KEY PRESSED - ANALYZING COORDINATE SYSTEM")
                        print("="*60)
                        
                        # Run comprehensive debug
                        debug_coordinate_system(env, navigator)
                        verify_quest_system_integrity(env, navigator)
                        diagnose_environment_coordinate_loading(env, navigator)
                        
                        # Check if coordinates need to be loaded
                        if len(navigator.sequential_coordinates) == 0:
                            print("\nNavigator has no coordinates - attempting to load...")
                            navigator.load_coordinate_path(12)
                        
                        if not hasattr(env, 'combined_path') or not env.combined_path:
                            print("\nEnvironment has no path - attempting to load quest 12...")
                            env.load_coordinate_path(quest_id=12)
                        
                        # Final status check
                        print("\n--- FINAL STATUS AFTER DEBUG ---")
                        debug_coordinate_system(env, navigator)

                    # On '4' key: Snap navigator and environment to the nearest coordinate in the current quest path
                    elif event.key == pygame.K_4:  # Snap to nearest recorded path coordinate
                        # Pause snapping if any dialog is active
                        raw_dialog = env.read_dialog() or ''
                        if raw_dialog.strip():
                            print("play.py: main(): '4' key: Navigation paused: dialog active, cannot snap to path.")
                        else:
                            print("play.py: main(): '4' key: Snapping to nearest coordinate on recorded path.")
                            # Ensure current quest is loaded
                            if not navigator.sequential_coordinates:
                                qid = quest_ids_all[start_checking_from_idx]
                                navigator.load_coordinate_path(int(qid))
                            # Snap navigator and environment
                            if navigator.snap_to_nearest_coordinate():
                                if hasattr(env, 'snap_to_nearest_path_coordinate'):
                                    env.snap_to_nearest_path_coordinate()
                                print(navigator.get_current_status())
                            else:
                                print("Navigator: Failed to snap to nearest coordinate.")

                    # On '5' key: Move to next coordinate (quest path)
                    elif event.key == pygame.K_5:  # Move to next coordinate (quest path)
                        # Pause movement if any dialog is active
                        raw_dialog = env.read_dialog() or ''
                        if raw_dialog.strip():
                            print("play.py: main(): '5' key:Navigation paused: dialog active, cannot move to next coordinate.")
                        else:
                            print("play.py: main(): '5' key: Moving one step along current-map segment")
                            # Apply quest-specific overrides (e.g., force A press for quest 015)
                            desired = quest_manager.filter_action(PATH_FOLLOW_ACTION)
                            if desired == PATH_FOLLOW_ACTION:
                                # Attempt to move to next coordinate; do not exit on failure
                                moved = navigator.move_to_next_coordinate()
                                if moved:
                                    print(navigator.get_current_status())
                                else:
                                    print("Navigator: No next coordinate or move failed (path complete).")
                            else:
                                # Override with quest-specific emulator action (e.g., A press)
                                current_obs, current_reward, current_terminated, current_truncated, current_info = env.step(desired)
                                # Record the override action
                                recorded_playthrough.append(desired)
                                # Update observation and info for this frame
                                obs, reward, terminated, truncated, info = current_obs, current_reward, current_terminated, current_truncated, current_info

                    # Check for manual game control keys ONLY if navigation is not actively overriding
                    elif navigator.navigation_status in ["idle", "completed", "failed"]:
                        if current_time_sec - last_action_time > debounce_time: # Check debounce for manual keys
                            if event.key in ACTION_MAPPING_PYGAME_TO_INT:
                                manual_action_to_take = ACTION_MAPPING_PYGAME_TO_INT[event.key]
                                last_action_time = current_time_sec
                                keys_pressed_this_frame = True # Indicate a key was pressed for this type of input
                    
                    # K_s: Save current game state manually (with Ctrl)
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
                        # Skip fallback for the 5 key (path-follow) to avoid sending None to PyBoy
                        if key_code == pygame.K_5:
                            continue
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
                # Filter action through quest manager
                filtered_action = quest_manager.filter_action(manual_action_to_take)
                current_obs, current_reward, current_terminated, current_truncated, current_info = env.step(filtered_action)
                executed_action_this_frame = filtered_action
                action_taken_by_player_this_turn = True
            
            # Update global obs, info etc. based on what happened
            obs, reward, terminated, truncated, info = current_obs, current_reward, current_terminated, current_truncated, current_info

            # --- Record Action if one was taken ---
            if executed_action_this_frame is not None:
                recorded_playthrough.append(executed_action_this_frame)

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
                print(f"play.py: main(): player_coords=({player_gx},{player_gy}), map_id={current_map_id}, map_name={current_map_name}")
                navigator._last_printed_position = pos3
                # Update UI with current location
                status_queue.put(('__location__', (player_gx, player_gy, current_map_id, current_map_name)))
                # Also display current quest ID (reflect environment's current_loaded_quest_id)
                status_queue.put(('__current_quest__', getattr(env, 'current_loaded_quest_id', None)))

            # Prepare for 'previous_map_id_was' triggers by capturing the current map before evaluation
            current_map_id = env.get_game_coords()[2]

            # Catch-up logic: scan only triggers for the active quest
            current_qid = getattr(quest_manager, 'current_quest_id', None)
            if current_qid is not None:
                active_quest = next((qq for qq in QUESTS if int(qq['quest_id']) == current_qid), None)
                if active_quest:
                    for idx, trg in enumerate(active_quest.get('event_triggers', [])):
                        tid = f"{active_quest['quest_id']}_{idx}"
                        if not trigger_completed.get(tid, False) and evaluator.check_trigger(trg):
                            trigger_completed[tid] = True
                            status_queue.put((tid, True))
            # Update status for the active quest only
            if current_qid is not None and active_quest:
                tids = [f"{active_quest['quest_id']}_{i}" for i in range(len(active_quest.get('event_triggers', [])))]
                if tids and all(trigger_completed.get(t, False) for t in tids):
                    if not quest_completed.get(current_qid, False):
                        quest_completed[current_qid] = True
                        status_queue.put((active_quest['quest_id'], True))
                    active_quest['completed'] = True

            # Automatically complete any "Complete quest XXX." subquests for the active quest
            current_qid = getattr(quest_manager, 'current_quest_id', None)
            if current_qid is not None and active_quest:
                qid_str = active_quest['quest_id']
                for sidx, step_text in enumerate(active_quest.get('subquest_list', [])):
                    if step_text.startswith("Complete quest "):
                        parts = step_text.split()
                        if len(parts) >= 3:
                            prev_qid = parts[2].rstrip('.')
                            if quest_completed.get(int(prev_qid), False):
                                step_id = f"{qid_str}_step_{sidx}"
                                status_queue.put((step_id, True))

            # After processing triggers and quests, update evaluator.prev_map_id for the next iteration
            evaluator.prev_map_id = current_map_id

            # Persist updated statuses to per-run status files
            try:
                # Save statuses to the current run directory
                with open(run_dir / "trigger_status.json", "w") as f:
                    json.dump(trigger_completed, f, indent=4)
                with open(run_dir / "quest_status.json", "w") as f:
                    # Persist int keys as zero-padded strings
                    json.dump({str(qid).zfill(3): val for qid, val in quest_completed.items()}, f, indent=4)
            except Exception as e:
                print(f"Error writing status files to {run_dir}: {e}")
            
            # Advance to next uncompleted quest if current quest completed
            current_qid = getattr(quest_manager, 'current_quest_id', None)
            if current_qid is not None and quest_completed.get(current_qid, False):
                # Find next quest in order that is not completed
                next_qid = None
                for qid in quest_ids_all:
                    if not quest_completed.get(qid, False):
                        next_qid = qid
                        break
                if next_qid is not None and next_qid != current_qid:
                    quest_manager.current_quest_id = next_qid
                    navigator.active_quest_id = next_qid
                    env.current_loaded_quest_id = next_qid
                    # Update UI with new current quest
                    status_queue.put(('__current_quest__', next_qid))
            clock.tick(30) # FPS

            # Save recorded actions and path trace data during loop
            save_loop_state(env, recorded_playthrough)

    except KeyboardInterrupt:
        print("\nPlay session interrupted by user.")
    finally:
        # Capture trace data and final state before closing (env.close clears them)
        coords_data = env.path_trace_data.copy()
        # Close environment and pygame
        env.close()
        pygame.quit()

        # Always save playthrough actions, coordinates, and end state
        save_final_state(env, run_dir, recorded_playthrough, coords_data, args.name, name_base)

if __name__ == "__main__":
    main()