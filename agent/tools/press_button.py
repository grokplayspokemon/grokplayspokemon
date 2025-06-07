"""
Tool definition: press_button

This tool instructs the emulator to press a single game button.
Valid buttons: "UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START".

Expected LLM output format when invoking this tool:
  {
    "name": "press_button",
    "arguments": {
      "button": "<one of the valid button strings>"
    }
  }
- The `button` argument is required and must match one of the enum values.
- No additional fields are permitted.

When invoked, GrokAgent will map the `button` string to the corresponding emulator action
via ACTION_MAPPING_PYGAME_TO_INT and call:
      env.process_action(mapped_action, source="PressButtonTool")
in the play loop, causing the emulator to send the press and release events for that button.
"""

# Tool JSON schema for function calling with the model
press_button_tool = {
    # Indicates this is a callable function for the model
    "type": "function",
    # Function definition following the xAI spec
    "function": {
        "name": "press_button",
        "description": "Press an emulator button (UP, DOWN, LEFT, RIGHT, A, B, START).",
        "parameters": {
            "type": "object",
            "properties": {
                "button": {
                    "type": "string",
                    "enum": ["UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START"],
                    "description": "Button to press"
                }
            },
            "required": ["button"]
        }
    }
} 