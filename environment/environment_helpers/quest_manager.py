# Quest Manager: quest enforcement logic
"""
Quest Manager: micromanage individual quests to ensure the player completes necessary steps.
Currently only implements logic for quest 015 (getting the Town Map from Blue's sister).
"""
from pyboy.utils import WindowEvent
from environment import RedGymEnv, VALID_ACTIONS, PATH_FOLLOW_ACTION
from data.items import Items
from grok_plays_pokemon.recorder.data.recorder_data.global_map import local_to_global
import json
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict
from data.item_handler import ItemHandler

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
        self.start_action_index = VALID_ACTIONS.index(WindowEvent.PRESS_BUTTON_START)
        self.pending_b_presses = 0
        # Wrap environment.step to apply filter_action on every call
        self._original_step = env.step
        def _step_wrapper(action, *args, **kwargs):
            return self._original_step(self.filter_action(action), *args, **kwargs)
        env.step = _step_wrapper
        self.item_count = 0
        self.pressed_button_dict = {} # {quest_id: {coordinate: {action_index: count}}}
        self.item_handler = ItemHandler(self.env)
        self.last_num_potions = 0

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

        # Increment the count for the specific action at the specific coordinate for the specific quest
        self.pressed_button_dict[quest][coord][action_index] += value
        print(f'Incremented count for action_index {action_index} at coordinate {coord} for quest_id {quest}')
    
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
        if 14 not in self.loaded_paths:
            file14 = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / "014" / "014_coords.json"
            try:
                data14 = json.load(file14.open('r'))
                self.loaded_paths[14] = [(int(pair[0]), int(pair[1])) for seg in data14.values() for pair in seg]
            except Exception:
                self.loaded_paths[14] = []
        coords14 = self.loaded_paths[14]
        x, y, map_id = self.env.get_game_coords()
        gy, gx = local_to_global(y, x, map_id)

        # reset pressed_button_dict after quest is completed
        if current_quest_id-1 in self.pressed_button_dict:
            del self.pressed_button_dict[current_quest_id-1]

        if current_quest_id == 14 and (gy, gx) in coords14:
            self.env.load_coordinate_path(14)
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
               
        if current_quest_id == 5 and gy == 338 and gx == 94:
            return self.up_action_index
        
        if current_quest_id == 2 or current_quest_id == 3:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            # HARD-CODED hack for Quest 003: if on tile (344, 88) on map 0, force UP
            if current_quest_id == 3:
                if (gy, gx) == (340, 94) or (gy, gx) == (340, 95):
                    print("quest_manager.py: hack for quest 003 at tile (344, 88) or (344, 89) - move UP")
                    return self.up_action_index
            if (gy, gx) == (355, 78) or (gy, gx) == (355, 77):
                return self.right_action_index
            elif (gy, gx) == (343, 89):
                return self.down_action_index
            elif (gy, gx) == (344, 89):
                return self.down_action_index
            elif (gy, gx) == (349, 82):
                return self.down_action_index
        
        # Talk to Oak
        if current_quest_id == 12:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (348, 110):
                return self.a_action_index
        
        # Talk to Nurse Joy
        if current_quest_id == 18:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (297, 118):
                return self.a_action_index
        
        # Talk to the clerk at the Pokemart
        if current_quest_id == 21:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            if (gy, gx) == (299, 132):
                return self.a_action_index
        
        # Hang out until we catch a Nidoran
        if current_quest_id == 23:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            # make sure no dialog is active
            if self.env.read_dialog():
                return action
            if gy == 285 or gy == 282:
                return self.up_action_index if gy == 285 else self.down_action_index
        

        if current_quest_id == 26:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            # press a once at (297, 118) to talk to nurse joy
            if (gy, gx) == (297, 118): # and current_quest_id in self.pressed_button_dict:
                # initialize the pressed_button_dict for this quest if it doesn't exist
                self.update_pressed_button_dict((gy, gx), self.start_action_index, current_quest_id)
                if (gy, gx) in self.pressed_button_dict[current_quest_id]:
                    # walk away if a has been pressed on this coordinate while not in dialog more than 1 time
                    if not self.env.read_dialog() and self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] > 1:
                        self.update_pressed_button_dict((gy, gx), self.down_action_index, current_quest_id)
                        return self.down_action_index
                    # ensure we actually heal and progress past nurse joy's verbosity
                    elif self.env.read_dialog() and self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] > 1:
                        # don't increment the count if we're in dialog
                        return self.a_action_index
                    elif self.env.read_hp_fraction() != 1:
                        return self.a_action_index
                    else:
                        # return a_action_index if a has not been pressed on this coordinate
                        self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id)
                        # self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] += 1
                        return self.a_action_index                   
            # press a once at (297, 118) to grab viridian city cut tree potion
            if (gy, gx) == (270, 89)  and current_quest_id in self.pressed_button_dict:
                # initialize the pressed_button_dict for this quest if it doesn't exist
                self.update_pressed_button_dict((gy, gx), self.start_action_index, current_quest_id)
                if (gy, gx) in self.pressed_button_dict[current_quest_id]:
                    # return normal action if a has been pressed on this coordinate
                    if self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] > 0:
                        return action
                    else:
                        # return a_action_index if a has not been pressed on this coordinate
                        self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id)
                        # self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] += 1
                        return self.a_action_index
            # goes *after* leaving pokemon center
            print(f"quest_manager.py: filter_action(): gy={gy}, gx={gx}")
            if (gy, gx) == (292, 97):
                print(f"quest_manager.py: filter_action(): self.env.read_hp_fraction(): {self.env.read_hp_fraction()}")
                if self.env.read_hp_fraction() == 1:
                    # Hard-code: load only the '1_again' segment for Quest 026
                    coord_file = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / "026" / "026_coords.json"
                    try:
                        data026 = json.load(coord_file.open('r'))
                        seg = data026.get("1_again", [])
                        if seg:
                            coords = [(int(c[0]), int(c[1])) for c in seg]
                            if self.nav:
                                self.nav.sequential_coordinates = coords
                                self.nav.coord_map_ids = [1] * len(coords)
                                self.nav.current_coordinate_index = 0
                                self.nav.active_quest_id = 26
                            self.env.current_loaded_quest_id = 26
                            print("quest_manager.py: filter_action(): Hard-loaded '1_again' segment for Quest 026")
                    except Exception as e:
                        print(f"quest_manager.py: filter_action(): Failed to load '1_again' segment: {e}")
                    return self.left_action_index
    
        # avoid list index out of bounds while trainer 0 approaches player
        if current_quest_id == 29:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            beat_viridian_forest_trainer_0 = int(self.env.read_bit(0xD7F3, 2))
            print(f"quest_manager.py: filter_action(): beat_viridian_forest_trainer_0={beat_viridian_forest_trainer_0}")
            # stop pressing a when actual dialog appears
            if (gy, gx) == (227, 134) and beat_viridian_forest_trainer_0 == 0 and not self.env.read_dialog():
                return self.a_action_index
        
        # avoid list index out of bounds while trainer 1 approaches player
        if current_quest_id == 30:
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
        if current_quest_id == 31:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            beat_viridian_forest_trainer_2   = int(self.env.read_bit(0xD7F3, 4))
            print(f"quest_manager.py: filter_action(): beat_viridian_forest_trainer_2={beat_viridian_forest_trainer_2}")
            if (gy, gx) == (121, 134) and beat_viridian_forest_trainer_2 == 0 and not self.env.read_dialog():
                return self.a_action_index
            # # press a once at (220, 133) to grab viridian forest antidote
            # if (gy, gx) == (220, 133) and current_quest_id not in self.pressed_button_dict:
            #     self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id)
            #     return self.a_action_index
        
            # press a once at (220, 133) to grab viridian forest antidote
            if (gy, gx) == (220, 133): # and current_quest_id in self.pressed_button_dict:
                x, y, map_id = self.env.get_game_coords()
                gy, gx = local_to_global(y, x, map_id)   
                print(f'self.pressed_button_dict: {self.pressed_button_dict}')                
                try:
                    if (gy, gx) in self.pressed_button_dict[current_quest_id]:
                        # return normal action if a has been pressed on this coordinate
                        if self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] > 0:
                            return action
                except:
                    # return a_action_index if a has not been pressed on this coordinate
                    self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id)
                    # self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] += 1
                    return self.a_action_index
                
        if current_quest_id == 33:
            x, y, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(y, x, map_id)
            # Only press A once to talk to Nurse Joy and heal Pokemon at Pewter Poke Center
            if (gy, gx) == (193, 62):
                self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id, 0)
                # spam a through dialog to ensure healing isn't skipped
                if self.env.read_dialog():
                    return self.a_action_index
                
                # only talk to nurse joy once; tracked with pressed_button_dict
                count = self.pressed_button_dict.get(current_quest_id, {}).get((gy, gx), {}).get(self.a_action_index, 0)
                if count == 0:                    
                    self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id, 1)
                    return self.a_action_index
                
        # stuck in pewter poke center probably; down leaves the poke center
        if current_quest_id == 34 and not self.env.read_dialog():
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
        if current_quest_id == 35:
            items_in_bag, items_quantity_in_bag = self.item_handler.get_items_in_bag(), self.item_handler.get_items_quantity_in_bag()   
            itdict = dict(zip(items_in_bag, items_quantity_in_bag))
            num_potions = itdict[Items.POTION.value]
            if not self.env.read_dialog():
                self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id, 0)
                self.item_handler.scripted_buy_items()
                if (gy, gx) == (186, 58) and self.pressed_button_dict[current_quest_id][(gy, gx)][self.a_action_index] == 0:
                    self.update_pressed_button_dict((gy, gx), self.a_action_index, current_quest_id, 1)
                    return self.a_action_index
            else:
                if current_quest_id == 35 and self.env.read_dialog() and (gy, gx) == (186, 58):
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
        # Hard-coded logic for quest 037: follow the recorded coordinate path
        if current_quest_id == 37:
            if 37 not in self.loaded_paths:
                file37 = Path(__file__).parent / "replays" / "recordings" / "paths_001_through_046" / "037" / "037_coords.json"
                try:
                    data37 = json.load(file37.open('r'))
                    self.loaded_paths[37] = [(int(pair[0]), int(pair[1])) for seg in data37.values() for pair in seg]
                except Exception:
                    self.loaded_paths[37] = []
            coords37 = self.loaded_paths[37]
            if (gy, gx) in coords37:
                self.env.load_coordinate_path(37)
                return PATH_FOLLOW_ACTION
            if (gy, gx) == (178, 67) and self.env.read_m("wSpritePlayerStateData1FacingDirection") == 0x4:
                return self.down_action_index
            if (gy, gx) == (177, 67) and self.env.read_m("wSpritePlayerStateData1FacingDirection") == 0x4:
                return self.down_action_index

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