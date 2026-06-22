"""
ApexSpreadator — Stock Screener Engine
Dynamically screens for high-volatility, liquid underlying assets to populate the bot's watchlist.
"""
import math
from typing import List, Optional, Dict, Any
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

                    # Liquidity check: Average daily volume > 500,000 shares
                    avg_volume = symbol_df["Volume"].mean()
                    if avg_volume < 500000:
                        continue

                    vol_scores.append((symbol, avg_range_pct))

                vol_scores.sort(key=lambda x: x[1], reverse=True)
                candidates = [x[0] for x in vol_scores[:limit]]
                logger.info(f"Top historical volatility candidates found: {candidates}")
                return candidates
            except Exception as err:
                logger.error(f"Failed to execute historical screening: {err}")
                return []

        logger.info(f"Screening {len(self.default_pool)} symbols for volatility candidates...")
        candidates = []
        
        try:
            # Batch download 5 days of history in one request
            symbols_str = " ".join(self.default_pool)
            data = yf.download(symbols_str, period="5d", interval="1d", group_by="ticker", progress=False)
            
            if data.empty:
                logger.warning("No data retrieved during batch screen download.")
                return []

            vol_scores = []
            for symbol in self.default_pool:
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

                    # Liquidity check: Average daily volume > 500,000 shares
                    avg_volume = df["Volume"].mean()
                    if avg_volume < 500000:
                        continue

                    vol_scores.append((symbol, avg_range_pct))
                except Exception as sym_err:
                    logger.debug(f"Skipping {symbol} in screening due to error: {sym_err}")
                    continue

            # Sort by daily range percent descending
            vol_scores.sort(key=lambda x: x[1], reverse=True)
            candidates = [x[0] for x in vol_scores[:limit]]
            logger.info(f"Top volatility candidates found: {candidates}")
        except Exception as err:
            logger.error(f"Failed to execute screening cycle: {err}")
            
        return candidates
