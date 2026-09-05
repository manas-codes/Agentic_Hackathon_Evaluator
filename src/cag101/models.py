"""SQLAlchemy models. Every score carries full provenance for audit."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Submissions and source material
# ---------------------------------------------------------------------------
class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    folder_name: Mapped[str] = mapped_column(String(512))
    folder_path: Mapped[str] = mapped_column(Text)
    drive_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    title: Mapped[str] = mapped_column(Text, default="")
    thematic_area: Mapped[str] = mapped_column(String(16), default="unclear")
    solution_types: Mapped[list] = mapped_column(JSON, default=list)
    development_stage: Mapped[str] = mapped_column(String(32), default="not_stated")
    office: Mapped[str] = mapped_column(Text, default="")
    formation_category: Mapped[str] = mapped_column(String(32), default="unknown")

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ingested", index=True)
    status_detail: Mapped[str] = mapped_column(Text, default="")

    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    files: Mapped[list["SubmissionFile"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    record: Mapped["CanonicalRecordRow | None"] = relationship(
        back_populates="submission", cascade="all, delete-orphan", uselist=False
    )
    identity: Mapped["RestrictedIdentityRow | None"] = relationship(
        back_populates="submission", cascade="all, delete-orphan", uselist=False
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    flags: Mapped[list["Flag"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    review: Mapped["ReviewDecision | None"] = relationship(
        back_populates="submission", cascade="all, delete-orphan", uselist=False
    )
    overrides: Mapped[list["Override"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class SubmissionFile(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    relative_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))        # form | deck | video | doc | image | other
    mime: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    drive_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    extraction_status: Mapped[str] = mapped_column(String(32), default="pending")
    extraction_detail: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    media_derived: Mapped[bool] = mapped_column(Boolean, default=False)

    submission: Mapped[Submission] = relationship(back_populates="files")

    __table_args__ = (UniqueConstraint("submission_id", "relative_path", name="uq_file_path"),)


class CanonicalRecordRow(Base):
    __tablename__ = "canonical_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), unique=True, index=True
    )
    content: Mapped[dict] = mapped_column(JSON)          # SubmissionContent
    meta: Mapped[dict] = mapped_column(JSON)             # MappingMeta
    mapper_model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="record")


class RestrictedIdentityRow(Base):
    """Section 1 / 7 personal details. Access gated to the `evaluator` role and
    above; never included in a model prompt."""

    __tablename__ = "restricted_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), unique=True, index=True
    )
    payload: Mapped[dict] = mapped_column(JSON)          # RestrictedIdentity
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="identity")


# ---------------------------------------------------------------------------
# Evaluation results
# ---------------------------------------------------------------------------
class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    rubric_slug: Mapped[str] = mapped_column(String(64))
    rubric_hash: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    pass_no: Mapped[int] = mapped_column(Integer, default=1)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    total: Mapped[float] = mapped_column(Float, default=0.0)
    verifier_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    verifier_adjustment: Mapped[float] = mapped_column(Float, default=0.0)
    verifier_notes: Mapped[str] = mapped_column(Text, default="")
    # Plain-language account of why the total came out where it did. Shown as
    # the Remarks column, so a committee reads the reasoning beside the number
    # rather than having to reconstruct it from twelve sub-scores.
    remarks: Mapped[str] = mapped_column(Text, default="")

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="evaluations")
    scores: Mapped[list["CriterionScore"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_eval_sub_final", "submission_id", "is_final"),)


class CriterionScore(Base):
    __tablename__ = "criterion_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), index=True
    )
    criterion: Mapped[str] = mapped_column(String(64), index=True)
    sub_criterion: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(32), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    evaluation: Mapped[Evaluation] = relationship(back_populates="scores")
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="score", cascade="all, delete-orphan"
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    criterion_score_id: Mapped[int] = mapped_column(
        ForeignKey("criterion_scores.id", ondelete="CASCADE"), index=True
    )
    quote: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str] = mapped_column(String(512), default="")
    locator: Mapped[str] = mapped_column(String(128), default="")   # e.g. "form 2.6", "deck slide 4"

    score: Mapped[CriterionScore] = relationship(back_populates="evidence")


class Flag(Base):
    __tablename__ = "flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(String(128))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="flags")

    __table_args__ = (UniqueConstraint("submission_id", "key", name="uq_flag_key"),)


class Override(Base):
    """Human override. The final score is the human's when this exists; both
    values are retained in the export."""

    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    criterion: Mapped[str] = mapped_column(String(64))   # or "__total__"
    machine_score: Mapped[float] = mapped_column(Float)
    human_score: Mapped[float] = mapped_column(Float)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    justification: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submission: Mapped[Submission] = relationship(back_populates="overrides")
    reviewer: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("submission_id", "criterion", name="uq_override_criterion"),
    )


class ReviewDecision(Base):
    """Where a submission stands in the committee's own review, as distinct
    from whether it has been scored.

    Scoring is automatic; sign-off is not. Every evaluated submission starts
    "pending" and an evaluator marks it "approved" once they have read the
    reasoning and are content to let the score stand. The decision is stored
    against the reviewer and the moment it was taken, because "who approved
    this" is the first question asked of any evaluation record.
    """

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved
    note: Mapped[str] = mapped_column(Text, default="")
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    submission: Mapped[Submission] = relationship(back_populates="review")
    reviewer: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_review_submission"),
    )


# ---------------------------------------------------------------------------
# Idea library — Concept Paper §IV.e
# ---------------------------------------------------------------------------
class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["ClusterMember"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ClusterMember(Base):
    __tablename__ = "cluster_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), index=True
    )
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    similarity: Mapped[float] = mapped_column(Float, default=0.0)

    cluster: Mapped[Cluster] = relationship(back_populates="members")


# ---------------------------------------------------------------------------
# Portal auth and audit
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32), default="viewer")  # admin|evaluator|viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ---------------------------------------------------------------------------
# Pipeline jobs — idempotency and resume
# ---------------------------------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("run_id", "submission_id", "stage", name="uq_job"),
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    rubric_slug: Mapped[str] = mapped_column(String(64))
    rubric_hash: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    submissions_total: Mapped[int] = mapped_column(Integer, default=0)
    submissions_done: Mapped[int] = mapped_column(Integer, default=0)
    submissions_failed: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(32), default="running")
    notes: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
