# Grok Plays Pokemon - Multi-Provider Edition

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