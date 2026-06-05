"""High-level Neo4j tool wrappers for Strands agents.

These @tool functions use the neo4j Python driver directly so they work as
standalone Strands tools without MCP coordination. The agent also receives the
neo4j-mcp MCPClient (loaded in agent.py) for raw read-cypher / write-cypher
access when it needs to write arbitrary Cypher.

Driver connection is lazy and shared within a process. Credentials come from
NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD environment variables.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import TransientError
from strands import tool


_driver: Driver | None = None


def _get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


def _run(query: str, params: dict | None = None) -> list[dict]:
    db = os.environ.get("NEO4J_DATABASE")
    with _get_driver().session(database=db) as session:
        result = session.run(query, params or {})
        return [dict(r) for r in result]


def snapshot_description_field() -> str:
    """Which description field the per-chunk ontology snapshots embed.

    Returns `'short_description'` (compact, the default) or `'full_description'`
    (verbose). Toggle with `ONTOLOGY_COMPACT_SNAPSHOT=0`. Both fields are ALWAYS
    written to the graph regardless of this flag — it only selects which one is
    serialized into the prompt snapshot, so switching modes between runs is safe
    (no graph-schema mismatch). The return value is one of two fixed literals and
    is safe to interpolate into Cypher.
    """
    compact = os.environ.get("ONTOLOGY_COMPACT_SNAPSHOT", "1").strip().lower() not in (
        "0", "false", "no", "off"
    )
    return "short_description" if compact else "full_description"


def _run_write(
    query: str,
    params: dict | None = None,
    *,
    max_attempts: int = 6,
    base_delay: float = 0.5,
    max_delay: float = 20.0,
) -> list[dict]:
    """Execute a write query, retrying transient failures with exponential backoff.

    When instance chunks are processed concurrently, two workers frequently
    `MERGE` overlapping entity names (e.g. both mention "Licensee") or touch
    the same nodes when creating relationships. Neo4j resolves most of this by
    making one transaction wait, but under contention it aborts one with a
    `TransientError` — typically `Neo.TransientError.Transaction.DeadlockDetected`
    or a lock-acquisition timeout. Because every write the agents issue is an
    idempotent `MERGE`, simply retrying is safe.

    Backoff is exponential with full jitter so colliding workers de-synchronise
    instead of retrying in lockstep. `TransientError` covers deadlocks and lock
    timeouts; non-transient errors (syntax, constraint logic) raise immediately.
    """
    db = os.environ.get("NEO4J_DATABASE")
    last_exc: TransientError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with _get_driver().session(database=db) as session:
                result = session.run(query, params or {})
                return [dict(r) for r in result]
        except TransientError as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            time.sleep(delay * (0.5 + random.random()))  # full jitter
    assert last_exc is not None
    raise last_exc


@tool
def create_or_merge_node(label: str, name: str, properties: str = "{}") -> str:
    """Create or merge a node in the ontology graph.

    Args:
        label:      PascalCase class label (e.g. 'Person', 'Organization').
        name:       Canonical name of the entity.
        properties: JSON string of additional key/value properties.

    Returns:
        The elementId of the created/merged node.
    """
    props: dict[str, Any] = json.loads(properties) if properties else {}
    props["name"] = name
    rows = _run(
        f"MERGE (n:{label} {{name: $name}}) SET n += $props RETURN elementId(n) AS id",
        {"name": name, "props": props},
    )
    return rows[0]["id"] if rows else ""


@tool
def create_relationship(
    from_id: str,
    to_id: str,
    rel_type: str,
    properties: str = "{}",
) -> str:
    """Create a directed relationship between two nodes.

    Args:
        from_id:    elementId of the source node.
        to_id:      elementId of the target node.
        rel_type:   Relationship type in UPPER_SNAKE_CASE (e.g. SAME_AS, SUBSET_OF).
        properties: JSON string of additional key/value properties.

    Returns:
        The elementId of the created/merged relationship.
    """
    props: dict[str, Any] = json.loads(properties) if properties else {}
    rows = _run(
        f"""
        MATCH (a) WHERE elementId(a) = $from_id
        MATCH (b) WHERE elementId(b) = $to_id
        MERGE (a)-[r:{rel_type}]->(b) SET r += $props
        RETURN elementId(r) AS id
        """,
        {"from_id": from_id, "to_id": to_id, "props": props},
    )
    return rows[0]["id"] if rows else ""


@tool
def find_entities_by_name(name: str) -> str:
    """Search the graph for entities whose name contains the given string (case-insensitive).

    Always call this before creating a new entity to avoid duplicates.

    Args:
        name: The name or partial name to search for.

    Returns:
        JSON list of {id, label, name} dicts for up to 10 matching nodes.
    """
    rows = _run(
        """
        MATCH (n)
        WHERE toLower(n.name) CONTAINS toLower($name)
        RETURN elementId(n) AS id, labels(n)[0] AS label, n.name AS name
        LIMIT 10
        """,
        {"name": name},
    )
    return json.dumps(rows)


@tool
def write_cypher(query: str, params_json: str = "{}") -> str:
    """Execute a write Cypher statement (MERGE/CREATE/SET/MATCH...) and return rows as JSON.

    Transient failures (deadlocks, lock timeouts under concurrent writes) are
    retried automatically with exponential backoff — you do NOT need to retry
    writes yourself. Pass entity names and the chunk elementId as parameters via
    `params_json` rather than interpolating them into the query string.

    Args:
        query:       Parameterized Cypher write statement.
        params_json: JSON object of parameter values for the query.

    Returns:
        JSON list of returned rows (often empty for pure MERGE writes), or a
        string beginning with 'ERROR:' if the write ultimately failed (e.g. a
        Cypher syntax error) so you can correct and retry.
    """
    params = json.loads(params_json) if params_json else {}
    try:
        rows = _run_write(query, params)
    except Exception as exc:  # noqa: BLE001 — surface to the agent, don't crash the run
        return f"ERROR: {type(exc).__name__}: {exc}"
    return json.dumps(rows, default=str)


@tool
def read_cypher(query: str, params_json: str = "{}") -> str:
    """Execute a read-only Cypher query and return rows as JSON (max 100 rows).

    For targeted verification reads only — the ontology schema is already in
    your system prompt, so do not use this to re-list EntityType / RelType.

    Args:
        query:       Parameterized read-only Cypher.
        params_json: JSON object of parameter values for the query.

    Returns:
        JSON list of result rows (truncated to the first 100).
    """
    params = json.loads(params_json) if params_json else {}
    rows = _run(query, params)
    if len(rows) > 100:
        rows = rows[:100] + [{"_warning": f"result truncated; {len(rows)} total rows"}]
    return json.dumps(rows, default=str)


@tool
def describe_ontology(label: str) -> str:
    """Return the full description(s) for an ontology label.

    The schema embedded in your prompt shows only `short_description`s to stay
    compact. Call this when a short description isn't enough to tell two similar
    EntityTypes or relationships apart, or before reusing a type you're unsure
    about.

    Args:
        label: An EntityType `entityLabel` (e.g. 'Obligation') or a RelType
               `relLabel` (e.g. 'GOVERNS').

    Returns:
        JSON with the matching EntityType's `full_description` (if any) and the
        `full_description` of each RelType edge using that relLabel (if any).
    """
    entity_type = _run(
        "MATCH (e:EntityType {entityLabel: $l}) "
        "RETURN e.entityLabel AS entityLabel, e.full_description AS full_description",
        {"l": label},
    )
    relationships = _run(
        "MATCH (a:EntityType)-[r:RelType {relLabel: $l}]->(b:EntityType) "
        "RETURN a.entityLabel AS `from`, r.relLabel AS relLabel, "
        "b.entityLabel AS `to`, r.full_description AS full_description",
        {"l": label},
    )
    return json.dumps(
        {"entity_type": entity_type, "relationships": relationships}, default=str
    )


@tool
def get_ontology_summary() -> str:
    """Return a summary of the current ontology in the graph.

    Returns:
        JSON with keys: classes, relationship_types, total_nodes, total_relationships.
    """
    classes = _run(
        "CALL db.labels() YIELD label "
        "RETURN label ORDER BY label"
    )
    rel_types = _run(
        "CALL db.relationshipTypes() YIELD relationshipType "
        "RETURN relationshipType AS type ORDER BY type"
    )
    totals = _run(
        "MATCH (n) WITH count(n) AS nodes "
        "OPTIONAL MATCH ()-[r]->() RETURN nodes, count(r) AS rels"
    )
    return json.dumps(
        {
            "classes": [r["label"] for r in classes],
            "relationship_types": [r["type"] for r in rel_types],
            "total_nodes": totals[0]["nodes"] if totals else 0,
            "total_relationships": totals[0]["rels"] if totals else 0,
        },
        indent=2,
    )
