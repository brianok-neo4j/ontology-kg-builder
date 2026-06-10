"""Phase 1: generate reference answers and grading criteria for a question set.

Run once per (question set, corpus) combination. The output JSON is committed
and reused for all subsequent eval runs — the judge reads from it instead of
re-researching each time, making grading faster, cheaper, and consistent.

Usage (CLI via eval/main.py):
    python -m eval.main groundtruth faa/questions.py \
        --source faa/data/accidents/ \
        --out eval/groundtruth/faa.json

Or programmatically:
    from eval.groundtruth import generate_groundtruth
    from faa.questions import FAA_QUESTIONS
    generate_groundtruth(FAA_QUESTIONS, source="faa/data/accidents/",
                         out_path="eval/groundtruth/faa.json")
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from shared.strands_anthropic import CacheAwareAnthropicModel as AnthropicModel
from shared.strands_anthropic import cache_control
from eval.models import USAGE_KEYS, cost_of

RESEARCHER_MODEL = "claude-opus-4-8"
RESEARCHER_MAX_TOKENS = 8192

RESEARCHER_SYSTEM_PROMPT = """You are an expert analyst establishing authoritative reference
answers for an evaluation benchmark. Your job is to research each question thoroughly against
the source corpus, then produce:

1. A comprehensive reference answer — the best possible answer to the question given the
   available source material. This becomes the gold standard for grading.

2. Specific grading criteria — concrete, checkable requirements for each grade level, so a
   judge can apply them consistently across many candidate answers without re-researching.

3. Key facts — the specific facts, figures, entities, or patterns that a good answer must
   include to demonstrate that the system actually retrieved from the corpus.

## How to research

Use `search_source_document` to find relevant passages. For aggregation questions (e.g.
"what are the most common X"), search for multiple representative terms and synthesise
across passages — do not rely on a single passage. For questions about specific entities,
locate them directly.

## Output format

After your research, produce a fenced JSON block and nothing after it:

```json
{
  "reference_answer": "A thorough, complete answer in natural language...",
  "grading_criteria": {
    "excellent": [
      "Must identify X as the primary Y",
      "Must include at least Z specific examples",
      "Must distinguish between A and B"
    ],
    "good": [
      "Correctly covers the main elements",
      "May miss minor examples or secondary details",
      "Approximate figures acceptable"
    ],
    "partial": "One-sentence description of what a Partial answer looks like — main gaps or vagueness",
    "weak": "One-sentence description of what a Weak answer looks like — major failures or fabrications"
  },
  "key_facts": [
    "Specific fact 1 that must appear",
    "Specific fact 2 that must appear"
  ]
}
```

Write grading criteria that are concrete and checkable — a judge reading them should be able
to grade without looking at the source corpus again. Be specific: name the entities, figures,
and patterns that distinguish Excellent from Good.
"""


def _make_doc_tool(doc_text: str):
    lowered = doc_text.lower()

    @tool
    def search_source_document(query: str) -> str:
        """Search the source corpus for passages relevant to the query.

        For aggregation questions, call this multiple times with different terms
        to build a complete picture. Returns up to 8 matching excerpts.

        Args:
            query: A word or short phrase to search for (case-insensitive).
        """
        terms = [query.lower()]
        if query.lower() not in lowered:
            terms = [w for w in re.findall(r"[a-zA-Z]{4,}", query.lower())][:5] or terms

        hits: list[str] = []
        seen_spans: list[tuple[int, int]] = []
        for term in terms:
            start = 0
            while len(hits) < 8:
                i = lowered.find(term, start)
                if i < 0:
                    break
                a, b = max(0, i - 400), min(len(doc_text), i + len(term) + 400)
                if not any(a < e and s < b for s, e in seen_spans):
                    hits.append(doc_text[a:b].strip())
                    seen_spans.append((a, b))
                start = i + len(term)
            if len(hits) >= 8:
                break
        if not hits:
            return f"No passages found for {query!r}."
        return "\n\n--- passage ---\n\n".join(hits)

    return search_source_document


def build_researcher(doc_text: str, model_id: str | None = None) -> Agent:
    """Build the research agent with source-search capability."""
    effective_model = model_id or RESEARCHER_MODEL
    system_blocks = [
        {"type": "text", "text": RESEARCHER_SYSTEM_PROMPT, "cache_control": cache_control()},
    ]
    agent = Agent(
        model=AnthropicModel(
            model_id=effective_model,
            max_tokens=RESEARCHER_MAX_TOKENS,
            params={"system": system_blocks},
        ),
        system_prompt=RESEARCHER_SYSTEM_PROMPT,
        tools=[_make_doc_tool(doc_text)],
        conversation_manager=SlidingWindowConversationManager(
            window_size=20, should_truncate_results=True,
        ),
    )
    agent._model_id = effective_model
    agent._prev_usage = {k: 0 for k in USAGE_KEYS}
    return agent


_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_reference(text: str) -> dict:
    matches = _JSON_RE.findall(text)
    for raw in reversed(matches):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "reference_answer" in obj and "grading_criteria" in obj:
            return obj
    # Fallback: store the raw text so the groundtruth isn't lost
    return {
        "reference_answer": text.strip(),
        "grading_criteria": {"excellent": [], "good": [], "partial": "", "weak": ""},
        "key_facts": [],
        "_parse_error": "JSON block not found in researcher output",
    }


def load_source_text(source: str) -> str:
    """Load and concatenate all document chunks from source path."""
    from shared.document import load_documents
    chunks = [chunk for _, chunk, _ in load_documents(source)]
    if not chunks:
        raise ValueError(f"No supported documents found at: {source}")
    return "\n\n".join(chunks)


def generate_groundtruth(
    questions: list[str],
    source: str,
    out_path: str | Path,
    model_id: str | None = None,
    resume: bool = False,
) -> Path:
    """Research each question and write a groundtruth JSON file.

    Args:
        questions:  List of question strings.
        source:     Path to source document(s) — same path used for ingestion.
        out_path:   Where to write the groundtruth JSON.
        model_id:   Researcher model (default: claude-opus-4-8).
        resume:     If True and out_path exists, skip already-researched questions.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[int, dict] = {}
    if resume and out_path.exists():
        for entry in json.loads(out_path.read_text(encoding="utf-8")):
            existing[entry["index"]] = entry
        print(f"Resuming: {len(existing)} question(s) already researched.")

    print(f"Loading source corpus from {source}...")
    doc_text = load_source_text(source)
    print(f"  {len(doc_text):,} chars across source documents")

    effective_model = model_id or RESEARCHER_MODEL
    researcher = build_researcher(doc_text, effective_model)

    results: list[dict] = []
    total_cost = 0.0

    for i, question in enumerate(questions, 1):
        if i in existing:
            print(f"  Q{i}: skipping (already researched)")
            results.append(existing[i])
            continue

        print(f"\n  Q{i}/{len(questions)}: {question[:70]}...")
        researcher.messages = []
        t0 = time.time()

        try:
            result = researcher(
                f"## Question {i}\n{question}\n\n"
                "Research this question thoroughly using search_source_document, "
                "then produce the reference answer and grading criteria JSON."
            )
            raw_text = str(result)
            summary = result.metrics.get_summary() if result and result.metrics else {}
        except Exception as exc:  # noqa: BLE001
            print(f"    ! ERROR: {exc}")
            raw_text = f"ERROR: {exc}"
            summary = {}

        duration = time.time() - t0

        cum = summary.get("accumulated_usage", {}) or {}
        prev = getattr(researcher, "_prev_usage", {k: 0 for k in USAGE_KEYS})
        usage = {k: (cum.get(k, 0) or 0) - prev.get(k, 0) for k in USAGE_KEYS}
        researcher._prev_usage = {k: (cum.get(k, 0) or 0) for k in USAGE_KEYS}
        cost = cost_of(usage, effective_model)
        total_cost += cost

        parsed = _parse_reference(raw_text)
        entry = {
            "index": i,
            "question": question,
            **parsed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "research_model": effective_model,
            "source_path": str(source),
            "duration_s": round(duration, 2),
            "usage": usage,
            "cost": cost,
        }
        results.append(entry)

        # Write incrementally so a partial run is recoverable
        out_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"    done in {duration:.1f}s — ${cost:.4f}")

    print(f"\nTotal research cost: ${total_cost:.4f}")
    print(f"Groundtruth written to: {out_path}")
    return out_path


def load_groundtruth(path: str | Path) -> list[dict]:
    """Load a groundtruth JSON file and return the list of entries."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
