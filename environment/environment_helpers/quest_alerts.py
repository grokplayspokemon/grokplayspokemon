import dearpygui.dearpygui as dpg
import threading
import time
from typing import Dict, Any, Optional, Callable
from multiprocessing import Process
import sys
import os

# Add logging integration
sys.path.append('/puffertank/grok_plays_pokemon')
from utils.logging_config import get_pokemon_logger

class QuestAlertManager:
    """
    Dear PyGui-based alert system for quest issues
    Spawns popup windows when quest problems are detected
    """
    def __init__(self, logger=None):
        if logger is None:
            self.logger = get_pokemon_logger()
        else:
            self.logger = logger
        
        self.active_alerts = {}  # Track open alerts to prevent duplicates
        self.alert_callbacks = {}  # Store callbacks for different alert types
        self.dpg_context = None
        self.alert_counter = 0  # For unique alert IDs
        
        # Initialize DPG context
        self._init_dpg()
        
        self.logger.log_system_event("QuestAlertManager initialized", {
            'component': 'quest_alerts'
        })
    
    def _init_dpg(self):
        """Initialize Dear PyGui context"""
        try:
            self.dpg_context = dpg.create_context()
            dpg.configure_app(docking=True, docking_space=True)
            dpg.create_viewport(title="Pokemon Quest Alerts", width=600, height=400)
            dpg.setup_dearpygui()
            
            # Start in separate thread to not block main process
            self.dpg_thread = threading.Thread(target=self._run_dpg, daemon=True)
            self.dpg_thread.start()
            
            self.logger.log_system_event("Dear PyGui context initialized", {
                'component': 'quest_alerts'
            })
        except Exception as e:
            self.logger.log_error("QuestAlerts", f"Failed to initialize DPG: {str(e)}")
            self.dpg_context = None
    
    def _run_dpg(self):
        """Run Dear PyGui in separate thread"""
        try:
            dpg.show_viewport()
            dpg.start_dearpygui()
        except Exception as e:
            self.logger.log_error("QuestAlerts", f"DPG runtime error: {str(e)}")
        finally:
            if self.dpg_context:
                dpg.destroy_context()
    
    def register_alert_callback(self, action_type: str, callback: Callable):
        """Register callback for alert actions"""
        self.alert_callbacks[action_type] = callback
        self.logger.log_system_event(f"Alert callback registered for {action_type}", {
            'component': 'quest_alerts',
            'action_type': action_type
        })
    
    def create_quest_stuck_alert(self, quest_id: str, stuck_data: Dict[str, Any]):
        """Create a popup alert for stuck quest"""
        alert_id = f"quest_stuck_{quest_id}_{self.alert_counter}"
        self.alert_counter += 1
        
        # Don't create duplicate alerts
        if alert_id in self.active_alerts:
            return
        
        # Store alert
        self.active_alerts[alert_id] = {
            'type': 'quest_stuck',
            'quest_id': quest_id,
            'data': stuck_data,
            'created_at': time.time()
        }
        
        if self.dpg_context is None:
            # Fallback to console output if DPG is not available
            self._console_fallback_alert("QUEST STUCK", quest_id, stuck_data)
            return
        
        # Create DPG window
        self._create_dpg_quest_stuck_window(alert_id, quest_id, stuck_data)
        
        self.logger.log_quest_event(quest_id, "Quest stuck alert created", {
            'alert_id': alert_id,
            'stuck_duration': stuck_data.get('duration', 'unknown')
        })
    
    def create_trigger_failure_alert(self, trigger_id: str, failure_data: Dict[str, Any]):
        """Create a popup alert for trigger failure"""
        alert_id = f"trigger_failure_{trigger_id}_{self.alert_counter}"
        self.alert_counter += 1
        
        # Don't create duplicate alerts
        if alert_id in self.active_alerts:
            return
        
        # Store alert
        self.active_alerts[alert_id] = {
            'type': 'trigger_failure',
            'trigger_id': trigger_id,
            'data': failure_data,
            'created_at': time.time()
        }
        
        if self.dpg_context is None:
            # Fallback to console output if DPG is not available
            self._console_fallback_alert("TRIGGER FAILURE", trigger_id, failure_data)
            return
        
        # Create DPG window
        self._create_dpg_trigger_failure_window(alert_id, trigger_id, failure_data)
        
        self.logger.log_trigger_event(trigger_id, "Trigger failure alert created", "", "")
    
    def create_navigation_alert(self, nav_data: Dict[str, Any]):
        """Create a popup alert for navigation issues"""
        alert_id = f"navigation_issue_{self.alert_counter}"
        self.alert_counter += 1
        
        # Store alert
        self.active_alerts[alert_id] = {
            'type': 'navigation_issue',
            'data': nav_data,
            'created_at': time.time()
        }
        
        if self.dpg_context is None:
            # Fallback to console output if DPG is not available
            self._console_fallback_alert("NAVIGATION ISSUE", "NAV", nav_data)
            return
        
        # Create DPG window
        self._create_dpg_navigation_window(alert_id, nav_data)
        
        self.logger.log_navigation_event("NAVIGATION_ALERT_CREATED", nav_data)
    
    def _create_dpg_quest_stuck_window(self, alert_id: str, quest_id: str, stuck_data: Dict[str, Any]):
        """Create Dear PyGui window for stuck quest"""
        window_id = f"window_{alert_id}"
        
        with dpg.window(label=f"🚨 Quest {quest_id} STUCK 🚨", 
                       modal=True, 
                       show=True, 
                       tag=window_id,
                       width=500, 
                       height=400):
            
            # Title and critical info
            dpg.add_text(f"⚠️  QUEST {quest_id} HAS BEEN STUCK", color=[255, 100, 100])
            dpg.add_separator()
            
            # Duration info
            dpg.add_text(f"Stuck Duration: {stuck_data.get('duration', 'Unknown')}")
            dpg.add_text(f"Last Progress: {stuck_data.get('last_progress', 'Never')}")
            dpg.add_text(f"Status: {stuck_data.get('current_status', 'UNKNOWN')}")
            
            dpg.add_separator()
            
            # Quest details
            quest_data = stuck_data.get('quest_data')
            if quest_data:
                dpg.add_text("Quest Details:")
                if isinstance(quest_data, dict):
                    for key, value in quest_data.items():
                        if key not in ['triggers', 'conditions']:  # Skip complex objects
                            dpg.add_text(f"  {key}: {value}")
            
            # Trigger details
            trigger_details = stuck_data.get('trigger_details', {})
            if trigger_details:
                dpg.add_separator()
                dpg.add_text("Trigger Status:")
                for trigger_id, details in trigger_details.items():
                    status_color = [100, 255, 100] if details.get('completed') else [255, 100, 100]
                    dpg.add_text(f"  {trigger_id}: {details.get('status', 'Unknown')}", 
                               color=status_color)
            
            dpg.add_separator()
            
            # Action buttons
            with dpg.group(horizontal=True):
                dpg.add_button(label="🔄 Retry Quest", 
                             callback=lambda: self._handle_alert_action("retry", alert_id, quest_id, stuck_data),
                             width=100)
                dpg.add_button(label="⏭️ Skip Quest", 
                             callback=lambda: self._handle_alert_action("skip", alert_id, quest_id, stuck_data),
                             width=100)
                dpg.add_button(label="🛠️ Manual Fix", 
                             callback=lambda: self._handle_alert_action("manual", alert_id, quest_id, stuck_data),
                             width=100)
            
            with dpg.group(horizontal=True):
                dpg.add_button(label="📋 Investigate", 
                             callback=lambda: self._handle_alert_action("investigate", alert_id, quest_id, stuck_data),
                             width=100)
                dpg.add_button(label="✅ Continue", 
                             callback=lambda: self._handle_alert_action("continue", alert_id, quest_id, stuck_data),
                             width=100)
    
    def _create_dpg_trigger_failure_window(self, alert_id: str, trigger_id: str, failure_data: Dict[str, Any]):
        """Create Dear PyGui window for trigger failure"""
        window_id = f"window_{alert_id}"
        
        with dpg.window(label=f"🔥 Trigger {trigger_id} FAILING 🔥", 
                       modal=True, 
                       show=True, 
                       tag=window_id,
                       width=500, 
                       height=350):
            
            # Title and critical info
            dpg.add_text(f"⚠️  TRIGGER {trigger_id} IS PERSISTENTLY FAILING", color=[255, 100, 100])
            dpg.add_separator()
            
            # Failure info
            dpg.add_text(f"Failure Count: {failure_data.get('failure_count', 'Unknown')}")
            dpg.add_text(f"Last Check: {failure_data.get('last_check', 'Never')}")
            dpg.add_text(f"Error: {failure_data.get('last_error', 'No details')}")
            
            dpg.add_separator()
            
            # Expected vs actual
            dpg.add_text("Expected:", color=[100, 255, 100])
            dpg.add_text(f"  {failure_data.get('expected_condition', 'Unknown')}")
            dpg.add_text("Actual:", color=[255, 100, 100])
            dpg.add_text(f"  {failure_data.get('actual_condition', 'Unknown')}")
            
            dpg.add_separator()
            
            # Action buttons
            with dpg.group(horizontal=True):
                dpg.add_button(label="✅ Force Complete", 
                             callback=lambda: self._handle_alert_action("force_complete", alert_id, trigger_id, failure_data),
                             width=120)
                dpg.add_button(label="🔍 Investigate", 
                             callback=lambda: self._handle_alert_action("investigate", alert_id, trigger_id, failure_data),
                             width=100)
            
            with dpg.group(horizontal=True):
                dpg.add_button(label="🚫 Ignore", 
                             callback=lambda: self._handle_alert_action("ignore", alert_id, trigger_id, failure_data),
                             width=100)
                dpg.add_button(label="📋 Log Details", 
                             callback=lambda: self._handle_alert_action("log_details", alert_id, trigger_id, failure_data),
                             width=100)
    
    def _create_dpg_navigation_window(self, alert_id: str, nav_data: Dict[str, Any]):
        """Create Dear PyGui window for navigation issues"""
        window_id = f"window_{alert_id}"
        
        with dpg.window(label="🧭 Navigation Issue 🧭", 
                       modal=True, 
                       show=True, 
                       tag=window_id,
                       width=450, 
                       height=300):
            
            # Title and critical info
            dpg.add_text("⚠️  NAVIGATION SYSTEM ISSUE DETECTED", color=[255, 150, 50])
            dpg.add_separator()
            
            # Navigation details
            dpg.add_text(f"Issue: {nav_data.get('issue', 'Unknown navigation problem')}")
            if 'current_position' in nav_data:
                dpg.add_text(f"Current Position: {nav_data['current_position']}")
            if 'target_position' in nav_data:
                dpg.add_text(f"Target Position: {nav_data['target_position']}")
            if 'error_message' in nav_data:
                dpg.add_text(f"Error: {nav_data['error_message']}")
            
            dpg.add_separator()
            
            # Action buttons
            with dpg.group(horizontal=True):
                dpg.add_button(label="🔄 Reset Navigation", 
                             callback=lambda: self._handle_alert_action("reset_navigation", alert_id, "NAV", nav_data),
                             width=140)
                dpg.add_button(label="⚡ Emergency Snap", 
                             callback=lambda: self._handle_alert_action("emergency_snap", alert_id, "NAV", nav_data),
                             width=130)
            
            with dpg.group(horizontal=True):
                dpg.add_button(label="✅ Continue", 
                             callback=lambda: self._handle_alert_action("continue", alert_id, "NAV", nav_data),
                             width=100)
    
    def _handle_alert_action(self, action: str, alert_id: str, item_id: str, data: Dict[str, Any]):
        """Handle alert action button clicks"""
        try:
            # Close the alert window
            window_id = f"window_{alert_id}"
            if dpg.does_item_exist(window_id):
                dpg.delete_item(window_id)
            
            # Remove from active alerts
            if alert_id in self.active_alerts:
                del self.active_alerts[alert_id]
            
            # Execute callback if registered
            if action in self.alert_callbacks:
                self.alert_callbacks[action](item_id, data)
            else:
                self.logger.log_system_event(f"No callback registered for action: {action}", {
                    'component': 'quest_alerts',
                    'action': action,
                    'item_id': item_id
                })
            
            self.logger.log_system_event(f"Alert action executed: {action}", {
                'component': 'quest_alerts',
                'action': action,
                'item_id': item_id,
                'alert_id': alert_id
            })
            
        except Exception as e:
            self.logger.log_error("QuestAlerts", f"Error handling alert action {action}: {str(e)}")
    
    def _console_fallback_alert(self, alert_type: str, item_id: str, data: Dict[str, Any]):
        """Fallback console output when DPG is not available"""
        print(f"\n{'='*60}")
        print(f"🚨 {alert_type} ALERT: {item_id} 🚨")
        print(f"{'='*60}")
        
        for key, value in data.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for k, v in value.items():
                    print(f"  {k}: {v}")
            else:
                print(f"{key}: {value}")
        
        print(f"{'='*60}")
        print("Actions available: Check logs for details and manual intervention")
        print(f"{'='*60}\n")
        
        # Log to file system as well
        self.logger.log_system_event(f"Console fallback alert: {alert_type}", {
            'component': 'quest_alerts',
            'item_id': item_id,
            'data': data,
            'fallback_reason': 'DPG not available'
        })
    
    def cleanup_alerts(self):
        """Clean up all active alerts"""
        try:
            for alert_id in list(self.active_alerts.keys()):
                window_id = f"window_{alert_id}"
                if dpg.does_item_exist(window_id):
                    dpg.delete_item(window_id)
            
            self.active_alerts.clear()
            
            self.logger.log_system_event("All alerts cleaned up", {
                'component': 'quest_alerts'
            })
        except Exception as e:
            self.logger.log_error("QuestAlerts", f"Error cleaning up alerts: {str(e)}")
    
    def get_active_alert_count(self) -> int:
        """Get number of active alerts"""
        return len(self.active_alerts)
    
    def __del__(self):
        """Cleanup on destruction"""
        try:
            self.cleanup_alerts()
            if self.dpg_context:
                dpg.destroy_context()
        except:
            pass  # Ignore cleanup errors during destruction

# Global instance
quest_alert_manager = QuestAlertManager() 