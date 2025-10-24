"""
Position Manager - Individual Position Tracking and Management

Manages individual option positions:
- Position entry and exit tracking
- Stop loss and profit target management
- Position Greeks monitoring
- Exit signal generation
- Trade journal integration
"""

import logging
import asyncio
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from colorama import Fore, Style
from src.core.colors import Colors


# Helper function for extracting underlying symbol from OCC format
def extract_underlying_symbol(full_symbol: str) -> str:
    """Extract underlying stock symbol from OCC format or return as-is for stocks"""
    if len(full_symbol) > 15:  # OCC options format: TICKER + YYMMDD + C/P + STRIKE (15 chars)
        return full_symbol[:-15]
    return full_symbol


class PositionManager:
    """Manages position exits with stop losses and profit targets"""

    def __init__(self, trading_client, trade_journal):
        self.trading_client = trading_client
        self.journal = trade_journal

        # FIXED: Issue #6 - Strategy-specific stop losses
        # Exit rules (defaults)
        self.STOP_LOSS_PCT = -0.30  # Default -30% stop loss
        self.PROFIT_TARGET_PCT = 0.50  # +50% profit target
        self.MIN_DTE_EXIT = 5  # Close positions < 5 DTE
        self.TRAILING_STOP_PCT = 0.20  # 20% trailing stop after hitting profit target

        # EXPERT TRADER RULE: For debit spreads, profit target is 50% of MAX GAIN, not 50% of debit paid
        # Max gain = spread width - debit paid
        # So 50% of max gain is achieved when: (current_value - debit) / debit = 0.50 * (width - debit) / debit
        # For credit spreads, profit target is 50% of credit collected (e.g., $1.00 credit → take profit at $0.50 remaining value)
        self.SPREAD_PROFIT_TARGET_MULTIPLIER = 0.50  # Take profit at 50% of max gain

        # Strategy-specific stop losses (EXPERT TRADER RULES)
        self.STRATEGY_STOP_LOSSES = {
            'LONG_CALL': -0.25,          # 25% for long premium (can lose 100%)
            'LONG_PUT': -0.25,           # 25% for long premium
            'SHORT_CALL': -0.50,         # 50% for short (undefined risk)
            'SHORT_PUT': -0.50,          # 50% for short (undefined risk)
            'BULL_CALL_SPREAD': -0.75,   # EXPERT RULE: 75% of debit paid for debit spreads
            'BEAR_PUT_SPREAD': -0.75,    # EXPERT RULE: 75% of debit paid for debit spreads
            'BULL_PUT_SPREAD': -0.75,    # EXPERT RULE: 75% for credit spreads (based on collected credit)
            'BEAR_CALL_SPREAD': -0.75,   # EXPERT RULE: 75% for credit spreads
            'IRON_CONDOR': -0.50,        # 50% of max loss (at strike width)
            'IRON_BUTTERFLY': -0.50,     # 50% of max loss
            'LONG_STRADDLE': -0.30,      # 30% for straddles
            'LONG_STRANGLE': -0.30,      # 30% for strangles
            'STRADDLE': -0.30,           # Generic straddle (assume long)
            'STRANGLE': -0.30,           # Generic strangle (assume long)
            'SHORT_STRADDLE': -0.40,     # 40% for short straddles
            'SHORT_STRANGLE': -0.40,     # 40% for short strangles
            'BUTTERFLY_SPREAD': -0.35,   # 35% for butterflies
            'COVERED_CALL': -0.15,       # 15% (mostly stock risk)
            'PROTECTIVE_PUT': -0.20,     # 20% (hedged position)
            'COLLAR': -0.20,             # 20% (hedged position)
        }

        # Strategy-specific DTE exits
        self.STRATEGY_DTE_EXITS = {
            'LONG_CALL': 7,        # Exit long premium by 7 DTE (gamma risk)
            'LONG_PUT': 7,         # Exit long premium by 7 DTE
            'SHORT_CALL': 3,       # Can hold short to 3 DTE
            'SHORT_PUT': 3,        # Can hold short to 3 DTE
            'BULL_CALL_SPREAD': 5, # Spreads exit at 5 DTE
            'BEAR_PUT_SPREAD': 5,
            'BULL_PUT_SPREAD': 5,
            'BEAR_CALL_SPREAD': 5,
            'IRON_CONDOR': 7,      # IC exit early (pin risk)
            'IRON_BUTTERFLY': 7,
            'LONG_STRADDLE': 7,    # Exit before gamma acceleration
            'LONG_STRANGLE': 7,
            'STRADDLE': 7,         # Generic straddle (assume long)
            'STRANGLE': 7,         # Generic strangle (assume long)
            'SHORT_STRADDLE': 5,
            'SHORT_STRANGLE': 5,
            'BUTTERFLY_SPREAD': 5,
            'COVERED_CALL': 3,
            'PROTECTIVE_PUT': 7,
            'COLLAR': 5,
        }

        # Emergency exit at 2 DTE (ALL positions)
        self.EMERGENCY_EXIT_DTE = 2

        # Track position highs for trailing stops
        self.position_highs = {}

        # ANTI-OVERTRADING: Track recently closed positions to prevent revenge trading
        self.recently_closed = {}  # {symbol: {'timestamp': float, 'pnl': float, 'strategy': str}}
        self.COOLDOWN_PERIOD_SECONDS = 3600  # 1 hour cooldown after closing position
        self.MAX_SAME_SYMBOL_LOSSES = 2  # Max 2 losses on same symbol per day
        self.daily_loss_count = {}  # {symbol: count} - resets daily

        # VOLATILITY PLAY PROTECTION: Minimum hold time for straddles/strangles
        self.position_entry_times = {}  # {position_id: timestamp}
        self.MIN_HOLD_MINUTES = {
            'STRADDLE': 120,       # Hold straddles at least 2 hours
            'STRANGLE': 120,       # Hold strangles at least 2 hours
            'LONG_STRADDLE': 120,
            'LONG_STRANGLE': 120,
            'SHORT_STRADDLE': 60,  # Short vol can exit faster
            'SHORT_STRANGLE': 60,
            'IRON_CONDOR': 60,
            'IRON_BUTTERFLY': 60,
        }

    def can_trade_symbol(self, symbol: str, strategy: str = '') -> Tuple[bool, str]:
        """
        Check if symbol can be traded (anti-overtrading protection).

        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        # Check cooling-off period
        if symbol in self.recently_closed:
            closed_info = self.recently_closed[symbol]
            time_since_close = time.time() - closed_info['timestamp']

            if time_since_close < self.COOLDOWN_PERIOD_SECONDS:
                minutes_remaining = (self.COOLDOWN_PERIOD_SECONDS - time_since_close) / 60
                return (False, f"COOLDOWN: {symbol} recently closed {minutes_remaining:.0f}min ago (wait {self.COOLDOWN_PERIOD_SECONDS/60:.0f}min)")

            # Cooldown expired, remove from tracking
            del self.recently_closed[symbol]

        # Check consecutive loss limit
        if symbol in self.daily_loss_count:
            loss_count = self.daily_loss_count[symbol]
            if loss_count >= self.MAX_SAME_SYMBOL_LOSSES:
                return (False, f"MAX_LOSSES: {symbol} has {loss_count} consecutive losses today (max {self.MAX_SAME_SYMBOL_LOSSES})")

        return (True, "OK")

    def record_position_entry(self, position_id: str, strategy: str):
        """
        Record when a position was entered (for minimum hold time tracking).

        Args:
            position_id: Unique position identifier
            strategy: Strategy name
        """
        self.position_entry_times[position_id] = time.time()
        logging.debug(f"Position entry recorded: {position_id} ({strategy})")

    def check_and_execute_exits(self):
        """Check all positions and execute exits if needed"""
        try:
            positions = self.trading_client.get_all_positions()

            if not positions:
                return

            print(f"\n{Colors.INFO}[POSITION CHECK] Monitoring {len(positions)} open positions...{Colors.RESET}")
            logging.info(f"=== POSITION CHECK: {len(positions)} open positions ===")

            # Track which positions we've already processed as part of multi-leg strategies
            processed_symbols = set()

            for position in positions:
                # Skip if already processed as part of a multi-leg strategy
                if position.symbol in processed_symbols:
                    continue

                avg_entry = float(position.avg_entry_price) if position.avg_entry_price is not None else 0.0
                current = float(position.current_price) if position.current_price is not None else 0.0
                pl = float(position.unrealized_pl) if position.unrealized_pl is not None else 0.0
                plpc = float(position.unrealized_plpc) if position.unrealized_plpc is not None else 0.0
                logging.info(f"Position: {position.symbol} | Qty: {position.qty} | Entry: ${avg_entry:.2f} | Current: ${current:.2f} | P&L: ${pl:,.2f} ({plpc:+.1%})")

                # Check if this is part of a multi-leg strategy
                underlying = extract_underlying_symbol(position.symbol)
                strategy_info = self.journal.get_position_strategy(underlying)

                if strategy_info:
                    strategy = strategy_info.get('strategy', 'UNKNOWN')
                    # Multi-leg strategies that need atomic closure
                    MULTI_LEG_STRATEGIES = ['STRADDLE', 'STRANGLE', 'BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD',
                                          'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD', 'IRON_CONDOR',
                                          'IRON_BUTTERFLY', 'BUTTERFLY_SPREAD']

                    if strategy in MULTI_LEG_STRATEGIES:
                        # Find all legs of this strategy
                        strategy_legs = [p for p in positions if extract_underlying_symbol(p.symbol) == underlying]

                        # Check exit criteria for the multi-leg strategy as a whole
                        exit_info = self._check_multi_leg_exit(strategy_legs, strategy_info)

                        if exit_info['should_exit']:
                            # Mark all legs as processed
                            for leg in strategy_legs:
                                processed_symbols.add(leg.symbol)

                            # Execute multi-leg exit
                            strategy = strategy_info.get('strategy', 'UNKNOWN')
                            self._execute_multi_leg_exit(strategy_legs, underlying, exit_info['reason'], strategy)
                        else:
                            # Still mark as processed to avoid duplicate checks
                            for leg in strategy_legs:
                                processed_symbols.add(leg.symbol)
                    else:
                        # Single-leg strategy - use normal exit logic
                        self._check_position_exit(position)
                else:
                    # No strategy info - treat as single position
                    self._check_position_exit(position)

        except Exception as e:
            logging.error(f"Error checking exits: {e}")

    def _check_position_exit(self, position):
        """Check individual position for exit conditions"""
        try:
            symbol = position.symbol
            qty = int(position.qty) if position.qty is not None else 0
            current_price = float(position.current_price) if position.current_price is not None else 0.0
            avg_entry = float(position.avg_entry_price) if position.avg_entry_price is not None else 0.0
            unrealized_pl = float(position.unrealized_pl) if position.unrealized_pl is not None else 0.0
            unrealized_pl_pct = float(position.unrealized_plpc) if position.unrealized_plpc is not None else 0.0
            market_value = float(position.market_value) if position.market_value is not None else 0.0

            # Get days to expiration (if option)
            dte = self._get_days_to_expiration(symbol)

            # Track position high for trailing stop
            if symbol not in self.position_highs:
                self.position_highs[symbol] = unrealized_pl_pct
            else:
                self.position_highs[symbol] = max(self.position_highs[symbol], unrealized_pl_pct)

            # FIXED: Issue #6 - Get strategy-specific stop loss
            strategy = self._get_position_strategy(symbol)
            stop_loss_pct = self.STRATEGY_STOP_LOSSES.get(strategy, self.STOP_LOSS_PCT)
            dte_exit_threshold = self.STRATEGY_DTE_EXITS.get(strategy, self.MIN_DTE_EXIT)

            # FIXED: Issue #10 - Gamma-based adjustments for high-gamma positions near expiration
            gamma_adjusted_stop = stop_loss_pct
            gamma_adjusted_dte = dte_exit_threshold

            if dte is not None and dte < 7:
                # High gamma risk in final 7 DTE - tighten stops by 30%
                gamma_adjusted_stop = stop_loss_pct * 0.7
                gamma_adjusted_dte = max(dte_exit_threshold - 2, 2)  # Exit 2 days earlier
                logging.debug(f"{symbol} gamma adjustment: stop {stop_loss_pct:.1%} → {gamma_adjusted_stop:.1%}, DTE {dte_exit_threshold} → {gamma_adjusted_dte}")

            # Use adjusted values
            stop_loss_pct = gamma_adjusted_stop
            dte_exit_threshold = gamma_adjusted_dte

            logging.debug(f"{symbol} strategy: {strategy}, stop loss: {stop_loss_pct:.1%}, DTE threshold: {dte_exit_threshold}")

            exit_reason = None

            # 0. EMERGENCY EXIT (2 DTE for all positions)
            if dte is not None and dte <= self.EMERGENCY_EXIT_DTE:
                exit_reason = f"EMERGENCY_EXIT ({dte} DTE - gamma risk!)"

            # 1. STRATEGY-SPECIFIC STOP LOSS
            elif unrealized_pl_pct <= stop_loss_pct:
                exit_reason = f"STOP_LOSS ({unrealized_pl_pct:.1%}, strategy: {strategy})"

            # 2. PROFIT TARGET
            elif unrealized_pl_pct >= self.PROFIT_TARGET_PCT:
                exit_reason = f"PROFIT_TARGET ({unrealized_pl_pct:.1%})"

            # 3. FIXED: Issue #13 - TIERED TRAILING STOP (activates from +15%, not just +50%)
            elif self.position_highs[symbol] >= 0.15:  # Activate after +15% gain
                # Tiered trailing stop based on high water mark
                drawdown_from_high = unrealized_pl_pct - self.position_highs[symbol]

                if self.position_highs[symbol] >= 0.50:
                    # After +50%, trail by 20%
                    trailing_stop = -0.20
                elif self.position_highs[symbol] >= 0.30:
                    # After +30%, trail by 25%
                    trailing_stop = -0.25
                else:  # >= 0.15
                    # After +15%, trail by 30%
                    trailing_stop = -0.30

                if drawdown_from_high <= trailing_stop:
                    exit_reason = f"TRAILING_STOP (high: {self.position_highs[symbol]:.1%}, now: {unrealized_pl_pct:.1%}, tier: {trailing_stop:.0%})"

            # 4. STRATEGY-SPECIFIC TIME EXIT (approaching expiration)
            elif dte is not None and dte < dte_exit_threshold:
                exit_reason = f"EXPIRATION_NEAR ({dte} DTE, threshold: {dte_exit_threshold} for {strategy})"

            # Execute exit if needed
            if exit_reason:
                print(f"{Colors.WARNING}[EXIT] {symbol}: {exit_reason}{Colors.RESET}")
                print(f"  Entry: ${avg_entry:.2f} | Current: ${current_price:.2f} | P&L: ${unrealized_pl:,.2f} ({unrealized_pl_pct:+.1%})")
                logging.info(f"*** EXIT TRIGGERED: {symbol} - {exit_reason} | Entry: ${avg_entry:.2f} | Current: ${current_price:.2f} | P&L: ${unrealized_pl:,.2f} ({unrealized_pl_pct:+.1%})")

                success = self._execute_exit(position, exit_reason)

                if success:
                    print(f"{Colors.SUCCESS}  ✓ Exit order submitted{Colors.RESET}")
                    logging.info(f"✓ Exit order submitted successfully for {symbol}")

                    # Log to database
                    self._log_exit_to_db(symbol, current_price, exit_reason, unrealized_pl, unrealized_pl_pct)

                    # Clean up position high tracking
                    if symbol in self.position_highs:
                        del self.position_highs[symbol]
                else:
                    print(f"{Colors.ERROR}  ✗ Exit order failed{Colors.RESET}")
                    logging.error(f"✗ Exit order FAILED for {symbol}")

        except Exception as e:
            logging.error(f"Error checking position {position.symbol}: {e}")

    def _check_multi_leg_exit(self, legs: List, strategy_info: Dict) -> Dict:
        """
        Check exit criteria for multi-leg strategy based on combined P&L and risk metrics.
        Returns dict with 'should_exit' (bool) and 'reason' (str).
        """
        try:
            # Calculate aggregate metrics across all legs
            total_pl = sum(float(leg.unrealized_pl) if leg.unrealized_pl else 0 for leg in legs)
            total_cost = sum(abs(float(leg.cost_basis)) if leg.cost_basis else 0 for leg in legs)
            total_market_value = sum(float(leg.market_value) if leg.market_value else 0 for leg in legs)

            # Calculate aggregate P&L percentage
            if total_cost > 0:
                aggregate_pl_pct = total_pl / total_cost
            else:
                aggregate_pl_pct = 0.0

            strategy = strategy_info.get('strategy', 'UNKNOWN')
            underlying = extract_underlying_symbol(legs[0].symbol if legs else '')

            # Get DTE from first leg (all legs should have same expiration)
            dte = self._get_days_to_expiration(legs[0].symbol) if legs else None

            # Get strategy-specific thresholds
            stop_loss_pct = self.STRATEGY_STOP_LOSSES.get(strategy, self.STOP_LOSS_PCT)
            dte_exit_threshold = self.STRATEGY_DTE_EXITS.get(strategy, self.MIN_DTE_EXIT)

            # Gamma adjustment for near-expiration positions
            if dte is not None and dte < 7:
                stop_loss_pct = stop_loss_pct * 0.7
                dte_exit_threshold = max(dte_exit_threshold - 2, 2)

            # Track high water mark for trailing stop
            tracking_key = f"{underlying}_{strategy}"
            if tracking_key not in self.position_highs:
                self.position_highs[tracking_key] = aggregate_pl_pct
            else:
                self.position_highs[tracking_key] = max(self.position_highs[tracking_key], aggregate_pl_pct)

            logging.debug(f"Multi-leg {strategy} {underlying}: P&L ${total_pl:,.2f} ({aggregate_pl_pct:+.1%}), DTE: {dte}")

            # Check exit conditions
            # -1. MINIMUM HOLD TIME (prevent premature exits on volatility plays)
            if strategy in self.MIN_HOLD_MINUTES:
                # Try to get entry time from first leg
                position_id = legs[0].asset_id if legs and hasattr(legs[0], 'asset_id') else None
                if position_id and position_id in self.position_entry_times:
                    entry_time = self.position_entry_times[position_id]
                    minutes_held = (time.time() - entry_time) / 60
                    min_hold_required = self.MIN_HOLD_MINUTES[strategy]

                    if minutes_held < min_hold_required:
                        # Still allow emergency exits and large stop losses
                        if dte is not None and dte <= self.EMERGENCY_EXIT_DTE:
                            pass  # Allow emergency exit
                        elif aggregate_pl_pct <= (stop_loss_pct * 1.5):  # 1.5x stop loss
                            pass  # Allow catastrophic loss exit
                        else:
                            logging.info(f"{underlying} {strategy}: Min hold time not met ({minutes_held:.0f}/{min_hold_required} min)")
                            return {'should_exit': False, 'reason': f'MIN_HOLD_TIME ({minutes_held:.0f}/{min_hold_required} min)'}

            # 0. EMERGENCY EXIT (2 DTE)
            if dte is not None and dte <= self.EMERGENCY_EXIT_DTE:
                return {'should_exit': True, 'reason': f"EMERGENCY_EXIT ({dte} DTE - gamma risk!)"}

            # 1. STRATEGY-SPECIFIC STOP LOSS
            if aggregate_pl_pct <= stop_loss_pct:
                return {'should_exit': True, 'reason': f"STOP_LOSS ({aggregate_pl_pct:.1%}, strategy: {strategy})"}

            # 2. PROFIT TARGET
            if aggregate_pl_pct >= self.PROFIT_TARGET_PCT:
                return {'should_exit': True, 'reason': f"PROFIT_TARGET ({aggregate_pl_pct:.1%})"}

            # 3. TRAILING STOP (activates from +15%)
            if self.position_highs[tracking_key] >= 0.15:
                drawdown_from_high = aggregate_pl_pct - self.position_highs[tracking_key]

                if self.position_highs[tracking_key] >= 0.50:
                    trailing_stop = -0.20
                elif self.position_highs[tracking_key] >= 0.30:
                    trailing_stop = -0.25
                else:
                    trailing_stop = -0.30

                if drawdown_from_high <= trailing_stop:
                    return {'should_exit': True,
                           'reason': f"TRAILING_STOP (high: {self.position_highs[tracking_key]:.1%}, now: {aggregate_pl_pct:.1%})"}

            # 4. STRATEGY-SPECIFIC TIME EXIT
            if dte is not None and dte < dte_exit_threshold:
                return {'should_exit': True, 'reason': f"EXPIRATION_NEAR ({dte} DTE, threshold: {dte_exit_threshold})"}

            return {'should_exit': False, 'reason': ''}

        except Exception as e:
            logging.error(f"Error checking multi-leg exit: {e}")
            return {'should_exit': False, 'reason': ''}

    def _execute_multi_leg_exit(self, legs: List, underlying: str, reason: str, strategy: str = 'UNKNOWN') -> bool:
        """
        Execute atomic multi-leg exit using Alpaca's multi-leg order API.
        Closes all legs of a strategy in a single atomic order to prevent orphaned positions.

        Args:
            legs: List of position legs to close
            underlying: Underlying symbol
            reason: Exit reason
            strategy: Strategy name (for tracking)
        """
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent, OrderClass

        try:
            if not legs:
                logging.warning("No legs provided to _execute_multi_leg_exit")
                return False

            logging.info(f"=== EXECUTING MULTI-LEG EXIT: {underlying} - {reason} ===")
            print(f"{Colors.WARNING}[MULTI-LEG EXIT] {underlying}: {reason}{Colors.RESET}")

            # Build option legs for the closing order
            option_legs = []
            total_pl = 0.0
            total_pl_pct = 0.0
            leg_count = 0

            for leg in legs:
                symbol = leg.symbol
                qty = abs(int(leg.qty)) if leg.qty else 0
                current_price = float(leg.current_price) if leg.current_price else 0.0
                unrealized_pl = float(leg.unrealized_pl) if leg.unrealized_pl else 0.0
                unrealized_pl_pct = float(leg.unrealized_plpc) if leg.unrealized_plpc else 0.0

                if qty == 0:
                    logging.warning(f"Skipping leg {symbol} with qty 0")
                    continue

                # Determine closing side (opposite of current position)
                if int(leg.qty) > 0:
                    # Long position - close by selling
                    close_side = OrderSide.SELL
                    position_intent = PositionIntent.SELL_TO_CLOSE
                else:
                    # Short position - close by buying
                    close_side = OrderSide.BUY
                    position_intent = PositionIntent.BUY_TO_CLOSE

                # For worthless positions, try to close via close_position instead
                if current_price <= 0.01:
                    logging.warning(f"Leg {symbol} near worthless (${current_price:.2f}), using close_position")
                    try:
                        self.trading_client.close_position(symbol)
                        total_pl += unrealized_pl
                        leg_count += 1
                        continue
                    except Exception as e:
                        logging.warning(f"close_position failed for {symbol}: {e}, will include in multi-leg order")

                # Add leg to multi-leg closing order
                option_leg = OptionLegRequest(
                    symbol=symbol,
                    ratio_qty=1,
                    side=close_side,
                    position_intent=position_intent
                )
                option_legs.append(option_leg)

                total_pl += unrealized_pl
                total_pl_pct += unrealized_pl_pct
                leg_count += 1

                logging.info(f"Leg {leg_count}: {symbol} | Qty: {qty} | Side: {close_side} | P&L: ${unrealized_pl:,.2f} ({unrealized_pl_pct:+.1%})")
                print(f"  Leg {leg_count}: {symbol} | P&L: ${unrealized_pl:,.2f} ({unrealized_pl_pct:+.1%})")

            if not option_legs:
                logging.warning("No valid legs to close in multi-leg order")
                # If all legs were closed individually, still mark as success
                if leg_count > 0:
                    self.journal.remove_active_position(underlying)
                    return True
                return False

            # Calculate limit price for multi-leg exit
            # CRITICAL: For multi-leg orders, limit_price is the NET debit/credit for the entire spread
            # NOT the individual leg prices
            #
            # For closing positions:
            # - Closing a debit spread (bought for $X) → selling back for $Y → net credit = Y
            # - Closing a credit spread (sold for $X) → buying back for $Y → net debit = Y
            #
            # We'll use current market prices to calculate reasonable exit limit

            net_exit_price = 0.0

            for leg in legs:
                current_price = float(leg.current_price) if leg.current_price else 0.0

                # For closing, we reverse the original position
                # Long position (qty > 0) → selling to close → credit
                # Short position (qty < 0) → buying to close → debit
                if int(leg.qty) > 0:
                    # Selling to close - adds credit
                    net_exit_price += current_price
                else:
                    # Buying to close - adds debit
                    net_exit_price -= current_price

            # Use absolute value for the limit price
            # Add 10% buffer to ensure fill (especially important for exits)
            spread_exit_price = abs(net_exit_price) * 1.10

            # Set reasonable bounds
            # Minimum $0.01, maximum $100 per spread
            spread_exit_price = max(0.01, min(spread_exit_price, 100.0))

            logging.info(f"Multi-leg exit: Net spread price ${net_exit_price:.2f}, "
                        f"Limit price ${spread_exit_price:.2f} (with 10% buffer)")

            # Submit multi-leg closing order
            # IMPORTANT: For Alpaca paper trading, all legs must close atomically
            # Using BUY_TO_CLOSE/SELL_TO_CLOSE position intents (already set above)
            order_request = LimitOrderRequest(
                qty=1,  # Each spread counts as 1 unit
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=option_legs,
                limit_price=spread_exit_price
            )

            order = self.trading_client.submit_order(order_request)
            logging.info(f"Multi-leg exit order submitted: Order ID {order.id}")
            print(f"{Colors.SUCCESS}✓ Multi-leg exit order submitted (Order ID: {order.id}){Colors.RESET}")
            print(f"  Total P&L: ${total_pl:,.2f} | {leg_count} legs closed")

            # Remove from active position tracking
            self.journal.remove_active_position(underlying)

            # ANTI-OVERTRADING: Track recently closed position
            self.recently_closed[underlying] = {
                'timestamp': time.time(),
                'pnl': total_pl,
                'strategy': strategy
            }

            # Track consecutive losses for this symbol
            if total_pl < 0:
                self.daily_loss_count[underlying] = self.daily_loss_count.get(underlying, 0) + 1
                logging.warning(f"{underlying}: Consecutive losses: {self.daily_loss_count[underlying]}")
            else:
                # Reset loss counter on profit
                if underlying in self.daily_loss_count:
                    del self.daily_loss_count[underlying]

            # Clean up entry time tracking
            for leg in legs:
                position_id = leg.asset_id if hasattr(leg, 'asset_id') else None
                if position_id and position_id in self.position_entry_times:
                    del self.position_entry_times[position_id]

            # Clean up high water mark tracking
            tracking_key = f"{underlying}_{reason.split()[0]}"
            if tracking_key in self.position_highs:
                del self.position_highs[tracking_key]

            return True

        except Exception as e:
            error_msg = str(e).lower()
            logging.error(f"Error executing multi-leg exit for {underlying}: {e}")
            print(f"{Colors.ERROR}✗ Multi-leg exit failed: {e}{Colors.RESET}")

            # Check if error is related to uncovered options
            # Alpaca paper accounts cannot trade uncovered/naked options
            if 'uncovered' in error_msg or 'naked' in error_msg:
                logging.error(f"CRITICAL: Multi-leg exit rejected - paper account cannot trade uncovered options")
                logging.error(f"This means the multi-leg order is being rejected by Alpaca")
                logging.error(f"Possible causes:")
                logging.error(f"  1. Multi-leg order format is incorrect")
                logging.error(f"  2. Position intents (BUY_TO_CLOSE/SELL_TO_CLOSE) are wrong")
                logging.error(f"  3. One leg has already been closed, leaving naked position")
                print(f"{Colors.ERROR}[!] Cannot close legs individually - would create uncovered positions{Colors.RESET}")
                print(f"{Colors.WARNING}[!] Keeping position open to avoid uncovered options violation{Colors.RESET}")

                # DO NOT close legs individually - this creates the uncovered position problem
                # Instead, log the issue and keep position open
                return False

            # For other errors (network, timeout, etc.), do NOT attempt individual closes
            # Closing legs individually creates uncovered options which paper accounts can't trade
            logging.error(f"Multi-leg exit failed, but NOT attempting individual leg closes")
            logging.error(f"Individual leg closes would create uncovered options positions")
            logging.warning(f"Position {underlying} will remain open - manual intervention may be required")
            print(f"{Colors.WARNING}[!] Position remains open - check logs for details{Colors.RESET}")

            return False

    def _execute_exit(self, position, reason: str) -> bool:
        """Execute position exit using Alpaca's close_position method"""
        try:
            symbol = position.symbol
            qty = abs(int(position.qty)) if position.qty is not None else 0

            if qty == 0:
                logging.warning(f"Cannot exit {symbol}: quantity is 0")
                return False

            # Use Alpaca's dedicated close_position method
            # This handles all the complexity of closing positions correctly
            logging.info(f"Closing position {symbol} - {reason}")

            try:
                # close_position() liquidates the position by placing a closing order
                order = self.trading_client.close_position(symbol)
                logging.info(f"Close position order submitted for {symbol}: {order.id if hasattr(order, 'id') else 'N/A'} - {reason}")

                # Remove from active position tracking
                underlying = extract_underlying_symbol(symbol)
                self.journal.remove_active_position(underlying)

                return True

            except Exception as close_error:
                error_msg = str(close_error).lower()

                # If close_position fails, it might be due to liquidity issues
                # Fall back to a manual limit order at $0.01 for worthless options
                if "no available bid" in error_msg or "not marketable" in error_msg or "not eligible" in error_msg:
                    logging.warning(f"close_position() failed for {symbol}, attempting manual limit order")

                    from alpaca.trading.requests import LimitOrderRequest
                    from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent

                    # Determine order details
                    if int(position.qty) > 0:
                        close_side = OrderSide.SELL
                        position_intent = PositionIntent.SELL_TO_CLOSE
                    else:
                        close_side = OrderSide.BUY
                        position_intent = PositionIntent.BUY_TO_CLOSE

                    # Get current price
                    current_price = float(position.current_price) if position.current_price is not None else 0.0

                    # For worthless options or no price data, treat as expired/worthless
                    if current_price <= 0:
                        logging.warning(f"No current price for {symbol}, treating as worthless - removing from tracking")
                        # Remove from active position tracking without placing order
                        underlying = extract_underlying_symbol(symbol)
                        self.journal.remove_active_position(underlying)
                        print(f"{Colors.INFO}[EXIT] {symbol}: Treated as worthless (no market price) - removed from tracking{Colors.RESET}")
                        return True  # Return success since position is effectively closed
                    else:
                        # Use current price with slippage
                        if close_side == OrderSide.SELL:
                            limit_price = round(current_price * 0.95, 2)
                        else:
                            limit_price = round(current_price * 1.05, 2)
                        limit_price = max(0.01, limit_price)

                    # Submit manual limit order
                    # IMPORTANT: DO NOT use position_intent for single-leg options
                    # Alpaca paper trading interprets SELL_TO_CLOSE as potentially creating uncovered positions
                    # Use simple order without position_intent to avoid "account not eligible" errors
                    limit_order_data = LimitOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=close_side,
                        time_in_force=TimeInForce.DAY,
                        limit_price=limit_price
                        # NOTE: position_intent removed - causes "uncovered options" rejection in paper trading
                    )

                    order = self.trading_client.submit_order(limit_order_data)
                    logging.info(f"Manual limit order submitted for {symbol}: {order.id} - {reason} @ ${limit_price}")

                    # Remove from active position tracking
                    underlying = extract_underlying_symbol(symbol)
                    self.journal.remove_active_position(underlying)

                    return True
                else:
                    # Different error - re-raise
                    raise

        except Exception as e:
            logging.error(f"Error executing exit for {position.symbol}: {e}")
            return False

    def _get_days_to_expiration(self, symbol: str) -> Optional[int]:
        """Extract days to expiration from OCC symbol"""
        try:
            # OCC format: TICKER + YYMMDD + C/P + STRIKE
            # Example: SPY251219C00450000
            if len(symbol) > 6:
                date_part = symbol[-15:-9]  # Extract YYMMDD
                exp_date = datetime.strptime(date_part, '%y%m%d')
                dte = (exp_date - datetime.now()).days
                return dte
            return None
        except:
            return None

    def _get_position_strategy(self, symbol: str) -> str:
        """
        FIXED: Issue #6 - Get strategy type for a position from trade journal
        Returns strategy name or 'UNKNOWN' if not found
        """
        try:
            # Try to get strategy from active positions tracking
            if self.journal:
                strategy_info = self.journal.get_position_strategy(symbol)
                if strategy_info:
                    return strategy_info.get('strategy', 'UNKNOWN')

            # Fallback: Try to infer from symbol
            # Multi-leg positions have _MULTI_LEG suffix
            if '_MULTI_LEG' in symbol:
                return 'UNKNOWN'  # Use default stop loss for multi-leg if strategy not tracked

            # Single-leg options: determine from OCC symbol
            if len(symbol) > 6:
                option_type = symbol[-9]  # C or P
                # For single-leg, assume LONG (most common for bot)
                if option_type == 'C':
                    return 'LONG_CALL'
                elif option_type == 'P':
                    return 'LONG_PUT'

            return 'UNKNOWN'

        except Exception as e:
            logging.debug(f"Error getting strategy for {symbol}: {e}")
            return 'UNKNOWN'

    def _log_exit_to_db(self, symbol: str, exit_price: float, exit_reason: str, pnl: float, pnl_pct: float):
        """Log exit to trade journal"""
        try:
            # Find matching open trade
            open_trades = self.journal.get_open_trades()

            for trade in open_trades:
                if trade['occ_symbol'] == symbol or trade['symbol'] == symbol:
                    entry_time = datetime.fromisoformat(trade['timestamp'])
                    hold_time = (datetime.now() - entry_time).total_seconds() / 3600

                    exit_data = {
                        'exit_price': exit_price,
                        'exit_reason': exit_reason,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'hold_time_hours': hold_time
                    }

                    self.journal.log_exit(trade['id'], exit_data)

                    # FIXED: Issue #17 - Log Grok calibration data
                    if trade.get('confidence'):
                        self.journal.log_grok_calibration(
                            trade_id=trade['id'],
                            symbol=trade['symbol'],
                            strategy=trade['strategy'],
                            grok_confidence=trade['confidence'],
                            pnl_pct=pnl_pct,
                            hold_time_hours=hold_time
                        )

                    break

        except Exception as e:
            logging.error(f"Error logging exit to database: {e}")


def print_banner():
    banner = f"""{Colors.HEADER}
================================================================================

  ####   #    #  #####   #    #       ####   #####   #####  #   ####   #    #   ####
  #   #  #    #  #    #  #   #       #    #  #    #    #    #  #    #  ##   #  #
  #    # #    #  #####   ####        #    #  #####     #    #  #    #  # #  #   ####
  #    # #    #  #    #  #  #        #    #  #         #    #  #    #  #  # #       #
  ####    ####   #####   #   #        ####   #         #    #   ####   #   ##   ####

  v3.1 Professional Options Trading
  ✓ Position Management & Exit Rules  ✓ Greeks Analysis & Portfolio Tracking
  ✓ IV Rank & Percentile Analysis  ✓ Earnings Calendar Risk Assessment
  ✓ Expert Market Scanning  ✓ Async Performance & Concurrent API Calls
  ✓ Trade Journal & Performance Analytics  ✓ Smart Exits & Stop Loss Management
================================================================================
{Colors.RESET}"""
    print(banner)


