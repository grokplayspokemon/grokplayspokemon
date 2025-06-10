#!/usr/bin/env python3
"""
Debug script to see exactly what Grok returns for Pokemon prompts
"""
import os
import json
import time
from openai import OpenAI

# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

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
        {"role": "system", "content": "You are an expert Pokemon player. Make decisions quickly and efficiently."},
        {"role": "user", "content": prompt}
    ]
    
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
            max_tokens=500
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
            max_tokens=50
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