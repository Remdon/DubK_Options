#!/usr/bin/env python3
"""
Clear Grok Data Fetcher Cache

This script clears the cached unusual options and earnings calendar data
so that Grok will fetch fresh data using the new web search prompts.
"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from utils.grok_data_fetcher import GrokDataFetcher
from config.default_config import config

def main():
    """Clear Grok cache"""
    print("[*] Initializing Grok Data Fetcher...")

    # Initialize Grok with API key from config
    grok = GrokDataFetcher(api_key=config.XAI_API_KEY)

    print("[*] Clearing all cached data...")
    grok.clear_cache()

    print("✓ Grok cache cleared successfully!")
    print("")
    print("Next scan will use fresh web search data from:")
    print("  - X (Twitter) for unusual options activity")
    print("  - Barchart, MarketBeat, Finviz for options flow")
    print("  - Yahoo Finance, Nasdaq for earnings calendar")
    print("")
    print("Run the bot to test: ./start_bot.sh")

if __name__ == '__main__':
    main()
