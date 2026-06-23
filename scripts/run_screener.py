"""
ApexSpreadator — Standalone Offline Screener
Executes the screening pipeline using yfinance to generate active zones.
"""
import os
import sys
import argparse

# Add project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from core.screener import ScreenerEngine
from utils import setup_logging, get_logger

# Set up logging for the screener run
setup_logging()
logger = get_logger("RunScreener")


def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator Offline Screener")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of volatile candidates to select (excluding anchors)"
    )
    args = parser.parse_args()

    logger.info("Initializing ScreenerEngine...")
    screener = ScreenerEngine(CONFIG)
    
    logger.info("Running offline screening pipeline...")
    try:
        screener.run_screening_pipeline(limit=args.limit)
        logger.info("✅ Screening pipeline completed successfully. Output written to data/active_zones.json")
    except Exception as e:
        logger.error(f"❌ Screening pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
