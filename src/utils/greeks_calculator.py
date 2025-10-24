"""
Greeks Calculator - Black-Scholes Model for Options Greeks

Calculates Delta, Gamma, Theta, Vega, and Rho for options contracts
when Greeks are not provided by the data provider.
"""

import math
from typing import Dict, Optional
from datetime import datetime, timedelta
import logging


class GreeksCalculator:
    """Calculate option Greeks using Black-Scholes model"""

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """
        Cumulative distribution function for standard normal distribution
        Approximation using error function
        """
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @staticmethod
    def _norm_pdf(x: float) -> float:
        """
        Probability density function for standard normal distribution
        """
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    @staticmethod
    def calculate_greeks(
        spot_price: float,
        strike: float,
        time_to_expiry: float,  # In years
        volatility: float,  # Implied volatility (annual)
        risk_free_rate: float,  # Annual risk-free rate
        option_type: str  # 'call' or 'put'
    ) -> Dict[str, float]:
        """
        Calculate all Greeks for an option using Black-Scholes model

        Args:
            spot_price: Current price of the underlying
            strike: Strike price of the option
            time_to_expiry: Time to expiration in years (DTE / 365)
            volatility: Implied volatility as decimal (e.g., 0.30 for 30%)
            risk_free_rate: Risk-free rate as decimal (e.g., 0.045 for 4.5%)
            option_type: 'call' or 'put'

        Returns:
            Dict with delta, gamma, theta, vega, rho
        """
        try:
            # Handle edge cases
            if time_to_expiry <= 0:
                # Expired or same-day expiration
                if option_type.lower() == 'call':
                    delta = 1.0 if spot_price > strike else 0.0
                else:
                    delta = -1.0 if spot_price < strike else 0.0

                return {
                    'delta': delta,
                    'gamma': 0.0,
                    'theta': 0.0,
                    'vega': 0.0,
                    'rho': 0.0
                }

            if volatility <= 0:
                volatility = 0.01  # Minimum 1% volatility

            if spot_price <= 0 or strike <= 0:
                logging.warning(f"Invalid prices: spot={spot_price}, strike={strike}")
                return {
                    'delta': 0.5 if option_type.lower() == 'call' else -0.5,
                    'gamma': 0.05,
                    'theta': -0.03,
                    'vega': 0.15,
                    'rho': 0.01
                }

            # Calculate d1 and d2
            d1 = (math.log(spot_price / strike) +
                  (risk_free_rate + 0.5 * volatility ** 2) * time_to_expiry) / \
                 (volatility * math.sqrt(time_to_expiry))

            d2 = d1 - volatility * math.sqrt(time_to_expiry)

            # Calculate Greeks
            if option_type.lower() == 'call':
                delta = GreeksCalculator._norm_cdf(d1)
                rho = strike * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * \
                      GreeksCalculator._norm_cdf(d2) / 100  # Per 1% change
            else:  # put
                delta = GreeksCalculator._norm_cdf(d1) - 1.0
                rho = -strike * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * \
                      GreeksCalculator._norm_cdf(-d2) / 100  # Per 1% change

            # Gamma is same for calls and puts
            gamma = GreeksCalculator._norm_pdf(d1) / \
                    (spot_price * volatility * math.sqrt(time_to_expiry))

            # Vega is same for calls and puts (per 1% change in volatility)
            vega = spot_price * GreeksCalculator._norm_pdf(d1) * math.sqrt(time_to_expiry) / 100

            # Theta (per day, not per year)
            if option_type.lower() == 'call':
                theta = ((-spot_price * GreeksCalculator._norm_pdf(d1) * volatility) /
                        (2 * math.sqrt(time_to_expiry)) -
                        risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry) *
                        GreeksCalculator._norm_cdf(d2)) / 365
            else:  # put
                theta = ((-spot_price * GreeksCalculator._norm_pdf(d1) * volatility) /
                        (2 * math.sqrt(time_to_expiry)) +
                        risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry) *
                        GreeksCalculator._norm_cdf(-d2)) / 365

            return {
                'delta': delta,
                'gamma': gamma,
                'theta': theta,
                'vega': vega,
                'rho': rho
            }

        except Exception as e:
            logging.error(f"Error calculating Greeks: {e}")
            # Return reasonable defaults
            return {
                'delta': 0.5 if option_type.lower() == 'call' else -0.5,
                'gamma': 0.05,
                'theta': -0.03,
                'vega': 0.15,
                'rho': 0.01
            }

    @staticmethod
    def add_greeks_to_options_chain(
        options_data: list,
        spot_price: float,
        risk_free_rate: float = 0.045  # 4.5% default
    ) -> list:
        """
        Add calculated Greeks to all options in a chain

        Args:
            options_data: List of option contracts
            spot_price: Current price of underlying
            risk_free_rate: Annual risk-free rate

        Returns:
            Updated options_data with Greeks added
        """
        for option in options_data:
            try:
                # Skip if Greeks already present and valid
                if (option.get('delta') and option.get('delta') != 0 and
                    option.get('gamma') and option.get('gamma') != 0):
                    continue

                # Extract required data
                strike = option.get('strike')
                expiration = option.get('expiration')
                implied_volatility = option.get('implied_volatility', 0)
                option_type = 'call' if option.get('option_type', '').lower() == 'call' else 'put'

                if not strike or not expiration:
                    continue

                # Calculate time to expiry
                if isinstance(expiration, str):
                    exp_date = datetime.strptime(expiration, '%Y-%m-%d')
                else:
                    exp_date = expiration

                days_to_expiry = (exp_date - datetime.now()).days
                time_to_expiry = max(days_to_expiry / 365.0, 1/365)  # Minimum 1 day

                # Use implied volatility or estimate
                if implied_volatility == 0:
                    implied_volatility = 0.30  # 30% default

                # Calculate Greeks
                greeks = GreeksCalculator.calculate_greeks(
                    spot_price=spot_price,
                    strike=strike,
                    time_to_expiry=time_to_expiry,
                    volatility=implied_volatility,
                    risk_free_rate=risk_free_rate,
                    option_type=option_type
                )

                # Add Greeks to option
                option['delta'] = greeks['delta']
                option['gamma'] = greeks['gamma']
                option['theta'] = greeks['theta']
                option['vega'] = greeks['vega']
                option['rho'] = greeks['rho']
                option['greeks_calculated'] = True  # Flag to indicate calculated vs provided

            except Exception as e:
                logging.debug(f"Could not calculate Greeks for option: {e}")
                continue

        return options_data
