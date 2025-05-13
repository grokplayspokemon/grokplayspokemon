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
    glob_y: int = Field(
        description="The global Y coordinate to navigate to."
    )
    glob_x: int = Field(
        description="The global X coordinate to navigate to."
    )

# class EmptyRequest(BaseModel):
#     pass

# class HandleBattleRequest(BaseModel):
#     buttons: List[Literal["a", "b", "start", "select", "up", "down", "left", "right"]] = Field(
#         description="List of buttons to press in sequence. Valid buttons: 'a', 'b', 'start', 'select', 'up', 'down', 'left', 'right'"
#     )
#     wait: Optional[bool] = Field(
#         default=True,
#         description="Whether to wait for a brief period after pressing each button. Defaults to true."
#     )

# Generate JSON schemas from the Pydantic models
press_buttons_schema = PressButtonsRequest.model_json_schema()
navigate_to_schema = NavigateToRequest.model_json_schema()
# empty_schema = EmptyRequest.model_json_schema()
# handle_battle_schema = HandleBattleRequest.model_json_schema()

# Define the available tools using the generated schemas
AVAILABLE_TOOLS = [
    {
        "name": "press_buttons",
        "type": "function",
        "description": "Press a sequence of buttons on the Game Boy.",
        "input_schema": press_buttons_schema,
    },
    {
        "name": "navigate_to",
        "type": "function",
        "description": "Follow predefined navigation path bounds from nav.py; stays within path or returns to the nearest valid point if off path.",
        "input_schema": navigate_to_schema,
    },
    # {
    #     "name": "exit_to_last_map",
    #     "type": "function",
    #     "description": "Exit to previous map by reversing movement actions recorded in completed_steps.",
    #     "input_schema": empty_schema,
    # },
    # {
    #     "name": "exit_menu",
    #     "type": "function",
    #     "description": "Exit any active menu, dialog, or battle sequence by pressing B repeatedly. Use this when stuck in menus or dialog sequences.",
    #     "input_schema": empty_schema,
    # },
    # {
    #     "name": "handle_battle",
    #     "type": "function",
    #     "description": "Handle a battle situation by first attempting to exit any dialog (if it's just an NPC conversation), then if dialog persists (meaning it's a trainer battle), select 'Fight' and repeatedly use attacks. Use this whenever dialog cannot be exited with the exit_menu tool.",
    #     "input_schema": handle_battle_schema,
    # },
    # {
    #     "name": "check_bounds",
    #     "type": "function",
    #     "description": "Check if the agent is within the playable area bounds. If out of bounds, automatically attempts to return to a valid area.",
    #     "input_schema": empty_schema,
    # }
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

# def exit_to_last_map() -> Dict[str, Any]:
#     # Implementation here
#     return {"success": True}

# def exit_menu(emulator) -> Dict[str, Any]:
#     # Implementation here
#     for _ in range(10): # Press 'b' 10 times
#         emulator.press_buttons(["b"], True) # Press 'b' and wait for a brief period
#     return {"success": True, "message": "Attempted to exit menu by pressing B repeatedly."}

# def handle_battle(self, tool_call):
#     # Capture initial battle state
#     dialog_before = self.emulator.get_active_dialog() or ""
#     status_text = dialog_before.strip().replace("\n", " ") if dialog_before else "(Battle dialog cleared)"
    
#     # CRITICAL: Execute full emulator cycle before proceeding
#     self.emulator.step()
#     time.sleep(0.5)  # Stabilization delay
    
#     # Ensure we're at the main battle menu before proceeding
#     if "FIGHT" not in dialog_before:
#         # If not at FIGHT menu, press B to reset UI state
#         self.emulator.press_buttons(["b"], True)
#         self.emulator.step()
#         time.sleep(0.3)
    
#     # Select FIGHT with explicit state management
#     logger.info("[BattleAI] Selecting FIGHT menu option")
#     self.emulator.press_buttons(["a"], True)
#     self.emulator.step()  # Critical: Complete step execution
    
#     # Add verification delay for UI stabilization
#     time.sleep(0.5)

# def check_bounds() -> Dict[str, Any]:
#     # Implementation here
#     return {"success": True}