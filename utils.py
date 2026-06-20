"""
ApexSpreadator — Utilities
Common helper functions for logging, time, standard normal distribution, and option pricing.
"""
import logging
import os
import json
import uuid
import math
import sys
from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

# Configure stdout and stderr encoding to avoid Windows console errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Eastern Time zone (market hours)
ET = ZoneInfo("America/New_York")


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure logging for the agent."""
    logger = logging.getLogger("ApexSpreadator")
    logger.setLevel(level)

    if not logger.handlers:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console.setFormatter(fmt)
        logger.addHandler(console)

        os.makedirs("data", exist_ok=True)
        file_handler = logging.FileHandler("data/agent.log", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger."""
    return logging.getLogger(f"ApexSpreadator.{name}")


def generate_id(prefix: str = "TRD") -> str:
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def now_et() -> datetime:
    """Get current time in Eastern Time."""
    return datetime.now(ET)


def now_iso() -> str:
    """Get current time as ISO string in ET."""
    return now_et().isoformat()


def is_market_hours(open_time: str = "09:30", close_time: str = "16:00") -> bool:
    """Check if current time is within regular market hours (ET)."""
    current = now_et()
    if current.weekday() > 4:
        return False
    market_open = time(*map(int, open_time.split(":")))
    market_close = time(*map(int, close_time.split(":")))
    return market_open <= current.time() <= market_close


def trading_days_remaining_in_month() -> int:
    """Estimate trading days remaining in the current month."""
    current = now_et()
    year = current.year
    month = current.month

    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=ET)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=ET)

    days_remaining = 0
    day = current + timedelta(days=1)
    while day < next_month:
        if day.weekday() < 5:
            days_remaining += 1
        day += timedelta(days=1)

    return days_remaining


def calculate_dte(expiration: str) -> int:
    """Calculate days to expiration from YYYYMMDD string."""
    try:
        exp_date = datetime.strptime(expiration, "%Y%m%d").replace(tzinfo=ET)
        current = now_et().replace(hour=0, minute=0, second=0, microsecond=0)
        return max(0, (exp_date - current).days)
    except Exception:
        return 0


def format_currency(amount: float) -> str:
    """Format a number as currency."""
    if amount >= 0:
        return f"${amount:,.2f}"
    return f"-${abs(amount):,.2f}"


def format_pnl(amount: float) -> str:
    """Format P&L with + or - sign."""
    if amount >= 0:
        return f"+${amount:,.2f}"
    return f"-${abs(amount):,.2f}"


def format_pct(value: float) -> str:
    """Format a decimal as percentage."""
    return f"{value * 100:+.1%}"


def load_json(filepath: str, default: Any = None) -> Any:
    """Load JSON from file, return default if not found."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(filepath: str, data: Any) -> None:
    """Save data to JSON file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(value, max_val))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default on zero denominator."""
    if denominator == 0:
        return default
    return numerator / denominator


# ═══════════════════════════════════════════════════════════════
# Black-Scholes-Merton Option Pricing & Greeks
# ═══════════════════════════════════════════════════════════════

def norm_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution."""
    try:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0
    except OverflowError:
        return 1.0 if x > 0 else 0.0


def norm_pdf(x: float) -> float:
    """Probability density function for the standard normal distribution."""
    try:
        return math.exp(-0.5 * x**2) / math.sqrt(2.0 * math.pi)
    except OverflowError:
        return 0.0


def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> Tuple[float, float, float, float]:
    """
    Black-Scholes call option pricing and Greeks.
    Returns (price, delta, theta_daily, vega_1pct).
    """
    if T <= 0.0001:
        return max(0.0, S - K), (1.0 if S >= K else 0.0), 0.0, 0.0
    if sigma <= 0.0001:
        price = max(0.0, S - K * math.exp(-r * T))
        return price, (1.0 if S >= K else 0.0), 0.0, 0.0

    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
        delta = norm_cdf(d1)
        theta_annual = -(S * norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm_cdf(d2)
        theta_daily = theta_annual / 365.0
        vega_1pct = (S * math.sqrt(T) * norm_pdf(d1)) / 100.0

        return max(0.0, price), delta, theta_daily, vega_1pct
    except (ValueError, ZeroDivisionError, OverflowError):
        return max(0.0, S - K), (1.0 if S >= K else 0.0), 0.0, 0.0


def black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> Tuple[float, float, float, float]:
    """
    Black-Scholes put option pricing and Greeks.
    Returns (price, delta, theta_daily, vega_1pct).
    """
    if T <= 0.0001:
        return max(0.0, K - S), (-1.0 if S <= K else 0.0), 0.0, 0.0
    if sigma <= 0.0001:
        price = max(0.0, K * math.exp(-r * T) - S)
        return price, (-1.0 if S <= K else 0.0), 0.0, 0.0

    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        price = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1.0
        theta_annual = -(S * norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm_cdf(-d2)
        theta_daily = theta_annual / 365.0
        vega_1pct = (S * math.sqrt(T) * norm_pdf(d1)) / 100.0

        return max(0.0, price), delta, theta_daily, vega_1pct
    except (ValueError, ZeroDivisionError, OverflowError):
        return max(0.0, K - S), (-1.0 if S <= K else 0.0), 0.0, 0.0
