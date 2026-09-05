"""Adversarial verification: a second reader who can only lower scores.

The asymmetry is the point. A verifier that could also raise scores would be a
second scorer, and two scorers average towards generosity — each one's charitable
reading survives. A verifier that can only lower answers one question: does the
quoted evidence actually support the score awarded? Anything it cannot support
comes down.

Enforced in code, not by instruction: a returned score above the original is
discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .llm import CostMeter, Usage, call_json
from .rubric import Rubric
from .score import SubmissionScore

PROMPT_VERSION = "verify-v1"

INSTRUCTIONS = """\
You are a review officer checking another evaluator's scoring of a submission to \
the CAG 101 Innovation Ideas Initiative, before it goes to a Filtering Committee.

You are not re-scoring the submission. You are auditing whether each sub-score is \
supported by the evidence quoted for it. Your only power is to LOWER a score. If \
a sub-score looks too harsh, uphold it and say so in your reason — do not raise \
it. Another reviewer handles generosity; your job is to catch inflation.

For each sub-score, ask in order:

1. Was any evidence quoted? If the evidence list is empty and the score sits \
   above the Absent or Weak band, lower it. Under the scheme's evidence policy a \
   sub-score above Adequate requires a verbatim quote.
2. Is the quote genuine? Check the quote appears in the submission text you have \
   been given. If a quote does not appear there, treat the sub-score as \
   unevidenced and lower it, and say clearly that the quote was not found.
3. Does the quote support what was claimed? A quote that merely mentions the \
   topic does not establish it. "The system will be scalable as it is on cloud" \
   does not support a strong score for technical scalability — it asserts the \
   conclusion. Lower scores that rest on assertion dressed as evidence.
4. Does a quantified claim have a basis that holds? If the number and its stated \
   basis do not support each other, or the basis is missing, lower the score for \
   any sub-criterion that relied on that number.
5. Is the rationale consistent with the score? A rationale describing serious \
   weakness attached to a high score is a scoring error. Lower it to match the \
   rationale.

Do NOT lower a score because:
- you would personally have been stricter, absent a specific evidence failure;
- the submission is short, plainly written, or unpolished;
- the initiative is modest in ambition — modest and well-evidenced is legitimate;
- a non-technology initiative left the optional technology fields empty;
- the operating environment is constrained (this applies especially to Special \
  Category State submissions, where the scheme requires that constraint be taken \
  into account rather than penalised).

Uphold generously and lower precisely. For every sub-score, return a verdict. \
Where you lower, give the new score and state the specific evidence failure in \
one or two sentences. Where you uphold, a brief reason is enough.
"""


@dataclass
class Adjustment:
    criterion: str
    sub_criterion: str
    original: float
    revised: float
    reason: str

    @property
    def delta(self) -> float:
        return round(self.revised - self.original, 2)


@dataclass
class VerificationResult:
    adjustments: list[Adjustment] = field(default_factory=list)
    upheld: int = 0
    notes: str = ""
    usage: Usage = field(default_factory=Usage)
    error: str = ""

    @property
    def total_adjustment(self) -> float:
        return round(sum(a.delta for a in self.adjustments), 2)

    def summary(self) -> str:
        if self.error:
            return f"verification failed: {self.error}"
        if not self.adjustments:
            return f"all {self.upheld} sub-scores upheld"
        return (
            f"{len(self.adjustments)} sub-score(s) lowered "
            f"({self.total_adjustment:+.1f} points), {self.upheld} upheld"
        )


def build_verifier_schema(score: SubmissionScore) -> dict[str, Any]:
    ids = [
        f"{c.criterion}.{s.sub_criterion}"
        for c in score.criteria
        for s in c.sub_scores
    ]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdicts", "notes"],
        "properties": {
            "verdicts": {
                "type": "array",
                "minItems": len(ids),
                "maxItems": len(ids),
                "description": "One verdict per sub-score: " + ", ".join(ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "verdict", "revised_score", "reason"],
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "verdict": {"type": "string", "enum": ["uphold", "lower"]},
                        "revised_score": {
                            "type": "number",
                            "description": (
                                "If lowering, the new score. If upholding, repeat "
                                "the original score."
                            ),
                        },
                        "reason": {"type": "string"},
                    },
                },
            },
            "notes": {
                "type": "string",
                "description": "Anything the committee should know about the "
                               "quality of this scoring. May be empty.",
            },
        },
    }


def render_scorecard_for_review(score: SubmissionScore, rubric: Rubric) -> str:
    lines: list[str] = [
        f"SCORECARD UNDER REVIEW — submission {score.submission_ref}",
        f"Rubric: {rubric.name} ({rubric.version}); total awarded "
        f"{score.total:.1f} of {rubric.total}",
        "",
        "SCORING BANDS:",
        rubric.render_bands(),
        "",
    ]
    for c in score.criteria:
        lines.append(f"=== {c.name} — awarded {c.total:.1f} of {c.max_points} ===")
        if c.error:
            lines.append(f"  (this criterion failed to score: {c.error})")
            continue
        for s in c.sub_scores:
            lines.append("")
            lines.append(
                f"  ID: {c.criterion}.{s.sub_criterion}"
            )
            lines.append(f"  Sub-criterion: {s.name}  (maximum {s.max_score})")
            lines.append(f"  Score awarded: {s.score} — band '{s.band}', "
                         f"scorer confidence {s.confidence:.2f}")
            lines.append(f"  Rationale given: {s.rationale}")
            if s.guardrail_applied:
                lines.append(f"  Automatic guardrail already applied: {s.guardrail_applied}")
            if s.evidence:
                lines.append("  Evidence quoted:")
                for e in s.evidence:
                    lines.append(
                        f"    - \"{e['quote']}\"  [{e['source_file']} / {e['locator']}]"
                    )
            else:
                lines.append("  Evidence quoted: NONE — NO_EVIDENCE_FOUND")
        lines.append("")
        if c.notes:
            lines.append(f"  Scorer's note on this criterion: {c.notes}")
        lines.append("")
    return "\n".join(lines)


def verify_submission(
    rubric: Rubric,
    score: SubmissionScore,
    submission_view: str,
    meter: CostMeter | None = None,
) -> VerificationResult:
    """Run the verification pass and apply its (lowering-only) adjustments."""
    cfg = load_config()
    outcome = VerificationResult()

    scorable = [s for c in score.criteria for s in c.sub_scores]
    if not scorable:
        outcome.error = "no sub-scores to verify"
        return outcome

    user_content = (
        render_scorecard_for_review(score, rubric)
        + "\n\n=== THE SUBMISSION THE SCORES WERE BASED ON ===\n"
        "Check every quote against this text.\n\n"
        + submission_view
    )

    try:
        llm = call_json(
            instructions=INSTRUCTIONS,
            user_content=user_content,
            schema=build_verifier_schema(score),
            model=str(cfg.require("llm.verification_model")),
            cacheable_prefix=None,
            meter=meter,
        )
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome

    outcome.usage = llm.usage
    outcome.notes = llm.data.get("notes", "")

    index = {
        f"{c.criterion}.{s.sub_criterion}": (c, s)
        for c in score.criteria
        for s in c.sub_scores
    }

    for verdict in llm.data.get("verdicts", []):
        pair = index.get(verdict.get("id", ""))
        if pair is None:
            continue
        criterion, sub = pair
        revised = float(verdict.get("revised_score", sub.score) or 0)

        # The asymmetry, enforced in code: a raise is discarded.
        if verdict.get("verdict") != "lower" or revised >= sub.score:
            outcome.upheld += 1
            continue

        revised = max(0.0, min(revised, float(sub.max_score)))
        outcome.adjustments.append(
            Adjustment(
                criterion=criterion.criterion,
                sub_criterion=sub.sub_criterion,
                original=sub.score,
                revised=round(revised, 2),
                reason=str(verdict.get("reason", ""))[:2000],
            )
        )
        sub.score = round(revised, 2)
        sub.rationale = (
            f"{sub.rationale}\n[Lowered on verification: "
            f"{verdict.get('reason', '')}]"
        ).strip()

    return outcome
