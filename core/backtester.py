"""
ApexSpreadator — Options Backtesting Engine
Simulates the performance of market structure vertical spreads on historical data.
"""
import os
import sys
import math
import copy
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd

# Add parent dir to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from models import (
    Opportunity, Position, VerticalSpread, OptionLeg, TradeStatus,
    AccountSnapshot, ExitReason
)
from core.strategy import StrategyEngine
from utils import black_scholes_call, black_scholes_put, format_pnl, get_logger

logger = get_logger("Backtester")


class BacktestPosition(Position):
    """Simulated trade position for backtester."""
    _counter = 0

    def __init__(
        self,
        symbol: str,
        long_strike: float,
        short_strike: float,
        right: str,
        qty: int,
        entry_date: str,
        entry_price: float,
        underlying_price: float,
        take_profit_price: float,
        invalidation_price: float,
        dte: int = 30,
        expiration: str = ""
    ):
        BacktestPosition._counter += 1
        
        # Build OptionLeg objects
        long_leg = OptionLeg(symbol=symbol, strike=long_strike, right=right, action="BUY", dte=dte)
        short_leg = OptionLeg(symbol=symbol, strike=short_strike, right=right, action="SELL", dte=dte)
        
        # Build VerticalSpread object
        width = abs(long_strike - short_strike)
        risk = entry_price
        reward = width - entry_price
        rr_ratio = reward / risk if risk > 0 else 0.0

        spread_obj = VerticalSpread(
            id=f"SPD_{BacktestPosition._counter:05d}",
            symbol=symbol,
            expiration=expiration,
            right=right,
            long_leg=long_leg,
            short_leg=short_leg,
            quantity=qty,
            net_debit=entry_price,
            current_value=entry_price,
            risk=risk,
            reward=reward,
            rr_ratio=rr_ratio
        )

        super().__init__(
            id=f"POS_{BacktestPosition._counter:05d}",
            spread=spread_obj,
            status=TradeStatus.OPEN,
            entry_time=entry_date,
            entry_price=entry_price,
            quantity=qty,
            current_value=entry_price,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
            underlying_price_at_entry=underlying_price,
            take_profit_price=take_profit_price,
            invalidation_price=invalidation_price
        )
        self.entry_date = entry_date


class OptionsBacktester:
    """
    Historical backtest simulation runner for Vertical spreads.
    """

    def __init__(self, start_capital: float = 25000.0):
        self.start_capital = start_capital
        self.capital = start_capital
        self.equity = start_capital
        self.positions: List[BacktestPosition] = []
        self.trade_history: List[Dict[str, Any]] = []
        self.equity_curve: List[Tuple[str, float]] = []
        self.monthly_pnl: Dict[str, float] = {}
        
        # Initialize strategy in backtest mode
        self.strategy = StrategyEngine(broker=None, config=CONFIG, risk_manager=None)
        
        pass


    def run_backtest(self, df_data: pd.DataFrame, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run backtest simulation.
        """
        logger.info(f"Starting historical backtest with starting capital of ${self.start_capital:,.2f}...")
        
        self.capital = self.start_capital
        self.equity = self.start_capital
        self.positions = []
        self.trade_history = []
        self.equity_curve = []
        self.monthly_pnl = {}
        self.strategy.selector.risk_filter_logs = []  # Reset selector logs

        # Determine timeframe and intraday status
        timeframe = CONFIG.strategy.default_timeframe.strip().lower()
        is_intraday = timeframe in ["15m", "1h", "60m"]

        # Group records by Date and filter market hours
        df_data = df_data.copy()
        if is_intraday:
            # Convert UTC index to US/Eastern timezone representation
            datetimes_et = pd.to_datetime(df_data["Date"], utc=True).dt.tz_convert("US/Eastern")
            df_data["Date"] = datetimes_et.dt.strftime("%Y-%m-%d %H:%M:%S")
            
            # Enforce standard market hours (09:30 AM to 04:00 PM Eastern Time)
            import datetime
            start_time = datetime.time(9, 30)
            end_time = datetime.time(16, 0)
            
            market_hours_mask = (datetimes_et.dt.time >= start_time) & (datetimes_et.dt.time <= end_time)
            
            before_filter = len(df_data)
            df_data = df_data[market_hours_mask].copy()
            after_filter = len(df_data)
            logger.info(f"Filtered standard market hours (09:30 AM - 04:00 PM ET). Retained {after_filter} of {before_filter} bars.")
        else:
            df_data["Date"] = pd.to_datetime(df_data["Date"], utc=True).dt.strftime("%Y-%m-%d")
        
        # Calculate annualized Historical Volatility (HV) for each symbol depending on timeframe
        df_data = df_data.sort_values(["Symbol", "Date"])
        df_data["Returns"] = df_data.groupby("Symbol")["Close"].pct_change()
        
        if timeframe == "15m":
            window_size = 520  # 20 days of 15m bars
            ann_factor = (252 * 26) ** 0.5
        elif timeframe in ["1h", "60m"]:
            window_size = 140  # 20 days of hourly bars
            ann_factor = (252 * 7) ** 0.5
        else:
            window_size = 20
            ann_factor = 252 ** 0.5
            
        df_data["HV"] = df_data.groupby("Symbol")["Returns"].transform(lambda x: x.rolling(window=window_size).std() * ann_factor)
        df_data["HV"] = df_data["HV"].fillna(0.20)
        
        df_data = df_data.sort_values("Date")
        dates = df_data["Date"].unique()
        symbols = df_data["Symbol"].unique()
        
        self._last_screen_time = None
        # Instantiate UniverseManager and filter watchlist
        from core.universe_manager import UniverseManager
        universe_manager = UniverseManager(CONFIG)
        target_universe = universe_manager.get_universe()
        symbols_set = set(df_data["Symbol"].unique())
        self.active_watchlist = [sym for sym in target_universe if sym in symbols_set]
        logger.info(f"Filtered universe to {len(self.active_watchlist)} symbols based on config.")

        
        # Build lookup table for rapid row access
        lookup: Dict[str, Dict[str, Dict[str, Any]]] = {s: {} for s in symbols}
        for _, row in df_data.iterrows():
            lookup[row["Symbol"]][row["Date"]] = {
                "open": row["Open"],
                "high": row["High"],
                "low": row["Low"],
                "close": row["Close"],
                "volume": row["Volume"],
                "vix": row.get("VIX", 18.0),
                "iv": row["HV"]
            }

        sorted_dates = sorted(list(dates))
        total_days = len(sorted_dates)
        
        # Keep track of calendar date to decrement DTE once per day
        last_calendar_day = None
        
        # Main simulation daily/intraday loop
        for day_idx, date_str in enumerate(sorted_dates):
            # Enforce strict market hours filter check inside the loop
            if is_intraday:
                try:
                    import datetime as dt_mod
                    dt_local = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    start_time = dt_mod.time(9, 30)
                    end_time = dt_mod.time(16, 0)
                    
                    if not (start_time <= dt_local.time() <= end_time):
                        continue
                except Exception:
                    pass

            current_calendar_day = date_str[:10]
            day_changed = (current_calendar_day != last_calendar_day)
            last_calendar_day = current_calendar_day
            
            # Print progress
            if day_idx % max(1, int(total_days / 10)) == 0 or day_idx == total_days - 1:
                pct = (day_idx / (total_days - 1)) * 100 if total_days > 1 else 100.0
                print(f"  [{pct:5.1f}%] Date: {date_str} | Equity: ${self.equity:,.0f} | Cash: ${self.capital:,.0f} | Open: {len(self.positions)}")

            # Assemble simulated Account Snapshot
            current_snapshot = AccountSnapshot(
                balance=self.capital,
                equity=self.equity,
                buying_power=self.capital,
                month_start_balance=self.start_capital
            )

            # ── 1. Update and check exits on existing positions ──
            active_positions = []
            for pos in self.positions:
                rec = lookup.get(pos.spread.symbol, {}).get(date_str)
                if not rec:
                    active_positions.append(pos)
                    continue

                stock_price = rec["close"]
                iv = rec["iv"]

                # Decrement DTE only once per calendar day
                if day_changed:
                    if pos.spread.long_leg:
                        pos.spread.long_leg.dte = max(0, pos.spread.long_leg.dte - 1)
                    if pos.spread.short_leg:
                        pos.spread.short_leg.dte = max(0, pos.spread.short_leg.dte - 1)

                # Price legs via Black-Scholes
                is_call = (pos.spread.right == "C")
                long_dte = pos.spread.long_leg.dte
                short_dte = pos.spread.short_leg.dte

                if is_call:
                    long_price, _, _, _ = black_scholes_call(stock_price, pos.spread.long_leg.strike, long_dte / 365.0, 0.04, iv)
                    short_price, _, _, _ = black_scholes_call(stock_price, pos.spread.short_leg.strike, short_dte / 365.0, 0.04, iv)
                else:
                    long_price, _, _, _ = black_scholes_put(stock_price, pos.spread.long_leg.strike, long_dte / 365.0, 0.04, iv)
                    short_price, _, _, _ = black_scholes_put(stock_price, pos.spread.short_leg.strike, short_dte / 365.0, 0.04, iv)

                pos.current_value = long_price - short_price
                pos.unrealized_pnl = (pos.current_value - pos.entry_price) * pos.quantity * 100.0
                pos.unrealized_pnl_pct = (pos.current_value - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0.0

                # Check Hard Expiry Circuit Breaker (No Ghost Tracking)
                import datetime as dt_mod
                
                is_expired = False
                exp_str = pos.spread.expiration
                if exp_str:
                    try:
                        exp_date = datetime.strptime(exp_str, "%Y-%m-%d" if "-" in exp_str else "%Y%m%d").date()
                        
                        if " " in date_str:
                            dt_et = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                            
                            # Force close if past expiration date, or if it is expiration day and time is >= 2:00 PM ET
                            if dt_et.date() > exp_date:
                                is_expired = True
                            elif dt_et.date() == exp_date and dt_et.time() >= dt_mod.time(14, 0):
                                is_expired = True
                        else:
                            # Daily bar comparison
                            dt_et_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                            if dt_et_date >= exp_date:
                                is_expired = True
                    except Exception as exp_err:
                        logger.error(f"Error parsing expiration for circuit breaker: {exp_err}")
                
                if is_expired:
                    # Force close the option spread at standard intrinsic value on expiration day
                    width = abs(pos.spread.long_leg.strike - pos.spread.short_leg.strike)
                    long_strike = pos.spread.long_leg.strike
                    short_strike = pos.spread.short_leg.strike
                    
                    if is_call:
                        long_val = max(0.0, stock_price - long_strike)
                        short_val = max(0.0, stock_price - short_strike)
                    else:
                        long_val = max(0.0, long_strike - stock_price)
                        short_val = max(0.0, short_strike - stock_price)
                        
                    pos.current_value = long_val - short_val
                    pos.unrealized_pnl = (pos.current_value - pos.entry_price) * pos.quantity * 100.0
                    pos.unrealized_pnl_pct = (pos.current_value - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0.0
                    
                    should_exit = True
                    exit_reason = ExitReason.TIME_EXIT
                    msg = f"⏰ Hard Expiry Circuit Breaker on {pos.id} ({pos.spread.symbol}): Option expired/closing before market close."
                else:
                    # Check standard exit conditions
                    should_exit, exit_reason, msg = self.strategy.check_exit_conditions(pos, current_underlying_price=stock_price)

                if should_exit:
                    realized_cash = pos.current_value * pos.quantity * 100.0
                    self.capital += realized_cash

                    # Track monthly P&L
                    month_key = date_str[:7]
                    self.monthly_pnl[month_key] = self.monthly_pnl.get(month_key, 0.0) + pos.unrealized_pnl

                    # Calculate holding days using standard 24-hour day math
                    try:
                        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d %H:%M:%S" if " " in pos.entry_date else "%Y-%m-%d")
                        exit_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S" if " " in date_str else "%Y-%m-%d")
                        holding_days_val = int((exit_dt - entry_dt).total_seconds() / (3600 * 24))
                        holding_days_val = max(0, holding_days_val)
                    except Exception:
                        holding_days_val = 0

                    self.trade_history.append({
                        "id": pos.id,
                        "symbol": pos.spread.symbol,
                        "long_strike": pos.spread.long_leg.strike,
                        "short_strike": pos.spread.short_leg.strike,
                        "right": pos.spread.right,
                        "qty": pos.quantity,
                        "entry_date": pos.entry_date,
                        "exit_date": date_str,
                        "entry_price": round(pos.entry_price, 4),
                        "exit_price": round(pos.current_value, 4),
                        "pnl": round(pos.unrealized_pnl, 2),
                        "pnl_pct": round(pos.unrealized_pnl_pct, 4),
                        "holding_days": holding_days_val,
                        "reason": exit_reason.value,
                        "expiration": pos.spread.expiration
                    })
                    logger.info(msg)
                else:
                    active_positions.append(pos)

            self.positions = active_positions

            # ── 2. Scan for new entries via Strategy Engine ──
            # The active_watchlist is static and set to all symbols in the dataset

            # Feed daily bars to ALL trackers to build zone history and indicators
            opportunities = {}
            for sym in self.active_watchlist:
                rec = lookup.get(sym, {}).get(date_str)
                if not rec:
                    continue

                opp = self.strategy.add_bar(
                    symbol=sym,
                    open_p=rec["open"],
                    high_p=rec["high"],
                    low_p=rec["low"],
                    close_p=rec["close"],
                    volume=rec["volume"],
                    timestamp=date_str,
                    iv=rec["iv"]
                )
                if opp:
                    opportunities[sym] = opp

            # Then, scan for new entries only on symbols in the active watchlist
            held_symbols = {p.spread.symbol for p in self.positions if p.spread}

            for sym in self.active_watchlist:
                if sym in held_symbols:
                    continue
                if len(self.positions) >= config.get("max_concurrent_positions", 4):
                    continue

                opp = opportunities.get(sym)
                if opp:
                    # Retest detected and options selection approved. Check portfolio limits
                    should_enter, reason = self.strategy.should_enter(opp, current_snapshot, self.positions)

                    if should_enter:
                        qty = self.strategy.calculate_position_size(opp.spread, current_snapshot)
                        cost = opp.spread.net_debit * qty * 100.0

                        if qty > 0 and self.capital >= cost:
                            self.capital -= cost
                            
                            new_pos = BacktestPosition(
                                symbol=sym,
                                long_strike=opp.spread.long_leg.strike,
                                short_strike=opp.spread.short_leg.strike,
                                right=opp.spread.right,
                                qty=qty,
                                entry_date=date_str,
                                entry_price=opp.spread.net_debit,
                                underlying_price=opp.underlying_price,
                                take_profit_price=opp.spread.short_leg.strike,
                                invalidation_price=opp.invalidation_price,
                                dte=opp.spread.long_leg.dte if (opp.spread and opp.spread.long_leg) else config.get("dte", 30),
                                expiration=opp.spread.expiration if opp.spread else ""
                            )
                            self.positions.append(new_pos)
                            logger.info(f"📥 [{sym}] Entered Vertical Spread at {date_str} x{qty} spreads. Debit: ${opp.spread.net_debit:.2f} per spread.")


            # ── 3. End-of-day equity calculation ──
            pos_value = sum(p.current_value * p.quantity * 100.0 for p in self.positions)
            self.equity = self.capital + pos_value
            self.equity_curve.append((date_str, round(self.equity, 2)))

        gain = ((self.equity - self.start_capital) / self.start_capital) * 100.0
        print(f"  [100.0%] Simulation complete | Equity: ${self.equity:,.0f} ({gain:+.1f}%) | Total Trades: {len(self.trade_history)}")

        return self._generate_report(config)

    def _generate_report(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Generate comprehensive backtest report."""
        
        # Collect final active zones and trend bias
        active_zones_dict = {}
        market_bias_dict = {}
        for sym, tracker in self.strategy.trackers.items():
            market_bias_dict[sym] = tracker.bias
            active_zones_dict[sym] = [
                {
                    "id": z.id,
                    "type": z.type,
                    "high": round(z.high, 2),
                    "low": round(z.low, 2),
                    "origin_time": z.origin_candle_time
                }
                for z in (tracker.demand_zones + tracker.supply_zones) if z.is_active
            ]

        if not self.trade_history:
            return {
                "starting_capital": self.start_capital,
                "ending_equity": round(self.equity, 2),
                "total_return_pct": 0.0,
                "total_pnl": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_date": "",
                "sharpe_ratio": 0.0,
                "avg_holding_days": 0.0,
                "profitable_months": 0,
                "total_months": 0,
                "exits_breakdown": {},
                "per_symbol": {},
                "market_bias": market_bias_dict,
                "active_zones": active_zones_dict,
                "risk_filter_logs": self.strategy.selector.risk_filter_logs
            }

        wins = [t for t in self.trade_history if t["pnl"] > 0]
        losses = [t for t in self.trade_history if t["pnl"] <= 0]

        total_pnl = sum(t["pnl"] for t in self.trade_history)
        gross_wins = sum(t["pnl"] for t in wins) if wins else 0
        gross_losses = abs(sum(t["pnl"] for t in losses)) if losses else 0

        total_return = ((self.equity - self.start_capital) / self.start_capital) * 100.0

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        max_dd_date = ""
        for date_str, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
                max_dd_date = date_str

        # Sharpe ratio
        returns = []
        for i in range(1, len(self.equity_curve)):
            ret = (self.equity_curve[i][1] - self.equity_curve[i-1][1]) / self.equity_curve[i-1][1]
            returns.append(ret)

        mean_ret = sum(returns) / len(returns) if returns else 0
        var_ret = sum((r - mean_ret)**2 for r in returns) / len(returns) if returns else 0
        std_ret = math.sqrt(var_ret) if var_ret > 0 else 0.0
        sharpe = (mean_ret / std_ret) * math.sqrt(252.0) if std_ret > 0 else 0.0

        # Per-symbol stats
        symbol_stats: Dict[str, Dict] = {}
        for t in self.trade_history:
            sym = t["symbol"]
            if sym not in symbol_stats:
                symbol_stats[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
            symbol_stats[sym]["trades"] += 1
            symbol_stats[sym]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                symbol_stats[sym]["wins"] += 1

        holding_days = [t.get("holding_days", 0) for t in self.trade_history]
        avg_hold = sum(holding_days) / len(holding_days) if holding_days else 0

        monthly_vals = list(self.monthly_pnl.values())
        profitable_months = sum(1 for v in monthly_vals if v > 0)

        report = {
            "starting_capital": self.start_capital,
            "ending_equity": round(self.equity, 2),
            "total_return_pct": round(total_return, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": len(self.trade_history),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round((len(wins) / len(self.trade_history)) * 100.0, 1),
            "avg_win": round(gross_wins / len(wins), 2) if wins else 0,
            "avg_loss": round(gross_losses / len(losses), 2) if losses else 0,
            "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else float("inf"),
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "max_drawdown_date": max_dd_date,
            "sharpe_ratio": round(sharpe, 3),
            "avg_holding_days": round(avg_hold, 1),
            "profitable_months": profitable_months,
            "total_months": len(monthly_vals),
            "exits_breakdown": self._get_exit_reasons_breakdown(),
            "per_symbol": {s: {"trades": v["trades"], "wins": v["wins"],
                               "win_rate": round(v["wins"]/v["trades"]*100, 1) if v["trades"] > 0 else 0,
                               "pnl": round(v["pnl"], 2)}
                           for s, v in symbol_stats.items()},
            "market_bias": market_bias_dict,
            "active_zones": active_zones_dict,
            "risk_filter_logs": self.strategy.selector.risk_filter_logs
        }

        return report

    def _get_exit_reasons_breakdown(self) -> Dict[str, int]:
        breakdown = {}
        for t in self.trade_history:
            reason = t.get("reason", "unknown")
            breakdown[reason] = breakdown.get(reason, 0) + 1
        return breakdown

    def save_trade_log(self, path: str):
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.trade_history, f, indent=2)

    def save_equity_curve(self, path: str):
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Equity"])
            writer.writerows(self.equity_curve)


def load_csv(path: str) -> Dict[str, pd.DataFrame]:
    df = pd.read_csv(path)
    timeframe = CONFIG.strategy.default_timeframe.strip().lower()
    is_intraday = timeframe in ["15m", "1h", "60m"]
    if is_intraday:
        df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.strftime("%Y-%m-%d")
    data = {}
    for sym in df["Symbol"].unique():
        data[sym] = df[df["Symbol"] == sym].sort_values("Date")
    return data


def main():
    import argparse
    import os
    import sys
    from utils import setup_logging
    setup_logging()
    
    parser = argparse.ArgumentParser(description="ApexSpreadator Backtesting Engine")
    parser.add_argument("--csv", type=str, required=True, help="Path to historical combined CSV")
    parser.add_argument("--capital", type=float, default=25000.0, help="Starting capital")
    parser.add_argument("--symbols", type=str, nargs="+", default=[], help="Override symbols to backtest")
    parser.add_argument("--timeframe", "--interval", dest="interval", type=str, default="1d", help="Timeframe of the data to backtest")
    parser.add_argument("--lookback", type=str, default="2y", help="Lookback period (e.g. '60d', '730d', '2y')")
    args = parser.parse_args()

    # Override default timeframe
    CONFIG.strategy.default_timeframe = args.interval

    if args.symbols:
        CONFIG.strategy.underlyings = args.symbols
        # Force the universe to static so the UniverseManager strictly obeys this list
        CONFIG.strategy.universe_type = "static" 
        CONFIG.strategy.screener_type = "static"

    # Context-Aware / Dynamic Ingestion: check if files exist, download if they don't
    interval = args.interval.strip().lower()
    from core.universe_manager import UniverseManager
    universe_manager = UniverseManager(CONFIG)
    target_universe = universe_manager.get_universe()
    
    need_download = False
    if not os.path.exists(args.csv):
        need_download = True
    else:
        for sym in target_universe:
            sym_file = f"data/{interval}/{sym.lower()}.csv"
            if not os.path.exists(sym_file):
                need_download = True
                break

    if need_download:
        print(f"⚠️ Missing historical data files for interval '{interval}'. Triggering yfinance download...")
        import subprocess
        cmd = [sys.executable, "data/download_historical.py", "--lookback", args.lookback, "--interval", interval, "--symbols"] + target_universe
        print(f"Running command: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            print(f"❌ Failed to download historical data: {e}")
            sys.exit(1)

    df_data = pd.read_csv(args.csv)
    
    backtester = OptionsBacktester(start_capital=args.capital)
    
    # Dynamically resolve DTE based on interval config
    dte_val = CONFIG.strategy.timeframe_dte_map.get(interval, 30)
    config = {
        "max_concurrent_positions": CONFIG.risk.max_concurrent_positions,
        "dte": dte_val
    }
    
    report = backtester.run_backtest(df_data, config)

    # ── Print Results ────────────────────────────────────────────
    print()
    print("╔" + "═" * 58 + "╗")
    print("║                 APEXSPREADATOR RESULTS                  ║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Starting Capital:      ${report['starting_capital']:>12,.2f}              ║")
    print(f"║  Ending Equity:         ${report['ending_equity']:>12,.2f}              ║")
    print(f"║  Total Return:          {report['total_return_pct']:>11.2f}%               ║")
    print(f"║  Total P&L:             ${report['total_pnl']:>12,.2f}              ║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Total Trades:          {report['total_trades']:>8}                      ║")
    print(f"║  Win Rate:              {report['win_rate_pct']:>7.1f}%                     ║")
    print(f"║  Avg Win:               ${report['avg_win']:>10,.2f}                ║")
    print(f"║  Avg Loss:              ${report['avg_loss']:>10,.2f}                ║")
    print(f"║  Profit Factor:         {report['profit_factor']:>8}                      ║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Sharpe Ratio:          {report['sharpe_ratio']:>8.3f}                      ║")
    print(f"║  Max Drawdown:          {report['max_drawdown_pct']:>7.2f}%                     ║")
    print(f"║  Avg Holding Days:      {report['avg_holding_days']:>8.1f}                      ║")
    print(f"║  Profitable Months:     {report['profitable_months']:>3} / {report['total_months']:<3}                      ║")
    print("╚" + "═" * 58 + "╝")

    os.makedirs("data", exist_ok=True)
    import json
    
    report_to_save = copy.deepcopy(report)
    if report_to_save.get("profit_factor") == float('inf'):
        report_to_save["profit_factor"] = "Infinity"

    report_path = "data/backtest_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_to_save, f, indent=2)
    print(f"\n  📄 Report saved: {report_path}")

    trades_path = "data/backtest_trades.json"
    backtester.save_trade_log(trades_path)
    print(f"  📄 Trade log saved: {trades_path}")

    curve_path = "data/backtest_equity_curve.csv"
    backtester.save_equity_curve(curve_path)
    print(f"  📄 Equity curve saved: {curve_path}")


if __name__ == "__main__":
    main()
