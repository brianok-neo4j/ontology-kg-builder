"""Tools for the cypher_qa agent.

The agent already has access to:
- `get_ontology_schema` (imported from ingest.tools) —
  returns the EntityType/RelType ontology.
- `find_entities_by_name` (imported from shared.neo4j_tools) — case-insensitive
  CONTAINS match on `name` across all instance nodes.

The only new tool here is `run_read_cypher`, which the agent uses to execute
its generated query. Per the project's design choice, this tool does NOT
enforce read-only — the system prompt instructs the agent to keep queries
read-only, and we trust that.
"""

from __future__ import annotations

import json

from strands import tool

from shared.neo4j_tools import _run


@tool
def run_read_cypher(query: str, params_json: str = "{}") -> str:
    """Execute a Cypher query and return the rows as JSON.

    Use ONLY for read queries (MATCH/RETURN/WITH/UNWIND/OPTIONAL MATCH/CALL
    against read-only procedures). The system prompt forbids writes — do not
    issue CREATE, MERGE, SET, DELETE, REMOVE, DROP, or LOAD CSV.

    Args:
        query:       Parameterized Cypher to execute.
        params_json: JSON object of parameter values for the query.

    Returns:
        JSON list of result rows (each row is a dict of column → value).
        Limited to the first 100 rows to keep agent context manageable; if
        you need a count, use `RETURN count(*)`.
    """
    print(f"\n[Cypher]\n{query}\n")
    params = json.loads(params_json) if params_json else {}
    rows = _run(query, params)
    if len(rows) > 100:
        truncated = rows[:100]
        truncated.append({"_warning": f"result truncated; {len(rows)} total rows"})
        rows = truncated
    return json.dumps(rows, default=str)
