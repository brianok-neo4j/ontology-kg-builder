"""A/B model harness — CLI.

A/B testing is scoped to the INSTANCE and QUERY phases only. The ontology is
built once with a single model so every A/B run starts from a common base; this
harness never varies the ontology model.

    # Compare query-phase models over the eval question set (read-only, safe):
    python -m eval.main query-ab
    python -m eval.main query-ab --models claude-sonnet-4-6 claude-haiku-4-5-20251001
    python -m eval.main query-ab --limit 5            # first 5 questions only

    # Compare instance-phase models on a chunk sample (DESTRUCTIVE — see below):
    python -m eval.main instance-ab path/to/document.pdf            # prints plan, does nothing
    python -m eval.main instance-ab path/to/document.pdf --confirm-wipe --limit 25

Reports are written to eval/results/ (markdown + raw JSON).

instance-ab wipes the instance layer (ontology + Document/Chunk preserved)
between model runs to keep each model's extraction isolated, so point
NEO4J_DATABASE at a scratch graph or one you're happy to rebuild.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from eval.models import DEFAULT_MODELS
from eval.questions import FLTCA_QUESTIONS

_OUT_DEFAULT = str(Path(__file__).parent / "results")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B model harness for the instance and query phases."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    q = sub.add_parser("query-ab", help="A/B the query phase over the eval question set.")
    q.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                   help=f"Model ids to compare (default: {' '.join(DEFAULT_MODELS)}).")
    q.add_argument("--limit", type=int, default=None,
                   help="Use only the first N questions.")
    q.add_argument("--judge", action="store_true",
                   help="Grade each answer with the LLM judge (Excellent/Good/Partial/Weak).")
    q.add_argument("--source", default=None, metavar="PATH",
                   help="Source document(s) used to build the graph, given to the judge "
                        "as ground truth (file or folder). Optional but recommended with --judge.")
    q.add_argument("--judge-model", default=None, metavar="MODEL_ID",
                   help="Model for the judge (default: a strong independent model).")
    q.add_argument("--out", default=_OUT_DEFAULT, help="Output directory for reports.")

    i = sub.add_parser(
        "instance-ab",
        help="A/B the instance phase. DESTRUCTIVE: wipes the instance layer between runs.",
    )
    i.add_argument("path", help="Documents file or folder.")
    i.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                   help=f"Model ids to compare (default: {' '.join(DEFAULT_MODELS)}).")
    i.add_argument("--limit", type=int, default=25,
                   help="Process only the first N chunks (default: 25; use 0 for all).")
    i.add_argument("--concurrency", type=int,
                   default=int(os.environ.get("INSTANCE_CONCURRENCY", "5")),
                   help="Parallel workers per instance run (default: 5).")
    i.add_argument("--confirm-wipe", action="store_true",
                   help="Required to actually run: confirms deleting the instance "
                        "layer between model runs.")
    i.add_argument("--out", default=_OUT_DEFAULT, help="Output directory for reports.")

    args = parser.parse_args()

    if args.command == "query-ab":
        from eval.query_ab import run_query_ab
        questions = FLTCA_QUESTIONS[: args.limit] if args.limit else FLTCA_QUESTIONS
        run_query_ab(
            args.models, questions, Path(args.out),
            judge=args.judge, source=args.source, judge_model=args.judge_model,
        )
    elif args.command == "instance-ab":
        from eval.instance_ab import run_instance_ab
        run_instance_ab(
            args.path,
            args.models,
            Path(args.out),
            limit=(args.limit or None),
            concurrency=args.concurrency,
            confirm_wipe=args.confirm_wipe,
        )


if __name__ == "__main__":
    main()
