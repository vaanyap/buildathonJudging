import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Assignment, Project, Timer, Track, User
from app.services.assignments import create_assignment
from tests.helpers import evaluation_body, full_scores, login, make_user


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
            "track_id": track.json()["id"],
            "team_members": ["Yuki Tanaka"],
            "github_url": "https://github.com/example/quizforge",
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
        json={"judge_id": judge.json()["id"], "project_id": project.json()["id"]},
        headers=organizer,
    )
    assert assignment.status_code == 201, assignment.text
    duplicate = client.post(
        "/api/organizer/assignments",
        json={"judge_id": judge.json()["id"], "project_id": project.json()["id"]},
        headers=organizer,
    )
    assert duplicate.status_code == 409
    return organizer, login(client, "maya@example.com"), project.json()


def test_autosave_status_transitions_and_reopen_audit(client, db):
    organizer, maya, project = _assign(client, db)
    project_id = project["id"]

    dashboard = client.get("/api/judge/dashboard", headers=maya)
    assert dashboard.json()["projects"][0]["status"] == "not_started"

    viewed = client.post(f"/api/judge/projects/{project_id}/view", headers=maya)
    assert viewed.status_code == 200
    assert viewed.json()["status"] == "viewed"

    draft = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(
            scores={"technical": 3, "innovation": None, "impact": None, "presentation": None},
            private_notes="Remember to ask about the rubric",
            formal_feedback="",
            alignment=None,
            alignment_explanation="",
        ),
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] == "draft_saved"
    assert draft.json()["scores"][0]["value"] == 3 or any(
        score["key"] == "technical" and score["value"] == 3 for score in draft.json()["scores"]
    )

    again = client.get(f"/api/judge/projects/{project_id}/evaluation", headers=maya)
    assert again.json()["private_notes"] == "Remember to ask about the rubric"
    assert again.json()["status"] == "draft_saved"

    progress = client.get("/api/organizer/progress", headers=organizer)
    counts = progress.json()["counts"]
    assert counts["submitted"] == 0
    assert counts["draft_saved"] == 1
    assert counts["completion_rate"] == 0

    ready = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(private_notes="Remember to ask about the rubric"),
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready_to_submit"

    cleared = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(
            scores={"technical": 4, "innovation": None, "impact": 4, "presentation": 4},
            private_notes="Remember to ask about the rubric",
        ),
    )
    assert cleared.json()["status"] == "draft_saved"

    ready_again = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(scores=full_scores(5), private_notes="Remember to ask about the rubric"),
    )
    assert ready_again.json()["status"] == "ready_to_submit"

    submitted = client.post(f"/api/judge/projects/{project_id}/evaluation/submit", headers=maya)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"
    assert submitted.json()["submission_count"] == 1

    locked = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(scores=full_scores(2)),
    )
    assert locked.status_code == 403
    assert client.post(f"/api/judge/projects/{project_id}/evaluation/submit", headers=maya).status_code == 400

    after = client.get("/api/organizer/progress", headers=organizer).json()["counts"]
    assert after["submitted"] == 1
    assert after["completion_rate"] == 1

    evaluation_id = submitted.json()["id"]
    reopened = client.post(f"/api/organizer/evaluations/{evaluation_id}/reopen", headers=organizer)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "ready_to_submit"
    assert "private_notes" not in reopened.json()

    edited = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(scores=full_scores(3), private_notes="Remember to ask about the rubric"),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "ready_to_submit"
    assert edited.json()["reopened"] is True

    results = client.get("/api/organizer/results", headers=organizer).json()
    project_row = next(item for item in results["projects"] if item["project_id"] == project_id)
    assert project_row["submitted_count"] == 0
    assert project_row["overall"] is None
    audits = project_row["evaluations"][0]["audits"]
    actions = [audit["action"] for audit in audits]
    assert "submit" in actions
    assert "reopen" in actions
    change = next(audit for audit in audits if audit["action"] == "score_change")
    assert change["changes"]["scores.technical"]["old"] == 5
    assert change["changes"]["scores.technical"]["new"] == 3
    assert "Remember to ask about the rubric" not in str(audits)


def test_timer_is_server_owned_and_extensions_apply(client, db):
    _organizer, maya, project = _assign(client, db)
    organizer = login(client, "org@example.com")
    project_id = project["id"]

    started = client.post(f"/api/judge/projects/{project_id}/timer/start", headers=maya)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    assert started.json()["actual_started_at"]
    assert started.json()["allowed_seconds"] == 300

    paused = client.post(f"/api/judge/projects/{project_id}/timer/pause", headers=maya)
    assert paused.json()["status"] == "paused"
    assert paused.json()["actual_started_at"]

    reset = client.post(f"/api/judge/projects/{project_id}/timer/reset", headers=maya)
    assert reset.json()["status"] == "idle"
    assert reset.json()["elapsed_seconds"] == 0
    assert reset.json()["actual_started_at"] is None

    scheduled = client.put(
        f"/api/organizer/schedule/{project_id}",
        json={"starts_at": "2026-11-21T10:00", "duration_minutes": 5, "location": "Tisch LC19"},
        headers=organizer,
    )
    assert scheduled.status_code == 200, scheduled.text
    extended = client.post(
        f"/api/organizer/schedule/{project_id}/extend",
        json={"minutes": 2},
        headers=organizer,
    )
    assert extended.status_code == 200, extended.text
    assert extended.json()["extension_minutes"] == 2

    timer = client.get(f"/api/judge/projects/{project_id}/timer", headers=maya)
    assert timer.json()["allowed_seconds"] == 420
    assert timer.json()["extension_minutes"] == 2
    assert timer.json()["warning"] is False

    assignment = db.scalar(select(Assignment).where(Assignment.project_id == project_id))
    db.refresh(assignment)
    row = db.scalar(select(Timer).where(Timer.assignment_id == assignment.id))
    row.accumulated_seconds = 370
    row.status = "paused"
    db.commit()

    warned = client.post(f"/api/judge/projects/{project_id}/timer/start", headers=maya)
    assert warned.status_code == 200, warned.text
    assert warned.json()["status"] == "running"
    assert warned.json()["warning"] is True
    assert warned.json()["time_up"] is False

    client.post(f"/api/judge/projects/{project_id}/timer/pause", headers=maya)
    row = db.scalar(select(Timer).where(Timer.assignment_id == assignment.id))
    db.refresh(row)
    row.accumulated_seconds = 420
    row.status = "paused"
    db.commit()
    blocked = client.post(f"/api/judge/projects/{project_id}/timer/start", headers=maya)
    assert blocked.status_code == 400
    current = client.get(f"/api/judge/projects/{project_id}/timer", headers=maya)
    assert current.json()["status"] == "time_up"
    assert current.json()["time_up"] is True
    assert current.json()["remaining_seconds"] == 0


def test_duplicate_evaluation_is_rejected_by_the_database(db):
    judge = User(email="maya@example.com", name="Maya", password_hash="x", role="judge", active=True)
    track = Track(name="Fintech", description="")
    first = Project(
        name="Dues",
        description="",
        track=track,
        team_members=["Grace"],
        github_url="",
        demo_url="",
        submission_url="",
    )
    second = Project(
        name="FairFare",
        description="",
        track=track,
        team_members=["Luis"],
        github_url="",
        demo_url="",
        submission_url="",
    )
    db.add_all([judge, track, first, second])
    db.flush()
    create_assignment(db, judge, first)
    second_assignment = create_assignment(db, judge, second)
    db.flush()
    second_assignment.evaluation.project_id = first.id
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
