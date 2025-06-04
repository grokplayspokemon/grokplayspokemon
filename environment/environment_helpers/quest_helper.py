# Quest Helper
"""
Simple quest-based warp blocking that integrates with existing stage_helper.blockings
and leverages the existing WARP_DICT from pokered_constants.py

AUTOMATIC PATH FOLLOWING:
- Loads coordinates from combined_quest_coordinates_continuous.json
- Automatically determines direction buttons needed for each step
- Forces player to follow the path once started (end-to-end scripting)
- Player cannot deviate from the path during quest execution

HOW TO FIND WARP INFORMATION:
1. Look in pokered_constants.py for WARP_DICT[map_name] to see all warps for a map
2. Each warp has: {'x': x, 'y': y, 'target_map_name': 'TARGET_MAP', 'warp_id': warp_id}
3. Use format: ['SOURCE_MAP', 'TARGET_MAP@WARP_ID'] to block specific warps
4. Use format: ['MAP_NAME', 'north/south/east/west'] to block map edges
"""

from typing import Dict, List, Optional, Tuple, Any
import json
from pathlib import Path
from environment.data.recorder_data.global_map import local_to_global

# Quest-specific blocking rules that work with existing stage_helper.blockings format
# Format: [map_name, direction_or_target_warp]
# - For map edges: 'north', 'south', 'east', 'west' 
# - For specific warps: 'TARGET_MAP@WARP_ID'

QUEST_BLOCKING_RULES = {
    2: [  # Quest 002 - Leave Player's House
        ['REDS_HOUSE_1F', 'REDS_HOUSE_2F@0'],        
    ],
    
    3: [  # Quest 003 - Stay out of Player's House
        ['PALLET_TOWN', 'REDS_HOUSE_1F@1'],  # Block entering Red's house from Pallet Town (warp 0 leads to REDS_HOUSE_1F@1)
        ['PALLET_TOWN', 'BLUES_HOUSE@1'],     # Block entering Blue's house from Pallet Town (warp 1 leads to BLUES_HOUSE@1)
    ],
    
    15: [  # Quest 015 - Get Town Map from Blue's sister
        ['BLUES_HOUSE', 'PALLET_TOWN@1']  # Block leaving Blue's house until map obtained
    ],
    
    # Example quest blocks using actual WARP_DICT data:
    # (These are examples - adjust based on your actual quest needs)
    
    # 5: [  # Example: Block leaving Oak's Lab until Pokemon received
    #     ['OAKS_LAB', 'PALLET_TOWN@2']  # Warp from Oak's Lab back to Pallet Town
    # ],
    
    # 10: [  # Example: Block certain dangerous routes until ready
    #     ['VIRIDIAN_CITY', 'ROUTE_22@0'],  # Block going west to Route 22
    #     ['ROUTE_22', 'ROUTE_23@0']        # Block going to Victory Road area
    # ],
    
    # 20: [  # Example: Block Pokemon Center until certain condition
    #     ['VIRIDIAN_POKECENTER', 'VIRIDIAN_CITY@0']  # Block leaving Pokemon Center
    # ],
    
    # Advanced example with multiple blocks for a complex quest:
    # 25: [
    #     ['ROUTE_1', 'VIRIDIAN_CITY@0'],    # Block going north until ready
    #     ['PALLET_TOWN', 'ROUTE_1@0'],      # Block going back south  
    #     ['PLAYERS_HOUSE_1F', 'PALLET_TOWN@0']  # Keep player inside house
    # ],
    
    # Add more quest blocks as needed...
    # To find the right warp info:
    # 1. print(WARP_DICT['MAP_NAME']) to see all warps for a map
    # 2. Look for the warp at coordinates where you want to block
    # 3. Use the target_map_name and warp_id from that warp entry
}

# Quest-specific scripted movement rules (legacy - now enhanced with path following)
QUEST_MOVEMENT_RULES = {
    5: [  # Quest 005 - Force UP movement at specific coordinates
        {'condition': {'global_coords': (338, 94)}, 'action': 'up'}
    ],
    
    12: [  # Quest 012 - Talk to Oak
        {'condition': {'global_coords': (348, 110)}, 'action': 'a'}
    ],
    
    15: [  # Quest 015 - Get Town Map from Blue's sister  
        # Force A press to get Town Map when standing in front of Blue's sister
        {'condition': {'global_coords': (348, 97), 'map_id': 39, 'item_check': {'item': 'TOWN MAP', 'has': False}}, 'action': 'a'},
        # Handle warp entry sequence - set pending B presses
        {'condition': {'global_coords': (344, 97)}, 'action': 'up', 'set_pending_b': 3},
        # Handle pending B presses
        {'condition': {'pending_b_presses': '>0'}, 'action': 'b', 'decrement_pending_b': True}
    ],
    
    # Add more quest movement rules as needed...
    # Format: 
    # quest_id: [
    #     {'condition': {...}, 'action': 'action_name', 'additional_params': value},
    #     ...
    # ]
    # 
    # Available conditions:
    # - 'local_coords': (x, y) - local map coordinates
    # - 'global_coords': (y, x) - global coordinates 
    # - 'map_id': int - specific map ID
    # - 'item_check': {'item': 'ITEM_NAME', 'has': True/False} - check item possession
    # - 'pending_b_presses': '>0' or int - check pending B press count
    #
    # Available actions: 'down', 'left', 'right', 'up', 'a', 'b', 'path', 'start'
    # 
    # Additional parameters:
    # - 'set_pending_b': int - set pending B press count  
    # - 'decrement_pending_b': True - decrement pending B press count
}

class QuestPathFollower:
    """Automatic path following system using combined_quest_coordinates_continuous.json"""
    
    def __init__(self, env):
        self.env = env
        
        # Path following state
        self.quest_paths = {}  # Quest ID -> List of (gy, gx) coordinates
        self.current_quest_path = []  # Current active path
        self.current_path_index = 0  # Current position in path
        self.path_following_active = False  # Whether automatic path following is enabled
        
        # Action mapping
        self.action_mapping = {
            'down': 0,
            'left': 1, 
            'right': 2,
            'up': 3,
            'a': 4,
            'b': 5,
            'path': 6,
            'start': 7
        }
        
        # Load path data
        self._load_quest_paths()
    
    def _load_quest_paths(self):
        """Load quest coordinate paths from combined_quest_coordinates_continuous.json"""
        coords_file = Path(__file__).parent / "quest_paths" / "combined_quest_coordinates_continuous.json"
        
        if not coords_file.exists():
            print(f"QuestPathFollower: Warning - combined coordinate file not found: {coords_file}")
            return
            
        try:
            with open(coords_file, 'r') as f:
                data = json.load(f)
            
            quest_start_indices = data.get("quest_start_indices", {})
            all_coordinates = data.get("coordinates", [])
            
            # Extract coordinates for each quest
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
                # FIXED: Coordinates in combined file are ALREADY global coordinates
                # Do NOT convert them with local_to_global - they are already (gy, gx) format
                self.quest_paths[quest_id] = [(coord[0], coord[1]) for coord in quest_coords]
            
            print(f"QuestPathFollower: Loaded {len(self.quest_paths)} quest paths")
            
        except Exception as e:
            print(f"QuestPathFollower: Error loading quest paths: {e}")
            self.quest_paths = {}
    
    def start_path_following(self, quest_id: int) -> bool:
        """Start automatic path following for the specified quest"""
        if quest_id not in self.quest_paths:
            print(f"QuestPathFollower: No path data for quest {quest_id}")
            return False
            
        self.current_quest_path = self.quest_paths[quest_id]
        self.current_path_index = 0
        self.path_following_active = True
        
        print(f"QuestPathFollower: Started path following for quest {quest_id}")
        print(f"QuestPathFollower: Path has {len(self.current_quest_path)} coordinates")
        if self.current_quest_path:
            first_coord = self.current_quest_path[0]
            # Validate coordinates are reasonable (global coordinates should be < 400)
            if first_coord[0] < 400 and first_coord[1] < 400:
                print(f"QuestPathFollower: Starting at global {first_coord}")
            else:
                print(f"QuestPathFollower: WARNING - Invalid starting coordinates {first_coord} (too high for global coords)")
        else:
            print(f"QuestPathFollower: No coordinates loaded")
        
        return True
    
    def stop_path_following(self):
        """Stop automatic path following"""
        self.path_following_active = False
        self.current_quest_path = []
        self.current_path_index = 0
        print(f"QuestPathFollower: Stopped path following")
    
    def get_next_movement_action(self) -> Optional[int]:
        """
        Calculate the next movement action needed to follow the quest path.
        Returns the action integer, or None if no movement needed.
        """
        # CRITICAL: Do not override actions when dialog is active - player needs to interact
        try:
            dialog = self.env.read_dialog()
            if dialog and dialog.strip():
                print(f"QuestPathFollower: Dialog active, pausing path following for player interaction")
                return None
        except Exception as e:
            print(f"QuestPathFollower: Error checking dialog: {e}")
            
        if not self.path_following_active or not self.current_quest_path:
            return None
            
        if self.current_path_index >= len(self.current_quest_path):
            print(f"QuestPathFollower: Reached end of path, stopping")
            self.stop_path_following()
            return None
        
        # Get current player position
        x, y, map_id = self.env.get_game_coords()
        current_global = local_to_global(y, x, map_id)
        
        # Get target coordinate - these are ALREADY global coordinates 
        target_global = self.current_quest_path[self.current_path_index]
        
        print(f"QuestPathFollower: Current pos {current_global}, target {target_global} (index {self.current_path_index})")
        
        # Check if we're at the target
        if current_global == target_global:
            print(f"QuestPathFollower: Reached target {target_global}, advancing to next")
            self.current_path_index += 1
            
            # Check if we've completed the path
            if self.current_path_index >= len(self.current_quest_path):
                print(f"QuestPathFollower: Path completed!")
                self.stop_path_following()
                return None
                
            # Get next target
            target_global = self.current_quest_path[self.current_path_index]
            print(f"QuestPathFollower: New target {target_global}")
        
        # Calculate direction needed  
        # Note: global coordinates are (gy, gx) format from local_to_global
        dy = target_global[0] - current_global[0]  # target_gy - current_gy (down = positive)
        dx = target_global[1] - current_global[1]  # target_gx - current_gx (right = positive)
        
        # Determine action - prioritize the larger movement first
        if abs(dy) > abs(dx):
            # Vertical movement has priority
            if dy > 0:
                action_str = 'down'
            else:
                action_str = 'up'
        elif abs(dx) > abs(dy):
            # Horizontal movement has priority
            if dx > 0:
                action_str = 'right'
            else:
                action_str = 'left'
        else:
            # Equal movement or no movement needed
            if dy > 0:
                action_str = 'down'
            elif dy < 0:
                action_str = 'up'
            elif dx > 0:
                action_str = 'right'
            elif dx < 0:
                action_str = 'left'
            else:
                # No movement needed
                return None
        
        action_int = self.action_mapping[action_str]
        print(f"QuestPathFollower: Moving {action_str} (action {action_int}) to reach {target_global}")
        
        return action_int
    
    def is_on_quest_path(self, global_coords: Tuple[int, int]) -> bool:
        """Check if the given global coordinates are on the current quest path"""
        if not self.path_following_active or not self.current_quest_path:
            return False
        return global_coords in self.current_quest_path
    
    def get_path_status(self) -> Dict[str, Any]:
        """Get current path following status for debugging"""
        return {
            'active': self.path_following_active,
            'path_length': len(self.current_quest_path),
            'current_index': self.current_path_index,
            'current_target': self.current_quest_path[self.current_path_index] if self.current_path_index < len(self.current_quest_path) else None,
            'progress': f"{self.current_path_index}/{len(self.current_quest_path)}"
        }

class QuestWarpBlocker:
    """Quest-based warp blocker and movement controller that integrates with stage_manager"""
    
    def __init__(self, env):
        self.env = env
        self.active_quest_blocks = []
        self.active_quest_movements = []
        
        # Initialize automatic path follower
        self.path_follower = QuestPathFollower(env)
    
    def update_quest_blocks(self, quest_id: Optional[int]):
        """Update blocking rules and scripted movements based on current quest"""
        # FIXED: Clear ALL quest-related blocks first before adding new ones
        # This ensures proper cleanup when transitioning between quests
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'blockings'):
            # Remove all quest-related blocks (blocks that contain '@' for warp blocks)
            original_blockings = self.env.stage_manager.blockings.copy()
            for block in original_blockings:
                if len(block) >= 2 and ('@' in block[1] or block[1] in ['north', 'south', 'east', 'west']):
                    # This is likely a quest-related block, remove it
                    if block in self.env.stage_manager.blockings:
                        self.env.stage_manager.blockings.remove(block)
                        print(f"QuestWarpBlocker: Removed old quest block {block}")
        
        # Clear active movements too
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_movements'):
            # Clear all scripted movements (they should be quest-specific)
            if hasattr(self.env.stage_manager, 'scripted_movements'):
                old_movements = self.env.stage_manager.scripted_movements.copy()
                self.env.stage_manager.scripted_movements.clear()
                if old_movements:
                    print(f"QuestWarpBlocker: Cleared {len(old_movements)} old scripted movements")
        
        if quest_id is None:
            self.active_quest_blocks = []
            self.active_quest_movements = []
            self.path_follower.stop_path_following()
            print("QuestWarpBlocker: No quest active, all blocks cleared")
            return
            
        # Get blocks for this quest
        self.active_quest_blocks = QUEST_BLOCKING_RULES.get(quest_id, [])
        
        # Get movements for this quest
        self.active_quest_movements = QUEST_MOVEMENT_RULES.get(quest_id, [])
        
        # DISABLED: Automatic path following conflicts with environment's PATH_FOLLOW_ACTION
        # Environment.step() handles PATH_FOLLOW_ACTION directly using Navigator coordinates
        # if self.path_follower.start_path_following(quest_id):
        #     print(f"QuestWarpBlocker: Started automatic path following for quest {quest_id}")
        #     
        #     # Generate comprehensive movement blocking to prevent deviation from path
        #     path_movement_rules = self._generate_path_following_movements(quest_id)
        #     self.active_quest_movements.extend(path_movement_rules)
        #     print(f"QuestWarpBlocker: Added {len(path_movement_rules)} path following movements")
        print(f"QuestWarpBlocker: Path following disabled - using environment's PATH_FOLLOW_ACTION instead")
        
        # Add new quest blocks to stage manager
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'blockings'):
            for block in self.active_quest_blocks:
                if block not in self.env.stage_manager.blockings:
                    self.env.stage_manager.blockings.append(block)
                    print(f"QuestWarpBlocker: Added block {block} for quest {quest_id}")
        
        # Add new quest movements to stage manager
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_movements'):
            for movement in self.active_quest_movements:
                if movement not in self.env.stage_manager.scripted_movements:
                    self.env.stage_manager.scripted_movements.append(movement)
                    print(f"QuestWarpBlocker: Added scripted movement for quest {quest_id}")
        
        print(f"QuestWarpBlocker: Quest {quest_id} setup complete. Active blocks: {len(self.active_quest_blocks)}, Path following: {self.path_follower.path_following_active}")
    
    def _generate_path_following_movements(self, quest_id: int) -> List[Dict[str, Any]]:
        """
        Generate scripted movements that force the player to follow the quest path.
        This creates a comprehensive movement override system.
        """
        if quest_id not in self.path_follower.quest_paths:
            return []
            
        # Create a single comprehensive movement rule that handles all path following
        path_movement = {
            'condition': {'always': True, 'quest_path_active': True},
            'action': 'path_follow',
            'quest_id': quest_id,
            'description': f'Automatic path following for quest {quest_id}'
        }
        
        return [path_movement]
    
    def handle_path_following_movement(self, original_action: int) -> int:
        """
        Handle automatic path following movement override.
        Called by stage_manager when path following is active.
        """
        # CRITICAL: Do not override actions when dialog is active - player needs to interact
        try:
            dialog = self.env.read_dialog()
            if dialog and dialog.strip():
                print(f"QuestWarpBlocker: Dialog active, allowing player action {original_action} for interaction")
                return original_action
        except Exception as e:
            print(f"QuestWarpBlocker: Error checking dialog: {e}")
            
        if not self.path_follower.path_following_active:
            return original_action
            
        # Get the next required movement from path follower
        path_action = self.path_follower.get_next_movement_action()
        
        if path_action is not None:
            print(f"QuestWarpBlocker: Path following overriding action {original_action} -> {path_action}")
            return path_action
        else:
            # No specific path movement needed, allow original action
            # But check if original action would take us off the path
            if self._would_action_deviate_from_path(original_action):
                print(f"QuestWarpBlocker: Action {original_action} would deviate from path, blocking")
                return self._get_noop_action()
            else:
                return original_action
    
    def _would_action_deviate_from_path(self, action: int) -> bool:
        """Check if the given action would take the player off the quest path"""
        if not self.path_follower.path_following_active:
            return False
            
        # Get current position
        x, y, map_id = self.env.get_game_coords()
        current_global = local_to_global(y, x, map_id)
        
        # Calculate where the action would take us
        new_x, new_y = x, y
        if action == 0:  # down
            new_y += 1
        elif action == 1:  # left
            new_x -= 1
        elif action == 2:  # right
            new_x += 1
        elif action == 3:  # up
            new_y -= 1
        else:
            return False  # Non-movement actions are allowed
            
        new_global = local_to_global(new_y, new_x, map_id)
        
        # Check if new position would be on the quest path
        return not self.path_follower.is_on_quest_path(new_global)
    
    def _get_noop_action(self) -> int:
        """Get a no-op action that doesn't move the player"""
        return 7  # START button - typically safe no-op
    
    def remove_quest_blocks(self, quest_id: Optional[int]):
        """Remove quest-specific blocks and movements when quest completes"""
        if quest_id is None:
            return
            
        quest_blocks = QUEST_BLOCKING_RULES.get(quest_id, [])
        quest_movements = QUEST_MOVEMENT_RULES.get(quest_id, [])
        
        # Remove from stage_helper.blockings if it exists
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'blockings'):
            for block in quest_blocks:
                if block in self.env.stage_manager.blockings:
                    self.env.stage_manager.blockings.remove(block)
                    print(f"QuestWarpBlocker: Removed block {block} for quest {quest_id}")
        
        # Remove from stage_helper.scripted_movements if it exists
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_movements'):
            for movement in quest_movements:
                if movement in self.env.stage_manager.scripted_movements:
                    self.env.stage_manager.scripted_movements.remove(movement)
                    print(f"QuestWarpBlocker: Removed scripted movement {movement} for quest {quest_id}")
        
        # Stop path following
        self.path_follower.stop_path_following()
        
        # Clear active blocks and movements
        self.active_quest_blocks = []
        self.active_quest_movements = []
    
    def get_quest_blocks(self, quest_id: int) -> List[List[str]]:
        """Get blocking rules for a specific quest"""
        return QUEST_BLOCKING_RULES.get(quest_id, [])
    
    def get_quest_movements(self, quest_id: int) -> List[Dict]:
        """Get scripted movement rules for a specific quest"""
        return QUEST_MOVEMENT_RULES.get(quest_id, [])
    
    def add_temporary_block(self, map_name: str, direction_or_warp: str):
        """Add a temporary block (useful for dynamic quest conditions)"""
        block = [map_name, direction_or_warp]
        if block not in self.active_quest_blocks:
            self.active_quest_blocks.append(block)
            
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'blockings'):
            if block not in self.env.stage_manager.blockings:
                self.env.stage_manager.blockings.append(block)
                print(f"QuestWarpBlocker: Added temporary block {block}")
    
    def remove_temporary_block(self, map_name: str, direction_or_warp: str):
        """Remove a temporary block"""
        block = [map_name, direction_or_warp]
        if block in self.active_quest_blocks:
            self.active_quest_blocks.remove(block)
            
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'blockings'):
            if block in self.env.stage_manager.blockings:
                self.env.stage_manager.blockings.remove(block)
                print(f"QuestWarpBlocker: Removed temporary block {block}")

# Helper function to explore WARP_DICT data (for development)
def print_warp_info_for_map(map_name: str):
    """Helper function to see all warps for a map - useful for development"""
    try:
        from environment.data.environment_data.pokered_constants import WARP_DICT
        if map_name in WARP_DICT:
            print(f"Warps for {map_name}:")
            for i, warp in enumerate(WARP_DICT[map_name]):
                print(f"  {i}: ({warp['x']}, {warp['y']}) -> {warp['target_map_name']}@{warp['warp_id']}")
        else:
            print(f"No warp data found for {map_name}")
    except ImportError:
        print("Could not import WARP_DICT from pokered_constants")

# Helper function to find the right warp block format for PLAYERS_HOUSE_1F
def debug_players_house_warp():
    """Debug helper to show how to block entry into PLAYERS_HOUSE_1F"""
    print("=== DEBUGGING PLAYERS_HOUSE_1F WARP BLOCKING ===")
    print("To block entry into PLAYERS_HOUSE_1F from PALLET_TOWN:")
    print()
    print("1. First, let's see PALLET_TOWN warps:")
    print_warp_info_for_map("PALLET_TOWN")
    print()
    print("2. Then, let's see PLAYERS_HOUSE_1F warps:")
    print_warp_info_for_map("PLAYERS_HOUSE_1F")
    print()
    print("3. To block entry, use format: ['PALLET_TOWN', 'PLAYERS_HOUSE_1F@WARP_ID']")
    print("   where WARP_ID is the ID of the warp that leads to PLAYERS_HOUSE_1F")
    print()
    print("4. Example blocking rule added to QUEST_BLOCKING_RULES:")
    print("   1: [['PALLET_TOWN', 'PLAYERS_HOUSE_1F@0']]")
    print("=====================================")

def debug_quest_blocking_system(env):
    """Comprehensive debug function to check why blocking isn't working"""
    print("=== QUEST BLOCKING SYSTEM DEBUG ===")
    
    # Check if quest manager exists and what quest is active
    quest_manager = getattr(env, 'quest_manager', None)
    if quest_manager:
        print(f"✓ Quest Manager found")
        print(f"  Current Quest ID: {quest_manager.current_quest_id}")
        print(f"  Quest active: {quest_manager.is_quest_active()}")
        
        # Check warp blocker
        if hasattr(quest_manager, 'warp_blocker'):
            blocker = quest_manager.warp_blocker
            print(f"✓ Warp Blocker found")
            print(f"  Active quest blocks: {blocker.active_quest_blocks}")
            print(f"  Path following active: {blocker.path_follower.path_following_active}")
            
            # Check path follower status
            if blocker.path_follower.path_following_active:
                status = blocker.path_follower.get_path_status()
                print(f"  Path following status: {status}")
            
            # Check if current quest has any blocks defined
            current_quest = quest_manager.current_quest_id
            if current_quest:
                quest_blocks = QUEST_BLOCKING_RULES.get(current_quest, [])
                print(f"  Quest {current_quest} blocks defined: {quest_blocks}")
            else:
                print("  No current quest - no blocks should be active")
        else:
            print("✗ Warp Blocker NOT found")
    else:
        print("✗ Quest Manager NOT found")
    
    # Check stage manager/helper
    stage_manager = getattr(env, 'stage_manager', None)
    if stage_manager:
        print(f"✓ Stage Manager found")
        if hasattr(stage_manager, 'blockings'):
            print(f"  Current blockings: {stage_manager.blockings}")
        else:
            print("✗ Stage Manager has no 'blockings' attribute")
    else:
        print("✗ Stage Manager NOT found")
    
    # Check current player position and map
    try:
        x, y, map_id = env.get_game_coords()
        print(f"✓ Player position: Map {map_id}, Local ({x}, {y})")
        
        # Try to get map name
        from environment.data.environment_data.map import MapIds
        map_name = None
        for name, id_val in MapIds.__dict__.items():
            if id_val == map_id and not name.startswith('_'):
                map_name = name
                break
        print(f"  Map name: {map_name}")
        
    except Exception as e:
        print(f"✗ Could not get player position: {e}")
    
    print("=====================================")
    return quest_manager, stage_manager 

def test_quest_3_blocking():
    """Simple test to verify Quest 3 blocking works with correct warp names"""
    print("=== TESTING QUEST 3 BLOCKING ===")
    
    # Show the Quest 3 blocks
    quest_3_blocks = QUEST_BLOCKING_RULES.get(3, [])
    print(f"Quest 3 blocking rules: {quest_3_blocks}")
    
    # Show the actual warp data
    print("\nActual warp data:")
    print_warp_info_for_map("PALLET_TOWN")
    
    # Verify the blocking format matches the warp data
    for block in quest_3_blocks:
        map_name, warp_target = block
        print(f"\nChecking block: {block}")
        if '@' in warp_target:
            target_map, warp_id = warp_target.split('@')
            print(f"  Blocks entry from {map_name} to {target_map} via warp {warp_id}")
            
            # Check if this matches actual warp data
            try:
                from environment.data.environment_data.pokered_constants import WARP_DICT
                if map_name in WARP_DICT:
                    warps = WARP_DICT[map_name]
                    for i, warp in enumerate(warps):
                        if warp['target_map_name'] == target_map and warp['warp_id'] == int(warp_id):
                            print(f"  ✅ MATCH FOUND: Warp {i} at ({warp['x']}, {warp['y']}) leads to {target_map}@{warp_id}")
                            break
                    else:
                        print(f"  ❌ NO MATCH: No warp found leading to {target_map}@{warp_id}")
                else:
                    print(f"  ❌ NO MAP DATA: {map_name} not found in WARP_DICT")
            except ImportError:
                print(f"  ⚠️ Cannot import WARP_DICT to verify")
    
    print("=== TEST COMPLETE ===")

def test_quest_blocking_integration():
    """Test the integration between quest helper and stage manager"""
    print("=== TESTING QUEST BLOCKING INTEGRATION ===")
    
    # Check if the current blocking rules are correctly formatted
    for quest_id, blocks in QUEST_BLOCKING_RULES.items():
        print(f"Quest {quest_id}: {blocks}")
        for block in blocks:
            if len(block) != 2:
                print(f"  ❌ Invalid block format: {block}")
            else:
                map_name, target = block
                if '@' in target:
                    parts = target.split('@')
                    if len(parts) == 2:
                        target_map, warp_id = parts
                        try:
                            int(warp_id)  # Verify warp_id is a number
                            print(f"  ✅ Valid warp block: {map_name} -> {target_map}@{warp_id}")
                        except ValueError:
                            print(f"  ❌ Invalid warp_id: {warp_id} must be a number")
                    else:
                        print(f"  ❌ Invalid warp format: {target}")
                elif target in ['north', 'south', 'east', 'west']:
                    print(f"  ✅ Valid edge block: {map_name} -> {target}")
                else:
                    print(f"  ❌ Invalid target format: {target}")
    
    print("=== INTEGRATION TEST COMPLETE ===")

def test_automatic_path_following():
    """Test the automatic path following system"""
    print("=== TESTING AUTOMATIC PATH FOLLOWING ===")
    
    class MockEnv:
        def get_game_coords(self):
            return (10, 15, 0)  # x, y, map_id
    
    env = MockEnv()
    path_follower = QuestPathFollower(env)
    
    print(f"Loaded quest paths: {len(path_follower.quest_paths)}")
    
    # Test starting path following for quest 5
    if 5 in path_follower.quest_paths:
        print(f"Quest 5 path: {len(path_follower.quest_paths[5])} coordinates")
        success = path_follower.start_path_following(5)
        print(f"Started path following: {success}")
        
        status = path_follower.get_path_status()
        print(f"Path status: {status}")
    
    print("=== PATH FOLLOWING TEST COMPLETE ===") 