"""
Color scheme component for consistent console output formatting.
Provides ANSI color codes for different message types.
"""

class Colors:
    """Color scheme for console output"""

    HEADER = '\033[1;36m'    # Cyan bright
    SUCCESS = '\033[1;32m'   # Green bright
    WARNING = '\033[1;33m'   # Yellow bright
    ERROR = '\033[1;31m'     # Red bright
    INFO = '\033[1;34m'      # Blue bright
    DIM = '\033[1;37m'       # White bright (for dim text)
    RESET = '\033[0m'        # Reset all formatting
