from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any

# Pydantic models for each tool input schema
class PressButtonsRequest(BaseModel):
    buttons: List[Literal["a", "b", "start", "select", "up", "down", "left", "right"]] = Field(
        description="List of buttons to press in sequence. Valid buttons: 'a', 'b', 'start', 'select', 'up', 'down', 'left', 'right'"
    )
    wait: Optional[bool] = Field(
        default=True,
        description="Whether to wait for a brief period after pressing each button. Defaults to true."
    )

class NavigateToRequest(BaseModel):
    glob_y: Optional[int] = Field(
        default=None,
        description="The global Y coordinate to navigate to."
    )
    glob_x: Optional[int] = Field(
        default=None,
        description="The global X coordinate to navigate to."
    )
    direction: Optional[Literal["n", "e", "s", "w", "up", "down", "left", "right"]] = Field(
        default=None,
        description="Cardinal direction to move up to 4 spaces: one of n, e, s, w or up, down, left, right."
    )

# Define schema for tools that take no arguments
class EmptyRequest(BaseModel):
    """No input required for this tool."""
    pass
empty_schema = EmptyRequest.model_json_schema()
press_buttons_schema = PressButtonsRequest.model_json_schema()
navigate_to_schema = NavigateToRequest.model_json_schema()

# Add AskFriendRequest model and schema
class AskFriendRequest(BaseModel):
    question: str = Field(description="Question to ask an unaffiliated helper Grok agent.")
ask_friend_schema = AskFriendRequest.model_json_schema()

# Define the available tools using the generated schemas
AVAILABLE_TOOLS = [
    {
        "name": "press_buttons",
        "type": "function",
        "description": "Press a sequence of buttons on the Game Boy emulator.",
        "input_schema": press_buttons_schema,
    },
    {
        "name": "navigate_to",
        "type": "function",
        "description": "Move to a specific walkable coordinate by (glob_y, glob_x) or by direction (up to 4 spaces): direction parameter.",
        "input_schema": navigate_to_schema,
    },
    {
        "name": "exit_menu",
        "type": "function",
        "description": "Exit any active menu, dialog, or battle sequence by pressing B repeatedly. Use this when stuck in menus or dialog sequences.",
        "input_schema": empty_schema,
    },
    {
        "name": "ask_friend",
        "type": "function",
        "description": "Ask an unaffiliated helper Grok any question with game state data, where to go next, etc.",
        "input_schema": ask_friend_schema,
    },
]

# Optional: Define the actual function implementations
def press_buttons(**kwargs) -> Dict[str, Any]:
    request = PressButtonsRequest(**kwargs)
    # Implementation here
    return {"success": True, "buttons_pressed": request.buttons}

def navigate_to(**kwargs) -> Dict[str, Any]:
    request = NavigateToRequest(**kwargs)
    # Implementation here
    return {"success": True, "destination": {"glob_y": request.glob_y, "glob_x": request.glob_x}}

# Add stub ask_friend implementation
def ask_friend(**kwargs) -> Dict[str, Any]:
    request = AskFriendRequest(**kwargs)
    # Stub response: echo back the question for now
    return {"question": request.question}

def exit_menu(emulator) -> Dict[str, Any]:
    # Implementation here
    for _ in range(10): # Press 'b' 10 times
        emulator.press_buttons(["b"], True) # Press 'b' and wait for a brief period
    return {"success": True, "message": "Attempted to exit menu by pressing B repeatedly."}