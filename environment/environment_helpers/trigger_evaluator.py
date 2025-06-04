import re
from typing import Dict, Optional, Tuple, List

from environment.data.environment_data.events import EventFlags
from environment.data.environment_data.flags import Flags
from environment.data.environment_data.items import Items
from environment.data.environment_data.species import Species
from environment.data.recorder_data.global_map import local_to_global
from environment.environment import RedGymEnv
from collections import deque

class TriggerEvaluator:
    """
    Evaluate completion triggers from required_completions.json using the RedGymEnv environment.
    Usage:
        evaluator = TriggerEvaluator(env)
        evaluator.prev_map_id = None
        for each step:
            # before calling env.step
            current_map = env.get_game_coords()[2]
            # evaluate triggers that use previous_map_id
            result = evaluator.check_trigger(trigger, current_map)
            evaluator.prev_map_id = current_map
    """
    def __init__(self, env: 'RedGymEnv'):
        self.env = env
        self.map_history = deque(maxlen=3)
        self.map_history.append(-1)
        # Track blocked reverse warps until leaving the warp target map
        self._blocked_warps = set()
        self._warp_target = None
        # Initialize prev_map_id to the current map ID when the evaluator is created.
        # This ensures the attribute exists from the moment of instantiation.
        self.prev_map_id = self.env.get_game_coords()[2]
        self.active_triggers = {}

    def check_trigger(self, trigger: Dict, current_map_id: Optional[int] = None) -> Dict[str, any]:
        # Default return values
        result = False
        values_str = "N/A"
        debug_str = "Trigger type not processed or error."

        # only append to map_history if the map id is different from the previous one
        if self.map_history[-1] != self.env.get_game_coords()[2]:
            self.map_history.append(self.env.get_game_coords()[2])
        # Clear blocked reverse-warps only when leaving the warp target map
        curr_map = self.map_history[-1] if len(self.map_history) >= 1 else None
        if self._warp_target is not None and curr_map != self._warp_target:
            self._blocked_warps.clear()
            self._warp_target = None

        ttype = trigger.get('type')
        # Evaluate each trigger type and store result
        if ttype == 'current_map_id_is':
            # Use map_history for current map (last element)
            curr_map = self.map_history[-1] if len(self.map_history) >= 1 else self.env.get_game_coords()[2]
            target_map_id = trigger.get('map_id')
            result = (curr_map == target_map_id)
            values_str = f"CurrentMap: {curr_map}"
            debug_str = f"Is CurrentMap ({curr_map}) == TargetMap ({target_map_id})?"
            print(f"[TriggerDebug] current_map_id_is check: map_history[-1]={curr_map}, target={target_map_id}, result={result}")
        elif ttype == 'previous_map_id_was':
            # Use map_history[-2] for previous map
            prev_map = self.map_history[-2] if len(self.map_history) >= 2 else None
            curr_map = self.map_history[-1] if len(self.map_history) >= 1 else self.env.get_game_coords()[2]
            target_map_id = trigger.get('map_id')
            result = (prev_map == target_map_id)
            values_str = f"PreviousMap: {prev_map}, CurrentMap: {curr_map}"
            debug_str = f"Is PreviousMap ({prev_map}) == TargetMap ({target_map_id})?"
            print(f"[TriggerDebug] previous_map_id_was check: map_history[-2]={prev_map}, current={curr_map}, target={target_map_id}, result={result}")
        elif ttype == 'current_map_is_previous_map_was':
            # One-way warp: block reverse direction until leaving the warp target map
            prev_map = self.map_history[-2] if len(self.map_history) >= 2 else None
            curr_map = self.map_history[-1] if len(self.map_history) >= 1 else None
            target_curr_map_id = trigger.get('current_map_id')
            target_prev_map_id = trigger.get('previous_map_id')
            condition_met = (curr_map == target_curr_map_id and prev_map == target_prev_map_id)
            block_key = (curr_map, prev_map) # Define block_key regardless of condition_met for clarity

            if condition_met:
                if block_key in self._blocked_warps:
                    result = False
                    values_str = f"PrevMap: {prev_map}, CurrMap: {curr_map} (Blocked)"
                    debug_str = f"Warp {target_prev_map_id}->{target_curr_map_id} already occurred & blocked reverse."
                else:
                    result = True
                    self._blocked_warps.add(block_key)
                    self._warp_target = curr_map
                    values_str = f"PrevMap: {prev_map}, CurrMap: {curr_map}"
                    debug_str = f"Warp {target_prev_map_id}->{target_curr_map_id} detected."
            else:
                result = False
                values_str = f"PrevMap: {prev_map}, CurrMap: {curr_map}"
                debug_str = f"Is PrevMap ({prev_map}) == TargetPrev ({target_prev_map_id}) AND CurrMap ({curr_map}) == TargetCurr ({target_curr_map_id})?"
            print(f"[TriggerDebug] warp one-way check: prev={prev_map}, curr={curr_map}, target_prev={target_prev_map_id}, target_curr={target_curr_map_id}, result={result}")
        elif ttype == 'party_size_is':
            current_party_size = self.env.read_m('wPartyCount')
            target_size = trigger.get('size')
            result = (current_party_size == target_size)
            values_str = f"PartySize: {current_party_size}"
            debug_str = f"Is PartySize ({current_party_size}) == TargetSize ({target_size})?"
        elif ttype == 'event_completed':
            # Generic event completion trigger - requires actual game event names from RAM
            event_name = trigger.get('event_name')
            opponent_identifier = trigger.get('opponent_identifier', '')  # Only for informational purposes
            
            if not event_name:
                print(f"[TriggerEvaluator] ERROR: event_completed trigger missing required 'event_name' field")
                print(f"[TriggerEvaluator] Note: 'opponent_identifier' is for display only - 'event_name' must be an actual game RAM event")
                result = False
                values_str = "Missing required event_name"
                debug_str = "event_completed trigger requires 'event_name' field with actual game event from RAM"
                return {"result": result, "values_str": values_str, "debug_str": debug_str}
            
            try:
                event_status = bool(self.env.events.get_event(event_name))
                result = event_status
                battle_info = f" [Battle vs {opponent_identifier}]" if opponent_identifier else ""
                values_str = f"{event_name} Status: {event_status}{battle_info}"
                debug_str = f"Is Event ({event_name}) completed (True)?{battle_info}"
            except Exception as e:
                print(f"[TriggerEvaluator] Warning: unknown event '{event_name}', treating as False ({e})")
                result = False
                values_str = f"{event_name} Status: Error/Unknown"
                debug_str = f"Event ({event_name}) check error: {e}"
        elif ttype == 'battle_won':
            # Legacy trigger type - redirect to event_completed for backward compatibility
            print(f"[TriggerEvaluator] Warning: 'battle_won' trigger type is deprecated, use 'event_completed' instead")
            # Create a new trigger dict with the updated type and delegate to event_completed handler
            legacy_trigger = dict(trigger)
            legacy_trigger['type'] = 'event_completed'
            return self.check_trigger(legacy_trigger, current_map_id)
        elif ttype == 'dialog_contains_text':
            raw_dialog = self.env.read_dialog() or ''
            norm_dialog = re.sub(r'\s+', ' ', raw_dialog.replace('\n', ' ')).strip()
            target_text_raw = trigger.get('text', '')
            target_text_norm = re.sub(r'\s+', ' ', target_text_raw.replace('\n', ' ')).strip()
            result = (target_text_norm in norm_dialog)
            values_str = f"Dialog: '{norm_dialog[:50]}...'"
            debug_str = f"Does Dialog contain '{target_text_norm}'?"
        elif ttype == 'item_received_dialog':
            raw_dialog = self.env.read_dialog() or ''
            norm_dialog = re.sub(r'\s+', ' ', raw_dialog.replace('\n', ' ')).strip()
            target_text_raw = trigger.get('text', '')
            target_text_norm = re.sub(r'\s+', ' ', target_text_raw.replace('\n', ' ')).strip()
            result = (target_text_norm in norm_dialog)
            values_str = f"Dialog: '{norm_dialog[:50]}...'"
            debug_str = f"Does Item Receipt Dialog contain '{target_text_norm}'?"
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
            debug_str = f"Is Count ({current_item_count}) of '{target_item_name_clean}' >= MinQty ({target_qty_min})?"
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
            debug_str = f"Does Party contain '{target_species_name}'?"
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
                result = False # Unknown target type
            values_str = f"CurrentBattleType: {current_battle_type_str} (Raw: {current_battle_type_raw})"
            debug_str = f"Is CurrentBattleType ({current_battle_type_str}) == TargetType ({target_battle_type_name})?"
        elif ttype == 'badge_is_obtained':
            target_badge_name = trigger.get('badge_name', '').upper()
            target_badge_bit = f"BIT_{target_badge_name}BADGE" if not target_badge_name.endswith('BADGE') else f"BIT_{target_badge_name}"
            badge_status = self.env.flags.get_bit(target_badge_bit)
            result = badge_status
            values_str = f"Badge '{target_badge_name}' Status: {badge_status}"
            debug_str = f"Is Badge '{target_badge_name}' ({target_badge_bit}) obtained (True)?"
        elif ttype == 'coordinates_are':
            dialog_active = bool((self.env.read_dialog() or '').strip())
            if dialog_active:
                result = False
                values_str = "Dialog Active"
                debug_str = "Coordinate check skipped: Dialog Active."
            else:
                x_local, y_local, map_cur = self.env.get_game_coords()
                global_y, global_x = local_to_global(y_local, x_local, map_cur)
                
                conditions_met = True
                value_parts = [f"ActualCoords: (X:{global_x}, Y:{global_y}, Map:{map_cur})"]
                debug_parts = ["Check Coords:"]

                if 'x_min' in trigger:
                    target_x_min = trigger['x_min']
                    conditions_met = conditions_met and (global_x > target_x_min)
                    debug_parts.append(f"X ({global_x}) > {target_x_min}?")
                if 'x_max' in trigger:
                    target_x_max = trigger['x_max']
                    conditions_met = conditions_met and (global_x < target_x_max)
                    debug_parts.append(f"X ({global_x}) < {target_x_max}?")
                if 'x' in trigger:
                    target_x = trigger['x']
                    conditions_met = conditions_met and (global_x == target_x)
                    debug_parts.append(f"X ({global_x}) == {target_x}?")
                if 'y_min' in trigger:
                    target_y_min = trigger['y_min']
                    conditions_met = conditions_met and (global_y > target_y_min)
                    debug_parts.append(f"Y ({global_y}) > {target_y_min}?")
                if 'y_max' in trigger:
                    target_y_max = trigger['y_max']
                    conditions_met = conditions_met and (global_y < target_y_max)
                    debug_parts.append(f"Y ({global_y}) < {target_y_max}?")
                if 'y' in trigger:
                    target_y = trigger['y']
                    conditions_met = conditions_met and (global_y == target_y)
                    debug_parts.append(f"Y ({global_y}) == {target_y}?")
                if 'map_id' in trigger:
                    target_map_id = trigger['map_id']
                    conditions_met = conditions_met and (map_cur == target_map_id)
                    debug_parts.append(f"MapID ({map_cur}) == {target_map_id}?")
                
                result = conditions_met
                values_str = '; '.join(value_parts)
                debug_str = ' '.join(debug_parts)
        elif ttype == 'party_hp_is_full':
            current_hp_fraction = self.env.read_hp_fraction() if hasattr(self.env, 'read_hp_fraction') else 0.0
            result = (current_hp_fraction >= 1.0)
            values_str = f"PartyHP Fraction: {current_hp_fraction:.2f}"
            debug_str = f"Is PartyHP Fraction ({current_hp_fraction:.2f}) >= 1.0?"
        else:
            result = False # Keep default
            values_str = f"Unsupported Type: {ttype}" # Keep default
            debug_str = f"Trigger type '{ttype}' is not implemented or recognized."
            # raise NotImplementedError(f"Unsupported trigger type: {ttype}") # Soft fail instead of crash

        return {"result": result, "values_str": values_str, "debug_str": debug_str}

    def check_all(self, triggers: List[Dict]) -> List[Dict[str, any]]:
        """Return list of detailed results for each trigger"""
        results = []
        for trg in triggers:
            results.append(self.check_trigger(trg))
        return results

# Example helper to evaluate a full set of event_triggers for a quest step

def evaluate_triggers_for_step(env: 'RedGymEnv', triggers: List[Dict], prev_map_id: Optional[int]) -> bool:
    evaluator = TriggerEvaluator(env)
    # prev_map_id is now handled by map_history within evaluator
    # evaluator.prev_map_id = prev_map_id 
    return all(evaluator.check_trigger(trg)["result"] for trg in triggers) 