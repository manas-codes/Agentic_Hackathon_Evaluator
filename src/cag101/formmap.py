"""Form-mapping: extracted text becomes a canonical Appendix-I record.

This is the only stage that deals with how messy real submissions are —
people type outside the boxes, merge sections, answer 2.5 inside 2.6, or send a
free-form note instead of the form. Everything downstream sees one clean shape.

**Privacy sequencing** (deliberate, and worth understanding before changing):

1. Contact details and ID-shaped numbers are redacted *before* this stage, so
   emails, mobile numbers and any Aadhaar-shaped digits never reach the model.
2. Names remain visible *to the mapper only*, because the mapper's job includes
   identifying who submitted so that (a) the restricted identity record can be
   built and (b) those names can be masked for the scorers.
3. Names and office are masked *before the scoring stage*, so scoring is blind.

The result: the model never sees contact details at any point, and never sees
names or office at the point where judgement happens.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .config import CREDENTIAL_HELP, load_config
from .db import init_db, session_scope
from .deidentify import redact_privacy
from .extract import load_cached_extraction
from .formations import classify_formation
from .ingest import submission_text
from .llm import CostMeter, call_json, describe_credential_source
from .models import CanonicalRecordRow, Flag, RestrictedIdentityRow, Submission
from .schema import (
    CanonicalRecord,
    MappingMeta,
    RestrictedIdentity,
    SubmissionContent,
)

PROMPT_VERSION = "formmap-v1"

INSTRUCTIONS = """\
You are a records officer in the PPG Wing of the Office of the Comptroller and \
Auditor General of India. Your task is transcription and classification, not \
evaluation. You are reading the text extracted from one submission to the \
CAG 101 Innovation Ideas Initiative and mapping it onto the fields of the \
Appendix-I submission form.

Rules you must follow:

1. TRANSCRIBE, DO NOT IMPROVE. Copy the submitter's own words into the long-text \
   fields. Do not summarise, rephrase, correct, expand or tidy them. The \
   evaluation that follows depends on assessing what was actually written. \
   Preserve the submitter's wording even where it is vague or repetitive — \
   vagueness is information the evaluators need.

2. DO NOT INVENT. If a field is not addressed anywhere in the material, leave it \
   empty (or the "not_stated" enum value) and name it in `missing_fields`. Never \
   fill a gap with a plausible guess. An empty field is a correct answer.

3. FIELDS MAY BE OUT OF PLACE. Submitters frequently answer one question under \
   another heading, or put everything in a covering note. Assign content to the \
   field it actually answers, not the heading it sits under. If the same content \
   answers two fields, use it for both and say so in `mapper_notes`.

4. ATTACHMENTS COUNT. Material in a deck, concept note, workflow diagram or \
   video transcript is part of the submission. Draw on all of it, and record in \
   `source_files_used` which files you drew from.

5. THE IMPACT TABLE IS LITERAL. For form field 4.2, transcribe each row's \
   estimate and its stated basis exactly as given. If a row has an estimate but \
   no basis, record the estimate and leave the basis empty — do not construct a \
   basis. If a row is blank, omit it entirely.

6. CLASSIFY CONSERVATIVELY. For enum fields, choose the value the submitter \
   actually indicated. Where they ticked nothing and the text does not make it \
   clear, use "unclear" or "not_stated" rather than inferring.

7. FLAG YOUR OWN UNCERTAINTY. `overall_confidence` reflects how reliably you \
   could map this material: 0.9+ for a cleanly filled form, 0.5-0.7 where you \
   had to interpret structure, below 0.5 where the material barely resembles the \
   form. List every field you were unsure about in `low_confidence_fields`. A \
   low score here routes the submission to a human — that is a useful outcome, \
   not a failure.

You will see placeholder tokens such as [EMAIL REDACTED] and [MOBILE REDACTED] \
where contact details were removed before the text reached you. Leave them as \
they are.
"""

# ---------------------------------------------------------------------------
# Output schema. Hand-written rather than generated from Pydantic so that the
# enum values and the "required + additionalProperties: false" strictness are
# explicit and reviewable.
# ---------------------------------------------------------------------------
THEMATIC_AREAS = ["A", "B", "C", "D", "unclear"]
SOLUTION_TYPES = [
    "framework_methodology", "application_software", "process_redesign",
    "dashboard_visualisation", "hardware_iot", "ai_ml_llm", "other",
]
DEVELOPMENT_STAGES = [
    "concept", "early_prototype", "pilot_tested", "partially_deployed",
    "ready_for_scale", "other", "not_stated",
]
LICENSING = [
    "fully_open_source", "mostly_open_source", "fully_proprietary",
    "not_decided", "not_stated",
]
SCALABILITY_MODES = [
    "central_deployment", "needs_local_customisation", "other", "not_stated",
]
IMPACT_DIMENSIONS = [
    "time_saved", "cost_reduction", "error_rate_improvement",
    "coverage_expanded", "citizen_stakeholder_benefit", "qualitative_other",
]
ATTACHMENT_TYPES = [
    "A_demo_video", "B_workflow_diagram", "C_concept_note", "none",
]


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


MAPPER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["content", "identity", "meta"],
    "properties": {
        "content": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "thematic_area", "title", "solution_types", "solution_type_other",
                "development_stage", "development_stage_other", "problem_statement",
                "proposed_solution", "pilot_evidence", "tech_stack", "licensing",
                "technical_scalability", "data_privacy_notes", "beneficiaries",
                "scalability_mode", "scalability_notes", "impact_rows",
                "one_time_cost", "recurring_cost", "roi_notes",
                "primary_attachment_type", "supporting_document_present", "office",
            ],
            "properties": {
                "thematic_area": {"type": "string", "enum": THEMATIC_AREAS,
                                  "description": "Form field 2.1"},
                "title": _str("Form field 2.2, verbatim"),
                "solution_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": SOLUTION_TYPES},
                    "description": "Form field 2.3; may be more than one",
                },
                "solution_type_other": _str("Description if 'other' was chosen, else empty"),
                "development_stage": {"type": "string", "enum": DEVELOPMENT_STAGES,
                                      "description": "Form field 2.4"},
                "development_stage_other": _str("Text if stage is 'other', else empty"),
                "problem_statement": _str("Form field 2.5, verbatim"),
                "proposed_solution": _str("Form field 2.6, verbatim"),
                "pilot_evidence": _str("Form field 2.7, verbatim; empty if not answered"),
                "tech_stack": _str("Form field 3.1; empty if not a technology initiative"),
                "licensing": {"type": "string", "enum": LICENSING,
                              "description": "Form field 3.2"},
                "technical_scalability": _str("Form field 3.3, verbatim"),
                "data_privacy_notes": _str("Form field 3.4, verbatim"),
                "beneficiaries": _str("Form field 4.1, verbatim"),
                "scalability_mode": {"type": "string", "enum": SCALABILITY_MODES,
                                     "description": "Form field 4.2, first part"},
                "scalability_notes": _str("Any free text given with 4.2, verbatim"),
                "impact_rows": {
                    "type": "array",
                    "description": "Form field 4.2 impact table; omit blank rows entirely",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["dimension", "estimate", "basis"],
                        "properties": {
                            "dimension": {"type": "string", "enum": IMPACT_DIMENSIONS},
                            "estimate": _str("The submitter's estimate, verbatim"),
                            "basis": _str("The stated basis or assumption, verbatim; "
                                          "empty if none was given"),
                        },
                    },
                },
                "one_time_cost": _str("Section 5 one-time cost, as written"),
                "recurring_cost": _str("Section 5 recurring cost, as written"),
                "roi_notes": _str("Form field 5.1, verbatim"),
                "primary_attachment_type": {
                    "type": "string", "enum": ATTACHMENT_TYPES,
                    "description": "Form field 6.1 — which primary format was submitted",
                },
                "supporting_document_present": {
                    "type": "boolean",
                    "description": "Form field 6.2 — whether an optional supporting doc exists",
                },
                "office": _str("The submitting office as written, e.g. "
                               "'O/o the Principal Accountant General (Audit-I), Karnataka'"),
            },
        },
        "identity": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "full_name", "designation", "is_team_submission",
                "co_submitter_names", "primary_submitter_name", "date_of_submission",
            ],
            "properties": {
                "full_name": _str("Section 1 submitter name"),
                "designation": _str("Section 1 designation"),
                "is_team_submission": {
                    "type": "boolean",
                    "description": "Form field 1.1; false if solo or not stated",
                },
                "co_submitter_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names from form field 1.2, one per entry",
                },
                "primary_submitter_name": _str("Section 7 declaration name"),
                "date_of_submission": _str("Section 7 date, as DD-MM-YYYY if parseable"),
            },
        },
        "meta": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "missing_fields", "low_confidence_fields", "overall_confidence",
                "mapper_notes", "source_files_used",
            ],
            "properties": {
                "missing_fields": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Form field numbers not addressed anywhere, e.g. ['2.7', '3.3']",
                },
                "low_confidence_fields": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Fields you mapped but are unsure about",
                },
                "overall_confidence": {
                    "type": "number", "minimum": 0.0, "maximum": 1.0,
                },
                "mapper_notes": _str("Anything an evaluator should know about how this "
                                     "material was structured, in one or two sentences"),
                "source_files_used": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Relative paths of files you drew content from",
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------
def map_one(
    submission: Submission,
    text: str,
    meter: CostMeter | None = None,
) -> tuple[CanonicalRecord, dict[str, Any]]:
    """Map one submission's text. Returns the record and the raw model payload."""
    cfg = load_config()

    # Privacy redaction happens here, before the first model call.
    safe_text, redaction = redact_privacy(text)

    user_content = (
        f"Submission reference: {submission.ref}\n"
        f"Source folder name: {submission.folder_name}\n\n"
        "The extracted text of the submission follows. Each source file is "
        "introduced by a '===== SOURCE: ... =====' line.\n\n"
        f"{safe_text}"
    )

    result = call_json(
        instructions=INSTRUCTIONS,
        user_content=user_content,
        schema=MAPPER_SCHEMA,
        model=str(cfg.require("llm.utility_model")),
        cacheable_prefix=None,   # nothing stable to cache here; text dominates
        meter=meter,
    )

    payload = result.data
    content_data = dict(payload["content"])
    identity_data = dict(payload["identity"])
    meta_data = dict(payload["meta"])

    office = content_data.get("office", "") or ""
    formation, formation_reason = classify_formation(office)
    content_data["formation_category"] = formation.value

    content = SubmissionContent.model_validate(content_data)
    identity = RestrictedIdentity(
        full_name=identity_data.get("full_name", ""),
        designation=identity_data.get("designation", ""),
        is_team_submission=identity_data.get("is_team_submission"),
        co_submitters=[
            {"name": n, "designation": "", "office": ""}
            for n in identity_data.get("co_submitter_names", [])
        ],
        primary_submitter_name=identity_data.get("primary_submitter_name", ""),
        date_of_submission=identity_data.get("date_of_submission", ""),
    )
    meta = MappingMeta(
        missing_fields=meta_data.get("missing_fields", []),
        mapper_notes=" ".join(
            filter(None, [
                meta_data.get("mapper_notes", ""),
                f"Formation: {formation_reason}",
                f"Redaction before mapping: {redaction.summary()}." if redaction.total else "",
                *redaction.notes,
            ])
        ).strip(),
        overall_confidence=float(meta_data.get("overall_confidence", 0.0)),
        source_files_used=meta_data.get("source_files_used", []),
        field_confidence={
            field: 0.4 for field in meta_data.get("low_confidence_fields", [])
        },
    )

    record = CanonicalRecord(
        submission_ref=submission.ref,
        folder_name=submission.folder_name,
        content=content,
        identity=identity,
        meta=meta,
    )
    return record, {"redaction": redaction, "usage": result.usage, "model": result.model}


def map_submissions(refs: list[str] | None = None, force: bool = False) -> int:
    """Map every extracted submission that has no canonical record yet."""
    init_db()
    meter = CostMeter.from_config()
    mapped = skipped = failed = 0

    with session_scope() as session:
        query = select(Submission).order_by(Submission.ref)
        if refs:
            query = query.where(Submission.ref.in_(refs))
        submissions = list(session.scalars(query))

    if not submissions:
        print("No submissions to map. Run: python cag.py ingest")
        return 1

    # Check the credential before touching anything, so a missing key produces
    # one clear message rather than an identical failure per submission.
    credential = describe_credential_source()
    if credential.startswith("none found"):
        print(CREDENTIAL_HELP)
        return 1
    print(f"Credential: {credential}")
    print(f"Mapping model: {load_config().require('llm.utility_model')}\n")

    for sub in submissions:
        with session_scope() as session:
            submission = session.get(Submission, sub.id)
            assert submission is not None

            existing = session.scalar(
                select(CanonicalRecordRow).where(
                    CanonicalRecordRow.submission_id == submission.id
                )
            )
            if existing and not force:
                print(f"{submission.ref}  already mapped (use --force to redo)")
                skipped += 1
                continue

            docs = load_cached_extraction(submission.ref, submission.content_hash)
            if not docs:
                print(f"{submission.ref}  SKIPPED — no extraction cache; re-run ingest")
                failed += 1
                continue

            text = submission_text(docs, max_chars=400_000)
            if not text.strip():
                print(f"{submission.ref}  SKIPPED — no extractable text")
                submission.status = "extraction_failed"
                failed += 1
                continue

            try:
                record, info = map_one(submission, text, meter=meter)
            except Exception as exc:
                print(f"{submission.ref}  FAILED — {type(exc).__name__}: {exc}")
                submission.status = "mapping_failed"
                submission.status_detail = str(exc)[:2000]
                failed += 1
                continue

            # Persist. Content and identity go to separate tables by design.
            if existing:
                session.delete(existing)
                session.flush()
            session.add(
                CanonicalRecordRow(
                    submission_id=submission.id,
                    content=json.loads(record.content.model_dump_json()),
                    meta=json.loads(record.meta.model_dump_json()),
                    mapper_model=info["model"],
                )
            )

            old_identity = session.scalar(
                select(RestrictedIdentityRow).where(
                    RestrictedIdentityRow.submission_id == submission.id
                )
            )
            if old_identity:
                session.delete(old_identity)
                session.flush()
            if record.identity:
                session.add(
                    RestrictedIdentityRow(
                        submission_id=submission.id,
                        payload=json.loads(record.identity.model_dump_json()),
                    )
                )

            # Denormalise the fields the dashboard filters on.
            submission.title = record.content.title
            submission.thematic_area = record.content.thematic_area.value
            submission.solution_types = [t.value for t in record.content.solution_types]
            submission.development_stage = record.content.development_stage.value
            submission.office = record.content.office
            submission.formation_category = record.content.formation_category.value
            submission.status = "mapped"

            _write_mapping_flags(session, submission, record, info["redaction"])

            mapped += 1
            print(
                f"{submission.ref}  mapped   confidence={record.meta.overall_confidence:.2f}  "
                f"theme={submission.thematic_area}  "
                f"missing={len(record.meta.missing_fields)}  "
                f"{(record.content.title or '(no title)')[:50]}"
            )

    print(
        f"\nMapped {mapped}, skipped {skipped}, failed {failed}. "
        f"Model usage: {meter.usage.summary()}"
    )
    return 0 if failed == 0 else 1


def _set_flag(session, submission_id: int, key: str, value: str, detail: str = "") -> None:
    existing = session.scalar(
        select(Flag).where(Flag.submission_id == submission_id, Flag.key == key)
    )
    if existing:
        existing.value = value
        existing.detail = detail
    else:
        session.add(
            Flag(submission_id=submission_id, key=key, value=value, detail=detail)
        )


def _write_mapping_flags(session, submission, record: CanonicalRecord, redaction) -> None:
    """Flags derivable at mapping time. Scoring adds more later."""
    content = record.content
    meta = record.meta

    _set_flag(session, submission.id, "thematic_area", content.thematic_area.value)
    _set_flag(session, submission.id, "development_stage", content.development_stage.value)
    _set_flag(session, submission.id, "formation_category", content.formation_category.value)
    _set_flag(
        session, submission.id, "technology_centric",
        "true" if content.is_technology_centric else "false",
    )
    _set_flag(
        session, submission.id, "privacy_concern_raised",
        "true" if content.data_privacy_notes.strip() else "false",
    )
    _set_flag(
        session, submission.id, "id_number_detected",
        "true" if redaction.id_number_detected else "false",
        detail=" ".join(redaction.notes),
    )

    # Evidence strength, from form field 2.7 and the declared stage.
    pilot = content.pilot_evidence.strip()
    has_numbers = any(ch.isdigit() for ch in pilot)
    if content.development_stage.value in {"partially_deployed", "ready_for_scale"} and has_numbers:
        strength = "deployed_with_data"
    elif pilot and has_numbers:
        strength = "piloted_with_results"
    elif pilot:
        strength = "tested_no_results"
    else:
        strength = "claimed_only"
    _set_flag(session, submission.id, "evidence_strength", strength)

    # Completeness. Mandatory fields are those marked * on the form.
    mandatory = {
        "2.1 thematic area": content.thematic_area.value != "unclear",
        "2.2 title": bool(content.title.strip()),
        "2.3 solution type": bool(content.solution_types),
        "2.4 development stage": content.development_stage.value != "not_stated",
        "2.5 problem statement": len(content.problem_statement.split()) >= 30,
        "2.6 proposed solution": len(content.proposed_solution.split()) >= 50,
        "4.1 beneficiaries": len(content.beneficiaries.split()) >= 15,
        "4.2 scalability": content.scalability_mode.value != "not_stated",
        "6.1 primary attachment": content.primary_attachment_type.value != "none",
    }
    gaps = [name for name, ok in mandatory.items() if not ok]
    if not gaps:
        completeness = "complete"
    elif len(gaps) <= 2:
        completeness = "minor_gaps"
    else:
        completeness = "incomplete"
    _set_flag(
        session, submission.id, "completeness", completeness,
        detail=("Missing or too brief: " + "; ".join(gaps)) if gaps else "",
    )

    # NAeG hint — indicative only, per Concept Paper §IV.b.
    naeg = (
        content.is_technology_centric
        and content.development_stage.value in {
            "pilot_tested", "partially_deployed", "ready_for_scale"
        }
    )
    _set_flag(
        session, submission.id, "naeg_candidate_hint", "true" if naeg else "false",
        detail=(
            "Digital-governance solution at pilot stage or beyond. Indicative "
            "only — the Filtering Committee decides NAeG nominations."
        ) if naeg else "",
    )

    # Route to a human when the mapper struggled or media went unread.
    media_unread = any(
        f.kind in {"video", "audio"} and f.extraction_status in {"skipped", "error"}
        for f in submission.files
    )
    ocr_used = any(f.ocr_used for f in submission.files)
    reasons: list[str] = []
    if meta.overall_confidence < 0.6:
        reasons.append(f"low form-mapping confidence ({meta.overall_confidence:.2f})")
    if media_unread:
        reasons.append("a video or audio attachment was not transcribed")
    if ocr_used:
        reasons.append("OCR was used, so text may contain recognition errors")
    if completeness == "incomplete":
        reasons.append("mandatory form fields are missing")
    _set_flag(
        session, submission.id, "human_review_required",
        "true" if reasons else "false",
        detail="; ".join(reasons),
    )
