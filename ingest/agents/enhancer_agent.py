"""Agent 2: Ontology Enhancer.

Reviews the schema-level ontology built by Agent 1 and improves its quality
by deduplicating EntityTypes, simplifying overly granular types, and
introducing class hierarchies where appropriate. All changes are made
directly in Neo4j via MCP tools.

Cost optimizations (applied 2026-05-14):

1. **Pre-fetch the schema into a cached system prefix.** Same pattern as the
   ontology and instance agents — the schema is fetched once when the agent
   is built, embedded in a second `system` block with `cache_control`, and
   routed via `params={"system": [...]}` because strands' AnthropicModel drops
   structured system content (see
   `~/.claude/projects/.../memory/strands-anthropic-cache-gotcha.md`).
   Within a single run, cycle 1 pays cache_write on the schema; cycles 2..N
   read from cache at ~10% of input price. Was ~$5 of the previous $6.42
   run cost.

2. **Sliding-window conversation manager.** `window_size=8` because the
   enhancer reasons across the whole schema and needs to remember more than
   the ontology/instance agents (which work per-chunk). `should_truncate_results=True`
   keeps oversized tool results from dominating the replayed history. The
   Neo4j graph is still the persistent state — if the agent needs to recall
   something dropped from the window, it can run a targeted read-cypher.

3. **Prompt forbids re-fetching the schema.** The previous run called
   `get_ontology_schema` a second time mid-run, planting another ~55k-token
   schema copy in history and pushing cycle 30 to 138k input tokens. The new
   prompt explicitly says the snapshot is authoritative.
"""

from __future__ import annotations

import json
import os

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.mcp import MCPClient

from shared.neo4j_tools import _run
from shared.strands_anthropic import CacheAwareAnthropicModel as AnthropicModel
from shared.strands_anthropic import cache_control


MODEL_ID = "claude-sonnet-4-6"
MODEL_MAX_TOKENS = 16384

BASE_SYSTEM_PROMPT = """You are an ontology quality reviewer. Your job is to
review the ontology schema currently in Neo4j and make targeted improvements
directly via the MCP tools (read-cypher, write-cypher).

The full ontology schema is provided below in the section titled
"Ontology snapshot (cached)". This snapshot was taken just before the run
started. You MUST treat it as the source of truth for what already exists.

DO NOT call any tool to refetch the EntityType / RelType inventory — the
snapshot already has every label and description. Calling get_ontology_schema
or read_neo4j_cypher against EntityType / RelType just to re-enumerate things
wastes input tokens and is forbidden. (Targeted reads — counting edges from
one specific label, checking for orphans on a specific entity — are still
fine via read_neo4j_cypher.)

## Graph model (read this first)

The ontology is stored using two graph elements:
  - `:EntityType` — a node, with properties `entityLabel`, `short_description`
    (a ≤12-word phrase) and `full_description` (the complete definition).
  - `:RelType`    — a **relationship** between two `:EntityType` nodes, with
    properties `relLabel` (e.g. `"SUBCLASS_OF"`, `"SAME_AS"`), `short_description`
    and `full_description`.

The snapshot below gives you the `full_description` of each type/relationship so
you can judge equivalence. Whenever you create or update a type/edge you MUST set
**both** `short_description` and `full_description`.

`RelType` is a relationship type, NOT a node label. Do not `MERGE (r:RelType {...})`
— that creates a stray node that no other query can see. Always create RelTypes as
edges between two existing EntityType nodes:

```cypher
MATCH (from:EntityType {entityLabel: 'ChildLabel'})
MATCH (to:EntityType   {entityLabel: 'ParentLabel'})
MERGE (from)-[r:RelType {relLabel: 'SUBCLASS_OF'}]->(to)
SET r.short_description = 'is a kind of ParentLabel',
    r.full_description  = 'ChildLabel is a kind of ParentLabel — ...'
```

Same pattern for `SAME_AS` (between equivalent EntityTypes) and for any rewired
edge. To create a new parent EntityType:

```cypher
MERGE (p:EntityType {entityLabel: 'NewParent'})
SET p.short_description = '...', p.full_description = '...'
```

To delete a redundant EntityType after copying its edges onto the surviving one:

```cypher
MATCH (e:EntityType {entityLabel: 'Redundant'})
DETACH DELETE e
```

Use parameterised queries or double-quoted strings to avoid apostrophe-in-string
syntax errors when descriptions contain `'`.

## Use the descriptions

Every `EntityType` node and every `RelType` edge has a `full_description` —
a natural-language definition written by the schema builder (the snapshot shows
it). The `entityLabel` and `relLabel` strings alone are often ambiguous (e.g.
two EntityTypes both labelled "Revenue" might describe very different things).
**Always read the full descriptions in the snapshot before deciding whether two
types are duplicates, whether a type is overly granular, or whether several
types share a common parent.** When the labels look similar but the descriptions
describe different concepts, leave them alone. When the labels differ but the
descriptions describe the same concept, that is your signal to merge or link.

When you create new EntityTypes (e.g. a parent in a hierarchy) or new RelTypes
(SAME_AS, SUBCLASS_OF, or rewired edges), you MUST set BOTH `short_description`
(≤12 words) and `full_description`. When you merge two types, write both on the
surviving type so they capture the combined meaning.

## What to look for and how to fix it

### 0. Ghost nodes (do this FIRST)
A correct ontology has `RelType` edges only between two `:EntityType` nodes. A
malformed write can leave a "ghost" node — a node with no label (or any
non-`EntityType` node) dangling off a `RelType` edge. Before anything else,
remove them, then verify none remain:

```cypher
MATCH (g)-[:RelType]-()
WHERE NOT g:EntityType
DETACH DELETE g
```
```cypher
// verify: should return 0
MATCH (g)-[:RelType]-() WHERE NOT g:EntityType RETURN count(g) AS ghosts
```
These are not real types — never try to merge, link, or describe them; just
delete them. (Do not delete `:EntityType` nodes here — including `Document` and
`Chunk`, which are legitimate EntityTypes.)

### 1. Duplicate or equivalent EntityTypes
If two EntityType nodes clearly represent the same concept — judged primarily
from their `description` fields, not just their labels (e.g. "CEO" and
"ChiefExecutiveOfficer", or "Revenue" and "NetRevenue") — resolve them by
either:
  a) Merging: copy any RelType edges from one to the other, then delete the
     redundant EntityType node. Update the surviving node's `description` if
     needed so it reflects the combined concept.
  b) Linking: add a RelType edge between them with relLabel = "SAME_AS" (and a
     description explaining the equivalence) when both forms should be
     preserved for traceability.

### 2. Overly granular EntityTypes
If several EntityTypes are specific variants of the same general concept —
again, confirm this from the descriptions, not just the labels (e.g.
"Q1Revenue", "Q2Revenue", "AnnualRevenue") — consolidate them into a single
more general EntityType and transfer their RelType edges to the consolidated
type. Set a `description` on the consolidated type that covers the full scope.

### 3. Hierarchy opportunities
If a group of EntityTypes are all subtypes of a common concept (use their
descriptions to confirm the shared semantics), introduce a parent EntityType
with its own `description`, and connect each subtype to it with a RelType edge
where relLabel = "SUBCLASS_OF" and a description such as "<subtype> is a kind
of <parent>". For example: "CommonStock", "PreferredStock", and
"ConvertibleNote" might all be SUBCLASS_OF "Security".

### 4. Jurisdiction- or organization-specific labels
Entity labels should be reusable across jurisdictions and contexts. If any
EntityType label embeds a place name, jurisdiction, or organization name, it is
a candidate for generalization — rename it to the category it represents.
Confirm the right category using the node's `description`, then:
  a) Rename the EntityType to the generalized label (update `entityLabel` and
     revise `description` if needed).
  b) Check whether a more-general EntityType already exists to merge into
     instead.

  Examples:
  | Too specific | Generalized |
  |---|---|
  | `OntarioLabourRelationsBoard` | `LabourRelationsBoard` or `RegulatoryTribunal` |
  | `TorontoMunicipalCouncil` | `MunicipalCouncil` |
  | `CanadaPensionPlan` | `PensionPlan` |

## Rules

- The snapshot below is your input. Do not refetch it.
- Make all changes with write-cypher (MERGE, SET, DELETE as appropriate). Every
  EntityType and RelType you create or modify must have a `description`.
- Remember: EntityType is a node label, RelType is a relationship type. New
  RelTypes must be MERGEd as `(from)-[r:RelType {relLabel: '...'}]->(to)` between
  two existing EntityType nodes — never as `(:RelType {...})` nodes.
- After each write, verify the change took effect by running a TARGETED
  read-cypher that uses the same edge pattern as the snapshot
  (`MATCH (a:EntityType)-[r:RelType]->(b:EntityType)` filtered to the specific
  labels you just touched). If a write claims success but the verification
  query returns no rows, you used the wrong pattern — fix it before continuing.
- Do not modify or delete the Document, Chunk, HAS_CHUNK, or FROM_CHUNK
  nodes/edges.
- Prefer merging over linking when two EntityTypes are clearly identical; prefer
  linking (SAME_AS) when both forms appear frequently in the source text.
- Only introduce a SUBCLASS_OF hierarchy if it genuinely improves clarity — do
  not add hierarchy just for completeness.
- When you finish, write a brief summary of what you changed and why, citing
  the descriptions that motivated each change. Do not re-call the schema tool
  to "confirm the final state" — your write-cypher results plus targeted reads
  are sufficient evidence.
"""


def _fetch_ontology_snapshot() -> dict:
    """Read the current EntityType / RelType graph state from Neo4j.

    The enhancer judges equivalence/hierarchy from descriptions, so it embeds the
    `full_description` (it's a single cached snapshot — size isn't a concern here,
    unlike the per-chunk agents which embed `short_description`).
    """
    entity_types = _run(
        """
        MATCH (e:EntityType)
        RETURN e.entityLabel AS entityLabel,
               e.full_description AS description
        ORDER BY e.entityLabel
        """
    )
    rels = _run(
        """
        MATCH (a:EntityType)-[r:RelType]->(b:EntityType)
        RETURN a.entityLabel AS from_entityLabel,
               r.relLabel    AS relLabel,
               b.entityLabel AS to_entityLabel,
               r.full_description AS description
        ORDER BY a.entityLabel, r.relLabel, b.entityLabel
        """
    )
    return {"entity_types": entity_types, "relationships": rels}


def build_mcp_client() -> MCPClient:
    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="mcp-neo4j-cypher",
                args=[],
                env={
                    **os.environ,
                    "NEO4J_URI": os.environ["NEO4J_URI"],
                    "NEO4J_USERNAME": os.environ["NEO4J_USERNAME"],
                    "NEO4J_PASSWORD": os.environ["NEO4J_PASSWORD"],
                    "NEO4J_SCHEMA_SAMPLE_SIZE": os.environ.get("NEO4J_SCHEMA_SAMPLE_SIZE", "1000"),
                },
            )
        ),
    )


def build_agent(
    mcp_client: MCPClient,
    snapshot: dict | None = None,
    model_id: str | None = None,
) -> Agent:
    """Build the enhancer agent with the current ontology snapshot embedded in
    a cached system prefix.

    Args:
        snapshot: Current ontology state. Fetched from Neo4j if None.
        model_id: Anthropic model to use. Defaults to MODULE-level MODEL_ID.
    """
    if snapshot is None:
        snapshot = _fetch_ontology_snapshot()
    snapshot_json = json.dumps(snapshot, indent=2)

    # Same params-injection pattern as ontology_agent and instance_agent.
    system_blocks = [
        {"type": "text", "text": BASE_SYSTEM_PROMPT},
        {
            "type": "text",
            "text": f"\n\n## Ontology snapshot (cached)\n\n```json\n{snapshot_json}\n```\n",
            # Single extended run, snapshot re-read across many cycles → 1h TTL.
            "cache_control": cache_control("1h"),
        },
    ]

    return Agent(
        model=AnthropicModel(
            model_id=model_id or MODEL_ID,
            max_tokens=MODEL_MAX_TOKENS,
            params={"system": system_blocks},
        ),
        system_prompt=BASE_SYSTEM_PROMPT,
        # Schema is in the system prompt now — the agent doesn't need the
        # get_ontology_schema tool.
        tools=[mcp_client],
        # Window=8 because the enhancer reasons across the whole schema and
        # may need to recall earlier observations more than the per-chunk
        # agents do. Truncate oversized tool results so the replayed history
        # stays predictable.
        conversation_manager=SlidingWindowConversationManager(
            window_size=8,
            should_truncate_results=True,
        ),
    )
