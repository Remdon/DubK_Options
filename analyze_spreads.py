"""
Analyze Bull Put Spread Strategy Performance
"""

import sqlite3
from datetime import datetime

db_path = 'spreads.db'
conn = sqlite3.connect(db_path)

print("="*80)
print("BULL PUT SPREAD STRATEGY - PERFORMANCE ANALYSIS")
print("="*80)

# Active Positions
print("\n### ACTIVE SPREAD POSITIONS ###\n")
cursor = conn.execute("""
    SELECT id, symbol, short_strike, long_strike, spread_width,
           num_contracts, total_credit, max_risk, max_profit,
           unrealized_pnl, unrealized_pnl_pct, expiration, entry_date,
           short_put_symbol, long_put_symbol
    FROM spread_positions
    WHERE state = 'OPEN'
    ORDER BY symbol
""")

active_positions = cursor.fetchall()
if active_positions:
    for pos in active_positions:
        id, symbol, short_strike, long_strike, spread_width, num_contracts, \
        total_credit, max_risk, max_profit, unrealized_pnl, unrealized_pnl_pct, \
        expiration, entry_date, short_symbol, long_symbol = pos

        print(f"[{id}] {symbol}")
        print(f"  Strikes: ${short_strike:.0f}/${long_strike:.0f} (${spread_width:.0f} wide)")
        print(f"  Contracts: {num_contracts}")
        print(f"  Credit: ${total_credit:.2f} | Max Risk: ${max_risk:.0f} | Max Profit: ${max_profit:.0f}")
        print(f"  Unrealized P&L: ${unrealized_pnl:.2f} ({unrealized_pnl_pct:.1f}%)")
        print(f"  Expiration: {expiration}")
        print(f"  Short: {short_symbol}")
        print(f"  Long: {long_symbol}")
        print(f"  Entry: {entry_date[:10]}")
        print()
else:
    print("No active spread positions found in database.")

# Performance History
print("\n### SPREAD PERFORMANCE HISTORY ###\n")
cursor = conn.execute("""
    SELECT symbol, start_date, end_date, short_strike, long_strike,
           num_contracts, credit_received, realized_pnl, realized_pnl_pct,
           outcome, hold_days
    FROM spread_history
    ORDER BY end_date DESC
    LIMIT 20
""")

history = cursor.fetchall()
if history:
    total_trades = len(history)
    wins = sum(1 for h in history if h[9] == 'WIN')
    losses = sum(1 for h in history if h[9] == 'LOSS')
    total_pnl = sum(h[7] for h in history)

    print(f"Total Trades: {total_trades}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {wins/total_trades*100:.1f}%")
    print(f"Total P&L: ${total_pnl:.2f}")
    print(f"Avg P&L per Trade: ${total_pnl/total_trades:.2f}")
    print()

    print("Recent Trades:")
    for h in history[:10]:
        symbol, start, end, short_strike, long_strike, contracts, credit, pnl, pnl_pct, outcome, days = h
        print(f"  {symbol} ${short_strike:.0f}/${long_strike:.0f} - {outcome}: ${pnl:.2f} ({pnl_pct:.1f}%) in {days} days")
else:
    print("No closed spread trades found.")

# Symbol Performance
print("\n### PERFORMANCE BY SYMBOL ###\n")
cursor = conn.execute("""
    SELECT symbol, spreads_total, spreads_won, spreads_lost, win_rate,
           total_profit, avg_profit_per_spread, quality_score
    FROM spread_symbol_performance
    ORDER BY quality_score DESC
""")

symbol_perf = cursor.fetchall()
if symbol_perf:
    for sp in symbol_perf:
        symbol, total, won, lost, win_rate, total_profit, avg_profit, quality = sp
        print(f"{symbol}: {won}W/{lost}L ({win_rate:.1f}% WR) | "
              f"Total: ${total_profit:.2f} | Avg: ${avg_profit:.2f} | "
              f"Quality: {quality:.1f}/100")
else:
    print("No symbol performance data available.")

# Summary Statistics
print("\n### STRATEGY HEALTH METRICS ###\n")

total_active = len(active_positions)
total_unrealized = sum(pos[9] for pos in active_positions) if active_positions else 0

print(f"Active Positions: {total_active}")
print(f"Total Unrealized P&L: ${total_unrealized:.2f}")

if history:
    print(f"Historical Win Rate: {wins/total_trades*100:.1f}%")
    print(f"Historical Avg P&L: ${total_pnl/total_trades:.2f} per trade")

# Check for INTC issue
print("\n### INTC POSITION ANALYSIS ###\n")
cursor = conn.execute("""
    SELECT * FROM spread_positions
    WHERE symbol = 'INTC' AND state = 'OPEN'
""")
intc = cursor.fetchone()
if intc:
    print("INTC spread found in database:")
    print(f"  ID: {intc[0]}")
    print(f"  Short Strike: ${intc[7]}")
    print(f"  Long Strike: ${intc[8]}")
    print(f"  Short Symbol: {intc[12]}")
    print(f"  Long Symbol: {intc[13]}")
else:
    print("WARNING: INTC has 3 legs in Alpaca but not tracked in database!")
    print("This suggests reconciliation missed it or it's a malformed spread.")

conn.close()

print("\n" + "="*80)
print("ANALYSIS COMPLETE")
print("="*80)
