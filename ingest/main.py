"""Entry point for the multi-agent ontology builder.

Three subcommands, each invoking one agent in isolation:

    python main.py ontology <path>   # Agent 1 — build schema from documents
    python main.py enhance           # Agent 2 — review/refine schema in graph
    python main.py instance  <path>  # Agent 3 — extract instance data from documents

The agents share Neo4j as their only state, so the subcommands can be run in
any combination — typically `ontology` then `enhance` then `instance`, but
`enhance` can be re-run any time and `instance` can be re-run on new documents
against an existing schema.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from strands.types.exceptions import MaxTokensReachedException

from shared.document import load_documents
from shared.neo4j_tools import _run
from ingest.agents.ontology_agent import (
    MODEL_ID as ONTOLOGY_MODEL_ID,
    MODEL_MAX_TOKENS as ONTOLOGY_MODEL_MAX_TOKENS,
    _fetch_ontology_snapshot as fetch_ontology_snapshot,
    build_agent as build_ontology_agent,
    build_mcp_client as build_ontology_mcp,
)
from ingest.agents.enhancer_agent import (
    MODEL_ID as ENHANCER_MODEL_ID,
    MODEL_MAX_TOKENS as ENHANCER_MODEL_MAX_TOKENS,
    build_agent as build_enhancer_agent,
    build_mcp_client as build_enhancer_mcp,
)
from ingest.agents.instance_agent import (
    MODEL_ID as INSTANCE_MODEL_ID,
    MODEL_MAX_TOKENS as INSTANCE_MODEL_MAX_TOKENS,
    build_agent as build_instance_agent,
    build_mcp_client as build_instance_mcp,
)
from ingest.tools import create_document_node, create_chunk_node
from ingest.domain_vocab import (
    VOCABULARIES,
    DomainVocabulary,
    detect_domain,
)

LOG_DIR = Path(__file__).parent / "logs"

# Default for --verbose-summary across all subcommands. Override per-run with the flag.
_VERBOSE_SUMMARY_DEFAULT = os.environ.get("ONTOLOGY_VERBOSE_SUMMARY", "").lower() in (
    "1", "true", "yes"
)

# Per-million-token pricing. Verify against https://www.anthropic.com/pricing
# before relying on cost estimates for budgeting.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80, "cache_write": 1.00, "cache_read": 0.08, "output": 4.00,
    },
    "claude-opus-4-8": {
        "input": 15.00, "cache_write": 18.75, "cache_read": 1.50, "output": 75.00,
    },
}


def run_cost(log_path: str) -> None:
    """Parse a metrics JSONL log and print a cost breakdown."""
    records = [
        json.loads(l)
        for l in Path(log_path).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]

    model_id = command = None
    for r in records:
        if r.get("event") == "run_start":
            model_id = r.get("model_id")
            command = r.get("command")
            break

    pricing = _PRICING.get(model_id or "")
    if not pricing:
        known = ", ".join(_PRICING)
        print(f"Model '{model_id}' not in pricing table ({known}). Add it to _PRICING.")
        return

    # Strands reports *cumulative* usage on every record. An agent that is reused
    # for the whole run (instance / query / enhancer) therefore reports running
    # totals — summing the per-record values triangular-counts them (off by up to
    # ~100×). An agent that is rebuilt mid-run (ontology, rebuilt on schema change)
    # resets its counters at each rebuild. Summing per-record *deltas*, treating a
    # decrease as a reset, recovers the true total in both cases.
    _USAGE_KEYS = {
        "inputTokens": "total_input",
        "outputTokens": "total_output",
        "cacheWriteInputTokens": "total_cache_write",
        "cacheReadInputTokens": "total_cache_read",
    }
    totals = {v: 0 for v in _USAGE_KEYS.values()}
    prev = {k: 0 for k in _USAGE_KEYS}
    chunk_events = errors = 0
    for r in records:
        ev = r.get("event", "")
        if ev in ("ontology_chunk", "instance_chunk", "enhancer"):
            chunk_events += 1
        if "_error" in ev:
            errors += 1
        u = (r.get("metrics") or {}).get("accumulated_usage") or {}
        if not u:
            # run_start / run_resume / error records carry no usage — skip them
            # so they don't reset the delta tracking and double-count the next record.
            continue
        for key, tot_key in _USAGE_KEYS.items():
            cur = u.get(key, 0)
            # Monotonic increase → delta; decrease → agent was rebuilt, count the
            # full current value as this run-segment's fresh total.
            totals[tot_key] += cur - prev[key] if cur >= prev[key] else cur
            prev[key] = cur

    total_input       = totals["total_input"]
    total_cache_write = totals["total_cache_write"]
    total_cache_read  = totals["total_cache_read"]
    total_output      = totals["total_output"]

    cost = (
        total_input       / 1e6 * pricing["input"]       +
        total_cache_write / 1e6 * pricing["cache_write"] +
        total_cache_read  / 1e6 * pricing["cache_read"]  +
        total_output      / 1e6 * pricing["output"]
    )

    print(f"Log      : {Path(log_path).name}")
    print(f"Command  : {command}   Model: {model_id}")
    print(f"Events   : {chunk_events} completed, {errors} errors")
    print()
    print(f"Input        : {total_input:>12,}   ${total_input / 1e6 * pricing['input']:.4f}")
    print(f"Cache write  : {total_cache_write:>12,}   ${total_cache_write / 1e6 * pricing['cache_write']:.4f}")
    print(f"Cache read   : {total_cache_read:>12,}   ${total_cache_read / 1e6 * pricing['cache_read']:.4f}")
    print(f"Output       : {total_output:>12,}   ${total_output / 1e6 * pricing['output']:.4f}")
    print()
    print(f"Total cost   :              ${cost:.4f}")
    if chunk_events:
        print(f"Cost/event   :              ${cost / chunk_events:.4f}")


def _find_resumable_run(command: str, input_path: str) -> tuple[Path | None, int, str | None]:
    """Find the most recent interrupted run for (command, input_path).

    Scans all JSONL log files for a run_start matching (command, input_path),
    then counts successful chunk events to determine how far it got.

    Returns (log_file, n_done, run_id), or (None, 0, None) if nothing found.
    """
    if not LOG_DIR.exists():
        return None, 0, None

    chunk_event = "ontology_chunk" if command == "ontology" else "instance_chunk"
    best_ts: str | None = None
    best: tuple[Path, int, str] | None = None

    for log_file in LOG_DIR.glob("*_metrics.jsonl"):
        matched = False
        n_done = 0
        run_id = None
        try:
            with log_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    event = record.get("event")
                    if event == "run_start":
                        if (record.get("command") == command and
                                record.get("input_path") == input_path):
                            matched = True
                            run_id = record.get("run_id")
                            n_done = 0
                        else:
                            matched = False
                    elif matched and event == chunk_event:
                        n_done += 1
        except Exception:
            continue

        if matched and run_id:
            if best_ts is None or run_id > best_ts:
                best_ts = run_id
                best = (log_file, n_done, run_id)

    if best is None:
        return None, 0, None
    return best


def _write_resume_event(log_file: Path, n_done: int, total: int) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "run_resume",
        "chunks_already_done": n_done,
        "chunks_remaining": total - n_done,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _log(log_file: Path, event: str, result, **extra) -> None:
    try:
        summary = result.metrics.get_summary() if result and result.metrics else {}
    except Exception:
        summary = {}
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "metrics": summary,
        **extra,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _invoke_with_recovery(
    agent,
    prompt: str,
    log_file: Path,
    event: str,
    **log_extra,
):
    """Run an agent on a prompt and survive token-limit failures.

    Logs the conversation depth pre-call (a cheap proxy for input-token
    pressure) and on failure logs an error record and returns None so the
    caller can skip this chunk and continue. Without this, a single
    MaxTokensReachedException kills the whole multi-document run.
    """
    history_depth = len(getattr(agent, "messages", []) or [])
    try:
        result = agent(prompt)
        _log(
            log_file,
            event,
            result,
            history_depth_pre_call=history_depth,
            **log_extra,
        )
        return result
    except MaxTokensReachedException as exc:
        _log(
            log_file,
            f"{event}_error",
            None,
            error_type="MaxTokensReachedException",
            error_message=str(exc),
            history_depth_pre_call=history_depth,
            **log_extra,
        )
        print(f"\n    ! MaxTokensReachedException — skipping chunk, continuing run")
        return None
    except Exception as exc:  # noqa: BLE001 — keep the run alive on any agent fault
        _log(
            log_file,
            f"{event}_error",
            None,
            error_type=type(exc).__name__,
            error_message=str(exc),
            history_depth_pre_call=history_depth,
            **log_extra,
        )
        print(f"\n    ! {type(exc).__name__}: {exc} — skipping chunk, continuing run")
        return None


def _bootstrap_ontology_base(vocab: DomainVocabulary | None = None) -> None:
    """Ensure base EntityType nodes exist and optionally pre-seed a domain vocabulary.

    On every call (including resumes):
    1. Deduplicate any EntityType nodes that share an entityLabel.
    2. Enforce a unique constraint on entityLabel so future duplicates are blocked.
    3. MERGE the mandatory Document/Chunk nodes and HAS_CHUNK relationship.
    4. If a domain vocabulary is provided, MERGE each of its EntityTypes using
       ON CREATE SET so agent-improved descriptions are never overwritten.
    """
    # If the agent wrote a MERGE with `description` in the merge clause instead
    # of in SET, Neo4j creates a second node. Remove extras before enforcing the
    # constraint so constraint creation doesn't fail on existing violations.
    _run(
        """
        MATCH (e:EntityType)
        WITH e.entityLabel AS label, collect(e) AS nodes
        WHERE size(nodes) > 1
        FOREACH (dup IN nodes[1..] | DETACH DELETE dup)
        """
    )
    _run(
        """
        CREATE CONSTRAINT entitytype_entitylabel IF NOT EXISTS
          FOR (e:EntityType) REQUIRE e.entityLabel IS UNIQUE
        """
    )
    _run(
        """
        MERGE (doc:EntityType {entityLabel: 'Document'})
          SET doc.description = 'A source document ingested into the system. Each Document is split into one or more Chunks.'
        MERGE (chunk:EntityType {entityLabel: 'Chunk'})
          SET chunk.description = 'A contiguous span of text extracted from a Document. Chunks are the unit of agent processing and the source of every extracted instance entity.'
        MERGE (doc)-[r:RelType {relLabel: 'HAS_CHUNK'}]->(chunk)
          SET r.description = 'A Document HAS_CHUNK each of the Chunks it was split into.'
        """
    )
    if vocab:
        for et in vocab.entity_types:
            _run(
                """
                MERGE (e:EntityType {entityLabel: $label})
                ON CREATE SET e.description = $description
                """,
                {"label": et.label, "description": et.description},
            )


def _resolve_domain(domain_flag: str, chunks: list) -> DomainVocabulary | None:
    """Return the DomainVocabulary to use for this run.

    --domain none   → no vocabulary (open ontology)
    --domain auto   → call detect_domain() on the first chunk text
    --domain <slug> → use that vocabulary directly
    """
    if domain_flag == "none":
        return None
    if domain_flag != "auto":
        vocab = VOCABULARIES.get(domain_flag)
        if vocab is None:
            print(f"Unknown domain '{domain_flag}', falling back to auto-detection.")
        else:
            return vocab
    # auto-detect
    sample = chunks[0][1] if chunks else ""
    print("Auto-detecting document domain...", end=" ", flush=True)
    slug = detect_domain(sample)
    if slug:
        print(f"{slug}")
        return VOCABULARIES[slug]
    print("unknown — proceeding without a domain vocabulary.")
    return None


def _open_log(command: str, **params) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_file = LOG_DIR / f"{run_id}_metrics.jsonl"
    print(f"Logging metrics to {log_file}")
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "run_start",
        "run_id": run_id,
        "command": command,
        "neo4j_database": os.environ.get("NEO4J_DATABASE"),
        **params,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return log_file


def _ontology_structure_sig(snapshot: dict) -> tuple[frozenset, frozenset]:
    """Structural fingerprint of an ontology snapshot, ignoring description text.

    Two snapshots are structurally identical if they have the same set of
    EntityType labels and the same set of (from, relLabel, to) RelType edges,
    even if their `description` strings differ. Used to decide when to rebuild
    the ontology agent: a rebuild resets the prompt cache, so description-only
    refinements must not trigger one (see analysis/performance_analysis.md,
    Finding B).
    """
    labels = frozenset(
        e.get("entityLabel") for e in snapshot.get("entity_types", [])
    )
    edges = frozenset(
        (r.get("from_entityLabel"), r.get("relLabel"), r.get("to_entityLabel"))
        for r in snapshot.get("relationships", [])
    )
    return labels, edges


def run_ontology(
    path: str,
    limit: int | None = None,
    resume: bool = False,
    domain: str = "auto",
    verbose_summary: bool = False,
    model_id: str | None = None,
) -> Path:
    chunks = list(load_documents(path))
    if not chunks:
        print(f"No supported documents found at: {path}")
        return
    if limit is not None and limit > 0:
        chunks = chunks[:limit]

    total = len(chunks)
    docs = sorted({doc for doc, _, _ in chunks})

    vocab = _resolve_domain(domain, chunks)

    n_done = 0
    log_file: Path | None = None

    if resume:
        log_file, n_done, run_id = _find_resumable_run("ontology", str(path))
        if log_file is None:
            print("No resumable run found — starting fresh.")
        elif n_done >= total:
            print(f"All {total} chunks already completed in {log_file.name}. Nothing to do.")
            return log_file
        else:
            print(f"Resuming {run_id}: skipping {n_done}/{total} already-processed chunks.")
            _write_resume_event(log_file, n_done, total)

    effective_model = model_id or ONTOLOGY_MODEL_ID
    if log_file is None:
        log_file = _open_log(
            "ontology",
            input_path=str(path),
            model_id=effective_model,
            model_max_tokens=ONTOLOGY_MODEL_MAX_TOKENS,
            document_count=len(docs),
            chunk_count=total,
            documents=[Path(d).name for d in docs],
            chunk_limit=limit,
            domain=vocab.slug if vocab else None,
        )

    _bootstrap_ontology_base(vocab)

    print("\n=== Building ontology schema ===")
    if vocab:
        print(f"Domain vocabulary: {vocab.display_name} ({len(vocab.entity_types)} preferred types)")
    if n_done:
        print(f"(skipping first {n_done} chunks already processed)")
    mcp = build_ontology_mcp()

    # Deferred caching: only add cache_control to the snapshot block once the
    # ontology has stabilised (no_change_streak >= threshold). In the volatile
    # early phase every rebuild would pay a cache write that is never read;
    # once stable, cache reads cost 10× less than regular input.
    stability_threshold = int(
        os.environ.get("ONTOLOGY_CACHE_STABILITY_THRESHOLD", "3")
    )
    no_change_streak = 0
    use_cache = False

    def _build_agent(snap):
        return build_ontology_agent(
            mcp,
            snapshot=snap,
            vocab=vocab,
            verbose_summary=verbose_summary,
            model_id=effective_model,
            use_cache=use_cache,
        )

    current_snapshot = fetch_ontology_snapshot()
    current_sig = _ontology_structure_sig(current_snapshot)
    agent = _build_agent(current_snapshot)

    current_doc: str | None = None
    for i, (doc, chunk, idx) in enumerate(chunks[n_done:]):
        chunk_num = n_done + i + 1
        if doc != current_doc:
            current_doc = doc
            doc_chunk_count = sum(1 for c in chunks if c[0] == doc)
            print(f"\n--- {Path(doc).name} ({doc_chunk_count} chunks) ---")

        print(f"  chunk {chunk_num}/{total}...", end=" ", flush=True)
        _invoke_with_recovery(
            agent,
            (
                f"Document: {Path(doc).name}\n"
                f"Chunk {chunk_num} of {total}:\n"
                "---\n"
                f"{chunk}\n"
                "---\n"
                "Identify the entity types and relationship types present in this chunk "
                "and update the ontology schema accordingly. Do not create instance data. "
                "Use at most two write-cypher calls: one for EntityType nodes, one for RelType edges."
            ),
            log_file,
            "ontology_chunk",
            doc=Path(doc).name,
            chunk_num=chunk_num,
            total_chunks=total,
        )

        # Rebuild the agent only when the schema *structure* changes (a new
        # EntityType label or a new (from, relLabel, to) edge) — NOT when the
        # agent merely refines a description. A rebuild re-embeds the snapshot
        # and resets the prompt cache, so rebuilding on every description tweak
        # kept the cache from ever engaging (see analysis/performance_analysis.md,
        # Finding B). Description-only changes leave the structure stable, so the
        # streak keeps counting toward cache activation.
        new_snapshot = fetch_ontology_snapshot()
        new_sig = _ontology_structure_sig(new_snapshot)
        if new_sig != current_sig:
            current_sig = new_sig
            current_snapshot = new_snapshot
            no_change_streak = 0
            use_cache = False
            agent = _build_agent(current_snapshot)
            print("done (+rebuilt: structure changed)")
        else:
            no_change_streak += 1
            if no_change_streak == stability_threshold and not use_cache:
                use_cache = True
                # Refresh to the latest descriptions for the now-cached build;
                # the structure is unchanged so this is the last rebuild until
                # a genuine structural change occurs.
                current_snapshot = new_snapshot
                agent = _build_agent(current_snapshot)
                print(f"done (cache enabled, streak={no_change_streak})")
            else:
                print("done")

    print(f"\nDone. Metrics logged to {log_file}")
    return log_file


def run_enhance(model_id: str | None = None) -> None:
    effective_model = model_id or ENHANCER_MODEL_ID
    log_file = _open_log(
        "enhance",
        model_id=effective_model,
        model_max_tokens=ENHANCER_MODEL_MAX_TOKENS,
    )

    print("\n=== Enhancing ontology schema ===")
    mcp = build_enhancer_mcp()
    from ingest.agents.enhancer_agent import (
        _fetch_ontology_snapshot,
    )
    agent = build_enhancer_agent(
        mcp, snapshot=_fetch_ontology_snapshot(), model_id=effective_model
    )

    _invoke_with_recovery(
        agent,
        (
            "Review the ontology schema provided in your system prompt and make "
            "any improvements: resolve duplicates, simplify overly granular types, "
            "and introduce class hierarchies where appropriate. Use the EntityType "
            "and RelType `description` fields — not just labels — to judge "
            "equivalence and hierarchy candidates."
        ),
        log_file,
        "enhancer",
    )

    print(f"\nDone. Metrics logged to {log_file}")


def run_instance(
    path: str,
    limit: int | None = None,
    resume: bool = False,
    verbose_summary: bool = False,
    model_id: str | None = None,
) -> Path:
    chunks = list(load_documents(path))
    if not chunks:
        print(f"No supported documents found at: {path}")
        return
    if limit is not None and limit > 0:
        chunks = chunks[:limit]

    total = len(chunks)
    docs = sorted({doc for doc, _, _ in chunks})

    n_done = 0
    log_file: Path | None = None

    if resume:
        log_file, n_done, run_id = _find_resumable_run("instance", str(path))
        if log_file is None:
            print("No resumable run found — starting fresh.")
        elif n_done >= total:
            print(f"All {total} chunks already completed in {log_file.name}. Nothing to do.")
            return log_file
        else:
            print(f"Resuming {run_id}: skipping {n_done}/{total} already-processed chunks.")
            _write_resume_event(log_file, n_done, total)

    effective_model = model_id or INSTANCE_MODEL_ID
    if log_file is None:
        log_file = _open_log(
            "instance",
            input_path=str(path),
            model_id=effective_model,
            model_max_tokens=INSTANCE_MODEL_MAX_TOKENS,
            document_count=len(docs),
            chunk_count=total,
            documents=[Path(d).name for d in docs],
            chunk_limit=limit,
        )

    # Pre-create all Document and Chunk nodes (MERGE is idempotent — safe on resume).
    print("\n=== Pre-creating Document and Chunk nodes ===")
    doc_ids: dict[str, str] = {}
    chunk_ids: dict[tuple[str, int], str] = {}
    for doc, chunk, idx in chunks:
        if doc not in doc_ids:
            doc_ids[doc] = create_document_node(Path(doc).name, doc)
            print(f"  Document: {Path(doc).name}")
        chunk_ids[(doc, idx)] = create_chunk_node(doc_ids[doc], idx, chunk)

    print("\n=== Populating instance data ===")
    if n_done:
        print(f"(skipping first {n_done} chunks already processed)")
    mcp = build_instance_mcp()
    agent = build_instance_agent(
        mcp, verbose_summary=verbose_summary, model_id=effective_model
    )

    current_doc: str | None = None
    for i, (doc, chunk, idx) in enumerate(chunks[n_done:]):
        chunk_num = n_done + i + 1
        if doc != current_doc:
            current_doc = doc
            doc_chunk_count = sum(1 for c in chunks if c[0] == doc)
            print(f"\n--- {Path(doc).name} ({doc_chunk_count} chunks) ---")

        chunk_id = chunk_ids[(doc, idx)]
        print(f"  chunk {chunk_num}/{total}...", end=" ", flush=True)
        _invoke_with_recovery(
            agent,
            (
                f"Document: {Path(doc).name}\n"
                f"Chunk {chunk_num} of {total} | Chunk node elementId: {chunk_id}\n"
                "---\n"
                f"{chunk}\n"
                "---\n"
                "Using the ontology schema provided in your system prompt, extract "
                "instance entities and relationships from this chunk. Connect every entity "
                "to the Chunk node via FROM_CHUNK. "
                "Batch all of this chunk's MERGEs into a single write_neo4j_cypher call."
            ),
            log_file,
            "instance_chunk",
            doc=Path(doc).name,
            chunk_num=chunk_num,
            total_chunks=total,
            chunk_id=chunk_id,
        )
        print("done")

    print(f"\nDone. Metrics logged to {log_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Multi-agent ontology builder. Run each agent independently "
                    "via a subcommand. Typical sequence: ontology → enhance → instance.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_ontology = subparsers.add_parser(
        "ontology",
        help="Agent 1: build the ontology schema from documents.",
    )
    p_ontology.add_argument("path", help="File or folder of documents.")
    p_ontology.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N chunks (useful for dry-runs).",
    )
    p_ontology.add_argument(
        "--resume",
        action="store_true",
        help="Resume the most recent interrupted run for this path.",
    )
    p_ontology.add_argument(
        "--domain",
        default="auto",
        choices=[*VOCABULARIES.keys(), "auto", "none"],
        help=(
            "Domain vocabulary to seed the ontology. 'auto' detects from the "
            "first chunk; 'none' uses no preferred vocabulary. "
            f"Available: {', '.join(VOCABULARIES.keys())}."
        ),
    )
    p_ontology.add_argument(
        "--verbose-summary",
        action=argparse.BooleanOptionalAction,
        default=_VERBOSE_SUMMARY_DEFAULT,
        help=(
            "Have the agent summarise each chunk after writing — adds ~16 s "
            "per chunk. Default off. Env: ONTOLOGY_VERBOSE_SUMMARY=1."
        ),
    )
    p_ontology.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help=f"Anthropic model to use (default: {ONTOLOGY_MODEL_ID}).",
    )

    p_enhance = subparsers.add_parser(
        "enhance",
        help="Agent 2: review and improve the ontology schema in the existing graph.",
    )
    p_enhance.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help=f"Anthropic model to use (default: {ENHANCER_MODEL_ID}).",
    )

    p_instance = subparsers.add_parser(
        "instance",
        help="Agent 3: extract instance data from documents using the existing ontology.",
    )
    p_instance.add_argument("path", help="File or folder of documents.")
    p_instance.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N chunks (useful for dry-runs).",
    )
    p_instance.add_argument(
        "--resume",
        action="store_true",
        help="Resume the most recent interrupted run for this path.",
    )
    p_instance.add_argument(
        "--verbose-summary",
        action=argparse.BooleanOptionalAction,
        default=_VERBOSE_SUMMARY_DEFAULT,
        help=(
            "Have the agent summarise each chunk after writing — adds ~16 s "
            "per chunk. Default off. Env: ONTOLOGY_VERBOSE_SUMMARY=1."
        ),
    )
    p_instance.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help=f"Anthropic model to use (default: {INSTANCE_MODEL_ID}).",
    )

    p_cost = subparsers.add_parser(
        "cost",
        help="Parse a metrics JSONL log and print a cost breakdown.",
    )
    p_cost.add_argument("log_file", help="Path to a *_metrics.jsonl log file.")

    args = parser.parse_args()

    if args.command == "ontology":
        run_ontology(
            args.path,
            limit=args.limit,
            resume=args.resume,
            domain=args.domain,
            verbose_summary=args.verbose_summary,
            model_id=args.model,
        )
    elif args.command == "enhance":
        run_enhance(model_id=args.model)
    elif args.command == "instance":
        run_instance(
            args.path,
            limit=args.limit,
            resume=args.resume,
            verbose_summary=args.verbose_summary,
            model_id=args.model,
        )
    elif args.command == "cost":
        run_cost(args.log_file)
