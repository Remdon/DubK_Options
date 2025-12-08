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
import threading
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
        self.db_lock = threading.Lock()  # CRITICAL FIX: Thread-safe database access
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

        # Symbol performance tracking (for dynamic position sizing)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS symbol_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,

                -- Performance metrics
                trades_total INTEGER DEFAULT 0,
                trades_won INTEGER DEFAULT 0,
                trades_lost INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,

                -- Profit tracking
                total_profit REAL DEFAULT 0.0,
                avg_profit_per_trade REAL DEFAULT 0.0,
                avg_roi_pct REAL DEFAULT 0.0,

                -- Risk metrics
                max_drawdown REAL DEFAULT 0.0,
                consecutive_losses INTEGER DEFAULT 0,
                max_consecutive_losses INTEGER DEFAULT 0,

                -- Timing
                avg_hold_days REAL DEFAULT 0.0,
                last_trade_date TEXT,
                updated_at TEXT NOT NULL,

                -- Quality score (0-100)
                quality_score REAL DEFAULT 50.0
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
        with self.db_lock:  # CRITICAL FIX: Thread-safe database write
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
        with self.db_lock:  # CRITICAL FIX: Thread-safe database write + idempotency
            position = self.get_wheel_position(symbol)
            if not position:
                logging.error(f"[WHEEL_MANAGER] {symbol}: No wheel position found to mark assigned")
                return False

            # CRITICAL FIX: Idempotency check - don't re-assign if already assigned
            if position['state'] == WheelState.ASSIGNED.value:
                logging.warning(f"[WHEEL_MANAGER] {symbol}: Already in ASSIGNED state, skipping duplicate assignment")
                return True  # Not an error, just already processed

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
            SELECT id, symbol, state FROM wheel_positions
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

    def update_symbol_performance(self, symbol: str, profit: float, roi_pct: float,
                                  hold_days: int, was_winner: bool) -> None:
        """
        Update symbol performance metrics after a trade closes.

        Args:
            symbol: Stock symbol
            profit: Total profit/loss from the trade
            roi_pct: Return on investment percentage
            hold_days: Number of days position was held
            was_winner: True if trade was profitable
        """
        try:
            # Get existing performance or create new
            cursor = self.conn.execute("""
                SELECT id, trades_total, trades_won, trades_lost, total_profit,
                       consecutive_losses, max_consecutive_losses, avg_hold_days
                FROM symbol_performance
                WHERE symbol = ?
            """, (symbol,))

            row = cursor.fetchone()

            now = datetime.now().isoformat()

            if row:
                # Update existing record
                perf_id, trades_total, trades_won, trades_lost, total_profit, \
                    consecutive_losses, max_consecutive_losses, avg_hold_days = row

                new_trades_total = trades_total + 1
                new_trades_won = trades_won + (1 if was_winner else 0)
                new_trades_lost = trades_lost + (0 if was_winner else 1)
                new_total_profit = total_profit + profit

                # Update consecutive losses
                if was_winner:
                    new_consecutive_losses = 0
                else:
                    new_consecutive_losses = consecutive_losses + 1
                    max_consecutive_losses = max(max_consecutive_losses, new_consecutive_losses)

                # Calculate updated metrics
                new_win_rate = (new_trades_won / new_trades_total) * 100 if new_trades_total > 0 else 0
                new_avg_profit = new_total_profit / new_trades_total if new_trades_total > 0 else 0

                # Weighted average hold days (give more weight to recent trades)
                new_avg_hold_days = (avg_hold_days * 0.7 + hold_days * 0.3) if avg_hold_days > 0 else hold_days

                # Calculate quality score (0-100)
                # Factors: win rate (40%), avg profit (30%), consistency (20%), recency (10%)
                win_rate_score = new_win_rate  # Already 0-100
                profit_score = min(max(new_avg_profit / 100, 0), 100)  # Normalize to 0-100
                consistency_score = max(0, 100 - (max_consecutive_losses * 20))  # Penalize losing streaks
                recency_bonus = 10 if was_winner else 0  # Bonus for recent win

                quality_score = (
                    win_rate_score * 0.40 +
                    profit_score * 0.30 +
                    consistency_score * 0.20 +
                    recency_bonus * 0.10
                )

                self.conn.execute("""
                    UPDATE symbol_performance
                    SET trades_total = ?,
                        trades_won = ?,
                        trades_lost = ?,
                        win_rate = ?,
                        total_profit = ?,
                        avg_profit_per_trade = ?,
                        consecutive_losses = ?,
                        max_consecutive_losses = ?,
                        avg_hold_days = ?,
                        last_trade_date = ?,
                        updated_at = ?,
                        quality_score = ?
                    WHERE symbol = ?
                """, (new_trades_total, new_trades_won, new_trades_lost, new_win_rate,
                     new_total_profit, new_avg_profit, new_consecutive_losses,
                     max_consecutive_losses, new_avg_hold_days, now, now, quality_score, symbol))

                logging.info(f"[PERFORMANCE] {symbol}: Updated - " +
                           f"{new_trades_won}/{new_trades_total} wins ({new_win_rate:.1f}%), " +
                           f"quality score: {quality_score:.1f}/100")

            else:
                # Create new record
                trades_total = 1
                trades_won = 1 if was_winner else 0
                trades_lost = 0 if was_winner else 1
                win_rate = 100.0 if was_winner else 0.0
                consecutive_losses = 0 if was_winner else 1
                max_consecutive_losses = consecutive_losses

                # Initial quality score
                quality_score = 70.0 if was_winner else 30.0

                self.conn.execute("""
                    INSERT INTO symbol_performance (
                        symbol, trades_total, trades_won, trades_lost, win_rate,
                        total_profit, avg_profit_per_trade, consecutive_losses,
                        max_consecutive_losses, avg_hold_days, last_trade_date,
                        updated_at, quality_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, trades_total, trades_won, trades_lost, win_rate,
                     profit, profit, consecutive_losses, max_consecutive_losses,
                     hold_days, now, now, quality_score))

                logging.info(f"[PERFORMANCE] {symbol}: Created - " +
                           f"first trade {'WON' if was_winner else 'LOST'}, quality score: {quality_score:.1f}/100")

            self.conn.commit()

        except Exception as e:
            logging.error(f"[PERFORMANCE] {symbol}: Error updating performance: {e}")
            import traceback
            traceback.print_exc()

    def get_symbol_performance(self, symbol: str) -> Optional[Dict]:
        """
        Get performance metrics for a symbol.

        Returns:
            Dict with performance metrics or None if no data
        """
        try:
            cursor = self.conn.execute("""
                SELECT trades_total, trades_won, trades_lost, win_rate,
                       total_profit, avg_profit_per_trade, avg_roi_pct,
                       max_drawdown, consecutive_losses, max_consecutive_losses,
                       avg_hold_days, last_trade_date, quality_score
                FROM symbol_performance
                WHERE symbol = ?
            """, (symbol,))

            row = cursor.fetchone()

            if row:
                return {
                    'trades_total': row[0],
                    'trades_won': row[1],
                    'trades_lost': row[2],
                    'win_rate': row[3],
                    'total_profit': row[4],
                    'avg_profit_per_trade': row[5],
                    'avg_roi_pct': row[6],
                    'max_drawdown': row[7],
                    'consecutive_losses': row[8],
                    'max_consecutive_losses': row[9],
                    'avg_hold_days': row[10],
                    'last_trade_date': row[11],
                    'quality_score': row[12]
                }

            return None

        except Exception as e:
            logging.error(f"[PERFORMANCE] {symbol}: Error fetching performance: {e}")
            return None

    def check_for_assignments(self, trading_client) -> List[str]:
        """
        Check if any short puts have been assigned (now own stock).

        This method detects when a short put position results in stock ownership,
        automatically transitioning the wheel position to ASSIGNED state and
        preparing to sell covered calls.

        Args:
            trading_client: Alpaca trading client to fetch current positions

        Returns:
            List of symbols that were assigned and transitioned
        """
        assigned_symbols = []

        try:
            # Get all current positions from broker
            all_positions = trading_client.get_all_positions()

            # Identify stock positions (non-option symbols)
            stock_positions = [p for p in all_positions if len(p.symbol) <= 6]  # Stock symbols are typically short

            for stock_pos in stock_positions:
                symbol = stock_pos.symbol
                shares_owned = int(stock_pos.qty) if stock_pos.qty else 0

                # Only process long stock positions (positive quantity)
                if shares_owned <= 0:
                    continue

                # Check if we have an active wheel position in SELLING_PUTS state
                wheel_pos = self.get_wheel_position(symbol)

                if wheel_pos and wheel_pos['state'] == WheelState.SELLING_PUTS.value:
                    # ASSIGNMENT DETECTED!
                    logging.info(f"[WHEEL ASSIGNMENT] {symbol} - Put was assigned, now own {shares_owned} shares")

                    # CRITICAL FIX: Calculate cost basis correctly
                    # put_premium is total premium for all contracts (e.g., $200 for 2 contracts)
                    # shares_owned is total shares (e.g., 200 shares = 2 contracts)
                    # Need to divide by 100 to get premium per share
                    strike = wheel_pos['current_strike']
                    put_premium = wheel_pos['put_premium_collected']
                    num_contracts = shares_owned // 100 if shares_owned > 0 else 0
                    premium_per_share = (put_premium / num_contracts) / 100 if num_contracts > 0 else 0
                    cost_basis = strike - premium_per_share

                    logging.info(f"[WHEEL ASSIGNMENT] {symbol} - Cost basis: ${cost_basis:.2f}/share " +
                               f"(strike ${strike:.2f} - ${premium_per_share:.2f} premium)")

                    # Update database: SELLING_PUTS → ASSIGNED
                    self.conn.execute("""
                        UPDATE wheel_positions
                        SET state = ?,
                            shares_owned = ?,
                            stock_cost_basis = ?,
                            current_option_symbol = NULL,
                            updated_at = ?
                        WHERE symbol = ?
                    """, (WheelState.ASSIGNED.value, shares_owned, cost_basis,
                         datetime.now().isoformat(), symbol))

                    # Log the assignment transaction
                    self._log_transaction(
                        wheel_id=wheel_pos['id'],
                        transaction_type='ASSIGNMENT',
                        symbol=symbol,
                        action='PUT_ASSIGNED',
                        quantity=shares_owned,
                        premium=0,  # No new premium, just state change
                        state_before=WheelState.SELLING_PUTS.value,
                        state_after=WheelState.ASSIGNED.value,
                        notes=f"Put assigned at ${strike:.2f}, cost basis ${cost_basis:.2f}/share"
                    )

                    self.conn.commit()
                    assigned_symbols.append(symbol)

                    logging.info(f"[WHEEL ASSIGNMENT] {symbol} - Transitioned to ASSIGNED state, ready for covered calls")

        except Exception as e:
            logging.error(f"[WHEEL_MANAGER] Error checking for assignments: {e}")
            import traceback
            traceback.print_exc()

        return assigned_symbols

    def sell_covered_call(self, symbol: str, trading_client, wheel_strategy) -> bool:
        """
        Automatically sell covered call on assigned stock.

        Called when a wheel position is in ASSIGNED state to transition to
        SELLING_CALLS state by selling a call option above cost basis.

        Args:
            symbol: Stock symbol
            trading_client: Alpaca trading client for order execution
            wheel_strategy: WheelStrategy instance for option selection

        Returns:
            True if covered call successfully sold
        """
        try:
            # Get wheel position
            wheel_pos = self.get_wheel_position(symbol)
            if not wheel_pos:
                logging.error(f"[WHEEL_MANAGER] {symbol}: No wheel position found")
                return False

            if wheel_pos['state'] != WheelState.ASSIGNED.value:
                logging.error(f"[WHEEL_MANAGER] {symbol}: Not in ASSIGNED state (current: {wheel_pos['state']})")
                return False

            shares_owned = wheel_pos['shares_owned']
            cost_basis = wheel_pos['stock_cost_basis']

            if not shares_owned or shares_owned <= 0:
                logging.error(f"[WHEEL_MANAGER] {symbol}: No shares owned")
                return False

            logging.info(f"[WHEEL_MANAGER] {symbol}: Selecting covered call (cost basis ${cost_basis:.2f})")

            # Get current stock price
            from src.data.market_data import MarketDataClient
            market_data = MarketDataClient()
            quote = market_data.get_quote(symbol)

            if not quote:
                logging.error(f"[WHEEL_MANAGER] {symbol}: Could not fetch quote")
                return False

            current_price = quote.get('last_price', 0)

            # Calculate target call strike (5% above cost basis, from config)
            target_strike_multiplier = 1.05  # 5% above cost basis
            min_call_strike = cost_basis * target_strike_multiplier

            logging.info(f"[WHEEL_MANAGER] {symbol}: Current price ${current_price:.2f}, " +
                       f"seeking call strike ≥ ${min_call_strike:.2f}")

            # Use wheel_strategy to find best covered call
            call_option = wheel_strategy.select_covered_call(
                symbol=symbol,
                min_strike=min_call_strike,
                current_price=current_price,
                shares_owned=shares_owned
            )

            if not call_option:
                logging.warning(f"[WHEEL_MANAGER] {symbol}: No suitable covered call found")
                return False

            # Extract call details
            call_symbol = call_option['symbol']
            call_strike = call_option['strike']
            call_expiration = call_option['expiration']
            call_premium = call_option['premium']
            call_qty = shares_owned // 100  # 1 contract per 100 shares

            if call_qty <= 0:
                logging.error(f"[WHEEL_MANAGER] {symbol}: Not enough shares for 1 contract ({shares_owned} shares)")
                return False

            total_premium = call_premium * call_qty * 100

            logging.info(f"[WHEEL_MANAGER] {symbol}: Selling {call_qty} covered call(s) " +
                       f"${call_strike:.2f} strike, exp {call_expiration}, premium ${call_premium:.2f}")

            # Place the order
            from alpaca.trading.requests import OrderRequest
            from alpaca.trading.enums import OrderSide, OrderType, TimeInForce

            order_request = OrderRequest(
                symbol=call_symbol,
                qty=call_qty,
                side=OrderSide.SELL,
                type=OrderType.LIMIT,
                time_in_force=TimeInForce.DAY,
                limit_price=call_premium
            )

            order = trading_client.submit_order(order_request)

            if order:
                logging.info(f"[WHEEL_MANAGER] {symbol}: Covered call order placed - Order ID: {order.id}")

                # Update database: ASSIGNED → SELLING_CALLS
                self.conn.execute("""
                    UPDATE wheel_positions
                    SET state = ?,
                        current_option_symbol = ?,
                        current_strike = ?,
                        current_expiration = ?,
                        current_premium = ?,
                        current_entry_date = ?,
                        call_premium_collected = call_premium_collected + ?,
                        total_premium_collected = total_premium_collected + ?,
                        updated_at = ?
                    WHERE symbol = ?
                """, (WheelState.SELLING_CALLS.value, call_symbol, call_strike, call_expiration,
                     total_premium, datetime.now().isoformat(), total_premium, total_premium,
                     datetime.now().isoformat(), symbol))

                # Log the transaction
                self._log_transaction(
                    wheel_id=wheel_pos['id'],
                    transaction_type='COVERED_CALL',
                    symbol=symbol,
                    action='SELL_CALL',
                    quantity=call_qty,
                    premium=total_premium,
                    state_before=WheelState.ASSIGNED.value,
                    state_after=WheelState.SELLING_CALLS.value,
                    option_symbol=call_symbol,
                    strike=call_strike,
                    expiration=call_expiration,
                    notes=f"Sold {call_qty} covered call(s) at ${call_strike:.2f} strike, ${call_premium:.2f} premium"
                )

                self.conn.commit()

                logging.info(f"[WHEEL_MANAGER] {symbol}: Transitioned to SELLING_CALLS state")
                return True
            else:
                logging.error(f"[WHEEL_MANAGER] {symbol}: Failed to place covered call order")
                return False

        except Exception as e:
            logging.error(f"[WHEEL_MANAGER] {symbol}: Error selling covered call: {e}")
            import traceback
            traceback.print_exc()
            return False

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logging.info("[WHEEL_MANAGER] Database connection closed")
