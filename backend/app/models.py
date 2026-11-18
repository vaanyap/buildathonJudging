from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    projects: Mapped[list[Project]] = relationship(back_populates="track")


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
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)
    team_members: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    github_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    demo_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    submission_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    track: Mapped[Track] = relationship(back_populates="projects")
    assignments: Mapped[list[Assignment]] = relationship(back_populates="project")
    schedule: Mapped[ScheduleSlot | None] = relationship(back_populates="project", uselist=False)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("judge_id", "project_id", name="uq_assignment_judge_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    judge: Mapped[User] = relationship(back_populates="assignments")
    project: Mapped[Project] = relationship(back_populates="assignments")
    evaluation: Mapped[Evaluation | None] = relationship(back_populates="assignment", uselist=False)
    timer: Mapped[Timer | None] = relationship(back_populates="assignment", uselist=False)


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    extension_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    location: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project: Mapped[Project] = relationship(back_populates="schedule")


class Timer(Base):
    __tablename__ = "timers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), unique=True, nullable=False)
    accumulated_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assignment: Mapped[Assignment] = relationship(back_populates="timer")


class Evaluation(Base):
    """One evaluation per judge per project. Drafts are stored here and are not completions."""

    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("judge_id", "project_id", name="uq_evaluation_judge_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), unique=True, nullable=False)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assignment: Mapped[Assignment] = relationship(back_populates="evaluation")
    judge: Mapped[User] = relationship(foreign_keys=[judge_id])
    project: Mapped[Project] = relationship(foreign_keys=[project_id])
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
