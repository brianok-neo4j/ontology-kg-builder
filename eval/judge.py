"""LLM judge for the query A/B harness.

Grades a query agent's answer against a rubric modelled on the pilot
assessment (Excellent / Good / Partial / Weak). The judge is a Strands agent
that establishes the correct, complete answer using three sources before
grading:

  1. the raw source document(s) — via a `search_source_document` tool;
  2. the web — via the community `http_request` tool (GET only);
  3. its own internal knowledge.

It is deliberately a separate, stronger model from the ones under test, so the
grade isn't biased toward whichever model produced the answer.
"""

from __future__ import annotations

import json
import os
import re

from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands_tools import http_request

from shared.strands_anthropic import CacheAwareAnthropicModel as AnthropicModel
from shared.strands_anthropic import cache_control
from eval.models import USAGE_KEYS, cost_of

# Strong, independent judge by default. Override per run with --judge-model.
JUDGE_MODEL = "claude-opus-4-8"
JUDGE_MAX_TOKENS = 4096

GRADES = ("Excellent", "Good", "Partial", "Weak")

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator grading answers produced by a
knowledge-graph question-answering system over a legal/regulatory corpus. Grade
how well each answer responds to its question, using the rubric below.

## Rubric (assign exactly one grade)

- **Excellent** — Fully and accurately answers the question. Captures all key
  elements with appropriate specifics (section references, figures, timelines,
  named roles/entities) wherever the source provides them. No material errors
  and no unsupported claims.
- **Good** — Accurate and largely complete. Captures the main elements but omits
  some specifics or secondary items. No material errors.
- **Partial** — Addresses the question but with significant gaps: misses major
  elements, stays vague where concrete specifics exist in the source, or mixes
  correct content with notable omissions.
- **Weak** — Fails to answer, is largely incomplete, or contains material
  inaccuracies or unsupported / hallucinated claims.

## How to grade

1. First establish the correct, complete answer yourself:
   - Use `search_source_document` to find the authoritative passages in the
     actual source corpus. Treat the source document as ground truth.
   - Use `http_request` (GET requests ONLY — never POST/PUT/DELETE) to consult
     authoritative web sources (e.g. official legislation/government sites) when
     the source document is insufficient or you want to corroborate.
   - Use your own knowledge to fill gaps and sanity-check, but defer to the
     source document and authoritative web sources over memory.
2. Compare the candidate answer against the correct answer you established.
3. Judge completeness AND accuracy. Under-extraction (missing real elements) and
   over-claiming (asserting things not supported by the source) both lower the
   grade. Reward specific, source-grounded detail.

## Output

Reason briefly, then end your reply with a single fenced JSON block and nothing
after it:

```json
{"grade": "Excellent|Good|Partial|Weak",
 "rationale": "2-4 sentences: what the answer got right, and what it missed or got wrong",
 "evidence": "the key source/web facts you used to judge"}
```
"""


def _make_doc_tool(doc_text: str):
    """A keyword search over the raw source document text, returned as a @tool."""
    lowered = doc_text.lower()

    @tool
    def search_source_document(query: str) -> str:
        """Search the raw source document for passages relevant to the query.

        Use this to check whether the candidate answer's claims are actually
        supported by the source text, and to find elements the answer may have
        missed. Returns up to 6 matching excerpts with surrounding context.

        Args:
            query: A word or short phrase to look for (case-insensitive).
        """
        terms = [query.lower()]
        # If the exact phrase isn't present, fall back to its significant words.
        if query.lower() not in lowered:
            terms = [w for w in re.findall(r"[a-zA-Z]{4,}", query.lower())][:5] or terms

        hits: list[str] = []
        seen_spans: list[tuple[int, int]] = []
        for term in terms:
            start = 0
            while len(hits) < 6:
                i = lowered.find(term, start)
                if i < 0:
                    break
                a, b = max(0, i - 300), min(len(doc_text), i + len(term) + 300)
                if not any(a < e and s < b for s, e in seen_spans):  # de-dupe overlaps
                    hits.append(doc_text[a:b].strip())
                    seen_spans.append((a, b))
                start = i + len(term)
            if len(hits) >= 6:
                break
        if not hits:
            return f"No passages found for {query!r} in the source document."
        return "\n\n--- passage ---\n\n".join(hits)

    return search_source_document


def build_judge(doc_text: str | None = None, model_id: str | None = None) -> Agent:
    """Build the judge agent. If doc_text is given, it gets a source-search tool."""
    # The judge only issues GET requests; allow http_request to run unattended.
    os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

    tools = [http_request]
    if doc_text:
        tools.append(_make_doc_tool(doc_text))

    system_blocks = [
        {"type": "text", "text": JUDGE_SYSTEM_PROMPT, "cache_control": cache_control()},
    ]
    effective_model = model_id or JUDGE_MODEL
    judge = Agent(
        model=AnthropicModel(
            model_id=effective_model,
            max_tokens=JUDGE_MAX_TOKENS,
            params={"system": system_blocks},
        ),
        system_prompt=JUDGE_SYSTEM_PROMPT,
        tools=tools,
        conversation_manager=SlidingWindowConversationManager(
            window_size=20, should_truncate_results=True,
        ),
    )
    # For per-question judge-cost accounting: the model id to price against and a
    # cumulative-usage baseline (accumulated_usage is cumulative across the many
    # gradings this one agent performs, so grade_answer diffs against it).
    judge._model_id = effective_model
    judge._prev_usage = {k: 0 for k in USAGE_KEYS}
    return judge


_VERDICT_RE = re.compile(r"\{[^{}]*\"grade\"[^{}]*\}", re.DOTALL)


def _parse_verdict(text: str) -> dict:
    """Extract the JSON verdict block from the judge's final reply."""
    # Prefer a fenced ```json block; fall back to the last grade-bearing object.
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = fenced or _VERDICT_RE.findall(text)
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        grade = str(obj.get("grade", "")).strip().capitalize()
        if grade in GRADES:
            return {
                "grade": grade,
                "rationale": str(obj.get("rationale", "")).strip(),
                "evidence": str(obj.get("evidence", "")).strip(),
            }
    return {"grade": "Unparsed", "rationale": text.strip()[-500:], "evidence": ""}


def grade_answer(judge: Agent, question: str, answer: str) -> dict:
    """Grade a single (question, answer) pair.

    Returns {grade, rationale, evidence, judge_usage, judge_cost} — the last two
    are this grading's token usage and dollar cost (diffed from the judge's
    cumulative usage and priced at the judge model), so per-question cost can
    include the cost of evaluating it.
    """
    judge.messages = []  # each grading is independent
    prompt = (
        f"## Question\n{question}\n\n"
        f"## Candidate answer to grade\n{answer}\n\n"
        "Establish the correct, complete answer using the source document, the "
        "web (GET only), and your knowledge, then grade the candidate answer "
        "against the rubric. End with the JSON verdict block."
    )
    try:
        result = judge(prompt)
        summary = result.metrics.get_summary() if result and result.metrics else {}
    except Exception as exc:  # noqa: BLE001 — a grading failure shouldn't kill the run
        return {"grade": "Error", "rationale": f"{type(exc).__name__}: {exc}",
                "evidence": "", "judge_usage": {}, "judge_cost": 0.0}

    # Diff this grading's usage from the judge's cumulative running total.
    cum = summary.get("accumulated_usage", {}) or {}
    prev = getattr(judge, "_prev_usage", {k: 0 for k in USAGE_KEYS})
    usage = {k: (cum.get(k, 0) or 0) - prev.get(k, 0) for k in USAGE_KEYS}
    judge._prev_usage = {k: (cum.get(k, 0) or 0) for k in USAGE_KEYS}
    model_id = getattr(judge, "_model_id", JUDGE_MODEL)

    verdict = _parse_verdict(str(result))
    verdict["judge_usage"] = usage
    verdict["judge_cost"] = cost_of(usage, model_id)
    return verdict
