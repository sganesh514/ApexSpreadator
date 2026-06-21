"""
ApexSpreadator — Strategy Engine
Coordinates UnderlyingTracker and OptionsSelector to make entry decisions,
sizes positions, and checks chart-based exit conditions.
"""
from typing import List, Dict, Optional, Tuple, Any
from config import AgentConfig
from models import Opportunity, Position, VerticalSpread, TradeStatus, ExitReason, AccountSnapshot
from core.broker_base import BrokerBase
from core.risk_manager import RiskManager
from core.underlying_tracker import UnderlyingTracker
from core.options_selector import OptionsSelector
from utils import get_logger, generate_id, now_iso, is_market_hours, format_currency, format_pnl

logger = get_logger("Strategy")


class StrategyEngine:
    """
    Coordinates underlying price action analysis and options selection.
    """

    def __init__(self, broker: Optional[BrokerBase], config: AgentConfig, risk_manager: Optional[RiskManager] = None):
        self.broker = broker
        self.config = config
        self.risk = risk_manager
        self.selector = OptionsSelector(min_rr_threshold=config.strategy.min_rr_threshold)
        self.trackers: Dict[str, UnderlyingTracker] = {}

    def get_tracker(self, symbol: str) -> UnderlyingTracker:
        """Get or create the UnderlyingTracker for a symbol."""
        if symbol not in self.trackers:
            self.trackers[symbol] = UnderlyingTracker(
                symbol=symbol,
                fractal_window=self.config.strategy.fractal_window
            )
        return self.trackers[symbol]

    def add_bar(self, symbol: str, open_p: float, high_p: float, low_p: float, close_p: float, volume: float, timestamp: str, iv: float = 0.18) -> Optional[Opportunity]:
        """
        Ingest a new price bar and check if it triggers a vertical spread opportunity.
        """
        tracker = self.get_tracker(symbol)
        signal = tracker.add_candle(open_p, high_p, low_p, close_p, volume, timestamp, iv)

        if not signal:
            return None

        # ── Retest signal detected, translate to options spread ──
        zone = signal["zone"]
        direction = signal["direction"]
        current_price = signal["price"]

        # Calculate take-profit target (sold strike) from swing points
        # If bullish retest: target the last swing high.
        # If bearish retest: target the last swing low.
        target_tp = current_price
        if direction == "BULLISH":
            if tracker.swing_highs:
                target_tp = tracker.swing_highs[-1][1]
            else:
                target_tp = current_price * 1.05  # fallback +5%
        else:
            if tracker.swing_lows:
                target_tp = tracker.swing_lows[-1][1]
            else:
                target_tp = current_price * 0.95  # fallback -5%

        # Calculate invalidation point (stop loss level just outside the zone)
        if direction == "BULLISH":
            target_sl = zone.low - (zone.low * self.config.strategy.stop_buffer_pct)
        else:
            target_sl = zone.high + (zone.high * self.config.strategy.stop_buffer_pct)

        # Get expiration and DTE mapping based on config
        timeframe = self.config.strategy.default_timeframe
        dte = self.config.strategy.timeframe_dte_map.get(timeframe, 30)
        
        # In a backtest or live, we determine expiration based on current time + dte
        # For mock backtester we just use a generic expiration date string
        # Let's mock a standard expiration format like YYYYMMDD
        import datetime
        expiration_date = (datetime.datetime.now() + datetime.timedelta(days=dte)).strftime("%Y%m%d")

        # Implied Volatility (IV)
        # In live we query broker, in backtesting we pass it or fallback to the candle's VIX metric
        iv_val = 0.18
        if len(tracker.candles) > 0 and "iv" in tracker.candles[-1]:
            iv_val = tracker.candles[-1]["iv"]
        elif self.broker:
            # Query broker for IV if connected
            pass

        # Select option vertical spread and apply R:R check
        spread, status = self.selector.select_spread(
            symbol=symbol,
            direction=direction,
            underlying_price=current_price,
            target_tp=target_tp,
            target_sl=target_sl,
            expiration=expiration_date,
            dte=dte,
            iv=iv_val,
            options_chain=None  # Can be passed in live mode
        )

        if not spread:
            logger.info(f"⚠️ [{symbol}] Retest signal at {current_price:.2f} did not result in an actionable spread: {status}")
            return None

        # Create Opportunity
        opp = Opportunity(
            symbol=symbol,
            zone=zone,
            direction=direction,
            underlying_price=current_price,
            spread=spread,
            timestamp=now_iso(),
            is_actionable=True,
            invalidation_price=target_sl
        )

        return opp

    def should_enter(
        self,
        opportunity: Opportunity,
        account: AccountSnapshot,
        open_positions: List[Position],
    ) -> Tuple[bool, str]:
        """
        Evaluate portfolio risk and other parameters to decide if we should enter.
        """
        symbol = opportunity.symbol
        spread = opportunity.spread

        # Enforce 20-period EMA trend filter
        tracker = self.get_tracker(symbol)
        closes = [c["close"] for c in tracker.candles]
        
        if len(closes) < 20:
            return False, "Not enough data for 20 EMA filter"
            
        # Calculate 20 EMA: SMA of first 20 as start, then smooth
        ema_20 = sum(closes[:20]) / 20.0
        alpha = 2.0 / (20 + 1)
        for val in closes[20:]:
            ema_20 = alpha * val + (1.0 - alpha) * ema_20
            
        current_price = closes[-1]
        
        if opportunity.direction == "BULLISH":
            if current_price < ema_20:
                msg = "Rejected: Bullish debit spread counter-trend (Price < 20 EMA)"
                logger.info(msg)
                return False, msg
        elif opportunity.direction == "BEARISH":
            if current_price > ema_20:
                msg = "Rejected: Bearish debit spread counter-trend (Price > 20 EMA)"
                logger.info(msg)
                return False, msg

        # Check market hours (skip in backtest where broker is None)
        if self.broker is not None and not is_market_hours(self.config.schedule.market_open, self.config.schedule.market_close):
            return False, "Outside regular market hours"

        # Check max concurrent positions
        active = [p for p in open_positions if p.status in (TradeStatus.OPEN, TradeStatus.PENDING)]
        if len(active) >= self.config.risk.max_concurrent_positions:
            return False, f"Max positions reached ({len(active)}/{self.config.risk.max_concurrent_positions})"

        # Check duplicate position in same underlying
        active_symbols = [p.spread.symbol for p in active if p.spread]
        if symbol in active_symbols:
            return False, f"Already have position in {symbol}"

        # Run risk manager checks if present
        if self.risk is not None:
            # Re-map vertical spread checks if needed
            pass

        logger.info(f"✅ Entry approved for {spread.description} | Opportunity R:R {spread.rr_ratio:.2f}")
        return True, "All checks passed"

    def calculate_position_size(self, spread: VerticalSpread, account: AccountSnapshot) -> int:
        """
        Calculate contracts to trade based on max capital at risk (2% of account balance).
        Since vertical spreads are debit spreads, the maximum loss is the net debit paid.
        """
        allowed_portfolio_loss = account.balance * self.config.risk.max_risk_per_trade
        max_loss_per_contract = spread.net_debit * 100.0

        if max_loss_per_contract <= 0:
            return 1

        contracts = int(allowed_portfolio_loss / max_loss_per_contract)
        return max(1, contracts)

    def check_exit_conditions(
        self,
        position: Position,
        current_underlying_price: float,
    ) -> Tuple[bool, ExitReason, str]:
        """
        Check if a position has hit its take profit (sold strike) or stop loss (invalidation point)
        entirely based on the underlying price.
        """
        if position.status != TradeStatus.OPEN:
            return False, ExitReason.MANUAL_CLOSE, "Position not open"

        spread = position.spread
        if not spread:
            return False, ExitReason.MANUAL_CLOSE, "No spread defined for position"

        # Check DTE expiration (force close at 0 DTE or 1 DTE)
        if spread.long_leg and spread.long_leg.dte <= 0:
            msg = f"⏰ DTE Exit on {position.id} ({spread.symbol}): Option expired."
            logger.info(msg)
            return True, ExitReason.TIME_EXIT, msg

        is_call = (spread.right == "C")
        tp_level = position.take_profit_price
        sl_level = position.invalidation_price

        # ── 1. Take Profit Check (Underlying touches or crosses Sold Strike) ──
        if is_call:
            if current_underlying_price >= tp_level:
                msg = f"🎯 TAKE PROFIT hit on {spread.symbol}: Price {current_underlying_price:.2f} reached target strike {tp_level:.2f}"
                logger.info(msg)
                return True, ExitReason.PROFIT_TARGET, msg
        else:
            if current_underlying_price <= tp_level:
                msg = f"🎯 TAKE PROFIT hit on {spread.symbol}: Price {current_underlying_price:.2f} reached target strike {tp_level:.2f}"
                logger.info(msg)
                return True, ExitReason.PROFIT_TARGET, msg

        # ── 2. Stop Loss Check (Underlying closes past Invalidation Point) ──
        if is_call:
            if current_underlying_price < sl_level:
                msg = f"🛑 STOP LOSS hit on {spread.symbol}: Price {current_underlying_price:.2f} invalidated below support level {sl_level:.2f}"
                logger.warning(msg)
                return True, ExitReason.STOP_LOSS, msg
        else:
            if current_underlying_price > sl_level:
                msg = f"🛑 STOP LOSS hit on {spread.symbol}: Price {current_underlying_price:.2f} invalidated above resistance level {sl_level:.2f}"
                logger.warning(msg)
                return True, ExitReason.STOP_LOSS, msg

        return False, ExitReason.MANUAL_CLOSE, "No exit condition met"

    def create_position(self, opportunity: Opportunity, quantity: int, order_id: int) -> Position:
        """Create a Position object from an approved opportunity."""
        # Consume the zone so it's not traded again
        if opportunity.zone:
            opportunity.zone.is_active = False
            
        spread = opportunity.spread
        
        # Calculate exit targets on the chart
        # Take Profit is the sold strike of the short leg
        tp_price = spread.short_leg.strike if spread.short_leg else opportunity.underlying_price
        
        # Invalidation Stop Loss level is the low of the demand zone or high of supply zone
        sl_price = opportunity.zone.low if opportunity.direction == "BULLISH" else opportunity.zone.high

        return Position(
            id=generate_id("POS"),
            spread=spread,
            status=TradeStatus.PENDING,
            entry_time=now_iso(),
            entry_price=spread.net_debit,
            quantity=quantity,
            current_value=spread.net_debit,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
            underlying_price_at_entry=opportunity.underlying_price,
            take_profit_price=tp_price,
            invalidation_price=sl_price,
            entry_order_id=order_id
        )
