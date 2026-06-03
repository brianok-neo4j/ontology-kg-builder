"""Domain vocabulary definitions for the multi-agent ontology builder.

Each vocabulary seeds the Neo4j graph with a preferred set of EntityType nodes
before chunk processing begins. The ontology agent is instructed to default to
these types and only introduce new ones as SUBCLASS_OF an existing preferred type.

Adding a new domain: add an entry to VOCABULARIES with a unique slug, a
display_name, a when_to_use description (used by the auto-detector), and a
tuple of EntityTypeDef objects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityTypeDef:
    label: str
    description: str


@dataclass(frozen=True)
class DomainVocabulary:
    slug: str
    display_name: str
    when_to_use: str  # one-sentence hint used by the auto-detector
    entity_types: tuple[EntityTypeDef, ...]


VOCABULARIES: dict[str, DomainVocabulary] = {
    "pole": DomainVocabulary(
        slug="pole",
        display_name="Intelligence / Law Enforcement (POLE)",
        when_to_use="Intelligence reports, law enforcement case files, crime analysis, surveillance records, security investigations.",
        entity_types=(
            EntityTypeDef("Person", "An individual human being, identified by name, alias, or biometric attribute."),
            EntityTypeDef("Organisation", "A formal or informal group acting together: company, agency, gang, NGO, or government body."),
            EntityTypeDef("Location", "A physical place or geographic area: address, building, region, country, or GPS coordinate."),
            EntityTypeDef("Event", "A discrete occurrence at a point or span in time: meeting, incident, arrest, travel leg, or transaction."),
            EntityTypeDef("Object", "A physical or digital artifact: vehicle, device, document, weapon, currency, or contraband item."),
            EntityTypeDef("Communication", "A message or interaction between parties: phone call, email, social-media post, or letter."),
        ),
    ),

    "fraud": DomainVocabulary(
        slug="fraud",
        display_name="Fraud / Financial Crime",
        when_to_use="Fraud investigation reports, AML case files, financial crime typologies, suspicious activity reports, sanctions documents.",
        entity_types=(
            EntityTypeDef("Person", "An individual involved in, facilitating, or victimised by financial activity."),
            EntityTypeDef("Organisation", "A company, institution, shell entity, or criminal network involved in financial flows."),
            EntityTypeDef("Account", "A financial account, wallet, or holding vehicle through which funds move."),
            EntityTypeDef("Transaction", "A movement of funds, assets, or value between accounts or parties."),
            EntityTypeDef("Asset", "A financial or physical item of value: property, security, cash, cryptocurrency, or commodity."),
            EntityTypeDef("Location", "A jurisdiction, address, or geographic area relevant to financial flows or incorporation."),
            EntityTypeDef("Event", "A significant financial occurrence: trade, transfer, filing, investigation, or sanction."),
            EntityTypeDef("Document", "A financial, legal, or identity record: invoice, contract, passport, certificate, or filing."),
        ),
    ),

    "legal": DomainVocabulary(
        slug="legal",
        display_name="Legal / Regulatory",
        when_to_use="Legislation, regulations, contracts, court decisions, compliance frameworks, government policy documents, licensing regimes.",
        entity_types=(
            EntityTypeDef("Party", "A person, organisation, or body with legal standing or obligations under the instrument."),
            EntityTypeDef("LegalInstrument", "A statute, regulation, contract, order, directive, or other document with legal effect."),
            EntityTypeDef("Obligation", "A duty or requirement imposed on a Party: must do, must provide, must report, must maintain."),
            EntityTypeDef("Right", "An entitlement or permission granted to a Party under the instrument."),
            EntityTypeDef("Prohibition", "A restriction, ban, or limit imposed on a Party."),
            EntityTypeDef("Role", "A defined function or capacity within a legal framework: Inspector, Operator, Licensee, Director."),
            EntityTypeDef("Process", "A regulated procedure or mechanism: Inspection, Licensing, Appeal, Review, Complaint."),
            EntityTypeDef("Sanction", "A penalty, fine, remedy, or enforcement action for non-compliance."),
            EntityTypeDef("Facility", "A physical premises, institution, or establishment subject to regulation."),
        ),
    ),

    "business": DomainVocabulary(
        slug="business",
        display_name="Business / Corporate",
        when_to_use="Annual reports, earnings calls, investor presentations, business plans, strategy documents, analyst reports, press releases.",
        entity_types=(
            EntityTypeDef("Person", "An individual such as an executive, employee, director, or shareholder."),
            EntityTypeDef("Organisation", "A company, subsidiary, partner, competitor, regulator, or trade body."),
            EntityTypeDef("Product", "A good, service, platform, or offering brought to market."),
            EntityTypeDef("Market", "A customer segment, geography, industry vertical, or competitive space."),
            EntityTypeDef("FinancialMetric", "A quantitative measure of financial performance or position: revenue, margin, headcount, debt."),
            EntityTypeDef("Event", "A significant business occurrence: acquisition, product launch, partnership, IPO, bankruptcy."),
            EntityTypeDef("Strategy", "A plan, initiative, objective, or competitive positioning described in the document."),
            EntityTypeDef("Risk", "An uncertainty, threat, or liability facing the business or its stakeholders."),
        ),
    ),

    "scientific": DomainVocabulary(
        slug="scientific",
        display_name="Scientific Research",
        when_to_use="Academic papers, research reports, systematic reviews, conference proceedings, scientific grant applications.",
        entity_types=(
            EntityTypeDef("Study", "An experiment, clinical trial, observational study, simulation, or systematic analysis."),
            EntityTypeDef("Finding", "A result, conclusion, observation, or reported outcome produced by a study."),
            EntityTypeDef("Subject", "The entity being studied: organism, material, system, population, compound, or phenomenon."),
            EntityTypeDef("Method", "A technique, protocol, instrument, algorithm, or computational approach used in research."),
            EntityTypeDef("Measurement", "A quantitative or qualitative variable, metric, or outcome measure collected in a study."),
            EntityTypeDef("Researcher", "An individual scientist, author, clinician-investigator, or co-investigator."),
            EntityTypeDef("Institution", "A university, research lab, hospital, funding body, or scientific organisation."),
            EntityTypeDef("Concept", "A scientific theory, hypothesis, phenomenon, construct, or domain-specific idea."),
            EntityTypeDef("Dataset", "A collection of data used as evidence, training material, or for analysis."),
        ),
    ),

    "patent": DomainVocabulary(
        slug="patent",
        display_name="Patents / Intellectual Property",
        when_to_use="Patent applications, granted patents, IP landscape analyses, freedom-to-operate reports, patent prosecution documents.",
        entity_types=(
            EntityTypeDef("Inventor", "An individual credited with conceiving the invention."),
            EntityTypeDef("Assignee", "The organisation or individual to whom patent rights are assigned or licensed."),
            EntityTypeDef("Claim", "A discrete assertion defining the scope of protection sought or granted."),
            EntityTypeDef("Embodiment", "A specific implementation or realisation of one or more claims described in the patent."),
            EntityTypeDef("Component", "A part, element, module, or sub-system described in the patent disclosure."),
            EntityTypeDef("Method", "A sequence of steps or process described as a claim or embodiment."),
            EntityTypeDef("Material", "A substance, compound, composition, or material with specified properties."),
            EntityTypeDef("PriorArt", "An existing publication, patent, product, or practice relevant to novelty or obviousness."),
        ),
    ),

    "medical": DomainVocabulary(
        slug="medical",
        display_name="Medical / Clinical",
        when_to_use="Clinical guidelines, medical case reports, drug labels, hospital protocols, public health documents, clinical trial reports.",
        entity_types=(
            EntityTypeDef("Patient", "An individual receiving, being evaluated for, or described in the context of healthcare."),
            EntityTypeDef("Clinician", "A healthcare professional: physician, nurse, pharmacist, therapist, or allied health worker."),
            EntityTypeDef("Condition", "A disease, disorder, syndrome, symptom, sign, or clinical finding."),
            EntityTypeDef("Intervention", "A drug, procedure, therapy, device, or behavioural treatment applied to a patient."),
            EntityTypeDef("Outcome", "A measured result of an intervention or the natural course of a condition."),
            EntityTypeDef("Measurement", "A clinical or laboratory value: vital sign, lab result, imaging finding, score, or dose."),
            EntityTypeDef("Anatomy", "A body structure, organ, tissue, system, or anatomical site."),
            EntityTypeDef("Facility", "A hospital, clinic, pharmacy, laboratory, or other care or research setting."),
            EntityTypeDef("Study", "A clinical trial, cohort study, case series, meta-analysis, or clinical audit."),
        ),
    ),

    "financial": DomainVocabulary(
        slug="financial",
        display_name="Financial / Investment",
        when_to_use="Prospectuses, fund fact sheets, investment research, financial statements, credit ratings, derivatives documentation.",
        entity_types=(
            EntityTypeDef("Company", "A publicly or privately held business entity that issues or is the subject of securities."),
            EntityTypeDef("Security", "A financial instrument: stock, bond, option, ETF, derivative, or structured product."),
            EntityTypeDef("Transaction", "A trade, issuance, transfer, settlement, or redemption of a security or asset."),
            EntityTypeDef("Market", "A trading venue, index, sector, asset class, or geographic market."),
            EntityTypeDef("FinancialMetric", "A quantitative indicator of valuation, performance, or risk: EPS, P/E, beta, yield, NAV."),
            EntityTypeDef("Event", "A market-moving occurrence: earnings release, merger announcement, dividend, rate decision, default."),
            EntityTypeDef("Risk", "A factor or scenario that may adversely affect returns, valuation, or solvency."),
            EntityTypeDef("Fund", "An investment vehicle: mutual fund, hedge fund, ETF, pension fund, or private equity fund."),
        ),
    ),

    "supply_chain": DomainVocabulary(
        slug="supply_chain",
        display_name="Supply Chain / Logistics",
        when_to_use="Logistics contracts, shipping manifests, procurement documents, supplier audits, supply chain risk reports, trade compliance filings.",
        entity_types=(
            EntityTypeDef("Supplier", "An organisation that provides goods, materials, components, or services upstream."),
            EntityTypeDef("Customer", "An organisation or individual that receives goods or services downstream."),
            EntityTypeDef("Product", "A good, component, raw material, or SKU moving through the supply chain."),
            EntityTypeDef("Order", "A commercial request for goods or services: purchase order, sales order, or work order."),
            EntityTypeDef("Shipment", "A physical movement of goods between origin and destination."),
            EntityTypeDef("Facility", "A warehouse, factory, port, distribution centre, farm, or retail site."),
            EntityTypeDef("Carrier", "A logistics provider, freight forwarder, shipping line, or transport company."),
            EntityTypeDef("Contract", "An agreement governing supply terms, pricing, service levels, or obligations."),
            EntityTypeDef("Event", "A supply chain occurrence: delivery, delay, disruption, customs hold, inspection, or recall."),
        ),
    ),
}


def detect_domain(sample_text: str) -> str | None:
    """Classify the document domain from a text sample using claude-haiku.

    Returns a vocabulary slug from VOCABULARIES, or None if detection fails or
    the model returns an unrecognised slug.
    """
    try:
        import anthropic

        options = "\n".join(
            f"  {v.slug}: {v.when_to_use}" for v in VOCABULARIES.values()
        )
        prompt = (
            "You are classifying a document to select the best ontology vocabulary.\n\n"
            f"Available vocabularies:\n{options}\n\n"
            "Document excerpt:\n"
            "---\n"
            f"{sample_text[:2500]}\n"
            "---\n\n"
            "Reply with exactly one vocabulary slug from the list above and nothing else."
        )
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}],
        )
        slug = response.content[0].text.strip().lower()
        return slug if slug in VOCABULARIES else None
    except Exception as exc:
        print(f"  (domain auto-detection failed: {exc})")
        return None
