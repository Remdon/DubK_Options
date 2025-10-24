"""
Trade journal component for tracking all trades and performance metrics.
Handles SQLite database operations for trade history, position tracking, and analytics.
"""
import sqlite3
import datetime
from typing import List, Dict, Optional
from config import config

class TradeJournal:
    """SQLite database for trade history and performance tracking"""

    def __init__(self, db_path='trades.db'):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        """Create database tables if they don't exist"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                occ_symbol TEXT,
                action TEXT,
                entry_price REAL,
                quantity INTEGER,
                total_cost REAL,
                confidence INTEGER,
                iv_rank REAL,
                delta REAL,
                theta REAL,
                vega REAL,
                gamma REAL,
                bid_ask_spread REAL,
                reason TEXT,
                status TEXT DEFAULT 'OPEN'
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS exits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                timestamp TEXT NOT NULL,
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL,
                pnl_pct REAL,
                hold_time_hours REAL,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                error_type TEXT,
                message TEXT,
                symbol TEXT,
                traceback TEXT
            )
        """)

        # Active positions tracking for Grok strategy analysis
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS active_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                occ_symbol TEXT,
                strategy TEXT NOT NULL,
                entry_timestamp TEXT NOT NULL,
                entry_price REAL,
                quantity INTEGER,
                confidence INTEGER,
                strikes TEXT,
                expiry TEXT,
                reason TEXT,
                grok_notes TEXT,
                UNIQUE(symbol, occ_symbol)
            )
        """)

        # Grok confidence calibration tracking
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS grok_calibration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                grok_confidence INTEGER NOT NULL,
                actual_outcome TEXT,
                pnl_pct REAL,
                hold_time_hours REAL,
                was_profitable INTEGER,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)

        self.conn.commit()

    def log_trade(self, trade_data: Dict) -> int:
        """Log trade entry to database"""
        cursor = self.conn.execute("""
            INSERT INTO trades (
                timestamp, symbol, strategy, occ_symbol, action, entry_price,
                quantity, total_cost, confidence, iv_rank, delta, theta, vega, gamma,
                bid_ask_spread, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.datetime.now().isoformat(),
            trade_data.get('symbol'),
            trade_data.get('strategy'),
            trade_data.get('occ_symbol'),
            trade_data.get('action'),
            trade_data.get('entry_price'),
            trade_data.get('quantity'),
            trade_data.get('total_cost'),
            trade_data.get('confidence'),
            trade_data.get('iv_rank'),
            trade_data.get('delta'),
            trade_data.get('theta'),
            trade_data.get('vega'),
            trade_data.get('gamma'),
            trade_data.get('bid_ask_spread'),
            trade_data.get('reason')
        ))
        self.conn.commit()
        return cursor.lastrowid

    def log_exit(self, trade_id: int, exit_data: Dict):
        """Log trade exit to database"""
        self.conn.execute("""
            INSERT INTO exits (
                trade_id, timestamp, exit_price, exit_reason, pnl, pnl_pct, hold_time_hours
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id,
            datetime.datetime.now().isoformat(),
            exit_data.get('exit_price'),
            exit_data.get('exit_reason'),
            exit_data.get('pnl'),
            exit_data.get('pnl_pct'),
            exit_data.get('hold_time_hours')
        ))

        # Update trade status
        self.conn.execute("UPDATE trades SET status = 'CLOSED' WHERE id = ?", (trade_id,))
        self.conn.commit()

    def track_active_position(self, position_data: Dict):
        """Track an active position with its strategy for Grok monitoring"""
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO active_positions (
                    symbol, occ_symbol, strategy, entry_timestamp, entry_price,
                    quantity, confidence, strikes, expiry, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position_data.get('symbol'),
                position_data.get('occ_symbol'),
                position_data.get('strategy'),
                datetime.datetime.now().isoformat(),
                position_data.get('entry_price'),
                position_data.get('quantity'),
                position_data.get('confidence'),
                position_data.get('strikes'),
                position_data.get('expiry'),
                position_data.get('reason')
            ))
            self.conn.commit()
        except Exception as e:
            print(f"[ERROR] Failed to track active position: {e}")

    def get_position_strategy(self, symbol: str) -> Optional[Dict]:
        """Get strategy info for an active position"""
        # Try multiple ways to match the symbol
        queries = [
            # Direct match
            ("SELECT strategy, entry_timestamp, confidence, strikes, expiry, reason, grok_notes FROM active_positions WHERE symbol = ? ORDER BY entry_timestamp DESC LIMIT 1", (symbol,)),
            # OCC symbol match (for offers)
            ("SELECT strategy, entry_timestamp, confidence, strikes, expiry, reason, grok_notes FROM active_positions WHERE occ_symbol = ? ORDER BY entry_timestamp DESC LIMIT 1", (symbol,)),
            # Like match for symbol (handles both underlying and OCC)
            ("SELECT strategy, entry_timestamp, confidence, strikes, expiry, reason, grok_notes FROM active_positions WHERE symbol = ? OR occ_symbol LIKE ? ORDER BY entry_timestamp DESC LIMIT 1", (symbol, f"{symbol}%")),
        ]

        for query, params in queries:
            try:
                cursor = self.conn.execute(query, params)
                row = cursor.fetchone()
                if row:
                    return {
                        'strategy': row[0],
                        'entry_timestamp': row[1],
                        'confidence': row[2],
                        'strikes': row[3],
                        'expiry': row[4],
                        'reason': row[5],
                        'grok_notes': row[6]
                    }
            except Exception:
                continue

        return None

    def remove_active_position(self, symbol: str):
        """Remove position from active tracking when closed"""
        try:
            self.conn.execute("""
                DELETE FROM active_positions
                WHERE symbol = ? OR occ_symbol LIKE ?
            """, (symbol, f"{symbol}%"))
            self.conn.commit()
        except Exception as e:
            print(f"[ERROR] Failed to remove active position: {e}")

    def update_grok_notes(self, symbol: str, notes: str):
        """Update Grok's notes about a position"""
        try:
            self.conn.execute("""
                UPDATE active_positions
                SET grok_notes = ?
                WHERE symbol = ? OR occ_symbol LIKE ?
            """, (notes, symbol, f"{symbol}%"))
            self.conn.commit()
        except Exception as e:
            print(f"[ERROR] Failed to update Grok notes: {e}")

    def log_error(self, error_type: str, message: str, symbol: str = None, traceback_str: str = None):
        """Log error to database"""
        self.conn.execute("""
            INSERT INTO errors (timestamp, error_type, message, symbol, traceback)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.datetime.now().isoformat(), error_type, message, symbol, traceback_str))
        self.conn.commit()

    def get_open_trades(self) -> List[Dict]:
        """Get all open trades"""
        cursor = self.conn.execute("SELECT * FROM trades WHERE status = 'OPEN'")
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_performance_stats(self, days=30) -> Dict:
        """Calculate performance statistics"""
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()

        cursor = self.conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                AVG(pnl_pct) as avg_return,
                SUM(pnl) as total_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses
            FROM exits
            WHERE timestamp > ?
        """, (since,))

        result = cursor.fetchone()
        if result and result[0] > 0:
            return {
                'total_trades': result[0],
                'avg_return': result[1],
                'total_pnl': result[2],
                'wins': result[3],
                'losses': result[4],
                'win_rate': result[3] / result[0] if result[0] > 0 else 0
            }
        return {'total_trades': 0}

    def log_grok_calibration(self, trade_id: int, symbol: str, strategy: str,
                            grok_confidence: int, pnl_pct: float, hold_time_hours: float):
        """Log Grok confidence vs actual outcome for calibration"""
        was_profitable = 1 if pnl_pct > 0 else 0

        # Determine outcome category
        if pnl_pct >= 0.20:
            outcome = "BIG_WIN"
        elif pnl_pct > 0:
            outcome = "SMALL_WIN"
        elif pnl_pct > -0.20:
            outcome = "SMALL_LOSS"
        else:
            outcome = "BIG_LOSS"

        try:
            self.conn.execute("""
                INSERT INTO grok_calibration (
                    trade_id, timestamp, symbol, strategy, grok_confidence,
                    actual_outcome, pnl_pct, hold_time_hours, was_profitable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, datetime.datetime.now().isoformat(), symbol, strategy, grok_confidence,
                outcome, pnl_pct, hold_time_hours, was_profitable
            ))
            self.conn.commit()
        except Exception as e:
            print(f"[ERROR] Failed to log Grok calibration: {e}")

    def get_grok_calibration_stats(self, min_samples: int = 20) -> Dict:
        """Get Grok confidence calibration statistics"""
        try:
            # Get win rates by confidence bucket
            cursor = self.conn.execute("""
                SELECT
                    CASE
                        WHEN grok_confidence >= 95 THEN '95+'
                        WHEN grok_confidence >= 90 THEN '90-94'
                        WHEN grok_confidence >= 85 THEN '85-89'
                        WHEN grok_confidence >= 80 THEN '80-84'
                        WHEN grok_confidence >= 75 THEN '75-79'
                        ELSE '<75'
                    END as confidence_bucket,
                    COUNT(*) as total_trades,
                    SUM(was_profitable) as wins,
                    AVG(pnl_pct) as avg_pnl_pct,
                    AVG(grok_confidence) as avg_confidence
                FROM grok_calibration
                GROUP BY confidence_bucket
                ORDER BY avg_confidence DESC
            """)

            results = {}
            for row in cursor.fetchall():
                bucket, total, wins, avg_pnl, avg_conf = row
                if total >= min_samples:  # Only include buckets with enough data
                    win_rate = (wins / total) if total > 0 else 0
                    results[bucket] = {
                        'total_trades': total,
                        'win_rate': win_rate,
                        'avg_pnl_pct': avg_pnl,
                        'avg_confidence': avg_conf,
                        'calibration_error': abs(avg_conf/100 - win_rate)  # How far off is Grok?
                    }

            return results

        except Exception as e:
            print(f"[ERROR] Failed to get calibration stats: {e}")
            return {}

    def print_grok_calibration_report(self):
        """Print Grok confidence calibration report"""
        stats = self.get_grok_calibration_stats(min_samples=10)

        if not stats:
            print("Not enough calibration data yet (need 10+ trades per confidence bucket)")
            return

        print("\n" + "="*80)
        print("GROK CONFIDENCE CALIBRATION REPORT")
        print("="*80)
        print(f"{'Bucket':<10} {'Trades':<8} {'Grok Conf':<12} {'Actual Win%':<14} {'Avg P&L':<12} {'Error':<10}")
        print("-"*80)

        for bucket, data in sorted(stats.items(), key=lambda x: x[1]['avg_confidence'], reverse=True):
            print(
                f"{bucket:<10} {data['total_trades']:<8} "
                f"{data['avg_confidence']:>6.1f}% {data['win_rate']*100:>10.1f}% "
                f"{data['avg_pnl_pct']*100:>8.1f}% {data['calibration_error']*100:>8.1f}%"
            )

        print("="*80)
        print("Note: 'Error' shows how far Grok's confidence is from actual win rate")
        print("      Example: If Grok says 90% but actual win rate is 75%, error = 15%")
        print("="*80 + "\n")
