#!/usr/bin/env python3
"""
Diagnostic script to test Grok/xAI API connectivity and response format
"""
import os
import json
from openai import OpenAI

def test_grok_api(api_key):
    """Test various API configurations to diagnose issues"""
    
    # Initialize client
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1"
    )
    
    print("🔍 Testing Grok/xAI API...\n")
    
    # Test 1: List available models
    print("Test 1: Listing available models...")
    try:
        models = client.models.list()
        print("✅ Available models:")
        for model in models:
            print(f"  - {model.id}")
        print()
    except Exception as e:
        print(f"❌ Failed to list models: {e}\n")
    
    # Test 2: Simple completion without tools
    print("Test 2: Simple completion (no tools)...")
    try:
        response = client.chat.completions.create(
            model="grok-3-mini",  # Try this first
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'Hello, World!' and nothing else."}
            ],
            max_tokens=50
        )
        message = response.choices[0].message
        print(f"✅ Response content: {message.content}")
        print(f"   Message type: {type(message)}")
        print(f"   Message attributes: {[attr for attr in dir(message) if not attr.startswith('_')]}\n")
    except Exception as e:
        print(f"❌ Simple completion failed: {e}\n")
    
    # Test 3: Completion with JSON response request
    print("Test 3: JSON response format...")
    try:
        response = client.chat.completions.create(
            model="grok-3-mini",
            messages=[
                {"role": "system", "content": "You must respond only with valid JSON."},
                {"role": "user", "content": "Respond with a JSON object containing 'action': 4 and 'reasoning': 'test'"}
            ],
            max_tokens=100
        )
        content = response.choices[0].message.content
        print(f"✅ Response: {content}")
        try:
            parsed = json.loads(content)
            print(f"   Parsed JSON: {parsed}\n")
        except:
            print(f"   ⚠️  Failed to parse as JSON\n")
    except Exception as e:
        print(f"❌ JSON response test failed: {e}\n")
    
    # Test 4: Function calling with tools (OpenAI format)
    print("Test 4: Function calling (OpenAI format)...")
    try:
        response = client.chat.completions.create(
            model="grok-3-mini",
            messages=[
                {"role": "user", "content": "Choose action 4 (press A button)"}
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "choose_action",
                    "description": "Choose an action",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "integer"},
                            "reasoning": {"type": "string"}
                        },
                        "required": ["action", "reasoning"]
                    }
                }
            }],
            tool_choice="auto"
        )
        
        message = response.choices[0].message
        print(f"✅ Response received")
        print(f"   Has content: {bool(message.content)}")
        print(f"   Content: {message.content if message.content else 'None'}")
        print(f"   Has tool_calls: {hasattr(message, 'tool_calls') and bool(message.tool_calls)}")
        print(f"   Has function_call: {hasattr(message, 'function_call') and bool(message.function_call)}")
        
        if hasattr(message, 'tool_calls') and message.tool_calls:
            for i, tool_call in enumerate(message.tool_calls):
                print(f"   Tool call {i}: {tool_call.function.name}")
                print(f"   Arguments: {tool_call.function.arguments}")
        elif hasattr(message, 'function_call') and message.function_call:
            print(f"   Function: {message.function_call.name}")
            print(f"   Arguments: {message.function_call.arguments}")
        print()
        
    except Exception as e:
        print(f"❌ Function calling test failed: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}\n")
    
    # Test 5: Try with response_format parameter
    print("Test 5: Using response_format parameter...")
    try:
        response = client.chat.completions.create(
            model="grok-3-mini",
            messages=[
                {"role": "user", "content": "Choose action 4. Respond with action number and reasoning."}
            ],
            response_format={"type": "json_object"},
            max_tokens=100
        )
        content = response.choices[0].message.content
        print(f"✅ Response: {content}\n")
    except Exception as e:
        print(f"❌ response_format test failed: {e}\n")
    
    # Test 6: Test different model names
    print("Test 6: Testing different model names...")
    model_names = ["grok-3-mini", "grok-2", "grok-1", "grok", "grok-beta"]
    for model in model_names:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=10
            )
            print(f"✅ Model '{model}' works!")
        except Exception as e:
            print(f"❌ Model '{model}' failed: {str(e)[:100]}")
    
    print("\n🏁 Diagnostic complete!")

if __name__ == "__main__":
    # Get API key from environment or command line
    api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    
    if not api_key:
        print("❌ No API key found! Set XAI_API_KEY or GROK_API_KEY environment variable.")
        exit(1)
    
    test_grok_api(api_key)