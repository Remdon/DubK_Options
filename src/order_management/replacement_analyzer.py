"""
Replacement Analyzer - Intelligent Order Replacement Analysis

Analyzes whether to cancel existing orders in favor of new opportunities using:
- AI-powered cost-benefit analysis
- Market conditions assessment
- Historical performance analysis
- Transaction cost calculations
"""

import logging
from datetime import datetime
from typing import Dict


class ReplacementAnalyzer:
    """
    PHASE 2: Intelligent replacement decision system for multi-leg orders

    Analyzes whether to cancel existing orders in favor of new opportunities
    using AI-powered cost-benefit analysis, market conditions, and historical performance.
    """

    def __init__(self, trade_journal, openbb_client):
        self.trade_journal = trade_journal
        self.openbb = openbb_client
        logging.info("ReplacementAnalyzer initialized for Phase 2 intelligence")

    def should_replace_order(self, existing_strategy: Dict, new_opportunity: Dict,
                            market_conditions: Dict) -> Dict:
        """
        PHASE 2: AI-powered decision on whether to replace existing order

        Args:
            existing_strategy: Current strategy details from tracker
            new_opportunity: New trade opportunity details
            market_conditions: Current market state

        Returns:
            {
                'should_replace': bool,
                'confidence_score': float (0-100),
                'reasons': List[str],
                'risk_factors': List[str],
                'cost_benefit': Dict
            }
        """
        logging.info(f"=== PHASE 2: Analyzing replacement opportunity ===")
        logging.info(f"Existing: {existing_strategy.get('strategy')} on {existing_strategy.get('symbol')}")
        logging.info(f"New: {new_opportunity.get('strategy')} at {new_opportunity.get('confidence')}% confidence")

        reasons = []
        risk_factors = []
        score = 0

        # Factor 1: Confidence Delta (weight: 30%)
        confidence_delta = new_opportunity['confidence'] - existing_strategy.get('confidence', 0)
        if confidence_delta > 15:
            score += 30
            reasons.append(f"Significantly higher confidence (+{confidence_delta}%)")
        elif confidence_delta > 8:
            score += 20
            reasons.append(f"Higher confidence (+{confidence_delta}%)")
        elif confidence_delta > 0:
            score += 10
            reasons.append(f"Marginally higher confidence (+{confidence_delta}%)")
        else:
            risk_factors.append(f"New confidence not higher ({confidence_delta}%)")

        # Factor 2: Time in Market (weight: 20%)
        # Older orders more likely to be stale
        order_age_minutes = self._calculate_order_age(existing_strategy)
        if order_age_minutes > 60:
            score += 20
            reasons.append(f"Existing order is stale ({order_age_minutes:.0f} min old)")
        elif order_age_minutes > 30:
            score += 15
            reasons.append(f"Existing order aging ({order_age_minutes:.0f} min old)")
        elif order_age_minutes > 15:
            score += 10
            reasons.append(f"Order has been pending ({order_age_minutes:.0f} min)")
        else:
            risk_factors.append(f"Order is fresh ({order_age_minutes:.0f} min old)")

        # Factor 3: Market Conditions (weight: 25%)
        market_score = self._analyze_market_conditions(
            existing_strategy.get('symbol'),
            market_conditions
        )
        score += market_score['score']
        reasons.extend(market_score['reasons'])
        risk_factors.extend(market_score['risks'])

        # Factor 4: Historical Performance (weight: 15%)
        performance_score = self._analyze_historical_performance(
            existing_strategy.get('strategy'),
            new_opportunity.get('strategy')
        )
        score += performance_score['score']
        reasons.extend(performance_score['reasons'])

        # Factor 5: Cost-Benefit Analysis (weight: 10%)
        cost_benefit = self._calculate_cost_benefit(
            existing_strategy,
            new_opportunity,
            market_conditions
        )
        score += cost_benefit['score']
        reasons.extend(cost_benefit['reasons'])
        risk_factors.extend(cost_benefit['risks'])

        # Decision threshold: 60% confidence to replace
        should_replace = score >= 60

        logging.info(f"Replacement analysis complete: Score={score}/100, Replace={should_replace}")
        for reason in reasons:
            logging.info(f"  ✓ {reason}")
        for risk in risk_factors:
            logging.warning(f"  ! {risk}")

        return {
            'should_replace': should_replace,
            'confidence_score': score,
            'reasons': reasons,
            'risk_factors': risk_factors,
            'cost_benefit': cost_benefit
        }

    def _calculate_order_age(self, strategy: Dict) -> float:
        """Calculate how long the order has been pending (in minutes)"""
        try:
            created_at = datetime.fromisoformat(strategy.get('created_at', datetime.now().isoformat()))
            age = (datetime.now() - created_at).total_seconds() / 60
            return age
        except Exception as e:
            logging.warning(f"Could not calculate order age: {e}")
            return 0

    def _analyze_market_conditions(self, symbol: str, conditions: Dict) -> Dict:
        """
        PHASE 2: Analyze current market conditions for replacement timing

        Considers:
        - Volatility regime (increasing/decreasing)
        - Price momentum
        - Volume patterns
        - Market hours (open/close dynamics)
        """
        score = 0
        reasons = []
        risks = []

        try:
            # Check volatility trend
            regime = conditions.get('regime', 'NEUTRAL')
            iv_rank = conditions.get('iv_rank', 50)

            if regime == 'BULL' and iv_rank < 30:
                score += 10
                reasons.append("Bullish regime with low IV (favorable for entry)")
            elif regime == 'BEAR' and iv_rank > 70:
                score += 10
                reasons.append("Bearish regime with high IV (favorable for defensive plays)")
            elif regime == 'NEUTRAL':
                score += 5
                reasons.append("Neutral market (moderate replacement opportunity)")

            # Check time of day (market hours matter)
            current_hour = datetime.now().hour
            if 9 <= current_hour <= 10:
                # Market open - high volatility
                score += 5
                reasons.append("Market open hours (good timing for re-entry)")
            elif 15 <= current_hour <= 16:
                # Market close - high volume
                score += 5
                reasons.append("Market close hours (high liquidity)")
            elif 11 <= current_hour <= 14:
                # Mid-day lull
                risks.append("Mid-day trading (lower liquidity)")

            # Check price momentum from scanner analysis
            if conditions.get('price_change_pct', 0) > 3:
                score += 5
                reasons.append(f"Strong momentum (+{conditions['price_change_pct']:.1f}%)")
            elif conditions.get('price_change_pct', 0) < -3:
                score += 5
                reasons.append(f"Reversal opportunity ({conditions['price_change_pct']:.1f}%)")

        except Exception as e:
            logging.warning(f"Error analyzing market conditions: {e}")
            risks.append("Could not fully analyze market conditions")

        return {'score': score, 'reasons': reasons, 'risks': risks}

    def _analyze_historical_performance(self, existing_strategy: str,
                                       new_strategy: str) -> Dict:
        """
        PHASE 2: Analyze historical performance of strategy types

        Checks trade journal for:
        - Win rate of each strategy
        - Average P&L
        - Recent performance trends
        """
        score = 0
        reasons = []

        try:
            # Query trade journal for historical performance
            query = """
                SELECT strategy,
                       COUNT(*) as total_trades,
                       AVG(CASE WHEN status='CLOSED' AND total_cost > 0 THEN 1 ELSE 0 END) as win_rate,
                       AVG(total_cost) as avg_pnl
                FROM trades
                WHERE timestamp > datetime('now', '-30 days')
                  AND strategy IN (?, ?)
                GROUP BY strategy
            """

            cursor = self.trade_journal.conn.cursor()
            results = cursor.execute(query, (existing_strategy, new_strategy)).fetchall()

            performance = {}
            for row in results:
                strategy_name = row[0]
                performance[strategy_name] = {
                    'total_trades': row[1],
                    'win_rate': row[2] or 0,
                    'avg_pnl': row[3] or 0
                }

            existing_perf = performance.get(existing_strategy, {})
            new_perf = performance.get(new_strategy, {})

            # Compare win rates
            if new_perf.get('total_trades', 0) >= 3:  # Need minimum sample size
                existing_wr = existing_perf.get('win_rate', 0)
                new_wr = new_perf.get('win_rate', 0)

                if new_wr > existing_wr + 0.15:  # 15% better win rate
                    score += 10
                    reasons.append(f"New strategy has better win rate ({new_wr:.0%} vs {existing_wr:.0%})")
                elif new_wr > existing_wr:
                    score += 5
                    reasons.append(f"New strategy performing better ({new_wr:.0%})")

            # Compare average P&L
            if new_perf.get('avg_pnl', 0) > existing_perf.get('avg_pnl', 0):
                score += 5
                reasons.append("New strategy has better average P&L")

        except Exception as e:
            logging.warning(f"Could not analyze historical performance: {e}")
            # Don't penalize if no history available

        return {'score': score, 'reasons': reasons}

    def _calculate_cost_benefit(self, existing_strategy: Dict,
                                new_opportunity: Dict,
                                market_conditions: Dict) -> Dict:
        """
        PHASE 2: Cost-benefit analysis of replacement

        Considers:
        - Transaction costs (bid-ask spread on cancellation)
        - Opportunity cost (old vs new potential)
        - Execution risk
        """
        score = 0
        reasons = []
        risks = []

        try:
            # Estimate transaction cost of cancellation
            # Multi-leg orders have wider spreads, higher cost to exit/re-enter
            spread_estimate = market_conditions.get('avg_bid_ask_spread', 0.05)

            if spread_estimate < 0.03:
                score += 5
                reasons.append("Tight spreads (low transaction cost)")
            elif spread_estimate > 0.10:
                score += 0
                risks.append(f"Wide spreads ({spread_estimate:.1%}) - high transaction cost")
            else:
                score += 3
                reasons.append("Moderate spreads")

            # Check if new opportunity is significantly better
            confidence_improvement = new_opportunity['confidence'] - existing_strategy.get('confidence', 0)
            if confidence_improvement >= 20:
                score += 5
                reasons.append("Major confidence improvement justifies costs")
            elif confidence_improvement >= 10:
                score += 3
                reasons.append("Meaningful confidence improvement")

        except Exception as e:
            logging.warning(f"Error in cost-benefit analysis: {e}")

        return {'score': score, 'reasons': reasons, 'risks': risks}

