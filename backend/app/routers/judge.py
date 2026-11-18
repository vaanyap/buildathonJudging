from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_judge
from app.database import get_db
from app.models import Assignment, Evaluation, Project, ProjectTrack, Score, Timer, Track, User
from app.schemas import EvaluationIn
from app.serialize import evaluation_dict, project_dict
from app.services.assignments import assignment_is_visible, ensure_timer, ensure_track_assignments
from app.services.domain import DomainError
from app.services.evaluations import (
    active_criteria,
    apply_autosave,
    mark_viewed,
    submit_evaluation,
)
from app.services.timers import PhaseName, get_timer_defaults, parse_phase, pause_timer, reset_timer, start_timer, sync_timer

router = APIRouter(prefix="/judge", tags=["judge"])


def _assignment_options():
    return (
        selectinload(Assignment.project).selectinload(Project.memberships).selectinload(ProjectTrack.track),
        selectinload(Assignment.project).selectinload(Project.timers),
        selectinload(Assignment.track),
        selectinload(Assignment.evaluation).selectinload(Evaluation.scores).selectinload(Score.criterion),
        selectinload(Assignment.evaluation).selectinload(Evaluation.recommended_track),
        selectinload(Assignment.evaluation).selectinload(Evaluation.track),
    )


def _prepare(db: Session, user: User) -> None:
    ensure_track_assignments(db, user)


def load_assignment(db: Session, user: User, project_id: int, track_id: int) -> Assignment:
    _prepare(db, user)
    assignment = db.scalar(
        select(Assignment)
        .where(
            Assignment.judge_id == user.id,
            Assignment.project_id == project_id,
            Assignment.track_id == track_id,
        )
        .options(*_assignment_options())
    )
    if assignment is None or assignment.evaluation is None or not assignment_is_visible(db, assignment):
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_timer(db, user, assignment.project)
    return assignment


def load_project_timer(db: Session, user: User, project_id: int) -> tuple[Timer, Project]:
    _prepare(db, user)
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.judge_id == user.id, Assignment.project_id == project_id)
        .options(selectinload(Assignment.project))
    )
    if assignment is None or not assignment_is_visible(db, assignment):
        raise HTTPException(status_code=404, detail="Project not found")
    timer = ensure_timer(db, user, assignment.project)
    return timer, assignment.project


def _raise(exc: DomainError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _criteria_payload(db: Session) -> list[dict]:
    return [
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
    ]


def _order(project: Project, track_id: int) -> int | None:
    for member in project.memberships:
        if member.track_id == track_id:
            return member.presentation_order
    return None


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    _prepare(db, user)
    assignments = db.scalars(
        select(Assignment).where(Assignment.judge_id == user.id).options(*_assignment_options())
    ).all()
    visible = [assignment for assignment in assignments if assignment_is_visible(db, assignment)]
    defaults = get_timer_defaults(db)
    by_project: dict[int, list[Assignment]] = {}
    for assignment in visible:
        by_project.setdefault(assignment.project_id, []).append(assignment)
    cards = []
    for assignment in visible:
        timer = ensure_timer(db, user, assignment.project)
        project = project_dict(assignment.project)
        siblings = [
            {"track_id": item.track_id, "track_name": item.track.name}
            for item in by_project[assignment.project_id]
            if item.track_id != assignment.track_id
        ]
        cards.append(
            {
                **project,
                "track_id": assignment.track_id,
                "track_name": assignment.track.name,
                "presentation_order": _order(assignment.project, assignment.track_id),
                "status": assignment.evaluation.status,
                "timer": sync_timer(timer, assignment.project, defaults),
                "other_tracks": siblings,
            }
        )
    db.commit()
    cards.sort(
        key=lambda item: (
            item["track_name"].lower(),
            item["presentation_order"] is None,
            item["presentation_order"] or 0,
            item["name"].lower(),
        )
    )
    return {"projects": cards}


@router.get("/projects/{project_id}/tracks/{track_id}")
def project_detail(
    project_id: int,
    track_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user, project_id, track_id)
    timer = ensure_timer(db, user, assignment.project)
    others = db.scalars(
        select(Assignment).where(Assignment.judge_id == user.id, Assignment.project_id == project_id).options(selectinload(Assignment.track))
    ).all()
    db.commit()
    tracks = db.scalars(select(Track).order_by(Track.name)).all()
    return {
        "project": project_dict(assignment.project),
        "track_id": assignment.track_id,
        "track_name": assignment.track.name,
        "other_tracks": [
            {"track_id": item.track_id, "track_name": item.track.name}
            for item in others
            if item.track_id != assignment.track_id and assignment_is_visible(db, item)
        ],
        "timer": sync_timer(timer, assignment.project, get_timer_defaults(db)),
        "evaluation": evaluation_dict(assignment.evaluation, include_private=True),
        "criteria": _criteria_payload(db),
        "tracks": [{"id": track.id, "name": track.name} for track in tracks],
    }


@router.post("/projects/{project_id}/tracks/{track_id}/view")
def view_project(
    project_id: int,
    track_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user, project_id, track_id)
    mark_viewed(assignment.evaluation)
    db.commit()
    return evaluation_dict(assignment.evaluation, include_private=True)


@router.get("/projects/{project_id}/timer")
def get_timer(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    timer, project = load_project_timer(db, user, project_id)
    payload = sync_timer(timer, project, get_timer_defaults(db))
    db.commit()
    return payload


def _phase_name(phase: str) -> PhaseName:
    try:
        return parse_phase(phase)
    except DomainError as exc:
        _raise(exc)
        raise


@router.post("/projects/{project_id}/timer/{phase}/start")
def timer_start(project_id: int, phase: str, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    timer, project = load_project_timer(db, user, project_id)
    phase_name = _phase_name(phase)
    defaults = get_timer_defaults(db)
    try:
        payload = start_timer(timer, project, defaults, phase_name)
    except DomainError as exc:
        if getattr(timer, f"{phase_name}_status") == "time_up":
            db.commit()
        else:
            db.rollback()
        _raise(exc)
    db.commit()
    return payload


@router.post("/projects/{project_id}/timer/{phase}/pause")
def timer_pause(project_id: int, phase: str, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    timer, project = load_project_timer(db, user, project_id)
    phase_name = _phase_name(phase)
    payload = pause_timer(timer, project, get_timer_defaults(db), phase_name)
    db.commit()
    return payload


@router.post("/projects/{project_id}/timer/{phase}/reset")
def timer_reset(project_id: int, phase: str, db: Session = Depends(get_db), user: User = Depends(require_judge)) -> dict:
    timer, project = load_project_timer(db, user, project_id)
    phase_name = _phase_name(phase)
    payload = reset_timer(timer, project, get_timer_defaults(db), phase_name)
    db.commit()
    return payload


@router.get("/projects/{project_id}/tracks/{track_id}/evaluation")
def get_evaluation(
    project_id: int,
    track_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user, project_id, track_id)
    return evaluation_dict(assignment.evaluation, include_private=True)


@router.put("/projects/{project_id}/tracks/{track_id}/evaluation")
def save_evaluation(
    project_id: int,
    track_id: int,
    body: EvaluationIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user, project_id, track_id)
    try:
        apply_autosave(db, assignment.evaluation, body, user, assignment.track_id)
    except DomainError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return evaluation_dict(assignment.evaluation, include_private=True)


@router.post("/projects/{project_id}/tracks/{track_id}/evaluation/submit")
def submit(
    project_id: int,
    track_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_judge),
) -> dict:
    assignment = load_assignment(db, user, project_id, track_id)
    try:
        submit_evaluation(db, assignment.evaluation, user, assignment.track_id)
    except DomainError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return evaluation_dict(assignment.evaluation, include_private=True)
