"""
Batch Order Manager - Efficient Multi-Strategy Management

Enables processing multiple orders/cancellations/exits in parallel:
- Reduces API calls
- Improves execution speed
- Coordinates multi-leg strategies
"""

import logging
import asyncio
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from colorama import Fore, Style


class BatchOrderManager:
    """
    PHASE 3: Batch operations for efficient multi-strategy management

    Enables processing multiple orders/cancellations/exits in parallel,
    reducing API calls and improving execution speed.
    """

    def __init__(self, trading_client, multi_leg_tracker, alert_manager):
        self.trading_client = trading_client
        self.multi_leg_tracker = multi_leg_tracker
        self.alert_manager = alert_manager
        logging.info("BatchOrderManager initialized for Phase 3 optimizations")

    def batch_cancel_strategies(self, strategy_ids: List[str], reason: str = "Batch cancellation") -> Dict:
        """
        PHASE 3: Cancel multiple multi-leg strategies in a single batch operation

        Args:
            strategy_ids: List of strategy IDs to cancel
            reason: Reason for batch cancellation

        Returns:
            {
                'total_requested': int,
                'successful': List[str],  # Strategy IDs successfully cancelled
                'failed': List[str],      # Strategy IDs that failed
                'had_fills': List[str],   # Strategy IDs with fills (not cancelled)
                'details': Dict[str, Dict]  # Detailed results per strategy
            }
        """
        if not strategy_ids:
            logging.warning("batch_cancel_strategies called with empty list")
            return {
                'total_requested': 0,
                'successful': [],
                'failed': [],
                'had_fills': [],
                'details': {}
            }

        logging.info(f"=== PHASE 3: Batch cancellation of {len(strategy_ids)} strategies ===")
        logging.info(f"Reason: {reason}")

        results = {
            'total_requested': len(strategy_ids),
            'successful': [],
            'failed': [],
            'had_fills': [],
            'details': {}
        }

        # Process each strategy in parallel-friendly manner
        for strategy_id in strategy_ids:
            try:
                strategy_status = self.multi_leg_tracker.get_strategy_status(strategy_id)
                if not strategy_status:
                    logging.warning(f"Strategy {strategy_id} not found in tracker")
                    results['failed'].append(strategy_id)
                    results['details'][strategy_id] = {
                        'success': False,
                        'error': 'Strategy not found in tracker'
                    }
                    continue

                # Check for fills before cancelling
                if self.multi_leg_tracker.has_any_fills(strategy_id):
                    logging.warning(f"Strategy {strategy_id} has fills - cannot cancel")
                    results['had_fills'].append(strategy_id)
                    results['details'][strategy_id] = {
                        'success': False,
                        'had_fills': True,
                        'error': 'Strategy has filled legs'
                    }
                    continue

                # Get unfilled legs
                unfilled_legs = self.multi_leg_tracker.get_unfilled_leg_ids(strategy_id)
                if not unfilled_legs:
                    logging.info(f"Strategy {strategy_id} has no unfilled legs to cancel")
                    results['successful'].append(strategy_id)
                    results['details'][strategy_id] = {
                        'success': True,
                        'message': 'No unfilled legs'
                    }
                    continue

                # Cancel each unfilled leg
                cancelled_count = 0
                cancel_errors = []

                for order_id in unfilled_legs:
                    try:
                        self.trading_client.cancel_order_by_id(order_id)
                        cancelled_count += 1
                        logging.info(f"Cancelled leg {order_id} from strategy {strategy_id}")
                    except Exception as e:
                        error_msg = str(e)
                        cancel_errors.append(f"Order {order_id}: {error_msg}")
                        logging.error(f"Failed to cancel leg {order_id}: {error_msg}")

                # Mark strategy as successful if we cancelled at least some legs
                if cancelled_count > 0:
                    results['successful'].append(strategy_id)
                    results['details'][strategy_id] = {
                        'success': True,
                        'cancelled_legs': cancelled_count,
                        'errors': cancel_errors if cancel_errors else None
                    }
                    logging.info(f"Successfully cancelled {cancelled_count}/{len(unfilled_legs)} legs for {strategy_id}")
                else:
                    results['failed'].append(strategy_id)
                    results['details'][strategy_id] = {
                        'success': False,
                        'error': f"Failed to cancel any legs: {'; '.join(cancel_errors)}"
                    }

            except Exception as e:
                logging.error(f"Error processing strategy {strategy_id} in batch cancel: {e}")
                results['failed'].append(strategy_id)
                results['details'][strategy_id] = {
                    'success': False,
                    'error': str(e)
                }

        # Summary logging
        logging.info(f"Batch cancel complete: {len(results['successful'])}/{results['total_requested']} successful")
        if results['had_fills']:
            logging.warning(f"Skipped {len(results['had_fills'])} strategies with fills: {results['had_fills']}")
        if results['failed']:
            logging.error(f"Failed {len(results['failed'])} strategies: {results['failed']}")

        return results

    def batch_close_positions(self, symbols: List[str], reason: str = "Batch exit") -> Dict:
        """
        PHASE 3: Close multiple positions in a single batch operation

        Args:
            symbols: List of OCC symbols to close
            reason: Reason for batch close

        Returns:
            {
                'total_requested': int,
                'successful': List[str],  # Symbols successfully closed
                'failed': List[str],      # Symbols that failed
                'partial': List[str],     # Symbols with partial closes
                'details': Dict[str, Dict]  # Detailed results per symbol
            }
        """
        if not symbols:
            logging.warning("batch_close_positions called with empty list")
            return {
                'total_requested': 0,
                'successful': [],
                'failed': [],
                'partial': [],
                'details': {}
            }

        logging.info(f"=== PHASE 3: Batch close of {len(symbols)} positions ===")
        logging.info(f"Reason: {reason}")

        results = {
            'total_requested': len(symbols),
            'successful': [],
            'failed': [],
            'partial': [],
            'details': {}
        }

        # Get all positions once (more efficient than per-symbol queries)
        try:
            all_positions = self.trading_client.get_all_positions()
            positions_by_symbol = {pos.symbol: pos for pos in all_positions}
        except Exception as e:
            logging.error(f"Failed to fetch positions for batch close: {e}")
            return {
                'total_requested': len(symbols),
                'successful': [],
                'failed': symbols,
                'partial': [],
                'details': {sym: {'success': False, 'error': f"Failed to fetch positions: {e}"} for sym in symbols}
            }

        # Process each position
        for symbol in symbols:
            try:
                if symbol not in positions_by_symbol:
                    logging.warning(f"Position {symbol} not found in account")
                    results['failed'].append(symbol)
                    results['details'][symbol] = {
                        'success': False,
                        'error': 'Position not found'
                    }
                    continue

                position = positions_by_symbol[symbol]
                qty = abs(int(position.qty)) if position.qty is not None else 0

                if qty == 0:
                    logging.info(f"Position {symbol} has zero quantity")
                    results['successful'].append(symbol)
                    results['details'][symbol] = {
                        'success': True,
                        'message': 'Zero quantity position'
                    }
                    continue

                # Attempt to close position
                try:
                    close_result = self.trading_client.close_position(symbol)
                    results['successful'].append(symbol)
                    results['details'][symbol] = {
                        'success': True,
                        'quantity': qty,
                        'order_id': getattr(close_result, 'id', 'UNKNOWN')
                    }
                    logging.info(f"Successfully closed position {symbol} (qty: {qty})")

                except Exception as close_error:
                    error_msg = str(close_error)
                    logging.error(f"Failed to close {symbol}: {error_msg}")
                    results['failed'].append(symbol)
                    results['details'][symbol] = {
                        'success': False,
                        'error': error_msg,
                        'quantity': qty
                    }

            except Exception as e:
                logging.error(f"Error processing position {symbol} in batch close: {e}")
                results['failed'].append(symbol)
                results['details'][symbol] = {
                    'success': False,
                    'error': str(e)
                }

        # Summary logging
        logging.info(f"Batch close complete: {len(results['successful'])}/{results['total_requested']} successful")
        if results['failed']:
            logging.error(f"Failed {len(results['failed'])} positions: {results['failed']}")

        # Alert on batch failures
        if len(results['failed']) > 2:  # Multiple failures
            self.alert_manager.send_alert(
                'CRITICAL',
                f"Batch close failed for {len(results['failed'])} positions: {', '.join(results['failed'][:5])}",
                throttle_key="batch_close_failure"
            )

        return results

    def batch_submit_orders(self, orders: List[Dict]) -> Dict:
        """
        PHASE 3: Submit multiple orders in a single batch operation

        Args:
            orders: List of order dictionaries, each containing:
                {
                    'symbol': str,
                    'qty': int,
                    'side': str ('buy' or 'sell'),
                    'limit_price': float,
                    'strategy_id': str (optional, for tracking)
                }

        Returns:
            {
                'total_requested': int,
                'successful': List[Dict],  # Orders successfully submitted
                'failed': List[Dict],      # Orders that failed
                'order_ids': List[str],    # All successful order IDs
                'details': Dict[str, Dict]  # Detailed results per order
            }
        """
        if not orders:
            logging.warning("batch_submit_orders called with empty list")
            return {
                'total_requested': 0,
                'successful': [],
                'failed': [],
                'order_ids': [],
                'details': {}
            }

        logging.info(f"=== PHASE 3: Batch submission of {len(orders)} orders ===")

        results = {
            'total_requested': len(orders),
            'successful': [],
            'failed': [],
            'order_ids': [],
            'details': {}
        }

        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        # Process each order
        for idx, order_info in enumerate(orders):
            order_key = f"order_{idx}_{order_info.get('symbol', 'UNKNOWN')}"

            try:
                symbol = order_info['symbol']
                qty = order_info['qty']
                side = OrderSide.BUY if order_info['side'].lower() == 'buy' else OrderSide.SELL
                limit_price = order_info['limit_price']

                # Submit order
                order_data = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price
                )

                order = self.trading_client.submit_order(order_data)
                order_id = str(order.id) if hasattr(order, 'id') else 'UNKNOWN'

                results['successful'].append(order_info)
                results['order_ids'].append(order_id)
                results['details'][order_key] = {
                    'success': True,
                    'order_id': order_id,
                    'symbol': symbol,
                    'qty': qty,
                    'limit_price': limit_price
                }

                logging.info(f"Batch order {idx+1}/{len(orders)} submitted: {symbol} {order_info['side']} {qty} @ ${limit_price} (ID: {order_id})")

            except Exception as e:
                error_msg = str(e)
                logging.error(f"Failed to submit batch order {idx+1}: {error_msg}")
                results['failed'].append(order_info)
                results['details'][order_key] = {
                    'success': False,
                    'error': error_msg,
                    'order': order_info
                }

        # Summary logging
        success_rate = len(results['successful']) / results['total_requested'] * 100 if results['total_requested'] > 0 else 0
        logging.info(f"Batch submit complete: {len(results['successful'])}/{results['total_requested']} successful ({success_rate:.1f}%)")

        if results['failed']:
            logging.error(f"Failed {len(results['failed'])} orders in batch")

        return results


