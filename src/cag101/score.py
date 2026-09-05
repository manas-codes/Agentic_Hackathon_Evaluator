"""The scoring agents. One agent per criterion, run in parallel, blind.

Three things make the output defensible rather than merely plausible:

* **Blind inputs.** Names and office are masked before scoring, so a score
  cannot favour a person or a formation. If a committee asks whether the machine
  preferred certain offices, the answer is that it could not see them.
* **Evidence or nothing.** Every sub-score carries verbatim quotes or the
  marker NO_EVIDENCE_FOUND, and a *deterministic* guardrail — not a model
  instruction — caps an unevidenced sub-score at the top of the Weak band. A
  model cannot talk its way past that.
* **Criterion isolation.** Each agent sees only its own slice of the rubric, so
  a strong solution does not inflate the impact score by halo, and disagreement
  between passes means something.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .deidentify import deidentify
from .llm import CostMeter, Usage, call_json
from .rubric import Criterion, Rubric
from .schema import CanonicalRecord, FormationCategory

PROMPT_VERSION = "score-v1"

INSTRUCTIONS = """\
You are an evaluator assisting a Filtering Committee of the Office of the \
Comptroller and Auditor General of India, assessing submissions to the CAG 101 \
Innovation Ideas Initiative.

You assess ONE criterion. You will be given that criterion's sub-criteria, the \
scoring bands, and one submission. Score each sub-criterion independently.

HOW TO SCORE

Work sub-criterion by sub-criterion. For each one:

1. Find what the submission actually says about it. Search the form fields and \
   every attachment provided.
2. Quote the passage that bears on it, verbatim, with the source file and a \
   locator (for example "form 2.6", "deck slide 4", "video transcript"). Quote \
   the submitter's words — never your paraphrase.
3. Place it in a band using the band definitions given, then choose a point \
   value inside that band's range for the sub-criterion's maximum.
4. State in one or two sentences why it sits in that band, referring to what the \
   evidence does and does not establish.

RULES THAT OVERRIDE YOUR JUDGEMENT

- If you cannot find anything in the submission bearing on a sub-criterion, \
  return an empty evidence list and score it in the Absent band. Do not reason \
  from what a submission of this type would probably contain. Absence of \
  evidence is the finding.
- Assess what is written, not what could have been written. A good idea \
  described vaguely scores as a vague description. It is not your role to \
  reconstruct the strongest version of the submission.
- Do not reward length, confidence of tone, technical vocabulary, or references \
  to national programmes and policy slogans. Reward specificity, mechanism, and \
  evidence. A plainly written submission with real numbers outranks a polished \
  one without them.
- Quantified claims are only as good as their stated basis. "50% time saving" \
  with no basis is a weaker signal than "about 120 hours per cycle, measured \
  across three pilot units". Where a number and its basis contradict each other, \
  say so and score down.
- Judge proportionately to the type of initiative. A process redesign is not \
  deficient for having no technology stack; the form marks Section 3 optional \
  for non-technology initiatives. Do not penalise a submission for failing to be \
  a different kind of submission.
- You are scoring one criterion only. Do not let a strong or weak impression of \
  the submission overall pull this criterion's scores with it.
- Where text is marked as a machine transcript or OCR output, it may contain \
  errors. Treat its substance as evidence but do not seize on odd wording.

You will see [NAME WITHHELD] and [OFFICE WITHHELD] where identifying details \
were removed. This is deliberate: the evaluation is blind. Do not speculate \
about who submitted this or from which office, and do not treat the masking as \
missing information.

Your output is a recommendation to a committee of human officers, who decide. \
Be accurate and be candid about weakness — an inflated score is more damaging \
here than a harsh one, because it displaces a better submission.
"""

SPECIAL_CATEGORY_ADDENDUM = """\

SPECIAL CATEGORY STATES — ADDITIONAL DIRECTION

This submission comes from a Special Category State formation. Under the scheme, \
the common criteria apply, but you must take due account of the geographical, \
logistical, connectivity, infrastructural and human-resource environment in \
which the innovation was conceived or implemented.

In practice: do not mark down constraints imposed by that operating environment \
— limited bandwidth, small teams, difficult terrain, thin vendor availability. \
Credit ingenuity shown in working within those constraints. Where a solution is \
modest in absolute terms but well matched to a genuinely hard environment, that \
is a strength. This adjusts your judgement, not the weights.
"""


@dataclass
class SubScore:
    sub_criterion: str
    name: str
    score: float
    max_score: int
    band: str
    rationale: str
    confidence: float
    evidence: list[dict[str, str]] = field(default_factory=list)
    guardrail_applied: str = ""

    @property
    def has_evidence(self) -> bool:
        return any(e.get("quote", "").strip() for e in self.evidence)


@dataclass
class CriterionResult:
    criterion: str
    name: str
    max_points: int
    sub_scores: list[SubScore]
    notes: str = ""
    usage: Usage = field(default_factory=Usage)
    error: str = ""

    @property
    def total(self) -> float:
        return round(sum(s.score for s in self.sub_scores), 2)


@dataclass
class SubmissionScore:
    submission_ref: str
    criteria: list[CriterionResult]
    usage: Usage = field(default_factory=Usage)

    @property
    def total(self) -> float:
        return round(sum(c.total for c in self.criteria), 2)

    @property
    def failed(self) -> bool:
        return any(c.error for c in self.criteria)

    def criterion(self, key: str) -> CriterionResult | None:
        return next((c for c in self.criteria if c.criterion == key), None)


# ---------------------------------------------------------------------------
# Schema per criterion, generated from the rubric
# ---------------------------------------------------------------------------
def build_scorer_schema(criterion: Criterion, rubric: Rubric) -> dict[str, Any]:
    sub_keys = [s.key for s in criterion.sub_criteria]
    band_names = [b.name for b in rubric.bands]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["sub_scores", "notes"],
        "properties": {
            "sub_scores": {
                "type": "array",
                "minItems": len(sub_keys),
                "maxItems": len(sub_keys),
                "description": (
                    "Exactly one entry per sub-criterion, in the order given: "
                    + ", ".join(sub_keys)
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "sub_criterion", "band", "score", "rationale",
                        "confidence", "evidence",
                    ],
                    "properties": {
                        "sub_criterion": {"type": "string", "enum": sub_keys},
                        "band": {"type": "string", "enum": band_names},
                        "score": {
                            "type": "number",
                            "description": (
                                "Points awarded, from 0 to this sub-criterion's "
                                "maximum. Must lie inside the band you chose."
                            ),
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One or two sentences. What the evidence "
                                           "does and does not establish.",
                        },
                        "confidence": {
                            "type": "number", "minimum": 0.0, "maximum": 1.0,
                            "description": "Your confidence in this sub-score.",
                        },
                        "evidence": {
                            "type": "array",
                            "description": (
                                "Verbatim quotes supporting the score. Empty array "
                                "if the submission says nothing bearing on this "
                                "sub-criterion."
                            ),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["quote", "source_file", "locator"],
                                "properties": {
                                    "quote": {"type": "string"},
                                    "source_file": {"type": "string"},
                                    "locator": {
                                        "type": "string",
                                        "description": "e.g. 'form 2.6', 'deck slide 4'",
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "notes": {
                "type": "string",
                "description": "Anything the committee should know about this "
                               "criterion for this submission. May be empty.",
            },
        },
    }


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------
def render_submission_for_scoring(
    record: CanonicalRecord, attachment_text: str, blind_office: bool = True
) -> str:
    """The submission as the scorer sees it: structured fields, then raw sources.

    Both are given because they serve different purposes — the structured record
    tells the scorer which form field an answer belongs to, and the raw text lets
    it quote verbatim and reach material the mapper may have compressed.
    """
    content = record.content
    lines: list[str] = [
        f"SUBMISSION {record.submission_ref}",
        f"Formation stream: {content.formation_category.value}",
        "",
        "--- MAPPED FORM FIELDS ---",
        f"2.1 Thematic area: {content.thematic_area.value}",
        f"2.2 Title: {content.title or '(not stated)'}",
        f"2.3 Solution type: {', '.join(t.value for t in content.solution_types) or '(not stated)'}"
        + (f" — other: {content.solution_type_other}" if content.solution_type_other else ""),
        f"2.4 Development stage: {content.development_stage.value}",
        "",
        f"2.5 Problem being solved:\n{content.problem_statement or '(not answered)'}",
        "",
        f"2.6 Proposed solution:\n{content.proposed_solution or '(not answered)'}",
        "",
        f"2.7 Pilot or test evidence:\n{content.pilot_evidence or '(not answered)'}",
        "",
        "--- SECTION 3: TECHNOLOGY (optional for non-technology initiatives) ---",
        f"3.1 Tech stack: {content.tech_stack or '(not answered)'}",
        f"3.2 Licensing: {content.licensing.value}",
        f"3.3 Technical scalability: {content.technical_scalability or '(not answered)'}",
        f"3.4 Data privacy/security: {content.data_privacy_notes or '(not answered)'}",
        "",
        "--- SECTION 4: SCALABILITY AND IMPACT ---",
        f"4.1 Beneficiaries and applicability:\n{content.beneficiaries or '(not answered)'}",
        f"4.2 Scalability mode: {content.scalability_mode.value}",
        f"4.2 Scalability notes: {content.scalability_notes or '(none)'}",
        "",
        "4.2 Impact table:",
    ]
    if content.impact_rows:
        for row in content.impact_rows:
            lines.append(
                f"  - {row.dimension.value}: estimate = {row.estimate or '(blank)'} "
                f"| basis = {row.basis or '(NO BASIS GIVEN)'}"
            )
    else:
        lines.append("  (the impact table was left entirely blank)")

    lines += [
        "",
        "--- SECTION 5: FINANCIAL SNAPSHOT (optional) ---",
        f"One-time cost: {content.one_time_cost or '(not stated)'}",
        f"Recurring cost: {content.recurring_cost or '(not stated)'}",
        f"5.1 ROI / financial benefit: {content.roi_notes or '(not answered)'}",
        "",
        "--- SECTION 6: UPLOADS ---",
        f"6.1 Primary attachment type: {content.primary_attachment_type.value}",
        f"6.2 Supporting document present: {content.supporting_document_present}",
        "",
        "--- FORM-MAPPING NOTES ---",
        f"Fields not addressed anywhere: "
        f"{', '.join(record.meta.missing_fields) or 'none'}",
        f"Mapping confidence: {record.meta.overall_confidence:.2f}",
        f"Notes: {record.meta.mapper_notes or 'none'}",
        "",
        "--- FULL EXTRACTED TEXT OF ALL SUBMITTED FILES ---",
        "Quote from here. Each file is introduced by a '===== SOURCE: ... =====' line.",
        "",
        attachment_text,
    ]
    return "\n".join(lines)


def blind_view(text: str, record: CanonicalRecord) -> str:
    """Mask names and office immediately before ANY model call.

    Public because the scorer is not the only caller: the verifier reads the
    same submission to check quotes, and the dry-run estimator sends it to the
    token-counting endpoint. Every one of those is a model call and every one
    must see the same masked text, or the blind-review guarantee holds for the
    scoring pass only - which is not what the configuration says it does.
    """
    cfg = load_config()
    if not bool(cfg.get("privacy.deidentify_before_llm", True)):
        return text
    names: list[str] = []
    if record.identity:
        names = [
            n for n in (
                [record.identity.full_name, record.identity.primary_submitter_name]
                + [c.name for c in record.identity.co_submitters]
            ) if n and n.strip()
        ]
    masked, _ = deidentify(
        text,
        names=names,
        office=record.content.office,
        blind_office=bool(cfg.get("privacy.blind_office", True)),
    )
    return masked


# ---------------------------------------------------------------------------
# Scoring one criterion
# ---------------------------------------------------------------------------
def score_criterion(
    rubric: Rubric,
    criterion_key: str,
    submission_view: str,
    is_special_category: bool = False,
    meter: CostMeter | None = None,
    model: str | None = None,
) -> CriterionResult:
    criterion = rubric.criterion(criterion_key)
    cfg = load_config()

    instructions = INSTRUCTIONS
    if is_special_category:
        instructions += SPECIAL_CATEGORY_ADDENDUM

    # The rubric slice is byte-identical for every submission scored on this
    # criterion, which is exactly what prompt caching wants.
    cacheable_prefix = (
        f"RUBRIC: {rubric.name} (version {rubric.version})\n"
        f"AUTHORITY: {rubric.authority}\n\n"
        f"{rubric.render_criterion(criterion_key)}\n\n"
        f"EVIDENCE POLICY: {rubric.evidence_policy.get('rule', '')}\n"
        f"{rubric.evidence_policy.get('attachment_credit', '')}"
    )

    result = CriterionResult(
        criterion=criterion_key,
        name=criterion.name,
        max_points=criterion.weight,
        sub_scores=[],
    )

    try:
        llm = call_json(
            instructions=instructions,
            user_content=submission_view,
            schema=build_scorer_schema(criterion, rubric),
            model=model or str(cfg.require("llm.scoring_model")),
            cacheable_prefix=cacheable_prefix,
            meter=meter,
        )
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.usage = llm.usage
    result.notes = llm.data.get("notes", "")
    result.sub_scores = _parse_sub_scores(llm.data.get("sub_scores", []), criterion, rubric)
    return result


def _parse_sub_scores(
    raw: list[dict[str, Any]], criterion: Criterion, rubric: Rubric
) -> list[SubScore]:
    """Validate and guard the model's sub-scores.

    Guardrails are deterministic, applied in code, and cannot be argued with:

    1. A score is clamped to [0, sub-criterion maximum].
    2. An unevidenced score above the Adequate band is pulled down to the top of
       the Weak band. This enforces the rubric's evidence policy mechanically.
    3. A missing sub-criterion is recorded as zero with an explicit note, never
       silently dropped — dropping it would quietly inflate the total.
    """
    by_key = {item.get("sub_criterion"): item for item in raw}
    adequate = next((b for b in rubric.bands if b.name == "Adequate"), None)
    weak = next((b for b in rubric.bands if b.name == "Weak"), None)
    evidence_ceiling = adequate.hi if adequate else 0.70
    weak_top = weak.hi if weak else 0.45

    out: list[SubScore] = []
    for spec in criterion.sub_criteria:
        item = by_key.get(spec.key)
        if item is None:
            out.append(
                SubScore(
                    sub_criterion=spec.key,
                    name=spec.name,
                    score=0.0,
                    max_score=spec.max,
                    band="Absent",
                    rationale=(
                        "The scoring agent returned no entry for this sub-criterion. "
                        "Recorded as zero and flagged for human review rather than omitted."
                    ),
                    confidence=0.0,
                    guardrail_applied="missing_sub_score",
                )
            )
            continue

        score = float(item.get("score", 0) or 0)
        clamped = max(0.0, min(score, float(spec.max)))
        guardrail = "clamped_to_range" if clamped != score else ""

        evidence = [
            e for e in item.get("evidence", []) if str(e.get("quote", "")).strip()
        ]
        if not evidence and clamped > evidence_ceiling * spec.max:
            capped = round(weak_top * spec.max, 2)
            guardrail = (
                f"unevidenced_score_capped: {clamped} -> {capped} "
                f"(rubric evidence policy — no verbatim quote supplied)"
            )
            clamped = capped

        out.append(
            SubScore(
                sub_criterion=spec.key,
                name=spec.name,
                score=round(clamped, 2),
                max_score=spec.max,
                band=str(item.get("band", "")),
                rationale=str(item.get("rationale", "")),
                confidence=float(item.get("confidence", 0) or 0),
                evidence=[
                    {
                        "quote": str(e.get("quote", ""))[:2000],
                        "source_file": str(e.get("source_file", ""))[:400],
                        "locator": str(e.get("locator", ""))[:120],
                    }
                    for e in evidence
                ],
                guardrail_applied=guardrail,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Scoring one submission — all criteria in parallel
# ---------------------------------------------------------------------------
def score_submission(
    rubric: Rubric,
    record: CanonicalRecord,
    attachment_text: str,
    meter: CostMeter | None = None,
    model: str | None = None,
) -> SubmissionScore:
    view = render_submission_for_scoring(record, attachment_text)
    view = blind_view(view, record)
    is_special = record.content.formation_category is FormationCategory.SPECIAL_CATEGORY_STATE

    results: dict[str, CriterionResult] = {}
    with ThreadPoolExecutor(max_workers=len(rubric.criteria)) as pool:
        futures = {
            pool.submit(
                score_criterion,
                rubric,
                key,
                view,
                is_special,
                meter,
                model,
            ): key
            for key in rubric.criterion_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                criterion = rubric.criterion(key)
                results[key] = CriterionResult(
                    criterion=key,
                    name=criterion.name,
                    max_points=criterion.weight,
                    sub_scores=[],
                    error=f"{type(exc).__name__}: {exc}",
                )

    ordered = [results[k] for k in rubric.criterion_keys]
    score = SubmissionScore(submission_ref=record.submission_ref, criteria=ordered)
    for c in ordered:
        score.usage.add(c.usage)
    return score
