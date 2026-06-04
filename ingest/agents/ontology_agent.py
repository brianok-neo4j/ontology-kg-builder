"""Agent 1: Ontology Builder.

Reads all document chunks and writes a schema-level ontology to Neo4j using
EntityType nodes and RelType edges. No instance data is written.

Mandatory schema bootstrapped before Agent 1 is invoked:
  (EntityType {entityLabel:"Document"})-[:RelType {relLabel:"HAS_CHUNK"}]->
      (EntityType {entityLabel:"Chunk"})

Agent 1 then adds further EntityType nodes and RelType edges based on the
content, and adds a FROM_CHUNK RelType edge from every non-Document EntityType
to Chunk.

Cost optimizations (vs the original single-agent-across-all-chunks design):

1. Per-chunk agent build: a fresh Agent is constructed for each chunk with the
   *current* ontology snapshot embedded in the system prompt and marked with
   `cache_control`. Within a chunk, the LLM runs many cycles (avg ~13) — the
   schema portion is cached after cycle 1 and re-read at 1/10 input price for
   cycles 2..N. The graph in Neo4j is the persistent state across chunks, not
   the in-process conversation history, so dropping the prior history doesn't
   hurt.

2. The system prompt tells the agent the schema is already provided and
   forbids calling get-schema / read-cypher just to inspect it. That cuts the
   tool-call cycles that drove the previous run's growing cost.

3. The system prompt pushes the agent to batch all of a chunk's MERGEs into a
   single write-cypher statement rather than one MERGE per tool call.

See `memory/strands-anthropic-cache-gotcha.md` for why we route the cached
`system` blocks through `params` rather than passing a list to `system_prompt`.
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
from ingest.domain_vocab import DomainVocabulary


MODEL_ID = "claude-sonnet-4-6"
MODEL_MAX_TOKENS = 8192

BASE_SYSTEM_PROMPT = """You are an ontology schema builder. Your job is to read document
chunks and produce a schema-level ontology in Neo4j — no instance data.

## Graph model

Use exactly these node and edge types:
- Nodes:  `EntityType` with properties:
    - `entityLabel` (the label name, PascalCase)
    - `description` (a natural-language description of what this entity type represents)
- Edges:  `RelType` with properties:
    - `relLabel`    (the rel type, UPPER_SNAKE_CASE — use a generic verb category,
                     not a specific predicate; see ## Relationship generalization)
    - `description` (natural-language description of what this relationship represents,
                     its direction semantics, and what kinds of values the instance
                     layer's `detail` property will hold for this edge type)

## Current schema (cached)

The current state of the ontology is provided below in the section titled
"Ontology snapshot". This snapshot is taken just before you process this
chunk. You MUST treat it as the source of truth for what already exists.

DO NOT call `get-schema` or `read-cypher` to inspect EntityType / RelType
nodes — the snapshot already has everything you need to know about what
exists. Calling these tools wastes input tokens for no gain.

(If you need raw read-cypher for something the snapshot doesn't cover — say,
a property on a non-EntityType node — that's fine. But do not use it to
re-list EntityType / RelType.)

## What to build

1. Identify the types of entities present in the chunk (e.g. Person, Organization,
   Product). For each, MERGE an `EntityType` node on `entityLabel` and SET its
   `description` to a concise natural-language definition. If a description
   already exists in the snapshot and is still accurate, **leave it alone — do
   not MERGE the type at all.** Only re-SET a description when you are adding
   genuinely new, distinguishing information or correcting an inaccuracy; never
   to reword, rephrase, or lightly polish wording that is already accurate.

2. Identify the types of relationships between entities. For each, MERGE a
   `RelType` edge between the relevant `EntityType` nodes on `relLabel` and SET
   its `description`. Create relationships in one direction only. Omit inverses
   unless they convey genuinely distinct semantic meaning. As with EntityType
   descriptions: if a RelType edge already exists in the snapshot with an
   accurate description, **leave it alone — do not MERGE it again.** Only re-SET
   a RelType description for a substantive change (new distinguishing detail or
   a corrected inaccuracy), never to reword wording that is already accurate.

3. `Document` and `Chunk` EntityType nodes already exist with a `HAS_CHUNK`
   RelType edge — and they already have descriptions. Do not recreate them or
   overwrite their descriptions.

4. For every new EntityType you create (not Document), add a `RelType` edge
   from that EntityType to the `Chunk` EntityType with `relLabel = "FROM_CHUNK"`
   and a description such as "links an instance entity back to the source
   Chunk it was extracted from".

## Generalization

Entity labels must be **reusable across documents, jurisdictions, and contexts**.
Before writing a label, ask: *"What category of thing is this?"* — then use that
category, not the specific name you encountered.

- **Never embed a jurisdiction, place, organization, or document name in a label.**
  | Too specific (bad) | Generalized (good) |
  |---|---|
  | `OntarioLabourRelationsBoard` | `LabourRelationsBoard` or `RegulatoryTribunal` |
  | `TorontoMunicipalCouncil` | `MunicipalCouncil` |
  | `CanadaPensionPlan` | `PensionPlan` |

- **Prefer the broadest label that is still meaningfully distinct from siblings.**
  If two superficially different entities share the same role or function, they
  belong to the same EntityType. Consolidate rather than proliferate.

- **Jurisdiction-specific adjectives are a red flag.** Any time a proposed label
  contains a place name, province, country, or organization name, generalize it
  before writing it to the graph.

## Relationship generalization

Relationship labels must be **reusable semantic categories**, not specific predicates.
Apply the same generalization discipline as entity labels.

- **A well-designed relLabel connects at least 3–5 different entity-type pairs.**
  If a label would only ever appear once across the whole document, it is too
  specific — generalize it to the verb category it belongs to.

- **Use a generic relLabel + capture specifics in `description` and at the instance
  layer via a `detail` property.** Do not embed the specific nature of the action
  in the label itself.

  | Too specific (bad) | Generalized (good) | Instance `detail` value |
  |---|---|---|
  | `REQUIRES_SCREENING_OF` | `REQUIRES` | `"background check and police record screening"` |
  | `GOVERNS_OPERATION_OF` | `GOVERNS` | `"operation of the facility"` |
  | `AUTHORISES_ENTRY_INTO` | `AUTHORISES` | `"entry into the licensed premises"` |
  | `REPORTS_CONCERNS_TO` | `REPORTS_TO` | `"concerns about resident safety"` |
  | `TRIGGERS_SUPERVISION_ORDER` | `TRIGGERS` | `"supervision order"` |

- When writing the `description` on a RelType edge, mention what kinds of values
  the `detail` property will take at the instance layer. Example:
  ```
  MERGE (o)-[r:RelType {relLabel: 'REQUIRES'}]->(p)
    SET r.description = "An Obligation REQUIRES something of a Party or Role.
                         Instance detail values: 'background screening of staff',
                         'written consent from resident', 'regular training', etc."
  ```

- **Preferred vocabulary.** Default to these labels before inventing new ones.
  Only create a new relLabel when none of these can describe the relationship
  even with a `detail` property to carry the specifics.

  | `relLabel` | Use for |
  |---|---|
  | `REQUIRES` | obligations, mandates, conditions imposed on a party |
  | `GOVERNS` | rules over, regulates, controls |
  | `AUTHORISES` | permits, enables, empowers |
  | `PROHIBITS` | forbids, prevents, bans |
  | `RESTRICTS` | limits scope, constrains options |
  | `EXEMPTS` | excludes from an obligation or rule |
  | `TRIGGERS` | causes, activates, initiates |
  | `SUBJECT_TO` | constrained by, under jurisdiction of |
  | `CONDITIONED_ON` | dependent on, contingent on |
  | `PRESCRIBES` | specifies form, content, or method |
  | `GRANTS` | confers rights, powers, approvals |
  | `ISSUES` | produces, publishes, or serves a document or decision |
  | `REPORTS_TO` | notifies, informs, submits to |
  | `PROTECTS` | shields, defends, guards |
  | `LIMITS` | caps, constrains a maximum |
  | `APPLIES_TO` | is relevant to, covers |
  | `ADVISES` | provides guidance to, informs |
  | `APPROVES` | accepts, confirms, endorses |
  | `INITIATES` | starts, commences, invokes |
  | `PARTICIPATES_IN` | takes part in, engages with |

## Rules

- Only schema — no instances, no specific names or values from the documents.

- **MERGE on `entityLabel` alone — never include `description` in the MERGE
  match clause.** Including description causes a unique-constraint violation
  when the node already exists with a different description string. Always:
  ```
  MERGE (e:EntityType {entityLabel: 'Foo'})
    SET e.description = "..."
  ```
  Never: `MERGE (e:EntityType {entityLabel: 'Foo', description: '...'})`

- **Never create EntityType nodes and RelType edges in the same query.** Mixing
  node MERGEs and edge MERGEs in one statement causes variable-scoping errors
  and stray "ghost" nodes. Keep node writes and edge writes in separate calls.

- **Make only the calls you actually need — at most two, often fewer:**
  - New/updated node types AND new edges → **two calls**: nodes first, then edges.
  - Only new edges (every node type this chunk needs already exists in the
    snapshot) → **one call**: `MATCH` the existing nodes, then `MERGE` the edges.
  - Only new node types (no new edges) → **one call**.
  - Nothing new in this chunk → **no write-cypher calls at all**; just stop.

  **Do NOT issue a placeholder / no-op query (e.g. `MATCH ... RETURN n LIMIT 1`)
  to "satisfy" a two-call pattern.** If there are no new nodes, skip the node
  call entirely and make only the edge call.

  Node call (only when there are new/changed node types):
  ```
  MERGE (a:EntityType {entityLabel: 'Company'})
    SET a.description = "A business entity ..."
  MERGE (b:EntityType {entityLabel: 'Product'})
    SET b.description = "A good or service ..."
  ```

  Edge call (re-fetch nodes with MATCH — this may be the ONLY call):
  ```
  MATCH (a:EntityType {entityLabel: 'Company'})
  MATCH (b:EntityType {entityLabel: 'Product'})
  MATCH (c:EntityType {entityLabel: 'Chunk'})
  MERGE (a)-[r:RelType {relLabel: 'SELLS'}]->(b)
    SET r.description = "A Company SELLS a Product."
  MERGE (a)-[r2:RelType {relLabel: 'FROM_CHUNK'}]->(c)
    SET r2.description = "links an instance entity back to the source Chunk"
  ```

- **Always use double-quoted strings for `description` values** (as shown
  above). Descriptions frequently contain apostrophes ("Residents' Council",
  "operator's obligations") that silently break single-quoted Cypher literals.
  `entityLabel` and `relLabel` values are apostrophe-safe and may use single
  quotes.

- Every EntityType and every RelType you create or update MUST have a non-empty
  `description`. Descriptions should be specific enough that another agent
  reading only the schema can tell two similar types apart.

- **Do not rewrite descriptions for cosmetic reasons.** This applies to BOTH
  `EntityType` nodes and `RelType` edges. Only write an EntityType or RelType
  whose label/pair is new, or whose description needs a *substantive* change
  (new distinguishing detail or a corrected inaccuracy). Re-stating an existing
  type or relationship with an equivalent, reworded description is wasted work —
  skip it entirely. When the snapshot already covers a type or relationship
  accurately, emit no MERGE for it. Prefer writing only the handful of types and
  relationships this chunk genuinely adds or changes.

- **After the write-cypher tool executes successfully, stop immediately.** Do
  not produce any closing text, confirmation, or summary — the tool result is
  sufficient evidence the write succeeded. Silence after the tool call is
  correct behaviour.
"""

_SUMMARY_INSTRUCTION = (
    "- After processing each chunk, briefly state what types you found or updated.\n"
)


def _fetch_ontology_snapshot() -> dict:
    """Read the current EntityType / RelType graph state from Neo4j."""
    entity_types = _run(
        """
        MATCH (e:EntityType)
        RETURN e.entityLabel AS entityLabel,
               e.description AS description
        ORDER BY e.entityLabel
        """
    )
    rels = _run(
        """
        MATCH (a:EntityType)-[r:RelType]->(b:EntityType)
        RETURN a.entityLabel AS from_entityLabel,
               r.relLabel    AS relLabel,
               b.entityLabel AS to_entityLabel,
               r.description AS description
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


def _format_vocab_section(vocab: DomainVocabulary) -> str:
    rows = "\n".join(
        f"| `{et.label}` | {et.description} |"
        for et in vocab.entity_types
    )
    return (
        f"\n\n## Domain vocabulary: {vocab.display_name}\n\n"
        "The following EntityTypes have been pre-seeded as the **preferred vocabulary** "
        "for this document domain. Default to these types for every entity you identify.\n\n"
        "Only introduce a new EntityType when none of the preferred types adequately "
        "captures the concept. In that case you MUST immediately add a "
        "`RelType {relLabel: 'SUBCLASS_OF'}` edge from the new type to the most "
        "appropriate preferred type.\n\n"
        "| Preferred EntityType | What it represents |\n"
        "|---|---|\n"
        f"{rows}\n"
    )


def build_agent(
    mcp_client: MCPClient,
    snapshot: dict | None = None,
    vocab: DomainVocabulary | None = None,
    verbose_summary: bool = False,
    model_id: str | None = None,
    use_cache: bool = False,
) -> Agent:
    """Build a fresh agent with the current ontology snapshot in the cached prefix.

    Args:
        snapshot:        Current ontology state. Fetched from Neo4j if None.
        vocab:           Domain vocabulary to inject as a preferred-types section.
        verbose_summary: If True, appends an instruction asking the agent to
                         summarise each chunk. Adds a second cycle (~16 s) per
                         chunk — disable for speed (default off).
        model_id:        Anthropic model to use. Defaults to MODULE-level MODEL_ID.
        use_cache:       Whether to add cache_control to the snapshot block.
                         Only effective once the ontology has stabilised; before
                         that point every rebuild pays a write that is never read.
    """
    if snapshot is None:
        snapshot = _fetch_ontology_snapshot()
    snapshot_json = json.dumps(snapshot, indent=2)

    effective_model = model_id or MODEL_ID
    prompt = BASE_SYSTEM_PROMPT
    if verbose_summary:
        prompt = prompt + _SUMMARY_INSTRUCTION

    # Block layout:
    #   1. BASE_SYSTEM_PROMPT  (static — same every run)
    #   2. Vocab section       (static per run — only present when vocab is set)
    #   3. Ontology snapshot   (changes when the graph grows; optionally cached)
    system_blocks: list[dict] = [{"type": "text", "text": prompt}]

    if vocab:
        system_blocks.append({"type": "text", "text": _format_vocab_section(vocab)})

    snapshot_block: dict = {
        "type": "text",
        "text": f"\n\n## Ontology snapshot\n\n```json\n{snapshot_json}\n```\n",
    }
    if use_cache:
        # 1h TTL: once the schema is structurally stable the snapshot prefix is
        # re-read across many chunks; keep it warm so it isn't re-written each
        # time a gap exceeds 5 minutes.
        snapshot_block["cache_control"] = cache_control("1h")
    system_blocks.append(snapshot_block)

    return Agent(
        model=AnthropicModel(
            model_id=effective_model,
            max_tokens=MODEL_MAX_TOKENS,
            params={"system": system_blocks},
        ),
        system_prompt=prompt,
        tools=[mcp_client],
        conversation_manager=SlidingWindowConversationManager(
            window_size=6,
            should_truncate_results=True,
        ),
    )
