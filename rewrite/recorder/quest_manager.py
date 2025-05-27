# Quest Manager: quest-015 enforcement logic
"""
Quest Manager: micromanage individual quests to ensure the player completes necessary steps.
Currently only implements logic for quest 015 (getting the Town Map from Blue's sister).
"""
from pyboy.utils import WindowEvent
from environment import RedGymEnv, VALID_ACTIONS, PATH_FOLLOW_ACTION
from data.items import Items
from global_map import local_to_global
import json
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict

class QuestManager:
    """
    Orchestrates quest-specific behavior by intercepting and modifying player actions,
    including forcing item interactions and dynamic coordinate path loading.
    """

    def __init__(self, env: RedGymEnv):
        """
        Initialize the QuestManager with the game environment and navigator.
        :param env: The RedGymEnv instance controlling the emulator.
        """
        self.env = env
        # Load quest definitions for auto-detecting quest based on map ID
        try:
            rc_file = Path(__file__).parent / "required_completions.json"
            rc_data = json.load(rc_file.open('r'))
            # Build mapping from location to sorted list of quest IDs
            quests_by_location: dict[int, list[int]] = defaultdict(list)
            for q in rc_data:
                loc = int(q["location_id"])
                quests_by_location[loc].append(int(q["quest_id"]))
            # Sort quest IDs for each location
            for loc, qlist in quests_by_location.items():
                qlist.sort()
            self.quests_by_location = dict(quests_by_location)
        except Exception as e:
            print(f"quest_manager.py: __init__(): failed to load quests_by_location: {e}")
            self.quests_by_location = {}
        # Current quest id is dynamically determined from the environment
        self.current_quest_id = getattr(env, 'current_loaded_quest_id', None)
        # Determine the discrete action index for pressing 'A'
        self.a_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_A)
        # Navigator instance for automatic path following
        self.nav = getattr(env, 'navigator', None)
        # Cache flattened coordinate lists per quest
        self.loaded_paths: dict[int, list[tuple[int,int]]] = {}
        # Prepare special warp interaction for quest 015: press UP then B×3
        self.up_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_UP)
        self.down_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_DOWN)
        self.left_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_LEFT)
        self.right_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_ARROW_RIGHT)
        self.b_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_B)
        self.pending_b_presses = 0
        # Wrap environment.step to apply filter_action on every call
        self._original_step = env.step
        def _step_wrapper(action, *args, **kwargs):
            return self._original_step(self.filter_action(action), *args, **kwargs)
        env.step = _step_wrapper

    def filter_action(self, action: int) -> int:
        """
        Inspect or modify the given action based on explicit hard-coded quest logic.
        """
        # Initialize quest based on map if none is set yet
        _, _, map_id = self.env.get_game_coords()
        if self.current_quest_id is None:
            quest_list = self.quests_by_location.get(map_id, [])
            if quest_list:
                initial_qid = quest_list[0]
                self.current_quest_id = initial_qid
                self.env.current_loaded_quest_id = initial_qid
                if self.nav:
                    self.nav.active_quest_id = initial_qid
                print(f"quest_manager.py: filter_action(): map_id={map_id}, initial_qid={initial_qid}")
        # Determine current quest: prefer navigator active quest if set, else environment loaded quest
        nav_qid = getattr(self.nav, 'active_quest_id', None) if self.nav is not None else None
        raw_qid = getattr(self.env, 'current_loaded_quest_id', None)
        current_quest_id = nav_qid if nav_qid is not None else raw_qid
        # Sync to environment and UI display
        self.env.current_loaded_quest_id = current_quest_id
        self.current_quest_id = current_quest_id
        # Hard-coded logic for quest 014: use environment path-follow
        coords14 = self.loaded_paths.get(14, [])
        # Check current global position
        x, y, map_id = self.env.get_game_coords()
        gy, gx = local_to_global(y, x, map_id)
        # Only auto-follow quest 014 path when actively on quest 014
        if current_quest_id == 14 and (gy, gx) in coords14:
            # Load into environment if not already
            if getattr(self.env, 'current_loaded_quest_id', None) != 14:
                self.env.load_coordinate_path(14)
            # Use path-follow action for correct navigation
            return PATH_FOLLOW_ACTION

        # Hard-coded logic for quest 015 (Town Map and warp)
        if current_quest_id == 15:
            # Handle warp into Blue's House
            if self.pending_b_presses > 0:
                self.pending_b_presses -= 1
                return self.b_action_index
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (344, 97):
                self.pending_b_presses = 3
                return self.up_action_index
            # Enforce A press to get Town Map
            return self._apply_quest_015_rules(action)

        # Hard-coded logic for quest 016
        if current_quest_id == 16 and self.nav:
            if 16 not in self.loaded_paths:
                file16 = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / "016" / "016_coords.json"
                try:
                    data16 = json.load(file16.open('r'))
                    self.loaded_paths[16] = [(int(pair[0]), int(pair[1])) for seg in data16.values() for pair in seg]
                except Exception:
                    self.loaded_paths[16] = []
            coords16 = self.loaded_paths[16]
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) in coords16 and getattr(self.nav, 'active_quest_id', None) != 16:
                self.nav.load_coordinate_path(16)
                self.nav.current_coordinate_index = coords16.index((gy, gx))
            if getattr(self.nav, 'active_quest_id', None) == 16:
                return PATH_FOLLOW_ACTION

        # HARD-CODED hack for Quest 003: if on tile (344, 88) on map 0, force UP
        if current_quest_id == 3:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (340, 94) or (gy, gx) == (340, 95):
                print("quest_manager.py: hack for quest 003 at tile (344, 88) or (344, 89) - move UP")
                return self.up_action_index

        if current_quest_id == 2 or current_quest_id == 3:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (349, 82):
                return self.down_action_index
        
        if current_quest_id == 5 and gy == 338 and gx == 94:
            return self.up_action_index
        
        if current_quest_id == 2 or current_quest_id == 3:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (355, 78) or (gy, gx) == (355, 77):
                return self.right_action_index
            if (gy, gx) == (343, 89):
                return self.down_action_index
            if (gy, gx) == (344, 89):
                return self.down_action_index
        
        if current_quest_id == 2 or current_quest_id == 3:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (344, 89):
                return self.down_action_index
        
        
        # No special rules: return original action
        return action

    def _apply_quest_015_rules(self, action: int) -> int:
        """
        Quest 015: Ensure the player receives the Town Map from Blue's sister.

        Logic:
        1. If the player already has the Town Map, allow all actions.
        2. Otherwise, if standing on the interaction tile (global coords 340, 107),
           force the 'A' action to interact and block movement off that tile.
        3. Otherwise, allow the original action.
        """
        obs = self.env._get_obs()
        bag_items = obs.get("bag_items", [])
        town_map_id = Items.TOWN_MAP.value
        # If Town Map in bag, no special enforcement
        if int(town_map_id) in list(bag_items):
            return action

        # Player still needs the Town Map
        x, y, map_id = self.env.get_game_coords()
        gy, gx = local_to_global(y, x, map_id)
        # If at Blue's sister tile, force 'A'
        if (gy, gx) == (340, 107):
            return self.a_action_index
        return action

    def get_first_step(self, quest: Dict) -> Optional[Dict]:
        """
        Return the first step of a quest, or None if no steps defined.
        """
        # Get all steps: prefer 'steps' key, fall back to 'subquest_list'
        all_steps = self.get_all_steps(quest)
        return all_steps[0] if all_steps else None

    def get_all_steps(self, quest: Dict) -> List[Dict]:
        """
        Return the full list of steps for a quest, converting 'subquest_list' into step dicts if necessary.
        """
        # Prefer an explicit 'steps' list if provided
        explicit = quest.get('steps')
        if explicit and isinstance(explicit, list):
            return explicit
        # Fallback: convert 'subquest_list' strings into step dictionaries
        subquests = quest.get('subquest_list', [])
        return [{'description': text} for text in subquests]

# End of QuestManager enforcement module 