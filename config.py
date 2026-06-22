"""
ApexSpreadator — Configuration Settings
All tunable parameters for the market structure and vertical spread trading bot.
"""
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class ConnectionConfig:
    """IBKR connection settings."""
    host: str = "127.0.0.1"
    port: int = 7497          # Paper: 7497, Live: 7496
    client_id: int = 1
    timeout: int = 30         # Connection timeout in seconds
    max_reconnect_attempts: int = 10
    reconnect_base_delay: float = 1.0


@dataclass
class AccountConfig:
    """Account and capital management."""
    starting_capital: float = 25000.0  # Default to $25k as requested
    monthly_target: float = 5000.0
    virtual_balance: bool = True  # Track our own limit on top of IBKR's paper balance


@dataclass
class StrategyConfig:
    """Market Structure and Vertical Spread strategy parameters."""
    # Watchlist
    underlyings: List[str] = field(default_factory=lambda: ["SPY", "QQQ"])

    # Dynamic Screener settings
    screener_type: str = "nasdaq100"       # "static", "sp500", or "nasdaq100"

    screener_limit: int = 5                # Number of top dynamic candidates to track
    screener_min_volume: int = 500000      # Minimum average volume (shares)

    # Timeframe and DTE selection mapping (timeframe -> option DTE)
    timeframe_dte_map: Dict[str, int] = field(default_factory=lambda: {
        "1d": 30,     # Daily chart -> 30 DTE
        "1h": 7,      # 1-Hour chart -> 7 DTE
        "15m": 3,     # 15-Min chart -> 3 DTE
    })
    default_timeframe: str = "1d"

    # Swing Point Extrema Window Size (N bars on each side)
    fractal_window: int = 3

    # Invalidation point buffers (pct of price to place stops past the zone)
    stop_buffer_pct: float = 0.002  # 0.2% buffer past the zone boundary
    
    # Minimum options Risk-to-Reward ratio
    min_rr_threshold: float = 2.5  # Crucial: Must be at least 2.5:1

    # Option type right restriction
    option_right: str = "C"  # C or P, but we support both dynamically based on bias


@dataclass
class RiskConfig:
    """Risk management parameters."""
    max_risk_per_trade: float = 0.02      # 2% of account balance
    max_concurrent_positions: int = 4
    max_portfolio_risk: float = 0.20      # 20% total exposure
    daily_loss_limit: float = 500.0       # $500
    monthly_drawdown_limit: float = 2000.0  # $2,000
    max_correlated_positions: int = 2


@dataclass
class ScheduleConfig:
    """Timing and scheduling."""
    scan_interval_seconds: int = 300      # 5 minutes
    position_check_seconds: int = 60      # 1 minute
    account_refresh_seconds: int = 3600   # 1 hour
    market_open: str = "09:30"            # ET
    market_close: str = "16:00"           # ET
    order_fill_timeout_seconds: int = 120
    order_price_adjustment: float = 0.01
    max_order_adjustments: int = 5


@dataclass
class OllamaConfig:
    """Ollama LLM integration settings."""
    model: str = "qwen3-coder:30b"
    enabled: bool = True


@dataclass
class DashboardConfig:
    """Web dashboard settings."""
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AgentConfig:
    """Master configuration combining all sub-configs."""
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    account: AccountConfig = field(default_factory=AccountConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    # Data paths
    data_dir: str = "data"
    trades_file: str = "data/trades.json"
    learning_file: str = "data/learning_state.json"
    journal_file: str = "data/journal.json"


# Global config instance
CONFIG = AgentConfig()
