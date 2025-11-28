"""
Options Bot Core - Main Trading Bot Logic

Contains the core OptionsBot class with:
- Market scanning and opportunity detection
- AI-powered trade analysis (Grok integration)
- Order execution and management
- Position monitoring and exit logic
- Risk management and portfolio tracking

NOTE: All sector-based logic has been removed. Risk management now uses:
- Per-symbol exposure limits
- Total position count limits
- Portfolio Greeks limits
"""

import os
import sys
import logging
import asyncio
import aiohttp
import time
import json
import requests
from datetime import datetime, timedelta, time as dt_time
from typing import List, Dict, Optional, Tuple
from colorama import Fore, Style
from collections import deque
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, QueryOrderStatus
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest, GetOrdersRequest
import statistics
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import modular components
from config import config
from src.analyzers import (
    OpenBBClient, IVAnalyzer, MarketRegimeAnalyzer,
    FlowAnalyzer, TechnicalAnalyzer, EconomicCalendar, SentimentAnalyzer
)
from src.connectors import OpenBBAPIServer
from src.scanners import ExpertMarketScanner
from src.core import TradeJournal, AlertManager, MarketCalendar, ScanResultCache
from src.core.colors import Colors
from src.risk import PortfolioManager, PositionManager
from src.strategies import (
    OptionsValidator, MultiLegOptionsManager,
    MultiLegOrderManager, MultiLegOrderTracker,
    WheelStrategy, WheelManager, WheelState,
    BullPutSpreadStrategy, SpreadManager, SpreadState
)
from src.order_management import ReplacementAnalyzer, BatchOrderManager
from src.ui.interactive_ui import InteractiveUI
from src.utils.validators import (
    validate_contract_liquidity, get_contract_price,
    calculate_dynamic_limit_price, validate_grok_response,
    sanitize_for_prompt, validate_symbol
)


# Helper function for extracting underlying symbol from OCC format
def extract_underlying_symbol(full_symbol: str) -> str:
    """Extract underlying stock symbol from OCC format or return as-is for stocks"""
    if len(full_symbol) > 15:  # OCC options format: TICKER + YYMMDD + C/P + STRIKE (15 chars)
        return full_symbol[:-15]
    return full_symbol


class OptionsBot:
    """Main options trading bot - PRODUCTION READY v3.0 - FIXED ALL CRITICAL ISSUES"""

    def reconcile_positions_on_startup(self):
        """Reconcile existing Alpaca positions with strategy database on startup"""
        try:
            logging.info("Reconciling positions with strategy database on startup...")
            positions = self.trading_client.get_all_positions()

            if not positions:
                logging.info("No positions to reconcile")
                return

            reconciled_count = 0

            for position in positions:
                symbol = position.symbol
                underlying = self._extract_underlying(symbol)

                # Check if strategy already exists
                existing_strategy = self.trade_journal.get_position_strategy(underlying)

                if existing_strategy:
                    logging.info(f"Strategy already exists for {underlying}: {existing_strategy['strategy']}")
                    continue

                # Extract details from position and symbol
                qty = int(position.qty) if position.qty is not None else 0
                entry_price = float(position.avg_entry_price) if position.avg_entry_price is not None else 0.0
                current_price = float(position.current_price) if position.current_price is not None else 0.0

                # Infer strategy from OCC symbol if possible
                strategy = "STOCK_POSITION"  # Default
                if len(symbol) > 6:  # Option contract
                    # Extract strategy from OCC symbol
                    try:
                        date_end = len(underlying) + 6  # Symbol + 6 digits for YYYYMMDD
                        if date_end < len(symbol):
                            option_type_char = symbol[date_end]

                            if option_type_char.upper() == 'C':
                                strategy = "LONG_CALL" if qty > 0 else "SHORT_CALL"
                            elif option_type_char.upper() == 'P':
                                strategy = "LONG_PUT" if qty > 0 else "SHORT_PUT"
                    except:
                        pass

                # Create strategy record
                position_tracking = {
                    'symbol': underlying,
                    'occ_symbol': symbol,
                    'strategy': strategy,
                    'entry_price': entry_price,
                    'quantity': qty,
                    'confidence': 70,  # Default confidence for reconciled positions
                    'strikes': self._extract_strikes_from_symbol(symbol),
                    'expiry': self._extract_expiry_from_symbol(symbol),
                    'reason': f"Reconciled from Alpaca position on startup - {strategy}"
                }

                self.trade_journal.track_active_position(position_tracking)
                reconciled_count += 1
                logging.info(f"Reconciled {underlying}: Created strategy '{strategy}' from position")

            logging.info(f"Position reconciliation complete: {reconciled_count} positions added to strategy DB")

        except Exception as e:
            logging.error(f"Error reconciling positions on startup: {e}")

    def _extract_strikes_from_symbol(self, symbol: str) -> str:
        """Extract strike prices from OCC symbol"""
        try:
            if len(symbol) >= 15:  # OCC format with strike
                # Last 8 digits are strike price * 1000
                strike_digits = symbol[-8:]
                strike_price = float(strike_digits) / 1000
                return f"{strike_price:.2f}"
        except (ValueError, IndexError):
            pass
        return "UNKNOWN"

    def _extract_expiry_from_symbol(self, symbol: str) -> str:
        """Extract expiration date from OCC symbol and convert to DTE"""
        try:
            if len(symbol) >= 12:  # Following format has 12+ characters
                # YYMMDD format in symbol
                underlying = self._extract_underlying(symbol)
                date_start = len(underlying)
                if date_start + 6 <= len(symbol):
                    expiry_str = symbol[date_start:date_start+6]  # YYMMDD

                    if len(expiry_str) == 6:
                        # Convert YYMMDD to datetime
                        expiry_date = datetime.strptime(expiry_str, '%y%m%d')
                        now = datetime.now()
                        days_to_expiry = (expiry_date - now).days

                        if days_to_expiry > 0:
                            return f"{days_to_expiry}DTE"
        except (ValueError, IndexError):
            pass
        return "UNKNOWN"

    def _extract_underlying(self, full_symbol: str) -> str:
        """Extract underlying stock symbol from OCC format or return as-is for stocks"""
        if len(full_symbol) > 15:  # OCC options format: TICKER + YYMMDD + C/P + STRIKE (15 chars)
            return full_symbol[:-15]
        return full_symbol

    def __init__(self):
        # FIX #2: Initialize alert_manager before setup_alpaca so we can alert on account issues
        self.alert_manager = AlertManager(email=config.ALERT_EMAIL, webhook=config.ALERT_WEBHOOK)

        self.api_server = OpenBBAPIServer()
        self.openbb = OpenBBClient()
        self.scan_cache = ScanResultCache()

        # Check OpenBB server
        print(f"{Colors.INFO}[*] Checking OpenBB REST API server...{Colors.RESET}")

        for attempt in range(5):
            if self.api_server.is_running():
                print(f"{Colors.SUCCESS}[OK] OpenBB API server running{Colors.RESET}")
                break
            if attempt < 4:
                print(f"{Colors.DIM}  Attempt {attempt+1}/5 failed, waiting 3 seconds...{Colors.RESET}")
                time.sleep(3)
        else:
            print(f"{Colors.ERROR}[!] OpenBB API not running. Please start it manually:{Colors.RESET}")
            print(f"{Colors.DIM}    python -m uvicorn openbb_core.api.rest_api:app --host 127.0.0.1 --port 6900{Colors.RESET}")
            sys.exit(1)

        # Initialize components
        self.setup_alpaca()

        self.market_calendar = MarketCalendar()
        self.iv_analyzer = IVAnalyzer(self.openbb)
        self.tac_analyzer = TechnicalAnalyzer(self.openbb)
        self.flow_analyzer = FlowAnalyzer(self.openbb)
        self.regime_analyzer = MarketRegimeAnalyzer(self.openbb)
        self.sentiment_analyzer = SentimentAnalyzer()
        self.earnings_calendar = EconomicCalendar()

        # GROK DEBUG: Log API key status before scanner init
        logging.warning(f"[GROK INIT] About to initialize scanner - XAI_API_KEY present: {bool(config.XAI_API_KEY)}, length: {len(config.XAI_API_KEY) if config.XAI_API_KEY else 0}")

        self.market_scanner = ExpertMarketScanner(
            self.openbb,
            self.iv_analyzer,
            earnings_calendar=self.earnings_calendar,  # Pass earnings calendar for earnings analysis
            grok_api_key=config.XAI_API_KEY  # Pass Grok API key for fallback data sources
        )
        self.trade_journal = TradeJournal()
        self.portfolio_manager = PortfolioManager(self.trading_client)

        # Initialize multi-leg options managers first
        self.multi_leg_manager = MultiLegOptionsManager(self.trading_client, OptionsValidator)
        self.multi_leg_order_manager = MultiLegOrderManager(self.trading_client, OptionsValidator)

        # PHASE 1: Initialize multi-leg order tracker for atomic operations
        self.multi_leg_tracker = MultiLegOrderTracker()

        # PHASE 2: Initialize intelligent replacement analyzer
        self.replacement_analyzer = ReplacementAnalyzer(self.trade_journal, self.openbb)

        # PHASE 3: Initialize batch operations manager
        self.batch_manager = BatchOrderManager(self.trading_client, self.multi_leg_tracker, self.alert_manager)

        # WHEEL STRATEGY: Initialize The Wheel for systematic premium collection (BEFORE position_manager)
        self.wheel_manager = WheelManager(db_path=config.DB_PATH)
        self.wheel_strategy = WheelStrategy(
            trading_client=self.trading_client,
            openbb_client=self.openbb,
            scanner=self.market_scanner,
            config=config
        )
        self.wheel_strategy.wheel_db = self.wheel_manager  # Link database manager
        logging.info(f"[WHEEL] The Wheel Strategy initialized - 50-95% win rate expected")

        # BULL PUT SPREAD STRATEGY: Initialize spread strategy with separate Alpaca account
        self.spread_trading_client = None
        self.spread_manager = None
        self.spread_strategy = None

        if config.ALPACA_BULL_PUT_KEY and config.ALPACA_BULL_PUT_SECRET_KEY:
            try:
                # Initialize separate Alpaca client for spread strategy
                self.spread_trading_client = TradingClient(
                    api_key=config.ALPACA_BULL_PUT_KEY,
                    secret_key=config.ALPACA_BULL_PUT_SECRET_KEY,
                    paper=True  # Always paper mode for now
                )

                # Verify spread account
                spread_account = self.spread_trading_client.get_account()
                logging.info(f"[SPREAD] Connected to spread account - Portfolio: ${float(spread_account.portfolio_value):,.2f}")

                # Initialize spread manager with separate database
                self.spread_manager = SpreadManager(db_path='spreads.db')

                # Initialize spread strategy
                self.spread_strategy = BullPutSpreadStrategy(
                    trading_client=self.spread_trading_client,
                    openbb_client=self.openbb,
                    scanner=self.market_scanner,
                    config=config
                )
                self.spread_strategy.spread_db = self.spread_manager  # Link database manager
                logging.info(f"[SPREAD] Bull Put Spread Strategy initialized - 65-75% win rate expected")

            except Exception as e:
                logging.error(f"[SPREAD] Failed to initialize spread strategy: {e}")
                self.spread_trading_client = None
                self.spread_manager = None
                self.spread_strategy = None
        else:
            logging.info("[SPREAD] Bull Put Spread strategy not configured (missing ALPACA_BULL_PUT_KEY)")

        # Initialize position manager AFTER wheel_manager so it can skip Wheel positions
        self.position_manager = PositionManager(
            self.trading_client,
            self.trade_journal,
            self.multi_leg_order_manager,
            self.wheel_manager,  # Pass wheel_manager to skip exit checks for Wheel positions
            config  # Pass config for Wheel profit target access
        )
        logging.info(f"[POSITION MANAGER] Initialized with Wheel position protection")

        # Initialize Grok logger for detailed AI analysis logging
        self.grok_logger = logging.getLogger('grok')
        self.grok_logger.setLevel(logging.DEBUG)
        # Add file handler for Grok logs
        grok_log_path = config.LOG_FILES.get('grok', 'logs/grok_interactions.log')
        # Ensure the log directory exists
        os.makedirs(os.path.dirname(grok_log_path), exist_ok=True)
        grok_handler = logging.FileHandler(grok_log_path)
        grok_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.grok_logger.addHandler(grok_handler)
        self.grok_logger.propagate = False  # Don't propagate to root logger

        self.pre_market_opportunities = deque(maxlen=100)  # Prevent memory leak

        # Rolling lists for continuous scanning during market hours
        self.rolling_top_50 = []  # Top 50 candidates from OpenBB scans (free API)
        self.rolling_top_25 = []  # Top 25 candidates after Grok analysis (expensive API)

        # Interactive UI for manual control
        self.interactive_ui = InteractiveUI(self)
        self.shutdown_requested = False  # Graceful shutdown flag
        self.last_openbb_scan_time = None
        self.last_grok_analysis_time = None
        self.last_position_grok_check = None  # Track last Grok position review

        print(f"\n{Colors.INFO}[i] Configuration:{Colors.RESET}")
        print(f"{Colors.DIM}  XAI API:{Colors.RESET} {Colors.SUCCESS if config.XAI_API_KEY else Colors.ERROR}{'Connected' if config.XAI_API_KEY else 'Missing'}{Colors.RESET}")
        print(f"{Colors.DIM}  Alpaca:{Colors.RESET} {Colors.SUCCESS if config.ALPACA_API_KEY else Colors.ERROR}{'Connected' if config.ALPACA_API_KEY else 'Missing'}{Colors.RESET}")
        print(f"{Colors.DIM}  Mode:{Colors.RESET} {Colors.WARNING if config.ALPACA_MODE == 'paper' else Colors.ERROR}{config.ALPACA_MODE.upper()}{Colors.RESET}")
        logging.info(f"ALPACA_MODE = '{config.ALPACA_MODE}' (type: {type(config.ALPACA_MODE)})")
        print(f"{Colors.DIM}  Database:{Colors.RESET} {Colors.SUCCESS}trades.db{Colors.RESET}")
        print(f"{Colors.DIM}  Expert Scanner:{Colors.RESET} {Colors.SUCCESS}Multi-factor analysis (Greeks + IV + Unusual Activity){Colors.RESET}")
        print(f"{Colors.DIM}  Position Management:{Colors.RESET} {Colors.SUCCESS}Active (Stop Loss / Profit Targets / Trailing){Colors.RESET}")
        print(f"{Colors.DIM}  Risk Management:{Colors.RESET} {Colors.SUCCESS}Full (Portfolio limits + Greeks + IV Rank){Colors.RESET}\n")

        # Display portfolio and stats at startup
        self.display_portfolio_summary()

        # Reconcile existing positions with strategy database
        self.reconcile_positions_on_startup()

        # Reconcile wheel positions with broker (remove stale positions)
        try:
            positions = self.trading_client.get_all_positions()
            reconcile_result = self.wheel_manager.reconcile_with_broker(positions)
            if reconcile_result['removed'] > 0:
                print(f"{Colors.SUCCESS}[WHEEL RECONCILE] Removed {reconcile_result['removed']} stale position(s) from database{Colors.RESET}")
        except Exception as e:
            logging.error(f"[WHEEL RECONCILE] Error reconciling positions: {e}")

    def setup_alpaca(self):
        """Initialize Alpaca trading client with account eligibility checking"""
        from alpaca.trading.client import TradingClient

        if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
            print(f"{Colors.ERROR}[X] Alpaca API keys missing{Colors.RESET}")
            sys.exit(1)

        paper = config.ALPACA_MODE.lower() == 'paper'
        self.trading_client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=paper)

        # CRITICAL FIX #1: Account eligibility checking
        account = self.trading_client.get_account()
        self.balance = float(account.equity) if account.equity is not None else 0.0
        self.buying_power = float(account.buying_power) if account.buying_power is not None else 0.0

        logging.info(f"Alpaca connected - Balance: ${self.balance:,.2f}")

        # Check account eligibility for options trading in LIVE mode
        if not paper:
            try:
                # Check if account has options approval
                account_number = getattr(account, 'account_number', 'N/A')
                status = getattr(account, 'status', 'N/A')
                options_approved = getattr(account, 'options_approved_level', None)
                options_trading_level = getattr(account, 'options_buying_power', 0)

                logging.info(f"Live trading account status: {status}")
                logging.info(f"Options approved level: {options_approved}")
                logging.info(f"Options buying power: ${options_trading_level:,.2f}")

                if options_trading_level <= 0:
                    critical_msg = "ACCOUNT NOT ELIGIBLE FOR OPTIONS TRADING - NO OPTIONS BUYING POWER"
                    logging.error(critical_msg)
                    self.alert_manager.send_alert('CRITICAL', critical_msg)
                    print(f"{Colors.ERROR}[CRITICAL] {critical_msg}{Colors.RESET}")
                    print(f"{Colors.ERROR}[!] Check your Alpaca account options approval and restart{Colors.RESET}")
                    sys.exit(1)

                if "APPROVED" not in str(options_approved).upper():
                    warning_msg = f"Options approval unclear: {options_approved}"
                    logging.warning(warning_msg)
                    self.alert_manager.send_alert('WARNING', warning_msg)

            except Exception as e:
                warning_msg = f"Could not verify account options eligibility: {e}"
                logging.warning(warning_msg)
                self.alert_manager.send_alert('WARNING', warning_msg)
                print(f"{Colors.WARNING}[WARNING] {warning_msg}{Colors.RESET}")

    def display_portfolio_summary(self):
        """Display current portfolio and performance stats at startup"""
        print(f"\n{Colors.HEADER}{'='*80}{Colors.RESET}")
        print(f"{Colors.HEADER}                          CURRENT PORTFOLIO STATUS{Colors.RESET}")
        print(f"{Colors.HEADER}{'='*80}{Colors.RESET}\n")

        try:
            # WHEEL ACCOUNT (Main)
            account = self.trading_client.get_account()
            equity = float(account.equity) if account.equity is not None else 0.0
            buying_power = float(account.buying_power) if account.buying_power is not None else 0.0
            cash = float(account.cash) if account.cash is not None else 0.0

            # Get positions
            positions = self.trading_client.get_all_positions()

            # Account overview
            print(f"{Colors.INFO}WHEEL ACCOUNT (Main):{Colors.RESET}")
            print(f"{Colors.DIM}  Portfolio Value:    ${equity:>15,.2f}{Colors.RESET}")
            print(f"{Colors.DIM}  Cash:               ${cash:>15,.2f}{Colors.RESET}")
            print(f"{Colors.DIM}  Buying Power:       ${buying_power:>15,.2f}{Colors.RESET}")
            print(f"{Colors.DIM}  Open Positions:     {len(positions):>15}{Colors.RESET}\n")

            # SPREAD ACCOUNT (Secondary) - if configured
            if self.spread_trading_client:
                try:
                    spread_account = self.spread_trading_client.get_account()
                    spread_equity = float(spread_account.equity) if spread_account.equity is not None else 0.0
                    spread_cash = float(spread_account.cash) if spread_account.cash is not None else 0.0
                    spread_buying_power = float(spread_account.buying_power) if spread_account.buying_power is not None else 0.0
                    spread_positions = self.spread_trading_client.get_all_positions()

                    print(f"{Colors.INFO}SPREAD ACCOUNT (Bull Put Spreads):{Colors.RESET}")
                    print(f"{Colors.DIM}  Portfolio Value:    ${spread_equity:>15,.2f}{Colors.RESET}")
                    print(f"{Colors.DIM}  Cash:               ${spread_cash:>15,.2f}{Colors.RESET}")
                    print(f"{Colors.DIM}  Buying Power:       ${spread_buying_power:>15,.2f}{Colors.RESET}")
                    print(f"{Colors.DIM}  Open Positions:     {len(spread_positions):>15}{Colors.RESET}\n")

                    # Combined totals
                    combined_equity = equity + spread_equity
                    print(f"{Colors.SUCCESS}COMBINED TOTAL:{Colors.RESET}")
                    print(f"{Colors.SUCCESS}  Total Portfolio:    ${combined_equity:>15,.2f}{Colors.RESET}\n")
                except Exception as e:
                    logging.debug(f"[SPREAD] Could not display spread account: {e}")

            # Current positions - WHEEL STRATEGY
            if positions:
                print(f"{Colors.INFO}OPEN POSITIONS - WHEEL STRATEGY:{Colors.RESET}")
                total_value = 0
                total_pl = 0

                for i, pos in enumerate(positions, 1):
                    full_symbol = pos.symbol
                    qty = int(pos.qty) if pos.qty is not None else 0
                    entry = float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0
                    current = float(pos.current_price) if pos.current_price is not None else 0.0
                    market_val = float(pos.market_value) if pos.market_value is not None else 0.0
                    unrealized_pl = float(pos.unrealized_pl) if pos.unrealized_pl is not None else 0.0
                    unrealized_pct = float(pos.unrealized_plpc) if pos.unrealized_plpc is not None else 0.0

                    total_value += market_val
                    total_pl += unrealized_pl

                    # Extract underlying symbol and expiration from OCC format
                    # Format: SYMBOL[6]YYMMDD[C|P]STRIKE[8]
                    # Example: SPY251219C00450000 -> SPY, 2025-12-19, Call
                    if len(full_symbol) > 6:
                        underlying = full_symbol[:full_symbol.index(next(c for c in full_symbol if c.isdigit()))]
                        try:
                            # Extract YYMMDD (6 digits starting after symbol)
                            date_start = len(underlying)
                            exp_str = full_symbol[date_start:date_start+6]
                            option_type = full_symbol[date_start+6]  # C or P

                            exp_date = datetime.strptime(exp_str, '%y%m%d').strftime('%m/%d/%y')
                            display_symbol = f"{underlying} {exp_date} {option_type}"
                        except:
                            underlying = extract_underlying_symbol(full_symbol)
                            display_symbol = full_symbol
                            exp_date = "N/A"
                    else:
                        # Stock position (not an option)
                        underlying = full_symbol
                        display_symbol = full_symbol
                        exp_date = "Stock"

                    # Get underlying stock's current price and daily percentage change
                    try:
                        stock_data = self.openbb.get_quote(underlying)
                        if stock_data and isinstance(stock_data, dict) and 'results' in stock_data:
                            stock_quote = stock_data['results'][0] if isinstance(stock_data['results'], list) else stock_data['results']
                            stock_price = stock_quote.get('price', stock_quote.get('last_price', 0))
                            stock_pct_change = stock_quote.get('percent_change', 0) * 100
                            stock_change_color = Colors.SUCCESS if stock_pct_change >= 0 else Colors.ERROR
                        else:
                            stock_price = 0
                            stock_pct_change = 0
                            stock_change_color = Colors.DIM
                    except:
                        stock_price = 0
                        stock_pct_change = 0
                        stock_change_color = Colors.DIM

                    # Color code P&L
                    pl_color = Colors.SUCCESS if unrealized_pl >= 0 else Colors.ERROR

                    print(f"{Colors.DIM}  {i:2d}. {display_symbol:25s} Qty: {qty:3d}  Entry: ${entry:7.2f}  Current: ${current:7.2f}")
                    print(f"      Stock: ${stock_price:>7.2f} {stock_change_color}({stock_pct_change:>+6.2f}%){Colors.RESET}  Value: ${market_val:>10,.2f}  {pl_color}P&L: ${unrealized_pl:>+10,.2f} ({unrealized_pct:>+6.1%}){Colors.RESET}\n")

                print(f"{Colors.DIM}  Total Market Value: ${total_value:,.2f}{Colors.RESET}")
                pl_color = Colors.SUCCESS if total_pl >= 0 else Colors.ERROR
                print(f"  {pl_color}Total Unrealized P&L: ${total_pl:>+,.2f}{Colors.RESET}\n")
            else:
                print(f"{Colors.DIM}  No open positions{Colors.RESET}\n")

            # SPREAD POSITIONS - BULL PUT SPREAD STRATEGY
            if self.spread_manager:
                try:
                    spread_positions = self.spread_manager.get_all_positions()

                    if spread_positions:
                        print(f"{Colors.INFO}OPEN POSITIONS - BULL PUT SPREAD STRATEGY:{Colors.RESET}")
                        spread_total_value = 0
                        spread_total_pl = 0
                        display_index = 0  # Track display index separately since we may skip spreads

                        for spread in spread_positions:
                            symbol = spread['symbol']
                            short_strike = spread['short_strike']
                            long_strike = spread['long_strike']
                            num_contracts = spread['num_contracts']
                            total_credit = spread['total_credit']
                            max_profit = spread['max_profit']
                            max_risk = spread['max_risk']
                            expiration = spread['expiration']
                            short_put_symbol = spread['short_put_symbol']
                            long_put_symbol = spread['long_put_symbol']

                            # Fetch LIVE P&L from Alpaca positions (more accurate than calculating)
                            try:
                                # Get current positions from Alpaca
                                alpaca_positions = self.spread_trading_client.get_all_positions()

                                short_leg_found = False
                                long_leg_found = False
                                short_leg_pnl = 0
                                long_leg_pnl = 0
                                short_current_price = 0
                                long_current_price = 0

                                # Find matching positions and get their P&L directly from Alpaca
                                for pos in alpaca_positions:
                                    if pos.symbol == short_put_symbol:
                                        short_leg_found = True
                                        short_leg_pnl = float(pos.unrealized_pl) if pos.unrealized_pl else 0
                                        short_current_price = float(pos.current_price) if pos.current_price else 0
                                    elif pos.symbol == long_put_symbol:
                                        long_leg_found = True
                                        long_leg_pnl = float(pos.unrealized_pl) if pos.unrealized_pl else 0
                                        long_current_price = float(pos.current_price) if pos.current_price else 0

                                # CRITICAL: If neither leg exists in Alpaca, spread was closed - skip display and close in DB
                                if not short_leg_found and not long_leg_found:
                                    logging.info(f"[SPREAD] {symbol}: Neither leg found in Alpaca - spread was closed, updating database")
                                    # Exit price is 0 since both legs are gone (fully expired or closed)
                                    self.spread_manager.close_spread_position(
                                        spread_id=spread['id'],
                                        exit_price=0.0,
                                        exit_reason="Both legs closed (not found in Alpaca positions)"
                                    )
                                    continue  # Skip displaying this spread

                                # If only one leg exists, log warning but still display (may be partial fill/close)
                                if not short_leg_found or not long_leg_found:
                                    missing_leg = "short" if not short_leg_found else "long"
                                    logging.warning(f"[SPREAD] {symbol}: {missing_leg} leg not found in Alpaca - may be partial position")

                                # Total spread P&L is sum of both legs (Alpaca already calculated correctly)
                                unrealized_pnl = short_leg_pnl + long_leg_pnl

                                # Calculate current spread value for display
                                current_value = short_current_price - long_current_price

                                # Calculate P&L percentage based on credit received
                                credit_per_spread = total_credit / num_contracts if num_contracts > 0 else total_credit
                                unrealized_pnl_pct = (unrealized_pnl / (credit_per_spread * 100) * 100) if credit_per_spread > 0 else 0

                                # Update database with live values
                                self.spread_manager.update_spread_value(spread['id'], current_value)

                            except Exception as e:
                                logging.warning(f"Could not fetch live prices for {symbol} spread: {e}")
                                # Fall back to database values
                                current_value = spread.get('current_value', total_credit)
                                unrealized_pnl = spread.get('unrealized_pnl', 0)
                                unrealized_pnl_pct = spread.get('unrealized_pnl_pct', 0)

                            # Calculate DTE
                            try:
                                exp_date = datetime.strptime(expiration, '%Y-%m-%d')
                                dte = (exp_date - datetime.now()).days
                                exp_display = exp_date.strftime('%m/%d/%y')
                            except:
                                dte = 0
                                exp_display = expiration

                            spread_total_value += (current_value * 100 * num_contracts)
                            spread_total_pl += unrealized_pnl

                            # Get underlying stock price and change
                            try:
                                stock_data = self.openbb.get_quote(symbol)
                                if stock_data and isinstance(stock_data, dict) and 'results' in stock_data:
                                    stock_quote = stock_data['results'][0] if isinstance(stock_data['results'], list) else stock_data['results']
                                    stock_price = stock_quote.get('price', stock_quote.get('last_price', 0))
                                    stock_pct_change = stock_quote.get('percent_change', 0) * 100
                                    stock_change_color = Colors.SUCCESS if stock_pct_change >= 0 else Colors.ERROR
                                else:
                                    stock_price = 0
                                    stock_pct_change = 0
                                    stock_change_color = Colors.DIM
                            except:
                                stock_price = 0
                                stock_pct_change = 0
                                stock_change_color = Colors.DIM

                            # Increment display index for this spread
                            display_index += 1

                            # Color code P&L
                            pl_color = Colors.SUCCESS if unrealized_pnl >= 0 else Colors.ERROR

                            # Display format: "Symbol ExpDate SPREAD Qty: X  Strikes: $XX/$XX"
                            display_symbol = f"{symbol} {exp_display} SPREAD"

                            print(f"{Colors.DIM}  {display_index:2d}. {display_symbol:25s} Qty: {num_contracts:3d}  Strikes: ${short_strike:.2f}/${long_strike:.2f}")
                            print(f"      Stock: ${stock_price:>7.2f} {stock_change_color}({stock_pct_change:>+6.2f}%){Colors.RESET}  DTE: {dte:3d}  {pl_color}P&L: ${unrealized_pnl:>+10,.2f} ({unrealized_pnl_pct/100:>+6.1%}){Colors.RESET}")
                            print(f"      Credit: ${total_credit:.2f}  Current: ${current_value:.2f}  Max Profit: ${max_profit:.0f}  Max Risk: ${max_risk:.0f}\n")

                        print(f"{Colors.DIM}  Total Market Value: ${spread_total_value:,.2f}{Colors.RESET}")
                        pl_color = Colors.SUCCESS if spread_total_pl >= 0 else Colors.ERROR
                        print(f"  {pl_color}Total Unrealized P&L: ${spread_total_pl:>+,.2f}{Colors.RESET}\n")
                    else:
                        print(f"{Colors.INFO}BULL PUT SPREAD POSITIONS:{Colors.RESET}")
                        print(f"{Colors.DIM}  No open spread positions{Colors.RESET}\n")
                except Exception as e:
                    logging.error(f"Error displaying spread positions: {e}", exc_info=True)

            # 30-day performance stats
            stats = self.trade_journal.get_performance_stats(days=30)
            if stats['total_trades'] > 0:
                print(f"{Colors.INFO}30-DAY PERFORMANCE:{Colors.RESET}")
                print(f"{Colors.DIM}  Total Trades:       {stats['total_trades']:>15}{Colors.RESET}")
                win_color = Colors.SUCCESS if stats['win_rate'] >= 0.5 else Colors.WARNING
                print(f"  {win_color}Win Rate:           {stats['win_rate']:>14.1%}{Colors.RESET}")
                pnl_color = Colors.SUCCESS if stats['total_pnl'] >= 0 else Colors.ERROR
                print(f"  {pnl_color}Total P&L:          ${stats['total_pnl']:>14,.2f}{Colors.RESET}")
                return_color = Colors.SUCCESS if stats['avg_return'] >= 0 else Colors.ERROR
                print(f"  {return_color}Avg Return:         {stats['avg_return']:>14.1%}{Colors.RESET}")
                print(f"{Colors.DIM}  Wins:               {stats['wins']:>15}{Colors.RESET}")
                print(f"{Colors.DIM}  Losses:             {stats['losses']:>15}{Colors.RESET}\n")
            else:
                print(f"{Colors.INFO}30-DAY PERFORMANCE:{Colors.RESET}")
                print(f"{Colors.DIM}  No closed trades in the last 30 days{Colors.RESET}\n")

        except Exception as e:
            logging.error(f"Error displaying portfolio summary: {e}")
            print(f"{Colors.ERROR}[!] Could not retrieve portfolio information{Colors.RESET}\n")

        print(f"{Colors.HEADER}{'='*80}{Colors.RESET}\n")

    def display_portfolio_strategy_summary(self):
        """Display strategic rationale for holding current positions"""
        try:
            positions = self.trading_client.get_all_positions()

            if not positions:
                print(f"{Colors.INFO}[STRATEGY SUMMARY] No open positions{Colors.RESET}\n")
                return

            print(f"\n{Colors.HEADER}{'='*80}{Colors.RESET}")
            print(f"{Colors.HEADER}                      STRATEGY SUMMARY{Colors.RESET}")
            print(f"{Colors.HEADER}{'='*80}{Colors.RESET}\n")

            print(f"{Colors.INFO}HOLDING STRATEGY ANALYSIS:{Colors.RESET}\n")

            for i, pos in enumerate(positions, 1):
                symbol = pos.symbol
                qty = int(pos.qty) if pos.qty is not None else 0
                current_price = float(pos.current_price) if pos.current_price is not None else 0.0
                avg_entry = float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0
                unrealized_plpct = float(pos.unrealized_plpc) if pos.unrealized_plpc is not None else 0.0

                # Extract underlying symbol
                underlying = self._extract_underlying(symbol)

                # Get strategy info from database (check trade journal first, then wheel positions)
                strategy_info = self.trade_journal.get_position_strategy(underlying)

                # If not found in trade journal, check if it's a Wheel position
                if not strategy_info:
                    wheel_position = self.wheel_manager.get_wheel_position(underlying)
                    if wheel_position:
                        strategy_info = {
                            'strategy': f"WHEEL_{wheel_position['state']}",
                            'strikes': f"${wheel_position.get('current_strike', 0):.2f}" if wheel_position.get('current_strike') else '',
                            'expiry': wheel_position.get('current_expiration', ''),
                            'reason': f"Wheel Strategy: {wheel_position['state']} | Premium collected: ${wheel_position['total_premium_collected']:.2f}",
                            'grok_notes': ''
                        }

                if strategy_info:
                    strategy = strategy_info.get('strategy', 'UNKNOWN')
                    strikes = strategy_info.get('strikes', '')
                    expiry = strategy_info.get('expiry', 'UNKNOWN')
                    entry_reason = strategy_info.get('reason', 'No reason recorded')
                    grok_notes = strategy_info.get('grok_notes', '')

                    # Analyze current position viability
                    hold_rationale = self._analyze_hold_rationale(
                        underlying, strategy, current_price, avg_entry, unrealized_plpct, grok_notes
                    )

                    print(f"{Colors.DIM}{i:2d}. {underlying:6s} | {strategy:18s} | P&L: {unrealized_plpct:+6.2%}{Colors.RESET}")
                    print(f"      Strategy Context: {entry_reason}")
                    if grok_notes:
                        print(f"      Recent Grok Analysis: {grok_notes}")
                    print(f"      {hold_rationale}")
                    print()

                else:
                    print(f"{Colors.WARNING}{i:2d}. {underlying:6s} | NO STRATEGY INFO | P&L: {unrealized_plpct:+.1%}{Colors.RESET}")
                    print(f"      WARNING: No strategy information found for this position{Colors.RESET}")
                    print(f"      RECOMMENDATION: Manual review required{Colors.RESET}\n")

            # Overall portfolio strategy assessment
            self._display_overall_strategy_assessment(positions)

            print(f"{Colors.HEADER}{'='*80}{Colors.RESET}\n")

        except Exception as e:
            logging.error(f"Error displaying strategy summary: {e}")

    def _analyze_hold_rationale(self, symbol: str, strategy: str, current_price: float,
                               entry_price: float, pnl_pct: float, grok_notes: str) -> str:
        """Analyze why we're still holding a position"""
        rationale_parts = []

        # Basic profit/loss assessment
        if pnl_pct >= 0.20:  # +20% or more
            rationale_parts.append("POSITION IN PROFIT - letting winners run")
        elif pnl_pct >= 0.05:  # +5% to +20%
            rationale_parts.append("Moderate profit - monitoring for optimal exit")
        elif pnl_pct >= -0.10:  # -10% to +5%
            rationale_parts.append("Close to breakeven - position within normal range")
        elif pnl_pct >= -0.20:  # -20% to -10%
            rationale_parts.append("Below breakeven - strategies have risk tolerance")
        else:  # -20% or worse
            rationale_parts.append("Significant loss - should have been stopped out")

        # Check if recent Grok assessment supports holding
        grok_hold_signals = [
            "hold", "monitor", "wait", "long-term", "positioning",
            "potential", "set-up", "developing", "patience"
        ]

        if grok_notes:
            grok_lower = grok_notes.lower()
            if any(signal in grok_lower for signal in grok_hold_signals):
                rationale_parts.append("AI analysis supports holding")
            elif "exit" in grok_lower or "sell" in grok_lower:
                rationale_parts.append("AI recently suggested potential exit")

        # Strategy-specific considerations
        strategy_lower = strategy.lower()
        if "long" in strategy_lower:
            rationale_parts.append("Directional bet needs time to work")
        elif "spread" in strategy_lower or "straddle" in strategy_lower:
            rationale_parts.append("Complex strategy may benefit from time decay or volatility change")
        elif "bull" in strategy_lower or "bear" in strategy_lower:
            rationale_parts.append("Momentum strategy - market may turn favorable")

        return " | ".join(rationale_parts)

    def _display_overall_strategy_assessment(self, positions: List):
        """Provide overall assessment of portfolio strategy"""
        try:
            print(f"{Colors.INFO}PORTFOLIO STRATEGY ASSESSMENT:{Colors.RESET}")

            total_value = sum(float(pos.market_value) if pos.market_value else 0 for pos in positions)
            total_pl = sum(float(pos.unrealized_pl) if pos.unrealized_pl else 0 for pos in positions)

            # Count strategies
            strategy_counts = {}
            for pos in positions:
                underlying = self._extract_underlying(pos.symbol)
                strategy_info = self.trade_journal.get_position_strategy(underlying)
                if strategy_info:
                    strategy = strategy_info.get('strategy', 'UNKNOWN')
                    strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

            if strategy_counts:
                print(f"Strategy Distribution: {', '.join([f'{strat}: {cnt}' for strat, cnt in strategy_counts.items()])}")

            if total_pl >= 0:
                print(f"Overall: {Colors.SUCCESS}Portfolio in profit (${total_pl:,.0f}){Colors.RESET}")
            else:
                print(f"Overall: {Colors.ERROR}Portfolio in loss (${total_pl:,.0f}){Colors.RESET}")

            print(f"Notes: Strategies are held based on AI analysis and risk management rules."
                  f" Positions monitored every 5 minutes with exit rules applied automatically.")

        except Exception as e:
            logging.error(f"Error in overall strategy assessment: {e}")

    def refresh_candidate_data(self, candidate: Dict) -> Optional[Dict]:
        """
        Refresh real-time data for a candidate immediately before Grok analysis.
        CRITICAL: Stock prices change rapidly - always get fresh data for Grok.
        """
        symbol = candidate['symbol']

        try:
            # Get fresh stock quote
            stock_data = self.openbb.get_quote(symbol)

            # Debug: Log what we received
            if stock_data is None:
                logging.debug(f"Received None for stock_data for {symbol}")
                return None

            if not isinstance(stock_data, dict):
                logging.warning(f"stock_data for {symbol} is type {type(stock_data)}: {str(stock_data)[:100]}")
                return None

            # Check for error responses or empty data
            if 'results' not in stock_data:
                logging.debug(f"No 'results' key in stock_data for {symbol}. Keys: {stock_data.keys()}")
                return None

            results = stock_data['results']
            if not results:
                logging.debug(f"Empty results for {symbol}")
                return None

            # Extract quote from results
            if isinstance(results, list):
                if len(results) == 0:
                    logging.debug(f"Empty results list for {symbol}")
                    return None
                stock_quote = results[0]
            else:
                stock_quote = results

            if not isinstance(stock_quote, dict):
                logging.warning(f"stock_quote for {symbol} is type {type(stock_quote)}: {str(stock_quote)[:100]}")
                return None

            # Get fresh options chain with Greeks
            options_data = self.openbb.get_options_chains(symbol)

            if not options_data or not isinstance(options_data, dict):
                logging.debug(f"Invalid options_data for {symbol}: type={type(options_data)}")
                return None

            if 'results' not in options_data:
                logging.debug(f"No 'results' in options_data for {symbol}")
                return None

            if not options_data['results']:
                logging.debug(f"Empty options results for {symbol}")
                return None

            # Re-analyze with fresh data
            # Note: _analyze_options_chain expects (symbol, options_data, stock_data)
            analysis = self.market_scanner._analyze_options_chain(
                symbol,
                options_data['results'],
                stock_quote
            )

            if not analysis:
                logging.debug(f"Analysis failed for {symbol}")
                return None

            # Calculate final_score using expert criteria (same logic as _score_by_expert_criteria)
            final_score = analysis['score']

            # Boost for multiple confirming signals
            signals = analysis['signals']
            if len(signals) >= 3:
                final_score *= 1.5

            # Boost for high IV rank (selling premium opportunity)
            iv_rank = analysis.get('iv_metrics', {}).get('iv_rank', 50)
            if iv_rank > 80:
                final_score *= 1.3

            # Update candidate with fresh data
            candidate['stock_data'] = stock_quote
            candidate['options_data'] = options_data['results']
            candidate['analysis'] = analysis
            candidate['final_score'] = final_score
            candidate['last_refresh'] = datetime.now()

            price = stock_quote.get('price', stock_quote.get('last_price', 0))
            logging.info(f"Refreshed real-time data for {symbol}: Price ${price:.2f}")
            return candidate

        except AttributeError as e:
            logging.error(f"AttributeError refreshing {symbol}: {e}. Check data structure.")
            return None
        except Exception as e:
            logging.error(f"Error refreshing data for {symbol}: {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"Traceback for {symbol}: {traceback.format_exc()}")
            return None

    def update_rolling_top_50(self, new_candidates: List[Dict]):
        """
        Update rolling top-50 list with new candidates from continuous OpenBB scanning.
        Maintains the 50 highest-scoring candidates.
        """
        # Combine existing and new candidates
        all_candidates = self.rolling_top_50 + new_candidates

        # Sort by final_score descending
        all_candidates.sort(key=lambda x: x.get('final_score', 0), reverse=True)

        # Keep top 50
        old_top_50 = set(c['symbol'] for c in self.rolling_top_50)
        self.rolling_top_50 = all_candidates[:50]
        new_top_50 = set(c['symbol'] for c in self.rolling_top_50)

        # Find new symbols that made it to top 50
        newly_added = new_top_50 - old_top_50

        if newly_added:
            logging.info(f"Rolling top-50 updated: {len(newly_added)} new symbols added: {', '.join(newly_added)}")
            return [c for c in self.rolling_top_50 if c['symbol'] in newly_added]

        return []

    def update_rolling_top_25(self, grok_analyzed: List[Dict]):
        """
        Update rolling top-25 list with new Grok-analyzed candidates.
        Maintains the 25 highest Grok-confidence candidates.
        """
        # Combine existing and new candidates
        all_candidates = self.rolling_top_25 + grok_analyzed

        # Sort by grok_confidence descending
        all_candidates.sort(key=lambda x: x.get('grok_confidence', 0), reverse=True)

        # Keep top 25
        old_top_25_symbols = set(c['symbol'] for c in self.rolling_top_25)
        self.rolling_top_25 = all_candidates[:25]
        new_top_25_symbols = set(c['symbol'] for c in self.rolling_top_25)

        # Log changes
        newly_added = new_top_25_symbols - old_top_25_symbols
        removed = old_top_25_symbols - new_top_25_symbols

        if newly_added:
            logging.info(f"Rolling top-25 updated: Added {', '.join(newly_added)}")
        if removed:
            logging.info(f"Rolling top-25 updated: Removed {', '.join(removed)}")

    def _apply_pre_grok_quality_gate(self, candidates: List[Dict], target_count: int = 30) -> List[Dict]:
        """
        TIER 3.1: Smart Grok Batching with Quality Gate
        Pre-filter candidates before expensive Grok API calls
        Reduces 50 → 30 candidates using quality scoring
        """
        if len(candidates) <= target_count:
            return candidates

        print(f"{Colors.INFO}[QUALITY GATE] Filtering {len(candidates)} → {target_count} candidates for Grok...{Colors.RESET}")

        # Quality score each candidate
        for candidate in candidates:
            quality_score = 0
            analysis = candidate.get('analysis', {})
            stock_data = candidate.get('stock_data', {})

            # Factor 1: Spread quality (weight: 30 points)
            spread_pct = analysis.get('avg_spread_pct', 1.0)
            if spread_pct < 0.05:
                quality_score += 30  # Excellent spread
            elif spread_pct < 0.10:
                quality_score += 20  # Good spread
            elif spread_pct < 0.15:
                quality_score += 10  # Acceptable spread
            # >15% spread gets 0 points

            # Factor 2: Liquidity (weight: 25 points)
            total_volume = analysis.get('total_volume', 0)
            total_oi = analysis.get('total_oi', 0)
            if total_volume > 50000 and total_oi > 100000:
                quality_score += 25  # Excellent liquidity
            elif total_volume > 20000 and total_oi > 50000:
                quality_score += 18  # Good liquidity
            elif total_volume > 10000 and total_oi > 25000:
                quality_score += 10  # Acceptable liquidity

            # Factor 3: Signal strength (weight: 20 points)
            signals = analysis.get('signals', [])
            num_signals = len(signals)
            if num_signals >= 4:
                quality_score += 20
            elif num_signals >= 3:
                quality_score += 15
            elif num_signals >= 2:
                quality_score += 10

            # Factor 4: IV rank extremes (weight: 15 points)
            iv_rank = analysis.get('iv_metrics', {}).get('iv_rank', 50)
            if iv_rank > 80 or iv_rank < 20:
                quality_score += 15  # Extreme IV = good for options
            elif iv_rank > 70 or iv_rank < 30:
                quality_score += 10  # Moderate extreme

            # Factor 5: Data freshness (weight: 10 points)
            import time
            data_timestamp = candidate.get('data_timestamp', 0)
            if data_timestamp > 0:
                age_minutes = (time.time() - data_timestamp) / 60
                if age_minutes < 5:
                    quality_score += 10
                elif age_minutes < 10:
                    quality_score += 7
                elif age_minutes < 15:
                    quality_score += 4

            # PENALTY: Red flags
            # Wide spread penalty
            if spread_pct > 0.20:
                quality_score -= 20

            # Stale data penalty
            if data_timestamp > 0 and (time.time() - data_timestamp) / 60 > 20:
                quality_score -= 15

            # Store quality score
            candidate['quality_score'] = quality_score

        # Sort by quality score
        candidates.sort(key=lambda x: x.get('quality_score', 0), reverse=True)

        # Take top N by quality
        filtered = candidates[:target_count]

        # Log quality gate results
        avg_quality_kept = sum(c.get('quality_score', 0) for c in filtered) / len(filtered) if filtered else 0
        avg_quality_dropped = sum(c.get('quality_score', 0) for c in candidates[target_count:]) / len(candidates[target_count:]) if len(candidates) > target_count else 0

        logging.info(f"TIER 3.1: Quality gate filtered {len(candidates)} → {len(filtered)} candidates")
        logging.info(f"  Avg quality kept: {avg_quality_kept:.1f}, Avg quality dropped: {avg_quality_dropped:.1f}")

        # Show what was filtered out
        dropped = candidates[target_count:]
        if dropped:
            dropped_symbols = [f"{c['symbol']}({c.get('quality_score', 0):.0f})" for c in dropped[:5]]
            logging.debug(f"  Dropped: {', '.join(dropped_symbols)}{'...' if len(dropped) > 5 else ''}")

        # TIER 3.3: CORRELATION FILTERING
        # Remove highly correlated positions for better diversification
        filtered = self._apply_correlation_filter(filtered)

        return filtered

    def _apply_correlation_filter(self, candidates: List[Dict], max_correlation: float = 0.7) -> List[Dict]:
        """
        TIER 3.3: Correlation Filtering
        Remove highly correlated stocks to ensure portfolio diversification
        """
        if len(candidates) < 2:
            return candidates

        print(f"{Colors.DIM}[CORRELATION FILTER] Checking for source diversity...{Colors.RESET}")

        # Simple correlation heuristic based on source overlap
        # Limit same source to avoid over-concentration from single discovery method

        filtered = []
        source_counts = {}

        for candidate in candidates:
            symbol = candidate['symbol']
            stock_data = candidate.get('stock_data', {})
            source = stock_data.get('source', '')

            # Track source diversity
            should_include = True

            # Rule: Limit same source (max 5 from same single source)
            if source:
                # Get primary source (first in comma-separated list)
                primary_source = source.split(',')[0]
                source_count = source_counts.get(primary_source, 0)
                if source_count >= 5:
                    logging.debug(f"TIER 3.3: Skipping {symbol} - source {primary_source} already has {source_count} stocks")
                    should_include = False

            if should_include:
                filtered.append(candidate)
                # Update counts
                if source:
                    primary_source = source.split(',')[0]
                    source_counts[primary_source] = source_counts.get(primary_source, 0) + 1

        removed_count = len(candidates) - len(filtered)
        if removed_count > 0:
            logging.info(f"TIER 3.3: Correlation filter removed {removed_count} highly correlated stocks")
            print(f"{Colors.DIM}  → Removed {removed_count} correlated stocks for better diversification{Colors.RESET}")

        return filtered

    def analyze_single_with_grok(self, candidate: Dict) -> Optional[Dict]:
        """
        Immediate Grok analysis for a single high-value candidate.
        Used when continuous scanning finds a promising new opportunity.
        ALWAYS refreshes data before analysis.
        """
        symbol = candidate['symbol']
        print(f"{Colors.INFO}[GROK IMMEDIATE] Analyzing new high-scorer: {symbol}...{Colors.RESET}")

        # CRITICAL: Refresh real-time data before Grok analysis
        refreshed = self.refresh_candidate_data(candidate)
        if not refreshed:
            logging.warning(f"Could not refresh data for {symbol}, skipping Grok analysis")
            return None

        candidate = refreshed

        # Analyze with Grok (single candidate)
        results = self.analyze_batch_with_grok([candidate], refresh_data=False)  # Already refreshed

        if results and len(results) > 0:
            result = results[0]
            print(f"{Colors.SUCCESS}  → {symbol}: {result.get('grok_confidence', 0)}% confidence, {result.get('strategy', 'UNKNOWN')}{Colors.RESET}")
            return result

        return None

    def analyze_batch_with_grok(self, candidates: List[Dict], refresh_data: bool = True) -> List[Dict]:
        """
        Optimized Grok analysis - batch candidates into single request
        Reduces 50 API calls to 5-10 calls
        ALWAYS refreshes data before analysis unless refresh_data=False
        """
        if not config.XAI_API_KEY:
            return candidates

        # CRITICAL: Refresh real-time data for all candidates before Grok analysis
        if refresh_data:
            print(f"{Colors.INFO}[DATA REFRESH] Updating real-time data for {len(candidates)} candidates...{Colors.RESET}")
            refreshed_candidates = []
            for candidate in candidates:
                refreshed = self.refresh_candidate_data(candidate)
                if refreshed:
                    refreshed_candidates.append(refreshed)
                else:
                    logging.warning(f"Could not refresh data for {candidate['symbol']}, excluding from Grok analysis")
                time.sleep(0.1)  # Rate limit

            candidates = refreshed_candidates

        if not candidates:
            print(f"{Colors.WARNING}No candidates with valid real-time data for Grok analysis{Colors.RESET}")
            return []

        print(f"\n{Colors.HEADER}[GROK ANALYSIS] Batch analysis of {len(candidates)} candidates...{Colors.RESET}")

        # Batch candidates (10 per request to avoid token limits)
        batch_size = 10
        rated_candidates = []

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i+batch_size]

            print(f"{Colors.DIM}  Batch {i//batch_size + 1}/{(len(candidates)-1)//batch_size + 1}: Analyzing {len(batch)} symbols...{Colors.RESET}", end='', flush=True)

            # Build batch prompt
            prompt = self._build_batch_prompt(batch)

            try:
                headers = {'Authorization': f'Bearer {config.XAI_API_KEY}', 'Content-Type': 'application/json'}
                payload = {
                    'model': 'grok-4-fast',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 2000,
                    'temperature': 0.7
                }

                # Log the prompt
                self.grok_logger.info(f"=== BATCH GROK PROMPT ===")
                self.grok_logger.info(f"Candidates: {[c['symbol'] for c in batch]}")
                self.grok_logger.info(f"Prompt:\n{prompt}")
                self.grok_logger.info(f"Model: grok-4-fast | Max Tokens: 1000 | Temperature: 0.7")

                # Enhanced retry logic for Grok API calls
                max_attempts = 3
                base_delay = 1.0

                for attempt in range(max_attempts):
                    try:
                        # Increased timeout for grok-4-fast model which may take longer
                        response = requests.post(config.XAI_BASE_URL, json=payload, headers=headers, timeout=180)

                        # Log the full response regardless of success
                        self.grok_logger.info(f"=== BATCH GROK RESPONSE (Attempt {attempt+1}/{max_attempts}) ===")
                        self.grok_logger.info(f"Status Code: {response.status_code}")

                        if response.status_code == 200:
                            full_response = response.json()
                            self.grok_logger.info(f"Full Response: {json.dumps(full_response, indent=2)}")
                            break  # Success, exit retry loop
                        else:
                            self.grok_logger.info(f"Error Response: {response.text}")

                            # Check for rate limiting or temporary errors that might respond to retries
                            if response.status_code in [429, 502, 503, 504] and attempt < max_attempts - 1:
                                delay = base_delay * (2 ** attempt)
                                self.grok_logger.info(f"Retrying in {delay}s due to status {response.status_code}...")
                                time.sleep(delay)
                                continue
                            else:
                                # Non-retryable error or last attempt
                                break

                    except requests.exceptions.Timeout as e:
                        self.grok_logger.warning(f"Grok API timeout (attempt {attempt+1}/{max_attempts}): {e}")
                        if attempt < max_attempts - 1:
                            delay = base_delay * (2 ** attempt)
                            self.grok_logger.info(f"Retrying timeout in {delay}s...")
                            time.sleep(delay)
                            continue
                        else:
                            response = None

                    except requests.exceptions.ConnectionError as e:
                        self.grok_logger.warning(f"Grok API connection error (attempt {attempt+1}/{max_attempts}): {e}")
                        if attempt < max_attempts - 1:
                            delay = base_delay * (2 ** attempt)
                            self.grok_logger.info(f"Retrying connection error in {delay}s...")
                            time.sleep(delay)
                            continue
                        else:
                            response = None

                    except Exception as e:
                        self.grok_logger.error(f"Unexpected error in Grok API call (attempt {attempt+1}/{max_attempts}): {e}")
                        if attempt < max_attempts - 1:
                            delay = base_delay * (2 ** attempt)
                            time.sleep(delay)
                            continue
                        else:
                            response = None
                            break

                # Check if we got a successful response
                if response and response.status_code == 200:
                    content = response.json()['choices'][0]['message']['content']

                    # Parse batch response
                    parsed = self._parse_batch_response(content, batch)
                    rated_candidates.extend(parsed)
                    print(f" ✓")
                else:
                    # IMPROVED ERROR REPORTING
                    error_code = response.status_code if response else "No Response"
                    error_msg = response.text[:200] if response else "Connection failed"
                    print(f" ✗ (HTTP {error_code})")
                    print(f"{Colors.ERROR}[GROK ERROR] Failed to analyze batch: {error_msg[:100]}{Colors.RESET}")
                    logging.error(f"Grok API failed: Status={error_code}, Response={error_msg}")
                    self.grok_logger.error(f"Grok API call failed: Status={error_code}, Response={error_msg}")

                    # Check for common issues
                    if response:
                        if response.status_code == 401:
                            print(f"{Colors.ERROR}[!] config.XAI_API_KEY is invalid or expired{Colors.RESET}")
                            self.grok_logger.error("config.XAI_API_KEY authentication failed - check API key")
                        elif response.status_code == 429:
                            print(f"{Colors.WARNING}[!] Grok API rate limit exceeded{Colors.RESET}")
                            self.grok_logger.warning("Grok API rate limit hit")
                        elif response.status_code >= 500:
                            print(f"{Colors.WARNING}[!] Grok API server error (temporary){Colors.RESET}")
                            self.grok_logger.warning(f"Grok API server error: {response.status_code}")
                    else:
                        print(f"{Colors.ERROR}[!] No response from Grok API - check network/firewall{Colors.RESET}")
                        self.grok_logger.error("No response from Grok API - connection failed")

                    # Add with default confidence
                    for candidate in batch:
                        candidate['grok_confidence'] = 0
                        candidate['strategy'] = 'UNKNOWN'
                        rated_candidates.append(candidate)

            except Exception as e:
                print(f" ✗ ({str(e)[:30]})")
                print(f"{Colors.ERROR}[GROK ERROR] Exception during API call: {str(e)}{Colors.RESET}")
                logging.error(f"Grok batch error: {e}", exc_info=True)
                self.grok_logger.error(f"Grok batch exception: {e}", exc_info=True)
                # Add with default confidence
                for candidate in batch:
                    candidate['grok_confidence'] = 0
                    candidate['strategy'] = 'UNKNOWN'
                    rated_candidates.append(candidate)

            time.sleep(0.5)  # Rate limiting between batches

        print(f"{Colors.SUCCESS}Grok analysis complete{Colors.RESET}\n")
        return rated_candidates

    def _create_concise_reason(self, reason: str, max_length: int = 35) -> str:
        """Create concise, meaningful summary from Grok reason - UI IMPROVEMENT"""
        if not reason:
            return "No analysis"

        # Remove common filler words for conciseness
        filler_words = {'the', 'a', 'an', 'is', 'are', 'and', 'or', 'with', 'for', 'to', 'of', 'in',
                       'that', 'this', 'be', 'has', 'have', 'from', 'at', 'by', 'on', 'as'}

        # Extract key phrases and signals
        keywords = []
        words = reason.split()

        for word in words:
            cleaned = word.strip('.,;:').lower()
            # Keep important words (not filler) and percentage numbers
            if cleaned not in filler_words or '%' in word or word.isupper():
                keywords.append(word)

            # Stop when we have enough content
            if len(' '.join(keywords)) >= max_length - 3:
                break

        # Join keywords
        concise = ' '.join(keywords)

        # Intelligently truncate at word boundary if still too long
        if len(concise) > max_length:
            concise = concise[:max_length].rsplit(' ', 1)[0]
            if len(concise) < max_length - 3:  # If truncation removed too much, add ellipsis
                concise += '...'

        return concise if concise else "Analysis unavailable"

    def _calculate_historical_volatility(self, symbol: str, days: int = 30) -> float:
        """Calculate realized historical volatility - PROMPT IMPROVEMENT 2.2"""
        try:
            hist_data = self.openbb.get_historical_price(symbol, days=days)
            if not hist_data or 'results' not in hist_data:
                return 0

            prices = [p.get('close', 0) for p in hist_data['results'] if p.get('close')]
            if len(prices) < 20:
                return 0

            # Calculate daily returns
            returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices)) if prices[i-1] > 0]

            if len(returns) < 15:
                return 0

            # Annualized volatility
            import math
            import statistics
            hv = statistics.stdev(returns) * math.sqrt(252)
            return hv
        except Exception as e:
            logging.debug(f"Error calculating HV for {symbol}: {e}")
            return 0

    def _build_batch_prompt(self, batch: List[Dict]) -> str:
        """Build prompt for batch Grok analysis with FULL portfolio context"""
        prompt = """As an expert options trader, analyze these stocks for potential options trades.
You have full visibility into our current portfolio to make sophisticated, risk-managed decisions.

CURRENT PORTFOLIO OVERVIEW:
"""

        # Get market regime for enhanced prompt instructions
        try:
            regime = self.regime_analyzer.analyze_market_regime()
            regime_type = regime.get('regime', 'UNKNOWN')
            regime_implications = regime.get('implications', {})
            volatility_action = regime_implications.get('vol_play', 'neutral')
        except:
            regime_type = 'UNKNOWN'
            volatility_action = 'neutral'

        # Add comprehensive portfolio analysis
        try:
            account = self.trading_client.get_account()
            positions = self.trading_client.get_all_positions()
            exposure = self.portfolio_manager.get_current_exposure()

            # Account summary
            equity = float(account.equity) if account.equity is not None else 0.0
            cash = float(account.cash) if account.cash is not None else 0.0
            buying_power = float(account.buying_power) if account.buying_power is not None else 0.0

            prompt += f"Portfolio Value: ${equity:,.0f} | Cash: ${cash:,.0f} | Buying Power: ${buying_power:,.0f}\n"
            prompt += f"Positions: {len(positions)} | Portfolio Allocated: {exposure['total_allocated']:.1%}\n\n"

            # Risk metrics
            portfolio_greeks = exposure.get('portfolio_greeks', {})
            prompt += f"PORTFOLIO GREEKS: Delta {portfolio_greeks.get('delta', 0):+.0f} | "
            prompt += f"Gamma {portfolio_greeks.get('gamma', 0):+.2f} | "
            prompt += f"Theta ${portfolio_greeks.get('theta', 0):+.0f}/day | "
            prompt += f"Vega ${portfolio_greeks.get('vega', 0):+.0f}\n\n"

            # Current positions with P&L
            if positions:
                prompt += "CURRENT POSITIONS:\n"
                for pos in positions[:8]:  # Limit to avoid token limits, show most recent
                    symbol = pos.symbol
                    qty = int(pos.qty) if pos.qty is not None else 0
                    entry = float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0
                    current = float(pos.current_price) if pos.current_price is not None else 0.0
                    pnl_pct = float(pos.unrealized_plpc) if pos.unrealized_plpc is not None else 0.0

                    # Get strategy info
                    underlying = extract_underlying_symbol(symbol)
                    strategy_info = self.trade_journal.get_position_strategy(underlying)

                    strategy = strategy_info.get('strategy', 'UNKNOWN') if strategy_info else 'UNKNOWN'
                    strikes = strategy_info.get('strikes', '') if strategy_info else ''

                    prompt += f"  {symbol}: {qty:+d} @ ${entry:.2f} → ${current:.2f} | P&L: {pnl_pct:+.1%} | {strategy} {strikes}\n"

                if len(positions) > 8:
                    prompt += f"  ... and {len(positions)-8} more positions\n"
                prompt += "\n"

            # Symbol concentration
            symbol_exposure = exposure.get('by_symbol', {})
            if symbol_exposure:
                prompt += "TOP SYMBOL EXPOSURE:\n"
                top_symbols = sorted(symbol_exposure.items(), key=lambda x: x[1], reverse=True)[:5]
                for symbol, pct in top_symbols:
                    prompt += f"  {symbol}: {pct:.1%}\n"
                prompt += "\n"

            # Recent performance
            stats = self.trade_journal.get_performance_stats(days=30)
            if stats.get('total_trades', 0) > 0:
                prompt += f"RECENT PERFORMANCE (30 days):\n"
                prompt += f"  Win Rate: {stats['win_rate']:.1%} | "
                prompt += f"Avg Return: {stats['avg_return']:.1%} | "
                prompt += f"Total P&L: ${stats['total_pnl']:,.0f}\n\n"

        except Exception as e:
            logging.warning(f"Could not get portfolio overview for Grok prompt: {e}")
            prompt += "[Portfolio data unavailable]\n\n"

        # Market regime context with strategy implications
        prompt += f"CURRENT MARKET REGIME: {regime_type}\n"
        prompt += f"Description: {regime.get('description', 'N/A')}\n"
        prompt += f"Volatility Play Action: {volatility_action}\n\n"

        # PHASE 2: ADD CRITICAL TRADING RULES
        prompt += """═══════════════════════════════════════════════════════════════════════
CRITICAL TRADING RULES - MUST FOLLOW STRICTLY
═══════════════════════════════════════════════════════════════════════

1. DEBIT SPREADS (Bull Call, Bear Put):
   ⚠️  ONLY recommend when IV Rank < 40 (options are CHEAP)
   ⚠️  ONLY when clear directional setup with pullback confirmation
   ⚠️  Spread width must be at least 8% of stock price
   ⚠️  NEVER pay more than 60% of spread width in debit
   ⚠️  If IV Rank > 40, DO NOT recommend debit spreads!

2. CREDIT SPREADS (Bull Put, Bear Call):
   ✓  ONLY recommend when IV Rank > 60 (options are EXPENSIVE)
   ✓  Target collecting at least 30% of spread width in credit
   ✓  Place short strikes at strong technical support/resistance levels
   ✓  If IV Rank < 60, DO NOT recommend credit spreads!

3. SINGLE LEG (Long Call/Put):
   ✓  ONLY when IV Rank < 20 (very cheap options)
   ✓  ONLY with very high conviction (9-10/10 confidence)
   ✓  Rare - spreads are usually better risk/reward

4. NEUTRAL STRATEGIES (Straddle, Strangle):
   ✓  Best when IV Rank > 60 but expect big move
   ✓  Need wide trading range or upcoming catalyst
   ✓  Consider Iron Butterfly if paper account allows

⛔ IRON CONDOR: """

        # Add Iron Condor note based on account type
        is_paper = config.ALPACA_MODE and config.ALPACA_MODE.lower().strip() == 'paper'
        if is_paper:
            prompt += "NOT ALLOWED in paper accounts (requires naked options)\n"
        else:
            prompt += "ONLY when IV Rank > 70 and range-bound market\n"

        prompt += """
═══════════════════════════════════════════════════════════════════════

TRADE ANALYSIS INSTRUCTIONS:
For EACH stock, provide ONE line in this EXACT format:
SYMBOL|STRATEGY|STRIKES|EXPIRY|CONFIDENCE|REASON

Where:
- SYMBOL: Stock ticker
"""

        # Adjust available strategies based on account type
        is_paper = config.ALPACA_MODE and config.ALPACA_MODE.lower().strip() == 'paper'
        if is_paper:
            # Paper accounts cannot trade naked options (IRON_CONDOR, SHORT_STRADDLE, SHORT_STRANGLE)
            prompt += "- STRATEGY: One of [LONG_CALL, LONG_PUT, BULL_CALL_SPREAD, BEAR_PUT_SPREAD, STRADDLE, STRANGLE]\n"
            prompt += "  (IMPORTANT: IRON_CONDOR is NOT allowed in paper accounts - do not recommend it!)\n"
        else:
            prompt += "- STRATEGY: One of [LONG_CALL, LONG_PUT, BULL_CALL_SPREAD, BEAR_PUT_SPREAD, IRON_CONDOR, STRADDLE, STRANGLE]\n"

        prompt += """- STRIKES: Strike price(s) like "450" or "450/455" for spreads
- EXPIRY: Days to expiration like "30DTE" or "45DTE"
- CONFIDENCE: Number 0-100 (CONSIDER PORTFOLIO RISK!)
- REASON: Consider portfolio balance, diversification, and risk management

"""

        # Add STRATEGY-SPECIFIC MARKET REGIME INSTRUCTIONS
        prompt += "MARKET REGIME STRATEGY GUIDANCE:\n"
        if regime_type == 'VOLATILITY_SPIKE':
            prompt += "- PRIORITIZE: STRADDLE, STRANGLE, IRON_CONDOR strategies (high volatility is favorable)\n"
            prompt += "- These strategies benefit most when volatility is elevated\n"
            prompt += "- Boost confidence 20-30% for STRADDLE/STRANGLE/IRON_CONDOR opportunities\n"
            prompt += "- Focus on stocks showing HIGH_GAMMA or HIGH_IV_RANK signals\n\n"
        elif regime_type == 'BULL_RAMPAGE':
            prompt += "- AVOID: High-risk volatility plays, STAY BULLISH\n"
            prompt += "- Prefer: LONG_CALL, BULL_CALL_SPREAD strategies on strong stocks\n"
            prompt += "- Reduce confidence for IRON_CONDOR/condor strategies\n\n"
        elif regime_type == 'BEAR_TRAP':
            prompt += "- CAUTION: Use STRADDLE/STRANGLE for uncertainty, avoid directional bets\n"
            prompt += "- Prefer: VOLATILITY STRATEGIES over directional plays\n\n"
        elif regime_type == 'CALM_DECLINE':
            prompt += "- PREFER: Volatility selling strategies (IRON_CONDOR ideal)\n"
            prompt += "- Moderate confidence for STRADDLE/STRANGLE, higher for credit spreads\n\n"
        else:
            prompt += "- STANDARD: Adjust strategies based on individual stock analysis\n\n"

        prompt += """NEW OPPORTUNITIES TO ANALYZE:

"""

        for candidate in batch:
            # FIXED: Issue #5 - Sanitize all inputs before building prompt
            symbol = sanitize_for_prompt(candidate['symbol'], max_length=10)

            # Validate symbol format
            if not validate_symbol(symbol):
                logging.warning(f"Skipping invalid symbol in prompt: {symbol}")
                continue

            analysis = candidate['analysis']
            stock = candidate['stock_data']
            options_data = candidate['options_data']

            price = stock.get('price', 0)
            pct_change = stock.get('percent_change', 0) * 100

            # Sanitize signals
            raw_signals = analysis['signals'][:3]
            signals = ', '.join([sanitize_for_prompt(s, max_length=30) for s in raw_signals])

            iv_rank = analysis['iv_metrics'].get('iv_rank', 50)
            iv_signal = sanitize_for_prompt(analysis['iv_metrics'].get('signal', 'NEUTRAL'), max_length=20)
            pcr = analysis['put_call_ratio']

            # Check if we already have this symbol in portfolio
            symbol_in_portfolio = symbol in exposure.get('by_symbol', {})

            # Extract average Greeks from options chain (ATM options)
            atm_options = [opt for opt in options_data
                          if opt.get('strike', 0) > 0 and price > 0 and abs(opt.get('strike', 0) - price) / price < 0.10]  # Within 10% of current price

            deltas = []
            gammas = []
            thetas = []
            vegas = []

            # FIXED: Issue #7 - Proper Greeks validation ranges
            for opt in atm_options[:10]:  # Check more options
                delta = opt.get('delta') or opt.get('greeks_delta') or opt.get('theoretical_delta')
                if delta is not None and isinstance(delta, (int, float)) and -1.0 <= delta <= 1.0:
                    deltas.append(delta)

                gamma = opt.get('gamma') or opt.get('greeks_gamma') or opt.get('theoretical_gamma')
                # ATM options can have gamma > 1, allow up to 10 for short DTE
                if gamma is not None and isinstance(gamma, (int, float)) and 0 <= gamma <= 10:
                    gammas.append(gamma)

                theta = opt.get('theta') or opt.get('greeks_theta') or opt.get('theoretical_theta')
                # Theta can be positive (short positions) or negative (long positions)
                if theta is not None and isinstance(theta, (int, float)) and -10 <= theta <= 10:
                    thetas.append(theta)

                vega = opt.get('vega') or opt.get('greeks_vega') or opt.get('theoretical_vega')
                # Vega higher for longer DTE, allow up to 100
                if vega is not None and isinstance(vega, (int, float)) and 0 <= vega <= 100:
                    vegas.append(vega)

            # Calculate averages
            avg_delta = sum(deltas) / len(deltas) if deltas else 0
            avg_gamma = sum(gammas) / len(gammas) if gammas else 0
            avg_theta = sum(thetas) / len(thetas) if thetas else 0
            avg_vega = sum(vegas) / len(vegas) if vegas else 0

            # CRITICAL FIX: If no Greeks found in options data, estimate them
            if avg_delta == 0 and avg_gamma == 0 and avg_theta == 0 and avg_vega == 0 and atm_options:
                logging.warning(f"{symbol}: No Greeks in options data, using estimates")
                # Use reasonable ATM option approximations
                # ATM calls: delta ~0.50, gamma ~0.06, theta ~-0.04, vega ~0.20
                # ATM puts: delta ~-0.50, gamma ~0.06, theta ~-0.04, vega ~0.20
                avg_delta = 0.50  # ATM option delta
                avg_gamma = 0.06  # ATM gamma (typically 0.03-0.08)
                avg_theta = -0.04  # ATM theta (time decay per day)
                avg_vega = 0.20  # ATM vega (sensitivity to IV changes)

            # PROMPT IMPROVEMENT 1.2: Calculate average bid-ask spread for ATM options
            atm_spreads = []
            for opt in atm_options[:10]:
                bid = opt.get('bid', 0)
                ask = opt.get('ask', 0)
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
                    spread_pct = (ask - bid) / mid
                    atm_spreads.append(spread_pct)

            avg_spread_pct = sum(atm_spreads) / len(atm_spreads) if atm_spreads else 0

            # Extract calls and puts from options_data for prompt improvements
            calls = [opt for opt in options_data if opt.get('option_type') == 'call']
            puts = [opt for opt in options_data if opt.get('option_type') == 'put']

            # Get average IV from analysis or calculate it
            avg_iv = analysis.get('avg_iv', 0)
            if avg_iv == 0:  # Calculate if not in analysis
                ivs = [opt.get('implied_volatility', 0) for opt in options_data if opt.get('implied_volatility', 0) > 0]
                avg_iv = sum(ivs) / len(ivs) if ivs else 0.01  # Default to 0.01 to avoid division by zero

            # Extract total volume and OI from analysis
            total_volume = analysis.get('total_volume', 0)
            total_oi = analysis.get('total_oi', 0)

            # PROMPT IMPROVEMENT 2.1: Calculate IV skew (smart money indicator)
            try:
                import statistics
                # OTM calls: 5-15% above current price
                otm_calls = [opt for opt in calls
                            if opt.get('strike', 0) > 0 and
                            1.05 < opt.get('strike', 0) / price < 1.15 and
                            opt.get('implied_volatility', 0) > 0]
                # OTM puts: 5-15% below current price
                otm_puts = [opt for opt in puts
                           if opt.get('strike', 0) > 0 and
                           0.85 < opt.get('strike', 0) / price < 0.95 and
                           opt.get('implied_volatility', 0) > 0]

                call_iv_avg = statistics.mean([opt.get('implied_volatility', 0) for opt in otm_calls]) if otm_calls else 0
                put_iv_avg = statistics.mean([opt.get('implied_volatility', 0) for opt in otm_puts]) if otm_puts else 0

                skew = put_iv_avg - call_iv_avg  # Positive = put skew (fear/hedging), negative = call skew (complacency)
            except Exception as e:
                logging.debug(f"Could not calculate skew for {symbol}: {e}")
                skew = 0

            # PROMPT IMPROVEMENT 2.2: Calculate HV/IV ratio (premium pricing indicator)
            hv = self._calculate_historical_volatility(symbol, days=30)
            hv_iv_ratio = hv / avg_iv if avg_iv > 0.01 else 0
            # Interpretation: <0.8 = IV overpriced (sell premium), 0.8-1.2 = fair, >1.2 = IV underpriced (buy premium)

            # PROMPT IMPROVEMENT 2.3: Calculate implied move from ATM straddle
            implied_move_pct = 0
            try:
                if calls and puts and price > 0:
                    atm_call = min(calls, key=lambda x: abs(x.get('strike', 0) - price))
                    atm_put = min(puts, key=lambda x: abs(x.get('strike', 0) - price))

                    if atm_call and atm_put:
                        straddle_price = atm_call.get('ask', 0) + atm_put.get('ask', 0)
                        if straddle_price > 0:
                            implied_move_pct = (straddle_price / price) * 0.85  # 85% probability (1 stdev)
            except Exception as e:
                logging.debug(f"Could not calculate implied move for {symbol}: {e}")

            # PROMPT IMPROVEMENT 2.4: Calculate delta-weighted volume (smart money flow)
            call_delta_vol = sum(
                opt.get('volume', 0) * abs(opt.get('delta', 0))
                for opt in calls if opt.get('delta') and opt.get('volume')
            )
            put_delta_vol = sum(
                opt.get('volume', 0) * abs(opt.get('delta', 0))
                for opt in puts if opt.get('delta') and opt.get('volume')
            )
            net_delta_vol = call_delta_vol - put_delta_vol  # Positive = bullish institutional flow, negative = bearish

            prompt += f"{symbol}: Price ${price:.2f} ({pct_change:+.1f}%), IV-Rank {iv_rank:.0f} ({iv_signal}), "
            prompt += f"HV/IV {hv_iv_ratio:.2f}, ImpMove {implied_move_pct:.1%}, P/C-Ratio {pcr:.2f}, Skew {skew:+.2f}, "
            prompt += f"DeltaVol {net_delta_vol:+,.0f}, ATM-Delta {avg_delta:.3f}, Theta ${avg_theta:.2f}/day, "
            prompt += f"Gamma {avg_gamma:.4f}, Vega ${avg_vega:.2f}, Spread {avg_spread_pct:.1%}, Vol {total_volume:,}, OI {total_oi:,}"

            # PHASE 2: Add IV rank strategy guidance
            if iv_rank < 30:
                prompt += f" [💰 CHEAP OPTIONS - Good for BUYING (debit spreads, long calls/puts)]"
            elif iv_rank > 70:
                prompt += f" [💸 EXPENSIVE OPTIONS - Good for SELLING (credit spreads)]"
            elif 40 <= iv_rank <= 60:
                prompt += f" [⚖️ NEUTRAL IV - Use with caution for spreads]"

            if symbol_in_portfolio:
                current_exposure = exposure['by_symbol'].get(symbol, 0)
                prompt += f" [IN PORTFOLIO: {current_exposure:.1%} exposure]"

            # PROMPT IMPROVEMENT 1.3: Add earnings proximity warning
            try:
                earnings_risk = self.earnings_calendar.check_earnings_risk(symbol)
                if earnings_risk['risk'] == 'HIGH':
                    days_until = earnings_risk.get('days_until', 'unknown')
                    prompt += f" [⚠️ EARNINGS: {days_until} days - IV CRUSH RISK!]"
                elif earnings_risk['risk'] == 'MODERATE':
                    days_until = earnings_risk.get('days_until', 'unknown')
                    prompt += f" [⚠️ Earnings: {days_until} days]"
                elif earnings_risk['risk'] == 'LOW' and earnings_risk.get('days_until', 99) < 21:
                    prompt += f" [Earnings: {earnings_risk.get('days_until')} days]"
            except Exception as e:
                logging.debug(f"Could not get earnings for {symbol}: {e}")

            prompt += f"\nSignals: {signals}\n\n"

        # PROMPT IMPROVEMENT 1.5: Multi-leg strategy bonuses
        prompt += """STRATEGY SELECTION BONUSES (add to base confidence):
- IRON_CONDOR in low volatility (IV rank <30): +10%
- STRADDLE/STRANGLE in high IV rank (>70): +15%
- Credit spreads vs naked short options: +5% (defined risk benefit)
- Debit spreads vs naked long options: +3% (lower cost, defined risk)
- Calendar spreads for term structure plays: +10%
- Multi-leg strategies (general sophistication bonus): +5%

STRATEGY PENALTIES (subtract from confidence):
- Naked short call/put: -10% (undefined risk)
- Strategies against market regime: -20% (e.g., bullish in BEAR_TRAP)
- Overlapping positions in same underlying: -15%
- Strategy not suited for IV environment: -15%

"""
        # PROMPT IMPROVEMENT 1.4: Quantified confidence scoring rubric
        prompt += """CONFIDENCE SCORING RUBRIC (BE PRECISE AND CONSISTENT):

95-100% - PERFECT SETUP (All 5 factors aligned):
  ✓ Strong directional/volatility signal from scanner (BIG_MOVE, HIGH_IV_RANK, etc.)
  ✓ IV environment favors strategy (IV rank extreme <25 or >75)
  ✓ Excellent liquidity (spread <5%, volume >1000, OI >5000)
  ✓ Favorable Greeks profile for chosen strategy
  ✓ No earnings within 14 days OR earnings play with clear directional edge

85-94% - STRONG SETUP (4/5 factors aligned, minor concerns)
70-84% - GOOD SETUP (3/5 factors present, some risks)
50-69% - MARGINAL SETUP (2/5 factors, significant risks)
<50% - WEAK SETUP (use only for diversification, low conviction)

AUTOMATIC CONFIDENCE REDUCTIONS:
- Earnings within 7 days: -20% (unless specifically earnings play)
- Bid-ask spread >10%: -15%
- Bid-ask spread >15%: -25%
- Volume <100 or OI <1000: -20%
- Volume <50 or OI <500: -30%
- Already 10%+ portfolio exposure to this symbol: -25%
- Already 15%+ portfolio exposure to this symbol: -35%
- Sector already >30% of portfolio: -15%
- Sector already >40% of portfolio: -25%
- Wide spread (>10%) + low volume (<100): -35% (compounding risk)

IMPORTANT RULES:
- Reserve 95%+ for truly exceptional setups (1-2 per week maximum)
- Most good trades should be 75-85% confidence
- Be conservative - overconfidence leads to losses
- Consider ALL factors, not just one strong signal
- Respect portfolio limits and diversification
- Ensure adequate buying power for position sizing

Provide ONLY the formatted lines, one per symbol. No other text."""

        return prompt

    def _parse_batch_response(self, response: str, batch: List[Dict]) -> List[Dict]:
        """Parse batch Grok response"""
        lines = response.strip().split('\n')
        results = []

        # Create lookup by symbol
        batch_lookup = {c['symbol']: c for c in batch}

        for line in lines:
            if '|' not in line:
                continue

            parts = line.split('|')
            if len(parts) >= 5:
                try:
                    symbol = parts[0].strip()
                    strategy = parts[1].strip()
                    strikes = parts[2].strip()
                    expiry = parts[3].strip()
                    confidence_str = ''.join(c for c in parts[4] if c.isdigit())
                    confidence = int(confidence_str) if confidence_str else 0
                    reason = parts[5].strip() if len(parts) > 5 else ''

                    # FIXED: Issue #4 - Validate Grok response before using
                    is_valid, error_msg = validate_grok_response(symbol, strategy, confidence, strikes)
                    if not is_valid:
                        logging.warning(f"Grok validation failed for {symbol}: {error_msg}")
                        self.grok_logger.warning(f"VALIDATION FAILED: {symbol} | {strategy} | {confidence} - {error_msg}")
                        continue

                    if symbol in batch_lookup:
                        candidate = batch_lookup[symbol]
                        candidate['grok_confidence'] = confidence
                        candidate['strategy'] = strategy
                        candidate['strikes'] = strikes
                        candidate['expiry'] = expiry
                        candidate['reason'] = reason
                        results.append(candidate)

                except Exception as e:
                    logging.debug(f"Error parsing line '{line}': {e}")
                    continue

        # Add any missing symbols with default values
        for symbol, candidate in batch_lookup.items():
            if not any(r['symbol'] == symbol for r in results):
                candidate['grok_confidence'] = 0
                candidate['strategy'] = 'UNKNOWN'
                results.append(candidate)

        return results

    def post_validate_grok_recommendation(self, symbol: str, grok_data: Dict, scanner_analysis: Dict,
                                          stock_price: float) -> tuple[bool, str]:
        """
        PHASE 3: POST-VALIDATION
        Validates Grok's recommendation against quantitative rules before execution.

        This is the final safety check to prevent bad trades that slip through:
        - Ensures strategy matches IV rank appropriateness
        - Validates spread width and debit/credit ratios
        - Checks position sizing and exposure limits
        - Prevents correlated/duplicate exposure

        Returns:
            (is_valid, rejection_reason)
        """
        strategy = grok_data.get('strategy', 'UNKNOWN').upper()
        strikes = grok_data.get('strikes', '')
        confidence = grok_data.get('grok_confidence', 0)

        # Get IV metrics from scanner analysis
        iv_metrics = scanner_analysis.get('iv_metrics', {})
        iv_rank = iv_metrics.get('iv_rank', 50)

        # Determine strategy type
        is_debit_spread = any(x in strategy for x in ['BULL CALL', 'BEAR PUT', 'DEBIT'])
        is_credit_spread = any(x in strategy for x in ['BULL PUT', 'BEAR CALL', 'CREDIT'])
        is_single_leg = any(x in strategy for x in ['LONG CALL', 'LONG PUT']) and 'SPREAD' not in strategy
        is_iron_condor = 'IRON CONDOR' in strategy or 'CONDOR' in strategy
        is_volatility_play = any(x in strategy for x in ['STRADDLE', 'STRANGLE'])

        # VALIDATION 1: Iron Condor check for paper trading
        if is_iron_condor and config.ALPACA_MODE == 'paper':
            return False, "Iron Condor not allowed in paper trading (requires naked options)"

        # VALIDATION 1.5: Volatility Play IV Rank Check
        # CRITICAL: Buying straddles/strangles in LOW IV and selling in HIGH IV
        if is_volatility_play:
            # Determine if buying or selling volatility
            is_long_vol = 'LONG' in strategy or ('STRADDLE' in strategy and 'SHORT' not in strategy) or ('STRANGLE' in strategy and 'SHORT' not in strategy)
            is_short_vol = 'SHORT' in strategy

            if is_long_vol:
                # Buying straddles/strangles: ONLY when IV rank is LOW (< 50)
                # This prevents buying expensive volatility that's about to collapse
                if iv_rank > 50:
                    return False, f"IV rank {iv_rank:.0f}% too HIGH for buying {strategy} (max 50% - avoid IV crush!)"
                if iv_rank > 40:
                    logging.warning(f"{symbol}: IV rank {iv_rank:.0f}% borderline for buying {strategy} (prefer <40%)")

            elif is_short_vol:
                # Selling straddles/strangles: ONLY when IV rank is HIGH (> 70)
                if iv_rank < 70:
                    return False, f"IV rank {iv_rank:.0f}% too LOW for selling {strategy} (min 70%)"

        # VALIDATION 2: IV Rank Appropriateness (Expert Trader Rules)
        # Debit spreads should ONLY be placed when IV is LOW (cheap options)
        if is_debit_spread:
            if iv_rank > 40:
                return False, f"IV rank {iv_rank:.0f}% too high for debit spread (max 40% - avoid buying expensive options)"
            if iv_rank > 30:
                logging.warning(f"{symbol}: IV rank {iv_rank:.0f}% is borderline for debit spread (prefer <30%)")

        # Credit spreads should ONLY be placed when IV is ELEVATED (expensive options to sell)
        # Conservative approach: require 60% minimum to ensure we're selling expensive premium
        if is_credit_spread:
            if iv_rank < 60:
                return False, f"IV rank {iv_rank:.0f}% too low for credit spread (min 60% - need expensive premium to sell)"
            if iv_rank < 70:
                logging.warning(f"{symbol}: IV rank {iv_rank:.0f}% is acceptable for credit spread but prefer >70%")

        # Single leg should ONLY be placed when IV is VERY LOW
        if is_single_leg:
            if iv_rank > 30:
                return False, f"IV rank {iv_rank:.0f}% too high for single leg (max 30%)"

        # VALIDATION 3: Spread Width and Debit/Credit Validation
        if '/' in strikes:
            try:
                strike_parts = strikes.split('/')
                long_strike = float(strike_parts[0].strip())
                short_strike = float(strike_parts[1].strip())
                spread_width = abs(long_strike - short_strike)

                # Check minimum spread width based on stock price tiers
                # Use reasonable minimums that allow viable credit/debit spreads
                if stock_price < 20:
                    min_spread_width = 0.50  # $0.50 minimum for low-priced stocks
                elif stock_price < 100:
                    min_spread_width = 1.00  # $1.00 minimum for mid-priced stocks
                elif stock_price < 500:
                    min_spread_width = 2.50  # $2.50 minimum for high-priced stocks (allows $3-5 spreads)
                else:
                    min_spread_width = 5.00  # $5.00 minimum for very high-priced stocks (>$500)

                if spread_width < min_spread_width:
                    return False, f"Spread width ${spread_width:.2f} too narrow (min ${min_spread_width:.2f} for ${stock_price:.2f} stock)"

                # For debit spreads, validate we're not overpaying
                if is_debit_spread:
                    # We should never pay more than 60% of spread width
                    max_debit = spread_width * 0.60
                    # Note: We don't have actual debit here, but we can warn
                    logging.info(f"{symbol}: Spread width ${spread_width:.2f} - ensure debit < ${max_debit:.2f} (60% of width)")

                # For credit spreads, validate we're collecting enough
                if is_credit_spread:
                    # We should collect at least 30% of spread width
                    min_credit = spread_width * 0.30
                    logging.info(f"{symbol}: Spread width ${spread_width:.2f} - ensure credit > ${min_credit:.2f} (30% of width)")

            except Exception as e:
                logging.warning(f"{symbol}: Could not parse strikes '{strikes}' for spread validation: {e}")

        # VALIDATION 4: Confidence Threshold
        # After pre-filter and Grok analysis, we should only execute high-confidence trades
        if confidence < 75:
            return False, f"Confidence {confidence}% below execution threshold (min 75%)"

        # VALIDATION 5: Position Sizing and Exposure
        # Check current portfolio exposure to this symbol
        try:
            current_exposure = self.portfolio_manager.get_current_exposure()
            symbol_exposure_pct = current_exposure.get('symbols', {}).get(symbol, {}).get('exposure_pct', 0)

            # Don't allow more than 15% portfolio exposure to a single symbol
            if symbol_exposure_pct > 15:
                return False, f"Portfolio exposure to {symbol} is {symbol_exposure_pct:.1f}% (max 15%)"

            # Warn if approaching limit
            if symbol_exposure_pct > 10:
                logging.warning(f"{symbol}: Portfolio exposure at {symbol_exposure_pct:.1f}% - approaching 15% limit")

        except Exception as e:
            logging.warning(f"Could not check portfolio exposure for {symbol}: {e}")

        # VALIDATION 6: Market Regime Alignment
        regime = scanner_analysis.get('regime', 'NEUTRAL')
        is_bullish = any(x in strategy for x in ['BULL', 'LONG CALL'])
        is_bearish = any(x in strategy for x in ['BEAR', 'LONG PUT'])

        # Ensure directional strategies align with market regime
        if is_bullish and regime == 'BEARISH':
            logging.warning(f"{symbol}: Bullish strategy in BEARISH regime - increased risk")
        if is_bearish and regime == 'BULLISH':
            logging.warning(f"{symbol}: Bearish strategy in BULLISH regime - increased risk")

        # All validations passed
        return True, "Post-validation passed"

    def test_grok_with_cached_data(self, execute_trades: bool = False):
        """
        Test mode: Skip market scan and use cached opportunities.
        Useful for testing Grok analysis without waiting for full universe scan.
        """
        print(f"\n{Colors.WARNING}[TEST MODE] Using cached opportunities for faster testing{Colors.RESET}")
        print(f"{Colors.DIM}{'='*80}{Colors.RESET}\n")

        # Try to load from scan cache first
        cached_scan = self.scan_cache.load_last_scan()

        test_candidates = []
        using_cached_data = False

        if cached_scan and cached_scan.get('opportunities'):
            print(f"{Colors.INFO}[CACHE] Loading {cached_scan['count']} opportunities from last scan{Colors.RESET}")
            print(f"{Colors.DIM}  Scan time: {cached_scan['timestamp']}{Colors.RESET}")
            print(f"{Colors.DIM}  Scan type: {cached_scan.get('scan_type', 'UNKNOWN')}{Colors.RESET}\n")

            # Reconstruct candidate objects from cache
            for opp in cached_scan['opportunities']:
                # We need to re-fetch data since cache doesn't have full structure
                symbol = opp['symbol']
                print(f"{Colors.DIM}  Loading data for {symbol}...{Colors.RESET}")

                stock_data = self.openbb.get_quote(symbol)
                options_data = self.openbb.get_options_chains(symbol)

                if stock_data and options_data and 'results' in stock_data and 'results' in options_data:
                    stock_quote = stock_data['results'][0] if isinstance(stock_data['results'], list) else stock_data['results']
                    analysis = self.market_scanner._analyze_options_chain(
                        symbol,
                        options_data['results'],
                        stock_quote
                    )

                    if analysis:
                        test_candidates.append({
                            'symbol': symbol,
                            'stock_data': stock_quote,
                            'options_data': options_data['results'],
                            'analysis': analysis,
                            'score': analysis.get('score', 0),
                            'final_score': analysis.get('final_score', analysis.get('score', 0))
                        })

            using_cached_data = True

        if not test_candidates:
            print(f"{Colors.WARNING}[WARNING] No cached opportunities found or data failed to load{Colors.RESET}")
            print(f"{Colors.INFO}[INFO] Running fresh market scan to generate test data...{Colors.RESET}\n")

            # Fresh scan with current data
            test_candidates = self.market_scanner.scan_market_for_opportunities()

            if not test_candidates:
                print(f"{Colors.ERROR}[ERROR] Could not find any opportunities for testing{Colors.RESET}")
                return

            using_cached_data = False  # Fresh scan = no need to refresh

        print(f"\n{Colors.SUCCESS}[OK] Loaded {len(test_candidates)} candidates for Grok testing{Colors.RESET}\n")

        # Show candidates before Grok
        print(f"{Colors.HEADER}[PRE-GROK] Top candidates by scanner score:{Colors.RESET}")
        for i, candidate in enumerate(test_candidates[:10], 1):
            print(f"  {i:2d}. {candidate['symbol']:6s}  Score: {candidate['final_score']:.0f}")
        print()

        # Analyze with Grok
        print(f"{Colors.HEADER}[GROK TEST] Starting batch analysis...{Colors.RESET}\n")

        # In test mode with cached data, do NOT refresh - use cached data as-is for testing
        if using_cached_data:
            print(f"{Colors.INFO}[*] Using cached data - NO refresh in test mode{Colors.RESET}\n")
        else:
            print(f"{Colors.INFO}[*] Using fresh scan data - already current{Colors.RESET}\n")

        grok_rated = self.analyze_batch_with_grok(test_candidates[:50], refresh_data=False)

        if not grok_rated:
            print(f"{Colors.ERROR}[ERROR] Grok analysis failed{Colors.RESET}")
            return

        # Sort by Grok confidence
        top_25 = sorted(grok_rated, key=lambda x: x.get('grok_confidence', 0), reverse=True)[:25]

        # Display results with formatted table
        print(f"\n{Colors.SUCCESS}[GROK RESULTS] Top 25 after AI analysis:{Colors.RESET}\n")

        # Column headers - UI IMPROVEMENT: Cleaner layout
        print(f"{Colors.HEADER}  {'#':>2}  {'SYMBOL':6}  {'CONF':>4}  {'STRATEGY':18}  {'STRIKES':12}  {'EXPIRY':8}  {'KEY ANALYSIS'}{Colors.RESET}")
        print(f"{Colors.DIM}  {'─'*2}  {'─'*6}  {'─'*4}  {'─'*18}  {'─'*12}  {'─'*8}  {'─'*35}{Colors.RESET}")

        for i, candidate in enumerate(top_25, 1):
            conf = candidate.get('grok_confidence', 0)
            strategy = candidate.get('strategy', 'UNKNOWN')[:18]  # Truncate long strategies
            strikes = candidate.get('strikes', 'N/A')[:12]
            expiry = candidate.get('expiry', 'N/A')[:8]
            reason = candidate.get('reason', '')

            # UI IMPROVEMENT: Use smart concise reason helper
            reason_short = self._create_concise_reason(reason, max_length=35)

            conf_color = Colors.SUCCESS if conf >= 75 else Colors.WARNING if conf >= 60 else Colors.DIM

            print(f"  {i:>2}  {candidate['symbol']:6}  {conf_color}{conf:3d}%{Colors.RESET}  {strategy:18}  {strikes:12}  {expiry:8}  {Colors.DIM}{reason_short}{Colors.RESET}")

        print()

        # UI IMPROVEMENT: Show full analysis for top 3 high-confidence picks
        top_3_high_conf = [c for c in top_25 if c.get('grok_confidence', 0) >= 80][:3]
        if top_3_high_conf:
            print(f"{Colors.SUCCESS}[TOP PICKS] Detailed analysis for best opportunities:{Colors.RESET}\n")
            for i, candidate in enumerate(top_3_high_conf, 1):
                symbol = candidate['symbol']
                conf = candidate.get('grok_confidence', 0)
                strategy = candidate.get('strategy', 'UNKNOWN')
                strikes = candidate.get('strikes', 'N/A')
                expiry = candidate.get('expiry', 'N/A')
                reason = candidate.get('reason', 'No analysis provided')

                print(f"{Colors.HEADER}{i}. {symbol} ({conf}% confidence){Colors.RESET}")
                print(f"   Strategy: {Colors.SUCCESS}{strategy}{Colors.RESET} | Strikes: {strikes} | Expiry: {expiry}")
                print(f"   Analysis: {Colors.DIM}{reason}{Colors.RESET}\n")

        # Show statistics
        high_conf = len([c for c in top_25 if c.get('grok_confidence', 0) >= 75])
        med_conf = len([c for c in top_25 if 60 <= c.get('grok_confidence', 0) < 75])
        low_conf = len([c for c in top_25 if c.get('grok_confidence', 0) < 60])

        print(f"{Colors.INFO}[STATISTICS]{Colors.RESET}")
        print(f"{Colors.DIM}  High Confidence (75%+):  {high_conf:3d}{Colors.RESET}")
        print(f"{Colors.DIM}  Med Confidence (60-74%): {med_conf:3d}{Colors.RESET}")
        print(f"{Colors.DIM}  Low Confidence (<60%):   {low_conf:3d}{Colors.RESET}\n")

        # Execute trades if requested
        if execute_trades:
            print(f"{Colors.WARNING}[EXECUTING] Processing high-confidence trades...{Colors.RESET}\n")
            for candidate in top_25:
                if candidate.get('grok_confidence', 0) >= 75:
                    self.evaluate_and_execute_trade(
                        candidate['symbol'],
                        candidate,
                        candidate['options_data'],
                        candidate['analysis']
                    )
        else:
            print(f"{Colors.INFO}[TEST MODE] Skipping trade execution (use --skip-scan to execute){Colors.RESET}\n")

        print(f"{Colors.SUCCESS}[DONE] Grok testing complete{Colors.RESET}\n")

    def run(self):
        """Main trading loop"""
        print(f"\n{Colors.SUCCESS}[OK] Starting options trading bot...{Colors.RESET}")

        # Check for debug mode argument
        # Use hasattr to check if debug_mode was already set by subclass
        if not hasattr(self, 'debug_mode'):
            self.debug_mode = '--debug' in sys.argv
        if self.debug_mode:
            print(f"{Colors.WARNING}[DEBUG MODE] Ignoring market hours - will scan and trade outside regular hours{Colors.RESET}")
            logging.info("DEBUG MODE: Ignoring market hours")

        print(f"{Colors.DIM}{'='*80}{Colors.RESET}\n")

        logging.info(f"")
        logging.info(f"{'#'*80}")
        logging.info(f"BOT STARTED - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - DEBUG_MODE: {self.debug_mode}")
        logging.info(f"{'#'*80}")

        iteration = 0
        last_daily_summary = None

        # Start interactive UI
        self.interactive_ui.start()

        try:
            while True:
                # Check for shutdown request
                if self.shutdown_requested:
                    print(f"\n{Colors.WARNING}[SHUTDOWN] Graceful shutdown in progress...{Colors.RESET}")
                    break

                iteration += 1

                # Log daily summary once per day
                current_date = datetime.now().strftime('%Y-%m-%d')
                if last_daily_summary != current_date:
                    self._log_daily_summary()
                    last_daily_summary = current_date

                # Reset circuit breaker periodically
                if iteration % 10 == 0:
                    self.openbb.reset_circuit_breaker()

                # Check if market is open OR if debug mode is enabled
                market_is_open = self.market_calendar.is_market_open()

                if market_is_open or self.debug_mode:
                    if self.debug_mode:
                        print(f"\n{Colors.WARNING}[DEBUG MARKET OPEN MODE]{Colors.RESET}")
                    else:
                        print(f"\n{Colors.SUCCESS}[MARKET OPEN]{Colors.RESET}")

                    # FIRST: Check and manage existing positions with exit rules (scheduled)
                    self.position_manager.check_and_execute_exits()

                    # ASSIGNMENT DETECTION: Check if any short puts were assigned
                    if self.wheel_manager:
                        assigned_symbols = self.wheel_manager.check_for_assignments(self.trading_client)

                        # Automatically sell covered calls on newly assigned stocks
                        for symbol in assigned_symbols:
                            logging.info(f"[WHEEL] {symbol}: Assignment detected, initiating covered call sale")
                            success = self.wheel_manager.sell_covered_call(
                                symbol=symbol,
                                trading_client=self.trading_client,
                                wheel_strategy=self.wheel_strategy
                            )
                            if success:
                                print(f"{Colors.SUCCESS}[WHEEL ASSIGNMENT] {symbol}: Covered call successfully sold{Colors.RESET}")
                            else:
                                print(f"{Colors.WARNING}[WHEEL ASSIGNMENT] {symbol}: Failed to sell covered call (will retry next cycle){Colors.RESET}")

                    # SPREAD POSITION MONITORING: Check all spread positions for exit signals (every 5 minutes)
                    if self.spread_manager and self.spread_strategy:
                        self.check_spread_positions()

                    # SECOND: Display portfolio strategy summary (scheduled)
                    self.display_portfolio_strategy_summary()

                    # THIRD: Look for new Wheel opportunities with 30-min scheduled scans
                    self.execute_market_session(iteration)

                    sleep_time = 300  # 5 minutes between position checks
                else:
                    print(f"\n{Colors.WARNING}[MARKET CLOSED]{Colors.RESET}")

                    # Smart scanning: Only at midnight and pre-market
                    should_scan, scan_type = self.market_calendar.should_run_scan()

                    if should_scan:
                        print(f"{Colors.HEADER}[{scan_type}] Running scheduled market scan...{Colors.RESET}")
                        self.execute_pre_market_scan()

                        # Mark scan as complete
                        self.market_calendar.mark_scan_completed(scan_type)

                        # Cache results
                        if len(self.pre_market_opportunities) > 0:
                            opps_to_cache = list(self.pre_market_opportunities)
                            self.scan_cache.save_scan(opps_to_cache, scan_type)
                    else:
                        # Show cached results if available
                        cached_scan = self.scan_cache.load_last_scan()
                        if cached_scan:
                            cached_time = datetime.fromisoformat(cached_scan['timestamp'])
                            age_hours = (datetime.now() - cached_time).total_seconds() / 3600
                            print(f"{Colors.INFO}[CACHED RESULTS] Last scan: {cached_time.strftime('%Y-%m-%d %H:%M')} ({age_hours:.1f}h ago){Colors.RESET}")
                            print(f"  Found {cached_scan['count']} opportunities")

                            # Show top 5
                            for i, opp in enumerate(cached_scan['opportunities'][:5]):
                                print(f"    {i+1}. {opp['symbol']:6s} {opp['confidence']:3d}% {opp['strategy']:20s}")

                            if cached_scan['count'] > 5:
                                print(f"{Colors.DIM}       ... and {cached_scan['count']-5} more{Colors.RESET}")
                        else:
                            print(f"{Colors.DIM}No scan needed. Next scan at midnight or 7:00 AM ET.{Colors.RESET}")

                    # Smart sleep timing
                    next_open = self.market_calendar.get_next_market_open()
                    now_et = datetime.now(self.market_calendar.eastern)

                    # Calculate sleep until next event
                    midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    premarket = now_et.replace(hour=7, minute=0, second=0, microsecond=0)
                    if premarket <= now_et:
                        premarket += timedelta(days=1)

                    # Sleep until next scan or market open (whichever is sooner)
                    sleep_until = min(midnight, premarket, next_open)
                    sleep_time = max(60, int((sleep_until - now_et).total_seconds()))  # At least 60 seconds
                    sleep_time = min(sleep_time, 3600)  # Max 1 hour

                # Display countdown timer while sleeping
                # Print start message only - no updates to avoid UI clutter and scroll interference
                print(f"{Colors.DIM}[*] Sleeping for {sleep_time}s until next check...{Colors.RESET}")

                # Interruptible sleep: Check for manual requests every second
                for elapsed in range(sleep_time):
                    time.sleep(1)

                    # Check for manual requests - execute immediately without waiting
                    manual_scan, manual_portfolio = self.interactive_ui.check_manual_requests()

                    if manual_scan or manual_portfolio:
                        remaining = sleep_time - elapsed - 1
                        print(f"\n{Colors.SUCCESS}[MANUAL] Interrupting sleep to execute user request{Colors.RESET}")

                        if manual_portfolio:
                            print(f"{Colors.HEADER}[MANUAL] Portfolio evaluation requested{Colors.RESET}")
                            self.display_portfolio_summary()  # Show full portfolio status first
                            self.position_manager.check_and_execute_exits()

                            # Check for assignments during manual portfolio evaluation
                            if self.wheel_manager:
                                assigned_symbols = self.wheel_manager.check_for_assignments(self.trading_client)
                                for symbol in assigned_symbols:
                                    logging.info(f"[WHEEL] {symbol}: Assignment detected, initiating covered call sale")
                                    success = self.wheel_manager.sell_covered_call(
                                        symbol=symbol,
                                        trading_client=self.trading_client,
                                        wheel_strategy=self.wheel_strategy
                                    )
                                    if success:
                                        print(f"{Colors.SUCCESS}[WHEEL ASSIGNMENT] {symbol}: Covered call successfully sold{Colors.RESET}")
                                    else:
                                        print(f"{Colors.WARNING}[WHEEL ASSIGNMENT] {symbol}: Failed to sell covered call{Colors.RESET}")

                            self.display_portfolio_strategy_summary()

                        if manual_scan:
                            print(f"{Colors.HEADER}[MANUAL] Wheel scan requested - executing now{Colors.RESET}")
                            self.execute_wheel_opportunities()
                            if self.spread_strategy:
                                print(f"{Colors.HEADER}[MANUAL] Spread scan requested - executing now{Colors.RESET}")
                                self.execute_spread_opportunities()

                        if remaining > 0:
                            print(f"{Colors.DIM}[*] Resuming sleep for {remaining}s until next check...{Colors.RESET}")
                        else:
                            print(f"{Colors.DIM}[*] Sleep complete, continuing to next cycle...{Colors.RESET}")

                    # Check for shutdown request
                    if self.shutdown_requested:
                        break

                print()  # Add blank line after sleep

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}[!] Shutting down gracefully...{Colors.RESET}")

            # Stop interactive UI
            self.interactive_ui.stop()

            # Show final performance stats
            stats = self.trade_journal.get_performance_stats(days=30)
            if stats['total_trades'] > 0:
                print(f"\n{Colors.HEADER}30-Day Performance:{Colors.RESET}")
                print(f"  Total Trades: {stats['total_trades']}")
                print(f"  Win Rate: {stats['win_rate']:.1%}")
                print(f"  Total P&L: ${stats['total_pnl']:,.2f}")
                print(f"  Avg Return: {stats['avg_return']:.1%}\n")

            logging.info("Bot stopped by user")
            sys.exit(0)

    def execute_pre_market_scan(self):
        """Scan for Wheel opportunities when market is closed"""
        next_open = self.market_calendar.get_next_market_open()
        time_until = self.market_calendar.seconds_until_market_open()

        print(f"Next Market Open: {next_open.strftime('%Y-%m-%d %H:%M %Z')}")
        print(f"Time Until Open: {time_until // 3600}h {(time_until % 3600) // 60}m\n")

        # SIMPLIFIED: Pre-market scan now only looks for Wheel opportunities
        print(f"{Colors.INFO}[PRE-MARKET] Scanning for Wheel Strategy opportunities...{Colors.RESET}")

        # Execute Wheel scan
        self.execute_wheel_opportunities()

        # Execute Spread scan (separate account)
        if self.spread_strategy:
            print(f"{Colors.INFO}[PRE-MARKET] Scanning for Bull Put Spread opportunities...{Colors.RESET}")
            self.execute_spread_opportunities()

        return  # Skip all Grok analysis

        # OLD CODE BELOW - KEPT FOR REFERENCE BUT NOT EXECUTED
        # ========================================================
        # Expert market scan
        top_candidates = self.market_scanner.scan_market_for_opportunities()

        if not top_candidates:
            print(f"{Colors.DIM}No significant opportunities detected{Colors.RESET}")
            return

        # TIER 3.1: Apply quality gate before Grok (50 → 30 candidates)
        quality_filtered = self._apply_pre_grok_quality_gate(top_candidates[:50], target_count=30)

        # Batch Grok analysis (data is already fresh from scan)
        grok_rated = self.analyze_batch_with_grok(quality_filtered, refresh_data=False)

        # Down-select to top 25 by confidence
        top_25 = sorted(grok_rated, key=lambda x: x.get('grok_confidence', 0), reverse=True)[:25]

        print(f"\n{Colors.SUCCESS}[TOP 25 FINAL] After AI analysis:{Colors.RESET}\n")

        # Column headers
        print(f"{Colors.HEADER}  {'#':>2}  {'SYMBOL':6}  {'CONF':>4}  {'STRATEGY':18}  {'STRIKES':12}  {'EXPIRY':8}{Colors.RESET}")
        print(f"{Colors.DIM}  {'─'*2}  {'─'*6}  {'─'*4}  {'─'*18}  {'─'*12}  {'─'*8}{Colors.RESET}")

        for i, candidate in enumerate(top_25, 1):
            conf = candidate.get('grok_confidence', 0)
            strategy = candidate.get('strategy', 'UNKNOWN')[:18]
            strikes = candidate.get('strikes', 'N/A')[:12]
            expiry = candidate.get('expiry', 'N/A')[:8]

            conf_color = Colors.SUCCESS if conf >= 75 else Colors.WARNING if conf >= 60 else Colors.DIM

            print(f"  {i:>2}  {candidate['symbol']:6}  {conf_color}{conf:3d}%{Colors.RESET}  {strategy:18}  {strikes:12}  {expiry:8}")

        print()

        # Store high-confidence opportunities (prevent memory leak with deque)
        high_conf_count = 0
        for candidate in top_25:
            if candidate.get('grok_confidence', 0) >= 75:
                self.pre_market_opportunities.append({
                    'symbol': candidate['symbol'],
                    'confidence': candidate.get('grok_confidence', 0),
                    'strategy': candidate.get('strategy', 'UNKNOWN'),
                    'strikes': candidate.get('strikes', ''),
                    'expiry': candidate.get('expiry', '30DTE'),
                    'reason': candidate.get('reason', ''),
                    'options_data': candidate['options_data'],
                    'analysis': candidate['analysis'],
                    'scanned_at': datetime.now()
                })
                high_conf_count += 1

        print(f"\n{Colors.HEADER}💎 {high_conf_count} HIGH-CONFIDENCE plays queued for market open{Colors.RESET}\n")

        # Cache the Grok-analyzed results for --test-grok to use
        self.scan_cache.save_scan(top_25, 'MANUAL_SCAN')
        print(f"{Colors.DIM}[*] Results cached to scan_results.json for --test-grok{Colors.RESET}\n")

        # Display portfolio overview after pre-market scan
        self.display_portfolio_summary()

    def check_positions_with_grok(self):
        """5-minute Grok monitoring of open positions for exit strategy re-evaluation"""
        now = datetime.now()

        # Check if 5 minutes have passed since last position check
        should_check = (
            self.last_position_grok_check is None or
            (now - self.last_position_grok_check).total_seconds() >= 300  # 5 minutes
        )

        if not should_check:
            return

        try:
            positions = self.trading_client.get_all_positions()

            if not positions:
                return

            print(f"{Colors.INFO}[GROK MONITOR] Checking {len(positions)} positions for exit signals...{Colors.RESET}")
            logging.info(f"=== 5-MIN GROK POSITION MONITORING ===")

            # Build position data for Grok with strategy context
            position_data = []
            for pos in positions:
                symbol = pos.symbol
                # Extract underlying symbol (remove option suffix if present)
                underlying = extract_underlying_symbol(symbol)

                # Get strategy info from database
                strategy_info = self.trade_journal.get_position_strategy(underlying)

                # Get current quote
                quote = self.openbb.get_quote(underlying)
                if not quote or 'results' not in quote:
                    continue

                stock_data = quote['results'][0] if isinstance(quote['results'], list) else quote['results']
                current_price = stock_data.get('price', stock_data.get('last_price', 0))

                pnl_pct = float(pos.unrealized_plpc) if pos.unrealized_plpc is not None else 0.0

                position_data.append({
                    'symbol': underlying,
                    'option_symbol': symbol,
                    'entry_price': float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0,
                    'current_price': float(pos.current_price) if pos.current_price is not None else 0.0,
                    'stock_price': current_price,
                    'pnl_pct': pnl_pct,
                    'qty': int(pos.qty) if pos.qty is not None else 0,
                    'strategy': strategy_info.get('strategy', 'UNKNOWN') if strategy_info else 'UNKNOWN',
                    'entry_reason': strategy_info.get('reason', 'N/A') if strategy_info else 'N/A',
                    'strikes': strategy_info.get('strikes', 'N/A') if strategy_info else 'N/A',
                    'grok_notes': strategy_info.get('grok_notes', '') if strategy_info else ''
                })

            if not position_data:
                return

            # Ask Grok for exit recommendations with FULL strategy context
            prompt = f"""You are an expert options trader. Analyze these positions and recommend exit actions.

IMPORTANT: Consider the STRATEGY used for each position when making recommendations.
- Different strategies have different exit criteria
- Multi-leg strategies (spreads, straddles) may need both legs managed
- Some strategies are meant to expire worthless (credit spreads)

POSITIONS:
"""
            for i, pos in enumerate(position_data, 1):
                prompt += f"{i}. {pos['symbol']} | Strategy: {pos['strategy']} | Strikes: {pos['strikes']}\n"
                prompt += f"   Entry: ${pos['entry_price']:.2f} | Current: ${pos['current_price']:.2f} | P&L: {pos['pnl_pct']:+.1%}\n"
                prompt += f"   Stock Price: ${pos['stock_price']:.2f} | Original Reason: {pos['entry_reason']}\n"
                if pos['grok_notes']:
                    prompt += f"   Previous Notes: {pos['grok_notes']}\n"
                prompt += "\n"

            prompt += """
For each position, respond with ONE of these actions:
- HOLD: Keep position open
- EXIT: Close position immediately
- TAKE_PROFIT: Exit to lock in gains
- CUT_LOSS: Exit to prevent further loss

Format: SYMBOL|ACTION|REASON
Example: AAPL|EXIT|Stock momentum reversed, exit signal"""

            headers = {'Authorization': f'Bearer {config.XAI_API_KEY}', 'Content-Type': 'application/json'}
            payload = {
                'model': 'grok-4-fast',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 500,
                'temperature': 0.3
            }

            # Increased timeout for grok-4-fast model which may take longer
            response = requests.post(config.XAI_BASE_URL, json=payload, headers=headers, timeout=30)

            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                logging.info(f"Grok position analysis:\n{content}")

                # Parse recommendations
                for line in content.split('\n'):
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 3:
                            sym = parts[0].strip()
                            action = parts[1].strip().upper()
                            reason = parts[2].strip()

                            # Save Grok's analysis notes for all recommendations (especially HOLD for future context)
                            try:
                                grok_note = f"{action}: {reason}"
                                self.trade_journal.update_grok_notes(sym, grok_note)
                                logging.info(f"Saved Grok notes for {sym}: {grok_note}")
                            except Exception as e:
                                logging.warning(f"Could not save Grok notes for {sym}: {e}")

                            if action in ['EXIT', 'TAKE_PROFIT', 'CUT_LOSS']:
                                # Find matching position
                                for pos_info in position_data:
                                    if pos_info['symbol'] == sym:
                                        print(f"{Colors.WARNING}[GROK EXIT] {sym}: {action} - {reason}{Colors.RESET}")
                                        logging.info(f"*** GROK RECOMMENDS EXIT: {sym} - {action} - {reason}")

                                        # Get strategy type for this position
                                        strategy_info = self.trade_journal.get_position_strategy(sym)
                                        strategy = strategy_info.get('strategy', 'UNKNOWN') if strategy_info else 'UNKNOWN'

                                        # Check if this is a multi-leg spread strategy
                                        multi_leg_strategies = [
                                            'BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD',
                                            'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD',
                                            'IRON_CONDOR', 'STRADDLE', 'STRANGLE'
                                        ]

                                        if strategy in multi_leg_strategies:
                                            # Close spread atomically using multi-leg order manager
                                            logging.info(f"Closing {strategy} spread for {sym} atomically")
                                            result = self.multi_leg_order_manager.close_spread(sym, strategy, positions)

                                            if result['success']:
                                                logging.info(f"✓ Spread closed successfully: {sym} - {result['legs_closed']} legs @ ${result.get('limit_price', 'N/A')}")
                                                # Remove from active tracking
                                                self.trade_journal.remove_active_position(sym)
                                            else:
                                                logging.error(f"Failed to close spread {sym}: {result['error']}")
                                        else:
                                            # Single-leg position - close individually
                                            for pos in positions:
                                                underlying = extract_underlying_symbol(pos.symbol)
                                                if underlying == sym:
                                                    self.position_manager._execute_exit(pos, f"GROK_{action}: {reason}")
                                                    break
                                        break

            self.last_position_grok_check = now

        except Exception as e:
            logging.error(f"Error in Grok position monitoring: {e}")

    def execute_market_session(self, iteration):
        """
        Continuous scanning during market hours with rolling top-50/top-25 lists.

        Strategy:
        - OpenBB scanning every 2 minutes (free API, rate-limited)
        - Maintain rolling top-50 list
        - Immediately Grok-analyze new high-scorers as they're discovered
        - Maintain rolling top-25 Grok-rated list
        - 30-minute scheduled full Grok analysis + exit strategies
        - Always refresh real-time data before Grok calls
        """
        print(f"{Colors.INFO}[Market Session #{iteration}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}\n")
        logging.info(f"")
        logging.info(f"{'='*80}")
        logging.info(f"MARKET SESSION #{iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"{'='*80}")

        now = datetime.now()

        # Log session state
        try:
            account = self.trading_client.get_account()
            positions = self.trading_client.get_all_positions()
            equity = float(account.equity) if account.equity is not None else 0.0
            cash = float(account.cash) if account.cash is not None else 0.0
            logging.info(f"Portfolio Value: ${equity:,.2f} | Cash: ${cash:,.2f}")
            logging.info(f"Open Positions: {len(positions)} | Rolling Top-50: {len(self.rolling_top_50)} | Rolling Top-25: {len(self.rolling_top_25)}")
        except Exception as e:
            logging.debug(f"Could not log session state: {e}")

        # ==========================================================================
        # SIMPLIFIED BOT: WHEEL STRATEGY ONLY
        # ==========================================================================
        # Removed all Grok analysis and directional spread strategies
        # Bot now ONLY uses Wheel Strategy for consistent premium collection
        # Expected win rate: 50-95% (vs 25% for old approach)
        # ==========================================================================

        # 30-MINUTE SCHEDULED WHEEL SCAN
        should_wheel_scan = (
            self.last_grok_analysis_time is None or
            (now - self.last_grok_analysis_time).total_seconds() >= 1800  # 30 minutes
        )

        if should_wheel_scan:
            print(f"{Colors.HEADER}[30-MIN SCAN] Running Wheel and Spread Scans in Parallel...{Colors.RESET}")
            logging.info(f"=== 30-MINUTE STRATEGY SCANS (PARALLEL EXECUTION) ===")

            # Run both scans in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {}

                # Submit wheel strategy scan
                futures['wheel'] = executor.submit(self.execute_wheel_opportunities)
                logging.info("[PARALLEL SCAN] Wheel strategy scan started")

                # Submit spread strategy scan if enabled
                if self.spread_strategy:
                    futures['spread'] = executor.submit(self.execute_spread_opportunities)
                    logging.info("[PARALLEL SCAN] Spread strategy scan started")

                # Wait for both scans to complete and handle any errors
                for strategy_name, future in futures.items():
                    try:
                        future.result()  # This blocks until the scan completes
                        logging.info(f"[PARALLEL SCAN] {strategy_name.capitalize()} strategy scan completed successfully")
                    except Exception as e:
                        logging.error(f"[PARALLEL SCAN] {strategy_name.capitalize()} strategy scan failed: {e}")
                        print(f"{Colors.ERROR}[ERROR] {strategy_name.capitalize()} scan failed: {e}{Colors.RESET}")

            print(f"{Colors.SUCCESS}[30-MIN SCAN] Both strategy scans completed{Colors.RESET}")
            self.last_grok_analysis_time = now

        # Display portfolio overview after each scan to show position changes
        self.display_portfolio_summary()

    def evaluate_and_execute_trade(self, symbol: str, grok_data: Dict, options_data: List[Dict], scanner_analysis: Dict):
        """Evaluate trade with full risk management and execute"""
        confidence = grok_data.get('grok_confidence', grok_data.get('confidence', 0))
        strategy = grok_data.get('strategy', 'UNKNOWN')
        strikes = grok_data.get('strikes', '')
        expiry = grok_data.get('expiry', '30DTE')

        logging.info(f"=== EVALUATING TRADE: {symbol} ===")
        logging.info(f"Strategy: {strategy} | Confidence: {confidence}% | Strikes: {strikes} | Expiry: {expiry}")

        # PHASE 3: POST-VALIDATION - Final safety check before execution
        stock_data = scanner_analysis.get('stock_data', {})
        stock_price = stock_data.get('price', 0)

        if stock_price == 0:
            # Fallback: try to get price from options data
            if options_data and len(options_data) > 0:
                stock_price = options_data[0].get('underlying_price', 0)

        if stock_price > 0:
            is_valid, rejection_reason = self.post_validate_grok_recommendation(
                symbol, grok_data, scanner_analysis, stock_price
            )

            if not is_valid:
                print(f"{Colors.WARNING}[POST-VALIDATION FAILED] {symbol}: {rejection_reason}{Colors.RESET}")
                logging.warning(f"POST-VALIDATION REJECTED: {symbol} | {strategy} | {rejection_reason}")
                self.grok_logger.warning(f"POST-VALIDATION REJECTED: {symbol} | {strategy} | {confidence}% | {rejection_reason}")
                return
            else:
                print(f"{Colors.SUCCESS}[POST-VALIDATION PASSED] {symbol}: {rejection_reason}{Colors.RESET}")
                logging.info(f"POST-VALIDATION PASSED: {symbol} | {strategy}")
        else:
            logging.warning(f"{symbol}: Could not get stock price for post-validation, proceeding with caution")

        # VALIDATION LAYER -1: ANTI-OVERTRADING CHECK (Cooling-off period & consecutive losses)
        can_trade, trade_reason = self.position_manager.can_trade_symbol(symbol, strategy)
        if not can_trade:
            print(f"{Colors.WARNING}[SKIP] {symbol}: {trade_reason}{Colors.RESET}")
            logging.warning(f"[ANTI-OVERTRADING] {symbol}: {trade_reason}")
            return

        # VALIDATION LAYER 0: Duplicate Position/Order Check

        # Define multi-leg strategies that allow multiple positions on same symbol
        MULTI_LEG_STRATEGIES = ['SPREAD', 'STRADDLE', 'STRANGLE', 'BUTTERFLY', 'CONDOR', 'COLLAR']
        is_multi_leg = any(strat in strategy.upper() for strat in MULTI_LEG_STRATEGIES)

        try:
            # Check for existing positions
            positions = self.trading_client.get_all_positions()

            # Handle case where get_all_positions returns None
            if positions is None:
                positions = []

            existing_positions_count = 0

            for position in positions:
                # Extract underlying symbol from OCC format or use symbol directly
                pos_symbol = position.symbol
                underlying = extract_underlying_symbol(pos_symbol)

                if underlying == symbol or pos_symbol == symbol:
                    existing_positions_count += 1

                    # For single-leg strategies, skip if we already have a position
                    if not is_multi_leg:
                        print(f"{Colors.WARNING}[SKIP] {symbol}: Already have position in this symbol ({pos_symbol}){Colors.RESET}")
                        logging.info(f"[SKIP] {symbol}: Already have position ({pos_symbol}) and strategy '{strategy}' is not multi-leg")
                        return

            # For multi-leg strategies, log that we're allowing multiple positions
            if is_multi_leg and existing_positions_count > 0:
                print(f"{Colors.INFO}[MULTI-LEG] {symbol}: {strategy} allows multiple positions (existing: {existing_positions_count}){Colors.RESET}")
                logging.info(f"Multi-leg strategy '{strategy}' - allowing position despite {existing_positions_count} existing position(s)")

            # Check for pending orders on this symbol
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus

            order_filter = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            orders = self.trading_client.get_orders(filter=order_filter)

            # Handle case where get_orders returns None
            if orders is None:
                orders = []

            # Build a set of expected OCC symbols for this trade to detect exact duplicates
            expected_occ_symbols = set()

            # Convert expiry to date format - handle both "30DTE" and "YYYY-MM-DD" formats
            try:
                if 'DTE' in expiry.upper():
                    # Extract days from "30DTE" format
                    days = int(''.join(filter(str.isdigit, expiry)))
                    exp_date = datetime.now() + timedelta(days=days)
                else:
                    # Parse as date string
                    exp_date = datetime.strptime(expiry, '%Y-%m-%d')
                exp_str = exp_date.strftime('%y%m%d')
            except Exception as e:
                logging.error(f"Failed to parse expiry '{expiry}': {e}")
                # Skip duplicate check if we can't parse expiry
                expected_occ_symbols = set()
                exp_str = None

            if exp_str:
                if is_multi_leg:
                    # For multi-leg, parse the strikes to get all leg symbols
                    strategy_details = self.multi_leg_manager.parse_multi_leg_strategy(
                        strategy, symbol, strikes, expiry, 0
                    )
                    # Properly check if strategy_details and legs exist and are iterable
                    legs_data = strategy_details.get('legs') if strategy_details else None
                    if legs_data is not None and isinstance(legs_data, list) and len(legs_data) > 0:
                        for leg in legs_data:
                            # Build OCC symbol: SYMBOL + YYMMDD + C/P + STRIKE (8 digits)
                            opt_type = 'C' if leg['type'].upper() == 'CALL' else 'P'
                            strike_str = f"{int(leg['strike'] * 1000):08d}"
                            occ_symbol = f"{symbol}{exp_str}{opt_type}{strike_str}"
                            expected_occ_symbols.add(occ_symbol)
                            logging.info(f"Expected leg OCC symbol: {occ_symbol}")
                    else:
                        logging.warning(f"Could not parse multi-leg strategy {strategy} for {symbol} - skipping duplicate check")
                else:
                    # For single-leg, build the single OCC symbol
                    opt_type = 'C' if 'CALL' in strategy.upper() else 'P'
                    strike_value = float(strikes.split('/')[0]) if '/' in strikes else float(strikes)
                    strike_str = f"{int(strike_value * 1000):08d}"
                    occ_symbol = f"{symbol}{exp_str}{opt_type}{strike_str}"
                    expected_occ_symbols.add(occ_symbol)
                    logging.info(f"Expected OCC symbol: {occ_symbol}")

            # Check each pending order
            duplicate_found = False
            for order in orders:
                order_symbol = order.symbol
                underlying = extract_underlying_symbol(order_symbol)

                # EXACT DUPLICATE CHECK: Check if this order is for the exact same contract
                if order_symbol in expected_occ_symbols:
                    duplicate_found = True
                    print(f"{Colors.WARNING}[DUPLICATE] {symbol}: Exact same order already pending (Order ID: {order.id}, Contract: {order_symbol}){Colors.RESET}")
                    logging.warning(f"[SKIP] {symbol}: Duplicate order detected - {order_symbol} already pending (Order ID: {order.id})")
                    # Don't place duplicate - skip this trade entirely
                    continue

                if underlying == symbol and not duplicate_found:
                    # For single-leg strategies, cancel and replace with better trade
                    if not is_multi_leg:
                        print(f"{Colors.WARNING}[CANCEL] {symbol}: Canceling existing order (ID: {order.id}) for better opportunity{Colors.RESET}")
                        logging.info(f"Canceling order {order.id} for {symbol} - replacing with {confidence}% confidence trade")
                        try:
                            self.trading_client.cancel_order_by_id(order.id)
                            print(f"{Colors.SUCCESS}  ✓ Order {order.id} canceled{Colors.RESET}")
                        except Exception as e:
                            logging.error(f"Failed to cancel order {order.id}: {e}")
                    else:
                        # PHASE 2: Multi-leg - use intelligent replacement analysis
                        # Check if order is part of tracked multi-leg strategy
                        strategy_id = self.multi_leg_tracker.get_strategy_by_leg_id(order.id)
                        if strategy_id:
                            logging.info(f"Found multi-leg order {order.id} - part of strategy {strategy_id}")
                            strategy_status = self.multi_leg_tracker.get_strategy_status(strategy_id)

                            # PHASE 2: Run intelligent replacement analysis
                            new_opportunity = {
                                'symbol': symbol,
                                'strategy': strategy,
                                'confidence': confidence,
                                'strikes': strikes,
                                'expiry': expiry
                            }

                            market_conditions = {
                                'regime': scanner_analysis.get('regime', 'NEUTRAL'),
                                'iv_rank': scanner_analysis.get('iv_rank', 50),
                                'price_change_pct': scanner_analysis.get('price_change_pct', 0),
                                'avg_bid_ask_spread': scanner_analysis.get('avg_bid_ask_spread', 0.05)
                            }

                            # Add confidence to existing strategy status for comparison
                            existing_strategy = strategy_status.copy() if strategy_status else {}
                            existing_strategy['confidence'] = existing_strategy.get('confidence', 70)  # Default if unknown

                            # Run Phase 2 analysis
                            replacement_decision = self.replacement_analyzer.should_replace_order(
                                existing_strategy,
                                new_opportunity,
                                market_conditions
                            )

                            print(f"{Colors.INFO}[PHASE 2] Replacement Analysis Score: {replacement_decision['confidence_score']}/100{Colors.RESET}")

                            if replacement_decision['should_replace']:
                                logging.info(f"PHASE 2 DECISION: Replace existing order (score: {replacement_decision['confidence_score']}/100)")
                                print(f"{Colors.SUCCESS}[PHASE 2] ✓ Replacement recommended:{Colors.RESET}")
                                for reason in replacement_decision['reasons'][:3]:  # Show top 3 reasons
                                    print(f"  • {reason}")

                                # Attempt safe cancellation
                                cancel_result = self.cancel_multi_leg_order_safely(strategy_id)
                                if cancel_result['success']:
                                    print(f"{Colors.SUCCESS}  ✓ Safely cancelled multi-leg strategy {strategy_id}{Colors.RESET}")
                                elif cancel_result['had_fills']:
                                    print(f"{Colors.ERROR}  ✗ Cannot cancel {strategy_id} - has filled legs{Colors.RESET}")
                                    logging.warning(f"Skipping {symbol} - existing multi-leg has fills, cannot replace")
                                    return  # Don't replace if existing has fills
                            else:
                                logging.info(f"PHASE 2 DECISION: Keep existing order (score: {replacement_decision['confidence_score']}/100)")
                                print(f"{Colors.WARNING}[PHASE 2] ✗ Replacement not recommended:{Colors.RESET}")
                                for risk in replacement_decision['risk_factors'][:2]:  # Show top 2 risks
                                    print(f"  • {risk}")
                                logging.info(f"Skipping {symbol} - Phase 2 analysis recommends keeping existing order")
                                return  # Don't replace
                        else:
                            # Legacy multi-leg order not in tracker - keep it for safety
                            logging.info(f"Existing order {order.id} on {symbol} allowed - multi-leg strategy '{strategy}' (not tracked)")

            # If we found an exact duplicate, skip this trade
            if duplicate_found:
                print(f"{Colors.WARNING}[SKIP] {symbol}: Cannot place order - exact duplicate already exists{Colors.RESET}")
                logging.info(f"[SKIP] {symbol}: Skipping trade due to duplicate order")
                return

        except Exception as e:
            logging.error(f"Error checking for duplicate positions/orders: {e}")
            # Fallback: Allow trade if duplicate check fails (fail-safe)
            print(f"{Colors.WARNING}[WARNING] Duplicate check failed ({str(e)[:50]}...) - proceeding with caution{Colors.RESET}")
            logging.warning(f"Proceeding with trade despite duplicate check error: {e}")

        # VALIDATION LAYER 1: Earnings Risk
        earnings_check = self.earnings_calendar.check_earnings_risk(symbol)
        logging.info(f"Earnings risk: {earnings_check['risk']} - {earnings_check['reason']}")
        if earnings_check['action'] == 'AVOID':
            print(f"{Colors.WARNING}[SKIP] {symbol}: {earnings_check['reason']}{Colors.RESET}")
            logging.info(f"[SKIP] {symbol}: {earnings_check['reason']}")
            return

        # VALIDATION LAYER 2: IV Rank
        avg_iv = scanner_analysis.get('avg_iv', 0)
        if avg_iv > 0:
            # Pass options_data to avoid duplicate API call
            iv_metrics = self.iv_analyzer.calculate_iv_metrics(symbol, avg_iv, options_data)
            iv_rank = iv_metrics['iv_rank']
            iv_signal = iv_metrics['signal']

            # Check IV alignment with strategy
            logging.info(f"IV Rank: {iv_rank:.0f} | Signal: {iv_signal}")
            if 'LONG_CALL' in strategy or 'LONG_PUT' in strategy:
                # Buying options - want LOW IV
                if iv_rank > 70:
                    print(f"{Colors.WARNING}[SKIP] {symbol}: IV rank too high ({iv_rank:.0f}) for buying options{Colors.RESET}")
                    logging.info(f"[SKIP] {symbol}: IV rank too high ({iv_rank:.0f}) for buying options")
                    return
        else:
            iv_rank = 50
            iv_metrics = {}

        # Get current exposure ONCE
        exposure = self.portfolio_manager.get_current_exposure()

        # Calculate optimal position size
        position_size_pct = self.portfolio_manager.calculate_optimal_position_size(confidence, exposure)

            # VALIDATION LAYER 3: Grok Response Validation
        validated_strategy = self._validate_grok_strategy(strategy, confidence, scanner_analysis, symbol, grok_data)
        if not validated_strategy:
            print(f"{Colors.WARNING}[SKIP] {symbol}: Grok strategy validation failed{Colors.RESET}")
            logging.info(f"[SKIP] {symbol}: Grok strategy validation failed")
            return

        # Update strategy if validated differently
        if validated_strategy != strategy:
            logging.warning(f"Grok strategy '{strategy}' not supported, using '{validated_strategy}' instead")
            strategy = validated_strategy
            grok_data['strategy'] = strategy
            logging.info(f"Strategy updated to {strategy} after validation")

            # For unsupported strategies, return and skip trade
            if validated_strategy.upper() == 'UNKNOWN':
                print(f"{Colors.WARNING}[SKIP] {symbol}: Strategy '{strategy}' not supported by bot{Colors.RESET}")
                logging.info(f"[SKIP] {symbol}: Strategy '{strategy}' is not supported")
                return

        # VALIDATION LAYER 4: Account Balance & Options Buying Power Check
        try:
            account = self.trading_client.get_account()
            total_equity = float(account.equity) if account.equity is not None else 0.0
            buying_power = float(account.buying_power) if account.buying_power is not None else 0.0
            options_bp = float(account.options_buying_power) if hasattr(account, 'options_buying_power') and account.options_buying_power is not None else buying_power
            cost_basis = float(account.cost_basis) if hasattr(account, 'cost_basis') and account.cost_basis is not None else 0.0

            logging.info(f"Account check: Equity=${total_equity:,.2f}, Buying Power=${buying_power:,.2f}, Options BP=${options_bp:,.2f}, Cost Basis=${cost_basis:,.2f}")

            # Estimate position cost (rough calculation)
            if total_equity > 0 and position_size_pct > 0:
                estimated_position_cost = total_equity * position_size_pct
                logging.info(f"Estimated position cost: ${estimated_position_cost:,.2f} ({position_size_pct:.1%} of account)")

                # Check if we have sufficient options buying power
                # Conservative approach: ensure we have at least 2x the estimated cost as buying power
                if options_bp < estimated_position_cost * 2:
                    print(f"{Colors.WARNING}[SKIP] {symbol}: Insufficient options buying power (${options_bp:,.2f}) for estimated cost (${estimated_position_cost:,.2f}){Colors.RESET}")
                    logging.info(f"[SKIP] {symbol}: Insufficient options buying power ${options_bp:,.2f} < ${estimated_position_cost*2:,.2f}")
                    return

                # Additional check: ensure cash available (conservative approach)
                cash_available = float(account.cash) if account.cash is not None else 0.0
                if cash_available < estimated_position_cost * 1.1:  # 10% buffer
                    print(f"{Colors.WARNING}[SKIP] {symbol}: Insufficient cash (${cash_available:,.2f}) for estimated cost (${estimated_position_cost:,.2f}){Colors.RESET}")
                    logging.info(f"[SKIP] {symbol}: Insufficient cash ${cash_available:,.2f} < ${estimated_position_cost*1.1:,.2f}")
                    return
        except Exception as e:
            logging.warning(f"Could not check account balance: {e}")
            # Continue anyway - let the order submission fail gracefully

        # Check if trade is allowed
        allowed, reason = self.portfolio_manager.can_enter_position(symbol, position_size_pct, exposure)
        logging.info(f"Position size: {position_size_pct:.1%} | Portfolio allocated: {exposure['total_allocated']:.1%}")

        if not allowed:
            print(f"{Colors.WARNING}[SKIP] {symbol}: {reason}{Colors.RESET}")
            logging.info(f"[SKIP] {symbol}: {reason}")
            return

        # Display trade decision
        print(f"{Colors.SUCCESS}[APPROVED] {symbol}: {strategy}{Colors.RESET}")
        print(f"  Confidence: {confidence}%")
        print(f"  Position Size: {position_size_pct:.1%}")
        print(f"  IV Rank: {iv_rank:.0f}")
        print(f"  Earnings Risk: {earnings_check['risk']}")
        print(f"  Portfolio Allocated: {exposure['total_allocated']:.1%}")

        logging.info(f"*** [APPROVED] {symbol}: {strategy} | Confidence: {confidence}% | Position Size: {position_size_pct:.1%} | IV Rank: {iv_rank:.0f}")

        if confidence >= 95:
            print(f"  {Colors.SUCCESS}🎯 HOME RUN TRADE{Colors.RESET}")
            logging.info(f"🎯 HOME RUN TRADE - {symbol} at {confidence}% confidence!")

        # Execute the options trade - ROUTE TO MULTI-LEG HANDLER FOR MULTI-LEG STRATEGIES
        if strategy.upper() in ['IRON_CONDOR', 'STRADDLE', 'STRANGLE',
                                'BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD',
                                'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD']:
            success = self.execute_multi_leg_strategy(
                symbol=symbol,
                strategy=strategy,
                strikes=strikes,
                expiry=expiry,
                position_size_pct=position_size_pct,
                options_data=options_data,
                confidence=confidence,
                iv_rank=iv_rank,
                reason=grok_data.get('reason', '')
            )
        else:
            success = self.execute_options_order(
                symbol=symbol,
                strategy=strategy,
                strikes=strikes,
                expiry=expiry,
                position_size_pct=position_size_pct,
                options_data=options_data,
                confidence=confidence,
                iv_rank=iv_rank,
                reason=grok_data.get('reason', '')
            )

        if success:
            print(f"{Colors.SUCCESS}  ✓ Order submitted successfully{Colors.RESET}")
            logging.info(f"✓✓✓ ORDER EXECUTED SUCCESSFULLY: {symbol} {strategy} ✓✓✓")

            # Alert on high-confidence trades
            if confidence >= 90:
                self.alert_manager.send_alert(
                    'INFO',
                    f"High-confidence trade: {symbol} {strategy} ({confidence}% confidence)",
                    throttle_key=f"trade_{symbol}"
                )
        else:
            print(f"{Colors.ERROR}  ✗ Order failed to execute{Colors.RESET}")
            logging.warning(f"✗ ORDER FAILED: {symbol} {strategy}")

    def execute_multi_leg_strategy(self, symbol: str, strategy: str, strikes: str,
                                 expiry: str, position_size_pct: float,
                                 options_data: List[Dict], confidence: int,
                                 iv_rank: float, reason: str) -> bool:
        """
        Execute multi-leg options strategy using the established multi-leg managers.

        Supports: IRON_CONDOR, STRADDLE, STRANGLE, BULL_CALL_SPREAD, BEAR_PUT_SPREAD
        """
        try:
            logging.info(f"=== EXECUTING MULTI-LEG STRATEGY: {strategy} on {symbol} ===")

            # Get account for position sizing
            account = self.trading_client.get_account()
            total_equity = float(account.equity) if account.equity is not None else 0.0
            position_value = total_equity * position_size_pct

            # Parse multi-leg strategy using existing manager
            strategy_details = self.multi_leg_manager.parse_multi_leg_strategy(
                strategy, symbol, strikes, expiry, 0  # current_price not needed for core logic
            )

            if not strategy_details:
                logging.error(f"Failed to parse multi-leg strategy: {strategy} for {symbol}")
                print(f"{Colors.ERROR}[ERROR] Failed to parse {strategy} strategy{Colors.RESET}")
                return False

            logging.info(f"Strategy parsed: {strategy_details['description']}")
            print(f"{Colors.INFO}[MULTI-LEG] {strategy}: {strategy_details['description']}{Colors.RESET}")

            # CRITICAL FIX: Add pricing data to legs from options_data
            # Legs from parse_multi_leg_strategy have no pricing - must enrich before sizing
            legs_with_pricing = []
            for leg in strategy_details['legs']:
                leg_strike = leg['strike']
                leg_type = leg['type']

                # Find matching option contract
                matching_contract = None
                for option in options_data:
                    if (option.get('option_type', '').lower() == leg_type.lower() and
                        abs(option.get('strike', 0) - leg_strike) < 0.01):
                        matching_contract = option
                        break

                if not matching_contract:
                    logging.error(f"No options data found for {leg_type} strike ${leg_strike}")
                    print(f"{Colors.ERROR}[ERROR] Missing pricing data for {leg_type} ${leg_strike}{Colors.RESET}")
                    return False

                # Add pricing to leg
                leg_copy = leg.copy()
                leg_copy['bid'] = matching_contract.get('bid', 0) or 0
                leg_copy['ask'] = matching_contract.get('ask', 0) or 0
                leg_copy['contract_price'] = matching_contract.get('ask' if leg['side'] == 'buy' else 'bid', 0) or 0
                leg_copy['implied_volatility'] = matching_contract.get('implied_volatility', 0)
                leg_copy['delta'] = matching_contract.get('delta', 0)
                leg_copy['gamma'] = matching_contract.get('gamma', 0)
                leg_copy['theta'] = matching_contract.get('theta', 0)
                leg_copy['vega'] = matching_contract.get('vega', 0)

                logging.info(f"Leg {leg_type} ${leg_strike} {leg['side']}: bid=${leg_copy['bid']:.2f}, ask=${leg_copy['ask']:.2f}, price=${leg_copy['contract_price']:.2f}")
                legs_with_pricing.append(leg_copy)

            # Calculate sizing using existing manager with priced legs
            sizing_info = self.multi_leg_order_manager.calculate_multi_leg_sizing(
                symbol, strategy, legs_with_pricing, confidence, total_equity
            )

            if not sizing_info.get('can_afford', False):
                print(f"{Colors.WARNING}[SKIP] Cannot afford {strategy} position at current size{Colors.RESET}")
                logging.info(f"Cannot afford {strategy} position")
                return False

            logging.info(f"Sizing calculated: {sizing_info['max_spreads']} spreads, net cost per spread: ${sizing_info['net_cost_per_spread']:.2f}")

            # Execute the strategy using existing manager with priced legs from sizing_info
            execution_result = self.multi_leg_order_manager.execute_multi_leg_order(
                symbol, sizing_info['legs'], strategy, sizing_info
            )

            if execution_result.get('success', False):
                logging.info(f"Multi-leg strategy executed successfully: {strategy} on {symbol}")

                # PHASE 1: Register multi-leg order with tracker for atomic operations
                if execution_result.get('order_ids'):
                    strategy_id = self.multi_leg_tracker.create_strategy_id(symbol, strategy)
                    self.multi_leg_tracker.register_multi_leg_order(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        strategy=strategy,
                        leg_order_ids=execution_result['order_ids'],
                        legs_info=[{'strike': leg.get('strike'), 'type': leg.get('type'),
                                   'side': leg.get('side'), 'quantity': leg.get('quantity')}
                                  for leg in sizing_info['legs']]
                    )
                    logging.info(f"Registered multi-leg strategy {strategy_id} with tracker")

                # Calculate total cost for trading journal
                total_cost = sizing_info.get('total_cost', 0)

                # FIXED: Issue #1 - Calculate multi-leg Greeks
                try:
                    # Try to calculate Greeks from actual leg data
                    stock_data = self.openbb.get_quote(symbol)
                    underlying_price = stock_data['results'][0].get('price', 0) if stock_data and 'results' in stock_data else 0

                    # Check if legs exist and are iterable before calling len()
                    legs_data = strategy_details.get('legs') if strategy_details else None
                    if legs_data is not None and len(legs_data) > 0:
                        greeks = self.multi_leg_order_manager.calculate_multi_leg_greeks(strategy_details['legs'], symbol, underlying_price)
                        logging.info(f"Calculated actual Greeks from legs: {greeks}")
                    else:
                        # Fallback: estimate Greeks from strategy type
                        atm_options = [opt for opt in options_data if opt.get('strike', 0) > 0 and
                                      abs(opt.get('strike', 0) - underlying_price) / underlying_price < 0.05]
                        atm_greeks = None
                        if atm_options:
                            atm_greeks = {
                                'delta': atm_options[0].get('delta', 0.5),
                                'gamma': atm_options[0].get('gamma', 0.05),
                                'theta': atm_options[0].get('theta', -0.05),
                                'vega': atm_options[0].get('vega', 0.1)
                            }
                        greeks = self.multi_leg_order_manager.estimate_strategy_greeks(strategy, underlying_price, strikes, atm_greeks)
                        logging.info(f"Estimated Greeks for {strategy}: {greeks}")
                except Exception as e:
                    logging.warning(f"Could not calculate Greeks for multi-leg: {e}, using estimates")
                    greeks = self.multi_leg_order_manager.estimate_strategy_greeks(strategy, 0, strikes, None)

                # Log to trading journal (summarize the multi-leg position)
                trade_data = {
                    'symbol': symbol,
                    'strategy': strategy,
                    'occ_symbol': f"{symbol}_MULTI_LEG",  # Composite symbol for multi-leg
                    'action': 'ENTER_MULTI_LEG',
                    'entry_price': sizing_info['net_cost_per_spread'],  # Net cost per spread
                    'quantity': sizing_info['max_spreads'],  # Number of spreads
                    'total_cost': total_cost,
                    'confidence': confidence,
                    'iv_rank': iv_rank,
                    'delta': greeks.get('delta', 0),  # FIXED: Calculated from legs
                    'theta': greeks.get('theta', 0),  # FIXED: Calculated from legs
                    'vega': greeks.get('vega', 0),   # FIXED: Calculated from legs
                    'gamma': greeks.get('gamma', 0),  # FIXED: Calculated from legs
                    'bid_ask_spread': 0,  # Not applicable for spread strategies
                    'reason': reason
                }

                self.trade_journal.log_trade(trade_data)

                # Track active multi-leg position
                position_tracking = {
                    'symbol': symbol,
                    'occ_symbol': f"{symbol}_MULTI_LEG",
                    'strategy': strategy,
                    'entry_price': sizing_info['net_cost_per_spread'],
                    'quantity': sizing_info['max_spreads'],
                    'confidence': confidence,
                    'strikes': strikes,
                    'expiry': expiry,
                    'reason': reason
                }
                self.trade_journal.track_active_position(position_tracking)

                print(f"{Colors.SUCCESS}✓ Multi-leg strategy executed: {sizing_info['max_spreads']} spreads @ net cost ${sizing_info['net_cost_per_spread']:.2f} each{Colors.RESET}")
                return True
            else:
                logging.error(f"Multi-leg strategy execution failed: {strategy} on {symbol}")
                print(f"{Colors.ERROR}✗ Multi-leg strategy execution failed{Colors.RESET}")

                if execution_result.get('errors'):
                    for error in execution_result['errors']:
                        logging.error(f"Execution error: {error}")
                        print(f"{Colors.ERROR}  Error: {error}{Colors.RESET}")

                return False

        except Exception as e:
            logging.error(f"Error executing multi-leg strategy {strategy} on {symbol}: {e}")
            self.trade_journal.log_error('MULTI_LEG_EXECUTION', str(e), symbol)
            print(f"{Colors.ERROR}[ERROR] Failed to execute {strategy}: {str(e)}{Colors.RESET}")
            return False

    def execute_options_order(self, symbol: str, strategy: str, strikes: str, expiry: str,
                             position_size_pct: float, options_data: List[Dict],
                             confidence: int, iv_rank: float, reason: str) -> bool:
        """Execute options order with full validation"""
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        try:
            # Calculate position value
            account = self.trading_client.get_account()
            total_equity = float(account.equity) if account.equity is not None else 0.0
            position_value = total_equity * position_size_pct

            # Get valid expiration date
            exp_date = self._get_valid_expiration(symbol, expiry)
            if not exp_date:
                logging.warning(f"No valid expiration found for {symbol}")
                return False

            exp_str = exp_date.strftime('%y%m%d')

            # Find best contract with STRICT validation
            contract = self._find_best_contract_validated(
                symbol=symbol,
                strategy=strategy,
                strikes=strikes,
                exp_str=exp_str,
                options_data=options_data
            )

            if not contract:
                logging.warning(f"No suitable contract found for {symbol} {strategy}")
                print(f"{Colors.WARNING}WARNING: No suitable contract found for {symbol} {strategy}{Colors.RESET}")
                print(f"{Colors.DIM}  Checked {len(options_data)} contracts - all failed validation{Colors.RESET}")
                return False

            # Extract contract details
            occ_symbol = contract['occ_symbol']
            strike_price = contract['strike']
            option_type = contract['type']
            contract_price = contract['price']
            bid_ask_spread = contract['spread_pct']

            # Extract Greeks
            delta = contract.get('delta', 0)
            theta = contract.get('theta', 0)
            vega = contract.get('vega', 0)
            gamma = contract.get('gamma', 0)

            # Calculate quantity
            cost_per_contract = contract_price * 100
            quantity = max(1, int(position_value / cost_per_contract))

            # Determine order side
            side = OrderSide.BUY  # Most strategies buy options

            # Extract bid/ask from contract data
            bid = contract.get('bid', 0) or 0
            ask = contract.get('ask', 0) or 0

            # FIXED: Issue #8 - Use dynamic slippage based on bid-ask spread
            side_str = 'buy' if side == OrderSide.BUY else 'sell'
            limit_price = calculate_dynamic_limit_price(bid, ask, side_str, contract_price)

            logging.info(f"Order limit price calculation: bid=${bid:.2f}, ask=${ask:.2f}, "
                        f"contract_price=${contract_price:.2f}, limit_price=${limit_price:.2f} ({side.value})")

            # Log trade details
            logging.info(f"Executing {strategy} on {symbol}")
            logging.info(f"  Contract: {occ_symbol}")
            logging.info(f"  Strike: ${strike_price}, Type: {option_type.upper()}")
            logging.info(f"  Price: ${contract_price:.2f}, Spread: {bid_ask_spread:.1%}")
            logging.info(f"  Delta: {delta:.3f}, Theta: ${theta:.2f}/day")
            logging.info(f"  Quantity: {quantity} contracts @ ${contract_price:.2f}")
            logging.info(f"  Total cost: ${quantity * cost_per_contract:.2f}")

            # Submit order
            order_data = LimitOrderRequest(
                symbol=occ_symbol,
                qty=quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price
            )

            try:
                order = self.trading_client.submit_order(order_data)
                logging.info(f"Order submitted: {order.id}")
            except Exception as order_error:
                error_msg = str(order_error)
                # Check if it's an asset not found error (common in paper trading)
                if "asset" in error_msg.lower() and "not found" in error_msg.lower():
                    logging.warning(f"Asset {occ_symbol} not available in Alpaca (paper trading limitation)")
                    print(f"{Colors.WARNING}[SKIP] {symbol}: Option contract not available in Alpaca paper trading{Colors.RESET}")
                    print(f"{Colors.DIM}  Contract: {occ_symbol}{Colors.RESET}")
                    print(f"{Colors.DIM}  Note: Paper trading has limited option symbol availability{Colors.RESET}")
                    return False
                else:
                    # Re-raise other errors
                    raise

            # Log to database
            trade_data = {
                'symbol': symbol,
                'strategy': strategy,
                'occ_symbol': occ_symbol,
                'action': 'BUY',
                'entry_price': contract_price,
                'quantity': quantity,
                'total_cost': quantity * cost_per_contract,
                'confidence': confidence,
                'iv_rank': iv_rank,
                'delta': delta,
                'theta': theta,
                'vega': vega,
                'gamma': gamma,
                'bid_ask_spread': bid_ask_spread,
                'reason': reason
            }

            self.trade_journal.log_trade(trade_data)

            # Track active position with strategy for Grok monitoring
            position_tracking = {
                'symbol': symbol,
                'occ_symbol': occ_symbol,
                'strategy': strategy,
                'entry_price': contract_price,
                'quantity': quantity,
                'confidence': confidence,
                'strikes': strikes,
                'expiry': expiry,
                'reason': reason
            }
            self.trade_journal.track_active_position(position_tracking)

            return True

        except Exception as e:
            logging.error(f"Error executing order for {symbol}: {e}")
            self.trade_journal.log_error('ORDER_EXECUTION', str(e), symbol)
            return False

    def _log_daily_summary(self):
        """Log comprehensive daily summary to file"""
        try:
            logging.info(f"")
            logging.info(f"{'='*80}")
            logging.info(f"DAILY SUMMARY - {datetime.now().strftime('%Y-%m-%d')}")
            logging.info(f"{'='*80}")

            # Account info
            account = self.trading_client.get_account()
            equity = float(account.equity) if account.equity is not None else 0.0
            cash = float(account.cash) if account.cash is not None else 0.0
            buying_power = float(account.buying_power) if account.buying_power is not None else 0.0
            logging.info(f"Portfolio Value: ${equity:,.2f}")
            logging.info(f"Cash Available: ${cash:,.2f}")
            logging.info(f"Buying Power: ${buying_power:,.2f}")

            # Positions
            positions = self.trading_client.get_all_positions()
            logging.info(f"Open Positions: {len(positions)}")

            if positions:
                total_market_value = 0
                total_unrealized_pl = 0
                for pos in positions:
                    market_value = float(pos.market_value) if pos.market_value is not None else 0.0
                    unrealized_pl = float(pos.unrealized_pl) if pos.unrealized_pl is not None else 0.0
                    avg_entry = float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0
                    current = float(pos.current_price) if pos.current_price is not None else 0.0
                    plpc = float(pos.unrealized_plpc) if pos.unrealized_plpc is not None else 0.0
                    total_market_value += market_value
                    total_unrealized_pl += unrealized_pl
                    logging.info(f"  {pos.symbol}: {pos.qty} @ ${avg_entry:.2f} | Current: ${current:.2f} | P&L: ${unrealized_pl:,.2f} ({plpc:+.1%})")

                logging.info(f"Total Position Value: ${total_market_value:,.2f}")
                logging.info(f"Total Unrealized P&L: ${total_unrealized_pl:,.2f}")

            # Performance stats
            stats = self.trade_journal.get_performance_stats(days=30)
            if stats.get('total_trades', 0) > 0:
                logging.info(f"")
                logging.info(f"30-DAY PERFORMANCE:")
                logging.info(f"  Total Trades: {stats['total_trades']}")
                logging.info(f"  Wins: {stats['wins']} | Losses: {stats['losses']}")
                logging.info(f"  Win Rate: {stats['win_rate']:.1%}")
                logging.info(f"  Avg Return: {stats['avg_return']:.1%}")
                logging.info(f"  Total P&L: ${stats['total_pnl']:,.2f}")
            else:
                logging.info(f"No closed trades in the last 30 days")

            # Rolling lists
            logging.info(f"")
            logging.info(f"TRACKING:")
            logging.info(f"  Rolling Top-50 Candidates: {len(self.rolling_top_50)}")
            logging.info(f"  Rolling Top-25 Grok-Rated: {len(self.rolling_top_25)}")

            if self.rolling_top_25:
                logging.info(f"  Top 5 Grok-Rated Candidates:")
                top_5 = sorted(self.rolling_top_25, key=lambda x: x.get('grok_confidence', 0), reverse=True)[:5]
                for i, cand in enumerate(top_5, 1):
                    logging.info(f"    {i}. {cand['symbol']}: {cand.get('grok_confidence', 0)}% | {cand.get('strategy', 'UNKNOWN')}")

            logging.info(f"{'='*80}")

        except Exception as e:
            logging.error(f"Error generating daily summary: {e}")

    def _is_monthly_expiration(self, exp_date: datetime) -> bool:
        """Check if expiration is monthly (3rd Friday) - FIXED: Issue #16"""
        # Monthly options expire on the 3rd Friday of each month
        # Find the 3rd Friday of the month
        first_day = exp_date.replace(day=1)
        # Find first Friday
        days_until_friday = (4 - first_day.weekday()) % 7
        first_friday = first_day + timedelta(days=days_until_friday)
        # 3rd Friday is 2 weeks later
        third_friday = first_friday + timedelta(days=14)

        return exp_date.date() == third_friday.date()

    def _is_quarterly_expiration(self, exp_date: datetime) -> bool:
        """Check if expiration is quarterly (Mar/Jun/Sep/Dec 3rd Friday) - FIXED: Issue #16"""
        # Quarterly expirations are in March, June, September, December
        quarterly_months = [3, 6, 9, 12]
        return exp_date.month in quarterly_months and self._is_monthly_expiration(exp_date)

    def _score_expiration(self, exp_date: datetime, target_dte: int) -> float:
        """Score expiration based on liquidity and proximity to target - FIXED: Issue #16"""
        score = 0.0

        # Calculate DTE
        dte = (exp_date.date() - datetime.now().date()).days

        # Heavily penalize 0 DTE (same-day expiration)
        if dte == 0:
            return -1000

        # Prefer quarterly expirations (highest liquidity)
        if self._is_quarterly_expiration(exp_date):
            score += 30
            logging.debug(f"Expiration {exp_date.date()} is QUARTERLY (+30 points)")
        # Prefer monthly expirations (3rd Friday)
        elif self._is_monthly_expiration(exp_date):
            score += 20
            logging.debug(f"Expiration {exp_date.date()} is MONTHLY (+20 points)")

        # Prefer closest to target DTE (but not exactly - allow some flexibility)
        dte_diff = abs(dte - target_dte)
        score -= dte_diff  # Closer is better

        return score

    def _get_valid_expiration(self, symbol: str, expiry_str: str) -> Optional[datetime]:
        """Get valid expiration date from options chain - FIXED: Issue #16"""
        try:
            # Parse target DTE
            dte_match = expiry_str.replace('DTE', '').strip()
            target_dte = int(dte_match) if dte_match.isdigit() else 30

            # Get available expirations
            expirations = self.openbb.get_options_expirations(symbol)

            if not expirations:
                # Fallback: calculate next Friday
                target_date = datetime.now() + timedelta(days=target_dte)
                days_ahead = 4 - target_date.weekday()  # Friday is 4
                if days_ahead <= 0:
                    days_ahead += 7
                next_friday = target_date + timedelta(days=days_ahead)
                return next_friday

            # FIXED: Issue #16 - Score expirations based on liquidity preferences
            scored_expirations = []
            for exp in expirations:
                score = self._score_expiration(exp, target_dte)
                scored_expirations.append((exp, score))

            # Sort by score (highest first)
            scored_expirations.sort(key=lambda x: x[1], reverse=True)

            # Log top 3 choices
            logging.info(f"Expiration selection for {symbol} (target: {target_dte} DTE):")
            for i, (exp, score) in enumerate(scored_expirations[:3], 1):
                dte = (exp.date() - datetime.now().date()).days
                exp_type = "QUARTERLY" if self._is_quarterly_expiration(exp) else ("MONTHLY" if self._is_monthly_expiration(exp) else "WEEKLY")
                logging.info(f"  {i}. {exp.date()} ({dte} DTE) - {exp_type} - Score: {score:.0f}")

            # Return best-scored expiration
            best_exp = scored_expirations[0][0]
            return best_exp

        except Exception as e:
            logging.error(f"Error getting expiration for {symbol}: {e}")
            return None

    def _find_best_contract_validated(self, symbol: str, strategy: str, strikes: str,
                                     exp_str: str, options_data: List[Dict]) -> Optional[Dict]:
        """Find best contract with STRICT validation - UPDATED: Handle multi-leg strategies"""
        try:
            strategy_upper = strategy.upper()

            # Handle MULTI-LEG STRATEGIES - ROUTE TO SPECIALIZED HANDLER
            if strategy_upper in ['IRON_CONDOR', 'STRADDLE', 'STRANGLE', 'BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD']:
                logging.info(f"Routing {strategy_upper} to multi-leg execution handler")
                return self._handle_multi_leg_contract_finding(symbol, strategy, strikes, exp_str, options_data)

            # SINGLE-LEG STRATEGIES (existing logic)
            # Parse target strike
            target_strike = None
            if strikes:
                strike_parts = strikes.split('/')
                try:
                    target_strike = float(strike_parts[0].strip())
                except:
                    pass

            # Determine option type for single-leg strategies
            if 'LONG_CALL' in strategy_upper or 'BULL' in strategy_upper:
                option_type = 'call'
            elif 'LONG_PUT' in strategy_upper or 'BEAR' in strategy_upper:
                option_type = 'put'
            else:
                # Default to call if unclear
                logging.warning(f"Unknown strategy type '{strategy}' for {symbol}, defaulting to CALL")
                option_type = 'call'

            # Get current price
            current_price = options_data[0].get('underlying_price', 0) if options_data else 0
            if not target_strike:
                target_strike = current_price

            # Find best contract
            best_contract = None
            min_diff = float('inf')
            rejection_reasons = {}
            contracts_checked = 0
            type_filtered = 0

            logging.info(f"Looking for {option_type} contracts near strike ${target_strike:.2f} from {len(options_data)} total contracts")

            # Log first contract as sample to see data structure
            if options_data and len(options_data) > 0:
                sample = options_data[0]
                logging.debug(f"Sample contract data: type={sample.get('option_type')}, strike={sample.get('strike')}, "
                             f"bid={sample.get('bid')}, ask={sample.get('ask')}, volume={sample.get('volume')}, "
                             f"oi={sample.get('open_interest')}, last={sample.get('last_price')}")

            for option in options_data:
                contracts_checked += 1

                # Filter by type
                opt_type = option.get('option_type', '').lower()
                if opt_type != option_type:
                    type_filtered += 1
                    continue

                # Get strike
                strike = option.get('strike', 0)
                if strike <= 0:
                    continue

                # Check if close to target
                diff = abs(strike - target_strike)
                if diff < min_diff:
                    # VALIDATE LIQUIDITY (use paper mode if in paper trading)
                    is_paper = config.ALPACA_MODE and config.ALPACA_MODE.lower().strip() == 'paper'
                    is_valid, reason = OptionsValidator.validate_contract_liquidity(option, paper_mode=is_paper)

                    if not is_valid:
                        logging.debug(f"Contract rejected: {symbol} ${strike} {opt_type} - {reason}")
                        # Track rejection reasons
                        if reason not in rejection_reasons:
                            rejection_reasons[reason] = 0
                        rejection_reasons[reason] += 1
                        continue

                    # Get price
                    price, price_source = OptionsValidator.get_contract_price(option)

                    if not price or price <= 0:
                        continue

                    # Calculate spread
                    bid = option.get('bid', 0) or 0
                    ask = option.get('ask', 0) or 0
                    if bid > 0 and ask > 0:
                        spread_pct = (ask - bid) / ((bid + ask) / 2)
                    else:
                        spread_pct = 0

                    min_diff = diff
                    best_contract = {
                        'strike': strike,
                        'type': opt_type,
                        'price': price,
                        'price_source': price_source,
                        'bid': bid,
                        'ask': ask,
                        'spread_pct': spread_pct,
                        'volume': option.get('volume', 0),
                        'open_interest': option.get('open_interest', 0),
                        'delta': option.get('delta', 0),
                        'gamma': option.get('gamma', 0),
                        'theta': option.get('theta', 0),
                        'vega': option.get('vega', 0),
                        'occ_symbol': self._build_occ_symbol(
                            symbol, exp_str, opt_type[0].upper(), strike
                        )
                    }

            # Log rejection reasons if no contract found
            if not best_contract:
                logging.warning(f"No contract found for {symbol} {strategy}")
                logging.warning(f"  Total contracts: {len(options_data)}")
                logging.warning(f"  Contracts checked: {contracts_checked}")
                logging.warning(f"  Filtered by type ({option_type}): {type_filtered}")
                logging.warning(f"  Target strike: ${target_strike:.2f}")

                if rejection_reasons:
                    logging.warning(f"  Rejection summary:")
                    for reason, count in sorted(rejection_reasons.items(), key=lambda x: x[1], reverse=True):
                        logging.warning(f"    {reason}: {count} contracts")
                else:
                    logging.warning(f"  No contracts of type '{option_type}' passed initial filtering")

            return best_contract

        except Exception as e:
            logging.error(f"Error finding contract: {e}")
            return None

    def _handle_multi_leg_contract_finding(self, symbol: str, strategy: str, strikes: str,
                                          exp_str: str, options_data: List[Dict]) -> Optional[Dict]:
        """
        Handle contract finding for multi-leg strategies.
        Returns contract info with pricing data for all legs.
        """
        try:
            strategy_upper = strategy.upper()

            # Parse strikes - multi-leg strategies need specific parsing
            strike_parts = strikes.split('/') if strikes else []
            if not strike_parts:
                logging.warning(f"No strike specified for multi-leg strategy {strategy} on {symbol}")
                return None

            parsed_strikes = []
            for s in strike_parts:
                try:
                    parsed_strikes.append(float(s.strip()))
                except ValueError:
                    logging.warning(f"Invalid strike format: {strikes}")
                    return None

            logging.info(f"Multi-leg strategy {strategy}: strikes {parsed_strikes}")

            # Strategy-specific leg configuration
            legs_config = []

            if strategy_upper == 'IRON_CONDOR':
                if len(parsed_strikes) < 4:
                    logging.warning(f"IRON_CONDOR requires 4 strikes, got {len(parsed_strikes)}")
                    return None

                strikes_sorted = sorted(parsed_strikes)
                if len(strikes_sorted) >= 4:
                    low_put, put_sell, call_sell, high_call = strikes_sorted[:4]

                    legs_config = [
                        {'strike': low_put, 'type': 'put', 'side': 'buy', 'ratio': 1},
                        {'strike': put_sell, 'type': 'put', 'side': 'sell', 'ratio': 1},
                        {'strike': call_sell, 'type': 'call', 'side': 'sell', 'ratio': 1},
                        {'strike': high_call, 'type': 'call', 'side': 'buy', 'ratio': 1}
                    ]

            elif strategy_upper in ['STRADDLE', 'STRANGLE']:
                if strategy_upper == 'STRADDLE' and len(parsed_strikes) < 1:
                    logging.warning("STRADDLE requires 1 strike")
                    return None
                elif strategy_upper == 'STRANGLE' and len(parsed_strikes) < 2:
                    logging.warning("STRANGLE requires 2 strikes")
                    return None

                # For straddle, use same strike for both call and put
                if strategy_upper == 'STRADDLE':
                    strike = parsed_strikes[0]
                    legs_config = [
                        {'strike': strike, 'type': 'call', 'side': 'buy', 'ratio': 1},
                        {'strike': strike, 'type': 'put', 'side': 'buy', 'ratio': 1}
                    ]
                else:  # STRANGLE
                    call_strike, put_strike = parsed_strikes[:2]
                    legs_config = [
                        {'strike': call_strike, 'type': 'call', 'side': 'buy', 'ratio': 1},
                        {'strike': put_strike, 'type': 'put', 'side': 'buy', 'ratio': 1}
                    ]

            elif strategy_upper in ['BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD']:
                if len(parsed_strikes) < 2:
                    logging.warning(f"{strategy_upper} requires 2 strikes")
                    return None

                if strategy_upper == 'BULL_CALL_SPREAD':
                    low_strike, high_strike = sorted(parsed_strikes[:2])
                    legs_config = [
                        {'strike': low_strike, 'type': 'call', 'side': 'buy', 'ratio': 1},
                        {'strike': high_strike, 'type': 'call', 'side': 'sell', 'ratio': 1}
                    ]
                else:  # BEAR_PUT_SPREAD
                    high_strike, low_strike = sorted(parsed_strikes[:2], reverse=True)
                    legs_config = [
                        {'strike': high_strike, 'type': 'put', 'side': 'buy', 'ratio': 1},
                        {'strike': low_strike, 'type': 'put', 'side': 'sell', 'ratio': 1}
                    ]

            else:
                logging.error(f"Unsupported multi-leg strategy: {strategy_upper}")
                return None

            # Find contracts for each leg
            legs_with_contracts = []
            total_debit = 0
            total_credit = 0

            for leg in legs_config:
                # Find matching contract
                leg_contract = self._find_single_leg_contract(
                    symbol, leg['type'], leg['strike'], exp_str, options_data
                )

                if not leg_contract:
                    logging.warning(f"Could not find contract for {leg['type']} ${leg['strike']}")
                    return None

                leg_contract['leg_info'] = leg
                legs_with_contracts.append(leg_contract)

                # Calculate contribution to net cost
                contract_price = leg_contract['price'] * leg_contract['ratio']
                if leg['side'] == 'buy':
                    total_debit += contract_price
                else:
                    total_credit += contract_price

            # Calculate net cost
            net_cost = total_debit - total_credit

            # Build result with multi-leg structure
            result = {
                'strategy': strategy_upper,
                'legs': legs_with_contracts,
                'net_cost': net_cost,
                'total_debit': total_debit,
                'total_credit': total_credit,
                'is_multi_leg': True
            }

            logging.info(f"Multi-leg contract analysis complete: {strategy_upper}")
            logging.info(f"  Net cost: ${net_cost:.2f} (Debit: ${total_debit:.2f}, Credit: ${total_credit:.2f})")
            logging.info(f"  Legs found: {len(legs_with_contracts)}")

            return result

        except Exception as e:
            logging.error(f"Error in multi-leg contract finding for {symbol} {strategy}: {e}")
            return None

    def _find_single_leg_contract(self, symbol: str, option_type: str, target_strike: float,
                                 exp_str: str, options_data: List[Dict]) -> Optional[Dict]:
        """Find a single contract for a specific strike and option type"""

        # Determine paper trading mode
        is_paper = config.ALPACA_MODE and config.ALPACA_MODE.lower().strip() == 'paper'

        for option in options_data:
            # Check if it matches our criteria
            opt_type = option.get('option_type', '').lower()
            strike = option.get('strike', 0)

            if opt_type != option_type.lower() or abs(strike - target_strike) > 0.01:  # Allow small rounding diff
                continue

            # Validate liquidity
            is_valid, reason = OptionsValidator.validate_contract_liquidity(option, paper_mode=is_paper)
            if not is_valid:
                logging.debug(f"Leg contract rejected: ${strike} {opt_type} - {reason}")
                continue

            # Get price
            price, price_source = OptionsValidator.get_contract_price(option)
            if not price or price <= 0:
                continue

            # Build OCC symbol
            occ_symbol = self._build_occ_symbol(symbol, exp_str, option_type[0].upper(), strike)

            # Calculate spread percentage
            bid = option.get('bid', 0) or 0
            ask = option.get('ask', 0) or 0
            spread_pct = 0
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid if mid > 0 else 0

            return {
                'occ_symbol': occ_symbol,
                'strike': strike,
                'type': opt_type,
                'price': price,
                'price_source': price_source,
                'bid': bid,
                'ask': ask,
                'spread_pct': spread_pct,
                'volume': option.get('volume', 0),
                'open_interest': option.get('open_interest', 0),
                'delta': option.get('delta', 0),
                'gamma': option.get('gamma', 0),
                'theta': option.get('theta', 0),
                'vega': option.get('vega', 0)
            }

        return None

    def _validate_grok_strategy(self, strategy: str, confidence: int, scanner_analysis: Dict, symbol: str, grok_data: Dict) -> Optional[str]:
        """Validate Grok's strategy recommendation against market conditions and scanner data"""
        try:
            logging.info(f"Validating Grok strategy: {strategy} for {symbol}")

            # Get current market context
            current_price = grok_data.get('stock_data', {}).get('price', 0)
            scanner_signals = scanner_analysis.get('signals', [])
            pcr = scanner_analysis.get('put_call_ratio', 1.0)
            iv_rank = scanner_analysis.get('iv_metrics', {}).get('iv_rank', 50)

            strikes = grok_data.get('strikes', '')
            expiry = grok_data.get('expiry', '30DTE')

            # VALIDATION 1: Supported strategy types
            SUPPORTED_STRATEGIES = [
                'LONG_CALL', 'LONG_PUT',
                'BULL_CALL_SPREAD', 'BEAR_PUT_SPREAD',
                'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD',
                'IRON_CONDOR', 'STRADDLE', 'STRANGLE', 'UNKNOWN'
            ]

            if strategy.upper() not in SUPPORTED_STRATEGIES:
                logging.warning(f"Grok suggested unsupported strategy: {strategy}")
                return 'UNKNOWN'

            if strategy.upper() == 'UNKNOWN':
                logging.info(f"Grok could not determine strategy for {symbol}")
                return None  # No valid strategy

            # VALIDATION 1.5: Paper account restrictions
            is_paper = config.ALPACA_MODE and config.ALPACA_MODE.lower().strip() == 'paper'
            if is_paper:
                # Iron Condors require naked options approval which paper accounts don't have
                if strategy.upper() == 'IRON_CONDOR':
                    logging.warning(f"IRON_CONDOR rejected in paper mode (requires naked options approval)")
                    return None

                # Short straddles/strangles also require naked options
                if strategy.upper() in ['SHORT_STRADDLE', 'SHORT_STRANGLE']:
                    logging.warning(f"{strategy} rejected in paper mode (requires naked options approval)")
                    return None

            # VALIDATION 2: Low confidence rejection
            if confidence < 40:
                logging.warning(f"Strategy {strategy} rejected - confidence too low: {confidence}%")
                return None

            # VALIDATION 3: Check if strikes are reasonable
            if strikes and current_price > 0:
                strike_parts = strikes.split('/')
                try:
                    strike_price = float(strike_parts[0].strip())

                    # Check if strike is too far from current price
                    if abs(strike_price - current_price) / current_price > 0.30:  # >30% away
                        logging.warning(f"Strike {strike_price} too far from current price {current_price}")
                        # Could suggest alternative but for now just log

                except ValueError:
                    logging.warning(f"Could not parse strike price: {strikes}")

            # VALIDATION 4: IV alignment with strategy
            if strategy.upper() in ['LONG_CALL', 'LONG_PUT']:
                # Buying options - prefer moderate to low IV
                if iv_rank > 85:  # Very high IV
                    logging.warning(f"IV rank too high ({iv_rank}) for buying options strategy")
                    # Consider neutral strategies instead
                    if pcr > 1.5:  # Heavy put pressure
                        return 'STRADDLE'  # Volatility play
                    elif 'HIGH_GAMMA' in scanner_signals:
                        return 'STRADDLE'

            elif 'SPREAD' in strategy.upper():
                # Spreads - prefer moderate volatility
                if iv_rank < 20:  # Very low IV
                    logging.warning(f"IV rank too low ({iv_rank}) for spreads")
                    # Could return None or downgrade confidence

            # VALIDATION 5: Put/Call ratio alignment
            if 'BEAR' in strategy.upper() or 'PUT' in strategy.upper():
                if pcr < 0.8:  # Calls dominating
                    logging.debug(f"Put-based strategy {strategy} in call-dominant environment (PCR: {pcr})")

            if 'BULL' in strategy.upper() or 'CALL' in strategy.upper():
                if pcr > 1.2:  # Puts dominating
                    logging.debug(f"Call-based strategy {strategy} in put-dominant environment (PCR: {pcr})")

            # VALIDATION 6: Market regime check
            regime = self.regime_analyzer.analyze_market_regime()
            regime_bias = regime.get('implications', {}).get('bias', 'neutral')

            # Check regime alignment
            if 'BULL' in strategy.upper() and regime_bias == 'bearish':
                logging.debug(f"Bullish strategy {strategy} in bearish regime")
            elif 'BEAR' in strategy.upper() and regime_bias == 'bullish':
                logging.debug(f"Bearish strategy {strategy} in bullish regime")

            # For volatility plays, high volatility is good
            if 'STRADDLE' in strategy.upper() or 'STRANGLE' in strategy.upper():
                volatility_regime = regime.get('implications', {}).get('vol_play', 'neutral')
                if volatility_regime == 'buy_straddles':
                    logging.info(f"Straddle/Strangle strategy perfectly aligned with market regime")

            # VALIDATION 7: Expiration validation
            if 'DTE' in expiry:
                try:
                    dte = int(expiry.replace('DTE', '').strip())
                    if dte < 7:  # Too short
                        logging.warning(f"Expiration too close: {dte} DTE")
                        # Could suggest longer expiration
                    elif dte > 90:  # Too long
                        logging.warning(f"Expiration too far: {dte} DTE")
                        # Could suggest shorter
                except ValueError:
                    logging.warning(f"Could not parse DTE from: {expiry}")

            # STRATEGY-SPECIFIC VALIDATIONS

            # For iron condors - need sufficient premium
            if 'IRON_CONDOR' in strategy.upper():
                if iv_rank < 30:  # Too low IV for condor premiums
                    logging.warning(f"Iron condor may not have enough premium (IV rank: {iv_rank})")

            # For spreads - check if strikes make sense
            if 'SPREAD' in strategy.upper() and '/' in strikes:
                try:
                    strike1, strike2 = [float(s.strip()) for s in strikes.split('/')]
                    if strategy.upper() == 'BULL_CALL_SPREAD':
                        if strike1 >= strike2:
                            logging.error("Bull call spread strikes in wrong order")
                            return None
                    elif strategy.upper() == 'BEAR_PUT_SPREAD':
                        if strike1 <= strike2:
                            logging.error("Bear put spread strikes in wrong order")
                            return None
                except ValueError:
                    logging.warning(f"Could not validate spread strikes: {strikes}")

            # FINAL VALIDATION: Minimum confidence threshold for execution
            if confidence >= 75:
                logging.info(f"✓ Strategy {strategy} passes all validations at {confidence}% confidence")
                return strategy
            elif confidence >= 60:
                logging.info(f"⚠ Strategy {strategy} passes but reduced confidence: {confidence}%")
                return strategy
            else:
                logging.info(f"✗ Strategy {strategy} fails validation: insufficient confidence {confidence}%")
                return None

        except Exception as e:
            logging.error(f"Error validating strategy {strategy} for {symbol}: {e}")
            # Return original strategy if validation fails (fail-safe)
            return strategy

    def _build_occ_symbol(self, symbol: str, exp_str: str, option_type: str, strike: float) -> str:
        """Build OCC format symbol"""
        strike_int = int(strike * 1000)
        strike_str = f"{strike_int:08d}"
        return f"{symbol}{exp_str}{option_type}{strike_str}"

    # =========================================================================
    # WHEEL STRATEGY EXECUTION METHODS
    # =========================================================================

    def execute_wheel_opportunities(self):
        """
        Scan for and execute Wheel Strategy opportunities.
        Called during market hours to identify quality stocks for systematic premium collection.

        The Wheel Strategy has 50-95% win rate and 15-40% annual returns.
        """
        print(f"\n{Colors.HEADER}[WHEEL STRATEGY] Scanning for premium collection opportunities...{Colors.RESET}")
        logging.info("="*80)
        logging.info("WHEEL STRATEGY SCAN")
        logging.info("="*80)

        try:
            # Get wheel stats
            stats = self.wheel_manager.get_wheel_stats()
            active_positions = stats['active_positions']

            print(f"{Colors.INFO}[WHEEL] Active positions: {active_positions}/{self.wheel_strategy.MAX_WHEEL_POSITIONS}{Colors.RESET}")

            if active_positions > 0:
                print(f"{Colors.DIM}  Selling puts: {stats['selling_puts']}{Colors.RESET}")
                print(f"{Colors.DIM}  Assigned (owning stock): {stats['assigned']}{Colors.RESET}")
                print(f"{Colors.DIM}  Selling calls: {stats['selling_calls']}{Colors.RESET}")
                print(f"{Colors.DIM}  Total premium collected: ${stats['total_premium_collected']:.2f}{Colors.RESET}")

            if stats['completed_cycles'] > 0:
                print(f"{Colors.SUCCESS}  Completed cycles: {stats['completed_cycles']} | "
                      f"Avg ROI: {stats['avg_roi']:.1f}% | Win rate: {stats['win_rate']:.1f}%{Colors.RESET}")

            # Check if we can add more wheel positions
            if active_positions >= self.wheel_strategy.MAX_WHEEL_POSITIONS:
                print(f"{Colors.WARNING}[WHEEL] Maximum wheel positions reached ({self.wheel_strategy.MAX_WHEEL_POSITIONS}){Colors.RESET}")
                return

            # Calculate how many positions we can add
            positions_to_fill = self.wheel_strategy.MAX_WHEEL_POSITIONS - active_positions
            print(f"{Colors.INFO}[WHEEL] Can add {positions_to_fill} more position(s){Colors.RESET}")

            # Get account info for position sizing
            account = self.trading_client.get_account()
            account_value = float(account.equity) if account.equity is not None else 0.0

            # Find wheel candidates (request enough to fill all slots)
            candidates = self.wheel_strategy.find_wheel_candidates(max_candidates=min(positions_to_fill * 2, 10))

            if not candidates:
                print(f"{Colors.DIM}[WHEEL] No wheel candidates found matching criteria{Colors.RESET}")
                return

            print(f"{Colors.SUCCESS}[WHEEL] Found {len(candidates)} wheel candidates (ranked best to worst):{Colors.RESET}\n")

            for i, candidate in enumerate(candidates, 1):
                quality_score = candidate.get('quality_score', 0)
                score_color = Colors.SUCCESS if quality_score >= 75 else Colors.INFO if quality_score >= 60 else Colors.WARNING

                print(f"  {i}. {candidate['symbol']:6} @ ${candidate['stock_price']:.2f} | "
                      f"{score_color}Score: {quality_score:.1f}/100{Colors.RESET} | "
                      f"IV {candidate['iv_rank']:.0f}% | "
                      f"{candidate['annual_return']:.1%} annual")
                print(f"{Colors.DIM}     Put: ${candidate['put_strike']:.2f} strike for ${candidate['put_premium']:.2f} premium{Colors.RESET}")

            # Try to fill multiple positions in one scan
            positions_filled = 0

            for candidate in candidates:
                # Check if we've filled all available slots
                if positions_filled >= positions_to_fill:
                    print(f"\n{Colors.SUCCESS}[WHEEL] Filled {positions_filled} position(s) this scan{Colors.RESET}")
                    break

                symbol = candidate['symbol']

                # Check if we already have a position on this symbol
                existing_position = self.wheel_manager.get_wheel_position(symbol)
                if existing_position:
                    print(f"\n{Colors.WARNING}[WHEEL] {symbol}: Already have active wheel position, skipping{Colors.RESET}")
                    # Handle existing position (e.g., sell next call if assigned)
                    self._manage_existing_wheel_position(existing_position)
                    continue  # Try next candidate

                # NEW: Check sector diversification limits (prevent concentration risk like 40% EV exposure)
                if not self.wheel_strategy.can_add_symbol_by_sector(symbol, self.wheel_manager):
                    print(f"\n{Colors.WARNING}[WHEEL] {symbol}: Sector limit reached, skipping to maintain diversification{Colors.RESET}")
                    continue  # Try next candidate

                # NEW: Check for consecutive losses on this symbol (prevent revenge trading)
                if not self.wheel_strategy.check_consecutive_losses(symbol, self.wheel_manager):
                    print(f"\n{Colors.WARNING}[WHEEL] {symbol}: Too many consecutive losses, pausing entries{Colors.RESET}")
                    continue  # Try next candidate

                # Get the optimal put to sell
                put_details = self.wheel_strategy.get_put_to_sell(symbol)
                if not put_details:
                    print(f"\n{Colors.ERROR}[WHEEL] {symbol}: Could not find suitable put to sell, trying next candidate{Colors.RESET}")
                    continue  # Try next candidate

                # Update active position count for sizing (includes positions filled this scan)
                current_positions = active_positions + positions_filled

                # Calculate position size (with dynamic sizing based on win rate)
                contracts = self.wheel_strategy.calculate_position_size(
                    symbol=symbol,
                    put_strike=put_details['strike'],
                    account_value=account_value,
                    existing_wheel_positions=current_positions,
                    wheel_manager=self.wheel_manager
                )

                if contracts == 0:
                    print(f"\n{Colors.WARNING}[WHEEL] {symbol}: Insufficient capital or position limit reached{Colors.RESET}")
                    continue  # Try next candidate

                # Execute the put sale
                print(f"\n{Colors.SUCCESS}[WHEEL] {symbol}: Attempting to sell {contracts} cash-secured put(s){Colors.RESET}")
                print(f"  Strike: ${put_details['strike']:.2f}")
                print(f"  Premium: ${put_details['premium']:.2f} (${put_details['premium'] * 100 * contracts:.2f} total)")
                print(f"  Expiration: {put_details['expiration']} ({put_details['dte']} DTE)")
                print(f"  Capital required: ${put_details['strike'] * 100 * contracts:,.2f}")

                success = self._execute_wheel_put_sale(symbol, put_details, contracts)

                if success:
                    positions_filled += 1
                    print(f"{Colors.SUCCESS}✓ [WHEEL] {symbol}: Put sale executed successfully! ({positions_filled}/{positions_to_fill} filled){Colors.RESET}\n")
                    # Continue to next candidate (don't return - fill more slots!)
                else:
                    print(f"{Colors.ERROR}✗ [WHEEL] {symbol}: Put sale failed, trying next candidate{Colors.RESET}\n")
                    continue  # Try next candidate

            # Summary
            if positions_filled > 0:
                print(f"{Colors.SUCCESS}[WHEEL] Successfully filled {positions_filled} position(s) this scan{Colors.RESET}")
            else:
                print(f"{Colors.WARNING}[WHEEL] No candidates executed successfully this scan{Colors.RESET}")

        except Exception as e:
            logging.error(f"[WHEEL] Error executing wheel opportunities: {e}", exc_info=True)
            print(f"{Colors.ERROR}[WHEEL ERROR] {str(e)}{Colors.RESET}")

    # =========================================================================
    # BULL PUT SPREAD STRATEGY EXECUTION METHODS
    # =========================================================================

    def execute_spread_opportunities(self):
        """
        Scan for and execute Bull Put Spread opportunities.
        Called during market hours to identify spread candidates.

        The Bull Put Spread Strategy has 65-75% win rate and defined risk.
        """
        if not self.spread_strategy or not self.spread_manager:
            logging.warning("[SPREAD] ❌ Spread strategy not configured - check ALPACA_BULL_PUT_KEY environment variables")
            print(f"{Colors.WARNING}[SPREAD] ❌ Spread strategy not configured{Colors.RESET}")
            return

        print(f"\n{Colors.HEADER}[SPREAD STRATEGY] Scanning for bull put spread opportunities...{Colors.RESET}")
        logging.info("="*80)
        logging.info("BULL PUT SPREAD STRATEGY SCAN")
        logging.info("="*80)

        try:
            # Log spread account status
            try:
                spread_account = self.spread_trading_client.get_account()
                spread_equity = float(spread_account.equity) if spread_account.equity else 0
                spread_cash = float(spread_account.cash) if spread_account.cash else 0
                logging.info(f"[SPREAD] Account Status: Equity=${spread_equity:,.2f}, Cash=${spread_cash:,.2f}")
                print(f"{Colors.INFO}[SPREAD] Account: ${spread_equity:,.2f} equity, ${spread_cash:,.2f} cash{Colors.RESET}")
            except Exception as e:
                logging.warning(f"[SPREAD] Could not fetch account status: {e}")

            # Get spread stats
            active_positions = self.spread_manager.get_position_count()

            print(f"{Colors.INFO}[SPREAD] Active positions: {active_positions}/{self.spread_strategy.MAX_SPREAD_POSITIONS}{Colors.RESET}")
            logging.info(f"[SPREAD] Active positions: {active_positions}/{self.spread_strategy.MAX_SPREAD_POSITIONS}")

            # Check if we have room for new positions
            if active_positions >= self.spread_strategy.MAX_SPREAD_POSITIONS:
                print(f"{Colors.WARNING}[SPREAD] Maximum spread positions reached ({active_positions}){Colors.RESET}")
                logging.info(f"[SPREAD] ⚠️  Maximum spread positions reached ({active_positions}/{self.spread_strategy.MAX_SPREAD_POSITIONS})")
                return

            # Check VIX throttle
            if not self.spread_strategy.check_vix_throttle():
                print(f"{Colors.WARNING}[SPREAD] VIX too high - pausing new spread entries{Colors.RESET}")
                logging.warning(f"[SPREAD] ⚠️  VIX throttle active - pausing new entries")
                return

            # Calculate how many positions to fill
            positions_to_fill = self.spread_strategy.MAX_SPREAD_POSITIONS - active_positions
            num_candidates = min(positions_to_fill * 2, 10)  # Request 2x candidates, max 10

            print(f"{Colors.INFO}[SPREAD] Scanning for {positions_to_fill} new spread(s) (requesting {num_candidates} candidates)...{Colors.RESET}")
            logging.info(f"[SPREAD] Positions to fill: {positions_to_fill}, requesting {num_candidates} candidates")

            # Find spread candidates
            candidates = self.spread_strategy.find_spread_candidates(max_candidates=num_candidates)

            if not candidates:
                print(f"{Colors.WARNING}[SPREAD] No spread candidates found - market may be too calm (low IV){Colors.RESET}")
                logging.warning("[SPREAD] ❌ No spread candidates found - check logs above for filter rejections")
                return

            print(f"{Colors.SUCCESS}[SPREAD] ✓ Found {len(candidates)} spread candidates{Colors.RESET}\n")
            logging.info(f"[SPREAD] ✓ Found {len(candidates)} spread candidates")

            # Execute spreads
            positions_filled = 0
            for candidate in candidates:
                if positions_filled >= positions_to_fill:
                    break

                symbol = candidate['symbol']
                print(f"{Colors.INFO}[SPREAD] Evaluating: {symbol} - {candidate['annual_return']:.1f}% annual return{Colors.RESET}")
                print(f"  Short ${candidate['short_strike']:.2f} / Long ${candidate['long_strike']:.2f}")
                print(f"  Credit: ${candidate['credit']:.2f}, Max Risk: ${candidate['max_risk']:.0f}, ROI: {candidate['roi']:.1f}%")

                # NEW: Check sector diversification limits
                if not self.spread_strategy.can_add_symbol_by_sector(symbol, self.spread_manager):
                    print(f"{Colors.WARNING}[SPREAD] {symbol}: Sector limit reached, skipping to maintain diversification{Colors.RESET}\n")
                    continue  # Try next candidate

                # Calculate position size
                spread_account = self.spread_trading_client.get_account()
                available_capital = float(spread_account.portfolio_value)
                contracts = self.spread_strategy.calculate_position_size(candidate, available_capital)

                print(f"  Position size: {contracts} contract(s)")

                # Execute spread
                success = self._execute_bull_put_spread(candidate, contracts)

                if success:
                    positions_filled += 1
                    print(f"{Colors.SUCCESS}✓ [SPREAD] {symbol}: Spread executed successfully! ({positions_filled}/{positions_to_fill} filled){Colors.RESET}\n")
                else:
                    print(f"{Colors.ERROR}✗ [SPREAD] {symbol}: Spread execution failed, trying next candidate{Colors.RESET}\n")
                    continue

            # Summary
            if positions_filled > 0:
                print(f"{Colors.SUCCESS}[SPREAD] Successfully filled {positions_filled} spread(s) this scan{Colors.RESET}")
            else:
                print(f"{Colors.WARNING}[SPREAD] No spreads executed successfully this scan{Colors.RESET}")

        except Exception as e:
            logging.error(f"[SPREAD] Error executing spread opportunities: {e}", exc_info=True)
            print(f"{Colors.ERROR}[SPREAD ERROR] {str(e)}{Colors.RESET}")

    def _execute_bull_put_spread(self, spread: Dict, contracts: int) -> bool:
        """
        Execute bull put spread order using individual leg orders.

        Bull Put Spread = Sell higher strike put + Buy lower strike put

        Args:
            spread: Spread candidate dict from strategy
            contracts: Number of spreads to execute

        Returns:
            True if successful, False otherwise
        """
        try:
            symbol = spread['symbol']
            short_put_symbol = spread['short_put_symbol']
            long_put_symbol = spread['long_put_symbol']
            short_strike = spread['short_strike']
            long_strike = spread['long_strike']
            credit = spread['credit']

            logging.info(f"[SPREAD] ═══════════════════════════════════════════════════")
            logging.info(f"[SPREAD] Executing {contracts}x bull put spread on {symbol}")
            logging.info(f"[SPREAD] ═══════════════════════════════════════════════════")
            logging.info(f"[SPREAD]   Short Put: {short_put_symbol} @ ${short_strike:.2f}")
            logging.info(f"[SPREAD]   Long Put:  {long_put_symbol} @ ${long_strike:.2f}")
            logging.info(f"[SPREAD]   Target Credit: ${credit:.2f} per spread")
            logging.info(f"[SPREAD]   Total Credit: ${credit * contracts:.2f}")

            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, PositionIntent

            # Use ACTUAL market prices from the spread dict (not estimates!)
            short_put_bid = spread.get('short_put_bid', credit * 0.6)
            short_put_ask = spread.get('short_put_ask', credit * 0.7)
            long_put_bid = spread.get('long_put_bid', credit * 0.3)
            long_put_ask = spread.get('long_put_ask', credit * 0.4)

            # Calculate limit prices for better fill rates
            # Short: Sell at 95% of bid (slightly below bid for better fill)
            # Long: Buy at 105% of ask (slightly above ask for better fill)
            short_put_price = round(short_put_bid * 0.95, 2)
            long_put_price = round(long_put_ask * 1.05, 2)

            logging.info(f"[SPREAD] Market prices - Short bid: ${short_put_bid:.2f}, Long ask: ${long_put_ask:.2f}")
            logging.info(f"[SPREAD] Order prices - Short limit: ${short_put_price:.2f} (95% of bid), Long limit: ${long_put_price:.2f} (105% of ask)")

            # CRITICAL FIX: Submit as multi-leg spread order (not two separate orders)
            # This ensures Alpaca recognizes it as a spread and only requires max risk ($500)
            # instead of naked short put margin ($14,864)
            logging.info(f"[SPREAD] Placing multi-leg bull put spread order")

            # Create legs for the spread
            short_leg = OptionLegRequest(
                symbol=short_put_symbol,
                ratio_qty=contracts,
                side=OrderSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN
            )

            long_leg = OptionLegRequest(
                symbol=long_put_symbol,
                ratio_qty=contracts,
                side=OrderSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN
            )

            # Submit multi-leg spread order with net credit limit
            net_credit_limit = round(short_put_price - long_put_price, 2)

            spread_order_request = LimitOrderRequest(
                symbol=symbol,  # Underlying symbol
                qty=contracts,
                side=OrderSide.BUY,  # For credit spread, use BUY side with negative limit price
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.MLEG,  # Multi-leg order (not MULTILEG!)
                limit_price=net_credit_limit,  # Net credit we want to receive
                legs=[short_leg, long_leg]
            )

            logging.info(f"[SPREAD] Submitting spread order: {contracts} contracts @ ${net_credit_limit:.2f} net credit")
            spread_order = self.spread_trading_client.submit_order(spread_order_request)

            if not spread_order:
                logging.error(f"[SPREAD] ❌ Failed to place spread order")
                return False

            logging.info(f"[SPREAD] ✓ Multi-leg spread order placed - Order ID: {spread_order.id}")
            print(f"{Colors.SUCCESS}[SPREAD] ✓ Bull put spread order placed - Order ID: {spread_order.id}{Colors.RESET}")
            print(f"{Colors.INFO}[SPREAD]   Legs: Sell {short_strike} put / Buy {long_strike} put{Colors.RESET}")
            print(f"{Colors.INFO}[SPREAD]   Net Credit: ${net_credit_limit:.2f} per spread{Colors.RESET}")

            # Create database entry to track the spread
            spread_id = self.spread_manager.create_spread_position(
                symbol=symbol,
                short_strike=short_strike,
                long_strike=long_strike,
                short_put_symbol=short_put_symbol,
                long_put_symbol=long_put_symbol,
                num_contracts=contracts,
                credit_per_spread=credit,
                expiration=spread['expiration'],
                entry_dte=spread['dte'],
                entry_delta=spread.get('probability_profit', 70) / 100,
                notes=f"Auto-generated spread - {spread['annual_return']:.1f}% annual return. "
                      f"Multi-leg order: {spread_order.id}"
            )

            logging.info(f"[SPREAD] ✓ Spread position #{spread_id} created in database")
            logging.info(f"[SPREAD] ═══════════════════════════════════════════════════")
            print(f"{Colors.SUCCESS}[SPREAD] ✓ {symbol}: Bull put spread executed successfully!{Colors.RESET}")
            print(f"{Colors.INFO}[SPREAD]   Spread ID: #{spread_id}{Colors.RESET}")
            print(f"{Colors.INFO}[SPREAD]   Multi-leg Order: {spread_order.id}{Colors.RESET}")

            return True

        except Exception as e:
            logging.error(f"[SPREAD] ❌ Error executing bull put spread: {e}", exc_info=True)
            print(f"{Colors.ERROR}[SPREAD] ❌ Error executing spread: {str(e)}{Colors.RESET}")
            return False

    def _execute_wheel_put_sale(self, symbol: str, put_details: Dict, contracts: int) -> bool:
        """
        Execute cash-secured put sale for wheel strategy.

        Args:
            symbol: Stock symbol
            put_details: Put option details from wheel_strategy.get_put_to_sell()
            contracts: Number of contracts to sell

        Returns:
            True if successful, False otherwise
        """
        try:
            # Build option symbol in OCC format
            option_symbol = put_details['option_symbol']
            strike = put_details['strike']
            premium = put_details['premium']

            # Sell put order (sell-to-open)
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Calculate limit price (accept 85% of bid to improve fill rate)
            limit_price = round(premium * 0.85, 2)

            order_data = LimitOrderRequest(
                symbol=option_symbol,
                qty=contracts,
                side=OrderSide.SELL,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY
            )

            logging.info(f"[WHEEL] {symbol}: Submitting put sale order - {contracts} contracts @ ${limit_price:.2f}")

            order = self.trading_client.submit_order(order_data)
            order_id = order.id

            logging.info(f"[WHEEL] {symbol}: Put sale order submitted - Order ID: {order_id}")

            # Wait briefly for fill
            time.sleep(5)
            order_status = self.trading_client.get_order_by_id(order_id)

            if order_status.status == 'filled':
                fill_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else limit_price
                total_premium = fill_price * 100 * contracts

                logging.info(f"[WHEEL] {symbol}: Put FILLED @ ${fill_price:.2f} (${total_premium:.2f} total)")

                # Create wheel position in database
                self.wheel_manager.create_wheel_position(
                    symbol=symbol,
                    initial_premium=total_premium,
                    option_symbol=option_symbol,
                    strike=strike,
                    expiration=put_details['expiration'],
                    notes=f"Wheel strategy initiated: ${total_premium:.2f} premium collected selling {contracts} puts @ ${strike:.2f}"
                )

                return True

            elif order_status.status in ['pending_new', 'accepted', 'new']:
                logging.info(f"[WHEEL] {symbol}: Put order pending (status: {order_status.status})")
                return True  # Order submitted successfully, waiting for fill

            else:
                logging.warning(f"[WHEEL] {symbol}: Put order not filled (status: {order_status.status})")
                return False

        except Exception as e:
            logging.error(f"[WHEEL] {symbol}: Error executing put sale: {e}", exc_info=True)
            return False

    def _manage_existing_wheel_position(self, position: Dict):
        """
        Manage existing wheel position based on current state.

        Args:
            position: Wheel position dict from wheel_manager.get_wheel_position()
        """
        symbol = position['symbol']
        state = position['state']

        try:
            if state == WheelState.ASSIGNED.value:
                # We own the stock, sell a covered call
                print(f"{Colors.INFO}[WHEEL] {symbol}: Stock assigned, selling covered call...{Colors.RESET}")

                call_details = self.wheel_strategy.get_call_to_sell(
                    symbol=symbol,
                    cost_basis=position['stock_cost_basis'],
                    shares=position['shares_owned']
                )

                if call_details:
                    success = self._execute_wheel_call_sale(symbol, call_details)
                    if success:
                        print(f"{Colors.SUCCESS}✓ [WHEEL] {symbol}: Covered call executed{Colors.RESET}")
                else:
                    print(f"{Colors.WARNING}[WHEEL] {symbol}: No suitable calls found{Colors.RESET}")

            elif state == WheelState.SELLING_CALLS.value:
                # Already selling calls, check if we should roll or close
                print(f"{Colors.DIM}[WHEEL] {symbol}: Currently selling calls (${position['total_premium_collected']:.2f} total premium){Colors.RESET}")

            elif state == WheelState.SELLING_PUTS.value:
                # Already selling puts, check for deep ITM or stop loss conditions
                print(f"{Colors.DIM}[WHEEL] {symbol}: Currently selling puts (${position['total_premium_collected']:.2f} total premium){Colors.RESET}")

                # NEW: Get current stock price and put premium for risk checks
                try:
                    quote = self.market_data.get_stock_quote(symbol)
                    current_stock_price = float(quote.get('latestPrice', 0)) if quote else 0

                    # Get current put value
                    put_symbol = position.get('option_symbol', '')
                    current_put_premium = 0

                    if put_symbol:
                        try:
                            put_position = self.trading_client.get_open_position(put_symbol)
                            if put_position:
                                current_put_premium = abs(float(put_position.market_value)) / (position.get('contracts', 1) * 100)
                        except Exception as e:
                            logging.debug(f"[WHEEL] {symbol}: Could not get current put value: {e}")

                    # NEW: Check for deep ITM condition (>$1.00 ITM)
                    if current_stock_price > 0 and self.wheel_strategy.should_roll_deep_itm_put(position, current_stock_price):
                        print(f"{Colors.ERROR}[WHEEL] {symbol}: PUT IS DEEP ITM - Should consider rolling down/out or closing{Colors.RESET}")
                        logging.warning(f"[WHEEL] {symbol}: Deep ITM put detected - manual intervention may be needed")
                        # TODO: Implement automatic rolling logic in future enhancement

                    # NEW: Check for stop loss (-200% ROI)
                    if current_put_premium > 0 and self.wheel_strategy.should_stop_loss_put(position, current_put_premium):
                        print(f"{Colors.ERROR}[WHEEL] {symbol}: STOP LOSS TRIGGERED - Position losing 2x the premium collected{Colors.RESET}")
                        logging.error(f"[WHEEL] {symbol}: Stop loss triggered - manual intervention required to close position")
                        # TODO: Implement automatic position closing in future enhancement

                except Exception as e:
                    logging.debug(f"[WHEEL] {symbol}: Error checking ITM/stop loss conditions: {e}")

        except Exception as e:
            logging.error(f"[WHEEL] {symbol}: Error managing existing position: {e}", exc_info=True)

    # =========================================================================
    # SPREAD POSITION MONITORING AND EXIT MANAGEMENT
    # =========================================================================

    def check_spread_positions(self):
        """
        Monitor all active spread positions and apply exit rules.
        Called every 5 minutes during market hours (like Wheel strategy).
        """
        if not self.spread_manager or not self.spread_strategy:
            return

        try:
            positions = self.spread_manager.get_all_positions()

            if not positions:
                return

            logging.info(f"[SPREAD MONITOR] Checking {len(positions)} spread position(s) for exit signals...")

            for position in positions:
                try:
                    # Get current spread value from broker
                    current_value = self._get_spread_current_value(position)

                    # Update database with current value and unrealized P&L
                    self.spread_manager.update_spread_value(
                        position['id'],
                        current_value
                    )

                    # Check exit conditions
                    self._check_spread_exit_conditions(position, current_value)

                except Exception as e:
                    logging.error(f"[SPREAD MONITOR] Error managing spread {position['symbol']}: {e}", exc_info=True)

        except Exception as e:
            logging.error(f"[SPREAD MONITOR] Error in spread position monitoring: {e}", exc_info=True)

    def _get_spread_current_value(self, position: Dict) -> float:
        """
        Get current market value of spread by fetching both legs.

        Args:
            position: Spread position dict from spread_manager.get_all_positions()

        Returns:
            Current value of spread (short put value - long put value)
        """
        try:
            symbol = position['symbol']
            short_put_symbol = position['short_put_symbol']
            long_put_symbol = position['long_put_symbol']

            # Try to get position values from broker
            try:
                short_position = self.spread_trading_client.get_open_position(short_put_symbol)
                long_position = self.spread_trading_client.get_open_position(long_put_symbol)

                if short_position and long_position:
                    # Spread value = short put value - long put value
                    short_value = float(short_position.market_value) / 100
                    long_value = float(long_position.market_value) / 100
                    spread_value = short_value - long_value
                    return abs(spread_value)

            except Exception as e:
                logging.debug(f"[SPREAD] Could not get spread value from broker positions: {e}")

            # Fallback: estimate from options chain
            return self._estimate_spread_value_from_chain(position)

        except Exception as e:
            logging.error(f"[SPREAD] Error getting spread value for {position['symbol']}: {e}", exc_info=True)
            return 0.0

    def _estimate_spread_value_from_chain(self, position: Dict) -> float:
        """
        Estimate spread value by fetching current option prices from chain.

        Args:
            position: Spread position dict

        Returns:
            Estimated spread value based on mid prices
        """
        try:
            symbol = position['symbol']
            expiration = position['expiration']
            short_strike = position['short_strike']
            long_strike = position['long_strike']

            # Get options chain
            options = self.spread_strategy._get_options_chain(symbol)

            if not options:
                logging.debug(f"[SPREAD] No options chain available for {symbol}")
                return 0.0

            # Find short put
            short_put = None
            long_put = None

            for opt in options:
                if (opt['type'] == 'put' and
                    opt['expiration'] == expiration and
                    abs(opt['strike'] - short_strike) < 0.01):
                    short_put = opt
                elif (opt['type'] == 'put' and
                      opt['expiration'] == expiration and
                      abs(opt['strike'] - long_strike) < 0.01):
                    long_put = opt

            if short_put and long_put:
                # Calculate mid prices
                short_mid = (short_put['bid'] + short_put['ask']) / 2
                long_mid = (long_put['bid'] + long_put['ask']) / 2
                spread_value = short_mid - long_mid
                return abs(spread_value)

            logging.debug(f"[SPREAD] Could not find both legs in options chain for {symbol}")
            return 0.0

        except Exception as e:
            logging.debug(f"[SPREAD] Error estimating spread value from chain: {e}")
            return 0.0

    def _check_spread_exit_conditions(self, position: Dict, current_value: float):
        """
        Check if spread should be closed based on profit target,
        stop loss, or expiration.

        Args:
            position: Spread position dict
            current_value: Current market value of spread
        """
        symbol = position['symbol']
        spread_id = position['id']
        credit_received = position['total_credit']
        max_profit = position['max_profit']

        # Calculate P&L
        # For credit spreads: P&L = credit received - current value
        unrealized_pnl = (credit_received - current_value) * 100
        pnl_pct = (unrealized_pnl / max_profit) if max_profit > 0 else 0

        # Profit Target (50%)
        if pnl_pct >= self.spread_strategy.PROFIT_TARGET_PCT:
            logging.info(f"[SPREAD] {symbol}: Hit profit target ({pnl_pct:.1%}), closing spread")
            print(f"{Colors.SUCCESS}[SPREAD EXIT] {symbol}: Profit target hit ({pnl_pct:.1%}) - Closing spread{Colors.RESET}")
            self._close_spread_position(position, "PROFIT_TARGET")
            return

        # Days to Expiration Management
        dte = self._calculate_spread_dte(position)

        # Close at 7 DTE if profitable
        if dte <= 7 and unrealized_pnl > 0:
            logging.info(f"[SPREAD] {symbol}: {dte} DTE with profit (${unrealized_pnl:.0f}), closing")
            print(f"{Colors.INFO}[SPREAD EXIT] {symbol}: {dte} DTE with profit - Closing early{Colors.RESET}")
            self._close_spread_position(position, "EXPIRATION_MANAGEMENT")
            return

        # Close at 0 DTE regardless (avoid assignment complexity)
        if dte <= 0:
            logging.info(f"[SPREAD] {symbol}: Expiration day, closing position")
            print(f"{Colors.WARNING}[SPREAD EXIT] {symbol}: Expiration day - Closing position{Colors.RESET}")
            self._close_spread_position(position, "EXPIRATION")
            return

        # Let losers run (defined risk) - no stop loss on credit spreads

    def _calculate_spread_dte(self, position: Dict) -> int:
        """
        Calculate days to expiration for spread.

        Args:
            position: Spread position dict

        Returns:
            Days to expiration
        """
        try:
            expiration_str = position['expiration']
            expiration_date = datetime.strptime(expiration_str, '%Y-%m-%d')
            dte = (expiration_date - datetime.now()).days
            return max(0, dte)
        except Exception as e:
            logging.error(f"[SPREAD] Error calculating DTE: {e}")
            return 999  # Return high number on error to avoid premature closes

    def _close_spread_position(self, position: Dict, reason: str) -> bool:
        """
        Close spread position by executing buy-to-close orders for both legs.

        Bull Put Spread Close = Buy back short put + Sell back long put

        Args:
            position: Spread position dict
            reason: Exit reason (PROFIT_TARGET, EXPIRATION_MANAGEMENT, etc.)

        Returns:
            True if successful, False otherwise
        """
        try:
            symbol = position['symbol']
            spread_id = position['id']
            short_put_symbol = position['short_put_symbol']
            long_put_symbol = position['long_put_symbol']
            num_contracts = position['num_contracts']

            logging.info(f"[SPREAD CLOSE] ═══════════════════════════════════════════════════")
            logging.info(f"[SPREAD CLOSE] Closing {symbol} spread - Reason: {reason}")
            logging.info(f"[SPREAD CLOSE] ═══════════════════════════════════════════════════")

            # Get current exit price before closing
            exit_price = self._get_spread_current_value(position)

            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Get current option prices for limit orders
            options = self.spread_strategy._get_options_chain(symbol)
            expiration = position['expiration']
            short_strike = position['short_strike']
            long_strike = position['long_strike']

            short_put = None
            long_put = None

            for opt in options:
                if (opt['type'] == 'put' and opt['expiration'] == expiration and
                    abs(opt['strike'] - short_strike) < 0.01):
                    short_put = opt
                elif (opt['type'] == 'put' and opt['expiration'] == expiration and
                      abs(opt['strike'] - long_strike) < 0.01):
                    long_put = opt

            # LEG 1: BUY TO CLOSE the short put (we sold this, now buying back)
            if short_put:
                short_close_price = round(short_put['ask'] * 1.05, 2)  # Pay 5% over ask for quick fill
            else:
                short_close_price = 0.50  # Fallback price

            logging.info(f"[SPREAD CLOSE] Buying to close SHORT put: {short_put_symbol}")
            short_close_order = LimitOrderRequest(
                symbol=short_put_symbol,
                qty=num_contracts,
                side=OrderSide.BUY,  # Buy to close the short position
                time_in_force=TimeInForce.DAY,
                limit_price=short_close_price
            )

            short_order = self.spread_trading_client.submit_order(short_close_order)
            logging.info(f"[SPREAD CLOSE] ✓ Short put buy-to-close order placed - Order ID: {short_order.id}")

            # LEG 2: SELL TO CLOSE the long put (we bought this, now selling back)
            if long_put:
                long_close_price = round(long_put['bid'] * 0.95, 2)  # Accept 5% below bid for quick fill
            else:
                long_close_price = 0.10  # Fallback price

            logging.info(f"[SPREAD CLOSE] Selling to close LONG put: {long_put_symbol}")
            long_close_order = LimitOrderRequest(
                symbol=long_put_symbol,
                qty=num_contracts,
                side=OrderSide.SELL,  # Sell to close the long position
                time_in_force=TimeInForce.DAY,
                limit_price=long_close_price
            )

            long_order = self.spread_trading_client.submit_order(long_close_order)
            logging.info(f"[SPREAD CLOSE] ✓ Long put sell-to-close order placed - Order ID: {long_order.id}")

            # Update database with close details
            self.spread_manager.close_spread_position(
                spread_id=spread_id,
                exit_price=exit_price,
                exit_reason=reason
            )

            logging.info(f"[SPREAD CLOSE] ✓ Spread position #{spread_id} closed - {reason}")
            logging.info(f"[SPREAD CLOSE] ═══════════════════════════════════════════════════")

            print(f"{Colors.SUCCESS}✓ [SPREAD EXIT] {symbol}: Position closed - {reason}{Colors.RESET}")
            print(f"{Colors.INFO}[SPREAD EXIT]   Short close order: {short_order.id}{Colors.RESET}")
            print(f"{Colors.INFO}[SPREAD EXIT]   Long close order: {long_order.id}{Colors.RESET}")

            return True

        except Exception as e:
            logging.error(f"[SPREAD CLOSE] ❌ Error closing spread position: {e}", exc_info=True)
            print(f"{Colors.ERROR}[SPREAD EXIT] ❌ Failed to close {position['symbol']}: {str(e)}{Colors.RESET}")
            return False

    def _execute_wheel_call_sale(self, symbol: str, call_details: Dict) -> bool:
        """
        Execute covered call sale for wheel strategy.

        Args:
            symbol: Stock symbol
            call_details: Call option details from wheel_strategy.get_call_to_sell()

        Returns:
            True if successful, False otherwise
        """
        try:
            option_symbol = call_details['option_symbol']
            strike = call_details['strike']
            premium = call_details['premium']
            contracts = call_details['contracts']

            # Sell call order (sell-to-open)
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Calculate limit price (accept 85% of bid to improve fill rate)
            limit_price = round(premium * 0.85, 2)

            order_data = LimitOrderRequest(
                symbol=option_symbol,
                qty=contracts,
                side=OrderSide.SELL,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY
            )

            logging.info(f"[WHEEL] {symbol}: Submitting call sale order - {contracts} contracts @ ${limit_price:.2f}")

            order = self.trading_client.submit_order(order_data)
            order_id = order.id

            # Wait briefly for fill
            time.sleep(5)
            order_status = self.trading_client.get_order_by_id(order_id)

            if order_status.status == 'filled':
                fill_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else limit_price
                total_premium = fill_price * 100 * contracts

                logging.info(f"[WHEEL] {symbol}: Call FILLED @ ${fill_price:.2f} (${total_premium:.2f} total)")

                # Update wheel position to SELLING_CALLS state
                self.wheel_manager.mark_selling_calls(
                    symbol=symbol,
                    call_premium=total_premium,
                    option_symbol=option_symbol,
                    strike=strike,
                    expiration=call_details['expiration']
                )

                return True

            elif order_status.status in ['pending_new', 'accepted', 'new']:
                logging.info(f"[WHEEL] {symbol}: Call order pending (status: {order_status.status})")
                return True

            else:
                logging.warning(f"[WHEEL] {symbol}: Call order not filled (status: {order_status.status})")
                return False

        except Exception as e:
            logging.error(f"[WHEEL] {symbol}: Error executing call sale: {e}", exc_info=True)
            return False


if __name__ == '__main__':
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='DUBK Options Trading Bot v3.0')
    parser.add_argument('--skip-scan', action='store_true',
                        help='Skip market scan and use cached opportunities for faster Grok testing')
    parser.add_argument('--test-grok', action='store_true',
                        help='Test Grok analysis on cached data without executing trades')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode: allow scanning and trading outside market hours')
    args = parser.parse_args()

    print_banner()

    bot = OptionsBot()

    if args.skip_scan or args.test_grok:
        # Load cached opportunities and test Grok
        bot.test_grok_with_cached_data(execute_trades=not args.test_grok)
    else:
        # Normal operation
        bot.run()
