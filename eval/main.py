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
_GT_DEFAULT = str(Path(__file__).parent / "groundtruth")


def _load_questions(spec: str) -> list[str]:
    """Load a question list from a Python module path (e.g. faa.questions) or .py file."""
    import importlib
    import importlib.util

    path = Path(spec)
    if path.suffix == ".py" and path.exists():
        module_name = path.stem
        spec_obj = importlib.util.spec_from_file_location(module_name, path)
        mod = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(mod)
    else:
        mod = importlib.import_module(spec)

    # Look for any list[str] attribute whose name looks like QUESTIONS
    for attr in dir(mod):
        if "QUESTIONS" in attr.upper():
            val = getattr(mod, attr)
            if isinstance(val, list) and val and isinstance(val[0], str):
                return val
    raise ValueError(f"No QUESTIONS list found in {spec!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B model harness for the instance and query phases."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── groundtruth ────────────────────────────────────────────────────────────
    gt = sub.add_parser(
        "groundtruth",
        help="Generate reference answers + grading criteria for a question set (run once).",
    )
    gt.add_argument(
        "questions", metavar="QUESTIONS",
        help="Python module path (e.g. faa.questions) or .py file containing a QUESTIONS list.",
    )
    gt.add_argument("--source", required=True, metavar="PATH",
                    help="Source document(s) to research against (same path used for ingestion).")
    gt.add_argument("--out", default=None, metavar="PATH",
                    help=f"Output JSON path (default: {_GT_DEFAULT}/<module_stem>.json).")
    gt.add_argument("--model", default=None, metavar="MODEL_ID",
                    help="Researcher model (default: claude-opus-4-8).")
    gt.add_argument("--resume", action="store_true",
                    help="Skip questions already present in the output file.")

    # ── query-ab ───────────────────────────────────────────────────────────────
    q = sub.add_parser("query-ab", help="A/B the query phase over the eval question set.")
    q.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                   help=f"Model ids to compare (default: {' '.join(DEFAULT_MODELS)}).")
    q.add_argument("--questions", default=None, metavar="QUESTIONS",
                   help="Python module path or .py file for questions (default: FLTCA built-in).")
    q.add_argument("--limit", type=int, default=None,
                   help="Use only the first N questions.")
    q.add_argument("--only", default=None, metavar="N[,N...]",
                   help="Comma-separated question numbers to run (1-based, e.g. --only 11 or "
                        "--only 11,12). Applied after --limit.")
    q.add_argument("--judge", action="store_true",
                   help="Grade each answer with the LLM judge (Excellent/Good/Partial/Weak).")
    q.add_argument("--groundtruth", default=None, metavar="PATH",
                   help="Path to a groundtruth JSON file produced by the groundtruth subcommand. "
                        "When provided, the judge grades against stored references instead of "
                        "re-researching (faster, cheaper, consistent). Implies --judge.")
    q.add_argument("--source", default=None, metavar="PATH",
                   help="Source document(s) used to build the graph, given to the judge "
                        "as ground truth (file or folder). Used only when --groundtruth is absent.")
    q.add_argument("--judge-model", default=None, metavar="MODEL_ID",
                   help="Model for the judge (default: a strong independent model).")
    q.add_argument("--out", default=_OUT_DEFAULT, help="Output directory for reports.")

    # ── instance-ab ────────────────────────────────────────────────────────────
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

    if args.command == "groundtruth":
        from eval.groundtruth import generate_groundtruth
        questions = _load_questions(args.questions)
        if args.out:
            out_path = args.out
        else:
            stem = Path(args.questions).stem if Path(args.questions).suffix == ".py" else args.questions.split(".")[-1]
            out_path = str(Path(_GT_DEFAULT) / f"{stem}.json")
        generate_groundtruth(
            questions=questions,
            source=args.source,
            out_path=out_path,
            model_id=args.model,
            resume=args.resume,
        )

    elif args.command == "query-ab":
        from eval.query_ab import run_query_ab

        if args.questions:
            questions = _load_questions(args.questions)
        else:
            questions = FLTCA_QUESTIONS
        if args.limit:
            questions = questions[: args.limit]

        question_indices: list[int] | None = None
        if args.only:
            only_set = {int(x) for x in args.only.replace(" ", ",").split(",") if x.strip()}
            pairs = [(idx, q) for idx, q in enumerate(questions, 1) if idx in only_set]
            question_indices = [idx for idx, _ in pairs]
            questions = [q for _, q in pairs]

        gt = None
        if args.groundtruth:
            from eval.groundtruth import load_groundtruth
            gt = load_groundtruth(args.groundtruth)

        run_query_ab(
            args.models, questions, Path(args.out),
            judge=args.judge, source=args.source,
            judge_model=args.judge_model, groundtruth=gt,
            question_indices=question_indices,
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
