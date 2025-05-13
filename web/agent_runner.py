# agent_runner.py
import asyncio
import logging
import time
import io
from pathlib import Path

# Get the consolidated game logger
logger = logging.getLogger("game")

async def run_agent(agent, num_steps=100000, run_log_dir=None, send_game_updates=None, grok_logger=None):
    """Run the agent with improved WebSocket management."""
    import time
    
    logger = logging.getLogger(__name__)
    
    # Clear the main game log file at the start of a new run
    main_log_file_path = Path("llm_plays_pokemon/DATAPlaysPokemon/game.log")
    try:
        if main_log_file_path.exists():
            with open(main_log_file_path, 'w') as f:
                f.write("") # Clear the file
            logger.info(f"Cleared main log file: {main_log_file_path}")
    except Exception as e:
        logger.error(f"Error clearing main log file {main_log_file_path}: {e}")
    
    logger.info(f"Starting agent for {num_steps} steps")
    logger.info("Agent task started.")
    agent.running = True
    step_count = 0
    
    # Enqueue start press via patched press_buttons
    agent.emulator.press_buttons(["start"], True)
    # Step emulator without consuming queue to allow initial manual processing
    agent.emulator.step()
    # Immediately drain any queued start press to apply it before sending frame
    from web.button_queue import queue, process_next_button
    while not queue.empty():
        await process_next_button("Agent-init", agent, send_game_updates)
    
    # Send initial frame update (if not already sent during init processing)
    if send_game_updates:
        try:
            frame = agent.get_frame()
            location = agent.emulator.get_location() or "Unknown"
            await send_game_updates(frame, f"Game started in {location}")
        except Exception as e:
            logger.error(f"Error sending initial frame: {e}")
    
    # # Auto-press Start to begin the game if needed
    # if hasattr(agent, 'emulator'):
    #     logger.info("Auto-pressing Start to begin the game")
    #     agent.emulator.press_buttons(["start"], True)
    #     agent.emulator.step()
        
    #     if grok_logger:
    #         grok_logger.info("Auto-pressed Start to begin the game")
            
    #     # Send initial frame update
    #     if send_game_updates:
    #         try:
    #             frame = agent.get_frame()
    #             location = agent.emulator.get_location() or "Unknown"
    #             await send_game_updates(frame, f"Game started in {location}")
    #         except Exception as e:
    #             logger.error(f"Error sending initial frame: {e}")
        
    #     # Wait 3 seconds after pressing Start
    #     logger.info("Waiting 3 seconds after pressing Start")
    #     await asyncio.sleep(3.0)
    
    # Main agent loop
    while agent.running and step_count < num_steps:
        try:
            # Check if agent is paused (dev mode)
            if hasattr(agent, 'app') and getattr(agent.app.state, 'is_paused', False):
                await asyncio.sleep(0.1)
                continue
            
            # Sleep before the next step
            logger.info(f"run_agent: sleeping for {agent.step_delay}s before step {step_count+1}")
            await asyncio.sleep(agent.step_delay)
            
            # Check if paused again after delay to avoid stepping during Dev mode
            if hasattr(agent, 'app') and getattr(agent.app.state, 'is_paused', False):
                logger.info(f"run_agent paused after delay before step {step_count+1}")
                continue
            
            # Execute the step
            logger.info(f"Executing step {step_count+1}")
            start_time = time.time()
            
            try:
                # Execute the step with forced render flag
                agent.step(force_render=True)
                # After the agent enqueues presses, process all queued presses from this step
                from web.button_queue import queue, process_next_button
                while not queue.empty():
                    await process_next_button("Agent", agent, send_game_updates)
                step_count += 1
                
                # Get the latest message and update
                latest_message = agent.get_last_message()
                
                # Log the message
                if latest_message:
                    logger.info(f"Agent message: {latest_message}")
                    if grok_logger and grok_logger != logger:
                        grok_logger.info(latest_message)
                
                # CRITICAL UPDATE: Force final frame rendering with synchronization delay
                if send_game_updates:
                    try:
                        # Add delay for state stabilization before final frame capture
                        await asyncio.sleep(0.1)
                        frame = agent.get_frame()
                        if frame:
                            # Send both the frame and message to UI
                            await send_game_updates(frame, latest_message or "Game update")
                            # Add synchronization delay after frame render
                            await asyncio.sleep(0.2)
                    except Exception as e:
                        logger.error(f"Error sending game update: {e}")
                
                # Calculate and log the elapsed time
                elapsed = time.time() - start_time
                logger.info(f"Step {step_count} completed in {elapsed:.2f}s")
                
            except Exception as e:
                logger.error(f"Error in step {step_count}: {e}", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            
        except asyncio.CancelledError:
            logger.info("Agent task was cancelled")
            break
            
        except Exception as e:
            logger.error(f"Unexpected error in run_agent: {e}", exc_info=True)
            await asyncio.sleep(1.0)
    
    logger.info(f"Agent run completed after {step_count} steps")
    logger.info("Agent task finished.")
    return {"steps_completed": step_count}