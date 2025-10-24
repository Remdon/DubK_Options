"""
IV Analyzer - Implied Volatility Rank and Percentile Analysis

Analyzes implied volatility metrics using:
- Historical realized volatility calculation
- IV rank (current IV position in 52-week range)
- IV percentile (percentage of time IV was lower)
- Buy/sell signals based on IV levels
"""

import logging
import time
import statistics
from typing import List, Dict, Optional


class IVAnalyzer:
    """Analyzes Implied Volatility rank and percentile with real historical data"""

    def __init__(self, openbb_client):
        self.openbb = openbb_client
        self.iv_cache = {}  # Cache IV metrics and historical data
        self.cache_expiry = 3600  # 1 hour
        self.price_volatility_cache = {}  # Cache realized volatility
        self.price_cache_expiry = 86400  # 24 hours

    def get_iv_history(self, symbol: str, days=252, options_chain: List[Dict] = None) -> List[float]:
        """Get historical IV data by calculating realized volatility from price history"""
        cache_key = f"{symbol}_{days}"

        # Check cache
        if cache_key in self.iv_cache:
            cached_time, cached_data = self.iv_cache[cache_key]
            if time.time() - cached_time < self.cache_expiry:
                return cached_data

        try:
            # Get historical price data from OpenBB
            price_data = self.openbb.get_historical_price(symbol, days=days)
            if not price_data or 'results' not in price_data:
                logging.debug(f"No price history available for {symbol}")
                return []

            prices = price_data['results']
            if len(prices) < 20:  # Need minimum data for volatility calculation
                logging.debug(f"Insufficient price history for {symbol}: {len(prices)} days")
                return []

            # Calculate daily returns
            returns = []
            for i in range(1, len(prices)):
                if prices[i].get('close') and prices[i-1].get('close'):
                    ret = (prices[i]['close'] - prices[i-1]['close']) / prices[i-1]['close']
                    returns.append(ret)

            if len(returns) < 10:
                logging.debug(f"Insufficient return data for {symbol}")
                return []

            # Calculate rolling realized volatility (annualized)
            realized_vols = []
            window_size = min(20, len(returns))  # 20-day rolling window

            for i in range(window_size, len(returns)):
                window_returns = returns[i-window_size:i]
                std_dev = statistics.stdev(window_returns)
                realized_vol = std_dev * (252 ** 0.5)  # Annualize (252 trading days)
                realized_vols.append(realized_vol)

            # Pad the beginning with average volatility
            avg_vol = statistics.mean(realized_vols) if realized_vols else 0.25
            padding = [avg_vol] * (len(returns) - len(realized_vols))
            full_vol_history = padding + realized_vols

            # FIXED: Issue #3 - DO NOT scale by current IV (causes circular reasoning)
            # Use realized volatility history directly without scaling
            # This provides a stable, non-circular IV rank calculation
            # Note: Realized vol (backward-looking) used as proxy for historical IV
            # In future, replace with actual historical options IV data if available

            # Cache realized volatility history (unscaled)
            self.iv_cache[cache_key] = (time.time(), full_vol_history)
            logging.debug(f"Calculated realized volatility history for {symbol}: {len(full_vol_history)} data points (unscaled)")
            return full_vol_history

        except Exception as e:
            logging.debug(f"Error getting IV history for {symbol}: {e}")
            return []

    def calculate_iv_metrics(self, symbol: str, current_iv: float, options_chain: List[Dict] = None) -> Dict:
        """Calculate IV Rank and IV Percentile"""
        if not current_iv or current_iv <= 0:
            return {
                'iv_rank': 0,
                'iv_percentile': 0,
                'signal': 'UNKNOWN',
                'description': 'No IV data'
            }

        # Use provided options chain if available to avoid duplicate API call
        iv_history = self.get_iv_history(symbol, options_chain=options_chain)

        if not iv_history or len(iv_history) < 10:
            # Not enough history - use current IV only
            return {
                'iv_rank': 50,  # Assume middle
                'iv_percentile': 50,
                'signal': 'NEUTRAL',
                'description': 'Insufficient IV history'
            }

        # Calculate IV Rank (where is current IV in 52-week range)
        iv_min = min(iv_history)
        iv_max = max(iv_history)

        if iv_max > iv_min:
            iv_rank = ((current_iv - iv_min) / (iv_max - iv_min)) * 100
            # Clamp to 0-100 range (current IV can be outside historical range)
            iv_rank = max(0, min(100, iv_rank))
        else:
            iv_rank = 50

        # Calculate IV Percentile (what % of time was IV lower)
        iv_percentile = (sum(1 for iv in iv_history if iv < current_iv) / len(iv_history)) * 100

        # Determine signal
        if iv_rank < 25:
            signal = 'BUY_OPTIONS'  # IV is cheap - buy premium
            description = 'Low IV - favorable for buying options'
        elif iv_rank > 75:
            signal = 'SELL_OPTIONS'  # IV is expensive - sell premium
            description = 'High IV - favorable for selling options'
        else:
            signal = 'NEUTRAL'
            description = 'Moderate IV'

        return {
            'iv_rank': round(iv_rank, 1),
            'iv_percentile': round(iv_percentile, 1),
            'signal': signal,
            'description': description,
            'iv_current': current_iv,
            'iv_52w_low': iv_min,
            'iv_52w_high': iv_max
        }
