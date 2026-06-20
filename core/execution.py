"""
ApexSpreadator — Execution Engine
Handles order construction and placement for Vertical Debit Spreads.
"""
import asyncio
from typing import Optional, Tuple

from config import AgentConfig
from models import Position, Opportunity, TradeStatus
from core.broker_base import BrokerBase
from utils import get_logger, format_currency

logger = get_logger("Execution")


class ExecutionEngine:
    """
    Handles the mechanics of placing and managing orders.
    - Constructs BAG combo orders for vertical debit spreads
    - Places limit orders at net debit mid-price
    - Auto-adjusts limit price if not filled
    - Handles close orders
    """

    def __init__(self, broker: BrokerBase, config: AgentConfig):
        self.broker = broker
        self.config = config

    async def open_spread(
        self,
        opportunity: Opportunity,
        quantity: int,
    ) -> Tuple[int, float]:
        """
        Open a vertical spread position.
        Returns (order_id, limit_price).
        Returns (-1, 0) on failure.
        """
        spread = opportunity.spread
        if not spread.long_leg or not spread.short_leg:
            logger.error("Cannot open spread: missing legs")
            return -1, 0.0

        # Calculate limit price at mid/debit paid
        limit_price = spread.net_debit

        logger.info(
            f"🔄 Opening vertical spread: {spread.description} | "
            f"Qty: {quantity} | Limit: {format_currency(limit_price)}"
        )

        order_id = await self.broker.place_vertical_spread(
            symbol=spread.symbol,
            long_strike=spread.long_leg.strike,
            short_strike=spread.short_leg.strike,
            right=spread.right,
            expiration=spread.expiration,
            quantity=quantity,
            limit_price=limit_price,
            action="BUY",
        )

        if order_id < 0:
            logger.error("Failed to place open order")
            return -1, 0.0

        # Monitor for fill with auto-adjustment
        filled, fill_price = await self._monitor_fill(order_id, limit_price)
        if filled:
            return order_id, fill_price

        return order_id, limit_price

    async def close_spread(self, position: Position) -> Tuple[bool, float]:
        """
        Close an open vertical spread position.
        Returns (success, exit_price).
        """
        spread = position.spread
        if not spread or not spread.long_leg or not spread.short_leg:
            logger.error(f"Cannot close {position.id}: missing legs")
            return False, 0.0

        # Estimate close price from current values
        limit_price = position.current_value if position.current_value > 0 else position.entry_price

        logger.info(
            f"🔄 Closing position {position.id}: {spread.description} | "
            f"Qty: {position.quantity} | Limit: {format_currency(limit_price)}"
        )

        order_id = await self.broker.place_vertical_spread(
            symbol=spread.symbol,
            long_strike=spread.long_leg.strike,
            short_strike=spread.short_leg.strike,
            right=spread.right,
            expiration=spread.expiration,
            quantity=position.quantity,
            limit_price=limit_price,
            action="SELL",
        )

        if order_id < 0:
            logger.error(f"Failed to place close order for {position.id}")
            return False, 0.0

        position.exit_order_id = order_id
        position.status = TradeStatus.CLOSING

        # Monitor for fill
        filled, fill_price = await self._monitor_fill(order_id, limit_price)
        return filled, fill_price

    async def _monitor_fill(
        self,
        order_id: int,
        initial_price: float,
    ) -> Tuple[bool, float]:
        """
        Monitor an order for fill, adjusting price if needed.
        Returns (filled, fill_price).
        """
        timeout = self.config.schedule.order_fill_timeout_seconds
        adjustment = self.config.schedule.order_price_adjustment
        max_adjustments = self.config.schedule.max_order_adjustments
        current_price = initial_price
        adjustments_made = 0

        logger.debug(f"Monitoring order #{order_id} for fill...")

        # Check every 10 seconds
        check_interval = 10
        elapsed = 0

        while elapsed < timeout * (max_adjustments + 1):
            await self.broker.sleep(check_interval)
            elapsed += check_interval

            status = await self.broker.get_order_status(order_id)

            if status["status"] == "Filled":
                fill_price = status["avg_fill_price"]
                logger.info(f"✅ Order #{order_id} filled at {format_currency(fill_price)}")
                return True, fill_price

            if status["status"] in ("Cancelled", "Error"):
                logger.error(f"❌ Order #{order_id} {status['status']}")
                return False, 0.0

            # Check if we should adjust price
            if elapsed >= timeout and adjustments_made < max_adjustments:
                current_price += adjustment
                adjustments_made += 1
                logger.info(
                    f"⏳ Order #{order_id} not filled, adjusting price to "
                    f"{format_currency(current_price)} (adjustment {adjustments_made}/{max_adjustments})"
                )
                await self.broker.modify_order_price(order_id, current_price)
                elapsed = 0  # Reset timer for next adjustment window

        # Max adjustments exhausted, cancel order
        logger.warning(f"⚠️ Order #{order_id} not filled after {max_adjustments} adjustments. Cancelling.")
        await self.broker.cancel_order(order_id)
        return False, 0.0
