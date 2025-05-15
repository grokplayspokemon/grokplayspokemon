import os, sys
# Ensure agent and project root on path
agent_dir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.abspath(os.path.join(agent_dir, os.pardir))
sys.path.insert(0, project_root)
sys.path.insert(0, agent_dir)

import pytest
from path_planner import CriticalPathPlanner

@ pytest.fixture(scope='module')
def planner():
    return CriticalPathPlanner()

@ pytest.mark.parametrize("variant", [lambda k: k,
                                      lambda k: k.upper(),
                                      lambda k: k.capitalize(),
                                      lambda k: k.strip(),
                                      lambda k: f"  {k}  ",
                                      lambda k: k.title()])
def test_mapping_case_and_whitespace_insensitive(planner, variant):
    # For every normalized key in the mapping, get_next_step should return the stored loc_text, action and next_zone
    for norm_key, expected in planner.mapping.items():
        loc_text_expected, action_expected, next_zone_expected = expected
        input_key = variant(norm_key)
        loc_text_out, action_out, next_zone_out = planner.get_next_step(input_key)
        assert loc_text_out == loc_text_expected, f"Location text mismatch for '{input_key}': expected '{loc_text_expected}', got '{loc_text_out}'"
        assert action_out == action_expected, f"Action mismatch for '{input_key}': expected '{action_expected}', got '{action_out}'"
        assert next_zone_out == next_zone_expected, f"Next zone mismatch for '{input_key}': expected '{next_zone_expected}', got '{next_zone_out}'"


def test_missing_location_fallback(planner):
    # A completely unknown location should return (input, 'explore', None)
    loc_text, action, next_zone = planner.get_next_step('This location does not exist')
    assert loc_text == 'This location does not exist'
    assert action == 'explore'
    assert next_zone is None

def test_route3_inline_instruction(planner):
    # Route 3 should have travel east then north action and next arrow instruction
    loc_text, action, next_zone = planner.get_next_step('Route 3')
    assert loc_text == 'Route 3 (Travel East, then North)'
    assert action == 'Travel East, then North'
    assert next_zone == 'At the end of Route 3, keep going North to find Route 4'

def test_route4_inline_instruction(planner):
    # Route 4 should have travel north to Poke Center action and next arrow instruction
    loc_text, action, next_zone = planner.get_next_step('Route 4')
    assert loc_text == 'Route 4 (Travel North to Poke Center, then enter Mt Moon)'
    assert action == 'Travel North to Poke Center, then enter Mt Moon'
    assert next_zone == 'Enter Mt Moon to the North to the left of the Poke Center' 