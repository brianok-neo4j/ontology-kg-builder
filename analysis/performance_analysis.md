# Performance, Speed & Cost Analysis — Ontology-Based KG Pipeline

**Date:** 2026-06-04
**Author:** Engineering analysis
**Scope:** Ingestion (ontology / enhance / instance) and retrieval (query) stages.
**Evidence base:**
- Ingestion metrics logs: `multi_agent_ontology_builder/logs/*_metrics.jsonl` (Strands `accumulated_usage`)
- Retrieval logs: `cypher_qa/logs/*_qa.jsonl`
- Live Aura database (6,027 nodes / 22,529 rels — the FLTCA + O. Reg. 246/22 graph)
- Current source in `ingest/`, `query/`, `shared/`
- `analysis/analysis_writeup.md`, `analysis/architecture.md`

> The logs were produced by an earlier (PDF-input, slightly different) version of
> this codebase, but the agent architecture, prompts, caching strategy, and Neo4j
> write patterns are materially identical to the current code, so the cost/latency
> structure carries over directly. Where a number is corpus-size-dependent it is
> noted.

---

## 1. Corrected baseline (what the pipeline actually costs)

The pipeline's own `cost` subcommand is **badly broken** (see Finding A), so the
numbers in `analysis_writeup.md` §3.4–3.5 are not trustworthy. The table below is
recomputed directly from the raw per-record `accumulated_usage`, correcting for the
fact that a reused agent reports *cumulative* usage on every record (summing those
records triangular-counts the totals).

| Stage | Corpus | Cost (true) | Wall time | Per-chunk | Dominant cost |
|---|---|---|---|---|---|
| **Ontology** (Sonnet) | 169 ch | **$13.82** | 35 min | $0.082 / 12.5 s | uncached **input** (3.87M tok, $11.6) |
| Ontology (Haiku, trial) | 169 ch | $7.04 | 32 min | $0.042 | input (7.65M tok) |
| **Enhance** (Sonnet) | — | **$0.95** | 4 min | — | cache read |
| **Instance** (Sonnet) | 169 ch | **$23.83** | 132 min | $0.142 / 47 s | output + cacheWrite |
| **Instance** (Sonnet) | 282 ch | **$39.11** | 219 min | $0.139 / 47 s | output + cacheWrite |
| **Query** (Sonnet) | 15 Q | $2.52 | — | **$0.168 / 53 s avg** | output + cacheRead |

**Full ~451-chunk corpus (Act + Regulation), end to end:**

- **Cost ≈ $78** (ontology $14 + enhance $1 + instance ~$63)
- **Wall time ≈ 6.5 hours** (ontology 35 min + enhance 4 min + instance ~5.8 h)

This is **~10× slower and meaningfully more expensive than the writeup claims**
(writeup: "instance 25–40 min for the full corpus"; reality: ~5.8 hours / ~$63).
The instance stage is **~80% of cost and ~90% of wall time** — it is the single most
important optimization target.

Instance-stage cost decomposition (combined 451 ch):

| Component | Tokens | Cost | Share |
|---|---|---|---|
| Output | 1.56M | $23.4 | 37% |
| Cache write | 5.1M | $19.2 | 30% |
| Cache read | 40.8M | $12.2 | 19% |
| Input | 2.7M | $8.1 | 13% |

---

## 2. Findings & Recommendations

Effort key: **S** = hours · **M** = 1–2 days · **L** = ≥1 week.
Each recommendation gives an estimated improvement and the evidence behind it.

---

### A. `cost` subcommand over-reports by 85–130× *(correctness bug)*

**Finding.** `run_cost()` in [ingest/main.py:100-119](ingest/main.py#L100-L119) sums
`metrics.accumulated_usage` across **every** log record. But a Strands agent reports
*cumulative* usage, and the instance/query agents are built **once** and reused for
the whole run — so record *N* already contains the totals of records 1…*N*. Summing
all records is a triangular over-count.

Measured: the instance 282-chunk run truly cost **$39.11**, but `cost` reports
**$5,108.57** (130×). The 169-chunk run: true **$23.83**, reported **$2,024** (85×).
Ontology over-reports ~2× (its agent is rebuilt frequently so usage partially resets).

**Recommendation.** For a reused agent, take the **last** record's `accumulated_usage`.
For an agent that is rebuilt mid-run (ontology), sum **per-record deltas** and treat a
decrease as a reset (`delta = max(cur, cur-prev)`). A single helper covers both.

- **Improvement:** correct cost reporting (currently unusable for budgeting). No runtime cost.
- **Effort:** **S** (≈1 hour).

---

### B. Prompt caching is effectively OFF in the ontology stage *(cost + speed)*

**Finding.** The ontology agent only enables `cache_control` after the schema is
stable for 3 consecutive chunks ([ingest/main.py:462-476](ingest/main.py#L462-L476)),
and rebuilds the agent on *any* snapshot difference. In practice the agent keeps
making tiny **description tweaks**, so the snapshot almost never stays byte-identical
for 3 chunks. Measured on the 169-chunk run:

- **Only 9 of 169 chunks (5%) ever read from cache.**
- **142 of 169 chunks (84%) re-sent the full ~20–50K-token schema as uncached input.**
- Result: **3.87M input tokens = $11.6 of the $13.82 ontology cost.**

The deferred-caching optimization is essentially defeated, and the cost it was meant
to remove is fully present.

**Recommendation.** Decouple "rebuild the agent" from "description changed":
1. Compare snapshots on **structure only** — the set of `entityLabel`s and
   `(from,relLabel,to)` triples — ignoring `description` text. Description
   refinements are the enhancer's job anyway; they don't need a per-chunk rebuild.
2. With structural comparison the schema stabilizes within ~20–30 chunks (it has
   17 entity types), so caching engages for the remaining ~120+ chunks.
3. Consider caching from chunk 1 unconditionally: within a single chunk the agent
   runs ~2 cycles, and cycle 2 reads the cache, so a cache write usually pays for
   itself within the same chunk even before cross-chunk reuse.

- **Improvement:** ontology input tokens drop from ~3.87M toward ~0.8–1.2M →
  **~$7–9 saved per run (50–65% of ontology cost)**, plus fewer agent rebuilds →
  lower latency (~20–30%).
- **Effort:** **S–M** (snapshot comparison change + a test).

---

### C. The instance stage is fully sequential but chunks are independent *(speed)*

**Finding.** [ingest/main.py:578-607](ingest/main.py#L578-L607) processes chunks one
at a time; at ~47 s/chunk the 451-chunk corpus takes **~5.8 hours**. Chunk extraction
is **order-independent and idempotent** (every write is `MERGE` on `name`), so there
is no data dependency between chunks. The same is true of the ontology stage.

**Recommendation.** Process chunks concurrently with a bounded worker pool
(e.g. `asyncio` + Anthropic async client, or a thread pool around the sync agent),
say 5–10 in flight. Two things to handle:
- **Write contention:** concurrent `MERGE` on the same `name` (e.g. two chunks both
  mention "Licensee") can deadlock. Mitigate with per-name retry on
  `TransientError`/deadlock, or shard by entity name. Neo4j handles this routinely.
- Give each worker its own agent instance / MCP client.

- **Improvement:** wall time for instance + ontology drops roughly **5–8×**
  (6.5 h → ~50–75 min) at unchanged token cost. Biggest single latency win.
- **Effort:** **M** (concurrency + dedup/retry handling + testing).
- **Pairs with Finding F** (a `name` index makes concurrent MERGE far cheaper and
  reduces deadlock windows).

---

### D. The instance agent carries prior-chunk conversation across independent chunks *(cost + speed + quality)*

**Finding.** One instance agent is reused for all chunks with
`SlidingWindowConversationManager(window_size=6)`
([instance_agent.py:200-216](ingest/agents/instance_agent.py#L200-L216)). Because the
same object persists, each new chunk inherits the **previous chunk's** user message,
assistant turns, and tool results (up to the window). Since chunks are independent,
this carried context is pure overhead — it is re-sent (as input or cache) every cycle,
adds latency, and risks cross-chunk contamination of the extraction.

This is also the most likely driver of the **anomalous 5.1M cache-write tokens**
(Finding E): ~11K cache-write tokens *per chunk*, far more than the 38K schema written
once — consistent with the rolling conversation prefix being re-cached each turn.

**Recommendation.** Reset the agent's `messages` to empty at the start of each chunk
(or rebuild a cheap agent that shares the cached system prompt). Each chunk should
start from a clean slate with only the cached schema + the chunk text.

- **Improvement:** removes carried-over input/cache-write per chunk; expected
  **~10–25% instance cost reduction** and a modest latency drop, plus cleaner,
  more reproducible extraction.
- **Effort:** **S**.

---

### E. Cache-write amplification: 5.1M cache-write tokens vs a 38K schema *(cost)*

**Finding.** The instance schema cached prefix is ~38K tokens and should be written
**once** per run. Measured cache-write is **5.1M tokens ($19.2)** across the two runs —
~80–130× the schema size, i.e. the cached prefix is being **rewritten roughly every
3–4 chunks**. Causes are (a) the 5-minute cache TTL expiring during slow stretches and
(b) the rolling conversation prefix (Finding D) invalidating the cached region.

**Recommendation.**
1. Fix Finding D first (stop the conversation from growing the cached prefix).
2. Pin a **single, stable cache breakpoint** on the system block only and confirm via
   the Anthropic `cache_creation_input_tokens` field that it is written once.
3. For long sequential runs, evaluate the **1-hour extended cache TTL** (higher write
   price, but one write instead of ~100) — only worthwhile if the run stays sequential;
   if Finding C (parallelism) lands, per-worker 5-min TTL is fine.

- **Improvement:** cache-write cost from ~$19 toward ~$1–3 → **~$15–18 saved**
  on a 451-chunk run (~25% of instance cost).
- **Effort:** **M** (requires confirming framework cache behavior — see Finding K).

---

### F. No `name` index → every instance `MERGE` is a full label scan *(speed)*

**Finding.** The only index in the DB is on `EntityType.entityLabel`
(plus the two default LOOKUP indexes). There is **no index on `name`** for any
instance label. `PROFILE` of a representative instance write:

```
MERGE (n:Obligation {name:$name})
  → NodeByLabelScan: 1,630 dbHits + Filter: 1,629  =  3,262 dbHits for ONE node
```

Every entity MERGE scans the entire label. As a label grows (Obligation reached
1,629 nodes), each merge gets more expensive — the instance stage is **O(n²)** in the
number of entities per label, which is part of why per-chunk time creeps up over a run.

**Recommendation.** Before the instance stage, create a uniqueness constraint (which
also creates a backing index) on `name` for every instance EntityType label:

```cypher
CREATE CONSTRAINT <label>_name IF NOT EXISTS
  FOR (n:`<Label>`) REQUIRE n.name IS UNIQUE
```

Iterate over the `entityLabel`s in the ontology (excluding Document/Chunk). Turns each
MERGE from a 1,600+ dbHit scan into a ~2 dbHit index seek.

- **Improvement:** dramatic per-write speedup that *grows* with corpus size;
  removes the O(n²) creep, materially cutting instance wall time (and MCP round-trip
  stalls). Also a prerequisite for safe concurrent MERGE (Finding C).
- **Effort:** **S** (one bootstrap loop, mirrors `_bootstrap_ontology_base`).

---

### G. Instance writes go through MCP stdio; query uses the direct driver *(speed)*

**Finding.** The ingest agents write via the `mcp-neo4j-cypher` subprocess over stdio
([instance_agent.py:149-164](ingest/agents/instance_agent.py#L149-L164)), while the
query side talks to Neo4j through the in-process driver
([shared/neo4j_tools.py](shared/neo4j_tools.py)). Each MCP write is a subprocess
round-trip (serialize → stdio → spawnee → driver → back), adding latency to the 2
write calls per chunk × ~451 chunks. The MCP path exists for the agent's arbitrary
Cypher, but the same can be served by a thin in-process `@tool` (as the query agent
already does).

**Recommendation.** Replace the MCP client in the ingest agents with a direct-driver
`write_cypher` `@tool` (reuse `shared/neo4j_tools._run`). Removes per-call subprocess
overhead and the `NEO4J_SCHEMA_SAMPLE_SIZE` workaround, and simplifies the
`MCPClient` lifecycle caveat in CLAUDE.md.

- **Improvement:** lower and more predictable per-write latency (est. **5–15%**
  instance wall-time reduction); fewer moving parts.
- **Effort:** **M** (swap tool, re-test the two-call write pattern and prompts).

---

### H. Ontology stage runs all chunks long after the schema has converged *(cost + speed)*

**Finding.** The schema has only 17 entity types and 23 rel labels; it converges early
(structural stability within ~20–30 chunks). Yet the ontology stage runs an LLM call
on **all 169/451 chunks** at $0.082 / 12.5 s each, even when later chunks add nothing.
The `no_change_streak` counter that gates caching already measures convergence but is
not used to short-circuit.

**Recommendation.** Once the schema is structurally stable for *M* consecutive chunks
(e.g. 15–20), switch to **sampling** — process every *k*-th remaining chunk (or stop
early), since rare new types tend to cluster. Make *M*/*k* configurable; the enhancer
pass still runs over the full schema afterward.

- **Improvement:** ontology stage chunk count can drop 40–60% →
  **~$5–8 and ~15–20 min saved** on a 451-chunk run, with low quality risk
  (instance stage still reads every chunk).
- **Effort:** **M** (add early-stop/sampling logic + a quality check that no
  late-chunk types are missed).

---

### I. Query agent has no conversation manager → unbounded session history *(cost, minor)*

**Finding.** `build_agent()` in [query/agent.py:129-137](query/agent.py#L129-L137)
sets no `conversation_manager`, so within a REPL session the full Q/A/tool history
accumulates. In the logs, `cacheRead` per question grows 74K → 578K across a session,
and input tokens climb (24K, 38K on later questions). Questions are usually
independent, so most of this is carried waste. Cost impact is modest (cacheRead is
$0.30/M) but it grows unbounded in long sessions and inflates latency.

**Recommendation.** Add `SlidingWindowConversationManager(window_size=4,
should_truncate_results=True)`, or reset history per question unless the user is
clearly asking a follow-up. The cached schema prefix is unaffected.

- **Improvement:** flattens per-question cost/latency in long sessions
  (later-question cost ~2–3× → ~1×); **S** savings per session but better worst case.
- **Effort:** **S**.

---

### J. Metrics logs are enormous (543 MB) because full cumulative traces are logged every chunk *(observability)*

**Finding.** `_log()` writes `result.metrics.get_summary()` after every chunk
([ingest/main.py:196-208](ingest/main.py#L196-L208)). For a reused agent that summary
includes **all accumulated `traces` and `agent_invocations`**, which grow every cycle.
A single instance record grows from 7 KB (chunk 1) to **2.9 MB (chunk 169)**; the
282-chunk log is **543 MB**. This makes logs slow to write, slow to parse (the
`--resume` scan re-reads them), and the root cause of the `cost`-command confusion.

**Recommendation.** Log **per-invocation deltas**, not the cumulative summary: record
only this chunk's input/output/cache tokens, cycle count, and duration (subtract the
previous cumulative values, or read per-call usage). Drop `traces`/`agent_invocations`
from the persisted record (keep behind a `--debug-traces` flag).

- **Improvement:** logs shrink ~100–1000×; faster `--resume`; makes Finding A's fix
  trivial. No model cost change.
- **Effort:** **S–M**.

---

### K. Caching depends on undocumented Strands `format_request` internals *(robustness)*

**Finding.** All cache savings rely on routing system blocks through
`params={"system":[...]}` so they spread *after* the string `system` field
([strands_anthropic.py](shared/strands_anthropic.py) + agent builders). If Strands
changes `format_request`, **caching silently breaks with no error** — and given
Finding B (caching barely engages in ontology) it could already be partially broken
without anyone noticing.

**Recommendation.** Add a cheap **cache-health assertion** to a smoke test: run 2
chunks and assert `cacheReadInputTokens > 0` on the second. Fail CI / warn loudly if
the second call shows zero cache reads. This catches both framework regressions and
the Finding B / E TTL issues.

- **Improvement:** prevents silent ~10× cost regressions; turns an invisible failure
  into a loud one.
- **Effort:** **S**.

---

### L. Model right-sizing for the instance stage *(cost)*

**Finding.** All agents use `claude-sonnet-4-6`. Output (3.46K tok/chunk × $15/M) is
the single largest instance cost component (37%). Much of the per-chunk work — emit
two `MERGE` batches conforming to a fixed schema — is mechanical. A Haiku trial was
already run for the ontology stage (cost $7 vs $14) but its quality wasn't compared.

**Recommendation.** A/B the instance stage on a held-out chunk set:
- Haiku-4.5 for instance extraction (4× cheaper output), measuring extraction recall
  vs the Sonnet baseline on the 15-question eval; OR
- a **router**: Haiku for short/simple chunks, Sonnet for long/dense ones (chunk
  length is a cheap proxy). The writeup notes the main failure mode is *under*-
  extraction, so quality must be measured, not assumed.

- **Improvement:** if Haiku holds quality, instance output cost ~$23 → ~$6
  (**~$17 saved**, ~27% of instance cost) and faster inference; partial if routed.
- **Effort:** **M** (A/B harness + eval scoring).

---

### M. Reduce cycles/round-trips per chunk *(cost + speed)*

**Finding.** Instance averages ~2.4 cycles/chunk and the system prompt mandates **two**
write calls (nodes, then rels) — but the runtime prompt in
[main.py:598](ingest/main.py#L598) tells the agent to "Batch all of this chunk's MERGEs
into a **single** call," contradicting the system prompt's two-call rule
([instance_agent.py:77-96](ingest/agents/instance_agent.py#L77-L96)). Each extra cycle
is a full LLM generation + MCP round-trip (~15–20 s). Query multi-hop questions
similarly burn up to 13 cycles / 117 s.

**Recommendation.**
- Resolve the one-vs-two-call contradiction. A single `MERGE`-nodes-then-`MATCH`-rels
  statement in one call (using `WITH` to pass node handles) removes one round trip per
  chunk where the model can reliably produce it.
- For the **query** agent, add 3–4 worked example traversals to the system prompt
  (the writeup already flags non-deterministic Cypher / the Q3 regression). Good
  examples reduce retry cycles on multi-hop questions — the dominant query latency.

- **Improvement:** ~1 fewer cycle/chunk → est. **10–20% instance latency + output
  cost** reduction; query multi-hop latency/cost down via fewer retries.
- **Effort:** **S** (prompt) / **M** (validating single-call writes don't regress).

---

## 3. Priority matrix

| # | Recommendation | Impact | Effort | Type |
|---|---|---|---|---|
| **A** | Fix `cost` over-count | Correct $ visibility (was 85–130× off) | S | Bug |
| **F** | Add `name` index/constraint per label | Large, growing speedup; enables C | S | Speed |
| **B** | Make ontology caching actually engage | ~$7–9 + 20–30% latency / ontology run | S–M | Cost+Speed |
| **C** | Parallelize chunk processing | **5–8× wall-time** on ingest | M | Speed |
| **D** | Reset conversation per instance chunk | ~10–25% instance cost + cleaner extraction | S | Cost+Quality |
| **E** | Kill cache-write amplification | ~$15–18 / instance run | M | Cost |
| **J** | Log per-chunk deltas, drop traces | Logs 100–1000× smaller; faster resume | S–M | Observability |
| **K** | Cache-health smoke test | Prevents silent 10× regressions | S | Robustness |
| **H** | Early-stop / sample ontology chunks | ~$5–8 + 15–20 min / run | M | Cost+Speed |
| **L** | Right-size instance model (Haiku A/B) | up to ~$17 / instance run | M | Cost |
| **M** | Fewer cycles / worked query examples | ~10–20% latency; better query determinism | S–M | Cost+Speed |
| **I** | Bound query session history | Flatter cost in long sessions | S | Cost |

### Suggested sequencing

1. **Quick wins first (1 day):** A, F, K, I, D — all **S**, independently shippable,
   and A+F+K de-risk everything else (correct measurement, fast writes, cache visibility).
2. **Biggest levers next:** C (parallelism, the 5–8× latency win) + B (re-enable
   ontology caching) + E (cache-write fix). F must land before C.
3. **Then:** J (logging), H (ontology sampling), M (cycles/examples).
4. **Measured experiment:** L (Haiku A/B) — needs the eval harness and an honest
   recall comparison, since under-extraction is the known failure mode.

### Combined expected outcome (451-chunk corpus)

| | Now | After quick wins + C/B/E | After all |
|---|---|---|---|
| Ingest wall time | ~6.5 h | **~50–75 min** (parallelism) | ~45–60 min |
| Ingest cost | ~$78 | ~$55–60 | **~$40–48** (with Haiku A/B) |
| Cost reporting | wrong (85–130×) | correct | correct |
| Query p50 / p95 latency | 53 s / 117 s | — | lower via worked examples |

---

## 4. Notes & caveats

- The token/latency figures come from an earlier **PDF-input** run (169 + 282 chunks);
  the current code ingests HTML and the live graph has **451 Chunks**. Per-chunk
  economics are architecture-bound and transfer; absolute totals scale with chunk count.
- Cache-write amplification (E) and the exact Strands cache behavior (K) should be
  confirmed empirically with the `cache_creation_input_tokens` field before investing
  in the 1-hour-TTL path — the diagnosis is evidence-based but the framework internals
  weren't executed during this analysis.
- Quality recommendations (worked query examples in M; numeric-target extraction and
  the Role-vs-InstitutionalActor gaps from the writeup) are accuracy levers that are
  largely orthogonal to the cost/speed work here and can proceed in parallel.
</content>
