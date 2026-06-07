# Retrieval-Quality Investigation — FLTCA Pilot

**Date:** 2026-06-05 → 06  **Branch:** `recall-recovery` (off `main`)
**Scope:** Why a newer graph build retrieved *worse* than an older one, and what
actually drives answer quality. All evals: the 15 FLTCA questions
(`eval/questions.py`), Sonnet answers, Opus judge grounded on the corpus + web,
graded Excellent / Good / Partial / Weak.

> **Headline:** The session's ontology-hygiene + cost work (generalization rules,
> entity-type cap, enhancer, compact snapshot) **traded away retrieval quality.**
> The single clean win recovered here is using **full ontology descriptions in
> the query prompt**. Pushing extraction *recall* back up did **not** restore
> quality — and an older, simpler, un-enhanced ontology still out-retrieves the
> newer one, which points the next investigation at **ontology design**, not
> recall or entity resolution.

---

## Configurations compared

| Config | Graph (AuraDB) | Ontology | Density (nodes/chunk) | Query prompt | ≥Good /15 | E/G/P/W |
|---|---|---|---|---|---|---|
| **beta** | `23e61d7d` | 19-type, **un-enhanced** (0 `SUBCLASS_OF`), has `Concept` catch-all | 12.3 | full* | **13** | 7/6/1/1 |
| **0.1 (compact)** | `4be25128` | 31-type, enhanced, generalized, capped | 8.0 | compact | 9 | 5/4/5/1 |
| **fulldesc** | `4be25128` rebuilt | 31-type (same as 0.1) | 7.8 | **full** | 11 | 6/5/2/2 |
| **relaxed** | `4be25128` rebuilt | 31-type (same as 0.1) | 11.4 | full | 10 | 4/6/5/0 |

\* beta's `description` was backfilled to `short`/`full` so current code could
run against it; that gave its eval the *full*-description query prompt.

`beta` = the ~2026-06-04 pipeline (git tag `beta` = `645484a`); `0.1` = git tag
`0.1` = `1fd4fba` (current `main`). Note: the pilot write-up
(`analysis_writeup.md`) describes a *different*, **enhanced** 17-type beta
(9 `SUBCLASS_OF`) scoring 10E/4G/1P by manual assessment — so two independent
methods (manual + this harness) both rate the beta *lineage* above the current
one.

---

## Experiments, in order

1. **Baseline eval of `0.1`** → 5E/4G/5P/1W (9 ≥Good). Several completeness
   questions (Q3/Q6/Q7) only Partial/Weak.

2. **Eval of prior `beta` graph** → 7E/6G/1P/1W (13 ≥Good). Prior won decisively,
   especially the "list every / all subtypes / full structure" questions.

3. **Extraction-gap analysis.** `beta` extracted **12.3 nodes/chunk + 28.4
   rels/chunk** vs `0.1`'s **8.0 + 16.7** — −35% nodes, −41% rels from the same
   451 chunks, with `beta`'s exact-dup ratio 1.01× (genuine content, not dup
   inflation). Hypotheses: (A) the instance prompt's compact `short_description`;
   (B) the leaner ontology (no `Concept` catch-all; far fewer `LegalInstrument`
   245 vs 867, `Right` 118 vs 237).

4. **Cause A test** — rebuilt `0.1`'s instance layer with
   `ONTOLOGY_COMPACT_SNAPSHOT=0` (full descriptions in the *instance* prompt),
   ontology held constant. Density barely moved (8.0 → 7.8) at +$5.6. **Cause A
   refuted** — instance-prompt description richness is not the lever.

5. **Cause B mechanism** — git diff `beta`→HEAD of the *ontology* agent shows the
   over-fragmentation fix: *"An EntityType is a CATEGORY… never one specific
   thing"*, the forbidden `<Specific><Category>` pattern, and
   `--max-entity-types`. These produced the lean, general schema with no
   high-capacity buckets.

6. **Lever #1 — full descriptions in the *query* prompt** (commit `ad9d2de`).
   Decoupled the query agent from the ingest `ONTOLOGY_COMPACT_SNAPSHOT` flag so
   it always embeds `full_description` (cheap — embedded once per question, not
   per chunk). `0.1`(9) → fulldesc(11). **Clean win; keep.**

7. **Lever #2 — "extract thoroughly" instance prompt** (commit `9b5d280`).
   Recovered density to ~beta (8.0 → 11.4 nodes/chunk, 16.7 → 26.7 rels/chunk;
   ~90–95% of the gap) and absorbed the `Concept` mass into *finer existing*
   types (`Definition` 218→342, `Criteria` 138→295, `LegalInstrument` 245→473) —
   so a re-added catch-all is **not** needed. **But grades did not follow**
   (fulldesc 11 → relaxed 10; Excellents 6→4), though it eliminated all Weak.

---

## Findings

1. **Query-prompt richness is a real, cheap lever.** Full type descriptions in
   the query prompt = +2 ≥Good for negligible cost. Committed (`ad9d2de`).

2. **Recall is *not* the bottleneck.** Recovering extraction density to beta
   levels did not recover grades — it slightly hurt them. **"Density drives
   quality" is refuted.** The judge consistently blamed **redundancy / duplicate
   surface-form variants / clutter / blending Act-and-regulation** and **missing
   specific numbered provisions** (e.g. s.3 Bill of Rights, s.21/s.22 standards)
   — not missing entities. More aggressive extraction added *noise*, not signal.

3. **Compact vs full descriptions in the *instance* prompt does not affect
   extraction volume.** (Cause A refuted.)

4. **The over-fragmentation fix had a quality cost.** The generalization rules +
   cap + enhancer that fixed the ~$700 schema blow-up also produced a schema that
   retrieves worse than the older, permissive one.

---

## The open question (most important): is the *ontology* the cause?

`beta` has **no entity resolution** and *also* has fragmentation (430 distinct
"Long-Term Care…" name variants), yet it beats every newer build. The `relaxed`
build matched beta's density and query prompt, also has no ER — and still scored
lower. With density, query-prompt, and (absent) ER all controlled, **the
remaining variable is the ontology itself.**

This *revises* the earlier "entity resolution is the bottleneck" read: ER would
help the `relaxed` build's clutter, but it cannot explain beta>relaxed, because
beta lacks ER too. The likely driver is **ontology design**:

- The current schema's **generalization** ("bare categories, never specifics")
  may be *too* general, losing useful mid-level distinctions.
- `beta`'s **`Concept`** type (formally-defined legal terms — "abuse",
  "consent", "incapable") is a *coherent, useful* retrieval bucket for legal QA.
  Splitting it into `Definition` / `Principle` / `Criteria` (fuzzy boundaries)
  may have **fragmented a useful concept space** and made extraction inconsistent.
- The **enhancer**'s consolidation / `SUBCLASS_OF` may have merged away useful
  types (though the beta graph we tested was itself un-enhanced and still won).

**Next experiment (recommended):** rebuild the ontology with the *reverted*
(beta-era) ontology-agent prompt — drop the generalization rules — then instance
+ eval, holding query-prompt (full) constant. This directly tests whether the
ontology changes, not recall or ER, cost us the quality.

**Caution / tension:** the generalization rules + cap fixed a real ~$700
over-fragmentation cost blow-up. A *full* revert risks bringing it back. Run the
test **controlled**: revert the generalization rules but keep `--max-entity-types`
as a guardrail, build Act-only, and measure ontology size + cost alongside
grades. Compare enhanced vs un-enhanced too.

If that confirms the ontology hypothesis, the real design goal becomes:
**recover beta's retrieval quality without its cost/fragmentation** — e.g. a
deliberately permissive-but-bounded schema that keeps a coherent `Concept`-like
type, rather than maximally-general categories.

---

## The experiment was run (2026-06-06): generalization rules *did* hurt — partly

Reverted the ontology-agent `## Generalization` section to beta-era wording
(commit `958343b`), kept `--max-entity-types 150` and everything else, full-wiped
`4be25128`, rebuilt ontology(Act-only) → enhance → instance → eval.

- **Reverted ontology = 19 types** (matches beta; *fewer* than `0.1`'s 31). So
  the "anti-fragmentation" rules were not reducing type count — they were pushing
  a *finer, different* carving. Enhancer added **0 `SUBCLASS_OF`** (beta-like).
  Density 11.2 nodes/chunk, 29.6 rels/chunk (≈beta). No `Concept` type.
- **Eval: 6E / 5G / 4P / 0W = 11 ≥Good.** The clean single-variable comparison is
  `relaxed`(10) → `REVERTED`(11): same instance + query prompts, only the ontology
  changed → **+1 ≥Good and +2 Excellent (4→6).** Reverting the generalization
  rules **recovered retrieval quality** — hypothesis directionally confirmed.
- **But it did not reach `beta` (13).** Generalization is *part* of beta's edge,
  not all. Residual candidates: beta's `Concept` catch-all (absent here), clutter
  from the relaxed instance prompt, or judge noise (11 vs 13 = 2 questions on N=15).

**Net (at the time):** `REVERTED` (6E/5G/4P/0W, 11 ≥Good) was the best clean build —
**but see the next section: reverting the generalization rules turned out to be
high-variance, and the better answer was to keep them ON and add a `Concept` type.**

---

## The `Concept` experiment (2026-06-06): the winner

Hypothesis: the residual gap to `beta` (11 vs 13) is `beta`'s `Concept` catch-all
type, which newer builds lacked. Added a coherent `Concept` to the `legal` seed
vocab (*"formally defined term, doctrine, or principle… 'consent', 'capacity',
'good faith'… for defined terms not fitting other types"*; generic, not
FLTCA-specific).

- **First attempt, on the rules-OFF base, blew up to 58 types** (over-fragmented:
  `ALCPatient`, `TemporaryLicence`, `FamilyCouncilAssistant`…) — vs 19 the prior
  rules-off run. **Key finding: reverting the generalization rules is
  HIGH-VARIANCE (19 ↔ 58); the rules provide stability, and the earlier "reverted"
  19-type win was partly luck.** Running the enhancer on it made it *worse* (62
  types — it added abstract parents without consolidating the children).
- **So: kept generalization rules ON + added `Concept`.** Stable **22 types**;
  `Concept` populated to **1,279 nodes** (≈beta's 1,159 — grab-bag contents, but it
  *helped*); density 12.1 nodes/chunk + 29.3 rels/chunk (≈beta).
- **Eval: 7E / 6G / 2P / 0W = 13 ≥Good — matched `beta` and beat its floor**
  (0 Weak vs beta's 1; fixed beta's one Weak, Q11 W→E). The best build of the whole
  investigation, and on the *stable, principled* config (rules ON, cost cap intact).

**Conclusion / recommended config:** **generalization rules ON + `Concept` seed +
full query descriptions + the ORIGINAL instance prompt** (see isolation test
below). Merge: `ad9d2de` (full query) + `213947f` (Concept seed); **keep the
rules ON** (do NOT merge the revert `958343b` — proven unstable) and **drop the
relaxed instance prompt** (`9b5d280` — see below).

### Isolation test: the relaxed instance prompt doesn't earn its keep

Ran the winning config with the **original** instance prompt instead of the
relaxed "extract thoroughly" one (gen ON + `Concept` + full query + original
instance; report `query_ab_20260606T210101Z`). Result **7E/5G/3P/0W = 12 ≥Good**
vs the relaxed build's 13. The two differ on 6 questions but **bidirectionally**
(relaxed wins Q1/Q10/Q11; original wins Q2/Q3/Q5) — judge noise, not signal.
Same 7 Excellent, same 0 Weak. But the relaxed prompt extracts ~30% more (12.1 vs
9.3 nodes/chunk) at **+22% cost.** So the relaxed instance prompt is noise-level
on quality and real on cost → **excluded from the recommended config.** This is a
direct A/B confirming the broader finding that recall *volume* is not the lever.

---

## Remaining gaps (CONCEPT vs beta): not just entity resolution

> **Note:** recorded *before* the relaxed-instance isolation test (gen ON +
> Concept + full query + *original* instance). Those findings may shift this.

Compared the winning `CONCEPT` build's judge rationales to `beta`'s on every
question where `CONCEPT` fell short of Excellent. The blockers are **mostly not
entity resolution** — ER is now a minor contributor.

| Q | CON/beta | Judge's complaint | Category |
|---|---|---|---|
| Q1 | G/G | omits the **$250,000 max penalty (s.158(3))** | Specificity |
| Q2 | P/G | **misframed sequencing** — revocation not effective until s.171 appeal window expires; beta captured s.169→170-171→174→175 | Process/relationship reasoning |
| Q3 | G/E | beta enumerated obligations across all roles **w/ section refs + timelines** | Coverage + specificity |
| Q4 | G/G | **overstated rights count** (~29 / 53 vs real 27) | Over-claiming (mild ER) |
| Q5 | G/E | beta listed **8 grounds verbatim w/ refs**, CON 6 | Coverage granularity + specificity |
| Q6 | G/E | beta gave **retention periods (7yr / 1yr)**; CON didn't | Specificity |
| Q7 | G/G | few `LegalInstrument` subtypes to map | Ontology subtype coverage |
| Q8 | P/P | `GOVERNS` **conflates "operates" vs "supervises"** → operators listed as supervisors; + KG noise | Edge semantics (+ clutter) |

**Prioritized levers:**

1. **Specificity (biggest).** Q1/Q3/Q5/Q6 lose Excellent purely for omitting
   concrete facts that ARE in the source — the $250k cap, 7yr/1yr retention
   periods, the 90-day timeline, verbatim section numbers. The graph captures
   the entities but strips the figures/citations. Fix: capture key figures/refs
   as node/edge properties at extraction time, and/or have the query agent quote
   `FROM_CHUNK` chunk text. Mostly query-side + extraction-property; **not ER.**
2. **Edge semantics.** Generic labels like `GOVERNS` conflate distinct meanings
   (operate vs supervise → Q8 category error) and miss conditional sequencing
   (Q2). The relationship-layer analogue of entity over-generalization. Fix:
   more discriminating relationship types / disciplined `detail`.
3. **Coverage granularity** (Q3/Q5/Q7) — beta enumerated grounds/obligations more
   completely; partly extraction thoroughness, partly ontology subtype richness.
4. **Entity resolution — secondary.** Only mild count-inflation (Q4) and some
   clutter (Q8). Real, but not what blocks the winning build from going higher.

**Bottom line:** the path from 13/15-with-6-Goods toward straight Excellents is
**specificity > edge-semantics > coverage > ER** — and specificity is largely a
query-side change.

---

## Caveats

- **N = 15, LLM-judge variance.** The grade *counts* (9 / 10 / 11 / 13) are
  within plausible noise of one another. The robust signals are **qualitative
  and cross-validated**: (a) the judge's repeated clutter/duplication/specificity
  complaints, and (b) the beta *lineage* scoring higher under **two independent
  methods** (manual pilot assessment 10E/4G/1P and this harness 7E/6G/1P/1W).
- Costs were also measured per run: `0.1` instance $32.45, fulldesc $38.02,
  relaxed $39.46 (+22% for the thorough-extraction prompt, ~all output tokens).

---

## Artifacts

- **Eval reports** (`eval/results/`, git-ignored): `query_ab_20260605T194431Z`
  (0.1), `…201646Z` (beta), `…213035Z` (fulldesc), `…20260606T015751Z` (relaxed).
- **Commits** (branch `recall-recovery`): `ad9d2de` (query full descriptions —
  keep), `9b5d280` (instance thorough-extraction — net-neutral, optional).
- **Tags:** `beta` = `645484a`, `0.1` = `1fd4fba`.
- **Live graph** at writing: AuraDB `4be25128` = the `relaxed` build (11.4
  nodes/chunk).
- Foundational cost/perf analysis: `performance_analysis.md`. Pilot baseline:
  `analysis_writeup.md`, `fltca_qa_assessment_v2.md`.
