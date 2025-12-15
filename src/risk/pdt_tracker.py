"""
Pattern Day Trading (PDT) Tracker
==================================

Prevents PDT violations for accounts with <$25k equity by tracking day trades
and enforcing protective limits.

PDT Rule:
- 4+ day trades within 5 rolling business days = PDT flag
- Day trade = Open AND close same security on same day
- Accounts <$25k limited to 3 day trades per 5-day window
- Violation = 90-day trading restriction

This tracker helps avoid getting stuck in positions you can't close.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sqlite3
from pathlib import Path


class PDTTracker:
    """
    Tracks day trades to prevent Pattern Day Trading violations.

    Strategy:
    1. Track all same-day open+close events as day trades
    2. Count day trades in rolling 5-business-day window
    3. Reserve 1 day trade for emergencies (use max 2 of 3 available)
    4. Block new positions if day trade budget exhausted
    5. Prefer holding positions overnight to avoid day trades
    """

    def __init__(self, db_path: str = "trades.db"):
        """
        Initialize PDT tracker with database storage.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.PDT_LIMIT = 3  # Max day trades allowed in 5 days
        self.PDT_RESERVE = 1  # Reserve 1 for emergencies
        self.PDT_WINDOW_DAYS = 5  # Rolling 5-business-day window

        self._init_database()

    def _init_database(self):
        """Create day_trades table if it doesn't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    trade_date DATE NOT NULL,
                    open_time TIMESTAMP,
                    close_time TIMESTAMP,
                    account TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, trade_date, account)
                )
            """)

            # Create index for faster lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_day_trades_date
                ON day_trades(trade_date DESC)
            """)

            conn.commit()
            conn.close()

            logging.info("[PDT] Day trades tracking table initialized")

        except Exception as e:
            logging.error(f"[PDT] Error initializing database: {e}")

    def record_day_trade(
        self,
        symbol: str,
        account: str = "spread",
        open_time: Optional[datetime] = None,
        close_time: Optional[datetime] = None,
        notes: str = ""
    ) -> bool:
        """
        Record a day trade event.

        Args:
            symbol: Stock/option symbol
            account: Account name (wheel/spread)
            open_time: When position was opened
            close_time: When position was closed
            notes: Reason for day trade (e.g., "STOP_LOSS", "PROFIT_TARGET")

        Returns:
            True if recorded successfully
        """
        try:
            trade_date = datetime.now().date()

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO day_trades
                (symbol, trade_date, open_time, close_time, account, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                trade_date,
                open_time.isoformat() if open_time else None,
                close_time.isoformat() if close_time else None,
                account,
                notes
            ))

            conn.commit()
            conn.close()

            # Check if approaching limit
            count = self.get_day_trade_count()
            remaining = self.PDT_LIMIT - count

            logging.warning(
                f"[PDT] ⚠️ Day trade recorded: {symbol} ({notes}) - "
                f"{count}/{self.PDT_LIMIT} day trades used, {remaining} remaining"
            )

            if remaining <= self.PDT_RESERVE:
                logging.error(
                    f"[PDT] 🚨 CRITICAL: Only {remaining} day trade(s) remaining! "
                    f"Reserve for emergencies only."
                )

            return True

        except Exception as e:
            logging.error(f"[PDT] Error recording day trade: {e}")
            return False

    def get_day_trade_count(self, days_back: int = 5) -> int:
        """
        Get count of day trades in rolling window.

        Args:
            days_back: Number of days to look back (default 5)

        Returns:
            Number of day trades in window
        """
        try:
            # Calculate cutoff date (5 business days back)
            cutoff_date = self._get_business_days_back(days_back)

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*)
                FROM day_trades
                WHERE trade_date >= ?
            """, (cutoff_date,))

            count = cursor.fetchone()[0]
            conn.close()

            return count

        except Exception as e:
            logging.error(f"[PDT] Error getting day trade count: {e}")
            return 0

    def can_day_trade(self, reserve_for_emergency: bool = True) -> bool:
        """
        Check if we can make another day trade without violating PDT.

        Args:
            reserve_for_emergency: If True, reserve 1 day trade for emergencies

        Returns:
            True if day trade is allowed
        """
        count = self.get_day_trade_count()

        if reserve_for_emergency:
            # Reserve 1 day trade for emergencies (use max 2 of 3)
            limit = self.PDT_LIMIT - self.PDT_RESERVE
        else:
            # Allow using all 3 day trades
            limit = self.PDT_LIMIT

        return count < limit

    def get_remaining_day_trades(self, reserve_for_emergency: bool = True) -> int:
        """
        Get number of day trades remaining.

        Args:
            reserve_for_emergency: If True, account for reserved trade

        Returns:
            Number of day trades available
        """
        count = self.get_day_trade_count()

        if reserve_for_emergency:
            limit = self.PDT_LIMIT - self.PDT_RESERVE
        else:
            limit = self.PDT_LIMIT

        return max(0, limit - count)

    def get_recent_day_trades(self, days_back: int = 5) -> List[Dict]:
        """
        Get list of recent day trades.

        Args:
            days_back: Number of days to look back

        Returns:
            List of day trade records
        """
        try:
            cutoff_date = self._get_business_days_back(days_back)

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, trade_date, account, notes, created_at
                FROM day_trades
                WHERE trade_date >= ?
                ORDER BY trade_date DESC
            """, (cutoff_date,))

            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    'symbol': row[0],
                    'trade_date': row[1],
                    'account': row[2],
                    'notes': row[3],
                    'created_at': row[4]
                }
                for row in rows
            ]

        except Exception as e:
            logging.error(f"[PDT] Error getting recent day trades: {e}")
            return []

    def _get_business_days_back(self, days: int) -> datetime.date:
        """
        Calculate date N business days ago (excluding weekends).

        Args:
            days: Number of business days to go back

        Returns:
            Date N business days ago
        """
        current_date = datetime.now().date()
        business_days_counted = 0

        while business_days_counted < days:
            current_date -= timedelta(days=1)
            # Skip weekends (Saturday=5, Sunday=6)
            if current_date.weekday() < 5:
                business_days_counted += 1

        return current_date

    def should_open_position(self) -> tuple[bool, str]:
        """
        Check if we should open a new position considering PDT risk.

        Returns:
            (allowed, reason) tuple
        """
        remaining = self.get_remaining_day_trades(reserve_for_emergency=True)

        if remaining <= 0:
            return (
                False,
                f"PDT limit reached ({self.get_day_trade_count()}/{self.PDT_LIMIT} "
                f"day trades used in 5 days). Cannot open positions that may need "
                f"same-day exit."
            )

        if remaining == 1:
            return (
                True,
                f"⚠️ Warning: Only {remaining} day trade remaining (reserved for emergencies). "
                f"Position MUST be held overnight if possible."
            )

        return (
            True,
            f"PDT OK: {remaining} day trades available (using {self.get_day_trade_count()}/{self.PDT_LIMIT})"
        )

    def is_same_day_open(self, symbol: str, account: str = "spread") -> bool:
        """
        Check if a position for this symbol was opened today.

        Args:
            symbol: Stock/option symbol (underlying, not OCC)
            account: Account name

        Returns:
            True if position was opened today
        """
        try:
            today = datetime.now().date()

            # Check day_trades table for today's opens
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*)
                FROM day_trades
                WHERE symbol = ?
                AND trade_date = ?
                AND account = ?
            """, (symbol, today, account))

            count = cursor.fetchone()[0]
            conn.close()

            if count > 0:
                return True

            # Also check spread_positions for today's entries
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM spread_positions
                WHERE symbol = ?
                AND DATE(entry_date) = ?
                AND state = 'OPEN'
            """, (symbol, today))

            count = cursor.fetchone()[0]
            conn.close()

            return count > 0

        except Exception as e:
            logging.error(f"[PDT] Error checking same-day open: {e}")
            return False
