# Ontology-Based Knowledge Graph Pipeline — Architecture

---

## Overview

This system converts unstructured legal and regulatory documents into a queryable knowledge graph in Neo4j, then answers natural-language questions over that graph with a high degree of accuracy and nuance.

The core insight is a **two-stage graph construction** approach: first derive an abstract schema (the *ontology*) that describes the types of things and relationships present in the document corpus, then populate the graph with *instance data* that conforms to that schema. The schema-first approach produces a graph that is semantically coherent across many documents and can be queried with Cypher traversals that reflect the actual structure of the legal domain — not just keyword search or embedding similarity.

The system was piloted against the *Fixing Long-Term Care Act, 2021* (FLTCA) and its companion *O. Reg. 246/22 – General*, a combined corpus of ~450 document chunks. Evaluation across 15 questions covering multi-hop traversal, aggregation, hierarchy, path-finding, property retrieval, and stress tests produced 10 Excellent / 4 Good / 1 Partial results, with zero Weak answers.

---

## High-Level Architecture

The system has three independent subsystems that run in sequence:

```
Documents (PDF, HTML, plain text, etc.)
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                   INGEST PIPELINE                           │
│                                                             │
│  Agent 1 — Ontology Builder                                 │
│    Reads every chunk, incrementally builds the schema       │
│    (EntityType nodes, RelType edges) in Neo4j               │
│                          │                                  │
│                          ▼                                  │
│  Agent 2 — Ontology Enhancer                                │
│    Reviews the complete schema, resolves duplicates,        │
│    adds class hierarchies, generalizes labels               │
│                          │                                  │
│                          ▼                                  │
│  Agent 3 — Instance Builder                                 │
│    Reads every chunk again; populates domain-labelled       │
│    instance nodes and typed edges conforming to schema      │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                   NEO4J AURA DATABASE                       │
│   Ontology layer (EntityType / RelType)                     │
│   Instance layer (Obligation, Role, Process, … nodes)      │
│   Provenance edges (FROM_CHUNK → Chunk → Document)         │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                   QUERY AGENT                               │
│    Grounds entity mentions → composes Cypher →             │
│    executes → summarizes in natural language                │
└─────────────────────────────────────────────────────────────┘
```

---

## The Two-Layer Graph Model

Neo4j holds two structurally separate layers that never reference each other directly.

### Ontology Layer

The ontology layer stores the *schema* — the abstract types of entities and relationships that exist in the domain. It uses two Neo4j constructs:

- **`EntityType` nodes** — each carries an `entityLabel` (PascalCase, e.g. `Obligation`, `Role`, `Process`) and a natural-language `description`.
- **`RelType` relationships** — connecting two `EntityType` nodes, carrying a `relLabel` (UPPER_SNAKE_CASE, e.g. `APPLIES_TO`, `CONDITIONED_ON`, `TRIGGERS`) and a `description` that defines the semantics of the edge and describes what the instance-layer `detail` property will hold.

Example ontology edges:
```
(EntityType {entityLabel: "Obligation"}) -[:RelType {relLabel: "APPLIES_TO"}]->
(EntityType {entityLabel: "Role"})

(EntityType {entityLabel: "Sanction"}) -[:RelType {relLabel: "CONDITIONED_ON"}]->
(EntityType {entityLabel: "Process"})
```

The ontology produced from the FLTCA corpus contains **17 entity types** and **23 unique relationship labels** across 258 RelType edges, with 9 `SUBCLASS_OF` edges forming a class hierarchy.

### Instance Layer

The instance layer stores the actual entities and relationships extracted from the documents. Instance nodes carry the domain label directly (e.g. `:Obligation`, `:Role`, `:Sanction`) — not the `EntityType` wrapper — and always have a `name` property used for deduplication via `MERGE`.

Relationships between instance nodes use the same `relLabel` values as the ontology, with an optional `detail` property carrying the specific content (e.g. what is being required, what a prohibition covers, who is conditioned on what).

Every instance node is linked to its source `Chunk` via a `FROM_CHUNK` edge, providing full provenance back to the originating text.

Example instance data:
```
(:Obligation {name: "Licensee must report critical incidents immediately"})
  -[:APPLIES_TO {detail: "duty to report critical incidents"}]->
  (:Role {name: "Licensee"})

(:Obligation {name: "..."})-[:FROM_CHUNK]->(:Chunk {chunk_index: 42})
(:Document {name: "fltca_2021.html"})-[:HAS_CHUNK]->(:Chunk {chunk_index: 42})
```

---

## Document Chunking (`shared/document.py`)

Before any agent runs, documents are split into semantic sections. The chunker handles multiple formats and uses document-native structure rather than fixed character counts.

| Format | Method |
|---|---|
| **Plain text / Markdown / RST / CSV / JSON** | Split on blank lines (`\n{2,}`). Sections under 200 chars are merged into their neighbour. |
| **PDF** | `pdfplumber` extracts text per page, then blank-line split. |
| **HTML / iXBRL (SEC filings)** | BeautifulSoup splits on `<hr>` tags (the SEC filing section-divider convention). iXBRL metadata blocks (`ix:header`, `ix:hidden`, XBRL namespace tags) are removed; inline value wrappers (`ix:nonfraction`, etc.) are unwrapped so numeric values remain in place. Tables are rendered as pipe-delimited rows. Sections under 200 chars are merged. |

The FLTCA Act alone produced 282 chunks; adding O. Reg. 246/22 brought the total to 451 chunks.

---

## Ingest Pipeline — Stage by Stage

### Stage 1: Ontology Builder (Agent 1)

**Purpose:** Read every document chunk and incrementally build a schema in Neo4j using `EntityType` nodes and `RelType` edges. No instance data is written.

**How it works:**

For each chunk, a fresh AI agent is constructed (using the Strands AI agent framework with `claude-sonnet-4-6`) with the current state of the ontology embedded in a cached system prompt. The agent is instructed to:

1. Identify the *types* of entities present in the chunk (e.g. `Obligation`, `Role`, `Process`) — not specific instances.
2. `MERGE` `EntityType` nodes using `entityLabel` alone (never including `description` in the merge clause, to avoid constraint violations on update).
3. `MERGE` `RelType` edges between entity types, using a controlled vocabulary of generic relationship labels (`REQUIRES`, `GOVERNS`, `AUTHORISES`, `TRIGGERS`, etc.) with specifics captured in the `description` field.
4. Batch all writes into at most two Cypher calls: one for nodes, one for edges.

After each chunk, the pipeline fetches the updated ontology snapshot from Neo4j and compares it to the previous snapshot. If the schema changed, a new agent is built embedding the new snapshot. If the schema is stable for three consecutive chunks (configurable via `ONTOLOGY_CACHE_STABILITY_THRESHOLD`), prompt caching is enabled on the snapshot block, reducing input token cost to approximately 10% for subsequent chunks.

**Generalization discipline:** The agent system prompt enforces strict label generalization. Labels must be reusable across jurisdictions and document types. Jurisdiction-specific names (e.g. `OntarioLabourRelationsBoard`) are explicitly rejected in favor of categories (`RegulatoryTribunal`). Relationship labels must connect at least 3–5 entity-type pairs — if a label would only appear once, it is too specific.

**Domain vocabulary seeding:** The pipeline supports pre-seeding the ontology with preferred entity types from a domain vocabulary (e.g. `legal`, `medical`, `financial`, `fraud`). By default it auto-detects the domain from the first chunk using a fast `claude-haiku-4-5-20251001` call. The vocabulary provides a starting set of preferred `EntityType` labels that the agent should default to before inventing new ones.

**Write pattern:** The MCP tool `mcp-neo4j-cypher` is used for all Neo4j writes, accessed via stdio. The agent communicates with Neo4j by calling the MCP tools `write-cypher` and `read-cypher` as needed.

---

### Stage 2: Ontology Enhancer (Agent 2)

**Purpose:** Review the complete schema produced by Agent 1 and make targeted quality improvements before instance data is extracted.

**How it works:**

A single AI agent is built with the full ontology snapshot embedded in a cached system prompt. It runs one extended agentic session (not per-chunk) and is instructed to:

1. **Resolve duplicates:** If two `EntityType` nodes represent the same concept (judged from their `description` fields, not just labels), merge them by copying RelType edges onto the surviving node and deleting the redundant one. Add `SAME_AS` edges when both forms should be preserved for traceability.

2. **Consolidate over-granular types:** If several EntityTypes are specific variants of the same general concept, merge them into a single type with a combined description and transfer all edges.

3. **Introduce class hierarchies:** Where a group of EntityTypes are all subtypes of a common concept, introduce a parent EntityType and link each subtype with `SUBCLASS_OF` edges. For example: `Sanction` and `Standard` as subtypes of `LegalInstrument`; `Obligation`, `Prohibition`, and `Right` as subtypes of `NormativeProvision`.

4. **Generalize jurisdiction-specific labels:** If any label embeds a place name, province, or organization name, rename it to the generalized category.

After every write, the agent runs a targeted verification query to confirm the change took effect before moving on.

The enhancer uses `SlidingWindowConversationManager(window_size=8)` — a wider window than the per-chunk agents — because it reasons across the entire schema and needs to recall earlier observations.

---

### Stage 3: Instance Builder (Agent 3)

**Purpose:** Re-read every document chunk and populate the instance layer with real entities and relationships that conform strictly to the finalized ontology.

**How it works:**

A single AI agent is built once for the entire instance run. The full ontology schema is embedded in a cached system prompt (a fixed cost at the start of the run). For each chunk, the agent receives:
- The chunk text
- The Neo4j `elementId` of the pre-created `Chunk` node for that chunk

The agent is instructed to:

1. Use only entity labels listed as `entityLabel` in the ontology — no new labels.
2. Use only relationship types listed as `relLabel` in the ontology — no new types.
3. Add a `detail` property to each relationship to capture what specifically is being governed, required, prohibited, etc.
4. `MERGE` every entity on `name` to ensure idempotency — re-running the pipeline on the same document does not create duplicates.
5. Connect every extracted entity to the provided `Chunk` node via `FROM_CHUNK`.
6. Batch all writes for a chunk into at most two Cypher calls (nodes, then relationships).

**Pre-creation of provenance nodes:** Before the agent loop begins, the pipeline pre-creates all `Document` and `Chunk` nodes in Neo4j using Python-level tool calls (not via the agent). This ensures the `elementId` values are available to pass to the agent on each chunk.

---

## Query Agent (`query/agent.py`)

**Purpose:** Answer natural-language questions over the populated graph.

**How it works:**

The query agent is built once at session start. The full ontology schema is fetched from Neo4j and embedded in a cached system prompt. The agent follows a fixed five-step workflow for every question:

1. **Read the ontology** — from the system prompt. The `description` fields are critical: they distinguish similarly-labeled types (e.g. `GOVERNS` vs. `RESTRICTS` vs. `CONDITIONED_ON` are all distinct).

2. **Ground entity mentions** — identify noun phrases in the question that name specific entities. Call `find_entities_by_name` to locate matching instance nodes by name (full-text or fuzzy match). Pick the best candidates.

3. **Compose Cypher** — write a read-only Cypher query using only labels and relationship types from the ontology. Parameterize entity names. Add `LIMIT 100` unless the question asks for counts or aggregates.

4. **Execute** — call `run_read_cypher` with the query and parameters.

5. **Summarize** — write a concise natural-language answer grounded in the returned rows. If the result is empty, say so and propose a refinement.

The `get_ontology_schema` tool is included as a fallback so the agent can refresh the schema mid-session if it diverges from the cached version (e.g. after an additional ingest run). The agent is instructed never to skip steps 1 or 2.

---

## Cost Optimization: Prompt Caching

All three ingest agents and the query agent use Anthropic's prompt caching to reduce token costs substantially.

The caching pattern is non-trivial. The Strands framework's `AnthropicModel` silently discards structured system content (a `cache_control` marker on a `SystemContentBlock`). The workaround is to pass the structured system blocks via `params={"system": [...]}` on `AnthropicModel` — these are spread *after* the string `system` field in the framework's `format_request` method and override it. This is implemented in `shared/strands_anthropic.py` as `CacheAwareAnthropicModel`.

| Agent | Caching strategy |
|---|---|
| **Ontology Builder** | Deferred — caching is only enabled once the ontology has been stable for N consecutive chunks (default 3). In the volatile early phase, every rebuild would pay a cache write cost that is never recovered. |
| **Enhancer** | Always cached — the schema is fetched once at build time and doesn't change during the run. |
| **Instance Builder** | Always cached — the schema is frozen for the lifetime of the process; cache is written on the first chunk and read for all subsequent ones. |
| **Query Agent** | Always cached — schema fetched once at `build_agent()` time; every question after the first reads from cache at ~10% of uncached cost. |

At cache-read pricing, schema tokens cost approximately 10% of uncached input pricing, yielding substantial savings on long runs (hundreds of chunks). For example, a cached schema of ~55,000 tokens read 450 times costs approximately the same as reading it uncached 45 times.

---

## Infrastructure

| Component | Technology |
|---|---|
| AI agents | [Strands](https://github.com/strands-agents/sdk-python) (Python agent framework) |
| Language model | `claude-sonnet-4-6` (all agents) |
| Domain detection | `claude-haiku-4-5-20251001` (fast, one-shot) |
| Neo4j access (ingest) | `mcp-neo4j-cypher` MCP server via stdio |
| Neo4j access (query) | Python Neo4j driver (`shared/neo4j_tools.py`) |
| Graph database | Neo4j Aura |
| PDF parsing | `pdfplumber` |
| HTML parsing | `BeautifulSoup4` |
| Credentials | `.env` at repository root |

---

## Ontology: The FLTCA Schema

After the ontology and enhancer pass over the FLTCA + O. Reg. 246/22 corpus, the schema contains:

| Metric | Count |
|---|---|
| Entity types | 17 |
| Unique relationship labels | 23 |
| Total RelType edges | 258 |
| `SUBCLASS_OF` edges (class hierarchy) | 9 |

### Entity Type Hierarchy

```
Party
  └─ InstitutionalActor (abstract)
       ├─ AdministrativeBody   (boards, committees)
       ├─ Court                (judicial/tribunal bodies)
       └─ RegulatoryBody       (agencies, quality centres)

LegalInstrument
  ├─ Sanction                  (penalties, fines, enforcement orders)
  └─ Standard                  (technical codes, guidelines, adopted by reference)

NormativeProvision (abstract)
  ├─ Obligation                (must do / must provide / must report)
  ├─ Prohibition               (must not / ban / restriction)
  └─ Right                     (entitlement / permission)

Concept      — formally defined legal terms (e.g. "abuse", "incapable", "consent")
Facility     — regulated physical premises
Funding      — government financial allocations
Process      — regulated procedures (Inspection, Appeal, Admission, etc.)
Role         — defined functions (Inspector, Licensee, Director, etc.)
```

Two structural types exist for provenance only and are excluded from semantic queries:

- **`Document`** — a source file ingested into the system
- **`Chunk`** — a contiguous text span extracted from a Document

### Key Relationship Labels

| Label | Semantics |
|---|---|
| `APPLIES_TO` | Scopes, qualifies, or is relevant to another entity (39 entity-type pairs) |
| `GOVERNS` | Rules over, regulates, or controls (32 pairs) |
| `CONDITIONED_ON` | Contingent on, dependent on a prior condition being satisfied (29 pairs) |
| `RESTRICTS` | Limits scope, constrains options, or caps a maximum (22 pairs) |
| `PRESCRIBES` | Specifies form, content, or method of another entity (17 pairs) |
| `REQUIRES` | Mandates, obligates, or conditions (13 pairs) |
| `SUBJECT_TO` | Under jurisdiction of or constrained by (11 pairs) |
| `TRIGGERS` | Causes, activates, or initiates another entity (11 pairs) |

---

## Evaluation Results

Evaluation was conducted in two rounds: Round 1 over the Act alone (282 chunks), Round 2 over the Act plus O. Reg. 246/22 (451 chunks). Fifteen questions spanning six categories were assessed.

### Round 2 Summary (Act + Regulation)

| Rating | Count |
|---|---|
| Excellent | 10 |
| Good | 4 |
| Partial | 1 |
| Weak | 0 |

### Improvement from Adding the Regulation

Adding O. Reg. 246/22 materially improved answers for 9 of 15 questions:

- **Q5 (Documents Licensee must maintain):** 18 categories → 60+ categories; specific retention periods now appear (7 years, 10 years, 30 days, 1 year)
- **Q9 (Licensee normative structure):** ~36 prohibitions → ~80 prohibitions; 9 rights → 19 rights
- **Q13 (What the Act prohibits the Licensee from doing):** Financial prohibitions category added with section references; 36 → ~80 prohibitions
- **Q1 (Sanctions from inspection):** Specific penalty amounts now present ($25,000 for first failure; $1,100–$11,000 range for regulatory breaches)
- **Q12 (Licensee reporting obligations):** Tiered incident timelines, PSW records, annual report, monthly attestation

### Query Category Performance

| Category | Examples | Performance |
|---|---|---|
| Multi-hop traversal | Sanctions from inspection; Pre-revocation processes | Excellent |
| Aggregation over full graph | All resident rights; All required documents | Good–Excellent |
| Hierarchy traversal | LegalInstrument subtypes; InstitutionalActor supervisory powers | Excellent |
| Path-finding | Complaint-to-sanction chain; Director-to-Resident shortest path | Good–Excellent |
| Detail/property questions | Licensee reporting obligations; Prohibited actions | Excellent |
| Stress tests | Who investigates abuse; What standards must homes comply with | Good–Excellent |

---

## Known Limitations

**Query formulation variability.** The query agent is not deterministic — the same question can produce different Cypher across sessions depending on how the agent interprets the schema. This primarily affects questions requiring two-path traversals (e.g. "obligations conditioned on a process"), where the agent may choose a simpler, narrower query in one session. Mitigation: add worked example queries to the system prompt for common traversal patterns.

**Numeric targets not captured as structured nodes.** Highly specific numeric values embedded in regulatory prose (e.g. "4 hours/day direct care", "36 minutes/day allied health") are not always extracted as named graph entities during ingestion. They tend to remain as free text that the graph cannot aggregate or filter on.

**Role vs. InstitutionalActor taxonomy.** The Director, Inspector, and Minister are modelled as `Role` nodes, not `InstitutionalActor` nodes. This is a deliberate ontology choice but limits certain supervisory-power queries that expect `InstitutionalActor` or `AdministrativeBody` as the subject.

**Notice/procedural fairness steps not modelled.** The "Notice of Proposal" step that precedes revocation orders under the *Statutory Powers Procedure Act* is not represented as an explicit `Process` node. This is a genuine extraction gap rather than a structural limitation of the graph model.

**Schema is frozen per instance run.** The Instance Builder embeds the ontology in a cached system prompt at agent build time. If the ontology is modified mid-run (e.g. by re-running the Enhancer), the instance agent must be restarted to pick up the changes.
