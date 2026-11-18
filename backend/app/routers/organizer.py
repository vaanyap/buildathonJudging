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
from app.models import Assignment, Evaluation, Project, ScheduleSlot, Track, User
from app.serialize import criterion_dict, project_dict
from app.services.aggregation import (
    build_results,
    cross_track_review,
    get_project,
    list_delays,
    load_projects,
    progress_snapshot,
)
from app.services.assignments import create_assignment, delete_assignment
from app.services.domain import DomainError
from app.services.evaluations import active_criteria, reopen_evaluation, utcnow
from app.services.timers import refresh_all_timers, sync_timer

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
    track_id: int
    team_members: list[str] = Field(default_factory=list)
    github_url: str = ""
    demo_url: str = ""
    submission_url: str = ""


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


class ReassignIn(BaseModel):
    judge_id: int
    force: bool = False


class ScheduleIn(BaseModel):
    starts_at: datetime
    duration_minutes: int = Field(ge=1, le=180)
    location: str = ""


class ExtendIn(BaseModel):
    minutes: int = Field(ge=1, le=60)


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
    _ = assignment.timer
    return assignment


def _project_admin(project: Project) -> dict:
    data = project_dict(project)
    data["assignments"] = [
        {
            "assignment_id": assignment.id,
            "judge_id": assignment.judge_id,
            "judge_name": assignment.judge.name,
            "status": assignment.evaluation.status if assignment.evaluation is not None else "not_started",
        }
        for assignment in sorted(project.assignments, key=lambda item: item.judge.name.lower())
    ]
    return data


def _apply_project(project: Project, body: ProjectIn, track: Track) -> None:
    project.name = body.name.strip()
    project.description = body.description.strip()
    project.track = track
    project.track_id = track.id
    project.team_members = _members(body.team_members)
    project.github_url = _clean_url(body.github_url, "GitHub link")
    project.demo_url = _clean_url(body.demo_url, "Demo link")
    project.submission_url = _clean_url(body.submission_url, "Submission link")
    project.updated_at = utcnow()


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    progress = progress_snapshot(db)
    delays = list_delays(db)
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
        "delays": delays,
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
    from app.models import Score

    saved_values = db.scalars(
        select(Score.value).where(Score.criterion_id == criterion.id, Score.value.is_not(None))
    ).all()
    if any(value < body.min_score or value > body.max_score for value in saved_values):
        raise HTTPException(
            status_code=409,
            detail="Saved scores fall outside that range. Edit those scorecards before changing the scale.",
        )
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
    track = _get_track(db, body.track_id)
    project = Project(name="", description="", track_id=track.id, team_members=[])
    try:
        _apply_project(project, body, track)
    except DomainError as exc:
        _http(exc)
    db.add(project)
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
    track = _get_track(db, body.track_id)
    try:
        _apply_project(project, body, track)
    except DomainError as exc:
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
            evaluation = assignment.evaluation
            status = evaluation.status if evaluation is not None else "not_started"
            if evaluation is not None and (evaluation.submission_count > 0 or status == "submitted"):
                raise DomainError("This project has a submitted evaluation and can't be deleted.", 409)
            if status in {"draft_saved", "ready_to_submit"} and not force:
                raise DomainError("This project has saved drafts. Confirm to delete it.", 409)
        for assignment in list(project.assignments):
            delete_assignment(db, assignment, force=True)
        if project.schedule is not None:
            db.delete(project.schedule)
        db.delete(project)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {"ok": True}


@router.post("/projects/import")
async def import_projects(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV is missing a header row")
    fields = {(name or "").strip().lower() for name in reader.fieldnames}
    if "name" not in fields or "track" not in fields:
        raise HTTPException(status_code=400, detail="CSV must include name and track columns")

    tracks_by_name = {track.name.lower(): track for track in db.scalars(select(Track)).all()}
    created = 0
    errors: list[dict] = []
    for index, raw_row in enumerate(reader, start=2):
        if None in raw_row or any(isinstance(value, list) for value in raw_row.values()):
            errors.append(
                {
                    "row": index,
                    "message": "This row has extra commas. Quote any field that contains a comma.",
                }
            )
            continue
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw_row.items()}
        name = row.get("name", "")
        track_name = row.get("track", "")
        if not name or not track_name:
            errors.append({"row": index, "message": "Name and track are required"})
            continue
        track = tracks_by_name.get(track_name.lower())
        if track is None:
            track = Track(name=track_name, description="")
            db.add(track)
            db.flush()
            tracks_by_name[track.name.lower()] = track
        members = [part.strip() for part in row.get("team_members", "").split(";") if part.strip()]
        project = Project(
            name=name,
            description=row.get("description", ""),
            track_id=track.id,
            team_members=members,
            github_url="",
            demo_url="",
            submission_url="",
            updated_at=utcnow(),
        )
        try:
            project.github_url = _clean_url(row.get("github_url", ""), "GitHub link")
            project.demo_url = _clean_url(row.get("demo_url", ""), "Demo link")
            project.submission_url = _clean_url(row.get("submission_url", ""), "Submission link")
        except DomainError as exc:
            errors.append({"row": index, "message": exc.message})
            continue
        db.add(project)
        created += 1
    _commit(db)
    return {"created": created, "errors": errors}


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
        payload_assignments.append(
            {
                "assignment_id": assignment.id,
                "project_id": assignment.project_id,
                "project_name": project.name if project is not None else "",
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
    try:
        assignment = create_assignment(db, judge, project)
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
    if judge.id == assignment.judge_id:
        return {
            "assignment_id": assignment.id,
            "judge_id": judge.id,
            "project_id": project.id,
            "status": assignment.evaluation.status if assignment.evaluation else "not_started",
        }
    try:
        delete_assignment(db, assignment, body.force)
        created = create_assignment(db, judge, project)
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
        delete_assignment(db, assignment, force)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    return {"ok": True}


def _schedule_row(project: Project, delays_by_id: dict[int, dict]) -> dict:
    data = project_dict(project)
    data["delay"] = delays_by_id.get(project.id)
    judges = []
    for assignment in sorted(project.assignments, key=lambda item: item.judge.name.lower()):
        timer = assignment.timer
        view = sync_timer(timer, project.schedule) if timer is not None else None
        judges.append(
            {
                "judge_id": assignment.judge_id,
                "judge_name": assignment.judge.name,
                "timer_status": view["status"] if view else "idle",
                "actual_started_at": view["actual_started_at"] if view else None,
                "actual_ended_at": view["actual_ended_at"] if view else None,
            }
        )
    data["judges"] = judges
    return data


@router.get("/schedule")
def schedule(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> dict:
    refresh_all_timers(db)
    delays = {item["project_id"]: item for item in list_delays(db)}
    rows = [_schedule_row(project, delays) for project in load_projects(db)]
    rows.sort(key=lambda item: (item["starts_at"] is None, item["starts_at"] or "", item["name"].lower()))
    _commit(db)
    return {"projects": rows}


@router.put("/schedule/{project_id}")
def save_schedule(
    project_id: int,
    body: ScheduleIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    starts_at = _as_eastern(body.starts_at)
    slot = project.schedule
    now = utcnow()
    if slot is None:
        slot = ScheduleSlot(
            project_id=project.id,
            starts_at=starts_at,
            duration_minutes=body.duration_minutes,
            extension_minutes=0,
            location=body.location.strip(),
            updated_at=now,
        )
        db.add(slot)
        project.schedule = slot
    else:
        slot.starts_at = starts_at
        slot.duration_minutes = body.duration_minutes
        slot.location = body.location.strip()
        slot.updated_at = now
    _commit(db)
    project = _require_project(db, project_id)
    delays = {item["project_id"]: item for item in list_delays(db)}
    _commit(db)
    return _schedule_row(project, delays)


@router.post("/schedule/{project_id}/extend")
def extend_schedule(
    project_id: int,
    body: ExtendIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_organizer),
) -> dict:
    project = _require_project(db, project_id)
    if project.schedule is None:
        raise HTTPException(status_code=400, detail="Set a time slot before extending it")
    project.schedule.extension_minutes += body.minutes
    project.schedule.updated_at = utcnow()
    _commit(db)
    project = _require_project(db, project_id)
    delays = {item["project_id"]: item for item in list_delays(db)}
    _commit(db)
    return _schedule_row(project, delays)


@router.get("/delays")
def delays(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> list[dict]:
    found = list_delays(db)
    _commit(db)
    return found


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
        reopen_evaluation(db, evaluation, user, project.track_id)
    except DomainError as exc:
        db.rollback()
        _http(exc)
    _commit(db)
    payload = evaluation_dict(evaluation, include_private=False)
    payload["judge_name"] = evaluation.judge.name if evaluation.judge is not None else ""
    return payload


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def write_csv_row(writer: csv.writer, values: list[object]) -> None:
    writer.writerow([csv_cell(value) for value in values])


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
    write_csv_row(writer, header)

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
                write_csv_row(writer, base + ["", "", ""] + ["" for _ in criteria] + ["", ""])
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
            write_csv_row(writer, row)
    return _csv_response(buffer.getvalue(), "results.csv")


@router.get("/export/feedback.csv")
def export_feedback(db: Session = Depends(get_db), _: User = Depends(require_organizer)) -> Response:
    report = build_results(db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    write_csv_row(
        writer,
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
        ],
    )
    for project in report["projects"]:
        for evaluation in project["evaluations"]:
            if evaluation["status"] != "submitted" and evaluation["submission_count"] <= 0:
                continue
            write_csv_row(
                writer,
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
                ],
            )
    return _csv_response(buffer.getvalue(), "feedback.csv")
