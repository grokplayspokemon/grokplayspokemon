"""
Advanced Logging Configuration for Pokemon Game System
======================================================

Provides comprehensive logging with line-based file rotation, multiple specialized loggers,
and structured logging capabilities specifically designed for Pokemon game analysis.

Features:
- Line-based file rotation (20k lines per file)
- Multiple specialized log streams (game, quest, navigation, environment, performance, errors, debug)
- Structured JSON logging for debug data
- Automatic terminal redirection
- Easy integration with existing codebase

Usage:
    from utils.logging_config import get_pokemon_logger, setup_logging
    
    # Initialize logging system
    setup_logging()
    
    # Get logger instance
    logger = get_pokemon_logger()
    
    # Use specialized logging methods
    logger.log_quest_event("002", "Quest advanced", {"from": 1, "to": 2})
    logger.log_navigation_event("MAP_CHANGE", {"from_map": 38, "to_map": 0})
    logger.log_error("PATHFOLLOW", "Navigation error occurred", {"position": [100, 200]})
"""

import logging
import logging.handlers
import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Union
import threading
from io import StringIO

class LineCountRotatingFileHandler(logging.Handler):
    """
    Custom handler that rotates log files based on line count rather than file size.
    Creates new files when line limit is reached with timestamped naming.
    """
    
    def __init__(self, filename: Union[str, Path], max_lines: int = 10000, backup_count: int = 10):
        super().__init__()
        self.filename = Path(filename)
        self.max_lines = max_lines
        self.backup_count = backup_count
        self.current_lines = 0
        self._lock = threading.Lock()
        
        # Ensure directory exists
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize current file
        self._init_current_file()
    
    def _init_current_file(self):
        """Initialize or reset the current log file"""
        if self.filename.exists():
            # Count existing lines
            with open(self.filename, 'r', encoding='utf-8', errors='ignore') as f:
                self.current_lines = sum(1 for _ in f)
        else:
            self.current_lines = 0
            # Create empty file
            with open(self.filename, 'w', encoding='utf-8') as f:
                f.write(f"# Log started at {datetime.now().isoformat()}\n")
                self.current_lines = 1
    
    def _rotate_file(self):
        """Rotate the current log file with timestamp"""
        if not self.filename.exists():
            return
            
        # Create timestamped backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = self.filename.with_name(f"{self.filename.stem}_{timestamp}.log")
        
        try:
            # Move current file to backup
            os.rename(self.filename, backup_name)
            
            # Clean up old backups if needed
            self._cleanup_old_backups()
            
            # Reset line counter
            self.current_lines = 0
            
            # Create new file
            with open(self.filename, 'w', encoding='utf-8') as f:
                f.write(f"# Log rotated at {datetime.now().isoformat()}\n")
                self.current_lines = 1
                
        except Exception as e:
            # Fallback - just recreate the file
            print(f"Warning: Failed to rotate log file {self.filename}: {e}")
            with open(self.filename, 'w', encoding='utf-8') as f:
                f.write(f"# Log recreated at {datetime.now().isoformat()} (rotation failed)\n")
                self.current_lines = 1
    
    def _cleanup_old_backups(self):
        """Remove old backup files beyond the backup count"""
        try:
            pattern = f"{self.filename.stem}_*.log"
            backup_files = sorted(self.filename.parent.glob(pattern))
            
            while len(backup_files) > self.backup_count:
                oldest = backup_files.pop(0)
                oldest.unlink()
                
        except Exception as e:
            print(f"Warning: Failed to cleanup old backup files: {e}")
    
    def emit(self, record):
        """Emit a log record, rotating file if necessary"""
        with self._lock:
            # Check if rotation is needed
            if self.current_lines >= self.max_lines:
                self._rotate_file()
            
            try:
                # Format and write the record
                msg = self.format(record)
                with open(self.filename, 'a', encoding='utf-8') as f:
                    f.write(msg + '\n')
                    f.flush()
                
                self.current_lines += 1
                
            except Exception as e:
                print(f"Error writing to log file {self.filename}: {e}")

class StructuredFormatter(logging.Formatter):
    """
    Formatter that outputs structured JSON for debug logs and readable format for others
    """
    
    def __init__(self, structured=False):
        self.structured = structured
        if structured:
            super().__init__()
        else:
            super().__init__(
                fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
    
    def format(self, record):
        if self.structured:
            # Create structured JSON log entry
            log_data = {
                'timestamp': datetime.fromtimestamp(record.created).isoformat(),
                'level': record.levelname,
                'logger': record.name,
                'message': record.getMessage(),
                'module': record.module,
                'function': record.funcName,
                'line': record.lineno
            }
            
            # Add extra data if present
            if hasattr(record, 'extra_data'):
                log_data['data'] = record.extra_data
            
            return json.dumps(log_data, ensure_ascii=False)
        else:
            return super().format(record)

class LoggerWriter:
    """
    Writer class to redirect print statements to logger
    """
    
    def __init__(self, logger, level=logging.INFO):
        self.logger = logger
        self.level = level
        self._buffer = StringIO()
    
    def write(self, text):
        if text.strip():  # Only log non-empty lines
            self.logger.log(self.level, text.strip())
    
    def flush(self):
        pass

# Add map tracking at module level for persistence
_current_map_id = None
_previous_map_ids = []

class PokemonLogger:
    """
    Specialized logger for Pokemon game system with multiple log streams
    """
    
    def __init__(self, logs_dir: Path, max_lines: int = 10000, overwrite_logs: bool = True):
        self.logs_dir = Path(logs_dir)
        self.max_lines = max_lines
        self.overwrite_logs = overwrite_logs
        
        # Ensure logs directory exists
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize map tracking
        global _current_map_id, _previous_map_ids
        _current_map_id = None
        _previous_map_ids = []
        
        # Initialize all loggers
        self._setup_loggers()
        
        # Store original stdout/stderr for restoration
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        
    def _setup_loggers(self):
        """Setup all specialized loggers"""
        
        # Main game logger
        self.game_logger = self._create_logger('pokemon.game', 'game.log')
        
        # Quest-specific logger
        self.quest_logger = self._create_logger('pokemon.quest', 'quest.log')
        
        # Navigation logger
        self.nav_logger = self._create_logger('pokemon.navigation', 'navigation.log')
        
        # Environment logger
        self.env_logger = self._create_logger('pokemon.environment', 'environment.log')
        
        # Performance logger
        self.perf_logger = self._create_logger('pokemon.performance', 'performance.log')
        
        # Error logger
        self.error_logger = self._create_logger('pokemon.errors', 'errors.log')
        
        # Debug logger with structured output
        self.debug_logger = self._create_logger('pokemon.debug', 'debug.log', structured=True)
        
    def _create_logger(self, name: str, filename: str, structured: bool = False) -> logging.Logger:
        """Create a logger with optional rotation or overwrite mode"""
        
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers to avoid duplicates
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Create file handler - either rotating or simple overwrite
        file_path = self.logs_dir / filename
        
        if self.overwrite_logs:
            # Simple file handler that overwrites on each run
            # Clear the file first
            if file_path.exists():
                file_path.unlink()
            
            handler = logging.FileHandler(file_path, mode='w', encoding='utf-8')
        else:
            # Use rotating handler
            handler = LineCountRotatingFileHandler(file_path, max_lines=self.max_lines)
        
        # Set formatter
        formatter = StructuredFormatter(structured=structured)
        handler.setFormatter(formatter)
        
        logger.addHandler(handler)
        logger.propagate = False  # Prevent duplicate logs
        
        return logger
    
    def update_map_tracking(self, current_map_id: int):
        """Update map tracking for logging context"""
        global _current_map_id, _previous_map_ids
        
        if _current_map_id is not None and _current_map_id != current_map_id:
            # Add previous map to history
            _previous_map_ids.append(_current_map_id)
            # Keep only last 2 previous maps
            if len(_previous_map_ids) > 2:
                _previous_map_ids = _previous_map_ids[-2:]
        
        _current_map_id = current_map_id
        
        # Log map change
        self.log_environment_event("MAP_CHANGE", {
            'current_map_id': current_map_id,
            'previous_map_ids': _previous_map_ids.copy()
        })
    
    def get_map_context(self) -> Dict[str, Any]:
        """Get current map context for logging"""
        global _current_map_id, _previous_map_ids
        return {
            'current_map_id': _current_map_id,
            'previous_map_ids': _previous_map_ids.copy()
        }
    
    def redirect_stdout_stderr(self):
        """Redirect stdout and stderr to loggers"""
        sys.stdout = LoggerWriter(self.game_logger, logging.INFO)
        sys.stderr = LoggerWriter(self.error_logger, logging.ERROR)
    
    def restore_stdout_stderr(self):
        """Restore original stdout and stderr"""
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
    
    # Specialized logging methods
    def log_quest_event(self, quest_id: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Log quest-related events with map context"""
        map_context = self.get_map_context()
        
        extra_msg = f"Quest {quest_id}: {message}"
        log_data = data.copy() if data else {}
        log_data.update(map_context)
        
        if log_data:
            extra_msg += f" | Data: {json.dumps(log_data)}"
        self.quest_logger.info(extra_msg)
    
    def log_navigation_event(self, event_type: str, data: Optional[Dict[str, Any]] = None):
        """Log navigation-related events with coordinates and map context"""
        map_context = self.get_map_context()
        
        extra_msg = f"NAV_{event_type}"
        log_data = data.copy() if data else {}
        log_data.update(map_context)
        
        if log_data:
            extra_msg += f" | {json.dumps(log_data)}"
        self.nav_logger.info(extra_msg)
        
        # ALSO log to game logger for debugging
        self.game_logger.debug(f"NAVIGATION: {extra_msg}")
    
    def log_environment_event(self, event_type: str, data: Optional[Dict[str, Any]] = None):
        """Log environment-related events"""
        extra_msg = f"ENV_{event_type}"
        if data:
            extra_msg += f" | {json.dumps(data)}"
        self.env_logger.info(extra_msg)
        
        # ALSO log to game logger for debugging
        self.game_logger.debug(f"ENVIRONMENT: {extra_msg}")
    
    def log_trigger_event(self, trigger_id: str, status: str, values: str = "", debug_info: str = ""):
        """Log trigger evaluation events"""
        extra_msg = f"[TRIGGER] {trigger_id} -> {status}"
        if values:
            extra_msg += f" | {values}"
        if debug_info:
            extra_msg += f" | {debug_info}"
        self.quest_logger.info(extra_msg)
    
    def log_error(self, component: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Log errors with component context"""
        extra_msg = f"{component}: ERROR - {message}"
        if data:
            extra_msg += f" | Data: {json.dumps(data)}"
        self.error_logger.error(extra_msg)
    
    def log_performance(self, metric: str, value: Union[float, int], unit: str = "", context: Optional[Dict[str, Any]] = None):
        """Log performance metrics"""
        extra_msg = f"PERF_{metric}: {value}"
        if unit:
            extra_msg += f" {unit}"
        if context:
            extra_msg += f" | Context: {json.dumps(context)}"
        self.perf_logger.info(extra_msg)
    
    def log_state_change(self, component: str, old_state: str, new_state: str, data: Optional[Dict[str, Any]] = None):
        """Log state transitions"""
        extra_msg = f"{component}: {old_state} -> {new_state}"
        if data:
            extra_msg += f" | Data: {json.dumps(data)}"
        self.env_logger.info(extra_msg)
    
    def log_debug(self, component: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Log debug information with structured data"""
        # Use structured logging for debug
        extra_data = {'component': component}
        if data:
            extra_data.update(data)
        
        # Create a LogRecord with extra data
        record = logging.LogRecord(
            name=self.debug_logger.name,
            level=logging.DEBUG,
            pathname='',
            lineno=0,
            msg=message,
            args=(),
            exc_info=None
        )
        record.extra_data = extra_data
        
        self.debug_logger.handle(record)
    
    def log_system_event(self, message: str, data: Optional[Dict[str, Any]] = None):
        """Log system-level events (like initialization, shutdown, etc.)"""
        extra_msg = f"SYSTEM: {message}"
        if data:
            extra_msg += f" | Data: {json.dumps(data)}"
        self.game_logger.info(extra_msg)
    
    # Convenience methods for common logging patterns
    def info(self, message: str):
        """General info logging"""
        self.game_logger.info(message)
    
    def error(self, message: str):
        """General error logging"""
        self.error_logger.error(message)
    
    def debug(self, message: str):
        """General debug logging"""
        self.game_logger.debug(message)

# Global logger instance
_pokemon_logger: Optional[PokemonLogger] = None

def setup_logging(logs_dir: str = "logs", max_lines: int = 10000, redirect_stdout: bool = False, overwrite_logs: bool = True) -> PokemonLogger:
    """
    Setup the Pokemon logging system
    
    Args:
        logs_dir: Directory to store log files
        max_lines: Maximum lines per log file before rotation (ignored if overwrite_logs=True)
        redirect_stdout: Whether to redirect stdout/stderr to loggers
        overwrite_logs: Whether to overwrite log files on each run (True) or use rotation (False)
    
    Returns:
        PokemonLogger instance
    """
    global _pokemon_logger
    
    # Convert relative path to absolute based on current working directory
    logs_path = Path(logs_dir)
    if not logs_path.is_absolute():
        # Assume relative to the project root (grok_plays_pokemon directory)
        current_dir = Path.cwd()
        if current_dir.name != "grok_plays_pokemon":
            # Try to find the project root
            project_root = current_dir
            while project_root.parent != project_root and not (project_root / "grok_plays_pokemon").exists():
                project_root = project_root.parent
            
            if (project_root / "grok_plays_pokemon").exists():
                logs_path = project_root / "grok_plays_pokemon" / logs_dir
            else:
                logs_path = current_dir / logs_dir
        else:
            logs_path = current_dir / logs_dir
    
    _pokemon_logger = PokemonLogger(logs_path, max_lines, overwrite_logs)
    
    if redirect_stdout:
        _pokemon_logger.redirect_stdout_stderr()
    
    # Log the initialization
    _pokemon_logger.info(f"\n\nPokemon logging system initialized - Log directory: {logs_path}")
    if overwrite_logs:
        _pokemon_logger.info("Log mode: Overwrite files on each run")
    else:
        _pokemon_logger.info(f"Log mode: Rotation every {max_lines} lines")
    
    return _pokemon_logger

def get_pokemon_logger() -> Optional[PokemonLogger]:
    """
    Return the global Pokemon logger if it has been initialized; otherwise return None.
    Note: Logging must be explicitly set up via setup_logging() to enable file logging.
    """
    global _pokemon_logger
    return _pokemon_logger  # Do not auto-initialize; avoid redirecting stdout by default

def close_logging():
    """
    Close the logging system and restore stdout/stderr
    """
    global _pokemon_logger
    if _pokemon_logger:
        _pokemon_logger.restore_stdout_stderr()
        _pokemon_logger = None

# Context manager for temporary logging
class TemporaryLogging:
    """Context manager for temporary logging setup"""
    
    def __init__(self, logs_dir: str = "logs", max_lines: int = 10000, redirect_stdout: bool = True):
        self.logs_dir = logs_dir
        self.max_lines = max_lines
        self.redirect_stdout = redirect_stdout
        self.logger = None
    
    def __enter__(self) -> PokemonLogger:
        self.logger = setup_logging(self.logs_dir, self.max_lines, self.redirect_stdout)
        return self.logger
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        close_logging()

# Example usage and testing
if __name__ == "__main__":
    # Test the logging system
    with TemporaryLogging("test_logs", 100) as logger:
        logger.info("Testing Pokemon logging system")
        logger.log_quest_event("001", "Quest started", {"position": [100, 200]})
        logger.log_navigation_event("MAP_CHANGE", {"from": 38, "to": 0})
        logger.log_error("PATHFINDING", "Failed to find path", {"target": [150, 300]})
        logger.log_performance("FPS", 60.5, "fps", {"scene": "battle"})
        logger.log_debug("TEST", "Debug message", {"test_data": [1, 2, 3]})
        
        # Test print redirection
        print("This should go to game.log")
        print("Another line for game.log")
        
        # Test line rotation (if we had 100+ lines)
        for i in range(105):
            logger.info(f"Test line {i}")
    
    print("Logging test completed - check test_logs directory") 