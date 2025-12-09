"""
Spread Manager - Database and State Management for Bull Put Spread Strategy

Manages spread position lifecycle:
- Database operations for spread tracking
- State transitions (OPEN → CLOSED)
- P&L tracking per spread
- Performance analytics per symbol
"""

import logging
import sqlite3
import threading
from typing import List, Dict, Optional
from datetime import datetime
from enum import Enum


class SpreadState(Enum):
    """Spread position states"""
    OPEN = "OPEN"  # Spread is active
    CLOSED_PROFIT = "CLOSED_PROFIT"  # Closed at profit target
    CLOSED_LOSS = "CLOSED_LOSS"  # Closed at stop loss
    EXPIRED_MAX_PROFIT = "EXPIRED_MAX_PROFIT"  # Expired worthless (max profit)
    EXPIRED_MAX_LOSS = "EXPIRED_MAX_LOSS"  # Expired ITM (max loss)


class SpreadManager:
    """
    Manages bull put spread position database and state tracking.

    Database Schema:
    - spread_positions: Active spread positions
    - spread_history: Closed spread positions with P&L
    - spread_symbol_performance: Performance tracking per symbol
    """

    def __init__(self, db_path: str = 'spreads.db'):
        """Initialize spread manager with database connection"""
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.db_lock = threading.Lock()  # CRITICAL FIX: Thread-safe database access
        self.create_tables()
        logging.info(f"[SPREAD_MANAGER] Initialized with database: {db_path}")

    def create_tables(self):
        """Create spread-specific database tables"""

        # Active spread positions
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS spread_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                -- Spread details
                short_strike REAL NOT NULL,
                long_strike REAL NOT NULL,
                spread_width REAL NOT NULL,
                expiration TEXT NOT NULL,

                -- Contract symbols
                short_put_symbol TEXT NOT NULL,
                long_put_symbol TEXT NOT NULL,

                -- Entry details
                num_contracts INTEGER NOT NULL,
                credit_per_spread REAL NOT NULL,
                total_credit REAL NOT NULL,
                entry_date TEXT NOT NULL,

                -- Risk metrics
                max_risk REAL NOT NULL,
                max_profit REAL NOT NULL,
                roi_percent REAL NOT NULL,

                -- Greeks at entry
                entry_delta REAL,
                entry_theta REAL,
                entry_vega REAL,

                -- Current status
                state TEXT NOT NULL,
                current_value REAL,
                unrealized_pnl REAL,
                unrealized_pnl_pct REAL,

                -- DTE tracking
                entry_dte INTEGER,
                current_dte INTEGER,

                -- Exit details (NULL if still open)
                exit_date TEXT,
                exit_price REAL,
                realized_pnl REAL,
                realized_pnl_pct REAL,
                hold_days INTEGER,
                exit_reason TEXT,

                -- Notes
                notes TEXT
            )
        """)

        # Spread history (completed positions)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS spread_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,

                -- Spread details
                short_strike REAL NOT NULL,
                long_strike REAL NOT NULL,
                spread_width REAL NOT NULL,
                expiration TEXT NOT NULL,

                -- Entry
                num_contracts INTEGER NOT NULL,
                credit_received REAL NOT NULL,

                -- Exit
                exit_price REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                realized_pnl_pct REAL NOT NULL,
                roi_percent REAL NOT NULL,

                -- Outcome
                outcome TEXT NOT NULL,
                hold_days INTEGER NOT NULL,

                -- Metrics
                max_risk REAL NOT NULL,
                max_profit REAL NOT NULL,

                notes TEXT
            )
        """)

        # Symbol performance tracking
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS spread_symbol_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,

                -- Performance metrics
                spreads_total INTEGER DEFAULT 0,
                spreads_won INTEGER DEFAULT 0,
                spreads_lost INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,

                -- Profit tracking
                total_profit REAL DEFAULT 0.0,
                avg_profit_per_spread REAL DEFAULT 0.0,
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
        logging.info("[SPREAD_MANAGER] Database tables created/verified")

    def create_spread_position(self, symbol: str, short_strike: float, long_strike: float,
                               short_put_symbol: str, long_put_symbol: str,
                               num_contracts: int, credit_per_spread: float,
                               expiration: str, entry_dte: int,
                               entry_delta: Optional[float] = None,
                               notes: Optional[str] = None) -> int:
        """
        Create a new spread position.

        Returns:
            spread_position_id
        """
        now = datetime.now().isoformat()

        spread_width = short_strike - long_strike
        total_credit = credit_per_spread * num_contracts
        max_risk = (spread_width * 100 * num_contracts) - (total_credit * 100)
        max_profit = total_credit * 100
        roi_percent = (max_profit / max_risk * 100) if max_risk > 0 else 0

        cursor = self.conn.execute("""
            INSERT INTO spread_positions (
                symbol, created_at, updated_at,
                short_strike, long_strike, spread_width, expiration,
                short_put_symbol, long_put_symbol,
                num_contracts, credit_per_spread, total_credit, entry_date,
                max_risk, max_profit, roi_percent,
                entry_delta, entry_dte, current_dte,
                state, current_value, unrealized_pnl, unrealized_pnl_pct, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, now, now,
            short_strike, long_strike, spread_width, expiration,
            short_put_symbol, long_put_symbol,
            num_contracts, credit_per_spread, total_credit, now,
            max_risk, max_profit, roi_percent,
            entry_delta, entry_dte, entry_dte,
            SpreadState.OPEN.value, total_credit, 0, 0, notes
        ))

        spread_id = cursor.lastrowid
        self.conn.commit()

        logging.info(f"[SPREAD] Created position #{spread_id}: {symbol} {short_strike}/{long_strike} spread "
                    f"({num_contracts}x, ${total_credit:.0f} credit, ${max_risk:.0f} max risk)")

        return spread_id

    def update_spread_value(self, spread_id: int, current_value: float):
        """Update current value and unrealized P&L for a spread"""
        cursor = self.conn.execute("""
            SELECT total_credit, max_profit FROM spread_positions WHERE id = ?
        """, (spread_id,))

        row = cursor.fetchone()
        if not row:
            return

        total_credit, max_profit = row

        # For credit spreads: unrealized P&L = credit received - current value
        unrealized_pnl = (total_credit - current_value) * 100
        unrealized_pnl_pct = (unrealized_pnl / max_profit * 100) if max_profit > 0 else 0

        self.conn.execute("""
            UPDATE spread_positions
            SET current_value = ?, unrealized_pnl = ?, unrealized_pnl_pct = ?, updated_at = ?
            WHERE id = ?
        """, (current_value, unrealized_pnl, unrealized_pnl_pct, datetime.now().isoformat(), spread_id))

        self.conn.commit()

    def close_spread_position(self, spread_id: int, exit_price: float, exit_reason: str):
        """Close a spread position and record P&L"""
        now = datetime.now().isoformat()

        # Get position details
        cursor = self.conn.execute("""
            SELECT symbol, short_strike, long_strike, spread_width, expiration,
                   num_contracts, credit_per_spread, total_credit, entry_date,
                   max_risk, max_profit, entry_dte
            FROM spread_positions WHERE id = ?
        """, (spread_id,))

        row = cursor.fetchone()
        if not row:
            logging.error(f"[SPREAD] Position {spread_id} not found")
            return

        symbol, short_strike, long_strike, spread_width, expiration, \
        num_contracts, credit_per_spread, total_credit, entry_date, \
        max_risk, max_profit, entry_dte = row

        # Calculate P&L
        realized_pnl = (total_credit - exit_price) * 100
        realized_pnl_pct = (realized_pnl / max_profit * 100) if max_profit > 0 else 0
        roi_percent = (realized_pnl / max_risk * 100) if max_risk > 0 else 0

        # Calculate hold days
        entry_dt = datetime.fromisoformat(entry_date)
        hold_days = (datetime.now() - entry_dt).days

        # Determine outcome state
        if realized_pnl > 0:
            state = SpreadState.CLOSED_PROFIT.value
            outcome = "WIN"
        else:
            state = SpreadState.CLOSED_LOSS.value
            outcome = "LOSS"

        # Update position
        self.conn.execute("""
            UPDATE spread_positions
            SET state = ?, exit_date = ?, exit_price = ?,
                realized_pnl = ?, realized_pnl_pct = ?, hold_days = ?,
                exit_reason = ?, updated_at = ?
            WHERE id = ?
        """, (state, now, exit_price, realized_pnl, realized_pnl_pct, hold_days, exit_reason, now, spread_id))

        # Add to history
        self.conn.execute("""
            INSERT INTO spread_history (
                symbol, start_date, end_date,
                short_strike, long_strike, spread_width, expiration,
                num_contracts, credit_received,
                exit_price, realized_pnl, realized_pnl_pct, roi_percent,
                outcome, hold_days, max_risk, max_profit, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, entry_date, now,
            short_strike, long_strike, spread_width, expiration,
            num_contracts, total_credit,
            exit_price, realized_pnl, realized_pnl_pct, roi_percent,
            outcome, hold_days, max_risk, max_profit, exit_reason
        ))

        self.conn.commit()

        # Update symbol performance
        self._update_symbol_performance(symbol, outcome, realized_pnl, roi_percent, hold_days)

        logging.info(f"[SPREAD] Closed position #{spread_id}: {symbol} - {outcome} "
                    f"(P&L: ${realized_pnl:.0f}, {realized_pnl_pct:.1f}%, {hold_days} days)")

    def get_all_positions(self) -> List[Dict]:
        """Get all active spread positions"""
        cursor = self.conn.execute("""
            SELECT * FROM spread_positions WHERE state = ?
        """, (SpreadState.OPEN.value,))

        columns = [desc[0] for desc in cursor.description]
        positions = []

        for row in cursor.fetchall():
            position = dict(zip(columns, row))
            positions.append(position)

        return positions

    def get_position_count(self) -> int:
        """Get number of active spread positions"""
        cursor = self.conn.execute("""
            SELECT COUNT(*) FROM spread_positions WHERE state = ?
        """, (SpreadState.OPEN.value,))
        return cursor.fetchone()[0]

    def reconcile_spreads_from_alpaca(self, trading_client) -> int:
        """
        Reconcile spread positions from Alpaca that aren't in database.

        Scans Alpaca positions for option spreads and creates database entries
        for any spreads not currently tracked.

        Returns:
            Number of spreads imported
        """
        import re
        from collections import defaultdict

        logging.info("[SPREAD_RECONCILE] Starting reconciliation with Alpaca positions...")

        try:
            # Get all positions from Alpaca
            alpaca_positions = trading_client.get_all_positions()

            # Parse option positions by underlying and expiration
            # OCC format: SYMBOL(6)YYMMDD(C/P)STRIKE(8)
            # Example: SOFI260102P00024000 = SOFI 2026-01-02 Put $24.00
            option_positions = defaultdict(list)

            for pos in alpaca_positions:
                symbol = pos.symbol

                # Check if it's an option (contains expiration date pattern)
                # Options format: 6 chars symbol + 6 digit date + C/P + 8 digit strike
                if len(symbol) >= 15:
                    try:
                        # Parse OCC symbol
                        underlying = symbol[:symbol.index('2')]  # Everything before year starts with '2'
                        date_start = symbol.index('2')
                        expiration_str = symbol[date_start:date_start+6]  # YYMMDD
                        option_type = symbol[date_start+6]  # C or P
                        strike_str = symbol[date_start+7:date_start+15]  # 8 digits

                        # Convert to readable format
                        exp_year = '20' + expiration_str[0:2]
                        exp_month = expiration_str[2:4]
                        exp_day = expiration_str[4:6]
                        expiration = f"{exp_year}-{exp_month}-{exp_day}"

                        strike = float(strike_str) / 1000  # Strike in dollars

                        qty = int(pos.qty) if pos.qty else 0

                        # Group by underlying + expiration + type
                        key = (underlying, expiration, option_type)
                        option_positions[key].append({
                            'symbol': symbol,
                            'strike': strike,
                            'qty': qty,
                            'side': 'short' if qty < 0 else 'long'
                        })

                    except (ValueError, IndexError) as e:
                        logging.debug(f"[SPREAD_RECONCILE] Could not parse symbol {symbol}: {e}")
                        continue

            # Identify spreads (short + long put with same underlying/expiration)
            spreads_found = 0
            spreads_imported = 0

            for (underlying, expiration, option_type), positions in option_positions.items():
                if option_type != 'P':  # Only looking for put spreads
                    continue

                # Need exactly 1 short and 1 long position for a spread
                short_puts = [p for p in positions if p['side'] == 'short']
                long_puts = [p for p in positions if p['side'] == 'long']

                # DEBUG: Log what we found
                logging.info(f"[SPREAD_RECONCILE] {underlying} {expiration}: Found {len(short_puts)} short, {len(long_puts)} long")
                if positions:
                    for p in positions:
                        logging.info(f"  - Strike ${p['strike']:.2f} Qty {p['qty']} ({p['side']})")

                if len(short_puts) == 1 and len(long_puts) == 1:
                    spreads_found += 1
                    short = short_puts[0]
                    long = long_puts[0]

                    # Validate it's a bull put spread (short strike > long strike)
                    if short['strike'] <= long['strike']:
                        logging.warning(f"[SPREAD_RECONCILE] {underlying}: Invalid spread - short ${short['strike']:.2f} not > long ${long['strike']:.2f}")
                        continue

                    # Check if already in database
                    cursor = self.conn.execute("""
                        SELECT id FROM spread_positions
                        WHERE symbol = ? AND expiration = ?
                        AND short_strike = ? AND long_strike = ?
                        AND state = ?
                    """, (underlying, expiration, short['strike'], long['strike'], SpreadState.OPEN.value))

                    if cursor.fetchone():
                        logging.debug(f"[SPREAD_RECONCILE] {underlying}: Spread already in database")
                        continue

                    # Import spread to database
                    num_contracts = abs(short['qty'])
                    spread_width = short['strike'] - long['strike']

                    # Estimate credit (we don't have historical data, use current value)
                    # This is a best guess for reconciliation
                    estimated_credit = spread_width * 0.20  # Assume 20% of width as credit

                    logging.info(f"[SPREAD_RECONCILE] Importing {underlying} spread: ${short['strike']:.2f}/${long['strike']:.2f} exp {expiration}")

                    with self.db_lock:
                        spread_id = self.create_spread_position(
                            symbol=underlying,
                            short_strike=short['strike'],
                            long_strike=long['strike'],
                            short_put_symbol=short['symbol'],
                            long_put_symbol=long['symbol'],
                            num_contracts=num_contracts,
                            credit_per_spread=estimated_credit,
                            expiration=expiration,
                            entry_dte=0,  # Unknown
                            notes="Imported from Alpaca reconciliation"
                        )

                    spreads_imported += 1
                    logging.info(f"[SPREAD_RECONCILE] ✓ Imported {underlying} spread (ID: {spread_id})")
                else:
                    # Log why spread wasn't recognized
                    if len(short_puts) == 0 and len(long_puts) == 0:
                        logging.debug(f"[SPREAD_RECONCILE] {underlying} {expiration}: No positions found")
                    elif len(short_puts) != 1 or len(long_puts) != 1:
                        logging.warning(f"[SPREAD_RECONCILE] {underlying} {expiration}: Unexpected position count - {len(short_puts)} short, {len(long_puts)} long (need exactly 1 of each)")

            logging.info(f"[SPREAD_RECONCILE] Complete: Found {spreads_found} spreads, imported {spreads_imported} new")
            return spreads_imported

        except Exception as e:
            logging.error(f"[SPREAD_RECONCILE] Error during reconciliation: {e}", exc_info=True)
            return 0

    def get_symbol_performance(self, symbol: str) -> Optional[Dict]:
        """Get performance stats for a symbol"""
        cursor = self.conn.execute("""
            SELECT * FROM spread_symbol_performance WHERE symbol = ?
        """, (symbol,))

        row = cursor.fetchone()
        if not row:
            return None

        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    def _update_symbol_performance(self, symbol: str, outcome: str, pnl: float, roi: float, hold_days: int):
        """Update performance metrics for a symbol"""
        now = datetime.now().isoformat()

        # Check if symbol exists
        cursor = self.conn.execute("""
            SELECT id, spreads_total, spreads_won, spreads_lost, total_profit,
                   consecutive_losses, max_consecutive_losses, max_drawdown
            FROM spread_symbol_performance WHERE symbol = ?
        """, (symbol,))

        row = cursor.fetchone()

        if row:
            # Update existing
            perf_id, total, won, lost, total_profit, consec_loss, max_consec, max_dd = row

            total += 1
            if outcome == "WIN":
                won += 1
                consec_loss = 0
            else:
                lost += 1
                consec_loss += 1
                max_consec = max(max_consec, consec_loss)

            total_profit += pnl
            win_rate = (won / total * 100) if total > 0 else 0
            avg_profit = total_profit / total if total > 0 else 0

            # Calculate quality score (0-100)
            quality_score = self._calculate_quality_score(win_rate, avg_profit, max_consec)

            self.conn.execute("""
                UPDATE spread_symbol_performance
                SET spreads_total = ?, spreads_won = ?, spreads_lost = ?,
                    win_rate = ?, total_profit = ?, avg_profit_per_spread = ?,
                    consecutive_losses = ?, max_consecutive_losses = ?,
                    last_trade_date = ?, updated_at = ?, quality_score = ?
                WHERE symbol = ?
            """, (total, won, lost, win_rate, total_profit, avg_profit,
                  consec_loss, max_consec, now, now, quality_score, symbol))
        else:
            # Create new
            won = 1 if outcome == "WIN" else 0
            lost = 1 if outcome == "LOSS" else 0
            win_rate = 100 if outcome == "WIN" else 0
            consec_loss = 0 if outcome == "WIN" else 1
            quality_score = self._calculate_quality_score(win_rate, pnl, consec_loss)

            self.conn.execute("""
                INSERT INTO spread_symbol_performance (
                    symbol, spreads_total, spreads_won, spreads_lost, win_rate,
                    total_profit, avg_profit_per_spread, consecutive_losses,
                    max_consecutive_losses, last_trade_date, updated_at, quality_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, 1, won, lost, win_rate, pnl, pnl, consec_loss, consec_loss, now, now, quality_score))

        self.conn.commit()

    def _calculate_quality_score(self, win_rate: float, avg_profit: float, max_consec_losses: int) -> float:
        """Calculate quality score (0-100) for a symbol"""
        # Win rate component (0-50 points)
        win_rate_score = (win_rate / 100) * 50

        # Profit component (0-30 points) - normalized to $200 avg profit = 30 points
        profit_score = min((avg_profit / 200) * 30, 30)

        # Consistency component (0-20 points) - penalize consecutive losses
        consistency_score = max(20 - (max_consec_losses * 5), 0)

        return win_rate_score + profit_score + consistency_score

    def reconcile_with_broker(self, broker_positions: List) -> Dict:
        """Remove spread positions from database that no longer exist in broker"""
        db_positions = self.get_all_positions()
        db_symbols = {pos['symbol'] for pos in db_positions}
        broker_symbols = {self._extract_underlying(pos.symbol) for pos in broker_positions}

        removed = 0
        for pos in db_positions:
            if pos['symbol'] not in broker_symbols:
                # Position closed at broker but still in database
                self.conn.execute("""
                    DELETE FROM spread_positions WHERE id = ?
                """, (pos['id'],))
                removed += 1
                logging.info(f"[SPREAD] Removed stale position: {pos['symbol']}")

        self.conn.commit()
        return {'removed': removed, 'checked': len(db_positions)}

    def _extract_underlying(self, full_symbol: str) -> str:
        """Extract underlying symbol from OCC format"""
        if len(full_symbol) > 15:
            return full_symbol[:-15]
        return full_symbol
