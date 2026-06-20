"""
ApexSpreadator — Position Manager
Tracks open positions, monitors P&L, and triggers exits.
"""
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime

from config import AgentConfig
from models import (
    Position, TradeRecord, TradeStatus, ExitReason,
    VerticalSpread, AccountSnapshot
)
from core.broker_base import BrokerBase
from utils import (
    get_logger, generate_id, now_iso, format_currency, format_pnl, format_pct,
    load_json, save_json, calculate_dte
)

logger = get_logger("Positions")


class PositionManager:
    """
    Manages all open positions.
    - Tracks entry/exit of vertical spreads
    - Monitors real-time P&L by querying broker
    - Syncs with broker portfolio data
    - Persists state for crash recovery
    """

    def __init__(self, broker: BrokerBase, config: AgentConfig):
        self.broker = broker
        self.config = config
        self._positions: Dict[str, Position] = {}  # id -> Position
        self._trade_history: List[TradeRecord] = []
        self._load_state()

    def _load_state(self) -> None:
        """Load persisted positions and trade history."""
        # Load trade history
        history_data = load_json(self.config.trades_file, [])
        if isinstance(history_data, list):
            for item in history_data:
                record = TradeRecord(**{k: v for k, v in item.items() if hasattr(TradeRecord, k)})
                self._trade_history.append(record)
        logger.info(f"Loaded {len(self._trade_history)} historical trades")

    def _save_state(self) -> None:
        """Persist trade history to disk."""
        data = [t.to_dict() for t in self._trade_history]
        save_json(self.config.trades_file, data)

    # ── Position Lifecycle ───────────────────────────────────────

    def add_position(self, position: Position) -> None:
        """Add a new position (after order placement)."""
        self._positions[position.id] = position
        logger.info(
            f"📌 Position added: {position.id} | {position.spread.description} | "
            f"Qty: {position.quantity} | Entry: {format_currency(position.entry_price)}"
        )

    def confirm_fill(self, position_id: str, fill_price: float) -> None:
        """Confirm that an entry order was filled."""
        pos = self._positions.get(position_id)
        if pos:
            pos.status = TradeStatus.OPEN
            pos.entry_price = fill_price
            pos.current_value = fill_price
            logger.info(
                f"✅ Position filled: {pos.id} | {pos.spread.description} | "
                f"Fill price: {format_currency(fill_price)}"
            )

    def close_position(self, position_id: str, exit_price: float, exit_reason: ExitReason, underlying_price: float = 0.0) -> Optional[TradeRecord]:
        """
        Close a position and create a trade record.
        Returns the TradeRecord.
        """
        pos = self._positions.get(position_id)
        if not pos:
            logger.error(f"Position {position_id} not found")
            return None

        # Calculate realized P&L
        # P&L = (exit_price - entry_price) * quantity * 100
        realized_pnl = (exit_price - pos.entry_price) * pos.quantity * 100
        realized_pnl_pct = (exit_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        # Calculate holding period
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time)
            exit_dt = datetime.now(entry_dt.tzinfo) if entry_dt.tzinfo else datetime.now()
            holding_days = (exit_dt - entry_dt).days
        except Exception:
            holding_days = 0

        # Create trade record
        record = TradeRecord(
            id=pos.id,
            symbol=pos.spread.symbol if pos.spread else "",
            long_strike=pos.spread.long_leg.strike if (pos.spread and pos.spread.long_leg) else 0.0,
            short_strike=pos.spread.short_leg.strike if (pos.spread and pos.spread.short_leg) else 0.0,
            right=pos.spread.right if pos.spread else "C",
            expiration=pos.spread.expiration if pos.spread else "",
            quantity=pos.quantity,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            underlying_price_at_entry=pos.underlying_price_at_entry,
            exit_time=now_iso(),
            exit_price=exit_price,
            underlying_price_at_exit=underlying_price,
            exit_reason=exit_reason.value,
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
            holding_days=holding_days,
            agent_analysis=f"Executed {exit_reason.value} close order.",
            lessons_learned=""
        )

        # Update status and save
        pos.status = TradeStatus.CLOSED
        self._trade_history.append(record)
        del self._positions[position_id]
        self._save_state()

        emoji = "🟢" if realized_pnl >= 0 else "🔴"
        logger.info(
            f"{emoji} Position closed: {pos.spread.description if pos.spread else ''} | "
            f"P&L: {format_pnl(realized_pnl)} ({format_pct(realized_pnl_pct)}) | "
            f"Reason: {exit_reason.value} | Held: {holding_days} days"
        )

        return record

    # ── Real-time Updates ────────────────────────────────────────

    async def update_positions(self) -> None:
        """
        Update all open positions with current market data.
        Called every position_check_interval.
        """
        if not self._positions:
            return

        # Get portfolio data from broker
        portfolio = await self.broker.get_portfolio()

        for pos_id, pos in list(self._positions.items()):
            if pos.status != TradeStatus.OPEN:
                continue

            try:
                await self._update_single_position(pos, portfolio)
            except Exception as e:
                logger.error(f"Error updating position {pos_id}: {e}")

    async def _update_single_position(self, pos: Position, portfolio: List[Dict]) -> None:
        """Update a single position with live data."""
        if pos.spread and pos.spread.long_leg and pos.spread.short_leg:
            # Get current Greeks for both legs
            long_greeks = await self.broker.get_option_greeks(
                pos.spread.symbol,
                pos.spread.expiration,
                pos.spread.long_leg.strike,
                pos.spread.right,
            )
            short_greeks = await self.broker.get_option_greeks(
                pos.spread.symbol,
                pos.spread.expiration,
                pos.spread.short_leg.strike,
                pos.spread.right,
            )

            # Update leg Greeks
            pos.spread.long_leg.delta = long_greeks.get("delta", 0)
            pos.spread.long_leg.theta = long_greeks.get("theta", 0)
            pos.spread.long_leg.vega = long_greeks.get("vega", 0)
            pos.spread.long_leg.iv = long_greeks.get("iv", 0)

            pos.spread.short_leg.delta = short_greeks.get("delta", 0)
            pos.spread.short_leg.theta = short_greeks.get("theta", 0)
            pos.spread.short_leg.vega = short_greeks.get("vega", 0)
            pos.spread.short_leg.iv = short_greeks.get("iv", 0)

            # Update net Greeks
            pos.spread.long_leg.dte = calculate_dte(pos.spread.expiration)
            pos.spread.short_leg.dte = pos.spread.long_leg.dte

            # Estimate current spread value from portfolio P&L or option bid/asks
            self._estimate_spread_value(pos)

    def _estimate_spread_value(self, pos: Position) -> None:
        """Estimate current spread value for P&L calculation."""
        if pos.spread and pos.spread.long_leg and pos.spread.short_leg:
            long_mid = pos.spread.long_leg.mid
            short_mid = pos.spread.short_leg.mid
            if long_mid > 0 and short_mid > 0:
                pos.current_value = long_mid - short_mid
                pos.unrealized_pnl = (pos.current_value - pos.entry_price) * pos.quantity * 100
                if pos.entry_price > 0:
                    pos.unrealized_pnl_pct = (pos.current_value - pos.entry_price) / pos.entry_price

    # ── Queries ──────────────────────────────────────────────────

    @property
    def open_positions(self) -> List[Position]:
        """Get all open positions."""
        return [p for p in self._positions.values() if p.status == TradeStatus.OPEN]

    @property
    def all_positions(self) -> List[Position]:
        """Get all tracked positions (open + pending)."""
        return list(self._positions.values())

    @property
    def trade_history(self) -> List[TradeRecord]:
        """Get completed trade history."""
        return self._trade_history

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a specific position by ID."""
        return self._positions.get(position_id)

    def get_account_stats(self) -> Dict[str, Any]:
        """Calculate aggregate account statistics from trade history."""
        if not self._trade_history:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
            }

        wins = [t for t in self._trade_history if t.realized_pnl > 0]
        losses = [t for t in self._trade_history if t.realized_pnl <= 0]

        total_wins = sum(t.realized_pnl for t in wins) if wins else 0
        total_losses = abs(sum(t.realized_pnl for t in losses)) if losses else 0

        return {
            "total_trades": len(self._trade_history),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(self._trade_history) if self._trade_history else 0,
            "avg_win": total_wins / len(wins) if wins else 0,
            "avg_loss": total_losses / len(losses) if losses else 0,
            "profit_factor": total_wins / total_losses if total_losses > 0 else float("inf"),
            "total_pnl": sum(t.realized_pnl for t in self._trade_history),
        }

    def get_monthly_pnl(self) -> float:
        """Get current month's realized P&L."""
        current_month = datetime.now().strftime("%Y-%m")
        return sum(
            t.realized_pnl for t in self._trade_history
            if t.exit_time and t.exit_time.startswith(current_month)
        )
