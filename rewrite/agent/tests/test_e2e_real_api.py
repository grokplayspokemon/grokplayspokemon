import os
import pytest
from grok_agent import GrokAgent


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get('XAI_API_KEY'),
    reason="requires XAI_API_KEY for real API calls"
)
def test_e2e_get_action_returns_valid_tool():
    # Initialize agent with real API key from environment
    agent = GrokAgent()
    agent.initialize(None, None, None)

    # Minimal dummy game state
    dummy_state = {
        "coords": [0, 0, 0],
        "global_coords": [0, 0],
        "current_quest": None,
        "quest_status": {},
        "screen": []
    }
    action = agent.get_action(dummy_state)

    # Basic structure
    assert isinstance(action, dict), "Action must be a dict"
    assert "name" in action and "args" in action, "Missing keys in action"

    # Must be one of our two tools
    assert action["name"] in ["press_next", "press_button"]

    if action["name"] == "press_next":
        assert action["args"] == {}, "press_next must have empty args"
    else:
        # press_button: valid button enum
        args = action["args"]
        assert isinstance(args, dict), "Arguments must be a dict"
        assert "button" in args, "Missing 'button' arg"
        assert args["button"] in ["UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START"]


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get('XAI_API_KEY'),
    reason="requires XAI_API_KEY for real API calls"
)
def test_e2e_follow_up_consistent_response():
    # Run two sequential calls and ensure deterministic shape
    agent = GrokAgent()
    agent.initialize(None, None, None)
    state1 = {"coords": [0, 0, 0], "global_coords": [0, 0], "current_quest": None, "quest_status": {}, "screen": []}
    state2 = {"coords": [1, 0, 0], "global_coords": [1, 0], "current_quest": None, "quest_status": {}, "screen": []}

    action1 = agent.get_action(state1)
    action2 = agent.get_action(state2)

    # Both should be valid tool calls (no errors)
    for act in (action1, action2):
        assert act["name"] in ["press_next", "press_button"]
        assert isinstance(act["args"], dict) 