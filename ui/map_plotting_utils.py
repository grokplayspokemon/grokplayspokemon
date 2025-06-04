# map_plotting_utils.py
import tkinter as tk

def draw_first_n_quest_paths(map_canvas, quest_coords_map, quest_colors_map, num_quests_to_draw):
    """
    Draws paths for the first N quests on the given Tkinter canvas.

    Args:
        map_canvas: The Tkinter Canvas widget to draw on.
        quest_coords_map: Dictionary mapping quest_id_str to list of (gy, gx) coordinates.
                          Example: {'001': [(y1, x1), (y2, x2), ...], ...}
        quest_colors_map: Dictionary mapping quest_id_str to hex color string.
                          Example: {'001': '#RRGGBB', ...}
        num_quests_to_draw: The number of initial quests to draw paths for.
    """
    map_canvas.delete("quest_path_line")  # Clear any previously drawn quest paths

    TILE_SIZE = 16  # Standard tile size in pixels

    try:
        canvas_w = int(map_canvas.cget('width'))
        canvas_h = int(map_canvas.cget('height'))
    except tk.TclError:
        # Fallback if canvas dimensions aren't immediately available
        # This might happen if called before the window is fully rendered.
        # Adjust these defaults if they don't suit your map canvas size.
        print("MAP_PLOT: WARNING - Canvas dimensions not available via cget. Using fallbacks (600x600).")
        canvas_w = 600 
        canvas_h = 600
        
    canvas_center_x = canvas_w // 2
    canvas_center_y = canvas_h // 2

    # This centering logic is based on the "IMMEDIATE TEST" block in your quest_ui.py.
    # It assumes a fixed viewpoint where a specific part of the Kanto map 
    # (approximated by kanto_center_pixel_x, kanto_center_pixel_y) 
    # is aligned with the canvas center.
    # Kanto map dimensions are roughly 440x440 tiles. (0,0) to (439,439)
    # An approximate center tile could be (220,220)
    kanto_map_center_pixel_x = 220 * TILE_SIZE 
    kanto_map_center_pixel_y = 220 * TILE_SIZE

    # Sort quest IDs to ensure we take the 'first N' in numerical order
    # Assumes quest_coords_map keys are like '001', '002', etc.
    sorted_quest_ids = sorted(quest_coords_map.keys(), key=lambda q_id_str: int(q_id_str))

    print(f"MAP_PLOT: Attempting to draw paths for the first {num_quests_to_draw} quests.")

    for i in range(min(num_quests_to_draw, len(sorted_quest_ids))):
        quest_id_str = sorted_quest_ids[i]

        if quest_id_str not in quest_coords_map:
            print(f"MAP_PLOT: DIAGNOSTIC - Coordinate data for quest {quest_id_str} not found. Skipping.")
            continue
        if quest_id_str not in quest_colors_map:
            print(f"MAP_PLOT: DIAGNOSTIC - Color for quest {quest_id_str} not found. Skipping.")
            continue
            
        coordinates = quest_coords_map[quest_id_str]  # List of (gy, gx) tuples
        color = quest_colors_map[quest_id_str]
        
        if not coordinates:
            # print(f"MAP_PLOT: DIAGNOSTIC - No coordinates for Quest {quest_id_str}. Skipping.")
            continue

        # print(f"MAP_PLOT: DIAGNOSTIC - Drawing Quest {quest_id_str} with {len(coordinates)} points. Color: {color}")

        if len(coordinates) < 2:
            # If only one point, draw a small circle marker
            if len(coordinates) == 1:
                gy, gx = coordinates[0]
                
                # Convert global tile coordinate to global pixel coordinate (center of tile)
                pixel_x = gx * TILE_SIZE + TILE_SIZE // 2
                pixel_y = gy * TILE_SIZE + TILE_SIZE // 2
                
                # Transform global pixel coordinate to canvas coordinate
                canvas_x = pixel_x - kanto_map_center_pixel_x + canvas_center_x
                canvas_y = pixel_y - kanto_map_center_pixel_y + canvas_center_y
                
                radius = 3  # Radius for the point marker
                map_canvas.create_oval(
                    canvas_x - radius, canvas_y - radius, canvas_x + radius, canvas_y + radius,
                    fill=color, outline=color, tags="quest_path_line"
                )
                # print(f"MAP_PLOT: DIAGNOSTIC - Drew single point for Quest {quest_id_str} at canvas ({int(canvas_x)}, {int(canvas_y)})")
            continue

        # Draw lines between consecutive points
        for j in range(len(coordinates) - 1):
            gy1, gx1 = coordinates[j]
            gy2, gx2 = coordinates[j+1]

            # Convert global tile coordinates to global pixel coordinates (center of tiles)
            pixel_x1 = gx1 * TILE_SIZE + TILE_SIZE // 2
            pixel_y1 = gy1 * TILE_SIZE + TILE_SIZE // 2
            pixel_x2 = gx2 * TILE_SIZE + TILE_SIZE // 2
            pixel_y2 = gy2 * TILE_SIZE + TILE_SIZE // 2

            # Transform global pixel coordinates to canvas coordinates
            canvas_x1 = pixel_x1 - kanto_map_center_pixel_x + canvas_center_x
            canvas_y1 = pixel_y1 - kanto_map_center_pixel_y + canvas_center_y
            canvas_x2 = pixel_x2 - kanto_map_center_pixel_x + canvas_center_x
            canvas_y2 = pixel_y2 - kanto_map_center_pixel_y + canvas_center_y
            
            map_canvas.create_line(
                canvas_x1, canvas_y1, canvas_x2, canvas_y2,
                fill=color, width=2, tags="quest_path_line" # Use a specific tag for easy clearing
            )
        # print(f"MAP_PLOT: DIAGNOSTIC - Drew path segments for Quest {quest_id_str}.")
    
    print(f"MAP_PLOT: Finished drawing attempt for quest paths.")