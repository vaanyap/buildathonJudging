from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Evaluation, Project, Timer, User
from app.services.domain import DomainError
from app.services.evaluations import ensure_score_rows, utcnow


def create_assignment(db: Session, judge: User, project: Project) -> Assignment:
    if judge.role != "judge":
        raise DomainError("Only judge accounts can be assigned to projects")
    if not judge.active:
        raise DomainError("That judge account is inactive")
    existing = db.scalar(
        select(Assignment).where(Assignment.judge_id == judge.id, Assignment.project_id == project.id)
    )
    if existing is not None:
        raise DomainError("That judge is already assigned to this project", 409)

    now = utcnow()
    assignment = Assignment(judge_id=judge.id, project_id=project.id, created_at=now)
    db.add(assignment)
    db.flush()

    evaluation = Evaluation(
        assignment_id=assignment.id,
        judge_id=judge.id,
        project_id=project.id,
        status="not_started",
        private_notes="",
        formal_feedback="",
        alignment=None,
        alignment_explanation="",
        cross_track_flag=False,
        cross_track_explanation="",
        submission_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(evaluation)
    db.flush()
    assignment.evaluation = evaluation

    timer = Timer(
        assignment_id=assignment.id,
        accumulated_seconds=0,
        status="idle",
        updated_at=now,
    )
    db.add(timer)
    assignment.timer = timer
    ensure_score_rows(db, evaluation)
    db.flush()
    return assignment


def delete_assignment(db: Session, assignment: Assignment, force: bool) -> None:
    evaluation = assignment.evaluation
    status = evaluation.status if evaluation is not None else "not_started"
    if status == "submitted":
        raise DomainError("Submitted evaluations can't be removed.", 409)
    if status in {"draft_saved", "ready_to_submit"} and not force:
        raise DomainError("This judge has saved work. Confirm to discard the draft.", 409)
    if evaluation is not None:
        for score in list(evaluation.scores):
            db.delete(score)
        for audit in list(evaluation.audits):
            db.delete(audit)
        db.delete(evaluation)
    if assignment.timer is not None:
        db.delete(assignment.timer)
    db.delete(assignment)
    db.flush()
