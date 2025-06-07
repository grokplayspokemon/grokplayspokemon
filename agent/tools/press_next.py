"""
Tool definition: press_next

This tool instructs the game to advance the navigator along the current quest path
by exactly one step. It is functionally identical to pressing the '5' key on the keyboard,
which triggers the PATH_FOLLOW_ACTION in the environment.

Expected LLM output format when invoking this tool:
  {
    "name": "press_next",
    "arguments": {}
  }
- The `arguments` object must be empty.
- No additional fields are permitted.

When invoked, GrokAgent will map this tool call to:
      env.process_action(PATH_FOLLOW_ACTION, source="PressNextTool")
in the play loop, causing the agent to move a single step along the recorded path.
"""

# Tool JSON schema for function calling with the model
press_next_tool = {
    # Indicates this is a callable function for the model
    "type": "function",
    # Function definition following the xAI spec
    "function": {
        "name": "press_next",
        "description": "Advance along the quest path by one step (equivalent to pressing the '5' key).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
} 