import os, sys
# Add paths to import from agent and project root
agent_dir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.abspath(os.path.join(agent_dir, os.pardir))
sys.path.insert(0, project_root)
sys.path.insert(0, agent_dir)

import pytest
from path_planner import CriticalPathPlanner

def test_get_next_step_returns_location_action_next_zone():
    planner = CriticalPathPlanner()
    # Override mapping directly for test with full location text
    planner.mapping = {'loc1': ('Loc1Description', 'act1', 'loc2')}

    loc_text, action, next_zone = planner.get_next_step('Loc1')
    assert loc_text == 'Loc1Description'
    assert action == 'act1'
    assert next_zone == 'loc2'


def test_get_next_step_case_and_whitespace_insensitive():
    planner = CriticalPathPlanner()
    planner.mapping = {'loc1': ('Loc1Description', 'act1', 'loc2')}

    loc_text, action, next_zone = planner.get_next_step('  LOC1  ')
    assert loc_text == 'Loc1Description'
    assert action == 'act1'
    assert next_zone == 'loc2'


def test_get_next_step_missing_returns_explore_none():
    planner = CriticalPathPlanner()
    planner.mapping = {}

    loc_text, action, next_zone = planner.get_next_step('any_location')
    assert loc_text == 'any_location'
    assert action == 'explore'
    assert next_zone is None 