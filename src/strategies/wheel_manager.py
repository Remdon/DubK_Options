"""
Wheel Manager - Database and State Management for The Wheel Strategy

Manages wheel position lifecycle:
- Database operations for wheel state tracking
- State transitions (SELLING_PUTS → ASSIGNED → SELLING_CALLS)
- Premium tracking across full cycles
- Assignment detection and handling
"""

import logging
import sqlite3
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from enum import Enum


class WheelState(Enum):
    """Wheel position states"""
    SELLING_PUTS = "SELLING_PUTS"  # Phase 1: Selling cash-secured puts
    ASSIGNED = "ASSIGNED"  # Phase 2: Put assigned, now own stock
    SELLING_CALLS = "SELLING_CALLS"  # Phase 3: Selling covered calls on owned stock
    COMPLETED = "COMPLETED"  # Cycle completed (stock called away)


class WheelManager:
    """
    Manages wheel position database and state transitions.

    Database Schema:
    - wheel_positions: Active wheel positions with state tracking
    - wheel_history: Completed wheel cycles with total premiums
    - wheel_transactions: Individual transactions (put sales, assignments, call sales)
    """

    def __init__(self, db_path: str = 'trades.db'):
        """Initialize wheel manager with database connection"""
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()
        logging.info(f"[WHEEL_MANAGER] Initialized with database: {db_path}")

    def create_tables(self):
        """Create wheel-specific database tables"""

        # Active wheel positions
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wheel_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                -- Stock details
                stock_cost_basis REAL,
                shares_owned INTEGER DEFAULT 0,

                -- Premium tracking
                total_premium_collected REAL DEFAULT 0.0,
                put_premium_collected REAL DEFAULT 0.0,
                call_premium_collected REAL DEFAULT 0.0,

                -- Current option position
                current_option_symbol TEXT,
                current_strike REAL,
                current_expiration TEXT,
                current_premium REAL,
                current_entry_date TEXT,

                -- Cycle tracking
                cycle_number INTEGER DEFAULT 1,
                cycles_completed INTEGER DEFAULT 0,

                -- Notes
                notes TEXT
            )
        """)

        # Wheel transaction history
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wheel_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wheel_position_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                transaction_type TEXT NOT NULL,

                symbol TEXT NOT NULL,
                option_symbol TEXT,
                strike REAL,
                expiration TEXT,

                action TEXT,
                quantity INTEGER,
                premium REAL,

                state_before TEXT,
                state_after TEXT,

                notes TEXT,

                FOREIGN KEY (wheel_position_id) REFERENCES wheel_positions(id)
            )
        """)

        # Completed wheel cycles
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wheel_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,

                total_premium_collected REAL,
                stock_assignment_cost REAL,
                stock_sale_price REAL,

                total_profit REAL,
                roi_percent REAL,
                hold_days INTEGER,

                num_puts_sold INTEGER DEFAULT 0,
                num_calls_sold INTEGER DEFAULT 0,

                notes TEXT
            )
        """)

        self.conn.commit()
        logging.info("[WHEEL_MANAGER] Database tables created/verified")

    def create_wheel_position(self, symbol: str, initial_premium: float,
                             option_symbol: str, strike: float, expiration: str,
                             notes: Optional[str] = None) -> int:
        """
        Create a new wheel position in SELLING_PUTS state.

        Returns:
            wheel_position_id
        """
        now = datetime.now().isoformat()

        cursor = self.conn.execute("""
            INSERT INTO wheel_positions (
                symbol, state, created_at, updated_at,
                total_premium_collected, put_premium_collected,
                current_option_symbol, current_strike, current_expiration,
                current_premium, current_entry_date, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, WheelState.SELLING_PUTS.value, now, now,
            initial_premium, initial_premium,
            option_symbol, strike, expiration,
            initial_premium, now, notes
        ))

        wheel_id = cursor.lastrowid

        # Log transaction
        self._log_transaction(
            wheel_id=wheel_id,
            transaction_type='PUT_SOLD',
            symbol=symbol,
            option_symbol=option_symbol,
            strike=strike,
            expiration=expiration,
            action='SELL_TO_OPEN',
            quantity=1,
            premium=initial_premium,
            state_before=None,
            state_after=WheelState.SELLING_PUTS.value,
            notes=f"Initial put sale: ${initial_premium:.2f} premium collected"
        )

        self.conn.commit()
        logging.info(f"[WHEEL_MANAGER] {symbol}: Created wheel position (ID: {wheel_id}) in SELLING_PUTS state")
        return wheel_id

    def mark_assigned(self, symbol: str, assignment_price: float, shares: int = 100) -> bool:
        """
        Mark a wheel position as ASSIGNED (put assigned, now own stock).

        Args:
            symbol: Stock symbol
            assignment_price: Price at which stock was assigned (put strike)
            shares: Number of shares assigned (default 100 per contract)

        Returns:
            True if updated successfully
        """
        position = self.get_wheel_position(symbol)
        if not position:
            logging.error(f"[WHEEL_MANAGER] {symbol}: No wheel position found to mark assigned")
            return False

        if position['state'] != WheelState.SELLING_PUTS.value:
            logging.warning(f"[WHEEL_MANAGER] {symbol}: Position in state {position['state']}, "
                          f"expected SELLING_PUTS")

        now = datetime.now().isoformat()

        self.conn.execute("""
            UPDATE wheel_positions
            SET state = ?,
                stock_cost_basis = ?,
                shares_owned = ?,
                updated_at = ?,
                current_option_symbol = NULL,
                current_strike = NULL,
                current_expiration = NULL
            WHERE symbol = ?
        """, (WheelState.ASSIGNED.value, assignment_price, shares, now, symbol))

        # Log transaction
        self._log_transaction(
            wheel_id=position['id'],
            transaction_type='ASSIGNMENT',
            symbol=symbol,
            action='ASSIGNED',
            quantity=shares,
            premium=0,
            state_before=WheelState.SELLING_PUTS.value,
            state_after=WheelState.ASSIGNED.value,
            notes=f"Put assigned: acquired {shares} shares @ ${assignment_price:.2f}"
        )

        self.conn.commit()
        logging.info(f"[WHEEL_MANAGER] {symbol}: Marked as ASSIGNED - {shares} shares @ ${assignment_price:.2f}")
        return True

    def mark_selling_calls(self, symbol: str, call_premium: float, option_symbol: str,
                          strike: float, expiration: str) -> bool:
        """
        Transition wheel position to SELLING_CALLS state (selling covered calls).

        Args:
            symbol: Stock symbol
            call_premium: Premium collected from call sale
            option_symbol: Option contract symbol
            strike: Call strike price
            expiration: Option expiration date

        Returns:
            True if updated successfully
        """
        position = self.get_wheel_position(symbol)
        if not position:
            logging.error(f"[WHEEL_MANAGER] {symbol}: No wheel position found")
            return False

        if position['state'] not in [WheelState.ASSIGNED.value, WheelState.SELLING_CALLS.value]:
            logging.warning(f"[WHEEL_MANAGER] {symbol}: Unexpected state {position['state']} "
                          f"for selling calls")

        now = datetime.now().isoformat()

        # Update premium tracking
        new_call_premium = position['call_premium_collected'] + call_premium
        new_total_premium = position['total_premium_collected'] + call_premium

        self.conn.execute("""
            UPDATE wheel_positions
            SET state = ?,
                call_premium_collected = ?,
                total_premium_collected = ?,
                current_option_symbol = ?,
                current_strike = ?,
                current_expiration = ?,
                current_premium = ?,
                current_entry_date = ?,
                updated_at = ?
            WHERE symbol = ?
        """, (WheelState.SELLING_CALLS.value, new_call_premium, new_total_premium,
              option_symbol, strike, expiration, call_premium, now, now, symbol))

        # Log transaction
        self._log_transaction(
            wheel_id=position['id'],
            transaction_type='CALL_SOLD',
            symbol=symbol,
            option_symbol=option_symbol,
            strike=strike,
            expiration=expiration,
            action='SELL_TO_OPEN',
            quantity=1,
            premium=call_premium,
            state_before=position['state'],
            state_after=WheelState.SELLING_CALLS.value,
            notes=f"Covered call sold: ${call_premium:.2f} premium collected (total: ${new_total_premium:.2f})"
        )

        self.conn.commit()
        logging.info(f"[WHEEL_MANAGER] {symbol}: Transitioned to SELLING_CALLS - "
                    f"${call_premium:.2f} premium (${new_total_premium:.2f} total)")
        return True

    def mark_completed(self, symbol: str, stock_sale_price: float) -> bool:
        """
        Mark wheel cycle as COMPLETED (stock called away or manually closed).
        Moves position to history and removes from active positions.

        Args:
            symbol: Stock symbol
            stock_sale_price: Price at which stock was sold

        Returns:
            True if completed successfully
        """
        position = self.get_wheel_position(symbol)
        if not position:
            logging.error(f"[WHEEL_MANAGER] {symbol}: No wheel position found")
            return False

        # Calculate profits
        assignment_cost = position['stock_cost_basis'] * position['shares_owned']
        sale_proceeds = stock_sale_price * position['shares_owned']
        stock_profit = sale_proceeds - assignment_cost
        total_profit = position['total_premium_collected'] + stock_profit
        roi = (total_profit / assignment_cost * 100) if assignment_cost > 0 else 0

        # Get transaction count
        transactions = self.get_wheel_transactions(symbol)
        num_puts = sum(1 for t in transactions if t['transaction_type'] == 'PUT_SOLD')
        num_calls = sum(1 for t in transactions if t['transaction_type'] == 'CALL_SOLD')

        # Calculate hold days
        start_date = datetime.fromisoformat(position['created_at'])
        end_date = datetime.now()
        hold_days = (end_date - start_date).days

        # Insert into history
        self.conn.execute("""
            INSERT INTO wheel_history (
                symbol, start_date, end_date,
                total_premium_collected, stock_assignment_cost, stock_sale_price,
                total_profit, roi_percent, hold_days,
                num_puts_sold, num_calls_sold,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, position['created_at'], end_date.isoformat(),
            position['total_premium_collected'], assignment_cost, stock_sale_price,
            total_profit, roi, hold_days,
            num_puts, num_calls,
            f"Completed wheel cycle: {hold_days} days, {roi:.1f}% ROI, ${total_profit:.2f} profit"
        ))

        # Remove from active positions
        self.conn.execute("DELETE FROM wheel_positions WHERE symbol = ?", (symbol,))

        self.conn.commit()
        logging.info(f"[WHEEL_MANAGER] {symbol}: Wheel cycle COMPLETED - "
                    f"{hold_days} days, {roi:.1f}% ROI, ${total_profit:.2f} profit")
        return True

    def get_wheel_position(self, symbol: str) -> Optional[Dict]:
        """Get active wheel position for a symbol"""
        cursor = self.conn.execute("""
            SELECT * FROM wheel_positions WHERE symbol = ?
        """, (symbol,))

        row = cursor.fetchone()
        if not row:
            return None

        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    def get_all_wheel_positions(self) -> List[Dict]:
        """Get all active wheel positions"""
        cursor = self.conn.execute("SELECT * FROM wheel_positions ORDER BY created_at DESC")
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_wheel_positions_by_state(self, state: WheelState) -> List[Dict]:
        """Get all wheel positions in a specific state"""
        cursor = self.conn.execute("""
            SELECT * FROM wheel_positions WHERE state = ? ORDER BY created_at DESC
        """, (state.value,))
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_wheel_transactions(self, symbol: str) -> List[Dict]:
        """Get all transactions for a wheel position"""
        cursor = self.conn.execute("""
            SELECT t.* FROM wheel_transactions t
            JOIN wheel_positions p ON t.wheel_position_id = p.id
            WHERE p.symbol = ?
            ORDER BY t.timestamp DESC
        """, (symbol,))

        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_wheel_history(self, limit: int = 10) -> List[Dict]:
        """Get completed wheel cycles"""
        cursor = self.conn.execute("""
            SELECT * FROM wheel_history
            ORDER BY end_date DESC
            LIMIT ?
        """, (limit,))

        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def reconcile_with_broker(self, broker_positions: List) -> Dict:
        """
        Reconcile wheel database with actual broker positions.
        Removes stale positions that no longer exist in broker account.

        Args:
            broker_positions: List of position objects from broker (Alpaca)

        Returns:
            {
                'removed': int,  # Number of positions removed
                'symbols_removed': List[str]  # Symbols that were removed
            }
        """
        import logging

        # Get all symbols from broker positions (extract underlying from options)
        broker_symbols = set()
        for pos in broker_positions:
            symbol = pos.symbol
            # Extract underlying from OCC format (e.g., "AAPL230616C00150000" -> "AAPL")
            if len(symbol) > 15:  # Options format
                underlying = symbol[:-15]
            else:
                underlying = symbol
            broker_symbols.add(underlying)

        # Get all active wheel positions from database
        cursor = self.conn.execute("""
            SELECT id, underlying_symbol, state FROM wheel_positions
            WHERE state != 'COMPLETED'
        """)
        db_positions = cursor.fetchall()

        # Find positions in database that don't exist in broker
        symbols_to_remove = []
        ids_to_remove = []
        for wheel_id, underlying, state in db_positions:
            if underlying not in broker_symbols:
                symbols_to_remove.append(underlying)
                ids_to_remove.append(wheel_id)
                logging.warning(f"[WHEEL RECONCILE] {underlying} not found in broker - removing from database (was in state: {state})")

        # Remove stale positions
        for wheel_id in ids_to_remove:
            self.conn.execute("""
                DELETE FROM wheel_positions
                WHERE id = ?
            """, (wheel_id,))

        self.conn.commit()

        if len(symbols_to_remove) > 0:
            logging.info(f"[WHEEL RECONCILE] Removed {len(symbols_to_remove)} stale position(s): {', '.join(symbols_to_remove)}")

        return {
            'removed': len(symbols_to_remove),
            'symbols_removed': symbols_to_remove
        }

    def get_wheel_stats(self) -> Dict:
        """
        Get overall wheel strategy statistics.

        Returns:
            {
                'active_positions': int,
                'selling_puts': int,
                'assigned': int,
                'selling_calls': int,
                'total_active_capital': float,
                'total_premium_collected': float,
                'completed_cycles': int,
                'avg_roi': float,
                'win_rate': float
            }
        """
        # Active positions by state (exclude COMPLETED positions)
        cursor = self.conn.execute("""
            SELECT state, COUNT(*) as count, SUM(total_premium_collected) as premium
            FROM wheel_positions
            WHERE state != 'COMPLETED'
            GROUP BY state
        """)
        state_counts = {row[0]: {'count': row[1], 'premium': row[2] or 0} for row in cursor.fetchall()}

        # Total active positions (only non-completed)
        active_positions = sum(s['count'] for s in state_counts.values())

        # Completed cycles
        cursor = self.conn.execute("""
            SELECT
                COUNT(*) as completed,
                AVG(roi_percent) as avg_roi,
                SUM(CASE WHEN total_profit > 0 THEN 1 ELSE 0 END) as wins
            FROM wheel_history
        """)
        row = cursor.fetchone()
        completed = row[0] or 0
        avg_roi = row[1] or 0
        wins = row[2] or 0
        win_rate = (wins / completed * 100) if completed > 0 else 0

        # Total premium collected (active + completed)
        cursor = self.conn.execute("""
            SELECT SUM(total_premium_collected) FROM wheel_positions
        """)
        active_premium = cursor.fetchone()[0] or 0

        cursor = self.conn.execute("""
            SELECT SUM(total_premium_collected) FROM wheel_history
        """)
        completed_premium = cursor.fetchone()[0] or 0

        return {
            'active_positions': active_positions,
            'selling_puts': state_counts.get(WheelState.SELLING_PUTS.value, {}).get('count', 0),
            'assigned': state_counts.get(WheelState.ASSIGNED.value, {}).get('count', 0),
            'selling_calls': state_counts.get(WheelState.SELLING_CALLS.value, {}).get('count', 0),
            'total_active_premium': active_premium,
            'total_premium_collected': active_premium + completed_premium,
            'completed_cycles': completed,
            'avg_roi': avg_roi,
            'win_rate': win_rate
        }

    def _log_transaction(self, wheel_id: int, transaction_type: str, symbol: str,
                        action: str, quantity: int, premium: float,
                        state_before: Optional[str], state_after: str,
                        option_symbol: Optional[str] = None,
                        strike: Optional[float] = None,
                        expiration: Optional[str] = None,
                        notes: Optional[str] = None):
        """Log a wheel transaction"""
        self.conn.execute("""
            INSERT INTO wheel_transactions (
                wheel_position_id, timestamp, transaction_type,
                symbol, option_symbol, strike, expiration,
                action, quantity, premium,
                state_before, state_after, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            wheel_id, datetime.now().isoformat(), transaction_type,
            symbol, option_symbol, strike, expiration,
            action, quantity, premium,
            state_before, state_after, notes
        ))

    def update_option_expired(self, symbol: str, expired_worthless: bool = True) -> bool:
        """
        Update wheel position when current option expires.

        Args:
            symbol: Stock symbol
            expired_worthless: True if option expired worthless (we keep premium)

        Returns:
            True if updated successfully
        """
        position = self.get_wheel_position(symbol)
        if not position:
            return False

        now = datetime.now().isoformat()

        if position['state'] == WheelState.SELLING_PUTS.value:
            if expired_worthless:
                # Put expired worthless - keep premium, can sell another put
                self.conn.execute("""
                    UPDATE wheel_positions
                    SET current_option_symbol = NULL,
                        current_strike = NULL,
                        current_expiration = NULL,
                        updated_at = ?
                    WHERE symbol = ?
                """, (now, symbol))

                self._log_transaction(
                    wheel_id=position['id'],
                    transaction_type='PUT_EXPIRED',
                    symbol=symbol,
                    action='EXPIRED_WORTHLESS',
                    quantity=0,
                    premium=0,
                    state_before=WheelState.SELLING_PUTS.value,
                    state_after=WheelState.SELLING_PUTS.value,
                    notes=f"Put expired worthless - kept ${position['current_premium']:.2f} premium"
                )

                self.conn.commit()
                logging.info(f"[WHEEL_MANAGER] {symbol}: Put expired worthless - ready to sell new put")
                return True

        elif position['state'] == WheelState.SELLING_CALLS.value:
            if expired_worthless:
                # Call expired worthless - keep premium and stock, can sell another call
                self.conn.execute("""
                    UPDATE wheel_positions
                    SET current_option_symbol = NULL,
                        current_strike = NULL,
                        current_expiration = NULL,
                        updated_at = ?
                    WHERE symbol = ?
                """, (now, symbol))

                self._log_transaction(
                    wheel_id=position['id'],
                    transaction_type='CALL_EXPIRED',
                    symbol=symbol,
                    action='EXPIRED_WORTHLESS',
                    quantity=0,
                    premium=0,
                    state_before=WheelState.SELLING_CALLS.value,
                    state_after=WheelState.SELLING_CALLS.value,
                    notes=f"Call expired worthless - kept ${position['current_premium']:.2f} premium and stock"
                )

                self.conn.commit()
                logging.info(f"[WHEEL_MANAGER] {symbol}: Call expired worthless - ready to sell new call")
                return True

        return False

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logging.info("[WHEEL_MANAGER] Database connection closed")
