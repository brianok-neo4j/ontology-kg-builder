"""Evaluation question set for the query A/B harness.

The 15 FLTCA questions used for the pilot assessment, spanning multi-hop
traversal, aggregation, hierarchy, path-finding, property retrieval, and
stress tests. Swap or extend this list for other corpora.
"""

from __future__ import annotations

FLTCA_QUESTIONS: list[str] = [
    "What sanctions can result from a failed inspection, and who can issue them?",
    "What processes must a Licensee participate in before a licence can be revoked?",
    "Which obligations does a Role have that are conditioned on a prior process completing?",
    "What are all the rights that residents hold under this Act?",
    "What are all the grounds under which a licence can be suspended or revoked?",
    "List every type of document a Licensee is required to maintain.",
    "What are all the subtypes of LegalInstrument, and which parties can issue each one?",
    "Which InstitutionalActors have supervisory powers over Facilities?",
    "Show me the full normative structure for the Licensee role — what Obligations, "
    "Prohibitions, and Rights apply to them?",
    "What is the complete chain from a resident complaint to a sanction being issued?",
    "How is the Director connected to a Resident — what is the shortest path of relationships?",
    "What specifically is a Licensee required to report to the Director?",
    "What does the Act prohibit a Licensee from doing to a resident?",
    "Who is responsible for investigating a complaint about abuse?",
    "What standards must a long-term care home comply with?",
]
