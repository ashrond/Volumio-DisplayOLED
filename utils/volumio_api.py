import requests
import json

# Volumio API URL
VOLUMIO_API_URL = "http://localhost:3000/api/v1/getState"

def get_volumio_state():
    """
    Fetches the current playback state from Volumio.
    Uses minimal overhead to avoid performance issues.
    """
    try:
        response = requests.get(VOLUMIO_API_URL, timeout=0.2)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.exceptions.RequestException:
        return None

def send_volumio_command(command, params=None):
    """
    Sends a command to Volumio via HTTP API.
    Example: send_volumio_command('volume', {'volume': 50})
    """
    url = f"http://localhost:3000/api/v1/commands/{command}"
    try:
        data = json.dumps(params) if params else None
        response = requests.post(url, data=data, timeout=0.2)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False
