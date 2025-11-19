"""
Grok Data Fetcher - Alternative Data Source via XAI Grok API

Uses Grok to fetch financial data from free public sources when paid APIs are unavailable.
Provides fallback for:
- Unusual options activity (replaces Intrinio)
- Earnings calendar (replaces FMP/Nasdaq)

Cost: ~$3-5/month vs $79-299/month for paid OpenBB providers
"""

import logging
import requests
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import sqlite3
import os


class GrokDataFetcher:
    """
    Fetches financial data using XAI Grok API from free public sources.

    Implements smart caching to minimize API costs:
    - Unusual options: 1-hour cache
    - Earnings calendar: 24-hour cache
    """

    def __init__(self, api_key: str, base_url: str = 'https://api.x.ai/v1/chat/completions'):
        """
        Initialize Grok data fetcher

        Args:
            api_key: XAI API key
            base_url: XAI API endpoint
        """
        self.api_key = api_key
        self.base_url = base_url
        self.cache_db = 'data_cache.db'
        self._init_cache_db()

    def _init_cache_db(self):
        """Initialize cache database"""
        conn = sqlite3.connect(self.cache_db)

        # Unusual options cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unusual_options_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cached_at TEXT NOT NULL,
                data TEXT NOT NULL
            )
        """)

        # Earnings calendar cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS earnings_calendar_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cached_at TEXT NOT NULL,
                days_ahead INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)

        conn.commit()
        conn.close()

    def fetch_unusual_options(self, min_premium: int = 100000) -> List[Dict]:
        """
        Fetch unusual options activity from free sources via Grok

        Sources: Barchart, MarketBeat, Finviz
        Cache: 1 hour

        Args:
            min_premium: Minimum premium for unusual trades ($)

        Returns:
            List of unusual options trades in OpenBB-compatible format
        """
        # Check cache first (1 hour TTL)
        cached = self._get_cached_unusual_options()
        if cached:
            logging.info("[GROK] Using cached unusual options data")
            return cached

        logging.info("[GROK] Fetching unusual options activity from free sources...")

        prompt = f"""You are a financial data API. Fetch TODAY's unusual options activity from free public sources (Barchart.com unusual activity page, MarketBeat unusual options, or Finviz screener).

IMPORTANT: Return ONLY valid JSON, no explanations or markdown. Use this EXACT format:

{{
  "results": [
    {{
      "symbol": "TSLA",
      "contract_symbol": "TSLA250117C00300000",
      "total_premium": 1500000,
      "volume": 5000,
      "open_interest": 2000,
      "sentiment": "BULLISH",
      "trade_type": "sweep"
    }}
  ]
}}

Requirements:
- Only include trades with total premium > ${min_premium:,}
- Classify sentiment as "BULLISH" (calls/bullish puts) or "BEARISH" (puts/bearish calls)
- Include volume and open_interest (estimate if not available)
- trade_type should be "sweep", "block", or "unknown"
- Return empty results array if no data available: {{"results": []}}
- DO NOT include any text outside the JSON structure"""

        try:
            response_data = self._call_grok_api(prompt)

            if response_data and 'results' in response_data:
                results = response_data['results']
                logging.info(f"[GROK] Fetched {len(results)} unusual options trades")

                # Cache results
                self._cache_unusual_options(results)

                return results
            else:
                logging.warning("[GROK] No unusual options data returned")
                return []

        except Exception as e:
            logging.error(f"[GROK] Error fetching unusual options: {e}")
            return []

    def fetch_earnings_calendar(self, upcoming_days: int = 30) -> List[Dict]:
        """
        Fetch earnings calendar from free sources via Grok

        Sources: Yahoo Finance, MarketBeat, Nasdaq
        Cache: 24 hours

        Args:
            upcoming_days: Number of days ahead to fetch

        Returns:
            List of earnings events in OpenBB-compatible format
        """
        # Check cache first (24 hour TTL)
        cached = self._get_cached_earnings(upcoming_days)
        if cached:
            logging.info("[GROK] Using cached earnings calendar data")
            return cached

        logging.info(f"[GROK] Fetching earnings calendar for next {upcoming_days} days from free sources...")

        today = datetime.now().strftime('%Y-%m-%d')
        end_date = (datetime.now() + timedelta(days=upcoming_days)).strftime('%Y-%m-%d')

        prompt = f"""You are a financial data API. Fetch upcoming earnings dates from {today} to {end_date} from free public sources (Yahoo Finance earnings calendar, MarketBeat earnings, or Nasdaq earnings calendar).

IMPORTANT: Return ONLY valid JSON, no explanations or markdown. Use this EXACT format:

{{
  "results": [
    {{
      "symbol": "TSLA",
      "report_date": "2025-01-29",
      "report_time": "AMC",
      "eps_estimate": 1.25,
      "revenue_estimate": 25000000000
    }}
  ]
}}

Requirements:
- Only include symbols with market cap > $2 billion
- report_time must be "BMO" (before market open), "AMC" (after market close), or "UNKNOWN"
- Include eps_estimate and revenue_estimate if available (use null if not available)
- Return empty results array if no earnings in date range: {{"results": []}}
- DO NOT include any text outside the JSON structure"""

        try:
            response_data = self._call_grok_api(prompt)

            if response_data and 'results' in response_data:
                results = response_data['results']
                logging.info(f"[GROK] Fetched {len(results)} earnings events")

                # Cache results
                self._cache_earnings(results, upcoming_days)

                return results
            else:
                logging.warning("[GROK] No earnings calendar data returned")
                return []

        except Exception as e:
            logging.error(f"[GROK] Error fetching earnings calendar: {e}")
            return []

    def _call_grok_api(self, prompt: str, max_retries: int = 3) -> Optional[Dict]:
        """
        Call Grok API with retry logic

        Args:
            prompt: Prompt to send to Grok
            max_retries: Number of retry attempts

        Returns:
            Parsed JSON response or None
        """
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        payload = {
            'model': 'grok-2-1212',  # Use grok-2-1212 (latest stable model with web access)
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 4000,
            'temperature': 0.3  # Low temperature for factual data extraction
        }

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    timeout=120
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')

                    # Log raw response for debugging
                    logging.debug(f"[GROK] Raw response: {content[:200]}...")

                    # Try to extract JSON from response
                    json_data = self._extract_json(content)

                    if json_data:
                        return json_data
                    else:
                        logging.warning(f"[GROK] Could not parse JSON from response (attempt {attempt + 1}/{max_retries})")

                elif response.status_code == 429:
                    # Rate limited - wait and retry
                    wait_time = (attempt + 1) * 2
                    logging.warning(f"[GROK] Rate limited, waiting {wait_time}s...")
                    import time
                    time.sleep(wait_time)
                    continue

                else:
                    logging.error(f"[GROK] API error {response.status_code}: {response.text}")

            except Exception as e:
                logging.error(f"[GROK] API call failed (attempt {attempt + 1}/{max_retries}): {e}")

        return None

    def _extract_json(self, content: str) -> Optional[Dict]:
        """
        Extract JSON from Grok response (handles markdown code blocks)

        Args:
            content: Raw response content

        Returns:
            Parsed JSON dict or None
        """
        # Remove markdown code blocks if present
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            content = content.split('```')[1].split('```')[0].strip()

        # Try to find JSON object
        try:
            # Direct parse
            return json.loads(content)
        except:
            # Try to find JSON object in text
            try:
                start = content.find('{')
                end = content.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(content[start:end])
            except:
                pass

        return None

    def _get_cached_unusual_options(self) -> Optional[List[Dict]]:
        """Get cached unusual options (1 hour TTL)"""
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.execute("""
            SELECT cached_at, data FROM unusual_options_cache
            ORDER BY cached_at DESC LIMIT 1
        """)

        row = cursor.fetchone()
        conn.close()

        if row:
            cached_at_str, data_json = row
            cached_at = datetime.fromisoformat(cached_at_str)

            # Check if cache is still valid (1 hour)
            if (datetime.now() - cached_at).total_seconds() < 3600:
                return json.loads(data_json)

        return None

    def _cache_unusual_options(self, data: List[Dict]):
        """Cache unusual options data"""
        conn = sqlite3.connect(self.cache_db)
        conn.execute("""
            INSERT INTO unusual_options_cache (cached_at, data)
            VALUES (?, ?)
        """, (datetime.now().isoformat(), json.dumps(data)))
        conn.commit()
        conn.close()

    def _get_cached_earnings(self, days_ahead: int) -> Optional[List[Dict]]:
        """Get cached earnings calendar (24 hour TTL)"""
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.execute("""
            SELECT cached_at, data FROM earnings_calendar_cache
            WHERE days_ahead = ?
            ORDER BY cached_at DESC LIMIT 1
        """, (days_ahead,))

        row = cursor.fetchone()
        conn.close()

        if row:
            cached_at_str, data_json = row
            cached_at = datetime.fromisoformat(cached_at_str)

            # Check if cache is still valid (24 hours)
            if (datetime.now() - cached_at).total_seconds() < 86400:
                return json.loads(data_json)

        return None

    def _cache_earnings(self, data: List[Dict], days_ahead: int):
        """Cache earnings calendar data"""
        conn = sqlite3.connect(self.cache_db)
        conn.execute("""
            INSERT INTO earnings_calendar_cache (cached_at, days_ahead, data)
            VALUES (?, ?, ?)
        """, (datetime.now().isoformat(), days_ahead, json.dumps(data)))
        conn.commit()
        conn.close()

    def clear_cache(self):
        """Clear all cached data"""
        conn = sqlite3.connect(self.cache_db)
        conn.execute("DELETE FROM unusual_options_cache")
        conn.execute("DELETE FROM earnings_calendar_cache")
        conn.commit()
        conn.close()
        logging.info("[GROK] Cache cleared")
