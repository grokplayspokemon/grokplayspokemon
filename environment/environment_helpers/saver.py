import json
import io
from pathlib import Path
from .run_manager import RunManager, RunInfo
from datetime import datetime

# Global run manager instance
_run_manager = None

def get_run_manager() -> RunManager:
    """Get the global run manager instance"""
    global _run_manager
    if _run_manager is None:
        _run_manager = RunManager()
    return _run_manager

def save_initial_state(env, run_info: RunInfo):
    """Save the initial emulator state using RunManager"""
    run_manager = get_run_manager()
    run_manager.save_initial_state(env, run_info)

def _recording_disabled(env) -> bool:
    """Helper: return True if environment indicates recordings are disabled."""
    return not getattr(env, "record_replays", False) or getattr(env, "disable_recordings", False)

def save_loop_state(env, recorded_playthrough):
    """Save actions and path trace data during the main loop to the current run directory."""
    # Skip completely when recordings are disabled
    if _recording_disabled(env):
        return

    if not hasattr(env, 'current_run_info') or env.current_run_info is None:
        return

    run_manager = get_run_manager()
    run_info = env.current_run_info

    # Save actions
    run_manager.save_actions(recorded_playthrough, run_info)

    # Save path trace
    if hasattr(env, 'path_trace_data') and env.path_trace_data:
        run_manager.save_coordinates(env.path_trace_data, run_info)

def save_final_state(env, run_info: RunInfo, recorded_playthrough=None, coords_data=None):
    """Save final actions JSON, coords JSON, and end state file after the session ends."""
    if _recording_disabled(env):
        return

    run_manager = get_run_manager()

    # Save final state
    run_manager.save_final_state(env, run_info)

    # Save actions if provided
    if recorded_playthrough is not None:
        run_manager.save_actions(recorded_playthrough, run_info)

    # Save coordinates if provided
    if coords_data is not None:
        run_manager.save_coordinates(coords_data, run_info)

def create_new_run(env, start_map_name: str, map_id: int) -> RunInfo | None:
    """Create a new run directory (unless recordings are disabled) and return RunInfo."""
    if _recording_disabled(env):
        return None

    run_manager = get_run_manager()
    run_info = run_manager.create_run_directory(start_map_name, map_id)

    # Store run info in environment for easy access
    env.current_run_info = run_info
    env.current_run_dir = run_info.run_dir  # Maintain backward compatibility

    return run_info

def load_latest_run(env, map_name: str = None, map_id: int = None) -> RunInfo | None:
    """Load the latest run state and return RunInfo"""
    run_manager = get_run_manager()
    run_info = run_manager.load_latest_state(env, map_name=map_name, map_id=map_id)
    
    if run_info and run_info.state_bytes: # Check if run_info and state_bytes are valid
        print(f"saver.py: load_latest_run(): successfully loaded state from {run_info.state_file}")
        env.current_run_info = run_info
        env.current_run_dir = run_info.run_dir  # Maintain backward compatibility

        # Reset the environment with the loaded state, marking it as an internal call
        # to prevent re-triggering run creation logic within env.reset()
        # This call itself might try to load quest/trigger statuses and persist them.
        _, infos = env.reset(options={'state': run_info.state_bytes}, _is_internal_call=True)
        
        # After env.reset, if infos contains loaded statuses, they are from this specific load.
        # The env.reset call should have updated current_call_infos and potentially self.persisted_...
        # No need to explicitly load them again here as env.reset handles it internally.
        
        return run_info # Return the valid run_info
    else:
        if run_info: # run_info exists but state_bytes might be missing/empty
             print(f"saver.py: load_latest_run(): Found run {run_info.run_id} but state_bytes are missing or invalid. Cannot load this run.")
        else: # run_info is None
            print(f"saver.py: load_latest_run(): No suitable run found by RunManager or error loading state. Cannot load latest run.")
        # Potentially clear persisted statuses in env if we expect a fresh start when load fails.
        # This might be too aggressive if a previous valid load happened in an earlier reset.
        # env.persisted_loaded_quest_statuses = {}
        # env.persisted_loaded_trigger_statuses = {}
        return None # Return None if loading failed or no valid state bytes

def save_quest_status(quest_status: dict, run_info: RunInfo):
    """Save quest status data"""
    run_manager = get_run_manager()
    run_manager.save_quest_status(quest_status, run_info)

def save_trigger_status(trigger_status: dict, run_info: RunInfo):
    """Save trigger status data"""
    run_manager = get_run_manager()
    run_manager.save_trigger_status(trigger_status, run_info)

def load_quest_progress(run_info: RunInfo) -> dict:
    """Load quest progress from a run"""
    run_manager = get_run_manager()
    return run_manager.load_quest_progress(run_info)

def load_trigger_status(run_info: RunInfo) -> dict:
    """Load trigger status from a run"""
    run_manager = get_run_manager()
    return run_manager.load_trigger_status(run_info)

def save_manual_state(env, run_dir: Path, save_name: str):
    """Save a manual state when user presses Ctrl+S.

    The state file is placed directly inside the active run directory so it is
    visible alongside other artefacts in `environment/replays/recordings/…`.
    """
    try:
        run_dir.mkdir(parents=True, exist_ok=True)

        # Save the state file in the run directory
        save_path = run_dir / f"{save_name}.state"
        with open(save_path, "wb") as f:
            env.pyboy.save_state(f)
        
        # Also save current game coordinates and quest info
        info_file = run_dir / f"{save_name}_info.json"
        try:
            x, y, map_id = env.get_game_coords()
            info_data = {
                'save_name': save_name,
                'coordinates': {'x': x, 'y': y, 'map_id': map_id},
                'current_quest_id': getattr(env, 'current_loaded_quest_id', None),
                'timestamp': save_name  # save_name includes timestamp
            }
            with open(info_file, 'w') as f:
                json.dump(info_data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save manual state info: {e}")
            
        print(f"Manual state saved to {save_path}")
        return True
        
    except Exception as e:
        print(f"Error saving manual state: {e}")
        return False

# Legacy compatibility functions - these maintain the old interface
def save_initial_state_legacy(env, run_dir: Path, name_base: str):
    """Legacy function for backward compatibility"""
    start_buf = io.BytesIO()
    env.pyboy.save_state(start_buf)
    start_buf.seek(0)
    start_state_file = run_dir / f"{name_base}_start.state"
    try:
        with open(start_state_file, "wb") as f:
            f.write(start_buf.read())
        print(f"Start state saved to {start_state_file}")
    except Exception as e:
        print(f"Error saving initial state to {start_state_file}: {e}")

def save_final_state_legacy(env, run_dir: Path, recorded_playthrough, coords_data, override_name, default_name):
    """Legacy function for backward compatibility"""
    # Determine filenames based on override_name or default_name
    if override_name:
        if override_name.lower().endswith('.json'):
            actions_filename = override_name
            name_base = Path(actions_filename).stem
        else:
            actions_filename = override_name + '.json'
            name_base = override_name
        actions_file = run_dir / actions_filename
        coords_file = run_dir / f"{name_base}_coords.json"
        end_state_file = run_dir / f"{name_base}_end.state"
    else:
        actions_file = run_dir / f"quest_id_{default_name}.json"
        coords_file = run_dir / f"{default_name}_coords.json"
        end_state_file = run_dir / f"{default_name}_end.state"
    
    # Save actions
    try:
        with open(actions_file, "w") as f:
            json.dump(recorded_playthrough, f, indent=4)
        print(f"Playthrough actions saved to {actions_file}")
    except Exception as e:
        print(f"Error saving final actions to {actions_file}: {e}")
    
    # Save coords
    try:
        with open(coords_file, "w") as f:
            json.dump(coords_data, f, indent=4)
        print(f"Coordinates saved to {coords_file}")
    except Exception as e:
        print(f"Error saving final coordinates to {coords_file}: {e}")
    
    # Save end state
    try:
        end_buf = io.BytesIO()
        env.pyboy.save_state(end_buf)
        end_buf.seek(0)
        with open(end_state_file, "wb") as f:
            f.write(end_buf.read())
        print(f"End state saved to {end_state_file}")
    except Exception as e:
        print(f"Error saving end state to {end_state_file}: {e}")

# NEW: Full snapshot manual save helper
def save_full_snapshot(env, recorded_playthrough=None, coords_data=None, snapshot_name: str | None = None, base_run_info: RunInfo | None = None):
    """Manually save *all* data that would normally be persisted at graceful shutdown.

    This helper is intended to be called while the game is still running (e.g. via
    a hot-key).  It gathers the same artefacts that are written when the session
    terminates – emulator end-state, action log, coordinate trace, quest and
    trigger status – and places them into a dedicated timestamped directory under
    the current run.  The directory structure mirrors that of a normal run so it
    can be re-loaded by existing tooling.
    """
    # NOTE: Unlike automatic recordings, manual snapshots should always be allowed
    # even when the user has disabled periodic recordings.  Therefore we *do not*
    # abort when _recording_disabled(env) is True.

    # Determine which run information to use
    if base_run_info is None:
        base_run_info = getattr(env, "current_run_info", None)
    if base_run_info is None:
        print("save_full_snapshot(): no active run – cannot snapshot")
        return False

    # Resolve snapshot directory/name
    if snapshot_name is None:
        snapshot_name = datetime.now().strftime("%Y%m%d_%H%M%S")

    snapshot_dir = base_run_info.run_dir / "manual_snapshots" / snapshot_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Construct a temporary RunInfo pointing at the snapshot directory
    snapshot_run_info = RunInfo(
        run_id=f"{base_run_info.run_id}__manual_{snapshot_name}",
        start_map_name=base_run_info.start_map_name,
        map_id=base_run_info.map_id,
        date=base_run_info.date,
        sequence=base_run_info.sequence,
        run_dir=snapshot_dir,
        start_state_path=snapshot_dir / f"{snapshot_name}_start.state",  # not used but kept for completeness
        end_state_path=snapshot_dir / f"{snapshot_name}_end.state",
        coords_path=snapshot_dir / f"{snapshot_name}_coords.json",
        actions_path=snapshot_dir / f"{snapshot_name}_actions.json",
        quest_status_path=snapshot_dir / "quest_status.json",
        trigger_status_path=snapshot_dir / "trigger_status.json",
    )

    run_manager = get_run_manager()

    # 1. Emulator state (equivalent to end state on shutdown)
    run_manager.save_final_state(env, snapshot_run_info)

    # 2. Action log
    if recorded_playthrough is None:
        recorded_playthrough = getattr(env, "recorded_playthrough", None)
    if recorded_playthrough:
        run_manager.save_actions(recorded_playthrough, snapshot_run_info)

    # 3. Coordinate trace
    if coords_data is None:
        coords_data = getattr(env, "path_trace_data", None)
    if coords_data:
        run_manager.save_coordinates(coords_data, snapshot_run_info)

    # 4. Quest / trigger status – copy the latest JSON if it exists
    for attr_name, save_func in [
        ("quest_status_path", run_manager.save_quest_status),
        ("trigger_status_path", run_manager.save_trigger_status),
    ]:
        source_path = getattr(base_run_info, attr_name)
        if source_path and source_path.exists():
            try:
                with open(source_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                save_func(data, snapshot_run_info)
            except Exception as e:
                print(f"save_full_snapshot(): could not copy {attr_name}: {e}")

    print(f"Full manual snapshot saved to {snapshot_dir}")
    return True 