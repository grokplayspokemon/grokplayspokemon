import io
import logging
import pickle
from collections import deque
import heapq
import os
from pathlib import Path
from datetime import datetime

from agent.memory_reader import PokemonRedReader, StatusCondition, Tileset
from PIL import Image, ImageDraw, ImageFont
from pyboy import PyBoy

# Import WARP_DICT for door detection
from game_data.constants import WARP_DICT, MAP_ID_REF

logger = logging.getLogger(__name__)

# Door Tile IDs mapped from tileset_constants.asm and door_tile_ids.asm
DOOR_TILE_IDS_BY_TILESET = {
    Tileset.OVERWORLD: {0x1B, 0x58},
    Tileset.FOREST: {0x3A},
    Tileset.MART: {0x5E},
    Tileset.POKECENTER: {0x5E}, # Assuming PokeCenter uses Mart tileset doors
    Tileset.GYM: {0x54}, # Assuming Gym uses House tileset doors
    Tileset.HOUSE: {0x54},
    Tileset.REDS_HOUSE_1: {0x54}, # Assuming Red's house uses House tileset doors
    Tileset.REDS_HOUSE_2: {0x54}, # Assuming Red's house uses House tileset doors
    Tileset.FOREST_GATE: {0x3B},
    Tileset.MUSEUM: {0x3B},
    Tileset.GATE: {0x3B},
    Tileset.SHIP: {0x1E},
    Tileset.LOBBY: {0x1C, 0x38, 0x1A},
    Tileset.MANSION: {0x1A, 0x1C, 0x53},
    Tileset.LAB: {0x34},
    Tileset.FACILITY: {0x43, 0x58, 0x1B},
    Tileset.PLATEAU: {0x3B, 0x1B},
    # Add other tilesets if needed, mapping them or defaulting
}

# Placeholder Stair Tile IDs - Adjust as needed!
STAIR_TILE_IDS_BY_TILESET = {
    Tileset.REDS_HOUSE_2: {0x55}, # GUESSING!
    Tileset.REDS_HOUSE_1: {0x55}, # Placeholder for 1F - GUESSING!
    # Add other tilesets/stairs IDs if known
}

class Emulator:
    def __init__(self, rom_path, headless=True, sound=False):
        self.rom_path = rom_path  # Store the ROM path
        self.headless = headless  # Store headless state
        self.sound = sound  # Store sound state
        try:
            # First try with cgb=True
            if headless:
                self.pyboy = PyBoy(rom_path, window="null", cgb=True)
            else:
                self.pyboy = PyBoy(rom_path, sound=sound, cgb=True)
        except Exception as e:
            logger.info("Failed to initialize in CGB mode, falling back to GB mode")
            # If that fails, try with cgb=False
            if headless:
                self.pyboy = PyBoy(rom_path, window="null", cgb=False)
            else:
                self.pyboy = PyBoy(rom_path, sound=sound, cgb=False)

    def tick(self, frames):
        """Advance the emulator by the specified number of frames."""
        for _ in range(frames):
            self.pyboy.tick()

    def initialize(self):
        """Initialize the emulator."""
        # Run the emulator for a short time to make sure it's ready
        self.pyboy.set_emulation_speed(0)
        for _ in range(60):
            self.tick(60)
        self.pyboy.set_emulation_speed(1)

    def get_screenshot(self):
        """Get the current screenshot."""
        return Image.fromarray(self.pyboy.screen.ndarray)

    def get_screenshot_with_overlay(self, alpha=128):
        """
        Get the current screenshot with a tile overlay showing walkable/unwalkable areas.
        
        Args:
            alpha (int): Transparency value for the overlay (0-255)
            
        Returns:
            PIL.Image: Screenshot with tile overlay
        """
        from tile_visualizer import overlay_on_screenshot
        screenshot = self.get_screenshot()
        collision_map = self.get_collision_map()
        return overlay_on_screenshot(screenshot, collision_map, alpha)

    def load_state(self, state_filename):
        """
        Load a PyBoy save state file into the emulator.
        
        Args:
            state_filename: Path to the PyBoy .state file
        """
        try:
            with open(state_filename, 'rb') as f:
                state_data = f.read()
                state_io = io.BytesIO(state_data)
                self.pyboy.load_state(state_io)
        except Exception as e:
            # If direct loading fails, try with pickle
            try:
                with open(state_filename, 'rb') as f:
                    state_data = pickle.load(f)
                    if "pyboy_state" in state_data:
                        pyboy_state_io = io.BytesIO(state_data["pyboy_state"])
                        self.pyboy.load_state(pyboy_state_io)
                    else:
                        raise ValueError("Invalid save state format")
            except Exception as e2:
                logger.error(f"Failed to load save state: {e2}")
                raise

    def save_state(self, filename_prefix="auto_save"):
        """Saves the current emulator state to a timestamped file."""
        saves_dir = Path("saves")
        saves_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_prefix}_{timestamp}.state"
        filepath = saves_dir / filename
        
        try:
            with open(filepath, "wb") as f:
                self.pyboy.save_state(f)
            logger.info(f"Saved state to {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to save state to {filepath}: {e}")
            return None

    def press_buttons(self, buttons, wait=True):
        """Press a sequence of buttons on the Game Boy.
        
        Args:
            buttons (list[str]): List of buttons to press in sequence
            wait (bool): Whether to wait after each button press
            
        Returns:
            str: Result of the button presses
        """
        results = []
        
        for button in buttons:
            if button not in ["a", "b", "start", "select", "up", "down", "left", "right"]:
                results.append(f"Invalid button: {button}")
                continue
                
            self.pyboy.button_press(button)
            self.tick(10)   # Press briefly
            self.pyboy.button_release(button)
            
            if wait:
                self.tick(120) # Wait longer after button release
            else:
                self.tick(10)   # Brief pause between button presses
                
            results.append(f"Pressed {button}")
        
        return "\n".join(results)

    def get_coordinates(self):
        """
        Returns the player's current coordinates from game memory.
        Returns:
            tuple[int, int]: (x, y) coordinates
        """
        reader = PokemonRedReader(self.pyboy.memory)
        return reader.read_coordinates()

    def get_active_dialog(self):
        """
        Returns the active dialog text from game memory.
        Returns:
            str: Dialog text
        """
        reader = PokemonRedReader(self.pyboy.memory)
        dialog = reader.read_dialog()
        if dialog:
            return dialog
        return None

    def get_location(self):
        """
        Returns the player's current location name from game memory.
        Returns:
            str: Location name
        """
        reader = PokemonRedReader(self.pyboy.memory)
        return reader.read_location()

    def _get_direction(self, array):
        """Determine the player's facing direction from the sprite pattern."""
        # Look through the array for any 2x2 grid containing numbers 0-3
        rows, cols = array.shape

        for i in range(rows - 1):
            for j in range(cols - 1):
                # Extract 2x2 grid
                grid = array[i : i + 2, j : j + 2].flatten()

                # Check for each direction pattern
                if list(grid) == [0, 1, 2, 3]:
                    return "down"
                elif list(grid) == [4, 5, 6, 7]:
                    return "up"
                elif list(grid) == [9, 8, 11, 10]:
                    return "right"
                elif list(grid) == [8, 9, 10, 11]:
                    return "left"

        return "no direction found"

    def _downsample_array(self, arr):
        """Downsample an 18x20 array to 9x10 by averaging 2x2 blocks."""
        # Ensure input array is 18x20
        if arr.shape != (18, 20):
            raise ValueError("Input array must be 18x20")

        # Reshape to group 2x2 blocks and take mean
        return arr.reshape(9, 2, 10, 2).mean(axis=(1, 3))

    def get_collision_map(self):
        """
        Generate a 2D array representing the collision map around the player.
        █ - Wall/Obstacle/Unwalkable
        · - Path/Walkable
        D - Door/Warp
        T - Stairs
        X - Blocked Path (Collision Pair)
        S - Sprite (NPC or Item)
        ↑/↓/←/→ - Player (facing direction)
        """
        scale = 4  # Scale factor for drawing
        font_size = 9
        font = ImageFont.truetype("arial.ttf", font_size) if "arial.ttf" in os.listdir() else ImageFont.load_default()

        reader = PokemonRedReader(self.pyboy.memory)
        player_x, player_y = reader.read_coordinates()
        player_direction = reader.read_player_direction()
        map_id = reader.read_current_map_id()
        map_name = MAP_ID_REF.get(map_id, f"UNKNOWN_MAP_{map_id}")

        # Get tileset for staircase identification
        tileset_id = reader.read_raw_tileset_id()
        tileset_enum = Tileset(tileset_id) if tileset_id is not None else None
        current_stair_ids = STAIR_TILE_IDS_BY_TILESET.get(tileset_enum, set())
        
        # Get warps for the current map
        current_map_warps = WARP_DICT.get(map_name, [])
        warp_coords = {(warp['x'], warp['y']) for warp in current_map_warps}

        # Dimensions for the visible part of the map
        visible_width = 20  # Tiles
        visible_height = 18 # Tiles
        
        # Determine the top-left corner of the visible map in world coordinates
        # Based on screen scroll registers and player position adjustments
        # wXCoord = 0xD362, wYCoord = 0xD361 (Player's map coords)
        # wCurMapWidth = 0xD35E
        # wPlayerBGMapOffsetY = 0xCC3E, wPlayerBGMapOffsetX = 0xCC3F
        # These offsets seem complex, let's approximate using player position for now
        # Assuming the player is roughly centered, offset by half the visible size
        
        # Use memory addresses for precise screen-to-map calculation
        # wBGMapPalPtr = 0xCFCF (Tile attributes pointer)
        # wBGMapDest = 0xCFCB (VRAM destination pointer for map tiles)
        # VRAM areas: 0x9800-0x9BFF (BG Map 1), 0x9C00-0x9FFF (BG Map 2)
        
        # Read screen scroll coordinates (relative to top-left of the map)
        scx = self.pyboy.memory[0xFF43] # SCX - Scroll X
        scy = self.pyboy.memory[0xFF42] # SCY - Scroll Y

        # Calculate the top-left tile coordinates shown on screen
        # Each screen tile is 8x8 pixels, scroll is in pixels
        top_left_map_x = (player_x - (visible_width // 2)) + (scx // 8) 
        top_left_map_y = (player_y - (visible_height // 2)) + (scy // 8)

        collision_matrix = [[' ' for _ in range(visible_width)] for _ in range(visible_height)]
        
        # Get the full tilemap (might be slow)
        # This part seems problematic and might not be giving the right coordinates or tiles
        # Let's rely on reading individual tiles around the player instead
        
        # Iterate through the visible grid
        for screen_y in range(visible_height):
            for screen_x in range(visible_width):
                # Calculate the corresponding world map coordinates
                map_x = top_left_map_x + screen_x
                map_y = top_left_map_y + screen_y
                
                try:
                    # Check if this coordinate is a warp point
                    if (map_x, map_y) in warp_coords:
                        collision_matrix[screen_y][screen_x] = 'D'
                        continue # Warps take precedence

                    # Check for stairs using visual tile ID
                    visual_tile_id = reader.read_map_tile_id(map_x, map_y)
                    if visual_tile_id in current_stair_ids:
                         collision_matrix[screen_y][screen_x] = 'T'
                         continue # Stairs take precedence over walkability check

                    # Check collision for the tile the player would move *into*
                    # Need to simulate a step? No, check tile properties directly
                    # This requires understanding tile collision data structure
                    # Placeholder: Assume non-warp/stair tiles are walkable for now
                    # Real collision check needed here
                    # collision_byte = self.get_tile_collision(map_x, map_y) # Needs implementation
                    # if collision_byte == 0xFF: # Example: 0xFF means unwalkable
                    #     collision_matrix[screen_y][screen_x] = '█'
                    # else:
                    collision_matrix[screen_y][screen_x] = '·' # Default to walkable if not warp/stair

                except IndexError:
                    # Coordinates are outside the map boundaries
                    collision_matrix[screen_y][screen_x] = ' ' # Treat as empty space
                except Exception as e:
                     logger.error(f"Error processing tile at ({map_x},{map_y}): {e}")
                     collision_matrix[screen_y][screen_x] = '?' # Error state


        # Add sprites (NPCs, items) - Read from wObjectStructs (starting at 0xC100)
        # Each struct is 16 bytes. Need to determine which objects are on screen.
        num_objects = self.pyboy.memory[0xD367] # wCurMapObjectCount (Incorrect addr? Needs verification)
        # Correct address seems to be wCurrentMapObjectCount = $D48D
        num_objects = self.pyboy.memory[0xD48D]
        object_base = 0xC100
        sprite_radius = 2 # How close a sprite needs to be to player coords

        for i in range(num_objects):
            obj_addr = object_base + i * 16
            # Object struct: yCoord, xCoord, yPixel, xPixel, objectID, ...
            obj_y = self.pyboy.memory[obj_addr + 0]  # yCoord (tile position)
            obj_x = self.pyboy.memory[obj_addr + 1]  # xCoord (tile position)
            
            # Calculate screen position relative to top-left
            screen_obj_x = obj_x - top_left_map_x
            screen_obj_y = obj_y - top_left_map_y
            
            # Check if sprite is within the visible collision map bounds
            if 0 <= screen_obj_y < visible_height and 0 <= screen_obj_x < visible_width:
                 # Don't overwrite player or existing Door/Stair
                 if collision_matrix[screen_obj_y][screen_obj_x] not in ['↑','↓','←','→', 'D', 'T']:
                     collision_matrix[screen_obj_y][screen_obj_x] = 'S'


        # Add player position and direction
        player_screen_x = player_x - top_left_map_x
        player_screen_y = player_y - top_left_map_y

        if 0 <= player_screen_y < visible_height and 0 <= player_screen_x < visible_width:
            if player_direction == 0:  # Down
                collision_matrix[player_screen_y][player_screen_x] = '↓'
            elif player_direction == 4:  # Up
                collision_matrix[player_screen_y][player_screen_x] = '↑'
            elif player_direction == 8:  # Left
                collision_matrix[player_screen_y][player_screen_x] = '←'
            elif player_direction == 12: # Right
                collision_matrix[player_screen_y][player_screen_x] = '→'
        
        # TODO: Add collision pair detection ('X') - Requires knowing tile pair collision data

        # Convert the matrix to a formatted string with borders for readability and overlay compatibility
        border = "+" + "-" * visible_width + "+"
        rows = ["|" + "".join(row) + "|" for row in collision_matrix]
        map_str = "\n".join([border] + rows + [border])
        # Append warp information for quick reference
        if current_map_warps:
            warp_lines = []
            for warp in current_map_warps:
                warp_lines.append(
                    f"({warp['x']},{warp['y']}) -> {warp['target_map_name']} (id={warp['target_map_id']}, warp_id={warp['warp_id']})"
                )
            map_str += "\nWarps:" + "\n" + "\n".join(warp_lines)
        
        # Log the generated map for debugging
        logger.debug(f"Collision Map (Player: {player_x},{player_y} Dir: {player_direction} Map: {map_name}):\n{map_str}")

        return map_str

    def get_valid_moves(self):
        """
        Returns a list of valid moves (up, down, left, right) based on the collision map.
        Returns:
            list[str]: List of valid movement directions
        """
        # Get collision map
        collision_map = self.pyboy.game_area_collision()
        terrain = self._downsample_array(collision_map)

        # Player is always at position (4,4) in the 9x10 downsampled map
        valid_moves = []

        # Check each direction
        if terrain[3][4] != 0:  # Up
            valid_moves.append("up")
        if terrain[5][4] != 0:  # Down
            valid_moves.append("down")
        if terrain[4][3] != 0:  # Left
            valid_moves.append("left")
        if terrain[4][5] != 0:  # Right
            valid_moves.append("right")

        return valid_moves

    def _can_move_between_tiles(self, tile1: int, tile2: int, tileset: str) -> bool:
        """
        Check if movement between two tiles is allowed based on tile pair collision data.

        Args:
            tile1: The tile being moved from
            tile2: The tile being moved to
            tileset: The current tileset name

        Returns:
            bool: True if movement is allowed, False if blocked
        """
        # Tile pair collision data
        TILE_PAIR_COLLISIONS_LAND = [
            ("CAVERN", 288, 261),
            ("CAVERN", 321, 261),
            ("FOREST", 304, 302),
            ("CAVERN", 298, 261),
            ("CAVERN", 261, 289),
            ("FOREST", 338, 302),
            ("FOREST", 341, 302),
            ("FOREST", 342, 302),
            ("FOREST", 288, 302),
            ("FOREST", 350, 302),
            ("FOREST", 351, 302),
        ]

        TILE_PAIR_COLLISIONS_WATER = [
            ("FOREST", 276, 302),
            ("FOREST", 328, 302),
            ("CAVERN", 276, 261),
        ]

        # Check both land and water collisions
        for ts, t1, t2 in TILE_PAIR_COLLISIONS_LAND + TILE_PAIR_COLLISIONS_WATER:
            if ts == tileset:
                # Check both directions since collisions are bidirectional
                if (tile1 == t1 and tile2 == t2) or (tile1 == t2 and tile2 == t1):
                    return False

        return True

    def get_sprites(self, debug=False):
        """
        Get the location of all of the sprites on the screen.
        returns set of coordinates that are (column, row)
        """
        # Group sprites by their exact Y coordinate
        sprites_by_y = {}

        for i in range(40):
            sp = self.pyboy.get_sprite(i)
            if sp.on_screen:
                x = int(sp.x / 160 * 10)
                y = int(sp.y / 144 * 9)
                orig_y = sp.y

                if orig_y not in sprites_by_y:
                    sprites_by_y[orig_y] = []
                sprites_by_y[orig_y].append((x, y, i))

        # Sort Y coordinates
        y_positions = sorted(sprites_by_y.keys())
        bottom_sprite_tiles = set()

        if debug:
            print("\nSprites grouped by original Y:")
            for orig_y in y_positions:
                sprites = sprites_by_y[orig_y]
                print(f"Y={orig_y}:")
                for x, grid_y, i in sprites:
                    print(f"  Sprite {i}: x={x}, grid_y={grid_y}")

        SPRITE_HEIGHT = 8

        # First, group sprites by X coordinate for each Y level
        for i in range(len(y_positions) - 1):
            y1 = y_positions[i]
            y2 = y_positions[i + 1]

            if y2 - y1 == SPRITE_HEIGHT:
                # Group sprites by X coordinate at each Y level
                sprites_at_y1 = {s[0]: s for s in sprites_by_y[y1]}  # x -> sprite info
                sprites_at_y2 = {s[0]: s for s in sprites_by_y[y2]}

                # Only match sprites that share the same X coordinate
                for x in sprites_at_y2:
                    if x in sprites_at_y1:  # If there's a matching top sprite at this X
                        bottom_sprite = sprites_at_y2[x]
                        bottom_sprite_tiles.add((x, bottom_sprite[1]))
                        if debug:
                            print(f"\nMatched sprites at x={x}, Y1={y1}, Y2={y2}")

        return bottom_sprite_tiles

    def find_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        """
        Finds the most efficient path from the player's current position (4,4) to the target position.
        If the target is unreachable, finds path to nearest accessible spot.
        Allows ending on a wall tile if that's the target.
        Takes into account terrain, sprite collisions, and tile pair collisions.

        Args:
            target_row: Row index in the 9x10 downsampled map (0-8)
            target_col: Column index in the 9x10 downsampled map (0-9)

        Returns:
            tuple[str, list[str]]: Status message and sequence of movements
        """
        # Get collision map, terrain, and sprites
        collision_map = self.pyboy.game_wrapper.game_area_collision()
        terrain = self._downsample_array(collision_map)
        sprite_locations = self.get_sprites()

        # Get full map for tile values and current tileset
        full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
        reader = PokemonRedReader(self.pyboy.memory)
        tileset = reader.read_tileset()

        # Start at player position (always 4,4 in the 9x10 grid)
        start = (4, 4)
        end = (target_row, target_col)

        # Validate target position
        if not (0 <= target_row < 9 and 0 <= target_col < 10):
            return "Invalid target coordinates", []

        # A* algorithm
        def heuristic(a, b):
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        open_set = []
        heapq.heappush(open_set, (0, start))
        came_from = {}
        g_score = {start: 0}
        f_score = {start: heuristic(start, end)}

        # Track closest reachable point
        closest_point = start
        min_distance = heuristic(start, end)

        def reconstruct_path(current):
            path = []
            while current in came_from:
                prev = came_from[current]
                if prev[0] < current[0]:
                    path.append("down")
                elif prev[0] > current[0]:
                    path.append("up")
                elif prev[1] < current[1]:
                    path.append("right")
                else:
                    path.append("left")
                current = prev
            path.reverse()
            return path

        while open_set:
            _, current = heapq.heappop(open_set)

            # Check if we've reached target
            if current == end:
                path = reconstruct_path(current)
                is_wall = terrain[end[0]][end[1]] == 0
                if is_wall:
                    return (
                        f"Partial Success: Your target location is a wall. In case this is intentional, attempting to navigate there.",
                        path,
                    )
                else:
                    return (
                        f"Success: Found path to target at ({target_row}, {target_col}).",
                        path,
                    )

            # Track closest point
            current_distance = heuristic(current, end)
            if current_distance < min_distance:
                closest_point = current
                min_distance = current_distance

            # If we're next to target and target is a wall, we can end here
            if (abs(current[0] - end[0]) + abs(current[1] - end[1])) == 1 and terrain[
                end[0]
            ][end[1]] == 0:
                path = reconstruct_path(current)
                # Add final move onto wall
                if end[0] > current[0]:
                    path.append("down")
                elif end[0] < current[0]:
                    path.append("up")
                elif end[1] > current[1]:
                    path.append("right")
                else:
                    path.append("left")
                return (
                    f"Success: Found path to position adjacent to wall at ({target_row}, {target_col}).",
                    path,
                )

            # Check all four directions
            for dr, dc, direction in [
                (1, 0, "down"),
                (-1, 0, "up"),
                (0, 1, "right"),
                (0, -1, "left"),
            ]:
                neighbor = (current[0] + dr, current[1] + dc)

                # Check bounds
                if not (0 <= neighbor[0] < 9 and 0 <= neighbor[1] < 10):
                    continue
                # Skip walls unless it's the final destination
                if terrain[neighbor[0]][neighbor[1]] == 0 and neighbor != end:
                    continue
                # Skip sprites unless it's the final destination
                if (neighbor[1], neighbor[0]) in sprite_locations and neighbor != end:
                    continue

                # Check tile pair collisions
                # Get bottom-left tile of each 2x2 block
                current_tile = full_map[current[0] * 2 + 1][
                    current[1] * 2
                ]  # Bottom-left tile of current block
                neighbor_tile = full_map[neighbor[0] * 2 + 1][
                    neighbor[1] * 2
                ]  # Bottom-left tile of neighbor block
                if not self._can_move_between_tiles(
                    current_tile, neighbor_tile, tileset
                ):
                    continue

                tentative_g_score = g_score[current] + 1
                if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g_score
                    f_score[neighbor] = tentative_g_score + heuristic(neighbor, end)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))

        # If target unreachable, return path to closest point
        if closest_point != start:
            path = reconstruct_path(closest_point)
            return (
                f"Partial Success: Could not reach the exact target, but found a path to the closest reachable point.",
                path,
            )

        return (
            "Failure: No path is visible to the chosen location. You may need to explore a totally different path to get where you're trying to go.",
            [],
        )

    def get_state_from_memory(self) -> str:
        """
        Reads the game state from memory and returns a string representation of it.
        """
        reader = PokemonRedReader(self.pyboy.memory)
        memory_str = ""

        name = reader.read_player_name()
        if name == "NINTEN":
            name = "Not yet set"
        rival_name = reader.read_rival_name()
        if rival_name == "SONY":
            rival_name = "Not yet set"

        # Get valid moves
        valid_moves = self.get_valid_moves()
        valid_moves_str = ", ".join(valid_moves) if valid_moves else "None"

        memory_str += f"Player: {name}\n"
        # memory_str += f"Rival: {rival_name}\n"
        # memory_str += f"Money: ${reader.read_money()}\n"
        memory_str += f"Location: {reader.read_location()}\n"
        memory_str += f"Coordinates: {reader.read_coordinates()}\n"
        memory_str += f"Valid Moves: {valid_moves_str}\n"
        # memory_str += f"Badges: {', '.join(reader.read_badges())}\n"

        # Inventory
        # memory_str += "Inventory:\n"
        # for item, qty in reader.read_items():
        #     memory_str += f"  {item} x{qty}\n"

        # Dialog
        dialog = reader.read_dialog()
        if dialog:
            memory_str += f"Dialog: {dialog}\n"
        else:
            memory_str += "Dialog: None\n"

        # Party Pokemon
        # memory_str += "\nPokemon Party:\n"
        # for pokemon in reader.read_party_pokemon():
        #     memory_str += f"\n{pokemon.nickname} ({pokemon.species_name}):\n"
        #     memory_str += f"Level {pokemon.level} - HP: {pokemon.current_hp}/{pokemon.max_hp}\n"
        #     memory_str += f"Types: {pokemon.type1.name}{', ' + pokemon.type2.name if pokemon.type2 else ''}\n"
        #     for move, pp in zip(pokemon.moves, pokemon.move_pp, strict=True):
        #         memory_str += f"- {move} (PP: {pp})\n"
        #     if pokemon.status != StatusCondition.NONE:
        #         memory_str += f"Status: {pokemon.status.get_status_name()}\n"

        return memory_str

    def stop(self):
        self.pyboy.stop()