"""
Rendering and drawing logic for quest UI
Handles map rendering, sprite drawing, and coordinate conversions
"""
import tkinter as tk
from pathlib import Path
import json
import colorsys
import os
import time
try:
    from PIL import Image, ImageTk, ImageDraw
except ImportError:
    import Image, ImageTk, ImageDraw

# Global variable to store map data
MAP_DATA = {}
MAP_DATA_INT_KEYS = {}

# Constants for coordinate conversion
PAD = 20  # Padding used in environment coordinate system
TILE_SIZE = 16

def load_map_data():
    """Load map data from JSON file"""
    global MAP_DATA
    try:
        script_dir = Path(__file__).parent
        map_data_path = script_dir.parent / "environment" / "data" / "environment_data" / "map_data.json"
        with open(map_data_path, 'r') as f:
            data = json.load(f)
            MAP_DATA = {region['id']: region for region in data.get('regions', [])}
            print(f"Render: Successfully loaded map_data.json. {len(MAP_DATA)} regions found.")
    except Exception as e:
        print(f"Render: ERROR - Could not load map_data.json: {e}")
        MAP_DATA = {}

def prepare_map_data_for_conversion():
    """Prepare map data with integer keys for faster lookup"""
    global MAP_DATA_INT_KEYS
    MAP_DATA_INT_KEYS = {}
    for str_id, region_data in MAP_DATA.items():
        try:
            int_id = int(str_id)
            MAP_DATA_INT_KEYS[int_id] = region_data
        except ValueError:
            print(f"Render: Warning - Could not convert map id '{str_id}' to integer")

def local_to_global(r, c, map_n):
    """Convert local coordinates to global coordinates
    
    This matches the environment's coordinate system exactly.
    
    Args:
        r: Local row (y) coordinate
        c: Local column (x) coordinate  
        map_n: Map ID number
        
    Returns:
        (global_y, global_x) tuple
    """
    try:
        map_coords = MAP_DATA_INT_KEYS[map_n]['coordinates']
        map_x = map_coords[0]
        map_y = map_coords[1]
        
        # Match environment's calculation exactly
        global_x = c + map_x - PAD
        global_y = r + map_y - PAD
        
        return global_y, global_x
    except KeyError:
        print(f'Render: Map id {map_n} not found in map_data.json.')
        return 222 + PAD, 218 + PAD

def initialize_map_canvas(canvas):
    """Initialize map canvas with required attributes"""
    canvas.full_map_img = None
    canvas.sprite_frames = []
    canvas.map_photo = None
    canvas.sprite_photo = None
    # Map dimensions: 436 wide x 444 tall (in tiles)
    canvas.kanto_map_tile_width = 436
    canvas.kanto_map_tile_height = 444
    canvas.initial_player_dot_drawn = False
    canvas.all_quest_coordinates = []
    canvas.traversed_coordinates = set()
    canvas.current_quest_id = None
    canvas.coordinate_traversal_distance = 3  # Default traversal distance
    canvas.quest_paths_drawn = False
    canvas.cached_sprite_photos = {}
    canvas.sprite_image_id = None
    canvas.cached_map_photo = None
    canvas.last_map_offset = (0, 0)
    canvas.map_image_id = None
    canvas.quest_coords = {}
    canvas.quest_colors = {}

def load_resources(map_canvas):
    """Load image resources"""
    try:
        base_dir = Path(__file__).resolve().parent.parent
        env_data_dir = base_dir / "environment" / "data" / "environment_data"
        
        # Load map
        full_map_img = Image.open(env_data_dir / "full_kanto_map.png")
        map_canvas.full_map_img = full_map_img
        
        # Load sprite frames
        spritesheet_img = Image.open(env_data_dir / "pokemon_red_player_spritesheet.png")
        sprite_frames = []
        for i in range(10):
            x = i * 17
            frame = spritesheet_img.crop((x, 0, x + 16, 16)).convert("RGBA")
            datas = frame.getdata()
            new_data = []
            for item in datas:
                if item[:3] in [(255,165,0), (255,184,77)]:
                    new_data.append((255,255,255,0))
                else:
                    new_data.append(item)
            frame.putdata(new_data)
            sprite_frames.append(frame)
        map_canvas.sprite_frames = sprite_frames
        
        # Load quest coordinates
        load_quest_coordinates(map_canvas)
        
    except Exception as e:
        print(f"Render: Error loading resources: {e}")

def load_quest_coordinates(map_canvas):
    """Load quest coordinate data"""
    base_dir = Path(__file__).resolve().parent.parent
    combined_coords_file = base_dir / "environment" / "environment_helpers" / "quest_paths" / "combined_quest_coordinates_continuous.json"
    
    # Try multiple possible locations
    possible_files = [
        combined_coords_file,
        base_dir / "combined_quest_coordinates_continuous.json",
        base_dir / "environment" / "combined_quest_coordinates_continuous.json"
    ]
    
    for file_path in possible_files:
        if file_path.exists():
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                
                quest_start_indices = data.get('quest_start_indices', {})
                all_coordinates = data.get('coordinates', [])
                
                # Process coordinates
                quest_ids = sorted(quest_start_indices.keys(), key=lambda x: int(x))
                
                for idx, qid in enumerate(quest_ids):
                    start_idx = quest_start_indices[qid]
                    end_idx = quest_start_indices.get(quest_ids[idx + 1] if idx + 1 < len(quest_ids) else None, 
                                                    len(all_coordinates))
                    
                    quest_coords_list = []
                    for coord_data in all_coordinates[start_idx:end_idx]:
                        if len(coord_data) >= 2:
                            gy, gx = coord_data[0], coord_data[1]
                            quest_coords_list.append((gy, gx))
                            map_canvas.all_quest_coordinates.append((gy, gx, int(qid)))
                    
                    map_canvas.quest_coords[qid.zfill(3)] = quest_coords_list
                    
                    # Generate color
                    h = idx / len(quest_ids)
                    r, g, b = colorsys.hsv_to_rgb(h, 0.7, 0.9)
                    map_canvas.quest_colors[qid.zfill(3)] = f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'
                
                print(f"Render: Loaded {len(map_canvas.all_quest_coordinates)} quest coordinates")
                return
                
            except Exception as e:
                print(f"Render: Error loading quest coordinates from {file_path}: {e}")
                continue

def draw_map_optimized(map_canvas, local_x, local_y, map_id, map_name, facing, env_labels):
    """Optimized map drawing with correct coordinate handling
    
    The key insight is that the map PNG image appears to already have
    padding built into it, so we render using global coordinates directly.
    """
    try:
        # Convert local coordinates to global (with padding)
        global_y, global_x = local_to_global(local_y, local_x, map_id)
        
        # Get canvas size
        canvas_w = map_canvas.winfo_width() or int(map_canvas.cget('width'))
        canvas_h = map_canvas.winfo_height() or int(map_canvas.cget('height'))
        
        # Calculate pixel positions
        # Use global coordinates directly since map image includes padding
        pixel_x = global_x * TILE_SIZE
        pixel_y = global_y * TILE_SIZE
        
        # Center of canvas
        center_x = canvas_w // 2
        center_y = canvas_h // 2
        
        # Offset to center player on canvas
        # Account for sprite being centered in its tile
        dx = center_x - pixel_x - TILE_SIZE // 2
        dy = center_y - pixel_y - TILE_SIZE // 2
        
        # Update map position
        if map_canvas.cached_map_photo is None:
            map_canvas.cached_map_photo = ImageTk.PhotoImage(map_canvas.full_map_img)
        
        current_offset = (dx, dy)
        if map_canvas.last_map_offset != current_offset or map_canvas.map_image_id is None:
            if map_canvas.map_image_id:
                map_canvas.delete(map_canvas.map_image_id)
            
            # Draw map image with padding offset
            # The map image starts at (-PAD, -PAD) in tile coordinates
            map_offset_x = dx - PAD * TILE_SIZE
            map_offset_y = dy - PAD * TILE_SIZE
            
            map_canvas.map_image_id = map_canvas.create_image(
                map_offset_x, map_offset_y, 
                image=map_canvas.cached_map_photo, 
                anchor='nw'
            )
            map_canvas.last_map_offset = current_offset
            
            # Draw quest coordinates
            draw_quest_coordinates(map_canvas, dx, dy, canvas_w, canvas_h)
        
        # Update sprite at center
        update_sprite(map_canvas, facing, center_x, center_y)
        
        # Update traversal (quest coords are stored without padding)
        update_coordinate_traversal(map_canvas, global_x - PAD, global_y - PAD, env_labels)
        
        return True
        
    except Exception as e:
        print(f"Render: Error in map drawing: {e}")
        import traceback
        traceback.print_exc()
        return False

def draw_quest_coordinates(map_canvas, dx, dy, canvas_w, canvas_h):
    """Draw quest coordinates on map
    
    Quest coordinates are stored as (y, x) tuples WITHOUT padding.
    We need to add padding to match the global coordinate system.
    """
    map_canvas.delete("quest_coordinates")
    
    current_quest = map_canvas.current_quest_id
    
    # Draw all quest coordinates
    for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
        # Skip if this is current quest (drawn separately on top)
        draw_on_top = current_quest is not None and quest_id == current_quest
        
        if not draw_on_top:
            # Add padding to match global coordinate system
            pixel_x = (coord_gx + PAD) * TILE_SIZE + TILE_SIZE // 2
            pixel_y = (coord_gy + PAD) * TILE_SIZE + TILE_SIZE // 2
            
            # Apply canvas offset
            coord_x = pixel_x + dx
            coord_y = pixel_y + dy
            
            # Skip if outside canvas
            if not (-10 <= coord_x <= canvas_w + 10 and -10 <= coord_y <= canvas_h + 10):
                continue
            
            coord_pos = (coord_gy, coord_gx)
            is_traversed = coord_pos in map_canvas.traversed_coordinates
            
            if is_traversed:
                color = "#4ec9b0"
                size = 4
            else:
                color = "#569cd6"
                size = 3
            
            map_canvas.create_oval(
                coord_x + 40 - size, coord_y + 40 - size, 
                coord_x + 40 + size, coord_y + 40 + size,
                fill=color, outline="", 
                tags="quest_coordinates"
            )
    
    # Draw current quest coordinates on top
    if current_quest is not None:
        for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
            if quest_id != current_quest:
                continue
            
            # Add padding to match global coordinate system
            pixel_x = (coord_gx + PAD) * TILE_SIZE + TILE_SIZE // 2
            pixel_y = (coord_gy + PAD) * TILE_SIZE + TILE_SIZE // 2
            
            # Apply canvas offset
            coord_x = pixel_x + dx
            coord_y = pixel_y + dy
            
            # Skip if outside canvas
            if not (-10 <= coord_x <= canvas_w + 10 and -10 <= coord_y <= canvas_h + 10):
                continue
            
            coord_pos = (coord_gy, coord_gx)
            is_traversed = coord_pos in map_canvas.traversed_coordinates
            
            if is_traversed:
                color = "#4ec9b0"
                outline = "#3ba776"
                size = 6
            else:
                color = "#dcdcaa"
                outline = "#b8a654"
                size = 5
            
            map_canvas.create_oval(
                coord_x - size, coord_y - size, 
                coord_x + size, coord_y + size,
                fill=color, outline=outline, width=2, 
                tags="quest_coordinates"
            )

def update_sprite(map_canvas, facing, center_x, center_y):
    """Update player sprite on map"""
    if facing not in map_canvas.cached_sprite_photos:
        SPRITE_INDICES = {'Down': 0, 'Up': 3, 'Left': 6, 'Right': 8}
        idx = SPRITE_INDICES.get(facing, 0)
        
        if 0 <= idx < len(map_canvas.sprite_frames):
            sprite = map_canvas.sprite_frames[idx]
            map_canvas.cached_sprite_photos[facing] = ImageTk.PhotoImage(sprite)
    
    if facing in map_canvas.cached_sprite_photos:
        sprite_photo = map_canvas.cached_sprite_photos[facing]
        
        # Sprite is 16x16, center it
        sprite_x = center_x - 8
        sprite_y = center_y - 8
        
        if map_canvas.sprite_image_id:
            map_canvas.delete(map_canvas.sprite_image_id)
        
        map_canvas.sprite_image_id = map_canvas.create_image(
            sprite_x, sprite_y, 
            image=sprite_photo, 
            anchor='nw'
        )
        
        # Layer ordering
        if hasattr(map_canvas, 'map_image_id') and map_canvas.map_image_id:
            map_canvas.tag_lower(map_canvas.map_image_id)
        map_canvas.tag_raise("quest_coordinates")
        map_canvas.tag_raise(map_canvas.sprite_image_id)

def update_coordinate_traversal(map_canvas, player_gx, player_gy, env_labels):
    """Update coordinate traversal tracking
    
    player_gx and player_gy are global coordinates WITHOUT padding,
    matching the quest coordinate system.
    """
    player_pos = (player_gy, player_gx)
    
    # Check each quest coordinate
    for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
        coord_pos = (coord_gy, coord_gx)
        distance = abs(player_pos[0] - coord_pos[0]) + abs(player_pos[1] - coord_pos[1])
        
        if distance <= map_canvas.coordinate_traversal_distance:
            map_canvas.traversed_coordinates.add(coord_pos)
    
    # Update stats
    total = len(map_canvas.all_quest_coordinates)
    traversed = len(map_canvas.traversed_coordinates)
    
    if total > 0:
        env_labels['total_coordinates'].config(text=f"Total Coordinates: {total}")
        env_labels['traversed_count'].config(text=f"Traversed: {traversed} ({traversed/total*100:.1f}%)")
        
        current_quest = map_canvas.current_quest_id
        if current_quest is not None:
            quest_coords = [(gy, gx) for gy, gx, qid in map_canvas.all_quest_coordinates if qid == current_quest]
            quest_traversed = [c for c in quest_coords if c in map_canvas.traversed_coordinates]
            
            if quest_coords:
                percentage = len(quest_traversed) / len(quest_coords) * 100
                env_labels['current_quest_stats'].config(
                    text=f"Quest {current_quest}: {len(quest_traversed)}/{len(quest_coords)} ({percentage:.1f}%)"
                )

def draw_warp_minimap(canvas, debug_text, warp_data):
    """Draw warp minimap with modern styling"""
    try:
        canvas.delete("all")
        
        if isinstance(warp_data, dict):
            warp_obs_data = warp_data.get("minimap")
            debug_info = warp_data.get("debug_info", {})
        else:
            warp_obs_data = warp_data
            debug_info = {}
        
        if warp_obs_data is None:
            canvas.create_text(150, 135, text="No Warp Data", fill="#f48771", font=("Segoe UI", 12))
            return
        
        # Draw grid
        tile_width = 30
        tile_height = 30
        
        for row in range(9):
            for col in range(10):
                x1 = col * tile_width
                y1 = row * tile_height
                x2 = x1 + tile_width
                y2 = y1 + tile_height
                
                warp_id = warp_obs_data[row, col] if hasattr(warp_obs_data, 'shape') else 0
                
                if warp_id > 0:
                    # Color based on warp ID
                    hue = (warp_id * 137.5) % 360
                    rgb = colorsys.hsv_to_rgb(hue / 360, 0.6, 0.8)
                    color = f"#{int(rgb[0]*255):02x}{int(rgb[1]*255):02x}{int(rgb[2]*255):02x}"
                    
                    canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="#3e3e42", width=1)
                    canvas.create_text(x1 + tile_width/2, y1 + tile_height/2, 
                                     text=str(warp_id), fill="#ffffff", font=("Segoe UI", 9, "bold"))
                else:
                    canvas.create_rectangle(x1, y1, x2, y2, fill="#252526", outline="#3e3e42", width=1)
        
        # Player position
        player_x = 4 * tile_width + tile_width/2
        player_y = 4 * tile_height + tile_height/2
        canvas.create_oval(player_x - 8, player_y - 8, player_x + 8, player_y + 8,
                         fill="#007ACC", outline="#005a9e", width=2)
        
        # Update debug text
        if debug_text:
            debug_text.config(state='normal')
            debug_text.delete(1.0, tk.END)
            debug_text.insert(tk.END, format_debug_info(debug_info, warp_obs_data))
            debug_text.config(state='disabled')
            
    except Exception as e:
        print(f"Render: Error drawing warp minimap: {e}")

def format_debug_info(debug_info, warp_obs_data):
    """Format debug information for display"""
    text = "=== WARP DEBUG INFO ===\n\n"
    
    if debug_info:
        text += f"Map: {debug_info.get('map_name', 'Unknown')} (ID: {debug_info.get('current_map', '?')})\n"
        text += f"Is Warping: {debug_info.get('is_warping', 'Unknown')}\n\n"
        
        warp_dict_entries = debug_info.get('warp_dict_entries', [])
        if warp_dict_entries:
            text += f"WARP_DICT Entries ({len(warp_dict_entries)}):\n"
            for entry in warp_dict_entries[:5]:
                text += f"  [{entry['index']}] ({entry['x']},{entry['y']}) → Map {entry['target_map_id']}\n"
            if len(warp_dict_entries) > 5:
                text += f"  ... and {len(warp_dict_entries) - 5} more\n"
    
    return text

def update_screen_canvas(canvas, status_data, photo_attr):
    """Update emulator or collision screen canvas"""
    try:
        pixel_data, width, height, mode = status_data
        img = Image.frombytes(mode, (width, height), pixel_data)
        
        canvas_w = canvas.winfo_width() or int(canvas.cget('width'))
        canvas_h = canvas.winfo_height() or int(canvas.cget('height'))
        
        if width != canvas_w or height != canvas_h:
            img = img.resize((canvas_w, canvas_h), Image.Resampling.NEAREST)
        
        photo = ImageTk.PhotoImage(img)
        setattr(canvas, photo_attr, photo)
        
        canvas.delete("all")
        canvas.create_image(0, 0, image=photo, anchor='nw')
        
    except Exception as e:
        print(f"Render: Error updating screen canvas: {e}")

def test_coordinate_conversion():
    """Test function to verify coordinate conversions"""
    # Load map data
    load_map_data()
    prepare_map_data_for_conversion()
    
    print("\n=== COORDINATE CONVERSION TEST ===")
    
    # Test player position
    local_y, local_x = 6, 6
    map_id = 37
    
    global_y, global_x = local_to_global(local_y, local_x, map_id)
    print(f"Player position test:")
    print(f"  Map {map_id} local (y={local_y},x={local_x}) -> global (y={global_y},x={global_x})")
    
    # Show calculation
    if map_id in MAP_DATA_INT_KEYS:
        coords = MAP_DATA_INT_KEYS[map_id]['coordinates']
        print(f"  Map offset: [x={coords[0]}, y={coords[1]}]")
        print(f"  global_x = {local_x} + {coords[0]} + {PAD} = {global_x}")
        print(f"  global_y = {local_y} + {coords[1]} + {PAD} = {global_y}")
    
    # Actual positions from UI
    print(f"\nActual pixel positions:")
    print(f"  Player tile: (992, 5360) = tile ({992//16}, {5360//16})")
    print(f"  Sprite: (1312, 5680) = tile ({1312//16}, {5680//16})")  
    print(f"  Quest dot: (1568, 5840) = tile ({1568//16}, {5840//16})")
    
    # Calculate expected positions
    print(f"\nExpected positions based on global coords:")
    print(f"  Player global ({global_y}, {global_x}):")
    print(f"    Pixels: ({global_x * 16}, {global_y * 16}) = ({global_x * 16}, {global_y * 16})")
    print(f"    With centering: ({global_x * 16 + 8}, {global_y * 16 + 8})")
    
    # Map offset calculation
    print(f"\nMap image offset calculation:")
    print(f"  Map starts at tile (-{PAD}, -{PAD}) = pixel ({-PAD * 16}, {-PAD * 16})")
    
    # Test quest coordinates
    print(f"\nQuest coordinates (stored without padding):")
    test_coords = [(338, 84), (345, 78), (355, 81)]
    for gy, gx in test_coords:
        with_pad_x = gx + PAD
        with_pad_y = gy + PAD
        pixel_x = with_pad_x * TILE_SIZE + TILE_SIZE // 2
        pixel_y = with_pad_y * TILE_SIZE + TILE_SIZE // 2
        print(f"  ({gy},{gx}) + padding = ({with_pad_y},{with_pad_x}) -> pixels ({pixel_x},{pixel_y})")
    
    print("=== END TEST ===\n")

def save_canvas_as_png(map_canvas, filename_prefix="global_map"):
    """Save the current map canvas as PNG with full rendering
    
    Args:
        map_canvas: The tkinter canvas containing the rendered map
        filename_prefix: Prefix for the filename
    """
    try:
        # Get canvas dimensions
        canvas_w = map_canvas.winfo_width()
        canvas_h = map_canvas.winfo_height()
        
        if canvas_w <= 1 or canvas_h <= 1:
            # Canvas not properly initialized yet
            return False
        
        # Create image from canvas
        # Use a PostScript file as intermediate since canvas.postscript() is most reliable
        ps_file = f"temp_canvas_{int(time.time())}.ps"
        map_canvas.postscript(file=ps_file)
        
        try:
            # Convert PostScript to PNG using PIL
            from PIL import Image
            img = Image.open(ps_file)
            
            # Generate filename with timestamp
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            png_filename = f"{filename_prefix}_{timestamp}.png"
            
            # Save to grok_plays_pokemon directory (root directory of the project)
            save_dir = Path(__file__).parent.parent  # Go up two levels from ui/ to grok_plays_pokemon/
            png_path = save_dir / png_filename
            
            # Convert and save as PNG
            if img.mode == 'RGBA':
                img.save(png_path, 'PNG')
            else:
                img.convert('RGB').save(png_path, 'PNG')
            
            print(f"PNG saved: {png_path}")
            return str(png_path)
            
        except Exception as e:
            print(f"Error converting PS to PNG: {e}")
            # Fallback: try direct canvas rendering
            try:
                # Alternative method using canvas bbox
                x = map_canvas.canvasx(0)
                y = map_canvas.canvasy(0)
                x1 = map_canvas.canvasx(canvas_w)
                y1 = map_canvas.canvasy(canvas_h)
                
                # This is a more complex approach - for now just report the attempt
                print(f"Canvas dimensions: {canvas_w}x{canvas_h}, bbox: ({x},{y}) to ({x1},{y1})")
                return False
                
            except Exception as e2:
                print(f"Fallback PNG save also failed: {e2}")
                return False
        finally:
            # Clean up temporary PostScript file
            try:
                if os.path.exists(ps_file):
                    os.remove(ps_file)
            except:
                pass
                
    except Exception as e:
        print(f"Error saving canvas as PNG: {e}")
        return False

def save_full_map_with_overlays(map_canvas, local_x, local_y, map_id, facing):
    """Create and save a full map PNG with all overlays rendered
    
    This creates a high-quality PNG of the global map with player sprite and coordinate overlays
    """
    try:
        if not hasattr(map_canvas, 'full_map_img') or map_canvas.full_map_img is None:
            print("Full map image not loaded, cannot save PNG")
            return False
        
        # Create a copy of the full map image for rendering
        full_map = map_canvas.full_map_img.copy()
        draw = ImageDraw.Draw(full_map)
        
        # Calculate global player position (corrected coordinates)
        global_y, global_x = local_to_global(local_y, local_x, map_id)
        
        # Draw player sprite at current position (FIXED positioning)
        if hasattr(map_canvas, 'sprite_frames') and map_canvas.sprite_frames:
            SPRITE_INDICES = {'Down': 0, 'Up': 3, 'Left': 6, 'Right': 8}
            sprite_idx = SPRITE_INDICES.get(facing, 0)
            
            if 0 <= sprite_idx < len(map_canvas.sprite_frames):
                sprite = map_canvas.sprite_frames[sprite_idx]
                
                # CORRECTED: Player sprite position calculation
                # Global coordinates already include padding, no need to add PAD again
                sprite_x = global_x * TILE_SIZE
                sprite_y = global_y * TILE_SIZE
                
                # Paste sprite onto map with proper transparency
                if sprite.mode == 'RGBA':
                    full_map.paste(sprite, (sprite_x, sprite_y), sprite)
                else:
                    full_map.paste(sprite, (sprite_x, sprite_y))
        
        # Draw quest coordinate overlays (FIXED positioning)
        if hasattr(map_canvas, 'all_quest_coordinates'):
            for coord_gy, coord_gx, quest_id in map_canvas.all_quest_coordinates:
                # CORRECTED: Quest coordinates are stored WITHOUT padding,
                # but the full map image INCLUDES padding, so we need to add PAD
                pixel_x = (coord_gx + PAD) * TILE_SIZE + TILE_SIZE // 2
                pixel_y = (coord_gy + PAD) * TILE_SIZE + TILE_SIZE // 2
                
                # Check if coordinate is traversed
                coord_pos = (coord_gy, coord_gx)
                is_traversed = hasattr(map_canvas, 'traversed_coordinates') and coord_pos in map_canvas.traversed_coordinates
                
                # Draw coordinate marker
                if is_traversed:
                    color = (78, 201, 176)  # #4ec9b0 (green for traversed)
                    size = 4
                else:
                    color = (86, 156, 214)  # #569cd6 (blue for untraversed)
                    size = 3
                
                # Draw filled circle
                draw.ellipse([
                    pixel_x - size, pixel_y - size,
                    pixel_x + size, pixel_y + size
                ], fill=color)
        
        # Use static filename (overwrites previous)
        png_filename = "global_map_with_overlays.png"
        
        # Save to grok_plays_pokemon directory
        save_dir = Path(__file__).parent.parent  # Go up two levels from ui/ to grok_plays_pokemon/
        png_path = save_dir / png_filename
        
        # Save the composed image
        full_map.save(png_path, 'PNG')
        print(f"Global map PNG saved (overwritten): {png_path}")
        return str(png_path)
        
    except Exception as e:
        print(f"Error saving full map PNG: {e}")
        import traceback
        traceback.print_exc()
        return False

# Run test if executed directly
if __name__ == "__main__":
    test_coordinate_conversion()