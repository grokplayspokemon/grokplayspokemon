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

# Complete HTML template with ultra-premium bezel design
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Grok Plays Pokémon - Ultra Premium Bezel</title>
    <link
        href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&family=VT323&display=swap"
        rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        html {
            width: 100vw;
            height: 100vh;
            overflow: hidden;
        }

        body {
            margin: 0 !important;
            padding: 0 !important;
            width: 100vw !important;
            height: 100vh !important;
            background: #000 !important;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            perspective: 2000px;
            overflow: hidden !important;
            color: #ffffff;
            font-size: 20px;
            position: fixed !important;
            left: 0 !important;
            right: 0 !important;
            top: 0 !important;
            bottom: 0 !important;
        }

        /* Animated gradient background */
        body::before {
            content: '';
            position: fixed;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(ellipse at center, 
                rgba(120, 119, 198, 0.3) 0%, 
                rgba(255, 119, 198, 0.1) 25%, 
                rgba(120, 219, 255, 0.05) 50%, 
                transparent 70%);
            animation: nebula 20s ease-in-out infinite;
            pointer-events: none;
            z-index: -2;
        }

        @keyframes nebula {
            0%, 100% { transform: rotate(0deg) scale(1); opacity: 0.5; }
            50% { transform: rotate(180deg) scale(1.5); opacity: 0.8; }
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Main TV container with 3D transform - ABSOLUTE FULL SCREEN */
        .tv-container {
            position: absolute;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            transform-style: preserve-3d;
            animation: float 6s ease-in-out infinite;
            z-index: 10;
        }

        @keyframes float {
            0%, 100% { transform: translateY(0) rotateX(0) rotateY(0); }
            50% { transform: translateY(-3px) rotateX(0.2deg) rotateY(0.2deg); }
        }

        /* Minimalist bezel - NO PADDING, TRANSPARENT BACKGROUND */
        .tv-bezel {
            position: relative;
            width: 100%;
            height: 100%;
            background: transparent; /* Let global map show through */
            padding: 0; /* Remove all padding */
            overflow: hidden;
        }

        /* Holographic shimmer effect */
        .tv-bezel::before {
            content: '';
            position: absolute;
            top: -100%;
            left: -100%;
            width: 300%;
            height: 300%;
            background: linear-gradient(45deg,
                transparent 30%,
                rgba(255, 255, 255, 0.1) 50%,
                transparent 70%);
            transform: rotate(45deg);
            animation: shimmer 4s ease-in-out infinite;
            pointer-events: none;
        }

        @keyframes shimmer {
            0% { transform: translateX(-100%) translateY(-100%) rotate(45deg); }
            100% { transform: translateX(100%) translateY(100%) rotate(45deg); }
        }

        /* Game screen container - COMPACT, NO BLACK BACKGROUND */
        .screen-wrapper {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: auto; /* Fit content */
            height: auto; /* Fit content */
            background: transparent; /* No black background */
            border-radius: 4px;
            overflow: hidden;
            box-shadow: 
                0 0 20px rgba(0, 0, 0, 0.8), /* Subtle shadow around game screen */
                0 0 40px rgba(120, 119, 198, 0.2);
            z-index: 20;
        }

        .game-screen {
            position: relative;
            width: auto;
            height: auto;
            background: transparent;
            display: flex;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.9);
            font-size: 24px;
        }

        /* Premium LED strip */
        .led-strip {
            position: absolute;
            bottom: -1px;
            left: 20%;
            right: 20%;
            height: 2px;
            background: linear-gradient(90deg,
                transparent,
                #00ffff 20%,
                #ff00ff 50%,
                #00ffff 80%,
                transparent);
            filter: blur(1px);
            animation: led-flow 3s linear infinite;
            box-shadow: 0 0 20px currentColor;
        }

        @keyframes led-flow {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(100%); }
        }

        /* Brand logo - TOP BLACK BEZEL AREA */
        .brand {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: calc((100vh - 432px) / 2);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 36px;
            font-weight: 200;
            letter-spacing: 12px;
            background: rgba(0, 0, 0, 0.95);
            color: #00ff41;
            font-family: 'VT323', monospace;
            text-shadow: 0 0 20px #00ff41;
            z-index: 50;
            border-bottom: 2px solid #00ff41;
        }

        @keyframes glow-text {
            from { filter: drop-shadow(0 0 20px rgba(120, 119, 198, 0.5)); }
            to { filter: drop-shadow(0 0 30px rgba(255, 119, 198, 0.8)); }
        }

        /* Holographic status orb */
        .status-orb {
            position: absolute;
            top: 30px;
            right: 30px;
            width: 16px;
            height: 16px;
            background: radial-gradient(circle at 30% 30%, 
                #fff, 
                #00ffff 40%, 
                #0080ff 60%, 
                #4000ff);
            border-radius: 50%;
            box-shadow: 
                0 0 30px #00ffff,
                0 0 60px #0080ff,
                inset 0 0 10px rgba(255, 255, 255, 0.5);
            animation: orb-pulse 2s ease-in-out infinite;
        }

        @keyframes orb-pulse {
            0%, 100% { 
                transform: scale(1);
                box-shadow: 
                    0 0 30px #00ffff,
                    0 0 60px #0080ff,
                    inset 0 0 10px rgba(255, 255, 255, 0.5);
            }
            50% { 
                transform: scale(1.2);
                box-shadow: 
                    0 0 40px #00ffff,
                    0 0 80px #0080ff,
                    inset 0 0 15px rgba(255, 255, 255, 0.8);
            }
        }

        /* Ambient light strips on sides */
        .ambient-light {
            position: absolute;
            top: 20%;
            width: 2px;
            height: 60%;
            background: linear-gradient(to bottom,
                transparent,
                rgba(120, 119, 198, 0.6),
                rgba(255, 119, 198, 0.6),
                rgba(120, 219, 255, 0.6),
                transparent);
            filter: blur(8px);
            opacity: 0.5;
            animation: pulse-light 4s ease-in-out infinite;
        }

        .ambient-light.left {
            left: -20px;
        }

        .ambient-light.right {
            right: -20px;
        }

        @keyframes pulse-light {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 0.8; }
        }

        /* Ultra-thin speaker grilles */
        .speaker-grille {
            position: absolute;
            bottom: 35px;
            width: 60px;
            height: 1px;
            background: repeating-linear-gradient(90deg,
                transparent,
                transparent 2px,
                rgba(255, 255, 255, 0.1) 2px,
                rgba(255, 255, 255, 0.1) 3px);
        }

        .speaker-grille.left {
            left: 40px;
        }

        .speaker-grille.right {
            right: 40px;
        }

        /* Invisible touch controls */
        .touch-zone {
            position: absolute;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            width: 120px;
            height: 40px;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .touch-zone:hover::after {
            content: '⚡';
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 20px;
            animation: electric 0.5s ease-out;
        }

        @keyframes electric {
            0% { opacity: 0; transform: translate(-50%, -50%) scale(0); }
            50% { opacity: 1; transform: translate(-50%, -50%) scale(1.5); }
            100% { opacity: 0; transform: translate(-50%, -50%) scale(2); }
        }

        /* Particle effects */
        .particle {
            position: fixed;
            pointer-events: none;
            opacity: 0;
            animation: particle-rise 10s linear infinite;
        }

        @keyframes particle-rise {
            0% {
                opacity: 0;
                transform: translateY(100vh) translateX(0) scale(0);
            }
            10% {
                opacity: 1;
                transform: translateY(90vh) translateX(10px) scale(1);
            }
            90% {
                opacity: 1;
                transform: translateY(10vh) translateX(-10px) scale(1);
            }
            100% {
                opacity: 0;
                transform: translateY(0) translateX(0) scale(0);
            }
        }

        /* Responsive scaling - MAINTAIN COMPACT DESIGN */
        @media (max-width: 768px) {
            .brand {
                font-size: 18px;
                letter-spacing: 4px;
                top: 10px;
            }
            
            #gameScreen {
                width: 280px; /* Slightly smaller on mobile */
                height: 252px; /* Maintain aspect ratio */
            }
            
            .status-orb {
                top: 10px;
                right: 10px;
                width: 10px;
                height: 10px;
            }
        }

        /* Game screen styling - COMPACT SIZE */
        #gameScreen {
            width: 320px; /* Fixed game screen size */
            height: 288px; /* Fixed game screen size */
            object-fit: contain;
            image-rendering: pixelated;
            image-rendering: -moz-crisp-edges;
            image-rendering: crisp-edges;
            border: 2px solid rgba(255, 255, 255, 0.1); /* Subtle border to define the game area */
            background: #000; /* Keep game screen background black */
            border-radius: 4px;
        }

        /* Global map background - FORCE FULL SCREEN */
        .global-map-background {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            right: 0 !important;
            bottom: 0 !important;
            pointer-events: none;
            z-index: 1;
            width: 100vw !important;
            height: 100vh !important;
            overflow: hidden;
            opacity: 1.0;
            background: #1a3a2a !important; /* Dark green background to fill any gaps */
        }
        
        #globalMapBackground {
            position: absolute;
            width: auto;
            height: auto;
            object-fit: none;
            opacity: 1.0;
            image-rendering: pixelated;
            transform-origin: top left;
            filter: saturate(1.4) contrast(1.1) brightness(0.9);
            min-width: 150vw;
            min-height: 150vh;
        }

        /* Hidden elements - keeping for JavaScript compatibility */
        .left-sidebar,
        .right-sidebar,
        .center-content,
        .global-map-container,
        .quest-webui-container,
        .header-team-display,
        .title-overlay {
            display: none !important;
        }

        /* Electronic Terminal Footer - BOTTOM BLACK BEZEL AREA */
        .bottom-bar {
            position: fixed !important;
            bottom: 0 !important;
            left: 0 !important;
            right: 0 !important;
            height: calc((100vh - 432px) / 2) !important;
            background: rgba(0, 0, 0, 0.95) !important;
            border-top: 2px solid #00ff41 !important;
            border-radius: 0 !important;
            padding: 20px !important;
            font-family: 'VT323', monospace !important;
            font-size: 18px !important;
            color: #00ff41 !important;
            text-shadow: 0 0 5px #00ff41 !important;
            box-shadow: 
                0 0 20px rgba(0, 255, 65, 0.3),
                inset 0 0 20px rgba(0, 0, 0, 0.5) !important;
            z-index: 30 !important;
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 20px !important;
            align-items: center !important;
            justify-content: center !important;
        }

        .bottom-bar::before {
            content: "▶ GROK SYSTEM STATUS" !important;
            position: absolute !important;
            top: -12px !important;
            left: 15px !important;
            background: rgba(0, 0, 0, 0.9) !important;
            padding: 2px 8px !important;
            font-size: 12px !important;
            border: 1px solid #00ff41 !important;
            border-radius: 4px !important;
        }

        .bottom-bar .bottom-stat {
            font-family: 'VT323', monospace !important;
            white-space: nowrap !important;
            margin: 0 !important;
        }

        .bottom-bar .bottom-stat-label {
            color: #00aa2e !important;
            font-size: 14px !important;
            text-transform: uppercase !important;
        }

        .bottom-bar .bottom-stat-value {
            color: #00ff41 !important;
            font-size: 16px !important;
            font-weight: normal !important;
            text-shadow: 0 0 8px #00ff41 !important;
        }

        .bottom-bar .bottom-stat-value.cost {
            color: #ffff00 !important;
            text-shadow: 0 0 8px #ffff00 !important;
        }

        /* Terminal cursor animation */
        .bottom-bar::after {
            content: "█" !important;
            position: absolute !important;
            right: 15px !important;
            top: 50% !important;
            transform: translateY(-50%) !important;
            animation: terminal-blink 1s step-end infinite !important;
            color: #00ff41 !important;
        }

        @keyframes terminal-blink {
            50% { opacity: 0; }
        }

        /* Keep utility classes for JavaScript */
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
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
            background: #000000;
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
            flex: 1 1 auto; /* allow flexbox to shrink if necessary */
            min-height: 0; /* critical: permits shrinking inside sidebar */
            background: #111;
            border: 2px solid #333;
            border-radius: 8px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            overflow: hidden; /* contain children without expanding */
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
            max-height: 100%; /* prevent element from growing beyond container */
            /* Hide default scrollbars; scrolling handled in JS for smooth effect */
            scrollbar-width: none; /* Firefox */
        }
        
        #grokThinking::-webkit-scrollbar,
        #grokResponse::-webkit-scrollbar {
            display: none; /* Chrome, Safari */
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

        /* Header team display - now positioned above game screen */
        .header-team-display {
            position: fixed;
            top: 120px; /* Below header */
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            gap: 12px;
            align-items: flex-start;
            z-index: 6000;
            background: rgba(0,0,0,0.8);
            padding: 10px;
            border-radius: 8px;
        }

        .pokemon-card {
            background: rgba(17, 17, 17, 0.9);
            backdrop-filter: blur(10px);
            border: 1px solid #2a2a2a;
            border-radius: 4px;
            padding: 4px;
            transition: all 0.2s ease;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100px; /* Increased from 80px */
            width: 100px; /* Keep at 100px */
            position: relative;
            z-index: 1001;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .pokemon-card:hover {
            background: rgba(24, 24, 24, 0.95);
            border-color: #444;
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
        }

        .pokemon-sprite {
            width: 72px; /* Increased from 64px */
            height: 72px;
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
            font-size: 16px !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            color: #ff00ff !important;
            line-height: 1.1 !important;
            font-family: 'VT323', monospace !important;
            text-shadow: 0 0 3px #ff00ff !important;
        }

        .pokemon-species-name {
            font-size: 14px !important;
            font-weight: 500 !important;
            color: #ff77c6 !important;
            text-transform: capitalize !important;
            margin-top: 4px !important;
            line-height: 1.1 !important;
            font-family: 'VT323', monospace !important;
        }

        .pokemon-level {
            font-size: 18px; /* Doubled from 11px */
            color: #e0e0e0;
            font-weight: 600;
            line-height: 1.1;
        }

        .pokemon-name-level-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            width: 100%;
            margin-bottom: 0px;
        }

        .pokemon-name-above {
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            color: #e0e0e0;
            line-height: 1;
        }

        .pokemon-level-inline {
            font-size: 10px;
            color: #e0e0e0;
            font-weight: 600;
            line-height: 1;
        }

        .pokemon-nickname-below {
            font-size: 8px;
            font-weight: 500;
            color: #bbb;
            text-align: center;
            margin-bottom: 0px;
            line-height: 1;
        }

        .type-badge {
            font-size: 6px; /* Very small for header */
            padding: 1px 3px;
            border-radius: 2px;
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.3px;
        }

        .pokemon-card-stats-footer {
            width: 100%;
            margin-top: auto;
        }

        .hp-bar {
            height: 6px; /* Smaller for header */
            background: #2a2a2a;
            border-radius: 2px;
            overflow: hidden;
            width: 100%;
            margin-bottom: 0px;
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

        .hp-and-badges-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            width: 100%;
            margin-top: 0px;
        }

        .hp-text {
            font-size: 8px; /* Much smaller for header */
            color: #aaa;
            font-variant-numeric: tabular-nums;
            line-height: 1;
        }

        .type-status-badges {
            display: flex;
            gap: 6px;
            align-items: center;
        }

        .pokemon-types {
            display: flex;
            gap: 4px;
            align-items: center;
        }

        .pokemon-status {
            font-size: 6px; /* Very small for header */
            font-weight: 600;
            color: #aaa;
            text-transform: uppercase;
            line-height: 1;
            padding: 1px 2px;
            border-radius: 1px;
            background: rgba(255, 255, 255, 0.1);
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
            background: #000000;
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
            background: rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(20px);
            border-top: 1px solid rgba(64, 64, 64, 0.6);
            border-image: linear-gradient(90deg, 
                rgba(32, 32, 32, 0.8) 0%, 
                rgba(64, 64, 64, 0.8) 50%, 
                rgba(32, 32, 32, 0.8) 100%) 1;
            padding: 12px 48px; /* Account for TV bezel */
            display: flex;
            justify-content: space-between;
            align-items: center;
            grid-row: 3;
            flex-wrap: nowrap;
            max-height: 72px;
            overflow-y: hidden;
            overflow-x: hidden !important;
            gap: 24px;
            position: relative;
            margin: 0 24px 24px 24px; /* Inset from TV bezel */
            border-radius: 0 0 8px 8px;
            box-shadow: 
                0 -2px 16px rgba(0, 0, 0, 0.5),
                0 1px 0 rgba(255, 255, 255, 0.05) inset,
                0 -1px 0 rgba(64, 64, 64, 0.3) inset;
        }

        /* Futuristic glow effect behind footer */
        .bottom-bar::before {
            content: "";
            position: absolute;
            top: 0;
            left: -100%;
            width: 300%;
            height: 100%;
            background: linear-gradient(90deg, 
                transparent 0%, 
                rgba(32, 32, 32, 0.2) 25%, 
                rgba(64, 64, 64, 0.3) 50%, 
                rgba(32, 32, 32, 0.2) 75%, 
                transparent 100%);
            animation: footerScan 10s ease-in-out infinite reverse;
            z-index: -1;
        }

        /* Scanning animation for footer (slightly different timing) */
        @keyframes footerScan {
            0%, 100% { transform: translateX(0); opacity: 0.2; }
            50% { transform: translateX(10%); opacity: 0.6; }
        }

        .bottom-bar::-webkit-scrollbar {
            height: 6px;
        }

        .bottom-stat {
            white-space: nowrap; /* keep each stat on one line */
            flex: 0 0 auto; /* prevent shrinking text vertically */
        }

        .bottom-stats {
            display: flex;
            gap: 24px;
            flex-wrap: nowrap;
            align-items: center;
            flex: 1 1 auto;
            overflow: hidden;
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

        /* Compact team area */
        .team-display-area {
            max-height: none;
            overflow: visible;
        }
        .pokemon-card {
            min-height: 280px; /* Much smaller card */
        }
        .pokemon-sprite {
            max-width: 128px;
            max-height: 128px;
        }

        /* Quest UI container (scrollable) */
        .quest-webui-container {
            max-height: 220px;
            overflow-y: hidden; /* hide scrollbars */
            position: relative;
        }

        /* hide scrollbar for webkit */
        .quest-webui-container::-webkit-scrollbar {
            display: none;
        }
        .quest-webui-container {
            -ms-overflow-style: none; /* IE/Edge */
            scrollbar-width: none;  /* Firefox */
        }


        .global-map-background {
            position: fixed;
            top: 0;
            left: 0;
            pointer-events: none;
            z-index: 0;
            width: 100vw;
            height: 100vh;
            overflow: hidden;
        }
        
        #globalMapBackground {
            position: absolute;
            width: auto;
            height: auto;
            object-fit: none;
            opacity: 1.0;
            image-rendering: pixelated;
            transform-origin: top left;
            filter: saturate(1.4) contrast(1.1);
        }

        /* --- GAME SCREEN VISIBILITY FIX ------------------------------------- */
        /* Hide legacy UI containers */
        .global-map-container,
        .quest-webui-container,
        .left-sidebar,
        .right-sidebar,
        .center-content {
            display: none !important;
        }

        /* FORCE GAME SCREEN VISIBILITY - Inline styles override this anyway */

        /* Ensure game screen container doesn't interfere */
        .game-screen-container {
            display: none !important;
        }

        /* Hide placeholders */
        #gamePlaceholder,
        .speech-bubble-overlay {
            display: none !important;
        }

        /* --- UI ADJUSTMENTS (2025-06-19b) ------------------------------------ */
        /* Ensure footer (bottom-bar) is visible above overlays */
        .bottom-bar {
            display: flex !important;
            z-index: 2500;
            background: rgba(0,0,0,0.8);
        }

        /* Hide left sidebar completely */
        .left-sidebar {
            display: none !important;
        }

        /* Hide Grok thinking completely */
        #grokThinking {
            display: none !important;
        }

        /* Remove left sidebar header */

        /* Hide Grok control buttons */
        .agent-controls { display: none !important; }

        /* Hide team display area background */
        .team-display-area {
            display: none !important;
        }

        /* Remove team display area header */
        .team-grid {
            position: fixed !important;
            bottom: calc(calc((100vh - 432px) / 2) + 20px) !important; /* Above footer */
            right: 20px !important;
            display: flex !important;
            flex-direction: row !important; /* Horizontal layout */
            gap: 15px !important;
            z-index: 4000 !important;
            pointer-events: none; /* Allow clicks through container */
            /* Debug: temporary border to see positioning */
            border: 2px solid #ff0000 !important;
            min-height: 50px !important;
            min-width: 200px !important;
        }
        .pokemon-card {
            display: flex !important;
            flex-direction: column !important; /* Stack vertically within each card */
            align-items: center !important;
            background: rgba(20, 20, 20, 0.9) !important;
            border: 2px solid rgba(255, 0, 255, 0.6) !important;
            border-radius: 12px !important;
            padding: 20px !important;
            min-height: 160px !important; /* Bigger cards */
            min-width: 140px !important; /* Bigger cards */
            transition: all 0.3s ease !important;
            pointer-events: auto; /* Allow clicks on individual cards */
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.7) !important;
        }
        .pokemon-card:hover {
            background: rgba(40, 20, 40, 0.95) !important;
            border-color: rgba(255, 0, 255, 0.8) !important;
            box-shadow: 0 6px 30px rgba(255, 0, 255, 0.4) !important;
            transform: translateY(-5px) !important;
        }
        /* Bars column - adjusted for vertical card layout */
        .pokemon-card-stats-footer {
            display: flex;
            flex-direction: column;
            width: 100% !important;
            margin-right: 0 !important;
            margin-top: 10px !important;
        }
        .hp-bar, .exp-bar {
            width: 100% !important;
            height: 8px !important;
            border-radius: 4px !important;
            margin-bottom: 8px !important;
        }
        .hp-fill {
            border-radius: 4px !important;
        }
        .exp-fill {
            border-radius: 4px !important;
            background: #ff00ff !important;
        }
        .hp-text {
            font-size: 14px !important;
            color: #ff00ff !important;
            font-family: 'VT323', monospace !important;
            text-shadow: 0 0 3px #ff00ff !important;
        }
        /* Show Pokemon info for floating cards */
        .pokemon-card-info-wrapper { 
            display: flex !important; 
            flex-direction: column !important;
            align-items: center !important;
            text-align: center !important;
            width: 100% !important;
        }

        /* Sprite - bigger for floating cards */
        .pokemon-sprite-container {
            width: 120px !important;
            height: 120px !important;
            background: rgba(0, 0, 0, 0.6) !important;
            border-radius: 10px !important;
            padding: 10px !important;
            border: 2px solid rgba(255, 0, 255, 0.5) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            margin-bottom: 10px !important;
        }
        .pokemon-sprite {
            width: 100px !important;
            height: 100px !important;
            image-rendering: pixelated !important;
        }

        /* Hide empty slots */
        .empty-slot { display: none !important; }
        /* --------------------------------------------------------------------- */

        /* --- UI ADJUSTMENTS (2025-06-19d) ------------------------------------ */
        /* Further lift of game screen to avoid footer overlap fully */
        .game-screen-container {
            bottom: 92px; /* footer approx 72px + 20px gap */
        }

        /* Bring team column even closer */
        .team-display-area {
            right: calc(20px + 30vw + 5px);
        }

        /* Sprite should be to the RIGHT of bars */
        .pokemon-card {
            flex-direction: row-reverse;
        }
        .pokemon-card-stats-footer {
            width: 60px;
            margin-left: 6px;
            margin-right: 0;
        }
        /* --------------------------------------------------------------------- */

        /* --- UI ADJUSTMENTS (2025-06-19e) ------------------------------------ */
        /* 1. Lift game screen slightly above footer */
        .game-screen-container {
            bottom: 1140px; /* raise to clear footer fully */
        }

        /* 2. Bring team column tight to game screen and lift similarly */
        .team-display-area {
            bottom: 60px;
            right: calc(20px + 30vw + 10px);
        }

        /* 3. Ensure sprite appears on LEFT? user: pokemon to LEFT of bars previously, wants to RIGHT? They said pokemon should be to the LEFT of the health and exp bars (then earlier we reversed). Now re-check: earlier still left of bars; they want sprite left of bars? Wait they said earlier 'Pokemon should be to the LEFT of health and exp bars'— then we reversed; they said still left? Actually original: 'pokemon should be to the LEFT of the health and exp bars'. We misread earlier. Let's keep sprite left of bars (default row).*/
        .pokemon-card {
            flex-direction: row;
        }
        .pokemon-card-stats-footer {
            width: 60px;
            margin-right: 6px;
            margin-left: 0;
        }

        /* 4. Subtle per-sprite breathing animations (scale only, no translate/rotate) */
        .pokemon-sprite { will-change: transform; animation: none; }
        @keyframes breatheA { 0%,100% { transform: scale(1); } 50% { transform: scale(1.03); } }
        @keyframes breatheB { 0%,100% { transform: scale(1); } 50% { transform: scale(1.05); } }
        @keyframes breatheC { 0%,100% { transform: scale(1); } 50% { transform: scale(1.02); } }
        .team-grid .pokemon-card:nth-child(1) .pokemon-sprite { animation: breatheA 4s ease-in-out infinite; }
        .team-grid .pokemon-card:nth-child(2) .pokemon-sprite { animation: breatheB 5s ease-in-out infinite; }
        .team-grid .pokemon-card:nth-child(3) .pokemon-sprite { animation: breatheC 3.5s ease-in-out infinite; }
        .team-grid .pokemon-card:nth-child(4) .pokemon-sprite { animation: breatheA 4.5s ease-in-out infinite; }
        .team-grid .pokemon-card:nth-child(5) .pokemon-sprite { animation: breatheB 5.5s ease-in-out infinite; }
        .team-grid .pokemon-card:nth-child(6) .pokemon-sprite { animation: breatheC 6s ease-in-out infinite; }

        /* 5. Prevent any overflow-induced scrollbar flicker */
        .team-display-area::-webkit-scrollbar { width: 6px; }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-20b) ----------------------------------------- */
        /* Hide the Active Team heading permanently */
        .team-display-area h2 { display: none !important; }

        /* Ensure footer never shows horizontal scrollbar */
        .bottom-bar { overflow-x: hidden !important; }
        .bottom-bar::-webkit-scrollbar { display:none; }

        /* Place sprite to RIGHT of bars (bars left) */
        .pokemon-card { flex-direction: row-reverse !important; }
        .pokemon-card-stats-footer { margin-left: 6px !important; margin-right:0 !important; }

        /* Enlarge sprite visuals */
        .pokemon-sprite-container {
            width: 128px !important;
            height: 128px !important;
        }
        .pokemon-sprite {
            max-width: 128px !important;
            max-height: 128px !important;
        }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-20c) ----------------------------------------- */
        /* Expand team panel width to accommodate bars + sprite */
        .team-display-area {
            width: 220px !important;   /* sprite 128 + bars 64 + gaps */
            background: rgba(0,0,0,0.6) !important;
            border-radius: 8px;
            padding: 6px !important;
        }
        .pokemon-card-stats-footer { width: 74px !important; }
        .hp-bar, .exp-bar { width: 100% !important; }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-20d) ----------------------------------------- */
        .game-screen-container { bottom: 1240px !important; }
        /* --------------------------------------------------------------------- */
        /* Duplicate rule removed - game screen styling consolidated above */

        /* --- UI PATCH (2025-06-21) ----------------------------------------- */
        /* Gradient backdrop for team panel: opaque center, fades horizontal */
        .team-display-area {
            background: radial-gradient(ellipse at center, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.6) 40%, rgba(0,0,0,0) 100%) !important;
        }

        /* Elevate header so it's always visible */
        .header { z-index: 4000 !important; }

        /* Ensure overall map background stays below */
        .global-map-background { z-index: 0 !important; }

        /* Reveal pokemon info wrapper (names / types) */
        .pokemon-card-info-wrapper { display: block !important; flex-basis: 100%; margin-top: 4px; text-align: center; }
        .pokemon-name { font-size: 16px; font-weight:600; }
        .pokemon-species-name { font-size: 14px; color:#bbb; }
        .pokemon-types-row { justify-content:center; }

        /* Keep sprite right, bars left */
        .pokemon-card { flex-direction: row-reverse !important; align-items: flex-start; }
        .pokemon-card-stats-footer { width: 74px !important; margin-left:6px !important; margin-right:0 !important; }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-21b) better fade & width --------------------- */
        .team-display-area {
            width: 260px !important;  /* extra space for text/bars */
            background: radial-gradient(ellipse at center, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.65) 35%, rgba(0,0,0,0.3) 70%, rgba(0,0,0,0) 100%) !important;
        }
        .pokemon-card-stats-footer { width: 90px !important; }
        /* --------------------------------------------------------------------- */

        /* --- Pokemon Team: Retro TV Interface Style --------------------- */
        .team-display-area {
            width: 420px !important;
            background: 
                /* Retro monitor bezel effect */
                linear-gradient(135deg, rgba(80,80,80,0.3) 0%, rgba(40,40,40,0.4) 25%, rgba(20,20,20,0.6) 50%, rgba(40,40,40,0.4) 75%, rgba(80,80,80,0.3) 100%),
                /* Main background */
                linear-gradient(90deg,
                    rgba(0,0,0,0) 0%,
                    rgba(0,0,0,0.75) 15%,
                    rgba(0,0,0,0.85) 50%,
                    rgba(0,0,0,0.75) 85%,
                    rgba(0,0,0,0) 100%) !important;
            border: 2px solid rgba(100,100,100,0.2);
            border-radius: 12px;
            box-shadow: 
                inset 0 0 0 1px rgba(150,150,150,0.1),
                inset 0 0 0 3px rgba(50,50,50,0.3),
                0 4px 16px rgba(0,0,0,0.4);
        }
        .pokemon-card-stats-footer { 
            width: 110px !important;
            background: rgba(20,20,20,0.3);
            border-radius: 4px;
            padding: 4px;
            border: 1px solid rgba(100,100,100,0.1);
        }
        .pokemon-card { 
            overflow: visible !important;
            background: rgba(40,40,40,0.2) !important;
            border-radius: 8px;
            padding: 6px !important;
            margin: 4px 0;
            border: 1px solid rgba(100,100,100,0.1);
            box-shadow: 
                inset 0 1px 0 rgba(255,255,255,0.05),
                0 2px 8px rgba(0,0,0,0.3);
        }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-21d) sprite container flex ------------------- */
        .pokemon-sprite-container {
            display: flex !important;
            flex-direction: column;
            align-items: center;
            height: auto !important;
        }
        .pokemon-sprite-container .hp-bar { width: 100% !important; margin-top:4px; }
        .pokemon-sprite-container .hp-text { font-size: 14px; margin-top:2px; }
        .pokemon-status { font-size: 14px; }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-22) fades & exp bar ------------------------- */
        .team-display-area { position: relative !important; }
        .team-display-area::before,
        .team-display-area::after {
            content: "";
            position: absolute;
            left: 0;
            width: 100%;
            height: 40px;
            pointer-events: none;
            background: linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.85) 100%);
            z-index: 0; /* behind content */
        }
        .team-display-area::before { top: 0; transform: scaleY(-1); }
        .team-display-area::after  { bottom: 0; }

        /* shorten EXP bar */
        .exp-bar { width: 50% !important; margin: 0 auto 4px; }

        /* shift info section towards sprite (right side) */
        .pokemon-card-info-wrapper { margin-left: auto; margin-right: 8px; width: 100%; text-align: right; }

        /* Fade grokThinking top/bottom */
        #grokThinking { position: fixed; max-height: calc(100vh - 40px); overflow: hidden; }
        #grokThinking::before, #grokThinking::after {
            content:""; position:absolute; left:0; width:100%; height:120px; pointer-events:none;
            background: linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.6) 100%);
        }
        #grokThinking::before { top:0; transform: scaleY(-1); }
        #grokThinking::after  { bottom:0; }
        /* --------------------------------------------------------------------- */
        /* --- UI PATCH (2025-06-22b) ensure team visible -------------------- */
        .team-display-area { overflow: visible !important; z-index: 3200 !important; }
        /* --------------------------------------------------------------------- */
        /* --- TEAM DISPLAY OVERRIDE - ELECTRONIC TERMINAL STYLE --- */
        .team-display-area {
            position: fixed !important;
            top: 80px !important;
            right: 20px !important;
            width: 200px !important;
            max-height: calc(100vh - 200px) !important;
            overflow-y: auto !important;
            background: rgba(0, 0, 0, 0.85) !important;
            backdrop-filter: blur(10px) !important;
            border: 2px solid #7877C6 !important;
            border-radius: 8px !important;
            padding: 10px !important;
            z-index: 3200 !important;
            box-shadow: 
                0 0 20px rgba(120, 119, 198, 0.3),
                inset 0 0 20px rgba(0, 0, 0, 0.5) !important;
            border-image: linear-gradient(90deg, #7877C6, #FF77C6, #78C7FF) 1 !important;
        }
        /* --------------------------------------------------------------------- */

        /* --- UI PATCH (2025-06-23c) walk-pause-flip cycle ------------------- */
        /* Long walk sequence, pause, quick flip (<0.5s), walk back, pause, flip */
        /* Small travel distance for compact header cards */

        @keyframes walkCycleA {
            /* Start left, facing left - walk right */
            0%   { transform: translateX(-15px) scaleX(-1) scale(1); }
            /* Walk to right position */
            35%  { transform: translateX( 15px) scaleX(-1) scale(1.03); }
            /* Pause at right */
            40%  { transform: translateX( 15px) scaleX(-1) scale(1.03); }
            /* Quick flip to face left (3% = ~0.3s of 10s cycle) */
            43%  { transform: translateX( 15px) scaleX(1) scale(1.03); }
            /* Walk left */
            78%  { transform: translateX(-15px) scaleX(1) scale(1); }
            /* Pause at left */
            83%  { transform: translateX(-15px) scaleX(1) scale(1); }
            /* Quick flip to face right */
            86%  { transform: translateX(-15px) scaleX(-1) scale(1); }
            /* Hold position until cycle restart */
            100% { transform: translateX(-15px) scaleX(-1) scale(1); }
        }

        @keyframes walkCycleB {
            /* Start right, facing right - walk left */
            0%   { transform: translateX( 15px) scaleX(1) scale(1); }
            /* Walk to left position */
            35%  { transform: translateX(-15px) scaleX(1) scale(1.02); }
            /* Pause at left */
            40%  { transform: translateX(-15px) scaleX(1) scale(1.02); }
            /* Quick flip to face right */
            43%  { transform: translateX(-15px) scaleX(-1) scale(1.02); }
            /* Walk right */
            78%  { transform: translateX( 15px) scaleX(-1) scale(1); }
            /* Pause at right */
            83%  { transform: translateX( 15px) scaleX(-1) scale(1); }
            /* Quick flip to face left */
            86%  { transform: translateX( 15px) scaleX(1) scale(1); }
            /* Hold position until cycle restart */
            100% { transform: translateX( 15px) scaleX(1) scale(1); }
        }

        /* Different period lengths per slot for variety */
        .header-team-display .pokemon-card:nth-child(1) .pokemon-sprite { animation: walkCycleA 10s linear infinite; }
        .header-team-display .pokemon-card:nth-child(2) .pokemon-sprite { animation: walkCycleB 9.5s linear infinite; }
        .header-team-display .pokemon-card:nth-child(3) .pokemon-sprite { animation: walkCycleA 11s linear infinite; }
        .header-team-display .pokemon-card:nth-child(4) .pokemon-sprite { animation: walkCycleB 10.5s linear infinite; }
        .header-team-display .pokemon-card:nth-child(5) .pokemon-sprite { animation: walkCycleA 9s linear infinite; }
        .header-team-display .pokemon-card:nth-child(6) .pokemon-sprite { animation: walkCycleB 11.5s linear infinite; }
        /* ------------------------------------------------------------------- */
    </style>
    <script>
        const CONFIG = {{ CONFIG | tojson }};
    </script>
</head>

<body>
    <!-- Full-size global map background, centered on player -->
    <div class="global-map-background">
        <img id="globalMapBackground" src="/global-map.png" alt="Global Map">
    </div>

    <!-- TV Container -->
    <div class="tv-container">
        <div class="tv-bezel">
            <!-- Ambient light effects -->
            <div class="ambient-light left"></div>
            <div class="ambient-light right"></div>
            
                        <!-- Main screen -->
            <div class="screen-wrapper">
                <div class="game-screen">
                    <!-- Game screen moved to body level -->
                    <!-- Fallback content when no game screen is available -->
                    <div id="gamePlaceholder" style="text-align: center; opacity: 0.8;">
                        <div style="font-size: 14px; text-transform: uppercase; letter-spacing: 4px; margin-bottom: 10px;">
                            Stream Preview
                        </div>
                        <div style="font-size: 18px; opacity: 0.6;">
                            [ Waiting for game content... ]
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Premium details -->
            <div class="led-strip"></div>
            <div class="status-orb"></div>
            <div class="speaker-grille left"></div>
            <div class="speaker-grille right"></div>
            <div class="touch-zone"></div>
        </div>
        
                    <!-- Brand -->
        <div class="brand">GROK Plays Pokémon</div>
            </div>

    <!-- Centered game screen - ADJUST THESE VALUES TO MOVE IT -->
    <div id="gameScreen" style="
        position: fixed !important;
        top: calc(50% + 280px) !important;    /* Move DOWN: increase +20px to move further down */
        left: calc(50% + 280px) !important;   /* Move RIGHT: increase +30px to move further right */
        transform: translate(-50%, -50%) !important;
        width: 480px !important;
        height: 432px !important;
        background: #000000 !important;
        border: 2px solid rgba(255, 255, 255, 0.2) !important;
        color: #ff0000 !important;
        font-size: 24px !important;
        font-weight: bold !important;
        text-align: center !important;
        line-height: 432px !important;
        z-index: 9999 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        border-radius: 4px !important;
        image-rendering: pixelated !important;
    ">
        🎮 GAME SCREEN HERE 🎮
    </div>

        <!-- Hidden Elements (keeping for JS compatibility) -->
        <div class="left-sidebar" style="display: none;">
            <div id="grokThinking" class="grok-message grok-thinking" style="display: none;"></div>
            <div id="grokResponse" class="grok-message grok-response" style="display: none;"></div>
            <div style="color: #00ff41; font-size: 16px;" id="grokWaiting">Waiting for Grok to think...</div>
        </div>

        <div class="center-content" style="display: none;">
            <div class="global-map-container">
                <div class="map-info">
                    <span>Position: <span id="mapPosition">(0, 0)</span></span>
                    <span>Map: <span id="currentMapName">Unknown</span></span>
                </div>
            </div>
            <div class="quest-section" id="questSection" style="display: none;">
                <h2 class="section-title" id="questTitle">Current Quest</h2>
                <div class="quest-description" id="questDescription"></div>
                <ul class="quest-list" id="questTriggers"></ul>
                <div class="quest-progress-bar">
                    <div class="quest-progress-fill" id="questProgress" style="width: 0%"></div>
                </div>
            </div>
        </div>

        <!-- Team Display Panel - Right Bezel Area -->
        <div class="team-display-area">
        </div>

        <!-- Floating Pokemon Team Cards -->
        <div class="team-grid" id="pokemon-team">
            <!-- Debug: Test content to verify positioning -->
            <div style="background: #ff0000; color: #fff; padding: 10px; border-radius: 5px;">TEST CARD 1</div>
            <div style="background: #00ff00; color: #000; padding: 10px; border-radius: 5px;">TEST CARD 2</div>
        </div>

        <div class="right-sidebar" style="display: none;">
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
        
    <!-- Complete Bottom Stats Bar (hidden but keeping for JS) -->
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

    <!-- Hidden Elements for compatibility -->
    <div class="header-team-display" id="header-pokemon-team" style="display: none;"></div>
    <div class="title-overlay" style="display: none;"></div>

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
            actionHistory: [],
            // Track last rendered team JSON to avoid needless re-renders that reset CSS animations
            lastTeamHash: ''
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
            const uptimeEl = document.getElementById('uptime');
            if (uptimeEl) {
                const elapsed = Date.now() - gameState.startTime;
                const hours = Math.floor(elapsed / 3600000);
                const minutes = Math.floor((elapsed % 3600000) / 60000);
                const seconds = Math.floor((elapsed % 60000) / 1000);
                uptimeEl.textContent =
                    `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }
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
            if (!actionLog) return; // Skip if element doesn't exist
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
            // Use standing-facing frames so the sprite appears centred and
            // static when the player is not moving.  See ui/render_to_global
            // for the authoritative frame indices.
            const spriteData = { 'Down': 1, 'Up': 4, 'Left': 6, 'Right': 8 };
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

        // Update player position and center map
        function updatePlayerPosition(localX, localY, mapId, facing = 'Down') {
            if (!gameState.mapData) return;
            // map_data.json stores coordinates as [X, Y] (col, row) – see render_to_global.py
            const [map_x, map_y] = gameState.mapData[mapId].coordinates;
            const globalX = localX + map_x + PAD;
            const globalY = localY + map_y + PAD;
            
            // Use the same centering logic as updatePlayerGlobal
            updatePlayerGlobal(globalX, globalY, facing);
        }

        // Center global map on player position
        function updatePlayerGlobal(globalX, globalY, facing='Down') {
            // Calculate pixel position on the map
            const pixelX = (globalX - PAD) * TILE_SIZE;
            const pixelY = (globalY - PAD) * TILE_SIZE;

            // Update background map position to center on player
            const bgImg = document.getElementById('globalMapBackground');
            if (bgImg) {
                // Scale factor to match game screen
                const scale = 3;
                // Center the map using full viewport dimensions
                const centerX = window.innerWidth / 2;
                const centerY = window.innerHeight / 2;
                const offsetX = centerX - (pixelX * scale);
                const offsetY = centerY - (pixelY * scale);
                bgImg.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`;
                console.log(`Map centered on player: (${globalX}, ${globalY}) -> pixel (${pixelX}, ${pixelY}) -> offset (${offsetX}, ${offsetY})`);
            }
        }

        // Initial setup
        document.addEventListener('DOMContentLoaded', () => {
            // DEBUG: Check if game screen exists
            console.log('=== GAME SCREEN DEBUG ===');
            let debugGameScreen = document.getElementById('gameScreen');
            console.log('Game screen found:', !!debugGameScreen);
            if (debugGameScreen) {
                console.log('Game screen element:', debugGameScreen);
                console.log('Game screen styles:', window.getComputedStyle(debugGameScreen));
                console.log('Game screen position:', debugGameScreen.getBoundingClientRect());
            }
            console.log('=== END DEBUG ===');
            const teamContainer = document.getElementById('pokemon-team');
            teamContainer.innerHTML = '';
            for (let i = 0; i < 6; i++) {
                teamContainer.appendChild(createEmptySlot());
            }
            
            // Make sure game screen is visible - FORCE ALL STYLES
            const gameScreen = document.getElementById('gameScreen');
            if (gameScreen) {
                console.log('Game screen element found:', gameScreen);
                console.log('Current src:', gameScreen.src);
                console.log('Current computed style:', window.getComputedStyle(gameScreen));
                
                // Force all styles using setProperty with important flag
                gameScreen.style.setProperty('display', 'block', 'important');
                gameScreen.style.setProperty('visibility', 'visible', 'important');
                gameScreen.style.setProperty('opacity', '1', 'important');
                gameScreen.style.setProperty('position', 'fixed', 'important');
                gameScreen.style.setProperty('top', 'calc(50% + 20px)', 'important');      // MOVE DOWN: change
                gameScreen.style.setProperty('left', 'calc(50% + 50px)', 'important');     // MOVE RIGHT: change
                gameScreen.style.setProperty('transform', 'translate(-50%, -50%)', 'important');
                gameScreen.style.setProperty('width', '480px', 'important');
                gameScreen.style.setProperty('height', '432px', 'important');
                gameScreen.style.setProperty('z-index', '9999', 'important');
                gameScreen.style.setProperty('background', '#000000', 'important');
                gameScreen.style.setProperty('border', '2px solid rgba(255, 255, 255, 0.1)', 'important');
                gameScreen.style.setProperty('image-rendering', 'pixelated', 'important');
                
                console.log('Game screen forced visible!');
                console.log('New computed style:', window.getComputedStyle(gameScreen));
                console.log('New position:', gameScreen.getBoundingClientRect());
            } else {
                console.error('Game screen element not found!');
            }
            
            loadQuestDefinitions();
            loadPlayerSprites();
            loadMapData();
            loadQuestPaths();
        });

        // Show speech bubble
        function showSpeechBubble(text, type = 'quest_start', duration = 4000) {
            const overlay = document.getElementById('speechBubbleOverlay');
            if (!overlay) {
                console.log('Speech bubble overlay not found, skipping');
                return;
            }
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

            // Auto-scroll quest container smoothly like Grok status
            const questContainer = document.querySelector('.quest-webui-container');
            if (questContainer) startAutoScroll(questContainer);
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
                startAutoScroll(thinkingEl);
            } else thinkingEl.style.display = 'none';
            if (response) {
                responseEl.textContent = response;
                responseEl.style.display = 'block';
                waitingEl.style.display = 'none';
                startAutoScroll(responseEl);
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

        // Pokemon species name mapping for PokeAPI compatibility
        function getPokemonAPIName(speciesName) {
            if (!speciesName) return '';
            // Normalize: lower-case, convert underscores to spaces, collapse whitespace
            const norm = speciesName.toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
            const apiNameMap = {
                'nidoran♂': 'nidoran-m',
                'nidoran♂': 'nidoran-m', // alt encoding safety
                'nidoran♀': 'nidoran-f',
                'nidoran m': 'nidoran-m',
                'nidoran f': 'nidoran-f',
                'nidoran_m': 'nidoran-m',
                'nidoran_f': 'nidoran-f',
                'mr. mime': 'mr-mime',
                'mr mime': 'mr-mime',
                "farfetch'd": 'farfetchd',
                'farfetchd': 'farfetchd'
            };
            if (apiNameMap[norm]) return apiNameMap[norm];
            // Replace spaces with dashes for generic fallback (e.g., 'ho oh' -> 'ho-oh')
            return norm.replace(/\s+/g, '-');
        }

        // Update Pokemon team
        async function updatePokemonTeam(partyData) {
            console.log('=== POKEMON TEAM UPDATE ===');
            console.log('Team data received:', partyData);
            const teamContainer = document.querySelector('.team-grid');
            console.log('Team container found:', !!teamContainer);
            if (teamContainer) {
                console.log('Team container element:', teamContainer);
                console.log('Team container computed style:', window.getComputedStyle(teamContainer));
            }
            if (!teamContainer) {
                console.error('Team container not found');
                return;
            }
            teamContainer.innerHTML = '';
            console.log('Processing', partyData.length, 'Pokemon...');
            for (const p of partyData) {
                try {
                    const apiName = getPokemonAPIName(p.speciesName);
                    let spriteUrl = localStorage.getItem(`pokemon_sprite_${apiName}`);
                    if (!spriteUrl) {
                        const resp = await fetch(`https://pokeapi.co/api/v2/pokemon/${apiName}`);
                        if (resp.ok) {
                            const d = await resp.json();
                            spriteUrl = d.sprites.front_default;
                            localStorage.setItem(`pokemon_sprite_${apiName}`, spriteUrl);
                        }
                    }
                    teamContainer.appendChild(createPokemonCardFromGameData(p, spriteUrl));
                } catch {
                    teamContainer.appendChild(createEmptySlot(`Error: ${p.nickname||'Unknown'}`));
                }
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
                    <div class="pokemon-name-level-row">
                        <div class="pokemon-name-above">${speciesName}</div>
                        <div class="pokemon-level-inline">Lv. ${pokemon.level}</div>
                    </div>
                    <div class="pokemon-nickname-below">${nickname}</div>
                    <img src="${spriteUrl||'https://placehold.co/64x64/333/666?text=No+Sprite'}" 
                        alt="${speciesName}" class="pokemon-sprite"
                        onerror="this.src='https://placehold.co/64x64/333/666?text=Error';this.onerror=null;">
                    <div class="hp-bar"><div class="hp-fill ${hpClass}" style="width:${hpPct}%"></div></div>
                    <div class="hp-and-badges-row">
                        <div class="hp-text mono">${pokemon.hp}/${pokemon.maxHp}</div>
                        <div class="type-status-badges">
                            <div class="pokemon-types">${typesHtml}</div>
                            <div class="pokemon-status${statusClass}">${status}</div>
                        </div>
                    </div>
                </div>
                <div class="pokemon-card-info-wrapper">
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
                    const statLocation = document.getElementById('statLocation');
                    const currentMapName = document.getElementById('currentMapName');
                    const statMapId = document.getElementById('statMapId');
                    const statLocal = document.getElementById('statLocal');
                    const statGlobal = document.getElementById('statGlobal');
                    const mapPosition = document.getElementById('mapPosition');
                    
                    if (statLocation) statLocation.textContent = msg.data.map_name || 'Unknown';
                    if (currentMapName) currentMapName.textContent = msg.data.map_name || 'Unknown';
                    if (statMapId) statMapId.textContent = msg.data.map_id || '0';
                    if (statLocal) statLocal.textContent = `(${msg.data.y||0},${msg.data.x||0})`;
                    if (statGlobal) statGlobal.textContent = `(${msg.data.gy||0},${msg.data.gx||0})`;
                    
                    if (msg.data.gx!==undefined && msg.data.gy!==undefined) {
                        // Use precomputed global coordinates when available for highest accuracy
                        updatePlayerGlobal(msg.data.gx, msg.data.gy, gameState.facing);
                    } else if (msg.data.x!==undefined && msg.data.y!==undefined) {
                        updatePlayerPosition(msg.data.x, msg.data.y, msg.data.map_id, gameState.facing);
                        if (mapPosition) mapPosition.textContent = `(${msg.data.gy},${msg.data.gx})`;
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
                    const statMoney = document.getElementById('statMoney');
                    const statSteps = document.getElementById('statSteps');
                    const statBadges = document.getElementById('statBadges');
                    const statSeen = document.getElementById('statSeen');
                    const statCaught = document.getElementById('statCaught');
                    
                    if (statMoney) statMoney.textContent = `₽${msg.data.money||0}`;
                    if (statSteps) statSteps.textContent = msg.data.steps||0;
                    if (statBadges) statBadges.textContent = `${msg.data.badges||0}/8`;
                    if (statSeen) statSeen.textContent = msg.data.pokedex_seen||0;
                    if (statCaught) statCaught.textContent = msg.data.pokedex_caught||0;
                    break;

                case 'pokemon_team':
                    {
                        const newHash = JSON.stringify(msg.data);
                        if (gameState.lastTeamHash !== newHash) {
                            gameState.lastTeamHash = newHash;
                            await updatePokemonTeam(msg.data);
                        }
                    }
                    break;

                case 'game_screen':
                    const gs = document.getElementById('gameScreen');
                    const ph = document.getElementById('gamePlaceholder');
                    console.log('GAME SCREEN EVENT RECEIVED!');
                    console.log('Game screen element:', gs);
                    console.log('Data received:', msg.data ? 'YES' : 'NO');
                    if (gs && msg.data) {
                        console.log('Setting game screen content...');
                        // Convert div to img and set src
                        gs.innerHTML = `<img src="${msg.data}" style="width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated;" alt="Game Screen">`;
                        // Keep the div styling - CHANGE THESE VALUES TO MOVE THE GAME SCREEN
                        gs.style.setProperty('display', 'flex', 'important');
                        gs.style.setProperty('visibility', 'visible', 'important');
                        gs.style.setProperty('opacity', '1', 'important');
                        gs.style.setProperty('position', 'fixed', 'important');
                        gs.style.setProperty('top', 'calc(50% + 20px)', 'important');      // MOVE DOWN: change
                        gs.style.setProperty('left', 'calc(50% + 50px)', 'important');     // MOVE RIGHT: change
                        gs.style.setProperty('transform', 'translate(-50%, -50%)', 'important');
                        gs.style.setProperty('width', '480px', 'important');
                        gs.style.setProperty('height', '432px', 'important');
                        gs.style.setProperty('z-index', '9999', 'important');
                        console.log('Game screen updated with data length:', msg.data.length);
                        console.log('Game screen computed style:', window.getComputedStyle(gs));
                        if (ph) ph.style.display = 'none';
                    } else {
                        console.error('Game screen element or data missing!', {element: !!gs, data: !!msg.data});
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
                        const apiCallsEl = document.getElementById('apiCallsCount');
                        const totalTokensEl = document.getElementById('totalTokens');
                        const callCostEl = document.getElementById('callCost');
                        const totalCostEl = document.getElementById('totalCost');
                        const statCallCostEl = document.getElementById('statCallCost');
                        const statLifetimeCostEl = document.getElementById('statLifetimeCost');
                        
                        if (apiCallsEl) apiCallsEl.textContent = msg.data.api_calls_count||0;
                        if (totalTokensEl) totalTokensEl.textContent = msg.data.total_tokens?.toLocaleString()||0;
                        if (callCostEl) callCostEl.textContent = msg.data.call_cost?`$${msg.data.call_cost.toFixed(4)}`:'$0.00';
                        if (totalCostEl) totalCostEl.textContent = msg.data.total_cost?`$${msg.data.total_cost.toFixed(4)}`:'$0.00';
                        if (costEl) costEl.style.display = 'block';
                        if (statCallCostEl) statCallCostEl.textContent = msg.data.call_cost?`$${msg.data.call_cost.toFixed(4)}`:'$0.00';
                        if (statLifetimeCostEl) statLifetimeCostEl.textContent = msg.data.total_cost?`$${msg.data.total_cost.toFixed(2)}`:'$0.00';
                    } else if (costEl) {
                        costEl.style.display = 'none';
                    }
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

        // ------------------------------------------------------------------
        // Smooth auto-scroll helper – scrolls element downward 1 px every 30 ms
        // until bottom is reached, then stops (avoids distracting bounce).
        // ------------------------------------------------------------------
        function startAutoScroll(el) {
            if (!el) return;
            // Cancel previous timer if still running
            if (el._scrollTimer) clearInterval(el._scrollTimer);
            // Only reset to top if we're already very close to the top (fresh content)
            if (el.scrollTop < 4) el.scrollTop = 0;
            if (el.scrollHeight <= el.clientHeight + 4) return; // nothing to scroll

            const tick = () => {
                // Increment position
                el.scrollTop += 1;
                // Reached bottom?
                if (el.scrollTop + el.clientHeight >= el.scrollHeight) {
                    clearInterval(el._scrollTimer);
                    el._scrollTimer = null;
                    // Pause, then reset and restart
                    el._scrollPause = setTimeout(() => {
                        el.scrollTop = 0;
                        clearTimeout(el._scrollPause);
                        el._scrollPause = null;
                        startAutoScroll(el); // recurse to restart
                    }, 2000); // 2-second pause
                }
            };

            el._scrollTimer = setInterval(tick, 30);
        }

        // ==============================================================
        // PREMIUM BEZEL EFFECTS - from Opus design
        // ==============================================================
        
        // Create floating particles
        function createParticle() {
            const particle = document.createElement('div');
            particle.className = 'particle';
            particle.style.left = Math.random() * 100 + 'vw';
            particle.style.animationDelay = Math.random() * 10 + 's';
            particle.style.animationDuration = (8 + Math.random() * 4) + 's';
            
            // Random particle appearance
            const types = ['✦', '✧', '✨', '◆', '●'];
            particle.textContent = types[Math.floor(Math.random() * types.length)];
            particle.style.color = `hsl(${Math.random() * 60 + 220}, 100%, 70%)`;
            particle.style.fontSize = (10 + Math.random() * 10) + 'px';
            particle.style.textShadow = '0 0 10px currentColor';
            
            document.body.appendChild(particle);
            
            // Remove particle after animation
            setTimeout(() => particle.remove(), 10000);
        }
        
        // Generate particles periodically
        setInterval(createParticle, 2000);
        
        // Initial particles
        for (let i = 0; i < 5; i++) {
            setTimeout(createParticle, i * 400);
        }
        
        // Interactive touch zone
        const touchZone = document.querySelector('.touch-zone');
        if (touchZone) {
            touchZone.addEventListener('click', function() {
                const orb = document.querySelector('.status-orb');
                if (orb) {
                    orb.style.animation = 'none';
                    setTimeout(() => {
                        orb.style.animation = 'orb-pulse 2s ease-in-out infinite';
                    }, 10);
                }
                
                // Create ripple effect
                const ripple = document.createElement('div');
                ripple.style.cssText = `
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    width: 0;
                    height: 0;
                    border-radius: 50%;
                    border: 2px solid rgba(120, 219, 255, 0.6);
                    animation: ripple 1s ease-out;
                    pointer-events: none;
                `;
                this.appendChild(ripple);
                setTimeout(() => ripple.remove(), 1000);
            });
        }
        
        // Add ripple animation
        const rippleStyle = document.createElement('style');
        rippleStyle.textContent = `
            @keyframes ripple {
                to {
                    width: 200px;
                    height: 200px;
                    border-color: transparent;
                }
            }
        `;
        document.head.appendChild(rippleStyle);
        
        // 3D tilt effect on mouse move for TV container
        const tvContainer = document.querySelector('.tv-container');
        let currentX = 0;
        let currentY = 0;
        let targetX = 0;
        let targetY = 0;
        
        if (tvContainer) {
            document.addEventListener('mousemove', (e) => {
                const rect = tvContainer.getBoundingClientRect();
                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;
                
                targetX = (e.clientX - centerX) / (rect.width / 2) * 2;
                targetY = (e.clientY - centerY) / (rect.height / 2) * 2;
            });
            
            function animate3DTV() {
                currentX += (targetX - currentX) * 0.05;
                currentY += (targetY - currentY) * 0.05;
                
                tvContainer.style.transform = `
                    perspective(2000px)
                    rotateY(${currentX}deg)
                    rotateX(${-currentY}deg)
                    translateY(${Math.sin(Date.now() * 0.001) * 10}px)
                `;
                
                requestAnimationFrame(animate3DTV);
            }
            
            animate3DTV();
        }

        // Premium game screen handling
        const gameScreen = document.getElementById('gameScreen');
        const gamePlaceholder = document.getElementById('gamePlaceholder');
        
        // Show game screen when data is available, hide placeholder
        function showGameScreen() {
            if (gameScreen && gameScreen.src && !gameScreen.src.includes('data:image/svg+xml')) {
                gameScreen.style.display = 'block';
                if (gamePlaceholder) gamePlaceholder.style.display = 'none';
            } else {
                gameScreen.style.display = 'none';
                if (gamePlaceholder) gamePlaceholder.style.display = 'block';
            }
        }
        
        // Monitor for game screen updates
        if (gameScreen) {
            gameScreen.addEventListener('load', showGameScreen);
            // Check initially
            showGameScreen();
        }
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