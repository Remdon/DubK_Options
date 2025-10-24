"""
Economic Calendar - Earnings and Market Event Tracking

Monitors:
- Earnings dates for symbols
- Earnings risk assessment (IV crush warnings)
- FOMC meetings and high-impact events
- Daily cache refresh
"""

import logging
import time
from datetime import datetime, date, timedelta
from typing import Dict, Optional


class EconomicCalendar:
    """Economic calendar and market event awareness"""

    def __init__(self):
        self.earnings_cache = {}
        self.events_cache = {}
        # FIXED: Issue #14 - Cache set to 4 hours (was 1 hour, tracker suggested 4-6 hours)
        # Earnings dates can change, so we refresh periodically
        self.cache_expiry = 14400  # 4 hours (4 * 3600)
        self.last_refresh_date = None  # Track last refresh date for daily reset

    def get_next_earnings(self, symbol: str) -> Optional[datetime]:
        """Get next earnings date for symbol"""
        # FIXED: Issue #14 - Force refresh on new trading day
        current_date = datetime.now().date()
        if self.last_refresh_date != current_date:
            logging.info(f"New trading day detected, clearing earnings cache (was: {self.last_refresh_date}, now: {current_date})")
            self.earnings_cache.clear()
            self.last_refresh_date = current_date

        # Check cache
        if symbol in self.earnings_cache:
            cached_time, cached_date = self.earnings_cache[symbol]
            if time.time() - cached_time < self.cache_expiry:
                return cached_date

        try:
            # Try to get earnings from yfinance
            import yfinance as yf
            ticker = yf.Ticker(symbol)

            # Get earnings dates
            if hasattr(ticker, 'calendar') and ticker.calendar is not None:
                earnings_date = ticker.calendar.get('Earnings Date')
                if earnings_date is not None and len(earnings_date) > 0:
                    next_earnings = earnings_date[0]

                    # Convert to datetime if needed
                    if hasattr(next_earnings, 'to_pydatetime'):
                        next_earnings = next_earnings.to_pydatetime()
                    elif isinstance(next_earnings, date) and not isinstance(next_earnings, datetime):
                        # Convert date to datetime (set time to midnight)
                        next_earnings = datetime.combine(next_earnings, datetime.min.time())

                    self.earnings_cache[symbol] = (time.time(), next_earnings)
                    return next_earnings

            return None

        except Exception as e:
            logging.debug(f"Could not get earnings for {symbol}: {e}")
            return None

    def check_earnings_risk(self, symbol: str) -> Dict:
        """Check if symbol has earnings risk"""
        earnings_date = self.get_next_earnings(symbol)

        if not earnings_date:
            return {
                'risk': 'UNKNOWN',
                'days_to_earnings': None,
                'action': 'PROCEED',
                'reason': 'No earnings data available'
            }

        days_to_earnings = (earnings_date - datetime.now()).days

        if 0 <= days_to_earnings <= 3:
            return {
                'risk': 'CRITICAL',
                'days_to_earnings': days_to_earnings,
                'action': 'AVOID',
                'reason': f'Earnings in {days_to_earnings} days - HIGH IV crush risk'
            }
        elif 4 <= days_to_earnings <= 7:
            return {
                'risk': 'MODERATE',
                'days_to_earnings': days_to_earnings,
                'action': 'CAUTION',
                'reason': f'Earnings in {days_to_earnings} days - approach with caution'
            }
        else:
            return {
                'risk': 'LOW',
                'days_to_earnings': days_to_earnings,
                'action': 'PROCEED',
                'reason': f'Earnings in {days_to_earnings} days'
            }

    def get_market_events(self) -> Dict:
        """Get upcoming high-impact market events"""
        # Simplified - in production would pull from economic calendar APIs
        today = datetime.now().date()
        upcoming_events = []

        # Add FOMC dates (simplified schedule)
        fomc_dates = [date(today.year, 1, 31), date(today.year, 3, 19),
                     date(today.year, 4, 30), date(today.year, 6, 11),
                     date(today.year, 7, 31), date(today.year, 9, 17),
                     date(today.year, 11, 6), date(today.year, 12, 18)]

        for event_date in fomc_dates:
            if event_date >= today:
                days_away = (event_date - today).days
                upcoming_events.append({
                    'event': 'FOMC Meeting',
                    'date': event_date,
                    'days_away': days_away,
                    'impact': 'VERY_HIGH',
                    'description': 'Federal Reserve monetary policy decision'
                })
                if len(upcoming_events) >= 3:  # Only next 3
                    break

        return {
            'upcoming_events': upcoming_events,
            'next_high_impact': upcoming_events[0] if upcoming_events else None
        }
