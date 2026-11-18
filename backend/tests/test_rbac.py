from tests.helpers import login, make_user


SENTINEL = "PRIVATE-SENTINEL-do-not-leak"


def _track_and_project(client, headers, name="Atlas", track_name="Artificial Intelligence"):
    track = client.post(
        "/api/organizer/tracks",
        json={"name": track_name, "description": "Applied work"},
        headers=headers,
    )
    assert track.status_code == 201, track.text
    project = client.post(
        "/api/organizer/projects",
        json={
            "name": name,
            "description": "A demo project",
            "track_ids": [track.json()["id"]],
            "team_members": ["Ada Lovelace"],
            "github_url": "https://github.com/example/atlas",
            "demo_url": "https://example.com/demo",
            "submission_url": "",
        },
        headers=headers,
    )
    assert project.status_code == 201, project.text
    return track.json(), project.json()


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/judge/dashboard").status_code == 401
    assert client.get("/api/organizer/projects").status_code == 401
    assert client.get("/api/organizer/results").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_roles_are_enforced_on_both_surfaces(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    make_user(db, "judge", "judge@example.com", "Judge")
    organizer = login(client, "org@example.com")
    judge = login(client, "judge@example.com")

    assert client.get("/api/organizer/projects", headers=judge).status_code == 403
    assert client.get("/api/organizer/export/results.csv", headers=judge).status_code == 403
    assert client.post("/api/organizer/evaluations/1/reopen", headers=judge).status_code == 403
    assert client.get("/api/judge/dashboard", headers=organizer).status_code == 403
    assert client.get("/api/judge/projects/1/tracks/1", headers=organizer).status_code == 403


def test_judge_only_sees_assigned_projects(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    _track, project_a = _track_and_project(client, organizer, name="Assigned")
    project_b = client.post(
        "/api/organizer/projects",
        json={
            "name": "Hidden",
            "description": "Not for this judge",
            "track_ids": [project_a["tracks"][0]["id"]],
            "team_members": ["Grace Hopper"],
            "github_url": "",
            "demo_url": "",
            "submission_url": "",
        },
        headers=organizer,
    )
    assert project_b.status_code == 201, project_b.text

    created = client.post(
        "/api/organizer/judges",
        json={"name": "Maya Chen", "email": "maya@example.com", "password": "password123"},
        headers=organizer,
    )
    assert created.status_code == 201, created.text
    other = client.post(
        "/api/organizer/judges",
        json={"name": "Jordan Lee", "email": "jordan@example.com", "password": "password123"},
        headers=organizer,
    )
    assert other.status_code == 201, other.text
    track_id = project_a["tracks"][0]["id"]
    assignment = client.post(
        "/api/organizer/assignments",
        json={"judge_id": created.json()["id"], "project_id": project_a["id"], "track_id": track_id},
        headers=organizer,
    )
    assert assignment.status_code == 201, assignment.text
    other_assignment = client.post(
        "/api/organizer/assignments",
        json={"judge_id": other.json()["id"], "project_id": project_b.json()["id"], "track_id": track_id},
        headers=organizer,
    )
    assert other_assignment.status_code == 201, other_assignment.text

    maya = login(client, "maya@example.com")
    dashboard = client.get("/api/judge/dashboard", headers=maya)
    assert dashboard.status_code == 200
    names = [item["name"] for item in dashboard.json()["projects"]]
    assert names == ["Assigned"]

    assert client.get(f"/api/judge/projects/{project_b.json()['id']}/tracks/{track_id}", headers=maya).status_code == 404
    assert client.get(f"/api/judge/projects/{project_b.json()['id']}/tracks/{track_id}/evaluation", headers=maya).status_code == 404

    jordan = login(client, "jordan@example.com")
    saved = client.put(
        f"/api/judge/projects/{project_b.json()['id']}/tracks/{track_id}/evaluation",
        json={
            "scores": {"technical": 2, "innovation": None, "impact": None, "presentation": None},
            "private_notes": SENTINEL,
            "formal_feedback": "Public comment that organizers may see later",
            "alignment": None,
            "alignment_explanation": "",
            "cross_track_flag": False,
            "recommended_track_id": None,
            "cross_track_explanation": "",
        },
        headers=jordan,
    )
    assert saved.status_code == 200, saved.text
    assert SENTINEL in saved.text

    leaked = client.get("/api/organizer/results", headers=organizer)
    assert leaked.status_code == 200
    assert SENTINEL not in leaked.text
    for path in ("/api/organizer/export/results.csv", "/api/organizer/export/feedback.csv"):
        exported = client.get(path, headers=organizer)
        assert exported.status_code == 200
        assert SENTINEL not in exported.text


def test_inactive_judge_cannot_sign_in(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    created = client.post(
        "/api/organizer/judges",
        json={"name": "Riley Nguyen", "email": "riley@example.com", "password": "password123"},
        headers=organizer,
    )
    judge_id = created.json()["id"]
    assert client.patch(f"/api/organizer/judges/{judge_id}", json={"active": False}, headers=organizer).status_code == 200
    rejected = client.post("/api/auth/login", json={"email": "riley@example.com", "password": "password123"})
    assert rejected.status_code == 401
