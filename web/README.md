# Grok Plays Pokemon - Web Interface

A modern web interface for monitoring and controlling the Grok AI agent playing Pokemon Red.

## Features

- **Real-time Game Monitoring**: Live view of game screen, Pokemon team, stats, and location
- **Quest Progress Tracking**: Visual quest progression with completion status
- **Agent Controls**: Start, pause, and stop the AI agent via web interface
- **Live Statistics**: Money, badges, Pokedex progress, and step counter
- **Pokemon Team Display**: Real-time party information with HP, levels, and types
- **Modern UI**: Responsive design with dark theme optimized for streaming

## Quick Start

1. **Install Dependencies**:
   ```bash
   pip install fastapi uvicorn requests numpy pillow
   ```

2. **Start the Web Interface**:
   ```bash
   python run_web_interface.py
   ```

3. **Open Your Browser**:
   Navigate to `http://localhost:8000`

4. **Start the Game**:
   Click the "Start" button in the web interface to begin the AI agent

## Architecture

### Data Flow
```
Environment (play.py) → Web Integration → FastAPI Server → Browser (SSE)
```

### Components

- **FastAPI Server** (`app.py`): Main web server with REST API and SSE endpoints
- **Web Integration** (`web_integration.py`): Bridges environment data to web interface
- **Frontend** (`templates/index.html`): Modern responsive UI with real-time updates
- **Environment Integration**: Modified `play.py` sends data to web interface

### Real-time Updates

The web interface receives real-time updates via Server-Sent Events (SSE) for:

- Game location and coordinates
- Pokemon team status and HP
- Quest progression and current objectives
- Game statistics (money, badges, Pokedex)
- Live game screen capture
- Agent status and actions

## Configuration

### Web Server Options

```bash
python run_web_interface.py --help
```

Options:
- `--host`: Host to bind to (default: localhost)
- `--port`: Port to bind to (default: 8000)
- `--auto-start-game`: Automatically start game after web server
- `--config`: Path to config file

### Environment Configuration

The web interface automatically connects to the game environment when `play.py` is running. No additional configuration needed.

## API Endpoints

### Control Endpoints
- `POST /start` - Start the game with AI agent
- `POST /pause` - Pause/resume the agent
- `POST /stop` - Stop the game and agent
- `GET /status` - Get current game and agent status

### Data Endpoints
- `GET /events` - Server-Sent Events stream for real-time updates
- `GET /game-state` - Current complete game state
- `GET /screenshot` - Current game screenshot
- `GET /required_completions.json` - Quest definitions

### Utility Endpoints
- `POST /upload-save-state` - Upload save state files
- `POST /update-game-state` - Internal endpoint for environment data

## Development

### Adding New Data Sources

1. **Environment Side** (in `play.py`):
   ```python
   # Send data via status_queue
   status_queue.put(('__new_data_type__', data))
   
   # Or use web integration directly
   web_integration.send_update('__new_data_type__', data)
   ```

2. **Web Server Side** (in `app.py`):
   ```python
   # Add handler in update_game_state function
   elif item_id == '__new_data_type__':
       app.state.game_state['new_field'] = data
   ```

3. **Frontend Side** (in `index.html`):
   ```javascript
   // Add handler in SSE message processing
   case 'new_data_type':
       updateNewDataDisplay(msg.data);
       break;
   ```

### Custom UI Components

The frontend uses vanilla JavaScript and CSS Grid for layout. To add new UI components:

1. Add HTML structure to `index.html`
2. Add CSS styling (dark theme consistent)
3. Add JavaScript handlers for real-time updates
4. Connect to SSE data stream

## Troubleshooting

### Web Server Won't Start
- Check if port 8000 is already in use
- Verify all dependencies are installed
- Check file permissions

### No Game Data
- Ensure `play.py` is running with web integration enabled
- Check web server logs for connection errors
- Verify environment is sending data to status_queue

### Browser Not Updating
- Check browser console for JavaScript errors
- Verify SSE connection is established
- Check network tab for failed requests

### Performance Issues
- Reduce update frequency in web integration
- Check for memory leaks in long-running sessions
- Monitor CPU usage of web server

## Security Considerations

- The web interface runs on localhost by default
- No authentication is implemented (designed for local use)
- File upload is restricted to save states directory
- Game control is limited to start/stop operations

For production deployment, consider:
- Adding authentication
- Implementing HTTPS
- Restricting file operations
- Rate limiting API calls 