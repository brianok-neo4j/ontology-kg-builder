# Release Notes

## v0.3 — Quantitative extraction significantly improved benchmarks

### Instance agent
- **Generic quantitative value extraction (Rule 10).** The instance agent now stores any explicitly-stated monetary amount, cap, limit, duration, or deadline as a named node property — `amount`, `max_amount`, `min_amount`, `rate`, `duration_days`, `deadline` — even when the property is not declared in the EntityType's `instance_properties` schema. Previously, numeric thresholds buried in prose (e.g. "shall not exceed $250,000") were silently dropped. After this change the `Administrative Penalty` node carries `max_amount: 250000`, and 19 other quantitative facts (fine caps, accommodation charge limits, temperature thresholds, trust account caps) are now queryable.

### Query agent
- For quantitative questions, the agent is now instructed to return full node properties (`RETURN properties(n)` or individual fields) rather than just `n.name`, so `max_amount`, `deadline`, and similar fields extracted at ingest time are surfaced in answers even when they are absent from the ontology schema snapshot.

### Eval harness
- `--only N[,N...]` flag added to `query-ab` for targeted per-question re-runs without cycling the full set. The original 1-based question number is preserved in `r["q"]` so groundtruth lookup and report labels remain correct when running a subset.
- Q11 rewritten from an ambiguous "in what situations does the Director have a direct legal obligation" framing to a structural-role comparison question: *"Under the Act, who bears direct legal obligations to individual residents — the Licensee or the Director — and what is the structural difference between their respective roles in ensuring resident welfare?"*

---

## v0.2 — Instance properties schema + retrieval quality
*Tag: `0.2`*

### Ontology
- **`instance_properties` on EntityType nodes.** The ontology layer now stores a JSON object of property extraction hints per entity type (e.g. `{"section_ref": "...", "amount": "..."}`). The instance agent reads these from the cached schema snapshot to extract dataset-specific properties — legal section references, dollar amounts, tail numbers, dosage codes — without requiring per-corpus prompt modifications.
- **IS-A test in enhancer.** Added an explicit IS-A validation paragraph to the enhancer agent's hierarchy section. Before creating a `SUBCLASS_OF` edge the agent must verify that every instance of the child type would also be a valid instance of the parent.
- Corrected `SUBCLASS_OF` / `SAME_AS` graph model across the full pipeline (ontology-layer only; instance nodes must never carry these edges).

### Retrieval quality improvements
- Added a `Concept` catch-all EntityType to the legal seed vocabulary to absorb residual abstract entities that would otherwise inflate the schema with over-specific types.
- Query agent: always embeds `full_description` (not the compact `short_description`) in the schema snapshot, regardless of the ingest-side `ONTOLOGY_COMPACT_SNAPSHOT` flag. The richer text measurably improves label and edge selection at query time.
- Instance agent: push-toward-completeness extraction guidance to recover recall after the ontology revert experiment.

### Eval framework (introduced)
- Three-phase pipeline: `groundtruth` (opus-4-8 researcher against source documents) → `query-ab` (sonnet-4-6 answers) → `judge` (opus-4-8 grades Excellent / Good / Partial / Weak).
- Groundtruth is document-grounded: all 15 FLTCA questions were audited and rewritten (Q3, Q4, Q7, Q8, Q11) to be answerable from source text with no knowledge of graph schema or traversal paths. Zero Cypher syntax, RelType labels, or hop-count language in any reference answer.
- Per-model JSONL metrics emitted for judge runs, consistent with the ingest/query metrics format read by `cost_watch.py`.

### Observability
- `cost_watch.py` improvements: auto-reads total chunk count from the log; relabels completed chunks.
- Per-chunk metrics logging: lean per-call records instead of cumulative summaries.
- Per-call message traces restored (default on; `--no-trace-logs` / `INGEST_LOG_TRACES=0` to disable).

---

## v0.1 — First stable release
*Tag: `0.1`*

### Architecture
- **Three-agent ingest pipeline** fully established: Ontology Builder (Agent 1, MCP writes via `mcp-neo4j-cypher`) → Enhancer (Agent 2, schema deduplication and hierarchy) → Instance Builder (Agent 3, direct-driver concurrent writes with deadlock-safe retry).
- **Two-layer Neo4j graph model** documented and enforced: ontology layer (`EntityType` nodes, `RelType` edges) fully separated from the instance layer (domain-labelled nodes and typed edges with `FROM_CHUNK` provenance).
- **Strands agent framework** with `CacheAwareAnthropicModel` fixing cache token counts. Prompt caching routed through `AnthropicModel(params={"system": [...]})` rather than the `system_prompt` string, which silently ignored cache control.
- **Query agent** with fixed 5-step workflow: read schema → ground entity mentions → compose Cypher → execute → summarise. Schema embedded as a cached system prompt prefix at build time; `get_ontology_schema` available as a mid-session refresh tool.

### Prompt caching & cost
- Ontology agent caches its static system prompt prefix on its own cache breakpoint, separate from the per-chunk schema snapshot.
- 1-hour cache TTL for ingest (`ANTHROPIC_CACHE_TTL=1h`), preventing schema-prefix re-writes on TTL expiry mid-run.
- Ontology agent conversation reset per chunk to keep the context window predictable and the cached prefix stable.
- `ONTOLOGY_COMPACT_SNAPSHOT` flag: splits `description` into `short_description` / `full_description`, embedding the compact form in the per-chunk ingest prompt to reduce input cost while keeping the full text accessible via `describe_ontology`.
- `ONTOLOGY_MAX_ENTITY_TYPES` guard (default 150) aborts runaway ontology fragmentation early, before quadratic input cost growth.
- Cache-health smoke test (`python -m shared.cache_check`) verifies the caching path end-to-end; run after upgrading `strands` or `anthropic`.

### Data quality
- Ghost-node prevention: require all variables in a relationship `MERGE` to be bound by a preceding `MATCH`/`MERGE` in the same query; unbound variables silently create unlabelled ghost nodes in Cypher.
- Enhancer defensively removes any ghost nodes that may have been created in earlier runs.
- Ontology agent: `MERGE` on `entityLabel` alone for `EntityType` nodes — `description` is never part of the merge pattern, preventing duplicate nodes on description-only changes.
- No-cosmetic-rewrite guidance extended to RelType descriptions.
- Node/edge two-call split made conditional: no placeholder/no-op query when only one call type is needed.

### Cost tracking
- `cost_watch.py`: live ingest cost and progress monitor.
- Fixed cost-subcommand triangular over-count (each model pair was counted multiple times).
- A/B model harness for instance and query phases (`eval/`).

---

## beta — FLTCA pilot
*Tag: `beta`*

Proof-of-concept on the *Fixing Long-Term Care Act, 2021* (S.O. 2021, c. 39, Sched. 1) and O. Reg. 246/22. Demonstrated end-to-end feasibility: ontology extraction from a regulatory corpus → instance population → natural-language query via a graph-grounded agent.

Initial pipeline features:
- Three-agent ingest pipeline (single-threaded instance extraction).
- Neo4j Aura via `mcp-neo4j-cypher` for all writes; direct-driver reads.
- HTML, PDF, and plain-text document loading with semantic (blank-line) chunking.
- Domain vocabulary seeding (`--domain legal` and 8 other built-in domains; `--domain auto` for haiku-based detection).
- `--resume` for the instance stage (set-based, handles out-of-order parallel completion).
- Parallel instance extraction with deadlock-safe concurrent writes and per-worker agents sharing one cached schema prefix; `--concurrency` / `$INSTANCE_CONCURRENCY`.

Key findings documented from the pilot:
- Deriving the ontology from detailed regulations causes severe over-fragmentation: specific provisions become entity *types* rather than instances. Run the ontology stage over the abstract/parent document only; run the instance stage over the full corpus.
