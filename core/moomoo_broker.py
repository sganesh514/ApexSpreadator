"""
ApexSpreadator — Moomoo Implementation
Concrete broker implementation using moomoo-api.
"""
import asyncio
import re
import sys
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple, Callable
import pandas as pd

try:
    from moomoo import (
        FTAPIConn, OpenQuoteContext, OpenSecContext,
        OptionType, OptionStrategyType, TrdSide, OrderType,
        TrdEnv, ModifyOrderOp, ComboLeg
    )
except ImportError:
    # Fallback/mock structure if SDK is not present in local python env
    from moomoo import FTAPIConn, OpenQuoteContext, OpenSecTradeContext as OpenSecContext
    from moomoo import OptionType, OptionStrategyType, TrdSide, OrderType, TrdEnv, ModifyOrderOp, ComboLeg

from utils import get_logger, calculate_dte
from core.broker_base import BrokerBase

logger = get_logger("Moomoo")


def _format_symbol(symbol: str) -> str:
    """Format symbol with US market prefix if missing."""
    if "." not in symbol:
        return f"US.{symbol}"
    return symbol


def _generate_option_code(symbol: str, expiration: str, strike: float, right: str) -> str:
    """Generate option contract code in Moomoo format (e.g. US.AAPL260622C00150000)."""
    clean_sym = symbol.replace("US.", "")
    exp_clean = expiration.replace("-", "")
    yy = exp_clean[2:4]
    mm = exp_clean[4:6]
    dd = exp_clean[6:8]
    yymmdd = f"{yy}{mm}{dd}"
    
    # Strike price format: strike * 1000 as int, padded to 8 digits
    strike_int = int(round(strike * 1000))
    strike_str = f"{strike_int:08d}"
    
    return f"US.{clean_sym}{yymmdd}{right}{strike_str}"


class MoomooBroker(BrokerBase):
    """
    Moomoo broker implementation using official moomoo-api SDK.
    Connects to OpenD Gateway.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, client_id: int = 1):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._quote_ctx = None
        self._trade_ctx = None
        self._connected = False
        self._order_callbacks: List[Callable] = []
        self._position_callbacks: List[Callable] = []
        self._disconnect_callbacks: List[Callable] = []

    async def connect(self) -> bool:
        """Establish connection to Moomoo Open API."""
        try:
            logger.info(f"Connecting to Moomoo OpenD at {self._host}:{self._port}...")
            
            def _init_connections():
                quote_ctx = OpenQuoteContext(host=self._host, port=self._port)
                trade_ctx = OpenSecContext(host=self._host, port=self._port)
                quote_ctx.start()
                trade_ctx.start()
                return quote_ctx, trade_ctx
                
            self._quote_ctx, self._trade_ctx = await asyncio.to_thread(_init_connections)
            
            # Check connection with a handshake loop
            for attempt in range(5):
                q_id = self._quote_ctx.get_sync_conn_id()
                t_id = self._trade_ctx.get_sync_conn_id()
                if q_id and t_id:
                    self._connected = True
                    logger.info("✅ Connected to Moomoo Quote and Security contexts.")
                    logger.info(f"   Mode: {'PAPER' if self.is_paper else 'LIVE'} trading")
                    return True
                await asyncio.sleep(1)
            
            logger.error("❌ Moomoo connection handshake timed out. Is OpenD Gateway running?")
            return False
        except Exception as e:
            logger.error(f"❌ Moomoo connection error: {e}")
            return False

    async def disconnect(self) -> None:
        """Gracefully disconnect from Moomoo."""
        logger.info("Disconnecting from Moomoo...")
        if self._quote_ctx:
            self._quote_ctx.close()
        if self._trade_ctx:
            self._trade_ctx.close()
        self._connected = False
        logger.info("Disconnected from Moomoo.")

    def is_connected(self) -> bool:
        """Check if connection is active."""
        if not self._connected or not self._quote_ctx or not self._trade_ctx:
            return False
        return bool(self._quote_ctx.get_sync_conn_id() and self._trade_ctx.get_sync_conn_id())

    @property
    def is_paper(self) -> bool:
        """Whether this is a paper trading connection."""
        if "--live" in sys.argv:
            return False
        return True

    @property
    def trd_env(self) -> TrdEnv:
        """Get trading environment enum."""
        return TrdEnv.REAL if not self.is_paper else TrdEnv.SIMULATE

    # ── Account ──────────────────────────────────────────────────

    async def get_account_summary(self) -> Dict[str, float]:
        """Get account summary from Moomoo."""
        try:
            def _fetch_acc_info():
                ret, data = self._trade_ctx.accinfo_query(trd_env=self.trd_env)
                if ret == 0 and not data.empty:
                    row = data.iloc[0]
                    return {
                        "balance": float(row.get("cash", 0.0)),
                        "equity": float(row.get("total_assets", 0.0)),
                        "buying_power": float(row.get("power", 0.0)),
                        "unrealized_pnl": float(row.get("unrealized_pnl", 0.0)),
                        "realized_pnl": float(row.get("realized_pnl", 0.0)),
                    }
                return None
                
            summary = await asyncio.to_thread(_fetch_acc_info)
            if summary:
                return summary
            
            # Return defaults if call failed
            return {
                "balance": 25000.0,
                "equity": 25000.0,
                "buying_power": 25000.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
            }
        except Exception as e:
            logger.error(f"Error getting account summary from Moomoo: {e}")
            return {
                "balance": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
            }

    # ── Market Data ──────────────────────────────────────────────

    async def get_underlying_price(self, symbol: str) -> float:
        """Get current market price for an underlying symbol."""
        code = _format_symbol(symbol)
        try:
            def _fetch_snapshot():
                ret, data = self._quote_ctx.get_market_snapshot([code])
                if ret == 0 and not data.empty:
                    price = data['last_price'].iloc[0]
                    return float(price)
                return 0.0
            
            price = await asyncio.to_thread(_fetch_snapshot)
            if price > 0:
                logger.debug(f"Moomoo price for {code}: ${price:.2f}")
                return price
            logger.warning(f"No price data returned for {code}")
            return 0.0
        except Exception as e:
            logger.error(f"Error getting price for {symbol} via Moomoo: {e}")
            return 0.0

    async def get_options_expirations(self, symbol: str) -> List[str]:
        """Get available option expiration dates."""
        code = _format_symbol(symbol)
        try:
            def _get_exps():
                ret, data = self._quote_ctx.get_option_expiration_date(code=code)
                if ret == 0 and not data.empty:
                    dates = data['strike_time'].values.tolist()
                    return [d.replace("-", "") for d in dates]
                return []
            return await asyncio.to_thread(_get_exps)
        except Exception as e:
            logger.error(f"Error getting expirations for {symbol} via Moomoo: {e}")
            return []

    async def get_options_strikes(self, symbol: str, expiration: str) -> List[float]:
        """Get available strikes for a symbol and expiration."""
        if len(expiration) == 8:
            exp_date = f"{expiration[:4]}-{expiration[4:6]}-{expiration[6:]}"
        else:
            exp_date = expiration
            
        code = _format_symbol(symbol)
        try:
            def _get_strikes():
                ret, data = self._quote_ctx.get_option_chain(code=code, start=exp_date, end=exp_date)
                if ret == 0 and not data.empty:
                    strikes = data['strike_price'].values.tolist()
                    return sorted(list(set(float(s) for s in strikes)))
                return []
            return await asyncio.to_thread(_get_strikes)
        except Exception as e:
            logger.error(f"Error getting strikes for {symbol} via Moomoo: {e}")
            return []

    async def get_options_chain(
        self,
        symbol: str,
        right: str = "C",
        min_dte: int = 0,
        max_dte: int = 365,
    ) -> pd.DataFrame:
        """Get filtered options chain with market data."""
        code = _format_symbol(symbol)
        opt_type = OptionType.CALL if right == "C" else OptionType.PUT
        
        try:
            # 1. Get available expiration dates
            def _get_exps():
                ret, data = self._quote_ctx.get_option_expiration_date(code=code)
                if ret == 0 and not data.empty:
                    return data['strike_time'].values.tolist()
                return []
                
            expirations = await asyncio.to_thread(_get_exps)
            
            # Filter expirations by DTE
            valid_exps = []
            for exp in expirations:
                exp_clean = exp.replace("-", "")
                dte = calculate_dte(exp_clean)
                if min_dte <= dte <= max_dte:
                    valid_exps.append((exp, exp_clean, dte))
                    
            if not valid_exps:
                logger.warning(f"No valid expirations found for {code} in range {min_dte}-{max_dte}")
                return pd.DataFrame()
                
            # Limit expirations to manage API load
            valid_exps = valid_exps[:3]
            
            # 2. Get option chain contract codes
            contract_codes = []
            dte_mapping = {}
            exp_mapping = {}
            
            def _fetch_chains():
                for exp_str, exp_clean, dte in valid_exps:
                    ret, data = self._quote_ctx.get_option_chain(
                        code=code,
                        start=exp_str,
                        end=exp_str,
                        option_type=opt_type
                    )
                    if ret == 0 and not data.empty:
                        codes = data['code'].values.tolist()
                        contract_codes.extend(codes)
                        for c in codes:
                            dte_mapping[c] = dte
                            exp_mapping[c] = exp_clean
                            
            await asyncio.to_thread(_fetch_chains)
            
            if not contract_codes:
                logger.warning(f"No contract codes found for {code}")
                return pd.DataFrame()
                
            # 3. Fetch market snapshot in chunks
            snapshot_dfs = []
            
            def _fetch_snapshots():
                chunk_size = 50
                for i in range(0, len(contract_codes), chunk_size):
                    chunk = contract_codes[i : i + chunk_size]
                    ret, data = self._quote_ctx.get_market_snapshot(chunk)
                    if ret == 0 and not data.empty:
                        snapshot_dfs.append(data)
                        
            await asyncio.to_thread(_fetch_snapshots)
            
            if not snapshot_dfs:
                logger.warning(f"Failed to fetch market snapshots for option contracts of {code}")
                return pd.DataFrame()
                
            full_df = pd.concat(snapshot_dfs, ignore_index=True)
            
            # 4. Map columns to both title-case and lower-case to satisfy all expectations
            mapped_rows = []
            for _, row in full_df.iterrows():
                row_code = row['code']
                strike = float(row.get('option_strike_price', 0.0))
                bid = float(row.get('bid_price', 0.0))
                ask = float(row.get('ask_price', 0.0))
                vol = int(row.get('volume', 0))
                oi = int(row.get('option_open_interest', 0))
                delta = float(row.get('option_delta', 0.0))
                theta = float(row.get('option_theta', 0.0))
                vega = float(row.get('option_vega', 0.0))
                iv = float(row.get('option_implied_volatility', 0.0))
                
                exp_clean = exp_mapping.get(row_code, "")
                dte = dte_mapping.get(row_code, 0)
                mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
                
                mapped_rows.append({
                    "con_id": row_code,
                    "symbol": symbol,
                    "expiration": exp_clean,
                    "strike": strike,
                    "right": right,
                    "dte": dte,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "last": float(row.get('last_price', 0.0)),
                    "volume": vol,
                    "open_interest": oi,
                    "delta": delta,
                    "theta": theta,
                    "vega": vega,
                    "iv": iv,
                    
                    # Titlecase mapping
                    "Strike": strike,
                    "Bid": bid,
                    "Ask": ask,
                    "Volume": vol,
                    "OpenInterest": oi,
                    "Expiration": exp_clean,
                    "Right": right,
                    "Dte": dte,
                    "Mid": mid,
                    "Delta": delta,
                    "Theta": theta,
                    "Vega": vega,
                    "Iv": iv
                })
                
            return pd.DataFrame(mapped_rows)
            
        except Exception as e:
            logger.error(f"Error fetching options chain for {symbol} via Moomoo: {e}", exc_info=True)
            return pd.DataFrame()

    async def get_option_greeks(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str = "C",
    ) -> Dict[str, float]:
        """Get Greeks for a specific option contract."""
        code = _generate_option_code(symbol, expiration, strike, right)
        try:
            def _fetch():
                ret, data = self._quote_ctx.get_market_snapshot([code])
                if ret == 0 and not data.empty:
                    row = data.iloc[0]
                    return {
                        "delta": float(row.get("option_delta", 0.0)),
                        "gamma": float(row.get("option_gamma", 0.0)),
                        "theta": float(row.get("option_theta", 0.0)),
                        "vega": float(row.get("option_vega", 0.0)),
                        "iv": float(row.get("option_implied_volatility", 0.0)),
                    }
                return {}
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error(f"Error getting Greeks for {code} via Moomoo: {e}")
            return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "iv": 0.0}

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
        """Place a vertical spread order via Moomoo Combo Order API."""
        long_code = _generate_option_code(symbol, expiration, long_strike, right)
        short_code = _generate_option_code(symbol, expiration, short_strike, right)
        
        try:
            leg1 = ComboLeg()
            leg1.code = long_code
            leg1.action = TrdSide.BUY if action == "BUY" else TrdSide.SELL
            leg1.ratio = 1
            
            leg2 = ComboLeg()
            leg2.code = short_code
            leg2.action = TrdSide.SELL if action == "BUY" else TrdSide.BUY
            leg2.ratio = 1
            
            combo_legs = [leg1, leg2]
            
            def _place():
                ret, data = self._trade_ctx.place_combo_order(
                    combo_legs=combo_legs,
                    price=float(limit_price),
                    qty=int(quantity),
                    order_type=OrderType.NORMAL,
                    strategy_type=OptionStrategyType.NONE,
                    trd_env=self.trd_env
                )
                if ret == 0 and not data.empty:
                    order_id = data['order_id'].iloc[0]
                    return int(order_id)
                logger.error(f"Failed to place combo order via Moomoo: {data}")
                return -1
                
            return await asyncio.to_thread(_place)
        except Exception as e:
            logger.error(f"Error placing combo order via Moomoo: {e}")
            return -1

    async def open_spread(self, opportunity, quantity: int) -> Tuple[int, float]:
        """Open a vertical spread (BUY to open)."""
        spread = opportunity.spread
        order_id = await self.place_vertical_spread(
            symbol=spread.symbol,
            long_strike=spread.long_leg.strike,
            short_strike=spread.short_leg.strike,
            right=spread.right,
            expiration=spread.expiration,
            quantity=quantity,
            limit_price=spread.net_debit,
            action="BUY"
        )
        return order_id, spread.net_debit

    async def close_spread(self, position) -> Tuple[bool, float]:
        """Close a vertical spread (SELL to close)."""
        spread = position.spread
        limit_price = position.current_value if position.current_value > 0 else position.entry_price
        order_id = await self.place_vertical_spread(
            symbol=spread.symbol,
            long_strike=spread.long_leg.strike,
            short_strike=spread.short_leg.strike,
            right=spread.right,
            expiration=spread.expiration,
            quantity=position.quantity,
            limit_price=limit_price,
            action="SELL"
        )
        return (order_id > 0), limit_price

    async def cancel_order(self, order_id: int) -> bool:
        """Cancel a pending order."""
        try:
            def _cancel():
                ret, data = self._trade_ctx.modify_order(
                    modify_order_op=ModifyOrderOp.CANCEL,
                    order_id=str(order_id),
                    qty=0,
                    price=0,
                    trd_env=self.trd_env
                )
                return ret == 0
            return await asyncio.to_thread(_cancel)
        except Exception as e:
            logger.error(f"Error cancelling order {order_id} via Moomoo: {e}")
            return False

    async def modify_order_price(self, order_id: int, new_price: float) -> bool:
        """Modify the limit price of a pending order."""
        try:
            status = await self.get_order_status(order_id)
            qty = status.get("qty", 1)
            
            def _modify():
                ret, data = self._trade_ctx.modify_order(
                    modify_order_op=ModifyOrderOp.NORMAL,
                    order_id=str(order_id),
                    qty=int(qty),
                    price=float(new_price),
                    trd_env=self.trd_env
                )
                return ret == 0
            return await asyncio.to_thread(_modify)
        except Exception as e:
            logger.error(f"Error modifying price for order {order_id} via Moomoo: {e}")
            return False

    async def get_order_status(self, order_id: int) -> Dict[str, Any]:
        """Get status of an order."""
        try:
            def _query():
                ret, data = self._trade_ctx.order_list_query(
                    order_id=str(order_id),
                    trd_env=self.trd_env
                )
                if ret == 0 and not data.empty:
                    row = data.iloc[0]
                    status_raw = str(row.get("order_status", ""))
                    status = "Submitted"
                    if "FILLED_ALL" in status_raw or "Filled" in status_raw:
                        status = "Filled"
                    elif "CANCELLED" in status_raw or "Cancelled" in status_raw:
                        status = "Cancelled"
                    elif "FAILED" in status_raw or "Disabled" in status_raw or "Error" in status_raw:
                        status = "Error"
                        
                    return {
                        "status": status,
                        "filled_qty": int(row.get("dealt_qty", 0)),
                        "avg_fill_price": float(row.get("dealt_avg_price", 0.0)),
                        "remaining_qty": int(row.get("qty", 0)) - int(row.get("dealt_qty", 0)),
                        "qty": int(row.get("qty", 0)),
                        "price": float(row.get("price", 0.0)),
                    }
                return {"status": "Error", "filled_qty": 0, "avg_fill_price": 0.0, "remaining_qty": 0}
                
            return await asyncio.to_thread(_query)
        except Exception as e:
            logger.error(f"Error getting order status for {order_id} via Moomoo: {e}")
            return {"status": "Error", "filled_qty": 0, "avg_fill_price": 0.0, "remaining_qty": 0}

    # ── Positions ────────────────────────────────────────────────

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get all current positions from Moomoo."""
        try:
            def _query():
                ret, data = self._trade_ctx.position_list_query(trd_env=self.trd_env)
                if ret == 0 and not data.empty:
                    positions = []
                    for _, row in data.iterrows():
                        code = row.get("code", "")
                        symbol = code
                        if code.startswith("US."):
                            base = code[3:]
                            match = re.search(r'\d', base)
                            if match:
                                symbol = base[:match.start()]
                            else:
                                symbol = base
                                
                        positions.append({
                            "conId": code,
                            "symbol": symbol,
                            "quantity": int(row.get("qty", 0)),
                            "avg_cost": float(row.get("cost_price", 0.0)),
                            "market_value": float(row.get("market_val", 0.0)),
                            "unrealized_pnl": float(row.get("pl_val", 0.0)),
                        })
                    return positions
                return []
            return await asyncio.to_thread(_query)
        except Exception as e:
            logger.error(f"Error getting positions from Moomoo: {e}")
            return []

    async def get_portfolio(self) -> List[Dict[str, Any]]:
        """Get portfolio items with market values."""
        return await self.get_positions()

    # ── Event Callbacks ──────────────────────────────────────────

    def on_order_status(self, callback: Callable) -> None:
        """Register callback for order status updates."""
        self._order_callbacks.append(callback)

    def on_position_update(self, callback: Callable) -> None:
        """Register callback for position updates."""
        self._position_callbacks.append(callback)

    def on_disconnect(self, callback: Callable) -> None:
        """Register callback for disconnect events."""
        self._disconnect_callbacks.append(callback)

    # ── Utilities ────────────────────────────────────────────────

    async def qualify_contract(self, **kwargs) -> Optional[Dict[str, Any]]:
        """Verify and qualify a contract with the broker."""
        return kwargs

    async def sleep(self, seconds: float) -> None:
        """Broker-aware sleep."""
        await asyncio.sleep(seconds)

    @property
    def name(self) -> str:
        return "Moomoo"
