"""
ApexSpreadator — Pipeline runner
Cleans the data directory, downloads historical data for SPY, QQQ,
runs the backtester, and bootstraps the learning agent.
"""
import os
import shutil
import subprocess
import sys
import argparse
from core.universe_manager import UniverseManager

# Configure stdout encoding to avoid Windows console errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Dynamically import CONFIG if possible to use its defaults
try:
    from config import CONFIG
    default_capital = CONFIG.account.starting_capital
    universe_mgr = UniverseManager(CONFIG)
    default_symbols = universe_mgr.get_universe()
except Exception as e:
    default_capital = 25000.0
    default_symbols = ["SPY", "QQQ"]


def clean_data_dir():
    print("🧹 Cleaning data directory...")
    data_dir = "data"
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        return

    for item in os.listdir(data_dir):
        item_path = os.path.join(data_dir, item)
        # Keep critical persistence files, logs, and download script
        if item in ["download_historical.py", "learning_state.json", "journal.json", "agent.log", "symbol_analysis_results.json"]:
            continue
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            print(f"  Removed: {item}")
        except Exception as e:
            print(f"  ❌ Failed to delete {item}: {e}")


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


def parse_lookback_to_days(lookback_str: str) -> int:
    s = lookback_str.strip().lower()
    if s.endswith("d"):
        return int(s[:-1])
    elif s.endswith("y"):
        return int(s[:-1]) * 365
    elif s.endswith("w"):
        return int(s[:-1]) * 7
    elif s.endswith("mo"):
        return int(s[:-2]) * 30
    elif s.endswith("m"):
        return int(s[:-1]) * 30
    else:
        try:
            return int(s)
        except ValueError:
            return 365


def validate_and_truncate_lookback(interval: str, lookback_str: str) -> tuple[int, str, bool]:
    days = parse_lookback_to_days(lookback_str)
    interval = interval.strip().lower()
    truncated = False
    
    if interval == "15m":
        if days > 60:
            days = 60
            truncated = True
    elif interval in ["1h", "60m"]:
        if days > 730:
            days = 730
            truncated = True
            
    if truncated:
        final_str = f"{days}d"
    else:
        final_str = lookback_str
        
    return days, final_str, truncated


def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Pipeline runner")
    parser.add_argument(
        "--lookback",
        type=str,
        default="2y",
        help="Lookback period (e.g. '60d', '730d', '2y') (default: 2y)"
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
        help=f"Symbols to backtest (default: {', '.join(default_symbols[:5])}...)"
    )
    parser.add_argument(
        "--timeframe", "--interval",
        dest="interval",
        type=str,
        default=CONFIG.strategy.default_timeframe,
        help=f"Timeframe/interval to backtest (default: {CONFIG.strategy.default_timeframe})"
    )
    args = parser.parse_args()

    # Lookback validation check and truncation
    days_requested, final_lookback, truncated = validate_and_truncate_lookback(args.interval, args.lookback)
    if truncated:
        print(f"⚠️ Warning: Requested lookback '{args.lookback}' exceeds the yfinance limit for interval '{args.interval}'.")
        print(f"  Truncating lookback to {final_lookback}.")
        # Log the truncation
        from utils import get_logger
        get_logger("Pipeline").warning(
            f"Requested lookback '{args.lookback}' truncated to '{final_lookback}' for interval '{args.interval}'."
        )
    
    args.lookback = final_lookback

    # Override CONFIG default timeframe
    CONFIG.strategy.default_timeframe = args.interval

    # 1. Clean the data folder
    clean_data_dir()

    # 2. Refetch the last N years of data for the specified underlyings
    symbols_str = ", ".join(args.symbols)
    run_command(
        [sys.executable, "data/download_historical.py", "--lookback", args.lookback, "--interval", args.interval, "--symbols"] + args.symbols,
        f"Downloading historical {args.lookback} {args.interval} data for {symbols_str}"
    )

    # 3. Run the backtest on the newly fetched data
    backtest_cmd = [
        sys.executable, "core/backtester.py", 
        "--csv", f"data/{args.interval}/all_symbols.csv", 
        "--capital", str(args.capital),
        "--interval", args.interval,
        "--lookback", args.lookback
    ]
    if args.symbols:
        backtest_cmd.extend(["--symbols"] + args.symbols)

    run_command(
        backtest_cmd,
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
