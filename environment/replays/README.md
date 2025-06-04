# Unified Run Management System

This document describes the unified run management system for Pokemon Red gameplay sessions.

## Overview

The run management system provides a standardized way to save, load, and organize all data from Pokemon Red gameplay sessions. Each run is contained within its own unique directory with a standardized naming convention and file structure.

## Directory Structure

### Run Directory Naming Convention

All runs follow the format: `start_map_name-map_id-ddmmyyyy-sequence`

Examples:
- `PALLET_TOWN-37-01062025-001`
- `PALLET_TOWN-37-01062025-002`
- `OAKS_LAB-38-01062025-001`
- `VIRIDIAN_CITY-1-02062025-001`

Where:
- `start_map_name`: Cleaned map name (uppercase, special chars replaced with underscores)
- `map_id`: Numeric ID of the starting map
- `ddmmyyyy`: Date in day-month-year format
- `sequence`: 3-digit sequence number (001, 002, etc.) for multiple runs on the same day/map

### File Structure

Each run directory contains:

```
PALLET_TOWN-37-01062025-001/
├── run_metadata.json                           # Run metadata and file manifest
├── PALLET_TOWN-37-01062025-001_start.state    # Initial emulator state
├── PALLET_TOWN-37-01062025-001_end.state      # Final emulator state
├── PALLET_TOWN-37-01062025-001_coords.json    # Coordinate trace data
├── PALLET_TOWN-37-01062025-001_actions.json   # Action sequence data
├── quest_status.json                           # Quest progress and status
└── trigger_status.json                         # Trigger evaluation status
```

## Core Components

### RunManager Class

The `RunManager` class (`environment/environment_helpers/run_manager.py`) is the central component that handles all run operations:

- **Creating runs**: `create_run_directory(start_map_name, map_id)`
- **Listing runs**: `list_runs(map_name=None, map_id=None)`
- **Finding latest**: `get_latest_run(map_name=None, map_id=None)`
- **Loading state**: `load_latest_state(env, map_name=None, map_id=None)`
- **Saving data**: Various save methods for different data types

### Saver Module

The `saver.py` module provides a simplified interface to the RunManager:

```python
from environment_helpers.saver import create_new_run, load_latest_run, save_quest_status

# Create a new run
run_info = create_new_run(env, "PALLET_TOWN", 37)

# Load the latest run for a specific map
run_info = load_latest_run(env, map_name="PALLET_TOWN", map_id=37)

# Save quest progress
save_quest_status(quest_data, run_info)
```

## Usage Examples

### Creating a New Run

```python
from environment_helpers.saver import create_new_run

# Create new run for Pallet Town (map ID 37)
run_info = create_new_run(env, "PALLET_TOWN", 37)
print(f"Created run: {run_info.run_id}")
print(f"Directory: {run_info.run_dir}")
```

### Loading the Latest Run

```python
from environment_helpers.saver import load_latest_run

# Load latest run from any map
run_info = load_latest_run(env)

# Load latest run from specific map
run_info = load_latest_run(env, map_name="PALLET_TOWN", map_id=37)

if run_info:
    print(f"Loaded run: {run_info.run_id}")
    # Game state is automatically loaded into the environment
```

### Saving Data During Gameplay

```python
from environment_helpers.saver import save_loop_state, save_quest_status

# Save current actions and coordinates (called periodically)
save_loop_state(env, recorded_actions)

# Save quest progress
quest_status = {"current_quest": 12, "completed": [1, 2, 3]}
save_quest_status(quest_status, env.current_run_info)
```

### Loading Previous Progress

```python
from environment_helpers.saver import load_quest_progress, load_trigger_status

# Load quest progress from a run
quest_data = load_quest_progress(run_info)
if quest_data:
    current_quest = quest_data.get("current_quest")
    completed_quests = quest_data.get("completed", [])

# Load trigger status
trigger_data = load_trigger_status(run_info)
```

## Integration with Play.py

The main `play.py` script automatically uses the RunManager system:

1. **Startup**: Creates a new run or loads the latest run based on command line arguments
2. **During gameplay**: Periodically saves actions and coordinates
3. **Shutdown**: Saves final state and all accumulated data

### Command Line Options

```bash
# Create a new run (default behavior)
python play.py

# Load the latest run for the current map
python play.py --run_dir latest

# Use a specific directory (legacy support)
python play.py --run_dir /path/to/custom/dir
```

## Configuration Options

### init_from_last_ending_state

When `init_from_last_ending_state: True` is set in the environment configuration, the system will automatically:

1. **Find the latest run**: Search all run directories in `/puffertank/grok_plays_pokemon/environment/replays/recordings/`
2. **Load the ending state**: Use the `*_end.state` file from the most recent run (by date and sequence number)
3. **Restore quest progress**: Load `quest_status.json` and `trigger_status.json` to continue where you left off
4. **Resume action history**: Access to the previous run's coordinate and action data for context

This allows seamless continuation across different gameplay sessions, maintaining full quest progress and game state.

Example configuration:
```yaml
environment:
  init_from_last_ending_state: true
  # ... other config options
```

The system determines the "latest" run by:
1. Parsing directory names to extract dates (ddmmyyyy format)
2. Finding the most recent date
3. Within that date, finding the highest sequence number
4. Loading the end state from that run's `*_end.state` file

## Data Persistence

### Automatic Saving

The system automatically saves:
- **Initial state**: When a run starts
- **Loop data**: Periodically during gameplay (actions and coordinates)
- **Quest progress**: When quest status changes
- **Final state**: When the session ends

### Manual Saving

You can manually save specific data:

```python
from environment_helpers.saver import save_final_state

# Save everything at once
save_final_state(env, run_info, recorded_actions, coords_data)
```

## File Formats

### run_metadata.json
```json
{
    "run_id": "PALLET_TOWN-37-01062025-001",
    "start_map_name": "PALLET_TOWN",
    "map_id": 37,
    "created_at": "2025-06-01T00:30:19.320035",
    "files": {
        "start_state": "PALLET_TOWN-37-01062025-001_start.state",
        "end_state": "PALLET_TOWN-37-01062025-001_end.state",
        "coordinates": "PALLET_TOWN-37-01062025-001_coords.json",
        "actions": "PALLET_TOWN-37-01062025-001_actions.json",
        "quest_status": "quest_status.json",
        "trigger_status": "trigger_status.json"
    }
}
```

### Coordinates Format
```json
{
    "37": [[100, 200], [101, 201], [102, 202]],
    "1": [[150, 250], [151, 251]]
}
```

### Quest Status Format
```json
{
    "current_quest": 12,
    "completed": [1, 2, 3, 4, 5],
    "failed": [],
    "last_updated": "2025-06-01T00:30:19.320035"
}
```

## Migration from Legacy System

The new system maintains backward compatibility with the old `current_run_dir` approach:

- `env.current_run_dir` is still set for legacy code
- `env.current_run_info` provides access to the new RunInfo object
- Old save functions are preserved as `*_legacy` functions

## Default Location

All runs are stored in: `/puffertank/grok_plays_pokemon/environment/replays/recordings/`

This can be customized by passing a different `base_recordings_dir` to the RunManager constructor.

## Cleanup

The system includes automatic cleanup functionality:

```python
# Keep only the 10 most recent runs
run_manager.cleanup_old_runs(keep_latest=10)

# Clean up runs for a specific map
run_manager.cleanup_old_runs(keep_latest=5, map_name="PALLET_TOWN")
```

## Benefits

1. **Unified Structure**: All run data is consistently organized
2. **Easy Resume**: Load the exact state where you left off
3. **Quest Continuity**: Quest progress is preserved across sessions
4. **Data Integrity**: Comprehensive metadata ensures data can always be parsed
5. **Filtering**: Easy to find runs by map, date, or other criteria
6. **Scalability**: System handles many runs efficiently
7. **Backward Compatibility**: Works with existing code

## Troubleshooting

### Common Issues

1. **Permission Errors**: Ensure the recordings directory is writable
2. **Disk Space**: Monitor disk usage as runs can accumulate
3. **Corrupted States**: Use the metadata to identify and remove problematic runs

### Debugging

Enable debug output by setting environment variables:
```bash
export POKEMON_DEBUG=1
python play.py
```

This will provide detailed logging of all save/load operations. 