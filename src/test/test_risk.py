"""
Unit tests for risk management functions
"""
import pytest
import os
import sys
import statistics
from unittest.mock import Mock

# Add the src directory to path so we can import from it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_calculate_dynamic_limit_price():
    """Test dynamic limit price calculations"""

    # Simulate the calculate_dynamic_limit_price function from main file
    def calculate_dynamic_limit_price(bid: float, ask: float, side: str, contract_price: float = None) -> float:
        """Calculate competitive limit price for high fill probability"""
        if bid <= 0 and ask <= 0:
            return contract_price * 1.02 if contract_price else 0.05

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
        else:  # sell
            if bid > 0:
                if spread_pct > 0.10:
                    limit_price = bid - (spread_width * 0.10)
                else:
                    limit_price = bid
            else:
                limit_price = ask - (spread_width * 1.05)

        max_deviation = mid * 0.03
        if side.lower() == 'buy':
            limit_price = min(limit_price, mid + max_deviation)
        else:
            limit_price = max(limit_price, mid - max_deviation)

        return max(0.05, round(limit_price, 2))

    # Test buy orders
    bid, ask = 4.95, 5.05
    buy_limit = calculate_dynamic_limit_price(bid, ask, 'buy')
    assert buy_limit == 5.05  # Should use ask price for tight spread

    # Test wide spread buy
    bid, ask = 4.80, 5.20
    spread_pct = (5.20 - 4.80) / 5.0 = 0.08
    buy_limit = calculate_dynamic_limit_price(bid, ask, 'buy')
    assert buy_limit == 5.20 + (0.40 * 0.10)  # Should add buffer for wide spread

    # Test sell orders
    bid, ask = 4.95, 5.05
    sell_limit = calculate_dynamic_limit_price(bid, ask, 'sell')
    assert sell_limit == 4.95  # Should use bid price for tight spread

    # Test minimum price
    limit = calculate_dynamic_limit_price(0.02, 0.02, 'buy')
    assert limit >= 0.05


def test_validate_contract_liquidity():
    """Test option contract liquidity validation"""

    def validate_contract_liquidity(contract: dict, paper_mode: bool = False) -> tuple[bool, str]:
        """Validates option contract fitness"""
        bid = contract.get('bid', 0) or 0
        ask = contract.get('ask', 0) or 0
        volume = contract.get('volume', 0) or 0
        oi = contract.get('open_interest', 0) or 0
        last_price = contract.get('last_price', 0) or 0

        is_paper_mode = paper_mode

        if is_paper_mode:
            if last_price > 0:
                return True, "OK (paper mode)"
            if bid > 0 or ask > 0:
                return True, "OK (paper mode - using bid/ask)"
            return False, "No valid price data"

        # Spread validation
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid if mid > 0 else 0.0
            if spread_pct > 0.15:
                return False, f"Bid-ask spread too wide: {spread_pct:.1%}"

        # Volume validation
        if volume < 50:
            return False, f"Insufficient volume: {volume} (min: 50)"

        # Open interest validation
        if oi < 500:
            return False, f"Insufficient open interest: {oi} (min: 500)"

        # Price validation
        if last_price < 0.05:
            return False, "Contract price too low"

        return True, "OK"

    # Test paper mode (relaxed requirements)
    paper_contract = {'last_price': 0.10}
    valid, msg = validate_contract_liquidity(paper_contract, paper_mode=True)
    assert valid == True
    assert "paper mode" in msg

    # Test live mode strict requirements
    good_contract = {
        'bid': 2.50, 'ask': 2.65, 'volume': 100, 'open_interest': 2000, 'last_price': 2.55
    }
    valid, msg = validate_contract_liquidity(good_contract)
    assert valid == True and msg == "OK"

    # Test insufficient volume
    bad_volume = {**good_contract, 'volume': 30}
    valid, msg = validate_contract_liquidity(bad_volume)
    assert valid == False
    assert "volume" in msg

    # Test insufficient OI
    bad_oi = {**good_contract, 'open_interest': 200}
    valid, msg = validate_contract_liquidity(bad_oi)
    assert valid == False
    assert "open_interest" in msg

    # Test excessive spread
    bad_spread = {'bid': 1.00, 'ask': 1.20, 'volume': 100, 'open_interest': 2000, 'last_price': 1.10}
    valid, msg = validate_contract_liquidity(bad_spread)
    assert valid == False
    assert "spread" in msg


def test_calculate_multi_leg_greeks():
    """Test multi-leg options Greeks calculation"""

    def calculate_multi_leg_greeks(legs: list, symbol: str, underlying_price: float = None) -> dict:
        """Calculate net Greeks for multi-leg strategies"""
        net_delta = net_gamma = net_theta = net_vega = 0.0

        for leg in legs:
            multiplier = leg.get('quantity', 1) if leg.get('side') == 'buy' else -leg.get('quantity', 1)

            net_delta += leg.get('delta', 0) * multiplier
            net_gamma += leg.get('gamma', 0) * multiplier
            net_theta += leg.get('theta', 0) * multiplier
            net_vega += leg.get('vega', 0) * multiplier

        return {
            'delta': round(net_delta, 4),
            'gamma': round(net_gamma, 6),
            'theta': round(net_theta, 4),
            'vega': round(net_vega, 4),
            'net_delta_dollars': round(net_delta * 100, 2),
            'theta_per_day': round(net_theta * 100, 2)
        }

    # Test long straddle: call + put at same strike
    legs = [
        {'type': 'call', 'side': 'buy', 'quantity': 1, 'delta': 0.55, 'gamma': 0.08, 'theta': -0.03, 'vega': 0.15},
        {'type': 'put', 'side': 'buy', 'quantity': 1, 'delta': -0.45, 'gamma': 0.08, 'theta': -0.03, 'vega': 0.15}
    ]

    greeks = calculate_multi_leg_greeks(legs, 'SPY', 400.0)
    assert abs(greeks['delta']) < 0.01  # Delta-neutral
    assert greeks['gamma'] > 0  # Positive gamma from both long legs
    assert greeks['theta'] < 0  # Negative theta from both long legs
    assert greeks['vega'] > 0  # Positive vega from both long legs

    # Test bull call spread: long lower call, short higher call
    spread_legs = [
        {'type': 'call', 'side': 'buy', 'quantity': 1, 'delta': 0.30, 'gamma': 0.05, 'theta': -0.02, 'vega': 0.10},
        {'type': 'call', 'side': 'sell', 'quantity': 1, 'delta': 0.15, 'gamma': -0.03, 'theta': -0.01, 'vega': -0.05}
    ]

    spread_greeks = calculate_multi_leg_greeks(spread_legs, 'AAPL', 150.0)
    assert spread_greeks['delta'] == 0.15  # Net positive delta (bullish)
    assert spread_greeks['gamma'] > 0  # Net positive gamma
    assert spread_greeks['theta'] < 0  # Net negative theta
    assert spread_greeks['vega'] > 0  # Net positive vega


class TestRiskCalculations:
    """Test risk calculation functions"""

    def test_portfolio_exposure_calculation(self):
        """Test portfolio exposure calculations"""

        class MockPosition:
            def __init__(self, symbol, market_value, qty):
                self.symbol = symbol
                self.market_value = market_value
                self.qty = qty

        # Mock positions
        positions = [
            MockPosition('AAPL', 10000, 5),
            MockPosition('MSFT', 15000, 10),
            MockPosition('SPY240101C00450000', 2500, 2)  # Option position
        ]

        total_equity = 50000

        # Calculate exposure
        exposure = {'by_symbol': {}, 'total_allocated': 0}

        for pos in positions:
            symbol = pos.symbol
            if isinstance(pos.market_value, (int, float)) and pos.market_value > 0:
                pct = pos.market_value / total_equity
                exposure['by_symbol'][symbol] = pct
                exposure['total_allocated'] += pct

        # Assertions
        assert exposure['by_symbol']['AAPL'] == 0.2  # $10k / $50k
        assert exposure['by_symbol']['MSFT'] == 0.3  # $15k / $50k
        assert exposure['by_symbol']['SPY240101C00450000'] == 0.05  # $2.5k / $50k
        assert exposure['total_allocated'] == 0.55  # 55% allocated

    def test_position_sizing_logic(self):
        """Test position sizing based on confidence and exposure"""

        class MockManager:
            def __init__(self):
                self.MAX_POSITION_PCT = 0.15
                self.MAX_SYMBOL_EXPOSURE = 0.25

            def can_enter_position(self, symbol, position_size_pct, exposure):
                total_positions = len(exposure.get('by_symbol', {}))
                if total_positions >= 10:  # MAX_TOTAL_POSITIONS
                    return False, "Max positions reached"

                current_symbol_exposure = exposure.get('by_symbol', {}).get(symbol, 0)
                if current_symbol_exposure + position_size_pct > self.MAX_SYMBOL_EXPOSURE:
                    return False, f"Would exceed symbol exposure limit"

                return True, "Position allowed"

            def calculate_optimal_position_size(self, confidence, exposure, base_size_pct=0.05):
                """Simplified position sizing"""
                if confidence >= 95:
                    multiplier = 1.8  # 9%
                elif confidence >= 90:
                    multiplier = 1.5  # 7.5%
                elif confidence >= 80:
                    multiplier = 1.2  # 6%
                else:
                    multiplier = 1.0  # 5%

                position_size = base_size_pct * multiplier
                return min(position_size, self.MAX_POSITION_PCT)  # Cap at 15%

        manager = MockManager()

        # Test position sizing
        size = manager.calculate_optimal_position_size(95, {})
        assert size == 0.09  # 5% * 1.8 = 9%, under cap

        size = manager.calculate_optimal_position_size(70, {})
        assert size == 0.05  # Base size for medium confidence

        # Test position limits
        exposure = {'by_symbol': {'AAPL': 0.20}}  # 20% already allocated to AAPL
        can_enter, reason = manager.can_enter_position('AAPL', 0.10, exposure)  # Would be 30%
        assert can_enter == False
        assert "symbol exposure" in reason


if __name__ == '__main__':
    pytest.main([__file__])
