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
        self.PROFIT_TARGET_PCT = config.SPREAD_PROFIT_TARGET_PCT  # 50% profit
        self.STOP_LOSS_PCT = config.SPREAD_STOP_LOSS_PCT  # -100% (let spread max out)

        logging.info(f"[SPREAD] Initialized with criteria: ${self.MIN_STOCK_PRICE}-${self.MAX_STOCK_PRICE}, "
                    f"IV rank {self.MIN_IV_RANK}%+, Spread width ${self.SPREAD_WIDTH}")

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
        - Spreads look for ELEVATED IV (20%+ rank) - sell premium when elevated

        This means spreads may find ZERO candidates when market is calm (VIX <15)
        """
        filtered = []
        rejection_reasons = {'price': 0, 'iv_rank': 0, 'market_cap': 0}
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

            filtered.append(stock)
            logging.info(f"[SPREAD FILTER] ✓ {symbol}: price ${price:.2f}, IV {iv_rank:.1f}%, cap ${market_cap/1e9:.2f}B")

        # Log why stocks were rejected with details
        if not filtered:
            logging.warning(f"[SPREAD] ❌ No stocks passed filters out of {len(stocks)} candidates")
            logging.warning(f"[SPREAD] Rejections: price={rejection_reasons['price']}, "
                          f"iv_rank={rejection_reasons['iv_rank']} (need >={self.MIN_IV_RANK}%), "
                          f"market_cap={rejection_reasons['market_cap']}")
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

        # Find short strike (10-20% OTM for better credit collection)
        # 25% OTM was too far - not collecting enough premium
        short_strike_target = stock_price * 0.85  # 15% OTM as starting point (better credit)
        short_put = self._find_closest_strike(puts, short_strike_target, 'short')

        if not short_put:
            logging.warning(f"[SPREAD] {symbol}: No short put found near ${short_strike_target:.2f} "
                          f"(15% OTM from ${stock_price:.2f})")
            return None

        # Validate short strike delta (should be -0.20 to -0.40 for 10-20% OTM)
        short_delta = short_put.get('delta', 0)
        if short_delta != 0:  # Only validate if delta is available
            if not (-0.40 <= short_delta <= -0.15):
                logging.warning(f"[SPREAD] {symbol}: Short put delta {short_delta:.3f} outside optimal range "
                              f"(-0.40 to -0.15). May not be properly OTM.")
                # Continue anyway - delta might be stale or inaccurate
            else:
                logging.debug(f"[SPREAD] {symbol}: Short put delta {short_delta:.3f} ✓ (within optimal range)")

        # Find long strike (SPREAD_WIDTH below short strike)
        long_strike_target = short_put['strike'] - self.SPREAD_WIDTH
        long_put = self._find_closest_strike(puts, long_strike_target, 'long')

        if not long_put:
            logging.warning(f"[SPREAD] {symbol}: No long put found near ${long_strike_target:.2f} "
                          f"(${self.SPREAD_WIDTH:.0f} below short ${short_put['strike']:.2f})")
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

        # Validate spread
        if credit < self.MIN_CREDIT:
            logging.warning(f"[SPREAD] {symbol}: Credit ${credit:.2f} (short bid ${short_premium:.2f} - long ask ${long_premium:.2f}) "
                          f"below minimum ${self.MIN_CREDIT:.2f}")
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
            vix_data = self.openbb_client.get_quote('VIX')

            if not vix_data or 'results' not in vix_data:
                logging.warning(f"[SPREAD] No VIX data returned, using fallback 15.0")
                return 15.0

            results = vix_data['results']

            # Handle both list and dict response formats
            if isinstance(results, list) and len(results) > 0:
                vix_price = results[0].get('last_price') or results[0].get('close') or results[0].get('price')
            elif isinstance(results, dict):
                vix_price = results.get('last_price') or results.get('close') or results.get('price')
            else:
                vix_price = None

            if vix_price and vix_price > 0:
                logging.debug(f"[SPREAD] VIX fetched: {vix_price:.2f}")
                return float(vix_price)
            else:
                logging.warning(f"[SPREAD] Invalid VIX data, using fallback 15.0")
                return 15.0

        except Exception as e:
            logging.warning(f"[SPREAD] Error fetching VIX (using fallback 15.0): {e}")
            return 15.0
