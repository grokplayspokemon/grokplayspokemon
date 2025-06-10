# warp_tracker
from environment.data.environment_data.tilesets import Tilesets
from environment.data.recorder_data.global_map import local_to_global, global_to_local 

# WarpTracker class encapsulates warp tracking and backtracking logic
class WarpTracker:
    def __init__(self):
        self.tracking = False
        self.backtracking = False
        self.pre_facing = None      # int direction index before warp
        self.post_facing = None     # int direction index after warp
        self.coords = []            # List of (gy, gx) tuples traveled after warp
        self.warp_map_id = None     # Current warp destination map id
        self.prev_map_id = None     # Previous map id to detect map changes
        self.last_facing = None     # Previous frame facing for pre_facing
        self.entry_coord = None     # initial global coord at warp entry, skip until movement
        self.last_len = 0

    def record_step(self, env, navigator):
        """Should be called after each env.step to detect warps and record coordinates."""
        if self.backtracking:
            return
        try:
            x, y, map_id = env.get_game_coords()
            raw_dir = env.read_m("wSpritePlayerStateData1FacingDirection")
            dir_idx = raw_dir // 4  # 0=down,1=up,2=left,3=right
            print(f"WarpTracker: current facing direction: {dir_idx}")
        except Exception:
            return
        prev = self.prev_map_id
        # Detect map change as new warp
        if prev is not None and map_id != prev:
            self.tracking = True
            self.coords.clear()
            self.warp_map_id = map_id
            self.pre_facing = self.last_facing if self.last_facing is not None else dir_idx
            self.post_facing = dir_idx
            print(f"\nWarp detected into map {map_id}. pre_facing={self.pre_facing}, post_facing={self.post_facing}. Starting tracking.")
            # Prevent nested warp detection while pressing B
            self.prev_map_id = map_id
            # FIXED: Use environment's action execution instead of direct step
            for _ in range(3):
                env.process_action(5, source="WarpTracker-Dialog")  # Press B to advance through pause/dialog
            
            # Record the warp entry coordinate, but skip recording until movement
            gy, gx = navigator._get_player_global_coords()
            self.entry_coord = (gy, gx)
        # If already tracking, append new coordinates
        elif self.tracking:
            gy, gx = navigator._get_player_global_coords()
            # On first movement after warp: handle entry vs non-entry differently
            if self.entry_coord is not None and (gy, gx) != self.entry_coord:
                # Compute manhattan distance between entry and first move
                ex, ey = self.entry_coord
                dist = abs(gy - ex) + abs(gx - ey)
                if dist == 1:
                    # contiguous step: record entry coordinate and movement coordinate
                    self.coords.append(self.entry_coord)
                    self.coords.append((gy, gx))
                else:
                    # non-contiguous (large jump): record current coordinate only
                    self.coords.append((gy, gx))
                # Trim history for any extra entries
                while len(self.coords) > 500:
                    self.coords.pop(0)
                # Clear entry marker
                self.entry_coord = None
            else:
                # Subsequent moves or non-entry cases: record if changed
                if not self.coords or self.coords[-1] != (gy, gx):
                    self.coords.append((gy, gx))
                    if len(self.coords) > 500:
                        self.coords.pop(0)
        # Update facing and map id for next detection
        self.last_facing = dir_idx
        self.prev_map_id = map_id
        # Print tracked coords each step
        if self.tracking and self.warp_map_id is not None and len(self.coords) > self.last_len:
            print(f"[WarpTracker] Map {self.warp_map_id} tracked coords (count={len(self.coords)}): {self.coords}")
        self.last_len = len(self.coords)

    def backtrack(self, env, navigator):
        """Reverse backtracks along recorded coords and then applies inverse facings."""
        # first direction is direction player faces on the distal warp tile
        seq = self.coords[1:-1]
        if not self.tracking or self.warp_map_id is None or not self.coords:
            print("No warp tracking data to backtrack.")
            return
        
        print(f"[WarpTracker] Backtracking sequence for map {self.warp_map_id} (reverse order): {list(reversed(seq))}")

        self.backtracking = True
        self.tracking = False
        # Step back along each recorded coordinate
        for target in reversed(seq):
            current = navigator._get_player_global_coords()
            # Skip if already at the target coordinate
            if current == target:
                continue
            print(f"[WarpTracker] Backtracking from {current} to {target}")
            y0, x0 = current
            y1, x1 = target
            dy, dx = y1 - y0, x1 - x0
            # Determine step toward target
            if abs(dy) > abs(dx):
                dir_name = "down" if dy > 0 else "up"
            else:
                dir_name = "right" if dx > 0 else "left"
            action_map = {"down": 0, "left": 1, "right": 2, "up": 3}
            action = action_map.get(dir_name)
            if action is not None:
                env.process_action(action, source="WarpTracker-Backtrack")  # FIXED: Use environment's action execution
            else:
                print(f"[WarpTracker] ERROR: No action for direction {dir_name}")
        # Apply inverse facings
        # 0=down,1=up,2=left,3=right
        inverse = {"down": "up", "up": "down", "left": "right", "right": "left"}
        idx_to_name = {0: "down", 1: "up", 2: "left", 3: "right"}
        for key in ["post_facing"]:
            dir_idx = getattr(self, key)
            if dir_idx is None:
                continue
            dir_name = idx_to_name.get(dir_idx)
            opp = inverse.get(dir_name)
            print(f"[WarpTracker] Executing inverse facing {opp} for {key}={dir_name}")
            action_map = {"down": 0, "left": 1, "right": 2, "up": 3}
            if opp:
                action = action_map.get(opp)
                env.process_action(action, source="WarpTracker-Facing")  # FIXED: Use environment's action execution
                    
        self.backtracking = False
        print("[WarpTracker] Backtracking complete.")
        # FIXED: Use environment's action execution instead of direct step
        for _ in range(3):
            env.process_action(5, source="WarpTracker-Complete")  # Press B to advance through pause/dialog

# Singleton instance of WarpTracker
warp_tracker = WarpTracker()

# Module-level proxies for compatibility

def record_warp_step(env, navigator):
    warp_tracker.record_step(env, navigator)


def backtrack_warp_sequence(env, navigator):
    warp_tracker.backtrack(env, navigator) 