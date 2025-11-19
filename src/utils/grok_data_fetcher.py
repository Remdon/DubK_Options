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

        prompt = f"""SEARCH THE WEB RIGHT NOW for today's unusual options activity. Visit these sites and extract real data:

1. https://www.barchart.com/options/unusual-activity
2. https://www.marketbeat.com/originals/unusual-options-activity/
3. https://finviz.com/ (screener with unusual volume)
4. Search X/Twitter for "#unusualoptions" or "unusual options activity" posts from today

Extract the ACTUAL unusual options trades being reported TODAY. Look for:
- Large block trades (premium > ${min_premium:,})
- Option sweeps (aggressive multi-exchange orders)
- High volume relative to open interest

Return ONLY valid JSON with REAL data you find, no explanations or markdown:

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
- Classify sentiment as "BULLISH" (calls) or "BEARISH" (puts)
- Include actual volume and open_interest from the sources
- trade_type: "sweep", "block", or "unknown"
- If you find NO unusual activity today, return: {{"results": []}}
- DO NOT make up data - only return what you actually find on the web
- DO NOT include any text outside the JSON structure"""

        try:
            logging.info("[GROK] Calling Grok API for unusual options...")
            response_data = self._call_grok_api(prompt)

            if response_data and 'results' in response_data:
                results = response_data['results']
                logging.info(f"[GROK] Fetched {len(results)} unusual options trades")

                # Cache results
                self._cache_unusual_options(results)

                return results
            elif response_data:
                # Got a response but no 'results' key - log what we got
                logging.warning(f"[GROK] Unexpected response structure: {response_data}")
                return []
            else:
                logging.warning("[GROK] No response data from Grok API (returned None)")
                return []

        except Exception as e:
            logging.error(f"[GROK] Error fetching unusual options: {e}", exc_info=True)
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

        prompt = f"""SEARCH THE WEB RIGHT NOW for upcoming earnings announcements from {today} to {end_date}. Visit these sites and extract real data:

1. https://finance.yahoo.com/calendar/earnings (most comprehensive free source)
2. https://www.marketbeat.com/earnings/ (detailed earnings calendar)
3. https://www.nasdaq.com/market-activity/earnings (official NASDAQ calendar)
4. Search X/Twitter for earnings announcements and company IR pages

Extract the ACTUAL earnings dates being reported for the next {upcoming_days} days. Look for:
- Major companies (market cap > $2B)
- Confirmed earnings dates
- Timing (before market/after market/during market)
- EPS estimates if available

Return ONLY valid JSON with REAL data you find on the web:

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
- report_time: "BMO" (before market open), "AMC" (after market close), or "UNKNOWN"
- Include eps_estimate and revenue_estimate from analyst consensus (null if not available)
- If you find NO earnings in the date range, return: {{"results": []}}
- DO NOT make up data - only return what you actually find on the web
- DO NOT include any text outside the JSON structure
- Focus on US stocks (NYSE, NASDAQ)"""

        try:
            logging.info("[GROK] Calling Grok API for earnings calendar...")
            response_data = self._call_grok_api(prompt)

            if response_data and 'results' in response_data:
                results = response_data['results']
                logging.info(f"[GROK] Fetched {len(results)} earnings events")

                # Cache results
                self._cache_earnings(results, upcoming_days)

                return results
            elif response_data:
                # Got a response but no 'results' key - log what we got
                logging.warning(f"[GROK] Unexpected earnings response structure: {response_data}")
                return []
            else:
                logging.warning("[GROK] No response data from Grok API for earnings (returned None)")
                return []

        except Exception as e:
            logging.error(f"[GROK] Error fetching earnings calendar: {e}", exc_info=True)
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
            'model': 'grok-4-fast-non-reasoning',  # Grok 4 Fast (2M context, web search, cost-efficient)
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
                    logging.info(f"[GROK] Raw response (first 500 chars): {content[:500]}")

                    # Try to extract JSON from response
                    json_data = self._extract_json(content)

                    if json_data:
                        logging.info(f"[GROK] Successfully parsed JSON with {len(json_data.get('results', []))} results")
                        return json_data
                    else:
                        logging.warning(f"[GROK] Could not parse JSON from response (attempt {attempt + 1}/{max_retries})")
                        logging.warning(f"[GROK] Full response content: {content}")

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
