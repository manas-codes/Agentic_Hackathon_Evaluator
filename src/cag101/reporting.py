"""Shared query layer for the dashboard and the Excel export.

Both surfaces must show the same numbers. Putting the assembly here rather than
in each of them is what guarantees that the figure a committee reads on screen
is the figure in the workbook they take away.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Cluster,
    ClusterMember,
    CriterionScore,
    Evaluation,
    Evidence,
    Flag,
    Override,
    ReviewDecision,
    RestrictedIdentityRow,
    Run,
    Submission,
    SubmissionFile,
)
from .rubric import Rubric, load_rubric

# Statuses that count as "evaluated" on the overview tab.
SCORED_STATUSES = {"scored"}

# Statuses where evaluation is blocked on something PPG must obtain or fix —
# not on evaluation capacity. These are reported separately from "pending".
BLOCKED_STATUSES = {
    "no_files_submitted",
    "extraction_failed",
    "mapping_failed",
    "scoring_failed",
}


@dataclass
class SubScoreView:
    criterion: str
    criterion_name: str
    sub_criterion: str
    sub_name: str
    score: float
    max_score: int
    band: str
    rationale: str
    confidence: float
    evidence: list[dict[str, str]] = field(default_factory=list)


@dataclass
class CriterionView:
    key: str
    name: str
    max_points: int
    machine_score: float
    human_score: float | None
    # the note the evaluator gave when amending the mark, so the edit dialog can
    # show what was said last time rather than opening blank
    override_justification: str = ""
    sub_scores: list[SubScoreView] = field(default_factory=list)

    @property
    def effective_score(self) -> float:
        return self.human_score if self.human_score is not None else self.machine_score

    @property
    def is_overridden(self) -> bool:
        return self.human_score is not None


@dataclass
class ScorecardView:
    ref: str
    submission_id: int
    title: str
    folder_name: str
    status: str
    status_detail: str
    thematic_area: str
    development_stage: str
    formation_category: str
    office: str
    criteria: list[CriterionView] = field(default_factory=list)
    flags: dict[str, str] = field(default_factory=dict)
    flag_details: dict[str, str] = field(default_factory=dict)
    files: list[dict[str, Any]] = field(default_factory=list)
    rubric_slug: str = ""
    rubric_hash: str = ""
    model: str = ""
    passes: list[dict[str, Any]] = field(default_factory=list)
    verifier_adjustment: float = 0.0
    verifier_notes: str = ""
    cost_inr: float = 0.0
    remarks: str = ""
    identity: dict[str, Any] | None = None
    cluster_label: str = ""
    cluster_members: list[str] = field(default_factory=list)
    evaluated: bool = False
    # committee sign-off, separate from whether a score exists
    review_decision: str = "pending"
    review_note: str = ""
    review_reviewer: str = ""
    review_decided_at: Any | None = None

    @property
    def machine_total(self) -> float:
        return round(sum(c.machine_score for c in self.criteria), 1)

    @property
    def total(self) -> float:
        return round(sum(c.effective_score for c in self.criteria), 1)

    @property
    def is_overridden(self) -> bool:
        return any(c.is_overridden for c in self.criteria)

    @property
    def needs_review(self) -> bool:
        return self.flags.get("human_review_required") == "true"

    @property
    def is_approved(self) -> bool:
        return self.review_decision == "approved"

    @property
    def below_threshold(self) -> bool:
        """Scored, but under the working qualification bar.

        Tinting these rows is a triage aid, not a decision: the scheme sets no
        cut-off, so a low row is one the committee still has to read.
        """
        return self.evaluated and self.total < QUALIFY_AT

    def criterion(self, key: str) -> CriterionView | None:
        return next((c for c in self.criteria if c.key == key), None)


# ---------------------------------------------------------------------------
# Building scorecards
# ---------------------------------------------------------------------------
def build_scorecard(
    session: Session,
    submission: Submission,
    rubric: Rubric,
    include_identity: bool = False,
    include_evidence: bool = True,
) -> ScorecardView:
    card = ScorecardView(
        ref=submission.ref,
        submission_id=submission.id,
        title=submission.title or "",
        folder_name=submission.folder_name,
        status=submission.status,
        status_detail=submission.status_detail or "",
        thematic_area=submission.thematic_area,
        development_stage=submission.development_stage,
        formation_category=submission.formation_category,
        office=submission.office or "",
    )

    for flag in session.scalars(select(Flag).where(Flag.submission_id == submission.id)):
        card.flags[flag.key] = flag.value
        if flag.detail:
            card.flag_details[flag.key] = flag.detail

    card.files = [
        {
            "name": f.name,
            "relative_path": f.relative_path,
            "kind": f.kind,
            "size_kb": round(f.size_bytes / 1024, 1),
            "status": f.extraction_status,
            "detail": f.extraction_detail,
            "chars": f.char_count,
            "ocr_used": f.ocr_used,
            "media_derived": f.media_derived,
        }
        for f in session.scalars(
            select(SubmissionFile)
            .where(SubmissionFile.submission_id == submission.id)
            .order_by(SubmissionFile.relative_path)
        )
    ]

    overrides = {
        o.criterion: o
        for o in session.scalars(
            select(Override).where(Override.submission_id == submission.id)
        )
    }

    decision = session.scalar(
        select(ReviewDecision).where(ReviewDecision.submission_id == submission.id)
    )
    if decision is not None:
        card.review_decision = decision.decision
        card.review_note = decision.note or ""
        card.review_reviewer = decision.reviewer.username if decision.reviewer else ""
        card.review_decided_at = decision.decided_at

    final = session.scalar(
        select(Evaluation).where(
            Evaluation.submission_id == submission.id, Evaluation.is_final.is_(True)
        )
    )

    if final is not None:
        card.evaluated = True
        card.rubric_slug = final.rubric_slug
        card.rubric_hash = final.rubric_hash
        card.model = final.model
        card.verifier_adjustment = final.verifier_adjustment
        card.verifier_notes = final.verifier_notes or ""
        card.cost_inr = final.cost_inr
        card.remarks = getattr(final, "remarks", "") or ""

        scores = list(
            session.scalars(
                select(CriterionScore).where(CriterionScore.evaluation_id == final.id)
            )
        )
        by_criterion: dict[str, list[CriterionScore]] = {}
        for row in scores:
            by_criterion.setdefault(row.criterion, []).append(row)

        for spec in rubric.criteria:
            rows = by_criterion.get(spec.key, [])
            sub_views: list[SubScoreView] = []
            sub_specs = {s.key: s for s in spec.sub_criteria}
            for row in rows:
                sub_spec = sub_specs.get(row.sub_criterion)
                evidence: list[dict[str, str]] = []
                if include_evidence:
                    evidence = [
                        {
                            "quote": e.quote,
                            "source_file": e.source_file,
                            "locator": e.locator,
                        }
                        for e in session.scalars(
                            select(Evidence).where(Evidence.criterion_score_id == row.id)
                        )
                    ]
                sub_views.append(
                    SubScoreView(
                        criterion=spec.key,
                        criterion_name=spec.name,
                        sub_criterion=row.sub_criterion,
                        sub_name=sub_spec.name if sub_spec else row.sub_criterion,
                        score=row.score,
                        max_score=row.max_score,
                        band=row.band,
                        rationale=row.rationale,
                        confidence=row.confidence,
                        evidence=evidence,
                    )
                )
            # Keep the rubric's declared order, not the database's.
            order = [s.key for s in spec.sub_criteria]
            sub_views.sort(key=lambda v: order.index(v.sub_criterion)
                           if v.sub_criterion in order else 99)

            override = overrides.get(spec.key)
            card.criteria.append(
                CriterionView(
                    key=spec.key,
                    name=spec.name,
                    max_points=spec.weight,
                    machine_score=round(sum(v.score for v in sub_views), 1),
                    human_score=override.human_score if override else None,
                    override_justification=override.justification if override else "",
                    sub_scores=sub_views,
                )
            )

        card.passes = [
            {
                "pass_no": e.pass_no,
                "total": e.total,
                "model": e.model,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cost_inr": e.cost_inr,
                "created_at": e.created_at,
            }
            for e in session.scalars(
                select(Evaluation)
                .where(
                    Evaluation.submission_id == submission.id,
                    Evaluation.is_final.is_(False),
                )
                .order_by(Evaluation.pass_no)
            )
        ]
    else:
        # Not yet scored — still show the criteria so the card renders.
        for spec in rubric.criteria:
            card.criteria.append(
                CriterionView(
                    key=spec.key,
                    name=spec.name,
                    max_points=spec.weight,
                    machine_score=0.0,
                    human_score=None,
                )
            )

    if include_identity:
        row = session.scalar(
            select(RestrictedIdentityRow).where(
                RestrictedIdentityRow.submission_id == submission.id
            )
        )
        card.identity = row.payload if row else None

    membership = session.scalar(
        select(ClusterMember).where(ClusterMember.submission_id == submission.id)
    )
    if membership:
        cluster = session.get(Cluster, membership.cluster_id)
        if cluster:
            card.cluster_label = cluster.label
            card.cluster_members = [
                s.ref
                for s in session.scalars(
                    select(Submission)
                    .join(ClusterMember, ClusterMember.submission_id == Submission.id)
                    .where(
                        ClusterMember.cluster_id == cluster.id,
                        Submission.id != submission.id,
                    )
                    .order_by(Submission.ref)
                )
            ]

    return card


def all_scorecards(
    session: Session,
    rubric: Rubric | None = None,
    include_identity: bool = False,
    include_evidence: bool = True,
) -> list[ScorecardView]:
    rubric = rubric or load_rubric()
    cards = [
        build_scorecard(session, s, rubric, include_identity, include_evidence)
        for s in session.scalars(select(Submission).order_by(Submission.ref))
    ]
    return cards


def rank_scorecards(cards: list[ScorecardView]) -> None:
    """Attach overall and within-stream ranks. Only evaluated cards are ranked."""
    evaluated = [c for c in cards if c.evaluated]
    for index, card in enumerate(
        sorted(evaluated, key=lambda c: -c.total), start=1
    ):
        setattr(card, "rank_overall", index)

    by_stream: dict[str, list[ScorecardView]] = {}
    for card in evaluated:
        by_stream.setdefault(card.formation_category, []).append(card)
    for stream_cards in by_stream.values():
        for index, card in enumerate(
            sorted(stream_cards, key=lambda c: -c.total), start=1
        ):
            setattr(card, "rank_in_stream", index)

    for card in cards:
        if not hasattr(card, "rank_overall"):
            setattr(card, "rank_overall", None)
            setattr(card, "rank_in_stream", None)


# ---------------------------------------------------------------------------
# Overview statistics
# ---------------------------------------------------------------------------
@dataclass
class Overview:
    total_submissions: int = 0
    evaluated: int = 0
    pending: int = 0
    failed: int = 0
    needs_review: int = 0
    overridden: int = 0
    qualified: int = 0            # evaluated and scoring above QUALIFY_AT
    top_ref: str = ""
    top_title: str = ""
    qualified_pct: float = 0.0
    mix: dict[str, int] = field(default_factory=dict)
    review_mix: list[int] = field(default_factory=list)
    mean_score: float = 0.0
    median_score: float = 0.0
    min_score: float = 0.0
    max_score: float = 0.0
    by_theme: dict[str, int] = field(default_factory=dict)
    by_formation: dict[str, int] = field(default_factory=dict)
    by_stage: dict[str, int] = field(default_factory=dict)
    by_completeness: dict[str, int] = field(default_factory=dict)
    by_evidence_strength: dict[str, int] = field(default_factory=dict)
    histogram: list[tuple[str, int]] = field(default_factory=list)
    # pre-computed arc geometry so the template stays free of arithmetic
    histogram_pie: list[dict[str, Any]] = field(default_factory=list)
    criterion_gauges: list[dict[str, Any]] = field(default_factory=list)
    criterion_means: list[tuple[str, float, int]] = field(default_factory=list)
    top: list[tuple[str, str, float]] = field(default_factory=list)
    cluster_count: int = 0
    clustered_submissions: int = 0
    naeg_candidates: int = 0
    total_cost_inr: float = 0.0
    last_run: dict[str, Any] | None = None
    rubric_name: str = ""
    rubric_slug: str = ""
    rubric_hash: str = ""


# Short column headings for the scorecard table. Consistently abbreviated so
# the header row reads as one set rather than a mix of full and clipped names;
# the full criterion name and its scheme focus stay available on hover.
CRITERION_SHORT = {
    # CAG 101
    "innovation_originality": "Innovation",
    "institutional_relevance": "Relevance",
    "feasibility_scalability": "Feasibility",
    "potential_impact": "Impact",
    # Category-I
    "the_solution": "Solution",
    "benefits": "Benefits",
    "sustainability_replicability": "Sustainability",
    "change_management": "Change mgmt",
}

# Criterion identity. The same hue means the same criterion on the dashboard,
# in the table header rule, in the score cue and on the detail page — colour
# follows the entity, never its rank.
CRITERION_HUE = {
    "innovation_originality": "innovation",
    "institutional_relevance": "relevance",
    "feasibility_scalability": "feasibility",
    "potential_impact": "impact",
    "the_solution": "innovation",
    "benefits": "relevance",
    "sustainability_replicability": "feasibility",
    "change_management": "impact",
}

THEME_LABELS = {
    "A": "A — Audit Planning & Risk",
    "B": "B — Business Process Re-engineering",
    "C": "C — Institutional & Capacity",
    "D": "D — Outreach & Engagement",
    "unclear": "Not clearly stated",
}

FORMATION_LABELS = {
    "union": "Union formations",
    "state": "State formations",
    "other": "Other formations",
    "special_category_state": "Special Category States",
    "unknown": "Unclassified",
}

STAGE_LABELS = {
    "concept": "Concept / idea",
    "early_prototype": "Early prototype",
    "pilot_tested": "Pilot tested",
    "partially_deployed": "Partially deployed",
    "ready_for_scale": "Ready for scale",
    "other": "Other",
    "not_stated": "Not stated",
}

# Five bands, not seven: the 7-step colour ramp failed the validator's
# adjacent-lightness gate, and five bins read better at this sample size.
# ---------------------------------------------------------------------------
# Presenting a rationale
# ---------------------------------------------------------------------------
# Abbreviations that end in a full stop and must not be treated as the end of a
# sentence. Short, because the rationales are departmental English, not prose.
_ABBREV = (
    "no.", "nos.", "vs.", "e.g.", "i.e.", "etc.", "viz.", "cf.", "fig.",
    "para.", "sec.", "rs.", "approx.", "govt.", "dept.", "mr.", "ms.", "dr.",
)


def as_bullets(text: str) -> list[str]:
    """Split a rationale into one bullet per finding.

    The panel has to be scannable — an evaluator with 400 submissions reads the
    reasoning, not an essay. Splitting is mechanical and lossless: every
    sentence becomes a bullet, in order, nothing dropped and nothing rewritten.
    Selecting "only the important ones" would mean an assistant deciding which
    parts of a scoring rationale the committee may see, which is not a decision
    it should be making.
    """
    if not text:
        return []

    out: list[str] = []
    buffer: list[str] = []
    for token in str(text).split():
        buffer.append(token)
        low = token.lower().rstrip("”\")")
        if low.endswith((".", "!", "?")) and not low.endswith(_ABBREV):
            # a single initial ("A.") is not a sentence end either
            if not (len(low) == 2 and low[0].isalpha()):
                out.append(" ".join(buffer))
                buffer = []
    if buffer:
        out.append(" ".join(buffer))

    # Very short fragments belong with the sentence before them rather than
    # standing alone as a bullet that says nothing.
    merged: list[str] = []
    for part in out:
        if merged and len(part) < 30:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


# Flag values and details are written for the operator running the pipeline.
# On screen they are read by a committee, so the sentences that describe the
# tooling are replaced with one that describes the consequence. Everything else
# in the detail — untranscribed video, a missing form, an office that cannot be
# determined — is a real finding the committee needs and is left untouched.
PUBLIC_FLAG_VALUES = {
    "in_session_single_pass": "single pass, second pass pending",
}

# A sentence mentioning any of these is describing the machinery, not the
# submission, and is replaced rather than shown.
_TOOLING_MARKERS = (
    "automated pipeline", "adversarial verifier", "adversarial verification",
    "api credential", "api key", "scoring pass with no independent",
    "cross-pass disagreement", "lowering-only",
)

_ROUTE_REPLACEMENT = (
    "Scored in a single assessment pass with no independent second-pass "
    "verification, so the cross-check between passes was not applied. Treat "
    "the score as a first read and confirm it before it is relied on."
)


def public_detail(text: str) -> str:
    """Strip sentences that describe the tooling, keep every real finding."""
    if not text:
        return ""
    kept, dropped = [], False
    for sentence in as_bullets(text):
        low = sentence.lower()
        if any(marker in low for marker in _TOOLING_MARKERS):
            dropped = True
            continue
        kept.append(sentence)
    if dropped:
        kept.insert(0, _ROUTE_REPLACEMENT)
    return " ".join(kept)


# Provenance that names the tooling is kept in the database and the audit log,
# never rendered: a committee reads a score against the rubric and the evidence,
# and naming the machinery on the page invites argument about the tool instead
# of the submission.
HIDDEN_FLAG_KEYS = {"model", "evaluator_model", "verifier_model"}


def public_flags(card: "ScorecardView") -> list[dict[str, str]]:
    """Flag rows as they should appear on screen or in an export."""
    rows = []
    for key, value in card.flags.items():
        if key in HIDDEN_FLAG_KEYS:
            continue
        rows.append({
            "key": key.replace("_", " "),
            "value": PUBLIC_FLAG_VALUES.get(value, value),
            "detail": public_detail(card.flag_details.get(key, "")),
        })
    return rows


PUBLIC_EVALUATOR_LABEL = "Automated assessment (see run id for the full record)"


# The bar a submission has to clear to count as qualified on the dashboard.
# Not a rule from the scheme — a working threshold for PPG's own triage, which
# is why it is named and configurable rather than buried in a template.
QUALIFY_AT = 70.0

# Donut geometry. Computed here rather than in the template so the arc maths is
# testable and the template carries no arithmetic.
DONUT_R = 70.0
DONUT_C = 2 * 3.141592653589793 * DONUT_R

# A hairline gap between donut segments, in the same units as the arc lengths.
# Two adjacent bands of an ordinal ramp are close by design; a gap makes the
# boundary structural rather than purely chromatic, which is what keeps the
# ring readable in greyscale and on a projector.
DONUT_GAP = 5.0

# Small arc gauge inside a KPI tile. Drawn in a 36x36 viewBox so the stroke
# width is expressed in the same units whatever size the tile renders at.
ARC_R = 15.5
ARC_C = 2 * 3.141592653589793 * ARC_R

HISTOGRAM_BANDS = [
    ("Below 50", 0, 50),
    ("50 - 59", 50, 60),
    ("60 - 69", 60, 70),
    ("70 - 79", 70, 80),
    ("80 and above", 80, 101),
]


def build_overview(
    session: Session, cards: list[ScorecardView], rubric: Rubric | None = None
) -> Overview:
    rubric = rubric or load_rubric()
    ov = Overview(
        total_submissions=len(cards),
        rubric_name=rubric.name,
        rubric_slug=rubric.slug,
        rubric_hash=rubric.content_hash,
    )

    evaluated = [c for c in cards if c.evaluated]
    ov.evaluated = len(evaluated)
    # A submission that arrived with no files, or whose files could not be read,
    # is not "awaiting evaluation" — it cannot be evaluated until PPG obtains the
    # material. Counting it as pending would overstate how much work remains and
    # understate how much needs chasing.
    ov.failed = sum(1 for c in cards if c.status in BLOCKED_STATUSES)
    ov.pending = ov.total_submissions - ov.evaluated - ov.failed
    ov.needs_review = sum(1 for c in cards if c.needs_review)
    ov.overridden = sum(1 for c in cards if c.is_overridden)
    ov.qualified = sum(1 for c in evaluated if c.total > QUALIFY_AT)
    ov.naeg_candidates = sum(
        1 for c in cards if c.flags.get("naeg_candidate_hint") == "true"
    )
    ov.total_cost_inr = round(sum(c.cost_inr for c in cards), 2)

    if evaluated:
        totals = [c.total for c in evaluated]
        ov.mean_score = round(statistics.fmean(totals), 1)
        ov.median_score = round(statistics.median(totals), 1)
        ov.min_score = round(min(totals), 1)
        ov.max_score = round(max(totals), 1)

        counts = Counter()
        for total in totals:
            for label, lo, hi in HISTOGRAM_BANDS:
                if lo <= total < hi:
                    counts[label] += 1
                    break
        ov.histogram = [(label, counts.get(label, 0)) for label, _, _ in HISTOGRAM_BANDS]

        for spec in rubric.criteria:
            values = [
                c.criterion(spec.key).effective_score
                for c in evaluated
                if c.criterion(spec.key) is not None
            ]
            if values:
                ov.criterion_means.append(
                    (spec.name, round(statistics.fmean(values), 1), spec.weight)
                )

        ov.top = [
            (c.ref, c.title or c.folder_name, c.total)
            for c in sorted(evaluated, key=lambda c: -c.total)[:10]
        ]
        if ov.top:
            ov.top_ref, ov.top_title, _ = ov.top[0]

        # Part-to-whole, so a ring is legitimate here: the bands are counts of
        # the same population and they sum to it. Ordered bands, so a ramp
        # light to dark rather than a categorical rainbow.
        total_evaluated = len(evaluated) or 1
        cursor = 0.0
        for index, (label, count) in enumerate(ov.histogram, start=1):
            pct = count / total_evaluated * 100
            arc = pct / 100 * DONUT_C
            # a full ring has no boundary to show, so it keeps no gap
            drawn = arc if pct >= 99.99 else max(arc - DONUT_GAP, 0.0)
            ov.histogram_pie.append({
                "label": label, "count": count, "pct": round(pct, 1),
                "step": index,
                # stroke-dasharray: the visible arc, then a gap of the rest
                "dash": round(drawn, 2),
                "gap": round(DONUT_C - drawn, 2),
                # Placement is a rotation, not a dash offset. Keeping
                # stroke-dashoffset free means it can animate from the full arc
                # length down to zero, which is what draws the sweep.
                "rot": round(cursor / 100 * 360, 3),
                "len": round(drawn, 2),
            })
            cursor += pct

        # Each criterion against its own maximum is a real part-to-whole, so a
        # ring per criterion is honest where one pie across the four would not
        # be: four means out of 25 each are not shares of a single total.
        for spec in rubric.criteria:
            values = [
                c.criterion(spec.key).effective_score
                for c in evaluated
                if c.criterion(spec.key) is not None
            ]
            if not values:
                continue
            mean = round(statistics.fmean(values), 1)
            ov.criterion_gauges.append({
                "key": spec.key,
                "name": spec.name,
                "short": CRITERION_SHORT.get(spec.key, spec.name),
                "hue": CRITERION_HUE.get(spec.key, "innovation"),
                "value": mean,
                "max": spec.weight,
                "pct": round(mean / spec.weight * 100, 1),
                "dash": round(mean / spec.weight * ARC_C, 2),
                "gap": round(ARC_C - mean / spec.weight * ARC_C, 2),
            })

    ov.by_theme = _labelled_counts(cards, "thematic_area", THEME_LABELS)
    ov.by_formation = _labelled_counts(cards, "formation_category", FORMATION_LABELS)
    ov.by_stage = _labelled_counts(cards, "development_stage", STAGE_LABELS)
    ov.by_completeness = dict(
        Counter(c.flags.get("completeness", "not assessed") for c in cards)
    )
    ov.by_evidence_strength = dict(
        Counter(c.flags.get("evidence_strength", "not assessed") for c in cards)
    )

    ov.cluster_count = session.scalar(select(func.count()).select_from(Cluster)) or 0
    ov.clustered_submissions = (
        session.scalar(select(func.count()).select_from(ClusterMember)) or 0
    )

    # Inputs for the KPI tile mini-charts. Real proportions, no decoration.
    ov.mix = {
        "evaluated": ov.evaluated,
        "pending": ov.pending,
        "blocked": ov.failed,
    }
    ov.qualified_pct = round(ov.qualified / ov.evaluated * 100, 1) if ov.evaluated else 0.0
    # review backlog by how bad the reason is, for the tile sparkline
    ov.review_mix = [
        sum(1 for c in cards if c.status == "no_files_submitted"),
        sum(1 for c in cards if c.flags.get("completeness") == "incomplete"
            and c.status != "no_files_submitted"),
        sum(1 for c in cards if c.needs_review
            and c.flags.get("completeness") != "incomplete"
            and c.status != "no_files_submitted"),
    ]

    run = session.scalar(select(Run).order_by(Run.started_at.desc()))
    if run:
        ov.last_run = {
            "run_id": run.run_id,
            "rubric": run.rubric_slug,
            "model": run.model,
            "total": run.submissions_total,
            "done": run.submissions_done,
            "failed": run.submissions_failed,
            "cost_inr": round(run.cost_inr, 2),
            "state": run.state,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }
    return ov


def _labelled_counts(
    cards: list[ScorecardView], attribute: str, labels: dict[str, str]
) -> dict[str, int]:
    counts = Counter(getattr(c, attribute) or "unknown" for c in cards)
    ordered: dict[str, int] = {}
    for key, label in labels.items():
        if counts.get(key):
            ordered[label] = counts[key]
    for key, count in counts.items():
        if key not in labels:
            ordered[key] = count
    return ordered
