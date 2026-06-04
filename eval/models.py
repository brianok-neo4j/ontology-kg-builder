"""Candidate models and cost helpers for the A/B harness.

Pricing is reused from `ingest.main._PRICING` (the canonical table) so the
harness never drifts from the rest of the project.
"""

from __future__ import annotations

from ingest.main import _PRICING

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"
OPUS = "claude-opus-4-8"

# Default A/B pair: the production model vs the cheaper/faster candidate.
DEFAULT_MODELS = [SONNET, HAIKU]

# The four usage counters every run reports.
USAGE_KEYS = (
    "inputTokens", "cacheWriteInputTokens", "cacheReadInputTokens", "outputTokens",
)


def cost_of(usage: dict, model_id: str) -> float:
    """Dollar cost of a usage dict for a model, using the canonical pricing table."""
    p = _PRICING.get(model_id)
    if not p:
        raise ValueError(
            f"No pricing for {model_id!r}. Add it to _PRICING in ingest/main.py."
        )
    return (
        usage.get("inputTokens", 0)            / 1e6 * p["input"]
        + usage.get("cacheWriteInputTokens", 0) / 1e6 * p["cache_write"]
        + usage.get("cacheReadInputTokens", 0)  / 1e6 * p["cache_read"]
        + usage.get("outputTokens", 0)          / 1e6 * p["output"]
    )
