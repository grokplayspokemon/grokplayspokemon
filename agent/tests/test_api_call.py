import pytest
from grok_agent import GrokAgent
from tools.press_next import press_next_tool
from jsonschema import ValidationError

def test_call_api_logs_and_returns(monkeypatch, caplog, tmp_path):
    # Set up agent with dummy client
    agent = GrokAgent()
    agent.model = 'test-model'
    # Dummy response object
    class DummyResponse:
        def __repr__(self):
            return '<DummyResponse>'
    dummy_response = DummyResponse()
    # Monkey-patch client.chat.completions.create
    class DummyCompletions:
        def create(self, **kwargs):
            return dummy_response
    class DummyChat:
        completions = DummyCompletions()
    agent.client = type('C', (), {'chat': DummyChat()})

    # Valid messages and tools
    messages = [{'role': 'system', 'content': 'hi'}]
    tools = [press_next_tool]

    caplog.set_level('INFO')
    # Call the API
    result = agent._call_api(messages, tools)

    # Assert return value
    assert result is dummy_response
    # Assert logs contain request and response
    log_text = caplog.text
    assert 'API request:' in log_text
    assert 'model=test-model' in log_text
    assert 'API raw response:' in log_text

    # Assert that validation rejects bad messages
    with pytest.raises(ValidationError):
        agent._call_api({'not': 'a list'}, tools)
    with pytest.raises(ValidationError):
        agent._call_api(messages, {'not': 'a list'}) 