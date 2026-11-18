from tests.helpers import evaluation_body, full_scores, login, make_user


def _track(client, headers, name: str) -> int:
    response = client.post("/api/organizer/tracks", json={"name": name, "description": ""}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _project(client, headers, name: str, track_id: int) -> int:
    response = client.post(
        "/api/organizer/projects",
        json={
            "name": name,
            "description": name,
            "track_id": track_id,
            "team_members": ["Ada Lovelace"],
            "github_url": "",
            "demo_url": "",
            "submission_url": "",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _judge(client, headers, name: str, email: str) -> tuple[int, dict]:
    response = client.post(
        "/api/organizer/judges",
        json={"name": name, "email": email, "password": "password123"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], login(client, email)


def _assign(client, headers, judge_id: int, project_id: int) -> None:
    response = client.post(
        "/api/organizer/assignments",
        json={"judge_id": judge_id, "project_id": project_id},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def _submit(client, headers, project_id: int, scores: dict, alignment: str = "strong", **extra) -> None:
    saved = client.put(
        f"/api/judge/projects/{project_id}/evaluation",
        headers=headers,
        json=evaluation_body(scores=scores, alignment=alignment, **extra),
    )
    assert saved.status_code == 200, saved.text
    if saved.json()["status"] != "ready_to_submit":
        raise AssertionError(saved.text)
    submitted = client.post(f"/api/judge/projects/{project_id}/evaluation/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text


def test_aggregation_ranks_within_track_and_ignores_drafts(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    ai = _track(client, organizer, "Artificial Intelligence")
    education = _track(client, organizer, "Education")
    split = _project(client, organizer, "GreenRoute", ai)
    polished = _project(client, organizer, "Atlas Note", ai)
    lesson = _project(client, organizer, "QuizForge", education)

    high_id, high = _judge(client, organizer, "Alex Rivera", "alex@example.com")
    low_id, low = _judge(client, organizer, "Jordan Lee", "jordan@example.com")
    steady_id, steady = _judge(client, organizer, "Maya Chen", "maya@example.com")
    draft_id, drafter = _judge(client, organizer, "Priya Patel", "priya@example.com")
    edu_id, edu = _judge(client, organizer, "Sam Okonkwo", "sam@example.com")

    for judge_id in (high_id, low_id):
        _assign(client, organizer, judge_id, split)
    for judge_id in (steady_id, high_id, draft_id):
        _assign(client, organizer, judge_id, polished)
    _assign(client, organizer, edu_id, lesson)

    _submit(client, high, split, full_scores(5))
    _submit(
        client,
        low,
        split,
        {"technical": 2, "innovation": 2, "impact": 4, "presentation": 2},
        alignment="partial",
    )
    _submit(client, steady, polished, full_scores(4), alignment="weak")
    _submit(client, high, polished, full_scores(4), alignment="weak")
    draft = client.put(
        f"/api/judge/projects/{polished}/evaluation",
        headers=drafter,
        json=evaluation_body(
            scores=full_scores(1),
            private_notes="PRIVATE-SENTINEL-do-not-leak",
            alignment=None,
            alignment_explanation="",
        ),
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] == "draft_saved"
    _submit(
        client,
        edu,
        lesson,
        full_scores(1),
        cross_track_flag=True,
        recommended_track_id=ai,
        cross_track_explanation="The generator is the part an AI panel should see.",
    )

    report = client.get("/api/organizer/results", headers=organizer)
    assert report.status_code == 200, report.text
    assert "PRIVATE-SENTINEL-do-not-leak" not in report.text
    rows = {row["project_name"]: row for row in report.json()["projects"]}

    assert rows["GreenRoute"]["overall"] == 3.75
    assert rows["GreenRoute"]["criterion_averages"]["technical"] == 3.5
    assert rows["GreenRoute"]["criterion_averages"]["impact"] == 4.5
    assert rows["GreenRoute"]["discrepancy"] is True
    assert rows["GreenRoute"]["rank"] == 2
    assert rows["GreenRoute"]["alignment_counts"] == {"strong": 1, "partial": 1, "weak": 0}

    # Weak alignment does not pull the quality average down, and the all-1s draft is ignored.
    assert rows["Atlas Note"]["overall"] == 4
    assert rows["Atlas Note"]["criterion_averages"]["technical"] == 4
    assert rows["Atlas Note"]["discrepancy"] is False
    assert rows["Atlas Note"]["rank"] == 1
    assert rows["Atlas Note"]["submitted_count"] == 2
    assert rows["Atlas Note"]["assigned_count"] == 3

    assert rows["QuizForge"]["overall"] == 1
    assert rows["QuizForge"]["rank"] == 1
    assert rows["QuizForge"]["track_name"] == "Education"

    flags = client.get("/api/organizer/cross-track", headers=organizer)
    assert flags.status_code == 200
    quiz = next(item for item in flags.json()["projects"] if item["project_name"] == "QuizForge")
    assert quiz["original_track"] == "Education"
    assert quiz["flag_count"] == 1
    assert quiz["flags"][0]["recommended_track"] == "Artificial Intelligence"
    assert rows["QuizForge"]["rank"] == 1

    feedback = client.get("/api/organizer/export/feedback.csv", headers=organizer)
    assert "PRIVATE-SENTINEL-do-not-leak" not in feedback.text
    assert "The generator is the part an AI panel should see." in feedback.text
    results_csv = client.get("/api/organizer/export/results.csv", headers=organizer)
    assert "GreenRoute" in results_csv.text
    assert "yes" in results_csv.text
