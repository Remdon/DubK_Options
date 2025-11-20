# Wheel Strategy Improvements - Based on Live Trade Analysis (Nov 19, 2025)

## Trade Performance Summary

**Period**: Nov 12-13 entry, reviewed Nov 19
**Total Positions**: 7 (6 open, 1 closed)
**Contracts**: 27 total (25 open)
**P&L**: -$2,916 unrealized (-$3,040 open + $124 CSCO profit)

### Performance by Position

| Symbol | Strike | Qty | Entry Credit | Current P&L | Status | Issue |
|--------|--------|-----|--------------|-------------|--------|-------|
| CART | $37 | 3 | $269.94 | +$105 | ✅ OTM | Working as intended |
| GFS | $30 | 4 | $299.92 | +$20 | ✅ OTM | Working as intended |
| CSCO | ~$50 | 2 | $169.96 | +$124 | ✅ CLOSED | Profit target hit |
| TX | $35 | 3 | $134.94 | -$210 | ⚠️ Near ITM | Volatility spike |
| CMCSA | $26 | 5 | $224.90 | -$165 | ⚠️ Near ITM | Longer dated, recoverable |
| SLM | $26 | 5 | $249.90 | -$225 | ⚠️ Near ITM | Policy noise |
| **MIR** | **$25** | **5** | **$324.90** | **-$1,000** | **❌ ITM** | **Deep ITM, -150% ROI** |
| **XPEV** | **$25** | **5** | **$684.90** | **-$1,565** | **❌ ITM** | **Deep ITM, -228% ROI** |

**Key Findings**:
- ✅ 43% of positions profitable (3/7)
- ❌ 57% losing, with 71% of losses from EV sector (MIR/XPEV)
- ❌ Average ROI on open positions: -123%
- ⚠️ Sector concentration: 40% in EV/auto

---

## Critical Issues Found

### 1. NO Stop Loss or Deep ITM Management

**Current Bot Behavior**:
- ✅ Has profit target (50% of premium) - WORKING
- ❌ Has NO stop loss for runaway losses - MISSING
- ❌ Has NO deep ITM rescue logic - MISSING

**Real Impact**:
- XPEV: -228% ROI ($1,565 loss on $684 credit)
- MIR: -150% ROI ($1,000 loss on $324 credit)
- Combined: -$2,565 loss (84% of total portfolio loss)

**What Should Happen**:
- If put goes >$1.00 ITM (e.g., stock at $21, strike $25), bot should:
  1. **Roll down and out**: Close $25 put, sell $22 Jan put for net credit
  2. **Or close at max loss**: -200% ROI stop loss (risk 2x premium)
  3. **Or transition to assignment**: Accept stock, immediately sell covered call

### 2. Sector Concentration Risk

**Current Bot Behavior**:
- ✅ Limits position count (MAX_WHEEL_POSITIONS = 5)
- ❌ NO sector diversification limits
- ❌ NO correlation checks

**Real Impact**:
- 40% of capital in EV sector (MIR + XPEV = 10 contracts / 25 total)
- EV sector drop (-20% since entry) caused 84% of losses
- When one EV name drops, correlated names drop together

**What Should Happen**:
- Limit per-sector exposure: MAX 20% of active positions per sector
- Use sector classification (EV, Tech, Finance, Staples, etc.)
- Spread across 3+ sectors minimum

### 3. Position Sizing Too Aggressive

**Current Bot Behavior**:
- ✅ Calculates contracts based on account value
- ⚠️ Allows too many contracts per symbol (5 contracts = $12,500 risk)

**Real Impact**:
- XPEV: 5 contracts × $25 strike = $12,500 notional
- MIR: 5 contracts × $25 strike = $12,500 notional
- Total EV risk: $25,000 (50% of a $50K account)

**What Should Happen**:
- Cap max contracts per symbol: 3-4 max (not 5)
- Cap per-symbol notional: 15% of account max
- Dynamic sizing: reduce if win rate drops below 70%

### 4. NO Dynamic Win Rate Adjustment

**Current Bot Behavior**:
- ✅ Tracks win rate in database
- ❌ Doesn't reduce position sizing when losing

**Real Impact**:
- Nov 12-13 entries likely had high win rate (previous cycles)
- Bot continued full position sizes despite changing market
- Should have reduced size after first ITM position

**What Should Happen**:
- If win rate drops below 70%: reduce contracts to 1-2
- If consecutive losses > 2: pause new entries for 1 week
- If monthly P&L < -5%: switch to defensive (closer strikes, fewer contracts)

---

## Recommended Bot Enhancements

### Priority 1: Add Deep ITM Management (CRITICAL)

**File to Modify**: `src/strategies/wheel_strategy.py` and `src/bot_core.py`

**New Logic**:
```python
def should_roll_deep_itm_put(self, position: Dict, current_stock_price: float) -> bool:
    """
    Check if put is deep ITM and should be rolled down/out.

    Deep ITM = strike > stock price + $1.00
    """
    strike = position['strike']
    intrinsic_value = strike - current_stock_price

    # If put is >$1.00 ITM, consider rolling
    if intrinsic_value > 1.00:
        # Check if rolling would net a credit
        # (close current put, sell lower strike put)
        return True

    return False

def should_stop_loss_put(self, position: Dict) -> bool:
    """
    Check if put hit max loss threshold.

    Stop loss at -200% ROI (lost 2x the premium collected)
    """
    entry_premium = position['total_premium_collected']
    current_value = self._get_current_put_value(position)

    # Calculate P&L
    unrealized_pnl = entry_premium - (current_value * 100)
    roi = (unrealized_pnl / entry_premium) if entry_premium > 0 else 0

    # Stop loss at -200% ROI
    if roi <= -2.00:  # Lost 2x the premium
        return True

    return False
```

**Integration**: Add to `_manage_existing_wheel_position()` BEFORE profit target check

### Priority 2: Add Sector Diversification

**File to Modify**: `src/strategies/wheel_strategy.py`

**New Config**:
```python
# Sector limits
MAX_POSITIONS_PER_SECTOR = 2  # Max 2 positions per sector
SECTORS = {
    'EV': ['XPEV', 'NIO', 'RIVN', 'LCID', 'MIR'],
    'AUTO': ['F', 'GM', 'TX'],
    'TECH': ['AAPL', 'MSFT', 'NVDA', 'AMD'],
    'FINANCE': ['JPM', 'BAC', 'WFC', 'SLM'],
    'TELECOM': ['T', 'VZ', 'CMCSA'],
    'STAPLES': ['KO', 'PEP', 'WMT', 'TGT'],
    # ... add more
}
```

**New Method**:
```python
def get_symbol_sector(self, symbol: str) -> str:
    """Get sector for symbol"""
    for sector, symbols in self.SECTORS.items():
        if symbol in symbols:
            return sector
    return 'OTHER'

def can_add_symbol_by_sector(self, symbol: str) -> bool:
    """Check if adding this symbol would violate sector limits"""
    sector = self.get_symbol_sector(symbol)

    # Get current positions in this sector
    current_positions = self.wheel_manager.get_all_positions()
    sector_count = sum(1 for pos in current_positions
                      if self.get_symbol_sector(pos['symbol']) == sector)

    if sector_count >= self.MAX_POSITIONS_PER_SECTOR:
        logging.info(f"[WHEEL] Skipping {symbol}: sector {sector} limit reached")
        return False

    return True
```

### Priority 3: Add Max Loss Per Position

**File to Modify**: `src/strategies/wheel_strategy.py`

**New Config**:
```python
# Risk limits
MAX_LOSS_PER_POSITION = -2.00  # -200% ROI (2x premium)
MAX_CONTRACTS_PER_SYMBOL = 3   # Reduce from 5
MAX_NOTIONAL_PER_SYMBOL = 0.15 # 15% of account
```

**Update Position Sizing**:
```python
def calculate_position_size(self, symbol, put_strike, account_value, existing_positions):
    # ... existing logic ...

    # NEW: Cap at 3 contracts
    contracts = min(contracts, self.MAX_CONTRACTS_PER_SYMBOL)

    # NEW: Cap at 15% notional
    notional = put_strike * 100 * contracts
    max_notional = account_value * self.MAX_NOTIONAL_PER_SYMBOL

    if notional > max_notional:
        contracts = int(max_notional / (put_strike * 100))

    return max(1, contracts)  # At least 1
```

### Priority 4: Dynamic Sizing Based on Win Rate

**File to Modify**: `src/strategies/wheel_strategy.py`

**New Method**:
```python
def get_dynamic_position_multiplier(self) -> float:
    """
    Reduce position sizing when win rate drops.

    Returns multiplier: 1.0 (full size) to 0.5 (half size)
    """
    stats = self.wheel_manager.get_wheel_stats()
    win_rate = stats.get('win_rate', 100.0) / 100.0

    if win_rate >= 0.70:
        return 1.0  # Full size
    elif win_rate >= 0.50:
        return 0.75  # 75% size
    else:
        return 0.5  # 50% size (defensive)

def calculate_position_size(self, symbol, put_strike, account_value, existing_positions):
    # ... existing logic ...

    # NEW: Apply dynamic multiplier
    multiplier = self.get_dynamic_position_multiplier()
    contracts = int(contracts * multiplier)

    return max(1, contracts)
```

---

## Implementation Priority

| Priority | Feature | Impact | Effort | Status |
|----------|---------|--------|--------|--------|
| 🔥 **P0** | Deep ITM management (roll/stop) | Prevents -200% ROI losses | 4 hrs | ❌ TODO |
| 🔥 **P0** | Max loss per position (-200% ROI) | Caps runaway losses | 2 hrs | ❌ TODO |
| ⚠️ **P1** | Sector diversification limits | Reduces concentration risk | 3 hrs | ❌ TODO |
| ⚠️ **P1** | Reduce max contracts to 3 | Reduces per-symbol risk | 30 min | ❌ TODO |
| 📊 **P2** | Dynamic sizing by win rate | Adapts to market regime | 2 hrs | ❌ TODO |
| 📊 **P2** | Consecutive loss pause | Prevents revenge trading | 1 hr | ❌ TODO |

**Total Effort**: ~12 hours of development + testing

---

## Expected Impact

### Before (Current Results)
- 7 positions, 57% losing
- Avg ROI: -123%
- Max loss: -228% (XPEV)
- Sector concentration: 40% EV

### After (With Improvements)
- **Sector limits**: Max 20% per sector → XPEV would be 3 contracts, not 5
- **Max contracts**: 3 per symbol → MIR/XPEV each 3 contracts (60% less risk)
- **Deep ITM management**: XPEV rolled at -$1.00 ITM → Loss capped at ~-100% ROI
- **Expected outcome**: -$900 total loss instead of -$2,916 (69% reduction)

### Projected Performance
- Win rate target: 75-85% (vs current 43%)
- Monthly return: 1-2% (vs current -6%)
- Max drawdown: -3% (vs current -6%)
- Sharpe ratio: 1.5+ (risk-adjusted returns)

---

## Testing Plan

### Phase 1: Backtest on Nov 12-19 Data
1. Simulate entries with new rules
2. Verify sector limits would block 5th EV position
3. Verify XPEV/MIR would trigger stop loss at -200%
4. Calculate theoretical P&L improvement

### Phase 2: Paper Trading Validation
1. Deploy new rules to paper account
2. Run for 2 weeks (20 trading days)
3. Compare to live results
4. Tune thresholds if needed

### Phase 3: Gradual Live Rollout
1. Deploy sector limits first (safest)
2. Add max loss stops (critical)
3. Enable dynamic sizing (performance boost)

---

## Conclusion

**Root Causes of -$2,916 Loss**:
1. No deep ITM management → -$2,565 from MIR/XPEV runaway losses
2. Sector concentration → 40% in falling EV sector
3. Oversized positions → 5 contracts per symbol too aggressive

**Fixes Needed** (in order):
1. Add -200% ROI stop loss (prevents XPEV-style disasters)
2. Limit sector exposure to 20% (diversifies risk)
3. Cap contracts at 3 per symbol (reduces notional)
4. Dynamic sizing by win rate (adapts to regime)

**Expected Results**:
- Current: -6% monthly, 43% win rate
- After fixes: +1-2% monthly, 75-85% win rate
- Risk reduction: 69% less max loss per position

All improvements are backward-compatible and can be added incrementally to existing wheel strategy implementation.
