import uuid
import math
from pathlib import Path

import numpy as np
from skimage.transform import downscale_local_mean
from pyboy import PyBoy
#from pyboy.logger import log_level
import mediapy as media
from einops import repeat
import random

from gymnasium import Env, spaces
from pyboy.utils import WindowEvent
from global_map import local_to_global, GLOBAL_MAP_SHAPE
from events import events, create_event_flag_mask

event_flags_start = 0xD747
event_flags_end = 0xD887
museum_ticket = (0xD754, 0)

MAP_N_ADDRESS = 0xD35E

class RedGymEnv(Env):
    def __init__(self, config=None):
        self.s_path = config["session_path"]
        self.save_final_state = config["save_final_state"]
        self.print_rewards = config["print_rewards"]
        self.headless = config["headless"]
        self.init_state = config["init_state"]
        self.act_freq = config["action_freq"]
        self.max_steps_config = config["max_steps"]
        self.max_steps = max(self.max_steps_config) if isinstance(self.max_steps_config, list) else self.max_steps_config
        self.save_video = config["save_video"]
        self.fast_video = config["fast_video"]
        self.frame_stacks = 3
        
        # reset parameters (except init state and max steps)
        self.event_weight = config["reset_params"]["event_weight"]
        self.level_weight = config["reset_params"]["level_weight"]
        self.heal_weight = config["reset_params"]["heal_weight"]
        self.op_lvl_weight = config["reset_params"]["op_lvl_weight"]
        self.explore_weight = config["reset_params"]["explore_weight"]
        self.reward_scale = config["reset_params"]["reward_scale"]
        self.use_explore_map_obs = config["reset_params"]["use_explore_map_obs"]
        self.use_recent_actions_obs = config["reset_params"]["use_recent_actions_obs"]
        self.zero_recent_actions = config["reset_params"]["zero_recent_actions"]

        self.instance_id = (
            str(uuid.uuid4())[:8]
            if "instance_id" not in config
            else config["instance_id"]
        )
        
        self.full_frame_writer = None
        self.model_frame_writer = None
        self.map_frame_writer = None
        self.reset_count = 0
        self.all_runs = []

        self.essential_map_locations = {
            v:i for i,v in enumerate([
                40, 0, 12, 1, 13, 51, 2, 54, 14, 59, 60, 61, 15, 3, 65
            ])
        }

        # Set this in SOME subclasses
        self.metadata = {"render.modes": []}
        self.reward_range = (0, 15000)
        
        self.valid_actions = [
            WindowEvent.PRESS_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT,
            WindowEvent.PRESS_ARROW_UP,
            WindowEvent.PRESS_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B,
            WindowEvent.PRESS_BUTTON_START,
        ]

        self.release_actions = [
            WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.RELEASE_BUTTON_B,
            WindowEvent.RELEASE_BUTTON_START
        ]

        # load event names (parsed from https://github.com/pret/pokered/blob/91dc3c9f9c8fd529bb6e8307b58b96efa0bec67e/constants/event_constants.asm)
        self.event_names = events

        # Setup action space
        self.action_space = spaces.Discrete(len(self.valid_actions))

        # Setup observation space
        self.enc_freqs = 8
        self.output_shape = (72, 80, self.frame_stacks)
        self.coords_pad = 12
        # Setup left steps buckets
        self.bucket_cap = 20480
        self.num_buckets = self.bucket_cap // 2048
        self.bucket_size = self.bucket_cap // self.num_buckets
        # Setup events
        self.events_mask = create_event_flag_mask(events)
        obs_spaces = {
                "screens": spaces.Box(low=0, high=255, shape=self.output_shape, dtype=np.uint8),
                "health": spaces.Box(low=0, high=1, shape=(6,)),
                "level": spaces.Box(low=-1, high=1, shape=(6,)),
                "events": spaces.Box(low=0, high=1, shape=(sum(self.events_mask),), dtype=np.uint8),
                # "left_steps": spaces.Box(low=0, high=1, shape=(self.num_buckets,)),
            }
        if self.use_explore_map_obs:
            obs_spaces["map"] = spaces.Box(low=0, high=255, shape=(self.coords_pad*4,self.coords_pad*4, 1), dtype=np.uint8)
        if self.use_recent_actions_obs:
            obs_spaces["recent_actions"] = spaces.Box(low=0, high=1, shape=(len(self.valid_actions) * self.frame_stacks,), dtype=np.uint8)
        self.observation_space = spaces.Dict(obs_spaces)

        head = "null" if config["headless"] else "SDL2"

        #log_level("ERROR")
        self.pyboy = PyBoy(
            config["gb_path"],
            window=head,
            no_input=False,
            symbols="pokered.sym"
        )

        if not config["headless"]:
            self.pyboy.set_emulation_speed(12)

    def reset(self, seed=None, options={}):
        self.seed = seed
        # restart game, skipping credits
        if self.init_state is not None:
            with open(self.init_state, "rb") as f:
                self.pyboy.load_state(f)

        self.init_map_mem()

        self.agent_stats = []

        self.explore_map = np.zeros(GLOBAL_MAP_SHAPE, dtype=np.uint8)

        self.recent_screens = np.zeros(self.output_shape, dtype=np.uint8)
        
        self.recent_actions = np.zeros((len(self.valid_actions), self.frame_stacks,), dtype=np.uint8)

        self.levels_satisfied = False
        self.base_explore = 0
        self.max_opponent_level = 0
        self.max_event_rew = 0
        self.max_level_rew = 0
        self.last_health = 1
        self.total_healing_rew = 0
        self.num_heals = 0
        self.died_count = 0
        self.party_size = 0
        self.step_count = 0
        self.badge_steps = [np.nan for i in range(2)]
        self.num_badges = 0
        self.visited_mt_moon = 0
        self.visited_cerulean = 0

        self.base_event_flags = sum([
                self.bit_count(self.read_m(i))
                for i in range(event_flags_start, event_flags_end)
        ])

        self.current_event_flags_set = {}

        # Set or sample max episode steps
        if isinstance(self.max_steps_config, int):
            self.max_steps = self.max_steps_config
        elif isinstance(self.max_steps_config, list):
            possible_max_steps = list(range(self.max_steps_config[0], self.max_steps_config[1] + 1, self.max_steps_config[2]))
            self.max_steps = random.choice(possible_max_steps)
        else:
            raise ValueError("max_steps_config must be an int or list")

        self.max_map_progress = 0
        self.progress_reward = self.get_game_state_reward()
        self.total_reward = sum([val for _, val in self.progress_reward.items()])
        self.reset_count += 1
        return self._get_obs(), {}

    def init_map_mem(self):
        self.seen_coords = {}

    def render(self, reduce_res=True):
        game_pixels_render = self.pyboy.screen.ndarray[:,:,0:1]  # (144, 160, 3)
        if reduce_res:
            game_pixels_render = (
                downscale_local_mean(game_pixels_render, (2,2,1))
            ).astype(np.uint8)
        return game_pixels_render
    
    def _get_obs(self):
        screen = self.render()
        self.update_recent_screens(screen)
        
        # normalize to approx 0-1
        levels =  np.asarray([
            self.read_m(a) for a in [0xD18C, 0xD1B8, 0xD1E4, 0xD210, 0xD23C, 0xD268]
        ])

        unmasked_events = np.array(self.read_event_bits(), dtype=np.int8)
        masked_events = unmasked_events[self.events_mask]

        observation = {
            "screens": self.recent_screens,
            "health": self.read_hp_fractions(),
            "level": levels * 0.01,
            "events": masked_events,
            # "left_steps": self.get_left_steps_buckets(),
        }

        # Append explore map to observation and check if it is the correct shape
        if self.use_explore_map_obs:
            observation["map"] = self.get_explore_map()[:, :, None]
            if observation["map"].shape != self.observation_space["map"].shape:
                print("Unexpected map shape at")
                observation["map"] = np.zeros((self.coords_pad*4, self.coords_pad*4), dtype=np.uint8)

        # Append recent actions to observation
        if self.use_recent_actions_obs:
            if self.zero_recent_actions:
                observation["recent_actions"] = np.zeros((len(self.valid_actions) * self.frame_stacks,), dtype=np.uint8)
            else:
                # flatten recent actions and add to observation
                observation["recent_actions"] = self.recent_actions.flatten()

        return observation

    def step(self, action):
        if self.save_video and self.step_count == 0:
            self.start_video()

        self.run_action_on_emulator(action)
        self.append_agent_stats(action)

        self.update_recent_actions(action)

        self.update_seen_coords()

        self.update_explore_map()

        self.update_heal_reward()

        self.party_size = self.read_m(0xD163)

        new_reward = self.update_reward()

        self.last_health = self.read_hp_fraction()

        self.update_map_progress()

        step_limit_reached = self.check_if_done()

        obs = self._get_obs()

        # create a map of all event flags set, with names where possible
        #if step_limit_reached:
        if self.step_count % 100 == 0:
            for address in range(event_flags_start, event_flags_end):
                val = self.read_m(address)
                for idx, bit in enumerate(f"{val:08b}"):
                    if bit == "1":
                        # TODO this currently seems to be broken!
                        key = f"0x{address:X}-{idx}"
                        if key in self.event_names.keys():
                            self.current_event_flags_set[key] = self.event_names[key]
                        else:
                            print(f"could not find key: {key}")

        if self.get_badges() > self.num_badges:
            self.num_badges = self.get_badges()
            self.badge_steps[self.num_badges-1] = self.step_count

        # check if mt moon is reached
        if self.read_m(MAP_N_ADDRESS) == 59 and self.visited_mt_moon == 0:
            self.visited_mt_moon = 1
        # check if cerulean city is reached
        if self.read_m(MAP_N_ADDRESS) == 3 and self.visited_cerulean == 0:
            self.visited_cerulean = 1

        info = {
            "deaths": self.died_count,
            "max_foe_level": self.max_opponent_level,
            "max_event_rew": self.max_event_rew,
            "party_size": self.party_size,
            "levels_sum": self.agent_stats[-1]["levels_sum"],
            "mt_moon": self.visited_mt_moon,
            "cerulean": self.visited_cerulean,
            "event_reward": self.progress_reward["event"],
            "healr": self.total_healing_rew,
            "coord_count": len(self.seen_coords),
            "max_map_progress": self.max_map_progress
        }
        # Append badges and steps to info
        for i in range(2):
            info[f"badge_{i+1}_steps"] = self.badge_steps[i]
            info[f"badge_{i+1}"] = int(not math.isnan(self.badge_steps[i]))

        self.step_count += 1

        return obs, new_reward, False, step_limit_reached, info
    
    def run_action_on_emulator(self, action):
        # press button then release after some steps
        self.pyboy.send_input(self.valid_actions[action])
        # disable rendering when we don't need it
        render_screen = self.save_video or not self.headless
        press_step = 8
        self.pyboy.tick(press_step, render_screen)
        self.pyboy.send_input(self.release_actions[action])
        self.pyboy.tick(self.act_freq - press_step - 1, render_screen)
        self.pyboy.tick(1, True)
        if self.save_video and self.fast_video:
            self.add_video_frame()

    def append_agent_stats(self, action):
        x_pos, y_pos, map_n = self.get_game_coords()
        levels = [
            self.read_m(a) for a in [0xD18C, 0xD1B8, 0xD1E4, 0xD210, 0xD23C, 0xD268]
        ]
        self.agent_stats.append(
            {
                "step": self.step_count,
                "x": x_pos,
                "y": y_pos,
                "map": map_n,
                # "map_location": self.get_map_location(map_n),
                "max_map_progress": self.max_map_progress,
                "last_action": action,
                "pcount": self.read_m(0xD163),
                "levels": levels,
                "levels_sum": sum(levels),
                "ptypes": self.read_party(),
                "hp": self.read_hp_fraction(),
                "coord_count": len(self.seen_coords),
                "deaths": self.died_count,
                "badge": self.get_badges(),
                "event": self.progress_reward["event"],
                "healr": self.total_healing_rew,
            }
        )

    def start_video(self):
        if self.full_frame_writer is not None:
            self.full_frame_writer.close()
        if self.model_frame_writer is not None:
            self.model_frame_writer.close()
        if self.map_frame_writer is not None:
            self.map_frame_writer.close()

        self.s_path.mkdir(exist_ok=True)
        base_dir = self.s_path / Path("rollouts")
        base_dir.mkdir(exist_ok=True)
        full_name = Path(
            f"full_reset_{self.reset_count}_id{self.instance_id}"
        ).with_suffix(".mp4")
        model_name = Path(
            f"model_reset_{self.reset_count}_id{self.instance_id}"
        ).with_suffix(".mp4")
        self.full_frame_writer = media.VideoWriter(
            base_dir / full_name, (144, 160), fps=60, input_format="gray"
        )
        self.full_frame_writer.__enter__()
        self.model_frame_writer = media.VideoWriter(
            base_dir / model_name, self.output_shape[:2], fps=60, input_format="gray"
        )
        self.model_frame_writer.__enter__()
        map_name = Path(
            f"map_reset_{self.reset_count}_id{self.instance_id}"
        ).with_suffix(".mp4")
        self.map_frame_writer = media.VideoWriter(
            base_dir / map_name,
            (self.coords_pad*4, self.coords_pad*4), 
            fps=60, input_format="gray"
        )
        self.map_frame_writer.__enter__()

    def add_video_frame(self):
        self.full_frame_writer.add_image(
            self.render(reduce_res=False)[:,:,0]
        )
        self.model_frame_writer.add_image(
            self.render(reduce_res=True)[:,:,0]
        )
        self.map_frame_writer.add_image(
            self.get_explore_map()
        )

    def get_left_steps_buckets(self):
        remaining_steps = self.max_steps - self.step_count
        if remaining_steps >= self.bucket_cap:
            return np.ones(self.num_buckets)
        buckets = np.zeros(self.num_buckets)
        current_bucket = int(remaining_steps // self.bucket_size)
        buckets[:current_bucket] = self.bucket_size
        buckets[current_bucket] = remaining_steps % self.bucket_size
        return buckets / self.bucket_size

    def get_game_coords(self):
        return (self.read_m(0xD362), self.read_m(0xD361), self.read_m(0xD35E))

    def update_seen_coords(self):
        x_pos, y_pos, map_n = self.get_game_coords()
        coord_string = f"x:{x_pos} y:{y_pos} m:{map_n}"
        self.seen_coords[coord_string] = self.step_count

    def get_global_coords(self):
        x_pos, y_pos, map_n = self.get_game_coords()
        return local_to_global(y_pos, x_pos, map_n)

    def update_explore_map(self):
        c = self.get_global_coords()
        if c[0] >= self.explore_map.shape[0] or c[1] >= self.explore_map.shape[1]:
            #print(f"coord out of bounds! global: {c} game: {self.get_game_coords()}")
            pass
        else:
            self.explore_map[c[0], c[1]] = 255

    def get_explore_map(self):
        c = self.get_global_coords()
        if c[0] >= self.explore_map.shape[0] or c[1] >= self.explore_map.shape[1]:
            out = np.zeros((self.coords_pad*2, self.coords_pad*2), dtype=np.uint8)
        else:
            out = self.explore_map[
                c[0]-self.coords_pad:c[0]+self.coords_pad,
                c[1]-self.coords_pad:c[1]+self.coords_pad
            ]
        return repeat(out, 'h w -> (h h2) (w w2)', h2=2, w2=2)
    
    def update_recent_screens(self, cur_screen):
        self.recent_screens = np.roll(self.recent_screens, 1, axis=2)
        self.recent_screens[:, :, 0] = cur_screen[:,:, 0]

    def update_recent_actions(self, action):
        self.recent_actions = np.roll(self.recent_actions, 1, axis=1)
        self.recent_actions[:, 0] = 0
        self.recent_actions[action, 0] = 1

    def update_reward(self):
        # compute reward
        self.progress_reward = self.get_game_state_reward()
        new_total = sum(
            [val for _, val in self.progress_reward.items()]
        )
        new_step = new_total - self.total_reward

        self.total_reward = new_total
        return new_step

    def group_rewards(self):
        prog = self.progress_reward
        # these values are only used by memory
        return (
            prog["level"] * 100 / self.reward_scale,
            self.read_hp_fraction() * 2000,
            prog["explore"] * 150 / (self.explore_weight * self.reward_scale),
        )

    def check_if_done(self):
        done = self.step_count >= self.max_steps - 1
        # done = self.read_hp_fraction() == 0 # end game on loss
        return done

    def read_m(self, addr):
        return self.pyboy.memory[addr]

    def read_bit(self, addr, bit: int) -> bool:
        # add padding so zero will read '0b100000000' instead of '0b0'
        return bin(256 + self.read_m(addr))[-bit - 1] == "1"

    def read_event_bits(self):
        return [
            int(bit) for i in range(event_flags_start, event_flags_end) 
            for bit in f"{self.read_m(i):08b}"[::-1]
        ]

    def get_levels_sum(self):
        min_poke_level = 2
        starter_additional_levels = 4
        poke_levels = [
            max(self.read_m(a) - min_poke_level, 0)
            for a in [0xD18C, 0xD1B8, 0xD1E4, 0xD210, 0xD23C, 0xD268]
        ]
        self.last_level_max_sum = max(sum(poke_levels) - starter_additional_levels, 0)
        return self.last_level_max_sum

    def get_levels_reward(self):
        explore_thresh = 22
        scale_factor = 4
        level_sum = self.get_levels_sum()
        if level_sum < explore_thresh:
            scaled = level_sum
        else:
            scaled = (level_sum - explore_thresh) / scale_factor + explore_thresh
        self.max_level_rew = max(self.max_level_rew, scaled)
        return self.max_level_rew

    def get_badges(self):
        return self.bit_count(self.read_m(0xD356))

    def read_party(self):
        return [
            self.read_m(addr)
            for addr in [0xD164, 0xD165, 0xD166, 0xD167, 0xD168, 0xD169]
        ]

    def get_all_events_reward(self):
        # adds up all event flags, exclude museum ticket
        return max(
            sum([
                self.bit_count(self.read_m(i))
                for i in range(event_flags_start, event_flags_end)
            ])
            - self.base_event_flags
            - int(self.read_bit(museum_ticket[0], museum_ticket[1])),
            0,
        )

    def get_game_state_reward(self, print_stats=False):
        # addresses from https://datacrystal.romhacking.net/wiki/Pok%C3%A9mon_Red/Blue:RAM_map
        # https://github.com/pret/pokered/blob/91dc3c9f9c8fd529bb6e8307b58b96efa0bec67e/constants/event_constants.asm
        state_scores = {
            "event": self.reward_scale * self.event_weight * self.update_max_event_rew(),
            "level": self.reward_scale * self.level_weight * self.get_levels_reward(),
            "heal": self.reward_scale * self.heal_weight * self.total_healing_rew,
            "op_lvl": self.reward_scale * self.op_lvl_weight * self.update_max_op_level(),
            "explore": self.reward_scale * self.explore_weight * len(self.seen_coords) * 0.1,
        }

        return state_scores

    def update_max_op_level(self, opp_base_level=5):
        opponent_level = (
            max([
                self.read_m(a)
                for a in [0xD8C5, 0xD8F1, 0xD91D, 0xD949, 0xD975, 0xD9A1]
            ])
            - opp_base_level
        )
        self.max_opponent_level = max(self.max_opponent_level, opponent_level)
        return self.max_opponent_level

    def update_max_event_rew(self):
        cur_rew = self.get_all_events_reward()
        if cur_rew > self.max_event_rew:
            self.max_steps += 2048
        self.max_event_rew = max(cur_rew, self.max_event_rew)
        return self.max_event_rew

    def update_heal_reward(self):
        cur_health = self.read_hp_fraction()
        # if health increased and party size did not change
        if cur_health > self.last_health and self.read_m(0xD163) == self.party_size:
            if self.last_health > 0:
                if self.last_level_max_sum == self.get_levels_sum(): # dont trigger heal on lvl up
                    heal_amount = cur_health - self.last_health
                    self.total_healing_rew += heal_amount
                    self.num_heals += 1
            else:
                self.died_count += 1

    def read_hp_fraction(self):
        hp_sum = sum([
            self.read_hp(add)
            for add in [0xD16C, 0xD198, 0xD1C4, 0xD1F0, 0xD21C, 0xD248]
        ])
        max_hp_sum = sum([
            self.read_hp(add)
            for add in [0xD18D, 0xD1B9, 0xD1E5, 0xD211, 0xD23D, 0xD269]
        ])
        max_hp_sum = max(max_hp_sum, 1)
        return hp_sum / max_hp_sum
    
    def read_hp_fractions(self):
        hp = np.asarray([
            self.read_hp(add)
            for add in [0xD16C, 0xD198, 0xD1C4, 0xD1F0, 0xD21C, 0xD248]
        ])
        max_hp = np.asarray([
            self.read_hp(add)
            for add in [0xD18D, 0xD1B9, 0xD1E5, 0xD211, 0xD23D, 0xD269]
        ])
        normalized_hp = hp / np.maximum(max_hp, 1)
        # nan to 0
        normalized_hp[np.isnan(normalized_hp)] = 0
        return normalized_hp

    def read_hp(self, start):
        return 256 * self.read_m(start) + self.read_m(start + 1)

    # built-in since python 3.10
    def bit_count(self, bits):
        return bin(bits).count("1")
    
    def update_map_progress(self):
        map_idx = self.read_m(0xD35E)
        self.max_map_progress = max(self.max_map_progress, self.get_map_progress(map_idx))
    
    def get_map_progress(self, map_idx):
        if map_idx in self.essential_map_locations.keys():
            return self.essential_map_locations[map_idx]
        else:
            return -1

    def save_state(self, path):
        with open(path, "wb") as f:
            self.pyboy.save_state(f)