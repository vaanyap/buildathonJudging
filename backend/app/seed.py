"""Demo data for a Tech@NYU buildathon judging day.

The schedule is anchored to the current clock so some presentations are already
late, one is running long, and the rest are still upcoming.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models import (
    Assignment,
    AuditEvent,
    Criterion,
    Evaluation,
    Project,
    ScheduleSlot,
    Score,
    Timer,
    Track,
    User,
)
from app.schemas import EvaluationIn
from app.auth import hash_password
from app.services.assignments import create_assignment
from app.services.evaluations import apply_autosave, mark_viewed, submit_evaluation, utcnow

ET = ZoneInfo("America/New_York")

ORGANIZER_EMAIL = "organizer@tech.nyu.edu"
ORGANIZER_PASSWORD = "organizer-demo"
JUDGE_PASSWORD = "judge-demo"

DEFAULT_CRITERIA = [
    ("technical", "Technical execution", "Soundness of the implementation, architecture, and engineering choices.", 1),
    ("innovation", "Innovation and creativity", "Originality of the idea and how the team approached the problem.", 2),
    ("impact", "Impact and usefulness", "Who it helps and whether the project is useful beyond the demo.", 3),
    ("presentation", "Presentation and demonstration", "Clarity of the pitch and whether the demo showed the project working.", 4),
]

TRACKS = [
    ("Artificial Intelligence", "Applied AI, from models to interfaces."),
    ("Sustainability", "Climate, campus operations, and resource use."),
    ("Healthcare", "Access, care delivery, and health tools."),
    ("Education", "Learning, research, and academic life."),
    ("Fintech", "Payments, student finance, and access to money."),
    ("Developer Tools", "Tools that help people build software."),
]

JUDGES = [
    ("Alex Rivera", "alex.rivera@nyu.edu"),
    ("Jordan Lee", "jordan.lee@nyu.edu"),
    ("Maya Chen", "maya.chen@nyu.edu"),
    ("Priya Patel", "priya.patel@nyu.edu"),
    ("Riley Nguyen", "riley.nguyen@nyu.edu"),
    ("Sam Okonkwo", "sam.okonkwo@nyu.edu"),
]

# Six rounds, three rooms. Each judge appears once per round.
PAIRINGS = [
    [(0, 1), (2, 3), (4, 5)],
    [(0, 2), (1, 4), (3, 5)],
    [(0, 3), (1, 5), (2, 4)],
    [(0, 4), (1, 3), (2, 5)],
    [(0, 5), (1, 2), (3, 4)],
    [(0, 1), (2, 4), (3, 5)],
]

ROOMS = ["Tisch LC19", "Leslie eLab", "MakerSpace"]
SCORE_KEYS = ["technical", "innovation", "impact", "presentation"]


def _project(
    name: str,
    track: str,
    description: str,
    team: list[str],
    slug: str,
) -> dict:
    return {
        "name": name,
        "track": track,
        "description": description,
        "team": team,
        "github_url": f"https://github.com/tech-nyu-buildathon/{slug}",
        "demo_url": f"https://{slug}.demo.buildathon.tech.nyu.edu",
        "submission_url": f"https://buildathon.tech.nyu.edu/submissions/{slug}",
    }


PROJECTS = [
    _project(
        "ShelterLink",
        "Sustainability",
        "A shift board for nearby shelters. Students see open volunteer slots, travel time from campus, and who else from their club is already signed up.",
        ["Rosa Diaz", "Kenji Watanabe"],
        "shelterlink",
    ),
    _project(
        "Atlas Note",
        "Artificial Intelligence",
        "Live notes for student-org meetings. It turns a messy transcript into decisions, owners, and due dates the board can actually use the next morning.",
        ["Lena Park", "Omar Farouk", "Chris Wu"],
        "atlas-note",
    ),
    _project(
        "GreenRoute",
        "Sustainability",
        "A campus walking planner that prefers lower-emission routes, including indoor shortcuts through buildings that are open after dark.",
        ["Hannah Brooks", "Dev Kapoor"],
        "greenroute",
    ),
    _project(
        "SignSpeak",
        "Artificial Intelligence",
        "Camera-based ASL practice. It scores handshape on short drills and keeps the feedback private to the person practicing.",
        ["Noah Kim", "Aisha Bello"],
        "signspeak",
    ),
    _project(
        "CampusBites",
        "Sustainability",
        "Matches end-of-day dining-hall surplus with student groups that can pick it up, and records what was saved so the hall can plan the next week.",
        ["Elena Rossi", "Malik Johnson"],
        "campusbites",
    ),
    _project(
        "MediBuddy",
        "Healthcare",
        "A medication reminder and refill tracker designed with older adults. The companion view uses large type and a single daily checklist.",
        ["Sofia Alvarez", "Jonah Weiss"],
        "medibuddy",
    ),
    _project(
        "PulseCheck",
        "Healthcare",
        "A two-minute campus wellness check-in that routes students to a specific NYU resource instead of a generic hotline list.",
        ["Anika Das", "Leo Martins"],
        "pulsecheck",
    ),
    _project(
        "OfficeHours+",
        "Education",
        "Matches students to TA office hours by topic and shows a live queue, so people stop guessing which room still has space.",
        ["Ben Carter", "Mei Lin", "Sara Iqbal"],
        "officehours-plus",
    ),
    _project(
        "LabLens",
        "Education",
        "Turns a research-paper PDF into a one-page brief with a glossary and a small citation map for students reading outside their major.",
        ["Camille Dubois", "Hector Nguyen"],
        "lablens",
    ),
    _project(
        "NeighborNet",
        "Sustainability",
        "A floor-level sharing board for borrowing tools and posting leftovers. The point is fewer duplicate purchases and less food in the trash.",
        ["Jules Moreau", "Samira Khan"],
        "neighbornet",
    ),
    _project(
        "QuizForge",
        "Education",
        "Builds practice quizzes from lecture notes and explains why an answer is wrong, using the student's own wording in the explanation.",
        ["Yuki Tanaka", "Andre Silva"],
        "quizforge",
    ),
    _project(
        "Dues",
        "Fintech",
        "A treasurer tool for club dues, reimbursements, and a shared ledger that can be reconciled with the bank export.",
        ["Grace Adelayo", "Tom Becker"],
        "dues",
    ),
    _project(
        "MicroGrant",
        "Fintech",
        "Tiny emergency grants for students. The application is short on purpose, and a human still approves every transfer.",
        ["Fatima Zahra", "Colin Byrne"],
        "microgrant",
    ),
    _project(
        "FairFare",
        "Fintech",
        "Splits cross-border family expenses and shows the fee before anyone sends money, including the recipient's expected arrival time.",
        ["Luis Romero", "Nina Petrov"],
        "fairfare",
    ),
    _project(
        "PR Pilot",
        "Developer Tools",
        "A review assistant that comments on a diff in plain language and suggests one test the author could add before requesting review.",
        ["Isha Reddy", "Markos Hale"],
        "pr-pilot",
    ),
    _project(
        "Dockyard",
        "Developer Tools",
        "Snapshots a team's local development environment so a new member can boot the project without a weekend of setup notes.",
        ["Chloe Martin", "Vijay Raman"],
        "dockyard",
    ),
    _project(
        "FormFit",
        "Healthcare",
        "A phone-camera coach for physical-therapy exercises, built with a clinic partner. It watches form and stays quiet when the rep looks right.",
        ["Amelia Grant", "Omar Said"],
        "formfit",
    ),
    _project(
        "TraceLog",
        "Developer Tools",
        "A small distributed-trace viewer for student backends that do not want to run a full observability stack for a class project.",
        ["Ethan Cole", "Nadia Hassan"],
        "tracelog",
    ),
]


def _scores(values: list[int | None] | None) -> dict[str, int | None]:
    if values is None:
        return {key: None for key in SCORE_KEYS}
    return {key: values[index] if index < len(values) else None for index, key in enumerate(SCORE_KEYS)}


def _eval(
    kind: str,
    scores: list[int | None] | None = None,
    alignment: str | None = None,
    explanation: str = "",
    feedback: str = "",
    private: str = "",
    cross_track: str | None = None,
    cross_explanation: str = "",
) -> dict:
    return {
        "kind": kind,
        "scores": scores,
        "alignment": alignment,
        "explanation": explanation,
        "feedback": feedback,
        "private": private,
        "cross_track": cross_track,
        "cross_explanation": cross_explanation,
    }


# Two entries per project, applied in judge-name order.
PLANS: dict[str, list[dict]] = {
    "ShelterLink": [_eval("not_started"), _eval("not_started")],
    "Atlas Note": [
        _eval(
            "submitted",
            [5, 4, 5, 5],
            "strong",
            "The meeting-note workflow is squarely an AI product and the demo stayed inside that job.",
            "The decision list at the end of the demo was the most convincing part. I would use this for a club board.",
            "Strong room presence. Not for the packet: they went slightly over while answering a side question.",
        ),
        _eval(
            "submitted",
            [4, 5, 4, 4],
            "strong",
            "Transcript to owners and dates is the right AI task for this track.",
            "Clear problem, working demo, and they were honest about where the model still drops a name.",
        ),
    ],
    "GreenRoute": [
        _eval(
            "submitted",
            [5, 5, 5, 5],
            "strong",
            "Indoor routing and emissions are both sustainability problems, and the project treats them as one.",
            "Polished demo and a real campus map. This is one of the stronger sustainability builds.",
        ),
        _eval(
            "submitted",
            [2, 3, 2, 2],
            "partial",
            "The route idea fits, but a lot of the demo was a generic map wrapper with emissions added as a label.",
            "I had trouble seeing what was computed versus hard-coded. The team was game, the build looked thin.",
            "Scores are far from the other judge. I want a second look before this is ranked.",
        ),
    ],
    "SignSpeak": [
        _eval(
            "submitted",
            [4, 5, 5, 4],
            "strong",
            "Vision model plus a practice loop is an AI project, and they evaluated handshape rather than just classifying a word.",
            "Respectful about privacy. The drill loop worked on stage.",
        ),
        _eval(
            "submitted",
            [4, 4, 5, 3],
            "partial",
            "The model work fits AI, but the value I saw was closer to a clinical communication tool.",
            "Useful and careful. The demo was a little slow to calibrate.",
            cross_track="Healthcare",
            cross_explanation="A clinic could use the same drill loop for patients rebuilding communication. Worth a healthcare mention without moving it off AI.",
        ),
    ],
    "CampusBites": [
        _eval(
            "submitted",
            [4, 4, 5, 4],
            "strong",
            "Food recovery on campus is a direct sustainability outcome, and they measured pounds, not just signups.",
            "Concrete pilot with one dining hall. I believed the pickup logistics.",
        ),
        _eval(
            "draft",
            [3, None, 4, None],
            private="Ask about the health-permit question before I score presentation.",
        ),
    ],
    "MediBuddy": [
        _eval(
            "ready",
            [4, 3, 5, 4],
            "strong",
            "Medication adherence for older adults sits cleanly in healthcare, and the large-type view was designed with them.",
            "I want to submit after the Q&A in case the refill API comes up.",
        ),
        _eval("viewed"),
    ],
    "PulseCheck": [
        _eval(
            "draft",
            [3, 4, None, None],
            alignment="partial",
            explanation="",
            private="Routing copy felt clinical. Check whether counseling services approved the wording.",
        ),
        _eval("not_started"),
    ],
    "OfficeHours+": [_eval("viewed"), _eval("viewed")],
    "LabLens": [
        _eval(
            "submitted",
            [4, 4, 4, 5],
            "strong",
            "It helps students read research, which is the education job, and the brief was readable on a phone.",
            "The glossary saved the demo. Citation map was a bonus, not the whole pitch.",
        ),
        _eval(
            "ready",
            [5, 4, 4, 4],
            "strong",
            "Education track is right: the user is a student outside the paper's field.",
            "Very clear brief. Submitting after I compare notes with the other education projects.",
        ),
    ],
    "NeighborNet": [
        _eval("draft", [4, None, None, None], private="Might be more mutual-aid than sustainability. Decide after the second project in this room."),
        _eval("draft", [None, None, None, None], feedback="Promising sharing loop.", private="Started feedback, still owe scores."),
    ],
    "QuizForge": [
        _eval(
            "submitted",
            [4, 5, 4, 4],
            "partial",
            "Students learn from it, so education is fair, but the hard part is the question generator.",
            "Explanations were specific, not generic hint text. That is the feature.",
            cross_track="Artificial Intelligence",
            cross_explanation="The retrieval and explanation quality is the engineering story. Flag for an AI mention; do not drop the education score.",
        ),
        _eval(
            "submitted",
            [5, 5, 4, 3],
            "partial",
            "It teaches, but I would also put it in front of the AI judges because the model behavior is the product.",
            "Demo worked from a real notes file. Presentation ran long.",
            cross_track="Artificial Intelligence",
            cross_explanation="Independently flagging this for AI. The education use is real, and so is the model work.",
        ),
    ],
    "Dues": [_eval("not_started"), _eval("not_started")],
    "MicroGrant": [
        _eval("viewed"),
        _eval("draft", [4, 3, 5, None], alignment="strong", explanation="Emergency grants are a fintech access problem.", private="Need the presentation score after they show the approval queue."),
    ],
    "FairFare": [
        _eval(
            "submitted",
            [4, 4, 5, 4],
            "strong",
            "Cross-border fees shown before sending is a fintech product, and they did not hide the cost.",
            "Honest about partners they do not have yet. The fee breakdown was the demo.",
        ),
        _eval(
            "submitted",
            [3, 4, 5, 4],
            "strong",
            "Fits fintech. The usefulness is the fee transparency, not a new currency.",
            "Solid story. I would have liked one real transfer receipt, even redacted.",
        ),
    ],
    "PR Pilot": [
        _eval(
            "ready",
            [5, 4, 4, 4],
            "strong",
            "A diff-native review tool is developer tools, and they stayed on the pull request rather than bolting on a chatbot.",
            "The suggested test was specific to the diff. Ready to submit.",
        ),
        _eval(
            "ready",
            [4, 4, 3, 5],
            "strong",
            "Built for people shipping code. Track fit is strong.",
            "Best live demo of the developer-tools block so far.",
        ),
    ],
    "Dockyard": [
        _eval(
            "submitted",
            [5, 3, 4, 4],
            "strong",
            "Environment snapshots are a developer-tools problem. They showed a clean machine booting the repo.",
            "Less flashy, very useful. I trusted the before-and-after.",
        ),
        _eval("not_started"),
    ],
    "FormFit": [
        _eval(
            "submitted",
            [4, 4, 5, 4],
            "strong",
            "Physical-therapy form feedback is healthcare, and a clinician was part of the design.",
            "Careful about not diagnosing. The rep counter worked.",
            cross_track="Artificial Intelligence",
            cross_explanation="The pose model is strong enough that AI should see it too. Healthcare ranking should stay as judged.",
        ),
        _eval(
            "submitted",
            [3, 4, 5, 5],
            "strong",
            "Clinic partner, exercise coaching, and a narrow claim. That is healthcare.",
            "Clear about what the camera does not do. Good restraint.",
        ),
    ],
    "TraceLog": [
        _eval("not_started"),
        _eval("viewed"),
    ],
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
    for model in (AuditEvent, Score, Evaluation, Timer, Assignment, ScheduleSlot, Project, Criterion, Track, User):
        db.execute(delete(model))
    db.commit()


def _apply_plan(db: Session, project: Project, tracks: dict[str, Track]) -> None:
    plan = PLANS[project.name]
    assignments = sorted(project.assignments, key=lambda item: item.judge.name.lower())
    if len(assignments) != len(plan):
        raise RuntimeError(f"{project.name} expected {len(plan)} judges, found {len(assignments)}")
    for assignment, spec in zip(assignments, plan, strict=True):
        evaluation = assignment.evaluation
        if evaluation is None or spec["kind"] == "not_started":
            continue
        mark_viewed(evaluation)
        if spec["kind"] == "viewed":
            continue
        recommended_id = None
        flag = False
        if spec["cross_track"]:
            flag = True
            recommended_id = tracks[spec["cross_track"]].id
        payload = EvaluationIn(
            scores=_scores(spec["scores"]),
            private_notes=spec["private"],
            formal_feedback=spec["feedback"],
            alignment=spec["alignment"],
            alignment_explanation=spec["explanation"],
            cross_track_flag=flag,
            recommended_track_id=recommended_id,
            cross_track_explanation=spec["cross_explanation"],
        )
        apply_autosave(db, evaluation, payload, assignment.judge, project.track_id)
        if spec["kind"] == "submitted":
            submit_evaluation(db, evaluation, assignment.judge, project.track_id)


def _seed_timers(db: Session, now: datetime) -> None:
    assignments = db.scalars(select(Assignment)).all()
    for assignment in assignments:
        project = db.get(Project, assignment.project_id)
        if project is None or project.schedule is None or assignment.timer is None or assignment.evaluation is None:
            continue
        slot = project.schedule
        timer = assignment.timer
        status = assignment.evaluation.status
        window_end = slot.starts_at + timedelta(minutes=20)
        if status == "submitted" and slot.starts_at <= now:
            timer.status = "time_up"
            timer.actual_started_at = slot.starts_at + timedelta(seconds=25)
            timer.actual_ended_at = timer.actual_started_at + timedelta(minutes=slot.duration_minutes)
            timer.accumulated_seconds = (slot.duration_minutes + slot.extension_minutes) * 60
            timer.running_since = None
        elif status == "viewed" and slot.starts_at <= now <= window_end:
            timer.status = "running"
            timer.actual_started_at = now - timedelta(minutes=2)
            timer.running_since = timer.actual_started_at
            timer.accumulated_seconds = 0
            timer.actual_ended_at = None
        elif status in {"draft_saved", "ready_to_submit"} and slot.starts_at <= now:
            timer.status = "paused"
            timer.actual_started_at = slot.starts_at + timedelta(seconds=40)
            timer.accumulated_seconds = 95
            timer.running_since = None
            timer.actual_ended_at = None
        timer.updated_at = utcnow()


def seed_database(db: Session) -> None:
    missing = set(PLANS) ^ {item["name"] for item in PROJECTS}
    if missing:
        raise RuntimeError(f"Seed plans do not match projects: {sorted(missing)}")
    seed_criteria(db)
    tracks = {}
    for name, description in TRACKS:
        track = Track(name=name, description=description)
        db.add(track)
        tracks[name] = track
    db.flush()

    organizer = User(
        email=ORGANIZER_EMAIL,
        name="Avery Shah",
        password_hash=hash_password(ORGANIZER_PASSWORD),
        role="organizer",
        active=True,
    )
    db.add(organizer)
    judges: list[User] = []
    for name, email in JUDGES:
        judge = User(
            email=email,
            name=name,
            password_hash=hash_password(JUDGE_PASSWORD),
            role="judge",
            active=True,
        )
        db.add(judge)
        judges.append(judge)
    db.flush()

    now = datetime.now(ET).replace(second=0, microsecond=0)
    start0 = now - timedelta(minutes=50)
    projects: list[Project] = []
    for index, spec in enumerate(PROJECTS):
        project = Project(
            name=spec["name"],
            description=spec["description"],
            track_id=tracks[spec["track"]].id,
            team_members=spec["team"],
            github_url=spec["github_url"],
            demo_url=spec["demo_url"],
            submission_url=spec["submission_url"],
            updated_at=utcnow(),
        )
        project.track = tracks[spec["track"]]
        db.add(project)
        projects.append(project)
    db.flush()

    for round_index, pairs in enumerate(PAIRINGS):
        starts = start0 + timedelta(minutes=20 * round_index)
        for room_index, pair in enumerate(pairs):
            project = projects[round_index * 3 + room_index]
            for judge_index in pair:
                create_assignment(db, judges[judge_index], project)
            extension = 2 if project.name == "Atlas Note" else 0
            slot = ScheduleSlot(
                project_id=project.id,
                starts_at=starts,
                duration_minutes=5,
                extension_minutes=extension,
                location=ROOMS[room_index],
                updated_at=utcnow(),
            )
            db.add(slot)
            project.schedule = slot
    db.flush()

    for project in projects:
        _apply_plan(db, project, tracks)
    _seed_timers(db, now)
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
