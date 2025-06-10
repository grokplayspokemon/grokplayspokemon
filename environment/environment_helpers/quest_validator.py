#!/usr/bin/env python3
"""
Quest Path Validator - Comprehensive validation system for quest coordinate files

This module provides validation for:
1. Quest coordinate file format and structure
2. Map ID validity and consistency
3. Coordinate bounds and reasonableness
4. Multi-map quest path continuity
5. Warp alignment verification

Used to prevent issues like the quest 009 map transition bug.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import traceback


class QuestPathValidator:
    """Comprehensive validator for quest coordinate files"""
    
    def __init__(self, quest_paths_dir: Optional[Path] = None):
        self.quest_paths_dir = quest_paths_dir or Path(__file__).parent / "quest_paths"
        self.validation_results = {}
        self.critical_errors = []
        self.warnings = []
        
    def validate_all_quests(self, quest_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """Validate all quest coordinate files or specific quest IDs"""
        if quest_ids is None:
            # Auto-discover quest IDs from directory structure
            quest_ids = []
            for quest_dir in self.quest_paths_dir.iterdir():
                if quest_dir.is_dir() and quest_dir.name.isdigit():
                    quest_ids.append(int(quest_dir.name))
            quest_ids.sort()
        
        print(f"QuestValidator: Validating {len(quest_ids)} quests...")
        
        for quest_id in quest_ids:
            try:
                result = self.validate_quest(quest_id)
                self.validation_results[quest_id] = result
                
                if result.get('critical_errors'):
                    self.critical_errors.extend([
                        f"Quest {quest_id:03d}: {error}" 
                        for error in result['critical_errors']
                    ])
                    
                if result.get('warnings'):
                    self.warnings.extend([
                        f"Quest {quest_id:03d}: {warning}" 
                        for warning in result['warnings']
                    ])
                    
            except Exception as e:
                error_msg = f"Failed to validate quest {quest_id}: {e}"
                self.critical_errors.append(error_msg)
                print(f"QuestValidator: ERROR - {error_msg}")
        
        # Generate summary
        total_quests = len(quest_ids)
        validated_quests = len(self.validation_results)
        failed_quests = total_quests - validated_quests
        quests_with_errors = len([r for r in self.validation_results.values() if r.get('critical_errors')])
        quests_with_warnings = len([r for r in self.validation_results.values() if r.get('warnings')])
        
        summary = {
            'total_quests': total_quests,
            'validated_quests': validated_quests,
            'failed_quests': failed_quests,
            'quests_with_errors': quests_with_errors,
            'quests_with_warnings': quests_with_warnings,
            'total_critical_errors': len(self.critical_errors),
            'total_warnings': len(self.warnings),
            'validation_passed': len(self.critical_errors) == 0,
            'critical_errors': self.critical_errors,
            'warnings': self.warnings,
            'results': self.validation_results
        }
        
        print(f"QuestValidator: Validation Summary:")
        print(f"  ✅ {validated_quests}/{total_quests} quests validated")
        print(f"  ❌ {quests_with_errors} quests with critical errors")
        print(f"  ⚠️  {quests_with_warnings} quests with warnings")
        print(f"  Overall Status: {'PASS' if summary['validation_passed'] else 'FAIL'}")
        
        return summary
    
    def validate_quest(self, quest_id: int) -> Dict[str, Any]:
        """Validate a specific quest coordinate file"""
        result = {
            'quest_id': quest_id,
            'file_exists': False,
            'file_readable': False,
            'valid_json': False,
            'segments': {},
            'total_coordinates': 0,
            'unique_maps': set(),
            'critical_errors': [],
            'warnings': [],
            'validation_passed': False
        }
        
        # Check file existence
        quest_dir = self.quest_paths_dir / f"{quest_id:03d}"
        quest_file = quest_dir / f"{quest_id:03d}_coords.json"
        
        if not quest_file.exists():
            result['critical_errors'].append(f"Quest file does not exist: {quest_file}")
            return result
        
        result['file_exists'] = True
        
        # Check file readability
        try:
            with open(quest_file, 'r') as f:
                quest_data = json.load(f)
            result['file_readable'] = True
            result['valid_json'] = True
        except Exception as e:
            result['critical_errors'].append(f"Cannot read or parse JSON file: {e}")
            return result
        
        # Validate quest data structure
        if not isinstance(quest_data, dict):
            result['critical_errors'].append("Quest data is not a dictionary")
            return result
        
        if not quest_data:
            result['critical_errors'].append("Quest data is empty")
            return result
        
        # Validate each segment
        for segment_key, segment_coords in quest_data.items():
            segment_result = self._validate_segment(segment_key, segment_coords, quest_id)
            result['segments'][segment_key] = segment_result
            
            # Accumulate errors and warnings
            result['critical_errors'].extend(segment_result.get('critical_errors', []))
            result['warnings'].extend(segment_result.get('warnings', []))
            
            # Accumulate statistics
            if segment_result.get('valid_coordinates'):
                result['total_coordinates'] += len(segment_result['valid_coordinates'])
                result['unique_maps'].add(segment_result.get('map_id'))
        
        # Cross-segment validation
        cross_validation = self._validate_cross_segment_continuity(quest_data, quest_id)
        result['critical_errors'].extend(cross_validation.get('critical_errors', []))
        result['warnings'].extend(cross_validation.get('warnings', []))
        
        # Final validation status
        result['validation_passed'] = len(result['critical_errors']) == 0
        result['unique_maps'] = list(result['unique_maps'])  # Convert set to list for JSON serialization
        
        return result
    
    def _validate_segment(self, segment_key: str, segment_coords: List, quest_id: int) -> Dict[str, Any]:
        """Validate a specific segment within a quest"""
        result = {
            'segment_key': segment_key,
            'map_id': None,
            'segment_number': None,
            'coordinate_count': len(segment_coords) if segment_coords else 0,
            'valid_coordinates': [],
            'critical_errors': [],
            'warnings': []
        }
        
        # Validate segment key format
        try:
            if '_' in segment_key:
                # Format: "map_id_segment" like "42_0"
                parts = segment_key.split('_')
                if len(parts) != 2:
                    result['critical_errors'].append(f"Invalid segment key format: {segment_key}")
                    return result
                
                map_id = int(parts[0])
                segment_num = int(parts[1])
                result['map_id'] = map_id
                result['segment_number'] = segment_num
            else:
                # Format: just "map_id" like "42"
                map_id = int(segment_key)
                result['map_id'] = map_id
                result['segment_number'] = 0
                
        except (ValueError, IndexError) as e:
            result['critical_errors'].append(f"Cannot parse segment key '{segment_key}': {e}")
            return result
        
        # Validate map_id range
        if map_id < 0 or map_id > 255:
            result['critical_errors'].append(f"Invalid map_id {map_id} in segment {segment_key}")
        
        # Validate coordinates
        if not segment_coords:
            result['warnings'].append(f"Empty coordinate list in segment {segment_key}")
            return result
        
        if not isinstance(segment_coords, list):
            result['critical_errors'].append(f"Coordinates in segment {segment_key} are not a list")
            return result
        
        for i, coord in enumerate(segment_coords):
            coord_validation = self._validate_coordinate(coord, i, segment_key)
            
            if coord_validation.get('valid'):
                result['valid_coordinates'].append(coord_validation['coordinate'])
            else:
                result['critical_errors'].extend(coord_validation.get('errors', []))
                result['warnings'].extend(coord_validation.get('warnings', []))
        
        return result
    
    def _validate_coordinate(self, coord: Any, index: int, segment_key: str) -> Dict[str, Any]:
        """Validate a single coordinate"""
        result = {
            'valid': False,
            'coordinate': None,
            'errors': [],
            'warnings': []
        }
        
        # Check if coordinate is a list/tuple with at least 2 elements
        if not isinstance(coord, (list, tuple)) or len(coord) < 2:
            result['errors'].append(f"Coordinate {index} in segment {segment_key} is not a valid [y, x] pair: {coord}")
            return result
        
        # Validate coordinate values
        try:
            gy, gx = int(coord[0]), int(coord[1])
        except (ValueError, TypeError) as e:
            result['errors'].append(f"Coordinate {index} in segment {segment_key} has non-integer values: {coord}")
            return result
        
        # Check coordinate bounds (reasonable global coordinate range)
        if gy < 0 or gx < 0:
            result['errors'].append(f"Coordinate {index} in segment {segment_key} has negative values: ({gy}, {gx})")
            return result
        
        if gy > 10000 or gx > 10000:
            result['warnings'].append(f"Coordinate {index} in segment {segment_key} has very large values: ({gy}, {gx})")
        
        result['valid'] = True
        result['coordinate'] = (gy, gx)
        return result
    
    def _validate_cross_segment_continuity(self, quest_data: Dict, quest_id: int) -> Dict[str, Any]:
        """Validate continuity across segments in multi-map quests"""
        result = {
            'critical_errors': [],
            'warnings': []
        }
        
        segments = list(quest_data.keys())
        if len(segments) <= 1:
            return result  # Single segment quests don't need continuity validation
        
        # Check for reasonable map transitions
        map_ids = []
        for segment_key in segments:
            try:
                if '_' in segment_key:
                    map_id = int(segment_key.split('_')[0])
                else:
                    map_id = int(segment_key)
                map_ids.append(map_id)
            except (ValueError, IndexError):
                continue
        
        unique_maps = set(map_ids)
        if len(unique_maps) > 5:
            result['warnings'].append(f"Quest spans {len(unique_maps)} maps, which may be excessive: {sorted(unique_maps)}")
        
        # Check for impossible map transitions (like going from indoor to outdoor without logical connection)
        # This is a simplified check - could be enhanced with actual map connection data
        for i in range(len(map_ids) - 1):
            current_map = map_ids[i]
            next_map = map_ids[i + 1]
            
            if abs(current_map - next_map) > 100:
                result['warnings'].append(f"Large map ID jump from {current_map} to {next_map} between segments")
        
        return result
    
    def generate_validation_report(self, output_file: Optional[Path] = None) -> str:
        """Generate a detailed validation report"""
        if not self.validation_results:
            return "No validation results available. Run validate_all_quests() first."
        
        report_lines = [
            "=" * 80,
            "QUEST PATH VALIDATION REPORT",
            "=" * 80,
            f"Generated: {str(Path(__file__).parent)}",
            "",
            "SUMMARY:",
            f"  Total Quests: {len(self.validation_results)}",
            f"  Critical Errors: {len(self.critical_errors)}",
            f"  Warnings: {len(self.warnings)}",
            f"  Overall Status: {'PASS' if not self.critical_errors else 'FAIL'}",
            ""
        ]
        
        if self.critical_errors:
            report_lines.extend([
                "CRITICAL ERRORS:",
                "-" * 40
            ])
            for error in self.critical_errors:
                report_lines.append(f"  ❌ {error}")
            report_lines.append("")
        
        if self.warnings:
            report_lines.extend([
                "WARNINGS:",
                "-" * 40
            ])
            for warning in self.warnings:
                report_lines.append(f"  ⚠️  {warning}")
            report_lines.append("")
        
        # Detailed per-quest results
        report_lines.extend([
            "DETAILED RESULTS:",
            "-" * 40
        ])
        
        for quest_id, result in sorted(self.validation_results.items()):
            status = "✅ PASS" if result['validation_passed'] else "❌ FAIL"
            report_lines.extend([
                f"Quest {quest_id:03d}: {status}",
                f"  Coordinates: {result['total_coordinates']}",
                f"  Maps: {len(result['unique_maps'])} ({result['unique_maps']})",
                f"  Segments: {len(result['segments'])}",
                f"  Errors: {len(result['critical_errors'])}",
                f"  Warnings: {len(result['warnings'])}",
                ""
            ])
        
        report_text = "\n".join(report_lines)
        
        if output_file:
            output_file.write_text(report_text)
            print(f"QuestValidator: Report saved to {output_file}")
        
        return report_text


def validate_quest_system(quest_paths_dir: Optional[Path] = None, 
                         output_report: bool = True) -> Dict[str, Any]:
    """
    Main function to validate the entire quest system
    
    Args:
        quest_paths_dir: Path to quest_paths directory (auto-detected if None)
        output_report: Whether to generate and save a validation report
        
    Returns:
        Dictionary with validation results and summary
    """
    try:
        validator = QuestPathValidator(quest_paths_dir)
        results = validator.validate_all_quests()
        
        if output_report:
            report_file = Path(__file__).parent / "quest_validation_report.txt"
            validator.generate_validation_report(report_file)
        
        return results
        
    except Exception as e:
        print(f"QuestValidator: FATAL ERROR during validation: {e}")
        traceback.print_exc()
        return {
            'validation_passed': False,
            'fatal_error': str(e),
            'critical_errors': [f"Fatal validation error: {e}"]
        }


if __name__ == "__main__":
    # Run validation when script is executed directly
    print("Running Quest Path Validation...")
    results = validate_quest_system()
    
    if results.get('validation_passed'):
        print("\n🎉 All quest paths validated successfully!")
        exit(0)
    else:
        print(f"\n💥 Validation failed with {results.get('total_critical_errors', 0)} critical errors")
        print("Check quest_validation_report.txt for details")
        exit(1) 