from PIL import Image, ImageDraw
import numpy as np

def create_tile_overlay(collision_map_str, alpha=128):
    """
    Create a transparent overlay showing walkable/unwalkable tiles from the collision map string.
    
    Args:
        collision_map_str (str): ASCII collision map from emulator
        alpha (int): Transparency value (0-255)
        
    Returns:
        PIL.Image: RGBA image overlay
    """
    if not collision_map_str:
        return None
        
    # Parse the collision map string
    lines = collision_map_str.split('\n')
    # Remove the border lines and legend
    map_lines = [line[1:-1] for line in lines[1:-1] if line.startswith('|')]
    
    # Create a transparent image (160x144 is the Game Boy resolution)
    overlay = Image.new('RGBA', (160, 144), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    # Calculate tile size
    tile_width = 160 // 10  # 10 columns
    tile_height = 144 // 9  # 9 rows
    
    # Draw tiles
    for row, line in enumerate(map_lines):
        for col, char in enumerate(line):
            x1 = col * tile_width
            y1 = row * tile_height
            x2 = x1 + tile_width
            y2 = y1 + tile_height
            
            if char == '█':  # Wall/obstacle
                draw.rectangle([x1, y1, x2, y2], fill=(255, 0, 0, alpha))  # Red for walls
            elif char == '·':  # Walkable path
                draw.rectangle([x1, y1, x2, y2], fill=(0, 255, 0, alpha))  # Green for paths
            elif char == 'S':  # Sprite/NPC
                draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 255, alpha))  # Blue for sprites
            elif char in '↑↓←→':  # Player
                draw.rectangle([x1, y1, x2, y2], fill=(255, 255, 0, alpha))  # Yellow for player
                
    return overlay

def overlay_on_screenshot(screenshot, collision_map_str, alpha=128):
    """
    Create a new image with the tile overlay blended onto the screenshot.
    
    Args:
        screenshot (PIL.Image): Original screenshot
        collision_map_str (str): ASCII collision map from emulator (numeric or character format)
        alpha (int): Transparency value (0-255)
        
    Returns:
        PIL.Image: Screenshot with overlay
    """
    # Convert numeric format to visual format if needed
    converted_map_str = convert_numeric_to_visual_map(collision_map_str)
    
    overlay = create_tile_overlay(converted_map_str, alpha)
    if overlay is None:
        return screenshot
        
    # Ensure screenshot is in RGBA mode for alpha blending
    if screenshot.mode != 'RGBA':
        screenshot = screenshot.convert('RGBA')
    
    # Composite the images
    return Image.alpha_composite(screenshot, overlay)

def convert_numeric_to_visual_map(collision_map_str):
    """
    Convert numeric collision map format to visual character format.
    
    Args:
        collision_map_str (str): Numeric collision map (0=walkable, 1=wall, 2=sprite, 3-6=player)
        
    Returns:
        str: Visual collision map with borders and characters
    """
    if not collision_map_str or not isinstance(collision_map_str, str):
        return None
    
    lines = collision_map_str.strip().split('\n')
    
    # Filter out legend lines and empty lines
    map_lines = []
    in_legend = False
    for line in lines:
        line = line.strip()
        if line.startswith('Legend:'):
            in_legend = True
            continue
        if in_legend:
            continue
        if line and all(c in '0123456789 ' for c in line):  # Only lines with numbers and spaces
            map_lines.append(line)
    
    if not map_lines:
        return None
    
    # Convert numeric codes to visual characters
    visual_lines = []
    for line in map_lines:
        if not line.strip():  # Skip empty lines
            continue
        visual_line = "|"  # Add border
        for char in line.split():
            if char == '0':  # walkable
                visual_line += '·'
            elif char == '1':  # wall/obstacle
                visual_line += '█'
            elif char == '2':  # sprite/NPC
                visual_line += 'S'
            elif char == '3':  # player facing up
                visual_line += '↑'
            elif char == '4':  # player facing down
                visual_line += '↓'
            elif char == '5':  # player facing left
                visual_line += '←'
            elif char == '6':  # player facing right
                visual_line += '→'
            else:
                visual_line += '?'  # unknown
        visual_line += "|"  # Add border
        visual_lines.append(visual_line)
    
    # Add top and bottom borders
    if visual_lines:
        border_width = len(visual_lines[0])
        top_border = "+" + "-" * (border_width - 2) + "+"
        bottom_border = top_border
        
        return top_border + "\n" + "\n".join(visual_lines) + "\n" + bottom_border
    
    return None 