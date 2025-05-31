import re
from typing import Dict, Optional, Tuple, List

from environment.data.environment_data.events import EventFlags
from environment.data.environment_data.flags import Flags
from environment.data.environment_data.items import Items
from environment.data.environment_data.species import Species
from environment.data.recorder_data.global_map import local_to_global

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
        self.prev_map_id: Optional[int] = None

    def check_trigger(self, trigger: Dict, current_map_id: Optional[int] = None) -> bool:
        ttype = trigger.get('type')
        # Evaluate each trigger type and store result
        if ttype == 'current_map_id_is':
            result = (self.env.get_game_coords()[2] == trigger.get('map_id'))
        elif ttype == 'previous_map_id_was':
            result = (self.prev_map_id is not None and self.prev_map_id == trigger.get('map_id'))
        elif ttype == 'party_size_is':
            result = (self.env.read_m('wPartyCount') == trigger.get('size'))
        elif ttype == 'battle_won':
            # Trainer battle completion flag may not exist for some identifiers; allow explicit event_name override
            identifier = trigger.get('opponent_identifier', '')
            if 'event_name' in trigger:
                name = trigger.get('event_name')
            else:
                name = f"EVENT_BATTLED_{identifier.upper()}"
            try:
                result = bool(self.env.events.get_event(name))
            except Exception as e:
                print(f"[TriggerEvaluator] Warning: unknown battle event '{name}', treating as False ({e})")
                result = False
        elif ttype == 'dialog_contains_text':
            raw_dialog = self.env.read_dialog() or ''
            norm_dialog = re.sub(r'\s+', ' ', raw_dialog.replace('\n', ' ')).strip()
            raw_text = trigger.get('text', '')
            norm_text = re.sub(r'\s+', ' ', raw_text.replace('\n', ' ')).strip()
            result = (norm_text in norm_dialog)
        elif ttype == 'item_received_dialog':
            raw_dialog = self.env.read_dialog() or ''
            norm_dialog = re.sub(r'\s+', ' ', raw_dialog.replace('\n', ' ')).strip()
            raw_text = trigger.get('text', '')
            norm_text = re.sub(r'\s+', ' ', raw_text.replace('\n', ' ')).strip()
            result = (norm_text in norm_dialog)
        elif ttype == 'item_is_in_inventory':
            qty_min = trigger.get('quantity_min', 1)
            # Read raw bag entries: pairs of (item_id, quantity)
            num_slots = self.env.read_m('wNumBagItems')
            _, addr = self.env.pyboy.symbol_lookup('wBagItems')
            raw_bytes = self.env.pyboy.memory[addr : addr + 2 * num_slots]
            # Normalize trigger item name
            raw_name = trigger.get('item_name', '').upper()
            clean_name = raw_name.replace('É', 'E').replace(' ', '_')
            clean_name = re.sub(r'[^A-Z0-9_]', '', clean_name)
            # Sum quantities for matching item
            count = 0
            for i in range(0, len(raw_bytes), 2):
                item_id = raw_bytes[i]
                quantity = raw_bytes[i+1]
                try:
                    item_enum = Items(item_id)
                except ValueError:
                    continue
                if item_enum.name == clean_name:
                    count += quantity
            result = (count >= qty_min)
        elif ttype == 'party_pokemon_species_is':
            species_name = trigger.get('species_name', '')
            party = self.env.party.party[:self.env.party.party_size]
            # Safely check each party member, skipping invalid species IDs
            result = False
            for p in party:
                try:
                    if Species(p.Species).name == species_name:
                        result = True
                        break
                except ValueError:
                    # Skip invalid or empty slots
                    continue
        elif ttype == 'battle_type_is':
            # Directly read battle type memory (0xD057): 1=wild, 2=trainer
            battle_type = trigger.get('battle_type_name', '').upper()
            raw = self.env.read_m(0xD057)
            if battle_type == 'TRAINER':
                result = (raw == 2)
            elif battle_type == 'WILD':
                result = (raw == 1)
            else:
                result = False
        elif ttype == 'badge_is_obtained':
            badge = trigger.get('badge_name', '').upper()
            bit = f"BIT_{badge}BADGE" if not badge.endswith('BADGE') else f"BIT_{badge}"
            result = self.env.flags.get_bit(bit)
        elif ttype == 'coordinates_are':
            # Disable coordinate triggers when any dialog is active
            raw_dialog = self.env.read_dialog() or ''
            if raw_dialog.strip():
                return False
            x_local, y_local, map_cur = self.env.get_game_coords()
            global_y, global_x = local_to_global(y_local, x_local, map_cur)
            result = True
            # Global x coordinate exact match or range
            if 'x_min' in trigger:
                result = result and (global_x > trigger.get('x_min'))
            if 'x_max' in trigger:
                result = result and (global_x < trigger.get('x_max'))
            if 'x' in trigger:
                result = result and (global_x == trigger.get('x'))
            # Global y coordinate exact match or range
            if 'y_min' in trigger:
                result = result and (global_y > trigger.get('y_min'))
            if 'y_max' in trigger:
                result = result and (global_y < trigger.get('y_max'))
            if 'y' in trigger:
                result = result and (global_y == trigger.get('y'))
            # Map id filter if provided
            if 'map_id' in trigger:
                result = result and (map_cur == trigger.get('map_id'))
        elif ttype == 'party_hp_is_full':
            # Check if all party Pokémon have full health
            # Using read_hp_fraction which is 1.0 when current HP equals max HP
            result = (hasattr(self.env, 'read_hp_fraction') and self.env.read_hp_fraction() >= 1.0)
        else:
            raise NotImplementedError(f"Unsupported trigger type: {ttype}")
        # Debug print
        # print(f"[TriggerEvaluator] Type={ttype}, Trigger={trigger}, Result={result}")
        return result

    def check_all(self, triggers: List[Dict]) -> List[bool]:
        """Return list of booleans for each trigger"""
        results = []
        for trg in triggers:
            results.append(self.check_trigger(trg))
        return results

# Example helper to evaluate a full set of event_triggers for a quest step

def evaluate_triggers_for_step(env: 'RedGymEnv', triggers: List[Dict], prev_map_id: Optional[int]) -> bool:
    evaluator = TriggerEvaluator(env)
    evaluator.prev_map_id = prev_map_id
    return all(evaluator.check_trigger(trg) for trg in triggers) 