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
        logging.info(f"[SPREAD] Scanning for bull put spread opportunities...")

        # Step 1: Get stock universe from scanner
        try:
            stocks = self._build_stock_universe()
            logging.info(f"[SPREAD] Built base universe with {len(stocks)} stocks")
        except Exception as e:
            logging.error(f"[SPREAD] Error building universe: {e}")
            return []

        # Step 2: Apply filters
        filtered_stocks = self._apply_filters(stocks)
        logging.info(f"[SPREAD] After filters: {len(filtered_stocks)} stocks")

        if not filtered_stocks:
            logging.warning("[SPREAD] No stocks passed filters")
            return []

        # Step 3: Find optimal spreads for each stock
        candidates = []
        for stock in filtered_stocks:
            try:
                spread = self._find_optimal_spread(stock)
                if spread:
                    candidates.append(spread)
            except Exception as e:
                logging.debug(f"[SPREAD] Error finding spread for {stock['symbol']}: {e}")
                continue

        if not candidates:
            logging.warning("[SPREAD] No spread candidates found")
            return []

        # Step 4: Sort by risk-adjusted return (ROI / max_risk)
        candidates.sort(key=lambda x: x['annual_return'] / max(x['max_risk'], 100), reverse=True)

        logging.info(f"[SPREAD] Found {len(candidates)} spread candidates, returning top {max_candidates}")

        # Log top candidates
        for i, candidate in enumerate(candidates[:max_candidates], 1):
            logging.info(f"[SPREAD]   {i}. {candidate['symbol']}: {candidate['annual_return']:.1f}% annual "
                        f"(credit ${candidate['credit']:.2f}, risk ${candidate['max_risk']:.0f}, ROI {candidate['roi']:.1f}%)")

        return candidates[:max_candidates]

    def _build_stock_universe(self) -> List[Dict]:
        """Build stock universe using scanner"""
        # Use scanner's market scan to get candidates
        try:
            candidates = self.scanner.scan_market_for_opportunities()

            # Convert scanner format to our format
            universe = []
            for candidate in candidates:
                try:
                    stock_info = {
                        'symbol': candidate.get('symbol'),
                        'price': candidate.get('stock_price', 0),
                        'iv_rank': candidate.get('iv_rank', 0),
                        'market_cap': candidate.get('market_cap', 0),
                        'volume': candidate.get('volume', 0)
                    }
                    universe.append(stock_info)
                except Exception as e:
                    logging.debug(f"[SPREAD] Error processing candidate {candidate.get('symbol')}: {e}")
                    continue

            return universe
        except Exception as e:
            logging.error(f"[SPREAD] Error scanning market: {e}")
            return []

    def _apply_filters(self, stocks: List[Dict]) -> List[Dict]:
        """Apply spread-specific filters"""
        filtered = []

        for stock in stocks:
            # Filter by price range
            if not (self.MIN_STOCK_PRICE <= stock['price'] <= self.MAX_STOCK_PRICE):
                continue

            # Filter by IV rank
            if stock.get('iv_rank', 0) < self.MIN_IV_RANK:
                continue

            # Filter by market cap
            if stock.get('market_cap', 0) < self.MIN_MARKET_CAP:
                continue

            filtered.append(stock)

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
                return None
        except Exception as e:
            logging.debug(f"[SPREAD] Error getting options for {symbol}: {e}")
            return None

        # Find target DTE expiration
        target_expiration = self._find_target_expiration(options_chain)
        if not target_expiration:
            return None

        # Get puts for target expiration
        puts = [opt for opt in options_chain if opt['expiration'] == target_expiration and opt['type'] == 'put']

        if len(puts) < 2:
            return None

        # Find short strike (25-35% OTM, delta around -0.25 to -0.35)
        short_strike_target = stock_price * 0.75  # 25% OTM as starting point
        short_put = self._find_closest_strike(puts, short_strike_target, 'short')

        if not short_put:
            return None

        # Find long strike (SPREAD_WIDTH below short strike)
        long_strike_target = short_put['strike'] - self.SPREAD_WIDTH
        long_put = self._find_closest_strike(puts, long_strike_target, 'long')

        if not long_put:
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
            logging.debug(f"[SPREAD] {symbol}: Credit ${credit:.2f} below minimum ${self.MIN_CREDIT:.2f}")
            return None

        if max_risk > self.MAX_CAPITAL_PER_SPREAD:
            logging.debug(f"[SPREAD] {symbol}: Max risk ${max_risk:.0f} exceeds limit ${self.MAX_CAPITAL_PER_SPREAD:.0f}")
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
            'liquidity_score': min(short_put.get('volume', 0), long_put.get('volume', 0))
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
        Calculate number of contracts for this spread.

        Args:
            spread: Spread candidate dict
            available_capital: Available capital in account

        Returns:
            Number of contracts (spreads)
        """
        # Calculate capital per contract
        capital_per_contract = spread['max_risk']

        # Max contracts based on position limit
        max_contracts_by_limit = int((available_capital * self.MAX_CAPITAL_PER_POSITION) / capital_per_contract)

        # Max contracts based on absolute max risk
        max_contracts_by_risk = int(self.MAX_CAPITAL_PER_SPREAD / capital_per_contract)

        # Use the smaller of the two
        contracts = min(max_contracts_by_limit, max_contracts_by_risk)

        # Minimum 1 contract
        contracts = max(1, contracts)

        logging.info(f"[SPREAD] Position size for {spread['symbol']}: {contracts} contracts "
                    f"(${capital_per_contract * contracts:.0f} capital required)")

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
        """Get current VIX level"""
        # Implementation depends on your data source
        # For now, return 15 (typical low-vol level)
        return 15.0
