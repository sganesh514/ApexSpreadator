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


def parse_lookback(lookback_str: str) -> int:
    lookback_str = lookback_str.lower().strip()
    if lookback_str.endswith("d"):
        return int(lookback_str[:-1])
    elif lookback_str.endswith("y"):
        return int(lookback_str[:-1]) * 365
    else:
        try:
            return int(lookback_str)
        except ValueError:
            raise ValueError(f"Invalid lookback format: {lookback_str}")

def validate_pipeline_config(interval: str, lookback_days: int):
    if interval == "15m" and lookback_days > 60:
        raise ValueError("15m interval supports a maximum of 60 days lookback.")
    if interval == "1h" and lookback_days > 730:
        raise ValueError("1h interval supports a maximum of 730 days lookback.")

def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Pipeline runner")
    parser.add_argument(
        "--lookback",
        type=str,
        default="3y",
        help="Lookback period for data (e.g., '60d', '2y')"
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="1d",
        choices=["15m", "1h", "1d"],
        help="Data interval to fetch and backtest"
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

    lookback_days = parse_lookback(args.lookback)
    validate_pipeline_config(args.interval, lookback_days)

    # 1. Clean the data folder
    clean_data_dir()

    # 2. Refetch the historical data for the specified underlyings
    symbols_str = ", ".join(args.symbols)
    run_command(
        [sys.executable, "data/download_historical.py", "--days", str(lookback_days), "--interval", args.interval, "--symbols"] + args.symbols,
        f"Downloading historical {args.lookback} data ({args.interval}) for {symbols_str}"
    )

    # 3. Run the backtest on the newly fetched data
    run_command(
        [sys.executable, "core/backtester.py", "--csv", f"data/{args.interval}/all_symbols.csv", "--capital", str(args.capital)],
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
