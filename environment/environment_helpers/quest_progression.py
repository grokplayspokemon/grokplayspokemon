from typing import List, Dict, Optional, Any
import json
from pathlib import Path
from multiprocessing import SimpleQueue
import time

# Add logging integration
import sys
import os
sys.path.append('/puffertank/grok_plays_pokemon')
from utils.logging_config import get_pokemon_logger

class QuestProgressionEngine:
    def __init__(self, env, navigator, quest_manager, 
                 quests_definitions: List[Dict], # Renamed from quests
                 quest_ids_all: List[int], 
                 status_queue: SimpleQueue, 
                 run_dir: Path,
                 initial_quest_statuses: Dict[str, bool],  # New parameter
                 initial_trigger_statuses: Dict[str, bool], # New parameter
                 logger=None):
        
        if logger is None:
            self.logger = get_pokemon_logger()
        else:
            self.logger = logger
        
        self.env = env
        self.navigator = navigator
        self.quest_manager = quest_manager
        self.quests_definitions = quests_definitions # Store the static definitions
        self.quest_ids_all = quest_ids_all # This seems to be just a list of integer IDs
        self.status_queue = status_queue
        self.run_dir = run_dir

        # Initialize from the passed-in loaded statuses
        self.quest_completed = set()
        if initial_quest_statuses:
            for qid_str, is_completed in initial_quest_statuses.items():
                try:
                    if is_completed:  # Only add if actually completed
                        self.quest_completed.add(int(qid_str))
                except ValueError:
                    self.logger.log_error("QuestProgressionEngine", f"Could not convert quest_id to int from initial_quest_statuses: {qid_str}")
        
        self.trigger_completed = set()
        if initial_trigger_statuses:
            # Safety check: handle both list and dictionary formats
            if isinstance(initial_trigger_statuses, dict):
                for trigger_id, is_completed in initial_trigger_statuses.items():
                    if is_completed:
                        self.trigger_completed.add(trigger_id)
            elif isinstance(initial_trigger_statuses, list):
                # Handle old list format (all triggers in list are considered completed)
                self.logger.log_error("QuestProgressionEngine", "Received list format for initial_trigger_statuses - converting to dictionary format")
                for trigger_id in initial_trigger_statuses:
                    self.trigger_completed.add(trigger_id)
            else:
                self.logger.log_error("QuestProgressionEngine", f"Unexpected format for initial_trigger_statuses: {type(initial_trigger_statuses)}")
                # Continue with empty set as fallback

        self.active_triggers_for_quest = {}

        self.logger.log_quest_event("INIT", "QuestProgressionEngine initializing based on loaded statuses from JSON")
        
        # Populate active_triggers_for_quest and ensure all quests are in self.quest_completed
        # This loop iterates over the static definitions to structure the engine's understanding of quests.
        for quest_def_data in self.quests_definitions: 
            quest_id_str = quest_def_data.get('quest_id')
            if not quest_id_str:
                self.logger.log_error("QuestProgressionEngine", f"Quest definition missing 'quest_id'", {'quest_def_data': quest_def_data})
                continue
            
            try:
                quest_id_int = int(quest_id_str)
            except ValueError:
                self.logger.log_error("QuestProgressionEngine", f"Could not convert quest_id_str to int in definitions loop: {quest_id_str}")
                continue

            # Ensure quest_id_int is in self.quest_completed, defaulting to False if not in initial_quest_statuses
            current_completion_status = quest_id_int in self.quest_completed
            # FIXED: Only keep in completed set if actually completed
            if not current_completion_status:
                # If quest is not completed, make sure it's not in the completed set
                self.quest_completed.discard(quest_id_int)
            
            self.logger.log_quest_event(quest_id_str, f"Initial loaded completion status", {'completion_status': current_completion_status})

            self.active_triggers_for_quest[quest_id_int] = []
            triggers_def = quest_def_data.get('event_triggers', [])
            for idx, trigger_def_data in enumerate(triggers_def):
                trigger_id_str = f"{quest_id_str}_{idx}"
                
                trigger_completion_status = trigger_id_str in self.trigger_completed
                # FIXED: Only keep in completed set if actually completed
                if not trigger_completion_status:
                    # If trigger is not completed, make sure it's not in the completed set
                    self.trigger_completed.discard(trigger_id_str)

                self.logger.log_quest_event(quest_id_str, f"Initialized trigger {trigger_id_str}", {
                    'trigger_id': trigger_id_str,
                    'completion_status': trigger_completion_status,
                    'trigger_type': trigger_def_data.get('type', 'unknown')
                })
                
                if not trigger_completion_status:
                    if quest_id_int not in self.active_triggers_for_quest:
                         self.active_triggers_for_quest[quest_id_int] = [] # Ensure key exists
                    self.active_triggers_for_quest[quest_id_int].append(trigger_id_str)
        
        self.save_status_to_json()

        # State tracking for change detection
        self.last_trigger_states = {}  # trigger_id -> last_status
        self.last_quest_states = {}    # quest_id -> last_status
        
        # Load persisted progress if available
        self._load_existing_progress()
        
        # Initialize quest monitor for Dear PyGui alerts
        try:
            from environment.environment_helpers.quest_monitor import QuestMonitor
            self.quest_monitor = QuestMonitor(self, self.logger)
            self.quest_monitor.start_monitoring()
            self.logger.log_system_event("Quest monitor initialized and started", {
                'component': 'quest_progression'
            })
        except Exception as e:
            self.logger.log_error("QuestProgressionEngine", f"Failed to initialize quest monitor: {str(e)}")
            self.quest_monitor = None
        
        self.logger.log_system_event("QuestProgressionEngine initialized", {
            'component': 'quest_progression',
            'trigger_count': len(self.trigger_completed),
            'quest_count': len(self.quest_completed),
            'monitor_active': self.quest_monitor is not None
        })

    def _log_trigger_state_change(self, trigger_id: str, new_status: bool, values: str = "", debug_info: str = ""):
        """Log trigger state changes only when status actually changes"""
        old_status = self.last_trigger_states.get(trigger_id, None)
        
        if old_status != new_status:
            self.last_trigger_states[trigger_id] = new_status
            status_str = "COMPLETED" if new_status else "INCOMPLETE"
            self.logger.log_trigger_event(trigger_id, status_str, values, debug_info)

    def _log_quest_state_change(self, quest_id: int, new_status: bool, data: Optional[Dict[str, Any]] = None):
        """Log quest state changes only when status actually changes"""
        old_status = self.last_quest_states.get(quest_id, None)
        
        if old_status != new_status:
            self.last_quest_states[quest_id] = new_status
            status_str = "completed" if new_status else "incomplete"
            self.logger.log_quest_event(str(quest_id), f"Quest {quest_id} status changed to {status_str}", data)

    def get_quest_data_by_id(self, quest_id_to_find: int) -> Optional[Dict]:
        self.logger.log_quest_event(str(quest_id_to_find), "get_quest_data_by_id called")
        
        # Validate input parameter
        if quest_id_to_find is None:
            self.logger.log_error("QuestProgressionEngine", "quest_id_to_find is None")
            return None
            
        # Ensure we have quest definitions loaded
        if not hasattr(self, 'quests_definitions') or not self.quests_definitions:
            self.logger.log_error("QuestProgressionEngine", "No quest definitions loaded")
            return None
        
        # Search for quest definition
        for quest_data in self.quests_definitions:
            try:
                quest_id_from_data = quest_data.get('quest_id')
                if quest_id_from_data is None:
                    self.logger.log_error("QuestProgressionEngine", f"Quest data missing quest_id field: {quest_data}")
                    continue
                    
                # Handle both string and int quest_id formats
                if isinstance(quest_id_from_data, str):
                    quest_id_int = int(quest_id_from_data)
                else:
                    quest_id_int = int(quest_id_from_data)
                    
                if quest_id_int == quest_id_to_find:
                    self.logger.log_quest_event(str(quest_id_to_find), "Quest data found", {'quest_data_keys': list(quest_data.keys())})
                    return quest_data
                    
            except (ValueError, TypeError) as e:
                self.logger.log_error("QuestProgressionEngine", f"Error parsing quest_id from quest data: {quest_data.get('quest_id', 'MISSING')}, error: {e}")
                continue
        
        # Quest not found - this is now a detailed error
        self.logger.log_error("QuestProgressionEngine", f"Quest data not found for quest_id: {quest_id_to_find}", {
            'searched_quest_id': quest_id_to_find,
            'available_quest_ids': [q.get('quest_id', 'MISSING') for q in self.quests_definitions],
            'total_quests_loaded': len(self.quests_definitions)
        })
        return None

    def _load_existing_progress(self):
        """Load existing quest and trigger progress from files"""
        self.logger.log_system_event("SYSTEM", "_load_existing_progress called")
        
        try:
            # Load quest status
            quest_status_file = self.run_dir / 'quest_status.json'
            if quest_status_file.exists():
                with open(quest_status_file, 'r') as f:
                    loaded_quest_status = json.load(f)
                    # Reset quest_completed and rebuild from loaded status
                    self.quest_completed = set()
                    for key, value in loaded_quest_status.items():
                        quest_id = int(key)
                        if value:  # Only add if actually completed
                            self.quest_completed.add(quest_id)
                
                self.logger.log_system_event(f"Loaded quest status for {len(loaded_quest_status)} quests",
                                           {'loaded_quest_count': len(loaded_quest_status)})
            
            # Load trigger status
            trigger_status_file = self.run_dir / 'trigger_status.json'
            if trigger_status_file.exists():
                with open(trigger_status_file, 'r') as f:
                    loaded_trigger_data = json.load(f)
                
                # Handle both old list format and new dictionary format
                if isinstance(loaded_trigger_data, list):
                    # Old format: list of trigger IDs (all completed)
                    self.trigger_completed = set(loaded_trigger_data)
                    self.logger.log_system_event("Loaded trigger status (old list format)", {
                        'loaded_trigger_count': len(loaded_trigger_data)
                    })
                elif isinstance(loaded_trigger_data, dict):
                    # New format: dictionary mapping trigger_id to completion status
                    self.trigger_completed = set()
                    for trigger_id, is_completed in loaded_trigger_data.items():
                        if is_completed:
                            self.trigger_completed.add(trigger_id)
                    self.logger.log_system_event("Loaded trigger status (new dictionary format)", {
                        'loaded_trigger_count': len(self.trigger_completed)
                    })
                else:
                    self.logger.log_error("SYSTEM", f"Unexpected trigger status format: {type(loaded_trigger_data)}")
                    self.trigger_completed = set()  # Fallback to empty set
                
        except Exception as e:
            self.logger.log_error("SYSTEM", f"Error loading existing progress: {str(e)}")

    def step(self, evaluator):
        """FIXED: Enhanced quest progression with better error handling"""
        self.logger.log_quest_event("SYSTEM", "QuestProgressionEngine.step() called")
        print(f"[QuestProgressionEngine] step() called")
        
        try:
            # 1) evaluate triggers for current quest
            current_qid = getattr(self.quest_manager, 'current_quest_id', None)
            self.logger.log_quest_event(str(current_qid) if current_qid else "NONE", "Current quest ID retrieved")
            
            if current_qid is not None:
                active_quest_def = next((qq for qq in self.quests_definitions if int(qq['quest_id']) == current_qid), None) # Use renamed attribute
                
                if active_quest_def:
                    self.logger.log_quest_event(str(current_qid), "Processing active quest", {'quest_def_found': True})
                    
                    # process event_triggers
                    event_triggers = active_quest_def.get('event_triggers', [])
                    self.logger.log_quest_event(str(current_qid), f"About to process {len(event_triggers)} event triggers", {'trigger_count': len(event_triggers)})
                    
                    for idx, trg_def in enumerate(event_triggers):
                        tid = f"{active_quest_def['quest_id']}_{idx}"
                        
                        print(f"[QuestProgressionEngine] Checking trigger {tid}: {trg_def.get('type', 'unknown')}")
                        
                        # FIXED: Check if trigger is already completed (don't re-evaluate completed triggers)
                        if tid in self.trigger_completed:
                            # Only log on first detection of completed status
                            self._log_trigger_state_change(tid, True)
                            print(f"[QuestProgressionEngine] Trigger {tid} already completed, skipping")
                            continue
                            
                        # FIXED: For sequence-based quests, we don't require all previous triggers to remain true
                        # We only require that they have been completed at some point
                        # This allows for map transition sequences like 38->37->0
                        
                        # Check if all previous triggers have been completed (not necessarily still true)
                        previous_complete = all(
                            tid in self.trigger_completed for i in range(idx)
                        )
                        
                        # Only evaluate this trigger if it's the first one or all previous are complete
                        if idx == 0 or previous_complete:
                            
                            try:
                                eval_dict = evaluator.check_trigger(trg_def) # Get detailed dict
                                result = eval_dict["result"]
                                values_str = eval_dict["values_str"]
                                debug_str = eval_dict["debug_str"]

                                # Remove excessive logging - state change detection handles this
                                
                                # Send detailed trigger debug info to UI
                                # Extend to quests 001-020 for comprehensive debugging
                                if active_quest_def['quest_id'] in ['001', '002', '003', '004', '005', '006', '007', '008', '009', '010', 
                                                                     '011', '012', '013', '014', '015', '016', '017', '018', '019', '020']:
                                    self.status_queue.put(('__trigger_debug__', {
                                        'id': tid,
                                        'status': result,
                                        'values_str': values_str,
                                        'debug_str': debug_str
                                    }))
                                else: # For other quests, send simple status for now
                                    if result: self.status_queue.put((tid, True))
                                    # No need to send False for simple updates, UI assumes pending

                                # Use state change detection for trigger completion
                                self._log_trigger_state_change(tid, result, values_str, debug_str)
                                
                                # Update quest monitor with trigger state
                                if self.quest_monitor:
                                    self.quest_monitor.update_trigger_state(tid, result)
                                
                                if result:
                                    self.trigger_completed.add(tid)
                                    
                            except Exception as e:
                                self.logger.log_error("SYSTEM", f"Error checking trigger {tid}: {str(e)}")
                                
                                # Alert about trigger evaluation errors
                                if self.quest_monitor:
                                    self.quest_monitor.report_trigger_error(tid, str(e))
                        else:
                            self.logger.log_quest_event(str(current_qid), f"Skipping trigger {tid} - previous triggers not all complete", {
                                'trigger_id': tid,
                                'reason': 'previous_triggers_incomplete',
                                'previous_complete': previous_complete
                            })
                    
                    # mark quest complete if all triggers done
                    tids = [f"{active_quest_def['quest_id']}_{i}" for i in range(len(active_quest_def.get('event_triggers', [])))]
                    all_triggers_completed_for_quest = all(tid in self.trigger_completed for tid in tids)
                    
                    # Remove excessive quest completion check logging - state change detection handles completions
                    
                    # Prepare detailed quest status for UI (for quests 001-020)
                    if active_quest_def['quest_id'] in ['001', '002', '003', '004', '005', '006', '007', '008', '009', '010',
                                                         '011', '012', '013', '014', '015', '016', '017', '018', '019', '020']:
                        num_triggers = len(tids)
                        completed_triggers_count = sum(1 for t_id in tids if t_id in self.trigger_completed)
                        quest_values_str = f"Triggers: {completed_triggers_count}/{num_triggers}"
                        quest_debug_str = f"Checking completion of {num_triggers} triggers for Quest {active_quest_def['quest_id']}."
                        self.status_queue.put(('__quest_status_detailed__', {
                            'id': active_quest_def['quest_id'],
                            'status': all_triggers_completed_for_quest,
                            'values_str': quest_values_str,
                            'debug_str': quest_debug_str
                        }))

                    if tids and all_triggers_completed_for_quest:
                        qint = int(active_quest_def['quest_id'])
                        if qint not in self.quest_completed:
                            self.quest_completed.add(qint)
                            # Use state change detection for quest completion
                            self._log_quest_state_change(qint, True, {'all_triggers_completed': True})
                            
                            # Update quest monitor with quest completion
                            if self.quest_monitor:
                                self.quest_monitor.update_quest_progress(str(qint).zfill(3))
                            
                            # Send simple completion for all quests if not already sent by detailed one
                            if active_quest_def['quest_id'] not in ['001', '002', '003', '004']:
                                self.status_queue.put((active_quest_def['quest_id'], True))
                else:
                    self.logger.log_error("SYSTEM", f"Active quest definition not found: {current_qid}")
            
            # 2) advance to next quest if needed
            current_qid = getattr(self.quest_manager, 'current_quest_id', None)
            if current_qid is not None and current_qid not in self.quest_completed:
                # find next
                next_qid = None
                for qid in self.quest_ids_all:
                    if qid not in self.quest_completed:
                        next_qid = qid
                        break
                
                if next_qid and next_qid != current_qid:
                    self.logger.log_quest_event(str(current_qid), f"Quest advancement: Moving from Quest {current_qid} to Quest {next_qid}", {
                        'from_quest': current_qid, 
                        'to_quest': next_qid,
                        'action': 'quest_advancement'
                    })
                    
                    # CRITICAL FIX: Load new quest coordinates FIRST before setting quest IDs
                    # This prevents the race condition where StageManager tries to use new quest ID
                    # with old quest coordinates
                    if hasattr(self.navigator, 'load_coordinate_path'):
                        self.logger.log_navigation_event("COORDINATE_LOADING_START", {
                            'message': f'Loading coordinate path for quest {next_qid} BEFORE setting quest IDs',
                            'quest_id': next_qid
                        })
                        
                        # ENHANCEMENT: Add retry mechanism for coordinate loading
                        load_success = False
                        max_retries = 3
                        
                        for attempt in range(max_retries):
                            try:
                                if self.navigator.load_coordinate_path(next_qid):
                                    self.logger.log_navigation_event("COORDINATE_LOADING_SUCCESS", {
                                        'message': f'Successfully loaded coordinate path for quest {next_qid}',
                                        'quest_id': next_qid,
                                        'attempt': attempt + 1
                                    })
                                    load_success = True
                                    break
                                else:
                                    self.logger.log_error("CoordinateLoadingError", f"Failed to load coordinate path for quest {next_qid}", {
                                        'quest_id': next_qid, 
                                        'attempt': attempt + 1
                                    })
                            except Exception as e:
                                self.logger.log_error("CoordinateLoadingException", f"Exception during coordinate loading", {
                                    'quest_id': next_qid,
                                    'attempt': attempt + 1,
                                    'error_message': str(e)
                                })
                            
                            if attempt < max_retries - 1:
                                self.logger.log_system_event("Retrying coordinate loading in 0.5 seconds",
                                                            {'retry_delay': 0.5})
                                time.sleep(0.5)
                        
                        if load_success:
                            # CRITICAL FIX: Snap to nearest coordinate after loading new quest path
                            # This ensures Navigator index is properly reset and aligned with new quest
                            if hasattr(self.navigator, 'snap_to_nearest_coordinate'):
                                self.logger.log_navigation_event("COORDINATE_SNAPPING", {
                                    'message': f'Snapping to nearest coordinate for quest {next_qid}',
                                    'quest_id': next_qid
                                })
                                snap_success = False
                                
                                # Try regular snap first
                                if self.navigator.snap_to_nearest_coordinate():
                                    self.logger.log_navigation_event("COORDINATE_SNAP_SUCCESS", {
                                        'message': 'Successfully snapped to nearest coordinate',
                                        'quest_id': next_qid
                                    })
                                    snap_success = True
                                # If regular snap fails, try emergency snap
                                elif hasattr(self.navigator, '_emergency_snap_to_path') and self.navigator._emergency_snap_to_path():
                                    self.logger.log_navigation_event("EMERGENCY_SNAP_SUCCESS", {
                                        'message': 'Emergency snap successful',
                                        'quest_id': next_qid
                                    })
                                    snap_success = True
                                else:
                                    self.logger.log_error("SnapFailedError", "All snapping attempts failed - using default index 0", {
                                        'quest_id': next_qid, 
                                        'fallback': 'index_0'
                                    })
                                    # Fallback: manually set index to 0 as safety measure
                                    self.navigator.current_coordinate_index = 0
                                    snap_success = True  # Continue with quest transition
                                
                                if snap_success and hasattr(self.navigator, 'get_current_status'):
                                    status = self.navigator.get_current_status()
                                    self.logger.log_navigation_event("NAVIGATOR_STATUS_CHECK", {
                                        'message': 'Navigator status after snapping',
                                        'quest_id': next_qid,
                                        'navigator_status': status
                                    })
                        else:
                            self.logger.log_error("CoordinateLoadingFailedError", f"Failed to load coordinate path for quest {next_qid} after {max_retries} attempts", {
                                'quest_id': next_qid,
                                'max_retries': max_retries,
                                'fallback_action': 'preserve_current_quest'
                            })
                            # Enhanced fallback: Try to preserve current quest instead of advancing
                            self.logger.log_quest_event(str(current_qid), f"Preserving current quest {current_qid} due to coordinate loading failure", {
                                'action': 'quest_preservation'
                            })
                            return  # Don't proceed with quest transition
                    
                    # FIXED: Ensure quest ID is correctly set across all components
                    # Only set these AFTER coordinates are successfully loaded
                    self.quest_manager.current_quest_id = next_qid
                    if hasattr(self.navigator, 'active_quest_id'):
                        self.navigator.active_quest_id = next_qid
                    if hasattr(self.env, 'current_loaded_quest_id'):
                        self.env.current_loaded_quest_id = next_qid
                    
                    self.logger.log_quest_event(str(next_qid), f"Quest IDs updated across all components", {
                        'action': 'component_quest_id_update',
                        'quest_manager_id': next_qid,
                        'navigator_id': getattr(self.navigator, 'active_quest_id', None),
                        'env_id': getattr(self.env, 'current_loaded_quest_id', None)
                    })
                    
                    # Notify UI of quest change
                    self.status_queue.put(('__current_quest__', next_qid))
                    self.logger.log_quest_event(str(next_qid), f"Advanced to quest {next_qid}", {
                        'action': 'quest_advancement_complete'
                    })
                    
                    # DIAGNOSTIC: Verify Navigator state after quest transition
                    if hasattr(self.navigator, 'sequential_coordinates') and hasattr(self.navigator, 'current_coordinate_index'):
                        coords_count = len(self.navigator.sequential_coordinates)
                        current_index = self.navigator.current_coordinate_index
                        
                        self.logger.log_navigation_event("NAVIGATOR_STATE_DIAGNOSTIC", {
                            'message': 'Navigator state after transition',
                            'quest_id': next_qid,
                            'coords_count': coords_count,
                            'current_index': current_index
                        })
                        
                        if coords_count > 0 and current_index < coords_count:
                            next_coord = self.navigator.sequential_coordinates[current_index]
                            self.logger.log_navigation_event("NEXT_TARGET_COORDINATE", {
                                'message': f'Next target coordinate: {next_coord}',
                                'quest_id': next_qid,
                                'coordinates': next_coord
                            })
                        else:
                            self.logger.log_error("NavigatorIndexOutOfRange", f"Index {current_index} out of range for {coords_count} coordinates", {
                                'current_index': current_index,
                                'coords_count': coords_count,
                                'quest_id': next_qid
                            })

                    # ENHANCEMENT: Add comprehensive state validation
                    self.logger.log_system_event("Post-transition validation starting", {
                        'function': 'post_transition_validation'
                    })
                    validation_errors = []

                    # Validate Navigator state
                    if hasattr(self.navigator, 'active_quest_id') and self.navigator.active_quest_id != next_qid:
                        validation_errors.append(f"Navigator quest ID mismatch: {self.navigator.active_quest_id} != {next_qid}")

                    if hasattr(self.navigator, 'sequential_coordinates') and not self.navigator.sequential_coordinates:
                        validation_errors.append("Navigator has no coordinates loaded")

                    if hasattr(self.navigator, 'current_coordinate_index') and hasattr(self.navigator, 'sequential_coordinates'):
                        if self.navigator.current_coordinate_index >= len(self.navigator.sequential_coordinates):
                            validation_errors.append(f"Navigator index {self.navigator.current_coordinate_index} >= {len(self.navigator.sequential_coordinates)} coordinates")

                    # Validate Environment state
                    if hasattr(self.env, 'current_loaded_quest_id') and self.env.current_loaded_quest_id != next_qid:
                        validation_errors.append(f"Environment quest ID mismatch: {self.env.current_loaded_quest_id} != {next_qid}")

                    # Validate QuestManager state
                    if hasattr(self.quest_manager, 'current_quest_id') and self.quest_manager.current_quest_id != next_qid:
                        validation_errors.append(f"QuestManager quest ID mismatch: {self.quest_manager.current_quest_id} != {next_qid}")

                    if validation_errors:
                        self.logger.log_error("SYSTEM", f"Validation errors detected: {validation_errors}")
                        
                        # Attempt to fix critical errors
                        if hasattr(self.navigator, 'active_quest_id'):
                            self.navigator.active_quest_id = next_qid
                            self.logger.log_system_event(f"Fixed Navigator quest ID to {next_qid}", {
                                'fixed_component': 'navigator'
                            })
                        
                        if hasattr(self.env, 'current_loaded_quest_id'):
                            self.env.current_loaded_quest_id = next_qid
                            self.logger.log_system_event(f"Fixed Environment quest ID to {next_qid}", {
                                'fixed_component': 'environment'
                            })
                    else:
                        self.logger.log_system_event(f"All components properly synchronized for quest {next_qid}", {
                            'validation_result': 'success', 
                            'quest_id': next_qid
                        })
            
            # 3) persist statuses with better error handling
            self._persist_progress()
            
        except Exception as e:
            self.logger.log_error("SYSTEM", f"Critical error in step(): {str(e)}")

    def _persist_progress(self):
        """Persist quest and trigger progress to files"""
        self.logger.log_system_event("SYSTEM", "_persist_progress called")
        
        try:
            # Save trigger status - FIX: Save as dictionary mapping trigger_id to completion status
            trigger_file = self.run_dir / 'trigger_status.json'
            trigger_status_dict = {tid: True for tid in self.trigger_completed}
            with open(trigger_file, 'w') as f:
                json.dump(trigger_status_dict, f, indent=4)
            
            # Save quest status
            quest_file = self.run_dir / 'quest_status.json'
            quest_status_for_save = {str(qid).zfill(3): qid in self.quest_completed for qid in self.quest_completed}
            with open(quest_file, 'w') as f:
                json.dump(quest_status_for_save, f, indent=4)
            
            self.logger.log_system_event("Progress persisted successfully", {
                'trigger_count': len(self.trigger_completed),
                'quest_count': len(self.quest_completed)
            })
                
        except Exception as e:
            self.logger.log_error("SYSTEM", f"Error persisting progress: {str(e)}")

    def get_quest_status(self) -> Dict[int, bool]:
        """Get current quest completion status"""
        self.logger.log_system_event("SYSTEM", "get_quest_status called")
        return {qid: qid in self.quest_completed for qid in self.quest_ids_all}

    def get_trigger_status(self) -> Dict[str, bool]:
        """Get current trigger completion status"""
        self.logger.log_system_event("SYSTEM", "get_trigger_status called")
        return {tid: tid in self.trigger_completed for tid in self.trigger_completed}

    def force_complete_quest(self, quest_id: int):
        """Manually mark a quest as completed"""
        self.logger.log_quest_event(str(quest_id), f"Manually completing quest {quest_id}", {
            'action': 'manual_completion'
        })
        
        if quest_id in self.quest_completed:
            self.quest_completed.remove(quest_id)
            self.quest_completed.add(quest_id)
            self.status_queue.put((str(quest_id).zfill(3), True))
            self._persist_progress()
            
            self.logger.log_quest_event(str(quest_id), f"Quest {quest_id} manually completed", {
                'action': 'manual_completion_success'
            })

    def reset_quest(self, quest_id: int):
        """Reset a quest to incomplete status"""
        self.logger.log_quest_event(str(quest_id), f"Resetting quest {quest_id}", {
            'action': 'quest_reset'
        })
        
        if quest_id in self.quest_completed:
            self.quest_completed.remove(quest_id)
            self.status_queue.put((str(quest_id).zfill(3), False))
            self._persist_progress()
            
            self.logger.log_quest_event(str(quest_id), f"Quest {quest_id} reset", {
                'action': 'quest_reset_success'
            })

    def save_status_to_json(self):
        """Save current quest and trigger status to JSON files"""
        self.logger.log_system_event("SYSTEM", "save_status_to_json called")
        self._persist_progress()
    
    def shutdown(self):
        """Shutdown the quest progression engine and all monitoring components"""
        self.logger.log_system_event("QuestProgressionEngine shutting down", {
            'component': 'quest_progression'
        })
        
        # Shutdown quest monitor
        if hasattr(self, 'quest_monitor') and self.quest_monitor:
            try:
                self.quest_monitor.stop_monitoring()
                self.logger.log_system_event("Quest monitor stopped", {
                    'component': 'quest_monitor'
                })
            except Exception as e:
                self.logger.log_error("SYSTEM", f"Error stopping quest monitor: {str(e)}")
        
        # Final persist of progress
        try:
            self._persist_progress()
        except Exception as e:
            self.logger.log_error("SYSTEM", f"Error persisting progress during shutdown: {str(e)}") 