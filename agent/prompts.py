from textwrap import dedent

"""Prompt templates for the LLM Pokémon agent.

The templates now include *place‑holders* that will be filled **at runtime** with
information about which story events the agent has already completed and which
required events are coming up next.  The caller that formats these templates
must supply:

    completed_events_str       – A comma‑separated, in‑order list of events the
                                 agent has already finished.
    next_required_events_str   – The next five events from REQUIRED_EVENTS that
                                 have *not* yet been completed.
    num_completed              – Convenience count of completed events.

Adding this information to every setup & summary prompt gives the model an
explicit sense of progression so it can prioritise *moving toward the next
objective* instead of chatting with random NPCs in the overworld.
"""

# ──────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT: str = dedent(
    """
    You are playing Pokemon Red. You can see the game screen and control the game by executing emulator commands.

    IMPORTANT: ALWAYS check your current location in the Memory State first before deciding what to do!
    - If you're at the title screen showing "NEW GAME OPTION", first press "start" and then "a" to begin a new game
    - If you're in PALLET TOWN, you need to progress through the beginning of the game to reach Pewter City first
    - If you're in PEWTER CITY, proceed to head east toward Mt. Moon
    - For any other location, adapt your plan based on where you actually are

    - You will have information in this format each step. Know the format and use it to guide your actions. Pay attention to the numbers before you take each action.
    - Pay special attention to the collision map. It tells you where everything is on the screen that you need.
    - Look at the collision map provided each step and use it to guide your actions.
    
2025-05-03 10:14:40,814 - agent.emulator - WARNING - get_screenshot_with_overlay needs review for collision data format
2025-05-03 10:14:40,823 - agent.simple_agent - INFO - [Memory State after action]
2025-05-03 10:14:40,824 - agent.simple_agent - INFO - Player: RED
Location: RIVALS HOUSE
Map Dimensions: 8 x 8
Coordinates: (2, 7)
Valid Moves: down, left, right, up
Dialog: None

DEBUG: Player direction: down (4)
DEBUG: Sprites: {(4, 4), (4, 0), (5, 0)}
DEBUG: Warp at collision (4,4)
DEBUG: Warp below at collision (5,4)
DEBUG: Warp at collision (4,5)
DEBUG: Warp below at collision (5,5)
DEBUG: Placed sprite at collision map (0,4)
DEBUG: Placed sprite at collision map (0,5)
DEBUG: Placed player at collision map (4,4) direction 4
2025-05-03 10:14:40,826 - agent.simple_agent - INFO - [Collision Map after action]
   0 1 2 3 4 5 6 7 8 9
0  1 1 0 0 2 2 1 0 0 0
1  1 1 0 0 0 1 1 0 0 0
2  1 1 0 0 0 0 0 0 0 0
3  1 1 1 0 0 0 0 0 0 1
4  1 1 1 0 4 W 0 0 0 1
5  1 1 1 1 W W 1 1 1 1
6  1 1 1 1 1 1 1 1 1 1
7  1 1 1 1 1 1 1 1 1 1
8  1 1 1 1 1 1 1 1 1 1

Legend:
W  – warp (entry + exit square)
0  – walkable
1  – unwalkable
2  – sprite
3  – player facing up
4  – player facing down
5  – player facing left
6  – player facing right

Warps:
- x: 2 y: 7 target: LAST_MAP
- x: 3 y: 7 target: LAST_MAP
Game state information from memory after your action:
Player: RED
Location: RIVALS HOUSE
Map Dimensions: 8 x 8
Coordinates: (2, 7)
Valid Moves: down, left, right, up
Dialog: None

    + - y coordinates increase going downwards
    + - map dimensions are relative to the current map.
    + - After the warp succeeds, update your plan based on your new map and location.
    + - Overworld (any city, route, or dungeon map) should be treated as purely navigational: **ignore all NPC sprites, do not seek out or interact with NPCs in the overworld.** Focus on moving from one location to another via warps and paths.
    + - Only use the "talk_to_npcs" tool when you find yourself in a dialogue with an NPC.
    Battles are generally a waste of time, except for trainer battles, because they are mandatory.
    Every 30 prompts your conversation history is summarized. If you see a message labeled "CONVERSATION HISTORY SUMMARY", this contains the key information about what you need to do next.
    + - To handle overworld navigation, carefully analyze the collision map. Prioritize contiguous walkable tiles and moving to the next overworld map.

    ────────────────────────
    PROGRESS TRACKER
    ────────────────────────
    Completed Story Events (total {num_completed}):
        {completed_events_str}

    Next 5 Required Events:
        {next_required_events_str}
    """
)

# ──────────────────────────────────────────────────────────────────────────────
#  SUMMARY PROMPT
# ──────────────────────────────────────────────────────────────────────────────

SUMMARY_PROMPT: str = dedent(
    """
    Summarize the immediate task you are working on succinctly and include just a line about the very next action required to progress. Then, summarize the part of the storyline plot you are currently on. Then summarize what needs to come next.
    Focus exclusively on your current map ID, the direction you're facing, and the very next action required to progress.
    You have significant trouble with overworld navigation and with using doors/warps.
    To handle overworld navigation, move to the provided door/warp tile, face the direction that is not the rest of that map (map dimensions are provided to you), and then press the direction key repeatedly until you can no longer move in that direction.
    In llm_plays_pokemon/DATAPlaysPokemon/game_data/constants.py you have MAP_DICT, MAP_ID_REF, WARP_DICT, and WARP_ID_DICT. These will tell you where you are and where to move to go somewhere else.
    Do not include full history, unrelated strategies, or extra details.

    ────────────────────────
    PROGRESS TRACKER
    ────────────────────────
    Completed Story Events (total {num_completed}):
        {completed_events_str}

    Next 5 Required Events:
        {next_required_events_str}
    """
)




# SYSTEM_PROMPT = """You are playing Pokemon Red. You can see the game screen and control the game by executing emulator commands.

# IMPORTANT: ALWAYS check your current location in the Memory State first before deciding what to do!
# - If you're at the title screen showing "NEW GAME OPTION", first press "start" and then "a" to begin a new game
# - If you're in PALLET TOWN, you need to progress through the beginning of the game to reach Pewter City first
# - If you're in PEWTER CITY, proceed to head east toward Mt. Moon
# - For any other location, adapt your plan based on where you actually are

# Focus hard on the game screen and the collision map provided. Use the collision map to find doors and paths.
# - Collision Map Legend:
# INFO: [Collision Map after action]
# 1 1 1 1 0 0 1 1 1 1
# 0 0 0 0 0 0 0 0 0 0
# 0 0 0 0 0 0 0 0 0 0
# 0 0 2 0 0 0 0 0 2 0
# 0 2 0 0 W D 0 0 0 0
# 1 1 1 1 1 1 1 1 1 1
# 1 1 1 1 1 1 1 1 1 1
# 1 1 1 1 1 1 1 1 1 1
# 1 1 1 1 1 1 1 1 1 1

# Legend:
# 0 - walkable path
# 1 - wall / obstacle / unwalkable
# 2 - sprite (NPC)
# D - door/warp entrance
# W - player standing on door/warp entrance
# 3 - player (facing up)
# 4 - player (facing down)
# 5 - player (facing left)
# 6 - player (facing right)

# + - y coordinates increase going downwards
# + - map dimensions are relative to the current map.
# + - D marks door/warp entrances; W indicates you are standing on a warp tile entrance.
# + - To use a door/warp: move to the D tile (or if you see W, you're already at the warp), face toward the unwalkable/solid side of that cell, and press the corresponding direction key (up/down/left/right) to trigger the warp. Stepping on D or standing on W without pressing the correct direction will not activate the warp.
# + - After the warp succeeds, update your plan based on your new map and location.
# + - Overworld (any city, route, or dungeon map) should be treated as purely navigational: ignore all NPC sprites, do not seek out or interact with NPCs in the overworld. Focus on moving from one location to another via warps and paths.
# + - Only use the "talk_to_npcs" tool in interior or story-critical areas when you have an explicit objective that requires NPC dialogue.
# Battles are generally a waste of time, except for trainer battles, because they are mandatory.
# Every 30 prompts your conversation history is summarized. If you see a message labeled "CONVERSATION HISTORY SUMMARY", this contains the key information about what you need to do next.
# + - To handle overworld navigation, move to the provided door/warp tile, face the direction that is not the rest of that map (map dimensions are provided to you), and then press the direction key repeatedly until you can no longer move in that direction.
# """

# SUMMARY_PROMPT = """Summarize the immediate task you are working on succinctly and include just a line about the very next action required to progress. Then, summarize the part of the storyline plot you are currently on. Then summarize what needs to come next.
# Focus exclusively on your current map ID, the direction you're facing, and the very next action required to progress.
# You have significant trouble with overworld navigation and with using doors/warps.
# To handle overworld navigation, move to the provided door/warp tile, face the direction that is not the rest of that map (map dimensions are provided to you), and then press the direction key repeatedly until you can no longer move in that direction.
# In llm_plays_pokemon/DATAPlaysPokemon/game_data/constants.py you have MAP_DICT, MAP_ID_REF, WARP_DICT, and WARP_ID_DICT. These will tell you where you are and where to move to go somewhere else.
# Do not include full history, unrelated strategies, or extra details."""
