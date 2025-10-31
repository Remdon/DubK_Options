"""
Centralized Configuration Management
=====================================

All configurable parameters in one place with validation and type hints.
"""

import os
from typing import Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# ENVIRONMENT VARIABLES CONFIG
# =============================================================================

class Config:
    """Centralized configuration with validation and environment variable loading"""

    def __init__(self):
        # =====================================================================
        # API CONFIGURATION
        # =====================================================================
        self.XAI_API_KEY = os.getenv('XAI_API_KEY')
        self.XAI_BASE_URL = os.getenv('XAI_BASE_URL', 'https://api.x.ai/v1/chat/completions')
        self.ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
        self.ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
        self.ALPACA_MODE = os.getenv('ALPACA_MODE', 'paper').lower()
        self.OPENBB_API_URL = os.getenv('OPENBB_API_URL', 'http://127.0.0.1:6900/api/v1')

        # =====================================================================
        # ALERTING CONFIGURATION
        # =====================================================================
        self.ALERT_EMAIL = os.getenv('ALERT_EMAIL')
        self.ALERT_WEBHOOK = os.getenv('ALERT_WEBHOOK')

        # =====================================================================
        # TRADING PARAMETERS
        # =====================================================================
        self.MAX_POSITION_PCT = float(os.getenv('MAX_POSITION_PCT', '0.15'))  # 15%
        self.MAX_SYMBOL_EXPOSURE = float(os.getenv('MAX_SYMBOL_EXPOSURE', '0.25'))  # 25%
        self.MAX_SECTOR_EXPOSURE = float(os.getenv('MAX_SECTOR_EXPOSURE', '0.40'))  # 40%
        self.MAX_TOTAL_POSITIONS = int(os.getenv('MAX_TOTAL_POSITIONS', '10'))
        self.MAX_PORTFOLIO_DELTA = float(os.getenv('MAX_PORTFOLIO_DELTA', '100'))
        self.MAX_PORTFOLIO_THETA = float(os.getenv('MAX_PORTFOLIO_THETA', '-500'))

        # =====================================================================
        # STRATEGY-SPECIFIC STOP LOSSES
        # =====================================================================
        # NOTE: These config values are currently NOT USED by position_manager.py
        # position_manager.py has hardcoded values. This config is kept for future
        # centralized configuration migration.
        # TIER 1 EXPERT TRADER RULES: -75% stop loss for spreads (prevents premature exits)
        self.STOP_LOSSES: Dict[str, float] = {
            'LONG_CALL': float(os.getenv('LONG_CALL_STOP_LOSS', '-0.25')),
            'LONG_PUT': float(os.getenv('LONG_PUT_STOP_LOSS', '-0.25')),
            'SHORT_CALL': float(os.getenv('SHORT_CALL_STOP_LOSS', '-0.50')),
            'SHORT_PUT': float(os.getenv('SHORT_PUT_STOP_LOSS', '-0.50')),
            'BULL_CALL_SPREAD': float(os.getenv('BULL_CALL_SPREAD_STOP_LOSS', '-0.75')),  # TIER 1: Updated to -75%
            'BEAR_PUT_SPREAD': float(os.getenv('BEAR_PUT_SPREAD_STOP_LOSS', '-0.75')),   # TIER 1: Updated to -75%
            'BULL_PUT_SPREAD': float(os.getenv('BULL_PUT_SPREAD_STOP_LOSS', '-0.75')),   # TIER 1: Updated to -75%
            'BEAR_CALL_SPREAD': float(os.getenv('BEAR_CALL_SPREAD_STOP_LOSS', '-0.75')), # TIER 1: Updated to -75%
            'IRON_CONDOR': float(os.getenv('IRON_CONDOR_STOP_LOSS', '-0.50')),
            'IRON_BUTTERFLY': float(os.getenv('IRON_BUTTERFLY_STOP_LOSS', '-0.50')),
            'LONG_STRADDLE': float(os.getenv('LONG_STRADDLE_STOP_LOSS', '-0.30')),
            'LONG_STRANGLE': float(os.getenv('LONG_STRANGLE_STOP_LOSS', '-0.30')),
            'STRADDLE': float(os.getenv('STRADDLE_STOP_LOSS', '-0.30')),
            'STRANGLE': float(os.getenv('STRANGLE_STOP_LOSS', '-0.30')),
            'SHORT_STRADDLE': float(os.getenv('SHORT_STRADDLE_STOP_LOSS', '-0.40')),
            'SHORT_STRANGLE': float(os.getenv('SHORT_STRANGLE_STOP_LOSS', '-0.40')),
            'BUTTERFLY_SPREAD': float(os.getenv('BUTTERFLY_SPREAD_STOP_LOSS', '-0.35')),
            'COVERED_CALL': float(os.getenv('COVERED_CALL_STOP_LOSS', '-0.15')),
            'PROTECTIVE_PUT': float(os.getenv('PROTECTIVE_PUT_STOP_LOSS', '-0.20')),
            'COLLAR': float(os.getenv('COLLAR_STOP_LOSS', '-0.20')),
        }

        # =====================================================================
        # STRATEGY-SPECIFIC EXPIRATION EXITS
        # =====================================================================
        self.DTE_EXITS: Dict[str, int] = {
            'LONG_CALL': int(os.getenv('LONG_CALL_DTE_EXIT', '7')),
            'LONG_PUT': int(os.getenv('LONG_PUT_DTE_EXIT', '7')),
            'SHORT_CALL': int(os.getenv('SHORT_CALL_DTE_EXIT', '3')),
            'SHORT_PUT': int(os.getenv('SHORT_PUT_DTE_EXIT', '3')),
            'BULL_CALL_SPREAD': int(os.getenv('BULL_CALL_SPREAD_DTE_EXIT', '5')),
            'BEAR_PUT_SPREAD': int(os.getenv('BEAR_PUT_SPREAD_DTE_EXIT', '5')),
            'BULL_PUT_SPREAD': int(os.getenv('BULL_PUT_SPREAD_DTE_EXIT', '5')),
            'BEAR_CALL_SPREAD': int(os.getenv('BEAR_CALL_SPREAD_DTE_EXIT', '5')),
            'IRON_CONDOR': int(os.getenv('IRON_CONDOR_DTE_EXIT', '7')),
            'IRON_BUTTERFLY': int(os.getenv('IRON_BUTTERFLY_DTE_EXIT', '7')),
            'LONG_STRADDLE': int(os.getenv('LONG_STRADDLE_DTE_EXIT', '7')),
            'LONG_STRANGLE': int(os.getenv('LONG_STRANGLE_DTE_EXIT', '7')),
            'STRADDLE': int(os.getenv('STRADDLE_DTE_EXIT', '7')),
            'STRANGLE': int(os.getenv('STRANGLE_DTE_EXIT', '7')),
            'SHORT_STRADDLE': int(os.getenv('SHORT_STRADDLE_DTE_EXIT', '5')),
            'SHORT_STRANGLE': int(os.getenv('SHORT_STRANGLE_DTE_EXIT', '5')),
            'BUTTERFLY_SPREAD': int(os.getenv('BUTTERFLY_SPREAD_DTE_EXIT', '5')),
            'COVERED_CALL': int(os.getenv('COVERED_CALL_DTE_EXIT', '3')),
            'PROTECTIVE_PUT': int(os.getenv('PROTECTIVE_PUT_DTE_EXIT', '7')),
            'COLLAR': int(os.getenv('COLLAR_DTE_EXIT', '5')),
        }

        # =====================================================================
        # SCANNER PARAMETERS
        # =====================================================================
        self.UNIVERSE_SOURCES = [
            'active', 'unusual_volume', 'gainers', 'losers',
            'most_volatile', 'oversold', 'overbought'
        ]

        self.UNIVERSE_LIMITS: Dict[str, int] = {
            'active': int(os.getenv('ACTIVE_LIMIT', '30')),
            'unusual_volume': int(os.getenv('UNUSUAL_VOLUME_LIMIT', '30')),
            'gainers': int(os.getenv('GAINERS_LIMIT', '25')),
            'losers': int(os.getenv('LOSERS_LIMIT', '25')),
            'most_volatile': int(os.getenv('VOLATILE_LIMIT', '25')),
            'oversold': int(os.getenv('OVERSOLD_LIMIT', '20')),
            'overbought': int(os.getenv('OVERBOUGHT_LIMIT', '20')),
        }

        self.SECTOR_CAPS: Dict[str, int] = {
            'Technology': int(os.getenv('TECH_SECTOR_CAP', '7')),
            'Financial Services': int(os.getenv('FINANCIAL_SECTOR_CAP', '7')),
            'Healthcare': int(os.getenv('HEALTHCARE_SECTOR_CAP', '7')),
            # Default for other sectors
            'default': 7
        }

        # =====================================================================
        # RISK AND LIQUIDITY PARAMETERS
        # =====================================================================
        self.SPREAD_THRESHOLDS: Dict[str, float] = {
            'excellent': float(os.getenv('EXCELLENT_SPREAD', '0.05')),
            'acceptable': float(os.getenv('ACCEPTABLE_SPREAD', '0.15')),
            'penalty_light': float(os.getenv('PENALTY_LIGHT_SPREAD', '0.20')),
            'penalty_heavy': float(os.getenv('PENALTY_HEAVY_SPREAD', '0.40'))
        }

        self.LIQUIDITY_THRESHOLDS: Dict[str, int] = {
            'min_volume': int(os.getenv('MIN_VOLUME', '50')),
            'min_open_interest': int(os.getenv('MIN_OPEN_INTEREST', '500')),
            'preferred_volume': int(os.getenv('PREFERRED_VOLUME', '10000')),
            'preferred_oi': int(os.getenv('PREFERRED_OI', '50000'))
        }

        self.PRICE_FILTERS: Dict[str, float] = {
            'min_price': float(os.getenv('MIN_PRICE', '15.00')),  # CRITICAL: Eliminates penny stocks (was 0.05)
            'max_price': float(os.getenv('MAX_PRICE', '500.0'))
        }

        # =====================================================================
        # AI AND ANALYSIS PARAMETERS
        # =====================================================================
        self.GROK_CONFIDENCE_THRESHOLDS: Dict[str, int] = {
            'execute': int(os.getenv('EXECUTE_CONFIDENCE', '75')),
            'alert': int(os.getenv('ALERT_CONFIDENCE', '90')),
            'filter': int(os.getenv('FILTER_CONFIDENCE', '30'))
        }

        self.BATCH_SIZE = int(os.getenv('GROK_BATCH_SIZE', '10'))
        self.QUALITY_GATE_SIZE = int(os.getenv('QUALITY_GATE_SIZE', '30'))

        # =====================================================================
        # TIMING AND RATE LIMITING
        # =====================================================================
        self.POSITION_CHECK_INTERVAL = int(os.getenv('POSITION_CHECK_INTERVAL', '300'))  # 5 min
        self.SCAN_INTERVAL = int(os.getenv('SCAN_INTERVAL', '1800'))  # 30 min
        self.CACHE_EXPIRY: Dict[str, int] = {
            'iv_metrics': int(os.getenv('IV_CACHE_EXPIRY', '3600')),  # 1 hour
            'technical': int(os.getenv('TECH_CACHE_EXPIRY', '900')),  # 15 min
            'regime': int(os.getenv('REGIME_CACHE_EXPIRY', '1800')),  # 30 min
            'flow': int(os.getenv('FLOW_CACHE_EXPIRY', '3600')),     # 1 hour
            'earnings': int(os.getenv('EARNINGS_CACHE_EXPIRY', '14400'))  # 4 hours
        }

        # =====================================================================
        # PERFORMANCE AND TRACKING
        # =====================================================================
        self.DB_PATH = os.getenv('DB_PATH', 'trades.db')
        self.LOG_LEVELS: Dict[str, str] = {
            'file': os.getenv('FILE_LOG_LEVEL', 'DEBUG'),
            'console': os.getenv('CONSOLE_LOG_LEVEL', 'WARNING'),
            'grok': os.getenv('GROK_LOG_LEVEL', 'DEBUG')
        }

        self.LOG_FILES: Dict[str, str] = {
            'main': os.getenv('MAIN_LOG_FILE', 'logs/openbb_options_bot.log'),
            'grok': os.getenv('GROK_LOG_FILE', 'logs/grok_interactions.log')
        }

        # =====================================================================
        # EMERGENCY AND SAFETY PARAMETERS
        # =====================================================================
        self.CIRCUIT_BREAKER: Dict[str, int] = {
            'max_failures': int(os.getenv('CIRCUIT_BREAKER_MAX_FAILURES', '10')),
            'timeout': int(os.getenv('CIRCUIT_BREAKER_TIMEOUT', '600'))  # 10 min
        }

        self.RETRY_CONFIG: Dict[str, Any] = {
            'max_attempts': int(os.getenv('MAX_RETRY_ATTEMPTS', '3')),
            'base_delay': float(os.getenv('RETRY_BASE_DELAY', '1.0')),
            'backoff_factor': float(os.getenv('RETRY_BACKOFF_FACTOR', '2.0'))
        }

        self.ALERT_THROTTLE = int(os.getenv('ALERT_THROTTLE_SECONDS', '300'))  # 5 min

        # =====================================================================
        # EARNINGS AND ECONOMIC CALENDAR
        # =====================================================================
        self.EARNINGS_RISK_LEVELS: Dict[str, Dict] = {
            'CRITICAL': {'days': 3, 'action': 'AVOID'},
            'MODERATE': {'days': 7, 'action': 'CAUTION'},
            'LOW': {'days': 14, 'action': 'PROCEED'}
        }

    def validate_config(self) -> List[str]:
        """Validate configuration and return list of issues"""
        issues = []

        # Required environment variables
        required_vars = ['XAI_API_KEY', 'ALPACA_API_KEY', 'ALPACA_SECRET_KEY']
        for var in required_vars:
            if not getattr(self, var):
                issues.append(f"Missing required environment variable: {var}")

        # Validate ALPACA_MODE
        if self.ALPACA_MODE not in ['paper', 'live']:
            issues.append(f"ALPACA_MODE must be 'paper' or 'live', got: {self.ALPACA_MODE}")

        # Validate numeric ranges
        if not 0 < self.MAX_POSITION_PCT <= 1:
            issues.append(f"MAX_POSITION_PCT must be between 0 and 1, got: {self.MAX_POSITION_PCT}")

        if self.MAX_TOTAL_POSITIONS < 1:
            issues.append(f"MAX_TOTAL_POSITIONS must be >= 1, got: {self.MAX_TOTAL_POSITIONS}")

        return issues

    def get_sector_cap(self, sector: str) -> int:
        """Get sector exposure cap, with default fallback"""
        return self.SECTOR_CAPS.get(sector, self.SECTOR_CAPS['default'])

    def get_strategy_stop_loss(self, strategy: str) -> float:
        """Get strategy-specific stop loss, with default fallback"""
        return self.STOP_LOSSES.get(strategy, -0.30)  # Default 30% stop loss

    def get_strategy_dte_exit(self, strategy: str) -> int:
        """Get strategy-specific DTE exit threshold, with default fallback"""
        return self.DTE_EXITS.get(strategy, 5)  # Default 5 days exit

    def is_paper_mode(self) -> bool:
        """Check if running in paper trading mode"""
        return self.ALPACA_MODE == 'paper'


# Global config instance
config = Config()
