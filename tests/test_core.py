"""Tests for the deterministic parts of the framework.

These cover the components whose correctness must not depend on a model:
de-identification, formation routing, rubric arithmetic, and the scoring
guardrails. Run with:

    python tests/test_core.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cag101.deidentify import (  # noqa: E402
    deidentify,
    mask_names,
    mask_office,
    redact_privacy,
)
from cag101.formations import classify_formation  # noqa: E402
from cag101.rubric import available_rubrics, load_rubric  # noqa: E402
from cag101.schema import (  # noqa: E402
    DevelopmentStage,
    FormationCategory,
    ImpactDimension,
    ImpactRow,
    SolutionType,
    SubmissionContent,
)
from cag101.score import _parse_sub_scores  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(f"{label}{f' — {detail}' if detail else ''}")


def section(name: str) -> None:
    print(f"\n{name}")
    print("-" * len(name))


# ---------------------------------------------------------------------------
# Privacy redaction
# ---------------------------------------------------------------------------
def test_privacy_redaction() -> None:
    section("Privacy redaction")

    text = (
        "Contact the officer at sample.officer@example.gov.in or on 9876500001. "
        "Alternate number +91 98765 00002 and 09876500003."
    )
    out, report = redact_privacy(text)
    check("sample.officer@example.gov.in" not in out, "email removed")
    check("9876500001" not in out, "mobile removed")
    check(report.emails == 1, "email counted", f"got {report.emails}")
    check(report.mobiles >= 2, "mobiles counted", f"got {report.mobiles}")
    print(f"  redaction report: {report.summary()}")

    # An Aadhaar-shaped number must be caught and flagged, not merely removed.
    out, report = redact_privacy("Beneficiary ID 4321 8765 2109 was verified.")
    check("4321 8765 2109" not in out, "Aadhaar-shaped number removed")
    check(report.id_number_detected, "Aadhaar-shaped number flagged")
    check(bool(report.notes), "flag carries an explanatory note")
    print(f"  ID detection note: {report.notes[0][:80] if report.notes else '(none)'}")

    out, report = redact_privacy("PAN ABCDE1234F on record.")
    check("ABCDE1234F" not in out, "PAN removed")

    # A figure that merely looks numeric must survive — audit text is full of
    # amounts, and destroying them would destroy the evidence.
    out, _ = redact_privacy("The split purchase pattern was worth Rs. 2.7 crore in 2024-25.")
    check("2.7 crore" in out, "monetary amounts preserved")
    check("2024-25" in out, "year ranges preserved")

    out, _ = redact_privacy("Examined 412 of 68,000 purchase orders, a coverage of 0.6 per cent.")
    check("412" in out and "68,000" in out, "audit statistics preserved")
    print("  audit figures survive redaction")


# ---------------------------------------------------------------------------
# Blind review masking
# ---------------------------------------------------------------------------
def test_blinding() -> None:
    section("Blind-review masking")

    text = (
        "Shri Rajesh Kumar Singh led the initiative. Singh coordinated with the "
        "team, and Rajesh Kumar Singh presented the results."
    )
    out, count = mask_names(text, ["Rajesh Kumar Singh"])
    check("Rajesh" not in out, "given name masked")
    check("Singh" not in out, "surname masked on its own")
    check(count >= 3, "all mentions masked", f"got {count}")
    print(f"  {count} name mentions masked")

    office = "O/o the Principal Accountant General (Audit-I), Karnataka"
    text = (
        "The pilot ran in Karnataka. The Principal Accountant General (Audit-I), "
        "Karnataka approved it. Audit coverage improved."
    )
    out, count = mask_office(text, office)
    check("Karnataka" not in out, "state name masked")
    check("Audit coverage improved" in out, "generic word 'Audit' not destroyed",
          f"got: {out[-40:]}")
    print(f"  {count} office mentions masked; the word 'Audit' survives")

    # The combined pass must do both jobs.
    out, report = deidentify(
        "Rajesh Kumar Singh (rajesh@example.gov.in, 9876500001) of the "
        "O/o the AG (Audit), Nagaland ran the pilot.",
        names=["Rajesh Kumar Singh"],
        office="O/o the AG (Audit), Nagaland",
    )
    check("rajesh@example.gov.in" not in out, "combined pass removes email")
    check("Rajesh" not in out, "combined pass masks name")
    check("Nagaland" not in out, "combined pass masks office")
    print(f"  combined pass: {report.summary()}")


# ---------------------------------------------------------------------------
# Formation routing
# ---------------------------------------------------------------------------
def test_formations() -> None:
    section("Formation routing")

    cases = [
        ("O/o the AG (Audit), Nagaland", FormationCategory.SPECIAL_CATEGORY_STATE),
        ("O/o the Principal AG (Audit), Jammu & Kashmir", FormationCategory.SPECIAL_CATEGORY_STATE),
        ("O/o the PAG (Audit-I), Karnataka", FormationCategory.STATE),
        ("O/o the AG (A&E), Odisha", FormationCategory.STATE),
        ("PDA (Railways), Chennai", FormationCategory.UNION),
        ("DG Audit, Central Expenditure", FormationCategory.UNION),
        ("O/o the DG, iCED Jaipur", FormationCategory.OTHER),
        ("Regional Training Institute, Shillong", FormationCategory.OTHER),
        ("PPG Wing, O/o CAG of India", FormationCategory.OTHER),
        ("", FormationCategory.UNKNOWN),
        ("Some Unrecognised Office", FormationCategory.UNKNOWN),
    ]
    for office, expected in cases:
        got, reason = classify_formation(office)
        check(got is expected, f"routing: {office or '(empty)'}",
              f"expected {expected.value}, got {got.value}")
        print(f"  {(office or '(empty)')[:48]:<50} -> {got.value}")

    # A Railways office in a Special Category State is still Union side — but a
    # Special Category State marker takes precedence by design, since the scheme
    # routes those separately. Verify the documented precedence holds.
    got, _ = classify_formation("PDA (Railways), Tripura")
    check(
        got is FormationCategory.SPECIAL_CATEGORY_STATE,
        "Special Category precedence over Union marker",
        f"got {got.value}",
    )
    print("  Special Category State takes precedence over Union markers (by design)")


# ---------------------------------------------------------------------------
# Rubric integrity
# ---------------------------------------------------------------------------
def test_rubrics() -> None:
    section("Rubric integrity")

    for slug in available_rubrics():
        rubric = load_rubric(slug)
        weight_sum = sum(c.weight for c in rubric.criteria)
        check(weight_sum == rubric.total, f"{slug}: weights sum to total",
              f"{weight_sum} != {rubric.total}")
        for c in rubric.criteria:
            check(c.max_points == c.weight, f"{slug}: {c.key} sub-points match weight",
                  f"{c.max_points} != {c.weight}")
        check(bool(rubric.content_hash), f"{slug}: has a content hash")
        rendered = rubric.render_criterion(rubric.criterion_keys[0])
        check("SCORING BANDS" in rendered, f"{slug}: renders bands into the prompt")
        check(
            rubric.criteria[1].name not in rendered,
            f"{slug}: criterion slice excludes other criteria",
            "criterion isolation is what makes pass disagreement meaningful",
        )
        print(f"  {slug}: {len(rubric.criteria)} criteria, total {rubric.total}, "
              f"hash {rubric.content_hash}")

    # The published weights must be exactly these. A silent edit here would
    # change every score without anyone noticing, so assert them explicitly.
    cag = load_rubric("cag101_v1")
    expected = {
        "innovation_originality": 25,
        "institutional_relevance": 25,
        "feasibility_scalability": 25,
        "potential_impact": 25,
    }
    for key, weight in expected.items():
        check(cag.criterion(key).weight == weight,
              f"cag101 published weight for {key} is {weight}")

    cat1 = load_rubric("category1_v1")
    expected1 = {
        "the_solution": 40,
        "benefits": 30,
        "sustainability_replicability": 20,
        "change_management": 10,
    }
    for key, weight in expected1.items():
        check(cat1.criterion(key).weight == weight,
              f"category1 published weight for {key} is {weight}")
    print("  published weightages match both scheme documents")


# ---------------------------------------------------------------------------
# Scoring guardrails
# ---------------------------------------------------------------------------
def test_guardrails() -> None:
    section("Scoring guardrails")

    rubric = load_rubric("cag101_v1")
    criterion = rubric.criterion("potential_impact")   # 10 + 8 + 7

    # 1. An unevidenced high score must be pulled down to the top of Weak.
    raw = [
        {"sub_criterion": "quantified_impact", "band": "Exceptional", "score": 10,
         "rationale": "Claims 50% saving.", "confidence": 0.9, "evidence": []},
        {"sub_criterion": "beneficiary_breadth", "band": "Strong", "score": 7,
         "rationale": "All offices.", "confidence": 0.8,
         "evidence": [{"quote": "All 130+ offices will benefit",
                       "source_file": "form.docx", "locator": "form 4.1"}]},
        {"sub_criterion": "improvement_depth", "band": "Adequate", "score": 4,
         "rationale": "Some depth.", "confidence": 0.6,
         "evidence": [{"quote": "efficiency will improve",
                       "source_file": "form.docx", "locator": "form 2.6"}]},
    ]
    subs = _parse_sub_scores(raw, criterion, rubric)
    quantified = next(s for s in subs if s.sub_criterion == "quantified_impact")
    check(quantified.score <= 4.5, "unevidenced high score capped at top of Weak",
          f"got {quantified.score} of 10")
    check("unevidenced_score_capped" in quantified.guardrail_applied,
          "cap is recorded in the audit trail")
    print(f"  unevidenced 10/10 -> {quantified.score}/10 "
          f"({quantified.guardrail_applied[:60]})")

    evidenced = next(s for s in subs if s.sub_criterion == "beneficiary_breadth")
    check(evidenced.score == 7, "evidenced score left alone", f"got {evidenced.score}")

    # 2. Out-of-range scores must be clamped, never trusted.
    raw_bad = [
        {"sub_criterion": "quantified_impact", "band": "Exceptional", "score": 99,
         "rationale": "x", "confidence": 1.0,
         "evidence": [{"quote": "measured 120 hours saved across three pilot units",
                       "source_file": "form.docx", "locator": "form 4.2"}]},
    ]
    subs = _parse_sub_scores(raw_bad, criterion, rubric)
    top = next(s for s in subs if s.sub_criterion == "quantified_impact")
    check(top.score == 10, "score above maximum clamped to maximum", f"got {top.score}")
    check("clamped" in top.guardrail_applied, "clamp recorded")
    print(f"  score of 99 on a 10-point sub-criterion -> {top.score}")

    # 3. A missing sub-criterion must become an explicit zero, never vanish —
    #    silently dropping it would inflate the criterion total.
    check(len(subs) == 3, "missing sub-criteria are materialised as zeros",
          f"got {len(subs)} entries")
    missing = [s for s in subs if s.guardrail_applied == "missing_sub_score"]
    check(len(missing) == 2, "both omitted sub-criteria recorded", f"got {len(missing)}")
    check(all(s.score == 0.0 for s in missing), "omitted sub-criteria score zero")
    print(f"  2 omitted sub-criteria materialised as explicit zeros")

    # 4. Totals can never exceed the criterion maximum.
    total = sum(s.score for s in subs)
    check(total <= criterion.weight, "criterion total within maximum",
          f"{total} > {criterion.weight}")
    print(f"  criterion total {total} of {criterion.weight}")


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------
def test_schema() -> None:
    section("Schema helpers")

    row = ImpactRow(
        dimension=ImpactDimension.TIME_SAVED,
        estimate="About 120 hours per PSU audit cycle",
        basis="Measured across three pilot units over two cycles",
    )
    check(row.is_quantified, "numeric estimate detected as quantified")
    check(row.has_basis, "basis of adequate length detected")

    vague = ImpactRow(
        dimension=ImpactDimension.QUALITATIVE,
        estimate="Significant improvement",
        basis="",
    )
    check(not vague.is_quantified, "non-numeric estimate not counted as quantified")
    check(not vague.has_basis, "empty basis detected")
    print("  impact-row quantification detection works")

    content = SubmissionContent(
        solution_types=[SolutionType.PROCESS_REDESIGN],
        development_stage=DevelopmentStage.PILOT_TESTED,
    )
    check(not content.is_technology_centric,
          "process redesign is not treated as technology-centric",
          "so it is not penalised for empty Section 3")

    tech = SubmissionContent(solution_types=[SolutionType.AI_ML_LLM])
    check(tech.is_technology_centric, "AI/ML solution is technology-centric")
    print("  technology-centric detection works (drives proportionate scoring)")

    # The model must never receive identity, and office must be masked.
    from cag101.schema import CanonicalRecord, RestrictedIdentity

    record = CanonicalRecord(
        submission_ref="CAG101-0001",
        folder_name="test",
        content=SubmissionContent(office="O/o the AG (Audit), Kerala"),
        identity=RestrictedIdentity(full_name="Test Officer", mobile="9876500001"),
    )
    payload = record.for_model(blind_office=True)
    check("identity" not in payload, "identity excluded from model payload")
    check("Test Officer" not in str(payload), "submitter name absent from model payload")
    check("9876500001" not in str(payload), "mobile absent from model payload")
    check("Kerala" not in str(payload), "office masked in model payload")
    print("  model payload carries no identity and no office")


# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 68)
    print("CAG 101 Evaluation Framework — deterministic component tests")
    print("=" * 68)

    test_privacy_redaction()
    test_blinding()
    test_formations()
    test_rubrics()
    test_guardrails()
    test_schema()

    print("\n" + "=" * 68)
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} of {CHECKS} checks failed:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"PASSED — all {CHECKS} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
