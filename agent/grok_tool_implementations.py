# grok_plays_pokemon/agent/grok_tool_implementations.py
from pydantic import BaseModel, Field, ValidationError
from typing import List, Literal, Optional, Dict, Any, Tuple

import pygame

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

# ----------------------------- NEW TOOL -----------------------------
class EnterNameRequest(BaseModel):
    name: str = Field(description="Exact name to enter (max 10 characters, letters/numbers only)")
    target: Optional[str] = Field(default="player", description="Either 'player' or 'rival'")

# Generate JSON schema for EnterNameRequest
enter_name_schema = EnterNameRequest.model_json_schema()

def _move_cursor(env: RedGymEnv, from_pos: Tuple[int,int], to_pos: Tuple[int,int]):
    """Helper: move naming-screen cursor using D-Pad presses."""
    row_from, col_from = from_pos
    row_to, col_to = to_pos
    # Vertical moves first
    vert = row_to - row_from
    key = WindowEvent.PRESS_ARROW_DOWN if vert > 0 else WindowEvent.PRESS_ARROW_UP
    for _ in range(abs(vert)):
        env.pyboy.send_input(key)
        env.pyboy.tick(2)
        env.pyboy.send_input(WindowEvent.RELEASE_ARROW_DOWN if vert>0 else WindowEvent.RELEASE_ARROW_UP)
        env.pyboy.tick(2)
    # Horizontal moves
    horiz = col_to - col_from
    key = WindowEvent.PRESS_ARROW_RIGHT if horiz > 0 else WindowEvent.PRESS_ARROW_LEFT
    for _ in range(abs(horiz)):
        env.pyboy.send_input(key)
        env.pyboy.tick(2)
        env.pyboy.send_input(WindowEvent.RELEASE_ARROW_RIGHT if horiz>0 else WindowEvent.RELEASE_ARROW_LEFT)
        env.pyboy.tick(2)

def _letter_pos(ch: str) -> Tuple[int,int]:
    idx = ord(ch) - ord('A')
    col = idx % 9   # X axis
    row = idx // 9  # Y axis (0-based)
    return (row, col)

def enter_name(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper,
    name: str,
    target: str = "player"
) -> Tuple[str, Dict[str, Any]]:
    """Automatically enter a name on the naming screen by moving the cursor and pressing A for each letter, then START at the end."""
    name = name.strip().upper()[:10]
    logger.info(f"Entering {target} name: {name}")

    cur_pos: Tuple[int,int] = (0,0)  # (row,col) – we start on 'A'
    LETTER_PRESS_WAIT = 9
    LETTER_RELEASE_WAIT = 15
    for ch in name:
        if not ('A' <= ch <= 'Z'):
            continue  # ignore unsupported chars
        target_pos = _letter_pos(ch)
        _move_cursor(env, cur_pos, target_pos)
        cur_pos = target_pos

        # Select the letter
        env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
        env.pyboy.tick(LETTER_PRESS_WAIT)
        env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
        env.pyboy.tick(LETTER_RELEASE_WAIT)

    # Confirm name with START, wait ~3 seconds, then press A to proceed
    env.pyboy.send_input(WindowEvent.PRESS_BUTTON_START)
    env.pyboy.tick(LETTER_PRESS_WAIT)
    env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_START)
    env.pyboy.tick(180)  # ~3 s at 60 fps (most configs)

    env.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
    env.pyboy.tick(LETTER_PRESS_WAIT)
    env.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
    env.pyboy.tick(LETTER_RELEASE_WAIT)

    summary = f"Entered {target} name '{name}' and confirmed."
    return summary, {"status":"success","name":name,"target":target}

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
            success, move_message = navigator.move_in_direction(direction, steps=4)
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

    # --- New simple "friend" logic ---
    # If the question is about naming, return a fun, short name suggestion.
    suggestion = None
    try:
        import os
        from openai import OpenAI  # Lazy import only when ask_friend is called

        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("No XAI_API_KEY or OPENAI_API_KEY env var set for ask_friend")

        client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

        friend_system_prompt = (
            "You are Grok's sardonic, jaded friend Gork. Answer the user's question in a short, concise way. "
            """If the question asks for a name, it's your idiotic alter ego Grok trying to name his character in pokemon, 
            name his rival, or name a pokemon he got. Reply with a name up to 7 characters using  
            A B C D E F G H I
J K L M N O P Q R
S T U V W X Y Z
× ( ) : ; [ ] Pk Mn
- ? ! ♂ ♀ / . ,"""
        )
        messages = [
            {"role": "system", "content": friend_system_prompt},
            {"role": "user", "content": question}
        ]

        completion = client.chat.completions.create(
            model="grok-3-mini",
            messages=messages,
            max_tokens=2500,
            temperature=0.8
        )

        suggestion = completion.choices[0].message.content.strip()
        # Only keep first word for naming scenarios
        if " " in suggestion:
            suggestion = suggestion.split()[0]

        # Detailed logging: prompt visible to second Grok (friend), to aid debugging
        try:
            import json as _json
            _debug_logger = logging.getLogger('agent_file_logger')
            _debug_logger.info(f"ASK_FRIEND_PROMPT: {_json.dumps(messages, ensure_ascii=False, indent=2)}")
        except Exception:
            pass

        # After receiving completion (suggestion assignment)
        try:
            _debug_logger = logging.getLogger('agent_file_logger')
            reasoning_trace = getattr(completion.choices[0].message, 'reasoning_content', None)
            if reasoning_trace:
                _debug_logger.info(f"ASK_FRIEND_THINKING: {reasoning_trace}")
            _debug_logger.info(f"ASK_FRIEND_RESPONSE: {suggestion}")
        except Exception:
            pass

    except Exception as e_friend:
        logger.warning(f"ask_friend secondary LLM call failed: {e_friend}")
        suggestion = None
    
    # --- Structured output request for name suggestions ---
    if suggestion is None and "name" in question.lower():
        logger.info("ask_friend: attempting structured-output name suggestion")
        try:
            import json

            # JSON-Schema for a simple name payload
            name_schema = {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Suggested player or rival name (1-10 alphanumeric characters)"
                    }
                },
                "required": ["name"],
            }

            completion2 = client.chat.completions.create(
                model="grok-3-mini",
                messages=messages,
                response_format={"type": "json_schema", "schema": name_schema},
                max_tokens=2500,
                temperature=0.7,
                reasoning_effort="high",
            )

            # The model is guaranteed to reply with a JSON dict per schema
            raw_json = completion2.choices[0].message.content
            logger.debug(f"ask_friend structured output raw: {raw_json}")

            data = json.loads(raw_json)
            suggestion = str(data.get("name", "")).strip()

            # Basic validation
            if not suggestion or len(suggestion) > 10 or not suggestion.isalnum():
                raise ValueError(f"Invalid name returned: '{suggestion}'")

        except Exception as e_struct:
            logger.error(f"ask_friend structured-output request failed: {e_struct}", exc_info=True)
            suggestion = None

    # After both attempts, if we *still* don't have a suggestion, treat that as a hard error
    if suggestion is None:
        error_msg = "ask_friend: failed to obtain name suggestion from Grok (both standard and structured output calls failed)"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # If we reach here, suggestion is guaranteed valid.

    if suggestion:
        human_summary = (
            f"Question for friend: '{question}'. Suggested answer: {suggestion}."
        )
    else:
        human_summary = (
            f"Question for friend: '{question}' (Current location: {current_location})."
        )

    structured_output: Dict[str, Any] = {
        "status": "success",
        "question_asked": question,
        "context_provided": {"location": current_location}
    }
    if suggestion:
        structured_output["suggested_name"] = suggestion

    return human_summary, structured_output

def handle_battle(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper
) -> Tuple[str, Dict[str, Any]]:
    """
    Automatically handle a battle by selecting the best move.
    """
    # SKIP battle tool for Nidoran capture quest to allow StageManager scripted catch
    if hasattr(env, 'quest_manager') and getattr(env.quest_manager, 'current_quest_id', None) == 23:
        logger.info("Skipping handle_battle for quest 23 (Nidoran capture); using StageManager scripted catch")
        # Simulate pressing START (ENTER) multiple times to advance scripted catch
        for _ in range(12):
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a))
            pygame.event.post(pygame.event.Event(pygame.KEYUP,   key=pygame.K_a))
            time.sleep(0.2)
        return "Skipped battle handling for Nidoran capture", {"status": "skipped"}
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

def follow_nav_path(
    env: RedGymEnv,
    quest_manager: QuestManager,
    navigator: InteractiveNavigator,
    env_wrapper: EnvWrapper,
) -> Tuple[str, Dict[str, Any]]:
    """Trigger the environment's built-in path-following action.

    This is equivalent to the player pressing the physical '5' key which maps
    to the special PATH_FOLLOW_ACTION (discrete action index 6).  It does *not*
    attempt to choose a direction or coordinate – it simply enqueues the
    standard path-follow action and lets the environment / StageManager decide
    the exact movement.
    """

    try:
        from environment.environment import PATH_FOLLOW_ACTION

        # ------------------------------------------------------------------
        # 1️⃣  Simulate a *real* keyboard press of the "5" key so the main
        #     pygame event-loop inside play.py treats it exactly like a human
        #     pressing the 5-key.  This guarantees we reuse all the quest-
        #     loading / snapping logic already implemented for the manual key.
        # ------------------------------------------------------------------
        try:
            import pygame  # Local import to avoid forcing pygame dependency when unused

            if pygame.get_init():
                # Post both KEYDOWN and KEYUP so the repeat logic in play.py
                # mirrors a quick tap.
                pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_5))
                pygame.event.post(pygame.event.Event(pygame.KEYUP,   key=pygame.K_5))
        except Exception as _e:
            # If pygame isn't available (headless or during unit tests), fall
            # back to the direct environment action below.
            pass

        # ------------------------------------------------------------------
        # 2️⃣  Still submit the direct PATH_FOLLOW_ACTION to the environment so
        #     non-interactive/headless runs continue to work.
        # ------------------------------------------------------------------

        env.process_action(PATH_FOLLOW_ACTION, source="follow_nav_path_tool")  # type: ignore[arg-type]

        human_summary = "Triggered path-follow action (keyboard '5')."
        structured_output = {"status": "success", "action": PATH_FOLLOW_ACTION}
        return human_summary, structured_output

    except Exception as e:
        logger.error(f"Error in follow_nav_path: {e}", exc_info=True)
        error_msg = f"Exception during follow_nav_path: {str(e)}"
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
        "name": "exit_menu",
        "type": "function",
        "description": "Exit any active menu, dialog, or battle sequence by pressing B repeatedly. Use this when stuck in menus or dialog sequences.",
        "input_schema": empty_schema,
    },
    {
        "name": "ask_friend",
        "type": "function",
        "description": "Ask an unaffiliated helper Grok agent for high-level advice or stuck situations.",
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
    },
    {
        "name": "enter_name",
        "type": "function",
        "description": "Automatically enter a provided name on naming screen.",
        "input_schema": enter_name_schema,
    },
    {
        "name": "follow_nav_path",
        "function": follow_nav_path,
        "declaration": {
            "name": "follow_nav_path",
            "description": "Advance along the current quest navigation path by issuing the PATH_FOLLOW_ACTION (key '5').",
            "parameters": empty_schema
        }
    },
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
        "declaration": {
            "name": "handle_battle",
            "description": "Fully handles battling.",
            "parameters": empty_schema
        }
    },
    {
        "name": "enter_name",
        "function": enter_name,
        "declaration": {
            "name": "enter_name",
            "description": "Enter a provided name on the character-naming screen automatically (player or rival).",
            "parameters": enter_name_schema
        }
    },
    {
        "name": "follow_nav_path",
        "function": follow_nav_path,
        "declaration": {
            "name": "follow_nav_path",
            "description": "Advance along the quest path by one step (equivalent to pressing the '5' key).",
            "parameters": empty_schema
        }
    },
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