from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assignments: Mapped[list[Assignment]] = relationship(back_populates="judge")
    track_assignments: Mapped[list[TrackAssignment]] = relationship(back_populates="judge")


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    memberships: Mapped[list[ProjectTrack]] = relationship(back_populates="track")
    track_assignments: Mapped[list[TrackAssignment]] = relationship(back_populates="track")


class Criterion(Base):
    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    min_score: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_score: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    team_members: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    github_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    demo_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    video_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    submission_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    table_location: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    winner_label: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    presentation_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qa_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    presentation_extension_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qa_extension_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    memberships: Mapped[list[ProjectTrack]] = relationship(back_populates="project")
    assignments: Mapped[list[Assignment]] = relationship(back_populates="project")
    timers: Mapped[list[Timer]] = relationship(back_populates="project")


class ProjectTrack(Base):
    """A project competes in a prize category. Order is per track."""

    __tablename__ = "project_tracks"
    __table_args__ = (UniqueConstraint("project_id", "track_id", name="uq_project_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    presentation_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="memberships")
    track: Mapped[Track] = relationship(back_populates="memberships")


class Assignment(Base):
    """One scorecard: a judge scoring one project for one track."""

    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("judge_id", "project_id", "track_id", name="uq_assignment_judge_project_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="project")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    judge: Mapped[User] = relationship(back_populates="assignments")
    project: Mapped[Project] = relationship(back_populates="assignments")
    track: Mapped[Track] = relationship()
    evaluation: Mapped[Evaluation | None] = relationship(back_populates="assignment", uselist=False)


class TrackAssignment(Base):
    """A judge works through every project in the track, in presentation order."""

    __tablename__ = "track_assignments"
    __table_args__ = (UniqueConstraint("judge_id", "track_id", name="uq_track_assignment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    judge: Mapped[User] = relationship(back_populates="track_assignments")
    track: Mapped[Track] = relationship(back_populates="track_assignments")


class AssignmentExclusion(Base):
    """Keeps a track judge off one project in one track after an organizer removes it."""

    __tablename__ = "assignment_exclusions"
    __table_args__ = (UniqueConstraint("judge_id", "project_id", "track_id", name="uq_assignment_exclusion"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DiscrepancySettings(Base):
    """Thresholds for flagging a project when submitted scores diverge."""

    __tablename__ = "discrepancy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    overall_spread: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    high_score: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    low_score: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DiscrepancyReview(Base):
    """Organizer workflow for one project scored inside one track. Missing row means Open."""

    __tablename__ = "discrepancy_reviews"
    __table_args__ = (UniqueConstraint("project_id", "track_id", name="uq_discrepancy_project_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project: Mapped[Project] = relationship()
    track: Mapped[Track] = relationship()
    updated_by: Mapped[User | None] = relationship()


class TimerDefaults(Base):
    """Global default phase lengths. A project can override either length."""

    __tablename__ = "timer_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    presentation_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    qa_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Timer(Base):
    """One presentation clock per judge per project, shared by every track scorecard."""

    __tablename__ = "timers"
    __table_args__ = (UniqueConstraint("judge_id", "project_id", name="uq_timer_judge_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    active_phase: Mapped[str] = mapped_column(String(16), nullable=False, default="presentation")
    presentation_accumulated_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    presentation_running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    presentation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    presentation_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    presentation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    qa_accumulated_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qa_running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    judge: Mapped[User] = relationship()
    project: Mapped[Project] = relationship(back_populates="timers")


class Evaluation(Base):
    """One evaluation per judge, project, and track. Drafts are stored here and are not completions."""

    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("judge_id", "project_id", "track_id", name="uq_evaluation_judge_project_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), unique=True, nullable=False)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started", index=True)
    private_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    formal_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    alignment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alignment_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cross_track_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recommended_track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"), nullable=True)
    cross_track_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assignment: Mapped[Assignment] = relationship(back_populates="evaluation")
    judge: Mapped[User] = relationship(foreign_keys=[judge_id])
    project: Mapped[Project] = relationship(foreign_keys=[project_id])
    track: Mapped[Track] = relationship(foreign_keys=[track_id])
    recommended_track: Mapped[Track | None] = relationship(foreign_keys=[recommended_track_id])
    scores: Mapped[list[Score]] = relationship(back_populates="evaluation")
    audits: Mapped[list[AuditEvent]] = relationship(back_populates="evaluation")


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("evaluation_id", "criterion_id", name="uq_score_eval_criterion"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criteria.id"), nullable=False)
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    evaluation: Mapped[Evaluation] = relationship(back_populates="scores")
    criterion: Mapped[Criterion] = relationship()


class AuditEvent(Base):
    """Trail for submission, reopen, and any score change after a submission existed."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    evaluation: Mapped[Evaluation] = relationship(back_populates="audits")
    actor: Mapped[User | None] = relationship()
