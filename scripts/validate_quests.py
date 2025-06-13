#!/usr/bin/env python3
"""
Standalone Quest Validation Script
Run this to validate all quest coordinate files before starting the game.

Usage:
    python validate_quests.py
    
This will check for issues like:
- Invalid map IDs (-1 values)
- Missing coordinate files
- Malformed JSON
- Coordinate bounds issues
- Multi-map quest consistency
"""

import json
import sys
from pathlib import Path

def validate_quest_files():
    """Validate all quest coordinate files"""
    quest_paths_dir = Path(__file__).parent / "environment" / "environment_helpers" / "quest_paths"
    
    if not quest_paths_dir.exists():
        print(f"❌ Quest paths directory not found: {quest_paths_dir}")
        return False
    
    print(f"🔍 Validating quest files in: {quest_paths_dir}")
    
    issues_found = []
    quests_validated = 0
    total_coordinates = 0
    
    for quest_dir in sorted(quest_paths_dir.iterdir()):
        if not quest_dir.is_dir() or not quest_dir.name.isdigit():
            continue
            
        quest_id = int(quest_dir.name)
        quest_file = quest_dir / f"{quest_dir.name}_coords.json"
        
        if not quest_file.exists():
            issues_found.append(f"Quest {quest_id:03d}: Missing coordinate file")
            continue
        
        try:
            with open(quest_file, 'r') as f:
                quest_data = json.load(f)
        except Exception as e:
            issues_found.append(f"Quest {quest_id:03d}: JSON parse error - {e}")
            continue
        
        # Validate quest structure
        if not quest_data:
            issues_found.append(f"Quest {quest_id:03d}: Empty coordinate data")
            continue
        
        quest_coords = 0
        invalid_map_ids = []
        
        for segment_key, coords in quest_data.items():
            # Validate segment key format
            try:
                if '_' in segment_key:
                    map_id = int(segment_key.split('_')[0])
                else:
                    map_id = int(segment_key)
            except (ValueError, IndexError):
                issues_found.append(f"Quest {quest_id:03d}: Invalid segment key format '{segment_key}'")
                continue
            
            # Check for invalid map IDs
            if map_id < 0 or map_id > 255:
                invalid_map_ids.append((segment_key, map_id))
            
            # Validate coordinates
            if not coords:
                issues_found.append(f"Quest {quest_id:03d}: Empty coordinates for segment '{segment_key}'")
                continue
            
            for i, coord in enumerate(coords):
                if len(coord) < 2:
                    issues_found.append(f"Quest {quest_id:03d}: Invalid coordinate format at {segment_key}[{i}]: {coord}")
                    continue
                
                try:
                    gy, gx = int(coord[0]), int(coord[1])
                    if gy < 0 or gx < 0 or gy > 10000 or gx > 10000:
                        issues_found.append(f"Quest {quest_id:03d}: Suspicious coordinate at {segment_key}[{i}]: ({gy}, {gx})")
                except (ValueError, TypeError):
                    issues_found.append(f"Quest {quest_id:03d}: Non-numeric coordinate at {segment_key}[{i}]: {coord}")
            
            quest_coords += len(coords)
        
        if invalid_map_ids:
            for segment_key, map_id in invalid_map_ids:
                issues_found.append(f"Quest {quest_id:03d}: Invalid map_id {map_id} in segment '{segment_key}'")
        
        total_coordinates += quest_coords
        quests_validated += 1
        
        if not invalid_map_ids:
            print(f"✅ Quest {quest_id:03d}: {quest_coords} coordinates, {len(quest_data)} segments")
        else:
            print(f"❌ Quest {quest_id:03d}: {quest_coords} coordinates, {len(invalid_map_ids)} map ID issues")
    
    print(f"\n=== VALIDATION SUMMARY ===")
    print(f"Quests validated: {quests_validated}")
    print(f"Total coordinates: {total_coordinates}")
    print(f"Issues found: {len(issues_found)}")
    
    if issues_found:
        print(f"\n❌ ISSUES DETECTED:")
        for issue in issues_found:
            print(f"  - {issue}")
        return False
    else:
        print(f"\n✅ ALL QUEST FILES VALIDATED SUCCESSFULLY!")
        return True

if __name__ == "__main__":
    success = validate_quest_files()
    sys.exit(0 if success else 1) 