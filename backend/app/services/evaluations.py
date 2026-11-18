"""Evaluation status, autosave, submit, and reopen.

Statuses:
- not_started: assigned, never opened
- viewed: opened, nothing worth saving yet
- draft_saved: partial scores or notes; never counts as complete
- ready_to_submit: every required score, alignment, and explanation are present
- submitted: judge finalized; only an organizer can reopen

Track alignment is required alongside the quality scores and is not one of them.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Criterion, Evaluation, Score, Track, User
from app.schemas import EvaluationIn
from app.services.domain import DomainError

ALIGNMENTS = {"strong", "partial", "weak"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def active_criteria(db: Session) -> list[Criterion]:
    return list(
        db.scalars(
            select(Criterion).where(Criterion.active.is_(True)).order_by(Criterion.sort_order, Criterion.id)
        ).all()
    )


def ensure_score_rows(db: Session, evaluation: Evaluation, criteria: list[Criterion] | None = None) -> None:
    criteria = active_criteria(db) if criteria is None else criteria
    existing = {score.criterion_id for score in evaluation.scores}
    now = utcnow()
    for criterion in criteria:
        if criterion.id not in existing:
            score = Score(evaluation_id=evaluation.id, criterion_id=criterion.id, value=None, updated_at=now)
            score.criterion = criterion
            evaluation.scores.append(score)
            db.add(score)


def attach_criteria(evaluation: Evaluation, criteria: list[Criterion]) -> None:
    by_id = {criterion.id: criterion for criterion in criteria}
    for score in evaluation.scores:
        if score.criterion is None and score.criterion_id in by_id:
            score.criterion = by_id[score.criterion_id]


def mark_viewed(evaluation: Evaluation) -> None:
    if evaluation.status == "not_started":
        evaluation.status = "viewed"
        evaluation.updated_at = utcnow()


def score_map(evaluation: Evaluation) -> dict[str, int | None]:
    values: dict[str, int | None] = {}
    for score in evaluation.scores:
        if score.criterion is not None:
            values[score.criterion.key] = score.value
    return values


def public_snapshot(evaluation: Evaluation) -> dict:
    """Scores and formal fields only. Private notes are intentionally absent."""
    return {
        "scores": score_map(evaluation),
        "formal_feedback": evaluation.formal_feedback,
        "alignment": evaluation.alignment,
        "alignment_explanation": evaluation.alignment_explanation,
        "cross_track_flag": evaluation.cross_track_flag,
        "recommended_track_id": evaluation.recommended_track_id,
        "cross_track_explanation": evaluation.cross_track_explanation,
    }


def diff_snapshots(before: dict, after: dict) -> dict:
    changes: dict = {}
    before_scores = before.get("scores") or {}
    after_scores = after.get("scores") or {}
    for key in sorted(set(before_scores) | set(after_scores)):
        if before_scores.get(key) != after_scores.get(key):
            changes[f"scores.{key}"] = {"old": before_scores.get(key), "new": after_scores.get(key)}
    for field in (
        "formal_feedback",
        "alignment",
        "alignment_explanation",
        "cross_track_flag",
        "recommended_track_id",
        "cross_track_explanation",
    ):
        if before.get(field) != after.get(field):
            changes[field] = {"old": before.get(field), "new": after.get(field)}
    return changes


def has_content(evaluation: Evaluation) -> bool:
    if any(score.value is not None for score in evaluation.scores):
        return True
    if evaluation.private_notes.strip():
        return True
    if evaluation.formal_feedback.strip():
        return True
    if evaluation.alignment:
        return True
    if evaluation.alignment_explanation.strip():
        return True
    if evaluation.cross_track_flag:
        return True
    if evaluation.cross_track_explanation.strip():
        return True
    if evaluation.recommended_track_id:
        return True
    return False


def missing_requirements(evaluation: Evaluation, criteria: list[Criterion], project_track_id: int) -> list[str]:
    by_id = {score.criterion_id: score.value for score in evaluation.scores}
    missing: list[str] = []
    for criterion in criteria:
        if criterion.required and by_id.get(criterion.id) is None:
            missing.append(criterion.name)
    if evaluation.alignment not in ALIGNMENTS:
        missing.append("Track alignment")
    elif not evaluation.alignment_explanation.strip():
        missing.append("Alignment explanation")
    if evaluation.cross_track_flag:
        if not evaluation.recommended_track_id:
            missing.append("Recommended track")
        elif evaluation.recommended_track_id == project_track_id:
            missing.append("A different recommended track")
        if not evaluation.cross_track_explanation.strip():
            missing.append("Cross-track explanation")
    return missing


def derive_status(evaluation: Evaluation, criteria: list[Criterion], project_track_id: int) -> str:
    if not missing_requirements(evaluation, criteria, project_track_id):
        return "ready_to_submit"
    if has_content(evaluation):
        return "draft_saved"
    return "viewed"


def _record_change(db: Session, evaluation: Evaluation, actor: User, before: dict | None) -> None:
    if before is None or evaluation.submission_count <= 0:
        return
    changes = diff_snapshots(before, public_snapshot(evaluation))
    if not changes:
        return
    db.add(
        AuditEvent(
            evaluation_id=evaluation.id,
            actor_id=actor.id,
            action="score_change",
            changes=changes,
            created_at=utcnow(),
        )
    )


def lock_evaluation(db: Session, evaluation: Evaluation) -> Evaluation:
    """Lock the row and reload it so a concurrent submit cannot be overwritten."""
    locked = db.scalar(
        select(Evaluation)
        .where(Evaluation.id == evaluation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise DomainError("Evaluation not found", 404)
    db.expire(locked, ["scores", "recommended_track"])
    _ = list(locked.scores)
    return locked


def apply_autosave(
    db: Session,
    evaluation: Evaluation,
    payload: EvaluationIn,
    actor: User,
    project_track_id: int,
) -> Evaluation:
    evaluation = lock_evaluation(db, evaluation)
    if payload.revision is not None and payload.revision != evaluation.revision:
        raise DomainError("This scorecard was updated somewhere else. Reload it and try again.", 409)
    if evaluation.status == "submitted":
        raise DomainError(
            "This evaluation is submitted. An organizer must reopen it before editing.",
            403,
        )
    criteria = active_criteria(db)
    ensure_score_rows(db, evaluation, criteria)
    attach_criteria(evaluation, criteria)
    before = public_snapshot(evaluation) if evaluation.submission_count > 0 else None

    evaluation.private_notes = payload.private_notes or ""
    evaluation.formal_feedback = (payload.formal_feedback or "").strip()
    evaluation.alignment = payload.alignment
    evaluation.alignment_explanation = (payload.alignment_explanation or "").strip()
    evaluation.cross_track_flag = bool(payload.cross_track_flag)
    evaluation.recommended_track_id = payload.recommended_track_id
    evaluation.cross_track_explanation = (payload.cross_track_explanation or "").strip()

    if evaluation.recommended_track_id is not None:
        track = db.get(Track, evaluation.recommended_track_id)
        if track is None:
            raise DomainError("Recommended track does not exist")
        evaluation.recommended_track = track
        if evaluation.cross_track_flag and evaluation.recommended_track_id == project_track_id:
            raise DomainError("Recommended track must be different from the project's track")
    else:
        evaluation.recommended_track = None

    by_key = {criterion.key: criterion for criterion in criteria}
    unknown = sorted(set(payload.scores) - set(by_key))
    if unknown:
        raise DomainError(f"Unknown criterion: {', '.join(unknown)}")

    now = utcnow()
    score_by_criterion = {score.criterion_id: score for score in evaluation.scores}
    for criterion in criteria:
        value = payload.scores.get(criterion.key)
        if value is not None and (value < criterion.min_score or value > criterion.max_score):
            raise DomainError(
                f"{criterion.name} must be between {criterion.min_score} and {criterion.max_score}"
            )
        score = score_by_criterion.get(criterion.id)
        if score is None:
            continue
        score.value = value
        score.updated_at = now
        score.criterion = criterion

    evaluation.status = derive_status(evaluation, criteria, project_track_id)
    evaluation.updated_at = now
    evaluation.revision += 1
    _record_change(db, evaluation, actor, before)
    return evaluation


def submit_evaluation(
    db: Session,
    evaluation: Evaluation,
    actor: User,
    project_track_id: int,
) -> Evaluation:
    evaluation = lock_evaluation(db, evaluation)
    if evaluation.status == "submitted":
        raise DomainError("This evaluation is already submitted")
    criteria = active_criteria(db)
    ensure_score_rows(db, evaluation, criteria)
    attach_criteria(evaluation, criteria)
    missing = missing_requirements(evaluation, criteria, project_track_id)
    evaluation.status = derive_status(evaluation, criteria, project_track_id)
    if missing:
        raise DomainError(
            "Complete every required score and the track alignment before submitting. Still needed: "
            + ", ".join(missing)
        )
    now = utcnow()
    evaluation.status = "submitted"
    evaluation.submitted_at = now
    evaluation.submission_count += 1
    evaluation.revision += 1
    evaluation.updated_at = now
    db.add(
        AuditEvent(
            evaluation_id=evaluation.id,
            actor_id=actor.id,
            action="submit",
            changes=public_snapshot(evaluation),
            created_at=now,
        )
    )
    return evaluation


def reopen_evaluation(
    db: Session,
    evaluation: Evaluation,
    actor: User,
    project_track_id: int,
) -> Evaluation:
    evaluation = lock_evaluation(db, evaluation)
    if evaluation.status != "submitted":
        raise DomainError("Only submitted evaluations can be reopened")
    criteria = active_criteria(db)
    attach_criteria(evaluation, criteria)
    now = utcnow()
    new_status = derive_status(evaluation, criteria, project_track_id)
    evaluation.status = new_status
    evaluation.revision += 1
    evaluation.updated_at = now
    db.add(
        AuditEvent(
            evaluation_id=evaluation.id,
            actor_id=actor.id,
            action="reopen",
            changes={"status": {"old": "submitted", "new": new_status}},
            created_at=now,
        )
    )
    return evaluation
