"""
Flow Analyzer - Order Flow and Dealer Positioning Analysis

Analyzes:
- Put/Call ratios (volume and open interest)
- Dealer positioning and bias
- Retail vs institutional activity
- Flow-based trading signals
"""

import logging
from typing import Dict, List, Optional


class FlowAnalyzer:
    """Analyzes order flow and dealer positioning"""

    def __init__(self, openbb_client):
        self.openbb = openbb_client
        self.flow_cache = {}
        self.cache_expiry = 3600  # 1 hour

    def analyze_order_flow(self, symbol: str, options_data: List[Dict]) -> Dict:
        """Analyze put/call ratios and dealer positioning"""
        if not options_data:
            return self._default_flow()

        try:
            # Calculate put/call ratios by different timeframes
            calls = [opt for opt in options_data if opt.get('option_type') == 'call']
            puts = [opt for opt in options_data if opt.get('option_type') == 'put']

            # Volume ratios
            call_volume = sum(opt.get('volume', 0) or 0 for opt in calls)
            put_volume = sum(opt.get('volume', 0) or 0 for opt in puts)
            pc_ratio = put_volume / call_volume if call_volume > 0 else 0

            # Open interest ratios
            call_oi = sum(opt.get('open_interest', 0) or 0 for opt in calls)
            put_oi = sum(opt.get('open_interest', 0) or 0 for opt in puts)
            pc_oi_ratio = put_oi / call_oi if call_oi > 0 else 0

            # Dealer positioning indicators
            dealer_bias = self._analyze_dealer_bias(pc_ratio, pc_oi_ratio, options_data)

            # Flow classification
            flow_signal = self._classify_flow(pc_ratio, pc_oi_ratio)

            result = {
                'pc_ratio': pc_ratio,
                'pc_oi_ratio': pc_oi_ratio,
                'dealer_bias': dealer_bias,
                'flow_signal': flow_signal,
                'call_dominance': pc_ratio < 0.8,
                'put_dominance': pc_ratio > 1.2,
                'retail_fear': pc_ratio > 1.5,
                'institutional_hedging': pc_oi_ratio > 1.5
            }

            return result

        except Exception as e:
            logging.debug(f"Error in flow analysis for {symbol}: {e}")
            return self._default_flow()

    def _analyze_dealer_bias(self, pc_ratio: float, pc_oi_ratio: float, options_data: List[Dict]) -> str:
        """Determine dealer/market maker positioning"""
        if pc_ratio > 1.5 and pc_oi_ratio > 1.2:
            return "Heavy hedging pressure - dealers buying calls"
        elif pc_ratio < 0.7 and pc_oi_ratio < 0.8:
            return "Aggressive positioning - dealers selling puts"
        elif pc_ratio > 1.2:
            return "Put-heavy retail activity - dealers accommodating"
        elif pc_ratio < 0.8:
            return "Call-heavy retail activity - dealers neutralizing"
        else:
            return "Balanced positioning - neutral dealer stance"

    def _classify_flow(self, pc_ratio: float, pc_oi_ratio: float) -> str:
        """Classify overall order flow"""
        if pc_ratio > 1.5 or pc_oi_ratio > 1.5:
            return "BEARISH_BIAS"
        elif pc_ratio < 0.7 or pc_oi_ratio < 0.7:
            return "BULLISH_BIAS"
        else:
            return "NEUTRAL_FLOW"

    def _default_flow(self) -> Dict:
        """Default flow analysis when data unavailable"""
        return {
            'pc_ratio': 1.0,
            'pc_oi_ratio': 1.0,
            'dealer_bias': 'Unknown',
            'flow_signal': 'NEUTRAL',
            'call_dominance': False,
            'put_dominance': False,
            'retail_fear': False,
            'institutional_hedging': False
        }
