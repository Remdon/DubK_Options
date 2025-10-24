"""
Portfolio Manager - Portfolio-Level Risk Management

Manages overall portfolio risk:
- Position concentration limits
- Portfolio Greeks tracking (delta, theta, gamma, vega)
- Exposure validation
- Risk limit enforcement

NOTE: Sector-based logic has been removed in favor of:
- Per-symbol exposure limits (MAX_SYMBOL_EXPOSURE)
- Total position count limits (MAX_TOTAL_POSITIONS)
- Portfolio Greeks limits
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from colorama import Fore, Style


# Helper function for extracting underlying symbol from OCC format
def extract_underlying_symbol(full_symbol: str) -> str:
    """Extract underlying stock symbol from OCC format or return as-is for stocks"""
    if len(full_symbol) > 15:  # OCC options format: TICKER + YYMMDD + C/P + STRIKE (15 chars)
        return full_symbol[:-15]
    return full_symbol


class PortfolioManager:
    """Enhanced portfolio manager with Greeks tracking"""

    def __init__(self, trading_client, max_positions=10):
        self.trading_client = trading_client
        self.max_positions = max_positions

        # Risk limits
        self.MAX_POSITION_PCT = 0.15
        self.MAX_SYMBOL_EXPOSURE = 0.25
        self.MAX_SECTOR_EXPOSURE = 0.40

        # Greeks limits
        self.MAX_PORTFOLIO_DELTA = 100  # Max net delta exposure
        self.MAX_PORTFOLIO_THETA = -500  # Max daily theta decay ($)

        # Sector mappings
        self.sectors = {
            'SPY': 'BROAD_MARKET', 'QQQ': 'TECH', 'IWM': 'SMALL_CAP',
            'AAPL': 'TECH', 'MSFT': 'TECH', 'GOOGL': 'TECH', 'NVDA': 'TECH',
            'JPM': 'FINANCE', 'BAC': 'FINANCE', 'GS': 'FINANCE',
            'XOM': 'ENERGY', 'CVX': 'ENERGY',
            'JNJ': 'HEALTHCARE', 'UNH': 'HEALTHCARE',
            'META': 'TECH', 'AMZN': 'TECH', 'TSLA': 'AUTO', 'AVGO': 'TECH',
            'AMD': 'TECH', 'INTC': 'TECH', 'QCOM': 'TECH',
            'WMT': 'RETAIL', 'HD': 'RETAIL', 'MCD': 'CONSUMER', 'NKE': 'CONSUMER',
            'COP': 'ENERGY', 'BA': 'INDUSTRIAL', 'CAT': 'INDUSTRIAL'
        }

    def get_current_exposure(self) -> Dict:
        """Calculate current portfolio exposure including Greeks"""
        try:
            positions = self.trading_client.get_all_positions()
            account = self.trading_client.get_account()
            total_equity = float(account.equity) if account.equity is not None else 1.0  # Avoid division by zero

            exposure = {
                'by_symbol': {},
                'total_positions': len(positions),
                'total_allocated': 0,
                'portfolio_greeks': {
                    'delta': 0,
                    'gamma': 0,
                    'theta': 0,
                    'vega': 0
                }
            }

            processed_positions = 0
            skipped_positions = 0

            for position in positions:
                symbol = position.symbol

                # Handle None market_value - use cost basis estimate as fallback
                market_value = None
                if position.market_value is not None:
                    market_value = abs(float(position.market_value))
                else:
                    # Fallback to cost basis estimate (avg_entry_price * qty * 100 for options)
                    if hasattr(position, 'avg_entry_price') and position.avg_entry_price is not None:
                        entry_price = float(position.avg_entry_price)
                        qty = abs(float(position.qty)) if position.qty is not None else 0
                        market_value = entry_price * qty * 100 if len(symbol) > 6 else entry_price * qty  # Options multiplier
                        logging.debug(f"Using cost basis estimate for {symbol}: ${market_value:.0f}")
                    else:
                        logging.warning(f"Position {symbol} missing both market_value and avg_entry_price, skipping exposure calc")
                        skipped_positions += 1
                        continue

                if market_value <= 0:
                    # Don't spam warnings for worthless/expired positions (expected)
                    logging.debug(f"Position {symbol} has zero market_value, skipping exposure calc (likely worthless/expired)")
                    skipped_positions += 1
                    continue

                pct_of_portfolio = market_value / total_equity
                processed_positions += 1

                # By symbol - use underlying symbol for exposure tracking
                underlying = extract_underlying_symbol(symbol)
                if underlying not in exposure['by_symbol']:
                    exposure['by_symbol'][underlying] = 0  # Track by underlying, not OCC symbol
                exposure['by_symbol'][underlying] += pct_of_portfolio

                exposure['total_allocated'] += pct_of_portfolio

                # TODO: Aggregate Greeks from positions (requires fetching current Greeks)
                # This would require calling options API for each position
                # For now, we'll skip to avoid API overhead

            exposure['processed_positions'] = processed_positions
            exposure['skipped_positions'] = skipped_positions

            logging.debug(f"Exposure calc: {processed_positions} positions processed, {skipped_positions} skipped, {exposure['total_allocated']:.1%} allocated")

            return exposure
        except Exception as e:
            logging.error(f"Error getting exposure: {e}")
            return {
                'by_symbol': {},
                'total_positions': 0,
                'total_allocated': 0,
                'portfolio_greeks': {'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0}
            }

    def can_enter_position(self, symbol: str, position_size_pct: float, exposure: Dict = None) -> Tuple[bool, str]:
        """Check if new position is allowed under risk limits"""
        if exposure is None:
            exposure = self.get_current_exposure()

        # Check position count limit
        if exposure['total_positions'] >= self.max_positions:
            return False, f"Max positions reached ({self.max_positions})"

        # Check per-position limit
        if position_size_pct > self.MAX_POSITION_PCT:
            return False, f"Position size {position_size_pct:.1%} exceeds limit {self.MAX_POSITION_PCT:.1%}"

        # Check symbol exposure
        current_symbol_exposure = exposure['by_symbol'].get(symbol, 0)
        if current_symbol_exposure + position_size_pct > self.MAX_SYMBOL_EXPOSURE:
            return False, f"Would exceed symbol exposure limit ({self.MAX_SYMBOL_EXPOSURE:.1%})"

        return True, "Position allowed"

    def calculate_optimal_position_size(self, confidence: int, exposure: Dict = None, base_size_pct=0.05) -> float:
        """Calculate position size based on confidence, incorporating Greeks and volatility context"""

        if exposure is None:
            exposure = self.get_current_exposure()

        # Base multipliers enhanced by portfolio context
        # FIXED: Reduced from 15% max to 8% max for better risk management
        if confidence >= 95:
            multiplier = 1.6  # HIGH CONFIDENCE - 8% (was 3.0 = 15%)
        elif confidence >= 90:
            multiplier = 1.3  # 6.5% (was 2.0 = 10%)
        elif confidence >= 80:
            multiplier = 1.1  # 5.5% (was 1.5 = 7.5%)
        else:
            multiplier = 1.0  # 5.0% (unchanged)

        position_size = base_size_pct * multiplier

        # FIXED: Absolute maximum cap at 8% per position (options can lose 100%)
        MAX_ABSOLUTE_POSITION_SIZE = 0.08
        position_size = min(position_size, MAX_ABSOLUTE_POSITION_SIZE)

        # Reduce if heavily allocated
        if exposure['total_allocated'] > 0.80:
            position_size *= 0.5
        elif exposure['total_allocated'] > 0.60:
            position_size *= 0.75

        # Consider portfolio Greeks exposure (additional reduction if already heavy exposure)
        portfolio_greeks = exposure.get('portfolio_greeks', {})
        net_delta = abs(portfolio_greeks.get('delta', 0))

        if net_delta > self.MAX_PORTFOLIO_DELTA / 2:  # Over 50% of max delta exposure
            position_size *= 0.7  # Reduce by 30%
            logging.debug(f"Reducing position size due to high portfolio delta: {net_delta}")

        # Cap at maximum
        position_size = min(position_size, self.MAX_POSITION_PCT)

        return position_size


