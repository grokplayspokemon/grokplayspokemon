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
from omegaconf import OmegaConf, DictConfig
import threading
import queue
from environment.environment_helpers.trigger_evaluator import TriggerEvaluator
from environment.environment_helpers.quest_progression import QuestProgressionEngine
from queue import SimpleQueue
from datetime import datetime
import os

from environment.wrappers.env_wrapper import EnvWrapper
from environment.wrappers.configured_env_wrapper import ConfiguredEnvWrapper
from environment.environment import VALID_ACTIONS, PATH_FOLLOW_ACTION
from pyboy.utils import WindowEvent
from environment.data.recorder_data.global_map import local_to_global
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.environment_helpers.saver import save_initial_state, save_loop_state, save_final_state
from environment.environment_helpers.warp_tracker import record_warp_step, backtrack_warp_sequence
from environment.environment_helpers.quest_manager import QuestManager
# from web.quest_server import status_queue, start_server
# from grok_integration import initialize_grok, get_grok_action, shutdown_grok # Removed Grok V1 integration

project_root_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root_path))

# Load quest definitions
QUESTS_FILE = Path(__file__).parent / "environment_helpers" / "required_completions.json"
with open(QUESTS_FILE, 'r') as f:
    QUESTS = json.load(f)

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
        primary_quest_path = Path(__file__).parent / "environment_helpers" / "quest_paths" / quest_dir_name / quest_file_name
        
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
                file_path = Path(__file__).parent / "environment_helpers" / "quest_paths" / quest_dir_name / quest_file_name
                
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
    # Replace top labels with bottom info_frame and separate labels
    info_frame = tk.Frame(root)
    info_frame.pack(side='bottom', fill='x')
    map_name_label = tk.Label(info_frame, text="Map: N/A")
    map_name_label.pack(side='left', padx=5)
    map_id_label = tk.Label(info_frame, text="Map ID: N/A")
    map_id_label.pack(side='left', padx=5)
    global_label = tk.Label(info_frame, text="Global: N/A")
    global_label.pack(side='left', padx=5)
    local_label = tk.Label(info_frame, text="Local: N/A")
    local_label.pack(side='left', padx=5)
    quest_label = tk.Label(info_frame, text="Current Quest: N/A")
    quest_label.pack(side='left', padx=5)

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
                    gx, gy, mid, mname = data
                    map_name_label.config(text=f"Map: {mname}")
                    map_id_label.config(text=f"Map ID: {mid}")
                    global_label.config(text=f"Global: ({gx}, {gy})")
                elif item_id == '__local_location__':
                    lx, ly = data
                    local_label.config(text=f"Local: ({lx}, {ly})")
                elif item_id == '__current_quest__':
                    print(f"play.py: poll(): data: Current quest: {data}")
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

    config_parts = [default_cli_config]
    if 'env_config' in yaml_config:
        config_parts.append(yaml_config.env_config)

    cli_overrides = {}
    for arg_name, arg_value in vars(args).items():
        # Only consider args that are explicitly set (not None) and are relevant to env_config
        if arg_value is not None and arg_name not in ["config_path", "required_completions_path", "max_total_steps", "interactive_mode", "run_dir", "grok_api_key"]: # Exclude non-env_config args
            mapped_key = arg_name
            if arg_name == "rom_path" and arg_value: # Map rom_path to gb_path for config
                mapped_key = "gb_path"
            elif arg_name == "initial_state_path" and arg_value: # Map initial_state_path to init_state
                mapped_key = "init_state"
            
            # Only add to overrides if it's a known key in default_cli_config or the mapped key is
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


    if isinstance(final_config.get("init_state"), Path): # Should be string by now
        final_config.init_state = str(final_config.init_state)
    
    # Ensure session_id is present
    if 'session_id' not in final_config or not final_config.session_id:
        final_config.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    # Ensure mapping_file path is resolved correctly if used
    if 'mapping_file' in final_config and final_config.mapping_file:
        # Example: resolve relative to project_root_path if it's a relative path
        # This depends on how mapping_file is intended to be specified
        pass


    print(f"Final merged config for EnvWrapper: {OmegaConf.to_yaml(final_config)}")
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
    # Add 'run_dir' argument for specifying where run-specific files (like quest_status.json) go
    parser.add_argument("--run_dir", type=str, default=None, help="Directory to store run-specific files. Defaults to runs/<session_id>.")
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
    parser.add_argument("--interactive_mode", type=bool, default=False) # Explicitly default to False
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
    
    # Define and create run_dir using the session_id from the final config
    # This ensures QuestManager and QuestProgressionEngine use the same, correct run_dir
    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        # Use a "runs" subdirectory in the project root, then the session ID
        run_dir = project_root_path / "runs" / config.session_id 
    
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run directory: {run_dir.resolve()}")


    # Initialize environment with the final merged config
    # base_env = EnvWrapper(config) # EnvWrapper should handle its own config # This line is redundant as ConfiguredEnvWrapper handles it.
    # If ConfiguredEnvWrapper is needed, ensure it's imported and used correctly:
    from environment.wrappers.configured_env_wrapper import ConfiguredEnvWrapper
    env = ConfiguredEnvWrapper(base_conf=config) # Pass the final merged config here

    navigator = InteractiveNavigator(env)
    
    # QuestManager now needs run_dir
    quest_manager = QuestManager(env=env, navigator=navigator, run_dir=run_dir, required_completions_path=args.required_completions_path)


    if not env.headless:
        pygame.init()
        screen_width, screen_height = 320, 288
        screen = pygame.display.set_mode((screen_width, screen_height))
        pygame.display.set_caption("Pokemon Red")

    print("Starting game loop...")
    running = True
    total_steps = 0
    total_reward = 0
    start_time = time.time()
    
    # Initialize these before the loop if they are used by QuestProgressionEngine or QuestManager init logic
    last_map_id = env.get_game_coords()[2]
    visited_warps = set()
    last_player_pos = env.get_game_coords()[:2]

    status_queue = SimpleQueue()
    all_quest_ids = [int(q["quest_id"]) for q in QUESTS] # QUESTS is loaded globally

    # Correctly instantiate QuestProgressionEngine
    quest_progression_engine = QuestProgressionEngine(
        env=env,
        navigator=navigator,
        quest_manager=quest_manager,
        quests=QUESTS,          # quests_data (List[Dict])
        quest_ids_all=all_quest_ids, # quest_ids_all (List[int])
        status_queue=status_queue,   # status_queue (SimpleQueue)
        run_dir=run_dir             # run_dir (Path) - now correctly defined
    )
    
    # UI thread for quest status (if needed, ensure tkinter is imported and start_quest_ui is compatible)
    # ui_thread_started = False
    # if not env.headless and False: # Disabled for now, can be re-enabled if UI is fixed/needed
    #     import tkinter as tk 
    #     from tkinter import ttk
    #     ui_thread = threading.Thread(target=start_quest_ui, daemon=True) # Pass status_queue if needed
    #     ui_thread.start()
    #     ui_thread_started = True
    
    if config.get("save_state", True): # Check config for save_state
        states_dir = Path(config.state_dir) # Get state_dir from config
        if not states_dir.is_absolute():
            states_dir = project_root_path / states_dir
        save_initial_state(env, states_dir)


    while running and total_steps < args.max_total_steps:
        current_action = None # Renamed to avoid conflict with 'action' variable if any
        if not env.headless:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if args.interactive_mode and event.type == pygame.KEYDOWN:
                    key_action = navigator.handle_pygame_event(event)
                    if key_action is not None:
                        current_action = key_action # Store manual action
                        # No step here, let the main step logic handle it
                    # else: current_action remains None
        
        # AI Action or Manual Action
        if current_action is None and not args.disable_ai_actions: # AI takes over if no manual input and AI enabled
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
                    if navigator.is_navigation_active():
                        current_action = navigator.get_next_action()
                    else:
                        # print("AI: No specific quest action, no navigation. Defaulting to random.")
                        current_action = np.random.choice(VALID_ACTIONS) # Example random action
            else: # No quest active
                if navigator.is_navigation_active():
                    current_action = navigator.get_next_action()
                else:
                    # print("AI: No active quest, no navigation. Defaulting to random.")
                    current_action = np.random.choice(VALID_ACTIONS) # Example random action

        elif current_action is None and args.disable_ai_actions and not args.interactive_mode: # Replay/tick mode
            # Read current joypad state or assume no-op
            # current_action = env.read_m(0xFFF0) 
            # if current_action == 0xFF: current_action = 0 # No button pressed = NO_OP
            current_action = 0 # Default to NO_OP for non-interactive, no-AI mode
        
        if current_action is None: # Still no action (e.g. interactive mode with no key press)
            if not env.headless: # Only tick if we need to render for interactive
                env.pyboy.tick()
                frame_rgb = env.render()
                update_screen(screen, frame_rgb, screen_width, screen_height)
                # pygame.display.flip() # update_screen handles flip
                time.sleep(0.01) # Minimal delay
            else: # Headless, no action -> could be an issue or intentional pause
                pass # Or env.pyboy.tick() if even headless ticks are desired without action
            continue


        obs, reward, terminated, truncated, info = env.step(current_action)
        total_reward += reward
        total_steps += 1

        # Warp Tracking Logic (simplified, ensure env methods are correct)
        new_map_id = env.get_game_coords()[2]
        # new_player_pos = env.get_player_coordinates() # Example
        if new_map_id != last_map_id: # Basic warp detection
            # record_warp_step(env, last_map_id, new_map_id, total_steps) # If function exists
            last_map_id = new_map_id
        # last_player_pos = new_player_pos

        if quest_progression_engine:
            trigger_evaluator = TriggerEvaluator(env) # Corrected instantiation
            quest_progression_engine.step(trigger_evaluator) # Pass the correct evaluator

        # quest_manager.update_progress() # This was a stub, get_current_quest now handles status updates

        if total_steps % config.get("log_frequency", 1000) == 0:
            elapsed_time = time.time() - start_time
            steps_per_sec = total_steps / elapsed_time if elapsed_time > 0 else 0
            current_quest_display = quest_manager.current_quest_id if quest_manager.current_quest_id is not None else "None"
            print(f"Step: {total_steps}, Reward: {total_reward:.2f}, Steps/sec: {steps_per_sec:.2f}, Current Quest: {current_quest_display}")
            if config.get("save_state", True):
                loop_states_dir = Path(config.state_dir).parent / "loop_states" # Example: relative to state_dir parent
                if not loop_states_dir.is_absolute():
                    loop_states_dir = project_root_path / loop_states_dir
                save_loop_state(env, loop_states_dir, total_steps)
        
        if not env.headless:
            frame_rgb = env.render()
            update_screen(screen, frame_rgb, screen_width, screen_height)
            # pygame.display.flip() # update_screen handles this

        if terminated or truncated:
            print("Game terminated or truncated.")
            running = False

    print(f"Finished after {total_steps} steps.")
    if config.get("save_state", True) and config.get("save_final_state", True):
        final_states_dir = Path(config.state_dir).parent / "final_states"
        if not final_states_dir.is_absolute():
             final_states_dir = project_root_path / final_states_dir
        save_final_state(env, final_states_dir)
    
    env.close()
    if not env.headless:
        pygame.quit()

if __name__ == "__main__":
    main()
    # print("play.py execution complete.")