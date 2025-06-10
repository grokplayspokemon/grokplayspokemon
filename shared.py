import threading

# This event will be used to signal the start of the game from the web UI
game_started = threading.Event() 
# Grok control
grok_enabled = threading.Event() 