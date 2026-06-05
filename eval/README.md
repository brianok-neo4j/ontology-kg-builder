# eval/ — A/B model harness

Compares Anthropic models for the **instance** and **query** phases of the
pipeline, to decide where a cheaper/faster model (e.g. Haiku) holds quality
versus the default (Sonnet). See `analysis/performance_analysis.md`, Finding L.

A/B is **scoped to the instance and query phases only**. The ontology is built
once with a single model, so every A/B run starts from the same ontology (a
common base); this harness never varies the ontology model.

## Layout

| File | Purpose |
|---|---|
| `models.py` | Candidate model ids + `cost_of()` (pricing reused from `ingest.main`) |
| `questions.py` | The 15 FLTCA eval questions (swap for other corpora) |
| `query_ab.py` | Query-phase A/B (read-only, safe) |
| `instance_ab.py` | Instance-phase A/B (destructive — wipes instance layer between runs) |
| `judge.py` | LLM judge — grades answers Excellent/Good/Partial/Weak |
| `main.py` | CLI |

Reports (markdown + raw JSON) are written to `eval/results/` (git-ignored).

## Prerequisites

A populated graph with an ontology already built (`python ingest/main.py
ontology … && enhance`). For query-ab you also need instance data; for
instance-ab the harness creates the instance data itself per model.

## Query A/B (read-only, safe)

Both models query the same graph — no isolation needed.

```bash
python -m eval.main query-ab
python -m eval.main query-ab --models claude-sonnet-4-6 claude-haiku-4-5-20251001
python -m eval.main query-ab --limit 5      # first 5 questions only
```

Captures per-question **cost, latency, cycles, and the answer text**. Cost and
latency/cycles are measured directly; cycle count is a useful quality proxy
(a model that needs many more cycles is struggling to ground entities or
compose Cypher). Answers are written side-by-side in the report.

#### Metrics logs (`eval/logs/`)

Alongside the markdown/JSON report, each run drops `<run_id>_metrics.jsonl`
files in `eval/logs/` in the **same format the ingest/query tools emit** — a
`run_start` header plus a per-call `usage`/`cycles`/`duration_s`/`cost` record.
One file is written **per model** (`…_query_<model>_metrics.jsonl`, and
`…_judge_<model>_metrics.jsonl` when `--judge` is used), so each log is
single-model and the shared cost tooling — which prices a whole log by its
`run_start` model — stays exact even for an A/B run, and judging cost is priced
at the judge model's own rate. Analyze them like any ingest log:

```bash
python scripts/cost_watch.py eval/logs/<run_id>_query_<model>_metrics.jsonl
python -m ingest.main cost   eval/logs/<run_id>_judge_<model>_metrics.jsonl
```

### LLM judge (`--judge`)

Add `--judge` to grade each answer **Excellent / Good / Partial / Weak** (the
pilot's rubric). The judge is a separate, stronger model (`--judge-model`,
default a strong independent model) so the grade isn't biased toward whichever
model produced the answer. It establishes the correct answer using three
sources before grading:

1. **the raw source document** — pass `--source <path>` (file/folder used to
   build the graph); the judge gets a `search_source_document` tool over it and
   treats it as ground truth;
2. **the web** — the community `http_request` tool (GET only), e.g. to consult
   official legislation sites;
3. **its own internal knowledge**.

```bash
python -m eval.main query-ab --judge --source "path/to/FLTCA.pdf"
python -m eval.main query-ab --judge --source docs/ --judge-model claude-opus-4-8 --limit 5
```

The report then includes a per-model grade distribution and each answer's grade
+ the judge's rationale. (Judging adds API cost — a strong judge plus web
fetches across all answers — so it's opt-in.)

## Instance A/B (destructive)

Instance extraction `MERGE`s entities by `name`, so two models writing into one
graph would conflate. The harness therefore **wipes the instance layer**
(everything except the ontology `EntityType` nodes and the `Document`/`Chunk`
provenance nodes) between model runs, so each model is measured on a clean graph
over the common ontology.

```bash
# Dry plan — prints what it would delete and the target DB, does nothing:
python -m eval.main instance-ab path/to/document.pdf

# Actually run (gated): first 25 chunks, Sonnet vs Haiku:
python -m eval.main instance-ab path/to/document.pdf --confirm-wipe --limit 25
```

⚠️ `--confirm-wipe` is required because this **deletes instance data** in the
target Neo4j database. Point `NEO4J_DATABASE` at a scratch graph (or one you're
happy to rebuild). The ontology and Document/Chunk nodes are preserved. After
the run the graph holds the **last** model's extraction — re-run the instance
stage with your chosen model to repopulate fully.

Reports **cost and latency** (measured from each run's metrics log) plus
**extraction-quality proxies**: entity/relationship counts, per-label
breakdown, and chunk coverage. It also dumps every extracted `(label, name)` and
relationship per model to JSON so you can diff what the cheaper model missed —
the known instance-stage failure mode is *under*-extraction, so compare coverage
closely rather than assuming parity.
