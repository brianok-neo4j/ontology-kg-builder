# Roadmap

Product-level epics for the ontology-based knowledge graph pipeline.
Status: one of **Done**, **In progress**, **Planned**.

---

## Epic 1 — Core ingest + query pipeline (Done)

Three-agent pipeline that takes a document corpus, builds an ontology from the
most-abstract document(s), enhances it, and extracts an instance-layer knowledge
graph. A Strands query agent answers natural-language questions over the graph by
grounding entity mentions, composing Cypher, and summarising results. Includes
prompt-cache optimisation (schema-level caching for ontology/instance/query
agents), concurrent instance extraction with deadlock-safe retry, and `--resume`
for interrupted runs.

Key deliverables: `ingest/`, `query/`, `shared/`, domain vocabularies, the
`mcp-neo4j-cypher` integration, `load-ontology` subcommand.

---

## Epic 2 — Evaluation harness (Done)

A/B comparison harness for the query and instance phases. LLM judge grades
answers Excellent / Good / Partial / Weak against ground truth established from
source documents + web + model knowledge. Captures per-question cost, latency,
and cycle count. Supports stored groundtruth (generate once, re-judge many times)
so eval reruns are cheap.

Key deliverables: `eval/`, `scripts/cost_watch.py`, judge rubric, FAA question
set, run reports in `eval/results/`.

---

## Epic 3 — FAA/NTSB aviation pilot (In progress)

End-to-end test of the pipeline on 250 NTSB accident reports (structured `.md`
files derived from `avall.mdb`). Developed a hand-authored domain ontology
(`faa/ontology.json`) to work around the over-fragmentation problem that arises
when deriving ontology from data-model outputs rather than narratives. Identified
and fixed the `load-ontology` dead-property bug (`description` → `full_description`
/ `short_description`). Three eval iterations to date; Sonnet progression:
0E/8G/0P/2W → 0E/8G/1P/1W → 1E/8G/1P/0W.

Open items: WeatherCondition normalization (category property), `highest_injury`
normalization, Pilot property richness.

---

## Epic 4 — Automated ontology optimization loop (Planned)

A fully automated process that takes a document corpus and a question set as
inputs and iterates toward a graph that answers all questions at Excellent or
Good quality — without manual intervention.

### Motivation

The FAA pilot (Epic 3) showed that moving Good answers to Excellent requires
targeted ontology and extraction improvements: richer node property specs in
EntityType descriptions, normalized categorical values on key node types
(CausalFactor.classification, WeatherCondition.category), and broader traversal
patterns in the query agent. Identifying which improvements to make required
reading judge rationales against the current ontology, then hand-editing
`ontology.json` and re-running instance extraction. This loop should be
automatable.

### Inputs

| Input | Description |
|---|---|
| `corpus` | Path to document(s) or directory — same as `ingest/main.py` |
| `questions` | Path to a `.json` file of `[{"id": "q1", "question": "..."}]` |
| `max_iterations` (`-Y`) | Maximum optimize→extract→eval cycles before stopping (default: 5) |
| `pass_threshold` | Minimum grade to count as passing: `excellent` or `good` (default: `good`) |

### Pipeline

```
Step 1 (parallel)
  1a. Build ontology   python ingest/main.py ontology <corpus>
                       python ingest/main.py enhance
  1b. Generate         run each question through the judge against source docs
      groundtruth      only — no graph needed; store as eval/groundtruth/<run>.json

Step 2. Load instances  python ingest/main.py instance <corpus>

Step 3. Query           run each question through the query agent
                        (same as eval/query_ab.py, single model)

Step 4. Judge           grade each answer against the stored groundtruth
                        (reference-based judge — no source re-read, cheap)

Step 5. Analyse gaps    IF all answers pass_threshold → done, report final state
                        ELSE → run the Gap Analyser (see below)

Step 6. Apply changes   apply the Gap Analyser's recommendations to the ontology
                        and/or extraction configuration

        → return to Step 2 (wipe instance layer, re-extract, re-query, re-judge)
        → stop after Y iterations regardless of score
```

### Gap Analyser (Step 5)

A new LLM agent (Sonnet) that receives:
- the full judge output for this iteration (grades + rationale for every question)
- the current ontology schema (entity types + relationship types + descriptions)
- a sample of relevant graph nodes and edges from Neo4j (e.g. 20 nodes per
  failing question, pulled via the query agent's entity-grounding tool)

The analyser produces a structured JSON diagnosis with one or more of:

```json
{
  "entity_type_changes": [
    {
      "entityLabel": "WeatherCondition",
      "action": "add_property_spec",
      "property": "category",
      "spec": "Normalized broad category: IMC | Icing | Crosswind | Night | Precipitation | Structural | VMC",
      "rationale": "Q3 judge: weather labels are too granular to distinguish fatal vs non-fatal..."
    }
  ],
  "relationship_changes": [...],
  "extraction_hint_changes": [
    {
      "target": "instance_agent.system_prompt",
      "action": "add_example",
      "content": "WeatherCondition: always set category to one of [IMC, Icing, ...]",
      "rationale": "..."
    }
  ],
  "ontology_description_changes": [
    {
      "entityLabel": "CausalFactor",
      "field": "full_description",
      "new_text": "...",
      "rationale": "..."
    }
  ]
}
```

### Change Applier (Step 6)

Reads the Gap Analyser's JSON and applies each change:

- **entity/relationship description changes** → update in Neo4j via `MERGE … SET`
  (same mechanism as `run_load_ontology`)
- **property spec additions** → append to the entityLabel's `full_description`
  so the instance agent sees the spec in its per-chunk system prompt
- **extraction hint changes** → write to a per-run `hints.json` that the instance
  agent's system prompt includes as an appendix in subsequent iterations

Property and schema changes go into Neo4j (the live ontology); extraction hints
are ephemeral to the run unless explicitly promoted to the domain ontology JSON.

### Exit conditions

The loop stops when either:
1. All questions grade at or above `pass_threshold` (success).
2. `max_iterations` cycles have completed (budget exhausted — report the best
   iteration and what gaps remain).

The final report is a markdown file (same format as `eval/results/`) annotated
with the iteration history: which questions improved, which changes were applied
in each cycle, and what the analyser diagnosed but couldn't fix.

### New CLI surface

```bash
# Full automated run
python -m eval.main optimize \
    --corpus faa/data/accidents/ \
    --questions eval/faa_questions.json \
    --max-iterations 5 \
    --pass-threshold good

# Groundtruth generation only (step 1b, detached) — same subcommand as today,
# backed by shared/groundtruth.py after the move
python -m eval.main groundtruth eval/faa_questions.json \
    --source faa/data/accidents/ \
    --out eval/groundtruth/faa.json

# Single gap-analysis pass against a completed eval run (for debugging the analyser)
python -m eval.main analyse-gaps \
    --eval-result eval/results/query_ab_20260608T210534Z.md \
    --ontology-snapshot faa/ontology.json
```

### New modules / relocations

| Module | Role | Action |
|---|---|---|
| `shared/groundtruth.py` | Generates reference answers from source docs (step 1b) | Move from `eval/groundtruth.py` — used by both the existing eval harness and the new optimize loop, so it belongs in `shared/` |
| `eval/gap_analyser.py` | LLM agent that diagnoses ontology/extraction gaps from judge output | New |
| `eval/change_applier.py` | Applies the analyser's JSON recommendations to Neo4j + config | New |
| `eval/optimize.py` | Loop controller: orchestrates steps 2–7, tracks iterations, writes final report | New |

### Design constraints

- **Step 1 (ontology + groundtruth) runs once per `optimize` invocation**, not per
  iteration. The ontology establishes the schema; only the instance extraction and
  downstream steps repeat. If the analyser determines the ontology itself is
  structurally wrong, that gets flagged in the final report as a manual action
  item rather than triggering an Agent 1 re-run (which would change the schema
  mid-loop and invalidate earlier iteration comparisons).
- **Groundtruth is generated against source docs, not the graph.** The judge uses
  the stored reference — it never re-reads source documents in the iteration loop,
  keeping per-iteration judge cost constant and comparable.
- **Changes are additive across iterations.** The Change Applier accumulates hints
  from all prior iterations; it does not revert earlier changes before applying new
  ones. The iteration log records what was changed per cycle so the history is
  auditable.
- **Instance layer is wiped before each Step 2.** The same mechanism as
  `eval/instance_ab.py`. Every iteration starts from a clean instance graph so
  extraction improvements are measured in isolation from prior runs.
