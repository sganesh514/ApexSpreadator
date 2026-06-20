"""
ApexSpreadator — Underlying Asset Tracker (Bidirectional Price Action Engine)
Ingests OHLCV data, computes market structure bias, identifies Supply/Demand zones,
and flags zone retests.
"""
from typing import List, Dict, Tuple, Optional, Any
from models import Zone
from utils import get_logger

logger = get_logger("UnderlyingTracker")


class UnderlyingTracker:
    """
    Price Action Engine tracking Market Structure and Supply/Demand zones.
    Bias states:
    - 'NEUTRAL': No established structure.
    - 'BULLISH': Price broke and closed above the previous swing high (BOS).
    - 'BEARISH': Price broke and closed below the previous swing low (BOS).
    """

    def __init__(self, symbol: str, fractal_window: int = 3):
        self.symbol = symbol
        self.n = fractal_window
        self.candles: List[Dict[str, Any]] = []  # List of candles: {time, open, high, low, close, volume}
        self.bias = "NEUTRAL"

        # Confirmed swing points
        self.swing_highs: List[Tuple[int, float]] = []  # List of (candle_idx, price)
        self.swing_lows: List[Tuple[int, float]] = []   # List of (candle_idx, price)

        # Supply / Demand zones
        self.demand_zones: List[Zone] = []
        self.supply_zones: List[Zone] = []
        
        self.zone_counter = 0

    def add_candle(self, open_p: float, high_p: float, low_p: float, close_p: float, volume: float, timestamp: str, iv: float = 0.18) -> Optional[Dict[str, Any]]:
        """
        Ingest a new candle and update the market structure.
        Returns a signal dict if a trade setup (retest) occurs, else None.
        """
        candle = {
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": volume,
            "time": timestamp,
            "iv": iv
        }
        self.candles.append(candle)
        idx = len(self.candles) - 1

        # We need at least 2*N + 1 candles to identify a swing point at index (idx - N)
        if len(self.candles) >= (2 * self.n + 1):
            target_idx = idx - self.n
            self._check_and_register_swing_points(target_idx)

        # Check for Breaks of Structure (BOS) using the current close
        self._check_for_break_of_structure(idx)

        # Invalidate any breached zones on close
        self._check_zone_invalidations(close_p)

        # Check for retests of active zones
        return self._check_zone_retests(candle)

    def _check_and_register_swing_points(self, idx: int) -> None:
        """Verify if the candle at idx is a swing high or swing low based on N bars surrounding it."""
        target_high = self.candles[idx]["high"]
        target_low = self.candles[idx]["low"]

        is_swing_high = True
        is_swing_low = True

        for i in range(idx - self.n, idx + self.n + 1):
            if i == idx:
                continue
            if self.candles[i]["high"] > target_high:
                is_swing_high = False
            if self.candles[i]["low"] < target_low:
                is_swing_low = False

        if is_swing_high:
            self.swing_highs.append((idx, target_high))
            logger.debug(f"[{self.symbol}] Confirmed Swing High at index {idx} (price: {target_high:.2f})")

        if is_swing_low:
            self.swing_lows.append((idx, target_low))
            logger.debug(f"[{self.symbol}] Confirmed Swing Low at index {idx} (price: {target_low:.2f})")

    def _check_for_break_of_structure(self, current_idx: int) -> None:
        """Check if the current candle close breaks the previous confirmed swing points."""
        current_close = self.candles[current_idx]["close"]

        # ── Bullish BOS ──────────────────────────────────────────────
        if (self.bias == "NEUTRAL" or self.bias == "BEARISH") and self.swing_highs:
            # Find the most recent confirmed swing high
            last_sh_idx, last_sh_price = self.swing_highs[-1]
            if current_close > last_sh_price:
                # BOS to the upside confirmed!
                old_bias = self.bias
                self.bias = "BULLISH"
                logger.info(f"⚡ [{self.symbol}] BULLISH Break of Structure (BOS) detected! "
                            f"Price closed at {current_close:.2f} above Swing High {last_sh_price:.2f}.")
                self._create_demand_zone(last_sh_idx, current_idx)

        # ── Bearish BOS ──────────────────────────────────────────────
        if (self.bias == "NEUTRAL" or self.bias == "BULLISH") and self.swing_lows:
            # Find the most recent confirmed swing low
            last_sl_idx, last_sl_price = self.swing_lows[-1]
            if current_close < last_sl_price:
                # BOS to the downside confirmed!
                old_bias = self.bias
                self.bias = "BEARISH"
                logger.info(f"⚡ [{self.symbol}] BEARISH Break of Structure (BOS) detected! "
                            f"Price closed at {current_close:.2f} below Swing Low {last_sl_price:.2f}.")
                self._create_supply_zone(last_sl_idx, current_idx)

    def _create_demand_zone(self, sh_idx: int, current_idx: int) -> None:
        """Create a Demand Zone: Locate the consolidation candle before the upward impulse."""
        # Find the starting point of the impulse move (the lowest low before the break)
        search_start = max(0, sh_idx - 5)
        lowest_idx = search_start
        lowest_low = self.candles[search_start]["low"]
        
        for i in range(search_start, current_idx):
            if self.candles[i]["low"] < lowest_low:
                lowest_low = self.candles[i]["low"]
                lowest_idx = i

        # Look for the last down (red) candle at or immediately preceding the lowest low
        consolidation_idx = lowest_idx
        for i in range(lowest_idx, max(-1, lowest_idx - 5), -1):
            if self.candles[i]["close"] < self.candles[i]["open"]:
                consolidation_idx = i
                break

        origin_candle = self.candles[consolidation_idx]
        self.zone_counter += 1
        
        # Demand Zone coordinates: High and Low of consolidation candle
        new_zone = Zone(
            id=f"DZ-{self.zone_counter:03d}",
            type="DEMAND",
            high=origin_candle["high"],
            low=origin_candle["low"],
            origin_candle_time=origin_candle["time"],
            is_active=True
        )
        self.demand_zones.append(new_zone)
        logger.info(f"🏷️ [{self.symbol}] New Demand Zone mapped: {new_zone.description} at candle {origin_candle['time']}")

    def _create_supply_zone(self, sl_idx: int, current_idx: int) -> None:
        """Create a Supply Zone: Locate the consolidation candle before the downward impulse."""
        # Find the starting point of the impulse move (the highest high before the break)
        search_start = max(0, sl_idx - 5)
        highest_idx = search_start
        highest_high = self.candles[search_start]["high"]
        
        for i in range(search_start, current_idx):
            if self.candles[i]["high"] > highest_high:
                highest_high = self.candles[i]["high"]
                highest_idx = i

        # Look for the last up (green) candle at or immediately preceding the highest high
        consolidation_idx = highest_idx
        for i in range(highest_idx, max(-1, highest_idx - 5), -1):
            if self.candles[i]["close"] > self.candles[i]["open"]:
                consolidation_idx = i
                break

        origin_candle = self.candles[consolidation_idx]
        self.zone_counter += 1
        
        # Supply Zone coordinates: High and Low of consolidation candle
        new_zone = Zone(
            id=f"SZ-{self.zone_counter:03d}",
            type="SUPPLY",
            high=origin_candle["high"],
            low=origin_candle["low"],
            origin_candle_time=origin_candle["time"],
            is_active=True
        )
        self.supply_zones.append(new_zone)
        logger.info(f"🏷️ [{self.symbol}] New Supply Zone mapped: {new_zone.description} at candle {origin_candle['time']}")

    def _check_zone_invalidations(self, current_close: float) -> None:
        """Deactivate zones that have been invalidly breached on a candle close."""
        # Invalidate demand zones if price closes below them
        for zone in self.demand_zones:
            if zone.is_active and current_close < zone.low:
                zone.is_active = False
                logger.info(f"❌ [{self.symbol}] Demand Zone {zone.id} breached and invalidated (Close: {current_close:.2f} < Low: {zone.low:.2f})")

        # Invalidate supply zones if price closes above them
        for zone in self.supply_zones:
            if zone.is_active and current_close > zone.high:
                zone.is_active = False
                logger.info(f"❌ [{self.symbol}] Supply Zone {zone.id} breached and invalidated (Close: {current_close:.2f} > High: {zone.high:.2f})")

    def _check_zone_retests(self, candle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Check if the current candle retests any active zone matching the current trend bias.
        Returns a signal dict if a retest occurs, else None.
        """
        current_high = candle["high"]
        current_low = candle["low"]
        current_close = candle["close"]

        # ── Bullish Retest (Demand Zone) ──────────────────────────────
        if self.bias == "BULLISH":
            for zone in self.demand_zones:
                # Retest is valid if price enters the zone range (touches or crosses below zone.high)
                # and price does not close below the invalidation point (zone.low)
                if zone.is_active and current_low <= zone.high and current_close >= zone.low:
                    logger.info(f"🎯 [{self.symbol}] Demand Zone Retest detected! Price touched {current_low:.2f} within zone {zone.id} ({zone.low:.2f} - {zone.high:.2f})")
                    return {
                        "symbol": self.symbol,
                        "type": "RETEST_DEMAND",
                        "zone": zone,
                        "direction": "BULLISH",
                        "price": current_close
                    }

        # ── Bearish Retest (Supply Zone) ──────────────────────────────
        elif self.bias == "BEARISH":
            for zone in self.supply_zones:
                # Retest is valid if price enters the zone range (touches or crosses above zone.low)
                # and price does not close above the invalidation point (zone.high)
                if zone.is_active and current_high >= zone.low and current_close <= zone.high:
                    logger.info(f"🎯 [{self.symbol}] Supply Zone Retest detected! Price touched {current_high:.2f} within zone {zone.id} ({zone.low:.2f} - {zone.high:.2f})")
                    return {
                        "symbol": self.symbol,
                        "type": "RETEST_SUPPLY",
                        "zone": zone,
                        "direction": "BEARISH",
                        "price": current_close
                    }

        return None

    def get_closest_zone_proximity(self, current_price: float) -> Tuple[Optional[Zone], float]:
        """
        Calculate the proximity (absolute percentage distance) of the current price
        to the nearest active zone matching the current trend bias.
        Returns (closest_zone, distance_pct).
        """
        closest_zone = None
        min_dist = float("inf")

        if self.bias == "BULLISH":
            for zone in self.demand_zones:
                if zone.is_active:
                    dist = abs(current_price - zone.high) / current_price
                    if dist < min_dist:
                        min_dist = dist
                        closest_zone = zone
        elif self.bias == "BEARISH":
            for zone in self.supply_zones:
                if zone.is_active:
                    dist = abs(current_price - zone.low) / current_price
                    if dist < min_dist:
                        min_dist = dist
                        closest_zone = zone

        return closest_zone, min_dist if closest_zone else 1.0
