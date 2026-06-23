import json
from datetime import datetime

def verify_trades():
    try:
        with open("data/backtest_trades.json", "r") as f:
            trades = json.load(f)
    except FileNotFoundError:
        print("❌ data/backtest_trades.json not found. Please run the backtest first:")
        print("   python run_pipeline.py --capital 50000 --symbols SPY QQQ --lookback 60d --interval 15m")
        return

    failures = []
    print(f"Loaded {len(trades)} trades from backtest_trades.json. Verifying...")

    for t in trades:
        symbol = t.get("symbol")
        entry_date_str = t.get("entry_date")
        exit_date_str = t.get("exit_date")
        exp_str = t.get("expiration")
        reason = t.get("reason")
        trade_id = t.get("id")

        if not exp_str or not exit_date_str:
            continue

        try:
            # Parse expiration date
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d" if "-" in exp_str else "%Y%m%d").date()
            
            # Parse exit date
            exit_date = datetime.strptime(exit_date_str.split()[0], "%Y-%m-%d").date()
            
            # Verify if exit_date is greater than exp_date
            if exit_date > exp_date:
                failures.append({
                    "id": trade_id,
                    "symbol": symbol,
                    "entry_date": entry_date_str,
                    "exit_date": exit_date_str,
                    "expiration": exp_str,
                    "reason": reason
                })
        except Exception as e:
            print(f"⚠️ Error parsing dates for trade {trade_id}: {e}")

    if failures:
        print(f"❌ Verification FAILED: Found {len(failures)} trades with exit dates past expiration!")
        for f in failures[:10]:
            print(f"  - Trade {f['id']} ({f['symbol']}): Exited {f['exit_date']} | Expired {f['expiration']} | Reason: {f['reason']}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more.")
    else:
        print("✅ Verification PASSED: No trades have exit dates past their expiration dates!")

if __name__ == "__main__":
    verify_trades()
