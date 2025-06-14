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
    # Support both tuple (legacy) and dict (current) formats
    if isinstance(status_data, dict):
        local_x = status_data.get('x', 0)
        local_y = status_data.get('y', 0)
        map_id = status_data.get('map_id') or status_data.get('mapId') or 0
        map_name = status_data.get('map_name') or status_data.get('mapName') or 'Unknown'
    else:
        # Legacy tuple/list format: (x, y, map_id, map_name)
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
