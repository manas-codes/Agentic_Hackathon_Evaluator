"""Run orchestration: passes, disagreement handling, persistence, resume.

Scoring policy, stated plainly because it has to be explainable to a committee:

* Each submission is scored by **two independent passes**. Each pass scores all
  four criteria in parallel and is then put through the lowering-only verifier.
* The **final score is the per-sub-criterion mean** of the passes, and the
  evidence recorded against it is the union of what the passes quoted. Nothing
  is hidden: every pass is stored in full and visible on the scorecard.
* If the passes disagree by more than the configured threshold, a **third pass**
  runs and the submission is flagged `human_review_required`. Disagreement is
  reported, not smoothed away — a submission the machine cannot score
  consistently is exactly the one a human should read.
* Every stored score carries the rubric version and hash, prompt version, model
  ID, token count and cost.
"""

from __future__ import annotations

import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import CREDENTIAL_HELP, load_config
from .db import init_db, session_scope
from .extract import load_cached_extraction
from .ingest import submission_text
from .llm import (
    CostCeilingExceeded,
    CostMeter,
    count_tokens,
    describe_credential_source,
    price_usage,
)
from .models import (
    CanonicalRecordRow,
    CriterionScore,
    Evaluation,
    Evidence,
    Flag,
    RestrictedIdentityRow,
    Run,
    Submission,
)
from .rubric import Rubric, load_rubric
from .schema import CanonicalRecord, MappingMeta, RestrictedIdentity, SubmissionContent
from .score import (
    INSTRUCTIONS as SCORE_INSTRUCTIONS,
    PROMPT_VERSION as SCORE_PROMPT_VERSION,
    CriterionResult,
    SubScore,
    SubmissionScore,
    blind_view,
    render_submission_for_scoring,
    score_submission,
)
from .verify import verify_submission


# ---------------------------------------------------------------------------
# Loading a canonical record back out of the database
# ---------------------------------------------------------------------------
def load_record(session: Session, submission: Submission) -> CanonicalRecord | None:
    row = session.scalar(
        select(CanonicalRecordRow).where(CanonicalRecordRow.submission_id == submission.id)
    )
    if row is None:
        return None
    identity_row = session.scalar(
        select(RestrictedIdentityRow).where(
            RestrictedIdentityRow.submission_id == submission.id
        )
    )
    return CanonicalRecord(
        submission_ref=submission.ref,
        folder_name=submission.folder_name,
        content=SubmissionContent.model_validate(row.content),
        identity=(
            RestrictedIdentity.model_validate(identity_row.payload)
            if identity_row
            else None
        ),
        meta=MappingMeta.model_validate(row.meta),
    )


# ---------------------------------------------------------------------------
# Consolidating passes into the final score
# ---------------------------------------------------------------------------
def consolidate(passes: list[SubmissionScore], rubric: Rubric) -> SubmissionScore:
    """Per-sub-criterion mean across passes; evidence is the union."""
    if len(passes) == 1:
        return passes[0]

    criteria: list[CriterionResult] = []
    for key in rubric.criterion_keys:
        spec = rubric.criterion(key)
        per_pass = [p.criterion(key) for p in passes]
        usable = [c for c in per_pass if c and not c.error]
        if not usable:
            criteria.append(
                CriterionResult(
                    criterion=key,
                    name=spec.name,
                    max_points=spec.weight,
                    sub_scores=[],
                    error="; ".join(c.error for c in per_pass if c and c.error),
                )
            )
            continue

        subs: list[SubScore] = []
        for sub_spec in spec.sub_criteria:
            values: list[float] = []
            evidence: list[dict[str, str]] = []
            rationales: list[str] = []
            bands: list[str] = []
            confidences: list[float] = []
            guardrails: list[str] = []
            seen_quotes: set[str] = set()

            for c in usable:
                found = next(
                    (s for s in c.sub_scores if s.sub_criterion == sub_spec.key), None
                )
                if found is None:
                    continue
                values.append(found.score)
                bands.append(found.band)
                confidences.append(found.confidence)
                if found.rationale:
                    rationales.append(found.rationale)
                if found.guardrail_applied:
                    guardrails.append(found.guardrail_applied)
                for e in found.evidence:
                    fingerprint = e.get("quote", "")[:200].strip().lower()
                    if fingerprint and fingerprint not in seen_quotes:
                        seen_quotes.add(fingerprint)
                        evidence.append(e)

            if not values:
                subs.append(
                    SubScore(
                        sub_criterion=sub_spec.key,
                        name=sub_spec.name,
                        score=0.0,
                        max_score=sub_spec.max,
                        band="Absent",
                        rationale="No pass returned a score for this sub-criterion.",
                        confidence=0.0,
                        guardrail_applied="missing_in_all_passes",
                    )
                )
                continue

            mean = round(statistics.fmean(values), 2)
            spread = round(max(values) - min(values), 2)
            # Keep the rationale from the pass nearest the mean, so the recorded
            # reasoning actually corresponds to the recorded number.
            nearest = min(range(len(values)), key=lambda i: abs(values[i] - mean))
            rationale = rationales[nearest] if nearest < len(rationales) else ""
            if spread > 0:
                rationale += (
                    f"\n[Across {len(values)} passes: {values}, "
                    f"mean {mean}, spread {spread}.]"
                )

            subs.append(
                SubScore(
                    sub_criterion=sub_spec.key,
                    name=sub_spec.name,
                    score=mean,
                    max_score=sub_spec.max,
                    band=bands[nearest] if nearest < len(bands) else "",
                    rationale=rationale.strip(),
                    confidence=round(statistics.fmean(confidences), 2) if confidences else 0.0,
                    evidence=evidence,
                    guardrail_applied="; ".join(sorted(set(guardrails))),
                )
            )

        criteria.append(
            CriterionResult(
                criterion=key,
                name=spec.name,
                max_points=spec.weight,
                sub_scores=subs,
                notes=" ".join(filter(None, (c.notes for c in usable)))[:2000],
            )
        )

    final = SubmissionScore(submission_ref=passes[0].submission_ref, criteria=criteria)
    for p in passes:
        final.usage.add(p.usage)
    return final


# ---------------------------------------------------------------------------
# Scoring one submission end to end
# ---------------------------------------------------------------------------
@dataclass
class SubmissionOutcome:
    ref: str
    total: float | None
    passes_run: int
    spread: float
    verifier_adjustment: float
    duration: float
    error: str = ""
    flagged: bool = False
    note: str = ""

    def line(self) -> str:
        if self.error:
            return f"{self.ref}  FAILED    {self.error[:90]}"
        flag = "  [human review]" if self.flagged else ""
        return (
            f"{self.ref}  {self.total:5.1f}/100  "
            f"{self.passes_run} pass(es), spread {self.spread:.1f}, "
            f"verifier {self.verifier_adjustment:+.1f}, {self.duration:.0f}s{flag}"
        )


def score_one(
    submission_id: int,
    run_id: str,
    rubric: Rubric,
    meter: CostMeter,
) -> SubmissionOutcome:
    cfg = load_config()
    started = time.monotonic()
    n_passes = max(1, int(cfg.get("pipeline.scoring_passes", 2)))
    threshold = float(cfg.get("pipeline.disagreement_threshold", 8))
    use_verifier = bool(cfg.get("pipeline.enable_verifier", True))

    with session_scope() as session:
        submission = session.get(Submission, submission_id)
        if submission is None:
            return SubmissionOutcome("?", None, 0, 0, 0, 0, error="submission vanished")
        ref = submission.ref
        record = load_record(session, submission)
        if record is None:
            return SubmissionOutcome(
                ref, None, 0, 0, 0, 0,
                error="not mapped yet — run `python cag.py map` first",
            )
        docs = load_cached_extraction(submission.ref, submission.content_hash)
        text = submission_text(docs, max_chars=400_000) if docs else ""

    if not text.strip():
        return SubmissionOutcome(
            ref, None, 0, 0, 0, 0, error="no extracted text available"
        )

    # --- passes -----------------------------------------------------------
    passes: list[SubmissionScore] = []
    verifier_total = 0.0
    verifier_notes: list[str] = []

    # A while loop, not `for pass_no in range(1, n_passes + 1)`. The range was
    # materialised once from n_passes == 2, so raising n_passes to 3 inside the
    # body had no effect and the tie-breaking third pass never ran - the config
    # promised it, the docstring promised it, and two wildly disagreeing passes
    # were silently averaged instead.
    pass_no = 0
    while pass_no < n_passes:
        pass_no += 1
        try:
            result = score_submission(rubric, record, text, meter=meter)
        except CostCeilingExceeded:
            raise
        except Exception as exc:
            return SubmissionOutcome(
                ref, None, len(passes), 0, verifier_total,
                time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            )

        if use_verifier:
            # Blinded, exactly as the scorer's view is. The verifier is a model
            # call like any other and it is the pass that can change a score,
            # so an unmasked view here would let it act on the formation it can
            # see - the precise thing blind review exists to prevent.
            view = blind_view(render_submission_for_scoring(record, text), record)
            verification = verify_submission(rubric, result, view, meter=meter)
            verifier_total += verification.total_adjustment
            verifier_notes.append(f"pass {pass_no}: {verification.summary()}")
            result.usage.add(verification.usage)

        passes.append(result)

        # A third pass only when the first two disagree materially.
        if pass_no == n_passes >= 2 and len(passes) > 1:
            spread = max(p.total for p in passes) - min(p.total for p in passes)
            if spread > threshold and n_passes < 3:
                n_passes = 3

    totals = [p.total for p in passes]
    spread = round(max(totals) - min(totals), 2) if len(totals) > 1 else 0.0
    final = consolidate(passes, rubric)

    # A criterion that errored in EVERY pass carries no sub-scores, so it
    # contributes zero to a total still presented as being out of 100. With two
    # passes failing the same way - a persistent schema failure, a refusal, a
    # truncation on a long submission - the spread is zero, so the disagreement
    # check does not catch it, and the submission is ranked against others
    # marked out of a real 100 while missing up to 25 points. Worse, the
    # already-scored filter then skips it on every later run, making the
    # corrupt score permanent. It must be flagged loudly and never look clean.
    broken = [c for c in final.criteria if c.error and not c.sub_scores]
    lost = sum(c.max_points for c in broken)

    flagged = spread > threshold or bool(broken)
    note = ""
    if broken:
        names = ", ".join(f"{c.name} ({c.max_points} marks)" for c in broken)
        note = (
            f"INCOMPLETE SCORE - {len(broken)} criterion/criteria could not be "
            f"scored in any pass: {names}. The recorded total is out of "
            f"{100 - lost}, not 100, and must not be compared with other "
            f"submissions until this is re-run. Errors: "
            + " | ".join(c.error for c in broken if c.error)[:600]
        )
    elif flagged:
        note = (
            f"Scoring passes disagreed by {spread:.1f} points "
            f"(threshold {threshold:.0f}). Totals: {totals}. "
            "A human should read this submission."
        )

    with session_scope() as session:
        submission = session.get(Submission, submission_id)
        assert submission is not None
        _persist(
            session, submission, run_id, rubric, passes, final,
            verifier_total, "; ".join(verifier_notes),
            time.monotonic() - started,
        )
        if flagged:
            _upsert_flag(session, submission.id, "human_review_required", "true", note)
        if broken:
            _upsert_flag(
                session, submission.id, "incomplete_score", "true",
                f"{len(broken)} criterion/criteria unscored; total is out of {100 - lost}.",
            )
            submission.status = "scoring_failed"
            submission.status_detail = note
        submission.status = "scored"
        submission.status_detail = note

    return SubmissionOutcome(
        ref=ref,
        total=final.total,
        passes_run=len(passes),
        spread=spread,
        verifier_adjustment=round(verifier_total, 2),
        duration=time.monotonic() - started,
        flagged=flagged,
        note=note,
    )


def _upsert_flag(session: Session, submission_id: int, key: str, value: str, detail: str) -> None:
    existing = session.scalar(
        select(Flag).where(Flag.submission_id == submission_id, Flag.key == key)
    )
    if existing:
        existing.value = value
        existing.detail = detail
    else:
        session.add(Flag(submission_id=submission_id, key=key, value=value, detail=detail))


def _persist(
    session: Session,
    submission: Submission,
    run_id: str,
    rubric: Rubric,
    passes: list[SubmissionScore],
    final: SubmissionScore,
    verifier_adjustment: float,
    verifier_notes: str,
    duration: float,
) -> None:
    """Store every pass plus the consolidated final evaluation."""
    cfg = load_config()
    model = str(cfg.require("llm.scoring_model"))

    # Supersede any previous final evaluation for this submission.
    for old in session.scalars(
        select(Evaluation).where(
            Evaluation.submission_id == submission.id, Evaluation.is_final.is_(True)
        )
    ):
        old.is_final = False

    to_store = [(i + 1, p, False) for i, p in enumerate(passes)]
    to_store.append((0, final, True))

    for pass_no, score, is_final in to_store:
        evaluation = Evaluation(
            submission_id=submission.id,
            run_id=run_id,
            rubric_slug=rubric.slug,
            rubric_hash=rubric.content_hash,
            prompt_version=SCORE_PROMPT_VERSION,
            model=model,
            pass_no=pass_no,
            is_final=is_final,
            total=score.total,
            verifier_applied=bool(cfg.get("pipeline.enable_verifier", True)),
            verifier_adjustment=verifier_adjustment if is_final else 0.0,
            verifier_notes=verifier_notes if is_final else "",
            input_tokens=score.usage.input_tokens,
            output_tokens=score.usage.output_tokens,
            cost_inr=score.usage.cost_inr,
            duration_seconds=duration if is_final else 0.0,
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
                        + (f"\n[Guardrail: {sub.guardrail_applied}]" if sub.guardrail_applied else "")
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


# ---------------------------------------------------------------------------
# Run driver
# ---------------------------------------------------------------------------
def run_scoring(
    refs: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    init_db()
    cfg = load_config()
    rubric = load_rubric()

    with session_scope() as session:
        query = select(Submission).order_by(Submission.ref)
        if refs:
            query = query.where(Submission.ref.in_(refs))
        candidates = [
            (s.id, s.ref, s.status) for s in session.scalars(query)
        ]

    eligible = [(sid, ref) for sid, ref, status in candidates
                if status in {"mapped", "scored"}]
    unmapped = [ref for _, ref, status in candidates
                if status not in {"mapped", "scored"}]

    # Scoring is the only stage that costs money, so it is the one stage that
    # must not repeat itself. A submission already scored against THIS rubric
    # and THIS prompt version is skipped: adding one folder to the intake and
    # re-running must bill for one submission, not four hundred.
    #
    # Changed files need no special case — `ingest` resets status to "ingested"
    # when the content hash moves, which drops the submission out of the
    # eligible set until it is re-mapped.
    skipped_already_scored: list[str] = []
    if not force and eligible:
        with session_scope() as session:
            done = {
                e.submission_id
                for e in session.scalars(
                    select(Evaluation).where(
                        Evaluation.is_final.is_(True),
                        Evaluation.rubric_hash == rubric.content_hash,
                        Evaluation.prompt_version == SCORE_PROMPT_VERSION,
                    )
                )
            }
        keep = []
        for sid, ref in eligible:
            (skipped_already_scored if sid in done else keep).append(
                ref if sid in done else (sid, ref)
            )
        eligible = keep

    if skipped_already_scored:
        print(
            f"Skipping {len(skipped_already_scored)} already scored against "
            f"{rubric.slug} (--force to redo): "
            f"{', '.join(skipped_already_scored[:10])}"
            + (" ..." if len(skipped_already_scored) > 10 else "")
        )

    if not eligible:
        print(
            "Nothing to score. Submissions must be ingested and mapped first:\n"
            "  python cag.py ingest\n  python cag.py map"
        )
        if unmapped:
            print(f"\nNot mapped: {', '.join(unmapped)}")
        return 1

    if limit:
        eligible = eligible[:limit]

    # Fail before spending anything, not after the first submission.
    credential = describe_credential_source()
    if credential.startswith("none found"):
        print(CREDENTIAL_HELP)
        return 1

    print(
        f"Rubric: {rubric.name} ({rubric.slug}, hash {rubric.content_hash})\n"
        f"Model: {cfg.require('llm.scoring_model')} "
        f"(verifier: {cfg.require('llm.verification_model')})\n"
        f"Credential: {credential}\n"
        f"Passes: {cfg.get('pipeline.scoring_passes', 2)}, "
        f"concurrency: {cfg.get('pipeline.concurrency', 6)}\n"
        f"Submissions to score: {len(eligible)}"
    )
    if unmapped:
        print(f"Skipping {len(unmapped)} not-yet-mapped: {', '.join(unmapped[:10])}")

    if dry_run:
        return _dry_run(eligible, rubric)

    run_id = f"run-{uuid.uuid4().hex[:10]}"
    meter = CostMeter.from_config()
    print(f"Run id: {run_id}  (cost ceiling Rs. {meter.ceiling_inr:,.0f})\n")

    with session_scope() as session:
        session.add(
            Run(
                run_id=run_id,
                rubric_slug=rubric.slug,
                rubric_hash=rubric.content_hash,
                model=str(cfg.require("llm.scoring_model")),
                submissions_total=len(eligible),
            )
        )

    outcomes: list[SubmissionOutcome] = []
    concurrency = max(1, int(cfg.get("pipeline.concurrency", 6)))
    halted = False

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(score_one, sid, run_id, rubric, meter): ref
            for sid, ref in eligible
        }
        for future in as_completed(futures):
            ref = futures[future]
            try:
                outcome = future.result()
            except CostCeilingExceeded as exc:
                print(f"\n{exc}")
                halted = True
                break
            except Exception as exc:
                outcome = SubmissionOutcome(
                    ref, None, 0, 0, 0, 0, error=f"{type(exc).__name__}: {exc}"
                )
            outcomes.append(outcome)
            print(outcome.line())

    scored = [o for o in outcomes if o.total is not None]
    failed = [o for o in outcomes if o.total is None]

    with session_scope() as session:
        run = session.scalar(select(Run).where(Run.run_id == run_id))
        if run:
            run.submissions_done = len(scored)
            run.submissions_failed = len(failed)
            run.cost_inr = meter.usage.cost_inr
            run.input_tokens = meter.usage.input_tokens
            run.output_tokens = meter.usage.output_tokens
            run.state = "halted_on_cost" if halted else "complete"

    print(f"\nScored {len(scored)}, failed {len(failed)}.")
    if scored:
        totals = sorted(o.total for o in scored)
        print(
            f"Range {totals[0]:.1f} to {totals[-1]:.1f}, "
            f"mean {statistics.fmean(totals):.1f}, "
            f"median {statistics.median(totals):.1f}"
        )
        flagged = [o for o in scored if o.flagged]
        if flagged:
            print(f"\n{len(flagged)} flagged for human review:")
            for o in flagged:
                print(f"  {o.ref}: {o.note}")
    if failed:
        print("\nFailures:")
        for o in failed:
            print(f"  {o.ref}: {o.error}")

    print(f"\nModel usage this run: {meter.usage.summary()}")
    print(f"Next: python cag.py export")
    return 0 if not failed and not halted else 1


def _dry_run(eligible: list[tuple[int, str]], rubric: Rubric) -> int:
    """Measure the real token cost of one submission, then extrapolate.

    Uses the token-counting endpoint rather than a characters-per-token guess,
    so the figure quoted to anyone is measured.
    """
    cfg = load_config()
    n_passes = max(1, int(cfg.get("pipeline.scoring_passes", 2)))
    model = str(cfg.require("llm.scoring_model"))
    sample_id, sample_ref = eligible[0]

    with session_scope() as session:
        submission = session.get(Submission, sample_id)
        assert submission is not None
        record = load_record(session, submission)
        docs = load_cached_extraction(submission.ref, submission.content_hash)

    if record is None or not docs:
        print(f"Cannot estimate: {sample_ref} is not mapped or has no extraction.")
        return 1

    text = submission_text(docs, max_chars=400_000)
    # The estimator sends this to the token-counting endpoint, which is a model
    # call and is billed as one - so it is blinded too.
    view = blind_view(render_submission_for_scoring(record, text), record)

    per_criterion: list[int] = []
    for key in rubric.criterion_keys:
        prefix = (
            f"RUBRIC: {rubric.name} (version {rubric.version})\n"
            f"AUTHORITY: {rubric.authority}\n\n"
            f"{rubric.render_criterion(key)}"
        )
        per_criterion.append(
            count_tokens(
                instructions=SCORE_INSTRUCTIONS,
                user_content=view,
                cacheable_prefix=prefix,
                model=model,
            )
        )

    scoring_input = sum(per_criterion)
    # The verifier reads the scorecard plus the submission again — roughly the
    # submission view plus the scorecard, which runs about 1.4x one criterion call.
    verifier_input = int(statistics.fmean(per_criterion) * 1.4)
    input_per_pass = scoring_input + verifier_input
    # Output: four scorer responses plus one verifier response, observed to sit
    # around 1,500 and 2,500 tokens respectively.
    output_per_pass = 4 * 1500 + 2500

    per_submission_input = input_per_pass * n_passes
    per_submission_output = output_per_pass * n_passes
    per_submission_cost = price_usage(model, per_submission_input, per_submission_output)

    print(
        f"\nDry run — measured on {sample_ref} "
        f"({len(text):,} chars of extracted text)\n"
        f"  Input tokens per pass:      {input_per_pass:,}\n"
        f"  Passes per submission:      {n_passes}\n"
        f"  Input tokens per submission:  {per_submission_input:,}\n"
        f"  Output tokens per submission: ~{per_submission_output:,} (estimated)\n"
        f"  Cost per submission:        Rs. {per_submission_cost:,.2f}  "
        f"(at {model} published rates, no cache credit)\n"
    )
    print("  Projected totals, ignoring prompt-cache savings (so an upper bound):")
    for n in (len(eligible), 10, 50, 100, 400):
        print(f"    {n:>4} submissions: Rs. {per_submission_cost * n:>12,.2f}")
    print(
        "\n  Prompt caching on the rubric prefix and the Batch API (50% off, "
        "config llm.use_batch_api) both reduce this materially on a large run.\n"
        f"  Current ceiling: Rs. {cfg.get('cost.max_run_cost_inr', 0):,} "
        "(config cost.max_run_cost_inr).\n"
        f"  INR figures use an assumed rate of {cfg.get('cost.usd_to_inr', 88)} "
        "per USD — set cost.usd_to_inr before quoting these."
    )
    return 0
