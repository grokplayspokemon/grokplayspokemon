# Stage Manager for Pokemon Red
"""
Stage Manager: Handles stage-based warp blocking using STAGE_DICT configuration
and the scripted_stage_blocking method for selective warp control.
"""

import time
from environment.data.environment_data.pokered_constants import MAP_ID_REF, WARP_DICT, MAP_DICT, ITEM_NAME_TO_ID_DICT
from typing import Dict, List, Any, Optional
from collections import deque
from environment.data.environment_data.tilesets import Tilesets
from environment.data.recorder_data.global_map import GLOBAL_MAP_SHAPE, local_to_global
import re
import logging
import os

# Set up dedicated parcel tracking logger
parcel_logger = logging.getLogger('parcel_tracker')
parcel_logger.setLevel(logging.DEBUG)

# Create file handler if it doesn't exist
if not parcel_logger.handlers:
    log_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(log_dir, 'parcel_debug.log')
    
    file_handler = logging.FileHandler(log_file, mode='w')  # 'w' to overwrite each run
    file_handler.setLevel(logging.DEBUG)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    parcel_logger.addHandler(file_handler)
    parcel_logger.info("=== PARCEL TRACKING LOG STARTED - LOGGING EVERY STEP ===")
    parcel_logger.info("=== WILL TRACK STAGES 6-22 TO CATCH PARCEL ACQUISITION AND LOSS ===")

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
    # ------------------------------------------------------------------
    # Stage 0 – Game boot & intro-dialog skipping until the custom name
    #           entry screen ("YOUR NAME?") is visible.  This is *only*
    #           responsible for getting the game from title screen to the
    #           letter-grid.  Further name typing is handled elsewhere.
    # ------------------------------------------------------------------
1: {
    'events': [],
    'blockings': [],
    'scripted_movements': [
        # 1. Press START until Oak's dialog appears
        {
            'condition': {
                'always': True,
                'oak_intro_active': False
            },
            'stop_condition': {
                'dialog_contains': 'Hello there'
            },
            'action': 'start'
        },
        # 2a. Start pressing B when Oak's dialog appears
        {
            'condition': {
                'dialog_contains': 'Hello there',
                'set_oak_intro_active': True
            },
            'stop_condition': {
                'dialog_contains': 'NEW NAME',
                'clear_oak_intro_active': True
            },
            'action': 'b'
        },
        # 2b. Continue pressing B through Oak's intro
        {
            'condition': {
                'oak_intro_active': True,
                'always': True
            },
            'stop_condition': {
                'dialog_contains': 'NEW NAME',
                'clear_oak_intro_active': True
            },
            'action': 'b'
        },
    ]
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
        'blockings': [],
        'scripted_movements': [
        # {
        #     'condition': {
        #         'global_coords': (350, 110), # all below to keep grok away from oak dialog-lock
        #     },
        #     'action': 'right'
        # },
        # {
        #     'condition': {
        #         'global_coords': (349, 110),
        #     },
        #     'action': 'down'
        # },
        # {
        #     'condition': {
        #         'global_coords': (349, 111),
        #     },
        #     'action': 'down'
        # },
        # {
        #     'condition': {
        #         'global_coords': (348, 109),
        #     },
        #     'action': 'down'
        # },
        # {
        #     'condition': {
        #         'global_coords': (348, 110),
        #         'party_size': 1,
        #     },
        #     'action': 'left'
        # },
        
        ]
    },
    5: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            # Example: Force UP movement at specific coordinates
            {'condition': {'global_coords': (338, 94)}, 'action': 'up'}
        ]
    },
    6: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            # {'condition': {'global_coords': (327, 90), 
            #                'item_check': {'item': 'POTION', 'has': False},
            #                'dialog_present': False,
            #                'facing_direction': 'down',
            #                }, 
            #  'action': 'right'}, # turn to npc to get the potion
            # {'condition': {'global_coords': (327, 90), 
            #                'item_check': {'item': 'POTION', 'has': False},
            #                'dialog_present': False,
            #                'facing_direction': 'left',
            #                }, 
            #  'action': 'a'}, # turn to npc to get the potion

            # {'condition': {'global_coords': (326, 89), 'item_check': {'item': 'POTION', 'has': False}}, 'action': 'down', 'set_pending_a': 8}, 
            # {'condition': {'global_coords': (326, 90), 'item_check': {'item': 'POTION', 'has': False}}, 'action': 'down'}, 
            # {'condition': {'global_coords': (328, 90), 'item_check': {'item': 'POTION', 'has': False}}, 'action': 'up'}, 
        ]
    },
    9: {
        # 'events': [''],
        # 'blockings': [],
        # 'scripted_movements': [
        #     {'condition': {'global_coords': (301, 133), 
        #                    'item_check': {
        #                        'item': 'OAKS_PARCEL', 'has': True
        #                        }
        #                    }, 'action': 'down'}
        # ]
    },
    10: {
        'events': [],
        'blockings': [],  # Will be dynamically modified based on conditions
        'scripted_movements': []
    },
    12: {
        # 'events': [],
        # 'blockings': [],
        # 'scripted_movements': [
        #     # Talk to Oak at specific coordinates
        #     {
        #         'condition': {
        #             'global_coords': (348, 110),
        #         }, 
        #         'action': 'a'
        #     }
        # ]
    },
    15: {
        'events': [],
        'blockings': [
            ['BLUES_HOUSE', 'PALLET_TOWN@1']  # Block leaving Blue's house until Town Map
        ],
        # 'scripted_movements': [
        #     # Force A press to get Town Map at Blue's sister location at global coords (340, 107)
        #     {'condition': {'global_coords': (340, 107), 'item_check': {'item': 'TOWN_MAP', 'has': False}}, 'action': 'a'},
        #     # Force A press to get Town Map at Blue's sister location at local coords (1, 3)
        #     {'condition': {'local_coords': (1, 3), 'map_id': 39, 'item_check': {'item': 'TOWN_MAP', 'has': False}}, 'action': 'a'},
        #     # Handle warp entry sequence
        #     {'condition': {'global_coords': (344, 97), 'pending_b_presses': True}, 'action': 'up', 'set_pending_b': 3},
        #     # Handle pending B presses
        #     {'condition': {'pending_b_presses': '>0'}, 'action': 'b', 'decrement_pending_b': True}
        # ]
    },
    23: {
        'events': [],
        'blockings': [
            # Block movement until Nidoran is caught
            ['ROUTE_22', 'VIRIDIAN_CITY@1'],  # Block route back to Viridian City
            ['ROUTE_22', 'ROUTE_23@2']        # Block route towards Indigo Plateau
        ],
        'scripted_movements': [
            # Movement rules for finding Nidoran (only when no dialog present)
            # NOTE: item_qty checks for "at least" the specified quantity (>=)
            {
                'condition': {
                    'global_coords': (282, 67),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'down'
            },
            {
                'condition': {
                    'global_coords': (282, 66),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'down'
            },
            {
                'condition': {
                    'global_coords': (282, 65),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'down'
            },
            {
                'condition': {
                    'global_coords': (282, 64),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'down'
            },
            {
                'condition': {
                    'global_coords': (285, 67),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'up'
            },
            {
                'condition': {
                    'global_coords': (285, 66),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'up'
            },
            {
                'condition': {
                    'global_coords': (285, 65),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'up'
            },
            {
                'condition': {
                    'global_coords': (285, 64),
                    'dialog_present': False,
                    'item_qty': {'item': 'POKE_BALL', 'qty': 1},  # At least 1 Poke Ball
                    'pokemon_caught': {'species': 'NIDORAN_M', 'caught': False}
                },
                'action': 'up'
            },
            # Special handling for Nidoran encounters
            {
                'condition': {
                    'global_coords': (285, 63),
                    'dialog_present': True,
                    'dialog_text': 'NIDORAN♂',
                },
                'action': 'a'
            },
        ]
    },
    21: {
        'events': [],
        'blockings': [
            # Prevent exiting the Viridian Poké Mart door (warp id 2 → LAST_MAP) until ≥4 Poké Balls
            ['VIRIDIAN_MART', 'LAST_MAP@2']
        ],
        'scripted_movements': [
            # 1️⃣  Engage path-follower until Grok reaches the counter tile
            {
                'condition': {
                    'quest_path_active': True  # ConsolidatedNavigator path following already loaded by QuestManager
                },
                'action': 'path'
            },
            # 2️⃣  When standing directly in front of the clerk (global coords 299,132) face LEFT and press A
            {
                'condition': {
                    'global_coords': (299, 132),
                },
                'action': 'a'
            },
            # 3️⃣  While the clerk dialog / shop menus are visible keep spamming A until QuestManager reports >4 Poké Balls
            {
                'condition': {
                    'dialog_present': True,
                },
                'action': 'a'
            },
        ]
    },
    22: {
        'events': [],
        'blockings': [
            # Viridian Mart has two door tiles, both share warp_id 2 pointing to LAST_MAP – block both tiles at once
            ['VIRIDIAN_MART', 'LAST_MAP@2'],
        ],
        'scripted_movements': [
            {'condition': 
                {'global_coords': (299, 132),
                 'item_qty': {'item': 'POKE_BALL', 'qty': 4}}, 'action': 'a'}  # At least 4 Poke Balls
            
        ]
    },
    26: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            # {'condition': {'global_coords': (270, 89)},
            #  'action': 'a'},
            # {'condition': {
            #     'global_coords': (297, 118),
            #     'dialog_present': False,
            #     'health_fraction': 1,                
            #     },
            # 'action': 'down'
            # },
            # # Viridian Poké Center uses *three* adjacent X-columns (global 116-118)
            # # in front of the counter.  The original rule only handled the
            # # centre tile (X=118) which left Grok stuck when the healing routine
            # # ended on either side position.  We duplicate the rule for the two
            # # neighbouring columns so the DOWN step is queued regardless of the
            # # exact alignment.
            # {'condition': {
            #     'global_coords': (297, 117),
            #     'dialog_present': False,
            #     'health_fraction': 1,                
            #     },
            #  'action': 'down'
            # },
            # {'condition': {
            #     'global_coords': (297, 116),
            #     'dialog_present': False,
            #     'health_fraction': 1,                
            #     },
            #  'action': 'down'
            # },
            {'condition': { # get hidden potion in cut tree
                'global_coords': (270, 89),
                },
            'action': 'a'
            },
        ]
    },
    33: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            {'condition': {
                'global_coords': (183, 87),
                'pending_a_presses': '>0',               # Set number of pending A presses
                'decrement_pending_a_presses': True         # Decrement pending A counter
                },
             'action': 'a'},
            {'condition': {
                'global_coords': (183, 87),
                'dialog_present': False,
                'health_fraction': 1,                
                },
            'action': 'down'
            },
            
        ]
    },
    37: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            {'condition': {'global_coords': (167, 67)}, 'action': 'a'},
            {'condition': # prevent grok from striking up convo with beaten trainer
                {'global_coords': (171, 67),
                 'event_completed': ['EVENT_BEAT_PEWTER_GYM_TRAINER_0'],
                 'facing_direction': 'left',
                 }, 'action': 'down'},
            
        ]
    },
    40: {
        'events': [],
        'blockings': [],
        'scripted_movements': [
            {'condition': 
                {'global_coords': (167, 67),
                 'dialog_present': False,
                 'has_badge': '1'
                 },
                 'action': 'down'
                 }
        ]
    },
    46: {
        'events': [],
        'blockings': [],
        'scripted_movements': []
    },
    # Add more stages as needed
}

# Universal battle logic - applies to ALL battles regardless of stage
UNIVERSAL_BATTLE_RULES = [
    # Handle Pokemon selection screen - "Bring out which POKéMON?"
    {
        'condition': {
            'dialog_present': True,
            'dialog_contains': 'Bring out which',
        },
        'action': 'down'  # Navigate down to find healthy Pokemon
    },
    # Handle "There's no will to fight" - fainted Pokemon selected
    {
        'condition': {
            'dialog_present': True,
            'dialog_contains': 'no will to fight',
        },
        'action': 'b'  # Press B to go back and select different Pokemon
    },
    # Handle Pokemon with FNT status in selection screen
    {
        'condition': {
            'dialog_present': True,
            'dialog_contains': 'FNT',
        },
        'action': 'down'  # Move down to next Pokemon
    },
    # Handle general fainted Pokemon messages
    {
        'condition': {
            'dialog_present': True,
            'dialog_contains': 'fainted',
        },
        'action': 'b'
    },
    # Handle "unable to battle" messages
    {
        'condition': {
            'dialog_present': True,
            'dialog_contains': 'unable to battle',
        },
        'action': 'b'
    },
    # Handle Pokemon switch confirmation "Go!"
    {
        'condition': {
            'dialog_present': True,
            'dialog_contains': 'Go!',
        },
        'action': 'b'
    },
]

class StageManager:
    """Manages stage-based progression, warp blocking, and scripted movement"""
    
    def __init__(self, env):
        self.env = env
        # Start at stage 1 because quest progression stages begin at 1.
        # (Stage-0 bootstrap was removed.)
        self.stage = 1
        self.blockings = []  # Current active blockings
        self.scripted_movements = []  # Current scripted movements
        self.pending_b_presses = 0  # For managing B press sequences
        
        # ------------------------------------------------------------------
        # Anti-stuck safeguard: Counts how many times Stage-1 scripted rule 3
        # (A-press on «NEW NAME») has fired in the current session.  If this
        # exceeds a reasonable threshold we assume the intro sequence is
        # already past the point where "YOUR NAME?" would appear (e.g. when
        # loading from a mid-intro save) and automatically advance to the
        # next stage to prevent endless A-button spamming.
        # ------------------------------------------------------------------
        self._stage1_a_press_counter = 0
        
        # ------------------------------------------------------------------
        # Tracks whether we are currently inside Professor Oak's intro
        # monologue (from the first «Hello there!» up until the preset-name
        # screen appears).  Used so the B-spamming rule only fires during
        # that interval.
        # ------------------------------------------------------------------
        self._oak_intro_active: bool = False
        
        # ------------------------------------------------------------------
        # ACTIVE BUTTON GENERATION 🔄
        # ------------------------------------------------------------------
        # Until now StageManager only *modified* player/AI actions that were
        # already present in the input stream.  For certain early-game
        # automation (notably Quest 001) we sometimes need to *originate* an
        # input even when the upstream systems supply **no** action (e.g. the
        # AI is thinking or interactive player is idle).
        #
        # We implement a tiny FIFO queue so update_stage_manager() can enqueue
        # one-off key-presses.  The next call to scripted_stage_movement()
        # will pop from this queue **before** evaluating the normal scripted
        # movement rules, thereby ensuring the press is executed exactly once
        # and is transparently blended with the existing override logic.
        # ------------------------------------------------------------------
        self._auto_action_queue: "deque[int]" = deque(maxlen=32)
        # Simple frame counter so we can throttle auto-press frequency
        self._frame_counter: int = 0
        
        # Stage 4 helper flag – ensures RIGHT is queued only once at Oak greeting tile
        self._oak_greet_right_sent: bool = False
        
        # ------------------------------------------------------------------
        # Track previous quantities of items in the bag
        # ------------------------------------------------------------------
        self._previous_quantities: Dict[int, int] = {}
        
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
        
        # ------------------------------------------------------------------
        # Immediately load stage-specific configuration for the initial stage
        # so that bootstrap scripted movements (Stage-0 intro-skip) are active
        # from the very first frame.  Without this call the "scripted_stage_
        # movement" method finds an empty list and the automation never fires.
        # ------------------------------------------------------------------
        self.update({})
        
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
                            print(f"StageManager: Player not on warp tile - player at ({x},{y}), warp at ({warp['x']},{warp['y']})")
                            
        return action
    
    def scripted_stage_movement(self, action: int) -> int:
        """
        Check if current conditions match any scripted movement rules.
        Returns a different action if scripted movement should override the input action.
        Enhanced with automatic quest path following.
        """
        # ------------------------------------------------------------------
        # RESET HEAL FLAG WHEN LEAVING POKÉ CENTER - allow path-follow after exit
        try:
            # Clear heal sequence flag if not on a Poké Center map
            x, y, current_map = self.env.get_game_coords()
            if getattr(self, '_heal_at_poke_center_seq_enqueued', False) and not self.env._is_pokecenter(current_map):
                self._heal_at_poke_center_seq_enqueued = False
        except Exception:
            pass
        # ------------------------------------------------------------------
        # EMERGENCY STAGE 23 CLEAR - Prevent infinite left-walking loops
        # ------------------------------------------------------------------
        if self.stage == 23:
            try:
                # Check if we should still be in stage 23
                current_quest = getattr(self.env.quest_manager, 'current_quest_id', None) if hasattr(self.env, 'quest_manager') else None
                
                # If we're no longer on quest 23, immediately clear all automation
                if current_quest and str(current_quest) != '023':
                    print(f"StageManager: EMERGENCY CLEAR - Stage 23 active but current quest is {current_quest}. Clearing all automation.")
                    self._auto_action_queue.clear()
                    self.clear_scripted_movements()
                    self.blockings.clear()
                    self.stage = 24
                    return action  # Return original action without any scripted override
                    
                # Also check if we have Nidoran (backup condition)
                try:
                    from environment.data.environment_data.species import Species
                    if hasattr(self.env, 'party') and self.env.party:
                        party = self.env.party.party[:self.env.party.party_size]
                        for p in party:
                            try:
                                species_name = Species(p.Species).name
                                if species_name == 'NIDORAN_M':
                                    print(f"StageManager: EMERGENCY CLEAR - Stage 23 but Nidoran already captured. Clearing automation.")
                                    self._auto_action_queue.clear()
                                    self.clear_scripted_movements()
                                    self.blockings.clear()
                                    self.stage = 24
                                    return action
                            except Exception:
                                continue
                except Exception:
                    pass
            except Exception as e:
                print(f"StageManager: Error in emergency stage 23 check: {e}")
        
        # ------------------------------------------------------------------
        # NIDORAN CAPTURE COMPLETION CHECKS – run every frame *before* we
        # evaluate/forward the action.  This guarantees the special naming
        # dialog handlers fire even when StageManager is in a different
        # stage and irrespective of whether the dedicated Nidoran script is
        # active.
        # ------------------------------------------------------------------
        
        
        try:
            self._caught_nidoran_to_naming_dialog()
            self._caught_nidoran_pokeball_failed()
        except Exception as e:
            print(f"StageManager: Error in caught_nidoran_to_naming_dialog: {e}")
            print(f"StageManager: Error in caught_nidoran_pokeball_failed: {e}")

        self._heal_at_poke_center()  # DISABLED: Prevents automatic warping out of Pokemon Center

        # if self.env.quest_manager.current_quest_id == 8:
        #     self._remove_item_from_bag('OAKS_PARCEL')
        
        print(f"StageManager: Current quest ID: {self.env.quest_manager.current_quest_id}, current items: {self.env.item_handler.get_items_in_bag()}, is parcel in bag: {0x46 in self.env.item_handler.get_items_in_bag()}")
        
        # ------------------------------------------------------------------
        # Execute any auto-generated action that
        # was queued in update_stage_manager().  This guarantees the
        # press originates *inside* StageManager and is not dependent
        # on upstream input.
        # ------------------------------------------------------------------
        if self._auto_action_queue:
            auto_act = self._auto_action_queue.popleft()
            print(f"StageManager: Executing queued auto-action {auto_act} (remaining: {len(self._auto_action_queue)})")
            return auto_act
        
        # ------------------------------------------------------------------
        # UNIVERSAL BATTLE RULES - Check these FIRST before stage-specific rules
        # These apply to ALL battles throughout the game
        # ------------------------------------------------------------------
        try:
            x, y, map_id = self.env.get_game_coords()
            
            # Check for global coordinates if available
            try:
                from environment.data.recorder_data.global_map import local_to_global
                x, y, map_id = self.env.get_game_coords()
                gy, gx = local_to_global(y, x, map_id)
                global_coords = (gy, gx)
            except Exception as e:
                global_coords = None
            
            # Process universal battle rules first
            for rule in UNIVERSAL_BATTLE_RULES:
                condition = rule.get('condition', {})
                if self._check_movement_condition(condition, x, y, map_id, global_coords):
                    scripted_action = rule.get('action')
                    if isinstance(scripted_action, str) and scripted_action in self.action_mapping:
                        scripted_action_int = self.action_mapping[scripted_action]
                        print(f"StageManager: UNIVERSAL BATTLE RULE → {scripted_action} ({scripted_action_int}) [rule: {rule}]")
                        return scripted_action_int
                    elif isinstance(scripted_action, int):
                        print(f"StageManager: UNIVERSAL BATTLE RULE → action {scripted_action} [rule: {rule}]")
                        return scripted_action
                        
        except Exception as e:
            print(f"StageManager: Error in universal battle rules: {e}")
            
        # ------------------------------------------------------------------
        # Store PATH_FOLLOW_ACTION for later processing - we want to check
        # scripted movement rules first to allow overrides
        # ------------------------------------------------------------------
        from environment.environment import PATH_FOLLOW_ACTION
        is_path_follow_action = (action == PATH_FOLLOW_ACTION)
            
        if not self.scripted_movements:
            # No scripted movements defined for the current stage – allow the
            # original action to proceed unchanged.  We only log once so the
            # console is not flooded every frame.
            print("StageManager: No scripted movements active – passing through action", action)
            return action
            
        try:
            x, y, map_id = self.env.get_game_coords()
            
            # Check for global coordinates if available
            try:
                from environment.data.recorder_data.global_map import local_to_global
                x, y, map_id = self.env.get_game_coords()  # Note: get_game_coords returns (x, y, map_id)
                gy, gx = local_to_global(y, x, map_id)
                global_coords = (gy, gx)
                # print(f"DEBUGGING STAGEMANAGER: global coords available: {global_coords}")
                if global_coords:
                    # print(f"DEBUGGING STAGEMANAGER: processing movement {movement} in self.scripted_movements {self.scripted_movements}")
                    for movement in self.scripted_movements:
                        # print(f"DEBUGGING STAGEMANAGER: condition: {condition}")
                        if self._check_condition(movement.get('condition', {})):
                            scripted_action = movement.get('scripted_action', '')
                            # print(f"DEBUGGING STAGEMANAGER: scripted_action: {scripted_action}")
                            action = movement.get('action', '')
                            # print(f"DEBUGGING STAGEMANAGER: action: {action}")
                            
                            if scripted_action == 'set_pending_b':
                                self.pending_b_presses = movement.get('b_presses', 1)
                                # print(f"DEBUGGING STAGEMANAGER: set_pending_b: {self.pending_b_presses}")
                            elif scripted_action == 'decrement_pending_b':
                                self.pending_b_presses = max(0, self.pending_b_presses - 1)
                                # print(f"DEBUGGING STAGEMANAGER: decrement_pending_b: {self.pending_b_presses}")
                            
                            # print(f"DEBUGGING STAGEMANAGER: scripted_action: {scripted_action}")
                            return action
            except Exception as e:
                # Global coordinate conversion failed (likely on maps that are
                # not part of the world map).  This is not fatal – just log
                # once and proceed with local coordinates only.
                print("StageManager: No global coords available (", e, ") – continuing with local coords only")
                global_coords = None
            
            # Process scripted movements in order
            for movement in self.scripted_movements:
                print(f"DEBUGGING STAGEMANAGER: processing movement {movement} in self.scripted_movements {self.scripted_movements}")
                # ------------------------------------------------------------------
                # NEW FEATURE: Optional "stop_condition"
                # ------------------------------------------------------------------
                # A scripted movement can now specify a complementary
                #     'stop_condition': {...}
                # When this evaluates to True the scripted movement will be
                # temporarily ignored (but *not* removed) allowing finely-
                # grained control without having to invert the original
                # triggering condition.
                # ------------------------------------------------------------------
                stop_cond = movement.get('stop_condition')
                if stop_cond and self._check_movement_condition(stop_cond, x, y, map_id, global_coords):
                    # The stop-condition overrides the trigger – skip this movement rule.
                    print("StageManager: stop_condition met – skipping scripted movement", movement)
                    continue

                condition = movement.get('condition', {})
                print(f"DEBUGGING STAGEMANAGER: condition: {condition}")
                
                # Check if trigger conditions are met
                if self._check_movement_condition(condition, x, y, map_id, global_coords):
                    scripted_action = movement.get('action', action)
                    print(f"DEBUGGING STAGEMANAGER: scripted_action: {scripted_action}")
                    print(f"DEBUGGING STAGEMANAGER: action: {action}")
                    # Handle special actions
                    if 'set_pending_b' in movement:
                        self.pending_b_presses = movement['set_pending_b']
                        print(f"DEBUGGING STAGEMANAGER: set_pending_b: {self.pending_b_presses}")
                    if 'decrement_pending_b' in movement and movement['decrement_pending_b']:
                        self.pending_b_presses = max(0, self.pending_b_presses - 1)
                        print(f"DEBUGGING STAGEMANAGER: decrement_pending_b: {self.pending_b_presses}")
                    # Handle special path following action
                    if scripted_action == 'path_follow':
                        return self._handle_path_following(action, movement)
                    print(f"DEBUGGING STAGEMANAGER: scripted_action: {scripted_action}")
                    # --------------------------------------------------------------
                    # NEW FEATURE: multi-action sequences
                    # --------------------------------------------------------------
                    # If the movement defines a 'multiaction' list we must handle
                    # it *before* the usual single-action logic, otherwise the
                    # default action placeholder would short-circuit the rule.
                    # --------------------------------------------------------------
                    multiaction_seq = movement.get('multiaction')
                    if multiaction_seq and not movement.get('_multiaction_done'):
                        action_int_list: list[int] = []
                        for act in multiaction_seq:
                            if isinstance(act, str):
                                mapped = self.action_mapping.get(act)
                                if mapped is None:
                                    print(f"StageManager: Unknown action in multiaction list: {act}")
                                    continue
                                action_int_list.append(mapped)
                            else:
                                action_int_list.append(int(act))

                        if action_int_list:
                            # Queue all actions after the first so they run on
                            # subsequent frames via the existing auto-action queue.
                            for later_act in action_int_list[1:]:
                                self._queue_auto_action(later_act)

                            movement['_multiaction_done'] = True  # fire only once
                            first_act = action_int_list[0]
                            print(
                                f"StageManager: MULTIACTION triggered – executing {first_act} now; "
                                f"queued {len(action_int_list) - 1} follow-up actions"
                            )
                            return first_act
                    # ------------------------------------------------------------------
                    # Standard single-action override (runs only if no multiaction fired)
                    # ------------------------------------------------------------------
                    scripted_action = movement.get('action')
                    if scripted_action is not None:
                        if isinstance(scripted_action, str) and scripted_action in self.action_mapping:
                            if self.stage == 1 and scripted_action == 'a':
                                self._stage1_a_press_counter += 1
                            scripted_action_int = self.action_mapping[scripted_action]
                            print(
                                f"StageManager: OVERRIDE → {scripted_action} ({scripted_action_int}) at {(x, y)} map {map_id} [rule: {movement}]"
                            )
                            return scripted_action_int
                        elif isinstance(scripted_action, int):
                            print(
                                f"StageManager: OVERRIDE → action {scripted_action} at {(x, y)} map {map_id} [rule: {movement}]"
                            )
                            return scripted_action
                        # else malformed; ignore and fall through
        except Exception as e:
            print(f"StageManager: Error in scripted_stage_movement: {e}")
            
        # ------------------------------------------------------------------
        # NEW FEATURE: Stray-A suppression – once Quest 001 is complete we
        # no longer want the intro-bootstrap key-repeat to mash the A button
        # when no dialog is present.  If we detect such an "A" (index 4)
        # that is NOT triggered by any scripted rule we simply convert it to
        # a noop so downstream systems stay idle.
        # ------------------------------------------------------------------
        try:
            if action == self.action_mapping.get('a'):
                active_dialog = (self.env.get_active_dialog() or '').replace("\n", " ").strip()

                # Case 1: No dialog visible after Quest 001 completion
                if (not active_dialog) and self._quest001_completed():
                    print("StageManager: Suppressing stray 'A' press – Quest 001 completed, no active dialog")
                    return self._get_noop_action()

                # Case 2: Any dialog active where the word 'YES' is NOT present –
                # pressing 'A' risks looping.  Convert to a 'B' press to advance/close.
                if active_dialog and 'YES' not in active_dialog.upper():
                    print("StageManager: Replacing 'A' press with 'B' – dialog active without 'YES'")
                    return self.action_mapping.get('b', self._get_noop_action())

                # Case 3: Naming-screen keyboard displayed (contains the row text).
                # If we reach here 'YES' is not in dialog by definition, but we still want to
                # suppress loops – same behaviour as above.
                if re.search(r"[\u25ba>]?\s*A\s+B\s+C\s+D\s+E\s+F\s+G\s+H\s+I", active_dialog, re.IGNORECASE):
                    print("StageManager: Replacing 'A' press with 'B' – naming screen keyboard active")
                    return self.action_mapping.get('b', self._get_noop_action())
        except Exception as e:
            print("StageManager: Error in stray-A suppression logic:", e)
        
        # ------------------------------------------------------------------
        # Handle PATH_FOLLOW_ACTION after checking all scripted movements
        # This allows scripted movements to override path following
        # ------------------------------------------------------------------
        if is_path_follow_action:
            # Suppress path-follow during Poké Center heal exit sequence
            if getattr(self, '_heal_at_poke_center_seq_enqueued', False):
                print("StageManager: Suppressing PATH_FOLLOW_ACTION during heal exit sequence")
                return self._get_noop_action()
            print(f"StageManager: Converting PATH_FOLLOW_ACTION to quest movement (no scripted overrides)")
            if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
                current_quest = getattr(self.env.quest_manager, 'current_quest_id', None)
                print(f"StageManager: Current quest ID from quest manager: {current_quest}")
            return self._convert_path_follow_to_movement(action)
        
        # print(f"DEBUGGING STAGEMANAGER: action={action}")
        return action
    
    def _check_movement_condition(self, condition: Dict[str, Any], x: int, y: int, map_id: int, global_coords: Optional[tuple]) -> bool:
        """Check if movement condition is satisfied"""
        try:
            # Quick helper to know if START menu is on-screen so we can
            # disable scripted/auto input that would otherwise cause the menu
            # to scroll uncontrollably.  Criteria: classic START-menu text
            # contains both "POKéMON" and "ITEM" on the same dialog buffer.
            def _start_menu_open() -> bool:
                dlg = (self.env.get_active_dialog() or '')
                return ('POKéMON' in dlg) and ('ITEM' in dlg)
            # Bail out early if START menu is open; StageManager should stay
            # completely passive so the user (or higher-level logic) can
            # navigate the menu.
            if _start_menu_open():
                # Clear any queued presses so they don't fire after closing
                if self._auto_action_queue:
                    print('StageManager: START menu active – clearing auto-action queue')
                self._auto_action_queue.clear()
                return False  # Condition treated as not met
            # ------------------------------------------------------------------
            # SUPER-VERBOSE DEBUGGING ─ print full context before evaluating the
            # condition so we can trace exactly why it does / does not match.
            # ------------------------------------------------------------------
            print("\n[StageManager-DEBUG] --------------------------------------------------")
            print("Checking condition:", condition)
            print("Player   : local=(%d,%d) map_id=%d" % (x, y, map_id))
            if global_coords:
                print("           global=", global_coords)
            current_dialog = (self.env.get_active_dialog() or '').replace("\n", "\\n")
            print("Dialog   : '%s'" % current_dialog)
            print("Flags    : oak_intro_active=%s, pending_b=%d" % (self._oak_intro_active, self.pending_b_presses))
            print("--------------------------------------------------------------------")

            # ------------------------------------------------------------------
            # NEW: Dialog-aware scripted movement conditions
            # ------------------------------------------------------------------
            # Allow conditions to react to in-game dialog text so we can automate
            # the early ‑game boot sequence (skipping intro text, choosing NEW
            # NAME, etc.).  Two complementary keys are supported:
            #   • dialog_contains : <substring>   – True iff substring is in the
            #                                       current active dialog
            #   • dialog_present  : True / False  – True  => any dialog visible
            #                                       False => *no* dialog visible
            # ------------------------------------------------------------------
            if 'dialog_contains' in condition:
                substr = str(condition['dialog_contains'])
                dialog = (self.env.get_active_dialog() or '')
                # Case-insensitive comparison
                dialog_ci = dialog.lower()
                substr_ci = substr.lower()
                # Empty substring – always match (but still require dialog
                # visibility so START spamming only happens while *no* dialog*)
                if substr:
                    if substr_ci not in dialog_ci:
                        # print(f"StageManager DEBUG: dialog_contains check failed – wanted substring '{substr}', active dialog='{dialog}'")
                        return False
                else:
                    # If empty string provided, succeed only when dialog is
                    # non-empty (avoid matching during title screen)
                    if not dialog:
                        # print("StageManager DEBUG: dialog_contains='' check failed – no active dialog visible")
                        return False

            if 'dialog_present' in condition:
                want_present = bool(condition['dialog_present'])
                has_dialog = bool(self.env.get_active_dialog() or '')
                if want_present != has_dialog:
                    # print(f"StageManager DEBUG: dialog_present check failed – want_present={want_present}, has_dialog={has_dialog}")
                    return False
            # Check local coordinates
            if 'local_coords' in condition:
                target_x, target_y = condition['local_coords']
                if (x, y) != (target_x, target_y):
                    # print(f"StageManager DEBUG: local_coords check failed – at ({x},{y}), expected ({target_x},{target_y})")
                    return False
            
            # Check global coordinates
            if 'global_coords' in condition:
                if not global_coords:
                    # print(f"StageManager DEBUG: global_coords check failed – global coordinates not available")
                    return False
                target_global = condition['global_coords']
                if global_coords != target_global:
                    # print(f"StageManager DEBUG: global_coords check failed – at {global_coords}, expected {target_global}")
                    return False
                    
            # Check map ID
            if 'map_id' in condition:
                if map_id != condition['map_id']:
                    # print(f"StageManager DEBUG: map_id check failed – current {map_id}, expected {condition['map_id']}")
                    return False
            
            # Check item possession
            if 'item_check' in condition:
                item_check = condition['item_check']
                item_name = item_check.get('item')
                should_have = item_check.get('has', True)
                
                if item_name and hasattr(self.env, 'item_handler'):
                    has_item = self.env.item_handler.has_item(item_name)
                    if has_item != should_have:
                        # print(f"StageManager DEBUG: item_check failed – item '{item_name}' possession {has_item}, expected {should_have}")
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
                        # print("StageManager DEBUG: pending_b_presses '>0' check failed – none pending")
                        return False
                elif check == True:
                    if self.pending_b_presses <= 0:
                        # print("StageManager DEBUG: pending_b_presses True check failed – none pending")
                        return False
                elif isinstance(check, int):
                    if self.pending_b_presses != check:
                        # print(f"StageManager DEBUG: pending_b_presses=={check} check failed – currently {self.pending_b_presses}")
                        return False
            
            # Check special path following conditions
            if 'always' in condition and condition['always']:
                # This condition always matches
                pass
                
            # --------------------------------------------------------------
            # Custom intro-sequence flag helpers
            # --------------------------------------------------------------
            if 'oak_intro_active' in condition:
                if bool(condition['oak_intro_active']) != self._oak_intro_active:
                    print("StageManager DEBUG: oak_intro_active condition failed – flag is",
                          self._oak_intro_active)
                    return False

            if 'quest_path_active' in condition and condition['quest_path_active']:
                # Check if quest manager has active path following
                if hasattr(self.env, 'quest_manager') and hasattr(self.env.quest_manager, 'warp_blocker'):
                    if not self.env.quest_manager.warp_blocker.path_follower.path_following_active:
                        print("StageManager DEBUG: quest_path_active check failed – no active path following")
                        return False
                else:
                    print("StageManager DEBUG: quest_path_active check failed – quest manager or warp_blocker missing")
                    return False
            
            # If we reach here the condition is satisfied – perform any side-effects
            if condition.get('set_oak_intro_active'):
                print("StageManager: Setting _oak_intro_active = True")
                self._oak_intro_active = True
            if condition.get('clear_oak_intro_active'):
                print("StageManager: Clearing _oak_intro_active flag")
                self._oak_intro_active = False
            
            # --------------------------------------------------------------
            # PARTY SIZE CONDITIONS
            # --------------------------------------------------------------
            # Accept four equivalent keys so stage designers can choose the
            # most intuitive one:
            #   • party_size_lt : current_party_size < value
            #   • party_size_gt : current_party_size > value
            #   • party_size_is : current_party_size == value
            #   • party_size    : alias for equality (==) to maintain
            #                     backwards-compatibility with older rules
            # --------------------------------------------------------------
            if (
                'party_size_lt' in condition or
                'party_size_gt' in condition or
                'party_size_is' in condition or
                'party_size' in condition  # NEW alias
            ):
                try:
                    current_party_size = self.env.read_m('wPartyCount')
                except Exception as _e:
                    # If for some reason we cannot read party size, fail the condition
                    print('StageManager DEBUG: Could not read party size –', _e)
                    return False
                if 'party_size_lt' in condition and not (current_party_size < condition['party_size_lt']):
                    print(f"StageManager DEBUG: party_size_lt check failed – current {current_party_size} !< {condition['party_size_lt']}")
                    return False
                if 'party_size_gt' in condition and not (current_party_size > condition['party_size_gt']):
                    print(f"StageManager DEBUG: party_size_gt check failed – current {current_party_size} !> {condition['party_size_gt']}")
                    return False
                if 'party_size_is' in condition and not (current_party_size == condition['party_size_is']):
                    print(f"StageManager DEBUG: party_size_is check failed – current {current_party_size} != {condition['party_size_is']}")
                    return False
                if 'party_size' in condition and not (current_party_size == condition['party_size']):
                    print(f"StageManager DEBUG: party_size (alias) check failed – current {current_party_size} != {condition['party_size']}")
                    return False
            
            # ----------------------------------------------------------
            # ITEM QUANTITY (>=) CHECK
            # ----------------------------------------------------------
            if 'item_qty' in condition:
                qty_spec = condition['item_qty']
                item_name = qty_spec.get('item')
                target_qty = qty_spec.get('qty', 0)
                if item_name:
                    current_qty = self._get_item_quantity(item_name)
                    if current_qty < target_qty:
                        print(f"StageManager DEBUG: item_qty check failed – {item_name} qty {current_qty} < {target_qty}")
                        return False

            # ----------------------------------------------------------
            # DIALOG TEXT CHECK
            # ----------------------------------------------------------
            if 'dialog_text' in condition:
                dialog_text = condition['dialog_text']
                if dialog_text not in (self.env.get_active_dialog() or ''):
                    print(f"StageManager DEBUG: dialog_text check failed – '{dialog_text}' not in '{self.env.get_active_dialog()}'")
                    return False
            
                
            # ----------------------------------------------------------
            # POKÉDEX CAUGHT CHECK
            # ----------------------------------------------------------
            if 'pokemon_caught' in condition:
                pc_spec = condition['pokemon_caught']
                species_name = pc_spec.get('species')
                should_be_caught = pc_spec.get('caught', True)
                if species_name:
                    try:
                        from environment.data.environment_data.species import Species
                        species_enum = Species[species_name]
                        species_id = species_enum.value  # 1-based ID
                        # env.caught_pokemon is a numpy array of bits (1=caught) indexed from 0
                        caught_flag = False
                        try:
                            caught_flag = bool(self.env.caught_pokemon[species_id - 1])
                        except Exception:
                            # Fallback: if caught_pokemon not available or index error
                            caught_flag = False
                        if caught_flag != should_be_caught:
                            print(
                                f"StageManager DEBUG: pokemon_caught check failed – {species_name} caught={caught_flag}, expected {should_be_caught}"
                            )
                            return False
                    except KeyError:
                        print(f"StageManager DEBUG: Unknown species '{species_name}' in pokemon_caught condition")
                        return False

            # ----------------------------------------------------------
            # PARTY HP FRACTION CHECK (health_fraction)
            # ----------------------------------------------------------
            # Accepts a float (0.0–1.0) or int 0/1.  Useful to verify the
            # whole party is fully healed before leaving a Poké Center or
            # ensure healing is needed before triggering the nurse dialog.
            # ----------------------------------------------------------
            if 'health_fraction' in condition:
                try:
                    desired_frac = float(condition['health_fraction'])
                    current_frac = float(getattr(self.env, 'read_hp_fraction', lambda: 1)())
                    if abs(current_frac - desired_frac) > 1e-3:  # allow tiny numeric error
                        print(
                            f"StageManager DEBUG: health_fraction check failed – current {current_frac:.2f} != {desired_frac:.2f}"
                        )
                        return False
                except Exception as _e:
                    print('StageManager DEBUG: health_fraction check error –', _e)
                    return False

            # ----------------------------------------------------------
            # PARTY POKÉMON SPECIES CHECK
            # ----------------------------------------------------------
            if 'party_pokemon_species_is' in condition:
                target_species_name = condition['party_pokemon_species_is']
                try:
                    from environment.data.environment_data.species import Species
                    party = self.env.party.party[:self.env.party.party_size]
                    species_found = False
                    for p in party:
                        try:
                            species_name_from_party = Species(p.Species).name
                            if species_name_from_party == target_species_name:
                                species_found = True
                                break
                        except Exception:
                            # Skip invalid or empty party slots
                            continue
                    if not species_found:
                        print(f"StageManager DEBUG: party_pokemon_species_is check failed – '{target_species_name}' not in party")
                        return False
                except Exception as _e:
                    print('StageManager DEBUG: Error in party_pokemon_species_is check –', _e)
                    return False

            # ----------------------------------------------------------
            # POKEMON HEALTH CHECKS - for battle automation
            # ----------------------------------------------------------
            if 'pokemon_fainted' in condition:
                # Check if the pokemon mentioned in dialog is fainted
                try:
                    dialog = self.env.get_active_dialog() or ''
                    pokemon_is_fainted = False
                    
                    # Extract pokemon name from dialog
                    if 'CHARMANDER' in dialog:
                        # Check if Charmander (first pokemon) is fainted
                        if hasattr(self.env, 'party') and self.env.party and self.env.party.party_size > 0:
                            charmander = self.env.party.party[0]
                            pokemon_is_fainted = (charmander.HP == 0)
                    elif 'NIDORAN♂' in dialog:
                        # Check if Nidoran (likely second pokemon) is fainted
                        if hasattr(self.env, 'party') and self.env.party and self.env.party.party_size > 1:
                            nidoran = self.env.party.party[1]
                            pokemon_is_fainted = (nidoran.HP == 0)
                    
                    expected_fainted = bool(condition['pokemon_fainted'])
                    if pokemon_is_fainted != expected_fainted:
                        print(f"StageManager DEBUG: pokemon_fainted check failed – fainted={pokemon_is_fainted}, expected {expected_fainted}")
                        return False
                        
                except Exception as e:
                    print(f"StageManager DEBUG: Error checking pokemon_fainted: {e}")
                    return False
            
            if 'pokemon_healthy' in condition:
                # Check if the pokemon mentioned in dialog is healthy (not fainted)
                try:
                    dialog = self.env.get_active_dialog() or ''
                    pokemon_is_healthy = False
                    
                    # Extract pokemon name from dialog
                    if 'CHARMANDER' in dialog:
                        # Check if Charmander (first pokemon) is healthy
                        if hasattr(self.env, 'party') and self.env.party and self.env.party.party_size > 0:
                            charmander = self.env.party.party[0]
                            pokemon_is_healthy = (charmander.HP > 0)
                    elif 'NIDORAN♂' in dialog:
                        # Check if Nidoran (likely second pokemon) is healthy
                        if hasattr(self.env, 'party') and self.env.party and self.env.party.party_size > 1:
                            nidoran = self.env.party.party[1]
                            pokemon_is_healthy = (nidoran.HP > 0)
                    
                    expected_healthy = bool(condition['pokemon_healthy'])
                    if pokemon_is_healthy != expected_healthy:
                        print(f"StageManager DEBUG: pokemon_healthy check failed – healthy={pokemon_is_healthy}, expected {expected_healthy}")
                        return False
                        
                except Exception as e:
                    print(f"StageManager DEBUG: Error checking pokemon_healthy: {e}")
                    return False

            # ----------------------------------------------------------
            # DIALOG CONTAINS CHECK (for partial text matching)
            # ----------------------------------------------------------
            if 'dialog_contains' in condition:
                dialog_substring = condition['dialog_contains']
                dialog = self.env.get_active_dialog() or ''
                if dialog_substring not in dialog:
                    print(f"StageManager DEBUG: dialog_contains check failed – '{dialog_substring}' not in '{dialog}'")
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
    
    def _convert_path_follow_to_movement(self, original_action: int) -> int:
        """
        CONSOLIDATED NAVIGATION: All PATH_FOLLOW_ACTION logic now delegated to ConsolidatedNavigator.
        StageManager no longer handles navigation - this prevents navigation logic conflicts.
        """
        # ------------------------------------------------------------------
        # VIRIDIAN MART SAFEGUARD (Stage 22)
        # ------------------------------------------------------------------
        # When we are inside Viridian Mart buying Poké Balls (stage 22) we must
        # ensure we *do not* exit the shop until we hold at least four balls.
        # Path-follow actions from ConsolidatedNavigator would normally march
        # straight out of the mart once the next quest node is selected, but
        # we want to block that behaviour until the inventory requirement is
        # satisfied.  Instead of forwarding the action to the navigator we
        # simply convert it into an "A" button press so Grok keeps interacting
        # with the shop clerk / purchase menu to acquire the missing balls.
        # ------------------------------------------------------------------
        try:
            if self.stage in (21, 22):
                current_qty = self._get_item_quantity('POKE_BALL')
                if current_qty < 4:
                    # Only intercept once the player has reached the clerk's
                    # tile.  Until then we must allow ConsolidatedNavigator to
                    # generate the movement actions that walk Grok to the
                    # counter.
                    from environment.data.recorder_data.global_map import local_to_global
                    x, y, map_id = self.env.get_game_coords()
                    gy, gx = local_to_global(y, x, map_id)

                    target_global = (299, 132)  # Standing in front of clerk

                    if (gy, gx) == target_global:
                        print(
                            f"StageManager: Stage {self.stage} – at clerk and below ball threshold "
                            f"(have {current_qty}, need 4). Sending 'A' to purchase."
                        )
                        return 4  # 'A' button index
                    else:
                        # Not yet at clerk – manually step toward the counter.
                        dy = target_global[0] - gy  # positive → need to move DOWN (0), negative → UP (3)
                        dx = target_global[1] - gx  # positive → need to move RIGHT (2), negative → LEFT (1)

                        # Prefer vertical alignment first; once aligned on Y, handle X.
                        if dy != 0:
                            action_toward = 0 if dy > 0 else 3
                        elif dx != 0:
                            action_toward = 2 if dx > 0 else 1
                        else:
                            action_toward = 4  # Fallback 'A' press (should not occur here)

                        print(
                            f"StageManager: Stage {self.stage} – guiding toward clerk. "
                            f"Current global={(gy, gx)}, target={target_global}, choosing action={action_toward}."
                        )
                        return action_toward
        except Exception as _e:
            # Non-fatal – fall back to regular navigation handling
            print('StageManager: Error in Stage-22 safeguard –', _e)

        print(f"StageManager: Delegating PATH_FOLLOW_ACTION to ConsolidatedNavigator")
        
        # Delegate all navigation to the ConsolidatedNavigator
        if hasattr(self.env, 'navigator') and self.env.navigator:
            return self.env.navigator.convert_path_follow_to_movement_action(original_action)
        else:
            print(f"StageManager: No ConsolidatedNavigator available - returning original action")
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
    
    def emergency_clear_all_automation(self):
        """Emergency method to clear ALL automation - use when player gets stuck"""
        print("StageManager: EMERGENCY CLEAR - Clearing ALL automation")
        
        # Clear all automation
        self._auto_action_queue.clear()
        self.clear_scripted_movements()
        self.blockings.clear()
        
        # Reset all flags
        self._oak_greet_right_sent = False
        if hasattr(self, '_nido_name_seq_enqueued'):
            self._nido_name_seq_enqueued = False
        if hasattr(self, '_heal_at_poke_center_seq_enqueued'):
            self._heal_at_poke_center_seq_enqueued = False
        
        # Set to undefined stage (no automation)
        self.stage = 999  # High number that has no STAGE_DICT entry
        
        print(f"StageManager: Emergency clear complete - stage set to {self.stage}")
            
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
        
        # Handle stage 10 special blocking logic
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

        # In update_stage_manager method, replace the Stage 1 completion check:
        if self.stage == 1:
            # Only transition to Stage 2 when we're actually in the game world
            # Check for:
            # 1. Map 0 (Pallet Town)
            # 2. NOT at coordinates 0,0 (which is title screen)
            # 3. No intro dialog visible
            current_map = self.env.get_game_coords()[2]
            x, y = self.env.get_game_coords()[:2]
            dialog = self.env.get_active_dialog() or ''
            
            # We're in Pallet Town if:
            # - Map is 0
            # - We're not at 0,0 (title screen position)
            # - No character selection dialog visible
            if (current_map == 0 and 
                (x != 0 or y != 0) and
                'ABCDEFGHIJKLMNOP' not in dialog):  # Character grid dialog
                print("StageManager: Intro complete - now in Pallet Town game world, entering Stage 2")
                self.stage = 2
                self.clear_scripted_movements()
            # Fallback: If we've pressed A on «NEW NAME» more than 25 times
            elif self._stage1_a_press_counter >= 25:
                print("StageManager: Detected stuck intro loop – forcing transition to Stage 2")
                self.stage = 2
                self.clear_scripted_movements()
                self._stage1_a_press_counter = 0  # Reset counter for safety

        # ------------------------------------------------------------------
        # OAK GREET TILE (348,110): press RIGHT once, then spam A until party
        # size > 0 — runs regardless of stage to avoid timing issues.
        # ------------------------------------------------------------------
        try:
            from environment.data.recorder_data.global_map import local_to_global
            x, y, map_id = self.env.get_game_coords()
            cur_global = local_to_global(y, x, map_id)

            if cur_global == (348, 110):
                # Current party size
                party_size = 0
                try:
                    party_size = self.env.read_m('wPartyCount')
                except Exception:
                    pass

                if party_size == 0:
                    if not self._oak_greet_right_sent:
                        self._queue_auto_action('right')
                        self._oak_greet_right_sent = True
                    elif self._frame_counter % 4 == 0:
                        if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                            self._queue_auto_action('a')
                else:
                    # Reset after Pokémon obtained
                    self._oak_greet_right_sent = False
            else:
                # Reset if player is not on the greet tile
                self._oak_greet_right_sent = False
        except Exception as e:
            print('StageManager: Oak greet right/A logic error:', e)

        # ------------------------------------------------------------------
        # ACTIVE INPUT GENERATION FOR QUEST 001
        # ------------------------------------------------------------------
        try:
            self._frame_counter = (self._frame_counter + 1) % 60  # prevent overflow
            if (
                hasattr(self.env, 'quest_manager') and
                getattr(self.env.quest_manager, 'current_quest_id', None) == '001' and
                not self._quest001_completed()
            ):
                dialog_active = bool((self.env.get_active_dialog() or '').strip())
                # Only press A when dialog is visible (to advance text) *or*
                # when player is idle in front of an NPC (no dialog);
                # heuristic: press every 6 frames regardless, StageManager's
                # existing warp/NPC intercepts will handle context.
                if self._frame_counter % 6 == 0:
                    # Avoid flooding queue
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action('a')
        except Exception as e:
            print(f"StageManager: Error in quest001 auto-action logic: {e}")

        # ------------------------------------------------------------------
        # QUEST 023 ▸ Scripted capture of Nidoran ♂
        # ------------------------------------------------------------------
        try:
            if self.stage == 23:
                # Delegate per-frame button generation to dedicated helper.
                self._scripted_catch_nidoran()
        except Exception as e:
            print('StageManager: Nidoran scripted catch error:', e)

        # # --------------------------------------------------------------
        # # STAGE 15: Auto-inject 'A' press when at Blue's sister location without TOWN_MAP
        # # --------------------------------------------------------------
        # try:
        #     print(f"StageManager: DEBUG - Current stage: {self.stage}")
        #     if self.stage == 15:
        #         from environment.data.recorder_data.global_map import local_to_global
        #         x, y, map_id = self.env.get_game_coords()
        #         gy, gx = local_to_global(y, x, map_id)
        #         town_map_qty = self._get_item_quantity('TOWN_MAP')
                
        #         print(f"StageManager: DEBUG - Stage 15, at ({gy}, {gx}), TOWN_MAP qty: {town_map_qty}, frame: {self._frame_counter}")
                
        #         # Check if at Blue's sister location and don't have TOWN_MAP
        #         if (gy, gx) == (340, 107) and town_map_qty == 0:
        #             # Only inject A press every few frames and if queue isn't full
        #             if self._frame_counter % 5 == 0 and len(self._auto_action_queue) < 3:
        #                 print(f"StageManager: Stage 15 - Auto-injecting 'A' press at Blue's sister location")
        #                 self._queue_auto_action('a')
        #             else:
        #                 print(f"StageManager: DEBUG - Skipping A injection: frame % 5 = {self._frame_counter % 5}, queue size: {len(self._auto_action_queue)}")
        # except Exception as e:
        #     print('StageManager: Error in Stage 15 auto-A injection:', e)
        
        # --------------------------------------------------------------
        # Disable Viridian-Mart automation after Poké Balls are stocked
        # --------------------------------------------------------------
        try:
            pokeball_qty = self._get_item_quantity('POKE_BALL')
            if pokeball_qty >= 4 and self.stage in (21, 22):
                print(f"StageManager: {pokeball_qty} Poké Balls in bag – disabling Mart blockings/scripted movements (advance stage)")
                self.stage = 23  # Undefined stage ⇒ no auto config
                self.blockings.clear()
                self.scripted_movements.clear()
        except Exception as e:
            print('StageManager: Error while evaluating Poké Ball quantity:', e)
        
        # --------------------------------------------------------------
        # STAGE 23 COMPLETION - Advance after Nidoran captured or quest completed
        # --------------------------------------------------------------
        try:
            if self.stage == 23:
                # Check if quest 23 is completed (Nidoran captured)
                quest_completed = False
                if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
                    status = getattr(self.env.quest_manager, 'quest_completed_status', {})
                    quest_completed = bool(status.get('023'))
                
                # Also check if we have Nidoran in party as backup
                nidoran_in_party = False
                try:
                    from environment.data.environment_data.species import Species
                    if hasattr(self.env, 'party') and self.env.party:
                        party = self.env.party.party[:self.env.party.party_size]
                        for p in party:
                            try:
                                species_name = Species(p.Species).name
                                if species_name == 'NIDORAN_M':
                                    nidoran_in_party = True
                                    break
                            except Exception:
                                continue
                except Exception as e:
                    print(f"StageManager: Error checking party for Nidoran: {e}")
                
                # Clear stage 23 if quest completed or if we're no longer on quest 23
                current_quest = getattr(self.env.quest_manager, 'current_quest_id', None) if hasattr(self.env, 'quest_manager') else None
                
                if quest_completed or nidoran_in_party or (current_quest and str(current_quest) != '023'):
                    print(f"StageManager: Stage 23 completed - clearing all automation. Quest completed: {quest_completed}, Nidoran in party: {nidoran_in_party}, Current quest: {current_quest}")
                    
                    # EMERGENCY CLEAR - Remove all stage 23 automation
                    self._auto_action_queue.clear()
                    self.clear_scripted_movements()
                    self.blockings.clear()
                    
                    # Reset any stage 23 specific flags
                    if hasattr(self, '_nido_name_seq_enqueued'):
                        self._nido_name_seq_enqueued = False
                    
                    # Advance to undefined stage (no automation)
                    self.stage = 24
                    print(f"StageManager: Advanced to stage {self.stage} - all automation disabled")
        except Exception as e:
            print('StageManager: Error in Stage-23 completion check:', e)
        
        # --------------------------------------------------------------
        # QUEST 026 ▸ Potion pickup monitoring
        # --------------------------------------------------------------
        try:
            if self.stage == 26 and self._compare_items_quantity('POTION'):
                print("StageManager: Potion acquired – Stage 26 completed. Restoring normal behaviour.")

                # Clear any queued auto-actions to prevent stray inputs
                self._auto_action_queue.clear()

                # Remove Stage-26 scripted movements and blockings
                self.clear_scripted_movements()
                self.blockings.clear()

                # Advance to undefined stage (no STAGE_DICT entry = no automation)
                self.stage = 27
        except Exception as e:
            print('StageManager: Error in Stage-26 Potion monitoring:', e)
            
        # talk to viridian city poke mart npc on route 1
        try:
            if self.stage == 6:
                coords = self.env.get_game_coords()[:2]
                facing_direction = self.env._get_direction(self.env.pyboy.game_area())
                in_dialog = self.env.get_active_dialog() or ''
                if coords == (327, 90) and self._get_item_quantity('POTION') == 0 and facing_direction == 'left' and not in_dialog:
                    self._queue_auto_action('a')
        except Exception as e:
            print('StageManager: Error in Stage-26 Potion monitoring:', e)

        # # ------------------------------------------------------------------
        # # DEBUG: Track Oak's parcel and stage transitions - LOG TO FILE
        # # ------------------------------------------------------------------
        # try:
        #     print(f"StageManager: DEBUG - Current stage: {self.stage}")
        #     if self.stage in [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]:
        #         parcel_qty = self._get_item_quantity('OAKS_PARCEL')
        #         town_map_qty = self._get_item_quantity('TOWN_MAP')
        #         x, y, map_id = self.env.get_game_coords()
        #         try:
        #             from environment.data.recorder_data.global_map import local_to_global
        #             gy, gx = local_to_global(y, x, map_id)
        #             global_coords = (gy, gx)
        #         except:
        #             global_coords = "N/A"
                
        #         dialog = (self.env.get_active_dialog() or '').replace('\n', ' ')[:100]
                
        #         # Track parcel changes
        #         previous_parcel_qty = getattr(self, '_previous_parcel_qty', parcel_qty)
        #         if parcel_qty != previous_parcel_qty:
        #             # LOG CRITICAL PARCEL CHANGE TO FILE
        #             parcel_logger.critical(f"PARCEL CHANGE DETECTED! Stage {self.stage} | OAKS_PARCEL: {previous_parcel_qty} -> {parcel_qty} | Global: {global_coords} | Dialog: '{dialog}'")
                    
        #             # Try to identify what might have caused the change
        #             try:
        #                 bag_items = self.env.get_items_in_bag() if hasattr(self.env, 'get_items_in_bag') else []
        #                 parcel_logger.critical(f"PARCEL CHANGE: Current bag contents: {bag_items}")
        #                 parcel_logger.critical(f"PARCEL CHANGE: Bag size: {len(bag_items)}")
                        
        #                 # Also try to get detailed item info
        #                 if hasattr(self.env, 'item_handler'):
        #                     detailed_items = self.env.item_handler.get_items_in_bag()
        #                     detailed_quantities = self.env.item_handler.get_items_quantity_in_bag()
        #                     parcel_logger.critical(f"PARCEL CHANGE: Detailed items: {detailed_items}")
        #                     parcel_logger.critical(f"PARCEL CHANGE: Detailed quantities: {detailed_quantities}")
        #             except Exception as e:
        #                 parcel_logger.error(f"PARCEL CHANGE: Error getting bag items: {e}")
                    
        #             # Also print to console for immediate visibility
        #             print(f"PARCEL CHANGE DETECTED! Check parcel_debug.log for details. Stage {self.stage} | OAKS_PARCEL: {previous_parcel_qty} -> {parcel_qty}")
                
        #         self._previous_parcel_qty = parcel_qty
                
        #         # LOG EVERY SINGLE STEP - NO FRAME LIMITING
        #         parcel_logger.info(f"STEP: Stage {self.stage} | OAKS_PARCEL: {parcel_qty} | TOWN_MAP: {town_map_qty} | Global: {global_coords} | Dialog: '{dialog[:50]}'")
                
        #         # Extra logging when parcel is present
        #         if parcel_qty > 0:
        #             parcel_logger.warning(f"PARCEL PRESENT: Stage {self.stage} | OAKS_PARCEL: {parcel_qty} | Global: {global_coords}")
        # except Exception as e:
        #     parcel_logger.error(f"PARCEL DEBUG ERROR: {e}")
        #     print(f"PARCEL DEBUG ERROR: {e}")

        # ------------------------------------------------------------------
        # OAK GREET TILE (348,110): press RIGHT once, then spam A until party
        # size > 0 — runs regardless of stage to avoid timing issues.
        # ------------------------------------------------------------------
        # try:
        #     from environment.data.recorder_data.global_map import local_to_global
        #     x, y, map_id = self.env.get_game_coords()
        #     cur_global = local_to_global(y, x, map_id)
        #     facing_direction = self.env._get_direction(self.env.pyboy.game_area())

        #     if cur_global == (348, 110):
        #         # Current party size
        #         party_size = 0
        #         try:
        #             party_size = self.env.read_m('wPartyCount')
        #         except Exception:
        #             pass

        #         if party_size == 0:
        #             raise Exception('Party size is 0')
        #             if not self._oak_greet_right_sent:
        #                 self._queue_auto_action('right')
        #                 print(f'\n\nStageManager: Queued auto-action right\n\n')
        #                 self._oak_greet_right_sent = True
        #             elif facing_direction == 'right' and party_size < 1:
        #                 self._queue_auto_action('a')

        #         else:
        #             # Reset after Pokémon obtained
        #             self._oak_greet_right_sent = False
        #     else:
        #         # Reset if player is not on the greet tile
        #         self._oak_greet_right_sent = False
        # except Exception as e:
        #     print('StageManager: Oak greet right/A logic error:', e)



    # ------------------------------------------------------------------
    # Quest/condition helpers
    # ------------------------------------------------------------------
    def _quest001_completed(self) -> bool:
        """Return True when quest 001 is marked as completed by QuestManager."""
        if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
            status = getattr(self.env.quest_manager, 'quest_completed_status', {})
            return bool(status.get('001'))
        return False 

    # ------------------------------------------------------------------
    # Auto-action queue helpers
    # ------------------------------------------------------------------
    def _queue_auto_action(self, action_name_or_int: "str|int") -> None:
        """Enqueue a single button press to be executed on the next frame.

        Args:
            action_name_or_int: Either an integer index that directly maps
                to the environment action space or a mnemonic string ("a",
                "b", "up", …) present in self.action_mapping.
        """
        if isinstance(action_name_or_int, str):
            action_int = self.action_mapping.get(action_name_or_int)
            if action_int is None:
                print(f"StageManager: Unknown action '{action_name_or_int}' – ignoring auto-queue request")
                return
        else:
            action_int = int(action_name_or_int)

        self._auto_action_queue.append(action_int)
        print(f"StageManager: Queued auto-action {action_name_or_int} → {action_int} (queue size: {len(self._auto_action_queue)})") 

    # ------------------------------------------------------------------
    # Helper ▸ Read item quantity in bag by human-readable name (e.g. "POKE_BALL")
    # ------------------------------------------------------------------
    def _get_item_quantity(self, item_name: str) -> int:
        """Return quantity of a specific item currently in the bag.

        Args:
            item_name: Canonical upper-snake item name as in ITEM_NAME_TO_ID_DICT.

        Returns:
            Quantity (int) if item present, else 0.  Falls back to 0 on error.
        """
        try:
            if not (hasattr(self.env, 'item_handler') and self.env.item_handler):
                return 0
            item_id = ITEM_NAME_TO_ID_DICT.get(item_name)
            if item_id is None:
                return 0
            # Directly call the item_handler's get_item_quantity method
            return self.env.item_handler.get_item_quantity(item_id)
        except Exception as e:
            print('StageManager: _get_item_quantity error:', e)
        return 0 
    
    def _scripted_catch_nidoran(self) -> None:
        """Handle dialog transitions during the scripted Nidoran ♂ capture.

        The original implementation only injected **one** batch of inputs and
        then permanently blocked further injections because the helper flag
        `_nido_name_seq_enqueued` stayed *True*.  Once the queued inputs were
        consumed the capture routine stalled and manual input was required.

        We now automatically clear the flag once the auto-action queue is
        empty so subsequent dialog states can enqueue their own dedicated
        button batches.  This turns the helper into a simple state machine
        that can react to *each* dialog screen until the naming prompt is
        reached.
        """

        # ------------------------------------------------------------------
        # ALWAYS CHECK FOR COMPLETION/NAMING DIALOG EACH FRAME
        # ------------------------------------------------------------------
        # These helpers raise RuntimeError when their respective dialogs are
        # detected.  Calling them on every frame guarantees we react
        # immediately before queuing any further button inputs.
        self._caught_nidoran_to_naming_dialog()
        self._caught_nidoran_pokeball_failed()

    
    def _caught_nidoran_pokeball_failed(self):                
        # pokeball failed; clear failed pokeball and enemy attack dialogs
        dlg = self.env.get_active_dialog() or ''
        # Reset helper flag when the previously-queued buttons finished so
        # we can inject new retries after a failed throw.
        if getattr(self, '_nido_name_seq_enqueued', False) and len(self._auto_action_queue) == 0:
            self._nido_name_seq_enqueued = False

        if "Aww!" in dlg:
            print(f'StageManager: "Aww!" dialog detected; _auto_action_queue={self._auto_action_queue}')
            print(f'StageManager: _nido_name_seq_enqueued={getattr(self, "_nido_name_seq_enqueued", False)}')
            button_seq = ['b'] * 10
            for btn in button_seq:
                if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                    self._queue_auto_action(btn)
            self._nido_name_seq_enqueued = True

        # # This dialog appears immediately after the "All right!" message; we treat it the same.
        # if 'NIDORAN♂!' in dlg or 'NIDORAN♂!' in dlg:  # handle unicode variations
        #     raise RuntimeError('StageManager: "NIDORAN♂!" dialog detected – capture sequence successful')

        #     if not getattr(self, '_nido_name_seq_enqueued', False):
        #         button_seq = ['a'] * 6  # slightly shorter but still clears dialogs
        #         for btn in button_seq:
        #             if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
        #                 self._queue_auto_action(btn)
        #         self._nido_name_seq_enqueued = True

    def _caught_nidoran_to_naming_dialog(self):
        """React to battle dialogs during Nidoran capture and enqueue inputs.

        This helper is called every frame by `_scripted_catch_nidoran`.  It
        observes the current dialog string and the enemy HP bar and queues
        context-appropriate button presses.  To prevent flooding, a simple
        latch (`_nido_name_seq_enqueued`) is used – we clear that latch once
        the previously queued actions have run so the next dialog state can
        enqueue its own inputs.
        """

        # return early if not on quest 23
        if not self.env.quest_manager.current_quest_id == 23:
            print(f'StageManager: returning early because not in quest 23.quest id={self.env.quest_manager.current_quest_id}')
            return
        
        # return early if not in battle
        if self.env.read_m(0xD057) == 0:
            print(f'StageManager: returning early because in overworld, not in battle. wIsInBattle={self.env.read_m(0xD057)}')
            return
        
        # return early if not in battle with Nidoran ♂
        dlg = self.env.get_active_dialog() or ''
        if '♂' not in dlg:
            print(f'StageManager: returning early because not in battle with Nidoran ♂. dlg="{dlg}"')
            return
        
        # 1) Allow a new batch once the previous auto-inputs finished.
        if getattr(self, '_nido_name_seq_enqueued', False) and len(self._auto_action_queue) == 0:
            self._nido_name_seq_enqueued = False

        dlg = (self.env.get_active_dialog() or '').strip()

        # Guard against divide-by-zero if the HP struct is momentarily unset.
        try:
            max_hp, cur_hp = self.env.get_enemy_party_head_hp()
            hp_fraction = cur_hp / max_hp if max_hp else 1.0
        except Exception:
            hp_fraction = 1.0

        print(f'StageManager: Nidoran dlg="{dlg}" hp_frac={hp_fraction:.2f}')

        # Phase A ▸ Initial "NIDORAN♂ appeared!" dialog – mash B to reach FIGHT.
        # Only match the actual appeared screen, not any dialog containing NIDORAN♂.
        if 'NIDORAN♂' in dlg and 'appeared!' in dlg and hp_fraction == 1:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                for _ in range(5):
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action('b')
                self._nido_name_seq_enqueued = True
            return

        # Phase B ▸ FIGHT menu visible while HP still full – pick first move (SCRATCH).
        fight_strings = ('▶FIGHT', '▸FIGHT', '\u25baFIGHT', 'u25baFIGHT', '►FIGHT')
        pokemon_strings = ('►PkMn', '\u25baPkMn')
        run_strings = ('►RUN', '\u25baRUN')
        item_strings = ('►ITEM', '\u25baITEM', )
        clear_dialog_strings = ('gained', 'used', 'But', 'failed', 'NIDORAN\u2642 was', 'Shoot!', 'NIDORAN\u2642\nused', 'LEER!', "CHARMANDER's", 'POISON PIN', 'Stiff', 'New POK', 'was', 'Cri', 'Critical', 'will be added')
        current_menu = self.env.get_menu_state()
        
        print(f'StageManager: current_menu={current_menu}')
        # clear all dialogs where we don't need to make a decision                
        if any(fs in dlg for fs in clear_dialog_strings) and not 'give a nickname' in dlg and not 'Do y' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                # B to clear dialogs
                for _ in range(5):
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action('b')
                self._nido_name_seq_enqueued = True
            return

        # Phase B ▸ FIGHT menu visible while HP still full – pick first move (SCRATCH).
        elif any(fs in dlg for fs in fight_strings) and hp_fraction == 1 or '\u25baSCRATCH' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                for _ in range(2):
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action('a')
                self._nido_name_seq_enqueued = True
            return

        # Phase C_0 ▸ FIGHT visible while HP is not full - open ITEM and use poke ball.
        elif any(fs in dlg for fs in fight_strings) and hp_fraction != 1 and not 'give a nickname' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                button_seq = ['down'] + ['a'] + ['down'] * 2 + ['a']
                for btn in button_seq:
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action(btn)
                self._nido_name_seq_enqueued = True
            return
        
        # Phase C_1 ▸ ITEM visible while HP is not full - open ITEM. But index in ITEM is unknown.
        elif any(fs in dlg for fs in item_strings) and hp_fraction != 1 and not 'give a nickname' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                button_seq = ['a']
                for btn in button_seq:
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action(btn)
                self._nido_name_seq_enqueued = True
            return
        
        # Phase C_2 ►CANCEL is visible means cursor is on CANCEL in ITEM.
        elif '►CANCEL' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                button_seq = ['up'] # up will move cursor to last item in ITEM
                for btn in button_seq:
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action(btn)
                self._nido_name_seq_enqueued = True
            return
        
        # Phase C_3 ▸ .
        elif 'POKEMON BALL' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                button_seq = ['a']
                for btn in button_seq:
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action(btn)
                self._nido_name_seq_enqueued = True
            return
        
        # Phase C_4 ▸ POKEMON BALL is visible means cursor is on POKEMON BALL in ITEM.
        
        # # Phase D ▸ Pres A once to get to nickname screen
        elif '\u25baYES\nNO\n' in dlg or 'give a nickname' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                for _ in range(1):
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action('a')
                self._nido_name_seq_enqueued = True
            return

        elif '\u25baPOK\u00e9' in dlg:
            if not getattr(self, '_nido_name_seq_enqueued', False):
                button_seq = ['a']
                for btn in button_seq:
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action(btn)
                self._nido_name_seq_enqueued = True
            return
        
        # # Phase D ▸ Generic fallback for any remaining "was caught" lines.
        # elif 'was' in dlg and hp_fraction != 1:
        #     if not getattr(self, '_nido_name_seq_enqueued', False):
        #         for _ in range(57):
        #             if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
        #                 self._queue_auto_action('a')
        #         self._nido_name_seq_enqueued = True
        #     return
        
    def _heal_at_poke_center(self):
        """Heal at the Poke Center."""
        x, y, map_id = self.env.get_game_coords()
        print(f'StageManager: _heal_at_poke_center x={x} y={y} map_id={map_id}')
        gy, gx = local_to_global(y, x, map_id)
        print(f'StageManager: _heal_at_poke_center gy={gy} gx={gx}')
        dlg = self.env.get_active_dialog() or ''
        
        if x in (1, 2, 3, 4) and self.env._is_pokecenter(map_id) and (
            y in (3, 4, 5, 6, 7, 8)
        ):
            # compute distance from door
            door_y = 7
            counter_y = 2
            dist_from_door = abs(y - door_y)
            dist_from_counter = abs(counter_y - y)
            
            # Reset flag when auto action queue is empty to allow new sequences
            if getattr(self, '_heal_at_poke_center_seq_enqueued', False) and len(self._auto_action_queue) == 0:
                self._heal_at_poke_center_seq_enqueued = False
            
            # Handle specific exit dialogs that need 'b' presses
            if ('Thank you!' in dlg or 'We hope to see' in dlg) and not 'fighting fit' in dlg:
                if not getattr(self, '_heal_at_poke_center_seq_enqueued', False):
                    actions_seq = ['b'] * 2  # Reduced to 2 as requested
                    print(f'StageManager: _heal_at_poke_center exit dialog actions_seq={actions_seq}')
                    for btn in actions_seq:
                        if len(self._auto_action_queue) < self._auto_action_queue.maxlen: 
                            self._queue_auto_action(btn)
                    self._heal_at_poke_center_seq_enqueued = True
                return
            
            print(f'StageManager: _heal_at_poke_center party_hp_fraction={self.env.read_hp_fraction()}')
            
            # Need healing
            if self.env.read_hp_fraction() != 1:
                if not getattr(self, '_heal_at_poke_center_seq_enqueued', False):
                    print(f'StageManager: _heal_at_poke_center party_hp_fraction={self.env.read_hp_fraction()}')
                    # ------------------------------------------------------------------
                    # ALIGN WITH NURSE – The nurse sprite sits behind the centre
                    # counter tile (local X≈4 on the 0-based 10-wide map).  If Grok
                    # approaches from X=1-3 the old logic pressed A while offset
                    # by one tile and the dialog never triggered, leaving him
                    # stuck in a repeat loop.  We first step horizontally until
                    # X == 4, then move up to the counter, then press A×2.
                    # ------------------------------------------------------------------
                    target_x_for_nurse = 3  # centre tile directly in front of Nurse Joy
                    horiz_moves: list[str] = []
                    if x < target_x_for_nurse:
                        horiz_moves = ['right'] * (target_x_for_nurse - x)
                    elif x > target_x_for_nurse:
                        horiz_moves = ['left'] * (x - target_x_for_nurse)

                    # Always include at least one UP tap so the player turns
                    # to face the nurse even when already at the counter
                    min_up_tap = 1 if dist_from_counter == 0 else 0
                    actions_seq = horiz_moves + (['up'] * (dist_from_counter + min_up_tap)) + ['a'] * 2
                    print(f'StageManager: _heal_at_poke_center actions_seq={actions_seq}')
                    for btn in actions_seq:
                        if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                            self._queue_auto_action(btn)
                    self._heal_at_poke_center_seq_enqueued = True
            
            # ------------------------------------------------------------------
            # FULL HEALTH ▸ EXIT ROUTINE
            # ------------------------------------------------------------------
            # Once the party HP fraction is 1.0 we are done healing.  We now
            # need to walk from the counter down to the doorway at local y=7
            # and then take one additional DOWN step to trigger the warp back
            # to Viridian City.  We enqueue *all* required DOWN presses in a
            # single batch so that ConsolidatedNavigator cannot pull Grok
            # northwards, which previously caused an up-down oscillation.
            # ------------------------------------------------------------------
            # elif self.env.read_hp_fraction() == 1:
            #     if not getattr(self, '_heal_at_poke_center_seq_enqueued', False):
            #         # Compute steps and direction to move to warp tile at local y == door_y.
            #         if y < door_y:
            #             steps = door_y - y
            #             direction = 'down'
            #         elif y > door_y:
            #             steps = y - door_y
            #             direction = 'up'
            #         else:
            #             # already on warp tile – need one press to trigger warp
            #             steps = 1
            #             direction = 'down'
            #         actions_seq = [direction] * steps
            #         print(f'StageManager: _heal_at_poke_center exit-to-door actions_seq={actions_seq}')
            #         for btn in actions_seq:
            #             if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
            #                 self._queue_auto_action(btn)
            #         self._heal_at_poke_center_seq_enqueued = True
            
            # ------------------------------------------------------------------
            # Nurse Joy main menu – when "▶HEAL / CANCEL" is displayed we must
            # press A exactly once to select HEAL.  Pressing B will exit and
            # stall the healing routine.  We enqueue a single A press the
            # first time we see the menu, and reset the helper flag once the
            # menu disappears so it can trigger again on future heals.
            # ------------------------------------------------------------------
            if ('HEAL' in dlg and 'CANCEL' in dlg) or ('YES' in dlg and 'NO' in dlg):
                if not getattr(self, '_nurse_heal_menu_enqueued', False):
                    if len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                        self._queue_auto_action('a')
                        self._nurse_heal_menu_enqueued = True
                # Exit early so we don't enqueue other sequences in same frame
                return
            else:
                # Reset flag when menu no longer visible
                if getattr(self, '_nurse_heal_menu_enqueued', False):
                    self._nurse_heal_menu_enqueued = False
    
        # ------------------------------------------------------------------
        # Early-Warp Assistance – if we're outside Pewter Poké Center (map_id 2
        # standing on the tile south of the entrance at local (x=16, y=18))
        # and Pokémon aren't fully healed, queue an UP press so we step onto
        # the warp and enter the Center before running the normal heal logic.
        # ------------------------------------------------------------------
        # SAFETY GUARD (2024-06-22): Do **NOT** trigger the early-warp helper if
        # the ConsolidatedNavigator is currently executing a quest path.  The
        # navigator already knows how to reach the Poké Center when required
        # and StageManager forcibly queuing an UP press while the avatar is
        # aligned horizontally with the doorway—but *not* vertically—makes
        # Grok walk against the wall indefinitely (observed at global
        # coordinates ~278,63 during Quest 26).
        in_nav_path = False
        try:
            if hasattr(self.env, "navigator") and self.env.navigator:
                # A path is considered active when at least one coordinate is
                # loaded.  We also double-check that the navigator believes
                # it is currently "active" (status set by load_coordinate_path)
                nav = self.env.navigator
                in_nav_path = bool(getattr(nav, "sequential_coordinates", [])) and \
                              getattr(nav, "navigation_status", "idle") == "active"
        except Exception:
            # Defensive: never let an attribute error break the heal routine.
            in_nav_path = False

        # Only apply the warp nudge when *not* following a quest path.
        if not in_nav_path:
            if map_id == 2 and self.env.read_hp_fraction() != 1 and not getattr(self, '_heal_at_poke_center_seq_enqueued', False):
                # Accept either of the two tiles directly south of the entrance
                if x == 16 and y in (17, 18) and len(self._auto_action_queue) < self._auto_action_queue.maxlen:
                    self._queue_auto_action('up')
                    self._heal_at_poke_center_seq_enqueued = True
                    return
    
    def _compare_items_quantity(self, item_name: str) -> bool:
        """Detect a change in the quantity of an item in the bag over multiple steps."""
        if not (hasattr(self.env, 'item_handler') and self.env.item_handler):
            return False
        
        item_id = ITEM_NAME_TO_ID_DICT.get(item_name)
        if item_id is None:
            return False
        
        # Initialize or update the previous quantities dictionary
        if not hasattr(self, '_previous_quantities'):
            self._previous_quantities = {}
        
        # Get current quantity directly from item_handler
        current_quantity = self.env.item_handler.get_item_quantity(item_id)
        
        # Get previous quantity, defaulting to current if not found
        previous_quantity = self._previous_quantities.get(item_id, current_quantity)
        
        # Update the stored quantity for the next comparison
        self._previous_quantities[item_id] = current_quantity
        
        # Return True if there is a change in quantity
        return current_quantity != previous_quantity
    
    def _remove_item_from_bag(self, item_name: str):
        """Remove an item from the bag every step."""
        if not (hasattr(self.env, 'item_handler') and self.env.item_handler):
            return
        
        item_id = ITEM_NAME_TO_ID_DICT.get(item_name)
        if item_id is None:
            return
        
        bag_items = self.env.item_handler.get_items_in_bag()
        quantities = self.env.item_handler.get_items_quantity_in_bag()
        
        for idx, iid in enumerate(bag_items):
            if iid == item_id:
                current_quantity = quantities[idx]
                if current_quantity > 0:
                    self.env.item_handler.sell_or_delete_item(is_sell=False, good_item_id=item_id)