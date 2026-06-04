"""Anthropic model adapter that exposes prompt-cache token counts.

Strands v1.16's upstream `AnthropicModel.format_chunk` drops the
`cache_read_input_tokens` and `cache_creation_input_tokens` fields when it
shapes the metadata chunk (see [strands/models/anthropic.py:355-369]). The
Usage TypedDict supports them (`cacheReadInputTokens`, `cacheWriteInputTokens`)
and the telemetry layer aggregates them properly — the Anthropic adapter is
the only piece that doesn't wire them through.

This subclass extracts both fields when they're present on the raw Anthropic
usage dict and emits them under strands' native names so the rest of the
runtime picks them up automatically (per-cycle usage, accumulated_usage,
metrics summary, OTel exports).

Use everywhere we'd otherwise use `AnthropicModel`.
"""

from __future__ import annotations

import os
from typing import Any

from strands.models.anthropic import AnthropicModel
from strands.types.streaming import StreamEvent


_VALID_TTL = {"5m", "1h"}


def cache_control(default_ttl: str = "5m") -> dict[str, str]:
    """Build a `cache_control` marker for a cached system block.

    The TTL controls how long Anthropic keeps the cached prefix alive between
    reads (each read refreshes the window):

    - ``"5m"`` — cheaper write (1.25x input price) but the entry is evicted
      after 5 idle minutes and the whole prefix is re-written on the next
      request. Right for short / single-shot use (e.g. one CLI question).
    - ``"1h"`` — pricier write (2x input price) but the ~38K-token schema
      prefix is written once and survives slow chunks, rate-limit backoffs and
      idle workers instead of being re-written whenever a gap exceeds 5 minutes.
      Right for long ingest runs that re-read the prefix hundreds of times: one
      extra-cost write replaces tens of full re-writes (see
      analysis/performance_analysis.md, Finding E).

    The TTL is overridable globally via the ``ANTHROPIC_CACHE_TTL`` env var
    (``"5m"`` or ``"1h"``); an unrecognised value falls back to ``default_ttl``.
    The ``ttl`` field is GA in the Anthropic SDK (no beta header required).
    """
    ttl = os.environ.get("ANTHROPIC_CACHE_TTL", default_ttl).strip().lower()
    if ttl not in _VALID_TTL:
        ttl = default_ttl
    return {"type": "ephemeral", "ttl": ttl}


class CacheAwareAnthropicModel(AnthropicModel):
    def format_chunk(self, event: dict[str, Any]) -> StreamEvent:
        chunk = super().format_chunk(event)
        if event.get("type") == "metadata":
            usage = event.get("usage", {}) or {}
            cache_read = usage.get("cache_read_input_tokens")
            cache_creation = usage.get("cache_creation_input_tokens")
            chunk_usage = chunk["metadata"]["usage"]
            if cache_read:
                chunk_usage["cacheReadInputTokens"] = cache_read
            if cache_creation:
                chunk_usage["cacheWriteInputTokens"] = cache_creation
        return chunk
