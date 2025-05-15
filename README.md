# Grok Plays Pokemon - Multi-Provider Edition

TLDR:
 the game state only changes after an emulator button press, so, after any emulator button press (which is required to take an entire step()) we can append the updated game state after running necessary functions and update game state code. this is in effect our 'message' to the llm, along with the previous steps' messages to the llm. the llm gets the message, does its thinking and analysis (which we capture for logging purposes, and for debugging purposes, to see if there are issues with what we're providing it), and then returns back a response message, along with a tool call/tool calls. we extract any tool calls and also extract the "explanation message," a sentence each response that the llm provides to explain why it chose the tool calls it chose. this is specified in the llm's prompts. the tool calls go to the "grok's tool calls" ui and the explanation sentence goes to the "grok's thoughts" ui. 

 in order to press an emulator button, grok must use a tool call. the following tool calls are available to grok:
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

battles are handled via the prompt. grok is a pretty good battler: grok heals when on low health, and picks super effective moves if available. grok receives a system_prompt when navigating the overworld, and a battle_system_prompt when in a battle. when in a dialog, grok does not receive a prompt; instead, only game state information is provdided. relevant game state information is appended to the system prompts: when navigating, various data from ram are provided, in addition to suggestions as to what direction to travel in and what the next destination is. event completion data are provided both explicitly, for mandatory game events, and implicitly, for most important events that will ultimately be completed in grok's journey to become champion. 

### Message Appending and History Management
- After every emulator button press (each agent step), we append the latest game state as a `user` message.
- We include a JSON payload of available navigation options `{ "available_moves": [...] }` so the LLM knows all valid actions.
- All raw LLM responses (`assistant` messages) and tool execution results (`tool` messages) are also appended to history.
- Duplicate `user` messages (identical state texts) are automatically de-duplicated based on content hashing.
- Once the total history length exceeds `MAX_HISTORY`, we trigger `summarize_history()` to condense past exchanges into an exploration reward summary and reset the message history.
- A single `system` prompt is kept at the top; entering battle or dialog resets to specialized battle or dialog prompts as needed.

A minimal implementation for LLMs playing Pokemon Red using the PyBoy emulator. This version supports multiple AI providers:

- Anthropic (Claude)
- OpenAI
- X.AI (Grok)

Features include:
- Simple agent that uses AI to play Pokemon Red
- Memory reading functionality to extract game state information
- Basic emulator control through function calling
- Comprehensive logging system for game frames and LLM messages
- Web-based UI for viewing gameplay (accessible at http://localhost:3000)

## Setup

1. Clone this repository
2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```
3. Set up your API key as an environment variable based on which provider you want to use:
   ```
   # For Anthropic
   export ANTHROPIC_API_KEY=your_api_key_here
   
   # For OpenAI
   export OPENAI_API_KEY=your_api_key_here
   
   # For X.AI/Grok
   export XAI_API_KEY=your_api_key_here
   ```

4. Place your Pokemon Red ROM file in the root directory (you need to provide your own ROM)

## Usage

Run the main script with your preferred provider:

```
# For default (Anthropic/Claude)
python main.py --rom pokemon.gb

# For OpenAI
python main.py --provider openai --model o4-mini --rom pokemon.gb

# For X.AI/Grok
python main.py --provider grok --model grok-3-latest --rom pokemon.gb
```

Or use the convenience script for Grok:
```
# Make sure to chmod +x run_with_grok.sh first
./run_with_grok.sh
```

Optional arguments:
- `--rom`: Path to the Pokemon ROM file (default: `pokemon.gb` in the root directory)
- `--steps`: Number of agent steps to run (default: 1000)
- `--port`: Port to run the web server on (default: 3000)
- `--max-history`: Maximum number of messages in history before summarization (default: 30)
- `--provider`: LLM provider to use (choices: "anthropic", "openai", "grok", default: "anthropic")
- `--model`: Model name to use (defaults based on provider)
- `--overlay`: Enable tile overlay visualization showing walkable/unwalkable areas

## Web Interface

The application now includes a web interface that can be accessed at http://localhost:3000 (or whatever port you specify with `--port`). This interface allows you to:

1. View the current game screen
2. See the AI's latest decision
3. Start and stop the agent
4. Save and load game states

## Logging System

The game automatically creates logs for each run in the `/logs` directory. Each run gets its own timestamped directory (e.g., `logs/run_20240321_123456/`) containing:

- `frames/`: Directory containing numbered PNG files of each game frame
- `grok_messages.log`: Log file of all LLM messages with timestamps
- `game.log`: General game logs including errors and important events

This logging system helps track the AI's decision-making process and the game's progression over time.

## Implementation Details

### Components

- `agent/simple_agent.py`: Main agent class that uses AI to play Pokemon
- `agent/emulator.py`: Wrapper around PyBoy with helper functions
- `agent/memory_reader.py`: Extracts game state information from emulator memory
- `web/`: Contains the FastAPI web server and UI components