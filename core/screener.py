"""
ApexSpreadator — Stock Screener Engine / Universe Provider
Provides the full configured universe of symbols for the bot's watchlist.
Supports pre-calculating supply/demand zones offline.
"""
from typing import List, Optional, Any
import os
import json
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from core.underlying_tracker import UnderlyingTracker
from utils import get_logger

logger = get_logger("Screener")


class ScreenerEngine:
    """
    Universe Provider that returns the full configured list of underlyings
    and supports generating active_zones.json for Warm Starts.
    """

    def __init__(self, config: Any, broker: Optional[Any] = None):
        self.config = config
        self.broker = broker

    def get_candidate_list(
        self,
        current_time: Optional[Any] = None,
        limit: Optional[int] = None,
        historical_df: Optional[Any] = None,
        date_limit: Optional[str] = None
    ) -> List[str]:
        """
        Return the full list of symbols configured in the strategy's underlyings.
        """
        strategy_config = getattr(self.config, "strategy", None)
        if strategy_config and hasattr(strategy_config, "underlyings"):
            return list(strategy_config.underlyings)
        return []

    def run_screening_pipeline(self, limit: Optional[int] = None) -> None:
        """
        Executes the offline screening pipeline: downloads historical data for all watchlisted tickers,
        feeds it into the StrategyEngine's UnderlyingTrackers to compute the trend bias and Supply/Demand zones,
        and writes the resulting state to data/active_zones.json.
        """
        symbols = self.get_candidate_list()
        if not symbols:
            logger.warning("No underlying symbols configured in strategy config.")
            return

        logger.info(f"Running screening pipeline for {len(symbols)} symbols...")
        
        # Get target DTE and timeframe from config
        timeframe = getattr(self.config.strategy, "default_timeframe", "1d")
        
        # Date range for 100 bars lookback (e.g. 150 calendar days for daily data)
        # For sub-daily intervals (15m/1h), 10-15 calendar days is sufficient
        lookback_days = 150 if timeframe == "1d" else 15
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=lookback_days)
        
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")
        
        active_zones_data = {}

        for symbol in symbols:
            try:
                logger.info(f"Fetching history for {symbol} ({timeframe})...")
                ticker_sym = symbol
                ticker = yf.Ticker(ticker_sym)
                df = ticker.history(start=start_str, end=end_str, interval=timeframe)
                
                if df.empty:
                    # Try fallback to local file
                    local_path = os.path.join("data", timeframe, f"{symbol.lower()}.csv")
                    if os.path.exists(local_path):
                        logger.info(f"yfinance returned empty for {symbol}. Loading fallback local file: {local_path}")
                        df = pd.read_csv(local_path)
                        if "Date" in df.columns:
                            df = df.set_index("Date")
                    else:
                        logger.warning(f"No historical data found for {symbol} via yfinance or local file.")
                        continue
                
                # Format index/Date
                df = df.reset_index()
                if "Datetime" in df.columns:
                    df = df.rename(columns={"Datetime": "Date"})
                
                # Ingest candles into a fresh tracker
                tracker = UnderlyingTracker(
                    symbol=symbol,
                    fractal_window=getattr(self.config.strategy, "fractal_window", 3)
                )
                
                for _, row in df.iterrows():
                    # Format timestamp
                    date_val = row.get("Date")
                    if isinstance(date_val, datetime):
                        ts_str = date_val.strftime("%Y-%m-%d %H:%M:%S" if timeframe != "1d" else "%Y-%m-%d")
                    else:
                        ts_str = str(date_val)
                    
                    tracker.add_candle(
                        open_p=float(row.get("Open", 0.0)),
                        high_p=float(row.get("High", 0.0)),
                        low_p=float(row.get("Low", 0.0)),
                        close_p=float(row.get("Close", 0.0)),
                        volume=float(row.get("Volume", 0.0)),
                        timestamp=ts_str,
                        iv=0.18
                    )
                
                # Serialize tracker state
                active_zones_data[symbol] = {
                    "trend_status": tracker.bias,
                    "demand_zones": [
                        {
                            "id": z.id,
                            "high": z.high,
                            "low": z.low,
                            "origin_candle_time": z.origin_candle_time,
                            "is_active": z.is_active
                        } for z in tracker.demand_zones
                    ],
                    "supply_zones": [
                        {
                            "id": z.id,
                            "high": z.high,
                            "low": z.low,
                            "origin_candle_time": z.origin_candle_time,
                            "is_active": z.is_active
                        } for z in tracker.supply_zones
                    ],
                    "swing_highs": tracker.swing_highs,
                    "swing_lows": tracker.swing_lows,
                    "candles": tracker.candles
                }
                logger.info(f"✅ Pre-computed state for {symbol}: bias={tracker.bias}, demand={len(tracker.demand_zones)}, supply={len(tracker.supply_zones)}")
            except Exception as e:
                logger.error(f"Error processing screening for {symbol}: {e}")
                continue

        # Write to active_zones.json
        os.makedirs("data", exist_ok=True)
        out_path = "data/active_zones.json"
        with open(out_path, "w") as f:
            json.dump(active_zones_data, f, indent=4)
        logger.info(f"✅ Screener pipeline completed. Wrote {len(active_zones_data)} pre-computed symbols to {out_path}")
