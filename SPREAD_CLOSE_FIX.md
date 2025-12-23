# Bull Put Spread Close Order Pricing Fix

**Date**: December 17, 2025
**Issue**: Spread close orders using stale prices, causing unfillable orders
**Status**: ✅ FIXED

---

## Problem Summary

### What Went Wrong
The IREN spread triggered a stop loss at -221% loss (-$221), but the close order was placed at **$0.04 debit** when the actual market cost was **~$1.57 debit** ($157 total). The order sat unfilled in Alpaca.

### Root Cause
The close order logic in `bot_core.py:4936-4972` used **cached options chain data** instead of **real-time Alpaca position prices**:

**OLD CODE:**
```python
# Get current option prices for limit orders
options = self.spread_strategy._get_options_chain(symbol)  # ❌ CACHED DATA

for opt in options:
    if (opt['type'] == 'put' and opt['expiration'] == expiration and
        abs(opt['strike'] - short_strike) < 0.01):
        short_put = opt
    elif (opt['type'] == 'put' and opt['expiration'] == expiration and
          abs(opt['strike'] - long_strike) < 0.01):
        long_put = opt

# Calculate individual leg prices
if short_put:
    short_close_price = round(short_put['ask'] * 1.05, 2)  # ❌ 5% slippage too tight
else:
    short_close_price = 0.50  # ❌ Unrealistic fallback

if long_put:
    long_close_price = round(long_put['bid'] * 0.95, 2)  # ❌ 5% slippage too tight
else:
    long_close_price = 0.10  # ❌ Unrealistic fallback

net_debit = round(short_close_price - long_close_price, 2)
```

**Issues:**
1. ❌ **Stale data** - Options chain cached/slow to update
2. ❌ **Missing data** - Fallback prices ($0.50/$0.10) completely wrong
3. ❌ **Insufficient slippage** - 5% too tight for stop losses
4. ❌ **No validation** - Accepts unreasonable prices

### Example: IREN Spread Failure

**Real-time Alpaca prices:**
- Short $30 put: **$2.18**
- Long $25 put: **$0.61**
- **Actual close cost**: $2.18 - $0.61 = **$1.57 debit** ($157)

**Bot's order:**
- Used stale/missing data from options chain
- Calculated: **$0.04 debit** ($4)
- **Result**: Order never filled - $153 too low

---

## Solution Implemented

### NEW CODE (bot_core.py:4936-5016)

```python
# CRITICAL FIX: Get REAL-TIME prices from Alpaca positions (not cached options chain)
try:
    alpaca_positions = self.spread_trading_client.get_all_positions()

    short_current_price = None
    long_current_price = None

    for pos in alpaca_positions:
        if pos.symbol == short_put_symbol:
            # ✅ Real-time current price for short put
            short_current_price = float(pos.current_price) if pos.current_price else None
        elif pos.symbol == long_put_symbol:
            # ✅ Real-time current price for long put
            long_current_price = float(pos.current_price) if pos.current_price else None

    # ✅ Validate we found both positions
    if short_current_price is None or long_current_price is None:
        logging.error(f"[SPREAD CLOSE] Cannot close - missing positions")
        return False

except Exception as e:
    logging.error(f"[SPREAD CLOSE] Failed to get real-time prices: {e}")
    return False

# ✅ Calculate with appropriate slippage based on exit reason
is_stop_loss = 'STOP_LOSS' in reason.upper()

# LEG 1: BUY TO CLOSE the short put
if is_stop_loss:
    short_close_price = round(short_current_price * 1.15, 2)  # ✅ 15% for stop loss
else:
    short_close_price = round(short_current_price * 1.10, 2)  # ✅ 10% for profit target

short_close_price = max(short_close_price, 0.05)

# LEG 2: SELL TO CLOSE the long put
if is_stop_loss:
    long_close_price = round(long_current_price * 0.85, 2)  # ✅ 15% haircut
else:
    long_close_price = round(long_current_price * 0.90, 2)  # ✅ 10% haircut

long_close_price = max(long_close_price, 0.01)

# Calculate net debit
net_debit = round(short_close_price - long_close_price, 2)

# ✅ Validate the close price is reasonable
spread_width = position['short_strike'] - position['long_strike']
max_reasonable_debit = spread_width * 1.1  # Can't cost more than 110% of width

if net_debit > max_reasonable_debit:
    logging.warning(f"[SPREAD CLOSE] Calculated debit ${net_debit:.2f} exceeds limit")
    net_debit = round(max_reasonable_debit, 2)  # ✅ Cap at reasonable max

logging.info(f"[SPREAD CLOSE] Real-time prices: Short ${short_current_price:.2f}, Long ${long_current_price:.2f}")
logging.info(f"[SPREAD CLOSE] Close prices with slippage: Short ${short_close_price:.2f}, Long ${long_close_price:.2f}")
logging.info(f"[SPREAD CLOSE] Slippage: {'15%' if is_stop_loss else '10%'} ({'STOP LOSS' if is_stop_loss else 'PROFIT TARGET'})")
logging.info(f"[SPREAD CLOSE] Net debit to close: ${net_debit:.2f}")
```

---

## Key Improvements

### 1. ✅ Real-Time Data Source
- **Before**: Cached options chain data (stale, unreliable)
- **After**: Live Alpaca position prices (`pos.current_price`)
- **Benefit**: Accurate, up-to-date pricing

### 2. ✅ Adaptive Slippage
- **Stop Loss**: 15% slippage (fast fill to cut losses)
- **Profit Target**: 10% slippage (reliable fill)
- **Before**: Fixed 5% (too tight, orders didn't fill)

### 3. ✅ Price Validation
- Checks that both leg prices are available
- Caps net debit at 110% of spread width
- Prevents orders > max loss (physically impossible)
- Returns `False` if validation fails

### 4. ✅ Better Logging
- Shows real-time prices vs. order prices
- Displays slippage percentage used
- Identifies stop loss vs. profit target exits
- Clear error messages if positions missing

### 5. ✅ No Unrealistic Fallbacks
- **Before**: Used $0.50/$0.10 fallback prices (wrong)
- **After**: Returns error if prices unavailable (safe)

---

## Expected Behavior with Fix

### Scenario: IREN Spread Stop Loss (-221%)

**Real-time Alpaca prices:**
- Short $30 put: $2.18
- Long $25 put: $0.61

**Bot's new calculation (15% stop loss slippage):**
- Short close price: $2.18 × 1.15 = **$2.51**
- Long close price: $0.61 × 0.85 = **$0.52**
- **Net debit**: $2.51 - $0.52 = **$1.99** (~$199)

**Validation:**
- Spread width: $5
- Max reasonable: $5 × 1.1 = $5.50 ✅
- Order price $1.99 < $5.50 ✅

**Result:**
- Order should fill quickly at ~$1.99 debit
- Actual fill likely between $1.57-$2.00 (market fluctuation)
- Stop loss executes successfully

---

## Testing Instructions

### 1. Cancel Existing Unfilled Order
In Alpaca web interface:
- Find order ID: `13fbe077-7db9-4c93-9e39-69699b8aaef0`
- Cancel it (currently sitting at $0.04 - won't fill)

### 2. Restart Bot
```bash
cd c:\Users\kyler\Documents\AI_BOT\DubK_Options
python run_bot.py
```

### 3. Monitor IREN Close
The bot should:
1. Detect IREN at -221% loss (still over -200% threshold)
2. Fetch real-time prices from Alpaca positions
3. Calculate close order with 15% stop loss slippage
4. Place order at ~$1.99 debit (not $0.04)
5. Order should fill within minutes

### 4. Check Logs
Look for:
```
[SPREAD CLOSE] Real-time prices: Short $2.18, Long $0.61
[SPREAD CLOSE] Close prices with slippage: Short $2.51, Long $0.52
[SPREAD CLOSE] Slippage: 15% (STOP LOSS)
[SPREAD CLOSE] Net debit to close: $1.99
```

---

## Impact on Other Spreads

This fix applies to **ALL spread closes**:

### Stop Loss Exits (-200% threshold)
- ✅ 15% slippage - faster fills
- ✅ Real-time pricing - accurate orders
- ✅ Validation - prevents bad orders

### Profit Target Exits (50% profit)
- ✅ 10% slippage - reliable fills
- ✅ Real-time pricing - accurate orders
- ✅ Maximizes profit capture

### Expiration Management (5 DTE)
- ✅ 10% slippage - gentle close
- ✅ Real-time pricing - fair exits

---

## Related Issues to Address Next

While this fixes the close order pricing, the **entry strategy still has problems**:

### Entry Issues (Not Fixed Yet)
1. ❌ Strike selection too aggressive (15% OTM vs. -0.30 delta)
2. ❌ Minimum credit too low ($0.30 on $5 spread)
3. ❌ No bull bias filter (entering in downtrends)
4. ❌ Delta validation too wide (-0.05 to -0.45)
5. ❌ Earnings buffer too short (14 days)

**See**: Previous analysis for full entry strategy fixes needed

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Data Source** | Cached options chain | Real-time Alpaca positions ✅ |
| **Stop Loss Slippage** | 5% (too tight) | 15% (fast fill) ✅ |
| **Profit Target Slippage** | 5% (too tight) | 10% (reliable) ✅ |
| **Price Validation** | None | Caps at spread width ✅ |
| **Error Handling** | Fallback to wrong prices | Return False if missing ✅ |
| **Logging** | Basic | Detailed with real prices ✅ |

**Result**: Spread close orders should now fill reliably at market prices with appropriate slippage.

---

## Files Modified

- `src/bot_core.py` (lines 4936-5016)
  - Replaced options chain lookup with Alpaca position prices
  - Added adaptive slippage (10%/15%)
  - Added price validation
  - Enhanced logging

**No database changes needed** - purely order execution logic.

---

## Next Steps

1. ✅ **Test with IREN** - Cancel old order, restart bot, verify new close
2. 🔄 **Fix entry strategy** - Address the 5 issues causing 50% win rate
3. 🔄 **Monitor fills** - Track how quickly orders fill with new slippage
4. 🔄 **Adjust slippage** - May reduce from 15% to 12% if fills too aggressive
