"""Strands agent for natural-language question answering over the ontology graph.

The agent follows a fixed five-step workflow:

  1. Inspect the ontology (provided in the system prompt below — no tool call).
  2. Identify entity mentions in the question and ground them against the
     instance graph via fuzzy name search.
  3. Compose a Cypher query restricted to ontology labels/rel-types and
     parameterized with the grounded entity names.
  4. Execute the Cypher.
  5. Summarize the rows in natural language.

The ontology schema is fetched once at agent build time and embedded in the
system prompt, then marked as a cacheable prefix so Anthropic's prompt cache
gives ~90% off on the schema tokens for every question after the first
(5-minute TTL). Tradeoff: the schema is frozen for the lifetime of the agent
process — restart to pick up ontology changes.

`get_ontology_schema` is included in the toolset as a fallback — the agent can
call it to refresh the schema mid-session without restarting the process.
"""

from __future__ import annotations

import json

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from shared.strands_anthropic import CacheAwareAnthropicModel as AnthropicModel
from shared.strands_anthropic import cache_control

from shared.neo4j_tools import _run, describe_ontology, find_entities_by_name
from ingest.tools import get_ontology_schema
from query.tools import run_read_cypher


MODEL_ID = "claude-sonnet-4-6"
MODEL_MAX_TOKENS = 8192

BASE_SYSTEM_PROMPT = """You are a graph question-answering agent. The graph is a
Neo4j database with two layers:

- Ontology layer: `EntityType` nodes (property `entityLabel`, `description`)
  connected by `RelType` edges (property `relLabel`, `description`).
- Instance layer: nodes whose Neo4j label is the EntityType's `entityLabel`,
  always with a `name` property; connected to each other by edges whose type
  is a RelType `relLabel`; also connected to `Chunk` nodes via FROM_CHUNK
  for provenance.

The full ontology schema is provided below in the section titled
"Ontology schema (cached)". You do NOT need to call any tool to fetch it —
read it directly from this prompt for every question. It includes each
type's/relationship's full `description`, so you can distinguish similar labels
(e.g. `GOVERNS` vs `RESTRICTS` vs `CONDITIONED_ON`) directly from the prompt;
`describe_ontology` remains available if you want to re-confirm one.

If the schema has changed since this agent was built, call `get_ontology_schema`
to refresh it before composing your Cypher.

Always answer questions by following this fixed workflow:

## Step 1 — Read the ontology

Read the ontology schema below. The `description`s distinguish similarly-labeled
types; call `describe_ontology` for the full definition when two labels look
close and the choice matters.

## Step 2 — Ground entity mentions

From the user's question, identify the noun phrases that name specific
entities. For each one, call `find_entities_by_name` to find matching
instance nodes. Pick the candidate(s) whose label and name fit the question
best. If multiple candidates plausibly match, prefer the most specific.

## Step 3 — Compose Cypher

Write a Cypher query that:
- Uses ONLY entity labels listed in the ontology's `entity_types`.
- Uses ONLY relationship types listed in the ontology's `relationships`.
- Parameterizes entity names (use $params, not string interpolation).
- Returns the minimum columns needed to answer the question.
- Adds `LIMIT 100` unless the question explicitly asks for counts/aggregates.
- Is read-only — no CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV.

## Step 4 — Execute

Call `run_read_cypher` with the query and parameters.

## Step 5 — Summarize

Write a concise natural-language answer grounded in the returned rows. If
the result is empty, say so plainly and propose one refinement (e.g. a
relaxed match or a different entity type) — don't fabricate.

Never skip step 1 or step 2. If a question is genuinely schema-free
(e.g. "how many nodes are there?"), state that and answer directly.
"""


def _fetch_ontology_schema_json() -> str:
    """Read the ontology directly from Neo4j (bypassing the @tool wrapper).

    The query agent always embeds the `full_description` (under the uniform key
    `description`), independent of the ingest-side `ONTOLOGY_COMPACT_SNAPSHOT`
    flag. The schema is embedded once per question here — not hundreds of times
    like the ingest per-chunk loops — so the richer text is cheap and measurably
    improves label/edge selection (eval 2026-06-05: verbose vs compact query
    prompt recovered ~half the grade gap to the denser prior graph).
    """
    field = "full_description"
    entity_types = _run(
        f"""
        MATCH (e:EntityType)
        RETURN e.entityLabel AS entityLabel,
               e.{field} AS description
        """
    )
    rels = _run(
        f"""
        MATCH (a:EntityType)-[r:RelType]->(b:EntityType)
        RETURN a.entityLabel AS from_entityLabel,
               r.relLabel    AS relLabel,
               b.entityLabel AS to_entityLabel,
               r.{field} AS description
        """
    )
    return json.dumps(
        {"entity_types": entity_types, "relationships": rels},
        indent=2,
    )


def build_agent(model_id: str | None = None) -> Agent:
    """Build the query agent.

    Args:
        model_id: Anthropic model to use. Defaults to module-level MODEL_ID.
                  Parameterised so the A/B harness (eval/) can compare models.
    """
    schema_json = _fetch_ontology_schema_json()
    system_blocks = [
        {"type": "text", "text": BASE_SYSTEM_PROMPT},
        {
            "type": "text",
            "text": f"\n\n## Ontology schema (cached)\n\n```json\n{schema_json}\n```\n",
            # 5m default: a single-shot CLI question exits before any reuse, so
            # the cheaper short-TTL write is right. A long REPL session can opt
            # into 1h via ANTHROPIC_CACHE_TTL=1h.
            "cache_control": cache_control(),
        },
    ]
    return Agent(
        model=AnthropicModel(
            model_id=model_id or MODEL_ID,
            max_tokens=MODEL_MAX_TOKENS,
            params={"system": system_blocks},
        ),
        system_prompt=BASE_SYSTEM_PROMPT,
        tools=[find_entities_by_name, run_read_cypher, get_ontology_schema, describe_ontology],
        # Bound the conversation so a long REPL session doesn't grow unboundedly
        # (each question would otherwise re-send the whole prior session as
        # input every cycle). window_size=40 is well above the worst observed
        # single question (~13 cycles ≈ 27 messages), so it never truncates a
        # multi-hop question mid-flight, while still capping cross-question
        # growth and preserving recent context for follow-ups. should_truncate_
        # results trims oversized tool results before dropping messages.
        conversation_manager=SlidingWindowConversationManager(
            window_size=40,
            should_truncate_results=True,
        ),
    )
