# DubK Options Bot - Wheel Strategy Only

**Systematic Premium Collection via The Wheel Strategy**

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![OpenBB](https://img.shields.io/badge/OpenBB-4.5.0-green.svg)](https://openbb.co/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🎯 What This Bot Does

This bot implements **The Wheel Strategy** - one of the highest win-rate options strategies (50-95% success rate). It systematically collects premium by:

1. **Selling Cash-Secured Puts** on quality stocks when IV is elevated (60-100%)
2. **Getting Assigned Stock** if put expires in-the-money
3. **Selling Covered Calls** on owned stock to collect more premium
4. **Repeating the Cycle** when stock is called away

**No directional bets. No complex spreads. Just systematic premium collection.**

---

## ⚠️ Important: Simplified from Multi-Strategy

**Previous versions** of this bot attempted to trade multiple strategies (spreads, straddles, iron condors) with AI analysis. **Result: 25% win rate, losing money.**

**Current version:** Wheel Strategy ONLY. All other code remains but is **not executed**. The bot has been simplified to focus on what works.

---

## 🎪 The Wheel Strategy

### Phase 1: Selling Puts
- Sell cash-secured puts 10% out-of-the-money
- Target: 30-45 days to expiration (optimal theta decay)
- Collect premium regardless of outcome
- **If expires worthless**: Keep premium, sell another put
- **If assigned**: Move to Phase 2

### Phase 2: Assigned Stock
- Own 100 shares at put strike price
- Move to Phase 3 immediately

### Phase 3: Selling Covered Calls
- Sell calls 5% above cost basis
- Collect premium on owned stock
- **If expires worthless**: Keep premium and stock, sell another call
- **If called away**: Sell stock for profit, return to Phase 1

---

## 📊 Win Rate vs Old Approach

| Metric | Old Multi-Strategy | Wheel Strategy |
|--------|-------------------|----------------|
| **Win Rate** | ~25% | 50-95% |
| **Complexity** | High (12+ strategies) | Low (3 phases) |
| **Entry Timing** | IV rank 100% (bad) | IV rank 60-100% (good) |
| **Stock Quality** | Penny stocks allowed | $20+ only, $2B+ market cap |
| **Execution** | Multi-leg failures | Simple single-leg |

---

## 🔧 Configuration

### Wheel Parameters (in `config/default_config.py`)

```python
# IV Requirements
WHEEL_MIN_IV_RANK = 50   # Sell when IV elevated (adaptive to market conditions)
WHEEL_MAX_IV_RANK = 100  # Can sell at any high IV

# Stock Quality
WHEEL_MIN_STOCK_PRICE = $20     # No penny stocks
WHEEL_MAX_STOCK_PRICE = $150    # Affordable for assignment
WHEEL_MIN_MARKET_CAP = $2B      # Quality companies only

# Position Limits
MAX_WHEEL_POSITIONS = 7          # Max 7 positions
MAX_CAPITAL_PER_WHEEL = 14%      # 14% capital per position (98% total)

# Strike Selection
WHEEL_PUT_OTM_PERCENT = 0.90     # Sell puts 10% OTM
WHEEL_CALL_ABOVE_BASIS = 1.05    # Sell calls 5% above cost

# DTE Parameters
WHEEL_TARGET_DTE = 35            # Target 35 days
WHEEL_MIN_DTE = 25               # Accept 25-45 days
WHEEL_MAX_DTE = 45
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd DubK_Options
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure API Keys

Create `.env` file:
```
XAI_API_KEY=your-xai-key-here
ALPACA_API_KEY=your-alpaca-key
ALPACA_SECRET_KEY=your-alpaca-secret
ALPACA_MODE=paper
```

### 3. Run the Bot

```bash
./start_bot.sh
```

Or manually:
```bash
# Terminal 1 - Start OpenBB API
python -m uvicorn openbb_core.api.rest_api:app --host 127.0.0.1 --port 6900

# Terminal 2 - Run Bot
python run_bot.py
```

---

## 📁 Project Structure

```
DubK_Options/
├── config/
│   └── default_config.py        # Wheel strategy configuration
├── src/
│   ├── bot_core.py              # Main bot (Wheel-only now)
│   ├── strategies/
│   │   ├── wheel_strategy.py    # Wheel candidate finding
│   │   └── wheel_manager.py     # Wheel position tracking
│   ├── scanners/
│   │   └── expert_scanner.py    # Stock universe scanner
│   ├── risk/
│   │   └── position_manager.py  # Exit management
│   └── journal/
│       └── trade_journal.py     # Performance tracking
├── run_bot.py                   # Entry point
└── start_bot.sh                 # Startup script
```

---

## 📈 What the Bot Does Every 30 Minutes

1. **Check Existing Positions**
   - Monitor for stop losses and profit targets
   - Check for assignments (puts → stock)
   - Manage covered call positions

2. **Scan for Wheel Candidates**
   - Filter stocks: $20-$150 price, $2B+ market cap
   - Check IV rank: Must be 60-100%
   - Calculate expected annual returns

3. **Execute Best Opportunities**
   - Sell cash-secured puts on top 3 candidates
   - Position size: Up to 20% capital per position
   - Maximum 5 active Wheel positions

---

## 🎓 Expected Performance

### Realistic Expectations

- **Win Rate**: 50-70% (most puts expire worthless)
- **Annual Return**: 15-40% on deployed capital
- **Time Horizon**: Best over 6-12 months
- **Capital Requirements**: $10k+ recommended (need cash for assignment)

### Risk Profile

- **Best Case**: Consistent 2-4% monthly returns via premium
- **Worst Case**: Get assigned stock in bear market (still collect premium)
- **Max Loss**: Stock goes to zero (same risk as owning stock)

---

## ⚠️ Important Disclaimers

### Start with Paper Trading

1. Run bot in paper mode for 2-4 weeks
2. Verify it only trades the Wheel strategy
3. Check that all entries are IV rank 60+
4. Monitor win rate (should be 50%+)
5. Scale gradually to live trading

### Risk Warnings

- ⚠️ **You must have cash for assignment** - If put assigned, you'll own 100 shares
- ⚠️ **Options can lose 100%** - Understand the risks before trading
- ⚠️ **Past performance ≠ future results** - No guarantees
- ⚠️ **Monitor daily** - Check positions regularly
- ⚠️ **Start small** - Test with minimal capital first

---

## 🔍 Monitoring Your Bot

### Check Wheel Positions

The bot displays:
- Active Wheel positions
- Current phase (SELLING_PUTS, ASSIGNED, SELLING_CALLS)
- Total premium collected
- Win rate and completed cycles

### Database Tracking

All Wheel positions are tracked in `trades.db`:
- `wheel_positions` - Active positions
- `wheel_transactions` - All premiums collected
- `wheel_history` - Completed cycles with P&L

---

## 📝 Legacy Code Notice

This codebase previously supported 12+ options strategies with AI analysis. That approach resulted in a 25% win rate and losses.

**Current state:** Bot simplified to Wheel-only. Old strategy code remains in the codebase but is **not executed**. The main trading loop only calls `execute_wheel_opportunities()`.

**Why keep old code?** For reference and potential future enhancements. But the bot is now focused on what works: systematic premium collection via the Wheel.

---

## 🤝 Contributing

If you improve the Wheel strategy or find bugs, pull requests are welcome.

Please focus on:
- Wheel strategy improvements
- Better stock quality filters
- Enhanced IV rank analysis
- Assignment detection and handling

---

## 📄 License

MIT License - See LICENSE file

---

## 🙏 Acknowledgments

- **OpenBB** - Market data platform
- **Alpaca** - Trading API
- **The Wheel Strategy** - Time-tested options strategy

---

## 📞 Support

**Issues**: Open a GitHub issue
**Documentation**: See `DubK_Options_Investigation_Report.md` for full analysis
**Questions**: Check logs first: `tail -f bot.log`

---

**Made for systematic options income generation.**

**Remember**: The Wheel works because it's simple, systematic, and has a statistical edge. Don't overcomplicate it.
