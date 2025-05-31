# navigator.py

from __future__ import annotations

import json
import time
import random
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

from pyboy.utils import WindowEvent

from environment.data.environment_data.map import MapIds
from environment.data.environment_data.warps import WARP_DICT

if TYPE_CHECKING:
    from environment import RedGymEnv

from environment.data.recorder_data.global_map import local_to_global, global_to_local


class InteractiveNavigator:
    def __init__(self, env_instance: RedGymEnv):
        self.env: RedGymEnv = env_instance
        self.pyboy = self.env.pyboy

        # Full flattened quest path coords and their map IDs
        self.sequential_coordinates: List[Tuple[int, int]] = []
        self.coord_map_ids: List[int] = []
        self.current_coordinate_index: int = 0

        self.active_quest_id: Optional[int] = None
        self._last_loaded_quest_id: Optional[int] = None

        # Multi-segment load tracking
        self.map_segment_count: dict[int, int] = {}

        # Raw local coords placeholder
        self.current_coords = None

        # Fallback/resume logic
        self.last_position = None
        self.quest_locked = False
        self._direction = 1                # 1 = forward, -1 = backward
        self._fallback_mode = False
        self._original_quest_id: Optional[int] = None

        # Movement tracking
        self.movement_failure_count = 0
        self.max_movement_failures = 10
        self.navigation_status = "idle"

        # Warp tracking
        self.door_warp = False
        self.last_warp_time = 0.0
        self.WARP_COOLDOWN_SECONDS = 0.5
        self.last_warp_origin_map: Optional[int] = None
        self._post_warp_exit_pos: Optional[Tuple[int, int]] = None
        self._left_home = False

        # Action mapping
        self.ACTION_MAPPING_STR_TO_INT = {
            "down": 0,
            "left": 1,
            "right": 2,
            "up": 3,
        }

    # ...............................................................
    #  U T I L I T I E S
    # ...............................................................
    def _get_player_global_coords(self) -> Optional[Tuple[int, int]]:
        if not hasattr(self.env, "get_game_coords"):
            print("Navigator: env lacks get_game_coords()")
            return None
        try:
            lx, ly, map_id = self.env.get_game_coords()
            gy, gx = local_to_global(ly, lx, map_id)
            pos3 = (gy, gx, map_id)
            if self.last_position != pos3:
                print(
                    f"navigator.py: _get_player_global_coords(): "
                    f"global_coords=({gy},{gx}), map_id={map_id}, "
                    f"local_coords=(y={ly},x={lx})"
                )
            self.last_position = pos3
            return gy, gx
        except Exception as e:
            print(f"Navigator: ERROR reading coords: {e}")
            return None

    @staticmethod
    def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    # ...............................................................
    #  S N A P
    # ...............................................................
    def snap_to_nearest_coordinate(self) -> bool:
        if not self.sequential_coordinates:
            try:
                self.load_segment_for_current_map()
            except RuntimeError as e:
                print(f"Navigator: snap_to_nearest_coordinate: {e}")
                return False

        cur_map = self.env.get_game_coords()[2]
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            print("Navigator: snap_to_nearest_coordinate: Cannot get player position")
            return False

        candidate_ids = [i for i, m in enumerate(self.coord_map_ids) if m == cur_map]
        if not candidate_ids:
            print(f"Navigator: No path points on current map {cur_map}, attempting to load segment")
            try:
                self.load_segment_for_current_map()
            except RuntimeError as e:
                print(f"Navigator: snap_to_nearest_coordinate: {e}")
                print(f"Navigator: snap_to_nearest_coordinate: Fallback to nearest path for map {cur_map}")
                if not self._fallback_to_nearest_path(cur_map):
                    return False
            candidate_ids = [i for i, m in enumerate(self.coord_map_ids) if m == cur_map]
            if not candidate_ids:
                return False

        nearest_i = min(candidate_ids, key=lambda i: self._manhattan(cur_pos, self.sequential_coordinates[i]))
        dist = self._manhattan(cur_pos, self.sequential_coordinates[nearest_i])
        self.current_coordinate_index = nearest_i
        print(
            f"navigator.py: snap_to_nearest_coordinate(): "
            f"nearest_idx={nearest_i}, coord={self.sequential_coordinates[nearest_i]}, distance={dist}"
        )

        self.movement_failure_count = 0
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            self.current_coordinate_index = max(0, len(self.sequential_coordinates) - 1)

        print("navigator.py: snap_to_nearest_coordinate(): SNAP COMPLETE")
        return True

    # ...............................................................
    #  W A R P   H A N D L E R
    # ...............................................................
    def warp_tile_handler(self) -> bool:
        cur = self._get_player_global_coords()
        # Debug: initial warp handler state
        print(f"warp_tile_handler: current_coordinate_index={self.current_coordinate_index}, coord_map_ids snippet={self.coord_map_ids[self.current_coordinate_index:self.current_coordinate_index+3] if self.coord_map_ids else []}")
        if cur is None:
            return False

        if (time.time() - self.last_warp_time) < self.WARP_COOLDOWN_SECONDS:
            return False

        if self._post_warp_exit_pos is not None:
            if self._manhattan(cur, self._post_warp_exit_pos) <= 1:
                return False
            else:
                self._post_warp_exit_pos = None
                self.last_warp_origin_map = None

        try:
            local_x, local_y, cur_map = self.env.get_game_coords()
            local = (local_x, local_y)
        except Exception:
            print("Navigator: Could not get game coords.")
            return False

        warp_entries = WARP_DICT.get(MapIds(cur_map).name, [])
        warp_tiles = [(e["x"], e["y"]) for e in warp_entries if e.get("x") is not None]
        if not warp_tiles:
            return False

        nearest_warp = next((wt for wt in warp_tiles if self._manhattan(local, wt) == 1), None)
        if not nearest_warp:
            return False

        warp_entry = next((e for e in warp_entries if (e.get("x"), e.get("y")) == nearest_warp), None)
        if warp_entry:
            # Debug: warp entry found
            print(f"warp_tile_handler: found warp_entry target_map_id={warp_entry.get('target_map_id')} for nearest_warp={nearest_warp}")
            # Only trigger warp if it matches the next intended map in the quest path
            next_idx = self.current_coordinate_index + self._direction
            intended_map = self.coord_map_ids[next_idx] if 0 <= next_idx < len(self.coord_map_ids) else None
            if warp_entry.get("target_map_id") != intended_map:
                return False
            if self._left_home and warp_entry.get("target_map_id") in {37, 38}:
                print("navigator.py: warp_tile_handler(): BLOCKED re-entry to home")
                return False
            if warp_entry.get("target_map_id") == self.last_warp_origin_map:
                return False

            base = Path(__file__).parent / "quest_paths"
            if self.active_quest_id is not None:
                quest_file = base / f"{self.active_quest_id:03d}" / f"{self.active_quest_id:03d}_coords.json"
                try:
                    data = json.loads(quest_file.read_text())
                    allowed_maps = {int(k.split("_")[0]) for k in data.keys()}
                except Exception:
                    allowed_maps = set()
                if warp_entry.get("target_map_id") not in allowed_maps and allowed_maps != set():
                    print(f"navigator.py: warp_tile_handler(): skipping warp to map {warp_entry.get('target_map_id')}")
                    return False

        is_door = any(
            self._manhattan(nearest_warp, wt2) == 1
            for wt2 in warp_tiles
            if wt2 != nearest_warp
        )

        dx = nearest_warp[0] - local[0]
        dy = nearest_warp[1] - local[1]
        dir_map = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right"}
        direction_to_step = dir_map.get((dx, dy))
        if direction_to_step is None:
            return False

        prev_map = cur_map
        self.last_warp_origin_map = prev_map

        self.env.run_action_on_emulator(self.ACTION_MAPPING_STR_TO_INT[direction_to_step])
        ticks = 15 if is_door else 20
        for _ in range(ticks):
            self.pyboy.tick(self.env.action_freq)
        if is_door:
            self.env.run_action_on_emulator(self.ACTION_MAPPING_STR_TO_INT["down"])
            for _ in range(15):
                self.pyboy.tick(self.env.action_freq)

        try:
            post_map = self.env.get_game_coords()[2]
        except Exception:
            post_map = None

        if post_map is not None and post_map != prev_map:
            # Debug: successful warp
            print(f"warp_tile_handler: warped from map {prev_map} to {post_map}, current idx before snap={self.current_coordinate_index}")
            if prev_map == 37 and post_map == 0:
                self._left_home = True
            self.last_warp_time = time.time()
            setattr(self.env, "prev_map_id", post_map)
            landed = self._get_player_global_coords()
            if landed:
                self._post_warp_exit_pos = landed
            if self.current_coordinate_index < len(self.sequential_coordinates):
                self.snap_to_nearest_coordinate()
            print(f"warp_tile_handler: after snap idx={self.current_coordinate_index}, target={self.sequential_coordinates[self.current_coordinate_index]}")
            return True

        return False

    # ...............................................................
    #  R O A M   I N   G R A S S  (Quest 23)
    # ...............................................................
    def roam_in_grass(self) -> bool:
        direction_str = random.choice(list(self.ACTION_MAPPING_STR_TO_INT.keys()))
        action = self.ACTION_MAPPING_STR_TO_INT[direction_str]
        moved = self._execute_movement(action)
        if moved:
            print(f"Navigator: Roaming in grass: moved {direction_str}")
        else:
            print(f"Navigator: Roaming in grass: movement {direction_str} failed")
        return True

    # ...............................................................
    #  L O C A L   C O O R D S
    # ...............................................................
    def get_current_local_coords(self):
        return (
            self.env.get_game_coords()[0],
            self.env.get_game_coords()[1],
            self.env.get_game_coords()[2],
        )

    # ...............................................................
    #  M O V E   T O   N E X T   C O O R D I N A T E
    # ...............................................................
    def move_to_next_coordinate(self) -> bool:
        # If quest ID changed, reset state
        env_qid = getattr(self.env, "current_loaded_quest_id", None)
        if hasattr(self, "_last_loaded_quest_id") and self._last_loaded_quest_id != env_qid:
            self._reset_state()

        # Resume original quest after fallback when re-entering its map
        if self._fallback_mode and self._original_quest_id is not None:
            cur_map = self.env.get_game_coords()[2]
            base = Path(__file__).parent / "quest_paths"
            orig_id = self._original_quest_id
            file_path = base / f"{orig_id:03d}" / f"{orig_id:03d}_coords.json"
            try:
                data = json.loads(file_path.read_text())
                if str(cur_map) in data:
                    self.load_coordinate_path(orig_id)
                    self._fallback_mode = False
                    self._direction = 1
                    print(f"Navigator: Fallback complete, resumed quest {orig_id:03d}")
            except Exception:
                pass

        # Debug: start of move_to_next_coordinate
        cur_map = self.env.get_game_coords()[2]
        next_coords = self.sequential_coordinates[self.current_coordinate_index:self.current_coordinate_index+3] if self.sequential_coordinates else []
        print(f"move_to_next_coordinate: quest={self.active_quest_id}, idx={self.current_coordinate_index}, cur_map={cur_map}, next_coords={next_coords}, path_len={len(self.sequential_coordinates)}")

        # Pause on dialog/battle
        try:
            if (self.env.read_dialog() or "").strip():
                return False
        except Exception:
            pass

        # Early warp handling
        if self.warp_tile_handler():
            print(f"move_to_next_coordinate: warp handled, new idx={self.current_coordinate_index}")
            return True

        # Ensure path loaded
        if not self.sequential_coordinates:
            env_qid = getattr(self.env, "current_loaded_quest_id", None)
            if env_qid is None:
                return False
            if not self.load_coordinate_path(env_qid):
                # Fallback to nearest path entry on current map if direct load fails
                cur_map = self.env.get_game_coords()[2]
                if self._fallback_to_nearest_path(cur_map):
                    return True
                try:
                    self.load_segment_for_current_map()
                    return True
                except RuntimeError:
                    return False

        # Special halt for Quest 12 at Oak
        if self.active_quest_id == 12:
            pos = self._get_player_global_coords()
            if pos == (348, 110):
                return False

        # End-of-path handling
        if self.current_coordinate_index >= len(self.sequential_coordinates):
            # Quest 23 roaming
            if self.active_quest_id == 23:
                return self.roam_in_grass()
            # Attempt to load next segment of same quest
            try:
                self.load_segment_for_current_map()
                return True
            except RuntimeError as e:
                # End-of-path next segment load error
                print(f"Navigator: end-of-path next segment load error: {e}")
                self.quest_locked = False
                return False

        # Multi-map quest: if map changed externally, load segment
        cur_map = self.env.get_game_coords()[2]
        prev_map = getattr(self.env, "prev_map_id", None)
        if prev_map is not None and cur_map != prev_map:
            print(
                f"navigator.py: move_to_next_coordinate(): MAP CHANGE detected for "
                f"quest {self.active_quest_id}, loading segment for map {cur_map}"
            )
            self.load_segment_for_current_map()
            setattr(self.env, "prev_map_id", cur_map)
            return True

        # Block re-entry to home warp after leaving
        try:
            if self._left_home and cur_map == MapIds.PALLET_TOWN.value:
                for entry in WARP_DICT.get(MapIds(cur_map).name, []):
                    if entry.get("target_map_id") == 37:
                        x, y = entry.get("x"), entry.get("y")
                        if x is not None and y is not None:
                            global_warp = local_to_global(y, x, cur_map)
                            if self.sequential_coordinates[self.current_coordinate_index] == global_warp:
                                print(
                                    f"navigator.py: move_to_next_coordinate(): "
                                    f"SKIPPING warp-entry coord {global_warp} after leaving home"
                                )
                                self.current_coordinate_index += 1
                                return True
        except Exception:
            pass

        # Move towards current target
        target = self.sequential_coordinates[self.current_coordinate_index]
        cur_pos = self._get_player_global_coords()
        if cur_pos is None:
            return False
        if cur_pos == target:
            self.current_coordinate_index += self._direction
            return True

        return self._step_towards(target)

    # ...............................................................
    #  S T E P  T O W A R D S
    # ...............................................................
    def _step_towards(self, target: Tuple[int, int]) -> bool:
        cur = self._get_player_global_coords()
        if cur is None:
            return False

        if cur == target:
            self.current_coordinate_index += self._direction
            return True

        dy = target[0] - cur[0]
        dx = target[1] - cur[1]
        moved = False
        if dy != 0:
            dir_str = "down" if dy > 0 else "up"
            moved = self._execute_movement(self.ACTION_MAPPING_STR_TO_INT[dir_str])
        if not moved and dx != 0:
            dir_str = "right" if dx > 0 else "left"
            moved = self._execute_movement(self.ACTION_MAPPING_STR_TO_INT[dir_str])

        new_pos = self._get_player_global_coords()
        if moved and new_pos != cur:
            if new_pos == target:
                self.current_coordinate_index += self._direction
            return True

        # Skip deadlocked coordinate
        self.current_coordinate_index += self._direction
        self.movement_failure_count = 0
        return True

    # ...............................................................
    #  E X E C U T E  M O V E M E N T
    # ...............................................................
    def _execute_movement(self, action: int) -> bool:
        pre = self._get_player_global_coords()
        self.env.run_action_on_emulator(action)
        for _ in range(5):
            self.pyboy.tick(self.env.action_freq)
        post = self._get_player_global_coords()
        return post != pre

    # ...............................................................
    #  L O A D   Q U E S T   P A T H
    # ...............................................................
    def load_coordinate_path(self, quest_id: int) -> bool:
        if self.quest_locked and self.current_coordinate_index < len(self.sequential_coordinates):
            return False

        self.map_segment_count = {}
        self._fallback_mode = False
        self._direction = 1
        self._original_quest_id = None

        base = Path(__file__).parent / "quest_paths"
        file_path = base / f"{quest_id:03d}" / f"{quest_id:03d}_coords.json"
        if not file_path.exists():
            print(f"Navigator: coord file missing → {file_path}")
            return False

        try:
            data = json.loads(file_path.read_text())
        except Exception as e:
            print(f"Navigator: failed to read coord file: {e}")
            return False

        coords: List[Tuple[int, int]] = []
        map_ids: List[int] = []
        for key, coord_list in data.items():
            mid = int(key.split("_")[0])
            for gy, gx in coord_list:
                coords.append((gy, gx))
                map_ids.append(mid)

        self.sequential_coordinates = coords
        self.coord_map_ids = map_ids
        self.current_coordinate_index = 0
        self.active_quest_id = quest_id
        self._last_loaded_quest_id = quest_id
        self.quest_locked = True
        self.movement_failure_count = 0

        print(
            f"Navigator: loaded quest {quest_id:03d}: "
            f"{len(coords)} points on {len(set(map_ids))} maps"
        )
        self.snap_to_nearest_coordinate()
        setattr(self.env, "current_loaded_quest_id", quest_id)
        return True

    # ...............................................................
    #  L O A D   S E G M E N T  F O R   C U R R E N T  M A P
    # ...............................................................
    def load_segment_for_current_map(self) -> None:
        env_qid = getattr(self.env, "current_loaded_quest_id", None)
        if env_qid is not None:
            self.active_quest_id = env_qid

        map_id = self.env.get_game_coords()[2]
        base = Path(__file__).parent / "quest_paths"

        for qid in range(self.active_quest_id, 0, -1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            if not fp.exists():
                continue
            try:
                data = json.loads(fp.read_text())
                segment_keys = [k for k in data.keys() if int(k.split("_")[0]) == map_id]
                if not segment_keys:
                    continue
                count = self.map_segment_count.get(map_id, 0)
                idx = count if count < len(segment_keys) else len(segment_keys) - 1
                self.map_segment_count[map_id] = count + 1

                selected = segment_keys[idx]
                coords = data[selected]
                self.sequential_coordinates = [(c[0], c[1]) for c in coords]
                self.coord_map_ids = [map_id] * len(coords)
                self.current_coordinate_index = 0
                self.active_quest_id = qid
                print(
                    f"Navigator: Loaded quest {qid:03d} segment '{selected}' "
                    f"on map {map_id} ({len(coords)} steps)"
                )
                return
            except Exception:
                continue

        raise RuntimeError(f"Navigator: No quest file with map id {map_id}")

    # ...............................................................
    #  F A L L B A C K   T O   N E A R E S T   P A T H
    # ...............................................................
    def _fallback_to_nearest_path(self, cur_map: int) -> bool:
        base = Path(__file__).parent / "quest_paths"
        cur_pos = self._get_player_global_coords()
        if not cur_pos:
            return False

        orig_id = self.active_quest_id or 0
        self._original_quest_id = orig_id
        self._fallback_mode = True

        entries: list[tuple[int, int, tuple[int, int]]] = []
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            try:
                qid = int(d.name)
                fpath = d / f"{d.name}_coords.json"
                if not fpath.exists():
                    continue
                data = json.loads(fpath.read_text())
                for key, coord_list in data.items():
                    mid = int(key.split("_")[0])
                    if mid != cur_map:
                        continue
                    for idx, (gy, gx) in enumerate(coord_list):
                        entries.append((qid, idx, (gy, gx)))
            except Exception:
                continue

        if not entries:
            print(f"Navigator: fallback: No path entries for map {cur_map}")
            return False

        dists = [(self._manhattan(cur_pos, coord), qid, idx, coord) for qid, idx, coord in entries]
        min_dist = min(dists, key=lambda x: x[0])[0]
        nearest = [(qid, idx, coord) for dist, qid, idx, coord in dists if dist == min_dist]
        sel_qid, sel_idx, sel_coord = min(nearest, key=lambda x: abs(x[0] - orig_id))
        print(
            f"Navigator: fallback: selected quest {sel_qid:03d} idx {sel_idx} "
            f"coord {sel_coord} with dist {min_dist}"
        )

        if not self.load_coordinate_path(sel_qid):
            print(f"Navigator: fallback: Failed to load quest {sel_qid:03d}")
            return False

        diff = sel_qid - orig_id
        self._direction = 1 if diff >= 0 else -1
        self.current_coordinate_index = sel_idx
        return True

    # ...............................................................
    #  S A F E   C O O R D   M A P   I D
    # ...............................................................
    def _safe_coord_map_id(self, idx: int) -> str | int:
        return self.coord_map_ids[idx] if 0 <= idx < len(self.coord_map_ids) else "?"

    # ...............................................................
    #  S T A T U S
    # ...............................................................
    def get_current_status(self) -> str:
        pos = self._get_player_global_coords()
        s = ["\n*** NAVIGATOR STATUS ***"]
        s.append(f"Quest          : {self.active_quest_id}")
        s.append(f"Current pos    : {pos}")
        s.append(f"Path length    : {len(self.sequential_coordinates)}")
        s.append(f"Current index  : {self.current_coordinate_index}")

        if self.sequential_coordinates and 0 <= self.current_coordinate_index < len(self.sequential_coordinates):
            tgt = self.sequential_coordinates[self.current_coordinate_index]
            dist = self._manhattan(pos, tgt) if pos else "?"
            s.append(f"Current target : {tgt} (dist {dist})")
            s.append(f"Target map-id  : {self._safe_coord_map_id(self.current_coordinate_index)}")
        else:
            s.append(
                "At end of path – quest complete"
                if self.sequential_coordinates else
                "No path loaded"
            )

        return "\n".join(s)

    # ...............................................................
    #  R E S E T
    # ...............................................................
    def _reset_state(self):
        self.sequential_coordinates.clear()
        self.coord_map_ids.clear()
        self.current_coordinate_index = 0
        self.quest_locked = False
        self.navigation_status = "idle"
        self.active_quest_id = None
        self.movement_failure_count = 0

    _reset_quest_state = _reset_state  # legacy alias

    # ...............................................................
    #  F O L L O W   P A T H   F O R   C U R R E N T   M A P
    # ...............................................................
    def follow_path_for_current_map(self) -> None:
        """
        Chain per-map segments across quest JSONs.
        Raises RuntimeError if any move fails.
        """
        if self.active_quest_id is None:
            raise RuntimeError("Navigator: no active quest to follow")
        start_id = self.active_quest_id
        map_id = self.env.get_game_coords()[2]
        base = Path(__file__).parent / "quest_paths"

        # 1. search backward for first JSON containing map_id
        found_id = None
        for qid in range(start_id, 0, -1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            if not fp.exists():
                continue
            data = json.loads(fp.read_text())
            if str(map_id) in data:
                found_id = qid
                break
        if found_id is None:
            raise RuntimeError(f"Navigator: no path JSON contains map {map_id}")

        # 2. chain forward from found_id to start_id
        for qid in range(found_id, start_id + 1):
            fp = base / f"{qid:03d}" / f"{qid:03d}_coords.json"
            data = json.loads(fp.read_text())
            arr = data.get(str(map_id), [])
            if not arr:
                continue
            self.sequential_coordinates = [(gy, gx) for gy, gx in arr]
            self.coord_map_ids = [map_id] * len(arr)
            self.current_coordinate_index = 0
            print(
                f"Navigator: following quest {qid:03d} segment on map {map_id} "
                f"({len(arr)} steps)"
            )
            self.snap_to_nearest_coordinate()
            while self.current_coordinate_index < len(self.sequential_coordinates):
                ok = self.move_to_next_coordinate()
                if not ok:
                    raise RuntimeError(f"Failed to step to next coordinate in quest {qid:03d}")


            # after finishing this file, continue to next JSON, keep same map_id

    # NOTE: Navigation and quest path workflow
    # - env.current_loaded_quest_id tracks the active quest and is synced on load_coordinate_path
    # - load_coordinate_path loads the full multi-map sequence for a quest and snaps to start
    # - load_segment_for_current_map handles per-map segment loading on map changes
    # - move_to_next_coordinate automatically advances steps, handles warps, and auto-loads next quest or segment
    # - Multi-map quests: flattened coordinates include segments on different maps; warp_tile_handler plus snap_to_nearest_coordinate ensure that when a warp occurs, we realign to the next map's coordinates seamlessly
