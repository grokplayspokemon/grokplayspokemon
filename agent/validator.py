import os
import json
from jsonschema import validate, ValidationError

# Load JSON schemas
_schema_dir = os.path.join(os.path.dirname(__file__), 'schemas')
with open(os.path.join(_schema_dir, 'message.schema.json')) as f:
    message_schema = json.load(f)
with open(os.path.join(_schema_dir, 'tool.schema.json')) as f:
    tool_schema = json.load(f)
with open(os.path.join(_schema_dir, 'function_call.schema.json')) as f:
    function_call_schema = json.load(f)

# Import tool definitions for semantic parameter validation
from tools.press_next import press_next_tool
from tools.press_button import press_button_tool


def validate_messages(messages):
    """
    Validate a list of messages against the xAI message schema.
    """
    if not isinstance(messages, list):
        raise ValidationError("messages must be a list of message objects")
    for msg in messages:
        validate(instance=msg, schema=message_schema)


def validate_tools(tools):
    """
    Validate a list of tool definitions against the xAI tool schema.
    """
    if not isinstance(tools, list):
        raise ValidationError("tools must be a list of tool definition objects")
    for tool in tools:
        validate(instance=tool, schema=tool_schema)


def validate_function_call(call):
    """
    Validate a function call object against the xAI function_call schema and
    semantically against the tool parameter schemas.
    call should be a dict with 'name' and 'arguments'.
    """
    # Top-level structure validation
    validate(instance=call, schema=function_call_schema)

    name = call['name']
    args = call['arguments']
    # Semantic validation based on tool definitions
    if name == press_next_tool['function']['name']:
        param_schema = press_next_tool['function']['parameters']
    elif name == press_button_tool['function']['name']:
        param_schema = press_button_tool['function']['parameters']
    else:
        raise ValidationError(f"Unknown function name: {name}")
    # Validate arguments against the tool's parameters schema
    validate(instance=args, schema=param_schema) 