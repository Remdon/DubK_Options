"""
Scan result caching component for storing and retrieving market scan results.
Enables faster testing and avoids redundant API calls.
"""
import json
import os
from typing import Optional, Dict
import datetime

class ScanResultCache:
    """Manages scan result caching to disk"""

    def __init__(self, cache_file='scan_results.json'):
        self.cache_file = cache_file

    def save_scan(self, opportunities: list, scan_type: str):
        """Save scan results to disk"""
        try:
            # Ensure logs directory exists
            os.makedirs('logs', exist_ok=True)
            cache_path = f'logs/{self.cache_file}'

            cache_data = {
                'timestamp': datetime.datetime.now().isoformat(),
                'scan_type': scan_type,
                'count': len(opportunities),
                'opportunities': [
                    {
                        'symbol': opp['symbol'],
                        'confidence': opp.get('grok_confidence', 0) if 'grok_confidence' in opp else opp.get('confidence', 0),
                        'strategy': opp.get('strategy', 'UNKNOWN'),
                        'strikes': opp.get('strikes', ''),
                        'expiry': opp.get('expiry', '30DTE'),
                        'reason': opp.get('reason', ''),
                        'final_score': opp.get('final_score', 0)
                    }
                    for opp in opportunities
                ]
            }

            with open(cache_path, 'w') as f:
                json.dump(cache_data, f, indent=2)

            print(f"[CACHE] Scan results saved to {cache_path}")

        except Exception as e:
            print(f"[ERROR] Failed to save scan cache: {e}")

    def load_last_scan(self) -> Optional[Dict]:
        """Load last scan results from disk"""
        try:
            cache_path = f'logs/{self.cache_file}'
            if not os.path.exists(cache_path):
                return None

            with open(cache_path, 'r') as f:
                cache_data = json.load(f)

            # Check if cache is from today
            cached_time = datetime.datetime.fromisoformat(cache_data['timestamp'])
            age_hours = (datetime.datetime.now() - cached_time).total_seconds() / 3600

            if age_hours > 24:  # Expire after 24 hours
                return None

            return cache_data

        except Exception as e:
            print(f"[ERROR] Failed to load scan cache: {e}")
            return None
