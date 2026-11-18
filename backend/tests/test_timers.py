"""Two-phase presentation and Q&A timers."""

from datetime import timedelta

from sqlalchemy import inspect, select, text

from app.database import engine
from app.migrate import upgrade_schema
from app.models import Assignment, Project, Timer
from app.services.evaluations import utcnow
from app.services.timers import warning_threshold
from tests.helpers import evaluation_body, login, make_user


def _assign(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    track = client.post("/api/organizer/tracks", json={"name": "Education", "description": ""}, headers=organizer)
    assert track.status_code == 201, track.text
    project = client.post(
        "/api/organizer/projects",
        json={
            "name": "QuizForge",
            "description": "Practice quizzes",
            "track_ids": [track.json()["id"]],
            "team_members": ["Yuki Tanaka"],
            "github_url": "",
            "demo_url": "",
            "submission_url": "",
        },
        headers=organizer,
    )
    assert project.status_code == 201, project.text
    judge = client.post(
        "/api/organizer/judges",
        json={"name": "Maya Chen", "email": "maya@example.com", "password": "password123"},
        headers=organizer,
    )
    assert judge.status_code == 201, judge.text
    assignment = client.post(
        "/api/organizer/assignments",
        json={"judge_id": judge.json()["id"], "project_id": project.json()["id"], "track_id": track.json()["id"]},
        headers=organizer,
    )
    assert assignment.status_code == 201, assignment.text
    return organizer, login(client, "maya@example.com"), project.json()["id"]


def test_warning_threshold_follows_phase_length():
    assert warning_threshold(180) == 60
    assert warning_threshold(121) == 60
    assert warning_threshold(120) == 30
    assert warning_threshold(90) == 30
    assert warning_threshold(45) == 0


def test_qa_does_not_autostart_and_phases_are_independent(client, db):
    organizer, maya, project_id = _assign(client, db)
    scheduled = client.put(
        f"/api/organizer/projects/{project_id}/timing",
        json={"presentation_minutes": 3, "qa_minutes": 2},
        headers=organizer,
    )
    assert scheduled.status_code == 200, scheduled.text

    started = client.post(f"/api/judge/projects/{project_id}/timer/presentation/start", headers=maya)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["current_phase"] == "presentation"
    assert body["phases"]["presentation"]["status"] == "running"
    presentation_started_at = body["phases"]["presentation"]["actual_started_at"]
    assert presentation_started_at
    assert body["phases"]["qa"]["status"] == "idle"
    assert body["phases"]["qa"]["actual_started_at"] is None

    # Driving the presentation clock to zero does not open Q&A.
    assignment = db.scalar(select(Assignment).where(Assignment.project_id == project_id))
    row = db.scalar(select(Timer).where(Timer.project_id == project_id))
    db.refresh(row)
    row.presentation_accumulated_seconds = 170
    row.presentation_running_since = utcnow() - timedelta(seconds=30)
    row.presentation_status = "running"
    db.commit()
    expired = client.get(f"/api/judge/projects/{project_id}/timer", headers=maya)
    assert expired.status_code == 200, expired.text
    assert expired.json()["phases"]["presentation"]["status"] == "time_up"
    assert expired.json()["phases"]["presentation"]["actual_ended_at"]
    assert expired.json()["phases"]["presentation"]["actual_started_at"] == presentation_started_at
    assert expired.json()["phases"]["qa"]["status"] == "idle"
    assert expired.json()["current_phase"] == "qa"

    qa = client.post(f"/api/judge/projects/{project_id}/timer/qa/start", headers=maya)
    assert qa.status_code == 200, qa.text
    assert qa.json()["current_phase"] == "qa"
    assert qa.json()["phases"]["qa"]["status"] == "running"
    assert qa.json()["phases"]["qa"]["actual_started_at"]
    assert qa.json()["phases"]["qa"]["allowed_seconds"] == 120
    assert qa.json()["phases"]["qa"]["warning_seconds"] == 30
    assert qa.json()["phases"]["presentation"]["status"] == "time_up"
    assert qa.json()["phases"]["presentation"]["actual_started_at"] == presentation_started_at

    # A 30 second warning on the 2 minute Q&A clock. Presentation stays expired.
    row = db.scalar(select(Timer).where(Timer.project_id == project_id))
    db.refresh(row)
    client.post(f"/api/judge/projects/{project_id}/timer/qa/pause", headers=maya)
    row = db.scalar(select(Timer).where(Timer.project_id == project_id))
    db.refresh(row)
    row.qa_accumulated_seconds = 95
    row.qa_status = "paused"
    db.commit()
    warned = client.post(f"/api/judge/projects/{project_id}/timer/qa/start", headers=maya)
    assert warned.status_code == 200, warned.text
    assert warned.json()["phases"]["qa"]["warning"] is True
    assert warned.json()["phases"]["qa"]["remaining_seconds"] == 25
    assert warned.json()["phases"]["presentation"]["time_up"] is True

    extended = client.post(
        f"/api/organizer/projects/{project_id}/timer/extend",
        json={"minutes": 2, "phase": "qa"},
        headers=organizer,
    )
    assert extended.status_code == 200, extended.text
    assert extended.json()["qa_extension_minutes"] == 2
    assert extended.json()["presentation_extension_minutes"] == 0
    after = client.get(f"/api/judge/projects/{project_id}/timer", headers=maya)
    assert after.json()["phases"]["qa"]["allowed_seconds"] == 240
    assert after.json()["phases"]["presentation"]["allowed_seconds"] == 180

    qa_started = after.json()["phases"]["qa"]["actual_started_at"]
    reset = client.post(f"/api/judge/projects/{project_id}/timer/qa/reset", headers=maya)
    assert reset.json()["phases"]["qa"]["status"] == "idle"
    assert reset.json()["phases"]["qa"]["actual_started_at"] is None
    assert reset.json()["phases"]["qa"]["elapsed_seconds"] == 0
    assert reset.json()["phases"]["presentation"]["actual_started_at"] == presentation_started_at
    assert reset.json()["phases"]["presentation"]["status"] == "time_up"
    assert qa_started

    # Scoring is independent of either clock.
    saved = client.put(
        f"/api/judge/projects/{project_id}/tracks/{client.get('/api/organizer/projects', headers=organizer).json()[0]['tracks'][0]['id']}/evaluation",
        headers=maya,
        json=evaluation_body(scores={"technical": 4, "innovation": None, "impact": None, "presentation": None}),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "draft_saved"
    still = client.get(f"/api/judge/projects/{project_id}/timer", headers=maya)
    assert still.json()["phases"]["presentation"]["status"] == "time_up"


def test_starting_qa_pauses_presentation_and_records_both_ends(client, db):
    _organizer, maya, project_id = _assign(client, db)
    client.post(f"/api/judge/projects/{project_id}/timer/presentation/start", headers=maya)
    client.post(f"/api/judge/projects/{project_id}/timer/presentation/pause", headers=maya)
    switched = client.post(f"/api/judge/projects/{project_id}/timer/qa/start", headers=maya)
    assert switched.status_code == 200, switched.text
    presentation = switched.json()["phases"]["presentation"]
    qa = switched.json()["phases"]["qa"]
    assert presentation["status"] == "paused"
    assert presentation["actual_started_at"]
    assert presentation["actual_ended_at"]
    assert qa["status"] == "running"
    assert qa["actual_started_at"]
    assert qa["actual_ended_at"] is None
    assert qa["actual_started_at"] != presentation["actual_started_at"]

    # Going back to presentation pauses Q&A and keeps the Q&A start.
    resumed = client.post(f"/api/judge/projects/{project_id}/timer/presentation/start", headers=maya)
    assert resumed.json()["phases"]["presentation"]["status"] == "running"
    assert resumed.json()["phases"]["presentation"]["actual_ended_at"] is None
    assert resumed.json()["phases"]["qa"]["status"] == "paused"
    assert resumed.json()["phases"]["qa"]["actual_started_at"] == qa["actual_started_at"]
    assert resumed.json()["phases"]["qa"]["actual_ended_at"]


def test_timer_state_survives_a_fresh_read(client, db):
    _organizer, maya, project_id = _assign(client, db)
    started = client.post(f"/api/judge/projects/{project_id}/timer/presentation/start", headers=maya)
    started_at = started.json()["phases"]["presentation"]["actual_started_at"]
    again = client.get(f"/api/judge/projects/{project_id}/timer", headers=maya)
    assert again.json()["phases"]["presentation"]["status"] == "running"
    assert again.json()["phases"]["presentation"]["actual_started_at"] == started_at
    assert again.json()["phases"]["qa"]["status"] == "idle"


def test_phase_defaults_apply_until_a_project_overrides_them(client, db):
    organizer, _maya, project_id = _assign(client, db)
    updated = client.put(
        "/api/organizer/timer-defaults",
        json={"presentation_minutes": 4, "qa_minutes": 3},
        headers=organizer,
    )
    assert updated.status_code == 200, updated.text
    timer = client.get(f"/api/judge/projects/{project_id}/timer", headers=_maya)
    assert timer.json()["phases"]["presentation"]["allowed_seconds"] == 240
    assert timer.json()["phases"]["qa"]["allowed_seconds"] == 180

    explicit = client.put(
        f"/api/organizer/projects/{project_id}/timing",
        json={"presentation_minutes": 6, "qa_minutes": 4},
        headers=organizer,
    )
    assert explicit.json()["presentation_minutes"] == 6
    assert explicit.json()["qa_minutes"] == 4
    client.put("/api/organizer/timer-defaults", json={"presentation_minutes": 8, "qa_minutes": 8}, headers=organizer)
    kept = client.get(f"/api/judge/projects/{project_id}/timer", headers=_maya)
    assert kept.json()["phases"]["presentation"]["allowed_seconds"] == 360
    assert kept.json()["phases"]["qa"]["allowed_seconds"] == 240


def test_track_board_shows_the_running_phase(client, db):
    organizer, maya, project_id = _assign(client, db)
    client.post(f"/api/judge/projects/{project_id}/timer/qa/start", headers=maya)
    board = client.get("/api/organizer/tracks/live", headers=organizer)
    assert board.status_code == 200, board.text
    track = board.json()["tracks"][0]
    assert track["current_project_id"] == project_id
    assert track["current_state"] == "qa"
    assert "Q&A" in track["current_detail"]
    progress = client.get("/api/organizer/progress", headers=organizer)
    monitored = progress.json()["projects"][0]["judges"][0]
    assert monitored["qa_status"] == "running"
    assert monitored["current_phase"] == "qa"


def test_legacy_slot_minutes_move_onto_the_project(client, db):
    organizer, _maya, project_id = _assign(client, db)
    db.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE schedule_slots (
                    id serial primary key,
                    project_id integer not null,
                    starts_at timestamptz not null default now(),
                    duration_minutes integer not null default 5,
                    extension_minutes integer not null default 0,
                    location varchar(120) not null default ''
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO schedule_slots (project_id, duration_minutes, extension_minutes)
                VALUES (:project_id, 5, 2)
                """
            ),
            {"project_id": project_id},
        )
        conn.execute(
            text(
                """
                UPDATE projects
                SET presentation_minutes = NULL, qa_minutes = NULL,
                    presentation_extension_minutes = 0, qa_extension_minutes = 0
                WHERE id = :project_id
                """
            ),
            {"project_id": project_id},
        )
    upgrade_schema(engine)
    upgrade_schema(engine)
    db.expire_all()
    project = db.get(Project, project_id)
    assert project.presentation_minutes == 3
    assert project.qa_minutes == 2
    assert project.presentation_extension_minutes == 2
    assert project.qa_extension_minutes == 0
    assert "schedule_slots" not in inspect(engine).get_table_names()
