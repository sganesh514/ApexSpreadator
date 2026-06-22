"""
ApexSpreadator — Risk Manager
Pre-trade validation, portfolio limits, and circuit breakers.
"""
from typing import List, Tuple, Optional
from datetime import datetime

from config import AgentConfig
from models import Position, VerticalSpread, AccountSnapshot, TradeStatus
from utils import get_logger, format_currency

logger = get_logger("Risk")

# Sector mapping for correlation checks
SECTOR_MAP = {
    "SPY": "broad_market",
    "QQQ": "tech_broad",
    "IWM": "small_cap",
    "AAPL": "tech",
    "MSFT": "tech",
    "TSLA": "ev_tech",
    "AMZN": "consumer_tech",
    "NVDA": "semiconductors",
}


class RiskManager:
    """
    Enforces risk rules before and during trades.
    Implements circuit breakers for catastrophic loss prevention.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._daily_realized_pnl: float = 0.0
        self._monthly_realized_pnl: float = 0.0
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_reason: str = ""
        self._current_date: str = ""
        self._day_start_equity: Optional[float] = None
        self._month_start_equity: Optional[float] = None

    def pre_trade_check(
        self,
        spread: VerticalSpread,
        account: AccountSnapshot,
        open_positions: List[Position],
    ) -> Tuple[bool, str]:
        """
        Run all pre-trade risk validations.
        Returns (passed, reason).
        """
        # Circuit breaker check
        if self._circuit_breaker_active:
            return False, f"Circuit breaker active: {self._circuit_breaker_reason}"

        # 1. Position count
        active = [p for p in open_positions if p.status in (TradeStatus.OPEN, TradeStatus.PENDING)]
        if len(active) >= self.config.risk.max_concurrent_positions:
            return False, f"Max positions ({self.config.risk.max_concurrent_positions}) reached"

        # 2. Trade risk vs account
        trade_cost = spread.net_debit * 100  # Per contract, x100 for options
        max_allowed = account.balance * self.config.risk.max_risk_per_trade
        if trade_cost > max_allowed:
            return False, (
                f"Trade cost {format_currency(trade_cost)} exceeds max risk "
                f"{format_currency(max_allowed)} ({self.config.risk.max_risk_per_trade * 100:.0f}% of {format_currency(account.balance)})"
            )

        # 3. Portfolio total risk
        current_risk = sum(p.total_risk for p in active)
        max_portfolio_risk = account.balance * self.config.risk.max_portfolio_risk
        if current_risk + trade_cost > max_portfolio_risk:
            return False, (
                f"Portfolio risk {format_currency(current_risk + trade_cost)} would exceed "
                f"limit {format_currency(max_portfolio_risk)}"
            )

        # Ensure start equities are initialized
        if self._month_start_equity is None:
            self._month_start_equity = account.equity or self.config.account.starting_capital

        if self._day_start_equity is None:
            self._day_start_equity = account.equity or self.config.account.starting_capital
            daily_limit_usd = self._day_start_equity * self.config.risk.daily_loss_limit_pct
            monthly_limit_usd = self._month_start_equity * self.config.risk.monthly_drawdown_limit_pct
            logger.info(
                f"Dynamic Risk Limits initialized - Daily Loss Cap: ${daily_limit_usd:.2f} "
                f"({self.config.risk.daily_loss_limit_pct*100}%), "
                f"Monthly Drawdown Cap: ${monthly_limit_usd:.2f} "
                f"({self.config.risk.monthly_drawdown_limit_pct*100}%)"
            )

        day_equity = self._day_start_equity
        month_equity = self._month_start_equity
        daily_limit_usd = day_equity * self.config.risk.daily_loss_limit_pct
        monthly_limit_usd = month_equity * self.config.risk.monthly_drawdown_limit_pct

        # 4. Daily loss limit
        if self._daily_realized_pnl <= -daily_limit_usd:
            return False, (
                f"Daily loss limit reached: {format_currency(self._daily_realized_pnl)} "
                f"(limit: {format_currency(-daily_limit_usd)})"
            )

        # 5. Monthly drawdown limit
        if self._monthly_realized_pnl <= -monthly_limit_usd:
            return False, (
                f"Monthly drawdown limit reached: {format_currency(self._monthly_realized_pnl)} "
                f"(limit: {format_currency(-monthly_limit_usd)})"
            )

        # 6. Correlation check — avoid too many same-sector positions
        new_sector = SECTOR_MAP.get(spread.symbol, "other")
        sector_count = sum(
            1 for p in active
            if p.spread and SECTOR_MAP.get(p.spread.symbol, "other") == new_sector
        )
        if sector_count >= self.config.risk.max_correlated_positions:
            return False, (
                f"Already {sector_count} positions in '{new_sector}' sector "
                f"(max: {self.config.risk.max_correlated_positions})"
            )

        # 7. Buying power check
        if account.buying_power > 0 and trade_cost > account.buying_power:
            return False, (
                f"Insufficient buying power: need {format_currency(trade_cost)}, "
                f"have {format_currency(account.buying_power)}"
            )

        logger.info(
            f"✅ Risk check passed for {spread.symbol}: "
            f"trade cost {format_currency(trade_cost)}, "
            f"portfolio risk {format_currency(current_risk + trade_cost)}/{format_currency(max_portfolio_risk)}"
        )
        return True, "All risk checks passed"

    def record_realized_pnl(self, pnl: float, current_equity: Optional[float] = None) -> None:
        """Record a realized P&L from a closed trade."""
        today = datetime.now().strftime("%Y-%m-%d")

        # Reset daily if new day
        if today != self._current_date:
            self._daily_realized_pnl = 0.0
            self._current_date = today
            self._day_start_equity = None

        if self._day_start_equity is None and current_equity is not None:
            self._day_start_equity = current_equity
            daily_limit_usd = self._day_start_equity * self.config.risk.daily_loss_limit_pct
            month_equity = self._month_start_equity or current_equity
            monthly_limit_usd = month_equity * self.config.risk.monthly_drawdown_limit_pct
            logger.info(
                f"Dynamic Risk Limits initialized - Daily Loss Cap: ${daily_limit_usd:.2f} "
                f"({self.config.risk.daily_loss_limit_pct*100}%), "
                f"Monthly Drawdown Cap: ${monthly_limit_usd:.2f} "
                f"({self.config.risk.monthly_drawdown_limit_pct*100}%)"
            )

        if self._month_start_equity is None and current_equity is not None:
            self._month_start_equity = current_equity

        self._daily_realized_pnl += pnl
        self._monthly_realized_pnl += pnl

        logger.info(
            f"P&L recorded: {format_currency(pnl)} | "
            f"Daily: {format_currency(self._daily_realized_pnl)} | "
            f"Monthly: {format_currency(self._monthly_realized_pnl)}"
        )

        # Check circuit breakers
        self._check_circuit_breakers()

    def _check_circuit_breakers(self) -> None:
        """Check if circuit breakers should activate."""
        day_equity = self._day_start_equity or self.config.account.starting_capital
        month_equity = self._month_start_equity or self.config.account.starting_capital
        
        daily_limit_usd = day_equity * self.config.risk.daily_loss_limit_pct
        monthly_limit_usd = month_equity * self.config.risk.monthly_drawdown_limit_pct

        if self._daily_realized_pnl <= -daily_limit_usd:
            self._circuit_breaker_active = True
            self._circuit_breaker_reason = (
                f"Daily loss limit hit: {format_currency(self._daily_realized_pnl)} "
                f"(limit: {format_currency(-daily_limit_usd)})"
            )
            logger.critical(f"🚨 CIRCUIT BREAKER: {self._circuit_breaker_reason}")

        if self._monthly_realized_pnl <= -monthly_limit_usd:
            self._circuit_breaker_active = True
            self._circuit_breaker_reason = (
                f"Monthly drawdown limit hit: {format_currency(self._monthly_realized_pnl)} "
                f"(limit: {format_currency(-monthly_limit_usd)})"
            )
            logger.critical(f"🚨 CIRCUIT BREAKER: {self._circuit_breaker_reason}")

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker (requires user action)."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        logger.info("Circuit breaker reset by user")

    def reset_daily(self) -> None:
        """Reset daily counters (called at market open)."""
        self._daily_realized_pnl = 0.0
        self._day_start_equity = None
        logger.info("Daily risk counters reset")

    def reset_monthly(self) -> None:
        """Reset monthly counters (called at month start)."""
        self._monthly_realized_pnl = 0.0
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        self._month_start_equity = None
        logger.info("Monthly risk counters reset")

    @property
    def is_circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    @property
    def circuit_breaker_reason(self) -> str:
        return self._circuit_breaker_reason

    def get_risk_status(self) -> dict:
        """Get current risk status for dashboard."""
        day_equity = self._day_start_equity or self.config.account.starting_capital
        month_equity = self._month_start_equity or self.config.account.starting_capital
        
        return {
            "daily_realized_pnl": self._daily_realized_pnl,
            "monthly_realized_pnl": self._monthly_realized_pnl,
            "daily_limit": day_equity * self.config.risk.daily_loss_limit_pct,
            "monthly_limit": month_equity * self.config.risk.monthly_drawdown_limit_pct,
            "circuit_breaker_active": self._circuit_breaker_active,
            "circuit_breaker_reason": self._circuit_breaker_reason,
        }
