# ontology-based-kg

A three-agent pipeline that builds a knowledge graph from documents using an
LLM-constructed ontology, then answers natural-language questions over it via
graph-aware Cypher retrieval.

## Architecture

```
ingest/          Three-agent pipeline: ontology schema → enhance → instance data
query/           Cypher QA agent: natural-language questions over the graph
shared/          Document loading, Neo4j driver wrapper, Anthropic model adapter
eval/            A/B model harness for the instance and query phases (+ LLM judge)
analysis/        Assessment reports and ontology reference documents
```

### Ingest pipeline (`ingest/`)

Three subcommands run in sequence, sharing Neo4j as their only state:

**Agent 1 — Ontology** (`ingest/agents/ontology_agent.py`)
Reads document chunks and writes a schema-level ontology to Neo4j using
`EntityType` nodes and `RelType` edges. Entity types must be **categories**
(`Plan`, `Obligation`, `Notice`) — never instance-level labels
(`EvacuationPlan`); the specific names belong in the instance layer. The current
schema is embedded in the system prompt with two cache breakpoints — one on the
static prompt prefix (a cache hit every chunk) and one on the schema snapshot
(engages once the structure stabilises); the agent is rebuilt only on a
*structural* change (new label/edge, not a reworded description) and its
conversation is reset each chunk. To keep the per-chunk prompt small the snapshot
embeds each type's `short_description` (full text on demand via the
`describe_ontology` tool; toggle with `ONTOLOGY_COMPACT_SNAPSHOT`). A safety cap
(`--max-entity-types`, default 150) aborts the run if the schema balloons, since
the embedded schema makes input cost grow quadratically with an over-fragmented
ontology.

> **Build the ontology from your most abstract document(s)** (e.g. high level documentation describing what things exist and how they relate), then
> run the instance stage over the full corpus (e.g., laws or metadata plus procedures and specifics implementing those laws).
> Deriving the ontology from highly detailed regulations causes the schema to
> over-fragment.

**Agent 2 — Enhancer** (`ingest/agents/enhancer_agent.py`)
Runs once after Agent 1. Reviews the full schema and makes targeted quality
improvements: deduplicates equivalent EntityTypes, consolidates overly granular
types, introduces `SUBCLASS_OF` hierarchies, generalises jurisdiction-specific labels.

**Agent 3 — Instance** (`ingest/agents/instance_agent.py`)
Uses the enhanced ontology as a strict schema. Extracts instance nodes and
relationships from each chunk, connecting every entity to its source `Chunk`
via `FROM_CHUNK`. Only labels and relationship types defined in the ontology
are used. Chunks are processed concurrently (`--concurrency`, default 5) since
they are independent and every write is an idempotent `MERGE`; writes use a
direct Neo4j driver that retries deadlocks/transient errors with exponential
backoff, and a uniqueness constraint on each label's `name` is created up front
so each `MERGE` is an index seek rather than a full label scan.

### Query agent (`query/`)

A Strands agent that answers natural-language questions by following a fixed
five-step workflow: read ontology schema → ground entity mentions → compose
Cypher → execute → summarise. The schema is embedded in a cached system
prompt at agent build time; `get_ontology_schema` is available as a tool
to refresh it mid-session without restarting.

## Setup

Requires Python ≥ 3.11. Recommended: Python 3.14 via Homebrew.

```bash
# Create and activate venv
/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
source .venv/bin/activate

# Install
pip install -e .
pip install mcp-neo4j-cypher --no-deps   # avoid mcp version downgrade
pip install "mcp>=1.23.0,<2.0.0" --upgrade

# Configure credentials
cp .env.example .env
# edit .env with your Anthropic API key and Neo4j Aura credentials
```

## Usage

### Ingest a document

```bash
# Build the ontology from the abstract document only (keeps the schema small),
# enhance it, then extract instances over the FULL corpus.
python ingest/main.py ontology path/to/Act.pdf
python ingest/main.py enhance
python ingest/main.py instance path/to/corpus_dir/   # Act + regulations

# Explicitly specify a domain vocabulary
python ingest/main.py ontology path/to/Act.pdf --domain legal

# Lower the schema-size safety cap (default 150) for tighter cost control
python ingest/main.py ontology path/to/Act.pdf --max-entity-types 80

# Ingest additional documents into an existing graph (skips ontology/enhance)
python ingest/main.py instance path/to/regulation.html

# Process instance chunks in parallel (default 5; tune to your rate limits)
python ingest/main.py instance path/to/corpus_dir/ --concurrency 8

# Resume an interrupted run (instance resume is set-based, so safe after a
# parallel run where chunks completed out of order)
python ingest/main.py ontology path/to/document.pdf --resume
python ingest/main.py instance path/to/document.pdf --resume

# Dry-run: first 5 chunks only
python ingest/main.py ontology path/to/document.pdf --limit 5
```

### Monitor an ingest run (cost & progress)

`scripts/cost_watch.py` reads a run's metrics JSONL log and reports running
cost, cache-hit rate, throughput, elapsed time, and — given the chunk count — an
ETA and projected final cost. It is safe to run against a log that is still
being written (it skips a partially-flushed trailing line), and works for
**any** ingest stage — `ontology`, `enhance`, or `instance` — since they share
the same log format. Defaults to the newest log in `ingest/logs/`.

```bash
# Snapshot of the newest run, projected to a known chunk count
python scripts/cost_watch.py --total-chunks 451

# Live dashboard, refreshing every 10s
python scripts/cost_watch.py --watch --total-chunks 451

# A specific stage's log (e.g. the ontology run)
python scripts/cost_watch.py ingest/logs/<run_id>_metrics.jsonl
```

(`python ingest/main.py cost <log>` gives the same cost totals as a one-shot,
without the live progress/ETA view.)

### Ask questions

```bash
# Single question
python query/main.py "What sanctions can result from a failed inspection?"

# Interactive REPL
python query/main.py

# Cost breakdown for a session log
python query/main.py cost query/logs/<run_id>_qa.jsonl
```

## Domain vocabularies

The ontology agent can be seeded with a preferred set of EntityTypes from a
built-in domain vocabulary. The agent defaults to these types and only creates
new ones as `SUBCLASS_OF` a preferred type.

| Slug | Domain | Suited for |
|---|---|---|
| `legal` | Legal / Regulatory | Legislation, regulations, contracts, compliance frameworks |
| `business` | Business / Corporate | Annual reports, earnings calls, strategy documents |
| `medical` | Medical / Clinical | Clinical guidelines, trial reports, drug labels |
| `scientific` | Scientific Research | Academic papers, systematic reviews |
| `financial` | Financial / Investment | Prospectuses, fund sheets, investment research |
| `fraud` | Fraud / Financial Crime | AML, SAR filings, fraud investigation reports |
| `pole` | Intelligence / Law Enforcement | Case files, crime analysis, surveillance |
| `patent` | Patents / IP | Patent applications, IP landscape analyses |
| `supply_chain` | Supply Chain / Logistics | Shipping manifests, supplier audits |

`--domain auto` (default) detects the domain from the first chunk using Claude Haiku.
`--domain none` disables vocabulary seeding for a fully open ontology.

## Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API access |
| `NEO4J_URI` | Neo4j Aura connection URI |
| `NEO4J_USERNAME` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |
| `NEO4J_DATABASE` | Named database (optional, defaults to server default) |
| `NEO4J_SCHEMA_SAMPLE_SIZE` | Schema sample size for mcp-neo4j-cypher (default: 1000) |
| `ONTOLOGY_VERBOSE_SUMMARY` | Set to `1` to have agents summarise each chunk (slower) |
| `ONTOLOGY_CACHE_STABILITY_THRESHOLD` | Chunks with no schema change before caching the snapshot block (default: 3) |
| `ONTOLOGY_MAX_ENTITY_TYPES` | Abort the ontology run if the schema exceeds this many EntityTypes (default: 150; flag: `--max-entity-types`). Guards against quadratic cost blow-up from an over-fragmented schema; raise and `--resume` if the growth is expected |
| `ONTOLOGY_COMPACT_SNAPSHOT` | Embed the compact `short_description` (`1`, default) vs verbose `full_description` (`0`) in per-chunk snapshots. Both are always stored; full text is always available via `describe_ontology` |
| `INSTANCE_CONCURRENCY` | Default parallel workers for the instance stage (default: 5; overridden by `--concurrency`) |
| `ANTHROPIC_CACHE_TTL` | Prompt-cache TTL for the schema prefix: `5m` or `1h`. Ingest defaults to `1h`, the query agent to `5m` |
| `INGEST_LOG_TRACES` | Log per-call message traces in ingest metrics logs (default on; `0` to disable; flag: `--no-trace-logs`) |

## Graph model

```
Ontology layer (EntityType nodes + RelType edges):
  (EntityType {entityLabel: "Obligation"})-[:RelType {relLabel: "APPLIES_TO"}]->
      (EntityType {entityLabel: "Role"})

Instance layer (labelled nodes + typed edges):
  (Obligation {name: "Licensee must ensure 24hr RN coverage"})-[:APPLIES_TO]->
      (Role {name: "Licensee"})
  (Obligation {name: "..."})-[:FROM_CHUNK]->(Chunk {chunk_index: 5, ...})
  (Document {name: "fltca_2021.html"})-[:HAS_CHUNK]->(Chunk {chunk_index: 5})
```

The ontology and instance layers are structurally separate — ontology nodes
carry the `EntityType` label, instance nodes carry the domain label directly
(e.g. `Obligation`, `Role`).

Each `EntityType` node and `RelType` edge carries two description fields: a
`short_description` (a ≤12-word phrase, embedded in per-chunk prompts) and a
`full_description` (the complete definition, fetched on demand via
`describe_ontology`). Instance relationships may carry a `detail` property
capturing what specifically is required/governed/etc.
