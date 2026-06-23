"""
ApexSpreadator — Unified Data Loader
Provides a single interface for routing live price streams and options chain queries.
"""
from typing import Optional, Any, List, Dict
from utils import get_logger

logger = get_logger("DataLoader")


async def get_live_price(symbol: str, broker: Any) -> float:
    """
    Retrieve the live price of an underlying asset from the broker.
    """
    try:
        clean_symbol = symbol
        if broker and broker.name == "Moomoo" and clean_symbol.startswith("^"):
            clean_symbol = clean_symbol.lstrip("^")
            
        return await broker.get_underlying_price(clean_symbol)
    except Exception as e:
        logger.error(f"Error fetching live price for {symbol}: {e}")
        return 0.0


async def get_live_options_chain(
    symbol: str,
    broker: Any,
    right: str,
    min_dte: int,
    max_dte: int
) -> List[Dict[str, Any]]:
    """
    Retrieve the options chain for a symbol from the broker.
    """
    try:
        clean_symbol = symbol
        if broker and broker.name == "Moomoo" and clean_symbol.startswith("^"):
            clean_symbol = clean_symbol.lstrip("^")
            
        return await broker.get_options_chain(
            symbol=clean_symbol,
            right=right,
            min_dte=min_dte,
            max_dte=max_dte
        )
    except Exception as e:
        logger.error(f"Error fetching options chain for {symbol}: {e}")
        return []
