"""Load an in-session evaluation into the database.

Used when the framework is run without an API key and the scoring is performed
inside a Claude Code session instead of by the automated pipeline. The scores
land in the same tables, so the portal and the Excel export are identical — but
the provenance is recorded differently, and it must be, because the controls are
weaker:

  * ONE pass, not two — so there is no cross-pass disagreement check.
  * No independent adversarial verifier — the same reader cannot audit itself.
  * The evaluator is a session, not a reproducible batch job.

Every submission loaded this way is therefore flagged `evaluation_route =
in_session_single_pass` and marked for human review, and the deterministic
guardrails (evidence cap, range clamp, missing-sub-score zeroing) are applied
through exactly the same code as the automated path.

Usage:
    python tools/load_manual_scores.py evaluations/*.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from cag101.db import init_db, session_scope  # noqa: E402
from cag101.formations import classify_formation  # noqa: E402
from cag101.models import (  # noqa: E402
    CanonicalRecordRow,
    CriterionScore,
    Evaluation,
    Evidence,
    Flag,
    RestrictedIdentityRow,
    Submission,
)
from cag101.rubric import load_rubric  # noqa: E402
from cag101.schema import (  # noqa: E402
    CanonicalRecord,
    MappingMeta,
    RestrictedIdentity,
    SubmissionContent,
)
from cag101.score import CriterionResult, SubmissionScore, _parse_sub_scores  # noqa: E402

MODEL_LABEL = "claude-opus-5 (in-session, Claude Code — not the automated API pipeline)"
PROMPT_VERSION = "manual-session-v1"
RUN_ID = "run-in-session-001"

ROUTE_CAVEAT = (
    "Evaluated in-session rather than by the automated pipeline: a single "
    "scoring pass with no independent adversarial verifier, so neither the "
    "cross-pass disagreement check nor the lowering-only verification was "
    "applied. Treat these scores as a first read and re-run through the "
    "automated pipeline once an API credential is available."
)


def upsert_flag(session, submission_id: int, key: str, value: str, detail: str = "") -> None:
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


def derive_flags(session, submission, content: SubmissionContent, payload: dict) -> None:
    """Mirror the flags the automated map stage would have set."""
    upsert_flag(session, submission.id, "thematic_area", content.thematic_area.value)
    upsert_flag(session, submission.id, "development_stage", content.development_stage.value)
    upsert_flag(session, submission.id, "formation_category", content.formation_category.value)
    upsert_flag(
        session, submission.id, "technology_centric",
        "true" if content.is_technology_centric else "false",
    )
    upsert_flag(
        session, submission.id, "privacy_concern_raised",
        "true" if content.data_privacy_notes.strip() else "false",
    )
    upsert_flag(session, submission.id, "evaluation_route",
                "in_session_single_pass", ROUTE_CAVEAT)

    flags = payload.get("flags", {})

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
    upsert_flag(session, submission.id, "evidence_strength",
                flags.get("evidence_strength", strength))

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
    completeness = "complete" if not gaps else ("minor_gaps" if len(gaps) <= 2 else "incomplete")
    upsert_flag(
        session, submission.id, "completeness", completeness,
        ("Missing or too brief: " + "; ".join(gaps)) if gaps else "",
    )

    naeg = (
        content.is_technology_centric
        and content.development_stage.value in {
            "pilot_tested", "partially_deployed", "ready_for_scale"
        }
    )
    upsert_flag(
        session, submission.id, "naeg_candidate_hint", "true" if naeg else "false",
        (
            "Digital-governance solution at pilot stage or beyond. Indicative "
            "only — the Filtering Committee decides NAeG nominations."
        ) if naeg else "",
    )

    # Human review: always true on this route, plus any material-specific reason.
    reasons = [ROUTE_CAVEAT]
    media_unread = any(
        f.kind in {"video", "audio"} and f.extraction_status in {"skipped", "error"}
        for f in submission.files
    )
    if media_unread:
        reasons.append(
            "A video or audio attachment was not transcribed, so it was not "
            "considered in scoring."
        )
    unreadable = [
        f.relative_path for f in submission.files
        if f.extraction_status in {"empty", "unsupported", "error"}
    ]
    if unreadable:
        reasons.append(
            "No text could be extracted from: " + "; ".join(unreadable) + "."
        )
    if completeness == "incomplete":
        reasons.append("Mandatory form fields are missing or too brief.")
    for extra in payload.get("review_reasons", []):
        reasons.append(extra)
    upsert_flag(session, submission.id, "human_review_required", "true", " ".join(reasons))


def load_one(path: Path) -> tuple[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ref = payload["ref"]
    rubric = load_rubric(payload.get("rubric", "cag101_v1"))

    content_data = dict(payload["content"])
    office = content_data.get("office", "") or ""
    formation, formation_reason = classify_formation(office)
    content_data.setdefault("formation_category", formation.value)
    content = SubmissionContent.model_validate(content_data)

    identity = RestrictedIdentity.model_validate(payload.get("identity", {}))
    meta_data = dict(payload.get("meta", {}))
    meta_data["mapper_notes"] = " ".join(
        filter(None, [meta_data.get("mapper_notes", ""), f"Formation: {formation_reason}"])
    )
    meta = MappingMeta.model_validate(meta_data)

    record = CanonicalRecord(
        submission_ref=ref,
        folder_name=payload.get("folder_name", ""),
        content=content,
        identity=identity,
        meta=meta,
    )

    # Build criterion results through the same guardrail code as the pipeline.
    criteria: list[CriterionResult] = []
    for spec in rubric.criteria:
        block = payload["scores"].get(spec.key)
        if block is None:
            raise ValueError(f"{ref}: no scores supplied for criterion {spec.key!r}")
        subs = _parse_sub_scores(block.get("sub_scores", []), spec, rubric)
        criteria.append(
            CriterionResult(
                criterion=spec.key,
                name=spec.name,
                max_points=spec.weight,
                sub_scores=subs,
                notes=block.get("notes", ""),
            )
        )
    score = SubmissionScore(submission_ref=ref, criteria=criteria)

    with session_scope() as session:
        submission = session.scalar(select(Submission).where(Submission.ref == ref))
        if submission is None:
            raise ValueError(f"No submission {ref} in the database — run ingest first.")

        # Canonical record
        old = session.scalar(
            select(CanonicalRecordRow).where(
                CanonicalRecordRow.submission_id == submission.id
            )
        )
        if old:
            session.delete(old)
            session.flush()
        session.add(
            CanonicalRecordRow(
                submission_id=submission.id,
                content=json.loads(content.model_dump_json()),
                meta=json.loads(meta.model_dump_json()),
                mapper_model=MODEL_LABEL,
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
        session.add(
            RestrictedIdentityRow(
                submission_id=submission.id,
                payload=json.loads(identity.model_dump_json()),
            )
        )

        # Denormalised dashboard fields
        submission.title = content.title
        submission.thematic_area = content.thematic_area.value
        submission.solution_types = [t.value for t in content.solution_types]
        submission.development_stage = content.development_stage.value
        submission.office = content.office
        submission.formation_category = content.formation_category.value
        submission.status = "scored"
        submission.status_detail = ROUTE_CAVEAT

        derive_flags(session, submission, content, payload)

        # Supersede any earlier evaluation
        for prior in session.scalars(
            select(Evaluation).where(
                Evaluation.submission_id == submission.id,
                Evaluation.is_final.is_(True),
            )
        ):
            prior.is_final = False

        evaluation = Evaluation(
            submission_id=submission.id,
            run_id=RUN_ID,
            rubric_slug=rubric.slug,
            rubric_hash=rubric.content_hash,
            prompt_version=PROMPT_VERSION,
            model=MODEL_LABEL,
            pass_no=1,
            is_final=True,
            total=score.total,
            verifier_applied=False,
            verifier_adjustment=0.0,
            verifier_notes=(
                "No adversarial verification pass was run on this route. "
                "Deterministic guardrails (evidence cap, range clamp, "
                "missing-sub-score zeroing) were applied."
            ),
            remarks=payload.get("remarks", ""),
        )
        session.add(evaluation)
        session.flush()

        for criterion in score.criteria:
            for sub in criterion.sub_scores:
                row = CriterionScore(
                    evaluation_id=evaluation.id,
                    criterion=criterion.criterion,
                    sub_criterion=sub.sub_criterion,
                    score=sub.score,
                    max_score=sub.max_score,
                    band=sub.band,
                    rationale=(
                        sub.rationale
                        + (f"\n[Guardrail: {sub.guardrail_applied}]"
                           if sub.guardrail_applied else "")
                    ).strip(),
                    confidence=sub.confidence,
                )
                session.add(row)
                session.flush()
                for e in sub.evidence:
                    session.add(
                        Evidence(
                            criterion_score_id=row.id,
                            quote=e.get("quote", ""),
                            source_file=e.get("source_file", ""),
                            locator=e.get("locator", ""),
                        )
                    )

    return ref, score.total


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    init_db()
    paths: list[Path] = []
    for arg in argv:
        p = Path(arg)
        paths.extend(sorted(p.parent.glob(p.name)) if "*" in p.name else [p])

    loaded: list[tuple[str, float]] = []
    for path in paths:
        if not path.exists():
            print(f"missing: {path}")
            continue
        try:
            ref, total = load_one(path)
        except Exception as exc:
            print(f"FAILED {path.name}: {type(exc).__name__}: {exc}")
            continue
        loaded.append((ref, total))
        print(f"{ref}  loaded  total {total:.1f}/100  ({path.name})")

    if loaded:
        totals = sorted(t for _, t in loaded)
        print(
            f"\nLoaded {len(loaded)} evaluation(s). "
            f"Range {totals[0]:.1f}–{totals[-1]:.1f}, "
            f"mean {sum(totals) / len(totals):.1f}"
        )
        print(
            "\nAll of these are flagged for human review: this route is a "
            "single pass with no independent verifier. Re-run through "
            "`python cag.py score` once an API credential is available."
        )
    return 0 if loaded else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
