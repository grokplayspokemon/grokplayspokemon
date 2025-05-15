import os, sys
# Add paths to import memory_reader (from agent) and game_data (from project root)
agent_dir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.abspath(os.path.join(agent_dir, os.pardir))
# Prepend project root and agent directory
sys.path.insert(0, project_root)
sys.path.insert(0, agent_dir)
import pytest
from memory_reader import PokemonRedReader


def test_clear_dialog_buffer_overwrites_with_space():
    # Define buffer boundaries from memory_reader
    buffer_start = 0xC3A0
    buffer_end = 0xC507

    # Create a fake memory list large enough for the buffer
    size = buffer_end + 1
    fake_memory = [0x00] * size

    # Pre-fill the dialog region with a non-space value to simulate stale data
    for addr in range(buffer_start, buffer_end + 1):
        fake_memory[addr] = 0x01

    # Initialize reader with fake memory
    reader = PokemonRedReader(fake_memory)

    # Call clear_dialog_buffer
    reader.clear_dialog_buffer()

    # Assert that every address in the dialog region has been set to 0x7F (space)
    for addr in range(buffer_start, buffer_end + 1):
        assert fake_memory[addr] == 0x7F, f"Address {hex(addr)} was not cleared." 