from typing import List, Dict, Optional
import json
from pathlib import Path
from multiprocessing import SimpleQueue

class QuestProgressionEngine:
    def __init__(self, env, navigator, quest_manager, 
                 quests_definitions: List[Dict], # Renamed from quests
                 quest_ids_all: List[int], 
                 status_queue: SimpleQueue, 
                 run_dir: Path,
                 initial_quest_statuses: Dict[str, bool],  # New parameter
                 initial_trigger_statuses: Dict[str, bool]): # New parameter
        
        self.env = env
        self.navigator = navigator
        self.quest_manager = quest_manager
        self.quests_definitions = quests_definitions # Store the static definitions
        self.quest_ids_all = quest_ids_all # This seems to be just a list of integer IDs
        self.status_queue = status_queue
        self.run_dir = run_dir

        # Initialize from the passed-in loaded statuses
        self.quest_completed = {} # Stores int quest_id -> bool
        if initial_quest_statuses:
            for qid_str, is_completed in initial_quest_statuses.items():
                try:
                    self.quest_completed[int(qid_str)] = is_completed
                except ValueError:
                    print(f"[QuestProgressionEngine] Warning: Could not convert quest_id '{qid_str}' to int from initial_quest_statuses.")
        
        self.trigger_completed = initial_trigger_statuses.copy() if initial_trigger_statuses else {} # Stores str trigger_id -> bool

        self.active_triggers_for_quest = {}

        print("[QuestProgressionEngine] Initializing based on loaded statuses from JSON.")
        # Populate active_triggers_for_quest and ensure all quests are in self.quest_completed
        # This loop iterates over the static definitions to structure the engine's understanding of quests.
        for quest_def_data in self.quests_definitions: 
            quest_id_str = quest_def_data.get('quest_id')
            if not quest_id_str:
                print(f"[QuestProgressionEngine] Warning: Quest definition missing 'quest_id': {quest_def_data}")
                continue
            
            try:
                quest_id_int = int(quest_id_str)
            except ValueError:
                print(f"[QuestProgressionEngine] Warning: Could not convert quest_id_str '{quest_id_str}' to int in definitions loop.")
                continue

            # Ensure quest_id_int is in self.quest_completed, defaulting to False if not in initial_quest_statuses
            current_completion_status = self.quest_completed.get(quest_id_int, False)
            self.quest_completed[quest_id_int] = current_completion_status 
            
            print(f"[QuestProgressionEngine] Quest {quest_id_str}: Initial loaded completion: {current_completion_status}")

            self.active_triggers_for_quest[quest_id_int] = []
            triggers_def = quest_def_data.get('event_triggers', [])
            for idx, trigger_def_data in enumerate(triggers_def):
                trigger_id_str = f"{quest_id_str}_{idx}"
                
                trigger_completion_status = self.trigger_completed.get(trigger_id_str, False)
                self.trigger_completed[trigger_id_str] = trigger_completion_status

                print(f"[QuestProgressionEngine] Initialized trigger {trigger_id_str} to {'completed' if trigger_completion_status else 'not completed'}")
                if not trigger_completion_status:
                    if quest_id_int not in self.active_triggers_for_quest:
                         self.active_triggers_for_quest[quest_id_int] = [] # Ensure key exists
                    self.active_triggers_for_quest[quest_id_int].append(trigger_id_str)
        
        self.save_status_to_json()

    def get_quest_data_by_id(self, quest_id_to_find: int) -> Optional[Dict]:
        # Ensure quest_id_to_find is a string for matching if quest_id in QUESTS is string
        # Or convert quest_data['quest_id'] to int if quest_id_to_find is int
        for quest_data in self.quests_definitions: # Use renamed attribute
            if int(quest_data.get('quest_id', '-1')) == quest_id_to_find:
                return quest_data
        return None

    def _load_existing_progress(self):
        """Load existing quest and trigger progress from files"""
        try:
            # Load quest status
            quest_status_file = self.run_dir / 'quest_status.json'
            if quest_status_file.exists():
                with open(quest_status_file, 'r') as f:
                    loaded_quest_status = json.load(f)
                    # Convert string keys to int keys
                    for key, value in loaded_quest_status.items():
                        quest_id = int(key)
                        if quest_id in self.quest_completed:
                            self.quest_completed[quest_id] = value
                print(f"QuestProgressionEngine: Loaded quest status for {len(loaded_quest_status)} quests")
            
            # Load trigger status
            trigger_status_file = self.run_dir / 'trigger_status.json'
            if trigger_status_file.exists():
                with open(trigger_status_file, 'r') as f:
                    self.trigger_completed = json.load(f)
                print(f"QuestProgressionEngine: Loaded trigger status for {len(self.trigger_completed)} triggers")
                
        except Exception as e:
            print(f"QuestProgressionEngine: Error loading existing progress: {e}")

    def step(self, evaluator):
        """FIXED: Enhanced quest progression with better error handling"""
        try:
            # # Print Quest 002 debug info at the start of every step
            # quest_002_def = next((q for q in self.quests_definitions if q['quest_id'] == '002'), None) # Use renamed attribute
            # if quest_002_def:
            #     triggers_002 = quest_002_def.get('event_triggers', [])
            #     for idx, trg in enumerate(triggers_002):
            #         tid = f"002_{idx}"
            #         completed = self.trigger_completed.get(tid, False)
            #         if not completed:
            #             print(f"[Quest002Debug] Step start: Trigger {tid} is NOT completed. Type: {trg['type']}")
            #         else:
            #             print(f"[Quest002Debug] Step start: Trigger {tid} IS completed. Type: {trg['type']}")
            #     print(f"[Quest002Debug] Overall Quest 002 completed: {self.quest_completed.get(2, False)}")
                
            # 1) evaluate triggers for current quest
            current_qid = getattr(self.quest_manager, 'current_quest_id', None)
            if current_qid is not None:
                active_quest_def = next((qq for qq in self.quests_definitions if int(qq['quest_id']) == current_qid), None) # Use renamed attribute
                if active_quest_def:
                    # Special debug for active quest
                    # print(f"[QuestDebug] Processing active quest {active_quest_def['quest_id']}")
                    
                    # process event_triggers
                    for idx, trg_def in enumerate(active_quest_def.get('event_triggers', [])):
                        tid = f"{active_quest_def['quest_id']}_{idx}"
                        
                        # FIXED: Check if trigger is already completed (don't re-evaluate completed triggers)
                        if self.trigger_completed.get(tid, False):
                            # print(f"[QuestDebug] Trigger {tid} already completed, skipping evaluation")
                            continue
                            
                        # FIXED: For sequence-based quests, we don't require all previous triggers to remain true
                        # We only require that they have been completed at some point
                        # This allows for map transition sequences like 38->37->0
                        
                        # Check if all previous triggers have been completed (not necessarily still true)
                        previous_complete = all(
                            self.trigger_completed.get(f"{active_quest_def['quest_id']}_{i}", False) 
                            for i in range(idx)
                        )
                        
                        # Only evaluate this trigger if it's the first one or all previous are complete
                        if idx == 0 or previous_complete:
                            # print(f"[QuestDebug] Evaluating trigger {tid} (previous triggers complete: {previous_complete})")
                            try:
                                eval_dict = evaluator.check_trigger(trg_def) # Get detailed dict
                                result = eval_dict["result"]
                                values_str = eval_dict["values_str"]
                                debug_str = eval_dict["debug_str"]

                                # print(f"[QuestDebug] Trigger {tid} evaluation result: {result}, Values: {values_str}, Debug: {debug_str}")
                                
                                # Send detailed trigger debug info to UI
                                # Only send for quests 001-004 for now, as requested
                                if active_quest_def['quest_id'] in ['001', '002', '003', '004']:
                                    self.status_queue.put(('__trigger_debug__', {
                                        'id': tid,
                                        'status': result,
                                        'values_str': values_str,
                                        'debug_str': debug_str
                                    }))
                                else: # For other quests, send simple status for now
                                    if result: self.status_queue.put((tid, True))
                                    # No need to send False for simple updates, UI assumes pending

                                if result:
                                    self.trigger_completed[tid] = True
                                    # self.status_queue.put((tid, True)) # Moved to detailed sender above or simple sender
                                    print(f"QuestProgressionEngine: Trigger {tid} completed")
                                    
                                    # # FIXED: Add specific debug for Quest 002
                                    # if active_quest_def['quest_id'] == '002':
                                        # print(f"[Quest002Debug] Trigger {idx} completed: {trg_def}")
                                        # print(f"[Quest002Debug] Completed triggers so far: {[i for i in range(len(active_quest_def.get('event_triggers', []))) if self.trigger_completed.get(f'002_{i}', False)]}")
                            except Exception as e:
                                print(f"QuestProgressionEngine: Error checking trigger {tid}: {e}")
                        # else:
                        #     print(f"[QuestDebug] Skipping trigger {tid} - previous triggers not all complete")
                    
                    # mark quest complete if all triggers done
                    tids = [f"{active_quest_def['quest_id']}_{i}" for i in range(len(active_quest_def.get('event_triggers', [])))]
                    all_triggers_completed_for_quest = all(self.trigger_completed.get(t, False) for t in tids)
                    # print(f"[QuestDebug] Quest {active_quest_def['quest_id']} - All triggers complete? {all_triggers_completed_for_quest}")
                    
                    # Prepare detailed quest status for UI (for quests 001-004)
                    if active_quest_def['quest_id'] in ['001', '002', '003', '004']:
                        num_triggers = len(tids)
                        completed_triggers_count = sum(1 for t_id in tids if self.trigger_completed.get(t_id, False))
                        quest_values_str = f"Triggers: {completed_triggers_count}/{num_triggers}"
                        quest_debug_str = f"Checking completion of {num_triggers} triggers for Quest {active_quest_def['quest_id']}."
                        self.status_queue.put(('__quest_status_detailed__', {
                            'id': active_quest_def['quest_id'],
                            'status': all_triggers_completed_for_quest,
                            'values_str': quest_values_str,
                            'debug_str': quest_debug_str
                        }))
                    # else: No simple quest status update here, will be covered by individual trigger or final completion logic

                    if tids and all_triggers_completed_for_quest:
                        qint = int(active_quest_def['quest_id'])
                        if not self.quest_completed.get(qint, False):
                            self.quest_completed[qint] = True
                            # Send simple completion for all quests if not already sent by detailed one
                            if active_quest_def['quest_id'] not in ['001', '002', '003', '004']:
                                self.status_queue.put((active_quest_def['quest_id'], True))
                            # print(f"QuestProgressionEngine: Quest {qint} completed")
                            
                            # # Ensure all components know about the completion
                            # if active_quest_def['quest_id'] == '002':
                                # print(f"[Quest002Debug] All triggers complete - marking Quest 002 as completed!")
                                # print(f"[Quest002Debug] Quest status after completion: {self.quest_completed}")
                                
                        # Update the definition in quests_definitions if it's mutable and shared, though this is risky
                        # It's better if QuestProgressionEngine manages its own state (self.quest_completed)
                        # and the UI reads from a consistently updated source (like status_queue or a getter)
                        # For now, we assume self.quest_completed is the source of truth for the engine.
            
            # 2) advance to next quest if needed
            current_qid = getattr(self.quest_manager, 'current_quest_id', None)
            if current_qid is not None and self.quest_completed.get(current_qid, False):
                # find next
                next_qid = None
                for qid in self.quest_ids_all:
                    if not self.quest_completed[qid]:
                        next_qid = qid
                        break
                
                if next_qid and next_qid != current_qid:
                    # FIXED: Add extra debug logging for quest advancement
                    print(f"[QuestAdvance] Moving from Quest {current_qid} to Quest {next_qid}")
                    
                    # FIXED: Ensure quest ID is correctly set across all components
                    self.quest_manager.current_quest_id = next_qid
                    if hasattr(self.navigator, 'active_quest_id'):
                        self.navigator.active_quest_id = next_qid
                    if hasattr(self.env, 'current_loaded_quest_id'):
                        self.env.current_loaded_quest_id = next_qid
                    
                    # Notify UI of quest change
                    self.status_queue.put(('__current_quest__', next_qid))
                    print(f"QuestProgressionEngine: Advanced to quest {next_qid}")
                    
                    # FIXED: Ensure quest path is loaded for the new quest
                    if hasattr(self.navigator, 'load_coordinate_path'):
                        if self.navigator.load_coordinate_path(next_qid):
                            print(f"[QuestAdvance] Successfully loaded coordinate path for quest {next_qid}")
                        else:
                            print(f"[QuestAdvance] Failed to load coordinate path for quest {next_qid}")
            
            # 3) persist statuses with better error handling
            self._persist_progress()
            
        except Exception as e:
            print(f"QuestProgressionEngine: Critical error in step(): {e}")

    def _persist_progress(self):
        """Persist quest and trigger progress to files"""
        try:
            # Save trigger status
            trigger_file = self.run_dir / 'trigger_status.json'
            with open(trigger_file, 'w') as f:
                json.dump(self.trigger_completed, f, indent=4)
            
            # Save quest status
            quest_file = self.run_dir / 'quest_status.json'
            quest_status_for_save = {str(qid).zfill(3): val for qid, val in self.quest_completed.items()}
            with open(quest_file, 'w') as f:
                json.dump(quest_status_for_save, f, indent=4)
                
        except Exception as e:
            print(f"QuestProgressionEngine: Error persisting progress: {e}")

    def get_quest_status(self) -> Dict[int, bool]:
        """Get current quest completion status"""
        return self.quest_completed.copy()

    def get_trigger_status(self) -> Dict[str, bool]:
        """Get current trigger completion status"""
        return self.trigger_completed.copy()

    def force_complete_quest(self, quest_id: int):
        """Manually mark a quest as completed"""
        if quest_id in self.quest_completed:
            self.quest_completed[quest_id] = True
            self.status_queue.put((str(quest_id).zfill(3), True))
            self._persist_progress()
            print(f"QuestProgressionEngine: Manually completed quest {quest_id}")

    def reset_quest(self, quest_id: int):
        """Reset a quest to incomplete status"""
        if quest_id in self.quest_completed:
            self.quest_completed[quest_id] = False
            # Reset associated triggers
            for trigger_id in list(self.trigger_completed.keys()):
                if trigger_id.startswith(f"{quest_id:03d}_"):
                    self.trigger_completed[trigger_id] = False
            self.status_queue.put((str(quest_id).zfill(3), False))
            self._persist_progress()
            print(f"QuestProgressionEngine: Reset quest {quest_id}")

    def save_status_to_json(self):
        """Save current quest and trigger status to JSON files"""
        self._persist_progress() 