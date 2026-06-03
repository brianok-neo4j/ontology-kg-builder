# FLTCA 2021 — cypher_qa Evaluation

Evaluation date: 2026-06-02  
Graph source: *Fixing Long-Term Care Act, 2021, S.O. 2021, c. 39, Sched. 1* (Ontario)  
Assessment method: answers compared against Ontario government sources, BLG legal analysis, CLEO guides, and HSARB materials.

Ratings: **Excellent** / **Good** / **Partial** / **Weak**

---

## Category 1 — Multi-hop traversal

### Q1: What sanctions can result from a failed inspection, and who can issue them?

**Rating: Excellent**

The answer correctly identifies the tiered sanctions ladder — Written Notification (s.154), Compliance Orders (s.155), Notice of Administrative Penalty (s.158), Funding Orders (s.156), Management Orders (s.157), and Licence Suspension/Revocation (s.159) — and correctly assigns issuance authority to Inspector, Director, and Minister. The escalation trigger from Inspector referral to Director action (s.154(1)(4)) is accurately modelled. The note that the Director holds the broadest sanctioning powers is correct.

**Minor gap:** The answer does not mention the specific monetary caps ($250,000 cap for administrative penalties; doubled criminal fines under the 2022 amendments), but those are in the regulations, not the statute text ingested.

---

### Q2: What processes must a Licensee participate in before a licence can be revoked?

**Rating: Good**

The answer correctly identifies the appeal process (ss.170–171), the Appeal Board (HSARB — referred to as "Appeal Board" in the Act), and the hearing obligations (s.174), including the 90-day hearing start requirement and the 7-day notice requirement. The key insight that the revocation order does not take legal effect until the appeal period expires is accurate. The Resident Relocation Process (s.159(5)(b)) is also correctly noted.

**Gaps:**
- The answer does not identify a formal "Notice of Proposal" or "show cause" step before the Director makes the revocation order itself. Under Ontario's *Statutory Powers Procedure Act* (SPPA), procedural fairness normally requires prior notice, but the graph did not find this as an explicit named process — which may reflect a modelling gap or the Act's structure.
- The web sources confirm suspension orders are immediately effective notwithstanding appeal rights — the answer implies appeals can stay the order, which is only partially true (s.25 of SPPA governs stays). The answer does flag this nuance but could be clearer.

---

### Q3: Which obligations does a Role have that are conditioned on a prior process completing?

**Rating: Excellent**

A genuine multi-hop traversal across the full graph. The answer correctly enumerates obligations for Director, Licensee, Minister, Placement Co-ordinator, Rights Adviser, and Staff, each with the conditioning process. The clustering by role is well-organised. The large Placement Co-ordinator cluster (gated on Eligibility Determination) and the Licensee restraint/confinement obligations (gated on reassessment processes) are both substantively correct.

**No significant gaps found** — this question tests graph connectivity rather than a single statutory provision, and the answer reflects a thorough traversal.

---

## Category 2 — Aggregation over the full graph

### Q4: What are all the rights that residents hold under this Act?

**Rating: Good, with a counting discrepancy**

The answer lists ~40 rights. Independent sources (CLEO, Ontario Association of Residents' Councils) place the Residents' Bill of Rights at **29 rights** in 5 categories. The discrepancy is explainable: the graph includes:
- Rights of *Residents' Council Members and Assistants* (entry/immunity rights) that are not in the Bill of Rights itself.
- The right to void a written agreement within 10 days (a contract right, not in the Bill of Rights).
- Some near-duplicate rights that appear as separate nodes in the graph (e.g., both "participate fully in development" and "participate fully in development and revision" of the plan of care).

The substantive rights are correct and well-categorised. The over-count is a graph modelling issue (scope too broad) rather than hallucination.

**Accuracy of substance:** High. All major categories are present and correctly described.

---

### Q5: List every type of document a Licensee is required to maintain.

**Rating: Excellent**

An impressively comprehensive answer. All major document categories are covered: Written Zero-Tolerance Policy (s.25), Restraint Minimization Policy (s.33), Plan of Care (s.6, ss.35–36), Emergency Plans (s.90), Attestation of Compliance (s.90(3)), Restraint/PASD Records (s.37), Health Records (s.193(2)(15)), Separate Accounts (s.95), Resident Trust Accounts (s.193(2)(18)), Survey Documentation (s.43(5)), Mission Statement (ss.4–5), Written Complaints Procedures (s.26), Financial Statements (s.193(2)(20)), and the posting/information package obligations (ss.84–85). The 3-year public availability requirement for published regulatory items (s.180(3)) is correctly noted.

**No significant gaps.** The section references are accurate throughout.

---

### Q6: What are all the grounds under which a licence can be suspended or revoked?

**Rating: Excellent**

The six grounds in s.159(2)(a)–(f) are enumerated correctly: non-compliance (a), false statements (b), fitness sub-criteria (c)(i)–(iii), security interest issues (d/e via ss.110–112), and controlling interest without approval (f). The escalation from suspension to revocation under s.159(6) ("cannot or will not properly operate") is correctly identified. The Minister's independent emergency power under s.161(1) ("imminently prejudicial to health, safety, or welfare of residents") is correctly distinguished from the Director's powers. The summary table is accurate.

---

## Category 3 — Hierarchy traversal

### Q7: What are all the subtypes of LegalInstrument, and which parties can issue each one?

**Rating: Good**

Correctly identifies **Sanction** and **Standard** as the two formal subtypes via `SUBCLASS_OF` relationships. The issuers of Sanctions (Inspector, Director, Minister as Roles; Superior Court of Justice as Court) are accurate. The observation that Standards are mandated by the Act/regulations rather than issued by a Role is correct and reflects how the Act works — the Act requires compliance with standards *set by regulation*, not standards issued by individual actors in the graph.

**Minor gap:** The answer identifies a single Prohibition instance as a subtype of LegalInstrument (the Prohibition on Convicted Persons on Governing Structure). This reflects a modelling artefact rather than a true statutory subtype relationship. Worth flagging as a potential schema quality issue.

---

### Q8: Which InstitutionalActors have supervisory powers over Facilities?

**Rating: Partial**

Technically correct within the ontology's `InstitutionalActor` taxonomy — **Board of Management** (an `AdministrativeBody`) governs municipal, territorial, and First Nations homes. But the answer likely misses the user's intent. The **Director** (the most important supervisory actor in the Act) is modelled as a `Role`, not an `InstitutionalActor`, and thus doesn't appear. Similarly the **Minister** and **Inspector** are `Role` nodes. The answer correctly adds these in the "Related Roles" table but buries the most important actors.

**Root cause:** The ontology models the Director, Minister, and Inspector as `Role` nodes rather than `InstitutionalActor` subtypes. For a question asking about supervisory powers, a human would expect these to feature prominently. The agent correctly explains this limitation.

---

### Q9: Show me the full normative structure — what Obligations, Prohibitions, and Rights apply to a given Role?

**Rating: Good** (assessed for Licensee)

The note: the agent initially asked for clarification on which Role to use, which is correct behaviour given the ambiguity. On re-run with "Licensee" specified, the answer is comprehensive: ~98 obligations, ~36 prohibitions, 9 rights. The obligations cover all major Act domains (care programs, restraint, admissions, compliance, finance). The prohibitions correctly cover abuse/neglect, restraint, consent, retaliation, and governance. The rights are correctly identified as procedural in nature (appeals, reviews, access).

**Difficulty assessing completeness** without reading the full statute. No obvious major omissions from cross-referencing the web sources.

---

## Category 4 — Path-finding between entities

### Q10: What is the complete chain from a resident complaint to a sanction being issued?

**Rating: Excellent**

This is the most impressive answer in the set. The 6-stage chain — Resident's Right to Complain → Mandatory Escalation to Director → Director Mandates Inspection → Inspector Conducts Inspection → Sanctions Issued (8-tier ladder) → Post-Sanction Consequences — is accurate and well-grounded. Every step is attributed to a specific statutory provision. The ASCII diagram at the end is a clean representation of the graph traversal. The mandatory nature of the Director's inspection obligation (s.29(1)) is correctly identified.

The answer took 29 Cypher queries to assemble but produced a result that would be extremely difficult to construct from traditional RAG.

---

### Q11: How is the Director connected to a Resident — what is the shortest path of relationships?

**Rating: Partial**

Technically correct — the graph does show 2-hop paths through the Accommodation Charge Reduction Application and the Residents Council. These are valid semantic paths. However, a human analyst would likely expect paths through more direct regulatory supervision relationships, such as:

- Director → GOVERNS → Long-Term Care Home → Facility where Resident lives
- Director → AUTHORISES → Inspection → Applies to Long-Term Care Home → involves Resident

The agent correctly filtered out provenance edges (`FROM_CHUNK`) but the resulting paths, while real, are not the most natural regulatory connections. This reveals a gap in how the graph models the Director's broad oversight relationship to the home and its residents.

**Verdict:** The answer is graph-accurate but not the most useful framing of the relationship.

---

## Category 5 — Detail/property questions

### Q12: What specifically is a Licensee required to report to the Director?

**Rating: Excellent**

Seven categories are correctly identified: Written Complaints (s.26), Investigation Results & Actions (s.27), Mandatory Suspicion Reporting (s.28), Regulatory Reports (s.91), Non-Arm's Length Transactions (s.96), Corporate Governance Changes (s.111), and Financial Statements (s.193(2)(20)). The section references are accurate. The mandatory reporting threshold (5 types of suspicion under s.28) is correctly enumerated.

**Minor gap:** The web sources add that PSW qualification records must be reported monthly (a regulatory requirement in O. Reg. 246/22, not the Act itself), which the graph doesn't capture — expected given the graph ingested only the statute.

---

### Q13: What does the Act prohibit a Licensee from doing to a resident?

**Rating: Excellent**

Comprehensive and well-organised: restraint/confinement prohibitions (8 specific types), abuse/neglect (2), consent (2), retaliation/whistleblowing (4), admission (2), and governance (6). The distinctions between the original restraint prohibition language and the substituted versions (on proclamation) are noted. The protection of residents from retaliation for whistleblowing (s.30(4)) is correctly identified.

---

## Category 6 — Stress tests

### Q14: Who is responsible for investigating a complaint about abuse?

**Rating: Good**

The answer correctly identifies the **Licensee** as the primary legally responsible party (obligation to ensure immediate investigation) and the **Director** as the oversight authority for mandatory reporting. This is substantively accurate.

**Nuance missed:** Web sources indicate that within the home, it is the **Director of Care (or designate)** who actually conducts the investigation. The graph models the Licensee as the obligated role (correct — the Licensee is the legal entity responsible) but does not surface the Director of Care as the operational actor. This is a modelling granularity issue rather than an error.

---

### Q15: What standards must a long-term care home comply with?

**Rating: Good**

The answer correctly identifies: Staffing and Care Standards (s.21), Program and Service Standards and Outcome Measures (s.22), IPAC Standards (s.23), regulatory standards under s.193(2)(14), and static/rolling incorporation by reference (ss.196(3)–(4)). The explanation of rolling incorporation is accurate and reflects a genuine complexity of the Act.

**Gap:** The answer does not mention the specific regulatory targets — **4 hours per resident per day** of direct care (nurses + PSWs) and **36 minutes per resident per day** of allied health care — which are the most-cited compliance standards in the Act's implementation. These are in O. Reg. 246/22 rather than the statute text, so their absence is expected given what the graph ingested.

---

## Overall Assessment

| Question | Rating |
|---|---|
| Q1 — Sanctions from inspection | Excellent |
| Q2 — Pre-revocation processes | Good |
| Q3 — Obligations conditioned on processes | Excellent |
| Q4 — All resident rights | Good |
| Q5 — Documents Licensee must maintain | Excellent |
| Q6 — Licence suspension/revocation grounds | Excellent |
| Q7 — LegalInstrument subtypes | Good |
| Q8 — InstitutionalActors with supervisory powers | Partial |
| Q9 — Full normative structure for Licensee | Good |
| Q10 — Complaint to sanction chain | Excellent |
| Q11 — Director to Resident shortest path | Partial |
| Q12 — Licensee reporting to Director | Excellent |
| Q13 — What Act prohibits Licensee from doing | Excellent |
| Q14 — Who investigates abuse complaints | Good |
| Q15 — Standards for long-term care homes | Good |

**Score: 8 Excellent, 5 Good, 2 Partial, 0 Weak**

---

## Recurring themes in gaps

**1. Regulations vs. the Act**
Most gaps arise from the graph only ingesting the *Act* text, not the subordinate regulations (O. Reg. 246/22 and its predecessors). Specific quantitative standards (4 hrs/day direct care, $250K penalty cap, monthly PSW records) live in the regulations and are absent from the graph.

**2. Role vs. InstitutionalActor taxonomy**
The Director, Minister, and Inspector are modelled as `Role` nodes, not `InstitutionalActor` subtypes. For questions phrased in terms of "institutional actors" or "supervisory authorities," the most important actors are in the wrong bucket.

**3. Path semantics**
The shortest-path question (Q11) reveals that graph paths through provenance/administrative edges can be technically valid but semantically misleading. A smarter retrieval_query filter on relationship types would improve this.

**4. Right counting**
The graph includes peripheral rights (Council members, contract voidability) alongside the core Residents' Bill of Rights, inflating the count from 29 to ~40. A `HAS_BILL_OF_RIGHTS_RIGHT` relationship type or a flag on Right nodes would resolve this.

**5. Notice/procedural fairness before sanctions**
The graph does not model the formal notice requirements (e.g., Notice of Proposal before revocation, or SPPA procedural rights) as explicit process nodes. This is a genuine gap in the ontology's coverage of pre-decision procedural steps.
