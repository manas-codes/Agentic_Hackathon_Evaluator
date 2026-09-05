"""Synthetic test submissions.

Fabricated fixtures for verifying the pipeline before real submissions arrive.
They deliberately span the quality range — one strong, evidenced submission, one
middling process redesign, one thin idea — so that scoring can be sanity-checked
for discrimination rather than just for "it ran".

Every sample is clearly marked as synthetic in its folder name and inside the
document. They are not real CAG submissions and contain no real personal data.
"""

from __future__ import annotations

from pathlib import Path

from .config import ensure_directories, load_config

SAMPLE_PREFIX = "ZZ-SAMPLE"

# ---------------------------------------------------------------------------
# Sample content. Structured as (field label, value) so the generated document
# mirrors the real Appendix-I form's layout closely enough to test the mapper.
# ---------------------------------------------------------------------------
SAMPLES: list[dict] = [
    {
        "folder": f"{SAMPLE_PREFIX}-01 Procurement Anomaly Detection",
        "quality": "strong",
        "fields": [
            ("Full Name", "A. Test Officer (SYNTHETIC SAMPLE — NOT A REAL SUBMISSION)"),
            ("Designation", "Senior Audit Officer"),
            ("Office", "O/o the Principal Accountant General (Audit-I), Karnataka"),
            ("Official Email ID", "sample.officer@example.gov.in"),
            ("Mobile", "9876500001"),
            ("1.1 Is this a team submission?", "Yes — submitting on behalf of a team"),
            ("1.2 Co-submitters",
             "B. Test Assistant | Assistant Audit Officer | O/o the PAG (Audit-I), Karnataka\n"
             "C. Test Analyst | Data Analyst | O/o the PAG (Audit-I), Karnataka"),
            ("2.1 Thematic area", "A — Audit Planning, Risk Identification and Execution Methodologies"),
            ("2.2 Title of your innovation",
             "Rule-and-model based anomaly detection for State PSU procurement audits"),
            ("2.3 What type of solution is this?", "Application / Software Tool; AI or ML Model / LLM"),
            ("2.4 Current stage of development", "Pilot Tested — tested in limited context"),
            ("2.5 What problem are you solving?",
             "Procurement transaction audit in State PSUs is presently sample-based. In the "
             "2024-25 cycle our wing examined 412 of an estimated 68,000 purchase orders "
             "across 11 PSUs — a coverage of roughly 0.6 per cent — because selection is "
             "manual and relies on the audit party physically reviewing files at the unit. "
             "The sampling is not risk-weighted: parties typically pick high-value orders, "
             "so structured splitting of orders just below delegated financial limits, "
             "repeat single-tender awards to the same vendor, and vendors sharing a bank "
             "account or address pass unexamined. Three of the four procurement paragraphs "
             "in our last Report were found by chance during file inspection rather than by "
             "design. The gap is in the selection step of the audit execution workflow: we "
             "have the ERP purchase data but no means of ranking it by risk before the party "
             "leaves for field audit."),
            ("2.6 What is your proposed solution and how does it work?",
             "A risk-scoring pipeline that ingests the PSU ERP purchase register and vendor "
             "master, applies 14 deterministic red-flag rules, and ranks every purchase "
             "order before the audit party is deployed. The rules encode known procurement "
             "risks: order values clustered within 5 per cent below a delegated financial "
             "power threshold; the same vendor winning more than 60 per cent of single-tender "
             "awards in a unit; vendors sharing bank account, PAN-shaped identifier, address "
             "or phone; tender periods shortened below the prescribed minimum; and "
             "post-award amendments raising value by more than 15 per cent. Each order gets "
             "a score and a plain-language reason string, so the auditor sees why an order "
             "surfaced rather than a bare number. On top of the rules an isolation-forest "
             "model flags statistical outliers on quantity-rate-vendor combinations, which "
             "catches patterns the rules do not encode; model output is advisory and never "
             "the sole basis for selection. Output is a ranked worksheet the party carries "
             "to field audit, and a small dashboard for the Group Officer. What is different "
             "from present practice is the sequence: risk ranking happens before deployment "
             "using data we already receive, instead of judgement applied to files after "
             "arrival. It does not replace the auditor's judgement — it changes what lands "
             "on the desk first."),
            ("2.7 Have you already piloted or tested this anywhere?",
             "Piloted on three State PSUs (data for FY 2023-24 and 2024-25) between "
             "January and March 2026 with the concurrence of the Group Officer. Of 240 "
             "orders flagged in the top risk decile, the audit party examined 96; 31 led to "
             "observations and 4 were included in the draft Report, including a "
             "split-purchase pattern worth Rs. 2.7 crore that sample-based selection had "
             "missed in two prior cycles. False positive rate on the examined set was about "
             "68 per cent, which we consider acceptable for a screening tool."),
            ("3.1 Technology / tech stack",
             "Python 3.11, pandas, scikit-learn (isolation forest), DuckDB for the "
             "transaction store, Streamlit for the review dashboard. Runs on a standard "
             "office desktop; no cloud dependency."),
            ("3.2 Is the technology proprietary, open-source, or a mix?", "Fully open-source"),
            ("3.3 How will the technology scale?",
             "The pipeline is columnar and processes the 68,000-order register in under two "
             "minutes on a desktop. It has been run against a synthetic 1.2 million row "
             "register in 9 minutes. The real bottleneck is not compute but data supply: "
             "each PSU's ERP exports differ in column naming, so onboarding a new PSU needs "
             "a mapping file — about half a day of work per unit. We have templated this for "
             "SAP and Tally exports, which covers 8 of our 11 PSUs."),
            ("3.4 Data privacy, security, or governance considerations",
             "Purchase registers contain vendor commercial data and are received under "
             "existing audit powers. Data stays on the office desktop; nothing is sent "
             "outside. Vendor bank account numbers are hashed on ingest — the tool needs to "
             "detect that two vendors share an account, not to display the account. No "
             "personal or citizen data is involved. Access is restricted to the audit party "
             "and the Group Officer."),
            ("4.1 Who within the CAG organization will benefit, and where can this be used?",
             "Directly applicable to every office conducting commercial audit of State PSUs "
             "and to Union-side PSU audit wings. The rule set is procurement-specific but "
             "the framework applies to any transaction register — we have identified "
             "scheme-expenditure audit and works audit as the next candidates. Estimated "
             "40-45 offices could use it as-is; the remainder would need rule adaptation."),
            ("4.2 Scalability", "Can be hosted/deployed/implemented centrally and be applicable "
             "across all CAG offices/or certain formations. A central build hosted by the IS "
             "Wing with per-office data mapping is the sensible deployment; the rule library "
             "should be centrally curated so improvements propagate."),
            ("4.2 Impact — Time Saved",
             "About 120 hours per PSU audit cycle | Selection previously took 3 auditors "
             "roughly 5 working days of file scanning per unit; the ranked worksheet reduces "
             "this to under half a day. Measured on the three pilot units."),
            ("4.2 Impact — Cost Reduction",
             "Not directly claimed | No cost head is reduced; the saving is auditor time "
             "redeployed to substantive examination rather than selection."),
            ("4.2 Impact — Error Rate Improvement",
             "Coverage-based, not error-based | Not measured. We can say selection is now "
             "risk-weighted; we cannot yet claim a reduction in missed observations."),
            ("4.2 Impact — Coverage Expanded",
             "0.6% to 100% screened, ~15% examined | All orders are now screened by rule; "
             "the examined proportion rose from 412 to about 1,400 orders per cycle in the "
             "pilot units because selection was faster."),
            ("4.2 Impact — Citizen / Stakeholder Benefit",
             "Stronger assurance on PSU procurement | Earlier detection of split purchases "
             "and vendor collusion patterns strengthens the assurance provided to the "
             "Legislature on public procurement."),
            ("4.2 Impact — Qualitative / Other Benefits",
             "31 additional observations in the pilot | Reason strings make the basis of "
             "selection auditable, which helped in responding to the auditee's objections "
             "about selection bias."),
            ("5. Rough one-time / setup cost", "Rs. 3-4 lakh (development already done in-house; "
             "cost is for central hardening, documentation and rule-library curation)"),
            ("5. Rough annual recurring cost", "Rs. 1.5 lakh/year (maintenance and rule updates)"),
            ("5.1 Monetary savings / ROI",
             "Roughly 120 auditor-hours saved per PSU cycle. Across 11 PSUs that is about "
             "1,320 hours a year in this office alone. We have not monetised this, since the "
             "hours are redeployed rather than saved as expenditure."),
            ("6.1 Primary submission", "A — Demo video or screen-recording (Demo.mp4, 7 minutes)"),
            ("6.2 Supporting document", "Technical note and rule catalogue (attached)"),
            ("7. Primary Submitter Name", "A. Test Officer"),
            ("7. Date of Submission", "10 / 08 / 2026"),
        ],
        "extra_files": {
            "Technical note and rule catalogue.docx": [
                "SYNTHETIC SAMPLE — supporting document",
                "Rule catalogue (14 rules)",
                "R1 Threshold clustering: order value within 5% below a delegated financial "
                "power limit. Rationale: structured splitting to avoid a higher sanction level.",
                "R2 Single-tender concentration: vendor share of single-tender awards in a "
                "unit exceeds 60% over 24 months.",
                "R3 Shared banking identity: two or more vendors sharing a hashed bank "
                "account identifier.",
                "R4 Shared contact identity: vendors sharing address or telephone.",
                "R5 Compressed tender period: bid submission window below the prescribed "
                "minimum for the value slab.",
                "R6 Post-award value escalation exceeding 15%.",
                "R7 Repeat emergency procurement by the same indenting officer.",
                "R8 Rate outlier against the unit's own 24-month median for the same item code.",
                "R9 Quantity outlier against consumption pattern.",
                "R10 Vendor registered within 90 days of first award.",
                "R11 Award to a vendor with prior performance penalty.",
                "R12 Sequential order numbers to the same vendor on the same date.",
                "R13 Purchase against a rate contract at above rate-contract price.",
                "R14 Item code substitution after award.",
                "Pilot results summary: 240 orders in top risk decile, 96 examined, 31 "
                "observations, 4 carried to draft Report. Split-purchase pattern of "
                "Rs. 2.7 crore detected in Unit B.",
            ],
        },
        "media_files": ["Demo.mp4"],
    },
    {
        "folder": f"{SAMPLE_PREFIX}-02 Digital File Movement Redesign",
        "quality": "medium",
        "fields": [
            ("Full Name", "D. Test Administrator (SYNTHETIC SAMPLE — NOT A REAL SUBMISSION)"),
            ("Designation", "Assistant Audit Officer (Administration)"),
            ("Office", "O/o the Accountant General (A&E), Odisha"),
            ("Official Email ID", "sample.admin@example.gov.in"),
            ("Mobile", "9876500002"),
            ("1.1 Is this a team submission?", "Yes"),
            ("1.2 Co-submitters", "E. Test Clerk | Senior Accountant | O/o the AG (A&E), Odisha"),
            ("2.1 Thematic area", "C — Institutional Process Improvements, Workforce and Capacity Building"),
            ("2.2 Title of your innovation", "Redesigned internal file movement and tracking process"),
            ("2.3 What type of solution is this?", "Process Redesign"),
            ("2.4 Current stage of development", "Partially Deployed — in use in one or few offices"),
            ("2.5 What problem are you solving?",
             "Physical files move between sections without a reliable record of where they "
             "are. Staff spend considerable time locating files, and pendency at a particular "
             "desk is not visible to the Branch Officer until a reminder is received. Delays "
             "in establishment matters such as pension sanction and leave cases result. There "
             "is an existing e-office facility but adoption in our office is partial and the "
             "physical file continues to be the working record."),
            ("2.6 What is your proposed solution and how does it work?",
             "We redesigned the movement process rather than adding software. Each file now "
             "carries a movement slip with a fixed set of stages, and each section maintains "
             "a single register instead of separate diaries. A weekly consolidated pendency "
             "statement is placed before the Branch Officer. Where a file crosses a stage "
             "time limit it is marked and the reason recorded. The change is procedural and "
             "was issued as an office order. We also simplified the number of stages in an "
             "establishment file from nine to six by removing duplicate checks that two "
             "different sections were both performing."),
            ("2.7 Have you already piloted or tested this anywhere?",
             "In use in the establishment section of this office since about mid-2025. "
             "Staff report that files are easier to locate. We have not measured this formally."),
            ("3.1 Technology / tech stack", "Not applicable — process change. Pendency statement "
             "is prepared in a spreadsheet."),
            ("3.2 Is the technology proprietary, open-source, or a mix?", "Not yet decided"),
            ("3.3 How will the technology scale?", "Not applicable."),
            ("3.4 Data privacy, security, or governance considerations",
             "Establishment files contain staff personal information. The register records "
             "only file number and stage, not contents."),
            ("4.1 Who within the CAG organization will benefit, and where can this be used?",
             "Administration and establishment sections in any office. The stage "
             "simplification is specific to establishment files but the movement discipline "
             "would apply anywhere files move between sections."),
            ("4.2 Scalability", "Will need regional/state-level/formation-level customisations "
             "to be deployed, since section structures differ between offices."),
            ("4.2 Impact — Time Saved",
             "Files located faster | Staff report less time searching. Not measured."),
            ("4.2 Impact — Cost Reduction", "Nil"),
            ("4.2 Impact — Error Rate Improvement",
             "Fewer files reported missing | Based on section feedback rather than a count."),
            ("4.2 Impact — Coverage Expanded", ""),
            ("4.2 Impact — Citizen / Stakeholder Benefit",
             "Faster pension and leave case disposal | Expected, not yet measured."),
            ("4.2 Impact — Qualitative / Other Benefits",
             "Pendency is visible to the Branch Officer weekly, which was not the case before."),
            ("5. Rough one-time / setup cost", "Minimal — printing of movement slips"),
            ("5. Rough annual recurring cost", "Negligible"),
            ("5.1 Monetary savings / ROI", "No monetary saving claimed."),
            ("6.1 Primary submission", "B — Process workflow / flow diagram"),
            ("6.2 Supporting document", "Not attached"),
            ("7. Primary Submitter Name", "D. Test Administrator"),
            ("7. Date of Submission", "18 / 08 / 2026"),
        ],
        "extra_files": {
            "Process workflow before and after.docx": [
                "SYNTHETIC SAMPLE — workflow description",
                "Before: Receipt -> Diarist -> Section clerk -> AAO check -> Section "
                "officer -> Second AAO check -> Branch Officer -> Return to section -> "
                "Despatch. Nine stages, two duplicate checks, no time limits.",
                "After: Receipt -> Section clerk -> AAO check -> Section officer -> "
                "Branch Officer -> Despatch. Six stages, stage time limits of 2 working "
                "days each, single register, weekly pendency statement.",
            ],
        },
        "media_files": [],
    },
    {
        "folder": f"{SAMPLE_PREFIX}-03 Audit Dashboard Idea",
        "quality": "weak",
        "fields": [
            ("Full Name", "F. Test Assistant (SYNTHETIC SAMPLE — NOT A REAL SUBMISSION)"),
            ("Designation", "Assistant Audit Officer"),
            ("Office", "O/o the Accountant General (Audit), Nagaland"),
            ("Official Email ID", "sample.assistant@example.gov.in"),
            ("Mobile", "9876500003"),
            ("1.1 Is this a team submission?", "No — solo submission"),
            ("2.1 Thematic area", "B — Business Process Re-engineering: Audit, Accounts and "
             "Institutional Products and Outputs"),
            ("2.2 Title of your innovation", "AI-powered dashboard for audit monitoring"),
            ("2.3 What type of solution is this?", "Dashboard / Visualisation Tool; AI or ML Model / LLM"),
            ("2.4 Current stage of development", "Concept / Idea — not yet built"),
            ("2.5 What problem are you solving?",
             "There is a lack of digitisation in audit monitoring. Officers do not have a "
             "single view of the status of audits. Data is scattered. A modern dashboard is "
             "needed to bring everything to one place and improve efficiency."),
            ("2.6 What is your proposed solution and how does it work?",
             "An AI-powered dashboard that shows all audit information in real time. It will "
             "use artificial intelligence and machine learning to give insights and "
             "predictions to management. It will have graphs and charts and can be accessed "
             "on mobile. This will make the department more efficient and modern and support "
             "data-driven decision making in line with Digital India."),
            ("2.7 Have you already piloted or tested this anywhere?", ""),
            ("3.1 Technology / tech stack", "AI, ML, Power BI, cloud"),
            ("3.2 Is the technology proprietary, open-source, or a mix?", "Not yet decided"),
            ("3.3 How will the technology scale?",
             "It will be scalable as it will be on cloud and can handle any number of users."),
            ("3.4 Data privacy, security, or governance considerations", "Data will be kept secure."),
            ("4.1 Who within the CAG organization will benefit, and where can this be used?",
             "All 130+ offices of the department will benefit. It can be used everywhere by "
             "all wings and all audit types."),
            ("4.2 Scalability", "Can be hosted/deployed/implemented centrally and be applicable "
             "across all CAG offices"),
            ("4.2 Impact — Time Saved", "50% time saving | Because everything will be automated."),
            ("4.2 Impact — Cost Reduction", "Substantial savings | Less manual work."),
            ("4.2 Impact — Error Rate Improvement", "Errors will reduce significantly |"),
            ("4.2 Impact — Coverage Expanded", "100% coverage |"),
            ("4.2 Impact — Citizen / Stakeholder Benefit", "Better governance |"),
            ("4.2 Impact — Qualitative / Other Benefits", "Modern image of the department."),
            ("5. Rough one-time / setup cost", "Minimal"),
            ("5. Rough annual recurring cost", "Minimal"),
            ("5.1 Monetary savings / ROI", ""),
            ("6.1 Primary submission", "C — Concept note with screenshots (not attached)"),
            ("6.2 Supporting document", ""),
            ("7. Primary Submitter Name", "F. Test Assistant"),
            ("7. Date of Submission", "29 / 08 / 2026"),
        ],
        "extra_files": {},
        "media_files": [],
    },
]

BANNER = (
    "SYNTHETIC TEST SUBMISSION — fabricated for pipeline testing. "
    "Not a real CAG submission. Contains no real personal data."
)


def _write_form_docx(path: Path, fields: list[tuple[str, str]]) -> None:
    import docx

    document = docx.Document()
    document.add_heading("CAG 101 INNOVATION IDEAS INITIATIVE", level=0)
    document.add_heading("Submission Form (Appendix-I)", level=1)
    document.add_paragraph(BANNER)

    for label, value in fields:
        document.add_heading(label, level=3)
        document.add_paragraph(value if value else "(left blank)")

    document.save(str(path))


def _write_plain_docx(path: Path, lines: list[str]) -> None:
    import docx

    document = docx.Document()
    document.add_paragraph(BANNER)
    for line in lines:
        document.add_paragraph(line)
    document.save(str(path))


def _write_media_placeholder(path: Path) -> None:
    """A stand-in for a real video. Exercises the 'media present but not
    transcribed' path without needing an actual encode."""
    path.write_bytes(b"\x00" * 2048)


def make_samples(count: int = 3) -> list[Path]:
    ensure_directories()
    base = load_config().path("paths.submissions")
    created: list[Path] = []

    for sample in SAMPLES[: max(1, min(count, len(SAMPLES)))]:
        folder = base / sample["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        _write_form_docx(folder / "Filled Submission Form Appendix-I.docx", sample["fields"])
        for name, lines in sample["extra_files"].items():
            _write_plain_docx(folder / name, lines)
        for name in sample["media_files"]:
            _write_media_placeholder(folder / name)
        created.append(folder)
    return created


def remove_samples() -> int:
    """Delete synthetic samples. Called before a real run."""
    import shutil

    base = load_config().path("paths.submissions")
    removed = 0
    for entry in base.iterdir():
        if entry.is_dir() and entry.name.startswith(SAMPLE_PREFIX):
            shutil.rmtree(entry)
            removed += 1
    return removed
