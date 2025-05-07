# nav.py
import logging
logger = logging.getLogger(__name__)

class Nav:
    def __init__(self, emulator):
        self.emulator = emulator

    def is_within_bounds(self):
        """
        Check if the agent's current position is within the defined global coordinate bounds.
        Only performs check when not in a dialog.
        
        Returns:
            bool: True if within bounds, False if out of bounds
        """
        # Skip check if in dialog
        dialog = self.emulator.get_active_dialog()
        if dialog is not None:
            return True
        
        # Get current coordinates
        coords = self.emulator.get_game_coords()
        if not coords or coords[0] == -1 or coords[1] == -1:  
            return True  # Assume valid if coordinates unavailable
        
        # Extract local coordinates
        x_local, y_local, map_id = coords
        
        # Convert to global coordinates
        from game_data.global_map import local_to_global
        glob_r, glob_c = local_to_global(y_local, x_local, map_id)
        
        # Check against all coordinate bounds from rewardable_coords
        include_conditions = [
            (80 >= glob_c >= 72) and (294 < glob_r <= 320),
            (69 < glob_c < 74) and (313 >= glob_r >= 295),
            (73 >= glob_c >= 72) and (220 <= glob_r <= 330),
            (75 >= glob_c >= 74) and (310 >= glob_r <= 319),
            # (glob_c >= 75 and glob_r <= 310),
            (81 >= glob_c >= 73) and (294 < glob_r <= 313),
            (73 <= glob_c <= 81) and (294 < glob_r <= 308),
            (80 >= glob_c >= 74) and (330 >= glob_r >= 284),
            (90 >= glob_c >= 89) and (336 >= glob_r >= 328),
            # Viridian Pokemon Center
            (282 >= glob_r >= 277) and glob_c == 98,
            # Pewter Pokemon Center
            (173 <= glob_r <= 178) and glob_c == 42,
            # Route 4 Pokemon Center
            (131 <= glob_r <= 136) and glob_c == 132,
            (75 <= glob_c <= 76) and (271 < glob_r < 273),
            (82 >= glob_c >= 74) and (284 <= glob_r <= 302),
            (74 <= glob_c <= 76) and (284 >= glob_r >= 277),
            (76 >= glob_c >= 70) and (266 <= glob_r <= 277),
            (76 <= glob_c <= 78) and (274 >= glob_r >= 272),
            (74 >= glob_c >= 71) and (218 <= glob_r <= 266),
            (71 >= glob_c >= 67) and (218 <= glob_r <= 235),
            (106 >= glob_c >= 103) and (228 <= glob_r <= 244),
            (116 >= glob_c >= 106) and (228 <= glob_r <= 232),
            (116 >= glob_c >= 113) and (196 <= glob_r <= 232),
            (113 >= glob_c >= 89) and (208 >= glob_r >= 196),
            (97 >= glob_c >= 89) and (188 <= glob_r <= 214),
            (102 >= glob_c >= 97) and (189 <= glob_r <= 196),
            (89 <= glob_c <= 91) and (188 >= glob_r >= 181),
            (74 >= glob_c >= 67) and (164 <= glob_r <= 184),
            (68 >= glob_c >= 67) and (186 >= glob_r >= 184),
            (64 <= glob_c <= 71) and (151 <= glob_r <= 159),
            (71 <= glob_c <= 73) and (151 <= glob_r <= 156),
            (73 <= glob_c <= 74) and (151 <= glob_r <= 164),
            (103 <= glob_c <= 74) and (157 <= glob_r <= 156),
            (80 <= glob_c <= 111) and (155 <= glob_r <= 156),
            (111 <= glob_c <= 99) and (155 <= glob_r <= 150),
            (111 <= glob_c <= 154) and (150 <= glob_r <= 153),
            (138 <= glob_c <= 154) and (153 <= glob_r <= 160),
            (153 <= glob_c <= 154) and (153 <= glob_r <= 154),
            (143 <= glob_c <= 144) and (153 <= glob_r <= 154),
            (154 <= glob_c <= 158) and (134 <= glob_r <= 145),
            (152 <= glob_c <= 156) and (145 <= glob_r <= 150),
            (42 <= glob_c <= 43) and (173 <= glob_r <= 178),
            (158 <= glob_c <= 163) and (134 <= glob_r <= 135),
            (161 <= glob_c <= 163) and (114 <= glob_r <= 128),
            (163 <= glob_c <= 169) and (114 <= glob_r <= 115),
            (114 <= glob_c <= 169) and (167 <= glob_r <= 102),
            (169 <= glob_c <= 179) and (102 <= glob_r <= 103),
            (178 <= glob_c <= 179) and (102 <= glob_r <= 95),
            (178 <= glob_c <= 163) and (95 <= glob_r <= 96),
            (164 <= glob_c <= 163) and (110 <= glob_r <= 96),
            (163 <= glob_c <= 151) and (110 <= glob_r <= 109),
            (151 <= glob_c <= 154) and (101 <= glob_r <= 109),
            (151 <= glob_c <= 152) and (101 <= glob_r <= 97),
            (153 <= glob_c <= 154) and (97 <= glob_r <= 101),
            (151 <= glob_c <= 154) and (97 <= glob_r <= 98),
            (152 <= glob_c <= 155) and (69 <= glob_r <= 81),
            (155 <= glob_c <= 169) and (80 <= glob_r <= 81),
            (168 <= glob_c <= 184) and (39 <= glob_r <= 43),
            (183 <= glob_c <= 178) and (43 <= glob_r <= 51),
            (179 <= glob_c <= 183) and (48 <= glob_r <= 59),
            (179 <= glob_c <= 158) and (59 <= glob_r <= 57),
            (158 <= glob_c <= 161) and (57 <= glob_r <= 30),
            (158 <= glob_c <= 150) and (30 <= glob_r <= 31),
            (153 <= glob_c <= 150) and (34 <= glob_r <= 31),
            (168 <= glob_c <= 254) and (134 <= glob_r <= 140),
            (282 >= glob_r >= 277) and (436 >= glob_c >= 0), # Include Viridian Pokecenter everywhere
            (173 <= glob_r <= 178) and (436 >= glob_c >= 0), # Include Pewter Pokecenter everywhere
            (131 <= glob_r <= 136) and (436 >= glob_c >= 0), # Include Route 4 Pokecenter everywhere
            (137 <= glob_c <= 197) and (82 <= glob_r <= 142), # Mt Moon Route 3
            (137 <= glob_c <= 187) and (53 <= glob_r <= 103), # Mt Moon B1F
            (137 <= glob_c <= 197) and (16 <= glob_r <= 66), # Mt Moon B2F
            (137 <= glob_c <= 436) and (82 <= glob_r <= 444),  # Most of the rest of map after Mt Moon
        ]
        
        return any(include_conditions)

    def bounds_check_tool(self, tool_call):
        """
        Tool to check if agent is within bounds and attempt to return to bounds if not.
        
        Args:
            tool_call: The tool call object
            
        Returns:
            dict: Tool result with status message
        """
        # Check if within bounds
        is_within = self.is_within_bounds()
        
        # Get position info for the message
        coords = self.emulator.get_game_coords()
        if coords and coords[0] != -1:
            x_local, y_local, map_id = coords
            from game_data.global_map import local_to_global
            glob_r, glob_c = local_to_global(y_local, x_local, map_id)
            location = self.emulator.get_location() or "Unknown"
            position = f"Location: {location}, Global Coords: ({glob_c}, {glob_r}), Local: ({x_local}, {y_local}), Map ID: {map_id}"
        else:
            position = "Position information unavailable"
        
        if is_within:
            # Agent is within bounds
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Agent is within playable bounds. {position}"}
                ]
            }
        else:
            # Agent is out of bounds
            warning = "YOU ARE OUT OF BOUNDS - RETURNING YOU TO PLAYABLE GAME AREA"
            
            # Log the warning
            logger.warning(f"{warning} {position}")
            
            # Attempt recovery through systematic movement
            recovery_attempted = False
            
            # Try valid moves first (emulator.get_valid_moves returns directions that are walkable)
            valid_moves = self.emulator.get_valid_moves()
            if valid_moves:
                for move in valid_moves:
                    self.emulator.press_buttons([move], True)
                    if self.is_within_bounds():
                        recovery_attempted = True
                        break
            
            # If still out of bounds, try cardinal directions
            if not recovery_attempted or not self.is_within_bounds():
                for direction in ["up", "down", "left", "right"]:
                    self.emulator.press_buttons([direction], True)
                    if self.is_within_bounds():
                        recovery_attempted = True
                        break
            
            # Check if we've returned to bounds
            now_within = self.is_within_bounds()
            status = "Successfully returned to playable area." if now_within else "Still outside playable area. Try moving manually."
            
            # Update message with recovery status
            if recovery_attempted:
                action_message = "Attempted recovery moves."
            else:
                action_message = "Could not determine valid recovery moves."
            
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"{warning}"},
                    {"type": "text", "text": f"{position}"},
                    {"type": "text", "text": f"{action_message} {status}"}
                ]
            }