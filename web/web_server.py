# web_server.py
"""
Final Flask web server for Grok Plays Pokemon
Complete implementation with all UI features
"""
import os
import json
import time
import base64
import threading
from pathlib import Path
from queue import Queue, Empty
from datetime import datetime
from flask import Flask, render_template_string, jsonify, Response, stream_with_context, request, send_from_directory
from flask_cors import CORS
from PIL import Image
import io
import logging
from shared import game_started, grok_enabled
from omegaconf import OmegaConf
from .quest_map_generator import generate as build_quest_map, PAD_ROW, PAD_COL, TILE_SIZE
from PIL import ImageDraw

# load the exact same config.yaml you merged in play.py
_CONFIG = OmegaConf.load(Path(__file__).parent.parent / "config.yaml")

# Configure logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Set up static folder
STATIC_DIR = Path(__file__).parent / 'static'
STATIC_DIR.mkdir(exist_ok=True)
(STATIC_DIR / 'images').mkdir(exist_ok=True)

# Global game state
game_state = {
    'location': {'map_name': 'Unknown', 'map_id': 0, 'x': 0, 'y': 0, 'gx': 0, 'gy': 0},
    'stats': {'money': 0, 'badges': 0, 'pokedex_seen': 0, 'pokedex_caught': 0, 'steps': 0},
    'pokemon_team': [],
    'current_quest': None,
    'quest_data': {'quests': {}, 'triggers': {}},
    'dialog': '',
    'nav_status': 'idle',
    'game_screen': None,
    'collision_overlay': None,
    'grok_thinking': '',
    'grok_response': '',
    'grok_cost': {},
    'last_update': time.time()
}

# SSE client connections
sse_clients = []
sse_lock = threading.Lock()

# Cache for quest definitions
quest_definitions = None
last_quest_id = None

def load_quest_definitions():
    """Load quest definitions from JSON file"""
    global quest_definitions
    if quest_definitions is None:
        try:
            file_path = Path(__file__).parent.parent / 'environment' / 'environment_helpers' / 'required_completions.json'
            with open(file_path, 'r') as f:
                quest_definitions = json.load(f)
                print(f"Loaded {len(quest_definitions)} quest definitions")
        except Exception as e:
            print(f"Error loading quest definitions: {e}")
            quest_definitions = []
    return quest_definitions

def get_quest_by_id(quest_id):
    """Get quest data by ID"""
    quests = load_quest_definitions()
    for quest in quests:
        if str(quest.get('quest_id')) == str(quest_id):
            return quest
    return None

def broadcast_update(event_type, data):
    """Send update to all SSE clients"""
    message = f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"
    
    with sse_lock:
        dead_clients = []
        for client_queue in sse_clients:
            try:
                client_queue.put(message)
            except:
                dead_clients.append(client_queue)
        
        for client in dead_clients:
            sse_clients.remove(client)

def handle_status_update(item_id, data):
    """Process updates from status_queue"""
    global game_state, last_quest_id
    
    # # Debug logging
    # if item_id in ('__emulator_screen__', '__collision_overlay_screen__'):
    #     # avoid printing raw pixel bytes
    #     if isinstance(data, tuple) and len(data) >= 1:
    #         print(f"Status update: {item_id} = raw pixel data ({len(data[0])} bytes)")
    #     else:
    #         print(f"Status update: {item_id} = {data}")
    # else:
    #     print(f"Status update: {item_id} = {data}")
    
    # Handle different types of updates
    # include Grok control status
    if item_id == '__grok_enabled__':
        game_state['grok_enabled'] = data
        broadcast_update('grok_enabled', data)

    elif item_id == '__location__':
        # Handle both dict and tuple formats
        if isinstance(data, dict):
            game_state['location'] = data
        elif isinstance(data, (list, tuple)) and len(data) >= 4:
            # Convert tuple format to dict
            x, y, map_id, map_name = data[:4]
            game_state['location'] = {
                'x': x, 'y': y, 
                'map_id': map_id, 
                'map_name': map_name,
                'gx': data[4] if len(data) > 4 else 0,
                'gy': data[5] if len(data) > 5 else 0
            }
        
        broadcast_update('location', game_state['location'])
        
    elif item_id == '__current_quest__':
        old_quest = game_state['current_quest']
        game_state['current_quest'] = data
        
        # Handle quest changes - show speech bubble for new quest
        if data and data != old_quest and data != last_quest_id:
            last_quest_id = data
            quest = get_quest_by_id(data)
            if quest and quest.get('begin_quest_text'):
                broadcast_update('speech_bubble', {
                    'text': quest['begin_quest_text'],
                    'type': 'quest_start',
                    'duration': 4000
                })
        
        broadcast_update('current_quest', data)
        
    elif item_id == '__quest_data__':
        old_data = game_state['quest_data'].copy()
        game_state['quest_data'] = data
        
        # Check for newly completed quests
        if 'quests' in data and 'quests' in old_data:
            for quest_id, completed in data['quests'].items():
                if completed and not old_data['quests'].get(quest_id, False):
                    # Quest just completed - show completion message
                    quest = get_quest_by_id(quest_id)
                    if quest and quest.get('end_quest_text'):
                        broadcast_update('speech_bubble', {
                            'text': quest['end_quest_text'],
                            'type': 'quest_complete',
                            'duration': 5000
                        })
        
        broadcast_update('quest_data', data)
        
    elif item_id == '__pokemon_team__':
        game_state['pokemon_team'] = data
        broadcast_update('pokemon_team', data)
        
    elif item_id == '__dialog__':
        game_state['dialog'] = data
        broadcast_update('dialog', data)
        
    elif item_id == '__nav_status__':
        game_state['nav_status'] = data
        broadcast_update('nav_status', data)
        
    elif item_id == '__grok_thinking__':
        game_state['grok_thinking'] = data
        broadcast_update('grok_thinking', data)
        
    elif item_id == '__grok_response__':
        game_state['grok_response'] = data
        broadcast_update('grok_response', data)

    elif item_id == '__grok_cost__':
        game_state['grok_cost'] = data
        broadcast_update('grok_cost', data)
        
    elif item_id == '__grok_action__':
        # Separate event for actions only
        game_state['last_action'] = data
        broadcast_update('grok_action', data)
        
    elif item_id == '__grok_decision__':
        # Key decision points extracted from thinking
        broadcast_update('grok_decision', data)
        
    elif item_id == '__grok_error__':
        # Any errors or issues
        broadcast_update('grok_error', data)
        
    elif item_id == '__grok_plan__':
        # Current plan or strategy
        broadcast_update('grok_plan', data)    
        
    elif item_id == '__action__':
        broadcast_update('action', data)
        
    elif item_id == '__llm_usage__':
        broadcast_update('llm_usage', data)
        
    elif item_id.startswith('__stats_'):
        stat_name = item_id.replace('__stats_', '').replace('__', '')
        game_state['stats'][stat_name] = data
        broadcast_update('stats', game_state['stats'])
        
    elif item_id == '__emulator_screen__':
        try:
            if isinstance(data, tuple) and len(data) == 4:
                pixel_data_bytes, img_width, img_height, img_mode = data
                img = Image.frombytes(img_mode, (img_width, img_height), pixel_data_bytes)
                img = img.resize((img_width * 3, img_height * 3), Image.NEAREST)
                
                buffered = io.BytesIO()
                img.save(buffered, format='PNG')
                img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                
                game_state['game_screen'] = f"data:image/png;base64,{img_b64}"
                broadcast_update('game_screen', game_state['game_screen'])
        except Exception as e:
            print(f"Error processing screen data: {e}")
            
    elif item_id == '__collision_overlay_screen__':
        try:
            if isinstance(data, tuple) and len(data) == 4:
                pixel_data_bytes, img_width, img_height, img_mode = data
                img = Image.frombytes(img_mode, (img_width, img_height), pixel_data_bytes)
                img = img.resize((img_width * 3, img_height * 3), Image.NEAREST)
                
                buffered = io.BytesIO()
                img.save(buffered, format='PNG')
                img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                
                game_state['collision_overlay'] = f"data:image/png;base64,{img_b64}"
                broadcast_update('collision_overlay', game_state['collision_overlay'])
        except Exception as e:
            print(f"Error processing collision overlay: {e}")
    
    # Handle trigger updates (quest progress)
    elif isinstance(item_id, str) and ('_' in item_id or item_id.isdigit()):
        if 'triggers' not in game_state['quest_data']:
            game_state['quest_data']['triggers'] = {}
        game_state['quest_data']['triggers'][str(item_id)] = data
        broadcast_update('trigger_update', {'id': item_id, 'completed': data})
    
    elif item_id == '__global_map_player__':
        # Player position for global map overlay
        # data expected as tuple/list [y, x]
        gy, gx = data[0], data[1] if isinstance(data, (list, tuple)) else (0, 0)
        game_state['global_map_player'] = {'gy': gy, 'gx': gx}
        broadcast_update('global_map_player', {'gy': gy, 'gx': gx})
    
    elif item_id == '__grok_prompt__':
        # Full prompt sent to Grok before it begins reasoning
        game_state['grok_prompt'] = data
        broadcast_update('grok_prompt', data)
        
    elif item_id == '__facing_direction__':
        # Facing direction of player for global map overlay
        game_state['facing'] = data
        broadcast_update('facing_direction', data) 
    
    game_state['last_update'] = time.time()

def monitor_status_queue(status_queue):
    """Monitor status queue in a separate thread"""
    while True:
        try:
            item_id, data = status_queue.get(timeout=0.1)
            handle_status_update(item_id, data)
        except Empty:
            continue
        except Exception as e:
            print(f"Error in status queue monitor: {e}")
            time.sleep(0.1)

# Complete HTML template from the artifact
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Grok Plays Pokémon - Stream</title>
    <link
        href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
        rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        /* Ensure body doesn't create unnecessary scrollbars */
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: #0a0a0a;
            color: #ffffff;
            overflow: hidden; /* Prevent body scroll when zoomed */
            height: 100vh;
            margin: 0;
            font-size: 20px;
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Main layout with 3 columns - Updated for flexible sidebars */
        .stream-container {
            display: grid;
            grid-template-columns: minmax(400px, 1fr) 2fr minmax(400px, 1fr);
            grid-template-rows: auto 1fr 180px;
            height: 100vh; /* Change from min-height to height */
            background-color: #1a1a1a;
            gap: 1px;
            overflow: hidden; /* Prevent container overflow */
        }

        /* Header */
        .header {
            grid-column: 1 / -1;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 20px 30px;
            background: #0a0a0a;
            border-bottom: 1px solid #1a1a1a;
        }

        .title {
            font-size: 48px; /* Doubled from 36px */
            font-weight: 300;
            letter-spacing: -0.5px;
        }

        .title strong {
            font-weight: 600;
            background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .live-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 12px 24px; /* Increased from 8px 16px */
            background: #dc2626;
            border-radius: 4px;
            font-size: 20px; /* Doubled from 12px */
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .live-dot {
            width: 12px; /* Increased from 8px */
            height: 12px;
            background: #fff;
            border-radius: 50%;
            animation: pulse 1s ease-in-out infinite;
        }

        /* Left sidebar - Grok's Actions */
        .left-sidebar {
            grid-column: 1;
            grid-row: 2;
            background: #0a0a0a;
            padding: 20px;
            overflow-y: auto;
            border-right: 1px solid #1a1a1a;
            min-width: 400px; /* Minimum width */
        }

        .actions-header {
            font-size: 28px; /* Doubled from 18px */
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #999;
            margin-bottom: 20px;
        }

        .action-log {
            display: flex;
            flex-direction: column;
            gap: 16px; /* Increased from 12px */
        }

        .action-entry {
            background: #111;
            border: 1px solid #2a2a2a;
            border-radius: 8px;
            padding: 16px; /* Increased from 12px */
            animation: slideIn 0.3s ease;
        }

        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateX(-20px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }

        .action-time {
            font-size: 18px; /* Doubled from 11px */
            color: #666;
            margin-bottom: 12px; /* Increased from 8px */
        }

        .action-button {
            display: inline-flex;
            align-items: center;
            gap: 10px; /* Increased from 8px */
            padding: 10px 20px; /* Increased from 6px 12px */
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 6px;
            font-size: 22px; /* Doubled from 13px */
            font-weight: 500;
            margin-bottom: 12px; /* Increased from 8px */
        }

        .action-icon {
            width: 28px; /* Increased from 20px */
            height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #2a2a2a;
            border-radius: 4px;
            font-size: 20px; /* Increased from 14px */
        }

        /* Action type specific colors */
        .action-up { border-color: #3b82f6; }
        .action-up .action-icon { background: #3b82f6; color: #fff; }
        
        .action-down { border-color: #8b5cf6; }
        .action-down .action-icon { background: #8b5cf6; color: #fff; }
        
        .action-left { border-color: #ec4899; }
        .action-left .action-icon { background: #ec4899; color: #fff; }
        
        .action-right { border-color: #10b981; }
        .action-right .action-icon { background: #10b981; color: #fff; }
        
        .action-a { border-color: #f59e0b; }
        .action-a .action-icon { background: #f59e0b; color: #000; }
        
        .action-b { border-color: #ef4444; }
        .action-b .action-icon { background: #ef4444; color: #fff; }
        
        .action-start { border-color: #6366f1; }
        .action-start .action-icon { background: #6366f1; color: #fff; }
        
        .action-path { border-color: #14b8a6; }
        .action-path .action-icon { background: #14b8a6; color: #fff; }

        .action-reason {
            font-size: 20px; /* Doubled from 12px */
            color: #999;
            line-height: 1.4;
        }

        /* Center content area */
        .center-content {
            grid-column: 2;
            grid-row: 2;
            display: grid;
            grid-template-columns: 1fr 1fr;
            grid-template-rows: 1fr auto;
            padding: 20px;
            gap: 20px;
            overflow: hidden;
            height: 100%;
        }

        /* Game screen area */
        .game-area {
            flex: 1;
            display: flex;
            flex-direction: row;
            position: relative;
        }

        .game-screen-container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            overflow: hidden;
            position: relative;
            max-width: 800px;
            margin: 0 auto;
            width: 100%;
        }

        #gameScreen {
            width: 100%;
            height: 100%;
            object-fit: contain;
            image-rendering: pixelated;
            image-rendering: -moz-crisp-edges;
            image-rendering: crisp-edges;
        }

        .game-placeholder {
            color: #666;
            font-size: 28px; /* Doubled from 18px */
        }

        /* Speech Bubble */
        .speech-bubble-overlay {
            position: absolute;
            top: 20px;
            right: 20px;
            max-width: 400px;
            z-index: 1000;
            pointer-events: none;
        }

        .speech-bubble {
            background: rgba(255, 248, 220, 0.95);
            border: 8px solid #FFA500;
            border-radius: 15px;
            padding: 20px 28px; /* Increased from 15px 20px */
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
            opacity: 0;
            transform: scale(0.8) translateY(-20px);
            transition: all 0.3s ease;
            position: relative;
        }

        .speech-bubble.show {
            opacity: 1;
            transform: scale(1) translateY(0);
        }

        .speech-bubble.quest-complete {
            background: rgba(144, 238, 144, 0.95);
            border-color: #228B22;
        }

        .speech-bubble::after {
            content: '';
            position: absolute;
            bottom: -15px;
            left: 30px;
            width: 0;
            height: 0;
            border-style: solid;
            border-width: 15px 10px 0 10px;
            border-color: transparent;
            border-top-color: inherit;
        }

        .speech-bubble-text {
            color: #8B4513;
            font-size: 24px; /* Doubled from 16px */
            font-weight: 600;
            line-height: 1.4;
        }

        .quest-complete .speech-bubble-text {
            color: #006400;
        }

        /* Right sidebar */
        .right-sidebar {
            grid-column: 3;
            grid-row: 2;
            display: flex;
            flex-direction: column;
            gap: 1px;
            background: #1a1a1a;
            overflow: hidden;
            min-width: 400px;
            max-height: 100%; /* Constrain height */
        }
        
        .sidebar-section {
            background: #0a0a0a;
            padding: 20px;
            overflow-y: auto;
            overflow-x: hidden; /* Hide horizontal overflow */
            display: flex;
            flex-direction: column;
            gap: 20px;
            flex-grow: 1;
            min-height: 0; /* Allow shrinking */
        }

        .section-title {
            font-size: 24px; /* Doubled from 16px */
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #999;
            margin-bottom: 12px;
        }
                
        /* Global Map Container */
        .global-map-container {
            height: 300px;
            overflow: hidden;
            position: relative;
            background: #0a0a0a;
            padding: 20px;
            border-top: 1px solid #1a1a1a;
        }

        .global-map-section {
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 15px;
            height: 100%;
            position: relative;
        }

        .map-canvas {
            width: 100%;
            height: 85%;
            background: #0a0a0a;
            border: 1px solid #222;
            border-radius: 4px;
            position: relative;
            overflow: hidden;
            margin-bottom: 10px;
        }

        #globalMapWrapper {
            position: relative;
            width: 100%;
            height: 100%;
            overflow: hidden;
        }

        #globalMapImage {
            position: absolute;
            image-rendering: pixelated;
            transform-origin: top left;
            transition: none; /* Remove transition for instant movement */
        }

        #globalMapCanvas {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 10;
        }

        .map-info {
            display: flex;
            justify-content: space-between;
            font-size: 18px;
            color: #999;
            padding: 0 5px;
        }
                

        /* Global Map Container - commented out duplicate */
        /*
        .global-map-container {
            height: 300px;
            overflow: hidden;
            position: relative;
            background: #0a0a0a;
            padding: 20px;
            border-top: 1px solid #1a1a1a;
        }

        .global-map-section {
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 15px;
            height: 100%;
        }

        .map-canvas {
            width: 100%;
            height: 85%;
            background: #0a0a0a;
            border: 1px solid #222;
            border-radius: 4px;
            position: relative;
            overflow: hidden;
            margin-bottom: 10px;
        }

        #globalMapImage {
            position: absolute;
            image-rendering: pixelated;
            display: none;
            transition: transform 0.1s linear;
        }
        */

        .player-sprite {
            position: absolute;
            width: 12px; /* Increased from 8px */
            height: 12px;
            background: #ff0000;
            border: 1px solid #fff;
            border-radius: 50%;
            z-index: 10;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            box-shadow: 0 0 8px rgba(255, 0, 0, 0.8);
            animation: playerPulse 1s ease-in-out infinite;
        }

        @keyframes playerPulse {
            0%, 100% { transform: translate(-50%, -50%) scale(1); }
            50% { transform: translate(-50%, -50%) scale(1.2); }
        }

        .map-info {
            display: flex;
            justify-content: space-between;
            font-size: 18px; /* Doubled from 11px */
            color: #999;
        }

        /* Quest Section */
        .quest-section {
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 20px; /* Increased from 15px */
        }

        .quest-description {
            color: #ccc;
            margin-bottom: 20px; /* Increased from 15px */
            font-size: 22px; /* Doubled from 13px */
            line-height: 1.5;
        }

        .quest-list {
            list-style: none;
            padding: 0;
            margin: 0 0 20px 0; /* Increased from 15px */
        }

        .quest-item {
            margin-bottom: 14px; /* Increased from 10px */
            color: #999;
            font-size: 20px; /* Doubled from 12px */
            display: flex;
            align-items: flex-start;
            gap: 14px; /* Increased from 10px */
            padding: 12px 16px; /* Increased from 8px 12px */
            background: #0a0a0a;
            border-radius: 6px;
            transition: all 0.3s ease;
        }

        .quest-item::before {
            content: '○';
            font-size: 22px; /* Doubled from 14px */
            color: #666;
            flex-shrink: 0;
            margin-top: -2px;
        }

        .quest-item.completed {
            color: #10b981;
            text-decoration: line-through;
            text-decoration-color: #666;
            background: rgba(16, 185, 129, 0.1);
        }

        .quest-item.completed::before {
            content: '●';
            color: #10b981;
        }

        .quest-progress-bar {
            width: 100%;
            height: 10px; /* Increased from 6px */
            background: #2a2a2a;
            border-radius: 5px; /* Increased from 3px */
            overflow: hidden;
        }

        .quest-progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #3b82f6, #8b5cf6);
            transition: width 0.5s ease;
        }

        /* Grok Status Section */
        .grok-status-section {
            flex: 1;
            min-height: 0;
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            overflow: hidden; /* Add this to contain children */
        }

        #grokStatus {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            min-height: 0; /* Important for flexbox children */
        }

        /* NEW – make thinking / response panels scroll within available space */
        #grokThinking,
        #grokResponse {
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden; /* Add this */
            word-wrap: break-word; /* Add this */
            word-break: break-word; /* Add this */
            overflow-wrap: break-word; /* Add this */
            min-height: 0; /* Add this */
            max-width: 100%; /* Add this */
        }
        
        .grok-message {
            margin-bottom: 14px; /* Increased from 10px */
            padding: 12px 16px; /* Increased from 8px 12px */
            background: #1a1a1a;
            border-radius: 4px;
            font-size: 20px; /* Doubled from 12px */
            line-height: 1.5;
            word-wrap: break-word; /* Add this */
            word-break: break-word; /* Add this */
            overflow-wrap: break-word; /* Add this */
            white-space: pre-wrap; /* Add this */
            max-width: 100%; /* Add this */
            box-sizing: border-box; /* Add this */
        }
        
        /* Ensure the waiting message also wraps */
        #grokWaiting {
            color: #666;
            font-size: 20px;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }

        /* Team section styling */
        .team-display-area {
            padding: 20px;
            background: #0a0a0a;
            border-top: 1px solid #1a1a1a;
            overflow: hidden;
        }

        /* Team section */
        .team-grid {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 16px; /* Increased from 12px */
            width: 100%;
        }

        .pokemon-card {
            background: #111;
            border: 1px solid #2a2a2a;
            border-radius: 8px;
            padding: 14px; /* Increased from 10px */
            transition: all 0.2s ease;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: space-between;
            min-height: 550px; /* Increased from 200px */
        }

        .pokemon-card:hover {
            background: #181818;
            border-color: #444;
            transform: translateY(-2px);
        }

        .pokemon-sprite-container {
            width: 100%;
            aspect-ratio: 1/1;
            background: linear-gradient(145deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.08));
            border-radius: 6px;
            padding: 12px; /* Increased from 8px */
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 12px; /* Increased from 8px */
        }

        .pokemon-sprite {
            width: 100%;
            height: 100%;
            max-width: 440px; /* Increased from 64px */
            max-height: 440px;
            image-rendering: pixelated;
            filter: brightness(1.1) contrast(1.1);
            object-fit: contain;
        }

        .pokemon-card-info-wrapper {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            flex-grow: 1;
            justify-content: flex-start;
        }

        .pokemon-card-main-info {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            width: 100%;
            margin-bottom: 10px; /* Increased from 6px */
        }

        .pokemon-card-left {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            text-align: left;
        }

        .pokemon-card-right {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            text-align: right;
        }

        .pokemon-name {
            font-size: 18px; /* Doubled from 11px */
            font-weight: 600;
            text-transform: uppercase;
            color: #e0e0e0;
            line-height: 1.1;
        }

        .pokemon-species-name {
            font-size: 16px; /* Doubled from 10px */
            font-weight: 500;
            color: #bbb;
            text-transform: capitalize;
            margin-top: 4px; /* Increased from 2px */
            line-height: 1.1;
        }

        .pokemon-level {
            font-size: 18px; /* Doubled from 11px */
            color: #e0e0e0;
            font-weight: 600;
            line-height: 1.1;
        }

        .pokemon-types-row {
            width: 100%;
            display: flex;
            justify-content: center;
            margin-bottom: 10px; /* Increased from 6px */
            min-height: 30px; /* Increased from 20px */
        }

        .pokemon-types {
            display: flex;
            gap: 6px; /* Increased from 4px */
            align-items: center;
        }

        .type-badge {
            font-size: 14px; /* Doubled from 9px */
            padding: 4px 10px; /* Increased from 2px 6px */
            border-radius: 4px;
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
        }

        .pokemon-card-stats-footer {
            width: 100%;
            margin-top: auto;
        }

        .hp-bar {
            height: 12px; /* Increased from 8px */
            background: #2a2a2a;
            border-radius: 4px; /* Increased from 3px */
            overflow: hidden;
            width: 100%;
            margin-bottom: 6px; /* Increased from 4px */
        }

        .hp-fill {
            height: 100%;
            background: #10b981;
            transition: width 0.3s ease, background-color 0.3s ease;
        }

        .hp-fill.medium {
            background: #f59e0b;
        }

        .hp-fill.low {
            background: #ef4444;
        }

        .pokemon-card-bottom-details {
            display: flex;
            justify-content: space-between;
            align-items: center;
            width: 100%;
            margin-bottom: 10px; /* Increased from 6px */
        }

        .hp-text {
            font-size: 16px; /* Doubled from 10px */
            color: #aaa;
            font-variant-numeric: tabular-nums;
            line-height: 1.1;
        }

        .pokemon-status {
            font-size: 16px; /* Doubled from 10px */
            font-weight: 600;
            color: #aaa;
            text-transform: uppercase;
            line-height: 1.1;
        }

        /* Status colors */
        .pokemon-status.PSN { color: #A040A0; }
        .pokemon-status.BRN { color: #F08030; }
        .pokemon-status.FRZ { color: #98D8D8; }
        .pokemon-status.PAR { color: #F8D030; }
        .pokemon-status.SLP { color: #9d9d9a; }

        /* EXP Bar styling */
        .exp-bar {
            height: 6px; /* Increased from 4px */
            background: #2a2a2a;
            border-radius: 3px;
            overflow: hidden;
            margin-bottom: 6px; /* Increased from 4px */
            width: 100%;
        }

        .exp-fill {
            height: 100%;
            background: #3b82f6;
            transition: width 0.3s ease;
        }

        .exp-text {
            font-size: 14px; /* Doubled from 9px */
            color: #999;
            text-align: center;
            font-variant-numeric: tabular-nums;
        }

        /* Type colors */
        .type-normal { background: #9d9d9a; color: #000; }
        .type-fire { background: #F08030; color: #000; }
        .type-water { background: #6890F0; color: #fff; }
        .type-electric { background: #F8D030; color: #000; }
        .type-grass { background: #78C850; color: #000; }
        .type-ice { background: #98D8D8; color: #000; }
        .type-fighting { background: #C03028; color: #fff; }
        .type-poison { background: #A040A0; color: #fff; }
        .type-ground { background: #E0C068; color: #000; }
        .type-flying { background: #A890F0; color: #000; }
        .type-psychic { background: #F85888; color: #fff; }
        .type-bug { background: #A8B820; color: #000; }
        .type-rock { background: #B8A038; color: #000; }
        .type-ghost { background: #705898; color: #fff; }
        .type-dragon { background: #7038F8; color: #fff; }
        .type-dark { background: #705848; color: #fff; }
        .type-steel { background: #B8B8D0; color: #000; }
        .type-fairy { background: #EE99AC; color: #000; }

        /* Empty slot */
        .empty-slot {
            background: #0a0a0a;
            border: 1px dashed #1a1a1a;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #333;
            font-size: 28px; /* Doubled from 18px */
            min-height: 280px; /* Increased from 200px */
        }

        /* Bottom stats bar - Much taller */
        .bottom-bar {
            grid-column: 1 / -1;
            background: #0a0a0a;
            border-top: 1px solid #1a1a1a;
            padding: 32px 40px; /* Doubled from 16px 30px */
            display: flex;
            justify-content: space-between;
            align-items: center;
            grid-row: 3;
            flex-wrap: wrap;
            min-height: 180px; /* Ensure minimum height */
        }

        .bottom-stats {
            display: flex;
            gap: 32px; /* Increased from 20px */
            flex-wrap: wrap;
            align-items: baseline;
            flex: 1;
        }

        .bottom-stat {
            display: flex;
            align-items: baseline;
            gap: 10px; /* Increased from 6px */
        }

        .bottom-stat-label {
            font-size: 18px; /* Doubled from 11px */
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .bottom-stat-value {
            font-size: 22px; /* Doubled from 13px */
            font-weight: 500;
            font-variant-numeric: tabular-nums;
        }

        .bottom-stat-value.cost {
            color: #2ecc71;
            font-weight: 600;
        }

        /* Input visualization */
        .input-viz {
            display: flex;
            gap: 12px; /* Increased from 8px */
            align-items: center;
        }

        .input-key {
            padding: 8px 16px; /* Doubled from 4px 8px */
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 4px;
            font-size: 18px; /* Doubled from 11px */
            font-weight: 500;
            text-transform: uppercase;
            animation: key-press 0.3s ease;
        }

        @keyframes key-press {
            0% { transform: scale(0.9); opacity: 0.5; }
            50% { transform: scale(1.1); }
            100% { transform: scale(1); opacity: 1; }
        }

        /* Agent controls */
        .agent-controls {
            display: flex;
            gap: 14px; /* Increased from 10px */
        }

        .agent-controls button {
            padding: 12px 28px; /* Doubled from 6px 16px */
            border: none;
            border-radius: 4px;
            font-size: 22px; /* Doubled from 14px */
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        #start-btn {
            background: #10b981;
            color: white;
        }

        #start-btn:hover:not(:disabled) {
            background: #059669;
        }

        #pause-btn {
            background: #f59e0b;
            color: white;
        }

        #pause-btn:hover:not(:disabled) {
            background: #d97706;
        }

        #stop-btn {
            background: #ef4444;
            color: white;
        }

        #stop-btn:hover:not(:disabled) {
            background: #dc2626;
        }

        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 10px; } /* Increased from 6px */
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 5px; } /* Increased from 3px */
        ::-webkit-scrollbar-thumb:hover { background: #444; }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }

        .grok-cost {
            background-color: rgba(100, 100, 100, 0.1);
            border-left: 4px solid #3b82f6; /* Increased from 3px */
            font-size: 18px; /* Doubled from 0.8rem */
            padding: 14px; /* Increased from 10px */
            margin-top: 14px; /* Increased from 10px */
        }

        .grok-cost-header {
            font-weight: bold;
            margin-bottom: 8px; /* Increased from 5px */
            font-size: 20px; /* Added explicit size */
        }

        .grok-cost-info {
            display: flex;
            flex-wrap: wrap;
            gap: 16px; /* Increased from 12px */
        }

        .grok-cost-metric {
            display: flex;
            flex-direction: column;
            min-width: 120px; /* Increased from 80px */
        }

        .grok-cost-label {
            font-size: 20px; /* Doubled from 0.7rem */
            color: #9ca3af;
        }

        .grok-cost-value {
            font-size: 24px; /* Doubled from 0.9rem */
            font-weight: 600;
        }

        .pricing-info {
            margin-top: 20px; /* Increased from 8px */
            font-size: 14px; /* Doubled from 0.7rem */
            color: #9ca3af;
        }
    </style>
    <script>
        const CONFIG = {{ CONFIG | tojson }};
    </script>
</head>

<body>
    <div class="stream-container">
        <header class="header">
            <h1 class="title"><strong>GROK</strong> Plays Pokémon Red</h1>
            <div style="display: flex; align-items: center; gap: 32px;">
                <div style="display: flex; gap: 24px; font-size: 20px; color: #999;">
                    <div>
                        <span>Session</span>
                        <span class="mono" style="color: #fff; margin-left: 12px;">#0847</span>
                    </div>
                    <div>
                        <span>Uptime</span>
                        <span class="mono" style="color: #fff; margin-left: 12px;" id="uptime">00:00:00</span>
                    </div>
                </div>
                <div style="display: flex; align-items: center; gap: 24px;">
                    <div class="agent-controls">
                        <button id="start-btn" onclick="startAgent()">Start Grok</button>
                        <button id="pause-btn" onclick="pauseAgent()" disabled>Pause Grok</button>
                        <button id="stop-btn" onclick="stopAgent()" disabled>Stop Grok</button>
                    </div>
                    <div class="live-badge">
                        <div class="live-dot"></div>
                        LIVE
                    </div>
                </div>
            </div>
        </header>

        <!-- Left sidebar - Grok's Actions -->
        <div class="left-sidebar">
            <h2 class="actions-header">Grok's Actions</h2>
            <div class="action-log" id="actionLog">
                <!-- Actions will be populated here -->
            </div>
        </div>

        <!-- Center content with game and team -->
        <div class="center-content">
            <div class="global-map-container" style="grid-column: 1; grid-row: 1; position: relative; height: 100%;">
                <h2 class="section-title">Global Map</h2>
                <div class="global-map-section">
                    <div class="map-canvas" id="globalMapWrapper">
                        <img id="globalMapImage" src="/global-map.png" alt="Global Map">
                        <canvas id="globalMapCanvas"></canvas>
                        <div id="playerSprite" class="player-sprite"></div>
                    </div>
                    <div class="map-info">
                        <span>Position: <span id="mapPosition">(0, 0)</span></span>
                        <span>Map: <span id="currentMapName">Unknown</span></span>
                    </div>
                </div>
            </div>
        
            <!--
            <div class="global-map-container" style="grid-column: 1; grid-row: 1; position: relative; height: 100%;">
                <h2 class="section-title">Global Map</h2>
                <div class="map-canvas" id="globalMapWrapper" style="position: absolute; inset: 0;">
                    <img id="globalMapImage" src="/global-map.png" alt="Global Map"
                         style="image-rendering: pixelated; width: 100%; height: 100%; object-fit: cover;">
                    <canvas id="globalMapCanvas"
                            style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; image-rendering: pixelated;"></canvas>
                </div>
                <div class="map-info">
                    <span>Position: <span id="mapPosition">(0, 0)</span></span>
                    <span>Map: <span id="currentMapName">Unknown</span></span>
                </div>
            </div>
            -->
            <div class="game-screen-container" style="grid-column: 2; grid-row: 1; position: relative; height: 100%;">
                <img id="gameScreen" alt="Game Screen" style="width: 100%; height: 100%; object-fit: contain; display: none;">
                <div class="game-placeholder" id="gamePlaceholder"
                     style="position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;">
                    Waiting for game capture...
                </div>
                <div class="speech-bubble-overlay" id="speechBubbleOverlay"
                     style="position: absolute; inset: 0; pointer-events: none;"></div>
            </div>
            <section class="team-display-area" style="grid-column: 1 / span 2; grid-row: 2;">
                <h2 class="section-title" style="text-align: center; margin-bottom: 20px;">Active Team</h2>
                <div class="team-grid" id="pokemon-team">
                    <div class="pokemon-card empty-slot">—</div>
                    <div class="pokemon-card empty-slot">—</div>
                    <div class="pokemon-card empty-slot">—</div>
                    <div class="pokemon-card empty-slot">—</div>
                    <div class="pokemon-card empty-slot">—</div>
                    <div class="pokemon-card empty-slot">—</div>
                </div>
            </section>
        </div>

        <!-- Right sidebar -->
        <div class="right-sidebar">
            <!-- Enhanced Sidebar with all sections -->
            <div class="sidebar-section">
                <!-- Quest Progress -->
                <div class="quest-section" id="questSection" style="display: none;">
                    <h2 class="section-title" id="questTitle">Current Quest</h2>
                    <div class="quest-description" id="questDescription"></div>
                    <ul class="quest-list" id="questTriggers"></ul>
                    <div class="quest-progress-bar">
                        <div class="quest-progress-fill" id="questProgress" style="width: 0%"></div>
                    </div>
                </div>

                <!-- Grok Status -->
                <div class="grok-status-section">
                    <h2 class="section-title">Grok Status</h2>
                    <div id="grokStatus">
                        <div id="grokThinking" class="grok-message grok-thinking" style="display: none;"></div>
                        <div id="grokResponse" class="grok-message grok-response" style="display: none;"></div>
                        <div id="grokCost" class="grok-cost" style="display: none;">
                            <div class="grok-cost-header">Token Usage</div>
                            <div class="grok-cost-info">
                                <div class="grok-cost-metric">
                                    <span class="grok-cost-label">API Calls</span>
                                    <span class="grok-cost-value" id="apiCallsCount">0</span>
                                </div>
                                <div class="grok-cost-metric">
                                    <span class="grok-cost-label">Total Tokens</span>
                                    <span class="grok-cost-value" id="totalTokens">0</span>
                                </div>
                                <div class="grok-cost-metric">
                                    <span class="grok-cost-label">Last Cost</span>
                                    <span class="grok-cost-value" id="callCost">$0.00</span>
                                </div>
                                <div class="grok-cost-metric">
                                    <span class="grok-cost-label">Total Cost</span>
                                    <span class="grok-cost-value" id="totalCost">$0.00</span>
                                </div>
                            </div>
                            <div class="pricing-info">grok-3-mini pricing</div>
                        </div>
                        <div style="color: #666; font-size: 20px;" id="grokWaiting">Waiting for Grok to think...</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Complete Bottom Stats Bar -->
        <footer class="bottom-bar">
            <div class="bottom-stats">
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Location</span>
                    <span class="bottom-stat-value" id="statLocation">Unknown</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Map ID</span>
                    <span class="bottom-stat-value mono" id="statMapId">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">(Y,X)</span>
                    <span class="bottom-stat-value mono" id="statLocal">(0,0)</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Global</span>
                    <span class="bottom-stat-value mono" id="statGlobal">(0,0)</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Money</span>
                    <span class="bottom-stat-value mono" id="statMoney">₽0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Seen Pokemon</span>
                    <span class="bottom-stat-value" id="statSeen">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Caught Pokemon</span>
                    <span class="bottom-stat-value" id="statCaught">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Badges</span>
                    <span class="bottom-stat-value" id="statBadges">0/8</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Steps</span>
                    <span class="bottom-stat-value mono" id="statSteps">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Call Cost</span>
                    <span class="bottom-stat-value mono cost" id="statCallCost">$0.00</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Lifetime Cost</span>
                    <span class="bottom-stat-value mono cost" id="statLifetimeCost">$0.00</span>
                </div>
            </div>
            <div class="input-viz" id="inputDisplay"></div>
        </footer>
    </div>

    <script>
        // Global state
        let gameState = {
            location: null,
            facing: 'Down',
            questDefinitions: null,
            questData: { quests: {}, triggers: {} },
            currentQuest: null,
            speechBubbleTimer: null,
            startTime: Date.now(),
            totalInputTokens: 0,
            totalOutputTokens: 0,
            lifetimeCost: 0,
            actionHistory: []
        };

        // Action mappings
        const ACTION_NAMES = ["down", "left", "right", "up", "a", "b", "path", "start"];
        const ACTION_ICONS = {
            "down": "↓",
            "left": "←",
            "right": "→",
            "up": "↑",
            "a": "A",
            "b": "B",
            "path": "◈",
            "start": "▶"
        };

        // Update uptime
        setInterval(() => {
            const elapsed = Date.now() - gameState.startTime;
            const hours = Math.floor(elapsed / 3600000);
            const minutes = Math.floor((elapsed % 3600000) / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);
            document.getElementById('uptime').textContent =
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }, 1000);

        // New function to extract decision points from thinking
        function extractDecisionPoints(thinking) {
            // Look for key patterns in Grok's thinking
            const patterns = [
                /Current objective: (.+?)(?:\.|$)/i,
                /I need to (.+?)(?:\.|$)/i,
                /My goal is to (.+?)(?:\.|$)/i,
                /I should (.+?)(?:\.|$)/i,
                /Next step: (.+?)(?:\.|$)/i,
                /Planning to (.+?)(?:\.|$)/i
            ];
            
            for (const pattern of patterns) {
                const match = thinking.match(pattern);
                if (match) {
                    addActionToLog('thinking', match[1], 'thinking');
                    break; // Only show the first match
                }
            }
        }
        
        
        // Add new CSS for the new action types
        const additionalCSS = `
        .action-tool { border-color: #14b8a6; }
        .action-tool .action-icon { background: #14b8a6; color: #fff; }

        .action-thinking { border-color: #8b5cf6; }
        .action-thinking .action-icon { background: #8b5cf6; color: #fff; }

        .action-prompt { border-color: #6366f1; }
        .action-prompt .action-icon { background: #6366f1; color: #fff; }

        .action-error { border-color: #ef4444; }
        .action-error .action-icon { background: #ef4444; color: #fff; }

        /* Make the action log scrollable with more content */
        .action-log {
            display: flex;
            flex-direction: column;
            gap: 16px;
            max-height: calc(100vh - 200px); /* Adjust based on header height */
            overflow-y: auto;
        }
        `;
        
        // Enhanced addActionToLog function
        function addActionToLog(action, reasoning, type = 'action') {
            const actionLog = document.getElementById('actionLog');
            const entry = document.createElement('div');
            entry.className = 'action-entry';

            const time = new Date().toLocaleTimeString();
            let icon, actionDisplay, actionClass;
            
            switch(type) {
                case 'tool':
                    icon = '🔧';
                    actionDisplay = `TOOL: ${action.toUpperCase()}`;
                    actionClass = 'action-tool';
                    break;
                case 'thinking':
                    icon = '💭';
                    actionDisplay = 'THINKING';
                    actionClass = 'action-thinking';
                    break;
                case 'prompt':
                    icon = '📝';
                    actionDisplay = 'NEW PROMPT';
                    actionClass = 'action-prompt';
                    break;
                case 'error':
                    icon = '⚠️';
                    actionDisplay = 'ERROR';
                    actionClass = 'action-error';
                    break;
                default:
                    icon = ACTION_ICONS[action] || "?";
                    actionDisplay = action.toUpperCase();
                    actionClass = `action-${action}`;
            }

            entry.innerHTML = `
                <div class="action-time">${time}</div>
                <div class="action-button ${actionClass}">
                    <span class="action-icon">${icon}</span>
                    <span>${actionDisplay}</span>
                </div>
                <div class="action-reason">${reasoning || 'No details provided'}</div>
            `;
            
            actionLog.insertBefore(entry, actionLog.firstChild);
            
            // Keep more history in the left sidebar
            while (actionLog.children.length > 50) {
                actionLog.removeChild(actionLog.lastChild);
            }
        }

        // Load quest definitions
        async function loadQuestDefinitions() {
            if (!gameState.questDefinitions) {
                try {
                    const res = await fetch('/required_completions.json');
                    gameState.questDefinitions = await res.json();
                    console.log('Loaded quest definitions:', gameState.questDefinitions);
                } catch (e) {
                    console.error("Failed to load quest definitions", e);
                    gameState.questDefinitions = [];
                }
            }
            return gameState.questDefinitions;
        }

        // Sprite state
        gameState.playerSprite = null;
        gameState.spriteFrames = {};
        gameState.spriteFrameUrls = {};

        // Load player sprite frames
        async function loadPlayerSprites() {
            const spriteData = { 'Down': 0, 'Up': 3, 'Left': 6, 'Right': 8 };
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = 16;
            tempCanvas.height = 16;
            const ctx = tempCanvas.getContext('2d');

            try {
                const img = new Image();
                img.src = '/static/images/pokemon_red_player_spritesheet.png';
                await new Promise((resolve, reject) => {
                    img.onload = resolve;
                    img.onerror = reject;
                });

                for (const [direction, index] of Object.entries(spriteData)) {
                    ctx.clearRect(0, 0, 16, 16);
                    ctx.drawImage(img, index * 17, 0, 16, 16, 0, 0, 16, 16);
                    // Store ImageData for future use and also cached dataURL for quick background-image
                    const imageData = ctx.getImageData(0, 0, 16, 16);
                    gameState.spriteFrames[direction] = imageData;
                    // Convert to dataURL once
                    const tmpC = document.createElement('canvas');
                    tmpC.width = tmpC.height = 16;
                    tmpC.getContext('2d').putImageData(imageData, 0, 0);
                    gameState.spriteFrameUrls[direction] = tmpC.toDataURL();
                }
                console.log('Player sprites loaded successfully');
            } catch (error) {
                console.error('Failed to load player sprites:', error);
            }
        }

        // Map constants
        const PAD = 20, TILE_SIZE = 16;
        gameState.mapData = null;
        gameState.questPaths = {};

        // Load map data
        async function loadMapData() {
            const res = await fetch('/static/data/environment_data/map_data.json');
            if (!res.ok) throw new Error(res.statusText);
            const json = await res.json();
            gameState.mapData = {};
            json.regions.forEach(r => {
                gameState.mapData[+r.id] = r;
            });
        }

        // Load quest paths
        async function loadQuestPaths() {
            const res = await fetch('/static/data/environment_helpers/quest_paths/combined_quest_coordinates_continuous.json');
            if (!res.ok) throw new Error(res.statusText);
            const data = await res.json();
            const { quest_start_indices: idxs, coordinates } = data;
            const qids = Object.keys(idxs).sort((a,b)=>+a - +b);
            qids.forEach((qid,i) => {
                const start = idxs[qid];
                const end   = idxs[qids[i+1]] || coordinates.length;
                gameState.questPaths[qid.padStart(3,'0')] =
                    coordinates.slice(start,end).map(c => [c[0], c[1]]);
            });
        }

        // Draw quest coordinates
        function drawQuestCoordinates(ctx, dx, dy) {

            // NEW – avoid blacking-out the map
            ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
            ctx.imageSmoothingEnabled = false;
            ctx.fillStyle = 'rgba(255,255,255,0.3)';
            Object.values(gameState.questPaths).forEach(coords => {
                coords.forEach(([gy, gx]) => {
                    // Coordinates arriving from the backend are already *padded* (they include
                    // the 20-tile border added in Python).  The quest-overlay base map that the
                    // browser displays, however, has this padding removed.  Therefore we must
                    // subtract PAD before converting to pixel space so quest dots line up.
                    const x = (gx - PAD) * TILE_SIZE + dx + TILE_SIZE/2;
                    const y = (gy - PAD) * TILE_SIZE + dy + TILE_SIZE/2;
                    ctx.beginPath();
                    ctx.arc(x, y, 3, 0, 2 * Math.PI);
                    ctx.fill();
                });
            });
        }

        // Update player position and redraw
        function updatePlayerPosition(localX, localY, mapId, facing = 'Down') {
            if (!gameState.mapData) return;
            // map_data.json stores coordinates as [X, Y] (col, row) – see render_to_global.py
            const [map_x, map_y] = gameState.mapData[mapId].coordinates;
            const globalX = localX + map_x + PAD;
            const globalY = localY + map_y + PAD;
            // Convert padded global → pixel coordinates relative to the *unpadded* map.
            const pixelX  = (globalX - PAD) * TILE_SIZE;
            const pixelY  = (globalY - PAD) * TILE_SIZE;

            const wrapper = document.getElementById('globalMapWrapper');
            const img     = document.getElementById('globalMapImage');
            const canvas  = document.getElementById('globalMapCanvas');
            const ctx     = canvas.getContext('2d');
            const W = wrapper.clientWidth, H = wrapper.clientHeight;

            let left = W/2 - pixelX - TILE_SIZE/2;
            let top  = H/2 - pixelY - TILE_SIZE/2;

            const minLeft = W - img.naturalWidth;
            left = Math.min(0, Math.max(minLeft, left));

            img.style.transform = `translate(${left}px, ${top}px)`;
            canvas.width = W;
            canvas.height = H;

            drawQuestCoordinates(ctx, left, top);

            const centerX = W/2, centerY = H/2;
            const spriteEl = document.getElementById('playerSprite');
            spriteEl.style.left = `${centerX - TILE_SIZE/2}px`;
            spriteEl.style.top  = `${centerY - TILE_SIZE/2}px`;
            const frameUrl = gameState.spriteFrameUrls[facing] || gameState.spriteFrameUrls['Down'];
            if (frameUrl) {
                spriteEl.style.width = `${TILE_SIZE}px`;
                spriteEl.style.height = `${TILE_SIZE}px`;
                spriteEl.style.backgroundImage = `url(${frameUrl})`;
                spriteEl.style.backgroundSize = 'contain';
                spriteEl.style.backgroundRepeat = 'no-repeat';
            }
        }

        // Directly centre map using already global (padded) coordinates
        function updatePlayerGlobal(globalX, globalY, facing='Down') {
            const wrapper = document.getElementById('globalMapWrapper');
            const img     = document.getElementById('globalMapImage');
            const canvas  = document.getElementById('globalMapCanvas');
            const ctx     = canvas.getContext('2d');

            // Subtract the padding applied in Python so global coordinates align to the
            // *unpadded* quest-overlay base map exactly like we already do for quest
            // dots and updatePlayerPosition().
            const pixelX = (globalX - PAD) * TILE_SIZE;
            const pixelY = (globalY - PAD) * TILE_SIZE;

            const W = wrapper.clientWidth, H = wrapper.clientHeight;
            let left = W/2 - pixelX - TILE_SIZE/2;
            let top  = H/2 - pixelY - TILE_SIZE/2;

            const minLeft = W - img.naturalWidth;
            left = Math.min(0, Math.max(minLeft, left));
            // Ensure we don't pan past the bottom/top edges (was missing before)
            const minTop  = H - img.naturalHeight;
            top  = Math.min(0, Math.max(minTop , top ));

            img.style.transform = `translate(${left}px, ${top}px)`;
            canvas.width = W;
            canvas.height = H;
            drawQuestCoordinates(ctx, left, top);

            const centerX = W/2, centerY = H/2;
            const spriteEl = document.getElementById('playerSprite');
            spriteEl.style.left = `${centerX - TILE_SIZE/2}px`;
            spriteEl.style.top  = `${centerY - TILE_SIZE/2}px`;
            const frameUrl = gameState.spriteFrameUrls[facing] || gameState.spriteFrameUrls['Down'];
            if (frameUrl) {
                spriteEl.style.width = `${TILE_SIZE}px`;
                spriteEl.style.height = `${TILE_SIZE}px`;
                spriteEl.style.backgroundImage = `url(${frameUrl})`;
                spriteEl.style.backgroundSize = 'contain';
                spriteEl.style.backgroundRepeat = 'no-repeat';
            }
            // Update map position display when using global coordinates
            const mapPosEl = document.getElementById('mapPosition');
            if (mapPosEl) mapPosEl.textContent = `(${globalY},${globalX})`;
        }

        // Initial setup
        document.addEventListener('DOMContentLoaded', () => {
            const teamContainer = document.getElementById('pokemon-team');
            teamContainer.innerHTML = '';
            for (let i = 0; i < 6; i++) {
                teamContainer.appendChild(createEmptySlot());
            }
            loadQuestDefinitions();
            loadPlayerSprites();
            loadMapData();
            loadQuestPaths();
        });

        // Show speech bubble
        function showSpeechBubble(text, type = 'quest_start', duration = 4000) {
            const overlay = document.getElementById('speechBubbleOverlay');
            clearTimeout(gameState.speechBubbleTimer);
            overlay.innerHTML = '';
            const bubble = document.createElement('div');
            bubble.className = `speech-bubble ${type === 'quest_complete' ? 'quest-complete' : ''}`;
            const textEl = document.createElement('div');
            textEl.className = 'speech-bubble-text';
            textEl.textContent = text;
            bubble.appendChild(textEl);
            overlay.appendChild(bubble);
            setTimeout(() => bubble.classList.add('show'), 50);
            if (duration > 0) {
                gameState.speechBubbleTimer = setTimeout(() => {
                    bubble.classList.remove('show');
                    setTimeout(() => overlay.innerHTML = '', 300);
                }, duration);
            }
        }

        // Update quest display
        async function updateQuestDisplay(questId) {
            await loadQuestDefinitions();
            const section = document.getElementById('questSection');
            if (!questId || !gameState.questDefinitions) {
                section.style.display = 'none';
                return;
            }
            const quest = gameState.questDefinitions.find(q => 
                parseInt(q.quest_id) === parseInt(questId)
            );
            if (!quest) {
                console.log(`Quest ${questId} not found`);
                section.style.display = 'none';
                return;
            }
            section.style.display = 'block';
            document.getElementById('questTitle').textContent = `QUEST ${quest.quest_id}:`;
            document.getElementById('questDescription').textContent = quest.begin_quest_text || '';
            const triggersEl = document.getElementById('questTriggers');
            triggersEl.innerHTML = '';
            const subquests = quest.subquest_list || [];
            const triggers = quest.event_triggers || [];
            let completedCount = 0;
            subquests.forEach((sub, idx) => {
                const li = document.createElement('li');
                li.className = 'quest-item';
                li.textContent = sub;
                if (idx < triggers.length) {
                    const tid = `${questId}_${idx}`;
                    if (gameState.questData.triggers && gameState.questData.triggers[tid]) {
                        li.classList.add('completed');
                        completedCount++;
                    }
                }
                triggersEl.appendChild(li);
            });
            const progress = subquests.length ? (completedCount/subquests.length)*100 : 0;
            document.getElementById('questProgress').style.width = `${progress}%`;
            if (progress === 100 && gameState.questData.quests && !gameState.questData.quests[questId]) {
                gameState.questData.quests[questId] = true;
                if (quest.end_quest_text) {
                    showSpeechBubble(quest.end_quest_text, 'quest_complete', 5000);
                }
            }
        }

        // Update Grok status
        function updateGrokStatus(thinking, response) {
            const thinkingEl = document.getElementById('grokThinking');
            const responseEl = document.getElementById('grokResponse');
            const waitingEl = document.getElementById('grokWaiting');
            if (thinking) {
                const match = thinking.match(/Current situation:\s*([\s\S]*?)(?:Game stats:|$)/);
                // Display full (untruncated) reasoning; container now scrolls as needed
                thinkingEl.textContent = (match ? match[1].trim() : thinking);
                thinkingEl.style.display = 'block';
                waitingEl.style.display = 'none';
            } else thinkingEl.style.display = 'none';
            if (response) {
                responseEl.textContent = response;
                responseEl.style.display = 'block';
                waitingEl.style.display = 'none';
            } else responseEl.style.display = 'none';
            if (!thinking && !response) waitingEl.style.display = 'block';
        }

        // Experience calc
        function calculateExpForLevel(level, growthRate = 'medium_slow') {
            if (growthRate === 'medium_slow') {
                return Math.floor((6/5)*level**3 - 15*level**2 + 100*level - 140);
            }
            return 0;
        }

        // Pokemon types
        function getPokemonTypes(speciesName) {
            const map = {
                'charmander': ['fire'], 'charmeleon': ['fire'], 'charizard': ['fire','flying'],
                'squirtle':['water'],'wartortle':['water'],'blastoise':['water'],
                'bulbasaur':['grass','poison'],'ivysaur':['grass','poison'],'venusaur':['grass','poison'],
                'pikachu':['electric'],'raichu':['electric'],
                'pidgey':['normal','flying'],'pidgeotto':['normal','flying'],'pidgeot':['normal','flying'],
                'rattata':['normal'],'raticate':['normal'],
                'spearow':['normal','flying'],'fearow':['normal','flying'],
                'caterpie':['bug'],'metapod':['bug'],'butterfree':['bug','flying'],
                'weedle':['bug','poison'],'kakuna':['bug','poison'],'beedrill':['bug','poison']
            };
            return map[speciesName.toLowerCase()] || ['normal'];
        }

        // Update Pokemon team
        async function updatePokemonTeam(partyData) {
            const teamContainer = document.getElementById('pokemon-team');
            teamContainer.innerHTML = '';
            for (const p of partyData) {
                try {
                    const key = p.speciesName.toLowerCase();
                    let spriteUrl = localStorage.getItem(`pokemon_sprite_${key}`);
                    if (!spriteUrl) {
                        const resp = await fetch(`https://pokeapi.co/api/v2/pokemon/${key}`);
                        if (resp.ok) {
                            const d = await resp.json();
                            spriteUrl = d.sprites.front_default;
                            localStorage.setItem(`pokemon_sprite_${key}`, spriteUrl);
                        }
                    }
                    teamContainer.appendChild(createPokemonCardFromGameData(p, spriteUrl));
                } catch {
                    teamContainer.appendChild(createEmptySlot(`Error: ${p.nickname||'Unknown'}`));
                }
            }
            for (let i = partyData.length; i < 6; i++) {
                teamContainer.appendChild(createEmptySlot());
            }
        }

        // Create card
        function createPokemonCardFromGameData(pokemon, spriteUrl) {
            const card = document.createElement('div');
            card.className = 'pokemon-card';
            const hpPct = pokemon.maxHp>0?(pokemon.hp/pokemon.maxHp)*100:0;
            let hpClass = hpPct<=20?'low':hpPct<=50?'medium':'';
            const cur = pokemon.level, curExp = pokemon.experience||0;
            const prevExp = calculateExpForLevel(cur), nextExp = calculateExpForLevel(cur+1);
            const expPct = nextExp>prevExp?Math.max(0,Math.min(100,((curExp-prevExp)/(nextExp-prevExp))*100)):0;
            const expToNext = Math.max(0,nextExp-curExp);
            const speciesName = pokemon.speciesName||`#${pokemon.id}`;
            const nickname = pokemon.nickname||speciesName;
            const typesHtml = (pokemon.types||getPokemonTypes(speciesName))
                .map(t => `<span class="type-badge type-${t.toLowerCase()}">${t.toUpperCase()}</span>`).join('');
            const status = pokemon.status||'OK';
            const statusClass = status!=='OK'?` ${status}`:'';

            card.innerHTML = `
                <div class="pokemon-sprite-container">
                    <img src="${spriteUrl||'https://placehold.co/64x64/333/666?text=No+Sprite'}" 
                        alt="${speciesName}" class="pokemon-sprite"
                        onerror="this.src='https://placehold.co/64x64/333/666?text=Error';this.onerror=null;">
                </div>
                <div class="pokemon-card-info-wrapper">
                    <div class="pokemon-card-main-info">
                        <div class="pokemon-card-left">
                            <div class="pokemon-name">${nickname}</div>
                            <div class="pokemon-species-name">${speciesName}</div>
                        </div>
                        <div class="pokemon-card-right">
                            <div class="pokemon-level">Lv. ${pokemon.level}</div>
                        </div>
                    </div>
                    <div class="pokemon-types-row"><div class="pokemon-types">${typesHtml}</div></div>
                </div>
                <div class="pokemon-card-stats-footer">
                    <div class="hp-bar"><div class="hp-fill ${hpClass}" style="width:${hpPct}%"></div></div>
                    <div class="pokemon-card-bottom-details">
                        <div class="hp-text">${pokemon.hp}/${pokemon.maxHp}</div>
                        <div class="pokemon-status${statusClass}">${status}</div>
                    </div>
                    <div class="exp-bar"><div class="exp-fill" style="width:${expPct}%"></div></div>
                    <div class="exp-text mono">EXP: ${curExp.toLocaleString()} / ${expToNext.toLocaleString()}</div>
                </div>
            `;
            return card;
        }

        
        // Empty slot
        function createEmptySlot(text = '—') {
            const slot = document.createElement('div');
            slot.className = 'pokemon-card empty-slot';
            slot.textContent = text;
            return slot;
        }

        // Render action
        function renderAction(action) {
            const viz = document.getElementById('inputDisplay');
            const span = document.createElement('span');
            span.textContent = action;
            span.className = 'input-key';
            viz.appendChild(span);
            if (viz.children.length > 10) viz.removeChild(viz.children[0]);
        }

        // SSE connection
        const eventSource = new EventSource('/events');
        let firstConnect = true, lastServerId = null, reconnectTimer = null;
        const RECONNECT_FALLBACK_MS = 5000, WATCHDOG_INTERVAL_MS = 45000;
        let lastSseTime = Date.now();

        eventSource.onopen = () => {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        };

        eventSource.onerror = err => {
            console.error('SSE error:', err);
            clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(() => location.reload(), RECONNECT_FALLBACK_MS);
        };

        setInterval(() => {
            if (Date.now() - lastSseTime > WATCHDOG_INTERVAL_MS) {
                console.warn('SSE watchdog timeout—reloading');
                location.reload();
            }
        }, WATCHDOG_INTERVAL_MS / 2);

        eventSource.onmessage = async e => {
            if (!e.data) return;
            const msg = JSON.parse(e.data);

            // handle server restarts
            if (msg.type === 'connected') {
                const sid = msg.data?.server_id;
                if (lastServerId && sid && lastServerId !== sid) return location.reload();
                lastServerId = sid;
                if (!firstConnect) return location.reload();
                firstConnect = false;
            }

            switch (msg.type) {
                case 'location':
                    gameState.location = msg.data;
                    document.getElementById('statLocation').textContent   = msg.data.map_name || 'Unknown';
                    document.getElementById('currentMapName').textContent = msg.data.map_name || 'Unknown';
                    document.getElementById('statMapId').textContent      = msg.data.map_id   || '0';
                    document.getElementById('statLocal').textContent      = `(${msg.data.y||0},${msg.data.x||0})`;
                    document.getElementById('statGlobal').textContent     = `(${msg.data.gy||0},${msg.data.gx||0})`;
                    if (msg.data.gx!==undefined && msg.data.gy!==undefined) {
                        // Use precomputed global coordinates when available for highest accuracy
                        updatePlayerGlobal(msg.data.gx, msg.data.gy, gameState.facing);
                    } else if (msg.data.x!==undefined && msg.data.y!==undefined) {
                        updatePlayerPosition(msg.data.x, msg.data.y, msg.data.map_id, gameState.facing);
                        document.getElementById('mapPosition').textContent = `(${msg.data.gy},${msg.data.gx})`;
                    }
                    break;

                case 'facing_direction':
                    gameState.facing = msg.data;
                    if (gameState.location?.x!==undefined && gameState.location?.y!==undefined) {
                        updatePlayerPosition(
                            gameState.location.x,
                            gameState.location.y,
                            gameState.location.map_id,
                            gameState.facing
                        );
                    }
                    break;

                case 'current_quest':
                    {
                        const old = gameState.currentQuest;
                        gameState.currentQuest = msg.data;
                        await updateQuestDisplay(msg.data);
                        if (msg.data && msg.data!==old && gameState.questDefinitions) {
                            const q = gameState.questDefinitions.find(q=>parseInt(q.quest_id)===parseInt(msg.data));
                            if (q?.begin_quest_text) showSpeechBubble(q.begin_quest_text, 'quest_start');
                        }
                    }
                    break;

                case 'quest_data':
                    gameState.questData = msg.data;
                    if (gameState.currentQuest) await updateQuestDisplay(gameState.currentQuest);
                    break;

                case 'trigger_update':
                    gameState.questData.triggers[msg.data.id] = msg.data.completed;
                    if (gameState.currentQuest) await updateQuestDisplay(gameState.currentQuest);
                    break;

                case 'speech_bubble':
                    if (msg.data.text) showSpeechBubble(msg.data.text, msg.data.type, msg.data.duration);
                    break;

                case 'stats':
                    document.getElementById('statMoney').textContent   = `₽${msg.data.money||0}`;
                    document.getElementById('statSteps').textContent   = msg.data.steps||0;
                    document.getElementById('statBadges').textContent  = `${msg.data.badges||0}/8`;
                    document.getElementById('statSeen').textContent    = msg.data.pokedex_seen||0;
                    document.getElementById('statCaught').textContent  = msg.data.pokedex_caught||0;
                    break;

                case 'pokemon_team':
                    await updatePokemonTeam(msg.data);
                    break;

                case 'game_screen':
                    const gs = document.getElementById('gameScreen');
                    const ph = document.getElementById('gamePlaceholder');
                    if (gs && msg.data) {
                        gs.src = msg.data;
                        gs.style.display = 'block';
                        if (ph) ph.style.display = 'none';
                    }
                    break;

                case 'grok_thinking':
                    // Store the thinking for potential reuse
                    gameState.lastThinking = msg.data;
                    updateGrokStatus(msg.data, null);
                    
                    // Extract key decision points for left sidebar
                    extractDecisionPoints(msg.data);
                    break;

                // Update the SSE message handler for grok_response
                case 'grok_response':
                    // Parse and display the response appropriately
                    const response = msg.data;
                    
                    // Check if this is an action response
                    if (response.includes('Action') || response.includes('Tool')) {
                        // Move action to left sidebar
                        if (response.startsWith('Tool ')) {
                            const m = response.match(/Tool (\w+): (.+)/);
                            if (m) {
                                addActionToLog(m[1], m[2], 'tool');
                            }
                        } else {
                            const m = response.match(/Action (\d+): (.+)/);
                            if (m) {
                                const actionNum = parseInt(m[1]);
                                const actionName = ACTION_NAMES[actionNum] || `action_${actionNum}`;
                                addActionToLog(actionName, m[2], 'action');
                            }
                        }
                        // Don't show action responses in the right sidebar
                        updateGrokStatus(gameState.lastThinking, null);
                    } else {
                        // Non-action responses stay in right sidebar
                        updateGrokStatus(null, response);
                    }
                    break;

                case 'grok_cost':
                    const costEl = document.getElementById('grokCost');
                    if (msg.data) {
                        document.getElementById('apiCallsCount').textContent = msg.data.api_calls_count||0;
                        document.getElementById('totalTokens').textContent   = msg.data.total_tokens?.toLocaleString()||0;
                        document.getElementById('callCost').textContent      = msg.data.call_cost?`$${msg.data.call_cost.toFixed(4)}`:'$0.00';
                        document.getElementById('totalCost').textContent     = msg.data.total_cost?`$${msg.data.total_cost.toFixed(4)}`:'$0.00';
                        costEl.style.display = 'block';
                        document.getElementById('statCallCost').textContent     = msg.data.call_cost?`$${msg.data.call_cost.toFixed(4)}`:'$0.00';
                        document.getElementById('statLifetimeCost').textContent = msg.data.total_cost?`$${msg.data.total_cost.toFixed(2)}`:'$0.00';
                    } else costEl.style.display = 'none';
                    break;

                case 'action':
                    renderAction(msg.data);
                    break;

                case 'grok_enabled':
                    const grokStatusEl = document.getElementById('grokWaiting');
                    if (grokStatusEl) {
                        if (msg.data) {
                            grokStatusEl.textContent = 'Grok is active and thinking...';
                            grokStatusEl.style.color = '#10b981';
                        } else {
                            grokStatusEl.textContent = 'Grok is disabled. Click Start to enable.';
                            grokStatusEl.style.color = '#666';
                        }
                    }
                    break;
            }

            lastSseTime = Date.now();
        };
    </script>

</body>
</html>
'''

@app.route('/grok_test', methods=['GET'])
def test_grok():
    """Test if Grok is properly initialized"""
    try:
        # Import the global grok_agent from play module
        import sys
        play_module = sys.modules.get('__main__')
        
        if play_module and hasattr(play_module, 'grok_agent'):
            grok_agent = play_module.grok_agent
            is_initialized = grok_agent is not None
            agent_info = {
                'initialized': is_initialized,
                'type': type(grok_agent).__name__ if grok_agent else None,
                'has_client': hasattr(grok_agent, 'client') if grok_agent else False
            }
        else:
            agent_info = {
                'initialized': False,
                'error': 'Could not access play module or grok_agent'
            }
            
        return jsonify({
            'grok_enabled': grok_enabled.is_set(),
            'agent_info': agent_info
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'grok_enabled': grok_enabled.is_set()
        })


@app.route('/')
def index():
    """Serve the main web UI"""
    # Convert OmegaConf DictConfig to plain dict for JSON serialization
    config_dict = OmegaConf.to_container(_CONFIG, resolve=True)
    return render_template_string(HTML_TEMPLATE, CONFIG=config_dict)

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files"""
    # First try to serve from the standard web/static directory
    target_path = STATIC_DIR / filename
    if target_path.exists():
        return send_from_directory(STATIC_DIR, filename)

    # If not found, fall back to environment data folders needed by the front-end
    # 1) environment/data/environment_data/
    env_data_dir = Path(__file__).parent.parent / 'environment' / 'data' / 'environment_data'
    fallback = env_data_dir / Path(filename).name  # only support direct filenames here
    if target_path.as_posix().startswith('data/environment_data/'):
        # Preserve sub-path after the prefix
        rel = Path(filename).relative_to('data/environment_data')
        fallback = env_data_dir / rel

    if fallback.exists():
        return send_from_directory(fallback.parent, fallback.name, max_age=0)

    # 2) environment/environment_helpers/quest_paths/
    quest_dir = Path(__file__).parent.parent / 'environment' / 'environment_helpers' / 'quest_paths'
    if target_path.as_posix().startswith('data/environment_helpers/quest_paths/'):
        rel = Path(filename).relative_to('data/environment_helpers/quest_paths')
        fallback = quest_dir / rel
        if fallback.exists():
            return send_from_directory(fallback.parent, fallback.name, max_age=0)

    # Not found anywhere
    return "File not found", 404

@app.route('/events')
def events():
    """Server-Sent Events endpoint"""
    def generate():
        client_queue = Queue()
        
        with sse_lock:
            sse_clients.append(client_queue)
        
        try:
            # Send initial connection message with unique server_id (PID)
            yield (
                f"data: " + json.dumps({
                    'type': 'connected',
                    'data': {
                        'msg': 'Connected to game server',
                        'server_id': os.getpid()
                    }
                }) + "\n\n"
            )
            
            # Send current game state if available
            keys = ['location', 'stats', 'pokemon_team', 'current_quest', 'quest_data', 'grok_thinking', 'grok_response', 'grok_enabled']
            initial_updates = [(key, game_state.get(key)) for key in keys]

            for event_type, data in initial_updates:
                if data:
                    yield f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"
            
            # Send game screen if available
            if game_state.get('game_screen'):
                yield f"data: {json.dumps({'type': 'game_screen', 'data': game_state['game_screen']})}\n\n"
            
            # Stream updates
            while True:
                try:
                    message = client_queue.get(timeout=30)
                    yield message
                except Empty:
                    # Send keepalive
                    yield f"data: {json.dumps({'type': 'keepalive', 'data': time.time()})}\n\n"
                    
        finally:
            with sse_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)
    
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )

@app.route('/game-state')
def get_game_state():
    """Get current game state as JSON"""
    return jsonify(game_state)

@app.route('/status')
def status():
    """Health check endpoint"""
    return jsonify({
        'status': 'running',
        'last_update': game_state.get('last_update', 0),
        'connected_clients': len(sse_clients),
        'current_quest': game_state.get('current_quest'),
        'location': game_state.get('location', {}).get('map_name', 'Unknown')
    })

@app.route('/required_completions.json')
def required_completions():
    """Serve the quest definitions file"""
    try:
        quests = load_quest_definitions()
        return jsonify(quests)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Agent control endpoints
# start/stop endpoints should use the shared event grok_enabled
@app.route('/start', methods=['POST'])
def start_grok():
    """Start the Grok agent"""
    try:
        # Set the grok_enabled flag
        grok_enabled.set()
        logger.info("GROK: Grok agent enabled via web UI")
        
        # Broadcast the status update
        broadcast_update('grok_enabled', True)
        
        # Return the expected JSON format
        return jsonify({'status': 'success', 'message': 'Grok agent started'})
    except Exception as e:
        logger.error(f"Error starting Grok: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_grok():
    """Stop the Grok agent"""
    try:
        # Clear the grok_enabled flag
        grok_enabled.clear()
        logger.info("GROK: Grok agent disabled via web UI")
        
        # Broadcast the status update
        broadcast_update('grok_enabled', False)
        
        # Return the expected JSON format
        return jsonify({'status': 'success', 'message': 'Grok agent stopped'})
    except Exception as e:
        logger.error(f"Error stopping Grok: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/pause', methods=['POST'])
def pause_grok():
    """Toggle the Grok agent pause state"""
    try:
        # Toggle the state
        if grok_enabled.is_set():
            grok_enabled.clear()
            paused = True
        else:
            grok_enabled.set()
            paused = False
        
        status_msg = 'paused' if paused else 'resumed'
        logger.info(f"GROK: Grok agent {status_msg}")
        
        # Broadcast the status update
        broadcast_update('grok_enabled', not paused)
        
        # Return the expected JSON format
        return jsonify({'status': 'success', 'message': f'Grok agent {status_msg}'})
    except Exception as e:
        logger.error(f"Error pausing/resuming Grok: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/grok_status', methods=['GET'])
def get_grok_status():
    """Get the current Grok agent status"""
    try:
        return jsonify({'enabled': grok_enabled.is_set()})
    except Exception as e:
        return jsonify({'enabled': False, 'error': str(e)}), 500

@app.route('/grok_usage', methods=['GET'])
def get_grok_usage():
    """Get Grok token usage and cost statistics"""
    try:
        # Get stats from game_state if available
        cost_stats = game_state.get('grok_cost', {})
        
        # If not in game_state, try to get directly from the agent
        import sys
        play_module = sys.modules.get('__main__')
        if not cost_stats and play_module and hasattr(play_module, 'grok_agent'):
            grok_agent = play_module.grok_agent
            if grok_agent and hasattr(grok_agent, 'get_token_usage_stats'):
                cost_stats = grok_agent.get_token_usage_stats()
        
        # Add pricing information
        cost_stats['pricing'] = {
            'input_rate': '0.30 per 131,072 tokens',
            'cached_input_rate': '0.075 per 131,072 tokens',
            'completion_rate': '0.50 per 131,072 tokens',
            'model': 'grok-3-mini'
        }
        
        return jsonify(cost_stats)
    except Exception as e:
        logger.error(f"Error retrieving Grok usage stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/is_started', methods=['GET'])
def is_started():
    return jsonify(started=game_started.is_set())

def start_server(status_queue, host='0.0.0.0', port=8080):
    """Start the Flask server with status queue monitoring"""
    # Start status queue monitor thread
    monitor_thread = threading.Thread(
        target=monitor_status_queue, 
        args=(status_queue,), 
        daemon=True
    )
    monitor_thread.start()
    
    # Pre-load quest definitions
    load_quest_definitions()
    
    print(f"🎮 Game server starting on http://{host}:{port}")
    print(f"📊 Status queue monitor started")
    print(f"🗺️  Global map tracking enabled") 
    print(f"💬 Speech bubble system active")
    print(f"📜 Quest system initialized with {len(quest_definitions or [])} quests")
    print(f"📁 Static files served from: {STATIC_DIR}")
    
    # Signal that the game has started when the web server is up
    from shared import game_started
    game_started.set()
    
    # 🔄  Enable Grok immediately so manual 'Start Grok' click is unnecessary
    if hasattr(_CONFIG, 'grok') and _CONFIG.grok.enabled:
        grok_enabled.set()
        broadcast_update('grok_enabled', True)
    
    # Run Flask server – enable reloader only when we are running in the main
    # thread (e.g. `python web_server.py`).  When the server is launched from
    # within `play.py` it runs in a background thread and signal-based reloaders
    # are disallowed; in that case we fall back to a plain threaded server.
    in_main_thread = threading.current_thread() is threading.main_thread()

    app.run(
        host=host,
        port=port,
        debug=in_main_thread,          # debug implies nice tracebacks
        use_reloader=in_main_thread,   # only safe in main thread
        threaded=True
    )

@app.route('/global-map.png')
def global_map_png():
    """Dynamically generate and serve the quest-overlay global map."""
    out_path = STATIC_DIR / 'images' / 'kanto_map.png'
    # Generate full-size quest-overlay map (do not crop) so front-end sizing remains stable
    quest_dir = Path(__file__).parent.parent / 'environment' / 'environment_helpers' / 'quest_paths'
    build_quest_map(output_path=out_path, quest_dir=quest_dir, n=10, crop=False)
    # # Optionally overlay current player position as a red rectangle
    # # Player sprite overlay now handled in javascript code
    # player = game_state.get('global_map_player')
    # if player:
    #     gy, gx = player.get('gy', 0), player.get('gx', 0)
    #     # Convert padded global to tile coords
    #     tx = gx - PAD_COL
    #     ty = gy - PAD_ROW
    #     # Load image and draw
    #     img = Image.open(out_path).convert('RGBA')
    #     draw = ImageDraw.Draw(img)
    #     x0, y0 = tx * TILE_SIZE, ty * TILE_SIZE
    #     draw.rectangle([x0, y0, x0 + TILE_SIZE - 1, y0 + TILE_SIZE - 1], outline=(255, 0, 0), width=2)
    #     img.save(out_path)
    return send_from_directory(out_path.parent, out_path.name, max_age=0)

@app.route('/static/images/pokemon_red_player_spritesheet.png')
def serve_spritesheet():
    """Serve the player spritesheet"""
    try:
        spritesheet_path = Path(__file__).parent.parent / 'environment' / 'data' / 'environment_data' / 'pokemon_red_player_spritesheet.png'
        if spritesheet_path.exists():
            return send_from_directory(spritesheet_path.parent, spritesheet_path.name)
        else:
            # If not found, return a placeholder or error
            return "Spritesheet not found", 404
    except Exception as e:
        print(f"Error serving spritesheet: {e}")
        return "Error loading spritesheet", 500

# ---------------------------------------------------------------------------
# Additional static data endpoints needed by the front-end
# ---------------------------------------------------------------------------

# Map data JSON (used by loadMapData() in the front-end JS)
@app.route('/static/data/environment_data/<path:filename>')
def serve_environment_data(filename):
    """Serve files from environment/data/environment_data/ directory."""
    try:
        data_dir = Path(__file__).parent.parent / 'environment' / 'data' / 'environment_data'
        target = data_dir / filename
        if target.exists():
            return send_from_directory(target.parent, target.name, max_age=0)
        else:
            return "File not found", 404
    except Exception as e:
        print(f"Error serving environment data file {filename}: {e}")
        return "Error loading file", 500

# Combined quest coordinates JSON (used by loadQuestPaths() in the front-end JS)
@app.route('/static/data/environment_helpers/quest_paths/<path:filename>')
def serve_quest_path_data(filename):
    """Serve files from environment/environment_helpers/quest_paths/ directory."""
    try:
        quest_dir = Path(__file__).parent.parent / 'environment' / 'environment_helpers' / 'quest_paths'
        target = quest_dir / filename
        if target.exists():
            return send_from_directory(target.parent, target.name, max_age=0)
        else:
            return "File not found", 404
    except Exception as e:
        print(f"Error serving quest path data file {filename}: {e}")
        return "Error loading file", 500

if __name__ == '__main__':
    # Developer-run mode: start an *empty* queue and print instructions.
    from queue import SimpleQueue
    print("\n📖  Running web_server directly. For full game data run play.py, which\n     starts the emulator, status queue, and this server automatically.\n     This standalone mode is for UI work only – you will not see live\n     game frames.\n")
    start_server(SimpleQueue())