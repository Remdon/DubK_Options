# OpenBB API Availability Analysis & XAI Grok Alternative

## Executive Summary

The bot is currently experiencing API unavailability warnings for two critical TIER 1 features:
1. **Unusual Options Activity** - Smart money flow detection
2. **Earnings Calendar** - IV crush opportunity detection

**Root Cause**: These OpenBB endpoints require **paid provider subscriptions** (Intrinio for unusual options, FMP/Nasdaq for earnings).

**Solution**: Use XAI Grok API (already integrated) to fetch and analyze this data from alternative sources.

---

## Current Implementation Analysis

### 1. Unusual Options Activity (Line 182-305 in expert_scanner.py)

**Current Code**:
```python
def scan_unusual_options_activity(self, min_premium: int = 100000) -> List[Dict]:
    url = f'{self.openbb.base_url}/derivatives/options/unusual'

    # IMPORTANT: Only Intrinio supports unusual options endpoint
    providers = ['intrinio']  # ← Requires paid Intrinio subscription

    # ... tries providers, all fail

    logging.warning("Unusual options API unavailable from all providers - continuing without this data")
    return []
```

**Issue**: Intrinio requires a paid subscription (~$50-$200/month depending on tier).

**What We Lose**:
- Block trade detection (>$100k premium trades)
- Sweep detection (multi-exchange aggressive orders)
- Sentiment classification (bullish vs bearish institutional flows)
- +50% score boost for candidates with unusual activity

### 2. Earnings Calendar (Line 505-542 in expert_scanner.py)

**Current Code**:
```python
def scan_earnings_plays(self, upcoming_days: int = 30) -> List[Dict]:
    url = f'{self.openbb.base_url}/equity/calendar/earnings'

    # IMPORTANT: Supported providers are: fmp, nasdaq, seeking_alpha, tmx
    # YFinance does NOT support the earnings calendar endpoint
    providers = ['fmp', 'nasdaq', 'seeking_alpha', 'tmx']  # ← Require subscriptions

    # ... tries providers, all fail

    logging.warning("Earnings calendar API unavailable from all providers - continuing without this data")
    return []
```

**Issue**:
- FMP (Financial Modeling Prep) requires paid API ($29-$99/month)
- Nasdaq API requires registration/subscription
- Seeking Alpha requires premium
- TMX is Toronto Stock Exchange (limited US coverage)

**What We Lose**:
- Pre-earnings credit spread opportunities (high IV)
- Post-earnings debit spread opportunities (IV crush)
- Earnings risk filter (prevents selling puts 3 days before earnings)
- Priority symbols for "earnings plays" strategy

---

## Cost Analysis: OpenBB vs Free Alternative

### Option 1: Pay for OpenBB Data Providers

**Intrinio** (Unusual Options):
- Starter: $50/month (delayed data)
- Professional: $200/month (real-time)
- Enterprise: $500+/month (full suite)

**FMP** (Earnings Calendar):
- Starter: $29/month
- Professional: $99/month

**Total Cost**: $79-$299/month minimum

### Option 2: Use XAI Grok API (Already Integrated!)

**XAI Grok** (Per your account):
- Already configured in bot (XAI_API_KEY)
- Already being used for candidate analysis
- Pricing: Pay-per-use (~$0.01 per request for grok-4-fast)
- **Estimated additional cost**: $5-15/month for this feature

**Advantage**: Can fetch data from multiple free sources:
- Barchart (free tier: unusual options activity)
- MarketBeat (free: earnings calendar)
- Yahoo Finance (free: basic earnings dates)
- Finviz (free: screener data)
- TradingView (public data)

---

## Proposed Solution: XAI Grok Data Fetcher

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│  ExpertMarketScanner (TIER 1 Scans)                     │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  1. Try OpenBB API (existing code)                       │
│     ↓                                                     │
│  2. If fails → Call GrokDataFetcher                      │
│     ↓                                                     │
│  3. Grok scrapes free sources & returns structured data  │
│     ↓                                                     │
│  4. Continue with existing analysis logic                │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

### Implementation Plan

#### Step 1: Create GrokDataFetcher utility (NEW FILE)

**File**: `src/utils/grok_data_fetcher.py`

**Features**:
- `fetch_unusual_options(symbols: List[str]) -> List[Dict]`
  - Prompt Grok to check Barchart, MarketBeat, Finviz for unusual activity
  - Return structured data matching OpenBB format
  - Cache results for 1 hour

- `fetch_earnings_calendar(days: int = 30) -> List[Dict]`
  - Prompt Grok to scrape Yahoo Finance, MarketBeat earnings calendars
  - Return structured data matching OpenBB format
  - Cache results for 24 hours (earnings dates don't change frequently)

**Key Advantages**:
1. **Grok can browse the web** - It has access to real-time public data
2. **Structured output** - Can request JSON format matching OpenBB schema
3. **Multi-source aggregation** - Can check multiple free sources and synthesize
4. **Already integrated** - XAI_API_KEY already configured

#### Step 2: Modify ExpertMarketScanner to use fallback

**Changes to `expert_scanner.py`**:

```python
def scan_unusual_options_activity(self, min_premium: int = 100000) -> List[Dict]:
    # Try OpenBB first (existing code)
    try:
        # ... existing OpenBB logic ...
    except:
        logging.warning("OpenBB unusual options unavailable - trying Grok fallback")

    # FALLBACK: Use Grok to fetch from free sources
    if not data:
        grok_fetcher = GrokDataFetcher(config.XAI_API_KEY)
        data = grok_fetcher.fetch_unusual_options(symbols=self.get_watchlist())
        if data:
            logging.info(f"[GROK FALLBACK] Fetched unusual options from free sources via Grok")

    # Continue with existing processing logic
    # ... rest of method unchanged ...
```

Similar pattern for `scan_earnings_plays()`.

#### Step 3: Smart Caching to Minimize Costs

**Cache Strategy**:
- Unusual options: Cache for 1 hour (data changes throughout day)
- Earnings calendar: Cache for 24 hours (dates don't change)
- Store in SQLite: `data_cache.db`
- Only call Grok if cache expired

**Expected API calls**:
- Unusual options: ~8 calls/day (every 3 hours) = ~240 calls/month
- Earnings calendar: ~1 call/day = ~30 calls/month
- **Total**: ~270 Grok calls/month ≈ $2.70-$5.00/month

---

## Data Sources Grok Can Access (Free)

### Unusual Options Activity

**Barchart** (https://www.barchart.com/options/unusual-activity):
- Free tier shows top unusual options daily
- Volume, OI, premium data
- Bullish/bearish classification

**MarketBeat** (https://www.marketbeat.com/originals/unusual-options-activity/):
- Daily unusual options reports
- Block trades
- Institutional activity indicators

**Finviz** (https://finviz.com/):
- Unusual volume screener
- Options volume vs average
- Free real-time for basic data

### Earnings Calendar

**Yahoo Finance** (https://finance.yahoo.com/calendar/earnings):
- Free earnings calendar (30+ days)
- EPS estimates
- Report timing (BMO/AMC)

**MarketBeat** (https://www.marketbeat.com/earnings/):
- Comprehensive earnings calendar
- Free access
- Historical earnings data

**Nasdaq** (https://www.nasdaq.com/market-activity/earnings):
- Official earnings calendar
- Free for upcoming dates
- Includes conference call times

---

## Implementation Timeline

### Phase 1: Core Fetcher (2-3 hours)
- [ ] Create `GrokDataFetcher` class
- [ ] Implement `fetch_unusual_options()` with Grok web browsing
- [ ] Implement `fetch_earnings_calendar()` with Grok web browsing
- [ ] Add caching layer (SQLite)
- [ ] Unit tests

### Phase 2: Scanner Integration (1 hour)
- [ ] Modify `scan_unusual_options_activity()` to use fallback
- [ ] Modify `scan_earnings_plays()` to use fallback
- [ ] Add logging for data source tracking
- [ ] Verify data format compatibility

### Phase 3: Testing & Validation (1 hour)
- [ ] Test with live Grok API calls
- [ ] Verify structured data matches OpenBB format
- [ ] Confirm existing analysis logic works unchanged
- [ ] Monitor Grok API usage and costs

---

## Example Grok Prompts

### Unusual Options Activity Prompt

```
You are a financial data API. Fetch unusual options activity for today from free sources (Barchart, MarketBeat, Finviz).

Return ONLY valid JSON in this exact format:
{
  "results": [
    {
      "symbol": "TSLA",
      "contract_symbol": "TSLA250117C00300000",
      "total_premium": 1500000,
      "volume": 5000,
      "open_interest": 2000,
      "sentiment": "BULLISH",
      "trade_type": "sweep"
    }
  ]
}

Requirements:
- Only include trades with premium > $100,000
- Classify sentiment as BULLISH (calls) or BEARISH (puts)
- Include volume and open interest
- Return empty array if no data available
```

### Earnings Calendar Prompt

```
You are a financial data API. Fetch upcoming earnings dates for the next 30 days from free sources (Yahoo Finance, MarketBeat, Nasdaq).

Return ONLY valid JSON in this exact format:
{
  "results": [
    {
      "symbol": "TSLA",
      "report_date": "2025-01-29",
      "report_time": "AMC",
      "eps_estimate": 1.25,
      "revenue_estimate": 25000000000
    }
  ]
}

Requirements:
- Include symbols with market cap > $2B
- Report time should be "BMO" (before market open), "AMC" (after market close), or "UNKNOWN"
- Include EPS and revenue estimates if available
- Return empty array if no earnings in date range
```

---

## Recommendation

**YES**, you should implement the Grok fallback instead of paying for OpenBB subscriptions:

### Why Grok is Better:

1. **Cost**: $3-5/month vs $79-299/month for OpenBB providers
2. **Already integrated**: XAI_API_KEY already configured, `requests` already imported
3. **Flexibility**: Can aggregate multiple free sources vs single provider
4. **Future-proof**: If one free source changes, Grok can adapt to new sources
5. **Multi-use**: Same Grok integration can fetch other data (social sentiment, news, etc.)

### Risks (Low):

1. **Rate limits**: Grok has rate limits, but caching mitigates this
2. **Data quality**: Free sources may be delayed (15-20 min), but acceptable for daily scans
3. **Parsing errors**: Grok might return malformed JSON - add validation layer

### Next Steps:

1. **I can implement `GrokDataFetcher` now** if you approve this approach
2. **Test with your existing XAI_API_KEY** to verify it works
3. **Deploy to EC2** once validated
4. **Monitor costs** for first week to confirm estimate

---

## Alternative: Hybrid Approach

If you want best of both worlds:

- **Use Grok for earnings calendar** (very reliable, dates don't change)
- **Skip unusual options for now** (requires real-time data, harder to scrape)
- **Focus TIER 1 on short squeeze detection** (uses free short interest data)

This reduces scope while keeping most TIER 1 alpha generation features.

---

**Question for you**: Should I proceed with implementing the `GrokDataFetcher` class with earnings calendar + unusual options? Or start with just earnings calendar?
