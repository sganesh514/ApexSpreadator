"""
ApexSpreadator — Interactive Brokers Implementation
Concrete broker implementation using ib_insync.
"""
import asyncio
from typing import List, Dict, Optional, Any, Callable
from datetime import datetime

from utils import get_logger, calculate_dte
from core.broker_base import BrokerBase

logger = get_logger("IBKR")


class IBKRBroker(BrokerBase):
    """
    Interactive Brokers implementation using ib_insync.
    Connects to TWS or IB Gateway via socket API.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib = None
        self._connected = False
        self._order_callbacks: List[Callable] = []
        self._position_callbacks: List[Callable] = []
        self._disconnect_callbacks: List[Callable] = []

    async def connect(self) -> bool:
        """Connect to IBKR TWS/Gateway."""
        try:
            from ib_insync import IB
            self._ib = IB()

            # Register event handlers
            self._ib.disconnectedEvent += self._on_disconnected
            self._ib.orderStatusEvent += self._on_order_status
            self._ib.newOrderEvent += self._on_new_order

            logger.info(f"Connecting to IBKR at {self._host}:{self._port} (clientId={self._client_id})...")
            await self._ib.connectAsync(self._host, self._port, clientId=self._client_id)

            if self._ib.isConnected():
                self._connected = True
                accounts = self._ib.managedAccounts()
                logger.info(f"✅ Connected to IBKR. Accounts: {accounts}")
                logger.info(f"   Mode: {'PAPER' if self.is_paper else 'LIVE'} trading")
                return True
            else:
                logger.error("❌ Failed to connect to IBKR")
                return False

        except Exception as e:
            logger.error(f"❌ Connection error: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from IBKR."""
        if self._ib and self._ib.isConnected():
            logger.info("Disconnecting from IBKR...")
            self._ib.disconnect()
            self._connected = False
            logger.info("Disconnected.")

    def is_connected(self) -> bool:
        """Check if connected to IBKR."""
        return self._ib is not None and self._ib.isConnected()

    # ── Account ──────────────────────────────────────────────────

    async def get_account_summary(self) -> Dict[str, float]:
        """Get account summary from IBKR."""
        try:
            summary = self._ib.accountSummary()
            result = {
                "balance": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
            }

            for item in summary:
                if item.tag == "TotalCashValue":
                    result["balance"] = float(item.value)
                elif item.tag == "NetLiquidation":
                    result["equity"] = float(item.value)
                elif item.tag == "BuyingPower":
                    result["buying_power"] = float(item.value)
                elif item.tag == "UnrealizedPnL":
                    result["unrealized_pnl"] = float(item.value)
                elif item.tag == "RealizedPnL":
                    result["realized_pnl"] = float(item.value)

            return result
        except Exception as e:
            logger.error(f"Error getting account summary: {e}")
            return {"balance": 0, "equity": 0, "buying_power": 0, "unrealized_pnl": 0, "realized_pnl": 0}

    # ── Market Data ──────────────────────────────────────────────

    async def get_underlying_price(self, symbol: str) -> float:
        """Get current market price for a stock."""
        try:
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            tickers = self._ib.reqTickers(contract)
            self._ib.sleep(1)  # Wait for data

            if tickers and tickers[0].marketPrice():
                price = tickers[0].marketPrice()
                logger.debug(f"{symbol} price: ${price:.2f}")
                return float(price)
            else:
                logger.warning(f"No price data for {symbol}")
                return 0.0
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            return 0.0

    async def get_options_expirations(self, symbol: str) -> List[str]:
        """Get available option expiration dates."""
        try:
            from ib_insync import Stock
            stock = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(stock)

            chains = self._ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)

            expirations = set()
            for chain in chains:
                if chain.exchange == "SMART":
                    expirations.update(chain.expirations)

            # Sort and return
            sorted_exps = sorted(expirations)
            logger.debug(f"{symbol} expirations: {len(sorted_exps)} available")
            return sorted_exps

        except Exception as e:
            logger.error(f"Error getting expirations for {symbol}: {e}")
            return []

    async def get_options_strikes(self, symbol: str, expiration: str) -> List[float]:
        """Get available strikes for a symbol and expiration."""
        try:
            from ib_insync import Stock
            stock = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(stock)

            chains = self._ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)

            strikes = set()
            for chain in chains:
                if chain.exchange == "SMART":
                    strikes.update(chain.strikes)

            sorted_strikes = sorted(strikes)
            logger.debug(f"{symbol} {expiration} strikes: {len(sorted_strikes)} available")
            return sorted_strikes

        except Exception as e:
            logger.error(f"Error getting strikes for {symbol}: {e}")
            return []

    async def get_options_chain(
        self,
        symbol: str,
        right: str = "C",
        min_dte: int = 0,
        max_dte: int = 365,
    ) -> List[Dict[str, Any]]:
        """Get filtered options chain with market data."""
        try:
            expirations = await self.get_options_expirations(symbol)
            current_price = await self.get_underlying_price(symbol)

            # Filter expirations by DTE
            valid_exps = []
            for exp in expirations:
                dte = calculate_dte(exp)
                if min_dte <= dte <= max_dte:
                    valid_exps.append((exp, dte))

            if not valid_exps:
                logger.warning(f"No valid expirations for {symbol} in DTE range {min_dte}-{max_dte}")
                return []

            # Get strikes near ATM
            strikes = await self.get_options_strikes(symbol, valid_exps[0][0])
            if not strikes:
                return []

            # Filter to ATM ± range
            atm_strike = min(strikes, key=lambda s: abs(s - current_price))
            atm_idx = strikes.index(atm_strike)
            nearby_strikes = strikes[max(0, atm_idx - 3): atm_idx + 4]

            contracts = []
            from ib_insync import Option

            for exp, dte in valid_exps[:6]:  # Limit to 6 expirations to manage API load
                for strike in nearby_strikes:
                    option = Option(symbol, exp, strike, right, "SMART")
                    try:
                        self._ib.qualifyContracts(option)
                        contracts.append({
                            "contract": option,
                            "con_id": option.conId,
                            "symbol": symbol,
                            "expiration": exp,
                            "strike": strike,
                            "right": right,
                            "dte": dte,
                        })
                    except Exception:
                        continue

            # Request market data for all contracts
            result = []
            for item in contracts:
                try:
                    ticker = self._ib.reqTickers(item["contract"])[0]
                    self._ib.sleep(0.5)

                    greeks = {}
                    if ticker.modelGreeks:
                        greeks = {
                            "delta": ticker.modelGreeks.delta or 0,
                            "gamma": ticker.modelGreeks.gamma or 0,
                            "theta": ticker.modelGreeks.theta or 0,
                            "vega": ticker.modelGreeks.vega or 0,
                            "iv": ticker.modelGreeks.impliedVol or 0,
                        }

                    item.update({
                        "bid": ticker.bid if ticker.bid and ticker.bid > 0 else 0,
                        "ask": ticker.ask if ticker.ask and ticker.ask > 0 else 0,
                        "mid": (ticker.bid + ticker.ask) / 2 if ticker.bid and ticker.ask and ticker.bid > 0 else 0,
                        "last": ticker.last if ticker.last and ticker.last > 0 else 0,
                        **greeks,
                    })
                    del item["contract"]  # Remove non-serializable contract object
                    result.append(item)
                except Exception as e:
                    logger.debug(f"Skipping contract {item.get('symbol')} {item.get('strike')}: {e}")
                    continue

            logger.info(f"Options chain for {symbol}: {len(result)} contracts loaded")
            return result

        except Exception as e:
            logger.error(f"Error getting options chain for {symbol}: {e}")
            return []

    async def get_option_greeks(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str = "C",
    ) -> Dict[str, float]:
        """Get Greeks for a specific option."""
        try:
            from ib_insync import Option
            option = Option(symbol, expiration, strike, right, "SMART")
            self._ib.qualifyContracts(option)

            ticker = self._ib.reqTickers(option)[0]
            self._ib.sleep(1)

            if ticker.modelGreeks:
                return {
                    "delta": ticker.modelGreeks.delta or 0,
                    "gamma": ticker.modelGreeks.gamma or 0,
                    "theta": ticker.modelGreeks.theta or 0,
                    "vega": ticker.modelGreeks.vega or 0,
                    "iv": ticker.modelGreeks.impliedVol or 0,
                }
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "iv": 0}

        except Exception as e:
            logger.error(f"Error getting Greeks for {symbol} {strike}{right} {expiration}: {e}")
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "iv": 0}

    # ── Order Execution ──────────────────────────────────────────

    async def place_vertical_spread(
        self,
        symbol: str,
        long_strike: float,
        short_strike: float,
        right: str,
        expiration: str,
        quantity: int,
        limit_price: float,
        action: str = "BUY",
    ) -> int:
        """
        Place a vertical spread order as a BAG combo.
        BUY action = open the spread (buy long_strike, sell short_strike)
        SELL action = close the spread (sell long_strike, buy short_strike)
        """
        try:
            from ib_insync import Contract, ComboLeg, Order, Option, TagValue

            # Qualify individual legs to get conIds
            long_option = Option(symbol, expiration, long_strike, right, "SMART")
            short_option = Option(symbol, expiration, short_strike, right, "SMART")
            self._ib.qualifyContracts(long_option)
            self._ib.qualifyContracts(short_option)

            if not long_option.conId or not short_option.conId:
                logger.error(f"Failed to qualify options for {symbol} {long_strike}/{short_strike}{right}")
                return -1

            # Build combo legs
            # For BUY (opening): BUY long leg, SELL short leg
            # For SELL (closing): SELL long leg, BUY short leg
            long_leg = ComboLeg()
            long_leg.conId = long_option.conId
            long_leg.ratio = 1
            long_leg.action = "BUY" if action == "BUY" else "SELL"
            long_leg.exchange = "SMART"

            short_leg = ComboLeg()
            short_leg.conId = short_option.conId
            short_leg.ratio = 1
            short_leg.action = "SELL" if action == "BUY" else "BUY"
            short_leg.exchange = "SMART"

            # Build BAG contract
            combo = Contract()
            combo.symbol = symbol
            combo.secType = "BAG"
            combo.currency = "USD"
            combo.exchange = "SMART"
            combo.comboLegs = [long_leg, short_leg]

            # Build order
            order = Order()
            order.action = action
            order.orderType = "LMT"
            order.totalQuantity = quantity
            order.lmtPrice = round(limit_price, 2)
            order.smartComboRoutingParams = [TagValue("NonGuaranteed", "1")]
            order.transmit = True

            # Place order
            trade = self._ib.placeOrder(combo, order)
            order_id = trade.order.orderId

            logger.info(
                f"📝 Placed {action} vertical spread: {symbol} {long_strike}/{short_strike}{right} "
                f"{expiration} x{quantity} @ ${limit_price:.2f} "
                f"(Order #{order_id})"
            )

            return order_id

        except Exception as e:
            logger.error(f"Error placing vertical spread order: {e}")
            return -1

    async def cancel_order(self, order_id: int) -> bool:
        """Cancel a pending order."""
        try:
            open_trades = self._ib.openTrades()
            for trade in open_trades:
                if trade.order.orderId == order_id:
                    self._ib.cancelOrder(trade.order)
                    logger.info(f"Cancelled order #{order_id}")
                    return True
            logger.warning(f"Order #{order_id} not found in open trades")
            return False
        except Exception as e:
            logger.error(f"Error cancelling order #{order_id}: {e}")
            return False

    async def modify_order_price(self, order_id: int, new_price: float) -> bool:
        """Modify limit price of a pending order."""
        try:
            open_trades = self._ib.openTrades()
            for trade in open_trades:
                if trade.order.orderId == order_id:
                    trade.order.lmtPrice = round(new_price, 2)
                    self._ib.placeOrder(trade.contract, trade.order)
                    logger.info(f"Modified order #{order_id} price to ${new_price:.2f}")
                    return True
            return False
        except Exception as e:
            logger.error(f"Error modifying order #{order_id}: {e}")
            return False

    async def get_order_status(self, order_id: int) -> Dict[str, Any]:
        """Get current status of an order."""
        try:
            open_trades = self._ib.openTrades()
            for trade in open_trades:
                if trade.order.orderId == order_id:
                    return {
                        "status": trade.orderStatus.status,
                        "filled_qty": trade.orderStatus.filled,
                        "avg_fill_price": trade.orderStatus.avgFillPrice,
                        "remaining_qty": trade.orderStatus.remaining,
                    }

            # Check completed orders
            fills = self._ib.fills()
            for fill in fills:
                if fill.execution.orderId == order_id:
                    return {
                        "status": "Filled",
                        "filled_qty": fill.execution.shares,
                        "avg_fill_price": fill.execution.price,
                        "remaining_qty": 0,
                    }

            return {"status": "Unknown", "filled_qty": 0, "avg_fill_price": 0, "remaining_qty": 0}

        except Exception as e:
            logger.error(f"Error getting order status #{order_id}: {e}")
            return {"status": "Error", "filled_qty": 0, "avg_fill_price": 0, "remaining_qty": 0}

    # ── Positions ────────────────────────────────────────────────

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get all current positions."""
        try:
            positions = self._ib.positions()
            result = []
            for pos in positions:
                result.append({
                    "con_id": pos.contract.conId,
                    "symbol": pos.contract.symbol,
                    "sec_type": pos.contract.secType,
                    "expiration": getattr(pos.contract, "lastTradeDateOrContractMonth", ""),
                    "strike": getattr(pos.contract, "strike", 0),
                    "right": getattr(pos.contract, "right", ""),
                    "quantity": pos.position,
                    "avg_cost": pos.avgCost,
                })
            return result
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    async def get_portfolio(self) -> List[Dict[str, Any]]:
        """Get portfolio with market values."""
        try:
            portfolio = self._ib.portfolio()
            result = []
            for item in portfolio:
                result.append({
                    "con_id": item.contract.conId,
                    "symbol": item.contract.symbol,
                    "sec_type": item.contract.secType,
                    "quantity": item.position,
                    "market_price": item.marketPrice,
                    "market_value": item.marketValue,
                    "avg_cost": item.averageCost,
                    "unrealized_pnl": item.unrealizedPNL,
                    "realized_pnl": item.realizedPNL,
                })
            return result
        except Exception as e:
            logger.error(f"Error getting portfolio: {e}")
            return []

    # ── Event Callbacks ──────────────────────────────────────────

    def on_order_status(self, callback: Callable) -> None:
        self._order_callbacks.append(callback)

    def on_position_update(self, callback: Callable) -> None:
        self._position_callbacks.append(callback)

    def on_disconnect(self, callback: Callable) -> None:
        self._disconnect_callbacks.append(callback)

    def _on_disconnected(self):
        """Handle disconnect event."""
        self._connected = False
        logger.warning("⚠️ Disconnected from IBKR!")
        for cb in self._disconnect_callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"Disconnect callback error: {e}")

    def _on_order_status(self, trade):
        """Handle order status update."""
        for cb in self._order_callbacks:
            try:
                cb(trade)
            except Exception as e:
                logger.error(f"Order status callback error: {e}")

    def _on_new_order(self, trade):
        """Handle new order event."""
        logger.debug(f"New order event: {trade.order.orderId}")

    # ── Utilities ────────────────────────────────────────────────

    async def qualify_contract(self, **kwargs) -> Optional[Dict[str, Any]]:
        """Qualify a contract with IBKR."""
        try:
            from ib_insync import Option, Stock
            sec_type = kwargs.get("sec_type", "OPT")

            if sec_type == "STK":
                contract = Stock(kwargs["symbol"], "SMART", "USD")
            else:
                contract = Option(
                    kwargs["symbol"],
                    kwargs["expiration"],
                    kwargs["strike"],
                    kwargs.get("right", "C"),
                    "SMART"
                )

            self._ib.qualifyContracts(contract)
            return {"con_id": contract.conId, "symbol": contract.symbol}
        except Exception as e:
            logger.error(f"Error qualifying contract: {e}")
            return None

    async def sleep(self, seconds: float) -> None:
        """ib_insync-aware sleep."""
        if self._ib:
            self._ib.sleep(seconds)
        else:
            await asyncio.sleep(seconds)

    @property
    def name(self) -> str:
        return "Interactive Brokers"

    @property
    def is_paper(self) -> bool:
        """Paper trading uses port 7497, live uses 7496."""
        return self._port == 7497

    @property
    def ib(self):
        """Direct access to ib_insync IB instance for advanced usage."""
        return self._ib
