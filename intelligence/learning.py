"""
ApexSpreadator — Learning Engine
Analyzes past trades and adjusts strategy parameters.
"""
from typing import List, Dict, Optional, Any
from datetime import datetime

from config import AgentConfig
from models import TradeRecord, LearningState
from utils import get_logger, load_json, save_json, safe_divide

logger = get_logger("Learning")


class LearningEngine:
    """
    Post-trade analysis and parameter adjustment.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.state = LearningState()
        self._load_state()
        self._adjustment_interval = 20

    def _load_state(self) -> None:
        """Load learned state from disk."""
        data = load_json(self.config.learning_file, {})
        if data:
            for key, value in data.items():
                if hasattr(self.state, key):
                    setattr(self.state, key, value)
            logger.info(f"Loaded learning state: {self.state.total_trades_analyzed} trades analyzed")

    def _save_state(self) -> None:
        """Save learned state to disk."""
        save_json(self.config.learning_file, self.state.to_dict())

    def analyze_trade(self, trade: TradeRecord) -> Dict[str, Any]:
        """
        Analyze a completed trade and update learning state.
        Returns analysis summary dict.
        """
        self.state.total_trades_analyzed += 1
        symbol = trade.symbol
        is_win = trade.realized_pnl > 0

        # 1. Update per-underlying stats
        if symbol not in self.state.underlying_stats:
            self.state.underlying_stats[symbol] = {
                "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                "total_pnl": 0.0, "avg_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
            }
        stats = self.state.underlying_stats[symbol]
        stats["total_trades"] += 1
        stats["winning_trades"] += 1 if is_win else 0
        stats["losing_trades"] += 0 if is_win else 1
        stats["total_pnl"] += trade.realized_pnl
        stats["avg_pnl"] = stats["total_pnl"] / stats["total_trades"]
        stats["best_trade"] = max(stats["best_trade"], trade.realized_pnl)
        stats["worst_trade"] = min(stats["worst_trade"], trade.realized_pnl)

        # 2. Update per-DTE-bucket stats
        dte_bucket = self._dte_bucket(trade)
        if dte_bucket not in self.state.dte_bucket_stats:
            self.state.dte_bucket_stats[dte_bucket] = {
                "total_trades": 0, "winning_trades": 0, "total_pnl": 0.0,
            }
        dte_stats = self.state.dte_bucket_stats[dte_bucket]
        dte_stats["total_trades"] += 1
        dte_stats["winning_trades"] += 1 if is_win else 0
        dte_stats["total_pnl"] += trade.realized_pnl

        # 3. Check if we should adjust parameters
        trades_since_adjustment = (
            self.state.total_trades_analyzed - self.state.last_adjustment_at_trade
        )
        adjustment_made = False
        if trades_since_adjustment >= self._adjustment_interval:
            self._adjust_parameters()
            adjustment_made = True

        self._save_state()

        analysis = {
            "trade_id": trade.id,
            "symbol": symbol,
            "outcome": "WIN" if is_win else "LOSS",
            "pnl": trade.realized_pnl,
            "pnl_pct": trade.realized_pnl_pct,
            "holding_days": trade.holding_days,
            "exit_reason": trade.exit_reason,
            "symbol_win_rate": safe_divide(stats["winning_trades"], stats["total_trades"]),
            "symbol_avg_pnl": stats["avg_pnl"],
            "dte_bucket": dte_bucket,
            "parameter_adjustment": adjustment_made,
        }

        emoji = "📈" if is_win else "📉"
        logger.info(
            f"{emoji} Trade analyzed: {symbol} | "
            f"{'WIN' if is_win else 'LOSS'} ${trade.realized_pnl:+.2f} | "
            f"Symbol win rate: {analysis['symbol_win_rate']:.1%} | "
            f"Total trades analyzed: {self.state.total_trades_analyzed}"
        )

        return analysis

    def _adjust_parameters(self) -> None:
        """Adjust strategy parameters based on accumulated data."""
        logger.info(f"🔧 Adjusting parameters at trade #{self.state.total_trades_analyzed}")

        old_values = {
            "entry_threshold": self.state.adjusted_entry_threshold,
            "profit_target": self.state.adjusted_profit_target,
            "stop_loss": self.state.adjusted_stop_loss,
        }

        total_wins = sum(
            s.get("winning_trades", 0) for s in self.state.underlying_stats.values()
        )
        total_trades = sum(
            s.get("total_trades", 0) for s in self.state.underlying_stats.values()
        )
        overall_win_rate = safe_divide(total_wins, total_trades, 0.5)

        if overall_win_rate < 0.50:
            self.state.adjusted_entry_threshold = min(75, self.state.adjusted_entry_threshold + 3)
        elif overall_win_rate > 0.70:
            self.state.adjusted_entry_threshold = max(60, self.state.adjusted_entry_threshold - 2)


        new_values = {
            "entry_threshold": self.state.adjusted_entry_threshold,
            "profit_target": self.state.adjusted_profit_target,
            "stop_loss": self.state.adjusted_stop_loss,
        }

        adjustment_record = {
            "trade_number": self.state.total_trades_analyzed,
            "timestamp": datetime.now().isoformat(),
            "overall_win_rate": overall_win_rate,
            "old_values": old_values,
            "new_values": new_values,
        }
        self.state.adjustment_history.append(adjustment_record)
        self.state.last_adjustment_at_trade = self.state.total_trades_analyzed

        logger.info(
            f"🔧 Parameters adjusted: "
            f"threshold {old_values['entry_threshold']:.0f}→{new_values['entry_threshold']:.0f}"
        )

    def _dte_bucket(self, trade: TradeRecord) -> str:
        """Categorize a trade into a DTE bucket based on expiration."""
        try:
            entry_dt = datetime.fromisoformat(trade.entry_time)
            exp_dt = datetime.strptime(trade.expiration, "%Y%m%d")
            dte = (exp_dt - entry_dt.replace(tzinfo=None)).days
        except Exception:
            dte = 30

        if dte < 15:
            return "0-15"
        elif dte < 30:
            return "15-30"
        elif dte < 45:
            return "30-45"
        else:
            return "45+"

    def get_insights(self) -> Dict[str, Any]:
        """Get learning insights for the dashboard."""
        best_symbol = None
        worst_symbol = None
        best_pnl = float("-inf")
        worst_pnl = float("inf")

        for symbol, stats in self.state.underlying_stats.items():
            avg = stats.get("avg_pnl", 0)
            if avg > best_pnl:
                best_pnl = avg
                best_symbol = symbol
            if avg < worst_pnl:
                worst_pnl = avg
                worst_symbol = symbol

        return {
            "total_trades_analyzed": self.state.total_trades_analyzed,
            "current_threshold": self.state.adjusted_entry_threshold,
            "current_profit_target": self.state.adjusted_profit_target,
            "current_stop_loss": self.state.adjusted_stop_loss,
            "best_underlying": best_symbol,
            "best_underlying_avg_pnl": best_pnl if best_pnl > float("-inf") else 0,
            "worst_underlying": worst_symbol,
            "worst_underlying_avg_pnl": worst_pnl if worst_pnl < float("inf") else 0,
            "underlying_stats": self.state.underlying_stats,
            "dte_bucket_stats": self.state.dte_bucket_stats,
            "adjustment_history": self.state.adjustment_history[-5:],
        }
