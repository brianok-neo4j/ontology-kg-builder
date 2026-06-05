"""Per-run metrics JSONL logging for the eval harness.

Writes the same `<run_id>_metrics.jsonl` format the ingest/query tools emit — a
`run_start` header followed by per-call records carrying a top-level `usage`
delta, `cycles`, and `duration_s` — so `scripts/cost_watch.py` and
`python -m ingest.main cost` can analyze eval runs exactly like ingest runs.

One file is written **per model** (and one per judge model). Keeping each file
single-model means the cost tools — which price a whole log by its `run_start`
`model_id` — stay exact even for an A/B run that mixes models, and the judge
(typically a pricier model) is costed at its own rate rather than the answer
model's.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"


def _slug(model_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", model_id).strip("-")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_log(run_id: str, command: str, model_id: str, role: str, **params) -> Path:
    """Create a metrics log for one model and write its `run_start` record.

    Args:
        run_id:   Shared UTC stamp for the whole eval run (groups the per-model files).
        command:  e.g. "query-ab".
        model_id: The model this file logs (priced against this id by the cost tools).
        role:     "query" or "judge" — distinguishes the answer model from the judge.
        params:   Extra run_start fields (e.g. question_count, chunk_count).
    """
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{run_id}_{role}_{_slug(model_id)}_metrics.jsonl"
    record = {
        "ts": _now(),
        "event": "run_start",
        "run_id": run_id,
        "command": command,
        "model_id": model_id,
        "role": role,
        **params,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return path


def log_call(path: Path, event: str, **fields) -> None:
    """Append one per-call metrics record (usage/cycles/duration_s + extras)."""
    record = {"ts": _now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
