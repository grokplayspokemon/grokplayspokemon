"""
Quest UI Module - Modern redesign with improved layout and styling
"""
import tkinter as tk
from tkinter import ttk
import queue
from typing import List, Dict, Any
from environment.environment_helpers.quest_manager import describe_trigger
from ui.map_plotting_utils import draw_first_n_quest_paths
import os
from pathlib import Path
import json

# Import rendering functions
from ui.render_to_global import (
    load_map_data, prepare_map_data_for_conversion, local_to_global,
    initialize_map_canvas, load_resources, draw_map_optimized,
    draw_warp_minimap, update_screen_canvas, save_full_map_with_overlays
)

# Import speech bubble system
from ui.speech_bubble_system import get_speech_bubble_manager, initialize_speech_bubbles

def start_quest_ui(quests: List[Dict[str, Any]], status_queue):
    """Start the modernized quest progress UI"""
    # Initialize map data
    load_map_data()
    prepare_map_data_for_conversion()
    
    root = tk.Tk()
    root.title("Pokémon Quest Tracker")
    
    # Modern color scheme
    BG_COLOR = "#1e1e1e"
    PANEL_BG = "#2d2d30"
    ACCENT_COLOR = "#007ACC"
    TEXT_COLOR = "#e0e0e0"
    HIGHLIGHT_COLOR = "#3e3e42"
    SUCCESS_COLOR = "#4ec9b0"
    WARNING_COLOR = "#dcdcaa"
    ERROR_COLOR = "#f48771"
    
    # Configure root window
    root.configure(bg=BG_COLOR)
    root.geometry("1600x900")
    root.minsize(1400, 800)
    
    # Modern styling
    style = ttk.Style()
    style.theme_use('clam')
    
    # Configure ttk styles for dark theme
    style.configure("Dark.TFrame", background=PANEL_BG)
    style.configure("Dark.TLabelframe", background=PANEL_BG, foreground=TEXT_COLOR, bordercolor="#3e3e42", lightcolor="#3e3e42", darkcolor="#3e3e42")
    style.configure("Dark.TLabelframe.Label", background=PANEL_BG, foreground=TEXT_COLOR, font=('Segoe UI', 10, 'bold'))
    style.configure("Dark.TLabel", background=PANEL_BG, foreground=TEXT_COLOR)
    style.configure("Dark.Treeview", background="#252526", foreground=TEXT_COLOR, fieldbackground="#252526", bordercolor="#3e3e42", lightcolor="#3e3e42", darkcolor="#3e3e42")
    style.configure("Dark.Treeview.Heading", background=HIGHLIGHT_COLOR, foreground=TEXT_COLOR, bordercolor="#3e3e42")
    style.map("Dark.Treeview.Heading", background=[('active', ACCENT_COLOR)])
    style.configure("Vertical.TScrollbar", background=PANEL_BG, bordercolor=PANEL_BG, arrowcolor=TEXT_COLOR, troughcolor="#252526")
    style.configure("Horizontal.TScrollbar", background=PANEL_BG, bordercolor=PANEL_BG, arrowcolor=TEXT_COLOR, troughcolor="#252526")
    
    # Main container with padding
    main_container = ttk.Frame(root, style="Dark.TFrame")
    main_container.pack(fill='both', expand=True, padx=10, pady=10)
    
    # Create notebook for tabbed interface
    notebook = ttk.Notebook(main_container)
    notebook.pack(fill='both', expand=True)
    
    # Tab 1: Game View
    game_tab = ttk.Frame(notebook, style="Dark.TFrame")
    notebook.add(game_tab, text="Game View")
    
    # Game view layout - 2x2 grid
    game_tab.grid_columnconfigure(0, weight=1, uniform="group1")
    game_tab.grid_columnconfigure(1, weight=1, uniform="group1")
    game_tab.grid_rowconfigure(0, weight=1, uniform="group2")
    game_tab.grid_rowconfigure(1, weight=1, uniform="group2")
    
    # Emulator Screen
    emulator_frame = create_styled_frame(game_tab, "Emulator Screen", PANEL_BG, TEXT_COLOR)
    emulator_frame.grid(row=0, column=0, padx=5, pady=5, sticky='nsew')
    
    emulator_canvas = tk.Canvas(emulator_frame, width=360, height=315, bg='#000000', highlightthickness=0)
    emulator_canvas.pack(expand=True, fill='both', padx=10, pady=10)
    emulator_canvas.emulator_photo_ref = None
    
    # Collision Overlay
    collision_frame = create_styled_frame(game_tab, "Collision Overlay", PANEL_BG, TEXT_COLOR)
    collision_frame.grid(row=0, column=1, padx=5, pady=5, sticky='nsew')
    
    collision_canvas = tk.Canvas(collision_frame, width=360, height=315, bg='#000000', highlightthickness=0)
    collision_canvas.pack(expand=True, fill='both', padx=10, pady=10)
    collision_canvas.collision_photo_ref = None
    
    # Map View
    map_frame = create_styled_frame(game_tab, "Map View", PANEL_BG, TEXT_COLOR)
    map_frame.grid(row=1, column=0, padx=5, pady=5, sticky='nsew')
    
    map_canvas = tk.Canvas(map_frame, bg='#1a1a1a', highlightthickness=0)
    map_canvas.pack(expand=True, fill='both', padx=10, pady=10)
    initialize_map_canvas(map_canvas)
    
    # Warp Minimap
    warp_frame = create_styled_frame(game_tab, "Warp Minimap", PANEL_BG, TEXT_COLOR)
    warp_frame.grid(row=1, column=1, padx=5, pady=5, sticky='nsew')
    
    warp_container = ttk.Frame(warp_frame, style="Dark.TFrame")
    warp_container.pack(expand=True, fill='both', padx=10, pady=10)
    
    warp_minimap_canvas = tk.Canvas(warp_container, width=300, height=270, bg='#1a1a1a', highlightthickness=0)
    warp_minimap_canvas.pack(side='top', fill='both', expand=True)
    warp_minimap_canvas.warp_data = None
    
    # Tab 2: Quest Progress
    quest_tab = ttk.Frame(notebook, style="Dark.TFrame")
    notebook.add(quest_tab, text="Quest Progress")
    
    # Quest progress with better layout
    quest_tab.grid_columnconfigure(0, weight=1)
    quest_tab.grid_rowconfigure(0, weight=1)
    
    quest_frame = create_styled_frame(quest_tab, "Quest Progress", PANEL_BG, TEXT_COLOR)
    quest_frame.grid(row=0, column=0, padx=5, pady=5, sticky='nsew')
    
    # Create treeview with modern styling
    tree = create_modern_treeview(quest_frame, quests)
    
    # Tab 3: Debug Info
    debug_tab = ttk.Frame(notebook, style="Dark.TFrame")
    notebook.add(debug_tab, text="Debug Info")
    
    # Debug info layout
    debug_tab.grid_columnconfigure(0, weight=1)
    debug_tab.grid_columnconfigure(1, weight=1)
    debug_tab.grid_rowconfigure(0, weight=1)
    
    # Coordinate Debug
    coord_frame = create_styled_frame(debug_tab, "Coordinate Info", PANEL_BG, TEXT_COLOR)
    coord_frame.grid(row=0, column=0, padx=5, pady=5, sticky='nsew')
    
    # Warp Debug
    warp_debug_frame = create_styled_frame(debug_tab, "Warp Debug", PANEL_BG, TEXT_COLOR)
    warp_debug_frame.grid(row=0, column=1, padx=5, pady=5, sticky='nsew')
    
    warp_debug_text = create_styled_text(warp_debug_frame, bg='#1e1e1e', fg=TEXT_COLOR)
    
    # Status bar at bottom
    status_bar = create_status_bar(root, BG_COLOR, TEXT_COLOR)
    
    # Create all labels dictionary
    env_labels = create_debug_labels(coord_frame, PANEL_BG, TEXT_COLOR, SUCCESS_COLOR, WARNING_COLOR, ERROR_COLOR)
    env_labels['status'] = status_bar
    
    # Load resources
    load_resources(map_canvas)
    
    # Test with provided coordinates
    map_canvas.all_quest_coordinates = [(338, 84, 1), (345, 78, 1), (345, 78, 2), (345, 79, 2), 
                                        (345, 80, 2), (344, 80, 2), (343, 80, 2), (342, 80, 2), 
                                        (341, 80, 2), (340, 80, 2), (340, 81, 2), (340, 82, 2), 
                                        (345, 86, 2), (343, 89, 2), (349, 82, 2), (350, 82, 2), 
                                        (351, 82, 2), (352, 82, 2), (352, 81, 2), (353, 81, 2), 
                                        (354, 81, 2), (355, 81, 2), (355, 80, 2), (355, 79, 2)]
    
    # Initialize speech bubble system
    speech_canvases = {
        'emulator': emulator_canvas,
        'collision': collision_canvas,
        'map': map_canvas,
        'warp': warp_minimap_canvas
    }
    speech_manager = initialize_speech_bubbles(speech_canvases)
    print("Speech bubble system initialized for quest UI")
    
    # Set up polling
    setup_polling(root, status_queue, emulator_canvas, collision_canvas, map_canvas, 
                  warp_minimap_canvas, warp_debug_text, tree, env_labels)
    
    # Start the UI
    root.mainloop()

def create_styled_frame(parent, title, bg, fg):
    """Create a styled frame with title"""
    frame = ttk.LabelFrame(parent, text=title, style="Dark.TLabelframe")
    return frame

def create_modern_treeview(parent, quests):
    """Create a modern styled treeview for quest progress"""
    # Treeview with scrollbars
    tree_frame = ttk.Frame(parent, style="Dark.TFrame")
    tree_frame.pack(fill='both', expand=True, padx=10, pady=10)
    
    # Create treeview
    columns = ('status', 'criteria', 'progress')
    tree = ttk.Treeview(tree_frame, columns=columns, show='tree headings', style="Dark.Treeview", height=20)
    
    # Configure columns
    tree.heading('#0', text='Quest / Step', anchor='w')
    tree.heading('status', text='Status', anchor='center')
    tree.heading('criteria', text='Criteria', anchor='w')
    tree.heading('progress', text='Progress', anchor='center')
    
    tree.column('#0', width=400, stretch=True)
    tree.column('status', width=100, stretch=False, anchor='center')
    tree.column('criteria', width=300, stretch=True)
    tree.column('progress', width=150, stretch=False, anchor='center')
    
    # Configure tags for styling
    tree.tag_configure('quest_header', font=('Segoe UI', 11, 'bold'))
    tree.tag_configure('complete', foreground='#4ec9b0', font=('Segoe UI', 10))
    tree.tag_configure('pending', foreground='#dcdcaa', font=('Segoe UI', 10))
    tree.tag_configure('active', background='#264f78')
    tree.tag_configure('step', font=('Segoe UI', 10))
    
    # Scrollbars
    vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=tree.yview, style="Vertical.TScrollbar")
    hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=tree.xview, style="Horizontal.TScrollbar")
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    
    # Grid layout
    tree.grid(row=0, column=0, sticky='nsew')
    vsb.grid(row=0, column=1, sticky='ns')
    hsb.grid(row=1, column=0, sticky='ew')
    
    tree_frame.grid_rowconfigure(0, weight=1)
    tree_frame.grid_columnconfigure(0, weight=1)
    
    # Populate with quests
    for quest in quests:
        qid = quest['quest_id']
        quest_text = f"Quest {qid}: {quest.get('begin_quest_text', '')}"
        parent_item = tree.insert('', 'end', iid=qid, text=quest_text, 
                                 values=('⏳ Pending', '', '0%'), 
                                 tags=('quest_header', 'pending'))
        
        # Add steps
        for idx, step in enumerate(quest.get('subquest_list', [])):
            step_id = f"{qid}_step_{idx}"
            step_text = f"   Step {idx+1}: {step}"
            tree.insert(qid, 'end', iid=step_id, text=step_text, 
                       values=('Pending', '', ''), 
                       tags=('step', 'pending'))
        
        # Add triggers
        for idx, trigger in enumerate(quest.get('event_triggers', [])):
            trigger_id = f"{qid}_{idx}"
            trigger_text = f"   {describe_trigger(trigger)}"
            tree.insert(qid, 'end', iid=trigger_id, text=trigger_text, 
                       values=('Pending', '', ''), 
                       tags=('step', 'pending'))
        
        # Auto-expand quest
        tree.item(qid, open=True)
    
    return tree

def create_styled_text(parent, **kwargs):
    """Create a styled text widget"""
    text = tk.Text(parent, wrap='word', font=('Consolas', 9), bd=0, **kwargs)
    scrollbar = ttk.Scrollbar(parent, orient='vertical', command=text.yview, style="Vertical.TScrollbar")
    text.configure(yscrollcommand=scrollbar.set)
    
    text.pack(side='left', fill='both', expand=True, padx=5, pady=5)
    scrollbar.pack(side='right', fill='y')
    
    return text

def create_status_bar(parent, bg, fg):
    """Create a modern status bar"""
    status_frame = tk.Frame(parent, bg=bg, height=30)
    status_frame.pack(side='bottom', fill='x')
    status_frame.pack_propagate(False)
    
    status_label = tk.Label(status_frame, text="Ready", bg=bg, fg=fg, 
                           font=('Segoe UI', 10), anchor='w', padx=10)
    status_label.pack(fill='both', expand=True)
    
    return status_label

def create_debug_labels(parent, bg, fg, success, warning, error):
    """Create debug info labels with modern styling"""
    labels = {}
    
    # Create sections
    sections = [
        ("Location Info", [
            'direct_map_name', 'direct_map_id', 'direct_local_coords', 
            'direct_global_coords', 'player_global_coords_map', 'direct_facing_direction'
        ]),
        ("Quest Status", [
            'current_quest_disp', 'total_coordinates', 'traversed_count', 
            'current_quest_stats', 'dialog', 'nav_status'
        ]),
        ("System Info", [
            'run_dir_disp', 'total_steps_disp', 'last_action_disp', 
            'action_source_disp', 'in_battle', 'fps_disp'
        ])
    ]
    
    for section_name, label_keys in sections:
        section_frame = ttk.LabelFrame(parent, text=section_name, style="Dark.TLabelframe")
        section_frame.pack(fill='x', padx=5, pady=5)
        
        for key in label_keys:
            label = tk.Label(section_frame, text=f"{key.replace('_', ' ').title()}: N/A", 
                           bg=bg, fg=fg, font=('Consolas', 9), anchor='w')
            label.pack(fill='x', padx=10, pady=2)
            labels[key] = label
    
    # Hidden debug labels
    for key in ['map_calc_details', 'map_tile_coords_display', 'map_sprite_info']:
        labels[key] = tk.Label(parent, text="")
    
    return labels

def setup_polling(root, status_queue, emulator_canvas, collision_canvas, map_canvas, 
                 warp_minimap_canvas, warp_debug_text, tree, env_labels):
    """Set up the polling mechanism"""
    
    def poll():
        try:
            while True:
                try:
                    item_id, status_data = status_queue.get_nowait()
                    
                    # Handle different update types
                    if item_id == '__warp_minimap__':
                        draw_warp_minimap(warp_minimap_canvas, warp_debug_text, status_data)
                    
                    elif item_id == '__location__':
                        handle_location_update(status_data, env_labels, map_canvas)
                    
                    elif item_id == '__emulator_screen__':
                        update_screen_canvas(emulator_canvas, status_data, 'emulator_photo_ref')
                    
                    elif item_id == '__collision_overlay_screen__':
                        update_screen_canvas(collision_canvas, status_data, 'collision_photo_ref')
                    
                    elif item_id == '__current_quest__':
                        handle_quest_update(status_data, env_labels, map_canvas, tree)
                    
                    elif item_id.startswith('__trigger_debug__') or item_id.startswith('__quest_status_detailed__'):
                        update_tree_item(tree, status_data)
                    
                    # Update other labels
                    else:
                        update_label_from_id(item_id, status_data, env_labels, tree)
                    
                except queue.Empty:
                    break
                    
        except Exception as e:
            print(f"UI Error in poll: {e}")
        
        root.after(10, poll)
    
    root.after(100, poll)

# Helper functions for polling
def handle_location_update(status_data, env_labels, map_canvas):
    """Handle location updates"""
    local_x, local_y, map_id, map_name = status_data
    
    # Update labels
    env_labels['direct_map_name'].config(text=f"Map: {map_name}")
    env_labels['direct_map_id'].config(text=f"Map ID: {map_id}")
    env_labels['direct_local_coords'].config(text=f"Local: ({local_x}, {local_y})")
    
    # Convert to global
    global_y, global_x = local_to_global(local_y, local_x, map_id)
    env_labels['direct_global_coords'].config(text=f"Global: ({global_x}, {global_y})")
    
    # Get facing direction
    facing_text = env_labels.get('direct_facing_direction', tk.Label(None, text="Facing: Down")).cget('text')
    facing = facing_text.split(': ')[1] if ': ' in facing_text else 'Down'
    
    # Update map
    draw_map_optimized(map_canvas, local_x, local_y, map_id, map_name, facing, env_labels)
    
    # Save PNG only once on first load (not constantly)
    if not hasattr(map_canvas, '_png_saved_once'):
        try:
            save_full_map_with_overlays(map_canvas, local_x, local_y, map_id, facing)
            map_canvas._png_saved_once = True  # Mark as saved to prevent constant saving
        except Exception as e:
            print(f"Error saving PNG: {e}")

def handle_quest_update(quest_id, env_labels, map_canvas, tree):
    """Handle quest update"""
    env_labels['current_quest_disp'].config(text=f"Current Quest: {quest_id}")
    
    old_quest = map_canvas.current_quest_id
    map_canvas.current_quest_id = quest_id
    
    if old_quest != quest_id:
        map_canvas.quest_paths_drawn = False
        
        # Speech bubble integration - show quest start text for new quest
        try:
            speech_manager = get_speech_bubble_manager()
            if quest_id and old_quest is not None:
                # This is a quest transition, show start text for new quest
                print(f"Quest changed from {old_quest} to {quest_id}, showing start text")
                speech_manager.show_quest_start(quest_id, ['emulator', 'map'])
            elif quest_id and old_quest is None:
                # First quest initialization, show start text
                print(f"Initial quest {quest_id}, showing start text")
                speech_manager.show_quest_start(quest_id, ['emulator', 'map'])
        except Exception as e:
            print(f"Error showing speech bubble for quest start: {e}")
    
    # Update tree highlighting
    for item in tree.get_children():
        tree.item(item, tags=tree.item(item, 'tags'))
    
    if quest_id and tree.exists(str(quest_id)):
        current_tags = list(tree.item(str(quest_id), 'tags'))
        if 'active' not in current_tags:
            current_tags.append('active')
        tree.item(str(quest_id), tags=current_tags)
        tree.see(str(quest_id))

def update_tree_item(tree, status_data):
    """Update tree item with detailed status"""
    item_id = str(status_data.get('id'))
    is_complete = status_data.get('status', False)
    criteria_str = status_data.get('values_str', '')
    progress_str = status_data.get('debug_str', '')
    
    if tree.exists(item_id):
        # Check if this is a quest completion (item is a quest header and just became complete)
        current_tags = list(tree.item(item_id, 'tags'))
        was_pending = 'pending' in current_tags
        is_quest_header = 'quest_header' in current_tags
        
        status = '✅ Complete' if is_complete else '⏳ Pending'
        values = (status, criteria_str, progress_str)
        
        tags = list(tree.item(item_id, 'tags'))
        if 'quest_header' in tags:
            tags = ['quest_header']
        else:
            tags = ['step']
        
        tags.append('complete' if is_complete else 'pending')
        
        tree.item(item_id, values=values, tags=tags)
        
        # Speech bubble integration - show quest completion text
        if is_complete and was_pending and is_quest_header:
            try:
                speech_manager = get_speech_bubble_manager()
                print(f"Quest {item_id} completed, showing completion text")
                speech_manager.show_quest_complete(item_id, ['emulator', 'map'])
            except Exception as e:
                print(f"Error showing speech bubble for quest completion: {e}")
        
        if is_complete and tree.parent(item_id) == "":
            tree.item(item_id, open=False)

def update_label_from_id(item_id, status_data, env_labels, tree):
    """Update label based on item ID"""
    label_mapping = {
        '__dialog__': ('dialog', lambda x: f"Dialog: {x if x else 'None'}"),
        '__nav_status__': ('nav_status', lambda x: f"Navigation: {x}"),
        '__run_dir__': ('run_dir_disp', lambda x: f"Run Dir: {x}"),
        '__total_steps__': ('total_steps_disp', lambda x: f"Total Steps: {x}"),
        '__facing_direction__': ('direct_facing_direction', lambda x: f"Facing: {x}"),
        '__status__': ('status', lambda x: f"Status: {x}"),
        '__last_action__': ('last_action_disp', lambda x: f"Last Action: {x}"),
        '__action_source__': ('action_source_disp', lambda x: f"Action Source: {x}"),
        '__in_battle__': ('in_battle', lambda x: f"In Battle: {x}"),
        '__fps__': ('fps_disp', lambda x: f"FPS: {x}")
    }
    
    if item_id in label_mapping:
        label_key, formatter = label_mapping[item_id]
        if label_key in env_labels:
            env_labels[label_key].config(text=formatter(status_data))
    else:
        # Handle simple quest/trigger updates
        item_id_str = str(item_id)
        if tree.exists(item_id_str):
            status = '✅ Complete' if status_data else '⏳ Pending'
            current_values = list(tree.item(item_id_str, 'values'))
            if current_values:
                current_values[0] = status
                tree.item(item_id_str, values=current_values)

# """
# Quest UI Module - Handles the quest progress UI display
# """
# import tkinter as tk
# from tkinter import ttk
# import queue
# from typing import List, Dict, Any
# from environment.environment_helpers.quest_manager import describe_trigger
# import os
# from pathlib import Path
# try:
#     from PIL import Image, ImageTk
# except ImportError:
#     import Image, ImageTk
# import json, colorsys

# # Global variable to store map data
# MAP_DATA = {}

# def load_map_data():
#     global MAP_DATA
#     try:
#         # Resolve path relative to this script file
#         script_dir = Path(__file__).parent
#         map_data_path = script_dir.parent / "environment" / "data" / "environment_data" / "map_data.json"
#         with open(map_data_path, 'r') as f:
#             data = json.load(f)
#             # Convert list of regions to a dict keyed by map_id for easier lookup
#             MAP_DATA = {region['id']: region for region in data.get('regions', [])}
#             print(f"UI: Successfully loaded map_data.json. {len(MAP_DATA)} regions found.")
#             if "-1" in MAP_DATA:
#                 print(f"UI: Kanto map data: {MAP_DATA['-1']}")
#             else:
#                 print("UI: Kanto map data (id: -1) not found in MAP_DATA.")
#     except FileNotFoundError:
#         print(f"UI: ERROR - map_data.json not found at {map_data_path}")
#         MAP_DATA = {} # Ensure MAP_DATA is an empty dict on failure
#     except json.JSONDecodeError:
#         print(f"UI: ERROR - Could not decode map_data.json")
#         MAP_DATA = {}
#     except Exception as e:
#         print(f"UI: ERROR - Unexpected error loading map_data.json: {e}")
#         MAP_DATA = {}

# # ─── COORDINATE CONVERSION USING EXACT LOGIC FROM a_map_practice.py ──────────
# # Convert MAP_DATA to integer keys for compatibility with a_map_practice.py logic
# MAP_DATA_INT_KEYS = {}

# def prepare_map_data_for_conversion():
#     """Convert MAP_DATA to integer keys to match a_map_practice.py format"""
#     global MAP_DATA_INT_KEYS
#     MAP_DATA_INT_KEYS = {}
#     for str_id, region_data in MAP_DATA.items():
#         try:
#             int_id = int(str_id)
#             MAP_DATA_INT_KEYS[int_id] = region_data
#         except ValueError:
#             print(f"UI: Warning - Could not convert map id '{str_id}' to integer")

# def local_to_global(r, c, map_n):
#     """EXACT logic from a_map_practice.py - DO NOT CHANGE"""
#     try:
#         map_x, map_y = MAP_DATA_INT_KEYS[map_n]['coordinates']
#         return r + map_y, c + map_x
#     except KeyError:
#         print(f'UI: Map id {map_n} not found in map_data.json.')
#         return r + 0, c + 0

# def start_quest_ui(quests: List[Dict[str, Any]], status_queue):
#     """Start the quest progress UI in a separate thread"""
#     load_map_data() # Load map data when UI starts
#     prepare_map_data_for_conversion() # Prepare integer-keyed map data for coordinate conversion
#     root = tk.Tk()
    
#     # Make window large and non-resizable
#     root.title("Quest Progress & Environment Monitor")
#     try:
#         root.state('zoomed')  # Works on Windows and some Linux window managers
#     except Exception:
#         try:
#             root.attributes('-zoomed', True)  # Fallback for other platforms
#         except Exception:
#             root.geometry("1400x900")
#     root.minsize(1200, 800)
#     root.resizable(True, True)
    
#     # Increase default font sizes
#     import tkinter.font as tkfont
#     default_font = tkfont.nametofont("TkDefaultFont")
#     default_font.configure(size=11)
#     header_font = tkfont.nametofont("TkHeadingFont") if "TkHeadingFont" in tkfont.names() else default_font
#     header_font.configure(size=12, weight="bold")
    
#     # Create main frame with 2x2 grid layout
#     main_frame = ttk.Frame(root)
#     main_frame.pack(fill='both', expand=True, padx=5, pady=5)
    
#     # Configure grid to NOT expand - use fixed sizes
#     main_frame.grid_columnconfigure(0, weight=0, minsize=390)
#     main_frame.grid_columnconfigure(1, weight=0, minsize=390)
#     main_frame.grid_rowconfigure(0, weight=0, minsize=355)
#     main_frame.grid_rowconfigure(1, weight=0, minsize=355)

#     # TOP LEFT: Emulator Screen (380x345)
#     emulator_frame = ttk.LabelFrame(main_frame, text="Emulator Screen", padding=5)
#     emulator_frame.grid(row=0, column=0, padx=2, pady=2)
#     emulator_frame.configure(width=380, height=345)
#     emulator_frame.grid_propagate(False)  # Prevent frame from shrinking/expanding
    
#     emulator_canvas = tk.Canvas(emulator_frame, width=360, height=315, bg='black')
#     emulator_canvas.pack(expand=False, fill='none', anchor='center')
#     emulator_canvas.emulator_photo_ref = None

#     # TOP RIGHT: Collision Overlay Screen (380x345)
#     collision_frame = ttk.LabelFrame(main_frame, text="Collision Overlay Screen", padding=5)
#     collision_frame.grid(row=0, column=1, padx=2, pady=2)
#     collision_frame.configure(width=380, height=345)
#     collision_frame.grid_propagate(False)  # Prevent frame from shrinking/expanding
    
#     collision_canvas = tk.Canvas(collision_frame, width=360, height=315, bg='black')
#     collision_canvas.pack(expand=False, fill='none', anchor='center')
#     collision_canvas.collision_photo_ref = None

#     # BOTTOM LEFT: Warp Minimap and Debug Info (380x345)
#     bottom_left_frame = ttk.Frame(main_frame)
#     bottom_left_frame.grid(row=1, column=0, padx=2, pady=2)
#     bottom_left_frame.configure(width=380, height=345)
#     bottom_left_frame.grid_propagate(False)  # Prevent frame from shrinking/expanding
    
#     # Configure the bottom left to have warp minimap on top and debug info below
#     bottom_left_frame.grid_rowconfigure(0, weight=2)  # Minimap gets more space
#     bottom_left_frame.grid_rowconfigure(1, weight=1)  # Debug info gets less space
#     bottom_left_frame.grid_columnconfigure(0, weight=1)
    
#     # Warp Minimap (9x10) in top of bottom left
#     warp_minimap_frame = ttk.LabelFrame(bottom_left_frame, text="Warp Minimap (9x10)", padding=5)
#     warp_minimap_frame.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)
    
#     warp_minimap_canvas = tk.Canvas(warp_minimap_frame, width=350, height=200, bg='lightgray')
#     warp_minimap_canvas.pack(expand=False, fill='none', anchor='center')
#     warp_minimap_canvas.warp_data = None

#     # Warp Debug Info in bottom of bottom left
#     warp_debug_frame = ttk.LabelFrame(bottom_left_frame, text="Warp Debug Info", padding=5)
#     warp_debug_frame.grid(row=1, column=0, sticky='nsew', padx=2, pady=2)
    
#     warp_debug_text = tk.Text(warp_debug_frame, bg='black', fg='white', font=('Courier', 8))
#     warp_debug_scrollbar = ttk.Scrollbar(warp_debug_frame, orient="vertical", command=warp_debug_text.yview)
#     warp_debug_text.configure(yscrollcommand=warp_debug_scrollbar.set)
#     warp_debug_text.pack(side='left', fill='both', expand=True)
#     warp_debug_scrollbar.pack(side='right', fill='y')
#     warp_debug_text.config(state='disabled')

#     # BOTTOM RIGHT: Map View with Quest Progress (380x345)
#     bottom_right_frame = ttk.Frame(main_frame)
#     bottom_right_frame.grid(row=1, column=1, padx=2, pady=2)
#     bottom_right_frame.configure(width=380, height=345)
#     bottom_right_frame.grid_propagate(False)  # Prevent frame from shrinking/expanding
    
#     # Use PanedWindow for map and quest progress
#     right_paned = ttk.PanedWindow(bottom_right_frame, orient='vertical')
#     right_paned.pack(fill='both', expand=True)

#     # Map View Frame (with legend integrated)
#     map_view_frame = ttk.LabelFrame(right_paned, text="Map View", padding=5)
#     right_paned.add(map_view_frame, weight=3)
    
#     # Create frame for map and legend side by side
#     map_container = ttk.Frame(map_view_frame)
#     map_container.pack(fill='both', expand=True)
    
#     # Configure grid for map and legend
#     map_container.grid_columnconfigure(0, weight=4)  # Map gets most space
#     map_container.grid_columnconfigure(1, weight=1)  # Legend gets some space
#     map_container.grid_rowconfigure(0, weight=1)
    
#     # Map canvas
#     map_canvas = tk.Canvas(map_container, bg='lightgrey')
#     map_canvas.grid(row=0, column=0, sticky='nsew', padx=(0,5))
#     map_canvas.map_photo = None
#     map_canvas.sprite_photo = None
#     map_canvas.kanto_map_tile_width = 444
#     map_canvas.kanto_map_tile_height = 436
#     map_canvas.initial_player_dot_drawn = False
    
#     # Initialize map canvas attributes for coordinate tracking
#     map_canvas.all_quest_coordinates = []
#     map_canvas.traversed_coordinates = set()
#     map_canvas.current_quest_id = None
#     map_canvas.coordinate_traversal_distance = 0  # Changed from 3 to 0 for exact position matching
#     map_canvas.quest_paths_drawn = False
#     map_canvas.cached_sprite_photos = {}
#     map_canvas.sprite_image_id = None
#     map_canvas.sprite_frames = []

#     # Legend panel (fixed position, always visible)
#     legend_frame = ttk.LabelFrame(map_container, text="Quest Coordinate Legend", padding=5)
#     legend_frame.grid(row=0, column=1, sticky='nsew')
    
#     # Create visual legend with actual colors
#     legend_canvas = tk.Canvas(legend_frame, width=150, height=200, bg='white')
#     legend_canvas.pack(fill='both', expand=True, padx=5, pady=5)
    
#     # Draw color samples in legend
#     y_offset = 10
#     legend_items = [
#         ("Other Quest (Untraversed)", "#87CEEB", 2),   # Light blue, small
#         ("Other Quest (Traversed)", "#0080FF", 4),     # Bright blue, larger  
#         ("Current Quest (Untraversed)", "#FFA500", 3), # Orange, medium
#         ("Current Quest (Traversed)", "#00FF00", 5)    # Bright green, largest
#     ]
    
#     for text, color, size in legend_items:
#         # Draw color square
#         legend_canvas.create_rectangle(5, y_offset, 5+size*2, y_offset+size*2, 
#                                      fill=color, outline='black', width=1)
#         # Add text
#         legend_canvas.create_text(5+size*2+5, y_offset+size, text=text, 
#                                 anchor='w', font=('TkDefaultFont', 8))
#         y_offset += size*2 + 15
    
#     # Add note about traversal
#     legend_canvas.create_text(5, y_offset+10, 
#                             text="Coordinates marked when\nplayer position exactly\nmatches coordinate", 
#                             anchor='nw', font=('TkDefaultFont', 7), fill='red')

#     # Quest Progress Frame
#     quest_tree_frame = ttk.Frame(right_paned)
#     right_paned.add(quest_tree_frame, weight=1)
    
#     quest_title = tk.Label(quest_tree_frame, text="Quest Progress", font=header_font)
#     quest_title.pack(fill='x')
    
#     style = ttk.Style(root)
#     style.configure("Treeview", rowheight=24, font=(default_font.actual('family'), 10))
#     style.configure("Treeview.Heading", font=(default_font.actual('family'), 10, 'bold'))
    
#     tree_columns = ('status', 'criteria_values', 'debug_calcs')
#     tree = ttk.Treeview(quest_tree_frame, columns=tree_columns, show='tree headings')
#     tree.heading('#0', text='Description')
#     tree.heading('status', text='Status')
#     tree.heading('criteria_values', text='Criteria Values')
#     tree.heading('debug_calcs', text='Debug/Calculations')
    
#     tree.column('#0', width=250, stretch=tk.NO)
#     tree.column('status', width=80, stretch=tk.NO)
#     tree.column('criteria_values', width=200, stretch=tk.NO)
#     tree.column('debug_calcs', width=300)
    
#     tree.tag_configure('done', foreground='green')
#     tree.tag_configure('pending', foreground='red')
#     tree.tag_configure('active_quest', background='lightyellow')
#     tree.tag_configure('debug_text', font=(default_font.actual('family'), 9))
    
#     quest_scroll_frame = ttk.Frame(quest_tree_frame)
#     quest_scroll_frame.pack(fill='both', expand=True)
    
#     vsb = ttk.Scrollbar(quest_scroll_frame, orient='vertical', command=tree.yview)
#     vsb.pack(side='right', fill='y')
#     tree.configure(yscrollcommand=vsb.set)
    
#     hbar = ttk.Scrollbar(quest_scroll_frame, orient='horizontal', command=tree.xview)
#     hbar.pack(side='bottom', fill='x')
#     tree.configure(xscrollcommand=hbar.set)
    
#     tree.pack(side='left', fill='both', expand=True)

#     # Environment labels dictionary
#     env_labels = {}
    
#     # Initialize coordinate statistics for legend updates - start as labels
#     # Create debug/status panels in the legend frame for map information
#     debug_info_frame = ttk.LabelFrame(legend_frame, text="Debug Info", padding=2)
#     debug_info_frame.pack(fill='x', pady=(10,0))
    
#     env_labels['total_coordinates'] = tk.Label(debug_info_frame, text="Total Coordinates: Loading...", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['total_coordinates'].pack(fill='x')
    
#     env_labels['traversed_count'] = tk.Label(debug_info_frame, text="Traversed: 0", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['traversed_count'].pack(fill='x')
    
#     env_labels['current_quest_stats'] = tk.Label(debug_info_frame, text="Current Quest: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['current_quest_stats'].pack(fill='x')
    
#     # Add detailed coordinate debug labels in a separate frame
#     coord_debug_frame = ttk.LabelFrame(legend_frame, text="Coordinate Debug", padding=2)
#     coord_debug_frame.pack(fill='x', pady=(5,0))
    
#     env_labels['direct_map_name'] = tk.Label(coord_debug_frame, text="Direct Map: N/A", font=('TkDefaultFont', 7), anchor='w')
#     env_labels['direct_map_name'].pack(fill='x')
    
#     env_labels['direct_map_id'] = tk.Label(coord_debug_frame, text="Direct Map ID: N/A", font=('TkDefaultFont', 7), anchor='w')
#     env_labels['direct_map_id'].pack(fill='x')
    
#     env_labels['direct_local_coords'] = tk.Label(coord_debug_frame, text="Direct Local: N/A", font=('TkDefaultFont', 7), anchor='w')
#     env_labels['direct_local_coords'].pack(fill='x')
    
#     env_labels['direct_global_coords'] = tk.Label(coord_debug_frame, text="Direct Global: N/A", font=('TkDefaultFont', 7), anchor='w')
#     env_labels['direct_global_coords'].pack(fill='x')
    
#     env_labels['player_global_coords_map'] = tk.Label(coord_debug_frame, text="Player Global (for map): N/A", font=('TkDefaultFont', 7), anchor='w')
#     env_labels['player_global_coords_map'].pack(fill='x')
    
#     env_labels['direct_facing_direction'] = tk.Label(coord_debug_frame, text="Direct Facing: N/A", font=('TkDefaultFont', 7), anchor='w')
#     env_labels['direct_facing_direction'].pack(fill='x')
    
#     # Map calculation details
#     env_labels['map_calc_details'] = tk.Label(coord_debug_frame, text="Map calc details...", font=('TkDefaultFont', 6), anchor='w', justify='left')
#     env_labels['map_calc_details'].pack(fill='x')
    
#     env_labels['map_tile_coords_display'] = tk.Label(coord_debug_frame, text="Tile coords...", font=('TkDefaultFont', 6), anchor='w', justify='left')
#     env_labels['map_tile_coords_display'].pack(fill='x')
    
#     env_labels['map_sprite_info'] = tk.Label(coord_debug_frame, text="Sprite info...", font=('TkDefaultFont', 6), anchor='w', justify='left')
#     env_labels['map_sprite_info'].pack(fill='x')
    
#     # Additional status labels for quest UI - place in warp debug frame
#     quest_status_frame = ttk.LabelFrame(warp_debug_frame, text="Quest Status", padding=2)
#     quest_status_frame.pack(fill='x', pady=(5,0))
    
#     env_labels['current_quest_disp'] = tk.Label(quest_status_frame, text="Current Quest ID: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['current_quest_disp'].pack(fill='x')
    
#     env_labels['dialog'] = tk.Label(quest_status_frame, text="Dialog: None", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['dialog'].pack(fill='x')
    
#     env_labels['nav_status'] = tk.Label(quest_status_frame, text="Navigation: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['nav_status'].pack(fill='x')
    
#     env_labels['run_dir_disp'] = tk.Label(quest_status_frame, text="Run Dir: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['run_dir_disp'].pack(fill='x')
    
#     env_labels['total_steps_disp'] = tk.Label(quest_status_frame, text="Total Steps: 0", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['total_steps_disp'].pack(fill='x')
    
#     env_labels['last_action_disp'] = tk.Label(quest_status_frame, text="Last Action: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['last_action_disp'].pack(fill='x')
    
#     env_labels['action_source_disp'] = tk.Label(quest_status_frame, text="Action Source: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['action_source_disp'].pack(fill='x')
    
#     env_labels['in_battle'] = tk.Label(quest_status_frame, text="In Battle: False", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['in_battle'].pack(fill='x')
    
#     env_labels['fps_disp'] = tk.Label(quest_status_frame, text="FPS: N/A", font=('TkDefaultFont', 8), anchor='w')
#     env_labels['fps_disp'].pack(fill='x')

#     # Load images from environment data directory
#     base_dir = Path(__file__).resolve().parent.parent
#     env_data_dir = base_dir / "environment" / "data" / "environment_data"
#     full_map_img = Image.open(env_data_dir / "full_kanto_map.png")
#     spritesheet_img = Image.open(env_data_dir / "pokemon_red_player_spritesheet.png")
#     sprite_frames = []
#     for i in range(10):
#         x = i * (16 + 1)
#         frame = spritesheet_img.crop((x, 0, x + 16, 16)).convert("RGBA")
#         datas = frame.getdata()
#         new_data = []
#         for item in datas:
#             if item[:3] in [(255,165,0), (255,184,77)]:
#                 new_data.append((255,255,255,0))
#             else:
#                 new_data.append(item)
#         frame.putdata(new_data)
#         sprite_frames.append(frame)
#     # Store references to prevent garbage collection
#     map_canvas.full_map_img = full_map_img
#     map_canvas.sprite_frames = sprite_frames
#     # map_canvas.map_photo = None # Already initialized
#     # map_canvas.sprite_photo = None # Already initialized
#     # emulator_canvas.emulator_photo_ref = None # Already initialized

#     # After loading sprite_frames, add comprehensive quest coordinate visualization
#     # Use the combined quest coordinates file which has global coordinates
#     combined_coords_file = base_dir / "environment" / "environment_helpers" / "quest_paths" / "combined_quest_coordinates_continuous.json"
#     quest_coords = {}
#     quest_colors = {}
    
#     # NEW: Global coordinate visualization system
#     all_quest_coordinates = []  # All coordinates: [(gy, gx, quest_id), ...]
#     traversed_coordinates = set()  # Coordinates that have been traversed: {(gy, gx), ...}
#     current_quest_id = None  # Track current quest for distinct visualization
#     coordinate_traversal_distance = 3  # Distance threshold for marking coordinates as traversed
    
#     print(f"UI: DIAGNOSTIC - Looking for coordinate file: {combined_coords_file}")
#     print(f"UI: DIAGNOSTIC - File exists: {combined_coords_file.exists()}")
    
#     # DIAGNOSTIC: List all files in the quest_paths directory
#     quest_paths_dir = base_dir / "environment" / "environment_helpers" / "quest_paths"
#     print(f"UI: DIAGNOSTIC - Quest paths directory: {quest_paths_dir}")
#     print(f"UI: DIAGNOSTIC - Quest paths directory exists: {quest_paths_dir.exists()}")
    
#     if quest_paths_dir.exists():
#         try:
#             all_files = list(quest_paths_dir.rglob("*"))
#             coord_files = [f for f in all_files if f.name.endswith("_coords.json")]
#             print(f"UI: DIAGNOSTIC - Found {len(coord_files)} coordinate files in quest_paths")
#             for f in coord_files[:10]:  # Show first 10
#                 print(f"UI: DIAGNOSTIC -   {f}")
#         except Exception as e:
#             print(f"UI: DIAGNOSTIC - Error listing quest_paths directory: {e}")
    
#     # DIAGNOSTIC: Check for the continuous file in multiple locations
#     possible_continuous_files = [
#         base_dir / "combined_quest_coordinates_continuous.json",
#         base_dir / "environment" / "combined_quest_coordinates_continuous.json", 
#         base_dir / "environment" / "environment_helpers" / "combined_quest_coordinates_continuous.json",
#         base_dir / "environment" / "environment_helpers" / "quest_paths" / "combined_quest_coordinates_continuous.json"
#     ]
    
#     print(f"UI: DIAGNOSTIC - Checking for continuous file in multiple locations:")
#     for possible_file in possible_continuous_files:
#         exists = possible_file.exists()
#         print(f"UI: DIAGNOSTIC -   {possible_file}: {exists}")
#         if exists:
#             combined_coords_file = possible_file  # Use the found file
#             break
    
#     if combined_coords_file.exists():
#         try:
#             print(f"UI: DIAGNOSTIC - Attempting to load coordinate file...")
#             with open(combined_coords_file, 'r') as f:
#                 combined_data = json.load(f)
            
#             quest_start_indices = combined_data.get('quest_start_indices', {})
#             all_coordinates = combined_data.get('coordinates', [])
            
#             print(f"UI: DIAGNOSTIC - File loaded - quest_start_indices keys: {list(quest_start_indices.keys())}")
#             print(f"UI: DIAGNOSTIC - Total coordinates in file: {len(all_coordinates)}")
            
#             # Create quest-specific coordinate lists
#             quest_ids = sorted(quest_start_indices.keys(), key=lambda x: int(x))
#             total_quests = len(quest_ids)
#             print(f"UI: DIAGNOSTIC - Processing {total_quests} quests: {quest_ids}")
            
#             # NEW: Build comprehensive coordinate list for global visualization
#             for idx, qid in enumerate(quest_ids):
#                 start_idx = quest_start_indices[qid]
#                 # Find end index (start of next quest or end of coordinates)
#                 if idx + 1 < len(quest_ids):
#                     next_qid = quest_ids[idx + 1]
#                     end_idx = quest_start_indices[next_qid]
#                 else:
#                     end_idx = len(all_coordinates)
                
#                 quest_coord_count = 0
#                 # Extract coordinates for this quest (already in global coordinates)
#                 quest_coords_list = []
#                 for coord_data in all_coordinates[start_idx:end_idx]:
#                     if len(coord_data) >= 2:
#                         gy, gx = coord_data[0], coord_data[1]
#                         quest_coords_list.append((gy, gx))
#                         # Add to global coordinate list with quest ID
#                         all_quest_coordinates.append((gy, gx, int(qid)))
#                         quest_coord_count += 1
                
#                 quest_coords[qid.zfill(3)] = quest_coords_list
                
#                 # Generate color for this quest
#                 h, s, v = idx/total_quests, 1.0, 1.0
#                 r, g, b = colorsys.hsv_to_rgb(h, s, v)
#                 quest_colors[qid.zfill(3)] = '#%02x%02x%02x' % (int(r*255), int(g*255), int(b*255))
                
#                 print(f"UI: DIAGNOSTIC - Quest {qid}: {quest_coord_count} coordinates (indices {start_idx}-{end_idx-1})")
                
#             print(f"UI: DIAGNOSTIC - Successfully loaded {len(all_quest_coordinates)} total coordinates from {len(quest_ids)} quests")
                
#         except Exception as e:
#             print(f"UI: ERROR - Error loading combined quest coordinates: {e}")
#             import traceback
#             traceback.print_exc()
#             # Fallback to individual quest files if combined file fails
#             quest_paths_dir = base_dir / "environment" / "environment_helpers" / "quest_paths"
#             print(f"UI: DIAGNOSTIC - Falling back to individual quest files in: {quest_paths_dir}")
#             if quest_paths_dir.exists():
#                 dirs = sorted([d for d in quest_paths_dir.iterdir() if d.is_dir()])
#                 total = len(dirs)
#                 print(f"UI: DIAGNOSTIC - Found {total} quest directories")
#                 for idx, d in enumerate(dirs):
#                     qid = d.name
#                     fpath = d / f"{qid}_coords.json"
#                     try:
#                         data = json.load(open(fpath))
#                         flat = []
#                         for arr in data.values():
#                             for pair in arr:
#                                 flat.append(tuple(pair))
#                                 # Add to global list
#                                 all_quest_coordinates.append((pair[0], pair[1], int(qid)))
#                         quest_coords[qid] = flat
#                         h, s, v = idx/total, 1.0, 1.0
#                         r, g, b = colorsys.hsv_to_rgb(h, s, v)
#                         quest_colors[qid] = '#%02x%02x%02x' % (int(r*255), int(g*255), int(b*255))
#                         print(f"UI: DIAGNOSTIC - Loaded quest {qid} from individual file: {len(flat)} coordinates")
#                     except Exception as e2:
#                         print(f"UI: DIAGNOSTIC - Failed to load quest {qid}: {e2}")
#                         continue
#             else:
#                 print(f"UI: ERROR - Quest paths directory does not exist: {quest_paths_dir}")
#     else:
#         print(f"UI: ERROR - Combined coordinates file not found: {combined_coords_file}")
#         # Try to find any coordinate files
#         quest_paths_dir = base_dir / "environment" / "environment_helpers" / "quest_paths"
#         print(f"UI: DIAGNOSTIC - Looking for quest paths directory: {quest_paths_dir}")
#         if quest_paths_dir.exists():
#             print(f"UI: DIAGNOSTIC - Quest paths directory exists, attempting fallback...")
#             dirs = sorted([d for d in quest_paths_dir.iterdir() if d.is_dir()])
#             total = len(dirs)
#             print(f"UI: DIAGNOSTIC - Found {total} quest directories")
#             for idx, d in enumerate(dirs):
#                 qid = d.name
#                 fpath = d / f"{qid}_coords.json"
#                 try:
#                     data = json.load(open(fpath))
#                     flat = []
#                     for arr in data.values():
#                         for pair in arr:
#                             flat.append(tuple(pair))
#                             # Add to global list
#                             all_quest_coordinates.append((pair[0], pair[1], int(qid)))
#                     quest_coords[qid] = flat
#                     h, s, v = idx/total, 1.0, 1.0
#                     r, g, b = colorsys.hsv_to_rgb(h, s, v)
#                     quest_colors[qid] = '#%02x%02x%02x' % (int(r*255), int(g*255), int(b*255))
#                     print(f"UI: DIAGNOSTIC - Loaded quest {qid} from individual file: {len(flat)} coordinates")
#                 except Exception as e3:
#                     print(f"UI: DIAGNOSTIC - Failed to load quest {qid}: {e3}")
#                     continue
#         else:
#             print(f"UI: ERROR - No quest coordinate files found anywhere!")
    
#     map_canvas.quest_coords = quest_coords
#     map_canvas.quest_colors = quest_colors
    
#     # NEW: Store comprehensive coordinate data for enhanced visualization
#     map_canvas.all_quest_coordinates = all_quest_coordinates
#     map_canvas.traversed_coordinates = traversed_coordinates
#     map_canvas.current_quest_id = current_quest_id
#     map_canvas.coordinate_traversal_distance = coordinate_traversal_distance
    
#     # DIAGNOSTIC: Verify coordinates were stored
#     print(f"UI: DIAGNOSTIC - Stored {len(all_quest_coordinates)} coordinates in map_canvas.all_quest_coordinates")
#     if all_quest_coordinates:
#         print(f"UI: DIAGNOSTIC - First 5 coordinates: {all_quest_coordinates[:5]}")
#         print(f"UI: DIAGNOSTIC - Last 5 coordinates: {all_quest_coordinates[-5:]}")
#         # Check for unique quest IDs
#         quest_ids_in_coords = set(coord[2] for coord in all_quest_coordinates)
#         print(f"UI: DIAGNOSTIC - Quest IDs represented in coordinates: {sorted(quest_ids_in_coords)}")
#     else:
#         print(f"UI: DIAGNOSTIC - WARNING - No coordinates stored in all_quest_coordinates!")
    
#     # PERFORMANCE: Pre-cache PhotoImage objects to avoid expensive recreations
#     map_canvas.cached_map_photo = None
#     map_canvas.cached_sprite_photos = {}  # Cache sprites by facing direction
#     map_canvas.last_map_offset = (0, 0)  # Track map offset for incremental updates
#     map_canvas.map_image_id = None  # Track map canvas item ID
#     map_canvas.sprite_image_id = None  # Track sprite canvas item ID
#     map_canvas.quest_paths_drawn = False  # Track if quest paths are already drawn
    
#     # FORCE initial coordinate drawing - don't wait for map position changes
#     print(f"UI: DIAGNOSTIC - Forcing initial coordinate drawing...")
#     if map_canvas.all_quest_coordinates:
#         print(f"UI: DIAGNOSTIC - Will force draw {len(map_canvas.all_quest_coordinates)} coordinates")
#         # Clear the flag to force drawing
#         map_canvas.quest_paths_drawn = False
        
#         # IMMEDIATE TEST: Draw some coordinates right now to verify drawing works
#         print(f"UI: DIAGNOSTIC - Drawing immediate test coordinates...")
#         TILE_SIZE = 16
#         for i, (coord_gy, coord_gx, quest_id) in enumerate(map_canvas.all_quest_coordinates[:20]):  # Draw first 20
#             # Convert global coordinates to pixel coordinates (no scaling - pixel perfect)
#             pixel_x = coord_gx * TILE_SIZE + TILE_SIZE // 2  # Center of tile
#             pixel_y = coord_gy * TILE_SIZE + TILE_SIZE // 2  # Center of tile
            
#             # For immediate test, just draw relative to canvas center
#             canvas_w = int(map_canvas.cget('width'))
#             canvas_h = int(map_canvas.cget('height'))
#             center_x = canvas_w // 2
#             center_y = canvas_h // 2
            
#             # Offset coordinates to center on canvas
#             kanto_center_pixel_x = 220 * TILE_SIZE  # Approximate Kanto center in pixels
#             kanto_center_pixel_y = 220 * TILE_SIZE
#             canvas_x = pixel_x - kanto_center_pixel_x + center_x
#             canvas_y = pixel_y - kanto_center_pixel_y + center_y
            
#             # Draw a large, bright test marker
#             test_size = 8
#             test_color = "#FF0000" if i < 5 else "#00FF00" if i < 10 else "#0000FF"
#             map_canvas.create_rectangle(
#                 canvas_x - test_size, canvas_y - test_size,
#                 canvas_x + test_size, canvas_y + test_size,
#                 fill=test_color, outline="white", width=2,
#                 tags="immediate_test_coords"
#             )
#             print(f"UI: DIAGNOSTIC - Drew immediate test coord {i}: global ({coord_gy}, {coord_gx}) -> pixel ({pixel_x}, {pixel_y}) -> canvas ({canvas_x}, {canvas_y}) in {test_color}")
#     else:
#         print(f"UI: DIAGNOSTIC - No coordinates to force draw!")

#     # Path Visualizer section - path_state and draw_paths()
#     # Internal state for path visualization (path_state and draw_paths remain the same, drawing on env_path_canvas and nav_path_canvas)
#     # path_state = {'env_coords': [], 'nav_coords': [], 'path_index': 0} # Already initialized if path canvases are kept
#     # def draw_paths(): # Original draw_paths might need update for new canvas locations or removal
#     # Simplified: If path canvases are removed, comment out draw_paths calls or adapt

#     # Bottom status bar
#     status_frame = tk.Frame(root)
#     status_frame.pack(side='bottom', fill='x', pady=5)
    
#     env_labels['status'] = tk.Label(status_frame, text="Status: Initializing...", relief='sunken', anchor='w')
#     env_labels['status'].pack(fill='x', padx=5)

#     # Populate rows with quests, subquests, and their criteria
#     for q in quests:
#         qid = q['quest_id']
#         # Insert quest as parent with description
#         parent_text = f"Quest {qid}: {q.get('begin_quest_text','')}"
#         tree.insert('', 'end', iid=qid, text=parent_text, values=('Pending',))
#         # Insert subquests (step descriptions) as children
#         for sidx, step in enumerate(q.get('subquest_list', [])):
#             sub_id = f"{qid}_step_{sidx}"
#             sub_text = f"Step {sidx+1}: {step}"
#             tree.insert(qid, 'end', iid=sub_id, text=sub_text, values=('Pending',))
#         # Insert criteria as children
#         for idx, trg in enumerate(q.get('event_triggers', [])):
#             trigger_id = f"{qid}_{idx}"
#             crit_text = describe_trigger(trg)
#             tree.insert(qid, 'end', iid=trigger_id, text=crit_text, values=('Pending',))
#     # Auto-expand all quests to show triggers and steps
#     for q in quests:
#         tree.item(q['quest_id'], open=True)

#     # Snap to first pending quest helper
#     def snap_to_current():
#         # Snap only among quests still present
#         root_ids = tree.get_children()
#         for qid in root_ids:
#             if tree.item(qid, 'values')[0] == 'Pending':
#                 tree.selection_set(qid)
#                 tree.see(qid)
#                 break

#     def draw_warp_minimap(warp_data):
#         """Draw the warp minimap from observation data and debug info"""
#         try:
#             warp_minimap_canvas.delete("all")  # Clear previous content
            
#             # Handle both old format (just numpy array) and new format (dict with minimap + debug_info)
#             if isinstance(warp_data, dict):
#                 warp_obs_data = warp_data.get("minimap")
#                 debug_info = warp_data.get("debug_info", {})
#             else:
#                 warp_obs_data = warp_data
#                 debug_info = {}
            
#             # Update debug info
#             debug_text = ""
            
#             if warp_obs_data is None:
#                 warp_minimap_canvas.create_text(150, 135, text="No Warp Data", fill="red", font=("Arial", 12))
#                 debug_text = "No warp observation data received"
#             else:
#                 # warp_obs_data should be a 9x10 numpy array with warp IDs
#                 canvas_width = 300
#                 canvas_height = 270
#                 tile_width = canvas_width / 10  # 10 columns
#                 tile_height = canvas_height / 9  # 9 rows
                
#                 # Collect warp information for debug
#                 warp_tiles = []
#                 total_warps = 0
                
#                 # Draw grid and warp tiles
#                 for row in range(9):
#                     for col in range(10):
#                         x1 = col * tile_width
#                         y1 = row * tile_height
#                         x2 = x1 + tile_width
#                         y2 = y1 + tile_height
                        
#                         warp_id = warp_obs_data[row, col] if hasattr(warp_obs_data, 'shape') else 0
                        
#                         if warp_id > 0:
#                             total_warps += 1
#                             warp_tiles.append(f"({col},{row}): ID={warp_id}")
                            
#                             # Color code based on warp ID (use HSV for variety)
#                             hue = (warp_id * 137.5) % 360  # Golden angle for good distribution
#                             rgb = colorsys.hsv_to_rgb(hue / 360, 0.7, 0.9)
#                             color = f"#{int(rgb[0]*255):02x}{int(rgb[1]*255):02x}{int(rgb[2]*255):02x}"
#                             warp_minimap_canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="black")
                            
#                             # Add warp ID text if tile is large enough
#                             if tile_width > 25:
#                                 warp_minimap_canvas.create_text(
#                                     x1 + tile_width/2, y1 + tile_height/2, 
#                                     text=str(warp_id), fill="black", font=("Arial", 8)
#                                 )
#                         else:
#                             # Empty tile
#                             warp_minimap_canvas.create_rectangle(x1, y1, x2, y2, fill="white", outline="lightgray")
                            
#                 # Add player position indicator (center at 4,4)
#                 center_col, center_row = 4, 4
#                 player_x = center_col * tile_width + tile_width/2
#                 player_y = center_row * tile_height + tile_height/2
#                 warp_minimap_canvas.create_oval(
#                     player_x - 5, player_y - 5, player_x + 5, player_y + 5,
#                     fill="red", outline="darkred", width=2
#                 )
                
#                 # Prepare comprehensive debug text
#                 debug_text = f"=== WARP DEBUG INFO ===\n"
                
#                 # Environment debug info
#                 if debug_info:
#                     debug_text += f"Map: {debug_info.get('map_name', 'Unknown')} (ID: {debug_info.get('current_map', '?')})\n"
#                     debug_text += f"Is Warping: {debug_info.get('is_warping', 'Unknown')}\n"
                    
#                     cache_status = debug_info.get('cache_status', {})
#                     debug_text += f"Cache - Warping: {cache_status.get('is_warping_cached', '?')}, Minimap: {cache_status.get('minimap_cached', '?')}\n\n"
                    
#                     # WARP_DICT entries
#                     warp_dict_entries = debug_info.get('warp_dict_entries', [])
#                     debug_text += f"WARP_DICT Entries: {len(warp_dict_entries)}\n"
#                     for entry in warp_dict_entries[:5]:  # Show first 5
#                         debug_text += f"  [{entry['index']}] ({entry['x']},{entry['y']}) -> Map {entry['target_map_id']}\n"
#                     if len(warp_dict_entries) > 5:
#                         debug_text += f"  ... and {len(warp_dict_entries) - 5} more\n"
                    
#                     # Memory warps
#                     memory_warps = debug_info.get('memory_warps', [])
#                     debug_text += f"\nMemory Warps: {len(memory_warps)}\n"
#                     for warp in memory_warps[:5]:  # Show first 5
#                         debug_text += f"  [{warp['index']}] ({warp['x']},{warp['y']}) -> Map {warp['dest_map']}\n"
#                     if len(memory_warps) > 5:
#                         debug_text += f"  ... and {len(memory_warps) - 5} more\n"
                    
#                     # Errors
#                     if 'warp_dict_error' in debug_info:
#                         debug_text += f"\nWARP_DICT Error: {debug_info['warp_dict_error']}\n"
#                     if 'memory_warp_error' in debug_info:
#                         debug_text += f"Memory Warp Error: {debug_info['memory_warp_error']}\n"
                
#                 # Minimap debug info
#                 debug_text += f"\n=== MINIMAP INFO ===\n"
#                 debug_text += f"Array Shape: {warp_obs_data.shape if hasattr(warp_obs_data, 'shape') else 'Unknown'}\n"
#                 debug_text += f"Total Warps: {total_warps}\n"
#                 debug_text += f"Player Position: ({center_col}, {center_row})\n\n"
                
#                 if warp_tiles:
#                     debug_text += "Minimap Warp Locations:\n"
#                     for warp in warp_tiles[:10]:  # Show first 10 warps
#                         debug_text += f"  {warp}\n"
#                     if len(warp_tiles) > 10:
#                         debug_text += f"  ... and {len(warp_tiles) - 10} more\n"
#                 else:
#                     debug_text += "No warps in minimap\n"
            
#             # Update debug text area
#             warp_debug_text.config(state='normal')
#             warp_debug_text.delete(1.0, tk.END)
#             warp_debug_text.insert(tk.END, debug_text)
#             warp_debug_text.config(state='disabled')
            
#         except Exception as e:
#             error_msg = f"Error drawing warp minimap: {e}"
#             print(error_msg)
#             warp_minimap_canvas.delete("all")
#             warp_minimap_canvas.create_text(150, 135, text=f"Error: {e}", fill="red", font=("Arial", 10))
            
#             # Update debug text with error
#             warp_debug_text.config(state='normal')
#             warp_debug_text.delete(1.0, tk.END)
#             warp_debug_text.insert(tk.END, f"Error: {error_msg}")
#             warp_debug_text.config(state='disabled')

#     def poll():
#         """Poll for status updates from the main thread"""
#         try:
#             while True:
#                 try:
#                     item_id, status_data = status_queue.get_nowait()
                    
#                     if item_id == '__warp_minimap__':
#                         # Handle warp minimap observation data
#                         draw_warp_minimap(status_data)
                    
#                     elif item_id == '__location__':
#                         # Receive LOCAL coordinates from play.py: (local_x, local_y, map_id, map_name)
#                         local_x, local_y, map_id, map_name_from_q = status_data
                        
#                         # Update UI labels with local coordinates
#                         env_labels['direct_map_name'].config(text=f"Direct Map: {map_name_from_q}")
#                         env_labels['direct_map_id'].config(text=f"Direct Map ID: {map_id}")
#                         env_labels['direct_local_coords'].config(text=f"Direct Local: ({local_x}, {local_y})")
                        
#                         # Convert to global coordinates using EXACT logic from a_map_practice.py
#                         # CRITICAL: respect the x/y order exactly as in a_map_practice.py
#                         global_y, global_x = local_to_global(local_y, local_x, map_id)
                        
#                         # Update UI labels with converted global coordinates
#                         env_labels['direct_global_coords'].config(text=f"Direct Global: ({global_x}, {global_y})")
#                         env_labels['player_global_coords_map'].config(text=f"Player Global (for map): ({global_x}, {global_y})")

#                         # Get player facing direction
#                         facing_text_val = env_labels.get('direct_facing_direction', tk.Label(None, text="Facing: Down")).cget('text')
#                         facing = facing_text_val.split(': ')[1] if ': ' in facing_text_val else 'Down'
                        
#                         # NEW: Update traversal tracking before drawing
#                         update_coordinate_traversal(global_x, global_y)
                        
#                         # PERFORMANCE: Use optimized map drawing
#                         success = draw_map_optimized(local_x, local_y, map_id, map_name_from_q, facing)
                        
#                         if success:
#                             # Convert to global coordinates for debug info
#                             global_y, global_x = local_to_global(local_y, local_x, map_id)
#                             TILE_SIZE = 16
#                             pixel_x = global_x * TILE_SIZE # + TILE_SIZE // 2
#                             pixel_y = global_y * TILE_SIZE # + TILE_SIZE // 2
#                             canvas_w = map_canvas.winfo_width()
#                             canvas_h = map_canvas.winfo_height()
#                             if canvas_w == 1 or canvas_h == 1:
#                                 canvas_w = int(map_canvas.cget('width'))
#                                 canvas_h = int(map_canvas.cget('height'))
#                             center_x = canvas_w // 2
#                             center_y = canvas_h // 2
#                             dx = center_x - pixel_x
#                             dy = center_y - pixel_y
                            
#                             # Add detailed coordinate debugging showing the conversion process
#                             map_calc_text = (f"Map ID: {map_id} ({map_name_from_q})\n"
#                                              f"Local Coords: ({local_x}, {local_y})\n"
#                                              f"Global Coords: ({global_x}, {global_y})\n"
#                                              f"Pixel Coords: ({pixel_x}, {pixel_y})\n"
#                                              f"Canvas Size: {canvas_w}x{canvas_h}\n"
#                                              f"Map Offset: ({dx}, {dy})\n"
#                                              f"Optimized Rendering: YES")
#                             env_labels['map_calc_details'].config(text=map_calc_text)
                        
#                         # Print debug info to console for troubleshooting
#                         # print(f"[UI DEBUG] Map: {map_name_from_q} (ID: {map_id}), Local: ({local_x}, {local_y}), Global: ({global_x}, {global_y}), Pixel: ({pixel_x}, {pixel_y})")
                        
#                         # Update tile coordinates display
#                         env_labels['map_tile_coords_display'].config(text=f"Using a_map_practice.py logic\nLocal->Global: ({local_x},{local_y})->({global_x},{global_y})")
                    
#                     elif item_id == '__emulator_screen__':
#                         pixel_data_bytes, img_width, img_height, img_mode = status_data
#                         try:
#                             emu_img = Image.frombytes(img_mode, (img_width, img_height), pixel_data_bytes)
#                             current_canvas_width = emulator_canvas.winfo_width()
#                             current_canvas_height = emulator_canvas.winfo_height()
#                             target_width = int(emulator_canvas.cget('width')) if current_canvas_width == 1 else current_canvas_width
#                             target_height = int(emulator_canvas.cget('height')) if current_canvas_height == 1 else current_canvas_height

#                             if img_width != target_width or img_height != target_height:
#                                 emu_img = emu_img.resize((target_width, target_height), Image.Resampling.NEAREST)
                            
#                             emulator_canvas.emulator_photo_ref = ImageTk.PhotoImage(emu_img)
#                             emulator_canvas.delete("all")  # Clear previous image
#                             emulator_canvas.create_image(0, 0, image=emulator_canvas.emulator_photo_ref, anchor='nw')
#                         except Exception as e:
#                             print(f"Error processing emulator screen: {e}")

#                     elif item_id == '__collision_overlay_screen__':
#                         pixel_data_bytes, img_width, img_height, img_mode = status_data
#                         try:
#                             collision_img = Image.frombytes(img_mode, (img_width, img_height), pixel_data_bytes)
#                             current_canvas_width = collision_canvas.winfo_width()
#                             current_canvas_height = collision_canvas.winfo_height()
#                             target_width = int(collision_canvas.cget('width')) if current_canvas_width == 1 else current_canvas_width
#                             target_height = int(collision_canvas.cget('height')) if current_canvas_height == 1 else current_canvas_height

#                             if img_width != target_width or img_height != target_height:
#                                 collision_img = collision_img.resize((target_width, target_height), Image.Resampling.NEAREST)
                            
#                             collision_canvas.collision_photo_ref = ImageTk.PhotoImage(collision_img)
#                             collision_canvas.delete("all")  # Clear previous image
#                             collision_canvas.create_image(0, 0, image=collision_canvas.collision_photo_ref, anchor='nw')
#                         except Exception as e:
#                             print(f"Error processing collision overlay screen: {e}")

#                     elif item_id == '__local_location__':
#                         # Note: Local coordinates are now processed in the main __location__ handler
#                         # This handler is kept for backward compatibility but does nothing
#                         pass
#                     elif item_id == '__current_quest__':
#                         env_labels['current_quest_disp'].config(text=f"Current Quest ID: {status_data}")
#                         # NEW: Update current quest ID for enhanced visualization
#                         old_quest_id = map_canvas.current_quest_id
#                         map_canvas.current_quest_id = status_data
                        
#                         # Force redraw coordinates if quest changed
#                         if old_quest_id != status_data:
#                             map_canvas.quest_paths_drawn = False
#                         # Highlight current quest in tree
#                         for child_iid in tree.get_children(): tree.item(child_iid, tags=()) # Clear old tags
#                         if status_data and tree.exists(str(status_data)):
#                             tree.item(str(status_data), tags=('active_quest',))
#                             tree.see(str(status_data))


#                     elif item_id == '__dialog__':
#                         env_labels['dialog'].config(text=f"Dialog: {status_data if status_data else 'None'}")
#                     elif item_id == '__nav_status__':
#                         env_labels['nav_status'].config(text=f"Navigation: {status_data}")
#                     elif item_id == '__run_dir__':
#                         env_labels['run_dir_disp'].config(text=f"Run Dir: {status_data}")
#                     elif item_id == '__total_steps__':
#                         env_labels['total_steps_disp'].config(text=f"Total Steps: {status_data}")
#                     elif item_id == '__facing_direction__':
#                         env_labels['direct_facing_direction'].config(text=f"Direct Facing: {status_data}")
#                         # PERFORMANCE: Only update sprite facing direction (optimized)
#                         # Only redraw if we have valid location data
#                         if ('direct_local_coords' in env_labels and 
#                             env_labels['direct_local_coords'].cget('text') != "Direct Local: N/A"):
#                             try:
#                                 # Get current location data from labels
#                                 local_coords_text = env_labels['direct_local_coords'].cget('text')
#                                 local_x_str, local_y_str = local_coords_text.split('(')[1].split(')')[0].split(', ')
#                                 local_x, local_y = int(local_x_str), int(local_y_str)
                                
#                                 map_id_text = env_labels['direct_map_id'].cget('text')
#                                 map_id = int(map_id_text.split(': ')[1])
                                
#                                 map_name_text = env_labels['direct_map_name'].cget('text')
#                                 map_name_from_q = map_name_text.split(': ')[1]
                                
#                                 # PERFORMANCE: Use optimized drawing for facing direction change
#                                 facing = status_data  # Use the new facing direction directly
#                                 draw_map_optimized(local_x, local_y, map_id, map_name_from_q, facing)
                                
#                             except (ValueError, IndexError, KeyError) as e:
#                                 # If parsing fails, just update the label without redrawing
#                                 print(f"UI: Could not redraw map on facing change: {e}")
#                                 pass
#                     # elif item_id == '__path_index__': # Path drawing simplified/removed
#                     #     path_state['path_index'] = status_data; draw_paths()
#                     # elif item_id == '__path_length__': # Path drawing simplified/removed
#                     #     draw_paths()
#                     # elif item_id == '__env_path__': # Path drawing simplified/removed
#                     #     path_state['env_coords'] = status_data; draw_paths()
#                     # elif item_id == '__nav_path__': # Path drawing simplified/removed
#                     #     path_state['nav_coords'] = status_data; draw_paths()
#                     elif item_id == '__status__':
#                         env_labels['status'].config(text=f"Status: {status_data}")
#                     elif item_id == '__last_action__':
#                         env_labels['last_action_disp'].config(text=f"Last Action: {status_data}")
#                     elif item_id == '__action_source__':
#                         env_labels['action_source_disp'].config(text=f"Action Source: {status_data}")
#                     elif item_id == '__in_battle__':
#                         env_labels['in_battle'].config(text=f"In Battle: {status_data}")
#                     elif item_id == '__fps__':
#                         env_labels['fps_disp'].config(text=f"FPS: {status_data}")
                    
#                     # New handlers for detailed quest/trigger updates
#                     elif item_id.startswith('__trigger_debug__') or item_id.startswith('__quest_status_detailed__'):
#                         # Expected status_data: { 'id': original_id, 'status': bool, 'values_str': "...", 'debug_str': "..." }
#                         original_id_str = str(status_data.get('id'))
#                         is_completed = status_data.get('status', False)
#                         criteria_val_str = status_data.get('values_str', "N/A")
#                         debug_calc_str = status_data.get('debug_str', "N/A")

#                         if tree.exists(original_id_str):
#                             # Ensure current_values has enough elements for new columns
#                             current_tree_values_tuple = tree.item(original_id_str, 'values')
#                             current_values_list = list(current_tree_values_tuple) if current_tree_values_tuple else []

#                             while len(current_values_list) < 3: # status, criteria_values, debug_calcs
#                                 current_values_list.append("N/A") 

#                             current_values_list[0] = 'Complete' if is_completed else 'Pending'
#                             current_values_list[1] = criteria_val_str
#                             current_values_list[2] = debug_calc_str
#                             tree.item(original_id_str, values=tuple(current_values_list), tags=('done' if is_completed else 'pending', 'debug_text'))
#                             if is_completed and tree.parent(original_id_str) == "": # If it's a main quest
#                                 tree.item(original_id_str, open=False)

#                     else: # Original quest/trigger simple status update (fallback)
#                         original_id_str = str(item_id) # item_id is the quest/trigger ID directly
#                         is_completed = bool(status_data) # status_data is True/False

#                         if tree.exists(original_id_str):
#                             current_tree_values_tuple = tree.item(original_id_str, 'values')
#                             current_values_list = list(current_tree_values_tuple) if current_tree_values_tuple else []
                            
#                             while len(current_values_list) < 3:
#                                 current_values_list.append("N/A (simple)")

#                             current_values_list[0] = 'Complete' if is_completed else 'Pending'
#                             # For simple updates, columns 1 and 2 might just keep their "N/A (simple)"
#                             tree.item(original_id_str, values=tuple(current_values_list), tags=('done' if is_completed else 'pending',))
#                             if is_completed and tree.parent(original_id_str) == "": # Main quest
#                                 tree.item(original_id_str, open=False)
#                 except queue.Empty:
#                     break
#         except Exception as e:
#             print(f"Error in quest UI poll: {e}")
        
#         # Schedule next poll
#         root.after(10, poll)

#     # PERFORMANCE: Optimized map drawing function
#     def draw_map_optimized(local_x, local_y, map_id, map_name_from_q, facing):
#         """Optimized map drawing that minimizes expensive operations"""
#         try:
#             # Convert to global coordinates
#             global_y, global_x = local_to_global(local_y, local_x, map_id)
            
#             # Get canvas size
#             canvas_w = map_canvas.winfo_width()
#             canvas_h = map_canvas.winfo_height()
#             if canvas_w == 1 or canvas_h == 1:
#                 canvas_w = int(map_canvas.cget('width'))
#                 canvas_h = int(map_canvas.cget('height'))

#             # Calculate positions
#             TILE_SIZE = 16
#             pixel_x = global_x * TILE_SIZE + TILE_SIZE // 2
#             pixel_y = global_y * TILE_SIZE + TILE_SIZE // 2
#             center_x = canvas_w // 2
#             center_y = canvas_h // 2
#             dx = center_x - pixel_x
#             dy = center_y - pixel_y
            
#             # OPTIMIZATION 1: Only create map PhotoImage once and cache it
#             if map_canvas.cached_map_photo is None:
#                 map_canvas.cached_map_photo = ImageTk.PhotoImage(map_canvas.full_map_img)
            
#             # OPTIMIZATION 2: Only move map if position actually changed
#             current_offset = (dx, dy)
#             if map_canvas.last_map_offset != current_offset or map_canvas.map_image_id is None:
#                 # Remove old map
#                 if map_canvas.map_image_id:
#                     map_canvas.delete(map_canvas.map_image_id)
                
#                 # Draw map at new position
#                 map_canvas.map_image_id = map_canvas.create_image(dx, dy, image=map_canvas.cached_map_photo, anchor='nw')
#                 map_canvas.last_map_offset = current_offset
                
#                 # OPTIMIZATION 3: Enhanced quest coordinate visualization with traversal tracking
#                 if not map_canvas.quest_paths_drawn or map_canvas.last_map_offset != current_offset:
#                     print(f"MAP DRAW: DIAGNOSTIC - Starting coordinate drawing...")
#                     print(f"MAP DRAW: DIAGNOSTIC - Total coordinates available: {len(map_canvas.all_quest_coordinates)}")
#                     print(f"MAP DRAW: DIAGNOSTIC - Current quest: {map_canvas.current_quest_id}")
#                     print(f"MAP DRAW: DIAGNOSTIC - Map offset: dx={dx}, dy={dy}")
                    
#                     # Remove old quest paths
#                     map_canvas.delete("quest_paths")
#                     map_canvas.delete("quest_coordinates")
                    
#                     # Draw ALL quest coordinates with enhanced visualization
#                     current_quest = map_canvas.current_quest_id
#                     coordinates_drawn = 0
#                     coordinates_on_screen = 0
                    
#                     # First pass: Draw all non-current quest coordinates (background layer)
#                     for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
#                         if current_quest is not None and quest_id == current_quest:
#                             continue  # Skip current quest coordinates for now
                            
#                         # Convert global coordinates to pixel coordinates (no scaling - pixel perfect)
#                         pixel_x_coord = coord_gx * TILE_SIZE + TILE_SIZE // 2  # Center of tile
#                         pixel_y_coord = coord_gy * TILE_SIZE + TILE_SIZE // 2  # Center of tile
#                         coord_x = pixel_x_coord + dx
#                         coord_y = pixel_y_coord + dy
                        
#                         # Check if coordinate is visible on screen
#                         if 0 <= coord_x <= canvas_w and 0 <= coord_y <= canvas_h:
#                             coordinates_on_screen += 1
                        
#                         # Check if coordinate has been traversed
#                         coord_pos = (coord_gy, coord_gx)
#                         is_traversed = coord_pos in map_canvas.traversed_coordinates
                        
#                         # Base color for non-current quest coordinates (semi-transparent blue)
#                         if is_traversed:
#                             # Traversed coordinates: solid color with higher opacity
#                             fill_color = "#0066CC"  # Bright blue for traversed
#                             outline_color = "#003399"  # Darker blue outline
#                             size = 6  # MUCH larger for visibility
#                         else:
#                             # Untraversed coordinates: lighter, more transparent
#                             fill_color = "#6699FF"  # Light blue for untraversed
#                             outline_color = "#4477CC"  # Medium blue outline
#                             size = 4  # MUCH larger for visibility
                            
#                         # Draw coordinate marker
#                         map_canvas.create_rectangle(
#                             coord_x - size, coord_y - size, 
#                             coord_x + size, coord_y + size,
#                             fill=fill_color, outline=outline_color, width=1,
#                             tags="quest_coordinates"
#                         )
#                         coordinates_drawn += 1
                    
#                     # Second pass: Draw current quest coordinates (foreground layer)
#                     current_quest_drawn = 0
#                     if current_quest is not None:
#                         for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
#                             if quest_id != current_quest:
#                                 continue
                                
#                             # Convert global coordinates to pixel coordinates (no scaling - pixel perfect)
#                             pixel_x_coord = coord_gx * TILE_SIZE + TILE_SIZE // 2  # Center of tile
#                             pixel_y_coord = coord_gy * TILE_SIZE + TILE_SIZE // 2  # Center of tile
#                             coord_x = pixel_x_coord + dx
#                             coord_y = pixel_y_coord + dy
                            
#                             # Check if coordinate is visible on screen
#                             if 0 <= coord_x <= canvas_w and 0 <= coord_y <= canvas_h:
#                                 coordinates_on_screen += 1
                            
#                             # Check if coordinate has been traversed
#                             coord_pos = (coord_gy, coord_gx)
#                             is_traversed = coord_pos in map_canvas.traversed_coordinates
                            
#                             # Current quest coordinates: bright, distinct colors
#                             if is_traversed:
#                                 # Current quest traversed: bright green
#                                 fill_color = "#00FF00"  # Bright green for current quest traversed
#                                 outline_color = "#00AA00"  # Darker green outline
#                                 size = 8  # MUCH larger for current quest traversed
#                             else:
#                                 # Current quest untraversed: bright orange/yellow
#                                 fill_color = "#FFAA00"  # Orange for current quest untraversed
#                                 outline_color = "#CC7700"  # Darker orange outline
#                                 size = 6  # MUCH larger for current quest untraversed
                                
#                             # Draw current quest coordinate marker (more prominent)
#                             map_canvas.create_rectangle(
#                                 coord_x - size, coord_y - size, 
#                                 coord_x + size, coord_y + size,
#                                 fill=fill_color, outline=outline_color, width=2,
#                                 tags="quest_coordinates"
#                             )
                            
#                             # Add a small inner highlight for current quest coordinates
#                             inner_size = max(1, size - 1)
#                             highlight_color = "#FFFFFF" if is_traversed else "#FFDDAA"
#                             map_canvas.create_rectangle(
#                                 coord_x - inner_size + 1, coord_y - inner_size + 1,
#                                 coord_x + inner_size - 1, coord_y + inner_size - 1,
#                                 fill="", outline=highlight_color, width=1,
#                                 tags="quest_coordinates"
#                             )
#                             current_quest_drawn += 1
                    
#                     print(f"MAP DRAW: DIAGNOSTIC - Drew {coordinates_drawn} background coordinates")
#                     print(f"MAP DRAW: DIAGNOSTIC - Drew {current_quest_drawn} current quest coordinates")
#                     print(f"MAP DRAW: DIAGNOSTIC - Total on screen: {coordinates_on_screen}")
                    
#                     # DIAGNOSTIC: Draw some test markers to verify drawing works
#                     if coordinates_drawn == 0 and current_quest_drawn == 0:
#                         print(f"MAP DRAW: DIAGNOSTIC - No coordinates drawn! Drawing test markers...")
#                         # Draw test markers at screen corners to verify canvas drawing works
#                         test_size = 10
#                         test_positions = [
#                             (20, 20, "red"),      # Top-left
#                             (canvas_w-20, 20, "green"),    # Top-right  
#                             (20, canvas_h-20, "blue"),     # Bottom-left
#                             (canvas_w-20, canvas_h-20, "yellow"), # Bottom-right
#                             (canvas_w//2, canvas_h//2, "magenta")  # Center
#                         ]
#                         for test_x, test_y, color in test_positions:
#                             map_canvas.create_rectangle(
#                                 test_x - test_size, test_y - test_size,
#                                 test_x + test_size, test_y + test_size,
#                                 fill=color, outline="black", width=2,
#                                 tags="test_markers"
#                             )
#                             print(f"MAP DRAW: DIAGNOSTIC - Drew test marker at ({test_x}, {test_y}) in {color}")
#                     map_canvas.quest_paths_drawn = True
            
#             # OPTIMIZATION 4: Cache sprite PhotoImages by facing direction
#             if facing not in map_canvas.cached_sprite_photos:
#                 # Sprite indices
#                 SPRITE_IDX_DOWN = 0
#                 SPRITE_IDX_UP = 3
#                 SPRITE_IDX_LEFT = 6
#                 SPRITE_IDX_RIGHT = 8
                
#                 idx = SPRITE_IDX_DOWN
#                 if facing == 'Down': idx = SPRITE_IDX_DOWN
#                 elif facing == 'Up': idx = SPRITE_IDX_UP
#                 elif facing == 'Left': idx = SPRITE_IDX_LEFT
#                 elif facing == 'Right': idx = SPRITE_IDX_RIGHT
                
#                 if not (0 <= idx < len(map_canvas.sprite_frames)): idx = SPRITE_IDX_DOWN
                
#                 sprite_on_map = map_canvas.sprite_frames[idx]
#                 map_canvas.cached_sprite_photos[facing] = ImageTk.PhotoImage(sprite_on_map)
            
#             # OPTIMIZATION 5: Only update sprite position, don't recreate
#             sprite_photo = map_canvas.cached_sprite_photos[facing]
#             player_draw_x = center_x - 8  # sprite is 16x16, so center is -8
#             player_draw_y = center_y - 8
            
#             # Remove old sprite and draw new one
#             if map_canvas.sprite_image_id:
#                 map_canvas.delete(map_canvas.sprite_image_id)
            
#             map_canvas.sprite_image_id = map_canvas.create_image(player_draw_x, player_draw_y, 
#                                                                image=sprite_photo, anchor='nw')
            
#             # Ensure correct layer ordering: map -> coordinates -> sprite
#             if hasattr(map_canvas, 'map_image_id') and map_canvas.map_image_id:
#                 map_canvas.tag_lower(map_canvas.map_image_id)  # Map at bottom
#             map_canvas.tag_raise("quest_coordinates")  # Coordinates above map
#             map_canvas.tag_raise(map_canvas.sprite_image_id)  # Sprite on top
            
#             # Update debug info
#             debug_text = (f"Local: ({local_x}, {local_y}) -> Global: ({global_x}, {global_y}) -> "
#                          f"Pixel: ({pixel_x}, {pixel_y}) -> Center: ({center_x}, {center_y}), "
#                          f"Facing: {facing}")
#             env_labels['map_sprite_info'].config(text=debug_text)
            
#             return True  # Success
            
#         except Exception as e:
#             print(f"UI: Error in optimized map drawing: {e}")
#             return False

#     # NEW: Function to update coordinate traversal tracking
#     def update_coordinate_traversal(player_gx, player_gy):
#         """Update traversed coordinates based on player position"""
#         try:
#             player_pos = (player_gy, player_gx)  # Note: coordinate order is (gy, gx)
#             traversal_distance = map_canvas.coordinate_traversal_distance
            
#             # Check all quest coordinates for traversal
#             for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
#                 coord_pos = (coord_gy, coord_gx)
#                 # Calculate Manhattan distance
#                 distance = abs(player_pos[0] - coord_pos[0]) + abs(player_pos[1] - coord_pos[1])
                
#                 if distance <= traversal_distance:
#                     # Mark as traversed
#                     map_canvas.traversed_coordinates.add(coord_pos)
            
#             # Update statistics labels
#             total_coords = len(map_canvas.all_quest_coordinates)
#             traversed_count = len(map_canvas.traversed_coordinates)
#             env_labels['total_coordinates'].config(text=f"Total Coordinates: {total_coords}")
#             env_labels['traversed_count'].config(text=f"Traversed: {traversed_count} ({traversed_count/total_coords*100:.1f}%)" if total_coords > 0 else "Traversed: 0")
            
#             # Current quest statistics
#             current_quest = map_canvas.current_quest_id
#             if current_quest is not None:
#                 # Count current quest coordinates
#                 current_quest_coords = [(gy, gx) for gy, gx, qid in map_canvas.all_quest_coordinates if qid == current_quest]
#                 current_quest_traversed = [coord for coord in current_quest_coords if coord in map_canvas.traversed_coordinates]
                
#                 env_labels['current_quest_stats'].config(
#                     text=f"Current Quest {current_quest}: {len(current_quest_traversed)}/{len(current_quest_coords)} traversed ({len(current_quest_traversed)/len(current_quest_coords)*100:.1f}%)" 
#                     if len(current_quest_coords) > 0 else f"Current Quest {current_quest}: No coordinates"
#                 )
#             else:
#                 env_labels['current_quest_stats'].config(text="Current Quest: N/A")
                
#         except Exception as e:
#             print(f"Error updating coordinate traversal: {e}")
    
#     # Bottom status bar
#     status_frame = tk.Frame(root)
#     status_frame.pack(side='bottom', fill='x', pady=5)
    
#     env_labels['status'] = tk.Label(status_frame, text="Status: Initializing...", relief='sunken', anchor='w')
#     env_labels['status'].pack(fill='x', padx=5)

#     # Start polling - schedule instead of calling directly
#     root.after(100, poll)
    
#     # Snap to current quest initially
#     snap_to_current()
    
#     # Start the UI main loop
#     root.mainloop() 

# # Helper function to display sample tile coordinates for debugging
# def display_sample_tile_coords(canvas, env_labels, current_map_id_str, map_data_dict, png_w, png_h, px_per_tile_x, px_per_tile_y):
#     canvas_width = canvas.winfo_width()
#     canvas_height = canvas.winfo_height()
#     if canvas_width <= 1 or canvas_height <= 1: return

#     current_map_info = map_data_dict.get(current_map_id_str)
#     if not current_map_info: return

#     map_origin_glob_x_tiles = current_map_info['coordinates'][0] # width (x)
#     map_origin_glob_y_tiles = current_map_info['coordinates'][1] # height (y)
#     map_tile_width  = current_map_info['tileSize'][0]   # width (x)
#     map_tile_height = current_map_info['tileSize'][1]   # height (y)

#     tile_coord_debug_text = []

#     # Sample points: corners and center of the current map
#     # (local_tile_x, local_tile_y, label)
#     local_tile_samples = [
#         (0, 0, "TL"), 
#         (map_tile_width, 0, "TR"),
#         (0, map_tile_height, "BL"),
#         (map_tile_width, map_tile_height, "BR"),
#         (map_tile_width // 2, map_tile_height // 2, "C")
#     ]

#     canvas.delete("debug_tile_coords") # Clear previous debug texts/shapes

#     for loc_x, loc_y, label in local_tile_samples:
#         if loc_x < 0 or loc_y < 0 or loc_x >= map_tile_width or loc_y >= map_tile_height:
#             continue # Skip if sample is outside map bounds (e.g. 1x1 map)

#         glob_tile_x = map_origin_glob_x_tiles + loc_x # width (x)
#         glob_tile_y = map_origin_glob_y_tiles + loc_y # height (y)

#         # Pixel pos on PNG (center of the tile)
#         pixel_on_png_x = glob_tile_x * px_per_tile_x + px_per_tile_x // 2
#         pixel_on_png_y = glob_tile_y * px_per_tile_y + px_per_tile_y // 2
#         # Scaled to canvas
#         canvas_x = (pixel_on_png_x / png_w) * canvas_width
#         canvas_y = (pixel_on_png_y / png_h) * canvas_height
        
#         # Draw a small circle and text on the map canvas
#         radius = 2 # Smaller radius
#         fill_color = "magenta"
#         text_color = "black" # Better contrast
#         canvas.create_oval(canvas_x - radius, canvas_y - radius, 
#                              canvas_x + radius, canvas_y + radius, 
#                              fill=fill_color, outline=text_color, tags="debug_tile_coords")
#         # Adjusted text position for clarity
#         canvas.create_text(canvas_x, canvas_y - (radius + 4), 
#                             text=f"{label}({glob_tile_x},{glob_tile_y})", 
#                             fill=text_color, anchor="s", font=("TkDefaultFont", 7), tags="debug_tile_coords")
        
#         tile_coord_debug_text.append(f"{label}: L({loc_x},{loc_y}) -> G({glob_tile_x},{glob_tile_y}) -> PNG({pixel_on_png_x:.1f},{pixel_on_png_y:.1f}) -> CV({canvas_x:.1f},{canvas_y:.1f})")

#     if 'map_tile_coords_display' in env_labels:
#         env_labels['map_tile_coords_display'].config(text="\n".join(tile_coord_debug_text) or "No samples calculated.")

# def setup_treeview_style_and_tags(tree):
#     style = ttk.Style()
#     # Configure Treeview style for padding and font if not done globally
#     # style.configure("Treeview", rowheight=25, font=('Arial', 10)) # Example
#     # style.configure("Treeview.Heading", font=('Arial', 10, 'bold')) # Example

#     # Define tags for visual differentiation of rows
#     tree.tag_configure('done', foreground='green')
#     tree.tag_configure('pending', foreground='red')
#     tree.tag_configure('active_quest', background='lightyellow') # For the main quest row
#     tree.tag_configure('active_trigger', background='lightblue') # For the specific active trigger
#     tree.tag_configure('error', foreground='orange red')
#     tree.tag_configure('debug_text', font=(tree.tk.call("font", "actual", style.lookup("Treeview", "font"))[0], 8)) # Smaller font for debug

#     # You can add more tags here if needed, for example:
#     # tree.tag_configure('type_event', foreground='blue')
#     # tree.tag_configure('type_item', foreground='purple')

#     # ... (keep the existing code)
#     # ... (keep the existing code)
#     # ... (keep the existing setup_treeview_style_and_tags function)