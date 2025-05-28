#!/usr/bin/env python3
import os
import sys
import json

# Ensure local modules are importable (tools/, agent_logging/)
sys.path.insert(0, os.path.dirname(__file__))

from grok_agent import GrokAgent
from agent_logging.logger import get_logger

def main():
    # Set up logger (writes to agent.log)
    logger = get_logger(__name__)
    logger.info("Starting GrokAgent basic functionality test")

    # Initialize agent with dummy environment/navigator/quest_manager
    agent = GrokAgent()
    agent.initialize(None, None, None)

    # Define a minimal dummy game state
    dummy_state = {
        "coords": [0, 0, 0],
        "global_coords": [0, 0],
        "current_quest": None,
        "quest_status": {},
        "screen": []
    }
    logger.info("Dummy state: %s", dummy_state)

    # Invoke get_action and capture result
    try:
        action = agent.get_action(dummy_state)
        logger.info("Action returned: %s", action)
        print("Action returned:", action)
    except Exception as e:
        logger.exception("Error during get_action")
        print("Error during get_action:", e)

if __name__ == '__main__':
    main() 