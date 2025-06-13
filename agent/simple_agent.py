# agent/simple_agent.py
"""
Simple Agent for Grok Integration
Provides autonomous Pokemon gameplay using Grok AI
"""

import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import asdict
import time
from agent.grok_tool_implementations import AVAILABLE_TOOLS_LIST, press_buttons, navigate_to, exit_menu, ask_friend, handle_battle, enter_name
from openai import OpenAI
from utils.logging_config import get_pokemon_logger, setup_logging, LineCountRotatingFileHandler
from pathlib import Path
from agent.prompts import SYSTEM_PROMPT, BATTLE_PROMPT, DIALOG_PROMPT

from environment.environment import VALID_ACTIONS, VALID_ACTIONS_STR, PATH_FOLLOW_ACTION
from environment.grok_integration import extract_structured_game_state, GameState
from environment.environment_helpers.quest_manager import QuestManager
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.wrappers.env_wrapper import EnvWrapper
from environment.environment import RedGymEnv
from pyboy.utils import WindowEvent

# Use the centralized PokemonLogger for agent logs
agent_logger = get_pokemon_logger()
if agent_logger is None:
    # Initialize Pokemon logging for the agent if not already set up
    agent_logger = setup_logging(redirect_stdout=False)

logger = logging.getLogger(__name__)

# Cost constants for grok-3-mini ($ per 131,072 tokens) 06/10/2025
COST_PER_INPUT_TOKEN = 0.30 / 131072
COST_PER_CACHED_INPUT_TOKEN = 0.075 / 131072
COST_PER_COMPLETION_TOKEN = 0.50 / 131072

class SimpleAgent:
    """Simple agent that uses Grok to make decisions about Pokemon gameplay"""
    
    def __init__(self, reader: RedGymEnv, quest_manager: QuestManager, 
                 navigator: InteractiveNavigator, env_wrapper: EnvWrapper,
                 xai_api_key: str, status_queue=None):
        self.reader = reader
        # Attach status queue for UI updates if provided
        self.status_queue = status_queue
        # Setup file-based logger for Grok agent events
        self.agent_file_logger = logging.getLogger('agent_file_logger')
        # Remove any legacy FileHandler to avoid unrotated logs
        for handler in list(self.agent_file_logger.handlers):
            if isinstance(handler, logging.FileHandler):
                self.agent_file_logger.removeHandler(handler)
        # Ensure a rotating handler for agent.log exists
        if not any(isinstance(h, LineCountRotatingFileHandler) and h.filename == Path('agent.log') for h in self.agent_file_logger.handlers):
            # Rotate agent.log every 10k lines
            fh = LineCountRotatingFileHandler('agent.log', max_lines=10000)
            fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            self.agent_file_logger.addHandler(fh)
            self.agent_file_logger.setLevel(logging.INFO)
        self.quest_manager = quest_manager
        self.navigator = navigator
        self.env_wrapper = env_wrapper
        
        # Initialize Grok client
        self.client = OpenAI(
            api_key=xai_api_key,
            base_url="https://api.x.ai/v1"
        )
        
        # Track last prompt and response for UI
        self.last_prompt = ""
        self.last_response = ""
        self.last_thinking = ""
        
        # Action history for context
        self.action_history = []
        self.max_history = 10
        
        # Cooldown to prevent API spam
        self.last_api_call = 0
        self.api_cooldown = 3.0  # seconds
        
        # Token and cost tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_reasoning_tokens = 0
        self.total_tokens = 0
        self.total_cached_tokens = 0
        self.total_cost = 0.0
        self.api_calls_count = 0
        # Load persistent token usage from file
        usage_file = Path("grok_usage.json")
        self._usage_file = usage_file
        try:
            if usage_file.exists():
                with open(usage_file, 'r') as f:
                    data = json.load(f)
                    self.total_prompt_tokens = data.get('total_prompt_tokens', self.total_prompt_tokens)
                    self.total_completion_tokens = data.get('total_completion_tokens', self.total_completion_tokens)
                    self.total_reasoning_tokens = data.get('total_reasoning_tokens', self.total_reasoning_tokens)
                    self.total_tokens = data.get('total_tokens', self.total_tokens)
                    self.total_cached_tokens = data.get('total_cached_tokens', self.total_cached_tokens)
                    self.total_cost = data.get('total_cost', self.total_cost)
                    self.api_calls_count = data.get('api_calls_count', self.api_calls_count)
        except Exception as e:
            self.agent_file_logger.error(f"Error loading token usage persistence: {e}")
        
        # Holds a pending name suggestion from ask_friend
        self._pending_name: Optional[str] = None
        
        self.tool_implementations = {
            "press_buttons": press_buttons,
            "navigate_to": navigate_to,
            "exit_menu": exit_menu,
            "ask_friend": ask_friend,
            "handle_battle": handle_battle,
            "enter_name": enter_name
        }
        
        # Track previous GameState to describe outcome of last action
        self._prev_state: Optional[GameState] = None
        
    def get_action(self, game_state: GameState) -> Optional[int]:
        """Get next action from Grok based on current game state"""
        
        # Log that Grok action is being requested with context
        agent_logger.log_system_event("GROK_GET_ACTION_START", {"location": game_state.location, "quest_id": game_state.quest_id})
        # Also log start event to agent.log
        self.agent_file_logger.info(f"GROK_GET_ACTION_START: {json.dumps({'location': game_state.location, 'quest_id': game_state.quest_id})}")
        
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_api_call < self.api_cooldown:
            agent_logger.info(f"Grok cooldown active: {self.api_cooldown - (current_time - self.last_api_call):.1f}s remaining")
            # Return None to let quest system take over
            return None
            
        try:
            # Build the prompt
            prompt = self._build_prompt(game_state)
            self.last_prompt = prompt
            # Log the exact prompt payload sent to Grok
            agent_logger.log_system_event("GROK_PROMPT_SENT", {"prompt": prompt})
            # Also log to agent.log
            self.agent_file_logger.info(f"GROK_PROMPT_SENT: {prompt}")
            
            # Call Grok API
            response = self._call_grok(prompt, game_state)
            agent_logger.log_system_event("GROK_API_RESPONSE", {"response": response})
            # Also log API response to agent.log
            self.agent_file_logger.info(f"GROK_API_RESPONSE: {response}")
            
            # Parse response to get action
            action = self._parse_response(response)
            
            # Record action in history
            if action is not None:
                self.action_history.append({
                    'action': action,
                    'location': game_state.location,
                    'time': current_time
                })
                # Trim history
                if len(self.action_history) > self.max_history:
                    self.action_history.pop(0)
            
            self.last_api_call = current_time
            # Log the final action chosen by Grok (including reasoning)
            agent_logger.log_system_event("GROK_GET_ACTION_RESULT", {"action": action, "response": self.last_response})
            # Also log final action to agent.log
            self.agent_file_logger.info(f"GROK_FINAL_ACTION: action={action} response={self.last_response}")
            # Store current state for next iteration diffing
            self._prev_state = game_state
            return action
            
        except Exception as e:
            agent_logger.log_error("GROK_GET_ACTION_ERROR", f"Error getting Grok action: {e}")
            self.last_response = f"Error: {str(e)}"
            return None
    
    def _build_prompt(self, game_state: GameState) -> str:
        """Build a comprehensive prompt for Grok.
        The prompt now contains:
          • concise current situational info
          • recent actions list
          • delta summary of last action outcome
          • special guidance when on name-entry screens that nudges Grok to
            call ask_friend and then enter the provided name via D-pad + A,
            finishing with START.
        """
        
        # Convert game state to dict for easier formatting
        state_dict = asdict(game_state)
        
        # Build context about current situation
        context_parts = []
        
        # Location context
        loc = game_state.location
        context_parts.append(f"You are at {loc['map_name']} (map {loc['map_id']}) at position ({loc['x']}, {loc['y']})")
        
        # Quest context
        if game_state.quest_id:
            context_parts.append(f"Current quest: {game_state.quest_id}")
            # Add quest description
            if self.quest_manager:
                quest_data = self.quest_manager.quest_progression_engine.get_quest_data_by_id(game_state.quest_id)
                if quest_data:
                    context_parts.append(f"Quest objective: {quest_data.get('begin_quest_text', 'Unknown')}")
        
        # Collision map (spatial representation); no collision map if in battle
        if game_state.collision_map and not (game_state.in_battle or game_state.dialog):
            # Determine rows: if it's a preformatted string, split on newlines; otherwise format nested lists
            if isinstance(game_state.collision_map, str):
                rows = game_state.collision_map.split("\n")
            else:
                rows = [" ".join(str(c) for c in row) for row in game_state.collision_map]
            # Indent each row for prompt readability
            indent_rows = "\n".join("  " + row for row in rows)
            collision_section = f"Collision map:\n{indent_rows}"
            context_parts.append(collision_section)
            # Log the collision map spatially to the agent log as a single line
            self.agent_file_logger.info("Collision map: " + ";".join(rows))
        
        # Party info
        if game_state.party:
            alive_pokemon = [p for p in game_state.party if p['hp'] > 0]
            context_parts.append(f"Party: {len(alive_pokemon)}/{len(game_state.party)} Pokemon alive")
            if alive_pokemon:
                lead = alive_pokemon[0]
                context_parts.append(f"Lead Pokemon: {lead['species']} Lv.{lead['level']} ({lead['hp']}/{lead['maxHp']} HP)")
        
        # Battle info
        if game_state.in_battle:
            # Use detailed battle prompt from environment if available
            if getattr(game_state, 'battle_prompt', None):
                context_parts.append(game_state.battle_prompt)
            else:
                context_parts.append("Currently in battle!")
        
        # Dialog info
        if game_state.dialog and not game_state.in_battle:
            context_parts.append(f"Dialog active: {game_state.dialog[:200]}...")

            # ------------------------------------------------------------------
            # SPECIAL CASE: Player-/Rival-naming screen
            # ------------------------------------------------------------------
            if "YOUR NAME?" in game_state.dialog.upper():
                context_parts.append(
                    "You are on the character-naming screen (player).  First, call ask_friend(question='What should we name the player?').  "
                    "After receiving the answer, call enter_name(name='ANSWER', target='player') to automatically type it and confirm."
                )
            if "RIVAL" in game_state.dialog.upper() and "NAME" in game_state.dialog.upper():
                context_parts.append(
                    "You are on the rival-naming screen.  Call ask_friend(question='What should we name the rival?') then enter_name(name='ANSWER', target='rival')."
                )
        
        # Recent actions
        if self.action_history:
            recent = self.action_history[-3:]
            action_str = ", ".join([VALID_ACTIONS_STR[a['action']] for a in recent if a['action'] < len(VALID_ACTIONS_STR)])
            context_parts.append(f"Recent actions: {action_str}")
        
        # ------------------------------------------------------------------
        # Describe what happened as a result of the **last** action Grok took
        # by diffing the previous GameState (if any) against the current
        # state.  This keeps Grok informed without flooding tokens.
        # ------------------------------------------------------------------
        if self._prev_state is not None and self.action_history:
            last_act = self.action_history[-1]['action'] if self.action_history else None
            last_act_name = VALID_ACTIONS_STR[last_act] if last_act is not None and last_act < len(VALID_ACTIONS_STR) else str(last_act)

            deltas: list[str] = []

            # Location delta
            prev_loc = self._prev_state.location
            curr_loc = game_state.location
            if (prev_loc['map_id'], prev_loc['x'], prev_loc['y']) != (curr_loc['map_id'], curr_loc['x'], curr_loc['y']):
                moved_str = f"moved to ({curr_loc['x']},{curr_loc['y']}) on {curr_loc['map_name']} (map {curr_loc['map_id']})"
                deltas.append(moved_str)

            # Dialog delta
            prev_dialog = (self._prev_state.dialog or '').strip()
            curr_dialog = (game_state.dialog or '').strip()
            if curr_dialog and curr_dialog != prev_dialog:
                if not prev_dialog:
                    deltas.append("dialog opened")
                else:
                    deltas.append("dialog changed")
            elif prev_dialog and not curr_dialog:
                deltas.append("dialog closed")

            # Battle state changes
            if self._prev_state.in_battle != game_state.in_battle:
                if game_state.in_battle:
                    deltas.append("entered battle")
                else:
                    deltas.append("battle ended")

            # HP fraction significant drop
            if abs(self._prev_state.hp_fraction - game_state.hp_fraction) > 0.2:
                deltas.append("hp changed significantly")

            if deltas:
                outcome = "; ".join(deltas)
                context_parts.append(f"Last action ({last_act_name}) outcome: {outcome}.")
        
        # Conditional prompt for game start
        if game_state.location['map_id'] == 0 and loc['x'] == 0 and loc['y'] == 0 or game_state.location['map_id'] == 38 and loc['x'] == 3 and loc['y'] == 6 and game_state.dialog != "":
            prompt_start = "Make sure you pick entertaining names for yourself and your rival!"
        else:
            prompt_start = ""

        # Ensure tool availability note (helps Grok discover ask_friend)
        context_parts.append("Available helper tools: ask_friend, enter_name, press_buttons, navigate_to, exit_menu, handle_battle.")

        # If we already have a suggested name pending, remind Grok
        if self._pending_name and "YOUR NAME?" in (game_state.dialog or "").upper():
            context_parts.append(
                f"Friend suggested the name '{self._pending_name}'. Call enter_name(name='{self._pending_name}', target='player') to type it."
            )

        # Build the full prompt
        prompt = f"""{prompt_start}

Current situation:
{chr(10).join('- ' + part for part in context_parts)}

Game stats:
- Money: ${game_state.money}
- Badges: {game_state.badges}
- Pokedex: {game_state.pokedex_seen} seen, {game_state.pokedex_caught} caught
- Items: {', '.join(game_state.items[:5])}{'...' if len(game_state.items) > 5 else ''}

Available actions:
0: down
1: left  
2: right
3: up
4: a (interact/confirm)
5: b (cancel/back)
6: path to location (handles general navigational movement but does not position you at exactly the place you need to stand.)
7: start (menu)
When you are ready, call the correct tool per the instructions.

"""

        return prompt
    
    def _make_messages(self, game_state: GameState, prompt: str) -> List[Dict[str, str]]:
        """Construct the list of messages (system + conditional user prompt) based on game state"""
        messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if game_state.in_battle:
            # Use the detailed battle prompt if generated, otherwise fallback to static template
            if getattr(game_state, 'battle_prompt', None):
                messages.append({"role": "user", "content": game_state.battle_prompt})
            else:
                messages.append({"role": "user", "content": BATTLE_PROMPT})
        elif game_state.dialog:
            # Use specialized prompt for naming screens so Grok knows to ask for a name and call enter_name
            dlg_upper = game_state.dialog.upper()
            if ("YOUR NAME?" in dlg_upper) or ("RIVAL" in dlg_upper and "NAME" in dlg_upper):
                # Re-use the richer prompt containing naming instructions that we embedded in `prompt`
                # This prompt already contains the helpful directions: it appears in the `prompt` parameter we built above.
                messages.append({"role": "user", "content": prompt})
            else:
                messages.append({"role": "user", "content": DIALOG_PROMPT})
        else:
            messages.append({"role": "user", "content": prompt})
        return messages
    
    def _call_grok(self, prompt: str, game_state: GameState) -> str:
        """Call Grok API and automatically handle multi-turn tool-call chains.
        The function now loops until Grok either returns a choose_action tool-call
        or a normal (non-tool) answer.  For every intermediate tool-call we:
          1. Execute the tool locally
          2. Append a synthetic `role="tool"` message containing the structured
             output of that tool so Grok sees the result
          3. Re-issue the chat completion with the augmented message list
        This prevents the infinite ask_friend loop seen in the logs because
        Grok now gets the friend's answer before it decides the next step.
        In addition, we now:
          • Push Grok thinking / response strings to the status_queue so the web UI can display them.
          • Stream per-call and lifetime token/cost statistics to the UI via the '__grok_cost__' event.
        """

        # --------------------------------------------------
        # 0. Notify UI that Grok has started thinking
        # --------------------------------------------------
        if self.status_queue is not None:
            try:
                # Keep the first 250 characters of the prompt for display – it's usually enough context
                self.status_queue.put(("__grok_thinking__", prompt[0:1000]))
            except Exception:
                pass

        # 1. Build initial message list from game state
        messages = self._make_messages(game_state, prompt)

        # 2. Assemble the full tool list once – reused for every retry
        tools: list[dict] = [
            {
                "type": "function",
                "function": {
                    "name": "choose_action",
                    "description": "Choose the next action to take in the game",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "integer", "minimum": 0, "maximum": 7},
                            "reasoning": {"type": "string"}
                        },
                        "required": ["action", "reasoning"]
                    }
                }
            }
        ]
        for tool_info in AVAILABLE_TOOLS_LIST:
            if "declaration" in tool_info:
                tools.append({"type": "function", "function": tool_info["declaration"]})
        # Ensure 'handle_battle' is present exactly once
        if not any(
            t.get("function", {}).get("name") == "handle_battle" for t in tools
        ):
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "handle_battle",
                        "description": "Automatically handle battles by selecting the best move.",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            )

        # Helper that actually calls Grok with current messages
        def _chat_once(msgs: list[dict]):
            api_kwargs = {
                "model": "grok-3-mini",
                "messages": msgs,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 2500,
            }
            completion = self.client.chat.completions.create(**api_kwargs)
            self.api_calls_count += 1
            return completion.choices[0].message, completion.usage

        # 3. Main loop – max 5 chained tool-calls to prevent runaway loops
        import uuid
        for iteration in range(5):
            message, usage = _chat_once(messages)
            # DEBUG: log the returned message and any call info
            self.agent_file_logger.debug(f"GROK_LOOP {iteration}: tool_calls={getattr(message,'tool_calls',None)}, function_call={getattr(message,'function_call',None)}, content={getattr(message,'content',None)}")

            # --------------------------------------------------
            # 3a. Update token usage / cost stats and push to UI
            # --------------------------------------------------
            try:
                if usage is not None:
                    self.total_prompt_tokens += usage.prompt_tokens or 0
                    self.total_completion_tokens += usage.completion_tokens or 0
                    self.total_tokens += usage.total_tokens or 0
                    # Accurate cost calculation using Grok pricing (per-million tokens)
                    INPUT_COST_PER_TOKEN = 0.30 / 1_000_000      # $0.30 / 1M tokens
                    CACHED_INPUT_COST_PER_TOKEN = 0.07 / 1_000_000
                    OUTPUT_COST_PER_TOKEN = 0.50 / 1_000_000

                    prompt_tokens = usage.prompt_tokens or 0
                    completion_tokens = usage.completion_tokens or 0
                    cached_prompt_tokens = getattr(usage, 'cached_prompt_tokens', 0)
                    call_cost = (
                        prompt_tokens * INPUT_COST_PER_TOKEN +
                        cached_prompt_tokens * CACHED_INPUT_COST_PER_TOKEN +
                        completion_tokens * OUTPUT_COST_PER_TOKEN
                    )
                    self.total_cost += call_cost

                    # Persist lifetime stats
                    try:
                        with open(self._usage_file, 'w') as f:
                            json.dump({
                                'total_prompt_tokens': self.total_prompt_tokens,
                                'total_completion_tokens': self.total_completion_tokens,
                                'total_reasoning_tokens': self.total_reasoning_tokens,
                                'total_tokens': self.total_tokens,
                                'total_cached_tokens': self.total_cached_tokens,
                                'total_cost': self.total_cost,
                                'api_calls_count': self.api_calls_count
                            }, f)
                    except Exception:
                        pass

                    self.status_queue and self.status_queue.put(("__grok_cost__", {
                        "api_calls_count": self.api_calls_count,
                        "total_tokens": self.total_tokens,
                        "call_cost": call_cost,
                        "total_cost": self.total_cost
                    }))
            except Exception:
                pass

            # If Grok produced a tool call (tools API) or function_call (functions API), handle it
            tool_call = None
            if hasattr(message, 'tool_calls') and message.tool_calls:
                tool_call = message.tool_calls[0]
            elif hasattr(message, 'function_call') and message.function_call:
                # wrap function_call into a synthetic tool_call
                fc = message.function_call
                synthetic_id = str(uuid.uuid4())
                class SyntheticFunc:
                    pass
                class SyntheticCall:
                    pass
                func = SyntheticFunc()
                func.name = fc.name
                func.arguments = fc.arguments
                sc = SyntheticCall()
                sc.id = synthetic_id
                sc.type = 'function'
                sc.function = func
                tool_call = sc

            if tool_call:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments or "{}")
                except Exception:
                    tool_args = {}

                if tool_name == "choose_action":
                    # Debug log and return choose_action for parsing upstream
                    self.agent_file_logger.debug(f"GROK_TOOL choose_action args={tool_args}")
                    return json.dumps({"tool_call": tool_name, "arguments": tool_args})

                # Unknown tool – fail fast
                if tool_name not in self.tool_implementations:
                    self.agent_file_logger.error(f"GROK_UNKNOWN_TOOL: {tool_name}")
                    return json.dumps({"error": f"Unknown tool: {tool_name}"})

                # Execute the tool locally
                try:
                    tool_fn = self.tool_implementations[tool_name]
                    human_summary, structured_output = tool_fn(
                        env=self.reader,
                        quest_manager=self.quest_manager,
                        navigator=self.navigator,
                        env_wrapper=self.env_wrapper,
                        **tool_args  # Pass any additional arguments
                    )
                    
                    # Log tool execution
                    self.agent_file_logger.info(f"Tool {tool_name} executed: {human_summary}")
                    self.last_response = f"Tool {tool_name}: {human_summary}"
                    
                    # Post-processing for ask_friend / enter_name
                    if tool_name == "ask_friend":
                        # Capture suggested name if provided
                        suggested = structured_output.get("suggested_name") if isinstance(structured_output, dict) else None
                        if suggested:
                            self._pending_name = suggested
                    elif tool_name == "enter_name":
                        # Naming done; clear pending suggestion
                        self._pending_name = None
                    
                    # For handle_battle, we don't return an action since it handles the battle
                    # Return None to indicate no single action needed
                    if tool_name == "handle_battle":
                        return None
                    
                    # For navigation tools, might return a specific action
                    # For now, return None and let the next iteration decide
                    return None
                    
                except Exception as e:
                    logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                    self.last_response = f"Tool error: {str(e)}"
                    return None
            
            # Feed the tool result back to Grok in the exact schema Grok expects:
            #   1) Re-append the assistant message that contained the tool_call so
            #      the history shows the request Grok just made (OpenAI keeps
            #      it on the server but we need it client-side for the follow-up).
            #   2) Append a role="tool" message with *tool_call_id* referencing
            #      the original call id and the JSON payload.
            assistant_call_msg = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args)
                        }
                    }
                ]
            }
            messages.append(assistant_call_msg)

            # Tool response – MUST include the same id so Grok can match it
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_name,
                "content": json.dumps(structured_output if structured_output else {"result": human_summary})
            }
            messages.append(tool_msg)
            self.agent_file_logger.debug(f"GROK_TOOL_RESP appended: {tool_msg}")

            # Continue to next iteration which will call Grok again with augmented messages
            continue

        # Exhausted loop
        return json.dumps({"error": "Exceeded tool-call chain limit"})
    
    def _parse_response(self, response: str) -> Optional[int]:
        """Parse Grok's response to extract the action or execute tools"""
        # ---------------------------------------------
        # Push final response to UI for display
        # ---------------------------------------------
        if self.status_queue is not None:
            try:
                self.status_queue.put(("__grok_response__", response[:500]))
            except Exception:
                pass
        # Existing parsing logic follows
        try:
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"Grok error: {data['error']}")
                return None
            
            tool_name = data.get("tool_call")
            
            # Handle choose_action (existing behavior)
            if tool_name == "choose_action":
                args = data.get("arguments", {})
                action = args.get("action")
                reasoning = args.get("reasoning", "")
                
                # Update last response with reasoning
                self.last_response = f"Action {action}: {reasoning}"
                
                # Validate action
                if action is not None and 0 <= action < len(VALID_ACTIONS):
                    return action
                else:
                    logger.error(f"Invalid action from Grok: {action}")
                    return None
            
            # Handle other tool calls
            elif tool_name in self.tool_implementations:
                logger.info(f"Executing tool: {tool_name}")
                tool_fn = self.tool_implementations[tool_name]
                args = data.get("arguments", {})
                
                try:
                    # Execute the tool
                    human_summary, structured_output = tool_fn(
                        env=self.reader,
                        quest_manager=self.quest_manager,
                        navigator=self.navigator,
                        env_wrapper=self.env_wrapper,
                        **args  # Pass any additional arguments
                    )
                    
                    # Log tool execution
                    self.agent_file_logger.info(f"Tool {tool_name} executed: {human_summary}")
                    self.last_response = f"Tool {tool_name}: {human_summary}"
                    
                    # Post-processing for ask_friend / enter_name
                    if tool_name == "ask_friend":
                        # Capture suggested name if provided
                        suggested = structured_output.get("suggested_name") if isinstance(structured_output, dict) else None
                        if suggested:
                            self._pending_name = suggested
                    elif tool_name == "enter_name":
                        # Naming done; clear pending suggestion
                        self._pending_name = None
                    
                    # For handle_battle, we don't return an action since it handles the battle
                    # Return None to indicate no single action needed
                    if tool_name == "handle_battle":
                        return None
                    
                    # For navigation tools, might return a specific action
                    # For now, return None and let the next iteration decide
                    return None
                    
                except Exception as e:
                    logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                    self.last_response = f"Tool error: {str(e)}"
                    return None
            
            else:
                logger.error(f"Unknown tool call: {tool_name}")
                return None
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Grok response: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return None
    
    def get_token_usage_stats(self):
        """Get current token usage and cost statistics"""
        return {
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "reasoning_tokens": self.total_reasoning_tokens,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "api_calls_count": self.api_calls_count,
            "avg_cost_per_call": self.total_cost / self.api_calls_count if self.api_calls_count > 0 else 0
        }