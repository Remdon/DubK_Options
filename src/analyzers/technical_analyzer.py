"""
Technical Analyzer - Advanced Technical Analysis for Entry/Exit Timing

Provides:
- RSI (Relative Strength Index) calculation
- Support/Resistance levels
- Trend analysis with moving averages
- Volatility metrics
"""

import logging
import time
import statistics
from typing import Dict, List, Optional


class TechnicalAnalyzer:
    """Advanced technical analysis for entry/exit timing"""

    def __init__(self, openbb_client):
        self.openbb = openbb_client
        self.technical_cache = {}
        self.cache_expiry = 900  # 15 minutes

    def analyze_technicals(self, symbol: str) -> Dict:
        """Comprehensive technical analysis"""
        cache_key = f"technical_{symbol}"

        if cache_key in self.technical_cache:
            cached_time, cached_data = self.technical_cache[cache_key]
            if time.time() - cached_time < self.cache_expiry:
                return cached_data

        try:
            # Get price data for technical analysis
            price_data = self.openbb.get_historical_price(symbol, days=30)

            if not price_data or 'results' not in price_data:
                return self._default_technicals()

            results = price_data['results']

            # Calculate technical indicators
            closes = [r.get('close', 0) for r in results if r.get('close')]
            highs = [r.get('high', 0) for r in results if r.get('high')]
            lows = [r.get('low', 0) for r in results if r.get('low')]

            if len(closes) < 10:
                return self._default_technicals()

            # RSI calculation
            rsi = self._calculate_rsi(closes)

            # Support/resistance levels
            support_resistance = self._calculate_support_resistance(highs, lows, closes[-1])

            # Trend analysis
            trend = self._calculate_trend(closes)

            # Volatility analysis
            volatility = self._calculate_volatility(closes)

            result = {
                'rsi': rsi,
                'rsi_status': 'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral',
                'support_resistance': support_resistance,
                'trend': trend,
                'volatility': volatility
            }

            # Cache result
            self.technical_cache[cache_key] = (time.time(), result)
            return result

        except Exception as e:
            logging.debug(f"Error in technical analysis for {symbol}: {e}")
            return self._default_technicals()

    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Calculate RSI indicator"""
        if len(closes) < period + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_support_resistance(self, highs: List[float], lows: List[float], current_price: float) -> Dict:
        """Calculate support and resistance levels"""
        if len(highs) < 10 or len(lows) < 10:
            return {'support': current_price * 0.95, 'resistance': current_price * 1.05}

        # Use recent highs/lows for support/resistance
        recent_highs = highs[-20:]
        recent_lows = lows[-20:]

        resistance = max(recent_highs)
        support = min(recent_lows)

        return {
            'support': support,
            'resistance': resistance,
            'near_support': abs(current_price - support) / current_price < 0.02,
            'near_resistance': abs(current_price - resistance) / current_price < 0.02
        }

    def _calculate_trend(self, closes: List[float]) -> Dict:
        """Calculate trend strength and direction"""
        if len(closes) < 20:
            return {'direction': 'sideways', 'strength': 0}

        # Simple trend calculation using moving averages
        short_ma = sum(closes[-10:]) / 10
        long_ma = sum(closes[-20:]) / 20

        trend_strength = abs(short_ma - long_ma) / long_ma
        direction = 'up' if short_ma > long_ma else 'down' if short_ma < long_ma else 'sideways'

        return {
            'direction': direction,
            'strength': trend_strength,
            'short_ma': short_ma,
            'long_ma': long_ma
        }

    def _calculate_volatility(self, closes: List[float]) -> Dict:
        """Calculate price volatility"""
        if len(closes) < 10:
            return {'atr': 0, 'daily_range_pct': 0}

        # Calculate daily returns
        returns = []
        for i in range(1, len(closes)):
            returns.append((closes[i] - closes[i-1]) / closes[i-1])

        if returns:
            volatility = statistics.stdev(returns) * (252 ** 0.5)  # Annualized
            avg_range_pct = sum(abs(r) for r in returns[-10:]) / 10
        else:
            volatility = 0
            avg_range_pct = 0

        return {
            'annual_volatility': volatility,
            'avg_daily_range_pct': avg_range_pct
        }

    def _default_technicals(self) -> Dict:
        """Default technical analysis when data unavailable"""
        return {
            'rsi': 50,
            'rsi_status': 'neutral',
            'support_resistance': {'support': 0, 'resistance': 0, 'near_support': False, 'near_resistance': False},
            'trend': {'direction': 'sideways', 'strength': 0},
            'volatility': {'annual_volatility': 0, 'avg_daily_range_pct': 0}
        }
