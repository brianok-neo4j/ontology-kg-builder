"""Agent 2: Instance Builder.

Given the ontology written by Agent 1, creates instance nodes and relationships
from each document chunk. Every entity is linked to its source Chunk via
FROM_CHUNK. Only entity labels and relationship types defined in the ontology
are used.

The ontology schema is fetched once when the agent is built and embedded in
the system prompt as a cacheable prefix, so Anthropic's prompt cache gives
~90% off on the schema tokens for every chunk after the first. Tradeoff: the
schema is frozen for the lifetime of this agent's process. If the schema is
changed mid-run (e.g. by re-running the enhancer), restart the instance run.

Implementation note: strands' AnthropicModel.format_request only forwards
system_prompt as a plain string and does NOT honour SystemContentBlock with
`cachePoint`. To get cache_control onto the Anthropic API request, we inject
the structured `system` list via `params={"system": [...]}` on AnthropicModel
— `params` is spread AFTER the string `system` field in format_request, so it
overrides the plain-string version.
"""

from __future__ import annotations

import json

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

from shared.neo4j_tools import _run, describe_ontology, read_cypher, snapshot_description_field, write_cypher
from shared.strands_anthropic import CacheAwareAnthropicModel as AnthropicModel
from shared.strands_anthropic import cache_control


MODEL_ID = "claude-sonnet-4-6"
MODEL_MAX_TOKENS = 8192

BASE_SYSTEM_PROMPT = """You are an instance data builder. Your job is to read document
chunks and populate a Neo4j graph with instance nodes and relationships that
conform strictly to a pre-built ontology schema.

The full ontology schema is provided below in the section titled
"Ontology schema (cached)". You do NOT need to call any tool to fetch it —
read it directly from this prompt for every chunk. To stay compact it shows
each type's/relationship's brief `description`; if you need the full definition
to pick the right label, call `describe_ontology` with the label.

## How to work

At the start of each chunk you will be given:
- The chunk text
- The elementId of the Chunk node for this chunk

## Your goal: thorough, grounded extraction

A single chunk usually contains MANY entities and relationships — often a dozen
or more of each. Capture ALL of them, not just the headline ones:

- Extract EVERY thing that matches an ontology type — the primary subject AND
  every secondary one: roles named in passing, referenced documents/instruments,
  obligations, processes, conditions, criteria, sanctions, rights, etc.
- Capture EVERY relationship the chunk text supports between those entities —
  including multiple edges per entity and edges that chain them together.
- Err toward completeness (recall): if something in the text plausibly fits an
  ontology type or relationship, extract it.

The ONLY limit is grounding: extract only what the chunk text actually supports.
Never invent an entity or relationship to seem thorough — an unsupported edge is
worse than a missing one. Within that bound, be exhaustive.

## Rules

1. Only create entities whose label appears as `entityLabel` on an EntityType node
   in the ontology. Do not invent new labels.

2. Only create relationships whose type appears as `relLabel` on a RelType edge
   in the ontology. Do not invent new relationship types. The ontology uses
   generic labels (e.g. `REQUIRES`, `GOVERNS`, `AUTHORISES`). Add a `detail`
   property to the instance relationship to capture what specifically is being
   required, governed, authorised, etc. Keep `detail` to a short phrase:

   ```cypher
   // Good — generic label, specifics in detail
   MERGE (ob)-[:REQUIRES {detail: 'background check and police record screening'}]->(r)
   MERGE (lic)-[:GOVERNS {detail: 'operation of the licensed home'}]->(fac)
   MERGE (role)-[:AUTHORISES {detail: 'entry into the premises'}]->(fac)
   ```

   `detail` is optional — omit it when the connected node names already make
   the relationship self-explanatory (e.g. `(Person)-[:FROM_CHUNK]->(Chunk)`).

3. Every entity you create MUST be connected to the provided Chunk node via a
   FROM_CHUNK relationship.

4. **Never create nodes and relationships in the same query** — mixing node
   MERGEs and relationship MERGEs in one statement causes variable-scoping
   errors and stray "ghost" nodes. Keep node writes and edge writes separate.
   This is about call STRUCTURE, not volume — batch ALL of a chunk's node MERGEs
   into the single node call and ALL its edge MERGEs into the single edge call
   (a chunk with 15 entities = one node call containing 15 MERGEs). At most two
   calls per chunk:
   - New entities AND relationships (the usual case) → **two calls**: nodes
     first, then relationships.
   - Only relationships to entities that already exist → **one call**: MATCH
     them, then MERGE the edges.
   - Only new entities (no relationships) → **one call**.

   **Do NOT issue a placeholder / no-op query to "fill" the two-call pattern.**

   Call 1 — entity nodes only:
   ```cypher
   MERGE (p:Person {name: 'Andy Jassy'}) SET p.title = 'CEO'
   MERGE (co:Company {name: 'Amazon.com, Inc.'})
   ```

   Call 2 (or the only call) — relationships (MATCH then MERGE):
   ```cypher
   MATCH (c:Chunk) WHERE elementId(c) = $chunk_id
   MATCH (p:Person {name: 'Andy Jassy'})
   MATCH (co:Company {name: 'Amazon.com, Inc.'})
   MERGE (p)-[:EMPLOYED_BY {detail: 'President and CEO'}]->(co)
   MERGE (p)-[:FROM_CHUNK]->(c)
   MERGE (co)-[:FROM_CHUNK]->(c)
   ```

   Pass the chunk elementId as a parameter. MERGE deduplicates on the
   matching pattern, so re-running is idempotent.

   **Every node variable in a relationship MERGE must be bound by a MATCH/MERGE
   in the same query, spelled identically.** An undefined variable does NOT
   error — Cypher silently creates a new unlabeled "ghost" node. E.g. if you
   `MATCH (p:Person {name: 'Andy Jassy'})`, the edge MERGE must use `p`, not
   `person`. Check every variable before issuing the edge call.

5. Use the entity's `name` field for MERGE deduplication (e.g.
   `MERGE (p:Person {name: '...'})`). Set additional properties with `SET`
   in the same statement when relevant.

6. Do not create Document or Chunk instance nodes — those are pre-created.

7. Do not create any nodes or edges that reference the ontology layer
   (EntityType, RelType). The instance graph is fully separate.

8. Do not call `read_cypher` to inspect EntityType / RelType — the cached
   snapshot above is authoritative. Targeted reads for debugging a write are
   fine but discouraged.

   Writes go through `write_cypher`; deadlocks and transient lock errors are
   retried for you automatically, so never re-issue a write just because it was
   slow — only retry if `write_cypher` returns a string beginning with 'ERROR:'.

9. **After the write-cypher tool executes successfully, stop immediately.** Do
   not produce any closing text, confirmation, or summary. Silence after the
   tool call is correct behaviour.
"""

_SUMMARY_INSTRUCTION = (
    "9. After processing each chunk, briefly describe what instances you created.\n"
)


def _fetch_ontology_schema_json() -> str:
    """Read the ontology directly from Neo4j (bypassing the @tool wrapper).

    Embeds the compact `short_description` by default (set
    `ONTOLOGY_COMPACT_SNAPSHOT=0` for `full_description`), under the uniform key
    `description`. Full text is always available via the `describe_ontology` tool.
    """
    field = snapshot_description_field()
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


def build_agent(
    verbose_summary: bool = False,
    model_id: str | None = None,
    schema_json: str | None = None,
) -> Agent:
    """Build the instance agent with the current ontology schema in a cached prefix.

    Writes go through the direct-driver `write_cypher` tool (with transient-error
    retry) rather than the neo4j MCP server, so the agent is safe to run in many
    concurrent threads — each thread builds its own agent and they share only the
    process-wide Neo4j driver. See `run_instance` in `ingest/main.py`.

    Args:
        verbose_summary: If True, appends an instruction asking the agent to
                         summarise each chunk. Adds a second cycle per chunk —
                         disable for speed (default off).
        model_id:        Anthropic model to use. Defaults to MODULE-level MODEL_ID.
        schema_json:     Pre-fetched ontology schema JSON. Fetched from Neo4j if
                         None. Pass it in when building many agents (one per
                         worker) to avoid re-reading the schema per worker and to
                         guarantee an identical cached prefix across workers.
    """
    if schema_json is None:
        schema_json = _fetch_ontology_schema_json()

    effective_model = model_id or MODEL_ID
    prompt = BASE_SYSTEM_PROMPT
    if verbose_summary:
        prompt = prompt + _SUMMARY_INSTRUCTION

    # Anthropic-API `system` blocks. The cache_control marker on the second
    # block tells Anthropic to cache everything up to that point (default
    # 5-minute TTL — enough for typical chunk-to-chunk gaps). The instance
    # agent is built once per run (schema doesn't change), so the cache is
    # always worth writing.
    system_blocks = [
        {"type": "text", "text": prompt},
        {
            "type": "text",
            "text": f"\n\n## Ontology schema (cached)\n\n```json\n{schema_json}\n```\n",
            # Long run, schema re-read on every chunk → keep the prefix warm for
            # an hour so it is written once rather than re-written on TTL expiry.
            "cache_control": cache_control("1h"),
        },
    ]
    return Agent(
        model=AnthropicModel(
            model_id=effective_model,
            max_tokens=MODEL_MAX_TOKENS,
            params={"system": system_blocks},
        ),
        system_prompt=prompt,
        # Direct-driver tools only: write_cypher (batched MERGEs, with
        # transient-error retry), read_cypher (targeted verification), and
        # describe_ontology (full_description on demand — the schema shows only
        # short_descriptions). The high-level create_or_merge_node /
        # create_relationship helpers are deliberately omitted so the agent
        # can't fall back to one-MERGE-per-call.
        tools=[write_cypher, read_cypher, describe_ontology],
        conversation_manager=SlidingWindowConversationManager(
            window_size=6,
            should_truncate_results=True,
        ),
    )
