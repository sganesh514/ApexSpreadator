"""
ApexSpreadator — Stock Screener Engine
Dynamically screens for high-volatility, liquid underlying assets to populate the bot's watchlist.
"""
import math
from typing import List, Optional, Dict, Any
import pandas as pd
import yfinance as yf
from config import AgentConfig
from utils import get_logger

logger = get_logger("Screener")


class ScreenerEngine:
    """
    Screens standard watchlists (e.g. tech/index large caps) for active volatility regimes.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        # Pool of liquid stocks/ETFs to scan from
        self.default_pool = [
            "SPY", "QQQ", "IWM", "AAPL", "MSFT", "AMZN", "GOOG", "META", 
            "NVDA", "TSLA", "AMD", "NFLX", "AVGO", "QCOM", "ADBE", "AMAT", 
            "MU", "PANW", "LRCX", "COST", "PEP", "INTC", "CSCO", "TXN"
        ]

    def _fetch_sp500_constituents(self) -> List[str]:
        logger.info("Fetching S&P 500 constituents from Wikipedia...")
        try:
            tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
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
            tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
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
        # If limit is 5 (default), but config says something else, use config_limit
        effective_limit = limit if limit != 5 else config_limit
        min_volume = getattr(strategy_config, "screener_min_volume", 500000) if strategy_config else 500000

        if historical_df is not None:
            logger.info(f"Screening historical data ending at {date_limit or 'latest'}...")
            try:
                df = historical_df
                if date_limit is not None:
                    df = df[df["Date"] <= date_limit]

                symbols = df["Symbol"].unique()
                vol_scores = []
                
                for symbol in symbols:
                    # Get last 5 days of data for this symbol
                    symbol_df = df[df["Symbol"] == symbol].sort_values("Date").tail(5)
                    
                    if len(symbol_df) < 3:
                        continue

                    # Volatility Metric: Average daily range percent
                    daily_ranges = (symbol_df["High"] - symbol_df["Low"]) / symbol_df["Close"]
                    avg_range_pct = daily_ranges.mean()

                    # Liquidity check: Average daily volume > min_volume shares
                    avg_volume = symbol_df["Volume"].mean()
                    if avg_volume < min_volume:
                        continue

                    vol_scores.append((symbol, avg_range_pct))

                vol_scores.sort(key=lambda x: x[1], reverse=True)
                candidates = [x[0] for x in vol_scores[:effective_limit]]
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
            # We download in chunks to avoid yfinance query size limits / timeouts
            chunk_size = 50
            chunks = [pool[i:i + chunk_size] for i in range(0, len(pool), chunk_size)]
            
            vol_scores = []
            for chunk in chunks:
                symbols_str = " ".join(chunk)
                try:
                    data = yf.download(symbols_str, period="5d", interval="1d", group_by="ticker", progress=False)
                    if data.empty:
                        continue

                    for symbol in chunk:
                        try:
                            # Handle batch df structure
                            if symbol in data:
                                df = data[symbol]
                            else:
                                continue
                            
                            df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
                            if len(df) < 3:
                                continue

                            # Volatility Metric: Average daily range percent
                            daily_ranges = (df["High"] - df["Low"]) / df["Close"]
                            avg_range_pct = daily_ranges.mean()

                            # Liquidity check
                            avg_volume = df["Volume"].mean()
                            if avg_volume < min_volume:
                                continue

                            vol_scores.append((symbol, avg_range_pct))
                        except Exception as sym_err:
                            logger.debug(f"Skipping {symbol} in screening due to error: {sym_err}")
                            continue
                except Exception as chunk_err:
                    logger.error(f"Failed to execute batch download for chunk: {chunk_err}")
                    continue

            # Sort by daily range percent descending
            vol_scores.sort(key=lambda x: x[1], reverse=True)
            candidates = [x[0] for x in vol_scores[:effective_limit]]
            logger.info(f"Top volatility candidates found: {candidates}")
        except Exception as err:
            logger.error(f"Failed to execute screening cycle: {err}")
            
        return candidates

