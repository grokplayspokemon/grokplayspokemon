import json
import io
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass


@dataclass
class RunInfo:
    """Information about a game run"""
    run_id: str
    start_map_name: str
    map_id: int
    date: str
    sequence: int
    run_dir: Path
    start_state_path: Optional[Path] = None
    end_state_path: Optional[Path] = None
    coords_path: Optional[Path] = None
    actions_path: Optional[Path] = None
    quest_status_path: Optional[Path] = None
    trigger_status_path: Optional[Path] = None
    state_bytes: Optional[bytes] = None  # Store loaded state bytes
    state_file: Optional[Path] = None    # Store which file the state was loaded from


class RunManager:
    """Unified run management system for Pokemon Red gameplay sessions"""
    
    def __init__(self, base_recordings_dir: Optional[Path] = None):
        """
        Initialize the run manager
        
        Args:
            base_recordings_dir: Base directory for recordings. Defaults to environment/replays/recordings
        """
        if base_recordings_dir is None:
            # Default to the standard recordings directory
            self.base_dir = Path(__file__).parent.parent / "replays" / "recordings"
        else:
            self.base_dir = Path(base_recordings_dir)
        
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
    def generate_run_id(self, start_map_name: str, map_id: int) -> str:
        """
        Generate a unique run ID following the format: sequence-start_map_name-map_id-ddmmyyyy
        
        Args:
            start_map_name: Name of the starting map
            map_id: ID of the starting map
            
        Returns:
            Unique run ID string
        """
        # Clean map name for filesystem compatibility
        clean_map_name = re.sub(r'[^\w\-_]', '_', start_map_name.upper())
        
        # Get current date in ddmmyyyy format
        date_str = datetime.now().strftime("%d%m%Y")
        
        # Find the next sequence number for this date (across all maps)
        pattern = f"-{date_str}"
        existing_runs = [d.name for d in self.base_dir.iterdir() if d.is_dir() and pattern in d.name]
        
        if not existing_runs:
            sequence = 1
        else:
            # Extract sequence numbers and find the next one
            sequences = []
            for run_name in existing_runs:
                try:
                    # New format: sequence-mapname-mapid-date
                    seq_part = run_name.split('-')[0]
                    if seq_part.isdigit():
                        sequences.append(int(seq_part))
                except (ValueError, IndexError):
                    continue
            sequence = max(sequences, default=0) + 1
        
        return f"{sequence:03d}-{clean_map_name}-{map_id}-{date_str}"
    
    def create_run_directory(self, start_map_name: str, map_id: int) -> RunInfo:
        """
        Create a new run directory with standardized structure
        
        Args:
            start_map_name: Name of the starting map
            map_id: ID of the starting map
            
        Returns:
            RunInfo object with directory paths
        """
        run_id = self.generate_run_id(start_map_name, map_id)
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        
        date_str = datetime.now().strftime("%d%m%Y")
        sequence = int(run_id.split('-')[0])
        
        run_info = RunInfo(
            run_id=run_id,
            start_map_name=start_map_name,
            map_id=map_id,
            date=date_str,
            sequence=sequence,
            run_dir=run_dir,
            start_state_path=run_dir / f"{run_id}_start.state",
            end_state_path=run_dir / f"{run_id}_end.state",
            coords_path=run_dir / f"{run_id}_coords.json",
            actions_path=run_dir / f"{run_id}_actions.json",
            quest_status_path=run_dir / "quest_status.json",
            trigger_status_path=run_dir / "trigger_status.json"
        )
        
        # Create a run metadata file
        metadata = {
            "run_id": run_id,
            "start_map_name": start_map_name,
            "map_id": map_id,
            "created_at": datetime.now().isoformat(),
            "files": {
                "start_state": f"{run_id}_start.state",
                "end_state": f"{run_id}_end.state",
                "coordinates": f"{run_id}_coords.json",
                "actions": f"{run_id}_actions.json",
                "quest_status": "quest_status.json",
                "trigger_status": "trigger_status.json"
            }
        }
        
        metadata_path = run_dir / "run_metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)
        
        print(f"Created run directory: {run_dir}")
        return run_info
    
    def get_latest_run(self, map_name: Optional[str] = None, map_id: Optional[int] = None) -> Optional[RunInfo]:
        """
        Get the most recent run, optionally filtered by map
        
        Args:
            map_name: Filter by starting map name (optional)
            map_id: Filter by starting map ID (optional)
            
        Returns:
            RunInfo for the latest run, or None if no runs found
        """
        runs = self.list_runs(map_name=map_name, map_id=map_id)
        if not runs:
            return None
        
        # FIXED: Use actual creation timestamp from metadata for proper sorting
        def sort_key(r):
            try:
                # Try to read creation timestamp from metadata
                metadata_path = r.run_dir / "run_metadata.json"
                if metadata_path.exists():
                    with open(metadata_path, 'r') as f:
                        metadata = json.load(f)
                        created_at_str = metadata.get("created_at")
                        if created_at_str:
                            # Parse ISO format timestamp
                            from datetime import datetime
                            dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            return dt
                
                # Fallback to date parsing from directory name
                date_str = r.date  # ddmmyyyy format
                if len(date_str) == 8 and date_str.isdigit():
                    day, month, year = date_str[:2], date_str[2:4], date_str[4:8]
                    # Convert to datetime for proper chronological sorting
                    from datetime import datetime
                    dt = datetime(int(year), int(month), int(day))
                    # Add sequence as microseconds for sub-day ordering
                    dt = dt.replace(microsecond=r.sequence * 1000)
                    return dt
                else:
                    # Fallback for malformed dates - use string comparison
                    print(f"Warning: Malformed date '{date_str}' in run {r.run_id}")
                    return datetime.min
            except Exception as e:
                print(f"Warning: Error parsing timestamp for run {r.run_id}: {e}")
                return datetime.min
        
        runs.sort(key=sort_key, reverse=True)
        return runs[0]
    
    def list_runs(self, map_name: Optional[str] = None, map_id: Optional[int] = None) -> List[RunInfo]:
        """
        List all runs, optionally filtered by map
        
        Args:
            map_name: Filter by starting map name (optional)
            map_id: Filter by starting map ID (optional)
            
        Returns:
            List of RunInfo objects
        """
        runs = []
        
        for run_dir in self.base_dir.iterdir():
            if not run_dir.is_dir():
                continue
                
            # Try to parse the directory name
            try:
                run_info = self._parse_run_directory(run_dir)
                if run_info is None:
                    continue
                    
                # Apply filters
                if map_name is not None:
                    clean_filter_name = re.sub(r'[^\w\-_]', '_', map_name.upper())
                    if run_info.start_map_name != clean_filter_name:
                        continue
                        
                if map_id is not None and run_info.map_id != map_id:
                    continue
                    
                runs.append(run_info)
                
            except Exception as e:
                print(f"Warning: Could not parse run directory {run_dir.name}: {e}")
                continue
        
        return runs
    
    def _parse_run_directory(self, run_dir: Path) -> Optional[RunInfo]:
        """
        Parse a run directory to extract RunInfo
        
        Args:
            run_dir: Path to the run directory
            
        Returns:
            RunInfo object or None if parsing fails
        """
        # Try to parse from metadata file first
        metadata_path = run_dir / "run_metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
                
                # Parse run_id to extract date and sequence (handle both formats)
                run_id_parts = metadata["run_id"].split('-')
                if run_id_parts[0].isdigit():
                    # New format: sequence-mapname-mapid-date
                    sequence = int(run_id_parts[0])
                    date = run_id_parts[3]
                else:
                    # Old format: mapname-mapid-date-sequence
                    sequence = int(run_id_parts[3])
                    date = run_id_parts[2]
                
                return RunInfo(
                    run_id=metadata["run_id"],
                    start_map_name=metadata["start_map_name"],
                    map_id=metadata["map_id"],
                    date=date,
                    sequence=sequence,
                    run_dir=run_dir,
                    start_state_path=run_dir / metadata["files"]["start_state"],
                    end_state_path=run_dir / metadata["files"]["end_state"],
                    coords_path=run_dir / metadata["files"]["coordinates"],
                    actions_path=run_dir / metadata["files"]["actions"],
                    quest_status_path=run_dir / metadata["files"]["quest_status"],
                    trigger_status_path=run_dir / metadata["files"]["trigger_status"]
                )
            except Exception as e:
                print(f"Warning: Could not parse metadata for {run_dir.name}: {e}")
        
        # Fallback: try to parse from directory name
        name_parts = run_dir.name.split('-')
        if len(name_parts) >= 4:
            try:
                # Try new format first: sequence-mapname-mapid-date
                if name_parts[0].isdigit():
                    sequence = int(name_parts[0])
                    start_map_name = name_parts[1]
                    map_id = int(name_parts[2])
                    date = name_parts[3]
                else:
                    # Old format: mapname-mapid-date-sequence
                    start_map_name = name_parts[0]
                    map_id = int(name_parts[1])
                    date = name_parts[2]
                    sequence = int(name_parts[3])
                
                run_id = run_dir.name
                
                return RunInfo(
                    run_id=run_id,
                    start_map_name=start_map_name,
                    map_id=map_id,
                    date=date,
                    sequence=sequence,
                    run_dir=run_dir,
                    start_state_path=run_dir / f"{run_id}_start.state",
                    end_state_path=run_dir / f"{run_id}_end.state",
                    coords_path=run_dir / f"{run_id}_coords.json",
                    actions_path=run_dir / f"{run_id}_actions.json",
                    quest_status_path=run_dir / "quest_status.json",
                    trigger_status_path=run_dir / "trigger_status.json"
                )
            except (ValueError, IndexError):
                pass
        
        return None
    
    def save_initial_state(self, env, run_info: RunInfo):
        """Save the initial emulator state"""
        try:
            start_buf = io.BytesIO()
            env.pyboy.save_state(start_buf)
            start_buf.seek(0)
            
            with open(run_info.start_state_path, "wb") as f:
                f.write(start_buf.read())
            print(f"Start state saved to {run_info.start_state_path}")
        except Exception as e:
            print(f"Error saving initial state to {run_info.start_state_path}: {e}")
    
    def save_final_state(self, env, run_info: RunInfo):
        """Save the final emulator state"""
        try:
            end_buf = io.BytesIO()
            env.pyboy.save_state(end_buf)
            end_buf.seek(0)
            
            with open(run_info.end_state_path, "wb") as f:
                f.write(end_buf.read())
            print(f"End state saved to {run_info.end_state_path}")
        except Exception as e:
            print(f"Error saving end state to {run_info.end_state_path}: {e}")
    
    def save_coordinates(self, coords_data: Dict[str, Any], run_info: RunInfo):
        """Save coordinate trace data"""
        try:
            with open(run_info.coords_path, "w") as f:
                json.dump(coords_data, f, indent=4)
            print(f"Coordinates saved to {run_info.coords_path}")
        except Exception as e:
            print(f"Error saving coordinates to {run_info.coords_path}: {e}")
    
    def save_actions(self, actions_data: List[Any], run_info: RunInfo):
        """Save action sequence data"""
        try:
            with open(run_info.actions_path, "w") as f:
                json.dump(actions_data, f, indent=4)
            print(f"Actions saved to {run_info.actions_path}")
        except Exception as e:
            print(f"Error saving actions to {run_info.actions_path}: {e}")
    
    def save_quest_status(self, quest_status: Dict[str, Any], run_info: RunInfo):
        """Save quest status data"""
        try:
            with open(run_info.quest_status_path, "w") as f:
                json.dump(quest_status, f, indent=4)
            print(f"Quest status saved to {run_info.quest_status_path}")
        except Exception as e:
            print(f"Error saving quest status to {run_info.quest_status_path}: {e}")
    
    def save_trigger_status(self, trigger_status: Dict[str, Any], run_info: RunInfo):
        """Save trigger status data"""
        try:
            with open(run_info.trigger_status_path, "w") as f:
                json.dump(trigger_status, f, indent=4)
            print(f"Trigger status saved to {run_info.trigger_status_path}")
        except Exception as e:
            print(f"Error saving trigger status to {run_info.trigger_status_path}: {e}")
    
    def load_latest_state(self, env, map_name: Optional[str] = None, map_id: Optional[int] = None) -> Optional[RunInfo]:
        """
        Load the latest game state, optionally filtered by map
        
        Args:
            env: Environment instance
            map_name: Filter by starting map name (optional)
            map_id: Filter by starting map ID (optional)
            
        Returns:
            RunInfo of the loaded run, or None if no suitable run found
        """
        latest_run = self.get_latest_run(map_name=map_name, map_id=map_id)
        print(f"run_manager.py: load_latest_state(): latest_run: {latest_run}")

        # ------------------------------------------------------------------
        # Fallback – try to locate *any* '.state' file under recordings/ when
        # the directory naming scheme does not match the expected pattern
        # (e.g. legacy runs such as 'quest_26_beat_rival_on_route_22_…').
        # ------------------------------------------------------------------
        if latest_run is None:
            print("run_manager.py: load_latest_state(): No previous runs found via naming scheme. Performing wildcard search …")
            try:
                state_paths = sorted(self.base_dir.rglob("*.state"), key=lambda p: p.stat().st_mtime, reverse=True)
                if state_paths:
                    state_path = state_paths[0]
                    print(f"run_manager.py: load_latest_state(): Fallback found state file {state_path}")

                    # Synthesise minimal RunInfo so caller logic stays unchanged
                    run_dir = state_path.parent
                    run_id = run_dir.name
                    # Attempt to derive map_id from run_id; fallback to 0
                    try:
                        map_id = int(run_id.split("_")[1]) if "_" in run_id else 0
                    except Exception:
                        map_id = 0

                    qs_path = run_dir / "quest_status.json"
                    trg_path = run_dir / "trigger_status.json"

                    fallback_info = RunInfo(
                        run_id=run_id,
                        start_map_name="UNKNOWN",
                        map_id=map_id,
                        date=datetime.now().strftime("%d%m%Y"),
                        sequence=0,
                        run_dir=run_dir,
                        end_state_path=state_path,
                        quest_status_path=qs_path,
                        trigger_status_path=trg_path,
                        state_file=state_path,
                    )
                    # Attempt to load it immediately
                    try:
                        with open(state_path, "rb") as f:
                            state_bytes = f.read()
                        env.pyboy.load_state(io.BytesIO(state_bytes))
                        fallback_info.state_bytes = state_bytes
                        print("run_manager.py: load_latest_state(): Successfully loaded fallback state file")
                        return fallback_info
                    except Exception as e:
                        print(f"run_manager.py: load_latest_state(): Error loading fallback state: {e}")
                        # Continue to return None below
            except Exception as e:
                print(f"run_manager.py: load_latest_state(): wildcard search failed: {e}")

            print("run_manager.py: load_latest_state(): No suitable state file located via fallback search")
            return None
        
        # Try to load end state first, then start state
        state_path = None
        if latest_run.end_state_path and latest_run.end_state_path.exists():
            state_path = latest_run.end_state_path
            print(f"run_manager.py: load_latest_state(): Loading end state from {latest_run.run_id} at {state_path}")
        elif latest_run.start_state_path and latest_run.start_state_path.exists():
            state_path = latest_run.start_state_path
            print(f"run_manager.py: load_latest_state(): Loading start state from {latest_run.run_id} at {state_path}")
        
        if state_path:
            print(f"run_manager.py: load_latest_state(): attempting to load state from: {state_path}")
            try:
                # Read state bytes first
                with open(state_path, "rb") as f:
                    state_bytes = f.read()
                
                # Store state bytes and file path in RunInfo
                latest_run.state_bytes = state_bytes
                latest_run.state_file = state_path
                
                # Load state into environment
                env.pyboy.load_state(io.BytesIO(state_bytes))
                print(f"run_manager.py: load_latest_state(): successfully loaded state from {state_path}")
                return latest_run
            except Exception as e:
                print(f"run_manager.py: load_latest_state(): error loading state from {state_path}: {e}")
        
        return None
    
    def load_quest_progress(self, run_info: RunInfo) -> Optional[Dict[str, Any]]:
        """Load quest progress from a run"""
        if not run_info.quest_status_path.exists():
            print(f"run_manager.py: load_quest_progress(): quest_status_path does not exist: run_info.quest_status_path: {run_info.quest_status_path}")
            return None
        
        try:
            print(f"run_manager.py: load_quest_progress(): attempting to load quest status from: {run_info.quest_status_path}")
            with open(run_info.quest_status_path, "r") as f:
                loaded_quest_status = json.load(f)
                print(f"run_manager.py: load_quest_progress(): parsing json from {run_info.quest_status_path}\nparsed json: {loaded_quest_status}")
                return loaded_quest_status
        except Exception as e:
            print(f"run_manager.py: load_quest_progress(): error loading quest status from {run_info.quest_status_path}: {e}")
            return None
    
    def load_trigger_status(self, run_info: RunInfo) -> Optional[Dict[str, Any]]:
        """Load trigger status from a run"""
        if not run_info.trigger_status_path.exists():
            print(f"run_manager.py: load_trigger_status(): trigger_status_path does not exist: run_info.trigger_status_path: {run_info.trigger_status_path}")
            return None
        
        try:
            print(f"run_manager.py: load_trigger_status(): attempting to load trigger status from: {run_info.trigger_status_path}")
            with open(run_info.trigger_status_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"run_manager.py: load_trigger_status(): error loading trigger status from {run_info.trigger_status_path}: {e}")
            return None
    
    def cleanup_old_runs(self, keep_latest: int = 10, map_name: Optional[str] = None, map_id: Optional[int] = None):
        """
        Clean up old runs, keeping only the most recent ones
        
        Args:
            keep_latest: Number of latest runs to keep
            map_name: Filter by starting map name (optional)
            map_id: Filter by starting map ID (optional)
        """
        runs = self.list_runs(map_name=map_name, map_id=map_id)
        runs.sort(key=lambda r: (r.date, r.sequence), reverse=True)
        
        runs_to_delete = runs[keep_latest:]
        
        for run_info in runs_to_delete:
            try:
                import shutil
                shutil.rmtree(run_info.run_dir)
                print(f"Deleted old run: {run_info.run_id}")
            except Exception as e:
                print(f"Error deleting run {run_info.run_id}: {e}") 