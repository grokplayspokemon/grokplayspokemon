# trigger_evaluator.py
import re
from typing import Dict, Optional, Tuple, List
import time
from collections import deque

from environment.data.environment_data.events import EventFlags
from environment.data.environment_data.flags import Flags
from environment.data.environment_data.items import Items
from environment.data.environment_data.species import Species
from environment.data.recorder_data.global_map import local_to_global
from environment.environment import RedGymEnv

class TriggerEvaluator:
    """
    Evaluate completion triggers from required_completions.json using the RedGymEnv environment.
    """
    def __init__(self, env: 'RedGymEnv'):
        self.env = env
        # FIXED: Per-trigger blocking instead of global blocking
        # Structure: {trigger_signature: {'timestamp': time, 'count': int}}
        self._trigger_cooldowns = {}
        self._trigger_cooldown_duration = 2.0  # 2 seconds cooldown per trigger
        self._max_trigger_count = 2  # Allow max 2 triggers per cooldown period
        
        # REMOVED: No longer maintain separate map tracking in trigger evaluator
        # The environment has its own environment_map_history that we'll use
        
        self.active_triggers = {}
        
        # Track visited coordinates for coordinates_match triggers
        self.visited_coordinates = set()  # Set of (x, y, map_id) tuples

    def _get_trigger_signature(self, trigger: Dict) -> str:
        """Generate a unique signature for a trigger to track its cooldown"""
        ttype = trigger.get('type')
        if ttype == 'current_map_is_previous_map_was':
            current_map_id = trigger.get('current_map_id')
            previous_map_id = trigger.get('previous_map_id')
            return f"map_transition_{previous_map_id}_to_{current_map_id}"
        else:
            # For other trigger types, use type + key parameters
            key_params = []
            for key in ['map_id', 'text', 'size', 'event_name', 'item_name']:
                if key in trigger:
                    key_params.append(f"{key}_{trigger[key]}")
            return f"{ttype}_{'_'.join(key_params)}"

    def _is_trigger_on_cooldown(self, trigger: Dict) -> bool:
        """Check if a trigger is on cooldown to prevent spam"""
        signature = self._get_trigger_signature(trigger)
        current_time = time.time()
        
        # TEMPORARY FIX: Bypass cooldown for Quest 16 transition that's causing a stuck loop
        # Quest 16 trigger: (prev_map == 0) and (curr_map == 12)
        if (trigger.get('type') == 'current_map_is_previous_map_was' and 
            trigger.get('previous_map_id') == 0 and 
            trigger.get('current_map_id') == 12):
            print(f"[TriggerEvaluator] BYPASSING COOLDOWN for Quest 16 trigger: {signature}")
            print(f"[TriggerEvaluator] Quest 16 Debug - Current game state:")
            if self.env:
                x, y, map_id = self.env.get_game_coords()
                print(f"[TriggerEvaluator] Quest 16 Debug - Player coords: ({x}, {y}), map: {map_id}")
                
                # Check if Oak's Parcel is in inventory
                try:
                    items_in_bag = list(self.env.get_items_in_bag())
                    parcel_present = any(item.name == 'OAKS_PARCEL' for item in items_in_bag)
                    print(f"[TriggerEvaluator] Quest 16 Debug - Oak's Parcel in bag: {parcel_present}")
                    print(f"[TriggerEvaluator] Quest 16 Debug - Items in bag: {[item.name for item in items_in_bag]}")
                except Exception as e:
                    print(f"[TriggerEvaluator] Quest 16 Debug - Error checking items: {e}")
            
            return False
        
        if signature in self._trigger_cooldowns:
            cooldown_data = self._trigger_cooldowns[signature]
            time_since_last = current_time - cooldown_data['timestamp']
            
            if time_since_last < self._trigger_cooldown_duration:
                # Still in cooldown period
                if cooldown_data['count'] >= self._max_trigger_count:
                    return True  # Too many triggers in cooldown period
                else:
                    # Increment counter but allow trigger
                    cooldown_data['count'] += 1
                    return False
            else:
                # Cooldown expired, reset
                self._trigger_cooldowns[signature] = {
                    'timestamp': current_time,
                    'count': 1
                }
                return False
        else:
            # First time seeing this trigger
            self._trigger_cooldowns[signature] = {
                'timestamp': current_time,
                'count': 1
            }
            return False

    def _get_trigger_logic_code(self, trigger: Dict) -> str:
        """Get the actual code/logic string for a trigger"""
        ttype = trigger.get('type')
        # Support legacy 'current_map_id' trigger as alias for 'current_map_id_is'
        if ttype == 'current_map_id':
            target_map = trigger.get('current_map_id')
            return f"current_map_id == {target_map}"
        if ttype == 'current_map_id_is':
            target_map = trigger['map_id']
            return f"current_map_id == {target_map}"
        elif ttype == 'previous_map_id_was':
            target_map = trigger['map_id']
            return f"prev_map_id == {target_map}"
        elif ttype == 'dialog_contains_text':
            text = trigger['text']
            return f"'{text}' in normalized_dialog"
        elif ttype == 'party_size_is':
            size = trigger['size']
            return f"party_size == {size}"
        elif ttype == 'event_completed':
            event_name = trigger.get('event_name', '')
            return f"env.events.get_event('{event_name}') == True"
        elif ttype == 'battle_won':
            return f"legacy_battle_won_check()"
        elif ttype == 'item_received_dialog':
            text = trigger['text']
            return f"'{text}' in item_dialog"
        elif ttype == 'item_is_in_inventory':
            item_name = trigger.get('item_name', '')
            quantity_min = trigger.get('quantity_min', 1)
            return f"inventory_count('{item_name}') >= {quantity_min}"
        elif ttype == 'party_hp_is_full':
            return "all(pokemon.hp == pokemon.max_hp for pokemon in party)"
        elif ttype == 'current_map_is_previous_map_was':
            current_map_id = trigger.get('current_map_id')
            previous_map_id = trigger.get('previous_map_id')
            return f"(prev_map == {previous_map_id}) and (curr_map == {current_map_id})"
        elif ttype == 'party_pokemon_species_is':
            species_name = trigger.get('species_name', '')
            return f"any(pokemon.species == '{species_name}' for pokemon in party)"
        elif ttype == 'battle_type_is':
            battle_type = trigger.get('battle_type_name', '')
            return f"battle_type == '{battle_type}'"
        elif ttype == 'quest_completed':
            quest_id = trigger.get('quest_id', '')
            return f"quest_{quest_id}_completed == True"
        elif ttype == 'badge_is_obtained':
            badge_name = trigger.get('badge_name', '')
            return f"badge_{badge_name}_obtained == True"
        elif ttype == 'coordinates_are':
            # global and local coordinates are allowed, but which need to be specified
            x_min = trigger.get('x_min', None)
            y_min = trigger.get('y_min', None)
            x_max = trigger.get('x_max', None)
            y_max = trigger.get('y_max', None)
            coord_space = trigger.get('coord_space', trigger.get('coordinate_space', 'local'))  # alias coordinate_space

            if coord_space not in ('local', 'global'):
                raise ValueError("coordinates_are trigger requires coord_space to be 'local' or 'global'")

            px = 'player_x' if coord_space == 'local' else 'player_global_x'
            py = 'player_y' if coord_space == 'local' else 'player_global_y'

            return (
                f"{px} >= {x_min} and {py} >= {y_min} and "
                f"({px} <= {x_max} or {x_max} is None) and "
                f"({py} <= {y_max} or {y_max} is None)"
            )
        elif ttype == 'coordinates_match':
            coords = trigger.get('coordinates', [])
            if len(coords) == 3:
                x, y, map_id = coords
                return f"player_at_coordinates({x}, {y}, {map_id}) or visited_coordinates({x}, {y}, {map_id})"
            else:
                return f"invalid_coordinates_match_format({coords})"
        else:
            return f"unknown_trigger_type('{ttype}')"

    def _get_map_history(self) -> Tuple[Optional[int], int]:
        """
        Get previous and current map IDs from the environment's centralized map tracking system.
        Returns (previous_map_id, current_map_id)
        """
        current_map_id = self.env.get_game_coords()[2]
        
        # Use environment's centralized map_history (deque with maxlen=3)
        if hasattr(self.env, 'map_history') and len(self.env.map_history) >= 2:
            previous_map_id = self.env.map_history[-2]
        else:
            # Fallback: use current map as previous (no map change)
            previous_map_id = current_map_id
        
        return previous_map_id, current_map_id

    def check_trigger(self, trigger: Dict, current_map_id: Optional[int] = None) -> Dict[str, any]:
        # Default return values
        result = False
        values_str = "N/A"
        debug_str = "Trigger type not processed or error."
        logic_code = self._get_trigger_logic_code(trigger)

        ttype = trigger.get('type')
        # Handle legacy 'current_map_id' trigger
        if ttype == 'current_map_id':
            curr_map = self.env.get_game_coords()[2]
            result = (curr_map == trigger.get('current_map_id'))
            values_str = f"CurrentMap: {curr_map}"
            debug_str = f"Evaluating: {logic_code} → {result}"
            return {"result": result, "values_str": values_str, "debug_str": debug_str, "logic_code": logic_code}
        if ttype == 'current_map_id_is':
            # Always use live environment state for current map
            curr_map = self.env.get_game_coords()[2]
            target_map_id = trigger.get('map_id')
            result = (curr_map == target_map_id)
            values_str = f"CurrentMap: {curr_map}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'previous_map_id_was':
            # Use global map history for consistency
            prev_map, curr_map = self._get_map_history()
            target_map_id = trigger.get('map_id')
            result = (prev_map == target_map_id)
            values_str = f"PreviousMap: {prev_map}, CurrentMap: {curr_map}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'current_map_is_previous_map_was':
            # Use global map history for consistency
            prev_map, curr_map = self._get_map_history()
            target_curr_map_id = trigger.get('current_map_id')
            target_prev_map_id = trigger.get('previous_map_id')
            
            # Enhanced debug logging for map transition triggers
            full_history = list(self.env.map_history) if hasattr(self.env, 'map_history') else "N/A"
            # print(f"[TriggerEvaluator] Map transition trigger: prev={prev_map}, curr={curr_map}, target_prev={target_prev_map_id}, target_curr={target_curr_map_id}, history={full_history}")
            
            condition_met = ((curr_map == target_curr_map_id) and (prev_map == target_prev_map_id))
            
            if condition_met:
                # Check cooldown to prevent spam
                if self._is_trigger_on_cooldown(trigger):
                    result = False
                    values_str = f"PrevMap: {prev_map}, CurrMap: {curr_map} (Cooldown)"
                    debug_str = f"Evaluating: {logic_code} → {result} (on cooldown)"
                    print(f"[TriggerEvaluator] Trigger on cooldown: {logic_code}")
                else:
                    result = True
                    values_str = f"PrevMap: {prev_map}, CurrMap: {curr_map}"
                    debug_str = f"Evaluating: {logic_code} → {result} (triggered)"
                    print(f"[TriggerEvaluator] TRIGGER FIRED: {logic_code}")
            else:
                result = False
                values_str = f"PrevMap: {prev_map}, CurrMap: {curr_map}"
                debug_str = f"Evaluating: {logic_code} → {result}"
                # print(f"[TriggerEvaluator] Condition not met: {logic_code}, prev_match={prev_map == target_prev_map_id}, curr_match={curr_map == target_curr_map_id}")
        elif ttype == 'party_size_is':
            current_party_size = self.env.read_m('wPartyCount')
            target_size = trigger.get('size')
            result = (current_party_size == target_size)
            values_str = f"PartySize: {current_party_size}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'event_completed':
            # Generic event completion trigger - requires actual game event names from RAM
            event_name = trigger.get('event_name')
            opponent_identifier = trigger.get('opponent_identifier', '')  # Only for informational purposes
            
            if not event_name:
                print(f"[TriggerEvaluator] ERROR: event_completed trigger missing required 'event_name' field")
                print(f"[TriggerEvaluator] Note: 'opponent_identifier' is for display only - 'event_name' must be an actual game RAM event")
                result = False
                values_str = "Missing required event_name"
                debug_str = f"Error: {logic_code} - missing event_name"
                return {"result": result, "values_str": values_str, "debug_str": debug_str, "logic_code": logic_code}
            
            try:
                event_status = bool(self.env.events.get_event(event_name))
                result = event_status
                battle_info = f" [Battle vs {opponent_identifier}]" if opponent_identifier else ""
                values_str = f"{event_name} Status: {event_status}{battle_info}"
                debug_str = f"Evaluating: {logic_code} → {result}"
            except Exception as e:
                print(f"[TriggerEvaluator] Warning: unknown event '{event_name}', treating as False ({e})")
                result = False
                values_str = f"{event_name} Status: Error/Unknown"
                debug_str = f"Error: {logic_code} → {e}"
        elif ttype == 'battle_won':
            # Legacy trigger type - redirect to event_completed for backward compatibility
            print(f"[TriggerEvaluator] Warning: 'battle_won' trigger type is deprecated, use 'event_completed' instead")
            # Create a new trigger dict with the updated type and delegate to event_completed handler
            legacy_trigger = dict(trigger)
            legacy_trigger['type'] = 'event_completed'
            return self.check_trigger(legacy_trigger, current_map_id)
        elif ttype == 'dialog_contains_text':
            # FIXED: Use buffered dialog for better timing/persistence
            raw_dialog = ''
            if hasattr(self.env, 'get_recent_dialog_for_triggers'):
                raw_dialog = self.env.get_recent_dialog_for_triggers() or ''
            else:
                # Fallback to direct read
                raw_dialog = self.env.read_dialog() or ''
            
            norm_dialog = re.sub(r'\s+', ' ', raw_dialog.replace('\n', ' ')).strip()
            target_text_raw = trigger.get('text', '')
            target_text_norm = re.sub(r'\s+', ' ', target_text_raw.replace('\n', ' ')).strip()
            
            # SPECIAL CASE: Empty target string means we want **no dialog** present
            if target_text_norm == '':
                result = (norm_dialog == '')
            else:
                # Enhanced buffer search - check both current dialog and buffer
                result = (target_text_norm in norm_dialog)
                if not result and hasattr(self.env, 'check_dialog_buffer_for_text'):
                    result = self.env.check_dialog_buffer_for_text(target_text_raw)
            
            values_str = f"Dialog: '{norm_dialog[:50]}...'" if norm_dialog else 'Dialog: <none>'
            debug_str = f"Evaluating: {logic_code} → {result}"
            
            # Enhanced debugging for quest 12 dialog issue
            if target_text_norm == 'along' and not result:
                print(f"[QUEST12_DEBUG] Dialog trigger failed for 'along':")
                print(f"[QUEST12_DEBUG]   Raw dialog: '{raw_dialog}'")
                print(f"[QUEST12_DEBUG]   Normalized dialog: '{norm_dialog}'") 
                print(f"[QUEST12_DEBUG]   Target text: '{target_text_norm}'")
                print(f"[QUEST12_DEBUG]   Contains check: {'along' in norm_dialog}")
                if hasattr(self.env, 'dialog_buffer'):
                    print(f"[QUEST12_DEBUG]   Dialog buffer: {self.env.dialog_buffer}")
            elif target_text_norm == 'along' and result:
                print(f"[QUEST12_DEBUG] Dialog trigger SUCCESS for 'along': '{norm_dialog}'")
            
            # Enhanced debugging for any dialog_contains_text trigger 
            if not result and norm_dialog and target_text_norm:
                print(f"[DIALOG_DEBUG] Failed trigger - target: '{target_text_norm}' not in dialog: '{norm_dialog}'")
            elif result and target_text_norm:
                print(f"[DIALOG_DEBUG] Success trigger - target: '{target_text_norm}' found in dialog: '{norm_dialog}'")
        elif ttype == 'item_received_dialog':
            # FIXED: Use buffered dialog for better timing/persistence
            raw_dialog = ''
            if hasattr(self.env, 'get_recent_dialog_for_triggers'):
                raw_dialog = self.env.get_recent_dialog_for_triggers() or ''
            else:
                # Fallback to direct read
                raw_dialog = self.env.read_dialog() or ''
            
            norm_dialog = re.sub(r'\s+', ' ', raw_dialog.replace('\n', ' ')).strip()
            target_text_raw = trigger.get('text', '')
            target_text_norm = re.sub(r'\s+', ' ', target_text_raw.replace('\n', ' ')).strip()
            result = (target_text_norm in norm_dialog)
            values_str = f"Dialog: '{norm_dialog[:50]}...'"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'item_is_in_inventory':
            target_item_name_raw = trigger.get('item_name', '').upper()
            target_item_name_clean = target_item_name_raw.replace('É', 'E').replace(' ', '_')
            target_item_name_clean = re.sub(r'[^A-Z0-9_]', '', target_item_name_clean)
            target_qty_min = trigger.get('quantity_min', 1)
            
            num_slots = self.env.read_m('wNumBagItems')
            _, addr = self.env.pyboy.symbol_lookup('wBagItems')
            raw_bytes = self.env.pyboy.memory[addr : addr + 2 * num_slots]
            current_item_count = 0
            found_item_details = "None"
            for i in range(0, len(raw_bytes), 2):
                item_id = raw_bytes[i]
                quantity = raw_bytes[i+1]
                try:
                    item_enum = Items(item_id)
                    if item_enum.name == target_item_name_clean:
                        current_item_count += quantity
                        found_item_details = f"{item_enum.name}: {current_item_count}"
                except ValueError:
                    continue
            result = (current_item_count >= target_qty_min)
            values_str = f"Item '{target_item_name_clean}': Count {current_item_count}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'party_pokemon_species_is':
            target_species_name = trigger.get('species_name', '')
            party = self.env.party.party[:self.env.party.party_size]
            current_party_species_names = []
            species_found = False
            for p in party:
                try:
                    species_name_from_party = Species(p.Species).name
                    current_party_species_names.append(species_name_from_party)
                    if species_name_from_party == target_species_name:
                        species_found = True
                except ValueError:
                    current_party_species_names.append("Invalid/Empty")
            result = species_found
            values_str = f"Party: {', '.join(current_party_species_names)}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'battle_type_is':
            target_battle_type_name = trigger.get('battle_type_name', '').upper()
            current_battle_type_raw = self.env.read_m(0xD057) # 1=wild, 2=trainer
            current_battle_type_str = "None"
            if current_battle_type_raw == 1: current_battle_type_str = "WILD"
            elif current_battle_type_raw == 2: current_battle_type_str = "TRAINER"
            
            if target_battle_type_name == 'TRAINER':
                result = (current_battle_type_raw == 2)
            elif target_battle_type_name == 'WILD':
                result = (current_battle_type_raw == 1)
            else:
                result = False
            
            values_str = f"BattleType: {current_battle_type_str}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'quest_completed':
            target_quest_id = trigger.get('quest_id', '')
            target_quest_id_str = str(target_quest_id).zfill(3)
            
            # Check if quest is completed via quest manager
            quest_completed = False
            if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
                quest_completed = self.env.quest_manager.quest_completed_status.get(target_quest_id_str, False)
            
            result = quest_completed
            values_str = f"Quest {target_quest_id_str}: {'Complete' if quest_completed else 'Incomplete'}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'badge_is_obtained':
            target_badge_name = trigger.get('badge_name', '').upper()
            
            # Check badge status via environment's badge system
            badge_obtained = False
            try:
                if hasattr(self.env, 'badges') and self.env.badges:
                    badge_obtained = self.env.badges.get_badge(target_badge_name)
                elif hasattr(self.env, 'read_m'):
                    # Fallback: read from memory directly
                    # Badge byte format in Red/Blue: bit flags
                    badges_byte = self.env.read_m('wObtainedBadges')
                    badge_flags = {
                        'BOULDER': 0x01,  # Brock
                        'CASCADE': 0x02,  # Misty  
                        'THUNDER': 0x04,  # Lt. Surge
                        'RAINBOW': 0x08,  # Erika
                        'SOUL': 0x10,     # Koga
                        'MARSH': 0x20,    # Sabrina
                        'VOLCANO': 0x40,  # Blaine
                        'EARTH': 0x80     # Giovanni
                    }
                    if target_badge_name in badge_flags:
                        badge_obtained = bool(badges_byte & badge_flags[target_badge_name])
            except Exception as e:
                print(f"[TriggerEvaluator] Error checking badge {target_badge_name}: {e}")
                badge_obtained = False
            
            result = badge_obtained
            values_str = f"Badge {target_badge_name}: {'Obtained' if badge_obtained else 'Not Obtained'}"
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'coordinates_are':
            x_min = trigger.get('x_min', None)
            y_min = trigger.get('y_min', None)
            x_max = trigger.get('x_max', None)
            y_max = trigger.get('y_max', None)
            
            coord_space = trigger.get('coord_space', trigger.get('coordinate_space', 'local'))  # alias coordinate_space

            # Get current player coordinates (local)
            player_x_local, player_y_local, map_id = self.env.get_game_coords()

            # Convert to global if requested
            if coord_space == 'global':
                player_y_global, player_x_global = local_to_global(player_y_local, player_x_local, map_id)
            else:
                print(f"[TriggerEvaluator] Coordinates are in local space")
                player_y_global, player_x_global = player_y_local, player_x_local

            # Evaluate bounds (None means unbounded in that direction)
            result = True
            if x_min is not None:
                result = result and (player_x_global >= x_min)
            if y_min is not None:
                result = result and (player_y_global >= y_min)
            if x_max is not None:
                result = result and (player_x_global <= x_max)
            if y_max is not None:
                result = result and (player_y_global <= y_max)

            coord_type_label = "Global" if coord_space == 'global' else 'Local'
            values_str = (
                f"{coord_type_label}PlayerPos: ({player_x_global}, {player_y_global}), "
                f"Bounds: x[{x_min},{x_max}], y[{y_min},{y_max}]"
            )
            debug_str = f"Evaluating: {logic_code} → {result}"
        elif ttype == 'coordinates_match':
            target_coords = trigger.get('coordinates', [])
            if len(target_coords) == 3:
                target_x, target_y, target_map_id = target_coords
                target_coord_tuple = (target_x, target_y, target_map_id)
                
                # Get current player coordinates
                player_x, player_y, player_map_id = self.env.get_game_coords()
                current_coord_tuple = (player_x, player_y, player_map_id)
                
                # Check if target coordinates have ever been visited (permanent)
                if target_coord_tuple in self.visited_coordinates:
                    result = True
                    status = "Already Visited"
                else:
                    # Check if player is currently at target coordinates
                    currently_at_target = (current_coord_tuple == target_coord_tuple)
                    if currently_at_target:
                        # Mark as visited permanently
                        self.visited_coordinates.add(target_coord_tuple)
                        result = True
                        status = "Just Visited"
                    else:
                        result = False
                        status = "Not Visited"
                
                values_str = f"PlayerPos: {current_coord_tuple}, Target: {target_coord_tuple}, Status: {status}"
                debug_str = f"Evaluating: {logic_code} → {result}"
            else:
                result = False
                values_str = f"Invalid coordinates format: {target_coords}"
                debug_str = f"Evaluating: {logic_code} → {result} (invalid format)"
        else:
            result = False # Keep default
            values_str = f"Unsupported Type: {ttype}" # Keep default
            debug_str = f"Trigger type '{ttype}' is not implemented or recognized."
            # raise NotImplementedError(f"Unsupported trigger type: {ttype}") # Soft fail instead of crash

        return {"result": result, "values_str": values_str, "debug_str": debug_str, "logic_code": logic_code}

    # REMOVED: check_all function - dead code that was never called
    # REMOVED: The function was creating a separate evaluator instance which bypassed proper initialization

# REMOVED: evaluate_triggers_for_step function - dead code that was never called
# The actual trigger evaluation happens through QuestProgressionEngine.step() which calls evaluator.check_trigger() directly 