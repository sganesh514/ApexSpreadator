"""
ApexSpreadator Agent — Backtest Bootstrapper
Feeds historical backtest trades to the LearningEngine and uses Ollama for a global strategy memo.
"""
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
import json
import asyncio
from datetime import datetime

# Add root folder to sys.path to resolve imports correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AgentConfig
from models import TradeRecord, JournalEntry
from intelligence.learning import LearningEngine
from intelligence.ollama_analyst import OllamaAnalyst
from intelligence.journal import TradeJournal
from utils import get_logger, setup_logging

logger = get_logger("Bootstrap")

async def bootstrap():
    setup_logging()
    print("==================================================")
    print("   APEXSPREADATOR — Agent Backtest Bootstrapper   ")
    print("==================================================")

    config = AgentConfig()
    learning = LearningEngine(config)
    journal = TradeJournal(config)
    analyst = OllamaAnalyst(config)

    # Load backtest data
    trades_path = "data/backtest_trades.json"
    report_path = "data/backtest_report.json"

    if not os.path.exists(trades_path) or not os.path.exists(report_path):
        print("❌ Missing backtest files. Please run the backtester first:")
        print("   python core/backtester.py --csv data/all_symbols_daily.csv --capital 25000")
        return

    with open(trades_path, "r", encoding="utf-8") as f:
        backtest_trades = json.load(f)

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    print(f"Loaded {len(backtest_trades)} historical trades from backtest.")

    # 1. Feed trades to LearningEngine in chronological order
    print("\nSeeding LearningEngine with historical trade outcomes...")
    sorted_trades_chrono = sorted(backtest_trades, key=lambda x: x.get("entry_date", ""))

    for t in sorted_trades_chrono:
        record = TradeRecord(
            id=t["id"],
            symbol=t["symbol"],
            long_strike=t["long_strike"],
            short_strike=t["short_strike"],
            right=t.get("right", "C"),
            expiration=t.get("expiration", "") or t.get("exit_date", "").replace("-", ""),
            quantity=t["qty"],
            entry_time=t["entry_date"] + "T09:30:00-05:00",
            entry_price=t["entry_price"],
            underlying_price_at_entry=t.get("underlying_price_at_entry", 0.0),
            exit_time=t["exit_date"] + "T16:00:00-05:00",
            exit_price=t["exit_price"],
            underlying_price_at_exit=t.get("underlying_price_at_exit", 0.0),
            exit_reason=t["reason"],
            realized_pnl=t["pnl"],
            realized_pnl_pct=t["pnl_pct"],
            holding_days=t["holding_days"],
            agent_analysis="",
            lessons_learned=""
        )
        learning.analyze_trade(record)

    print(f"\n✅ LearningEngine updated! Total trades analyzed: {learning.state.total_trades_analyzed}")
    print("Adjusted strategy parameters:")
    print(f"  - Entry score threshold: {learning.state.adjusted_entry_threshold:.1f}")

    # 2. Call Ollama for meta-analysis
    print("\nChecking Ollama model availability...")
    ollama_ready = await analyst.check_availability()
    if not ollama_ready:
        print("⚠️ Ollama is not running locally. Skipping AI summary. Seeded learning state saved successfully.")
        return

    print("Generating Global Strategy Recommendation Report from Ollama...")
    # Gather top winners and losers for prompt context
    sorted_by_pnl = sorted(backtest_trades, key=lambda x: x["pnl"])
    top_losers = sorted_by_pnl[:3]
    top_winners = sorted_by_pnl[-3:]

    winners_text = "\n".join(
        f"  - {t['symbol']} {t['long_strike']}/{t['short_strike']}: P&L ${t['pnl']:+,.2f} ({t['reason']}, held {t['holding_days']} days)"
        for t in top_winners
    )
    losers_text = "\n".join(
        f"  - {t['symbol']} {t['long_strike']}/{t['short_strike']}: P&L ${t['pnl']:+,.2f} ({t['reason']}, held {t['holding_days']} days)"
        for t in top_losers
    )

    prompt = f"""You are an expert options quant. Our Vertical spread trading bot just completed a backtest.

BACKTEST METRICS:
- Total Trades: {report['total_trades']}
- Win Rate: {report['win_rate_pct']}%
- Profit Factor: {report['profit_factor']}
- Sharpe Ratio: {report['sharpe_ratio']}
- Max Drawdown: {report['max_drawdown_pct']}%
- Exits Breakdown: {json.dumps(report['exits_breakdown'])}

SAMPLE OF TOP WINNING TRADES:
{winners_text}

SAMPLE OF TOP LOSING TRADES:
{losers_text}

Provide a 4-5 sentence strategic recommendation memo for the live trading agent:
1. Summarize the major vulnerability of the strategy (e.g. drawdown source, exits).
2. Recommend the best symbol watchlist configuration based on these results.
3. Suggest concrete exit or entry parameter changes (e.g., risk sizing, R:R thresholds).
4. Outline how the agent should manage risk on single-name stocks versus index ETFs.
"""

    summary_text = await analyst._generate(prompt)

    if summary_text:
        print("\nOllama Recommendations:")
        print(summary_text)
        print("\nSaving analysis to Trade Journal...")
        journal.log_weekly_summary(
            f"BOOTSTRAP STRATEGY MEMO (Backtest Analysis)\n\n"
            f"Stats: WR {report['win_rate_pct']}%, PF {report['profit_factor']}, Sharpe {report['sharpe_ratio']}, Max DD {report['max_drawdown_pct']}%\n\n"
            f"{summary_text}"
        )
        print("✅ Bootstrap complete. Journal entry saved.")
    else:
        print("❌ Ollama did not return a response.")

if __name__ == "__main__":
    asyncio.run(bootstrap())
