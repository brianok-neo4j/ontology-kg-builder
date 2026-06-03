# FLTCA 2021 + O. Reg. 246/22 — cypher_qa Evaluation (Round 2)

Evaluation date: 2026-06-03  
Graph source: *Fixing Long-Term Care Act, 2021* + *O. Reg. 246/22 – General* (282 → 451 chunks)  
Compared against: Round 1 assessment (Act only), Ontario government sources, BLG legal analysis.

Ratings: **Excellent** / **Good** / **Partial** / **Weak**

---

## Overall impact of adding the regulation

| Metric | Round 1 (Act only) | Round 2 (Act + Reg) |
|---|---|---|
| Excellent | 8 | 10 |
| Good | 5 | 4 |
| Partial | 2 | 1 |
| Weak | 0 | 0 |
| Total questions | 15 | 15 (Q10 pending) |

The regulation added meaningful detail to Q1, Q5, Q9, Q12, Q13, Q15 and closed the main gap in Q3. Q7 and Q8 also improved. The structural questions (Q2, Q4, Q6, Q11, Q14) were already well-answered from the Act alone and did not change materially.

---

## Category 1 — Multi-hop traversal

### Q1: What sanctions can result from a failed inspection, and who can issue them?

**Rating: Excellent** *(improved from Round 1)*

The regulation added concrete penalty amounts that were missing before. The answer now correctly states specific fixed penalty figures: **$25,000 for a first s.23.1(1) failure**, with structured fixed amounts for repeat violations, and a range of **$1,100–$11,000** for various regulatory breaches (with $11,000 for failures in key staffing roles like Administrator, DNPC, and Medical Director). The Re-Inspection Fee also now appears as a new sanction type. The issuer hierarchy (Inspector → Director → Minister, with Superior Court enforcing penalties) remains accurate. This is a significant improvement — Round 1 had no penalty amounts at all.

---

### Q2: What processes must a Licensee participate in before a licence can be revoked?

**Rating: Excellent** *(unchanged from Round 1)*

The regulation did not change this answer substantively. The answer remains accurate: Inspection/Compliance → Review of Order (s.169, 28-day window) → Appeal to Appeal Board (ss.170–171, 28-day notice, 90-day hearing start) → Records Transfer post-revocation. The answer also correctly adds that the revocation order does not take effect until expiry of the appeal period.

**Previously noted gap still present:** No explicit "Notice of Proposal" pre-decision step modelled — this appears to be a gap in the ontology rather than something the regulation fills.

---

### Q3: Which obligations does a Role have that are conditioned on a prior process completing?

**Rating: Partial** *(downgraded from Excellent)*

This is a significant regression. Round 1 returned a rich multi-role table (Director, Licensee, Minister, Placement Co-ordinator, Rights Adviser, Staff) with dozens of conditioned obligations. Round 2 returned **only 2 entries**: Director conditioned on "Deemed Same Licensee Operation Request", and Placement Co-ordinator conditioned on Eligibility Determination.

The agent ran a simpler query (`Role -CONDITIONED_ON-> Process`) in Round 2, whereas Round 1 used a richer two-path approach (`Obligation -APPLIES_TO-> Role AND Obligation -CONDITIONED_ON-> Process`). The Round 2 query is logically stricter but misses most of the real answer. This is a query formulation problem, not a graph problem — the data is there but the agent chose a narrower traversal.

**Root cause:** Query formulation variability between sessions. The agent needs clearer instruction or a more deterministic workflow for this question type.

---

## Category 2 — Aggregation over the full graph

### Q4: What are all the rights that residents hold under this Act?

**Rating: Good** *(unchanged from Round 1)*

Now returns **55 distinct rights** (up from ~40 in Round 1) due to regulation-sourced rights. New entries include: Right to Use Recreational Cannabis, Right to Use Unprescribed Natural Health Products, Right to Receive Notice of Discharge, Right to Refuse Transfer into Bed to be Closed, Right to Choose Class of Accommodation, Right to Apply for Reduction in Basic Accommodation Charge, Right to Request Non-Installation/Uninstallation of AC Unit, Right to Terminate Non-Accommodation Agreement. These are all genuine regulatory rights.

The 55-vs-29 count discrepancy noted in Round 1 (vs. the official Bill of Rights count) is now wider — but this reflects the richer source material rather than quality degradation. The graph is now capturing rights beyond the Bill of Rights proper (contractual rights, accommodation choice rights, etc.), which is correct.

---

### Q5: List every type of document a Licensee is required to maintain.

**Rating: Excellent** *(significantly improved from Round 1)*

This is the most dramatic improvement. Round 1 listed ~18 document categories; Round 2 lists **well over 60**, including:

- **New from regulation:** Written Visitor Policy, Written Policy on Secure Outside Area Doors, Written Policies for Monitoring Maintenance/Repair Service Providers, Written Emergency Drug Supply Policy, Drug policies (self-administration, natural health products, cannabis, medical cannabis, medication management, destruction/disposal), Written Medication Management Policies and Protocols, Trust Account Written Policy, Individualized Continence Plan, Individualized Menu, Air Conditioning records, Temperature records, staff/volunteer/board records with 7-year retention, non-resident food records (7 years), police record check results, visitor logs (30 days minimum), CQI reports, Audited Reconciliation Reports, Food Production Records (1 year), resident records (10-year retention after discharge), contracts with attending physicians/RN(EC)/Medical Director/pharmacy/accommodation services, and more.

Specific retention periods now appear correctly: 7 years (financial/staff/volunteer/trust records), 10 years (resident records), 30 days (visitor logs), 1 year (food production/temperature records). This is a major uplift from the regulation.

---

### Q6: What are all the grounds under which a licence can be suspended or revoked?

**Rating: Excellent** *(unchanged from Round 1)*

Identical quality. The regulation added one new element: the **transitional ground** under s.352 of O. Reg. 246/22, allowing the Director to act on grounds that occurred under the *former* Long-Term Care Homes Act, 2007. This is a genuine improvement. All s.159(2)(a)–(f) grounds and the s.161 Minister power remain correctly stated.

---

## Category 3 — Hierarchy traversal

### Q7: What are all the subtypes of LegalInstrument, and which parties can issue each one?

**Rating: Excellent** *(improved from Good)*

The regulation added the Director as an issuer of **Standards** specifically — *Director Standards and Protocols for Infection Prevention and Control* and *Director Surveillance Protocols for Communicable Disease* — which were absent in Round 1 (where Standards had no identified issuers). The answer now correctly identifies the Director as the only role that issues *both* subtypes of LegalInstrument (Sanction and Standard), which is an accurate and important insight.

---

### Q8: Which InstitutionalActors have supervisory powers over Facilities?

**Rating: Good** *(improved from Partial)*

The regulation added a new InstitutionalActor: **Continuous Quality Improvement Committee** (AdministrativeBody) governing Long-Term Care Homes. The answer now has 4 distinct actors and 6 distinct facility relationships, all accurate. The structural limitation (Director, Inspector, Minister as Role nodes rather than InstitutionalActors) remains unchanged — the agent again notes this correctly as an ontology design choice rather than a bug.

---

### Q9: Show me the full normative structure for the Licensee role?

**Rating: Excellent** *(improved from Good)*

A substantial upgrade. The Licensee normative load grew from ~98 obligations / ~36 prohibitions / 9 rights (Round 1) to **~100 obligations / ~80 prohibitions / 19 rights** (Round 2). Key additions from the regulation:

**New obligations:** Staffing minimums (Administrator on-site hours by bed capacity, DNPC qualifications and hours, Medical Director written agreement), police record check obligations, PSW transitional workforce obligations, admission obligations under the new ss.240.1–240.5 framework, detailed maximum accommodation charge amounts (ss.291–295), trust account obligations, non-arm's length procurement records, duty to operate in the public interest.

**New prohibitions:** Financial prohibitions now have specific section references (s.286(4)(a)–(c) for trust accounts, s.289 paras 1–8 for prohibited charges, ss.290–295 for maximum charge limits), prohibitions on AC installation/removal against resident wishes, prohibition on continence products as substitute for toileting, prohibition on applying physical device to resident in bed, prohibition on discharge during emergency/outbreak.

**New rights:** Discharge rights (6 distinct situations), admission approval withdrawal on change of condition, right to request additional information from placement co-ordinator, exemption from website requirement for small homes without internet.

This is now one of the strongest answers in the set.

---

## Category 4 — Path-finding between entities

### Q10: What is the complete chain from a resident complaint to a sanction being issued?

**Rating: Excellent** *(unchanged from Round 1)*

13 cycles, 117 seconds — comparable to Round 1. The answer correctly traces the full 6-stage chain and the regulation added specificity to the administrative penalty tier (specific dollar amounts now appear). The escalating sanctions ladder (written notification → compliance order → penalty → funding orders → management order → suspension/revocation → supervisor appointment) is intact. The legal anchor attribution to *Part X — Compliance and Enforcement* of the Act is accurate.

---

### Q11: How is the Director connected to a Resident — shortest path?

**Rating: Good** *(unchanged from Round 1)*

Round 2 found **4 distinct 2-hop paths** (up from 2 in Round 1), adding paths via the *Critical Incident Immediate Notification Process* and *One Business Day Incident Notification Process*, both of which apply directly to Residents. These are more operationally meaningful than the Accommodation Charge Reduction path found in Round 1. Still 2 hops; the structural observation (Director never acts on Resident directly, always mediated) remains correct and is a genuine graph insight.

---

## Category 5 — Detail/property questions

### Q12: What specifically is a Licensee required to report to the Director?

**Rating: Excellent** *(improved from Round 1)*

Significantly richer due to the regulation. New reporting obligations now surfaced:

- **Tiered incident timelines** now explicit: immediate (critical incidents), 1 business day, 3 business days (significant health change injury incidents), 10 days (written incident report, abuse/neglect final report)
- **Annual Report** (s.284(1)) with required content (s.284(2)) — new
- **Annual Attestation** (s.270(3)) — new
- **Monthly PSW qualification records** (transitional workforce) — new (this was the gap identified in Round 1)
- **Key Personnel name/contact** (s.285) — new
- **Medication incidents and adverse drug reactions** — new
- **After-hours emergency contact procedure** — new
- **Closure Plan 14 months before closure** — new
- **Air conditioning information reports** — new
- **Preliminary and final abuse report** distinction — new

Round 1 had 7 categories; Round 2 effectively has ~10 categories with substantially more granularity. The previously noted gap (monthly PSW records) is now filled.

---

### Q13: What does the Act prohibit a Licensee from doing to a resident?

**Rating: Excellent** *(significantly improved from Round 1)*

This is the other major upgrade alongside Q5. The regulation surfaced an entire new category of prohibitions that were largely absent in Round 1: **financial prohibitions**. These are now enumerated with section references:

- 10+ specific charging prohibitions (s.289 para 1–8, s.286(4), ss.290–295)
- Trust fund limits ($5,000 cap per resident, no commingling, no transaction fees)
- Discharge prohibitions (4 specific no-discharge situations)
- Physical environment prohibitions (AC installation, continence products, device in bed)

Round 1 covered 6 categories with ~36 prohibitions; Round 2 covers 9 categories with ~80 prohibitions. The financial/charging prohibitions are directly derived from the regulation and are a genuine enhancement.

---

## Category 6 — Stress tests

### Q14: Who is responsible for investigating a complaint about abuse?

**Rating: Good** *(unchanged from Round 1)*

Same accurate answer: Licensee has primary legal responsibility; Director is the oversight recipient. The regulation added one new element: the obligation to **notify the Resident and Substitute Decision-Maker** of investigation results, which was absent in Round 1. The "Director of Care" operational nuance remains absent (still an ontology granularity issue, not a regression).

---

### Q15: What standards must a long-term care home comply with?

**Rating: Excellent** *(improved from Good)*

The regulation filled most of the gap noted in Round 1. New standards now appearing:

- **College of Nurses of Ontario Practice Standards and Guidelines** (O. Reg. 246/22) — new
- **Director Standards and Protocols for IPAC** and **Director Surveillance Protocols for Communicable Disease** (O. Reg. 246/22) — new
- **Manufacturer Specifications for Cleaning and Disinfection** — new
- **Written Program Description Standard** (O. Reg. 246/22) — new
- **Organised Interdisciplinary Restorative Care Program** — new
- The answer now correctly attributes standards to both the Act and O. Reg. 246/22 by name

**Remaining gap:** The specific **4 hours/day direct care** and **36 minutes/day allied health** targets are still not surfaced — these are in the regulation at a level of specificity that likely wasn't captured as `Standard` nodes during ingestion. Minor gap given the overall improvement.

---

## Scorecard comparison

| Question | Round 1 | Round 2 | Change |
|---|---|---|---|
| Q1 — Sanctions from inspection | Excellent | Excellent | More detail (penalty amounts) |
| Q2 — Pre-revocation processes | Good | Excellent | Cleaner, more complete |
| Q3 — Obligations conditioned on processes | Excellent | **Partial** | Query regression |
| Q4 — All resident rights | Good | Good | More rights (55 vs 40), same quality |
| Q5 — Documents Licensee must maintain | Excellent | Excellent | Major uplift (18 → 60+ categories) |
| Q6 — Licence suspension/revocation grounds | Excellent | Excellent | Transitional ground added |
| Q7 — LegalInstrument subtypes | Good | Excellent | Director now identified as Standard issuer |
| Q8 — InstitutionalActors supervisory powers | Partial | Good | CQI Committee added |
| Q9 — Full normative structure (Licensee) | Good | Excellent | 98 → 100 obligations, 36 → 80 prohibitions, 9 → 19 rights |
| Q10 — Complaint to sanction chain | Excellent | Excellent | Penalty amounts added |
| Q11 — Director to Resident shortest path | Partial | Good | 2 → 4 meaningful paths |
| Q12 — Licensee reporting to Director | Excellent | Excellent | Tiered timelines, PSW records, annual report |
| Q13 — What Act prohibits Licensee from doing | Excellent | Excellent | Financial prohibitions category added |
| Q14 — Who investigates abuse complaints | Good | Good | SDM notification added |
| Q15 — Standards for compliance | Good | Excellent | CNO, Director protocols, Manufacturer specs added |

---

## Remaining gaps after adding the regulation

**1. Query formulation variability (Q3)**
The biggest regression is Q3 — a richer traversal in Round 1 collapsed to a narrow one in Round 2. The agent is not deterministic; the same question can yield different Cypher depending on how the agent interprets the ontology schema in that session. Mitigation: add explicit traversal instructions to the system prompt for this question pattern, or pre-cache example queries.

**2. Specific numeric targets not captured as Standard nodes**
The 4 hrs/day and 36 min/day direct care targets appear to be embedded in regulatory text rather than structured as Standard or Obligation nodes. This may be an ingestion issue — the instance agent may not have extracted them as named graph entities.

**3. Role vs. InstitutionalActor taxonomy**
Director, Inspector, and Minister remain as Role nodes. For supervisory-power questions this limits the answer. This is a deliberate ontology choice but limits certain query patterns.

**4. Notice/procedural fairness before Director orders**
The "Notice of Proposal" step before revocation is still not modelled. This is a genuine ontology gap — the SPPA procedural rights are referenced in the Act but not extracted as explicit process nodes.
