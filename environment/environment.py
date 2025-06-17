# environment.py
import io
import os
import time
import uuid
import json # Added for saving path trace data
from abc import abstractmethod
from collections import deque
from multiprocessing import Lock, shared_memory
from pathlib import Path
from typing import Any, Iterable, Optional
from datetime import datetime
from PIL import Image
import heapq
import logging
import logging.handlers
import sys

import mediapy as media
import numpy as np
import numpy.typing as npt
from gymnasium import Env, spaces
from omegaconf import DictConfig, ListConfig
from pyboy import PyBoy
from pyboy.utils import WindowEvent

from environment.data.environment_data.elevators import NEXT_ELEVATORS
from environment.data.environment_data.events import (
    EVENT_FLAGS_START,
    EVENTS_FLAGS_LENGTH,
    MUSEUM_TICKET,
    REQUIRED_EVENTS,
    EventFlags,
)
from environment.data.environment_data.field_moves import FieldMoves
from environment.data.environment_data.flags import Flags
from environment.data.environment_data.items import (
    HM_ITEMS,
    KEY_ITEMS,
    MAX_ITEM_CAPACITY,
    REQUIRED_ITEMS,
    USEFUL_ITEMS,
    Items,
)
from environment.data.environment_data.map import (
    MAP_ID_COMPLETION_EVENTS,
    MapIds,
)
from environment.data.environment_data.missable_objects import MissableFlags
from environment.data.environment_data.party import PartyMons
from environment.data.environment_data.strength_puzzles import STRENGTH_SOLUTIONS
from environment.data.environment_data.tilesets import Tilesets
from environment.data.environment_data.tm_hm import (
    CUT_SPECIES_IDS,
    STRENGTH_SPECIES_IDS,
    SURF_SPECIES_IDS,
    TmHmMoves,
)
from environment.data.environment_data.moves import Moves as Move
from environment.data.environment_data.types import PokemonType
from environment.data.recorder_data.global_map import GLOBAL_MAP_SHAPE, local_to_global
from debug.debug import debug_print
from environment.data.environment_data.warps import WARP_DICT
from environment.data.recorder_data.global_map import local_to_global, global_to_local, MAP_DATA
from environment.environment_helpers.navigator import InteractiveNavigator
import itertools
import tempfile

from environment.environment_helpers.tile_visualizer import overlay_on_screenshot
from environment.environment_helpers.quest_path_visualizer import QuestPathVisualizer
from environment.environment_helpers.stage_helper import StageManager
from environment.data.environment_data.menus import (
    TEXT_MENU_CURSOR_LOCATION,
    TEXT_MENU_CURSOR_LOCATIONS,
    PC_POKE_MENU_CURSOR_LOCATIONS,
    PC_ITEM_MENU_CURSOR_LOCATIONS,
    BATTLE_MENU_STATES,
    RedRamMenuValues,
    RedRamSubMenuValues,
)

PIXEL_VALUES = np.array([0, 85, 153, 255], dtype=np.uint8)
VISITED_MASK_SHAPE = (144 // 16, 160 // 16, 1)


VALID_ACTIONS = [
    WindowEvent.PRESS_ARROW_DOWN,    # 0: down
    WindowEvent.PRESS_ARROW_LEFT,    # 1: left
    WindowEvent.PRESS_ARROW_RIGHT,   # 2: right
    WindowEvent.PRESS_ARROW_UP,      # 3: up
    WindowEvent.PRESS_BUTTON_A,      # 4: a
    WindowEvent.PRESS_BUTTON_B,      # 5: b
    None,                            # 6: path-follow (handled in step()) - KEY "5"
    WindowEvent.PRESS_BUTTON_START,  # 7: start
    WindowEvent.PRESS_BUTTON_SELECT,  # 8: select (used as noop)
]

VALID_RELEASE_ACTIONS = [
    WindowEvent.RELEASE_ARROW_DOWN,
    WindowEvent.RELEASE_ARROW_LEFT,
    WindowEvent.RELEASE_ARROW_RIGHT,
    WindowEvent.RELEASE_ARROW_UP,
    WindowEvent.RELEASE_BUTTON_A,
    WindowEvent.RELEASE_BUTTON_B,
    None,                         # 6: path-follow
    WindowEvent.RELEASE_BUTTON_START,
    WindowEvent.RELEASE_BUTTON_SELECT, # 8: select (used as noop)
]

from environment.data.environment_data.item_handler import ItemHandler
from environment.environment_helpers.tile_visualizer import overlay_on_screenshot
from environment.data.environment_data.ram_addresses import RamAddress as RAM
from environment.data.environment_data.battle import (
    PokemonData,
    StatusCondition,
    ENEMY_PARTY_COUNT, 
    ENEMY_PARTY_SPECIES, 
    ENEMYS_POKEMON_TYPES, 
    POKEMON_MATCH_TYPES,
    PLAYERS_MOVE_POWER,
    PLAYERS_MOVE_TYPE,
    PLAYERS_MOVE_PP,
    PLAYERS_MOVE_NUM,
    ENEMYS_POKEMON_HP,
    ENEMYS_POKEMON_MAX_HP,
)

from environment.data.environment_data.species import Species
from environment.data.environment_data.constants import MOVES_INFO_DICT, ID_TO_SPECIES


VALID_ACTIONS_STR = ["down", "left", "right", "up", "a", "b", "path", "start"]

PATH_FOLLOW_ACTION = 6  # discrete action index for path-follow (key "5")
ACTION_SPACE = spaces.Discrete(len(VALID_ACTIONS))  # total actions including path-follow and start

# x, y, map_n
SEAFOAM_SURF_SPOTS = {
    (23, 5, 162),
    (7, 11, 162),
    (7, 3, 162),
    (15, 7, 161),
    (23, 9, 161),
    (25, 16, 162),
}

logger = logging.getLogger(__name__)

# Add logging import at the top
sys.path.append('/puffertank/grok_plays_pokemon')
from utils.logging_config import get_pokemon_logger


class RedGymEnv(Env):
    env_id = shared_memory.SharedMemory(create=True, size=4)
    lock = Lock()

    def __init__(self, env_config: DictConfig | dict):
        if isinstance(env_config, dict):
            env_config = DictConfig(env_config)

        self.navigator = None
        self.persisted_loaded_quest_statuses = None
        self.persisted_loaded_trigger_statuses = None

        # Config-driven assignments
        self.video_dir = Path(env_config.video_dir)
        self.headless = env_config.headless
        self.emulator_delay = env_config.emulator_delay
        self.state_dir = Path(env_config.state_dir)

        # Initial state logic
        self.init_state = env_config.override_init_state or None
        self.init_from_last_ending_state = env_config.init_from_last_ending_state
        if not self.init_state and self.init_from_last_ending_state:
            self.init_state = None  # will load last ending state

        if self.init_state:
            path = Path(self.init_state)
            if path.suffix == '.state':
                self.init_state_path = path
                self.init_state_name = path.stem
            else:
                self.init_state_name = self.init_state
                self.init_state_path = self.state_dir / f"{self.init_state_name}.state"
        else:
            self.init_state_name = None
            self.init_state_path = None

        self.action_freq = env_config.action_freq
        self.max_steps = False
        self.save_video = env_config.save_video
        self.fast_video = env_config.fast_video
        self.fps = 60 if self.fast_video else 6
        self.n_record = env_config.n_record
        self.perfect_ivs = env_config.perfect_ivs
        self.reduce_res = False
        self.gb_path = env_config.gb_path
        self.log_frequency = False
        self.two_bit = False
        self.auto_flash = env_config.auto_flash
        self.required_tolerance = False

        # Encounter and automation flags
        self.disable_wild_encounters = bool(env_config.disable_wild_encounters)
        self.disable_wild_encounters_maps = set()
        self.auto_teach_cut = env_config.auto_teach_cut
        self.auto_teach_surf = env_config.auto_teach_surf
        self.auto_teach_strength = env_config.auto_teach_strength
        self.auto_use_cut = env_config.auto_use_cut
        self.auto_use_surf = env_config.auto_use_surf
        self.auto_use_strength = env_config.auto_use_strength
        self.auto_solve_strength_puzzles = env_config.auto_solve_strength_puzzles
        self.auto_remove_all_nonuseful_items = env_config.auto_remove_all_nonuseful_items
        self.auto_pokeflute = env_config.auto_pokeflute
        self.auto_next_elevator_floor = env_config.auto_next_elevator_floor
        self.skip_safari_zone = env_config.skip_safari_zone
        self.infinite_safari_steps = env_config.infinite_safari_steps
        self.insert_saffron_guard_drinks = env_config.insert_saffron_guard_drinks
        self.infinite_money = env_config.infinite_money
        self.infinite_health = env_config.infinite_health
        self.infinite_pp_and_move_hack = env_config.infinite_pp_and_move_hack
        self.use_global_map = False
        self.save_state = False
        self.animate_scripts = env_config.animate_scripts

        # Exploration parameters (disabled)
        self.map_history = deque(maxlen=10)
        self.exploration_inc = False
        self.exploration_max = False
        self.max_steps_scaling = False
        self.map_id_scalefactor = False

        # Action space and state
        self.action_space = ACTION_SPACE
        self.door_warp = False
        self.on_a_warp_tile = False
        self.next_to_warp_tile = False
        self.action_taken = None
        self.last_dialog = ''
        self.current_dialog = ''

        # Replay recording
        self.current_run_info = None
        self.current_run_dir = None
        try:
            self.record_replays = env_config.record_replays
        except Exception:
            self.record_replays = False

        # New override – if disable_recordings is True, forcibly disable
        # any replay saving regardless of the original record_replays value.
        self.disable_recordings = getattr(env_config, "disable_recordings", False)
        if self.disable_recordings:
            self.record_replays = False

        # Observation
        self.screen_output_shape = (144, 160, 3)
        self.global_map_shape = GLOBAL_MAP_SHAPE
        self.coords_pad = 12
        self.enc_freqs = 8

        # Video writers only if enabled
        if self.save_video:
            self.instance_id = str(uuid.uuid4())[:8]
            self.video_dir.mkdir(exist_ok=True)
            self.full_frame_writer = None
            self.map_frame_writer = None
            self.screen_obs_frame_writer = None
            self.visited_mask_frame_writer = None

        self.reset_count = 0
        self.all_runs = []

        self.metadata = {"render.modes": []}
        self.reward_range = (0, 15000)

        self.essential_map_locations = {
            v: i for i, v in enumerate([40, 0, 12, 1, 13, 51, 2, 54, 14, 59, 60, 61, 15, 3, 65])
        }

        obs_dict = {
            "screen": spaces.Box(low=0, high=255, shape=self.screen_output_shape, dtype=np.uint8),
            "visited_mask": spaces.Box(
                low=0, high=255, shape=self.screen_output_shape, dtype=np.uint8
            ),
            # Discrete is more apt, but pufferlib is slower at processing Discrete
            "direction": spaces.Box(low=0, high=4, shape=(1,), dtype=np.uint8),
            "blackout_map_id": spaces.Box(low=0, high=0xF7, shape=(1,), dtype=np.uint8),
            "battle_type": spaces.Box(low=0, high=4, shape=(1,), dtype=np.uint8),
            # "x": spaces.Box(low=0, high=255, shape=(1,), dtype=np.u`int8),
            # "y": spaces.Box(low=0, high=255, shape=(1,), dtype=np.uint8),
            "map_id": spaces.Box(low=0, high=0xF7, shape=(1,), dtype=np.uint8),
            # "badges": spaces.Box(low=0, high=np.iinfo(np.uint16).max, shape=(1,), dtype=np.uint16),
            "bag_items": spaces.Box(
                low=0, high=max(Items._value2member_map_.keys()), shape=(20,), dtype=np.uint8
            ),
            "bag_quantity": spaces.Box(low=0, high=100, shape=(20,), dtype=np.uint8),
            # This could be a dict within a sequence, but we'll do it like this and concat later
            "species": spaces.Box(low=0, high=0xBE, shape=(6,), dtype=np.uint8),
            "hp": spaces.Box(low=0, high=714, shape=(6,), dtype=np.uint32),
            "status": spaces.Box(low=0, high=7, shape=(6,), dtype=np.uint8),
            "type1": spaces.Box(low=0, high=0x1A, shape=(6,), dtype=np.uint8),
            "type2": spaces.Box(low=0, high=0x1A, shape=(6,), dtype=np.uint8),
            "level": spaces.Box(low=0, high=100, shape=(6,), dtype=np.uint8),
            "maxHP": spaces.Box(low=0, high=714, shape=(6,), dtype=np.uint32),
            "attack": spaces.Box(low=0, high=714, shape=(6,), dtype=np.uint32),
            "defense": spaces.Box(low=0, high=714, shape=(6,), dtype=np.uint32),
            "speed": spaces.Box(low=0, high=714, shape=(6,), dtype=np.uint32),
            "special": spaces.Box(low=0, high=714, shape=(6,), dtype=np.uint32),
            "moves": spaces.Box(low=0, high=0xA4, shape=(6, 4), dtype=np.uint8),
            # Add 4 for rival_3, game corner rocket, saffron guard and lapras
            "events": spaces.Box(low=0, high=1, shape=(320,), dtype=np.uint8),
            "rival_3": spaces.Box(low=0, high=1, shape=(1,), dtype=np.uint8),
            "game_corner_rocket": spaces.Box(low=0, high=1, shape=(1,), dtype=np.uint8),
            "saffron_guard": spaces.Box(low=0, high=1, shape=(1,), dtype=np.uint8),
            "lapras": spaces.Box(low=0, high=1, shape=(1,), dtype=np.uint8),
        }
        if not self.skip_safari_zone:
            obs_dict["safari_steps"] = spaces.Box(low=0, high=502.0, shape=(1,), dtype=np.uint32)

        # Add minimap warp observations for comprehensive warp detection
        obs_dict["minimap_warp_obs"] = spaces.Box(low=0, high=829, shape=(9, 10), dtype=np.uint16)

        if self.use_global_map:
            obs_dict["global_map"] = spaces.Box(
                low=0, high=255, shape=self.global_map_shape, dtype=np.uint8
            )
        self.observation_space = spaces.Dict(obs_dict)

        self.pyboy = PyBoy(
            str(env_config.gb_path),
            debug=False,
            no_input=False,
            window="null",  # Always use "null" as play.py will handle rendering
            log_level="CRITICAL",
            symbols=os.path.join(os.path.dirname(__file__), "pokered.sym"),
            sound_emulated=False,
        )
        self.register_hooks()
        if not self.headless:  # self.headless is from env_config
            self.pyboy.set_emulation_speed(6)  # Keep this for when play.py wants visible output
        self.screen = self.pyboy.screen
        # Need a pyboy memory view to use memory_reader.py
        self.memory = self.pyboy.memory

        self.first = True
        self.item_handler = ItemHandler(self)
        self.navigator = InteractiveNavigator(self)
        self.stage_manager = StageManager(self)
        
        # Initialize logging state tracking to reduce spam
        self.prev_logged_location = None
        self.prev_logged_action = None
        self.prev_logged_dialog = None
        self.prev_logged_battle_state = None
        
        with RedGymEnv.lock:
            env_id = (
                (int(RedGymEnv.env_id.buf[0]) << 24)
                + (int(RedGymEnv.env_id.buf[1]) << 16)
                + (int(RedGymEnv.env_id.buf[2]) << 8)
                + (int(RedGymEnv.env_id.buf[3]))
            )
            self.env_id = env_id
            env_id += 1
            RedGymEnv.env_id.buf[0] = (env_id >> 24) & 0xFF
            RedGymEnv.env_id.buf[1] = (env_id >> 16) & 0xFF
            RedGymEnv.env_id.buf[2] = (env_id >> 8) & 0xFF
            RedGymEnv.env_id.buf[3] = (env_id) & 0xFF

        if self.save_video and self.n_record:
            self.save_video = self.env_id < self.n_record
        
        # Path following attributes
        self.combined_path = []
        self.current_path_target_index = 0
        self.current_loaded_quest_id = None # Add this for logging

        # Warp tile caching: in-memory and file-backed cache
        self._warp_info_cache = {}
        self._new_warp_info = {}

        # Initialize warp-related cache variables
        self._is_warping = None
        self._minimap_warp_obs = None

        # Quest path visualizer
        self.quest_visualizer = QuestPathVisualizer()
        self.show_quest_paths = True  # Enable quest path visualization by default
        self.quest_visualization_ids = [1, 2, 3, 4, 5]  # First 5 quests by default
        
        # Persisting action result from navigator
        self.persisted_final_action = None

        self.init_mem()

        # Initialize logger
        self.logger = get_pokemon_logger()

    def set_navigator(self, navigator):
        self.navigator = navigator

    def register_hooks(self):
        self.pyboy.hook_register(None, "DisplayStartMenu", self.start_menu_hook, None)
        self.pyboy.hook_register(None, "RedisplayStartMenu", self.start_menu_hook, None)
        self.pyboy.hook_register(None, "StartMenu_Item", self.item_menu_hook, None)
        self.pyboy.hook_register(None, "StartMenu_Pokemon", self.pokemon_menu_hook, None)
        self.pyboy.hook_register(None, "StartMenu_Pokemon.choseStats", self.chose_stats_hook, None)
        self.pyboy.hook_register(None, "StartMenu_Item.choseItem", self.chose_item_hook, None)
        self.pyboy.hook_register(None, "DisplayTextID.spriteHandling", self.sprite_hook, None)
        self.pyboy.hook_register(
            None, "CheckForHiddenObject.foundMatchingObject", self.hidden_object_hook, None
        )
        self.pyboy.hook_register(None, "HandleBlackOut", self.blackout_hook, None)
        self.pyboy.hook_register(None, "SetLastBlackoutMap.done", self.blackout_update_hook, None)
        if not self.auto_use_cut:
            self.pyboy.hook_register(None, "UsedCut.nothingToCut", self.cut_hook, context=False)
            self.pyboy.hook_register(None, "UsedCut.canCut", self.cut_hook, context=True)
        # there is already an event for waking up the snorlax. No need to make a hookd for it
        if not self.auto_pokeflute:
            self.pyboy.hook_register(
                None, "ItemUsePokeFlute.noSnorlaxToWakeUp", self.pokeflute_hook, context=False
            )
            self.pyboy.hook_register(
                None, "PlayedFluteHadEffectText.done", self.pokeflute_hook, context=True
            )
        if not self.auto_use_surf:
            self.pyboy.hook_register(None, "SurfingAttemptFailed", self.surf_hook, context=False)
            self.pyboy.hook_register(None, "ItemUseSurfboard.surf", self.surf_hook, context=True)

        if self.disable_wild_encounters:
            self.setup_disable_wild_encounters()
        self.pyboy.hook_register(None, "AnimateHealingMachine", self.pokecenter_heal_hook, None)
        # self.pyboy.hook_register(None, "OverworldLoopLessDelay", self.overworld_loop_hook, None)
        self.pyboy.hook_register(None, "CheckWarpsNoCollisionLoop", self.update_warps_hook, None)
        signBank, signAddr = self.pyboy.symbol_lookup("IsSpriteOrSignInFrontOfPlayer.retry")
        self.pyboy.hook_register(
            signBank,
            signAddr - 1,
            self.sign_hook,
            None,
        )
        self.pyboy.hook_register(None, "ItemUseBall.loop", self.use_ball_hook, None)
        self.reset_count = 0

    def setup_disable_wild_encounters(self):
        bank, addr = self.pyboy.symbol_lookup("TryDoWildEncounter.gotWildEncounterType")
        self.pyboy.hook_register(
            bank,
            addr + 8,
            self.disable_wild_encounter_hook,
            None,
        )

    def setup_enable_wild_ecounters(self):
        bank, addr = self.pyboy.symbol_lookup("TryDoWildEncounter.gotWildEncounterType")
        self.pyboy.hook_deregister(bank, addr + 8)

    def update_state(self, state: bytes):
        self.reset(seed=None, options={"state": state})

    def reset(self, seed: Optional[int] = None, options: Optional[dict[str, Any]] = None, _is_internal_call: bool = False):
        # restart game, skipping credits
        options = options or {}

        # Use current_call_infos for clarity within this specific reset invocation
        current_call_infos = {}

        # Reset recording attributes for the new run - but preserve run info if loading from last state
        self.path_trace_data = {}
        # Only clear run info if we're not going to load from last ending state
        if not (self.init_from_last_ending_state and not options.get("state", None)):
            self.current_run_info = None
            self.current_run_dir = None

        infos = {}
        self.explore_map_dim = 384
        if self.first or options.get("state", None) is not None:
            # We only init seen hidden objs once cause they can only be found once!
            state_loaded_successfully = False
            explicit_state_provided_this_call = False # Track if an explicit state was given in this call

            if options and options.get("state", None) is not None:
                try:
                    print(f"Attempting to load state from provided 'options'.")
                    self.pyboy.load_state(io.BytesIO(options["state"]))
                    state_loaded_successfully = True
                    explicit_state_provided_this_call = True
                    # Explicit state loaded, clear any persisted quest/trigger from previous init_from_last_ending_state
                    self.persisted_loaded_quest_statuses = None
                    self.persisted_loaded_trigger_statuses = None
                    print("State loaded successfully from 'options'. Cleared persisted quest/trigger statuses.")
                except Exception as e:
                    print(f"environment.py: reset(): Error loading state from 'options': {e}")
                    raise
            elif self.init_state_path:  # Only try if a path is configured
                try:
                    print(f"Attempting to load state from path: {self.init_state_path}")
                    state_file_to_load = Path(self.init_state_path)
                    with open(state_file_to_load, "rb") as f:
                        self.pyboy.load_state(f)
                    state_loaded_successfully = True
                    explicit_state_provided_this_call = True
                    # Explicit state loaded, clear persisted
                    self.persisted_loaded_quest_statuses = None
                    self.persisted_loaded_trigger_statuses = None
                    print(f"State loaded successfully from {state_file_to_load}. Cleared persisted quest/trigger statuses.")
                except FileNotFoundError:
                    print(f"environment.py: reset(): State file not found at {self.init_state_path}. Starting new game.")
                    
                except Exception as e: # Catch other errors like corrupted state
                    print(f"environment.py: reset(): Error loading state from {self.init_state_path}: {e}. Starting new game.")
                    
            elif self.init_from_last_ending_state and not explicit_state_provided_this_call: # Only if no explicit state override in this call
                try:
                    from .environment_helpers.saver import load_latest_run
                    
                    loaded_run_info = load_latest_run(self)
                    if loaded_run_info:
                        state_loaded_successfully = True # Assuming state load is part of load_latest_run if applicable
                        print(f"State loaded successfully from latest run: {loaded_run_info.run_id}")
                        
                        from .environment_helpers.saver import load_quest_progress, load_trigger_status
                        
                        lqs = load_quest_progress(loaded_run_info)
                        if lqs is not None: # Check for None explicitly
                            print(f"environment.py: reset(): Quest progress present. Loaded quest progress from run: {loaded_run_info.run_id}")
                            current_call_infos["loaded_quest_statuses"] = lqs
                            self.persisted_loaded_quest_statuses = lqs # PERSIST
                            print(f"environment.py: reset(): current_call_infos AFTER loading quest_statuses: {current_call_infos}")
                        else:
                            print(f"environment.py: reset(): No quest progress (or empty dict) in run: {loaded_run_info.run_id}. Not overriding current_call_infos or persisted.")
                            # DO NOT set current_call_infos["loaded_quest_statuses"] = {} here
                            # DO NOT set self.persisted_loaded_quest_statuses = {} here unless we intend to clear previous good data
                            # If persisted was already {}, it remains {}. If it had data, it keeps it.
                            if self.persisted_loaded_quest_statuses is None: # Initialize if never set
                                self.persisted_loaded_quest_statuses = {}

                        lts = load_trigger_status(loaded_run_info)
                        if lts is not None: # Check for None explicitly
                            print(f"Loaded trigger status from run: {loaded_run_info.run_id}")
                            current_call_infos["loaded_trigger_statuses"] = lts
                            self.persisted_loaded_trigger_statuses = lts # PERSIST
                            print(f"environment.py: reset(): current_call_infos AFTER loading trigger_statuses: {current_call_infos}")
                        else:
                            print(f"environment.py: reset(): No trigger status (or empty dict) in run: {loaded_run_info.run_id}. Not overriding current_call_infos or persisted.")
                            # DO NOT set current_call_infos["loaded_trigger_statuses"] = {} here
                            if self.persisted_loaded_trigger_statuses is None: # Initialize if never set
                                self.persisted_loaded_trigger_statuses = {}
                    else: 
                        print(f"environment.py: reset(): No previous runs found for init_from_last_ending_state. Initializing persisted statuses to empty if not set.")
                        if self.persisted_loaded_quest_statuses is None:
                            self.persisted_loaded_quest_statuses = {}
                        if self.persisted_loaded_trigger_statuses is None:
                            self.persisted_loaded_trigger_statuses = {}
                        # current_call_infos remains unpopulated for these keys here
                except Exception as e:
                    print(f"environment.py: reset(): Error loading last ending state: {e}. Initializing persisted to empty if not set.")
                    if self.persisted_loaded_quest_statuses is None: 
                        self.persisted_loaded_quest_statuses = {}
                    if self.persisted_loaded_trigger_statuses is None:
                        self.persisted_loaded_trigger_statuses = {}
                    # current_call_infos remains unpopulated for these keys here
            else: 
                print("environment.py: reset(): Not configured to load from last ending state or explicit state given. Defaulting/New game.")
                # if not explicit_state_provided_this_call, persisted statuses are kept.
                # current_call_infos remains unpopulated for these keys here unless explicit_state_provided_this_call was true (then they are cleared above)
            
            # REMOVED: This block was problematic as it always initialized with {} if state_loaded_successfully was false.
            # if not state_loaded_successfully and not (self.init_from_last_ending_state and not loaded_run_info) : # if not loading from last state and no run was found
            #     print("environment.py: reset(): Proceeding with a new game session (or PyBoy default state).")
            
            self.events = EventFlags(self.pyboy)
            self.missables = MissableFlags(self.pyboy)
            self.flags = Flags(self.pyboy)
            self.required_events = self.get_required_events()
            self.required_items = self.get_required_items()
            self.base_event_flags = sum(
                self.read_m(i).bit_count()
                for i in range(EVENT_FLAGS_START, EVENT_FLAGS_START + EVENTS_FLAGS_LENGTH)
            )

            if self.save_state:
                state = io.BytesIO()
                self.pyboy.save_state(state)
                state.seek(0)
                # Removed state binary data from infos as per user's previous instruction
                current_call_infos |= {
                    # "state": {
                    #     tuple(
                    #         sorted(list(self.required_events) + list(self.required_items))
                    #     ): state.read()
                    # },
                    "required_count": len(self.required_events) + len(self.required_items),
                    "env_id": self.env_id,
                }
        # lazy random seed setting
        # if not seed:
        #     seed = random.randint(0, 4096)
        #  self.pyboy.tick(seed, render=False)
        self.reset_count += 1

        self.flags = Flags(self.pyboy)
        self.party = PartyMons(self.pyboy)
        self.required_events = self.get_required_events()
        self.required_items = self.get_required_items()
        self.seen_pokemon = np.zeros(152, dtype=np.uint8)
        self.caught_pokemon = np.zeros(152, dtype=np.uint8)
        self.obtained_move_ids = np.zeros(0xA5, dtype=np.uint8)
        self.pokecenters = np.zeros(252, dtype=np.uint8)

        self.recent_screens = deque()
        self.recent_actions = deque()
        self.a_press = set()
        self.explore_map *= 0
        self.reward_explore_map *= 0
        self.cut_explore_map *= 0
        self.reset_mem()
        # Initialize map_history with current map
        self.current_map_id = self.read_m("wCurMap")
        self.map_history.clear()
        for i in range(10):
            self.map_history.append(self.current_map_id)

        self.update_pokedex()
        self.update_tm_hm_obtained_move_ids()
        self.party_size = self.read_m("wPartyCount")
        self.taught_cut = self.check_if_party_has_hm(TmHmMoves.CUT.value)
        self.taught_surf = self.check_if_party_has_hm(TmHmMoves.SURF.value)
        self.taught_strength = self.check_if_party_has_hm(TmHmMoves.STRENGTH.value)
        self.levels_satisfied = False
        self.base_explore = 0
        self.max_opponent_level = 0
        self.max_level_rew = 0
        self.max_level_sum = 0
        self.last_health = 1
        self.total_heal_health = 0
        self.died_count = 0
        self.step_count = 0
        self.blackout_check = 0
        self.blackout_count = 0
        self.use_surf = 0

        self.current_event_flags_set = {}
        self.event_progress = {} # TODO: implement event progress
        self.action_hist = np.zeros(len(VALID_ACTIONS))

        self.max_map_progress = 0

        # Wrap recording session for replay runs
        if self.record_replays:
            # --- Start of new recording session using RunManager ---
            from .environment_helpers.saver import create_new_run
            
            # Get current game state info for naming AFTER potential state load
            current_map_id_for_name = self.read_m("wCurMap")
            map_name_str_for_name = self.get_map_name_by_id(current_map_id_for_name)

            # Create new run using RunManager
            if not _is_internal_call: # Only create new run if not an internal call
                self.current_run_info = create_new_run(self, map_name_str_for_name, current_map_id_for_name)
                self.current_run_dir = self.current_run_info.run_dir  # Set both for compatibility
            
            # Log the first point in the path trace for the new run.
            if not _is_internal_call: # Also condition path trace on not being internal, or handle it based on self.current_run_info
                self.update_path_trace()
        
        self.current_path_target_index = 0 # Reset path index on environment reset

        # If this reset call didn't populate quest/trigger statuses (e.g. self.first=False, no options state),
        # but they were persisted from a previous init_from_last_ending_state, use them.
        # Ensure we use persisted if current_call_infos doesn't have the key OR if it's an empty dict from a failed load.
        if (not current_call_infos.get("loaded_quest_statuses") or current_call_infos.get("loaded_quest_statuses") == {}) and self.persisted_loaded_quest_statuses:
            current_call_infos["loaded_quest_statuses"] = self.persisted_loaded_quest_statuses
            print(f"environment.py: reset(): Using self.persisted_loaded_quest_statuses for return info: {self.persisted_loaded_quest_statuses}")
        if (not current_call_infos.get("loaded_trigger_statuses") or current_call_infos.get("loaded_trigger_statuses") == {}) and self.persisted_loaded_trigger_statuses:
            current_call_infos["loaded_trigger_statuses"] = self.persisted_loaded_trigger_statuses
            print(f"environment.py: reset(): Using self.persisted_loaded_trigger_statuses for return info: {self.persisted_loaded_trigger_statuses}")

        self.first = False
        
        # Reset logging state trackers on reset
        self.prev_logged_location = None
        self.prev_logged_action = None
        self.prev_logged_dialog = None
        self.prev_logged_battle_state = None
        if hasattr(self, '_path_follow_logged'):
            self._path_follow_logged = False
        
        # Apply infinite money and health on reset
        if self.infinite_money:
            _, wPlayerMoney = self.pyboy.symbol_lookup("wPlayerMoney")
            for offset in range(3):
                self.pyboy.memory[wPlayerMoney + offset] = 0x99
        if self.infinite_health:
            self.reverse_damage()
            self.party = PartyMons(self.pyboy)
        if self.infinite_pp_and_move_hack:
            # Infinite PP and super move hack in default reset
            for i in range(self.read_m("wPartyCount")):
                _, moves_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Moves")
                # Set first slot to Hyper Beam
                self.pyboy.memory[moves_addr] = TmHmMoves.HYPER_BEAM.value
                # Replenish PP for all slots
                _, pp_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}PP")
                for slot in range(4):
                    self.pyboy.memory[pp_addr + slot] = 0x3F
        else:
            # Ensure Charmander (species 4) keeps its default Scratch move if it was overwritten
            if self.read_m("wPartyCount") > 0:
                print(f"environment.py: reset(): Restoring Charmander's Scratch move and PP")
                species = self.read_m("wPartyMon1Species")
                print(f"environment.py: reset(): Species: {species}")
                if species == 176:  # Charmander species ID
                    _, moves_addr = self.pyboy.symbol_lookup("wPartyMon1Moves")
                    print(f"environment.py: reset(): Moves address: {moves_addr}")
                    self.pyboy.memory[moves_addr] = Move.SCRATCH.value
                    # Restore PP to 35 (0x23)
                    _, pp_addr = self.pyboy.symbol_lookup("wPartyMon1PP")
                    print(f"environment.py: reset(): PP address: {pp_addr}")
                    self.pyboy.memory[pp_addr] = 0x23

        # ADDED DEBUG: Print infos right before returning
        print(f"environment.py: reset(): FINAL current_call_infos before return: {current_call_infos}")
        return self._get_obs(), current_call_infos

    def init_mem(self):
        # Maybe I should preallocate a giant matrix for all map ids
        # All map ids have the same size, right?
        self.seen_coords: dict[int, dict[tuple[int, int, int], int]] = {}
        self.explore_map = np.zeros(GLOBAL_MAP_SHAPE, dtype=np.float32)
        self.reward_explore_map = np.zeros(GLOBAL_MAP_SHAPE, dtype=np.float32)
        self.cut_explore_map = np.zeros(GLOBAL_MAP_SHAPE, dtype=np.float32)
        self.seen_map_ids = np.zeros(256)
        self.seen_npcs = {}
        self.seen_warps = {}
        self.safari_zone_steps = {
            k: 0
            for k in [
                MapIds.SAFARI_ZONE_CENTER,
                MapIds.SAFARI_ZONE_CENTER_REST_HOUSE,
                MapIds.SAFARI_ZONE_EAST,
                MapIds.SAFARI_ZONE_EAST_REST_HOUSE,
                MapIds.SAFARI_ZONE_WEST,
                # MapIds.SAFARI_ZONE_WEST_REST_HOUSE,
                MapIds.SAFARI_ZONE_NORTH,
                MapIds.SAFARI_ZONE_NORTH_REST_HOUSE,
                MapIds.SAFARI_ZONE_SECRET_HOUSE,
            ]
        }

        self.valid_cut_coords = {}
        self.invalid_cut_coords = {}
        self.cut_tiles = {}

        self.valid_pokeflute_coords = {}
        self.invalid_pokeflute_coords = {}
        self.pokeflute_tiles = {}

        self.valid_surf_coords = {}
        self.invalid_surf_coords = {}
        self.surf_tiles = {}

        self.seen_hidden_objs = {}
        self.seen_signs = {}

        self.seen_start_menu = 0
        self.seen_pokemon_menu = 0
        self.seen_stats_menu = 0
        self.seen_bag_menu = 0
        self.seen_action_bag_menu = 0
        self.pokecenter_heal = 0
        self.use_ball_count = 0

    def reset_mem(self):
        self.seen_start_menu = 0
        self.seen_pokemon_menu = 0
        self.seen_stats_menu = 0
        self.seen_bag_menu = 0
        self.seen_action_bag_menu = 0
        self.pokecenter_heal = 0
        self.use_ball_count = 0
        self.never_run_again = False

    def render(self) -> npt.NDArray[np.uint8]:
        return self.screen.ndarray

    def screen_obs(self):
        # Return full RGB screen as numpy array
        game_pixels_render = self.screen.ndarray

        """
        import cv2
        cv2.imshow("a", game_pixels_render)
        cv2.waitKey(150)
        cv2.destroyAllWindows()
        """

        # place an overlay on top of the screen greying out places we haven't visited
        # first get our location
        player_x, player_y, map_n = self.get_game_coords()

        # player is centered at 68, 72 in pixel units
        # 68 -> player y, 72 -> player x
        # guess we want to attempt to map the pixels to player units or vice versa
        # Experimentally determined magic numbers below. Beware
        # visited_mask = np.zeros(VISITED_MASK_SHAPE, dtype=np.float32)
        visited_mask = np.zeros_like(game_pixels_render)
        """
        if self.taught_cut:
            cut_mask = np.zeros_like(game_pixels_render)
        else:
            cut_mask = np.random.randint(0, 255, game_pixels_render.shape, dtype=np.uint8)
        """
        # If not in battle, set the visited mask. There's no reason to process it when in battle
        scale = 2 if self.reduce_res else 1
        if self.read_m("wIsInBattle") == 0:
            '''
            for y in range(-72 // 16, 72 // 16):
                for x in range(-80 // 16, 80 // 16):
                    # y-y1 = m (x-x1)
                    # map [(0,0),(1,1)] -> [(0,.5),(1,1)] (cause we dont wnat it to be fully black)
                    # y = 1/2 x + .5
                    # current location tiles - player_y*8, player_x*8
                    """
                    visited_mask[y, x, 0] = self.seen_coords.get(
                        (
                            player_x + x + 1,
                            player_y + y + 1,
                            map_n,
                        ),
                        0.15,
                    )
                    """

                    visited_mask[
                        (16 * y + 76) // scale : (16 * y + 16 + 76) // scale,
                        (16 * x + 80) // scale : (16 * x + 16 + 80) // scale,
                        :,
                    ] = int(
                        self.seen_coords.get(
                            (
                                player_x + x + 1,
                                player_y + y + 1,
                                map_n,
                            ),
                            0,
                        )
                        * 255
                    )
                    """
                    if self.taught_cut:
                        cut_mask[
                            16 * y + 76 : 16 * y + 16 + 76,
                            16 * x + 80 : 16 * x + 16 + 80,
                            :,
                        ] = int(
                            255
                            * (
                                self.cut_coords.get(
                                    (
                                        player_x + x + 1,
                                        player_y + y + 1,
                                        map_n,
                                    ),
                                    0,
                                )
                            )
                        )
                        """
            '''
            gr, gc = local_to_global(player_y, player_x, map_n)
            visited_mask = (
                255
                * np.repeat(
                    np.repeat(self.explore_map[gr - 4 : gr + 6, gc - 4 : gc + 6], 16 // scale, 0),
                    16 // scale,
                    -1,
                )
            ).astype(np.uint8)[6 // scale : -10 // scale, :]
            visited_mask = np.expand_dims(visited_mask, -1)

        """
        import cv2
        cv2.imshow("a", game_pixels_render * visited_mask)
        cv2.waitKey(250)
        cv2.destroyAllWindows()
        """

        """
        global_map = np.expand_dims(
            255 * resize(self.explore_map, game_pixels_render.shape, anti_aliasing=False),
            axis=-1,
        ).astype(np.uint8)
        """
        if self.use_global_map:
            global_map = np.expand_dims(
                255 * self.explore_map,
                axis=-1,
            ).astype(np.uint8)

        return {
            "screen": game_pixels_render,
            "visited_mask": visited_mask,
        } | ({"global_map": global_map} if self.use_global_map else {})

    def _get_obs(self):
        # player_x, player_y, map_n = self.get_game_coords()
        _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
        bag = np.array(self.pyboy.memory[wBagItems : wBagItems + 40], dtype=np.uint8)
        numBagItems = self.read_m("wNumBagItems")
        # item ids start at 1 so using 0 as the nothing value is okay
        bag[2 * numBagItems :] = 0

        return (
            self.screen_obs()
            | {
                "direction": np.array(
                    self.read_m("wSpritePlayerStateData1FacingDirection") // 4, dtype=np.uint8
                ),
                "blackout_map_id": np.array(self.read_m("wLastBlackoutMap"), dtype=np.uint8),
                "battle_type": np.array(self.read_m("wIsInBattle") + 1, dtype=np.uint8),
                # "x": np.array(player_x, dtype=np.uint8),
                # "y": np.array(player_y, dtype=np.uint8),
                "map_id": np.array(self.read_m(0xD35E), dtype=np.uint8),
                "bag_items": bag[::2].copy(),
                "bag_quantity": bag[1::2].copy(),
                "species": np.array([self.party[i].Species for i in range(6)], dtype=np.uint8),
                "hp": np.array([self.party[i].HP for i in range(6)], dtype=np.uint32),
                "status": np.array([self.party[i].Status for i in range(6)], dtype=np.uint8),
                "type1": np.array([self.party[i].Type1 for i in range(6)], dtype=np.uint8),
                "type2": np.array([self.party[i].Type2 for i in range(6)], dtype=np.uint8),
                "level": np.array([self.party[i].Level for i in range(6)], dtype=np.uint8),
                "maxHP": np.array([self.party[i].MaxHP for i in range(6)], dtype=np.uint32),
                "attack": np.array([self.party[i].Attack for i in range(6)], dtype=np.uint32),
                "defense": np.array([self.party[i].Defense for i in range(6)], dtype=np.uint32),
                "speed": np.array([self.party[i].Speed for i in range(6)], dtype=np.uint32),
                "special": np.array([self.party[i].Special for i in range(6)], dtype=np.uint32),
                "moves": np.array([self.party[i].Moves for i in range(6)], dtype=np.uint8),
                "events": np.array(self.events.asbytes, dtype=np.uint8),
                "rival_3": np.array(
                    self.read_m("wSSAnne2FCurScript") == 4, dtype=np.uint8
                ),  # rival 3
                "game_corner_rocket": np.array(
                    self.missables.get_missable("HS_GAME_CORNER_ROCKET"), np.uint8
                ),  # game corner rocket
                "saffron_guard": np.array(
                    self.flags.get_bit("BIT_GAVE_SAFFRON_GUARDS_DRINK"), np.uint8
                ),  # saffron guard
                "lapras": np.array(self.flags.get_bit("BIT_GOT_LAPRAS"), np.uint8),  # got lapras
                "minimap_warp_obs": self.get_minimap_warp_obs(),  # minimap warp observations
            }
            | (
                {}
                if self.skip_safari_zone
                else {
                    "safari_steps": np.array(self.read_short("wSafariSteps"), dtype=np.uint32),
                }
            )
        )

    def set_perfect_iv_dvs(self):
        party_size = self.read_m("wPartyCount")
        for i in range(party_size):
            _, addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Species")
            self.pyboy.memory[addr + 17 : addr + 17 + 12] = 0xFF

    def check_if_party_has_hm(self, hm: int) -> bool:
        party_size = self.read_m("wPartyCount")
        for i in range(party_size):
            # PRET 1-indexes
            _, addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Moves")
            if hm in self.pyboy.memory[addr : addr + 4]:
                return True
        return False

    def is_next_to_warp_tile(self):
        if self.read_m("wIsInBattle") != 0 or self.read_dialog() != "":
            return False
        
        cur = self.get_game_coords()
        if cur is None:
            return False

        cur_map = self.get_game_coords()[2]
        prev_map = getattr(self, 'prev_map_id', None)
        local = self.get_game_coords()[:2]
        if not local:
            return False

        # Warp tile caching for this map
        info = self._warp_info_cache.get(cur_map)
        if info is None:
            entries = WARP_DICT.get(MapIds(cur_map).name, [])
            warp_tiles = [(e.get('x'), e.get('y')) for e in entries if e.get('x') is not None and e.get('y') is not None]
            # classify door warp: adjacent warp tiles
            door_flag = False
            for a, b in itertools.combinations(warp_tiles, 2):
                if self.navigator._manhattan(a, b) == 1:
                    door_flag = True
                    break
            info = {'warp_tiles': warp_tiles, 'door_warp': door_flag}
            self._warp_info_cache[cur_map] = info
            self._new_warp_info[cur_map] = info
        warp_tiles = info['warp_tiles']
        self.door_warp = info['door_warp']
        
        # Only log warp details when they change AND when an action is taken
        action_taken = hasattr(self, 'step_count') and hasattr(self, '_last_logged_step') and getattr(self, 'step_count', 0) != getattr(self, '_last_logged_step', -1)
        if action_taken and (not hasattr(self, '_last_logged_warp_info') or self._last_logged_warp_info != (cur_map, len(warp_tiles), self.door_warp)):
            print(f"environment.py: is_next_to_warp_tile(): Map {cur_map} - {len(warp_tiles)} warp tiles, door_warp: {self.door_warp}")
            self._last_logged_warp_info = (cur_map, len(warp_tiles), self.door_warp)
            self._last_logged_step = getattr(self, 'step_count', 0)
        
        # now that we have all the warp tiles for this map:
        # for each warp tile, check if the player is 1 tile away from it.
        # we only want to do things if player is 1 tile away from a warp tile.
        # moving onto a single tile warp triggers the warp immediately.
        if not self.door_warp:
            for warp_tile in warp_tiles:
                if self.navigator._manhattan(local, warp_tile) == 1:
                    self.on_a_warp_tile = False
                    return True
                elif self.navigator._manhattan(local, warp_tile) == 0:
                    self.on_a_warp_tile = True
                    return False
        elif self.door_warp:
            for warp_tile in warp_tiles:
                if self.navigator._manhattan(local, warp_tile) == 2:
                    self.on_a_warp_tile = False
                    return True
                elif self.navigator._manhattan(local, warp_tile) == 0:
                    self.on_a_warp_tile = True
                    return False
        return False

    @property
    def is_warping(self):
        if self._is_warping is None:
            hdst_map = self.read_m(0xFF8B)
            if self.read_bit(0xd736, 2) == 1:
                self._is_warping = hdst_map == 255 or self.read_m(0xd35e) == hdst_map
            elif self.read_m(0xcd5b) == 1:
                self._is_warping = True
            else:
                x, y = self.get_game_coords()[:2]
                n_warps = self.read_m(0xd3ae)  # wNumberOfWarps
                for i in range(n_warps):
                    warp_addr = 0xd3af + i * 4
                    if self.read_m(warp_addr + 0) == y and self.read_m(warp_addr + 1) == x:
                        self._is_warping = hdst_map == 255 or self.read_m(0xD35E) == hdst_map
                        break
            # self._is_warping = self.read_bit(0xd736, 2) == 1 and self.read_m(0xFF8B) == self.read_m(0xD35E)
        return self._is_warping

    def get_warp_debug_info(self) -> dict:
        """Get detailed warp debug information for UI display"""
        try:
            cur_map = self.read_m("wCurMap")
            
            debug_info = {
                "current_map": cur_map,
                "map_name": MapIds(cur_map).name if cur_map < 248 else "INVALID",
                "warp_dict_entries": [],
                "memory_warps": [],
                "is_warping": getattr(self, '_is_warping', None),
                "cache_status": {
                    "is_warping_cached": self._is_warping is not None,
                    "minimap_cached": self._minimap_warp_obs is not None
                }
            }
            
            # Get WARP_DICT entries
            try:
                map_name = MapIds(cur_map).name
                warp_entries = WARP_DICT.get(map_name, [])
                for i, warp in enumerate(warp_entries):
                    debug_info["warp_dict_entries"].append({
                        "index": i,
                        "x": warp.get("x"),
                        "y": warp.get("y"),
                        "target_map_id": warp.get("target_map_id"),
                        "target_map_name": warp.get("target_map_name", "")
                    })
            except Exception as e:
                debug_info["warp_dict_error"] = str(e)
            
            # Get memory warps
            try:
                n_warps = self.read_m(0xD3AE)
                debug_info["n_warps_memory"] = n_warps
                
                if n_warps > 0:
                    warp_entries_addr = 0xD3B1
                    for i in range(min(n_warps, 16)):  # Safety limit
                        warp_addr = warp_entries_addr + i * 4
                        warp_y = self.read_m(warp_addr + 0)
                        warp_x = self.read_m(warp_addr + 1)
                        warp_point = self.read_m(warp_addr + 2)
                        warp_dest = self.read_m(warp_addr + 3)
                        
                        debug_info["memory_warps"].append({
                            "index": i,
                            "x": warp_x,
                            "y": warp_y,
                            "warp_point": warp_point,
                            "dest_map": warp_dest
                        })
            except Exception as e:
                debug_info["memory_warp_error"] = str(e)
                
            return debug_info
            
        except Exception as e:
            return {"error": str(e)}

    def get_minimap_warp_obs(self) -> np.ndarray:
        """Generate a 9x10 minimap showing warp points visible on screen with unique IDs"""
        
        # Use cached result if available
        if self._minimap_warp_obs is not None:
            return self._minimap_warp_obs
            
        # Initialize minimap (9x10 to match downsampled game area)
        minimap = np.zeros((9, 10), dtype=np.uint16)
        
        try:
            # Get current map information
            cur_map = self.read_m("wCurMap")
            
            # Handle invalid maps gracefully
            if cur_map >= 248:
                self._minimap_warp_obs = minimap
                return minimap
            
            # Try to get warps from WARP_DICT first (preferred method)
            try:
                map_name = MapIds(cur_map).name
                warp_entries = WARP_DICT.get(map_name, [])
                
                for i, warp in enumerate(warp_entries):
                    warp_x = warp.get("x")
                    warp_y = warp.get("y")
                    target_map_id = warp.get("target_map_id", 0)
                    
                    if warp_x is not None and warp_y is not None:
                        # Convert local coordinates to minimap coordinates
                        # Game screen shows 20x18 tiles, minimap is 10x9 (downsampled 2:1)
                        minimap_x = min(warp_x // 2, 9)
                        minimap_y = min(warp_y // 2, 8)
                        
                        # Generate unique warp ID using hash
                        warp_id = hash((cur_map, warp_x, warp_y, target_map_id)) % 829 + 1
                        minimap[minimap_y, minimap_x] = warp_id
                        
            except Exception as dict_error:
                # Fallback: Read warps from memory (only log critical errors)
                try:
                    n_warps = self.read_m(0xD3AE)  # wNumberOfWarps
                    
                    if n_warps > 0:
                        warp_entries_addr = 0xD3B1  # wWarpEntries
                        for i in range(min(n_warps, 50)):  # Safety limit
                            warp_addr = warp_entries_addr + i * 4
                            warp_y = self.read_m(warp_addr + 0)
                            warp_x = self.read_m(warp_addr + 1)
                            warp_point = self.read_m(warp_addr + 2)
                            warp_dest = self.read_m(warp_addr + 3)
                            
                            # Convert to minimap coordinates
                            minimap_x = min(warp_x // 2, 9)
                            minimap_y = min(warp_y // 2, 8)
                            
                            # Generate unique warp ID
                            warp_id = hash((cur_map, warp_x, warp_y, warp_dest)) % 829 + 1
                            minimap[minimap_y, minimap_x] = warp_id
                            
                except Exception as memory_error:
                    # Only log if we can't get any warp data at all
                    if np.count_nonzero(minimap) == 0:
                        print(f"MINIMAP WARP: Both WARP_DICT and memory fallback failed: dict_error={dict_error}, memory_error={memory_error}")
                    
        except Exception as e:
            print(f"MINIMAP WARP: Critical error generating minimap: {e}")
            # Return empty minimap on any error
            minimap = np.zeros((9, 10), dtype=np.uint16)
        
        # Cache the result
        self._minimap_warp_obs = minimap
        return minimap

    def clear_warp_cache(self) -> None:
        """Clear warp-related cache to ensure fresh collision detection"""
        self._is_warping = None
        self._minimap_warp_obs = None

    
    def update_map_history(self) -> None:        
        if self.map_history[-1] != self.read_m("wCurMap"):
            self.map_history.append(self.read_m("wCurMap"))
            # Only log map history changes when an action is taken (during step execution)
            action_taken = hasattr(self, 'step_count') and hasattr(self, '_last_map_history_logged_step') and getattr(self, 'step_count', 0) != getattr(self, '_last_map_history_logged_step', -1)
            if action_taken:
                print(f"environment.py: update_map_history(): MAP_TRACKING: Updated map_history: {list(self.map_history)}")
                self._last_map_history_logged_step = getattr(self, 'step_count', 0)
   
    def step(self, action):
        self.step_count += 1
        
        self.handle_oak_dialog()
        
        # ANTI-SPAM LOGGING: Only log when values actually change or significant events occur
        # This prevents console spam while preserving important debugging information
        
        # Only log step start if action is different or significant
        current_location = self.get_game_coords()
        print(f"\n\n\n\nenvironment.py: step(): START OF STEP {self.step_count}; location: {self.get_game_coords()}")

        if action != self.prev_logged_action or action == PATH_FOLLOW_ACTION:
            print(f"\n=== STEP {self.step_count}: ACTION {action} START ===")
            self.prev_logged_action = action
        else:
            print(f"STEP {self.step_count}: ACTION {action} CONTINUES")
            
        if action == PATH_FOLLOW_ACTION:
            print(f"STEP {self.step_count}: PATH_FOLLOW_ACTION detected - will convert to movement action")
            
        # Only log location if it has changed
        if self.prev_logged_location == None:
            self.prev_logged_location = current_location
        if current_location != self.prev_logged_location:
            print(f"STEP {self.step_count}: Location changed to: {current_location}")
            self.prev_logged_location = current_location
        
        # # COMPLETELY DISABLED: All warp detection and blocking
        # self.clear_warp_cache()
        # is_warping_result = self.is_warping
        # if is_warping_result:
        #     print(f"STEP {self.step_count}: WARP DETECTED - Skipping action {action}")
        #     return self._get_obs(), 0.0, False, False, {"warp_skip": "true", "reason": "warp_transition"}
        
        dialog = self.read_dialog() or ''

        reset = False # Initialize reset here
        
        # COMPLETELY DISABLED: Dialog detection blocking
        # raw_dialog = self.read_dialog() or ''
        # if raw_dialog.strip() and action == PATH_FOLLOW_ACTION:
        #     print("environment.py: step(): Navigation paused: dialog active, cannot move to next coordinate.")
        #     return self._get_obs(), 0.0, reset, False, {}

        if self.save_video and self.step_count == 0:
            self.start_video()

        _, wMapPalOffset = self.pyboy.symbol_lookup("wMapPalOffset")
        if self.auto_flash and self.pyboy.memory[wMapPalOffset] == 6:
            self.pyboy.memory[wMapPalOffset] = 0

        if self.auto_remove_all_nonuseful_items:
            self.remove_all_nonuseful_items()

        # Infinite money: set to maximum ($999999) in BCD
        _, wPlayerMoney = self.pyboy.symbol_lookup("wPlayerMoney")
        if self.infinite_money:
            for offset in range(3):
                self.pyboy.memory[wPlayerMoney + offset] = 0x99

        if (
            self.disable_wild_encounters
            and MapIds(self.read_m("wCurMap")).name not in self.disable_wild_encounters_maps
        ):
            self.pyboy.memory[self.pyboy.symbol_lookup("wRepelRemainingSteps")[1]] = 0xFF

        self.update_safari_zone()

        self.item_handler.scripted_buy_items()
        self.check_num_bag_items()
        
        # UNIFIED ARCHITECTURE: Convert PATH_FOLLOW_ACTION to movement action BEFORE calling run_action_on_emulator
        # This ensures everything goes through the same action execution path
        final_action = action
        
        # Pre-conversion sync: ensure navigator index is up-to-date before path-follow conversion
        if action == PATH_FOLLOW_ACTION and hasattr(self, 'navigator') and self.navigator:
            try:
                self.navigator.snap_to_nearest_coordinate()
                print(f"environment.py: pre-conversion snap at step {self.step_count}; location: {self.get_game_coords()}")
            except Exception as e:
                print(f"environment.py: pre-conversion snap error: {e}")
        
        if action == PATH_FOLLOW_ACTION:
            print(f"🎯🎯🎯 STEP {self.step_count}: PATH_FOLLOW_ACTION DETECTED! 🎯🎯🎯")
            
            # Get current player status for debugging
            x, y, map_id = self.get_game_coords()
            print(f"🎯 Current player: local=({x}, {y}), map={map_id}")
            
            # Check quest system status
            if hasattr(self, 'quest_manager'):
                current_quest = self.quest_manager.get_current_quest()
                print(f"🎯 Current quest from quest_manager: {current_quest}")
            else:
                print(f"🎯 NO QUEST MANAGER!")
                current_quest = None
            
            # Check navigator status
            if hasattr(self, 'navigator') and self.navigator:
                print(f"🎯 Navigator active_quest_id: {self.navigator.active_quest_id}")
                print(f"🎯 Navigator coords loaded: {len(self.navigator.sequential_coordinates)}")
                print(f"🎯 Navigator current_index: {self.navigator.current_coordinate_index}")
                
                # FORCE QUEST LOADING IF NEEDED
                if current_quest and (not self.navigator.sequential_coordinates or self.navigator.active_quest_id != current_quest):
                    print(f"🎯 FORCE LOADING quest {current_quest} into navigator")
                    success = self.navigator.load_coordinate_path(current_quest)
                    print(f"🎯 Force load result: {success}")
                
                print(f"🎯 Converting PATH_FOLLOW_ACTION via ConsolidatedNavigator")
                converted_action = self.navigator.convert_path_follow_to_movement_action(PATH_FOLLOW_ACTION)
                if converted_action is not None and converted_action != PATH_FOLLOW_ACTION:
                    final_action = converted_action
                    print(f"🎯 PATH_FOLLOW_ACTION converted to movement action {final_action}")
                else:
                    # raise Exception("🎯 environment.py: step(): PATH_FOLLOW_ACTION conversion failed, using fallback UP action")
                    print(f"🎯 PATH_FOLLOW_ACTION conversion failed, using fallback B action")
                    final_action = 5  # B action as fallback
            else:
                raise Exception("🎯 environment.py: step(): RAISE ERROR: NO CONSOLIDATED NAVIGATOR AVAILABLE - using fallback B action")
                print(f"🎯 NO CONSOLIDATED NAVIGATOR AVAILABLE - using fallback B action")
                final_action = 5  # B action as fallback
        
        # Ensure StageManager state is up-to-date before scripted movement overrides
        if hasattr(self, 'stage_manager') and hasattr(self.stage_manager, 'update_stage_manager'):
            try:
                self.stage_manager.update_stage_manager()
            except Exception as e:
                print(f"environment.py: step(): StageManager update error: {e}")

        # Apply stage-specific scripted movement overrides using the original action
        if hasattr(self, 'stage_manager') and hasattr(self.stage_manager, 'scripted_stage_movement'):
            overridden = self.stage_manager.scripted_stage_movement(action)
            if overridden != final_action:
                final_action = overridden
                print(f"environment.py: step(): StageManager.scripted_stage_movement override to {final_action}")

        # SAFETY GUARD: Never pass a None action to the emulator. If the navigation
        # logic (e.g., end-of-path in ConsolidatedNavigator) returns `None` we
        # substitute the configured *noop* button so the game still advances one
        # frame and downstream systems (StageManager, quest triggers, etc.) keep
        # running.
        if final_action is None:
            final_action = getattr(self, 'noop_button_index', 4)
            print(f"environment.py: step(): final_action was None – substituting noop action {final_action}")

        print(f"environment.py: step(): step number is: {self.step_count} ACTION {final_action} running on emulator")
        self.run_action_on_emulator(final_action)
        
        
        # Continue with all the normal game state updates that must happen after every action
        self.events = EventFlags(self.pyboy)
        self.missables = MissableFlags(self.pyboy)
        self.flags = Flags(self.pyboy)
        self.party = PartyMons(self.pyboy)
        self.update_health()
        self.update_pokedex()
        self.update_tm_hm_obtained_move_ids()
        self.party_size = self.read_m("wPartyCount")
        self.update_max_op_level()

        self.last_health = self.read_hp_fraction()
        self.update_map_progress()
        if self.perfect_ivs:
            self.set_perfect_iv_dvs()
        self.taught_cut = self.check_if_party_has_hm(TmHmMoves.CUT.value)
        self.taught_surf = self.check_if_party_has_hm(TmHmMoves.SURF.value)
        self.taught_strength = self.check_if_party_has_hm(TmHmMoves.STRENGTH.value)
        self.pokecenters[self.read_m("wLastBlackoutMap")] = 1
        if self.read_m("wWalkBikeSurfState") == 0x2:
            self.use_surf = 1
        if self.infinite_health:
            self.reverse_damage()
            # Refresh party after resetting HP so obs reflects the change
            self.party = PartyMons(self.pyboy)

        info = {}

        required_events = self.get_required_events()
        required_items = self.get_required_items()
        new_required_events = required_events - self.required_events
        new_required_items = required_items - self.required_items
        if self.save_state and (new_required_events or new_required_items):
            state = io.BytesIO()
            self.pyboy.save_state(state)
            state.seek(0)
            info["state"] = {
                tuple(sorted(list(required_events) + list(required_items))): state.read()
            }
            info["required_count"] = len(required_events) + len(required_items)
            info["env_id"] = self.env_id
            info = info | self.agent_stats(final_action)
        elif (
            self.step_count != 0
            and self.log_frequency
            and self.step_count % self.log_frequency == 0
        ):
            info = info | self.agent_stats(final_action)

        self.required_events = required_events
        self.required_items = required_items

        # REMOVED: Old prev_map_id tracking - now using centralized self.map_history

        # FIXED: Add step count to info for external tracking
        info["step_count"] = self.step_count
        info["current_quest_id"] = getattr(self, 'current_loaded_quest_id', None)
        info["path_length"] = len(getattr(self, 'combined_path', []))
        info["path_index"] = getattr(self, 'current_path_target_index', 0)

        obs = self._get_obs()
        reward = 0

        # Log important game state changes (only when they actually change)
        if self.logger:
            try:
                # Log significant events
                if reward > 0:
                    self.logger.log_environment_event("REWARD_GAINED", {
                        'message': f'Reward gained: {reward}',
                        'reward': reward,
                        'total_reward': getattr(self, 'total_reward', 0) + reward,
                        'step_count': self.step_count
                    })
                
                # Log battle events only when battle state changes
                current_battle_state = self.read_m("wIsInBattle") != 0
                if current_battle_state != self.prev_logged_battle_state:
                    self.logger.log_environment_event("BATTLE_STATE", {
                        'message': 'Entered battle' if current_battle_state else 'Exited battle or battle status changed',
                        'battle_active': current_battle_state,
                        'step_count': self.step_count
                    })
                    self.prev_logged_battle_state = current_battle_state
                    if current_battle_state:
                        # Automatically generate a battle prompt for Grok when battle starts
                        info["battle_prompt"] = self.format_battle_state()
        
                # Log dialog events only when dialog changes
                dialog = self.read_dialog() or ''
                if dialog.strip():
                    self.logger.log_environment_event("DIALOG_ACTIVE", {
                        'message': f'Dialog detected: {dialog[:50]}...' if len(dialog) > 50 else f'Dialog: {dialog}',
                        'dialog_text': dialog,
                        'step_count': self.step_count
                    })
                    
            except Exception as e:
                import traceback; traceback.print_exc()

        # Note: step_count was already incremented at the beginning of step method
        # print(f"environment.py: step(): self.step_count=={self.step_count}\n")

        # Trigger debug: dialog, inventory, battle flags
        dialog = self.read_dialog() or ''
        self.last_dialog = dialog
        debug_print(f"[TriggerTest] dialog_contains_text: {dialog}")
        bag_items = list(self.get_items_in_bag())
        debug_print(f"[TriggerTest] item_is_in_inventory: {[item.name for item in bag_items]}")
        # Show completed and pending events in order
        completed_events = [evt for evt in REQUIRED_EVENTS if self.events.get_event(evt)]
        pending_events = [evt for evt in REQUIRED_EVENTS if not self.events.get_event(evt)]
        debug_print(f"[TriggerTest] Events - Completed: {len(completed_events)}, Pending: {len(pending_events)}")
        # REMOVED: Old map tracking system replaced with centralized self.map_history
        # prev_map_id is now always calculated from self.map_history[-2] where needed
        
        # REMOVED DUPLICATE: run_action_on_emulator() was already called at line 1825
        # The duplicate call was causing warp activation failures
        
        print(f"environment.py: step(): infinite_pp_and_move_hack: {self.infinite_pp_and_move_hack}")
        if self.infinite_pp_and_move_hack:
            # Dynamic STAB selection, infinite PP, and buff stats
            # Map Pokemon type codes to strongest STAB move IDs
            TYPE_TO_MOVE = {
                PokemonType.FIGHTING.value: Move.HI_JUMP_KICK.value,
                PokemonType.FLYING.value: Move.SKY_ATTACK.value,
                PokemonType.GROUND.value: Move.EARTHQUAKE.value,
                PokemonType.ROCK.value: Move.ROCK_SLIDE.value,
                PokemonType.FIRE.value: Move.FIRE_BLAST.value,
                PokemonType.WATER.value: Move.HYDRO_PUMP.value,
                PokemonType.GRASS.value: Move.SOLARBEAM.value,
                PokemonType.POISON.value: Move.SLUDGE.value,
                PokemonType.ELECTRIC.value: Move.THUNDERBOLT.value,
                PokemonType.PSYCHIC.value: Move.PSYCHIC_M.value,
                PokemonType.ICE.value: Move.BLIZZARD.value,
                PokemonType.NORMAL.value: Move.EXPLOSION.value,
            }
            for i in range(self.read_m("wPartyCount")):
                # Read Pokemon types
                _, t1_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Type1")
                type1 = self.pyboy.memory[t1_addr]
                _, t2_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Type2")
                type2 = self.pyboy.memory[t2_addr]
                move_id = TYPE_TO_MOVE.get(type1) or TYPE_TO_MOVE.get(type2) or TmHmMoves.HYPER_BEAM.value
                # Set STAB move in slot 1
                _, moves_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Moves")
                self.pyboy.memory[moves_addr] = move_id
                # Replenish PP for all slots
                _, pp_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}PP")
                for slot in range(4):
                    self.pyboy.memory[pp_addr + slot] = 0x3F
                # Buff stats: Attack, Speed, Special
                for stat in ["Attack", "Speed", "Special"]:
                    _, stat_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}{stat}")
                    self.pyboy.memory[stat_addr] = 0xFF
                    self.pyboy.memory[stat_addr+1] = 0xFF
        else:
            # Ensure Charmander (species 4) keeps its default Scratch move if it was overwritten
            if self.read_m("wPartyCount") > 0:
                print(f"environment.py: step(): Restoring Charmander's Scratch move and PP")
                species = self.read_m("wPartyMon1Species")
                print(f"environment.py: step(): Species: {species}")
                if species == 176:  # Charmander species ID
                    _, moves_addr = self.pyboy.symbol_lookup("wPartyMon1Moves")
                    print(f"environment.py: step(): Moves address: {moves_addr}")
                    self.pyboy.memory[moves_addr] = Move.SCRATCH.value
                    # Restore PP to 35 (0x23)
                    _, pp_addr = self.pyboy.symbol_lookup("wPartyMon1PP")
                    print(f"environment.py: step(): PP address: {pp_addr}")
                    self.pyboy.memory[pp_addr] = 0x23

        self.current_dialog_lines = self.get_active_dialog()
        done = False
        truncated = False
        # Only log step completion for significant actions or when something important happened
        if action == PATH_FOLLOW_ACTION or action != self.prev_logged_action or reward > 0:
            print(f"=== STEP {self.step_count}: ACTION {action} (final: {final_action}) COMPLETE ===\n")
        
        self.update_map_history()
        print(f"environment.py: step(): END OF STEP {self.step_count}; location: {self.get_game_coords()}\n\n\n\n")

        # Print collision map to terminal for debugging formatting
        collision_map_markdown = self.get_collision_map_markdown()
        print(collision_map_markdown)

        # Update StageManager every frame to allow stage transitions and cleanup of scripted rules
        if hasattr(self, 'stage_manager') and hasattr(self.stage_manager, 'update_stage_manager'):
            try:
                self.stage_manager.update_stage_manager()
            except Exception as e:
                print(f"environment.py: step(): StageManager update error: {e}")

        return obs, reward, done, truncated, info



    def run_action_on_emulator(self, action):
        # PATH_FOLLOW_ACTION should never reach here directly - it's handled in step()
        # When navigator calls this with directional actions (0-3), those should execute normally
        print(f"environment.py: run_action_on_emulator(): TOP OF run_action_on_emulator() - step number {self.step_count} - RUNNING ACTION {action} ON EMULATOR")
        
        if action == PATH_FOLLOW_ACTION:
            print(f"*** WARNING: PATH_FOLLOW_ACTION reached run_action_on_emulator - this should be handled in step() ***")
            return
        
        # Validate action is in valid range
        # Handle when actions are NoneType
        try:
            if action < 0 or action >= len(VALID_ACTIONS) or VALID_ACTIONS[action] is None:
                print(f"ERROR: Invalid action {action} passed to run_action_on_emulator")
                return
        except Exception as e:
            print(f"ERROR: Invalid action {action} passed to run_action_on_emulator")
            return
        
        self.action_hist[action] += 1
        # press button then release after some steps
        # TODO: Add video saving logic

        # Send input to emulator
        self.pyboy.send_input(VALID_ACTIONS[action])
        self.pyboy.send_input(VALID_RELEASE_ACTIONS[action], delay=self.emulator_delay)
        self.pyboy.tick(self.action_freq - 1, render=False)

        # TODO: Split this function up. update_seen_coords should not be here!
        self.update_seen_coords()

        # DO NOT DELETE. Some animations require dialog navigation
        for _ in range(1000):
            if not self.read_m("wJoyIgnore"):
                break
            self.pyboy.button("a", 8)
            self.pyboy.tick(self.action_freq, render=True)

        if self.events.get_event("EVENT_GOT_HM01"):
            if self.auto_teach_cut and not self.check_if_party_has_hm(TmHmMoves.CUT.value):
                self.teach_hm(TmHmMoves.CUT.value, 30, CUT_SPECIES_IDS)
            if self.auto_use_cut:
                self.cut_if_next()

        if self.events.get_event("EVENT_GOT_HM03"):
            if self.auto_teach_surf and not self.check_if_party_has_hm(TmHmMoves.SURF.value):
                self.teach_hm(TmHmMoves.SURF.value, 15, SURF_SPECIES_IDS)
            if self.auto_use_surf:
                self.surf_if_attempt(VALID_ACTIONS[action])

        if self.events.get_event("EVENT_GOT_HM04"):
            if self.auto_teach_strength and not self.check_if_party_has_hm(
                TmHmMoves.STRENGTH.value
            ):
                self.teach_hm(TmHmMoves.STRENGTH.value, 15, STRENGTH_SPECIES_IDS)
            if self.auto_solve_strength_puzzles:
                self.solve_strength_puzzle()
            if not self.check_if_party_has_hm(TmHmMoves.STRENGTH.value) and self.auto_use_strength:
                self.use_strength()

        if self.events.get_event("EVENT_GOT_POKE_FLUTE") and self.auto_pokeflute:
            self.use_pokeflute()

        if self.get_game_coords() == (18, 4, 7) and self.skip_safari_zone:
            self.skip_safari_zone_atn()

        if self.auto_next_elevator_floor:
            self.next_elevator_floor()

        if self.insert_saffron_guard_drinks:
            self.insert_guard_drinks()

        # One last tick just in case
        self.pyboy.tick(1, render=True)
        print(f"environment.py: run_action_on_emulator(): BOTTOM OF run_action_on_emulator() - step number {self.step_count} - ACTION {action} COMPLETE")

    def party_has_cut_capable_mon(self):
        # find bulba and replace tackle (first skill) with cut
        party_size = self.read_m("wPartyCount")
        for i in range(party_size):
            # PRET 1-indexes
            _, species_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Species")
            poke = self.pyboy.memory[species_addr]
            # https://github.com/pret/pokered/blob/d38cf5281a902b4bd167a46a7c9fd9db436484a7/constants/pokemon_constants.asm
            if poke in CUT_SPECIES_IDS:
                return True
        return False

    def teach_hm(self, tmhm: int, pp: int, pokemon_species_ids):
        # find bulba and replace tackle (first skill) with cut
        party_size = self.read_m("wPartyCount")
        for i in range(party_size):
            # PRET 1-indexes
            # https://github.com/pret/pokered/blob/d38cf5281a902b4bd167a46a7c9fd9db436484a7/constants/pokemon_constants.asm
            if self.party[i].Species in pokemon_species_ids:
                _, move_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Moves")
                _, pp_addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}PP")
                for slot in range(4):
                    if self.party[i].Moves[slot] not in {
                        TmHmMoves.CUT.value,
                        TmHmMoves.FLY.value,
                        TmHmMoves.SURF.value,
                        TmHmMoves.STRENGTH.value,
                        TmHmMoves.FLASH.value,
                    }:
                        self.pyboy.memory[move_addr + slot] = tmhm
                        self.pyboy.memory[pp_addr + slot] = pp
                        # fill up pp: 30/30
                        break

    def use_pokeflute(self):
        in_overworld = self.read_m("wCurMapTileset") == Tilesets.OVERWORLD.value
        # not in battle
        _, _, map_id = self.get_game_coords()
        if (
            in_overworld
            and self.read_m(0xD057) == 0
            and map_id in (MapIds.ROUTE_12.value, MapIds.ROUTE_16.value)
            and not (
                self.events.get_event("EVENT_BEAT_ROUTE12_SNORLAX")
                and map_id == MapIds.ROUTE_12.value
            )
            and not (
                self.events.get_event("EVENT_BEAT_ROUTE16_SNORLAX")
                and map_id == MapIds.ROUTE_16.value
            )
        ):
            _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
            bag_items = self.pyboy.memory[wBagItems : wBagItems + 40]
            if Items.POKE_FLUTE.value not in bag_items[::2]:
                return
            pokeflute_index = bag_items[::2].index(Items.POKE_FLUTE.value)

            # Check if we're on the snorlax coordinates

            coords = self.get_game_coords()
            if coords == (9, 62, 23):
                self.pyboy.button("RIGHT", 8)
                self.pyboy.tick(self.action_freq, render=True)
            elif coords == (10, 63, 23):
                self.pyboy.button("UP", 8)
                self.pyboy.tick(self.action_freq, render=True)
            elif coords == (10, 61, 23):
                self.pyboy.button("DOWN", 8)
                self.pyboy.tick(self.action_freq, render=True)
            elif coords == (27, 10, 27):
                self.pyboy.button("LEFT", 8)
                self.pyboy.tick(self.action_freq, render=True)
            elif coords == (27, 10, 25):
                self.pyboy.button("RIGHT", 8)
                self.pyboy.tick(self.action_freq, render=True)
            else:
                return
            # Then check if snorlax is a missable object
            # Then trigger snorlax

            _, wMissableObjectFlags = self.pyboy.symbol_lookup("wMissableObjectFlags")
            _, wMissableObjectList = self.pyboy.symbol_lookup("wMissableObjectList")
            missable_objects_list = self.pyboy.memory[
                wMissableObjectList : wMissableObjectList + 34
            ]
            missable_objects_list = missable_objects_list[: missable_objects_list.index(0xFF)]
            missable_objects_sprite_ids = missable_objects_list[::2]
            missable_objects_flags = missable_objects_list[1::2]
            for sprite_id in missable_objects_sprite_ids:
                picture_id = self.read_m(f"wSprite{sprite_id:02}StateData1PictureID")
                flags_bit = missable_objects_flags[missable_objects_sprite_ids.index(sprite_id)]
                flags_byte = flags_bit // 8
                flag_bit = flags_bit % 8
                flag_byte_value = self.read_bit(wMissableObjectFlags + flags_byte, flag_bit)
                if picture_id == 0x43 and not flag_byte_value:
                    # open start menu
                    self.pyboy.button("START", 8)
                    self.pyboy.tick(self.action_freq, render=True)
                    # scroll to bag
                    # 2 is the item index for bag
                    for _ in range(24):
                        if self.read_m("wCurrentMenuItem") == 2:
                            break
                        self.pyboy.button("DOWN", 8)
                        self.pyboy.tick(self.action_freq, render=True)
                    self.pyboy.button("A", 8)
                    self.pyboy.tick(self.action_freq, render=True)

                    # Scroll until you get to pokeflute
                    # We'll do this by scrolling all the way up then all the way down
                    # There is a faster way to do it, but this is easier to think about
                    # Could also set the menu index manually, but there are like 4 variables
                    # for that
                    for _ in range(20):
                        self.pyboy.button("UP", 8)
                        self.pyboy.tick(self.action_freq, render=True)

                    for _ in range(21):
                        if (
                            self.read_m("wCurrentMenuItem") + self.read_m("wListScrollOffset")
                            == pokeflute_index
                        ):
                            break
                        self.pyboy.button("DOWN", 8)
                        self.pyboy.tick(self.action_freq, render=True)

                    # press a bunch of times
                    for _ in range(5):
                        self.pyboy.button("A", 8)
                        self.pyboy.tick(4 * self.action_freq, render=True)

                    break

    def cut_if_next(self):
        # https://github.com/pret/pokered/blob/d38cf5281a902b4bd167a46a7c9fd9db436484a7/constants/tileset_constants.asm#L11C8-L11C11
        in_erika_gym = self.read_m("wCurMapTileset") == Tilesets.GYM.value
        in_overworld = self.read_m("wCurMapTileset") == Tilesets.OVERWORLD.value
        if self.read_m(0xD057) == 0 and (in_erika_gym or in_overworld):
            _, wTileMap = self.pyboy.symbol_lookup("wTileMap")
            tileMap = self.pyboy.memory[wTileMap : wTileMap + 20 * 18]
            tileMap = np.array(tileMap, dtype=np.uint8)
            tileMap = np.reshape(tileMap, (18, 20))
            y, x = 8, 8
            up, down, left, right = (
                tileMap[y - 2 : y, x : x + 2],  # up
                tileMap[y + 2 : y + 4, x : x + 2],  # down
                tileMap[y : y + 2, x - 2 : x],  # left
                tileMap[y : y + 2, x + 2 : x + 4],  # right
            )

            # Gym trees apparently get the same tile map as outside bushes
            # GYM = 7
            if (in_overworld and 0x3D in up) or (in_erika_gym and 0x50 in up):
                self.pyboy.button("UP", delay=8)
                self.pyboy.tick(self.action_freq, render=True)
            elif (in_overworld and 0x3D in down) or (in_erika_gym and 0x50 in down):
                self.pyboy.button("DOWN", delay=8)
                self.pyboy.tick(self.action_freq, render=True)
            elif (in_overworld and 0x3D in left) or (in_erika_gym and 0x50 in left):
                self.pyboy.button("LEFT", delay=8)
                self.pyboy.tick(self.action_freq, render=True)
            elif (in_overworld and 0x3D in right) or (in_erika_gym and 0x50 in right):
                self.pyboy.button("RIGHT", delay=8)
                self.pyboy.tick(self.action_freq, render=True)
            else:
                return

            # open start menu
            self.pyboy.button("START", delay=8)
            self.pyboy.tick(self.action_freq, self.animate_scripts)
            # scroll to pokemon
            # 1 is the item index for pokemon
            for _ in range(24):
                if self.pyboy.memory[self.pyboy.symbol_lookup("wCurrentMenuItem")[1]] == 1:
                    break
                self.pyboy.button("DOWN", delay=8)
                self.pyboy.tick(self.action_freq, render=self.animate_scripts)
            self.pyboy.button("A", delay=8)
            self.pyboy.tick(self.action_freq, self.animate_scripts)

            # find pokemon with cut
            # We run this over all pokemon so we dont end up in an infinite for loop
            for _ in range(7):
                self.pyboy.button("DOWN", delay=8)
                self.pyboy.tick(self.action_freq, self.animate_scripts)
                party_mon = self.pyboy.memory[self.pyboy.symbol_lookup("wCurrentMenuItem")[1]]
                _, addr = self.pyboy.symbol_lookup(f"wPartyMon{party_mon%6+1}Moves")
                if 0xF in self.pyboy.memory[addr : addr + 4]:
                    break

            # Enter submenu
            self.pyboy.button("A", delay=8)
            self.pyboy.tick(4 * self.action_freq, self.animate_scripts)

            # Scroll until the field move is found
            _, wFieldMoves = self.pyboy.symbol_lookup("wFieldMoves")
            field_moves = self.pyboy.memory[wFieldMoves : wFieldMoves + 4]

            for _ in range(10):
                current_item = self.read_m("wCurrentMenuItem")
                if current_item < 4 and FieldMoves.CUT.value == field_moves[current_item]:
                    break
                self.pyboy.button("DOWN", delay=8)
                self.pyboy.tick(self.action_freq, self.animate_scripts)

            # press a bunch of times
            for _ in range(5):
                self.pyboy.button("A", delay=8)
                self.pyboy.tick(4 * self.action_freq, self.animate_scripts)

    def surf_if_attempt(self, action: WindowEvent):
        if (
            self.read_m("wIsInBattle") == 0
            and self.read_m("wWalkBikeSurfState") != 2
            and self.check_if_party_has_hm(TmHmMoves.SURF.value)
            and action
            in [
                WindowEvent.PRESS_ARROW_DOWN,
                WindowEvent.PRESS_ARROW_LEFT,
                WindowEvent.PRESS_ARROW_RIGHT,
                WindowEvent.PRESS_ARROW_UP,
            ]
            ):
            in_overworld = self.read_m("wCurMapTileset") == Tilesets.OVERWORLD.value
            in_plateau = self.read_m("wCurMapTileset") == Tilesets.PLATEAU.value
            in_cavern = self.read_m("wCurMapTileset") == Tilesets.CAVERN.value
            if (
                in_overworld
                or in_plateau
                or (in_cavern and self.get_game_coords() in SEAFOAM_SURF_SPOTS)
            ):
                _, wTileMap = self.pyboy.symbol_lookup("wTileMap")
                tileMap = self.pyboy.memory[wTileMap : wTileMap + 20 * 18]
                tileMap = np.array(tileMap, dtype=np.uint8)
                tileMap = np.reshape(tileMap, (18, 20))
                y, x = 8, 8
                # This could be made a little faster by only checking the
                # direction that matters, but I decided to copy pasta the cut routine
                up, down, left, right = (
                    tileMap[y - 2 : y, x : x + 2],  # up
                    tileMap[y + 2 : y + 4, x : x + 2],  # down
                    tileMap[y : y + 2, x - 2 : x],  # left
                    tileMap[y : y + 2, x + 2 : x + 4],  # right
                )

                # down, up, left, right
                direction = self.read_m("wSpritePlayerStateData1FacingDirection")

                if not (
                    (direction == 0x4 and action == WindowEvent.PRESS_ARROW_UP and 0x14 in up)
                    or (
                        direction == 0x0 and action == WindowEvent.PRESS_ARROW_DOWN and 0x14 in down
                    )
                    or (
                        direction == 0x8 and action == WindowEvent.PRESS_ARROW_LEFT and 0x14 in left
                    )
                    or (
                        direction == 0xC
                        and action == WindowEvent.PRESS_ARROW_RIGHT
                        and 0x14 in right
                    )
                ):
                    return

                # open start menu
                self.pyboy.send_input(WindowEvent.PRESS_BUTTON_START)
                self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_START, delay=8)
                self.pyboy.tick(self.action_freq, self.animate_scripts)
                # scroll to pokemon
                # 1 is the item index for pokemon
                for _ in range(24):
                    if self.pyboy.memory[self.pyboy.symbol_lookup("wCurrentMenuItem")[1]] == 1:
                        break
                    self.pyboy.send_input(WindowEvent.PRESS_ARROW_DOWN)
                    self.pyboy.send_input(WindowEvent.RELEASE_ARROW_DOWN, delay=8)
                    self.pyboy.tick(self.action_freq, self.animate_scripts)
                self.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
                self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A, delay=8)
                self.pyboy.tick(self.action_freq, self.animate_scripts)

                # find pokemon with surf
                # We run this over all pokemon so we dont end up in an infinite for loop
                for _ in range(7):
                    self.pyboy.send_input(WindowEvent.PRESS_ARROW_DOWN)
                    self.pyboy.send_input(WindowEvent.RELEASE_ARROW_DOWN, delay=8)
                    self.pyboy.tick(self.action_freq, self.animate_scripts)
                    party_mon = self.pyboy.memory[self.pyboy.symbol_lookup("wCurrentMenuItem")[1]]
                    _, addr = self.pyboy.symbol_lookup(f"wPartyMon{party_mon%6+1}Moves")
                    if 0x39 in self.pyboy.memory[addr : addr + 4]:
                        break

                # Enter submenu
                self.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
                self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A, delay=8)
                self.pyboy.tick(4 * self.action_freq, self.animate_scripts)

                # Scroll until the field move is found
                _, wFieldMoves = self.pyboy.symbol_lookup("wFieldMoves")
                field_moves = self.pyboy.memory[wFieldMoves : wFieldMoves + 4]

                for _ in range(10):
                    current_item = self.read_m("wCurrentMenuItem")
                    if current_item < 4 and field_moves[current_item] in (
                        FieldMoves.SURF.value,
                        FieldMoves.SURF_2.value,
                    ):
                        break
                    self.pyboy.send_input(WindowEvent.PRESS_ARROW_DOWN)
                    self.pyboy.send_input(WindowEvent.RELEASE_ARROW_DOWN, delay=8)
                    self.pyboy.tick(self.action_freq, self.animate_scripts)

                # press a bunch of times
                for _ in range(5):
                    self.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
                    self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A, delay=8)
                    self.pyboy.tick(4 * self.action_freq, self.animate_scripts)

    def solve_strength_puzzle(self):
        in_cavern = self.read_m("wCurMapTileset") == Tilesets.CAVERN.value
        if self.read_m(0xD057) == 0 and in_cavern:
            for sprite_id in range(1, self.read_m("wNumSprites") + 1):
                picture_id = self.read_m(f"wSprite{sprite_id:02}StateData1PictureID")
                mapY = self.read_m(f"wSprite{sprite_id:02}StateData2MapY")
                mapX = self.read_m(f"wSprite{sprite_id:02}StateData2MapX")
                if solution := STRENGTH_SOLUTIONS.get(
                    (picture_id, mapY, mapX) + self.get_game_coords(), None
                ):
                    missable, steps = solution
                    if missable and self.missables.get_missable(missable):
                        break
                    if not self.disable_wild_encounters:
                        self.setup_disable_wild_encounters()
                    # Activate strength
                    self.flags.set_bit("BIT_STRENGTH_ACTIVE", 1)
                    # Perform solution
                    current_repel_steps = self.read_m("wRepelRemainingSteps")
                    for step in steps:
                        self.pyboy.memory[self.pyboy.symbol_lookup("wRepelRemainingSteps")[1]] = (
                            0xFF
                        )
                        match step:
                            case str(button):
                                self.pyboy.button(button, 8)
                                self.pyboy.tick(self.action_freq * 2, self.animate_scripts)
                            case (str(button), int(button_freq), int(action_freq)):
                                self.pyboy.button(button, button_freq)
                                self.pyboy.tick(action_freq, self.animate_scripts)
                            case _:
                                raise
                        while self.read_m("wJoyIgnore"):
                            self.pyboy.tick(self.action_freq, render=False)
                    self.pyboy.memory[self.pyboy.symbol_lookup("wRepelRemainingSteps")[1]] = (
                        current_repel_steps
                    )
                    if not self.disable_wild_encounters:
                        self.setup_enable_wild_ecounters()
                    break

    def use_strength(self):
        self.flags.set_bit("BIT_STRENGTH_ACTIVE", 1)

    def skip_safari_zone_atn(self):
        # First move down
        self.pyboy.button("down", 8)
        self.pyboy.tick(self.action_freq, render=self.animate_scripts)
        _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
        _, wNumBagItems = self.pyboy.symbol_lookup("wNumBagItems")
        numBagItems = self.read_m(wNumBagItems)
        bag = np.array(self.pyboy.memory[wBagItems : wBagItems + 40], dtype=np.uint8)
        if numBagItems < 20 and not self.events.get_event("EVENT_GOT_HM03"):
            self.events.set_event("EVENT_GOT_HM03", True)
            bag[numBagItems * 2] = Items.HM_03.value
            bag[numBagItems * 2 + 1] = 1
            numBagItems += 1
        if numBagItems < 20 and not self.missables.get_missable("HS_SAFARI_ZONE_WEST_ITEM_4"):
            self.missables.set_missable("HS_SAFARI_ZONE_WEST_ITEM_4", True)
            bag[numBagItems * 2] = Items.GOLD_TEETH.value
            bag[numBagItems * 2 + 1] = 1
            numBagItems += 1
        bag[numBagItems * 2 :] = 0xFF
        self.pyboy.memory[wBagItems : wBagItems + 40] = bag
        self.pyboy.memory[wNumBagItems] = numBagItems

    def next_elevator_floor(self):
        curMapId = MapIds(self.read_m("wCurMap"))
        if curMapId in (MapIds.SILPH_CO_ELEVATOR, MapIds.CELADON_MART_ELEVATOR):
            for _ in range(5):
                self.pyboy.button("up", 8)
                self.pyboy.tick(self.action_freq, render=self.animate_scripts)
            # walk right
            for _ in range(5):
                self.pyboy.button("right", 8)
                self.pyboy.tick(self.action_freq, render=self.animate_scripts)
        elif (
            curMapId == MapIds.ROCKET_HIDEOUT_ELEVATOR
            and Items.LIFT_KEY.name in self.required_items
        ):
            for _ in range(5):
                self.pyboy.button("left", 8)
                self.pyboy.tick(self.action_freq, render=self.animate_scripts)
        else:
            return

        self.pyboy.button("up", 8)
        self.pyboy.tick(self.action_freq, render=self.animate_scripts)
        self.pyboy.button("a", 8)
        self.pyboy.tick(5 * self.action_freq, render=self.animate_scripts)
        for _ in range(NEXT_ELEVATORS.get(MapIds(self.read_m("wWarpedFromWhichMap")), 0)):
            self.pyboy.button("down", 8)
            self.pyboy.tick(self.action_freq, render=self.animate_scripts)

        self.pyboy.button("a", 8)
        self.pyboy.tick(20 * self.action_freq, render=self.animate_scripts)
        # now leave elevator
        if curMapId in (MapIds.SILPH_CO_ELEVATOR, MapIds.CELADON_MART_ELEVATOR):
            for _ in range(5):
                self.pyboy.button("down", 8)
                self.pyboy.tick(self.action_freq, render=self.animate_scripts)
            self.pyboy.button("left", 8)
            self.pyboy.tick(self.action_freq, render=self.animate_scripts)
            self.pyboy.button("down", 8)
            self.pyboy.tick(self.action_freq, render=self.animate_scripts)
        elif (
            curMapId == MapIds.ROCKET_HIDEOUT_ELEVATOR
            and Items.LIFT_KEY.name in self.required_items
        ):
            self.pyboy.button("right", 8)
            self.pyboy.tick(self.action_freq, render=self.animate_scripts)
            self.pyboy.button("up", 8)
            self.pyboy.tick(self.action_freq, render=self.animate_scripts)

    def insert_guard_drinks(self):
        if not self.flags.get_bit("BIT_GAVE_SAFFRON_GUARDS_DRINK") and MapIds(
            self.read_m("wCurMap")
        ) in [
            MapIds.CELADON_MART_1F,
            MapIds.CELADON_MART_2F,
            MapIds.CELADON_MART_3F,
            MapIds.CELADON_MART_4F,
            MapIds.CELADON_MART_5F,
            MapIds.CELADON_MART_ELEVATOR,
            MapIds.CELADON_MART_ROOF,
        ]:
            _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
            _, wNumBagItems = self.pyboy.symbol_lookup("wNumBagItems")
            numBagItems = self.read_m(wNumBagItems)
            bag = np.array(self.pyboy.memory[wBagItems : wBagItems + 40], dtype=np.uint8)
            if numBagItems < 20 and not {
                Items.LEMONADE.value,
                Items.FRESH_WATER.value,
                Items.SODA_POP.value,
            }.intersection(bag[::2]):
                bag[numBagItems * 2] = Items.LEMONADE.value
                bag[numBagItems * 2 + 1] = 1
                numBagItems += 1
                bag[numBagItems * 2 :] = 0xFF
                self.pyboy.memory[wBagItems : wBagItems + 40] = bag
                self.pyboy.memory[wNumBagItems] = numBagItems

            _, wBagSavedMenuItem = self.pyboy.symbol_lookup("wBagSavedMenuItem")
            _, wListScrollOffset = self.pyboy.symbol_lookup("wListScrollOffset")
            # TODO: Make this point to the location of the last removed item
            # Should be something like the current location - the number of items
            # that have been removed - 1
            self.pyboy.memory[wBagSavedMenuItem] = 0
            self.pyboy.memory[wListScrollOffset] = 0

    def sign_hook(self, *args, **kwargs):
        sign_id = self.read_m("hSpriteIndexOrTextID")
        map_id = self.read_m("wCurMap")
        # self.seen_signs[(map_id, sign_id)] = 1.0 if self.scale_map_id(map_id) else 0.0
        self.seen_signs[(map_id, sign_id)] = 1.0

    def hidden_object_hook(self, *args, **kwargs):
        hidden_object_id = self.pyboy.memory[self.pyboy.symbol_lookup("wHiddenObjectIndex")[1]]
        map_id = self.pyboy.memory[self.pyboy.symbol_lookup("wCurMap")[1]]
        # self.seen_hidden_objs[(map_id, hidden_object_id)] = (
        #     1.0 if self.scale_map_id(map_id) else 0.0
        # )
        self.seen_hidden_objs[(map_id, hidden_object_id)] = 1.0

    def sprite_hook(self, *args, **kwargs):
        sprite_id = self.pyboy.memory[self.pyboy.symbol_lookup("hSpriteIndexOrTextID")[1]]
        map_id = self.pyboy.memory[self.pyboy.symbol_lookup("wCurMap")[1]]
        # self.seen_npcs[(map_id, sprite_id)] = 1.0 if self.scale_map_id(map_id) else 0.0
        self.seen_npcs[(map_id, sprite_id)] = 1.0

    def start_menu_hook(self, *args, **kwargs):
        if self.read_m("wIsInBattle") == 0:
            self.seen_start_menu = 1

    def item_menu_hook(self, *args, **kwargs):
        # if self.read_m("wIsInBattle") == 0:
        self.seen_bag_menu = 1

    def pokemon_menu_hook(self, *args, **kwargs):
        if self.read_m("wIsInBattle") == 0:
            self.seen_pokemon_menu = 1

    def chose_stats_hook(self, *args, **kwargs):
        if self.read_m("wIsInBattle") == 0:
            self.seen_stats_menu = 1

    def chose_item_hook(self, *args, **kwargs):
        # if self.read_m("wIsInBattle") == 0:
        self.seen_action_bag_menu = 1

    def blackout_hook(self, *args, **kwargs):
        self.blackout_count += 1

    def blackout_update_hook(self, *args, **kwargs):
        self.blackout_check = self.read_m("wLastBlackoutMap")
        if (
            self.disable_wild_encounters
            and MapIds(self.blackout_check).name in self.disable_wild_encounters_maps
        ):
            self.pyboy.memory[self.pyboy.symbol_lookup("wRepelRemainingSteps")[1]] = 0x01
        # Reapply infinite health on blackout relocation
        if self.infinite_health:
            self.reverse_damage()
            self.party = PartyMons(self.pyboy)

    def pokecenter_heal_hook(self, *args, **kwargs):
        self.pokecenter_heal = 1
        # Reapply infinite health when healed in a Poké Center
        if self.infinite_health:
            self.reverse_damage()
            self.party = PartyMons(self.pyboy)

    def overworld_loop_hook(self, *args, **kwargs):
        self.user_control = True

    def update_warps_hook(self, *args, **kwargs):
        # current map id, destiation map id, warp id
        key = (
            self.read_m("wCurMap"),
            self.read_m("hWarpDestinationMap"),
            self.read_m("wDestinationWarpID"),
        )
        if key[-1] != 0xFF:
            self.seen_warps[key] = 1

    def cut_hook(self, context: bool):
        player_direction = self.pyboy.memory[
            self.pyboy.symbol_lookup("wSpritePlayerStateData1FacingDirection")[1]
        ]
        x, y, map_id = self.get_game_coords()  # x, y, map_id
        if player_direction == 0:  # down
            coords = (x, y + 1, map_id)
        if player_direction == 4:
            coords = (x, y - 1, map_id)
        if player_direction == 8:
            coords = (x - 1, y, map_id)
        if player_direction == 0xC:
            coords = (x + 1, y, map_id)

        wTileInFrontOfPlayer = self.pyboy.memory[
            self.pyboy.symbol_lookup("wTileInFrontOfPlayer")[1]
        ]
        if context:
            if wTileInFrontOfPlayer in [0x3D, 0x50]:
                self.valid_cut_coords[coords] = 1
            else:
                self.invalid_cut_coords[coords] = 1
        else:
            self.invalid_cut_coords[coords] = 1

        self.cut_tiles[wTileInFrontOfPlayer] = 1
        self.cut_explore_map[local_to_global(y, x, map_id)] = 1

    def pokeflute_hook(self, context: bool):
        player_direction = self.pyboy.memory[
            self.pyboy.symbol_lookup("wSpritePlayerStateData1FacingDirection")[1]
        ]
        x, y, map_id = self.get_game_coords()  # x, y, map_id
        if player_direction == 0:  # down
            coords = (x, y + 1, map_id)
        if player_direction == 4:
            coords = (x, y - 1, map_id)
        if player_direction == 8:
            coords = (x - 1, y, map_id)
        if player_direction == 0xC:
            coords = (x + 1, y, map_id)
        if context:
            self.valid_pokeflute_coords[coords] = 1
        else:
            self.invalid_pokeflute_coords[coords] = 1
        wTileInFrontOfPlayer = self.pyboy.memory[
            self.pyboy.symbol_lookup("wTileInFrontOfPlayer")[1]
        ]
        self.pokeflute_tiles[wTileInFrontOfPlayer] = 1

    def surf_hook(self, context: bool, *args, **kwargs):
        player_direction = self.pyboy.memory[
            self.pyboy.symbol_lookup("wSpritePlayerStateData1FacingDirection")[1]
        ]
        x, y, map_id = self.get_game_coords()  # x, y, map_id
        if player_direction == 0:  # down
            coords = (x, y + 1, map_id)
        if player_direction == 4:
            coords = (x, y - 1, map_id)
        if player_direction == 8:
            coords = (x - 1, y, map_id)
        if player_direction == 0xC:
            coords = (x + 1, y, map_id)
        if context:
            self.valid_surf_coords[coords] = 1
        else:
            self.invalid_surf_coords[coords] = 1
        wTileInFrontOfPlayer = self.pyboy.memory[
            self.pyboy.symbol_lookup("wTileInFrontOfPlayer")[1]
        ]
        self.surf_tiles[wTileInFrontOfPlayer] = 1

    def use_ball_hook(self, *args, **kwargs):
        self.use_ball_count += 1

    def disable_wild_encounter_hook(self, *args, **kwargs):
        if (
            self.disable_wild_encounters
            and MapIds(self.read_m("wCurMap")).name not in self.disable_wild_encounters_maps
        ):
            self.pyboy.memory[self.pyboy.symbol_lookup("wRepelRemainingSteps")[1]] = 0xFF
            self.pyboy.memory[self.pyboy.symbol_lookup("wCurEnemyLevel")[1]] = 0x01

    def agent_stats(self, action):
        levels = [self.read_m(f"wPartyMon{i+1}Level") for i in range(self.read_m("wPartyCount"))]
        badges = self.read_m("wObtainedBadges")

        _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
        bag = np.array(self.pyboy.memory[wBagItems : wBagItems + 40], dtype=np.uint8)
        numBagItems = self.read_m("wNumBagItems")
        # item ids start at 1 so using 0 as the nothing value is okay
        bag[2 * numBagItems :] = 0
        bag_item_ids = bag[::2]

        exploration_sum = max(
            sum(sum(self.seen_coords.get(tileset.value, {}).values()) for tileset in Tilesets), 1
        )

        return {
            "env_ids": int(self.env_id),
            "stats": {
                "step": self.step_count, # + self.reset_count * self.max_steps,
                "max_map_progress": self.max_map_progress,
                "last_action": action,
                "party_count": self.read_m("wPartyCount"),
                "levels": levels,
                "levels_sum": sum(levels),
                "ptypes": self.read_party(),
                "hp": self.read_hp_fraction(),
                "coord": sum(sum(tileset.values()) for tileset in self.seen_coords.values()),
                "warps": len(self.seen_warps),
                "a_press": len(self.a_press),
                "map_id": np.sum(self.seen_map_ids),
                "npc": sum(self.seen_npcs.values()),
                "hidden_obj": sum(self.seen_hidden_objs.values()),
                "sign": sum(self.seen_signs.values()),
                "deaths": self.died_count,
                "badge": self.get_badges(),
                "healr": self.total_heal_health,
                "action_hist": self.action_hist,
                "caught_pokemon": int(sum(self.caught_pokemon)),
                "seen_pokemon": int(sum(self.seen_pokemon)),
                "obtained_move_ids": int(sum(self.obtained_move_ids)),
                "opponent_level": self.max_opponent_level,
                "taught_cut": int(self.check_if_party_has_hm(TmHmMoves.CUT.value)),
                "taught_surf": int(self.check_if_party_has_hm(TmHmMoves.SURF.value)),
                "taught_strength": int(self.check_if_party_has_hm(TmHmMoves.STRENGTH.value)),
                "cut_tiles": len(self.cut_tiles),
                "valid_cut_coords": len(self.valid_cut_coords),
                "invalid_cut_coords": len(self.invalid_cut_coords),
                "valid_pokeflute_coords": len(self.valid_pokeflute_coords),
                "invalid_pokeflute_coords": len(self.invalid_pokeflute_coords),
                "valid_surf_coords": len(self.valid_surf_coords),
                "invalid_surf_coords": len(self.invalid_surf_coords),
                "menu": {
                    "start_menu": self.seen_start_menu,
                    "pokemon_menu": self.seen_pokemon_menu,
                    "stats_menu": self.seen_stats_menu,
                    "bag_menu": self.seen_bag_menu,
                    "action_bag_menu": self.seen_action_bag_menu,
                },
                "blackout_check": self.blackout_check,
                "item_count": self.read_m(0xD31D),
                "reset_count": self.reset_count,
                "blackout_count": self.blackout_count,
                "pokecenter": np.sum(self.pokecenters),
                "pokecenter_heal": self.pokecenter_heal,
                "in_battle": self.read_m("wIsInBattle") > 0,
                "event": self.event_progress.get("event", None),
                # "max_steps": self.get_max_steps(),
                # redundant but this is so we don't interfere with the swarm logic
                "required_count": len(self.required_events) + len(self.required_items),
                "safari_zone": {k.name: v for k, v in self.safari_zone_steps.items()},
                "use_ball_count": self.use_ball_count,
            }
            | {
                "exploration": {
                    tileset.name.lower(): sum(self.seen_coords.get(tileset.value, {}).values())
                    / exploration_sum
                    for tileset in Tilesets
                }
            }
            | {f"badge_{i+1}": bool(badges & (1 << i)) for i in range(8)},
            "events": {event: self.events.get_event(event) for event in REQUIRED_EVENTS}
            | {
                "rival3": int(self.read_m(0xD665) == 4),
                "game_corner_rocket": self.missables.get_missable("HS_GAME_CORNER_ROCKET"),
                "saffron_guard": self.flags.get_bit("BIT_GAVE_SAFFRON_GUARDS_DRINK"),
                "lapras": self.flags.get_bit("BIT_GOT_LAPRAS"),
            },
            "required_items": {item.name: item.value in bag_item_ids for item in REQUIRED_ITEMS},
            "useful_items": {item.name: item.value in bag_item_ids for item in USEFUL_ITEMS},
            # Remove padding
            "pokemon_exploration_map": self.explore_map,
            # "cut_exploration_map": self.cut_explore_map,
            "species": [pokemon.Species for pokemon in self.party],
            "levels": [pokemon.Level for pokemon in self.party],
            "moves": [list(int(m) for m in pokemon.Moves) for pokemon in self.party],
        }

    def start_video(self):
        if self.full_frame_writer is not None:
            self.full_frame_writer.close()
        if self.map_frame_writer is not None:
            self.map_frame_writer.close()
        if self.screen_obs_frame_writer is not None:
            self.screen_obs_frame_writer.close()
        if self.visited_mask_frame_writer is not None:
            self.visited_mask_frame_writer.close()

        base_dir = self.video_dir / Path("rollouts")
        base_dir.mkdir(exist_ok=True)
        full_name = Path(f"full_reset_{self.reset_count}_id{self.instance_id}").with_suffix(".mp4")
        self.full_frame_writer = media.VideoWriter(
            base_dir / full_name, (144, 160), fps=self.fps, input_format="gray"
        )
        self.full_frame_writer.__enter__()

        map_name = Path(f"map_reset_{self.reset_count}_id{self.instance_id}").with_suffix(".mp4")
        self.map_frame_writer = media.VideoWriter(
            base_dir / map_name,
            self.explore_map.shape,
            fps=self.fps,
            input_format="gray",
        )
        self.map_frame_writer.__enter__()

        screen_obs = self.screen_obs()
        screen_obs_name = Path(
            f"screen_obs_reset_{self.reset_count}_id{self.instance_id}"
        ).with_suffix(".mp4")
        self.screen_obs_frame_writer = media.VideoWriter(
            base_dir / screen_obs_name,
            screen_obs["screen"].shape[:2],
            fps=self.fps,
            input_format="gray",
        )
        self.screen_obs_frame_writer.__enter__()

        visited_mask_name = Path(
            f"visited_mask_reset_{self.reset_count}_id{self.instance_id}"
        ).with_suffix(".mp4")
        self.visited_mask_frame_writer = media.VideoWriter(
            base_dir / visited_mask_name,
            screen_obs["visited_mask"].shape[:2],
            fps=self.fps,
            input_format="gray",
        )
        self.visited_mask_frame_writer.__enter__()

    def add_video_frame(self):
        self.full_frame_writer.add_image(self.render()[:, :])
        
        # Create map frame with quest paths if enabled
        map_frame = self.explore_map.copy()
        if self.show_quest_paths and hasattr(self, 'quest_visualizer'):
            try:
                # Overlay quest paths on the explore map
                map_frame = self.quest_visualizer.overlay_quest_paths_on_map(
                    map_frame, 
                    quest_ids=self.quest_visualization_ids,
                    dot_size=1,  # Smaller dots for video
                    alpha=200
                )
                # Convert back to grayscale for the video writer if needed
                if len(map_frame.shape) == 3:
                    # Use RGB to grayscale conversion (weighted average)
                    map_frame = np.dot(map_frame[...,:3], [0.2989, 0.5870, 0.1140])
            except Exception as e:
                print(f"QuestVisualizer: Error in add_video_frame: {e}")
                map_frame = self.explore_map  # Fallback to original map
        
        self.map_frame_writer.add_image(map_frame)

        screen_obs = self.screen_obs()
        self.screen_obs_frame_writer.add_image(screen_obs["screen"].squeeze(-1))
        self.visited_mask_frame_writer.add_image(screen_obs["visited_mask"].squeeze(-1))

    def get_game_coords(self):
        return (self.read_m("wXCoord"), self.read_m("wYCoord"), self.read_m("wCurMap"))
    
    def set_quest_visualization(self, enabled: bool, quest_ids: list = None):
        """
        Enable or disable quest path visualization.
        
        Args:
            enabled: Whether to show quest paths on the map
            quest_ids: List of quest IDs to visualize. If None, uses current setting.
        """
        self.show_quest_paths = enabled
        if quest_ids is not None:
            self.quest_visualization_ids = quest_ids
        print(f"Quest visualization {'enabled' if enabled else 'disabled'} for quests: {self.quest_visualization_ids}")
    
    def get_quest_visualization_map(self, quest_ids: list = None, dot_size: int = 2, alpha: int = 180):
        """
        Get the current explore map with quest paths overlaid.
        
        Args:
            quest_ids: List of quest IDs to visualize. If None, uses current setting.
            dot_size: Size of dots for quest coordinates
            alpha: Alpha value for quest overlay
            
        Returns:
            Map array with quest paths overlaid
        """
        if quest_ids is None:
            quest_ids = self.quest_visualization_ids
        
        if hasattr(self, 'quest_visualizer'):
            try:
                return self.quest_visualizer.overlay_quest_paths_on_map(
                    self.explore_map, 
                    quest_ids=quest_ids,
                    dot_size=dot_size,
                    alpha=alpha
                )
            except Exception as e:
                print(f"QuestVisualizer: Error creating visualization: {e}")
                return self.explore_map
        else:
            print("QuestVisualizer: Quest visualizer not available")
            return self.explore_map
    
    def save_quest_visualization(self, output_path: str = None, quest_ids: list = None):
        """
        Save a visualization of the current map with quest paths.
        
        Args:
            output_path: Path to save the image. If None, saves to default location.
            quest_ids: List of quest IDs to visualize. If None, uses current setting.
        """
        if output_path is None:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = Path(__file__).parent.parent / "quest_visualizations"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / f"quest_map_{timestamp}.png")
        
        if quest_ids is None:
            quest_ids = self.quest_visualization_ids
        
        if hasattr(self, 'quest_visualizer'):
            try:
                self.quest_visualizer.save_quest_map_visualization(
                    self.explore_map,
                    output_path,
                    quest_ids=quest_ids,
                    dot_size=2,
                    alpha=200
                )
                print(f"Quest visualization saved to: {output_path}")
                return output_path
            except Exception as e:
                print(f"QuestVisualizer: Error saving visualization: {e}")
                return None
        else:
            print("QuestVisualizer: Quest visualizer not available")
            return None

    # def get_max_steps(self):
    #     return max(
    #         0,
    #         self.max_steps,
    #         self.max_steps
    #         * (len(self.required_events) + len(self.required_items))
    #         * self.max_steps_scaling,
    #     )

    def update_seen_coords(self):
        inc = 0.5 if (self.read_m("wMovementFlags") & 0b1000_0000) else self.exploration_inc

        x_pos, y_pos, map_n = self.get_game_coords()
        # self.seen_coords[(x_pos, y_pos, map_n)] = inc
        cur_map_tileset = self.read_m("wCurMapTileset")
        if cur_map_tileset not in self.seen_coords:
            self.seen_coords[cur_map_tileset] = {}
        self.seen_coords[cur_map_tileset][(x_pos, y_pos, map_n)] = min(
            self.seen_coords[cur_map_tileset].get((x_pos, y_pos, map_n), 0.0) + inc,
            self.exploration_max,
        )
        # TODO: Turn into a wrapper?
        self.explore_map[local_to_global(y_pos, x_pos, map_n)] = min(
            self.explore_map[local_to_global(y_pos, x_pos, map_n)] + inc,
            self.exploration_max,
        )
        self.reward_explore_map[local_to_global(y_pos, x_pos, map_n)] = min(
            self.explore_map[local_to_global(y_pos, x_pos, map_n)] + inc,
            self.exploration_max,
        ) * (self.map_id_scalefactor if self.scale_map_id(map_n) else 1.0)
        # self.seen_global_coords[local_to_global(y_pos, x_pos, map_n)] = 1
        self.seen_map_ids[map_n] = 1

    def update_a_press(self):
        if self.read_m("wIsInBattle") != 0 or self.read_m("wFontLoaded"):
            return

        direction = self.read_m("wSpritePlayerStateData1FacingDirection")
        x_pos, y_pos, map_n = self.get_game_coords()
        if direction == 0:
            y_pos += 1
        if direction == 4:
            y_pos -= 1
        if direction == 8:
            x_pos -= 1
        if direction == 0xC:
            x_pos += 1
        # if self.scale_map_id(map_n):
        self.a_press.add((x_pos, y_pos, map_n))

    def get_explore_map(self):
        explore_map = np.zeros(GLOBAL_MAP_SHAPE)
        for (x, y, map_n), v in self.seen_coords.items():
            gy, gx = local_to_global(y, x, map_n)
            if 0 > gy >= explore_map.shape[0] or 0 > gx >= explore_map.shape[1]:
                print(f"coord out of bounds! global: ({gx}, {gy}) game: ({x}, {y}, {map_n})")
            else:
                explore_map[gy, gx] = v

        return explore_map

    def read_m(self, addr: str | int) -> int:
        if isinstance(addr, str):
            return self.pyboy.memory[self.pyboy.symbol_lookup(addr)[1]]
        return self.pyboy.memory[addr]

    def read_short(self, addr: str | int) -> int:
        if isinstance(addr, str):
            _, addr = self.pyboy.symbol_lookup(addr)
        data = self.pyboy.memory[addr : addr + 2]
        return int(data[0] << 8) + int(data[1])

    def read_bit(self, addr: str | int, bit: int) -> bool:
        # add padding so zero will read '0b100000000' instead of '0b0'
        return bool(int(self.read_m(addr)) & (1 << bit))

    def read_event_bits(self):
        _, addr = self.pyboy.symbol_lookup("wEventFlags")
        return self.pyboy.memory[addr : addr + EVENTS_FLAGS_LENGTH]

    def get_badges(self):
        return self.read_m("wObtainedBadges").bit_count()

    def read_party(self):
        _, addr = self.pyboy.symbol_lookup("wPartySpecies")
        party_length = self.pyboy.memory[self.pyboy.symbol_lookup("wPartyCount")[1]]
        return self.pyboy.memory[addr : addr + party_length]

    def update_max_op_level(self):
        # opp_base_level = 5
        opponent_level = max(
            [0]
            + [self.read_m(f"wEnemyMon{i+1}Level") for i in range(self.read_m("wEnemyPartyCount"))]
        )
        # - opp_base_level

        self.max_opponent_level = max(0, self.max_opponent_level, opponent_level)
        return self.max_opponent_level

    def update_health(self):
        cur_health = self.read_hp_fraction()
        # if health increased and party size did not change
        if cur_health > self.last_health and self.read_m("wPartyCount") == self.party_size:
            if self.last_health > 0:
                self.total_heal_health += cur_health - self.last_health
            else:
                self.died_count += 1

    def update_pokedex(self):
        # TODO: Make a hook
        _, wPokedexOwned = self.pyboy.symbol_lookup("wPokedexOwned")
        _, wPokedexOwnedEnd = self.pyboy.symbol_lookup("wPokedexOwnedEnd")
        _, wPokedexSeen = self.pyboy.symbol_lookup("wPokedexSeen")
        _, wPokedexSeenEnd = self.pyboy.symbol_lookup("wPokedexSeenEnd")

        caught_mem = self.pyboy.memory[wPokedexOwned:wPokedexOwnedEnd]
        seen_mem = self.pyboy.memory[wPokedexSeen:wPokedexSeenEnd]
        self.caught_pokemon = np.unpackbits(np.array(caught_mem, dtype=np.uint8))
        self.seen_pokemon = np.unpackbits(np.array(seen_mem, dtype=np.uint8))

    def update_tm_hm_obtained_move_ids(self):
        # TODO: Make a hook
        # Scan party
        for i in range(self.read_m("wPartyCount")):
            _, addr = self.pyboy.symbol_lookup(f"wPartyMon{i+1}Moves")
            for move_id in self.pyboy.memory[addr : addr + 4]:
                # if move_id in TM_HM_MOVES:
                self.obtained_move_ids[move_id] = 1
        """
        # Scan current box (since the box doesn't auto increment in pokemon red)
        num_moves = 4
        box_struct_length = 25 * num_moves * 2
        for i in range(self.pyboy.memory[0xDA80)):
            offset = i * box_struct_length + 0xDA96
            if self.pyboy.memory[offset) != 0:
                for j in range(4):
                    move_id = self.pyboy.memory[offset + j + 8)
                    if move_id != 0:
                        self.obtained_move_ids[move_id] = 1
        """

    def remove_all_nonuseful_items(self):
        _, wNumBagItems = self.pyboy.symbol_lookup("wNumBagItems")
        if self.pyboy.memory[wNumBagItems] == MAX_ITEM_CAPACITY:
            _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
            bag_items = self.pyboy.memory[wBagItems : wBagItems + MAX_ITEM_CAPACITY * 2]
            # Fun fact: The way they test if an item is an hm in code is by testing the item id
            # is greater than or equal to 0xC4 (the item id for HM_01)

            # TODO either remove or check if guard has been given drink
            # guard given drink are 4 script pointers to check, NOT an event
            new_bag_items = [
                (item, quantity)
                for item, quantity in zip(bag_items[::2], bag_items[1::2])
                if Items(item) in KEY_ITEMS | REQUIRED_ITEMS | HM_ITEMS
            ]
            # Write the new count back to memory
            self.pyboy.memory[wNumBagItems] = len(new_bag_items)
            # 0 pad
            new_bag_items += [(255, 255)] * (20 - len(new_bag_items))
            # now flatten list
            new_bag_items = list(sum(new_bag_items, ()))
            # now write back to list
            self.pyboy.memory[wBagItems : wBagItems + len(new_bag_items)] = new_bag_items

            _, wBagSavedMenuItem = self.pyboy.symbol_lookup("wBagSavedMenuItem")
            _, wListScrollOffset = self.pyboy.symbol_lookup("wListScrollOffset")
            # TODO: Make this point to the location of the last removed item
            # Should be something like the current location - the number of items
            # that have been removed - 1
            self.pyboy.memory[wBagSavedMenuItem] = 0
            self.pyboy.memory[wListScrollOffset] = 0

    def update_safari_zone(self):
        curMapId = MapIds(self.read_m("wCurMap"))
        # scale map id performs the same check
        if curMapId in {
            MapIds.SAFARI_ZONE_CENTER,
            MapIds.SAFARI_ZONE_CENTER_REST_HOUSE,
            MapIds.SAFARI_ZONE_EAST,
            MapIds.SAFARI_ZONE_EAST_REST_HOUSE,
            MapIds.SAFARI_ZONE_WEST,
            # MapIds.SAFARI_ZONE_WEST_REST_HOUSE,
            MapIds.SAFARI_ZONE_NORTH,
            MapIds.SAFARI_ZONE_NORTH_REST_HOUSE,
            MapIds.SAFARI_ZONE_SECRET_HOUSE,
        }:
            if (
                self.infinte_safari_steps
                and not self.events.get_event("EVENT_GOT_HM03")
                and not self.missables.get_missable("HS_SAFARI_ZONE_WEST_ITEM_4")
            ):
                _, wSafariSteps = self.pyboy.symbol_lookup("wSafariSteps")
                # lazily set safari steps to 256. I dont want to do the math for 512
                self.pyboy.memory[wSafariSteps] = 0
                self.pyboy.memory[wSafariSteps + 1] = 0xFF

            # update safari zone
            self.safari_zone_steps[curMapId] = max(
                self.safari_zone_steps[curMapId], self.read_short("wSafariSteps")
            )

    def reverse_damage(self):
        for i in range(self.read_m("wPartyCount")):
            _, wPartyMonHP = self.pyboy.symbol_lookup(f"wPartyMon{i+1}HP")
            _, wPartymonMaxHP = self.pyboy.symbol_lookup(f"wPartyMon{i+1}MaxHP")
            self.pyboy.memory[wPartyMonHP] = 0
            self.pyboy.memory[wPartyMonHP + 1] = 128
            self.pyboy.memory[wPartymonMaxHP] = 0
            self.pyboy.memory[wPartymonMaxHP + 1] = 128

    def read_hp_fraction(self):
        party_size = self.read_m("wPartyCount")
        hp_sum = sum(self.read_short(f"wPartyMon{i+1}HP") for i in range(party_size))
        max_hp_sum = sum(self.read_short(f"wPartyMon{i+1}MaxHP") for i in range(party_size))
        max_hp_sum = max(max_hp_sum, 1)
        return hp_sum / max_hp_sum    
    
    def get_enemy_party_head_hp(self):
        hp_total = (self.read_m(ENEMYS_POKEMON_MAX_HP[0]) << 8) + self.read_m(ENEMYS_POKEMON_MAX_HP[1])
        hp_avail = (self.read_m(ENEMYS_POKEMON_HP[0]) << 8) + self.read_m(ENEMYS_POKEMON_HP[1])
        return hp_total, hp_avail

    def get_map_name_by_id(self, map_id_val: int) -> str:
        try:
            return MapIds(map_id_val).name
        except ValueError:
            return f"map_{map_id_val}"

    def update_path_trace(self):
        # When play.py is controlling replay saving, current_run_info is set externally.
        # If it's not set (i.e., None), this function should not attempt to trace.
        if not self.current_run_info and not self.current_run_dir:
            return

        player_x, player_y, map_n = self.get_game_coords()
        gy, gx = local_to_global(player_y, player_x, map_n)
        map_key_str = str(map_n)

        if map_key_str not in self.path_trace_data:
            self.path_trace_data[map_key_str] = []

        if not self.path_trace_data[map_key_str] or self.path_trace_data[map_key_str][-1] != [gy, gx]:
            self.path_trace_data[map_key_str].append([gy, gx])

    def _convert_text(self, bytes_data: list[int]) -> str:
        """Convert Pokemon text format to ASCII"""
        result = ""
        for b in bytes_data:
            if b == 0x50:  # End marker
                break
            elif b == 0x4E:  # Line break
                result += "\n"
            # Main character ranges
            elif 0x80 <= b <= 0x99:  # A-Z
                result += chr(b - 0x80 + ord("A"))
            elif 0xA0 <= b <= 0xB9:  # a-z
                result += chr(b - 0xA0 + ord("a"))
            elif 0xF6 <= b <= 0xFF:  # Numbers 0-9
                result += str(b - 0xF6)
            # Punctuation characters (9A-9F)
            elif b == 0x9A:  # (
                result += "("
            elif b == 0x9B:  # )
                result += ")"
            elif b == 0x9C:  # :
                result += ":"
            elif b == 0x9D:  # ;
                result += ";"
            elif b == 0x9E:  # [
                result += "["
            elif b == 0x9F:  # ]
                result += "]"
            # Special characters
            elif b == 0x7F:  # Space
                result += " "
            elif b == 0x6D:  # : (also appears here)
                result += ":"
            elif b == 0x54:  # POKé control character
                result += "POKé"
            elif b == 0xBA:  # é
                result += "é"
            elif b == 0xBB:  # 'd
                result += "'d"
            elif b == 0xBC:  # 'l
                result += "'l"
            elif b == 0xBD:  # 's
                result += "'s"
            elif b == 0xBE:  # 't
                result += "'t"
            elif b == 0xBF:  # 'v
                result += "'v"
            elif b == 0xE1:  # PK
                result += "Pk"
            elif b == 0xE2:  # MN
                result += "Mn"
            elif b == 0xE3:  # -
                result += "-"
            elif b == 0xE6:  # ?
                result += "?"
            elif b == 0xE7:  # !
                result += "!"
            elif b == 0xE8:  # .
                result += "."
            elif b == 0xE9:  # .
                result += "."
            # E-register special characters
            elif b == 0xE0:  # '
                result += "'"
            elif b == 0xE1:  # PK
                result += "POKé"
            elif b == 0xE2:  # MN
                result += "MON"
            elif b == 0xE3:  # -
                result += "-"
            elif b == 0xE4:  # 'r
                result += "'r"
            elif b == 0xE5:  # 'm
                result += "'m"
            elif b == 0xE6:  # ?
                result += "?"
            elif b == 0xE7:  # !
                result += "!"
            elif b == 0xE8:  # .
                result += "."
            elif b == 0xE9:  # ア
                result += "ア"
            elif b == 0xEA:  # ウ
                result += "ウ"
            elif b == 0xEB:  # エ
                result += "エ"
            elif b == 0xEC:  # ▷
                result += "▷"
            elif b == 0xED:  # ►
                result += "►"
            elif b == 0xEE:  # ▼
                result += "▼"
            elif b == 0xEF:  # ♂
                result += "♂"
            # F-register special characters
            elif b == 0xF0:  # ♭
                result += "♭"
            elif b == 0xF1:  # ×
                result += "×"
            elif b == 0xF2:  # .
                result += "."
            elif b == 0xF3:  # /
                result += "/"
            elif b == 0xF4:  # ,
                result += ","
            elif b == 0xF5:  # ♀
                result += "♀"
            # Numbers 0-9 (0xF6-0xFF)
            elif 0xF6 <= b <= 0xFF:
                result += str(b - 0xF6)
            else:
                # For debugging, show the hex value of unknown characters
                result += f"[{b:02X}]"
        return result.strip()

    def read_dialog(self) -> str:
        """Read any dialog text currently on screen by scanning the tilemap buffer"""
        # Tilemap buffer is from C3A0 to C507
        buffer_start = 0xC3A0
        buffer_end = 0xC507

        # Get all bytes from the buffer
        buffer_bytes = [self.memory[addr] for addr in range(buffer_start, buffer_end)]

        # Look for sequences of text (ignoring long sequences of 0x7F/spaces)
        text_lines = []
        current_line = []
        space_count = 0
        last_was_border = False

        for b in buffer_bytes:
            if b == 0x7C:  # ║ character
                if last_was_border:
                    # If the last character was a border and this is ║, treat as newline
                    text = self._convert_text(current_line)
                    if text.strip():
                        text_lines.append(text)
                    current_line = []
                    space_count = 0
                else:
                    # current_line.append(b)
                    pass
                last_was_border = True
            elif b == 0x7F:  # Space
                space_count += 1
                current_line.append(b)  # Always keep spaces
                last_was_border = False
            # All text characters: uppercase, lowercase, special chars, punctuation, symbols
            elif (
                # Box drawing (0x79-0x7E)
                # (0x79 <= b <= 0x7E)
                # or
                # Uppercase (0x80-0x99)
                (0x80 <= b <= 0x99)
                or
                # Punctuation (0x9A-0x9F)
                (0x9A <= b <= 0x9F)
                or
                # Lowercase (0xA0-0xB9)
                (0xA0 <= b <= 0xB9)
                or
                # Contractions (0xBA-0xBF)
                (0xBA <= b <= 0xBF)
                or
                # Special characters in E-row (0xE0-0xEF)
                (0xE0 <= b <= 0xEF)
                or
                # Special characters in F-row (0xF0-0xF5)
                (0xF0 <= b <= 0xF5)
                or
                # Numbers (0xF6-0xFF)
                (0xF6 <= b <= 0xFF)
                or
                # Line break
                b == 0x4E
            ):
                space_count = 0
                current_line.append(b)
                last_was_border = (
                    0x79 <= b <= 0x7E
                )  # Track if this is a border character

            # If we see a lot of spaces, might be end of line
            if space_count > 10 and current_line:
                text = self._convert_text(current_line)
                if text.strip():  # Only add non-empty lines
                    text_lines.append(text)
                current_line = []
                space_count = 0
                last_was_border = False

        # Add final line if any
        if current_line:
            text = self._convert_text(current_line)
            if text.strip():
                text_lines.append(text)

        text = "\n".join(text_lines)

        # Post-process for name entry context
        if "lower case" in text.lower() or "UPPER CASE" in text:
            # We're in name entry, replace ♭ with ED
            text = text.replace("♭", "ED\n")

        return text
    
    def update_map_progress(self):
        map_idx = self.read_m(0xD35E)
        self.max_map_progress = max(0, self.max_map_progress, self.get_map_progress(map_idx))

    def get_map_progress(self, map_idx):
        if map_idx in self.essential_map_locations.keys():
            return self.essential_map_locations[map_idx]
        else:
            return -1

    def get_items_in_bag(self) -> Iterable[Items]:
        num_bag_items = self.read_m("wNumBagItems")
        _, addr = self.pyboy.symbol_lookup("wBagItems")
        return [Items(i) for i in self.pyboy.memory[addr : addr + 2 * num_bag_items][::2]]

    def get_hm_count(self) -> int:
        return len(HM_ITEMS.intersection(self.get_items_in_bag()))

    def get_required_events(self) -> set[str]:
        return (
            set(
                event
                for event, v in zip(REQUIRED_EVENTS, self.events.get_events(REQUIRED_EVENTS))
                if v
            )
            | ({"rival3"} if (self.read_m("wSSAnne2FCurScript") == 4) else set())
            | (
                {"game_corner_rocket"}
                if self.missables.get_missable("HS_GAME_CORNER_ROCKET")
                else set()
            )
            | ({"saffron_guard"} if self.flags.get_bit("BIT_GAVE_SAFFRON_GUARDS_DRINK") else set())
            | ({"lapras"} if self.flags.get_bit("BIT_GOT_LAPRAS") else set())
        )

    def get_required_items(self) -> set[str]:
        try:
            wNumBagItems = self.read_m("wNumBagItems")
            _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
            bag_items = self.pyboy.memory[wBagItems : wBagItems + wNumBagItems * 2 : 2]
            return {Items(item).name for item in bag_items if Items(item) in REQUIRED_ITEMS}
        except Exception as e:
            print(f"Error getting required items: {e}")
            return set()

    def get_events_sum(self):
        # adds up all event flags, exclude museum ticket
        return max(
            sum(
                [
                    self.read_m(i).bit_count()
                    for i in range(EVENT_FLAGS_START, EVENT_FLAGS_START + EVENTS_FLAGS_LENGTH)
                ]
            )
            - self.base_event_flags
            - int(self.read_bit(*MUSEUM_TICKET)),
            0,
        )

    def scale_map_id(self, map_n: int) -> bool:
        map_id = MapIds(map_n)
        if map_id not in MAP_ID_COMPLETION_EVENTS:
            return False
        after, until = MAP_ID_COMPLETION_EVENTS[map_id]

        if all(
            (item.startswith("EVENT_") and self.events.get_event(item))
            or (item.startswith("HS_") and self.missables.get_missable(item))
            or (item.startswith("BIT_") and self.flags.get_bit(item))
            for item in after
        ) and any(
            (item.startswith("EVENT_") and not self.events.get_event(item))
            or (item.startswith("HS_") and not self.missables.get_missable(item))
            or (item.startswith("BIT_") and not self.flags.get_bit(item))
            for item in until
        ):
            return True
        return False

    def check_num_bag_items(self):
        _, wBagItems = self.pyboy.symbol_lookup("wBagItems")
        _, wNumBagItems = self.pyboy.symbol_lookup("wNumBagItems")
        numBagItems = self.read_m(wNumBagItems)
        bag = np.array(self.pyboy.memory[wBagItems : wBagItems + 40], dtype=np.uint8)
        if numBagItems >= 20:
            print(
                f"WARNING: env id {int(self.env_id)} contains a full bag with items: {[Items(item) for item in bag[::2]]}"
            )

    def close(self):
        # Skip environment auto-save if replays are disabled
        if not getattr(self, 'record_replays', False):
            return
        if self.save_video:
            self.full_frame_writer.close()
            self.map_frame_writer.close()
            self.screen_obs_frame_writer.close()
            self.visited_mask_frame_writer.close()

        # Use RunManager for saving final state and data
        if hasattr(self, 'current_run_info') and self.current_run_info:
            from .environment_helpers.saver import save_final_state
            try:
                # Save final state and coordinates using RunManager
                save_final_state(self, self.current_run_info, coords_data=self.path_trace_data)
                print(f"Saved final run data to {self.current_run_info.run_dir}")
            except Exception as e:
                print(f"Error saving final run data: {e}")
            finally:
                # Clear for next potential full re-initialization if object is reused
                self.current_run_info = None
                self.current_run_dir = None
                self.path_trace_data = {}
        else:
            # Fallback: save ending game state in state_dir for resume (e.g., if env is used outside play.py)
            try:
                end_name = f"{self.init_state_name}_end.state" if self.init_state_name else "default_env_end.state"
                end_path = self.state_dir / end_name
                end_path.parent.mkdir(parents=True, exist_ok=True)
                with open(end_path, "wb") as f_end:
                    self.pyboy.save_state(f_end)
                print(f"Saved fallback ending game state to {end_path}")
            except Exception as e:
                print(f"Error saving fallback ending game state: {e}")


    def read_game_time(self) -> tuple[int, int, int]:
        """Read game time as (hours, minutes, seconds)"""
        hours = (self.memory[0xDA40] << 8) + self.memory[0xDA41]
        minutes = self.memory[0xDA42]
        seconds = self.memory[0xDA44]
        return (hours, minutes, seconds)

    def read_location(self) -> str:
        """Read current location name"""
        map_id = self.memory[0xD35E]
        return MapIds(map_id).name.replace("_", " ")

    def read_tileset(self) -> str:
        """Read current map's tileset name"""
        tileset_id = self.memory[0xD367]
        return Tilesets(tileset_id).name.replace("_", " ")
    
    def get_screenshot(self):
        """Get the current screenshot."""
        return Image.fromarray(self.pyboy.screen.ndarray)
    
    def get_collision_map_array(self):
        """Get the current collision map as numpy array."""
        return self.pyboy.collision_map.ndarray
    
    def _get_direction(self, array):
        """Determine the player's facing direction from the sprite pattern."""
        # Look through the array for any 2x2 grid containing numbers 0-3
        rows, cols = array.shape

        for i in range(rows - 1):
            for j in range(cols - 1):
                # Extract 2x2 grid
                grid = array[i : i + 2, j : j + 2].flatten()

                # Check for each direction pattern
                if list(grid) == [0, 1, 2, 3]:
                    return "down"
                elif list(grid) == [4, 5, 6, 7]:
                    return "up"
                elif list(grid) == [9, 8, 11, 10]:
                    return "right"
                elif list(grid) == [8, 9, 10, 11]:
                    return "left"

        # FIXED: Try reading direction from memory as fallback
        try:
            # Read player direction from memory (0 = down, 4 = up, 8 = left, 12 = right)
            direction_byte = self.read_m(0xC109)  # Player direction memory address
            if direction_byte == 0:
                return "down"
            elif direction_byte == 4:
                return "up"
            elif direction_byte == 8:
                return "left"
            elif direction_byte == 12:
                return "right"
        except Exception:
            pass

        return "unknown"
    
    def get_coordinates(self):
        """
        Return player's position as (col, row) to match the 9×10 grid and
        path‑finding helpers.
        Returns:
            tuple[int, int]: (col, row) coordinates
        """
        self.current_map_id = self.read_m("wCurMap")
        # read_coordinates returns (col, row)
        return self.get_game_coords()[:2]
    
    # get_coordinates returns (col, row)
    # def get_coordinates_xy(self):
    #     """Return coordinates in the original GameBoy order (x,y)."""
    #     row, col = self.get_coordinates()
    #     return (col, row)

    def get_active_dialog(self):
        """
        Returns the active dialog text from game memory.
        Returns:
            str: Dialog text
        """
        dialog = self.read_dialog()
        if dialog:
            return dialog
        return None

    def get_location(self):
        """
        Returns the player's current location name from game memory.
        Returns:
            str: Location name
        """
        return self.read_location()

    def _get_player_center(self, array):
        """Locate the 2×2 sprite block that represents the player and return
        the centre (row, col) within the 18×20 screen grid.  Falls back to
        (9,8) if the pattern is not found.
        """
        rows, cols = array.shape

        patterns = [
            ([0, 1, 2, 3], "down"),   # facing down
            ([4, 5, 6, 7], "up"),     # facing up
            ([9, 8, 11, 10], "right"),
            ([8, 9, 10, 11], "left"),
        ]

        for i in range(rows - 1):
            for j in range(cols - 1):
                block = array[i : i + 2, j : j + 2].flatten().tolist()
                for pattern, _ in patterns:
                    if block == pattern:
                        return i + 1, j + 1  # center of 2×2 block
        # Fallback to assumed center of screen
        return 9, 8

    def _downsample_array(self, arr):
        """Downsample an 18x20 array to 9x10 by averaging 2x2 blocks."""
        # Ensure input array is 18x20
        if arr.shape != (18, 20):
            raise ValueError("Input array must be 18x20")

        # Reshape to group 2x2 blocks and take mean
        return arr.reshape(9, 2, 10, 2).mean(axis=(1, 3))

    def get_collision_map(self):
        """
        Creates a simple ASCII map showing player position, direction, terrain and sprites.
        Takes into account tile pair collisions for more accurate walkability.
        Returns:
            str: A string representation of the ASCII map with legend
        """
        # Get the terrain and movement data
        full_map = self.pyboy.game_area()
        collision_map = self.pyboy.game_area_collision()
        downsampled_terrain = self._downsample_array(collision_map)

        # Get sprite locations
        sprite_locations = self.get_sprites()

        # Get character direction from the full map
        direction = self._get_direction(full_map)
        if direction == "no direction found":
            return None

        # Prepare collision lookup
        tileset = self.read_tileset()
        full_tilemap = self.pyboy.game_wrapper._get_screen_background_tilemap()

        # Numeric codes: 0=walkable, 1=wall, 2=sprite, 3=player up, 4=player down, 5=player left, 6=player right
        dir_codes = {"up": 3, "down": 4, "left": 5, "right": 6}
        player_code = dir_codes.get(direction, 3)

        # Build numeric grid
        grid = []
        for i in range(9):
            row = []
            for j in range(10):
                # Player at center
                if i == 4 and j == 4:
                    row.append(player_code)
                # Sprite positions
                elif (j, i) in sprite_locations:
                    row.append(2)
                else:
                    # Base terrain check
                    walkable = False
                    if downsampled_terrain[i][j] != 0:
                        current_tile = full_tilemap[i * 2 + 1][j * 2]
                        player_tile = full_tilemap[9][8]
                        if self._can_move_between_tiles(player_tile, current_tile, tileset):
                            walkable = True
                    # Append code
                    row.append(0 if walkable else 1)
            grid.append(row)

        # Prepare output lines
        lines = []
        for row in grid:
            lines.append(" ".join(str(x) for x in row))

        # Legend for numeric codes
        lines.extend([
            "",
            "Legend:",
            "0 - walkable path",
            "1 - wall / obstacle / unwalkable",
            "2 - sprite (NPC)",
            "3 - player (facing up)",
            "4 - player (facing down)",
            "5 - player (facing left)",
            "6 - player (facing right)",
        ])
        return "\n".join(lines)

    def get_valid_moves(self):
        """Return list of valid cardinal directions for the player this frame.

        Uses the full 18×20 collision grid so single‑tile warps/doors are not
        lost in down‑sampling.  Additionally, certain tile IDs are treated as
        walkable even if the collision byte is 0 (warp/door tiles in Pokémon
        Red).
        """

        collision = self.pyboy.game_area_collision()  # 18×20 ints (0/1)
        # The background tilemap (same resolution) lets us identify warps
        full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()

        # Known warp/door tile indices (inside houses, building exits, etc.)
        WARP_TILE_IDS = {
            # stair warp tiles
            0x0A, 0x0B,
            # interior door top/bottom
            0x4E, 0x4F,
            # exterior single‑door top/bottom variants
            0x50, 0x51, 0x52, 0x53,
            # house / lab door variants
            0x5E, 0x5F,
            0x6E, 0x6F,
            0x70, 0x71, 0x72, 0x73,
        }

        # Helper to decide if the tile at (r,c) can be entered
        def is_walkable(r: int, c: int) -> bool:
            if not (0 <= r < 18 and 0 <= c < 20):
                return False
            if collision[r][c] != 0:
                return True
            # collision == 0  => normally a wall; allow if warp tile id
            return full_map[r][c] in WARP_TILE_IDS

        # Locate player sprite dynamically (works after map scroll)
        pr, pc = self._get_player_center(full_map)
        directions = {
            "up": (pr - 1, pc),
            "down": (pr + 1, pc),
            "left": (pr, pc - 1),
            "right": (pr, pc + 1),
        }

        valid = [d for d, (r, c) in directions.items() if is_walkable(r, c)]

        # If standing on a warp tile, always allow the direction that leads off‑screen
        if full_map[pr][pc] in WARP_TILE_IDS:
            # Determine facing direction to exit (depends on warp orientation)
            # crude heuristic: if pr < 9 then up exits, if pr > 9 down exits
            if pr <= 8 and "up" not in valid:
                valid.append("up")
            if pr >= 9 and "down" not in valid:
                valid.append("down")
        return valid

    def _can_move_between_tiles(self, tile1: int, tile2: int, tileset: str) -> bool:
        """
        Check if movement between two tiles is allowed based on tile pair collision data.

        Args:
            tile1: The tile being moved from
            tile2: The tile being moved to
            tileset: The current tileset name

        Returns:
            bool: True if movement is allowed, False if blocked
        """
        # Tile pair collision data
        TILE_PAIR_COLLISIONS_LAND = [
            ("CAVERN", 288, 261),
            ("CAVERN", 321, 261),
            ("FOREST", 304, 302),
            ("CAVERN", 298, 261),
            ("CAVERN", 261, 289),
            ("FOREST", 338, 302),
            ("FOREST", 341, 302),
            ("FOREST", 342, 302),
            ("FOREST", 288, 302),
            ("FOREST", 350, 302),
            ("FOREST", 351, 302),
        ]

        TILE_PAIR_COLLISIONS_WATER = [
            ("FOREST", 276, 302),
            ("FOREST", 328, 302),
            ("CAVERN", 276, 261),
        ]

        # Check both land and water collisions
        for ts, t1, t2 in TILE_PAIR_COLLISIONS_LAND + TILE_PAIR_COLLISIONS_WATER:
            if ts == tileset:
                # Check both directions since collisions are bidirectional
                if (tile1 == t1 and tile2 == t2) or (tile1 == t2 and tile2 == t1):
                    return False

        return True

    def get_sprites(self, debug=False):
        """
        Get the location of all of the sprites on the screen.
        returns set of coordinates that are (column, row)
        """
        # Group sprites by their exact Y coordinate
        sprites_by_y = {}

        for i in range(40):
            sp = self.pyboy.get_sprite(i)
            if sp.on_screen:
                x = int(sp.x / 160 * 10)
                y = int(sp.y / 144 * 9)
                orig_y = sp.y

                if orig_y not in sprites_by_y:
                    sprites_by_y[orig_y] = []
                sprites_by_y[orig_y].append((x, y, i))

        # Sort Y coordinates
        y_positions = sorted(sprites_by_y.keys())
        bottom_sprite_tiles = set()

        if debug:
            print("\nSprites grouped by original Y:")
            for orig_y in y_positions:
                sprites = sprites_by_y[orig_y]
                print(f"Y={orig_y}:")
                for x, grid_y, i in sprites:
                    print(f"  Sprite {i}: x={x}, grid_y={grid_y}")

        SPRITE_HEIGHT = 8

        # First, group sprites by X coordinate for each Y level
        for i in range(len(y_positions) - 1):
            y1 = y_positions[i]
            y2 = y_positions[i + 1]

            if y2 - y1 == SPRITE_HEIGHT:
                # Group sprites by X coordinate at each Y level
                sprites_at_y1 = {s[0]: s for s in sprites_by_y[y1]}  # x -> sprite info
                sprites_at_y2 = {s[0]: s for s in sprites_by_y[y2]}

                # Only match sprites that share the same X coordinate
                for x in sprites_at_y2:
                    if x in sprites_at_y1:  # If there's a matching top sprite at this X
                        bottom_sprite = sprites_at_y2[x]
                        bottom_sprite_tiles.add((x, bottom_sprite[1]))
                        if debug:
                            print(f"\nMatched sprites at x={x}, Y1={y1}, Y2={y2}")

        return bottom_sprite_tiles

    # ------------------------------------------------------------------
    # Warp / Door detection helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Door tile ID sets – top halves, bottom halves, and single‑tile stairs
    # ------------------------------------------------------------------

    # Actual single‑tile warp stairs (bottom step). Top‑half graphics 0x1E/0x1F
    # are **not** warps and must be excluded or they create false doors.
    _STAIR_TILES = {0x0A, 0x0B, 0x1A, 0x1B, 0x1C, 0x1D}

    # ------------------------------------------------------------------
    # Door tile IDs
    # ------------------------------------------------------------------

    # Older logic tried to infer doors by matching a TOP‑tile directly above a
    # BOTTOM‑tile.  In practice the full tilemap scrolls, NPC sprites overlap
    # the graphics, and many legitimate warp tiles (e.g. cave exits) are
    # single‑tile, making that approach brittle.  Instead we maintain a single
    # flat set that lists **only** the tile IDs that the game engine uses as
    # the *walk‑into* warp tile – the bottom half of doors and the staircase
    # step.  This greatly simplifies detection and eliminates duplicate /
    # mismatched pairs.

    _DOOR_WARP_IDS = {
        # Warp tile list – add 0x1B (exterior house door bottom)

        0x4F,   # interior door bottom
        0x34,   # staircase bottom
        0x1B,   # exterior house/lab door bottom
    }

    # Re‑use _STAIR_TILES so stairs are always included even if list drifts
    _DOOR_TILE_IDS = _STAIR_TILES | _DOOR_WARP_IDS

    # Manual mapping from certain interior map names to their exterior location.
    # This is deliberately minimal – we only include early‑game interiors for now.
    _INTERIOR_DEST_OVERRIDES = {
        "PLAYERS HOUSE 1F": "Pallet Town",
        # For staircases inside the house, upstairs leads to 1F, not outdoors
        "PLAYERS HOUSE 2F": "Players House 1F",
        "OAKS LAB": "Pallet Town",
        "RIVALS HOUSE": "Pallet Town",
    }

    def _infer_door_destination(self, current_location: str) -> str | None:
        """Best‑effort guess of the exterior destination for a door.

        The approach is heuristic – for certain known interiors we return a
        hard‑coded town/city.  For generic buildings whose name starts with a
        town/city (e.g. "VIRIDIAN POKECENTER") we strip the building type and
        append the proper suffix ("City"/"Town") when possible.
        """

        # Direct overrides first
        if current_location in self._INTERIOR_DEST_OVERRIDES:
            return self._INTERIOR_DEST_OVERRIDES[current_location]

        tokens = current_location.split()
        if not tokens:
            return None

        first = tokens[0].capitalize()

        # Known town/city keywords to help choose suffix
        towns = {
            "Pallet": "Town",
            "Lavender": "Town",
            "Viridian": "City",
            "Pewter": "City",
            "Cerulean": "City",
            "Vermilion": "City",
            "Celadon": "City",
            "Fuchsia": "City",
            "Saffron": "City",
            "Cinnabar": "Island",
            "Indigo": "Plateau",
        }

        if first in towns:
            return f"{first} {towns[first]}"

        # If the location name already ends with City/Town/etc. don't modify
        if tokens[-1] in {"Town", "City", "Island", "Plateau", "Route"}:
            return current_location

        return None

    def _get_doors_info(self) -> list[tuple[str | None, tuple[int, int]]]:
        """Return a list of visible warps using the game's warp table.

        Each entry is ``(destination_name_or_None, (row, col))`` where
        ``row`` and ``col`` are the absolute map‑tile coordinates read
        directly from WRAM.  Because these come from ``wWarpEntries`` they do
        **not** depend on the camera and therefore never jitter.
        """

        # ------------------------------------------------------------------
        # 1. Read warp entries for this map from WRAM
        # ------------------------------------------------------------------

        # ------------------------------------------------------------------
        # Fallback to viewport tile‑scan (stable & working)
        # ------------------------------------------------------------------
        # Use ONLY the 18×20 viewport that is currently visible on‑screen so
        # we never report off‑screen doors.  This makes the coordinate system
        # match exactly what the player sees and what the collision/overlay
        # map shows.
        full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()

        doors: list[tuple[int, int]] = []  # store down‑sampled cell coords
        
        # print(f"environment.py: _get_doors_info(): full_map: {full_map}")

        # --------------------------------------------------------------
        # Exact 2×2 pattern whitelist (UL, UR, LL, LR).  None = wildcard.
        # --------------------------------------------------------------
        PATTERNS = [
            # Exterior house / lab door
            (0x0B, 0x0C, 0x1B, 0x1C),
            # Player house staircase bottom
            (0x34, 0x1E, 0x34, 0x1F),
            # Interior single door (UR wildcard, lower row wildcard)
            (0x4F, 0x4E, None, None),
        ]

        def match_block(tl, tr, bl, br):
            for a, b, c_, d in PATTERNS:
                if (a is None or tl == a) and (b is None or tr == b) and (
                    c_ is None or bl == c_
                ) and (d is None or br == d):
                    return True
            return False

        # Screen viewport fixed 18×20; iterate by 2×2 blocks
        for base_r in range(0, 18, 2):
            for base_c in range(0, 20, 2):
                if base_r + 1 >= 18 or base_c + 1 >= 20:
                    continue

                tl = full_map[base_r][base_c] & 0xFF
                tr = full_map[base_r][base_c + 1] & 0xFF
                bl = full_map[base_r + 1][base_c] & 0xFF
                br = full_map[base_r + 1][base_c + 1] & 0xFF

                if not match_block(tl, tr, bl, br):
                    continue

                ds_r, ds_c = base_r // 2, base_c // 2
                doors.append((ds_r, ds_c, tl))

        # De‑duplicate by down‑sampled coordinates (2×2 => 1 block)
        # Log raw door positions with tile IDs for debugging
        if logger.isEnabledFor(logging.DEBUG):
            try:
                logger.debug(
                    "[DoorDetect] door tile list (row,col,tileHex): "
                    + str([(r, c, hex(full_map[r][c] & 0xFF)) for r, c in doors])
                )
            except Exception:
                pass

        # Log full 18×20 background tile hex grid for manual pattern work
        full_dump = [
            " ".join(hex(t & 0xFF)[2:].upper().zfill(2) for t in row)
            for row in full_map
        ]
        # Verbose full‑map dump can flood logs; keep it at DEBUG level.
        # print(f"[DoorDetect] full 18x20 background tile IDs:\n" + "\n".join(full_dump))

        # Down‑sampled coordinate → tile_id for every warp tile we found.
        # Using only the warp tile list already removes door tops, so no extra
        # filtering is necessary.
        unique_coords: dict[tuple[int, int], int] = {}
        unique_coords: dict[tuple[int, int], int] = {}
        for ds_r, ds_c, tid in doors:
            unique_coords[(ds_r, ds_c)] = tid

        # Validate each down‑sampled 2×2 block: keep it *only* if it contains
        # the canonical stair/door warp tile **and** a matching graphic from
        # the same staircase pair.  For interior staircases that means one of
        # the top‑half graphics 0x1E/0x1F together with bottom warp 0x34.  This
        # removes stray 0x34 tiles that appear elsewhere in furniture.

        def has_stair_pattern(ds_r: int, ds_c: int) -> bool:
            base_r, base_c = ds_r * 2, ds_c * 2
            if base_r + 1 >= 18 or base_c + 1 >= 20:
                return False
            tiles = {
                full_map[base_r][base_c] & 0xFF,
                full_map[base_r][base_c + 1] & 0xFF,
                full_map[base_r + 1][base_c] & 0xFF,
                full_map[base_r + 1][base_c + 1] & 0xFF,
            }
            # Require warp tile and at least one stair‑top tile
            return 0x34 in tiles and bool(tiles & {0x1E, 0x1F})

        unique_coords = {
            (ds_r, ds_c): tid
            for (ds_r, ds_c), tid in unique_coords.items()
            if (
                # if warp tile is 0x34 we require full pattern; for other warp
                # ids we keep them as is (single‑tile cave exits, doors etc.)
                (tid != 0x34) or has_stair_pattern(ds_r, ds_c)
            )
        }

        # ---------- diagnostic logging ---------------------------------------------------
        # Log both the raw (row,col) positions and the hex tile IDs that survived the
        # filtering so false positives are easy to spot in the runtime logs.

        if doors:
            raw_with_hex = [(r, c, hex(tid)) for r, c, tid in doors]
            kept_with_hex = [
                (ds_r, ds_c, hex(tid)) for (ds_r, ds_c), tid in unique_coords.items()
            ]
            logger.debug(
                "[DoorDetect] found %d warp‑candidate tiles in %s | raw=%s kept=%s",
                len(doors),
                self.get_location(),
                raw_with_hex,
                kept_with_hex,
            )
        else:
            logger.debug("[DoorDetect] found 0 warp tiles in %s", self.get_location())

        # Diagnostic: dump down‑sampled 9×10 background tile IDs (bottom‑left of
        # each 2×2 block) so we can compare with collision map coordinates.
        try:
            ds_rows = []
            for ds_r in range(9):
                row_ids = []
                for ds_c in range(10):
                    tile_id = full_map[ds_r * 2 + 1][ds_c * 2] & 0xFF
                    row_ids.append(hex(tile_id)[2:].upper().zfill(2))
                ds_rows.append(" ".join(row_ids))
            print("[DoorDetect] down‑sampled 9x10 tile IDs:\n" + "\n".join(ds_rows))
        except Exception:
            pass

        # Extra diagnostic: if none found, log tile IDs at player column across
        # the bottom six rows to help identify unknown door tiles.
        if not doors:
            try:
                full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
                player_r, player_c = self._get_player_center(full_map)
                sample = [full_map[r][player_c] for r in range(12, 18)]
                print(
                    f"[DoorDetect] sampling column {player_c} rows 12‑17 tile IDs: {sample}"
                )

                # Dump the entire 18×20 tile id grid once (compact)
                grid_flat = [hex(t)[2:].upper().zfill(2) for row in full_map for t in row]
                rows_str = [" ".join(grid_flat[i * 20 : (i + 1) * 20]) for i in range(18)]
                print("[DoorDetect] full 18x20 background tilemap:\n" + "\n".join(rows_str))
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Convert to on‑screen 9×10 coordinates; include only doors visible in
        # the current viewport so the numbers stay meaningful for navigate_to.
        # ------------------------------------------------------------------

        # We now convert each unique down‑sampled cell to world‑tile
        # coordinates using the player's position as the origin.

        try:
            player_row, player_col = self.get_coordinates()
        except Exception:
            player_row = player_col = 0

        location_name = self.get_location() or ""
        dest_guess = self._infer_door_destination(location_name)

        visible_doors: list[tuple[str | None, tuple[int, int]]] = []

        for (ds_r, ds_c), tid in unique_coords.items():
            delta_cells_r = ds_r - 4  # relative to player cell (4,4)
            delta_cells_c = ds_c - 4

            # In this emulator build each 9×10 cell corresponds to **one**
            # world tile (not two) because the player position is restricted
            # to whole‑tile increments that line up with the down‑sampled
            # grid.  Therefore apply the delta in cells directly.
            world_r = player_row + delta_cells_r
            world_c = player_col + delta_cells_c

            # Fine‑grained destination override for staircase tiles inside
            # the player's house so the prompt doesn't claim they lead to
            # Pallet Town.
            # If this warp is a staircase (tile 0x34) we cannot reliably infer
            # its destination from the location name heuristic, so omit the
            # label rather than risk a misleading "Pallet Town" message.
            dest_final = None if tid == 0x34 else dest_guess
            visible_doors.append((dest_final, (world_r, world_c)))

        # ------------------------------------------------------------------
        # Extra diagnostic: dump the exact 2×2 blocks that generated each
        # down‑sampled cell we report as a door so it is easy to curate the
        # warp‑tile list.
        # ------------------------------------------------------------------

        if visible_doors and logger.isEnabledFor(logging.DEBUG):
            blocks_info: list[str] = []
            # Skip detailed 2×2 dump in world‑coord mode – not easily mapped.
            pass

        # print(f"[DoorDetect] visible_doors={visible_doors}")
        return visible_doors

    # ------------------------------------------------------------------
    # Diagnostics helpers for SimpleAgent logging
    # ------------------------------------------------------------------

    def _screen_origin(self) -> tuple[int, int]:
        """Return (cam_row, cam_col) world‑tile coordinates of viewport top‑left."""
        try:
            player_row, player_col = self.get_coordinates()
        except Exception:
            return (0, 0)
        return (player_row - 9, player_col - 8)

    def tile_hex_at(self, world_row: int, world_col: int) -> str | None:
        """Return background tile hex at given world coords if visible."""
        cam_row, cam_col = self._screen_origin()
        r = world_row - cam_row
        c = world_col - cam_col
        if 0 <= r < 18 and 0 <= c < 20:
            tile = self.pyboy.game_wrapper._get_screen_background_tilemap()[r][c] & 0xFF
            return hex(tile)[2:].upper().zfill(2)
        return None

    def block_hex_at(self, world_row: int, world_col: int) -> list[str]:
        """Return list of 4 hex tile IDs of the 2×2 block containing world tile."""
        cam_row, cam_col = self._screen_origin()
        r = world_row - cam_row
        c = world_col - cam_col
        if not (0 <= r < 18 and 0 <= c < 20):
            return []
        base_r = (r // 2) * 2
        base_c = (c // 2) * 2
        full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
        tiles = [
            full_map[base_r][base_c] & 0xFF,
            full_map[base_r][base_c + 1] & 0xFF if base_c + 1 < 20 else 0,
            full_map[base_r + 1][base_c] & 0xFF if base_r + 1 < 18 else 0,
            full_map[base_r + 1][base_c + 1] & 0xFF if base_r + 1 < 18 and base_c + 1 < 20 else 0,
        ]
        return [hex(t)[2:].upper().zfill(2) for t in tiles]

    def find_path(self, target_row: int, target_col: int) -> tuple[str, list[str]]:
        """
        Finds the most efficient path from the player's current position (4,4) to the target position.
        If the target is unreachable, finds path to nearest accessible spot.
        Allows ending on a wall tile if that's the target.
        Takes into account terrain, sprite collisions, and tile pair collisions.

        Args:
            target_row: Row index in the 9x10 downsampled map (0-8)
            target_col: Column index in the 9x10 downsampled map (0-9)

        Returns:
            tuple[str, list[str]]: Status message and sequence of movements
        """
        # Get collision map, terrain, and sprites
        collision_map = self.pyboy.game_wrapper.game_area_collision()
        terrain = self._downsample_array(collision_map)
        sprite_locations = self.get_sprites()

        # Get full map for tile values and current tileset
        full_map = self.pyboy.game_wrapper._get_screen_background_tilemap()
        tileset = self.read_tileset()

        # Start at player position (always 4,4 in the 9x10 grid)
        start = (4, 4)
        end = (target_row, target_col)

        # Validate target position
        if not (0 <= target_row < 9 and 0 <= target_col < 10):
            return "Invalid target coordinates", []

        # A* algorithm
        def heuristic(a, b):
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        open_set = []
        heapq.heappush(open_set, (0, start))
        came_from = {}
        g_score = {start: 0}
        f_score = {start: heuristic(start, end)}

        # Track closest reachable point
        closest_point = start
        min_distance = heuristic(start, end)

        def reconstruct_path(current):
            path = []
            while current in came_from:
                prev = came_from[current]
                if prev[0] < current[0]:
                    path.append("down")
                elif prev[0] > current[0]:
                    path.append("up")
                elif prev[1] < current[1]:
                    path.append("right")
                else:
                    path.append("left")
                current = prev
            path.reverse()
            return path

        while open_set:
            _, current = heapq.heappop(open_set)

            # Check if we've reached target
            if current == end:
                path = reconstruct_path(current)
                is_wall = terrain[end[0]][end[1]] == 0
                if is_wall:
                    return (
                        f"Partial Success: Your target location is a wall. In case this is intentional, attempting to navigate there.",
                        path,
                    )
                else:
                    return (
                        f"Success: Found path to target at ({target_row}, {target_col}).",
                        path,
                    )

            # Track closest point
            current_distance = heuristic(current, end)
            if current_distance < min_distance:
                closest_point = current
                min_distance = current_distance

            # If we're next to target and target is a wall, we can end here
            if (abs(current[0] - end[0]) + abs(current[1] - end[1])) == 1 and terrain[
                end[0]
            ][end[1]] == 0:
                path = reconstruct_path(current)
                # Add final move onto wall
                if end[0] > current[0]:
                    path.append("down")
                elif end[0] < current[0]:
                    path.append("up")
                elif end[1] > current[1]:
                    path.append("right")
                else:
                    path.append("left")
                return (
                    f"Success: Found path to position adjacent to wall at ({target_row}, {target_col}).",
                    path,
                )

            # Check all four directions
            for dr, dc, direction in [
                (1, 0, "down"),
                (-1, 0, "up"),
                (0, 1, "right"),
                (0, -1, "left"),
            ]:
                neighbor = (current[0] + dr, current[1] + dc)

                # Check bounds
                if not (0 <= neighbor[0] < 9 and 0 <= neighbor[1] < 10):
                    continue
                # Skip walls unless it's the final destination
                if terrain[neighbor[0]][neighbor[1]] == 0 and neighbor != end:
                    continue
                # Skip sprites unless it's the final destination
                if (neighbor[1], neighbor[0]) in sprite_locations and neighbor != end:
                    continue

                # Check tile pair collisions
                # Get bottom-left tile of each 2x2 block
                current_tile = full_map[current[0] * 2 + 1][
                    current[1] * 2
                ]  # Bottom-left tile of current block
                neighbor_tile = full_map[neighbor[0] * 2 + 1][
                    neighbor[1] * 2
                ]  # Bottom-left tile of neighbor block
                if not self._can_move_between_tiles(
                    current_tile, neighbor_tile, tileset
                ):
                    continue

                tentative_g_score = g_score[current] + 1
                if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g_score
                    f_score[neighbor] = tentative_g_score + heuristic(neighbor, end)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))

        # If target unreachable, return path to closest point
        if closest_point != start:
            path = reconstruct_path(closest_point)
            return (
                f"Partial Success: Could not reach the exact target, but found a path to the closest reachable point.",
                path,
            )

        return (
            "Failure: No path is visible to the chosen location. You may need to explore a totally different path to get where you're trying to go.",
            [],
        )    
    
    def get_screenshot_with_overlay(self, alpha=128):
        """
        Get the current screenshot with a tile overlay showing walkable/unwalkable areas.
        
        Args:
            alpha (int): Transparency value for the overlay (0-255)
            
        Returns:
            PIL.Image: Screenshot with tile overlay
        """
        try:
            # FIXED: Clear cached warp data to ensure fresh collision detection
            self.clear_warp_cache()
            
            screenshot = self.get_screenshot()
            collision_map_str = self.get_collision_map()
            
            # Ensure we have valid data
            if not collision_map_str or not isinstance(collision_map_str, str):
                print(f"Environment: Warning - Invalid collision map data: {type(collision_map_str)}")
                return screenshot
            
            return overlay_on_screenshot(screenshot, collision_map_str, alpha)
        except Exception as e:
            print(f"Environment: Error creating collision overlay: {e}")
            # Return regular screenshot if overlay fails
            return self.get_screenshot()
    
    def read_player_name(self) -> str:
        """Read the player's name"""
        name_bytes = self.memory[0xD158:0xD163]
        return self._convert_text(name_bytes)

    def read_rival_name(self) -> str:
        """Read rival's name"""
        name_bytes = self.memory[0xD34A:0xD351]
        return self._convert_text(name_bytes)
    
    def get_state_from_memory(self) -> str:
        """
        Reads the game state from memory and returns a string representation of it.
        """
        memory_str = "# Current Game State\n\nThis information is direct from the emulator at the present moment along with your screenshot. Use the information below to make decisions about what to do and where to go next.\n\n"

        name = self.read_player_name()
        if name == "NINTEN":
            name = "Not yet set"
        rival_name = self.read_rival_name()
        if rival_name == "SONY":
            rival_name = "Not yet set"

        # Get valid moves
        valid_moves = self.get_valid_moves()
        valid_moves_str = ", ".join(valid_moves) if valid_moves else "None"

        # Present each field as a clear bullet for easier parsing by the LLM
        memory_str += f"- Player: {name}\n"
        # memory_str += f"- Rival: {rival_name}\n"
        memory_str += f"- Money: ${self.item_handler.read_money()}\n"
        memory_str += f"- Current Environment: {self.read_location()}\n"
        memory_str += f"- Coordinates: {self.get_game_coords()}\n"

        memory_str += f"Badges: {', '.join(self.item_handler.get_badges())}\n"

        # Inventory
        memory_str += "Inventory:\n"
        for item, qty in self.item_handler.get_items_quantity_in_bag():
            memory_str += f"  {item} x{qty}\n"

        # Dialog
        dialog = self.read_dialog()
        if dialog:
            memory_str += f"Dialog: {dialog}\n"
        else:

            memory_str += "Dialog: None\n"

        # --------------------------------------------------------------
        # Door / warp hints (experimental)
        # --------------------------------------------------------------
        door_info = self._get_doors_info()
        if door_info:
            memory_str += (
                "\n# Available Doors And Warps\n\n"
                "Here is the list of doors/warps visible in this environment and their coordinates. "
                "You can navigate to one by calling navigate_to with that (row, col) or by manually pressing D‑pad moves until you reach it.\n"
            )
            for dest, (x, y) in door_info:
                if dest:
                    memory_str += f"- Visible Door, Stairs, or Warp located at ({x}, {y})\n"
                else:
                    memory_str += f"- Door / warp at ({x}, {y})\n"

        # Party Pokemon
        memory_str += "\nPokemon Party:\n"
        for pokemon in self.read_party_pokemon():
            memory_str += f"\n{pokemon.nickname} ({pokemon.species_name}):\n"
            memory_str += f"Level {pokemon.level} - HP: {pokemon.current_hp}/{pokemon.max_hp}\n"
            memory_str += f"Types: {pokemon.type1.name}{', ' + pokemon.type2.name if pokemon.type2 else ''}\n"
            for move, pp in zip(pokemon.moves, pokemon.move_pp, strict=True):
                memory_str += f"- {move} (PP: {pp})\n"
            if pokemon.status != StatusCondition.NONE:
                memory_str += f"Status: {pokemon.status.get_status_name()}\n"

        return memory_str

    def stop(self):
        self.pyboy.stop()
        
    def handle_oak_dialog(self):
        dialog = self.read_dialog()
        print(f"handle_oak_dialog: self.never_run_again: {self.never_run_again}")
        if "J K L M N O P" in dialog:
            self.never_run_again = True
            print(f"Environment: NEW NAME dialog detected, setting never_run_again to True")
        

    def process_action(self, action: int, source: str = "unknown") -> tuple:
        """
        Single entry point for ALL actions - no exceptions!
        
        Args:
            action: Action ID (0-7)
            source: String identifier of action source for debugging
            
        Returns:
            tuple: (obs, reward, done, truncated, info)
        """
        # Run through stage_helper StageManager
        
        
        
        # Log source for debugging
        print(f"UNIFIED_ACTION: Source={source}, Action={action}, Step={self.step_count}")
        
        # Always call step() - no shortcuts, no exceptions
        return self.step(action)

    def load_coordinate_path(self, quest_id: int) -> bool:
        """
        ENVIRONMENT COMPATIBILITY: Load quest coordinates via navigator and synchronize.
        This method exists for backward compatibility with code that expects the environment
        to have coordinate loading capability. All actual coordinate loading is done by the navigator.
        
        Args:
            quest_id: Quest ID to load
            
        Returns:
            bool: True if coordinates loaded successfully
        """
        print(f"Environment: Loading quest {quest_id} coordinates via navigator")
        
        # Delegate to navigator
        if hasattr(self, 'navigator') and self.navigator:
            success = self.navigator.load_coordinate_path(quest_id)
            
            if success:
                # Synchronize environment attributes for backward compatibility
                self.combined_path = self.navigator.sequential_coordinates.copy()
                self.current_path_target_index = self.navigator.current_coordinate_index
                self.current_loaded_quest_id = quest_id
                
                print(f"Environment: Synchronized {len(self.combined_path)} coordinates from navigator")
                return True
            else:
                print(f"Environment: Navigator failed to load quest {quest_id}")
                return False
        else:
            print(f"Environment: No navigator available to load quest {quest_id}")
            return False

    def get_screenshot_with_overlay(self, alpha=128):
        """
        Get the current screenshot with a tile overlay showing walkable/unwalkable areas.
        
        Args:
            alpha (int): Transparency value for the overlay (0-255)
            
        Returns:
            PIL.Image: Screenshot with tile overlay
        """
        try:
            # FIXED: Clear cached warp data to ensure fresh collision detection
            self.clear_warp_cache()
            
            screenshot = self.get_screenshot()
            collision_map_str = self.get_collision_map()
            
            # Ensure we have valid data
            if not collision_map_str or not isinstance(collision_map_str, str):
                print(f"Environment: Warning - Invalid collision map data: {type(collision_map_str)}")
                return screenshot
            
            return overlay_on_screenshot(screenshot, collision_map_str, alpha)
        except Exception as e:
            print(f"Environment: Error creating collision overlay: {e}")
            # Return regular screenshot if overlay fails
            return self.get_screenshot()

    def format_battle_state(self) -> str:
        """
        Format battle-specific state with comprehensive metrics for LLM processing.
        Only observes and reports state - does NOT execute any actions.
        """
        state_parts = ["## CURRENT BATTLE STATE ##"]
        
        # Check if battle just finished
        if getattr(self, 'battle_just_finished', False):
            state_parts.append("BATTLE JUST FINISHED. YOU ARE NOW IN THE OVERWORLD.")
            return "\n".join(state_parts)
        
        # Battle type detection
        battle_status = self.read_m(0xD057)
        is_wild = battle_status == 1
        is_trainer = battle_status == 2
        
        # Include active dialog
        dialog = self.read_dialog()
        if dialog:
            dialog_text = dialog.replace('\n', ' ')[:500]
            state_parts.append(f"BATTLE DIALOG: {dialog_text}")
        else:
            state_parts.append("BATTLE DIALOG: None visible")
        
        # Battle type
        if is_wild:
            state_parts.append("BATTLE TYPE: Wild Pokémon")
        elif is_trainer:
            state_parts.append("BATTLE TYPE: Trainer Battle")
        
        # Location and basic status
        state_parts.append(f"\nCurrent Location: {self.get_game_coords()}")
        
        # Party information
        try:
            party_data = self.read_party_pokemon()
            if party_data and len(party_data) > 0:
                active_pokemon = party_data[0]
                species_name = ID_TO_SPECIES.get(active_pokemon.species_id, f"Species_{active_pokemon.species_id}")
                
                state_parts.append(f"\nACTIVE POKÉMON:")
                state_parts.append(f"- {species_name}, Level {active_pokemon.level}")
                state_parts.append(f"- HP: {active_pokemon.current_hp}/{active_pokemon.max_hp}")
                state_parts.append(f"- Status: {active_pokemon.status.name if hasattr(active_pokemon.status, 'name') else 'OK'}")
                
                # Moves with PP
                state_parts.append("\nMOVES:")
                has_any_pp = False
                has_damaging_pp = False
                
                for i, move_name in enumerate(active_pokemon.moves):
                    if i < len(active_pokemon.move_pp):
                        pp = active_pokemon.move_pp[i]
                        max_pp = 25  # Default max PP
                        state_parts.append(f"- Move {i+1}: {move_name} ({pp}/{max_pp} PP)")
                        
                        if pp > 0:
                            has_any_pp = True
                            # Check if it's a damaging move
                            move_info = MOVES_INFO_DICT.get(move_name.replace(" ", "_").upper(), {})
                            if move_info.get("power", 0) > 0:
                                has_damaging_pp = True
                
                # PP warnings
                if not has_any_pp:
                    state_parts.append("\n⚠️ WARNING: No PP remaining on any moves!")
                    if is_wild:
                        state_parts.append("Recommendation: Run from battle")
                    else:
                        state_parts.append("Recommendation: Use Struggle (will damage self)")
                elif not has_damaging_pp:
                    state_parts.append("\n⚠️ WARNING: No PP on damaging moves!")
                
                # Check other party members
                if len(party_data) > 1:
                    healthy_pokemon = sum(1 for p in party_data[1:] if p.current_hp > 0)
                    if healthy_pokemon > 0:
                        state_parts.append(f"\nOther Pokémon available: {healthy_pokemon}")
                
        except Exception as e:
            state_parts.append(f"\nError reading party data: {e}")
        
        # Enemy Pokémon info (if available)
        try:
            enemy_types = self.read_enemy_current_pokemon_types()
            if enemy_types[0]:
                state_parts.append(f"\nENEMY POKÉMON:")
                state_parts.append(f"- Type: {enemy_types[0].name}{f'/{enemy_types[1].name}' if enemy_types[1] else ''}")
        except:
            pass
        
        # Inventory summary
        state_parts.append("\nINVENTORY:")
        healing_items = getattr(self, 'battle_bag_healing_items', [])
        ball_items = getattr(self, 'battle_bag_balls', [])
        
        if healing_items:
            state_parts.append(f"- Healing: {', '.join(f'{name} x{qty}' for name, qty in healing_items[:3])}")
        if ball_items and is_wild:
            state_parts.append(f"- Poké Balls: {', '.join(f'{name} x{qty}' for name, qty in ball_items[:3])}")
        
        # Battle statistics
        state_parts.append(f"\nBattle Turn Count: {getattr(self, 'battle_turn_count', 0)}")
        
        # Menu position hint
        if dialog and "►" in dialog:
            if "►FIGHT" in dialog:
                state_parts.append("\nMENU: Main battle menu (FIGHT selected)")
            elif any(marker in dialog for marker in ["►POTION", "►POKÉBALL", "►ITEM"]):
                state_parts.append("\nMENU: Item selection menu")
            elif "PP" in dialog and "/" in dialog:
                state_parts.append("\nMENU: Move selection menu")
        
        return "\n".join(state_parts)



    def read_enemy_current_pokemon_types(self) -> tuple[PokemonType, PokemonType | None]:
        """Read primary and secondary types of the current battling enemy Pokemon"""
        type1_val = self.memory[ENEMYS_POKEMON_TYPES[0]]
        type2_val = self.memory[ENEMYS_POKEMON_TYPES[1]]
        type1 = PokemonType(type1_val)
        type2 = PokemonType(type2_val)
        if type1 == type2:
            type2 = None
        return type1, type2
    
    def read_enemy_current_pokemon_types(self) -> tuple[PokemonType, PokemonType | None]:
        """Read primary and secondary types of the current battling enemy Pokemon"""
        type1_val = self.memory[ENEMYS_POKEMON_TYPES[0]]
        type2_val = self.memory[ENEMYS_POKEMON_TYPES[1]]
        type1 = PokemonType(type1_val)
        type2 = PokemonType(type2_val)
        if type1 == type2:
            type2 = None
        return type1, type2
    
    def choose_best_battle_move(self):
        """Determine the best move using actual move data from memory for any Pokémon.
        IMPORTANT: THE FIGHT BATTLE MENU MUST HAVE ALREADY BEEN SELECTED BEFORE CALLING THIS FUNCTION"""
        logger.info("choose_best_battle_move: calculating move effectiveness generically")
        if "TYPE/" not in self.get_active_dialog():
            logger.info("environment.py: choose_best_battle_move: no TYPE/ in dialog; not in FIGHT menu!")
            attempts = 0
            dialog = self.get_active_dialog()
            while "FIGHT" not in dialog and attempts < 10:
                print("pressing B to advance dialog...dialog=", dialog)
                # Press B to advance dialog
                self.pyboy.send_input(WindowEvent.PRESS_BUTTON_B)
                self.pyboy.tick(9)
                self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_B)
                self.pyboy.tick(15)
                
                dialog = self.get_active_dialog() or ""
                attempts += 1

            # Move cursor to FIGHT option
            print("moving cursor to FIGHT option...dialog=", dialog)
            for _ in range(4):
                self.pyboy.send_input(WindowEvent.PRESS_ARROW_UP)
                self.pyboy.tick(9)
                self.pyboy.send_input(WindowEvent.RELEASE_ARROW_UP)
                self.pyboy.tick(15)
                time.sleep(0.5)
                self.pyboy.send_input(WindowEvent.PRESS_ARROW_LEFT)
                self.pyboy.tick(9)
                self.pyboy.send_input(WindowEvent.RELEASE_ARROW_LEFT)
                self.pyboy.tick(15)
                time.sleep(0.5)
            
            
            # Select FIGHT option
            # print("selecting FIGHT option...dialog=", dialog)
            self.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
            self.pyboy.tick(9)
            self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
            self.pyboy.tick(15)
            time.sleep(0.5)            
        
        # Read enemy types
        try:
            primary_type, secondary_type = self.read_enemy_current_pokemon_types()
            primary_val = primary_type.value if isinstance(primary_type, PokemonType) else primary_type
            secondary_val = secondary_type.value if secondary_type else None
            logger.info(f"[BattleAI] Enemy types: primary={primary_val}, secondary={secondary_val}")
        except Exception as e:
            logger.error(f"[BattleAI] Failed to read enemy types: {e}")
            return 0
        # Get active Pokémon data
        party = self.read_party_pokemon()
        if not party:
            logger.error("[BattleAI] No party data available")
            return 0
        active = party[0]
        num_moves = len(active.moves)
        # print(f"num_moves={num_moves}")
        if num_moves == 0:
            return 0
        # Reset cursor to first move (up arrow)
        # print(f"resetting cursor to first move...num_moves={num_moves}")
        # dialog = self.get_active_dialog()

        best_index = 0
        best_score = -1.0
        # Evaluate each move
        for idx in range(num_moves):
            if idx > 0:
                self.run_action_on_emulator(VALID_ACTIONS[0])  # down
                self.pyboy.tick(9)
                time.sleep(0.5)
            power = self.read_m(PLAYERS_MOVE_POWER)
            mtype = self.read_m(PLAYERS_MOVE_TYPE)
            # Skip non-damaging or no PP
            if power <= 0 or (idx < len(active.move_pp) and active.move_pp[idx] <= 0):
                continue
            m1 = POKEMON_MATCH_TYPES.get((mtype, primary_val), 1.0)
            m2 = POKEMON_MATCH_TYPES.get((mtype, secondary_val), 1.0) if secondary_val is not None else 1.0
            score = power * m1 * m2
            logger.info(f"[BattleAI] Move {idx}: power={power}, type={mtype}, score={score}")
            if score > best_score:
                best_score = score
                best_index = idx
        print(f"[BattleAI] Selected move index: {best_index} with score {best_score}")
        # store best move in instance variable
        self.best_battle_move = best_index
        # select the move: diff = (N-1) – best_index, then move in opposite direction diff times
        print(f"[BattleAI] Selecting move {best_index}...")
        for _ in range(best_index):
            self.run_action_on_emulator(VALID_ACTIONS[3])  # up
            self.pyboy.tick(9)
            time.sleep(3.5) 
        
        # self.run_action_on_emulator(VALID_ACTIONS[4])  # a
        
        return best_index

    def read_party_size(self) -> int:
        """Read number of Pokemon in party"""
        return self.memory[0xD163]

    def read_party_pokemon(self) -> list[PokemonData]:
        """Read all Pokemon currently in the party with full data"""
        # Use the PartyMons struct loaded in reset for reliable offsets
        party_list: list[PokemonData] = []
        for struct in self.party.party[: self.party_size]:
            # Extract raw moves and PP from the struct
            moves = [Move(mid).name.replace("_", " ") for mid in struct.Moves if mid != 0]
            move_pp = list(struct.PP[: len(moves)])

            # Assemble PokemonData from struct fields
            experience = (struct.Exp[0] << 16) + (struct.Exp[1] << 8) + struct.Exp[2]
            species_id = struct.Species
            species_name = Species(species_id).name.replace("_", " ")
            status_val = struct.Status
            type1 = PokemonType(struct.Type1)
            type2 = PokemonType(struct.Type2)
            if type1 == type2:
                type2 = None
            pokemon = PokemonData(
                species_id=species_id,
                species_name=species_name,
                current_hp=struct.HP,
                max_hp=struct.MaxHP,
                level=struct.Level,
                status=StatusCondition(status_val),
                type1=type1,
                type2=type2,
                moves=moves,
                move_pp=move_pp,
                trainer_id=struct.OTID,
                nickname="",
                experience=experience,
            )
            party_list.append(pokemon)
        return party_list

    def get_collision_map_markdown(self):
        """
        Return a Markdown table-based mini-map of the current 9×10 meta-tile
        collision grid, similar in spirit to the example provided in the
        documentation.

        The mini-map includes:
        • Column headers reflecting the player-centred X coordinates
        • Row headers reflecting the player-centred Y coordinates
        • A cell-level symbol plus coordinate for quick inspection
        • A legend explaining all symbols used
        """
        # ------------------------------------------------------------------
        # Build the numeric 9×10 grid exactly as in get_collision_map()
        # ------------------------------------------------------------------
        full_map = self.pyboy.game_area()
        collision_map = self.pyboy.game_area_collision()
        downsampled_terrain = self._downsample_array(collision_map)
        sprite_locations = self.get_sprites()
        direction = self._get_direction(full_map)
        if direction == "no direction found":
            return "(Unable to determine player direction – cannot build minimap)"

        tileset = self.read_tileset()
        full_tilemap = self.pyboy.game_wrapper._get_screen_background_tilemap()
        dir_codes = {"up": 3, "down": 4, "left": 5, "right": 6}
        player_code = dir_codes.get(direction, 3)

        grid = []
        for i in range(9):
            row = []
            for j in range(10):
                if i == 4 and j == 4:
                    row.append(player_code)
                elif (j, i) in sprite_locations:
                    row.append(2)
                else:
                    walkable = False
                    if downsampled_terrain[i][j] != 0:
                        current_tile = full_tilemap[i * 2 + 1][j * 2]
                        player_tile = full_tilemap[9][8]
                        if self._can_move_between_tiles(player_tile, current_tile, tileset):
                            walkable = True
                    row.append(0 if walkable else 1)
            grid.append(row)

        # ------------------------------------------------------------------
        # Convert to Markdown table
        # ------------------------------------------------------------------
        symbol_map = {
            0: "@",  # Free / walkable ground
            1: "X",  # Collision / wall
            2: "👾",  # NPC / sprite
            3: "🧍↑",  # Player facing up
            4: "🧍↓",  # Player facing down
            5: "🧍←",  # Player facing left
            6: "🧍→",  # Player facing right
        }

        # Player world coordinates (row, col)
        world_row, world_col, _ = self.get_game_coords()
        header_x_vals = [world_col - 4 + i for i in range(10)]

        md_lines = []
        md_lines.append("--- Visible Area ---")
        md_lines.append("## Visible Game Area (9x10 Meta-Tiles)")
        md_lines.append(f"Player Position (Local position): X={world_col}, Y={world_row}")
        md_lines.append("")

        # Header row
        header_row = "|  Y \\ X  | " + " | ".join(str(x) for x in header_x_vals) + " |"
        separator_row = "| --- " + "| --- " * len(header_x_vals) + "|"
        md_lines.append(header_row)
        md_lines.append(separator_row)

        # Body rows
        for i, row in enumerate(grid):
            y_val = world_row - 4 + i
            cells = []
            for j, code in enumerate(row):
                symbol = symbol_map.get(code, "?")
                cell = f"{symbol} ({header_x_vals[j]}x{y_val})"
                cells.append(cell)
            md_lines.append("| " + str(y_val) + " | " + " | ".join(cells) + " |")

        # Legend
        md_lines.append("")
        md_lines.append("### Map Legend (Visible Area)")
        md_lines.extend([
            "- 🧍→ : Player (Facing Right)",
            "- 🧍← : Player (Facing Left)",
            "- 🧍↑ : Player (Facing Up)",
            "- 🧍↓ : Player (Facing Down)",
            "- X : Collision/Impassable",
            "- @ : Free Ground",
            "- 👾 : Sprite (NPC)",
        ])
        return "\n".join(md_lines)

    def get_menu_state(self):
        """Detect the currently active menu state.

        Returns an Enum value from RedRamMenuValues / RedRamSubMenuValues
        indicating which menu item is currently highlighted.  Falls back to
        RedRamMenuValues.UNKNOWN_MENU if the cursor location is not mapped.
        """
        # Read the current text-menu cursor X,Y location from memory
        cursor_x = self.read_m(TEXT_MENU_CURSOR_LOCATION[0])
        cursor_y = self.read_m(TEXT_MENU_CURSOR_LOCATION[1])
        cursor_key = (cursor_x, cursor_y)

        # 1. Check main & battle menus first
        if cursor_key in TEXT_MENU_CURSOR_LOCATIONS:
            val = TEXT_MENU_CURSOR_LOCATIONS[cursor_key]
            return val, val.name

        # 2. Check PC Pokémon sub-menus
        if cursor_key in PC_POKE_MENU_CURSOR_LOCATIONS:
            val = PC_POKE_MENU_CURSOR_LOCATIONS[cursor_key]
            return val, val.name

        # 3. Check PC item sub-menus
        if cursor_key in PC_ITEM_MENU_CURSOR_LOCATIONS:
            val = PC_ITEM_MENU_CURSOR_LOCATIONS[cursor_key]
            return val, val.name

        # 4. If in battle, but cursor_key not mapped, return generic battle state
        if self.read_m("wIsInBattle"):
            # If we are in battle but cursor not mapped, provide UNKNOWN but mark battle
            val = (
                RedRamMenuValues.BATTLE_MENU_FIGHT
                if cursor_key in BATTLE_MENU_STATES
                else RedRamMenuValues.UNKNOWN_MENU
            )
            return val, val.name

        # Default – unknown menu
        val = RedRamMenuValues.UNKNOWN_MENU
        return val, val.name
    
    def _is_pokecenter(self, map_id: int) -> bool:
        return map_id in {41, 58, 64, 68, 81, 89, 133, 141, 154, 171, 147, 182}
  
