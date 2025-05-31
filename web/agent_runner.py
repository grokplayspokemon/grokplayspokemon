import logging
import os
from PIL import Image
import asyncio
from dataclasses import asdict # For converting GameState to dict
from pathlib import Path

# Imports from the existing environment
from environment.play import get_default_config # For env configuration
from environment.wrappers.env_wrapper import EnvWrapper
from environment.environment import VALID_ACTIONS, PATH_FOLLOW_ACTION
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.environment_helpers.quest_manager import QuestManager

# Import the new GameState and extraction function
from environment.grok_integration import GameState, extract_structured_game_state
# Assuming PokemonRedReader is in agent.memory_reader
from agent.memory_reader import PokemonRedReader
# Import SimpleAgent - it will now act more as a controller/LLM interface
from agent.simple_agent import SimpleAgent


logger = logging.getLogger(__name__)

# Define project root for config paths
project_root_path = Path(__file__).resolve().parent.parent 

async def run_agent( # Note: 'agent' param is now the LLM-wrapping SimpleAgent
                    simple_agent: SimpleAgent, # Renamed for clarity
                    app_state, # For pause state
                    num_steps, 
                    run_log_dir, 
                    send_game_updates, 
                    claude_logger):
    
    env_wrapper = None # Define in outer scope for finally block
    try:
        logger.info(f"Starting agent runner for {num_steps} steps with pre-initialized SimpleAgent")

        # 1. Initialize the game environment (EnvWrapper)
        # The simple_agent is already initialized by app.py with its configs.
        # We only need to set up the game environment here.
        
        # Load full config, which includes env and agent sections
        # get_default_config loads from config.yaml and can be overridden by args
        # We need the rom_path and initial_state_path for the environment config
        rom_path_abs = str(project_root_path / "pokered.gb")
        # Try to get initial_state_path from config, fallback to default
        # This assumes get_default_config returns an object that allows attribute access
        # or is a dict. If it's an OmegaConf object, this is fine.
        raw_config = get_default_config(rom_path=rom_path_abs) # Load base config

        default_initial_state = str(project_root_path / "initial_states" / "init.state")
        initial_state_path_abs = raw_config.env.get("override_init_state") or default_initial_state
        if not Path(initial_state_path_abs).is_file() and not raw_config.env.get("override_init_state"):
             logger.warning(f"Default initial state {initial_state_path_abs} not found. Ensure 'override_init_state' is set in config.yaml if needed, or place init.state.")
             # Potentially use a known good state or raise error if essential

        required_completions_path_abs = str(project_root_path / "environment" / "environment_helpers" / "required_completions.json")

        # Construct the environment config for EnvWrapper
        # We pass specific overrides to get_default_config for env settings.
        # The raw_config.env will be the source for most env settings.
        env_config_for_wrapper = get_default_config(
            rom_path=rom_path_abs,
            initial_state_path=initial_state_path_abs,
            disable_ai_actions=False, # Agent will provide actions
            # other args here will override yaml and then defaults in get_default_config
            # For most, we rely on config.yaml or defaults within get_default_config
        )

        env_wrapper = EnvWrapper(env_config_for_wrapper) # Pass the OmegaConf object/dict directly
        logger.info("EnvWrapper initialized.")

        reader = PokemonRedReader(env_wrapper.pyboy.mb)
        logger.info("PokemonRedReader initialized.")

        navigator = InteractiveNavigator(env_wrapper, use_global_map=env_config_for_wrapper.env.use_global_map)
        quest_manager = QuestManager(env_wrapper, navigator, required_completions_path_abs)
        logger.info("Navigator and QuestManager initialized.")

        # Crucially, pass the initialized environment components to the SimpleAgent instance
        # This assumes SimpleAgent has a method to accept these or was designed to get them this way.
        # Based on previous edits, SimpleAgent __init__ accepts these.
        # However, simple_agent is already initialized in app.py. 
        # So, these components must be passed to SimpleAgent when it's created in app.py.
        # This function (run_agent) should NOT re-initialize or set these on an existing SimpleAgent instance
        # unless SimpleAgent has specific setter methods for this purpose post-init.
        # The current SimpleAgent.__init__ takes these. So, app.py must handle it.

        # This means the simple_agent passed to this function must ALREADY have these references.
        # If not, SimpleAgent will not function correctly with its tools.
        # Let's assume app.py correctly initializes SimpleAgent with these or similar objects derived from the config.
        # For run_agent to work, simple_agent needs its env_wrapper, reader, etc. set.
        # Let's check if the passed simple_agent has them, if not, log a warning.
        if not simple_agent.env_wrapper or not simple_agent.reader or not simple_agent.quest_manager or not simple_agent.navigator:
            logger.error("SimpleAgent was passed to run_agent without its environment components (env_wrapper, reader, etc.) initialized. Tools will likely fail. These should be set during SimpleAgent creation in app.py.")
            # Potentially raise an error here, or try to set them if SimpleAgent allows it (not ideal)
            # For now, we proceed, but this is a critical point for app.py to handle correctly.
            # A possible fix is for SimpleAgent to have a `set_environment_components` method if initialized early.
            # simple_agent.set_environment_components(env_wrapper, reader, quest_manager, navigator)

        # Start continuous frame streamer for full framerate
        async def frame_streamer():
            try:
                while True:
                    # Get frame directly from EnvWrapper
                    frame_pil = env_wrapper.render_obs() # Assuming this returns a PIL image or similar
                    if frame_pil:
                        # Convert PIL Image to bytes (PNG)
                        import io
                        img_byte_arr = io.BytesIO()
                        frame_pil.save(img_byte_arr, format='PNG')
                        frame_bytes = img_byte_arr.getvalue()
                        await send_game_updates(frame_data=frame_bytes, game_state_update=None, claude_message=None)
                    await asyncio.sleep(1/30)  # ~30 FPS
            except asyncio.CancelledError:
                logger.info("Frame streamer cancelled.")
                return
            except Exception as e:
                logger.error(f"Frame streamer error: {e}", exc_info=True)
                return

        frame_task = asyncio.create_task(frame_streamer())
        steps_completed = 0
        
        # Main agent loop
        while steps_completed < num_steps:
            # Pause handling
            while getattr(app_state, 'is_paused', False): # Use app_state for pause
                await asyncio.sleep(0.1)

            # --- 1. Get current game state for the LLM ---
            # Get screenshot from env_wrapper
            # current_raw_frame_pil = env_wrapper.render() # This might be a full screen render for pygame
            current_screen_obs_pil = env_wrapper.render_obs() # Gets the observation PIL image

            # Convert PIL to bytes for SimpleAgent
            img_byte_arr = io.BytesIO()
            current_screen_obs_pil.save(img_byte_arr, format='PNG')
            current_frame_bytes = img_byte_arr.getvalue()
            
            # Extract structured state
            try:
                current_structured_state = extract_structured_game_state(env_wrapper, reader, quest_manager)
            except Exception as e:
                logger.error(f"Error extracting structured game state for LLM: {e}", exc_info=True)
                current_structured_state = GameState(location={"map_name":"Error"}, quest_id=None, party=[], dialog="Error extracting state", in_battle=False, hp_fraction=0, money=0, badges=0, pokedex_seen=0, pokedex_caught=0, steps=0, items=[])

            # --- 2. Run the SimpleAgent's decision logic ---
            # SimpleAgent's step method will now use the provided frame and structured_state
            # and internally call the LLM. It should return an action.
            action_from_agent = simple_agent.step(current_frame_bytes, current_structured_state)

            # --- 3. Execute the action in the environment ---
            if action_from_agent is not None:
                # The action from SimpleAgent should be one of the VALID_ACTIONS indices
                # or a special action like PATH_FOLLOW_ACTION if simple_agent handles that.
                # For now, assume SimpleAgent returns a valid discrete action for env_wrapper.step()
                obs, reward, terminated, truncated, info = env_wrapper.step(action_from_agent)
                # Handle termination/truncation if necessary
                if terminated or truncated:
                    logger.info(f"Environment terminated or truncated at step {steps_completed}.")
                    break 
            else:
                # If agent provides no action, we might tick or take a default
                env_wrapper.pyboy.tick() # Keep emulator ticking
                logger.warning("SimpleAgent returned no action. Ticking environment.")


            steps_completed += 1

            # --- 4. Capture the new frame AFTER the action for UI and logging ---
            # This frame is for UI update and logging, distinct from the one fed to LLM
            ui_frame_pil = env_wrapper.render_obs()
            ui_frame_bytes = None
            if ui_frame_pil:
                img_byte_arr_ui = io.BytesIO()
                ui_frame_pil.save(img_byte_arr_ui, format='PNG')
                ui_frame_bytes = img_byte_arr_ui.getvalue()

                # Log frame to disk
                frame_count = steps_completed
                # Ensure "frames" directory exists in run_log_dir
                frames_log_dir = os.path.join(run_log_dir, "frames")
                os.makedirs(frames_log_dir, exist_ok=True)
                frame_path = os.path.join(frames_log_dir, f"frame_{frame_count:05d}.png")
                try:
                    with open(frame_path, "wb") as f:
                        f.write(ui_frame_bytes)
                except Exception as e:
                    logger.error(f"Failed to save frame to disk: {e}")


            # --- 5. Compute structured game state for UI ---
            # This is after the action has been taken
            ui_structured_game_state = None
            try:
                ui_structured_game_state = extract_structured_game_state(env_wrapper, reader, quest_manager)
            except Exception as e:
                logger.error(f"Error extracting UI structured game state: {e}", exc_info=True)
                ui_structured_game_state = GameState(location={"map_name":"Error"}, quest_id=None, party=[], dialog="Error extracting state", in_battle=False, hp_fraction=0, money=0, badges=0, pokedex_seen=0, pokedex_caught=0, steps=0, items=[])
            
            ui_game_state_dict = asdict(ui_structured_game_state) if ui_structured_game_state else {}


            # --- 6. Send model "thought" (assistant reply) produced in this step ---
            # This comes from simple_agent.get_last_message()
            llm_message = simple_agent.get_last_message() or ''
            thought_msg = llm_message.strip()
            if thought_msg:
                claude_logger.info(thought_msg) # Log LLM thought
            
            # Send updates: frame, thought, and structured game state
            # The frame streamer sends frames frequently; this sends the LLM message and associated state.
            # Avoid resending the exact same message if nothing changed.
            # We send game state with each LLM message.
            if thought_msg or ui_frame_bytes: # Send if there's a new thought or a new frame
                 # Check if the message is genuinely new to avoid spamming UI with same thought
                if thought_msg != getattr(run_agent, "_last_sent_thought_msg", None) or ui_game_state_dict != getattr(run_agent, "_last_sent_game_state", {}):
                    await send_game_updates(frame_data=ui_frame_bytes, game_state_update=ui_game_state_dict, claude_message=thought_msg)
                    run_agent._last_sent_thought_msg = thought_msg
                    run_agent._last_sent_game_state = ui_game_state_dict.copy()

            
            # --- 7. Handle tool results (if any) ---
            # SimpleAgent should expose tool results if they need special handling/logging for UI
            if hasattr(simple_agent, 'last_tool_message') and simple_agent.last_tool_message:
                raw_tool_msg = simple_agent.last_tool_message or ''
                # ... (concise tool message logic from original, if still needed) ...
                # For now, let's assume tool results are part of the main LLM thought or logged separately
                # If a special UI update is needed for tool results:
                # concise_tool_msg = "Tool result: " + raw_tool_msg # Simple version
                # claude_logger.info(concise_tool_msg) # Log tool result specifically
                # tool_frame_pil = env_wrapper.render_obs()
                # tool_frame_bytes = None
                # if tool_frame_pil:
                #     # ... convert to bytes ...
                # tool_game_state = extract_structured_game_state(...)
                # tool_game_state_dict = asdict(tool_game_state)
                # await send_game_updates(frame_data=tool_frame_bytes, game_state_update=tool_game_state_dict, claude_message=concise_tool_msg)
                
                simple_agent.last_tool_message = None # Clear after processing
            
            await asyncio.sleep(0.1) # Control loop timing, adjust as needed

    except asyncio.CancelledError:
        logger.info("Agent runner task was cancelled")
    except Exception as e:
        logger.error(f"Error running agent runner: {e}", exc_info=True)
        # Consider sending an error message to the UI if possible
        await send_game_updates(claude_message=f"AGENT RUNNER ERROR: {e}")
        # Do not re-raise here to allow finally block to clean up
    finally:
        logger.info("Agent runner finally block reached.")
        if 'frame_task' in locals() and frame_task and not frame_task.done():
            logger.info("Cancelling frame_task.")
            frame_task.cancel()
            try:
                await frame_task
            except asyncio.CancelledError:
                logger.info("Frame_task successfully cancelled.")
            except Exception as e:
                logger.error(f"Error during frame_task cleanup: {e}")
        
        if env_wrapper is not None:
            logger.info("Closing EnvWrapper.")
            env_wrapper.close() # Ensure environment resources are released
            
        logger.info(f"Agent runner completed {steps_completed if 'steps_completed' in locals() else 'unknown'} steps")