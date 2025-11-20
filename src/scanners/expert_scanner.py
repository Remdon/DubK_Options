"""
Expert Market Scanner - Advanced Options Opportunity Detection

Scans for what expert options traders care about:
- Unusual options activity (volume/OI ratios)
- Greeks anomalies (high gamma, unusual delta distribution)
- IV rank extremes (very high or very low)
- Technical setups (support/resistance, breakouts)
- Put/call skew imbalances
- Large block trades
"""

import logging
import asyncio
import time
import requests
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from colorama import Fore, Style
import statistics

# Import Colors class
from src.core.colors import Colors

# Import Grok data fetcher for fallback data sources
from src.utils.grok_data_fetcher import GrokDataFetcher


# ============================================================================
# TIER 1 ALPHA GENERATION - CONFIGURATION CONSTANTS
# ============================================================================

# Unusual Options Activity
MIN_UNUSUAL_PREMIUM = 100000  # $100k minimum premium for block trades
SENTIMENT_BULLISH_THRESHOLD = 0.70  # 70%+ bullish trades = BULLISH signal
SENTIMENT_BEARISH_THRESHOLD = 0.70  # 70%+ bearish trades = BEARISH signal

# Short Squeeze Detection
SHORT_INTEREST_THRESHOLD = 0.20  # 20% of float
DAYS_TO_COVER_THRESHOLD = 5  # Minimum days to cover

# Dark Pool Activity
DARKPOOL_SPIKE_THRESHOLD = 2.0  # 2x average = spike
DARKPOOL_STRONG_SPIKE_THRESHOLD = 3.0  # 3x average = strong spike

# Earnings Plays
EARNINGS_PRE_WINDOW_START = 0  # Days before earnings to start monitoring
EARNINGS_PRE_WINDOW_END = 7    # Days before earnings to stop monitoring
EARNINGS_POST_WINDOW_START = -3  # Days after earnings to stop monitoring
EARNINGS_POST_WINDOW_END = 0   # Days after earnings to start monitoring
EARNINGS_HIGH_IV_THRESHOLD = 70  # IV rank > 70 = sell premium
EARNINGS_LOW_IV_THRESHOLD = 30   # IV rank < 30 = buy debit

# Market Regime Detection (per-symbol)
REGIME_UPTREND_THRESHOLD = 0.03  # 3% above SMA20
REGIME_SHORT_UPTREND_THRESHOLD = 0.01  # 1% short-term trend
REGIME_DOWNTREND_THRESHOLD = -0.03  # 3% below SMA20
REGIME_SHORT_DOWNTREND_THRESHOLD = -0.01  # 1% short-term trend
REGIME_HIGH_VOL_THRESHOLD = 0.30  # 30% annualized volatility
REGIME_LOW_VOL_THRESHOLD = 0.15  # 15% annualized volatility

# Scoring Boosts
BOOST_UNUSUAL_OPTIONS = 1.5   # +50% score boost for unusual options
BOOST_SQUEEZE_CANDIDATE = 1.3  # +30% score boost for squeeze candidates
BOOST_DARKPOOL_SPIKE = 1.25   # +25% score boost for dark pool spikes
BOOST_BULL_REGIME_CALL_HEAVY = 1.15  # +15% boost for call-heavy in bull regime
BOOST_BULL_REGIME_MOMENTUM = 1.10    # +10% boost for bullish momentum
BOOST_BEAR_REGIME_PUT_HEAVY = 1.15   # +15% boost for put-heavy in bear regime
BOOST_BEAR_REGIME_MOMENTUM = 1.10    # +10% boost for bearish momentum
BOOST_HIGH_VOL_PREMIUM_SELLING = 1.20  # +20% boost for high IV premium selling
BOOST_LOW_VOL_DEBIT_BUYING = 1.15     # +15% boost for low IV debit buying

# Put/Call Ratio Thresholds
PCR_CALL_HEAVY_THRESHOLD = 0.7  # PCR < 0.7 = call heavy (bullish)
PCR_PUT_HEAVY_THRESHOLD = 1.5   # PCR > 1.5 = put heavy (bearish)

# ============================================================================
# SPRINT 1: QUICK WINS - Additional Configuration Constants
# ============================================================================

# VWAP Analysis
VWAP_BULLISH_THRESHOLD = 0.005  # 0.5% above VWAP = bullish
VWAP_BEARISH_THRESHOLD = -0.005  # 0.5% below VWAP = bearish
VWAP_AT_THRESHOLD = 0.002  # Within 0.2% = at VWAP

# VIX Context (Market-Wide Volatility)
VIX_HIGH_THRESHOLD = 25.0  # VIX > 25 = high fear, sell premium
VIX_LOW_THRESHOLD = 15.0   # VIX < 15 = low fear, buy premium
VIX_EXTREME_HIGH = 35.0    # VIX > 35 = extreme fear, caution

# IV Percentile Bands (for better IV context)
IV_PERCENTILE_EXTREME_HIGH = 90  # >90th percentile = extremely high
IV_PERCENTILE_HIGH = 75          # >75th percentile = high
IV_PERCENTILE_MEDIUM = 50        # 50th percentile = median
IV_PERCENTILE_LOW = 25           # <25th percentile = low
IV_PERCENTILE_EXTREME_LOW = 10   # <10th percentile = extremely low

# ATR-Based Stop Loss Multipliers
ATR_STOP_LOSS_MULTIPLIER = 2.0   # Stop loss = entry - (ATR * 2.0)
ATR_PROFIT_TARGET_MULTIPLIER = 3.0  # Profit target = entry + (ATR * 3.0)

# Scoring Boosts for Sprint 1 Features
BOOST_VWAP_BULLISH = 1.08        # +8% boost for price > VWAP
BOOST_VWAP_BEARISH_PUT = 1.08    # +8% boost for price < VWAP (put plays)
BOOST_VIX_CONTEXT_MATCH = 1.10   # +10% boost for VIX-appropriate strategy
BOOST_SECTOR_STRENGTH = 1.12     # +12% boost for strong sector

# ============================================================================
# HIGH-IV WATCHLIST - Fallback for Wheel Strategy
# ============================================================================
# Stocks with consistently high IV (good for premium selling)
# Updated regularly based on market conditions
HIGH_IV_WATCHLIST = [
    # Large Cap Tech (high beta, liquid options)
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA', 'NVDA', 'AMD', 'NFLX',

    # Financial Services (liquid, institutional grade)
    'JPM', 'BAC', 'GS', 'MS', 'C', 'WFC', 'SCHW',

    # Healthcare (stable, good premiums)
    'JNJ', 'UNH', 'PFE', 'ABBV', 'TMO', 'ABT',

    # Consumer (brand names, liquid)
    'DIS', 'NKE', 'SBUX', 'MCD', 'HD', 'WMT', 'TGT',

    # Energy (high IV during volatility)
    'XOM', 'CVX', 'COP', 'SLB', 'EOG',

    # Communication Services
    'T', 'VZ', 'CMCSA',

    # Industrial
    'BA', 'CAT', 'GE', 'UPS', 'FDX',

    # ETFs (very liquid options)
    'SPY', 'QQQ', 'IWM', 'DIA', 'EEM', 'GLD', 'SLV', 'XLE', 'XLF', 'XLK',

    # Meme/High Volatility (premium gold mines)
    'GME', 'AMC', 'BBBY', 'PLTR', 'SNAP', 'HOOD', 'COIN', 'RIOT', 'MARA',

    # Semiconductor (high IV sector)
    'INTC', 'QCOM', 'AVGO', 'MU', 'TSM', 'ASML',

    # EV & Clean Energy
    'F', 'GM', 'LCID', 'RIVN', 'NIO', 'XPEV', 'ENPH', 'SEDG',

    # Retail
    'AMZN', 'ETSY', 'W', 'CHWY', 'CROX',
]


class ExpertMarketScanner:
    """
    Enhanced market scanner that looks for what expert options traders care about:
    1. Unusual options activity (volume/OI ratios)
    2. Greeks anomalies (high gamma, unusual delta distribution)
    3. IV rank extremes (very high or very low)
    4. Technical setups (support/resistance, breakouts)
    5. Put/call skew imbalances
    6. Large block trades
    """

    def __init__(self, openbb_client, iv_analyzer, earnings_calendar=None, grok_api_key=None):
        self.openbb = openbb_client
        self.iv_analyzer = iv_analyzer
        self.earnings_calendar = earnings_calendar
        self.market_regime = None  # TIER 2.2: Cache market regime for scan session

        # TIER 1 ALPHA GENERATION: Caches for smart money tracking
        self.unusual_options_cache = {}  # Cache unusual options activity
        self.institutional_cache = {}     # Cache institutional positioning
        self.short_interest_cache = {}    # Cache short squeeze candidates
        self.darkpool_cache = {}          # Cache dark pool activity

        # TIER 1 GROK FALLBACK: Alternative data fetcher when OpenBB providers unavailable
        self.grok_fetcher = None
        logging.info(f"[GROK DEBUG] Scanner received grok_api_key: {bool(grok_api_key)} (length: {len(grok_api_key) if grok_api_key else 0})")
        if grok_api_key:
            try:
                logging.info("[GROK DEBUG] Attempting to initialize GrokDataFetcher...")
                self.grok_fetcher = GrokDataFetcher(grok_api_key)
                logging.info("[GROK] Grok data fetcher initialized for fallback data sources")
            except Exception as e:
                logging.error(f"[GROK] Could not initialize Grok fetcher: {e}", exc_info=True)

        # SPRINT 1: QUICK WINS - Caches for enhanced analysis
        self.vix_value = None  # Cache VIX for session
        self.sector_cache = {}  # Cache sector data for symbols
        self.sector_performance_cache = {}  # Cache sector performance
        self.vwap_cache = {}  # Cache VWAP data for symbols

    # ========================================================================
    # TIER 1: ALPHA GENERATION - Smart Money Detection
    # ========================================================================

    def scan_unusual_options_activity(self, min_premium: int = 100000) -> List[Dict]:
        """
        TIER 1.1: Detect smart money flows via unusual options activity

        Returns trades with:
        - Block trades (>$100k premium)
        - Sweeps (aggressive multi-exchange orders)
        - Sentiment classification (bullish/bearish)

        This is THE edge most retail traders lack.
        """
        print(f"{Colors.HEADER}[TIER 1] Scanning unusual options activity...{Colors.RESET}")

        try:
            url = f'{self.openbb.base_url}/derivatives/options/unusual'

            # IMPORTANT: Only Intrinio supports unusual options endpoint
            # Other providers (yfinance, cboe) do NOT have this endpoint
            providers = ['intrinio']

            for provider in providers:
                try:
                    params = {'provider': provider}

                    # Add provider-specific params
                    if provider == 'intrinio':
                        params['min_value'] = min_premium
                        params['trade_type'] = 'sweep'

                    response = requests.get(url, params=params, timeout=60)

                    if response.status_code == 200:
                        logging.info(f"Unusual options data fetched from {provider}")
                        break
                    else:
                        logging.debug(f"Unusual options API returned {response.status_code} for {provider}")
                        continue

                except Exception as e:
                    logging.debug(f"Error fetching unusual options from {provider}: {e}")
                    continue
            else:
                # All OpenBB providers failed - try Grok fallback
                logging.warning("Unusual options API unavailable from all OpenBB providers")

                if self.grok_fetcher:
                    logging.info("[GROK FALLBACK] Attempting to fetch unusual options from free sources via Grok...")
                    try:
                        grok_data = self.grok_fetcher.fetch_unusual_options(min_premium=min_premium)
                        if grok_data:
                            # Convert Grok data to OpenBB format
                            data = grok_data
                            logging.info(f"[GROK FALLBACK] Successfully fetched {len(data)} unusual options from free sources")
                        else:
                            logging.warning("[GROK FALLBACK] No unusual options data available from free sources")
                            return []
                    except Exception as e:
                        logging.error(f"[GROK FALLBACK] Error fetching from Grok: {e}")
                        return []
                else:
                    logging.warning("Grok fallback not available - continuing without unusual options data")
                    return []

            if 'data' not in locals():
                data = response.json().get('results', [])

            # Process unusual activity
            unusual_trades = []
            symbol_clusters = {}  # Track multiple trades on same symbol

            for trade in data[:500]:  # Analyze recent 500 trades
                symbol = trade.get('underlying_symbol', trade.get('symbol', ''))
                if not symbol:
                    continue

                sentiment = trade.get('sentiment', 'neutral').lower()
                total_premium = trade.get('total_value', 0)
                trade_type = trade.get('trade_type', '')

                # Track clusters (multiple unusual trades on same symbol = strong signal)
                if symbol not in symbol_clusters:
                    symbol_clusters[symbol] = {
                        'count': 0,
                        'total_premium': 0,
                        'bullish_count': 0,
                        'bearish_count': 0,
                        'trades': []
                    }

                symbol_clusters[symbol]['count'] += 1
                symbol_clusters[symbol]['total_premium'] += total_premium
                if sentiment == 'bullish':
                    symbol_clusters[symbol]['bullish_count'] += 1
                elif sentiment == 'bearish':
                    symbol_clusters[symbol]['bearish_count'] += 1
                symbol_clusters[symbol]['trades'].append(trade)

            # Find high-conviction clusters (2+ trades, aligned sentiment)
            for symbol, cluster in symbol_clusters.items():
                if cluster['count'] >= 2:  # Multiple trades
                    # Check sentiment alignment
                    total = cluster['count']
                    bullish_pct = cluster['bullish_count'] / total
                    bearish_pct = cluster['bearish_count'] / total

                    if bullish_pct >= SENTIMENT_BULLISH_THRESHOLD:
                        sentiment = 'BULLISH'
                        conviction = bullish_pct
                    elif bearish_pct >= SENTIMENT_BEARISH_THRESHOLD:
                        sentiment = 'BEARISH'
                        conviction = bearish_pct
                    else:
                        sentiment = 'MIXED'
                        conviction = 0.5

                    unusual_trades.append({
                        'symbol': symbol,
                        'trade_count': cluster['count'],
                        'total_premium': cluster['total_premium'],
                        'sentiment': sentiment,
                        'conviction': conviction,
                        'signal_strength': cluster['count'] * conviction * (cluster['total_premium'] / 1000000),  # Combine factors
                        'trades': cluster['trades']
                    })

            # Sort by signal strength
            unusual_trades.sort(key=lambda x: x['signal_strength'], reverse=True)

            # Cache results
            self.unusual_options_cache = {t['symbol']: t for t in unusual_trades}

            print(f"{Colors.SUCCESS}Found {len(unusual_trades)} symbols with unusual activity{Colors.RESET}")
            if unusual_trades:
                for i, trade in enumerate(unusual_trades[:5]):
                    print(f"  {i+1}. {trade['symbol']}: {trade['trade_count']} trades, "
                          f"${trade['total_premium']/1e6:.1f}M premium, {trade['sentiment']}")

            return unusual_trades

        except Exception as e:
            logging.error(f"Error scanning unusual options: {e}")
            return []

    def analyze_institutional_ownership(self, symbol: str) -> Dict:
        """
        TIER 1.2: Track institutional holdings including options contracts

        Returns:
        - Call/put contracts held by institutions
        - Quarter-over-quarter changes
        - Number of institutions increasing positions
        """
        # Check cache first
        if symbol in self.institutional_cache:
            return self.institutional_cache[symbol]

        try:
            url = f'{self.openbb.base_url}/equity/ownership/institutional'
            params = {
                'provider': 'fmp',
                'symbol': symbol
            }

            response = requests.get(url, params=params, timeout=30)
            if response.status_code != 200:
                return {'error': f'API returned {response.status_code}'}

            data = response.json().get('results', [])
            if not data:
                return {'error': 'No institutional data available'}

            # Analyze institutional positioning
            total_holders = len(data)
            increasing_positions = sum(1 for d in data if d.get('change', 0) > 0)
            decreasing_positions = sum(1 for d in data if d.get('change', 0) < 0)

            # Calculate concentration (top 10 holders %)
            sorted_holdings = sorted(data, key=lambda x: x.get('shares', 0), reverse=True)
            top_10_shares = sum(d.get('shares', 0) for d in sorted_holdings[:10])
            total_shares = sum(d.get('shares', 0) for d in data)
            concentration = (top_10_shares / total_shares * 100) if total_shares > 0 else 0

            result = {
                'total_holders': total_holders,
                'increasing': increasing_positions,
                'decreasing': decreasing_positions,
                'net_sentiment': (increasing_positions - decreasing_positions) / total_holders if total_holders > 0 else 0,
                'concentration_top10_pct': concentration,
                'signal': 'BULLISH' if increasing_positions > decreasing_positions * 1.5 else 'BEARISH' if decreasing_positions > increasing_positions * 1.5 else 'NEUTRAL'
            }

            # Cache result
            self.institutional_cache[symbol] = result
            return result

        except Exception as e:
            logging.debug(f"Error analyzing institutional ownership for {symbol}: {e}")
            return {'error': str(e)}

    def scan_short_squeeze_candidates(self, symbols: List[str]) -> List[Dict]:
        """
        TIER 1.3: Identify potential short squeezes

        Criteria:
        - Short interest > 20% of float
        - Days to cover > 5
        - Recent unusual call buying (from unusual options)
        - Price breaking above resistance

        Short squeezes produce explosive moves - calls are best way to capture them.
        """
        print(f"{Colors.HEADER}[TIER 1] Scanning for short squeeze candidates...{Colors.RESET}")

        squeeze_candidates = []

        for symbol in symbols[:30]:  # Check top 30 symbols
            try:
                # Get short interest data
                url = f'{self.openbb.base_url}/equity/shorts/short_interest'
                params = {
                    'provider': 'stocksera',
                    'symbol': symbol
                }

                response = requests.get(url, params=params, timeout=15)
                if response.status_code != 200:
                    continue

                data = response.json().get('results', [])
                if not data:
                    continue

                latest = data[0]  # Most recent data
                short_interest = latest.get('short_interest', 0)
                short_pct = latest.get('short_percent_of_float', 0)
                days_to_cover = latest.get('days_to_cover', 0)

                # Squeeze criteria
                if short_pct > 20 and days_to_cover > 5:
                    # Check for unusual call buying
                    unusual_data = self.unusual_options_cache.get(symbol, {})
                    has_call_buying = unusual_data.get('sentiment') == 'BULLISH'

                    squeeze_score = (short_pct / 10) + (days_to_cover / 2)
                    if has_call_buying:
                        squeeze_score *= 1.5  # Big boost for unusual call buying

                    squeeze_candidates.append({
                        'symbol': symbol,
                        'short_pct': short_pct,
                        'days_to_cover': days_to_cover,
                        'has_unusual_calls': has_call_buying,
                        'squeeze_score': squeeze_score
                    })

                    # Cache result
                    self.short_interest_cache[symbol] = {
                        'short_pct': short_pct,
                        'days_to_cover': days_to_cover,
                        'squeeze_candidate': True
                    }

                time.sleep(0.5)  # Rate limiting

            except Exception as e:
                logging.debug(f"Error checking short interest for {symbol}: {e}")
                continue

        squeeze_candidates.sort(key=lambda x: x['squeeze_score'], reverse=True)

        print(f"{Colors.SUCCESS}Found {len(squeeze_candidates)} potential squeeze candidates{Colors.RESET}")
        if squeeze_candidates:
            for i, candidate in enumerate(squeeze_candidates[:5]):
                print(f"  {i+1}. {candidate['symbol']}: {candidate['short_pct']:.1f}% short, "
                      f"{candidate['days_to_cover']:.1f} DTC, "
                      f"{'WITH' if candidate['has_unusual_calls'] else 'NO'} unusual calls")

        return squeeze_candidates

    def analyze_darkpool_activity(self, symbol: str) -> Dict:
        """
        TIER 1.4: Dark pool activity often precedes large moves

        Signal:
        - Spike in dark pool volume (>2x average)
        - Followed by unusual options activity
        - → Institutions positioning before catalyst
        """
        # Check cache
        if symbol in self.darkpool_cache:
            return self.darkpool_cache[symbol]

        try:
            url = f'{self.openbb.base_url}/equity/darkpool/otc'
            params = {
                'provider': 'finra',
                'symbol': symbol
            }

            response = requests.get(url, params=params, timeout=30)
            if response.status_code != 200:
                return {'error': f'API returned {response.status_code}'}

            data = response.json().get('results', [])
            if len(data) < 10:
                return {'error': 'Insufficient dark pool data'}

            # Calculate average weekly volume
            recent_10_weeks = data[:10]
            weekly_volumes = [d.get('weekly_share_volume', 0) for d in recent_10_weeks]
            avg_volume = sum(weekly_volumes) / len(weekly_volumes)
            latest_volume = weekly_volumes[0]

            # Detect spike
            if avg_volume > 0:
                volume_ratio = latest_volume / avg_volume
                is_spike = volume_ratio > 2.0

                result = {
                    'latest_volume': latest_volume,
                    'avg_volume': avg_volume,
                    'volume_ratio': volume_ratio,
                    'is_spike': is_spike,
                    'signal': 'STRONG' if volume_ratio > 3.0 else 'MODERATE' if is_spike else 'NORMAL'
                }

                # Cache result
                self.darkpool_cache[symbol] = result
                return result

            return {'error': 'No volume data'}

        except Exception as e:
            logging.debug(f"Error analyzing dark pool for {symbol}: {e}")
            return {'error': str(e)}

    def scan_earnings_plays(self, upcoming_days: int = 30) -> List[Dict]:
        """
        TIER 1.5: Earnings plays with IV crush analysis

        Two strategies:
        1. PRE-EARNINGS (High IV): Sell credit spreads when IV rank > 70
        2. POST-EARNINGS (IV Crush): Buy debit spreads when IV rank drops < 30

        Earnings create predictable volatility patterns. IV crush is most reliable edge.
        """
        print(f"{Colors.HEADER}[TIER 1] Scanning earnings calendar...{Colors.RESET}")

        try:
            url = f'{self.openbb.base_url}/equity/calendar/earnings'

            # IMPORTANT: Supported providers are: fmp, nasdaq, seeking_alpha, tmx
            # YFinance does NOT support the earnings calendar endpoint
            providers = ['fmp', 'nasdaq', 'seeking_alpha', 'tmx']

            for provider in providers:
                try:
                    params = {
                        'provider': provider,
                        'start_date': datetime.now().strftime('%Y-%m-%d'),
                        'end_date': (datetime.now() + timedelta(days=upcoming_days)).strftime('%Y-%m-%d')
                    }

                    response = requests.get(url, params=params, timeout=30)

                    if response.status_code == 200:
                        logging.info(f"Earnings calendar data fetched from {provider}")
                        break
                    else:
                        logging.debug(f"Earnings calendar API returned {response.status_code} for {provider}")
                        continue

                except Exception as e:
                    logging.debug(f"Error fetching earnings from {provider}: {e}")
                    continue
            else:
                # All OpenBB providers failed - try Grok fallback
                logging.warning("Earnings calendar API unavailable from all OpenBB providers")

                if self.grok_fetcher:
                    logging.info("[GROK FALLBACK] Attempting to fetch earnings calendar from free sources via Grok...")
                    try:
                        grok_data = self.grok_fetcher.fetch_earnings_calendar(upcoming_days=upcoming_days)
                        if grok_data:
                            # Convert Grok data to OpenBB format
                            data = grok_data
                            logging.info(f"[GROK FALLBACK] Successfully fetched {len(data)} earnings events from free sources")
                        else:
                            logging.warning("[GROK FALLBACK] No earnings data available from free sources")
                            return []
                    except Exception as e:
                        logging.error(f"[GROK FALLBACK] Error fetching from Grok: {e}")
                        return []
                else:
                    logging.warning("Grok fallback not available - continuing without earnings data")
                    return []

            if 'data' not in locals():
                data = response.json().get('results', [])

            earnings_plays = []

            for earning in data[:100]:  # Analyze next 100 earnings
                symbol = earning.get('symbol', '')
                if not symbol:
                    continue

                report_date = earning.get('reportDate', '')
                if not report_date:
                    continue

                try:
                    report_date_obj = datetime.strptime(report_date, '%Y-%m-%d')
                    days_until = (report_date_obj - datetime.now()).days

                    # Get current IV rank for this symbol
                    try:
                        # Quick options chain fetch to get IV
                        chain = self.openbb.get_options_chains(symbol, provider='yfinance')
                        if chain and 'results' in chain:
                            options_data = chain['results']
                            ivs = [opt.get('implied_volatility', 0) for opt in options_data[:20] if opt.get('implied_volatility')]
                            if ivs:
                                avg_iv = statistics.mean(ivs)
                                iv_metrics = self.iv_analyzer.calculate_iv_metrics(symbol, avg_iv, options_data)
                                iv_rank = iv_metrics.get('iv_rank', 50)

                                # Determine strategy
                                if days_until <= EARNINGS_PRE_WINDOW_END and days_until > EARNINGS_PRE_WINDOW_START:
                                    # PRE-EARNINGS SETUP
                                    if iv_rank > EARNINGS_HIGH_IV_THRESHOLD:
                                        earnings_plays.append({
                                            'symbol': symbol,
                                            'days_until': days_until,
                                            'iv_rank': iv_rank,
                                            'strategy': 'SELL_PREMIUM',  # Sell credit spreads
                                            'reason': f'High IV rank {iv_rank:.0f}% before earnings - IV crush play',
                                            'report_date': report_date
                                        })
                                elif days_until < EARNINGS_POST_WINDOW_END and days_until > EARNINGS_POST_WINDOW_START:
                                    # POST-EARNINGS SETUP (recent report)
                                    if iv_rank < EARNINGS_LOW_IV_THRESHOLD:
                                        earnings_plays.append({
                                            'symbol': symbol,
                                            'days_until': abs(days_until),
                                            'iv_rank': iv_rank,
                                            'strategy': 'BUY_DEBIT',  # Buy cheap options after crush
                                            'reason': f'Low IV rank {iv_rank:.0f}% after earnings - cheap options',
                                            'report_date': report_date
                                        })
                    except:
                        pass  # Skip if can't get IV data

                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    logging.debug(f"Error processing earnings for {symbol}: {e}")
                    continue

            print(f"{Colors.SUCCESS}Found {len(earnings_plays)} earnings plays{Colors.RESET}")
            if earnings_plays:
                for i, play in enumerate(earnings_plays[:5]):
                    print(f"  {i+1}. {play['symbol']}: {play['strategy']}, "
                          f"IV rank {play['iv_rank']:.0f}%, "
                          f"{play['days_until']} days {'until' if play['days_until'] > 0 else 'since'} earnings")

            return earnings_plays

        except Exception as e:
            logging.error(f"Error scanning earnings: {e}")
            return []

    # ========================================================================
    # END TIER 1: ALPHA GENERATION
    # ========================================================================

    # ========================================================================
    # SPRINT 1: QUICK WINS - Enhanced Market Analysis
    # ========================================================================

    def analyze_vwap_position(self, symbol: str, current_price: float) -> Dict:
        """
        SPRINT 1.1: Analyze price position relative to VWAP

        VWAP is THE most important intraday indicator institutions watch.
        - Price > VWAP = bullish (institutions buying)
        - Price < VWAP = bearish (institutions selling)
        - VWAP bounces = support level

        Args:
            symbol: Stock symbol
            current_price: Current stock price

        Returns:
            Dict with VWAP analysis:
            - vwap: VWAP value
            - distance_from_vwap_pct: % above/below VWAP
            - position: 'ABOVE' | 'BELOW' | 'AT'
            - entry_quality: 'EXCELLENT' | 'GOOD' | 'FAIR' | 'POOR'
            - signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
        """
        try:
            # Check cache first
            if symbol in self.vwap_cache:
                cached_data = self.vwap_cache[symbol]
                cache_age = time.time() - cached_data.get('timestamp', 0)
                if cache_age < 300:  # 5 minutes cache
                    vwap_data = cached_data
                    logging.debug(f"{symbol}: Using cached VWAP data ({cache_age:.0f}s old)")
                else:
                    vwap_data = self.openbb.get_technical_vwap(symbol)
                    if vwap_data:
                        vwap_data['timestamp'] = time.time()
                        self.vwap_cache[symbol] = vwap_data
            else:
                vwap_data = self.openbb.get_technical_vwap(symbol)
                if vwap_data:
                    vwap_data['timestamp'] = time.time()
                    self.vwap_cache[symbol] = vwap_data

            if not vwap_data or 'results' not in vwap_data:
                return {'position': 'UNKNOWN', 'entry_quality': 'UNKNOWN', 'signal': 'NEUTRAL'}

            # Get most recent VWAP value
            results = vwap_data['results']
            if isinstance(results, list) and len(results) > 0:
                vwap = results[-1].get('vwap')
            elif isinstance(results, dict):
                vwap = results.get('vwap')
            else:
                return {'position': 'UNKNOWN', 'entry_quality': 'UNKNOWN', 'signal': 'NEUTRAL'}

            if not vwap or vwap == 0:
                return {'position': 'UNKNOWN', 'entry_quality': 'UNKNOWN', 'signal': 'NEUTRAL'}

            # Calculate distance from VWAP
            distance_pct = (current_price - vwap) / vwap

            # Determine position
            if distance_pct > VWAP_BULLISH_THRESHOLD:
                position = 'ABOVE'
                signal = 'BULLISH'
            elif distance_pct < VWAP_BEARISH_THRESHOLD:
                position = 'BELOW'
                signal = 'BEARISH'
            else:
                position = 'AT'
                signal = 'NEUTRAL'

            # Determine entry quality
            abs_distance = abs(distance_pct)
            if abs_distance < 0.003:  # Within 0.3% of VWAP
                entry_quality = 'EXCELLENT'  # Near VWAP = good entry
            elif abs_distance < 0.01:  # Within 1% of VWAP
                entry_quality = 'GOOD'
            elif abs_distance < 0.02:  # Within 2% of VWAP
                entry_quality = 'FAIR'
            else:
                entry_quality = 'POOR'  # Too far from VWAP

            return {
                'vwap': vwap,
                'distance_from_vwap_pct': distance_pct,
                'position': position,
                'entry_quality': entry_quality,
                'signal': signal
            }

        except Exception as e:
            logging.debug(f"Error analyzing VWAP for {symbol}: {e}")
            return {'position': 'UNKNOWN', 'entry_quality': 'UNKNOWN', 'signal': 'NEUTRAL'}

    def get_vix_context(self) -> Dict:
        """
        SPRINT 1.2: Get VIX context for market-wide volatility assessment

        VIX is the "fear gauge" - tells you when to sell vs buy premium:
        - VIX > 25 = High fear → SELL premium (credit spreads)
        - VIX < 15 = Low fear → BUY premium (debit spreads)
        - VIX > 35 = Extreme fear → CAUTION

        Returns:
            Dict with VIX analysis:
            - vix: Current VIX value
            - level: 'EXTREME_HIGH' | 'HIGH' | 'NORMAL' | 'LOW'
            - recommended_strategy: 'SELL_PREMIUM' | 'BUY_PREMIUM' | 'NEUTRAL' | 'CAUTION'
            - confidence: Confidence score (0-100)
        """
        try:
            # Use cached VIX if available (cache for entire scan session)
            if self.vix_value is not None:
                vix = self.vix_value
                logging.debug(f"Using cached VIX: {vix:.2f}")
            else:
                vix = self.openbb.get_vix()
                if vix is not None:
                    self.vix_value = vix
                    logging.info(f"VIX: {vix:.2f}")
                else:
                    logging.warning("Could not fetch VIX, using default")
                    return {
                        'vix': None,
                        'level': 'UNKNOWN',
                        'recommended_strategy': 'NEUTRAL',
                        'confidence': 0
                    }

            # Determine VIX level
            if vix >= VIX_EXTREME_HIGH:
                level = 'EXTREME_HIGH'
                recommended_strategy = 'CAUTION'
                confidence = 95  # Very high confidence to avoid risk
            elif vix >= VIX_HIGH_THRESHOLD:
                level = 'HIGH'
                recommended_strategy = 'SELL_PREMIUM'
                confidence = 80  # High confidence to sell premium
            elif vix <= VIX_LOW_THRESHOLD:
                level = 'LOW'
                recommended_strategy = 'BUY_PREMIUM'
                confidence = 75  # Good confidence to buy premium
            else:
                level = 'NORMAL'
                recommended_strategy = 'NEUTRAL'
                confidence = 50  # Neutral market

            return {
                'vix': vix,
                'level': level,
                'recommended_strategy': recommended_strategy,
                'confidence': confidence
            }

        except Exception as e:
            logging.error(f"Error getting VIX context: {e}")
            return {
                'vix': None,
                'level': 'UNKNOWN',
                'recommended_strategy': 'NEUTRAL',
                'confidence': 0
            }

    def get_sector_classification(self, symbol: str) -> Dict:
        """
        SPRINT 1.3: Get sector and industry classification

        Used for sector rotation tracking - follow the money between sectors

        Args:
            symbol: Stock symbol

        Returns:
            Dict with sector data:
            - sector: Sector name
            - industry: Industry name
            - beta: Stock beta (volatility vs market)
            - sector_strength: Sector performance ranking
        """
        try:
            # Check cache first
            if symbol in self.sector_cache:
                cached_data = self.sector_cache[symbol]
                cache_age = time.time() - cached_data.get('timestamp', 0)
                if cache_age < 3600:  # 1 hour cache (sector doesn't change often)
                    return cached_data

            # Fetch profile data
            profile_data = self.openbb.get_equity_profile(symbol)

            if not profile_data or 'results' not in profile_data:
                return {
                    'sector': 'UNKNOWN',
                    'industry': 'UNKNOWN',
                    'beta': 1.0,
                    'sector_strength': 'NEUTRAL'
                }

            results = profile_data['results']
            if isinstance(results, list) and len(results) > 0:
                profile = results[0]
            elif isinstance(results, dict):
                profile = results
            else:
                return {
                    'sector': 'UNKNOWN',
                    'industry': 'UNKNOWN',
                    'beta': 1.0,
                    'sector_strength': 'NEUTRAL'
                }

            sector = profile.get('sector', 'UNKNOWN')
            industry = profile.get('industry', 'UNKNOWN')
            beta = profile.get('beta', 1.0)

            # Determine sector strength (placeholder for now, will enhance later)
            sector_strength = 'NEUTRAL'

            result = {
                'sector': sector,
                'industry': industry,
                'beta': beta,
                'sector_strength': sector_strength,
                'timestamp': time.time()
            }

            # Cache result
            self.sector_cache[symbol] = result

            return result

        except Exception as e:
            logging.debug(f"Error getting sector classification for {symbol}: {e}")
            return {
                'sector': 'UNKNOWN',
                'industry': 'UNKNOWN',
                'beta': 1.0,
                'sector_strength': 'NEUTRAL'
            }

    def enhance_iv_rank_with_percentiles(self, iv_rank: float, symbol: str) -> Dict:
        """
        SPRINT 1.4: Enhance IV rank with percentile bands

        IV rank alone doesn't tell the full story - a "60" IV rank could be:
        - Normal for a volatile tech stock
        - Extremely high for a stable utility stock

        Args:
            iv_rank: Current IV rank (0-100)
            symbol: Stock symbol

        Returns:
            Dict with enhanced IV analysis:
            - iv_rank: Original IV rank
            - percentile_band: 'EXTREME_HIGH' | 'HIGH' | 'MEDIUM' | 'LOW' | 'EXTREME_LOW'
            - recommendation: 'STRONG_SELL_PREMIUM' | 'SELL_PREMIUM' | 'NEUTRAL' | 'BUY_PREMIUM' | 'STRONG_BUY_PREMIUM'
            - confidence: Confidence in recommendation (0-100)
        """
        try:
            # Determine percentile band
            if iv_rank >= IV_PERCENTILE_EXTREME_HIGH:
                percentile_band = 'EXTREME_HIGH'
                recommendation = 'STRONG_SELL_PREMIUM'
                confidence = 95
            elif iv_rank >= IV_PERCENTILE_HIGH:
                percentile_band = 'HIGH'
                recommendation = 'SELL_PREMIUM'
                confidence = 80
            elif iv_rank >= IV_PERCENTILE_MEDIUM:
                percentile_band = 'MEDIUM'
                recommendation = 'NEUTRAL'
                confidence = 50
            elif iv_rank >= IV_PERCENTILE_LOW:
                percentile_band = 'LOW'
                recommendation = 'BUY_PREMIUM'
                confidence = 75
            else:
                percentile_band = 'EXTREME_LOW'
                recommendation = 'STRONG_BUY_PREMIUM'
                confidence = 90

            return {
                'iv_rank': iv_rank,
                'percentile_band': percentile_band,
                'recommendation': recommendation,
                'confidence': confidence
            }

        except Exception as e:
            logging.debug(f"Error enhancing IV rank for {symbol}: {e}")
            return {
                'iv_rank': iv_rank,
                'percentile_band': 'MEDIUM',
                'recommendation': 'NEUTRAL',
                'confidence': 50
            }

    # ========================================================================
    # END SPRINT 1: QUICK WINS
    # ========================================================================

    def _detect_market_regime(self, symbol: str) -> Dict:
        """
        TIER 2.2: Detect market regime for SPECIFIC SYMBOL (not SPY)

        This provides stock-specific trend and volatility analysis for adaptive scoring.
        Using per-symbol detection ensures we capture the actual stock's behavior,
        not the broad market which may be divergent.

        Args:
            symbol: The stock symbol to analyze (e.g., 'AAPL', 'NVDA')

        Returns:
            Dict with regime, strength, volatility, trend_pct, annualized_vol
        """
        try:
            # Get THIS symbol's data for last 20 trading days
            symbol_data = self.openbb.get_historical_price(symbol, days=20)
            if not symbol_data or 'results' not in symbol_data:
                logging.warning(f"Could not fetch {symbol} data for regime detection, using NEUTRAL")
                return {'regime': 'NEUTRAL', 'strength': 0.5, 'volatility': 'NORMAL'}

            results = symbol_data['results']
            if len(results) < 10:
                return {'regime': 'NEUTRAL', 'strength': 0.5, 'volatility': 'NORMAL'}

            # Calculate metrics
            closes = [r.get('close', 0) for r in results if r.get('close')]
            if len(closes) < 10:
                return {'regime': 'NEUTRAL', 'strength': 0.5, 'volatility': 'NORMAL'}

            current_price = closes[-1]
            sma_10 = sum(closes[-10:]) / 10
            sma_20 = sum(closes) / len(closes)

            # Trend detection
            trend_pct = (current_price - sma_20) / sma_20
            short_trend_pct = (sma_10 - sma_20) / sma_20

            # Volatility detection (daily returns std)
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            import statistics
            vol = statistics.stdev(returns) if len(returns) > 1 else 0
            annualized_vol = vol * (252 ** 0.5)

            # Determine regime
            if trend_pct > REGIME_UPTREND_THRESHOLD and short_trend_pct > REGIME_SHORT_UPTREND_THRESHOLD:  # Strong uptrend
                regime = 'BULL'
                strength = min(abs(trend_pct) * 20, 1.0)  # 0.0 to 1.0
            elif trend_pct < REGIME_DOWNTREND_THRESHOLD and short_trend_pct < REGIME_SHORT_DOWNTREND_THRESHOLD:  # Strong downtrend
                regime = 'BEAR'
                strength = min(abs(trend_pct) * 20, 1.0)
            else:
                regime = 'NEUTRAL'
                strength = 0.5

            # Volatility regime
            if annualized_vol > REGIME_HIGH_VOL_THRESHOLD:
                vol_regime = 'HIGH'
            elif annualized_vol < REGIME_LOW_VOL_THRESHOLD:
                vol_regime = 'LOW'
            else:
                vol_regime = 'NORMAL'

            regime_data = {
                'regime': regime,
                'strength': strength,
                'volatility': vol_regime,
                'trend_pct': trend_pct,
                'annualized_vol': annualized_vol
            }

            logging.debug(f"TIER 2.2: {symbol} regime: {regime} (strength {strength:.2f}), Volatility: {vol_regime} ({annualized_vol:.1%})")
            return regime_data

        except Exception as e:
            logging.error(f"Error detecting regime for {symbol}: {e}")
            return {'regime': 'NEUTRAL', 'strength': 0.5, 'volatility': 'NORMAL'}

    async def scan_market_for_opportunities_async(self) -> List[Dict]:
        """Async market scan with TIER 1 alpha generation + SPRINT 1 enhancements"""
        print(f"{Colors.HEADER}[EXPERT SCAN] Multi-factor options analysis with SMART MONEY detection...{Colors.RESET}\n")

        # ═════════════════════════════════════════════════════════════════════
        # SPRINT 1: Get VIX context for entire scan session
        # ═════════════════════════════════════════════════════════════════════
        print(f"{Colors.INFO}[SPRINT 1] Fetching market context...{Colors.RESET}")
        vix_context = self.get_vix_context()
        if vix_context['vix']:
            print(f"{Colors.INFO}VIX: {vix_context['vix']:.2f} ({vix_context['level']}) → "
                  f"Strategy: {vix_context['recommended_strategy']}{Colors.RESET}\n")
        else:
            print(f"{Colors.WARNING}VIX data unavailable{Colors.RESET}\n")

        # ═════════════════════════════════════════════════════════════════════
        # TIER 1: ALPHA GENERATION - Run smart money scans FIRST
        # ═════════════════════════════════════════════════════════════════════
        print(f"{Colors.HEADER}═══════════════════════════════════════════════════════════════════════{Colors.RESET}")
        print(f"{Colors.HEADER}TIER 1: SMART MONEY DETECTION{Colors.RESET}")
        print(f"{Colors.HEADER}═══════════════════════════════════════════════════════════════════════{Colors.RESET}\n")

        # Run Tier 1 scanners
        unusual_options = self.scan_unusual_options_activity(min_premium=MIN_UNUSUAL_PREMIUM)
        earnings_plays = self.scan_earnings_plays(upcoming_days=30)

        # Extract symbols from Tier 1 results for priority processing
        tier1_priority_symbols = set()
        if unusual_options:
            tier1_priority_symbols.update([t['symbol'] for t in unusual_options[:20]])  # Top 20 unusual
        if earnings_plays:
            tier1_priority_symbols.update([e['symbol'] for e in earnings_plays[:10]])  # Top 10 earnings

        print(f"\n{Colors.SUCCESS}[TIER 1 COMPLETE] {len(tier1_priority_symbols)} priority symbols identified{Colors.RESET}")
        print(f"{Colors.HEADER}═══════════════════════════════════════════════════════════════════════{Colors.RESET}\n")

        # TIER 2.2: Per-symbol regime detection will be done during scoring
        # (removed global SPY-based regime detection - each stock analyzed individually)

        # Step 1: Get base universe of stocks (fast)
        print(f"{Colors.DIM}[1/5] Building stock universe...{Colors.RESET}")
        stock_universe = await self._build_stock_universe()

        if not stock_universe:
            return []

        # Step 2: Pre-filter by stock metrics (fast, no API calls)
        print(f"{Colors.DIM}[2/5] Pre-filtering {len(stock_universe)} candidates...{Colors.RESET}")
        filtered_stocks = self._pre_filter_stocks(stock_universe)

        print(f"{Colors.INFO}Pre-filtered to {len(filtered_stocks)} candidates{Colors.RESET}")

        # Step 3: Concurrent options analysis (FAST with async)
        print(f"{Colors.DIM}[3/5] Analyzing options chains (concurrent)...{Colors.RESET}")
        candidates = await self._analyze_options_concurrent(filtered_stocks)

        # Step 4: Score and rank by expert criteria (TIER 2.2: regime-adaptive)
        print(f"{Colors.DIM}[4/5] Scoring opportunities by expert criteria...{Colors.RESET}")
        scored_candidates = self._score_by_expert_criteria(candidates)

        print(f"{Colors.DIM}[5/5] Finalizing top candidates...{Colors.RESET}")

        # Return top 50
        top_50 = scored_candidates[:50]

        print(f"\n{Colors.SUCCESS}[SCAN COMPLETE] Found {len(top_50)} high-quality opportunities{Colors.RESET}\n")
        self._display_top_opportunities(top_50)

        return top_50

    async def _build_stock_universe(self) -> List[Dict]:
        """Build universe from multiple sources - TIER 2.1: Expanded to 7 sources"""
        all_stocks = []

        # SOURCE 1: Active stocks (most traded)
        try:
            url = f'{self.openbb.base_url}/equity/discovery/active'
            response = requests.get(url, params={'provider': 'yfinance', 'limit': 100}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'active'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} active stocks")
        except Exception as e:
            logging.error(f"Error fetching active stocks: {e}")

        # SOURCE 2: Unusual volume
        try:
            url = f'{self.openbb.base_url}/equity/screener'
            response = requests.get(url, params={'provider': 'yfinance', 'signal': 'unusual_volume', 'limit': 100}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'unusual_volume'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} unusual volume stocks")
        except Exception as e:
            logging.error(f"Error fetching unusual volume: {e}")

        # SOURCE 3: Top gainers
        try:
            url = f'{self.openbb.base_url}/equity/discovery/gainers'
            response = requests.get(url, params={'provider': 'yfinance', 'limit': 50}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'gainers'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} top gainers")
        except Exception as e:
            logging.error(f"Error fetching gainers: {e}")

        # SOURCE 4: Top losers
        try:
            url = f'{self.openbb.base_url}/equity/discovery/losers'
            response = requests.get(url, params={'provider': 'yfinance', 'limit': 50}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'losers'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} top losers")
        except Exception as e:
            logging.error(f"Error fetching losers: {e}")

        # SOURCE 5: High IV rank stocks (options-focused)
        # Use screener to find stocks with high volatility
        try:
            url = f'{self.openbb.base_url}/equity/screener'
            response = requests.get(url, params={'provider': 'yfinance', 'signal': 'most_volatile', 'limit': 50}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'high_volatility'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} high volatility stocks")
        except Exception as e:
            logging.error(f"Error fetching high volatility stocks: {e}")

        # SOURCE 6: Oversold stocks (potential bounce plays)
        try:
            url = f'{self.openbb.base_url}/equity/screener'
            response = requests.get(url, params={'provider': 'yfinance', 'signal': 'oversold', 'limit': 30}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'oversold'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} oversold stocks")
        except Exception as e:
            logging.error(f"Error fetching oversold stocks: {e}")

        # SOURCE 7: Overbought stocks (potential reversal plays)
        try:
            url = f'{self.openbb.base_url}/equity/screener'
            response = requests.get(url, params={'provider': 'yfinance', 'signal': 'overbought', 'limit': 30}, timeout=30)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for stock in results:
                    stock['source'] = 'overbought'
                all_stocks.extend(results)
                logging.info(f"TIER 2.1: Fetched {len(results)} overbought stocks")
        except Exception as e:
            logging.error(f"Error fetching overbought stocks: {e}")

        # Deduplicate and track sources
        unique_stocks = {}
        for stock in all_stocks:
            symbol = stock.get('symbol')
            if symbol and symbol not in unique_stocks:
                unique_stocks[symbol] = stock
            elif symbol:
                # Track multiple sources for same stock
                existing_source = unique_stocks[symbol].get('source', '')
                new_source = stock.get('source', '')
                if new_source not in existing_source:
                    unique_stocks[symbol]['source'] = f"{existing_source},{new_source}"

        logging.info(f"TIER 2.1: Total unique stocks from API sources: {len(unique_stocks)}")

        # FALLBACK: If API returned few/no stocks, use HIGH_IV_WATCHLIST
        if len(unique_stocks) < 50:
            logging.warning(f"TIER 2.1: API returned only {len(unique_stocks)} stocks, adding HIGH_IV_WATCHLIST as fallback")
            for symbol in HIGH_IV_WATCHLIST:
                if symbol not in unique_stocks:
                    unique_stocks[symbol] = {
                        'symbol': symbol,
                        'source': 'high_iv_watchlist',
                        'price': None  # Will be fetched later
                    }
            logging.info(f"TIER 2.1: Universe expanded to {len(unique_stocks)} stocks with watchlist")

        return list(unique_stocks.values())

    def _pre_filter_stocks(self, stocks: List[Dict]) -> List[Dict]:
        """Filter stocks likely to have liquid options"""
        filtered = []

        for stock in stocks:
            symbol = stock.get('symbol', '')

            # Filter out non-optionable stocks
            if symbol.endswith('F') and len(symbol) > 4:
                continue
            if len(symbol) > 5:  # OTC stocks
                continue

            price = stock.get('price', 0) or 0
            if price < 2:  # Penny stocks
                continue

            volume = stock.get('volume', 0) or 0
            if volume < 500000:  # Very low volume
                continue

            # TIER 1.2: EARNINGS PROXIMITY FILTER
            # Check for earnings risk to prevent IV crush losses
            try:
                earnings_risk = self.earnings_calendar.check_earnings_risk(symbol)
                risk_level = earnings_risk.get('risk', 'UNKNOWN')
                days_until = earnings_risk.get('days_until', 99)

                # AUTO-REJECT: Earnings within 3 days (HIGH risk)
                if risk_level == 'HIGH' and days_until < 3:
                    logging.debug(f"Skipping {symbol}: earnings in {days_until} days (IV crush risk)")
                    continue  # Skip this stock entirely

            except Exception as e:
                # If earnings check fails, continue (don't block on this)
                logging.debug(f"Could not check earnings for {symbol}: {e}")
                earnings_risk = {'risk': 'UNKNOWN', 'days_until': 99}

            # Calculate pre-score
            score = 0
            pct_change = abs(stock.get('percent_change', 0) or 0)

            # Price movement
            if pct_change > 0.10:
                score += 50
            elif pct_change > 0.05:
                score += 30
            elif pct_change > 0.03:
                score += 15

            # Volume
            if volume > 50000000:
                score += 30
            elif volume > 20000000:
                score += 20
            elif volume > 10000000:
                score += 10

            # Price range (options sweet spot)
            if 10 <= price <= 500:
                score += 20
            elif 5 <= price <= 1000:
                score += 10

            # TIER 1.2: Apply earnings penalty for MODERATE risk (3-7 days)
            if earnings_risk.get('risk') == 'MODERATE':
                days_until = earnings_risk.get('days_until', 99)
                if 3 <= days_until <= 7:
                    score -= 20  # Penalty for near-term earnings
                    logging.debug(f"{symbol}: -20 score penalty for earnings in {days_until} days")

            if score > 30:  # Threshold
                stock['pre_score'] = score
                stock['earnings_risk'] = earnings_risk  # Store for later reference
                filtered.append(stock)

        # Sort by pre-score and take top 75
        filtered.sort(key=lambda x: x['pre_score'], reverse=True)
        return filtered[:75]

    async def _analyze_options_concurrent(self, stocks: List[Dict]) -> List[Dict]:
        """Analyze options chains concurrently for speed"""
        # For simplicity in sync environment, we'll do sequential with better progress
        # In true async, would use aiohttp

        candidates = []
        total = len(stocks)
        failed = 0
        rate_limit_failed = 0  # Track rate limit-specific failures
        success = 0
        last_symbols = []

        for i, stock in enumerate(stocks):
            symbol = stock['symbol']

            # Progress display
            progress_pct = (i + 1) / total * 100
            progress_bar = '=' * int(progress_pct / 5) + '-' * (20 - int(progress_pct / 5))
            recent = ', '.join(last_symbols[-5:]) if last_symbols else 'none yet'

            print(f"\r{Colors.DIM}Progress: [{progress_bar}] {i+1}/{total} | "
                  f"OK:{success} FAIL:{failed} | Current: {symbol:6s} | Recent: {recent:30s}{Colors.RESET}",
                  end='', flush=True)

            try:
                # Get options chain
                chain = self.openbb.get_options_chains(symbol, provider='yfinance')
                if not chain or 'results' not in chain:
                    failed += 1
                    continue

                options_data = chain['results']

                # Analyze this opportunity (pass options_data to avoid duplicate API calls)
                analysis = self._analyze_options_chain(symbol, options_data, stock, options_data)

                if analysis['score'] > 0:
                    # TIER 1.4: Capture timestamp for data freshness tracking
                    import time
                    # Note: Sector-based limiting removed - using symbol-level and Greeks limits instead
                    candidates.append({
                        'symbol': symbol,
                        'score': analysis['score'],
                        'options_data': options_data,
                        'analysis': analysis,
                        'stock_data': stock,
                        'data_timestamp': time.time()  # TIER 1.4: Track when data was fetched
                    })
                    success += 1
                    last_symbols.append(symbol)

                # Smart rate limiting - only increase for actual rate limit errors
                # Regular requests still rate-limited to prevent hitting limits
                sleep_time = 0.15  # Base rate limiting for all requests

                if rate_limit_failed > 0:  # Scale up for rate limiting errors
                    if rate_limit_failed > 5:
                        sleep_time = 2.0  # Heavy rate limiting
                    elif rate_limit_failed > 3:
                        sleep_time = 1.0  # Moderate rate limiting
                    elif rate_limit_failed > 1:
                        sleep_time = 0.5  # Light rate limiting

                time.sleep(sleep_time)

            except Exception as e:
                failed += 1
                error_str = str(e).lower()
                logging.debug(f"Error analyzing {symbol}: {e}")

                # Check if this is a rate limiting error (only then extend sleep)
                is_rate_limit_error = (
                    'rate limit' in error_str or
                    'too many requests' in error_str or
                    '429' in error_str or
                    'timeout' in error_str or
                    'time out' in error_str
                )

                if is_rate_limit_error:
                    rate_limit_failed += 1
                    # Longer sleep for rate limit errors
                    time.sleep(1.0)
                else:
                    # Regular rate limiting for non-rate-limit errors
                    time.sleep(0.2)

        print()  # New line after progress
        return candidates

    def _analyze_options_chain(self, symbol: str, options_data: List[Dict], stock_data: Dict, options_chain_for_iv: List[Dict] = None) -> Dict:
        """Expert-level options chain analysis"""
        if not options_data:
            return {'score': 0}

        score = 0
        signals = []

        # Extract key metrics
        calls = [opt for opt in options_data if opt.get('option_type') == 'call']
        puts = [opt for opt in options_data if opt.get('option_type') == 'put']

        # 1. UNUSUAL VOLUME/OI ANALYSIS
        unusual_volume_score = 0
        for option in options_data[:50]:
            volume = option.get('volume', 0) or 0
            oi = option.get('open_interest', 0) or 0

            if oi > 0:
                vol_oi_ratio = volume / oi
                if vol_oi_ratio > 0.5:  # Volume is 50%+ of OI = unusual
                    unusual_volume_score += 10

        score += min(unusual_volume_score, 50)
        if unusual_volume_score > 30:
            signals.append("UNUSUAL_VOLUME")

        # 2. GREEKS ANALYSIS
        deltas = [opt.get('delta', 0) for opt in options_data[:50] if opt.get('delta')]
        gammas = [opt.get('gamma', 0) for opt in options_data[:50] if opt.get('gamma')]

        if gammas:
            avg_gamma = statistics.mean(gammas)
            if avg_gamma > 0.05:  # High gamma = explosive moves possible
                score += 20
                signals.append("HIGH_GAMMA")

        # 3. IV ANALYSIS
        ivs = [opt.get('implied_volatility', 0) for opt in options_data[:50] if opt.get('implied_volatility')]
        if ivs:
            avg_iv = statistics.mean(ivs)
            # Pass options_chain to avoid duplicate API call
            iv_metrics = self.iv_analyzer.calculate_iv_metrics(symbol, avg_iv, options_chain_for_iv or options_data)

            iv_rank = iv_metrics['iv_rank']

            # Extreme IV situations
            if iv_rank > 75:
                score += 25
                signals.append("HIGH_IV_RANK")
            elif iv_rank < 25:
                score += 25
                signals.append("LOW_IV_RANK")
        else:
            avg_iv = 0
            iv_metrics = {}

        # 4. PUT/CALL IMBALANCE
        call_volume = sum(opt.get('volume', 0) or 0 for opt in calls)
        put_volume = sum(opt.get('volume', 0) or 0 for opt in puts)

        if call_volume > 0:
            put_call_ratio = put_volume / call_volume

            # Extreme imbalances are interesting
            if put_call_ratio > 2.0:  # Heavy put buying = fear
                score += 15
                signals.append("PUT_HEAVY")
            elif put_call_ratio < 0.5:  # Heavy call buying = greed
                score += 15
                signals.append("CALL_HEAVY")
        else:
            put_call_ratio = 0

        # 5. STOCK MOMENTUM
        pct_change = abs(stock_data.get('percent_change', 0) or 0)
        if pct_change > 0.10:
            score += 30
            signals.append("BIG_MOVE")
        elif pct_change > 0.05:
            score += 20
            signals.append("STRONG_MOVE")

        # 6. LIQUIDITY
        total_volume = sum(opt.get('volume', 0) or 0 for opt in options_data[:50])
        total_oi = sum(opt.get('open_interest', 0) or 0 for opt in options_data[:50])

        if total_volume > 10000:
            score += 20
        if total_oi > 50000:
            score += 20

        # TIER 2.3 & 2.4: ADVANCED OPTIONS METRICS
        price = stock_data.get('price', 0) or 0
        iv_skew = 0
        max_pain_strike = 0
        strike_concentration_score = 0

        # TIER 2.4: STRIKE CONCENTRATION ANALYSIS (Max Pain)
        # Identify strikes with heavy OI concentration
        if price > 0 and options_data:
            try:
                # Group OI by strike
                strike_oi = {}
                for opt in options_data:
                    strike = opt.get('strike', 0)
                    oi = opt.get('open_interest', 0) or 0
                    if strike > 0 and oi > 0:
                        if strike not in strike_oi:
                            strike_oi[strike] = 0
                        strike_oi[strike] += oi

                if strike_oi:
                    total_oi_all_strikes = sum(strike_oi.values())

                    # Find strikes with >20% of total OI
                    concentrated_strikes = []
                    for strike, oi in strike_oi.items():
                        concentration = oi / total_oi_all_strikes if total_oi_all_strikes > 0 else 0
                        if concentration > 0.20:  # >20% concentration
                            concentrated_strikes.append({
                                'strike': strike,
                                'oi': oi,
                                'concentration': concentration
                            })

                    # Find max pain (strike with most total OI)
                    if concentrated_strikes:
                        max_pain_strike = max(concentrated_strikes, key=lambda x: x['oi'])['strike']

                        # Score based on proximity to concentrated strikes
                        for conc in concentrated_strikes:
                            strike_distance_pct = abs(conc['strike'] - price) / price
                            if strike_distance_pct < 0.05:  # Within 5% of price
                                strike_concentration_score += 15
                                signals.append("MAX_PAIN_ZONE")
                                logging.debug(f"{symbol}: Strike ${conc['strike']:.0f} has {conc['concentration']:.0%} OI (max pain zone)")
                                break

            except Exception as e:
                logging.debug(f"Could not calculate strike concentration for {symbol}: {e}")

        # TIER 2.3: IV SKEW ANALYSIS
        if price > 0 and calls and puts:
            try:
                import statistics as stats
                # OTM calls: 5-15% above current price
                otm_calls = [opt for opt in calls
                            if opt.get('strike', 0) > 0 and
                            1.05 < opt.get('strike', 0) / price < 1.15 and
                            opt.get('implied_volatility', 0) > 0]
                # OTM puts: 5-15% below current price
                otm_puts = [opt for opt in puts
                           if opt.get('strike', 0) > 0 and
                           0.85 < opt.get('strike', 0) / price < 0.95 and
                           opt.get('implied_volatility', 0) > 0]

                if otm_calls and otm_puts:
                    call_iv_avg = stats.mean([opt.get('implied_volatility', 0) for opt in otm_calls])
                    put_iv_avg = stats.mean([opt.get('implied_volatility', 0) for opt in otm_puts])
                    iv_skew = put_iv_avg - call_iv_avg

                    # Score based on skew
                    if iv_skew > 0.10:  # Strong put skew (>10pp) = fear/hedging
                        score += 15
                        signals.append("PUT_SKEW")
                    elif iv_skew < -0.10:  # Strong call skew (<-10pp) = complacency
                        score += 10
                        signals.append("CALL_SKEW")
            except Exception as e:
                logging.debug(f"Could not calculate IV skew for {symbol}: {e}")
                iv_skew = 0

        # TIER 1.1: BID-ASK SPREAD QUALITY GATE
        # Calculate average bid-ask spread for ATM options to assess execution quality
        atm_spreads = []
        avg_spread_pct = 0

        if price > 0:
            # Find ATM options (within 5% of current price)
            atm_options = [
                opt for opt in options_data
                if opt.get('strike', 0) > 0 and
                0.95 <= opt.get('strike', 0) / price <= 1.05 and
                opt.get('bid', 0) > 0 and opt.get('ask', 0) > 0
            ]

            for opt in atm_options[:20]:  # Check up to 20 ATM options
                bid = opt.get('bid', 0)
                ask = opt.get('ask', 0)
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
                    if mid > 0:
                        spread_pct = (ask - bid) / mid
                        atm_spreads.append(spread_pct)

            if atm_spreads:
                avg_spread_pct = statistics.mean(atm_spreads)

                # QUALITY GATE: Wide spreads are a major red flag
                if avg_spread_pct > 0.25:  # >25% spread = very illiquid
                    score -= 50  # Heavy penalty
                    signals.append("WIDE_SPREAD")
                elif avg_spread_pct > 0.15:  # >15% spread = poor execution
                    score -= 30  # Moderate penalty
                    signals.append("MODERATE_SPREAD")
                elif avg_spread_pct < 0.05:  # <5% spread = excellent liquidity
                    score += 10  # Small bonus
                    signals.append("TIGHT_SPREAD")

        # Add strike concentration score to total
        score += strike_concentration_score

        # TIER 3.2: MULTI-TIMEFRAME ANALYSIS
        # Analyze options across different DTE buckets for term structure opportunities
        timeframe_analysis = {'short': {}, 'medium': {}, 'long': {}}
        calendar_spread_opportunity = False

        try:
            import datetime
            today = datetime.date.today()

            # Bucket options by DTE
            short_term = []   # 0-21 days
            medium_term = []  # 21-60 days
            long_term = []    # 60+ days

            for opt in options_data[:100]:  # Analyze first 100 options
                exp_date_str = opt.get('expiration', '')
                if not exp_date_str:
                    continue

                try:
                    # Parse expiration date
                    exp_date = datetime.datetime.strptime(exp_date_str, '%Y-%m-%d').date()
                    dte = (exp_date - today).days

                    if 0 <= dte <= 21:
                        short_term.append(opt)
                    elif 21 < dte <= 60:
                        medium_term.append(opt)
                    elif dte > 60:
                        long_term.append(opt)
                except:
                    continue

            # Analyze each timeframe
            for bucket_name, bucket_options in [('short', short_term), ('medium', medium_term), ('long', long_term)]:
                if bucket_options:
                    ivs = [opt.get('implied_volatility', 0) for opt in bucket_options if opt.get('implied_volatility', 0) > 0]
                    volumes = [opt.get('volume', 0) for opt in bucket_options]
                    ois = [opt.get('open_interest', 0) for opt in bucket_options]

                    timeframe_analysis[bucket_name] = {
                        'count': len(bucket_options),
                        'avg_iv': sum(ivs) / len(ivs) if ivs else 0,
                        'total_volume': sum(volumes),
                        'total_oi': sum(ois)
                    }

            # Check for calendar spread opportunity (IV term structure skew)
            short_iv = timeframe_analysis.get('short', {}).get('avg_iv', 0)
            medium_iv = timeframe_analysis.get('medium', {}).get('avg_iv', 0)
            if short_iv > 0 and medium_iv > 0:
                iv_term_skew = short_iv - medium_iv

                # If short-term IV significantly higher than medium-term IV (>5pp)
                # = good calendar spread opportunity (sell short, buy medium)
                if iv_term_skew > 0.05:
                    score += 10
                    signals.append("CALENDAR_SPREAD")
                    calendar_spread_opportunity = True
                    logging.debug(f"{symbol}: Calendar spread opportunity (short IV {short_iv:.1%} vs medium {medium_iv:.1%})")

        except Exception as e:
            logging.debug(f"Could not perform multi-timeframe analysis for {symbol}: {e}")

        return {
            'score': score,
            'signals': signals,
            'avg_iv': avg_iv if 'avg_iv' in locals() else 0,
            'iv_metrics': iv_metrics if 'iv_metrics' in locals() else {},
            'put_call_ratio': put_call_ratio if 'put_call_ratio' in locals() else 0,
            'total_volume': total_volume if 'total_volume' in locals() else 0,
            'total_oi': total_oi if 'total_oi' in locals() else 0,
            'unusual_volume_score': unusual_volume_score if 'unusual_volume_score' in locals() else 0,
            'avg_spread_pct': avg_spread_pct,  # TIER 1.1: Track for Grok prompt
            'iv_skew': iv_skew,  # TIER 2.3: Track IV skew
            'max_pain_strike': max_pain_strike,  # TIER 2.4: Track max pain strike
            'timeframe_analysis': timeframe_analysis,  # TIER 3.2: Multi-timeframe data
            'calendar_spread_opportunity': calendar_spread_opportunity  # TIER 3.2: Calendar spread flag
        }

    def _score_by_expert_criteria(self, candidates: List[Dict]) -> List[Dict]:
        """Final scoring with expert weightings"""
        for candidate in candidates:
            analysis = candidate['analysis']

            # Apply expert weightings
            final_score = candidate['score']

            # Boost for multiple confirming signals
            signals = analysis['signals']
            if len(signals) >= 3:
                final_score *= 1.5

            # Boost for high IV rank (selling premium opportunity)
            iv_rank = analysis['iv_metrics'].get('iv_rank', 50)
            if iv_rank > 80:
                final_score *= 1.3

            # TIER 1.4: DATA FRESHNESS PENALTY
            # Check if options data is stale (>15 minutes old)
            options_data = candidate.get('options_data', [])
            if options_data:
                # Check timestamp if available
                try:
                    import time
                    current_time = time.time()

                    # Check if we have a timestamp stored
                    data_timestamp = candidate.get('data_timestamp', current_time)
                    age_minutes = (current_time - data_timestamp) / 60

                    if age_minutes > 15:
                        final_score -= 10  # Penalty for stale data
                        logging.debug(f"{candidate['symbol']}: -10 score penalty for stale data ({age_minutes:.1f} min old)")
                        signals.append("STALE_DATA")
                except Exception as e:
                    logging.debug(f"Could not check data freshness for {candidate['symbol']}: {e}")

            # TIER 2.2: MARKET REGIME ADAPTATION (PER-SYMBOL)
            # Detect regime for THIS specific symbol (not SPY)
            symbol = candidate['symbol']
            stock_data = candidate.get('stock_data', {})
            regime_data = self._detect_market_regime(symbol)
            regime = regime_data['regime']
            vol_regime = regime_data['volatility']
            pcr = analysis.get('put_call_ratio', 1.0)
            stock_pct_change = stock_data.get('percent_change', 0)

            # BULL REGIME: Prefer call-heavy, bullish momentum
            if regime == 'BULL':
                if pcr < PCR_CALL_HEAVY_THRESHOLD:
                    final_score *= BOOST_BULL_REGIME_CALL_HEAVY
                    logging.debug(f"{symbol}: +{(BOOST_BULL_REGIME_CALL_HEAVY-1)*100:.0f}% BULL regime bonus (call-heavy)")
                if stock_pct_change > REGIME_UPTREND_THRESHOLD:
                    final_score *= BOOST_BULL_REGIME_MOMENTUM
                    logging.debug(f"{symbol}: +{(BOOST_BULL_REGIME_MOMENTUM-1)*100:.0f}% BULL momentum bonus")

            # BEAR REGIME: Prefer put-heavy, bearish momentum
            elif regime == 'BEAR':
                if pcr > PCR_PUT_HEAVY_THRESHOLD:
                    final_score *= BOOST_BEAR_REGIME_PUT_HEAVY
                    logging.debug(f"{symbol}: +{(BOOST_BEAR_REGIME_PUT_HEAVY-1)*100:.0f}% BEAR regime bonus (put-heavy)")
                if stock_pct_change < REGIME_DOWNTREND_THRESHOLD:
                    final_score *= BOOST_BEAR_REGIME_MOMENTUM
                    logging.debug(f"{symbol}: +{(BOOST_BEAR_REGIME_MOMENTUM-1)*100:.0f}% BEAR momentum bonus")

            # HIGH VOLATILITY: Prefer credit spreads and neutral strategies
            if vol_regime == 'HIGH':
                if iv_rank > EARNINGS_HIGH_IV_THRESHOLD:
                    final_score *= BOOST_HIGH_VOL_PREMIUM_SELLING
                    logging.debug(f"{symbol}: +{(BOOST_HIGH_VOL_PREMIUM_SELLING-1)*100:.0f}% HIGH VOL premium selling bonus")

            # LOW VOLATILITY: Prefer debit spreads and directional strategies
            elif vol_regime == 'LOW':
                if iv_rank < EARNINGS_LOW_IV_THRESHOLD:
                    final_score *= BOOST_LOW_VOL_DEBIT_BUYING
                    logging.debug(f"{symbol}: +{(BOOST_LOW_VOL_DEBIT_BUYING-1)*100:.0f}% LOW VOL debit buying bonus")

            # TIER 1: SMART MONEY BOOST
            # Boost scores for symbols with unusual options activity, short squeeze potential, etc.
            if symbol in self.unusual_options_cache:
                unusual_data = self.unusual_options_cache[symbol]
                # Big boost for smart money flows
                final_score *= BOOST_UNUSUAL_OPTIONS
                logging.info(f"{symbol}: +{(BOOST_UNUSUAL_OPTIONS-1)*100:.0f}% TIER 1 SMART MONEY boost (unusual options: {unusual_data.get('sentiment')})")

            if symbol in self.short_interest_cache:
                squeeze_data = self.short_interest_cache[symbol]
                # Boost for squeeze candidates
                final_score *= BOOST_SQUEEZE_CANDIDATE
                logging.info(f"{symbol}: +{(BOOST_SQUEEZE_CANDIDATE-1)*100:.0f}% TIER 1 SQUEEZE CANDIDATE boost ({squeeze_data.get('short_pct')}% short)")

            if symbol in self.darkpool_cache:
                darkpool_data = self.darkpool_cache[symbol]
                if darkpool_data.get('is_spike'):
                    # Boost for dark pool activity
                    final_score *= BOOST_DARKPOOL_SPIKE
                    logging.info(f"{symbol}: +{(BOOST_DARKPOOL_SPIKE-1)*100:.0f}% TIER 1 DARK POOL SPIKE boost ({darkpool_data.get('volume_ratio'):.1f}x avg)")

            # ═════════════════════════════════════════════════════════════════════
            # SPRINT 1: QUICK WINS - Enhanced Scoring
            # ═════════════════════════════════════════════════════════════════════

            # SPRINT 1.1: VWAP Analysis
            stock_data = candidate.get('stock_data', {})
            current_price = stock_data.get('last_price') or stock_data.get('close', 0)

            if current_price > 0:
                vwap_analysis = self.analyze_vwap_position(symbol, current_price)

                # Store VWAP data in candidate for Grok
                candidate['vwap_analysis'] = vwap_analysis

                # Apply VWAP-based boosts
                if vwap_analysis['position'] == 'ABOVE' and vwap_analysis['entry_quality'] in ['EXCELLENT', 'GOOD']:
                    # Price above VWAP = bullish, good for call plays
                    final_score *= BOOST_VWAP_BULLISH
                    logging.debug(f"{symbol}: +{(BOOST_VWAP_BULLISH-1)*100:.0f}% SPRINT 1 VWAP BULLISH boost (price {vwap_analysis['distance_from_vwap_pct']*100:.1f}% above VWAP)")

                elif vwap_analysis['position'] == 'BELOW' and vwap_analysis['entry_quality'] in ['EXCELLENT', 'GOOD']:
                    # Price below VWAP = bearish, good for put plays
                    if pcr > 1.0:  # Put-heavy flow confirms
                        final_score *= BOOST_VWAP_BEARISH_PUT
                        logging.debug(f"{symbol}: +{(BOOST_VWAP_BEARISH_PUT-1)*100:.0f}% SPRINT 1 VWAP BEARISH boost (price {vwap_analysis['distance_from_vwap_pct']*100:.1f}% below VWAP)")

            # SPRINT 1.2: VIX Context Matching
            vix_context = self.get_vix_context()

            # Store VIX context in candidate for Grok
            candidate['vix_context'] = vix_context

            # Boost if strategy aligns with VIX environment
            if vix_context['vix']:
                # High VIX + High IV stock = excellent credit spread opportunity
                if vix_context['level'] in ['HIGH', 'EXTREME_HIGH'] and iv_rank > EARNINGS_HIGH_IV_THRESHOLD:
                    final_score *= BOOST_VIX_CONTEXT_MATCH
                    logging.debug(f"{symbol}: +{(BOOST_VIX_CONTEXT_MATCH-1)*100:.0f}% SPRINT 1 VIX CONTEXT MATCH (VIX {vix_context['vix']:.1f}, IV rank {iv_rank:.0f})")

                # Low VIX + Low IV stock = excellent debit spread opportunity
                elif vix_context['level'] == 'LOW' and iv_rank < EARNINGS_LOW_IV_THRESHOLD:
                    final_score *= BOOST_VIX_CONTEXT_MATCH
                    logging.debug(f"{symbol}: +{(BOOST_VIX_CONTEXT_MATCH-1)*100:.0f}% SPRINT 1 VIX CONTEXT MATCH (VIX {vix_context['vix']:.1f}, IV rank {iv_rank:.0f})")

            # SPRINT 1.3: Sector Classification & Rotation
            sector_data = self.get_sector_classification(symbol)

            # Store sector data in candidate for Grok
            candidate['sector_data'] = sector_data

            # TODO: Add sector strength boost when sector performance tracking is implemented
            # if sector_data['sector_strength'] == 'STRONG':
            #     final_score *= BOOST_SECTOR_STRENGTH
            #     logging.debug(f"{symbol}: +{(BOOST_SECTOR_STRENGTH-1)*100:.0f}% SPRINT 1 SECTOR STRENGTH boost ({sector_data['sector']})")

            # SPRINT 1.4: Enhanced IV Rank with Percentiles
            iv_percentile_analysis = self.enhance_iv_rank_with_percentiles(iv_rank, symbol)

            # Store enhanced IV analysis in candidate for Grok
            candidate['iv_percentile_analysis'] = iv_percentile_analysis

            # Log enhanced IV context
            logging.debug(f"{symbol}: IV Rank {iv_rank:.0f} → {iv_percentile_analysis['percentile_band']} "
                         f"({iv_percentile_analysis['recommendation']}, confidence {iv_percentile_analysis['confidence']}%)")

            candidate['final_score'] = final_score

        # Sort by final score
        candidates.sort(key=lambda x: x['final_score'], reverse=True)

        # Note: Sector-based hard caps removed - using symbol-level and Greeks limits instead
        return candidates

    def pre_filter_for_grok(self, candidates: List[Dict]) -> List[Dict]:
        """
        PRE-FILTER: Apply quantitative guardrails BEFORE sending to Grok

        This ensures Grok only sees candidates that meet strict quantitative standards:
        - IV rank appropriateness for strategy type
        - Technical setup quality (pullback confirmation)
        - Market regime alignment
        - Minimum score thresholds

        Returns: Filtered list of candidates qualified for Grok analysis
        """
        filtered = []
        rejected_reasons = {}

        for candidate in candidates:
            symbol = candidate['symbol']
            analysis = candidate.get('analysis', {})
            stock_data = candidate.get('stock_data', {})
            score = candidate.get('final_score', 0)

            # Extract key metrics
            iv_rank = analysis.get('iv_metrics', {}).get('iv_rank', 50)
            pcr = analysis.get('put_call_ratio', 1.0)
            pct_change = stock_data.get('percent_change', 0)

            # Detect setup type
            regime_data = self._detect_market_regime(symbol)
            regime = regime_data['regime']

            # Determine if this is bullish or bearish setup
            is_bullish = regime == 'BULL' or (pct_change > 0.03 and pcr < 0.8)
            is_bearish = regime == 'BEAR' or (pct_change < -0.03 and pcr > 1.3)

            # Get RSI-like indicator from percent change
            # Approximate RSI: >60 = overbought, <40 = oversold
            approx_rsi = 50 + (pct_change * 500)  # Rough approximation

            # FILTER 1: IV Rank Appropriateness
            # Debit spreads (buying premium) should ONLY happen in LOW IV
            if is_bullish or is_bearish:
                # This will likely be a debit spread (buying options)
                if iv_rank > 50:
                    rejected_reasons[symbol] = f"IV rank {iv_rank:.0f}% too high for debit spread (need <50%)"
                    logging.info(f"PRE-FILTER REJECT: {symbol} - {rejected_reasons[symbol]}")
                    continue

            # FILTER 2: Technical Setup Quality
            # For bullish setups, require recent pullback (not buying at top)
            if is_bullish:
                if approx_rsi > 65:
                    rejected_reasons[symbol] = f"No pullback - approx RSI {approx_rsi:.0f} (need <65)"
                    logging.info(f"PRE-FILTER REJECT: {symbol} - {rejected_reasons[symbol]}")
                    continue

            # For bearish setups, require recent rally (not selling at bottom)
            if is_bearish:
                if approx_rsi < 35:
                    rejected_reasons[symbol] = f"No rally - approx RSI {approx_rsi:.0f} (need >35)"
                    logging.info(f"PRE-FILTER REJECT: {symbol} - {rejected_reasons[symbol]}")
                    continue

            # FILTER 3: Minimum Score Threshold
            if score < 50:
                rejected_reasons[symbol] = f"Score {score:.0f} too low (need >50)"
                logging.info(f"PRE-FILTER REJECT: {symbol} - {rejected_reasons[symbol]}")
                continue

            # FILTER 4: Bid-Ask Spread Quality
            avg_spread = analysis.get('avg_spread_pct', 0)
            if avg_spread > 0.20:  # >20% spread = too wide
                rejected_reasons[symbol] = f"Spread {avg_spread:.1%} too wide (need <20%)"
                logging.info(f"PRE-FILTER REJECT: {symbol} - {rejected_reasons[symbol]}")
                continue

            # PASSED all filters
            candidate['pre_filter_status'] = 'APPROVED'
            candidate['setup_type'] = 'BULLISH' if is_bullish else 'BEARISH' if is_bearish else 'NEUTRAL'
            candidate['regime'] = regime
            filtered.append(candidate)
            logging.info(f"PRE-FILTER APPROVED: {symbol} - Score:{score:.0f}, IV:{iv_rank:.0f}%, Setup:{candidate['setup_type']}")

        print(f"\n{Colors.INFO}[PRE-FILTER] {len(candidates)} → {len(filtered)} qualified for Grok analysis{Colors.RESET}")

        if rejected_reasons:
            print(f"{Colors.DIM}Rejected samples: {list(rejected_reasons.items())[:3]}{Colors.RESET}\n")

        return filtered

    def _display_top_opportunities(self, opportunities: List[Dict]):
        """Display top opportunities with details"""
        print(f"{Colors.HEADER}TOP OPPORTUNITIES:{Colors.RESET}")

        for i, opp in enumerate(opportunities[:10]):
            symbol = opp['symbol']
            score = opp['final_score']
            analysis = opp['analysis']
            stock = opp['stock_data']

            pct_change = stock.get('percent_change', 0) * 100
            signals = ', '.join(analysis['signals'][:3])
            iv_rank = analysis['iv_metrics'].get('iv_rank', 0)

            print(f"  {i+1:2d}. {symbol:6s} Score:{score:6.0f} | "
                  f"Chg:{pct_change:+6.1f}% | IV-Rank:{iv_rank:4.0f} | "
                  f"Signals: {signals}")

        if len(opportunities) > 10:
            print(f"{Colors.DIM}     ... and {len(opportunities)-10} more{Colors.RESET}\n")

    def scan_market_for_opportunities(self) -> List[Dict]:
        """Synchronous wrapper for async scan"""
        # Run async scan
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.scan_market_for_opportunities_async())
            return result
        finally:
            loop.close()

    def _calculate_current_iv(self, options_chain: List[Dict], stock_price: float) -> float:
        """
        Calculate current implied volatility from ATM options in the chain.

        Args:
            options_chain: List of option contracts
            stock_price: Current stock price

        Returns:
            Average IV from ATM options, or 0 if no valid data
        """
        if not options_chain or not stock_price or stock_price <= 0:
            return 0.0

        import statistics

        # Find ATM options (strike within 5% of stock price)
        atm_options = [
            opt for opt in options_chain
            if opt.get('implied_volatility', 0) > 0 and
               opt.get('strike', 0) > 0 and
               0.95 <= opt.get('strike', 0) / stock_price <= 1.05
        ]

        if not atm_options:
            # Fallback: Use first 20 options with valid IV
            atm_options = [
                opt for opt in options_chain[:20]
                if opt.get('implied_volatility', 0) > 0
            ]

        if atm_options:
            ivs = [opt['implied_volatility'] for opt in atm_options]
            return statistics.mean(ivs)

        return 0.0

    def get_filtered_universe(self) -> List[Dict]:
        """
        Get a filtered stock universe for Wheel Strategy.
        Returns enriched stock data with IV rank, market cap, and beta.

        Returns:
            List of stock dicts with: symbol, price, iv_rank, market_cap, beta, volume
        """
        logging.info("[WHEEL] Building filtered stock universe...")

        # Run async universe build to get base stocks
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            universe = loop.run_until_complete(self._build_stock_universe())
            logging.info(f"[WHEEL] Built base universe with {len(universe)} stocks")
        except Exception as e:
            logging.error(f"[WHEEL] Failed to build base universe: {e}")
            return []
        finally:
            loop.close()

        # Enrich universe with IV rank, market cap, and beta
        # But apply basic price filter first to reduce API calls
        enriched_universe = []
        processed = 0
        skipped_price = 0

        for stock in universe:
            symbol = stock.get('symbol')
            if not symbol:
                continue

            try:
                # Get current price (may not be in universe data)
                if 'price' not in stock or not stock['price']:
                    quote_data = self.openbb.get_quote(symbol)
                    if quote_data and 'results' in quote_data:
                        results = quote_data['results']
                        if isinstance(results, list) and len(results) > 0:
                            stock['price'] = results[0].get('last_price') or results[0].get('close')
                        elif isinstance(results, dict):
                            stock['price'] = results.get('last_price') or results.get('close')

                # Quick price filter before expensive API calls (from config defaults: $20-$150)
                price = stock.get('price', 0)
                if not price or price < 15 or price > 200:  # Slightly wider than Wheel limits
                    skipped_price += 1
                    continue

                processed += 1

                # Progress logging for long-running scans
                if processed % 10 == 0:
                    logging.info(f"[WHEEL] Enriching stock {processed}/{len(universe)} ({symbol})...")

                # Get IV rank from options chain
                options_chain_data = self.openbb.get_options_chains(symbol)
                if options_chain_data and 'results' in options_chain_data:
                    options_chain = options_chain_data['results']

                    # Calculate current IV from ATM options
                    current_iv = self._calculate_current_iv(options_chain, stock.get('price', 0))

                    if current_iv and current_iv > 0:
                        # Calculate IV metrics (IV rank)
                        iv_metrics = self.iv_analyzer.calculate_iv_metrics(symbol, current_iv, options_chain)
                        stock['iv_rank'] = iv_metrics.get('iv_rank', 0)
                        stock['iv_percentile'] = iv_metrics.get('iv_percentile', 0)
                        stock['current_iv'] = current_iv
                    else:
                        stock['iv_rank'] = 0
                        stock['iv_percentile'] = 0
                else:
                    stock['iv_rank'] = 0
                    stock['iv_percentile'] = 0

                # Get market cap and beta from equity profile
                profile_data = self.openbb.get_equity_profile(symbol)
                if profile_data and 'results' in profile_data:
                    profile = profile_data['results']
                    if isinstance(profile, list) and len(profile) > 0:
                        profile = profile[0]

                    stock['market_cap'] = profile.get('market_cap') or profile.get('marketCap', 0)
                    stock['beta'] = profile.get('beta', 1.0)
                    stock['sector'] = profile.get('sector', 'Unknown')
                else:
                    stock['market_cap'] = 0
                    stock['beta'] = 1.0
                    stock['sector'] = 'Unknown'

                enriched_universe.append(stock)

            except Exception as e:
                logging.debug(f"[WHEEL] Could not enrich {symbol}: {e}")
                continue

        logging.info(f"[WHEEL] Enriched {len(enriched_universe)} stocks (processed {processed}, skipped {skipped_price} by price)")
        return enriched_universe


