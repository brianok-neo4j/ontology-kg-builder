# FLTCA Eval Gap Analysis

**Run:** `query_ab_20260609T145840Z`  
**Judge mode:** Reference-based (stored groundtruth)  
**Scores:** 2E / 9G / 3P / 1W (prior live-judge run: 7E / 5G / 3P)

Gaps are grouped by root cause. Each entry names the question(s) affected, what the
groundtruth expected that the agent missed, and the most direct fix.

---

## Category 1 — Quantitative values not stored in the graph

The graph holds entity names and relationships but does not capture numeric
thresholds, dollar amounts, or time windows as node/edge properties. The agent
cannot retrieve what isn't there.

| Q | Missing value | Fix |
|---|---|---|
| Q1 | $25,000 (first-violation administrative penalty); $1,100–$11,000 range; $11,000 for key roles | Store as `Sanction.amount` or `Sanction.amount_detail` property during instance extraction |
| Q1 | Re-Inspection Fee (a distinct sanction type) | Add as a `Sanction` instance node linked to Inspector via `ISSUES` |
| Q1 | Court fine range ($4,000–$1,000,000) and imprisonment (up to 12 months) — agent cited different figures | Store as properties on the Conviction/Penalty Sanction node |
| Q2 | 28-day window for review request; 28-day window for appeal; 90-day hearing-start requirement | Add as `Process.deadline_days` or `Process.detail` properties |
| Q6 | Retention periods: 7-year, 10-year, 30-day, 1-year by document class | Add `Document.retention_period` property; or link each `Document` type to a `Requirement` carrying the period |
| Q10 | $25,000 first-violation penalty amount; $1,100–$11,000 range (duplicates Q1 gap) | Same fix as Q1 |
| Q12 | 3-business-day tier for significant health change/injury incidents | Add as `Process.deadline_days` on the relevant Incident Notification Process node |
| Q12 | 14-month Closure Plan requirement | Add as a `Document` instance with the timeline in its `detail` property |

**Root cause:** Instance extraction prompts instruct the agent to extract names and
relationships, but not to pull numeric values from the text into node properties.
The EntityType descriptions for `Sanction`, `Process`, and `Document` need property
specs that tell the extraction agent to capture amounts, deadlines, and retention
periods.

---

## Category 2 — Section/regulation references not stored in the graph

The groundtruth expects specific statutory section numbers linked to obligations,
sanctions, and processes. The agent either omits them or cites the wrong ones.

| Q | Missing reference | Fix |
|---|---|---|
| Q2 | s.169 (review of compliance order); ss.170–171 (appeal) — agent cited s.159/s.171 | Extract section numbers as a `section_ref` property on `Obligation`, `Process`, and `Sanction` nodes |
| Q5 | s.352 O. Reg. 246/22 transitional ground for suspension/revocation of licences under the former LTCHA 2007 | Add as a `Sanction` or `LegalInstrument` node with `source_section` = "s.352 O.Reg.246/22" |
| Q9 | s.286(4), s.289, ss.290–295 financial prohibition section references | Add `section_ref` properties to financial prohibition `Obligation`/`Sanction` nodes |
| Q13 | Same financial section references (s.289, s.286(4), ss.290–295) | Same fix as Q9 |
| Q10 | "Part X — Compliance and Enforcement" (Act structural reference) | Add as a `LegalInstrument` or `Document` node; or as a `part_ref` property on the relevant nodes |

**Root cause:** The instance agent extracts entity names but strips citation metadata.
Adding `section_ref` and `regulation_ref` to the EntityType descriptions for
`Obligation`, `Sanction`, `Process`, and `LegalInstrument` would prompt the agent
to preserve these during extraction.

---

## Category 3 — Missing instance nodes

Specific named entities the groundtruth expects are absent from the graph entirely —
either not extracted at all, or named differently and not merging.

| Q | Missing node | Type | Fix |
|---|---|---|---|
| Q1 | Minister (as a sanction issuer — triggers Licence Suspension) | Role | Ensure "Minister" is extracted as a `Role` node and linked via `TRIGGERS`/`ISSUES` to Suspension/Revocation sanctions |
| Q8 | Continuous Quality Improvement Committee (added by O. Reg. 246/22) | Role or InstitutionalActor | Add extraction pass over O. Reg. 246/22 specifically, or add as a named entity in the ontology description |
| Q9 | Financial prohibitions section (prohibition count ~100, not 760) | Obligation | Inflated count (760 vs ~100) suggests duplicate extraction; add deduplication or tighter MERGE criteria on Obligation names |
| Q10 | Management Order stage (Minister appoints a manager before revocation) | Process | Extract "Management Order" as a `Process` node linked `Minister TRIGGERS` → `Management Order` → `Licence Revocation` |
| Q12 | Annual Attestation (s.270(3)) | Document or Process | Add as a `Document` node with `section_ref` = "s.270(3)" linked to Licensee via `REQUIRED_TO_MAINTAIN` |
| Q15 | College of Nurses of Ontario Practice Standards and Guidelines | Standard | Ensure CNO Standards are extracted as a `Standard` node from the relevant regulatory text |
| Q15 | Director IPAC Standards and Protocols | Standard | Extract as a `Standard` node; currently only a generic IPAC Program is present |
| Q15 | Director Communicable Disease Surveillance Protocols | Standard | Extract as a named `Standard` node |
| Q15 | Manufacturer Specifications for Cleaning and Disinfection | Standard | Extract as a `Standard` node |
| Q15 | Written Program Description Standard | Standard | Extract as a `Standard` node |

**Root cause:** Two sub-problems. (a) Some entities are in the source documents but
the instance agent didn't extract them — likely because the chunk containing them
was ambiguous or the entity type wasn't prominent enough in the ontology description
to prompt extraction. (b) The inflated obligation count (760 vs ~100) is a
duplicate-extraction problem — slightly varied phrasings of the same obligation
create separate nodes because MERGE is on the full name string.

---

## Category 4 — Wrong ontology structure (Q7 — Weak)

**Question:** What are all the subtypes of LegalInstrument, and which parties can issue each?

**What the agent said:** Five subtypes — Policy, Notice, Record, Licence, Plan  
**What the groundtruth expects:** Two subtypes — Sanction and Standard  
**Grade: Weak** — none of the correct subtypes were identified; all issuer mappings were fabricated.

This is the most serious structural gap. Two possible causes:

1. **`SUBCLASS_OF` edges are wrong or missing.** The ontology layer should have
   `(Sanction)-[:SUBCLASS_OF]->(LegalInstrument)` and
   `(Standard)-[:SUBCLASS_OF]->(LegalInstrument)`. If those edges don't exist, or
   if other nodes (Policy, Notice, etc.) are incorrectly linked as subtypes, the
   agent will return the wrong answer regardless of query quality.

2. **The agent is inferring subtypes from property lookups rather than traversing
   `SUBCLASS_OF`.** If the query used name-matching or label inference instead of
   the `SUBCLASS_OF` relationship, it could return irrelevant nodes.

**Fix:** Run a direct Cypher check:
```cypher
MATCH (child)-[:SUBCLASS_OF]->(parent {entityLabel: 'LegalInstrument'})
RETURN child.entityLabel
```
If this returns Policy/Notice/Record/Licence/Plan instead of Sanction/Standard,
the ontology enhancer mis-wired the hierarchy and needs to be corrected via
`run_load_ontology` or a targeted `MERGE … SET` patch. If it returns nothing,
the `SUBCLASS_OF` edges are missing entirely and need to be added.

---

## Category 5 — Shortest-path query returns fabricated paths (Q11 — Partial)

**Question:** How is the Director connected to a Resident — shortest path of relationships?

**What the agent said:** Correct hop count (2) and correct structural observation
(Director connects through intermediary nodes, not directly). But the named
intermediary nodes don't match the reference: the agent cited unverified paths
while the groundtruth expects specific mediation through `Critical Incident
Immediate Notification Process`, `One Business Day Incident Notification Process`,
and `Accommodation Charge Reduction`.

This is the same fabrication risk identified in the FAA eval — the agent identifies
the pattern correctly but, when it can't verify specific examples from the graph,
generates plausible-sounding node names.

**Fix options:**
- The `run_read_cypher` result for the shortest-path query should be returning the
  actual intermediary node names. If those names are absent from the graph, the
  agent has nothing to anchor on and invents them. Ensuring the three reference
  intermediary nodes are present in the instance layer would allow the agent to
  retrieve and cite them correctly.
- The "every example must come from a query result" rule (already in the system
  prompt) should prevent this — but if the Cypher path query returns zero or sparse
  results, the agent falls back to reasoning rather than admitting the gap. A
  stricter rule: if `shortestPath` returns no rows, say so explicitly rather than
  proposing candidate paths.

---

## Category 6 — AC installation/removal prohibition missing (Q13)

**Question:** What does the Act prohibit a Licensee from doing to a resident?

The agent covered financial prohibitions and discharge situations but missed the
**AC installation/removal prohibition** — Licensees cannot force residents to
install or remove air conditioning units.

**Fix:** This is likely a missed extraction. The AC prohibition is a `Prohibition`
or `Obligation` node that wasn't created during instance extraction. Adding a
specific mention in the `Prohibition` EntityType description that AC-related
restrictions should be extracted would prompt the agent to pick it up.

---

## Category 7 — Missing notification obligation (Q14)

**Question:** Who is responsible for investigating a complaint about abuse?

The agent correctly identified the Licensee as primary investigator and the
Director as oversight recipient. It missed the obligation to **notify the Resident
and Substitute Decision-Maker of investigation results**.

**Fix:** This is an extracted `Obligation` node that links `Licensee` → `APPLIES_TO`
→ `Resident` and `SubstituteDecisionMaker` via a notification obligation. Either
the node wasn't extracted or the relationship wasn't created. Checking for a
`Notification` or `Report` obligation node linked to the investigation process would
confirm whether it's a data gap or a query gap.

---

## Summary: Fix priority

| Priority | Fix | Questions unblocked | Effort |
|---|---|---|---|
| 1 | Add `section_ref` property to `Obligation`, `Sanction`, `Process` EntityType descriptions; re-extract | Q2, Q5, Q9, Q10, Q13 (5→Excellent) | Medium — ontology description change + re-extract |
| 2 | Fix `SUBCLASS_OF` hierarchy for LegalInstrument → verify/patch Sanction and Standard as the two subtypes | Q7 (Weak→Excellent) | Low — targeted Cypher patch, no re-extract needed |
| 3 | Add `amount`/`amount_detail` to `Sanction` and `deadline_days`/`detail` to `Process` descriptions; re-extract | Q1, Q2, Q10, Q12 (Partial/Good→Excellent) | Medium — ontology description change + re-extract |
| 4 | Ensure missing Standard nodes are extracted (CNO, Director IPAC, Surveillance, Manufacturer Specs, Written Program Description) | Q15 (Partial→Good/Excellent) | Medium — may need targeted instance re-run over specific chunks |
| 5 | Add missing instance nodes: Minister→Sanctions, CQI Committee, Management Order, Annual Attestation, Re-Inspection Fee | Q1, Q8, Q10, Q12 | Medium — instance re-extract or targeted load-ontology patch |
| 6 | Fix inflated obligation count (760 vs ~100) — deduplicate or tighten MERGE criteria | Q9 | Low — post-processing Cypher dedup pass |
| 7 | Add `retention_period` to `Document` descriptions | Q6 (Good→Excellent) | Medium — ontology description + re-extract |
| 8 | Ensure AC prohibition and Resident/SDM notification obligation are extracted | Q13, Q14 (Good→Excellent) | Low — targeted instance re-run or manual node patch |
