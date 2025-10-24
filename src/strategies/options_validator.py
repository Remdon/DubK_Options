"""
Options Validator - Contract Liquidity and Pricing Validation

Validates options contracts for:
- Bid-ask spread quality (<15% in live mode)
- Volume and open interest minimums
- Price validity
- Paper vs live trading mode differences
"""

import logging
import os
from typing import Dict, Tuple, Optional


class OptionsValidator:
    """Validates option contracts for liquidity and pricing"""

    @staticmethod
    def validate_contract_liquidity(contract: Dict, paper_mode: bool = False) -> Tuple[bool, str]:
        """Validate option contract has sufficient liquidity"""
        bid = contract.get('bid', 0) or 0
        ask = contract.get('ask', 0) or 0
        volume = contract.get('volume', 0) or 0
        oi = contract.get('open_interest', 0) or 0
        last_price = contract.get('last_price', 0) or 0

        # Determine if we're in paper mode (case-insensitive check)
        ALPACA_MODE = os.getenv('ALPACA_MODE', 'paper')
        is_paper_mode = paper_mode or (ALPACA_MODE and ALPACA_MODE.lower().strip() == 'paper')

        # Debug: Log mode detection (only once)
        if not hasattr(OptionsValidator, '_mode_logged'):
            logging.info(f"Validation mode: paper_mode={paper_mode}, ALPACA_MODE='{ALPACA_MODE}', is_paper_mode={is_paper_mode}")
            OptionsValidator._mode_logged = True

        # PAPER TRADING MODE: Relaxed validation
        if is_paper_mode:
            # In paper trading, bid/ask/volume/OI might be missing or zero
            # Focus on just having a valid price
            if last_price > 0:
                return True, "OK (paper mode)"

            # If no last price, check if we have bid/ask
            if bid > 0 or ask > 0:
                return True, "OK (paper mode - using bid/ask)"

            return False, "No valid price data"

        # LIVE TRADING MODE: Strict validation
        # FIXED: Issue #9 - Reduce bid-ask spread threshold to 15%
        # Check bid-ask spread
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            if mid > 0:
                spread_pct = (ask - bid) / mid

                # Professional threshold: <5% excellent, 5-10% acceptable, 10-15% caution, >15% avoid
                if spread_pct > 0.15:  # 15% max spread (was 25%)
                    return False, f"Bid-ask spread too wide: {spread_pct:.1%}"
        else:
            return False, "No valid bid/ask prices"

        # FIXED: Issue #11 - Increase volume/OI minimums for better liquidity
        # Check volume (was 10, now 50)
        if volume < 50:
            return False, f"Insufficient volume: {volume} (min: 50)"

        # Check open interest (was 100, now 500)
        if oi < 500:
            return False, f"Insufficient open interest: {oi} (min: 500)"

        # Check minimum price (avoid penny options)
        if last_price < 0.05:
            return False, f"Contract price too low: ${last_price:.2f}"

        return True, "OK"

    @staticmethod
    def get_contract_price(contract: Dict) -> Tuple[Optional[float], str]:
        """Get best price for contract with validation"""
        bid = contract.get('bid', 0) or 0
        ask = contract.get('ask', 0) or 0
        last_price = contract.get('last_price', 0) or 0

        # Prefer mid-point of bid/ask if available
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            return mid, 'mid'

        # Fall back to last price if recent
        if last_price > 0:
            return last_price, 'last'

        # Use ask as last resort
        if ask > 0:
            return ask, 'ask'

        return None, 'none'
