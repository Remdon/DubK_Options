"""
Circuit Breaker, API Cache, and Rate Limiter Utilities

Provides robust API interaction utilities:
- Circuit breaker pattern for fault tolerance
- API response caching
- Rate limiting to prevent API throttling
"""

import logging
import time
from typing import Dict, Optional, Callable, Any
from datetime import datetime
from collections import deque


class CircuitBreaker:
    """Circuit breaker to prevent repeated API failures"""

    def __init__(self, max_failures=10, timeout_seconds=300):
        self.max_failures = max_failures
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time = 0
        self.active = False

    def record_failure(self):
        """Record an API failure"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.max_failures:
            self.active = True
            logging.error(f"Circuit breaker activated - {self.failure_count} failures")

    def record_success(self):
        """Record successful request"""
        self.failure_count = max(0, self.failure_count - 1)
        if self.failure_count == 0:
            self.active = False

    def should_allow_request(self) -> bool:
        """Check if request should be allowed"""
        if self.active:
            # Check if timeout has expired
            if time.time() - self.last_failure_time > self.timeout_seconds:
                self.active = False
                self.failure_count = 0
                logging.info("Circuit breaker reset - timeout expired")
            else:
                return False
        return True


class APICache:
    """API response caching to prevent redundant calls"""

    def __init__(self, max_age_seconds=3600):
        self.cache = {}
        self.max_age = max_age_seconds

    def get(self, key: str):
        """Get cached value if not expired"""
        if key in self.cache:
            timestamp, data = self.cache[key]
            if time.time() - timestamp < self.max_age:
                return data
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value):
        """Cache value with timestamp"""
        self.cache[key] = (time.time(), value)

        # Clean up old entries to prevent memory growth
        if len(self.cache) > 1000:
            current_time = time.time()
            to_remove = [k for k, (t, _) in self.cache.items() if current_time - t > self.max_age]
            for k in to_remove:
                del self.cache[k]


class RateLimiter:
    """API rate limiting"""

    def __init__(self, requests_per_minute=30):
        self.requests_per_minute = requests_per_minute
        self.request_times = deque(maxlen=requests_per_minute)

    def wait_if_needed(self):
        """Wait if rate limit would be exceeded"""
        current_time = time.time()

        # Remove requests older than 60 seconds
        while self.request_times and current_time - self.request_times[0] > 60:
            self.request_times.popleft()

        if len(self.request_times) >= self.requests_per_minute:
            sleep_time = 60 - (current_time - self.request_times[0])
            if sleep_time > 0:
                time.sleep(sleep_time)

        self.request_times.append(current_time)


