import sys
import os
import argparse

# Add parent directory to path so we can import from core and config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from core.screener import ScreenerEngine
from utils import setup_logging

def main():
    setup_logging()
    
    parser = argparse.ArgumentParser(description="Test dynamic stock screener")
    parser.add_argument("--type", type=str, choices=["static", "sp500", "nasdaq100"], default="static",
                        help="Screener type: static, sp500, or nasdaq100")
    parser.add_argument("--limit", type=int, default=5,
                        help="Number of candidates to return")
    parser.add_argument("--min-volume", type=int, default=500000,
                        help="Minimum volume threshold")
    args = parser.parse_args()

    print("=" * 60)
    print(f"TESTING SCREENER: type={args.type}, limit={args.limit}, min_volume={args.min_volume}")
    print("=" * 60)

    # Configure Strategy settings
    CONFIG.strategy.screener_type = args.type
    CONFIG.strategy.screener_limit = args.limit
    CONFIG.strategy.screener_min_volume = args.min_volume

    # Run Screener Engine
    screener = ScreenerEngine(CONFIG)
    
    # Test Wikipedia fetching directly if requested
    if args.type == "sp500":
        sp500 = screener._fetch_sp500_constituents()
        print(f"Fetched {len(sp500)} S&P 500 constituents from Wikipedia.")
        print(f"First 10 constituents: {sp500[:10]}")
    elif args.type == "nasdaq100":
        ndx = screener._fetch_nasdaq100_constituents()
        print(f"Fetched {len(ndx)} Nasdaq-100 constituents from Wikipedia.")
        print(f"First 10 constituents: {ndx[:10]}")
        
    print("\nRunning get_candidate_list()...")
    candidates = screener.get_candidate_list()
    
    print("\n" + "=" * 60)
    print(f"FINAL WATCHLIST CANDIDATES FOUND ({len(candidates)}):")
    print(f"{candidates}")
    print("=" * 60)

if __name__ == "__main__":
    main()
