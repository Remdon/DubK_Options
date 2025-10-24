"""
Alert management component for sending notifications and monitoring critical events.
Handles email, webhook, and alert throttling to prevent spam.
"""
import time
from typing import Optional

class AlertManager:
    """Handles alerts for critical events"""

    def __init__(self, email: Optional[str] = None, webhook: Optional[str] = None):
        self.email = email
        self.webhook = webhook
        self.last_alert_time = {}
        self.alert_cooldown = 300  # 5 minutes between duplicate alerts

    def send_alert(self, level: str, message: str, throttle_key: str = None):
        """Send alert via configured channels"""
        # Throttle duplicate alerts
        if throttle_key:
            last_time = self.last_alert_time.get(throttle_key, 0)
            if time.time() - last_time < self.alert_cooldown:
                return
            self.last_alert_time[throttle_key] = time.time()

        # Log to console/file (handled by main logging system)

        # Send webhook alert
        if self.webhook:
            try:
                import requests
                payload = {
                    'text': f"🤖 *{level}*: {message}",
                    'username': 'Options Trading Bot'
                }
                requests.post(self.webhook, json=payload, timeout=5)
            except Exception as e:
                print(f"[DEBUG] Failed to send webhook alert: {e}")

        # Email alerts could be added here for production use
        # if self.email:
        #     send_email(self.email, f"{level}: {message}")

        print(f"[ALERT {level}] {message}")
