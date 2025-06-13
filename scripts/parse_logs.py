#!/usr/bin/env python3
"""
Comprehensive Pokemon Game Log Parser
=====================================

This script extracts and analyzes all important information from Pokemon game logs
to enable easy tracking of changes over time and diagnostic analysis.

Enhanced to work with the new line-based log rotation system and comprehensive
event pattern matching for Pokemon game analysis.

Usage:
    python parse_logs.py [logfile]                # Parse specific log file
    python parse_logs.py                          # Parse from stdin
    python parse_logs.py --analyze                # Run analysis on all logs in directory
    python parse_logs.py --summary                # Generate summary statistics
    python parse_logs.py --csv output.csv         # Export to CSV
    python parse_logs.py --logs-dir /path/to/logs # Specify logs directory

Supported log patterns:
    - Quest events (current, completed, advanced, transitions)
    - Trigger evaluations (status, completion, results)
    - Navigation events (coordinates, maps, positions, snapping)
    - Stage manager operations (path following, actions)
    - Errors and warnings (path errors, general errors, exceptions)
    - Performance metrics and timing
    - State changes and transitions
    - Game events (battles, items, NPCs, map transitions)
    - Structured JSON debug logs
"""

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter
from typing import Dict, List, Any, Tuple, Optional

class LogEvent:
    """Represents a parsed log event with metadata"""
    
    def __init__(self, timestamp: str, event_type: str, data: Dict[str, Any]):
        self.timestamp = timestamp
        self.event_type = event_type
        self.data = data
    
    def to_csv_row(self) -> str:
        """Convert to CSV format for easy analysis"""
        # Flatten nested data for CSV
        flat_data = {}
        for key, value in self.data.items():
            if isinstance(value, (dict, list)):
                flat_data[key] = json.dumps(value)
            else:
                flat_data[key] = str(value)
        
        # Standard columns
        columns = [
            self.timestamp,
            self.event_type,
            flat_data.get('component', ''),
            flat_data.get('message', ''),
            flat_data.get('quest_id', ''),
            flat_data.get('map_id', ''),
            flat_data.get('coordinates', ''),
            flat_data.get('action', ''),
            flat_data.get('trigger_id', ''),
            flat_data.get('error_type', ''),
            flat_data.get('level', ''),
            flat_data.get('extra_data', '')
        ]
        
        return ','.join(f'"{col}"' for col in columns)

class PokemonLogParser:
    """Comprehensive log parser for Pokemon game analysis"""
    
    def __init__(self):
        self.patterns = self._build_patterns()
        self.events = []
        self.stats = defaultdict(int)
        
    def _build_patterns(self) -> List[Tuple[re.Pattern, str, callable]]:
        """Build comprehensive regex patterns for log parsing"""
        
        patterns = [
            # JSON structured logs (from debug logger) - keep first for efficiency
            (re.compile(r'^(\{.+\})$'), 'STRUCTURED', self._parse_json_log),
            
            # Quest Events - moved before STANDARD
            (re.compile(r'Current quest ID: (\d+)'), 'QUEST_CURRENT', 
             lambda m: {'quest_id': int(m.group(1))}),
            
            (re.compile(r'Quest (\d+) completed'), 'QUEST_COMPLETED',
             lambda m: {'quest_id': int(m.group(1))}),
            
            (re.compile(r'Advanced to quest (\d+)'), 'QUEST_ADVANCED',
             lambda m: {'quest_id': int(m.group(1))}),
            
            (re.compile(r'QuestProgressionEngine: Quest (\d+) completed'), 'QUEST_ENGINE_COMPLETED',
             lambda m: {'quest_id': int(m.group(1))}),
            
            # Quest Triggers - moved before STANDARD
            (re.compile(r'\[TRIGGER\] (\d+_\d+) -> (COMPLETED|PENDING) \| ([^|]*) \| (.*)'), 'TRIGGER_STATUS',
             lambda m: {
                 'trigger_id': m.group(1),
                 'status': m.group(2),
                 'values': m.group(3).strip(),
                 'debug_info': m.group(4).strip()
             }),
            
            (re.compile(r'Trigger (\d+_\d+) completed'), 'TRIGGER_COMPLETED',
             lambda m: {'trigger_id': m.group(1)}),
            
            (re.compile(r'Trigger (\d+_\d+) evaluation result: (True|False)'), 'TRIGGER_EVALUATED',
             lambda m: {'trigger_id': m.group(1), 'result': m.group(2) == 'True'}),
            
            # Navigation Events - moved before STANDARD
            (re.compile(r'current_map_id=(\d+)'), 'MAP_CURRENT',
             lambda m: {'map_id': int(m.group(1))}),
            
            (re.compile(r'global_coords.*=\((\d+),\s*(\d+)\), map_id=(\d+)'), 'PLAYER_POSITION',
             lambda m: {
                 'x': int(m.group(1)),
                 'y': int(m.group(2)),
                 'map_id': int(m.group(3)),
                 'coordinates': f"({m.group(1)},{m.group(2)})"
             }),
            
            (re.compile(r'COORD_MAP_ID: Index (\d+), Coord \((\d+), (\d+)\) -> Map ID (\d+)'), 'NAV_COORDINATE',
             lambda m: {
                 'index': int(m.group(1)),
                 'x': int(m.group(2)),
                 'y': int(m.group(3)),
                 'map_id': int(m.group(4)),
                 'coordinates': f"({m.group(2)},{m.group(3)})"
             }),
            
            (re.compile(r'SNAP_DEBUG\] Nearest coordinate: index (\d+), coord \((\d+), (\d+)\), distance (\d+)'), 'NAV_SNAP',
             lambda m: {
                 'index': int(m.group(1)),
                 'x': int(m.group(2)),
                 'y': int(m.group(3)),
                 'distance': int(m.group(4)),
                 'coordinates': f"({m.group(2)},{m.group(3)})"
             }),
            
            # Stage Manager / Path Following - moved before STANDARD
            (re.compile(r'StageManager: PATH_FOLLOW conversion - current position: \((\d+), (\d+)\) on map (\d+)'), 'STAGE_POSITION',
             lambda m: {
                 'x': int(m.group(1)),
                 'y': int(m.group(2)),
                 'map_id': int(m.group(3)),
                 'coordinates': f"({m.group(1)},{m.group(2)})"
             }),
            
            (re.compile(r'StageManager: PATH_FOLLOW delta: dy=(-?\d+), dx=(-?\d+)'), 'STAGE_DELTA',
             lambda m: {
                 'dy': int(m.group(1)),
                 'dx': int(m.group(2))
             }),
            
            (re.compile(r'StageManager: PATH_FOLLOW converted to (\w+) \(action (\d+)\)'), 'STAGE_ACTION',
             lambda m: {
                 'action_name': m.group(1),
                 'action_id': int(m.group(2))
             }),
            
            # Coordinate Loading - moved before STANDARD
            (re.compile(r'Loading coordinate path for quest (\d+)'), 'COORD_LOADING',
             lambda m: {'quest_id': int(m.group(1))}),
            
            (re.compile(r'Successfully loaded coordinate path for quest (\d+)'), 'COORD_LOADED',
             lambda m: {'quest_id': int(m.group(1))}),
            
            (re.compile(r'Failed to load coordinate path for quest (\d+)'), 'COORD_LOAD_FAILED',
             lambda m: {'quest_id': int(m.group(1))}),
            
            # Navigator State - moved before STANDARD
            (re.compile(r'Navigator state after transition: (\d+) coordinates, index (\d+)'), 'NAV_STATE',
             lambda m: {
                 'coord_count': int(m.group(1)),
                 'current_index': int(m.group(2))
             }),
            
            (re.compile(r'Next target coordinate: \[(\d+), (\d+)\]'), 'NAV_TARGET',
             lambda m: {
                 'target_x': int(m.group(1)),
                 'target_y': int(m.group(2)),
                 'coordinates': f"({m.group(1)},{m.group(2)})"
             }),
            
            # Errors - moved before STANDARD
            (re.compile(r'PATH_FOLLOW: ERROR - (.+)'), 'PATH_ERROR',
             lambda m: {'error_message': m.group(1)}),
            
            (re.compile(r'Error (.+)'), 'GENERAL_ERROR',
             lambda m: {'error_message': m.group(1)}),
            
            (re.compile(r'Exception (.+)'), 'EXCEPTION',
             lambda m: {'exception_message': m.group(1)}),
            
            (re.compile(r'Warning: (.+)'), 'WARNING',
             lambda m: {'warning_message': m.group(1)}),
            
            # Quest Advancement - moved before STANDARD
            (re.compile(r'\[QuestAdvance\] Moving from Quest (\d+) to Quest (\d+)'), 'QUEST_TRANSITION',
             lambda m: {
                 'from_quest': int(m.group(1)),
                 'to_quest': int(m.group(2))
             }),
            
            (re.compile(r'\[QuestAdvance\] Quest (\d+) advancement: (.+)'), 'QUEST_ADVANCEMENT_DEBUG',
             lambda m: {
                 'quest_id': int(m.group(1)),
                 'debug_message': m.group(2)
             }),
            
            # Validation Events - moved before STANDARD
            (re.compile(r'\[VALIDATION\] (.+)'), 'VALIDATION',
             lambda m: {'validation_message': m.group(1)}),
            
            (re.compile(r'Validation failed: (.+)'), 'VALIDATION_FAILED',
             lambda m: {'failure_reason': m.group(1)}),
            
            (re.compile(r'Validation passed: (.+)'), 'VALIDATION_PASSED',
             lambda m: {'success_reason': m.group(1)}),
            
            # Performance Metrics - moved before STANDARD
            (re.compile(r'\[PERF\] (.+) took ([\d.]+)s'), 'PERFORMANCE_TIMING',
             lambda m: {
                 'operation': m.group(1),
                 'duration_seconds': float(m.group(2))
             }),
            
            (re.compile(r'Performance: (.+) \| Metrics: (.+)'), 'PERFORMANCE_METRICS',
             lambda m: {
                 'operation': m.group(1),
                 'metrics_json': m.group(2)
             }),
            
            # State Changes - moved before STANDARD
            (re.compile(r'State change in (.+): (.+) -> (.+)'), 'STATE_CHANGE',
             lambda m: {
                 'component': m.group(1),
                 'old_state': m.group(2),
                 'new_state': m.group(3)
             }),
            
            (re.compile(r'\[STATE\] (.+): (.+) -> (.+)'), 'STATE_CHANGE_ALT',
             lambda m: {
                 'component': m.group(1),
                 'old_state': m.group(2),
                 'new_state': m.group(3)
             }),
            
            # Game Events - moved before STANDARD
            (re.compile(r'Battle started with (.+)'), 'BATTLE_START',
             lambda m: {'opponent': m.group(1)}),
            
            (re.compile(r'Battle ended: (.+)'), 'BATTLE_END',
             lambda m: {'result': m.group(1)}),
            
            (re.compile(r'Item (.+) used'), 'ITEM_USED',
             lambda m: {'item_name': m.group(1)}),
            
            (re.compile(r'Item (.+) obtained'), 'ITEM_OBTAINED',
             lambda m: {'item_name': m.group(1)}),
            
            (re.compile(r'Pokemon (.+) caught'), 'POKEMON_CAUGHT',
             lambda m: {'pokemon_name': m.group(1)}),
            
            (re.compile(r'NPC (.+) talked to'), 'NPC_INTERACTION',
             lambda m: {'npc_name': m.group(1)}),
            
            # Standard log format - MOVED TO END AS FALLBACK
            (re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (\w+)\s* \| ([\w.]+) \| (.+)$'), 'STANDARD', self._parse_standard_log),
            
            # Additional Quest Patterns
            (re.compile(r'Quest (\d+) status: (.+)'), 'QUEST_STATUS',
             lambda m: {
                 'quest_id': int(m.group(1)),
                 'status': m.group(2)
             }),
            
            (re.compile(r'QuestProgressionEngine: (.+)'), 'QUEST_ENGINE_EVENT',
             lambda m: {'engine_message': m.group(1)}),
            
            # Additional Navigation Patterns
            (re.compile(r'Navigator: (.+)'), 'NAVIGATOR_EVENT',
             lambda m: {'navigator_message': m.group(1)}),
            
            (re.compile(r'Path: (.+)'), 'PATH_INFO',
             lambda m: {'path_info': m.group(1)}),
            
            (re.compile(r'Route: (.+)'), 'ROUTE_INFO',
             lambda m: {'route_info': m.group(1)}),
            
            # Environment Events
            (re.compile(r'Environment: (.+)'), 'ENVIRONMENT_EVENT',
             lambda m: {'environment_message': m.group(1)}),
            
            (re.compile(r'Game state: (.+)'), 'GAME_STATE',
             lambda m: {'game_state': m.group(1)}),
            
            # Additional Error Patterns
            (re.compile(r'CRITICAL: (.+)'), 'CRITICAL_ERROR',
             lambda m: {'critical_message': m.group(1)}),
            
            (re.compile(r'FATAL: (.+)'), 'FATAL_ERROR',
             lambda m: {'fatal_message': m.group(1)}),
            
            (re.compile(r'Timeout: (.+)'), 'TIMEOUT_ERROR',
             lambda m: {'timeout_message': m.group(1)}),
            
            (re.compile(r'\[QuestAdvance\] (.+)'), 'QUEST_ADVANCE_DEBUG',
             lambda m: {'debug_message': m.group(1)}),
            
            # Validation
            (re.compile(r'Validation errors detected'), 'VALIDATION_ERROR', lambda m: {}),
            (re.compile(r'All components properly synchronized'), 'VALIDATION_SUCCESS', lambda m: {}),
            
            # Memory/Bag Events
            (re.compile(r'bag.*pokemon.*count.*(\d+)'), 'POKEMON_COUNT',
             lambda m: {'pokemon_count': int(m.group(1))}),
            
            # Map Transitions
            (re.compile(r'map.*transition|changing.*map|entered.*map'), 'MAP_TRANSITION_GENERIC',
             lambda m: {'message': m.group(0)}),
            
            # Item Events
            (re.compile(r'item|inventory|pickup'), 'ITEM_EVENT_GENERIC',
             lambda m: {'message': m.group(0)}),
            
            # NPC Interactions  
            (re.compile(r'npc|talk|interact|dialog'), 'NPC_INTERACTION_GENERIC',
             lambda m: {'message': m.group(0)}),
            
            # Generic debug patterns
            (re.compile(r'\[DEBUG\] (.+)'), 'DEBUG',
             lambda m: {'debug_message': m.group(1)}),
            
            (re.compile(r'\[INFO\] (.+)'), 'INFO',
             lambda m: {'info_message': m.group(1)}),
            
            # Catch-all for any remaining print statements
            (re.compile(r'\[PRINT\] (.+)'), 'PRINT_STATEMENT',
             lambda m: {'message': m.group(1)})
        ]
        
        return patterns
    
    def _parse_json_log(self, match) -> Dict[str, Any]:
        """Parse JSON structured log entry"""
        try:
            data = json.loads(match.group(1))
            return {
                'timestamp': data.get('timestamp', ''),
                'level': data.get('level', ''),
                'component': data.get('component', ''),
                'message': data.get('message', ''),
                'quest_id': data.get('quest_id'),
                'map_id': data.get('map_id'),
                'coordinates': data.get('coordinates'),
                'action': data.get('action'),
                'trigger_id': data.get('trigger_id'),
                'error_type': data.get('error_type')
            }
        except json.JSONDecodeError:
            return {'message': match.group(1)}
    
    def _parse_standard_log(self, match) -> Dict[str, Any]:
        """Parse standard log format"""
        return {
            'timestamp': match.group(1),
            'level': match.group(2),
            'component': match.group(3),
            'message': match.group(4)
        }
    
    def parse_line(self, line: str) -> Optional[LogEvent]:
        """Parse a single log line"""
        line = line.strip()
        if not line:
            return None
        
        # Try each pattern
        for pattern, event_type, parser_func in self.patterns:
            match = pattern.search(line)
            if match:
                try:
                    if event_type in ['STRUCTURED', 'STANDARD']:
                        data = parser_func(match)
                        timestamp = data.get('timestamp', datetime.now().isoformat())
                        # For structured logs, use the component as event type if available
                        if event_type == 'STRUCTURED' and 'component' in data:
                            event_type = f"STRUCTURED_{data['component'].upper()}"
                    else:
                        data = parser_func(match)
                        timestamp = datetime.now().isoformat()  # Use current time as fallback
                        data['original_line'] = line
                    
                    # Add original line for debugging
                    data['raw_line'] = line
                    
                    event = LogEvent(timestamp, event_type, data)
                    self.stats[event_type] += 1
                    return event
                    
                except Exception as e:
                    # Create error event for parsing failures
                    return LogEvent(
                        datetime.now().isoformat(),
                        'PARSE_ERROR',
                        {'error': str(e), 'line': line}
                    )
        
        # If no pattern matches, create unknown event
        return LogEvent(
            datetime.now().isoformat(),
            'UNKNOWN',
            {'message': line}
        )
    
    def parse_file(self, file_path: Path) -> List[LogEvent]:
        """Parse entire log file"""
        events = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    event = self.parse_line(line)
                    if event:
                        event.data['line_number'] = line_num
                        event.data['source_file'] = str(file_path)
                        events.append(event)
        except Exception as e:
            print(f"Error parsing file {file_path}: {e}", file=sys.stderr)
        
        return events
    
    def generate_csv_output(self, events: List[LogEvent]) -> str:
        """Generate CSV output for analysis"""
        if not events:
            return ""
        
        # CSV header
        header = "timestamp,event_type,component,message,quest_id,map_id,coordinates,action,trigger_id,error_type,level,extra_data\n"
        
        # CSV rows
        rows = [event.to_csv_row() for event in events]
        
        return header + '\n'.join(rows)
    
    def generate_summary(self, events: List[LogEvent]) -> str:
        """Generate summary statistics"""
        if not events:
            return "No events found."
        
        summary = []
        summary.append("=== POKEMON GAME LOG ANALYSIS SUMMARY ===\n")
        
        # Basic stats
        summary.append(f"Total Events: {len(events)}")
        summary.append(f"Time Range: {events[0].timestamp} to {events[-1].timestamp}")
        summary.append("")
        
        # Event type distribution
        summary.append("Event Type Distribution:")
        for event_type, count in sorted(self.stats.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / len(events)) * 100
            summary.append(f"  {event_type}: {count} ({percentage:.1f}%)")
        summary.append("")
        
        # Quest analysis
        quest_events = [e for e in events if 'quest_id' in e.data and e.data['quest_id']]
        if quest_events:
            quest_ids = [e.data['quest_id'] for e in quest_events]
            unique_quests = set(quest_ids)
            summary.append(f"Quests Encountered: {sorted(unique_quests)}")
            
            # Quest progression
            completed_quests = [e for e in events if e.event_type in ['QUEST_COMPLETED', 'QUEST_ENGINE_COMPLETED']]
            if completed_quests:
                summary.append("Completed Quests:")
                for event in completed_quests:
                    summary.append(f"  Quest {event.data['quest_id']} at {event.timestamp}")
        summary.append("")
        
        # Error analysis
        error_events = [e for e in events if 'error' in e.event_type.lower() or e.data.get('level') == 'ERROR']
        if error_events:
            summary.append(f"Errors Found: {len(error_events)}")
            error_types = Counter([e.event_type for e in error_events])
            for error_type, count in error_types.most_common(5):
                summary.append(f"  {error_type}: {count}")
        summary.append("")
        
        # Navigation analysis
        nav_events = [e for e in events if 'NAV_' in e.event_type or 'STAGE_' in e.event_type]
        if nav_events:
            summary.append(f"Navigation Events: {len(nav_events)}")
            
            # Map transitions
            map_events = [e for e in events if 'map_id' in e.data and e.data['map_id']]
            if map_events:
                maps_visited = set([e.data['map_id'] for e in map_events])
                summary.append(f"Maps Visited: {sorted(maps_visited)}")
        summary.append("")
        
        # Performance analysis
        perf_events = [e for e in events if e.event_type == 'PERFORMANCE']
        if perf_events:
            durations = [e.data['duration'] for e in perf_events]
            avg_duration = sum(durations) / len(durations)
            max_duration = max(durations)
            summary.append(f"Performance Events: {len(perf_events)}")
            summary.append(f"Average Duration: {avg_duration:.3f}s")
            summary.append(f"Max Duration: {max_duration:.3f}s")
        
        return '\n'.join(summary)

def main():
    parser = argparse.ArgumentParser(description='Parse Pokemon game logs for analysis')
    parser.add_argument('logfile', nargs='?', help='Log file to parse (default: stdin)')
    parser.add_argument('--analyze', action='store_true', help='Analyze all logs in logs directory')
    parser.add_argument('--summary', action='store_true', help='Generate summary statistics')
    parser.add_argument('--csv', action='store_true', help='Output in CSV format')
    parser.add_argument('--logs-dir', default='/puffertank/grok_plays_pokemon/logs', 
                       help='Directory containing log files')
    parser.add_argument('--output', help='Output file (default: stdout)')
    
    args = parser.parse_args()
    
    log_parser = PokemonLogParser()
    
    if args.analyze:
        # Analyze all log files in directory
        logs_dir = Path(args.logs_dir)
        if not logs_dir.exists():
            print(f"Logs directory {logs_dir} does not exist", file=sys.stderr)
            return 1
        
        all_events = []
        for log_file in logs_dir.glob('*.log'):
            print(f"Parsing {log_file}...", file=sys.stderr)
            events = log_parser.parse_file(log_file)
            all_events.extend(events)
        
        # Sort by timestamp
        all_events.sort(key=lambda e: e.timestamp)
        events = all_events
        
    elif args.logfile:
        # Parse specific file
        log_file = Path(args.logfile)
        if not log_file.exists():
            print(f"Log file {log_file} does not exist", file=sys.stderr)
            return 1
        events = log_parser.parse_file(log_file)
    else:
        # Parse from stdin
        events = []
        for line in sys.stdin:
            event = log_parser.parse_line(line)
            if event:
                events.append(event)
    
    # Generate output
    if args.summary:
        output = log_parser.generate_summary(events)
    elif args.csv:
        output = log_parser.generate_csv_output(events)
    else:
        # Default: simple parsed output
        output_lines = []
        for event in events:
            if event.event_type == 'STRUCTURED':
                # For structured events, output the important fields
                parts = []
                parts.append(event.timestamp)
                parts.append(event.event_type)
                if 'quest_id' in event.data and event.data['quest_id']:
                    parts.append(f"QUEST_ID={event.data['quest_id']}")
                if 'map_id' in event.data and event.data['map_id']:
                    parts.append(f"MAP_ID={event.data['map_id']}")
                if 'coordinates' in event.data and event.data['coordinates']:
                    parts.append(f"COORDS={event.data['coordinates']}")
                if 'action' in event.data and event.data['action']:
                    parts.append(f"ACTION={event.data['action']}")
                if 'trigger_id' in event.data and event.data['trigger_id']:
                    parts.append(f"TRIGGER={event.data['trigger_id']}")
                if 'message' in event.data:
                    parts.append(f"MSG={event.data['message']}")
                output_lines.append(','.join(parts))
            else:
                # For other events, use the original format but enhanced
                data_parts = []
                for key, value in event.data.items():
                    if key not in ['raw_line', 'original_line', 'line_number', 'source_file'] and value:
                        data_parts.append(f"{key}={value}")
                output_lines.append(f"{event.event_type},{','.join(data_parts)}")
        
        output = '\n'.join(output_lines)
    
    # Write output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
    else:
        print(output)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
