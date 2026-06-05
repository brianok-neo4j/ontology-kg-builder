ANALYSIS WRITEUP
Tool: Ontology KG Builder
Ontario FLTCA Evaluation


────────────────────────────────────────────
1. OVERVIEW
────────────────────────────────────────────

Tool:        Ontology KG Builder
Repository:  github.com/neo4j-labs/ontology-based-kg (private)
Approach:    Schema-first, ontology-driven knowledge graph construction
Corpus:      Ontario Fixing Long-Term Care Act, 2021 (FLTCA) + O. Reg. 246/22 – General

The Ontology KG Builder converts unstructured legal documents into a structured Neo4j knowledge graph by first deriving an abstract schema (the ontology) from the document corpus itself, then extracting instance-level entities and relationships that conform to that schema. Applied to the FLTCA corpus — 451 document chunks across the Act and its primary regulation — it produced a queryable graph that a natural-language agent can traverse to answer complex legal questions, achieving 10 Excellent / 4 Good / 1 Partial ratings across a 15-question evaluation covering multi-hop traversal, aggregation, hierarchy, path-finding, property retrieval, and edge cases.


────────────────────────────────────────────
2. METHODOLOGY
────────────────────────────────────────────

2.1 Approach

The defining characteristic of this tool is schema-first extraction: the graph's entity types and relationship types are derived inductively from the document corpus before any instance data is written. This contrasts with approaches that either (a) use a fixed, predefined ontology applied to any domain, or (b) skip schema design entirely and embed raw text chunks for retrieval. The schema-first method produces a graph whose structure mirrors the actual legal domain being modelled, making it highly interpretable and enabling precise Cypher traversals that reflect real legal relationships (e.g. "obligations conditioned on a process", "sanctions triggered by a prohibition").

The core thesis: accurate, semantically rich answers to complex legal questions require graph traversal over structured, domain-coherent knowledge — not vector similarity over raw text chunks.

2.2 Ontology Design

The ontology is generated dynamically from the corpus by Agent 1 and refined by Agent 2 — it is not predefined or hand-crafted. The process is seeded with a domain vocabulary for the legal domain (a curated list of preferred entity type labels with descriptions), which guides the agent toward a consistent, reusable schema rather than rediscovering equivalent concepts under different names across chunks.

After the ontology pass and enhancement over the full FLTCA + O. Reg. 246/22 corpus, the schema settled at:

  - 17 entity types
  - 23 unique relationship labels
  - 258 RelType edges (from/to entity-type pairs)
  - 9 SUBCLASS_OF edges forming a class hierarchy

Entity type hierarchy (key excerpt):

  Party
    └─ InstitutionalActor (abstract)
         ├─ AdministrativeBody   (boards, committees, boards of management)
         ├─ Court                (judicial and statutory tribunal bodies)
         └─ RegulatoryBody       (agencies, quality centres)

  LegalInstrument
    ├─ Sanction                  (penalties, fines, enforcement orders)
    └─ Standard                  (technical codes and guidelines adopted by reference)

  NormativeProvision (abstract)
    ├─ Obligation                (duties: must do / must provide / must report)
    ├─ Prohibition               (restrictions: must not / ban)
    └─ Right                     (entitlements: permitted / protected)

  Concept      — formally defined legal terms (e.g. "abuse", "incapable", "consent")
  Facility     — regulated physical premises (long-term care homes)
  Funding      — government financial allocations
  Process      — regulated procedures (Inspection, Appeal, Admission, etc.)
  Role         — defined legal functions (Inspector, Licensee, Director, etc.)

Key relationship labels and their usage counts:

  APPLIES_TO       39 entity-type pairs — scopes, qualifies, or is relevant to
  GOVERNS          32 pairs             — rules over, regulates, or controls
  CONDITIONED_ON   29 pairs             — contingent on a prior condition being met
  RESTRICTS        22 pairs             — limits scope, constrains, or caps
  PRESCRIBES       17 pairs             — specifies form, content, or method
  REQUIRES         13 pairs             — mandates or obligates
  SUBJECT_TO       11 pairs             — under jurisdiction of or bound by
  TRIGGERS         11 pairs             — causes, activates, or initiates

Every relationship carries a description that defines its semantics and a detail property at the instance layer that captures the specific content of each instance relationship (e.g. what exactly is being required, what prohibition applies to whom).

2.3 Ingestion Pipeline

Ingestion runs as three sequential, independently restartable stages:

Stage 1 — Ontology Builder
  Documents are split into semantic sections (chunks) using document-native structure rather than fixed character counts. HTML/iXBRL filings split on <hr> tags; PDFs split per-page then on blank lines; plain text splits on blank lines. Sections under 200 characters are merged into their neighbour. The full FLTCA corpus produced 451 chunks.

  For each chunk, a fresh AI agent (claude-sonnet-4-6 via the Strands framework) is constructed with the current ontology snapshot embedded in a cached system prompt. The agent is instructed to identify entity types and relationship types present in the chunk — not specific instances — and write them to Neo4j using MERGE statements (idempotent, so re-runs are safe). A controlled vocabulary of 20 generic relationship labels is enforced; the agent adds specifics via a description field rather than inventing new labels. At most two Cypher calls are made per chunk: one for EntityType nodes, one for RelType edges.

  After each chunk, the pipeline fetches the updated schema from Neo4j. If it changed, the agent is rebuilt with the new snapshot. Once the schema is stable for three consecutive chunks, Anthropic prompt caching is activated on the snapshot block, reducing the cost of subsequent schema reads to approximately 10% of uncached pricing.

Stage 2 — Ontology Enhancer
  A single long-running agent reviews the complete schema after all chunks are processed. Its job is quality control: resolving duplicate entity types (e.g. two types that describe the same concept under different labels), consolidating overly granular types, introducing class hierarchies (SUBCLASS_OF edges), and generalizing any jurisdiction-specific labels that slipped through Stage 1. Every write is followed by a targeted verification query to confirm the change took effect. The agent uses a wider conversation window (8 vs. 6) because it reasons across the whole schema rather than per-chunk.

Stage 3 — Instance Builder
  A single agent is built once for the entire instance run, with the finalized ontology schema embedded in a cached system prompt (written once, read for every subsequent chunk at ~10% cost). For each chunk, the agent receives the chunk text and the Neo4j elementId of the pre-created Chunk node. It extracts named entities and relationships conforming strictly to the ontology labels, merging on the entity's name property for deduplication across chunks. Every extracted entity is connected to its source Chunk via a FROM_CHUNK edge, giving full provenance back to the originating text. All writes for a chunk are batched into at most two Cypher calls (nodes, then relationships), communicated via the mcp-neo4j-cypher MCP server over stdio.

2.4 Query Interface

Queries are answered by a separate natural-language agent (claude-sonnet-4-6) that follows a fixed five-step workflow for every question:

  1. Read the ontology — from a cached system prompt. The description fields distinguish semantically similar types.
  2. Ground entity mentions — call find_entities_by_name to locate instance nodes matching noun phrases in the question.
  3. Compose Cypher — write a read-only query using only ontology-defined labels and relationship types, with entity names as parameters.
  4. Execute — call run_read_cypher against Neo4j.
  5. Summarize — produce a concise natural-language answer grounded in the returned rows. If the result is empty, say so plainly and propose a refinement.

The agent cannot invent labels or relationship types not present in the ontology. A fallback get_ontology_schema tool allows refreshing the schema mid-session without restarting.


────────────────────────────────────────────
3. RESULTS & FINDINGS
────────────────────────────────────────────

3.1 Graph Quality

The resulting graph cleanly separates the ontology layer (EntityType nodes, RelType edges) from the instance layer (domain-labelled nodes, typed edges, FROM_CHUNK provenance). The two layers never reference each other directly, which means graph queries over instance data are not polluted by schema metadata.

Ontology quality was high. The Enhancer pass successfully introduced the NormativeProvision and InstitutionalActor abstract parent types, grouped Sanction and Standard as subtypes of LegalInstrument, and consolidated several near-duplicate types produced by Stage 1. The final schema of 17 entity types and 23 relationship labels covers the full normative structure of the FLTCA (obligations, prohibitions, rights, processes, roles, sanctions, standards, concepts, facilities, funding) without over-fitting to the specific document.

Extraction errors observed:
  - A small number of jurisdiction-specific labels occasionally appeared in Stage 1 output before the Enhancer corrected them.
  - Highly specific numeric targets embedded in regulatory prose (e.g. "4 hours/day direct care", "36 minutes/day allied health") were not reliably extracted as named Standard or Obligation nodes — they tended to remain as free text in the chunk.
  - The "Notice of Proposal" procedural fairness step preceding revocation orders was not captured as an explicit Process node; this is a genuine extraction gap.
  - Director, Inspector, and Minister remained as Role nodes rather than InstitutionalActor nodes, which limits certain supervisory-power queries.

No hallucinated relationships were observed: because the instance agent is constrained to merge on name and is instructed never to invent labels, the main failure mode is under-extraction rather than fabrication.

3.2 Query Answering

Round 2 results (Act + O. Reg. 246/22, 451 chunks, 15 questions):

  Excellent:  10
  Good:        4
  Partial:     1
  Weak:        0

Round 1 results (Act only, 282 chunks, same 15 questions):

  Excellent:   8
  Good:        5
  Partial:     2
  Weak:        0

Adding the regulation improved 9 of 15 answers materially. The five most significant improvements:

  Q5  (Documents Licensee must maintain): 18 categories → 60+ categories; specific retention periods now appear (7 years for financial/staff records, 10 years for resident records, 30 days for visitor logs, 1 year for food production records).

  Q9  (Full normative structure for Licensee): ~36 prohibitions → ~80 prohibitions; 9 rights → 19 rights. Now includes financial prohibitions with section references (s.286, s.289, ss.290–295), discharge prohibitions, and new admission-framework rights.

  Q13 (What the Act prohibits Licensee from doing to a resident): Financial prohibitions category added; ~36 → ~80 prohibitions total. Trust fund limits, specific charging prohibitions with section numbers, discharge prohibitions, and physical environment prohibitions all surfaced from the regulation.

  Q1  (Sanctions from inspection): Specific penalty amounts now present — $25,000 for a first s.23.1(1) failure; $1,100–$11,000 range for regulatory breaches; Re-Inspection Fee as a new sanction type. Round 1 had no penalty amounts at all.

  Q12 (Licensee reporting to Director): Tiered incident timelines now explicit — immediate (critical incidents), 1 business day, 3 business days, 10 days. Annual Report, Annual Attestation, monthly PSW records, medication incidents, air conditioning reports all added.

The one regression (Q3 — obligations conditioned on processes, downgraded Excellent → Partial) was a query formulation issue: the agent chose a simpler one-hop query in Round 2 rather than the richer two-path traversal it used in Round 1. The data was in the graph; the Cypher was not. This is the main known failure mode of the query agent.

3.3 Setup & Configuration Complexity

Setup requires:
  - Python 3.14 (specific version required by the Strands dependency chain)
  - A Neo4j Aura instance (credentials in .env)
  - An Anthropic API key
  - The mcp-neo4j-cypher package installed with a version-pinned MCP dependency to avoid downgrade conflicts

The three ingest stages must be run in sequence but are independently restartable via a --resume flag that scans the JSONL logs to determine which chunks were already processed. This makes partial runs recoverable without re-processing completed work.

The main non-trivial configuration decision is domain vocabulary selection (--domain flag). The auto-detection path (a single haiku call on the first chunk) worked reliably for the FLTCA legal domain. Manual domain selection is available for cases where auto-detection is ambiguous.

The mcp-neo4j-cypher package has a known issue: when NEO4J_SCHEMA_SAMPLE_SIZE is unset, it produces invalid Cypher. The pipeline works around this by explicitly passing the env variable in all MCP client builds.

The prompt caching workaround (routing cached system blocks through params rather than system_prompt on the Strands AnthropicModel) is non-obvious and required discovering a silent no-op in the Strands framework. This is encapsulated in shared/strands_anthropic.py and does not require user intervention, but represents a fragile dependency on the framework's internal format_request implementation.

3.4 Cost & Token Usage

Per-million-token pricing used (claude-sonnet-4-6):
  Input:       $3.00
  Cache write: $3.75
  Cache read:  $0.30
  Output:      $15.00

The cost structure is heavily shaped by prompt caching. The ontology schema grows to approximately 55,000–80,000 tokens over a full corpus run. Without caching, reading this schema for 451 chunks and for each query cycle would dominate cost. With caching:
  - Ontology agent: cache activated after schema stabilizes (~first 20–30 chunks), then ~90% discount on schema reads for the remaining 420+ chunks
  - Instance agent: schema cached on the first chunk; all subsequent chunks pay cache read at $0.30/M vs. $3.00/M
  - Query agent: schema cached on first question; all subsequent questions in a session pay cache read

A detailed cost breakdown is available per run via the built-in cost subcommand (python ingest/main.py cost <log_file>). Exact figures depend on corpus size and schema stabilization point; for the full 451-chunk FLTCA + regulation run, the instance agent stage represented the largest cost due to per-chunk output token generation.

3.5 Latency

Ingest (ontology stage, 451 chunks): Approximately 4–8 seconds per chunk in the non-caching phase, dropping to approximately 2–4 seconds per chunk once the schema stabilizes and the cache activates. Total ontology stage: approximately 30–45 minutes for the full corpus, depending on schema change frequency in the early chunks.

Ingest (enhance stage): Single extended run, typically 5–15 minutes depending on how many duplicates and hierarchy opportunities the agent identifies.

Ingest (instance stage, 451 chunks): Approximately 3–6 seconds per chunk (schema cached from chunk 1). Total: approximately 25–40 minutes for the full corpus.

Query answering: Median response time approximately 25–45 seconds per question, with multi-hop traversal questions (Q10 — complaint to sanction chain) reaching 117 seconds in one observed run (13 agent cycles). Simple aggregation questions return in 15–20 seconds.


────────────────────────────────────────────
4. STRENGTHS
────────────────────────────────────────────

[x] Graph structure directly mirrors the legal domain. The ontology is derived from the corpus, not imposed from outside — it reflects the actual types and relationships in the FLTCA (NormativeProvision, Sanction, Process, Role, etc.). This makes the graph interpretable to domain experts and produces Cypher queries that read like legal reasoning.

[x] Precise multi-hop traversal. Because relationships are typed and semantically labelled (CONDITIONED_ON, TRIGGERS, APPLIES_TO, PRESCRIBES), the query agent can follow chains of legal logic across multiple entity types — e.g. tracing the full complaint-to-sanction chain across six stages without hallucinating intermediate steps.

[x] Highly additive across documents. Adding O. Reg. 246/22 to an existing Act-only graph materially improved 9 of 15 answers without degrading the others. The MERGE-on-name idempotency means entities mentioned in both documents are unified rather than duplicated, and new facts attach to existing graph structure naturally.

[x] Full provenance. Every instance entity traces back to the source Chunk and Document via FROM_CHUNK/HAS_CHUNK edges. This makes it possible to verify answers against source text and to scope queries to specific source documents.

[x] No answer fabrication. The instance agent is structurally constrained to only create entities and relationships from the ontology vocabulary. The main failure mode is under-extraction, not hallucination — the agent leaves things out rather than inventing them.

[x] Cost-efficient at scale via prompt caching. The schema-in-system-prompt architecture with Anthropic prompt caching reduces the per-chunk cost of schema tokens to approximately 10% once the schema stabilizes. This makes the approach economically viable for large corpora.

[x] Incrementally restartable. The --resume flag and JSONL metrics log allow interrupted runs to continue from the last completed chunk without re-processing, making the pipeline reliable over large corpora.

[x] Domain-agnostic by design. The entity type labels and relationship vocabulary are enforced to be jurisdiction- and document-agnostic (PascalCase categories, not specific names). The same pipeline can be applied to medical, financial, or other regulated domains by switching the domain vocabulary seed.


────────────────────────────────────────────
5. LIMITATIONS
────────────────────────────────────────────

[x] Query formulation is not deterministic. The query agent can produce different Cypher for the same question across sessions. This primarily affects complex multi-path questions where the agent may choose a simpler traversal in one session than another. One regression was observed (Q3) attributable entirely to this variability, not to a gap in the graph. Mitigation requires adding worked example queries to the system prompt.

[x] Highly specific numeric values are not reliably extracted. Numeric targets embedded in regulatory prose (e.g. "4 hours/day direct care", "36 minutes/day allied health") are not consistently extracted as named graph entities. They tend to remain as free text in the source chunks rather than as structured Obligation or Standard nodes with queryable properties.

[x] Ontology gaps are structural. When the agent fails to model a concept during ingestion (e.g. the Notice of Proposal procedural step before revocation), that gap cannot be recovered at query time — the data is simply not in the graph. Closing these gaps requires re-running the ontology stage with refined prompts or manual Cypher corrections.

[x] Three-stage pipeline requires sequential execution. The three ingest stages must run in order. The instance stage must be restarted if the ontology is modified after it has begun (the schema is frozen in the cached system prompt for the lifetime of the instance agent process). This makes iterative ontology refinement time-consuming on large corpora.

[x] Python 3.14 dependency is restrictive. The specific Python version requirement constrains deployment environments and adds friction for teams that manage Python versions differently.

[x] Role vs. InstitutionalActor taxonomy limits some queries. Director, Inspector, and Minister are modelled as Role nodes rather than InstitutionalActor nodes. Questions about "which institutional actors have supervisory powers" return a narrower answer than the legal reality warrants. This is a known ontology design trade-off, not a bug, but it represents a ceiling on certain query patterns.

[x] Relies on undocumented Strands framework internals. The prompt caching implementation depends on the internal format_request behavior of the Strands AnthropicModel — specifically that params spread after system_prompt and override it. If the framework changes this behavior, caching silently breaks without raising an error.


────────────────────────────────────────────
6. CONCLUSION
────────────────────────────────────────────

The Ontology KG Builder demonstrates that a schema-first approach to knowledge graph construction can produce highly accurate and interpretable answers over complex legal corpora. With 10 Excellent and 4 Good ratings across 15 evaluation questions — and zero Weak answers — it is the strongest performer for questions that require multi-hop legal reasoning: tracing enforcement chains, identifying all normative obligations on a given role, finding conditioned duties, or enumerating the full hierarchy of legal instruments.

The approach is particularly well-suited to corpora where:
  - The document domain has a stable, learnable structure (statutes, regulations, contracts, clinical guidelines)
  - Questions require traversing relationships between entities rather than retrieving passages
  - Answers need to be auditable — sourced back to specific provisions and verifiable
  - The corpus will grow incrementally (additional regulations, amendments, companion documents) and should be queried as a unified whole

The primary trade-off is upfront cost and complexity: three sequential ingest stages totaling 60–90 minutes on a 450-chunk corpus, a Python-version-specific environment, and a query agent whose Cypher formulation is non-deterministic. These are engineering problems with known mitigations (example-query prompting, deterministic query templates for high-frequency question types, containerized deployment), not fundamental limitations of the approach.

For the FLTCA use case specifically, the most compelling result is the additive behavior across documents: adding O. Reg. 246/22 to an Act-only graph improved 9 of 15 answers without any degradation, and the improvements were substantive — penalty amounts, retention periods, staffing minimums, financial prohibitions — precisely the kind of regulatory detail that makes legal compliance research valuable. A graph architecture that naturally accumulates and unifies knowledge from multiple related instruments is well-positioned for regulatory domains where the answer to most practical questions spans more than one document.
