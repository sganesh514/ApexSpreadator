"""
ApexSpreadator — Stock Screener Engine / Universe Provider
Provides the full configured universe of symbols for the bot's watchlist.
"""
from typing import List, Optional, Any

class ScreenerEngine:
    """
    Universe Provider that returns the full configured list of underlyings.
    """

    def __init__(self, config: Any, broker: Optional[Any] = None):
        self.config = config
        self.broker = broker

    def get_candidate_list(
        self,
        current_time: Optional[Any] = None,
        limit: Optional[int] = None,
        historical_df: Optional[Any] = None,
        date_limit: Optional[str] = None
    ) -> List[str]:
        """
        Return the full list of symbols configured in the strategy's underlyings.
        """
        strategy_config = getattr(self.config, "strategy", None)
        if strategy_config and hasattr(strategy_config, "underlyings"):
            return list(strategy_config.underlyings)
        return []
