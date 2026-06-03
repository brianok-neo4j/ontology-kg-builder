# ontology-based-kg

A three-agent pipeline that builds a knowledge graph from documents using an
LLM-constructed ontology, then answers natural-language questions over it via
graph-aware Cypher retrieval.

## Architecture

```
ingest/          Three-agent pipeline: ontology schema → enhance → instance data
query/           Cypher QA agent: natural-language questions over the graph
shared/          Document loading, Neo4j driver wrapper, Anthropic model adapter
analysis/        Assessment reports and ontology reference documents
```

### Ingest pipeline (`ingest/`)

Three subcommands run in sequence, sharing Neo4j as their only state:

**Agent 1 — Ontology** (`ingest/agents/ontology_agent.py`)
Reads document chunks and writes a schema-level ontology to Neo4j using
`EntityType` nodes and `RelType` edges. Fresh agent per chunk with the current
schema embedded in a cached system prompt.

**Agent 2 — Enhancer** (`ingest/agents/enhancer_agent.py`)
Runs once after Agent 1. Reviews the full schema and makes targeted quality
improvements: deduplicates equivalent EntityTypes, consolidates overly granular
types, introduces `SUBCLASS_OF` hierarchies, generalises jurisdiction-specific labels.

**Agent 3 — Instance** (`ingest/agents/instance_agent.py`)
Uses the enhanced ontology as a strict schema. Extracts instance nodes and
relationships from each chunk, connecting every entity to its source `Chunk`
via `FROM_CHUNK`. Only labels and relationship types defined in the ontology
are used.

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
# Auto-detect document domain and run all three phases
python ingest/main.py ontology path/to/document.pdf
python ingest/main.py enhance
python ingest/main.py instance path/to/document.pdf

# Explicitly specify a domain vocabulary
python ingest/main.py ontology path/to/document.pdf --domain legal

# Ingest additional documents into an existing graph (skips ontology/enhance)
python ingest/main.py instance path/to/regulation.html

# Resume an interrupted run
python ingest/main.py ontology path/to/document.pdf --resume
python ingest/main.py instance path/to/document.pdf --resume

# Dry-run: first 5 chunks only
python ingest/main.py ontology path/to/document.pdf --limit 5
```

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
| `ONTOLOGY_CACHE_STABILITY_THRESHOLD` | Chunks with no schema change before enabling prompt cache (default: 3) |

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
