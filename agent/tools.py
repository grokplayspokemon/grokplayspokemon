# tools.py
AVAILABLE_TOOLS = [
    {
        "name": "press_buttons",
        "type": "function",
        "description": "Press a sequence of buttons on the Game Boy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "buttons": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["a", "b", "start", "select", "up", "down", "left", "right"]
                    },
                    "description": "List of buttons to press in sequence. Valid buttons: 'a', 'b', 'start', 'select', 'up', 'down', 'left', 'right'"
                },
                "wait": {
                    "type": "boolean",
                    "description": "Whether to wait for a brief period after pressing each button. Defaults to true."
                }
            },
            "required": ["buttons"],
        },
    },
    {
        "name": "navigate_to",
        "type": "function",
        "description": "Automatically navigate to a position on the map grid. The screen is divided into a (10, 9) (x, y) grid, with the top-left corner as (0, 0). You are always at (4, 4) using this scheme, and labeled (glob_r, glob_c, P, ?). The screen moves with you when you move.",
        "input_schema": {
            "type": "object",
            "properties": {
                "row": {
                    "type": "integer",
                    "description": "The row coordinate to navigate to (0-8). Scale is identical to the global coordinates."
                },
                "col": {
                    "type": "integer",
                    "description": "The column coordinate to navigate to (0-9). Scale is identical to the global coordinates."
                }
            },
            "required": ["row", "col"],
        },
    },
    {
        "name": "exit_to_last_map",
        "type": "function",
        "description": "Exit to previous map by reversing movement actions recorded in completed_steps.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "exit_menu",
        "type": "function",
        "description": "Exit any active menu, dialog, or battle sequence by pressing B repeatedly. Use this when stuck in menus or dialog sequences.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "handle_battle",
        "type": "function",
        "description": "Handle a battle situation by first attempting to exit any dialog (if it's just an NPC conversation), then if dialog persists (meaning it's a trainer battle), select 'Fight' and repeatedly use attacks. Use this whenever dialog cannot be exited with the exit_menu tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "buttons": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["a", "b", "start", "select", "up", "down", "left", "right"]
                    },
                    "description": "List of buttons to press in sequence. Valid buttons: 'a', 'b', 'start', 'select', 'up', 'down', 'left', 'right'"
                },
                "wait": {
                    "type": "boolean",
                    "description": "Whether to wait for a brief period after pressing each button. Defaults to true."
                }
            },
            "required": ["buttons"],
        },
    },
    {
        "name": "check_bounds",
        "type": "function",
        "description": "Check if the agent is within the playable area bounds. If out of bounds, automatically attempts to return to a valid area.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    }
]