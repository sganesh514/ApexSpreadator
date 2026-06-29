"""
ApexSpreadator — Broker Interface Layer
Defines the BrokerBase abstract base class and required methods.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any, Callable


class BrokerBase(ABC):
    """
    Abstract broker interface.
    Ensure constructors accept a port integer.
    """

    @abstractmethod
    def __init__(self, port: int):
        """Initialize the broker connection with the resolved port."""
        self._port = port

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the broker. Returns True on success."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect from the broker."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker connection is active."""
        pass

    # ── Account ──────────────────────────────────────────────────

    @abstractmethod
    async def get_account_summary(self) -> Dict[str, float]:
        """
        Get account summary.
        Returns dict with keys: balance, equity, buying_power, unrealized_pnl, realized_pnl
        """
        pass

    @abstractmethod
    async def get_account_balance(self) -> float:
        """
        Get the account cash balance.
        Returns cash balance as float.
        """
        pass

    # ── Market Data ──────────────────────────────────────────────

    @abstractmethod
    async def get_underlying_price(self, symbol: str) -> float:
        """Get current price for an underlying symbol."""
        pass

    async def get_underlying_prices(self, symbols: List[str]) -> Dict[str, float]:
        """
        Get current market prices for a list of underlying symbols.
        Default implementation queries them one by one.
        """
        prices = {}
        for s in symbols:
            prices[s] = await self.get_underlying_price(s)
        return prices

    @abstractmethod
    async def get_options_chain(
        self,
        symbol: str,
        right: str = "C",
        min_dte: int = 0,
        max_dte: int = 365,
    ) -> List[Dict[str, Any]]:
        """
        Get options chain for a symbol.
        Returns list of option contracts with: conId, symbol, expiration, strike, right, bid, ask, last
        """
        pass

    @abstractmethod
    async def get_option_greeks(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str = "C",
    ) -> Dict[str, float]:
        """
        Get Greeks for a specific option contract.
        Returns dict with: delta, gamma, theta, vega, iv
        """
        pass

    @abstractmethod
    async def get_options_expirations(self, symbol: str) -> List[str]:
        """Get available option expiration dates for a symbol. Returns list of YYYYMMDD strings."""
        pass

    @abstractmethod
    async def get_options_strikes(self, symbol: str, expiration: str) -> List[float]:
        """Get available strikes for a symbol and expiration."""
        pass

    # ── Order Execution ──────────────────────────────────────────

    @abstractmethod
    async def place_vertical_spread(
        self,
        symbol: str,
        long_strike: float,
        short_strike: float,
        right: str,
        expiration: str,
        quantity: int,
        limit_price: float,
        action: str = "BUY",  # BUY to open, SELL to close
    ) -> int:
        """
        Place a vertical spread order.
        Returns order ID.
        """
        pass

    @abstractmethod
    async def place_order(self, order_data: Dict[str, Any]) -> int:
        """
        Place an order using raw or dictionary based data.
        Returns order ID.
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: int) -> bool:
        """Cancel a pending order. Returns True on success."""
        pass

    @abstractmethod
    async def modify_order_price(self, order_id: int, new_price: float) -> bool:
        """Modify the limit price of a pending order."""
        pass

    @abstractmethod
    async def get_order_status(self, order_id: int) -> Dict[str, Any]:
        """
        Get status of an order.
        Returns dict with: status, filled_qty, avg_fill_price, remaining_qty
        """
        pass

    # ── Positions ────────────────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get all current positions.
        Returns list of dicts with: conId, symbol, quantity, avg_cost, market_value, unrealized_pnl
        """
        pass

    @abstractmethod
    async def get_portfolio(self) -> List[Dict[str, Any]]:
        """
        Get portfolio items with market values.
        Returns list of portfolio entries.
        """
        pass

    # ── Event Callbacks ──────────────────────────────────────────

    @abstractmethod
    def on_order_status(self, callback: Callable) -> None:
        """Register callback for order status updates."""
        pass

    @abstractmethod
    def on_position_update(self, callback: Callable) -> None:
        """Register callback for position updates."""
        pass

    @abstractmethod
    def on_disconnect(self, callback: Callable) -> None:
        """Register callback for disconnect events."""
        pass

    # ── Utilities ────────────────────────────────────────────────

    @abstractmethod
    async def qualify_contract(self, **kwargs) -> Optional[Dict[str, Any]]:
        """Verify and qualify a contract with the broker."""
        pass

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Broker-aware sleep (processes events during wait)."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the broker."""
        pass

    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """Whether this is a paper trading connection."""
        pass
