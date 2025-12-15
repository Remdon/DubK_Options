#!/usr/bin/env python3
"""
Diagnostic Script for Bull Put Spread Strategy
Run this on EC2 via SSH to analyze spread positions and performance
"""

import os
import sys
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, 'src')

load_dotenv()

def analyze_spreads():
    """Analyze spread database and compare with Alpaca positions"""

    print("="*80)
    print("BULL PUT SPREAD DIAGNOSTIC REPORT")
    print("="*80)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Check database exists
    db_path = 'spreads.db'
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)

    # 1. Active Spread Positions
    print("### 1. ACTIVE SPREAD POSITIONS IN DATABASE ###")
    print()

    cursor = conn.execute("""
        SELECT id, symbol, short_strike, long_strike, spread_width,
               num_contracts, total_credit, max_risk, max_profit,
               unrealized_pnl, unrealized_pnl_pct, expiration,
               short_put_symbol, long_put_symbol, entry_date, notes
        FROM spread_positions
        WHERE state = 'OPEN'
        ORDER BY symbol
    """)

    active = cursor.fetchall()
    if active:
        print(f"Found {len(active)} active spread(s):")
        print()
        for pos in active:
            (id, symbol, short_strike, long_strike, spread_width, num_contracts,
             total_credit, max_risk, max_profit, unrealized_pnl, unrealized_pnl_pct,
             expiration, short_symbol, long_symbol, entry_date, notes) = pos

            print(f"[{id}] {symbol} - ${short_strike:.0f}/${long_strike:.0f} Spread")
            print(f"    Contracts: {num_contracts} x ${spread_width:.0f} wide")
            print(f"    Credit: ${total_credit:.2f} | Max Risk: ${max_risk:.0f} | Max Profit: ${max_profit:.0f}")
            print(f"    Current P&L: ${unrealized_pnl:.2f} ({unrealized_pnl_pct:.1f}%)")
            print(f"    Expiration: {expiration}")
            print(f"    Entry: {entry_date[:10]}")
            print(f"    Short Leg: {short_symbol}")
            print(f"    Long Leg: {long_symbol}")
            if notes:
                print(f"    Notes: {notes}")

            # Calculate stop loss threshold
            stop_loss_threshold = max_profit * -0.75 if max_profit > 0 else 0
            distance_to_stop = unrealized_pnl - stop_loss_threshold
            print(f"    Stop Loss: ${stop_loss_threshold:.2f} (${distance_to_stop:.2f} away)")
            print()
    else:
        print("No active spreads in database.")
        print()

    # 2. Get Alpaca Positions
    print("### 2. ALPACA POSITIONS (SPREAD ACCOUNT) ###")
    print()

    try:
        from alpaca.trading.client import TradingClient

        spread_api_key = os.getenv('ALPACA_BULL_PUT_KEY')
        spread_secret_key = os.getenv('ALPACA_BULL_PUT_SECRET_KEY')
        alpaca_mode = os.getenv('ALPACA_MODE', 'paper')

        if spread_api_key and spread_secret_key:
            paper = alpaca_mode.lower() == 'paper'
            client = TradingClient(spread_api_key, spread_secret_key, paper=paper)

            positions = client.get_all_positions()

            # Parse option positions
            from collections import defaultdict
            option_positions = defaultdict(list)

            for pos in positions:
                symbol = pos.symbol
                if len(symbol) >= 15:  # Option format
                    try:
                        # Parse OCC symbol
                        underlying = symbol[:symbol.index('2')]
                        date_start = symbol.index('2')
                        expiration_str = symbol[date_start:date_start+6]
                        option_type = symbol[date_start+6]
                        strike_str = symbol[date_start+7:date_start+15]

                        exp_year = '20' + expiration_str[0:2]
                        exp_month = expiration_str[2:4]
                        exp_day = expiration_str[4:6]
                        expiration = f"{exp_year}-{exp_month}-{exp_day}"

                        strike = float(strike_str) / 1000
                        qty = int(pos.qty) if pos.qty else 0
                        side = 'SHORT' if qty < 0 else 'LONG'

                        market_value = float(pos.market_value) if pos.market_value else 0
                        unrealized_pl = float(pos.unrealized_pl) if pos.unrealized_pl else 0

                        option_positions[underlying].append({
                            'symbol': symbol,
                            'strike': strike,
                            'qty': qty,
                            'side': side,
                            'expiration': expiration,
                            'type': option_type,
                            'market_value': market_value,
                            'unrealized_pl': unrealized_pl
                        })
                    except Exception as e:
                        print(f"Could not parse {symbol}: {e}")

            # Display positions by underlying
            if option_positions:
                print(f"Found {len(option_positions)} underlying(s) with option positions:")
                print()

                for underlying, positions in sorted(option_positions.items()):
                    print(f"{underlying}:")
                    total_pl = sum(p['unrealized_pl'] for p in positions)

                    # Group by expiration
                    by_exp = defaultdict(list)
                    for p in positions:
                        by_exp[p['expiration']].append(p)

                    for exp, legs in sorted(by_exp.items()):
                        print(f"  Expiration: {exp}")
                        short_legs = [l for l in legs if l['side'] == 'SHORT']
                        long_legs = [l for l in legs if l['side'] == 'LONG']

                        print(f"    {len(short_legs)} SHORT leg(s), {len(long_legs)} LONG leg(s)")

                        for leg in sorted(legs, key=lambda x: x['strike'], reverse=True):
                            print(f"      {leg['side']:5} ${leg['strike']:6.2f} {leg['type']} | "
                                  f"Qty: {leg['qty']:3} | Value: ${leg['market_value']:7.2f} | "
                                  f"P&L: ${leg['unrealized_pl']:+7.2f}")

                        # Validate spread structure
                        if len(short_legs) == 1 and len(long_legs) == 1:
                            print(f"      ✓ Valid 2-leg spread")
                        elif len(short_legs) == 1 and len(long_legs) > 1:
                            print(f"      ✗ ANOMALY: {len(long_legs)} long legs (expected 1)")
                        elif len(short_legs) != 1 or len(long_legs) != 1:
                            print(f"      ⚠ Unexpected structure")
                        print()

                    print(f"  Total P&L for {underlying}: ${total_pl:+.2f}")
                    print()
            else:
                print("No option positions found in Alpaca.")
                print()
        else:
            print("Alpaca spread credentials not configured.")
            print()

    except Exception as e:
        print(f"Error fetching Alpaca positions: {e}")
        print()

    # 3. Historical Performance
    print("### 3. HISTORICAL PERFORMANCE ###")
    print()

    cursor = conn.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
               AVG(realized_pnl) as avg_pnl,
               SUM(realized_pnl) as total_pnl,
               AVG(hold_days) as avg_days
        FROM spread_history
    """)

    stats = cursor.fetchone()
    if stats and stats[0] > 0:
        total, wins, losses, avg_pnl, total_pnl, avg_days = stats
        win_rate = (wins / total * 100) if total > 0 else 0

        print(f"Total Closed Trades: {total}")
        print(f"Wins: {wins} | Losses: {losses}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Average P&L: ${avg_pnl:.2f} per trade")
        print(f"Total P&L: ${total_pnl:.2f}")
        print(f"Average Hold Time: {avg_days:.1f} days")
        print()

        # Recent trades
        cursor = conn.execute("""
            SELECT symbol, short_strike, long_strike, credit_received,
                   realized_pnl, outcome, hold_days, end_date
            FROM spread_history
            ORDER BY end_date DESC
            LIMIT 10
        """)

        recent = cursor.fetchall()
        if recent:
            print("Recent Trades:")
            for trade in recent:
                symbol, short, long, credit, pnl, outcome, days, end_date = trade
                print(f"  {end_date[:10]} | {symbol:6} ${short:.0f}/${long:.0f} | "
                      f"{outcome:4} | ${pnl:+7.2f} in {days} days")
            print()
    else:
        print("No historical trades found.")
        print()

    # 4. Symbol Performance
    print("### 4. PERFORMANCE BY SYMBOL ###")
    print()

    cursor = conn.execute("""
        SELECT symbol, spreads_total, spreads_won, spreads_lost,
               win_rate, total_profit, avg_profit_per_spread, quality_score
        FROM spread_symbol_performance
        ORDER BY quality_score DESC
    """)

    symbol_perf = cursor.fetchall()
    if symbol_perf:
        print(f"{'Symbol':<8} {'Trades':<8} {'W/L':<10} {'Win%':<8} {'Total $':<12} {'Avg $':<10} {'Quality':<8}")
        print("-" * 80)
        for sp in symbol_perf:
            symbol, total, won, lost, win_rate, total_profit, avg_profit, quality = sp
            print(f"{symbol:<8} {total:<8} {won}/{lost:<8} {win_rate:>6.1f}% "
                  f"${total_profit:>10.2f}  ${avg_profit:>8.2f}  {quality:>6.1f}/100")
        print()
    else:
        print("No symbol performance data available.")
        print()

    # 5. Recommendations
    print("### 5. DIAGNOSTIC SUMMARY & RECOMMENDATIONS ###")
    print()

    # Check for anomalies
    issues = []

    # Check for 3+ leg positions
    if option_positions:
        for underlying, positions in option_positions.items():
            by_exp = defaultdict(list)
            for p in positions:
                by_exp[p['expiration']].append(p)

            for exp, legs in by_exp.items():
                short_legs = [l for l in legs if l['side'] == 'SHORT']
                long_legs = [l for l in legs if l['side'] == 'LONG']

                if len(short_legs) == 1 and len(long_legs) > 1:
                    issues.append(f"❌ {underlying} {exp}: Has {len(long_legs)} long legs (expected 1)")
                    issues.append(f"   Action: Close extra long put to restore proper spread structure")

    # Check database vs Alpaca sync
    db_symbols = {pos[1] for pos in active}  # pos[1] is symbol
    alpaca_symbols = set(option_positions.keys()) if option_positions else set()

    missing_in_db = alpaca_symbols - db_symbols
    missing_in_alpaca = db_symbols - alpaca_symbols

    if missing_in_db:
        issues.append(f"⚠️  Spreads in Alpaca but NOT in database: {', '.join(missing_in_db)}")
        issues.append(f"   Action: Run reconciliation to import missing spreads")

    if missing_in_alpaca:
        issues.append(f"⚠️  Spreads in database but NOT in Alpaca: {', '.join(missing_in_alpaca)}")
        issues.append(f"   Action: Close stale database entries")

    if issues:
        print("Issues Found:")
        for issue in issues:
            print(f"  {issue}")
        print()
    else:
        print("✓ No critical issues detected")
        print()

    # Overall health
    if active:
        total_unrealized = sum(pos[9] for pos in active)  # pos[9] is unrealized_pnl
        avg_unrealized = total_unrealized / len(active)

        print(f"Portfolio Health:")
        print(f"  Active Spreads: {len(active)}")
        print(f"  Total Unrealized P&L: ${total_unrealized:.2f}")
        print(f"  Average P&L per Spread: ${avg_unrealized:.2f}")

        if stats and stats[0] > 0:
            print(f"  Historical Win Rate: {win_rate:.1f}%")
            print(f"  Historical Total P&L: ${total_pnl:.2f}")

    conn.close()

    print()
    print("="*80)
    print("DIAGNOSTIC COMPLETE")
    print("="*80)

if __name__ == "__main__":
    analyze_spreads()
