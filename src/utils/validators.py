"""
Option contract and strategy validation functions
"""

from typing import Dict, Tuple, List
from config import config


def validate_contract_liquidity(contract: Dict, paper_mode: bool = None) -> Tuple[bool, str]:
    """
    Validate option contract has sufficient liquidity.

    Args:
        contract: Contract data dict with bid, ask, volume, oi, price
        paper_mode: Override paper mode detection (uses config if None)

    Returns:
        (is_valid, reason_message)
    """
    if paper_mode is None:
        paper_mode = config.is_paper_mode()

    bid = contract.get('bid', 0) or 0
    ask = contract.get('ask', 0) or 0
    volume = contract.get('volume', 0) or 0
    oi = contract.get('open_interest', 0) or 0
    last_price = contract.get('last_price', 0) or 0

    # PAPER TRADING MODE: Relaxed validation
    if paper_mode:
        if last_price > 0:
            return True, "OK (paper mode)"
        if bid > 0 or ask > 0:
            return True, "OK (paper mode - using bid/ask)"
        return False, "No valid price data"

    # LIVE TRADING MODE: Strict validation
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else 0.0

        if spread_pct > 0.15:
            return False, f"Bid-ask spread too wide: {spread_pct:.1%}"

    if volume < config.LIQUIDITY_THRESHOLDS['min_volume']:
        return False, f"Insufficient volume: {volume} (min: {config.LIQUIDITY_THRESHOLDS['min_volume']})"

    if oi < config.LIQUIDITY_THRESHOLDS['min_open_interest']:
        return False, f"Insufficient open interest: {oi} (min: {config.LIQUIDITY_THRESHOLDS['min_open_interest']})"

    if last_price < config.PRICE_FILTERS['min_price']:
        return False, "Contract price too low"

    return True, "OK"


def get_contract_price(contract: Dict) -> Tuple[float, str]:
    """Get best available price from contract data"""
    bid = contract.get('bid', 0) or 0
    ask = contract.get('ask', 0) or 0
    last_price = contract.get('last_price', 0) or 0

    # Prefer mid-point if bid/ask available
    if bid > 0 and ask > 0:
        return (bid + ask) / 2, 'mid'

    # Fall back to last price
    if last_price > 0:
        return last_price, 'last'

    # Last resort: ask price
    if ask > 0:
        return ask, 'ask'

    return 0, 'none'


def calculate_dynamic_limit_price(bid: float, ask: float, side: str, contract_price: float = None) -> float:
    """
    Calculate competitive limit price that's likely to fill while minimizing slippage.

    Strategy:
    - BUY orders: Set limit at ask price (or slightly above for wide spreads)
    - SELL orders: Set limit at bid price (or slightly below for wide spreads)
    """
    if bid <= 0 and ask <= 0:
        if contract_price and contract_price > 0:
            return round(contract_price * 1.02 if side.lower() == 'buy' else contract_price * 0.98, 2)
        return config.PRICE_FILTERS['min_price']

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        spread_width = ask - bid
        spread_pct = (spread_width / mid) if mid > 0 else 0.0
    elif ask > 0:
        mid = ask
        spread_pct = 0.05
        spread_width = mid * spread_pct
    else:
        mid = bid
        spread_pct = 0.05
        spread_width = mid * spread_pct

    if side.lower() == 'buy':
        if ask > 0:
            if spread_pct > 0.10:
                limit_price = ask + (spread_width * 0.10)
            else:
                limit_price = ask
        else:
            limit_price = bid + (spread_width * 1.05)
    else:
        if bid > 0:
            if spread_pct > 0.10:
                limit_price = bid - (spread_width * 0.10)
            else:
                limit_price = bid
        else:
            limit_price = ask - (spread_width * 1.05)

    # Safety check: Don't exceed 3% from mid price
    max_deviation = mid * 0.03
    if side.lower() == 'buy':
        limit_price = min(limit_price, mid + max_deviation)
    else:
        limit_price = max(limit_price, mid - max_deviation)

    return max(config.PRICE_FILTERS['min_price'], round(limit_price, 2))


def validate_grok_response(symbol: str, strategy: str, confidence: int, strikes: str) -> Tuple[bool, str]:
    """
    Validate Grok AI response before using.

    Args:
        symbol: Stock symbol
        strategy: Strategy name
        confidence: Confidence percentage
        strikes: Strike prices string

    Returns:
        (is_valid, error_message)
    """
    # Validate symbol format
    if not symbol or not isinstance(symbol, str):
        return False, "Symbol cannot be empty"

    import re
    if not re.match(r'^[A-Z]{1,5}$', symbol.strip().upper()):
        return False, f"Invalid symbol format: {symbol}"

    # Validate strategy
    if strategy not in config.STOP_LOSSES:
        return False, f"Invalid strategy: {strategy}"

    # Validate confidence
    if not isinstance(confidence, (int, float)):
        return False, "Confidence must be numeric"

    if not 0 <= confidence <= 100:
        return False, f"Confidence out of bounds: {confidence}"

    # Validate strikes
    if not strikes or not isinstance(strikes, str):
        return False, "Strikes cannot be empty"

    # Check strikes contain only valid characters
    if not re.match(r'^[\d\./\s-]+$', strikes.strip()):
        return False, f"Invalid strikes format: {strikes}"

    return True, "Valid"


def sanitize_for_prompt(text: str, max_length: int = 100) -> str:
    """Sanitize input text for safe use in AI prompts"""
    if text is None:
        return ""

    safe_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-_/(),%')
    sanitized = ''.join(c for c in str(text).upper() if c in safe_chars)
    return sanitized[:max_length]


def validate_symbol(symbol: str) -> bool:
    """Validate stock ticker format"""
    if not symbol or not isinstance(symbol, str):
        return False

    import re
    pattern = r'^[A-Z]{1,5}$'
    return bool(re.match(pattern, symbol.strip().upper()))
