import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import find_user_by_email, hash_password, require_organizer
from app.database import get_db
from app.models import Assignment, Evaluation, Project, ProjectTrack, Track, User
from app.serialize import criterion_dict, project_dict
from app.services.aggregation import (
    build_results,
    cross_track_review,
    get_project,
    load_projects,
    progress_snapshot,
)
from app.services.assignments import (
    assign_judge_to_track,
    create_assignment,
    delete_assignment,
    remove_judge_from_track,
    sync_project_tracks,
)
from app.services.discrepancies import (
    STATUSES,
    analyze_project,
    apply_note_visibility,
    get_settings,
    is_active,
    log_private_note_views,
    review_for,
    save_review,
    status_of,
)
from app.services.domain import DomainError
from app.services.evaluations import active_criteria, reopen_evaluation, utcnow
from app.services.submissions_import import import_submissions
from app.services.timers import PhaseName, get_timer_defaults, parse_phase
from app.services.tracks import track_board
from app.services.winners import build_winners, winners_csv, winners_markdown

router = APIRouter(prefix="/organizer", tags=["organizer"])
EASTERN = ZoneInfo("America/New_York")


class TrackIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class CriterionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    min_score: int = Field(ge=1, le=5)
    max_score: int = Field(ge=1, le=5)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    track_ids: list[int] = Field(default_factory=list)
    team_members: list[str] = Field(default_factory=list)
    github_url: str = ""
    demo_url: str = ""
    video_url: str = ""
    submission_url: str = ""
    table_location: str = ""


class JudgeIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class JudgePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)


class AssignmentIn(BaseModel):
    judge_id: int
    project_id: int
    track_id: int


class TrackJudgeIn(BaseModel):
    judge_id: int
    track_id: int


class ReassignIn(BaseModel):
    judge_id: int
    force: bool = False


class TimingIn(BaseModel):
    presentation_minutes: int | None = Field(default=None, ge=1, le=180)
    qa_minutes: int | None = Field(default=None, ge=1, le=180)


class OrderIn(BaseModel):
    presentation_order: int | None = Field(default=None, ge=1, le=500)


class ExtendIn(BaseModel):
    minutes: int = Field(ge=1, le=60)
    phase: PhaseName


class DiscrepancySettingsIn(BaseModel):
    overall_spread: float = Field(ge=0.1, le=4)
    high_score: int = Field(ge=1, le=5)
    low_score: int = Field(ge=1, le=5)


class DiscrepancyReviewIn(BaseModel):
    status: str
    note: str = ""


class TimerDefaultsIn(BaseModel):
    presentation_minutes: int = Field(ge=1, le=180)
    qa_minutes: int = Field(ge=1, le=180)


def _http(exc: DomainError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That record already exists") from None


def _as_eastern(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=EASTERN)
    return value.astimezone(EASTERN)


def _clean_url(value: str, label: str) -> str:
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise DomainError(f"{label} must start with http:// or https://")
    return value


def _members(values: list[str]) -> list[str]:
    return [item.strip() for item in values if item and item.strip()]


def _get_track(db: Session, track_id: int) -> Track:
    track = db.get(Track, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return track


def _get_judge(db: Session, judge_id: int) -> User:
    user = db.get(User, judge_id)
    if user is None or user.role != "judge":
        raise HTTPException(status_code=404, detail="Judge not found")
    return user


def _require_project(db: Session, project_id: int) -> Project:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _assignment_for_edit(db: Session, assignment_id: int) -> Assignment:
    assignment = db.scalar(select(Assignment).where(Assignment.id == assignment_id))
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    _ = assignment.judge
    _ = assignment.project
    if assignment.evaluation is not None:
        _ = list(assignment.evaluation.scores)
        _ = list(assignment.evaluation.audits)
    return assignment


def _project_admin(project: Project) -> dict:
    data = project_dict(project)
    data["assignments"] = [
        {
            "assignment_id": assignment.id,
            "judge_id": assignment.judge_id,
            "judge_name": assignment.judge.name,
            "track_id": assignment.track_id,
            "track_name": assignment.track.name if assignment.track is not None else "",
            "status": assignment.evaluation.status if assignment.evaluation is not None else "not_started",
        }
        for assignment in sorted(project.assignments, key=lambda item: (item.track.name.lower(), item.judge.name.lower()))
    ]
    return data


def _apply_project(project: Project, body: ProjectIn) -> None:
    project.name = body.name.strip()
    project.description = body.description.strip()
    project.team_members = _members(body.team_members)
    project.github_url = _clean_url(body.github_url, "GitHub link")
    project.demo_url = _clean_url(body.demo_url, "Demo link")
    project.video_url = _clean_url(body.video_url, "Video link")
    project.submission_url = _clean_url(body.submission_url, "Submission link")
    project.table_location = body.table_location.strip()
    project.updated_at = utcnow()


def _tracks_for(db: Session, track_ids: list[int]) -> list[Track]:
    tracks = []
    for track_id in track_ids:
        tracks.append(_get_track(db, track_id))
    return tracks


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    progress = progress_snapshot(db)
    board = track_board(db)
    missing = []
    for project in progress["projects"]:
        outstanding = [judge for judge in project["judges"] if judge["status"] != "submitted"]
        if project["assigned_count"] and outstanding:
            missing.append(
                {
                    "project_id": project["project_id"],
                    "project_name": project["project_name"],
                    "track_name": project["track_name"],
                    "outstanding": outstanding,
                }
            )
    judge_count = db.scalar(
        select(func.count()).select_from(User).where(User.role == "judge", User.active.is_(True))
    )
    _commit(db)
    return {
        "counts": {**progress["counts"], "judges": int(judge_count or 0)},
        "tracks": board["tracks"],
        "missing": missing,
        "criteria": [criterion_dict(criterion) for criterion in active_criteria(db)],
    }


@router.get("/tracks")
def tracks(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> list[dict]:
    return [
        {"id": track.id, "name": track.name, "description": track.description}
        for track in db.scalars(select(Track).order_by(Track.name)).all()
    ]


@router.post("/tracks", status_code=201)
def create_track(body: TrackIn, db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    name = body.name.strip()
    existing = db.scalar(select(Track).where(func.lower(Track.name) == name.lower()))
    if existing is not None:
        raise HTTPException(status_code=409, detail="A track with that name already exists")
    track = Track(name=name, description=body.description.strip())
    db.add(track)
    _commit(db)
    db.refresh(track)
    return {"id": track.id, "name": track.name, "description": track.description}


@router.get("/criteria")
def criteria(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> list[dict]:
    return [criterion_dict(criterion) for criterion in active_criteria(db)]


@router.patch("/criteria/{criterion_id}")
def update_criterion(
    criterion_id: int,
    body: CriterionIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    from app.models import Criterion

    criterion = db.get(Criterion, criterion_id)
    if criterion is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    if body.min_score > body.max_score:
        raise HTTPException(status_code=400, detail="Minimum score cannot exceed maximum score")
    criterion.name = body.name.strip()
    criterion.description = body.description.strip()
    criterion.min_score = body.min_score
    criterion.max_score = body.max_score
    _commit(db)
    return criterion_dict(criterion)


@router.get("/projects")
def projects(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> list[dict]:
    return [_project_admin(project) for project in load_projects(db)]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    project = Project(name="", description="", team_members=[])
    try:
        _apply_project(project, body)
        db.add(project)
        db.flush()
        sync_project_tracks(db, project, _tracks_for(db, body.track_ids))
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return _project_admin(_require_project(db, project.id))


@router.patch("/projects/{project_id}")
def update_project(
    project_id: int,
    body: ProjectIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    try:
        _apply_project(project, body)
        if body.track_ids:
            sync_project_tracks(db, project, _tracks_for(db, body.track_ids))
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return _project_admin(_require_project(db, project.id))


@router.delete("/projects/{project_id}")
def remove_project(
    project_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    try:
        for assignment in list(project.assignments):
            status = assignment.evaluation.status if assignment.evaluation is not None else "not_started"
            if status == "submitted":
                raise DomainError("This project has a submitted evaluation and can't be deleted.", 409)
            if status in {"draft_saved", "ready_to_submit"} and not force:
                raise DomainError("This project has saved drafts. Confirm to delete it.", 409)
        for assignment in list(project.assignments):
            delete_assignment(db, assignment, force=True)
        db.delete(project)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {"ok": True}


@router.post("/projects/import")
async def import_projects(
    file: UploadFile = File(...),
    dry_run: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8") from None
    try:
        summary = import_submissions(db, text, dry_run=dry_run)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    if not dry_run:
        _commit(db)
    return summary


def _judge_payload(db: Session, judge: User) -> dict:
    rows = db.scalars(select(Assignment).where(Assignment.judge_id == judge.id)).all()
    # Ensure relationships are available.
    payload_assignments = []
    submitted = 0
    for assignment in rows:
        project = db.get(Project, assignment.project_id)
        evaluation = db.scalar(select(Evaluation).where(Evaluation.assignment_id == assignment.id))
        status = evaluation.status if evaluation is not None else "not_started"
        if status == "submitted":
            submitted += 1
        track = db.get(Track, assignment.track_id)
        payload_assignments.append(
            {
                "assignment_id": assignment.id,
                "project_id": assignment.project_id,
                "project_name": project.name if project is not None else "",
                "track_id": assignment.track_id,
                "track_name": track.name if track is not None else "",
                "status": status,
            }
        )
    payload_assignments.sort(key=lambda item: item["project_name"].lower())
    return {
        "id": judge.id,
        "name": judge.name,
        "email": judge.email,
        "active": judge.active,
        "assignment_count": len(payload_assignments),
        "submitted_count": submitted,
        "assignments": payload_assignments,
    }


@router.get("/judges")
def judges(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> list[dict]:
    people = db.scalars(select(User).where(User.role == "judge").order_by(User.name)).all()
    return [_judge_payload(db, judge) for judge in people]


@router.post("/judges", status_code=201)
def create_judge(body: JudgeIn, db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    email = str(body.email).strip().lower()
    if find_user_by_email(db, email) is not None:
        raise HTTPException(status_code=409, detail="A user with that email already exists")
    judge = User(
        email=email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        role="judge",
        active=True,
    )
    db.add(judge)
    _commit(db)
    db.refresh(judge)
    return _judge_payload(db, judge)


@router.patch("/judges/{judge_id}")
def update_judge(
    judge_id: int,
    body: JudgePatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    judge = _get_judge(db, judge_id)
    if body.name is not None:
        judge.name = body.name.strip()
    if body.active is not None:
        judge.active = body.active
    if body.password:
        judge.password_hash = hash_password(body.password)
    _commit(db)
    return _judge_payload(db, judge)


@router.post("/assignments", status_code=201)
def assign(body: AssignmentIn, db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    judge = _get_judge(db, body.judge_id)
    project = _require_project(db, body.project_id)
    track = _get_track(db, body.track_id)
    try:
        assignment = create_assignment(db, judge, project, track)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {
        "assignment_id": assignment.id,
        "judge_id": judge.id,
        "project_id": project.id,
        "status": "not_started",
    }


@router.post("/assignments/{assignment_id}/reassign")
def reassign(
    assignment_id: int,
    body: ReassignIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    assignment = _assignment_for_edit(db, assignment_id)
    judge = _get_judge(db, body.judge_id)
    project = assignment.project
    track = assignment.track
    if judge.id == assignment.judge_id:
        return {
            "assignment_id": assignment.id,
            "judge_id": judge.id,
            "project_id": project.id,
            "status": assignment.evaluation.status if assignment.evaluation else "not_started",
        }
    try:
        delete_assignment(db, assignment, body.force, exclude=True)
        created = create_assignment(db, judge, project, track)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {
        "assignment_id": created.id,
        "judge_id": judge.id,
        "project_id": project.id,
        "status": "not_started",
    }


@router.delete("/assignments/{assignment_id}")
def unassign(
    assignment_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    assignment = _assignment_for_edit(db, assignment_id)
    try:
        delete_assignment(db, assignment, force, exclude=True)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {"ok": True}


def _defaults_payload(db: Session) -> dict:
    defaults = get_timer_defaults(db)
    return {
        "presentation_minutes": defaults.presentation_minutes,
        "qa_minutes": defaults.qa_minutes,
    }


@router.get("/timer-defaults")
def read_timer_defaults(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    payload = _defaults_payload(db)
    _commit(db)
    return payload


@router.put("/timer-defaults")
def save_timer_defaults(
    body: TimerDefaultsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    defaults = get_timer_defaults(db)
    defaults.presentation_minutes = body.presentation_minutes
    defaults.qa_minutes = body.qa_minutes
    defaults.updated_at = utcnow()
    _commit(db)
    return _defaults_payload(db)


@router.get("/tracks/live")
def live_tracks(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    board = track_board(db)
    _commit(db)
    return board


@router.post("/track-assignments", status_code=201)
def add_track_judge(body: TrackJudgeIn, db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    judge = _get_judge(db, body.judge_id)
    track = _get_track(db, body.track_id)
    try:
        row = assign_judge_to_track(db, judge, track)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {"id": row.id, "judge_id": judge.id, "track_id": track.id}


@router.delete("/track-assignments")
def remove_track_judge(
    judge_id: int,
    track_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    judge = _get_judge(db, judge_id)
    track = _get_track(db, track_id)
    try:
        remove_judge_from_track(db, judge, track)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {"ok": True}


@router.put("/projects/{project_id}/timing")
def save_timing(
    project_id: int,
    body: TimingIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    project.presentation_minutes = body.presentation_minutes
    project.qa_minutes = body.qa_minutes
    project.updated_at = utcnow()
    _commit(db)
    return project_dict(_require_project(db, project_id))


@router.put("/projects/{project_id}/tracks/{track_id}/order")
def save_order(
    project_id: int,
    track_id: int,
    body: OrderIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    member = next((item for item in project.memberships if item.track_id == track_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="That project is not in this track")
    member.presentation_order = body.presentation_order
    _commit(db)
    return project_dict(_require_project(db, project_id))


@router.post("/projects/{project_id}/timer/extend")
def extend_timer(
    project_id: int,
    body: ExtendIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    try:
        phase = parse_phase(body.phase)
    except DomainError as exc:
        _http(exc)
        raise
    if phase == "presentation":
        project.presentation_extension_minutes += body.minutes
    else:
        project.qa_extension_minutes += body.minutes
    project.updated_at = utcnow()
    _commit(db)
    return project_dict(_require_project(db, project_id))


def _review_payload(review) -> dict | None:
    if review is None:
        return None
    return {
        "status": review.status,
        "note": review.note,
        "updated_by": review.updated_by.name if review.updated_by is not None else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


def _discrepancy_rows(db: Session, reveal_key: tuple[int, int] | None = None, actor: User | None = None) -> list[dict]:
    from app.services.evaluations import active_criteria

    settings = get_settings(db)
    criteria = active_criteria(db)
    rows = []
    for project in load_projects(db):
        for member in project.memberships:
            analysis = analyze_project(project, criteria, settings, member.track_id)
            if analysis is None:
                continue
            review = review_for(db, project.id, member.track_id)
            reveal = reveal_key == (project.id, member.track_id)
            revealed = apply_note_visibility(analysis, review, reveal and is_active(analysis, review))
            if reveal and actor is not None and revealed:
                log_private_note_views(db, actor, project, revealed)
            rows.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "track_id": member.track_id,
                    "track_name": member.track.name,
                    "active": is_active(analysis, review),
                    "status": status_of(review) if analysis else "open",
                    "reasons": analysis["reasons"],
                    "divergent_criteria": analysis["divergent_criteria"],
                    "involved_judge_ids": analysis["involved_judge_ids"],
                    "review": _review_payload(review) or {"status": "open", "note": "", "updated_by": None, "updated_at": None},
                    "judges": analysis["judges"],
                }
            )
    rows.sort(key=lambda item: (not item["active"], item["track_name"].lower(), item["project_name"].lower()))
    return rows


@router.get("/discrepancy-settings")
def read_discrepancy_settings(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    row = get_settings(db)
    _commit(db)
    return {"overall_spread": row.overall_spread, "high_score": row.high_score, "low_score": row.low_score}


@router.put("/discrepancy-settings")
def save_discrepancy_settings(
    body: DiscrepancySettingsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    if body.low_score >= body.high_score:
        raise HTTPException(status_code=400, detail="Low score must be below the high score")
    row = get_settings(db)
    row.overall_spread = body.overall_spread
    row.high_score = body.high_score
    row.low_score = body.low_score
    row.updated_at = utcnow()
    _commit(db)
    return {"overall_spread": row.overall_spread, "high_score": row.high_score, "low_score": row.low_score}


@router.get("/discrepancies")
def discrepancies(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    rows = _discrepancy_rows(db)
    _commit(db)
    return {"projects": [row for row in rows if row["active"]]}


@router.get("/discrepancies/{project_id}/tracks/{track_id}")
def discrepancy_detail(
    project_id: int,
    track_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
) -> dict:
    rows = _discrepancy_rows(db, reveal_key=(project_id, track_id), actor=user)
    match = next((row for row in rows if row["project_id"] == project_id and row["track_id"] == track_id), None)
    if match is None or not match["active"]:
        db.rollback()
        raise HTTPException(status_code=404, detail="No active discrepancy for that project and track")
    _commit(db)
    return match


@router.patch("/discrepancies/{project_id}/tracks/{track_id}")
def update_discrepancy(
    project_id: int,
    track_id: int,
    body: DiscrepancyReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
) -> dict:
    if body.status not in STATUSES:
        raise HTTPException(status_code=400, detail="Status must be open, discussing, or resolved")
    project = _require_project(db, project_id)
    settings = get_settings(db)
    analysis = analyze_project(project, active_criteria(db), settings, track_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="That project is not flagged in this track")
    try:
        save_review(db, project, track_id, user, body.status, body.note)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    rows = _discrepancy_rows(db)
    match = next((row for row in rows if row["project_id"] == project_id and row["track_id"] == track_id), None)
    _commit(db)
    return match or {"project_id": project_id, "track_id": track_id, "active": False, "status": body.status}


@router.get("/winners")
def winners(
    per_track: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    return build_winners(db, per_track)


@router.get("/winners.csv")
def winners_export_csv(
    per_track: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> Response:
    return _csv_response(winners_csv(build_winners(db, per_track)), "winners.csv")


@router.get("/winners.md")
def winners_export_md(
    per_track: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> Response:
    report = build_winners(db, per_track)
    return Response(
        content=winners_markdown(report),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="winners.md"'},
    )


@router.get("/progress")
def progress(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    snapshot = progress_snapshot(db)
    _commit(db)
    return snapshot


@router.get("/cross-track")
def cross_track(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    return cross_track_review(db)


@router.get("/results")
def results(
    track_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    return build_results(db, track_id=track_id)


@router.post("/evaluations/{evaluation_id}/reopen")
def reopen(
    evaluation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
) -> dict:
    from app.serialize import evaluation_dict

    evaluation = db.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    project = db.get(Project, evaluation.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # Load score criteria for status derivation and the response.
    _ = list(evaluation.scores)
    for score in evaluation.scores:
        _ = score.criterion
    _ = evaluation.recommended_track
    try:
        reopen_evaluation(db, evaluation, user, evaluation.track_id)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    payload = evaluation_dict(evaluation, include_private=False)
    payload["judge_name"] = evaluation.judge.name if evaluation.judge is not None else ""
    return payload


def _csv_response(text: str, filename: str) -> Response:
    return Response(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/results.csv")
def export_results(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> Response:
    report = build_results(db)
    criteria = report["criteria"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = [
        "project",
        "track",
        "rank",
        "overall_average",
        "assigned_judges",
        "submitted_evaluations",
        "discrepancy",
        "discrepancy_reasons",
    ]
    header += [f"avg_{criterion['key']}" for criterion in criteria]
    header += ["judge", "judge_status", "judge_overall"]
    header += [criterion["key"] for criterion in criteria]
    header += ["alignment", "alignment_explanation"]
    writer.writerow(header)

    for project in report["projects"]:
        base = [
            project["project_name"],
            project["track_name"],
            "" if project["rank"] is None else project["rank"],
            "" if project["overall"] is None else f"{project['overall']:.2f}",
            project["assigned_count"],
            project["submitted_count"],
            "yes" if project["discrepancy"] else "no",
            "; ".join(project["discrepancy_reasons"]),
        ]
        base += [
            "" if project["criterion_averages"].get(criterion["key"]) is None else f"{project['criterion_averages'][criterion['key']]:.2f}"
            for criterion in criteria
        ]
        evaluations = {item["judge_id"]: item for item in project["evaluations"]}
        judges = project["judges"] or [None]
        for judge in judges:
            if judge is None:
                writer.writerow(base + ["", "", ""] + ["" for _ in criteria] + ["", ""])
                continue
            evaluation = evaluations.get(judge["judge_id"])
            score_by_key = {}
            if evaluation is not None:
                score_by_key = {score["key"]: score["value"] for score in evaluation["scores"]}
            row = list(base)
            row.append(judge["judge_name"])
            row.append(judge["status"])
            row.append("" if evaluation is None or evaluation.get("overall") is None else f"{evaluation['overall']:.2f}")
            for criterion in criteria:
                value = score_by_key.get(criterion["key"])
                row.append("" if value is None else value)
            row.append(evaluation["alignment"] if evaluation else "")
            row.append(evaluation["alignment_explanation"] if evaluation else "")
            writer.writerow(row)
    return _csv_response(buffer.getvalue(), "results.csv")


@router.get("/export/feedback.csv")
def export_feedback(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> Response:
    report = build_results(db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "project",
            "track",
            "judge",
            "status",
            "formal_feedback",
            "alignment",
            "alignment_explanation",
            "cross_track_flag",
            "recommended_track",
            "cross_track_explanation",
        ]
    )
    for project in report["projects"]:
        for evaluation in project["evaluations"]:
            if evaluation["status"] != "submitted" and evaluation["submission_count"] <= 0:
                continue
            writer.writerow(
                [
                    project["project_name"],
                    project["track_name"],
                    evaluation["judge_name"],
                    evaluation["status"],
                    evaluation["formal_feedback"],
                    evaluation["alignment"] or "",
                    evaluation["alignment_explanation"],
                    "yes" if evaluation["cross_track_flag"] else "no",
                    evaluation["recommended_track_name"] or "",
                    evaluation["cross_track_explanation"],
                ]
            )
    return _csv_response(buffer.getvalue(), "feedback.csv")
