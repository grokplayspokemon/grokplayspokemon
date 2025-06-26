# quest_manager.py
# Quest Manager: quest enforcement logic
"""
Quest Manager: micromanage individual quests to ensure the player completes necessary steps.
Currently only implements logic for quest 015 (getting the Town Map from Blue's sister).
"""
from pyboy.utils import WindowEvent
from environment.environment import RedGymEnv, VALID_ACTIONS, PATH_FOLLOW_ACTION
from environment.data.environment_data.items import Items
from environment.data.recorder_data.global_map import local_to_global
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from environment.data.environment_data.item_handler import ItemHandler
from environment.environment_helpers.quest_helper import QuestWarpBlocker

# Simple nurse joy coordinate mapping (global coordinates for standing in front of nurse joy)
NURSE_JOY_COORD_MAP = {
    # Map ID: (global_y, global_x) coordinate for standing in front of nurse joy
    # These would need to be filled in with actual coordinates
    # For now, using placeholder coordinates
}

class QuestManager:
    """
    Orchestrates quest-specific behavior, determines the current active quest based on completion status,
    and can intercept/modify player actions for specific quest steps.
    """

    def __init__(self, env: RedGymEnv, navigator=None, required_completions_path: Optional[str]=None, run_dir: Optional[Path]=None):
        """
        Initialize the QuestManager.
        :param env: The RedGymEnv (or EnvWrapper) instance.
        :param navigator: Optional navigator instance.
        :param required_completions_path: Path to required_completions.json.
        :param run_dir: Directory for the current run, used for saving/loading quest status.
        """
        self.env = env
        self.nav = navigator if navigator is not None else getattr(env, 'navigator', None)
        self.talked_to_nurse_joy = False
        self.current_quest_id = None
        
        self.run_dir = run_dir
        if self.run_dir:
            self.quest_completed_path = self.run_dir / "quest_status.json"
        else:
            # Fallback if run_dir is not provided, though this might indicate an issue
            self.quest_completed_path = Path("quest_status.json") 
            print("Warning: QuestManager initialized without run_dir. Quest status will be local.")

        try:
            rc_file_path = Path(required_completions_path) if required_completions_path else Path(__file__).parent / "required_completions.json"
            
            # Validate file exists before loading
            if not rc_file_path.exists():
                print(f"QuestManager __init__: Quest definitions file not found: {rc_file_path}")
                self.quest_definitions = []
                self.quests_by_location = {}
                return
                
            with rc_file_path.open('r') as f:
                loaded_data = json.load(f)
            
            # Validate the loaded data is a list
            if not isinstance(loaded_data, list):
                print(f"QuestManager __init__: Quest definitions file contains invalid format (not a list): {type(loaded_data)}")
                self.quest_definitions = []
                self.quests_by_location = {}
                return
                
            self.quest_definitions: List[Dict[str, Any]] = loaded_data
            
            # Validate quest definitions and log loading status
            valid_quests = []
            for i, q_def in enumerate(self.quest_definitions):
                if not isinstance(q_def, dict):
                    print(f"QuestManager __init__: Skipping invalid quest definition at index {i} (not a dict): {type(q_def)}")
                    continue
                    
                quest_id = q_def.get("quest_id")
                if quest_id is None:
                    print(f"QuestManager __init__: Skipping quest definition missing quest_id at index {i}: {q_def}")
                    continue
                    
                try:
                    # Ensure quest_id can be converted to int
                    quest_id_int = int(quest_id)
                    valid_quests.append(q_def)
                    print(f"QuestManager __init__: Successfully loaded quest {quest_id_int:03d}: {q_def.get('begin_quest_text', 'No description')[:50]}...")
                except (ValueError, TypeError) as e:
                    print(f"QuestManager __init__: Skipping quest with invalid quest_id '{quest_id}': {e}")
                    continue
            
            self.quest_definitions = valid_quests
            print(f"QuestManager __init__: Successfully loaded {len(self.quest_definitions)} valid quest definitions")
            
            # Build mapping from location to sorted list of quest IDs (as integers)
            quests_by_location: Dict[int, List[int]] = defaultdict(list)
            for q_def in self.quest_definitions:
                try:
                    loc = int(q_def["location_id"])
                    # FIXED: Ensure quest_id is converted to int consistently
                    quest_id_raw = q_def["quest_id"]
                    if isinstance(quest_id_raw, str):
                        quest_id_int = int(quest_id_raw.lstrip('0') or '0')
                    else:
                        quest_id_int = int(quest_id_raw)
                    quests_by_location[loc].append(quest_id_int)
                except (ValueError, TypeError, KeyError) as e:
                    print(f"QuestManager __init__: Error processing quest for location mapping: {q_def.get('quest_id', 'UNKNOWN')}, error: {e}")
                    continue
                    
            for loc, qlist in quests_by_location.items():
                qlist.sort()
            self.quests_by_location = dict(quests_by_location)
            
            print(f"QuestManager __init__: Quest location mapping: {dict(self.quests_by_location)}")

        except Exception as e:
            print(f"QuestManager __init__: Failed to load quest definitions: {e}")
            import traceback
            traceback.print_exc()
            self.quest_definitions = []
            self.quests_by_location = {}

        self.quest_completed_status: Dict[str, bool] = {} # Stores "001": True, "002": False etc.
        self._load_quest_completion_status() # Load initial status

        self.current_quest_id: Optional[int] = None # Will be set by get_current_quest()
        
        # FIXED: Store reference to QuestProgressionEngine once it's created
        self.quest_progression_engine = None
        
        self.a_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A)
        self.up_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP)
        self.down_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN)
        self.left_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT)
        self.right_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT)
        self.b_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B)
        self.start_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START)
        
        self.loaded_paths: Dict[int, List[Tuple[int, int]]] = {}
        self.pending_b_presses = 0
        self.item_handler = ItemHandler(self.env) # Assuming ItemHandler is correctly defined
        self.pressed_button_dict: Dict[int, Dict[Tuple[int, int], Dict[int, int]]] = {} 
        self.last_num_potions = 0
        
        # Initialize simple warp blocker
        self.warp_blocker = QuestWarpBlocker(self.env)

        # Initial call to determine current quest
        self.get_current_quest()

    def _load_quest_completion_status(self):
        """Loads quest completion status from quest_status.json in the run_dir."""
        temp_status: Dict[str, bool] = {}
        try:
            if self.quest_completed_path.exists():
                with self.quest_completed_path.open('r') as f:
                    loaded_json = json.load(f)
                    # Ensure keys are strings like "001"
                    temp_status = {str(k).zfill(3): v for k, v in loaded_json.items()}
        except Exception as e:
            print(f"QuestManager: Error loading quest status from {self.quest_completed_path}: {e}")
        
        # Initialize all known quests, defaulting to False if not in loaded file
        for q_def in self.quest_definitions:
            q_id_str = str(q_def["quest_id"]).zfill(3)
            if q_id_str not in temp_status:
                temp_status[q_id_str] = False
        self.quest_completed_status = temp_status

    def get_current_quest(self) -> Optional[int]:
        """
        Determines the current active quest based on completion status and prerequisites.
        Sets self.current_quest_id and updates env and navigator.
        Returns the current quest ID (int) or None if no quest is currently actionable.
        """
        # PERFORMANCE FIX: Cache quest status and only refresh when actually needed
        # This prevents expensive QuestProgressionEngine calls on every filter_action()
        
        if not hasattr(self, '_cached_quest_status') or not hasattr(self, '_last_status_check'):
            self._cached_quest_status = {}
            self._last_status_check = 0
        
        # Only refresh quest status every 100ms to reduce overhead
        import time
        current_time = time.time()
        if current_time - self._last_status_check > 0.1:  # Refresh every 100ms maximum
            if hasattr(self, 'quest_progression_engine') and self.quest_progression_engine is not None:
                # Get status from QuestProgressionEngine
                quest_status = self.quest_progression_engine.get_quest_status()
                self._cached_quest_status = {str(qid).zfill(3): completed for qid, completed in quest_status.items()}
            else:
                # Fallback to loading from file
                self._load_quest_completion_status() # Ensure we have the latest status
                self._cached_quest_status = self.quest_completed_status.copy()
            
            self._last_status_check = current_time
        
        # Use cached status for quest determination
        self.quest_completed_status = self._cached_quest_status
        
        for quest_def in self.quest_definitions: # Iterate in defined order
            quest_id_str = str(quest_def["quest_id"]).zfill(3)
            quest_id_int = int(quest_def["quest_id"])

            if self.quest_completed_status.get(quest_id_str, False):
                continue # This quest is already completed

            prerequisites_met = True
            required_completions = quest_def.get("required_completions", [])
            
            if required_completions:
                for req_q_id_any_type in required_completions:
                    req_q_id_str = str(req_q_id_any_type).zfill(3)
                    req_status = self.quest_completed_status.get(req_q_id_str, False)
                    if not req_status:
                        prerequisites_met = False
                        break
            
            if prerequisites_met:
                # This is the first uncompleted quest whose prerequisites are met
                if self.current_quest_id != quest_id_int:                    
                    # FIXED: Only do expensive setup work when quest actually changes
                    old_quest_id = self.current_quest_id
                    self.current_quest_id = quest_id_int
                    
                    # Update warp blocker with new quest - only when quest changes
                    self.warp_blocker.update_quest_blocks(self.current_quest_id)
                    
                    # Update stage manager stage to match quest (simple 1:1 mapping for now)
                    if hasattr(self.env, 'stage_manager'):
                        self.env.stage_manager.stage = quest_id_int
                        # Call update to load the stage configuration from STAGE_DICT
                        self.env.stage_manager.update({})
                    
                    if hasattr(self.env, 'current_loaded_quest_id'):
                        self.env.current_loaded_quest_id = self.current_quest_id
                    if self.nav and hasattr(self.nav, 'active_quest_id'):
                        self.nav.active_quest_id = self.current_quest_id
                else:
                    # Quest hasn't changed, no need to do expensive setup work
                    pass
                
                return self.current_quest_id

        # If loop completes, no actionable quest found (e.g., all done)
        if self.current_quest_id is not None:
             self.current_quest_id = None # Explicitly set to None
        if hasattr(self.env, 'current_loaded_quest_id'):
            self.env.current_loaded_quest_id = None
        if self.nav and hasattr(self.nav, 'active_quest_id'):
            self.nav.active_quest_id = None
        return None

    def is_quest_active(self) -> bool:
        """Checks if there is a current active quest."""
        # current_quest_id is updated by get_current_quest()
        return self.current_quest_id is not None

    def get_quest_definition(self, quest_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if quest_id is None:
            return None
        for q_def in self.quest_definitions:
            if int(q_def["quest_id"]) == quest_id:
                return q_def
        return None

    def update_progress(self): # This method might be simplified or its responsibility shifted
        """Called periodically to update quest states or UI. Now mostly a stub."""
        # The actual quest completion is handled by QuestProgressionEngine
        # This manager now focuses on identifying the current quest.
        # UI updates could be driven by the status_queue from QuestProgressionEngine.
        current_q = self.get_current_quest() # Refresh current quest ID
        # print(f"QuestManager.update_progress(): Current quest is {current_q}")
        pass # Most logic moved or handled by QuestProgressionEngine & get_current_quest


    def update_pressed_button_dict(self, coord, action_index, quest, value=1):
        # If the quest ID is not in the dictionary, initialize it
        if quest not in self.pressed_button_dict:
            self.pressed_button_dict[quest] = {}
            print(f'Initialized entry for quest_id: {quest}')

        # If the coordinate is not in the quest's entry, initialize it
        if coord not in self.pressed_button_dict[quest]:
            self.pressed_button_dict[quest][coord] = {}
            print(f'Initialized entry for coordinate: {coord} under quest_id: {quest}')

        # If the action index is not in the coordinate's entry, initialize its count to 0
        if action_index not in self.pressed_button_dict[quest][coord]:
            self.pressed_button_dict[quest][coord][action_index] = 0
            print(f'Initialized count for action_index: {action_index} at coordinate: {coord}')

        # Guarantee that the common "A" button entry always exists so look-ups never
        # raise a KeyError even if it has not been pressed yet.  This is critical
        # for the nurse-joy logic (e.g. Quest 026) which queries the A-button count
        # before the first press is registered.
        if self.a_action_index not in self.pressed_button_dict[quest][coord]:
            self.pressed_button_dict[quest][coord][self.a_action_index] = 0

        # (Optional) store quest id directly under the coordinate entry for easier
        # debugging/inspection – we use a string key to avoid collision with the
        # integer action indices.
        self.pressed_button_dict[quest][coord]["quest_id"] = quest

        # Increment the count for the specific action at the specific coordinate for the specific quest
        self.pressed_button_dict[quest][coord][action_index] += value
        print(f'Incremented count for action_index {action_index} at coordinate {coord} for quest_id {quest}')
    
    def filter_action(self, action: int) -> int:
        """
        Inspect or modify the given action based on quest logic and warp blocking.
        """
        # Early exit: no action to filter
        if action is None:
            return None
        
        # CRITICAL: Do not override actions when dialog is active - player needs to interact
        try:
            dialog = self.env.read_dialog()
            if dialog and dialog.strip():
                return action
        except Exception as e:
            pass
        
        self.get_current_quest() # Ensure current_quest_id is up-to-date

        active_quest_id = self.current_quest_id # Use the ID set by get_current_quest
        
        # If no quest is active, or no specific logic, return original action
        if active_quest_id is None:
            return action

        x, y, map_id = self.env.get_game_coords()
        
        # Store original action to detect if stage manager converted it
        original_action = action
        
        # Apply warp blocking via stage_manager.scripted_stage_blocking()
        # The warp_blocker automatically updates stage_manager.blockings when quest changes
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_stage_blocking'):
            action = self.env.stage_manager.scripted_stage_blocking(action)
        
        # Apply scripted movement via stage_manager.scripted_stage_movement()
        # Allow stage manager to handle ALL actions including PATH_FOLLOW_ACTION for scripted overrides
        from environment.environment import PATH_FOLLOW_ACTION
        if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_stage_movement'):
            print(f"QuestManager: Applying scripted stage movement for action {action}")
            action = self.env.stage_manager.scripted_stage_movement(action)
            print(f"QuestManager: Scripted stage movement returned action {action}")

        # CRITICAL FIX: If stage manager converted PATH_FOLLOW_ACTION to a movement action,
        # do NOT apply hardcoded quest overrides that could break path following
        from environment.environment import PATH_FOLLOW_ACTION
        if original_action == PATH_FOLLOW_ACTION and action != PATH_FOLLOW_ACTION:
            if action in [0, 1, 2, 3]:  # Valid movement actions (UP, DOWN, LEFT, RIGHT)
                return action  # Return stage manager's conversion immediately
            
        # Continue with existing hard-coded logic as fallback
        # TODO: Gradually replace this with more warp blocker rules

        # Example: Keep hard-coded logic, but now it relies on self.current_quest_id
        # This part needs careful review to ensure it aligns with the new quest progression model.
        # Much of this might become obsolete if QuestProgressionEngine handles sub-steps or finer details.
        
        x, y, map_id = self.env.get_game_coords()
        gy, gx = local_to_global(y, x, map_id)

        # Reset pressed_button_dict for a *newly completed* previous quest
        # This logic might be tricky. QuestProgressionEngine now marks completion.
        # This might be better handled by QuestProgressionEngine when a quest completes.
        # For now, let's see if it causes issues.
        # prev_quest_id = active_quest_id - 1 if active_quest_id else 0 
        # if prev_quest_id > 0 and self.quest_completed_status.get(str(prev_quest_id).zfill(3), False):
        #    if prev_quest_id in self.pressed_button_dict:
        #        del self.pressed_button_dict[prev_quest_id]

        # potion from route 1 guy: (x,y) = (85, 340)
        # if active_quest_id == 7:
        #     if (gy, gx) == (85, 340):
        #         # need to determine if a has been pressed to get the potion. without dialog stuff,
        #         # the safest way to do it is check potion quantity in bag before and after.
        #         if self.item_handler.get_item_quantity("POTION") > 0:
        

        
        # if active_quest_id == 2 and map_id == 37:
        #     if (y, x) == (7, 3) or (y, x) == (7, 4):
        #         return self.down_action_index
        
        # if active_quest_id == 3:
        #     if (gy, gx) == (345, 89) and action == self.up_action_index:
        #         return self.down_action_index
        
        # # Hard-coded logic for quest 014: Path following (example, assuming navigator handles it)
        # if active_quest_id == 14:
        #     # Assuming navigator.load_coordinate_path itself checks if path is already loaded for this quest
        #     if self.nav.load_coordinate_path(14): # This method should return bool or raise error
        #          # If path is successfully loaded (or confirmed loaded) for quest 14
        #          # and we are on a coordinate of that path, then trigger path follow.
        #          quest_def_14 = self.get_quest_definition(14)
        #          if quest_def_14 and "associated_coordinates_file" in quest_def_14:
        #              # This is just an example, actual path loading and checking if on path needs to be robust
        #              # For now, if quest 14 is active, assume path following might be needed
        #              # The navigator or env should handle the actual path following action.
        #              # This manager might just signal that PATH_FOLLOW_ACTION is appropriate.
        #              # A more robust way: navigator.is_on_path_for_quest(14)
        #              if self.nav and self.nav.is_on_path_for_quest(14): # Hypothetical method
        #                  return PATH_FOLLOW_ACTION 

        # Hard-coded logic for quest 015 (Town Map and warp)
        if active_quest_id == 15:
            if self.pending_b_presses > 0:
                self.pending_b_presses -= 1
                return self.b_action_index
            if (gy, gx) == (344, 97): # Specific warp entry
                self.pending_b_presses = 3
                return self.up_action_index
            # Enforce A press to get Town Map - This kind of detailed step logic
            # might eventually move to a sub-quest system or be handled by QuestProgressionEngine
            # if specific conditions are met (e.g., standing in front of NPC).
            # For now, keeping the original structure:
            return self._apply_quest_015_rules(action, active_quest_id)

        # DEPRECATED HARDCODED OVERRIDES - These are now handled by stage manager
        # Keeping commented for reference but preventing execution to avoid overriding stage manager
        
        # REMOVED: Quest 5 hardcoded override that was breaking PATH_FOLLOW_ACTION conversion
        # Original problematic code:
        # if active_quest_id == 5 and gy == 338 and gx == 94:
        #     return self.up_action_index
        # This hardcoded logic was overriding stage manager's proper PATH_FOLLOW_ACTION conversion
        # and causing movement failures. Now handled by stage manager's scripted_movements in STAGE_DICT[5]
        
        # if active_quest_id == 2 or active_quest_id == 3:
        #     if active_quest_id == 3:
        #         if (gy, gx) == (340, 94) or (gy, gx) == (340, 95):
        #             return self.up_action_index
        #     # These are very specific coordinate-based actions.
        #     # Consider if these should be defined in coordinate paths or as micro-objectives.
        #     if (gy, gx) == (355, 78) or (gy, gx) == (355, 77): return self.right_action_index
        #     elif (gy, gx) == (343, 89): return self.down_action_index
        #     elif (gy, gx) == (344, 89): return self.down_action_index
        #     elif (gy, gx) == (349, 82): return self.down_action_index
        
        # if active_quest_id == 12: # Talk to Oak
        #     if (gy, gx) == (348, 110): return self.a_action_index
            
        # Talk to Nurse Joy
        if active_quest_id == 18 and not self.talked_to_nurse_joy:
            if "We hope to see" in self.env.read_dialog():
                self.talked_to_nurse_joy = True
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (297, 118):
                return self.a_action_index
            
        # Talk to the clerk at the Pokemart
        if self.current_quest_id == 21:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            # If Grok is standing in front of the Poké Mart clerk, handle the buy routine.
            if (gy, gx) == (299, 132):
                # Always initialise / refresh bag info.
                items_in_bag = self.item_handler.get_items_in_bag()
                items_quantity_in_bag = self.item_handler.get_items_quantity_in_bag()
                item_quantities = dict(zip(items_in_bag, items_quantity_in_bag))

                # Number of Poké Balls currently held (0 if none).
                num_pokeballs = item_quantities.get(Items.POKE_BALL.value, 0)

                # 1) If we already have at least 5 Poké Balls we are done ‑ allow normal actions.
                if num_pokeballs > 4:
                    return action

                # 2) If there is any dialog visible we keep spamming the A button to progress the
                #    shop menus and confirm the purchase.  This is a safe generic strategy that
                #    mirrors what we do for the Pewter Mart potion routine (Quest 35).
                if self.env.read_dialog():
                    return self.a_action_index

                # 3) No dialog → Make sure we actually initiate the conversation with the clerk.
                #    Face left (the clerk is on our left when at (299,132)).  The navigator should
                #    already have positioned Grok correctly so a single A-press is enough.
                return self.a_action_index
        
        # # Hang out until we catch a Nidoran
        # if self.current_quest_id == 23:
        #     x, y, map_id = self.env.get_game_coords()
        #     gy, gx = local_to_global(y, x, map_id)
        #     # make sure no dialog is active
        #     if self.env.read_dialog():
        #         return action
        #     if gy == 285 or gy == 282:
        #         if x <= 67:
        #             return self.up_action_index

        

        if self.current_quest_id == 26:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            print(f'quest_manager.py: filter_action(): current_quest_id={self.current_quest_id}, gy={gy}, gx={gx}')
            # # press a once at (297, 118) to talk to nurse joy
            # if (gy, gx) == (297, 118): # and self.current_quest_id in self.pressed_button_dict:
            #     print(f'quest_manager.py: filter_action(): quest 26 condition true')
            #     # initialize the pressed_button_dict for this quest if it doesn't exist
            #     self.update_pressed_button_dict((gy, gx), self.start_action_index, self.current_quest_id)
            #     print(f'quest_manager.py: filter_action(): pressed_button_dict={self.pressed_button_dict}')
            #     if (gy, gx) in self.pressed_button_dict[self.current_quest_id]:
            #         print(f'quest_manager.py: filter_action(): (gy, gx) in pressed_button_dict: {self.pressed_button_dict[self.current_quest_id][(gy, gx)]}')
            #         # walk away if a has been pressed on this coordinate while not in dialog more than 1 time
            #         if not self.env.read_dialog() and self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] > 1:
            #             self.update_pressed_button_dict((gy, gx), self.down_action_index, self.current_quest_id)
            #             return self.down_action_index
            #         # ensure we actually heal and progress past nurse joy's verbosity
            #         elif self.env.read_dialog() and self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] > 1:
            #             # don't increment the count if we're in dialog
            #             return self.a_action_index
            #         elif self.env.read_hp_fraction() != 1:
            #             return self.a_action_index
            #         else:
            #             # return a_action_index if a has not been pressed on this coordinate
            #             self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id)
            #             # self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] += 1
            #             return self.a_action_index                   
            # press a once at (297, 118) to grab viridian city cut tree potion
            if (gy, gx) == (270, 89)  and self.current_quest_id in self.pressed_button_dict:
                # initialize the pressed_button_dict for this quest if it doesn't exist
                self.update_pressed_button_dict((gy, gx), self.start_action_index, self.current_quest_id)
                if (gy, gx) in self.pressed_button_dict[self.current_quest_id]:
                    # return normal action if a has been pressed on this coordinate
                    if self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] > 0:
                        return action
                    else:
                        # return a_action_index if a has not been pressed on this coordinate
                        self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id)
                        # self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] += 1
                        return self.a_action_index
            # # goes *after* leaving pokemon center
            # print(f"quest_manager.py: filter_action(): gy={gy}, gx={gx}")
            # if (gy, gx) == (292, 97):
            #     print(f"quest_manager.py: filter_action(): self.env.read_hp_fraction(): {self.env.read_hp_fraction()}")
            #     if self.env.read_hp_fraction() == 1:
            #         # Hard-code: load only the '1_again' segment for Quest 026
            #         coord_file = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / "026" / "026_coords.json"
            #         try:
            #             data026 = json.load(coord_file.open('r'))
            #             seg = data026.get("1_again", [])
            #             if seg:
            #                 coords = [(int(c[0]), int(c[1])) for c in seg]
            #                 if self.nav:
            #                     self.nav.sequential_coordinates = coords
            #                     self.nav.coord_map_ids = [1] * len(coords)
            #                     self.nav.current_coordinate_index = 0
            #                     self.nav.active_quest_id = 26
            #                 self.env.current_loaded_quest_id = 26
            #                 print("quest_manager.py: filter_action(): Hard-loaded '1_again' segment for Quest 026")
            #         except Exception as e:
            #             print(f"quest_manager.py: filter_action(): Failed to load '1_again' segment: {e}")
            #         return self.left_action_index
    
        # avoid list index out of bounds while trainer 0 approaches player
        if self.current_quest_id == 29:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            beat_viridian_forest_trainer_0 = int(self.env.read_bit(0xD7F3, 2))
            print(f"quest_manager.py: filter_action(): beat_viridian_forest_trainer_0={beat_viridian_forest_trainer_0}")
            # stop pressing a when actual dialog appears
            if (gy, gx) == (227, 134) and beat_viridian_forest_trainer_0 == 0 and not self.env.read_dialog():
                return self.a_action_index
        
        # avoid list index out of bounds while trainer 1 approaches player
        if self.current_quest_id == 30:
            print(f"quest_manager.py: filter_action(): dialog={self.env.read_dialog()}")
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            beat_viridian_forest_trainer_1 = int(self.env.read_bit(0xD7F3, 3))
            print(f"quest_manager.py: filter_action(): beat_viridian_forest_trainer_1={beat_viridian_forest_trainer_1}")
            # stop pressing a when main battle menu appears
            if (gy, gx) == (227, 134) and beat_viridian_forest_trainer_1 == 0 and "►" not in self.env.read_dialog():
                print(f"quest_manager.py: filter_action(): PRESSING a")
                return self.a_action_index
        
        # avoid list index out of bounds while trainer 2 approaches player
        if self.current_quest_id == 31:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            beat_viridian_forest_trainer_2   = int(self.env.read_bit(0xD7F3, 4))
            print(f"quest_manager.py: filter_action(): beat_viridian_forest_trainer_2={beat_viridian_forest_trainer_2}")
            if (gy, gx) == (121, 134) and beat_viridian_forest_trainer_2 == 0 and not self.env.read_dialog():
                return self.a_action_index
            # # press a once at (220, 133) to grab viridian forest antidote
            # if (gy, gx) == (220, 133) and self.current_quest_id not in self.pressed_button_dict:
            #     self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id)
            #     return self.a_action_index
        
            # press a once at (220, 133) to grab viridian forest antidote
            if (gy, gx) == (220, 133): # and self.current_quest_id in self.pressed_button_dict:
                x, y, map_id = self.env.get_game_coords()
                gy, gx = local_to_global(y, x, map_id)   
                print(f'self.pressed_button_dict: {self.pressed_button_dict}')                
                try:
                    if (gy, gx) in self.pressed_button_dict[self.current_quest_id]:
                        # return normal action if a has been pressed on this coordinate
                        if self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] > 0:
                            return action
                except:
                    # return a_action_index if a has not been pressed on this coordinate
                    self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id)
                    # self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] += 1
                    return self.a_action_index
                
        if self.current_quest_id == 33:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            # Only press A once to talk to Nurse Joy and heal Pokemon at Pewter Poke Center
            if (gy, gx) == (193, 62):
                self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id, 0)
                # spam a through dialog to ensure healing isn't skipped
                if self.env.read_dialog():
                    return self.a_action_index
                
                # only talk to nurse joy once; tracked with pressed_button_dict
                count = self.pressed_button_dict.get(self.current_quest_id, {}).get((gy, gx), {}).get(self.a_action_index, 0)
                if count == 0:                    
                    self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id, 1)
                    return self.a_action_index
                
        # stuck in pewter poke center probably; down leaves the poke center
        if self.current_quest_id == 34 and not self.env.read_dialog():
            if (gy, gx) == (197, 62):
                return self.down_action_index
            if (gy, gx) == (193, 62):
                return self.down_action_index
            if (gy, gx) == (194, 62):
                return self.down_action_index
            if (gy, gx) == (195, 62):
                return self.down_action_index
            if (gy, gx) == (196, 62):
                return self.down_action_index
            if (gy, gx) == (197, 62):
                return self.down_action_index
            
        # map 56 pewter_mart initiate dialog with clerk
        if self.current_quest_id == 35:
            items_in_bag, items_quantity_in_bag = self.item_handler.get_items_in_bag(), self.item_handler.get_items_quantity_in_bag()   
            itdict = dict(zip(items_in_bag, items_quantity_in_bag))
            num_potions = itdict[Items.POTION.value]
            if not self.env.read_dialog():
                self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id, 0)
                self.item_handler.scripted_buy_items()
                if (gy, gx) == (186, 58) and self.pressed_button_dict[self.current_quest_id][(gy, gx)][self.a_action_index] == 0:
                    self.update_pressed_button_dict((gy, gx), self.a_action_index, self.current_quest_id, 1)
                    return self.a_action_index
            else:
                if self.current_quest_id == 35 and self.env.read_dialog() and (gy, gx) == (186, 58):
                    if self.last_num_potions != num_potions:
                        return action
                    else:
                        self.last_num_potions = num_potions
                        pewter_mart_clerk_dialog = self.env.read_dialog()
                        self.item_handler.scripted_buy_items()
                        money = self.item_handler.read_money()
                        print(f"quest_manager.py: filter_action(): money={money}")
                        print(f"quest_manager.py: filter_action(): items_in_bag={items_in_bag}")
                        print(f"quest_manager.py: filter_action(): items_quantity_in_bag={items_quantity_in_bag}")
                        num_potions = items_quantity_in_bag[Items.POTION.value]
                        # max 10 or it could take a long time to shop
                        num_potions_can_afford = max(min(10, money // 300), 0)
                        construct_string = f"A×{num_potions_can_afford}"
                        print(f"quest_manager.py: filter_action(): num_potions_can_afford={num_potions_can_afford}")
                        if pewter_mart_clerk_dialog:
                            # track potions so we don't get stuck in infinite loop
                            self.last_num_potions = num_potions
                            # select buy in top mart menu
                            if "►BUY" in pewter_mart_clerk_dialog:  
                                return self.a_action_index
                            # press down to move cursor to potion in buy mart submenu
                            elif "►POK" in pewter_mart_clerk_dialog:
                                return self.down_action_index
                            # press a to pull up quantity to buy sub-submenu in buy submenu
                            elif "►POTION" in pewter_mart_clerk_dialog:
                                return self.a_action_index
                            # buy the max you can afford (computed) when quantity is selected
                            elif construct_string in pewter_mart_clerk_dialog:
                                return self.a_action_index
                            elif "×" in pewter_mart_clerk_dialog:
                                # pressing down in quantity sub-submenu increments quantity by 1
                                return self.up_action_index
                            # press a to confirm quantity and purchase in buy submenu dialog
                            elif "YES" in pewter_mart_clerk_dialog:
                                # detect change in potion number
                                self.last_num_potions = num_potions
                                # press a to purchase potions
                                return self.a_action_index
        # # Hard-coded logic for quest 037: follow the recorded coordinate path
        # if self.current_quest_id == 37:
        #     if 37 not in self.loaded_paths:
        #         file37 = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / "037" / "037_coords.json"
        #         try:
        #             data37 = json.load(file37.open('r'))
        #             self.loaded_paths[37] = [(int(pair[0]), int(pair[1])) for seg in data37.values() for pair in seg]
        #         except Exception:
        #             self.loaded_paths[37] = []
        #     coords37 = self.loaded_paths[37]
        #     if (gy, gx) in coords37:
        #         self.env.load_coordinate_path(37)
        #         return PATH_FOLLOW_ACTION
        #     if (gy, gx) == (178, 67) and self.env.read_m("wSpritePlayerStateData1FacingDirection") == 0x4:
        #         return self.down_action_index
        #     if (gy, gx) == (177, 67) and self.env.read_m("wSpritePlayerStateData1FacingDirection") == 0x4:
        #         return self.down_action_index
        
        
        # # Not working/not tested
        # # Simplified talk to Nurse Joy for any Pokecenter heal quest if standing in front
        # # Example: if current quest involves healing and player is at (heal_spot_x, heal_spot_y)
        # current_quest_def = self.get_quest_definition(active_quest_id)
        # if current_quest_def and "Heal" in current_quest_def.get("begin_quest_text", ""):
        #    if str(map_id) in self.env.read_tileset() and (gy, gx) == NURSE_JOY_COORD_MAP.get(map_id): # need to obtain local coord for standing in front of nurse joy
        #        return self.a_action_index

        return action

    def _apply_quest_015_rules(self, action: int, current_quest_id: int) -> int:
        """Applies specific action filtering rules for Quest 015."""
        if current_quest_id != 15:
            return action

        x, y, map_id = self.env.get_game_coords()
        gy, gx = local_to_global(y, x, map_id)

        # Logic for ensuring 'A' is pressed to get Town Map (example from original)
        # This relies on specific coordinates and map ID.
        # map_id 39 is Blues House
        # (gy, gx) == (348,97) is standing in front of Daisy (Blue's sister)
        if map_id == 39 and (gy, gx) == (348, 97):
            # Check if Town Map is already obtained; if so, don't force 'A'
            if self.item_handler.has_item("TOWN MAP"):
                return action # Allow normal action if map is obtained
            
            # If 'A' hasn't been pressed enough times at this spot for this quest
            # This count should ideally be managed by a more robust state machine or trigger system
            pressed_count = self.pressed_button_dict.get(15, {}).get((gy, gx), {}).get(self.a_action_index, 0)
            if pressed_count < 2: # Example: force 'A' press twice
                # self.update_pressed_button_dict((gy, gx), self.a_action_index, 15) # This was for counting
                return self.a_action_index
        return action

    def get_current_quest_definition(self) -> Optional[Dict[str, Any]]:
        """Returns the full definition dictionary for the current quest_id."""
        if self.current_quest_id is None:
            self.get_current_quest() # Ensure self.current_quest_id is determined
        
        if self.current_quest_id is not None:
            return self.get_quest_definition(self.current_quest_id)
        return None

    def action_to_index(self, action_str: str) -> int:
        """
        Convert action string to action index.
        
        Args:
            action_str: Action string like "A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START"
            
        Returns:
            int: Action index for the environment
        """
        action_mapping = {
            "DOWN": self.down_action_index,
            "LEFT": self.left_action_index, 
            "RIGHT": self.right_action_index,
            "UP": self.up_action_index,
            "A": self.a_action_index,
            "B": self.b_action_index,
            "START": self.start_action_index,
            "PATH": PATH_FOLLOW_ACTION  # For path following
        }
        
        # Handle case insensitive input
        action_upper = action_str.upper()
        
        if action_upper in action_mapping:
            return action_mapping[action_upper]
        else:
            # Fallback to A button if unknown action
            print(f"QuestManager: Unknown action '{action_str}', defaulting to A button")
            return self.a_action_index

# End of QuestManager enforcement module 

def verify_quest_system_integrity(env, navigator):
    """Comprehensive quest system validation protocol with content verification"""
    
    # Phase 1: File System Verification with correct paths
    quest_files = ['012_coords.json', '013_coords.json', '014_coords.json']
    print("=== COORDINATE FILE ACCESSIBILITY VERIFICATION ===")
    
    for quest_id, file_name in zip([12, 13, 14], quest_files):
        quest_dir_name = f"{quest_id:03d}"
        file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / file_name
        
        status = "EXISTS" if file_path.exists() else "MISSING"
        print(f"Quest file {file_name}: {status}")
        
        if file_path.exists():
            # Validate content structure
            try:
                with open(file_path, 'r') as f:
                    content = json.load(f)
                print(f"  → Structure: {list(content.keys())} maps")
                for map_id, coords in content.items():
                    print(f"    Map {map_id}: {len(coords)} coordinates")
                    if coords:
                        print(f"      First: {coords[0]}, Last: {coords[-1]}")
            except Exception as e:
                print(f"  → Content validation error: {e}")
    
    # Phase 2: Quest Load Sequence Testing with content verification
    print(f"\n=== QUEST LOADING CONTENT VERIFICATION ===")
    for quest_id in [12, 13, 14]:
        print(f"\n--- TESTING QUEST {quest_id:03d} LOAD ---")
        
        # Store original navigator state
        original_coords = navigator.sequential_coordinates.copy() if navigator.sequential_coordinates else []
        original_index = navigator.current_coordinate_index
        original_quest_id = getattr(navigator, 'active_quest_id', None)
        
        # Test quest loading
        success = navigator.load_coordinate_path(quest_id)
        if success:
            print(f"✓ Quest {quest_id:03d}: {len(navigator.sequential_coordinates)} coordinates loaded")
            if navigator.sequential_coordinates:
                print(f"  First: {navigator.sequential_coordinates[0]}")
                print(f"  Last: {navigator.sequential_coordinates[-1]}")
                
                # Content uniqueness verification
                coord_set = set(navigator.sequential_coordinates)
                print(f"  Unique coordinates: {len(coord_set)}/{len(navigator.sequential_coordinates)}")
                
                # Validate against expected content
                quest_dir_name = f"{quest_id:03d}"
                quest_file_name = f"{quest_dir_name}_coords.json"
                file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name
                
                if file_path.exists():
                    with open(file_path, 'r') as f:
                        expected_content = json.load(f)
                    
                    # Flatten expected coordinates for comparison
                    expected_coords = []
                    for map_coords in expected_content.values():
                        expected_coords.extend([tuple(coord) for coord in map_coords])
                    
                    loaded_coords = [tuple(coord) for coord in navigator.sequential_coordinates]
                    
                    if loaded_coords == expected_coords:
                        print(f"  ✓ Content matches file exactly")
                    else:
                        print(f"  ⚠ Content mismatch detected")
                        print(f"    Expected: {len(expected_coords)} coords")
                        print(f"    Loaded: {len(loaded_coords)} coords")
        else:
            print(f"✗ Quest {quest_id:03d}: LOAD FAILED")
        
        # Restore original navigator state
        navigator.sequential_coordinates = original_coords
        navigator.current_coordinate_index = original_index
        navigator.active_quest_id = original_quest_id
    
    # Phase 3: Position Alignment Verification  
    current_pos = navigator._get_player_global_coords()
    print(f"\nCurrent player position: {current_pos}")
    
    return True

def determine_starting_quest(player_pos, map_id, completed_quests, quest_ids_all):
    """Determine the most appropriate starting quest based on player position and game state"""
    
    if map_id == 40:  # Oak's Lab
        # If standing on Quest 12 return path in Lab, prioritize it
        if player_pos and player_pos in [(356, 110), (355, 110), (354, 110), (353, 110), (352, 110), (351, 110), (350, 110), (349, 110), (348, 110)]:
            if not completed_quests.get(12, False):
                return 12
            elif not completed_quests.get(13, False):
                return 13
        # Default Oak's Lab quests: try Quest 11 first, then 12 and 13
        for quest_id in [11, 12, 13]:
            if not completed_quests.get(quest_id, False):
                return quest_id
    
    elif map_id == 0:  # Pallet Town
        # Check if player is on quest 13 or 14 coordinates
        if not completed_quests.get(13, False):
            return 13
        elif not completed_quests.get(14, False):
            return 14
    
    # Fallback: First uncompleted quest
    for quest_id in quest_ids_all:
        if not completed_quests.get(quest_id, False):
            return quest_id
    
    return quest_ids_all[0]  # Ultimate fallback

def describe_trigger(trg):
    """Function to describe trigger criteria for UI display"""
    ttype = trg.get('type')
    if ttype == 'current_map_id_is' or ttype == 'current_map_id':
        return f"Map ID == {trg.get('current_map_id', trg.get('map_id', 'unknown'))}"
    elif ttype == 'previous_map_id_was':
        return f"Previous Map ID == {trg.get('current_map_id', trg.get('map_id', 'unknown'))}"
    elif ttype == 'dialog_contains_text':
        return f"Dialog contains \"{trg['text']}\""
    elif ttype == 'party_size_is':
        return f"Party size == {trg['size']}"
    elif ttype == 'event_completed':
        event_name = trg.get('event_name', '')
        opponent_id = trg.get('opponent_identifier', '')
        if event_name:
            display_text = f"Event completed: {event_name}"
            if opponent_id:
                display_text += f" (vs {opponent_id})"
            return display_text
        else:
            return "Event completed (missing event_name)"
    elif ttype == 'battle_won':
        # Legacy support - redirect to event_completed description
        return f"Battle won vs {trg.get('opponent_identifier','')} (legacy)"
    elif ttype == 'item_received_dialog':
        return f"Item received dialog \"{trg['text']}\""
    elif ttype == 'item_is_in_inventory':
        return f"Inventory has >= {trg.get('quantity_min',1)} x {trg.get('item_name','')}"
    elif ttype == 'party_hp_is_full':
        return "Party HP is full"
    elif ttype == 'current_map_is_previous_map_was':
        current_map_id = trg.get('current_map_id')
        previous_map_id = trg.get('previous_map_id')
        return f"Map transition: {previous_map_id} → {current_map_id}"
    elif ttype == 'party_pokemon_species_is':
        return f"Party contains {trg.get('species_name', '')}"
    elif ttype == 'battle_type_is':
        return f"Battle type is {trg.get('battle_type_name', '')}"
    else:
        return str(trg)

def describe_trigger_logic(trg):
    """Function to describe the actual code/logic being executed for a trigger"""
    ttype = trg.get('type')
    if ttype == 'current_map_id_is' or ttype == 'current_map_id':
        target_map = trg.get('current_map_id', trg.get('map_id', 'unknown'))
        return f"current_map_id == {target_map}"
    elif ttype == 'previous_map_id_was':
        target_map = trg.get('current_map_id', trg.get('map_id', 'unknown'))
        return f"prev_map_id == {target_map}"
    elif ttype == 'dialog_contains_text':
        text = trg['text']
        return f"'{text}' in normalized_dialog"
    elif ttype == 'party_size_is':
        size = trg['size']
        return f"party_size == {size}"
    elif ttype == 'event_completed':
        event_name = trg.get('event_name', '')
        return f"env.events.get_event('{event_name}') == True"
    elif ttype == 'battle_won':
        # Legacy support
        return f"legacy_battle_won_check()"
    elif ttype == 'item_received_dialog':
        text = trg['text']
        return f"'{text}' in item_dialog"
    elif ttype == 'item_is_in_inventory':
        item_name = trg.get('item_name', '')
        quantity_min = trg.get('quantity_min', 1)
        return f"inventory_count('{item_name}') >= {quantity_min}"
    elif ttype == 'party_hp_is_full':
        return "all(pokemon.hp == pokemon.max_hp for pokemon in party)"
    elif ttype == 'current_map_is_previous_map_was':
        current_map_id = trg.get('current_map_id')
        previous_map_id = trg.get('previous_map_id')
        return f"(prev_map == {previous_map_id}) and (curr_map == {current_map_id})"
    elif ttype == 'party_pokemon_species_is':
        species_name = trg.get('species_name', '')
        return f"any(pokemon.species == '{species_name}' for pokemon in party)"
    elif ttype == 'battle_type_is':
        battle_type = trg.get('battle_type_name', '')
        return f"battle_type == '{battle_type}'"
    else:
        return f"unknown_trigger_type('{ttype}')" 
# # quest_manager.py
# # Quest Manager: quest enforcement logic
# """
# Quest Manager: micromanage individual quests to ensure the player completes necessary steps.
# Currently only implements logic for quest 015 (getting the Town Map from Blue's sister).
# """
# from pyboy.utils import WindowEvent
# from environment.environment import RedGymEnv, VALID_ACTIONS, PATH_FOLLOW_ACTION
# from environment.data.environment_data.items import Items
# from environment.data.recorder_data.global_map import local_to_global
# import json
# from pathlib import Path
# from typing import Dict, List, Optional, Any, Tuple
# from collections import defaultdict
# from environment.data.environment_data.item_handler import ItemHandler
# from environment.environment_helpers.quest_helper import QuestWarpBlocker

# # Simple nurse joy coordinate mapping (global coordinates for standing in front of nurse joy)
# NURSE_JOY_COORD_MAP = {
#     # Map ID: (global_y, global_x) coordinate for standing in front of nurse joy
#     # These would need to be filled in with actual coordinates
#     # For now, using placeholder coordinates
# }

# class QuestManager:
#     """
#     Orchestrates quest-specific behavior, determines the current active quest based on completion status,
#     and can intercept/modify player actions for specific quest steps.
#     """

#     def __init__(self, env: RedGymEnv, navigator=None, required_completions_path: Optional[str]=None, run_dir: Optional[Path]=None):
#         """
#         Initialize the QuestManager.
#         :param env: The RedGymEnv (or EnvWrapper) instance.
#         :param navigator: Optional navigator instance.
#         :param required_completions_path: Path to required_completions.json.
#         :param run_dir: Directory for the current run, used for saving/loading quest status.
#         """
#         self.env = env
#         self.nav = navigator if navigator is not None else getattr(env, 'navigator', None)
        
#         self.run_dir = run_dir
#         if self.run_dir:
#             self.quest_completed_path = self.run_dir / "quest_status.json"
#         else:
#             # Fallback if run_dir is not provided, though this might indicate an issue
#             self.quest_completed_path = Path("quest_status.json") 
#             print("Warning: QuestManager initialized without run_dir. Quest status will be local.")

#         try:
#             rc_file_path = Path(required_completions_path) if required_completions_path else Path(__file__).parent / "required_completions.json"
            
#             # Validate file exists before loading
#             if not rc_file_path.exists():
#                 print(f"QuestManager __init__: Quest definitions file not found: {rc_file_path}")
#                 self.quest_definitions = []
#                 self.quests_by_location = {}
#                 return
                
#             with rc_file_path.open('r') as f:
#                 loaded_data = json.load(f)
            
#             # Validate the loaded data is a list
#             if not isinstance(loaded_data, list):
#                 print(f"QuestManager __init__: Quest definitions file contains invalid format (not a list): {type(loaded_data)}")
#                 self.quest_definitions = []
#                 self.quests_by_location = {}
#                 return
                
#             self.quest_definitions: List[Dict[str, Any]] = loaded_data
            
#             # Validate quest definitions and log loading status
#             valid_quests = []
#             for i, q_def in enumerate(self.quest_definitions):
#                 if not isinstance(q_def, dict):
#                     print(f"QuestManager __init__: Skipping invalid quest definition at index {i} (not a dict): {type(q_def)}")
#                     continue
                    
#                 quest_id = q_def.get("quest_id")
#                 if quest_id is None:
#                     print(f"QuestManager __init__: Skipping quest definition missing quest_id at index {i}: {q_def}")
#                     continue
                    
#                 try:
#                     # Ensure quest_id can be converted to int
#                     quest_id_int = int(quest_id)
#                     valid_quests.append(q_def)
#                     print(f"QuestManager __init__: Successfully loaded quest {quest_id_int:03d}: {q_def.get('begin_quest_text', 'No description')[:50]}...")
#                 except (ValueError, TypeError) as e:
#                     print(f"QuestManager __init__: Skipping quest with invalid quest_id '{quest_id}': {e}")
#                     continue
            
#             self.quest_definitions = valid_quests
#             print(f"QuestManager __init__: Successfully loaded {len(self.quest_definitions)} valid quest definitions")
            
#             # Build mapping from location to sorted list of quest IDs (as integers)
#             quests_by_location: Dict[int, List[int]] = defaultdict(list)
#             for q_def in self.quest_definitions:
#                 try:
#                     loc = int(q_def["location_id"])
#                     # FIXED: Ensure quest_id is converted to int consistently
#                     quest_id_raw = q_def["quest_id"]
#                     if isinstance(quest_id_raw, str):
#                         quest_id_int = int(quest_id_raw.lstrip('0') or '0')
#                     else:
#                         quest_id_int = int(quest_id_raw)
#                     quests_by_location[loc].append(quest_id_int)
#                 except (ValueError, TypeError, KeyError) as e:
#                     print(f"QuestManager __init__: Error processing quest for location mapping: {q_def.get('quest_id', 'UNKNOWN')}, error: {e}")
#                     continue
                    
#             for loc, qlist in quests_by_location.items():
#                 qlist.sort()
#             self.quests_by_location = dict(quests_by_location)
            
#             print(f"QuestManager __init__: Quest location mapping: {dict(self.quests_by_location)}")

#         except Exception as e:
#             print(f"QuestManager __init__: Failed to load quest definitions: {e}")
#             import traceback
#             traceback.print_exc()
#             self.quest_definitions = []
#             self.quests_by_location = {}

#         self.quest_completed_status: Dict[str, bool] = {} # Stores "001": True, "002": False etc.
#         self._load_quest_completion_status() # Load initial status

#         self.current_quest_id: Optional[int] = None # Will be set by get_current_quest()
        
#         # FIXED: Store reference to QuestProgressionEngine once it's created
#         self.quest_progression_engine = None
        
#         self.a_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A)
#         self.up_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP)
#         self.down_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN)
#         self.left_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT)
#         self.right_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT)
#         self.b_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B)
#         self.start_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START)
        
#         self.loaded_paths: Dict[int, List[Tuple[int, int]]] = {}
#         self.pending_b_presses = 0
#         self.item_handler = ItemHandler(self.env) # Assuming ItemHandler is correctly defined
#         self.pressed_button_dict: Dict[int, Dict[Tuple[int, int], Dict[int, int]]] = {} 
#         self.last_num_potions = 0
        
#         # Initialize simple warp blocker
#         self.warp_blocker = QuestWarpBlocker(self.env)

#         # Initial call to determine current quest
#         self.get_current_quest()

#     def _load_quest_completion_status(self):
#         """Loads quest completion status from quest_status.json in the run_dir."""
#         temp_status: Dict[str, bool] = {}
#         try:
#             if self.quest_completed_path.exists():
#                 with self.quest_completed_path.open('r') as f:
#                     loaded_json = json.load(f)
#                     # Ensure keys are strings like "001"
#                     temp_status = {str(k).zfill(3): v for k, v in loaded_json.items()}
#         except Exception as e:
#             print(f"QuestManager: Error loading quest status from {self.quest_completed_path}: {e}")
        
#         # Initialize all known quests, defaulting to False if not in loaded file
#         for q_def in self.quest_definitions:
#             q_id_str = str(q_def["quest_id"]).zfill(3)
#             if q_id_str not in temp_status:
#                 temp_status[q_id_str] = False
#         self.quest_completed_status = temp_status

#     def get_current_quest(self) -> Optional[int]:
#         """
#         Determines the current active quest based on completion status and prerequisites.
#         Sets self.current_quest_id and updates env and navigator.
#         Returns the current quest ID (int) or None if no quest is currently actionable.
#         """
#         # PERFORMANCE FIX: Cache quest status and only refresh when actually needed
#         # This prevents expensive QuestProgressionEngine calls on every filter_action()
        
#         if not hasattr(self, '_cached_quest_status') or not hasattr(self, '_last_status_check'):
#             self._cached_quest_status = {}
#             self._last_status_check = 0
        
#         # Only refresh quest status every 100ms to reduce overhead
#         import time
#         current_time = time.time()
#         if current_time - self._last_status_check > 0.1:  # Refresh every 100ms maximum
#             if hasattr(self, 'quest_progression_engine') and self.quest_progression_engine is not None:
#                 # Get status from QuestProgressionEngine
#                 quest_status = self.quest_progression_engine.get_quest_status()
#                 self._cached_quest_status = {str(qid).zfill(3): completed for qid, completed in quest_status.items()}
#             else:
#                 # Fallback to loading from file
#                 self._load_quest_completion_status() # Ensure we have the latest status
#                 self._cached_quest_status = self.quest_completed_status.copy()
            
#             self._last_status_check = current_time
        
#         # Use cached status for quest determination
#         self.quest_completed_status = self._cached_quest_status
        
#         for quest_def in self.quest_definitions: # Iterate in defined order
#             quest_id_str = str(quest_def["quest_id"]).zfill(3)
#             quest_id_int = int(quest_def["quest_id"])

#             if self.quest_completed_status.get(quest_id_str, False):
#                 continue # This quest is already completed

#             prerequisites_met = True
#             required_completions = quest_def.get("required_completions", [])
            
#             if required_completions:
#                 for req_q_id_any_type in required_completions:
#                     req_q_id_str = str(req_q_id_any_type).zfill(3)
#                     req_status = self.quest_completed_status.get(req_q_id_str, False)
#                     if not req_status:
#                         prerequisites_met = False
#                         break
            
#             if prerequisites_met:
#                 # This is the first uncompleted quest whose prerequisites are met
#                 if self.current_quest_id != quest_id_int:                    
#                     # FIXED: Only do expensive setup work when quest actually changes
#                     old_quest_id = self.current_quest_id
#                     self.current_quest_id = quest_id_int
                    
#                     # Update warp blocker with new quest - only when quest changes
#                     self.warp_blocker.update_quest_blocks(self.current_quest_id)
                    
#                     # Update stage manager stage to match quest (simple 1:1 mapping for now)
#                     if hasattr(self.env, 'stage_manager'):
#                         self.env.stage_manager.stage = quest_id_int
#                         # Call update to load the stage configuration from STAGE_DICT
#                         self.env.stage_manager.update({})
                    
#                     if hasattr(self.env, 'current_loaded_quest_id'):
#                         self.env.current_loaded_quest_id = self.current_quest_id
#                     if self.nav and hasattr(self.nav, 'active_quest_id'):
#                         self.nav.active_quest_id = self.current_quest_id
#                 else:
#                     # Quest hasn't changed, no need to do expensive setup work
#                     pass
                
#                 return self.current_quest_id

#         # If loop completes, no actionable quest found (e.g., all done)
#         if self.current_quest_id is not None:
#              self.current_quest_id = None # Explicitly set to None
#         if hasattr(self.env, 'current_loaded_quest_id'):
#             self.env.current_loaded_quest_id = None
#         if self.nav and hasattr(self.nav, 'active_quest_id'):
#             self.nav.active_quest_id = None
#         return None

#     def is_quest_active(self) -> bool:
#         """Checks if there is a current active quest."""
#         # current_quest_id is updated by get_current_quest()
#         return self.current_quest_id is not None

#     def get_quest_definition(self, quest_id: Optional[int]) -> Optional[Dict[str, Any]]:
#         if quest_id is None:
#             return None
#         for q_def in self.quest_definitions:
#             if int(q_def["quest_id"]) == quest_id:
#                 return q_def
#         return None

#     def update_progress(self): # This method might be simplified or its responsibility shifted
#         """Called periodically to update quest states or UI. Now mostly a stub."""
#         # The actual quest completion is handled by QuestProgressionEngine
#         # This manager now focuses on identifying the current quest.
#         # UI updates could be driven by the status_queue from QuestProgressionEngine.
#         current_q = self.get_current_quest() # Refresh current quest ID
#         # print(f"QuestManager.update_progress(): Current quest is {current_q}")
#         pass # Most logic moved or handled by QuestProgressionEngine & get_current_quest

#     def filter_action(self, action: int) -> int:
#         """
#         Inspect or modify the given action based on quest logic and warp blocking.
#         """
#         # Early exit: no action to filter
#         if action is None:
#             return None
        
#         # CRITICAL: Do not override actions when dialog is active - player needs to interact
#         try:
#             dialog = self.env.read_dialog()
#             if dialog and dialog.strip():
#                 return action
#         except Exception as e:
#             pass
        
#         self.get_current_quest() # Ensure current_quest_id is up-to-date

#         active_quest_id = self.current_quest_id # Use the ID set by get_current_quest
        
#         # If no quest is active, or no specific logic, return original action
#         if active_quest_id is None:
#             return action

#         x, y, map_id = self.env.get_game_coords()
        
#         # Store original action to detect if stage manager converted it
#         original_action = action
        
#         # Apply warp blocking via stage_manager.scripted_stage_blocking()
#         # The warp_blocker automatically updates stage_manager.blockings when quest changes
#         if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_stage_blocking'):
#             action = self.env.stage_manager.scripted_stage_blocking(action)
        
#         # Apply scripted movement via stage_manager.scripted_stage_movement()
#         if hasattr(self.env, 'stage_manager') and hasattr(self.env.stage_manager, 'scripted_stage_movement'):
#             print(f"QuestManager: Applying scripted stage movement for action {action}")
#             action = self.env.stage_manager.scripted_stage_movement(action)
#             print(f"QuestManager: Scripted stage movement returned action {action}")

#         # CRITICAL FIX: If stage manager converted PATH_FOLLOW_ACTION to a movement action,
#         # do NOT apply hardcoded quest overrides that could break path following
#         from environment.environment import PATH_FOLLOW_ACTION
#         if original_action == PATH_FOLLOW_ACTION and action != PATH_FOLLOW_ACTION:
#             if action in [0, 1, 2, 3]:  # Valid movement actions (UP, DOWN, LEFT, RIGHT)
#                 return action  # Return stage manager's conversion immediately
            
#         # Continue with existing hard-coded logic as fallback
#         # TODO: Gradually replace this with more warp blocker rules

#         # Example: Keep hard-coded logic, but now it relies on self.current_quest_id
#         # This part needs careful review to ensure it aligns with the new quest progression model.
#         # Much of this might become obsolete if QuestProgressionEngine handles sub-steps or finer details.
        
#         x, y, map_id = self.env.get_game_coords()
#         gy, gx = local_to_global(y, x, map_id)

#         # Reset pressed_button_dict for a *newly completed* previous quest
#         # This logic might be tricky. QuestProgressionEngine now marks completion.
#         # This might be better handled by QuestProgressionEngine when a quest completes.
#         # For now, let's see if it causes issues.
#         # prev_quest_id = active_quest_id - 1 if active_quest_id else 0 
#         # if prev_quest_id > 0 and self.quest_completed_status.get(str(prev_quest_id).zfill(3), False):
#         #    if prev_quest_id in self.pressed_button_dict:
#         #        del self.pressed_button_dict[prev_quest_id]

#         # potion from route 1 guy: (x,y) = (85, 340)
#         # if active_quest_id == 7:
#         #     if (gy, gx) == (85, 340):
#         #         # need to determine if a has been pressed to get the potion. without dialog stuff,
#         #         # the safest way to do it is check potion quantity in bag before and after.
#         #         if self.item_handler.get_item_quantity("POTION") > 0:
        

        
#         # if active_quest_id == 2 and map_id == 37:
#         #     if (y, x) == (7, 3) or (y, x) == (7, 4):
#         #         return self.down_action_index
        
#         # if active_quest_id == 3:
#         #     if (gy, gx) == (345, 89) and action == self.up_action_index:
#         #         return self.down_action_index
        
#         # Hard-coded logic for quest 014: Path following (example, assuming navigator handles it)
#         if active_quest_id == 14:
#             # Assuming navigator.load_coordinate_path itself checks if path is already loaded for this quest
#             if self.nav.load_coordinate_path(14): # This method should return bool or raise error
#                  # If path is successfully loaded (or confirmed loaded) for quest 14
#                  # and we are on a coordinate of that path, then trigger path follow.
#                  quest_def_14 = self.get_quest_definition(14)
#                  if quest_def_14 and "associated_coordinates_file" in quest_def_14:
#                      # This is just an example, actual path loading and checking if on path needs to be robust
#                      # For now, if quest 14 is active, assume path following might be needed
#                      # The navigator or env should handle the actual path following action.
#                      # This manager might just signal that PATH_FOLLOW_ACTION is appropriate.
#                      # A more robust way: navigator.is_on_path_for_quest(14)
#                      if self.nav and self.nav.is_on_path_for_quest(14): # Hypothetical method
#                          return PATH_FOLLOW_ACTION 

#         # Hard-coded logic for quest 015 (Town Map and warp)
#         if active_quest_id == 15:
#             if self.pending_b_presses > 0:
#                 self.pending_b_presses -= 1
#                 return self.b_action_index
#             if (gy, gx) == (344, 97): # Specific warp entry
#                 self.pending_b_presses = 3
#                 return self.up_action_index
#             # Enforce A press to get Town Map - This kind of detailed step logic
#             # might eventually move to a sub-quest system or be handled by QuestProgressionEngine
#             # if specific conditions are met (e.g., standing in front of NPC).
#             # For now, keeping the original structure:
#             return self._apply_quest_015_rules(action, active_quest_id)

#         # DEPRECATED HARDCODED OVERRIDES - These are now handled by stage manager
#         # Keeping commented for reference but preventing execution to avoid overriding stage manager
        
#         # REMOVED: Quest 5 hardcoded override that was breaking PATH_FOLLOW_ACTION conversion
#         # Original problematic code:
#         # if active_quest_id == 5 and gy == 338 and gx == 94:
#         #     return self.up_action_index
#         # This hardcoded logic was overriding stage manager's proper PATH_FOLLOW_ACTION conversion
#         # and causing movement failures. Now handled by stage manager's scripted_movements in STAGE_DICT[5]
        
#         # if active_quest_id == 2 or active_quest_id == 3:
#         #     if active_quest_id == 3:
#         #         if (gy, gx) == (340, 94) or (gy, gx) == (340, 95):
#         #             return self.up_action_index
#         #     # These are very specific coordinate-based actions.
#         #     # Consider if these should be defined in coordinate paths or as micro-objectives.
#         #     if (gy, gx) == (355, 78) or (gy, gx) == (355, 77): return self.right_action_index
#         #     elif (gy, gx) == (343, 89): return self.down_action_index
#         #     elif (gy, gx) == (344, 89): return self.down_action_index
#         #     elif (gy, gx) == (349, 82): return self.down_action_index
        
#         if active_quest_id == 12: # Talk to Oak
#             if (gy, gx) == (348, 110): return self.a_action_index
        
#         # Simplified talk to Nurse Joy for any Pokecenter heal quest if standing in front
#         # Example: if current quest involves healing and player is at (heal_spot_x, heal_spot_y)
#         current_quest_def = self.get_quest_definition(active_quest_id)
#         if current_quest_def and "Heal" in current_quest_def.get("begin_quest_text", ""):
#            if map_id in self.env.read_tileset() and (gy, gx) == NURSE_JOY_COORD_MAP.get(map_id): # need to obtain local coord for standing in front of nurse joy
#                return self.a_action_index

#         return action

#     def _apply_quest_015_rules(self, action: int, current_quest_id: int) -> int:
#         """Applies specific action filtering rules for Quest 015."""
#         if current_quest_id != 15:
#             return action

#         x, y, map_id = self.env.get_game_coords()
#         gy, gx = local_to_global(y, x, map_id)

#         # Logic for ensuring 'A' is pressed to get Town Map (example from original)
#         # This relies on specific coordinates and map ID.
#         # map_id 39 is Blues House
#         # (gy, gx) == (348,97) is standing in front of Daisy (Blue's sister)
#         if map_id == 39 and (gy, gx) == (348, 97):
#             # Check if Town Map is already obtained; if so, don't force 'A'
#             if self.item_handler.has_item("TOWN MAP"):
#                 return action # Allow normal action if map is obtained
            
#             # If 'A' hasn't been pressed enough times at this spot for this quest
#             # This count should ideally be managed by a more robust state machine or trigger system
#             pressed_count = self.pressed_button_dict.get(15, {}).get((gy, gx), {}).get(self.a_action_index, 0)
#             if pressed_count < 2: # Example: force 'A' press twice
#                 # self.update_pressed_button_dict((gy, gx), self.a_action_index, 15) # This was for counting
#                 return self.a_action_index
#         return action

#     def get_current_quest_definition(self) -> Optional[Dict[str, Any]]:
#         """Returns the full definition dictionary for the current quest_id."""
#         if self.current_quest_id is None:
#             self.get_current_quest() # Ensure current_quest_id is determined
        
#         if self.current_quest_id is not None:
#             return self.get_quest_definition(self.current_quest_id)
#         return None

#     def action_to_index(self, action_str: str) -> int:
#         """
#         Convert action string to action index.
        
#         Args:
#             action_str: Action string like "A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START"
            
#         Returns:
#             int: Action index for the environment
#         """
#         action_mapping = {
#             "DOWN": self.down_action_index,
#             "LEFT": self.left_action_index, 
#             "RIGHT": self.right_action_index,
#             "UP": self.up_action_index,
#             "A": self.a_action_index,
#             "B": self.b_action_index,
#             "START": self.start_action_index,
#             "PATH": PATH_FOLLOW_ACTION  # For path following
#         }
        
#         # Handle case insensitive input
#         action_upper = action_str.upper()
        
#         if action_upper in action_mapping:
#             return action_mapping[action_upper]
#         else:
#             # Fallback to A button if unknown action
#             print(f"QuestManager: Unknown action '{action_str}', defaulting to A button")
#             return self.a_action_index

# # End of QuestManager enforcement module 

# def verify_quest_system_integrity(env, navigator):
#     """Comprehensive quest system validation protocol with content verification"""
    
#     # Phase 1: File System Verification with correct paths
#     quest_files = ['012_coords.json', '013_coords.json', '014_coords.json']
#     print("=== COORDINATE FILE ACCESSIBILITY VERIFICATION ===")
    
#     for quest_id, file_name in zip([12, 13, 14], quest_files):
#         quest_dir_name = f"{quest_id:03d}"
#         file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / file_name
        
#         status = "EXISTS" if file_path.exists() else "MISSING"
#         print(f"Quest file {file_name}: {status}")
        
#         if file_path.exists():
#             # Validate content structure
#             try:
#                 with open(file_path, 'r') as f:
#                     content = json.load(f)
#                 print(f"  → Structure: {list(content.keys())} maps")
#                 for map_id, coords in content.items():
#                     print(f"    Map {map_id}: {len(coords)} coordinates")
#                     if coords:
#                         print(f"      First: {coords[0]}, Last: {coords[-1]}")
#             except Exception as e:
#                 print(f"  → Content validation error: {e}")
    
#     # Phase 2: Quest Load Sequence Testing with content verification
#     print(f"\n=== QUEST LOADING CONTENT VERIFICATION ===")
#     for quest_id in [12, 13, 14]:
#         print(f"\n--- TESTING QUEST {quest_id:03d} LOAD ---")
        
#         # Store original navigator state
#         original_coords = navigator.sequential_coordinates.copy() if navigator.sequential_coordinates else []
#         original_index = navigator.current_coordinate_index
#         original_quest_id = getattr(navigator, 'active_quest_id', None)
        
#         # Test quest loading
#         success = navigator.load_coordinate_path(quest_id)
#         if success:
#             print(f"✓ Quest {quest_id:03d}: {len(navigator.sequential_coordinates)} coordinates loaded")
#             if navigator.sequential_coordinates:
#                 print(f"  First: {navigator.sequential_coordinates[0]}")
#                 print(f"  Last: {navigator.sequential_coordinates[-1]}")
                
#                 # Content uniqueness verification
#                 coord_set = set(navigator.sequential_coordinates)
#                 print(f"  Unique coordinates: {len(coord_set)}/{len(navigator.sequential_coordinates)}")
                
#                 # Validate against expected content
#                 quest_dir_name = f"{quest_id:03d}"
#                 quest_file_name = f"{quest_dir_name}_coords.json"
#                 file_path = Path(__file__).parent / "quest_paths" / quest_dir_name / quest_file_name
                
#                 if file_path.exists():
#                     with open(file_path, 'r') as f:
#                         expected_content = json.load(f)
                    
#                     # Flatten expected coordinates for comparison
#                     expected_coords = []
#                     for map_coords in expected_content.values():
#                         expected_coords.extend([tuple(coord) for coord in map_coords])
                    
#                     loaded_coords = [tuple(coord) for coord in navigator.sequential_coordinates]
                    
#                     if loaded_coords == expected_coords:
#                         print(f"  ✓ Content matches file exactly")
#                     else:
#                         print(f"  ⚠ Content mismatch detected")
#                         print(f"    Expected: {len(expected_coords)} coords")
#                         print(f"    Loaded: {len(loaded_coords)} coords")
#         else:
#             print(f"✗ Quest {quest_id:03d}: LOAD FAILED")
        
#         # Restore original navigator state
#         navigator.sequential_coordinates = original_coords
#         navigator.current_coordinate_index = original_index
#         navigator.active_quest_id = original_quest_id
    
#     # Phase 3: Position Alignment Verification  
#     current_pos = navigator._get_player_global_coords()
#     print(f"\nCurrent player position: {current_pos}")
    
#     return True

# def determine_starting_quest(player_pos, map_id, completed_quests, quest_ids_all):
#     """Determine the most appropriate starting quest based on player position and game state"""
    
#     if map_id == 40:  # Oak's Lab
#         # If standing on Quest 12 return path in Lab, prioritize it
#         if player_pos and player_pos in [(356, 110), (355, 110), (354, 110), (353, 110), (352, 110), (351, 110), (350, 110), (349, 110), (348, 110)]:
#             if not completed_quests.get(12, False):
#                 return 12
#             elif not completed_quests.get(13, False):
#                 return 13
#         # Default Oak's Lab quests: try Quest 11 first, then 12 and 13
#         for quest_id in [11, 12, 13]:
#             if not completed_quests.get(quest_id, False):
#                 return quest_id
    
#     elif map_id == 0:  # Pallet Town
#         # Check if player is on quest 13 or 14 coordinates
#         if not completed_quests.get(13, False):
#             return 13
#         elif not completed_quests.get(14, False):
#             return 14
    
#     # Fallback: First uncompleted quest
#     for quest_id in quest_ids_all:
#         if not completed_quests.get(quest_id, False):
#             return quest_id
    
#     return quest_ids_all[0]  # Ultimate fallback

# def describe_trigger(trg):
#     """Function to describe trigger criteria for UI display"""
#     ttype = trg.get('type')
#     if ttype == 'current_map_id_is' or ttype == 'current_map_id':
#         return f"Map ID == {trg.get('current_map_id', trg.get('map_id', 'unknown'))}"
#     elif ttype == 'previous_map_id_was':
#         return f"Previous Map ID == {trg.get('current_map_id', trg.get('map_id', 'unknown'))}"
#     elif ttype == 'dialog_contains_text':
#         return f"Dialog contains \"{trg['text']}\""
#     elif ttype == 'party_size_is':
#         return f"Party size == {trg['size']}"
#     elif ttype == 'event_completed':
#         event_name = trg.get('event_name', '')
#         opponent_id = trg.get('opponent_identifier', '')
#         if event_name:
#             display_text = f"Event completed: {event_name}"
#             if opponent_id:
#                 display_text += f" (vs {opponent_id})"
#             return display_text
#         else:
#             return "Event completed (missing event_name)"
#     elif ttype == 'battle_won':
#         # Legacy support - redirect to event_completed description
#         return f"Battle won vs {trg.get('opponent_identifier','')} (legacy)"
#     elif ttype == 'item_received_dialog':
#         return f"Item received dialog \"{trg['text']}\""
#     elif ttype == 'item_is_in_inventory':
#         return f"Inventory has >= {trg.get('quantity_min',1)} x {trg.get('item_name','')}"
#     elif ttype == 'party_hp_is_full':
#         return "Party HP is full"
#     elif ttype == 'current_map_is_previous_map_was':
#         current_map_id = trg.get('current_map_id')
#         previous_map_id = trg.get('previous_map_id')
#         return f"Map transition: {previous_map_id} → {current_map_id}"
#     elif ttype == 'party_pokemon_species_is':
#         return f"Party contains {trg.get('species_name', '')}"
#     elif ttype == 'battle_type_is':
#         return f"Battle type is {trg.get('battle_type_name', '')}"
#     else:
#         return str(trg)

# def describe_trigger_logic(trg):
#     """Function to describe the actual code/logic being executed for a trigger"""
#     ttype = trg.get('type')
#     if ttype == 'current_map_id_is' or ttype == 'current_map_id':
#         target_map = trg.get('current_map_id', trg.get('map_id', 'unknown'))
#         return f"current_map_id == {target_map}"
#     elif ttype == 'previous_map_id_was':
#         target_map = trg.get('current_map_id', trg.get('map_id', 'unknown'))
#         return f"prev_map_id == {target_map}"
#     elif ttype == 'dialog_contains_text':
#         text = trg['text']
#         return f"'{text}' in normalized_dialog"
#     elif ttype == 'party_size_is':
#         size = trg['size']
#         return f"party_size == {size}"
#     elif ttype == 'event_completed':
#         event_name = trg.get('event_name', '')
#         return f"env.events.get_event('{event_name}') == True"
#     elif ttype == 'battle_won':
#         # Legacy support
#         return f"legacy_battle_won_check()"
#     elif ttype == 'item_received_dialog':
#         text = trg['text']
#         return f"'{text}' in item_dialog"
#     elif ttype == 'item_is_in_inventory':
#         item_name = trg.get('item_name', '')
#         quantity_min = trg.get('quantity_min', 1)
#         return f"inventory_count('{item_name}') >= {quantity_min}"
#     elif ttype == 'party_hp_is_full':
#         return "all(pokemon.hp == pokemon.max_hp for pokemon in party)"
#     elif ttype == 'current_map_is_previous_map_was':
#         current_map_id = trg.get('current_map_id')
#         previous_map_id = trg.get('previous_map_id')
#         return f"(prev_map == {previous_map_id}) and (curr_map == {current_map_id})"
#     elif ttype == 'party_pokemon_species_is':
#         species_name = trg.get('species_name', '')
#         return f"any(pokemon.species == '{species_name}' for pokemon in party)"
#     elif ttype == 'battle_type_is':
#         battle_type = trg.get('battle_type_name', '')
#         return f"battle_type == '{battle_type}'"
#     else:
#         return f"unknown_trigger_type('{ttype}')" 

