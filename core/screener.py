"""
ApexSpreadator — Stock Screener Engine
Dynamically screens for high-volatility, liquid underlying assets to populate the bot's watchlist.
"""
import os
import math
import io
import urllib.request
from typing import List, Optional, Dict, Any
import pandas as pd
from config import AgentConfig
from utils import get_logger
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta



logger = get_logger("Screener")


class ScreenerEngine:
    """
    Screens standard watchlists (e.g. tech/index large caps) for active volatility regimes.
    """

    def __init__(self, config: AgentConfig, broker: Optional[Any] = None):
        self.config = config
        self.broker = broker
        # Pool of liquid stocks/ETFs to scan from
        self.default_pool = [
            "SPY", "QQQ", "IWM", "AAPL", "MSFT", "AMZN", "GOOG", "META", 
            "NVDA", "TSLA", "AMD", "NFLX", "AVGO", "QCOM", "ADBE", "AMAT", 
            "MU", "PANW", "LRCX", "COST", "PEP", "INTC", "CSCO", "TXN"
        ]

    def _fetch_sp500_constituents(self) -> List[str]:
        logger.info("Fetching S&P 500 constituents from Wikipedia...")
        try:
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8')
            
            tables = pd.read_html(io.StringIO(html))
            for table in tables:
                if "Symbol" in table.columns:
                    symbols = table["Symbol"].tolist()
                    return [str(s).strip().replace(".", "-") for s in symbols]
                elif "Ticker" in table.columns:
                    symbols = table["Ticker"].tolist()
                    return [str(s).strip().replace(".", "-") for s in symbols]
            logger.warning("Could not find Symbol/Ticker column in S&P 500 tables.")
            return []
        except Exception as e:
            logger.error(f"Error fetching S&P 500 from Wikipedia: {e}")
            return []

    def _fetch_nasdaq100_constituents(self) -> List[str]:
        logger.info("Fetching Nasdaq-100 constituents from Wikipedia...")
        try:
            url = "https://en.wikipedia.org/wiki/Nasdaq-100"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8')
            
            tables = pd.read_html(io.StringIO(html))
            for table in tables:
                if "Ticker" in table.columns:
                    symbols = table["Ticker"].tolist()
                    return [str(s).strip().replace(".", "-") for s in symbols]
                elif "Symbol" in table.columns:
                    symbols = table["Symbol"].tolist()
                    return [str(s).strip().replace(".", "-") for s in symbols]
            logger.warning("Could not find Ticker/Symbol column in Nasdaq-100 tables.")
            return []
        except Exception as e:
            logger.error(f"Error fetching Nasdaq-100 from Wikipedia: {e}")
            return []


    def get_candidate_list(
        self,
        current_time: Optional[Any] = None,
        limit: int = 5,
        historical_df: Optional[Any] = None,
        date_limit: Optional[str] = None
    ) -> List[str]:
        """
        Screen the pool of underlying assets and return candidate list sorted by volatility.
        Supports both live screening via yfinance and historical screening for backtesting.
        """
        # Resolve limit and min_volume from config
        strategy_config = getattr(self.config, "strategy", None)
        config_limit = getattr(strategy_config, "screener_limit", 5) if strategy_config else 5
        effective_limit = limit if limit != 5 else config_limit
        min_volume = getattr(strategy_config, "screener_min_volume", 500000) if strategy_config else 500000

        # If current_time is provided, we are in Backtest/Historical Mode
        if current_time is not None:
            if isinstance(current_time, str):
                date_str = current_time
            else:
                date_str = current_time.strftime("%Y-%m-%d")

            # Use historical_df if passed, otherwise load from the csv archive
            df = historical_df
            if df is None:
                archive_path = "data/all_symbols_daily.csv"
                if os.path.exists(archive_path):
                    if not hasattr(self, "_cached_archive_df") or self._cached_archive_df is None:
                        try:
                            self._cached_archive_df = pd.read_csv(archive_path)
                            self._cached_archive_df["Date"] = pd.to_datetime(self._cached_archive_df["Date"], utc=True).dt.strftime("%Y-%m-%d")
                        except Exception as e:
                            logger.error(f"Failed to load backtest data archive: {e}")
                            return []
                    df = self._cached_archive_df
                else:
                    logger.error(f"Historical data archive {archive_path} not found.")
                    return []

            logger.info(f"Screening historical data ending at {date_str}...")
            try:
                # Filter by date
                df_filtered = df[df["Date"] <= date_str]
                symbols = df_filtered["Symbol"].unique()
                vol_scores = []
                
                for symbol in symbols:
                    # Get last 5 days of data for this symbol
                    symbol_df = df_filtered[df_filtered["Symbol"] == symbol].sort_values("Date").tail(5)
                    
                    if len(symbol_df) < 3:
                        continue

                    # Volatility Metric: Average daily range percent
                    daily_ranges = (symbol_df["High"] - symbol_df["Low"]) / symbol_df["Close"]
                    avg_range_pct = daily_ranges.mean()

                    # Liquidity check
                    avg_volume = symbol_df["Volume"].mean()
                    if avg_volume < min_volume:
                        continue

                    vol_scores.append((symbol, avg_range_pct))

                vol_scores.sort(key=lambda x: x[1], reverse=True)
                candidates = [x[0] for x in vol_scores[:effective_limit]]
                
                # Protect anchor symbols
                anchors = ["SPY", "QQQ"]
                for anchor in anchors:
                    if anchor not in candidates:
                        candidates.append(anchor)

                logger.info(f"Top historical volatility candidates found: {candidates}")
                return candidates
            except Exception as err:
                logger.error(f"Failed to execute historical screening: {err}")
                return []

        # If historical_df is passed but current_time is None (legacy support)
        if historical_df is not None:
            logger.info(f"Screening historical data ending at {date_limit or 'latest'}...")
            try:
                df = historical_df
                if date_limit is not None:
                    df = df[df["Date"] <= date_limit]

                symbols = df["Symbol"].unique()
                vol_scores = []
                
                for symbol in symbols:
                    symbol_df = df[df["Symbol"] == symbol].sort_values("Date").tail(5)
                    if len(symbol_df) < 3:
                        continue

                    daily_ranges = (symbol_df["High"] - symbol_df["Low"]) / symbol_df["Close"]
                    avg_range_pct = daily_ranges.mean()

                    avg_volume = symbol_df["Volume"].mean()
                    if avg_volume < min_volume:
                        continue

                    vol_scores.append((symbol, avg_range_pct))

                vol_scores.sort(key=lambda x: x[1], reverse=True)
                candidates = [x[0] for x in vol_scores[:effective_limit]]
                
                # Protect anchor symbols
                anchors = ["SPY", "QQQ"]
                for anchor in anchors:
                    if anchor not in candidates:
                        candidates.append(anchor)

                logger.info(f"Top historical volatility candidates found: {candidates}")
                return candidates
            except Exception as err:
                logger.error(f"Failed to execute historical screening: {err}")
                return []

        # Determine dynamic pool based on configuration
        screener_type = getattr(strategy_config, "screener_type", "static") if strategy_config else "static"
        
        if screener_type == "sp500":
            pool = self._fetch_sp500_constituents()
            if not pool:
                logger.warning("Dynamic S&P 500 fetch failed, falling back to default pool.")
                pool = self.default_pool
        elif screener_type == "nasdaq100":
            pool = self._fetch_nasdaq100_constituents()
            if not pool:
                logger.warning("Dynamic Nasdaq-100 fetch failed, falling back to default pool.")
                pool = self.default_pool
        else:
            pool = self.default_pool

        logger.info(f"Screening {len(pool)} symbols for volatility candidates (type: {screener_type})...")
        candidates = []
        
        try:
            # We fetch candidates in parallel using ThreadPoolExecutor
            def fetch_symbol_score(symbol):
                try:
                    ticker = yf.Ticker(symbol)
                    df = ticker.history(period="10d")
                    if df.empty:
                        return None
                        
                    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
                    if len(df) < 3:
                        return None
                        
                    # Volatility Metric: Average daily range percent
                    daily_ranges = (df["High"] - df["Low"]) / df["Close"]
                    avg_range_pct = daily_ranges.mean()
                    
                    # Liquidity check
                    avg_volume = df["Volume"].mean()
                    if avg_volume < min_volume:
                        return None
                        
                    return symbol, avg_range_pct
                except Exception as sym_err:
                    logger.debug(f"Skipping {symbol} in screening due to error: {sym_err}")
                    return None

            vol_scores = []
            max_workers = min(10, len(pool))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = executor.map(fetch_symbol_score, pool)
                for res in results:
                    if res is not None:
                        vol_scores.append(res)
                        
            # Sort by daily range percent descending
            vol_scores.sort(key=lambda x: x[1], reverse=True)
            candidates = [x[0] for x in vol_scores[:effective_limit]]
            
            # Protect anchor symbols
            anchors = ["SPY", "QQQ"]
            for anchor in anchors:
                if anchor not in candidates:
                    candidates.append(anchor)

            logger.info(f"Top volatility candidates found: {candidates}")
        except Exception as err:
            logger.error(f"Failed to execute screening cycle: {err}")
            
        return candidates

    def run_screening_pipeline(self, limit: int = 5) -> Dict[str, Any]:
        """
        Standalone offline screening pipeline:
        1. Fetches candidate pool (S&P 500, Nasdaq-100, or static pool).
        2. Downloads daily candles from yfinance.
        3. Calculates volatility & 10-EMA.
        4. Sorts by volatility, applies limit, and adds anchors (SPY, QQQ).
        5. Runs candle history through local UnderlyingTracker to map zones.
        6. Writes results to data/active_zones.json.
        """
        from core.underlying_tracker import UnderlyingTracker
        import json
        
        strategy_config = getattr(self.config, "strategy", None)
        screener_type = getattr(strategy_config, "screener_type", "static") if strategy_config else "static"
        
        # 1. Fetch pool
        if screener_type == "sp500":
            pool = self._fetch_sp500_constituents()
            if not pool:
                logger.warning("Dynamic S&P 500 fetch failed, falling back to default pool.")
                pool = self.default_pool
        elif screener_type == "nasdaq100":
            pool = self._fetch_nasdaq100_constituents()
            if not pool:
                logger.warning("Dynamic Nasdaq-100 fetch failed, falling back to default pool.")
                pool = self.default_pool
        else:
            pool = self.default_pool
            
        # Ensure SPY and QQQ are in the candidate pool for zone tracking
        pool_set = set(pool)
        pool_set.add("SPY")
        pool_set.add("QQQ")
        unique_pool = list(pool_set)
        
        logger.info(f"Downloading historical daily data via yfinance for {len(unique_pool)} tickers...")
        
        # 2. Download daily data via yfinance in parallel
        ticker_data = {}
        
        def fetch_ticker(symbol: str) -> Optional[pd.DataFrame]:
            try:
                yf_sym = symbol
                if yf_sym == "VIX":
                    yf_sym = "^VIX"
                
                ticker = yf.Ticker(yf_sym)
                df = ticker.history(period="90d")
                if df.empty:
                    logger.warning(f"No yfinance data returned for {symbol}")
                    return None
                
                df = df.reset_index()
                rename_map = {
                    "Date": "Date",
                    "Open": "Open",
                    "High": "High",
                    "Low": "Low",
                    "Close": "Close",
                    "Volume": "Volume"
                }
                df = df.rename(columns=rename_map)
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
                df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
                return df
            except Exception as e:
                logger.error(f"Failed to fetch {symbol} from yfinance: {e}")
                return None
                
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = executor.map(fetch_ticker, unique_pool)
            for sym, res in zip(unique_pool, results):
                if res is not None and len(res) >= 10:
                    ticker_data[sym] = res
                    
        # 3. Calculate Volatility and 10-EMA
        vol_scores = []
        ema_map = {}
        volatility_map = {}
        
        for symbol, df in ticker_data.items():
            df["Range_Pct"] = (df["High"] - df["Low"]) / df["Close"]
            avg_range_pct = df["Range_Pct"].tail(5).mean()
            volatility_map[symbol] = avg_range_pct
            
            df["EMA_10"] = df["Close"].ewm(span=10, adjust=False).mean()
            ema_map[symbol] = df["EMA_10"].iloc[-1]
            
            if symbol not in ["SPY", "QQQ"]:
                min_volume = getattr(strategy_config, "screener_min_volume", 500000) if strategy_config else 500000
                avg_volume = df["Volume"].tail(5).mean()
                if avg_volume >= min_volume:
                    vol_scores.append((symbol, avg_range_pct))
                    
        vol_scores.sort(key=lambda x: x[1], reverse=True)
        top_candidates = [x[0] for x in vol_scores[:limit]]
        
        final_watchlist = []
        for sym in top_candidates:
            if sym not in final_watchlist:
                final_watchlist.append(sym)
        for anchor in ["SPY", "QQQ"]:
            if anchor not in final_watchlist and anchor in ticker_data:
                final_watchlist.append(anchor)
                
        logger.info(f"Shortlisted candidates: {final_watchlist}")
        
        # 4. Map zones locally via UnderlyingTracker
        active_zones_dict = {}
        for symbol in final_watchlist:
            df = ticker_data[symbol]
            tracker = UnderlyingTracker(
                symbol=symbol,
                fractal_window=getattr(strategy_config, "fractal_window", 3) if strategy_config else 3
            )
            
            for _, row in df.iterrows():
                timestamp = row["Date"].strftime("%Y-%m-%d")
                tracker.add_candle(
                    open_p=row["Open"],
                    high_p=row["High"],
                    low_p=row["Low"],
                    close_p=row["Close"],
                    volume=row["Volume"],
                    timestamp=timestamp,
                    iv=0.18
                )
                
            demand_zones = []
            for zone in tracker.demand_zones:
                if zone.is_active:
                    demand_zones.append({
                        "id": zone.id,
                        "high": float(zone.high),
                        "low": float(zone.low),
                        "entry": float(zone.high),
                        "invalidation": float(zone.low),
                        "origin_candle_time": zone.origin_candle_time,
                        "is_active": zone.is_active
                    })
            supply_zones = []
            for zone in tracker.supply_zones:
                if zone.is_active:
                    supply_zones.append({
                        "id": zone.id,
                        "high": float(zone.high),
                        "low": float(zone.low),
                        "entry": float(zone.low),
                        "invalidation": float(zone.high),
                        "origin_candle_time": zone.origin_candle_time,
                        "is_active": zone.is_active
                    })
                    
            swing_highs = [(int(idx), float(price)) for idx, price in tracker.swing_highs]
            swing_lows = [(int(idx), float(price)) for idx, price in tracker.swing_lows]
            
            recent_candles = []
            for c in tracker.candles[-30:]:
                recent_candles.append({
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c["volume"]),
                    "time": c["time"],
                    "iv": float(c["iv"])
                })
                
            active_zones_dict[symbol] = {
                "symbol": symbol,
                "trend_status": tracker.bias,
                "volatility": float(volatility_map[symbol]),
                "ema_10": float(ema_map[symbol]),
                "demand_zones": demand_zones,
                "supply_zones": supply_zones,
                "swing_highs": swing_highs,
                "swing_lows": swing_lows,
                "candles": recent_candles
            }
            
        os.makedirs("data", exist_ok=True)
        out_path = "data/active_zones.json"
        with open(out_path, "w") as f:
            json.dump(active_zones_dict, f, indent=4)
            
        logger.info(f"Successfully serialized {len(active_zones_dict)} symbols to {out_path}")
        return active_zones_dict



