"""Evaluation question set for the query A/B harness.

The 15 FLTCA questions used for the pilot assessment, spanning factual
retrieval, aggregation, normative structure, process chains, and
exhaustiveness tests. All questions are answerable from the source
documents — none require knowledge of the graph schema or traversal paths.
Swap or extend this list for other corpora.
"""

from __future__ import annotations

FLTCA_QUESTIONS: list[str] = [
    "What sanctions can result from a failed inspection, and who can issue them?",
    "What processes must a Licensee participate in before a licence can be revoked?",
    "Which roles have duties that only apply after a specific event or process has occurred? Give examples from the Act.",
    "What rights are set out in the Residents' Bill of Rights, and how does the Act make them legally enforceable against a Licensee?",
    "What are all the grounds under which a licence can be suspended or revoked?",
    "List every type of document a Licensee is required to maintain.",
    "What are all the types of legal instruments referenced in this Act and its regulations, and who can issue each one?",
    "Who has supervisory or oversight powers over long-term care homes, and what is the source of each power?",
    "Show me the full normative structure for the Licensee — what obligations, "
    "prohibitions, and rights apply to them?",
    "What is the complete chain from a resident complaint to a sanction being issued?",
    "Under the Act, who bears direct legal obligations to individual residents — the Licensee or the Director — and what is the structural difference between their respective roles in ensuring resident welfare?",
    "What specifically is a Licensee required to report to the Director?",
    "What does the Act prohibit a Licensee from doing to a resident?",
    "Who is responsible for investigating a complaint about abuse?",
    "What standards must a long-term care home comply with?",
]
