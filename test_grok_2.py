#!/usr/bin/env python3
"""
Debug script to see exactly what Grok returns for Pokemon prompts
"""
import os
import json
import time
from openai import OpenAI

# NEW: pull tool declarations from the main agent so we test with the same list
from agent.grok_tool_implementations import AVAILABLE_TOOLS_LIST

# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# --------------------------------------------
# Utility to expand AVAILABLE_TOOLS_LIST into OpenAI tool spec
# --------------------------------------------

def build_tools(include_choose_action: bool = True):
    """Return the full tools array Grok will see in-game."""
    tools: list[dict] = []
    if include_choose_action:
        tools.append({
            "type": "function",
            "function": {
                "name": "choose_action",
                "description": "Choose the next action to take in the game",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "integer", "minimum": 0, "maximum": 7},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["action", "reasoning"],
                },
            },
        })

    # Add game-specific helper tools
    for tool in AVAILABLE_TOOLS_LIST:
        if "declaration" in tool:
            tools.append({"type": "function", "function": tool["declaration"]})
    return tools

# --------------------------------------------
# Main debug routine
# --------------------------------------------

def debug_grok_response():
    client = OpenAI(
        api_key=os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    # The exact prompt from your logs
    prompt = """You are in the title screens. In order to proceed, you need to press the start button. When you can no longer press start, press a to proceed. Make sure you pick entertaining names for yourself and your rival!
You are playing Pokemon Red. Your goal is to progress through the game efficiently.

Current situation:
- You are at REDS_HOUSE_2F (map 38) at position (3, 6)
- Current quest: 2
- Quest objective: Unknown
- Dialog active: YOUR NAME?
►A B C D E F G H I
J K L M N O P Q R
S ...

Game stats:
- Money: $999999
- Badges: 0
- Pokedex: 0 seen, 0 caught
- Items: 

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

If there's active dialog, press 'a' to advance it. If in battle, make battle decisions. Otherwise, navigate towards your quest objective."""
    
    messages = [
        {"role": "system", "content": "You are playing Pokemon Red."},
        {"role": "user", "content": prompt}
    ]
    
    tools = build_tools()
    
    print("🔍 Debugging Grok response with exact Pokemon prompt\n")
    
    # Add delay to avoid rate limiting
    time.sleep(1)
    
    try:
        # Make the call
        print("Making API call...")
        completion = client.chat.completions.create(
            model="grok-3-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=2500
        )
        
        print("\n📦 Raw completion object:")
        print(f"Model: {completion.model}")
        print(f"ID: {completion.id}")
        print(f"Created: {completion.created}")
        print(f"Object type: {completion.object}")
        
        if hasattr(completion, 'usage') and completion.usage:
            print(f"\n📊 Usage:")
            print(f"  Prompt tokens: {completion.usage.prompt_tokens}")
            print(f"  Completion tokens: {completion.usage.completion_tokens}")
            print(f"  Total tokens: {completion.usage.total_tokens}")
            if hasattr(completion.usage, 'completion_tokens_details'):
                print(f"  Details: {completion.usage.completion_tokens_details}")
        
        print(f"\n✉️  Choices: {len(completion.choices)} choice(s)")
        
        for i, choice in enumerate(completion.choices):
            print(f"\nChoice {i}:")
            print(f"  Index: {choice.index}")
            print(f"  Finish reason: {choice.finish_reason}")
            
            message = choice.message
            print(f"\n  Message:")
            print(f"    Role: {message.role}")
            print(f"    Content: {message.content}")
            print(f"    Has tool_calls: {hasattr(message, 'tool_calls') and message.tool_calls is not None}")
            print(f"    Has function_call: {hasattr(message, 'function_call') and message.function_call is not None}")
            
            if hasattr(message, 'tool_calls') and message.tool_calls:
                print(f"    Tool calls: {len(message.tool_calls)} call(s)")
                for j, tc in enumerate(message.tool_calls):
                    print(f"      Tool call {j}:")
                    print(f"        ID: {tc.id}")
                    print(f"        Type: {tc.type}")
                    print(f"        Function name: {tc.function.name}")
                    print(f"        Function arguments: {tc.function.arguments}")
            
            # Print all attributes for debugging
            print(f"\n  All message attributes:")
            for attr in dir(message):
                if not attr.startswith('_'):
                    value = getattr(message, attr, None)
                    if not callable(value):
                        print(f"    {attr}: {repr(value)[:100]}")

            # If Grok requested ask_friend, perform a quick follow-up call to show end-to-end chain
            if hasattr(message, 'tool_calls') and message.tool_calls:
                tc = message.tool_calls[0]
                if tc.function.name == 'ask_friend':
                    print("\n🔄 Grok called ask_friend – simulating friend reply and requesting follow-up …")
                    friend_answer = {"suggested_name": "GROK"}
                    # Construct follow-up messages array
                    follow_messages = messages + [
                        {"role": "assistant", "content": "", "tool_calls": [tc]},
                        {"role": "tool", "tool_call_id": tc.id, "name": "ask_friend", "content": json.dumps(friend_answer)},
                    ]
                    # Second call: now Grok should ask to enter_name or choose_action
                    follow_completion = client.chat.completions.create(
                        model="grok-3-mini",
                        messages=follow_messages,
                        tools=tools,
                        tool_choice="auto",
                        temperature=0.7,
                        max_tokens=2560,
                    )
                    second_msg = follow_completion.choices[0].message
                    print("\n🗣️  Second response:")
                    print(f"  Role: {second_msg.role}")
                    print(f"  Content: {second_msg.content}")
                    if second_msg.tool_calls:
                        print(f"  Tool call: {second_msg.tool_calls[0].function.name}")
                        print(f"  Args: {second_msg.tool_calls[0].function.arguments}")

        # Try a simpler prompt
        print("\n" + "="*60)
        print("Testing with simplified prompt...")
        print("="*60)
        
        time.sleep(1)  # Rate limit
        
        simple_completion = client.chat.completions.create(
            model="grok-3-mini",
            messages=[{"role": "user", "content": "Press action 4"}],
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=2560
        )
        
        print(f"\nSimple prompt completion tokens: {simple_completion.usage.completion_tokens}")
        if simple_completion.choices[0].message.tool_calls:
            print("✅ Simple prompt worked!")
            tc = simple_completion.choices[0].message.tool_calls[0]
            print(f"Tool call: {tc.function.name}")
            print(f"Arguments: {tc.function.arguments}")
        else:
            print("❌ Simple prompt also failed")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_grok_response()