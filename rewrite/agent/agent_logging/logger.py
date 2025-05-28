import logging
import os

def get_logger(name: str, log_file: str = None, level: int = logging.INFO):
    """
    Configure and return a logger that writes to a file in the agent_logging directory.
    """
    if log_file is None:
        log_file = os.path.join(os.path.dirname(__file__), 'agent.log')
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # Avoid adding multiple handlers to the logger
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger 