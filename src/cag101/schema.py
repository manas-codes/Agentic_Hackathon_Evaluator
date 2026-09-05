"""Canonical submission schema — the Appendix-I form as structured data.

Submitters type outside the boxes, merge sections, or attach a free-form note
instead of filling the form. The form-mapper absorbs all of that variance and
emits this schema, so every stage downstream sees one clean shape.

Identity fields live in a separate model (`RestrictedIdentity`) that is never
serialised into a model prompt and never leaves the restricted DB table.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Controlled vocabularies (form fields 2.1, 2.3, 2.4, 3.2, 4.2, 6.1)
# ---------------------------------------------------------------------------
class ThematicArea(str, Enum):
    A = "A"  # Audit Planning, Risk Identification and Execution Methodologies
    B = "B"  # Business Process Re-engineering: Audit, Accounts and Institutional Products
    C = "C"  # Institutional Process Improvements, Workforce and Capacity Building
    D = "D"  # Stakeholder Outreach, Collaboration and Public Engagement
    UNCLEAR = "unclear"


class SolutionType(str, Enum):
    FRAMEWORK_METHODOLOGY = "framework_methodology"
    APPLICATION_SOFTWARE = "application_software"
    PROCESS_REDESIGN = "process_redesign"
    DASHBOARD_VISUALISATION = "dashboard_visualisation"
    HARDWARE_IOT = "hardware_iot"
    AI_ML_LLM = "ai_ml_llm"
    OTHER = "other"


class DevelopmentStage(str, Enum):
    CONCEPT = "concept"
    EARLY_PROTOTYPE = "early_prototype"
    PILOT_TESTED = "pilot_tested"
    PARTIALLY_DEPLOYED = "partially_deployed"
    READY_FOR_SCALE = "ready_for_scale"
    OTHER = "other"
    NOT_STATED = "not_stated"


class Licensing(str, Enum):
    FULLY_OPEN_SOURCE = "fully_open_source"
    MOSTLY_OPEN_SOURCE = "mostly_open_source"
    FULLY_PROPRIETARY = "fully_proprietary"
    NOT_DECIDED = "not_decided"
    NOT_STATED = "not_stated"


class ScalabilityMode(str, Enum):
    CENTRAL_DEPLOYMENT = "central_deployment"
    NEEDS_LOCAL_CUSTOMISATION = "needs_local_customisation"
    OTHER = "other"
    NOT_STATED = "not_stated"


class PrimaryAttachmentType(str, Enum):
    A_DEMO_VIDEO = "A_demo_video"
    B_WORKFLOW_DIAGRAM = "B_workflow_diagram"
    C_CONCEPT_NOTE = "C_concept_note"
    NONE = "none"


class FormationCategory(str, Enum):
    """Evaluation streams per Concept Paper §IV."""

    UNION = "union"
    STATE = "state"
    OTHER = "other"
    SPECIAL_CATEGORY_STATE = "special_category_state"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Restricted identity — Section 1 and 7. Never sent to a model.
# ---------------------------------------------------------------------------
class CoSubmitter(BaseModel):
    name: str = ""
    designation: str = ""
    office: str = ""


class RestrictedIdentity(BaseModel):
    """Personal details. Held in a restricted table, excluded from model input
    and from exports below the `evaluator` role."""

    full_name: str = ""
    designation: str = ""
    official_email: str = ""
    mobile: str = ""
    is_team_submission: bool | None = None
    co_submitters: list[CoSubmitter] = Field(default_factory=list)
    primary_submitter_name: str = ""
    date_of_submission: str = ""  # kept as written; DD-MM-YYYY when parseable
    signature_present: bool | None = None


# ---------------------------------------------------------------------------
# Impact table (form field 4.2, second occurrence)
# ---------------------------------------------------------------------------
class ImpactDimension(str, Enum):
    TIME_SAVED = "time_saved"
    COST_REDUCTION = "cost_reduction"
    ERROR_RATE = "error_rate_improvement"
    COVERAGE = "coverage_expanded"
    STAKEHOLDER_BENEFIT = "citizen_stakeholder_benefit"
    QUALITATIVE = "qualitative_other"


class ImpactRow(BaseModel):
    dimension: ImpactDimension
    estimate: str = ""
    basis: str = ""

    @property
    def is_quantified(self) -> bool:
        """True when the estimate contains a number — the difference between a
        measurable claim and a narrative one."""
        return any(ch.isdigit() for ch in self.estimate)

    @property
    def has_basis(self) -> bool:
        return len(self.basis.strip()) >= 10


# ---------------------------------------------------------------------------
# The submission content
# ---------------------------------------------------------------------------
class SubmissionContent(BaseModel):
    """Everything a scoring agent is allowed to see (post de-identification)."""

    # Section 2 — your initiative
    thematic_area: ThematicArea = ThematicArea.UNCLEAR
    title: str = ""
    solution_types: list[SolutionType] = Field(default_factory=list)
    solution_type_other: str = ""
    development_stage: DevelopmentStage = DevelopmentStage.NOT_STATED
    development_stage_other: str = ""
    problem_statement: str = ""          # 2.5
    proposed_solution: str = ""          # 2.6
    pilot_evidence: str = ""             # 2.7 (optional)

    # Section 3 — technology (optional)
    tech_stack: str = ""                 # 3.1
    licensing: Licensing = Licensing.NOT_STATED
    technical_scalability: str = ""      # 3.3
    data_privacy_notes: str = ""         # 3.4

    # Section 4 — scalability and impact
    beneficiaries: str = ""              # 4.1
    scalability_mode: ScalabilityMode = ScalabilityMode.NOT_STATED
    scalability_notes: str = ""          # 4.2 (first)
    impact_rows: list[ImpactRow] = Field(default_factory=list)   # 4.2 (second)

    # Section 5 — financial snapshot (optional)
    one_time_cost: str = ""
    recurring_cost: str = ""
    roi_notes: str = ""                  # 5.1

    # Section 6 — uploads
    primary_attachment_type: PrimaryAttachmentType = PrimaryAttachmentType.NONE
    supporting_document_present: bool = False

    # Office context, masked during scoring when privacy.blind_office is set
    office: str = ""
    formation_category: FormationCategory = FormationCategory.UNKNOWN

    @field_validator("title", "problem_statement", "proposed_solution", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    # -- derived helpers ---------------------------------------------------
    @property
    def is_technology_centric(self) -> bool:
        tech_types = {
            SolutionType.APPLICATION_SOFTWARE,
            SolutionType.DASHBOARD_VISUALISATION,
            SolutionType.HARDWARE_IOT,
            SolutionType.AI_ML_LLM,
        }
        return bool(tech_types & set(self.solution_types)) or bool(self.tech_stack.strip())

    @property
    def quantified_impact_count(self) -> int:
        return sum(1 for r in self.impact_rows if r.is_quantified)

    def word_count(self, field_name: str) -> int:
        return len((getattr(self, field_name, "") or "").split())


class MappingMeta(BaseModel):
    """How well the form-mapper managed. Low confidence routes to human review."""

    field_confidence: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    mapper_notes: str = ""
    overall_confidence: float = 0.0
    source_files_used: list[str] = Field(default_factory=list)


class CanonicalRecord(BaseModel):
    """One submission, fully normalised."""

    submission_ref: str                  # stable internal reference, e.g. CAG101-0007
    folder_name: str
    content: SubmissionContent
    identity: RestrictedIdentity | None = None
    meta: MappingMeta = Field(default_factory=MappingMeta)

    def for_model(self, blind_office: bool = True) -> dict[str, Any]:
        """Serialise for a model prompt: identity dropped, office optionally masked."""
        data = self.content.model_dump(mode="json")
        if blind_office:
            data["office"] = "[withheld for blind review]"
        # formation_category is retained: Special Category States receive
        # explicitly different treatment under the scheme, so the scorer needs it.
        data["submission_ref"] = self.submission_ref
        return data
