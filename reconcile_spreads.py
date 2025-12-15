"""
Initialize spread database and reconcile existing Alpaca positions
"""

import os
import sys
from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, 'src')

load_dotenv()

from strategies.spread_manager import SpreadManager
from alpaca.trading.client import TradingClient

# Initialize spread manager (creates tables)
print("Initializing Spread Manager database...")
spread_manager = SpreadManager(db_path='spreads.db')

# Connect to Alpaca spread trading account
print("Connecting to Alpaca spread trading account...")
spread_api_key = os.getenv('ALPACA_BULL_PUT_KEY')
spread_secret_key = os.getenv('ALPACA_BULL_PUT_SECRET_KEY')
alpaca_mode = os.getenv('ALPACA_MODE', 'paper')

if not spread_api_key or not spread_secret_key:
    print("ERROR: ALPACA_BULL_PUT_KEY or ALPACA_BULL_PUT_SECRET_KEY not set")
    sys.exit(1)

paper = alpaca_mode.lower() == 'paper'
trading_client = TradingClient(spread_api_key, spread_secret_key, paper=paper)

print(f"Connected to Alpaca ({alpaca_mode} mode)")

# Run reconciliation
print("\nReconciling spreads from Alpaca...")
imported_count = spread_manager.reconcile_spreads_from_alpaca(trading_client)

print(f"\n{'='*80}")
print(f"RECONCILIATION COMPLETE")
print(f"{'='*80}")
print(f"Imported {imported_count} spread(s) from Alpaca")

# Show current positions
positions = spread_manager.get_all_positions()
print(f"\nActive spread positions in database: {len(positions)}")
for pos in positions:
    print(f"  [{pos['id']}] {pos['symbol']} ${pos['short_strike']:.0f}/${pos['long_strike']:.0f} - Exp: {pos['expiration']}")
