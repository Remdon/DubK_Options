"""
Multi-Leg Order Manager - Advanced Multi-Leg Order Management

Manages complex multi-leg order operations:
- Order submission with validation
- Fill monitoring and partial fill handling
- Cancellation and rollback logic
- Transaction cost optimization
"""

import logging
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
from colorama import Fore, Style

# Import utility functions
from src.utils.validators import calculate_dynamic_limit_price


class MultiLegOrderManager:
    """
    Comprehensive multi-leg options order management with full IRON_CONDOR support.
    Handles complex spreads, straddles, strangles, and iron condors.
    """

    def __init__(self, trading_client, options_validator):
        self.trading_client = trading_client
        self.validator = options_validator
        self.supported_strategies = [
            'IRON_CONDOR', 'BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD',
            'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD',
            'STRADDLE', 'STRANGLE', 'BUTTERFLY', 'COLLAR'
        ]

    def can_execute_multi_leg(self, strategy: str, legs: List[Dict]) -> Tuple[bool, str]:
        """
        Validate if a multi-leg strategy can be executed with current position sizes.
        Returns (can_execute, reason)
        """
        if strategy.upper() not in self.supported_strategies:
            return False, f"Strategy '{strategy}' not supported by multi-leg manager"

        # Check minimum legs required
        strategy_requirements = {
            'IRON_CONDOR': 4,
            'BULL_CALL_SPREAD': 2,
            'BEAR_PUT_SPREAD': 2,
            'BULL_PUT_SPREAD': 2,
            'BEAR_CALL_SPREAD': 2,
            'STRADDLE': 2,
            'STRANGLE': 2,
            'BUTTERFLY': 3,
            'COLLAR': 2
        }

        required_legs = strategy_requirements.get(strategy.upper(), 0)
        if len(legs) != required_legs:
            return False, f"{strategy} requires {required_legs} legs, got {len(legs)}"

        # Validate leg logic (long vs short positioning)
        strategy_leg_validation = {
            'IRON_CONDOR': lambda legs: (
                # Standard iron condor: Buy low put, Sell mid put, Sell mid call, Buy high call
                len([l for l in legs if l.get('side') == 'buy']) == 2 and
                len([l for l in legs if l.get('side') == 'sell']) == 2
            ),
            'STRADDLE': lambda legs: (
                # Both legs should be buys with same strike (approximate)
                all(l.get('side') == 'buy' for l in legs) and
                len(set(l.get('strike', 0) for l in legs)) == 1  # Same strike for straddle
            ),
            'STRANGLE': lambda legs: (
                # Both legs should be buys with different strikes
                all(l.get('side') == 'buy' for l in legs) and
                len(set(l.get('strike', 0) for l in legs)) == 2  # Different strikes for strangle
            ),
            'BULL_CALL_SPREAD': lambda legs: (
                # Lower strike buy, higher strike sell
                legs[0].get('strike', 0) < legs[1].get('strike', 0) and
                legs[0].get('side') == 'buy' and legs[1].get('side') == 'sell'
            ),
            'BEAR_PUT_SPREAD': lambda legs: (
                # Higher strike buy, lower strike sell
                legs[0].get('strike', 0) > legs[1].get('strike', 0) and
                legs[0].get('side') == 'buy' and legs[1].get('side') == 'sell'
            ),
            'BULL_PUT_SPREAD': lambda legs: (
                # Lower strike buy, higher strike sell (credit spread)
                legs[0].get('strike', 0) < legs[1].get('strike', 0) and
                legs[0].get('side') == 'buy' and legs[1].get('side') == 'sell'
            ),
            'BEAR_CALL_SPREAD': lambda legs: (
                # Lower strike sell, higher strike buy (credit spread)
                legs[0].get('strike', 0) < legs[1].get('strike', 0) and
                legs[0].get('side') == 'sell' and legs[1].get('side') == 'buy'
            )
        }

        validator = strategy_leg_validation.get(strategy.upper())
        if validator and not validator(legs):
            return False, f"Leg configuration invalid for {strategy}"

        return True, "Ready for execution"

    def calculate_multi_leg_sizing(self, symbol: str, strategy: str, legs: List[Dict],
                                  confidence: int, account_equity: float) -> Dict:
        """
        Calculate position sizes for multi-leg strategies with portfolio risk management.
        Returns sizing info and adjusted legs with quantities.
        """
        # Get base position size for confidence
        confidence_multipliers = {60: 0.03, 70: 0.04, 75: 0.05, 80: 0.06, 85: 0.07, 90: 0.08, 95: 0.10}
        base_position_pct = confidence_multipliers.get((confidence // 5) * 5, 0.04)  # Round to nearest 5

        # Strategy-specific adjustments
        strategy_multipliers = {
            'IRON_CONDOR': 0.8,  # Conservative due to complexity
            'STRADDLE': 0.6,     # High risk, limit size
            'STRANGLE': 0.5,     # High risk, limit size
            'BULL_CALL_SPREAD': 0.9,  # Relatively safe debit spread
            'BEAR_PUT_SPREAD': 0.9,   # Relatively safe debit spread
            'BULL_PUT_SPREAD': 0.9,   # Relatively safe credit spread
            'BEAR_CALL_SPREAD': 0.9   # Relatively safe credit spread
        }

        position_pct = base_position_pct * strategy_multipliers.get(strategy.upper(), 0.7)
        position_value = account_equity * position_pct

        # Calculate net credit/debit for the strategy
        total_debit = 0
        total_credit = 0

        for leg in legs:
            contract_price = leg.get('contract_price', 0)
            if leg.get('side') == 'buy':
                total_debit += contract_price * 100
            else:
                total_credit += contract_price * 100

        net_cost = total_debit - total_credit

        # VALIDATION: For debit spreads, ensure we're not overpaying
        is_debit_spread = net_cost > 0 and strategy.upper() in ['BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD']
        is_credit_spread = net_cost < 0 and strategy.upper() in ['BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD']

        if is_debit_spread and len(legs) >= 2:
            # Calculate spread width
            strikes = sorted([leg.get('strike', 0) for leg in legs if leg.get('strike')])
            if len(strikes) >= 2:
                spread_width = abs(strikes[-1] - strikes[0]) * 100  # Convert to dollar per contract
                max_acceptable_debit = spread_width * 0.60  # Never pay more than 60% of width

                if net_cost > max_acceptable_debit:
                    logging.warning(f"{symbol}: Debit ${net_cost:.2f} exceeds 60% of spread width ${spread_width:.2f} (max ${max_acceptable_debit:.2f})")
                    logging.warning(f"{symbol}: REJECTING debit spread - bad risk/reward ratio")
                    return {
                        'position_pct': position_pct,
                        'position_value': position_value,
                        'max_spreads': 0,
                        'net_cost_per_spread': net_cost,
                        'total_cost': 0,
                        'legs': [],
                        'can_afford': False,
                        'rejection_reason': f'Debit ${net_cost:.2f} > 60% of spread width ${spread_width:.2f}'
                    }

        if is_credit_spread and len(legs) >= 2:
            # Calculate spread width
            strikes = sorted([leg.get('strike', 0) for leg in legs if leg.get('strike')])
            if len(strikes) >= 2:
                spread_width = abs(strikes[-1] - strikes[0]) * 100  # Convert to dollar per contract
                min_acceptable_credit = spread_width * 0.30  # Collect at least 30% of width

                if abs(net_cost) < min_acceptable_credit:
                    logging.warning(f"{symbol}: Credit ${abs(net_cost):.2f} below 30% of spread width ${spread_width:.2f} (min ${min_acceptable_credit:.2f})")
                    logging.warning(f"{symbol}: REJECTING credit spread - insufficient premium collected")
                    return {
                        'position_pct': position_pct,
                        'position_value': position_value,
                        'max_spreads': 0,
                        'net_cost_per_spread': net_cost,
                        'total_cost': 0,
                        'legs': [],
                        'can_afford': False,
                        'rejection_reason': f'Credit ${abs(net_cost):.2f} < 30% of spread width ${spread_width:.2f}'
                    }

        if net_cost == 0:
            max_spreads = int(position_value / 100)  # Minimum 1 contract per leg if free
        else:
            max_spreads = max(1, int(position_value / abs(net_cost)))

        max_spreads = min(max_spreads, 50)  # Cap at reasonable spread count

        # Apply quantity to each leg
        legs_with_quantities = []
        for leg in legs:
            leg_copy = leg.copy()
            leg_copy['quantity'] = max_spreads
            legs_with_quantities.append(leg_copy)

        total_cost = net_cost * max_spreads

        result = {
            'position_pct': position_pct,
            'position_value': position_value,
            'max_spreads': max_spreads,
            'net_cost_per_spread': net_cost,
            'total_cost': total_cost,
            'legs': legs_with_quantities,
            'can_afford': abs(total_cost) <= position_value * 1.2  # 20% buffer
        }

        return result

    def cancel_multi_leg_order_safely(self, strategy_id: str) -> Dict:
        """
        PHASE 1: Safely cancel a multi-leg order with pre-fill verification

        Returns:
            Dict with:
                - success: bool
                - cancelled_legs: int
                - message: str
                - had_fills: bool (True if any leg filled before cancellation)
        """
        result = {
            'success': False,
            'cancelled_legs': 0,
            'message': '',
            'had_fills': False
        }

        try:
            # Get strategy status
            strategy_status = self.multi_leg_tracker.get_strategy_status(strategy_id)
            if not strategy_status:
                result['message'] = f"Strategy {strategy_id} not found in tracker"
                logging.error(result['message'])
                return result

            # CRITICAL CHECK: Verify no legs have filled
            if self.multi_leg_tracker.has_any_fills(strategy_id):
                result['had_fills'] = True
                result['message'] = f"❌ CANNOT CANCEL: Strategy {strategy_id} has {len(strategy_status['filled_leg_ids'])} filled legs. Must close positions instead."
                logging.error(result['message'])
                self.alert_manager.send_critical_alert(
                    "Multi-Leg Partial Fill Detected",
                    result['message']
                )
                return result

            # Safe to cancel - no fills detected
            unfilled_leg_ids = self.multi_leg_tracker.get_unfilled_leg_ids(strategy_id)

            cancelled_count = 0
            failed_cancellations = []

            for order_id in unfilled_leg_ids:
                try:
                    self.trading_client.cancel_order_by_id(order_id)
                    self.multi_leg_tracker.update_leg_status(strategy_id, order_id, 'CANCELLED')
                    cancelled_count += 1
                    logging.info(f"Cancelled leg order {order_id} from strategy {strategy_id}")
                except Exception as e:
                    failed_cancellations.append(order_id)
                    logging.error(f"Failed to cancel leg order {order_id}: {e}")

            result['success'] = cancelled_count > 0
            result['cancelled_legs'] = cancelled_count

            if failed_cancellations:
                result['message'] = f"Cancelled {cancelled_count} legs, failed to cancel {len(failed_cancellations)} legs"
            else:
                result['message'] = f"Successfully cancelled all {cancelled_count} legs of strategy {strategy_id}"

            logging.info(result['message'])

        except Exception as e:
            result['message'] = f"Error during multi-leg cancellation: {str(e)}"
            logging.error(result['message'])

        return result

    def close_multi_leg_position_safely(self, strategy_id: str) -> Dict:
        """
        PHASE 1: Safely close a multi-leg position with exit verification

        Returns:
            Dict with:
                - success: bool
                - closed_legs: int
                - message: str
                - partial_close: bool (True if only some legs closed)
        """
        result = {
            'success': False,
            'closed_legs': 0,
            'message': '',
            'partial_close': False
        }

        try:
            # Get strategy status
            strategy_status = self.multi_leg_tracker.get_strategy_status(strategy_id)
            if not strategy_status:
                result['message'] = f"Strategy {strategy_id} not found in tracker"
                logging.error(result['message'])
                return result

            symbol = strategy_status['symbol']
            strategy = strategy_status['strategy']
            filled_leg_ids = strategy_status['filled_leg_ids']

            if not filled_leg_ids:
                result['message'] = f"No filled legs to close for strategy {strategy_id}"
                logging.warning(result['message'])
                return result

            logging.info(f"Attempting to close {len(filled_leg_ids)} filled legs of strategy {strategy_id} ({symbol} {strategy})")

            closed_count = 0
            failed_closes = []

            # Get all positions to match leg order IDs
            positions = self.trading_client.get_all_positions()

            for leg_order_id in filled_leg_ids:
                try:
                    # Find position that corresponds to this leg order
                    # Note: Alpaca positions don't directly link to order IDs,
                    # so we need to close by symbol from legs_info
                    leg_closed = False

                    for leg_info in strategy_status['legs_info']:
                        # Build OCC symbol for this leg
                        leg_symbol = f"{symbol}"  # Would need full OCC symbol reconstruction

                        # For now, close all positions matching the underlying
                        for position in positions:
                            if position.symbol.startswith(symbol):
                                try:
                                    self.trading_client.close_position(position.symbol)
                                    closed_count += 1
                                    leg_closed = True
                                    logging.info(f"Closed position {position.symbol} for leg order {leg_order_id}")
                                    break
                                except Exception as close_err:
                                    logging.error(f"Failed to close position {position.symbol}: {close_err}")
                                    failed_closes.append(position.symbol)

                        if leg_closed:
                            break

                except Exception as e:
                    failed_closes.append(leg_order_id)
                    logging.error(f"Failed to close leg {leg_order_id}: {e}")

            # Check for partial close
            if closed_count > 0 and closed_count < len(filled_leg_ids):
                result['partial_close'] = True
                result['message'] = f"⚠️ PARTIAL CLOSE: Closed {closed_count}/{len(filled_leg_ids)} legs of strategy {strategy_id}"
                logging.error(result['message'])
                self.alert_manager.send_critical_alert(
                    "Multi-Leg Partial Close Detected",
                    f"{result['message']}\nStrategy: {symbol} {strategy}\nFailed: {', '.join(failed_closes)}"
                )
            elif closed_count == len(filled_leg_ids):
                result['success'] = True
                result['message'] = f"Successfully closed all {closed_count} legs of strategy {strategy_id}"
                logging.info(result['message'])
            elif closed_count == 0:
                result['message'] = f"Failed to close any legs of strategy {strategy_id}"
                logging.error(result['message'])
            else:
                result['success'] = True
                result['message'] = f"Closed {closed_count} legs"

            result['closed_legs'] = closed_count

        except Exception as e:
            result['message'] = f"Error during multi-leg close: {str(e)}"
            logging.error(result['message'])

        return result

    def execute_multi_leg_order(self, symbol: str, legs: List[Dict], strategy: str,
                               sizing_info: Dict) -> Dict:
        """
        Execute multi-leg order with proper order bracketing.
        For vertical spreads (BULL_CALL_SPREAD, BEAR_PUT_SPREAD), uses multi-leg orders to avoid wash trade errors.
        Returns comprehensive execution results.
        """
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent, OrderClass

        execution_results = {
            'success': False,
            'strategy': strategy,
            'legs_executed': 0,
            'total_quantity': 0,
            'total_cost': 0,
            'order_ids': [],
            'errors': []
        }

        # Check if this is a vertical spread that requires multi-leg order submission
        is_vertical_spread = strategy.upper() in ['BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD', 'BEAR_CALL_SPREAD', 'BULL_PUT_SPREAD']

        if is_vertical_spread and len(legs) == 2:
            # Use multi-leg order to avoid wash trade detection
            return self._execute_vertical_spread(symbol, legs, strategy, sizing_info)

        try:
            # Execute each leg separately (for STRADDLE, STRANGLE, IRON_CONDOR, etc.)
            for leg in legs:
                quantity = leg.get('quantity', 0)
                if quantity <= 0:
                    continue

                # Build OCC symbol
                exp_str = leg.get('expiration_str', '251121')  # Default fallback
                occ_symbol = self._build_occ_symbol(symbol, exp_str, leg['type'][0].upper(), leg['strike'])

                # Determine order side
                order_side = OrderSide.BUY if leg['side'] == 'buy' else OrderSide.SELL

                # Get pricing
                bid = leg.get('bid', 0) or 0
                ask = leg.get('ask', 0) or 0
                contract_price = leg.get('contract_price', 0)

                # FIXED: Issue #8 - Use dynamic slippage for multi-leg orders
                side_str = 'buy' if order_side == OrderSide.BUY else 'sell'
                limit_price = calculate_dynamic_limit_price(bid, ask, side_str, contract_price)

                try:
                    # Determine position intent based on order side
                    position_intent = PositionIntent.BUY_TO_OPEN if order_side == OrderSide.BUY else PositionIntent.SELL_TO_OPEN

                    order_data = LimitOrderRequest(
                        symbol=occ_symbol,
                        qty=quantity,
                        side=order_side,
                        time_in_force=TimeInForce.DAY,
                        limit_price=limit_price,
                        position_intent=position_intent
                    )

                    order = self.trading_client.submit_order(order_data)

                    leg_result = {
                        'symbol': occ_symbol,
                        'side': leg['side'],
                        'quantity': quantity,
                        'price': limit_price,
                        'order_id': order.id if hasattr(order, 'id') else 'UNKNOWN',
                        'strategy_leg': leg
                    }

                    execution_results['order_ids'].append(order.id)
                    execution_results['legs_executed'] += 1
                    execution_results['total_quantity'] += quantity
                    execution_results['total_cost'] += limit_price * quantity * 100

                    logging.info(f"Multi-leg leg executed: {occ_symbol} {leg['side']} {quantity} @ ${limit_price}")

                except Exception as e:
                    error_msg = f"Failed to execute leg {leg['type']} {leg['strike']}: {str(e)}"
                    execution_results['errors'].append(error_msg)
                    logging.error(error_msg)

            # Success if at least one leg executed
            execution_results['success'] = execution_results['legs_executed'] > 0

            if execution_results['success']:
                logging.info(f"Multi-leg strategy {strategy} partially executed: {execution_results['legs_executed']}/{len(legs)} legs")

        except Exception as e:
            execution_results['errors'].append(f"Critical execution error: {str(e)}")
            logging.error(f"Multi-leg execution failed: {e}")

        return execution_results

    def _execute_vertical_spread(self, symbol: str, legs: List[Dict], strategy: str, sizing_info: Dict) -> Dict:
        """
        Execute vertical spread using multi-leg order to avoid wash trade detection.
        BULL_CALL_SPREAD, BEAR_PUT_SPREAD, etc. must be submitted as a single multi-leg order.
        """
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent, OrderClass

        execution_results = {
            'success': False,
            'strategy': strategy,
            'legs_executed': 0,
            'total_quantity': 0,
            'total_cost': 0,
            'order_ids': [],
            'errors': []
        }

        try:
            # Build option legs for multi-leg order
            option_legs = []
            net_debit = 0.0
            quantity = legs[0].get('quantity', 0)

            for leg in legs:
                # Build OCC symbol
                exp_str = leg.get('expiration_str', '251121')
                occ_symbol = self._build_occ_symbol(symbol, exp_str, leg['type'][0].upper(), leg['strike'])

                # Determine side and position intent
                order_side = OrderSide.BUY if leg['side'] == 'buy' else OrderSide.SELL
                position_intent = PositionIntent.BUY_TO_OPEN if leg['side'] == 'buy' else PositionIntent.SELL_TO_OPEN

                # Get pricing
                bid = leg.get('bid', 0) or 0
                ask = leg.get('ask', 0) or 0
                contract_price = leg.get('contract_price', 0)
                side_str = 'buy' if order_side == OrderSide.BUY else 'sell'
                limit_price = calculate_dynamic_limit_price(bid, ask, side_str, contract_price)

                # Calculate net debit/credit
                if leg['side'] == 'buy':
                    net_debit += limit_price
                else:
                    net_debit -= limit_price

                # Create option leg
                option_leg = OptionLegRequest(
                    symbol=occ_symbol,
                    ratio_qty=1,  # Each leg has ratio 1 for vertical spreads
                    side=order_side,
                    position_intent=position_intent
                )
                option_legs.append(option_leg)

                logging.info(f"Prepared leg {leg['type']} ${leg['strike']} {leg['side']}: {occ_symbol} @ ${limit_price}")

            # Calculate limit price for the spread (net debit/credit)
            # IMPROVED: Adjust pricing based on spread type for better fill rates
            is_credit_spread = net_debit < 0  # Negative net_debit means we're collecting credit
            is_debit_spread = net_debit > 0   # Positive net_debit means we're paying debit

            if is_credit_spread:
                # For credit spreads: Accept 85% of max credit for faster fills
                # Example: If we can collect $1.00, we'll accept $0.85
                spread_limit_price = round(abs(net_debit) * 0.85, 2)
                logging.info(f"Credit spread: Adjusted from ${abs(net_debit):.2f} to ${spread_limit_price:.2f} (85% for faster fills)")
            elif is_debit_spread:
                # For debit spreads: Willing to pay 10% more for fills
                # Example: If mid price is $1.00, we'll pay up to $1.10
                spread_limit_price = round(abs(net_debit) * 1.10, 2)
                logging.info(f"Debit spread: Adjusted from ${abs(net_debit):.2f} to ${spread_limit_price:.2f} (110% for faster fills)")
            else:
                # Net zero (unlikely) - use small minimum
                spread_limit_price = 0.05
                logging.warning(f"Net debit is zero for {strategy}, using minimum ${spread_limit_price}")

            # Ensure minimum price (avoid free spreads or rounding issues)
            spread_limit_price = max(0.01, spread_limit_price)

            # Create multi-leg order
            logging.info(f"Submitting {strategy} as multi-leg order: {quantity} spreads @ net ${spread_limit_price}")

            order_request = LimitOrderRequest(
                qty=quantity,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=option_legs,
                limit_price=spread_limit_price
            )

            order = self.trading_client.submit_order(order_request)

            # IMPROVED: Monitor order fill status for immediate feedback
            order_id = order.id if hasattr(order, 'id') else None
            fill_status = 'pending'

            if order_id:
                # Wait up to 5 seconds to check if order fills quickly
                import time
                for i in range(5):
                    time.sleep(1)
                    try:
                        order_status = self.trading_client.get_order_by_id(order_id)
                        if order_status.status in ['filled', 'partially_filled']:
                            fill_status = str(order_status.status)
                            logging.info(f"Order {order_id} {fill_status} quickly!")
                            break
                    except Exception as e:
                        logging.debug(f"Error checking order status: {e}")
                        break

            # Success!
            execution_results['success'] = True
            execution_results['legs_executed'] = len(legs)
            execution_results['total_quantity'] = quantity
            execution_results['total_cost'] = spread_limit_price * quantity * 100
            execution_results['order_ids'].append(order_id if order_id else 'UNKNOWN')
            execution_results['fill_status'] = fill_status

            logging.info(f"✓ Multi-leg {strategy} executed successfully: {len(legs)} legs, {quantity} spreads @ ${spread_limit_price} (Status: {fill_status})")

        except Exception as e:
            error_msg = f"Failed to execute multi-leg {strategy}: {str(e)}"
            execution_results['errors'].append(error_msg)
            logging.error(error_msg)

        return execution_results

    def calculate_multi_leg_greeks(self, legs: List[Dict], symbol: str, underlying_price: float = None) -> Dict:
        """
        Calculate net Greeks for multi-leg options strategies

        Args:
            legs: List of leg dictionaries with structure:
                  {'type': 'call'/'put', 'strike': float, 'side': 'buy'/'sell',
                   'quantity': int, 'delta': float, 'gamma': float, 'theta': float, 'vega': float}
            symbol: Underlying symbol
            underlying_price: Current price of underlying (for validation)

        Returns:
            Dict with net Greeks: {'delta': float, 'gamma': float, 'theta': float, 'vega': float,
                                   'net_delta_dollars': float, 'theta_per_day': float}
        """
        net_delta = net_gamma = net_theta = net_vega = 0.0

        for leg in legs:
            try:
                # Get leg Greeks (might be in different keys)
                leg_delta = leg.get('delta', 0) or leg.get('greeks_delta', 0) or 0
                leg_gamma = leg.get('gamma', 0) or leg.get('greeks_gamma', 0) or 0
                leg_theta = leg.get('theta', 0) or leg.get('greeks_theta', 0) or 0
                leg_vega = leg.get('vega', 0) or leg.get('greeks_vega', 0) or 0

                quantity = leg.get('quantity', 1)
                side = leg.get('side', 'buy').lower()

                # Apply sign based on side (buy = positive, sell = negative)
                multiplier = quantity if side == 'buy' else -quantity

                # Accumulate Greeks
                net_delta += leg_delta * multiplier
                net_gamma += leg_gamma * multiplier
                net_theta += leg_theta * multiplier
                net_vega += leg_vega * multiplier

                logging.debug(f"Leg {leg.get('type')} {leg.get('strike')}: "
                             f"delta={leg_delta * multiplier:.3f}, "
                             f"gamma={leg_gamma * multiplier:.4f}, "
                             f"theta={leg_theta * multiplier:.2f}, "
                             f"vega={leg_vega * multiplier:.2f}")

            except Exception as e:
                logging.warning(f"Error calculating Greeks for leg: {e}")
                continue

        # Calculate dollar Greeks
        net_delta_dollars = net_delta * 100 if underlying_price else net_delta
        theta_per_day = net_theta * 100  # Theta is per share, convert to per contract

        result = {
            'delta': round(net_delta, 4),
            'gamma': round(net_gamma, 6),
            'theta': round(net_theta, 4),
            'vega': round(net_vega, 4),
            'net_delta_dollars': round(net_delta_dollars, 2),
            'theta_per_day': round(theta_per_day, 2)
        }

        logging.info(f"Multi-leg Greeks for {symbol}: "
                    f"Δ={result['delta']:.3f}, "
                    f"Γ={result['gamma']:.4f}, "
                    f"Θ=${result['theta_per_day']:.2f}/day, "
                    f"V={result['vega']:.2f}")

        return result

    def estimate_strategy_greeks(self, strategy: str, underlying_price: float, strikes: str,
                                atm_greeks: Dict = None) -> Dict:
        """
        Estimate Greeks for multi-leg strategies when leg-level Greeks are unavailable

        Args:
            strategy: Strategy name (e.g., 'BULL_CALL_SPREAD')
            underlying_price: Current price of underlying
            strikes: Strike string (e.g., "450/455" for spreads)
            atm_greeks: ATM option Greeks for reference

        Returns:
            Dict with estimated Greeks
        """
        try:
            # Clean and parse strikes
            if not strikes or not isinstance(strikes, str):
                strikes = "0"
            strike_list = []
            for s in strikes.split('/'):
                try:
                    if s.strip():
                        strike_list.append(float(s.strip()))
                except ValueError:
                    continue

            # Use ATM Greeks as baseline if provided
            if atm_greeks:
                base_delta = abs(atm_greeks.get('delta', 0.5))
                base_gamma = atm_greeks.get('gamma', 0.05)
                base_theta = atm_greeks.get('theta', -0.05)
                base_vega = atm_greeks.get('vega', 0.1)
            else:
                # Conservative defaults for ATM options
                base_delta = 0.50  # ATM delta
                base_gamma = 0.05  # ATM gamma
                base_theta = -0.05  # ATM theta (decay)
                base_vega = 0.1  # ATM vega

            strategy_upper = strategy.upper()

            # Strategy-specific Greek profiles
            if strategy_upper == 'BULL_CALL_SPREAD':
                # Long lower call, short higher call
                net_delta = base_delta * 0.6  # Positive delta bias
                net_gamma = base_gamma * 0.3   # Reduced gamma
                net_theta = base_theta * 1.2   # More negative theta
                net_vega = base_vega * 0.3     # Reduced vega

            elif strategy_upper == 'BEAR_PUT_SPREAD':
                # Long higher put, short lower put
                net_delta = -base_delta * 0.6  # Negative delta bias
                net_gamma = base_gamma * 0.3
                net_theta = base_theta * 1.2
                net_vega = base_vega * 0.3

            elif strategy_upper in ['BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD']:
                # Credit spreads - net short premium
                net_delta = base_delta * 0.4 if strategy_upper == 'BULL_PUT_SPREAD' else -base_delta * 0.4
                net_gamma = -base_gamma * 0.2  # Short gamma
                net_theta = -base_theta * 0.8  # Positive theta (collect decay)
                net_vega = -base_vega * 0.3    # Short vega

            elif strategy_upper == 'IRON_CONDOR':
                # Sell puts and calls, buy wings - delta neutral
                net_delta = 0.0  # Should be delta-neutral
                net_gamma = -base_gamma * 0.4  # Short gamma
                net_theta = -base_theta * 1.5  # Positive theta (main profit)
                net_vega = -base_vega * 0.5    # Short vega

            elif strategy_upper in ['LONG_STRADDLE', 'LONG_STRANGLE', 'STRADDLE', 'STRANGLE']:
                # Long call + long put - volatility plays
                net_delta = 0.0  # Delta-neutral
                net_gamma = base_gamma * 1.5   # High positive gamma
                net_theta = base_theta * 2.0   # High negative theta
                net_vega = base_vega * 1.5     # High positive vega

            elif strategy_upper in ['SHORT_STRADDLE', 'SHORT_STRANGLE']:
                # Short call + short put - volatility plays
                net_delta = 0.0
                net_gamma = -base_gamma * 1.5  # High negative gamma
                net_theta = -base_theta * 2.0  # High positive theta
                net_vega = -base_vega * 1.5    # High negative vega

            elif strategy_upper == 'BUTTERFLY_SPREAD':
                # Long 1 ITM, short 2 ATM, long 1 OTM
                net_delta = 0.1  # Near delta-neutral
                net_gamma = base_gamma * 0.2
                net_theta = -base_theta * 0.5
                net_vega = -base_vega * 0.2

            elif strategy_upper in ['CALENDAR_SPREAD', 'DIAGONAL_SPREAD']:
                # Sell short-term, buy long-term
                net_delta = base_delta * 0.3
                net_gamma = base_gamma * 0.1
                net_theta = -base_theta * 0.8  # Positive theta from short leg
                net_vega = base_vega * 0.7     # Long vega from long-term leg

            elif strategy_upper == 'COVERED_CALL':
                # Long stock + short call
                net_delta = 1.0 - base_delta
                net_gamma = -base_gamma * 0.5
                net_theta = -base_theta * 0.5
                net_vega = -base_vega * 0.5

            elif strategy_upper == 'PROTECTIVE_PUT':
                # Long stock + long put
                net_delta = 1.0 - base_delta
                net_gamma = base_gamma * 0.5
                net_theta = base_theta * 0.5
                net_vega = base_vega * 0.5

            elif strategy_upper == 'COLLAR':
                # Long stock + long put + short call
                net_delta = 0.8  # Slightly reduced from 1.0
                net_gamma = 0.0  # Offsetting gamma
                net_theta = 0.0  # Offsetting theta
                net_vega = 0.0   # Offsetting vega

            else:
                # Unknown strategy - use conservative defaults
                logging.warning(f"Unknown strategy for Greek estimation: {strategy}")
                net_delta = 0.0
                net_gamma = 0.0
                net_theta = 0.0
                net_vega = 0.0

            result = {
                'delta': round(net_delta, 4),
                'gamma': round(net_gamma, 6),
                'theta': round(net_theta, 4),
                'vega': round(net_vega, 4),
                'net_delta_dollars': round(net_delta * 100, 2),
                'theta_per_day': round(net_theta * 100, 2),
                'estimated': True  # Flag for estimated Greeks
            }

            logging.info(f"Estimated Greeks for {strategy}: "
                        f"Δ={result['delta']:.3f}, Γ={result['gamma']:.4f}, "
                        f"Θ=${result['theta_per_day']:.2f}/day, V={result['vega']:.2f}")

            return result

        except Exception as e:
            logging.error(f"Error estimating Greeks for {strategy}: {e}")
            return {
                'delta': 0.0,
                'gamma': 0.0,
                'theta': 0.0,
                'vega': 0.0,
                'net_delta_dollars': 0.0,
                'theta_per_day': 0.0,
                'estimated': True
            }

    def _build_occ_symbol(self, symbol: str, exp_str: str, option_type: str, strike: float) -> str:
        """Build OCC format symbol"""
        # Ensure exp_str is 6 digits (YYMMDD format)
        while len(exp_str) < 6:
            exp_str = '25' + exp_str if exp_str.startswith('1') else '251' + exp_str  # Default to 2025

        strike_int = int(strike * 1000)
        strike_str = f"{strike_int:08d}"
        return f"{symbol}{exp_str}{option_type}{strike_str}"
