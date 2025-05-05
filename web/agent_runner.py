import logging
logger = logging.getLogger(__name__)

import os
from PIL import Image
import asyncio

async def run_agent(agent, num_steps, run_log_dir, send_game_updates, grok_logger):
    try:
        logger.info(f"Starting agent for {num_steps} steps")
        # Auto-press Start to exit title screen
        try:
            agent.emulator.press_buttons(["start"], True)
            # Always capture frame for UI, regardless of screenshot setting
            frame = agent.get_frame()
            await send_game_updates(frame, "Auto-pressed Start to begin the game")
            grok_logger.info("Auto-pressed Start to begin the game")
            # Save frame to disk only if screenshots are enabled
            if getattr(agent, 'use_screenshots', False):
                # frames folder already exists
                frame_path = os.path.join(run_log_dir, "frames", "frame_00000.png")
                with open(frame_path, "wb") as f:
                    f.write(frame)
        except Exception as e:
            logger.error(f"Error auto-pressing start: {e}")
        steps_completed = 0
        
        while steps_completed < num_steps:
            # Handle pause state
            while getattr(agent.app.state, 'is_paused', False):
                await asyncio.sleep(0.1)
                continue

            # Execute one agent step (Grok prompt + action)
            agent.step()
            steps_completed += 1

            # Capture the frame and optionally save to disk
            frame = agent.get_frame()
            if getattr(agent, 'use_screenshots', False):
                frame_path = os.path.join(run_log_dir, "frames", f"frame_{steps_completed:05d}.png")
                with open(frame_path, "wb") as f:
                    f.write(frame)

            # Send the updated frame and Grok's latest response
            message = agent.get_last_message()
            if message:
                grok_logger.info(message)
            await send_game_updates(frame, message)

            # Insert lag time between response/action and next prompt
            await asyncio.sleep(agent.step_delay)
            
        logger.info(f"Agent completed {steps_completed} steps")
    except asyncio.CancelledError:
        logger.info("Agent task was cancelled")
        raise
    except Exception as e:
        logger.error(f"Error running agent: {e}")
        # Swallow exception to keep server alive and end agent loop gracefully
        return