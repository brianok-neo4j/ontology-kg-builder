# Ontology-Based Knowledge Graph Pipeline — Architecture

---

## Overview

This system converts unstructured documents (legislation, regulations, filings,
reports) into a queryable Neo4j knowledge graph, then answers natural-language
questions over that graph.

The core design is **schema-first, two-layer construction**: first derive an
abstract *ontology* (the types of things and relationships in the domain) from
the most abstract document(s), then populate an *instance* layer that conforms
to that ontology. A schema-first graph is semantically coherent across many
documents and supports Cypher traversals that mirror the real structure of the
domain — not keyword or embedding similarity.

Piloted against the *Fixing Long-Term Care Act, 2021* (FLTCA) + *O. Reg. 246/22*
(~451 chunks). Retrieval quality is measured by an automated A/B + LLM-judge
harness (`eval/`); the full methodology, experiments, and results are in
[`retrieval_quality_investigation.md`](retrieval_quality_investigation.md).

---

## High-Level Architecture

Three subsystems, sharing Neo4j as their only state:

```
Documents (PDF, HTML, plain text, …)
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                       INGEST PIPELINE                          │
│                                                                │
│  Agent 1 — Ontology Builder   (build ontology from Act only)   │
│    fresh agent per chunk; MERGEs EntityType nodes + RelType    │
│    edges; generalization rules + size cap keep it compact      │
│                            │                                   │
│                            ▼                                   │
│  Agent 2 — Enhancer   (single-shot over the whole schema)      │
│    dedupes, consolidates, adds SUBCLASS_OF, removes ghosts     │
│                            │                                   │
│                            ▼                                   │
│  Agent 3 — Instance Builder   (over the FULL corpus)           │
│    concurrent workers; direct-driver writes w/ deadlock retry; │
│    domain-labelled instance nodes + typed edges + FROM_CHUNK   │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                      NEO4J AURA DATABASE                       │
│  Ontology layer (EntityType / RelType)                         │
│  Instance layer (Obligation, Role, Process, Concept, … nodes)  │
│  Provenance (FROM_CHUNK → Chunk ← HAS_CHUNK ← Document)        │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                          QUERY AGENT                           │
│   read schema → ground entity mentions → compose Cypher →      │
│   execute (read-only) → summarize in natural language          │
└──────────────────────────────────────────────────────────────┘
```

> **Build the ontology from the abstract document(s) only** (e.g. an Act), then
> run the instance stage over the full corpus (Act + detailed regulations).
> Deriving the ontology from highly detailed regulations over-fragments the
> schema (specific provisions become *types* instead of *instances*), which —
> because the schema is embedded in every chunk's prompt — balloons cost
> quadratically.

---

## The Two-Layer Graph Model

Two structurally separate layers that never reference each other directly.

### Ontology layer

The *schema* — abstract types and relationships:

- **`EntityType` nodes** — `entityLabel` (PascalCase, e.g. `Obligation`, `Role`,
  `Process`, `Concept`) plus two description fields (see *Compact snapshot*
  below): `short_description` and `full_description`.
- **`RelType` edges** between two `EntityType` nodes — `relLabel`
  (UPPER_SNAKE_CASE, e.g. `APPLIES_TO`, `CONDITIONED_ON`, `TRIGGERS`) plus the
  same two description fields. The description defines the edge semantics and
  what the instance-layer `detail` property will hold.
- **`SUBCLASS_OF` edges** between `EntityType` nodes form an optional class
  hierarchy (added by the Enhancer).

```
(EntityType {entityLabel:"Obligation"}) -[:RelType {relLabel:"APPLIES_TO"}]-> (EntityType {entityLabel:"Role"})
```

### Instance layer

The actual extracted entities. Instance nodes carry the **domain label
directly** (`:Obligation`, `:Role`, `:Concept`) — not the `EntityType` wrapper —
and always have a `name` used for `MERGE` deduplication. Edges reuse the
ontology `relLabel`s, with an optional `detail` property holding the specific
content. Every instance node links to its source `Chunk` via `FROM_CHUNK`.

```
(:Obligation {name:"Licensee must report critical incidents"})
   -[:APPLIES_TO {detail:"duty to report critical incidents"}]-> (:Role {name:"Licensee"})
(:Obligation {name:"…"})-[:FROM_CHUNK]->(:Chunk {chunk_index:42})
(:Document {name:"fltca_2021.html"})-[:HAS_CHUNK]->(:Chunk {chunk_index:42})
```

`Document` and `Chunk` are provenance-only and excluded from semantic queries.

---

## Document Chunking (`shared/document.py`)

No fixed token size — chunks are the document's own semantic sections.

| Format | Method |
|---|---|
| Plain text / Markdown / RST / CSV / JSON | Split on blank lines (`\n{2,}`); sections < 200 chars merged into a neighbour. |
| PDF | `pdfplumber` extracts text per page, then blank-line split. |
| HTML / iXBRL | BeautifulSoup splits on `<hr>` (SEC filing pattern); iXBRL metadata decomposed, inline value wrappers unwrapped, tables rendered pipe-delimited; < 200-char sections merged. |

Supported: `.txt`, `.md`, `.rst`, `.csv`, `.json`, `.pdf`, `.html`, `.htm`.

---

## Ingest Pipeline — Stage by Stage

All ingest agents use `claude-sonnet-4-6` via the Strands framework. Each stage
writes a per-call metrics JSONL log (`ingest/logs/`); cost is recovered with
`python ingest/main.py cost <log>` or `scripts/cost_watch.py`.

### Stage 1 — Ontology Builder (`ingest/agents/ontology_agent.py`)

Reads each chunk and incrementally builds the schema (`EntityType` + `RelType`),
no instance data. A **fresh agent per chunk** embeds the current ontology
snapshot in its system prompt. Per chunk, at most two `write-cypher` calls (one
for `EntityType` nodes, one for `RelType` edges).

Key design points:

- **Generalization discipline.** An `EntityType` must be a *category with many
  instances*, never a specific thing. The prompt forbids the
  `<Specific><Category>` pattern (e.g. `EvacuationPlan` → use `Plan`; the
  specific name becomes an instance later) and rejects jurisdiction-embedded
  labels (`OntarioLabourRelationsBoard` → `RegulatoryTribunal`). This keeps a
  whole-statute ontology to ~20–50 types rather than hundreds. *(These rules
  materially affect retrieval quality and ontology stability — see the
  investigation doc.)*
- **Size cap.** `--max-entity-types` (default 150, `ONTOLOGY_MAX_ENTITY_TYPES`)
  aborts the run if the schema balloons, guarding against quadratic cost from an
  over-fragmented schema embedded in every prompt.
- **Domain-vocabulary seeding.** `--domain` seeds preferred `EntityType`s from a
  built-in vocabulary (9 domains; `auto` detects from the first chunk with
  `claude-haiku-4-5-20251001`). The `legal` vocabulary includes a **`Concept`**
  catch-all (defined terms, doctrines, principles) — a coherent home for
  abstract notions that don't fit other types.
- **Ghost-node prevention.** The prompt requires every variable in an edge
  `MERGE` to be bound in the same query (an unbound variable silently creates an
  unlabelled "ghost" node).
- **Writes** go through `mcp-neo4j-cypher` (stdio MCP server).
- **Caching** — see *Prompt caching*: a static prefix breakpoint (always cached)
  plus a snapshot breakpoint that engages once the schema is stable for N chunks.

### Stage 2 — Enhancer (`ingest/agents/enhancer_agent.py`)

A single-shot agent over the full schema (uses `SlidingWindowConversationManager(window_size=8)`).
Order of operations:

1. **Remove ghost nodes** first (defensive cleanup of any unlabelled nodes
   dangling off `RelType` edges).
2. **Deduplicate** equivalent `EntityType`s (judged from descriptions, not just
   labels).
3. **Consolidate** over-granular types.
4. **Add `SUBCLASS_OF`** hierarchies where a group shares a parent concept.
5. **Generalize** any remaining jurisdiction-specific labels.

Reads the `full_description` fields (it needs the complete definitions to judge
equivalence). Writes via `mcp-neo4j-cypher`.

### Stage 3 — Instance Builder (`ingest/agents/instance_agent.py`)

Re-reads the full corpus and populates the instance layer, constrained to the
finalized ontology. This stage differs most from the others:

- **Concurrent.** Chunks are independent and every write is an idempotent
  `MERGE` on `name`, so chunks run in a thread pool (`--concurrency` /
  `$INSTANCE_CONCURRENCY`, default 5). Each worker builds its own agent and
  **resets its conversation per chunk**; all workers share one identical cached
  schema prefix and the process-wide Neo4j driver.
- **Direct-driver writes (not MCP).** Writes go through a direct-driver
  `write_cypher` tool so concurrent-write **deadlocks / transient lock errors
  are retried with exponential backoff** (`shared/neo4j_tools._run_write`). Tools
  are `[write_cypher, read_cypher, describe_ontology]` — the high-level
  create-node helpers are deliberately omitted so the agent can't fall back to
  one-MERGE-per-call.
- **Name indexes.** Before the run, `_ensure_instance_name_indexes()` creates a
  uniqueness constraint on `name` for every instance label, so each `MERGE` is an
  index seek, not a label scan.
- **Two-call write pattern.** Nodes first, then relationships (MATCH the nodes,
  then MERGE edges) — never mixed in one query, with the same ghost-prevention
  rule as Stage 1. At most two `write_cypher` calls per chunk.
- **Provenance pre-creation.** `Document` and `Chunk` nodes are pre-created in
  Python (idempotent `MERGE`) so each chunk's `elementId` can be passed to the
  agent.
- **Set-based resume.** `--resume` skips the *set* of completed `chunk_num`s
  (parallel workers finish out of order).

### Conversation management

The ontology/instance-worker agents use
`SlidingWindowConversationManager(window_size=6, should_truncate_results=True)`;
the enhancer uses `window_size=8`; the instance agent additionally resets its
conversation each chunk.

---

## Query Agent (`query/agent.py`)

Answers natural-language questions over the populated graph. Built once per
session with the full ontology embedded in a cached system prompt. Fixed
five-step workflow:

1. **Read the ontology** from the prompt — the descriptions distinguish similar
   labels (`GOVERNS` vs `RESTRICTS` vs `CONDITIONED_ON`). The query agent embeds
   the **`full_description`** (see below) since it's embedded once per question,
   not per chunk.
2. **Ground entity mentions** — `find_entities_by_name` (case-insensitive
   contains) to map question phrases to real instance nodes.
3. **Compose Cypher** — read-only, restricted to ontology labels/rel-types,
   parameterized with grounded names.
4. **Execute** — `run_read_cypher`.
5. **Summarize** — concise NL answer grounded in the rows.

`get_ontology_schema` and `describe_ontology` are available to refresh / expand
schema mid-session. A `SlidingWindowConversationManager(window_size=40)` bounds a
long REPL session without truncating any single multi-hop question.

---

## Compact snapshot (description fields)

Each `EntityType`/`RelType` stores **both** a `short_description` (≤12-word
phrase) and a `full_description` (complete definition). `ONTOLOGY_COMPACT_SNAPSHOT`
(default `1`) selects which is *serialized into per-chunk prompts*:

- **Ingest loops embed `short_description`** by default — the schema is embedded
  in every one of hundreds of chunk prompts, so compactness controls cost. The
  `describe_ontology` tool fetches full text on demand.
- **The query agent always embeds `full_description`**, independent of the flag —
  it's embedded once per question (cheap), and the richer text measurably
  improves label/edge selection.

---

## Prompt caching (`shared/strands_anthropic.py`)

All agents use Anthropic prompt caching. The Strands `AnthropicModel` silently
drops a `cache_control` marker on a `SystemContentBlock`; the workaround
(`CacheAwareAnthropicModel`) passes structured system blocks via
`params={"system":[...]}`, which `format_request` spreads *after* the string
`system` field, overriding it. The subclass also fixes cache token counts.

| Agent | Strategy |
|---|---|
| Ontology Builder | Two breakpoints: a **static prefix** (base prompt + vocab — cache hit every chunk) and the **snapshot** block, which begins caching once the schema is stable for `ONTOLOGY_CACHE_STABILITY_THRESHOLD` chunks (default 3). |
| Enhancer | Single cached schema prefix (one run). |
| Instance Builder | One cached schema prefix, written once and read by every chunk / worker. |
| Query Agent | Cached schema prefix; every question after the first reads it. |

**TTL.** `ANTHROPIC_CACHE_TTL` (`5m`/`1h`). Ingest defaults to **1h** (the prefix
is re-read across a long run, so one pricier write beats many TTL-expiry
re-writes); the query agent defaults to **5m** (a single-shot question exits
before reuse). A cache-health smoke test (`python -m shared.cache_check`) guards
this silent path against Strands/SDK regressions.

---

## Evaluation harness (`eval/`)

A/B model comparison for the **instance** and **query** phases (the ontology is
built once, so every run starts from a common base).

- **`query-ab`** (read-only) runs the question set through the query agent for
  each candidate model, capturing per-question cost, latency, cycles, and answer.
- **`instance-ab`** (destructive — wipes the instance layer between models)
  compares extraction density/coverage.
- **LLM judge** (`--judge`) grades each answer **Excellent / Good / Partial /
  Weak** using a separate, stronger model that establishes ground truth from
  (a) the source document (a `search_source_document` tool), (b) the web
  (`http_request`, GET only), and (c) its own knowledge.
- Each run writes per-model `<run_id>_<role>_<model>_metrics.jsonl` to
  `eval/logs/` in the same format as ingest, readable by `scripts/cost_watch.py`.

See [`retrieval_quality_investigation.md`](retrieval_quality_investigation.md)
for the methodology, the experiment arc, and findings.

---

## Infrastructure

| Component | Technology |
|---|---|
| AI agents | Strands (Python agent framework) |
| Model | `claude-sonnet-4-6` (all agents); `claude-haiku-4-5-20251001` (domain detect) |
| Neo4j (ontology/enhancer) | `mcp-neo4j-cypher` MCP server via stdio |
| Neo4j (instance/query) | direct Python driver (`shared/neo4j_tools.py`) with deadlock retry |
| Graph database | Neo4j Aura |
| PDF / HTML parsing | `pdfplumber` / `BeautifulSoup4` |
| Credentials | `.env` at repo root |

---

## Ontology design (corpus-dependent)

The ontology is generated, not hand-authored, so its exact contents depend on
the corpus and settings — there is no fixed schema. The *design intent*:

- **Categories, not specifics** — `EntityType`s are broad categories (`Plan`,
  `Obligation`, `Notice`, `Role`, `Process`, `Sanction`, `LegalInstrument`,
  `Facility`, …); specific named things live in the instance layer.
- **A `Concept` catch-all** (in the `legal` seed) gives defined terms, doctrines,
  and abstract notions a coherent home rather than fragmenting them or dropping
  them.
- **Generic, reusable relationship labels** (`APPLIES_TO`, `GOVERNS`, `REQUIRES`,
  `CONDITIONED_ON`, `TRIGGERS`, `PRESCRIBES`, `SUBJECT_TO`, …) with specifics in
  the edge `detail`.
- **Healthy size:** ~20–50 `EntityType`s for a statute + regulations; the
  generalization rules and cap keep it there and keep it *stable* across runs.

(The FLTCA pilot produced ontologies in the ~19–31-type range depending on
configuration; the investigation doc records the comparisons.)

---

## Known limitations & active work

Tracked as GitHub issues; full analysis in
[`retrieval_quality_investigation.md`](retrieval_quality_investigation.md).

- **Specificity** — concrete figures, dates, and section references that exist
  in the source aren't reliably surfaced (they live in prose, not as graph
  properties). The single biggest retrieval-quality lever; largely query-side +
  extraction-property work.
- **Edge semantics** — generic labels like `GOVERNS` can conflate distinct
  meanings (e.g. "operates" vs "supervises"); conditional/sequencing relations
  are under-captured.
- **Instance-layer entity resolution** — `MERGE`-on-`name` catches only exact
  duplicates, so surface-form variants of one entity ("Licensee" / "the
  licensee") stay split. Documented; secondary to specificity/edge-semantics.
- **Schema frozen per instance run** — the instance agent embeds the ontology at
  build time; re-running the Enhancer mid-run requires restarting the instance
  stage.
