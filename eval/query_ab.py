"""A/B harness for the QUERY phase.

Runs the same question set through the query agent with each candidate model,
against the *same* (read-only) graph — no isolation needed — and reports
per-question cost, latency, cycles, and the answer text for side-by-side
quality review.

Quality is not auto-graded here: the harness captures each model's answer so a
human (or a follow-up LLM judge) can compare them. Cost/latency/cycles are
measured directly. Cycle count is a useful quality proxy — a model that needs
many more cycles is struggling to ground entities or compose Cypher.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from query.agent import build_agent as build_query_agent
from eval.models import USAGE_KEYS, cost_of
from eval.judge import (
    GRADES, build_judge, grade_answer,
    build_judge_no_tools, grade_answer_with_reference,
)
from eval.metrics_log import log_call, open_log


def load_source_text(source: str | None) -> str | None:
    """Concatenate the source document(s) into one text blob for the judge.

    Uses the same chunker the pipeline ingested with, so the judge sees exactly
    the text that produced the graph.
    """
    if not source:
        return None
    from shared.document import load_documents
    chunks = [chunk for _, chunk, _ in load_documents(source)]
    return "\n\n".join(chunks) if chunks else None


def _run_one_model(
    model_id: str,
    questions: list[str],
    run_id: str,
    question_indices: list[int] | None = None,
) -> list[dict]:
    """Run every question through one model; return per-question records.

    One agent per model, conversation reset per question so each question is
    independent and its metrics are isolated. accumulated_usage is cumulative
    across questions, so we diff against the previous question.

    question_indices, when provided, maps each position to its original 1-based
    question number so --only runs preserve correct groundtruth lookup and report
    labels. When absent, positions are numbered 1..N as usual.

    Also writes a per-model `<run_id>_query_<model>_metrics.jsonl` in the
    ingest/query metrics format so `cost_watch.py` / `ingest cost` can read it.
    """
    agent = build_query_agent(model_id=model_id)
    log_path = open_log(
        run_id, "query-ab", model_id, role="query",
        question_count=len(questions), chunk_count=len(questions),
    )
    prev = {k: 0 for k in USAGE_KEYS}
    prev_cycles = 0
    prev_trace_n = 0
    rows: list[dict] = []
    for pos, q in enumerate(questions):
        i = question_indices[pos] if question_indices else pos + 1
        agent.messages = []
        print(f"  [{model_id}] Q{i}/{len(questions)}: {q[:60]}...", flush=True)
        t0 = time.time()
        try:
            result = agent(q)
            answer = str(result)
            metrics = result.metrics if result and result.metrics else None
            summary = metrics.get_summary() if metrics else {}
            all_traces = [t.to_dict() for t in (metrics.traces if metrics else [])]
        except Exception as exc:  # noqa: BLE001 — record the failure, keep going
            answer = f"ERROR: {type(exc).__name__}: {exc}"
            summary = {}
            all_traces = []
        duration = time.time() - t0

        cum = summary.get("accumulated_usage", {})
        cum_cycles = summary.get("total_cycles", 0)
        usage = {k: cum.get(k, 0) - prev[k] for k in USAGE_KEYS}
        cycles = cum_cycles - prev_cycles
        traces = all_traces[prev_trace_n:]
        prev = {k: cum.get(k, 0) for k in USAGE_KEYS}
        prev_cycles = cum_cycles
        prev_trace_n = len(all_traces)
        cost = cost_of(usage, model_id)

        rows.append({
            "q": i,
            "question": q,
            "model": model_id,
            "answer": answer,
            "usage": usage,
            "cycles": cycles,
            "duration_s": round(duration, 2),
            "cost": cost,
        })
        log_call(
            log_path, "query_question",
            model_id=model_id, question_num=i, question=q, answer=answer,
            usage=usage, cycles=cycles, duration_s=round(duration, 2), cost=cost,
            error=answer.startswith("ERROR:"),
            traces=traces,
        )
    return rows


def _judge_all(
    by_model: dict[str, list[dict]], source: str | None,
    judge_model: str | None, run_id: str,
    groundtruth: list[dict] | None = None,
) -> None:
    """Grade every answer in-place with the LLM judge (adds grade/rationale/evidence).

    If groundtruth is provided (a list of reference entries from eval/groundtruth.py),
    the judge is tool-free and grades against the stored reference — faster, cheaper,
    and consistent. Otherwise falls back to the original path: source-doc + web search.

    Writes a `<run_id>_judge_<model>_metrics.jsonl` so judging cost is tracked in
    the same format as everything else, priced at the judge model's own rate.
    """
    n_gradings = sum(len(rows) for rows in by_model.values())

    if groundtruth:
        gt_by_index = {entry["index"]: entry for entry in groundtruth}
        print(f"\n=== Judging answers against stored groundtruth "
              f"({judge_model or 'default judge'}, {n_gradings} gradings) ===")
        judge = build_judge_no_tools(model_id=judge_model)
        log_path = open_log(
            run_id, "query-ab", judge._model_id, role="judge",
            question_count=n_gradings, chunk_count=n_gradings,
        )
        for model, rows in by_model.items():
            for r in rows:
                ref_entry = gt_by_index.get(r["q"])
                if ref_entry is None:
                    print(f"  [{model}] Q{r['q']}: no groundtruth entry — skipping")
                    r["grade"] = "Ungraded"
                    continue
                verdict = grade_answer_with_reference(judge, r["question"], r["answer"], ref_entry)
                r.update(verdict)
                print(f"  [{model}] Q{r['q']}: {verdict['grade']}")
                log_call(
                    log_path, "judge",
                    model_id=judge._model_id, answer_model=model, question_num=r["q"],
                    usage=verdict.get("judge_usage", {}), cost=verdict.get("judge_cost", 0.0),
                    grade=verdict.get("grade", ""),
                )
        return

    doc_text = load_source_text(source)
    if source and not doc_text:
        print(f"  ! could not load source document {source!r}; judging with web + knowledge only")
    print(f"\n=== Judging answers ({judge_model or 'default judge'}"
          f"{', with source doc' if doc_text else ', no source doc'}) ===")
    judge = build_judge(doc_text=doc_text, model_id=judge_model)
    log_path = open_log(
        run_id, "query-ab", judge._model_id, role="judge",
        question_count=n_gradings, chunk_count=n_gradings,
    )
    for model, rows in by_model.items():
        for r in rows:
            verdict = grade_answer(judge, r["question"], r["answer"])
            r.update(verdict)
            print(f"  [{model}] Q{r['q']}: {verdict['grade']}")
            log_call(
                log_path, "judge",
                model_id=judge._model_id, answer_model=model, question_num=r["q"],
                usage=verdict.get("judge_usage", {}), cost=verdict.get("judge_cost", 0.0),
                grade=verdict.get("grade", ""),
            )


def run_query_ab(
    models: list[str],
    questions: list[str],
    out_dir: Path,
    judge: bool = False,
    source: str | None = None,
    judge_model: str | None = None,
    groundtruth: list[dict] | None = None,
    question_indices: list[int] | None = None,
) -> Path:
    """Run the question set across every model and write a comparison report.

    If judge=True, an LLM judge grades each answer (Excellent/Good/Partial/Weak).
    When groundtruth is provided, the judge grades against the stored reference
    (no web/source re-research). Otherwise falls back to source-doc + web judging.

    Returns the path to the markdown report.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    by_model: dict[str, list[dict]] = {}
    for model in models:
        print(f"\n=== Query A/B: {model} ({len(questions)} questions) ===")
        by_model[model] = _run_one_model(model, questions, stamp, question_indices)

    if judge or groundtruth:
        _judge_all(by_model, source, judge_model, stamp, groundtruth=groundtruth)

    raw_path = out_dir / f"query_ab_{stamp}.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for model in models:
            for row in by_model[model]:
                f.write(json.dumps(row) + "\n")

    report_path = out_dir / f"query_ab_{stamp}.md"
    _write_report(report_path, models, questions, by_model)
    print(f"\nReport : {report_path}\nRaw    : {raw_path}")
    return report_path


def _totals(rows: list[dict]) -> dict:
    return {
        "cost": sum(r["cost"] for r in rows),
        "judge_cost": sum(r.get("judge_cost", 0.0) for r in rows),
        "duration_s": sum(r["duration_s"] for r in rows),
        "cycles": sum(r["cycles"] for r in rows),
        "errors": sum(1 for r in rows if r["answer"].startswith("ERROR:")),
    }


def _write_report(path: Path, models, questions, by_model) -> None:
    lines: list[str] = []
    lines.append(f"# Query A/B comparison\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Models: {', '.join(models)}")
    lines.append(f"Questions: {len(questions)}\n")

    n = len(questions)
    judged = any("grade" in r for rows in by_model.values() for r in rows)

    # Aggregate totals
    lines.append("## Totals\n")
    if judged:
        lines.append("| Model | Answer cost | Judge cost | Total cost | Total time | Total cycles | Errors | answer $/q | s/q |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for m in models:
            t = _totals(by_model[m])
            lines.append(
                f"| `{m}` | ${t['cost']:.4f} | ${t['judge_cost']:.4f} | "
                f"${t['cost'] + t['judge_cost']:.4f} | {t['duration_s']:.1f}s | {t['cycles']} | "
                f"{t['errors']} | ${t['cost']/n:.4f} | {t['duration_s']/n:.1f} |"
            )
    else:
        lines.append("| Model | Total cost | Total time | Total cycles | Errors | $/q | s/q |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for m in models:
            t = _totals(by_model[m])
            lines.append(
                f"| `{m}` | ${t['cost']:.4f} | {t['duration_s']:.1f}s | {t['cycles']} | "
                f"{t['errors']} | ${t['cost']/n:.4f} | {t['duration_s']/n:.1f} |"
            )
    lines.append("")

    # Grade distribution (if judged)
    if judged:
        lines.append("## Grades (LLM judge)\n")
        lines.append("| Model | " + " | ".join(GRADES) + " | Other |")
        lines.append("|---|" + "---:|" * (len(GRADES) + 1))
        for m in models:
            counts = {g: 0 for g in GRADES}
            other = 0
            for r in by_model[m]:
                g = r.get("grade", "")
                if g in counts:
                    counts[g] += 1
                else:
                    other += 1
            row = " | ".join(str(counts[g]) for g in GRADES)
            lines.append(f"| `{m}` | {row} | {other} |")
        lines.append("")

    # Per-question cost/latency/cycles
    cost_legend = "answer$ + judge$ / s / cyc" if judged else "$ / s / cyc"
    lines.append(f"## Per-question cost / latency / cycles\n")
    lines.append(f"_Cell: {cost_legend}_\n")
    header = "| Q | " + " | ".join(f"`{m}`" for m in models) + " |"
    lines.append(header)
    lines.append("|---|" + "---|" * len(models))
    for i in range(n):
        cells = []
        q_num = by_model[models[0]][i]["q"]
        for m in models:
            r = by_model[m][i]
            if judged:
                cells.append(
                    f"${r['cost']:.4f} + ${r.get('judge_cost', 0.0):.4f} / "
                    f"{r['duration_s']:.1f}s / {r['cycles']}"
                )
            else:
                cells.append(f"${r['cost']:.4f} / {r['duration_s']:.1f}s / {r['cycles']}")
        lines.append(f"| {q_num} | " + " | ".join(cells) + " |")
    lines.append("")

    # Answers side by side for review
    lines.append("## Answers" + (" and grades" if judged else " (for quality review)") + "\n")
    for i, q in enumerate(questions):
        q_num = by_model[models[0]][i]["q"]
        lines.append(f"### Q{q_num}. {q}\n")
        for m in models:
            r = by_model[m][i]
            grade = f" — **{r['grade']}**" if judged else ""
            judge_c = f", judge ${r.get('judge_cost', 0.0):.4f}" if judged else ""
            lines.append(
                f"**`{m}`**{grade} ({r['cycles']} cycles, {r['duration_s']:.1f}s, "
                f"answer ${r['cost']:.4f}{judge_c}):\n"
            )
            lines.append(f"> {r['answer'].strip().replace(chr(10), chr(10) + '> ')}\n")
            if judged and r.get("rationale"):
                lines.append(f"_Judge ({r.get('grade')}): {r['rationale']}_\n")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
