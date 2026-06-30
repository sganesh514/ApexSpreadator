"""
ApexSpreadator — Strategy Engine
Coordinates UnderlyingTracker and OptionsSelector to make entry decisions,
sizes positions, and checks chart-based exit conditions.
"""
from typing import List, Dict, Optional, Tuple, Any
import datetime
from config import AgentConfig
from models import Opportunity, Position, VerticalSpread, TradeStatus, ExitReason, AccountSnapshot
from core.broker_base import BrokerBase
from core.risk_manager import RiskManager
from core.underlying_tracker import UnderlyingTracker
from core.options_selector import OptionsSelector, BrokerDataError
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

    def ingest_and_detect(self, symbol: str, open_p: float, high_p: float, low_p: float, close_p: float, volume: float, timestamp: str, iv: float = 0.18) -> Optional[Dict[str, Any]]:
        """
        Ingest a new price bar and detect if it triggers a zone retest signal.
        Returns a raw signal dict if a retest occurs, else None.

        This method only handles candle ingestion and signal detection — it does
        NOT price or create spreads.  Use price_signal() to convert the raw
        signal into a priced Opportunity.
        """
        tracker = self.get_tracker(symbol)

        # Check if same day update
        if tracker.candles and tracker.candles[-1]["time"] == timestamp:
            last_candle = tracker.candles[-1]
            last_candle["close"] = close_p
            last_candle["high"] = max(last_candle["high"], high_p)
            last_candle["low"] = min(last_candle["low"], low_p)
            last_candle["volume"] = volume

            # Check zone invalidations on close
            tracker._check_zone_invalidations(close_p)

            # Check for retests
            signal = tracker._check_zone_retests(last_candle)
        else:
            signal = tracker.add_candle(open_p, high_p, low_p, close_p, volume, timestamp, iv)

        return signal

    def price_signal(
        self,
        signal: Dict[str, Any],
        timestamp: str,
        iv: float = 0.18,
        options_chain: Optional[Any] = None,
        is_backtesting: bool = False,
    ) -> Optional[Opportunity]:
        """
        Convert a raw zone retest signal into an actionable Opportunity by
        pricing a vertical debit spread.

        When ``options_chain`` is provided (live mode), uses real broker chain
        data for strike selection and per-contract IV.  When ``options_chain``
        is None, behaviour depends on ``is_backtesting``:
          - True  → generates a synthetic spread via Black-Scholes.
          - False → returns None (live mode requires real chain data).
        """
        zone = signal["zone"]
        direction = signal["direction"]
        current_price = signal["price"]
        symbol = signal["symbol"]

        tracker = self.get_tracker(symbol)

        # Calculate take-profit target (sold strike) from swing points
        target_tp = current_price
        if direction == "BULLISH":
            if tracker.swing_highs:
                target_tp = tracker.swing_highs[-1][1]
            else:
                target_tp = current_price * 1.05
        else:
            if tracker.swing_lows:
                target_tp = tracker.swing_lows[-1][1]
            else:
                target_tp = current_price * 0.95

        # Calculate invalidation point (stop loss level just outside the zone)
        if direction == "BULLISH":
            target_sl = zone.low - (zone.low * self.config.strategy.stop_buffer_pct)
        else:
            target_sl = zone.high + (zone.high * self.config.strategy.stop_buffer_pct)

        # Get expiration and DTE mapping based on config
        timeframe = self.config.strategy.default_timeframe
        dte = self.config.strategy.timeframe_dte_map.get(timeframe, 30)

        from utils import str_to_date
        bar_dt = str_to_date(timestamp)
        expiration_date = (bar_dt + datetime.timedelta(days=dte)).strftime("%Y%m%d")

        # Implied Volatility — prefer the explicitly passed IV (e.g. ATM IV
        # extracted from the broker's chain) over the candle's VIX proxy.
        iv_val = 0.18  # absolute backstop
        if len(tracker.candles) > 0 and "iv" in tracker.candles[-1]:
            candle_iv = tracker.candles[-1]["iv"]
            if candle_iv and candle_iv > 0.001:
                iv_val = candle_iv
        if iv > 0.001:
            iv_val = iv  # explicit override (e.g. per-symbol ATM IV)

        # Select option vertical spread and apply R:R check
        try:
            spread, status = self.selector.select_spread(
                symbol=symbol,
                direction=direction,
                underlying_price=current_price,
                target_tp=target_tp,
                target_sl=target_sl,
                expiration=expiration_date,
                dte=dte,
                iv=iv_val,
                options_chain=options_chain,
                is_backtesting=is_backtesting
            )
        except BrokerDataError as err:
            spread, status = None, str(err)

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

    def add_bar(self, symbol: str, open_p: float, high_p: float, low_p: float, close_p: float, volume: float, timestamp: str, iv: float = 0.18) -> Optional[Opportunity]:
        """
        Ingest a new price bar and check if it triggers a vertical spread opportunity.

        Backward-compatible convenience method that calls ingest_and_detect()
        then price_signal().  Used by the backtester where synthetic spreads
        are acceptable.  In live mode, callers should use ingest_and_detect()
        and price_signal() separately so they can fetch a real options chain
        from the broker between the two calls.
        """
        signal = self.ingest_and_detect(symbol, open_p, high_p, low_p, close_p, volume, timestamp, iv)
        if not signal:
            return None
        return self.price_signal(
            signal=signal,
            timestamp=timestamp,
            iv=iv,
            options_chain=None,
            is_backtesting=(self.broker is None)
        )

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
        
        if len(closes) < 10:
            return False, "Not enough data for 10 EMA filter"
            
        # Calculate 10 EMA: SMA of first 10 as start, then smooth
        ema_10 = sum(closes[:10]) / 10.0
        alpha = 2.0 / (10 + 1)
        for val in closes[10:]:
            ema_10 = alpha * val + (1.0 - alpha) * ema_10
            
        current_price = closes[-1]
        
        if opportunity.direction == "BULLISH":
            if current_price < ema_10:
                msg = "Rejected: Bullish debit spread counter-trend (Price < 10 EMA)"
                logger.info(msg)
                return False, msg
        elif opportunity.direction == "BEARISH":
            if current_price > ema_10:
                msg = "Rejected: Bearish debit spread counter-trend (Price > 10 EMA)"
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
            passed, reason = self.risk.pre_trade_check(spread, account, open_positions)
            if not passed:
                return False, reason

        # Check if account has enough equity to execute minimum contract size safely
        qty = self.calculate_position_size(spread, account)
        if qty <= 0:
            msg = "Rejected: Account equity insufficient to execute minimum contract size safely."
            logger.info(msg)
            return False, msg

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
        
        if contracts == 0:
            current_equity = account.equity or account.balance or self.config.account.starting_capital
            absolute_ceiling = current_equity * 0.035
            if max_loss_per_contract <= absolute_ceiling:
                return 1
            else:
                return 0
                
        return contracts

    def check_exit_conditions(
        self,
        position: Position,
        current_underlying_price: float,
        current_simulation_date: Optional[datetime.date] = None
    ) -> Tuple[bool, ExitReason, str]:
        """
        Check if a position should be exited.
        Priority: 1) Premium stop-loss  2) Time exit  3) TP/SL on underlying.
        """
        if position.status != TradeStatus.OPEN:
            return False, ExitReason.MANUAL_CLOSE, "Position not open"

        spread = position.spread
        if not spread:
            return False, ExitReason.MANUAL_CLOSE, "No spread defined for position"

        # 2. Time Exit — deterministic absolute date comparison
        if current_simulation_date and spread.expiration_date:
            if current_simulation_date >= spread.expiration_date:
                msg = f"⏰ TIME EXIT on {position.id} ({spread.symbol}): Simulation date {current_simulation_date} >= expiry {spread.expiration_date}"
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
