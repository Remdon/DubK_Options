"""
Market calendar component for handling trading hours, holidays, and smart scheduling.
Determines when scans should run and when market is open/closed.
"""
import datetime
from typing import Tuple, Optional
import pytz

class MarketCalendar:
    """US Stock Market Calendar with Holiday Awareness"""

    def __init__(self):
        self.eastern = pytz.timezone('US/Eastern')
        self.holidays = [
            '2025-01-01', '2025-01-20', '2025-02-17', '2025-04-18',
            '2025-05-26', '2025-06-19', '2025-07-04', '2025-09-01',
            '2025-11-27', '2025-12-25',
        ]
        self.last_midnight_scan = None
        self.last_premarket_scan = None

    def should_run_scan(self) -> Tuple[bool, str]:
        """
        Determine if a scan should run based on smart scheduling:
        - Once at midnight (12:00 AM ET)
        - Once 2 hours before market open (7:00 AM ET for 9:30 open)
        - NOT every hour when market is closed
        """
        now_et = datetime.datetime.now(self.eastern)
        current_time = now_et.time()
        today_str = now_et.strftime('%Y-%m-%d')

        # Check if midnight scan is due (12:00 AM - 12:30 AM window)
        midnight_window = datetime.time(0, 0) <= current_time < datetime.time(0, 30)
        if midnight_window:
            if self.last_midnight_scan != today_str:
                return True, "MIDNIGHT_SCAN"

        # Check if pre-market scan is due (7:00 AM - 7:25 AM window)
        premarket_window = datetime.time(7, 0) <= current_time < datetime.time(7, 25)
        if premarket_window and now_et.weekday() < 5:  # Weekday only
            if today_str not in self.holidays:
                if self.last_premarket_scan != today_str:
                    return True, "PREMARKET_SCAN"

        return False, "NO_SCAN_NEEDED"

    def mark_scan_completed(self, scan_type: str):
        """Mark that a scan has been completed"""
        now_et = datetime.datetime.now(self.eastern)
        today_str = now_et.strftime('%Y-%m-%d')

        if scan_type == "MIDNIGHT_SCAN":
            self.last_midnight_scan = today_str
        elif scan_type == "PREMARKET_SCAN":
            self.last_premarket_scan = today_str

    def is_market_open(self) -> bool:
        """Check if market is currently open"""
        now_et = datetime.datetime.now(self.eastern)
        today_str = now_et.strftime('%Y-%m-%d')

        if today_str in self.holidays:
            print(f"Market closed - Holiday: {today_str}")
            return False

        if now_et.weekday() >= 5:  # Monday = 0, Sunday = 6
            print(f"Market closed - Weekend")
            return False

        market_open = datetime.time(9, 30)
        market_close = datetime.time(16, 0)
        current_time = now_et.time()

        is_open = market_open <= current_time <= market_close
        return is_open

    def get_next_market_open(self) -> datetime.datetime:
        """Get datetime of next market open"""
        now_et = datetime.datetime.now(self.eastern)
        next_open = now_et

        if now_et.time() > datetime.time(16, 0):
            next_open = next_open + datetime.timedelta(days=1)

        while next_open.weekday() >= 5:  # Weekend
            next_open = next_open + datetime.timedelta(days=1)

        while next_open.strftime('%Y-%m-%d') in self.holidays:
            next_open = next_open + datetime.timedelta(days=1)
            while next_open.weekday() >= 5:
                next_open = next_open + datetime.timedelta(days=1)

        next_open = next_open.replace(hour=9, minute=30, second=0, microsecond=0)
        return next_open

    def seconds_until_market_open(self) -> int:
        """Get seconds until next market open"""
        if self.is_market_open():
            return 0
        now_et = datetime.datetime.now(self.eastern)
        next_open = self.get_next_market_open()
        return int((next_open - now_et).total_seconds())
