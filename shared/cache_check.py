"""Cache-health smoke test.

Every cost saving in this project rides on Anthropic prompt-caching the schema
system prefix, which depends on a fragile detail: structured system blocks (with
`cache_control`) must be routed through `params={"system": [...]}` on the Strands
AnthropicModel, because the framework silently drops a `cache_control` marker
passed any other way (see shared/strands_anthropic.py). On top of that,
`CacheAwareAnthropicModel` has to wire the cache token counts back through, since
upstream Strands drops them.

If any of those break — a Strands `format_request` change, the cache_control
field, the usage wiring — caching stops **with no error**. Every run just
silently pays full input price: a ~10x cost regression that is invisible until
someone reads a bill. (Finding B was exactly this: caching that looked enabled
but almost never engaged.)

This check exercises that exact path end to end with two tiny API calls and
asserts the cached prefix is both written (call 1) and re-read (call 2). Run it
manually or in CI:

    python -m shared.cache_check

Exit codes: 0 = caching works (or skipped: no API key), 1 = silent caching
regression detected.
"""

from __future__ import annotations

import os
import sys
import uuid

from dotenv import load_dotenv


# A cacheable prefix must exceed Anthropic's minimum cacheable length
# (~1024 tokens for Sonnet/Opus, ~2048 for Haiku). This filler is ~3K tokens —
# comfortably above the threshold for any model.
_FILLER = (
    "This sentence is padding so the cached system prefix exceeds the minimum "
    "cacheable length required by the Anthropic API. " * 200
)


def check_cache_health(model_id: str = "claude-sonnet-4-6", verbose: bool = True) -> bool:
    """Return True iff the cached system prefix is both written and re-read.

    Uses the real `CacheAwareAnthropicModel` + `params={"system": [...]}` plumbing
    so it catches regressions in the exact path the agents depend on. A unique
    nonce is prepended to the prefix so call 1 is guaranteed a cache *write*
    (never a read of a leftover entry from a previous run), and call 2 — sent
    immediately with the identical prefix — must be a cache *read*.
    """
    from strands import Agent

    from shared.strands_anthropic import CacheAwareAnthropicModel, cache_control

    nonce = uuid.uuid4().hex
    system_text = (
        f"Cache-health probe {nonce}. You are a test assistant; ignore the "
        f"padding below, it exists only to fill the cached prefix.\n\n{_FILLER}"
    )
    system_blocks = [
        {"type": "text", "text": system_text, "cache_control": cache_control()},
    ]
    agent = Agent(
        model=CacheAwareAnthropicModel(
            model_id=model_id,
            max_tokens=16,
            params={"system": system_blocks},
        ),
        system_prompt=system_text,
        tools=[],
        callback_handler=None,  # silence the model's streamed reply
    )

    def _accumulated(prompt: str) -> dict:
        agent.messages = []  # identical request each call; only the cache differs
        result = agent(prompt)
        summary = result.metrics.get_summary() if result and result.metrics else {}
        return summary.get("accumulated_usage", {}) or {}

    u1 = _accumulated("Reply with the single word: ok")
    cache_write = u1.get("cacheWriteInputTokens", 0)

    u2 = _accumulated("Reply with the single word: ok")
    # Usage is cumulative across calls, so a non-zero cache-read total can only
    # come from call 2 (call 1 wrote the fresh, nonce-unique prefix).
    cache_read = u2.get("cacheReadInputTokens", 0)

    wrote = cache_write > 0
    read = cache_read > 0
    if verbose:
        print(f"model             : {model_id}")
        print(f"cache_control     : {cache_control()}")
        print(f"call 1 cacheWrite : {cache_write:>7,}   "
              f"{'OK' if wrote else 'FAIL — prefix was not cached'}")
        print(f"cacheRead (total) : {cache_read:>7,}   "
              f"{'OK' if read else 'FAIL — prefix was not re-read'}")
    return wrote and read


def main() -> int:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("SKIP: ANTHROPIC_API_KEY not set — cannot run cache-health check.")
        return 0
    try:
        ok = check_cache_health()
    except Exception as exc:  # noqa: BLE001 — a thrown error is itself a failure signal
        print(f"FAIL: cache-health check errored: {type(exc).__name__}: {exc}")
        return 1
    if ok:
        print("\nPASS: prompt caching is active (prefix written, then re-read).")
        return 0
    print(
        "\nFAIL: prompt caching is NOT working — the schema prefix is not being "
        "cached/re-read, so every run silently pays full input price. Check the "
        "params-injection and cache-token wiring in shared/strands_anthropic.py "
        "and whether Strands' format_request still honours params['system']."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
