from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_judge
from app.database import get_db
from app.models import Assignment, Evaluation, Project, Score, Track, User
from app.schemas import EvaluationIn
from app.serialize import evaluation_dict, project_dict
from app.services.domain import DomainError
from app.services.evaluations import (
    active_criteria,
    apply_autosave,
    mark_viewed,
    submit_evaluation,
)
from app.services.timers import pause_timer, reset_timer, start_timer, sync_timer

router = APIRouter(prefix="/judge", tags=["judge"])


def _assignment_options():
    return (
        selectinload(Assignment.project).selectinload(Project.track),
        selectinload(Assignment.project).selectinload(Project.schedule),
        selectinload(Assignment.evaluation).selectinload(Evaluation.scores).selectinload(Score.criterion),
        selectinload(Assignment.evaluation).selectinload(Evaluation.recommended_track),
        selectinload(Assignment.timer),
    )


def load_assignment(db: Session, judge_id: int, project_id: int) -> Assignment:
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.judge_id == judge_id, Assignment.project_id == project_id)
        .options(*_assignment_options())
    )
    if assignment is None or assignment.evaluation is None or assignment.timer is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return assignment


def _raise(exc: DomainError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignments = db.scalars(
        select(Assignment).where(Assignment.judge_id == user.id).options(*_assignment_options())
    ).all()
    projects = []
    for assignment in assignments:
        timer = sync_timer(assignment.timer, assignment.project.schedule)
        project = project_dict(assignment.project)
        project["status"] = assignment.evaluation.status
        project["timer"] = timer
        projects.append(project)
    db.commit()
    projects.sort(key=lambda item: (item["starts_at"] is None, item["starts_at"] or "", item["name"].lower()))
    return {"projects": projects}


@router.get("/projects/{project_id}")
def project_detail(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    timer = sync_timer(assignment.timer, assignment.project.schedule)
    db.commit()
    tracks = db.scalars(select(Track).order_by(Track.name)).all()
    return {
        "project": project_dict(assignment.project),
        "timer": timer,
        "evaluation": evaluation_dict(assignment.evaluation, include_private=True),
        "criteria": [
            {
                "id": criterion.id,
                "key": criterion.key,
                "name": criterion.name,
                "description": criterion.description,
                "min_score": criterion.min_score,
                "max_score": criterion.max_score,
                "required": criterion.required,
            }
            for criterion in active_criteria(db)
        ],
        "tracks": [{"id": track.id, "name": track.name} for track in tracks],
    }


@router.post("/projects/{project_id}/view")
def view_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    mark_viewed(assignment.evaluation)
    db.commit()
    return evaluation_dict(assignment.evaluation, include_private=True)


@router.get("/projects/{project_id}/timer")
def get_timer(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    payload = sync_timer(assignment.timer, assignment.project.schedule)
    db.commit()
    return payload


@router.post("/projects/{project_id}/timer/start")
def timer_start(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    try:
        payload = start_timer(assignment.timer, assignment.project.schedule)
    except DomainError as exc:
        if assignment.timer.status == "time_up":
            db.commit()
        else:
            db.rollback()
        _raise(exc)
    db.commit()
    return payload


@router.post("/projects/{project_id}/timer/pause")
def timer_pause(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    payload = pause_timer(assignment.timer, assignment.project.schedule)
    db.commit()
    return payload


@router.post("/projects/{project_id}/timer/reset")
def timer_reset(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    payload = reset_timer(assignment.timer, assignment.project.schedule)
    db.commit()
    return payload


@router.get("/projects/{project_id}/evaluation")
def get_evaluation(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    return evaluation_dict(assignment.evaluation, include_private=True)


@router.put("/projects/{project_id}/evaluation")
def save_evaluation(
    project_id: int,
    body: EvaluationIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    try:
        apply_autosave(db, assignment.evaluation, body, user, assignment.project.track_id)
    except DomainError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return evaluation_dict(assignment.evaluation, include_private=True)


@router.post("/projects/{project_id}/evaluation/submit")
def submit(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user.id, project_id)
    try:
        submit_evaluation(db, assignment.evaluation, user, assignment.project.track_id)
    except DomainError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return evaluation_dict(assignment.evaluation, include_private=True)
