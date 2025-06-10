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
from openai import OpenAI
from utils.logging_config import get_pokemon_logger

from environment.environment import VALID_ACTIONS, VALID_ACTIONS_STR, PATH_FOLLOW_ACTION
from environment.grok_integration import extract_structured_game_state, GameState
from environment.environment_helpers.quest_manager import QuestManager
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.wrappers.env_wrapper import EnvWrapper
from environment.environment import RedGymEnv
from pyboy.utils import WindowEvent

# Use the centralized PokemonLogger for agent logs
agent_logger = get_pokemon_logger()

logger = logging.getLogger(__name__)

class SimpleAgent:
    """Simple agent that uses Grok to make decisions about Pokemon gameplay"""
    
    def __init__(self, reader: RedGymEnv, quest_manager: QuestManager, 
                 navigator: InteractiveNavigator, env_wrapper: EnvWrapper,
                 xai_api_key: str):
        self.reader = reader
        # Setup file-based logger for Grok agent events
        self.agent_file_logger = logging.getLogger('agent_file_logger')
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename.endswith('agent.log') for h in self.agent_file_logger.handlers):
            fh = logging.FileHandler('agent.log')
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
            response = self._call_grok(prompt)
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
            # Add quest description if available
            if self.quest_manager:
                quest_data = self.quest_manager.quest_progression_engine.get_quest_data_by_id(game_state.quest_id)
                if quest_data:
                    context_parts.append(f"Quest objective: {quest_data.get('description', 'Unknown')}")
        
        # Party status
        if game_state.party:
            alive_pokemon = [p for p in game_state.party if p['hp'] > 0]
            context_parts.append(f"Party: {len(alive_pokemon)}/{len(game_state.party)} Pokemon alive")
            if alive_pokemon:
                lead = alive_pokemon[0]
                context_parts.append(f"Lead Pokemon: {lead['species']} Lv.{lead['level']} ({lead['hp']}/{lead['maxHp']} HP)")
        
        # Battle context
        if game_state.in_battle:
            context_parts.append("Currently in battle!")
        
        # Dialog context
        if game_state.dialog:
            context_parts.append(f"Dialog active: {game_state.dialog[:50]}...")
        
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
You are playing Pokemon Red. Your goal is to progress through the game efficiently.

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
6: path (follow quest path)
7: start (menu)

Think step by step about what to do next, then call the 'choose_action' tool with your chosen action number.

"""

        return prompt
    
    def _call_grok(self, prompt: str) -> str:
        """Call Grok API with optimized prompts based on context"""
        
        # Detect special game contexts
        is_naming_screen = "NAME?" in prompt
        
        # Optimize prompt for specific contexts
        if is_naming_screen:
            # Much simpler prompt for naming screen to avoid reasoning overload
            optimized_messages = [
                {"role": "user", "content": "You're at the Pokemon character naming screen. After the '\n' the name you are picking displays. '\u25ba' represents your cursor position."}
            ]
            max_tokens = 2000  # Less tokens needed for simple response
            reasoning_effort = None  # No reasoning needed for simple action
        else:
            # Regular prompt for other situations
            optimized_messages = [
                {"role": "system", "content": "You are an expert Pokemon player. After the '\n' the name you are picking displays. '\u25ba' represents your cursor position."},
                {"role": "user", "content": prompt}
            ]
            max_tokens = 2000  # More tokens for complex situations
            reasoning_effort = "low"  # Enable reasoning for complex decisions
        
        tools = [{
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
        }]
        
        # Log what we're doing
        agent_logger.log_system_event("GROK_API_CALL", {
            "is_naming_screen": is_naming_screen,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "prompt_length": len(prompt)
        })
        
        try:
            # Build kwargs dynamically
            api_kwargs = {
                "model": "grok-3-mini",
                "messages": optimized_messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": max_tokens
            }
            
            # Only add reasoning_effort if not None
            if reasoning_effort:
                api_kwargs["reasoning_effort"] = reasoning_effort
            
            # Make the API call
            completion = self.client.chat.completions.create(**api_kwargs)
            
            # Log token usage
            usage = getattr(completion, 'usage', None)
            if usage:
                details = getattr(usage, 'completion_tokens_details', None)
                reasoning_tokens = getattr(details, 'reasoning_tokens', 0) if details else 0
                
                agent_logger.log_system_event("GROK_TOKEN_USAGE", {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "total_tokens": usage.total_tokens,
                    "finish_reason": completion.choices[0].finish_reason
                })
            
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
                    "context": "naming_screen" if is_naming_screen else "general"
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
            
            # No tool call generated
            agent_logger.log_error("GROK_NO_TOOL_CALL", "No tool call in response", {
                "finish_reason": finish_reason,
                "has_content": bool(getattr(message, 'content', None))
            })
            
            # Provide sensible defaults
            if is_naming_screen:
                return json.dumps({
                    "tool_call": "choose_action",
                    "arguments": {"action": 4, "reasoning": "Selecting letter A at naming screen"}
                })
            else:
                return json.dumps({"error": "No tool call generated"})
                
        except Exception as e:
            logger.error(f"Grok API error: {e}")
            
            # # Provide fallback for naming screen
            # if is_naming_screen:
            #     return json.dumps({
            #         "tool_call": "choose_action",
            #         "arguments": {"action": 4, "reasoning": "Error fallback: pressing A"}
            #     })
            
            return json.dumps({"error": str(e)})
    
    def _parse_response(self, response: str) -> Optional[int]:
        """Parse Grok's response to extract the action"""
        
        try:
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"Grok error: {data['error']}")
                return None
            
            if data.get("tool_call") == "choose_action":
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
            
            return None
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Grok response: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return None