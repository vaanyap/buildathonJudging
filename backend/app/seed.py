"""Demo data built from the MLH submissions export.

Judges are assigned to prize categories and work through those projects.
A few scorecards are filled in so the demo shows drafts, a discrepancy, and a
cross-track flag.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.migrate import upgrade_schema
from app.models import (
    Assignment,
    AssignmentExclusion,
    AuditEvent,
    Criterion,
    DiscrepancyReview,
    DiscrepancySettings,
    Evaluation,
    Project,
    ProjectTrack,
    Score,
    Timer,
    TimerDefaults,
    Track,
    TrackAssignment,
    User,
)
from app.schemas import EvaluationIn
from app.services.assignments import assign_judge_to_track
from app.services.discrepancies import get_settings
from app.services.evaluations import apply_autosave, mark_viewed, submit_evaluation, utcnow
from app.services.submissions_import import import_submissions
from app.services.timers import get_timer_defaults

ORGANIZER_EMAIL = "v@v"
ORGANIZER_PASSWORD = "v"
JUDGE_PASSWORD = "judge-demo"
CSV_PATH = Path(__file__).resolve().parents[2] / "examples" / "submissions_export.csv"

DEFAULT_CRITERIA = [
    ("technical", "Technical execution", "Soundness of the implementation, architecture, and engineering choices.", 1),
    ("innovation", "Innovation and creativity", "Originality of the idea and how the team approached the problem.", 2),
    ("impact", "Impact and usefulness", "Who it helps and whether the project is useful beyond the demo.", 3),
    ("presentation", "Presentation and demonstration", "Clarity of the pitch and whether the demo showed the project working.", 4),
]

JUDGES = [
    ("Alex Rivera", "vpuri22@uwo.ca"),
    ("Jordan Lee", "jordan.lee@nyu.edu"),
    ("Maya Chen", "maya.chen@nyu.edu"),
    ("Priya Patel", "priya.patel@nyu.edu"),
    ("Riley Nguyen", "riley.nguyen@nyu.edu"),
    ("Sam Okonkwo", "sam.okonkwo@nyu.edu"),
]

TRACK_JUDGES = {
    "Best use of True Markets Gateway API": ["Alex Rivera", "Jordan Lee"],
    "Most creative use of ElevenLabs": ["Maya Chen", "Priya Patel"],
    "SpaceX Super Intelligence Track": ["Riley Nguyen", "Sam Okonkwo"],
    "Best Open-Source AI Project": ["Alex Rivera", "Maya Chen"],
    "Best Use of Snowflake": ["Jordan Lee", "Priya Patel"],
}


def seed_criteria(db: Session) -> list[Criterion]:
    existing = list(db.scalars(select(Criterion).order_by(Criterion.sort_order)).all())
    if existing:
        return existing
    rows: list[Criterion] = []
    for key, name, description, order in DEFAULT_CRITERIA:
        row = Criterion(
            key=key,
            name=name,
            description=description,
            min_score=1,
            max_score=5,
            sort_order=order,
            required=True,
            active=True,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _reset(db: Session) -> None:
    for model in (
        AuditEvent,
        Score,
        Evaluation,
        Timer,
        DiscrepancyReview,
        AssignmentExclusion,
        Assignment,
        TrackAssignment,
        ProjectTrack,
        Project,
        DiscrepancySettings,
        TimerDefaults,
        Criterion,
        Track,
        User,
    ):
        db.execute(delete(model))
    db.commit()


def _users(db: Session) -> dict[str, User]:
    db.add(
        User(
            email=ORGANIZER_EMAIL,
            name="Avery Shah",
            password_hash=hash_password(ORGANIZER_PASSWORD),
            role="organizer",
            active=True,
        )
    )
    people: dict[str, User] = {}
    for name, email in JUDGES:
        judge = User(
            email=email,
            name=name,
            password_hash=hash_password(JUDGE_PASSWORD),
            role="judge",
            active=True,
        )
        db.add(judge)
        people[name] = judge
    db.flush()
    return people


def _orders(db: Session) -> None:
    tracks = db.scalars(select(Track)).all()
    for track in tracks:
        members = db.scalars(select(ProjectTrack).where(ProjectTrack.track_id == track.id)).all()
        ordered = sorted(
            members,
            key=lambda member: (
                member.project.submitted_at is None,
                member.project.submitted_at.timestamp() if member.project.submitted_at else 0,
                member.project.name.lower(),
            ),
        )
        for index, member in enumerate(ordered, start=1):
            member.presentation_order = index
    db.flush()


def _assignment(db: Session, judge: str, project: str, track: str, people: dict[str, User]) -> Assignment:
    row = db.scalar(
        select(Assignment)
        .join(Project, Project.id == Assignment.project_id)
        .join(Track, Track.id == Assignment.track_id)
        .where(Project.name == project, Track.name == track, Assignment.judge_id == people[judge].id)
    )
    if row is None:
        raise RuntimeError(f"Missing assignment {judge} / {project} / {track}")
    return row


def _score(db: Session, assignment: Assignment, kind: str, scores: list[int] | None, **extra) -> None:
    evaluation = assignment.evaluation
    if evaluation is None:
        return
    mark_viewed(evaluation)
    if kind == "viewed":
        return
    values = scores or [None, None, None, None]
    payload = EvaluationIn(
        scores={
            "technical": values[0],
            "innovation": values[1],
            "impact": values[2],
            "presentation": values[3],
        },
        private_notes=extra.get("private", ""),
        formal_feedback=extra.get("feedback", ""),
        alignment=extra.get("alignment"),
        alignment_explanation=extra.get("explanation", ""),
        cross_track_flag=extra.get("cross_track_id") is not None,
        recommended_track_id=extra.get("cross_track_id"),
        cross_track_explanation=extra.get("cross_explanation", ""),
    )
    apply_autosave(db, evaluation, payload, assignment.judge, assignment.track_id)
    if kind == "submitted":
        submit_evaluation(db, evaluation, assignment.judge, assignment.track_id)


def _seed_scorecards(db: Session, people: dict[str, User]) -> None:
    spacex = db.scalar(select(Track).where(Track.name == "SpaceX Super Intelligence Track"))
    if spacex is None:
        raise RuntimeError("SpaceX track missing after import")
    jarvis_open = "Best Open-Source AI Project"
    _score(
        db,
        _assignment(db, "Alex Rivera", "JARVIS", jarvis_open, people),
        "submitted",
        [5, 5, 5, 4],
        alignment="strong",
        explanation="The open-source agent work is the center of this prize.",
        feedback="Clear demo and a working voice loop. I would use the decision list.",
        private="Private note from the high score. Visible only while this discrepancy is open.",
    )
    _score(
        db,
        _assignment(db, "Maya Chen", "JARVIS", jarvis_open, people),
        "submitted",
        [2, 2, 1, 2],
        alignment="partial",
        explanation="The repo is open, but the demo felt like a thin wrapper.",
        feedback="I had trouble seeing what was computed versus hard-coded.",
        private="Private note from the low score. The other judge and I are far apart.",
    )
    _score(
        db,
        _assignment(db, "Maya Chen", "CityLine", "Most creative use of ElevenLabs", people),
        "submitted",
        [4, 4, 5, 4],
        alignment="partial",
        explanation="Voice is real, but the routing problem is closer to the super-intelligence prize.",
        feedback="The station announcements were specific, not generic hint text.",
        cross_track_id=spacex.id,
        cross_explanation="Flag for the SpaceX Super Intelligence Track. Keep the ElevenLabs score as judged.",
    )
    _score(
        db,
        _assignment(db, "Alex Rivera", "perps.fun", "Best use of True Markets Gateway API", people),
        "draft",
        [4, None, 3, None],
        private="Ask about the order book before I score presentation.",
    )
    _score(
        db,
        _assignment(db, "Jordan Lee", "Droplet", "Best use of True Markets Gateway API", people),
        "viewed",
        None,
    )
    _score(
        db,
        _assignment(db, "Priya Patel", "NYC Subway Optimization", "Best Use of Snowflake", people),
        "ready",
        [4, 4, 4, 5],
        alignment="strong",
        explanation="Snowflake is doing the transit analysis, which is this prize.",
        feedback="The map made the delay story concrete.",
    )


def _seed_timers(db: Session, people: dict[str, User]) -> None:
    now = utcnow()
    jarvis = db.scalar(select(Project).where(Project.name == "JARVIS"))
    droplet = db.scalar(select(Project).where(Project.name == "Droplet"))
    if jarvis is None or droplet is None:
        return
    alex = people["Alex Rivera"]
    jordan = people["Jordan Lee"]
    jarvis_timer = db.scalar(select(Timer).where(Timer.judge_id == alex.id, Timer.project_id == jarvis.id))
    if jarvis_timer is not None:
        started = now - timedelta(minutes=4)
        ended = now - timedelta(seconds=45)
        jarvis_timer.presentation_status = "time_up"
        jarvis_timer.presentation_started_at = started
        jarvis_timer.presentation_ended_at = ended
        jarvis_timer.presentation_accumulated_seconds = 180
        jarvis_timer.qa_status = "running"
        jarvis_timer.qa_started_at = now - timedelta(seconds=30)
        jarvis_timer.qa_running_since = jarvis_timer.qa_started_at
        jarvis_timer.qa_accumulated_seconds = 0
        jarvis_timer.active_phase = "qa"
        jarvis_timer.updated_at = now
    droplet_timer = db.scalar(select(Timer).where(Timer.judge_id == jordan.id, Timer.project_id == droplet.id))
    if droplet_timer is not None:
        droplet_timer.presentation_status = "running"
        droplet_timer.presentation_started_at = now - timedelta(minutes=1)
        droplet_timer.presentation_running_since = droplet_timer.presentation_started_at
        droplet_timer.active_phase = "presentation"
        droplet_timer.updated_at = now


def seed_database(db: Session) -> None:
    seed_criteria(db)
    get_timer_defaults(db)
    get_settings(db)
    people = _users(db)
    summary = import_submissions(db, CSV_PATH.read_text(encoding="utf-8"))
    if summary["skipped"]:
        raise RuntimeError(f"Seed import skipped rows: {summary['skipped']}")
    _orders(db)
    tracks = {track.name: track for track in db.scalars(select(Track)).all()}
    for track_name, judge_names in TRACK_JUDGES.items():
        track = tracks[track_name]
        for judge_name in judge_names:
            assign_judge_to_track(db, people[judge_name], track)
    _seed_scorecards(db, people)
    _seed_timers(db, people)
    db.commit()


def seed_if_empty(db: Session) -> bool:
    existing = db.scalar(select(User.id).limit(1))
    if existing is not None:
        return False
    seed_database(db)
    return True


def reset_and_seed(db: Session) -> None:
    _reset(db)
    seed_database(db)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the judging database")
    parser.add_argument("--reset", action="store_true", help="Delete existing rows and seed again")
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    upgrade_schema(engine)
    db = SessionLocal()
    try:
        if args.reset:
            reset_and_seed(db)
            print("Reset and seeded demo data.")
        elif seed_if_empty(db):
            print("Seeded demo data.")
        else:
            print("Database already has users; nothing to do. Pass --reset to reseed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
