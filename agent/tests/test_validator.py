import pytest
from validator import validate_messages, validate_tools, validate_function_call
from jsonschema import ValidationError
from tools.press_next import press_next_tool
from tools.press_button import press_button_tool


def test_validate_messages_valid():
    msgs = [{"role": "system", "content": "hello"}]
    # should not raise
    validate_messages(msgs)


def test_validate_messages_invalid_type():
    with pytest.raises(ValidationError):
        validate_messages({"role": "system", "content": "hello"})


def test_validate_messages_missing_field():
    with pytest.raises(ValidationError):
        validate_messages([{"role": "system"}])


def test_validate_tools_valid():
    # single valid tool definitions
    validate_tools([press_next_tool, press_button_tool])


def test_validate_tools_invalid_type():
    with pytest.raises(ValidationError):
        validate_tools(press_next_tool)


def test_validate_tools_invalid_schema():
    with pytest.raises(ValidationError):
        validate_tools([{"type": "function"}])


def test_validate_function_call_valid_press_next():
    call = {"name": "press_next", "arguments": {}}
    validate_function_call(call)


def test_validate_function_call_valid_press_button():
    call = {"name": "press_button", "arguments": {"button": "A"}}
    validate_function_call(call)


def test_validate_function_call_unknown_name():
    with pytest.raises(ValidationError):
        validate_function_call({"name": "foo", "arguments": {}})


def test_validate_function_call_bad_args():
    # missing required 'button'
    with pytest.raises(ValidationError):
        validate_function_call({"name": "press_button", "arguments": {}}) 