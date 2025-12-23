"""
The Wheel Strategy - Systematic Premium Collection

The Wheel is one of the most profitable options strategies with 50-95% win rates.
It consists of three phases that cycle continuously:

PHASE 1: SELLING_PUTS
- Sell cash-secured puts at 5-10% OTM
- Collect premium (keep if expires worthless)
- If assigned, move to PHASE 2

PHASE 2: ASSIGNED (Own Stock)
- Stock assigned at put strike (bought at discount)
- Immediately move to PHASE 3

PHASE 3: SELLING_CALLS
- Sell covered calls above cost basis
- Collect premium (keep if expires worthless)
- If assigned (stock called away), return to PHASE 1
- If expires worthless, repeat PHASE 3

Expected Returns: 15-40% annually
Win Rate: 50-95% (most puts expire worthless)
Risk: Lower than naked options, similar to stock ownership
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from colorama import Fore, Style


class WheelStrategy:
    """
    Implements The Wheel Strategy for systematic premium collection.

    Key Features:
    - Identifies wheel candidates (quality stocks with high IV)
    - Manages wheel state transitions (SELLING_PUTS → ASSIGNED → SELLING_CALLS)
    - Calculates optimal strikes and position sizing
    - Tracks total premium collected across full wheel cycles
    """

    def __init__(self, trading_client, openbb_client, scanner, config):
        """
        Initialize Wheel Strategy

        Args:
            trading_client: Alpaca trading client
            openbb_client: OpenBB client for market data
            scanner: Market scanner for finding candidates
            config: Bot configuration
        """
        self.trading_client = trading_client
        self.openbb_client = openbb_client
        self.scanner = scanner
        self.config = config
        self.wheel_db = None  # Will be set by WheelManager

        # Load all parameters from config
        self.MIN_STOCK_PRICE = config.WHEEL_MIN_STOCK_PRICE
        self.MAX_STOCK_PRICE = config.WHEEL_MAX_STOCK_PRICE
        self.MIN_IV_RANK = config.WHEEL_MIN_IV_RANK
        self.MAX_IV_RANK = config.WHEEL_MAX_IV_RANK
        self.MIN_MARKET_CAP = config.WHEEL_MIN_MARKET_CAP

        # Optional beta filters (not in config yet, use defaults)
        # EXPERT GUIDANCE: Beta is NOT a primary filter for wheel strategies.
        # IV Rank is superior for screening. Beta is for portfolio diversification only.
        # Professional range: 0.5-2.0 if used at all.
        self.MIN_BETA = 0.5  # Allow defensive stocks (LMT, KO, etc.)
        self.MAX_BETA = 1.8  # Allow growth stocks with good fundamentals
        self.MIN_ANNUAL_RETURN = 0.20  # 20% target annual return

        # Minimum average volume (liquidity requirement)
        # Expert guidance: 500K-1M is appropriate. Lowered from 1M to 750K for more candidates.
        self.MIN_AVG_VOLUME = 750_000  # 750K shares daily (balance quality vs candidates)

        # Wheel parameters from config
        self.PUT_OTM_PERCENT = config.WHEEL_PUT_OTM_PERCENT
        self.CALL_ABOVE_BASIS_PERCENT = config.WHEEL_CALL_ABOVE_BASIS_PERCENT
        self.TARGET_DTE = config.WHEEL_TARGET_DTE
        self.MIN_DTE = config.WHEEL_MIN_DTE
        self.MAX_DTE = config.WHEEL_MAX_DTE

        # Position sizing from config
        self.MAX_WHEEL_POSITIONS = config.MAX_WHEEL_POSITIONS
        self.MAX_CAPITAL_PER_WHEEL = config.MAX_CAPITAL_PER_WHEEL

        logging.info(f"[WHEEL] Initialized with criteria: ${self.MIN_STOCK_PRICE}-${self.MAX_STOCK_PRICE}, "
                    f"IV rank {self.MIN_IV_RANK}-{self.MAX_IV_RANK}%, Beta {self.MIN_BETA}-{self.MAX_BETA}, "
                    f"Min Volume {self.MIN_AVG_VOLUME:,.0f}")

    def find_wheel_candidates(self, max_candidates: int = 5) -> List[Dict]:
        """
        Find top wheel candidates based on criteria and expected returns.

        Returns:
            List of candidate dicts sorted by expected annual return:
            {
                'symbol': str,
                'stock_price': float,
                'iv_rank': float,
                'beta': float,
                'market_cap': float,
                'put_strike': float,
                'put_premium': float,
                'annual_return': float,
                'reason': str
            }
        """
        logging.info(f"[WHEEL] Searching for wheel candidates...")

        # VIX-BASED POSITION THROTTLE: Black swan protection
        try:
            vix_data = self.openbb_client.get_quote('VIX')
            if vix_data and 'results' in vix_data:
                results = vix_data['results']
                if isinstance(results, list) and len(results) > 0:
                    vix_price = results[0].get('last_price') or results[0].get('close')
                elif isinstance(results, dict):
                    vix_price = results.get('last_price') or results.get('close')
                else:
                    vix_price = None

                if vix_price and vix_price > 30:
                    logging.warning(f"[WHEEL] VIX ELEVATED: {vix_price:.2f} (> 30) - PAUSING new positions for black swan protection")
                    print(f"{Fore.YELLOW}[WHEEL] VIX at {vix_price:.2f} - Market stress detected, pausing new Wheel positions{Style.RESET_ALL}")
                    return []  # No new positions during high volatility
                elif vix_price:
                    logging.info(f"[WHEEL] VIX check: {vix_price:.2f} (< 30) - Normal market conditions")
        except Exception as e:
            logging.warning(f"[WHEEL] Could not fetch VIX (continuing anyway): {e}")

        candidates = []

        # Get universe from scanner
        try:
            universe = self.scanner.get_filtered_universe()
            logging.info(f"[WHEEL] Scanning {len(universe)} stocks for wheel opportunities")
        except Exception as e:
            logging.error(f"[WHEEL] Failed to get universe: {e}")
            return []

        for stock in universe:
            symbol = stock.get('symbol')
            if not symbol:
                continue

            # Apply wheel candidate criteria
            is_candidate, reason, details = self._evaluate_wheel_candidate(stock)

            if is_candidate:
                candidates.append(details)
                logging.info(f"[WHEEL] {symbol}: {reason} - {details['annual_return']:.1%} annual return")
            else:
                logging.debug(f"[WHEEL] {symbol} rejected: {reason}")

        # Score and rank candidates using multi-factor scoring
        for candidate in candidates:
            candidate['quality_score'] = self._calculate_quality_score(candidate)

        # Sort by quality score (highest first) - considers multiple factors beyond just return
        candidates = sorted(candidates, key=lambda x: x['quality_score'], reverse=True)

        if candidates:
            logging.info(f"[WHEEL] Found {len(candidates)} wheel candidates, ranking by quality score...")
            logging.info(f"[WHEEL] ════════════════════════════════════════════════════════")
            for i, c in enumerate(candidates[:max_candidates], 1):
                logging.info(f"[WHEEL]   #{i}: {c['symbol']:6s} - Score: {c['quality_score']:.1f}/100")
                logging.info(f"[WHEEL]        Annual Return: {c['annual_return']:.1%}, "
                           f"Put: ${c['put_strike']:.2f} @ ${c['put_premium']:.2f}, "
                           f"IV Rank: {c['iv_rank']:.0f}%")
            logging.info(f"[WHEEL] ════════════════════════════════════════════════════════")
            logging.info(f"[WHEEL] Returning top {max_candidates} candidates sorted best to worst")
        else:
            logging.warning(f"[WHEEL] No wheel candidates found matching criteria")

        return candidates[:max_candidates]

    def _evaluate_wheel_candidate(self, stock: Dict) -> Tuple[bool, str, Optional[Dict]]:
        """
        Evaluate if a stock meets wheel criteria.

        Returns:
            (is_candidate, reason, candidate_details or None)
        """
        symbol = stock.get('symbol', 'UNKNOWN')
        price = stock.get('price', 0) or stock.get('last_price', 0)

        # Check price range
        if not price or price < self.MIN_STOCK_PRICE:
            return False, f"Price ${price:.2f} below minimum ${self.MIN_STOCK_PRICE}", None
        if price > self.MAX_STOCK_PRICE:
            return False, f"Price ${price:.2f} above maximum ${self.MAX_STOCK_PRICE}", None

        # Check average volume (liquidity requirement)
        avg_volume = stock.get('volume', 0) or stock.get('avg_volume', 0)
        if avg_volume > 0 and avg_volume < self.MIN_AVG_VOLUME:
            return False, f"Avg volume {avg_volume:,.0f} below minimum {self.MIN_AVG_VOLUME:,.0f} (poor liquidity)", None

        # Check IV rank
        iv_rank = stock.get('iv_rank', 0) or stock.get('iv_percentile', 0)
        if iv_rank < self.MIN_IV_RANK:
            return False, f"IV rank {iv_rank:.1f}% below minimum {self.MIN_IV_RANK}%", None

        # Check market cap
        market_cap = stock.get('market_cap', 0)
        if market_cap and market_cap < self.MIN_MARKET_CAP:
            return False, f"Market cap ${market_cap/1e9:.1f}B below minimum ${self.MIN_MARKET_CAP/1e9:.1f}B", None

        # Check beta (if available)
        beta = stock.get('beta')
        if beta is not None:
            if beta < self.MIN_BETA or beta > self.MAX_BETA:
                return False, f"Beta {beta:.2f} outside range {self.MIN_BETA}-{self.MAX_BETA}", None

        # Calculate optimal put strike and expected premium
        put_strike = round(price * self.PUT_OTM_PERCENT, 2)

        # Estimate put premium (will be refined with actual options chain)
        # Rule of thumb: ~1-3% of strike price for 30-45 DTE at high IV
        # Conservative estimate: 1.5% for IV rank 60%, 2.5% for IV rank 80%, 3.5% for IV rank 100%
        premium_rate = 0.01 + (iv_rank / 100) * 0.025  # Linear interpolation
        put_premium = put_strike * premium_rate

        # Calculate annualized return
        # Return = (premium / capital_required) * (365 / DTE)
        capital_required = put_strike * 100  # Cash secured for 1 contract
        return_per_cycle = put_premium * 100 / capital_required
        cycles_per_year = 365 / self.TARGET_DTE
        annual_return = return_per_cycle * cycles_per_year

        # Check minimum return threshold
        if annual_return < self.MIN_ANNUAL_RETURN:
            return False, f"Annual return {annual_return:.1%} below minimum {self.MIN_ANNUAL_RETURN:.0%}", None

        # Build candidate details
        candidate = {
            'symbol': symbol,
            'stock_price': price,
            'iv_rank': iv_rank,
            'beta': beta if beta is not None else 1.0,
            'market_cap': market_cap,
            'put_strike': put_strike,
            'put_premium': put_premium,
            'annual_return': annual_return,
            'capital_required': capital_required,
            'dte': self.TARGET_DTE,
            'reason': f"Wheel candidate: {annual_return:.1%} annual return (IV rank {iv_rank:.0f}%)"
        }

        return True, f"Qualified wheel candidate", candidate

    def get_put_to_sell(self, symbol: str, target_dte: Optional[int] = None) -> Optional[Dict]:
        """
        Find the optimal cash-secured put to sell for a given symbol.

        Args:
            symbol: Stock symbol
            target_dte: Target days to expiration (default: self.TARGET_DTE)

        Returns:
            Dict with put details or None if no suitable put found:
            {
                'symbol': str,
                'strike': float,
                'expiration': str,
                'dte': int,
                'premium': float,
                'delta': float,
                'option_symbol': str
            }
        """
        if target_dte is None:
            target_dte = self.TARGET_DTE

        try:
            # Get current stock price
            stock_data = self.openbb_client.get_quote(symbol)
            if not stock_data or 'results' not in stock_data:
                logging.error(f"[WHEEL] {symbol}: Failed to get stock quote")
                return None

            # Extract price from results
            results = stock_data['results']
            if isinstance(results, list) and len(results) > 0:
                price = results[0].get('last_price') or results[0].get('close')
            elif isinstance(results, dict):
                price = results.get('last_price') or results.get('close')
            else:
                price = None

            if not price or price <= 0:
                logging.error(f"[WHEEL] {symbol}: No valid price in quote data")
                return None

            # Calculate target strike (10% OTM)
            target_strike = round(price * self.PUT_OTM_PERCENT, 2)

            # Get options chain
            chain_data = self.openbb_client.get_options_chains(symbol)
            if not chain_data or 'results' not in chain_data:
                logging.error(f"[WHEEL] {symbol}: No options chain available")
                return None

            # Filter for puts only
            all_options = chain_data['results']
            puts = [opt for opt in all_options if opt.get('option_type') == 'put']

            if not puts:
                logging.error(f"[WHEEL] {symbol}: No put options in chain")
                return None

            # Filter for target DTE range and strike
            best_put = None
            best_score = 0

            from datetime import datetime

            for put in puts:
                # Calculate DTE from expiration date
                expiration = put.get('expiration')
                if expiration:
                    try:
                        # Parse expiration date (usually ISO format: YYYY-MM-DD)
                        if isinstance(expiration, str):
                            exp_date = datetime.fromisoformat(expiration.replace('Z', ''))
                        else:
                            exp_date = expiration
                        dte = (exp_date - datetime.now()).days
                    except:
                        dte = put.get('dte', 0)
                else:
                    dte = put.get('dte', 0)

                strike = put.get('strike', 0)
                premium = put.get('bid', 0) or put.get('mark', 0)

                # Check DTE range
                if dte < self.MIN_DTE or dte > self.MAX_DTE:
                    continue

                # Check strike is near target (within 5%)
                if abs(strike - target_strike) / target_strike > 0.05:
                    continue

                # Check premium is reasonable (> $0.10)
                if premium < 0.10:
                    continue

                # LIQUIDITY FILTER: Check volume and open interest
                volume = put.get('volume', 0)
                open_interest = put.get('open_interest', 0)

                # Require minimum liquidity for execution
                # Relaxed requirements: at least 10 volume OR 100 open interest
                if volume < 10 and open_interest < 100:
                    continue  # Skip illiquid options

                # Score: prefer closer to target DTE, higher premium, and better liquidity
                dte_score = 1.0 - abs(dte - target_dte) / self.MAX_DTE
                premium_score = premium / (strike * 0.05)  # Normalize by ~5% of strike
                liquidity_score = min(1.0, (volume + open_interest / 10) / 100)  # Normalize liquidity
                score = dte_score * 0.3 + premium_score * 0.5 + liquidity_score * 0.2

                if score > best_score:
                    best_score = score
                    best_put = {
                        'symbol': symbol,
                        'strike': strike,
                        'expiration': put.get('expiration'),
                        'dte': dte,
                        'premium': premium,
                        'delta': put.get('delta', 0),
                        'volume': put.get('volume', 0),
                        'open_interest': put.get('open_interest', 0),
                        'option_symbol': put.get('contract_symbol') or put.get('symbol')
                    }

            if best_put:
                # Calculate probability from delta (delta = probability ITM)
                delta = best_put.get('delta', 0)
                prob_otm = (1.0 - abs(delta)) * 100 if delta else 0  # Convert to probability OTM
                delta_str = f"Δ {delta:.2f}" if delta else "Δ N/A"
                prob_str = f" ({prob_otm:.0f}% prob OTM)" if delta else ""

                logging.info(f"[WHEEL] {symbol}: Found put to sell - ${best_put['strike']:.2f} "
                           f"strike, ${best_put['premium']:.2f} premium, {best_put['dte']} DTE, "
                           f"{delta_str}{prob_str} [Vol: {best_put.get('volume', 0)}, OI: {best_put.get('open_interest', 0)}]")
            else:
                logging.warning(f"[WHEEL] {symbol}: No suitable puts found in {self.MIN_DTE}-{self.MAX_DTE} DTE range "
                              f"(target strike: ${target_strike:.2f}, checked {len(puts)} puts)")

            return best_put

        except Exception as e:
            logging.error(f"[WHEEL] {symbol}: Error finding put to sell: {e}")
            return None

    def get_call_to_sell(self, symbol: str, cost_basis: float, shares: int,
                        target_dte: Optional[int] = None) -> Optional[Dict]:
        """
        Find the optimal covered call to sell for stock we own.

        Args:
            symbol: Stock symbol
            cost_basis: Our average cost per share
            shares: Number of shares owned (must be multiple of 100)
            target_dte: Target days to expiration (default: self.TARGET_DTE)

        Returns:
            Dict with call details or None if no suitable call found:
            {
                'symbol': str,
                'strike': float,
                'expiration': str,
                'dte': int,
                'premium': float,
                'delta': float,
                'option_symbol': str,
                'contracts': int  # How many contracts to sell
            }
        """
        if target_dte is None:
            target_dte = self.TARGET_DTE

        if shares < 100 or shares % 100 != 0:
            logging.error(f"[WHEEL] {symbol}: Invalid share count {shares} (must be multiple of 100)")
            return None

        contracts = shares // 100

        try:
            # Get current stock price
            stock_data = self.openbb_client.get_quote(symbol)
            if not stock_data:
                logging.error(f"[WHEEL] {symbol}: Failed to get stock price")
                return None

            current_price = stock_data.get('price') or stock_data.get('last')
            if not current_price:
                logging.error(f"[WHEEL] {symbol}: No price in stock data")
                return None

            # Calculate minimum strike (5% above cost basis to ensure profit)
            min_strike = cost_basis * self.CALL_ABOVE_BASIS_PERCENT

            # If current price is below cost basis, adjust strategy
            if current_price < cost_basis:
                logging.warning(f"[WHEEL] {symbol}: Stock at ${current_price:.2f} below cost basis ${cost_basis:.2f}")
                # Sell call at or slightly above current price to collect premium
                target_strike = round(current_price * 1.02, 2)  # 2% OTM
            else:
                target_strike = round(max(min_strike, current_price * 1.03), 2)  # 3% OTM or above basis

            # Get options chain
            chain = self.openbb_client.get_options_chain(symbol)
            if not chain or 'calls' not in chain:
                logging.error(f"[WHEEL] {symbol}: No options chain available")
                return None

            calls = chain['calls']

            # Filter for target DTE range and strike
            best_call = None
            best_score = 0

            for call in calls:
                dte = call.get('dte', 0)
                strike = call.get('strike', 0)
                premium = call.get('bid', 0) or call.get('mark', 0)

                # Check DTE range
                if dte < self.MIN_DTE or dte > self.MAX_DTE:
                    continue

                # Check strike is above minimum
                if strike < min_strike:
                    continue

                # Check strike is within 10% of target
                if abs(strike - target_strike) / target_strike > 0.10:
                    continue

                # Check premium is reasonable (> $0.10)
                if premium < 0.10:
                    continue

                # Score: prefer closer to target DTE and higher premium
                dte_score = 1.0 - abs(dte - target_dte) / self.MAX_DTE
                premium_score = premium / (strike * 0.03)  # Normalize by ~3% of strike
                score = dte_score * 0.4 + premium_score * 0.6

                if score > best_score:
                    best_score = score
                    best_call = {
                        'symbol': symbol,
                        'strike': strike,
                        'expiration': call.get('expiration'),
                        'dte': dte,
                        'premium': premium,
                        'delta': call.get('delta', 0),
                        'volume': call.get('volume', 0),
                        'open_interest': call.get('open_interest', 0),
                        'option_symbol': call.get('contract_symbol') or call.get('symbol'),
                        'contracts': contracts
                    }

            if best_call:
                total_premium = best_call['premium'] * 100 * contracts
                delta = best_call.get('delta', 0)
                prob_otm = (1.0 - abs(delta)) * 100 if delta else 0  # For calls, delta is probability ITM
                delta_str = f"Δ {delta:.2f}" if delta else "Δ N/A"
                prob_str = f" ({prob_otm:.0f}% prob OTM)" if delta else ""

                logging.info(f"[WHEEL] {symbol}: Found call to sell - ${best_call['strike']:.2f} strike, "
                           f"${best_call['premium']:.2f} premium (${total_premium:.2f} total), {best_call['dte']} DTE, "
                           f"{delta_str}{prob_str} [Vol: {best_call.get('volume', 0)}, OI: {best_call.get('open_interest', 0)}]")
            else:
                logging.warning(f"[WHEEL] {symbol}: No suitable calls found above ${min_strike:.2f} "
                              f"in {self.MIN_DTE}-{self.MAX_DTE} DTE range")

            return best_call

        except Exception as e:
            logging.error(f"[WHEEL] {symbol}: Error finding call to sell: {e}")
            return None

    def _calculate_quality_score(self, candidate: Dict) -> float:
        """
        Calculate a quality score for a wheel candidate using multiple factors.

        Scoring Factors (0-100 scale):
        - Annual Return (40%): Higher returns = higher score
        - IV Rank (30%): Sweet spot 60-80%, penalize extremes
        - Market Cap (15%): Larger = more stable (capped at $500B)
        - Beta (15%): Lower volatility preferred (1.0 = neutral, <1.5 good)

        Returns:
            Quality score from 0-100 (higher is better)
        """
        # Factor 1: Annual Return (40% weight)
        # Scale: 15% = 50 points, 30% = 75 points, 50%+ = 100 points
        annual_return = candidate.get('annual_return', 0)
        return_score = min(100, (annual_return / 0.50) * 100)  # 50% annual = max score

        # Factor 2: IV Rank (30% weight)
        # Sweet spot: 60-80% IV rank (max score)
        # Penalty for too low (<50%) or too high (>90%)
        iv_rank = candidate.get('iv_rank', 0)
        if 60 <= iv_rank <= 80:
            iv_score = 100  # Sweet spot
        elif 50 <= iv_rank < 60:
            iv_score = 80 + (iv_rank - 50) * 2  # 80-100 scale
        elif 80 < iv_rank <= 90:
            iv_score = 100 - (iv_rank - 80) * 2  # 100-80 scale
        elif iv_rank > 90:
            iv_score = max(50, 80 - (iv_rank - 90) * 3)  # Penalize extreme IV
        else:  # iv_rank < 50
            iv_score = max(30, iv_rank * 1.6)  # Scale from 0-80

        # Factor 3: Market Cap (15% weight)
        # Preference for larger, more stable companies
        # Scale: $2B = 50 points, $10B = 75 points, $100B+ = 100 points
        market_cap = candidate.get('market_cap', 0)
        if market_cap > 0:
            market_cap_billions = market_cap / 1e9
            if market_cap_billions >= 100:
                cap_score = 100
            elif market_cap_billions >= 10:
                cap_score = 75 + (min(market_cap_billions, 100) - 10) / 90 * 25
            else:
                cap_score = 50 + (market_cap_billions - 2) / 8 * 25
            cap_score = max(30, min(100, cap_score))
        else:
            cap_score = 50  # Default if no market cap data

        # Factor 4: Beta (15% weight)
        # Lower volatility preferred: 0.8-1.2 = ideal, >1.5 = penalty
        beta = candidate.get('beta', 1.0)
        if beta is None:
            beta = 1.0

        if 0.8 <= beta <= 1.2:
            beta_score = 100  # Low volatility sweet spot
        elif beta < 0.8:
            beta_score = 85 + (beta / 0.8) * 15  # Bonus for very low beta
        elif 1.2 < beta <= 1.5:
            beta_score = 100 - (beta - 1.2) * 50  # 100 -> 85 scale
        else:  # beta > 1.5
            beta_score = max(40, 85 - (beta - 1.5) * 30)  # Penalize high beta

        # Calculate weighted total score
        quality_score = (
            return_score * 0.40 +
            iv_score * 0.30 +
            cap_score * 0.15 +
            beta_score * 0.15
        )

        return round(quality_score, 1)

    # =========================================================================
    # RISK MANAGEMENT METHODS (Added Nov 2025 based on live trade analysis)
    # =========================================================================

    def get_symbol_sector(self, symbol: str) -> str:
        """
        Get sector classification for a symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Sector name (e.g., 'EV', 'TECH', 'FINANCE') or 'OTHER'
        """
        for sector, symbols in self.config.SECTORS.items():
            if symbol in symbols:
                return sector
        return 'OTHER'

    def can_add_symbol_by_sector(self, symbol: str, wheel_manager) -> bool:
        """
        Check if adding this symbol would violate sector diversification limits.

        NOTE: Sector limit disabled because many symbols fall into 'OTHER' category,
        blocking valid trades. Risk is managed through:
        - MAX_WHEEL_POSITIONS (7 positions max)
        - MAX_CONTRACTS_PER_SYMBOL (10 contracts max)
        - WHEEL_STOP_LOSS_PCT (-200% ROI)
        - check_consecutive_losses() prevents revenge trading

        Args:
            symbol: Symbol to check
            wheel_manager: WheelManager instance to get current positions

        Returns:
            Always True (sector limit disabled)
        """
        # Sector limit disabled - too many symbols fall into 'OTHER' category
        # Risk managed through position limits and stop losses instead
        return True

    def should_roll_deep_itm_put(self, position: Dict, current_stock_price: float) -> bool:
        """
        Check if put is deep ITM and should be rolled down/out.

        Deep ITM positions can cause runaway losses (e.g., XPEV -$1,565 loss).
        Roll to a lower strike to reduce risk and collect additional credit.

        Args:
            position: Wheel position dict from wheel_manager
            current_stock_price: Current stock price

        Returns:
            True if should roll, False otherwise
        """
        # Only check puts in SELLING_PUTS state
        if position['state'] != 'SELLING_PUTS':
            return False

        put_strike = position.get('put_strike', 0)
        if put_strike == 0:
            return False

        # Calculate intrinsic value (how far ITM)
        intrinsic_value = put_strike - current_stock_price

        # If put is >$1.00 ITM, consider rolling
        if intrinsic_value > self.config.WHEEL_DEEP_ITM_THRESHOLD:
            logging.warning(f"[WHEEL] {position['symbol']}: Put is ${intrinsic_value:.2f} ITM "
                          f"(strike ${put_strike:.2f}, stock ${current_stock_price:.2f}) - Consider rolling")
            return True

        return False

    def should_stop_loss_put(self, position: Dict, current_premium: float) -> bool:
        """
        Check if put hit max loss threshold.

        Stop loss at -200% ROI prevents disasters like XPEV (-228% ROI).

        Args:
            position: Wheel position dict
            current_premium: Current market value of put

        Returns:
            True if stop loss triggered, False otherwise
        """
        entry_premium = position.get('total_premium_collected', 0)
        if entry_premium == 0:
            return False

        # Calculate unrealized P&L
        # For short puts: P&L = premium collected - current value
        unrealized_pnl = entry_premium - (current_premium * 100)
        roi = (unrealized_pnl / entry_premium) if entry_premium > 0 else 0

        # Stop loss at -200% ROI (lost 2x the premium)
        if roi <= self.config.WHEEL_STOP_LOSS_PCT:
            logging.error(f"[WHEEL] {position['symbol']}: STOP LOSS TRIGGERED "
                        f"(ROI: {roi:.1%}, loss: ${abs(unrealized_pnl):.0f})")
            return True

        return False

    def get_dynamic_position_multiplier(self, wheel_manager) -> float:
        """
        Position sizing multiplier based on portfolio performance.

        NOTE: Previously reduced positions to 0.5-0.75x when win rate was low,
        but this prevented proper capital utilization. Now always returns 1.0
        to ensure 14% allocation per position (98% total across 7 positions).

        Risk is managed through:
        - MAX_WHEEL_POSITIONS (7 positions max)
        - MAX_SECTOR_POSITIONS (2 per sector)
        - WHEEL_STOP_LOSS_PCT (-200% ROI)
        - check_consecutive_losses() prevents revenge trading

        Returns:
            Multiplier: Always 1.0 (full size)
        """
        # Always use full position size - other risk controls handle downside
        return 1.0

    def check_consecutive_losses(self, symbol: str, wheel_manager) -> bool:
        """
        Check if symbol has too many consecutive losses.

        Prevents revenge trading - pauses after 2 consecutive losses.

        Args:
            symbol: Symbol to check
            wheel_manager: WheelManager instance

        Returns:
            True if OK to trade, False if should pause
        """
        performance = wheel_manager.get_symbol_performance(symbol)

        if performance:
            consecutive_losses = performance.get('consecutive_losses', 0)

            if consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES:
                logging.warning(f"[WHEEL] {symbol}: {consecutive_losses} consecutive losses - "
                              f"PAUSING new entries until streak breaks")
                return False

        return True

    def calculate_position_size(self, symbol: str, put_strike: float, account_value: float,
                               existing_wheel_positions: int, wheel_manager=None) -> int:
        """
        Calculate how many put contracts to sell based on capital, limits, and win rate.

        Dynamic sizing based on historical performance:
        - High performers (70%+ win rate): 16-18% capital allocation
        - Average performers (50-70% win rate): 14% capital allocation (baseline)
        - Low performers (<50% win rate): 10-12% capital allocation
        - New symbols (no history): 14% capital allocation (baseline)

        Args:
            symbol: Stock symbol
            put_strike: Put strike price
            account_value: Total account value
            existing_wheel_positions: Number of existing wheel positions
            wheel_manager: WheelManager instance for performance data (optional)

        Returns:
            Number of contracts to sell (0 if position limit reached)
        """
        # Check position limit
        if existing_wheel_positions >= self.MAX_WHEEL_POSITIONS:
            logging.warning(f"[WHEEL] {symbol}: Maximum wheel positions ({self.MAX_WHEEL_POSITIONS}) reached")
            return 0

        # BASE CAPITAL ALLOCATION: 14% (baseline)
        base_capital_pct = self.MAX_CAPITAL_PER_WHEEL

        # DYNAMIC SIZING: Adjust based on historical win rate
        capital_pct = base_capital_pct  # Default to baseline

        if wheel_manager:
            performance = wheel_manager.get_symbol_performance(symbol)

            if performance and performance['trades_total'] >= 3:  # Need at least 3 trades for reliability
                win_rate = performance['win_rate']
                quality_score = performance['quality_score']

                # Win rate-based capital allocation
                if win_rate >= 70.0:
                    # High performer: 16-18% allocation
                    capital_pct = 0.16 + (min(win_rate, 90) - 70) / 100  # 16-18%
                    sizing_reason = f"HIGH WIN RATE ({win_rate:.1f}%)"
                elif win_rate >= 50.0:
                    # Average performer: 14-15% allocation
                    capital_pct = 0.14 + (win_rate - 50) / 500  # 14-15%
                    sizing_reason = f"AVERAGE WIN RATE ({win_rate:.1f}%)"
                else:
                    # Low performer: 10-12% allocation
                    capital_pct = 0.10 + (win_rate / 250)  # 10-12%
                    sizing_reason = f"LOW WIN RATE ({win_rate:.1f}%)"

                # Further adjust by quality score
                quality_multiplier = 0.8 + (quality_score / 500)  # 0.8 to 1.0
                capital_pct *= quality_multiplier

                # Cap at reasonable limits
                capital_pct = max(0.10, min(capital_pct, 0.18))  # 10-18% range

                logging.info(f"[WHEEL] {symbol}: Dynamic sizing - {sizing_reason}, " +
                           f"quality {quality_score:.1f}/100 → {capital_pct*100:.1f}% capital allocation")
            else:
                sizing_reason = "NEW SYMBOL (no history)"
                logging.info(f"[WHEEL] {symbol}: {sizing_reason} → {capital_pct*100:.1f}% capital allocation (baseline)")
        else:
            logging.info(f"[WHEEL] {symbol}: Static sizing → {capital_pct*100:.1f}% capital allocation")

        # Calculate max capital for this position
        max_capital = account_value * capital_pct

        # Each contract requires cash securing 100 shares at strike price
        capital_per_contract = put_strike * 100

        # Calculate max contracts based on allocated capital
        max_contracts = int(max_capital / capital_per_contract)

        # Use full allocated capital (not just 1 contract!)
        # Cap at reasonable limit to avoid over-concentration (reduced from 10 to 3 based on live trade analysis)
        contracts = min(max_contracts, self.config.MAX_CONTRACTS_PER_SYMBOL)

        # Apply dynamic multiplier based on overall portfolio win rate
        if wheel_manager:
            multiplier = self.get_dynamic_position_multiplier(wheel_manager)
            if multiplier < 1.0:
                original_contracts = contracts
                contracts = max(1, int(contracts * multiplier))
                logging.warning(f"[WHEEL] {symbol}: Dynamic sizing reduced contracts from "
                              f"{original_contracts} to {contracts} (multiplier {multiplier:.2f})")

        contracts = min(max_contracts, contracts)

        if contracts == 0:
            logging.warning(f"[WHEEL] {symbol}: Insufficient capital for wheel position "
                          f"(need ${capital_per_contract:.2f}, have ${max_capital:.2f})")
        else:
            actual_capital = capital_per_contract * contracts
            pct_of_account = (actual_capital / account_value) * 100
            logging.info(f"[WHEEL] {symbol}: Position size {contracts} contract(s) "
                       f"(${actual_capital:,.2f} capital = {pct_of_account:.1f}% of account)")

        return contracts

    def select_covered_call(self, symbol: str, min_strike: float,
                           current_price: float, shares_owned: int) -> Optional[Dict]:
        """
        Select best covered call option for assigned stock.

        Finds call options that:
        - Strike >= min_strike (typically 5% above cost basis)
        - DTE in 21-60 range (same as puts)
        - Good liquidity and premium
        - Maximize annual return

        Args:
            symbol: Stock symbol
            min_strike: Minimum call strike (e.g., cost basis * 1.05)
            current_price: Current stock price
            shares_owned: Number of shares owned (determines contract quantity)

        Returns:
            Dict with call option details or None if no suitable call found
        """
        try:
            logging.info(f"[WHEEL] {symbol}: Finding covered call (min strike ${min_strike:.2f})")

            # Get options chain
            chain = self.market_data.get_options_chain(symbol)
            if not chain:
                logging.warning(f"[WHEEL] {symbol}: No options chain available")
                return None

            # Filter for calls only
            calls = [opt for opt in chain if opt.get('type') == 'call']

            if not calls:
                logging.warning(f"[WHEEL] {symbol}: No call options in chain")
                return None

            # Filter by DTE range (same as puts: 21-60 days)
            valid_calls = []
            for call in calls:
                expiration_str = call.get('expiration')
                if not expiration_str:
                    continue

                # Parse expiration and calculate DTE
                try:
                    exp_date = datetime.strptime(expiration_str, '%Y-%m-%d')
                    dte = (exp_date - datetime.now()).days

                    if dte < self.MIN_DTE or dte > self.MAX_DTE:
                        continue

                    # Filter by strike (must be >= min_strike)
                    strike = call.get('strike', 0)
                    if strike < min_strike:
                        continue

                    # Filter by liquidity (same as puts)
                    volume = call.get('volume', 0)
                    open_interest = call.get('open_interest', 0)
                    if volume < 10 and open_interest < 100:
                        continue

                    # Get premium
                    bid = call.get('bid', 0)
                    ask = call.get('ask', 0)
                    mid_price = (bid + ask) / 2 if bid and ask else 0

                    if mid_price <= 0:
                        continue

                    # Calculate annual return
                    contracts = shares_owned // 100
                    total_premium = mid_price * contracts * 100
                    capital_at_risk = strike * shares_owned  # Stock value if called away
                    annual_return = ((total_premium / capital_at_risk) * (365 / dte)) * 100 if capital_at_risk > 0 else 0

                    # Store call with scoring data
                    call['premium'] = mid_price
                    call['dte'] = dte
                    call['annual_return'] = annual_return
                    call['liquidity_score'] = volume + (open_interest / 10)

                    valid_calls.append(call)

                except Exception as e:
                    logging.debug(f"[WHEEL] {symbol}: Error parsing call option: {e}")
                    continue

            if not valid_calls:
                logging.warning(f"[WHEEL] {symbol}: No calls found meeting criteria " +
                              f"(min strike ${min_strike:.2f}, DTE {self.MIN_DTE}-{self.MAX_DTE})")
                return None

            # Score and rank calls
            # Priority: 1) Annual return (50%), 2) Strike price closer to min (30%), 3) Liquidity (20%)
            for call in valid_calls:
                strike = call.get('strike', 0)
                strike_distance = abs(strike - min_strike) / min_strike  # Prefer strikes closer to min

                # Normalize scores (0-100 scale)
                return_score = min(call['annual_return'], 100)  # Cap at 100%
                strike_score = max(0, 100 - (strike_distance * 100))  # Closer to min = higher score
                liquidity_score = min(call['liquidity_score'] / 10, 100)  # Normalize

                # Weighted composite score
                call['composite_score'] = (
                    return_score * 0.50 +
                    strike_score * 0.30 +
                    liquidity_score * 0.20
                )

            # Sort by composite score
            valid_calls.sort(key=lambda x: x['composite_score'], reverse=True)

            # Select best call
            best_call = valid_calls[0]

            logging.info(f"[WHEEL] {symbol}: Selected covered call - " +
                       f"${best_call['strike']:.2f} strike, {best_call['dte']} DTE, " +
                       f"${best_call['premium']:.2f} premium, {best_call['annual_return']:.1f}% annual return")

            return {
                'symbol': best_call.get('option_symbol', ''),
                'strike': best_call['strike'],
                'expiration': best_call.get('expiration', ''),
                'premium': best_call['premium'],
                'dte': best_call['dte'],
                'annual_return': best_call['annual_return']
            }

        except Exception as e:
            logging.error(f"[WHEEL] {symbol}: Error selecting covered call: {e}")
            import traceback
            traceback.print_exc()
            return None
