import csv
import io
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Evaluation, Timer, User
from app.schemas import EvaluationIn
from app.services.domain import DomainError
from app.services.evaluations import apply_autosave
from app.services.timers import sync_timer
from app.database import SessionLocal
from tests.helpers import evaluation_body, full_scores, login, make_user
from tests.test_evaluations import _assign


def test_reopened_submission_cannot_be_deleted(client, db):
    organizer, maya, project = _assign(client, db)
    project_id = project["id"]
    saved = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=maya,
        json=evaluation_body(),
    )
    assert saved.status_code == 200, saved.text
    submitted = client.post(f"/api/judge/projects/{project_id}/evaluation/submit", headers=maya)
    assert submitted.status_code == 200, submitted.text
    reopened = client.post(f"/api/organizer/evaluations/{submitted.json()['id']}/reopen", headers=organizer)
    assert reopened.status_code == 200, reopened.text

    projects = client.get("/api/organizer/projects", headers=organizer).json()
    assignment_id = next(item for item in projects if item["id"] == project_id)["assignments"][0]["assignment_id"]
    removed = client.delete(f"/api/organizer/assignments/{assignment_id}?force=true", headers=organizer)
    assert removed.status_code == 409
    deleted = client.delete(f"/api/organizer/projects/{project_id}?force=true", headers=organizer)
    assert deleted.status_code == 409
    still_there = client.get("/api/organizer/results", headers=organizer).json()
    assert any(item["project_id"] == project_id and item["evaluations"] for item in still_there["projects"])


def test_csv_export_neutralizes_formulas(client, db):
    organizer, maya, project = _assign(client, db)
    saved = client.put(
        f"/api/judge/projects/{project['id']}/evaluation",
        headers=maya,
        json=evaluation_body(formal_feedback="=1+1", alignment_explanation="+cmd|'/c calc'!A0"),
    )
    assert saved.status_code == 200, saved.text
    submitted = client.post(f"/api/judge/projects/{project['id']}/evaluation/submit", headers=maya)
    assert submitted.status_code == 200, submitted.text
    exported = client.get("/api/organizer/export/feedback.csv", headers=organizer)
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.text)))
    feedback = rows[1][4]
    explanation = rows[1][6]
    assert feedback.startswith("'=")
    assert explanation.startswith("'+")


def test_scale_change_rejects_existing_scores(client, db):
    organizer, maya, project = _assign(client, db)
    saved = client.put(
        f"/api/judge/projects/{project['id']}/evaluation",
        headers=maya,
        json=evaluation_body(scores=full_scores(5)),
    )
    assert saved.status_code == 200, saved.text
    criteria = client.get("/api/organizer/criteria", headers=organizer).json()
    technical = next(item for item in criteria if item["key"] == "technical")
    rejected = client.patch(
        f"/api/organizer/criteria/{technical['id']}",
        headers=organizer,
        json={
            "name": technical["name"],
            "description": technical["description"],
            "min_score": 1,
            "max_score": 4,
        },
    )
    assert rejected.status_code == 409
    again = client.get("/api/organizer/criteria", headers=organizer).json()
    assert next(item for item in again if item["key"] == "technical")["max_score"] == 5


def test_import_reports_extra_commas_without_aborting(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    client.post("/api/organizer/tracks", json={"name": "Education", "description": ""}, headers=organizer)
    payload = "name,track,description\nKept,Education,A clean row\nBroken,Education,hello,extra\n"
    response = client.post(
        "/api/organizer/projects/import",
        headers=organizer,
        files={"file": ("projects.csv", payload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 1
    assert body["errors"][0]["row"] == 3
    names = [item["name"] for item in client.get("/api/organizer/projects", headers=organizer).json()]
    assert names == ["Kept"]


def test_stale_autosave_cannot_overwrite_a_submission(client, db):
    organizer, maya, project = _assign(client, db)
    ready = client.put(
        f"/api/judge/projects/{project['id']}/evaluation",
        headers=maya,
        json=evaluation_body(scores=full_scores(4)),
    )
    assert ready.status_code == 200, ready.text
    other = SessionLocal()
    try:
        evaluation = other.scalar(select(Evaluation).where(Evaluation.project_id == project["id"]))
        assert evaluation is not None
        judge = other.get(User, evaluation.judge_id)
        submitted = client.post(f"/api/judge/projects/{project['id']}/evaluation/submit", headers=maya)
        assert submitted.status_code == 200, submitted.text
        try:
            apply_autosave(
                other,
                evaluation,
                EvaluationIn(scores=full_scores(1), alignment="weak", alignment_explanation="Too late"),
                judge,
                project["track_id"],
            )
            raise AssertionError("stale autosave should have been rejected")
        except DomainError as exc:
            assert exc.status_code == 403
        other.rollback()
    finally:
        other.close()
    current = client.get(f"/api/judge/projects/{project['id']}/evaluation", headers=maya).json()
    assert current["status"] == "submitted"
    assert all(score["value"] == 4 for score in current["scores"])


def test_timer_end_uses_the_moment_time_expired(db):
    _ = db
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=20)
    timer = Timer(
        assignment_id=1,
        accumulated_seconds=0,
        status="running",
        running_since=started,
        actual_started_at=started,
        updated_at=started,
    )
    sync_timer(timer, None, now)
    assert timer.status == "time_up"
    assert timer.actual_ended_at == started + timedelta(seconds=300)


def test_revision_conflict_is_rejected(client, db):
    _organizer, maya, project = _assign(client, db)
    first = client.put(
        f"/api/judge/projects/{project['id']}/evaluation",
        headers=maya,
        json=evaluation_body(scores={"technical": 2, "innovation": None, "impact": None, "presentation": None}, alignment=None, alignment_explanation=""),
    )
    assert first.status_code == 200, first.text
    stale = client.put(
        f"/api/judge/projects/{project['id']}/evaluation",
        headers=maya,
        json=evaluation_body(
            scores=full_scores(5),
            revision=first.json()["revision"] - 1,
        ),
    )
    assert stale.status_code == 409
    current = client.get(f"/api/judge/projects/{project['id']}/evaluation", headers=maya).json()
    assert next(score["value"] for score in current["scores"] if score["key"] == "technical") == 2
