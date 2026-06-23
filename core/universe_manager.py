"""
ApexSpreadator — Centralized Universe Manager
Manages static and dynamic universe selection and integrates constituents fetching.
"""
import urllib.request
import io
import pandas as pd
from typing import List, Optional, Any
from utils import get_logger

logger = get_logger("UniverseManager")


class UniverseManager:
    """
    Manages static and dynamic universe lists based on configuration or manual input.
    """

    def __init__(self, config: Optional[Any] = None):
        self.config = config

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

    def get_universe(self, universe_type: Optional[str] = None) -> list:
        """
        Get the list of tickers for the configured or requested universe type.
        """
        # Resolve static underlyings fallback
        fallback_list = ["SPY", "QQQ"]
        if self.config and hasattr(self.config, "strategy") and hasattr(self.config.strategy, "underlyings"):
            fallback_list = self.config.strategy.underlyings

        # Resolve universe type if not provided
        if universe_type is None:
            if self.config and hasattr(self.config, "strategy"):
                strat = self.config.strategy
                universe_type = getattr(strat, "universe_type", getattr(strat, "screener_type", "static"))
            else:
                universe_type = "static"

        u_type = str(universe_type).lower().strip()
        
        if u_type == "sp500":
            tickers = self._fetch_sp500_constituents()
            if tickers:
                return tickers
            logger.warning("Dynamic S&P 500 fetch failed. Falling back to static underlyings.")
            return fallback_list
        elif u_type == "nasdaq100":
            tickers = self._fetch_nasdaq100_constituents()
            if tickers:
                return tickers
            logger.warning("Dynamic Nasdaq-100 fetch failed. Falling back to static underlyings.")
            return fallback_list
        else:
            # "static"
            return fallback_list
