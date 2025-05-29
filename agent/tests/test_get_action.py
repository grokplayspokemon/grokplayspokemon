import pytest
import json
from grok_agent import GrokAgent
# Stub out reasoning and usage extraction
import grok_agent
from jsonschema import ValidationError
from tools.press_next import press_next_tool
from tools.press_button import press_button_tool

class DummyFuncCall:
    def __init__(self, name, args):
        self.name = name
        self.arguments = json.dumps(args)

class DummyMessage:
    def __init__(self, func_call):
        self.function_call = func_call

class DummyChoice:
    def __init__(self, msg):
        self.message = msg

class DummyResponse:
    def __init__(self, name, args):
        self.choices = [DummyChoice(DummyMessage(DummyFuncCall(name, args)))]


def test_get_action_valid(monkeypatch):
    agent = GrokAgent()
    agent.model = 'test-model'
    # Stub API call
    dummy = DummyResponse('press_button', {'button': 'A'})
    monkeypatch.setattr(grok_agent, 'extract_reasoning', lambda resp: 'reason')
    monkeypatch.setattr(grok_agent, 'extract_usage_metrics', lambda resp: {'usage': 1})
    monkeypatch.setattr(agent, '_call_api', lambda messages, tools: dummy)

    dummy_state = {'coords': [0,0,0], 'global_coords': [0,0], 'current_quest': None, 'quest_status': {}, 'screen': []}
    result = agent.get_action(dummy_state)
    assert result == {'name': 'press_button', 'args': {'button': 'A'}}
    # Validate last_reasoning and last_usage set
    assert agent.last_reasoning == 'reason'
    assert agent.last_usage == {'usage': 1}


def test_get_action_invalid_json_args(monkeypatch):
    agent = GrokAgent()
    agent.model = 'test-model'
    # Create a func_call with invalid JSON
    class BadFuncCall:
        def __init__(self):
            self.name = 'press_next'
            self.arguments = '{bad json'
    bad_msg = DummyMessage(BadFuncCall())
    bad_resp = type('R', (), {'choices': [DummyChoice(bad_msg)]})
    monkeypatch.setattr(agent, '_call_api', lambda messages, tools: bad_resp)

    with pytest.raises(ValueError):
        agent.get_action({}) 

def test_get_action_unknown_function_name(monkeypatch):
    agent = GrokAgent()
    agent.model = 'test-model'
    # Create a func_call with unknown name
    class UnknownFuncCall:
        def __init__(self):
            self.name = 'bad_func'
            self.arguments = json.dumps({})
    bad_msg = DummyMessage(UnknownFuncCall())
    bad_resp = type('R', (), {'choices': [DummyChoice(bad_msg)]})
    monkeypatch.setattr(agent, '_call_api', lambda messages, tools: bad_resp)

    with pytest.raises(ValidationError):
        agent.get_action({}) 