"""
Multi-Leg Order Tracker - Atomic Multi-Leg Order Tracking

Tracks multi-leg option strategies as single atomic units to prevent:
- Orphaned legs
- Partial fills without monitoring
- Lost tracking of strategy components
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional


class MultiLegOrderTracker:
    """
    PHASE 1: Multi-leg order tracking for atomic operations
    Tracks all legs of a multi-leg strategy as a single atomic unit to prevent orphaned legs
    """

    def __init__(self):
        self.multi_leg_orders = {}  # strategy_id -> order details
        logging.info("MultiLegOrderTracker initialized")

    def create_strategy_id(self, symbol: str, strategy: str) -> str:
        """Generate unique strategy ID for tracking"""
        return f"{symbol}_{strategy}_{uuid.uuid4().hex[:8]}"

    def register_multi_leg_order(self, strategy_id: str, symbol: str, strategy: str,
                                 leg_order_ids: List[str], legs_info: List[Dict]) -> None:
        """
        Register a multi-leg order for tracking

        Args:
            strategy_id: Unique identifier for this strategy
            symbol: Underlying symbol
            strategy: Strategy name (e.g., 'BULL_CALL_SPREAD')
            leg_order_ids: List of Alpaca order IDs for each leg
            legs_info: List of leg details (strike, type, side, quantity)
        """
        if not leg_order_ids:
            logging.warning(f"No order IDs provided for multi-leg strategy {strategy_id}")
            return

        self.multi_leg_orders[strategy_id] = {
            'symbol': symbol,
            'strategy': strategy,
            'leg_order_ids': leg_order_ids,
            'legs_info': legs_info,
            'created_at': datetime.now().isoformat(),
            'status': 'PENDING',  # PENDING, FILLED, PARTIALLY_FILLED, CANCELLED, FAILED
            'filled_leg_ids': [],
            'cancelled_leg_ids': [],
            'failed_leg_ids': []
        }

        logging.info(f"Registered multi-leg order {strategy_id}: {symbol} {strategy} with {len(leg_order_ids)} legs")
        logging.info(f"Leg order IDs: {', '.join(str(oid) for oid in leg_order_ids)}")

    def update_leg_status(self, strategy_id: str, order_id: str, status: str) -> None:
        """
        Update status of a specific leg

        Args:
            strategy_id: Strategy identifier
            order_id: Alpaca order ID
            status: FILLED, CANCELLED, or FAILED
        """
        if strategy_id not in self.multi_leg_orders:
            logging.warning(f"Strategy {strategy_id} not found in tracker")
            return

        order_data = self.multi_leg_orders[strategy_id]

        if order_id not in order_data['leg_order_ids']:
            logging.warning(f"Order {order_id} not part of strategy {strategy_id}")
            return

        # Update status tracking
        if status == 'FILLED':
            if order_id not in order_data['filled_leg_ids']:
                order_data['filled_leg_ids'].append(order_id)
        elif status == 'CANCELLED':
            if order_id not in order_data['cancelled_leg_ids']:
                order_data['cancelled_leg_ids'].append(order_id)
        elif status == 'FAILED':
            if order_id not in order_data['failed_leg_ids']:
                order_data['failed_leg_ids'].append(order_id)

        # Update overall strategy status
        total_legs = len(order_data['leg_order_ids'])
        filled_count = len(order_data['filled_leg_ids'])
        cancelled_count = len(order_data['cancelled_leg_ids'])
        failed_count = len(order_data['failed_leg_ids'])

        if filled_count == total_legs:
            order_data['status'] = 'FILLED'
        elif cancelled_count == total_legs:
            order_data['status'] = 'CANCELLED'
        elif failed_count == total_legs:
            order_data['status'] = 'FAILED'
        elif filled_count > 0 and filled_count < total_legs:
            order_data['status'] = 'PARTIALLY_FILLED'
            logging.warning(f"⚠️ PARTIAL FILL DETECTED: Strategy {strategy_id} has {filled_count}/{total_legs} legs filled")

    def get_strategy_by_leg_id(self, order_id: str) -> Optional[str]:
        """Find strategy ID by leg order ID"""
        for strategy_id, data in self.multi_leg_orders.items():
            if order_id in data['leg_order_ids']:
                return strategy_id
        return None

    def get_strategy_status(self, strategy_id: str) -> Optional[Dict]:
        """Get current status of a multi-leg strategy"""
        return self.multi_leg_orders.get(strategy_id)

    def has_any_fills(self, strategy_id: str) -> bool:
        """Check if any leg of the strategy has filled"""
        if strategy_id not in self.multi_leg_orders:
            return False
        return len(self.multi_leg_orders[strategy_id]['filled_leg_ids']) > 0

    def get_unfilled_leg_ids(self, strategy_id: str) -> List[str]:
        """Get list of leg order IDs that have not filled yet"""
        if strategy_id not in self.multi_leg_orders:
            return []

        order_data = self.multi_leg_orders[strategy_id]
        return [
            order_id for order_id in order_data['leg_order_ids']
            if order_id not in order_data['filled_leg_ids']
            and order_id not in order_data['cancelled_leg_ids']
            and order_id not in order_data['failed_leg_ids']
        ]

    def cleanup_completed_strategies(self, older_than_hours: int = 24) -> None:
        """Remove old completed strategies from tracker"""
        cutoff_time = datetime.now() - timedelta(hours=older_than_hours)

        strategies_to_remove = []
        for strategy_id, data in self.multi_leg_orders.items():
            created_at = datetime.fromisoformat(data['created_at'])
            if data['status'] in ['FILLED', 'CANCELLED', 'FAILED'] and created_at < cutoff_time:
                strategies_to_remove.append(strategy_id)

        for strategy_id in strategies_to_remove:
            del self.multi_leg_orders[strategy_id]
            logging.info(f"Cleaned up completed strategy {strategy_id}")
