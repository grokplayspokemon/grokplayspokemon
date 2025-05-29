#!/usr/bin/env python3
import os
import re
import sys

# Ensure local modules are importable
dir_path = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, dir_path)

from agent_logging.logger import get_logger

# Configure a separate logger for spec audit
log_file = os.path.join(dir_path, 'agent_logging', 'spec_audit.log')
logger = get_logger(__name__, log_file=log_file)

# Load xAI API summary document
spec_path = os.path.join(dir_path, 'tools', 'xai_documents_summary')
try:
    with open(spec_path) as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    logger.error("Spec summary file not found at %s", spec_path)
    sys.exit(1)

# Extract numbered section headings
headings = []
for line in lines:
    m = re.match(r'^(\d+)\.\s+(.+)$', line)
    if m:
        headings.append(m.group(2).strip())

logger.info("Starting xAI spec compliance audit")

# Utility to search file for regex

def file_contains(path, pattern):
    try:
        text = open(path).read()
    except FileNotFoundError:
        return False
    return re.search(pattern, text) is not None

# Paths to key code files
grok_file = os.path.join(dir_path, 'grok_agent.py')
schema_dir = os.path.join(dir_path, 'schemas')
validator_file = os.path.join(dir_path, 'validator.py')

# Audit each feature
for h in headings:
    if h.lower().startswith('api is stateless'):
        stateless = not file_contains(grok_file, r'self\.history') and not file_contains(grok_file, r'self\.messages')
        logger.info("Feature '%s': stateless implementation = %s", h, stateless)
    elif h.lower().startswith('roles and message content'):
        msg_schema = os.path.join(schema_dir, 'message.schema.json')
        exists = file_contains(msg_schema, r'"role"') and file_contains(msg_schema, r'"content"')
        logger.info("Feature '%s': message schema fields = %s", h, exists)
    elif 'function calling' in h.lower() and 'tool' not in h.lower():
        # Tools passed to API call
        exists = file_contains(grok_file, r'tools=tools') or file_contains(grok_file, r'tools =')
        logger.info("Feature '%s': function calling via tools param = %s", h, exists)
    elif 'function/tool definitions' in h.lower():
        tool_schema = os.path.join(schema_dir, 'tool.schema.json')
        exists = file_contains(tool_schema, r'"type"') and file_contains(tool_schema, r'"function"')
        logger.info("Feature '%s': tool schema presence = %s", h, exists)
    elif h.lower().startswith('structured outputs'):
        exists = file_contains(validator_file, r'validate_function_call')
        logger.info("Feature '%s': structured output validation = %s", h, exists)
    elif h.lower().startswith('streaming responses'):
        has_stream = file_contains(grok_file, r'stream\s*=')
        logger.info("Feature '%s': streaming param usage = %s", h, has_stream)
    elif 'function calling modes' in h.lower():
        has_choice = file_contains(grok_file, r'tool_choice')
        logger.info("Feature '%s': tool_choice usage = %s", h, has_choice)
    else:
        logger.info("Feature '%s': manual review required", h)

logger.info("xAI spec audit complete") 