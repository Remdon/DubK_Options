"""
OpenBB API Server - Server Management and Health Checking

Manages the OpenBB REST API server:
- Server status checking with retries
- Server health validation
"""

import time
import requests


class OpenBBAPIServer:
    """Manages the OpenBB REST API server"""

    def __init__(self, host='127.0.0.1', port=6900):
        self.host = host
        self.port = port
        self.process = None
        self.running = False

    def is_running(self, retries=3, wait=2):
        """Check if API server is responding with retries"""
        for attempt in range(retries):
            try:
                response = requests.get(f'http://{self.host}:{self.port}/', timeout=2)
                if response.status_code in [200, 404]:
                    return True
            except:
                if attempt < retries - 1:
                    time.sleep(wait)
        return False
