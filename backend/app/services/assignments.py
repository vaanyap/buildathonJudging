from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    AssignmentExclusion,
    Evaluation,
    Project,
    ProjectTrack,
    Timer,
    Track,
    TrackAssignment,
    User,
)
from app.services.domain import DomainError
from app.services.evaluations import ensure_score_rows, utcnow


def judge_track_ids(db: Session, judge_id: int) -> set[int]:
    return set(db.scalars(select(TrackAssignment.track_id).where(TrackAssignment.judge_id == judge_id)).all())


def _exclusion(db: Session, judge_id: int, project_id: int, track_id: int) -> AssignmentExclusion | None:
    return db.scalar(
        select(AssignmentExclusion).where(
            AssignmentExclusion.judge_id == judge_id,
            AssignmentExclusion.project_id == project_id,
            AssignmentExclusion.track_id == track_id,
        )
    )


def _clear_exclusion(db: Session, judge_id: int, project_id: int, track_id: int) -> None:
    row = _exclusion(db, judge_id, project_id, track_id)
    if row is not None:
        db.delete(row)
        db.flush()


def ensure_timer(db: Session, judge: User, project: Project) -> Timer:
    timer = db.scalar(select(Timer).where(Timer.judge_id == judge.id, Timer.project_id == project.id))
    if timer is not None:
        return timer
    timer = Timer(
        judge_id=judge.id,
        project_id=project.id,
        active_phase="presentation",
        presentation_accumulated_seconds=0,
        presentation_status="idle",
        qa_accumulated_seconds=0,
        qa_status="idle",
        updated_at=utcnow(),
    )
    timer.judge = judge
    timer.project = project
    db.add(timer)
    db.flush()
    return timer


def create_assignment(db: Session, judge: User, project: Project, track: Track, source: str = "project") -> Assignment:
    if judge.role != "judge":
        raise DomainError("Only judge accounts can be assigned to projects")
    if not judge.active:
        raise DomainError("That judge account is inactive")
    member = db.scalar(
        select(ProjectTrack).where(ProjectTrack.project_id == project.id, ProjectTrack.track_id == track.id)
    )
    if member is None:
        raise DomainError("That project is not in this track")
    if source == "project":
        _clear_exclusion(db, judge.id, project.id, track.id)
    existing = db.scalar(
        select(Assignment).where(
            Assignment.judge_id == judge.id,
            Assignment.project_id == project.id,
            Assignment.track_id == track.id,
        )
    )
    if existing is not None:
        if source == "project" and existing.source != "project":
            existing.source = "project"
            ensure_timer(db, judge, project)
            return existing
        raise DomainError("That judge is already assigned to this project in this track", 409)

    now = utcnow()
    assignment = Assignment(
        judge_id=judge.id,
        project_id=project.id,
        track_id=track.id,
        source=source,
        created_at=now,
    )
    assignment.judge = judge
    assignment.project = project
    assignment.track = track
    db.add(assignment)
    db.flush()

    evaluation = Evaluation(
        assignment_id=assignment.id,
        judge_id=judge.id,
        project_id=project.id,
        track_id=track.id,
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
    evaluation.track = track
    db.add(evaluation)
    db.flush()
    assignment.evaluation = evaluation
    ensure_timer(db, judge, project)
    ensure_score_rows(db, evaluation)
    db.flush()
    return assignment


def delete_assignment(db: Session, assignment: Assignment, force: bool, exclude: bool = False) -> None:
    evaluation = assignment.evaluation
    status = evaluation.status if evaluation is not None else "not_started"
    if status == "submitted" or (evaluation is not None and evaluation.submission_count > 0):
        raise DomainError("Evaluations that have been submitted can't be removed.", 409)
    if status in {"draft_saved", "ready_to_submit"} and not force:
        raise DomainError("This judge has saved work. Confirm to discard the draft.", 409)
    if exclude and assignment.track_id in judge_track_ids(db, assignment.judge_id):
        if _exclusion(db, assignment.judge_id, assignment.project_id, assignment.track_id) is None:
            db.add(
                AssignmentExclusion(
                    judge_id=assignment.judge_id,
                    project_id=assignment.project_id,
                    track_id=assignment.track_id,
                    created_at=utcnow(),
                )
            )
    if evaluation is not None:
        for score in list(evaluation.scores):
            db.delete(score)
        for audit in list(evaluation.audits):
            db.delete(audit)
        db.delete(evaluation)
    judge_id = assignment.judge_id
    project_id = assignment.project_id
    db.delete(assignment)
    db.flush()
    remaining = db.scalar(
        select(Assignment.id).where(Assignment.judge_id == judge_id, Assignment.project_id == project_id).limit(1)
    )
    if remaining is None:
        timer = db.scalar(select(Timer).where(Timer.judge_id == judge_id, Timer.project_id == project_id))
        if timer is not None:
            db.delete(timer)
    db.flush()


def assignment_is_visible(db: Session, assignment: Assignment) -> bool:
    if assignment.source == "project":
        return True
    return assignment.track_id in judge_track_ids(db, assignment.judge_id)


def ensure_track_assignments(db: Session, judge: User) -> None:
    track_ids = judge_track_ids(db, judge.id)
    if not track_ids:
        return
    memberships = db.scalars(select(ProjectTrack).where(ProjectTrack.track_id.in_(track_ids))).all()
    for membership in memberships:
        if _exclusion(db, judge.id, membership.project_id, membership.track_id) is not None:
            continue
        existing = db.scalar(
            select(Assignment).where(
                Assignment.judge_id == judge.id,
                Assignment.project_id == membership.project_id,
                Assignment.track_id == membership.track_id,
            )
        )
        if existing is not None:
            continue
        project = membership.project or db.get(Project, membership.project_id)
        track = membership.track or db.get(Track, membership.track_id)
        if project is None or track is None:
            continue
        create_assignment(db, judge, project, track, source="track")


def grant_track_to_project(db: Session, project: Project, track: Track) -> None:
    judge_ids = db.scalars(select(TrackAssignment.judge_id).where(TrackAssignment.track_id == track.id)).all()
    for judge_id in judge_ids:
        judge = db.get(User, judge_id)
        if judge is None or _exclusion(db, judge.id, project.id, track.id) is not None:
            continue
        existing = db.scalar(
            select(Assignment).where(
                Assignment.judge_id == judge.id,
                Assignment.project_id == project.id,
                Assignment.track_id == track.id,
            )
        )
        if existing is not None:
            continue
        create_assignment(db, judge, project, track, source="track")


def release_track_sourced_assignments(db: Session, project: Project, track: Track) -> None:
    track_judge_ids = set(
        db.scalars(select(TrackAssignment.judge_id).where(TrackAssignment.track_id == track.id)).all()
    )
    assignments = db.scalars(
        select(Assignment).where(Assignment.project_id == project.id, Assignment.track_id == track.id)
    ).all()
    for assignment in assignments:
        if assignment.source != "track" or assignment.judge_id in track_judge_ids:
            continue
        evaluation = assignment.evaluation
        status = evaluation.status if evaluation is not None else "not_started"
        submitted = status == "submitted" or (evaluation is not None and evaluation.submission_count > 0)
        if submitted:
            continue
        delete_assignment(db, assignment, force=True, exclude=False)


def sync_project_tracks(db: Session, project: Project, tracks: list[Track]) -> list[str]:
    """Replace prize categories. Submitted scorecards keep a category that would otherwise disappear."""
    wanted = {track.id: track for track in tracks}
    current = {member.track_id: member for member in list(project.memberships)}
    warnings: list[str] = []
    for track_id, member in current.items():
        if track_id in wanted:
            continue
        submitted = db.scalar(
            select(Evaluation.id).where(
                Evaluation.project_id == project.id,
                Evaluation.track_id == track_id,
                Evaluation.status == "submitted",
            )
        )
        if submitted is not None:
            warnings.append(f"Kept {member.track.name} because a submitted scorecard uses it")
            continue
        release_track_sourced_assignments(db, project, member.track)
        db.delete(member)
    db.flush()
    for track_id, track in wanted.items():
        if track_id in current:
            continue
        member = ProjectTrack(project_id=project.id, track_id=track.id)
        member.project = project
        member.track = track
        db.add(member)
        project.memberships.append(member)
        db.flush()
        grant_track_to_project(db, project, track)
    return warnings


def assign_judge_to_track(db: Session, judge: User, track: Track) -> TrackAssignment:
    if judge.role != "judge":
        raise DomainError("Only judge accounts can be assigned to a track")
    if not judge.active:
        raise DomainError("That judge account is inactive")
    existing = db.scalar(
        select(TrackAssignment).where(TrackAssignment.judge_id == judge.id, TrackAssignment.track_id == track.id)
    )
    if existing is not None:
        raise DomainError("That judge is already assigned to this track", 409)
    row = TrackAssignment(judge_id=judge.id, track_id=track.id, created_at=utcnow())
    row.judge = judge
    row.track = track
    db.add(row)
    db.flush()
    ensure_track_assignments(db, judge)
    return row


def remove_judge_from_track(db: Session, judge: User, track: Track) -> None:
    row = db.scalar(
        select(TrackAssignment).where(TrackAssignment.judge_id == judge.id, TrackAssignment.track_id == track.id)
    )
    if row is None:
        raise DomainError("That judge is not assigned to this track", 404)
    db.delete(row)
    db.flush()
    assignments = db.scalars(
        select(Assignment).where(
            Assignment.judge_id == judge.id,
            Assignment.track_id == track.id,
            Assignment.source == "track",
        )
    ).all()
    for assignment in assignments:
        evaluation = assignment.evaluation
        status = evaluation.status if evaluation is not None else "not_started"
        submitted = status == "submitted" or (evaluation is not None and evaluation.submission_count > 0)
        if submitted:
            continue
        delete_assignment(db, assignment, force=True, exclude=False)
