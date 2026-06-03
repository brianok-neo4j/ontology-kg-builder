"""CLI entry point for the query agent.

Usage:
    python query/main.py "your natural-language question here"
    python query/main.py            # interactive REPL
    python query/main.py cost <log> # cost breakdown for a session log
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from query.agent import MODEL_ID, build_agent

_PRICING = {
    "claude-sonnet-4-6":          {"input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80, "cache_write": 1.00, "cache_read": 0.08, "output":  4.00},
    "claude-opus-4-8":            {"input": 15.00, "cache_write": 18.75, "cache_read": 1.50, "output": 75.00},
}

LOG_DIR = Path(__file__).parent / "logs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(log_file: Path, event: dict) -> None:
    with log_file.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _compute_cost(usage: dict, model_id: str) -> float:
    p = _PRICING.get(model_id, _PRICING["claude-sonnet-4-6"])
    return (
        usage.get("inputTokens", 0)            / 1e6 * p["input"]
        + usage.get("cacheWriteInputTokens", 0) / 1e6 * p["cache_write"]
        + usage.get("cacheReadInputTokens", 0)  / 1e6 * p["cache_read"]
        + usage.get("outputTokens", 0)          / 1e6 * p["output"]
    )


def _ask(agent, question: str, log_file: Path, question_num: int, run_id: str) -> None:
    print(f"\n> {question}\n")
    t0 = datetime.now(timezone.utc)
    result = agent(question)
    duration = (datetime.now(timezone.utc) - t0).total_seconds()

    metrics = result.metrics if result and result.metrics else None
    summary = metrics.get_summary() if metrics else {}
    usage = summary.get("accumulated_usage", {})
    tool_usage = summary.get("tool_usage", {})
    cycles = summary.get("total_cycles", 0)

    cost = _compute_cost(usage, MODEL_ID)

    # Compact token line
    if usage:
        print(
            f"\n[{duration:.1f}s | {cycles} cycles | "
            f"in={usage.get('inputTokens', 0):,} "
            f"cache_r={usage.get('cacheReadInputTokens', 0):,} "
            f"out={usage.get('outputTokens', 0):,} | "
            f"${cost:.4f}]"
        )

    # Full trace tree: each Trace includes name, timing, message (tool calls + text),
    # and children (nested tool-result traces).
    traces = [t.to_dict() for t in (metrics.traces if metrics else [])]

    _write_log(log_file, {
        "ts": _now(),
        "event": "qa_question",
        "run_id": run_id,
        "question_num": question_num,
        "question": question,
        "answer": str(result) if result else "",
        "metrics": {
            "total_cycles": cycles,
            "total_duration": duration,
            "accumulated_usage": usage,
            "tool_usage": {
                name: {
                    "call_count": info.get("execution_stats", {}).get("call_count", 0),
                    "error_count": info.get("execution_stats", {}).get("error_count", 0),
                }
                for name, info in tool_usage.items()
            },
        },
        "traces": traces,
    })


def _print_cost(log_path: str) -> None:
    path = Path(log_path)
    if not path.exists():
        sys.exit(f"Log not found: {log_path}")

    entries = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    start = next((e for e in entries if e.get("event") == "session_start"), {})
    questions = [e for e in entries if e.get("event") == "qa_question"]

    model_id = start.get("model_id", MODEL_ID)
    p = _PRICING.get(model_id, _PRICING["claude-sonnet-4-6"])

    total_input = total_cw = total_cr = total_out = 0
    for q in questions:
        u = q.get("metrics", {}).get("accumulated_usage", {})
        total_input += u.get("inputTokens", 0)
        total_cw    += u.get("cacheWriteInputTokens", 0)
        total_cr    += u.get("cacheReadInputTokens", 0)
        total_out   += u.get("outputTokens", 0)

    cost = (
        total_input / 1e6 * p["input"]
        + total_cw   / 1e6 * p["cache_write"]
        + total_cr   / 1e6 * p["cache_read"]
        + total_out  / 1e6 * p["output"]
    )

    print(f"Log      : {path.name}")
    print(f"Model    : {model_id}")
    print(f"Questions: {len(questions)}")
    print()
    print(f"Input        : {total_input:>12,}   ${total_input / 1e6 * p['input']:.4f}")
    print(f"Cache write  : {total_cw:>12,}   ${total_cw / 1e6 * p['cache_write']:.4f}")
    print(f"Cache read   : {total_cr:>12,}   ${total_cr / 1e6 * p['cache_read']:.4f}")
    print(f"Output       : {total_out:>12,}   ${total_out / 1e6 * p['output']:.4f}")
    print()
    print(f"Total cost   :                ${cost:.4f}")
    if questions:
        print(f"Cost/question:                ${cost / len(questions):.4f}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "cost":
        if len(sys.argv) < 3:
            sys.exit("Usage: python query/main.py cost <log_file>")
        _print_cost(sys.argv[2])
        return

    LOG_DIR.mkdir(exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_file = LOG_DIR / f"{run_id}_qa.jsonl"

    _write_log(log_file, {
        "ts": _now(),
        "event": "session_start",
        "run_id": run_id,
        "model_id": MODEL_ID,
        "neo4j_uri": os.environ.get("NEO4J_URI", ""),
        "neo4j_database": os.environ.get("NEO4J_DATABASE", ""),
    })
    print(f"Logging to {log_file}")

    agent = build_agent()
    question_num = 0

    if len(sys.argv) > 1:
        question_num += 1
        _ask(agent, " ".join(sys.argv[1:]), log_file, question_num, run_id)
    else:
        print("Query agent REPL. Empty line or Ctrl-D to exit.\n")
        while True:
            try:
                question = input("? ").strip()
            except EOFError:
                print()
                break
            if not question:
                break
            question_num += 1
            _ask(agent, question, log_file, question_num, run_id)

    # Session summary
    _write_log(log_file, {
        "ts": _now(),
        "event": "session_end",
        "run_id": run_id,
        "question_count": question_num,
    })
    print(f"\nSession ended. {question_num} question(s) logged to {log_file.name}")


if __name__ == "__main__":
    main()
