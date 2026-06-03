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
from typing import Any

from neo4j import GraphDatabase, Driver
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
