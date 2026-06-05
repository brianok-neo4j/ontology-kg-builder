#!/usr/bin/env python3
"""Live cost analyzer for an ingest metrics JSONL log.

Safe to run against a log that is still being written by a running ingest
process: it reads whole lines, skips any partially-flushed trailing line, and
sums the per-call `usage` deltas (the format _per_call_metrics emits), which is
correct even when parallel instance workers interleave their records.

Usage:
    python scripts/cost_watch.py                       # newest *_metrics.jsonl
    python scripts/cost_watch.py path/to/run_metrics.jsonl
    python scripts/cost_watch.py --watch                # refresh every 10s
    python scripts/cost_watch.py --watch --interval 5
    python scripts/cost_watch.py --total-chunks 451     # project final cost
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Mirror ingest/main.py:_PRICING (USD per 1M tokens). Keep in sync.
PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "cache_write": 1.00, "cache_read": 0.08, "output": 4.00},
    "claude-opus-4-8": {"input": 15.00, "cache_write": 18.75, "cache_read": 1.50, "output": 75.00},
}

USAGE_KEYS = {
    "inputTokens": "input",
    "outputTokens": "output",
    "cacheWriteInputTokens": "cache_write",
    "cacheReadInputTokens": "cache_read",
}

LOG_DIR = Path(__file__).resolve().parent.parent / "ingest" / "logs"


def _read_records(path: Path) -> list[dict]:
    """Parse JSONL, tolerating a partially-written final line (live tail)."""
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # Last line may be mid-write by the running process — ignore it.
            continue
    return records


def _fmt_dur(seconds: float) -> str:
    """Human duration: '4m 12s', '1h 03m', '45s'."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def _parse_ts(rec: dict):
    ts = rec.get("ts")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def analyze(path: Path, total_chunks: int | None) -> str:
    records = _read_records(path)
    if not records:
        return f"{path.name}: no parseable records yet."

    model_id = command = run_id = None
    logged_total = None
    for r in records:
        if r.get("event") == "run_start":
            model_id = r.get("model_id")
            command = r.get("command")
            run_id = r.get("run_id")
            # The ingest run records the chunk count up front (load_documents),
            # so we can show progress/ETA without the caller supplying it. An
            # explicit --total-chunks still wins (e.g. a --limit/--resume run).
            logged_total = r.get("chunk_count")
            break

    if total_chunks is None:
        total_chunks = logged_total

    pricing = PRICING.get(model_id or "")
    if not pricing:
        return f"Model {model_id!r} not in PRICING ({', '.join(PRICING)})."

    totals = {v: 0 for v in USAGE_KEYS.values()}
    chunk_events = errors = 0
    chunk_costs = []          # per-chunk cost, for distribution
    cycles = 0
    durations = []            # per-call agent durations (model time, not wall)
    first_ts = last_ts = None

    for r in records:
        ev = r.get("event", "")
        ts = _parse_ts(r)
        if ts:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        if "_error" in ev:
            errors += 1
        u = r.get("usage")
        if not u:
            continue
        if ev in ("ontology_chunk", "instance_chunk", "enhancer"):
            chunk_events += 1
        c = 0.0
        for src, dst in USAGE_KEYS.items():
            tok = u.get(src, 0) or 0
            totals[dst] += tok
            c += tok / 1e6 * pricing[dst]
        chunk_costs.append(c)
        cycles += r.get("cycles", 0) or 0
        if r.get("duration_s"):
            durations.append(r["duration_s"])

    cost = sum(totals[dst] / 1e6 * pricing[dst] for dst in ("input", "output", "cache_write", "cache_read"))

    # Cache-read share of input tokens — the lever we tuned. High = good.
    cached = totals["cache_read"]
    fresh = totals["input"] + totals["cache_write"]
    cache_hit = cached / (cached + fresh) * 100 if (cached + fresh) else 0.0

    lines = []
    lines.append(f"Log        : {path.name}   (run_id {run_id})")
    lines.append(f"Command    : {command}    Model: {model_id}")
    done_str = f"{chunk_events} chunks"
    if total_chunks:
        done_str += f" / {total_chunks} ({chunk_events / total_chunks * 100:.0f}%)"
    lines.append(f"Chunks done: {done_str}   errors: {errors}")

    if first_ts and last_ts and last_ts > first_ts:
        # Elapsed: from the first record to NOW for an in-progress run (last_ts
        # only marks the most recent *write*, which lags a bit), but never past
        # a clearly-finished run — cap at last_ts + the interval since last write
        # so a stale, completed log reports its true span, not "hours ago".
        now = datetime.now(tz=first_ts.tzinfo) if first_ts.tzinfo else datetime.now()
        since_last_write = (now - last_ts).total_seconds()
        finished = since_last_write > 120  # no writes for 2 min ⇒ run is done/stalled
        end_ref = last_ts if finished else now
        elapsed = (end_ref - first_ts).total_seconds()
        rate = chunk_events / elapsed * 60 if elapsed else 0
        status = "elapsed (final)" if finished else "elapsed"
        lines.append(f"{status:<11}: {_fmt_dur(elapsed)}   throughput: {rate:.1f} chunks/min")
        lines.append(f"Started    : {first_ts.astimezone():%Y-%m-%d %H:%M:%S %Z}")

        if total_chunks and chunk_events < total_chunks and rate > 0:
            remaining = total_chunks - chunk_events
            eta_secs = remaining / (rate / 60)
            eta_at = now + timedelta(seconds=eta_secs)
            lines.append(
                f"ETA        : {_fmt_dur(eta_secs)} remaining  "
                f"→ ~{eta_at.astimezone():%H:%M:%S %Z}  ({remaining} chunks left)"
            )
        elif finished:
            lines.append(f"Finished   : {last_ts.astimezone():%Y-%m-%d %H:%M:%S %Z}")
    lines.append("")
    lines.append(f"  Input      : {totals['input']:>13,}   ${totals['input']/1e6*pricing['input']:.4f}")
    lines.append(f"  Cache write: {totals['cache_write']:>13,}   ${totals['cache_write']/1e6*pricing['cache_write']:.4f}")
    lines.append(f"  Cache read : {totals['cache_read']:>13,}   ${totals['cache_read']/1e6*pricing['cache_read']:.4f}")
    lines.append(f"  Output     : {totals['output']:>13,}   ${totals['output']/1e6*pricing['output']:.4f}")
    lines.append("")
    lines.append(f"  Cache hit rate (read / all input): {cache_hit:.1f}%")
    lines.append("")
    lines.append(f"Total so far : ${cost:.4f}")
    if chunk_events:
        avg = cost / chunk_events
        lines.append(f"Cost/chunk   : ${avg:.4f}   ({cycles/chunk_events:.1f} cycles/chunk avg)")
        if total_chunks and chunk_events < total_chunks:
            proj = avg * total_chunks
            lines.append(f"Projected    : ${proj:.4f}  (extrapolating ${avg:.4f}/chunk to {total_chunks} chunks)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_file", nargs="?", help="Path to *_metrics.jsonl (default: newest in ingest/logs)")
    ap.add_argument("--watch", action="store_true", help="Refresh continuously")
    ap.add_argument("--interval", type=float, default=10.0, help="Watch refresh seconds (default 10)")
    ap.add_argument("--total-chunks", type=int, default=None,
                    help="Override total chunks for ETA/projection "
                         "(default: read from the log's run_start chunk_count)")
    args = ap.parse_args()

    if args.log_file:
        path = Path(args.log_file)
    else:
        logs = sorted(LOG_DIR.glob("*_metrics.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            print(f"No *_metrics.jsonl in {LOG_DIR}", file=sys.stderr)
            return 1
        path = logs[0]

    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    if not args.watch:
        print(analyze(path, args.total_chunks))
        return 0

    try:
        while True:
            print("\033[2J\033[H", end="")  # clear screen
            print(f"watching {path.name}  (Ctrl-C to stop)  {datetime.now():%H:%M:%S}\n")
            print(analyze(path, args.total_chunks))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
