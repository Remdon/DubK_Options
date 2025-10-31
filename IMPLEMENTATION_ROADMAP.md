# Implementation Roadmap: From Losing to Winning

## Current Status (2025-10-31)
- **Win Rate**: ~25% (6 of 8 positions losing)
- **Total P&L**: -$1,117 (-1.7%)
- **Problem**: Directional gambling, no hedging, poor entry timing
- **Root Cause**: Missing professional strategies

## PHASE 1: IMMEDIATE FIXES (Deploy Today)

### 1A. ✅ Fix Duplicate Check NoneType Error
**Status**: Code fixed, needs deployment to EC2
**File**: `src/bot_core.py` line 2486
**Impact**: Eliminates crashes during duplicate checking

**Deployment**:
```bash
ssh into EC2
cd DubK_Options
git pull
# Restart bot
```

### 1B. ✅ Stricter IV Rank Filters
**Status**: Code fixed, needs deployment to EC2
**File**: `src/bot_core.py` lines 1647-1659
**Changes**:
- Debit spreads: Max 40% IV rank (was 50%)
- Credit spreads: Min 60% IV rank (was 50%)
**Impact**: Prevents entering at IV extremes

**Expected Result**: Win rate improves from 25% → 40-50%

---

## PHASE 2: QUICK WINS (Week 1 - This Week)

### 2A. Enhance Grok Prompts with Portfolio Context
**Priority**: CRITICAL
**Effort**: 2-4 hours
**Expected Impact**: +10-15% win rate

**Implementation**:
1. Add portfolio Greeks calculation
2. Include current positions in Grok prompt
3. Add correlation check
4. Add VIX regime context

**Code Location**: `src/bot_core.py` - Grok analysis section

**New Prompt Template**:
```
PORTFOLIO CONTEXT:
- Total Positions: 9
- Portfolio Delta: +15 (bullish bias)
- Portfolio Theta: -$45/day (losing to decay)
- VIX: 17.43 (NORMAL, 42nd percentile)
- Tech Exposure: 35% (AAPL, INTC, QFIN)

NEW OPPORTUNITY: {symbol}
- Correlation to existing: AAPL 0.85, INTC 0.78
- Would increase Tech to 45% (OVER 30% limit)

QUESTION: Should we enter this trade given portfolio context?
Consider: diversification, delta balance, sector limits
```

### 2B. Add VIX Regime Position Sizing
**Priority**: HIGH
**Effort**: 1-2 hours
**Expected Impact**: +5-10% returns

**Logic**:
```python
# src/bot_core.py - position sizing function
def get_vix_position_multiplier(vix_value, vix_percentile):
    if vix_percentile < 20:
        # VIX very low = volatility will spike
        return 0.5  # REDUCE credit spreads, INCREASE debit spreads
    elif vix_percentile < 40:
        # VIX below average
        return 0.75
    elif vix_percentile < 60:
        # VIX normal
        return 1.0
    elif vix_percentile < 80:
        # VIX elevated = good for selling premium
        return 1.25
    else:
        # VIX very high = GREAT for selling premium
        return 1.5
```

### 2C. Delta-Neutral Portfolio Tracking
**Priority**: HIGH
**Effort**: 2-3 hours
**Expected Impact**: -30% drawdowns

**Implementation**:
1. Calculate portfolio delta every position check
2. If delta > +25 or < -25, flag for hedging
3. Recommend offsetting trades

**Code**:
```python
# src/risk/position_manager.py
def get_portfolio_greeks(self):
    total_delta = 0
    total_gamma = 0
    total_theta = 0
    total_vega = 0

    for position in self.get_open_positions():
        greeks = self.calculate_position_greeks(position)
        total_delta += greeks['delta']
        total_gamma += greeks['gamma']
        total_theta += greeks['theta']
        total_vega += greeks['vega']

    return {
        'delta': total_delta,
        'gamma': total_gamma,
        'theta': total_theta,
        'vega': total_vega
    }

def needs_delta_hedge(self):
    greeks = self.get_portfolio_greeks()
    if abs(greeks['delta']) > 25:
        return True, greeks['delta']
    return False, 0
```

---

## PHASE 3: THE WHEEL STRATEGY (Week 2)

### 3A. Wheel Strategy Core Logic
**Priority**: CRITICAL
**Effort**: 8-12 hours
**Expected Impact**: +25-40% win rate improvement

**The Wheel Cycle**:
```
1. SELL CASH-SECURED PUT
   - Enter when IV rank > 60%
   - Strike: 5-10% OTM
   - DTE: 30-45 days
   - Collect premium

2a. PUT EXPIRES WORTHLESS (80% of time)
   → Keep premium
   → Repeat Step 1

2b. PUT ASSIGNED (20% of time)
   → Own stock at discount
   → Go to Step 3

3. SELL COVERED CALL
   - Strike: Above cost basis
   - DTE: 30-45 days
   - Collect premium

4a. CALL EXPIRES WORTHLESS (70% of time)
   → Keep premium + stock
   → Repeat Step 3

4b. CALL ASSIGNED (30% of time)
   → Sell stock at profit
   → Go to Step 1
```

**New Files Needed**:
```
src/strategies/wheel_strategy.py
src/strategies/wheel_manager.py
```

**Database Schema Addition**:
```sql
CREATE TABLE wheel_positions (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    state TEXT,  -- 'SELLING_PUTS', 'ASSIGNED', 'SELLING_CALLS'
    stock_cost_basis REAL,
    shares_owned INTEGER,
    total_premium_collected REAL,
    created_at TIMESTAMP
);
```

### 3B. Wheel Strategy Selection
**Criteria for Wheel Candidates**:
- Stock price: $15-150 (affordable, quality)
- IV rank: > 60% (expensive premium to sell)
- Beta: 0.8-1.3 (not too volatile)
- Market cap: > $2B
- Dividend: Bonus if > 2% (collect while holding stock)

**Implementation**:
```python
# src/strategies/wheel_strategy.py
class WheelStrategy:
    def find_wheel_candidates(self, max_candidates=5):
        candidates = []
        for stock in self.scanner.get_universe():
            if not self.is_wheel_candidate(stock):
                continue

            # Find optimal put to sell
            put_strike = stock['price'] * 0.90  # 10% OTM
            put_premium = self.get_put_premium(stock['symbol'], put_strike, 35)

            # Calculate returns
            annual_return = (put_premium / (put_strike * 100)) * (365 / 35)

            if annual_return > 0.20:  # 20% annual return target
                candidates.append({
                    'symbol': stock['symbol'],
                    'put_strike': put_strike,
                    'put_premium': put_premium,
                    'annual_return': annual_return
                })

        return sorted(candidates, key=lambda x: x['annual_return'], reverse=True)[:max_candidates]
```

---

## PHASE 4: MEAN REVERSION (Week 3)

### 4A. IV Percentile Tracking
**Priority**: HIGH
**Effort**: 4-6 hours
**Expected Impact**: +15-20% win rate

**Database Schema**:
```sql
CREATE TABLE iv_history (
    symbol TEXT,
    date DATE,
    iv_value REAL,
    iv_rank INTEGER,
    PRIMARY KEY (symbol, date)
);
```

**Logic**:
```python
# src/analyzers/iv_analyzer.py
def calculate_iv_zscore(symbol, current_iv):
    """Calculate Z-score of current IV vs 90-day history"""
    history = self.get_iv_history(symbol, days=90)
    mean_iv = np.mean(history)
    std_iv = np.std(history)

    z_score = (current_iv - mean_iv) / std_iv
    return z_score

def should_sell_premium(symbol, current_iv_rank):
    """Sell premium when IV is abnormally high"""
    z_score = self.calculate_iv_zscore(symbol, current_iv_rank)

    if z_score > 2.0:
        # IV is 2+ std devs above mean = VERY HIGH
        return True, "EXTREME_HIGH_IV"
    elif z_score > 1.5:
        return True, "HIGH_IV"
    else:
        return False, "NORMAL_IV"

def should_buy_options(symbol, current_iv_rank):
    """Buy options when IV is abnormally low"""
    z_score = self.calculate_iv_zscore(symbol, current_iv_rank)

    if z_score < -1.5:
        # IV is 1.5+ std devs below mean = VERY LOW
        return True, "EXTREME_LOW_IV"
    elif z_score < -1.0:
        return True, "LOW_IV"
    else:
        return False, "NORMAL_IV"
```

### 4B. Mean Reversion Entry Rules
**New Validation Logic**:
```python
# src/bot_core.py - post_validate_trade()
def post_validate_trade(self, symbol, strategy, iv_rank):
    # ... existing validations ...

    # NEW: Mean reversion check
    z_score = self.iv_analyzer.calculate_iv_zscore(symbol, iv_rank)

    is_credit = any(x in strategy for x in ['BULL_PUT', 'BEAR_CALL', 'CREDIT'])
    is_debit = any(x in strategy for x in ['BULL_CALL', 'BEAR_PUT', 'DEBIT'])

    if is_credit:
        # Selling premium - need HIGH IV that will revert down
        if z_score < 1.0:
            return False, f"IV rank {iv_rank}% not HIGH ENOUGH for mean reversion (Z-score: {z_score:.2f}, need > 1.0)"

    if is_debit:
        # Buying options - need LOW IV that will revert up
        if z_score > -0.5:
            return False, f"IV rank {iv_rank}% not LOW ENOUGH for mean reversion (Z-score: {z_score:.2f}, need < -0.5)"

    return True, "Mean reversion validated"
```

---

## PHASE 5: EARNINGS PLAYS (Week 4)

### 5A. Pre-Earnings Volatility Expansion
**Strategy**: Buy straddles 1-2 weeks before earnings when IV < 50%

**Logic**:
```python
# src/strategies/earnings_strategy.py
class EarningsStrategy:
    def find_pre_earnings_plays(self):
        """Find stocks 7-14 days before earnings with low IV"""
        candidates = []

        for stock in self.earnings_calendar.get_upcoming_earnings(days=14):
            days_to_earnings = stock['days_until']

            if 7 <= days_to_earnings <= 14:
                iv_rank = self.get_iv_rank(stock['symbol'])

                if iv_rank < 50:
                    # IV will likely spike 20-100% before earnings
                    expected_iv_gain = (100 - iv_rank) * 0.5  # Conservative estimate
                    candidates.append({
                        'symbol': stock['symbol'],
                        'days_to_earnings': days_to_earnings,
                        'current_iv_rank': iv_rank,
                        'expected_iv_gain': expected_iv_gain,
                        'strategy': 'LONG_STRADDLE',
                        'exit_before_earnings': True
                    })

        return sorted(candidates, key=lambda x: x['expected_iv_gain'], reverse=True)
```

### 5B. Post-Earnings IV Crush
**Strategy**: Sell credit spreads immediately after earnings when IV > 80%

**Logic**:
```python
def find_post_earnings_plays(self):
    """Find stocks 0-1 days after earnings with high IV"""
    candidates = []

    for stock in self.earnings_calendar.get_recent_earnings(days=1):
        iv_rank = self.get_iv_rank(stock['symbol'])

        if iv_rank > 80:
            # IV will collapse 30-60% in next 2-5 days
            expected_iv_drop = iv_rank * 0.4  # Conservative estimate
            candidates.append({
                'symbol': stock['symbol'],
                'current_iv_rank': iv_rank,
                'expected_iv_drop': expected_iv_drop,
                'strategy': 'CREDIT_SPREAD',  # Sell expensive premium
                'dte': 7  # Short-dated to capture IV crush
            })

    return sorted(candidates, key=lambda x: x['expected_iv_drop'], reverse=True)
```

---

## PHASE 6: GAMMA SCALPING (Week 5-6)

### 6A. Long Gamma Setup
**Requirements**:
- Stock trading capability (not just options)
- Real-time delta calculation
- Auto-rebalancing

**Implementation Complexity**: HIGH (requires stock trading integration)
**Expected Returns**: 10-20% annually
**Risk Level**: LOW (delta-neutral)

**Decision**: DEFER to Month 2-3 after other strategies proven

---

## EXPECTED RESULTS TIMELINE

### Current (Before Improvements)
- Win Rate: 25%
- Monthly Return: -2% to -5%
- Sharpe Ratio: Negative
- Max Drawdown: -35%

### After Phase 1 (Day 1 - Deploy Fixes)
- Win Rate: 40-50%
- Monthly Return: 0% to +5%
- Sharpe Ratio: 0.5-1.0
- Max Drawdown: -25%

### After Phase 2 (Week 1 - Quick Wins)
- Win Rate: 50-60%
- Monthly Return: +5% to +10%
- Sharpe Ratio: 1.0-1.5
- Max Drawdown: -20%

### After Phase 3 (Week 2 - The Wheel)
- Win Rate: 60-75%
- Monthly Return: +10% to +15%
- Sharpe Ratio: 1.5-2.0
- Max Drawdown: -15%

### After Phase 4 (Week 3 - Mean Reversion)
- Win Rate: 65-80%
- Monthly Return: +12% to +18%
- Sharpe Ratio: 2.0-2.5
- Max Drawdown: -12%

### After Phase 5 (Week 4 - Earnings Plays)
- Win Rate: 70-85%
- Monthly Return: +15% to +25%
- Sharpe Ratio: 2.5-3.0
- Max Drawdown: -10%

---

## DEPLOYMENT CHECKLIST

### Today (Phase 1):
- [ ] SSH into EC2
- [ ] `cd DubK_Options && git pull`
- [ ] Verify no conflicts
- [ ] Restart bot
- [ ] Monitor for NoneType errors (should be gone)
- [ ] Watch for IV rank rejections (should see more)

### This Week (Phase 2):
- [ ] Implement Grok portfolio context enhancement
- [ ] Add VIX regime position sizing
- [ ] Add portfolio delta tracking
- [ ] Deploy to EC2
- [ ] Monitor win rate improvement

### Week 2 (Phase 3):
- [ ] Design Wheel database schema
- [ ] Implement Wheel core logic
- [ ] Test Wheel candidate selection
- [ ] Paper trade Wheel for 1 week
- [ ] Deploy to live if successful

### Weeks 3-4 (Phases 4-5):
- [ ] Build IV history database
- [ ] Implement mean reversion logic
- [ ] Add earnings play strategies
- [ ] Full backtest on historical data
- [ ] Deploy incrementally

---

## SUCCESS METRICS

**Track Weekly**:
1. Win Rate (% of profitable trades)
2. Average Return Per Trade
3. Sharpe Ratio
4. Max Drawdown
5. Portfolio Delta (should be near 0)
6. Strategy Distribution (diversification)

**Success Criteria**:
- Win Rate > 60% by end of Week 2
- Monthly Return > 10% by end of Week 3
- Sharpe Ratio > 1.5 by end of Week 4
- No single strategy > 40% of positions

---

## RISK MANAGEMENT

**Phase 1-2 (Weeks 1-2)**:
- Max position size: 10% of capital
- Max total positions: 10
- Max strategy concentration: 40%

**Phase 3+ (Weeks 3+)**:
- Increase max position size to 12% (only for Wheel)
- Increase max positions to 15
- Add sector diversification limits

**Emergency Stop**:
- If win rate < 40% after Phase 2 → PAUSE and debug
- If drawdown > 25% at any point → REDUCE position sizing by 50%
- If any strategy win rate < 35% → DISABLE that strategy

---

## NEXT ACTIONS

1. **RIGHT NOW**: Deploy Phase 1 fixes to EC2
2. **Today**: Start Phase 2A (Grok enhancement)
3. **Tomorrow**: Complete Phase 2B and 2C
4. **This Weekend**: Design Phase 3 (Wheel) architecture
5. **Next Week**: Implement and test Wheel strategy

Let's get the bot winning!
