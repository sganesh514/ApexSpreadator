"""
ApexSpreadator — Universe Manager
Handles universe selection (static watchlist, Nasdaq-100, S&P 500 constituents) on startup.
"""
import urllib.request
import io
from typing import List
import pandas as pd
from utils import get_logger
from config import CONFIG

logger = get_logger("UniverseManager")


class UniverseManager:
    """
    Manages static and dynamic universe lists.
    """

    # Hardcoded fallbacks for offline or network failure scenarios
    _nasdaq100_fallback = [
        "AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "PEP", 
        "COST", "ADBE", "CSCO", "NFLX", "AMD", "CMCSA", "TMUS", "TXN", "INTU", "AMGN", 
        "HON", "QCOM", "ISRG", "PGR", "LRCX", "VRTX", "BKNG", "REGN", "MDLZ", "PANW", 
        "MU", "SNPS", "CDNS", "ADI", "KLAC", "MELI", "CHTR", "MAR", "CSX", "ORLY", 
        "CTAS", "MNST", "NXPI", "FTNT", "KDP", "DXCM", "AEP", "ODFL", "PAYX", "MCHP", 
        "KHC", "EXC", "EA", "BIIB", "CTSH", "IDXX", "ROPI", "FAST", "VRSK", "CPRT", 
        "PCAR", "ANSS", "ZS", "SWKS", "ZM", "DOCU", "CDW", "WBD", "DDOG", "CRWD"
    ]

    _sp500_fallback = [
        "AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL", "GOOG", "BRK-B", "LLY", "AVGO",
        "JPM", "TSLA", "UNH", "V", "XOM", "MA", "HD", "PG", "COST", "JNJ",
        "ABBV", "BAC", "MRK", "NFLX", "CRM", "AMD", "ADBE", "PEP", "TMO", "CVX",
        "WMT", "COST", "KO", "MCD", "DIS", "CSCO", "INTC", "AMGN", "QCOM", "TXN"
    ]

    @staticmethod
    def _fetch_sp500_constituents() -> List[str]:
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
            logger.warning("Could not find Symbol/Ticker column in S&P 500 Wikipedia tables.")
            return []
        except Exception as e:
            logger.error(f"Error fetching S&P 500 from Wikipedia: {e}")
            return []

    @staticmethod
    def _fetch_nasdaq100_constituents() -> List[str]:
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
            logger.warning("Could not find Ticker/Symbol column in Nasdaq-100 Wikipedia tables.")
            return []
        except Exception as e:
            logger.error(f"Error fetching Nasdaq-100 from Wikipedia: {e}")
            return []

    @classmethod
    def get_universe(cls, universe_type: str) -> List[str]:
        """
        Get the list of tickers for the specified universe type.
        """
        u_type = str(universe_type).lower().strip()
        if u_type == "sp500":
            tickers = cls._fetch_sp500_constituents()
            if tickers:
                return tickers
            logger.warning("Dynamic S&P 500 fetch failed, using fallback list.")
            return cls._sp500_fallback
        elif u_type == "nasdaq100":
            tickers = cls._fetch_nasdaq100_constituents()
            if tickers:
                return tickers
            logger.warning("Dynamic Nasdaq-100 fetch failed, using fallback list.")
            return cls._nasdaq100_fallback
        else:
            # "static" or default
            return CONFIG.strategy.underlyings
