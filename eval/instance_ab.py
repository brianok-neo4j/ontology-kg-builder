"""A/B harness for the INSTANCE phase.

Runs the instance stage with each candidate model over the SAME documents and
the SAME pre-built ontology (the common base — ontology is built once with a
single model and is not varied), then compares cost, latency, and extraction
quality.

Isolation: instance extraction MERGEs entities by `name`, so two models writing
into one graph would conflate. The harness therefore wipes the instance layer
(everything except the ontology `EntityType` nodes and the `Document`/`Chunk`
provenance nodes) *between* model runs, so each model is measured on a clean
graph over a common ontology.

⚠️  This DELETES instance data in the target Neo4j database. It is gated behind
an explicit confirm flag, prints the target database first, and is intended to
run against a scratch graph (or one you are happy to rebuild). The ontology and
Document/Chunk nodes are preserved.

Quality is reported as extraction stats (entity/relationship counts, per-label
breakdown, chunk coverage) plus a full dump of the extracted (label, name) and
relationships per model for manual / diff review. Cost and latency are measured
directly from the run.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from shared.neo4j_tools import _run
from ingest.main import run_instance
from eval.models import USAGE_KEYS, cost_of


# Labels that are NOT instance data: the ontology layer and provenance nodes.
_NON_INSTANCE = ("EntityType", "Document", "Chunk")
_NON_INSTANCE_PRED = " AND ".join(f"NOT n:{label}" for label in _NON_INSTANCE)


def _instance_node_count() -> int:
    rows = _run(f"MATCH (n) WHERE {_NON_INSTANCE_PRED} RETURN count(n) AS c")
    return rows[0]["c"] if rows else 0


def _ontology_exists() -> bool:
    rows = _run("MATCH (e:EntityType) RETURN count(e) AS c")
    return bool(rows and rows[0]["c"] > 0)


def _reset_instance_layer() -> int:
    """Delete all instance nodes (keep ontology + Document/Chunk). Returns count removed."""
    before = _instance_node_count()
    # Batched so large graphs don't blow the transaction up.
    _run(
        f"""
        MATCH (n) WHERE {_NON_INSTANCE_PRED}
        CALL {{ WITH n DETACH DELETE n }} IN TRANSACTIONS OF 1000 ROWS
        """
    )
    return before


def _extraction_stats() -> dict:
    """Quantitative quality proxies for the current instance graph."""
    ents = _run(
        f"MATCH (n) WHERE {_NON_INSTANCE_PRED} "
        "RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC"
    )
    rels = _run(
        f"MATCH (a)-[r]->(b) WHERE ({_NON_INSTANCE_PRED.replace('n:', 'a:')}) "
        f"AND ({_NON_INSTANCE_PRED.replace('n:', 'b:')}) "
        "RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
    )
    total_chunks = _run("MATCH (c:Chunk) RETURN count(c) AS c")[0]["c"]
    covered = _run(
        "MATCH (c:Chunk) WHERE (c)<-[:FROM_CHUNK]-() RETURN count(c) AS c"
    )[0]["c"]
    return {
        "entity_count": sum(r["c"] for r in ents),
        "relationship_count": sum(r["c"] for r in rels),
        "entities_by_label": {r["label"]: r["c"] for r in ents},
        "relationships_by_type": {r["t"]: r["c"] for r in rels},
        "chunks_total": total_chunks,
        "chunks_with_entities": covered,
        "chunk_coverage": round(covered / total_chunks, 3) if total_chunks else 0.0,
    }


def _dump_extractions(out_dir: Path, model: str, stamp: str) -> Path:
    """Dump every instance entity and relationship for manual/diff review."""
    ents = _run(
        f"MATCH (n) WHERE {_NON_INSTANCE_PRED} "
        "RETURN labels(n)[0] AS label, n.name AS name ORDER BY label, name"
    )
    rels = _run(
        f"MATCH (a)-[r]->(b) WHERE ({_NON_INSTANCE_PRED.replace('n:', 'a:')}) "
        f"AND ({_NON_INSTANCE_PRED.replace('n:', 'b:')}) "
        "RETURN a.name AS from, type(r) AS rel, b.name AS to, r.detail AS detail "
        "ORDER BY from, rel, to"
    )
    path = out_dir / f"instance_ab_{model.replace('/', '_')}_{stamp}.json"
    path.write_text(
        json.dumps({"model": model, "entities": ents, "relationships": rels}, indent=2),
        encoding="utf-8",
    )
    return path


def _sum_usage_from_log(log_file: Path) -> dict:
    """Sum the per-chunk `usage` deltas written by the instance run."""
    totals = {k: 0 for k in USAGE_KEYS}
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        u = rec.get("usage") or {}
        for k in USAGE_KEYS:
            totals[k] += u.get(k, 0)
    return totals


def run_instance_ab(
    path: str,
    models: list[str],
    out_dir: Path,
    limit: int | None,
    concurrency: int,
    confirm_wipe: bool,
) -> Path | None:
    """Run instance extraction with each model on a clean ontology and compare.

    Returns the report path, or None if not confirmed / preconditions unmet.
    """
    db = os.environ.get("NEO4J_DATABASE") or "<server default>"

    if not _ontology_exists():
        print("No EntityType nodes found — build the ontology first "
              "(python ingest/main.py ontology ... && enhance). Aborting.")
        return None

    if not confirm_wipe:
        n = _instance_node_count()
        print(
            "\n⚠️  instance-ab will DELETE the instance layer between model runs.\n"
            f"    Target Neo4j database : {db}\n"
            f"    Instance nodes to wipe: {n:,} (ontology + Document/Chunk preserved)\n"
            f"    Models                : {', '.join(models)}\n"
            f"    Chunks                : {'first ' + str(limit) if limit else 'ALL'} of {path}\n\n"
            "    Re-run with --confirm-wipe to proceed. Point NEO4J_DATABASE at a\n"
            "    scratch graph if you don't want to disturb your main graph."
        )
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results: dict[str, dict] = {}

    for model in models:
        print(f"\n=== Instance A/B: {model} ===")
        removed = _reset_instance_layer()
        print(f"  reset instance layer ({removed:,} nodes removed)")
        t0 = time.time()
        log_file = run_instance(path, limit=limit, model_id=model, concurrency=concurrency)
        wall = time.time() - t0
        usage = _sum_usage_from_log(log_file)
        stats = _extraction_stats()
        dump = _dump_extractions(out_dir, model, stamp)
        results[model] = {
            "usage": usage,
            "cost": cost_of(usage, model),
            "wall_s": round(wall, 1),
            "stats": stats,
            "dump": str(dump),
            "log": str(log_file),
        }
        print(f"  cost=${results[model]['cost']:.4f}  wall={wall:.0f}s  "
              f"entities={stats['entity_count']}  rels={stats['relationship_count']}  "
              f"coverage={stats['chunk_coverage']}")

    report_path = out_dir / f"instance_ab_{stamp}.md"
    _write_report(report_path, path, models, limit, db, results)
    print(f"\nReport: {report_path}")
    print("Note: the graph now holds the LAST model's extraction. Re-run the "
          "instance stage with your chosen model to repopulate fully.")
    return report_path


def _write_report(path, doc_path, models, limit, db, results) -> None:
    lines: list[str] = []
    lines.append("# Instance A/B comparison\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Documents: {doc_path}")
    lines.append(f"Chunks: {'first ' + str(limit) if limit else 'all'}")
    lines.append(f"Database: {db}")
    lines.append(f"Models: {', '.join(models)}\n")

    lines.append("## Cost / latency\n")
    lines.append("| Model | Cost | Wall time | Input | Output | CacheWrite | CacheRead |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for m in models:
        r = results[m]; u = r["usage"]
        lines.append(
            f"| `{m}` | ${r['cost']:.4f} | {r['wall_s']:.0f}s | "
            f"{u['inputTokens']:,} | {u['outputTokens']:,} | "
            f"{u['cacheWriteInputTokens']:,} | {u['cacheReadInputTokens']:,} |"
        )
    lines.append("")

    lines.append("## Extraction stats (quality proxies)\n")
    lines.append("| Model | Entities | Relationships | Chunk coverage |")
    lines.append("|---|---:|---:|---:|")
    for m in models:
        s = results[m]["stats"]
        lines.append(
            f"| `{m}` | {s['entity_count']} | {s['relationship_count']} | "
            f"{s['chunk_coverage']} ({s['chunks_with_entities']}/{s['chunks_total']}) |"
        )
    lines.append("")

    lines.append("### Entities by label\n")
    all_labels = sorted({l for m in models for l in results[m]["stats"]["entities_by_label"]})
    lines.append("| Label | " + " | ".join(f"`{m}`" for m in models) + " |")
    lines.append("|---|" + "---:|" * len(models))
    for lab in all_labels:
        cells = [str(results[m]["stats"]["entities_by_label"].get(lab, 0)) for m in models]
        lines.append(f"| {lab} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Artifacts\n")
    for m in models:
        lines.append(f"- `{m}`: extractions → `{results[m]['dump']}`; run log → `{results[m]['log']}`")
    lines.append("\nDiff the per-model extraction dumps to inspect what the cheaper "
                 "model missed or added (the known instance-stage failure mode is "
                 "under-extraction, so compare entity/relationship coverage closely).")

    path.write_text("\n".join(lines), encoding="utf-8")
