# grok_plays_pokemon/agent/grok_tool_implementations.py
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any, Tuple

# Import environment components for type hinting and use in tools
from environment.wrappers.env_wrapper import EnvWrapper
from environment.environment import RedGymEnv
from environment.environment_helpers.quest_manager import QuestManager
from environment.environment_helpers.navigator import InteractiveNavigator
from pyboy.utils import WindowEvent # For button presses
import logging
import json # For serializing dicts if necessary for human-readable part
import time

logger = logging.getLogger(__name__)

# Pydantic models for each tool input schema
class PressButtonsRequest(BaseModel):
    buttons: List[Literal["a", "b", "start", "select", "up", "down", "left", "right"]] = Field(
        description="List of buttons to press in sequence. Valid buttons: 'a', 'b', 'start', 'select', 'up', 'down', 'left', 'right'"
    )
    wait_frames: Optional[int] = Field(
        default=5, # Number of frames to wait after each button press
        description="Number of frames to wait after pressing each button. Defaults to 5."
    )

class NavigateToRequest(BaseModel):
    target_y: Optional[int] = Field(
        default=None,
        description="The target Y coordinate (global or local based on navigator's current mode) to navigate to."
    )
    target_x: Optional[int] = Field(
        default=None,
        description="The target X coordinate (global or local based on navigator's current mode) to navigate to."
    )
    direction: Optional[Literal["n", "e", "s", "w", "up", "down", "left", "right"]] = Field(
        default=None,
        description="Cardinal direction to move a short distance (e.g., 1-4 spaces): one of n, e, s, w or up, down, left, right."
    )
    max_steps: Optional[int] = Field(
        default=100, description="Maximum steps for navigation attempt."
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
    question: str = Field(description="Question to ask an unaffiliated helper Grok agent for high-level advice or stuck situations.")
ask_friend_schema = AskFriendRequest.model_json_schema()


# Optional: Define the actual function implementations
def press_buttons(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper,
    buttons: List[Literal["a", "b", "start", "select", "up", "down", "left", "right"]],
    wait_frames: Optional[int] = 5
) -> Tuple[str, Dict[str, Any]]:
    assert env is not None, "RedGymEnv (env) not provided to press_buttons"
    assert buttons, "Button list cannot be empty for press_buttons"
    assert wait_frames >= 0, "wait_frames cannot be negative"

    logger.info(f"Executing press_buttons: {buttons}, wait_frames: {wait_frames}")
    button_map = {
        "up": WindowEvent.PRESS_ARROW_UP, "down": WindowEvent.PRESS_ARROW_DOWN,
        "left": WindowEvent.PRESS_ARROW_LEFT, "right": WindowEvent.PRESS_ARROW_RIGHT,
        "a": WindowEvent.PRESS_BUTTON_A, "b": WindowEvent.PRESS_BUTTON_B,
        "start": WindowEvent.PRESS_BUTTON_START,
    }
    release_map = {
        "up": WindowEvent.RELEASE_ARROW_UP, "down": WindowEvent.RELEASE_ARROW_DOWN,
        "left": WindowEvent.RELEASE_ARROW_LEFT, "right": WindowEvent.RELEASE_ARROW_RIGHT,
        "a": WindowEvent.RELEASE_BUTTON_A, "b": WindowEvent.RELEASE_BUTTON_B,
        "start": WindowEvent.RELEASE_BUTTON_START,
    }

    pressed_sequence = []
    try:
        # Prioritize env.pyboy if available
        pyboy_instance = env.pyboy
        if not pyboy_instance and env_wrapper:
            pyboy_instance = env_wrapper.pyboy # Fallback to env_wrapper if env doesn't expose it directly (it should)
        
        if not pyboy_instance:
            error_msg = "PyBoy instance not available via env or env_wrapper."
            logger.error(error_msg)
            return error_msg, {"status": "error", "message": error_msg}

        for button_name in buttons:
            pyboy_button = button_map.get(button_name.lower())
            pyboy_release = release_map.get(button_name.lower())
            if pyboy_button and pyboy_release:
                pyboy_instance.send_input(pyboy_button)
                for _ in range(max(1, wait_frames // 2)): # Hold for at least 1 frame
                    pyboy_instance.tick()
                pyboy_instance.send_input(pyboy_release)
                for _ in range(max(1, wait_frames // 2)): # Wait after release
                    pyboy_instance.tick()
                pressed_sequence.append(button_name)
            else:
                error_msg = f"Invalid button: {button_name}"
                logger.warning(error_msg)
                # Return on first error, or collect errors? For now, return on first.
                return f"Error: {error_msg}", {"status": "error", "message": error_msg, "buttons_attempted": pressed_sequence}
        
        human_summary = f"Pressed: {', '.join(pressed_sequence)}."
        structured_output = {"status": "success", "buttons_pressed": pressed_sequence, "wait_frames_each": wait_frames}
        return human_summary, structured_output
    except Exception as e:
        logger.error(f"Error in press_buttons: {e}", exc_info=True)
        error_msg = f"Exception during press_buttons: {str(e)}"
        return error_msg, {"status": "error", "message": error_msg, "buttons_attempted": pressed_sequence}

def navigate_to(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper,
    target_y: Optional[int] = None,
    target_x: Optional[int] = None,
    direction: Optional[Literal["n", "e", "s", "w", "up", "down", "left", "right"]] = None,
    max_steps: Optional[int] = 100
) -> Tuple[str, Dict[str, Any]]:
    assert env is not None, "RedGymEnv (env) not provided to navigate_to"
    assert navigator is not None, "InteractiveNavigator not provided to navigate_to"
    assert max_steps > 0, "max_steps must be positive"
    assert (target_y is not None and target_x is not None) or direction is not None, "Either (target_y, target_x) or direction must be specified for navigate_to"

    logger.info(f"Executing navigate_to: y={target_y}, x={target_x}, dir={direction}, max_steps={max_steps}")
    
    try:
        if target_y is not None and target_x is not None:
            # Assuming navigator.navigate_to_coords handles global/local based on its internal state or a convention
            path_found, message = navigator.navigate_to_coords(target_y, target_x, max_steps=max_steps)
            if path_found:
                human_summary = f"Navigation to ({target_y},{target_x}): {message}"
                structured_output = {"status": "success", "message": human_summary, "target_coords": {"y": target_y, "x": target_x}}
                return human_summary, structured_output
            else:
                human_summary = f"Navigation to ({target_y},{target_x}) failed: {message}"
                structured_output = {"status": "error", "message": human_summary, "target_coords": {"y": target_y, "x": target_x}}
                return human_summary, structured_output
        elif direction:
            # This part requires navigator to have a method like `move_in_direction`
            # For now, we can simulate with button presses if InteractiveNavigator doesn't have it directly
            # Or expect InteractiveNavigator to have a simple directional move capability.
            success, move_message = navigator.move_in_direction(direction, steps=4) # Assuming steps=4 as per previous description
            if success:
                human_summary = f"Moved towards {direction}: {move_message}"
                structured_output = {"status": "success", "message": human_summary, "direction": direction}
                return human_summary, structured_output
            else:
                human_summary = f"Move towards {direction} failed: {move_message}"
                structured_output = {"status": "error", "message": human_summary, "direction": direction}
                return human_summary, structured_output
        else:
            # This case should be caught by the assertion, but as a fallback:
            err_msg = "Invalid navigate_to parameters: neither coordinates nor direction provided."
            return err_msg, {"status": "error", "message": err_msg}
            
    except Exception as e:
        logger.error(f"Error in navigate_to: {e}", exc_info=True)
        error_msg = f"Exception during navigate_to: {str(e)}"
        return error_msg, {"status": "error", "message": error_msg}

def exit_menu(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper
) -> Tuple[str, Dict[str, Any]]:
    assert env is not None, "RedGymEnv (env) not provided to exit_menu"
    logger.info("Executing exit_menu")
    try:
        # Using the press_buttons tool for consistency might be too much overhead here.
        # Direct pyboy interaction for a fixed sequence is fine.
        pyboy_instance = env.pyboy
        if not pyboy_instance and env_wrapper:
            pyboy_instance = env_wrapper.pyboy
        
        if not pyboy_instance:
            error_msg = "PyBoy instance not available via env or env_wrapper for exit_menu."
            logger.error(error_msg)
            return error_msg, {"status": "error", "message": error_msg}

        b_button = WindowEvent.PRESS_BUTTON_B
        b_release = WindowEvent.RELEASE_BUTTON_B
        press_count = 0
        for i in range(8): # Press 'b' up to 8 times
            pyboy_instance.send_input(b_button)
            for _f in range(3): pyboy_instance.tick()
            pyboy_instance.send_input(b_release)
            for _f in range(3): pyboy_instance.tick()
            press_count +=1
            # Potentially add a check here using `env` if we can determine if out of menu
            # current_dialog = env.get_current_dialog()
            # if not env.is_in_menu_prompt_dialog(): break # Fictional env method
        
        human_summary = f"Attempted to exit menu by pressing B {press_count} times."
        structured_output = {"status": "success", "message": human_summary, "b_presses": press_count}
        return human_summary, structured_output
    except Exception as e:
        logger.error(f"Error in exit_menu: {e}", exc_info=True)
        error_msg = f"Exception during exit_menu: {str(e)}"
        return error_msg, {"status": "error", "message": error_msg}

def ask_friend(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper,
    question: str
) -> Tuple[str, Dict[str, Any]]:
    assert env is not None, "RedGymEnv (env) not provided to ask_friend"
    assert question, "Question cannot be empty for ask_friend"
    logger.info(f"Executing ask_friend: {question}")
    
    current_location = "Unknown"
    try:
        _, _, map_id = env.get_game_coords()
        current_location = env.get_map_name_by_id(map_id)
    except Exception as e_loc:
        logger.warning(f"Could not get current location for ask_friend: {e_loc}")

    human_summary = f"Question for friend: '{question}' (Current location: {current_location}). Friend's response will be handled by the agent."
    # The structured output is what the 'tool' role message will contain.
    # For ask_friend, the 'result' is that the question is posed. The *answer* comes from the LLM in a subsequent turn.
    # So, the tool itself doesn't return the friend's answer.
    # This is a slight mismatch with typical tool patterns where the tool *provides* the data.
    # For now, let's simulate a passthrough acknowledgement.
    structured_output = {
        "status": "success", 
        "question_asked": question,
        "context_provided": {"location": current_location},
        "note": "The agent will process the friend's answer in a separate step."
    }
    return human_summary, structured_output

# def handle_battle(
#     env: RedGymEnv,
#     quest_manager: QuestManager,
#     navigator: InteractiveNavigator,
#     env_wrapper: EnvWrapper
# ) -> Tuple[str, Dict[str, Any]]:
#     """
#     Automatically handle a battle by selecting the best move.
#     """

#     env.pyboy.send_input(WindowEvent.PRESS_BUTTON_B)  
#     env.pyboy.tick(10)
#     env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_B)
#     env.pyboy.tick(10)
#     env.pyboy.send_input(WindowEvent.PRESS_BUTTON_UP)
#     env.pyboy.tick(10)
#     env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_UP)
#     env.pyboy.tick(10)
#     # 1. CAPTURE INITIAL STATE
#     dialog_before = env.get_active_dialog() or ""
#     status_text = dialog_before.strip().replace("\n", " ") if dialog_before else "(Battle dialog cleared)"
    
#     env.pyboy.tick(24)
#     time.sleep(0.5)

#     # 3. BATTLE MENU VERIFICATION
#     battle_menu_visible = "►FIGHT" in dialog_before
#     logger.info(f"[BattleAI] Battle menu state: {'Active' if battle_menu_visible else 'Not detected'}")
    
#         # Ensure battle menu is visible, clearing any initial dialogs
#     dialog = env.get_active_dialog() or ""
#     if "►FIGHT" not in dialog:
#         # Clear blocking dialog (e.g., 'running from a trainer battle') until the fight menu appears
#         for _ in range(6):
#             print("doing what o4-mini suggested..")
#             env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
#             env.pyboy.tick()
#             env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
#             env.pyboy.tick()
#             time.sleep(0.1)
#             dialog = env.get_active_dialog() or ""
#             if "►FIGHT" in dialog:
#                 break
#     print("done doing what o4-mini suggested..")
    
#     # Press B to exit unwanted dialogs
#     while not battle_menu_visible:
#         print(f"battle_menu_visible={battle_menu_visible}")
#         for _ in range(4):
#             env.pyboy.send_input(WindowEvent.PRESS_BUTTON_B)
#             env.pyboy.tick(10)
#             env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_B)
#             env.pyboy.tick(10)
#             env.pyboy.send_input(WindowEvent.PRESS_BUTTON_UP)
#             env.pyboy.tick(10)
#             env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_UP)
#             env.pyboy.tick(10)
#             env.pyboy.send_input(WindowEvent.PRESS_BUTTON_LEFT)
#             env.pyboy.tick(10)
#             env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_LEFT)
#             env.pyboy.tick(10)
#             time.sleep(0.5)
#             battle_menu_visible = "►FIGHT" in dialog_before
                
#     # Cursor should be on FIGHT menu now    
#     # Open fight menu
#     print("Opening fight menu...")
#     env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
#     env.pyboy.tick(10)
#     env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
#     env.pyboy.tick(10)
#     time.sleep(0.5)
    
#     # Determine best move
#     print("determining best move..")
#     try:
#         best_idx = env.choose_best_battle_move()
#     except Exception:
#         best_idx = 0
    
#     print(f"best_idx={best_idx}")
        
#     # Reset cursor to top
#     for _ in range(4):
#         print("resetting cursor to top...")
#         env.pyboy.send_input(WindowEvent.PRESS_ARROW_UP)
#         env.pyboy.tick(10)
#         env.pyboy.send_input(WindowEvent.RELEASE_ARROW_UP)
#         env.pyboy.tick(10)
#         time.sleep(0.2)
        
        
#     # Move cursor down to selected move
#     for _ in range(best_idx):
#         print("moving cursor down to selected move..")
#         env.pyboy.send_input(WindowEvent.PRESS_ARROW_DOWN)
#         env.pyboy.tick(10)
#         env.pyboy.send_input(WindowEvent.RELEASE_ARROW_DOWN)
#         env.pyboy.tick(10)
#         time.sleep(0.4)
        
#     # Select move
#     print("selecing move...")
#     env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
#     env.pyboy.tick(5)
#     env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
#     env.pyboy.tick(5)
#     time.sleep(0.4)
    
#     # Summary
#     human_summary = f"Selected battle move {move_name} index {best_idx}."
#     structured = {"status": "success", "move_index": best_idx}
#     return human_summary, structured

def handle_battle(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper
) -> Tuple[str, Dict[str, Any]]:
    """
    Automatically handle a battle by selecting the best move.
    """
    logger.info("Executing handle_battle tool")
    
    try:
        # Get current dialog to understand battle state
        dialog = env.get_active_dialog() or ""
        print(f"dialog={dialog}")
        
        # Clear any blocking dialogs to see what menu we're in
        attempts = 0
        while "FIGHT" not in dialog and attempts < 10:
            print("pressing B to advance dialog...dialog=", dialog)
            # Press B to advance dialog
            env.pyboy.send_input(WindowEvent.PRESS_BUTTON_B)
            env.pyboy.tick(9)
            env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_B)
            env.pyboy.tick(15)
            
            dialog = env.get_active_dialog() or ""
            attempts += 1

        # Move cursor to FIGHT option
        print("moving cursor to FIGHT option...dialog=", dialog)
        for _ in range(4):
            env.pyboy.send_input(WindowEvent.PRESS_ARROW_UP)
            env.pyboy.tick(9)
            env.pyboy.send_input(WindowEvent.RELEASE_ARROW_UP)
            env.pyboy.tick(15)
            time.sleep(0.5)
            env.pyboy.send_input(WindowEvent.PRESS_ARROW_LEFT)
            env.pyboy.tick(9)
            env.pyboy.send_input(WindowEvent.RELEASE_ARROW_LEFT)
            env.pyboy.tick(15)
            time.sleep(0.5)
        
        
        # Select FIGHT option
        print("selecting FIGHT option...dialog=", dialog)
        env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
        env.pyboy.tick(9)
        env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
        env.pyboy.tick(15)
        time.sleep(0.5)
        
        # Use the AI to choose best move
        try:
            best_move_idx = env.choose_best_battle_move()
        except Exception as e:
            logger.warning(f"Error choosing best move: {e}, defaulting to first move")
            best_move_idx = 0
        
        print(f"best_move_idx={best_move_idx}")
        
        # Navigate to the selected move
        # First go to top of move list
        print("going to top of move list...dialog=", dialog)
        for _ in range(4):
            env.pyboy.send_input(WindowEvent.PRESS_ARROW_UP)
            env.pyboy.tick(9)
            env.pyboy.send_input(WindowEvent.RELEASE_ARROW_UP)
            env.pyboy.tick(15)
        
        # Then go down to selected move
        print("going down to selected move...dialog=", dialog)
        for _ in range(best_move_idx):
            env.pyboy.send_input(WindowEvent.PRESS_ARROW_DOWN)
            env.pyboy.tick(9)
            env.pyboy.send_input(WindowEvent.RELEASE_ARROW_DOWN)
            env.pyboy.tick(15)
        
        # Select the move
        print("selecting the move...dialog=", dialog)
        env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
        env.pyboy.tick(9)
        env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
        env.pyboy.tick(15)
        
        # Get move name if possible
        try:
            party = env.read_party_pokemon()
            if party and best_move_idx < len(party[0].moves):
                move_name = party[0].moves[best_move_idx]
            else:
                move_name = f"Move {best_move_idx + 1}"
        except:
            move_name = f"Move {best_move_idx + 1}"
        
        human_summary = f"Selected {move_name} (slot {best_move_idx + 1}) in battle"
        structured_output = {
            "status": "success",
            "move_selected": move_name,
            "move_index": best_move_idx
        }
        
        return human_summary, structured_output
        
    except Exception as e:
        logger.error(f"Error in handle_battle: {e}", exc_info=True)
        error_msg = f"Battle handling failed: {str(e)}"
        return error_msg, {"status": "error", "message": error_msg}


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
    {
        "name": "handle_battle",
        "function": handle_battle,
        "declaration": {
            "name": "handle_battle",
            "description": "Fully handles battling.",
            "parameters": empty_schema
        }
    }
]


# Define the available tools LIST for SimpleAgent processing
# Each item's 'declaration' will be used for OpenAI's tool format.
AVAILABLE_TOOLS_LIST = [
    {
        "name": "press_buttons",
        "function": press_buttons,
        "declaration": {
            "name": "press_buttons",
            "description": "Press a sequence of buttons on the Game Boy emulator. Valid buttons: 'a', 'b', 'start', 'select', 'up', 'down', 'left', 'right'.",
            "parameters": press_buttons_schema
        }
    },
    {
        "name": "navigate_to",
        "function": navigate_to,
        "declaration": {
            "name": "navigate_to",
            "description": "Navigate to target (y,x) coordinates or move in a specified cardinal direction for a short distance.",
            "parameters": navigate_to_schema
        }
    },
    {
        "name": "exit_menu",
        "function": exit_menu,
        "declaration": {
            "name": "exit_menu",
            "description": "Attempt to exit any active menu, dialog, or battle sequence by pressing the B button repeatedly.",
            "parameters": empty_schema # No parameters for exit_menu
        }
    },
    {
        "name": "ask_friend",
        "function": ask_friend,
        "declaration": {
            "name": "ask_friend",
            "description": "Ask a conceptual 'friend' (another LLM instance or a pre-defined knowledge base) a question for high-level strategy, hints, or if stuck. The agent will handle getting the friend's actual response.",
            "parameters": ask_friend_schema
        }
    },
    {
        "name": "handle_battle",
        "function": handle_battle,
        "declartion": {
            "name": "handle_battle",
            "description": "Fully handles battling.",
            "parameters": empty_schema
        }
    }
]




# For SimpleAgent to quickly map name to function if needed, though it iterates AVAILABLE_TOOLS_LIST
# TOOLS_MAP = {tool["name"]: tool["function"] for tool in AVAILABLE_TOOLS_LIST}

# Convert to the dictionary format SimpleAgent might expect for its internal tool handling
# (especially for Google tools that need a list of declarations).
# Anthropic tools are often passed as a list of dicts similar to AVAILABLE_TOOLS_LIST.
# SimpleAgent's _tool_setup_for_provider method should handle this transformation.

# For direct use in SimpleAgent if it expects a dict:
# AVAILABLE_TOOLS_DICT = {tool["name"]: tool for tool in AVAILABLE_TOOLS_LIST}

# The SimpleAgent has been updated to process the AVAILABLE_TOOLS_LIST directly
# when constructing provider-specific tool configurations.