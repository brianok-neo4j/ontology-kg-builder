# Test plan — multi-agent ontology cost-reduction

Status: **All three agents fully validated 2026-05-14.** Ontology (Phase A + B), enhancer, instance Phase I dry-run, and instance full 50-chunk run all pass.

End-to-end pipeline cost on `amzn-20260331.html`, all three agents fully validated:

| Phase | Baseline | Optimized | Δ |
|---|---:|---:|---:|
| Ontology (50 chunks) | $52.65 | $7.46 | −85.8% |
| Enhancer | $7.04 | $1.43 | −77.7% |
| Instance (50 chunks) | $99.50 | $7.80 | **−92.2%** |
| **Total** | **$159.19** | **$16.69** | **−89.5%** |

---

## Ontology agent

Phase B results (full 50-chunk run on the empty `multiagentontologyv2` database):

| Metric | Baseline (20260513T131942Z) | New (20260514T162549Z) | Δ |
|---|---|---|---|
| Total cycles | 659 | 101 | −85% |
| Non-cached input tokens | 16,314,839 | 133,566 | −99% |
| Output tokens | 247,054 | 99,314 | −60% |
| Cache write tokens | — | 1,371,075 | (new) |
| Cache read tokens | — | 1,423,368 | (new) |
| **Cost (incl cache)** | **$52.65** | **$7.46** | **−85.8%** |
| `get_neo4j_schema` calls | 44 | 0 | ✓ |
| `read_neo4j_cypher` calls | 195 | 0 | ✓ |
| `write_neo4j_cypher` calls | 515 | 51 | ✓ (≈1 per chunk) |
| Per-chunk cycles | mix 4–23 | 49×`2`, 1×`3` | ✓ |

Schema produced: 127 EntityTypes, 397 RelTypes (vs baseline 175 / 529 — about 27% fewer types). Sample descriptions look substantive. The lower density may reflect either better dedup (the agent reads the cached snapshot before each chunk so it consolidates rather than re-creating near-duplicates) OR the batched-write prompt encouraging more conservative type creation. Worth a side-by-side review when time permits.

Log: [logs/20260514T162549Z_metrics.jsonl](logs/20260514T162549Z_metrics.jsonl).

---

Phase A results (5-chunk dry-run on the empty `multiagentontologyv2` database):

| Metric | Baseline first-5 | New first-5 | Δ |
|---|---|---|---|
| Total cycles | 39 | 10 | −74% |
| Input tokens (strands-visible) | 248,517 | 9,968 | −96% |
| Output tokens | 11,314 | 5,899 | −48% |
| Cost (strands-visible) | $0.9153 | $0.1184 | −87% |
| `get_neo4j_schema` calls | 4 | 0 | ✓ |
| `read_neo4j_cypher` calls | 13 | 0 | ✓ |
| `write_neo4j_cypher` calls | 22 | 5 (one batched per chunk) | ✓ |
| Per-chunk cycles | mix 4–14 | exactly `[2,2,2,2,2]` | ✓ |

Real billing will be modestly higher than the strands-visible cost because cache-read tokens are billed (just at 10% of normal input rate) but don't show up in strands' `inputTokens` counter. Realistic Phase B projection: **$10–$20** for the full 50 chunks vs the $52.65 baseline (60–80% reduction).

Log: [logs/20260514T125312Z_metrics.jsonl](logs/20260514T125312Z_metrics.jsonl).

Harness lives at [test_harness.py](test_harness.py); supports re-running Phase A any time. (Original Phase A from 2026-05-13 was deferred and resumed 2026-05-14.)

---

## Enhancer agent

Validated 2026-05-14 by running the optimized enhancer against the already-Phase-B-produced state of `multiagentontologyv2`. Not a clean cold-start comparison (the previous un-optimized enhance had already mutated the graph) but enough to confirm the optimization works.

| Metric | Baseline (un-optimized) | Optimized | Δ |
|---|---:|---:|---:|
| Cycles | 30 | 32 | +2 |
| Non-cached input tokens | 2,081,305 | 49,825 | −97.6% |
| Output tokens | 11,657 | 15,267 | +31% |
| Cache write tokens | — | 107,318 | (new) |
| Cache read tokens | — | 2,166,312 | (new) |
| **Cost (incl cache)** | **$6.42** | **$1.43** | **−77.7%** |
| `get_neo4j_schema` calls | 2 | 0 | ✓ tool removed |
| `read_neo4j_cypher` calls | 14 | 20 | (targeted reads still allowed) |
| `write_neo4j_cypher` calls | 21 | 42 | more substantive edits |

Output tokens went UP (+31%) because the agent had a cheaper input budget and used it to make more substantive consolidations rather than just adding hierarchy edges. Net schema growth: 134→137 EntityTypes, 428→441 RelTypes.

Log: [logs/20260514T180328Z_metrics.jsonl](logs/20260514T180328Z_metrics.jsonl).

---

## Instance agent

**Phase I (5-chunk dry-run) passed 2026-05-14.** Full 50-chunk run still pending.

Phase I results (5-chunk dry-run against the existing `multiagentontologyv2` ontology):

| Metric | Baseline first-5 (20260513T165847Z) | Optimized first-5 (20260514T194741Z) | Δ |
|---|---:|---:|---:|
| Cycles | 42 | 13 | −69% |
| Non-cached input tokens | 3,636,800 | 42,750 | −98.8% |
| Output tokens | 51,557 | 26,272 | −49% |
| Cache write tokens | — | 132,790 | (new) |
| Cache read tokens | — | 762,975 | (new) |
| **Cost (incl cache)** | **$11.68** | **$1.25** | **−89.3%** |
| `get_neo4j_schema` calls | 4 | 0 | ✓ |
| `create_or_merge_node` calls | 120 | (tool removed) | ✓ |
| `create_relationship` calls | 240 | (tool removed) | ✓ |
| `write_neo4j_cypher` calls | 0 | 8 | ✓ ~1.6 batched per chunk |
| Per-chunk cycles | mix 4–14 | `[2,2,4,3,2]` | ✓ |

The dominant lever was **batching**: baseline did 360 individual write calls (120 entity MERGEs + 240 relationship MERGEs) each consuming a cycle. Optimized does 8 batched `write_neo4j_cypher` calls. Schema caching is the secondary lever.

Optimizations applied:

1. Snapshot-in-system with `cache_control` + cache-aware Anthropic adapter (same pattern as ontology agent).
2. `SlidingWindowConversationManager(window_size=6, should_truncate_results=True)`.
3. Dropped `create_or_merge_node` and `create_relationship` from the agent's tool list — forces all writes through the MCP `write_neo4j_cypher` tool.
4. Prompt instructs batched MERGEs in a single `write_neo4j_cypher` call per chunk, with a worked example wiring entities + FROM_CHUNK in one query.

Log: [logs/20260514T194741Z_metrics.jsonl](logs/20260514T194741Z_metrics.jsonl).

### Full 50-chunk instance run

Validated 2026-05-14 against `multiagentontologyv2` (137 EntityTypes / 440 RelTypes from the optimized ontology + enhancer pipeline).

| Metric | Baseline (20260513T165847Z) | Optimized (20260514T205127Z) | Δ |
|---|---:|---:|---:|
| Wall clock | 96.5 min | 36.1 min | −63% |
| Cycles | 659 | 106 | −84% |
| Non-cached input tokens | 30,525,742 | 340,396 | −98.9% |
| Output tokens | 528,018 | 188,641 | −64% |
| Cache write tokens | — | 522,604 | (new) |
| Cache read tokens | — | 6,632,944 | (new) |
| **Cost (incl cache)** | **$99.50** | **$7.80** | **−92.2%** |
| `write_neo4j_cypher` calls | (baseline used `create_or_merge_node` 1,200 + `create_relationship` 2,400 + `get_ontology_schema` 50) | 56 (~1.1 batched calls per chunk) | ✓ |
| Per-chunk cycles | mix 4–23 | 47×`2` + 3×`4` | ✓ |

The full run **beat the Phase-I-extrapolated projection of $12–13** — actual was $7.80. Cache hit pattern stabilised better than a linear 5→50 extrapolation suggested, and output tokens grew less than 10× going from 5 to 50 chunks (the batched-write strategy stays compact even on busier chunks).

Graph populated: 920 instance nodes (top labels: `RiskFactor` ×80, `Chunk` ×50, `SupplementalCashFlowItem` ×38, `LiabilityLineItem` ×37, `ReportSection` ×36, `LongTermDebt` ×30, …), 2,792 relationships. Distribution across ontology types looks sensible.

Log: [logs/20260514T205127Z_metrics.jsonl](logs/20260514T205127Z_metrics.jsonl).

---

## What changed (recap)

Three changes to the ontology agent, all unverified end-to-end:

1. **Per-chunk schema injection with `cache_control`** — [agents/ontology_agent.py](agents/ontology_agent.py) `build_agent()` now accepts a `snapshot` arg and embeds it in a second `system` block marked `cache_control: ephemeral`. The Anthropic-API-shaped blocks go through `params={"system": [...]}` (strands' `AnthropicModel` discards `SystemContentBlock` lists — see `~/.claude/projects/-Users-brianokeefe-git-kgbuilder/memory/strands-anthropic-cache-gotcha.md`).
2. **Agent rebuilt per chunk** — [main.py](main.py) `run_ontology` now calls `fetch_ontology_snapshot()` + `build_ontology_agent(mcp, snapshot=...)` inside the chunk loop. The MCPClient is reused. Conversation history is discarded per chunk; Neo4j is the persistent state.
3. **System prompt updates** — forbids calling `get-schema`/`read-cypher` to inspect EntityType/RelType (the snapshot is authoritative); explicitly tells the agent to batch every chunk's MERGEs into a **single** `write-cypher` call, with a worked example.

The instance agent already had its own caching change applied at the same time. That's also untested under load.

## Baseline to beat

From [logs/20260513T131942Z_metrics.jsonl](logs/20260513T131942Z_metrics.jsonl):

| | Ontology phase (50 chunks) |
|---|---|
| Input tokens | 16,314,839 |
| Output tokens | 247,054 |
| Cost (Sonnet 4.x rates) | **$52.65** |
| Cycles | 659 (avg 13.2/chunk) |
| Wall clock | 77.7 min |
| Per-cycle input | growing 3.8k → 28.7k over the run |

Target ranges if the changes work:

- **Cycles per chunk: 3–5** (one optional read-cypher + one batched write-cypher + closing summary)
- **Cost: $15–$25** (50–70% reduction). Floor is hard to predict because we can't see `cache_read_input_tokens` through strands' metrics.

## Test steps

### Pre-flight

1. Confirm the target DB is in the desired starting state. Choose one:
   - **Clean DB**: `MATCH (n:EntityType) DETACH DELETE n` against `multiagentontology` before starting. Tests the agent from cold (snapshot starts at Document/Chunk only).
   - **Reuse-from-baseline**: leave the existing 175-EntityType / 529-RelType graph in place. Tests in-place growth (snapshot is large from the start; bigger cache write but same payback math).
   - Recommend: **clean DB** for a like-for-like comparison with the baseline log, which was also a fresh run.
2. Verify `multi_agent_ontology_builder/.env` is set to the right `NEO4J_DATABASE` and `ANTHROPIC_API_KEY`.

### Phase A — 5-chunk dry-run

```bash
# Bootstrap Document/Chunk EntityTypes first if DB is clean — _bootstrap_ontology_base
# is already part of run_ontology, so the command below handles it.
python multi_agent_ontology_builder/main.py ontology /Users/brianokeefe/Documents/data/10K-files/amzn-20260331.html
```

Then **interrupt with Ctrl-C after 5 chunks**, or temporarily slice `chunks = list(load_documents(path))[:5]` in [main.py](main.py).

Inspect the new metrics log:

```python
import json, pathlib
rows = [json.loads(l) for l in pathlib.Path("multi_agent_ontology_builder/logs/<new>_metrics.jsonl").read_text().splitlines()]
# Per-chunk deltas — same logic we used on the baseline log
```

**Pass criteria for Phase A:**

- Average cycles per chunk ≤ 6 (vs baseline 13.2).
- The agent does NOT call `get-schema` or any `read-cypher` query against `EntityType` / `RelType` (look at `tool_usage` in the metrics records — schema-reads should be near zero).
- The agent's `write-cypher` calls are batched: ideally one call per chunk; certainly not one per MERGE.
- Resulting EntityType/RelType nodes look comparable in quality to a 5-chunk slice of the baseline. (Quick sanity: spot-check 3–5 EntityType `description` fields.)

If any criterion fails, iterate on the prompt before scaling up.

### Phase B — full 50-chunk run

If Phase A passes, run the full doc:

```bash
python multi_agent_ontology_builder/main.py ontology /Users/brianokeefe/Documents/data/10K-files/amzn-20260331.html
```

**Compare to baseline:**

| Metric | Baseline (20260513T131942Z) | New run | Δ |
|---|---|---|---|
| Total input tokens | 16,314,839 | | |
| Total output tokens | 247,054 | | |
| Total cost | $52.65 | | |
| Total cycles | 659 | | |
| Cycles / chunk | 13.2 | | |
| Wall clock | 77.7 min | | |

A modest 30%+ cost reduction is success. 60%+ is what the math projects if caching is fully effective.

### Phase C — schema quality check

Diff the resulting ontology against the baseline:

```cypher
MATCH (e:EntityType) RETURN e.entityLabel, e.description ORDER BY e.entityLabel
```

Look for:
- Comparable count of EntityTypes (baseline had 175 across 50 chunks; new run should land in the same order of magnitude).
- Descriptions are still meaningful (not truncated or trivial).
- No regression in connectivity — count `:RelType` edges; baseline had 529.

## If results disappoint

- Cycles didn't drop → prompt isn't being followed; tighten the *DO NOT* clauses or move the snapshot earlier in the prompt.
- Cycles dropped but cost didn't → cache isn't hitting. Verify `cache_control` is on the right block by re-running the `format_request` introspection in this session's history.
- Quality degraded → snapshot may not be the right form. Try richer `description` text or sorting it differently.

## Verifying the cache actually hit

Strands' `inputTokens` metric sums only Anthropic's `input_tokens` field (non-cached). It does NOT include `cache_read_input_tokens`. So a successful cache produces a **misleading-looking drop** in the metrics-log input tokens — that's the signature we're looking for. (Cypher_qa proved this earlier: 186k → 351 input tokens for the same query.)

To get a hard confirmation, tap the raw Anthropic response by patching `AnthropicModel.stream` or running one call through the SDK directly outside of strands. Not required for the cost-benefit verdict — the input-token drop is sufficient evidence.

## Resume checklist

When picking this up tomorrow:

- [ ] Decide DB starting state (clean vs reuse)
- [ ] Phase A 5-chunk dry-run
- [ ] Inspect cycles, schema-fetch tool usage, write batching
- [ ] If pass: Phase B full run
- [ ] Compare cost vs $52.65 baseline
- [ ] Phase C schema-quality diff
- [ ] If pass: also run `instance` phase against the (unchanged) doc and compare to the $91.58-input baseline for that phase — same caching changes apply
- [ ] If pass: delete this file or move findings into a memory entry
