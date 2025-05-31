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
        
        self.run_dir = run_dir
        if self.run_dir:
            self.quest_completed_path = self.run_dir / "quest_status.json"
        else:
            # Fallback if run_dir is not provided, though this might indicate an issue
            self.quest_completed_path = Path("quest_status.json") 
            print("Warning: QuestManager initialized without run_dir. Quest status will be local.")

        try:
            rc_file_path = Path(required_completions_path) if required_completions_path else Path(__file__).parent / "required_completions.json"
            with rc_file_path.open('r') as f:
                self.quest_definitions: List[Dict[str, Any]] = json.load(f)
            
            # Build mapping from location to sorted list of quest IDs (as integers)
            quests_by_location: Dict[int, List[int]] = defaultdict(list)
            for q_def in self.quest_definitions:
                loc = int(q_def["location_id"])
                quests_by_location[loc].append(int(q_def["quest_id"]))
            for loc, qlist in quests_by_location.items():
                qlist.sort()
            self.quests_by_location = dict(quests_by_location)

        except Exception as e:
            print(f"QuestManager __init__: Failed to load quest definitions: {e}")
            self.quest_definitions = []
            self.quests_by_location = {}

        self.quest_completed_status: Dict[str, bool] = {} # Stores "001": True, "002": False etc.
        self._load_quest_completion_status() # Load initial status

        self.current_quest_id: Optional[int] = None # Will be set by get_current_quest()
        
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
        self._load_quest_completion_status() # Ensure we have the latest status

        for quest_def in self.quest_definitions: # Iterate in defined order
            quest_id_str = str(quest_def["quest_id"]).zfill(3)
            quest_id_int = int(quest_def["quest_id"])

            if self.quest_completed_status.get(quest_id_str, False):
                continue # This quest is already completed

            prerequisites_met = True
            if "required_completions" in quest_def:
                for req_q_id_any_type in quest_def["required_completions"]:
                    req_q_id_str = str(req_q_id_any_type).zfill(3)
                    if not self.quest_completed_status.get(req_q_id_str, False):
                        prerequisites_met = False
                        break
            
            if prerequisites_met:
                # This is the first uncompleted quest whose prerequisites are met
                if self.current_quest_id != quest_id_int:
                    print(f"QuestManager: Current quest updated to {quest_id_int} ({quest_def.get('begin_quest_text', '')[:50]}...)")
                
                self.current_quest_id = quest_id_int
                if hasattr(self.env, 'current_loaded_quest_id'):
                    self.env.current_loaded_quest_id = self.current_quest_id
                if self.nav and hasattr(self.nav, 'active_quest_id'):
                    self.nav.active_quest_id = self.current_quest_id
                return self.current_quest_id

        # If loop completes, no actionable quest found (e.g., all done)
        if self.current_quest_id is not None:
             print(f"QuestManager: No new actionable quest found. Last active was {self.current_quest_id}. All quests might be complete.")
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

    def filter_action(self, action: int) -> int:
        """
        Inspect or modify the given action based on explicit hard-coded quest logic
        for the current_quest_id determined by get_current_quest().
        """
        self.get_current_quest() # Ensure current_quest_id is up-to-date

        active_quest_id = self.current_quest_id # Use the ID set by get_current_quest
        
        # If no quest is active, or no specific logic, return original action
        if active_quest_id is None:
            return action

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


        # Hard-coded logic for quest 014: Path following (example, assuming env handles it)
        if active_quest_id == 14:
            # Assuming env.load_coordinate_path itself checks if path is already loaded for this quest
            if self.env.load_coordinate_path(14): # This method should return bool or raise error
                 # If path is successfully loaded (or confirmed loaded) for quest 14
                 # and we are on a coordinate of that path, then trigger path follow.
                 quest_def_14 = self.get_quest_definition(14)
                 if quest_def_14 and "associated_coordinates_file" in quest_def_14:
                     # This is just an example, actual path loading and checking if on path needs to be robust
                     # For now, if quest 14 is active, assume path following might be needed
                     # The navigator or env should handle the actual path following action.
                     # This manager might just signal that PATH_FOLLOW_ACTION is appropriate.
                     # A more robust way: navigator.is_on_path_for_quest(14)
                     if self.nav and self.nav.is_on_path_for_quest(14): # Hypothetical method
                         return PATH_FOLLOW_ACTION 

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


        if active_quest_id == 5 and gy == 338 and gx == 94:
            return self.up_action_index
        
        if active_quest_id == 2 or active_quest_id == 3:
            if active_quest_id == 3:
                if (gy, gx) == (340, 94) or (gy, gx) == (340, 95):
                    return self.up_action_index
            # These are very specific coordinate-based actions.
            # Consider if these should be defined in coordinate paths or as micro-objectives.
            if (gy, gx) == (355, 78) or (gy, gx) == (355, 77): return self.right_action_index
            elif (gy, gx) == (343, 89): return self.down_action_index
            elif (gy, gx) == (344, 89): return self.down_action_index
            elif (gy, gx) == (349, 82): return self.down_action_index
        
        if active_quest_id == 12: # Talk to Oak
            if (gy, gx) == (348, 110): return self.a_action_index
        
        # Simplified talk to Nurse Joy for any Pokecenter heal quest if standing in front
        # Example: if current quest involves healing and player is at (heal_spot_x, heal_spot_y)
        # current_quest_def = self.get_quest_definition(active_quest_id)
        # if current_quest_def and "Heal" in current_quest_def.get("begin_quest_text", ""):
        #    if map_id in POKECENTER_MAP_IDS and (gy, gx) == NURSE_JOY_COORD_MAP.get(map_id):
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
            self.get_current_quest() # Ensure current_quest_id is determined
        
        if self.current_quest_id is not None:
            return self.get_quest_definition(self.current_quest_id)
        return None

# End of QuestManager enforcement module 