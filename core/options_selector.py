"""
ApexSpreadator — Options Selector & Risk Filter (Translation Layer)
Translates market structure retest signals into target Vertical Debit Spreads,
selects strikes matching structural targets, and enforces the 2.5:1 Risk-to-Reward filter.
"""
import math
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from models import VerticalSpread, OptionLeg, Zone
from utils import black_scholes_call, black_scholes_put, get_logger

logger = get_logger("OptionsSelector")


class BrokerDataError(Exception):
    """Exception raised when broker data feed has an error or is missing options chain."""
    pass


class OptionsSelector:
    """
    Translates Underlying retests to vertical spreads and checks R:R ratios.
    """

    def __init__(self, min_rr_threshold: float = 2.5):
        self.min_rr = min_rr_threshold
        self.risk_filter_logs: List[Dict[str, Any]] = []

    def select_spread(
        self,
        symbol: str,
        direction: str,
        underlying_price: float,
        target_tp: float,
        target_sl: float,
        expiration: str,
        dte: int,
        iv: float,
        interest_rate: float = 0.04,
        options_chain: Optional[List[Dict[str, Any]]] = None,
        is_backtesting: bool = False
    ) -> Tuple[Optional[VerticalSpread], str]:
        """
        Select strikes for a Vertical Debit Spread and enforce the 2.5:1 R:R filter.
        """
        is_call = (direction == "BULLISH")
        right = "C" if is_call else "P"

        logger.info(f"🔍 [{symbol}] Selecting {direction} spread. Underlying: {underlying_price:.2f} | "
                    f"Target TP: {target_tp:.2f} | Target SL: {target_sl:.2f} | DTE: {dte} | IV: {iv*100:.1f}%")

        # Convert pandas DataFrame to list of dicts if needed
        import pandas as pd
        if isinstance(options_chain, pd.DataFrame):
            options_chain = options_chain.to_dict(orient="records")

        # ── 1. Strike Selection & Option Chain Validation ────────────
        if not options_chain:
            if is_backtesting:
                logger.info(f"Backtest mode: Generating synthetic spread for {symbol}")
                return self._generate_synthetic_spread(
                    symbol=symbol,
                    direction=direction,
                    underlying_price=underlying_price,
                    target_tp=target_tp,
                    target_sl=target_sl,
                    expiration=expiration,
                    dte=dte,
                    iv=iv,
                    interest_rate=interest_rate
                )
            else:
                status = "Broker data feed error: No options chain available."
                self._log_rejection(expiration, symbol, direction, underlying_price, 0.0, 0.0, 0.0, 0.0, status)
                return None, status

        # Live option chain expiration resolution with fallback:
        # If the exact target expiration is not hosted by the broker, find the nearest
        # available expiration that is >= the target. We never go shorter to protect
        # positions from theta decay acceleration and early assignment risk.
        available_expirations = sorted(set(
            o["expiration"] for o in options_chain if o["right"] == right
        ))
        resolved_expiration = self._resolve_expiration(expiration, available_expirations)
        if not resolved_expiration:
            status = (
                f"No expiration \u2265 target {expiration} available in chain "
                f"(target DTE: {dte}). Available: {available_expirations or 'none'}"
            )
            self._log_rejection(expiration, symbol, direction, underlying_price, 0.0, 0.0, 0.0, 0.0, status)
            return None, status

        if resolved_expiration != expiration:
            logger.info(
                f"[{symbol}] Expiration fallback: {expiration} \u2192 {resolved_expiration} "
                f"(broker does not host exact {dte}-DTE cycle)"
            )
            # Recompute effective DTE from the resolved expiration so Greeks stay accurate
            try:
                resolved_dt = datetime.strptime(resolved_expiration, "%Y%m%d").date()
                dte = max(1, (resolved_dt - datetime.now().date()).days)
            except ValueError:
                pass  # Keep original dte if parse fails

        expiration = resolved_expiration
        valid_options = [o for o in options_chain if o["expiration"] == expiration and o["right"] == right]
        if not valid_options:
            status = f"No options found for resolved expiration {expiration}"
            self._log_rejection(expiration, symbol, direction, underlying_price, 0.0, 0.0, 0.0, 0.0, status)
            return None, status

        strikes = sorted(list(set(o["strike"] for o in valid_options)))

        long_strike = 0.0
        short_strike = 0.0

        if is_call:
            valid_long = [s for s in strikes if s >= underlying_price]
            long_strike = valid_long[0] if valid_long else strikes[-1]
            valid_short = [s for s in strikes if s >= target_tp]
            short_strike = valid_short[0] if valid_short else strikes[-1]
        else:
            valid_long = [s for s in strikes if s <= underlying_price]
            long_strike = valid_long[-1] if valid_long else strikes[0]
            valid_short = [s for s in strikes if s <= target_tp]
            short_strike = valid_short[-1] if valid_short else strikes[0]

        if long_strike == short_strike:
            increment = 1.0 if underlying_price < 200 else 5.0
            short_strike += increment if is_call else -increment

        logger.debug(f"[{symbol}] Strikes mapped: Long={long_strike:.2f}, Short={short_strike:.2f}")

        # ── 2. Pricing & Greeks ──────────────────────────────────────
        long_price = 0.0
        short_price = 0.0

        long_opt = next((o for o in valid_options if o["strike"] == long_strike), None)
        short_opt = next((o for o in valid_options if o["strike"] == short_strike), None)

        # Per-contract IV: use each contract's broker-reported IV rather than
        # the flat VIX-derived parameter.  Falls back to the passed-in `iv`
        # when the chain doesn't carry per-contract IV or when using BS pricing.
        long_iv = iv
        short_iv = iv

        if long_opt and short_opt:
            long_price = long_opt.get("mid") or (long_opt.get("bid", 0.0) + long_opt.get("ask", 0.0)) / 2.0
            short_price = short_opt.get("mid") or (short_opt.get("bid", 0.0) + short_opt.get("ask", 0.0)) / 2.0
            long_delta = long_opt.get("delta", 0.0)
            long_theta = long_opt.get("theta", 0.0)
            long_vega = long_opt.get("vega", 0.0)
            long_iv = float(long_opt.get("iv", 0.0)) or iv
            short_delta = short_opt.get("delta", 0.0)
            short_theta = short_opt.get("theta", 0.0)
            short_vega = short_opt.get("vega", 0.0)
            short_iv = float(short_opt.get("iv", 0.0)) or iv
            logger.info(
                f"[{symbol}] Per-contract IV: long={long_iv*100:.1f}%, "
                f"short={short_iv*100:.1f}% (VIX fallback was {iv*100:.1f}%)"
            )
        else:
            long_price, long_delta, long_theta, long_vega = self._price_option(underlying_price, long_strike, dte, iv, interest_rate, is_call)
            short_price, short_delta, short_theta, short_vega = self._price_option(underlying_price, short_strike, dte, iv, interest_rate, is_call)

        net_debit = long_price - short_price
        if net_debit <= 0.02:
            status = f"Net debit too low (${net_debit:.2f})"
            self._log_rejection(expiration, symbol, direction, underlying_price, long_strike, short_strike, net_debit, 0, status)
            return None, status

        # ── 3. Risk-to-Reward Calculation & Filter ──────────────────
        width = abs(long_strike - short_strike)
        risk = net_debit
        reward = width - net_debit
        rr_ratio = reward / risk if risk > 0 else 0.0

        if rr_ratio < self.min_rr:
            status = f"REJECTED: R:R {rr_ratio:.2f} < {self.min_rr}"
            self._log_rejection(expiration, symbol, direction, underlying_price, long_strike, short_strike, net_debit, rr_ratio, status)
            return None, status

        # ── 4. Build Spreads ─────────────────────────────────────────
        long_leg = OptionLeg(
            symbol=symbol,
            expiration=expiration,
            strike=long_strike,
            right=right,
            action="BUY",
            mid=long_price,
            iv=long_iv,
            delta=long_delta,
            theta=long_theta,
            vega=long_vega,
            dte=dte
        )

        short_leg = OptionLeg(
            symbol=symbol,
            expiration=expiration,
            strike=short_strike,
            right=right,
            action="SELL",
            mid=short_price,
            iv=short_iv,
            delta=short_delta,
            theta=short_theta,
            vega=short_vega,
            dte=dte
        )

        spread = VerticalSpread(
            symbol=symbol,
            expiration=expiration,
            right=right,
            long_leg=long_leg,
            short_leg=short_leg,
            net_debit=net_debit,
            current_value=net_debit,
            risk=risk,
            reward=reward,
            rr_ratio=rr_ratio
        )

        self._log_rejection(expiration, symbol, direction, underlying_price, long_strike, short_strike, net_debit, rr_ratio, "APPROVED")
        return spread, "Approved"

    def _generate_synthetic_spread(
        self,
        symbol: str,
        direction: str,
        underlying_price: float,
        target_tp: float,
        target_sl: float,
        expiration: str,
        dte: int,
        iv: float,
        interest_rate: float
    ) -> Tuple[Optional[VerticalSpread], str]:
        """
        Generate mock option spread based on math strikes for backtesting.
        """
        is_call = (direction == "BULLISH")
        right = "C" if is_call else "P"

        # Mock Strike Selection
        increment = 1.0 if underlying_price < 200 else 5.0
        if is_call:
            long_strike = math.ceil(underlying_price / increment) * increment
            short_strike = math.ceil(target_tp / increment) * increment
        else:
            long_strike = math.floor(underlying_price / increment) * increment
            short_strike = math.floor(target_tp / increment) * increment

        if long_strike == short_strike:
            increment = 1.0 if underlying_price < 200 else 5.0
            short_strike += increment if is_call else -increment

        long_price, long_delta, long_theta, long_vega = self._price_option(underlying_price, long_strike, dte, iv, interest_rate, is_call)
        short_price, short_delta, short_theta, short_vega = self._price_option(underlying_price, short_strike, dte, iv, interest_rate, is_call)

        net_debit = long_price - short_price
        if net_debit <= 0.02:
            status = f"Net debit too low (${net_debit:.2f})"
            self._log_rejection(expiration, symbol, direction, underlying_price, long_strike, short_strike, net_debit, 0, status)
            return None, status

        width = abs(long_strike - short_strike)
        risk = net_debit
        reward = width - net_debit
        rr_ratio = reward / risk if risk > 0 else 0.0

        if rr_ratio < self.min_rr:
            status = f"REJECTED: R:R {rr_ratio:.2f} < {self.min_rr}"
            self._log_rejection(expiration, symbol, direction, underlying_price, long_strike, short_strike, net_debit, rr_ratio, status)
            return None, status

        long_leg = OptionLeg(
            symbol=symbol,
            expiration=expiration,
            strike=long_strike,
            right=right,
            action="BUY",
            mid=long_price,
            iv=iv,
            delta=long_delta,
            theta=long_theta,
            vega=long_vega,
            dte=dte
        )

        short_leg = OptionLeg(
            symbol=symbol,
            expiration=expiration,
            strike=short_strike,
            right=right,
            action="SELL",
            mid=short_price,
            iv=iv,
            delta=short_delta,
            theta=short_theta,
            vega=short_vega,
            dte=dte
        )

        spread = VerticalSpread(
            symbol=symbol,
            expiration=expiration,
            right=right,
            long_leg=long_leg,
            short_leg=short_leg,
            net_debit=net_debit,
            current_value=net_debit,
            risk=risk,
            reward=reward,
            rr_ratio=rr_ratio
        )

        self._log_rejection(expiration, symbol, direction, underlying_price, long_strike, short_strike, net_debit, rr_ratio, "SyntheticData")
        return spread, "SyntheticData"

    def _log_rejection(self, date: str, symbol: str, direction: str, price: float, long_stk: float, short_stk: float, debit: float, rr: float, status: str):
        self.risk_filter_logs.append({
            "date": date,
            "symbol": symbol,
            "direction": direction,
            "underlying_price": round(price, 2),
            "long_strike": long_stk,
            "short_strike": short_stk,
            "net_debit": round(debit, 2),
            "rr_ratio": round(rr, 2),
            "status": status
        })

    def _resolve_expiration(
        self,
        target_expiration: str,
        available_expirations: List[str],
    ) -> Optional[str]:
        """
        Given a target expiration string ("YYYYMMDD") and a sorted list of expiration
        strings available in the broker's options chain, return the best expiration to use:

        1. Exact match — return ``target_expiration`` if it is hosted by the broker.
        2. Forward fallback — return the lexicographically smallest expiration that is
           **>= target_expiration** (i.e., the nearest future cycle on or after the target).
           We deliberately never go shorter than the target to protect positions from
           accelerated theta decay and early assignment risk on near-expiry contracts.
        3. If no qualifying expiration exists, return ``None``.
        """
        if not available_expirations:
            return None

        # Exact match
        if target_expiration in available_expirations:
            return target_expiration

        # Nearest available expiration >= target (forward-only fallback)
        candidates = [e for e in available_expirations if e >= target_expiration]
        return candidates[0] if candidates else None

    def _price_option(self, S: float, K: float, dte: int, iv: float, r: float, is_call: bool) -> Tuple[float, float, float, float]:
        """Helper to price options via Black-Scholes model."""
        T = dte / 365.0
        if is_call:
            return black_scholes_call(S, K, T, r, iv)
        else:
            return black_scholes_put(S, K, T, r, iv)
