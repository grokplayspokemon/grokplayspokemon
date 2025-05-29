import json
import io
from pathlib import Path

def save_initial_state(env, run_dir: Path, name_base: str):
    """Save the initial emulator state to <run_dir>/<name_base>_start.state"""
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

def save_loop_state(env, recorded_playthrough):
    """Save actions and path trace data during the main loop to the current run directory"""
    if not getattr(env, 'current_run_dir', None):
        return
    run_dir = env.current_run_dir
    base_name = run_dir.name
    # Save actions
    actions_file = run_dir / f"{base_name}_actions.json"
    try:
        with open(actions_file, "w") as f:
            json.dump(recorded_playthrough, f, indent=4)
    except Exception as e:
        print(f"Error saving actions to {actions_file}: {e}")
    # Save path trace
    if hasattr(env, 'path_trace_data') and env.path_trace_data:
        coords_file = run_dir / f"{base_name}_coords.json"
        try:
            with open(coords_file, "w") as f:
                json.dump(env.path_trace_data, f, indent=4)
        except Exception as e:
            print(f"Error saving path trace data to {coords_file}: {e}")

def save_final_state(env, run_dir: Path, recorded_playthrough, coords_data, override_name, default_name):
    """Save final actions JSON, coords JSON, and end state file after the session ends"""
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