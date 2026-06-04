# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Setup

Python 3.14 is required (Homebrew: `/opt/homebrew/opt/python@3.14/bin/python3.14`). Always create the venv with the explicit path to avoid version mismatches:

```bash
/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install mcp-neo4j-cypher --no-deps   # avoid mcp version downgrade
pip install "mcp>=1.23.0,<2.0.0" --upgrade
```

Credentials go in `.env` at the repo root (see `.env.example`). Both `ingest/main.py` and `query/main.py` use bare `load_dotenv()` which finds it automatically when run from the repo root.

## Running

```bash
# Ingest pipeline — run in sequence
python ingest/main.py ontology path/to/document.pdf
python ingest/main.py enhance
python ingest/main.py instance path/to/document.pdf
python ingest/main.py instance path/to/document.pdf --concurrency 8   # parallel chunks (default 5)

# Add documents to an existing graph (skip ontology/enhance)
python ingest/main.py instance path/to/extra_document.html

# Query
python query/main.py "your question here"   # single question
python query/main.py                         # interactive REPL
python query/main.py cost query/logs/<id>_qa.jsonl
```

Supported input formats: `.txt`, `.md`, `.rst`, `.csv`, `.json`, `.pdf`, `.html`, `.htm`

## Architecture

### Package layout

```
ingest/          Three-agent ingest pipeline
  agents/
    ontology_agent.py    Agent 1 — build schema from documents
    enhancer_agent.py    Agent 2 — deduplicate/refine schema
    instance_agent.py    Agent 3 — extract instance data
  domain_vocab.py        Domain vocabularies (9 built-in domains)
  tools.py               get_ontology_schema, create_document_node, create_chunk_node
  main.py                CLI entry point (subcommands: ontology, enhance, instance, cost)

query/           Natural-language QA over the graph
  agent.py               Strands agent with cached schema system prompt
  tools.py               run_read_cypher
  main.py                CLI entry point (single question, REPL, cost)

shared/          Shared utilities
  document.py            Document loading and chunking
  neo4j_tools.py         Neo4j driver wrapper + @tool functions
  strands_anthropic.py   CacheAwareAnthropicModel (fixes cache token counts)
```

### Two-layer graph model

Neo4j holds two structurally separate layers:

**Ontology layer** — `EntityType` nodes and `RelType` relationships:
```
(EntityType {entityLabel: "Obligation"})-[:RelType {relLabel: "APPLIES_TO"}]->(EntityType {entityLabel: "Role"})
```

**Instance layer** — domain-labelled nodes and typed edges:
```
(Obligation {name: "..."})-[:APPLIES_TO {detail: "..."}]->(Role {name: "Licensee"})
(Obligation {name: "..."})-[:FROM_CHUNK]->(Chunk {chunk_index: 5})
(Document {name: "fltca_2021.html"})-[:HAS_CHUNK]->(Chunk {chunk_index: 5})
```

The layers never reference each other directly. Ontology nodes carry the `EntityType` label; instance nodes carry the domain label directly.

### Ingest pipeline detail

**Agent 1 (ontology):** Fresh Strands agent per chunk. The current ontology snapshot is embedded in the system prompt with `cache_control` — after the schema stabilises (default: 3 consecutive chunks with no change), the snapshot is cached and subsequent chunks pay ~10% of normal input cost. Uses `mcp-neo4j-cypher` via stdio for writes. At most 2 `write-cypher` calls per chunk: one for `EntityType` nodes, one for `RelType` edges.

**Agent 2 (enhancer):** Single-shot run over the full schema. Deduplicates equivalent EntityTypes, consolidates overly granular types, adds `SUBCLASS_OF` hierarchies, generalises jurisdiction-specific labels. Uses `SlidingWindowConversationManager(window_size=8)`.

**Agent 3 (instance):** Chunks are processed **concurrently** (a thread pool, `--concurrency`/`$INSTANCE_CONCURRENCY`, default 5) since they are independent and every write is an idempotent `MERGE` on `name`. Each worker builds its own agent and resets its conversation per chunk; all workers share one identical cached schema prefix (written once, read by the rest) and the process-wide Neo4j driver. Unlike the ontology/enhancer agents, the instance agent writes through a **direct-driver `write_cypher` tool** (not the MCP server) so that deadlocks/transient lock errors from concurrent writes are retried with exponential backoff inside `shared/neo4j_tools._run_write`. Before the run, `_ensure_instance_name_indexes()` creates a uniqueness constraint on `name` for every instance label so each `MERGE` is an index seek, not a label scan. At most 2 `write_cypher` calls per chunk. `--resume` is set-based (skips the set of completed `chunk_num`s) because parallel workers finish out of order.

All agents use `claude-sonnet-4-6`. The ontology/enhancer/instance-worker agents use `SlidingWindowConversationManager(window_size=6, should_truncate_results=True)` (enhancer uses window_size=8); the instance agent additionally resets its conversation each chunk.

### Query agent detail

Schema fetched once at `build_agent()` time and embedded as a cached system prompt prefix. Agent follows a fixed 5-step workflow: read schema → ground entity mentions (`find_entities_by_name`) → compose Cypher → execute (`run_read_cypher`) → summarise. `get_ontology_schema` is also available as a tool to refresh the schema mid-session without restarting.

### Document chunking (`shared/document.py`)

- **Plain text / Markdown / RST / CSV / JSON:** Split on blank lines (`\n{2,}`). Sections < 200 chars are merged into their neighbour.
- **PDF:** `pdfplumber` extracts text per page, then blank-line split.
- **HTML / iXBRL:** BeautifulSoup, split on `<hr>` tags (SEC filing pattern). iXBRL metadata blocks are decomposed; inline value wrappers are unwrapped (text preserved). Tables rendered as pipe-delimited rows. Sections < 200 chars merged.

No fixed token/character chunk size — chunks are semantic sections as the document naturally divides them.

### neo4j-mcp known issue

`mcp-neo4j-cypher`'s `get-schema` tool f-string interpolates `config_sample_size` directly into Cypher. When `NEO4J_SCHEMA_SAMPLE_SIZE` is unset, `config_sample_size` is `None`, producing invalid Cypher. All `build_mcp_client()` functions explicitly pass `NEO4J_SCHEMA_SAMPLE_SIZE` in the subprocess env (defaulting to `"1000"`) to guarantee a valid integer is always present.

### MCPClient lifecycle

Applies to the ontology and enhancer agents, which still use `mcp-neo4j-cypher`. (The instance agent no longer uses MCP — it writes via the direct-driver `write_cypher` tool so it can run in concurrent threads and retry deadlocks.) The `MCPClient` must **not** be started via `with` before passing to the Agent — the Agent manages its lifecycle. Pass the `MCPClient` object directly in `tools=[..., mcp_client]`.

### Prompt caching (strands gotcha)

`Agent(system_prompt=[SystemContentBlock(...)])` is silently a no-op for caching in Strands. Route cache-controlled system blocks through `AnthropicModel(params={"system": [...]})` instead — `params` is spread after the string `system` field in `format_request` and overrides it. See `shared/strands_anthropic.py` for the cache token count fix.

## Domain vocabularies

The ontology agent can be seeded with preferred EntityTypes via `--domain`:

| Slug | Domain |
|---|---|
| `legal` | Legislation, regulations, contracts, compliance frameworks |
| `business` | Annual reports, earnings calls, strategy documents |
| `medical` | Clinical guidelines, trial reports, drug labels |
| `scientific` | Academic papers, systematic reviews |
| `financial` | Prospectuses, fund sheets, investment research |
| `fraud` | AML, SAR filings, fraud investigation reports |
| `pole` | Intelligence / law enforcement case files |
| `patent` | Patent applications, IP landscape analyses |
| `supply_chain` | Shipping manifests, supplier audits |

`--domain auto` (default) detects the domain from the first chunk using `claude-haiku-4-5-20251001`. `--domain none` disables vocabulary seeding.

## Ontology conventions

The agent system prompts enforce these — maintain them in any new tools or prompts:
- Node labels: `PascalCase`
- Relationship types: `UPPER_SNAKE_CASE`
- Property keys: `lowercase_snake_case`
- Always `MERGE` nodes on `name`, never `CREATE`, to avoid duplicates
- `MERGE` on `entityLabel` alone for `EntityType` nodes — never include `description` in the merge clause

## Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API access |
| `NEO4J_URI` | Neo4j Aura connection URI |
| `NEO4J_USERNAME` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |
| `NEO4J_DATABASE` | Named database (optional, defaults to server default) |
| `NEO4J_SCHEMA_SAMPLE_SIZE` | Sample size for mcp-neo4j-cypher get-schema (default: `"1000"`) |
| `ONTOLOGY_VERBOSE_SUMMARY` | Set to `1` to have agents summarise each chunk (adds ~16s/chunk) |
| `ONTOLOGY_CACHE_STABILITY_THRESHOLD` | Consecutive no-change chunks before enabling prompt cache (default: `3`) |

## Analysis documents

`analysis/` contains evaluation and reference documents from the FLTCA 2021 pilot:
- `ontology_reference.md` / `ontology_reference_v2.md` — ontology snapshots after schema runs
- `fltca_qa_assessment.md` — round 1 QA evaluation (Act only)
- `fltca_qa_assessment_v2.md` — round 2 QA evaluation (Act + O. Reg. 246/22)
- `test_plan.md` — test questions and evaluation methodology
