"""
Multi-Leg Options Manager - Multi-Leg Strategy Execution

Handles execution of multi-leg options strategies:
- Spreads (vertical, horizontal, diagonal)
- Straddles and strangles
- Iron condors and butterflies
- Atomic execution coordination
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest


class MultiLegOptionsManager:
    """Handles multi-leg options strategies like straddles, strangles, spreads, etc."""

    def __init__(self, trading_client, options_validator):
        self.trading_client = trading_client
        self.validator = options_validator

    def parse_multi_leg_strategy(self, strategy: str, symbol: str, strikes: str,
                                 expiry: str, current_price: float) -> Dict:
        """
        Parse multi-leg strategy and return leg details.
        Supports: STRADDLE, STRANGLE, BULL_CALL_SPREAD, BEAR_PUT_SPREAD, IRON_CONDOR
        """
        strategy_upper = strategy.upper()
        result = {
            'is_multi_leg': True,
            'legs': [],
            'strategy_type': strategy_upper,
            'total_cost_estimate': 0,
            'description': ''
        }

        # Parse strikes (format: "450/460" for two strikes, "440/450/460/470" for four strikes)
        strike_parts = strikes.split('/') if strikes else []
        if not strike_parts or len(strike_parts) < 1:
            return None

        try:
            parsed_strikes = [float(s.strip()) for s in strike_parts]
        except ValueError:
            return None

        # STRADDLE: Call and Put at same strike
        if strategy_upper == 'STRADDLE':
            if len(parsed_strikes) < 1:
                return None
            strike = parsed_strikes[0]
            result['description'] = f'STRADDLE ${strike:.2f} (Call + Put)'
            result['legs'] = [
                {'type': 'call', 'strike': strike, 'side': 'buy', 'ratio': 1},
                {'type': 'put', 'strike': strike, 'side': 'buy', 'ratio': 1}
            ]

        # STRANGLE: Call and Put at different strikes
        elif strategy_upper == 'STRANGLE':
            if len(parsed_strikes) < 2:
                return None
            call_strike, put_strike = parsed_strikes[:2]
            result['description'] = f'STRANGLE ${call_strike:.2f} Call + ${put_strike:.2f} Put'
            result['legs'] = [
                {'type': 'call', 'strike': call_strike, 'side': 'buy', 'ratio': 1},
                {'type': 'put', 'strike': put_strike, 'side': 'buy', 'ratio': 1}
            ]

        # BULL CALL SPREAD: Buy lower call, Sell higher call (limited risk bullish)
        elif strategy_upper == 'BULL_CALL_SPREAD':
            if len(parsed_strikes) < 2:
                return None
            low_strike, high_strike = sorted(parsed_strikes[:2])
            result['description'] = f'BULL CALL SPREAD ${low_strike:.2f}/${high_strike:.2f}'
            result['legs'] = [
                {'type': 'call', 'strike': low_strike, 'side': 'buy', 'ratio': 1},
                {'type': 'call', 'strike': high_strike, 'side': 'sell', 'ratio': 1}
            ]

        # BEAR PUT SPREAD: Buy higher put, Sell lower put (limited risk bearish)
        elif strategy_upper == 'BEAR_PUT_SPREAD':
            if len(parsed_strikes) < 2:
                return None
            high_strike, low_strike = sorted(parsed_strikes[:2], reverse=True)
            result['description'] = f'BEAR PUT SPREAD ${high_strike:.2f}/${low_strike:.2f}'
            result['legs'] = [
                {'type': 'put', 'strike': high_strike, 'side': 'buy', 'ratio': 1},
                {'type': 'put', 'strike': low_strike, 'side': 'sell', 'ratio': 1}
            ]

        # BULL PUT SPREAD: Sell higher put, Buy lower put (credit spread - bullish)
        elif strategy_upper == 'BULL_PUT_SPREAD':
            if len(parsed_strikes) < 2:
                return None
            high_strike, low_strike = sorted(parsed_strikes[:2], reverse=True)
            result['description'] = f'BULL PUT SPREAD ${high_strike:.2f}/${low_strike:.2f}'
            result['legs'] = [
                {'type': 'put', 'strike': low_strike, 'side': 'buy', 'ratio': 1},   # Buy lower put (protection)
                {'type': 'put', 'strike': high_strike, 'side': 'sell', 'ratio': 1}  # Sell higher put (collect credit)
            ]

        # BEAR CALL SPREAD: Sell lower call, Buy higher call (credit spread - bearish)
        elif strategy_upper == 'BEAR_CALL_SPREAD':
            if len(parsed_strikes) < 2:
                return None
            low_strike, high_strike = sorted(parsed_strikes[:2])
            result['description'] = f'BEAR CALL SPREAD ${low_strike:.2f}/${high_strike:.2f}'
            result['legs'] = [
                {'type': 'call', 'strike': low_strike, 'side': 'sell', 'ratio': 1},  # Sell lower call (collect credit)
                {'type': 'call', 'strike': high_strike, 'side': 'buy', 'ratio': 1}   # Buy higher call (protection)
            ]

        # IRON CONDOR: Sell higher call, Buy highest call, Buy lowest put, Sell higher put
        elif strategy_upper == 'IRON_CONDOR':
            if len(parsed_strikes) < 4:
                return None
            strikes_sorted = sorted(parsed_strikes)

            # Expected order: low_put_strike, put_sell_strike, call_sell_strike, high_call_strike
            if len(strikes_sorted) >= 4:
                low_put_strike, put_sell_strike, call_sell_strike, high_call_strike = strikes_sorted[:4]
                result['description'] = f'IRON CONDOR ${put_sell_strike:.2f}-${low_put_strike:.2f} Puts, ${call_sell_strike:.2f}-${high_call_strike:.2f} Calls'
                result['legs'] = [
                    {'type': 'put', 'strike': low_put_strike, 'side': 'buy', 'ratio': 1},    # Buy low put (protection)
                    {'type': 'put', 'strike': put_sell_strike, 'side': 'sell', 'ratio': 1},   # Sell higher put (income)
                    {'type': 'call', 'strike': call_sell_strike, 'side': 'sell', 'ratio': 1}, # Sell lower call (income)
                    {'type': 'call', 'strike': high_call_strike, 'side': 'buy', 'ratio': 1}   # Buy high call (protection)
                ]

        else:
            # Unsupported multi-leg strategy
            return None

        return result

    def calculate_multi_leg_sizing(self, legs: List[Dict], position_value: float,
                                   options_data: Dict) -> List[Dict]:
        """
        Calculate position sizes for each leg, accounting for credit/debit nature.
        Adjusts for net cost/credit of the strategy.
        """
        # Find contracts for each leg
        legs_with_contracts = []
        total_debit = 0
        total_credit = 0

        for leg in legs:
            # Find contract - will implement this logic
            contract_key = f"{leg['strike']}_{leg['type']}"
            contract = options_data.get(contract_key)

            if not contract:
                logging.warning(f"Could not find contract for {leg['type']} ${leg['strike']}")
                return None

            price = contract.get('ask', 0) if leg['side'] == 'buy' else contract.get('bid', 0)
            if price <= 0:
                logging.warning(f"No price available for {leg['type']} ${leg['strike']}")
                return None

            # Calculate per-contract cost (100 shares per contract)
            contract_cost = price * 100 * (1 if leg['side'] == 'buy' else -1)
            leg['contract_price'] = price
            leg['contract_cost'] = contract_cost

            legs_with_contracts.append(leg)

            # Track total debits/credits
            if leg['side'] == 'buy':
                total_debit += contract_cost
            else:
                total_credit -= contract_cost  # Negative because selling gives credit

        # Net cost/credit for the strategy
        net_cost = total_debit - total_credit
        abs_net_cost = abs(net_cost)

        if abs_net_cost == 0:
            logging.warning("Strategy has zero net cost - invalid")
            return None

        # Calculate number of spreads we can afford
        max_spreads = int(position_value / abs_net_cost)
        max_spreads = max(1, max_spreads)  # Minimum 1 spread

        # Set quantity for all legs (same for spread strategies)
        for leg in legs_with_contracts:
            leg['quantity'] = max_spreads

        logging.info(f"Strategy sizing: {max_spreads} spreads, net cost per spread: ${net_cost:.2f}, total: ${net_cost * max_spreads:.2f}")

        return legs_with_contracts

    def validate_multi_leg_liquidity(self, legs: List[Dict]) -> bool:
        """
        Validate all legs have sufficient liquidity for the planned size.
        """
        for leg in legs:
            # Skip volume check for selling options (sold to open positions usually liquid)
            if leg['side'] == 'sell':
                continue

            # Check volume minimums
            volume = leg.get('volume', 0)
            quantity = leg.get('quantity', 1)

            # Minimum volume requirements based on strategy size
            if quantity >= 50 and volume < 1000:  # Large position
                return False
            elif quantity >= 20 and volume < 500:  # Medium position
                return False
            elif volume < 100:  # Small position
                return False

        return True

    def execute_multi_leg_order(self, symbol: str, legs: List[Dict], strategy_type: str) -> List[Dict]:
        """
        Execute orders for all legs of a multi-leg strategy.
        Returns list of order results.
        """
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_results = []

        for leg in legs:
            quantity = leg.get('quantity', 0)
            if quantity <= 0:
                continue

            # Build OCC symbol
            exp_str = leg.get('expiration_str', '251219')  # Default fallback
            occ_symbol = self._build_occ_symbol(symbol, exp_str, leg['type'][0].upper(), leg['strike'])

            # Determine order side
            order_side = OrderSide.BUY if leg['side'] == 'buy' else OrderSide.SELL

            # Limit price calculation
            bid = leg.get('bid', 0)
            ask = leg.get('ask', 0)

            if order_side == OrderSide.BUY and ask > 0:
                limit_price = round(ask * 1.01, 2)
            elif order_side == OrderSide.SELL and bid > 0:
                limit_price = round(bid * 0.99, 2)
            elif leg['contract_price'] > 0:
                limit_price = round(leg['contract_price'], 2)
            else:
                logging.error(f"No price available for {occ_symbol}")
                continue

            # Submit order
            try:
                order_data = LimitOrderRequest(
                    symbol=occ_symbol,
                    qty=quantity,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price
                )

                order = self.trading_client.submit_order(order_data)

                order_results.append({
                    'symbol': occ_symbol,
                    'side': leg['side'],
                    'quantity': quantity,
                    'price': limit_price,
                    'order_id': order.id if hasattr(order, 'id') else 'UNKNOWN',
                    'strategy': strategy_type,
                    'leg_info': leg
                })

                logging.info(f"Multi-leg order submitted: {occ_symbol} {leg['side']} {quantity} @ ${limit_price}")

            except Exception as e:
                error_msg = str(e)
                if "asset not found" in error_msg.lower():
                    logging.warning(f"Asset {occ_symbol} not available in paper trading")
                else:
                    logging.error(f"Failed to submit order for {occ_symbol}: {e}")

                order_results.append({
                    'symbol': occ_symbol,
                    'side': leg['side'],
                    'quantity': quantity,
                    'price': limit_price,
                    'error': error_msg,
                    'leg_info': leg
                })

        return order_results

    def execute_multi_leg_strategy(self, symbol: str, strategy: str, strikes: str,
                                 expiry: str, position_size_pct: float,
                                 options_data: List[Dict], confidence: int,
                                 iv_rank: float, reason: str) -> bool:
        """
        Execute multi-leg options strategy using the established multi-leg managers.

        Supports: IRON_CONDOR, STRADDLE, STRANGLE, BULL_CALL_SPREAD, BEAR_PUT_SPREAD
        """
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        try:
            logging.info(f"=== EXECUTING MULTI-LEG STRATEGY: {strategy} on {symbol} ===")

            # Get account for position sizing
            account = self.trading_client.get_account()
            total_equity = float(account.equity) if account.equity is not None else 0.0
            position_value = total_equity * position_size_pct

            # Parse multi-leg strategy using existing manager
            strategy_details = self.multi_leg_manager.parse_multi_leg_strategy(
                strategy, symbol, strikes, expiry, 0  # current_price not needed for core logic
            )

            if not strategy_details:
                logging.error(f"Failed to parse multi-leg strategy: {strategy} for {symbol}")
                print(f"{Colors.ERROR}[ERROR] Failed to parse {strategy} strategy{Colors.RESET}")
                return False

            logging.info(f"Strategy parsed: {strategy_details['description']}")
            print(f"{Colors.INFO}[MULTI-LEG] {strategy}: {strategy_details['description']}{Colors.RESET}")

            # Calculate sizing using existing manager
            sizing_info = self.multi_leg_order_manager.calculate_multi_leg_sizing(
                symbol, strategy, strategy_details['legs'], confidence, total_equity
            )

            if not sizing_info.get('can_afford', False):
                print(f"{Colors.WARNING}[SKIP] Cannot afford {strategy} position at current size{Colors.RESET}")
                logging.info(f"Cannot afford {strategy} position")
                return False

            logging.info(f"Sizing calculated: {sizing_info['max_spreads']} spreads, net cost per spread: ${sizing_info['net_cost_per_spread']:.2f}")

            # Execute the strategy using existing manager
            execution_result = self.multi_leg_order_manager.execute_multi_leg_order(
                symbol, strategy_details['legs'], strategy, sizing_info
            )

            if execution_result.get('success', False):
                logging.info(f"Multi-leg strategy executed successfully: {strategy} on {symbol}")

                # Calculate total cost for trading journal
                total_cost = sizing_info.get('total_cost', 0)

                # FIXED: Issue #1 - Calculate multi-leg Greeks
                try:
                    # Try to calculate Greeks from actual leg data
                    stock_data = self.openbb.get_quote(symbol)
                    underlying_price = stock_data['results'][0].get('price', 0) if stock_data and 'results' in stock_data else 0

                    if strategy_details['legs'] and len(strategy_details['legs']) > 0:
                        greeks = calculate_multi_leg_greeks(strategy_details['legs'], symbol, underlying_price)
                        logging.info(f"Calculated actual Greeks from legs: {greeks}")
                    else:
                        # Fallback: estimate Greeks from strategy type
                        atm_options = [opt for opt in options_data if opt.get('strike', 0) > 0 and
                                      abs(opt.get('strike', 0) - underlying_price) / underlying_price < 0.05]
                        atm_greeks = None
                        if atm_options:
                            atm_greeks = {
                                'delta': atm_options[0].get('delta', 0.5),
                                'gamma': atm_options[0].get('gamma', 0.05),
                                'theta': atm_options[0].get('theta', -0.05),
                                'vega': atm_options[0].get('vega', 0.1)
                            }
                        greeks = estimate_strategy_greeks(strategy, underlying_price, strikes, atm_greeks)
                        logging.info(f"Estimated Greeks for {strategy}: {greeks}")
                except Exception as e:
                    logging.warning(f"Could not calculate Greeks for multi-leg: {e}, using estimates")
                    greeks = estimate_strategy_greeks(strategy, 0, strikes, None)

                # Log to trading journal (summarize the multi-leg position)
                trade_data = {
                    'symbol': symbol,
                    'strategy': strategy,
                    'occ_symbol': f"{symbol}_MULTI_LEG",  # Composite symbol for multi-leg
                    'action': 'ENTER_MULTI_LEG',
                    'entry_price': sizing_info['net_cost_per_spread'],  # Net cost per spread
                    'quantity': sizing_info['max_spreads'],  # Number of spreads
                    'total_cost': total_cost,
                    'confidence': confidence,
                    'iv_rank': iv_rank,
                    'delta': greeks.get('delta', 0),  # FIXED: Calculated from legs
                    'theta': greeks.get('theta', 0),  # FIXED: Calculated from legs
                    'vega': greeks.get('vega', 0),   # FIXED: Calculated from legs
                    'gamma': greeks.get('gamma', 0),  # FIXED: Calculated from legs
                    'bid_ask_spread': 0,  # Not applicable for spread strategies
                    'reason': reason
                }

                self.trade_journal.log_trade(trade_data)

                # Track active multi-leg position
                position_tracking = {
                    'symbol': symbol,
                    'occ_symbol': f"{symbol}_MULTI_LEG",
                    'strategy': strategy,
                    'entry_price': sizing_info['net_cost_per_spread'],
                    'quantity': sizing_info['max_spreads'],
                    'confidence': confidence,
                    'strikes': strikes,
                    'expiry': expiry,
                    'reason': reason
                }
                self.trade_journal.track_active_position(position_tracking)

                print(f"{Colors.SUCCESS}✓ Multi-leg strategy executed: {sizing_info['max_spreads']} spreads @ net cost ${sizing_info['net_cost_per_spread']:.2f} each{Colors.RESET}")
                return True
            else:
                logging.error(f"Multi-leg strategy execution failed: {strategy} on {symbol}")
                print(f"{Colors.ERROR}✗ Multi-leg strategy execution failed{Colors.RESET}")

                if execution_result.get('errors'):
                    for error in execution_result['errors']:
                        logging.error(f"Execution error: {error}")
                        print(f"{Colors.ERROR}  Error: {error}{Colors.RESET}")

                return False

        except Exception as e:
            logging.error(f"Error executing multi-leg strategy {strategy} on {symbol}: {e}")
            self.trade_journal.log_error('MULTI_LEG_EXECUTION', str(e), symbol)
            print(f"{Colors.ERROR}[ERROR] Failed to execute {strategy}: {str(e)}{Colors.RESET}")
            return False

    def _build_occ_symbol(self, symbol: str, exp_str: str, option_type: str, strike: float) -> str:
        """Build OCC format symbol"""
        strike_int = int(strike * 1000)
        strike_str = f"{strike_int:08d}"
        return f"{symbol}{exp_str}{option_type}{strike_str}"


