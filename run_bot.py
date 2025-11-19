#!/usr/bin/env python3
"""
OpenBB Options Trading Bot v4.0 - FULLY MODULAR ARCHITECTURE
===============================================================

Production-ready autonomous options trading bot with complete modular architecture:
- All classes extracted into focused modules
- Composition over inheritance
- No dependencies on legacy monolithic file
- Clean separation of concerns

Entry point for the modular options trading bot.
"""

import sys
import os
from pathlib import Path

# Add the project root to the path to ensure imports work correctly
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import logging
from datetime import datetime
from dotenv import load_dotenv
from colorama import Fore, Style, init

# Initialize colorama for Windows
init(autoreset=True)

# Load environment variables
load_dotenv()

# =============================================================================
# MODULAR IMPORTS - All Components Extracted
# =============================================================================

# Configuration
from config import config

# Import the core bot class (extracted from legacy file)
from src.bot_core import OptionsBot as CoreOptionsBot


class ModularOptionsBot:
    """
    Fully Modular Options Trading Bot

    This class COMPOSES functionality from modular components.
    No inheritance from legacy code - all dependencies are injected.

    Architecture:
    - All analyzer classes are in src/analyzers/
    - All strategy classes are in src/strategies/
    - All risk management in src/risk/
    - All scanners in src/scanners/
    - All order management in src/order_management/
    - All connectors in src/connectors/
    - Core trading logic in src/bot_core.py

    This enables:
    - Easy testing of individual components
    - Clear dependency injection
    - No circular dependencies
    - Simple extension and customization
    """

    def __init__(self):
        """Initialize with fully modular architecture using composition"""
        # Store configuration
        self.config = config
        # Initialize the core bot (which now uses all modular components)
        self.core_bot = CoreOptionsBot()
        print(f"{Fore.GREEN}[+] DUBK Options Bot v4.0 initialized successfully!{Style.RESET_ALL}")

    def run(self):
        """Run the trading bot using the core bot's run method"""
        return self.core_bot.run()

    def test_grok_with_cached_data(self, execute_trades=False):
        """Test Grok analysis with cached data"""
        return self.core_bot.test_grok_with_cached_data(execute_trades=execute_trades)

    # Expose core bot's methods for testing and debugging
    def get_account_info(self):
        """Get current account information"""
        return {
            'balance': self.core_bot.balance,
            'buying_power': self.core_bot.buying_power,
            'positions': len(self.core_bot.position_manager.active_positions),
        }

    def get_active_positions(self):
        """Get all active positions"""
        return self.core_bot.position_manager.active_positions

    def get_portfolio_greeks(self):
        """Get current portfolio Greeks"""
        return self.core_bot.portfolio_manager.get_portfolio_greeks()


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def print_modular_banner():
    """Display information about the fully modular architecture"""
    # Pre-calculate padding to account for ANSI color codes
    banner = f"""{Fore.CYAN}{Style.BRIGHT}
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║   ██████╗ ██╗   ██╗██████╗ ██╗  ██╗    ██████╗ ██████╗ ████████╗██╗ ██████╗  ║
║   ██╔══██╗██║   ██║██╔══██╗██║ ██╔╝   ██╔═══██╗██╔══██╗╚══██╔══╝██║██╔═══██╗ ║
║   ██║  ██║██║   ██║██████╔╝█████╔╝    ██║   ██║██████╔╝   ██║   ██║██║   ██║ ║
║   ██║  ██║██║   ██║██╔══██╗██╔═██╗    ██║   ██║██╔═══╝    ██║   ██║██║   ██║ ║
║   ██████╔╝╚██████╔╝██████╔╝██║  ██╗   ╚██████╔╝██║        ██║   ██║╚██████╔╝ ║
║   ╚═════╝  ╚═════╝ ╚═════╝ ╚═╝  ╚═╝    ╚═════╝ ╚═╝        ╚═╝   ╚═╝ ╚═════╝  ║
║                                                                               ║
║                   {Fore.YELLOW}🎯 AUTONOMOUS TRADING BOT v4.0 🎯{Fore.CYAN}                              ║
║                                                                               ║
║   {Fore.GREEN}Strategy 1:{Fore.WHITE} Wheel Strategy         {Fore.CYAN}│  {Fore.GREEN}Strategy 2:{Fore.WHITE} Bull Put Spreads       {Fore.CYAN}║
║   {Fore.WHITE}├─ Win Rate: 50-95%             {Fore.CYAN}│  {Fore.WHITE}├─ Win Rate: 65-75%           {Fore.CYAN}║
║   {Fore.WHITE}├─ Annual Return: 15-40%        {Fore.CYAN}│  {Fore.WHITE}├─ Annual Return: 15-30%      {Fore.CYAN}║
║   {Fore.WHITE}└─ Capital: $96K (Main)         {Fore.CYAN}│  {Fore.WHITE}└─ Capital: $10K (Spread)     {Fore.CYAN}║
║                                                                               ║
║   {Fore.MAGENTA}🛡️  RISK MANAGEMENT FEATURES{Fore.CYAN}                                                    ║
║   {Fore.WHITE}✓ Deep ITM Detection & Stop Loss (-200% ROI)                          {Fore.CYAN}║
║   {Fore.WHITE}✓ Sector Diversification (Max 2 per sector)                          {Fore.CYAN}║
║   {Fore.WHITE}✓ Dynamic Position Sizing (Win Rate Adaptive)                        {Fore.CYAN}║
║   {Fore.WHITE}✓ Consecutive Loss Protection                                        {Fore.CYAN}║
║   {Fore.WHITE}✓ Real-time Position Monitoring (Every 5 min)                        {Fore.CYAN}║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
{Style.RESET_ALL}"""
    print(banner)


def main():
    """Main entry point for the fully modular options trading bot"""
    # Display modular architecture info
    print_modular_banner()

    # Parse command-line arguments
    import argparse
    parser = argparse.ArgumentParser(description='DUBK Options Bot v4.0')

    parser.add_argument('--test-modules', action='store_true',
                        help='Test modular components without running trading bot')
    parser.add_argument('--validate-config', action='store_true',
                        help='Validate configuration and exit')
    parser.add_argument('--skip-scan', action='store_true',
                        help='Skip market scan and use cached opportunities')
    parser.add_argument('--test-grok', action='store_true',
                        help='Test Grok analysis on cached data')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode (ignore market hours)')

    args = parser.parse_args()

    # Validate configuration if requested
    if args.validate_config:
        print(f"{Fore.BLUE}[*] Validating configuration...{Style.RESET_ALL}")
        issues = config.validate_config()
        if issues:
            print(f"{Fore.RED}[-] Configuration issues found:{Style.RESET_ALL}")
            for issue in issues:
                print(f"  * {issue}")
            return 1
        else:
            print(f"{Fore.GREEN}[+] Configuration is valid{Style.RESET_ALL}")
        return 0

    # Test modules if requested
    if args.test_modules:
        print(f"{Fore.BLUE}[*] Testing modular components...{Style.RESET_ALL}")
        import subprocess
        result = subprocess.run([sys.executable, 'src/test/test_runner.py'])
        if result.returncode == 0:
            print(f"{Fore.GREEN}[+] All modular tests passed{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}[-] Some tests failed{Style.RESET_ALL}")
        return result.returncode

    # Test configuration validation
    issues = config.validate_config()
    if issues:
        print(f"{Fore.RED}[!] Configuration validation issues (non-critical):{Style.RESET_ALL}")
        for issue in issues:
            print(f"  {Fore.YELLOW}* {issue}{Style.RESET_ALL}")

    # Initialize and run modular bot
    try:
        print(f"{Fore.BLUE}[*] Starting DUBK Options Trading Bot v4.0...{Style.RESET_ALL}")

        # Create the fully modular bot (using composition, not inheritance)
        bot = ModularOptionsBot()

        # Handle different execution modes
        if args.skip_scan or args.test_grok:
            # Test with Grok functionality
            bot.test_grok_with_cached_data(execute_trades=not args.test_grok)
        else:
            # Normal operation
            bot.run()

        return 0

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] Bot stopped by user{Style.RESET_ALL}")
        logging.info("Modular bot stopped by user")
        return 0
    except Exception as e:
        print(f"\n{Fore.RED}[!] Critical error: {e}{Style.RESET_ALL}")
        logging.error(f"Critical error in modular bot: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
