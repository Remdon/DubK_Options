"""
Interactive UI for Manual Bot Control

Provides non-blocking user interface for:
- Manual scan initiation
- Manual portfolio evaluation
- Status monitoring

Runs in separate thread to avoid disrupting automated operations.
"""

import threading
import select
import sys
import logging
from datetime import datetime
from colorama import Fore, Style


class InteractiveUI:
    """Non-blocking interactive UI for manual bot control"""

    def __init__(self, bot):
        """
        Initialize interactive UI.

        Args:
            bot: Reference to main bot instance
        """
        self.bot = bot
        self.running = False
        self.ui_thread = None
        self.manual_scan_requested = False
        self.manual_portfolio_requested = False

    def start(self):
        """Start the interactive UI in a separate thread"""
        self.running = True
        self.ui_thread = threading.Thread(target=self._ui_loop, daemon=True)
        self.ui_thread.start()
        logging.info("[UI] Interactive UI started - Press ENTER for menu")

    def stop(self):
        """Stop the interactive UI"""
        self.running = False
        if self.ui_thread:
            self.ui_thread.join(timeout=1.0)

    def check_manual_requests(self):
        """
        Check if user requested manual actions (called by main loop).

        Returns:
            tuple: (scan_requested, portfolio_requested)
        """
        scan = self.manual_scan_requested
        portfolio = self.manual_portfolio_requested

        # Reset flags after checking
        self.manual_scan_requested = False
        self.manual_portfolio_requested = False

        return scan, portfolio

    def _ui_loop(self):
        """Main UI loop (runs in separate thread)"""
        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}  INTERACTIVE MODE ENABLED{Style.RESET_ALL}")
        print(f"{Fore.GREEN}  Commands: [ENTER] = Menu  |  's' = Scan  |  'p' = Portfolio  |  'q' = Quit{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        while self.running:
            try:
                # Non-blocking input check (only works on Unix, Windows needs different approach)
                if sys.platform == 'win32':
                    # Windows: Use msvcrt
                    import msvcrt
                    if msvcrt.kbhit():
                        key = msvcrt.getch().decode('utf-8').lower()
                        self._handle_command(key)
                else:
                    # Unix: Use select
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        key = sys.stdin.read(1).lower()
                        self._handle_command(key)

                # Small sleep to prevent CPU spinning
                threading.Event().wait(0.1)

            except Exception as e:
                logging.debug(f"[UI] Input check error: {e}")
                threading.Event().wait(1.0)

    def _handle_command(self, key):
        """Handle user command"""
        if key == '\n' or key == '\r' or key == '':
            # Enter pressed - show menu
            self._show_menu()

        elif key == 's':
            # Manual scan requested
            print(f"\n{Fore.YELLOW}[MANUAL] Wheel scan requested - will execute on next cycle{Style.RESET_ALL}")
            self.manual_scan_requested = True
            logging.info("[UI] Manual Wheel scan requested by user")

        elif key == 'p':
            # Manual portfolio evaluation requested
            print(f"\n{Fore.YELLOW}[MANUAL] Portfolio evaluation requested - will execute on next cycle{Style.RESET_ALL}")
            self.manual_portfolio_requested = True
            logging.info("[UI] Manual portfolio evaluation requested by user")

        elif key == 'q':
            # Quit (graceful shutdown)
            print(f"\n{Fore.RED}[SHUTDOWN] Graceful shutdown requested...{Style.RESET_ALL}")
            self.running = False
            self.bot.shutdown_requested = True

        elif key == 'h' or key == '?':
            # Help
            self._show_help()

    def _show_menu(self):
        """Display interactive menu"""
        print(f"\n{Fore.CYAN}╔{'═'*78}╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║{' '*30}INTERACTIVE MENU{' '*32}║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╠{'═'*78}╣{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║{Style.RESET_ALL}  {Fore.GREEN}s{Style.RESET_ALL} - Trigger Wheel Scan (searches for new opportunities){' '*23}║")
        print(f"{Fore.CYAN}║{Style.RESET_ALL}  {Fore.GREEN}p{Style.RESET_ALL} - Evaluate Portfolio (check P&L, positions, exits){' '*22}║")
        print(f"{Fore.CYAN}║{Style.RESET_ALL}  {Fore.GREEN}h{Style.RESET_ALL} - Help (show current bot status){' '*40}║")
        print(f"{Fore.CYAN}║{Style.RESET_ALL}  {Fore.RED}q{Style.RESET_ALL} - Quit (graceful shutdown){' '*47}║")
        print(f"{Fore.CYAN}╠{'═'*78}╣{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║{Style.RESET_ALL}  {Fore.YELLOW}Note:{Style.RESET_ALL} Commands execute on next bot cycle (non-disruptive){' '*19}║")
        print(f"{Fore.CYAN}╚{'═'*78}╝{Style.RESET_ALL}\n")

    def _show_help(self):
        """Display help and current status"""
        print(f"\n{Fore.CYAN}╔{'═'*78}╗{Style.RESET_ALL}")
        print(f"{Fore.CYAN}║{' '*32}BOT STATUS{' '*35}║{Style.RESET_ALL}")
        print(f"{Fore.CYAN}╠{'═'*78}╣{Style.RESET_ALL}")

        # Get current status
        try:
            account = self.bot.trading_client.get_account()
            portfolio_value = float(account.portfolio_value) if account.portfolio_value else 0
            positions = self.bot.trading_client.list_positions()
            position_count = len(positions)

            print(f"{Fore.CYAN}║{Style.RESET_ALL}  Portfolio Value: ${portfolio_value:,.2f}{' '*(54-len(f'{portfolio_value:,.2f}'))}║")
            print(f"{Fore.CYAN}║{Style.RESET_ALL}  Open Positions: {position_count}{' '*(61-len(str(position_count)))}║")

            # Wheel positions
            if hasattr(self.bot, 'wheel_manager'):
                wheel_positions = self.bot.wheel_manager.get_all_positions()
                wheel_count = len(wheel_positions)
                print(f"{Fore.CYAN}║{Style.RESET_ALL}  Wheel Positions: {wheel_count}/7{' '*(58-len(str(wheel_count)))}║")

                # Total premium
                total_premium = sum(p.get('total_premium_collected', 0) for p in wheel_positions)
                print(f"{Fore.CYAN}║{Style.RESET_ALL}  Total Premium Collected: ${total_premium:,.2f}{' '*(45-len(f'{total_premium:,.2f}'))}║")

        except Exception as e:
            print(f"{Fore.CYAN}║{Style.RESET_ALL}  Status: {Fore.RED}Error fetching data{Style.RESET_ALL}{' '*48}║")
            logging.debug(f"[UI] Error fetching status: {e}")

        print(f"{Fore.CYAN}║{Style.RESET_ALL}  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{' '*48}║")
        print(f"{Fore.CYAN}╚{'═'*78}╝{Style.RESET_ALL}\n")
