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


async def get_live_prices(symbols: List[str], broker: Any) -> Dict[str, float]:
    """
    Retrieve the live prices of multiple underlying assets from the broker in batch.
    """
    try:
        if broker and hasattr(broker, "get_underlying_prices"):
            return await broker.get_underlying_prices(symbols)
            
        # Fallback to individual calls
        prices = {}
        for sym in symbols:
            prices[sym] = await get_live_price(sym, broker)
        return prices
    except Exception as e:
        logger.error(f"Error fetching live batch prices: {e}")
        return {}


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


def extract_atm_iv_from_chain(
    chain,
    underlying_price: float,
    right: str = "C",
) -> Optional[float]:
    """
    Extract per-symbol ATM implied volatility from an already-fetched options chain.

    Finds the nearest ATM contract (closest strike to underlying_price) from the
    nearest available expiration and returns its broker-reported implied volatility.

    Accepts both pandas DataFrame and list-of-dicts input.
    Returns IV as a decimal (e.g. 0.35 for 35%), or None if unavailable.
    """
    import pandas as pd

    if isinstance(chain, pd.DataFrame):
        if chain.empty:
            return None
        chain = chain.to_dict(orient="records")

    if not chain:
        return None

    # Filter to the specified right
    contracts = [c for c in chain if c.get("right") == right]
    if not contracts:
        contracts = list(chain)  # fallback: any right

    # Nearest expiration
    expirations = sorted(set(c["expiration"] for c in contracts if c.get("expiration")))
    if not expirations:
        return None

    nearest_contracts = [c for c in contracts if c["expiration"] == expirations[0]]
    if not nearest_contracts:
        return None

    # Nearest ATM contract
    atm_contract = min(
        nearest_contracts,
        key=lambda c: abs(float(c.get("strike", 0)) - underlying_price)
    )

    iv = atm_contract.get("iv", 0.0)
    if iv and float(iv) > 0.001:
        logger.debug(
            f"Extracted ATM IV for {atm_contract.get('symbol', '?')}: "
            f"{float(iv)*100:.1f}% (strike={atm_contract.get('strike')}, "
            f"exp={atm_contract.get('expiration')})"
        )
        return float(iv)

    return None

