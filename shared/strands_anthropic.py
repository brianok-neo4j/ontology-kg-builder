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

from typing import Any

from strands.models.anthropic import AnthropicModel
from strands.types.streaming import StreamEvent


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
