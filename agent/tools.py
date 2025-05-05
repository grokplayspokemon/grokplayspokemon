from config import USE_NAVIGATOR, USE_OVERLAY

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
]

if USE_NAVIGATOR:
    AVAILABLE_TOOLS.append({
        "name": "navigate_to",
        "type": "function",
        "description": "Automatically navigate to a position on the map grid. The screen is divided into a (10, 9) (x, y) grid, with the top-left corner as (0, 0).",
        "input_schema": {
            "type": "object",
            "properties": {
                "row": {
                    "type": "integer",
                    "description": "The row coordinate to navigate to (0-8)."
                },
                "col": {
                    "type": "integer",
                    "description": "The column coordinate to navigate to (0-9)."
                }
            },
            "required": ["row", "col"],
        },
    })

if USE_OVERLAY:
    AVAILABLE_TOOLS.append({
        "name": "get_collision_map",
        "type": "function",
        "description": "Retrieve the ASCII collision map overlay showing walkable (0), walls/unwalkable (1), sprites (2), warps (D/W), and player direction codes (3-6).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        },
    })

# Tool for talking to all NPCs in the visible area
AVAILABLE_TOOLS.append({
    "name": "talk_to_npcs",
    "type": "function",
    "description": "Navigate to and talk to each NPC sprite on screen.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
    }
})

AVAILABLE_TOOLS.append({
    "name": "fetch_url",
    "type": "function",
    "description": "Fetch the content of a URL (e.g., a walkthrough link) and return its text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch."}
        },
        "required": ["url"]
    },
})

AVAILABLE_TOOLS.append({
    "name": "exit_to_last_map",
    "type": "function",
    "description": "Exit to previous map by reversing movement actions recorded in completed_steps.",
    "input_schema": {"type": "object", "properties": {}, "required": []}
})