"""
Market Regime Analyzer - Identifies Current Market Conditions

Analyzes market regime using:
- S&P 500 price action
- VIX volatility levels
- Classification into 6 distinct regimes
- Trading implications for each regime
"""

import logging
import time
from typing import Dict, Optional


class MarketRegimeAnalyzer:
    """Analyzes current market regime for smarter trading decisions"""

    def __init__(self, openbb_client):
        self.openbb = openbb_client
        self.regime_cache = {}
        self.cache_expiry = 1800  # 30 minutes

    def analyze_market_regime(self) -> Dict:
        """Determine current market regime using SPX, VIX, and options data"""
        cache_key = "market_regime"

        # Check cache
        if cache_key in self.regime_cache:
            cached_time, cached_data = self.regime_cache[cache_key]
            if time.time() - cached_time < self.cache_expiry:
                return cached_data

        try:
            # Get SPX and VIX data
            spx_data = self.openbb.get_quote("^GSPC")  # S&P 500
            vix_data = self.openbb.get_quote("^VIX")   # Volatility index

            if not spx_data or not vix_data:
                return self._default_regime()

            spx_quote = spx_data.get('results', [{}])[0] if spx_data.get('results') else {}
            vix_quote = vix_data.get('results', [{}])[0] if vix_data.get('results') else {}

            spx_price = spx_quote.get('price', 0)
            spx_change = spx_quote.get('percent_change', 0) * 100
            vix_level = vix_quote.get('price', 20)  # Default to 20 if unavailable

            # Regime classification
            regime = self._classify_regime(spx_change, vix_level)

            result = {
                'regime': regime,
                'spx_change': spx_change,
                'vix_level': vix_level,
                'description': self._regime_description(regime),
                'implications': self._regime_implications(regime)
            }

            # Cache result
            self.regime_cache[cache_key] = (time.time(), result)
            return result

        except Exception as e:
            logging.debug(f"Error analyzing market regime: {e}")
            return self._default_regime()

    def _classify_regime(self, spx_change: float, vix_level: float) -> str:
        """Classify market regime based on SPX and VIX"""
        # Define regime thresholds
        BULL_THRESHOLD = 0.5  # +0.5% day = bullish
        BEAR_THRESHOLD = -1.0 # -1.0% day = bearish
        HIGH_VOL_THRESHOLD = 25  # VIX > 25 = high vol
        LOW_VOL_THRESHOLD = 15   # VIX < 15 = low vol

        if spx_change >= BULL_THRESHOLD and vix_level <= LOW_VOL_THRESHOLD:
            return "BULL_RAMPAGE"
        elif spx_change >= BULL_THRESHOLD and vix_level >= HIGH_VOL_THRESHOLD:
            return "RISKY_RALLY"
        elif spx_change <= BEAR_THRESHOLD and vix_level >= HIGH_VOL_THRESHOLD:
            return "BEAR_TRAP"
        elif spx_change <= BEAR_THRESHOLD and vix_level <= LOW_VOL_THRESHOLD:
            return "CALM_DECLINE"
        elif spx_change > -0.5 and spx_change < 0.5 and vix_level >= HIGH_VOL_THRESHOLD:
            return "VOLATILITY_SPIKE"
        else:
            return "NEUTRAL_CONSOLIDATION"

    def _regime_description(self, regime: str) -> str:
        """Human-readable regime description"""
        descriptions = {
            "BULL_RAMPAGE": "Strong upward momentum with low volatility - ideal for directional bullish trades",
            "RISKY_RALLY": "Upward move despite high volatility - cautious optimism, watch for reversals",
            "BEAR_TRAP": "Sharp decline with extreme volatility - probably oversold, potential bounce",
            "CALM_DECLINE": "Steady downward drift in normal conditions - structural downtrend",
            "VOLATILITY_SPIKE": "High volatility without clear direction - perfect for volatility plays",
            "NEUTRAL_CONSOLIDATION": "Sideways action in normal ranges - wait for more clarity"
        }
        return descriptions.get(regime, "Market regime unclear - exercise caution")

    def _regime_implications(self, regime: str) -> Dict:
        """Trading implications for each regime"""
        implications = {
            "BULL_RAMPAGE": {
                "bias": "bullish", "vol_play": "sell_volatility",
                "directional": "favors_calls", "risk": "moderate"
            },
            "RISKY_RALLY": {
                "bias": "bullish", "vol_play": "sell_calls",
                "directional": "favors_puts_for_hedge", "risk": "high"
            },
            "BEAR_TRAP": {
                "bias": "neutral", "vol_play": "buy_puts",
                "directional": "bounce_trades", "risk": "very_high"
            },
            "CALM_DECLINE": {
                "bias": "bearish", "vol_play": "buy_puts",
                "directional": "favors_puts", "risk": "moderate"
            },
            "VOLATILITY_SPIKE": {
                "bias": "neutral", "vol_play": "buy_straddles",
                "directional": "avoid", "risk": "very_high"
            },
            "NEUTRAL_CONSOLIDATION": {
                "bias": "neutral", "vol_play": "sell_short_term",
                "directional": "small_positions", "risk": "low"
            }
        }
        return implications.get(regime, {"bias": "neutral", "vol_play": "neutral", "directional": "caution", "risk": "unknown"})

    def _default_regime(self) -> Dict:
        """Default regime when analysis fails"""
        return {
            'regime': 'UNKNOWN',
            'spx_change': 0,
            'vix_level': 20,
            'description': 'Market regime analysis unavailable',
            'implications': {
                'bias': 'neutral', 'vol_play': 'neutral',
                'directional': 'caution', 'risk': 'unknown'
            }
        }
