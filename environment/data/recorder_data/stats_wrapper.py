from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

import numpy as np
from gymnasium import Env

from environment.data.recorder_data.events import filtered_event_names
from environment.data.environment_data.items import Items
from environment.data.recorder_data.map_data import map_locations
from environment.data.environment_data.moves import Moves
from environment import RedGymEnv
from environment.data.recorder_data.pokedex import Pokedex, PokedexOrder

event_flags_start = 0xD747
event_flags_end = 0xD887
MAP_N_ADDRESS = 0xD35E


class WildEncounterResult(Enum):
    WIN = 0
    LOSE = 1
    CAUGHT = 2
    ESCAPED = 3

    def __repr__(self):
        return self.name


@dataclass
class WildEncounter:
    species: PokedexOrder
    level: int
    result: WildEncounterResult


class StatsWrapper(Env):
    def __init__(self, env: RedGymEnv):
        self.env = env
        # Initialize move_usage to track how many times each move is used
        self.move_usage = defaultdict(int)

        self.env.hook_register(
            None, "PlayerCanExecuteMove", self.increment_move_hook, None
        )
        # self.env.hook_register(
        #     None, "AnimateHealingMachine", self.pokecenter_hook, None
        # )
        # self.env.hook_register(
        #     None, "RedsHouse1FMomText.heal", self.pokecenter_hook, None
        # )
        # self.env.hook_register(None, "UseItem_", self.chose_item_hook, None)
        # self.env.hook_register(
        #     None, "FaintEnemyPokemon.wild_win", self.record_wild_win_hook, None
        # )
        # self.env.hook_register(None, "HandlePlayerBlackOut", self.blackout_hook, None)
        # self.env.hook_register(
        #     None, "ItemUseBall.captured", self.catch_pokemon_hook, None
        # )
        # self.env.hook_register(
        #     None, "TryRunningFromBattle.canEscape", self.escaped_battle_hook, None
        # )

    def reset(self):
        pass

    def step(self, action):
        pass

    def render(self):
        pass

    def init_stats_fields(self, event_obs):
        self.party_size = 1
        self.total_heal = 0
        self.num_heals = 0
        self.died_count = 0
        self.party_levels = np.asarray([-1 for _ in range(6)])
        self.events_sum = 0
        self.max_opponent_level = 0
        self.seen_coords = 0
        self.current_location = self.env.read_m(MAP_N_ADDRESS)
        self.location_first_visit_steps = {loc: -1 for loc in map_locations.keys()}
        self.location_frequency = {loc: 0 for loc in map_locations.keys()}
        self.location_steps_spent = {loc: 0 for loc in map_locations.keys()}
        self.current_events = event_obs
        self.events_steps = {name: -1 for name in filtered_event_names}
        self.caught_species = np.zeros(152, dtype=np.uint8)
        self.pokecenter_count = 0
        self.pokecenter_location_count = defaultdict(int)
        self.item_usage = defaultdict(int)
        self.wild_encounters: list[WildEncounter] = []

    def update_stats(self, event_obs):
        pass

    def update_party_levels(self):
        for i in range(
            self.env.memory[self.env.symbol_lookup("wPartyCount")[1]]
        ):
            self.party_levels[i] = self.env.memory[
                self.env.symbol_lookup(f"wPartyMon{i+1}Level")[1]
            ]

    def update_location_stats(self):
        new_location = self.env.memory[self.env.symbol_lookup("map_n_ADDRESS_placeholder")]
        if self.location_first_visit_steps[new_location] == -1:
            self.location_first_visit_steps[new_location] = self.env.step_count
        if new_location != self.current_location:
            self.location_frequency[new_location] += 1
            self.current_location = new_location
        elif new_location == self.current_location:
            self.location_steps_spent[new_location] += 1

    def update_event_stats(self, event_obs):
        comparison = self.current_events == event_obs
        if np.all(comparison):
            return
        changed_ids = np.where(comparison == False)[0]
        for i in changed_ids:
            self.events_steps[filtered_event_names[i]] = self.env.step_count
            self.events_sum += 1
        self.current_events = event_obs

    def update_pokedex(self):
        _, wPokedexOwned = self.env.symbol_lookup("wPokedexOwned")
        _, wPokedexOwnedEnd = self.env.symbol_lookup("wPokedexOwnedEnd")

        caught_mem = self.env.memory[wPokedexOwned:wPokedexOwnedEnd]
        self.caught_species = np.unpackbits(
            np.array(caught_mem, dtype=np.uint8), bitorder="little"
        )
    
    def update_time_played(self):
        hours = self.env.memory[self.env.symbol_lookup("wPlayTimeHours")[1]]
        minutes = self.env.memory[self.env.symbol_lookup("wPlayTimeMinutes")[1]]
        self.seconds_played = hours * 3600 + minutes * 60
        self.seconds_played += self.env.memory[self.env.symbol_lookup("wPlayTimeSeconds")[1]]

    def increment_move_hook(self, *args, **kwargs):
        _, wPlayerSelectedMove = self.env.symbol_lookup("wPlayerSelectedMove")
        self.move_usage[
            Moves(self.env.memory[wPlayerSelectedMove]).name.lower()
        ] += 1

    def pokecenter_hook(self, *args, **kwargs):
        self.pokecenter_count += 1
        map_location = self.env.memory[self.env.symbol_lookup(MAP_N_ADDRESS)]
        self.pokecenter_location_count[map_location] += 1

    def chose_item_hook(self, *args, **kwargs):
        _, wCurItem = self.env.symbol_lookup("wCurItem")
        self.item_usage[Items(self.env.memory[wCurItem]).name.lower()] += 1

    def record_battle(self, result: WildEncounterResult):
        _, wEnemyMon = self.env.symbol_lookup("wEnemyMon")
        _, wEnemyMon1Level = self.env.symbol_lookup("wCurEnemyLevel")
        self.wild_encounters.append(
            WildEncounter(
                species=PokedexOrder(self.env.memory[wEnemyMon]),
                level=self.env.memory[wEnemyMon1Level],
                result=result,
            )
        )

    def record_wild_win_hook(self, *args, **kwargs):
        self.record_battle(WildEncounterResult.WIN)

    def blackout_hook(self, *args, **kwargs):
        _, wIsInBattle = self.env.symbol_lookup("wIsInBattle")
        if self.env.memory[wIsInBattle] == 1:
            self.record_battle(WildEncounterResult.LOSE)

    def catch_pokemon_hook(self, *args, **kwargs):
        self.record_battle(WildEncounterResult.CAUGHT)

    def escaped_battle_hook(self, *args, **kwargs):
        self.record_battle(WildEncounterResult.ESCAPED)

    def get_info(self):
        info = {
            "seconds_played": self.seconds_played,
            "party_size": self.party_size,
            "party_levels": self.party_levels,
            "caught_species": {
                Pokedex(pokemon_id + 1).name
                for pokemon_id, caught in enumerate(self.caught_species)
                if caught
            },
            "total_heal": self.total_heal,
            "num_heals": self.num_heals,
            "died_count": self.died_count,
            "seen_coords": self.seen_coords,
            "max_opponent_level": self.max_opponent_level,
            "events_sum": self.events_sum,
            "events_steps": self.events_steps,
            "move_usage": self.move_usage,
            "pokecenter_count": sum(self.pokecenter_location_count.values()),
            "pokecenter_location_count": self.pokecenter_location_count,
            "item_usage": self.item_usage,
            "location_first_visit_steps": self.location_first_visit_steps,
            "location_frequency": self.location_frequency,
            "location_steps_spent": self.location_steps_spent,
            "wild_encounters": self.wild_encounters,
        }
        return info
