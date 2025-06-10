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

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: #0a0a0a;
            color: #ffffff;
            overflow: hidden;
            height: 100vh;
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Main layout */
        .stream-container {
            display: grid;
            grid-template-columns: 2fr 1fr 300px;
            grid-template-rows: auto 1fr auto;
            height: 100vh;
            background-color: #1a1a1a;
            gap: 1px;
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
            font-size: 36px;
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
            padding: 8px 16px;
            background: #dc2626;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .live-dot {
            width: 8px;
            height: 8px;
            background: #fff;
            border-radius: 50%;
            animation: pulse 1s ease-in-out infinite;
        }

        /* Main content area */
        .main-content {
            display: grid;
            grid-template-columns: 2fr 1fr; /* Two-column layout */
            grid-template-rows: auto 1fr; /* Let rows size naturally */
            height: calc(100vh - 140px); /* Account for header and footer */
            overflow: hidden;
            gap: 1px;
            grid-column: 1 / -1; /* Span all columns of parent */
            grid-row: 2;
        }

        /* Game area */
        .game-area {
            grid-column: 1;
            grid-row: 1 / span 2; /* Span both rows */
            padding: 20px;
            display: flex;
            flex-direction: column;
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
            font-size: 18px;
        }

        /* Speech Bubble */
        .speech-bubble-overlay {
            position: absolute;
            top: 20px;
            right: 20px;
            max-width: 650px;
            z-index: 1000;
            pointer-events: none;
        }

        .speech-bubble {
            background: rgba(255, 248, 220, 0.95);
            border: 8px solid #FFA500;
            border-radius: 15px;
            padding: 15px 20px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
            opacity: 0;
            transform: scale(0.8) translateY(-20px);
            transition: all 0.3s ease;
            position: relative;
            max-width: 650px;
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
            font-size: 16px;
            font-weight: 600;
            line-height: 1.4;
        }

        .quest-complete .speech-bubble-text {
            color: #006400;
        }

        /* Enhanced Sidebar */
        .sidebar-and-map {
            grid-column: 2;
            grid-row: 1 / span 2; /* Span both rows */
            display: flex;
            flex-direction: column;
            gap: 1px;
            background: #1a1a1a;
            overflow: hidden;
        }

        .sidebar-section {
            background: #0a0a0a;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
            flex-grow: 1;
        }

        .section-title {
            font-size: 31px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #999;
            margin-bottom: 12px;
        }

        /* Global Map Container for fixed viewport */
        .global-map-container {
            height: 550px;
            overflow: hidden;
            position: relative; /* Needed for centering the player sprite */
            background: #0a0a0a;
            padding: 20px;
            border-top: 1px solid #1a1a1a;
        }

        /* Global Map Section - Fixed to show world map */
        .global-map-section {
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 15px;
            height: 100%;
        }

        .map-canvas {
            width: 100%;
            height: 90%;
            background: #0a0a0a;
            border: 1px solid #222;
            border-radius: 4px;
            position: relative;
            overflow: hidden; /* This crops the map */
            margin-bottom: 10px;
        }

        #globalMapImage {
            position: absolute;
            image-rendering: pixelated;
            display: none; /* Hide until positioned */
            transition: transform 0.1s linear;
        }

        .player-sprite {
            position: absolute;
            width: 8px;
            height: 8px;
            background: #ff0000;
            border: 1px solid #fff;
            border-radius: 50%;
            z-index: 10;
            /* Center the sprite in the container */
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
            font-size: 12px;
            color: #999;
        }

        /* Quest Section */
        .quest-section {
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 15px;
        }

        .quest-description {
            color: #ccc;
            margin-bottom: 15px;
            font-size: 14px;
            line-height: 1.5;
        }

        .quest-list {
            list-style: none;
            padding: 0;
            margin: 0 0 15px 0;
        }

        .quest-item {
            margin-bottom: 8px;
            color: #999;
            font-size: 13px;
            display: flex;
            align-items: flex-start;
            gap: 10px;
        }

        .quest-item::before {
            content: '□';
            font-size: 16px;
            color: #666;
            flex-shrink: 0;
        }

        .quest-item.completed {
            color: #10b981;
            text-decoration: line-through;
            text-decoration-color: #666;
        }

        .quest-item.completed::before {
            content: '☑';
            color: #10b981;
        }

        .quest-progress-bar {
            width: 100%;
            height: 6px;
            background: #2a2a2a;
            border-radius: 3px;
            overflow: hidden;
        }

        .quest-progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #3b82f6, #8b5cf6);
            transition: width 0.5s ease;
        }

        /* Grok Status Section */
        .grok-status-section {
            height: 100%;
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 15px;
        }

        .grok-message {
            height: 100%;
            margin-bottom: 10px;
            padding: 8px 12px;
            background: #1a1a1a;
            border-radius: 4px;
            font-size: 13px;
            line-height: 1.4;
        }

        .grok-thinking {
            border-left: 3px solid #f59e0b;
            height: 100%;
        }

        .grok-response {
            border-left: 3px solid #10b981;
            height: 100%;
        }

        /* Team section styling */
        # /*.team-display-area {
        #     padding: 20px;
        #     background: #0a0a0a;
        #     border-top: 1px solid #1a1a1a;
        # }*/
        
        .team-display-area {
            grid-column: 1;
            grid-row: 3;
            padding: 20px;
            border-top: 1px solid #1a1a1a;
            background: #0a0a0a;
            /* ADDED: Prevent content from overflowing/pushing */
            overflow: hidden;
        }

        /* Team section */
                /* Team section */
        .team-grid {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 16px;
            width: 100%;
        }

        .pokemon-card {
            background: #111;
            border: 1px solid #2a2a2a;
            border-radius: 8px;
            padding: 12px;
            transition: all 0.2s ease;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: space-between;
            min-height: 320px;
        }

        .pokemon-card:hover {
            background: #181818;
            border-color: #444;
            transform: translateY(-4px);
        }

        .pokemon-sprite-container {
            width: 100%;
            aspect-ratio: 1/1;
            background: linear-gradient(145deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.08));
            border-radius: 6px;
            padding: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 10px;
        }

        .pokemon-sprite {
            width: 100%;
            height: 100%;
            max-width: 396px;
            max-height: 396px;
            image-rendering: pixelated;
            filter: brightness(1.1) contrast(1.1);
            object-fit: contain;
            animation: bounce 1ms ease-in-out infinite;
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
            margin-bottom: 8px;
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
            font-size: 32px;
            font-weight: 600;
            text-transform: uppercase;
            color: #e0e0e0;
            line-height: 1.1;
        }

        .pokemon-species-name {
            font-size: 26px;
            font-weight: 500;
            color: #bbb;
            text-transform: capitalize;
            margin-top: 2px;
            line-height: 1.1;
        }

        .pokemon-level {
            font-size: 38px;
            color: #e0e0e0;
            font-weight: 600;
            line-height: 1.1;
        }

        .pokemon-types-row {
            width: 100%;
            display: flex;
            justify-content: flex-end;
            margin-bottom: 8px;
            min-height: 50px;
        }

        .pokemon-types {
            display: flex;
            flex-direction: column;
            gap: 4px;
            align-items: flex-end;
        }

        .type-badge {
            font-size: 22px;
            padding: 2px 6px;
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
            height: 22px;
            background: #2a2a2a;
            border-radius: 3px;
            overflow: hidden;
            width: 100%;
            margin-bottom: 6px;
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
        }

        .hp-text {
            font-size: 26px;
            color: #aaa;
            font-variant-numeric: tabular-nums;
            line-height: 1.1;
        }

        .pokemon-status {
            font-size: 26px;
            font-weight: 600;
            color: #aaa;
            text-transform: uppercase;
            line-height: 1.1;
        }

        /* Type colors */
        .type-normal {
            background: #9d9d9a;
            color: #000;
        }

        .type-fire {
            background: #F08030;
            color: #000;
        }

        .type-water {
            background: #6890F0;
            color: #fff;
        }

        .type-electric {
            background: #F8D030;
            color: #000;
        }

        .type-grass {
            background: #78C850;
            color: #000;
        }

        .type-ice {
            background: #98D8D8;
            color: #000;
        }

        .type-fighting {
            background: #C03028;
            color: #fff;
        }

        .type-poison {
            background: #A040A0;
            color: #fff;
        }

        .type-ground {
            background: #E0C068;
            color: #000;
        }

        .type-flying {
            background: #A890F0;
            color: #000;
        }

        .type-psychic {
            background: #F85888;
            color: #fff;
        }

        .type-bug {
            background: #A8B820;
            color: #000;
        }

        .type-rock {
            background: #B8A038;
            color: #000;
        }

        .type-ghost {
            background: #705898;
            color: #fff;
        }

        .type-dragon {
            background: #7038F8;
            color: #fff;
        }

        .type-dark {
            background: #705848;
            color: #fff;
        }

        .type-steel {
            background: #B8B8D0;
            color: #000;
        }

        .type-fairy {
            background: #EE99AC;
            color: #000;
        }

        /* Empty slot */
        .empty-slot {
            background: #0a0a0a;
            border: 1px dashed #1a1a1a;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #333;
            font-size: 24px;
            min-height: 320px;
        }

        /* Bottom stats bar */
        .bottom-bar {
            grid-column: 1 / -1;
            background: #0a0a0a;
            border-top: 1px solid #1a1a1a;
            padding: 16px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            grid-row: 4;
            flex-wrap: wrap;
            /* Allow wrapping if footer gets crowded */
        }

        .bottom-stats {
            display: flex;
            gap: 25px;
            /* MODIFIED: Slightly reduced gap */
            flex-wrap: wrap;
            /* Allow wrapping */
            align-items: baseline;
        }

        .bottom-stat {
            display: flex;
            align-items: baseline;
            gap: 12px;
        }

        .bottom-stat-label {
            font-size: 22px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .bottom-stat-value {
            font-size: 26px;
            font-weight: 500;
            font-variant-numeric: tabular-nums;
        }
        
        
        
        
        /* OLD TEAM SECTION
        .team-grid-container {
            padding: 20px;
            background: #0a0a0a;
            border-top: 1px solid #1a1a1a;
        }

        .team-grid {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 12px;
        }

        .pokemon-card {
            background: #111;
            border: 1px solid #2a2a2a;
            border-radius: 6px;
            padding: 10px;
            text-align: center;
        }

        .pokemon-sprite {
            width: 64px;
            height: 64px;
            image-rendering: pixelated;
            margin: 0 auto 8px;
        }

        .pokemon-name {
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 4px;
        }

        .pokemon-level {
            font-size: 11px;
            color: #999;
        }

        .hp-bar {
            height: 4px;
            background: #2a2a2a;
            border-radius: 2px;
            margin-top: 6px;
            overflow: hidden;
        }

        .hp-fill {
            height: 100%;
            background: #10b981;
            transition: width 0.3s ease;
        }

        .hp-fill.medium { background: #f59e0b; }
        .hp-fill.low { background: #ef4444; }

        /* Bottom stats - Complete footer */
        .bottom-bar {
            grid-column: 1 / -1;
            background: #0a0a0a;
            border-top: 1px solid #1a1a1a;
            padding: 16px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .bottom-stats {
            display: flex;
            gap: 25px;
            flex-wrap: wrap;
            align-items: baseline;
            flex: 1;
        }

        .bottom-stat {
            display: flex;
            align-items: baseline;
            gap: 8px;
        }

        .bottom-stat-label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .bottom-stat-value {
            font-size: 14px;
            font-weight: 500;
            font-variant-numeric: tabular-nums;
        }

        .bottom-stat-value.cost {
            color: #2ecc71;
            font-weight: 600;
        } */

        /* Input visualization */
        .input-viz {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .input-key {
            padding: 4px 8px;
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 4px;
            font-size: 11px;
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
            gap: 10px;
        }

        .agent-controls button {
            padding: 6px 16px;
            border: none;
            border-radius: 4px;
            font-size: 14px;
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
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #444; }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
    </style>
    <!-- Inserted CONFIG for UI scripts -->
    <script>
        const CONFIG = {{ CONFIG | tojson }};
    </script>
    <!-- End CONFIG injection -->
</head>

<body>
    <div class="stream-container">
        <header class="header">
            <h1 class="title"><strong>GROK</strong> Plays Pokémon Red</h1>
            <div style="display: flex; align-items: center; gap: 24px;">
                <div style="display: flex; gap: 16px; font-size: 12px; color: #999;">
                    <div>
                        <span>Session</span>
                        <span class="mono" style="color: #fff; margin-left: 8px;">#0847</span>
                    </div>
                    <div>
                        <span>Uptime</span>
                        <span class="mono" style="color: #fff; margin-left: 8px;" id="uptime">00:00:00</span>
                    </div>
                </div>
                <div style="display: flex; align-items: center; gap: 16px;">
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

        <div class="main-content">
            <!-- Game Screen and Team Area -->
            <div class="game-area">
                <div class="game-screen-container">
                    <img id="gameScreen" alt="Game Screen" style="display: none;">
                    <div class="game-placeholder" id="gamePlaceholder">
                        Waiting for game capture...
                    </div>
                    <!-- Speech Bubble Overlay -->
                    <div class="speech-bubble-overlay" id="speechBubbleOverlay"></div>
                </div>
                
                <!-- Pokemon Team -->                
                <section class="panel team-display-area">
                    <h2 class="section-title"
                        style="text-align: center; margin-bottom: 20px; font-size: 36px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; color: #bbb;">
                        Active Team</h2>
                    <div class="team-grid" id="pokemon-team" style="max-width: 100%; margin: 0 auto;">
                        <div class="pokemon-card empty-slot">
                            <div class="loading"></div>
                        </div>
                    </div>
                </section>
                
                <!-- OLD TEAM SECTION
                <div class="team-grid-container">
                    <h2 class="section-title" style="text-align: center; margin-bottom: 16px;">Active Team</h2>
                    <div class="team-grid" id="pokemonTeam"></div>
                </div>
                -->
            </div>

            <!-- Sidebar and Map Column -->
            <div class="sidebar-and-map">
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
                            <div style="color: #666; font-size: 13px;" id="grokWaiting">Waiting for Grok to think...</div>
                        </div>
                    </div>
                </div>

                <!-- Global Map inside its own container -->
                <div class="global-map-container">
                    <div class="global-map-section">
                        <h2 class="section-title">Global Map</h2>
                        <div class="map-canvas" id="globalMap">
                            <div class="map-placeholder">
                                <div>Kanto region map</div>
                                <div style="font-size: 12px; margin-top: 8px;">Player position will be shown here</div>
                            </div>
                            <img id="globalMapImage" src="/static/images/kanto_map.png" alt="Kanto Map">
                            <div class="player-sprite" id="playerSprite" style="display: none;"></div>
                        </div>
                        <div class="map-info">
                            <span>Position: <span id="mapPosition">(0, 0)</span></span>
                            <span>Map: <span id="currentMapName">Unknown</span></span>
                        </div>
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
                    <span class="bottom-stat-label">(Y,X) Local</span>
                    <span class="bottom-stat-value mono" id="statLocal">(0,0)</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">(Y,X) Global</span>
                    <span class="bottom-stat-value mono" id="statGlobal">(0,0)</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Money</span>
                    <span class="bottom-stat-value mono" id="statMoney">₽0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Seen</span>
                    <span class="bottom-stat-value" id="statSeen">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Caught</span>
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
                    <span class="bottom-stat-label">Tokens</span>
                    <span class="bottom-stat-value mono" id="statLifetimeTokens">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Input</span>
                    <span class="bottom-stat-value mono" id="statInputTokens">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Output</span>
                    <span class="bottom-stat-value mono" id="statOutputTokens">0</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Step Cost</span>
                    <span class="bottom-stat-value mono cost" id="statStepCost">$0.00</span>
                </div>
                <div class="bottom-stat">
                    <span class="bottom-stat-label">Total Cost</span>
                    <span class="bottom-stat-value mono cost" id="statLifetimeCost">$0.00</span>
                </div>
            </div>
            <div class="input-viz" id="inputDisplay"></div>
        </footer>
    </div>

    <script>
        // Global state
        let gameState = {
            questDefinitions: null,
            questData: { quests: {}, triggers: {} },
            currentQuest: null,
            speechBubbleTimer: null,
            startTime: Date.now(),
            totalInputTokens: 0,
            totalOutputTokens: 0,
            lifetimeCost: 0
        };

        // World map coordinates mapping (adjust these based on your actual map)
        const WORLD_MAP_SCALE = 1; // Scale factor for world map
        const WORLD_MAP_OFFSET = { x: 0, y: 0 }; // Offset for positioning

        // Update uptime
        setInterval(() => {
            const elapsed = Date.now() - gameState.startTime;
            const hours = Math.floor(elapsed / 3600000);
            const minutes = Math.floor((elapsed % 3600000) / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);
            document.getElementById('uptime').textContent = 
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }, 1000);

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

        // Update player position on world map
        function updatePlayerPosition(gx, gy) {
            const playerSprite = document.getElementById('playerSprite');
            const mapImage = document.getElementById('globalMapImage');
            const mapCanvas = document.getElementById('globalMap');
            const mapPlaceholder = document.querySelector('.map-placeholder');

            if (!mapImage.complete || mapImage.naturalWidth === 0) {
                // If map image is not ready, try again shortly.
                setTimeout(() => updatePlayerPosition(gx, gy), 100);
                return;
            }

            // Hide placeholder and show map/player
            if (mapPlaceholder) mapPlaceholder.style.display = 'none';
            mapImage.style.display = 'block';
            playerSprite.style.display = 'block';
            
            // The environment sends coordinates based on a system that includes extra padding.
            // The rendering logic from the original UI expects coordinates without this extra padding.
            // We adjust for this difference here. The padding value (PAD) is 20, so the difference is 40.
            const render_gx = gx - 40;
            const render_gy = gy - 40;

            // Pan the map so the player's adjusted render position is in the center
            const canvasWidth = mapCanvas.offsetWidth;
            const canvasHeight = mapCanvas.offsetHeight;
            const mapLeft = (canvasWidth / 2) - render_gx;
            const mapTop = (canvasHeight / 2) - render_gy;
            mapImage.style.transform = `translate(${mapLeft}px, ${mapTop}px)`;

            // Update position display with original coordinates from the game environment
            document.getElementById('mapPosition').textContent = `(${gx}, ${gy})`;
        }

        // Show speech bubble
        function showSpeechBubble(text, type = 'quest_start', duration = 4000) {
            const overlay = document.getElementById('speechBubbleOverlay');
            
            // Clear existing bubble
            clearTimeout(gameState.speechBubbleTimer);
            overlay.innerHTML = '';
            
            // Create bubble
            const bubble = document.createElement('div');
            bubble.className = `speech-bubble ${type === 'quest_complete' ? 'quest-complete' : ''}`;
            
            const textEl = document.createElement('div');
            textEl.className = 'speech-bubble-text';
            textEl.textContent = text;
            
            bubble.appendChild(textEl);
            overlay.appendChild(bubble);
            
            // Animate in
            setTimeout(() => bubble.classList.add('show'), 50);
            
            // Auto-hide
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
            
            // Find quest
            const quest = gameState.questDefinitions.find(q => 
                parseInt(q.quest_id) === parseInt(questId)
            );
            
            if (!quest) {
                console.log(`Quest ${questId} not found in definitions`);
                section.style.display = 'none';
                return;
            }
            
            console.log(`Displaying quest ${questId}:`, quest);
            section.style.display = 'block';
            
            // Update title and description
            document.getElementById('questTitle').textContent = 
                `Quest ${quest.quest_id}: ${quest.quest_name || ''}`;
            document.getElementById('questDescription').textContent = 
                quest.begin_quest_text || '';
            
            // Update triggers/objectives
            const triggersEl = document.getElementById('questTriggers');
            triggersEl.innerHTML = '';
            
            const triggers = quest.event_triggers || [];
            let completedCount = 0;
            
            triggers.forEach(trigger => {
                const li = document.createElement('li');
                li.className = 'quest-item mono';
                li.textContent = trigger.description || `Trigger ${trigger.id}`;
                
                // Check completion status
                if (gameState.questData.triggers && gameState.questData.triggers[trigger.id]) {
                    li.classList.add('completed');
                    completedCount++;
                }
                
                triggersEl.appendChild(li);
            });
            
            // Update progress bar
            const progress = triggers.length > 0 ? (completedCount / triggers.length) * 100 : 0;
            document.getElementById('questProgress').style.width = `${progress}%`;
            
            // Check for quest completion
            if (progress === 100 && gameState.questData.quests && !gameState.questData.quests[questId]) {
                // Mark quest as complete and show completion message
                if (!gameState.questData.quests) gameState.questData.quests = {};
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
                thinkingEl.textContent = `Thinking: ${thinking}`;
                thinkingEl.style.display = 'block';
                waitingEl.style.display = 'none';
            } else {
                thinkingEl.style.display = 'none';
            }
            
            if (response) {
                responseEl.textContent = `Action: ${response}`;
                responseEl.style.display = 'block';
                waitingEl.style.display = 'none';
            } else {
                responseEl.style.display = 'none';
            }
            
            if (!thinking && !response) {
                waitingEl.style.display = 'block';
            }
        }

        // Update Pokemon team
        async function updatePokemonTeam(partyData) {
            const teamContainer = document.getElementById('pokemon-team');
            teamContainer.innerHTML = ''; // Clear existing

            // Fetch sprites from PokeAPI and render each Pokemon
            for (const pokemon of partyData) {
                try {
                    // Cache check
                    let spriteUrl = localStorage.getItem(`pokemon_sprite_${pokemon.id}`);

                    if (!spriteUrl) {
                        // Fetch from PokeAPI
                        const response = await fetch(`https://pokeapi.co/api/v2/pokemon/${pokemon.id}`);
                        if (response.ok) {
                            const data = await response.json();
                            spriteUrl = data.sprites.front_default;
                            // Cache for future use
                            localStorage.setItem(`pokemon_sprite_${pokemon.id}`, spriteUrl);
                        }
                    }

                    const card = createPokemonCardFromGameData(pokemon, spriteUrl);
                    teamContainer.appendChild(card);

                } catch (error) {
                    console.error(`Failed to load Pokemon ${pokemon.id}:`, error);
                    const errorCard = createEmptySlot(`Error: ${pokemon.nickname || 'Unknown'}`);
                    teamContainer.appendChild(errorCard);
                }
            }

            // Fill empty slots
            for (let i = partyData.length; i < 6; i++) {
                teamContainer.appendChild(createEmptySlot());
            }
        }

        function createPokemonCardFromGameData(pokemon, spriteUrl) {
            const card = document.createElement('div');
            card.className = 'pokemon-card';

            const hpPercent = pokemon.maxHp > 0 ? (pokemon.hp / pokemon.maxHp) * 100 : 0;
            let hpClass = '';
            if (hpPercent <= 20) hpClass = 'low';
            else if (hpPercent <= 50) hpClass = 'medium';

            // Get species name from ID mapping (simplified)
            const speciesNames = {
                1: 'Bulbasaur', 4: 'Charmander', 7: 'Squirtle',
                16: 'Pidgey', 19: 'Rattata', 25: 'Pikachu',
                29: 'Nidoran♀', 32: 'Nidoran♂', 129: 'Magikarp'
                // Add more as needed
            };

            const speciesName = speciesNames[pokemon.id] || `Pokemon #${pokemon.id}`;

            card.innerHTML = `
                <div class="pokemon-sprite-container">
                    <img src="${spriteUrl || 'https://placehold.co/96x96/333333/666666?text=No+Sprite'}" 
                        alt="${speciesName}" 
                        class="pokemon-sprite" 
                        onerror="this.src='https://placehold.co/96x96/333333/666666?text=Error'; this.onerror=null;">
                </div>
                
                <div class="pokemon-card-info-wrapper">
                    <div class="pokemon-card-main-info">
                        <div class="pokemon-card-left">
                            <div class="pokemon-name">${pokemon.nickname || speciesName}</div>
                            <div class="pokemon-species-name">${speciesName}</div>
                        </div>
                        <div class="pokemon-card-right">
                            <div class="pokemon-level">Lv. ${pokemon.level}</div>
                        </div>
                    </div>

                    <div class="pokemon-types-row">
                        <div class="pokemon-types">
                            ${pokemon.types.map(type =>
                `<span class="type-badge type-${type.toLowerCase()}">${type.toUpperCase()}</span>`
            ).join('')}
                        </div>
                    </div>
                </div>

                <div class="pokemon-card-stats-footer">
                    <div class="hp-bar">
                        <div class="hp-fill ${hpClass}" style="width: ${hpPercent}%"></div>
                    </div>
                    <div class="pokemon-card-bottom-details">
                        <div class="hp-text">HP: ${pokemon.hp}/${pokemon.maxHp}</div>
                        <div class="pokemon-status">${pokemon.status}</div>
                    </div>
                </div>
            `;

            return card;
        }

        function createEmptySlot(text = '—') {
            const slot = document.createElement('div');
            slot.className = 'pokemon-card empty-slot';
            slot.textContent = text;
            return slot;
        }

        async function handleFullGameState(state) {
            // Update all components with full state
            if (state.location) updateLocation(state.location);
            if (state.quest_id) updateQuest(`Quest ${String(state.quest_id).padStart(3, '0')}`);
            if (state.party) await updatePokemonTeam(state.party);
            if (state.money !== undefined) {
                updateStats({
                    money: state.money,
                    badges: state.badges,
                    pokedex: `${state.pokedex_caught}/${state.pokedex_seen}`,
                    steps: state.steps
                });
            }
        }

        // Initialize with placeholder while waiting for real data
        document.addEventListener('DOMContentLoaded', () => {
            const teamContainer = document.getElementById('pokemon-team');
            teamContainer.innerHTML = '';
            for (let i = 0; i < 6; i++) {
                teamContainer.appendChild(createEmptySlot());
            }
        });
        
        
        /* OLD TEAM UI JAVASCRIPT SECTION
        async function updatePokemonTeam(teamData) {
            const container = document.getElementById('pokemonTeam');
            container.innerHTML = '';
            
            // Create cards for each Pokemon
            for (let i = 0; i < 6; i++) {
                const pokemon = teamData[i];
                const card = document.createElement('div');
                card.className = 'pokemon-card';
                
                if (pokemon) {
                    const hpPercent = pokemon.maxHp > 0 ? (pokemon.hp / pokemon.maxHp) * 100 : 0;
                    const hpClass = hpPercent <= 20 ? 'low' : (hpPercent <= 50 ? 'medium' : '');
                    
                    card.innerHTML = `
                        <img class="pokemon-sprite" src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/${pokemon.id}.png" 
                             onerror="this.src='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='">
                        <div class="pokemon-name">${pokemon.nickname || 'Pokemon'}</div>
                        <div class="pokemon-level">Lv. ${pokemon.level}</div>
                        <div class="hp-bar">
                            <div class="hp-fill ${hpClass}" style="width: ${hpPercent}%"></div>
                        </div>
                    `;
                } else {
                    card.innerHTML = '<div style="color: #333; font-size: 24px;">—</div>';
                    card.style.opacity = '0.3';
                }
                
                container.appendChild(card);
            }
        }
        */

        // Update LLM usage
        function updateLLMUsage(data) {
            if (data.input_tokens) {
                gameState.totalInputTokens += data.input_tokens;
                document.getElementById('statInputTokens').textContent = gameState.totalInputTokens;
            }
            if (data.output_tokens) {
                gameState.totalOutputTokens += data.output_tokens;
                document.getElementById('statOutputTokens').textContent = gameState.totalOutputTokens;
            }
            document.getElementById('statLifetimeTokens').textContent = 
                gameState.totalInputTokens + gameState.totalOutputTokens;
            
            if (data.input_cost !== undefined && data.output_cost !== undefined) {
                const stepCost = data.input_cost + data.output_cost;
                gameState.lifetimeCost += stepCost;
                document.getElementById('statStepCost').textContent = `$${stepCost.toFixed(2)}`;
                document.getElementById('statLifetimeCost').textContent = `$${gameState.lifetimeCost.toFixed(2)}`;
            }
        }

        // Render action
        function renderAction(action) {
            const viz = document.getElementById('inputDisplay');
            const span = document.createElement('span');
            span.textContent = action;
            span.className = 'input-key';
            viz.appendChild(span);
            if (viz.children.length > 10) {
                viz.removeChild(viz.children[0]);
            }
        }

        // SSE Connection
        const eventSource = new EventSource('/events');
        
        eventSource.onmessage = async (e) => {
            if (!e.data) return;
            try {
                const msg = JSON.parse(e.data);
                console.log('SSE message:', msg.type, msg.data);
                
                switch(msg.type) {
                    case 'location':
                        // Update all location displays
                        document.getElementById('statLocation').textContent = msg.data.map_name || 'Unknown';
                        document.getElementById('currentMapName').textContent = msg.data.map_name || 'Unknown';
                        document.getElementById('statMapId').textContent = msg.data.map_id || '0';
                        document.getElementById('statLocal').textContent = `(${msg.data.y || 0},${msg.data.x || 0})`;
                        document.getElementById('statGlobal').textContent = `(${msg.data.gy || 0},${msg.data.gx || 0})`;
                        
                        // Update player position on world map
                        if (msg.data.gx !== undefined && msg.data.gy !== undefined) {
                            updatePlayerPosition(msg.data.gx, msg.data.gy);
                        }
                        break;
                        
                    case 'current_quest':
                        const oldQuest = gameState.currentQuest;
                        gameState.currentQuest = msg.data;
                        await updateQuestDisplay(msg.data);
                        
                        // Show quest start bubble if quest changed
                        if (msg.data && msg.data !== oldQuest && gameState.questDefinitions) {
                            const quest = gameState.questDefinitions.find(q => 
                                parseInt(q.quest_id) === parseInt(msg.data)
                            );
                            if (quest && quest.begin_quest_text) {
                                showSpeechBubble(quest.begin_quest_text, 'quest_start');
                            }
                        }
                        break;
                        
                    case 'quest_data':
                        gameState.questData = msg.data;
                        if (gameState.currentQuest) {
                            await updateQuestDisplay(gameState.currentQuest);
                        }
                        break;
                        
                    case 'trigger_update':
                        if (!gameState.questData.triggers) {
                            gameState.questData.triggers = {};
                        }
                        gameState.questData.triggers[msg.data.id] = msg.data.completed;
                        if (gameState.currentQuest) {
                            await updateQuestDisplay(gameState.currentQuest);
                        }
                        break;
                        
                    case 'speech_bubble':
                        if (msg.data.text) {
                            showSpeechBubble(msg.data.text, msg.data.type, msg.data.duration);
                        }
                        break;
                        
                    case 'stats':
                        document.getElementById('statMoney').textContent = `₽${msg.data.money || 0}`;
                        document.getElementById('statSteps').textContent = msg.data.steps || 0;
                        document.getElementById('statBadges').textContent = `${msg.data.badges || 0}/8`;
                        document.getElementById('statSeen').textContent = msg.data.pokedex_seen || 0;
                        document.getElementById('statCaught').textContent = msg.data.pokedex_caught || 0;
                        break;
                        
                    case 'pokemon_team':
                        await updatePokemonTeam(msg.data);
                        break;
                        
                    case 'game_screen':
                        const gameScreen = document.getElementById('gameScreen');
                        const placeholder = document.getElementById('gamePlaceholder');
                        if (gameScreen && msg.data) {
                            gameScreen.src = msg.data;
                            gameScreen.style.display = 'block';
                            if (placeholder) {
                                placeholder.style.display = 'none';
                            }
                        }
                        break;
                        
                    case 'grok_thinking':
                        updateGrokStatus(msg.data, null);
                        break;
                        
                    case 'grok_response':
                        updateGrokStatus(null, msg.data);
                        break;
                        
                    case 'action':
                        renderAction(msg.data);
                        break;
                        
                    case 'llm_usage':
                        updateLLMUsage(msg.data);
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
            } catch (err) {
                console.error('Error parsing SSE message', err);
            }
        };

        eventSource.onerror = (err) => {
            console.error('SSE connection error:', err);
        };

        // Agent control functions
        async function startAgent() {
            try {
                document.getElementById('start-btn').disabled = true;
                document.getElementById('start-btn').textContent = 'Starting...';
                
                const response = await fetch('/start', { method: 'POST' });
                
                // Check if response is ok before trying to parse JSON
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                // Check content type
                const contentType = response.headers.get("content-type");
                if (!contentType || !contentType.includes("application/json")) {
                    throw new TypeError("Response was not JSON");
                }
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    document.getElementById('start-btn').style.display = 'none';
                    document.getElementById('pause-btn').disabled = false;
                    document.getElementById('stop-btn').disabled = false;
                    console.log('Grok agent started successfully');
                } else {
                    alert('Failed to start Grok: ' + (result.message || 'Unknown error'));
                    document.getElementById('start-btn').disabled = false;
                    document.getElementById('start-btn').textContent = 'Start Grok';
                }
            } catch (error) {
                console.error('Error starting Grok agent:', error);
                alert('Error starting Grok agent: ' + error.message);
                document.getElementById('start-btn').disabled = false;
                document.getElementById('start-btn').textContent = 'Start Grok';
            }
        }

        async function pauseAgent() {
            try {
                const response = await fetch('/pause', { method: 'POST' });
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    const pauseBtn = document.getElementById('pause-btn');
                    if (result.message.includes('paused')) {
                        pauseBtn.textContent = 'Resume Grok';
                    } else {
                        pauseBtn.textContent = 'Pause Grok';
                    }
                    console.log('Grok agent pause state toggled');
                }
            } catch (error) {
                console.error('Error pausing/resuming Grok agent:', error);
            }
        }

        async function stopAgent() {
            try {
                const response = await fetch('/stop', { method: 'POST' });
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    document.getElementById('start-btn').style.display = 'inline-block';
                    document.getElementById('start-btn').disabled = false;
                    document.getElementById('start-btn').textContent = 'Start Grok';
                    document.getElementById('pause-btn').disabled = true;
                    document.getElementById('pause-btn').textContent = 'Pause Grok';
                    document.getElementById('stop-btn').disabled = true;
                    console.log('Grok agent stopped successfully');
                }
            } catch (error) {
                console.error('Error stopping Grok agent:', error);
            }
        }

        // Initialize on load
        document.addEventListener('DOMContentLoaded', () => {
            updatePokemonTeam([]);
            loadQuestDefinitions();
        });
    </script>
</body>
</html>'''

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
    return send_from_directory(STATIC_DIR, filename)

@app.route('/events')
def events():
    """Server-Sent Events endpoint"""
    def generate():
        client_queue = Queue()
        
        with sse_lock:
            sse_clients.append(client_queue)
        
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'data': 'Connected to game server'})}\n\n"
            
            # Send current game state if available
            keys = ['location', 'stats', 'pokemon_team', 'current_quest', 'quest_data', 'grok_thinking', 'grok_response']
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
    
    # Run Flask server
    app.run(host=host, port=port, debug=False, threaded=True)

if __name__ == '__main__':
    # Test mode
    from queue import SimpleQueue
    test_queue = SimpleQueue()
    
    # Add some test data
    test_queue.put(('__location__', {'x': 1, 'y': 6, 'map_id': 37, 'map_name': 'REDS_HOUSE_1F', 'gx': 76, 'gy': 354}))
    test_queue.put(('__current_quest__', 2))
    test_queue.put(('__stats_money', 999999))
    # # Send Grok status
    # test_queue.put(('__grok_enabled__', grok_enabled.is_set() if grok_agent else False))
    start_server(test_queue)
