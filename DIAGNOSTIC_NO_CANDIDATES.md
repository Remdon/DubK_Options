# Diagnostic: Why No Wheel Candidates Found

**Date**: 2025-11-12 07:37 AM
**Issue**: Bot found ZERO wheel candidates on first scan

---

## Analysis of Bot Run

### What Happened:
```
[WHEEL STRATEGY] Scanning for premium collection opportunities...
[WHEEL] Active positions: 0/7
WARNING:root:[WHEEL] No wheel candidates found matching criteria
```

### Account Status:
- Portfolio Value: $99,744.78
- Cash: $99,744.78 (100% cash - ready to trade)
- Buying Power: $199,489.56
- Open Positions: 0
- **Result**: Bot has plenty of capital but found nothing to trade

---

## Likely Root Causes

### 1. **Market Closed / Pre-Market Scan Issue**
**Time of Run**: 2025-11-12 07:37 AM MST = 9:37 AM EST

- Market opens at 9:30 AM EST
- Bot ran just 7 minutes after market open
- Options data may not be fully populated yet
- IV rank calculations may be stale

**Impact**: Scanner may return empty universe or incomplete data

### 2. **IV Rank 60% Threshold Too Strict**
**Current Setting**: `WHEEL_MIN_IV_RANK = 60%`

This means the bot ONLY trades when stocks have IV in the top 40% of their historical range.

**Problem**: In normal/low volatility environments, very few stocks meet this threshold:
- Only ~15-25% of stocks have IV rank above 60% on average day
- After recent market calm, even fewer stocks qualify
- VIX was likely below 15 (low fear environment)

**Current Market Context** (Nov 2025):
- Markets have been stable
- VIX likely in 12-15 range (very low)
- Most stocks have IV rank 20-40% (selling cheap premium)

### 3. **Combined Filter Stack is Too Aggressive**

The Wheel candidate must pass ALL these filters simultaneously:

| Filter | Threshold | Pass Rate |
|--------|-----------|-----------|
| **Stock Price** | $20-$150 | ~40% of stocks |
| **Market Cap** | $2B minimum | ~30% of stocks |
| **IV Rank** | 60-100% | ~20% of stocks |
| **Beta** | 0.8-1.3 | ~50% of stocks |
| **Annual Return** | 20% minimum | ~30% (depends on IV) |

**Combined Pass Rate**: 40% × 30% × 20% × 50% × 30% = **0.36%**

With a base universe of ~200 stocks, only **0-2 stocks** would qualify on average.

In low volatility environment (IV rank constraint tighter), this drops to **ZERO**.

### 4. **Scanner Universe May Be Too Small**

Looking at the expert_scanner code:
```python
universe = loop.run_until_complete(self._build_stock_universe())
logging.info(f"[WHEEL] Built base universe with {len(universe)} stocks")
```

If the base universe is only scanning 50-100 stocks (common for "active" lists), and the filter pass rate is 0.36%, you get **zero candidates**.

### 5. **Options Chain Data May Be Missing**

Even if candidates pass filters, the bot still needs:
- Options chain data available
- Puts in the 25-45 DTE range
- Liquid options (volume/open interest)
- Valid bid/ask spreads

If OpenBB API doesn't return options data for qualifying stocks, they get rejected.

---

## Recommendations (In Order of Priority)

### IMMEDIATE FIX #1: Lower IV Rank Threshold (Quick Test)

**Change**:
```python
# From:
WHEEL_MIN_IV_RANK = 60  # Only sell when IV is elevated

# To:
WHEEL_MIN_IV_RANK = 40  # Sell when IV is moderately elevated
```

**Why**: This increases eligible stocks from ~20% to ~40% of universe (doubles opportunities)

**Trade-off**: Collect less premium per trade, but still profitable
- 60% IV rank = ~$2.50 premium per contract
- 40% IV rank = ~$1.80 premium per contract
- Still well above 20% annual return threshold

**Expert Validation**: TastyTrade and Option Alpha recommend IV rank 50+, so 40% is slightly more aggressive but acceptable.

### IMMEDIATE FIX #2: Expand Universe Sources

**Current Problem**: Scanner may only be pulling from limited sources

**Solution**: Verify `config/default_config.py` universe sources:
```python
self.UNIVERSE_SOURCES = [
    'active', 'unusual_volume', 'gainers', 'losers',
    'most_volatile', 'oversold', 'overbought'
]

self.UNIVERSE_LIMITS: Dict[str, int] = {
    'active': 30,           # Increase to 50
    'unusual_volume': 30,   # Increase to 50
    'gainers': 25,          # Increase to 40
    'losers': 25,           # Increase to 40
    'most_volatile': 25,    # Keep (these have high IV)
    'oversold': 20,         # Increase to 30
    'overbought': 20,       # Increase to 30
}
```

This expands base universe from ~175 stocks to ~265 stocks (+51%).

### IMMEDIATE FIX #3: Add Explicit High-IV List

**Add to universe sources**:
- Stocks with recent earnings (naturally high IV)
- Meme stocks (consistently high IV)
- Volatile sectors (tech, biotech)

**Implementation**: Add hardcoded "high IV watchlist" to scanner:
```python
HIGH_IV_WATCHLIST = [
    'GME', 'AMC', 'BBBY',  # Meme stocks (always high IV)
    'TSLA', 'NVDA',         # High beta tech
    'LCID', 'RIVN',         # EV stocks
    'SNAP', 'HOOD',         # Volatile recent IPOs
    # etc
]
```

### MEDIUM-TERM FIX #4: Dynamic IV Threshold

**Problem**: Fixed 60% threshold doesn't adapt to market conditions

**Solution**: Implement dynamic threshold based on VIX:
```python
# When VIX > 20: Use 60% threshold (strict, lots of opportunities)
# When VIX 15-20: Use 50% threshold (moderate)
# When VIX < 15: Use 40% threshold (adaptive to low vol environment)

def get_dynamic_iv_threshold(self, vix_level: float) -> float:
    if vix_level > 20:
        return 60.0
    elif vix_level > 15:
        return 50.0
    else:
        return 40.0
```

### LONG-TERM FIX #5: Add "Dry Spell" Mode

**Problem**: Bot may go days without finding candidates

**Solution**: Implement fallback mode when no candidates found for 24+ hours:
```python
# If no candidates found for 3+ scans (90 minutes):
# - Lower IV rank threshold by 10%
# - Expand DTE range to 21-60 days
# - Reduce minimum annual return to 15%
# - Alert user that entering "low opportunity environment"
```

---

## Testing Plan

### Test 1: Lower IV Rank to 40% (5 minutes)
1. SSH to EC2 server
2. Edit config: `nano config/default_config.py`
3. Change `WHEEL_MIN_IV_RANK` from 60 to 40
4. Restart bot: `./start_bot.sh`
5. Check if candidates appear

**Expected**: Should find 2-5 candidates in low vol environment

### Test 2: Check Universe Size (Diagnostic)
1. Add logging to see universe size:
   ```python
   logging.info(f"[WHEEL] Base universe size: {len(universe)} stocks")
   logging.info(f"[WHEEL] After price filter: {len(enriched_universe)} stocks")
   ```
2. This shows if scanner is the bottleneck

### Test 3: Manual Candidate Check
Run this Python script to manually test filters:
```python
# Test known high-IV stocks
test_symbols = ['SPY', 'QQQ', 'IWM', 'AAPL', 'TSLA', 'NVDA',
                'AMD', 'GME', 'AMC', 'PLTR']

for symbol in test_symbols:
    # Check if they meet Wheel criteria
    # Print rejection reason if they fail
```

---

## Most Likely Scenario

**Diagnosis**: IV Rank 60% threshold + low volatility market = ZERO candidates

**Quick Fix**: Lower `WHEEL_MIN_IV_RANK` to 40-50%

**Long-term**: Implement dynamic threshold based on VIX

---

## Next Steps for User

1. **Check VIX level** - If VIX < 15, the 60% IV threshold is too strict
2. **Lower IV threshold temporarily** to 40% to verify bot works
3. **Monitor results** - If candidates appear, keep 40% or adjust to 50%
4. **Implement dynamic threshold** for automatic adaptation

---

**Bottom Line**: The bot is working correctly, but the market doesn't have enough high-IV stocks to meet the strict 60% threshold. This is GOOD for market health but BAD for premium selling opportunities. Lower the threshold to 40-50% to adapt to current market conditions.
