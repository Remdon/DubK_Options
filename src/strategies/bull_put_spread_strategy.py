"""
Bull Put Spread Strategy - Defined-Risk Premium Collection

The Bull Put Spread is a credit spread strategy with:
- DEFINED RISK: Max loss = spread width - credit received
- Lower capital requirement than Wheel ($300-500 vs $2,000-10,000)
- High win rate (65-75% when properly managed)
- Ideal for smaller accounts ($10K-$25K)

How it works:
1. Sell put at higher strike (collect premium)
2. Buy put at lower strike (define max loss)
3. Net credit received upfront
4. Profit if stock stays above short strike at expiration

Example:
- Stock at $100
- Sell $95 put for $2.00
- Buy $90 put for $0.50
- Net credit: $1.50 ($150 per spread)
- Max risk: $500 - $150 = $350
- Max profit: $150 (43% ROI)

Expected Returns: 15-30% annually
Win Rate: 65-75% (most spreads expire worthless)
Risk: DEFINED (can't lose more than spread width - credit)
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from colorama import Fore, Style


class BullPutSpreadStrategy:
    """
    Implements Bull Put Spread Strategy for defined-risk premium collection.

    Key Features:
    - Identifies high IV stocks suitable for credit spreads
    - Calculates optimal strikes and spread widths
    - Manages risk per spread (defined max loss)
    - Tracks performance per symbol
    """

    def __init__(self, trading_client, openbb_client, scanner, config):
        """
        Initialize Bull Put Spread Strategy

        Args:
            trading_client: Alpaca trading client (using ALPACA_BULL_PUT_KEY)
            openbb_client: OpenBB client for market data
            scanner: Market scanner for finding candidates
            config: Bot configuration
        """
        self.trading_client = trading_client
        self.openbb_client = openbb_client
        self.scanner = scanner
        self.config = config
        self.spread_db = None  # Will be set by SpreadManager

        # Load parameters from config
        self.MIN_STOCK_PRICE = config.SPREAD_MIN_STOCK_PRICE
        self.MAX_STOCK_PRICE = config.SPREAD_MAX_STOCK_PRICE
        self.MIN_IV_RANK = config.SPREAD_MIN_IV_RANK
        self.MAX_IV_RANK = config.SPREAD_MAX_IV_RANK
        self.MIN_MARKET_CAP = config.SPREAD_MIN_MARKET_CAP

        # Spread-specific parameters
        self.SPREAD_WIDTH = config.SPREAD_WIDTH  # $5 default
        self.MIN_CREDIT = config.SPREAD_MIN_CREDIT  # $1.00 minimum credit
        self.MAX_CAPITAL_PER_SPREAD = config.SPREAD_MAX_CAPITAL_PER_SPREAD  # $500 max risk
        self.SHORT_STRIKE_DELTA = config.SPREAD_SHORT_STRIKE_DELTA  # -0.25 to -0.35 (25-35% OTM)

        # Position sizing
        self.MAX_SPREAD_POSITIONS = config.MAX_SPREAD_POSITIONS
        self.MAX_CAPITAL_PER_POSITION = config.MAX_CAPITAL_PER_SPREAD_POSITION

        # DTE parameters (shorter than Wheel)
        self.TARGET_DTE = config.SPREAD_TARGET_DTE  # 30-45 days
        self.MIN_DTE = config.SPREAD_MIN_DTE  # 21 days
        self.MAX_DTE = config.SPREAD_MAX_DTE  # 60 days

        # Exit parameters
        self.PROFIT_TARGET_PCT = config.SPREAD_PROFIT_TARGET_PCT  # 50% profit target
        self.STOP_LOSS_PCT = config.SPREAD_STOP_LOSS_PCT  # -50% stop loss (preserve capital)

        logging.info(f"[SPREAD] Initialized with criteria: ${self.MIN_STOCK_PRICE}-${self.MAX_STOCK_PRICE}, "
                    f"IV rank {self.MIN_IV_RANK}%+, Spread width ${self.SPREAD_WIDTH}")

    def check_consecutive_losses(self, symbol: str, spread_manager) -> bool:
        """
        Check if symbol has too many consecutive losses.

        Prevents revenge trading on spreads - pauses after 2 consecutive losses.

        Args:
            symbol: Symbol to check
            spread_manager: SpreadManager instance

        Returns:
            True if OK to trade, False if should pause
        """
        performance = spread_manager.get_symbol_performance(symbol)

        if performance:
            consecutive_losses = performance.get('consecutive_losses', 0)

            if consecutive_losses >= 2:  # Max 2 consecutive losses
                logging.warning(f"[SPREAD] {symbol}: {consecutive_losses} consecutive losses - "
                              f"PAUSING new entries until streak breaks")
                return False

        return True

    def find_spread_candidates(self, max_candidates: int = 10) -> List[Dict]:
        """
        Find top bull put spread candidates based on criteria and expected returns.

        Returns:
            List of candidate dicts sorted by risk-adjusted return:
            {
                'symbol': str,
                'stock_price': float,
                'iv_rank': float,
                'short_strike': float,
                'long_strike': float,
                'credit': float,
                'max_risk': float,
                'max_profit': float,
                'roi': float,
                'annual_return': float,
                'probability_profit': float,
                'dte': int
            }
        """
        logging.info(f"[SPREAD] ════════════════════════════════════════════════════════")
        logging.info(f"[SPREAD] Starting Bull Put Spread Candidate Scan")
        logging.info(f"[SPREAD] ════════════════════════════════════════════════════════")

        # Step 1: Get stock universe from scanner
        try:
            stocks = self._build_stock_universe()
            logging.info(f"[SPREAD] ✓ Built base universe with {len(stocks)} stocks")
        except Exception as e:
            logging.error(f"[SPREAD] ❌ Error building universe: {e}")
            import traceback
            traceback.print_exc()
            return []

        if not stocks:
            logging.warning(f"[SPREAD] ❌ No stocks returned from scanner")
            return []

        # Step 2: Apply filters
        filtered_stocks = self._apply_filters(stocks)
        logging.info(f"[SPREAD] After filters: {len(filtered_stocks)}/{len(stocks)} stocks")

        if not filtered_stocks:
            logging.warning("[SPREAD] ❌ No stocks passed filters - likely due to low IV rank in calm market")
            return []

        # Step 3: Find optimal spreads for each stock
        candidates = []
        spread_errors = 0
        for stock in filtered_stocks:
            try:
                logging.info(f"[SPREAD] Searching for spread on {stock['symbol']} @ ${stock['price']:.2f}, IV {stock.get('iv_rank', 0):.1f}%")
                spread = self._find_optimal_spread(stock)
                if spread:
                    candidates.append(spread)
                    logging.info(f"[SPREAD] ✓ Found spread: {spread['symbol']} ${spread['short_strike']:.2f}/${spread['long_strike']:.2f} for ${spread['credit']:.2f} credit")
                else:
                    logging.warning(f"[SPREAD] ✗ No valid spread found for {stock['symbol']}")
            except Exception as e:
                spread_errors += 1
                logging.warning(f"[SPREAD] ✗ Error finding spread for {stock['symbol']}: {e}")
                continue

        if not candidates:
            logging.warning(f"[SPREAD] ❌ No spread candidates found from {len(filtered_stocks)} filtered stocks ({spread_errors} errors)")
            return []

        # Step 4: Sort by risk-adjusted return (ROI / max_risk)
        candidates.sort(key=lambda x: x['annual_return'] / max(x['max_risk'], 100), reverse=True)

        logging.info(f"[SPREAD] ✓ Found {len(candidates)} spread candidates, returning top {max_candidates}")
        logging.info(f"[SPREAD] ────────────────────────────────────────────────────────")

        # Log top candidates with details
        for i, candidate in enumerate(candidates[:max_candidates], 1):
            logging.info(f"[SPREAD]   #{i}: {candidate['symbol']:6s} - {candidate['annual_return']:5.1f}% annual return")
            logging.info(f"[SPREAD]        Strikes: ${candidate['short_strike']:.2f}/${candidate['long_strike']:.2f}, "
                        f"Credit: ${candidate['credit']:.2f}, Risk: ${candidate['max_risk']:.0f}, ROI: {candidate['roi']:.1f}%")

        logging.info(f"[SPREAD] ════════════════════════════════════════════════════════")

        return candidates[:max_candidates]

    def _build_stock_universe(self) -> List[Dict]:
        """
        Build stock universe for spread strategy.

        NOTE: Uses same scanner as Wheel but spreads need DIFFERENT characteristics:
        - Wheel wants: LOW IV rank (buy low, sell high)
        - Spreads want: HIGHER IV rank (sell premium when elevated)

        Strategy: Accept ALL scanner results, filter in _apply_filters()
        """
        # Use scanner's market scan to get candidates
        try:
            candidates = self.scanner.scan_market_for_opportunities()

            # Convert scanner format to our format
            # IMPORTANT: Don't filter here - spreads and wheel want different IV profiles
            universe = []
            for candidate in candidates:
                try:
                    # Extract from nested structure (expert scanner returns: {symbol, stock_data, analysis})
                    stock_data = candidate.get('stock_data', {})
                    analysis = candidate.get('analysis', {})
                    iv_metrics = analysis.get('iv_metrics', {})
                    symbol = candidate.get('symbol', 'UNKNOWN')

                    # Get price from stock_data - try multiple field names
                    # OpenBB API may return: last_price, close, price, last, prev_close
                    price = (
                        stock_data.get('last_price') or
                        stock_data.get('price') or
                        stock_data.get('close') or
                        stock_data.get('last') or
                        stock_data.get('prev_close') or
                        stock_data.get('previous_close') or
                        0
                    )

                    # If still no price, try to get from analysis or candidate root level
                    if price == 0:
                        price = (
                            analysis.get('stock_price') or
                            candidate.get('price') or
                            0
                        )

                    if price == 0:
                        logging.warning(f"[SPREAD] {symbol}: No price found in data structure, skipping")
                        logging.warning(f"[SPREAD] {symbol}: stock_data keys available: {list(stock_data.keys())}")
                        logging.warning(f"[SPREAD] {symbol}: stock_data sample: {dict(list(stock_data.items())[:5])}")
                        continue

                    stock_info = {
                        'symbol': symbol,
                        'price': price,
                        'iv_rank': iv_metrics.get('iv_rank', 0),
                        'market_cap': stock_data.get('market_cap', 0),
                        'volume': stock_data.get('volume', 0)
                    }
                    universe.append(stock_info)
                    logging.debug(f"[SPREAD] {symbol}: Extracted price ${price:.2f}, IV rank {iv_metrics.get('iv_rank', 0):.1f}%")
                except Exception as e:
                    logging.warning(f"[SPREAD] Error processing candidate {candidate.get('symbol')}: {e}")
                    import traceback
                    logging.debug(traceback.format_exc())
                    continue

            logging.info(f"[SPREAD] Scanner provided {len(universe)} total candidates")
            return universe
        except Exception as e:
            logging.error(f"[SPREAD] Error scanning market: {e}")
            return []

    def _apply_filters(self, stocks: List[Dict]) -> List[Dict]:
        """
        Apply spread-specific filters.

        NOTE: Spreads need HIGHER IV than Wheel strategy
        - Wheel looks for LOW IV (0-30% rank) - buy low volatility
        - Spreads look for ELEVATED IV (30%+ rank) - sell premium when elevated

        This means spreads may find ZERO candidates when market is calm (VIX <15)

        NEW: Also screens for earnings risk (14 days minimum to avoid IV crush)
        """
        filtered = []
        rejection_reasons = {'price': 0, 'iv_rank': 0, 'market_cap': 0, 'earnings': 0}
        rejected_details = []

        for stock in stocks:
            symbol = stock.get('symbol', 'UNKNOWN')
            price = stock.get('price', 0)
            iv_rank = stock.get('iv_rank', 0)
            market_cap = stock.get('market_cap', 0)

            # Filter by price range
            if not (self.MIN_STOCK_PRICE <= price <= self.MAX_STOCK_PRICE):
                rejection_reasons['price'] += 1
                rejected_details.append(f"{symbol}: price ${price:.2f} (need ${self.MIN_STOCK_PRICE}-${self.MAX_STOCK_PRICE})")
                continue

            # Filter by IV rank (spreads need elevated IV to sell premium)
            if iv_rank < self.MIN_IV_RANK:
                rejection_reasons['iv_rank'] += 1
                rejected_details.append(f"{symbol}: IV rank {iv_rank:.1f}% (need >={self.MIN_IV_RANK}%)")
                continue

            # Filter by market cap
            if market_cap < self.MIN_MARKET_CAP:
                rejection_reasons['market_cap'] += 1
                rejected_details.append(f"{symbol}: market cap ${market_cap/1e9:.2f}B (need >=${self.MIN_MARKET_CAP/1e9:.1f}B)")
                continue

            # CRITICAL: Check earnings date (avoid IV crush)
            # Spreads are killed by earnings - IV drops and spreads get tested
            # Extended to 21 days minimum buffer (IV crush can happen before actual report)
            try:
                if hasattr(self.scanner, 'earnings_calendar') and self.scanner.earnings_calendar:
                    earnings_risk = self.scanner.earnings_calendar.check_earnings_risk(symbol)

                    # Reject if earnings within 21 days (prevent IV crush + pre-earnings volatility)
                    # earnings_risk returns: {'risk': 'HIGH/MODERATE/LOW', 'days_until': X, 'action': 'AVOID/CAUTION/PROCEED'}
                    if earnings_risk.get('days_until') is not None:
                        days_until = earnings_risk['days_until']

                        if 0 < days_until < 21:
                            rejection_reasons['earnings'] += 1
                            rejected_details.append(f"{symbol}: earnings in {days_until} days (need >=21 days)")
                            logging.warning(f"[SPREAD FILTER] ✗ {symbol}: Earnings in {days_until} days - SKIPPING (need 21+ day buffer to avoid IV crush)")
                            continue
            except Exception as e:
                logging.debug(f"[SPREAD] Could not check earnings for {symbol}: {e}")
                # Don't reject on error - continue without earnings check

            # CRITICAL: Check for strong bearish bias (bull put spreads need neutral-to-bullish)
            # HARD REJECT on bearish conditions - spreads get destroyed in downtrends
            try:
                if 'technical_bias' in stock:
                    bias = stock.get('technical_bias', '').lower()
                    if any(keyword in bias for keyword in ['strong bear', 'very bearish', 'strongly bearish', 'extreme bear']):
                        rejection_reasons['bias'] = rejection_reasons.get('bias', 0) + 1
                        rejected_details.append(f"{symbol}: strong bearish bias detected (spreads need neutral-bullish)")
                        logging.warning(f"[SPREAD FILTER] ✗ {symbol}: Strong bearish bias '{bias}' - REJECTING (bull put spreads need uptrend)")
                        continue  # Skip this candidate
            except Exception as e:
                logging.debug(f"[SPREAD] Could not check technical bias for {symbol}: {e}")

            filtered.append(stock)
            logging.info(f"[SPREAD FILTER] ✓ {symbol}: price ${price:.2f}, IV {iv_rank:.1f}%, cap ${market_cap/1e9:.2f}B")

        # Log why stocks were rejected with details
        if not filtered:
            logging.warning(f"[SPREAD] ❌ No stocks passed filters out of {len(stocks)} candidates")
            logging.warning(f"[SPREAD] Rejections: price={rejection_reasons['price']}, "
                          f"iv_rank={rejection_reasons['iv_rank']} (need >={self.MIN_IV_RANK}%), "
                          f"market_cap={rejection_reasons['market_cap']}, "
                          f"earnings={rejection_reasons['earnings']} (need >=14 days)")
            # Log first 5 rejection details for debugging
            for detail in rejected_details[:5]:
                logging.warning(f"[SPREAD]   - {detail}")
            if len(rejected_details) > 5:
                logging.warning(f"[SPREAD]   ... and {len(rejected_details)-5} more rejections")
        else:
            logging.info(f"[SPREAD] ✓ {len(filtered)}/{len(stocks)} stocks passed filters")

        return filtered

    def _find_optimal_spread(self, stock: Dict) -> Optional[Dict]:
        """
        Find optimal bull put spread for a given stock.

        Strategy:
        - Short strike: 25-35% OTM (delta -0.25 to -0.35)
        - Long strike: SPREAD_WIDTH below short strike
        - Target credit: At least MIN_CREDIT
        - Max risk: No more than MAX_CAPITAL_PER_SPREAD
        """
        symbol = stock['symbol']
        stock_price = stock['price']

        # Get options chain
        try:
            options_chain = self._get_options_chain(symbol)
            if not options_chain:
                logging.warning(f"[SPREAD] {symbol}: No options chain available")
                return None
        except Exception as e:
            logging.warning(f"[SPREAD] {symbol}: Error getting options chain: {e}")
            return None

        # Find target DTE expiration
        target_expiration = self._find_target_expiration(options_chain)
        if not target_expiration:
            expirations = sorted(set(opt['expiration'] for opt in options_chain))
            dtes = [(exp, (datetime.strptime(exp, '%Y-%m-%d') - datetime.now()).days) for exp in expirations[:3]]
            logging.warning(f"[SPREAD] {symbol}: No expiration in {self.MIN_DTE}-{self.MAX_DTE} DTE range. "
                          f"Available: {dtes}")
            return None

        # Get puts for target expiration
        puts = [opt for opt in options_chain if opt['expiration'] == target_expiration and opt['type'] == 'put']

        if len(puts) < 2:
            logging.warning(f"[SPREAD] {symbol}: Only {len(puts)} put(s) available for {target_expiration}, need at least 2")
            return None

        # Find short strike using delta targeting (25-35% OTM sweet spot)
        # Delta -0.25 to -0.35 provides optimal balance of premium vs. safety
        # This corresponds to ~75-65% probability of expiring OTM

        # Target delta -0.30 (30% OTM, 70% probability of success)
        # Start with price-based estimate, then refine by delta
        short_strike_target = stock_price * 0.70  # ~30% OTM as starting point
        short_put = self._find_closest_strike(puts, short_strike_target, 'short')

        if not short_put:
            logging.warning(f"[SPREAD] {symbol}: No short put found near ${short_strike_target:.2f} "
                          f"(30% OTM from ${stock_price:.2f})")
            return None

        # CRITICAL: Validate short strike delta (must be in safe range)
        # Delta -0.15 to -0.40 = 15-40% OTM acceptable range
        # -0.15 = 15% OTM (85% probability ITM - balanced safety/premium)
        # -0.40 = 40% OTM (60% probability ITM - still profitable)
        short_delta = short_put.get('delta', 0)
        if short_delta != 0:  # Only validate if delta is available
            if not (-0.40 <= short_delta <= -0.15):
                logging.warning(f"[SPREAD] {symbol}: Short put delta {short_delta:.3f} outside safe range "
                              f"(-0.40 to -0.15). Strike ${short_put['strike']:.2f} rejected - "
                              f"{'too close to current price' if short_delta > -0.15 else 'too far OTM'}.")
                return None  # HARD REJECT - don't trade bad deltas
            else:
                logging.info(f"[SPREAD] {symbol}: Short put delta {short_delta:.3f} ✓ (safe range, {abs(short_delta)*100:.0f}% OTM)")

        # Find long strike (SPREAD_WIDTH below short strike)
        long_strike_target = short_put['strike'] - self.SPREAD_WIDTH
        long_put = self._find_closest_strike(puts, long_strike_target, 'long')

        if not long_put:
            logging.warning(f"[SPREAD] {symbol}: No long put found near ${long_strike_target:.2f} "
                          f"(${self.SPREAD_WIDTH:.0f} below short ${short_put['strike']:.2f})")
            return None

        # Log selected strikes for debugging
        logging.debug(f"[SPREAD] {symbol}: Selected strikes - Short: ${short_put['strike']:.2f}, Long: ${long_put['strike']:.2f}")

        # CRITICAL FIX: Use tolerance for floating-point comparison
        # Validates that strikes are different to avoid synthetic positions
        strike_diff = abs(short_put['strike'] - long_put['strike'])
        if strike_diff < 0.01:  # Less than 1 cent difference
            logging.error(f"[SPREAD] {symbol}: INVALID SPREAD - Both legs have same strike ${short_put['strike']:.2f}! "
                        f"Strike difference ${strike_diff:.4f} < 0.01 tolerance. "
                        f"This would create a synthetic position, not a spread. "
                        f"Target long strike was ${long_strike_target:.2f} but no different strike available.")
            return None

        # Validate strikes are in correct order (short > long for bull put spread)
        # Using tolerance to avoid floating-point errors
        if short_put['strike'] <= long_put['strike'] + 0.01:  # Allow 1 cent tolerance
            logging.error(f"[SPREAD] {symbol}: INVALID SPREAD - Short strike ${short_put['strike']:.2f} "
                        f"not higher than long strike ${long_put['strike']:.2f}!")
            return None

        # Calculate spread metrics
        short_premium = short_put['bid']  # Selling at bid
        long_premium = long_put['ask']    # Buying at ask
        credit = short_premium - long_premium
        spread_width = short_put['strike'] - long_put['strike']
        max_risk = (spread_width * 100) - (credit * 100)  # Per contract
        max_profit = credit * 100
        roi = (max_profit / max_risk) * 100 if max_risk > 0 else 0

        # Calculate annual return
        dte = (datetime.strptime(target_expiration, '%Y-%m-%d') - datetime.now()).days
        annual_return = (roi / dte) * 365 if dte > 0 else 0

        # CRITICAL: Validate credit quality
        # Credit should be 30-50% of spread width for good risk/reward
        # Example: $5 spread should collect $1.50-2.50 credit minimum
        credit_as_pct_of_width = (credit / spread_width) * 100 if spread_width > 0 else 0
        min_credit_pct = 30.0  # Require minimum 30% of spread width as credit

        if credit < self.MIN_CREDIT:
            logging.warning(f"[SPREAD] {symbol}: Credit ${credit:.2f} (short bid ${short_premium:.2f} - long ask ${long_premium:.2f}) "
                          f"below absolute minimum ${self.MIN_CREDIT:.2f}")
            return None

        if credit_as_pct_of_width < min_credit_pct:
            logging.warning(f"[SPREAD] {symbol}: Credit ${credit:.2f} only {credit_as_pct_of_width:.1f}% of "
                          f"spread width ${spread_width:.2f} (need >={min_credit_pct}% for good risk/reward). "
                          f"Risking ${max_risk:.0f} to make ${max_profit:.0f} is not favorable.")
            return None

        if max_risk > self.MAX_CAPITAL_PER_SPREAD:
            logging.warning(f"[SPREAD] {symbol}: Max risk ${max_risk:.0f} exceeds limit ${self.MAX_CAPITAL_PER_SPREAD:.0f}")
            return None

        # Estimate probability of profit (based on delta)
        prob_profit = 1 - abs(short_put.get('delta', -0.30))  # If delta -0.30, prob = 70%

        return {
            'symbol': symbol,
            'stock_price': stock_price,
            'iv_rank': stock.get('iv_rank', 0),
            'short_strike': short_put['strike'],
            'long_strike': long_put['strike'],
            'spread_width': spread_width,
            'credit': credit,
            'max_risk': max_risk,
            'max_profit': max_profit,
            'roi': roi,
            'annual_return': annual_return,
            'probability_profit': prob_profit * 100,
            'dte': dte,
            'expiration': target_expiration,
            'short_put_symbol': short_put['symbol'],
            'long_put_symbol': long_put['symbol'],
            'liquidity_score': min(short_put.get('volume', 0), long_put.get('volume', 0)),
            # Add actual market prices for order execution
            'short_put_bid': short_premium,
            'short_put_ask': short_put['ask'],
            'long_put_bid': long_put['bid'],
            'long_put_ask': long_premium
        }

    def _get_options_chain(self, symbol: str) -> List[Dict]:
        """
        Get options chain from OpenBB with Greeks calculated

        Returns:
            List of options dicts with format:
            {
                'symbol': 'AAPL250117P00150000',
                'expiration': '2025-01-17',
                'strike': 150.0,
                'type': 'put',
                'bid': 2.50,
                'ask': 2.55,
                'volume': 100,
                'open_interest': 500,
                'delta': -0.30,
                'gamma': 0.05,
                'theta': -0.02,
                'vega': 0.15,
                'implied_volatility': 0.35
            }
        """
        try:
            # Use OpenBB client to get options chain with Greeks
            chain_data = self.openbb_client.get_options_chains(symbol, provider='yfinance')

            if not chain_data or 'results' not in chain_data:
                logging.debug(f"[SPREAD] No options chain data for {symbol}")
                return []

            options = chain_data['results']

            if not isinstance(options, list):
                logging.debug(f"[SPREAD] Invalid options format for {symbol}")
                return []

            # Filter and normalize the options data
            normalized_options = []
            for opt in options:
                try:
                    # Extract required fields
                    normalized_opt = {
                        'symbol': opt.get('contract_symbol', ''),
                        'expiration': opt.get('expiration', ''),
                        'strike': float(opt.get('strike', 0)),
                        'type': opt.get('option_type', '').lower(),  # 'call' or 'put'
                        'bid': float(opt.get('bid', 0)),
                        'ask': float(opt.get('ask', 0)),
                        'volume': int(opt.get('volume', 0)),
                        'open_interest': int(opt.get('open_interest', 0)),
                        'delta': float(opt.get('delta', 0)),
                        'gamma': float(opt.get('gamma', 0)),
                        'theta': float(opt.get('theta', 0)),
                        'vega': float(opt.get('vega', 0)),
                        'implied_volatility': float(opt.get('implied_volatility', 0))
                    }

                    # Only include options with valid data
                    if normalized_opt['strike'] > 0 and normalized_opt['expiration']:
                        normalized_options.append(normalized_opt)

                except (ValueError, TypeError) as e:
                    logging.debug(f"[SPREAD] Error normalizing option for {symbol}: {e}")
                    continue

            logging.debug(f"[SPREAD] Found {len(normalized_options)} options for {symbol}")
            return normalized_options

        except Exception as e:
            logging.error(f"[SPREAD] Error getting options chain for {symbol}: {e}")
            return []

    def _find_target_expiration(self, options_chain: List[Dict]) -> Optional[str]:
        """Find expiration closest to TARGET_DTE"""
        expirations = sorted(set(opt['expiration'] for opt in options_chain))
        target_date = datetime.now() + timedelta(days=self.TARGET_DTE)

        closest_exp = None
        min_diff = float('inf')

        for exp in expirations:
            exp_date = datetime.strptime(exp, '%Y-%m-%d')
            dte = (exp_date - datetime.now()).days

            if self.MIN_DTE <= dte <= self.MAX_DTE:
                diff = abs(dte - self.TARGET_DTE)
                if diff < min_diff:
                    min_diff = diff
                    closest_exp = exp

        return closest_exp

    def _find_closest_strike(self, options: List[Dict], target_strike: float, leg_type: str) -> Optional[Dict]:
        """Find option closest to target strike with adequate liquidity"""
        if not options:
            return None

        # Filter by liquidity (min 10 volume OR 100 open interest)
        liquid_options = [opt for opt in options
                          if opt.get('volume', 0) >= 10 or opt.get('open_interest', 0) >= 100]

        if not liquid_options:
            liquid_options = options  # Fall back to all options if none meet liquidity

        # Find closest to target
        closest = min(liquid_options, key=lambda x: abs(x['strike'] - target_strike))

        return closest

    def get_symbol_sector(self, symbol: str) -> str:
        """
        Get sector classification for a symbol.
        Uses same sector map as Wheel strategy for consistency.
        """
        for sector, symbols in self.config.SECTORS.items():
            if symbol in symbols:
                return sector
        return 'OTHER'

    def can_add_symbol_by_sector(self, symbol: str, spread_manager) -> bool:
        """
        Check if adding this symbol would violate sector diversification limits.

        Prevents concentration risk like 40% in one sector.
        Uses same MAX_SECTOR_POSITIONS limit as Wheel strategy.

        Args:
            symbol: Stock symbol to check
            spread_manager: SpreadManager instance for current positions

        Returns:
            True if symbol can be added without violating sector limits
        """
        sector = self.get_symbol_sector(symbol)

        # Get current spread positions in this sector
        all_positions = spread_manager.get_all_positions()
        sector_count = sum(1 for pos in all_positions
                          if self.get_symbol_sector(pos['symbol']) == sector)

        if sector_count >= self.config.MAX_SECTOR_POSITIONS:
            logging.warning(f"[SPREAD] {symbol}: Sector '{sector}' limit reached "
                          f"({sector_count}/{self.config.MAX_SECTOR_POSITIONS} positions)")
            return False

        return True

    def calculate_position_size(self, spread: Dict, available_capital: float) -> int:
        """
        Calculate number of contracts for this spread based on account size and risk limits.

        Position Sizing Rules:
        1. Max 10% of account value per spread (MAX_CAPITAL_PER_POSITION)
        2. Max $500 risk per spread (MAX_CAPITAL_PER_SPREAD)
        3. Use the more conservative of the two limits

        Args:
            spread: Spread candidate dict with 'max_risk' field
            available_capital: Total account portfolio value

        Returns:
            Number of contracts (minimum 1, maximum based on risk limits)
        """
        symbol = spread['symbol']
        capital_per_contract = spread['max_risk']

        # Rule 1: Max contracts based on % of portfolio
        max_contracts_by_pct = int((available_capital * self.MAX_CAPITAL_PER_POSITION) / capital_per_contract)

        # Rule 2: Max contracts based on absolute max risk per spread
        max_contracts_by_risk = int(self.MAX_CAPITAL_PER_SPREAD / capital_per_contract)

        # Use the smaller (more conservative) of the two
        contracts = min(max_contracts_by_pct, max_contracts_by_risk)

        # Minimum 1 contract (always trade at least 1 spread if qualified)
        contracts = max(1, contracts)

        # Calculate actual capital required
        capital_required = capital_per_contract * contracts
        pct_of_account = (capital_required / available_capital * 100) if available_capital > 0 else 0

        logging.info(f"[SPREAD] Position size for {symbol}: {contracts} contract(s)")
        logging.info(f"[SPREAD]   Capital required: ${capital_required:.0f} ({pct_of_account:.1f}% of ${available_capital:,.0f} account)")
        logging.info(f"[SPREAD]   Max risk per contract: ${capital_per_contract:.0f}")
        logging.info(f"[SPREAD]   Limit by % of account: {max_contracts_by_pct} contracts")
        logging.info(f"[SPREAD]   Limit by max risk: {max_contracts_by_risk} contracts")

        return contracts

    def check_vix_throttle(self) -> bool:
        """
        Check if VIX is too high to enter new spreads.

        Returns:
            True if safe to trade, False if VIX too high
        """
        try:
            vix = self._get_vix()
            if vix > 30:
                logging.warning(f"[SPREAD] VIX at {vix:.1f} - pausing new spread entries (protection)")
                return False
            return True
        except Exception as e:
            logging.warning(f"[SPREAD] Could not fetch VIX, assuming safe: {e}")
            return True

    def _get_vix(self) -> float:
        """
        Get current VIX level from market data.

        Returns:
            Current VIX value, or 15.0 as fallback if fetch fails
        """
        try:
            # Try primary source: OpenBB quote for VIX
            vix_data = self.openbb_client.get_quote('VIX')

            if not vix_data or 'results' not in vix_data:
                logging.debug(f"[SPREAD] VIX data response: {vix_data}")
                logging.warning(f"[SPREAD] No VIX data in expected format, using fallback 15.0")
                return 15.0

            results = vix_data['results']

            # Handle both list and dict response formats
            if isinstance(results, list) and len(results) > 0:
                vix_price = results[0].get('last_price') or results[0].get('close') or results[0].get('price')
            elif isinstance(results, dict):
                vix_price = results.get('last_price') or results.get('close') or results.get('price')
            else:
                vix_price = None
                logging.debug(f"[SPREAD] VIX results format unexpected: {type(results)}, {results}")

            if vix_price and vix_price > 0:
                logging.debug(f"[SPREAD] VIX fetched: {vix_price:.2f}")
                return float(vix_price)
            else:
                # Log the actual data structure to help debug
                logging.warning(f"[SPREAD] Could not extract VIX price from data structure")
                logging.debug(f"[SPREAD] VIX data keys: {vix_data.keys() if isinstance(vix_data, dict) else 'not a dict'}")
                logging.debug(f"[SPREAD] VIX results sample: {str(results)[:200]}")
                return 15.0

        except Exception as e:
            logging.warning(f"[SPREAD] Error fetching VIX (using fallback 15.0): {e}")
            logging.debug(f"[SPREAD] VIX fetch exception details", exc_info=True)
            return 15.0
