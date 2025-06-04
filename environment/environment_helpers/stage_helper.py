# Stage Manager for Pokemon Red
"""
Stage Manager: Handles stage-based warp blocking using STAGE_DICT configuration
and the scripted_stage_blocking method for selective warp control.
"""

from environment.data.environment_data.pokered_constants import MAP_ID_REF, WARP_DICT, MAP_DICT
from typing import Dict, List, Any, Optional

# Stage-specific configuration with both blocking and scripted movement
#             ['block', 'ROUTE_5', 'UNDERGROUND_PATH_ROUTE_5@1',],

# =============================================================================
# STAGE_DICT CONFIGURATION FORMAT DOCUMENTATION
# =============================================================================
#
# EVENTS FORMAT:
# 'events': ['EVENT_NAME1', 'EVENT_NAME2']
# - Events are string identifiers that trigger special checks in update_stage_manager()
# - Available events: 'EVENT_GOT_MASTER_BALL', 'CAN_USE_SURF'
# - These are used to check game state conditions and progress
#
# BLOCKINGS FORMAT:
# 'blockings': [
#     ['SOURCE_MAP_NAME', 'direction'],           # Block map edge movement
#     ['SOURCE_MAP_NAME', 'TARGET_MAP@WARP_ID']   # Block specific warp
# ]
# 
# Edge blocking examples:
# ['PALLET_TOWN', 'north']     # Blocks leaving north edge of Pallet Town
# ['ROUTE_1', 'south']         # Blocks leaving south edge of Route 1
# 
# Warp blocking examples:
# ['REDS_HOUSE_1F', 'REDS_HOUSE_2F@0']    # Blocks warp from Red's house 1F to 2F (warp ID 0)
# ['PALLET_TOWN', 'REDS_HOUSE_1F@0']      # Blocks warp from Pallet Town to Red's house 1F (warp ID 0)
# ['VIRIDIAN_CITY', 'ROUTE_2@0']          # Blocks warp from Viridian City to Route 2 (warp ID 0)
#
# SCRIPTED_MOVEMENTS FORMAT:
# 'scripted_movements': [
#     {
#         'condition': {
#             'local_coords': (x, y),          # Local map coordinates
#             'global_coords': (y, x),         # Global coordinates (note: y, x order)
#             'map_id': map_id,                # Specific map ID
#             'item_check': {                  # Check item possession
#                 'item': 'ITEM_NAME',
#                 'has': True/False
#             },
#             'pending_b_presses': '>0'/True/int,  # Check pending B presses
#             'always': True,                  # Always trigger (use carefully)
#             'quest_path_active': True        # Trigger when quest path following is active
#         },
#         'action': 'up'/'down'/'left'/'right'/'a'/'b'/'start'/'path'/'path_follow',
#         'set_pending_b': int,               # Set number of pending B presses
#         'decrement_pending_b': True         # Decrement pending B counter
#     }
# ]
#
# Action options: 'down'=0, 'left'=1, 'right'=2, 'up'=3, 'a'=4, 'b'=5, 'path'=6, 'start'=7
# Special actions: 'path_follow' - delegates to quest manager path following
#
# IMPORTANT NOTES:
# - Blockings and scripted movements are DISABLED when dialog is active
# - The system prioritizes player interaction during conversations
# - Global coordinates use (y, x) order, local coordinates use (x, y) order
# - Warp IDs can be found in WARP_DICT in pokered_constants.py
# - Use update_stage_manager() to dynamically add/remove rules
# =============================================================================

STAGE_DICT = {
    1: {
        'events': [],
        'blockings': [],
        'scripted_movements': []
    },
    2: {
        'events': [],
        'blockings': [
            ['PALLET_TOWN', 'north'],                    # Block leaving Pallet Town north
            ['REDS_HOUSE_1F', 'REDS_HOUSE_2F@1'],       # Block warp from Red's house 1F to 2F (correct warp_id)
        ],
        'scripted_movements': [
            {
                'condition': {
                    'global_coords': (349, 81),         # Global coordinates (note: y, x order)
                },
                'action': 'down'
            },
            {
                'condition': {
                    'local_coords': (3, 7),          # Local map coordinates
                    'map_id': 37,                # Specific map ID
                },
                'action': 'down'
            },
                        {
                'condition': {
                    'local_coords': (2, 7),          # Local map coordinates
                    'map_id': 37,                # Specific map ID
                },
                'action': 'down'
            },
        ]
    },
    3: {
        'events': [],
        'blockings': [
            ['PALLET_TOWN', 'north'],                    # Block leaving Pallet Town north  
            ['PALLET_TOWN', 'REDS_HOUSE_1F@1'],         # Block entering Red's house 1F from Pallet Town (warp_id 1)
        ],
        'scripted_movements': []
    },
    4: {
        'events': [],
        'blockings': [],  # Allow normal movement after Oak encounter
        'scripted_movements': []
    },
    5: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            # Example: Force UP movement at specific coordinates
            {'condition': {'global_coords': (338, 94)}, 'action': 'up'}
        ]
    },
    7: {
        'events': ['EVENT_GOT_MASTER_BALL'],
        'blockings': [],
        'scripted_movements': []
    },
    9: {
        'events': ['CAN_USE_SURF'],
        'blockings': [],
        'scripted_movements': []
    },
    10: {
        'events': [],
        'blockings': [],  # Will be dynamically modified based on conditions
        'scripted_movements': []
    },
    12: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            # Talk to Oak at specific coordinates
            {'condition': {'global_coords': (348, 110)}, 'action': 'a'}
        ]
    },
    15: {
        'events': [],
        'blockings': [
            ['BLUES_HOUSE', 'PALLET_TOWN@1']  # Block leaving Blue's house until Town Map
        ],
        'scripted_movements': [
            # Force A press to get Town Map at Blue's sister location
            {'condition': {'local_coords': (1, 3), 'map_id': 39, 'item_check': {'item': 'TOWN MAP', 'has': False}}, 'action': 'a'},
            # Handle warp entry sequence
            {'condition': {'global_coords': (344, 97), 'pending_b_presses': True}, 'action': 'up', 'set_pending_b': 3},
            # Handle pending B presses
            {'condition': {'pending_b_presses': '>0'}, 'action': 'b', 'decrement_pending_b': True}
        ]
    },
    # Add more stages as needed
}

class StageManager:
    """Manages stage-based progression, warp blocking, and scripted movement"""
    
    def __init__(self, env):
        self.env = env
        self.stage = 1  # Current stage
        self.blockings = []  # Current active blockings
        self.scripted_movements = []  # Current scripted movements
        self.pending_b_presses = 0  # For managing B press sequences
        
        # Action mapping for scripted movements
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
        
    def update(self, current_states: Dict[str, Any]):
        """Update stage based on current game state"""
        # This is where you'd implement your stage progression logic
        # For now, just update blockings and scripted movements based on current stage
        if self.stage in STAGE_DICT:
            stage_config = STAGE_DICT[self.stage]
            if 'blockings' in stage_config:
                self.blockings = stage_config['blockings'].copy()
            if 'scripted_movements' in stage_config:
                self.scripted_movements = stage_config['scripted_movements'].copy()
    
    
    def scripted_stage_blocking(self, action: int) -> int:
        """
        The main warp blocking method based on your original code.
        Checks if current action would trigger a blocked warp and returns noop if so.
        """
        # CRITICAL: Do not block actions when dialog is active - player needs to interact
        try:
            dialog = self.env.read_dialog()
            if dialog and dialog.strip():
                print(f"StageManager: Dialog active, allowing all actions for player interaction")
                return action
        except Exception as e:
            print(f"StageManager: Error checking dialog in scripted_stage_blocking: {e}")
        
        if not self.blockings:
            print(f"StageManager: No blockings configured for stage {self.stage}")
            return action
        
        print(f"StageManager: Checking blocking for stage {self.stage}, action {action}, blockings: {self.blockings}")
            
        # Menu check removed - dialog check above is sufficient protection
        # The 0xFFB0 check was preventing all blocking during normal gameplay
            
        map_id = self.env.get_game_coords()[2]
        if map_id not in MAP_ID_REF:
            print(f"StageManager: Map ID {map_id} not in MAP_ID_REF")
            return action
            
        map_name = MAP_ID_REF[map_id]
        print(f"StageManager: Current map: {map_name} (ID: {map_id})")
        
        # Find blockings for this map
        blocking_indexes = [idx for idx in range(len(self.blockings)) 
                          if self.blockings[idx][0] == map_name]
        
        print(f"StageManager: Found {len(blocking_indexes)} blocking rules for map {map_name}")
        if not blocking_indexes:
            return action
            
        x, y = self.env.get_game_coords()[:2]
        new_x, new_y = x, y
        
        # Calculate new position based on action
        if action == 0:  # down
            new_y += 1
        elif action == 1:  # left
            new_x -= 1
        elif action == 2:  # right
            new_x += 1
        elif action == 3:  # up
            new_y -= 1
        else:
            print(f"StageManager: Non-movement action {action}, allowing")
            return action  # Not a movement action
            
        print(f"StageManager: Player at ({x},{y}) -> trying to move to ({new_x},{new_y}) with action {action}")
            
        # Check each blocking rule for this map
        for idx in blocking_indexes:
            blocking = self.blockings[idx]
            blocked_dir_warp = blocking[1]
            print(f"StageManager: Checking blocking rule: {blocking}")
            
            if blocked_dir_warp in ['north', 'south', 'west', 'east']:
                # Handle map edge blocking
                if blocked_dir_warp == 'north' and action == 3 and new_y < 0:
                    print(f"StageManager: BLOCKING {blocked_dir_warp} edge movement from {map_name}")
                    return self._get_noop_action()
                elif blocked_dir_warp == 'south' and action == 0 and new_y >= MAP_DICT[map_name]['height']:
                    print(f"StageManager: BLOCKING {blocked_dir_warp} edge movement from {map_name}")
                    return self._get_noop_action()
                elif blocked_dir_warp == 'west' and action == 1 and new_x < 0:
                    print(f"StageManager: BLOCKING {blocked_dir_warp} edge movement from {map_name}")
                    return self._get_noop_action()
                elif blocked_dir_warp == 'east' and action == 2 and new_x >= MAP_DICT[map_name]['width']:
                    print(f"StageManager: BLOCKING {blocked_dir_warp} edge movement from {map_name}")
                    return self._get_noop_action()
            else:
                # Handle specific warp blocking (your format: 'TARGET_MAP@WARP_ID')
                if '@' not in blocked_dir_warp:
                    print(f"StageManager: Invalid warp format (no @): {blocked_dir_warp}")
                    continue
                    
                blocked_warp_map_name, blocked_warp_warp_id = blocked_dir_warp.split('@')
                print(f"StageManager: Looking for warp to {blocked_warp_map_name} with warp_id {blocked_warp_warp_id}")
                
                # Get all warps in current map
                if map_name not in WARP_DICT:
                    print(f"StageManager: No warps found for map {map_name} in WARP_DICT")
                    continue
                    
                warps = WARP_DICT[map_name]
                print(f"StageManager: Found {len(warps)} warps in {map_name}")
                
                # Check if player is stepping on a blocked warp
                for warp in warps:
                    print(f"StageManager: Checking warp: target={warp['target_map_name']}, warp_id={warp['warp_id']}, coords=({warp['x']},{warp['y']})")
                    if (warp['target_map_name'] == blocked_warp_map_name and 
                        warp['warp_id'] == int(blocked_warp_warp_id)):
                        print(f"StageManager: Found matching warp at ({warp['x']},{warp['y']})")
                        if (new_x, new_y) == (warp['x'], warp['y']):
                            print(f"StageManager: BLOCKING warp from {map_name} to {blocked_warp_map_name}@{blocked_warp_warp_id} at ({new_x},{new_y})")
                            return self._get_noop_action()
                        else:
                            print(f"StageManager: Player not on warp tile - player at ({new_x},{new_y}), warp at ({warp['x']},{warp['y']})")
                            
        return action
    
    def scripted_stage_movement(self, action: int) -> int:
        """
        Check if current conditions match any scripted movement rules.
        Returns a different action if scripted movement should override the input action.
        Enhanced with automatic quest path following.
        """
        # CRITICAL: Do not override actions when dialog is active - player needs to interact
        try:
            dialog = self.env.read_dialog()
            if dialog and dialog.strip():
                print(f"StageManager: Dialog active, allowing player action {action} for interaction")
                return action
        except Exception as e:
            print(f"StageManager: Error checking dialog in scripted_stage_movement: {e}")
            
        if not self.scripted_movements:
            return action
            
        try:
            x, y, map_id = self.env.get_game_coords()
            
            # Check for global coordinates if available
            try:
                from environment.data.recorder_data.global_map import local_to_global
                global_y, global_x = local_to_global(y, x, map_id)
                global_coords = (global_y, global_x)
            except:
                global_coords = None
            
            # Process scripted movements in order
            for movement in self.scripted_movements:
                condition = movement.get('condition', {})
                
                # Check if conditions are met
                if self._check_movement_condition(condition, x, y, map_id, global_coords):
                    scripted_action = movement.get('action', action)
                    
                    # Handle special actions
                    if 'set_pending_b' in movement:
                        self.pending_b_presses = movement['set_pending_b']
                    if 'decrement_pending_b' in movement and movement['decrement_pending_b']:
                        self.pending_b_presses = max(0, self.pending_b_presses - 1)
                    
                    # Handle special path following action
                    if scripted_action == 'path_follow':
                        return self._handle_path_following(action, movement)
                    
                    # Convert action string to action index
                    if isinstance(scripted_action, str) and scripted_action in self.action_mapping:
                        scripted_action_int = self.action_mapping[scripted_action]
                        print(f"StageManager: Scripted movement triggered - {scripted_action} ({scripted_action_int}) at {(x,y)} map {map_id}")
                        return scripted_action_int
                    elif isinstance(scripted_action, int):
                        print(f"StageManager: Scripted movement triggered - action {scripted_action} at {(x,y)} map {map_id}")
                        return scripted_action
                        
        except Exception as e:
            print(f"StageManager: Error in scripted_stage_movement: {e}")
            
        return action
    
    def _check_movement_condition(self, condition: Dict[str, Any], x: int, y: int, map_id: int, global_coords: Optional[tuple]) -> bool:
        """Check if movement condition is satisfied"""
        try:
            # Check local coordinates
            if 'local_coords' in condition:
                target_x, target_y = condition['local_coords']
                if (x, y) != (target_x, target_y):
                    return False
            
            # Check global coordinates
            if 'global_coords' in condition and global_coords:
                target_global = condition['global_coords']
                if global_coords != target_global:
                    return False
                    
            # Check map ID
            if 'map_id' in condition:
                if map_id != condition['map_id']:
                    return False
            
            # Check item possession
            if 'item_check' in condition:
                item_check = condition['item_check']
                item_name = item_check.get('item')
                should_have = item_check.get('has', True)
                
                if item_name and hasattr(self.env, 'item_handler'):
                    has_item = self.env.item_handler.has_item(item_name)
                    if has_item != should_have:
                        return False
                elif item_name:
                    # Fallback item check method
                    try:
                        items = self.env.get_items_in_bag() if hasattr(self.env, 'get_items_in_bag') else []
                        # This would need proper item ID mapping
                        return False  # Skip for now if no item_handler
                    except:
                        return False
            
            # Check pending B presses
            if 'pending_b_presses' in condition:
                check = condition['pending_b_presses']
                if check == '>0':
                    if self.pending_b_presses <= 0:
                        return False
                elif check == True:
                    if self.pending_b_presses <= 0:
                        return False
                elif isinstance(check, int):
                    if self.pending_b_presses != check:
                        return False
            
            # Check special path following conditions
            if 'always' in condition and condition['always']:
                # This condition always matches
                pass
                
            if 'quest_path_active' in condition and condition['quest_path_active']:
                # Check if quest manager has active path following
                if hasattr(self.env, 'quest_manager') and hasattr(self.env.quest_manager, 'warp_blocker'):
                    if not self.env.quest_manager.warp_blocker.path_follower.path_following_active:
                        return False
                else:
                    return False
            
            return True
            
        except Exception as e:
            print(f"StageManager: Error checking movement condition: {e}")
            return False
    
    def _handle_path_following(self, original_action: int, movement: Dict[str, Any]) -> int:
        """Handle path following movement by delegating to quest manager"""
        try:
            if hasattr(self.env, 'quest_manager') and hasattr(self.env.quest_manager, 'warp_blocker'):
                quest_blocker = self.env.quest_manager.warp_blocker
                return quest_blocker.handle_path_following_movement(original_action)
            else:
                print("StageManager: Quest manager not available for path following")
                return original_action
        except Exception as e:
            print(f"StageManager: Error in path following: {e}")
            return original_action
    
    def _get_noop_action(self) -> int:
        """Return the noop button index"""
        # You'll need to define this based on your action mapping
        # For now, return 4 as a placeholder (assuming it's the noop action)
        return getattr(self.env, 'noop_button_index', 4)
    
    def add_blocking(self, map_name: str, direction_or_warp: str):
        """Add a temporary blocking rule"""
        blocking = [map_name, direction_or_warp]
        if blocking not in self.blockings:
            self.blockings.append(blocking)
            
    def remove_blocking(self, map_name: str, direction_or_warp: str):
        """Remove a blocking rule"""
        blocking = [map_name, direction_or_warp]
        if blocking in self.blockings:
            self.blockings.remove(blocking)
            
    def add_scripted_movement(self, condition: Dict[str, Any], action: str, **kwargs):
        """Add a temporary scripted movement rule"""
        movement = {
            'condition': condition,
            'action': action,
            **kwargs
        }
        if movement not in self.scripted_movements:
            self.scripted_movements.append(movement)
            print(f"StageManager: Added scripted movement {movement}")
            
    def remove_scripted_movement(self, condition: Dict[str, Any], action: str):
        """Remove a scripted movement rule"""
        for movement in self.scripted_movements[:]:  # Copy list to avoid modification during iteration
            if (movement.get('condition') == condition and 
                movement.get('action') == action):
                self.scripted_movements.remove(movement)
                print(f"StageManager: Removed scripted movement {movement}")
    
    def clear_scripted_movements(self):
        """Clear all scripted movements"""
        self.scripted_movements = []
        print("StageManager: Cleared all scripted movements")
            
    def update_stage_manager(self):
        """Your original update_stage_manager method structure"""
        current_states = {
            'items': self.env.get_items_in_bag() if hasattr(self.env, 'get_items_in_bag') else [],
            'map_id': self.env.current_map_id - 1,
            'badges': self.env.get_badges() if hasattr(self.env, 'get_badges') else [],
            'visited_pokecenters': getattr(self.env, 'visited_pokecenter_list', []),
            'last_pokecenter': self.env.get_last_pokecenter_id() if hasattr(self.env, 'get_last_pokecenter_id') else None,
        }
        
        # Add event checking based on stage
        if self.stage in STAGE_DICT:
            stage_config = STAGE_DICT[self.stage]
            if 'events' in stage_config:
                event_list = stage_config['events']
                current_states['events'] = {}
                
                if 'EVENT_GOT_MASTER_BALL' in event_list:
                    current_states['events']['EVENT_GOT_MASTER_BALL'] = self.env.read_bit(0xD838, 5)
                if 'CAN_USE_SURF' in event_list:
                    current_states['events']['CAN_USE_SURF'] = getattr(self.env, 'can_use_surf', False)
        
        self.update(current_states)
        
        # Handle stage 10 special blocking logic (your example)
        if self.stage == 10:
            map_id = self.env.current_map_id - 1
            if map_id == 0xD8:  # pokemon mansion b1f
                bag_items = self.env.get_items_in_bag() if hasattr(self.env, 'get_items_in_bag') else []
                additional_blocking = ['POKEMON_MANSION_B1F', 'POKEMON_MANSION_1F@6']
                
                if 0x2B not in bag_items:  # secret key not in bag items
                    if additional_blocking not in self.blockings:
                        self.blockings.append(additional_blocking)
                else:  # secret key in bag items
                    if self.env.read_bit(0xD796, 0) is True:  # switch on
                        if additional_blocking in self.blockings:
                            self.blockings.remove(additional_blocking)
                    else:  # switch off
                        if additional_blocking not in self.blockings:
                            self.blockings.append(additional_blocking) 