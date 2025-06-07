"""
Grok Integration Module - Fully Modular Autonomous Pokemon Player
Provides complete isolation of Grok logic from main play.py
"""

import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass, asdict
import logging

# Import RedGymEnv
from environment.environment import RedGymEnv # Changed
from environment.data.environment_data.items import Items # For type hinting from RedGymEnv
from environment.data.environment_data.types import PokemonType as EnvPokemonType # For type hinting
from environment.data.environment_data.party import PartyMons # For type hinting
from environment.data.environment_data.moves import Moves as Move # For TM/HM names if needed
from environment.data.environment_data.species import Species as PokemonSpecies # Added for species names

# EnvWrapper and QuestManager might still be used by SimpleAgent, kept for now if called by it
from environment.wrappers.env_wrapper import EnvWrapper 
from environment.environment_helpers.quest_manager import QuestManager
from environment.data.recorder_data.global_map import local_to_global # For global coords

# Keep GameState dataclass
@dataclass
class GameState:
    """Structured game state for UI updates"""
    location: Dict[str, Any]
    quest_id: Optional[int] 
    party: List[Dict[str, Any]]
    dialog: Optional[str]
    in_battle: bool 
    hp_fraction: float 
    money: int
    badges: int
    pokedex_seen: int
    pokedex_caught: int
    steps: int 
    items: List[str]

# Helper functions for type conversion if needed, but RedGymEnv might provide direct strings/enums
def _convert_pokemon_type_to_str(type_enum: Optional[EnvPokemonType]) -> Optional[str]:
    return type_enum.name.lower() if type_enum else None

def _get_status_string_from_env(status_byte: int) -> str:
    # RedGymEnv's PartyMon.Status is already the byte.
    # We need a mapping similar to PokemonRedReader's StatusCondition.get_status_name()
    # For now, returning raw byte, or we can replicate the logic from memory_reader.py here if essential.
    # Let's replicate simplified status mapping for clarity
    if status_byte == 0: return "OK"
    if status_byte & 0b111: return "SLEEP" # Bits 0-2 for sleep counter
    if status_byte & 0b00001000: return "POISON"
    if status_byte & 0b00010000: return "BURN"
    if status_byte & 0b00100000: return "FREEZE"
    if status_byte & 0b01000000: return "PARALYSIS"
    return "UNKNOWN"


def extract_structured_game_state(env_wrapper: EnvWrapper, reader: RedGymEnv, quest_manager: Optional[QuestManager] = None) -> GameState: # env_wrapper was optional
    """Extract comprehensive game state using RedGymEnv directly."""
    try:
        # Location
        coord_x, coord_y, map_id_val = reader.get_game_coords()
        map_name = reader.get_map_name_by_id(map_id_val)
        gy, gx = reader.local_to_global(coord_y, coord_x, map_id_val) if hasattr(reader, 'local_to_global') else (None, None)

        location_data = {
            "x": coord_x, "y": coord_y, 
            "gx": gx, "gy": gy, 
            "map_id": map_id_val, 
            "map_name": map_name
        }

        # Party
        party_data = []
        num_party_pokemon = reader.read_m("wPartyCount")
        for i in range(num_party_pokemon):
            pokemon: PartyMons = reader.party[i]
            
            move_names = []
            if hasattr(pokemon, 'Moves'):
                for move_id_val in pokemon.Moves:
                    try:
                        move_names.append(Move(move_id_val).name)
                    except ValueError:
                        move_names.append(f"UNKNOWN_MOVE_{move_id_val}")
            
            type1_str = _convert_pokemon_type_to_str(EnvPokemonType(pokemon.Type1)) if pokemon.Type1 is not None else None
            type2_str = _convert_pokemon_type_to_str(EnvPokemonType(pokemon.Type2)) if pokemon.Type2 is not None and pokemon.Type1 != pokemon.Type2 else None
            types_list = [t for t in [type1_str, type2_str] if t]

            species_name_str = "UnknownSpecies"
            try:
                species_name_str = PokemonSpecies(pokemon.Species).name
            except ValueError:
                species_name_str = f"SPECIES_ID_{pokemon.Species}"

            party_data.append({
                "id": pokemon.Species,
                "species": species_name_str,
                "nickname": f"Pkmn{i+1}", # RedGymEnv PartyMon doesn't store nickname directly
                "level": pokemon.Level,
                "hp": pokemon.HP,
                "maxHp": pokemon.MaxHP,
                "types": types_list,
                "status": _get_status_string_from_env(pokemon.Status)
            })

        # Money - RedGymEnv stores money in BCD. Need to convert.
        # wPlayerMoney is at 0xD347 for Red/Blue
        money = 0
        try:
            _, money_addr = reader.pyboy.symbol_lookup("wPlayerMoney")
            money_bcd_bytes = [reader.read_m(money_addr + i) for i in range(3)] # Example address
            multiplier = 1
            for byte in reversed(money_bcd_bytes):
                money += (byte >> 4) * 10 * multiplier + (byte & 0x0F) * multiplier
                multiplier *= 100
        except Exception as e_money:
            logging.warning(f"Could not read/convert money: {e_money}")
            money = -1 # Indicate error or unavailable
        
        # Badges
        num_badges = reader.get_badges() # This returns count

        # Pokedex
        pokedex_seen = int(sum(reader.seen_pokemon))
        pokedex_caught = int(sum(reader.caught_pokemon))

        # Items
        items_in_bag = reader.get_items_in_bag() # Returns Iterable[Items]
        items_list = [item.name for item in items_in_bag]
        
        # Dialog
        dialog = reader.read_dialog()

        # In Battle
        in_battle = reader.read_m("wIsInBattle") > 0

        # HP Fraction
        hp_fraction = reader.read_hp_fraction()
        
        # Steps
        steps = reader.step_count

        # Quest ID: Determine current quest via QuestManager if available, else fallback to reader
        quest_id_to_report = reader.current_loaded_quest_id
        if quest_manager and hasattr(quest_manager, 'get_current_quest'):
            current = quest_manager.get_current_quest()
            if current is not None:
                quest_id_to_report = current
        elif quest_manager and hasattr(quest_manager, 'current_quest_id') and quest_manager.current_quest_id is not None:
            try:
                quest_id_to_report = int(quest_manager.current_quest_id)
            except (ValueError, TypeError):
                logging.warning(f"QuestManager.current_quest_id ('{quest_manager.current_quest_id}') is not valid. Using reader.current_loaded_quest_id.")
                quest_id_to_report = reader.current_loaded_quest_id

        return GameState(
            location=location_data,
            quest_id=quest_id_to_report, 
            party=party_data,
            dialog=dialog,
            in_battle=in_battle, 
            hp_fraction=hp_fraction,
            money=money,
            badges=num_badges,
            pokedex_seen=pokedex_seen,
            pokedex_caught=pokedex_caught,
            steps=steps, 
            items=items_list
        )
    except Exception as e:
        logging.error(f"Error extracting structured game state from RedGymEnv: {e}", exc_info=True)
        # Return minimal/default state on error
        return GameState(
            location={"x": 0, "y": 0, "gx": 0, "gy": 0, "map_id": 0, "map_name": "Unknown"},
            quest_id=None, party=[], dialog=None, in_battle=False,
            hp_fraction=1.0, money=0, badges=0, pokedex_seen=0,
            pokedex_caught=0, steps=0, items=[]
        )

# Removed GrokIntegration class, FastAPI app, routes, SSE, Grok client, threading, queues, etc.
# Removed old _extract_game_state, _get_grok_decision, _convert_tool_calls_to_actions, etc.
# Removed singleton logic for _grok_instance and related functions.
# Removed agent.emulator and agent.memory_reader imports.