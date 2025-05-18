# Install

`conda create -n poke_replay python=3.11 --yes`

`conda activate poke_replay`

`pip install -r requirements.txt`

# Known Issues

On MacOS, you might need to uninstall pysdl2-dll.

`pip uninstall pysdl2-dll`

# Add ROM

The Pokémon Red Rom should be located at the path `./PokemonRed.gb`. Otherwise specify the path using `--rom my_rom.gb`.

# Record playthrough

`python play.py --name my_replay.json`

Controls:

```
action_mapping = {
    pygame.K_UP: 3,
    pygame.K_DOWN: 0,
    pygame.K_LEFT: 1,
    pygame.K_RIGHT: 2,
    pygame.K_a: 4, # A
    pygame.K_s: 5, # B
    pygame.K_RETURN: 6,
}
```

Note that the key A is used for `A` and the key S is used for `B`. Change this to your likings. I did this because of the different local keyboard layouts.

When done recording, press `ESC`, `Ctrl + C`, or just quit.

You may also resume a saved playthrough as follows:

`python play.py --name my_replay_resume.json --resume my_replay.json`

During the playthrough, you can press `P` to capture a screenshot or press `O` to save the state.

# Replay

`python replay.py --name example_replay.json`

or headless

`python replay.py --name example_replay.json --headless`

# Recording Instructions

- Play untill receiving TM Dig (ensure to beat Misty and get Badge 2 as well)
- No need to speed run
- No need to play absolutely precise
- Your strategy can be pretty free after all
- Out of scope:
    - Completing the pokedex
    - Using the storage system
- (Optional: You can proceed further in the main story if you want to keep recording.) 


### New Functionality
## Notes
Okay, let's review the pathfinding functions we've built into ReplayExtender and then discuss how they could be called within a step() function framework, which is a good, structured approach for integrating an LLM.
Assessment of ReplayExtender Pathfinding Functions:
Core Logic:
The chain of functions (obtain_path_for_this_map -> compute_nearest_json_path_coord -> find_the_navigational_path -> walk_on_the_path) is logically sound for the goal: take an ultimate on-screen target, find a strategic intermediate point on a predefined global path (warp_paths.json), use A\* (make_path_to_target_row_col) to plan moves to that intermediate point, and then execute those moves.
Helper methods for coordinates, map IDs, and A\* (heuristic, downsampling, sprite fetching) provide the necessary support.
Key Dependencies & Accuracy:
warp_paths.json: The quality and coverage of paths in this file are crucial for obtain_path_for_this_map and compute_nearest_json_path_coord to find relevant global paths.
tile_pair_collisions.json: The accuracy of make_path_to_target_row_col in respecting ledges and complex tile interactions depends entirely on this file.
As we discussed, you don't currently have this file. This means _can_move_between_tiles will default to being permissive (allowing most moves if the tile isn't a basic wall).
Impact: A\* will primarily avoid solid walls (from game_area_collision()) and sprites. It may generate paths that try to go up ledges, which will fail during walk_on_the_path (the player simply won't move). This is a limitation to be aware of.
get_sprites() and A\* Grid Mapping: The accuracy of sprite avoidance depends on pyboy.get_sprite() and the conversion of sprite pixel coordinates to the 9x10 A\* grid. The current implementation for get_sprites is a reasonable adaptation.
Coordinate Systems: Consistent use and conversion between global coordinates, local 9x10 A\* grid coordinates, and raw tile map coordinates are vital. The helper methods aim to manage this.
Completeness for Described Purpose:
For the task of "finding a path to an intermediate point on a JSON path and then walking it," the functions are structurally complete.
They return status messages, which Grok's controlling logic will need to interpret.
Conclusion on ReplayExtender methods: The functions provide a good framework. The main current weakness for A\* precision is the lack of detailed tile_pair_collisions.json data, making ledge handling in A\* non-functional.
Integrating into a step() Function Framework:
Your idea of a central step() function is excellent. This function would be the main loop where the LLM (Grok) provides an action or a goal, and the environment (your Python code) executes it and returns new observations.
Here's a conceptual way to integrate your ReplayExtender's capabilities:
Let's assume your main environment class has an instance of ReplayExtender, e.g., self.path_navigator = ReplayExtender(self.pyboy, self.env_wrapper).
And you'll need to manage the state of an ongoing A\* path:
self.current_astar_path_moves = [] (list of strings like \["up", "down"])
self.actions_executed_on_current_astar_path = []

## Summary 0
Here's a summary of what we've covered, the overall plan as I understand it, and our current tasks, with some additional observations:
Project Overview:
The project, "DATAPlaysPokemon," aims to create an AI system capable of playing the game Pokemon Red. This involves not just emulating the game but also providing the AI with the means to perceive the game world, make decisions, and execute actions within it. A core aspect is enabling intelligent navigation and interaction with the game environment.
Work Done So Far:
Our recent efforts have primarily focused on enhancing the navigation capabilities within the recorder/warp_runner.py script. Key accomplishments include:
Pathing Functionality:
We've implemented execute_warp_to_coords, a function that allows the system to navigate to specific global X, Y coordinates.
This function relies on pre-computed paths defined in warp_paths.json, which it loads using load_paths.
The navigation logic involves matching the current map ID, finding a path containing the target coordinates, and then stepping through the path segment by segment.
Integration of Pathing:
Pathing can be triggered via command-line arguments (--glob_x, --glob_y) when warp_runner.py is launched.
We've also integrated an interactive pathing trigger: pressing the 'W' key during interactive play prompts the user for global X and Y coordinates, then attempts to navigate there.
Action Resumption & Recording:
The script retains the ability to resume playthroughs from a saved list of actions (--resume argument and execute_preloaded_actions function).
All actions (manual, preloaded, or from pathing) are recorded and saved to a JSON file.
Explored Path Logging:
During interactive play, the global coordinates visited on each map are recorded and saved to explored_paths.json. This can be valuable for generating or refining the warp_paths.json.
Refinements and Debugging:
We've added more detailed debug printing during path execution, showing current map ID, local and global coordinates.
The script now handles PyBoy cleanup more consistently across different execution modes (command-line pathing vs. interactive).
The screen is explicitly updated after interactive warp attempts to clear any console input text.
Overall Plan (Inferred and Stated):
Autonomous Pokemon Playing AI: The ultimate goal is to develop an AI that can play Pokemon Red with a significant degree of autonomy. This involves decision-making for battles, exploration, item management, and progressing through the game's storyline.
Robust Game Interfacing:
Environment: The red_gym_env_v2.py (used by warp_runner.py and play.py) serves as the core environment, likely leveraging PyBoy to interact with the Pokemon Red ROM.
Memory Reading: agent/memory_reader.py is a critical component. It provides a rich, structured way to read and interpret the game's memory. This allows the AI to understand the current game state in detail (player party, enemy Pokemon, items, location, game events, badges, money, dialogs, etc.). This detailed state information is fundamental for any sophisticated AI agent.
Intelligent Navigation: As we've been working on, enabling the AI to navigate the game world effectively is paramount. This includes:
Following predefined paths (warp_runner.py).
Potentially, more dynamic pathfinding or exploration algorithms in the future.
Modular and LLM-Friendly Codebase: A key requirement is that the entire system should be easily understandable, modifiable, and maintainable, particularly by an LLM coding assistant (like me). This implies:
Clear, well-commented (where non-obvious) code.
Logical separation of concerns (e.g., memory reading, environment interaction, agent logic).
Well-defined interfaces between modules.
Configuration and Data Management:
config.py likely holds settings for the environment and agent.
pokered.sym is used for mapping symbolic names to memory addresses, crucial for memory_reader.py.
Various JSON files (events.json, warp_paths.json, explored_paths.json) store game data, paths, and recorded information.
Checkpoint directories (bin/checkpoints_*) suggest a mechanism for saving/loading game states, which is vital for testing, debugging, and potentially for AI training (e.g., starting from specific game events).
Agent Logic (Future): While not explicitly worked on yet, the "agent" directory and the detailed information from memory_reader.py point towards the future development of the AI's brain/decision-making logic.
Current Tasks (Testing Pathing Functions):
Our immediate focus is to thoroughly test and solidify the pathing functionalities in warp_runner.py:
Verification of execute_warp_to_coords:
Ensure it reliably navigates to a variety of target coordinates across different maps.
Test edge cases: paths that are very short, very long, or involve tricky sequences of movements.
Confirm correct handling when a path to the target coordinates isn't found or if the player is on a map not covered in warp_paths.json.
Accuracy of Coordinate Systems:
Double-check the consistency and correct usage of local game coordinates versus global coordinates throughout the pathing logic.
Ensure env.get_game_coords() and env.get_global_coords() are providing accurate data that aligns with warp_paths.json.
Interactive Pathing ('W' key):
Test the interactive warp input and execution flow.
Ensure the game state is correctly paused and resumed.
Verify screen updates and player control after the warp attempt (success or failure).
Command-Line Pathing:
Test pathing triggered by --glob_x and --glob_y arguments.
Confirm it runs to completion and exits as expected.
explored_paths.json Generation:
Verify that interactive movement correctly logs coordinates to this file.
Assess if the format and content are suitable for later use (e.g., to help generate or update warp_paths.json).
Error Handling and Robustness:
Identify and address any potential crashes or unexpected behavior during path execution (e.g., player getting stuck, game events interrupting pathing).
Ensure graceful failure messages if pathing cannot be completed.
Additional Points to Consider for the Overall Plan:
Decision-Making Framework: How will the AI make decisions? This could involve rule-based systems, search algorithms (like MCTS), or machine learning models. The memory_reader.py provides the necessary input for such a framework.
Exploration Strategy: Beyond following predefined paths, how will the AI explore new areas or find objectives?
Battle Logic: Developing a competent battle AI will be a significant sub-project, leveraging the detailed battle information from memory_reader.py.
User Interface/Visualization: While warp_runner.py uses Pygame for display, further tools for visualizing the AI's state, decisions, or the game world as the AI sees it might be beneficial (e.g., tile_visualizer.py).
This summary should provide a good foundation for our continued work. Please let me know if you'd like to refine or add anything to this understanding!

New state variables in ReplayExtender to track the current navigation goal (target 9x10 grid coordinates), the planned path (list of actions), and the overall navigation status (e.g., "idle", "planning", "navigating", "completed", "failed").
A method set_navigation_goal(target_grid_row, target_grid_col) to initiate a pathfinding task.
A core step() method in ReplayExtender. This method will:
If in a "planning" state, call find_the_navigational_path() to generate the sequence of moves and transition to "navigating".
If in a "navigating" state, execute the next move from the stored path using pyboy.button_press() and pyboy.tick() for appropriate durations.
Update the navigation status accordingly (e.g., to "completed" or "failed").
A reset_navigation() method to clear the current goal and path.
Then, I'll modify the main() function in recorder/replay.py to use this new system:
In the interactive mode, the 'W' key will be repurposed to prompt for target 9x10 grid coordinates and then call extender.set_navigation_goal().
The main interactive loop will continuously call extender.step() and print its status, allowing you to observe the pathfinding process.
A new key (e.g., 'R') will be added to call extender.reset_navigation().
The existing pyboy.tick() and screen update logic in the main loop will ensure the game continues to render and progress.


