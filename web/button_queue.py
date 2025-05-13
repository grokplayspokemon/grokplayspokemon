# Module to unify Dev-mode and Agent-mode button processing
import asyncio
import logging

# Unbounded queue to serialize button presses
queue = asyncio.Queue()

# Will hold the original emulator.press_buttons implementation
t_orig_press = None

def set_orig_press(fn):
    global t_orig_press
    t_orig_press = fn

async def process_next_button(source, agent, send_game_updates):
    """
    Drain exactly one button from the queue, invoke the real press,
    capture a frame, and broadcast via send_game_updates.
    """
    global t_orig_press
    if t_orig_press is None:
        raise RuntimeError("Original press_buttons not set")

    # Get the next button to process
    button = await queue.get()
    # DEBUG: log retrieval
    logging.getLogger(__name__).info(f"process_next_button: dequeueing button {button} from {source}")

    # If a dialog/menu is active and this is a directional press, skip it
    try:
        # Skip directional inputs only during non-battle dialogs/menus
        if agent.emulator.get_active_dialog() and not agent.emulator.is_in_battle() and button in ["up", "down", "left", "right"]:
            logging.getLogger(__name__).info(f"process_next_button: Skipping directional button '{button}' during non-battle dialog")
            queue.task_done()
            return
    except Exception as e:
        logging.getLogger(__name__).error(f"process_next_button: Error checking dialog state: {e}")

    # Actually press via the original
    t_orig_press([button], True)

    # Step the emulator to apply the button press
    try:
        agent.emulator.step()
    except Exception as e:
        logging.getLogger(__name__).error(f"button_queue.py: Error stepping emulator: {e}")
        pass

    # Capture and send the resulting screen
    try:
        frame = agent.get_frame()
    except Exception as e:
        logging.getLogger(__name__).error(f"button_queue.py: Error getting frame: {e}")
        frame = None
    # Send the frame (None if error, to allow client redraw)
    await send_game_updates(frame, f"{source}: pressed {button}")
    
    # Mark task done
    queue.task_done() 