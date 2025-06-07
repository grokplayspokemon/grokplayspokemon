import time
from typing import Dict, Any, Optional, Set
from collections import defaultdict, deque
import threading
import sys
import os

# Add logging integration
sys.path.append('/puffertank/grok_plays_pokemon')
from utils.logging_config import get_pokemon_logger
from environment.environment_helpers.quest_alerts import quest_alert_manager

class QuestMonitor:
    """
    Monitor quest progression and detect issues that require immediate attention
    Triggers Dear PyGui alerts when problems are detected
    """
    def __init__(self, quest_progression_engine, logger=None):
        if logger is None:
            self.logger = get_pokemon_logger()
        else:
            self.logger = logger
        
        self.quest_progression = quest_progression_engine
        self.last_quest_progress = {}  # quest_id -> last_progress_time
        self.trigger_failure_counts = defaultdict(int)  # trigger_id -> failure_count
        self.last_trigger_states = {}  # trigger_id -> (result, timestamp)
        self.quest_stuck_threshold = 300  # 5 minutes without progress
        self.trigger_failure_threshold = 20  # 20 consecutive failures
        
        # Track quest advancement history
        self.quest_history = deque(maxlen=10)
        self.current_quest_start_time = time.time()
        
        # Monitor thread
        self.monitoring = False
        self.monitor_thread = None
        
        # Register alert callbacks
        self._setup_alert_callbacks()
        
        self.logger.log_system_event("QuestMonitor initialized", {
            'component': 'quest_monitor',
            'stuck_threshold': self.quest_stuck_threshold,
            'failure_threshold': self.trigger_failure_threshold
        })
    
    def _setup_alert_callbacks(self):
        """Setup callbacks for different alert types"""
        
        def handle_retry_quest(quest_id: str, stuck_data: Dict[str, Any]):
            """Handle retry quest action"""
            self.logger.log_quest_event(quest_id, "Retrying stuck quest", {
                'action': 'quest_retry'
            })
            
            # Reset quest monitoring for this quest
            if quest_id in self.last_quest_progress:
                del self.last_quest_progress[quest_id]
            
            # Reset associated triggers
            for trigger_id in list(self.trigger_failure_counts.keys()):
                if trigger_id.startswith(f"{quest_id}_"):
                    self.trigger_failure_counts[trigger_id] = 0
                    if trigger_id in self.last_trigger_states:
                        del self.last_trigger_states[trigger_id]
            
            # Restart quest progression for this quest
            try:
                if hasattr(self.quest_progression, 'reset_quest'):
                    self.quest_progression.reset_quest(int(quest_id))
            except Exception as e:
                self.logger.log_error("QuestMonitor", f"Error resetting quest {quest_id}: {str(e)}")
        
        def handle_skip_quest(quest_id: str, stuck_data: Dict[str, Any]):
            """Handle skip quest action"""
            self.logger.log_quest_event(quest_id, "Skipping stuck quest", {
                'action': 'quest_skip'
            })
            
            try:
                if hasattr(self.quest_progression, 'force_complete_quest'):
                    self.quest_progression.force_complete_quest(int(quest_id))
            except Exception as e:
                self.logger.log_error("QuestMonitor", f"Error skipping quest {quest_id}: {str(e)}")
        
        def handle_manual_intervention(quest_id: str, stuck_data: Dict[str, Any]):
            """Handle manual intervention action"""
            self.logger.log_quest_event(quest_id, "Manual intervention requested", {
                'action': 'manual_intervention',
                'stuck_data': stuck_data
            })
            # Just log for now - manual intervention requires external action
        
        def handle_force_complete_trigger(trigger_id: str, failure_data: Dict[str, Any]):
            """Handle force complete trigger action"""
            self.logger.log_trigger_event(trigger_id, "Force completing failed trigger", "", "")
            
            # Add trigger to completed set
            if hasattr(self.quest_progression, 'trigger_completed'):
                self.quest_progression.trigger_completed.add(trigger_id)
            
            # Reset failure count
            self.trigger_failure_counts[trigger_id] = 0
        
        def handle_investigate_trigger(trigger_id: str, failure_data: Dict[str, Any]):
            """Handle investigate trigger action"""
            self.logger.log_trigger_event(trigger_id, "Investigation requested for failing trigger", "", "")
            # Log detailed failure data for investigation
            self.logger.log_system_event(f"Trigger {trigger_id} investigation data", failure_data)
        
        def handle_ignore_trigger(trigger_id: str, failure_data: Dict[str, Any]):
            """Handle ignore trigger action"""
            self.logger.log_trigger_event(trigger_id, "Ignoring failed trigger", "", "")
            # Reset failure count to stop alerts
            self.trigger_failure_counts[trigger_id] = 0
        
        def handle_reset_navigation(nav_data: Dict[str, Any]):
            """Handle navigation reset action"""
            self.logger.log_navigation_event("NAVIGATION_RESET_REQUESTED", {
                'message': 'User requested navigation reset',
                'nav_data': nav_data
            })
            
            # Try to reset navigator if available
            if hasattr(self.quest_progression, 'navigator'):
                try:
                    if hasattr(self.quest_progression.navigator, 'snap_to_nearest_coordinate'):
                        self.quest_progression.navigator.snap_to_nearest_coordinate()
                        self.logger.log_navigation_event("NAVIGATION_RESET_SUCCESS", {
                            'message': 'Navigation successfully reset'
                        })
                except Exception as e:
                    self.logger.log_error("QuestMonitor", f"Error resetting navigation: {str(e)}")
        
        def handle_emergency_snap(nav_data: Dict[str, Any]):
            """Handle emergency snap action"""
            self.logger.log_navigation_event("EMERGENCY_SNAP_REQUESTED", {
                'message': 'User requested emergency snap',
                'nav_data': nav_data
            })
            
            if hasattr(self.quest_progression, 'navigator'):
                try:
                    if hasattr(self.quest_progression.navigator, '_emergency_snap_to_path'):
                        self.quest_progression.navigator._emergency_snap_to_path()
                        self.logger.log_navigation_event("EMERGENCY_SNAP_SUCCESS", {
                            'message': 'Emergency snap successful'
                        })
                except Exception as e:
                    self.logger.log_error("QuestMonitor", f"Error with emergency snap: {str(e)}")
        
        # Register all callbacks
        quest_alert_manager.register_alert_callback("retry", handle_retry_quest)
        quest_alert_manager.register_alert_callback("skip", handle_skip_quest)
        quest_alert_manager.register_alert_callback("manual", handle_manual_intervention)
        quest_alert_manager.register_alert_callback("force_complete", handle_force_complete_trigger)
        quest_alert_manager.register_alert_callback("investigate", handle_investigate_trigger)
        quest_alert_manager.register_alert_callback("ignore", handle_ignore_trigger)
        quest_alert_manager.register_alert_callback("reset_navigation", handle_reset_navigation)
        quest_alert_manager.register_alert_callback("emergency_snap", handle_emergency_snap)
        quest_alert_manager.register_alert_callback("continue", lambda data: None)  # No-op for continue
    
    def start_monitoring(self):
        """Start the monitoring thread"""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        self.logger.log_system_event("Quest monitoring started", {
            'component': 'quest_monitor'
        })
    
    def stop_monitoring(self):
        """Stop the monitoring thread"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        self.logger.log_system_event("Quest monitoring stopped", {
            'component': 'quest_monitor'
        })
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        while self.monitoring:
            try:
                self._check_quest_progress()
                self._check_trigger_failures()
                time.sleep(10)  # Check every 10 seconds
            except Exception as e:
                self.logger.log_error("QuestMonitor", f"Error in monitoring loop: {str(e)}")
                time.sleep(5)  # Wait before retrying
    
    def _check_quest_progress(self):
        """Check if any quests appear to be stuck"""
        current_time = time.time()
        
        # Get current quest
        if hasattr(self.quest_progression, 'quest_manager'):
            current_quest = getattr(self.quest_progression.quest_manager, 'current_quest_id', None)
            
            if current_quest is not None:
                quest_id = str(current_quest).zfill(3)
                
                # Check if quest is making progress
                if quest_id not in self.last_quest_progress:
                    self.last_quest_progress[quest_id] = current_time
                    return
                
                time_since_progress = current_time - self.last_quest_progress[quest_id]
                
                # If quest has been stuck for too long, trigger alert
                if time_since_progress > self.quest_stuck_threshold:
                    self._trigger_quest_stuck_alert(quest_id, time_since_progress)
    
    def _check_trigger_failures(self):
        """Check for persistent trigger failures"""
        for trigger_id, failure_count in self.trigger_failure_counts.items():
            if failure_count >= self.trigger_failure_threshold:
                self._trigger_trigger_failure_alert(trigger_id, failure_count)
    
    def _trigger_quest_stuck_alert(self, quest_id: str, stuck_duration: float):
        """Trigger a stuck quest alert"""
        # Get quest details
        quest_data = None
        if hasattr(self.quest_progression, 'get_quest_data_by_id'):
            try:
                quest_data = self.quest_progression.get_quest_data_by_id(int(quest_id))
            except:
                pass
        
        # Get trigger details for this quest
        trigger_details = {}
        for trigger_id in self.last_trigger_states:
            if trigger_id.startswith(f"{quest_id}_"):
                result, timestamp = self.last_trigger_states[trigger_id]
                trigger_details[trigger_id] = {
                    'completed': result,
                    'status': 'Completed' if result else 'Pending',
                    'last_check': timestamp
                }
        
        stuck_data = {
            'duration': f"{int(stuck_duration)} seconds",
            'last_progress': time.strftime('%H:%M:%S', time.localtime(self.last_quest_progress.get(quest_id, 0))),
            'current_status': 'STUCK',
            'trigger_details': trigger_details,
            'quest_data': quest_data
        }
        
        self.logger.log_quest_event(quest_id, f"Quest stuck detected - {stuck_duration}s without progress", {
            'stuck_duration': stuck_duration,
            'action': 'stuck_quest_alert'
        })
        
        quest_alert_manager.create_quest_stuck_alert(quest_id, stuck_data)
    
    def _trigger_trigger_failure_alert(self, trigger_id: str, failure_count: int):
        """Trigger a trigger failure alert"""
        last_state = self.last_trigger_states.get(trigger_id, (False, 0))
        
        failure_data = {
            'failure_count': failure_count,
            'last_error': f"Trigger has failed {failure_count} consecutive times",
            'expected_condition': 'Trigger should evaluate to True',
            'actual_condition': 'Trigger consistently evaluates to False',
            'last_check': time.strftime('%H:%M:%S', time.localtime(last_state[1]))
        }
        
        self.logger.log_trigger_event(trigger_id, f"Persistent trigger failure - {failure_count} failures", "", "")
        
        quest_alert_manager.create_trigger_failure_alert(trigger_id, failure_data)
        
        # Reset counter to prevent spam alerts
        self.trigger_failure_counts[trigger_id] = 0
    
    def update_quest_progress(self, quest_id: str):
        """Update quest progress timestamp"""
        self.last_quest_progress[quest_id] = time.time()
    
    def update_trigger_state(self, trigger_id: str, result: bool):
        """Update trigger state and track failures"""
        current_time = time.time()
        self.last_trigger_states[trigger_id] = (result, current_time)
        
        if result:
            # Reset failure count on success
            if trigger_id in self.trigger_failure_counts:
                self.trigger_failure_counts[trigger_id] = 0
            
            # Update quest progress for the associated quest
            quest_id = trigger_id.split('_')[0]
            self.update_quest_progress(quest_id)
        else:
            # Increment failure count
            self.trigger_failure_counts[trigger_id] += 1
    
    def cleanup(self):
        """Clean up the monitor"""
        self.stop_monitoring()
        quest_alert_manager.cleanup_alerts()
        self.logger.log_system_event("QuestMonitor cleaned up", {
            'component': 'quest_monitor'
        }) 