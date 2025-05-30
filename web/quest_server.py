from flask import Flask, send_file, Response
import queue
import json
from pathlib import Path
import threading

app = Flask(__name__)
app.template_folder = Path(__file__).parent / "templates"
app.static_folder = Path(__file__).parent / "static"

# Global queue for updates
status_queue = queue.Queue()

def format_event_data(item_id, data):
    """Format the event data for SSE"""
    if item_id == '__location__':
        gx, gy, map_id, map_name = data
        return json.dumps({
            'type': 'location',
            'data': {
                'gx': gx,
                'gy': gy,
                'map_id': map_id,
                'map_name': map_name
            }
        })
    elif item_id == '__current_quest__':
        return json.dumps({
            'type': 'current_quest',
            'data': str(data).zfill(3) if data is not None else 'N/A'
        })
    else:
        # Handle quest/trigger status updates
        return json.dumps({
            'type': 'status',
            'id': item_id,
            'data': data
        })

@app.route('/')
def index():
    """Serve the main index.html static page"""
    return send_file(Path(__file__).parent / 'templates' / 'index.html')

@app.route('/events')
def events():
    """SSE endpoint for live updates"""
    def event_stream():
        while True:
            try:
                # Get update from queue with timeout
                item_id, data = status_queue.get(timeout=1)
                event_data = format_event_data(item_id, data)
                print(f"SSE /events: {event_data}")
                yield f"data: {event_data}\n\n"
            except queue.Empty:
                # Send keepalive comment
                yield ": keepalive\n\n"
            except Exception as e:
                print(f"Error in event stream: {e}")
                continue

    return Response(event_stream(), mimetype='text/event-stream')

def start_server(host='0.0.0.0', port=3030):
    """Start the Flask server in a separate thread"""
    def run():
        try:
            app.run(host=host, port=port, debug=False, threaded=True)
        except OSError as e:
            print(f"Quest server failed to start on {host}:{port}: {e}")
    
    server_thread = threading.Thread(target=run, daemon=True)
    server_thread.start()
    print(f"Quest progress server started at http://{host}:{port}")
    return server_thread 

# Add standalone run support
if __name__ == '__main__':
    # Run the quest progress server standalone
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True) 