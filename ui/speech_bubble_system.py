"""
Comic-style Speech Bubble System for Quest Text Display

This module provides a bolt-on system to render quest text in comic-style speech bubbles
on top of the existing tkinter UI when quests start and complete.
"""

import tkinter as tk
from tkinter import PhotoImage
from PIL import Image, ImageDraw, ImageFont, ImageTk
import json
import threading
import time
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any

class SpeechBubbleRenderer:
    """Renders comic-style speech bubbles with quest text"""
    
    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        self.current_bubble_id = None
        self.fade_timer = None
        self.bubble_image_ref = None  # Keep reference to prevent garbage collection
        
    def create_speech_bubble(self, text: str, bubble_type: str = "quest_start") -> Image.Image:
        """Create a comic-style speech bubble image with the given text"""
        
        # Bubble styling based on type
        if bubble_type == "quest_start":
            bg_color = (255, 248, 220, 240)  # Light yellow with transparency
            border_color = (255, 165, 0, 255)  # Orange border
            text_color = (139, 69, 19, 255)   # Dark brown text
        else:  # quest_complete
            bg_color = (144, 238, 144, 240)   # Light green with transparency
            border_color = (34, 139, 34, 255)  # Forest green border
            text_color = (0, 100, 0, 255)     # Dark green text
        
        # Text styling
        try:
            # Try to load a comic-style font
            font_paths = [
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Arial.ttf",  # macOS
                "C:/Windows/Fonts/comic.ttf",  # Windows Comic Sans
                "C:/Windows/Fonts/arial.ttf"   # Windows Arial
            ]
            
            font = None
            for font_path in font_paths:
                try:
                    font = ImageFont.truetype(font_path, 16)
                    break
                except (OSError, IOError):
                    continue
            
            if font is None:
                font = ImageFont.load_default()
                
        except Exception:
            font = ImageFont.load_default()
        
        # Text processing and wrapping
        wrapped_text = self._wrap_text(text, font, max_width=300)
        lines = wrapped_text.split('\n')
        
        # Calculate text dimensions
        line_height = 20
        text_height = len(lines) * line_height
        text_width = max(font.getbbox(line)[2] - font.getbbox(line)[0] for line in lines) if lines else 0
        
        # Bubble dimensions with padding
        padding = 20
        bubble_width = max(text_width + padding * 2, 150)
        bubble_height = text_height + padding * 2
        
        # Speech tail dimensions
        tail_width = 20
        tail_height = 15
        
        # Total image dimensions
        img_width = bubble_width + tail_width
        img_height = bubble_height + tail_height
        
        # Create image with transparency
        img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw main bubble (rounded rectangle)
        bubble_rect = [tail_width, 0, img_width, bubble_height]
        self._draw_rounded_rectangle(draw, bubble_rect, 15, bg_color, border_color)
        
        # Draw speech tail
        tail_points = [
            (tail_width, bubble_height - 30),
            (5, bubble_height + tail_height - 5),
            (tail_width, bubble_height - 15)
        ]
        draw.polygon(tail_points, fill=bg_color, outline=border_color)
        
        # Draw text
        y_offset = padding
        for line in lines:
            # Center text horizontally
            line_bbox = font.getbbox(line)
            line_width = line_bbox[2] - line_bbox[0]
            x_offset = tail_width + (bubble_width - line_width) // 2
            
            draw.text((x_offset, y_offset), line, font=font, fill=text_color)
            y_offset += line_height
        
        return img
    
    def _wrap_text(self, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        """Wrap text to fit within max_width"""
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            # Test if adding this word would exceed max width
            test_line = ' '.join(current_line + [word])
            bbox = font.getbbox(test_line)
            test_width = bbox[2] - bbox[0]
            
            if test_width <= max_width or not current_line:
                current_line.append(word)
            else:
                lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        return '\n'.join(lines)
    
    def _draw_rounded_rectangle(self, draw: ImageDraw.Draw, rect: List[int], 
                               radius: int, fill_color: Tuple[int, int, int, int], 
                               border_color: Tuple[int, int, int, int]):
        """Draw a rounded rectangle"""
        x1, y1, x2, y2 = rect
        
        # Draw the main rectangle
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill_color)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill_color)
        
        # Draw the four corners
        draw.pieslice([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=fill_color)
        draw.pieslice([x2 - 2*radius, y1, x2, y1 + 2*radius], 270, 360, fill=fill_color)
        draw.pieslice([x1, y2 - 2*radius, x1 + 2*radius, y2], 90, 180, fill=fill_color)
        draw.pieslice([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=fill_color)
        
        # Draw border
        draw.arc([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=border_color, width=2)
        draw.arc([x2 - 2*radius, y1, x2, y1 + 2*radius], 270, 360, fill=border_color, width=2)
        draw.arc([x1, y2 - 2*radius, x1 + 2*radius, y2], 90, 180, fill=border_color, width=2)
        draw.arc([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=border_color, width=2)
        
        # Border lines
        draw.line([x1 + radius, y1, x2 - radius, y1], fill=border_color, width=2)
        draw.line([x1 + radius, y2, x2 - radius, y2], fill=border_color, width=2)
        draw.line([x1, y1 + radius, x1, y2 - radius], fill=border_color, width=2)
        draw.line([x2, y1 + radius, x2, y2 - radius], fill=border_color, width=2)
    
    def show_bubble(self, text: str, bubble_type: str = "quest_start", duration: float = 4.0):
        """Display a speech bubble with the given text"""
        if not text or not text.strip():
            return
            
        try:
            # Clear any existing bubble
            self.clear_bubble()
            
            # Create the bubble image
            bubble_img = self.create_speech_bubble(text.strip(), bubble_type)
            
            # Convert to PhotoImage for tkinter
            photo = ImageTk.PhotoImage(bubble_img)
            
            # Position bubble in top-right corner with margin
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            
            if canvas_width <= 1 or canvas_height <= 1:
                # Canvas not ready, try again later
                self.canvas.after(100, lambda: self.show_bubble(text, bubble_type, duration))
                return
            
            margin = 20
            x = canvas_width - bubble_img.width - margin
            y = margin
            
            # Add bubble to canvas
            self.current_bubble_id = self.canvas.create_image(x, y, anchor=tk.NW, image=photo)
            self.bubble_image_ref = photo  # Keep reference
            
            # Set up fade timer
            if duration > 0:
                fade_start_time = duration - 1.0  # Start fading 1 second before removal
                if fade_start_time > 0:
                    self.fade_timer = self.canvas.after(int(fade_start_time * 1000), 
                                                       lambda: self._start_fade(1.0))
                else:
                    self.fade_timer = self.canvas.after(int(duration * 1000), self.clear_bubble)
        
        except Exception as e:
            print(f"Error showing speech bubble: {e}")
    
    def _start_fade(self, fade_duration: float):
        """Start fading out the bubble"""
        # For simplicity, just remove after fade duration
        # Could implement actual alpha fading with more complex image manipulation
        self.fade_timer = self.canvas.after(int(fade_duration * 1000), self.clear_bubble)
    
    def clear_bubble(self):
        """Clear the current speech bubble"""
        try:
            if self.current_bubble_id is not None:
                self.canvas.delete(self.current_bubble_id)
                self.current_bubble_id = None
            
            if self.fade_timer is not None:
                self.canvas.after_cancel(self.fade_timer)
                self.fade_timer = None
            
            self.bubble_image_ref = None
        except Exception as e:
            print(f"Error clearing speech bubble: {e}")


class SpeechBubbleManager:
    """Manages speech bubbles across multiple canvases"""
    
    def __init__(self, quest_data_file: Optional[Path] = None):
        self.renderers: Dict[str, SpeechBubbleRenderer] = {}
        self.quest_data: Dict[str, Dict] = {}
        self.last_quest_id = None
        
        # Load quest data
        if quest_data_file is None:
            quest_data_file = Path(__file__).parent.parent / "environment" / "environment_helpers" / "required_completions.json"
        
        self.load_quest_data(quest_data_file)
    
    def load_quest_data(self, quest_file: Path):
        """Load quest data from JSON file"""
        try:
            if quest_file.exists():
                with open(quest_file, 'r') as f:
                    quests = json.load(f)
                    for quest in quests:
                        quest_id = quest.get('quest_id')
                        if quest_id:
                            self.quest_data[quest_id] = quest
                print(f"Loaded {len(self.quest_data)} quests for speech bubbles")
            else:
                print(f"Quest data file not found: {quest_file}")
        except Exception as e:
            print(f"Error loading quest data: {e}")
    
    def register_canvas(self, canvas_name: str, canvas: tk.Canvas):
        """Register a canvas for speech bubble rendering"""
        self.renderers[canvas_name] = SpeechBubbleRenderer(canvas)
        print(f"Registered canvas '{canvas_name}' for speech bubbles")
    
    def show_quest_start(self, quest_id: str, canvas_names: Optional[List[str]] = None):
        """Show quest start text on specified canvases"""
        quest_data = self.quest_data.get(quest_id)
        if not quest_data:
            print(f"No quest data found for quest {quest_id}")
            return
        
        begin_text = quest_data.get('begin_quest_text', '').strip()
        if not begin_text:
            return
        
        # Default to all canvases if none specified
        if canvas_names is None:
            canvas_names = list(self.renderers.keys())
        
        # Show on specified canvases
        for canvas_name in canvas_names:
            if canvas_name in self.renderers:
                print(f"Showing quest {quest_id} start text: {begin_text}...")
                self.renderers[canvas_name].show_bubble(begin_text, "quest_start", duration=4.0)
    
    def show_quest_complete(self, quest_id: str, canvas_names: Optional[List[str]] = None):
        """Show quest completion text on specified canvases"""
        quest_data = self.quest_data.get(quest_id)
        if not quest_data:
            print(f"No quest data found for quest {quest_id}")
            return
        
        end_text = quest_data.get('end_quest_text', '').strip()
        if not end_text:
            return
        
        # Default to all canvases if none specified  
        if canvas_names is None:
            canvas_names = list(self.renderers.keys())
        
        # Show on specified canvases
        for canvas_name in canvas_names:
            if canvas_name in self.renderers:
                print(f"Showing quest {quest_id} completion text: {end_text}...")
                self.renderers[canvas_name].show_bubble(end_text, "quest_complete", duration=5.0)
    
    def handle_quest_change(self, new_quest_id: Optional[str]):
        """Handle quest changes - show start text for new quest"""
        if new_quest_id and new_quest_id != self.last_quest_id:
            # Only show start text if this is actually a new quest
            if self.last_quest_id is not None:
                # Quest changed, show start text for new quest
                self.show_quest_start(new_quest_id)
            self.last_quest_id = new_quest_id
    
    def handle_quest_completion(self, quest_id: str):
        """Handle quest completion - show end text"""
        self.show_quest_complete(quest_id)
    
    def clear_all_bubbles(self):
        """Clear all speech bubbles from all canvases"""
        for renderer in self.renderers.values():
            renderer.clear_bubble()


# Global manager instance for easy access
_global_speech_manager = None

def get_speech_bubble_manager() -> SpeechBubbleManager:
    """Get the global speech bubble manager instance"""
    global _global_speech_manager
    if _global_speech_manager is None:
        _global_speech_manager = SpeechBubbleManager()
    return _global_speech_manager

def initialize_speech_bubbles(canvases: Dict[str, tk.Canvas]):
    """Initialize speech bubble system with the given canvases"""
    manager = get_speech_bubble_manager()
    for name, canvas in canvases.items():
        manager.register_canvas(name, canvas)
    return manager 