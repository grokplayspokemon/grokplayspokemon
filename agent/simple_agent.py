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
from agent.grok_tool_implementations import AVAILABLE_TOOLS_LIST, press_buttons, navigate_to, exit_menu, ask_friend, handle_battle
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
        
        self.tool_implementations = {
            "press_buttons": press_buttons,
            "navigate_to": navigate_to,
            "exit_menu": exit_menu,
            "ask_friend": ask_friend,
            "handle_battle": handle_battle
        }
        
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
            return action
            
        except Exception as e:
            agent_logger.log_error("GROK_GET_ACTION_ERROR", f"Error getting Grok action: {e}")
            self.last_response = f"Error: {str(e)}"
            return None
    
    def _build_prompt(self, game_state: GameState) -> str:
        """Build a comprehensive prompt for Grok"""
        
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
        
        # Recent actions
        if self.action_history:
            recent = self.action_history[-3:]
            action_str = ", ".join([VALID_ACTIONS_STR[a['action']] for a in recent if a['action'] < len(VALID_ACTIONS_STR)])
            context_parts.append(f"Recent actions: {action_str}")
        
        # Conditional prompt for game start
        if game_state.location['map_id'] == 0 and loc['x'] == 0 and loc['y'] == 0 or game_state.location['map_id'] == 38 and loc['x'] == 3 and loc['y'] == 6 and game_state.dialog != "":
            prompt_start = "Make sure you pick entertaining names for yourself and your rival!"
        else:
            prompt_start = ""

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

Think step by step about what to do next, then call the 'choose_action' tool with your chosen action number.

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
            messages.append({"role": "user", "content": DIALOG_PROMPT})
        else:
            messages.append({"role": "user", "content": prompt})
        return messages
    
    def _call_grok(self, prompt: str, game_state: GameState) -> str:
        """Call Grok API with optimized prompts based on context"""
        
        # Construct messages list based on current game state
        messages = self._make_messages(game_state, prompt)
        # Log messages count
        agent_logger.log_system_event("GROK_API_MESSAGES", {"messages_count": len(messages)})
        # Standard max tokens
        max_tokens = 2000
        # Build kwargs for API call
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "choose_action",
                    "description": "Choose the next action to take in the game",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "integer", "description": "The action number to execute (0-7)", "minimum": 0, "maximum": 7},
                            "reasoning": {"type": "string", "description": "Brief explanation of why this action was chosen"}
                        },
                        "required": ["action", "reasoning"]
                    }
                }
            }
        ]
        for tool_info in AVAILABLE_TOOLS_LIST:
            if 'declaration' in tool_info:  # Make sure declaration exists
                tools.append({
                    "type": "function",
                    "function": tool_info['declaration']
                })
        
        # Register handle_battle function for fully automated battle handling
        tools.append({
            "type": "function",
            "function": {
                "name": "handle_battle",
                "description": "Automatically handle battles by selecting the best move.",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        })
        
        # Log what we're doing
        agent_logger.log_system_event("GROK_API_CALL", {"max_tokens": max_tokens, "prompt_length": len(prompt)})
        
        try:
            # Build kwargs dynamically
            api_kwargs = {
                "model": "grok-3-mini",
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": max_tokens
            }
            
            # Call the API
            completion = self.client.chat.completions.create(**api_kwargs)
            
            # Track API call count
            self.api_calls_count += 1
            
            # Log token usage
            usage = getattr(completion, 'usage', None)
            if usage:
                details = getattr(usage, 'completion_tokens_details', None)
                reasoning_tokens = getattr(details, 'reasoning_tokens', 0) if details else 0
                
                # Calculate token usage for this call
                prompt_tokens = usage.prompt_tokens
                completion_tokens = usage.completion_tokens
                total_tokens = usage.total_tokens
                
                # Calculate cost
                prompt_cost = prompt_tokens * COST_PER_INPUT_TOKEN
                completion_cost = completion_tokens * COST_PER_COMPLETION_TOKEN
                total_cost = prompt_cost + completion_cost
                
                # Update cumulative totals
                self.total_prompt_tokens += prompt_tokens
                self.total_completion_tokens += completion_tokens
                self.total_reasoning_tokens += reasoning_tokens
                self.total_tokens += total_tokens
                self.total_cost += total_cost
                
                # Log token usage and cost
                agent_logger.log_system_event("GROK_TOKEN_USAGE", {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "total_tokens": total_tokens,
                    "finish_reason": completion.choices[0].finish_reason,
                    "call_cost": total_cost,
                    "cumulative_cost": self.total_cost,
                    "api_calls_count": self.api_calls_count,
                    "avg_cost_per_call": self.total_cost / self.api_calls_count if self.api_calls_count > 0 else 0
                })
                
                # Also log to agent.log
                self.agent_file_logger.info(
                    f"GROK_TOKEN_COST: prompt={prompt_tokens}, completion={completion_tokens}, "
                    f"cost=${total_cost:.6f}, total=${self.total_cost:.6f}, calls={self.api_calls_count}"
                )
                
                # Send to UI via status queue
                if hasattr(self, 'status_queue'):
                    self.status_queue.put(('__grok_cost__', {
                        'call_cost': total_cost,
                        'total_cost': self.total_cost,
                        'total_tokens': self.total_tokens,
                        'api_calls_count': self.api_calls_count
                    }))
                    # Send detailed token usage for UI input/output fields
                    self.status_queue.put(('__llm_usage__', {
                        'input_tokens': prompt_tokens,
                        'output_tokens': completion_tokens,
                        'input_cost': prompt_cost,
                        'output_cost': completion_cost
                    }))
                    # Persist token usage to file
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
                    except Exception as e:
                        self.agent_file_logger.error(f"Error saving token usage persistence: {e}")
            
            # Get the message
            message = completion.choices[0].message
            
            # Capture reasoning if present
            if hasattr(message, 'reasoning_content') and message.reasoning_content:
                self.last_thinking = message.reasoning_content
                # Send to UI via status queue
                if hasattr(self, 'status_queue'):
                    self.status_queue.put(('__grok_thinking__', self.last_thinking))
            
            # Check for tool calls
            if hasattr(message, 'tool_calls') and message.tool_calls:
                tool_call = message.tool_calls[0]
                args = json.loads(tool_call.function.arguments)
                
                # Update last response for UI
                self.last_response = f"Action {args.get('action', '?')}: {args.get('reasoning', '')}"
                
                agent_logger.log_system_event("GROK_SUCCESS", {
                    "action": args.get('action'),
                    "reasoning": args.get('reasoning', '')[:100],  # First 100 chars
                    "context": "naming_screen" if "NAME?" in prompt else "general"
                })
                
                return json.dumps({
                    "tool_call": tool_call.function.name,
                    "arguments": args
                })
            
            # Check finish reason
            finish_reason = completion.choices[0].finish_reason
            if finish_reason == "length":
                agent_logger.log_error("GROK_LENGTH_LIMIT", 
                    f"Hit token limit with {max_tokens} max_tokens", {})
                
                # Retry with more tokens
                if max_tokens < 3000:
                    agent_logger.log_system_event("GROK_RETRY", {"new_max_tokens": 3000})
                    api_kwargs["max_tokens"] = 3000
                    retry_completion = self.client.chat.completions.create(**api_kwargs)
                    
                    retry_message = retry_completion.choices[0].message
                    if hasattr(retry_message, 'tool_calls') and retry_message.tool_calls:
                        tool_call = retry_message.tool_calls[0]
                        args = json.loads(tool_call.function.arguments)
                        return json.dumps({
                            "tool_call": tool_call.function.name,
                            "arguments": args
                        })
            
            # Add this after parsing the tool call response:
            if data.get("tool_call") == "handle_battle":
                # Execute the handle_battle function
                if hasattr(self, 'tool_implementations'):
                    handle_battle_fn = self.tool_implementations.get("handle_battle")
                    if handle_battle_fn:
                        human_summary, structured_output = handle_battle_fn(
                            self.reader,  # env
                            self.quest_manager,
                            self.navigator,
                            self.env_wrapper
                        )
                        # Return some indication that battle was handled
                        return json.dumps({
                            "tool_call": "handle_battle",
                            "result": structured_output,
                            "summary": human_summary
                        })
                        
            # No tool call generated
            agent_logger.log_error("GROK_NO_TOOL_CALL", "No tool call in response", {
                "finish_reason": finish_reason,
                "has_content": bool(getattr(message, 'content', None))
            })
            
            # Provide sensible defaults
            if "NAME?" in prompt:
                return json.dumps({
                    "tool_call": "choose_action",
                    "arguments": {"action": 4, "reasoning": "Selecting default actions..."}
                })
            else:
                return json.dumps({"error": "No tool call generated"})
                
        except Exception as e:
            logger.error(f"Grok API error: {e}")
            
            return json.dumps({"error": str(e)})
    
    def _parse_response(self, response: str) -> Optional[int]:
        """Parse Grok's response to extract the action or execute tools"""
        
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