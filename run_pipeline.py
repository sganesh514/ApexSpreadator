"""
ApexSpreadator — Pipeline runner
Cleans the interval-specific data directory, downloads historical data,
runs the backtester, and bootstraps the learning agent.
"""
import os
import shutil
import subprocess
import sys
import argparse
import re

# Configure stdout encoding to avoid Windows console errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Dynamically import CONFIG if possible to use its defaults
try:
    from config import CONFIG
    default_capital = CONFIG.account.starting_capital
    default_symbols = CONFIG.strategy.underlyings
except ImportError:
    default_capital = 25000.0
    default_symbols = ["SPY", "QQQ"]


def parse_lookback(lookback_str: str) -> int:
    """Parse lookback string like '60d', '2y', '1y' into total integer days."""
    lookback_str = lookback_str.strip().lower()
    match = re.match(r'^(\d+)\s*([dy])$', lookback_str)
    if not match:
        raise ValueError(f"Invalid lookback format '{lookback_str}'. Use e.g. '60d', '2y'.")
    value = int(match.group(1))
    unit = match.group(2)
    if unit == 'y':
        return value * 365
    return value


def clean_interval_dir(interval: str):
    """Clean only the specific data/{interval}/ folder, preserving root data/ files."""
    interval_dir = os.path.join("data", interval)
    if not os.path.exists(interval_dir):
        os.makedirs(interval_dir, exist_ok=True)
        print(f"🧹 Created data/{interval}/ directory.")
        return

    print(f"🧹 Cleaning data/{interval}/ directory...")
    for item in os.listdir(interval_dir):
        item_path = os.path.join(interval_dir, item)
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            print(f"  Removed: {interval}/{item}")
        except Exception as e:
            print(f"  ❌ Failed to delete {interval}/{item}: {e}")


def run_command(cmd, desc):
    print(f"\n🚀 {desc}...")
    print(f"Running: {' '.join(cmd)}")
    try:
        # Stream the output to terminal in real-time
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1
        )
        for line in process.stdout:
            print(line, end="")
        process.wait()
        if process.returncode != 0:
            print(f"\n❌ Error during: {desc} (Exit code: {process.returncode})")
            sys.exit(process.returncode)
        print(f"✅ Completed: {desc}")
    except Exception as e:
        print(f"\n❌ Exception running command: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Pipeline runner")
    parser.add_argument(
        "--lookback",
        type=str,
        default="2y",
        help="Lookback period, e.g. '60d', '2y' (default: 2y)"
    )
    parser.add_argument(
        "--interval",
        type=str,
        choices=["15m", "1d", "1y"],
        default="1d",
        help="Data interval (default: 1d)"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=default_capital,
        help=f"Starting capital for backtest (default: ${default_capital:,.2f})"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        nargs="+",
        default=default_symbols,
        help=f"Symbols to backtest (default: {', '.join(default_symbols)})"
    )
    args = parser.parse_args()

    # Parse lookback into integer days
    lookback_days = parse_lookback(args.lookback)
    interval = args.interval

    # Paths
    csv_path = f"data/{interval}/all_symbols.csv"

    # 1. Clean only the interval-specific folder
    clean_interval_dir(interval)

    # 2. Download historical data into data/{interval}/
    symbols_str = ", ".join(args.symbols)
    run_command(
        [sys.executable, "data/download_historical.py",
         "--lookback", str(lookback_days),
         "--interval", interval,
         "--symbols"] + args.symbols,
        f"Downloading {args.lookback} of {interval} data for {symbols_str}"
    )

    # 3. Run the backtest on the newly fetched data
    run_command(
        [sys.executable, "core/backtester.py",
         "--csv", csv_path,
         "--capital", str(args.capital)],
        f"Running backtest on {symbols_str} with ${args.capital:,.2f} capital"
    )

    # 4. Bootstrap learning agent from backtest results
    if os.path.exists("tools/bootstrap_agent.py"):
        run_command(
            [sys.executable, "tools/bootstrap_agent.py"],
            "Bootstrapping learning agent with backtest results"
        )

    print("\n🎉 Pipeline completed successfully!")


if __name__ == "__main__":
    main()
