"""
ApexSpreadator — Bulk Sector Backtester & Monte Carlo Engine
Backtests 100 individual symbols across 10 sectors, isolating capital for each run.
Provides a summary of sector performance and generates Monte Carlo projections 
for each individual symbol.
"""
import os
import sys
import json
import subprocess
import random
import numpy as np
import pandas as pd
from typing import Dict, List

# Ensure utf-8 encoding for stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# 100 Symbols grouped by 10 Sectors
SECTORS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "CRM", "QCOM", "TXN", "INTC"],
    "Healthcare": ["LLY", "JNJ", "UNH", "MRK", "ABBV", "PFE", "AMGN", "ISRG", "SYK", "MDT"],
    "Financials": ["JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "C", "BLK", "SCHW"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "TGT"],
    "Communication Services": ["GOOGL", "META", "NFLX", "CMCSA", "TMUS", "DIS", "VZ", "T", "CHTR", "EA"],
    "Industrials": ["GE", "CAT", "UNP", "BA", "HON", "RTX", "LMT", "DE", "UPS", "MMM"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "CL", "KMB", "HSY"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "HAL", "VLO", "OXY", "PSX"],
    "Utilities": ["NEE", "SO", "DUK", "SRE", "AEP", "D", "EXC", "XEL", "ED", "WEC"],
    "Real Estate": ["PLD", "AMT", "EQIX", "CCI", "PSA", "O", "SPG", "WELL", "DLR", "AVB"]
}

def main():
    print("==================================================")
    print("   APEXSPREADATOR — Sector Backtest & Monte Carlo ")
    print("==================================================")
    
    all_symbols = []
    for symbols in SECTORS.values():
        all_symbols.extend(symbols)
        
    print(f"Total symbols to test: {len(all_symbols)} across {len(SECTORS)} sectors.\n")
    
    # 1. Bulk Download Data
    print("[1/4] Triggering Bulk Historical Data Download (3 Years)...")
    download_cmd = [sys.executable, "data/download_historical.py", "--years", "3", "--symbols"] + all_symbols
    try:
        subprocess.run(download_cmd, check=True)
    except subprocess.CalledProcessError:
        print("❌ Error during bulk data download. Aborting.")
        sys.exit(1)
        
    master_csv = "data/all_symbols_daily.csv"
    if not os.path.exists(master_csv):
        print(f"❌ Master CSV {master_csv} not found. Aborting.")
        sys.exit(1)
        
    # 2. Slice CSV for individual runs
    print("\n[2/4] Loading Master CSV...")
    df_master = pd.read_csv(master_csv)
    
    print("\n[3/4] Running isolated backtests and Monte Carlo for each symbol...")
    results = []
    os.makedirs("data/temp", exist_ok=True)
    
    for sector, symbols in SECTORS.items():
        for sym in symbols:
            print(f"  -> Processing {sym} ({sector})...")
            
            # Slice and save temporary CSV for the individual symbol
            df_sym = df_master[df_master["Symbol"] == sym]
            if df_sym.empty:
                print(f"     ⚠️ No data found for {sym}. Skipping.")
                continue
                
            temp_csv = f"data/temp/{sym}_temp.csv"
            df_sym.to_csv(temp_csv, index=False)
            
            # Run backtester
            bt_cmd = [sys.executable, "core/backtester.py", "--csv", temp_csv, "--capital", "25000"]
            subprocess.run(bt_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Read backtest report & trades
            report_path = "data/backtest_report.json"
            trades_path = "data/backtest_trades.json"
            
            if os.path.exists(report_path) and os.path.exists(trades_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    rep = json.load(f)
                with open(trades_path, "r", encoding="utf-8") as f:
                    trades = json.load(f)
                
                trade_pnls = [t["pnl"] for t in trades]
                
                # Monte Carlo Projections (Simulate 50 trades, 1000 iterations)
                mc_expected = 0.0
                mc_opt = 0.0
                mc_pess = 0.0
                prob_profit = 0.0
                
                if len(trade_pnls) >= 2:
                    simulations = []
                    for _ in range(1000):
                        path_pnl = sum(random.choice(trade_pnls) for _ in range(50))
                        simulations.append(path_pnl)
                        
                    mc_expected = float(np.median(simulations))
                    mc_opt = float(np.percentile(simulations, 95))
                    mc_pess = float(np.percentile(simulations, 5))
                    prob_profit = sum(1 for x in simulations if x > 0) / 1000.0
                
                results.append({
                    "symbol": sym,
                    "sector": sector,
                    "trades": rep.get("total_trades", 0),
                    "win_rate": rep.get("win_rate_pct", 0.0),
                    "pnl": rep.get("total_pnl", 0.0),
                    "return_pct": rep.get("total_return_pct", 0.0),
                    "mc_expected_pnl": mc_expected,
                    "mc_optimistic_pnl": mc_opt,
                    "mc_pessimistic_pnl": mc_pess,
                    "mc_prob_profit": prob_profit
                })
                
                # Remove files to avoid cross-contamination
                os.remove(report_path)
                os.remove(trades_path)
                if os.path.exists("data/backtest_equity_curve.csv"):
                    os.remove("data/backtest_equity_curve.csv")
            else:
                print(f"     ❌ Backtester failed to generate data for {sym}.")

    # Clean up temp CSVs
    import shutil
    shutil.rmtree("data/temp", ignore_errors=True)
    
    print("\n[4/4] Generating Final Reports...\n")
    
    # 1. Aggregate Sector Stats
    sector_stats = {}
    for r in results:
        sec = r["sector"]
        if sec not in sector_stats:
            sector_stats[sec] = {
                "total_pnl": 0.0,
                "total_trades": 0,
                "cumulative_return_pct": 0.0,
                "sum_win_rate": 0.0,
                "symbol_count": 0
            }
        
        sector_stats[sec]["total_pnl"] += r["pnl"]
        sector_stats[sec]["total_trades"] += r["trades"]
        sector_stats[sec]["cumulative_return_pct"] += r["return_pct"]
        sector_stats[sec]["sum_win_rate"] += r["win_rate"]
        sector_stats[sec]["symbol_count"] += 1
        
    summary_list = []
    for sec, stats in sector_stats.items():
        count = stats["symbol_count"]
        if count == 0: continue
        avg_win_rate = stats["sum_win_rate"] / count
        avg_return = stats["cumulative_return_pct"] / count
        
        summary_list.append({
            "sector": sec,
            "total_pnl": stats["total_pnl"],
            "avg_return_pct": avg_return,
            "avg_win_rate_pct": avg_win_rate,
            "total_trades": stats["total_trades"],
            "symbols_tested": count
        })
        
    summary_list.sort(key=lambda x: x["total_pnl"], reverse=True)
    
    print("===============================================================================")
    print("                       SECTOR PERFORMANCE RANKING                              ")
    print("===============================================================================")
    print(f"{'Sector':<25} | {'Total PnL':>12} | {'Avg Return':>10} | {'Avg Win Rate':>12} | {'Trades'}")
    print("-" * 79)
    for s in summary_list:
        print(f"{s['sector']:<25} | ${s['total_pnl']:,.2f} | {s['avg_return_pct']:.2f}% | {s['avg_win_rate_pct']:.1f}% | {s['total_trades']}")
    print("===============================================================================\n")
    
    # 2. Save JSON outputs
    with open("data/sector_analysis_results.json", "w", encoding="utf-8") as f:
        json.dump(summary_list, f, indent=2)
        
    # Save individual symbol results for the Monte Carlo Dashboard
    with open("data/symbol_analysis_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        
    print("✅ Sector results saved to data/sector_analysis_results.json")
    print("✅ Symbol Monte Carlo metrics saved to data/symbol_analysis_results.json")

if __name__ == "__main__":
    main()
