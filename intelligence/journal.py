"""
ApexSpreadator — Trade Journal
Logs every trade action with context and analysis.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime

from config import AgentConfig
from models import TradeRecord, Opportunity, JournalEntry
from utils import get_logger, generate_id, now_iso, load_json, save_json, format_currency, format_pnl

logger = get_logger("Journal")


class TradeJournal:
    """
    Maintains a trade journal with entries for:
    - Trade entries (with pre-trade analysis)
    - Trade exits (with post-trade review)
    - Lessons learned
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._entries: List[JournalEntry] = []
        self._load()

    def _load(self) -> None:
        """Load journal entries from disk."""
        data = load_json(self.config.journal_file, [])
        if isinstance(data, list):
            for item in data:
                entry = JournalEntry(**{k: v for k, v in item.items() if hasattr(JournalEntry, k)})
                self._entries.append(entry)
        logger.info(f"Loaded {len(self._entries)} journal entries")

    def _save(self) -> None:
        """Save journal entries to disk."""
        data = [e.to_dict() for e in self._entries]
        save_json(self.config.journal_file, data)

    def log_entry(
        self,
        opportunity: Opportunity,
        quantity: int,
        analysis: str = "",
    ) -> JournalEntry:
        """Log a new trade entry."""
        spread = opportunity.spread
        long_stk = spread.long_leg.strike if (spread and spread.long_leg) else 0.0
        short_stk = spread.short_leg.strike if (spread and spread.short_leg) else 0.0
        type_str = "Bull Call" if (spread and spread.right == "C") else "Bear Put"

        content = (
            f"OPENED: {spread.symbol if spread else ''} {long_stk}/{short_stk} {type_str} Spread. "
            f"Expiration: {spread.expiration if spread else ''}. "
            f"Qty: {quantity} @ {format_currency(spread.net_debit if spread else 0.0)} debit. "
            f"R:R Ratio: {spread.rr_ratio if spread else 0.0:.2f}."
        )

        if analysis:
            content += f"\n\nAGENT ANALYSIS: {analysis}"

        entry = JournalEntry(
            id=generate_id("JRN"),
            timestamp=now_iso(),
            trade_id=spread.id if spread else "",
            entry_type="entry",
            symbol=spread.symbol if spread else "",
            content=content,
            data={
                "long_strike": long_stk,
                "short_strike": short_stk,
                "right": spread.right if spread else "C",
                "net_debit": spread.net_debit if spread else 0.0,
                "quantity": quantity,
                "rr_ratio": spread.rr_ratio if spread else 0.0,
                "underlying_price": opportunity.underlying_price,
            },
        )

        self._entries.append(entry)
        self._save()

        logger.info(f"📝 Journal entry: {content[:100]}...")
        return entry

    def log_exit(
        self,
        trade: TradeRecord,
        analysis: str = "",
    ) -> JournalEntry:
        """Log a trade exit."""
        emoji = "🟢" if trade.realized_pnl > 0 else "🔴"
        type_str = "Bull Call" if trade.right == "C" else "Bear Put"
        content = (
            f"{emoji} CLOSED: {trade.symbol} {trade.long_strike}/{trade.short_strike} {type_str}. "
            f"P&L: {format_pnl(trade.realized_pnl)} ({trade.realized_pnl_pct:+.1%}). "
            f"Exit reason: {trade.exit_reason}. "
            f"Held: {trade.holding_days} days."
        )

        if analysis:
            content += f"\n\nPOST-TRADE REVIEW: {analysis}"

        entry = JournalEntry(
            id=generate_id("JRN"),
            timestamp=now_iso(),
            trade_id=trade.id,
            entry_type="exit",
            symbol=trade.symbol,
            content=content,
            data={
                "realized_pnl": trade.realized_pnl,
                "realized_pnl_pct": trade.realized_pnl_pct,
                "exit_reason": trade.exit_reason,
                "holding_days": trade.holding_days,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
            },
        )

        self._entries.append(entry)
        self._save()

        logger.info(f"📝 Journal exit: {content[:100]}...")
        return entry

    def log_lesson(
        self,
        trade_id: str,
        symbol: str,
        lesson: str,
    ) -> JournalEntry:
        """Log a lesson learned from a trade."""
        entry = JournalEntry(
            id=generate_id("JRN"),
            timestamp=now_iso(),
            trade_id=trade_id,
            entry_type="lesson",
            symbol=symbol,
            content=f"LESSON: {lesson}",
        )

        self._entries.append(entry)
        self._save()
        return entry

    def log_weekly_summary(self, content: str) -> JournalEntry:
        """Log a weekly summary/strategic analysis."""
        entry = JournalEntry(
            id=generate_id("JRN"),
            timestamp=now_iso(),
            trade_id="",
            entry_type="weekly_summary",
            symbol="",
            content=content,
            data={},
        )

        self._entries.append(entry)
        self._save()
        logger.info(f"📝 Journal weekly summary: {content[:100]}...")
        return entry

    def get_recent_entries(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get most recent journal entries for the dashboard."""
        return [e.to_dict() for e in self._entries[-count:]]

    def get_entries_for_trade(self, trade_id: str) -> List[Dict[str, Any]]:
        """Get all journal entries for a specific trade."""
        return [e.to_dict() for e in self._entries if e.trade_id == trade_id]

    @property
    def entries(self) -> List[JournalEntry]:
        return self._entries
