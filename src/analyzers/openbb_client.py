"""
OpenBB Client - REST API Client with Error Handling and Circuit Breaker

Provides a robust client for accessing OpenBB financial data with:
- Automatic retry logic with exponential backoff
- Circuit breaker pattern to prevent API hammering
- Auto-reset after timeout
- Comprehensive error handling
- Automatic Greeks calculation when not provided
"""

import logging
import time
import requests
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from src.utils.greeks_calculator import GreeksCalculator


class OpenBBClient:
    """Client for OpenBB REST API with error handling and retry logic"""

    def __init__(self, base_url='http://127.0.0.1:6900/api/v1'):
        self.base_url = base_url
        self.max_retries = 3
        self.retry_delay = 1.0
        self.consecutive_failures = 0
        self.circuit_breaker_threshold = 10
        self.circuit_breaker_active = False
        # FIXED: Issue #15 - Auto-reset circuit breaker after timeout
        self.circuit_breaker_activated_at = None
        self.circuit_breaker_timeout = 600  # 10 minutes

    def _should_retry(self, exception) -> bool:
        """Determine if request should be retried"""
        if self.circuit_breaker_active:
            return False

        # Retry on timeouts and connection errors
        return isinstance(exception, (requests.Timeout, requests.ConnectionError))

    def _handle_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """Handle HTTP request with retry logic"""
        # FIXED: Issue #15 - Check if circuit breaker should auto-reset
        if self.circuit_breaker_active:
            if self.circuit_breaker_activated_at:
                elapsed = time.time() - self.circuit_breaker_activated_at
                if elapsed > self.circuit_breaker_timeout:
                    logging.info(f"Circuit breaker auto-reset after {elapsed:.0f}s timeout (threshold: {self.circuit_breaker_timeout}s)")
                    self.reset_circuit_breaker()
                else:
                    remaining = self.circuit_breaker_timeout - elapsed
                    logging.warning(f"Circuit breaker active - skipping request (resets in {remaining:.0f}s)")
                    return None
            else:
                logging.warning("Circuit breaker active - skipping request")
                return None

        for attempt in range(self.max_retries):
            try:
                response = requests.request(method, url, **kwargs)

                if response.status_code == 200:
                    self.consecutive_failures = 0
                    return response.json()
                elif response.status_code == 400:
                    # Bad request - don't retry
                    logging.debug(f"Bad request (400): {url}")
                    return None
                else:
                    logging.warning(f"Request failed: {response.status_code}")

            except Exception as e:
                if self._should_retry(e) and attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logging.debug(f"Retrying after {wait_time}s due to: {e}")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.debug(f"Request failed: {e}")
                    self.consecutive_failures += 1

                    # Activate circuit breaker if too many failures
                    if self.consecutive_failures >= self.circuit_breaker_threshold:
                        self.circuit_breaker_active = True
                        self.circuit_breaker_activated_at = time.time()  # FIXED: Issue #15 - Track activation time
                        logging.error(f"Circuit breaker activated - too many API failures (will auto-reset in {self.circuit_breaker_timeout}s)")

                    return None

        return None

    def get_options_chains(self, symbol: str, provider='yfinance') -> Optional[Dict]:
        """
        Get complete options chain with Greeks and IV

        IMPORTANT: YFinance provider does NOT include Greeks in the response.
        This method automatically calculates Greeks using Black-Scholes model.
        """
        url = f'{self.base_url}/derivatives/options/chains'
        params = {'symbol': symbol, 'provider': provider}

        result = self._handle_request('GET', url, params=params, timeout=30)
        time.sleep(0.1)  # Rate limiting

        # Calculate Greeks if not provided by API
        if result and 'results' in result:
            try:
                # Get current stock price for Greeks calculation
                quote_data = self.get_quote(symbol)
                if quote_data and 'results' in quote_data:
                    quote_results = quote_data['results']
                    if isinstance(quote_results, list) and len(quote_results) > 0:
                        spot_price = quote_results[0].get('last_price') or quote_results[0].get('close')
                    elif isinstance(quote_results, dict):
                        spot_price = quote_results.get('last_price') or quote_results.get('close')
                    else:
                        spot_price = None

                    if spot_price and spot_price > 0:
                        # Calculate Greeks for all options in chain
                        options_list = result['results']
                        if isinstance(options_list, list):
                            result['results'] = GreeksCalculator.add_greeks_to_options_chain(
                                options_list, spot_price
                            )
                            logging.debug(f"{symbol}: Calculated Greeks using Black-Scholes (spot=${spot_price:.2f})")
            except Exception as e:
                logging.debug(f"Could not calculate Greeks for {symbol}: {e}")

        return result

    def get_options_expirations(self, symbol: str) -> List[datetime]:
        """Get available expiration dates for a symbol"""
        try:
            # Try to extract from options chain
            chain = self.get_options_chains(symbol)
            if chain and 'results' in chain:
                expirations = set()
                for option in chain['results']:
                    exp = option.get('expiration')
                    if exp:
                        try:
                            exp_date = datetime.fromisoformat(exp.replace('Z', '+00:00'))
                            expirations.add(exp_date)
                        except:
                            pass
                return sorted(list(expirations))
            return []
        except Exception as e:
            logging.error(f"Error getting expirations for {symbol}: {e}")
            return []

    def get_historical_price(self, symbol: str, days=60) -> Optional[Dict]:
        """Get historical price data"""
        url = f'{self.base_url}/equity/price/historical'
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        params = {
            'symbol': symbol,
            'start_date': start_date,
            'end_date': end_date,
            'provider': 'yfinance'
        }

        result = self._handle_request('GET', url, params=params, timeout=30)
        time.sleep(0.1)
        return result

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get current quote data"""
        url = f'{self.base_url}/equity/price/quote'
        params = {'symbol': symbol, 'provider': 'yfinance'}

        result = self._handle_request('GET', url, params=params, timeout=20)
        time.sleep(0.05)
        return result

    # ========================================================================
    # SPRINT 1: QUICK WINS - New endpoints for enhanced scanning
    # ========================================================================

    def get_technical_vwap(self, symbol: str, days: int = 1) -> Optional[Dict]:
        """
        Get Volume-Weighted Average Price (VWAP)

        VWAP is THE most important intraday indicator institutions watch.
        Price above VWAP = bullish, below = bearish

        Args:
            symbol: Stock symbol
            days: Lookback period (default 1 day for intraday)

        Returns:
            Dict with VWAP data or None if failed
        """
        url = f'{self.base_url}/technical/vwap'
        params = {'symbol': symbol, 'provider': 'yfinance', 'interval': '1m'}

        result = self._handle_request('GET', url, params=params, timeout=15)
        time.sleep(0.1)  # Rate limiting
        return result

    def get_technical_rsi(self, symbol: str, period: int = 14) -> Optional[Dict]:
        """
        Get Relative Strength Index (RSI)

        RSI > 70 = overbought, RSI < 30 = oversold

        Args:
            symbol: Stock symbol
            period: RSI period (default 14)

        Returns:
            Dict with RSI data or None if failed
        """
        url = f'{self.base_url}/technical/rsi'
        params = {'symbol': symbol, 'provider': 'yfinance', 'period': period}

        result = self._handle_request('GET', url, params=params, timeout=15)
        time.sleep(0.1)  # Rate limiting
        return result

    def get_technical_atr(self, symbol: str, period: int = 14) -> Optional[Dict]:
        """
        Get Average True Range (ATR) - volatility indicator

        ATR measures stock volatility, used for dynamic stop loss sizing

        Args:
            symbol: Stock symbol
            period: ATR period (default 14)

        Returns:
            Dict with ATR data or None if failed
        """
        url = f'{self.base_url}/technical/atr'
        params = {'symbol': symbol, 'provider': 'yfinance', 'period': period}

        result = self._handle_request('GET', url, params=params, timeout=15)
        time.sleep(0.1)  # Rate limiting
        return result

    def get_market_indices(self) -> Optional[Dict]:
        """
        Get major market indices including VIX

        VIX > 25 = high fear (sell premium), VIX < 15 = low fear (buy premium)

        Returns:
            Dict with index data including VIX or None if failed
        """
        url = f'{self.base_url}/index/market'
        params = {'provider': 'yfinance'}

        result = self._handle_request('GET', url, params=params, timeout=15)
        time.sleep(0.1)  # Rate limiting
        return result

    def get_vix(self) -> Optional[float]:
        """
        Get current VIX (CBOE Volatility Index)

        Tries multiple approaches:
        1. Equity quote with ^VIX symbol (yfinance)
        2. Equity quote with VIX symbol (yfinance)
        3. Index market endpoint
        4. FRED economic data (VIXCLS series)

        Returns:
            VIX value as float or None if failed
        """
        try:
            # Approach 1: Try as ^VIX with yfinance
            url = f'{self.base_url}/equity/price/quote'
            params = {'symbol': '^VIX', 'provider': 'yfinance'}

            result = self._handle_request('GET', url, params=params, timeout=10)
            time.sleep(0.1)

            if result and 'results' in result:
                results = result['results']
                if isinstance(results, list) and len(results) > 0:
                    vix_val = results[0].get('last_price') or results[0].get('close') or results[0].get('price')
                    if vix_val and vix_val > 0:
                        logging.debug(f"VIX fetched via ^VIX symbol: {vix_val}")
                        return float(vix_val)
                elif isinstance(results, dict):
                    vix_val = results.get('last_price') or results.get('close') or results.get('price')
                    if vix_val and vix_val > 0:
                        logging.debug(f"VIX fetched via ^VIX symbol: {vix_val}")
                        return float(vix_val)

            # Approach 2: Try with VIX (no caret)
            params = {'symbol': 'VIX', 'provider': 'yfinance'}
            result = self._handle_request('GET', url, params=params, timeout=10)
            time.sleep(0.1)

            if result and 'results' in result:
                results = result['results']
                if isinstance(results, list) and len(results) > 0:
                    vix_val = results[0].get('last_price') or results[0].get('close') or results[0].get('price')
                    if vix_val and vix_val > 0:
                        logging.debug(f"VIX fetched via VIX symbol: {vix_val}")
                        return float(vix_val)
                elif isinstance(results, dict):
                    vix_val = results.get('last_price') or results.get('close') or results.get('price')
                    if vix_val and vix_val > 0:
                        logging.debug(f"VIX fetched via VIX symbol: {vix_val}")
                        return float(vix_val)

            # Approach 3: Try index endpoint
            url = f'{self.base_url}/index/price/historical'
            params = {'symbol': '^VIX', 'provider': 'yfinance'}
            result = self._handle_request('GET', url, params=params, timeout=10)
            time.sleep(0.1)

            if result and 'results' in result:
                results = result['results']
                if isinstance(results, list) and len(results) > 0:
                    # Get most recent close price
                    vix_val = results[-1].get('close') if results else None
                    if vix_val and vix_val > 0:
                        logging.debug(f"VIX fetched via index endpoint: {vix_val}")
                        return float(vix_val)

            # Approach 4: Try FRED economic data (VIXCLS - daily VIX closing values)
            url = f'{self.base_url}/economy/fred_series'
            params = {'symbol': 'VIXCLS', 'provider': 'fred', 'limit': 1}
            result = self._handle_request('GET', url, params=params, timeout=10)
            time.sleep(0.1)

            if result and 'results' in result:
                results = result['results']
                if isinstance(results, list) and len(results) > 0:
                    # FRED returns data points with 'value' field
                    vix_val = results[-1].get('value') if results else None
                    if vix_val and vix_val > 0:
                        logging.debug(f"VIX fetched via FRED VIXCLS: {vix_val}")
                        return float(vix_val)

            logging.warning("Could not fetch VIX from any source (^VIX, VIX, index, FRED)")
            return None

        except Exception as e:
            logging.warning(f"Could not fetch VIX from any source")
            logging.debug(f"VIX fetch error details: {e}")
            return None

    def get_equity_profile(self, symbol: str) -> Optional[Dict]:
        """
        Get company profile including sector, industry, beta

        Used for sector rotation tracking and beta-adjusted analysis

        Args:
            symbol: Stock symbol

        Returns:
            Dict with company profile data or None if failed
        """
        url = f'{self.base_url}/equity/profile'
        params = {'symbol': symbol, 'provider': 'yfinance'}

        result = self._handle_request('GET', url, params=params, timeout=15)
        time.sleep(0.1)  # Rate limiting
        return result

    def get_sector_performance(self) -> Optional[Dict]:
        """
        Get sector performance for rotation tracking

        Identifies which sectors are getting institutional flow

        Returns:
            Dict with sector performance data or None if failed
        """
        url = f'{self.base_url}/index/sectors'
        params = {'provider': 'yfinance'}

        result = self._handle_request('GET', url, params=params, timeout=15)
        time.sleep(0.1)  # Rate limiting
        return result

    def reset_circuit_breaker(self):
        """Reset circuit breaker (called periodically or auto-reset after timeout)"""
        if self.circuit_breaker_active:
            logging.info("Resetting circuit breaker")
            self.circuit_breaker_active = False
            self.circuit_breaker_activated_at = None  # FIXED: Issue #15 - Clear activation time
            self.consecutive_failures = 0
