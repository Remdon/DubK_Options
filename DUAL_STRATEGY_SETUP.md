# Dual-Strategy Bot: Wheel + Bull Put Spreads

## Overview

Your bot now runs TWO independent strategies on TWO separate Alpaca paper accounts:

1. **Wheel Strategy** - Main account (`ALPACA_API_KEY`)
   - Capital requirement: $50K-$100K optimal
   - Win rate: 50-95%
   - Returns: 15-40% annually
   - Trades on main Alpaca account
   - Database: `trades.db`

2. **Bull Put Spread Strategy** - Secondary account (`ALPACA_BULL_PUT_KEY`)
   - Capital requirement: $10K-$25K optimal
   - Win rate: 65-75%
   - Returns: 15-30% annually
   - Trades on separate Alpaca account
   - Database: `spreads.db`

**Both strategies run in parallel with independent P&L tracking.**

---

## Setup Instructions

### 1. Create Second Alpaca Paper Account

1. Go to https://alpaca.markets/
2. Create a new paper trading account (you can have multiple)
3. Get the API keys for this new account

### 2. Add Keys to .env File

Edit your `.env` file and add the new keys:

```bash
# Existing Wheel Strategy Account
ALPACA_API_KEY=your_existing_key_here
ALPACA_SECRET_KEY=your_existing_secret_here
ALPACA_MODE=paper

# NEW: Bull Put Spread Strategy Account
ALPACA_BULL_PUT_KEY=your_new_key_here
ALPACA_BULL_PUT_SECRET_KEY=your_new_secret_here
```

**Important**: Keep both accounts in paper mode until both strategies are validated.

### 3. Verify Configuration

The bot will automatically detect the second account on startup. You'll see:

```
[WHEEL] The Wheel Strategy initialized - 50-95% win rate expected
[SPREAD] Connected to spread account - Portfolio: $10,000.00
[SPREAD] Bull Put Spread Strategy initialized - 65-75% win rate expected
```

If you don't add the `ALPACA_BULL_PUT_KEY`, only the Wheel strategy will run.

---

## How It Works

### Separate Execution
- **Every 30 minutes** during market hours:
  1. Bot scans for Wheel opportunities (main account)
  2. Bot scans for Spread opportunities (secondary account)
  3. Both execute independently

### Separate Databases
- **Wheel positions**: Stored in `trades.db`
- **Spread positions**: Stored in `spreads.db`
- **Independent P&L tracking** for each strategy

### Separate Risk Management
- Wheel: Max 7 positions, 14% capital each
- Spreads: Max 15 positions, 10% capital each
- No interaction between strategies

---

## Configuration Parameters

### Bull Put Spread Settings

Located in `config/default_config.py`:

```python
# Stock filters
SPREAD_MIN_STOCK_PRICE = 20.00          # Can trade $20-300 stocks
SPREAD_MAX_STOCK_PRICE = 300.00         # Higher than Wheel (defined risk)
SPREAD_MIN_MARKET_CAP = 2000000000      # $2B minimum

# Spread construction
SPREAD_WIDTH = 5.00                      # $5 wide spreads
SPREAD_MIN_CREDIT = 1.00                 # Minimum $1.00 credit
SPREAD_MAX_CAPITAL_PER_SPREAD = 500      # Max $500 risk per spread
SPREAD_SHORT_STRIKE_DELTA = -0.30        # 25-35% OTM

# Position limits
MAX_SPREAD_POSITIONS = 15                # Max 15 spreads
MAX_CAPITAL_PER_SPREAD_POSITION = 0.10   # 10% per spread

# Timing
SPREAD_TARGET_DTE = 35                   # 30-45 days optimal
SPREAD_MIN_DTE = 21
SPREAD_MAX_DTE = 60

# Exit rules
SPREAD_PROFIT_TARGET_PCT = 0.50          # Close at 50% profit
SPREAD_STOP_LOSS_PCT = -1.00             # Stop at -100% (max loss)
```

You can override these in your `.env` file.

---

## Expected Performance

### With $96K Wheel + $10K Spreads

**Wheel Account ($96K)**:
- 5-7 active positions
- $800-1,300/month premium
- ~15-30% annual return
- Database: `trades.db`

**Spread Account ($10K)**:
- 10-15 active spreads
- $300-500/month income
- ~18-30% annual return
- Database: `spreads.db`

**Combined**:
- Total monthly income: $1,100-1,800
- Diversification across 2 strategies
- Independent risk management

---

## Database Structure

### Wheel Database (trades.db)

Tables:
- `wheel_positions` - Active wheel positions
- `wheel_transactions` - Transaction history
- `wheel_history` - Completed cycles
- `symbol_performance` - Win rate tracking

### Spread Database (spreads.db)

Tables:
- `spread_positions` - Active spreads
- `spread_history` - Closed spreads
- `spread_symbol_performance` - Win rate tracking

**Query Example**:
```sql
-- Wheel positions
SELECT * FROM wheel_positions WHERE state = 'SELLING_PUTS';

-- Spread positions
SELECT * FROM spread_positions WHERE state = 'OPEN';
```

---

## Manual Triggers

Use the interactive UI to manually trigger scans:

- Press `s` - Triggers BOTH Wheel AND Spread scans
- Press `p` - Evaluates BOTH portfolios
- Press `h` - Shows status of main account (Wheel only)

---

## Monitoring Performance

### Wheel Strategy
```bash
# Check active wheel positions
sqlite3 trades.db "SELECT symbol, state, total_premium_collected FROM wheel_positions;"

# Check wheel performance
sqlite3 trades.db "SELECT symbol, win_rate, total_profit FROM symbol_performance ORDER BY win_rate DESC;"
```

### Spread Strategy
```bash
# Check active spreads
sqlite3 spreads.db "SELECT symbol, short_strike, long_strike, unrealized_pnl FROM spread_positions WHERE state = 'OPEN';"

# Check spread performance
sqlite3 spreads.db "SELECT symbol, win_rate, total_profit FROM spread_symbol_performance ORDER BY win_rate DESC;"
```

---

## Deployment to EC2

When deploying to EC2, make sure:

1. **Update .env on server** with both sets of keys:
```bash
nano ~/DubK_Options/.env
# Add ALPACA_BULL_PUT_KEY and ALPACA_BULL_PUT_SECRET_KEY
```

2. **Pull latest code**:
```bash
cd ~/DubK_Options
git pull origin main
```

3. **Restart bot**:
```bash
./start_bot.sh
```

4. **Verify both strategies initialized**:
```bash
tail -f bot.log | grep -E "WHEEL|SPREAD"
```

You should see both strategies scanning every 30 minutes.

---

## Transitioning to Live Trading

### Option 1: Move Wheel to Live First
1. Keep Wheel in paper until Dec 19 (assignment test)
2. If assignments work correctly, move Wheel to live
3. Keep Spreads in paper for 1-2 months
4. Move Spreads to live once validated

### Option 2: Move Spreads to Live First
1. Spreads have defined risk ($300-500 max loss per spread)
2. Easier to validate (no assignment complexity)
3. Start with $10K live account for spreads
4. Keep Wheel in paper until assignments validated

### Recommended Approach
1. **Week 1-2**: Both strategies in paper (current setup)
2. **Dec 19**: Validate Wheel assignments work correctly
3. **Week 3-4**: Move Spreads to live ($10K account) - lower risk
4. **Week 5-6**: Move Wheel to live ($50K-75K account) - after assignment validation

---

## Troubleshooting

### Spread Strategy Not Running
**Symptom**: Only see Wheel scans in logs

**Solutions**:
1. Check `.env` has `ALPACA_BULL_PUT_KEY` and `ALPACA_BULL_PUT_SECRET_KEY`
2. Check startup logs for spread initialization errors
3. Verify second Alpaca account is active

### Both Strategies Finding Zero Candidates
**Symptom**: "No candidates found" for both strategies

**Solutions**:
1. Check VIX < 30 (both strategies pause if VIX > 30)
2. Verify OpenBB API server is running: `ps aux | grep openbb`
3. Check IV rank threshold (lowered to 50% in both strategies)

### Spread Database Empty
**Symptom**: `spreads.db` file doesn't exist

**Solution**: Database is created automatically on first spread execution. If no spreads found, database won't exist yet.

---

## Example Startup Output

```
[WHEEL] The Wheel Strategy initialized - 50-95% win rate expected
[SPREAD] Connected to spread account - Portfolio: $10,000.00
[SPREAD] Bull Put Spread Strategy initialized - 65-75% win rate expected

[30-MIN SCAN] Wheel Strategy Scan...
[WHEEL] Active positions: 7/7
[WHEEL] Maximum wheel positions reached (7)

[SPREAD STRATEGY] Scanning for bull put spread opportunities...
[SPREAD] Active positions: 0/15
[SPREAD] Scanning for 15 new spread(s)...
[SPREAD] Found 8 spread candidates

[SPREAD] Evaluating: TSLA - 28.5% annual return
  Short $240.00 / Long $235.00
  Credit: $1.50, Max Risk: $350, ROI: 42.9%
  Position size: 2 contract(s)
✓ [SPREAD] TSLA: Spread executed successfully! (1/15 filled)

[SPREAD] Successfully filled 3 spread(s) this scan
```

---

## Files Modified

1. `config/default_config.py` - Added spread configuration
2. `src/strategies/bull_put_spread_strategy.py` - New spread strategy logic
3. `src/strategies/spread_manager.py` - New spread database manager
4. `src/strategies/__init__.py` - Export new classes
5. `src/bot_core.py` - Integrated spread strategy execution

---

## Next Steps

1. **Add your second Alpaca paper account keys to `.env`**
2. **Restart the bot**: `./start_bot.sh`
3. **Verify both strategies initialize** in startup logs
4. **Wait for first scan** (happens every 30 minutes)
5. **Monitor both databases** to see independent P&L tracking
6. **Let run for 1-2 weeks** to validate both strategies
7. **Transition to live** once both strategies proven in paper

---

**Generated**: 2025-11-19
**Author**: Claude AI
**Version**: 1.0 - Dual Strategy Implementation
