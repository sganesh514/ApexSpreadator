"""
ApexSpreadator Agent — Main Entry Point
Starts the trading agent loop and web dashboard.
"""
from core.broker_factory import get_broker
import asyncio
import signal
import sys
import os
import threading
from datetime import datetime, timedelta
import pandas as pd
import uvicorn
from core.data_loader import get_live_price, get_live_options_chain

from config import CONFIG, AgentConfig
from models import (
    AgentState, TradeStatus, ExitReason,
    AccountSnapshot, ScannerStatus
)
from core.strategy import StrategyEngine
from core.execution import ExecutionEngine
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from core.screener import ScreenerEngine
from intelligence.learning import LearningEngine
from intelligence.ollama_analyst import OllamaAnalyst
from intelligence.journal import TradeJournal
from dashboard.server import DashboardServer
from utils import (
    setup_logging, get_logger, is_market_hours, now_iso,
    trading_days_remaining_in_month, format_currency, format_pnl
)

logger = get_logger("Main")


class ApexSpreadatorAgent:
    """
    The main trading agent that orchestrates all components for ApexSpreadator.
    """

    def __init__(self, config: AgentConfig = None):
        self.config = config or CONFIG
        self.state = AgentState.STARTING
        self._paused = False

        # Initialize components
        # Initialize components using broker factory
        self.broker = get_broker(self.config.connection.broker_type, self.config)
        self.risk_manager = RiskManager(self.config)
        self.strategy = StrategyEngine(self.broker, self.config, self.risk_manager)
        self.execution = ExecutionEngine(self.broker, self.config)
        self.position_manager = PositionManager(self.broker, self.config)
        self.learning = LearningEngine(self.config)
        self.analyst = OllamaAnalyst(self.config)
        self.journal = TradeJournal(self.config)
        self.dashboard = DashboardServer(agent=self)

        # Account tracking
        self._account = AccountSnapshot(
            balance=self.config.account.starting_capital,
            equity=self.config.account.starting_capital,
            buying_power=self.config.account.starting_capital,
            month_start_balance=self.config.account.starting_capital,
            monthly_target=self.config.account.monthly_target,
        )

        # Scanner / Market structure status
        self._scanner_status = ScannerStatus()
        self._last_scan_time = None
        self._next_scan_time = None

    # ═══════════════════════════════════════════════════════════════
    # Main Loop
    # ═══════════════════════════════════════════════════════════════

    async def run(self):
        """Main agent loop."""
        logger.info("=" * 60)
        logger.info("  APEXSPREADATOR AGENT — Starting Up")
        logger.info("=" * 60)

        # Connect to IBKR
        connected = await self.broker.connect()
        if not connected:
            logger.error("Failed to connect to IBKR. Is TWS/Gateway running?")
            logger.info("Starting in DASHBOARD-ONLY mode. Connect to IBKR and restart.")
            self.state = AgentState.ERROR
            return

        self.state = AgentState.RUNNING

        # Check Ollama availability
        await self.analyst.check_availability()

        # Load and parse active zones from data/active_zones.json
        import json
        zones_path = "data/active_zones.json"
        if not os.path.exists(zones_path):
            logger.error(f"❌ Active zones file not found at {zones_path}. Please run the offline screener first: `python scripts/run_screener.py`")
            self.state = AgentState.ERROR
            return
        
        try:
            with open(zones_path, "r") as f:
                active_zones = json.load(f)
        except Exception as e:
            logger.error(f"❌ Failed to parse active zones file: {e}. Please run the offline screener again: `python scripts/run_screener.py`")
            self.state = AgentState.ERROR
            return
            
        candidates = list(active_zones.keys())
        if not candidates:
            logger.error(f"❌ Active zones file at {zones_path} contains no candidates.")
            self.state = AgentState.ERROR
            return
            
        self.config.strategy.underlyings = candidates
        logger.info(f"Watchlist initialized from active_zones.json: {', '.join(candidates)}")

        # Initialize the UnderlyingTracker instances explicitly using coordinates loaded from JSON
        from models import Zone
        for symbol, data in active_zones.items():
            tracker = self.strategy.get_tracker(symbol)
            tracker.bias = data.get("trend_status", "NEUTRAL")
            
            # Load demand zones
            tracker.demand_zones = []
            for dz in data.get("demand_zones", []):
                tracker.demand_zones.append(
                    Zone(
                        id=dz.get("id", ""),
                        type="DEMAND",
                        high=float(dz.get("high", 0.0)),
                        low=float(dz.get("low", 0.0)),
                        origin_candle_time=dz.get("origin_candle_time", ""),
                        is_active=dz.get("is_active", True)
                    )
                )
            
            # Load supply zones
            tracker.supply_zones = []
            for sz in data.get("supply_zones", []):
                tracker.supply_zones.append(
                    Zone(
                        id=sz.get("id", ""),
                        type="SUPPLY",
                        high=float(sz.get("high", 0.0)),
                        low=float(sz.get("low", 0.0)),
                        origin_candle_time=sz.get("origin_candle_time", ""),
                        is_active=sz.get("is_active", True)
                    )
                )
            
            # Load swing points
            if "swing_highs" in data:
                tracker.swing_highs = [(idx, val) for idx, val in data["swing_highs"]]
            if "swing_lows" in data:
                tracker.swing_lows = [(idx, val) for idx, val in data["swing_lows"]]
            
            # Load candles
            if "candles" in data:
                tracker.candles = data["candles"]
                
            logger.info(f"Initialized tracker for {symbol}: bias={tracker.bias}, demand_zones={len(tracker.demand_zones)}, supply_zones={len(tracker.supply_zones)}")

        # Refresh account data
        await self._refresh_account()

        logger.info(f"Account Balance: {format_currency(self._account.balance)}")
        logger.info(f"Monthly Target: {format_currency(self.config.account.monthly_target)}")
        logger.info(f"Mode: {'PAPER' if self.broker.is_paper else 'LIVE'}")
        logger.info(f"Watchlist: {', '.join(self.config.strategy.underlyings)}")
        logger.info("Agent is LIVE and ready.")
        logger.info("=" * 60)

        # Main loop
        scan_counter = 0
        position_counter = 0

        while self.state == AgentState.RUNNING:
            try:
                if not self.broker.is_connected():
                    logger.warning("Lost IBKR connection. Attempting reconnect...")
                    await self.broker.connect()
                    await asyncio.sleep(5)
                    continue

                if self._paused:
                    await asyncio.sleep(5)
                    continue

                # Check market hours
                in_hours = is_market_hours(
                    self.config.schedule.market_open,
                    self.config.schedule.market_close,
                )

                if in_hours:
                    # Position check — every minute
                    position_counter += 1
                    if position_counter >= (self.config.schedule.position_check_seconds // 5):
                        position_counter = 0
                        await self._check_positions()

                    # Scan — every 5 minutes
                    scan_counter += 1
                    if scan_counter >= (self.config.schedule.scan_interval_seconds // 5):
                        scan_counter = 0
                        await self._run_scan()

                    # Broadcast updates to dashboard
                    await self.dashboard.broadcast_full_update()
                else:
                    # Outside market hours
                    scan_counter = 0
                    position_counter = 0

                await asyncio.sleep(5)  # Base loop interval

            except KeyboardInterrupt:
                logger.info("Shutdown signal received")
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(10)

        await self._shutdown()

    async def _run_scan(self):
        """Execute a single scan cycle by fetching the latest live prices and feeding them to trackers."""
        self._scanner_status.is_scanning = True
        self._last_scan_time = now_iso()

        try:
            # Refresh account
            await self._refresh_account()

            for symbol in self.config.strategy.underlyings:
                price = await get_live_price(symbol, self.broker)
                if price <= 0:
                    continue
                
                # Fetch live/mid option IV if available from VIX or index
                vix_val = 18.0
                try:
                    vix_val = await get_live_price("^VIX", self.broker)
                    if vix_val <= 0:
                        vix_val = 18.0
                except Exception:
                    pass
                iv_val = vix_val / 100.0

                today_str = datetime.now().strftime("%Y-%m-%d")

                # Feed the latest price to the tracker (StrategyEngine.add_bar handles same-day vs new-day)
                opp = self.strategy.add_bar(
                    symbol=symbol,
                    open_p=price,
                    high_p=price,
                    low_p=price,
                    close_p=price,
                    volume=0.0,
                    timestamp=today_str,
                    iv=iv_val
                )

                if opp:
                    # Trade signal generated! Let's check entry approval
                    await self._try_enter_trade(opp)

        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)
        finally:
            self._scanner_status.is_scanning = False

    async def _try_enter_trade(self, opportunity):
        """Attempt to enter a trade on a zone retest opportunity."""
        should_enter, reason = self.strategy.should_enter(
            opportunity=opportunity,
            account=self._account,
            open_positions=self.position_manager.all_positions,
        )

        if not should_enter:
            logger.info(f"Skipping {opportunity.symbol} setup: {reason}")
            return

        # Calculate position size
        quantity = self.strategy.calculate_position_size(opportunity.spread, self._account)
        if quantity <= 0:
            logger.warning("Position size calculated as 0, skipping")
            return

        # Get pre-trade analysis from Ollama
        analysis = await self.analyst.analyze_pre_trade(opportunity)

        # Query options chain from broker to get real contracts if live option chain filtering is used
        options_chain = None
        try:
            dte = opportunity.spread.long_leg.dte
            options_chain = await get_live_options_chain(
                symbol=opportunity.symbol,
                broker=self.broker,
                right=opportunity.spread.right,
                min_dte=max(0, dte - 5),
                max_dte=dte + 5
            )
        except Exception as chain_err:
            logger.warning(f"Failed to fetch option chain from broker: {chain_err}")

        # Re-price using actual broker values if available
        if options_chain:
            # Let the selector select using actual option chain
            spread, status = self.strategy.selector.select_spread(
                symbol=opportunity.symbol,
                direction=opportunity.direction,
                underlying_price=opportunity.underlying_price,
                target_tp=opportunity.spread.short_leg.strike,
                target_sl=opportunity.invalidation_price,
                expiration=opportunity.spread.expiration,
                dte=dte,
                iv=opportunity.spread.long_leg.iv,
                options_chain=options_chain
            )
            if spread:
                opportunity.spread = spread
            else:
                logger.info(f"OptionsSelector rejected spread based on real broker pricing: {status}")
                return

        # Execute the trade
        order_id, fill_price = await self.execution.open_spread(opportunity, quantity)

        if order_id < 0:
            logger.error("Trade execution failed")
            return

        # Create and track position
        position = self.strategy.create_position(opportunity, quantity, order_id)
        position.spread.id = position.id
        self.position_manager.add_position(position)

        if fill_price > 0:
            self.position_manager.confirm_fill(position.id, fill_price)

        # Journal the entry
        self.journal.log_entry(opportunity, quantity, analysis)

        # Broadcast update
        await self.dashboard.broadcast("trade_opened", {
            "position_id": position.id,
            "symbol": opportunity.symbol,
            "long_strike": opportunity.spread.long_leg.strike,
            "short_strike": opportunity.spread.short_leg.strike,
        })
        logger.info(f"🚀 Trade opened: {opportunity.spread.description} x{quantity}")

    async def _check_positions(self):
        """Check all open positions for exit conditions."""
        await self.position_manager.update_positions()

        for position in self.position_manager.open_positions:
            # Query current underlying price
            underlying_price = await self.broker.get_underlying_price(position.spread.symbol)
            if underlying_price <= 0:
                continue

            should_exit, exit_reason, reason_text = self.strategy.check_exit_conditions(
                position, underlying_price
            )

            if should_exit:
                await self._close_position(position, exit_reason, underlying_price)

    async def _close_position(self, position, exit_reason: ExitReason, underlying_price: float):
        """Close a position and run post-trade analysis."""
        success, exit_price = await self.execution.close_spread(position)

        if not success:
            logger.error(f"Failed to close position {position.id}")
            return

        # Record the close
        trade_record = self.position_manager.close_position(
            position.id, exit_price, exit_reason, underlying_price
        )

        if not trade_record:
            return

        # Record P&L in risk manager
        self.risk_manager.record_realized_pnl(trade_record.realized_pnl, self._account.equity)

        # Learning analysis
        analysis = self.learning.analyze_trade(trade_record)

        # Ollama post-trade review
        post_analysis = await self.analyst.analyze_post_trade(trade_record)
        trade_record.agent_analysis = post_analysis

        # Journal the exit
        self.journal.log_exit(trade_record, post_analysis)

        # Broadcast
        await self.dashboard.broadcast("trade_closed", trade_record.to_dict())

    async def _refresh_account(self):
        """Refresh account data from broker."""
        try:
            summary = await self.broker.get_account_summary()

            if self.config.account.virtual_balance:
                stats = self.position_manager.get_account_stats()
                total_pnl = stats["total_pnl"]
                self._account.balance = self.config.account.starting_capital + total_pnl
                self._account.equity = self._account.balance
                self._account.buying_power = self._account.balance
            else:
                self._account.balance = summary.get("balance", 0)
                self._account.equity = summary.get("equity", 0)
                self._account.buying_power = summary.get("buying_power", 0)

            # Update stats
            stats = self.position_manager.get_account_stats()
            self._account.total_trades = stats["total_trades"]
            self._account.winning_trades = stats["winning_trades"]
            self._account.losing_trades = stats["losing_trades"]
            self._account.win_rate = stats["win_rate"]
            self._account.avg_win = stats["avg_win"]
            self._account.avg_loss = stats["avg_loss"]
            self._account.profit_factor = stats["profit_factor"]
            self._account.total_pnl = stats["total_pnl"]
            self._account.monthly_pnl = self.position_manager.get_monthly_pnl()
            self._account.open_positions_count = len(self.position_manager.open_positions)
            self._account.trading_days_remaining = trading_days_remaining_in_month()
            self._account.monthly_target = self.config.account.monthly_target

            if self._account.monthly_target > 0:
                self._account.monthly_progress_pct = (
                    self._account.monthly_pnl / self._account.monthly_target * 100
                )

            self._account.timestamp = now_iso()

        except Exception as e:
            logger.error(f"Error refreshing account: {e}")

    async def _shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down ApexSpreadator Agent...")
        self.state = AgentState.STOPPED
        await self.broker.disconnect()
        logger.info("Shutdown complete.")

    # ═══════════════════════════════════════════════════════════════
    # Dashboard API Interface
    # ═══════════════════════════════════════════════════════════════

    def get_account_data(self):
        return self._account.to_dict()

    def get_positions_data(self):
        return [p.to_dict() for p in self.position_manager.all_positions
                if p.status in (TradeStatus.OPEN, TradeStatus.PENDING)]

    def get_history_data(self):
        return [t.to_dict() for t in self.position_manager.trade_history]

    def get_market_structure_data(self):
        symbol = self.config.strategy.underlyings[0] if self.config.strategy.underlyings else "SPY"
        tracker = self.strategy.get_tracker(symbol)
        
        current_price = 0.0
        if tracker.candles:
            current_price = tracker.candles[-1]["close"]
            
        closest_zone, proximity = tracker.get_closest_zone_proximity(current_price)
        
        active_zones = []
        for zone in tracker.demand_zones + tracker.supply_zones:
            if zone.is_active:
                active_zones.append({
                    "id": zone.id,
                    "type": zone.type,
                    "low": zone.low,
                    "high": zone.high,
                    "origin_candle_time": zone.origin_candle_time
                })
                
        return {
            "bias": tracker.bias,
            "proximity_pct": proximity if closest_zone else None,
            "closest_zone": {
                "id": closest_zone.id,
                "type": closest_zone.type,
                "low": closest_zone.low,
                "high": closest_zone.high,
            } if closest_zone else None,
            "active_zones": active_zones,
            "risk_filter_logs": self.strategy.selector.risk_filter_logs
        }

    def get_stats_data(self):
        stats = self.position_manager.get_account_stats()
        if stats.get("profit_factor") == float("inf"):
            stats["profit_factor"] = "Infinity"
        return stats

    def get_journal_data(self):
        return self.journal.get_recent_entries(20)

    def get_risk_data(self):
        return self.risk_manager.get_risk_status()

    async def manual_close_position(self, position_id: str):
        """Close a position from the dashboard."""
        position = self.position_manager.get_position(position_id)
        if not position:
            return {"error": f"Position {position_id} not found"}

        if position.status != TradeStatus.OPEN:
            return {"error": f"Position {position_id} is not open (status: {position.status.value})"}

        logger.info(f"Manual close requested for {position_id}")
        underlying_price = await self.broker.get_underlying_price(position.spread.symbol)
        await self._close_position(position, ExitReason.MANUAL_CLOSE, underlying_price)
        return {"success": True, "position_id": position_id}

    def toggle_pause(self):
        """Pause or resume the agent."""
        self._paused = not self._paused
        state = "PAUSED" if self._paused else "RUNNING"
        logger.info(f"Agent {state}")
        return {"paused": self._paused, "state": state}

    def reset_circuit_breaker(self):
        """Reset the circuit breaker from the dashboard."""
        self.risk_manager.reset_circuit_breaker()
        return {"success": True, "message": "Circuit breaker reset"}


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

def main():
    """Start the ApexSpreadator Agent."""
    setup_logging()
    logger_main = get_logger("Startup")

    os.makedirs("data", exist_ok=True)

    import argparse
    parser = argparse.ArgumentParser(description="ApexSpreadator Agent")
    parser.add_argument(
        "--broker",
        type=str,
        default=CONFIG.connection.broker_type,
        help="Broker to switch to (ibkr or moomoo)"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode instead of paper/simulated"
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=CONFIG.dashboard.port,
        help="Port for running the FastAPI web dashboard"
    )
    args = parser.parse_args()

    # Apply options to configuration
    CONFIG.connection.broker_type = args.broker
    dashboard_port = args.dashboard_port

    if args.live:
        logger_main.warning("🔴 LIVE TRADING MODE — Real money at risk!")
    else:
        display_port = 7497 if args.broker.lower() == "ibkr" else 11111
        logger_main.info(f"📄 Paper trading mode (broker: {args.broker.upper()}, port {display_port})")

    # Create the agent
    agent = ApexSpreadatorAgent(CONFIG)

    # Start dashboard in a separate thread
    def run_dashboard():
        uvicorn.run(
            agent.dashboard.app,
            host=CONFIG.dashboard.host,
            port=dashboard_port,
            log_level="warning",
        )

    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()
    logger_main.info(f"🌐 Dashboard running at http://localhost:{dashboard_port}")

    # Run the agent
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger_main.info("Interrupted by user")
    except Exception as e:
        logger_main.error(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
