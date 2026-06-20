"""
ApexSpreadator — Ollama Trade Analyst
Uses local Ollama LLM for trade analysis and insights.
"""
import json
from typing import Dict, Any, Optional

from config import AgentConfig
from models import TradeRecord, Opportunity
from utils import get_logger

logger = get_logger("Ollama")


class OllamaAnalyst:
    """
    Uses local Ollama LLM models for natural-language trade analysis.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.model = config.ollama.model
        self.enabled = config.ollama.enabled
        self._ollama_available: Optional[bool] = None

    async def check_availability(self) -> bool:
        """Check if Ollama is available and the model is loaded."""
        if not self.enabled:
            return False

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_names = [m.get("name", "").split(":")[0] for m in models]
                    self._ollama_available = self.model.split(":")[0] in model_names
                    if self._ollama_available:
                        logger.info(f"✅ Ollama available with model '{self.model}'")
                    else:
                        logger.warning(
                            f"⚠️ Ollama running but model '{self.model}' not found. "
                            f"Available: {model_names}"
                        )
                    return self._ollama_available
        except Exception as e:
            logger.warning(f"⚠️ Ollama not available: {e}")
            self._ollama_available = False
            return False

    async def _generate(self, prompt: str) -> str:
        """Send a prompt to Ollama and get a response."""
        if not self._ollama_available:
            return ""

        try:
            import httpx
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 500,
                        }
                    }
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "")
                else:
                    logger.error(f"Ollama error: {resp.status_code}")
                    return ""
        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
            return ""

    async def analyze_pre_trade(self, opportunity: 'Opportunity') -> str:
        """Generate pre-trade analysis for an opportunity."""
        if not self.enabled or not self._ollama_available:
            return self._fallback_pre_trade(opportunity)

        spread = opportunity.spread
        long_stk = spread.long_leg.strike if (spread and spread.long_leg) else 0.0
        short_stk = spread.short_leg.strike if (spread and spread.short_leg) else 0.0
        type_str = "Bull Call" if (spread and spread.right == "C") else "Bear Put"

        prompt = f"""You are an expert options trader analyzing a vertical debit spread opportunity.

TRADE DETAILS:
- Underlying: {spread.symbol if spread else ''} at ${opportunity.underlying_price:.2f}
- Strategy: {type_str} Spread
- Long Strike: ${long_stk:.2f}
- Short Strike: ${short_stk:.2f}
- Expiration: {spread.expiration if spread else ''}
- Net Debit: ${spread.net_debit if spread else 0.0:.2f}
- R:R Ratio: {spread.rr_ratio if spread else 0.0:.2f} (Target >= 2.5)

Provide a brief (3-4 sentences) analysis covering:
1. Is this a good entry? Why or why not?
2. Key risk to watch
3. Expected behavior of this spread over the holding period"""

        response = await self._generate(prompt)
        return response if response else self._fallback_pre_trade(opportunity)

    async def analyze_post_trade(self, trade: TradeRecord) -> str:
        """Generate post-trade review and lessons learned."""
        if not self.enabled or not self._ollama_available:
            return self._fallback_post_trade(trade)

        outcome = "PROFITABLE" if trade.realized_pnl > 0 else "LOSS"
        type_str = "Bull Call" if trade.right == "C" else "Bear Put"
        prompt = f"""You are an expert options trader reviewing a completed vertical spread trade.

TRADE SUMMARY:
- Symbol: {trade.symbol}
- Strategy: {type_str} Spread
- Long Strike: ${trade.long_strike:.2f}
- Short Strike: ${trade.short_strike:.2f}
- Expiration: {trade.expiration}
- Entry Price: ${trade.entry_price:.2f} (debit)
- Exit Price: ${trade.exit_price:.2f}
- Realized P&L: ${trade.realized_pnl:+.2f} ({trade.realized_pnl_pct:+.1%})
- Holding Period: {trade.holding_days} days
- Exit Reason: {trade.exit_reason}
- Underlying at Entry: ${trade.underlying_price_at_entry:.2f}
- Underlying at Exit: ${trade.underlying_price_at_exit:.2f}

This trade was a {outcome}.

Provide a brief (3-4 sentences) review covering:
1. What went right or wrong?
2. Was the entry timing good?
3. Key lesson to apply to future trades"""

        response = await self._generate(prompt)
        return response if response else self._fallback_post_trade(trade)

    def _fallback_pre_trade(self, opportunity: 'Opportunity') -> str:
        """Fallback analysis when Ollama is unavailable."""
        spread = opportunity.spread
        if not spread:
            return "No spread details."
        type_str = "Bull Call" if spread.right == "C" else "Bear Put"
        return f"Approved entry for {spread.symbol} {type_str} Debit Spread. R:R ratio is {spread.rr_ratio:.2f}."

    def _fallback_post_trade(self, trade: TradeRecord) -> str:
        """Fallback post-trade analysis when Ollama is unavailable."""
        type_str = "Bull Call" if trade.right == "C" else "Bear Put"
        if trade.realized_pnl > 0:
            return (
                f"Profitable trade on {trade.symbol} {type_str} spread netting ${trade.realized_pnl:.2f} "
                f"over {trade.holding_days} days. Exit via {trade.exit_reason}."
            )
        else:
            return (
                f"Loss of ${abs(trade.realized_pnl):.2f} on {trade.symbol} {type_str} spread "
                f"after {trade.holding_days} days. Exit via {trade.exit_reason}."
            )
