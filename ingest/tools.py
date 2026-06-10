"""Shared tools for both agents in the multi-agent ontology builder."""

from __future__ import annotations

import json

from strands import tool

from shared.neo4j_tools import _run


@tool
def get_ontology_schema() -> str:
    """Fetch the ontology schema from Neo4j (full descriptions).

    The query agent's mid-session schema-refresh tool: returns all EntityType
    nodes and the RelType edges connecting them, each with its full
    `description`. Query-facing only — it always returns full text (not gated by
    ONTOLOGY_COMPACT_SNAPSHOT) so a refresh matches the full-description schema
    embedded in the query prompt. The ingest per-chunk loops use their own
    fetchers, which honour the compact flag.

    Returns:
        JSON with keys:
          entity_types  - list of {entityLabel, description}
          relationships - list of {from_entityLabel, relLabel, to_entityLabel, description}
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
    subclass_of = _run(
        """
        MATCH (child:EntityType)-[:SUBCLASS_OF]->(parent:EntityType)
        RETURN child.entityLabel AS child, parent.entityLabel AS parent
        ORDER BY child.entityLabel
        """
    )
    same_as = _run(
        """
        MATCH (a:EntityType)-[:SAME_AS]->(b:EntityType)
        RETURN a.entityLabel AS a, b.entityLabel AS b
        ORDER BY a.entityLabel
        """
    )
    return json.dumps(
        {
            "entity_types": entity_types,
            "relationships": rels,
            "subclass_of": subclass_of,
            "same_as": same_as,
        },
        indent=2,
    )


@tool
def create_document_node(name: str, path: str) -> str:
    """Create or merge a Document instance node.

    Args:
        name: Display name of the document (typically the filename).
        path: Absolute or relative file path.

    Returns:
        elementId of the Document node.
    """
    rows = _run(
        "MERGE (d:Document {path: $path}) SET d.name = $name RETURN elementId(d) AS id",
        {"name": name, "path": path},
    )
    return rows[0]["id"] if rows else ""


@tool
def create_chunk_node(doc_id: str, chunk_index: int, text: str) -> str:
    """Create a Chunk node and connect it to its Document via HAS_CHUNK.

    Args:
        doc_id:      elementId of the parent Document node.
        chunk_index: Zero-based index of this chunk within the document.
        text:        The chunk text content.

    Returns:
        elementId of the Chunk node.
    """
    rows = _run(
        """
        MATCH (d:Document) WHERE elementId(d) = $doc_id
        MERGE (c:Chunk {doc_id: $doc_id, chunk_index: $chunk_index})
        SET c.text = $text
        MERGE (d)-[:HAS_CHUNK]->(c)
        RETURN elementId(c) AS id
        """,
        {"doc_id": doc_id, "chunk_index": chunk_index, "text": text},
    )
    return rows[0]["id"] if rows else ""


@tool
def get_chunk_id(doc_path: str, chunk_index: int) -> str:
    """Look up the elementId of an existing Chunk node by document path and index.

    Args:
        doc_path:    Path of the parent document.
        chunk_index: Zero-based chunk index.

    Returns:
        elementId of the Chunk node, or empty string if not found.
    """
    rows = _run(
        """
        MATCH (d:Document {path: $path})-[:HAS_CHUNK]->(c:Chunk {chunk_index: $chunk_index})
        RETURN elementId(c) AS id
        """,
        {"path": doc_path, "chunk_index": chunk_index},
    )
    return rows[0]["id"] if rows else ""
