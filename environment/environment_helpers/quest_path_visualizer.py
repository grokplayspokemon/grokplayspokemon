import json
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from PIL import Image, ImageDraw

# Color palette for quests - each quest gets a distinct color
QUEST_COLORS = [
    (255, 0, 0),     # Quest 1: Red
    (0, 255, 0),     # Quest 2: Green  
    (0, 0, 255),     # Quest 3: Blue
    (255, 255, 0),   # Quest 4: Yellow
    (255, 0, 255),   # Quest 5: Magenta
    (0, 255, 255),   # Quest 6: Cyan
    (255, 128, 0),   # Quest 7: Orange
    (128, 0, 255),   # Quest 8: Purple
    (255, 192, 203), # Quest 9: Pink
    (0, 128, 0),     # Quest 10: Dark Green
]

class QuestPathVisualizer:
    def __init__(self, quest_coords_file: Optional[str] = None):
        """
        Initialize the quest path visualizer.
        
        Args:
            quest_coords_file: Path to quest coordinates file. If None, uses default path.
        """
        if quest_coords_file is None:
            # Use the combined quest coordinates file
            base_dir = Path(__file__).parent
            quest_coords_file = str(base_dir / "quest_paths" / "combined_quest_coordinates_continuous.json")
        
        self.quest_coords_file = quest_coords_file
        self.quest_coordinates = {}
        self.quest_data = None
        
        # Load quest coordinates
        self._load_quest_coordinates()
        
    def _load_quest_coordinates(self):
        """Load quest coordinates from the file."""
        try:
            coords_path = Path(self.quest_coords_file)
            if not coords_path.exists():
                print(f"QuestVisualizer: Warning - Quest coordinates file not found: {coords_path}")
                return
                
            with open(coords_path, 'r') as f:
                self.quest_data = json.load(f)
            
            # Extract coordinates for each quest
            quest_start_indices = self.quest_data.get("quest_start_indices", {})
            all_coordinates = self.quest_data.get("coordinates", [])
            
            for quest_id_str, start_idx in quest_start_indices.items():
                quest_id = int(quest_id_str)
                
                # Find end index by looking for the next quest
                end_idx = len(all_coordinates)
                sorted_quest_ids = sorted([int(k) for k in quest_start_indices.keys()])
                
                current_quest_idx = sorted_quest_ids.index(quest_id)
                if current_quest_idx + 1 < len(sorted_quest_ids):
                    next_quest_id = sorted_quest_ids[current_quest_idx + 1]
                    end_idx = quest_start_indices[str(next_quest_id)]
                
                quest_coords = all_coordinates[start_idx:end_idx]
                # Convert to (y, x) tuples
                self.quest_coordinates[quest_id] = [(coord[0], coord[1]) for coord in quest_coords]
            
            print(f"QuestVisualizer: Loaded {len(self.quest_coordinates)} quest paths from {coords_path}")
            
        except Exception as e:
            print(f"QuestVisualizer: Error loading quest coordinates: {e}")
            self.quest_coordinates = {}
    
    def get_quest_color(self, quest_id: int) -> Tuple[int, int, int]:
        """Get the color for a specific quest."""
        if quest_id <= len(QUEST_COLORS):
            return QUEST_COLORS[quest_id - 1]
        else:
            # Generate a color for quests beyond the predefined palette
            np.random.seed(quest_id)  # Consistent color for same quest
            return tuple(np.random.randint(0, 256, 3))
    
    def overlay_quest_paths_on_map(self, base_map: np.ndarray, quest_ids: List[int] = None, 
                                   line_width: int = 2, point_radius: int = 1) -> np.ndarray:
        """
        Overlay quest paths on a map array.
        
        Args:
            base_map: Base map as numpy array (H, W) or (H, W, C)
            quest_ids: List of quest IDs to visualize. If None, uses [1,2,3,4,5]
            line_width: Width of path lines
            point_radius: Radius of coordinate points
            
        Returns:
            Map with quest paths overlaid
        """
        if quest_ids is None:
            quest_ids = [1, 2, 3, 4, 5]
        
        # Convert base map to PIL Image for drawing
        if len(base_map.shape) == 2:
            # Grayscale - convert to RGB
            map_img = Image.fromarray(base_map, mode='L').convert('RGB')
        elif len(base_map.shape) == 3:
            if base_map.shape[2] == 3:
                map_img = Image.fromarray(base_map.astype(np.uint8), mode='RGB')
            elif base_map.shape[2] == 4:
                map_img = Image.fromarray(base_map.astype(np.uint8), mode='RGBA')
            else:
                # Convert to RGB
                map_img = Image.fromarray(base_map[:,:,0], mode='L').convert('RGB')
        else:
            raise ValueError(f"Unsupported map shape: {base_map.shape}")
        
        draw = ImageDraw.Draw(map_img)
        
        # Draw each quest path
        for quest_id in quest_ids:
            if quest_id not in self.quest_coordinates:
                print(f"QuestVisualizer: Warning - No coordinates found for quest {quest_id}")
                continue
            
            coords = self.quest_coordinates[quest_id]
            color = self.get_quest_color(quest_id)
            
            if not coords:
                continue
                
            # Draw points and lines
            for i, (y, x) in enumerate(coords):
                # Draw point
                x1, y1 = x - point_radius, y - point_radius
                x2, y2 = x + point_radius, y + point_radius
                draw.ellipse([x1, y1, x2, y2], fill=color, outline=color)
                
                # Draw line to next point
                if i > 0:
                    prev_y, prev_x = coords[i-1]
                    draw.line([(prev_x, prev_y), (x, y)], fill=color, width=line_width)
        
        # Convert back to numpy array
        return np.array(map_img)
    
    def render_quest_paths_on_full_kanto_map(self, quest_ids: List[int] = None, 
                                             output_path: str = None) -> str:
        """
        Render quest paths on the full Kanto map.
        
        Args:
            quest_ids: List of quest IDs to visualize. If None, uses [1,2,3,4,5]
            output_path: Path to save output. If None, auto-generates path.
            
        Returns:
            Path to saved image
        """
        if quest_ids is None:
            quest_ids = [1, 2, 3, 4, 5]
        
        # Find the full Kanto map
        base_dir = Path(__file__).parent.parent.parent
        kanto_map_path = base_dir / "environment" / "data" / "environment_data" / "full_kanto_map.png"
        
        if not kanto_map_path.exists():
            raise FileNotFoundError(f"Full Kanto map not found: {kanto_map_path}")
        
        # Load the map
        kanto_map = Image.open(kanto_map_path).convert("RGBA")
        print(f"QuestVisualizer: Loaded Kanto map: {kanto_map.size} pixels")
        
        # Create drawing canvas
        map_with_paths = kanto_map.copy()
        draw = ImageDraw.Draw(map_with_paths)
        
        # Draw quest paths
        for quest_id in quest_ids:
            if quest_id not in self.quest_coordinates:
                print(f"QuestVisualizer: Warning - No coordinates found for quest {quest_id}")
                continue
            
            coords = self.quest_coordinates[quest_id]
            color = self.get_quest_color(quest_id)
            
            print(f"QuestVisualizer: Drawing Quest {quest_id} with {len(coords)} coordinates, color: {color}")
            
            if not coords:
                continue
                
            # Draw the path
            for i, (y, x) in enumerate(coords):
                # Draw a small circle for each coordinate
                radius = 2
                x1, y1 = x - radius, y - radius
                x2, y2 = x + radius, y + radius
                draw.ellipse([x1, y1, x2, y2], fill=color, outline=color)
                
                # Connect consecutive coordinates with lines
                if i > 0:
                    prev_y, prev_x = coords[i-1]
                    draw.line([(prev_x, prev_y), (x, y)], fill=color, width=2)
        
        # Save the result
        if output_path is None:
            timestamp = "__quest_visualization"
            output_dir = base_dir / "quest_visualizations"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / f"kanto_with_quests{timestamp}.png")
        
        map_with_paths.save(output_path)
        print(f"QuestVisualizer: Quest paths rendered and saved to: {output_path}")
        
        return output_path
    
    def get_quest_coordinates_for_id(self, quest_id: int) -> List[Tuple[int, int]]:
        """Get coordinates for a specific quest ID."""
        return self.quest_coordinates.get(quest_id, [])
    
    def get_available_quest_ids(self) -> List[int]:
        """Get list of available quest IDs."""
        return sorted(self.quest_coordinates.keys())
    
    def create_legend(self, quest_ids: List[int] = None, output_path: str = None) -> str:
        """Create a legend image showing quest colors."""
        if quest_ids is None:
            quest_ids = [1, 2, 3, 4, 5]
            
        if output_path is None:
            base_dir = Path(__file__).parent.parent.parent
            output_dir = base_dir / "quest_visualizations"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / "quest_legend.png")
        
        # Create legend image
        legend_img = Image.new("RGBA", (250, 50 + len(quest_ids) * 25), (255, 255, 255, 255))
        draw = ImageDraw.Draw(legend_img)
        
        # Title
        draw.text((10, 10), "Quest Path Legend:", fill=(0, 0, 0))
        
        # Color indicators
        for i, quest_id in enumerate(quest_ids):
            color = self.get_quest_color(quest_id)
            y_pos = 35 + i * 25
            
            # Draw color square
            draw.rectangle([10, y_pos, 30, y_pos + 20], fill=color, outline=(0, 0, 0))
            
            # Quest label with coordinate count
            coord_count = len(self.quest_coordinates.get(quest_id, []))
            draw.text((40, y_pos + 5), f"Quest {quest_id} ({coord_count} coords)", fill=(0, 0, 0))
        
        legend_img.save(output_path)
        print(f"QuestVisualizer: Legend saved to: {output_path}")
        
        return output_path 