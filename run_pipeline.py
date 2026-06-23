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


def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Pipeline runner")
    parser.add_argument(
        "--years",
        type=int,
        default=3,
        help="Number of years of history to backtest (default: 3)"
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
    args = parser.parse_args()

    # 1. Clean the data folder
    clean_data_dir()

    # 2. Refetch the last N years of data for the specified underlyings
    symbols_str = ", ".join(args.symbols)
    run_command(
        [sys.executable, "data/download_historical.py", "--years", str(args.years), "--symbols"] + args.symbols,
        f"Downloading historical {args.years}-year data for {symbols_str}"
    )

    # 3. Run the backtest on the newly fetched data
    backtest_cmd = [
        sys.executable, "core/backtester.py", 
        "--csv", "data/all_symbols_daily.csv", 
        "--capital", str(args.capital)
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
